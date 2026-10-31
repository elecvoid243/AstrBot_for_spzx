"""Exercise file references and host browsing through the real authenticated ChatUI."""

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
import tempfile
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
    parser.add_argument("--rich-previews", action="store_true")
    parser.add_argument("--editing", action="store_true")
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
    cross_drive = tempfile.TemporaryDirectory(prefix="astrbot-f02-")
    cross_folder = Path(cross_drive.name)
    cross_file = cross_folder / "C-drive.txt"
    cross_file.write_text("F02 REAL C DRIVE\n", encoding="utf-8")
    overlimit = cross_folder / "overlimit"
    overlimit.mkdir()
    for index in range(1002):
        (overlimit / f"temporary-{index:04}.txt").touch()
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
    if args.rich_previews:
        # Real document bytes, parsed by the existing production viewers.
        from docx import Document
        from openpyxl import Workbook
        from PIL import Image
        from reportlab.pdfgen.canvas import Canvas

        rich = {}
        for extension in ["png", "pdf", "docx", "xlsx", "csv", "md"]:
            rich[extension] = files / f"预览 space#%文档.{extension}"
        Image.new("RGB", (24, 16), "red").save(rich["png"])
        canvas = Canvas(str(rich["pdf"]))
        canvas.drawString(60, 750, "F03 PDF REAL DOCUMENT")
        canvas.save()
        document = Document()
        document.add_paragraph("F03 DOCX REAL DOCUMENT")
        document.save(rich["docx"])
        workbook = Workbook()
        workbook.active.append(["F03 XLSX REAL DOCUMENT", 42])
        workbook.save(rich["xlsx"])
        rich["csv"].write_text('name,value\n"F03 CSV\nmultiline",7\n', encoding="utf-8")
        rich["md"].write_text("# F03 Markdown\n\nF03 SOURCE LINE 3\n", encoding="utf-8")
        unsupported = files / "unsupported.zip"
        unsupported.write_bytes(b"PK\x03\x04\x00")
        oversized = files / "oversized.pdf"
        with oversized.open("wb") as stream:
            stream.truncate(50 * 1024 * 1024 + 1)
        for prefix, folder in [
            ("external", files),
            ("inside", evidence / "project-one"),
        ]:
            for extension, path in rich.items():
                links += f"\n\n[F03 {prefix} {extension}]({quote((folder / path.name).as_posix(), safe='/:')})"
        for label, path, fragment in [
            ("CSV source", rich["csv"], "#L3"),
            ("MD source", rich["md"], "#L3"),
            ("PDF line", rich["pdf"], "#L3"),
            ("unsupported", unsupported, ""),
            ("oversize", oversized, ""),
        ]:
            links += f"\n\n[F03 {label}]({quote(path.as_posix(), safe='/:')}{fragment})"

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

                    async def jump_host(path):
                        """Enter a real host target through the production path bar.

                        Args:
                            path: Absolute filesystem path to browse or preview.
                        """
                        await page.locator(".breadcrumb-leaf").click()
                        await page.locator(".breadcrumb-path-input").fill(str(path))
                        await page.locator(".breadcrumb-path-input").press("Enter")

                    await page.get_by_role(
                        "button", name="文件与浏览器", exact=True
                    ).click()
                    await page.get_by_role("tab", name="文件", exact=True).click()
                    picker = page.locator(".v-dialog")
                    await picker.locator('[data-testid="dir-path-input"] input').fill(
                        str(files)
                    )
                    await picker.locator('[data-testid="dir-path-input"] input').press(
                        "Enter"
                    )
                    await picker.get_by_text(files.name, exact=True).first.wait_for()
                    await picker.locator('[data-testid="dir-confirm"]').click()
                    await (
                        page.locator(".file-browser-entry")
                        .filter(has_text="second.txt")
                        .click()
                    )
                    await (
                        page.locator(".code-content")
                        .get_by_text("SECOND TARGET", exact=True)
                        .wait_for()
                    )
                    assert (
                        files.drive.lower() == "g:"
                        and cross_folder.drive.lower() == "c:"
                    )
                    await page.get_by_role(
                        "button", name="浏览宿主目录或切换盘符", exact=True
                    ).click()
                    await picker.locator('[data-testid="dir-computer"]').click()
                    await picker.locator(".dir-item").filter(has_text="C:\\").dblclick()
                    await picker.locator('[data-testid="dir-path-input"] input').fill(
                        str(cross_folder)
                    )
                    await picker.locator('[data-testid="dir-path-input"] input').press(
                        "Enter"
                    )
                    await picker.get_by_text(
                        cross_folder.name, exact=True
                    ).first.wait_for()
                    await picker.locator('[data-testid="dir-confirm"]').click()
                    await (
                        page.locator(".file-browser-entry")
                        .filter(has_text="C-drive.txt")
                        .click()
                    )
                    await (
                        page.locator(".code-content")
                        .get_by_text("F02 REAL C DRIVE", exact=True)
                        .wait_for()
                    )
                    await page.screenshot(path=str(evidence / "host-browsing-zh.png"))
                    await jump_host(target)
                    await (
                        page.locator(".code-content")
                        .get_by_text("F01 source line 80", exact=True)
                        .wait_for()
                    )
                    await page.get_by_role(
                        "button", name="上一级目录", exact=True
                    ).click()
                    await (
                        page.locator(".file-browser-entry")
                        .filter(has_text=files.name)
                        .wait_for()
                    )
                    await (
                        page.locator(".file-browser-entry")
                        .filter(has_text=files.name)
                        .dblclick()
                    )
                    await (
                        page.locator(".file-browser-entry")
                        .filter(has_text="second.txt")
                        .wait_for()
                    )
                    checks.append(
                        "F02 no project manual Files entry; real G:/C: drive picker, directory items, parent navigation and file path input"
                    )
                    await jump_host(overlimit)
                    await page.get_by_text(
                        "⚠ 列表已截断,仅显示前 1000 项", exact=True
                    ).wait_for()
                    await jump_host(cross_folder / "missing-folder")
                    await page.get_by_text("路径不存在", exact=False).first.wait_for()
                    checks.append(
                        "F02 real missing manual target and real 1002-entry directory show explicit error and truncation"
                    )
                    await page.get_by_role(
                        "button", name="文件与浏览器", exact=True
                    ).click()
                    await page.wait_for_timeout(250)
                    file_requests.clear()

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
                    assert await page.locator(".file-reference-link").count() == (
                        24 if args.rich_previews else 7
                    )
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

                    async def render_rich(prefix, scenario):
                        """Render all six real files through actual Agent citation links.

                        Args:
                            prefix: Citation group (external or inside).
                            scenario: Evidence label describing project context.
                        """
                        for extension in rich:
                            await click(f"F03 {prefix} {extension}")
                            if extension == "png":
                                await page.wait_for_function(
                                    "document.querySelector('.image-preview img')?.naturalWidth === 24"
                                )
                            elif extension == "pdf":
                                await page.wait_for_function(
                                    "document.querySelector('.pdf-preview canvas')?.width > 0"
                                )
                                await page.wait_for_function("""() => {
                                    const c = document.querySelector('.pdf-preview canvas');
                                    if (!c || c.width < 500) return false;
                                    const pixels = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                                    return pixels.some((v, i) => i % 4 !== 3 && v < 200 && pixels[i - (i % 4) + 3] === 255);
                                }""")
                            elif extension == "docx":
                                await (
                                    page.locator(".docx-preview__body")
                                    .get_by_text("F03 DOCX REAL DOCUMENT", exact=True)
                                    .wait_for()
                                )
                            elif extension == "xlsx":
                                await (
                                    page.locator(".xlsx-preview table")
                                    .get_by_text("F03 XLSX REAL DOCUMENT", exact=True)
                                    .wait_for()
                                )
                            elif extension == "csv":
                                await (
                                    page.locator(".csv-preview table")
                                    .get_by_text("F03 CSV multiline", exact=True)
                                    .wait_for()
                                )
                            else:
                                await (
                                    page.locator(".preview-markdown h1")
                                    .get_by_text("F03 Markdown", exact=True)
                                    .wait_for()
                                )
                            await page.screenshot(
                                path=str(evidence / f"rich-{scenario}-{extension}.png")
                            )
                            checks.append(
                                f"F03 {scenario}: actual {extension} viewer rendered current real file via Agent citation"
                            )

                    if args.rich_previews:
                        await render_rich("external", "no-project")
                        Image.new("RGB", (32, 20), "blue").save(rich["png"])
                        await click("F03 external png")
                        await page.wait_for_function(
                            "document.querySelector('.image-preview img')?.naturalWidth === 32"
                        )
                        Image.new("RGB", (24, 16), "red").save(rich["png"])
                        await click("F03 external png")
                        await page.wait_for_function(
                            "document.querySelector('.image-preview img')?.naturalWidth === 24"
                        )
                        checks.append(
                            "F03 same-path image citation rereads externally changed disk bytes on every click"
                        )
                        await click("F03 CSV source")
                        await (
                            page.locator(".line-referenced")
                            .get_by_text('multiline",7', exact=True)
                            .wait_for()
                        )
                        assert not await page.locator(".csv-preview").count()
                        await click("F03 MD source")
                        await (
                            page.locator(".line-referenced")
                            .get_by_text("F03 SOURCE LINE 3", exact=True)
                            .wait_for()
                        )
                        await click("F03 PDF line")
                        await page.get_by_text(
                            "此格式无法按源文本行号定位。", exact=True
                        ).wait_for()
                        assert await page.locator(".line-referenced").count() == 0
                        checks.append(
                            "F03 CSV quoted multiline and Markdown citations locate source lines; PDF line citation gives explicit unavailable feedback"
                        )
                        await click("F03 unsupported")
                        await page.locator(".preview-binary").wait_for()
                        await click("F03 oversize")
                        await page.get_by_text(
                            "文件超过 50MB 预览上限", exact=True
                        ).wait_for()
                        await page.route(
                            "**/plugins/extensions/spcode/file-binary*",
                            lambda route: route.fulfill(
                                status=403, json={"reason": "permission_denied"}
                            ),
                        )
                        await click("F03 external pdf")
                        await page.get_by_text(
                            "宿主账户无权读取此文件。", exact=True
                        ).wait_for()
                        await page.unroute("**/plugins/extensions/spcode/file-binary*")
                        checks.append(
                            "F03 unsupported real binary and real oversized PDF show errors; permission denial is a transport boundary injection"
                        )
                        await page.route(
                            "**/plugins/extensions/spcode/file-binary*",
                            lambda route: route.fulfill(
                                status=404, json={"reason": "path_not_found"}
                            ),
                        )
                        await click("F03 external pdf")
                        await page.get_by_text("文件已不存在。", exact=True).wait_for()
                        await page.unroute("**/plugins/extensions/spcode/file-binary*")
                        checks.append(
                            "F03 missing-between-metadata-and-bytes error has localized UI feedback (transport boundary injection)"
                        )
                        started, release = asyncio.Event(), asyncio.Event()

                        async def hold_binary(route):
                            """Delay actual binary response to exercise plugin lifecycle.

                            Args:
                                route: Intercepted browser request to the real file service.
                            """
                            started.set()
                            await release.wait()
                            await route.continue_()

                        await page.route(
                            "**/plugins/extensions/spcode/file-binary*", hold_binary
                        )
                        await click("F03 external pdf")
                        await asyncio.wait_for(started.wait(), 20)
                        async with client.post(
                            origin + "/api/plugin/off",
                            headers=headers,
                            json={"name": "astrbot_plugin_spcode_toolkit"},
                        ) as response:
                            assert response.status == 200
                        release.set()
                        await page.get_by_text(
                            "请启用 spcode 插件以预览此文件。", exact=True
                        ).wait_for()
                        await page.unroute("**/plugins/extensions/spcode/file-binary*")
                        async with client.post(
                            origin + "/api/plugin/on",
                            headers=headers,
                            json={"name": "astrbot_plugin_spcode_toolkit"},
                        ) as response:
                            assert response.status == 200
                        await click("F03 external pdf")
                        await page.wait_for_function(
                            "document.querySelector('.pdf-preview canvas')?.width > 20"
                        )
                        checks.append(
                            "F03 actual plugin off/on between metadata and byte read produces enable feedback, then restores PDF"
                        )
                        started, release = asyncio.Event(), asyncio.Event()
                        await page.route(
                            "**/plugins/extensions/spcode/file-binary*", hold_binary
                        )
                        await click("F03 external pdf")
                        await asyncio.wait_for(started.wait(), 20)
                        await click("F03 external md")
                        await page.locator(".preview-markdown h1").wait_for()
                        release.set()
                        await page.wait_for_timeout(300)
                        assert not await page.locator(".pdf-preview").count()
                        await page.unroute("**/plugins/extensions/spcode/file-binary*")
                        checks.append(
                            "F03 delayed binary transport followed by Markdown navigation cannot overwrite the latest target"
                        )

                    await click("F01 whole")
                    await (
                        page.locator(".code-content")
                        .get_by_text("F01 source line 80", exact=True)
                        .wait_for(timeout=20000)
                    )
                    assert await page.get_by_role(
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
                    if args.editing:
                        edit_umo = "webchat:FriendMessage:webchat!f01!" + chat_id
                        permissions_url = origin + "/api/v1/chat/file-access-mode"
                        original_other = other.read_bytes()
                        write_url = (
                            origin + "/api/v1/plugins/extensions/spcode/host-file-write"
                        )
                        async with client.post(write_url, json={}) as response:
                            assert response.status in (401, 403)
                        for mode, encoding, bom, newline in [
                            ("readonly", "utf-8", b"\xef\xbb\xbf", "\r\n"),
                            ("workspace", "gbk", b"", "\r\n"),
                        ]:
                            other.write_bytes(
                                bom + ("旧内容" + newline).encode(encoding)
                            )
                            async with client.post(
                                permissions_url,
                                headers=headers,
                                json={"umo": edit_umo, "mode": mode},
                            ) as response:
                                assert response.status == 200
                            async with client.get(
                                permissions_url,
                                headers=headers,
                                params={"umo": edit_umo},
                            ) as response:
                                before = (await response.json())["data"]
                            await click("F01 other")
                            await page.get_by_role(
                                "button", name="编辑", exact=True
                            ).click()
                            await page.locator(".cm-content").fill("人工保存\n")
                            async with page.expect_response(
                                lambda response: (
                                    "/spcode/host-file-write" in response.url
                                )
                            ) as saved_response:
                                await (
                                    page.locator(".preview-editor-toolbar")
                                    .get_by_role("button", name="保存", exact=True)
                                    .click()
                                )
                            assert (await (await saved_response.value).json())["data"][
                                "saved"
                            ]
                            await (
                                page.get_by_role("status")
                                .filter(has_text="已保存")
                                .wait_for()
                            )
                            assert other.read_bytes() == bom + (
                                "人工保存" + newline
                            ).encode(encoding), (mode, other.read_bytes())
                            async with client.get(
                                permissions_url,
                                headers=headers,
                                params={"umo": edit_umo},
                            ) as response:
                                assert (await response.json())["data"] == before
                            # Reopening the same clean target must leave edit mode and reread disk.
                            await click("F01 other")
                            await (
                                page.locator(".code-content")
                                .get_by_text("人工保存", exact=True)
                                .wait_for()
                            )
                            assert not await page.locator(".cm-content").count()
                            await page.get_by_role(
                                "button", name="编辑", exact=True
                            ).click()
                            # A real external writer changes the file; saving must preserve both versions.
                            await page.locator(".cm-content").fill("完整草稿\n")
                            other.write_bytes(
                                bom + ("外部修改" + newline).encode(encoding)
                            )
                            await click("F01 whole")
                            await (
                                page.get_by_role("dialog")
                                .get_by_role("button", name="保存并打开", exact=True)
                                .click()
                            )
                            await page.get_by_text(
                                "磁盘文件已发生变化", exact=False
                            ).wait_for()
                            assert (
                                await page.locator(".cm-content").inner_text()
                            ).rstrip("\n") == "完整草稿"
                            assert other.read_bytes() == bom + (
                                "外部修改" + newline
                            ).encode(encoding)
                            # The first confirmation is already stale. No force-save bypass exists.
                            other.write_bytes(
                                bom + ("再次外部修改" + newline).encode(encoding)
                            )
                            page.once("dialog", lambda dialog: dialog.accept())
                            await page.get_by_role(
                                "button", name="覆盖已确认版本", exact=True
                            ).click()
                            await page.wait_for_timeout(250)
                            assert other.read_bytes() == bom + (
                                "再次外部修改" + newline
                            ).encode(encoding)
                            page.once("dialog", lambda dialog: dialog.accept())
                            await page.get_by_role(
                                "button", name="覆盖已确认版本", exact=True
                            ).click()
                            await page.get_by_text(
                                "磁盘文件已发生变化", exact=False
                            ).wait_for(state="hidden")
                            assert other.read_bytes() == bom + (
                                "完整草稿" + newline
                            ).encode(encoding)
                            # Cancel preserves the full editor; discard opens without writing.
                            await page.locator(".cm-content").fill("CANCELLED DRAFT")
                            await click("F01 whole")
                            await (
                                page.get_by_role("dialog")
                                .get_by_role("button", name="取消", exact=True)
                                .click()
                            )
                            assert (
                                await page.locator(".cm-content").inner_text()
                                == "CANCELLED DRAFT"
                            )
                            await click("F01 whole")
                            await (
                                page.get_by_role("dialog")
                                .get_by_role("button", name="放弃并打开", exact=True)
                                .click()
                            )
                            await (
                                page.locator(".code-content")
                                .get_by_text("F01 source line 80", exact=True)
                                .wait_for()
                            )
                            await click("F01 other")
                            await page.get_by_role(
                                "button", name="编辑", exact=True
                            ).click()
                            await page.locator(".cm-content").fill("保存跳转\n")
                            await click("F01 whole")
                            await (
                                page.get_by_role("dialog")
                                .get_by_role("button", name="保存并打开", exact=True)
                                .click()
                            )
                            await (
                                page.locator(".code-content")
                                .get_by_text("F01 source line 80", exact=True)
                                .wait_for()
                            )
                            assert other.read_bytes() == bom + (
                                "保存跳转" + newline
                            ).encode(encoding)
                            checks.append(
                                f"F04 real no-project {mode} manual save, {encoding}/BOM/newlines final bytes, external conflict, stale overwrite, full draft, cancel/discard/save navigation and unchanged Agent permissions"
                            )
                        # All these UI entry points would remove or hide the editor.
                        await click("F01 other")
                        await page.get_by_role(
                            "button", name="编辑", exact=True
                        ).click()
                        await page.locator(".cm-content").fill("GUARDED COMPLETE DRAFT")
                        for action in [
                            page.get_by_role(
                                "button", name="浏览宿主目录或切换盘符", exact=True
                            ),
                            page.get_by_role("button", name="关闭文件侧栏", exact=True),
                            page.locator(".sidebar-provider-btn").first,
                            page.locator(
                                ".new-chat-btn:not(.sidebar-provider-btn)"
                            ).first,
                            page.get_by_role("tab").last,
                        ]:
                            await action.click()
                            await (
                                page.get_by_role("dialog")
                                .get_by_role("button", name="取消", exact=True)
                                .click()
                            )
                            assert (
                                await page.locator(".cm-content").inner_text()
                                == "GUARDED COMPLETE DRAFT"
                            )
                        await click("F01 whole")
                        await (
                            page.get_by_role("dialog")
                            .get_by_role("button", name="放弃并打开", exact=True)
                            .click()
                        )
                        checks.append(
                            "F04 real directory picker, file pane close, provider workspace, new session and workbench tab all preserve the original draft/view on cancel"
                        )
                        # Real host errors use real files, not a mocked save service.
                        for failure in ["permission", "encoding", "missing"]:
                            other.write_bytes("旧内容\r\n".encode("gbk"))
                            await click("F01 other")
                            await page.get_by_role(
                                "button", name="编辑", exact=True
                            ).click()
                            draft = (
                                "😀" if failure == "encoding" else "FAILED SAVE DRAFT"
                            )
                            await page.locator(".cm-content").fill(draft)
                            if failure == "permission":
                                other.chmod(0o444)
                            elif failure == "missing":
                                other.unlink()
                            async with page.expect_response(
                                lambda response: (
                                    "/spcode/host-file-write" in response.url
                                )
                            ) as failed_response:
                                await (
                                    page.locator(".preview-editor-toolbar")
                                    .get_by_role("button", name="保存", exact=True)
                                    .click()
                                )
                            result = (await (await failed_response.value).json())[
                                "data"
                            ]
                            assert not result["saved"], result
                            assert (
                                result["reason"]
                                == {
                                    "permission": "permission_denied",
                                    "encoding": "encoding_error",
                                    "missing": "path_not_found",
                                }[failure]
                            )
                            assert (
                                await page.locator(".cm-content").inner_text() == draft
                            )
                            if failure != "missing":
                                assert other.read_bytes() == "旧内容\r\n".encode("gbk")
                                other.chmod(0o666)
                            await click("F01 whole")
                            await (
                                page.get_by_role("dialog")
                                .get_by_role("button", name="放弃并打开", exact=True)
                                .click()
                            )
                        other.write_bytes("旧内容\r\n".encode("gbk"))
                        checks.append(
                            "F04 real Windows read-only file, GBK encoding failure and externally removed target all reject saving and preserve the complete UI draft"
                        )
                        # A real save is delayed only after the actual service has written.
                        await click("F01 other")
                        await page.get_by_role(
                            "button", name="编辑", exact=True
                        ).click()
                        await page.locator(".cm-content").fill("SAVED EARLIER SNAPSHOT")
                        entered_save, release_save = asyncio.Event(), asyncio.Event()

                        async def delayed_save(route):
                            response = await route.fetch()
                            entered_save.set()
                            await release_save.wait()
                            await route.fulfill(response=response)

                        await page.route(
                            "**/plugins/extensions/spcode/host-file-write", delayed_save
                        )
                        await (
                            page.locator(".preview-editor-toolbar")
                            .get_by_role("button", name="保存", exact=True)
                            .click()
                        )
                        await asyncio.wait_for(entered_save.wait(), 20)
                        await page.locator(".cm-content").fill("旧内容\n")
                        await click("F01 whole")
                        assert (await page.locator(".cm-content").inner_text()).rstrip(
                            "\n"
                        ) == "旧内容"
                        release_save.set()
                        await page.wait_for_timeout(300)
                        await page.unroute(
                            "**/plugins/extensions/spcode/host-file-write"
                        )
                        assert (await page.locator(".cm-content").inner_text()).rstrip(
                            "\n"
                        ) == "旧内容"
                        assert (
                            other.read_bytes().decode("gbk") == "SAVED EARLIER SNAPSHOT"
                        )
                        await click("F01 whole")
                        await (
                            page.get_by_role("dialog")
                            .get_by_role("button", name="保存并打开", exact=True)
                            .click()
                        )
                        await (
                            page.locator(".code-content")
                            .get_by_text("F01 source line 80", exact=True)
                            .wait_for()
                        )
                        assert other.read_bytes().decode("gbk") == "旧内容\r\n"
                        checks.append(
                            "F04 actual save plus delayed response cannot clear a newer draft returned to the original text or navigate during an in-flight save"
                        )
                        # Explicit reload drops the draft only after user confirmation.
                        await click("F01 other")
                        await page.get_by_role(
                            "button", name="编辑", exact=True
                        ).click()
                        await page.locator(".cm-content").fill("RELOAD DRAFT")
                        other.write_bytes(b"RELOAD DISK\n")
                        await (
                            page.locator(".preview-editor-toolbar")
                            .get_by_role("button", name="保存", exact=True)
                            .click()
                        )
                        await page.get_by_text(
                            "磁盘文件已发生变化", exact=False
                        ).wait_for()
                        await page.wait_for_timeout(350)
                        await page.screenshot(
                            path=str(evidence / "file-edit-conflict-zh.png")
                        )
                        page.once("dialog", lambda dialog: dialog.accept())
                        await page.get_by_role(
                            "button", name="从磁盘重新加载", exact=True
                        ).click()
                        await (
                            page.locator(".code-content")
                            .get_by_text("RELOAD DISK", exact=True)
                            .wait_for()
                        )
                        other.write_bytes(original_other)
                        await click("F01 whole")
                        checks.append(
                            "F04 real conflict reload explicitly replaces the draft with current disk contents; unauthenticated saves rejected"
                        )

                    for label, first_line, last_line in [
                        ("F01 single", 40, 40),
                        ("F01 range", 55, 58),
                    ]:
                        async with client.post(
                            origin + "/api/plugin/off",
                            headers=headers,
                            json={"name": "astrbot_plugin_spcode_toolkit"},
                        ) as response:
                            assert response.status == 200, await response.text()
                        await click(label)
                        preview = page.locator(".file-browser-pane-right")
                        await preview.get_by_text(
                            "文件预览需要 spcode。", exact=False
                        ).wait_for()
                        if first_line == 40:
                            await page.get_by_role(
                                "button", name="浏览宿主目录或切换盘符", exact=True
                            ).click()
                            await picker.get_by_text(
                                "请安装或启用 spcode 插件", exact=False
                            ).wait_for()
                            assert "[MISSING:" not in await picker.inner_text()
                            await page.screenshot(
                                path=str(
                                    evidence / "disabled-plugin-folder-picker.png"
                                ),
                                animations="disabled",
                            )
                            await picker.get_by_role(
                                "button", name="取消", exact=True
                            ).click()
                            checks.append(
                                "F02 correction: actually disabled spcode folder picker displays real install/enable guidance without a missing translation key"
                            )
                        async with client.post(
                            origin + "/api/plugin/on",
                            headers=headers,
                            json={"name": "astrbot_plugin_spcode_toolkit"},
                        ) as response:
                            assert response.status == 200, await response.text()
                        if first_line == 40:
                            # Supplementary transport fault: match the host's real HTTP 200 missing-route envelope.
                            await page.route(
                                "**/plugins/extensions/spcode/file-browser*",
                                lambda route: route.fulfill(
                                    status=200,
                                    json={
                                        "status": "error",
                                        "message": "未找到该路由",
                                        "data": {},
                                    },
                                ),
                            )
                            before_requests = len(file_requests)
                            await page.get_by_role(
                                "button", name="浏览宿主目录或切换盘符", exact=True
                            ).click()
                            await picker.get_by_text(
                                "请安装或启用 spcode 插件", exact=False
                            ).wait_for()
                            assert "[MISSING:" not in await picker.inner_text()
                            await page.wait_for_timeout(200)
                            assert len(file_requests) - before_requests == 1, (
                                file_requests[before_requests:]
                            )
                            await page.screenshot(
                                path=str(evidence / "missing-route-folder-picker.png"),
                                animations="disabled",
                            )
                            await picker.get_by_role(
                                "button", name="取消", exact=True
                            ).click()
                            await page.unroute(
                                "**/plugins/extensions/spcode/file-browser*"
                            )
                            checks.append(
                                "F02 correction: HTTP 200 status:error/data:{} transport injection shows plugin guidance and makes exactly one folder request, without parent fallback"
                            )
                        await preview.get_by_role(
                            "button", name="重试", exact=True
                        ).click()
                        await (
                            preview.locator(".code-content")
                            .get_by_text("F01 source line 80", exact=True)
                            .wait_for()
                        )
                        await page.wait_for_function(
                            "count => document.querySelectorAll('.line-referenced').length === count",
                            arg=last_line - first_line + 1,
                        )
                        assert (
                            f"line {first_line}"
                            in await page.locator(".line-referenced").first.inner_text()
                        )
                        assert (
                            f"line {last_line}"
                            in await page.locator(".line-referenced").last.inner_text()
                        )
                        await page.wait_for_function("""() => {
                            const view = document.querySelector('.code-view')?.getBoundingClientRect();
                            const line = document.querySelector('.line-referenced')?.getBoundingClientRect();
                            return view && line && line.top >= view.top && line.bottom <= Math.min(view.bottom, window.innerHeight);
                        }""")
                        await page.screenshot(
                            path=str(
                                evidence
                                / f"citation-retry-{first_line}-{last_line}.png"
                            )
                        )
                        checks.append(
                            f"F02 correction: actual spcode disable/enable followed by existing Retry preserves current text and visible source lines {first_line}-{last_line}"
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
                        if args.rich_previews and project_name == "project-one":
                            for source in rich.values():
                                shutil.copy2(source, project / source.name)
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
                        if args.rich_previews:
                            await render_rich(
                                "inside"
                                if project_name == "project-one"
                                else "external",
                                "project-inside"
                                if project_name == "project-one"
                                else "project-outside",
                            )
                    checks.append(
                        "history links remain host-absolute across two real project loads"
                    )

                    # Manual host browsing must leave the actual session context unchanged.
                    async def session_context():
                        """Read the real project and worktree state for this conversation.

                        Returns:
                            Stable project directory and active worktree values.
                        """
                        values = []
                        for route in ["project-status", "git-worktrees"]:
                            async with client.get(
                                origin + "/api/v1/plugins/extensions/spcode/" + route,
                                headers=headers,
                                params={"umo": umo},
                            ) as response:
                                state = (await response.json())["data"]
                                values.append(
                                    (
                                        state.get("directory"),
                                        state.get("active_worktree"),
                                    )
                                )
                        return values

                    async with client.post(
                        origin + "/api/v1/plugins/extensions/spcode/worktree-activate",
                        headers=headers,
                        json={"umo": umo, "path": str(project)},
                    ) as response:
                        assert (await response.json())["data"]["success"]
                    async with client.post(
                        origin + "/api/v1/chat/file-access-mode",
                        headers=headers,
                        json={"umo": umo, "mode": "readonly"},
                    ) as response:
                        assert response.status == 200
                    before_context = await session_context()
                    if args.editing:
                        await jump_host(cross_file)
                        await page.get_by_role(
                            "button", name="编辑", exact=True
                        ).click()
                        await page.locator(".cm-content").fill(
                            "F04 LOADED PROJECT EXTERNAL SAVE"
                        )
                        async with page.expect_response(
                            lambda response: "/spcode/host-file-write" in response.url
                        ) as project_save:
                            await (
                                page.locator(".preview-editor-toolbar")
                                .get_by_role("button", name="保存", exact=True)
                                .click()
                            )
                        assert (await (await project_save.value).json())["data"][
                            "saved"
                        ]
                        assert (
                            cross_file.read_bytes()
                            == b"F04 LOADED PROJECT EXTERNAL SAVE"
                        )
                        assert await session_context() == before_context
                        await (
                            page.locator(".preview-editor-toolbar")
                            .get_by_role("button", name="取消", exact=True)
                            .click()
                        )
                        cross_file.write_bytes(b"F02 REAL C DRIVE\n")
                        checks.append(
                            "F04 actual cross-drive save with loaded project and active worktree leaves their real backend state unchanged"
                        )

                    async with client.get(
                        origin + "/api/v1/chat/file-access-mode",
                        headers=headers,
                        params={"umo": umo},
                    ) as response:
                        before_permissions = (await response.json())["data"]
                    await jump_host(cross_file)
                    await (
                        page.locator(".code-content")
                        .get_by_text("F02 REAL C DRIVE", exact=True)
                        .wait_for()
                    )
                    await jump_host(project_file)
                    await (
                        page.locator(".code-content")
                        .get_by_text("PROJECT FILE CONTENT", exact=True)
                        .wait_for()
                    )
                    await jump_host(cross_file)
                    await (
                        page.locator(".code-content")
                        .get_by_text("F02 REAL C DRIVE", exact=True)
                        .wait_for()
                    )
                    await page.get_by_role(
                        "button", name="Refresh worktrees", exact=False
                    ).count()
                    # The existing branch/worktree refresh control invokes real polling services.
                    await page.locator(".git-diff-sidebar-bw-refresh").click()
                    await page.wait_for_timeout(1200)
                    assert await session_context() == before_context
                    async with client.get(
                        origin + "/api/v1/chat/file-access-mode",
                        headers=headers,
                        params={"umo": umo},
                    ) as response:
                        assert (await response.json())["data"] == before_permissions
                    await (
                        page.locator(".code-content")
                        .get_by_text("F02 REAL C DRIVE", exact=True)
                        .wait_for()
                    )
                    checks.append(
                        "F02 project -> external drive -> project -> external; actual project, active worktree and permission capsule unchanged across real status refresh"
                    )
                    await page.evaluate(
                        "localStorage.setItem('astrbot-locale', 'en-US')"
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
                    await jump_host(cross_file)
                    await (
                        page.locator(".code-content")
                        .get_by_text("F02 REAL C DRIVE", exact=True)
                        .wait_for()
                    )
                    await page.screenshot(path=str(evidence / "host-browsing-en.png"))
                    if args.editing:
                        await click("F01 other")
                        await page.get_by_role(
                            "button", name="Edit", exact=True
                        ).click()
                        await page.locator(".cm-content").fill(
                            "PRESERVED ENGLISH DRAFT"
                        )
                        other.write_bytes(b"EXTERNAL DISK CHANGE\n")
                        await (
                            page.locator(".preview-editor-toolbar")
                            .get_by_role("button", name="Save", exact=True)
                            .click()
                        )
                        await page.get_by_text(
                            "The file changed on disk.", exact=False
                        ).wait_for()
                        await page.wait_for_timeout(350)
                        await page.screenshot(
                            path=str(evidence / "file-edit-conflict-en.png")
                        )
                        page.once("dialog", lambda dialog: dialog.accept())
                        await page.get_by_role(
                            "button", name="Reload from disk", exact=True
                        ).click()
                        await (
                            page.locator(".code-content")
                            .get_by_text("EXTERNAL DISK CHANGE", exact=True)
                            .wait_for()
                        )
                        other.write_bytes(original_other)
                        await click("F01 whole")

                    await page.evaluate(
                        "localStorage.setItem('astrbot-locale', 'zh-CN')"
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
                    await click("F01 whole")
                    await (
                        page.get_by_role("dialog")
                        .get_by_role("button", name="取消", exact=True)
                        .click()
                    )
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
                    await click("F01 whole")
                    await (
                        page.locator(".code-content")
                        .get_by_text("CURRENT DISK CONTENT", exact=True)
                        .wait_for()
                    )
                    await jump_host(cross_file)
                    await (
                        page.locator(".code-content")
                        .get_by_text("F02 REAL C DRIVE", exact=True)
                        .wait_for()
                    )
                    temporary_worktree = evidence / "temporary-worktree"
                    await asyncio.to_thread(
                        subprocess.run,
                        [
                            "git",
                            "-C",
                            str(project),
                            "worktree",
                            "add",
                            "-b",
                            "f02-fixture",
                            str(temporary_worktree),
                        ],
                        check=True,
                        capture_output=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    await page.locator(".git-diff-sidebar-bw-refresh").click()
                    await page.wait_for_timeout(1500)
                    await (
                        page.locator(".code-content")
                        .get_by_text("F02 REAL C DRIVE", exact=True)
                        .wait_for()
                    )
                    assert await session_context() == before_context
                    await page.locator(".sp-capsule").click()
                    await page.get_by_role(
                        "button", name="切换或重新加载项目…", exact=True
                    ).click()
                    async with page.expect_response(
                        "**/plugins/extensions/spcode/project-unload"
                    ) as unload:
                        await page.get_by_role(
                            "button", name="卸载当前加载目录", exact=True
                        ).click()
                    assert (await (await unload.value).json())["data"]["success"]
                    await page.get_by_text("未加载项目", exact=True).first.wait_for(
                        timeout=30000
                    )
                    await (
                        page.locator(".code-content")
                        .get_by_text("F02 REAL C DRIVE", exact=True)
                        .wait_for()
                    )
                    checks.append(
                        "F02 real worktree topology change and actual project unload preserve the external browser target"
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
        cross_drive.cleanup()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
