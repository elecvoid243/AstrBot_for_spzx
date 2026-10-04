"""Tests for the builtin ``astrbot_file_remove`` tool (fs.py FileRemoveTool).

Ported from the spcode plugin's ``tests`` expectations. ``send2trash`` is
always monkeypatched to a recorder so the real recycle bin is never touched.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.tools import fs_access
from astrbot.core.tools.computer_tools import fs as fs_tools
from astrbot.core.tools.computer_tools.fs import FileRemoveTool


def _make_context(
    *,
    require_admin: bool = True,
    role: str = "admin",
    runtime: str = "local",
    umo: str = "qq:friend:user-1",
    file_access_default_mode: str = "full",
    file_remove_blacklist: list[str] | None = None,
) -> ContextWrapper:
    config_holder = SimpleNamespace(
        get_config=lambda umo=None: {
            "provider_settings": {
                "computer_use_require_admin": require_admin,
                "computer_use_runtime": runtime,
                "file_access_default_mode": file_access_default_mode,
                "file_remove_blacklist": list(file_remove_blacklist or []),
            }
        }
    )
    event = SimpleNamespace(
        role=role,
        unified_msg_origin=umo,
        get_sender_id=lambda: "user-1",
    )
    # ``extra`` must be a real dict for _record_changed_file to aggregate.
    astr_ctx = SimpleNamespace(context=config_holder, event=event, extra={})
    return ContextWrapper(context=astr_ctx)


class _FakeSp:
    """In-memory stand-in for astrbot.core.sp (custom roots storage)."""

    def __init__(self) -> None:
        self.store: dict = {}

    async def session_get(self, umo, key, default=None):
        return self.store.get((umo, key), default)

    async def session_put(self, umo, key, value):
        self.store[(umo, key)] = value


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(fs_access, "sp", _FakeSp())
    fs_access.reset()
    yield
    fs_access.reset()


class _TrashRecorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, path) -> None:
        self.calls.append(str(path))


@pytest.fixture
def fake_trash(monkeypatch: pytest.MonkeyPatch) -> _TrashRecorder:
    import send2trash

    recorder = _TrashRecorder()
    monkeypatch.setattr(send2trash, "send2trash", recorder)
    return recorder


def _system_dir() -> str:
    return "C:/Windows" if os.name == "nt" else "/etc"


def _make_dir_link(link: Path, target: str) -> None:
    """Create a directory link (symlink on POSIX, junction on Windows).

    Windows symlink creation needs SeCreateSymbolicLinkPrivilege, but a
    directory junction is available to any user and ``Path.resolve`` follows
    it exactly like a symlink.
    """
    if os.name == "nt":
        # cmd parses forward slashes as option switches; feed a native path.
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(Path(target))],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip(f"cannot create directory junction: {result.stderr}")
    else:
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this platform")


def _remove_dir_link(link: Path) -> None:
    """Remove the link itself without touching the link target."""
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "rmdir", str(link)],
            capture_output=True,
            text=True,
        )
    else:
        link.unlink(missing_ok=True)


# ── single file ───────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_single_file(tmp_path, fake_trash):
    target = tmp_path / "victim.txt"
    target.write_text("bye", encoding="utf-8")
    ctx = _make_context()

    out = await FileRemoveTool().call(ctx, path=str(target))

    data = json.loads(out)
    assert data["ok"] is True
    assert data["deleted"] == 1
    assert fake_trash.calls == [str(target.resolve())]
    changed = ctx.context.extra["changed_files"]
    assert len(changed) == 1
    assert changed[0]["kind"] == "remove"
    assert changed[0]["runtime"] == "local"
    assert changed[0]["path"] == str(target.resolve())
    # Recorder is a fake: the file is still on disk.
    assert target.exists()


# ── path defense ──────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_rejects_dotdot():
    ctx = _make_context()
    out = await FileRemoveTool().call(ctx, path="../secret.txt")
    data = json.loads(out)
    assert data["ok"] is False
    assert ".." in out


@pytest.mark.asyncio
async def test_remove_rejects_unc_path():
    ctx = _make_context()
    out = await FileRemoveTool().call(ctx, path=r"\\server\share\file.txt")
    data = json.loads(out)
    assert data["ok"] is False
    assert "UNC" in out


@pytest.mark.asyncio
async def test_remove_rejects_system_dir():
    ctx = _make_context()
    forbidden = _system_dir()
    out = await FileRemoveTool().call(ctx, path=forbidden)
    data = json.loads(out)
    assert data["ok"] is False
    assert data["evidence"]["blocked_by"].lower().startswith(forbidden.lower())


@pytest.mark.asyncio
async def test_remove_rejects_symlink_to_system_dir(tmp_path):
    link = tmp_path / "link_to_system"
    target = _system_dir()
    _make_dir_link(link, target)
    try:
        ctx = _make_context()
        out = await FileRemoveTool().call(ctx, path=str(link))
    finally:
        _remove_dir_link(link)
    data = json.loads(out)
    assert data["ok"] is False
    assert data["evidence"]["blocked_by"].lower().startswith(target.lower())


@pytest.mark.asyncio
async def test_remove_rejects_user_blacklist(tmp_path):
    target = tmp_path / "secret" / "data.txt"
    target.parent.mkdir()
    target.write_text("x", encoding="utf-8")
    ctx = _make_context(file_remove_blacklist=[str(tmp_path)])

    out = await FileRemoveTool().call(ctx, path=str(target))

    data = json.loads(out)
    assert data["ok"] is False
    assert data["evidence"]["blocked_by"].startswith("user:")
    assert "user:" in out


@pytest.mark.asyncio
async def test_remove_missing_path(tmp_path):
    ctx = _make_context()
    out = await FileRemoveTool().call(ctx, path=str(tmp_path / "nope.txt"))
    data = json.loads(out)
    assert data["ok"] is False
    assert "不存在" in out


@pytest.mark.asyncio
async def test_remove_empty_path():
    ctx = _make_context()
    for value in ("", "   "):
        out = await FileRemoveTool().call(ctx, path=value)
        data = json.loads(out)
        assert data["ok"] is False


@pytest.mark.asyncio
async def test_remove_rejects_non_local_runtime():
    ctx = _make_context(runtime="sandbox")
    out = await FileRemoveTool().call(ctx, path="/whatever.txt")
    data = json.loads(out)
    assert data["ok"] is False


# ── directory batch semantics ─────────────────────────


@pytest.mark.asyncio
async def test_remove_dir_requires_confirm(tmp_path, fake_trash):
    directory = tmp_path / "adir"
    directory.mkdir()
    (directory / "a.txt").write_text("a", encoding="utf-8")
    ctx = _make_context()

    out = await FileRemoveTool().call(ctx, path=str(directory))

    data = json.loads(out)
    assert data["ok"] is False
    assert "proposal" in data
    assert "confirm" in data["proposal"].lower()
    assert "confirm_delete" in data["options"]
    assert fake_trash.calls == []
    assert directory.exists()
    assert ctx.context.extra.get("changed_files") in (None, [])


@pytest.mark.asyncio
async def test_remove_dir_string_confirm_does_not_delete(tmp_path, fake_trash):
    """A non-bool truthy ``confirm`` (e.g. "true") must NOT pass the gate."""
    directory = tmp_path / "adir"
    directory.mkdir()
    (directory / "a.txt").write_text("a", encoding="utf-8")
    ctx = _make_context()

    out = await FileRemoveTool().call(ctx, path=str(directory), confirm="true")

    data = json.loads(out)
    assert data["ok"] is False
    assert "proposal" in data
    assert "confirm_delete" in data["options"]
    assert fake_trash.calls == []
    assert directory.exists()
    assert ctx.context.extra.get("changed_files") in (None, [])


@pytest.mark.asyncio
async def test_remove_dir_over_max_items_proposal(tmp_path, fake_trash):
    directory = tmp_path / "big"
    directory.mkdir()
    for i in range(51):
        (directory / f"f{i}.txt").write_text("x", encoding="utf-8")
    ctx = _make_context()

    out = await FileRemoveTool().call(
        ctx, path=str(directory), confirm=True, max_items=50
    )

    data = json.loads(out)
    assert data["ok"] is False
    assert data["evidence"]["file_count"] == 51
    assert fake_trash.calls == []
    assert directory.exists()


@pytest.mark.asyncio
async def test_remove_dir_confirmed(tmp_path, fake_trash):
    directory = tmp_path / "tree"
    directory.mkdir()
    for i in range(3):
        (directory / f"f{i}.txt").write_text("x", encoding="utf-8")
    ctx = _make_context()

    out = await FileRemoveTool().call(ctx, path=str(directory), confirm=True)

    data = json.loads(out)
    assert data["ok"] is True
    assert data["deleted"] == 3
    assert fake_trash.calls == [str(directory.resolve())]
    changed = ctx.context.extra["changed_files"]
    assert changed[0]["kind"] == "remove"


@pytest.mark.asyncio
async def test_remove_dir_unreadable_child_skipped(
    tmp_path, fake_trash, monkeypatch: pytest.MonkeyPatch
):
    directory = tmp_path / "partial"
    directory.mkdir()
    (directory / "good.txt").write_text("g", encoding="utf-8")
    (directory / "bad.txt").write_text("b", encoding="utf-8")

    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        name = os.path.basename(os.fspath(path))
        if name == "bad.txt":
            raise OSError("unreadable child")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", fake_stat)

    ctx = _make_context()
    out = await FileRemoveTool().call(ctx, path=str(directory), confirm=True)

    data = json.loads(out)
    assert data["ok"] is True
    assert data["deleted"] == 1  # bad.txt skipped, good.txt counted
    assert fake_trash.calls == [str(directory.resolve())]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_max_items", [0, -5, "abc", None])
async def test_max_items_invalid_falls_back_to_50(
    tmp_path, fake_trash, bad_max_items
):
    directory = tmp_path / "small"
    directory.mkdir()
    for i in range(3):
        (directory / f"f{i}.txt").write_text("x", encoding="utf-8")
    ctx = _make_context()

    out = await FileRemoveTool().call(
        ctx, path=str(directory), confirm=True, max_items=bad_max_items
    )

    data = json.loads(out)
    # Anything other than a positive int must fall back to 50, so 3 files
    # are allowed through (a raw 0/-5 would have produced a proposal).
    assert data["ok"] is True
    assert data["deleted"] == 3


# ── access-mode guards ────────────────────────────────


@pytest.mark.asyncio
async def test_readonly_mode_rejected(tmp_path):
    target = tmp_path / "x.txt"
    target.write_text("x", encoding="utf-8")
    ctx = _make_context(file_access_default_mode="readonly")

    out = await FileRemoveTool().call(ctx, path=str(target))

    assert out == fs_tools._READONLY_WRITE_ERROR


@pytest.mark.asyncio
async def test_workspace_mode_outside_roots_rejected(tmp_path):
    target = tmp_path / "outside.txt"
    target.write_text("x", encoding="utf-8")
    ctx = _make_context(file_access_default_mode="workspace")

    out = await FileRemoveTool().call(ctx, path=str(target))

    assert out.startswith("Error:")
    assert "restricted" in out
    assert "Blocked path" in out


# ── recycle bin failure ───────────────────────────────


@pytest.mark.asyncio
async def test_send2trash_oserror_friendly_message(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    import send2trash

    def boom(_path):
        raise OSError("no trash service")

    monkeypatch.setattr(send2trash, "send2trash", boom)
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    ctx = _make_context()

    out = await FileRemoveTool().call(ctx, path=str(target))

    data = json.loads(out)
    assert data["ok"] is False
    assert "回收站不可用" in out
    assert "trash-cli" in out
