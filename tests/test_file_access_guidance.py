"""File access mode guidance injection and the ``/mode`` command."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.builtin_stars.astrbot.file_access_guidance import (
    inject_file_access_guidance,
)
from astrbot.builtin_stars.astrbot import file_access_guidance as guidance_mod
from astrbot.builtin_stars.builtin_commands.commands import file_access as cmd_mod
from astrbot.builtin_stars.builtin_commands.commands.file_access import (
    FileAccessCommands,
)
from astrbot.core.agent.message import TextPart
from astrbot.core.config import default as default_config
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.tools import fs_access
from astrbot.core.tools.fs_access import FileAccessMode

UMO = "webchat:FriendMessage:webchat!tester!s1"

CFG = {
    "provider_settings": {
        "file_access_default_mode": "full",
        "file_access_readonly_blocked_tools": [
            "astrbot_file_write_tool",
            " code_format ",
            "",
        ],
    }
}


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


def _ctx(cfg: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(get_config=lambda umo=None: CFG if cfg is None else cfg)


def _event() -> SimpleNamespace:
    return SimpleNamespace(unified_msg_origin=UMO, set_result=MagicMock())


# ── config helpers ────────────────────────────────────────────────


def test_readonly_blocked_tools_trims_and_drops_empty():
    assert fs_access.readonly_blocked_tools(CFG) == (
        "astrbot_file_write_tool",
        "code_format",
    )


def test_readonly_blocked_tools_empty_when_unconfigured():
    assert fs_access.readonly_blocked_tools({}) == ()


def test_default_blocklist_excludes_shell_and_python():
    """Shell/Python stay available: the Windows ACL sandbox constrains them."""
    tools = default_config.DEFAULT_CONFIG["provider_settings"][
        "file_access_readonly_blocked_tools"
    ]
    assert "astrbot_execute_shell" not in tools
    assert "astrbot_execute_python" not in tools
    assert "astrbot_file_write_tool" in tools
    assert "astrbot_apply_patch" in tools


def test_mode_guidance_has_stable_markers():
    assert fs_access.mode_guidance(FileAccessMode.FULL).splitlines()[0] == (
        "[File access mode: full permissions]"
    )
    assert fs_access.mode_guidance(FileAccessMode.WORKSPACE).splitlines()[0] == (
        "[File access mode: workspace write access]"
    )
    assert fs_access.mode_guidance(FileAccessMode.READONLY).splitlines()[0] == (
        "[File access mode: read-only]"
    )


def test_mode_guidance_never_claims_unrestricted_reads():
    """Read access is not mode-dependent, so the guidance must not imply it."""
    for mode in FileAccessMode:
        assert "read files freely" not in fs_access.mode_guidance(mode)


def test_workspace_guidance_lists_roots():
    text = fs_access.mode_guidance(
        FileAccessMode.WORKSPACE, writable_roots=["D:/a", "C:/b"]
    )
    assert text.splitlines() == [
        "[File access mode: workspace write access]",
        "Write operations are restricted to these paths:",
        "- D:/a",
        "- C:/b",
        "Writes outside these paths are rejected.",
    ]


# ── guidance injection ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_readonly_injects_denials_and_stored_guidance():
    fs_access.set_mode_for_umo(UMO, FileAccessMode.READONLY)
    req = ProviderRequest(prompt="hi")

    await inject_file_access_guidance(_ctx(), _event(), req)

    assert req.denied_tools == {"astrbot_file_write_tool", "code_format"}
    assert len(req.extra_user_content_parts) == 1
    part = req.extra_user_content_parts[0]
    assert isinstance(part, TextPart)
    assert "read-only" in part.text
    # store=True: the part must survive into the conversation history.
    assert part._no_save is False


@pytest.mark.asyncio
async def test_full_mode_injects_guidance_without_denials():
    req = ProviderRequest(prompt="hi")

    await inject_file_access_guidance(_ctx(), _event(), req)

    assert req.denied_tools == set()
    assert len(req.extra_user_content_parts) == 1
    assert "full permissions" in req.extra_user_content_parts[0].text


@pytest.mark.asyncio
async def test_guidance_is_not_duplicated():
    req = ProviderRequest(prompt="hi")

    await inject_file_access_guidance(_ctx(), _event(), req)
    await inject_file_access_guidance(_ctx(), _event(), req)

    assert len(req.extra_user_content_parts) == 1


@pytest.mark.asyncio
async def test_config_failure_leaves_request_untouched():
    def _boom(umo=None):
        raise RuntimeError("config unavailable")

    req = ProviderRequest(prompt="hi")
    await inject_file_access_guidance(
        SimpleNamespace(get_config=_boom), _event(), req
    )

    assert req.extra_user_content_parts == []
    assert req.denied_tools == set()


# ── /mode command ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mode_command_switches_and_stops_local_execution(
    monkeypatch: pytest.MonkeyPatch,
):
    shutdown = AsyncMock()
    monkeypatch.setattr(cmd_mod, "shutdown_local_execution_for_owner", shutdown)
    event = _event()

    await FileAccessCommands(_ctx()).set_mode(event, "readonly")

    assert fs_access.get_mode_for_umo(UMO) is FileAccessMode.READONLY
    shutdown.assert_awaited_once_with(UMO)
    assert event.set_result.called


@pytest.mark.asyncio
async def test_mode_command_loosening_does_not_stop_execution(
    monkeypatch: pytest.MonkeyPatch,
):
    shutdown = AsyncMock()
    monkeypatch.setattr(cmd_mod, "shutdown_local_execution_for_owner", shutdown)
    fs_access.set_mode_for_umo(UMO, FileAccessMode.READONLY)

    await FileAccessCommands(_ctx()).set_mode(_event(), "full")

    assert fs_access.get_mode_for_umo(UMO) is FileAccessMode.FULL
    shutdown.assert_not_awaited()


@pytest.mark.asyncio
async def test_mode_command_reports_current_without_argument():
    event = _event()

    await FileAccessCommands(_ctx()).set_mode(event, None)

    assert event.set_result.called
    assert fs_access.get_mode_for_umo(UMO) is FileAccessMode.FULL


@pytest.mark.asyncio
async def test_mode_command_rejects_unknown_mode():
    event = _event()

    await FileAccessCommands(_ctx()).set_mode(event, "banana")

    assert event.set_result.called
    assert fs_access.get_mode_for_umo(UMO) is FileAccessMode.FULL


@pytest.mark.asyncio
async def test_mode_command_same_mode_is_noop(monkeypatch: pytest.MonkeyPatch):
    shutdown = AsyncMock()
    monkeypatch.setattr(cmd_mod, "shutdown_local_execution_for_owner", shutdown)
    fs_access.set_mode_for_umo(UMO, FileAccessMode.READONLY)

    await FileAccessCommands(_ctx()).set_mode(_event(), "readonly")

    shutdown.assert_not_awaited()


@pytest.mark.asyncio
async def test_workspace_injects_stored_guidance_with_paths(
    monkeypatch: pytest.MonkeyPatch,
):
    fs_access.set_mode_for_umo(UMO, FileAccessMode.WORKSPACE)
    monkeypatch.setattr(
        guidance_mod,
        "_workspace_writable_roots",
        AsyncMock(return_value=("D:/ws", "C:/tmp")),
    )
    req = ProviderRequest(prompt="hi")

    await inject_file_access_guidance(_ctx(), _event(), req)

    assert len(req.extra_user_content_parts) == 1
    part = req.extra_user_content_parts[0]
    assert part._no_save is False  # stored with the conversation
    assert part.text.splitlines()[0] == "[File access mode: workspace write access]"
    assert "- D:/ws" in part.text
    assert "- C:/tmp" in part.text


@pytest.mark.asyncio
async def test_workspace_paths_follow_whitelist_edits(
    monkeypatch: pytest.MonkeyPatch,
):
    """A whitelist edit must appear on the very next request."""
    fs_access.set_mode_for_umo(UMO, FileAccessMode.WORKSPACE)
    roots = AsyncMock(return_value=("D:/a",))
    monkeypatch.setattr(guidance_mod, "_workspace_writable_roots", roots)

    first = ProviderRequest(prompt="hi")
    await inject_file_access_guidance(_ctx(), _event(), first)
    assert "- D:/a" in first.extra_user_content_parts[0].text

    roots.return_value = ("D:/a", "D:/b")
    second = ProviderRequest(prompt="next")
    await inject_file_access_guidance(_ctx(), _event(), second)
    assert "- D:/b" in second.extra_user_content_parts[0].text


@pytest.mark.asyncio
async def test_history_declaration_prevents_duplicate():
    """On later turns the declaration lives in contexts, not the new request."""
    stored = fs_access.mode_guidance(FileAccessMode.FULL)
    req = ProviderRequest(
        prompt="hi",
        contexts=[
            {"role": "user", "content": [{"type": "text", "text": stored}]}
        ],
    )

    await inject_file_access_guidance(_ctx(), _event(), req)

    assert req.extra_user_content_parts == []


@pytest.mark.asyncio
async def test_workspace_guidance_not_duplicated_when_whitelist_unchanged(
    monkeypatch: pytest.MonkeyPatch,
):
    """Identical paths must not be appended again: keeps the prefix cache warm."""
    fs_access.set_mode_for_umo(UMO, FileAccessMode.WORKSPACE)
    monkeypatch.setattr(
        guidance_mod, "_workspace_writable_roots", AsyncMock(return_value=("D:/a",))
    )
    stored = fs_access.mode_guidance(
        FileAccessMode.WORKSPACE, writable_roots=["D:/a"]
    )
    req = ProviderRequest(
        prompt="hi",
        contexts=[
            {"role": "user", "content": [{"type": "text", "text": stored}]}
        ],
    )

    await inject_file_access_guidance(_ctx(), _event(), req)

    assert req.extra_user_content_parts == []


@pytest.mark.asyncio
async def test_workspace_roots_include_custom_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    extra = tmp_path / "extra"
    monkeypatch.setattr(
        guidance_mod,
        "resolve_workspace_root_for_umo",
        AsyncMock(return_value=workspace),
    )
    await fs_access.set_custom_roots(UMO, [str(extra)])

    roots = await guidance_mod._workspace_writable_roots(CFG, UMO)

    assert str(workspace.resolve()) in roots
    assert str(extra.resolve()) in roots
    assert len(roots) == len(set(roots))
