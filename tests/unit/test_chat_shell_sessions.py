"""Dashboard shell-session cold-start endpoint: service behavior tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.computer import computer_client
from astrbot.core.computer.booters.local import LocalShellComponent
from astrbot.dashboard.services.chat_service import ChatService, ChatServiceError

pytestmark = pytest.mark.asyncio


def _make_service() -> ChatService:
    service = ChatService.__new__(ChatService)
    service.db = MagicMock()
    service.core_lifecycle = MagicMock()
    service.conv_mgr = MagicMock()
    service.platform_history_mgr = MagicMock()
    service.umop_config_router = MagicMock()
    service.running_convs = {}
    service.chat_runs = {}
    service.chat_runs_by_session = {}
    service.save_bot_message = AsyncMock()
    return service


def _session(creator="alice"):
    return SimpleNamespace(
        creator=creator,
        platform_id="webchat",
        is_group=False,
        session_id="cid1",
    )


SESSION_ITEM = {
    "session_id": "sh_abc123",
    "pid": 4321,
    "status": "running",
    "exit_code": None,
    "started_at": 1759656000.0,
    "sandboxed": False,
    "unread_output_bytes": 128,
}


async def test_get_session_shell_sessions_returns_sessions(monkeypatch):
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=_session())
    shell = LocalShellComponent()
    monkeypatch.setattr(
        shell, "list_sessions", AsyncMock(return_value={"sessions": [SESSION_ITEM]})
    )
    monkeypatch.setattr(computer_client, "local_booter", SimpleNamespace(shell=shell))

    result = await service.get_session_shell_sessions("alice", "cid1")

    assert result == {"sessions": [SESSION_ITEM]}
    call = shell.list_sessions.await_args
    assert call.kwargs["owner_id"] == "webchat:FriendMessage:webchat!alice!cid1"
    assert call.kwargs["requester_id"] == "alice"
    assert call.kwargs["requester_is_admin"] is True


async def test_get_session_shell_sessions_rejects_foreign_session():
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=_session("bob"))
    with pytest.raises(ChatServiceError):
        await service.get_session_shell_sessions("alice", "cid1")


async def test_get_session_shell_sessions_rejects_missing_session():
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=None)
    with pytest.raises(ChatServiceError):
        await service.get_session_shell_sessions("alice", "cid1")


async def test_get_session_shell_sessions_empty_when_no_local_booter(monkeypatch):
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=_session())
    monkeypatch.setattr(computer_client, "local_booter", None)

    assert await service.get_session_shell_sessions("alice", "cid1") == {"sessions": []}


async def test_get_session_shell_sessions_empty_when_not_local_shell(monkeypatch):
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=_session())
    monkeypatch.setattr(
        computer_client, "local_booter", SimpleNamespace(shell=object())
    )

    assert await service.get_session_shell_sessions("alice", "cid1") == {"sessions": []}


PEEK_RESULT = {
    "session_id": "sh_abc123",
    "pid": 4321,
    "status": "running",
    "stdout": "hello\n",
    "stderr": "",
    "exit_code": None,
    "cursor": 6,
    "has_more": False,
    "session_closed": False,
}


async def test_get_shell_session_output_passthrough(monkeypatch):
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=_session())
    shell = LocalShellComponent()
    peek = AsyncMock(return_value=PEEK_RESULT)
    monkeypatch.setattr(shell, "peek_session_output", peek)
    monkeypatch.setattr(computer_client, "local_booter", SimpleNamespace(shell=shell))

    result = await service.get_shell_session_output(
        "alice", "cid1", "sh_abc123", cursor=0, max_chars=1000, yield_time_ms=2000
    )

    assert result == PEEK_RESULT
    kwargs = peek.await_args.kwargs
    assert kwargs["owner_id"] == "webchat:FriendMessage:webchat!alice!cid1"
    assert kwargs["requester_is_admin"] is True
    assert kwargs["cursor"] == 0
    assert kwargs["yield_time_ms"] == 2000


async def test_peek_removed_session_raises(monkeypatch):
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=_session())
    shell = LocalShellComponent()
    monkeypatch.setattr(
        shell,
        "peek_session_output",
        AsyncMock(side_effect=ValueError("Shell session sh_x was not found.")),
    )
    monkeypatch.setattr(computer_client, "local_booter", SimpleNamespace(shell=shell))

    with pytest.raises(ChatServiceError):
        await service.get_shell_session_output("alice", "cid1", "sh_x")


async def test_get_shell_session_output_requires_owner(monkeypatch):
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=_session("bob"))
    with pytest.raises(ChatServiceError):
        await service.get_shell_session_output("alice", "cid1", "sh_x")


async def test_terminate_shell_session_injects_notice(monkeypatch):
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=_session())
    shell = LocalShellComponent()
    monkeypatch.setattr(
        shell,
        "terminate_session",
        AsyncMock(return_value={"status": "terminated", "pid": 4321}),
    )
    monkeypatch.setattr(computer_client, "local_booter", SimpleNamespace(shell=shell))
    from astrbot.core.pipeline.process_stage import follow_up as follow_up_mod

    injected: list[tuple[str, str]] = []
    monkeypatch.setattr(
        follow_up_mod,
        "inject_system_notice_to_active_run",
        lambda umo, text: injected.append((umo, text)) or True,
    )

    result = await service.terminate_shell_session("alice", "cid1", "sh_abc123")

    assert result["status"] == "terminated"
    assert len(injected) == 1
    umo, text = injected[0]
    assert umo == "webchat:FriendMessage:webchat!alice!cid1"
    assert "sh_abc123" in text
    assert "4321" in text


async def test_terminate_notice_skipped_without_active_run(monkeypatch):
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=_session())
    shell = LocalShellComponent()
    monkeypatch.setattr(
        shell,
        "terminate_session",
        AsyncMock(return_value={"status": "terminated", "pid": 1}),
    )
    monkeypatch.setattr(computer_client, "local_booter", SimpleNamespace(shell=shell))
    from astrbot.core.pipeline.process_stage import follow_up as follow_up_mod

    # No active run: injection returns False, terminate still succeeds.
    monkeypatch.setattr(
        follow_up_mod, "inject_system_notice_to_active_run", lambda u, t: False
    )

    result = await service.terminate_shell_session("alice", "cid1", "sh_abc123")
    assert result["status"] == "terminated"
