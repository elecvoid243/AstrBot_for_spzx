"""Exercise F01 through real login, Agent replies and the installed spcode service."""

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import aiohttp
from aiohttp import web
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    """Validate the production UI against an isolated, real plugin and filesystem.

    Raises:
        AssertionError: A file-reference behavior is incorrect.
        RuntimeError: The isolated host cannot start.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-dir", type=Path, required=True)
    parser.add_argument("--dashboard-dir", type=Path, required=True)
    parser.add_argument("--chromium", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    runtime = evidence / "isolated-host"
    data = runtime / "data"
    installed = data / "plugins/astrbot_plugin_spcode_toolkit"
    shutil.copytree(
        args.plugin_dir.resolve(),
        installed,
        ignore=shutil.ignore_patterns(
            ".git",
            ".worktrees",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            "tests",
            ".scratch",
        ),
    )
    files = evidence / "宿主 外部文件"
    files.mkdir()
    target = files / "源 文件#%.py"
    target.write_text(
        "".join(f"F01 source line {n}\n" for n in range(1, 81)), encoding="utf-8"
    )
    other = files / "second.txt"
    other.write_text("SECOND TARGET\n", encoding="utf-8")
    large = files / "large.txt"
    large.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    href = quote(target.as_posix(), safe="/:")
    links = (
        f"[F01 whole]({href})\n\n[F01 single]({href}#L40)\n\n[F01 range]({href}#L55-L58)\n\n"
        f"[F01 invalid]({href}#L900)\n\n[F01 missing]({quote((files / 'missing.py').as_posix(), safe='/:')})\n\n"
        f"[F01 large]({quote(large.as_posix(), safe='/:')})\n\n[F01 other]({quote(other.as_posix(), safe='/:')})\n\n"
        f"Plain {target.as_posix()}\n\n`[code]({href})`\n\n[web](https://example.com) [relative](src/main.py)\n\nF01_DONE"
    )
    requests = []
    stream_gate = asyncio.Event()

    async def completion(request):
        """Return deterministic replies without replacing the real Agent executor.

        Args:
            request: Local OpenAI-compatible model request.

        Returns:
            A title or an actual streamed Agent reply with host file references.
        """
        body = await request.json()
        requests.append(body)
        prompt = "\n".join(
            str(m.get("content", "")) for m in body["messages"] if m["role"] == "system"
        )
        content = links if body.get("stream") or body.get("tools") else "F01 title"
        if content == links:
            assert "host-absolute path" in prompt and "#L12-L18" in prompt
        payload = {
            "id": "f01",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "f01",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
        }
        if body.get("stream"):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            # The incomplete link must never issue a file request.
            for text in [f"[F01 whole]({href}", links[len(f"[F01 whole]({href}") :]]:
                chunk = {
                    **payload,
                    "object": "chat.completion.chunk",
                    "choices": [
                        {"index": 0, "delta": {"content": text}, "finish_reason": None}
                    ],
                }
                await response.write(
                    f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
                )
                if not stream_gate.is_set():
                    stream_gate.set()
                    await asyncio.sleep(1)
            await response.write(b"data: [DONE]\n\n")
            await response.write_eof()
            return response
        return web.json_response(payload)

    fixture = web.Application()
    fixture.router.add_post("/v1/chat/completions", completion)
    fixture.router.add_get(
        "/v1/models",
        lambda _: web.json_response(
            {"object": "list", "data": [{"id": "f01", "object": "model"}]}
        ),
    )
    runner = web.AppRunner(fixture, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    fixture_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    password = "F01LocalAcceptance9"
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 600000
    ).hex()
    config = {
        "dashboard": {
            "host": "127.0.0.1",
            "port": port,
            "username": "f01",
            "pbkdf2_password": f"pbkdf2_sha256$600000${salt}${digest}",
            "password_storage_upgraded": True,
            "password_change_required": False,
        },
        "provider": [
            {
                "id": "f01",
                "type": "openai_chat_completion",
                "provider_type": "chat_completion",
                "enable": True,
                "key": ["local-fixture"],
                "api_base": fixture_url + "/v1",
                "model": "f01",
            }
        ],
        "agent_runner": {
            "runner_type": "local",
            "config": {"model": {"provider_id": "f01"}},
        },
    }
    (data / "cmd_config.json").write_text(json.dumps(config), encoding="utf-8")
    env = {
        **os.environ,
        "ASTRBOT_ROOT": str(runtime),
        "PYTHONPATH": str(ROOT),
        "PYTHONUTF8": "1",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    process = None
    checks = []
    try:
        with (evidence / "host.log").open("w", encoding="utf-8") as log:
            process = await asyncio.to_thread(
                subprocess.Popen,
                [
                    sys.executable,
                    str(ROOT / "main.py"),
                    "--webui-dir",
                    str(args.dashboard_dir.resolve()),
                ],
                cwd=runtime,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            async with aiohttp.ClientSession() as client:
                for _ in range(150):
                    if process.poll() is not None:
                        raise RuntimeError("Host exited; inspect " + str(evidence))
                    try:
                        async with client.get(
                            origin + "/api/v1/auth/setup"
                        ) as response:
                            if response.status < 500:
                                break
                    except aiohttp.ClientError:
                        pass
                    await asyncio.sleep(1)
                async with async_playwright() as pw:
                    chrome = args.chromium.resolve()
                    browser = await pw.chromium.launch(
                        executable_path=str(chrome), headless=True
                    )
                    context = await browser.new_context(
                        viewport={"width": 1600, "height": 1000}
                    )
                    page = await context.new_page()
                    page.on(
                        "pageerror",
                        lambda error: print("PAGE_ERROR:", error, flush=True),
                    )
                    file_requests = []
                    page.on(
                        "request",
                        lambda request: (
                            file_requests.append(request.url)
                            if "/spcode/file-browser" in request.url
                            else None
                        ),
                    )
                    await page.goto(origin + "/#/auth/login")
                    await page.locator('input[name="username"]').fill("f01")
                    await page.locator('input[name="password"]').fill(password)
                    await page.locator('button[type="submit"]').click()
                    await page.wait_for_function("!!localStorage.getItem('token')")
                    await page.wait_for_function(
                        "!location.hash.includes('/auth/')", timeout=60000
                    )
                    token = await page.evaluate("localStorage.getItem('token')")
                    headers = {"Authorization": f"Bearer {token}"}
                    endpoint = origin + "/api/v1/plugins/extensions/spcode/file-browser"
                    async with client.get(
                        endpoint, params={"path": str(target), "scope": "host"}
                    ) as response:
                        assert response.status in (401, 403), response.status
                    async with client.get(
                        endpoint,
                        params={"path": str(target), "scope": "host"},
                        headers=headers,
                    ) as response:
                        payload = await response.json()
                        assert payload["data"]["content"].startswith(
                            "F01 source line 1"
                        ), payload
                    checks.append(
                        "real login and unauthenticated rejection; actual installed spcode reads host file"
                    )
                    async with client.get(
                        origin + "/api/v1/chat/sessions/new", headers=headers
                    ) as response:
                        chat_id = (await response.json())["data"]["session_id"]
                    await page.goto(origin + f"/#/chat/{chat_id}")
                    await page.locator(".chat-text-input, .chat-textarea").wait_for(
                        timeout=60000
                    )
                    await page.locator(".session-item.active").wait_for(timeout=60000)
                    with contextlib.suppress(Exception):
                        await page.get_by_role("button", name="关闭", exact=True).click(
                            timeout=2500
                        )
                    await page.locator(".chat-text-input, .chat-textarea").fill(
                        "F01_RUN"
                    )
                    await page.locator(".send-btn").click()
                    await asyncio.wait_for(stream_gate.wait(), 60)
                    await asyncio.sleep(0.3)
                    assert not file_requests, file_requests
                    await page.get_by_text("F01_DONE", exact=False).first.wait_for(
                        timeout=90000
                    )
                    await (
                        page.locator(".file-reference-link")
                        .filter(has_text="F01 whole")
                        .first.wait_for(timeout=15000)
                    )
                    assert not file_requests, file_requests
                    assert await page.locator(".file-reference-link").count() == 7
                    checks.append(
                        "real Agent request contains protocol; streaming completion and rendering never auto-read"
                    )

                    async def click(label):
                        """Activate the named citation through the production ChatUI.

                        Args:
                            label: Exact visible citation label.
                        """
                        await page.get_by_role(
                            "link", name=label, exact=True
                        ).first.click()

                    await click("F01 whole")
                    await (
                        page.locator(".code-content")
                        .get_by_text("F01 source line 80", exact=True)
                        .wait_for(timeout=20000)
                    )
                    assert not await page.get_by_role(
                        "button", name="编辑", exact=True
                    ).count()
                    await click("F01 single")
                    await page.wait_for_function(
                        "document.querySelectorAll('.line-referenced').length === 1"
                    )
                    assert (
                        "line 40" in await page.locator(".line-referenced").inner_text()
                    )
                    await click("F01 range")
                    await page.wait_for_function(
                        "document.querySelectorAll('.line-referenced').length === 4"
                    )
                    assert (
                        "line 55"
                        in await page.locator(".line-referenced").first.inner_text()
                    )
                    await page.wait_for_function(
                        "document.querySelector('.code-view')?.scrollTop > 200"
                    )
                    await page.wait_for_timeout(700)
                    await page.screenshot(
                        path=str(evidence / "file-reference-range.png")
                    )
                    checks.append(
                        "no loaded project; Chinese space encoded Windows path; whole/single/closed range current source preview"
                    )
                    await click("F01 invalid")
                    await page.get_by_text(
                        "引用的源文本行范围超出当前文件。", exact=True
                    ).wait_for()
                    assert await page.locator(".line-referenced").count() == 0
                    await click("F01 missing")
                    await page.get_by_text("路径不存在", exact=False).first.wait_for()
                    await click("F01 large")
                    await page.get_by_text("文件过大", exact=False).first.wait_for()
                    checks.append(
                        "missing, oversize and invalid range produce explicit UI states"
                    )
                    target.write_text("CURRENT DISK CONTENT\n", encoding="utf-8")
                    await click("F01 whole")
                    await (
                        page.locator(".code-content")
                        .get_by_text("CURRENT DISK CONTENT", exact=True)
                        .wait_for()
                    )
                    await page.reload()
                    await page.get_by_role(
                        "link", name="F01 whole", exact=True
                    ).first.wait_for(timeout=60000)
                    await click("F01 whole")
                    await (
                        page.locator(".code-content")
                        .get_by_text("CURRENT DISK CONTENT", exact=True)
                        .wait_for()
                    )
                    checks.append(
                        "same-path reopen and persisted history read current disk bytes"
                    )
                    umo = "webchat:FriendMessage:webchat!f01!" + chat_id
                    for project_name in ["project-one", "project-two"]:
                        project = evidence / project_name
                        project.mkdir()
                        project_file = project / "project.txt"
                        project_file.write_text(
                            "ORIGINAL PROJECT CONTENT\n", encoding="utf-8"
                        )
                        for command in [
                            ["git", "init"],
                            ["git", "add", "project.txt"],
                            [
                                "git",
                                "-c",
                                "user.name=F01 Acceptance",
                                "-c",
                                "user.email=f01@example.invalid",
                                "commit",
                                "-m",
                                "test: seed temporary project",
                            ],
                        ]:
                            await asyncio.to_thread(
                                subprocess.run,
                                command,
                                cwd=project,
                                check=True,
                                capture_output=True,
                                creationflags=getattr(
                                    subprocess, "CREATE_NO_WINDOW", 0
                                ),
                            )
                        project_file.write_text(
                            "PROJECT FILE CONTENT\n", encoding="utf-8"
                        )
                        async with client.post(
                            origin + "/api/v1/plugins/extensions/spcode/project-load",
                            headers=headers,
                            json={
                                "umo": umo,
                                "directory": str(project),
                                "force": True,
                                "no_agentsmd": True,
                                "no_codegraph": True,
                            },
                        ) as response:
                            assert (await response.json())["data"]["success"]
                        await page.reload()
                        await page.get_by_role(
                            "link", name="F01 whole", exact=True
                        ).first.wait_for(timeout=60000)
                        await click("F01 whole")
                        await (
                            page.locator(".code-content")
                            .get_by_text("CURRENT DISK CONTENT", exact=True)
                            .wait_for()
                        )
                    checks.append(
                        "history links remain host-absolute across two real project loads"
                    )
                    await page.get_by_role("tab", name="工作区", exact=True).click()
                    await (
                        page.locator(".file-tree-list .entry-name")
                        .get_by_text("project.txt", exact=True)
                        .wait_for()
                    )
                    assert not await page.get_by_text(
                        "CURRENT DISK CONTENT", exact=True
                    ).count()
                    await page.screenshot(
                        path=str(evidence / "reference-to-project-list.png")
                    )
                    checks.append(
                        "citation -> existing Files tab restores the real project file list"
                    )

                    await (
                        page.locator(".file-browser-entry")
                        .filter(has_text="project.txt")
                        .click()
                    )
                    await (
                        page.locator(".code-content")
                        .get_by_text("PROJECT FILE CONTENT", exact=True)
                        .wait_for()
                    )
                    await page.get_by_role("button", name="编辑", exact=True).click()
                    await page.locator(".cm-content").fill("UNSAVED PROJECT DRAFT")
                    page.once("dialog", lambda dialog: dialog.dismiss())
                    await click("F01 whole")
                    assert (
                        await page.locator(".cm-content").inner_text()
                        == "UNSAVED PROJECT DRAFT"
                    )
                    assert (
                        project_file.read_text(encoding="utf-8")
                        == "PROJECT FILE CONTENT\n"
                    )
                    page.once("dialog", lambda dialog: dialog.accept())
                    await (
                        page.locator(".preview-editor-toolbar")
                        .get_by_role("button", name="取消", exact=True)
                        .click()
                    )
                    checks.append(
                        "cancelling the existing dirty guard preserves the project editor and draft"
                    )

                    await click("F01 whole")
                    await (
                        page.locator(".code-content")
                        .get_by_text("CURRENT DISK CONTENT", exact=True)
                        .wait_for()
                    )
                    await page.get_by_role("tab", name="Git 变更", exact=True).click()
                    await page.locator(".git-diff-file-open").click()
                    await page.get_by_text("在文件浏览器中打开", exact=True).click()
                    await (
                        page.locator(".code-content")
                        .get_by_text("PROJECT FILE CONTENT", exact=True)
                        .wait_for()
                    )
                    assert not await page.get_by_text(
                        "CURRENT DISK CONTENT", exact=True
                    ).count()
                    await page.screenshot(
                        path=str(evidence / "reference-to-git-project-file.png")
                    )
                    checks.append(
                        "citation -> Git diff -> actual open-file menu opens the selected project file"
                    )
                    # Simulate unavailable transport only at the browser boundary.
                    await page.route(
                        "**/plugins/extensions/spcode/file-browser*",
                        lambda route: route.fulfill(
                            status=503, json={"status": "error"}
                        ),
                    )
                    await click("F01 other")
                    await page.get_by_text(
                        "文件预览需要 spcode。", exact=False
                    ).wait_for()
                    checks.append(
                        "unavailable service UI feedback (transport fault injection)"
                    )
                    await page.unroute("**/plugins/extensions/spcode/file-browser*")
                    async with client.post(
                        origin + "/api/plugin/off",
                        headers=headers,
                        json={"name": "astrbot_plugin_spcode_toolkit"},
                    ) as response:
                        assert response.status == 200, await response.text()
                    await click("F01 other")
                    await page.get_by_text(
                        "文件预览需要 spcode。", exact=False
                    ).wait_for()
                    checks.append(
                        "actually disabled spcode keeps citation visible and shows enable-plugin feedback"
                    )
                    async with client.post(
                        origin + "/api/plugin/on",
                        headers=headers,
                        json={"name": "astrbot_plugin_spcode_toolkit"},
                    ) as response:
                        assert response.status == 200, await response.text()
                    await click("F01 other")
                    await (
                        page.locator(".code-content")
                        .get_by_text("SECOND TARGET", exact=True)
                        .wait_for()
                    )
                    checks.append("re-enabled plugin restores the real host preview")
                    await browser.close()
        (evidence / "result.json").write_text(
            json.dumps(
                {
                    "passed": True,
                    "checks": checks,
                    "fixture": "deterministic local OpenAI-compatible model",
                    "vllm": "not tested",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {"evidence": str(evidence), "checks": checks}, ensure_ascii=False
            ),
            flush=True,
        )
    finally:
        (evidence / "model-requests.json").write_text(
            json.dumps(requests, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if process and process.poll() is None:
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                await asyncio.to_thread(process.wait, 15)
            if process.poll() is None:
                process.kill()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
