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
