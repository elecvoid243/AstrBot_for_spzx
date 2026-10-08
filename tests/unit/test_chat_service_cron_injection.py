"""Tests for the cron -> webchat synthetic turn injection seam."""

from unittest.mock import AsyncMock

import pytest

from astrbot.dashboard.services import chat_service as chat_service_module
from astrbot.dashboard.services.chat_service import ChatService, ChatServiceError


def _service(chat_runs_by_session=None) -> ChatService:
    service = ChatService.__new__(ChatService)
    service.chat_runs = {}
    service.chat_runs_by_session = chat_runs_by_session or {}
    return service


@pytest.mark.asyncio
async def test_inject_registers_synthetic_run_before_queueing(monkeypatch):
    """The run must own the back queue before the input item is queued."""
    calls: list[tuple] = []

    class FakeQueue:
        async def put(self, item) -> None:
            calls.append(("put", item))

    class FakeQueueMgr:
        def get_or_create_queue(self, cid: str) -> FakeQueue:
            calls.append(("queue", cid))
            return FakeQueue()

    async def fake_register(session_id, message_id, username, llm_checkpoint_id=None):
        calls.append(("register", session_id, message_id, username))

    monkeypatch.setattr(chat_service_module, "webchat_queue_mgr", FakeQueueMgr())
    service = _service()
    monkeypatch.setattr(service, "register_synthetic_chat_run", fake_register)

    await service.inject_cron_turn(
        cid="conv-1", username="alice", message_id="msg-1", text="ping the agent"
    )

    assert [call[0] for call in calls] == ["register", "queue", "put"]
    assert calls[0][1:] == ("conv-1", "msg-1", "alice")
    _, queue_key = calls[1]
    assert queue_key == "conv-1"
    _, item = calls[2]
    sender, cid, payload = item
    assert (sender, cid) == ("alice", "conv-1")
    assert payload["message"] == [{"type": "plain", "text": "ping the agent"}]
    assert payload["message_id"] == "msg-1"
    assert payload["persist_user_history"] is True
    assert payload["llm_checkpoint_id"]


@pytest.mark.asyncio
async def test_inject_refuses_while_session_busy(monkeypatch):
    """A running turn on the target session must skip, not interleave."""
    service = _service({"conv-1": {"run-in-flight"}})
    register = AsyncMock()
    monkeypatch.setattr(service, "register_synthetic_chat_run", register)

    with pytest.raises(ChatServiceError, match="busy"):
        await service.inject_cron_turn(
            cid="conv-1", username="alice", message_id="msg-1", text="ping"
        )

    register.assert_not_awaited()
