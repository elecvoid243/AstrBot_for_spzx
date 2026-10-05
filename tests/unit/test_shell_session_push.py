"""Push wiring: shell-session snapshots to the webchat system stream."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import astrbot.dashboard.api.app as app_module
from astrbot.core.computer import computer_client
from astrbot.core.computer.booters.local import LocalShellComponent
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
    webchat_queue_mgr,
)

pytestmark = pytest.mark.asyncio

UMO = "webchat:FriendMessage:webchat!alice!cid1"

SESSION_ITEM = {
    "session_id": "sh_abc123",
    "pid": 4321,
    "status": "running",
    "exit_code": None,
    "started_at": 1759656000.0,
    "sandboxed": False,
    "unread_output_bytes": 128,
}


def _record_system_events(monkeypatch) -> list:
    calls: list = []

    async def fake_put(conversation_id, payload):
        calls.append((conversation_id, payload))
        return True

    monkeypatch.setattr(webchat_queue_mgr, "put_system_event", fake_put)
    return calls


async def test_push_ignores_non_webchat_owner(monkeypatch):
    calls = _record_system_events(monkeypatch)

    await app_module._shell_sessions_changed("telegram:GroupMessage:telegram!u!g1")

    assert calls == []


async def test_push_sends_snapshot_payload(monkeypatch):
    calls = _record_system_events(monkeypatch)
    shell = LocalShellComponent()
    monkeypatch.setattr(
        shell, "list_sessions", AsyncMock(return_value={"sessions": [SESSION_ITEM]})
    )
    monkeypatch.setattr(computer_client, "local_booter", SimpleNamespace(shell=shell))

    await app_module._shell_sessions_changed(UMO)

    assert calls == [
        (
            "cid1",
            {
                "type": "shell_sessions_changed",
                "data": {"sessions": [SESSION_ITEM]},
            },
        )
    ]
    kwargs = shell.list_sessions.await_args.kwargs
    assert kwargs["owner_id"] == UMO
    assert kwargs["requester_is_admin"] is True


async def test_push_no_booter_is_noop(monkeypatch):
    calls = _record_system_events(monkeypatch)
    monkeypatch.setattr(computer_client, "local_booter", None)

    await app_module._shell_sessions_changed(UMO)

    assert calls == []


async def test_push_non_local_shell_is_noop(monkeypatch):
    calls = _record_system_events(monkeypatch)
    monkeypatch.setattr(
        computer_client, "local_booter", SimpleNamespace(shell=object())
    )

    await app_module._shell_sessions_changed(UMO)

    assert calls == []
