"""Verify a final offline ZIP through real AstrBot login, ChatUI and Agent tools.

Run with a development interpreter containing AstrBot dependencies, aiohttp,
playwright and psutil. Only the supplied, newly created evidence directory is
written. The OpenAI-compatible model fixture and website bind to loopback.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import html
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import aiohttp
import psutil
from aiohttp import web
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
    parser.add_argument("--plugin-zip", type=Path, required=True)
    parser.add_argument("--dashboard-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    distribution = evidence / "离线 安装包"
    with zipfile.ZipFile(args.plugin_zip) as archive:
        archive.extractall(distribution)
    runtime_root = evidence / "迁移后的 AstrBot"
    data = runtime_root / "data"
    (data / "plugins").mkdir(parents=True)
    persistent = data / "plugin_data/astrbot_plugin_browser_use"
    persistent.mkdir(parents=True)
    sentinel = persistent / "existing-user-data.txt"
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
    await asyncio.to_thread(
        subprocess.run, command, check=True, creationflags=subprocess.CREATE_NO_WINDOW
    )
    assert (
        await asyncio.to_thread(
            subprocess.run,
            command,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    ).returncode != 0
    assert sentinel.read_text() == "preserve"
    moved_root = evidence / "再次迁移 AstrBot data"
    assert runtime_root.resolve().is_relative_to(evidence)
    assert moved_root.resolve().is_relative_to(evidence)
    runtime_root.rename(moved_root)
    runtime_root = moved_root
    data = runtime_root / "data"
    persistent = data / "plugin_data/astrbot_plugin_browser_use"
    sentinel = persistent / "existing-user-data.txt"
    installed = data / "plugins/astrbot_plugin_browser_use"
    chromium = installed / "runtime/chromium/chrome.exe"
    metrics = {
        "passed": False,
        "plugin_zip_sha256": hashlib.sha256(args.plugin_zip.read_bytes()).hexdigest(),
        "model": "loopback deterministic OpenAI fixture",
        "external_target": "not tested",
        "vllm": "not tested",
        "checks": [
            "final ZIP install",
            "Chinese and space paths",
            "overwrite refused",
            "persistent data preserved",
        ],
    }
    model_requests = []
    slow_entered = asyncio.Event()

    async def website(request):
        """Serve a deterministic Chinese form and dynamic, scrollable page.

        Args:
            request: Loopback website request.

        Returns:
            The local form, submitted result, or intentionally slow page.
        """
        if request.path == "/slow":
            slow_entered.set()
            await asyncio.sleep(25)
        value = html.escape(request.query.get("value", ""))
        return web.Response(
            text=(
                f'<!doctype html><meta charset="utf-8"><title>B06 登录态</title>'
                '<form action="/result"><input name="value" placeholder="offline-value">'
                '<button type="submit">Submit</button></form>'
                f'<p id="result">{value}</p><p id="clock" style="position:fixed;right:12px;top:12px;background:white"></p>'
                + "".join(
                    f'<div style="height:300px;background:{"#e5f4ff" if index % 2 else "#fff4d8"};padding:24px">B06 离线浏览器 · 登录态 present · Scroll {index}</div>'
                    for index in range(8)
                )
                + '<script>document.cookie="b06_login=present;path=/";setInterval(()=>document.querySelector("#clock").textContent=Date.now(),100)</script>'
            ),
            content_type="text/html",
        )

    fixture_url = ""

    async def completion(request):
        """Return actual tool calls; never replace the host Agent executor.

        Args:
            request: OpenAI-compatible request from the real host provider.

        Returns:
            A deterministic completion or equivalent streaming completion.
        """
        body = await request.json()
        model_requests.append(body)
        messages = body["messages"]
        latest = max(
            i for i, message in enumerate(messages) if message["role"] == "user"
        )
        user_text = str(messages[latest]["content"])
        results = [m for m in messages[latest + 1 :] if m["role"] == "tool"]
        step = len(results)
        tools = {entry["function"]["name"] for entry in body.get("tools", [])}
        if not tools:
            # ChatUI also asks the configured provider for a short chat title.
            assert body.get("stream") is False
            return web.json_response(
                {
                    "id": "b06-title",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": "b06-fixture",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "B06 offline browser",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 4,
                        "total_tokens": 24,
                    },
                }
            )
        assert "browser_navigate" in tools, sorted(tools)
        if "B06_SLOW" in user_text:
            plan = [("browser_navigate", {"url": fixture_url + "/slow"})]
        else:
            elements = []
            tabs = []
            for result in results:
                content = str(result.get("content", ""))
                for start, char in enumerate(content):
                    if char in "[{":
                        with contextlib.suppress(ValueError):
                            parsed, _ = json.JSONDecoder().raw_decode(content[start:])
                            if (
                                isinstance(parsed, dict)
                                and "interactive_elements" in parsed
                            ):
                                elements = parsed["interactive_elements"]
                            if (
                                isinstance(parsed, list)
                                and parsed
                                and all(
                                    isinstance(item, dict) and "tab_id" in item
                                    for item in parsed
                                )
                            ):
                                tabs = parsed
            field = next(
                (
                    item["index"]
                    for item in elements
                    if item.get("placeholder") == "offline-value"
                ),
                1,
            )
            button = next(
                (item["index"] for item in elements if item.get("tag") == "button"), 2
            )
            plan = [
                ("browser_navigate", {"url": fixture_url}),
                ("browser_get_state", {}),
                ("browser_type", {"index": field, "text": "离线完整链路成功"}),
                ("browser_get_state", {}),
                ("browser_click", {"index": button}),
                ("browser_get_html", {"selector": "#result"}),
                ("browser_scroll", {"direction": "down"}),
                ("browser_navigate", {"url": fixture_url + "/tab", "new_tab": True}),
                ("browser_list_tabs", {}),
                (
                    "browser_switch_tab",
                    {
                        "tab_id": next(
                            (
                                item["tab_id"]
                                for item in tabs
                                if "/result" in item["url"]
                            ),
                            "0000",
                        )
                    },
                ),
            ]
        if step < len(plan):
            name, arguments = plan[step]
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_b06_{step}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        },
                    }
                ],
            }
            finish = "tool_calls"
        else:
            assert "B06_SLOW" in user_text or "离线完整链路成功" in str(results), (
                results
            )
            message = {
                "role": "assistant",
                "content": "B06_DONE_AGAIN"
                if "_AGAIN" in user_text
                else "B06_SLOW_DONE"
                if "B06_SLOW" in user_text
                else "B06_DONE",
            }
            finish = "stop"
        payload = {
            "id": f"b06-{len(model_requests)}",
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
            chunk = {
                **payload,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return web.Response(
                text=f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n",
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
    password = "B06LocalAcceptance9"
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
        json.dumps({"allowed_domains": ["http://127.0.0.1"]}), encoding="utf-8"
    )
    environment = {
        **os.environ,
        "ASTRBOT_ROOT": str(runtime_root),
        "PYTHONPATH": str(ROOT),
        "PYTHONUTF8": "1",
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
    process = None
    browser = None
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
                async with async_playwright() as playwright:
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
                    await page.get_by_role("button", name="关闭", exact=True).click(
                        timeout=15000
                    )
                    await page.screenshot(path=str(evidence / "chat-before-task.png"))
                    await page.locator(".chat-text-input, .chat-textarea").fill(
                        "B06_RUN"
                    )
                    await page.locator(".send-btn").click()
                    await page.wait_for_timeout(2000)
                    await page.screenshot(path=str(evidence / "chat-task-sent.png"))
                    await page.get_by_text("B06_DONE", exact=False).first.wait_for(
                        timeout=120000
                    )
                    opened_at = time.monotonic()
                    await page.locator(
                        'button.sp-ghost-btn[aria-label*="Agent"]'
                    ).click()
                    await page.locator(".agent-browser-image img").wait_for(
                        timeout=20000
                    )
                    await page.wait_for_function(
                        "document.querySelector('.agent-browser-image img')?.naturalWidth > 0"
                    )
                    metrics["panel_first_image_seconds"] = round(
                        time.monotonic() - opened_at, 3
                    )
                    await page.screenshot(path=str(evidence / "chatui.png"))
                    metrics["checks"].extend(
                        [
                            "production Chat.vue",
                            "real host Agent tool loop",
                            "Chinese input and submit",
                            "scroll and new tab",
                            "no-project entry",
                            "authenticated frame rendering",
                        ]
                    )
                    base = origin + "/api/v1/plugins/extensions/browser-use"
                    async with client.get(
                        base + "/status",
                        params={"chat_id": chat_id},
                        headers=auth_headers,
                    ) as response:
                        state = (await response.json())["data"]
                        assert response.status == 200, state
                    identity = {
                        key: state[key] for key in ("generation", "browser_session_id")
                    }
                    latencies = []
                    async with client.get(
                        base + "/stream",
                        params={"chat_id": chat_id, **identity, "action_cursor": 0},
                        headers=auth_headers,
                        read_bufsize=1024 * 1024,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as response:
                        assert response.status == 200
                        while len(latencies) < 4:
                            line = await response.content.readline()
                            assert line, "Observation stream ended before four frames."
                            if line.startswith(b"data:"):
                                event = json.loads(line[5:])
                                if event["type"] == "frame":
                                    latencies.append(
                                        round(
                                            (time.time() - event["capture_timestamp"])
                                            * 1000,
                                            2,
                                        )
                                    )
                    metrics["host_sse_frame_delivery_ms"] = latencies
                    async with client.get(
                        base + "/status", params={"chat_id": chat_id}
                    ) as response:
                        assert response.status == 401
                    async with client.get(
                        base + "/status",
                        params={"chat_id": "missing-chat"},
                        headers=auth_headers,
                    ) as response:
                        assert response.status == 403
                    async with client.get(
                        origin + "/api/v1/chat/sessions/new", headers=auth_headers
                    ) as response:
                        other_chat = (await response.json())["data"]["session_id"]
                    async with client.get(
                        base + "/status",
                        params={"chat_id": other_chat},
                        headers=auth_headers,
                    ) as response:
                        assert (
                            response.status == 409
                            and (await response.json())["data"]["reason"] == "busy"
                        )
                    metrics["checks"].append("unauthenticated and wrong-chat rejected")
                    await page.reload()
                    await page.locator(".chat-text-input, .chat-textarea").wait_for()
                    if not await page.locator(".agent-browser-image img").count():
                        await page.locator(
                            'button.sp-ghost-btn[aria-label*="Agent"]'
                        ).click()
                    await page.locator(".agent-browser-image img").wait_for(
                        timeout=20000
                    )
                    async with client.get(
                        base + "/status",
                        params={"chat_id": chat_id},
                        headers=auth_headers,
                    ) as response:
                        restored = (await response.json())["data"]
                    assert all(
                        restored[key] == value for key, value in identity.items()
                    )
                    metrics["checks"].append("refresh restores same Chromium session")
                    before = time.monotonic()
                    async with page.expect_response(
                        lambda response: (
                            "/browser-use/stop?" in response.url
                            and response.request.method == "POST"
                        )
                    ) as control_response:
                        await page.locator(".agent-browser-stop").click()
                    assert (await (await control_response.value).json())["data"][
                        "execution_state"
                    ] == "stopped"
                    metrics["stop_seconds"] = round(time.monotonic() - before, 3)
                    before = time.monotonic()
                    async with page.expect_response(
                        lambda response: (
                            "/browser-use/close?" in response.url
                            and response.request.method == "POST"
                        )
                    ) as control_response:
                        await page.locator(".agent-browser-close").click()
                    assert (await (await control_response.value).json())["data"][
                        "execution_state"
                    ] == "closed"
                    metrics["close_seconds"] = round(time.monotonic() - before, 3)
                    await page.locator(".chat-text-input, .chat-textarea").fill(
                        "B06_SLOW"
                    )
                    await page.locator(".send-btn").click()
                    await asyncio.wait_for(slow_entered.wait(), 30)
                    before = time.monotonic()
                    async with client.patch(
                        origin + "/api/v1/plugins/astrbot_plugin_browser_use/enabled",
                        headers=auth_headers,
                        json={"enabled": False},
                    ) as response:
                        assert (
                            response.status == 200
                            and (await response.json())["status"] == "ok"
                        )
                    metrics["disable_seconds"] = round(time.monotonic() - before, 3)
                    async with client.get(
                        base + "/status",
                        params={"chat_id": chat_id},
                        headers=auth_headers,
                    ) as response:
                        assert (
                            response.status == 503
                            and (await response.json())["data"]["reason"] == "disabled"
                        )
                    assert not list(persistent.glob("observer-*.json"))
                    async with client.patch(
                        origin + "/api/v1/plugins/astrbot_plugin_browser_use/enabled",
                        headers=auth_headers,
                        json={"enabled": True},
                    ) as response:
                        assert (
                            response.status == 200
                            and (await response.json())["status"] == "ok"
                        )
                    await page.locator(".chat-text-input, .chat-textarea").fill(
                        "B06_RUN_AGAIN"
                    )
                    await page.locator(".send-btn").click()
                    await page.wait_for_timeout(2000)
                    await page.get_by_text(
                        "B06_DONE_AGAIN", exact=False
                    ).first.wait_for(timeout=120000)
                    async with client.get(
                        base + "/status",
                        params={"chat_id": chat_id},
                        headers=auth_headers,
                    ) as response:
                        renewed = (await response.json())["data"]
                    assert (
                        renewed["generation"] != identity["generation"]
                        and renewed["available"]
                    )
                    metrics["checks"].extend(
                        [
                            "UI stop and close",
                            "disable during real navigation",
                            "bridge removed",
                            "real plugin re-enable with new generation",
                            "Agent usable after re-enable",
                        ]
                    )
                    await page.screenshot(path=str(evidence / "reenabled.png"))
                    await browser.close()
                    browser = None
                async with client.patch(
                    origin + "/api/v1/plugins/astrbot_plugin_browser_use/enabled",
                    headers=auth_headers,
                    json={"enabled": False},
                ) as response:
                    assert (await response.json())["status"] == "ok"
                await asyncio.sleep(1)
                survivors = [
                    p.pid
                    for p in psutil.process_iter(["cmdline"])
                    if p.info["cmdline"]
                    and str(installed).lower() in " ".join(p.info["cmdline"]).lower()
                ]
                assert not survivors, survivors
                metrics["checks"].append(
                    "no installed MCP or Chromium process after disable"
                )
                assert sentinel.read_text() == "preserve"
                assert all(
                    "data:image" not in json.dumps(request)
                    for request in model_requests
                )
                metrics["model_requests"] = len(model_requests)
                metrics["checks"].append(
                    "observation frames absent from model requests"
                )
                metrics["passed"] = True
                print(json.dumps(metrics, ensure_ascii=False), flush=True)
    finally:
        (evidence / "result.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (evidence / "model-requests.json").write_text(
            json.dumps(model_requests, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if browser:
            await browser.close()
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
