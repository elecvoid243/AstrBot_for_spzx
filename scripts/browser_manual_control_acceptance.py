"""Verify H01/H02 through real AstrBot login, production ChatUI and private Chromium.

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
    parser.add_argument(
        "--handoff-boundaries",
        action="store_true",
        help="Use a one-round budget and hold the real pre-reset request hook.",
    )
    parser.add_argument(
        "--text-input",
        action="store_true",
        help="Exercise H03 native Chromium text, IME, paste and focus through production ChatUI.",
    )
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
    model_waiting = asyncio.Event()
    model_release = asyncio.Event()
    pre_reset_entered = asyncio.Event()
    pre_reset_release = asyncio.Event()
    post_effects = 0
    form_state = {}
    private_text = "H03-" + secrets.token_urlsafe(12)

    async def website(request):
        """Serve a deterministic authenticated counter page for actual CDP clicks.

        Args:
            request: Loopback website request.

        Returns:
            Counter fixture or its acknowledged click number.
        """
        nonlocal counter, post_effects, form_state
        if request.path == "/form-report":
            assert request.cookies.get("fixture_login") == login_cookie
            form_state = await request.json()
            return web.Response(text="ok")
        if request.path == "/pre-reset":
            prepared = await request.json()
            assert prepared["prompt"] == "H02_PRE_RESET" and prepared["tools_prepared"]
            metrics["pre_reset_hook"] = prepared
            pre_reset_entered.set()
            await pre_reset_release.wait()
            return web.Response(text="Continue normal image preparation and reset")
        if request.path == "/increment":
            cookies.append(request.cookies.get("fixture_login"))
            assert cookies[-1] == login_cookie
            counter += 1
            return web.Response(text=str(counter))
        if request.path == "/continue":
            assert request.cookies.get("fixture_login") == login_cookie
            post_effects += 1
            return web.Response(text="Continued after fresh observation")
        if request.path == "/slow":
            slow_entered.set()
            await asyncio.sleep(3)
        response = web.Response(
            text="""<!doctype html><meta charset="utf-8"><title>H01 authenticated counter</title>
<style>body{margin:0;font:24px Arial;background:#eff6ff}button{position:absolute;left:60px;top:60px;width:200px;height:80px;font-size:24px}p{position:absolute;left:60px;top:180px}</style>
<button id="increment" onclick="fetch('/increment').then(r=>r.text()).then(n=>{document.getElementById('count').textContent=n;this.style.backgroundColor='#b9f6ca';this.textContent='Human updated '+n})">Click counter</button>
<button style="top:300px;width:260px" onclick="fetch('/continue').then(r=>r.text()).then(n=>this.textContent=n)">Continue on fresh page</button>
<form style="position:absolute;left:380px;top:60px;width:450px" onsubmit="event.preventDefault()">
<input id="text" aria-label="English and Chinese" style="width:400px;height:50px;font-size:24px" autocomplete="off">
<textarea id="multiline" aria-label="Multiline" style="display:block;margin-top:20px;width:400px;height:80px;font-size:24px"></textarea>
<input id="password" aria-label="Test password" type="password" autocomplete="off" style="display:block;margin-top:20px;width:400px;height:50px">
</form>
<script>
const held = new Set();
const report = () => fetch('/form-report', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:document.getElementById('text').value,multiline:document.getElementById('multiline').value,password:document.getElementById('password').value,held:[...held],active:document.activeElement.id})});
addEventListener('keydown',e=>{held.add(e.code);report()});
addEventListener('keyup',e=>{held.delete(e.code);report()});
addEventListener('input',report);
addEventListener('focusin',report);
</script>
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
        if args.handoff_boundaries:
            plan = plan[:1]
        handoff = next(
            (
                mode
                for item in reversed(messages)
                for mode in (
                    "H01_RUN",
                    "H01_SLOW",
                    "H02_MODEL",
                    "H02_BATCH",
                    "H02_CANCEL_EDGE",
                    "H02_CANCEL",
                    "H02_PRE_RESET",
                    "H02_BUDGET",
                    "H02_RECOVERY",
                )
                if item["role"] == "user"
                and mode in str(item.get("content"))
                and "SYSTEM NOTICE" not in str(item.get("content"))
            ),
            None,
        )
        if handoff is not None:
            start = max(
                i
                for i, item in enumerate(messages)
                if item["role"] == "user"
                and handoff in str(item.get("content"))
                and "SYSTEM NOTICE" not in str(item.get("content"))
            )
            results = [item for item in messages[start:] if item["role"] == "tool"]
        if args.handoff_boundaries and "after cancellation" in str(messages[start]):
            assert "Human browser handback" not in str(messages[start:]), (
                "Old handback observation leaked into an unrelated request"
            )
            metrics["future_request_excludes_old_handback"] = True
        handoff = handoff if handoff and handoff.startswith("H02_") else None
        if handoff and body.get("tools"):
            task_start = max(
                i
                for i, item in enumerate(messages)
                if item["role"] == "user"
                and handoff in str(item.get("content"))
                and "SYSTEM NOTICE" not in str(item.get("content"))
            )
            results = [item for item in messages[task_start:] if item["role"] == "tool"]
            returned = [
                item
                for item in messages[task_start:]
                if item["role"] == "user"
                and "Human browser handback" in str(item.get("content"))
            ]
            if (
                args.handoff_boundaries
                and not returned
                and handoff in {"H02_RECOVERY", "H02_CANCEL_EDGE"}
                and len(results) < 3
            ):
                calls = [(f"recovery_{len(results)}", "browser_get_state", {})]
            elif args.handoff_boundaries and not returned:
                assert handoff != "H02_PRE_RESET", (
                    "Completed handback before reset lost its fresh observation"
                )
                model_waiting.set()
                await model_release.wait()
                calls = [
                    (
                        "stale",
                        "browser_click",
                        {"coordinate_x": 160, "coordinate_y": 100},
                    )
                ]
            elif returned:
                assert "Human updated" in str(returned[-1]), returned[-1]
                if handoff in {"H02_RECOVERY", "H02_CANCEL_EDGE"} or any(
                    item.get("tool_call_id") == "h02_continue" for item in results
                ):
                    calls = []
                else:
                    assert "Human updated" in str(returned[-1]), returned[-1]
                    calls = [
                        (
                            "continue",
                            "browser_click",
                            {"coordinate_x": 160, "coordinate_y": 340},
                        )
                    ]
            elif not results:
                calls = [("open", "browser_navigate", {"url": fixture_url + "/"})]
            elif handoff == "H02_BATCH":
                calls = [
                    ("slow", "browser_navigate", {"url": fixture_url + "/slow"}),
                    (
                        "queued",
                        "browser_click",
                        {"coordinate_x": 160, "coordinate_y": 100},
                    ),
                ]
            else:
                model_waiting.set()
                await model_release.wait()
                calls = [
                    (
                        "stale",
                        "browser_click",
                        {"coordinate_x": 160, "coordinate_y": 100},
                    )
                ]
            message = {
                "role": "assistant",
                "content": None if calls else handoff + "_DONE",
                **(
                    {
                        "tool_calls": [
                            {
                                "id": "h02_" + call_id,
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": json.dumps(arguments),
                                },
                            }
                            for call_id, name, arguments in calls
                        ]
                    }
                    if calls
                    else {}
                ),
            }
            finish = "tool_calls" if calls else "stop"
        elif not body.get("tools"):
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
                for index, call in enumerate(delta["tool_calls"]):
                    call["index"] = index
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
    fixture.router.add_post("/pre-reset", website)
    fixture.router.add_post("/form-report", website)
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
            "config": {
                "model": {"provider_id": "b06"},
                "misc": {"max_steps": 1 if args.handoff_boundaries else 128},
            },
        },
    }
    (data / "cmd_config.json").write_text(json.dumps(config), encoding="utf-8")
    if args.handoff_boundaries:
        hook_plugin = data / "plugins/astrbot_plugin_h02_fixture"
        hook_plugin.mkdir()
        (hook_plugin / "metadata.yaml").write_text(
            "name: astrbot_plugin_h02_fixture\ndesc: Isolated pre-reset handoff acceptance\nversion: v0.0.0\nauthor: acceptance\n",
            encoding="utf-8",
        )
        (hook_plugin / "main.py").write_text(
            "import aiohttp\n"
            "from astrbot.api.event import AstrMessageEvent, filter\n"
            "from astrbot.api.provider import ProviderRequest\n"
            "from astrbot.api.star import Star\n\n"
            "class H02Fixture(Star):\n"
            '    """Hold a real post-build, pre-reset lifecycle hook."""\n'
            "    @filter.on_llm_request()\n"
            "    async def hold_pre_reset(self, event: AstrMessageEvent, req: ProviderRequest):\n"
            '        """Wait for explicit handback before normal request preparation.\n\n'
            "        Args:\n"
            "            event: Trusted production platform event.\n"
            "            req: Provider request constructed by build_main_agent.\n"
            '        """\n'
            '        if req.prompt != "H02_PRE_RESET":\n'
            "            return\n"
            "        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as client:\n"
            f'            async with client.post({fixture_url + "/pre-reset"!r}, json={{"prompt": req.prompt, "tools_prepared": bool(req.func_tool), "phase": "OnLLMRequestEvent after build_main_agent, before reset"}}) as response:\n'
            "                response.raise_for_status()\n",
            encoding="utf-8",
        )
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
                await page.locator(".send-btn[aria-label]").click()
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
                await page.locator(".send-btn[aria-label]").click()
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
                if args.text_input:

                    async def form_matches(**expected):
                        """Wait for the actual private page's event report.

                        Args:
                            **expected: Required field values, held keys or focus.
                        """
                        for _ in range(300):
                            if all(form_state.get(k) == v for k, v in expected.items()):
                                return
                            await asyncio.sleep(0.1)
                        raise AssertionError(
                            "Private form did not reach the expected input/focus state"
                        )

                    async def click_form(x, y):
                        """Focus a private form field via the displayed production image.

                        Args:
                            x: Remote CSS horizontal coordinate.
                            y: Remote CSS vertical coordinate.
                        """
                        box = await image.bounding_box()
                        await image.click(
                            position={
                                "x": box["width"] * x / 1280,
                                "y": box["height"] * y / 720,
                            }
                        )

                    await click_form(500, 85)
                    await form_matches(active="text")
                    await page.keyboard.type("English!")
                    await page.keyboard.insert_text(" 中文，🙂")
                    await form_matches(text="English! 中文，🙂")
                    metrics["checks"].append(
                        "H03: queued English, Chinese punctuation and emoji arrive once in real private form"
                    )
                    await page.keyboard.press("Control+a")
                    await page.keyboard.insert_text("replacement")
                    await form_matches(text="replacement")
                    await page.keyboard.press("Backspace")
                    await form_matches(text="replacemen")
                    await page.keyboard.press("Shift+Home")
                    await page.keyboard.insert_text("selected")
                    await form_matches(text="selected")
                    metrics["checks"].append(
                        "H03: Ctrl+A, replacement, Backspace and Shift+Home edit actual selection"
                    )
                    # Native Chromium composition events, rather than synthetic DOM events.
                    ime = await context.new_cdp_session(page)
                    await ime.send(
                        "Input.imeSetComposition",
                        {"text": "中文候选", "selectionStart": 4, "selectionEnd": 4},
                    )
                    await asyncio.sleep(0.3)
                    assert form_state["text"] == "selected"
                    await ime.send("Input.insertText", {"text": "中文"})
                    await form_matches(text="selected中文")
                    await ime.send(
                        "Input.imeSetComposition",
                        {"text": "取消", "selectionStart": 2, "selectionEnd": 2},
                    )
                    await ime.send(
                        "Input.imeSetComposition",
                        {"text": "", "selectionStart": 0, "selectionEnd": 0},
                    )
                    await page.keyboard.insert_text("!")
                    await form_matches(text="selected中文!")
                    metrics["checks"].append(
                        "H03: native Chromium IME preedit stays local, commit once, cancellation inserts nothing (not physical Windows IME)"
                    )
                    await context.grant_permissions(
                        ["clipboard-read", "clipboard-write"]
                    )
                    await page.evaluate(
                        "navigator.clipboard.writeText('explicit paste 粘贴🙂')"
                    )
                    await page.keyboard.press("Control+v")
                    await form_matches(text="selected中文!explicit paste 粘贴🙂")
                    metrics["checks"].append(
                        "H03: user-triggered trusted paste transfers plain text through production host"
                    )
                    unchanged = form_state["text"]
                    chat_input = page.locator(".chat-text-input, .chat-textarea")
                    await chat_input.fill("local chat")
                    await page.keyboard.type(" stays local")
                    await form_matches(held=[])
                    assert await chat_input.input_value() == "local chat stays local"
                    assert form_state["text"] == unchanged
                    await chat_input.fill("")
                    await page.locator(".agent-browser-fullscreen").click()
                    await click_form(500, 85)
                    await page.keyboard.press("Tab")
                    await form_matches(active="multiline")
                    await page.keyboard.type("line1")
                    await page.keyboard.press("Enter")
                    await page.keyboard.type("line2")
                    await form_matches(multiline="line1\nline2")
                    await page.keyboard.down("Backspace")
                    await page.keyboard.down("Backspace")
                    await page.keyboard.up("Backspace")
                    await form_matches(multiline="line1\nlin")
                    await page.keyboard.insert_text("e2")
                    metrics["checks"].append(
                        "H03: repeated keydown applies repeat deletion without duplicated text"
                    )
                    await page.keyboard.press("Escape")
                    await form_matches(multiline="line1\nline2", held=[])
                    assert (
                        await page.locator(".agent-browser-stage.is-fullscreen").count()
                        == 1
                    )
                    await page.keyboard.down("Shift")
                    await form_matches(held=["ShiftLeft"])
                    await page.locator(".agent-browser-leave").click()
                    await form_matches(held=[])
                    await page.keyboard.up("Shift")
                    await page.keyboard.press("Escape")
                    assert (
                        await page.locator(".agent-browser-stage.is-fullscreen").count()
                        == 0
                    )
                    metrics["checks"].append(
                        "H03: fullscreen Tab/Enter/Escape reach remote focus; visible leave button releases held Shift; chat typing stays local"
                    )
                    await click_form(500, 285)
                    await form_matches(active="password")
                    await page.keyboard.insert_text(private_text)
                    await form_matches(password=private_text)
                    await page.keyboard.down("Control")
                    await form_matches(held=["ControlLeft"])
                    await page.screenshot(path=str(evidence / "keyboard-zh.png"))
                    await page.keyboard.up("Control")
                    await form_matches(held=[])
                    metrics["checks"].append(
                        "H03: isolated password arrives without action-summary content"
                    )
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
                if args.text_input:
                    await page.get_by_text(
                        "已交还控制权，浏览器及登录态保留。", exact=True
                    ).wait_for()
                    metrics["checks"].append(
                        "H03: successful handback keeps the truthful UI confirmation"
                    )
                if args.text_input:
                    await form_matches(
                        text=unchanged,
                        multiline="line1\nline2",
                        password=private_text,
                        held=[],
                    )
                    metrics["checks"].append(
                        "H03: handback retains real form values, password and keyboard release"
                    )
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
                    expired = {**command, "input_id": 2, "control_epoch": epoch - 1}
                    if args.text_input:
                        expired.update(
                            operation="text", text="must not arrive", source="typing"
                        )
                        expired.pop("x")
                        expired.pop("y")
                    await control_ws.send_json(expired)
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
                if args.text_input:
                    async with client.post(
                        ticket_url, headers=auth_headers, json=ticket_body
                    ) as response:
                        issued = await response.json()
                    async with client.ws_connect(
                        origin + issued["websocket_path"]
                    ) as control_ws:
                        await control_ws.send_json({"ticket": issued["ticket"]})
                        ack = await control_ws.receive_json()
                        assert ack["status"] == "applied"
                        target = {k: ack["state"][k] for k in target}
                        await control_ws.send_json(
                            {
                                "operation": "key",
                                "control_epoch": ack["control_epoch"],
                                "input_id": 1,
                                "phase": "down",
                                "key": "Shift",
                                "code": "ShiftLeft",
                                "modifiers": 8,
                                "target": target,
                            }
                        )
                        assert (await control_ws.receive_json())["status"] == "applied"
                        await form_matches(held=["ShiftLeft"])
                        await control_ws.send_json(
                            {
                                "operation": "text",
                                "source": "paste",
                                "text": "must not arrive",
                                "control_epoch": ack["control_epoch"],
                                "input_id": 2,
                                "target": {**target, "document_id": "expired-document"},
                            }
                        )
                        assert (await control_ws.receive_json())["status"] == "rejected"
                    await form_matches(held=[], text=unchanged, password=private_text)
                    metrics["checks"].append(
                        "H03: actual host rejects stale text target and releases held key without inserting into the private form"
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
                scenarios = (
                    ("H02_PRE_RESET", "H02_BUDGET", "H02_RECOVERY", "H02_CANCEL_EDGE")
                    if args.handoff_boundaries
                    else ("H02_MODEL", "H02_BATCH", "H02_CANCEL")
                )
                for scenario in scenarios:
                    locale = "zh-CN" if scenario == "H02_MODEL" else "en-US"
                    await page.evaluate(
                        "locale => localStorage.setItem('astrbot-locale', locale)",
                        locale,
                    )
                    await page.reload()
                    await page.locator(".chat-text-input, .chat-textarea").wait_for(
                        timeout=30000
                    )
                    await open_browser_panel(page)
                    model_waiting.clear()
                    model_release.clear()
                    slow_entered.clear()
                    pre_reset_entered.clear()
                    pre_reset_release.clear()
                    baseline_count = counter
                    baseline_effects = post_effects
                    await page.locator(".chat-text-input, .chat-textarea").fill(
                        scenario
                    )
                    await page.locator(".send-btn[aria-label]").click()
                    await asyncio.wait_for(
                        (
                            pre_reset_entered
                            if scenario == "H02_PRE_RESET"
                            else slow_entered
                            if scenario == "H02_BATCH"
                            else model_waiting
                        ).wait(),
                        45,
                    )
                    await page.locator(".agent-browser-takeover").click()
                    await page.locator(
                        '.agent-browser-view[data-human-control="true"]'
                    ).wait_for(timeout=30000)
                    current = await status()
                    assert current["control_mode"] == "human"
                    assert current["current_action"] is None
                    assert (
                        current["browser_session_id"] == identity["browser_session_id"]
                    )
                    while_human = len(model_requests)
                    actions = current["action_cursor"]
                    # Allow the already-issued fixture response to arrive late.
                    model_release.set()
                    await asyncio.sleep(1)
                    assert counter == baseline_count
                    assert len(model_requests) == while_human
                    assert (await status())["action_cursor"] == actions
                    metrics["checks"].append(
                        scenario
                        + ": no late model action or remaining batch call during human control"
                    )
                    if scenario in {"H02_CANCEL", "H02_CANCEL_EDGE"}:
                        await page.locator(".chat-text-input, .chat-textarea").fill("")
                        await (
                            page.locator(".send-btn")
                            .filter(has=page.locator(".mdi-stop"))
                            .click()
                        )
                        await asyncio.sleep(1)
                    else:
                        if scenario == "H02_MODEL":
                            await page.locator(".chat-text-input, .chat-textarea").fill(
                                "Do not resume while I control the page"
                            )
                            await page.locator(".send-btn[aria-label]").click()
                            await page.locator(
                                ".chat-text-input, .chat-textarea"
                            ).press("Enter")
                            await asyncio.sleep(1)
                            assert len(model_requests) == while_human
                            metrics["checks"].append(
                                "same-chat follow-up does not resume the paused model"
                            )
                        bounds = await image.bounding_box()
                        await image.click(
                            position={
                                "x": bounds["width"] * 160 / 1280,
                                "y": bounds["height"] * 100 / 720,
                            }
                        )
                        for _ in range(40):
                            if counter == baseline_count + 1:
                                break
                            await asyncio.sleep(0.1)
                        assert counter == baseline_count + 1, counter
                        await asyncio.sleep(0.5)
                        await page.screenshot(
                            path=str(evidence / (scenario.lower() + "-human.png"))
                        )
                    await page.locator(".agent-browser-takeover").click()
                    await page.locator(
                        '.agent-browser-view[data-human-control="false"]'
                    ).wait_for(timeout=20000)
                    if scenario == "H02_PRE_RESET":
                        for _ in range(150):
                            if (await status())["control_mode"] == "agent":
                                break
                            await asyncio.sleep(0.1)
                        assert len(model_requests) == while_human
                        assert post_effects == baseline_effects
                        assert (await status())["control_mode"] == "agent"
                        metrics["checks"].append(
                            "acquire and release completed inside the real OnLLMRequestEvent before reset or first model request"
                        )
                        pre_reset_release.set()
                    if scenario in {"H02_CANCEL", "H02_CANCEL_EDGE"}:
                        await asyncio.sleep(1)
                        assert len(model_requests) == while_human
                        assert (
                            counter == baseline_count
                            and post_effects == baseline_effects
                        )
                        metrics["checks"].append(
                            "cancel while paused remains terminal after explicit handback"
                        )
                    else:
                        await page.get_by_text(
                            scenario + "_DONE", exact=False
                        ).first.wait_for(timeout=45000)
                        assert post_effects == baseline_effects + (
                            scenario != "H02_RECOVERY"
                        )
                        assert counter == baseline_count + 1
                        if args.handoff_boundaries:
                            latest = next(
                                item
                                for item in reversed(model_requests)
                                if item.get("tools")
                            )["messages"]
                            task_start = max(
                                i
                                for i, item in enumerate(latest)
                                if item["role"] == "user"
                                and scenario in str(item.get("content"))
                                and "SYSTEM NOTICE" not in str(item.get("content"))
                            )
                            task_messages = latest[task_start:]
                            assert "Human updated" in str(task_messages)
                            if scenario != "H02_RECOVERY":
                                assert "Permission denied" not in str(task_messages)
                            metrics["checks"].append(
                                scenario
                                + ": production max_steps=1 preserves correct tool budget and completes after fresh observation"
                            )
                        if scenario == "H02_BATCH":
                            paired = next(
                                item
                                for item in reversed(model_requests)
                                if item.get("tools")
                            )["messages"]
                            invalidated = [
                                item
                                for item in paired
                                if item.get("tool_call_id") == "h02_queued"
                            ]
                            assert len(invalidated) == 1 and "Not executed" in str(
                                invalidated[0]
                            ), invalidated
                            metrics["checks"].append(
                                "real parallel batch has a truthful unexecuted result paired with its original call ID"
                            )
                        metrics["checks"].append(
                            scenario
                            + (
                                ": fresh observation completes the last recovery slot"
                                if scenario == "H02_RECOVERY"
                                else ": fresh observed human change drives one new Agent action after explicit handback"
                            )
                        )
                    assert (await status())["control_mode"] == "agent"
                    await page.locator(".send-btn .mdi-stop").wait_for(
                        state="hidden", timeout=15000
                    )
                # A new request after cancellation reuses the retained private browser.
                await page.locator(".chat-text-input, .chat-textarea").fill(
                    "H01_RUN after cancellation"
                )
                await page.locator(".send-btn[aria-label]").click()
                await asyncio.sleep(4)
                assert (await status())["browser_session_id"] == identity[
                    "browser_session_id"
                ]
                metrics["checks"].append(
                    "new request after cancellation reuses the preserved browser"
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
                await ordinary_page.locator(".send-btn[aria-label]").click()
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
                if args.text_input:
                    logs = (evidence / "host.log").read_text(
                        encoding="utf-8", errors="replace"
                    )
                    assert (
                        private_text not in logs and "explicit paste 粘贴🙂" not in logs
                    )
                    assert private_text not in json.dumps(
                        model_requests, ensure_ascii=False
                    )
                    metrics["checks"].append(
                        "H03: password and pasted content absent from host logs and model tool summaries"
                    )
                assert len(cookies) == (5 if args.handoff_boundaries else 4) and all(
                    cookie == login_cookie for cookie in cookies
                )
                metrics.update(
                    passed=True,
                    browser_identity={
                        **identity,
                        "tab_id": state["tab_id"],
                        "document_id": state["document_id"],
                    },
                    clicks=counter,
                    model_requests=len(model_requests),
                    resumed_side_effects=post_effects,
                )
                print(json.dumps(metrics, ensure_ascii=False), flush=True)
    finally:
        model_release.set()
        pre_reset_release.set()
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
