"""Follow one historical report through diagnosis, repair and delivery."""

import argparse
import asyncio
import json
import re
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from playwright.async_api import expect
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from tests.agent_teams.report_debug_fixture import (
    ARCHIVE_TARGET,
    DELIVERY_TARGET,
    FILE,
    SIMULATED_RECEIPT,
    citation_review,
    report_with_review,
)
from tests.agent_teams.report_release_browser import API, MODEL, ROOT, Acceptance
from tests.agent_teams.report_release_fixture import FACTS, definitions
from tests.agent_teams.workflow_debug_scope_browser import DebugScopeAcceptance


class ReportDebugAcceptance(DebugScopeAcceptance):
    """Use the report model and installed connector in a disposable Dashboard."""

    controlled_completion = Acceptance.controlled_completion

    async def citation_pdfs(self, source_ref):
        """Publish two readable PDFs as different exact versions.

        Args:
            source_ref: Existing version of the managed report input artifact.

        Returns:
            Current and superseded citation PDF references in creation order.
        """
        (source_run,) = (await self.api("GET", "/workflow_runs"))["runs"]
        source = await self.api("GET", f"/workflow_runs/{source_run['run_id']}")
        (task,) = source["tasks"]
        artifact = source_ref["artifact_id"]
        version = source_ref["version"]
        refs = []
        for filename, heading in (
            ("citations.pdf", "Citation index"),
            ("superseded-citations.pdf", "Superseded citation index"),
        ):
            stream = BytesIO()
            pdf = Canvas(stream)
            pdf.drawString(72, 750, heading)
            for y, fact in zip((723, 696), FACTS.values(), strict=True):
                pdf.drawString(
                    72, y, f"{fact['source']} / {fact['heading']} / {fact['value']}"
                )
            pdf.save()
            data = stream.getvalue()
            stored = await self.api(
                "POST",
                f"/workflow_runs/{task['run_id']}/files",
                params={"filename": filename},
                data=data,
            )
            saved = await self.api(
                "POST",
                f"/workflow_artifacts/{artifact}/versions",
                data={
                    "expected_version": version,
                    "request_id": filename,
                    "content": {
                        "text": heading,
                        "files": [{"file_id": stored["file_id"]}],
                    },
                },
            )
            version = saved["version"]
            ref = {**saved, "file_id": stored["file_id"]}
            response = await self.context.request.get(self.origin + API + pdf_path(ref))
            assert response.ok, await response.text()
            body = await response.body()
            assert body == data
            parsed = PdfReader(BytesIO(body)).pages[0].extract_text()
            assert heading in parsed and "DOC-A" in parsed and "DOC-B" in parsed
            refs.append(ref)
        return refs

    async def exercise(self, browser):
        """Run the historical scenario, then create its independent debug forks.

        Args:
            browser: Chromium running against the isolated Dashboard.
        """
        viewport = {"width": 1512, "height": 1080}
        self.context = await browser.new_context(viewport=viewport)
        bot = await self.login(self.context, "report-owner", self.password, "en-US")
        chat = await self.login(
            await browser.new_context(viewport=viewport),
            "report-owner",
            self.password,
            "zh-CN",
        )
        source_inputs = await self.source_inputs()
        good_pdf, bad_pdf = await self.citation_pdfs(source_inputs["document_a"])
        await self.api(
            "POST",
            "/providers",
            data={
                "config": {
                    "id": "report-debug-embedding",
                    "type": "openai_embedding",
                    "provider_type": "embedding",
                    "embedding_model": "report-debug-embedding",
                    "enable": True,
                    "embedding_api_key": "fixture-key",
                    "embedding_api_base": self.ledger_url + "/v1",
                    "embedding_dimensions": 3,
                }
            },
        )
        binding = (
            await self.api(
                "POST",
                "/knowledge-bases",
                data={
                    "name": "Citation delivery fixture",
                    "embedding_provider_id": "report-debug-embedding",
                },
            )
        )["kb_id"]
        component = next(
            item
            for item in await self.api("GET", "/workflow_components")
            if item["reference"]["id"].endswith("/previewable")
        )
        child = citation_review(good_pdf, component, binding)
        renderer, _ = definitions(None)
        renderer["default_provider_id"] = MODEL
        renderer_draft = await self.open_draft(renderer)
        await self.publish(renderer_draft)
        _, report = definitions({"asset_id": renderer_draft["asset_id"], "version": 1})
        report["default_provider_id"] = MODEL

        child_id, child_draft = await self.publish_versions(child, "Citation review")
        parent = report_with_review(report, child, child_id)
        parent_id, _ = await self.publish_versions(parent, report["name"])
        started = await self.api(
            "POST",
            f"/workflow_assets/{parent_id}/runs",
            data={
                "version": 3,
                "inputs": {
                    **source_inputs,
                    "pdf": good_pdf,
                    "next_pdf": bad_pdf,
                    "revised": False,
                    "repeat": True,
                    "record": {"count": 77},
                    "delivery_target": DELIVERY_TARGET,
                    "archive_target": ARCHIVE_TARGET,
                },
            },
        )
        root = started["run_id"]
        first_approval = await self.todo(root)
        await bot.goto(self.origin + run_route(root, False))
        await self.review(bot, first_approval)
        historical = await self.wait_run(root, lambda run: run["status"] == "blocked")
        assert historical["snapshot"]["asset_version"] == 3
        first, second = [
            round_["tasks"][0] for round_ in historical["loops"][0]["iterations"]
        ]
        assert first["status"] == "completed" and second["status"] == "blocked"
        first_scope, second_scope = (
            next(
                scope
                for scope in historical["scopes"]
                if scope["run_id"] == call["state"]["child_scope_run_id"]
            )
            for call in (first, second)
        )
        good_check, failed, skipped = (
            node(first_scope, "check"),
            node(second_scope, "check"),
            node(second_scope, "unused"),
        )
        assert good_check["status"] == "completed"
        assert good_check["result"]["pdf"] == good_pdf
        draft = good_check["result"]["draft"]
        assert all(
            fact["source"] in draft and f"{fact['value']} households" in draft
            for fact in FACTS.values()
        )
        assert failed["status"] == "failed", failed["state"]
        assert (
            failed["state"]["reason"] == "Transform result does not match its contract"
        )
        assert skipped["status"] == "skipped"
        assert node(second_scope, "prepare")["result"]["draft"] == draft
        assert first_approval["artifact_ref"] == good_check["result_ref"]
        assert [(todo["todo_id"], todo["status"]) for todo in historical["todos"]] == [
            (first_approval["todo_id"], "approved")
        ]
        assert not self.deliveries

        # The new policy recognizes the second attachment without changing the frozen v3.
        check = next(task for task in child["tasks"] if task["id"] == "check")
        check["name"] = "Check citations with updated policy"
        check["output_contract"]["properties"]["pdf"] = {
            **FILE,
            "enum": [good_pdf, bad_pdf],
        }
        revision = await self.api(
            "PUT",
            f"/agent_drafts/workflow/{child_id}",
            data={
                "expected_revision": child_draft["revision"],
                "body": {"definition": child},
            },
        )
        await self.exercise_forks(
            browser,
            bot,
            chat,
            historical,
            first_approval,
            good_pdf,
            bad_pdf,
            revision["revision"],
        )

    async def publish_versions(self, definition, name):
        """Publish two previous versions and pin the scenario's named v3.

        Args:
            definition: Workflow to publish through the asset editor API.
            name: Name of the v3 definition.

        Returns:
            Published asset identity and its latest draft revision.
        """
        draft = await self.open_draft({**definition, "name": f"{name} (v1)"})
        asset = draft["asset_id"]
        assert (await self.publish(draft))["version"] == 1
        for version in (2, 3):
            draft = await self.api(
                "PUT",
                f"/agent_drafts/workflow/{asset}",
                data={
                    "expected_revision": draft["revision"],
                    "body": {
                        "definition": {
                            **definition,
                            "name": f"{name} (v2)" if version == 2 else name,
                        }
                    },
                },
            )
            assert (await self.publish(draft))["version"] == version
        return asset, draft

    async def exercise_forks(
        self, browser, bot, chat, historical, old_approval, good_pdf, bad_pdf, revision
    ):
        """Inspect both rounds, rehearse the two fixes, and check durable provenance.

        Args:
            browser: Chromium for an independent unauthorized login.
            bot: English Bot workbench.
            chat: Chinese Chat run conversation.
            historical: Blocked v3 root with both fixed citation call scopes.
            old_approval: The exact first-round approval already answered.
            good_pdf: Exact replacement with the known v3 PDF.
            bad_pdf: Exact superseded PDF consumed by the failed attempt.
            revision: Unpublished child draft to verify explicitly.
        """
        root = historical["run_id"]
        rounds = historical["loops"][0]["iterations"]
        first, second = (
            next(
                scope
                for scope in historical["scopes"]
                if scope["run_id"] == round_["tasks"][0]["state"]["child_scope_run_id"]
            )
            for round_ in rounds
        )
        failed = node(second, "check")
        attempt = failed["state"]["attempt_id"]
        selection = {
            "scope_run_id": second["run_id"],
            "task_run_id": failed["run_id"],
            "attempt_id": attempt,
        }
        draft = node(first, "check")["result"]["draft"]
        source_path = f"/workflow_runs/{root}"
        await self.api(
            "POST",
            "/accounts",
            data={"username": "report-viewer", "password": self.password},
        )
        viewer = await browser.new_context()
        private = await self.login(viewer, "report-viewer", self.password, "en-US")
        await private.goto(self.origin + run_route(root, True))
        await expect(private.get_by_test_id("run-detail")).to_have_count(0)
        for method, path, options in (
            (
                "GET",
                f"{source_path}/data_line",
                {"params": {**selection, "input_name": "pdf"}},
            ),
            ("POST", f"{source_path}/debug_forks/preview", {"data": selection}),
            ("GET", pdf_path(good_pdf), {}),
            ("GET", pdf_path(bad_pdf), {}),
        ):
            denied = await viewer.request.fetch(
                self.origin + API + path, method=method, **options
            )
            assert denied.status in {403, 404}, await denied.text()

        for page, zh in ((bot, False), (chat, True)):
            open_graph = page.get_by_role(
                "button",
                name="展开执行图（只读）"
                if zh
                else "Expand execution graph (read-only)",
                exact=True,
            )
            scope = page.get_by_test_id("inspection-scope")
            round_picker = page.get_by_test_id("inspection-round")
            canvas = page.get_by_test_id("workflow-run-canvas")
            inspector = page.get_by_test_id("workflow-data-line")
            preview = page.get_by_test_id("debug-fork-preview")
            comparison = page.get_by_test_id("debug-fork-comparison")
            locale = "zh" if zh else "en"
            await page.goto(self.origin + run_route(root, zh))
            await open_graph.click()
            await scope.select_option(first["run_id"])
            await inspect_edge(page, "data:input:check:pdf")
            await expect(inspector.get_by_test_id("inspection-value")).to_contain_text(
                good_pdf["file_id"]
            )
            await expect(
                inspector.get_by_test_id("inspection-context")
            ).to_contain_text(node(first, "check")["state"]["attempt_id"])
            await scope.select_option(second["run_id"])
            await inspect_edge(page, "data:input:unused:value")
            await expect(
                inspector.get_by_test_id("inspection-read-status")
            ).to_have_text("已跳过 · 未读取" if zh else "Skipped · not read")
            await expect(inspector.get_by_test_id("inspection-value")).to_have_count(0)
            upstream = inspector.get_by_test_id("inspection-upstream")
            await upstream.locator("summary").click()
            await expect(upstream.locator("pre")).to_have_text(json.dumps(draft))
            await expect(upstream).to_contain_text(
                node(second, "prepare")["result_ref"]["artifact_id"]
            )
            await inspector.scroll_into_view_if_needed()
            await page.screenshot(
                path=self.evidence / f"report-debug-skipped-{locale}.png",
                animations="disabled",
            )
            await inspect_edge(page, "data:input:check:pdf")
            await expect(inspector.get_by_test_id("inspection-value")).to_contain_text(
                bad_pdf["file_id"]
            )
            await expect(
                inspector.get_by_test_id("inspection-context")
            ).to_contain_text(attempt)
            await expect(
                inspector.get_by_test_id("inspection-evidence")
            ).to_contain_text(node(second, "prepare")["result_ref"]["artifact_id"])
            await scope.select_option(root)
            await round_picker.select_option("citation-rounds:2")
            await canvas.locator(
                '.vue-flow__node[data-id="review"] .member-flow-node'
            ).click()
            async with page.expect_response(
                lambda response: response.url.endswith("/debug_forks/preview")
            ) as planned:
                await page.get_by_test_id("debug-failed-child").click()
            plan_response = await planned.value
            assert plan_response.ok, await plan_response.text()
            plan = (await plan_response.json())["data"]
            assert plan["source"]["task_run_id"] == failed["run_id"]
            assert plan["source"]["attempt_id"] == attempt
            assert plan["source"]["asset_version"] == 3
            assert plan["source"]["parent_call"]["iteration"] == 2
            if not zh:
                denied = await viewer.request.post(
                    self.origin + API + f"{source_path}/debug_forks",
                    data={
                        **selection,
                        "preview_token": plan["preview_token"],
                        "request_id": "unauthorized-fork",
                    },
                )
                assert denied.status in {403, 404}, await denied.text()
            await expect(scope).to_have_value(second["run_id"])
            await expect(
                preview.get_by_test_id("debug-fork-scope-select")
            ).to_have_value("single")
            await expect(preview).to_contain_text("Check citations")
            await expect(preview).to_contain_text("v3")
            await expect(preview).to_contain_text(attempt)
            assert not self.deliveries

            if zh:
                await preview.get_by_test_id("debug-fork-use-draft").click()
                await expect(
                    preview.get_by_test_id("debug-fork-draft-details")
                ).to_contain_text(f"r{revision}")
                await expect(preview).to_contain_text(
                    "Check citations with updated policy"
                )
                expected_pdf = bad_pdf
            else:
                await replace_pdf(page, preview, good_pdf)
                expected_pdf = good_pdf
            await expect(
                preview.get_by_role("row", name=re.compile(r"^draft\b"))
            ).to_contain_text(json.dumps(draft))
            await expect(preview.get_by_test_id("start-debug-fork")).to_be_enabled()
            await preview.locator(".v-card-text").evaluate(
                "(element) => element.scrollTop = 0"
            )
            await page.screenshot(
                path=self.evidence / f"report-debug-preview-{locale}.png",
                animations="disabled",
            )
            fork_id = await start_fork(page, root, preview)
            fork = await self.wait_run(
                fork_id, lambda run: run["status"] == "completed"
            )
            assert fork_id != root
            assert [task["node_id"] for task in fork["tasks"]] == ["check"]
            assert not fork["todos"] and not self.deliveries
            assert fork["result"]["pdf"] == expected_pdf
            assert fork["result"]["draft"] == draft
            history = fork["snapshot"]["debug_fork"]
            assert history["source"]["attempt_id"] == attempt
            assert history["original"]["status"] == "failed"
            if zh:
                assert history["definition"]["draft_revision"] == revision
            await page.reload()
            original_result = comparison.locator(".fork-result").filter(
                has=page.get_by_role(
                    "heading", name="原结果" if zh else "Original result"
                )
            )
            await expect(original_result).to_contain_text(
                "Transform result does not match its contract"
            )
            fork_result = comparison.locator(".fork-result").filter(
                has=page.get_by_role(
                    "heading", name="分支结果" if zh else "Fork result"
                )
            )
            await expect(fork_result.locator("pre")).to_contain_text(
                expected_pdf["file_id"]
            )
            await expect(fork_result.locator("pre")).to_contain_text(json.dumps(draft))
            await comparison.get_by_test_id("debug-fork-parent").click()
            await expect(scope).to_have_value(root)
            await expect(round_picker).to_have_value("citation-rounds:2")
            await expect(page.get_by_test_id("failed-child-call")).to_contain_text(
                "Review report citations"
            )
            await page.go_back()
            await comparison.get_by_test_id("debug-fork-source").click()
            await expect(scope).to_have_value(second["run_id"])
            await expect(
                inspector.get_by_test_id("inspection-context")
            ).to_contain_text(attempt)
            await inspect_edge(page, "data:input:check:pdf")
            await expect(inspector.get_by_test_id("inspection-value")).to_contain_text(
                bad_pdf["file_id"]
            )

        for page, simulated in ((bot, False), (chat, True)):
            await self.exercise_delivery(
                page, viewer, root, good_pdf, old_approval, draft, simulated
            )
        await viewer.close()
        persisted = await self.api("GET", source_path)
        for key in ("status", "cursor", "tasks", "todos", "scopes", "loops"):
            assert persisted[key] == historical[key], key
        print(
            f"Verified report {root}: v3 rounds, exact inputs, draft, and both delivery branches."
        )

    async def exercise_delivery(
        self, page, viewer, root, good_pdf, old_approval, draft, simulated
    ):
        """Approve the new report and prove the selected write path in the ledger.

        Args:
            page: Owner page showing the original second-round data line.
            viewer: Independent unauthorized Dashboard context.
            root: Source root run shared by both delivery forks.
            good_pdf: Exact PDF replacement that satisfies the frozen check.
            old_approval: Approval answered by the original first round.
            draft: Original draft carried unchanged by the debug fork.
            simulated: Whether to use a test receipt for the first write.
        """
        before = list(self.deliveries)
        await page.get_by_test_id("open-debug-fork").click()
        preview = page.get_by_test_id("debug-fork-preview")
        await preview.get_by_test_id("debug-fork-scope-select").select_option(
            "downstream"
        )
        await expect(preview.get_by_test_id("debug-fork-nodes")).to_contain_text(
            "Record receipt for archive"
        )
        await expect(preview).to_contain_text("v3")
        await replace_pdf(page, preview, good_pdf)
        fork_id = await start_fork(page, root, preview)
        todo = await self.todo(fork_id)
        assert todo["todo_id"] != old_approval["todo_id"]
        assert todo["artifact_ref"] != old_approval["artifact_ref"]
        waiting = await self.api("GET", f"/workflow_runs/{fork_id}")
        tasks = {task["node_id"]: task for task in waiting["tasks"]}
        assert waiting["snapshot"]["debug_fork"]["source"]["asset_version"] == 3
        assert waiting["snapshot"]["debug_fork"]["scope"] == "downstream"
        assert tasks["approve"]["state"].get("debug_write_preview") is None
        assert todo["artifact_ref"] == tasks["check"]["result_ref"]
        assert tasks["check"]["result"]["pdf"] == good_pdf
        assert self.deliveries == before

        answer_url = (
            f"{self.origin}{API}/workflow_runs/{fork_id}/todos/{todo['todo_id']}/answer"
        )
        stale = await self.context.request.post(
            answer_url,
            data={
                "request_id": "wrong-original-approval",
                "action": "approve",
                "artifact_ref": old_approval["artifact_ref"],
            },
        )
        assert stale.status == 409, await stale.text()
        await (
            page.get_by_test_id("run-detail")
            .get_by_role(
                "button",
                name="运行工作台" if simulated else "Run workspace",
                exact=True,
            )
            .click()
        )
        await self.review(page, todo)
        stopped = await self.wait_run(fork_id, lambda run: run["status"] == "blocked")
        deliver = node(stopped, "deliver")
        write = deliver["state"]["debug_write_preview"]
        assert write["target"] == DELIVERY_TARGET
        assert write["parameters"]["record"] == {"count": 77}
        assert self.deliveries == before
        write_url = f"{self.origin}{API}/workflow_runs/{deliver['run_id']}/tool_calls/{write['record_id']}"
        for action, payload in (
            ("execute", {}),
            ("simulate", {"result": SIMULATED_RECEIPT}),
        ):
            denied = await viewer.request.post(
                f"{write_url}/{action}",
                data={**payload, "preview_token": write["preview_token"]},
            )
            assert denied.status in {403, 404}, await denied.text()
        assert self.deliveries == before

        await page.get_by_test_id("open-debug-write-preview").click()
        dialog = page.get_by_test_id("debug-write-preview")
        await expect(dialog).to_contain_text(DELIVERY_TARGET)
        await expect(dialog).to_contain_text('"count": 77')
        if simulated:
            sim_button = dialog.get_by_test_id("simulate-debug-write")
            await sim_button.click()
            value = (
                dialog.get_by_test_id("debug-write-test-value")
                .locator("textarea")
                .first
            )
            receipt_json = json.dumps(SIMULATED_RECEIPT, indent=2)
            await value.fill(receipt_json)
            await value.press("Tab")
            await expect(value).to_have_value(receipt_json)
            await page.screenshot(
                path=self.evidence / "report-debug-delivery-zh.png",
                animations="disabled",
            )
            async with page.expect_response(f"{write_url}/simulate") as response:
                await sim_button.click()
        else:
            await page.screenshot(
                path=self.evidence / "report-debug-delivery-en.png",
                animations="disabled",
            )
            async with page.expect_response(f"{write_url}/execute") as response:
                await dialog.get_by_test_id("confirm-debug-write").click()
        decision = await response.value
        assert decision.ok, await decision.text()
        saved_decision = (await decision.json())["data"]
        assert saved_decision["run_id"] == deliver["run_id"]
        if simulated:
            for _ in range(200):
                outcome = await self.api("GET", f"/workflow_runs/{fork_id}")
                later = node(outcome, "archive-write")
                if later["state"].get("debug_write_preview"):
                    break
                await asyncio.sleep(0.1)
            assert outcome["status"] == "blocked" and later["status"] == "blocked"
            pause = later["state"]["debug_write_preview"]
            assert pause["target"] == ARCHIVE_TARGET
            assert pause["record_id"] != write["record_id"]
            assert pause["parameters"]["record"] == SIMULATED_RECEIPT["record"]
            assert not pause.get("authorized_by")
            expected_receipt = SIMULATED_RECEIPT
            assert self.deliveries == before
        else:
            outcome = await self.wait_run(
                fork_id, lambda run: run["status"] == "completed"
            )
            expected_receipt = {"record": {"count": 78}}
            assert node(outcome, "archive-write")["status"] == "skipped"
            assert self.deliveries == [
                *before,
                {
                    "identity": write["record_id"],
                    "binding": deliver["snapshot"]["definition"]["knowledge_base_id"],
                    "record": {"count": 77},
                    "target": DELIVERY_TARGET,
                },
            ]
        delivery = node(outcome, "deliver")
        archived = node(outcome, "archive-record")
        call = next(
            call
            for call in delivery["tool_calls"]
            if call["record_id"] == write["record_id"]
        )
        assert call["status"] == "applied"
        assert bool(call["started_execution_id"]) != simulated
        assert archived["result"] == expected_receipt
        line_query = {
            "scope_run_id": fork_id,
            "task_run_id": archived["run_id"],
            "attempt_id": archived["state"]["attempt_id"],
            "input_name": "record",
        }
        consumed = await self.api(
            "GET", f"/workflow_runs/{fork_id}/data_line", params=line_query
        )
        assert consumed["read_status"] == "read"
        assert consumed["value"] == expected_receipt["record"]
        provenance = (
            [
                {
                    "record_id": write["record_id"],
                    "task_run_id": delivery["run_id"],
                    "attempt_id": delivery["state"]["attempt_id"],
                }
            ]
            if simulated
            else []
        )
        assert consumed["evidence"].get("simulated_sources", []) == provenance
        assert archived["state"].get("simulated_sources", []) == provenance

        reopened = await page.context.new_page()
        await reopened.goto(self.origin + run_route(fork_id, not simulated))
        comparison = reopened.get_by_test_id("debug-fork-comparison")
        await expect(comparison).to_contain_text(json.dumps(draft))
        writes = comparison.get_by_test_id("debug-write-status")
        await expect(writes).to_have_count(2 if simulated else 1)
        receipt_status = "已记录模拟回执" if simulated else "Connector receipt recorded"
        await (
            writes.filter(has_text=receipt_status)
            .get_by_test_id("open-debug-write-preview")
            .click()
        )
        saved = reopened.get_by_test_id("debug-write-preview")
        await expect(saved).to_contain_text(DELIVERY_TARGET)
        proof = saved.get_by_test_id(
            "debug-write-simulated" if simulated else "debug-write-current"
        )
        await expect(proof).to_contain_text(str(expected_receipt["record"]["count"]))
        if simulated:
            await expect(saved.get_by_test_id("debug-write-current")).to_have_count(0)
            await saved.get_by_role("button", name="关闭", exact=True).click()
            await (
                writes.filter(has_text="已在外部写入前暂停")
                .get_by_test_id("open-debug-write-preview")
                .click()
            )
            await expect(saved).to_contain_text(ARCHIVE_TARGET)
            await expect(saved).to_contain_text('"count": 192')
            await expect(saved.get_by_test_id("confirm-debug-write")).to_be_enabled()
            await saved.get_by_role("button", name="关闭", exact=True).click()
            workspace = reopened.get_by_test_id("run-detail")
            await workspace.get_by_role(
                "button", name=re.compile("^Deliver approved report")
            ).click()
            timeline = workspace.get_by_test_id("durable-tool-call").filter(
                has_text=write["record_id"]
            )
            await expect(timeline).to_contain_text(receipt_status)
            await workspace.get_by_role(
                "button", name=re.compile("^Record receipt for archive")
            ).click()
            await expect(workspace.get_by_test_id("result-text")).to_contain_text(
                '"count": 192'
            )
            await expect(
                workspace.get_by_test_id("workflow-simulated-sources")
            ).to_be_visible()
            await workspace.get_by_role(
                "button", name="展开执行图（只读）", exact=True
            ).click()
            await inspect_edge(reopened, "data:input:archive-record:record")
            inspector = workspace.get_by_test_id("workflow-data-line")
            await expect(inspector.get_by_test_id("inspection-value")).to_contain_text(
                '"count": 192'
            )
            sources = inspector.get_by_test_id("workflow-simulated-sources")
            await sources.locator("summary").click()
            await expect(sources.locator("li")).to_contain_text(write["record_id"])
        else:
            await expect(proof).to_contain_text(call["started_execution_id"])
            await expect(saved.get_by_test_id("confirm-debug-write")).to_have_count(0)
        await reopened.close()


def node(scope, node_id):
    """Find a task in the selected scope.

    Args:
        scope: Saved run or fixed child scope from the Dashboard API.
        node_id: Stable node ID within that scope.

    Returns:
        The task observation for that node.
    """
    return next(task for task in scope["tasks"] if task["node_id"] == node_id)


def pdf_path(ref):
    """Build the download path for one exact PDF attachment.

    Args:
        ref: Artifact identity, version and file identity.

    Returns:
        The API path for the exact attachment.
    """
    return f"/workflow_artifacts/{ref['artifact_id']}/versions/{ref['version']}/files/{ref['file_id']}"


def run_route(run_id, chat):
    """Build a Dashboard entry point for a root run.

    Args:
        run_id: Root run to reopen.
        chat: Whether to use its Chat run conversation.

    Returns:
        The Bot or Chat hash route.
    """
    return f"/#/chat/runs/{run_id}" if chat else f"/#/agent-teams?workflow_run={run_id}"


async def inspect_edge(page, edge_id):
    """Activate a named data edge through the execution canvas.

    Args:
        page: Page displaying the run graph.
        edge_id: Stable data edge ID for the consumer port.
    """
    edge = page.locator(f'.vue-flow__edge.inspection-edge[data-id="{edge_id}"]')
    await edge.focus()
    await expect(edge).to_be_focused()
    await page.keyboard.press("Enter")


async def replace_pdf(page, preview, ref):
    """Choose an authorized exact PDF in the attachment picker.

    Args:
        page: Page hosting the preview and picker options.
        preview: Visible debug fork preview card.
        ref: Exact citation attachment to use as the replacement.
    """
    await (
        preview.get_by_test_id("debug-fork-input-picker")
        .get_by_role("combobox")
        .click()
    )
    await page.get_by_role("option", name="pdf", exact=True).click()
    await (
        preview.get_by_test_id("debug-fork-artifact-id")
        .locator("input")
        .fill(ref["artifact_id"])
    )
    await (
        preview.get_by_test_id("debug-fork-artifact-version")
        .locator("input")
        .fill(str(ref["version"]))
    )
    await (
        preview.get_by_test_id("debug-fork-attachment").get_by_role("combobox").click()
    )
    attachment = page.get_by_role(
        "option", name=f"citations.pdf · {ref['file_id']}", exact=True
    )
    await attachment.click()
    await expect(attachment).to_be_hidden()
    await expect(
        preview.get_by_role("row", name=re.compile(r"^pdf\b")).get_by_test_id(
            "debug-fork-changed-value"
        )
    ).to_contain_text(ref["file_id"])


async def start_fork(page, root, preview):
    """Confirm the visible debug fork plan.

    Args:
        page: Page hosting the preview.
        root: Historical root run ID.
        preview: Visible debug fork preview card.

    Returns:
        The independently created fork root ID.
    """
    start = preview.get_by_test_id("start-debug-fork")
    await expect(start).to_be_enabled()
    async with page.expect_response(
        lambda response: (
            response.url.endswith(f"/{root}/debug_forks")
            and response.request.method == "POST"
        )
    ) as response:
        await start.click()
    launched = await response.value
    assert launched.ok, await launched.text()
    return (await launched.json())["data"]["run_id"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard", type=Path, default=ROOT / "dashboard/dist")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--chromium")
    args = SimpleNamespace(**vars(parser.parse_args()), llama=None, weights=None)
    asyncio.run(ReportDebugAcceptance(args).run())
