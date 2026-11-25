"""Verify an original-version debug fork in both Dashboard run views."""

import argparse
import asyncio
from pathlib import Path
from types import SimpleNamespace

from playwright.async_api import expect

from tests.agent_teams.report_release_browser import ROOT, Acceptance


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
        text = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }
        citations = {
            "type": "object",
            "properties": {
                "citation": {
                    "type": "object",
                    "properties": {"source": {"type": "string"}},
                    "required": ["source"],
                }
            },
            "required": ["citation"],
        }
        child_definition = {
            "name": "Citation review",
            "port_contract_version": 2,
            "input_contract": text,
            "entry_task_ids": ["parse", "check", "format"],
            "result_task_id": "format",
            "tasks": [
                {
                    "id": "parse",
                    "name": "Read citations",
                    "transform": "parse_json",
                    "output_contract": citations,
                    "inputs": {"text": {"input": "text"}},
                    "position": {"x": 180, "y": 0},
                },
                {
                    "id": "check",
                    "name": "Select citation",
                    "transform": "map_fields",
                    "input_contract": citations,
                    "output_contract": citations,
                    "inputs": {"citation": {"task_id": "parse", "field": "citation"}},
                    "position": {"x": 430, "y": 0},
                },
                {
                    "id": "format",
                    "name": "Format report",
                    "transform": "map_fields",
                    "input_contract": citations,
                    "output_contract": citations,
                    "inputs": {"citation": {"task_id": "check", "field": "citation"}},
                    "position": {"x": 680, "y": 0},
                },
            ],
        }
        child = await self.open_draft(child_definition)
        await self.publish(child)
        child_id = child["asset_id"]
        parent = await self.open_draft(
            {
                "name": "Document report",
                "port_contract_version": 2,
                "input_contract": text,
                "entry_task_ids": ["review"],
                "result_task_id": "review",
                "tasks": [
                    {
                        "id": "review",
                        "name": "Review citations",
                        "workflow": {"asset_id": child_id, "version": 1},
                        "input_contract": text,
                        "output_contract": citations,
                        "inputs": {"text": {"input": "text"}},
                    }
                ],
            }
        )
        await self.publish(parent)
        started = await self.api(
            "POST",
            f"/workflow_assets/{parent['asset_id']}/runs",
            data={
                "version": 1,
                "inputs": {"text": '{"citation":{"source":"Source A"}}'},
            },
        )
        root = started["run_id"]
        source = await self.wait_run(root, lambda run: run["status"] == "completed")
        (scope,) = source["scopes"]
        check = next(task for task in scope["tasks"] if task["node_id"] == "check")
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
            await page.get_by_test_id("inspection-scope").select_option(scope["run_id"])
            edge = page.locator(
                '.vue-flow__edge.inspection-edge[data-id="data:input:check:citation"]'
            )
            await edge.focus()
            await page.keyboard.press("Enter")
            await expect(page.get_by_test_id("inspection-value")).to_contain_text(
                "Source A"
            )
            await page.get_by_test_id("open-debug-fork").click()
            preview = page.get_by_test_id("debug-fork-preview")
            await expect(preview).to_contain_text("Select citation")
            await expect(preview).to_contain_text("v1")
            await expect(preview).to_contain_text(check["state"]["attempt_id"])
            await expect(preview).to_contain_text("Source A")
            await preview.get_by_role("combobox").click()
            await page.get_by_role("option", name="citation", exact=True).click()
            await (
                page.get_by_test_id("debug-fork-replacement")
                .locator("textarea")
                .first.fill('{"source":"Source B"}')
            )
            await expect(
                preview.get_by_test_id("debug-fork-changed-value")
            ).to_contain_text("Source B")
            await page.get_by_test_id("start-debug-fork").click()
            comparison = page.get_by_test_id("debug-fork-comparison")
            await expect(comparison).to_contain_text("Source A")
            await expect(comparison).to_contain_text("Source B")
            await page.reload()
            await expect(comparison).to_contain_text("Source B")
            await comparison.scroll_into_view_if_needed()
            await page.screenshot(
                path=self.evidence / f"agent-workflow-debug-fork-{locale}.jpg",
                quality=88,
            )
            await page.get_by_test_id("debug-fork-source").click()
            await expect(page.get_by_test_id("inspection-context")).to_contain_text(
                check["state"]["attempt_id"]
            )
            await expect(page.get_by_test_id("inspection-value")).to_contain_text(
                "Source A"
            )

        saved = await self.api("GET", f"/workflow_runs/{root}")
        assert saved["cursor"] == source["cursor"]
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
