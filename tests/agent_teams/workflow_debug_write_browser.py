"""Exercise the Agent write boundary through the Dashboard and a loopback ledger."""

import argparse
import asyncio
import json
import secrets
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import web
from playwright.async_api import expect

from tests.agent_teams.report_release_browser import (
    MODEL,
    ROOT,
    Acceptance,
    available_port,
)


class DebugWriteAcceptance(Acceptance):
    """Replay an Agent node with different data and inspect the captured write."""

    async def controlled_completion(self, request):
        """Ask the normal runner to send a report and then submit its result.

        Args:
            request: OpenAI-compatible model request from the real pipeline.

        Returns:
            Scripted tool calls or the final narration.
        """
        payload = await request.json()
        self.requests.append(payload)
        messages = payload["messages"]
        prompt = json.dumps(messages, ensure_ascii=False)
        assert "ATN187 REPORT" in prompt
        results = [
            message["content"] for message in messages if message["role"] == "tool"
        ]
        finished = any(result.startswith("Result accepted.") for result in results)
        if finished:
            message = {"role": "assistant", "content": "Done."}
        else:
            name = "submit_task_result" if results else "record_report_delivery"
            arguments = (
                {"result_json": '{"report":"Historical report complete"}'}
                if results
                else {"target": "team/reviewers"}
                if "missing count" in prompt
                else {"target": "team/reviewers", "count": 12}
                if "edited citations" in prompt
                else {"target": "team/publishers", "count": 7}
            )
            if not results:
                assert name in [tool["function"]["name"] for tool in payload["tools"]]
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_" + secrets.token_hex(6),
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
        completion = {
            "id": f"fixture-{len(self.requests)}",
            "object": "chat.completion",
            "model": MODEL,
            "created": 1,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop" if finished else "tool_calls",
                    "message": message,
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        }
        if not payload.get("stream"):
            return web.json_response(completion)
        delta = {"role": "assistant", "content": message["content"]}
        if calls := message.get("tool_calls"):
            delta["tool_calls"] = [{"index": 0, **calls[0]}]
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for delta, finish in (
            (delta, None),
            ({}, completion["choices"][0]["finish_reason"]),
        ):
            chunk = {
                **completion,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    async def start_dashboard(self):
        """Install only the controlled connector in the disposable Dashboard root.

        Returns:
            The ready Dashboard subprocess.
        """
        shutil.copytree(
            Path(__file__).with_name("fixtures") / "workflow_ledger_plugin",
            self.runtime / "data/plugins/astrbot_plugin_workflow_ledger",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        return await super().start_dashboard()

    async def run(self):
        """Keep a separate HTTP ledger alive while the Dashboard is running."""
        self.deliveries = []

        async def record(request):
            """Capture actual connector calls and return distinct receipt evidence.

            Args:
                request: HTTP request from the controlled tool.

            Returns:
                The independent ledger's JSON receipt.
            """
            self.deliveries.append(await request.json())
            return web.json_response({"receipt": "ledger-receipt-original"})

        ledger = web.Application()
        ledger.router.add_post("/record", record)
        runner = web.AppRunner(ledger)
        await runner.setup()
        port = available_port()
        try:
            await web.TCPSite(runner, "127.0.0.1", port).start()
            with patch.dict(
                "os.environ", {"ATN178_LEDGER_URL": f"http://127.0.0.1:{port}"}
            ):
                await super().run()
        finally:
            await runner.cleanup()

    async def exercise(self, browser):
        """Verify the authenticated Bot and Chat views with the same source attempt.

        Args:
            browser: Chromium running the built Dashboard.
        """
        self.context = await browser.new_context(
            viewport={"width": 1440, "height": 980}
        )
        bot = await self.login(self.context, "report-owner", self.password, "en-US")
        zh_context = await browser.new_context(viewport={"width": 1440, "height": 980})
        chat = await self.login(zh_context, "report-owner", self.password, "zh-CN")
        notes = {
            "type": "object",
            "properties": {"notes": {"type": "string"}},
            "required": ["notes"],
        }
        source_draft = await self.open_draft(
            {
                "name": "Deliver reviewed report",
                "port_contract_version": 2,
                "default_provider_id": MODEL,
                "input_contract": notes,
                "entry_task_ids": ["deliver"],
                "result_task_id": "deliver",
                "tasks": [
                    {
                        "id": "deliver",
                        "name": "Deliver report",
                        "member": "delivery",
                        "role": {
                            "name": "Report delivery",
                            "prompt": "Follow the task and submit the final result.",
                            "tools": ["record_report_delivery"],
                            "skills": [],
                        },
                        "installation": {"profile_id": "default"},
                        "task": "ATN187 REPORT. Deliver report for {notes}.",
                        "inputs": {"notes": {"input": "notes"}},
                        "input_contract": notes,
                        "output_contract": {
                            "type": "object",
                            "properties": {"report": {"type": "string"}},
                            "required": ["report"],
                        },
                    }
                ],
            }
        )
        await self.publish(source_draft)
        start = await self.api(
            "POST",
            f"/workflow_assets/{source_draft['asset_id']}/runs",
            data={"version": 1, "inputs": {"notes": "original citations"}},
        )
        root = start["run_id"]
        source = await self.wait_run(root, lambda run: run["status"] == "completed")
        (delivery,) = self.deliveries
        assert delivery["target"] == "team/publishers" and delivery["record"] == {
            "count": 7
        }
        expected_ledger = list(self.deliveries)

        for locale, page, route, expand, close in (
            (
                "en",
                bot,
                f"/#/agent-teams?workflow_run={root}",
                "Expand execution graph (read-only)",
                "Close",
            ),
            ("zh", chat, f"/#/chat/runs/{root}", "展开执行图（只读）", "关闭"),
        ):
            await page.goto(self.origin + route)
            await page.get_by_role("button", name=expand, exact=True).click()
            edge = page.locator(
                '.vue-flow__edge.inspection-edge[data-id="data:input:deliver:notes"]'
            )
            await edge.focus()
            await page.keyboard.press("Enter")
            await expect(page.get_by_test_id("inspection-value")).to_contain_text(
                "original citations"
            )
            await page.get_by_test_id("open-debug-fork").click()
            await (
                page.get_by_test_id("debug-fork-preview")
                .get_by_role("combobox")
                .click()
            )
            await page.get_by_role("option", name="notes", exact=True).click()
            await (
                page.get_by_test_id("debug-fork-replacement")
                .locator("textarea")
                .first.fill("edited citations")
            )
            assert self.deliveries == expected_ledger
            async with page.expect_response(
                lambda response: (
                    response.url.endswith(f"/{root}/debug_forks")
                    and response.request.method == "POST"
                )
            ) as started:
                await page.get_by_test_id("start-debug-fork").click()
            fork_id = (await (await started.value).json())["data"]["run_id"]
            open_preview = page.get_by_test_id("open-debug-write-preview")
            await open_preview.click()
            preview = page.get_by_test_id("debug-write-preview")
            await expect(preview).to_contain_text("team/reviewers")
            await expect(preview).to_contain_text('"count": 12')
            await expect(preview).to_contain_text(
                "Paused before external write"
                if locale == "en"
                else "已在外部写入前暂停"
            )
            history = preview.get_by_test_id("debug-write-history")
            await expect(
                history.get_by_text("ledger-receipt-original", exact=False)
            ).to_be_hidden()
            await history.locator("summary").click()
            await expect(
                history.get_by_text("ledger-receipt-original", exact=False)
            ).to_be_visible()
            await page.screenshot(
                path=self.evidence / f"agent-workflow-debug-write-{locale}.jpg",
                quality=88,
            )
            await page.context.set_offline(True)
            await preview.get_by_role("button", name=close, exact=True).click()
            await expect(preview).to_be_hidden()
            await page.context.set_offline(False)
            await page.reload()
            await open_preview.click()
            await expect(preview).to_contain_text("team/reviewers")
            assert self.deliveries == expected_ledger
            await preview.get_by_role("button", name=close, exact=True).click()
            await page.get_by_test_id("cancel-run").click()
            await page.get_by_test_id("confirm-run-control").click()
            await expect(page.get_by_test_id("debug-write-status")).to_contain_text(
                "Debug fork cancelled" if locale == "en" else "调试分支已取消"
            )
            fork = await self.api("GET", f"/workflow_runs/{fork_id}")
            (stopped,) = fork["tasks"]
            preview_id = stopped["state"]["debug_write_preview"]["record_id"]
            (intent,) = stopped["tool_calls"]
            assert intent["record_id"] == preview_id and intent["status"] == "intent"
            assert (
                intent["started_execution_id"] is None and intent["raw_results"] == []
            )
            late = await page.context.request.post(
                self.origin
                + f"/api/v1/workflow_runs/{stopped['run_id']}/tool_calls/{preview_id}/resolve",
                data={
                    "action": "confirm_result",
                    "request_id": "late",
                    "result": "Late receipt",
                    "evidence": "Stale browser action after cancellation",
                },
            )
            assert late.status == 409, await late.text()
            assert self.deliveries == expected_ledger
        (source_task,) = source["tasks"]
        path = f"/workflow_runs/{root}/debug_forks"
        attempt = {
            "scope_run_id": root,
            "task_run_id": source_task["run_id"],
            "attempt_id": source_task["state"]["attempt_id"],
        }
        plan = await self.api("POST", f"{path}/preview", data=attempt)
        malformed = await self.api(
            "POST",
            path,
            data={
                **attempt,
                "request_id": "missing-count",
                "preview_token": plan["preview_token"],
                "replacement": {"name": "notes", "value": "missing count"},
            },
        )
        stopped = await self.wait_run(
            malformed["run_id"], lambda run: run["status"] == "blocked"
        )
        (task,) = stopped["tasks"]
        assert task["state"]["debug_write_preview"]["status"] == "unavailable"
        assert (
            task["state"]["reason"]
            == "External write parameters are incomplete or invalid"
        )
        assert self.deliveries == expected_ledger
        saved = await self.api("GET", f"/workflow_runs/{root}")
        assert saved["cursor"] == source["cursor"]
        assert saved["tasks"] == source["tasks"]
        print(
            "Verified the unchanged source run and zero new ledger deliveries across three Agent debug forks.",
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard", type=Path, default=ROOT / "dashboard/dist")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--chromium")
    args = SimpleNamespace(**vars(parser.parse_args()), llama=None, weights=None)
    asyncio.run(DebugWriteAcceptance(args).run())
