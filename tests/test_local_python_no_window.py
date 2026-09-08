from __future__ import annotations

import os

import pytest

from astrbot.core.computer.booters.local import LocalPythonComponent

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="Console-window suppression is Windows-only.",
)

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008


async def _run_python(code: str) -> str:
    """Run code through the local Python component and return stripped stdout."""
    result = await LocalPythonComponent().exec(code, timeout=60)
    data = result["data"]
    assert data["error"] == "", data["error"]
    return data["output"]["text"].strip()


@pytest.mark.asyncio
async def test_create_process_patch_covers_subprocess_and_multiprocessing():
    output = await _run_python(
        "import _winapi\n"
        "import multiprocessing.popen_spawn_win32 as spawn\n"
        "print(\n"
        "    getattr(_winapi.CreateProcess, '_ab_no_window_patched', False),\n"
        "    spawn._winapi.CreateProcess is _winapi.CreateProcess,\n"
        ")\n"
    )

    assert output == "True True"


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (0, CREATE_NO_WINDOW),
        (CREATE_NEW_CONSOLE, CREATE_NO_WINDOW),
        (DETACHED_PROCESS, DETACHED_PROCESS),
        (CREATE_NO_WINDOW, CREATE_NO_WINDOW),
    ],
)
@pytest.mark.asyncio
async def test_flag_rewriting(flags, expected):
    output = await _run_python(f"print(_ab_no_window_flags({flags}))\n")

    assert output == str(expected)


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
async def test_subprocess_output_still_works():
    output = await _run_python(
        "import subprocess\n"
        "done = subprocess.run(\n"
        "    ['cmd', '/c', 'echo', 'ok'], capture_output=True, text=True\n"
        ")\n"
        "print(done.returncode, done.stdout.strip())\n"
    )

    assert output == "0 ok"
