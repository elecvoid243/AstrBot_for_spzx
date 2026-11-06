"""Exercise production ChatUI media with the actual isolated Browser Use plugin.

The package mode runs the shipped installer and can verify matched Dashboard
and backend archives. Network guards simulate blocked egress, not an airgap.
The parent launcher owns the entire test/host/runtime/viewer tree in a Job Object.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import ipaddress
import json
import os
import platform
import re
import secrets
import shutil
import socket
import statistics
import subprocess
import sys
import time
import zipfile
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
    page, target, mode, first_case, out, metrics, fixture_origin, samples=6
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
        samples: Matched native actions, in groups of six.
    """
    await page.evaluate("mode=>__videoProof.mode=mode", mode)
    traffic_before = await page.evaluate("({...__videoProof.traffic})")
    await page.evaluate("__videoProof.resetPresentation()")
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
    service_before = await page.evaluate("__videoProof.serviceStats.slice(-1)")
    actions = []
    await page.evaluate(
        "width=>__videoProof.cssWidth=width", await target.evaluate("innerWidth")
    )
    for offset in range(samples):
        sample_index = offset % 6 + 1
        case_id = first_case + offset
        await target.evaluate("id=>probe.pending=id", case_id)
        await page.evaluate("id=>__videoProof.caseId=id", case_id)
        for attempt in range(3):
            previous_inputs = await page.evaluate("__videoProof.inputs.length")
            await page.wait_for_function(
                "document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.pendingInput === '0' && document.querySelector('.agent-browser-view')?.dataset.awaitingFrame==='false'"
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
                await page.mouse.wheel(
                    0, 320 if sample_index == 5 else (-320 if samples > 6 else -160)
                )
            await page.wait_for_function(
                "n=>__videoProof.inputs.length>n", arg=previous_inputs
            )
            await page.wait_for_function(
                "id=>__videoProof.commands.some(c=>c.caseId===id&&['pointer','wheel'].includes(c.operation))||document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0'",
                arg=case_id,
            )
            if await page.evaluate(
                "id=>__videoProof.commands.some(c=>c.caseId===id&&['pointer','wheel'].includes(c.operation))",
                case_id,
            ):
                break
            assert (
                await page.locator(".agent-browser-view").get_attribute(
                    "data-human-control"
                )
                == "true"
            )
            metrics.setdefault("unmapped_input_attempts", []).append(
                {
                    "mode": mode,
                    "case_id": case_id,
                    "attempt": attempt + 1,
                    "wire_pointer_or_wheel_commands": 0,
                }
            )
        else:
            raise AssertionError(
                f"No mapped input admission after three UI attempts: {mode}/{case_id}"
            )
        await page.wait_for_function(
            "id=>__videoProof.frames.some(f=>f.valid && f.mapped!==false && f.actual[1]===id)",
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
    feedback = {}
    for case_id in range(first_case, first_case + samples):
        sent = next(i for i in proof["inputs"] if i["caseId"] == case_id)
        presented = next(
            f
            for f in proof["frames"]
            if f["valid"] and f.get("mapped", True) and f["actual"][1] == case_id
        )
        latencies.append(presented["t"] - sent["t"])
        feedback[case_id] = presented
    actual = [
        f
        for f in proof["frames"]
        if f["valid"]
        and f.get("mapped", True)
        and f["mode"] == mode
        and f["t"] >= measurement_started
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
        "p50_ms": statistics.median(latencies),
        "p95_ms": ordered[index]
        + (ordered[min(index + 1, len(ordered) - 1)] - ordered[index])
        * (position - index),
        "max_ms": max(latencies),
        "p95_method": f"linear quantile; {samples} matched samples, not a stability guarantee",
        "sample_window_ms": await page.evaluate(
            "performance.timeOrigin+performance.now()"
        )
        - measurement_started,
        "presented_frames": len(actual),
        "unique_source_frames": len({f["words"][3] for f in actual}),
        "repeated_source_frames": len(actual) - len({f["words"][3] for f in actual}),
        "dimensions": sorted({(f["width"], f["height"]) for f in actual}),
        "css_viewport": await target.evaluate(
            "({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})"
        ),
        "cpu_core_percent": cpu_seconds / (time.monotonic() - cpu_started) * 100,
        "rss_bytes_end": rss_bytes,
        "resource_scope": "owned acceptance/host/private browser/receiver tree, including optical sampling; one logical core is 100 percent",
        "rtp_stats_before": stats_before,
        "rtp_stats_after": await page.evaluate("__videoProof.statistics()"),
        "service_stats_before": service_before,
        "service_stats_after": await page.evaluate(
            "__videoProof.serviceStats.slice(-1)"
        ),
        "receiver_presentation": await page.evaluate("__videoProof.presentation"),
        "application_bytes": {
            name: value - traffic_before[name]
            for name, value in (await page.evaluate("__videoProof.traffic")).items()
        },
        "source_paint_to_present_ms": [
            feedback[case]["t"] - fixture_origin - feedback[case]["words"][0]
            for case in range(first_case, first_case + samples)
        ],
        "source_change_to_captured_paint_ms": [
            feedback[case]["words"][0] - feedback[case]["actual"][2]
            for case in range(first_case, first_case + samples)
        ],
        "input_to_native_event_ms": [
            fixture_origin
            + feedback[case]["actual"][2]
            - next(i["t"] for i in proof["inputs"] if i["caseId"] == case)
            for case in range(first_case, first_case + samples)
        ],
        "presented_fps": (len(actual) - 1)
        / ((actual[-1]["t"] - actual[0]["t"]) / 1000),
        "valid_frames": len(actual),
        "clock": "first receiver input event to optically decoded actual fixture state, including separately recorded unsent attempts; performance.timeOrigin+now; "
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


async def fit_native_hd(page, target):
    """Use real fullscreen/container resize to request a native 1080p viewport.

    Args:
        page: Actual production receiver with an acquired human control lease.
        target: Read-only inspection of the private website's native dimensions.
    """
    await page.set_viewport_size({"width": 1943, "height": 1116})
    if (
        await page.locator(".agent-browser-fullscreen").get_attribute("aria-pressed")
        != "true"
    ):
        await page.locator(".agent-browser-fullscreen").click()
    for _ in range(3):
        dimensions = await page.locator(".agent-browser-image").evaluate(
            "el=>{const s=getComputedStyle(el);return {width:el.clientWidth-parseFloat(s.paddingLeft)-parseFloat(s.paddingRight),height:el.clientHeight-parseFloat(s.paddingTop)-parseFloat(s.paddingBottom)}}"
        )
        size = page.viewport_size
        await page.set_viewport_size(
            {
                "width": round(size["width"] + 1920 - dimensions["width"]),
                "height": round(size["height"] + 1080 - dimensions["height"]),
            }
        )
        await asyncio.sleep(0.6)
    await target.wait_for_function(
        "innerWidth===1920 && innerHeight===1080", timeout=15000
    )
    # Container adjustments and their ordered resize ACK must settle before
    # measuring the new pixels, especially after explicit JPEG selection.
    await asyncio.sleep(1)
    await page.wait_for_function(
        """()=>{const r=document.querySelector('.agent-browser-view');
        if(!r?.dataset.frameId||r.dataset.pendingInput!=='0'||r.dataset.awaitingFrame!=='false')return false;
        const v=document.querySelector('.agent-browser-viewport video'),i=document.querySelector('.agent-browser-viewport img');
        return r.dataset.mediaSessionId?v?.videoWidth===1920&&v.videoHeight===1080:i?.naturalWidth===1920&&i.naturalHeight===1080}""",
        timeout=15000,
    )


async def exercise_four_viewers(
    playwright,
    chromium,
    origin,
    chat_id,
    context,
    page,
    second_page,
    target,
    host,
    seconds,
    out,
    metrics,
):
    """Measure four independent real receivers while the original lease stays held.

    Args:
        playwright: Owned browser driver.
        chromium: Installed private browser executable.
        origin: Isolated production host URL.
        chat_id: Current owned ChatUI session.
        context: Original authenticated receiver context.
        page: Original human controller.
        second_page: Already connected read-only receiver.
        target: Actual private website.
        host: Owned host process.
        seconds: Full four-viewer measurement duration.
        out: Isolated evidence directory.
        metrics: Acceptance results.
    """
    extra = []
    pages = [page, second_page]
    try:
        for _ in range(2):
            browser = await playwright.chromium.launch(
                executable_path=str(chromium),
                headless=True,
                ignore_default_args=["--hide-scrollbars"],
            )
            extra.append(browser)
            receiver = await browser.new_context(
                storage_state=await context.storage_state(),
                viewport={"width": 1600, "height": 1000},
            )
            await receiver.add_init_script(
                path=str(ROOT / "scripts/browser_video_production_instrument.js")
            )
            await receiver.route(
                re.compile(r"^https?://(?!127\.0\.0\.1[:/]|localhost[:/])"),
                lambda route: route.abort(),
            )
            receiver_page = await receiver.new_page()
            await receiver_page.goto(origin + f"/#/chat/{chat_id}")
            await receiver_page.locator(".chat-text-input, .chat-textarea").wait_for(
                timeout=60000
            )
            await open_agent_panel(receiver_page)
            await receiver_page.locator(
                '.agent-browser-view[data-video-phase="live"]'
            ).wait_for(timeout=30000)
            pages.append(receiver_page)
        identities = [
            await p.locator(".agent-browser-view").get_attribute(
                "data-media-session-id"
            )
            for p in pages
        ]
        assert len(set(identities)) == 4 and all(identities)
        for p in pages[1:]:
            assert await p.locator(".agent-browser-takeover").is_disabled()
        await fit_native_hd(page, target)
        for p in pages:
            await p.evaluate(
                "__videoProof.attach();__videoProof.optical=false;__videoProof.resetPresentation()"
            )
        await target.evaluate(
            """() => {const c=document.createElement('canvas');c.width=180;c.height=80;c.style='position:fixed;left:1050px;top:400px';document.body.append(c);const ctx=c.getContext('2d');let n=0;function paint(){ctx.fillStyle=n%2?'#ffb000':'#00b0ff';ctx.fillRect(0,0,180,80);ctx.fillStyle='#000';ctx.font='24px sans-serif';ctx.fillText(String(n++),10,50);window.v03Animation=requestAnimationFrame(paint)}paint()}"""
        )
        child = next(frame for frame in target.frames if "/contract-child" in frame.url)
        await child.evaluate(
            "window.v03Scroll=setInterval(()=>scroller.scrollTop=scroller.scrollTop?0:120,4500)"
        )
        source = next(
            p
            for p in psutil.Process(host.pid).children(recursive=True)
            if any("mcp_entry.py" in arg for arg in p.cmdline())
        )
        cpu_before = {}
        for process in [source, *source.children(recursive=True)]:
            with contextlib.suppress(psutil.Error):
                cpu_before[(process.pid, process.create_time())] = sum(
                    process.cpu_times()[:2]
                )
        snapshots = []
        receiver_systems = [
            await p.context.browser.new_browser_cdp_session() for p in pages
        ]
        receiver_cpu = []
        for system in receiver_systems:
            receiver_cpu.append(
                {
                    item["id"]: item["cpuTime"]
                    for item in (await system.send("SystemInfo.getProcessInfo"))[
                        "processInfo"
                    ]
                }
            )
        started = time.monotonic()
        next_action = started + 5
        next_sample = started
        clicks = []
        while time.monotonic() - started < seconds:
            if time.monotonic() >= next_action:
                await page.wait_for_function(
                    "document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0'",
                    timeout=15000,
                )
                before = await target.evaluate("hits.length")
                surface = await page.locator(".agent-browser-viewport").bounding_box()
                viewport = await target.evaluate(
                    "({width:innerWidth,height:innerHeight})"
                )
                await page.mouse.click(*viewport_point(surface, viewport, 160, 200))
                await page.wait_for_function(
                    "document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0'",
                    timeout=15000,
                )
                native = await target.evaluate("hits.slice()")
                assert len(native) in {before, before + 1}
                if len(native) > before:
                    assert native[-1] == "B"
                clicks.append(
                    {
                        "elapsed_s": time.monotonic() - started,
                        "hit": len(native) > before,
                    }
                )
                next_action = time.monotonic() + 5
            if time.monotonic() >= next_sample:
                receivers = []
                for index, p in enumerate(pages):
                    receivers.append(
                        await p.evaluate(
                            "({presentation:__videoProof.presentation,peerStates:__videoProof.peers.map(p=>p.connectionState),lastRecords:__videoProof.metadata.slice(-4),serviceStats:__videoProof.serviceStats.slice(-1),visibility:document.visibilityState})"
                        )
                    )
                    receivers[-1]["rtp"] = await p.evaluate("__videoProof.statistics()")
                    native = (
                        await receiver_systems[index].send("SystemInfo.getProcessInfo")
                    )["processInfo"]
                    receivers[-1]["cpu_seconds"] = sum(
                        max(
                            0,
                            item["cpuTime"]
                            - receiver_cpu[index].get(item["id"], item["cpuTime"]),
                        )
                        for item in native
                    )
                    receivers[-1]["rss_bytes"] = 0
                    for item in native:
                        with contextlib.suppress(psutil.Error):
                            receivers[-1]["rss_bytes"] += (
                                psutil.Process(item["id"]).memory_info().rss
                            )
                rss = cpu = 0
                non_loopback_tcp = []
                for process in [source, *source.children(recursive=True)]:
                    with contextlib.suppress(psutil.Error):
                        rss += process.memory_info().rss
                        cpu += max(
                            0,
                            sum(process.cpu_times()[:2])
                            - cpu_before.get(
                                (process.pid, process.create_time()),
                                sum(process.cpu_times()[:2]),
                            ),
                        )
                        for connection in process.net_connections(kind="tcp"):
                            if (
                                connection.raddr
                                and connection.status == "ESTABLISHED"
                                and not ipaddress.ip_address(
                                    connection.raddr.ip
                                ).is_loopback
                            ):
                                non_loopback_tcp.append(
                                    {
                                        "pid": process.pid,
                                        "peer": connection.raddr.ip,
                                        "port": connection.raddr.port,
                                    }
                                )
                assert not non_loopback_tcp, non_loopback_tcp
                snapshots.append(
                    {
                        "elapsed_s": time.monotonic() - started,
                        "source_tree_cpu_seconds": cpu,
                        "source_tree_rss": rss,
                        "non_loopback_established_tcp": non_loopback_tcp,
                        "receivers": receivers,
                    }
                )
                (out / "four-viewers-progress.json").write_text(
                    json.dumps(
                        {
                            "identities": identities,
                            "clicks": clicks,
                            "snapshots": snapshots,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                next_sample = time.monotonic() + 5
            assert (
                await page.locator(".agent-browser-view").get_attribute(
                    "data-human-control"
                )
                == "true"
            )
            await asyncio.sleep(0.25)
        measurement_duration = time.monotonic() - started
        for system in receiver_systems:
            await system.detach()
        for p in pages:
            presentation = await p.evaluate("__videoProof.presentation")
            assert (
                presentation["callbacks"] > seconds * 2
                and presentation["repeatedRtp"] == 0
                and presentation["mapped"] > seconds * 2
                and presentation["uniqueSource"] > seconds * 2
                and presentation["repeatedSource"] == 0
            ), presentation
        await child.evaluate("clearInterval(window.v03Scroll)")
        await target.evaluate("cancelAnimationFrame(window.v03Animation)")
        for _ in range(3):
            old = (
                await pages[2]
                .locator(".agent-browser-view")
                .get_attribute("data-media-session-id")
            )
            await (
                pages[2]
                .locator(".git-diff-sidebar-header button[aria-label]")
                .last.click()
            )
            await pages[2].wait_for_function(
                "__videoProof.peers.every(p=>p.connectionState==='closed')"
            )
            await open_agent_panel(pages[2])
            await (
                pages[2]
                .locator('.agent-browser-view[data-video-phase="live"]')
                .wait_for(timeout=30000)
            )
            assert (
                await pages[2]
                .locator(".agent-browser-view")
                .get_attribute("data-media-session-id")
                != old
            )
        metrics["four_viewers"] = {
            "duration_s": time.monotonic() - started,
            "required_window_s": seconds,
            "measurement_window_s": measurement_duration,
            "identities": identities,
            "clicks": clicks,
            "snapshots": snapshots,
            "reopen_cycles": 3,
        }
        metrics["checks"].append(
            "four independent real video receivers sustain animation/scrolling for the required window; one controller, native hits, separate RTP records and three explicit reopen cycles"
        )
        await page.locator(".agent-browser-fullscreen").click()
        await page.set_viewport_size({"width": 1600, "height": 1000})
    finally:
        for browser in extra:
            await browser.close()


async def exercise_terminal_faults(page, target, child, out, metrics):
    """Release original native sessions across explicit UI lifetime interruptions.

    Args:
        page: Actual ChatUI human controller.
        target: Owned private top-level website, inspected without injecting input.
        child: Actual cross-origin frame receiving the native input.
        out: Isolated evidence output.
        metrics: Machine-readable acceptance results.
    """
    await child.evaluate(
        "window.v03Keys=[];addEventListener('keydown',e=>v03Keys.push([e.type,e.code]));addEventListener('keyup',e=>v03Keys.push([e.type,e.code]))"
    )
    for fault, held_kind in (
        ("control-disconnect", "pointer"),
        ("sidebar-close", "pointer"),
        ("control-disconnect", "key"),
        ("sidebar-close", "key"),
    ):
        await page.wait_for_function(
            "document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0'"
        )
        surface = await page.locator(".agent-browser-viewport").bounding_box()
        viewport = await target.evaluate("({width:innerWidth,height:innerHeight})")
        await page.mouse.move(*viewport_point(surface, viewport, 800, 190))
        if held_kind == "pointer":
            await page.mouse.down()
            await child.wait_for_function(
                "a.matches(':active')||b.matches(':active')", timeout=10000
            )
        else:
            await page.mouse.click(*viewport_point(surface, viewport, 800, 190))
        await page.wait_for_function(
            "document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0'"
        )
        before = await child.evaluate("hits.length")
        if held_kind == "key":
            await page.keyboard.down("Control")
            await child.wait_for_function(
                "v03Keys.filter(e=>e[0]==='keydown'&&e[1]==='ControlLeft').length>v03Keys.filter(e=>e[0]==='keyup'&&e[1]==='ControlLeft').length"
            )
        if fault == "sidebar-close":
            await page.locator(
                ".git-diff-sidebar-header button[aria-label]"
            ).last.evaluate("button=>button.click()")
        else:
            await page.evaluate(
                "__videoProof.sockets.findLast(s=>s.url.includes('browser-control')).close(4001,'acceptance held pointer disconnect')"
            )
        if held_kind == "pointer":
            await child.wait_for_function(
                "!a.matches(':active')&&!b.matches(':active')", timeout=10000
            )
            await page.mouse.up()
        assert await child.evaluate("hits.length") == before
        if held_kind == "key":
            await page.keyboard.up("Control")
            await child.wait_for_function(
                "v03Keys.filter(e=>e[0]==='keyup'&&e[1]==='ControlLeft').length>=v03Keys.filter(e=>e[0]==='keydown'&&e[1]==='ControlLeft').length"
            )
        if fault == "sidebar-close":
            await open_agent_panel(page)
        await page.locator(".agent-browser-refresh").click()
        await page.wait_for_function(
            "document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.humanControl==='false'"
        )
        await page.locator(".agent-browser-takeover").click()
        await page.wait_for_function(
            "document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.humanControl==='true'"
        )
        metrics["checks"].append(
            f"held native iframe {held_kind} releases without extra click on {fault}; explicit refresh/takeover recovers"
        )
    for held_kind in ("pointer", "key"):
        await asyncio.sleep(1)
        await page.wait_for_function(
            "document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0' && document.querySelector('.agent-browser-view')?.dataset.awaitingFrame==='false'"
        )
        surface = await page.locator(".agent-browser-viewport").bounding_box()
        viewport = await target.evaluate("({width:innerWidth,height:innerHeight})")
        point = viewport_point(surface, viewport, 800, 190)
        if held_kind == "pointer":
            await page.mouse.move(*point)
            await page.mouse.down()
            await child.wait_for_function("a.matches(':active')||b.matches(':active')")
        else:
            await page.mouse.click(*point)
            await page.wait_for_function(
                "document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0' && document.querySelector('.agent-browser-view')?.dataset.awaitingFrame==='false' && document.querySelector('.agent-browser-view')?.dataset.frameId"
            )
            await page.keyboard.down("Control")
            await child.wait_for_function(
                "v03Keys.filter(e=>e[0]==='keydown'&&e[1]==='ControlLeft').length>v03Keys.filter(e=>e[0]==='keyup'&&e[1]==='ControlLeft').length"
            )
        before = await child.evaluate("hits.length")
        original_url = page.url
        await page.get_by_text("创建对话", exact=True).first.evaluate(
            "button=>button.click()"
        )
        await page.wait_for_url(lambda url: str(url) != original_url)
        if held_kind == "pointer":
            await page.mouse.up()
            await child.wait_for_function(
                "!a.matches(':active')&&!b.matches(':active')"
            )
        else:
            await page.keyboard.up("Control")
            await child.wait_for_function(
                "v03Keys.filter(e=>e[0]==='keyup'&&e[1]==='ControlLeft').length>=v03Keys.filter(e=>e[0]==='keydown'&&e[1]==='ControlLeft').length"
            )
        assert await child.evaluate("hits.length") == before
        await (
            page.locator(".session-item")
            .filter(has_text="V02_BROWSER_READY")
            .first.click()
        )
        await page.wait_for_url(original_url)
        await open_agent_panel(page)
        await page.wait_for_function(
            "document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.humanControl==='false'"
        )
        await page.locator(".agent-browser-takeover").click()
        await page.wait_for_function(
            "document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.humanControl==='true'"
        )
        metrics["checks"].append(
            f"switching chats while a native iframe {held_kind} is held releases its original session; explicit return/takeover recovers without replay"
        )
    await page.screenshot(path=str(out / "terminal-faults-recovered.png"))


async def main(args):
    """Run owned production services and save source/transport/pixel evidence.

    Args:
        args: Explicit isolated output and startup gate arguments.
    """
    plugin = args.plugin_source.resolve()
    private_python = args.private_python.resolve() if args.private_python else None
    chromium = args.chromium.resolve() if args.chromium else None
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    runroot = out / ("安装后的 AstrBot" if args.plugin_zip else "isolated-host")
    data = runroot / "data"
    installed = data / "plugins/astrbot_plugin_browser_use"
    host_source = ROOT
    dashboard_dist = ROOT / "dashboard/dist"
    if args.plugin_zip:
        if private_python or chromium:
            raise ValueError(
                "Final package acceptance cannot borrow a runtime override."
            )
        distribution = out / "离线 安装包"
        with zipfile.ZipFile(args.plugin_zip) as archive:
            archive.extractall(distribution)
        installed.parent.mkdir(parents=True)
        persistent = (
            data / "plugin_data/astrbot_plugin_browser_use/existing-user-data.txt"
        )
        persistent.parent.mkdir(parents=True)
        persistent.write_text("preserve", encoding="utf-8")
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
        first = await asyncio.to_thread(
            subprocess.run,
            command,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert first.returncode == 0, first.stderr.decode(errors="replace")
        refused = await asyncio.to_thread(
            subprocess.run,
            command,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert refused.returncode != 0
        installed.rename(out / "旧插件备份")
        reinstalled = await asyncio.to_thread(
            subprocess.run,
            command,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert reinstalled.returncode == 0, reinstalled.stderr.decode(errors="replace")
        assert persistent.read_text(encoding="utf-8") == "preserve"
        private_python = installed / "runtime/python/python.exe"
        chromium = installed / "runtime/chromium/chrome.exe"
        manifest = json.loads(
            (installed / "runtime/manifest.json").read_text(encoding="utf-8")
        )
        for name, digest in manifest["source_sha256"].items():
            assert (
                hashlib.sha256((installed / name).read_bytes()).hexdigest() == digest
            ), name
        for name, digest in manifest["installer_sha256"].items():
            assert (
                hashlib.sha256((distribution / name).read_bytes()).hexdigest() == digest
            )
        if args.dashboard_zip:
            assert (
                hashlib.sha256(args.dashboard_zip.read_bytes()).hexdigest()
                == manifest["dashboard_sha256"]
            )
            dashboard_root = out / "dashboard-package"
            with zipfile.ZipFile(args.dashboard_zip) as archive:
                archive.extractall(dashboard_root)
            dashboard_dist = dashboard_root / "dist"
            build = json.loads(
                (dashboard_dist / "assets/browser-observer-build.json").read_text(
                    encoding="utf-8"
                )
            )
            assert build["astrbot_revision"] == manifest["astrbot_revision"]
            assert build["plugin_revision"] == manifest["plugin_revision"]
            assert build["plugin_version"] == manifest["plugin_version"]
            backend_archive = args.dashboard_zip.parent / build["backend_source_file"]
            assert (
                hashlib.sha256(backend_archive.read_bytes()).hexdigest()
                == build["backend_source_sha256"]
            )
            host_source = out / "backend"
            with zipfile.ZipFile(backend_archive) as archive:
                archive.extractall(host_source)
            assert (
                await asyncio.to_thread(
                    subprocess.check_output,
                    ["git", "rev-parse", "HEAD"],
                    cwd=ROOT,
                    text=True,
                )
            ).strip() == build["astrbot_revision"]
        runtime_check = await asyncio.to_thread(
            subprocess.run,
            [
                str(private_python),
                "-X",
                "utf8",
                "-B",
                str(installed / "scripts/check_runtime.py"),
                "--report",
                str(out / "installed-runtime-check.json"),
            ],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert runtime_check.returncode == 0, runtime_check.stderr.decode(
            errors="replace"
        )
        check = json.loads(
            (out / "installed-runtime-check.json").read_text(encoding="utf-8")
        )
        assert all(
            Path(path).resolve().is_relative_to(installed)
            for path in check["sys_path"]
            if path
        )
    else:
        if private_python is None or chromium is None:
            raise ValueError(
                "Source acceptance requires explicit private Python and Chromium."
            )
        installed.mkdir(parents=True)
        for source in plugin.iterdir():
            if source.is_file() and source.suffix in {".py", ".json", ".yaml", ".txt"}:
                shutil.copy2(source, installed / source.name)
    metrics = {"passed": False, "stage": "V02 production source checkout", "checks": []}
    if args.plugin_zip:
        metrics.update(
            stage="V03 matched final packages"
            if args.dashboard_zip
            else "V03 candidate plugin package with source host",
            plugin_zip_sha256=hashlib.sha256(args.plugin_zip.read_bytes()).hexdigest(),
            manifest=manifest,
        )
        metrics["checks"].append(
            "shipped installer installs through Chinese/space paths, refuses overwrite and reinstalls after moving old source; private imports, source hashes and persistent data verified"
        )
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
    model_waiting, model_release = asyncio.Event(), asyncio.Event()
    website_password = secrets.token_urlsafe(18)
    website_cookie = secrets.token_urlsafe(18)
    website_login = {"submitted": 0, "continued": 0, "stale": 0}
    handback_marker = secrets.token_hex(8) if args.stale_handback else ""
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
        if request.path == "/login":
            if request.method == "POST":
                form = await request.post()
                if (
                    form.get("username") != "Offline 用户"
                    or form.get("password") != website_password
                    or int(form.get("slider", "0")) < 70
                ):
                    return web.Response(
                        status=401,
                        text="Local fixture credentials or slider were incorrect.",
                    )
                website_login["submitted"] += 1
                response = web.HTTPSeeOther("/authenticated")
                response.set_cookie(
                    "v03_login", website_cookie, httponly=True, samesite="Lax"
                )
                return response
            return web.Response(
                content_type="text/html",
                text="""<!doctype html><meta charset="utf-8"><title>Local manual login</title><style>body{font:20px system-ui;margin:40px}input{display:block;width:320px;height:36px;margin:18px 0}button{height:40px;padding:0 24px}</style><h1>Ordinary local test account</h1><form method="post" action="/login"><input name="username" autocomplete="off" placeholder="Local test username"><input name="password" type="password" placeholder="Local test password"><input name="slider" type="range" min="0" max="100" value="10"><button type="submit">Sign in locally</button></form>""",
            )
        if request.path in {"/authenticated", "/continued"}:
            if request.cookies.get("v03_login") != website_cookie:
                return web.Response(status=403, text="Sign in first")
            if request.path == "/continued":
                website_login["continued"] += 1
            return web.Response(
                content_type="text/html",
                text="""<!doctype html><meta charset="utf-8"><title>Signed in local account</title><style>body{min-height:2000px}</style><h1>Signed in: Offline 用户</h1><form action="/continued"><button>Continue signed-in task</button></form><script>addEventListener('click',()=>sessionStorage.setItem('authenticatedClicks',String(Number(sessionStorage.getItem('authenticatedClicks')||0)+1)))</script>""",
            )
        if request.path == "/stale-login-action":
            website_login["stale"] += 1
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
        login_start = next(
            (
                i
                for i in range(len(messages) - 1, -1, -1)
                if messages[i]["role"] == "user"
                and "V03_LOGIN" in str(messages[i].get("content"))
                and "SYSTEM NOTICE" not in str(messages[i].get("content"))
            ),
            None,
        )
        if login_start is not None and body.get("tools"):
            login_results = [
                item for item in messages[login_start:] if item["role"] == "tool"
            ]
            returned = [
                item
                for item in messages[login_start:]
                if item["role"] == "user"
                and "Human browser handback" in str(item.get("content"))
            ]
            if returned:
                observation = json.loads(returned[-1]["content"].split("\n", 1)[1])
                assert observation["url"].endswith("/authenticated")
                element = next(
                    element
                    for element in observation["interactive_elements"]
                    if element.get("text", "").startswith("Continue signed-in task")
                )
                if args.stale_handback:
                    assert handback_marker in element["text"]
                calls = (
                    []
                    if any(
                        item.get("tool_call_id") == "v03_continue"
                        for item in login_results
                    )
                    else [
                        ("v03_continue", "browser_click", {"index": element["index"]})
                    ]
                )
            elif not login_results:
                calls = [
                    (
                        "v03_login_open",
                        "browser_navigate",
                        {"url": fixture_url + "/login"},
                    )
                ]
            else:
                model_waiting.set()
                await model_release.wait()
                calls = [
                    (
                        "v03_stale",
                        "browser_navigate",
                        {"url": fixture_url + "/stale-login-action"},
                    )
                ]
            message = {
                "role": "assistant",
                "content": None if calls else "V03_LOGIN_DONE",
            }
            if calls:
                message["tool_calls"] = [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                    for call_id, name, arguments in calls
                ]
            finish = "tool_calls" if calls else "stop"
        elif body.get("tools") and not results:
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
    app.router.add_post("/login", website)
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
                "python_path": "" if args.plugin_zip else str(private_python),
                "browser_path": "" if args.plugin_zip else str(chromium),
            }
        ),
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "ASTRBOT_ROOT": str(runroot),
        "PYTHONPATH": str(host_source),
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
    environment["PYTHONPATH"] = os.pathsep.join((str(guard), str(host_source)))
    if args.plugin_zip:
        environment["BROWSER_OFFLINE_AUDIT"] = str(out / "offline-audit")
        for destination in (guard, private_python.parent / "Lib/site-packages"):
            shutil.copy2(
                ROOT / "scripts/browser_offline_guard.py",
                destination / "browser_offline_guard.py",
            )
            (destination / "sitecustomize.py").write_text(
                "import browser_offline_guard\n", encoding="utf-8"
            )
        metrics["offline_guard"] = (
            "Test-only Python audit/install guard plus native Chromium loopback proxy/DNS rules; injected after package verification; no physical airgap claim"
        )
        negative = await asyncio.to_thread(
            subprocess.run,
            [
                str(private_python),
                "-X",
                "utf8",
                "-B",
                "-c",
                "import json,subprocess,sys; r=subprocess.run([sys.argv[1],'--headless','--no-first-run','--disable-background-networking','--dump-dom','--user-data-dir='+sys.argv[2],'https://example.com'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30); blocked=any(s in r.stdout+r.stderr for s in ['ERR_PROXY_CONNECTION_FAILED','ERR_NAME_NOT_RESOLVED','ERR_SOCKS_CONNECTION_FAILED']); print(json.dumps({'returncode':r.returncode,'external_browser_navigation_blocked':blocked})); assert blocked",
                str(chromium),
                str(out / "egress-probe"),
            ],
            env=environment,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert negative.returncode == 0, negative.stderr.decode(errors="replace")
        metrics["offline_negative_probe"] = json.loads(negative.stdout)
    host = viewer = second_viewer = playwright = page = private_browser = None
    auth = {}
    errors = []
    try:
        with (out / "host.log").open("w", encoding="utf-8") as log:
            host = await asyncio.to_thread(
                subprocess.Popen,
                [
                    sys.executable,
                    str(host_source / "main.py"),
                    "--webui-dir",
                    str(dashboard_dist),
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
                    or message.text.startswith("Browser viewport probe")
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
            # The admin notice loads asynchronously one second after mount.
            await page.wait_for_function(
                "localStorage.getItem('astrbot:first_notice_seen:v1')==='1'||Array.from(document.querySelectorAll('.v-overlay--active button')).some(button=>button.textContent.trim()==='关闭')"
            )
            popup = page.get_by_role("button", name="关闭", exact=True)
            if await popup.count():
                await popup.first.click()
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
            if args.hd:
                await fit_native_hd(page, target)
                await page.wait_for_function(
                    "document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-viewport video')?.videoWidth===1920 && document.querySelector('.agent-browser-viewport video')?.videoHeight===1080"
                )
                await page.screenshot(path=str(out / "native-1080-video.png"))
                metrics["checks"].append(
                    "actual production fullscreen layout requests native CSS 1920x1080 and decodes 1920x1080 video; no bitmap upscaling"
                )
            await exercise_controls(
                page, target, "video", 1, out, metrics, fixture_origin, args.samples
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
            if args.terminal_faults:
                await page.get_by_role("button", name="重试视频", exact=True).click()
                await page.wait_for_function(
                    "document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.videoPhase==='live'"
                )
                surface = await page.locator(".agent-browser-viewport").bounding_box()
                viewport = await target.evaluate(
                    "({width:innerWidth,height:innerHeight})"
                )
                drag = await target.locator("#drag").bounding_box()
                await page.mouse.move(
                    *viewport_point(surface, viewport, drag["x"] + 40, drag["y"] + 30)
                )
                await page.mouse.down()
                await target.wait_for_function("held!==null")
                await page.evaluate(
                    "__videoProof.sockets.findLast(s=>s.url.includes('browser-media')).close(4001,'acceptance held drag media fault')"
                )
                await page.locator(
                    '.agent-browser-view[data-video-phase="failed"][data-human-control="true"]'
                ).wait_for()
                await page.mouse.up()
                await target.wait_for_function("held===null", timeout=10000)
                metrics["checks"].append(
                    "held native drag releases while media is failed and no frame is mapped"
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
            if args.hd:
                await fit_native_hd(page, target)
            await exercise_controls(
                page,
                target,
                "jpeg",
                args.samples + 1,
                out,
                metrics,
                await target.evaluate(
                    "Number(new URL(location.href).searchParams.get('origin'))"
                ),
                args.samples,
            )
            await page.evaluate("__videoProof.mode='video'")
            if args.hd:
                await page.locator(".agent-browser-fullscreen").click()
                await page.set_viewport_size({"width": 1600, "height": 1000})
                await asyncio.sleep(1)
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
                # Navigation can schedule a legitimate 350 ms viewport update.
                # Finish it before intentionally withholding all frame metadata.
                await asyncio.sleep(1)
                await page.wait_for_function(
                    "document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0' && document.querySelector('.agent-browser-view')?.dataset.awaitingFrame==='false'"
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
                point = viewport_point(surface, viewport, x, y)
                await page.mouse.move(*(round(value) for value in point))
                await page.mouse.down()
                await page.wait_for_function("__videoProof.heldInputs.length===1")
                held_id = await page.evaluate(
                    "JSON.parse(__videoProof.heldInputs[0][1]).input_id"
                )
                if kind == "same-url-reload":
                    await page.mouse.up()
                    await page.wait_for_function("__videoProof.heldInputs.length===2")
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
                if kind == "same-url-reload":
                    await page.wait_for_function(
                        "id=>__videoProof.acks.some(a=>a.input_id===id+1 && a.operation==='pointer' && a.status==='applied')",
                        arg=held_id,
                    )
                    assert (
                        await page.locator(".agent-browser-view").get_attribute(
                            "data-human-control"
                        )
                        == "true"
                    )
                    metrics["checks"].append(
                        "stale native press with its release already in flight drains termination on the same live control lease without replay"
                    )
                else:
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
            if args.four_viewer_seconds:
                await exercise_four_viewers(
                    playwright,
                    chromium,
                    origin,
                    chat_id,
                    context,
                    page,
                    second_page,
                    target,
                    host,
                    args.four_viewer_seconds,
                    out,
                    metrics,
                )
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
            await child.evaluate("scroller.scrollTop=scroller.scrollTop?0:120")
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
            if args.terminal_faults:
                await exercise_terminal_faults(page, target, child, out, metrics)
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
            idle_processes = [
                private_process,
                *private_process.children(recursive=True),
            ]
            idle_cpu = {
                (p.pid, p.create_time()): sum(p.cpu_times()[:2]) for p in idle_processes
            }
            idle_started = time.monotonic()
            await asyncio.sleep(4)
            metrics["last_viewer_idle"] = {
                "window_s": time.monotonic() - idle_started,
                "private_mcp_rss": private_process.memory_info().rss,
                "private_mcp_cpu_seconds": sum(private_process.cpu_times()[:2])
                - idle_cpu[(private_process.pid, private_process.create_time())],
                "source_tree_rss": sum(
                    p.memory_info().rss for p in idle_processes if p.is_running()
                ),
                "note": "Browser and its test-page animation remain alive; capture/encoder transport has stopped (zero media UDP sockets).",
            }
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
            if args.login_handoff:
                model_waiting.clear()
                model_release.clear()
                await ordinary_page.locator(".chat-text-input, .chat-textarea").fill(
                    "V03_LOGIN"
                )
                await ordinary_page.locator(".send-btn[aria-label]").click()
                await asyncio.wait_for(model_waiting.wait(), 45)
                await ordinary_page.locator(".agent-browser-takeover").click()
                await ordinary_page.locator(
                    '.agent-browser-view[data-human-control="true"]'
                ).wait_for(timeout=30000)
                login_identity = await ordinary_page.locator(
                    ".agent-browser-view"
                ).evaluate("el=>({...el.dataset})")
                count_before = len(model_requests)
                model_release.set()
                await asyncio.sleep(1)
                assert len(model_requests) == count_before
                native_port = next(
                    int(arg.split("=", 1)[1])
                    for process in psutil.Process(host.pid).children(recursive=True)
                    for arg in process.cmdline()
                    if arg.startswith("--remote-debugging-port=")
                    and arg.split("=", 1)[1] != "0"
                )
                private_browser = await playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{native_port}"
                )
                login_target = next(
                    p
                    for c in private_browser.contexts
                    for p in c.pages
                    if p.url.endswith("/login")
                )
                await ordinary_page.wait_for_function(
                    "doc=>document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.frameDocumentId===doc",
                    arg=await login_target.evaluate("String(performance.timeOrigin)"),
                )
                for name, text in (
                    ("username", "Offline 用户"),
                    ("password", website_password),
                ):
                    bounds = await login_target.locator(
                        f'input[name="{name}"]'
                    ).bounding_box()
                    surface = await ordinary_page.locator(
                        ".agent-browser-viewport"
                    ).bounding_box()
                    viewport = await login_target.evaluate(
                        "({width:innerWidth,height:innerHeight})"
                    )
                    await ordinary_page.mouse.click(
                        *viewport_point(
                            surface,
                            viewport,
                            bounds["x"] + bounds["width"] / 2,
                            bounds["y"] + bounds["height"] / 2,
                        )
                    )
                    await ordinary_page.wait_for_function(
                        "document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0' && document.querySelector('.agent-browser-view')?.dataset.awaitingFrame==='false' && document.querySelector('.agent-browser-view')?.dataset.frameId"
                    )
                    await ordinary_page.keyboard.insert_text(text)
                    for _ in range(100):
                        if (
                            await login_target.locator(
                                f'input[name="{name}"]'
                            ).input_value()
                            == text
                        ):
                            break
                        await asyncio.sleep(0.1)
                    else:
                        metrics["login_field_diagnostic"] = await login_target.evaluate(
                            "({url:location.href,username:document.querySelector('[name=username]').value,password_length:document.querySelector('[name=password]').value.length,focus:document.activeElement?.name})"
                        )
                        raise AssertionError(
                            f"Native login field did not match: {name}"
                        )
                    print(f"Native login field confirmed: {name}", flush=True)
                    await ordinary_page.wait_for_function(
                        "document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0' && document.querySelector('.agent-browser-view')?.dataset.awaitingFrame==='false' && document.querySelector('.agent-browser-view')?.dataset.frameId"
                    )
                slider = await login_target.locator(
                    'input[name="slider"]'
                ).bounding_box()
                surface = await ordinary_page.locator(
                    ".agent-browser-viewport"
                ).bounding_box()
                await ordinary_page.wait_for_function(
                    "document.querySelector('.agent-browser-view')?.dataset.frameId"
                )
                await ordinary_page.mouse.move(
                    *viewport_point(
                        surface,
                        viewport,
                        slider["x"] + slider["width"] * 0.1,
                        slider["y"] + slider["height"] / 2,
                    )
                )
                await ordinary_page.mouse.down()
                for step in range(1, 22):
                    await ordinary_page.mouse.move(
                        *viewport_point(
                            surface,
                            viewport,
                            slider["x"] + slider["width"] * (0.1 + 0.7 * step / 21),
                            slider["y"] + slider["height"] / 2,
                        )
                    )
                    await asyncio.sleep(0.01)
                await ordinary_page.mouse.up()
                await login_target.wait_for_function(
                    "+document.querySelector('input[name=slider]').value>=70",
                    polling=100,
                )
                await ordinary_page.wait_for_function(
                    "document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0' && document.querySelector('.agent-browser-view')?.dataset.awaitingFrame==='false' && document.querySelector('.agent-browser-view')?.dataset.frameId"
                )
                button = await login_target.locator(
                    'button[type="submit"]'
                ).bounding_box()
                await ordinary_page.mouse.click(
                    *viewport_point(
                        surface,
                        viewport,
                        button["x"] + button["width"] / 2,
                        button["y"] + button["height"] / 2,
                    )
                )
                await login_target.wait_for_url("**/authenticated", timeout=30000)
                assert len(model_requests) == count_before
                assert website_login == {"submitted": 1, "continued": 0, "stale": 0}
                await ordinary_page.screenshot(
                    path=str(out / "handoff-local-login.png")
                )
                if args.stale_handback:
                    await ordinary_page.wait_for_function(
                        "document.querySelector('.agent-browser-view')?.dataset.frameId && document.querySelector('.agent-browser-view')?.dataset.pendingInput==='0' && document.querySelector('.agent-browser-view')?.dataset.awaitingFrame==='false'"
                    )
                    surface = await ordinary_page.locator(
                        ".agent-browser-viewport"
                    ).bounding_box()
                    viewport = await login_target.evaluate(
                        "({width:innerWidth,height:innerHeight})"
                    )
                    heading = await login_target.locator("h1").bounding_box()
                    point = viewport_point(
                        surface, viewport, heading["x"] + 50, heading["y"] + 15
                    )
                    await ordinary_page.evaluate(
                        "__videoProof.caseId=400;__videoProof.holdInput=true"
                    )
                    await ordinary_page.mouse.move(*(round(value) for value in point))
                    await ordinary_page.mouse.down()
                    await ordinary_page.mouse.up()
                    await ordinary_page.wait_for_function(
                        "__videoProof.heldInputs.length===2"
                    )
                    held_id = await ordinary_page.evaluate(
                        "JSON.parse(__videoProof.heldInputs[0][1]).input_id"
                    )
                    await ordinary_page.locator(".agent-browser-takeover").click()
                    assert await ordinary_page.locator(
                        ".agent-browser-takeover"
                    ).is_disabled()
                    await login_target.evaluate(
                        "marker=>{document.querySelector('button').textContent+=' '+marker;scrollTo(0,30)}",
                        handback_marker,
                    )
                    assert await login_target.evaluate("scrollY") == 30
                    assert (
                        await login_target.evaluate(
                            "Number(sessionStorage.getItem('authenticatedClicks')||0)"
                        )
                        == 0
                    )
                    await ordinary_page.screenshot(
                        path=str(out / "handoff-stale-pending.png")
                    )
                    await ordinary_page.evaluate("__videoProof.flushInput()")
                    await ordinary_page.wait_for_function(
                        "id=>__videoProof.acks.some(a=>a.input_id===id&&a.status==='stale')&&__videoProof.acks.some(a=>a.input_id===id+1&&a.status==='applied')",
                        arg=held_id,
                    )
                else:
                    await ordinary_page.locator(".agent-browser-takeover").click()
                await ordinary_page.get_by_text(
                    "V03_LOGIN_DONE", exact=False
                ).first.wait_for(timeout=45000)
                assert website_login == {"submitted": 1, "continued": 1, "stale": 0}
                if args.stale_handback:
                    proof = await ordinary_page.evaluate(
                        "({commands:__videoProof.commands.filter(c=>c.caseId===400),acks:__videoProof.acks.filter(a=>a.input_id)})"
                    )
                    (out / "stale-handback-transport.json").write_text(
                        json.dumps(proof, indent=2), encoding="utf-8"
                    )
                    commands = proof["commands"]
                    assert (
                        len(
                            [
                                c
                                for c in commands
                                if c["operation"] == "pointer"
                                and c.get("phase") == "down"
                            ]
                        )
                        == 1
                    )
                    assert [
                        c["operation"]
                        for c in commands
                        if c["operation"] in {"reset_keys", "release"}
                    ] == ["reset_keys", "release"]
                    assert (
                        len(
                            [
                                a
                                for a in proof["acks"]
                                if a["operation"] == "release"
                                and a["status"] == "applied"
                            ]
                        )
                        == 1
                    )
                    assert (
                        await login_target.evaluate(
                            "Number(sessionStorage.getItem('authenticatedClicks')||0)"
                        )
                        == 1
                    )
                    metrics["stale_handback"] = {
                        "fault": "Outgoing UI down/up held by test transport before real source scroll; actual runtime returns stale/applied after explicit handback queues.",
                        "original_press_count": 1,
                        "reset_then_release_once": True,
                        "fresh_button_marker": handback_marker,
                        "native_authenticated_clicks": 1,
                    }
                    metrics["checks"].append(
                        "injected stale down with already-in-flight up preserves queued explicit handback; reset precedes one release; Agent observes the new marker and continues once without replay"
                    )
                assert website_password not in json.dumps(
                    model_requests, ensure_ascii=False
                )
                assert (
                    await ordinary_page.locator(".agent-browser-view").get_attribute(
                        "data-browser-session-id"
                    )
                    == login_identity["browserSessionId"]
                )
                metrics["login_handoff"] = {
                    "site_effects": website_login,
                    "browser_session_id": login_identity["browserSessionId"],
                    "model_requests_during_human": 0,
                    "password_excluded_from_model": True,
                }
                metrics["checks"].append(
                    "ordinary-account real ChatUI Agent waits; human types local credentials and drags slider; real form/cookie succeeds; explicit handback supplies fresh authenticated DOM and Agent continues exactly once without stale navigation"
                )
                await ordinary_page.screenshot(
                    path=str(out / "handoff-agent-continued.png")
                )
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
        if args.plugin_zip:
            audit_counts = {}
            for audit_path in (out / "offline-audit").glob("*.jsonl"):
                for line in audit_path.read_text(encoding="utf-8").splitlines():
                    event = json.loads(line)
                    label = event["event"] + (
                        ":" + str(event["allowed"]) if "allowed" in event else ""
                    )
                    audit_counts[label] = audit_counts.get(label, 0) + 1
            metrics["offline_audit_counts"] = audit_counts
            assert audit_counts.get("socket.connect:False", 0) > 0
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
    parser.add_argument("--private-python", type=Path)
    parser.add_argument("--chromium", type=Path)
    parser.add_argument("--plugin-zip", type=Path)
    parser.add_argument("--dashboard-zip", type=Path)
    parser.add_argument("--hd", action="store_true")
    parser.add_argument("--samples", type=int, choices=(6, 30), default=6)
    parser.add_argument("--login-handoff", action="store_true")
    parser.add_argument("--stale-handback", action="store_true")
    parser.add_argument("--terminal-faults", action="store_true")
    parser.add_argument(
        "--four-viewer-seconds", type=int, choices=(0, 15, 300), default=0
    )
    args = parser.parse_args()
    if args.stale_handback and not args.login_handoff:
        parser.error("--stale-handback requires --login-handoff")
    if not args.start_gate:
        from browser_video_probe_process import OwnedProbeProcess

        args.out.mkdir(parents=True, exist_ok=True)
        owned = OwnedProbeProcess(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(Path(__file__).resolve()),
                *sys.argv[1:],
            ],
            args.out,
            dict(os.environ),
        )
        try:
            owned.process.wait(timeout=1800)
        finally:
            ownership = owned.close(graceful_timeout=1)
            (args.out / "ownership.json").write_text(
                json.dumps(ownership, indent=2), encoding="utf-8"
            )
        raise SystemExit(owned.process.returncode)
    deadline = time.monotonic() + 20
    while args.start_gate and not args.start_gate.exists():
        if time.monotonic() > deadline:
            raise RuntimeError("Process ownership gate was not established")
        time.sleep(0.02)
    asyncio.run(main(args))
