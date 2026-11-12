import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.dashboard.api.chat import unified_send_chat
from astrbot.dashboard.services import unified_stream
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
    assert not bob.control_queues
    assert not bob.data_queues

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


def test_plain_frames_merge_and_structured_frames_do_not():
    hub = UnifiedStreamHub()
    connection = hub.connect("alice")

    hub.publish("alice", "s1", {"type": "plain", "data": "a", "message_id": "m1"})
    hub.publish("alice", "s1", {"type": "plain", "data": "b", "message_id": "m1"})
    hub.publish(
        "alice",
        "s1",
        {"type": "plain", "data": "c", "chain_type": "reasoning", "message_id": "m1"},
    )
    hub.publish(
        "alice",
        "s1",
        {"type": "plain", "data": "d", "chain_type": "tool_call", "message_id": "m1"},
    )

    queue = connection.data_queues["s1"]
    assert [frame.payload["data"] for frame in queue] == ["ab", "c", "d"]
    assert [frame.seq for frame in queue] == [2, 3, 4]
    assert len(hub.rings["s1"].frames) == 4


@pytest.mark.asyncio
async def test_control_priority_keeps_terminal_after_data():
    hub = UnifiedStreamHub()
    connection = hub.connect("alice")
    stream = hub.stream(connection)
    assert _decode(await anext(stream))["type"] == "stream_ready"

    hub.publish(
        "alice",
        "s1",
        {"type": "plain", "data": "token", "message_id": "m1"},
    )
    hub.publish(
        "alice",
        "s1",
        {"type": "interactive_choice", "data": {}, "message_id": "m1"},
    )
    hub.publish("alice", "s1", {"type": "end", "data": None, "message_id": "m1"})

    frames = [_decode(await anext(stream)) for _ in range(3)]
    assert [frame["type"] for frame in frames] == [
        "interactive_choice",
        "plain",
        "end",
    ]
    assert [frame["seq"] for frame in frames] == [2, 1, 3]
    hub.disconnect(connection)
    await stream.aclose()


def test_data_overflow_drops_oldest_and_emits_one_resync(monkeypatch):
    monkeypatch.setattr(unified_stream, "_DATA_MAX_FRAMES", 2)
    monkeypatch.setattr(unified_stream, "_DATA_MAX_BYTES", 10_000_000)
    hub = UnifiedStreamHub()
    connection = hub.connect("alice")

    for index in range(4):
        hub.publish(
            "alice",
            "s1",
            {"type": "plain", "data": f"t{index}", "message_id": f"m{index}"},
        )

    queue = connection.data_queues["s1"]
    assert [frame.payload["message_id"] for frame in queue] == ["m2", "m3"]
    assert connection.dropped["s1"] == 2
    assert len(connection.control_queues["s1"]) == 1
    resync = connection.control_queues["s1"][0].payload
    assert resync["type"] == "resync_required"
    assert resync["from_seq"] == 1

    hub.publish(
        "alice",
        "s1",
        {"type": "plain", "data": "t4", "message_id": "m4"},
    )
    assert connection.dropped["s1"] == 3
    assert len(connection.control_queues["s1"]) == 1

    hub._next_batch(connection)
    assert not connection.data_queues.get("s1")

    for index in range(5, 8):
        hub.publish(
            "alice",
            "s1",
            {"type": "plain", "data": f"t{index}", "message_id": f"m{index}"},
        )
    assert connection.dropped["s1"] == 4
    assert len(connection.control_queues["s1"]) == 1
    assert connection.control_queues["s1"][0].payload["from_seq"] == 6


def test_ring_buffer_replay_and_snapshot_fallback(monkeypatch):
    hub = UnifiedStreamHub()
    for index in range(3):
        hub.publish(
            "alice",
            "s1",
            {"type": "plain", "data": f"t{index}", "message_id": f"m{index}"},
        )
    assert hub.rings["s1"].min_seq == 1

    replayed = hub.connect("alice", hub.epoch, {"s1": 1})
    assert "s1" in replayed.replayed_sessions
    assert [frame.seq for frame in replayed.data_queues["s1"]] == [2, 3]

    fallback = hub.connect("alice", hub.epoch, {"s1": 0})
    assert "s1" not in fallback.replayed_sessions
    assert not fallback.data_queues

    stale_epoch = hub.connect("alice", "stale", {"s1": 1})
    assert "s1" not in stale_epoch.replayed_sessions
    assert not stale_epoch.data_queues

    monkeypatch.setattr(unified_stream, "_RING_MAX_FRAMES", 3)
    hub.publish(
        "alice",
        "s1",
        {"type": "plain", "data": "t3", "message_id": "m3"},
    )
    assert hub.rings["s1"].min_seq == 2

    stale = hub.connect("alice", hub.epoch, {"s1": 1})
    assert "s1" not in stale.replayed_sessions

    covered = hub.connect("alice", hub.epoch, {"s1": 2})
    assert "s1" in covered.replayed_sessions
    assert [frame.seq for frame in covered.data_queues["s1"]] == [3, 4]


def test_publish_to_is_stored_for_replay():
    hub = UnifiedStreamHub()
    first = hub.connect("alice")
    hub.publish(
        "alice",
        "s1",
        {"type": "plain", "data": "before", "message_id": "m1"},
    )
    hub.publish_to(
        first,
        "s1",
        {"type": "run_snapshot", "data": {"content": {}}, "message_id": "m1"},
    )
    assert hub.rings["s1"].min_seq == 1

    replayed = hub.connect("alice", hub.epoch, {"s1": 1})
    assert "s1" in replayed.replayed_sessions
    assert [frame.seq for frame in replayed.data_queues["s1"]] == [2]
    assert replayed.data_queues["s1"][0].payload["type"] == "run_snapshot"


def test_ring_buffer_evicts_by_bytes_and_age(monkeypatch):
    monkeypatch.setattr(unified_stream, "_RING_MAX_BYTES", 250)
    hub = UnifiedStreamHub()
    for index in range(3):
        hub.publish(
            "alice",
            "s1",
            {"type": "plain", "data": "x" * 40, "message_id": f"m{index}"},
        )
    ring = hub.rings["s1"]
    assert ring.bytes <= 250
    assert ring.min_seq is not None and ring.min_seq > 1

    monkeypatch.setattr(unified_stream, "_RING_MAX_AGE_SECONDS", 0.01)
    aged = UnifiedStreamHub()
    aged.publish(
        "alice",
        "s2",
        {"type": "plain", "data": "old", "message_id": "m1"},
    )
    time.sleep(0.02)
    aged.publish(
        "alice",
        "s2",
        {"type": "plain", "data": "new", "message_id": "m2"},
    )
    assert aged.rings["s2"].min_seq == 2


@pytest.mark.asyncio
async def test_heartbeat_when_idle(monkeypatch):
    monkeypatch.setattr(unified_stream, "_HEARTBEAT_SECONDS", 0.01)
    hub = UnifiedStreamHub()
    connection = hub.connect("alice")
    stream = hub.stream(connection)
    assert _decode(await anext(stream))["type"] == "stream_ready"
    assert (
        await asyncio.wait_for(anext(stream), timeout=1) == unified_stream.SSE_HEARTBEAT
    )
    hub.disconnect(connection)
    await stream.aclose()


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
        ChatService._publish_unified_run_started(service, run)

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
async def test_unified_initial_frames_replays_each_running_run_for_one_account():
    service = ChatService.__new__(ChatService)
    service.chat_runs = {}
    for index in range(6):
        run = ChatRunState(
            run_id=f"run-{index}",
            username="alice",
            session_id=f"s{index}",
            llm_checkpoint_id=f"ckpt-{index}",
            platform_history_id="webchat",
            back_queue=asyncio.Queue(),
            unified_input_message={"id": index, "content": {"type": "user"}},
        )
        service.chat_runs[run.run_id] = run
    finished = ChatRunState(
        run_id="finished",
        username="alice",
        session_id="s-finished",
        llm_checkpoint_id="ckpt-finished",
        platform_history_id="webchat",
        back_queue=asyncio.Queue(),
    )
    finished.status = "finished"
    service.chat_runs[finished.run_id] = finished
    other = ChatRunState(
        run_id="bob-run",
        username="bob",
        session_id="s-bob",
        llm_checkpoint_id="ckpt-bob",
        platform_history_id="webchat",
        back_queue=asyncio.Queue(),
    )
    service.chat_runs[other.run_id] = other

    frames = service.unified_initial_frames("alice")
    assert len(frames) == 12
    assert [session_id for session_id, _ in frames] == [
        f"s{index}" for index in range(6) for _ in range(2)
    ]
    assert [payload["type"] for _, payload in frames].count("run_started") == 6
    assert [payload["type"] for _, payload in frames].count("run_snapshot") == 6
    snapshots = [payload for _, payload in frames if payload["type"] == "run_snapshot"]
    assert [snapshot["data"]["input_message"]["id"] for snapshot in snapshots] == list(
        range(6)
    )
    started = [payload for _, payload in frames if payload["type"] == "run_started"]
    assert [payload["message_id"] for payload in started] == [
        f"run-{index}" for index in range(6)
    ]


@pytest.mark.asyncio
async def test_six_fake_runs_share_one_unified_connection():
    hub = UnifiedStreamHub()
    connection = hub.connect("alice")
    runs = [
        ChatRunState(
            run_id=f"run-{index}",
            username="alice",
            session_id=f"s{index}",
            llm_checkpoint_id=f"ckpt-{index}",
            platform_history_id="webchat",
            back_queue=asyncio.Queue(),
        )
        for index in range(6)
    ]

    async def publish_run(run: ChatRunState) -> None:
        await asyncio.sleep(0)
        hub.publish(
            "alice",
            run.session_id,
            {
                "type": "run_started",
                "data": {"run_id": run.run_id},
                "message_id": run.run_id,
            },
        )
        hub.publish(
            "alice",
            run.session_id,
            {"type": "plain", "data": "token", "message_id": run.run_id},
        )
        hub.publish(
            "alice",
            run.session_id,
            {"type": "end", "data": None, "message_id": run.run_id},
        )

    stream = hub.stream(connection)
    ready = _decode(await anext(stream))
    assert ready["type"] == "stream_ready"

    await asyncio.gather(*(publish_run(run) for run in runs))
    assert len(hub.connections) == 1
    frames = [_decode(await anext(stream)) for _ in range(18)]
    assert {frame["session_id"] for frame in frames} == {
        f"s{index}" for index in range(6)
    }
    for index in range(6):
        session_frames = [
            frame for frame in frames if frame["session_id"] == f"s{index}"
        ]
        assert [frame["seq"] for frame in session_frames] == [1, 2, 3]
        assert [frame["type"] for frame in session_frames] == [
            "run_started",
            "plain",
            "end",
        ]
        assert all(frame["message_id"] == f"run-{index}" for frame in session_frames)
    hub.disconnect(connection)
    await stream.aclose()


@pytest.mark.asyncio
async def test_synthetic_run_registration_broadcasts_run_started():
    hub = UnifiedStreamHub()
    connection = hub.connect("alice")
    service = ChatService.__new__(ChatService)
    service.unified_stream_hub = hub
    service.chat_runs = {}
    service.chat_runs_by_session = {}
    service._consume_chat_run = AsyncMock()

    stream = hub.stream(connection)
    ready = _decode(await anext(stream))
    assert ready["type"] == "stream_ready"

    await ChatService.register_synthetic_chat_run(
        service,
        "s-synth",
        "synth-1",
        "alice",
    )
    frame = _decode(await anext(stream))
    assert frame["type"] == "run_started"
    assert frame["session_id"] == "s-synth"
    assert frame["message_id"] == "synth-1"
    assert frame["seq"] == 1
    assert frame["data"]["input_message"] is None
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
async def test_unified_session_state_packages_goal_and_shell():
    service = ChatService.__new__(ChatService)

    async def fake_goal(username, session_id):
        return {"goal": {"text": "goal"}}

    async def fake_shell(username, session_id):
        return {"sessions": [{"id": "shell"}]}

    service.get_session_goal = fake_goal
    service.get_session_shell_sessions = fake_shell
    frame = await service.unified_session_state("alice", "s1")
    assert frame == {
        "type": "session_state",
        "data": {"goal": {"text": "goal"}, "sessions": [{"id": "shell"}]},
    }


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
        start_unified_chat_run=AsyncMock(side_effect=ChatServiceError("rejected")),
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
