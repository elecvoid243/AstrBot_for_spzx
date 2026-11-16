"""Session-scoped pushes must reach both chat transports.

The ChatUI defaults to the unified stream and opens no per-session system
stream, so publishing only through ``webchat_queue_mgr.put_system_event``
(which no-ops without a subscriber) left the shell indicator and goal bar
stale until a refresh.

Author: elecvoid243
Date: 2026-11-16
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.dashboard.services import chat_service as chat_service_module
from astrbot.dashboard.services.chat_service import ChatService

pytestmark = pytest.mark.asyncio

PAYLOAD = {"type": "shell_sessions_changed", "data": {"sessions": []}}


def _service(*, connections: dict, session) -> SimpleNamespace:
    """Minimal ChatService surface used by ``publish_session_event``."""
    return SimpleNamespace(
        db=SimpleNamespace(get_platform_session_by_id=AsyncMock(return_value=session)),
        unified_stream_hub=SimpleNamespace(
            connections=connections,
            publish=MagicMock(),
        ),
    )


async def test_fans_out_to_system_stream_and_unified_hub(monkeypatch):
    put = AsyncMock(return_value=True)
    monkeypatch.setattr(chat_service_module.webchat_queue_mgr, "put_system_event", put)
    service = _service(
        connections={"conn-1": object()},
        session=SimpleNamespace(creator="alice"),
    )

    await ChatService.publish_session_event(service, "cid1", PAYLOAD)

    put.assert_awaited_once_with("cid1", PAYLOAD)
    service.unified_stream_hub.publish.assert_called_once_with("alice", "cid1", PAYLOAD)


async def test_skips_owner_lookup_without_unified_connections(monkeypatch):
    monkeypatch.setattr(
        chat_service_module.webchat_queue_mgr,
        "put_system_event",
        AsyncMock(return_value=True),
    )
    service = _service(connections={}, session=SimpleNamespace(creator="alice"))

    await ChatService.publish_session_event(service, "cid1", PAYLOAD)

    service.db.get_platform_session_by_id.assert_not_awaited()
    service.unified_stream_hub.publish.assert_not_called()


async def test_uses_the_supplied_owner_without_a_lookup(monkeypatch):
    """Side-chat threads pass the origin's creator: no session row exists."""
    monkeypatch.setattr(
        chat_service_module.webchat_queue_mgr,
        "put_system_event",
        AsyncMock(return_value=True),
    )
    service = _service(connections={"conn-1": object()}, session=None)

    await ChatService.publish_session_event(service, "thread-1", PAYLOAD, owner="alice")

    service.db.get_platform_session_by_id.assert_not_awaited()
    service.unified_stream_hub.publish.assert_called_once_with(
        "alice", "thread-1", PAYLOAD
    )


async def test_skips_unified_publish_for_unknown_session(monkeypatch):
    monkeypatch.setattr(
        chat_service_module.webchat_queue_mgr,
        "put_system_event",
        AsyncMock(return_value=True),
    )
    service = _service(connections={"conn-1": object()}, session=None)

    await ChatService.publish_session_event(service, "missing", PAYLOAD)

    service.unified_stream_hub.publish.assert_not_called()
