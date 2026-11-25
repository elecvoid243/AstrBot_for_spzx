"""Verify original and current-draft debug forks in both Dashboard run views."""

import argparse
import asyncio
import json
import sqlite3
from contextlib import closing
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from playwright.async_api import expect
from pypdf import PdfWriter

from tests.agent_teams.report_release_browser import ROOT, SOURCE_MODEL, Acceptance
from tests.agent_teams.report_release_fixture import REPORT_FIELDS, TEXT, contract
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

            await (
                preview.get_by_test_id("debug-fork-input-picker")
                .get_by_role("combobox")
                .click()
            )
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

        await self.exercise_draft(bot, chat)
        await self.exercise_missing_pdf(bot, chat)

    async def exercise_draft(self, bot, chat):
        """Compare the historical definition and a current draft with renamed ports.

        Args:
            bot: English Dashboard workbench page.
            chat: Chinese Dashboard Chat page.
        """
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
        child_definition["tasks"][1]["output_contract"] = {
            **citations,
            "maxProperties": 1,
        }
        current_draft = await self.api(
            "PUT",
            f"/agent_drafts/workflow/{child_id}",
            data={
                "body": {"definition": child_definition},
                "expected_revision": revised_draft["revision"],
            },
        )

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
            await preview.get_by_test_id("debug-fork-use-draft").click()
            await expect(
                preview.get_by_test_id("debug-fork-draft-details")
            ).to_contain_text(f"r{current_draft['revision']}")
            await expect(preview).to_contain_text("Unpublished citation selection")
            await expect(preview.get_by_test_id("start-debug-fork")).to_be_enabled()
            await preview.get_by_test_id("debug-fork-use-source").click()
            await expect(
                preview.get_by_test_id("debug-fork-draft-details")
            ).to_have_count(0)
            await expect(preview).to_contain_text("v1")
            await (
                preview.get_by_test_id("debug-fork-input-picker")
                .get_by_role("combobox")
                .click()
            )
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

        renamed = {
            "type": "object",
            "properties": {"selected": citations["properties"]["citation"]},
            "required": ["selected"],
        }
        child_definition["tasks"][1].update(
            input_contract=renamed, output_contract=renamed, inputs={}
        )
        changed_draft = await self.api(
            "PUT",
            f"/agent_drafts/workflow/{child_id}",
            data={
                "body": {"definition": child_definition},
                "expected_revision": current_draft["revision"],
            },
        )
        for locale, page in (("en", bot), ("zh", chat)):
            await page.get_by_test_id("open-debug-fork").click()
            preview = page.get_by_test_id("debug-fork-preview")
            await preview.get_by_test_id("debug-fork-use-draft").click()
            details = preview.get_by_test_id("debug-fork-draft-details")
            await expect(details).to_contain_text(f"r{changed_draft['revision']}")
            await expect(details).to_contain_text(
                "Input citation removed" if locale == "en" else "已删除输入 citation"
            )
            await expect(
                preview.get_by_test_id("debug-fork-diagnostics")
            ).to_contain_text("inputs.selected")
            await expect(preview.get_by_test_id("start-debug-fork")).to_be_disabled()
            await expect(preview).to_contain_text("Source A")
            await preview.screenshot(
                path=self.evidence / f"agent-workflow-debug-draft-{locale}.jpg",
                quality=90,
                animations="disabled",
            )
            binding = preview.get_by_test_id("debug-fork-bind-selected")
            if locale == "en":
                await binding.click()
                await page.get_by_role("option", name="Enter a test value").click()
                await preview.locator("textarea").first.fill('{"source": 1e999}')
                await expect(preview).to_contain_text("Enter valid JSON.")
                await expect(
                    preview.get_by_test_id("debug-fork-check-bindings")
                ).to_be_disabled()
            await binding.click()
            await page.get_by_role(
                "option",
                name="Use recorded input citation"
                if locale == "en"
                else "沿用已记录的输入 citation",
                exact=True,
            ).click()
            await expect(preview.get_by_test_id("start-debug-fork")).to_be_disabled()
            await preview.get_by_test_id("debug-fork-check-bindings").click()
            await expect(
                preview.get_by_test_id("debug-fork-diagnostics")
            ).to_have_count(0)
            await preview.get_by_test_id("start-debug-fork").click()
            comparison = page.get_by_test_id("debug-fork-comparison")
            await expect(
                comparison.get_by_test_id("debug-fork-draft-details")
            ).to_contain_text(f"r{changed_draft['revision']}")
            result = comparison.locator(".fork-result").last
            await expect(result).to_contain_text('"selected"')
            await expect(result).to_contain_text("Source A")
            await page.reload()
            await expect(comparison).to_contain_text(
                "Mapped from recorded input citation"
                if locale == "en"
                else "映射自已记录的输入 citation"
            )

        saved = await self.api("GET", f"/workflow_runs/{root}")
        assert saved["cursor"] == source["cursor"]
        assert not self.requests

    async def exercise_missing_pdf(self, bot, chat):
        """Fill a missing attachment and compare exact versions after reloading.

        Args:
            bot: English Dashboard workbench page.
            chat: Chinese Dashboard Chat page.
        """
        draft_schema = contract({"text": TEXT, "confidence": {"type": "number"}})
        inputs = contract(
            {
                "draft": draft_schema,
                "pdf": {"type": "object", "format": "astrbot-file"},
                "report": {"type": "object", "format": "astrbot-artifact"},
            }
        )
        source_draft = await self.open_draft(
            {
                "name": "Report draft",
                "default_provider_id": SOURCE_MODEL,
                "result_task_id": "source",
                "tasks": [
                    {
                        "id": "source",
                        "name": "Prepare draft",
                        "member": "source",
                        "role": {
                            "name": "Prepare the draft",
                            "prompt": "Call submit_task_result, then reply Done.",
                            "tools": [],
                            "skills": [],
                        },
                        "installation": {"profile_id": "default"},
                        "task": "ATN180 SOURCE. Submit a draft without attachments.",
                        "output_contract": contract(
                            {"text": TEXT, "files": REPORT_FIELDS["files"]}
                        ),
                    },
                ],
            }
        )
        await self.publish(source_draft)
        started = await self.api(
            "POST",
            f"/workflow_assets/{source_draft['asset_id']}/runs",
            data={"version": 1},
        )
        source_run = await self.wait_run(
            started["run_id"], lambda run: run["status"] == "completed"
        )
        (source,) = source_run["tasks"]
        original = source["result_ref"]
        artifact_id = original["artifact_id"]
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        pdf = BytesIO()
        writer.write(pdf)
        stored = await self.api(
            "POST",
            f"/workflow_runs/{source['run_id']}/files",
            params={"filename": "citations.pdf"},
            data=pdf.getvalue(),
        )
        revisions = f"/workflow_artifacts/{artifact_id}/versions"
        pinned = await self.api(
            "POST",
            revisions,
            data={
                "expected_version": original["version"],
                "request_id": "add-citation-pdf",
                "content": {
                    "text": "Updated draft",
                    "files": [{"file_id": stored["file_id"]}],
                },
            },
        )
        workflow = await self.open_draft(
            {
                "name": "Review citation evidence",
                "port_contract_version": 2,
                "input_contract": inputs,
                "entry_task_ids": ["check"],
                "result_task_id": "check",
                "tasks": [
                    {
                        "id": "check",
                        "name": "Check citations",
                        "transform": "map_fields",
                        "input_contract": inputs,
                        "output_contract": contract({"draft": draft_schema}),
                        "inputs": {
                            name: {"input": name} for name in inputs["properties"]
                        },
                    },
                ],
            }
        )
        await self.publish(workflow)
        original_draft = {"text": source["result"]["text"], "confidence": 1.0}
        launched = await self.api(
            "POST",
            f"/workflow_assets/{workflow['asset_id']}/runs",
            data={
                "version": 1,
                "inputs": {
                    "draft": original_draft,
                    "pdf": {**pinned, "file_id": stored["file_id"]},
                    "report": {
                        "version": original["version"],
                        "artifact_id": artifact_id,
                    },
                },
            },
        )
        root = launched["run_id"]
        run = await self.wait_run(root, lambda run: run["status"] == "completed")
        (check,) = run["tasks"]
        # Reproduce a historical snapshot that retained the read marker but lost the PDF reference.
        with closing(sqlite3.connect(self.runtime / "data/data_v4.db")) as db, db:
            (payload,) = db.execute(
                "SELECT snapshot FROM agent_workflow_runs WHERE run_id = ?",
                (check["run_id"],),
            ).fetchone()
            snapshot = json.loads(payload)
            del snapshot["inputs"]["pdf"]
            db.execute(
                "UPDATE agent_workflow_runs SET snapshot = ? WHERE run_id = ?",
                (json.dumps(snapshot), check["run_id"]),
            )

        for locale, page, route in (
            ("en", bot, f"/#/agent-teams?workflow_run={root}"),
            ("zh", chat, f"/#/chat/runs/{root}"),
        ):
            await page.goto(self.origin + route)
            await page.get_by_role(
                "button",
                name="Expand execution graph (read-only)"
                if locale == "en"
                else "展开执行图（只读）",
                exact=True,
            ).click()
            edge = page.locator(
                '.vue-flow__edge.inspection-edge[data-id="data:input:check:pdf"]'
            )
            await edge.focus()
            await page.keyboard.press("Enter")
            await page.get_by_test_id("open-debug-fork").click()
            preview = page.get_by_test_id("debug-fork-preview")
            start = preview.get_by_test_id("start-debug-fork")
            field = preview.get_by_test_id("debug-fork-input-picker").get_by_role(
                "combobox"
            )
            missing = "Historical value missing" if locale == "en" else "历史值缺失"
            await expect(preview).to_contain_text(missing)
            await expect(preview).to_contain_text("Fixed input documents")
            await expect(start).to_be_disabled()
            for name in ("pdf", "report"):
                await field.click()
                await page.get_by_role("option", name=name, exact=True).click()
                await (
                    preview.get_by_test_id("debug-fork-artifact-id")
                    .locator("input")
                    .first.fill(artifact_id)
                )
                version = (
                    preview.get_by_test_id("debug-fork-artifact-version")
                    .locator("input")
                    .first
                )
                if name == "pdf":
                    await version.fill(str(original["version"]))
                    await expect(preview).to_contain_text(
                        "This version has no managed attachments"
                        if locale == "en"
                        else "此版本没有受管附件"
                    )
                    await expect(start).to_be_disabled()
                if name == "report":
                    await version.fill(str(original["version"]))
                    await expect(start).to_be_enabled()
                    await expect(
                        preview.get_by_role("row", name="report", exact=False)
                    ).to_contain_text("Reused" if locale == "en" else "沿用")
                await version.fill(str(pinned["version"]))
                if name == "pdf":
                    await (
                        preview.get_by_test_id("debug-fork-attachment")
                        .get_by_role("combobox")
                        .click()
                    )
                    await page.get_by_role(
                        "option", name="citations.pdf", exact=False
                    ).click()
            await expect(preview).to_contain_text(
                "Filled" if locale == "en" else "补入"
            )
            await expect(preview).to_contain_text(
                "Replaced" if locale == "en" else "替换"
            )
            await expect(start).to_be_enabled()
            await field.click()
            await page.get_by_role("option", name="draft", exact=True).click()
            editor = (
                preview.get_by_test_id("debug-fork-replacement")
                .locator("textarea")
                .first
            )
            await editor.fill(
                json.dumps({**original_draft, "confidence": "1e400"}).replace(
                    '"1e400"', "1e400"
                )
            )
            await expect(start).to_be_disabled()
            await editor.fill(json.dumps({**original_draft, "confidence": 0.5}))
            await expect(start).to_be_enabled()
            await expect(
                preview.get_by_role("row", name="draft", exact=False).get_by_test_id(
                    "debug-fork-changed-value"
                )
            ).to_contain_text('"confidence": 0.5')
            await field.click()
            await page.get_by_role("option", name="pdf", exact=True).click()
            await expect(
                preview.get_by_test_id("debug-fork-attachment")
            ).to_contain_text("citations.pdf")
            await preview.screenshot(
                path=self.evidence / f"agent-workflow-debug-fork-inputs-{locale}.jpg",
                quality=88,
            )
            await start.click()
            comparison = page.get_by_test_id("debug-fork-comparison")
            await expect(comparison).to_contain_text(
                "Filled" if locale == "en" else "补入"
            )
            if locale == "en":
                await self.api(
                    "POST",
                    revisions,
                    data={
                        "expected_version": pinned["version"],
                        "request_id": "upload-after-fork",
                        "content": {"text": "Newest draft", "files": []},
                    },
                )
            await page.reload()
            await expect(comparison).to_contain_text(stored["file_id"])
            await expect(comparison).to_contain_text("Fixed input documents")
            await expect(comparison).to_contain_text(
                "Replaced" if locale == "en" else "替换"
            )


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
