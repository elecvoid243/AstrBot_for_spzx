"""WebChatAdapter.create_event propagates the collab context payload field.

Covers the payload -> event-extra hop of the agent collab context pipeline
(deliver() puts collab_context into the queue payload; build_main_agent
reads it from event extras and injects it as a temp provider part).
"""

from astrbot.core.platform import MessageMember, MessageType
from astrbot.core.platform.astrbot_message import AstrBotMessage
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter

_COLLAB_CONTEXT = "[协作讨论] 你正在参与一场多会话协作讨论，主题：评审这个设计"


def _adapter() -> WebChatAdapter:
    """Adapter instance without the base-class side effects (dirs, db)."""
    adapter = object.__new__(WebChatAdapter)
    adapter.metadata = PlatformMetadata(
        id="webchat", name="webchat", description="webchat"
    )
    return adapter


def _message(payload: dict) -> AstrBotMessage:
    msg = AstrBotMessage()
    msg.type = MessageType.FRIEND_MESSAGE
    msg.self_id = "webchat"
    msg.session_id = "webchat!alice!conv123"
    msg.message_id = "mid1"
    msg.sender = MessageMember("alice", "alice")
    msg.message = []
    msg.message_str = "[来自 主持人]: 内容"
    msg.raw_message = ("alice", "conv123", payload)
    return msg


def test_collab_context_propagates_to_event_extra():
    event = _adapter().create_event(_message({"collab_context": _COLLAB_CONTEXT}))
    assert event.get_extra("collab_context") == _COLLAB_CONTEXT


def test_missing_collab_context_is_noop():
    event = _adapter().create_event(_message({}))
    assert event.get_extra("collab_context") is None


def test_blank_collab_context_is_noop():
    event = _adapter().create_event(_message({"collab_context": "   "}))
    assert event.get_extra("collab_context") is None


def test_persist_user_history_writes_record(monkeypatch):
    import asyncio

    from astrbot.core.platform.sources.webchat import webchat_adapter as wa

    inserted = []

    async def fake_insert(**kwargs):
        inserted.append(kwargs)
        return None

    monkeypatch.setattr(wa.db_helper, "insert_platform_message_history", fake_insert)
    payload = {
        "message": [{"type": "plain", "text": "[来自 主持人]: 内容"}],
        "persist_user_history": True,
    }
    asyncio.run(_adapter().convert_message(("alice", "conv123", payload)))
    assert len(inserted) == 1
    record = inserted[0]
    assert record["platform_id"] == "webchat"
    assert record["user_id"] == "conv123"
    assert record["content"]["type"] == "user"
    assert record["content"]["message"] == payload["message"]
    assert record["sender_id"] == "alice"


def test_regular_messages_do_not_write_history(monkeypatch):
    import asyncio

    from astrbot.core.platform.sources.webchat import webchat_adapter as wa

    called = False

    async def fake_insert(**kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(wa.db_helper, "insert_platform_message_history", fake_insert)
    asyncio.run(
        _adapter().convert_message(
            ("alice", "conv123", {"message": [{"type": "plain", "text": "普通消息"}]})
        )
    )
    assert not called


def test_orphan_bot_reply_is_persisted(monkeypatch):
    import asyncio

    from astrbot.core.platform.sources.webchat import webchat_event as we

    inserted = []

    async def fake_insert(**kwargs):
        inserted.append(kwargs)
        return None

    monkeypatch.setattr(we.db_helper, "insert_platform_message_history", fake_insert)
    # request id not registered on the conversation back queue -> no dashboard
    # consumer -> the reply must be persisted here
    asyncio.run(
        we._persist_bot_reply_if_orphan(
            session_id="webchat!alice!conv123",
            request_id="mid-orphan",
            plain_text="输出内容",
            reasoning_text="思考内容",
        )
    )
    assert len(inserted) == 1
    record = inserted[0]
    assert record["user_id"] == "conv123"
    assert record["content"]["type"] == "bot"
    assert record["content"]["message"] == [
        {"type": "think", "think": "思考内容"},
        {"type": "plain", "text": "输出内容"},
    ]


def test_consumed_reply_is_not_persisted(monkeypatch):
    import asyncio

    from astrbot.core.platform.sources.webchat import webchat_event as we

    inserted = []

    async def fake_insert(**kwargs):
        inserted.append(kwargs)
        return None

    monkeypatch.setattr(we.db_helper, "insert_platform_message_history", fake_insert)
    # registered request id -> ChatService._consume_chat_run owns persistence
    monkeypatch.setattr(
        we.webchat_queue_mgr, "list_back_request_ids", lambda cid: ["mid-owned"]
    )
    asyncio.run(
        we._persist_bot_reply_if_orphan(
            session_id="webchat!alice!conv123",
            request_id="mid-owned",
            plain_text="x",
        )
    )
    assert not inserted


def test_agent_stats_chain_is_not_persisted(monkeypatch):
    import asyncio

    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import Json
    from astrbot.core.platform.sources.webchat import webchat_event as we

    inserted = []

    async def fake_insert(**kwargs):
        inserted.append(kwargs)
        return None

    monkeypatch.setattr(we.db_helper, "insert_platform_message_history", fake_insert)
    stats_chain = MessageChain(
        type="agent_stats",
        chain=[Json(data={"token_usage": {"output": 1}})],
    )
    asyncio.run(
        we._persist_bot_reply_if_orphan(
            session_id="webchat!alice!conv123",
            request_id="mid-stats",
            message_chain=stats_chain,
        )
    )
    assert not inserted


def test_context_compression_chain_is_not_persisted(monkeypatch):
    """Author: elecvoid243, 2026-10-08.

    The compression notice is live-only telemetry (the dashboard renders a
    toast). Orphan turns (goal-loop / collab) have no dashboard accumulator to
    drop it, so this fallback must drop it too — otherwise the wire JSON would
    be persisted as a bot reply carrying only that blob.
    """
    import asyncio

    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import Json
    from astrbot.core.platform.sources.webchat import webchat_event as we

    inserted = []

    async def fake_insert(**kwargs):
        inserted.append(kwargs)
        return None

    monkeypatch.setattr(we.db_helper, "insert_platform_message_history", fake_insert)
    notice_chain = MessageChain(
        type="context_compression",
        chain=[
            Json(
                data={
                    "strategy": "llm_compress",
                    "tokens_before": 900,
                    "tokens_after": 348,
                }
            )
        ],
    )
    asyncio.run(
        we._persist_bot_reply_if_orphan(
            session_id="webchat!alice!conv123",
            request_id="mid-compression",
            message_chain=notice_chain,
        )
    )
    assert not inserted
