"""Measure a disposable private Chromium video path in an actual browser viewer."""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4


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
    args = parser.parse_args()
    sys.path.insert(0, str(args.playwright_path))
    import psutil
    from playwright.sync_api import sync_playwright

    args.out.mkdir(parents=True, exist_ok=True)
    if args.bound_test:
        folder = args.out / "bound-test"
        folder.mkdir(exist_ok=True)
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
        with (folder / "runtime.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=55,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        record = {
            "exit_code": result.returncode,
            "elapsed": time.monotonic() - before,
            "runtime_record": (folder / "runtime.json").exists(),
        }
        (folder / "bound.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
        assert result.returncode != 0 and record["runtime_record"], record
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
                with (folder / "runtime.log").open("w", encoding="utf-8") as log:
                    runtime = subprocess.Popen(
                        command,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        env=env,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                viewer = None
                sample_stop = threading.Event()
                sample_thread = None
                resource_samples = []
                owned_pids = set()

                def sample_resources():
                    """Measure every process owned by this isolated driver."""
                    known = {}
                    while not sample_stop.is_set():
                        rows = []
                        for proc in psutil.Process().children(recursive=True):
                            with contextlib.suppress(psutil.Error):
                                current = known.setdefault(proc.pid, proc)
                                owned_pids.add(proc.pid)
                                rows.append(
                                    {
                                        "pid": proc.pid,
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
                    phases.append({"name": "slider_drag", "start": phases[-1]["end"]})
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
                    page.mouse.move(left + 70 * scale, top + 335 * scale)
                    page.mouse.down()
                    for step in range(1, 51):
                        page.mouse.move(
                            left + (70 + 280 * step / 50) * scale,
                            top + (335 - 35 * step / 50) * scale,
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
                        phases=phases, target=target, move_interval=args.move_interval
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
                finally:
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
                    try:
                        runtime.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        runtime.terminate()
                        runtime.wait(timeout=10)
                    print("EXIT", runtime.returncode, flush=True)
                    remaining = []
                    for pid in owned_pids:
                        with contextlib.suppress(psutil.Error):
                            proc = psutil.Process(pid)
                            if proc.name().lower() in {"chrome.exe", "python.exe"}:
                                remaining.append({"pid": pid, "name": proc.name()})
                    (folder / "cleanup.json").write_text(
                        json.dumps(
                            {
                                "runtime_exit": runtime.returncode,
                                "remaining_owned_browser_or_runtime": remaining,
                            },
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    assert not remaining, remaining


if __name__ == "__main__":
    main()
