"""Integration tests for the Windows ACL process sandbox backend.

These tests exercise the real restricted-token runner (no mocking of the OS
boundary). They require native Windows; on other platforms the module skips.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from astrbot.core.computer.process_sandbox.base import (
    SandboxSpec,
    SandboxTimeoutError,
)
from astrbot.core.computer.process_sandbox.windows_acl import (
    SandboxUnavailableError,
    WindowsAclProcessSandbox,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows ACL sandbox backend is Windows-only",
)

PYTHON = sys.executable


def _make_tree(tmp_name: str = "") -> tuple[Path, Path, Path]:
    base = Path(tempfile.gettempdir()) / (
        "astrbot-win-acl-test-" + tmp_name + uuid.uuid4().hex[:8]
    )
    base.mkdir()
    workspace = base / "ws"
    workspace.mkdir()
    outside = base / "outside"
    outside.mkdir()
    return base, workspace, outside


def _spec(workspace: Path, *, writable: bool) -> SandboxSpec:
    return SandboxSpec(workspace=workspace, workspace_writable=writable)


DENIED_WRITER = (
    "import sys\n"
    "try:\n"
    "    open(sys.argv[1], 'w', encoding='utf-8').write('x')\n"
    "except OSError as exc:\n"
    "    print('DENIED', type(exc).__name__)\n"
)


def test_workspace_mode_allows_inside_and_denies_outside():
    base, workspace, outside = _make_tree()
    try:
        sandbox = WindowsAclProcessSandbox()
        inside = workspace / "inside.txt"
        result = sandbox.run(
            [
                PYTHON,
                "-c",
                "import sys; open(sys.argv[1], 'w').write('ok')",
                str(inside),
            ],
            _spec(workspace, writable=True),
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert inside.read_text(encoding="utf-8") == "ok"

        target = outside / "evil.txt"
        result = sandbox.run(
            [PYTHON, "-c", DENIED_WRITER, str(target)],
            _spec(workspace, writable=True),
            timeout=60,
        )
        assert b"DENIED" in result.stdout
        assert not target.exists()

        secret = outside / "secret.txt"
        secret.write_text("secret", encoding="utf-8")
        result = sandbox.run(
            [PYTHON, "-c", "import os,sys; os.remove(sys.argv[1])", str(secret)],
            _spec(workspace, writable=True),
            timeout=60,
        )
        assert secret.exists()
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_readonly_mode_denies_workspace_writes():
    base, workspace, outside = _make_tree()
    try:
        sandbox = WindowsAclProcessSandbox()
        target = workspace / "ro.txt"
        result = sandbox.run(
            [PYTHON, "-c", DENIED_WRITER, str(target)],
            _spec(workspace, writable=False),
            timeout=60,
        )
        assert b"DENIED" in result.stdout
        assert not target.exists()

        outside_target = outside / "evil.txt"
        result = sandbox.run(
            [PYTHON, "-c", DENIED_WRITER, str(outside_target)],
            _spec(workspace, writable=False),
            timeout=60,
        )
        assert b"DENIED" in result.stdout
        assert not outside_target.exists()
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.mark.asyncio
async def test_spawn_argv_streams_output_and_exit_code():
    base, workspace, _outside = _make_tree()
    try:
        sandbox = WindowsAclProcessSandbox()
        process = await sandbox.spawn_argv(
            [PYTHON, "-c", "print('ASYNC-OUT'); raise SystemExit(3)"],
            _spec(workspace, writable=True),
        )
        output = await process.stdout.read()
        exit_code = await process.wait()
        assert exit_code == 3
        assert b"ASYNC-OUT" in output
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_timeout_kills_the_confined_process():
    base, workspace, _outside = _make_tree()
    try:
        sandbox = WindowsAclProcessSandbox()
        with pytest.raises(SandboxTimeoutError):
            sandbox.run(
                [PYTHON, "-c", "import time; time.sleep(10)"],
                _spec(workspace, writable=True),
                timeout=1,
            )
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_runner_failure_is_reported_as_unavailable():
    base, workspace, _outside = _make_tree()
    try:
        sandbox = WindowsAclProcessSandbox()
        with pytest.raises(SandboxUnavailableError):
            sandbox.run(
                ["definitely-not-an-executable-" + uuid.uuid4().hex],
                _spec(workspace, writable=True),
                timeout=30,
            )
    finally:
        shutil.rmtree(base, ignore_errors=True)
