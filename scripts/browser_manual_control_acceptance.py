"""Verify H01 through real AstrBot login, production ChatUI and private Chromium.

Run with a development interpreter containing AstrBot dependencies, aiohttp and
playwright. Only the supplied, newly created evidence directory is
written. The OpenAI-compatible model fixture and website bind to loopback.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import io
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
from aiohttp import web
from PIL import Image
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    """Install into disposable data and exercise production UI and lifecycle.

    Raises:
        AssertionError: A published behavior or resource check fails.
        RuntimeError: The isolated host cannot start or the fixture fails.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    sys.stdout.reconfigure(encoding="utf-8")
    parser.add_argument("--plugin-source", type=Path, required=True)
    parser.add_argument("--private-runtime", type=Path, required=True)
    parser.add_argument("--dashboard-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    runtime_root = evidence / "isolated-host"
    data = runtime_root / "data"
    installed = data / "plugins/astrbot_plugin_browser_use"
    shutil.copytree(
        args.plugin_source.resolve(),
        installed,
        ignore=shutil.ignore_patterns(
            ".git",
            "runtime",
            "tests",
            "dist",
            ".build",
            ".dev",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
        ),
    )
    chromium = args.private_runtime.resolve() / "chromium/chrome.exe"
    metrics = {"passed": False, "checks": []}
    counter = 0
    cookies = []
    login_cookie = secrets.token_urlsafe(24)
    ordinary_password = "H01" + secrets.token_urlsafe(20) + "9"
    model_requests = []
    slow_entered = asyncio.Event()

    async def website(request):
        """Serve a deterministic authenticated counter page for actual CDP clicks.

        Args:
            request: Loopback website request.

        Returns:
            Counter fixture or its acknowledged click number.
        """
        nonlocal counter
        if request.path == "/increment":
            cookies.append(request.cookies.get("fixture_login"))
            assert cookies[-1] == login_cookie
            counter += 1
            return web.Response(text=str(counter))
        if request.path == "/slow":
            slow_entered.set()
            await asyncio.sleep(8)
        response = web.Response(
            text="""<!doctype html><meta charset="utf-8"><title>H01 authenticated counter</title>
<style>body{margin:0;font:24px Arial;background:#eff6ff}button{position:absolute;left:60px;top:60px;width:200px;height:80px;font-size:24px}p{position:absolute;left:60px;top:180px}</style>
<button id="increment" onclick="fetch('/increment').then(r=>r.text()).then(n=>{document.getElementById('count').textContent=n;this.style.backgroundColor='#b9f6ca'})">Click counter</button>
<p>Signed in: H01 fixture · Count: <b id="count">0</b></p>""",
            content_type="text/html",
        )
        response.set_cookie("fixture_login", login_cookie, httponly=True)
        return response

    async def completion(request):
        """Drive the real Agent through two deterministic browser tools.

        Args:
            request: Local OpenAI-compatible completion request.

        Returns:
            One browser action or the explicit completed reply.
        """
        body = await request.json()
        model_requests.append(body)
        messages = body["messages"]
        start = max(i for i, item in enumerate(messages) if item["role"] == "user")
        results = [item for item in messages[start:] if item["role"] == "tool"]
        slow = "H01_SLOW" in str(messages[start])
        plan = [
            ("browser_navigate", {"url": fixture_url + ("/slow" if slow else "/")}),
            ("browser_get_state", {}),
        ]
        if not body.get("tools"):
            message = {"role": "assistant", "content": "H01 fixture"}
            finish = "stop"
        elif len(results) < len(plan):
            name, arguments = plan[len(results)]
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"h01_{len(results)}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
            finish = "tool_calls"
        else:
            message = {
                "role": "assistant",
                "content": "H01_SLOW_DONE" if slow else "H01_DONE",
            }
            finish = "stop"
        payload = {
            "id": "h01-fixture",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "b06-fixture",
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }
        if body.get("stream"):
            delta = {key: value for key, value in message.items() if key != "role"}
            if "tool_calls" in delta:
                delta["tool_calls"][0]["index"] = 0
            payload.update(
                object="chat.completion.chunk",
                choices=[{"index": 0, "delta": delta, "finish_reason": finish}],
            )
            return web.Response(
                text=f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n",
                content_type="text/event-stream",
            )
        return web.json_response(payload)

    fixture = web.Application()
    fixture.router.add_post("/v1/chat/completions", completion)
    fixture.router.add_get(
        "/v1/models",
        lambda _: web.json_response(
            {"object": "list", "data": [{"id": "b06-fixture", "object": "model"}]}
        ),
    )
    fixture.router.add_get("/{path:.*}", website)
    runner = web.AppRunner(fixture, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    fixture_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    password = "H01" + secrets.token_urlsafe(20) + "9"
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 600000
    ).hex()
    config = {
        "dashboard": {
            "host": "127.0.0.1",
            "port": port,
            "username": "b06",
            "pbkdf2_password": f"pbkdf2_sha256$600000${salt}${digest}",
            "password_storage_upgraded": True,
            "password_change_required": False,
        },
        "provider": [
            {
                "id": "b06",
                "type": "openai_chat_completion",
                "provider_type": "chat_completion",
                "enable": True,
                "key": ["local-fixture"],
                "api_base": fixture_url + "/v1",
                "model": "b06-fixture",
            }
        ],
        "agent_runner": {
            "runner_type": "local",
            "config": {"model": {"provider_id": "b06"}},
        },
    }
    (data / "cmd_config.json").write_text(json.dumps(config), encoding="utf-8")
    (data / "config").mkdir(exist_ok=True)
    (data / "config/astrbot_plugin_browser_use_config.json").write_text(
        json.dumps(
            {
                "allowed_domains": ["http://127.0.0.1"],
                "python_path": str(
                    args.private_runtime.resolve() / "python/python.exe"
                ),
                "browser_path": str(chromium),
            }
        ),
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "ASTRBOT_ROOT": str(runtime_root),
        "PYTHONPATH": str(ROOT),
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    guard = evidence / "network-guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(
        "import socket, ipaddress\n"
        "_connect = socket.socket.connect\n"
        "_resolve = socket.getaddrinfo\n"
        "def local(host):\n"
        '    try: return host == "localhost" or ipaddress.ip_address(host).is_loopback\n'
        "    except ValueError: return False\n"
        "def connect(self, address):\n"
        '    if isinstance(address, tuple) and not local(address[0]): raise OSError("Acceptance test blocks external sockets")\n'
        "    return _connect(self, address)\n"
        "def resolve(host, *args, **kwargs):\n"
        '    if not local(host): raise OSError("Acceptance test blocks external DNS")\n'
        "    return _resolve(host, *args, **kwargs)\n"
        "socket.socket.connect = connect\n"
        "socket.getaddrinfo = resolve\n",
        encoding="utf-8",
    )
    environment["PYTHONPATH"] = os.pathsep.join((str(guard), str(ROOT)))

    async def open_browser_panel(view):
        """Open the unchanged production Files & Browser navigation.

        Args:
            view: Authenticated production ChatUI page.
        """
        if await view.locator(".agent-browser-image img").is_visible():
            return
        await view.keyboard.press("Escape")
        try:
            tab = view.locator(".git-diff-sidebar-browser-tab")
            if not await tab.is_visible():
                await view.locator("button.sp-ghost-btn").click()
            await tab.get_by_role("tab").click()
            await view.locator(".browser-mode-trigger").click()
            await view.locator(".browser-mode-agent").click()
            await view.locator(".agent-browser-image img").wait_for(timeout=30000)
        except Exception:
            await view.screenshot(path=str(evidence / "navigation-failure.png"))
            raise

    process = None
    browser = None
    playwright = None
    page = None
    auth_headers = {}
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
                cwd=runtime_root,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            async with aiohttp.ClientSession() as client:
                for _ in range(180):
                    if process.poll() is not None:
                        raise RuntimeError(
                            f"Host exited with {process.returncode}; inspect host.log"
                        )
                    try:
                        async with client.get(
                            origin + "/api/v1/auth/setup"
                        ) as response:
                            if response.status < 500:
                                break
                    except aiohttp.ClientError:
                        pass
                    await asyncio.sleep(1)
                else:
                    raise RuntimeError(
                        "Host startup exceeded 180 seconds; inspect host.log"
                    )
                playwright = await async_playwright().start()
                browser = await playwright.chromium.launch(
                    executable_path=str(chromium), headless=True
                )
                context = await browser.new_context(
                    viewport={"width": 1600, "height": 1000}
                )
                page = await context.new_page()
                page.on(
                    "pageerror",
                    lambda error: print(f"PAGE_ERROR: {error}", flush=True),
                )
                await page.goto(origin + "/#/auth/login")
                await page.locator('input[name="username"]').fill("b06")
                await page.locator('input[name="password"]').fill(password)
                await page.locator('button[type="submit"]').click()
                await page.wait_for_function("!!localStorage.getItem('token')")
                await page.wait_for_function(
                    "!location.hash.includes('/auth/')", timeout=60000
                )
                token = await page.evaluate("localStorage.getItem('token')")
                auth_headers = {"Authorization": f"Bearer {token}"}
                metrics["checks"].append("real Dashboard login")
                async with client.get(
                    origin + "/api/v1/chat/sessions/new", headers=auth_headers
                ) as response:
                    chat_id = (await response.json())["data"]["session_id"]
                await page.goto(origin + f"/#/chat/{chat_id}")
                await page.locator(".chat-text-input, .chat-textarea").wait_for(
                    timeout=60000
                )
                await page.locator(".session-item.active").wait_for(timeout=60000)
                popup = page.get_by_role("button", name="关闭", exact=True)
                if await popup.count():
                    await popup.first.click()
                await page.locator(".chat-text-input, .chat-textarea").fill("H01_RUN")
                await page.locator(".send-btn").click()
                await page.get_by_text("H01_DONE", exact=False).first.wait_for(
                    timeout=120000
                )
                await asyncio.sleep(1)
                await open_browser_panel(page)
                image = page.locator(".agent-browser-image img")
                await image.wait_for(timeout=30000)
                await page.wait_for_function(
                    "document.querySelector('.agent-browser-image img')?.naturalWidth > 0"
                )
                base = origin + "/api/v1/plugins/extensions/browser-use"

                async def status():
                    """Read the authenticated actual browser state.

                    Returns:
                        Current runtime identity and metadata.
                    """
                    async with client.get(
                        base + "/status",
                        params={"chat_id": chat_id},
                        headers=auth_headers,
                    ) as response:
                        value = await response.json()
                        assert response.status == 200, value
                        return value["data"]

                state = await status()
                identity = {
                    key: state[key] for key in ("generation", "browser_session_id")
                }
                ticket_body = {"chat_id": chat_id, **identity}
                ticket_url = origin + "/api/v1/plugins/browser-control/session"
                for patch, headers, expected in [
                    ({}, {}, 401),
                    ({"chat_id": "missing"}, auth_headers, 403),
                    ({"generation": "old"}, auth_headers, 409),
                    ({"owner": "forged"}, auth_headers, 422),
                ]:
                    async with client.post(
                        ticket_url, headers=headers, json={**ticket_body, **patch}
                    ) as response:
                        assert response.status == expected, (response.status, expected)
                metrics["checks"].append(
                    "unauthenticated, wrong chat, old generation and forged owner rejected"
                )
                bounds = await image.bounding_box()
                position = {
                    "x": bounds["width"] * 160 / 1280,
                    "y": bounds["height"] * 100 / 720,
                }
                await page.keyboard.press("Escape")
                await image.click(position=position)
                await asyncio.sleep(0.3)
                assert counter == 0
                async with page.expect_response(
                    lambda r: (
                        "/browser-control/session" in r.url
                        and r.request.method == "POST"
                    )
                ) as issued:
                    await page.locator(".agent-browser-takeover").click()
                ui_ticket = (await (await issued.value).json())["ticket"]
                await page.locator(
                    '.agent-browser-view[data-human-control="true"]'
                ).wait_for(timeout=20000)
                await page.screenshot(path=str(evidence / "manual-zh.png"))
                before = sum(bool(item.get("tools")) for item in model_requests)
                await page.locator(".chat-text-input, .chat-textarea").fill(
                    "H01_WHILE_HUMAN"
                )
                await page.locator(".send-btn").click()
                await page.get_by_text(
                    "Browser is under human control or paused.", exact=False
                ).first.wait_for(timeout=15000)
                assert sum(bool(item.get("tools")) for item in model_requests) == before
                await page.keyboard.press("Escape")
                await image.click(position=position)
                for _ in range(30):
                    if counter == 1:
                        break
                    await asyncio.sleep(0.1)
                assert counter == 1, counter
                for _ in range(60):
                    encoded = (await image.get_attribute("src")).split(",", 1)[1]
                    with Image.open(io.BytesIO(base64.b64decode(encoded))) as pixels:
                        red, green, blue = pixels.convert("RGB").getpixel((80, 80))
                    if 160 < red < 210 and green > 220 and 180 < blue < 225:
                        break
                    await asyncio.sleep(0.1)
                else:
                    async with client.get(
                        base + "/snapshot", params=ticket_body, headers=auth_headers
                    ) as response:
                        diagnostic = (await response.json())["data"]
                        (evidence / "fresh-snapshot.jpg").write_bytes(
                            base64.b64decode(diagnostic["image"])
                        )
                    raise AssertionError(
                        "Live displayed frame did not show the clicked page update."
                    )
                metrics["checks"].append(
                    "live displayed JPEG confirms actual click feedback"
                )
                await page.screenshot(path=str(evidence / "manual-click-zh.png"))
                await page.locator(".agent-browser-takeover").click()
                await page.locator(
                    '.agent-browser-view[data-human-control="false"]'
                ).wait_for()
                for _ in range(30):
                    restored = await status()
                    if restored["control_mode"] == "agent":
                        break
                    await asyncio.sleep(0.1)
                assert restored["control_mode"] == "agent"
                assert all(restored[key] == value for key, value in identity.items())
                assert all(
                    restored[key] == state[key] for key in ("tab_id", "document_id")
                )
                metrics["checks"].extend(
                    [
                        "production ChatUI read-only before confirmed takeover",
                        "actual Chromium counter clicked",
                        "new Agent turn explicitly rejected before model",
                        "explicit UI handback preserves runtime, browser and login cookie",
                    ]
                )
                async with client.ws_connect(
                    origin + "/api/v1/plugins/browser-control/ws"
                ) as control_ws:
                    await control_ws.send_json({"ticket": ui_ticket})
                    assert (await control_ws.receive_json())["status"] == "rejected"
                async with client.post(
                    ticket_url, headers=auth_headers, json=ticket_body
                ) as response:
                    issued = await response.json()
                async with client.ws_connect(
                    origin + issued["websocket_path"]
                ) as control_ws:
                    await control_ws.send_json({"ticket": issued["ticket"]})
                    ack = await control_ws.receive_json()
                    assert ack["status"] == "applied", ack
                    epoch = ack["control_epoch"]
                    target = {
                        key: ack["state"][key]
                        for key in (
                            "tab_id",
                            "document_id",
                            "viewport_width",
                            "viewport_height",
                            "scroll_x",
                            "scroll_y",
                        )
                    }
                    command = {
                        "operation": "click",
                        "control_epoch": epoch,
                        "input_id": 1,
                        "x": 160,
                        "y": 100,
                        "target": target,
                    }
                    for _ in range(2):
                        await control_ws.send_json(command)
                        assert (await control_ws.receive_json())["status"] == "applied"
                    await asyncio.sleep(0.3)
                    assert counter == 2, counter
                    await control_ws.send_json(
                        {**command, "input_id": 2, "control_epoch": epoch - 1}
                    )
                    assert (await control_ws.receive_json())["status"] == "rejected"
                for _ in range(30):
                    if (await status())["control_mode"] == "paused":
                        break
                    await asyncio.sleep(0.1)
                assert (await status())["control_mode"] == "paused"
                metrics["checks"].extend(
                    [
                        "single-use ticket replay rejected",
                        "duplicate numbered input clicks once through real host",
                        "old control epoch rejected",
                        "disconnect leaves paused admission",
                        "authenticated website cookie preserved",
                    ]
                )
                await page.evaluate("localStorage.setItem('astrbot-locale', 'en-US')")
                await page.reload()
                await page.locator(".chat-text-input, .chat-textarea").wait_for(
                    timeout=30000
                )
                await open_browser_panel(page)
                await page.locator(".agent-browser-image img").wait_for(timeout=20000)
                await page.locator(".agent-browser-takeover").click()
                await page.locator(
                    '.agent-browser-view[data-human-control="true"]'
                ).wait_for(timeout=20000)
                await page.screenshot(path=str(evidence / "manual-en.png"))
                await page.locator(".agent-browser-takeover").click()
                for _ in range(40):
                    if (await status())["control_mode"] == "agent":
                        break
                    await asyncio.sleep(0.1)
                await page.locator(".chat-text-input, .chat-textarea").fill("H01_SLOW")
                await page.locator(".send-btn").click()
                await asyncio.wait_for(slow_entered.wait(), 30)
                async with client.post(
                    ticket_url, headers=auth_headers, json=ticket_body
                ) as response:
                    active_ticket = await response.json()
                    assert response.status == 200, active_ticket
                async with client.ws_connect(
                    origin + active_ticket["websocket_path"]
                ) as control_ws:
                    await control_ws.send_json({"ticket": active_ticket["ticket"]})
                    rejected = await control_ws.receive_json()
                    assert rejected["status"] == "rejected", rejected
                    assert "running" in rejected["message"], rejected
                await page.get_by_text("H01_SLOW_DONE", exact=False).first.wait_for(
                    timeout=60000
                )
                metrics["checks"].append(
                    "running real Agent explicitly refuses takeover"
                )
                async with client.post(
                    origin + "/api/v1/accounts",
                    headers=auth_headers,
                    json={"username": "h01-user", "password": ordinary_password},
                ) as response:
                    assert response.status == 200, await response.text()
                ordinary_context = await browser.new_context(
                    viewport={"width": 1600, "height": 1000}
                )
                ordinary_page = await ordinary_context.new_page()
                await ordinary_page.goto(origin + "/#/auth/login")
                await ordinary_page.locator('input[name="username"]').fill("h01-user")
                await ordinary_page.locator('input[name="password"]').fill(
                    ordinary_password
                )
                await ordinary_page.locator('button[type="submit"]').click()
                await ordinary_page.wait_for_function("!!localStorage.getItem('token')")
                await ordinary_page.wait_for_function(
                    "!location.hash.includes('/auth/')", timeout=60000
                )
                ordinary_token = await ordinary_page.evaluate(
                    "localStorage.getItem('token')"
                )
                ordinary_auth = {"Authorization": f"Bearer {ordinary_token}"}
                async with client.get(
                    origin + "/api/v1/chat/sessions/new", headers=ordinary_auth
                ) as response:
                    ordinary_chat = (await response.json())["data"]["session_id"]
                    assert response.status == 200
                for owner_headers, body in [
                    (ordinary_auth, ticket_body),
                    (auth_headers, {**ticket_body, "chat_id": ordinary_chat}),
                ]:
                    async with client.post(
                        ticket_url, headers=owner_headers, json=body
                    ) as response:
                        assert response.status == 403, await response.text()
                async with client.post(
                    base + "/close", headers=auth_headers, params=ticket_body
                ) as response:
                    assert response.status == 200, await response.text()
                async with client.post(
                    ticket_url, headers=auth_headers, json=ticket_body
                ) as response:
                    assert response.status == 409, response.status
                metrics["checks"].append(
                    "closed browser identity cannot obtain a new control ticket"
                )
                await ordinary_page.goto(origin + f"/#/chat/{ordinary_chat}")
                await ordinary_page.locator(
                    ".chat-text-input, .chat-textarea"
                ).wait_for(timeout=30000)
                await asyncio.sleep(1)
                await ordinary_page.screenshot(
                    path=str(evidence / "ordinary-before-fill.png")
                )
                await ordinary_page.locator(".chat-text-input, .chat-textarea").fill(
                    "H01_RUN_ORDINARY"
                )
                await ordinary_page.screenshot(
                    path=str(evidence / "ordinary-ready.png")
                )
                await ordinary_page.locator(".send-btn").click()
                await ordinary_page.get_by_text("H01_DONE", exact=False).first.wait_for(
                    timeout=120000
                )
                await open_browser_panel(ordinary_page)
                await ordinary_page.locator(".agent-browser-image img").wait_for(
                    timeout=30000
                )
                await ordinary_page.locator(".agent-browser-takeover").click()
                await ordinary_page.locator(
                    '.agent-browser-view[data-human-control="true"]'
                ).wait_for(timeout=20000)
                await ordinary_page.locator(".agent-browser-takeover").click()
                await ordinary_page.locator(
                    '.agent-browser-view[data-human-control="false"]'
                ).wait_for(timeout=15000)
                metrics["checks"].append(
                    "real ordinary-account login and takeover; both roles reject another account's chat"
                )
                async with client.patch(
                    origin + "/api/v1/plugins/astrbot_plugin_browser_use/enabled",
                    headers=auth_headers,
                    json={"enabled": False},
                ) as response:
                    assert response.status == 200, await response.text()
                async with client.post(
                    ticket_url, headers=auth_headers, json=ticket_body
                ) as response:
                    assert response.status in {403, 409, 503}, response.status
                metrics["checks"].append("disabled plugin refuses control")
                assert cookies == [login_cookie, login_cookie]
                metrics.update(
                    passed=True,
                    browser_identity={
                        **identity,
                        "tab_id": state["tab_id"],
                        "document_id": state["document_id"],
                    },
                    clicks=counter,
                    model_requests=len(model_requests),
                )
                print(json.dumps(metrics, ensure_ascii=False), flush=True)
    finally:
        if page is not None and not metrics["passed"]:
            with contextlib.suppress(Exception):
                await page.screenshot(path=str(evidence / "failure.png"))
        (evidence / "result.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (evidence / "model-requests.json").write_text(
            json.dumps(model_requests, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()
        if process and process.poll() is None:
            # Disable the real plugin before stopping the isolated host process.
            with contextlib.suppress(Exception):
                async with aiohttp.ClientSession() as client:
                    async with client.patch(
                        origin + "/api/v1/plugins/astrbot_plugin_browser_use/enabled",
                        headers=auth_headers,
                        json={"enabled": False},
                    ) as response:
                        await response.read()
            process.terminate()
            await asyncio.to_thread(process.wait, 15)
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
