from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.tools import fs_access
from astrbot.core.tools.fs_access import FileAccessMode

UMO = "webchat:FriendMessage:webchat!tester!s1"


def _make_context(config: dict | None = None, umo: str = UMO) -> ContextWrapper:
    config_holder = SimpleNamespace(
        get_config=lambda umo=None: (
            config or {"provider_settings": {"file_access_default_mode": "full"}}
        )
    )
    event = SimpleNamespace(
        role="admin", unified_msg_origin=umo, get_sender_id=lambda: "tester"
    )
    astr_ctx = SimpleNamespace(context=config_holder, event=event)
    return ContextWrapper(context=astr_ctx)


class _FakeSp:
    """In-memory SharedPreferences stand-in for fs_access persistence."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], object] = {}

    async def session_get(self, umo, key, default=None):
        return self.store.get((umo, key), default)

    async def session_put(self, umo, key, value):
        self.store[(umo, key)] = value

    async def session_remove(self, umo, key):
        self.store.pop((umo, key), None)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(fs_access, "sp", _FakeSp())
    fs_access.reset()
    yield
    fs_access.reset()


def test_override_beats_config_default():
    fs_access.set_mode_for_umo(UMO, FileAccessMode.READONLY)
    mode = fs_access.get_mode_for_umo(
        UMO,
        default=fs_access.resolve_default(
            {"provider_settings": {"file_access_default_mode": "workspace"}}
        ),
    )
    assert mode is FileAccessMode.READONLY


def test_get_mode_reads_config_default_via_context():
    context = _make_context(
        {"provider_settings": {"file_access_default_mode": "workspace"}}
    )
    assert fs_access.get_mode(context) is FileAccessMode.WORKSPACE


def test_resolve_default_invalid_value_falls_back_to_full():
    assert (
        fs_access.resolve_default(
            {"provider_settings": {"file_access_default_mode": "bogus"}}
        )
        is FileAccessMode.FULL
    )


def test_restore_writable_returns_last_non_readonly():
    fs_access.set_mode_for_umo(UMO, FileAccessMode.WORKSPACE)
    fs_access.set_mode_for_umo(UMO, FileAccessMode.READONLY)
    restored = fs_access.restore_writable_for_umo(UMO, default=FileAccessMode.FULL)
    assert restored is FileAccessMode.WORKSPACE


def test_restore_writable_never_restores_readonly():
    fs_access.set_mode_for_umo(UMO, FileAccessMode.READONLY)
    restored = fs_access.restore_writable_for_umo(UMO, default=FileAccessMode.READONLY)
    assert restored is FileAccessMode.FULL


def test_count_readonly_overrides_and_clear():
    fs_access.set_mode_for_umo("u1", FileAccessMode.READONLY)
    fs_access.set_mode_for_umo("u2", FileAccessMode.WORKSPACE)
    assert fs_access.count_readonly_overrides() == 1
    fs_access.clear_mode_for_umo("u1")
    assert fs_access.count_readonly_overrides() == 0


def test_dynamic_roots_set_and_clear(tmp_path):
    fs_access.set_dynamic_roots(UMO, [tmp_path])
    assert fs_access.get_dynamic_roots(UMO) == (tmp_path,)
    fs_access.set_dynamic_roots(UMO, None)
    assert fs_access.get_dynamic_roots(UMO) == ()


@pytest.mark.asyncio
async def test_extra_write_roots_combines_config_and_dynamic(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    context = _make_context(
        {"provider_settings": {"file_access_extra_roots": [str(other)]}}
    )
    fs_access.set_dynamic_roots(UMO, [tmp_path])
    roots = await fs_access.extra_write_roots(context)
    assert other in roots
    assert tmp_path in roots


@pytest.mark.asyncio
async def test_custom_roots_roundtrip_normalized_and_deduped(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    roots = await fs_access.set_custom_roots(
        UMO, [str(repo), "  ", str(repo), str(tmp_path / "b")]
    )
    assert roots == (repo.resolve(), (tmp_path / "b").resolve())


@pytest.mark.asyncio
async def test_custom_roots_rehydrate_from_storage_after_reset(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    await fs_access.set_custom_roots(UMO, [str(repo)])
    fs_access.reset()  # clears the in-memory cache only, not SharedPreferences
    assert await fs_access.get_custom_roots(UMO) == (repo.resolve(),)


@pytest.mark.asyncio
async def test_extra_write_roots_include_custom_roots(tmp_path):
    custom = tmp_path / "custom"
    custom.mkdir()
    await fs_access.set_custom_roots(UMO, [str(custom)])
    roots = await fs_access.extra_write_roots(_make_context())
    assert custom.resolve() in roots


@pytest.mark.asyncio
async def test_custom_roots_storage_failure_fails_closed(monkeypatch):
    class _BrokenSp:
        async def session_get(self, *_args, **_kwargs):
            raise RuntimeError("storage down")

        async def session_put(self, *_args, **_kwargs):
            raise RuntimeError("storage down")

    monkeypatch.setattr(fs_access, "sp", _BrokenSp())
    fs_access.reset()
    assert await fs_access.get_custom_roots(UMO) == ()


@pytest.mark.asyncio
async def test_assert_writable_full_is_noop(tmp_path):
    await fs_access.assert_writable(str(tmp_path / "anywhere.txt"), _make_context())


@pytest.mark.asyncio
async def test_assert_writable_readonly_rejects(tmp_path):
    fs_access.set_mode_for_umo(UMO, FileAccessMode.READONLY)
    with pytest.raises(PermissionError):
        await fs_access.assert_writable(
            str(tmp_path / "a.txt"), _make_context(), current_workspace_root=tmp_path
        )


@pytest.mark.asyncio
async def test_assert_writable_workspace_blocks_outside(tmp_path):
    fs_access.set_mode_for_umo(UMO, FileAccessMode.WORKSPACE)
    ws = tmp_path / "ws"
    ws.mkdir()
    # Inside the workspace root passes.
    await fs_access.assert_writable(
        str(ws / "a.txt"), _make_context(), current_workspace_root=ws
    )
    # Outside fails.
    with pytest.raises(PermissionError):
        await fs_access.assert_writable(
            str(tmp_path / "outside.txt"),
            _make_context(),
            current_workspace_root=ws,
        )


@pytest.mark.asyncio
async def test_assert_writable_workspace_allows_dynamic_roots(tmp_path):
    fs_access.set_mode_for_umo(UMO, FileAccessMode.WORKSPACE)
    wt = tmp_path / "worktree"
    wt.mkdir()
    fs_access.set_dynamic_roots(UMO, [wt])
    await fs_access.assert_writable(str(wt / "main.py"), _make_context())


def test_is_tightening_orders_full_workspace_readonly():
    assert fs_access.is_tightening(FileAccessMode.FULL, FileAccessMode.WORKSPACE)
    assert fs_access.is_tightening(FileAccessMode.FULL, FileAccessMode.READONLY)
    assert fs_access.is_tightening(FileAccessMode.WORKSPACE, FileAccessMode.READONLY)
    assert not fs_access.is_tightening(
        FileAccessMode.READONLY, FileAccessMode.WORKSPACE
    )
    assert not fs_access.is_tightening(FileAccessMode.WORKSPACE, FileAccessMode.FULL)
    assert fs_access.is_tightening(None, FileAccessMode.WORKSPACE)
    assert not fs_access.is_tightening(None, FileAccessMode.FULL)


@pytest.mark.asyncio
async def test_mode_persists_and_loads_after_memory_reset():
    await fs_access.set_mode_for_umo_persisted(UMO, FileAccessMode.READONLY)
    fs_access.reset()

    loaded = await fs_access.load_mode_for_umo(UMO)

    assert loaded is FileAccessMode.READONLY
    assert fs_access.get_mode_for_umo(UMO) is FileAccessMode.READONLY


@pytest.mark.asyncio
async def test_clear_mode_removes_persisted_value():
    await fs_access.set_mode_for_umo_persisted(UMO, FileAccessMode.WORKSPACE)
    await fs_access.clear_mode_persisted_for_umo(UMO)
    fs_access.reset()

    assert await fs_access.load_mode_for_umo(UMO) is None


@pytest.mark.asyncio
async def test_sandbox_spec_windows_workspace_grants_workspace_and_extra_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    monkeypatch.setattr(sys, "platform", "win32")
    fs_access.set_mode_for_umo(UMO, FileAccessMode.WORKSPACE)
    extra = tmp_path / "extra"
    extra.mkdir()
    fs_access.set_dynamic_roots(UMO, [extra])

    spec = await fs_access.sandbox_spec_for_context(
        _make_context(), current_workspace_root=tmp_path
    )

    assert spec is not None
    assert spec.workspace == tmp_path.resolve()
    assert spec.workspace_writable is True
    assert spec.allow_network is False
    assert extra.resolve() in spec.writable_roots
    assert tmp_path.resolve() not in spec.writable_roots


@pytest.mark.asyncio
async def test_sandbox_spec_windows_readonly_has_no_writable_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    monkeypatch.setattr(sys, "platform", "win32")
    fs_access.set_mode_for_umo(UMO, FileAccessMode.READONLY)
    fs_access.set_dynamic_roots(UMO, [tmp_path / "extra"])

    spec = await fs_access.sandbox_spec_for_context(
        _make_context(), current_workspace_root=tmp_path
    )

    assert spec is not None
    assert spec.workspace_writable is False
    assert spec.writable_roots == ()


@pytest.mark.asyncio
async def test_sandbox_spec_is_none_in_full_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    fs_access.set_mode_for_umo(UMO, FileAccessMode.FULL)

    spec = await fs_access.sandbox_spec_for_context(
        _make_context(), current_workspace_root=tmp_path
    )

    assert spec is None


@pytest.mark.asyncio
async def test_sandbox_spec_is_none_on_non_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    fs_access.set_mode_for_umo(UMO, FileAccessMode.WORKSPACE)

    spec = await fs_access.sandbox_spec_for_context(
        _make_context(), current_workspace_root=tmp_path
    )

    assert spec is None


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="case-insensitive paths are Windows-only")
async def test_assert_writable_windows_case_insensitive(tmp_path):
    fs_access.set_mode_for_umo(UMO, FileAccessMode.WORKSPACE)
    ws = tmp_path / "MyWorkspace"
    ws.mkdir()
    context = _make_context()
    await fs_access.assert_writable(
        str(tmp_path / "myworkspace" / "a.txt"),
        context,
        current_workspace_root=ws,
    )


def test_default_config_contains_file_access_keys():
    from astrbot.core.config.default import DEFAULT_CONFIG

    provider_settings = DEFAULT_CONFIG["provider_settings"]
    assert provider_settings["file_access_default_mode"] == "full"
    assert provider_settings["file_access_extra_roots"] == []
