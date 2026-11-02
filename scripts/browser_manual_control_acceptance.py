"""Verify H01/H02 through real AstrBot login, production ChatUI and private Chromium.

Run with a development interpreter containing AstrBot dependencies, aiohttp, psutil and
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
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import aiohttp
import psutil
from aiohttp import web
from PIL import Image
from playwright.async_api import Error as PlaywrightError
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
    parser.add_argument("--plugin-source", type=Path)
    parser.add_argument("--private-runtime", type=Path)
    parser.add_argument("--plugin-zip", type=Path)
    parser.add_argument("--login-handoff", action="store_true")
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
    parser.add_argument(
        "--lost-key-target",
        action="store_true",
        help="Destroy the real held-key CDP target, then stop/close through production ChatUI.",
    )
    parser.add_argument(
        "--pointer-input",
        action="store_true",
        help="Exercise actual wheel/double-click and viewport feedback.",
    )
    parser.add_argument(
        "--recovery",
        action="store_true",
        help="Exercise H05 two-client lifecycle recovery and lost acknowledgements.",
    )
    args = parser.parse_args()
    if args.lost_key_target and not args.text_input:
        parser.error("--lost-key-target requires --text-input")
    if not args.plugin_zip and not (args.plugin_source and args.private_runtime):
        parser.error(
            "Supply --plugin-zip or both --plugin-source and --private-runtime"
        )
    if args.plugin_zip and (args.plugin_source or args.private_runtime):
        parser.error("Final ZIP acceptance cannot use source/runtime overrides")
    evidence = args.evidence_dir.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    runtime_root = evidence / "isolated-host"
    data = runtime_root / "data"
    installed = data / "plugins/astrbot_plugin_browser_use"
    metrics = {"passed": False, "checks": []}
    if args.plugin_zip:
        distribution = evidence / "离线 安装包"
        with zipfile.ZipFile(args.plugin_zip) as archive:
            archive.extractall(distribution)
        (data / "plugins").mkdir(parents=True)
        sentinel = (
            data / "plugin_data/astrbot_plugin_browser_use/existing-user-data.txt"
        )
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text("preserve", encoding="utf-8")
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(distribution / "install.ps1"),
            "-AstrBotDataDir",
            str(data),
        ]
        installed_result = await asyncio.to_thread(
            subprocess.run,
            command,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert installed_result.returncode == 0, installed_result.stderr.decode(
            errors="replace"
        )
        assert (
            await asyncio.to_thread(
                subprocess.run,
                command,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        ).returncode != 0
        moved = evidence / "搬迁后的 AstrBot"
        assert runtime_root.resolve().is_relative_to(evidence)
        assert moved.resolve().is_relative_to(evidence)
        runtime_root.rename(moved)
        runtime_root = moved
        data = runtime_root / "data"
        installed = data / "plugins/astrbot_plugin_browser_use"
        args.private_runtime = installed / "runtime"
        assert (
            data / "plugin_data/astrbot_plugin_browser_use/existing-user-data.txt"
        ).read_text() == "preserve"
        manifest = json.loads((args.private_runtime / "manifest.json").read_text())
        for name, digest in manifest["source_sha256"].items():
            assert (
                hashlib.sha256((installed / name).read_bytes()).hexdigest() == digest
            ), name
        build = json.loads(
            (args.dashboard_dir / "assets/browser-observer-build.json").read_text()
        )
        assert build["astrbot_revision"] == manifest["astrbot_revision"]
        assert build["plugin_revision"] == manifest["plugin_revision"]
        metrics.update(
            plugin_zip_sha256=hashlib.sha256(args.plugin_zip.read_bytes()).hexdigest(),
            manifest=manifest,
            installation="final ZIP; relocated; bundled defaults",
        )
        metrics["checks"].extend(
            [
                "final ZIP installed with shipped PowerShell installer; overwrite refused; persistent data retained",
                "whole isolated data tree relocated through Chinese/space paths; source hashes and matched revisions verified",
            ]
        )
    else:
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
    website_password = "H06-" + secrets.token_urlsafe(18)
    website_login = {"submitted": 0, "continued": 0, "stale": 0}
    website_session = secrets.token_urlsafe(20)

    pointer_state = {}
    recovery_clicks = 0

    async def website(request):
        """Serve a deterministic authenticated counter page for actual CDP clicks.

        Args:
            request: Loopback website request.

        Returns:
            Counter fixture or its acknowledged click number.
        """
        nonlocal counter, post_effects, form_state, pointer_state
        if request.path == "/stale-login-action":
            website_login["stale"] += 1
            return web.Response(text="Stale action must never execute")
        if request.path == "/login-submit":
            form = await request.post()
            assert form["username"] == "Offline 用户"
            assert form["password"] == website_password
            website_login["submitted"] += 1
            response = web.HTTPFound("/authenticated")
            response.set_cookie("h06_session", website_session, httponly=True)
            return response
        if request.path in {"/authenticated", "/login-continue"}:
            assert request.cookies.get("h06_session") == website_session
            if request.path == "/login-continue":
                website_login["continued"] += 1
            return web.Response(
                text="""<!doctype html><meta charset="utf-8"><title>H06 authenticated account</title>
<style>body{font:24px Arial;background:#dcfce7}button{position:absolute;left:60px;top:60px;width:260px;height:80px}</style>
<button onclick="location.href='/login-continue'">Continue signed-in task</button>
<p style="position:absolute;top:200px">H06 authenticated account · Offline 用户</p>""",
                content_type="text/html",
            )
        if request.path == "/login":
            return web.Response(
                text="""<!doctype html><meta charset="utf-8"><title>H06 sign in</title>
<style>body{font:24px Arial;background:#eff6ff}input,button{display:block;width:400px;height:55px;font-size:24px;margin:20px}</style>
<form action="/login-submit" method="post" style="position:absolute;left:60px;top:30px">
<label>English / 中文账号<input name="username" autocomplete="off"></label>
<label>Test password<input name="password" type="password" autocomplete="off"></label>
<button type="submit">Sign in / 登录</button></form>""",
                content_type="text/html",
            )
        if request.path == "/pointer-report":
            assert request.cookies.get("fixture_login") == login_cookie
            pointer_state = await request.json()
            return web.Response(text="ok")
        if request.path == "/pointer":
            return web.Response(
                text="""<!doctype html><meta charset="utf-8"><title>H04 pointer fixture</title>
<style>body{margin:0;font:24px Arial;background:#eff6ff;height:4000px}#nested{position:absolute;left:40px;top:120px;width:500px;height:300px;overflow:auto;border:2px solid #222}#long{width:2000px;height:2500px;background:repeating-linear-gradient(#e9d5ff 0px,#e9d5ff 80px,#bbf7d0 80px,#bbf7d0 160px)}#double{position:absolute;left:600px;top:60px;width:200px;height:80px;background:#fca5a5}a{position:fixed;left:40px;top:20px}</style>
<a href="/">Return to form</a><button id="double">Double-click me</button><div id="nested"><div id="long">Nested long list</div></div><div style="position:absolute;top:550px">Main page long list</div>
<script>let doubles=0;const nested=document.getElementById('nested'),button=document.getElementById('double');
const report=()=>fetch('/pointer-report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({x:nested.scrollLeft,y:nested.scrollTop,pageY:scrollY,doubles,doc:String(performance.timeOrigin)})});
button.ondblclick=()=>{doubles++;button.style.background='#22c55e';button.textContent='Double '+doubles;report()};
addEventListener('scroll',report,true);report();</script>""",
                content_type="text/html",
            )
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
<button style="left:950px;top:60px;width:220px" onclick="setTimeout(()=>location.href='/next-form',1500)">Navigate after delay</button>
<form style="position:absolute;left:380px;top:60px;width:450px" onsubmit="event.preventDefault()">
<input id="text" aria-label="English and Chinese" style="width:400px;height:50px;font-size:24px" autocomplete="off">
<textarea id="multiline" aria-label="Multiline" style="display:block;margin-top:20px;width:400px;height:80px;font-size:24px"></textarea>
<input id="password" aria-label="Test password" type="password" autocomplete="off" style="display:block;margin-top:20px;width:400px;height:50px">
</form>
<script>
const held = new Set();
const released = [];
const report = () => fetch('/form-report', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:document.getElementById('text').value,multiline:document.getElementById('multiline').value,password:document.getElementById('password').value,held:[...held],released,document:String(performance.timeOrigin),active:document.activeElement.id})});
addEventListener('keydown',e=>{held.add(e.code);report()});
addEventListener('keyup',e=>{held.delete(e.code);released.push(e.code);report()});
addEventListener('input',report);
addEventListener('focusin',report);
</script>
<a style="position:absolute;left:60px;top:430px" href="/pointer">Pointer fixture</a>
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
                    "H06_LOGIN",
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
        login_task = "H06_LOGIN" in str(messages[start])
        if login_task and body.get("tools"):
            returned = [
                item
                for item in messages[start:]
                if item["role"] == "user"
                and "Human browser handback" in str(item.get("content"))
            ]
            if returned:
                observation = json.loads(returned[-1]["content"].split("\n", 1)[1])
                assert observation["url"].endswith("/authenticated")
                continue_element = next(
                    element
                    for element in observation["interactive_elements"]
                    if element.get("text") == "Continue signed-in task"
                )
                assert website_login["submitted"] == 1
                if any(item.get("tool_call_id") == "h06_continue" for item in results):
                    login_calls = []
                else:
                    login_calls = [
                        (
                            "continue",
                            "browser_click",
                            {"index": continue_element["index"]},
                        )
                    ]
            elif not results:
                login_calls = [
                    ("open", "browser_navigate", {"url": fixture_url + "/login"})
                ]
            else:
                model_waiting.set()
                await model_release.wait()
                login_calls = [
                    (
                        "stale",
                        "browser_navigate",
                        {"url": fixture_url + "/stale-login-action"},
                    )
                ]
            message = {
                "role": "assistant",
                "content": None if login_calls else "H06_LOGIN_DONE",
            }
            if login_calls:
                message["tool_calls"] = [
                    {
                        "id": "h06_" + call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                    for call_id, name, arguments in login_calls
                ]
            finish = "tool_calls" if login_calls else "stop"
        elif handoff and body.get("tools"):
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
    fixture.router.add_post("/pointer-report", website)
    fixture.router.add_post("/login-submit", website)
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
                "python_path": ""
                if args.plugin_zip
                else str(args.private_runtime.resolve() / "python/python.exe"),
                "browser_path": "" if args.plugin_zip else str(chromium),
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
    if args.plugin_zip:
        audit_dir = evidence / "network-audit"
        environment["BROWSER_OFFLINE_AUDIT"] = str(audit_dir)
        # Embedded Python ignores PYTHONPATH; a disposable .pth installs the same
        # audit guard without changing application modules or bundled defaults.
        guard_source = ROOT / "scripts/browser_offline_guard.py"
        shutil.copy2(guard_source, guard / "browser_offline_guard.py")
        (guard / "sitecustomize.py").write_text(
            "import browser_offline_guard\n", encoding="utf-8"
        )
        embedded_site = args.private_runtime / "python/Lib/site-packages"
        shutil.copy2(guard_source, embedded_site / "browser_offline_guard.py")
        (embedded_site / "h06_acceptance.pth").write_text(
            "import browser_offline_guard\n", encoding="utf-8"
        )

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
            diagnostic = await view.evaluate("""async () => {
              const headers={Authorization:'Bearer '+localStorage.getItem('token')};
              const chat=location.hash.split('/chat/')[1]?.split('?')[0];
              const base='/api/v1/plugins/extensions/browser-use/';
              const status=await fetch(base+'status?'+new URLSearchParams({chat_id:chat}),{headers});
              const state=await status.json();
              const data=state.data || {};
              delete data.image;
              const snapshot=await fetch(base+'snapshot?'+new URLSearchParams({chat_id:chat,generation:data.generation,browser_session_id:data.browser_session_id}),{headers});
              const shot=await snapshot.json();
              if(shot.data) delete shot.data.image;
              return {status:status.status,state,snapshot_status:snapshot.status,snapshot:shot};
            }""")
            (evidence / "navigation-diagnostic.json").write_text(
                json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            raise

    process = None
    browser = None
    playwright = None
    page = None
    auth_headers = {}
    private_debug_browser = None
    resource_task = None
    owned_process_ids = set()
    resources = {
        "samples": 0,
        "peak_rss_bytes": 0,
        "peak_processes": 0,
        "non_loopback_peers": [],
    }

    async def sample_resources():
        """Sample only the isolated host tree without collecting command secrets."""
        while process is not None and process.poll() is None:
            tree = [psutil.Process(process.pid)]
            with contextlib.suppress(psutil.Error):
                tree.extend(tree[0].children(recursive=True))
            owned_process_ids.update(child.pid for child in tree[1:])
            rss = 0
            for child in tree:
                with contextlib.suppress(psutil.Error):
                    rss += child.memory_info().rss
                    for connection in child.net_connections(kind="inet"):
                        if connection.raddr and connection.raddr.ip not in {
                            "127.0.0.1",
                            "::1",
                        }:
                            resources["non_loopback_peers"].append(connection.raddr.ip)
            resources["samples"] += 1
            resources["peak_processes"] = max(resources["peak_processes"], len(tree))
            resources["peak_rss_bytes"] = max(resources["peak_rss_bytes"], rss)
            await asyncio.sleep(0.5)

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
            resource_task = asyncio.create_task(sample_resources())
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
                    viewport={"width": 1600, "height": 1000},
                    device_scale_factor=1.5 if args.pointer_input else 1,
                )
                if args.pointer_input:
                    # Delay actual SSE captures, not synthetic component events.
                    # Metadata can be delivered after the real WS acknowledgement.
                    await context.add_init_script(r"""
(() => {
  const nativeFetch = window.fetch.bind(window);
  const gate = window.__h04Frames = {hold:false,holdMetadata:false,pending:null,
    metadata:[],metadataCount:0,blockedFrames:0,lastMetadata:null,latest:null,
    limits:{frames:0,maxEventBytes:0,maxImageBytes:0,maxWidth:0,maxHeight:0}};
  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    const url = typeof args[0] === 'string' ? args[0] : args[0].url;
    if (!url?.includes('/browser-use/stream') || !response.body) return response;
    const reader = response.body.getReader();
    const encoder = new TextEncoder(), decoder = new TextDecoder();
    const body = new ReadableStream({
      start(controller) {
        const emit = block => controller.enqueue(encoder.encode(block + '\n\n'));
        gate.flushMetadata = () => {
          gate.holdMetadata = false;
          for (const block of gate.metadata) emit(block);
          gate.metadata = [];
        };
        gate.release = () => {
          gate.hold = false;
          if (gate.pending) emit(gate.pending);
          gate.pending = null;
        };
        (async () => {
          let buffer = '';
          try {
            while (true) {
              const {value, done} = await reader.read();
              if (done) { controller.close(); break; }
              buffer += decoder.decode(value, {stream:true}).replaceAll('\r','');
              let end;
              while ((end = buffer.indexOf('\n\n')) >= 0) {
                const block = buffer.slice(0,end);
                buffer = buffer.slice(end+2);
                const data = block.split('\n').find(line => line.startsWith('data: '));
                if (data) {
                  const event = JSON.parse(data.slice(6));
                  gate.limits.maxEventBytes = Math.max(gate.limits.maxEventBytes,encoder.encode(block).length);
                  if (event.type === 'frame') {
                    gate.limits.frames++;
                    gate.limits.maxImageBytes = Math.max(gate.limits.maxImageBytes,atob(event.image).length);
                    gate.limits.maxWidth = Math.max(gate.limits.maxWidth,event.width);
                    gate.limits.maxHeight = Math.max(gate.limits.maxHeight,event.height);
                  }
                  if (event.type === 'frame' && gate.hold) {
                    gate.pending = block;
                    gate.latest = {sequence:event.sequence,input_id:event.applied_input_id};
                    gate.blockedFrames++;
                    continue;
                  }
                  if (event.type === 'state' || event.type === 'action') {
                    gate.metadataCount++;
                    gate.lastMetadata = {sequence:event.sequence,input_id:event.applied_input_id};
                    if (gate.holdMetadata) {
                      if (gate.metadata.length >= 8) throw new Error('Acceptance metadata gate overflow');
                      gate.metadata.push(block);
                      continue;
                    }
                  }
                }
                emit(block);
              }
            }
          } catch(error) { controller.error(error); }
        })();
      },
      cancel() { return reader.cancel(); }
    });
    return new Response(body, {status:response.status,headers:response.headers});
  };
})();
""")
                page = await context.new_page()
                if args.recovery:
                    await context.add_init_script("""
(() => {
  const NativeSocket = window.WebSocket;
  window.WebSocket = class extends NativeSocket {
    constructor(...args) {
      super(...args);
      this.addEventListener('message', event => {
        const ack = JSON.parse(event.data);
        if ((window.__h05DropClick && ack.operation === 'click') ||
            (window.__h05DropRelease && ack.operation === 'release')) {
          window.__h05DropClick = false;
          window.__h05DropRelease = false;
          event.stopImmediatePropagation();
          this.close();
        }
      });
    }
  };
})();
""")
                wheel_acks = []
                feedback_acks = []

                def socket_opened(ws):
                    """Collect only category/ID/status for pointer acceptance.

                    Args:
                        ws: Actual production socket opened by this isolated page.
                    """

                    def received(raw):
                        """Record wheel confirmation without retaining input contents.

                        Args:
                            raw: A server frame from the authenticated socket.
                        """
                        with contextlib.suppress(ValueError, TypeError):
                            ack = json.loads(raw)
                            if (
                                ack.get("operation") in {"click", "text"}
                                and ack.get("status") == "applied"
                            ):
                                feedback_acks.append(
                                    {
                                        "operation": ack["operation"],
                                        "input_id": ack["input_id"],
                                        "received_at": time.perf_counter(),
                                    }
                                )
                            if ack.get("operation") == "wheel":
                                wheel_acks.append(
                                    {
                                        key: ack.get(key)
                                        for key in ("input_id", "status")
                                    }
                                )

                    ws.on("framereceived", received)

                page.on("websocket", socket_opened)
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
                if args.pointer_input:
                    before_id = await page.locator(".agent-browser-view").get_attribute(
                        "data-frame-input"
                    )
                    before_latency = await page.locator(
                        ".agent-browser-view"
                    ).get_attribute("data-frame-latency")
                    before_pixels = await image.get_attribute("src")
                    await page.evaluate(
                        "Object.assign(window.__h04Frames,{hold:true,holdMetadata:true,pending:null,metadata:[],blockedFrames:0,latest:null})"
                    )
                    await asyncio.sleep(0.2)
                    before_pixels = await image.get_attribute("src")
                    with Image.open(
                        io.BytesIO(base64.b64decode(before_pixels.split(",", 1)[1]))
                    ) as pixels:
                        before_pixel = pixels.convert("RGB").getpixel((80, 80))
                    await page.evaluate(
                        "document.querySelector('.agent-browser-image img').addEventListener('click',()=>window.__h04Frames.inputStarted=performance.now(),{once:true,capture:true})"
                    )
                click_started = time.perf_counter()
                await image.click(position=position)
                for _ in range(30):
                    if counter == 1:
                        break
                    await asyncio.sleep(0.1)
                assert counter == 1, counter
                if args.pointer_input:
                    for _ in range(80):
                        click_ack = next(
                            (
                                ack
                                for ack in feedback_acks
                                if ack["operation"] == "click"
                                and ack["received_at"] >= click_started
                            ),
                            None,
                        )
                        if click_ack:
                            break
                        await asyncio.sleep(0.025)
                    assert click_ack is not None
                    await page.evaluate("window.__h04Frames.flushMetadata()")
                    await page.wait_for_function(
                        "id => window.__h04Frames.latest?.input_id >= id && window.__h04Frames.lastMetadata?.input_id >= id && window.__h04Frames.latest.sequence > window.__h04Frames.lastMetadata.sequence",
                        arg=click_ack["input_id"],
                    )
                    await asyncio.sleep(0.35)
                    gate = await page.evaluate(
                        "({blockedFrames:window.__h04Frames.blockedFrames,metadataCount:window.__h04Frames.metadataCount,latest:window.__h04Frames.latest,lastMetadata:window.__h04Frames.lastMetadata})"
                    )
                    metrics["same_target_frame_proof"] = {
                        "ack_input_id": click_ack["input_id"],
                        "ack_ms": round(
                            (click_ack["received_at"] - click_started) * 1000, 1
                        ),
                        "before_displayed_input": before_id,
                        "gate": gate,
                    }
                    await page.screenshot(path=str(evidence / "feedback-held-zh.png"))
                    held_pixels = await image.get_attribute("src")
                    with Image.open(
                        io.BytesIO(base64.b64decode(held_pixels.split(",", 1)[1]))
                    ) as pixels:
                        held_pixel = pixels.convert("RGB").getpixel((80, 80))
                    metrics["same_target_frame_proof"].update(
                        before_pixel=before_pixel,
                        held_pixel=held_pixel,
                        held_awaiting=await page.locator(
                            ".agent-browser-view"
                        ).get_attribute("data-awaiting-frame"),
                        held_displayed_input=await page.locator(
                            ".agent-browser-view"
                        ).get_attribute("data-frame-input"),
                    )
                    assert held_pixel == before_pixel
                    assert (
                        await page.locator(".agent-browser-view").get_attribute(
                            "data-awaiting-frame"
                        )
                        == "true"
                    ), "Metadata-only action/state falsely confirmed old pixels"
                    assert (
                        await page.locator(".agent-browser-view").get_attribute(
                            "data-frame-input"
                        )
                        == before_id
                    )
                    assert (
                        await page.locator(".agent-browser-view").get_attribute(
                            "data-frame-latency"
                        )
                        == before_latency
                    )
                    released_after_ms = await page.evaluate(
                        "performance.now()-window.__h04Frames.inputStarted"
                    )
                    await page.evaluate("window.__h04Frames.release()")
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
                if args.pointer_input:
                    await page.wait_for_function(
                        "document.querySelector('.agent-browser-image img')?.complete && document.querySelector('.agent-browser-view')?.dataset.awaitingFrame === 'false'"
                    )
                    loaded_ms = float(
                        await page.locator(".agent-browser-view").get_attribute(
                            "data-frame-latency"
                        )
                    )
                    assert loaded_ms >= released_after_ms - 20
                    metrics["same_target_frame_proof"].update(
                        released_after_ms=round(released_after_ms, 1),
                        loaded_latency_ms=loaded_ms,
                        visible_changed_pixel=[red, green, blue],
                    )
                    metrics["checks"].append(
                        "H04-P2: real click acknowledgement and same-target metadata cannot confirm held old JPEG; only released actual changed capture confirms"
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
                        viewport = await status()
                        await image.click(
                            position={
                                "x": box["width"] * x / viewport["viewport_width"],
                                "y": box["height"] * y / viewport["viewport_height"],
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
                if args.text_input:
                    await page.locator(".agent-browser-takeover").click()
                    await page.locator(
                        '.agent-browser-view[data-human-control="true"]'
                    ).wait_for()
                    old_document = (await status())["document_id"]
                    await click_form(1050, 85)
                    await page.keyboard.down("ArrowRight")
                    await form_matches(held=["ArrowRight"])
                    for _ in range(150):
                        if form_state.get(
                            "document"
                        ) != old_document and form_state.get("released") == [
                            "ArrowRight"
                        ]:
                            break
                        await asyncio.sleep(0.1)
                    else:
                        raise AssertionError(
                            "Actual navigation did not release the held key on the new document"
                        )
                    assert await page.locator(".agent-browser-keyboard").evaluate(
                        "e=>e!==document.activeElement"
                    )
                    await page.keyboard.up("ArrowRight")
                    await page.keyboard.insert_text("must stay local")
                    await asyncio.sleep(0.3)
                    assert form_state["text"] == "" and form_state["held"] == []
                    await click_form(500, 85)
                    await page.keyboard.insert_text("new page")
                    await form_matches(text="new page", held=[])
                    await page.locator(".agent-browser-takeover").click()
                    await page.locator(
                        '.agent-browser-view[data-human-control="false"]'
                    ).wait_for()
                    metrics["checks"].append(
                        "H03: actual navigation releases held ArrowRight, clears local focus and blocks residual text until explicit refocus"
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
                if args.pointer_input:
                    view = page.locator(".agent-browser-view")

                    async def pointer_at(x, y, double=False):
                        """Map fixture CSS coordinates through the actual displayed image.

                        Args:
                            x: Remote CSS viewport x.
                            y: Remote CSS viewport y.
                            double: Whether to send the native local double-click sequence.
                        """
                        try:
                            await page.wait_for_function(
                                "document.querySelector('.agent-browser-image img')?.complete && document.querySelector('.agent-browser-image img')?.naturalWidth > 0"
                            )
                        except PlaywrightError:
                            async with client.get(
                                base + "/snapshot",
                                params=ticket_body,
                                headers=auth_headers,
                            ) as diagnostic:
                                (
                                    evidence / "pointer-snapshot-diagnostic.txt"
                                ).write_text(
                                    str(diagnostic.status)
                                    + "\n"
                                    + (await diagnostic.text()),
                                    encoding="utf-8",
                                )
                            raise
                        remote = await status()
                        box = await image.bounding_box()
                        action = image.dblclick if double else image.click
                        await action(
                            position={
                                "x": box["width"] * x / remote["viewport_width"],
                                "y": box["height"] * y / remote["viewport_height"],
                            }
                        )
                        await asyncio.sleep(0.2)

                    await pointer_at(150, 440)
                    for _ in range(100):
                        if pointer_state and (await status())["url"].endswith(
                            "/pointer"
                        ):
                            break
                        await asyncio.sleep(0.1)
                    else:
                        raise AssertionError(
                            "Human navigation to actual pointer fixture failed"
                        )
                    await asyncio.sleep(0.4)
                    await pointer_at(700, 100, double=True)
                    for _ in range(50):
                        if pointer_state.get("doubles") == 1:
                            break
                        await asyncio.sleep(0.1)
                    assert pointer_state["doubles"] == 1, pointer_state
                    latencies = []
                    for dx, dy in [(0, 80), (0, 100), (90, 0), (120, 0), (0, 120)]:
                        box = await image.bounding_box()
                        await page.mouse.move(
                            box["x"] + box["width"] * 200 / 1280,
                            box["y"] + box["height"] * 220 / 720,
                        )
                        before = dict(pointer_state)
                        frame_before = int(
                            await view.get_attribute("data-frame-input") or 0
                        )
                        started = time.perf_counter()
                        await page.mouse.wheel(dx, dy)
                        for _ in range(100):
                            if (
                                (
                                    pointer_state.get("x") != before.get("x")
                                    or pointer_state.get("y") != before.get("y")
                                )
                                and int(
                                    await view.get_attribute("data-frame-input") or 0
                                )
                                > frame_before
                                and await view.get_attribute("data-awaiting-frame")
                                == "false"
                                and await view.get_attribute("data-pending-input")
                                == "0"
                            ):
                                break
                            await asyncio.sleep(0.025)
                        else:
                            raise AssertionError(
                                (
                                    "Wheel did not update real nested list and loaded frame",
                                    pointer_state,
                                    before,
                                )
                            )
                        latencies.append(
                            round((time.perf_counter() - started) * 1000, 1)
                        )
                        await asyncio.sleep(0.25)
                    assert pointer_state["x"] > 0 and pointer_state["y"] > 0
                    assert pointer_state["pageY"] == 0
                    encoded = (await image.get_attribute("src")).split(",", 1)[1]
                    with Image.open(io.BytesIO(base64.b64decode(encoded))) as pixels:
                        red, green, blue = pixels.convert("RGB").getpixel((700, 100))
                        assert red < 80 and green > 150 and blue < 150, (
                            red,
                            green,
                            blue,
                        )
                    await page.screenshot(path=str(evidence / "pointer-en.png"))
                    metrics["pointer_latency_ms"] = latencies
                    limits = await page.evaluate("window.__h04Frames.limits")
                    assert limits["maxEventBytes"] <= 768 * 1024
                    assert limits["maxImageBytes"] <= 512 * 1024
                    assert limits["maxWidth"] <= 1280 and limits["maxHeight"] <= 720
                    metrics["observed_frame_limits"] = limits
                    metrics["pointer_state"] = dict(pointer_state)
                    metrics["wheel_acks"] = wheel_acks
                    # Scroll the long document outside the nested container, then return.
                    box = await image.bounding_box()
                    await page.mouse.move(
                        box["x"] + box["width"] * 1000 / 1280,
                        box["y"] + box["height"] * 600 / 720,
                    )
                    await page.mouse.wheel(0, 240)
                    for _ in range(80):
                        if pointer_state.get("pageY", 0) > 0:
                            break
                        await asyncio.sleep(0.025)
                    assert pointer_state["pageY"] > 0
                    metrics["main_document_scroll_y"] = pointer_state["pageY"]
                    await asyncio.sleep(0.3)
                    await page.mouse.wheel(0, -600)
                    for _ in range(80):
                        if pointer_state.get("pageY") == 0:
                            break
                        await asyncio.sleep(0.025)
                    assert pointer_state["pageY"] == 0
                    await asyncio.sleep(0.3)
                    metrics["checks"].append(
                        "H04: actual long document scrolls independently of the nested list and ChatUI"
                    )
                    # Change the actual private CSS viewport and its device scale.
                    ports = set()
                    for child in psutil.Process(process.pid).children(recursive=True):
                        with contextlib.suppress(psutil.Error):
                            for argument in child.cmdline():
                                if (
                                    child.name().lower() == "chrome.exe"
                                    and argument.startswith("--remote-debugging-port=")
                                ):
                                    ports.add(int(argument.split("=", 1)[1]))
                    assert len(ports) == 1
                    private_view = await playwright.chromium.connect_over_cdp(
                        f"http://127.0.0.1:{ports.pop()}"
                    )
                    remote_page = next(
                        p
                        for c in private_view.contexts
                        for p in c.pages
                        if p.url.endswith("/pointer")
                    )
                    if args.plugin_zip:
                        network_probe = await remote_page.evaluate("""async () => {
                          try { await fetch('http://192.0.2.1:81/offline-probe',{signal:AbortSignal.timeout(3000)});return 'unexpected-success'; }
                          catch(error) { return error.name; }
                        }""")
                        assert network_probe != "unexpected-success"
                        metrics["native_browser_external_probe"] = network_probe
                    remote_cdp = await remote_page.context.new_cdp_session(remote_page)
                    await remote_cdp.send(
                        "Emulation.setDeviceMetricsOverride",
                        {
                            "width": 960,
                            "height": 600,
                            "deviceScaleFactor": 1.25,
                            "mobile": False,
                        },
                    )
                    await asyncio.sleep(0.5)
                    assert (await status())["viewport_width"] == 960
                    await pointer_at(700, 100, double=True)
                    assert pointer_state["doubles"] == 2
                    await remote_cdp.send(
                        "Emulation.setDeviceMetricsOverride",
                        {
                            "width": 1280,
                            "height": 720,
                            "deviceScaleFactor": 1,
                            "mobile": False,
                        },
                    )
                    await asyncio.sleep(0.5)
                    assert (await status())["viewport_width"] == 1280
                    metrics["checks"].append(
                        "H04: actual private CSS viewport resize and DPR 1.25 retain coordinate hits with bounded JPEG scaling"
                    )
                    # Host zoom/DPR, narrow sidebar and fullscreen all use image CSS bounds.
                    await page.set_viewport_size({"width": 1050, "height": 850})
                    await asyncio.sleep(0.4)
                    await pointer_at(700, 100, double=True)
                    assert pointer_state["doubles"] == 3
                    await page.locator(".agent-browser-fullscreen").click()
                    await page.set_viewport_size({"width": 1440, "height": 700})
                    await asyncio.sleep(0.2)
                    box = await image.bounding_box()
                    area = await page.locator(".agent-browser-image").bounding_box()
                    assert (
                        box["y"] >= area["y"]
                        and box["y"] + box["height"] <= area["y"] + area["height"]
                    )
                    assert box["x"] > area["x"] + 20
                    before_blank = pointer_state["doubles"]
                    await page.mouse.click(
                        area["x"] + 2, area["y"] + area["height"] / 2
                    )
                    await asyncio.sleep(0.1)
                    assert pointer_state["doubles"] == before_blank
                    await pointer_at(700, 100, double=True)
                    assert pointer_state["doubles"] == 4
                    assert await page.locator(".agent-browser-handback").evaluate(
                        "e => e.getBoundingClientRect().width >= e.scrollWidth && e.getBoundingClientRect().width > 50"
                    )
                    await page.screenshot(
                        path=str(evidence / "pointer-fullscreen-en.png")
                    )
                    metrics["checks"].append(
                        "H04: wide-short fullscreen fits image within available area; blank margin cannot click; handback label stays readable"
                    )
                    await (
                        page.locator(".agent-browser-stage.is-fullscreen button")
                        .filter(has_text="Hand back")
                        .click()
                    )
                    await page.locator(
                        '.agent-browser-view[data-human-control="false"]'
                    ).wait_for()
                    await page.keyboard.press("Escape")
                    await page.set_viewport_size({"width": 1600, "height": 1000})
                    await page.locator(".agent-browser-takeover").click()
                    await page.locator(
                        '.agent-browser-view[data-human-control="true"]'
                    ).wait_for()
                    metrics["checks"].append(
                        "H04: fullscreen handback explicitly restores Agent admission without leaving the fullscreen UI first"
                    )
                    await asyncio.sleep(0.3)
                    # A burst may reject stale samples, but must stay bounded and owned.
                    box = await image.bounding_box()
                    await page.mouse.move(
                        box["x"] + box["width"] * 200 / 1280,
                        box["y"] + box["height"] * 220 / 720,
                    )
                    queue_sizes = []
                    for _ in range(24):
                        await page.mouse.wheel(0, 24)
                        queue_sizes.append(
                            int(await view.get_attribute("data-pending-input") or 0)
                        )
                    await asyncio.sleep(0.6)
                    assert await view.get_attribute("data-human-control") == "true"
                    assert max(queue_sizes) <= 128
                    assert await view.get_attribute("data-pending-input") == "0"
                    metrics["pointer_max_pending"] = max(queue_sizes)
                    # Wheel outside the image must retain ordinary browser default behavior.
                    await page.evaluate(
                        "window.__outsideWheel=null; addEventListener('wheel',e=>setTimeout(()=>window.__outsideWheel=e.defaultPrevented,0),{capture:true,once:true})"
                    )
                    await page.locator(".chat-text-input, .chat-textarea").hover()
                    await page.mouse.wheel(0, 50)
                    await asyncio.sleep(0.1)
                    assert await page.evaluate("window.__outsideWheel") is False
                    await page.locator(".agent-browser-takeover").click()
                    await view.locator(
                        'xpath=self::*[@data-human-control="false"]'
                    ).wait_for()
                    await page.evaluate(
                        "localStorage.setItem('themeMode','dark'); localStorage.setItem('astrbot-locale','zh-CN')"
                    )
                    await page.reload()
                    await page.locator(".chat-text-input, .chat-textarea").wait_for(
                        timeout=30000
                    )
                    await open_browser_panel(page)
                    await image.wait_for()
                    await page.locator(".agent-browser-takeover").click()
                    await page.locator(
                        '.agent-browser-view[data-human-control="true"]'
                    ).wait_for()
                    await asyncio.sleep(0.4)
                    await page.screenshot(path=str(evidence / "pointer-zh-dark.png"))
                    await page.locator(".agent-browser-fullscreen").click()
                    await asyncio.sleep(0.2)
                    await page.screenshot(
                        path=str(evidence / "pointer-fullscreen-zh.png")
                    )
                    await page.keyboard.press("Escape")
                    metrics["checks"].append(
                        "H04: high-frequency wheel queue bounded with ordered acks; outside image wheel keeps browser default; real dark Chinese UI checked"
                    )
                    await pointer_at(120, 35)
                    for _ in range(50):
                        if not (await status())["url"].endswith("/pointer"):
                            break
                        await asyncio.sleep(0.1)
                    await asyncio.sleep(0.3)
                    metrics["checks"].extend(
                        [
                            "H04: real authenticated nested list vertical/horizontal wheel; parent page remains fixed",
                            "H04: native double-click hits at DPR 1.5, normal/narrow/sidebar and fullscreen after layout changes",
                            "H04: continuous input-to-loaded-frame latency measured; actual navigation invalidates old position",
                        ]
                    )
                await page.locator(".agent-browser-takeover").click()
                # Wait for confirmed handback before querying the renewed host lease.
                await page.locator(
                    '.agent-browser-view[data-human-control="false"]'
                ).wait_for()
                for _ in range(40):
                    if (await status())["control_mode"] == "agent":
                        break
                    await asyncio.sleep(0.1)
                if args.pointer_input:
                    async with client.post(
                        ticket_url, headers=auth_headers, json=ticket_body
                    ) as response:
                        issued = await response.json()
                    async with client.ws_connect(
                        origin + issued["websocket_path"]
                    ) as ws:
                        await ws.send_json({"ticket": issued["ticket"]})
                        acquired = await ws.receive_json()
                        target = {
                            key: acquired["state"][key]
                            for key in (
                                "tab_id",
                                "document_id",
                                "viewport_width",
                                "viewport_height",
                                "scroll_x",
                                "scroll_y",
                                "position_revision",
                            )
                        }
                        target["position_revision"] += 1
                        stale = {
                            "operation": "wheel",
                            "input_id": 1,
                            "control_epoch": acquired["control_epoch"],
                            "target": target,
                            "x": 100,
                            "y": 100,
                            "delta_x": 0,
                            "delta_y": 80,
                        }
                        for _ in range(2):
                            await ws.send_json(stale)
                            assert (await ws.receive_json())["status"] == "stale"
                        assert (await status())["control_mode"] == "human"
                        await ws.send_json(
                            {
                                "operation": "reset_keys",
                                "input_id": 2,
                                "control_epoch": acquired["control_epoch"],
                            }
                        )
                        assert (await ws.receive_json())["status"] == "applied"
                        await ws.send_json(
                            {
                                "operation": "release",
                                "control_epoch": acquired["control_epoch"],
                            }
                        )
                        assert (await ws.receive_json())["status"] == "applied"
                    metrics["checks"].append(
                        "H04: actual host consumes/replays stale wheel ack without dispatch; stays human until explicit handback"
                    )
                if args.recovery:
                    second_context = await browser.new_context(
                        viewport={"width": 1600, "height": 1000}
                    )
                    await second_context.add_init_script(
                        "localStorage.setItem('astrbot-locale','en-US')"
                    )
                    second = await second_context.new_page()
                    await second.goto(origin + "/#/auth/login")
                    await second.locator('input[name="username"]').fill("b06")
                    await second.locator('input[name="password"]').fill(password)
                    await second.locator('button[type="submit"]').click()
                    await second.wait_for_function(
                        "!!localStorage.getItem('token') && !location.hash.includes('/auth/')"
                    )
                    await second.goto(origin + f"/#/chat/{chat_id}")
                    await second.locator(".chat-text-input, .chat-textarea").wait_for()
                    await asyncio.sleep(1)
                    popup = second.get_by_role(
                        "button", name=re.compile("^(关闭|Close)$")
                    )
                    if await popup.count():
                        await popup.first.click()
                    await open_browser_panel(second)

                    async def acquire_ui(view):
                        """Acquire only through the real UI and wait for decoded current pixels.

                        Args:
                            view: Independently authenticated production ChatUI page.
                        """
                        await view.locator(".agent-browser-takeover").click()
                        await view.locator(
                            '.agent-browser-view[data-human-control="true"]'
                        ).wait_for(timeout=30000)
                        await view.wait_for_function(
                            "document.querySelector('.agent-browser-image img')?.naturalWidth > 0"
                        )
                        await asyncio.sleep(0.4)

                    async def paused_without_effects():
                        """Verify authoritative paused ownership and preserved private lifetime.

                        Returns:
                            Fresh server metadata after disconnect revocation.
                        """
                        for _ in range(100):
                            current_state = await status()
                            if current_state["control_mode"] == "paused":
                                assert all(
                                    current_state[k] == v for k, v in identity.items()
                                )
                                return current_state
                            await asyncio.sleep(0.1)
                        raise AssertionError("Controller was not revoked")

                    baseline_effects = (counter, post_effects, len(model_requests))
                    await acquire_ui(page)
                    await second.locator(".agent-browser-occupied").wait_for()
                    assert await second.locator(".agent-browser-takeover").is_disabled()
                    await second.screenshot(
                        path=str(evidence / "recovery-occupied.png")
                    )
                    # A forged/racing second connection also fails at the server.
                    async with client.post(
                        ticket_url, headers=auth_headers, json=ticket_body
                    ) as response:
                        rival = await response.json()
                    async with client.ws_connect(
                        origin + rival["websocket_path"]
                    ) as ws:
                        await ws.send_json({"ticket": rival["ticket"]})
                        assert (await ws.receive_json())["status"] == "rejected"
                    assert (await status())["control_mode"] == "human"
                    metrics["checks"].append(
                        "H05: two independently logged-in production clients show one controller and read-only occupancy; racing socket cannot steal ownership"
                    )
                    for exit_path in (
                        "refresh",
                        "network",
                        "sidebar",
                        "chat",
                        "hidden",
                        "pagehide",
                    ):
                        print(f"H05 recovery exit: {exit_path}", flush=True)
                        if exit_path == "refresh":
                            await page.reload()
                        elif exit_path == "network":
                            await context.set_offline(True)
                        elif exit_path == "sidebar":
                            await page.locator("button.sp-ghost-btn").click()
                        elif exit_path == "chat":
                            async with client.get(
                                origin + "/api/v1/chat/sessions/new",
                                headers=auth_headers,
                            ) as response:
                                another_chat = (await response.json())["data"][
                                    "session_id"
                                ]
                            await page.goto(origin + f"/#/chat/{another_chat}")
                        elif exit_path == "hidden":
                            # Deterministic browser lifecycle fault, through production listeners.
                            await page.evaluate(
                                "Object.defineProperty(document,'visibilityState',{value:'hidden',configurable:true}); document.dispatchEvent(new Event('visibilitychange'))"
                            )
                        else:
                            await page.evaluate(
                                "dispatchEvent(new PageTransitionEvent('pagehide'))"
                            )
                        await paused_without_effects()
                        assert (
                            counter,
                            post_effects,
                            len(model_requests),
                        ) == baseline_effects
                        if exit_path == "network":
                            await context.set_offline(False)
                        if exit_path == "hidden":
                            await page.evaluate(
                                "delete document.visibilityState; document.dispatchEvent(new Event('visibilitychange'))"
                            )
                        await page.goto(origin + f"/#/chat/{chat_id}")
                        await page.reload()
                        await page.locator(".chat-text-input, .chat-textarea").wait_for(
                            timeout=30000
                        )
                        await open_browser_panel(page)
                        await page.locator(".agent-browser-paused").wait_for()
                        assert (
                            await page.locator(".agent-browser-view").get_attribute(
                                "data-human-control"
                            )
                            == "false"
                        )
                        await acquire_ui(page)
                        metrics["checks"].append(
                            f"H05: {exit_path} revokes control and drops queued input; synchronized reconnect stays paused until explicit reacquire, no page/model effects"
                        )
                    # Drop a real successful input acknowledgement, then close its socket.
                    # A second queued click must be discarded, never replayed after refresh.
                    await page.evaluate("window.__h05DropClick=true")
                    before_unknown = counter
                    box = await image.bounding_box()
                    await image.dblclick(
                        position={
                            "x": box["width"] * 160 / 1280,
                            "y": box["height"] * 100 / 720,
                        }
                    )
                    await paused_without_effects()
                    assert counter == before_unknown + 1
                    recovery_clicks += 1
                    await page.wait_for_function(
                        "document.querySelector('.agent-browser-notice')?.textContent.match(/输入确认丢失|Input confirmation was lost/)"
                    )
                    await page.screenshot(
                        path=str(evidence / "recovery-unknown-zh.png")
                    )
                    await page.reload()
                    await page.locator(".chat-text-input, .chat-textarea").wait_for()
                    await open_browser_panel(page)
                    await acquire_ui(second)
                    await second.locator(".agent-browser-takeover").click()
                    await second.locator(
                        '.agent-browser-view[data-human-control="false"]'
                    ).wait_for()
                    await asyncio.sleep(0.5)
                    assert counter == before_unknown + 1
                    recovered_state = await status()
                    assert all(recovered_state[k] == v for k, v in identity.items())
                    await acquire_ui(page)
                    await page.evaluate("window.__h05DropRelease=true")
                    await page.locator(".agent-browser-takeover").click()
                    await page.wait_for_function(
                        "document.querySelector('.agent-browser-notice')?.textContent.match(/输入确认丢失|Input confirmation was lost/)"
                    )
                    assert (await status())["control_mode"] == "agent"
                    assert counter == before_unknown + 1
                    assert await page.locator(".agent-browser-paused").count() == 0
                    metrics["checks"].append(
                        "H05: real handback ack lost after successful server release; UI reports unknown and resynchronizes Agent state without falsely claiming paused or replaying handback"
                    )
                    await second_context.close()
                    if args.pointer_input:
                        # Reproduce the earlier auxiliary-CDP detach, then demand
                        # new decoded pixels from the production observer.
                        old_pixels = await image.get_attribute("src")
                        await remote_page.evaluate(
                            "setTimeout(()=>document.body.style.backgroundColor='#f97316',700)"
                        )
                        await remote_cdp.detach()
                        await private_view.close()
                        for _ in range(100):
                            if await image.get_attribute(
                                "src"
                            ) != old_pixels and await image.evaluate(
                                "e=>e.complete && e.naturalWidth>0"
                            ):
                                break
                            await asyncio.sleep(0.1)
                        else:
                            raise AssertionError(
                                "Production observer did not recover after auxiliary CDP detach"
                            )
                        metrics["checks"].append(
                            "H05: real auxiliary CDP session and transport detached; production read-only observer receives newly changed decoded page pixels without restarting Chromium"
                        )
                    metrics["checks"].append(
                        "H05: real click ack lost after dispatch; UI reports unknown, queued double-click discarded; other authenticated client reacquires and explicitly hands back without replay"
                    )
                if args.login_handoff:
                    model_waiting.clear()
                    model_release.clear()
                    await page.locator(".chat-text-input, .chat-textarea").fill(
                        "H06_LOGIN"
                    )
                    await page.locator(".send-btn[aria-label]").click()
                    await asyncio.wait_for(model_waiting.wait(), 45)
                    await page.locator(".agent-browser-takeover").click()
                    await page.locator(
                        '.agent-browser-view[data-human-control="true"]'
                    ).wait_for(timeout=30000)
                    login_identity = await status()
                    assert all(login_identity[k] == identity[k] for k in identity)
                    requests_before_login = len(model_requests)
                    agent_cursor = login_identity["action_cursor"]
                    agent_actions = {
                        action["action_id"]
                        for action in login_identity["recent_actions"]
                        if action.get("actor") != "human"
                    }
                    model_release.set()
                    await asyncio.sleep(1)
                    assert (await status())["action_cursor"] == agent_cursor
                    assert len(model_requests) == requests_before_login
                    box = await image.bounding_box()
                    await image.click(
                        position={
                            "x": box["width"] * 160 / 1280,
                            "y": box["height"] * 105 / 720,
                        }
                    )
                    await page.keyboard.insert_text("Offline 用户")
                    await page.wait_for_function(
                        "document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0'"
                    )
                    await page.keyboard.press("Tab")
                    await page.keyboard.insert_text(website_password)
                    await page.wait_for_function(
                        "document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0'"
                    )
                    await page.screenshot(path=str(evidence / "h06-login-human.png"))
                    login_started = time.perf_counter()
                    await page.keyboard.press("Tab")
                    await page.keyboard.press("Enter")
                    for _ in range(150):
                        if website_login["submitted"] == 1 and (await status())[
                            "url"
                        ].endswith("/authenticated"):
                            break
                        await asyncio.sleep(0.1)
                    else:
                        raise AssertionError("Real remote form login was not submitted")
                    assert len(model_requests) == requests_before_login
                    authenticated = await status()
                    assert website_password not in json.dumps(authenticated)
                    assert all(authenticated[k] == identity[k] for k in identity)
                    assert authenticated["current_action"] is None
                    assert {
                        action["action_id"]
                        for action in authenticated["recent_actions"]
                        if action.get("actor") != "human"
                    } <= agent_actions
                    assert all(
                        action.get("actor") == "human"
                        for action in authenticated["recent_actions"]
                        if action["action_id"] not in agent_actions
                    )
                    assert website_login == {"submitted": 1, "continued": 0, "stale": 0}
                    await page.screenshot(
                        path=str(evidence / "h06-login-authenticated.png")
                    )
                    await page.locator(".agent-browser-takeover").click()
                    await page.get_by_text(
                        "H06_LOGIN_DONE", exact=False
                    ).first.wait_for(timeout=45000)
                    assert website_login == {"submitted": 1, "continued": 1, "stale": 0}
                    resumed_login = await status()
                    assert all(resumed_login[k] == identity[k] for k in identity)
                    metrics["login_handoff"] = {
                        "identity": identity,
                        "side_effects": website_login,
                        "login_to_agent_done_ms": round(
                            (time.perf_counter() - login_started) * 1000, 1
                        ),
                        "human_model_requests": 0,
                        "human_agent_actions": 0,
                    }
                    metrics["checks"].append(
                        "H06: real POST English/Chinese/password login; same browser/cookie; zero Agent actions during takeover; fresh authenticated handback continues once without stale replay"
                    )
                    await page.screenshot(
                        path=str(evidence / "h06-login-agent-continued.png")
                    )
                    # Restore the pre-existing fixture for every unchanged H02 assertion.
                    await page.locator(".chat-text-input, .chat-textarea").fill(
                        "H01_RUN after login"
                    )
                    await page.locator(".send-btn[aria-label]").click()
                    await page.locator(".send-btn .mdi-stop").wait_for(
                        state="hidden", timeout=45000
                    )
                    for _ in range(100):
                        if (await status())["url"].endswith("/"):
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
                    if args.recovery and scenario in {"H02_MODEL", "H02_PRE_RESET"}:
                        await context.set_offline(True)
                        await paused_without_effects()
                        await asyncio.sleep(0.5)
                        assert len(model_requests) == while_human
                        assert (
                            counter == baseline_count
                            and post_effects == baseline_effects
                        )
                        await context.set_offline(False)
                        await page.reload()
                        await page.locator(
                            ".chat-text-input, .chat-textarea"
                        ).wait_for()
                        await open_browser_panel(page)
                        await page.locator(".agent-browser-paused").wait_for()
                        assert len(model_requests) == while_human
                        await acquire_ui(page)
                        current = await status()
                        metrics["checks"].append(
                            "H05: real running Agent remains paused through network outage and production ChatUI refresh; no model/page effects before explicit reacquire and handback"
                        )
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
                        cancel_started = time.perf_counter()
                        await (
                            page.locator(".send-btn")
                            .filter(has=page.locator(".mdi-stop"))
                            .click()
                        )
                        await page.locator(".send-btn .mdi-stop").wait_for(
                            state="hidden", timeout=15000
                        )
                        metrics.setdefault("cancel_convergence_ms", []).append(
                            round((time.perf_counter() - cancel_started) * 1000, 1)
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
                if args.lost_key_target:
                    # Inspect only this disposable host's process descendants.
                    ports = set()
                    private_debug_pids = set()
                    for child in psutil.Process(process.pid).children(recursive=True):
                        with contextlib.suppress(psutil.Error):
                            for argument in child.cmdline():
                                if (
                                    child.name().lower() == "chrome.exe"
                                    and argument.startswith("--remote-debugging-port=")
                                ):
                                    ports.add(int(argument.split("=", 1)[1]))
                                    private_debug_pids.add(child.pid)
                    assert len(ports) == 1, (
                        "Expected one private Chromium debugging endpoint"
                    )
                    private_debug_browser = await playwright.chromium.connect_over_cdp(
                        f"http://127.0.0.1:{ports.pop()}"
                    )
                    private_cdp = await private_debug_browser.new_browser_cdp_session()
                    await private_cdp.send(
                        "Target.createTarget", {"url": fixture_url + "/spare"}
                    )
                    await open_browser_panel(page)
                    await page.locator(".agent-browser-takeover").click()
                    await page.locator(
                        '.agent-browser-view[data-human-control="true"]'
                    ).wait_for(timeout=20000)
                    await click_form(500, 85)
                    await page.keyboard.down("Shift")
                    await form_matches(held=["ShiftLeft"])
                    held_state = await status()
                    destroyed = held_state["tab_id"]
                    requests_before_loss = len(model_requests)
                    metrics["lost_key_target"] = {
                        "held_epoch": held_state["control_epoch"],
                        "target_id": destroyed,
                    }
                    assert (
                        await private_cdp.send(
                            "Target.closeTarget", {"targetId": destroyed}
                        )
                    )["success"]
                    await asyncio.sleep(0.5)
                    async with page.expect_response(
                        lambda r: (
                            "/browser-use/stop" in r.url and r.request.method == "POST"
                        )
                    ) as stopped:
                        stop_started = time.perf_counter()
                        await page.locator(".agent-browser-stop").click(timeout=15000)
                    stopped_response = await stopped.value
                    metrics["lost_key_target"]["stop_response_ms"] = round(
                        (time.perf_counter() - stop_started) * 1000, 1
                    )
                    metrics["lost_key_target"]["stop_status"] = stopped_response.status
                    assert stopped_response.status == 200, (
                        "Stopping was blocked by a keyUp to the destroyed target"
                    )
                    await page.locator(
                        '.agent-browser-view[data-connection="stopped"]'
                    ).wait_for(timeout=15000)
                    stopped_state = await status()
                    assert stopped_state["control_mode"] == "paused"
                    assert stopped_state["control_epoch"] > held_state["control_epoch"]
                    metrics["lost_key_target"]["stopped_epoch"] = stopped_state[
                        "control_epoch"
                    ]
                    await page.screenshot(
                        path=str(evidence / "destroyed-target-stopped.png")
                    )
                    async with page.expect_response(
                        lambda r: (
                            "/browser-use/close" in r.url and r.request.method == "POST"
                        )
                    ) as closed:
                        close_started = time.perf_counter()
                        await page.locator(".agent-browser-close").click(timeout=15000)
                    close_response = await closed.value
                    metrics["lost_key_target"]["close_response_ms"] = round(
                        (time.perf_counter() - close_started) * 1000, 1
                    )
                    metrics["lost_key_target"]["close_status"] = close_response.status
                    assert close_response.status == 200
                    await page.locator(
                        '.agent-browser-view[data-connection="closed"]'
                    ).wait_for(timeout=15000)
                    for _ in range(50):
                        if not private_debug_browser.is_connected():
                            if not any(
                                psutil.pid_exists(pid) for pid in private_debug_pids
                            ):
                                break
                        else:
                            try:
                                targets = (await private_cdp.send("Target.getTargets"))[
                                    "targetInfos"
                                ]
                            except PlaywrightError:
                                await asyncio.sleep(0.1)
                                continue
                            if not any(
                                t.get("type") == "page"
                                and t.get("url", "").startswith(fixture_url)
                                for t in targets
                            ):
                                break
                        await asyncio.sleep(0.1)
                    else:
                        raise AssertionError(
                            "Host close left private fixture pages alive"
                        )
                    assert len(model_requests) == requests_before_loss
                    metrics["lost_key_target"].update(
                        page_targets_removed=True, no_model_resume=True
                    )
                    await page.keyboard.up("Shift")
                    await page.screenshot(
                        path=str(evidence / "destroyed-target-closed.png")
                    )
                    metrics["checks"].append(
                        "H03-P2: destroyed real held-Shift target; production UI stop and close succeed and remove real private pages"
                    )
                else:
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
                if args.recovery:
                    ordinary_identity = await ordinary_page.evaluate(
                        "({account:localStorage.getItem('account_id'),token:localStorage.getItem('token')})"
                    )
                    ordinary_state_url = base + "/status"
                    async with client.get(
                        ordinary_state_url,
                        headers=ordinary_auth,
                        params={"chat_id": ordinary_chat},
                    ) as response:
                        ordinary_state = (await response.json())["data"]
                    ordinary_body = {
                        "chat_id": ordinary_chat,
                        **{k: ordinary_state[k] for k in identity},
                    }
                    await acquire_ui(ordinary_page)
                    before_logout = (counter, post_effects, len(model_requests))
                    await ordinary_page.locator(".chat-account-actions button").click()
                    await ordinary_page.wait_for_function(
                        "!localStorage.getItem('token')", timeout=15000
                    )
                    await asyncio.sleep(31)
                    await ordinary_page.goto(origin + "/#/auth/login")
                    await ordinary_page.locator('input[name="username"]').fill(
                        "h01-user"
                    )
                    await ordinary_page.locator('input[name="password"]').fill(
                        ordinary_password
                    )
                    await ordinary_page.locator('button[type="submit"]').click()
                    await ordinary_page.wait_for_function(
                        "!!localStorage.getItem('token') && !location.hash.includes('/auth/')"
                    )
                    ordinary_auth = {
                        "Authorization": "Bearer "
                        + await ordinary_page.evaluate("localStorage.getItem('token')")
                    }
                    await ordinary_page.goto(origin + f"/#/chat/{ordinary_chat}")
                    await ordinary_page.locator(
                        ".chat-text-input, .chat-textarea"
                    ).wait_for()
                    await open_browser_panel(ordinary_page)
                    await ordinary_page.locator(".agent-browser-paused").wait_for()
                    assert (counter, post_effects, len(model_requests)) == before_logout
                    await acquire_ui(ordinary_page)
                    metrics["checks"].append(
                        "H05: production logout button clears client credentials and closes input/observation while controlling; fresh login retains paused browser and cannot replay or resume Agent"
                    )
                    before_revoke = (counter, post_effects, len(model_requests))
                    async with client.post(
                        ticket_url, headers=ordinary_auth, json=ordinary_body
                    ) as response:
                        old_ticket = await response.json()
                    async with client.post(
                        origin
                        + f"/api/v1/accounts/{ordinary_identity['account']}/disable",
                        headers=auth_headers,
                    ) as response:
                        assert response.status == 200
                    await ordinary_page.wait_for_function(
                        "!localStorage.getItem('token')", timeout=15000
                    )
                    # The host cannot authorize cleanup through a revoked account;
                    # the independent runtime deadline must still revoke it.
                    await asyncio.sleep(31)
                    async with client.post(
                        origin
                        + f"/api/v1/accounts/{ordinary_identity['account']}/enable",
                        headers=auth_headers,
                    ) as response:
                        assert response.status == 200
                    async with client.ws_connect(
                        origin + old_ticket["websocket_path"]
                    ) as ws:
                        await ws.send_json({"ticket": old_ticket["ticket"]})
                        assert (await ws.receive_json())["status"] == "rejected"
                    await ordinary_page.goto(origin + "/#/auth/login")
                    await ordinary_page.locator('input[name="username"]').fill(
                        "h01-user"
                    )
                    await ordinary_page.locator('input[name="password"]').fill(
                        ordinary_password
                    )
                    await ordinary_page.locator('button[type="submit"]').click()
                    await ordinary_page.wait_for_function(
                        "!!localStorage.getItem('token') && !location.hash.includes('/auth/')"
                    )
                    ordinary_token = await ordinary_page.evaluate(
                        "localStorage.getItem('token')"
                    )
                    ordinary_auth = {"Authorization": f"Bearer {ordinary_token}"}
                    async with client.get(
                        ordinary_state_url,
                        headers=ordinary_auth,
                        params={"chat_id": ordinary_chat},
                    ) as response:
                        after_revoke = (await response.json())["data"]
                    assert after_revoke["control_mode"] == "paused"
                    assert all(after_revoke[k] == ordinary_body[k] for k in identity)
                    assert (counter, post_effects, len(model_requests)) == before_revoke
                    await ordinary_page.goto(origin + f"/#/chat/{ordinary_chat}")
                    await ordinary_page.locator(
                        ".chat-text-input, .chat-textarea"
                    ).wait_for()
                    await open_browser_panel(ordinary_page)
                    await ordinary_page.locator(".agent-browser-paused").wait_for()
                    await ordinary_page.screenshot(
                        path=str(evidence / "recovery-revoked.png")
                    )
                    await acquire_ui(ordinary_page)
                    metrics["checks"].append(
                        "H05: ordinary account access revoked while controlling; UI input stops, runtime deadline revokes ownership; fresh login preserves paused Chromium, old ticket cannot revive it"
                    )
                disable_started = time.perf_counter()
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
                metrics["disable_response_ms"] = round(
                    (time.perf_counter() - disable_started) * 1000, 1
                )
                if args.recovery:
                    await ordinary_page.locator(
                        '.agent-browser-view[data-human-control="false"]'
                    ).wait_for(timeout=15000)
                    before_restart = (counter, post_effects, len(model_requests))
                    async with client.patch(
                        origin + "/api/v1/plugins/astrbot_plugin_browser_use/enabled",
                        headers=auth_headers,
                        json={"enabled": True},
                    ) as response:
                        assert response.status == 200, await response.text()
                    async with client.post(
                        ticket_url, headers=ordinary_auth, json=ordinary_body
                    ) as response:
                        assert response.status == 409
                    assert (
                        counter,
                        post_effects,
                        len(model_requests),
                    ) == before_restart
                    await ordinary_page.reload()
                    await ordinary_page.locator(
                        ".chat-text-input, .chat-textarea"
                    ).wait_for()
                    await ordinary_page.locator(".session-item.active").wait_for(
                        timeout=30000
                    )
                    await asyncio.sleep(1)
                    await ordinary_page.locator(
                        ".chat-text-input, .chat-textarea"
                    ).fill("H01_RUN after plugin restart")
                    await ordinary_page.screenshot(
                        path=str(evidence / "recovery-restart-ready.png")
                    )
                    await ordinary_page.locator(".send-btn[aria-label]").click()
                    for _ in range(120):
                        async with client.get(
                            base + "/status",
                            headers=ordinary_auth,
                            params={"chat_id": ordinary_chat},
                        ) as response:
                            restarted = (await response.json())["data"]
                        if restarted.get("available"):
                            break
                        await asyncio.sleep(0.5)
                    assert restarted["generation"] != ordinary_body["generation"]
                    assert (
                        restarted["browser_session_id"]
                        != ordinary_body["browser_session_id"]
                    )
                    await open_browser_panel(ordinary_page)
                    await acquire_ui(ordinary_page)
                    await ordinary_page.screenshot(
                        path=str(evidence / "recovery-restarted.png")
                    )
                    async with client.patch(
                        origin + "/api/v1/plugins/astrbot_plugin_browser_use/enabled",
                        headers=auth_headers,
                        json={"enabled": False},
                    ) as response:
                        assert response.status == 200
                    metrics["checks"].append(
                        "H05: plugin disabled during real human ownership; explicit restart keeps old identity invalid and cannot resume work; new explicit task creates a new generation/browser and can acquire"
                    )
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
                if args.plugin_zip:
                    assert not resources["non_loopback_peers"], resources
                    cleanup_started = time.perf_counter()
                    for _ in range(150):
                        survivors = [
                            p.pid
                            for p in psutil.process_iter(["cmdline"])
                            if p.pid in owned_process_ids
                            and str(installed).lower()
                            in " ".join(p.info["cmdline"] or []).lower()
                        ]
                        if not survivors:
                            break
                        await asyncio.sleep(0.1)
                    assert not survivors, survivors
                    metrics["owned_process_cleanup"] = {
                        "observed_child_pids": sorted(owned_process_ids),
                        "remaining_installed_children": survivors,
                        "fixture_ui_browser": "separate test-process child, closed in finally",
                    }
                    metrics["disable_process_convergence_ms"] = round(
                        (time.perf_counter() - cleanup_started) * 1000, 1
                    )
                    assert not list(
                        (data / "plugin_data/astrbot_plugin_browser_use").glob(
                            "**/observer.json"
                        )
                    )
                    audit_files = list(audit_dir.glob("*.jsonl"))
                    audit_records = [
                        json.loads(line)
                        for path in audit_files
                        for line in path.read_text().splitlines()
                    ]
                    loaded = [
                        r["executable"]
                        for r in audit_records
                        if r["event"] == "guard_loaded"
                    ]
                    assert str(args.private_runtime / "python/python.exe") in loaded, (
                        loaded
                    )
                    assert any(r.get("browser_proxy") for r in audit_records)
                    assert any(r.get("allowed") is False for r in audit_records)
                    metrics["offline_audit"] = {
                        "guarded_interpreters": sorted(set(loaded)),
                        "events": len(audit_records),
                        "blocked_external_events": sum(
                            r.get("allowed") is False for r in audit_records
                        ),
                        "strength": "Python audited sockets/DNS/installers; native Chromium dead SOCKS proxy, loopback bypass, DNS/QUIC disabled; no OS firewall or physical offline claim",
                    }
                    logs = (evidence / "host.log").read_text(
                        encoding="utf-8", errors="replace"
                    )
                    assert (
                        website_password not in logs
                        and website_password not in json.dumps(model_requests)
                    )
                    metrics["checks"].extend(
                        [
                            "H06: relocated bundled interpreter runs under audited loopback-only networking; no pip/Chromium download; native browser proxy constraints recorded",
                            "H06: disable leaves no installed MCP/Chromium process or observer discovery file; password absent from host logs and model requests",
                        ]
                    )
                assert len(cookies) == (
                    5 if args.handoff_boundaries else 4
                ) + recovery_clicks and all(
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
        if resource_task:
            resource_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await resource_task
        metrics["resources"] = resources
        if page is not None and not metrics["passed"]:
            with contextlib.suppress(Exception):
                await page.screenshot(path=str(evidence / "failure.png"))
        (evidence / "result.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (evidence / "model-requests.json").write_text(
            json.dumps(model_requests, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if private_debug_browser is not None and private_debug_browser.is_connected():
            with contextlib.suppress(Exception):
                private_cleanup = await private_debug_browser.new_browser_cdp_session()
                await private_cleanup.send("Browser.close")
            with contextlib.suppress(Exception):
                await private_debug_browser.close()
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
