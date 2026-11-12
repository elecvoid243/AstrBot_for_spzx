import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.dashboard.api.chat import unified_send_chat
from astrbot.dashboard.services.chat_service import (
    ChatRunState,
    ChatService,
    ChatServiceError,
)
from astrbot.dashboard.services.unified_stream import UnifiedStreamHub


def _decode(event: str) -> dict:
    """Decode one SSE event emitted by the unified hub.

    Args:
        event: Complete SSE event text.

    Returns:
        Decoded frame payload.
    """
    return json.loads(event.removeprefix("data: ").strip())


class _JsonRequest:
    """Minimal Request stand-in for route-level tests."""

    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self) -> dict:
        return self._payload


@pytest.mark.asyncio
async def test_unified_hub_sequences_frames_per_session_and_account():
    hub = UnifiedStreamHub()
    alice = hub.connect("alice")
    bob = hub.connect("bob")
    alice_stream = hub.stream(alice)
    bob_stream = hub.stream(bob)

    ready = _decode(await anext(alice_stream))
    assert ready == {"type": "stream_ready", "epoch": hub.epoch}

    hub.publish(
        "alice",
        "s1",
        {"type": "plain", "data": "hello", "message_id": "run-1"},
    )
    frame = _decode(await anext(alice_stream))
    assert frame == {
        "type": "plain",
        "data": "hello",
        "message_id": "run-1",
        "session_id": "s1",
        "seq": 1,
    }
    assert bob.queue.qsize() == 0

    hub.publish(
        "alice",
        "s1",
        {"type": "end", "data": None, "message_id": "run-1"},
    )
    assert _decode(await anext(alice_stream))["seq"] == 2

    hub.disconnect(alice)
    hub.disconnect(bob)
    await alice_stream.aclose()
    await bob_stream.aclose()


@pytest.mark.asyncio
async def test_start_unified_chat_run_publishes_run_started_to_end():
    hub = UnifiedStreamHub()
    service = SimpleNamespace(
        unified_stream_hub=hub,
        chat_runs={},
        _unified_publish_tasks=set(),
    )

    async def fake_build(username, post_data, *, message_id=None):
        run = ChatRunState(
            run_id=message_id,
            username=username,
            session_id="s1",
            llm_checkpoint_id="ckpt",
            platform_history_id="webchat",
            back_queue=asyncio.Queue(),
            unified_input_message={"id": 7, "content": {"type": "user"}},
            user_message_id=7,
        )
        service.chat_runs[message_id] = run

        async def stream():
            yield 'data: {"type": "session_id", "session_id": "s1"}\n\n'
            yield 'data: {"type": "plain", "data": "hello"}\n\n'
            yield 'data: {"type": "end", "data": {"status": "completed"}}\n\n'

        return stream()

    service.build_chat_stream = fake_build
    service._publish_run_to_unified_stream = (
        ChatService._publish_run_to_unified_stream.__get__(service)
    )
    connection = hub.connect("alice")
    stream = hub.stream(connection)
    assert _decode(await anext(stream))["type"] == "stream_ready"

    run = await ChatService.start_unified_chat_run(
        service,
        "alice",
        {"message_id": "run-1", "session_id": "s1", "message": "hi"},
    )
    assert run.run_id == "run-1"
    assert run.user_message_id == 7
    await asyncio.sleep(0.05)

    frames = [_decode(await anext(stream)) for _ in range(3)]
    assert [frame["type"] for frame in frames] == ["run_started", "plain", "end"]
    assert frames[0]["data"]["input_message"] == {
        "id": 7,
        "content": {"type": "user"},
    }
    assert [frame["seq"] for frame in frames] == [1, 2, 3]
    assert all(frame["message_id"] == "run-1" for frame in frames)
    assert frames[1]["session_id"] == "s1"

    hub.disconnect(connection)
    await stream.aclose()


@pytest.mark.asyncio
async def test_start_unified_chat_run_propagates_admission_error():
    service = SimpleNamespace(
        unified_stream_hub=UnifiedStreamHub(),
        chat_runs={},
        _unified_publish_tasks=set(),
    )

    async def rejected_build(username, post_data, *, message_id=None):
        raise ChatServiceError("rejected")

    service.build_chat_stream = rejected_build

    with pytest.raises(ChatServiceError, match="rejected"):
        await ChatService.start_unified_chat_run(
            service,
            "alice",
            {"message_id": "run-1", "session_id": "s1", "message": "hi"},
        )


@pytest.mark.asyncio
async def test_unified_send_route_returns_receipt_and_http_error():
    run = SimpleNamespace(session_id="s1", run_id="m1", user_message_id=42)
    service = SimpleNamespace(
        start_unified_chat_run=AsyncMock(return_value=run),
    )
    auth = SimpleNamespace(username="alice")

    response = await unified_send_chat(
        _JsonRequest(
            {
                "mode": "send",
                "session_id": "s1",
                "message_id": "m1",
                "content": [{"type": "plain", "text": "hello"}],
            }
        ),
        auth,
        service,
    )
    body = response
    assert body["data"]["run_id"] == "m1"
    assert body["data"]["input_message_id"] == 42
    sent = service.start_unified_chat_run.await_args.args[1]
    assert sent["message"] == [{"type": "plain", "text": "hello"}]
    assert sent["message_id"] == "m1"

    service = SimpleNamespace(
        start_unified_chat_run=AsyncMock(
            side_effect=ChatServiceError("rejected")
        ),
    )
    response = await unified_send_chat(
        _JsonRequest(
            {
                "mode": "send",
                "session_id": "s1",
                "message_id": "m1",
                "content": [],
            }
        ),
        auth,
        service,
    )
    assert response.status_code == 400
    assert json.loads(response.body)["message"] == "rejected"
