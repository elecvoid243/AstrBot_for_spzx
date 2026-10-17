"""Tool-level tests for astrbot_apply_patch."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.tools.computer_tools import fs as fs_tools
from astrbot.core.tools.computer_tools import patch_tool as patch_tool_module
from astrbot.core.tools.computer_tools import util as computer_util
from astrbot.core.tools.computer_tools.patch_tool import ApplyPatchTool


def _context(*, runtime="local", umo="qq:friend:user-1", mode="full", role="admin"):
    holder = SimpleNamespace(
        get_config=lambda umo=None: {
            "provider_settings": {
                "computer_use_require_admin": False,
                "computer_use_runtime": runtime,
                "file_access_default_mode": mode,
            }
        }
    )
    event = SimpleNamespace(
        role=role, unified_msg_origin=umo, get_sender_id=lambda: "user-1"
    )
    return ContextWrapper(context=SimpleNamespace(context=holder, event=event))


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    monkeypatch.setattr(
        computer_util, "get_astrbot_workspaces_path", lambda: str(workspaces)
    )
    monkeypatch.setattr(
        fs_tools.fs_access,
        "sp",
        SimpleNamespace(
            session_get=AsyncMock(return_value=None),
            session_put=AsyncMock(),
        ),
    )
    ws = workspaces / computer_util.normalize_umo_for_workspace("qq:friend:user-1")
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.mark.asyncio
async def test_dry_run_changes_nothing(workspace):
    target = workspace / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    result = await ApplyPatchTool().call(
        _context(), patch="*** Update File: a.txt\n-old\n+new\n", dry_run=True
    )
    assert "Dry run" in result
    assert target.read_text(encoding="utf-8") == "old\n"


@pytest.mark.asyncio
async def test_delete_requires_confirm_but_dry_run_reports_it(workspace):
    target = workspace / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    result = await ApplyPatchTool().call(
        _context(), patch="*** Delete File: a.txt\n", dry_run=True
    )
    assert "confirm=true" in result
    assert target.exists()


@pytest.mark.asyncio
async def test_delete_without_confirm_does_not_write(workspace):
    target = workspace / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    result = await ApplyPatchTool().call(_context(), patch="*** Delete File: a.txt\n")
    assert "NOT applied" in result
    assert target.exists()


@pytest.mark.asyncio
async def test_readonly_mode_rejects(workspace):
    target = workspace / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    result = await ApplyPatchTool().call(
        _context(mode="readonly"),
        patch="*** Update File: a.txt\n-old\n+new\n",
    )
    assert "readonly" in result.lower()
    assert target.read_text(encoding="utf-8") == "old\n"


@pytest.mark.asyncio
async def test_path_escape_rejected(workspace):
    result = await ApplyPatchTool().call(
        _context(mode="workspace"),
        patch="*** Update File: ../outside.txt\n-a\n+b\n",
    )
    assert "denied" in result.lower() or "restricted" in result.lower()


@pytest.mark.asyncio
async def test_commit_writes_update(workspace):
    target = workspace / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    result = await ApplyPatchTool().call(
        _context(), patch="*** Update File: a.txt\n-old\n+new\n"
    )
    assert "Applied patch" in result
    assert target.read_text(encoding="utf-8") == "new\n"


@pytest.mark.asyncio
async def test_commit_add_and_delete(workspace):
    doomed = workspace / "old.txt"
    doomed.write_text("bye\n", encoding="utf-8")
    result = await ApplyPatchTool().call(
        _context(),
        patch=("*** Add File: new.txt\n+hi\n*** Delete File: old.txt\n"),
        confirm=True,
    )
    assert "Applied patch" in result
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "hi\n"
    assert not doomed.exists()


@pytest.mark.asyncio
async def test_failed_second_write_rolls_back_first(workspace, monkeypatch):
    first = workspace / "a.txt"
    second = workspace / "b.txt"
    first.write_text("a\n", encoding="utf-8")
    second.write_text("b\n", encoding="utf-8")

    original_write = patch_tool_module._write_file_bytes

    def flaky_write(path, data):
        if Path(path) == second:
            raise OSError("disk full")
        return original_write(path, data)

    monkeypatch.setattr(patch_tool_module, "_write_file_bytes", flaky_write)
    result = await ApplyPatchTool().call(
        _context(),
        patch=("*** Update File: a.txt\n-a\n+A\n*** Update File: b.txt\n-b\n+B\n"),
    )
    assert "No files were modified" in result
    assert first.read_text(encoding="utf-8") == "a\n"
    assert second.read_text(encoding="utf-8") == "b\n"


@pytest.mark.asyncio
async def test_changed_since_plan_aborts(workspace, monkeypatch):
    target = workspace / "a.txt"
    target.write_text("old\n", encoding="utf-8")

    original_plan = patch_tool_module.plan_patch

    def plan_then_mutate(entries, read):
        result = original_plan(entries, read)
        target.write_text("externally changed\n", encoding="utf-8")
        return result

    monkeypatch.setattr(patch_tool_module, "plan_patch", plan_then_mutate)
    result = await ApplyPatchTool().call(
        _context(), patch="*** Update File: a.txt\n-old\n+new\n"
    )
    assert "changed since plan" in result
    assert target.read_text(encoding="utf-8") == "externally changed\n"
