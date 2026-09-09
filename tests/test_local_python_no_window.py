from __future__ import annotations

import asyncio
import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

import pytest

from astrbot.core.computer.booters.local import LocalPythonComponent

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="Console-window suppression is Windows-only.",
)

_CONSOLE_WINDOW_CLASSES = {
    "ConsoleWindowClass",  # conhost.exe
    "CASCADIA_HOSTING_WINDOW_CLASS",  # Windows Terminal
}


def _visible_console_hwnds() -> set[int]:
    """Return the handles of all visible top-level console windows."""
    hwnds: set[int] = set()
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def on_window(hwnd, _lparam):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(64)
            ctypes.windll.user32.GetClassNameW(hwnd, buf, 64)
            if buf.value in _CONSOLE_WINDOW_CLASSES:
                hwnds.add(hwnd)
        return True

    keep_alive = callback_type(on_window)  # prevent GC of the callback
    ctypes.windll.user32.EnumWindows(keep_alive, 0)
    return hwnds


async def _run_python(code: str) -> str:
    """Run code through the local Python component and return stripped stdout."""
    result = await LocalPythonComponent().exec(code, timeout=60)
    data = result["data"]
    assert data["error"] == "", data["error"]
    return data["output"]["text"].strip()


@pytest.mark.asyncio
async def test_python_env_pointing_to_pythonw_is_redirected_to_console_python(
    monkeypatch: pytest.MonkeyPatch,
):
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pytest.skip("pythonw.exe not found next to the test interpreter")
    monkeypatch.setenv("PYTHON", str(pythonw))

    output = await _run_python(
        "import os, sys; print(os.path.basename(sys.executable))"
    )

    assert output == "python.exe"


@pytest.mark.asyncio
async def test_subprocess_child_has_no_console_window():
    output = await _run_python(
        "import subprocess, sys\n"
        "inner = 'import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())'\n"
        "done = subprocess.run(\n"
        "    [sys.executable, '-c', inner], capture_output=True, text=True\n"
        ")\n"
        "print(done.stdout.strip())\n"
    )

    assert output == "0"


@pytest.mark.asyncio
async def test_nested_console_spawn_flashes_no_window():
    before = _visible_console_hwnds()
    flashed: set[int] = set()
    finished = asyncio.Event()

    async def watch():
        while not finished.is_set():
            flashed.update(_visible_console_hwnds() - before)
            await asyncio.sleep(0.2)

    watcher = asyncio.create_task(watch())
    try:
        output = await _run_python(
            "import subprocess\n"
            "done = subprocess.run(\n"
            "    ['ping', '-n', '3', '127.0.0.1'],\n"
            "    stdout=subprocess.DEVNULL,\n"
            "    stderr=subprocess.DEVNULL,\n"
            ")\n"
            "print(done.returncode)\n"
        )
    finally:
        finished.set()
        await watcher

    assert output == "0"
    assert flashed == set(), f"a console window flashed during nested spawn: {flashed}"


@pytest.mark.asyncio
async def test_subprocess_output_still_works():
    output = await _run_python(
        "import subprocess\n"
        "done = subprocess.run(\n"
        "    ['cmd', '/c', 'echo', 'ok'], capture_output=True, text=True\n"
        ")\n"
        "print(done.returncode, done.stdout.strip())\n"
    )

    assert output == "0 ok"
