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
        simulated = next(
            (
                json.loads(result.removeprefix("Simulated receipt (no external write): "))["receipt"]
                for result in results
                if result.startswith("Simulated receipt (no external write): ")
            ),
            None,
        )
        item = next(
            (
                name for name in ("test-message-en", "test-message-zh")
                if f"Archive simulated item {name}." in prompt
            ),
            None,
        )
        finished = any(result.startswith("Result accepted.") for result in results)
        if finished:
            message = {"role": "assistant", "content": "Done."}
        else:
            name = "submit_task_result" if results or item else "record_report_delivery"
            arguments = (
                {"text": item}
                if item
                else {
                    "result_json": json.dumps({
                        "report": simulated or "Historical report complete",
                        "items": [simulated] if simulated else [],
                    })
                }
                if results
                else {"target": "team/reviewers"}
                if "missing count" in prompt
                else {"target": "team/reviewers", "count": 12}
                if "edited citations" in prompt
                else {"target": "team/archive", "count": 14}
                if "final citations" in prompt
                else {"target": "team/publishers", "count": 7}
            )
            if not results and not item:
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
        if not finished and not results and "ambiguous delivery" in prompt:
            message["tool_calls"][0]["function"]["arguments"] = json.dumps(
                {"target": "team/uncertain", "count": 15}
            )
            message["tool_calls"].append(
                {
                    "id": "call_" + secrets.token_hex(6),
                    "type": "function",
                    "function": {
                        "name": "record_report_delivery",
                        "arguments": json.dumps({"target": "team/next", "count": 20}),
                    },
                }
            )
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
            delta["tool_calls"] = [
                {"index": index, **call} for index, call in enumerate(calls)
            ]
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
            if self.deliveries[-1]["target"] == "team/uncertain":
                request.transport.close()
            receipt = (
                "original" if len(self.deliveries) == 1 else str(len(self.deliveries))
            )
            return web.json_response({"receipt": f"ledger-receipt-{receipt}"})

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
                "edges": [{"source": "deliver", "target": "dispatch"}],
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
                            "properties": {
                                "report": {"type": "string"},
                                "items": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["report", "items"],
                        },
                    },
                    {
                        "id": "dispatch",
                        "name": "Dispatch archive items",
                        "member": "archive",
                        "role": {"name": "Archive", "prompt": "Submit the supplied item.", "tools": [], "skills": []},
                        "installation": {"profile_id": "default"},
                        "task": "ATN187 REPORT. Archive simulated item {item}.",
                        "inputs": {"items": {"task_id": "deliver", "field": "items"}},
                        "dispatch": {"max_items": 2, "max_parallel": 1},
                    },
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

        prior_token = None
        execution_ids = set()
        for locale, page, route, expand, target, count, notes in (
            (
                "en",
                bot,
                f"/#/agent-teams?workflow_run={root}",
                "Expand execution graph (read-only)",
                "team/reviewers",
                12,
                "edited citations",
            ),
            (
                "zh",
                chat,
                f"/#/chat/runs/{root}",
                "展开执行图（只读）",
                "team/archive",
                14,
                "final citations",
            ),
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
                page.get_by_test_id("debug-fork-input-picker")
                .get_by_role("combobox")
                .click()
            )
            await page.get_by_role("option", name="notes", exact=True).click()
            await (
                page.get_by_test_id("debug-fork-replacement")
                .locator("textarea")
                .first.fill(notes)
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
            await expect(preview).to_contain_text(target)
            await expect(preview).to_contain_text(f'"count": {count}')
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
            fork = await self.api("GET", f"/workflow_runs/{fork_id}")
            (stopped,) = fork["tasks"]
            write = stopped["state"]["debug_write_preview"]
            assert len(self.deliveries) == len(expected_ledger)
            execute_url = (
                self.origin
                + f"/api/v1/workflow_runs/{stopped['run_id']}"
                + f"/tool_calls/{write['record_id']}/execute"
            )
            if prior_token:
                stale = await page.context.request.post(
                    execute_url, data={"preview_token": prior_token}
                )
                assert stale.status == 409, await stale.text()
                assert self.deliveries == expected_ledger
                async with page.expect_response(execute_url) as confirmed:
                    await preview.get_by_test_id("confirm-debug-write").click()
                response = await confirmed.value
                assert response.status == 200, await response.text()
            else:
                confirmation_lost = asyncio.Event()

                async def lose_confirmation_response(route):
                    """Forward the exact browser approval and discard its reply.

                    Args:
                        route: Intercepted Dashboard confirmation request.
                    """
                    async with self.client.post(
                        execute_url,
                        json=route.request.post_data_json,
                        headers={
                            "Authorization": await route.request.header_value(
                                "authorization"
                            )
                        },
                    ) as response:
                        assert response.status == 200, await response.text()
                    await route.abort()
                    confirmation_lost.set()

                await page.route(execute_url, lose_confirmation_response)
                await preview.get_by_test_id("confirm-debug-write").click()
                await asyncio.wait_for(confirmation_lost.wait(), timeout=15)
                await page.unroute(execute_url)
            receipt_text = f"ledger-receipt-{len(expected_ledger) + 1}"
            finished = await self.wait_run(
                fork_id, lambda run: run["status"] == "completed"
            )
            await page.reload()
            await open_preview.click()
            evidence = preview.get_by_test_id("debug-write-current")
            await expect(evidence).to_contain_text(receipt_text)
            await expect(evidence).not_to_contain_text("ledger-receipt-original")
            await expect(preview.get_by_test_id("confirm-debug-write")).to_have_count(0)
            assert len(self.deliveries) == len(expected_ledger) + 1
            assert self.deliveries[-1] == {
                "target": target,
                "record": {"count": count},
                "identity": write["record_id"],
            }
            (finished_task,) = finished["tasks"]
            call = next(
                item
                for item in finished_task["tool_calls"]
                if item["record_id"] == write["record_id"]
            )
            assert call["status"] == "applied" and receipt_text in json.dumps(
                call["raw_results"]
            )
            assert call["started_execution_id"]
            execution_ids.add(call["started_execution_id"])
            assert call["started_execution_id"] in await evidence.inner_text()
            duplicate = await page.context.request.post(
                execute_url, data={"preview_token": write["preview_token"]}
            )
            assert duplicate.status == 200, await duplicate.text()
            assert len(self.deliveries) == len(expected_ledger) + 1
            expected_ledger = list(self.deliveries)
            prior_token = write["preview_token"]
        assert len(execution_ids) == 2
        source_task = next(task for task in source["tasks"] if task["node_id"] == "deliver")
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
        await self.exercise_unknown_batch(bot, path, attempt, plan["preview_token"])
        saved = await self.api("GET", f"/workflow_runs/{root}")
        assert saved["cursor"] == source["cursor"]
        assert saved["tasks"] == source["tasks"]
        print(
            "Verified exact authorized writes from Bot and Chat, response loss and reopen, "
            "stale approval rejection, and the unchanged source run.",
            flush=True,
        )

        before = list(self.deliveries)
        selection = {**attempt, "scope": "downstream"}
        for locale, page in (("en", bot), ("zh", chat)):
            plan = await self.api(
                "POST", f"/workflow_runs/{root}/debug_forks/preview", data=selection
            )
            started = await self.api(
                "POST",
                f"/workflow_runs/{root}/debug_forks",
                data={
                    **selection,
                    "preview_token": plan["preview_token"],
                    "request_id": f"simulate-{locale}",
                },
            )
            fork_id = started["run_id"]
            await self.wait_run(fork_id, lambda run: run["status"] == "blocked")
            route = (
                f"/#/agent-teams?workflow_run={fork_id}"
                if locale == "en"
                else f"/#/chat/runs/{fork_id}"
            )
            await page.goto(self.origin + route)
            open_write = page.get_by_test_id("open-debug-write-preview")
            await open_write.click()
            panel = page.get_by_test_id("debug-write-preview")
            simulation = panel.get_by_test_id("simulate-debug-write")
            await simulation.click()
            json_field = panel.get_by_test_id("debug-write-test-value").locator("textarea").first
            message_id = f"test-message-{locale}"
            value = json.dumps({"receipt": message_id})
            await json_field.fill(value)
            await json_field.press("Tab")
            await expect(json_field).to_have_value(value)
            await page.screenshot(
                path=self.evidence / f"agent-workflow-simulated-receipt-{locale}.jpg",
                quality=88,
                animations="disabled",
            )
            await json_field.fill('{"receipt": 23}')
            async with page.expect_response(
                lambda response: response.url.endswith("/simulate")
            ) as invalid:
                await simulation.click()
            response = await invalid.value
            assert response.status == 422, await response.text()
            await expect(panel.get_by_text("result.receipt", exact=True)).to_be_visible()
            assert self.deliveries == before
            await json_field.fill(json.dumps({"receipt": message_id}))
            async with page.expect_response(
                lambda response: response.url.endswith("/simulate")
            ) as submitted:
                await simulation.click()
            response = await submitted.value
            assert response.status == 200, await response.text()
            finished = await self.wait_run(fork_id, lambda run: run["status"] == "completed")
            assert finished["result"] == {"report": message_id, "items": [message_id]}
            delivery, dispatch = sorted(finished["tasks"], key=lambda task: task["node_id"])
            (origin,) = delivery["state"]["simulated_sources"]
            assert dispatch["state"]["simulated_sources"] == [origin]
            (item_scope,) = finished["scopes"]
            for task in item_scope["tasks"]:
                assert task["state"]["simulated_sources"] == [origin]
            item = next(task for task in item_scope["tasks"] if task["node_id"] == "item-0")
            assert item["result"]["text"] == message_id
            assert self.deliveries == before
            await page.reload()
            await expect(page.get_by_test_id("debug-fork-comparison").get_by_test_id(
                "workflow-simulated-sources"
            )).to_be_visible()
            timeline = page.get_by_test_id("durable-tool-call").filter(
                has_text="record_report_delivery"
            )
            await expect(timeline).to_contain_text(
                "Simulated receipt recorded" if locale == "en" else "已记录模拟回执"
            )
            await expect(timeline).to_contain_text(origin["record_id"])
            await open_write.click()
            await expect(panel.get_by_test_id("debug-write-simulated")).to_contain_text(message_id)
            await expect(panel.get_by_test_id("debug-write-current")).to_have_count(0)
            await expect(panel.get_by_test_id("simulate-debug-write")).to_have_count(0)

        print(
            "Verified contract-checked simulated receipts, Agent results, reopening, "
            "and zero additional ledger writes in Bot and Chat.",
            flush=True,
        )

    async def exercise_unknown_batch(self, page, launch_path, attempt, preview_token):
        """Keep an uncertain authorized call ahead of the next unstarted write.

        Args:
            page: Authenticated Bot workbench page.
            launch_path: Debug fork entry point for the recorded source.
            attempt: Exact original attempt selected for replay.
            preview_token: Confirmation of the fork's initial inputs.
        """
        before = list(self.deliveries)
        fork = await self.api(
            "POST",
            launch_path,
            data={
                **attempt,
                "request_id": "ambiguous-batch",
                "preview_token": preview_token,
                "replacement": {"name": "notes", "value": "ambiguous delivery"},
            },
        )
        identity = fork["run_id"]
        await page.goto(self.origin + f"/#/agent-teams?workflow_run={identity}")
        await page.get_by_test_id("open-debug-write-preview").click()
        preview = page.get_by_test_id("debug-write-preview")
        await expect(preview).to_contain_text("team/uncertain")
        assert self.deliveries == before
        async with page.expect_response(
            lambda response: response.url.endswith("/execute")
        ) as confirmed:
            await preview.get_by_test_id("confirm-debug-write").click()
        response = await confirmed.value
        assert response.status == 200, await response.text()
        for _ in range(200):
            current = await self.api("GET", f"/workflow_runs/{identity}")
            (task,) = current["tasks"]
            calls = task["tool_calls"]
            if current["status"] == "blocked" and calls[0]["status"] == "unknown":
                break
            await asyncio.sleep(0.05)
        assert current["status"] == "blocked"
        assert [call["status"] for call in calls] == ["unknown", "intent"]
        assert not calls[0]["raw_results"] and not calls[1]["started_execution_id"]
        assert (
            task["state"]["debug_write_preview"]["record_id"] == calls[0]["record_id"]
        )
        assert self.deliveries == before + [
            {
                "identity": calls[0]["record_id"],
                "target": "team/uncertain",
                "record": {"count": 15},
            }
        ]
        await page.reload()
        await page.get_by_test_id("open-debug-write-preview").click()
        await expect(preview).to_contain_text("External outcome needs verification")
        await expect(preview.get_by_test_id("confirm-debug-write")).to_have_count(0)



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard", type=Path, default=ROOT / "dashboard/dist")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--chromium")
    args = SimpleNamespace(**vars(parser.parse_args()), llama=None, weights=None)
    asyncio.run(DebugWriteAcceptance(args).run())
