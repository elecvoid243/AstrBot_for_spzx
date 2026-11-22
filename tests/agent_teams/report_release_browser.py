"""Exercise the multi-document report in an isolated production Dashboard."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from aiohttp import ClientError, ClientSession, web
from playwright.async_api import async_playwright, expect

from tests.agent_teams.report_release_fixture import (
    FACTS,
    FEEDBACK,
    MODEL,
    REPORT,
    REPORT_FIELDS,
    SAMPLES,
    TEXT,
    contract,
    definitions,
)

ROOT = Path(__file__).resolve().parents[2]
API = "/api/v1"
SOURCE_MODEL = "source-preparation"


def available_port():
    """Allocate a loopback port for an acceptance process."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class Acceptance:
    """Own the disposable server, model, browser and run evidence."""

    def __init__(self, args):
        self.args = args
        self.evidence = args.evidence.resolve()
        self.evidence.mkdir(parents=True)
        self.runtime = self.evidence / "astrbot"
        (self.runtime / "data").mkdir(parents=True)
        self.origin = f"http://127.0.0.1:{available_port()}"
        self.password = "Report-" + secrets.token_urlsafe(18)
        self.requests = []
        self.extracted = []
        self.extract_started = set()
        self.extract_gate = asyncio.Event()
        self.processes = []

    async def start_process(self, command, url, name, env=None):
        """Start an owned process and wait for its HTTP readiness endpoint.

        Args:
            command: Executable and arguments for the owned child.
            url: Local HTTP readiness endpoint.
            name: Prefix for this process's log.
            env: Optional isolation environment.

        Returns:
            The ready subprocess.

        Raises:
            RuntimeError: The child exited or its readiness deadline expired.
        """
        with (self.evidence / f"{name}.log").open("ab") as log:
            process = await asyncio.to_thread(
                subprocess.Popen,
                command,
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        self.processes.append(process)
        for _ in range(300):
            if process.poll() is not None:
                break
            try:
                async with self.client.get(url) as response:
                    if response.status == 200:
                        return process
            except ClientError:
                pass
            await asyncio.sleep(0.2)
        raise RuntimeError(
            f"{name} failed to start. See {self.evidence / (name + '.log')}"
        )

    async def start_dashboard(self):
        """Serve the built Dashboard against the isolated root."""
        env = {
            **os.environ,
            "ASTRBOT_ROOT": str(self.runtime),
            "ASTRBOT_DISABLE_METRICS": "1",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "127.0.0.1,localhost",
        }
        return await self.start_process(
            [
                sys.executable,
                str(ROOT / "main.py"),
                "--webui-dir",
                str(self.args.dashboard.resolve()),
            ],
            self.origin + "/api/v1/auth/setup-status",
            "dashboard",
            env,
        )

    async def controlled_completion(self, request):
        """Respond through the normal OpenAI tool protocol and reverse branch order."""
        payload = await request.json()
        messages = payload["messages"]
        prompt = json.dumps(messages, ensure_ascii=False)
        self.requests.append(payload)
        finished = any(
            message["role"] == "tool"
            and message["content"].startswith("Result accepted.")
            for message in messages
        )
        tool_name = "submit_task_result"
        if finished:
            value = {}
        elif match := re.search(r"ATN180 EXTRACT ([AB])", prompt):
            key = match[1].lower()
            self.extract_started.add(key)
            if self.extract_started == set(FACTS):
                self.extract_gate.set()
            await asyncio.wait_for(self.extract_gate.wait(), 10)
            if key == "a":
                await asyncio.sleep(1.8)
            value = FACTS[key]
            self.extracted.append(key)
        elif "ATN180 REPORT" in prompt:
            revised = FEEDBACK in prompt
            headings = "Survey results" if revised else "Survey"
            target = "Planning target" if revised else "Target"
            text = (
                f"DOC-A | {headings} | 37 households\nDOC-B | {target} | 40 households"
            )
            if revised:
                text += (
                    "\nSource index: DOC-A / Survey results; DOC-B / Planning target"
                )
            stored = next((m for m in reversed(messages) if m["role"] == "tool"), None)
            if stored:
                value = {
                    "text": text,
                    "sources": ["DOC-A", "DOC-B"],
                    "files": [{"file_id": json.loads(stored["content"])["file_id"]}],
                }
            else:
                tool_name = "store_task_file"
                value = {"filename": "report.md", "content": text}
        elif "ATN180 SOURCE" in prompt:
            value = {"text": "Fixed input documents", "files": []}
        else:
            raise AssertionError(f"Unexpected task: {prompt}")
        arguments = (
            value
            if tool_name == "store_task_file"
            else {"result_json": json.dumps(value)}
        )
        completion = {
            "id": f"fixture-{len(self.requests)}",
            "object": "chat.completion",
            "model": MODEL,
            "created": 1,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_" + secrets.token_hex(6),
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": json.dumps(arguments),
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
        if finished:
            completion["choices"][0] = {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Done."},
            }
        if not payload.get("stream"):
            return web.json_response(completion)
        choice = completion["choices"][0]
        message = choice["message"]
        delta = {"role": "assistant", "content": message["content"]}
        if calls := message.get("tool_calls"):
            delta["tool_calls"] = [{"index": 0, **calls[0]}]
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for delta, finish in ((delta, None), ({}, choice["finish_reason"])):
            chunk = {
                **completion,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    async def api(self, method, path, *, client=None, **kwargs):
        """Issue an authenticated Dashboard request and expose validation failures."""
        response = await (client or self.context.request).fetch(
            self.origin + API + path, method=method, **kwargs
        )
        assert response.ok, (method, path, response.status, await response.text())
        body = await response.json()
        assert body["status"] == "ok", body
        return body.get("data")

    async def open_draft(self, definition):
        """Create a draft through the same public endpoint as the editor."""
        return await self.api(
            "POST", "/agent_drafts/workflow", data={"body": {"definition": definition}}
        )

    async def publish(self, draft):
        """Publish the explicitly selected draft revision."""
        return await self.api(
            "POST",
            f"/agent_drafts/workflow/{draft['asset_id']}/publish",
            data={"revision": draft["revision"]},
        )

    async def wait_run(self, run_id, predicate):
        """Wait for a durable, user-visible state and report terminal failures.

        Args:
            run_id: Public identity of the original root.
            predicate: Condition identifying the desired saved state.

        Returns:
            The matching Dashboard snapshot.

        Raises:
            AssertionError: The run ended early or did not reach the target state.
        """
        for _ in range(480):
            run = await self.api("GET", f"/workflow_runs/{run_id}")
            if predicate(run):
                return run
            assert run["status"] not in {
                "failed",
                "blocked",
                "cancelled",
                "completed",
            }, (
                run["status"],
                run["state"],
                [
                    (t["node_id"], t["status"], t["state"].get("reason"))
                    for t in run["tasks"]
                ],
                [
                    (s["run_id"], s["state"].get("reason"))
                    for s in run.get("scopes", [])
                ],
            )
            await asyncio.sleep(0.25)
        raise AssertionError(f"Timed out waiting for {run_id}")

    async def source_inputs(self):
        """Attach the original fixture bytes to a durable source artifact."""
        fields = contract({"text": TEXT, "files": REPORT_FIELDS["files"]})
        draft = await self.open_draft(
            {
                "name": "Fixed report inputs",
                "default_provider_id": SOURCE_MODEL,
                "result_task_id": "source",
                "tasks": [
                    {
                        "id": "source",
                        "name": "Document source",
                        "member": "source",
                        "role": {
                            "name": "Prepare the source artifact",
                            "prompt": "Call the requested tools. When submit_task_result returns Result accepted, reply Done.",
                            "tools": [],
                            "skills": [],
                        },
                        "installation": {"profile_id": "default"},
                        "task": "ATN180 SOURCE. Call submit_task_result with text set to Fixed input documents and files set to an empty array.",
                        "output_contract": fields,
                    }
                ],
            }
        )
        await self.publish(draft)
        start = await self.api(
            "POST",
            f"/workflow_assets/{draft['asset_id']}/runs",
            data={
                "version": 1,
            },
        )
        source = await self.wait_run(
            start["run_id"], lambda r: r["status"] == "completed"
        )
        assert source["snapshot"]["asset_version"] == 1
        assert source["snapshot"].get("port_contract_version") is None
        task = source["tasks"][0]
        ref = task["result_ref"]
        files = {}
        for key in FACTS:
            file = SAMPLES / f"DOC-{key.upper()}.md"
            files[key] = await self.api(
                "POST",
                f"/workflow_runs/{task['run_id']}/files",
                params={"filename": file.name},
                data=file.read_bytes(),
            )
        revision = await self.api(
            "POST",
            f"/workflow_artifacts/{ref['artifact_id']}/versions",
            data={
                "expected_version": ref["version"],
                "request_id": "fixture-sources",
                "content": {
                    "text": "Fixed input documents",
                    "files": [{"file_id": file["file_id"]} for file in files.values()],
                },
            },
        )
        return {
            f"document_{key}": {
                "artifact_id": ref["artifact_id"],
                "version": revision["version"],
                "file_id": file["file_id"],
            }
            for key, file in files.items()
        }

    async def login(self, context, username, password, locale):
        """Sign in through the real Dashboard and select the tutorial language."""
        page = await context.new_page()
        await page.add_init_script(
            f"localStorage.setItem('astrbot-locale', {json.dumps(locale)}); localStorage.setItem('astrbot:first_notice_seen:v1', '1')"
        )
        await page.goto(self.origin + "/#/auth/login")
        await page.locator('input[name="username"]').fill(username)
        await page.locator('input[name="password"]').fill(password)
        async with page.expect_response(
            lambda response: response.url.endswith("/auth/login")
        ) as login:
            await page.locator('button[type="submit"]').click()
        signed_in = await login.value
        assert signed_in.ok, signed_in.status
        assert (await signed_in.json())["status"] == "ok"
        await page.wait_for_function("!!localStorage.getItem('token')")
        if username == "report-owner":
            await page.wait_for_function("!location.hash.includes('/auth/')")
        token = await page.evaluate("localStorage.getItem('token')")
        await context.set_extra_http_headers({"Authorization": f"Bearer {token}"})
        return page

    async def observed_events(self, run_id):
        """Read every event committed for the same root using public cursors."""
        history = []
        cursor = 1
        while True:
            batch = await self.api(
                "GET", f"/workflow_runs/{run_id}/events", params={"after": str(cursor)}
            )
            assert not batch["reset"], batch
            if not batch["events"]:
                return history
            assert batch["cursor"] > cursor
            history.extend(batch["events"])
            cursor = batch["cursor"]

    async def todo(self, run_id, after=None):
        """Return the single open approval from the original root."""
        state = await self.wait_run(
            run_id,
            lambda r: any(
                t["status"] == "pending" and t["todo_id"] != after for t in r["todos"]
            ),
        )
        pending = [
            t
            for t in state["todos"]
            if t["status"] == "pending" and t["todo_id"] != after
        ]
        assert len(pending) == 1, pending
        return pending[0]

    async def review(self, page, todo, reject=False):
        """Preview and decide the exact report in the workbench or Chat."""
        card = page.locator(f'[data-todo-id="{todo["todo_id"]}"]')
        if await page.locator(".workbench-panel-nav").is_visible():
            await page.locator(".workbench-panel-nav button").nth(2).click()
        await card.get_by_test_id("human-preview").click()
        await expect(card.locator("pre")).to_contain_text("DOC-A")
        if reject:
            await (
                card.get_by_test_id("human-feedback").locator("textarea").fill(FEEDBACK)
            )
        async with page.expect_response(
            lambda response: response.url.endswith("/answer")
        ) as deciding:
            await card.get_by_test_id(
                "human-reject" if reject else "human-submit"
            ).click()
        decision = await deciding.value
        assert decision.ok, await decision.text()

    async def report_bytes(self, todo):
        """Return the exact review download and avoid mutable latest references."""
        ref = todo["artifact_ref"]
        artifact = f"/workflow_artifacts/{ref['artifact_id']}/versions/{ref['version']}"
        obj = await self.api("GET", artifact)
        assert obj["content"]["sources"] == ["DOC-A", "DOC-B"], obj
        assert len(obj["files"]) == 1, obj
        file = obj["files"][0]
        assert file["filename"] == "report.md", file
        response = await self.context.request.get(
            self.origin + API + artifact + f"/files/{file['file_id']}"
        )
        assert response.ok, await response.text()
        content = await response.body()
        text = content.decode()
        for expected in FACTS.values():
            assert all(str(item) in text for item in expected.values()), text
        return content, artifact

    async def browser_download(self, page, todo):
        """Read the bytes downloaded by an operator from the reviewed version."""
        if await page.locator(".workbench-panel-nav").is_visible():
            await page.locator(".workbench-panel-nav button").nth(2).click()
        card = page.locator(f'[data-todo-id="{todo["todo_id"]}"]')
        await expect(card.get_by_test_id("human-export")).to_be_visible()
        button = card.get_by_role(
            "button", name=re.compile("^(Download file|下载文件).*report.md")
        )
        if not await button.count():
            await card.get_by_test_id("human-preview").click()
        async with page.expect_download() as result:
            await button.click()
        download = await result.value
        assert download.suggested_filename == "report.md"
        return (await download.path()).read_bytes()

    async def check_access(self, browser, run_id, todo):
        """Prove that a logged-in non-owner cannot read or decide the owner's run.

        Args:
            browser: Chromium instance supporting an independent login.
            run_id: The owner's active root.
            todo: Exact pending decision to protect.

        Returns:
            The unauthorized account's browser context for the later download check.
        """
        await self.api(
            "POST",
            "/accounts",
            data={"username": "report-viewer", "password": self.password},
        )
        viewer = await browser.new_context()
        page = await self.login(viewer, "report-viewer", self.password, "en-US")
        await page.goto(self.origin + f"/#/chat/runs/{run_id}")
        await expect(page.locator(".run-summary")).to_have_count(0)
        ref = todo["artifact_ref"]
        artifact = f"/workflow_artifacts/{ref['artifact_id']}/versions/{ref['version']}"
        for path in (
            f"/chat/run-conversations/{run_id}",
            f"/workflow_runs/{run_id}",
            f"/workflow_runs/{run_id}/events",
            f"/workflow_runs/{run_id}/events/stream",
            artifact,
        ):
            response = await viewer.request.get(self.origin + API + path)
            assert response.status in {403, 404}, (path, response.status)
        response = await viewer.request.post(
            self.origin
            + API
            + f"/workflow_runs/{run_id}/todos/{todo['todo_id']}/answer",
            data={
                "action": "approve",
                "request_id": "other-account",
                "artifact_ref": ref,
            },
        )
        assert response.status in {403, 404}, response.status
        return viewer

    async def import_package(self, package):
        """Install the native export with deliberate resource bindings for every dependency.

        Args:
            package: The exported definition and its fixed dependency closure.

        Returns:
            The newly installed workflow asset envelope.
        """
        payload = {"package": package}
        preview = await self.api(
            "POST", "/workflow_assets/import/preview", data=payload
        )
        payload["selections"] = [
            {
                "kind": requirement["kind"],
                "source_id": requirement["source_id"],
                "target_id": next(
                    c["id"] for c in requirement["candidates"] if not c.get("disabled")
                ),
            }
            for requirement in preview["requirements"]
        ]
        ready = await self.api("POST", "/workflow_assets/import/preview", data=payload)
        assert ready["ready"] is True, ready
        return await self.api("POST", "/workflow_assets/import", data=payload)

    async def exercise(self, browser):
        """Run the controlled or real-model report and verify its approved attachment."""
        self.context = await browser.new_context(
            viewport={"width": 1512, "height": 1000}, accept_downloads=True
        )
        page = await self.login(self.context, "report-owner", self.password, "en-US")
        inputs = await self.source_inputs()
        child, _ = definitions({})
        child["default_provider_id"] = MODEL
        child_draft = await self.open_draft(child)
        await self.publish(child_draft)
        _, graph = definitions({"asset_id": child_draft["asset_id"], "version": 1})
        graph["default_provider_id"] = MODEL
        draft = await self.open_draft(graph)
        base = f"/workflow_assets/{draft['asset_id']}"
        page.set_default_timeout(15000)
        await page.goto(self.origin + f"/#/agent-teams/workflows/{draft['asset_id']}")
        await expect(
            page.get_by_text("Collect results in source order", exact=True).first
        ).to_be_visible()
        await page.screenshot(path=self.evidence / "canvas-en.png")
        await page.get_by_role("button", name="Test draft", exact=True).click()
        await page.get_by_test_id("trial-scope").click()
        await page.get_by_role("option", name="Selected node only").click()
        await page.get_by_test_id("trial-start-node").click()
        await page.get_by_role("option", name="Read DOC-A").click()
        form = page.get_by_test_id("structured-input-values").locator("textarea").first
        await form.fill(json.dumps({"document_a": inputs["document_a"]}))
        await expect(page.get_by_role("option", name="Read DOC-A")).to_be_hidden()
        await expect(page.get_by_test_id("reference-file-document_a")).to_contain_text(
            "DOC-A.md"
        )
        await page.screenshot(path=self.evidence / "trial-en.png")
        async with page.expect_response(
            lambda response: response.url.endswith("/trials")
        ) as starting:
            await page.get_by_role("button", name="Start test").click()
        response = await starting.value
        assert response.ok, await response.text()
        trial = (await response.json())["data"]
        trial = await self.wait_run(
            trial["run_id"], lambda r: r["status"] == "completed"
        )
        assert "37 households" in trial["result"]["text"], trial["result"]
        await page.goto(self.origin + f"/#/agent-teams/workflows/{draft['asset_id']}")
        async with page.expect_response(
            lambda response: response.url.endswith("/publish")
        ) as publishing:
            await page.get_by_test_id("publish-workflow").click()
        published = await publishing.value
        assert published.ok, await published.text()
        package = await self.api("GET", base + "/export")
        (self.evidence / "multi-document-report.json").write_text(
            json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        assert "fixture-placeholder" not in json.dumps(package)
        await page.goto(self.origin + "/#/agent-teams")
        await page.get_by_role("tab", name="Workflows", exact=True).click()
        asset_card = page.locator(".at-asset-card").filter(has_text=REPORT)
        await asset_card.get_by_role("button", name="Start Run").click()
        dialog = page.get_by_role("dialog")
        await (
            dialog.get_by_test_id("structured-input-values")
            .locator("textarea")
            .first.fill(json.dumps(inputs))
        )
        async with page.expect_response(
            lambda response: response.url.endswith(base + "/runs")
        ) as launching:
            await dialog.get_by_role("button", name="Start Run").click()
        launched = await launching.value
        assert launched.ok, await launched.text()
        start = (await launched.json())["data"]
        run_id = start["run_id"]
        workbench = self.origin + f"/#/agent-teams?workflow_run={run_id}"
        await page.goto(workbench)
        first = await self.todo(run_id)
        first_ref = first["artifact_ref"]
        old_path = f"/workflow_artifacts/{first_ref['artifact_id']}/versions/{first_ref['version']}"
        old = await self.api("GET", old_path)
        file_id = old["files"][0]["file_id"]
        preview = await self.context.request.get(
            self.origin + API + old_path + f"/files/{file_id}",
            params={"download": "false"},
        )
        assert preview.ok, await preview.text()
        assert "source index" not in (await preview.text()).casefold()
        await self.review(page, first, reject=True)
        second = await self.todo(run_id, after=first["todo_id"])
        assert second["todo_id"] != first["todo_id"], second
        assert second["artifact_ref"] != first["artifact_ref"], second
        await page.get_by_text("Open in Chat", exact=True).click()
        await page.wait_for_url(f"**/chat/runs/{run_id}")
        assert run_id in await page.locator(".run-summary").inner_text()
        if not self.args.llama:
            assert self.extracted == ["b", "a"], self.extracted
            viewer = await self.check_access(browser, run_id, second)
            paused = page.get_by_test_id("continue-run")
            await page.get_by_test_id("pause-run").click()
            await expect(paused).to_be_visible()
            count = len(self.requests)
            await self.context.set_offline(True)
            token = await page.evaluate("localStorage.getItem('token')")
            async with self.client.post(
                self.origin + API + f"/workflow_runs/{run_id}/control",
                headers={"Authorization": f"Bearer {token}"},
                json={"action": "continue", "request_id": "continue-during-disconnect"},
            ) as control:
                assert control.ok, await control.text()
            await self.context.set_offline(False)
            await expect(page.get_by_test_id("pause-run")).to_be_visible(timeout=15000)
            await page.get_by_test_id("pause-run").click()
            await expect(paused).to_be_visible()
            before = await self.observed_events(run_id)
            await expect(
                page.locator(f'[data-todo-id="{second["todo_id"]}"]')
            ).to_be_visible()
            self.dashboard.terminate()
            await asyncio.to_thread(self.dashboard.wait, 15)
            await page.close()
            self.dashboard = await self.start_dashboard()
            page = await self.context.new_page()
            await page.goto(self.origin + "/#/chat")
            await (
                page.locator(".session-item")
                .filter(has=page.locator(f'[title="{run_id}"]'))
                .click()
            )
            await page.wait_for_url(f"**/chat/runs/{run_id}")
            assert len(self.requests) == count, "Restart replayed model work"
            await page.get_by_test_id("continue-run").click()
            await expect(page.get_by_test_id("pause-run")).to_be_visible()
            restored = await self.todo(run_id)
            assert len(self.requests) == count, "Continuing replayed model work"
            assert restored["todo_id"] == second["todo_id"]
            assert restored["artifact_ref"] == second["artifact_ref"]
            after = await self.observed_events(run_id)
            assert before and after[: len(before)] == before
        refused = await self.context.request.post(
            self.origin
            + API
            + f"/workflow_runs/{run_id}/todos/{second['todo_id']}/answer",
            data={
                "action": "approve",
                "request_id": "stale-exact-version",
                "artifact_ref": first_ref,
            },
        )
        assert refused.status in {400, 409}, await refused.text()
        await self.review(page, second)
        finished = await self.wait_run(run_id, lambda r: r["status"] == "completed")
        ordered = next(
            t["result"]["results"] for t in finished["tasks"] if t["node_id"] == "join"
        )
        verified_rows = (
            [
                {
                    **row,
                    "result": {
                        **row["result"],
                        "source": row["result"]["source"].removeprefix("# "),
                    },
                }
                for row in ordered
            ]
            if self.args.llama
            else ordered
        )
        assert verified_rows == [
            {"node_id": f"extract_{key}", "result": expected}
            for key, expected in FACTS.items()
        ], ordered
        rounds = finished["state"]["loops"]["review_report"]["iterations"]
        assert [entry["status"] for entry in rounds] == ["rejected", "passed"], rounds
        assert {scope["snapshot"]["asset_version"] for scope in finished["scopes"]} == {
            1
        }
        assert finished["result"]["sources"] == ["DOC-A", "DOC-B"], finished["result"]
        content, artifact = await self.report_bytes(second)
        (self.evidence / "approved-report.md").write_bytes(content)
        attachment = content.decode()
        source_index = re.split(
            "source index", attachment, maxsplit=1, flags=re.IGNORECASE
        )
        assert len(source_index) == 2, attachment
        findings, citations = source_index
        for fact in FACTS.values():
            source, heading, value = fact["source"], fact["heading"], fact["value"]
            assert re.search(
                rf"{source}[^\r\n]*{heading}[^\r\n]*\b{value}\b", findings
            ), attachment
            assert re.search(rf"{source}[^\r\n]*{heading}", citations), attachment
        assert await self.browser_download(page, second) == content
        await page.screenshot(path=self.evidence / "chat-approved-en.png")
        if not self.args.llama:
            assert len(self.requests) == count, (
                "Resuming or approving replayed model work"
            )
            completed_tasks = {
                event["run_id"]: event["cursor"]
                for event in before
                if event["kind"] == "task.completed"
            }
            extraction_cursors = {
                task["node_id"]: completed_tasks[task["run_id"]]
                for task in finished["tasks"]
                if task["node_id"] in {"extract_a", "extract_b"}
            }
            assert extraction_cursors["extract_b"] < extraction_cursors["extract_a"], (
                extraction_cursors
            )
            ref = second["artifact_ref"]
            file_id = finished["result"]["files"][0]["file_id"]
            denied_download = await viewer.request.get(
                self.origin + API + artifact + f"/files/{file_id}"
            )
            assert denied_download.status in {403, 404}, denied_download.status
            await viewer.close()
            bot = await self.context.new_page()
            await bot.goto(workbench)
            assert await self.browser_download(bot, second) == content
            assert ref == next(
                t["artifact_ref"]
                for t in finished["todos"]
                if t["status"] == "approved"
            )
            for surface in (page, bot):
                await (
                    surface.locator(".run-task")
                    .filter(has_text="Extract DOC-A")
                    .first.click()
                )
                await expect(
                    surface.get_by_test_id("model-delivery").first
                ).to_be_visible()
            timeline = await page.get_by_test_id("model-delivery").all_inner_texts()
            assert (
                timeline
                and timeline
                == await bot.get_by_test_id("model-delivery").all_inner_texts()
            )
            await bot.close()
            zh = await browser.new_context(
                viewport={"width": 1512, "height": 1000}, accept_downloads=True
            )
            translated = await self.login(zh, "report-owner", self.password, "zh-CN")
            await translated.goto(
                self.origin + f"/#/agent-teams/workflows/{draft['asset_id']}"
            )
            await expect(
                translated.get_by_text(
                    "Collect results in source order", exact=True
                ).first
            ).to_be_visible()
            await translated.screenshot(path=self.evidence / "canvas-zh.png")
            await translated.goto(self.origin + f"/#/chat/runs/{run_id}")
            assert await self.browser_download(translated, second) == content
            await translated.screenshot(path=self.evidence / "chat-approved-zh.png")
            await zh.close()
            await page.set_viewport_size({"width": 390, "height": 844})
            await page.locator(".workbench-panel-nav button").nth(2).click()
            self.extract_started.clear()
            self.extract_gate.clear()
            await page.get_by_test_id("rerun-workflow").click()
            await page.get_by_test_id("confirm-run-control").click()
            await page.wait_for_function(
                f"location.hash.startsWith('#/chat/runs/') && !location.hash.endsWith('/{run_id}')"
            )
            rerun = page.url.rsplit("/", 1)[1]
            first_narrow = await self.todo(rerun)
            await self.review(page, first_narrow, reject=True)
            second_narrow = await self.todo(rerun, after=first_narrow["todo_id"])
            await self.review(page, second_narrow)
            narrow = await self.wait_run(rerun, lambda r: r["status"] == "completed")
            assert narrow["snapshot"]["asset_version"] == 1
            assert narrow["snapshot"]["inputs"] == inputs
            assert await self.browser_download(page, second_narrow) == content
            await page.screenshot(path=self.evidence / "chat-narrow-en.png")
        if not self.args.llama:
            package = json.loads(
                (SAMPLES / "multi-document-report.json").read_text(encoding="utf-8")
            )
            installed = await self.import_package(package)
            asset = installed["asset"]
            assert (
                asset["definition"]["name"] == REPORT
                and asset["asset_id"] != draft["asset_id"]
            )
            self.extract_started.clear()
            self.extract_gate.clear()
            started = await self.api(
                "POST",
                f"/workflow_assets/{asset['asset_id']}/runs",
                data={"version": asset["version"], "inputs": inputs},
            )
            packaged_run = started["run_id"]
            previous = None
            for action in ("reject", "approve"):
                review = await self.todo(packaged_run, after=previous)
                previous = review["todo_id"]
                payload = {
                    "action": action,
                    "request_id": f"packaged-{action}",
                    "artifact_ref": review["artifact_ref"],
                }
                if action == "reject":
                    payload["feedback"] = FEEDBACK
                await self.api(
                    "POST",
                    f"/workflow_runs/{packaged_run}/todos/{previous}/answer",
                    data=payload,
                )
            await self.wait_run(packaged_run, lambda r: r["status"] == "completed")
            assert (await self.report_bytes(review))[0] == content
            print(f"Installed package completed {packaged_run}", flush=True)
        (self.evidence / "result.json").write_text(
            json.dumps(
                {
                    "root": run_id,
                    "inputs": inputs,
                    "first": first,
                    "second": second,
                    "fact_rows": ordered,
                    "rounds": rounds,
                    "decisions": [t["response"] for t in finished["todos"]],
                    "controlled_restart": (
                        {
                            "event_count_before": len(before),
                            "event_count_after": len(after),
                            "extraction_completion_cursors": extraction_cursors,
                            "model_requests_before_restart": count,
                            "rerun": rerun,
                            "imported_root": packaged_run,
                        }
                        if not self.args.llama
                        else None
                    ),
                    "report_sha256": hashlib.sha256(content).hexdigest(),
                    "approved_artifact": artifact,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"Approved report {run_id} · {self.evidence}", flush=True)

    async def run(self):
        """Start only loopback services and stop their processes on every exit."""
        async with AsyncExitStack() as stack:
            self.client = await stack.enter_async_context(ClientSession())
            try:
                if self.args.llama:
                    plugin = (
                        self.runtime
                        / "data/plugins/astrbot_plugin_report_prompt_fixture"
                    )
                    plugin.mkdir(parents=True)
                    shutil.copyfile(
                        Path(__file__).with_name("report_prompt_fixture.py"),
                        plugin / "main.py",
                    )
                    (plugin / "metadata.yaml").write_text(
                        "name: astrbot_plugin_report_prompt_fixture\nversion: 1.0.0\n"
                        "desc: Scope the local acceptance model prompt.\nauthor: AstrBot\n",
                        encoding="utf-8",
                    )
                    model = "atn180-qwen"
                    endpoint = f"http://127.0.0.1:{available_port()}"
                    await self.start_process(
                        [
                            str(self.args.llama.resolve()),
                            "--host",
                            "127.0.0.1",
                            "--port",
                            endpoint.rsplit(":", 1)[1],
                            "--model",
                            str(self.args.weights.resolve()),
                            "--alias",
                            model,
                            "--ctx-size",
                            "8192",
                            "--threads",
                            "4",
                            "--n-gpu-layers",
                            "0",
                            "--jinja",
                        ],
                        endpoint + "/health",
                        "llama",
                    )
                else:
                    model = MODEL
                port = available_port()
                fixture_endpoint = f"http://127.0.0.1:{port}"
                fixture = web.Application()
                fixture.router.add_post(
                    "/v1/chat/completions", self.controlled_completion
                )
                runner = web.AppRunner(fixture)
                await runner.setup()
                stack.push_async_callback(runner.cleanup)
                await web.TCPSite(runner, "127.0.0.1", port).start()
                if not self.args.llama:
                    endpoint = fixture_endpoint
                salt = secrets.token_hex(16)
                digest = hashlib.pbkdf2_hmac(
                    "sha256", self.password.encode(), bytes.fromhex(salt), 600000
                ).hex()
                (self.runtime / "data/cmd_config.json").write_text(
                    json.dumps(
                        {
                            "dashboard": {
                                "host": "127.0.0.1",
                                "port": int(self.origin.rsplit(":", 1)[1]),
                                "username": "report-owner",
                                "pbkdf2_password": f"pbkdf2_sha256$600000${salt}${digest}",
                                "password_storage_upgraded": True,
                                "password_change_required": False,
                            },
                            "provider": [
                                {
                                    "id": provider_id,
                                    "type": "openai_chat_completion",
                                    "provider_type": "chat_completion",
                                    "enable": True,
                                    "key": ["fixture-placeholder"],
                                    "api_base": base_url + "/v1",
                                    "model": model_name,
                                    "modalities": ["text", "tool_use"],
                                    "timeout": 90,
                                    "custom_extra_body": {
                                        "temperature": 0,
                                        "max_tokens": 384,
                                        **(
                                            {"parallel_tool_calls": False}
                                            if model_name == "atn180-qwen"
                                            else {}
                                        ),
                                    },
                                }
                                for provider_id, base_url, model_name in (
                                    (MODEL, endpoint, model),
                                    (SOURCE_MODEL, fixture_endpoint, MODEL),
                                )
                            ],
                            "provider_settings": {
                                "streaming_response": False,
                                "llm_safety_mode": False,
                            },
                            "agent_runner": {
                                "runner_type": "local",
                                "config": {"model": {"provider_id": MODEL}},
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                self.dashboard = await self.start_dashboard()
                playwright = await stack.enter_async_context(async_playwright())
                browser = await playwright.chromium.launch(
                    executable_path=self.args.chromium, headless=True
                )
                stack.push_async_callback(browser.close)
                await self.exercise(browser)
            finally:
                for process in reversed(self.processes):
                    if process.poll() is None:
                        process.terminate()
                        try:
                            await asyncio.to_thread(process.wait, 15)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            await asyncio.to_thread(process.wait)
                if self.requests:
                    (self.evidence / "controlled-model-requests.json").write_text(
                        json.dumps(self.requests, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard", type=Path, default=ROOT / "dashboard/dist")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--chromium")
    parser.add_argument("--llama", type=Path)
    parser.add_argument("--weights", type=Path)
    args = parser.parse_args()
    if bool(args.llama) != bool(args.weights):
        parser.error("--llama and --weights must be supplied together")
    asyncio.run(Acceptance(args).run())
