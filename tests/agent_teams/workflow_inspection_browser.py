"""Exercise data-line inspection in both production Dashboard run views."""

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from playwright.async_api import expect

from tests.agent_teams.report_release_browser import API, ROOT, Acceptance
from tests.agent_teams.report_release_fixture import MODEL
from tests.agent_teams.test_workflow_loops import revision_definition

REVIEW_LINE = (
    '.vue-flow__edge[data-id="data:input:review:draft"] .vue-flow__edge-textwrapper'
)


class InspectionAcceptance(Acceptance):
    """Run fixed nested workflows against a disposable Dashboard and model."""

    allow_retry = False

    async def controlled_completion(self, request):
        """Fail only the first root's second-round checker before its explicit retry.

        Args:
            request: OpenAI-compatible request at the local fixture boundary.

        Returns:
            A validated tool submission, a final message, or the controlled failure.
        """
        payload = await request.json()
        self.requests.append(payload)
        messages = payload["messages"]
        prompt = next(
            str(item["content"])
            for item in reversed(messages)
            if item["role"] == "user" and "AT182" in str(item["content"])
        )
        done = any(
            item["role"] == "tool" and item["content"].startswith("Result accepted.")
            for item in messages
        )
        document = "Cobalt" if "Cobalt" in prompt else "Amber"
        checking = "AT182 CHECK" in prompt
        if checking and not done:
            second = "round 2" in prompt
            if second and document == "Cobalt" and not self.allow_retry:
                return web.json_response(
                    {"error": {"message": "Controlled second-round failure"}},
                    status=400,
                )
            result = {"approved": second, "text": "Citation review complete"}
        elif "AT182 WRITE" in prompt:
            result = {
                "text": f"{document} draft, round {2 if 'round 1' in prompt else 1}"
            }
        else:
            result = {"text": f"{document} report"}
        completion = {
            "id": f"inspection-{len(self.requests)}",
            "object": "chat.completion",
            "model": MODEL,
            "created": 1,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop" if done else "tool_calls",
                    "message": {"role": "assistant", "content": "Complete"}
                    if done
                    else {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"submit-{len(self.requests)}",
                                "type": "function",
                                "function": {
                                    "name": "submit_task_result",
                                    "arguments": json.dumps(
                                        {"result_json": json.dumps(result)}
                                        if checking
                                        else result
                                    ),
                                },
                            }
                        ],
                    },
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
        choice = completion["choices"][0]
        delta = dict(choice["message"])
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
        """Verify frozen failed records, permissions and a deliberately delayed read.

        Args:
            browser: Chromium running against the isolated production server.
        """
        self.context = await browser.new_context(
            viewport={"width": 1512, "height": 1050}
        )
        bot = await self.login(self.context, "report-owner", self.password, "en-US")
        role = await self.api(
            "POST",
            "/agent_roles",
            data={
                "definition": {
                    "name": "Report editor",
                    "prompt": "Follow the assigned task.",
                },
            },
        )
        child = revision_definition(role["role_id"], max_iterations=2)
        child["name"] = "Citation review"
        child["default_provider_id"] = MODEL
        child["tasks"][0].update(
            name="Write draft", task="AT182 WRITE {draft}", position={"x": 0, "y": 80}
        )
        child["tasks"][1].update(
            name="Check citations",
            task="AT182 CHECK {draft}",
            position={"x": 340, "y": 80},
        )
        child["tasks"][2].update(
            task="AT182 PUBLISH {draft}", position={"x": 460, "y": 350}
        )
        child_draft = await self.open_draft(child)
        await self.publish(child_draft)
        parent = await self.open_draft(
            {
                "name": "Review two documents",
                "required_inputs": ["draft", "feedback"],
                "result_task_id": "review_report",
                "tasks": [
                    {
                        "id": "review_report",
                        "name": "Citation workflow",
                        "workflow": {"asset_id": child_draft["asset_id"], "version": 1},
                        "inputs": {
                            "draft": {"input": "draft"},
                            "feedback": {"input": "feedback"},
                        },
                    }
                ],
            }
        )
        await self.publish(parent)
        start = f"/workflow_assets/{parent['asset_id']}/runs"
        cobalt = await self.api(
            "POST",
            start,
            data={
                "version": 1,
                "inputs": {"draft": "Cobalt", "feedback": "Check two sources"},
            },
        )
        root = cobalt["run_id"]
        failed = await self.wait_run(root, lambda run: run["status"] == "blocked")
        scope = failed["scopes"][0]
        reviewer = next(task for task in scope["tasks"] if task["node_id"] == "review")
        assert reviewer["status"] == "awaiting_result", reviewer["state"].get("reason")
        attempt = reviewer["state"]["attempt_id"]
        self.allow_retry = True
        await self.api(
            "POST",
            f"/workflow_runs/{reviewer['run_id']}/retry",
            data={
                "request_id": "inspection-retry",
                "attempt_id": attempt,
                "input_policy": "original",
            },
        )
        completed = await self.wait_run(root, lambda run: run["status"] == "completed")
        current = next(
            task
            for task in completed["scopes"][0]["tasks"]
            if task["node_id"] == "review"
        )

        query = {
            "scope_run_id": scope["run_id"],
            "task_run_id": reviewer["run_id"],
            "input_name": "draft",
            "attempt_id": attempt,
        }
        path = f"/workflow_runs/{root}/data_line"
        data = await self.api("GET", path, params=query)
        assert data["task_status"] == "awaiting_result"
        assert data["value"] == "Cobalt draft, round 2"
        await self.api(
            "POST",
            "/accounts",
            data={"username": "report-viewer", "password": self.password},
        )
        viewer = await browser.new_context()
        private = await self.login(viewer, "report-viewer", self.password, "en-US")
        for entry in (f"/#/agent-teams?workflow_run={root}", f"/#/chat/runs/{root}"):
            await private.goto(self.origin + entry)
            denied = await viewer.request.get(self.origin + API + path, params=query)
            assert denied.status in {403, 404}
            await expect(private.get_by_test_id("run-detail")).to_have_count(0)
            await expect(private.locator(".run-summary")).to_have_count(0)
        await viewer.close()

        for locale, surface in (
            ("en", bot),
            (
                "zh",
                await self.login(
                    await browser.new_context(viewport={"width": 1512, "height": 1050}),
                    "report-owner",
                    self.password,
                    "zh-CN",
                ),
            ),
        ):
            route = (
                f"/#/agent-teams?workflow_run={root}"
                if locale == "en"
                else f"/#/chat/runs/{root}"
            )
            await surface.goto(self.origin + route)
            await surface.get_by_test_id("child-scope").locator("summary").first.click()
            await (
                surface.get_by_test_id(f"iteration-task-{reviewer['run_id']}")
                .locator("xpath=ancestor::details[1]/summary")
                .click()
            )
            await surface.get_by_test_id(f"iteration-task-{reviewer['run_id']}").click()
            await surface.get_by_text(
                "Attempts" if locale == "en" else "运行尝试", exact=True
            ).click()
            saved = surface.get_by_test_id("saved-attempt").filter(has_text=attempt)
            await saved.locator("summary").click()
            await saved.get_by_test_id("inspect-saved-attempt").click()
            await expect(surface.get_by_test_id("inspection-scope")).to_have_value(
                scope["run_id"]
            )
            await expect(surface.get_by_test_id("inspection-round")).to_have_value(
                "revision:2"
            )
            await expect(surface.get_by_test_id("inspection-value")).to_have_text(
                '"Cobalt draft, round 2"'
            )
            await expect(surface.get_by_test_id("inspection-context")).to_contain_text(
                attempt
            )
            await expect(surface.get_by_test_id("inspection-evidence")).to_contain_text(
                data["evidence"]["artifact"]["artifact_id"]
            )
            await surface.get_by_test_id("run-detail").scroll_into_view_if_needed()
            await surface.screenshot(
                path=self.evidence / f"agent-workflow-data-line-{locale}.jpg",
                quality=88,
            )
            if locale == "en":
                inspector = surface.get_by_test_id("workflow-data-line")
                await inspector.get_by_role(
                    "button", name="Close data line inspector"
                ).click()
                await surface.locator(REVIEW_LINE).click(timeout=10000)
                await expect(
                    surface.get_by_test_id("inspection-context")
                ).to_contain_text(attempt)
            await surface.get_by_test_id("inspection-attempt").select_option(
                current["state"]["attempt_id"]
            )
            await expect(surface.get_by_test_id("inspection-context")).to_contain_text(
                current["state"]["attempt_id"]
            )
            await surface.get_by_test_id("inspection-round").select_option("revision:1")
            await expect(surface.get_by_test_id("inspection-value")).to_have_text(
                '"Cobalt draft, round 1"'
            )

        amber = await self.api(
            "POST",
            start,
            data={
                "version": 1,
                "inputs": {"draft": "Amber", "feedback": "Check two sources"},
            },
        )
        second_root = amber["run_id"]
        another = await self.wait_run(
            second_root, lambda run: run["status"] == "completed"
        )
        old_response_ready = asyncio.Event()
        release_old_response = asyncio.Event()
        old_response_released = asyncio.Event()

        async def delay_old_response(route):
            """Hold the first root's response while the second selection loads.

            Args:
                route: Intercepted browser request for the old data line.
            """
            response = await route.fetch()
            old_response_ready.set()
            await release_old_response.wait()
            try:
                await route.fulfill(response=response)
            finally:
                old_response_released.set()

        await bot.route(f"**/workflow_runs/{root}/data_line?*", delay_old_response)
        await bot.get_by_test_id("inspection-round").select_option("revision:2")
        await asyncio.wait_for(old_response_ready.wait(), 15)
        try:
            await bot.evaluate(
                "(id) => { location.hash = '/agent-teams?workflow_run=' + id; }",
                second_root,
            )
            await expect(bot.get_by_test_id("inspection-scope")).to_contain_text(
                another["scopes"][0]["run_id"][:8]
            )
            await bot.get_by_test_id("inspection-scope").select_option(
                another["scopes"][0]["run_id"]
            )
            await bot.locator(REVIEW_LINE).click(timeout=10000)
            await expect(bot.get_by_test_id("inspection-value")).to_have_text(
                '"Amber draft, round 2"'
            )
        finally:
            release_old_response.set()
            await asyncio.wait_for(old_response_released.wait(), 15)
        await expect(bot.get_by_test_id("inspection-value")).to_have_text(
            '"Amber draft, round 2"'
        )
        await (
            bot.get_by_test_id("workflow-data-line")
            .get_by_role("button", name="Close data line inspector")
            .click()
        )
        edge = bot.locator('.vue-flow__edge[data-id="data:input:review:draft"]')
        await edge.focus()
        await expect(edge).to_be_focused()
        await bot.keyboard.press("Enter")
        await expect(bot.get_by_test_id("inspection-value")).to_have_text(
            '"Amber draft, round 2"'
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard", type=Path, default=ROOT / "dashboard/dist")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--chromium")
    parsed = parser.parse_args()
    asyncio.run(
        InspectionAcceptance(
            SimpleNamespace(**vars(parsed), llama=None, weights=None)
        ).run()
    )
