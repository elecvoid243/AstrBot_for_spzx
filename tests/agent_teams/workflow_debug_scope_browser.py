"""Verify the downstream and fixed-child debug scopes in Bot and Chat."""

import argparse
import asyncio
import json
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from playwright.async_api import expect

from tests.agent_teams.report_release_browser import ROOT, Acceptance
from tests.agent_teams.test_workflow_debug_forks import debug_delivery_definitions
from tests.agent_teams.test_workflow_plugin_restart import external_ledger
from tests.agent_teams.workflow_debug_write_browser import DebugWriteAcceptance


class DebugScopeAcceptance(DebugWriteAcceptance):
    """Run the real Dashboard with deterministic transforms and an empty ledger."""

    async def run(self):
        """Keep the local embedding endpoint and independent ledger available."""
        with contextmanager(external_ledger.__wrapped__)() as ledger, patch.dict(
            "os.environ",
            {"ATN178_LEDGER_URL": ledger.url, "ATN178_LEDGER_KEY": "local-ledger-secret"},
        ):
            self.ledger_url = ledger.url
            self.deliveries = ledger.deliveries
            await Acceptance.run(self)

    async def exercise(self, browser):
        """Start both debug scopes, review the fresh artifact, and stop at preview.

        Args:
            browser: Chromium connected to the isolated Dashboard.
        """
        self.context = await browser.new_context(viewport={"width": 1440, "height": 980})
        bot = await self.login(self.context, "report-owner", self.password, "en-US")
        zh_context = await browser.new_context(viewport={"width": 1440, "height": 980})
        chat = await self.login(zh_context, "report-owner", self.password, "zh-CN")
        await self.api(
            "POST",
            "/providers",
            data={
                "config": {
                    "id": "scope-embedding",
                    "type": "openai_embedding",
                    "provider_type": "embedding",
                    "embedding_model": "scope-embedding",
                    "enable": True,
                    "embedding_api_key": "fixture-key",
                    "embedding_api_base": self.ledger_url + "/v1",
                    "embedding_dimensions": 3,
                },
            },
        )
        binding = (await self.api(
            "POST",
            "/knowledge-bases",
            data={"name": "Source references", "embedding_provider_id": "scope-embedding"},
        ))["kb_id"]
        component = next(
            item for item in await self.api("GET", "/workflow_components")
            if item["reference"]["id"].endswith("/previewable")
        )
        child = await self.open_draft(debug_delivery_definitions(component, binding))
        await self.publish(child)
        parent = await self.open_draft(
            debug_delivery_definitions(component, binding, child["asset_id"])
        )
        await self.publish(parent)
        start = await self.api(
            "POST",
            f"/workflow_assets/{parent['asset_id']}/runs",
            data={
                "version": 1,
                "inputs": {
                    "packet": json.dumps({"report": "Original report", "send": False, "record": {"count": 7}}),
                    "source_notes": "Pinned source notes",
                },
            },
        )
        root = start["run_id"]
        original = await self.wait_run(root, lambda run: bool(run["todos"]))
        (old_todo,) = original["todos"]
        original = await self.api(
            "POST",
            f"/workflow_runs/{root}/todos/{old_todo['todo_id']}/answer",
            data={
                "request_id": "original-report",
                "action": "approve",
                "artifact_ref": old_todo["artifact_ref"],
            },
        )
        assert original["status"] == "completed"
        (child_scope,) = original["scopes"]
        assert not self.deliveries

        for locale, page, route, expand in (
            ("en", bot, f"/#/agent-teams?workflow_run={root}", "Expand execution graph (read-only)"),
            ("zh", chat, f"/#/chat/runs/{root}", "展开执行图（只读）"),
        ):
            await page.goto(self.origin + route)
            await page.get_by_role("button", name=expand, exact=True).click()
            preview = page.get_by_test_id("debug-fork-preview")
            if locale == "en":
                await page.get_by_test_id("inspection-scope").select_option(child_scope["run_id"])
                edge = page.locator('.vue-flow__edge.inspection-edge[data-id="data:input:decode:text"]')
                await edge.focus()
                await page.keyboard.press("Enter")
                await page.get_by_test_id("open-debug-fork").click()
                await expect(preview.get_by_test_id("debug-fork-scope-select")).to_have_value("single")
                await preview.get_by_test_id("debug-fork-scope-select").select_option("downstream")
                await expect(preview.get_by_test_id("debug-fork-boundary")).to_contain_text("Prepare source notes")
            else:
                await (
                    page.get_by_test_id("workflow-run-canvas")
                    .locator('.vue-flow__node[data-id="review-report"] .member-flow-node')
                    .click()
                )
                await page.get_by_test_id("debug-whole-child").click()
                await expect(preview.get_by_test_id("debug-fork-scope")).to_contain_text("试跑整个固定子流程")
                await expect(preview.get_by_test_id("debug-fork-nodes")).to_contain_text("Prepare source notes")
                await expect(preview).to_contain_text("Pinned source notes")
            await expect(preview.get_by_test_id("debug-fork-nodes")).to_contain_text("Deliver approved report")
            await expect(preview).to_contain_text("v1")
            assert not self.deliveries
            await page.screenshot(
                path=self.evidence / f"agent-workflow-debug-scope-{locale}.jpg",
                quality=88,
                animations="disabled",
            )
            await preview.locator(".v-select").get_by_role("combobox").click()
            await page.get_by_role("option", name="text", exact=True).click()
            await page.get_by_test_id("debug-fork-replacement").locator("textarea").first.fill(
                json.dumps({"report": "Edited report", "send": True, "record": {"count": 12}})
            )
            async with page.expect_response(
                lambda response: response.url.endswith(f"/{root}/debug_forks")
                and response.request.method == "POST"
            ) as started:
                await page.get_by_test_id("start-debug-fork").click()
            response = await started.value
            assert response.ok, await response.text()
            fork_id = (await response.json())["data"]["run_id"]
            await expect(page).to_have_url(re.compile(fork_id))
            await expect(page.get_by_test_id("debug-fork-comparison")).to_be_visible()
            fork = await self.wait_run(fork_id, lambda run: bool(run["todos"]))
            (todo,) = fork["todos"]
            assert todo["todo_id"] != old_todo["todo_id"]
            assert todo["artifact_ref"] != old_todo["artifact_ref"]
            assert not self.deliveries
            await page.get_by_test_id("run-detail").get_by_role(
                "button", name="Run workspace" if locale == "en" else "运行工作台", exact=True
            ).click()
            results = page.get_by_role(
                "button", name="Inbox & artifacts" if locale == "en" else "待办与产物", exact=True
            )
            if await results.is_visible():
                await results.click()
            approval = page.locator(f'[data-testid="human-todo"][data-todo-id="{todo["todo_id"]}"]')
            await approval.get_by_test_id("human-preview").click()
            await expect(approval).to_contain_text("Edited report")
            await expect(approval).to_contain_text("Pinned source notes")
            await approval.get_by_test_id("human-submit").click()
            await page.get_by_test_id("open-debug-write-preview").click()
            write = page.get_by_test_id("debug-write-preview")
            await expect(write).to_contain_text("team/reports")
            await expect(write).to_contain_text('"count": 12')
            assert not self.deliveries
            other = await page.context.new_page()
            other_path = f"/#/chat/runs/{fork_id}" if locale == "en" else f"/#/agent-teams?workflow_run={fork_id}"
            await other.goto(self.origin + other_path)
            await other.get_by_test_id("open-debug-write-preview").click()
            await expect(other.get_by_test_id("debug-write-preview")).to_contain_text("team/reports")
            await other.close()
        persisted = await self.api("GET", f"/workflow_runs/{root}")
        assert original["cursor"] == persisted["cursor"]
        assert not self.deliveries
        assert not self.requests
        print("Bot and Chat observed both explicit scopes with zero ledger writes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard", type=Path, default=ROOT / "dashboard/dist")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--chromium")
    parsed = parser.parse_args()
    asyncio.run(
        DebugScopeAcceptance(
            SimpleNamespace(**vars(parsed), llama=None, weights=None)
        ).run()
    )
