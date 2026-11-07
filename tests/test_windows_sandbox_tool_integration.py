"""End-to-end tool-path test for the Windows write/delete sandbox.

Unlike the backend unit tests, this drives the public shell/Python tools with
the real local booter, the real file-access policy, and the real Windows ACL
runner. Windows-only; skipped elsewhere.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.computer.computer_client import shutdown_local_booter
from astrbot.core.tools import fs_access
from astrbot.core.tools.computer_tools.python import LocalPythonTool
from astrbot.core.tools.computer_tools.shell import ExecuteShellTool

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows ACL sandbox integration is Windows-only",
)


def _make_tree() -> tuple[Path, Path, Path]:
    base = Path(tempfile.gettempdir()) / ("astrbot-win-tool-" + uuid.uuid4().hex[:8])
    base.mkdir()
    workspace = base / "ws"
    workspace.mkdir()
    outside = base / "outside"
    outside.mkdir()
    return base, workspace, outside


def _make_context(umo: str, workspace: Path) -> ContextWrapper:
    event = SimpleNamespace(
        unified_msg_origin=umo,
        role="admin",
        get_sender_id=lambda: "tester",
        get_platform_name=lambda: "webchat",
    )
    config_holder = SimpleNamespace(
        get_config=lambda **_kwargs: {
            "provider_settings": {
                "computer_use_runtime": "local",
                "computer_use_require_admin": True,
                "computer_use_local_shell": "auto",
                "file_access_default_mode": "full",
            }
        }
    )
    astr_ctx = SimpleNamespace(context=config_holder, event=event)
    return ContextWrapper(context=astr_ctx, tool_call_timeout=120)


@pytest.fixture()
def tree():
    base, workspace, outside = _make_tree()
    try:
        yield workspace, outside
    finally:
        fs_access.reset()
        shutil.rmtree(base, ignore_errors=True)


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_booter():
    yield
    try:
        await shutdown_local_booter()
    except Exception:
        pass


@pytest.mark.asyncio
async def test_shell_tool_workspace_and_readonly(monkeypatch, tree):
    from astrbot.core.tools.computer_tools import shell as shell_tools

    workspace, outside = tree
    monkeypatch.setattr(
        shell_tools, "workspace_root_for_context", _workspace_returner(workspace)
    )
    umo = "webchat:FriendMessage:webchat!tester!sandbox-1"
    context = _make_context(umo, workspace)

    fs_access.set_mode_for_umo(umo, fs_access.FileAccessMode.WORKSPACE)
    inside = workspace / "inside.txt"
    outside_target = outside / "evil.txt"
    result = await ExecuteShellTool().call(
        context,
        command=f"Set-Content -LiteralPath '{inside}' -Value hi; Set-Content -LiteralPath '{outside_target}' -Value hi",
    )
    assert isinstance(result, str)
    assert inside.exists()
    assert not outside_target.exists()

    ro_umo = "webchat:FriendMessage:webchat!tester!sandbox-2"
    ro_context = _make_context(ro_umo, workspace)
    fs_access.set_mode_for_umo(ro_umo, fs_access.FileAccessMode.READONLY)
    ro_inside = workspace / "ro.txt"
    result = await ExecuteShellTool().call(
        ro_context,
        command=f"Set-Content -LiteralPath '{ro_inside}' -Value hi",
    )
    assert isinstance(result, str)
    assert not ro_inside.exists()


@pytest.mark.asyncio
async def test_python_tool_workspace_and_readonly(monkeypatch, tree):
    from astrbot.core.tools.computer_tools import python as python_tools

    workspace, outside = tree
    umo = "webchat:FriendMessage:webchat!tester!sandbox-3"
    context = _make_context(umo, workspace)
    monkeypatch.setattr(
        python_tools,
        "workspace_root_for_context",
        _workspace_returner(workspace),
    )

    fs_access.set_mode_for_umo(umo, fs_access.FileAccessMode.WORKSPACE)
    inside = workspace / "py_inside.txt"
    outside_target = outside / "py_evil.txt"
    result = await LocalPythonTool().call(
        context,
        code=(
            "open(r'" + str(inside) + "', 'w').write('ok')\n"
            "open(r'" + str(outside_target) + "', 'w').write('bad')"
        ),
    )
    text = [part.text for part in result.content]
    assert inside.exists()
    assert not outside_target.exists()
    assert any("PermissionError" in part for part in text)

    ro_umo = "webchat:FriendMessage:webchat!tester!sandbox-4"
    ro_context = _make_context(ro_umo, workspace)
    fs_access.set_mode_for_umo(ro_umo, fs_access.FileAccessMode.READONLY)
    ro_target = workspace / "py_ro.txt"
    result = await LocalPythonTool().call(
        ro_context,
        code="open(r'" + str(ro_target) + "', 'w').write('xd')",
    )
    text = [part.text for part in result.content]
    assert not ro_target.exists()
    assert any("PermissionError" in part for part in text)


def _workspace_returner(workspace: Path):
    async def fake_workspace_root(_context):
        return workspace

    return fake_workspace_root
