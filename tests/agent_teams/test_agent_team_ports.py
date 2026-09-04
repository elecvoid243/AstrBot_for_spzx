"""TeamPorts roundtrip over the real WebChatQueueMgr contract.

Mirrors tests/test_agent_collab_integration.py: a fake listener plays the
role of the webchat adapter, mirroring a scripted reply to system
subscribers after receiving the injected input.
"""

import pytest

from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
    WebChatQueueMgr,
    webchat_queue_mgr,
)
from astrbot.dashboard.services.agent_team_ports import (
    build_ports,
    build_ports_for_test,
)


@pytest.mark.asyncio
async def test_deliver_collect_roundtrip_and_registrar_order():
    mgr = WebChatQueueMgr()
    scripted = {"conv-1": ["成员回复"]}
    registered: list[tuple[str, str]] = []
    inbox: list[dict] = []

    async def fake_listener(data):
        username, conv_id, payload = data
        inbox.append(payload)
        mid = payload["message_id"]
        for chunk, t in ((scripted[conv_id].pop(0), "plain"), ("", "end")):
            await mgr.put_system_event(
                conv_id,
                {
                    "type": t,
                    "message_id": mid,
                    "data": chunk,
                    "streaming": False,
                    "chain_type": "normal",
                },
            )

    mgr.set_listener(fake_listener)

    async def run_registrar(cid, message_id, checkpoint_id):
        registered.append((cid, message_id))

    events: list[dict] = []
    ports = build_ports_for_test(
        mgr, "alice", emit=events.append, run_registrar=run_registrar
    )
    message_id = await ports.deliver("conv-1", "任务文本", "团队上下文")
    reply, parts = await ports.collect("conv-1", message_id)

    assert reply == "成员回复"
    # register MUST have happened before the input was queued
    assert registered and registered[0][1] == message_id
    payload = inbox[0]
    assert payload["team_context"] == "团队上下文"
    assert payload["persist_user_history"] is True
    assert any(e["type"] == "message" and e["direction"] == "stream" for e in events)


@pytest.mark.asyncio
async def test_build_ports_registrar_passes_username_and_checkpoint():
    """build_ports must call ChatService.register_synthetic_chat_run with the
    run owner's username in the username slot and the payload's checkpoint id
    in the llm_checkpoint_id slot (matching the real 4-arg signature)."""

    class FakeChatService:
        def __init__(self):
            self.chat_runs_by_session: dict = {}
            self.calls: list[tuple[str, str, str, str | None]] = []

        async def register_synthetic_chat_run(
            self,
            session_id: str,
            message_id: str,
            username: str,
            llm_checkpoint_id: str | None = None,
        ) -> None:
            self.calls.append((session_id, message_id, username, llm_checkpoint_id))

    cid = "conv-prod-wiring-1"
    fake = FakeChatService()
    ports = build_ports(fake, "alice", emit=lambda e: None)
    message_id = await ports.deliver(cid, "hi", None)

    session_id, mid, owner, checkpoint = fake.calls[0]
    payload = webchat_queue_mgr.queues[cid].get_nowait()[2]
    assert session_id == cid
    assert owner == "alice"
    assert mid == message_id == payload["message_id"]
    assert checkpoint == payload["llm_checkpoint_id"]
    webchat_queue_mgr.remove_queues(cid)
