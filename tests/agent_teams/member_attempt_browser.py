"""Verify autonomous member attempts on an isolated production Dashboard."""

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from playwright.async_api import expect

from tests.agent_teams.report_release_browser import API, ROOT, Acceptance
from tests.agent_teams.report_release_fixture import MODEL


class MemberAttemptAcceptance(Acceptance):
    """Run two dynamic members and document the evidence behind each attempt."""

    def __init__(self, args):
        """Initialize the model gates for one isolated acceptance run.

        Args:
            args: Browser, build, locale and evidence settings.
        """
        super().__init__(args)
        self.allow_correction = asyncio.Event()
        self.allow_late_note = asyncio.Event()
        self.exact_attachment = None

    async def controlled_completion(self, request):
        """Drive only the real runtime's coordinator and result tools.

        Args:
            request: OpenAI-compatible request sent to the loopback model.

        Returns:
            One scripted completion or the original attempt's controlled failure.
        """
        payload = await request.json()
        self.requests.append(payload)
        messages = payload["messages"]
        context = json.dumps(messages, ensure_ascii=False)
        results = {
            row["tool_call_id"]: row["content"]
            for row in messages
            if row["role"] == "tool"
        }
        accepted = any("Result accepted" in result for result in results.values())
        tool = "submit_task_result"
        if accepted:
            step, args = "done", {}
        elif "AT191_WRITE_A" in context and "AT191_COORD" not in context:
            if "Correction for the retry" not in context:
                assert "Original instruction for A" in context
                return web.json_response(
                    {"error": {"message": "Controlled first attempt failure"}},
                    status=400,
                )
            assert "Private instruction for B" not in context
            if "read-evidence" not in results:
                step, tool, args = (
                    "read-evidence",
                    "read_task_artifact",
                    self.exact_attachment,
                )
            else:
                assert "B produced independent evidence." in results["read-evidence"]
                step, args = "result-a", {"text": "A used the corrected instruction."}
        elif "AT191_WRITE_B" in context and "AT191_COORD" not in context:
            assert (
                "Private instruction for B" in context
                or "AT191_TRIAL_MEMBER" in context
            )
            assert "Original instruction for A" not in context
            step, args = "result-b", {"text": "B produced independent evidence."}
        else:
            assert "AT191_COORD" in context
            trial = "AT191_TRIAL" in context
            plan = (
                ["create-b", "trial-dispatch", "trial-wait"]
                if trial
                else [
                    "create-a",
                    "create-b",
                    "initial-a",
                    "initial-b",
                    "dispatch-a",
                    "dispatch-b",
                    "correction",
                    "late",
                ]
            )
            step = next((key for key in plan if key not in results), "result-team")
            if step.startswith("create"):
                tool = "team_manage"
                name = step[-1].upper()
                args = {
                    "action": "create_member",
                    "name": f"Member {name}",
                    "preset_id": "writer",
                }
            elif step in {"initial-a", "initial-b", "correction", "late"}:
                tool = "team_communicate"
                member = "b" if step == "initial-b" else "a"
                args = {
                    "action": "send",
                    "recipient_member_id": json.JSONDecoder().raw_decode(
                        results["create-" + member]
                    )[0]["member_id"],
                    "text": {
                        "initial-a": "Original instruction for A",
                        "initial-b": "Private instruction for B",
                        "correction": "Correction for the retry",
                        "late": "Unconfirmed late delivery for A",
                    }[step],
                }
                if step == "correction":
                    await asyncio.wait_for(self.allow_correction.wait(), 60)
                    args["artifact_refs"] = [self.exact_attachment]
                if step == "late":
                    await asyncio.wait_for(self.allow_late_note.wait(), 60)
            elif "dispatch" in step:
                tool = "team_manage"
                member = "b" if trial else step[-1]
                args = {
                    "action": "dispatch",
                    "member_id": json.JSONDecoder().raw_decode(
                        results["create-" + member]
                    )[0]["member_id"],
                    "name": f"Draft {member.upper()}",
                    "task": f"AT191_WRITE_{member.upper()} "
                    + (
                        "AT191_TRIAL_MEMBER"
                        if trial
                        else f"Write evidence for {member.upper()}."
                    )
                    + (" Keep {{year}}." if member == "a" else ""),
                }
            elif step == "trial-wait":
                tool = "team_manage"
                args = {
                    "action": "wait",
                    "task_ids": [
                        json.JSONDecoder().raw_decode(results["trial-dispatch"])[0][
                            "task_id"
                        ]
                    ],
                }
            else:
                args = {"text": "Whole team done."}
            if tool in {"team_manage", "team_communicate"}:
                args["request_id"] = step

        message = (
            {"role": "assistant", "content": "Done"}
            if accepted
            else {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": step,
                        "type": "function",
                        "function": {"name": tool, "arguments": json.dumps(args)},
                    }
                ],
            }
        )
        choice = {
            "index": 0,
            "finish_reason": "stop" if accepted else "tool_calls",
            "message": message,
        }
        completion = {
            "id": f"member-attempt-{len(self.requests)}",
            "object": "chat.completion",
            "model": MODEL,
            "created": 1,
            "choices": [choice],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        }
        if not payload.get("stream"):
            return web.json_response(completion)
        delta = dict(message)
        if "tool_calls" in delta:
            delta["tool_calls"] = [{"index": 0, **delta["tool_calls"][0]}]
        stream = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await stream.prepare(request)
        for part, finish in ((delta, None), ({}, choice["finish_reason"])):
            chunk = {
                **completion,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": part, "finish_reason": finish}],
            }
            await stream.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await stream.write(b"data: [DONE]\n\n")
        await stream.write_eof()
        return stream

    async def exercise(self, browser):
        """Read the failed and retried attempts, then confirm a separate team trial.

        Args:
            browser: Headless Chromium connected to the real Dashboard.
        """
        zh = self.args.locale == "zh-CN"
        self.context = await browser.new_context(
            viewport={"width": 1440, "height": 1020}
        )
        page = await self.login(
            self.context, "report-owner", self.password, self.args.locale
        )
        definition = {
            "kind": "autonomous",
            "name": "Evidence desk",
            "default_provider_id": MODEL,
            "coordinator": {
                "id": "coordinator",
                "role": {
                    "name": "Coordinator",
                    "prompt": "AT191_COORD",
                    "max_steps": 20,
                },
                "installation": {"profile_id": "default"},
            },
            "allowed_roles": [
                {
                    "id": "writer",
                    "role": {
                        "name": "Writer",
                        "prompt": "Write assigned evidence.",
                        "max_instances": 2,
                    },
                    "installation": {"profile_id": "default"},
                }
            ],
            "max_members": 3,
            "max_parallel": 3,
        }
        if zh:
            asset = await self.api(
                "POST", "/workflow_assets", data={"definition": definition}
            )
        else:
            draft = await self.api(
                "POST",
                "/agent_drafts/autonomous",
                data={"body": {"definition": definition}},
            )
            asset = await self.api(
                "POST",
                f"/agent_drafts/autonomous/{draft['asset_id']}/publish",
                data={"revision": draft["revision"]},
            )
        asset_id = asset["asset_id"]
        run = await self.api(
            "POST",
            f"/workflow_assets/{asset_id}/runs",
            data={"version": 1, "inputs": {"goal": "AT191_COORD Prepare evidence."}},
        )
        root = run["run_id"]
        failed = await self.wait_run(
            root,
            lambda entry: (
                any(
                    task["status"] in {"awaiting_result", "failed"}
                    and task["snapshot"]["definition"]["name"] == "Draft A"
                    for task in entry["tasks"]
                )
                and any(
                    task["status"] == "completed"
                    and task["snapshot"]["definition"]["name"] == "Draft B"
                    for task in entry["tasks"]
                )
            ),
        )
        a, b = (
            next(
                task
                for task in failed["tasks"]
                if task["snapshot"]["definition"]["name"] == label
            )
            for label in ("Draft A", "Draft B")
        )
        previous = a["state"]["attempt_id"]
        self.exact_attachment = b["result_ref"]
        self.allow_correction.set()
        await self.wait_run(
            root,
            lambda entry: any(
                m["text"] == "Correction for the retry" for m in entry["messages"]
            ),
        )
        await self.api(
            "POST",
            f"/workflow_runs/{a['run_id']}/retry",
            data={
                "request_id": "retry-a",
                "attempt_id": previous,
                "input_policy": "original",
            },
        )
        retried = await self.wait_run(
            root,
            lambda entry: any(
                task["run_id"] == a["run_id"] and task["status"] == "completed"
                for task in entry["tasks"]
            ),
        )
        a = next(task for task in retried["tasks"] if task["run_id"] == a["run_id"])
        self.allow_late_note.set()
        original = await self.wait_run(
            root, lambda entry: entry["status"] == "completed"
        )

        path = f"/workflow_runs/{root}/member_attempt"
        for task, attempt_id, text, ref in (
            (a, previous, "Original instruction for A", None),
            (a, a["state"]["attempt_id"], "Correction for the retry", a["result_ref"]),
            (b, b["state"]["attempt_id"], "Private instruction for B", b["result_ref"]),
        ):
            query = {"task_run_id": task["run_id"], "attempt_id": attempt_id}
            evidence = await self.api("GET", path, params=query)
            assert (
                evidence["member"]["member_id"]
                == task["snapshot"]["member"]["member_id"]
            )
            assert [
                (m["text"], m["consumption"]["attempt_id"])
                for m in evidence["messages"]
            ] == [(text, attempt_id)]
            assert evidence["result_artifact"] == ref
            assert evidence["unconfirmed_deliveries"] == int(
                task["run_id"] == a["run_id"]
            )
            if text == "Correction for the retry":
                assert evidence["messages"][0]["artifact_refs"] == [b["result_ref"]]
                assert evidence["read_artifacts"] == [b["result_ref"]]
            else:
                assert evidence["read_artifacts"] == []
        query = {"task_run_id": a["run_id"], "attempt_id": previous}
        wrong = await self.context.request.get(
            self.origin + API + path,
            params={**query, "attempt_id": b["state"]["attempt_id"]},
        )
        assert wrong.status == 404
        await self.api(
            "POST",
            "/accounts",
            data={"username": "report-viewer", "password": self.password},
        )
        viewer = await browser.new_context()
        private = await self.login(viewer, "report-viewer", self.password, "en-US")
        route = f"/#/chat/runs/{root}" if zh else f"/#/agent-teams?workflow_run={root}"
        await private.goto(self.origin + route)
        denied = await viewer.request.get(self.origin + API + path, params=query)
        ref = b["result_ref"]
        artifact_path = (
            f"/workflow_artifacts/{ref['artifact_id']}/versions/{ref['version']}"
        )
        denied_artifact = await viewer.request.get(self.origin + API + artifact_path)
        assert denied.status in {403, 404}, await denied.text()
        assert denied_artifact.status in {403, 404}, await denied_artifact.text()
        await expect(private.get_by_test_id("run-detail")).to_have_count(0)
        await expect(private.get_by_test_id("member-attempt-inspector")).to_have_count(
            0
        )
        await viewer.close()

        await page.goto(self.origin + route)
        attempts = page.get_by_text("运行尝试" if zh else "Attempts", exact=True)
        await page.get_by_role("button", name="Draft B", exact=False).click()
        await attempts.click()
        await page.get_by_test_id("inspect-current-attempt").click()
        inspector = page.get_by_test_id("member-attempt-inspector")
        await expect(
            inspector.get_by_test_id("member-attempt-message")
        ).to_contain_text("Private instruction for B")
        await inspector.get_by_test_id("member-attempt-read-artifact").click()
        await expect(
            inspector.get_by_test_id("member-attempt-artifact")
        ).to_contain_text("B produced independent evidence.")
        await inspector.get_by_role(
            "button", name="关闭" if zh else "Close", exact=True
        ).click()
        await page.get_by_role("button", name="Draft A", exact=False).click()
        await attempts.click()
        current = page.get_by_test_id("inspect-current-attempt")
        await current.click()
        await expect(
            inspector.get_by_test_id("member-attempt-message")
        ).to_contain_text("Correction for the retry")
        assert (
            await inspector.get_by_test_id("member-attempt-read-artifact").count() == 2
        )
        await expect(inspector).to_contain_text(
            "已记录的读取" if zh else "Recorded read"
        )
        await inspector.get_by_role(
            "button", name="关闭" if zh else "Close", exact=True
        ).click()
        saved = page.get_by_test_id("saved-attempt").filter(has_text=previous)
        await saved.locator("summary").click()
        await saved.get_by_test_id("inspect-saved-attempt").click()
        await expect(
            inspector.get_by_test_id("member-attempt-dispatch")
        ).to_contain_text("Write evidence for A. Keep {{year}}.")
        await expect(
            inspector.get_by_test_id("member-attempt-identity")
        ).to_contain_text("Member A")
        await expect(
            inspector.get_by_test_id("member-attempt-message")
        ).to_contain_text("Original instruction for A")
        await expect(
            inspector.get_by_test_id("member-attempt-unconfirmed")
        ).to_be_visible()
        await expect(
            inspector.get_by_test_id("member-attempt-no-result")
        ).to_be_visible()
        for unrelated in (
            "Private instruction for B",
            "Correction for the retry",
            "Unconfirmed late delivery for A",
        ):
            await expect(inspector).not_to_contain_text(unrelated)
        await page.screenshot(
            path=self.evidence / "member-attempt.jpg", quality=88, animations="disabled"
        )

        if not zh:
            updated = await self.api(
                "PUT",
                f"/agent_drafts/autonomous/{asset_id}",
                data={
                    "expected_revision": draft["revision"],
                    "body": {
                        "definition": {**definition, "name": "Evidence desk draft"}
                    },
                },
            )
            revision = updated["revision"]
        else:
            revision = 1
        await inspector.get_by_test_id("member-attempt-trial").click()
        dialog = (
            page.get_by_role("dialog")
            .filter(
                has=page.get_by_text(
                    "试跑整个团队" if zh else "Test whole team", exact=True
                )
            )
            .filter(
                has=page.get_by_text(
                    "草稿修订" if zh else "Draft revision", exact=False
                )
            )
        )
        await expect(dialog).to_contain_text(
            f"{'草稿修订' if zh else 'Draft revision'} {revision}"
        )
        await expect(dialog).to_contain_text(
            "Evidence desk" if zh else "Evidence desk draft"
        )
        start = dialog.get_by_role(
            "button", name="开始试运行" if zh else "Start test", exact=True
        )
        await expect(start).to_be_enabled()
        await page.screenshot(
            path=self.evidence / "whole-team-trial.jpg",
            quality=88,
            animations="disabled",
        )
        await dialog.get_by_role("textbox", name="goal").fill("AT191_COORD AT191_TRIAL")
        async with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.endswith(f"/workflow_assets/{asset_id}/trials")
            )
        ) as running:
            await start.click()
        response = await running.value
        assert response.ok, await response.text()
        trial_id = (await response.json())["data"]["run_id"]
        assert trial_id != root
        await page.wait_for_function(
            "(runId) => location.hash.includes(runId)", arg=trial_id
        )
        trial = await self.wait_run(
            trial_id, lambda entry: entry["status"] == "completed"
        )
        assert trial["snapshot"]["trial"]["draft_revision"] == revision
        assert any(
            task["snapshot"].get("created_by_task_id") for task in trial["tasks"]
        )
        assert (await self.api("GET", f"/workflow_runs/{root}"))[
            "messages"
        ] == original["messages"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard", type=Path, default=ROOT / "dashboard/dist")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--chromium")
    parser.add_argument("--locale", choices=["en-US", "zh-CN"], required=True)
    parsed = parser.parse_args()
    asyncio.run(
        MemberAttemptAcceptance(
            SimpleNamespace(**vars(parsed), llama=None, weights=None)
        ).run()
    )
