"""Exercise production ChatUI media with the actual isolated Browser Use plugin.

This source-checkout acceptance does not validate a final offline installation.
The parent launcher owns the entire test/host/runtime/viewer tree in a Job Object.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import psutil
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.async_api import async_playwright  # noqa: E402


def viewport_point(surface, viewport, x, y):
    """Map fixture CSS coordinates through the stable production surface.

    Args:
        surface: Actual receiver element bounds.
        viewport: Authoritative fixture CSS viewport dimensions.
        x: Fixture CSS horizontal coordinate.
        y: Fixture CSS vertical coordinate.

    Returns:
        Actual receiver pointer position.
    """
    return (
        surface["x"] + x / viewport["width"] * surface["width"],
        surface["y"] + y / viewport["height"] * surface["height"],
    )


async def exercise_controls(
    page, target, mode, first_case, out, metrics, fixture_origin
):
    """Measure the same six native interactions in each production display mode.

    Args:
        page: Logged-in production receiver.
        target: Actual private Chromium fixture page, inspected read-only.
        mode: Explicit video or JPEG selection.
        first_case: First optical action ID for this independent sample.
        out: Isolated evidence directory.
        metrics: Machine-readable acceptance results.
        fixture_origin: Epoch origin encoded by the unchanged V01 fixture.
    """
    await page.evaluate("mode=>__videoProof.mode=mode", mode)
    measurement_started = await page.evaluate(
        "performance.timeOrigin+performance.now()"
    )
    cpu_before = {}
    for process in [psutil.Process(), *psutil.Process().children(recursive=True)]:
        with contextlib.suppress(psutil.Error):
            cpu_before[(process.pid, process.create_time())] = sum(
                process.cpu_times()[:2]
            )
    cpu_started = time.monotonic()
    stats_before = await page.evaluate("__videoProof.statistics()")
    actions = []
    for sample_index in range(1, 7):
        case_id = first_case + sample_index - 1
        await target.evaluate("id=>probe.pending=id", case_id)
        await page.evaluate("id=>__videoProof.caseId=id", case_id)
        await page.wait_for_function(
            "document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.pendingInput === '0'"
        )
        surface = await page.locator(".agent-browser-viewport").bounding_box()
        viewport = await target.evaluate("({width:innerWidth,height:innerHeight})")

        if sample_index in {1, 2}:
            slider = await target.locator("#slider").bounding_box()
            await page.mouse.click(
                *viewport_point(
                    surface,
                    viewport,
                    slider["x"]
                    + slider["width"] * (0.35 if sample_index == 1 else 0.7),
                    slider["y"] + slider["height"] / 2,
                )
            )
        elif sample_index in {3, 4}:
            drag = await target.locator("#drag").bounding_box()
            x, y = drag["x"] + 40, drag["y"] + 30
            await page.mouse.move(*viewport_point(surface, viewport, x, y))
            await page.mouse.down()
            for step in range(1, 22):
                await page.mouse.move(
                    *viewport_point(surface, viewport, x + 7 * step, y + 2 * step)
                )
                await asyncio.sleep(0.008)
            await page.mouse.up()
        else:
            await page.mouse.move(*viewport_point(surface, viewport, 500, 650))
            await page.mouse.wheel(0, 320 if sample_index == 5 else -160)
        await page.wait_for_function(
            "id=>__videoProof.frames.some(f=>f.valid && f.actual[1]===id)",
            arg=case_id,
            timeout=12000,
        )
        await page.wait_for_function(
            "document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0' && document.querySelector('.agent-browser-view')?.dataset.awaitingFrame==='false'",
            timeout=12000,
        )
        actions.append(
            await target.evaluate(
                "({values:probe.rendered.values,events:probe.events.filter(e=>e.id===probe.pending)})"
            )
        )
        await asyncio.sleep(0.2)
    proof = await page.evaluate(
        "({metadata:__videoProof.metadata,frames:__videoProof.frames,commands:__videoProof.commands,acks:__videoProof.acks,inputs:__videoProof.inputs,ice:__videoProof.peers.map(p=>p.getConfiguration().iceServers)})"
    )
    (out / f"{mode}-transport-pixels.json").write_text(
        json.dumps(proof, indent=2), encoding="utf-8"
    )
    latencies = []
    for case_id in range(first_case, first_case + 6):
        sent = next(i for i in proof["inputs"] if i["caseId"] == case_id)
        presented = next(
            f for f in proof["frames"] if f["valid"] and f["actual"][1] == case_id
        )
        latencies.append(presented["t"] - sent["t"])
    actual = [
        f
        for f in proof["frames"]
        if f["valid"] and f["mode"] == mode and f["t"] >= measurement_started
    ]
    cpu_seconds = 0
    rss_bytes = 0
    for process in [psutil.Process(), *psutil.Process().children(recursive=True)]:
        with contextlib.suppress(psutil.Error):
            cpu_seconds += max(
                0,
                sum(process.cpu_times()[:2])
                - cpu_before.get(
                    (process.pid, process.create_time()), sum(process.cpu_times()[:2])
                ),
            )
            rss_bytes += process.memory_info().rss
    position = (len(latencies) - 1) * 0.95
    index = int(position)
    ordered = sorted(latencies)
    metrics[f"guarded_{mode}_measurement"] = {
        "latencies_ms": latencies,
        "p95_ms": ordered[index]
        + (ordered[min(index + 1, len(ordered) - 1)] - ordered[index])
        * (position - index),
        "max_ms": max(latencies),
        "p95_method": "linear quantile; six samples, not a stability guarantee",
        "dimensions": sorted({(f["width"], f["height"]) for f in actual}),
        "css_viewport": await target.evaluate(
            "({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})"
        ),
        "cpu_core_percent": cpu_seconds / (time.monotonic() - cpu_started) * 100,
        "rss_bytes_end": rss_bytes,
        "resource_scope": "owned acceptance/host/private browser/receiver tree, including optical sampling; one logical core is 100 percent",
        "rtp_stats_before": stats_before,
        "rtp_stats_after": await page.evaluate("__videoProof.statistics()"),
        "source_paint_to_present_ms": [
            next(f for f in proof["frames"] if f["valid"] and f["actual"][1] == case)[
                "t"
            ]
            - fixture_origin
            - next(f for f in proof["frames"] if f["valid"] and f["actual"][1] == case)[
                "words"
            ][0]
            for case in range(first_case, first_case + 6)
        ],
        "source_change_to_captured_paint_ms": [
            next(f for f in proof["frames"] if f["valid"] and f["actual"][1] == case)[
                "words"
            ][0]
            - next(f for f in proof["frames"] if f["valid"] and f["actual"][1] == case)[
                "actual"
            ][2]
            for case in range(first_case, first_case + 6)
        ],
        "presented_fps": (len(actual) - 1)
        / ((actual[-1]["t"] - actual[0]["t"]) / 1000),
        "valid_frames": len(actual),
        "clock": "receiver input event to optically decoded actual fixture state; performance.timeOrigin+now; "
        + (
            "expectedDisplayTime adjusted"
            if mode == "video"
            else "JPEG load plus two animation frames"
        ),
        "fixture_origin": fixture_origin,
        "actions": actions,
        "ice_servers": proof["ice"],
    }
    assert all(
        command.get("target", {}).get("rtp_timestamp") == command.get("presentedRtp")
        for command in proof["commands"]
        if mode == "video"
        and command.get("operation") == "pointer"
        and command.get("phase") == "down"
    )
    assert all(
        ack.get("status") == "applied" for ack in proof["acks"] if ack.get("input_id")
    )
    assert all(config == [] for config in proof["ice"])
    metrics["checks"].append(
        f"{mode}: slider/21-point drags/wheels all applied with optical feedback; video presses match actual RTP"
    )
    await page.screenshot(path=str(out / f"manual-{mode}.png"))


async def open_agent_panel(page):
    """Use the existing production navigation and dismiss its first-use notice.

    Args:
        page: Authenticated receiver in its own disposable browser context.
    """
    popup = page.get_by_role("button", name="关闭", exact=True)
    if await popup.count() and await popup.first.is_visible():
        await popup.first.evaluate("button=>button.click()")
    await page.keyboard.press("Escape")
    entry = page.locator("button.sp-ghost-btn")
    if await entry.get_attribute("aria-pressed") != "true":
        await entry.click()
    if await page.locator(".agent-browser-view").is_visible():
        return
    tab = page.locator(".git-diff-sidebar-browser-tab")
    await tab.wait_for()
    await tab.get_by_role("tab").click()
    await page.locator(".browser-mode-trigger").click()
    await page.locator(".browser-mode-agent").click()
    await page.locator(".agent-browser-view").wait_for()


async def main(args):
    """Run owned production services and save source/transport/pixel evidence.

    Args:
        args: Explicit isolated output and startup gate arguments.
    """
    plugin = args.plugin_source.resolve()
    private_python = args.private_python.resolve()
    chromium = args.chromium.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    runroot = out / "isolated-host"
    data = runroot / "data"
    installed = data / "plugins/astrbot_plugin_browser_use"
    installed.mkdir(parents=True)
    for source in plugin.iterdir():
        if source.is_file() and source.suffix in {".py", ".json", ".yaml", ".txt"}:
            shutil.copy2(source, installed / source.name)
    metrics = {"passed": False, "stage": "V02 production source checkout", "checks": []}
    metrics["environment"] = {
        "os": platform.platform(),
        "architecture": platform.machine(),
        "processor": platform.processor(),
        "logical_cpus": psutil.cpu_count(),
        "host_python": sys.version,
        "encoding": "software libvpx VP8 in private aiortc 1.14.0 / PyAV 16.0.1",
    }
    metrics["source_sha256"] = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [
            ROOT / "astrbot/dashboard/access.py",
            ROOT / "astrbot/dashboard/api/plugins.py",
            ROOT / "dashboard/src/components/chat/AgentBrowserView.vue",
            ROOT / "dashboard/src/components/chat/useAgentBrowserVideo.ts",
            ROOT / "dashboard/src/components/chat/browserMediaFrames.ts",
        ]
    }
    metrics["plugin_sha256"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in installed.glob("*.py")
    }
    model_requests = []
    target_html = (plugin / "scripts/video_probe_target.html").read_text(
        encoding="utf-8"
    )
    fixture_url = ""

    async def website(request):
        """Serve the same optical scroll/drag/animation target as V01.

        Args:
            request: Loopback fixture request.

        Returns:
            HTML from the tracked real-browser fixture.
        """
        if request.path == "/contract":
            html = (ROOT / "scripts/browser_video_contract_target.html").read_text(
                encoding="utf-8"
            )
            return web.Response(
                text=html.replace(
                    "CHILD_URL",
                    fixture_url.replace("127.0.0.1", "localhost") + "/contract-child",
                ),
                content_type="text/html",
            )
        if request.path == "/contract-child":
            return web.Response(
                text=(ROOT / "scripts/browser_video_contract_child.html").read_text(
                    encoding="utf-8"
                ),
                content_type="text/html",
            )
        return web.Response(text=target_html, content_type="text/html")

    async def completion(request):
        """Bootstrap a real browser through normal Agent/MCP admission.

        Args:
            request: OpenAI-compatible local model request.

        Returns:
            One deterministic navigation or a completed response.
        """
        body = await request.json()
        model_requests.append(body)
        messages = body["messages"]
        start = max(i for i, item in enumerate(messages) if item["role"] == "user")
        results = [item for item in messages[start:] if item["role"] == "tool"]
        message = {"role": "assistant", "content": "V02_BROWSER_READY"}
        finish = "stop"
        if body.get("tools") and not results:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "v02_open",
                        "type": "function",
                        "function": {
                            "name": "browser_navigate",
                            "arguments": json.dumps(
                                {
                                    "url": fixture_url
                                    + "/?origin="
                                    + str(round(time.time() * 1000))
                                }
                            ),
                        },
                    }
                ],
            }
            finish = "tool_calls"
        payload = {
            "id": "v02-local",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "v02-fixture",
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }
        if body.get("stream"):
            delta = {key: value for key, value in message.items() if key != "role"}
            for index, call in enumerate(delta.get("tool_calls", [])):
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

    app = web.Application()
    app.router.add_post("/v1/chat/completions", completion)
    app.router.add_get(
        "/v1/models",
        lambda _: web.json_response(
            {"data": [{"id": "v02-fixture", "object": "model"}]}
        ),
    )
    app.router.add_get("/{path:.*}", website)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    fixture_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    password = "V02" + secrets.token_urlsafe(20) + "9"
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 600000
    ).hex()
    config = {
        "dashboard": {
            "host": "127.0.0.1",
            "port": port,
            "username": "v02",
            "pbkdf2_password": f"pbkdf2_sha256$600000${salt}${digest}",
            "password_storage_upgraded": True,
            "password_change_required": False,
        },
        "provider": [
            {
                "id": "v02",
                "type": "openai_chat_completion",
                "provider_type": "chat_completion",
                "enable": True,
                "key": ["local-fixture"],
                "api_base": fixture_url + "/v1",
                "model": "v02-fixture",
            }
        ],
        "agent_runner": {
            "runner_type": "local",
            "config": {"model": {"provider_id": "v02"}, "misc": {"max_steps": 8}},
        },
    }
    (data / "cmd_config.json").write_text(json.dumps(config), encoding="utf-8")
    (data / "config").mkdir(exist_ok=True)
    (data / "config/astrbot_plugin_browser_use_config.json").write_text(
        json.dumps(
            {
                "allowed_domains": ["http://127.0.0.1", "http://localhost"],
                "python_path": str(private_python),
                "browser_path": str(chromium),
            }
        ),
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "ASTRBOT_ROOT": str(runroot),
        "PYTHONPATH": str(ROOT),
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    guard = out / "network-guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(
        "import socket, ipaddress\n"
        "_connect=socket.socket.connect\n_resolve=socket.getaddrinfo\n"
        "def local(host):\n"
        "    try: return host == 'localhost' or ipaddress.ip_address(host).is_loopback\n"
        "    except ValueError: return False\n"
        "def connect(self,address):\n"
        "    if isinstance(address,tuple) and not local(address[0]): raise OSError('Fixture blocks external sockets')\n"
        "    return _connect(self,address)\n"
        "def resolve(host,*args,**kwargs):\n"
        "    if not local(host): raise OSError('Fixture blocks external DNS')\n"
        "    return _resolve(host,*args,**kwargs)\n"
        "socket.socket.connect=connect\nsocket.getaddrinfo=resolve\n",
        encoding="utf-8",
    )
    environment["PYTHONPATH"] = os.pathsep.join((str(guard), str(ROOT)))
    host = viewer = second_viewer = playwright = page = private_browser = None
    auth = {}
    errors = []
    try:
        with (out / "host.log").open("w", encoding="utf-8") as log:
            host = await asyncio.to_thread(
                subprocess.Popen,
                [
                    sys.executable,
                    str(ROOT / "main.py"),
                    "--webui-dir",
                    str(ROOT / "dashboard/dist"),
                ],
                cwd=runroot,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        async with aiohttp.ClientSession() as client:
            for _ in range(180):
                if host.poll() is not None:
                    raise RuntimeError(f"Host exited {host.returncode}")
                try:
                    async with client.get(origin + "/api/v1/auth/setup") as response:
                        if response.status < 500:
                            break
                except aiohttp.ClientError:
                    pass
                await asyncio.sleep(1)
            else:
                raise RuntimeError("Host startup timeout")
            playwright = await async_playwright().start()
            viewer = await playwright.chromium.launch(
                executable_path=str(chromium),
                headless=True,
                ignore_default_args=["--hide-scrollbars"],
            )
            context = await viewer.new_context(viewport={"width": 1600, "height": 1000})
            await context.add_init_script(
                path=str(ROOT / "scripts/browser_video_production_instrument.js")
            )
            await context.route(
                re.compile(r"^https?://(?!127\.0\.0\.1[:/]|localhost[:/])"),
                lambda route: route.abort(),
            )
            page = await context.new_page()
            page.on(
                "response",
                lambda response: (
                    print("HTTP:", response.status, response.url, flush=True)
                    if response.status >= 400 and "/browser-" in response.url
                    else None
                ),
            )
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    print("UI:", message.text, flush=True)
                    if message.type == "error"
                    else None
                ),
            )
            await page.goto(origin + "/#/auth/login")
            await page.locator('input[name="username"]').fill("v02")
            await page.locator('input[name="password"]').fill(password)
            await page.locator('button[type="submit"]').click()
            await page.wait_for_function("!!localStorage.getItem('token')")
            await page.wait_for_function(
                "!location.hash.includes('/auth/')", timeout=60000
            )
            auth = {
                "Authorization": "Bearer "
                + await page.evaluate("localStorage.getItem('token')")
            }
            metrics["checks"].append("real Dashboard login")
            async with client.get(
                origin + "/api/v1/chat/sessions/new", headers=auth
            ) as response:
                chat_id = (await response.json())["data"]["session_id"]
            await page.goto(origin + f"/#/chat/{chat_id}")
            await page.locator(".chat-text-input, .chat-textarea").wait_for(
                timeout=60000
            )
            await page.locator(".session-item.active").wait_for(timeout=60000)
            popup = page.get_by_role("button", name="关闭", exact=True)
            if await popup.count():
                await popup.first.evaluate("button=>button.click()")
            await page.locator(".chat-text-input, .chat-textarea").fill("V02_OPEN")
            await page.locator(".send-btn[aria-label]").click()
            await page.get_by_text("V02_BROWSER_READY", exact=False).first.wait_for(
                timeout=120000
            )
            await asyncio.sleep(2)
            await page.keyboard.press("Escape")
            popup = page.get_by_role("button", name="关闭", exact=True)
            if await popup.count() and await popup.first.is_visible():
                await popup.first.evaluate("button=>button.click()")
            await page.keyboard.press("Escape")
            tab = page.locator(".git-diff-sidebar-browser-tab")
            if not await tab.is_visible():
                await page.locator("button.sp-ghost-btn").click()
            if not await page.locator(".agent-browser-view").is_visible():
                await tab.get_by_role("tab").click()
                await page.locator(".browser-mode-trigger").click()
                await page.locator(".browser-mode-agent").click()
            async with client.get(
                origin + "/api/v1/plugins/extensions/browser-use/status",
                params={"chat_id": chat_id},
                headers=auth,
            ) as response:
                metrics["initial_status"] = await response.json()
            print(
                "Initial media status:",
                json.dumps(metrics["initial_status"]),
                flush=True,
            )
            await page.locator(".agent-browser-refresh").click()
            await page.wait_for_function(
                "document.querySelector('.agent-browser-view')?.dataset.videoPhase === 'live' || ['failed','unsupported'].includes(document.querySelector('.agent-browser-view')?.dataset.videoPhase) || document.querySelector('.agent-browser-view')?.dataset.connection === 'disconnected'",
                timeout=20000,
            )
            assert (
                await page.locator(".agent-browser-view").get_attribute(
                    "data-video-phase"
                )
                == "live"
            ), "Media startup did not reach mapped video"
            await page.wait_for_function(
                "document.querySelector('.agent-browser-view')?.dataset.frameId?.length === 32"
            )
            await page.screenshot(path=str(out / "video-live.png"))
            metrics["checks"].append(
                "production ChatUI receives actual mapped WebRTC video from private Chromium"
            )
            metrics["media"] = await page.locator(".agent-browser-view").evaluate(
                "el=>({...el.dataset, videoWidth:el.querySelector('video').videoWidth,videoHeight:el.querySelector('video').videoHeight,decoded:el.querySelector('video').getVideoPlaybackQuality().totalVideoFrames})"
            )
            await page.evaluate("__videoProof.attach()")
            # Discover only this owned host's Chromium; no process-name sweep or
            # user browser is eligible for the read-only fixture inspection.
            for child in psutil.Process(host.pid).children(recursive=True):
                command = child.cmdline()
                debugging = next(
                    (
                        arg.split("=", 1)[1]
                        for arg in command
                        if arg.startswith("--remote-debugging-port=")
                    ),
                    None,
                )
                if debugging and debugging != "0":
                    private_browser = await playwright.chromium.connect_over_cdp(
                        f"http://127.0.0.1:{debugging}"
                    )
                    break
            assert private_browser is not None, "Owned private Chromium was not found"
            target = next(
                p
                for c in private_browser.contexts
                for p in c.pages
                if p.url.startswith(fixture_url)
            )
            fixture_origin = await target.evaluate(
                "Number(new URL(location.href).searchParams.get('origin'))"
            )
            await page.locator(".agent-browser-takeover").click()
            await page.locator(
                '.agent-browser-view[data-human-control="true"]'
            ).wait_for(timeout=20000)
            await page.wait_for_function(
                "document.querySelector('.agent-browser-view')?.dataset.mediaSessionId && document.querySelector('.agent-browser-view')?.dataset.frameId"
            )
            await exercise_controls(
                page, target, "video", 1, out, metrics, fixture_origin
            )
            await target.evaluate("""() => {
              const input=document.createElement('input');input.id='keyboard';
              input.style='position:fixed;left:20px;top:200px;width:300px;height:28px;z-index:10';
              document.body.append(input);window.keyboardEvents=[];
              input.addEventListener('keydown',e=>keyboardEvents.push([e.type,e.code]));
              input.addEventListener('keyup',e=>keyboardEvents.push([e.type,e.code]));
            }""")
            await asyncio.sleep(0.5)
            await page.wait_for_function(
                "document.querySelector('.agent-browser-view')?.dataset.frameId"
            )
            surface = await page.locator(".agent-browser-viewport").bounding_box()
            viewport = await target.evaluate("({width:innerWidth,height:innerHeight})")
            await page.mouse.click(
                surface["x"] + 80 / viewport["width"] * surface["width"],
                surface["y"] + 215 / viewport["height"] * surface["height"],
            )
            await page.wait_for_function(
                "document.querySelector('.agent-browser-view')?.dataset.awaitingFrame==='false' && document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0'"
            )
            await page.evaluate("__videoProof.holdMetadata=true")
            await page.wait_for_function(
                "document.querySelector('.agent-browser-view')?.dataset.videoPhase==='frozen'"
            )
            await page.keyboard.type("V02abc", delay=10)
            assert await target.locator("#keyboard").input_value() == ""
            await page.evaluate("__videoProof.flushMetadata()")
            await target.wait_for_function(
                "document.querySelector('#keyboard').value==='V02abc'", timeout=8000
            )
            await page.keyboard.press("Control+A")
            await page.keyboard.type("replacement", delay=10)
            await target.wait_for_function(
                "document.querySelector('#keyboard').value==='replacement'",
                timeout=8000,
            )
            metrics["checks"].append(
                "metadata gap queues original-target text without dispatch until mapped pixels recover; rapid text and Ctrl+A preserve native keyboard order"
            )
            await page.wait_for_function(
                "document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0'"
            )
            await page.keyboard.down("Control")
            await target.wait_for_function(
                "keyboardEvents.filter(e=>e[0]==='keydown'&&e[1]==='ControlLeft').length > keyboardEvents.filter(e=>e[0]==='keyup'&&e[1]==='ControlLeft').length"
            )
            control_sockets = await page.evaluate(
                "__videoProof.sockets.filter(s=>s.url.includes('browser-control')).length"
            )
            await page.evaluate(
                "__videoProof.sockets.findLast(s=>s.url.includes('browser-media')).close(4001,'acceptance media fault')"
            )
            await page.locator(
                '.agent-browser-view[data-video-phase="failed"][data-human-control="true"]'
            ).wait_for(timeout=5000)
            await page.screenshot(path=str(out / "video-fault.png"))
            await page.keyboard.up("Control")
            await target.wait_for_function(
                "keyboardEvents.filter(e=>e[0]==='keydown'&&e[1]==='ControlLeft').length === keyboardEvents.filter(e=>e[0]==='keyup'&&e[1]==='ControlLeft').length"
            )
            metrics["native_keyboard_events"] = await target.evaluate("keyboardEvents")
            metrics["checks"].append(
                "held key releases its original native input session while media is failed and no frame is mapped"
            )
            await page.get_by_role("button", name="切换 JPEG", exact=True).click()
            await page.wait_for_function(
                "document.querySelector('.agent-browser-view')?.dataset.videoPhase==='idle' && document.querySelector('.agent-browser-view')?.dataset.frameId && !document.querySelector('.agent-browser-view')?.dataset.mediaSessionId"
            )
            assert (
                await page.locator(".agent-browser-view").get_attribute(
                    "data-human-control"
                )
                == "true"
            )
            assert (
                await page.evaluate(
                    "__videoProof.sockets.filter(s=>s.url.includes('browser-control')).length"
                )
                == control_sockets
            )
            metrics["checks"].append(
                "media fault freezes pixels; explicit JPEG fallback preserves original control socket/lease"
            )
            await page.evaluate("__videoProof.mode='jpeg'")
            await target.goto(
                fixture_url + "/?origin=" + str(round(time.time() * 1000))
            )
            await page.wait_for_function(
                "doc=>document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.frameDocumentId===doc && !document.querySelector('.agent-browser-view')?.dataset.mediaSessionId",
                arg=await target.evaluate("String(performance.timeOrigin)"),
            )
            await exercise_controls(
                page,
                target,
                "jpeg",
                7,
                out,
                metrics,
                await target.evaluate(
                    "Number(new URL(location.href).searchParams.get('origin'))"
                ),
            )
            await page.evaluate("__videoProof.mode='video'")
            await page.get_by_role("button", name="重试视频", exact=True).click()
            await page.locator(
                '.agent-browser-view[data-video-phase="live"][data-human-control="true"]'
            ).wait_for(timeout=20000)
            await target.goto(fixture_url + "/contract")
            await page.wait_for_function(
                "document.querySelector('.agent-browser-view')?.dataset.frameId && __videoProof.metadata.some(m=>m.frame_id===document.querySelector('.agent-browser-view').dataset.frameId && m.url.endsWith('/contract'))"
            )
            cdp = await private_browser.new_browser_cdp_session()
            metrics["native_iframe_targets"] = [
                item["targetId"]
                for item in (await cdp.send("Target.getTargets"))["targetInfos"]
                if item["type"] == "iframe"
            ]
            contract_cases = []
            for index, kind in enumerate(
                ("same-url-reload", "closed-shadow-scroll", "cross-origin-frame-scroll")
            ):
                await page.wait_for_function(
                    "document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0'"
                )
                surface = await page.locator(".agent-browser-viewport").bounding_box()
                viewport = await target.evaluate(
                    "({width:innerWidth,height:innerHeight})"
                )
                x, y = (800, 190) if kind == "cross-origin-frame-scroll" else (250, 190)
                before = await page.locator(".agent-browser-view").get_attribute(
                    "data-frame-id"
                )
                await page.evaluate(
                    "id=>{__videoProof.caseId=id;__videoProof.holdInput=true;__videoProof.holdMetadata=true}",
                    200 + index,
                )
                await page.mouse.move(*viewport_point(surface, viewport, x, y))
                await page.mouse.down()
                await page.wait_for_function("__videoProof.heldInputs.length===1")
                held_id = await page.evaluate(
                    "JSON.parse(__videoProof.heldInputs[0][1]).input_id"
                )
                if kind == "same-url-reload":
                    await target.reload()
                elif kind == "closed-shadow-scroll":
                    await target.evaluate("scrollShadow()")
                else:
                    child = next(
                        frame
                        for frame in target.frames
                        if "/contract-child" in frame.url
                    )
                    await child.evaluate("scrollChild()")
                await asyncio.sleep(0.2)
                await page.evaluate("__videoProof.flushInput()")
                await page.wait_for_function(
                    "id=>__videoProof.acks.some(a=>a.input_id===id && a.status==='stale')",
                    arg=held_id,
                )
                await page.mouse.up()
                assert await target.evaluate("hits.length") == (
                    1 if kind == "cross-origin-frame-scroll" else 0
                )
                if kind == "cross-origin-frame-scroll":
                    assert await child.evaluate("hits.length") == 0
                await page.evaluate("__videoProof.flushMetadata()")
                await page.wait_for_function(
                    "old=>document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view').dataset.frameId!==old",
                    arg=before,
                )
                nonce = await target.evaluate("fixtureDocument")
                sampled = await page.evaluate(
                    """({x,y})=>{
                  const root=document.querySelector('.agent-browser-view'),v=root.querySelector('video'),c=document.createElement('canvas');
                  c.width=v.videoWidth;c.height=v.videoHeight;const ctx=c.getContext('2d');ctx.drawImage(v,0,0);
                  const record=__videoProof.metadata.findLast(m=>m.frame_id===root.dataset.frameId),scale=c.width/record.viewport_width;
                  let nonce=0;for(let bit=0;bit<32;bit++)if(ctx.getImageData(Math.floor((bit*18+9)*scale),Math.floor(6*scale),1,1).data[0]>128)nonce+=2**bit;
                  return {nonce,color:Array.from(ctx.getImageData(Math.floor(x*scale),Math.floor(y*scale),1,1).data),record};
                }""",
                    {"x": x - 100, "y": y + 15},
                )
                assert sampled["nonce"] == nonce, (kind, sampled["nonce"], nonce)
                if kind != "same-url-reload":
                    assert sampled["color"][1] > 150 and sampled["color"][0] < 80, (
                        sampled
                    )
                await page.mouse.click(*viewport_point(surface, viewport, x, y))
                if kind == "cross-origin-frame-scroll":
                    await child.wait_for_function("hits.length===1")
                    assert await child.evaluate("hits") == ["B"]
                else:
                    expected = "A" if kind == "same-url-reload" else "B"
                    await target.wait_for_function("hits.length===1")
                    assert await target.evaluate("hits") == [expected]
                    if kind == "same-url-reload":
                        await target.evaluate("hits.length=0")
                contract_cases.append(
                    {
                        "kind": kind,
                        "old_frame": before,
                        "stale_input_id": held_id,
                        "new_record": sampled["record"],
                        "optical_document": nonce,
                        "new_pixel": sampled["color"],
                    }
                )
            metrics["coordinate_contract_cases"] = contract_cases
            assert (
                await page.evaluate(
                    "__videoProof.sockets.filter(s=>s.url.includes('browser-control')).length"
                )
                == control_sockets
            )
            metrics["checks"].append(
                "delayed old input/metadata rejected across reload, closed-shadow and cross-origin scrolling; optical new document/color and actual fresh hits recover on original control socket"
            )
            await page.wait_for_function(
                "document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0' && document.querySelector('.agent-browser-view')?.dataset.awaitingFrame==='false'"
            )
            await asyncio.sleep(0.5)
            first_media = await page.locator(".agent-browser-view").get_attribute(
                "data-media-session-id"
            )
            visual = await target.context.new_cdp_session(target)
            await visual.send("Emulation.setPageScaleFactor", {"pageScaleFactor": 1.25})
            await page.wait_for_function(
                "!document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.videoPhase==='frozen'"
            )
            await visual.send("Emulation.setPageScaleFactor", {"pageScaleFactor": 1})
            await visual.detach()
            await page.wait_for_function(
                "document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.videoPhase==='live'"
            )
            assert (
                await page.locator(".agent-browser-view").get_attribute(
                    "data-media-session-id"
                )
                == first_media
            )
            metrics["checks"].append(
                "native pinch scaling freezes unmappable input; restoring scale recovers actual mapped pixels on the same peer and lease"
            )
            second_viewer = await playwright.chromium.launch(
                executable_path=str(chromium),
                headless=True,
                ignore_default_args=["--hide-scrollbars"],
            )
            second_context = await second_viewer.new_context(
                storage_state=await context.storage_state(),
                viewport={"width": 1600, "height": 1000},
            )
            await second_context.add_init_script(
                "localStorage.setItem('astrbot-locale','en-US')"
            )
            await second_context.add_init_script(
                path=str(ROOT / "scripts/browser_video_production_instrument.js")
            )
            await second_context.route(
                re.compile(r"^https?://(?!127\.0\.0\.1[:/]|localhost[:/])"),
                lambda route: route.abort(),
            )
            second_page = await second_context.new_page()
            await second_page.goto(origin + f"/#/chat/{chat_id}")
            await second_page.locator(".chat-text-input, .chat-textarea").wait_for(
                timeout=60000
            )
            await open_agent_panel(second_page)
            await second_page.locator(
                '.agent-browser-view[data-video-phase="live"]'
            ).wait_for(timeout=20000)
            assert (
                await second_page.locator(".agent-browser-view").get_attribute(
                    "data-media-session-id"
                )
                != first_media
            )
            assert await second_page.locator(".agent-browser-takeover").is_disabled()
            await second_page.screenshot(path=str(out / "video-live-en.png"))
            assert (
                await second_page.locator(".agent-browser-view video").evaluate(
                    "v=>v.getVideoPlaybackQuality().totalVideoFrames"
                )
                > 0
            )
            await second_page.get_by_role("button", name="Use JPEG", exact=True).click()
            await second_page.wait_for_function(
                "document.querySelector('.agent-browser-view')?.dataset.frameId && !document.querySelector('.agent-browser-view')?.dataset.mediaSessionId"
            )
            before_mixed = await page.locator(".agent-browser-view").get_attribute(
                "data-frame-id"
            )
            child = next(
                frame for frame in target.frames if "/contract-child" in frame.url
            )
            await child.evaluate("scroller.scrollTop=0")
            await page.wait_for_function(
                "old=>document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view').dataset.frameId!==old",
                arg=before_mixed,
            )
            assert (
                await page.locator(".agent-browser-view").get_attribute(
                    "data-human-control"
                )
                == "true"
            )
            await second_viewer.close()
            second_viewer = None
            metrics["checks"].append(
                "second actual ChatUI viewer decodes a static source and cannot steal control; mixed JPEG/video viewers retain current coordinate state after automatic child scrolling"
            )
            ticket_body = {
                "chat_id": chat_id,
                **{
                    name: metrics["initial_status"]["data"][name]
                    for name in ("generation", "browser_session_id")
                },
            }
            media_ticket_url = origin + "/api/v1/plugins/browser-media/session"
            ordinary_password = "V02" + secrets.token_urlsafe(20) + "9"
            async with client.post(
                origin + "/api/v1/accounts",
                headers=auth,
                json={"username": "v02-user", "password": ordinary_password},
            ) as response:
                assert response.status == 200, await response.text()
                ordinary_id = (await response.json())["data"]["account_id"]
            async with client.post(
                origin + "/api/v1/auth/login",
                json={"username": "v02-user", "password": ordinary_password},
            ) as response:
                ordinary_auth = {
                    "Authorization": "Bearer "
                    + (await response.json())["data"]["token"]
                }
            async with client.get(
                origin + "/api/v1/chat/sessions/new", headers=ordinary_auth
            ) as response:
                ordinary_chat = (await response.json())["data"]["session_id"]
            admissions = []
            for headers, body, expected in [
                ({}, ticket_body, 401),
                (ordinary_auth, ticket_body, 403),
                (auth, {**ticket_body, "chat_id": ordinary_chat}, 403),
                (auth, {**ticket_body, "generation": "expired"}, 409),
                (auth, {**ticket_body, "owner": "forged"}, 422),
            ]:
                async with client.post(
                    media_ticket_url, headers=headers, json=body
                ) as response:
                    assert response.status == expected, await response.text()
                    admissions.append(response.status)
            async with client.post(
                origin + "/api/v1/plugins/extensions/browser-use/media",
                headers=auth,
                params=ticket_body,
            ) as response:
                assert response.status == 403
            async with client.post(
                media_ticket_url, headers=auth, json=ticket_body
            ) as response:
                expired = await response.json()
                assert response.status == 200, expired
            await asyncio.sleep(16)
            async with client.ws_connect(
                origin + expired["websocket_path"], origin=origin
            ) as expired_socket:
                await expired_socket.send_json(
                    {
                        "ticket": expired["ticket"],
                        "type": "offer",
                        "sdp": "expired fixture offer",
                    }
                )
                assert (await expired_socket.receive_json())["type"] == "revoked"
            metrics["media_admission_statuses"] = admissions
            metrics["checks"].append(
                "real host denies unauthenticated, cross-account/chat, stale generation, forged owner, public extension and expired media admission"
            )
            await page.locator(".agent-browser-takeover").click()
            await page.wait_for_function(
                "document.querySelector('.agent-browser-view')?.dataset.humanControl==='false'"
            )
            await page.wait_for_function(
                "__videoProof.acks.some(a=>a.operation==='release'&&a.status==='applied'&&a.state?.control_mode==='agent')"
            )
            old_media = await page.locator(".agent-browser-view").get_attribute(
                "data-media-session-id"
            )
            await page.locator(
                ".git-diff-sidebar-header button[aria-label]"
            ).last.click()
            await page.wait_for_function(
                "__videoProof.peers.every(p=>p.connectionState==='closed')"
            )
            private_process = next(
                p
                for p in psutil.Process(host.pid).children(recursive=True)
                if any("mcp_entry.py" in arg for arg in p.cmdline())
            )
            for _ in range(60):
                udp = private_process.net_connections(kind="udp")
                if not udp:
                    break
                await asyncio.sleep(0.1)
            assert not udp, "Media UDP sockets survived the last viewer"
            metrics["last_viewer_udp_sockets"] = len(udp)
            await open_agent_panel(page)
            await page.locator('.agent-browser-view[data-video-phase="live"]').wait_for(
                timeout=20000
            )
            assert (
                await page.locator(".agent-browser-view").get_attribute(
                    "data-media-session-id"
                )
                != old_media
            )
            metrics["checks"].append(
                "explicit handback refreshes Agent observation; sidebar close retires peers and last-viewer UDP; reopening keeps the browser with a fresh media session"
            )
            await page.locator(".agent-browser-stop").click()
            await page.locator(
                '.agent-browser-view[data-connection="stopped"]'
            ).wait_for()
            assert await target.evaluate("fixtureDocument") == nonce
            await page.wait_for_function(
                "document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.videoPhase==='live'"
            )
            await page.locator(".agent-browser-close").click()
            await page.locator(
                '.agent-browser-view[data-connection="closed"]'
            ).wait_for(timeout=20000)
            assert not await page.locator(".agent-browser-viewport video").is_visible()
            assert await page.locator(".agent-browser-viewport video").evaluate(
                "v=>v.srcObject===null"
            )
            metrics["checks"].append(
                "stop preserves actual Chromium and read-only video; browser close clears pixels and closes media"
            )
            ordinary_context = await viewer.new_context(
                viewport={"width": 1600, "height": 1000}
            )
            await ordinary_context.add_init_script(
                path=str(ROOT / "scripts/browser_video_production_instrument.js")
            )
            await ordinary_context.route(
                re.compile(r"^https?://(?!127\.0\.0\.1[:/]|localhost[:/])"),
                lambda route: route.abort(),
            )
            ordinary_page = await ordinary_context.new_page()
            await ordinary_page.goto(origin + "/#/auth/login")
            await ordinary_page.locator('input[name="username"]').fill("v02-user")
            await ordinary_page.locator('input[name="password"]').fill(
                ordinary_password
            )
            await ordinary_page.locator('button[type="submit"]').click()
            await ordinary_page.wait_for_function("!!localStorage.getItem('token')")
            await ordinary_page.goto(origin + f"/#/chat/{ordinary_chat}")
            await ordinary_page.locator(".chat-text-input, .chat-textarea").wait_for(
                timeout=60000
            )
            await open_agent_panel(ordinary_page)
            await ordinary_page.locator(".chat-text-input, .chat-textarea").fill(
                "V02_OPEN"
            )
            await ordinary_page.locator(".send-btn[aria-label]").click()
            await ordinary_page.get_by_text(
                "V02_BROWSER_READY", exact=False
            ).first.wait_for(timeout=120000)
            await asyncio.sleep(2)
            await open_agent_panel(ordinary_page)
            await ordinary_page.locator(".agent-browser-refresh").click()
            page = ordinary_page
            await ordinary_page.locator(
                '.agent-browser-view[data-video-phase="live"]'
            ).wait_for(timeout=20000)
            await ordinary_page.locator(".agent-browser-takeover").click()
            await ordinary_page.locator(
                '.agent-browser-view[data-human-control="true"]'
            ).wait_for(timeout=20000)
            await ordinary_page.wait_for_function(
                "document.querySelector('.agent-browser-view')?.dataset.frameId"
            )
            ordinary_state = await ordinary_page.locator(
                ".agent-browser-view"
            ).evaluate("el=>({...el.dataset})")
            assert (
                ordinary_state["browserSessionId"] != ticket_body["browser_session_id"]
            )
            async with client.post(
                origin + f"/api/v1/accounts/{ordinary_id}/disable", headers=auth
            ) as response:
                assert response.status == 200, await response.text()
            await ordinary_page.wait_for_function(
                "!document.querySelector('.agent-browser-view')?.dataset.frameId && !document.querySelector('.agent-browser-view')?.dataset.generation && !document.querySelector('.agent-browser-viewport video')?.srcObject",
                timeout=15000,
            )
            async with client.post(
                media_ticket_url,
                headers=ordinary_auth,
                json={
                    "chat_id": ordinary_chat,
                    "generation": ordinary_state["generation"],
                    "browser_session_id": ordinary_state["browserSessionId"],
                },
            ) as response:
                assert response.status == 401
            metrics["checks"].append(
                "new ordinary owner receives a different browser; disabling its persisted account clears active video and input and revokes old tickets"
            )
            async with client.post(
                origin + f"/api/v1/accounts/{ordinary_id}/enable", headers=auth
            ) as response:
                assert response.status == 200
            await ordinary_page.goto(origin + "/#/auth/login")
            await ordinary_page.locator('input[name="username"]').fill("v02-user")
            await ordinary_page.locator('input[name="password"]').fill(
                ordinary_password
            )
            await ordinary_page.locator('button[type="submit"]').click()
            await ordinary_page.wait_for_function("!!localStorage.getItem('token')")
            await ordinary_page.goto(origin + f"/#/chat/{ordinary_chat}")
            await ordinary_page.locator(".chat-text-input, .chat-textarea").wait_for(
                timeout=60000
            )
            await open_agent_panel(ordinary_page)
            await ordinary_page.locator(".agent-browser-refresh").click()
            await ordinary_page.locator(
                '.agent-browser-view[data-video-phase="live"]'
            ).wait_for(timeout=20000)
            private_process = next(
                p
                for p in psutil.Process(host.pid).children(recursive=True)
                if any("mcp_entry.py" in arg for arg in p.cmdline())
            )
            owned_private = [
                (p.pid, p.create_time())
                for p in [private_process, *private_process.children(recursive=True)]
            ]
            async with client.patch(
                origin + "/api/v1/plugins/astrbot_plugin_browser_use/enabled",
                headers=auth,
                json={"enabled": False},
            ) as response:
                assert response.status == 200
            await ordinary_page.wait_for_function(
                "!document.querySelector('.agent-browser-view')?.dataset.frameId && !document.querySelector('.agent-browser-viewport video')?.srcObject",
                timeout=15000,
            )
            for _ in range(150):
                survivors = []
                for pid, created in owned_private:
                    with contextlib.suppress(psutil.Error):
                        process = psutil.Process(pid)
                        if process.is_running() and process.create_time() == created:
                            survivors.append(pid)
                if not survivors:
                    break
                await asyncio.sleep(0.1)
            assert not survivors, survivors
            metrics["plugin_disable_survivors"] = survivors
            metrics["checks"].append(
                "disabling actual plugin while video is active clears pixels and retires every owned MCP/Chromium descendant before host shutdown"
            )
            metrics["passed"] = True
    finally:
        metrics["ui_errors"] = errors
        if page and not metrics["passed"]:
            with contextlib.suppress(Exception):
                await page.screenshot(path=str(out / "failure.png"))
                metrics["failure_ui"] = await page.locator(
                    ".agent-browser-view"
                ).evaluate("el=>({...el.dataset,text:el.innerText})")
                metrics["media_diagnostic"] = await page.evaluate(
                    "({protocol:__videoProof.protocol,peers:__videoProof.peers.map(p=>p.connectionState),VideoFrame:typeof VideoFrame,callback:typeof document.querySelector('video')?.requestVideoFrameCallback})"
                )
                proof = await page.evaluate(
                    "({metadata:__videoProof.metadata,frames:__videoProof.frames,commands:__videoProof.commands,acks:__videoProof.acks,inputs:__videoProof.inputs})"
                )
                (out / "failure-transport.json").write_text(
                    json.dumps(proof, indent=2), encoding="utf-8"
                )
        if host and host.poll() is None and not metrics["passed"]:
            with contextlib.suppress(Exception):
                for child in psutil.Process(host.pid).children(recursive=True):
                    if any("mcp_entry.py" in arg for arg in child.cmdline()):
                        private_token = child.environ()["ASTRBOT_BROWSER_BRIDGE_TOKEN"]
                        bridge_path = Path(
                            child.environ()["ASTRBOT_BROWSER_BRIDGE_FILE"]
                        )
                        bridge = json.loads(bridge_path.read_text(encoding="utf-8"))
                        async with aiohttp.ClientSession() as diagnostic:
                            async with diagnostic.get(
                                f"http://127.0.0.1:{bridge['port']}/snapshot",
                                params={"generation": bridge["generation"]},
                                headers={"Authorization": f"Bearer {private_token}"},
                            ) as response:
                                metrics["private_snapshot_diagnostic"] = {
                                    "status": response.status,
                                    "error": (await response.text())[:200]
                                    if response.status != 200
                                    else "current snapshot valid",
                                }
                        break
        if host and host.poll() is None and auth:
            with contextlib.suppress(Exception):
                async with aiohttp.ClientSession() as client:
                    async with client.patch(
                        origin + "/api/v1/plugins/astrbot_plugin_browser_use/enabled",
                        headers=auth,
                        json={"enabled": False},
                    ) as response:
                        metrics["final_plugin_disable"] = response.status
                        await response.read()
        if private_browser:
            with contextlib.suppress(Exception):
                await private_browser.close()
        if second_viewer:
            await second_viewer.close()
        if viewer:
            await viewer.close()
        if playwright:
            await playwright.stop()
        if host and host.poll() is None:
            host.terminate()
            await asyncio.to_thread(host.wait, 15)
        await runner.cleanup()
        (out / "result.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out / "model-requests.json").write_text(
            json.dumps(model_requests, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(metrics, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-gate", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--plugin-source", type=Path, required=True)
    parser.add_argument("--private-python", type=Path, required=True)
    parser.add_argument("--chromium", type=Path, required=True)
    args = parser.parse_args()
    deadline = time.monotonic() + 20
    while args.start_gate and not args.start_gate.exists():
        if time.monotonic() > deadline:
            raise RuntimeError("Process ownership gate was not established")
        time.sleep(0.02)
    asyncio.run(main(args))
