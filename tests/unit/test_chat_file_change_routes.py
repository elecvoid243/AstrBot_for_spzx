"""Unit tests for the chat file-change diff/restore routes.

The handlers are thin wrappers over EditHistoryManager + render_unified_diff,
so these exercise them via direct calls (auth Depends skipped with None).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from astrbot.core.tools.computer_tools.edit_history import EditHistoryManager


@pytest.fixture()
def seeded(tmp_path: Path) -> tuple[Path, EditHistoryManager, str]:
    target = tmp_path / "a.txt"
    target.write_bytes(b"v1\n")
    history = EditHistoryManager(base_dir=tmp_path / "hist")
    entry = history.save_backup(str(target), b"v1\n")
    target.write_bytes(b"v1\nv2\n")
    return target, history, entry.id


@pytest.mark.asyncio
async def test_diff_route(seeded, monkeypatch):
    from astrbot.dashboard.api import chat as chat_api

    target, history, backup_id = seeded
    monkeypatch.setattr(chat_api, "get_history_manager", lambda: history)
    payload = chat_api.ChatFileChangeDiffRequest(path=str(target), backup_id=backup_id)

    resp = await chat_api.chat_file_change_diff(payload, None)

    assert resp["status"] == "ok"
    data = resp["data"]
    assert data["adds"] == 1
    assert data["dels"] == 0
    assert "+v2" in data["diff"]
    assert data["truncated"] is False


@pytest.mark.asyncio
async def test_diff_route_created_requires_sha(seeded, monkeypatch):
    from astrbot.dashboard.api import chat as chat_api

    target, history, _ = seeded
    monkeypatch.setattr(chat_api, "get_history_manager", lambda: history)
    payload = chat_api.ChatFileChangeDiffRequest(path=str(target), backup_id="")

    resp = await chat_api.chat_file_change_diff(payload, None)

    assert resp["status"] == "error"


@pytest.mark.asyncio
async def test_diff_route_created_with_matching_sha(seeded, monkeypatch):
    from astrbot.dashboard.api import chat as chat_api

    target, history, _ = seeded
    monkeypatch.setattr(chat_api, "get_history_manager", lambda: history)
    payload = chat_api.ChatFileChangeDiffRequest(
        path=str(target),
        backup_id="",
        expect_sha256=hashlib.sha256(b"v1\nv2\n").hexdigest(),
    )

    resp = await chat_api.chat_file_change_diff(payload, None)

    assert resp["status"] == "ok"
    # Created files diff against an empty baseline: both lines are additions.
    assert resp["data"]["adds"] == 2
    assert resp["data"]["dels"] == 0


@pytest.mark.asyncio
async def test_restore_route_conflict(seeded, monkeypatch):
    from astrbot.dashboard.api import chat as chat_api

    target, history, backup_id = seeded
    monkeypatch.setattr(chat_api, "get_history_manager", lambda: history)
    payload = chat_api.ChatFileChangeRestoreRequest(
        path=str(target), backup_id=backup_id, expect_sha256="deadbeef"
    )

    resp = await chat_api.chat_file_change_restore(payload, None)

    assert resp["status"] == "error"
    # File untouched on conflict.
    assert target.read_bytes() == b"v1\nv2\n"


@pytest.mark.asyncio
async def test_restore_route_ok(seeded, monkeypatch):
    from astrbot.dashboard.api import chat as chat_api

    target, history, backup_id = seeded
    monkeypatch.setattr(chat_api, "get_history_manager", lambda: history)
    current = target.read_bytes()
    payload = chat_api.ChatFileChangeRestoreRequest(
        path=str(target),
        backup_id=backup_id,
        expect_sha256=hashlib.sha256(current).hexdigest(),
    )

    resp = await chat_api.chat_file_change_restore(payload, None)

    assert resp["status"] == "ok"
    assert target.read_bytes() == b"v1\n"
    # The pre-restore content must have been snapshotted before overwriting.
    entries = history.list_backups(str(target))
    assert any("[pre-restore snapshot]" == e.diff_preview for e in entries)


@pytest.mark.asyncio
async def test_status_route_reports_reverted(seeded, monkeypatch):
    """A file whose content matches its baseline is reported as reverted."""
    from astrbot.dashboard.api import chat as chat_api
    from astrbot.dashboard.schemas import (
        ChatFileChangeStatusItem,
        ChatFileChangeStatusRequest,
    )

    target, history, backup_id = seeded
    monkeypatch.setattr(chat_api, "get_history_manager", lambda: history)
    target.write_bytes(b"v1\n")  # Simulate a revert.
    payload = ChatFileChangeStatusRequest(
        files=[ChatFileChangeStatusItem(path=str(target), backup_id=backup_id)]
    )

    resp = await chat_api.chat_file_change_status(payload, None)

    assert resp["status"] == "ok"
    assert resp["data"]["files"] == [{"path": str(target), "reverted": True}]


@pytest.mark.asyncio
async def test_status_route_reports_active_change(seeded, monkeypatch):
    from astrbot.dashboard.api import chat as chat_api
    from astrbot.dashboard.schemas import (
        ChatFileChangeStatusItem,
        ChatFileChangeStatusRequest,
    )

    target, history, backup_id = seeded
    monkeypatch.setattr(chat_api, "get_history_manager", lambda: history)
    payload = ChatFileChangeStatusRequest(
        files=[
            ChatFileChangeStatusItem(path=str(target), backup_id=backup_id),
            ChatFileChangeStatusItem(path=str(target), backup_id="bogus"),
        ]
    )

    resp = await chat_api.chat_file_change_status(payload, None)

    assert resp["status"] == "ok"
    files = resp["data"]["files"]
    # File still differs from the baseline → active; bogus backup → not reverted.
    assert files[0]["reverted"] is False
    assert files[1]["reverted"] is False


# ── restore-removed (recycle-bin undo) ────────────────────────────────


@pytest.mark.asyncio
async def test_restore_removed_success(monkeypatch):
    """Backend reports ok → 200 response carrying the restored path."""
    from astrbot.dashboard.api import chat as chat_api

    calls: list[str] = []

    def fake_restore(path, *, recycle_root=None):
        calls.append(path)
        return {"ok": True, "restored_path": path}

    monkeypatch.setattr(
        chat_api, "restore_from_recycle_bin", fake_restore
    )
    payload = chat_api.ChatFileChangeRestoreRemovedRequest(path="C:/tmp/gone.txt")

    resp = await chat_api.chat_file_change_restore_removed(payload, None)

    assert resp["status"] == "ok"
    assert resp["data"]["restored_path"] == "C:/tmp/gone.txt"
    assert calls == ["C:/tmp/gone.txt"]


@pytest.mark.asyncio
async def test_restore_removed_not_found(monkeypatch):
    """Backend reports ok:False → error response surfacing its message."""
    from astrbot.dashboard.api import chat as chat_api

    def fake_restore(path, *, recycle_root=None):
        return {"ok": False, "error": "not in recycle bin"}

    monkeypatch.setattr(
        chat_api, "restore_from_recycle_bin", fake_restore
    )
    payload = chat_api.ChatFileChangeRestoreRemovedRequest(path="C:/tmp/gone.txt")

    resp = await chat_api.chat_file_change_restore_removed(payload, None)

    assert resp["status"] == "error"
    assert resp["message"] == "not in recycle bin"


@pytest.mark.asyncio
async def test_restore_removed_blank_path(monkeypatch):
    """Whitespace-only path → error without touching the backend."""
    from astrbot.dashboard.api import chat as chat_api

    def fail_backend(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("backend must not be called for a blank path")

    monkeypatch.setattr(
        chat_api, "restore_from_recycle_bin", fail_backend
    )
    payload = chat_api.ChatFileChangeRestoreRemovedRequest(path="   ")

    resp = await chat_api.chat_file_change_restore_removed(payload, None)

    assert resp["status"] == "error"
