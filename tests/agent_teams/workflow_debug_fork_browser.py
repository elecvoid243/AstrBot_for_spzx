"""Verify an original-version debug fork in both Dashboard run views."""

import argparse
import asyncio
from pathlib import Path
from types import SimpleNamespace

from playwright.async_api import expect

from tests.agent_teams.report_release_browser import ROOT, Acceptance
from tests.agent_teams.test_workflow_debug_forks import failed_child_definitions


class DebugForkAcceptance(Acceptance):
    """Run deterministic transforms against the disposable Dashboard."""

    async def exercise(self, browser):
        """Compare a historical child node after its draft and version change.

        Args:
            browser: Chromium connected to the isolated Dashboard.
        """
        self.context = await browser.new_context(
            viewport={"width": 1440, "height": 980}
        )
        bot = await self.login(self.context, "report-owner", self.password, "en-US")
        child_definition = failed_child_definitions()
        child = await self.open_draft(child_definition)
        await self.publish(child)
        child_id = child["asset_id"]
        parent = await self.open_draft(failed_child_definitions(child_id))
        await self.publish(parent)
        started = await self.api(
            "POST",
            f"/workflow_assets/{parent['asset_id']}/runs",
            data={
                "version": 1,
                "inputs": {
                    "text": '{"text":"Invalid citations in round two","approved":false}',
                    "approved": False,
                },
            },
        )
        root = started["run_id"]
        source = await self.wait_run(root, lambda run: run["status"] == "blocked")
        first, second = [
            round_["tasks"][0] for round_ in source["loops"][0]["iterations"]
        ]
        assert first["status"] == "completed"
        assert second["status"] == "blocked"
        scope_id = second["state"]["child_scope_run_id"]
        scope = next(scope for scope in source["scopes"] if scope["run_id"] == scope_id)
        check = next(task for task in scope["tasks"] if task["node_id"] == "check")
        bibliography = next(
            task for task in scope["tasks"] if task["node_id"] == "cross-check"
        )
        attempt = check["state"]["attempt_id"]
        await self.api(
            "POST",
            f"/workflow_runs/{check['run_id']}/retry",
            data={
                "request_id": "inner-retry",
                "attempt_id": attempt,
                "input_policy": "original",
            },
        )
        source = await self.wait_run(root, lambda run: run["status"] == "blocked")
        child_definition["tasks"][1]["name"] = "Updated citation selection"
        revised_draft = await self.api(
            "PUT",
            f"/agent_drafts/workflow/{child_id}",
            data={
                "body": {"definition": child_definition},
                "expected_revision": child["revision"],
            },
        )
        assert (await self.publish(revised_draft))["version"] == 2
        child_definition["tasks"][1]["name"] = "Unpublished citation selection"
        await self.api(
            "PUT",
            f"/agent_drafts/workflow/{child_id}",
            data={
                "body": {"definition": child_definition},
                "expected_revision": revised_draft["revision"],
            },
        )

        chat_context = await browser.new_context(
            viewport={"width": 1440, "height": 980}
        )
        chat = await self.login(chat_context, "report-owner", self.password, "zh-CN")
        for locale, page, route in (
            ("en", bot, f"/#/agent-teams?workflow_run={root}"),
            ("zh", chat, f"/#/chat/runs/{root}"),
        ):
            await page.goto(self.origin + route)
            graph = page.get_by_role(
                "button",
                name="Expand execution graph (read-only)"
                if locale == "en"
                else "展开执行图（只读）",
                exact=True,
            )
            await graph.click()
            await page.get_by_test_id("inspection-round").select_option(
                "review-rounds:2"
            )
            await (
                page.get_by_test_id("workflow-run-canvas")
                .locator('.vue-flow__node[data-id="review"] .member-flow-node')
                .click()
            )
            await page.get_by_test_id("debug-failed-child").click()
            canvas_scope = page.get_by_test_id("inspection-scope")
            await expect(canvas_scope).to_have_value(scope_id)

            preview = page.get_by_test_id("debug-fork-preview")
            await expect(preview).to_contain_text("Check citations")
            await expect(preview).to_contain_text("v1")
            await expect(preview).to_contain_text("Invalid citations in round two")
            await expect(
                preview.get_by_test_id("debug-fork-parent-context")
            ).to_contain_text("Review citations")
            await expect(
                preview.get_by_test_id("debug-fork-parent-context")
            ).to_contain_text("2")
            chooser = preview.get_by_test_id("debug-fork-candidate")
            assert await chooser.locator("option").count() == 3
            await chooser.select_option(bibliography["state"]["attempt_id"])
            await expect(preview.locator("p.font-weight-medium")).to_contain_text(
                "Check bibliography"
            )
            await chooser.select_option(attempt)
            await expect(preview.locator("p.font-weight-medium")).to_contain_text(
                "Check citations"
            )
            await expect(preview.locator(".debug-attempt")).to_contain_text(attempt)
            await page.screenshot(
                path=self.evidence / f"agent-workflow-debug-child-{locale}.jpg",
                quality=88,
            )

            await preview.locator(".v-select").get_by_role("combobox").click()
            await page.get_by_role("option", name="text", exact=True).click()
            await (
                page.get_by_test_id("debug-fork-replacement")
                .locator("textarea")
                .first.fill('{"text":"Verified citations","approved":true}')
            )
            await expect(
                preview.get_by_test_id("debug-fork-changed-value")
            ).to_contain_text("Verified citations")
            await page.get_by_test_id("start-debug-fork").click()
            comparison = page.get_by_test_id("debug-fork-comparison")
            await expect(comparison).to_contain_text("Invalid citations in round two")
            await expect(comparison).to_contain_text("Verified citations")
            await page.reload()
            await expect(comparison).to_contain_text("Verified citations")
            await expect(
                comparison.get_by_test_id("debug-fork-parent-context")
            ).to_contain_text("Review citations")
            await comparison.scroll_into_view_if_needed()
            await page.screenshot(
                path=self.evidence
                / f"agent-workflow-debug-child-comparison-{locale}.jpg",
                quality=88,
            )
            await page.get_by_test_id("debug-fork-parent").click()
            await expect(canvas_scope).to_have_value(root)
            await expect(page.get_by_test_id("inspection-round")).to_have_value(
                "review-rounds:2"
            )
            await expect(page.get_by_test_id("failed-child-call")).to_contain_text(
                "Review citations"
            )
            await page.go_back()
            await expect(comparison).to_contain_text("Verified citations")
            await page.get_by_test_id("debug-fork-source").click()
            await expect(canvas_scope).to_have_value(scope_id)
            await expect(page.get_by_test_id("inspection-context")).to_contain_text(
                attempt
            )
            await expect(page.get_by_test_id("inspection-value")).to_contain_text(
                "Invalid citations in round two"
            )

        saved = await self.api("GET", f"/workflow_runs/{root}")
        assert saved["cursor"] == source["cursor"]
        assert saved["scopes"] == source["scopes"]
        assert not self.requests


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard", type=Path, default=ROOT / "dashboard/dist")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--chromium")
    parsed = parser.parse_args()
    asyncio.run(
        DebugForkAcceptance(
            SimpleNamespace(**vars(parsed), llama=None, weights=None)
        ).run()
    )
