"""Measure a disposable private Chromium video path in an actual browser viewer."""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

from browser_video_probe_process import OwnedProbeProcess


def request_recovery(page, base, auth, folder):
    """Verify recovery using awaited receiver statistics.

    Args:
        page: Actual receiving browser page.
        base: Loopback fixture URL.
        auth: Ephemeral credential query.
        folder: Evidence directory.

    Raises:
        AssertionError: No additional keyframe is decoded within eight seconds.
    """
    expression = "async()=>Array.from((await pc.getStats()).values()).find(s=>s.type==='inbound-rtp'&&s.kind==='video').keyFramesDecoded"
    before = page.evaluate(expression)
    with urlopen(Request(base + "/recovery" + auth, data=b"{}"), timeout=10):
        pass
    deadline = time.monotonic() + 8
    after = before
    while after <= before and time.monotonic() < deadline:
        page.wait_for_timeout(100)
        after = page.evaluate(expression)
    record = {
        "before": before,
        "after": after,
        "verified": after > before,
        "method": "Explicit sender recovery request using the same flag as RTCP PLI; no receiver PLI injection",
    }
    (folder / "recovery.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8"
    )
    assert after > before, record


def run_discrete_actions(page, base, auth, folder, scale, left, top):
    """Measure the same twelve settled widget changes in both media paths.

    Args:
        page: Actual receiver page driven by Playwright mouse input.
        base: Loopback fixture URL.
        auth: Ephemeral fixture credential query.
        folder: Isolated evidence directory.
        scale: Viewer scale relative to browser viewport coordinates.
        left: Viewport's displayed left edge.
        top: Viewport's displayed top edge.

    Returns:
        All twelve action results, including exact values decoded from pixels.
    """
    from PIL import Image

    cases = []
    actions = [(f"slider-{value}", "slider", value) for value in (20, 70, 35, 85)]
    actions += [
        (f"drag-{i}", "drag", delta)
        for i, delta in enumerate(((80, 30), (-60, 25), (100, -30), (-40, 15)), 1)
    ]
    actions += [
        (f"scroll-{i}", "scroll", delta)
        for i, delta in enumerate((240, 320, -160, -400), 1)
    ]
    for name, kind, value in actions:
        page.wait_for_function(
            "displayed && displayed.applied_input_id >= (probe.inputs.at(-1)?.id||0) && inflight===0 && pending.length===0",
            timeout=8000,
        )
        with urlopen(base + "/inspect" + auth, timeout=10) as response:
            before = json.load(response)["target"]
        expected = [int(before["slider"]), *before["drag"], before["scroll"]]
        page.evaluate("name => probe.activeCase=name", name)
        if kind == "slider":
            rect = before["rects"]["slider"]
            x, y = (
                rect["x"] + 8 + (rect["width"] - 16) * value / 100,
                rect["y"] + rect["height"] / 2,
            )
            page.mouse.move(left + x * scale, top + y * scale)
            page.mouse.down()
            expected[0] = value
        elif kind == "drag":
            rect = before["rects"]["drag"]
            x, y = rect["x"] + 30, rect["y"] + 25
            page.mouse.move(left + x * scale, top + y * scale)
            page.mouse.down()
            page.wait_for_function("inflight===0 && pending.length===0", timeout=8000)
            page.mouse.move(left + (x + value[0]) * scale, top + (y + value[1]) * scale)
            expected[1] += value[0]
            expected[2] += value[1]
        else:
            page.mouse.move(
                left + before["width"] * 0.85 * scale,
                top + before["height"] * 0.8 * scale,
            )
            page.mouse.wheel(0, value)
            expected[3] += value
        stimulus = page.evaluate("probe.inputs.at(-1)")
        query = {"id": stimulus["id"], "values": expected}
        page.wait_for_function(
            "q => probe.frames.some(f=>f.optical_valid && f.actual[1]===q.id && f.actual.slice(3).every((v,i)=>v===q.values[i]))",
            arg=query,
            timeout=8000,
        )
        presented = page.evaluate(
            "q=>probe.frames.find(f=>f.optical_valid && f.actual[1]===q.id && f.actual.slice(3).every((v,i)=>v===q.values[i]))",
            query,
        )
        with urlopen(base + "/inspect" + auth, timeout=10) as response:
            after = json.load(response)["target"]
        actual = [int(after["slider"]), *after["drag"], after["scroll"]]
        assert actual == expected, (name, actual, expected)
        change = next(
            item
            for item in after["probe"]["changes"]
            if item["id"] == stimulus["id"]
            and item["values"] == expected
            and item["t"] == presented["actual"][2]
        )
        encoded = page.evaluate(
            """()=>{const source=document.querySelector('video')||document.querySelector('img');const c=document.createElement('canvas');c.width=source.videoWidth||source.naturalWidth;c.height=source.videoHeight||source.naturalHeight;c.getContext('2d').drawImage(source,0,0);return c.toDataURL('image/png').split(',')[1]}"""
        )
        evidence = folder / f"discrete-{name}.png"
        evidence.write_bytes(base64.b64decode(encoded))
        pixel_check = None
        if kind in {"slider", "drag"}:
            if kind == "drag":
                x, y = (
                    expected[1] + (160 if value[0] > 0 else 5),
                    expected[2] + 60 - expected[3],
                )
            with Image.open(evidence) as image:
                pixel_scale = image.width / before["width"]
                pixel = image.convert("RGB").getpixel(
                    (round(x * pixel_scale), round(y * pixel_scale))
                )
            assert pixel[2] > 120 and pixel[0] < 140, (name, pixel)
            pixel_check = {
                "coordinate": [x, y],
                "rgb": list(pixel),
                "assertion": "Blue native slider thumb or moved component is visible at the changed position",
            }
        cases.append(
            {
                "name": name,
                "kind": kind,
                "requested": value,
                "before": [int(before["slider"]), *before["drag"], before["scroll"]],
                "actual": actual,
                "input": stimulus,
                "render_change": change,
                "presented": presented,
                "input_to_actual_state_present_ms": presented["t"] - stimulus["t"],
                "pixel_check": pixel_check,
                "image": str(evidence.resolve()),
            }
        )
        (folder / "discrete.json").write_text(
            json.dumps(cases, indent=2), encoding="utf-8"
        )
        if kind != "scroll":
            page.mouse.up()
        page.wait_for_function(
            "displayed && displayed.applied_input_id >= probe.inputs.at(-1).id && inflight===0 && pending.length===0",
            timeout=8000,
        )
        page.evaluate("probe.activeCase=null")
    assert len(cases) == 12
    return cases


def main():
    """Run bounded baseline/video cases and preserve raw presentation evidence.

    Raises:
        RuntimeError: The real fixture exits early or fails to present frames.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--chromium", type=Path, required=True)
    parser.add_argument("--playwright-path", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sizes", default="1920x1080")
    parser.add_argument("--modes", default="jpeg,video")
    parser.add_argument("--bound-test", action="store_true")
    parser.add_argument("--move-interval", type=float, default=33)
    parser.add_argument("--fault-test", choices=["pre-ready", "post-ready"])
    parser.add_argument("--recovery-test", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.playwright_path))
    import psutil
    from playwright.sync_api import sync_playwright

    args.out.mkdir(parents=True, exist_ok=True)
    if args.fault_test or args.recovery_test:
        args.modes = "video"
    if args.bound_test:
        folder = args.out / ("bound-test-" + uuid4().hex[:8])
        folder.mkdir()
        command = [
            str(args.python),
            "-B",
            str(args.plugin / "scripts/video_probe.py"),
            "--out",
            str(folder),
            "--chromium",
            str(args.chromium),
            "--mode",
            "video",
            "--lifetime",
            "0.2",
        ]
        before = time.monotonic()
        temporary = folder.resolve() / "temp"
        temporary.mkdir()
        owned = OwnedProbeProcess(
            command,
            folder,
            {
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "TEMP": str(temporary),
                "TMP": str(temporary),
            },
        )
        try:
            owned.process.wait(timeout=55)
        finally:
            cleanup = owned.close(graceful_timeout=0.5)
            (folder / "cleanup.json").write_text(
                json.dumps(cleanup, indent=2), encoding="utf-8"
            )
        record = {
            "exit_code": owned.process.returncode,
            "elapsed": time.monotonic() - before,
            "runtime_record": (folder / "runtime.json").exists(),
        }
        (folder / "bound.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
        assert owned.process.returncode != 0 and record["runtime_record"], record
        assert not cleanup["remaining_owned_processes"], cleanup
        print("BOUND FIRED", record, flush=True)
        return

    with sync_playwright() as pw:
        for size in args.sizes.split(","):
            width, height = map(int, size.split("x"))
            for mode in args.modes.split(","):
                folder = args.out / f"{mode}-{size}-{uuid4().hex[:8]}"
                folder.mkdir()
                command = [
                    str(args.python),
                    "-B",
                    str(args.plugin / "scripts/video_probe.py"),
                    "--out",
                    str(folder),
                    "--chromium",
                    str(args.chromium),
                    "--mode",
                    mode,
                    "--width",
                    str(width),
                    "--height",
                    str(height),
                    "--lifetime",
                    "120",
                ]
                info = None
                temporary = folder.resolve() / "temp"
                temporary.mkdir()
                env = {
                    **os.environ,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "TEMP": str(temporary),
                    "TMP": str(temporary),
                }
                if args.fault_test:
                    command.extend(["--fault-mode", args.fault_test])
                owned = OwnedProbeProcess(command, folder, env)
                runtime = owned.process
                viewer = None
                page = None
                sample_stop = threading.Event()
                sample_thread = None
                resource_samples = []

                def sample_resources():
                    """Measure every process owned by this isolated driver."""
                    known = {}
                    while not sample_stop.is_set():
                        rows = []
                        for proc in psutil.Process().children(recursive=True):
                            with contextlib.suppress(psutil.Error):
                                created = proc.create_time()
                                current = known.setdefault((proc.pid, created), proc)
                                rows.append(
                                    {
                                        "pid": proc.pid,
                                        "create_time": created,
                                        "ppid": current.ppid(),
                                        "cpu": current.cpu_percent(),
                                        "rss": current.memory_info().rss,
                                        "name": current.name(),
                                    }
                                )
                        resource_samples.append(
                            {
                                "t": time.time() * 1000 - info["origin"],
                                "processes": rows,
                            }
                        )
                        sample_stop.wait(1)

                try:
                    deadline = time.monotonic() + 50
                    while not (folder / "ready.json").exists():
                        if (
                            args.fault_test == "pre-ready"
                            and (folder / "fault-fired.json").exists()
                        ):
                            time.sleep(0.5)
                            raise TimeoutError(
                                "Injected pre-ready startup timeout after Chromium started."
                            )
                        if runtime.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError(f"Fixture failed: {folder}")
                        time.sleep(0.1)
                    info = json.loads((folder / "ready.json").read_text())
                    base = f"http://127.0.0.1:{info['port']}"
                    auth = f"?token={info['token']}"
                    viewer = pw.chromium.launch(
                        executable_path=str(args.chromium),
                        headless=True,
                        args=["--no-proxy-server", "--disable-background-networking"],
                    )
                    sample_thread = threading.Thread(
                        target=sample_resources, daemon=True
                    )
                    sample_thread.start()
                    page = viewer.new_page(viewport={"width": 1920, "height": 1110})
                    page.on(
                        "pageerror",
                        lambda error: print("PAGE ERROR", str(error), flush=True),
                    )
                    page.goto(
                        base
                        + "/viewer"
                        + auth
                        + f"&mode={mode}&origin={info['origin']}&width={width}"
                    )
                    page.wait_for_function("probe.frames.length>8", timeout=25000)
                    if args.recovery_test:
                        request_recovery(page, base, auth, folder)
                        page.evaluate("stopProbe()")
                        (folder / "recovery-viewer.json").write_text(
                            json.dumps(page.evaluate("probe"), indent=2),
                            encoding="utf-8",
                        )
                        continue
                    if args.fault_test == "post-ready":
                        try:
                            with urlopen(
                                Request(base + "/fault" + auth, data=b"{}"), timeout=5
                            ):
                                pass
                        except (OSError, ConnectionError):
                            pass
                        runtime.wait(timeout=5)
                        assert runtime.returncode == 87, runtime.returncode
                        (folder / "fault-test.json").write_text(
                            json.dumps(
                                {
                                    "injected": "post-ready abrupt runtime exit",
                                    "exit": runtime.returncode,
                                }
                            ),
                            encoding="utf-8",
                        )
                        continue
                    phases = []
                    phases.append(
                        {
                            "name": "static_text",
                            "start": page.evaluate(
                                "performance.timeOrigin+performance.now()-"
                                + str(info["origin"])
                            ),
                        }
                    )
                    page.wait_for_timeout(4000)
                    page.screenshot(path=str(folder / "small-text.png"))
                    phases[-1]["end"] = page.evaluate(
                        "performance.timeOrigin+performance.now()-"
                        + str(info["origin"])
                    )
                    displayed_png = page.evaluate(
                        """() => {const source=document.querySelector('video')||document.querySelector('img');const c=document.createElement('canvas');c.width=source.videoWidth||source.naturalWidth;c.height=source.videoHeight||source.naturalHeight;c.getContext('2d').drawImage(source,0,0);return c.toDataURL('image/png').split(',')[1]}"""
                    )
                    (folder / "display.png").write_bytes(
                        base64.b64decode(displayed_png)
                    )
                    phases.append(
                        {"name": "continuous_animation", "start": phases[-1]["end"]}
                    )
                    page.wait_for_timeout(7000)
                    phases[-1]["end"] = page.evaluate(
                        "performance.timeOrigin+performance.now()-"
                        + str(info["origin"])
                    )
                    surface = page.locator("#surface").bounding_box()
                    scale = min(surface["width"] / width, surface["height"] / height)
                    left = surface["x"] + (surface["width"] - width * scale) / 2
                    top = surface["y"] + (surface["height"] - height * scale) / 2
                    discrete = run_discrete_actions(
                        page, base, auth, folder, scale, left, top
                    )
                    if mode == "video":
                        request_recovery(page, base, auth, folder)
                    phases.append({"name": "slider_drag", "start": phases[-1]["end"]})
                    phases[-1]["start"] = page.evaluate(
                        "performance.timeOrigin+performance.now()-"
                        + str(info["origin"])
                    )
                    with urlopen(base + "/inspect" + auth, timeout=15) as response:
                        initial = json.load(response)
                    slider = initial["target"]["rects"]["slider"]
                    slider_y = slider["y"] + slider["height"] / 2
                    for reverse in (False, True, False):
                        xs = (90, 500) if not reverse else (500, 90)
                        page.mouse.move(left + xs[0] * scale, top + slider_y * scale)
                        page.mouse.down()
                        for step in range(1, 41):
                            page.mouse.move(
                                left + (xs[0] + (xs[1] - xs[0]) * step / 40) * scale,
                                top + slider_y * scale,
                            )
                            page.wait_for_timeout(args.move_interval)
                        page.mouse.up()
                        page.wait_for_timeout(400)
                    phases[-1]["end"] = page.evaluate(
                        "performance.timeOrigin+performance.now()-"
                        + str(info["origin"])
                    )
                    phases.append(
                        {"name": "component_drag", "start": phases[-1]["end"]}
                    )
                    with urlopen(base + "/inspect" + auth, timeout=10) as response:
                        drag = json.load(response)["target"]["rects"]["drag"]
                    drag_x, drag_y = drag["x"] + 30, drag["y"] + 25
                    page.mouse.move(left + drag_x * scale, top + drag_y * scale)
                    page.mouse.down()
                    for step in range(1, 51):
                        page.mouse.move(
                            left + (drag_x + 280 * step / 50) * scale,
                            top + (drag_y - 35 * step / 50) * scale,
                        )
                        page.wait_for_timeout(args.move_interval)
                    page.mouse.up()
                    page.wait_for_timeout(1200)
                    phases[-1]["end"] = page.evaluate(
                        "performance.timeOrigin+performance.now()-"
                        + str(info["origin"])
                    )
                    phases.append({"name": "long_scroll", "start": phases[-1]["end"]})
                    page.mouse.move(
                        left + width * 0.85 * scale, top + height * 0.8 * scale
                    )
                    for i in range(25):
                        page.mouse.wheel(0, 80 if i < 15 else -80)
                        page.wait_for_timeout(150)
                    page.wait_for_timeout(1500)
                    phases[-1]["end"] = page.evaluate(
                        "performance.timeOrigin+performance.now()-"
                        + str(info["origin"])
                    )
                    page.screenshot(path=str(folder / "scroll.png"))
                    with urlopen(base + "/inspect" + auth, timeout=15) as response:
                        target = json.load(response)
                    assert [target["target"]["width"], target["target"]["height"]] == [
                        width,
                        height,
                    ], "The fixture viewport changed."
                    with urlopen(base + "/reference" + auth, timeout=10) as response:
                        (folder / "reference.png").write_bytes(response.read())
                    page.evaluate("stopProbe()")
                    presented = page.evaluate("probe")
                    presented.update(
                        phases=phases,
                        target=target,
                        move_interval=args.move_interval,
                        discrete=discrete,
                    )
                    presented["owned_resources"] = resource_samples
                    (folder / "viewer.json").write_text(
                        json.dumps(presented, indent=2), encoding="utf-8"
                    )
                    print(
                        "PRESENTED",
                        mode,
                        size,
                        len(presented["frames"]),
                        "inputs",
                        len(presented["inputs"]),
                        "target",
                        {k: v for k, v in target["target"].items() if k != "probe"},
                        "errors",
                        presented["errors"],
                        str(folder),
                        flush=True,
                    )
                    assert int(target["target"]["slider"]) > 60, target["target"]
                    assert target["target"]["drag"][0] > 300, target["target"]
                    assert any(
                        e["scroll"] > 0 for e in target["target"]["probe"]["events"]
                    ), target["target"]
                    assert not presented["errors"], presented["errors"]
                except TimeoutError as error:
                    if args.fault_test != "pre-ready":
                        raise
                    (folder / "fault-test.json").write_text(
                        json.dumps(
                            {
                                "injected": "pre-ready timeout",
                                "message": str(error),
                                "ready_exists": (folder / "ready.json").exists(),
                            }
                        ),
                        encoding="utf-8",
                    )
                finally:
                    if (
                        page
                        and not args.fault_test
                        and not args.recovery_test
                        and not (folder / "viewer.json").exists()
                    ):
                        with contextlib.suppress(Exception):
                            failure = {"viewer": page.evaluate("probe")}
                            with urlopen(
                                base + "/inspect" + auth, timeout=5
                            ) as response:
                                failure["target"] = json.load(response)
                            (folder / "failure.json").write_text(
                                json.dumps(failure, indent=2), encoding="utf-8"
                            )
                            page.screenshot(path=str(folder / "failure.png"))
                    sample_stop.set()
                    if sample_thread:
                        sample_thread.join(timeout=3)
                    if viewer:
                        viewer.close()
                    if info:
                        try:
                            with urlopen(
                                Request(
                                    f"http://127.0.0.1:{info['port']}/shutdown?token={info['token']}",
                                    data=b"{}",
                                    headers={"Content-Type": "application/json"},
                                ),
                                timeout=10,
                            ):
                                pass
                        except OSError:
                            pass
                    cleanup = owned.close(
                        graceful_timeout=0.25 if args.fault_test else 20
                    )
                    print("EXIT", runtime.returncode, flush=True)
                    (folder / "cleanup.json").write_text(
                        json.dumps(cleanup, indent=2),
                        encoding="utf-8",
                    )
                    assert not cleanup["remaining_owned_processes"], cleanup
                    if args.fault_test:
                        assert cleanup["forced_job_termination"], cleanup
                        assert len(cleanup["known_processes"]) > 1, cleanup
                        print(
                            "FAULT CLEANUP PASSED",
                            args.fault_test,
                            "tracked",
                            len(cleanup["known_processes"]),
                            flush=True,
                        )


if __name__ == "__main__":
    main()
