"""Tool-level tests for astrbot_apply_patch."""

from __future__ import annotations

import re
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

    def plan_then_mutate(entries, read, **kwargs):
        result = original_plan(entries, read, **kwargs)
        target.write_text("externally changed\n", encoding="utf-8")
        return result

    monkeypatch.setattr(patch_tool_module, "plan_patch", plan_then_mutate)
    result = await ApplyPatchTool().call(
        _context(), patch="*** Update File: a.txt\n-old\n+new\n"
    )
    assert "changed since plan" in result
    assert target.read_text(encoding="utf-8") == "externally changed\n"


@pytest.mark.asyncio
async def test_sandbox_update_uses_remote_io(monkeypatch):
    writes = []

    class FakeBooter:
        pass

    async def fake_read(sb, path):
        return b"old\n"

    async def fake_write(sb, path, data, *, create_parents=False):
        writes.append((path, data, create_parents))

    async def fake_remove(sb, path):
        writes.append((path, None, False))

    monkeypatch.setattr(patch_tool_module, "_sandbox_read_bytes", fake_read)
    monkeypatch.setattr(patch_tool_module, "_sandbox_write_bytes", fake_write)
    monkeypatch.setattr(patch_tool_module, "_sandbox_remove_file", fake_remove)
    monkeypatch.setattr(
        patch_tool_module,
        "get_booter",
        AsyncMock(return_value=FakeBooter()),
    )

    result = await ApplyPatchTool().call(
        _context(runtime="sandbox"),
        patch="*** Update File: /remote/a.txt\n-old\n+new\n",
    )
    assert "Applied patch" in result
    assert writes == [("/remote/a.txt", b"new\n", True)]


@pytest.mark.asyncio
async def test_parse_error_reports_line_number(workspace):
    result = await ApplyPatchTool().call(
        _context(), patch="*** Update File: a.txt\n?bad\n"
    )
    assert "(line 2)" in result


@pytest.mark.asyncio
async def test_hunk_failure_reports_file_and_hunk_number(workspace):
    (workspace / "a.txt").write_text("old\n", encoding="utf-8")
    result = await ApplyPatchTool().call(
        _context(),
        patch=("*** Update File: a.txt\n-old\n+new\n@@ never\n-never\n+x\n"),
    )
    assert "a.txt" in result
    assert "hunk #2" in result


@pytest.mark.asyncio
async def test_invalid_encoding_writes_nothing(workspace):
    target = workspace / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    result = await ApplyPatchTool().call(
        _context(),
        patch="*** Update File: a.txt\n-old\n+new\n",
        encoding="no-such-codec",
    )
    assert "encoding" in result.lower()
    assert target.read_text(encoding="utf-8") == "old\n"


@pytest.mark.asyncio
async def test_gbk_update_roundtrip(workspace):
    target = workspace / "gbk.txt"
    target.write_bytes("旧值\n".encode("gbk"))
    result = await ApplyPatchTool().call(
        _context(),
        patch="*** Update File: gbk.txt\n-旧值\n+新值\n",
        encoding="gbk",
    )
    assert "Applied patch" in result
    assert target.read_bytes() == "新值\n".encode("gbk")


@pytest.mark.asyncio
async def test_strict_indent_false_allows_tab_replacement(workspace):
    target = workspace / "a.py"
    target.write_text("def f():\n    return 1\n", encoding="utf-8")
    result = await ApplyPatchTool().call(
        _context(),
        patch="*** Update File: a.py\n-    return 1\n+\treturn 2\n",
        strict_indent=False,
    )
    assert "Applied patch" in result
    assert "\treturn 2" in target.read_text(encoding="utf-8")


# ── history / rollback (2026-10-18) ──────────────────────────────


@pytest.mark.asyncio
async def test_list_history_and_rollback(workspace):
    target = workspace / "a.txt"
    target.write_text("v1\n", encoding="utf-8")
    await ApplyPatchTool().call(_context(), patch="*** Update File: a.txt\n-v1\n+v2\n")
    history = await ApplyPatchTool().call(_context(), path="a.txt", list_history=True)
    assert "Edit history for" in history
    backup_id = re.search(r"\[([0-9T_]+)\]", history).group(1)

    rolled = await ApplyPatchTool().call(_context(), path="a.txt", backup_id=backup_id)
    assert "Successfully rolled back" in rolled
    assert target.read_text(encoding="utf-8") == "v1\n"


@pytest.mark.asyncio
async def test_history_mode_validation(workspace):
    (workspace / "a.txt").write_text("x\n", encoding="utf-8")
    result = await ApplyPatchTool().call(
        _context(),
        patch="*** Update File: a.txt\n-x\n+y\n",
        path="a.txt",
        list_history=True,
    )
    assert "exactly one mode" in result


@pytest.mark.asyncio
async def test_history_requires_path(workspace):
    result = await ApplyPatchTool().call(_context(), list_history=True)
    assert "`path` is required" in result


@pytest.mark.asyncio
async def test_list_history_allowed_in_readonly_mode(workspace):
    target = workspace / "a.txt"
    target.write_text("v1\n", encoding="utf-8")
    await ApplyPatchTool().call(_context(), patch="*** Update File: a.txt\n-v1\n+v2\n")
    result = await ApplyPatchTool().call(
        _context(mode="readonly"), path="a.txt", list_history=True
    )
    assert "Edit history for" in result


def test_description_documents_all_modes():
    desc = ApplyPatchTool().description
    for needle in (
        "Replace All",
        "list_history",
        "backup_id",
        "encoding",
        "strict_indent",
    ):
        assert needle in desc
