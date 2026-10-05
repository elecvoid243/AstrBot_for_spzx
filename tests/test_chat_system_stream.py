"""Tests for ChatService.build_system_stream (orphan/system event consumer)."""

# Author: elecvoid243
# Date: 2026-07-25

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
    webchat_queue_mgr,
)
from astrbot.dashboard.services.chat_service import ChatService


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
    service._branch_relations = None
    service.save_bot_message = AsyncMock()
    return service


async def _pump(stream, sink: list) -> None:
    async for chunk in stream:
        for block in chunk.strip().split("\n\n"):
            if block.startswith("data: "):
                sink.append(json.loads(block[6:]))


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("condition not met within timeout")


class TestBuildSystemStream:
    @pytest.mark.asyncio
    async def test_orphan_event_streamed_and_saved_on_complete(self):
        service = _make_service()
        session_id = f"conv-{id(self)}-a"
        stream = await service.build_system_stream("tester", session_id)
        sink: list = []
        pump = asyncio.create_task(_pump(stream, sink))
        try:
            await _wait_until(
                lambda: webchat_queue_mgr.has_system_subscribers(session_id)
            )
            await webchat_queue_mgr.put_system_event(
                session_id,
                {
                    "type": "plain",
                    "data": "goal turn output",
                    "streaming": False,
                    "message_id": "orphan-1",
                },
            )
            await webchat_queue_mgr.put_system_event(
                session_id,
                {
                    "type": "complete",
                    "data": "goal turn output",
                    "streaming": False,
                    "message_id": "orphan-1",
                },
            )
            await _wait_until(lambda: service.save_bot_message.await_count == 1)
            assert [e["type"] for e in sink] == ["plain", "complete"]
            args = service.save_bot_message.await_args.args
            assert args[0] == session_id
            assert args[1][-1]["text"] == "goal turn output"
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await stream.aclose()

    @pytest.mark.asyncio
    async def test_goal_state_changed_passes_without_message_id(self):
        """goal_state_changed carries no message_id by design (it is session-
        scoped, not turn-scoped); the stream must yield it verbatim and never
        route it into the message accumulator/persistence path."""
        service = _make_service()
        session_id = f"conv-{id(self)}-goal"
        stream = await service.build_system_stream("tester", session_id)
        sink: list = []
        pump = asyncio.create_task(_pump(stream, sink))
        try:
            await _wait_until(
                lambda: webchat_queue_mgr.has_system_subscribers(session_id)
            )
            await webchat_queue_mgr.put_system_event(
                session_id,
                {
                    "type": "goal_state_changed",
                    "data": {"goal": {"goal": "g", "status": "paused"}},
                },
            )
            await _wait_until(lambda: len(sink) == 1)
            assert sink[0]["type"] == "goal_state_changed"
            assert sink[0]["data"]["goal"]["status"] == "paused"
            service.save_bot_message.assert_not_awaited()
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await stream.aclose()

    @pytest.mark.asyncio
    async def test_shell_sessions_changed_passes_without_message_id(self):
        """shell_sessions_changed is session-scoped like goal_state_changed
        (no message_id); it must pass the message_id gate verbatim and stay
        out of the accumulator/persistence path."""
        service = _make_service()
        session_id = f"conv-{id(self)}-shell"
        stream = await service.build_system_stream("tester", session_id)
        sink: list = []
        pump = asyncio.create_task(_pump(stream, sink))
        try:
            await _wait_until(
                lambda: webchat_queue_mgr.has_system_subscribers(session_id)
            )
            await webchat_queue_mgr.put_system_event(
                session_id,
                {
                    "type": "shell_sessions_changed",
                    "data": {"sessions": [{"session_id": "sh_1", "status": "running"}]},
                },
            )
            await _wait_until(lambda: len(sink) == 1)
            assert sink[0]["type"] == "shell_sessions_changed"
            assert sink[0]["data"]["sessions"][0]["session_id"] == "sh_1"
            service.save_bot_message.assert_not_awaited()
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await stream.aclose()

    @pytest.mark.asyncio
    async def test_active_run_payloads_are_skipped(self):
        service = _make_service()
        session_id = f"conv-{id(self)}-b"
        service.chat_runs["user-run-1"] = MagicMock()  # primary run in flight
        stream = await service.build_system_stream("tester", session_id)
        sink: list = []
        pump = asyncio.create_task(_pump(stream, sink))
        try:
            await _wait_until(
                lambda: webchat_queue_mgr.has_system_subscribers(session_id)
            )
            await webchat_queue_mgr.put_system_event(
                session_id,
                {
                    "type": "plain",
                    "data": "user message echo",
                    "streaming": True,
                    "message_id": "user-run-1",
                },
            )
            await webchat_queue_mgr.put_system_event(
                session_id,
                {
                    "type": "plain",
                    "data": "orphan output",
                    "streaming": False,
                    "message_id": "orphan-2",
                },
            )
            await _wait_until(lambda: len(sink) == 1)
            assert sink[0]["message_id"] == "orphan-2"
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await stream.aclose()
        # The orphan plain payload was accumulated but no complete/end arrived,
        # so aclose triggers the finally-flush and persists it.
        service.save_bot_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_flushes_partial_turn(self):
        service = _make_service()
        session_id = f"conv-{id(self)}-c"
        stream = await service.build_system_stream("tester", session_id)
        sink: list = []
        pump = asyncio.create_task(_pump(stream, sink))
        try:
            await _wait_until(
                lambda: webchat_queue_mgr.has_system_subscribers(session_id)
            )
            await webchat_queue_mgr.put_system_event(
                session_id,
                {
                    "type": "plain",
                    "data": "partial",
                    "streaming": True,
                    "message_id": "orphan-3",
                },
            )
            await _wait_until(lambda: len(sink) == 1)
            assert sink[0]["data"] == "partial"
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await stream.aclose()
        service.save_bot_message.assert_awaited_once()


class TestSystemStreamConfigGate:
    def test_enabled_by_default(self):
        service = _make_service()
        service.core_lifecycle.astrbot_config = {"dashboard": {}}
        assert service.system_stream_enabled() is True

    def test_disabled_via_config(self):
        service = _make_service()
        service.core_lifecycle.astrbot_config = {
            "dashboard": {"system_stream_enabled": False}
        }
        assert service.system_stream_enabled() is False


class TestSendToSystemStreamIntegration:
    """Cross-layer: WebChatMessageEvent._send mirror → build_system_stream."""

    @pytest.mark.asyncio
    async def test_event_send_reaches_system_stream_and_persists(self):
        from astrbot.core.platform.sources.webchat.webchat_event import (
            WebChatMessageEvent,
        )

        service = _make_service()
        session_id = f"conv-{id(self)}-int"
        stream = await service.build_system_stream("tester", session_id)
        sink: list = []
        pump = asyncio.create_task(_pump(stream, sink))
        try:
            await _wait_until(
                lambda: webchat_queue_mgr.has_system_subscribers(session_id)
            )
            webchat_queue_mgr.get_or_create_back_queue("req-int-1", session_id)
            await WebChatMessageEvent._send(
                "req-int-1",
                MessageChain(chain=[Plain("orphan turn")]),
                f"webchat!user!{session_id}",
                emit_complete=True,
            )
            await _wait_until(lambda: service.save_bot_message.await_count == 1)
            assert [e["type"] for e in sink] == ["plain", "complete"]
            assert sink[0]["data"] == "orphan turn"
            assert sink[0]["message_id"] == "req-int-1"
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await stream.aclose()
            webchat_queue_mgr.remove_back_queue("req-int-1")

    @pytest.mark.asyncio
    async def test_send_by_session_style_call_does_not_leak(self):
        from astrbot.core.platform.sources.webchat.webchat_event import (
            WebChatMessageEvent,
        )

        service = _make_service()
        session_id = f"conv-{id(self)}-int2"
        stream = await service.build_system_stream("tester", session_id)
        sink: list = []
        pump = asyncio.create_task(_pump(stream, sink))
        try:
            await _wait_until(
                lambda: webchat_queue_mgr.has_system_subscribers(session_id)
            )
            webchat_queue_mgr.get_or_create_back_queue("req-int-2", session_id)
            await WebChatMessageEvent._send(
                "req-int-2",
                MessageChain(chain=[Plain("proactive")]),
                session_id,
                emit_complete=True,
                mirror_system=False,
            )
            await asyncio.sleep(0.1)
            assert sink == []
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await stream.aclose()
            webchat_queue_mgr.remove_back_queue("req-int-2")


# Author: elecvoid243
# Date: 2026-07-25
# Regression tests for orphan goal-loop turn `agent_stats`
# persistence. Covers the second half of the system-event-stream fix:
# the backend flush path now propagates `pending_agent_stats` from
# `BotMessageAccumulator` to `save_bot_message`. The frontend leaf
# (`systemStream.ts`) was fixed earlier today; these tests guard the
# backend contract it depends on.
#
# Normal turn flow is intentionally not exercised here. The primary
# `_consume_chat_run` path decouples agent_stats from the accumulator
# (stores on `ChatRunState` directly) and never calls
# `add_plain(chain_type="agent_stats")` — so changes to `add_plain`
# and `flush()` cannot affect Normal turn. See
# `tests/unit/test_chat_service_sanitize_history.py` for Normal turn
# regression baselines.
class TestOrphanTurnAgentStatsPersistence:
    """BotMessageAccumulator.add_plain(chain_type='agent_stats')."""

    def _stats_payload(
        self,
        *,
        token_usage: dict | None = None,
        agent_duration: float | None = 12.3,
        other: dict | None = None,
    ) -> str:
        payload: dict = {}
        if token_usage is not None:
            payload["token_usage"] = token_usage
        if agent_duration is not None:
            payload["agent_duration"] = agent_duration
        if other:
            payload.update(other)
        return json.dumps(payload, ensure_ascii=False)

    def test_agent_stats_chain_type_parses_and_stores(self):
        from astrbot.dashboard.services.chat_service import BotMessageAccumulator

        accumulator = BotMessageAccumulator()
        stats_json = self._stats_payload(
            token_usage={"input_other": 123, "input_cached": 456, "output": 78},
            agent_duration=9.6,
        )
        accumulator.add_plain(stats_json, chain_type="agent_stats", streaming=False)

        # `pending_agent_stats` carries the parsed dict; default
        # initialised empty.
        assert accumulator.pending_agent_stats == {
            "token_usage": {
                "input_other": 123,
                "input_cached": 456,
                "output": 78,
            },
            "agent_duration": 9.6,
        }

        # The stats blob MUST NOT leak into message_parts as plain
        # text — that's the original bug this fix addresses.
        parts = accumulator.build_message_parts()
        assert parts == [], f"expected no parts, got {parts!r}"

    def test_agent_stats_does_not_pollute_other_chain_types(self):
        """Adding `agent_stats` then a normal `plain` chunk keeps the
        plain chunk isolated as a regular plain part and the stats
        blob fully inside `pending_agent_stats`.

        This is the regression safety net for the **Normal turn
        flow**: any added branch must be purely additive and never
        bleed into the default `pending_text` slot.
        """
        from astrbot.dashboard.services.chat_service import BotMessageAccumulator

        accumulator = BotMessageAccumulator()
        accumulator.add_plain(
            self._stats_payload(token_usage={"input_other": 5, "output": 7}),
            chain_type="agent_stats",
            streaming=False,
        )
        accumulator.add_plain("hello ", chain_type=None, streaming=True)
        accumulator.add_plain("world", chain_type=None, streaming=True)

        # Stats blob stays in its own slot.
        assert accumulator.pending_agent_stats == {
            "token_usage": {"input_other": 5, "output": 7},
            "agent_duration": 12.3,
        }
        # Plain text chunk resolves through the regular append path;
        # the stats JSON never gets concatenated into `pending_text`.
        parts = accumulator.build_message_parts()
        assert len(parts) == 1
        assert parts[0]["type"] == "plain"
        assert parts[0]["text"] == "hello world"
        assert "token_usage" not in parts[0]["text"]

    def test_agent_stats_malformed_json_degrades_to_empty(self):
        from astrbot.dashboard.services.chat_service import BotMessageAccumulator

        accumulator = BotMessageAccumulator()
        accumulator.add_plain(
            "{this is not valid json", chain_type="agent_stats", streaming=False
        )
        # Better to drop than to persist a JSON literal as plain text.
        assert accumulator.pending_agent_stats == {}

    def test_has_content_true_when_only_agent_stats(self):
        """Orphan turns that produce only an `agent_stats` payload
        (e.g., goal-loop with no user-visible output) still need to
        flush. `has_content` must therefore include
        `pending_agent_stats`.
        """
        from astrbot.dashboard.services.chat_service import BotMessageAccumulator

        accumulator = BotMessageAccumulator()
        accumulator.add_plain(
            self._stats_payload(token_usage={"output": 50}),
            chain_type="agent_stats",
            streaming=False,
        )
        assert accumulator.has_content() is True

    def test_has_content_unchanged_for_normal_turn_path(self):
        """Guard against accidentally making `has_content` return
        True when Normal turn would have returned False. Normal
        turn never calls `add_plain(chain_type='agent_stats')`, so
        `pending_agent_stats` stays empty; the OR-chain must
        resolve identically to the pre-fix expression
        `bool(parts or pending_text or pending_tool_calls)`.
        """
        from astrbot.dashboard.services.chat_service import BotMessageAccumulator

        empty = BotMessageAccumulator()
        assert empty.has_content() is False

        with_plain = BotMessageAccumulator()
        with_plain.add_plain("hi", chain_type=None, streaming=False)
        assert with_plain.has_content() is True

        with_tool = BotMessageAccumulator()
        with_tool.add_plain(
            json.dumps(
                {
                    "id": "call_x",
                    "name": "read_file",
                    "arguments": "{}",
                }
            ),
            chain_type="tool_call",
            streaming=False,
        )
        assert with_tool.has_content() is True


class TestOrphanTurnSystemStreamStatsE2E:
    """End-to-end: `agent_stats` payload through `build_system_stream`
    reaches `save_bot_message` as a structured dict (not a JSON
    literal baked into a plain part).
    """

    @pytest.mark.asyncio
    async def test_orphan_turn_with_agent_stats_persists_structured(self):
        service = _make_service()
        session_id = f"conv-{id(self)}-stats-e2e"
        stream = await service.build_system_stream("tester", session_id)
        sink: list = []
        pump = asyncio.create_task(_pump(stream, sink))
        try:
            await _wait_until(
                lambda: webchat_queue_mgr.has_system_subscribers(session_id)
            )
            stats_payload = {
                "token_usage": {
                    "input_other": 21658,
                    "input_cached": 42880,
                    "output": 702,
                },
                "agent_ttft": 9.6,
            }
            await webchat_queue_mgr.put_system_event(
                session_id,
                {
                    "type": "plain",
                    "chain_type": "agent_stats",
                    "data": json.dumps(stats_payload, ensure_ascii=False),
                    "streaming": False,
                    "message_id": "orphan-stats-1",
                },
            )
            await webchat_queue_mgr.put_system_event(
                session_id,
                {
                    "type": "plain",
                    "data": "goal-loop final text",
                    "streaming": False,
                    "message_id": "orphan-stats-1",
                },
            )
            await webchat_queue_mgr.put_system_event(
                session_id,
                {
                    "type": "complete",
                    "streaming": False,
                    "message_id": "orphan-stats-1",
                },
            )
            await _wait_until(lambda: service.save_bot_message.await_count == 1)
            args = service.save_bot_message.await_args.args
            # save_bot_message(webchat_conv_id, message_parts,
            #                  agent_stats, refs, llm_checkpoint_id,
            #                  platform_history_id).
            persisted_stats = args[2]
            persisted_parts = args[1]
            assert persisted_stats == stats_payload
            # The plain text part survives alongside the stats.
            assert any(
                p.get("type") == "plain" and p.get("text") == "goal-loop final text"
                for p in persisted_parts
            )
            # And critically: no part carries the raw JSON blob as text.
            assert not any(
                "token_usage" in (p.get("text") or "")
                for p in persisted_parts
                if p.get("type") == "plain"
            )
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await stream.aclose()

    @pytest.mark.asyncio
    async def test_orphan_stats_only_turn_persists_without_plain_text(self):
        """Orphan turn with ONLY `agent_stats` and no plain output
        (a typical goal-loop synthetic turn) still flushes.
        """
        service = _make_service()
        session_id = f"conv-{id(self)}-stats-only"
        stream = await service.build_system_stream("tester", session_id)
        sink: list = []
        pump = asyncio.create_task(_pump(stream, sink))
        try:
            await _wait_until(
                lambda: webchat_queue_mgr.has_system_subscribers(session_id)
            )
            await webchat_queue_mgr.put_system_event(
                session_id,
                {
                    "type": "plain",
                    "chain_type": "agent_stats",
                    "data": json.dumps(
                        {"token_usage": {"output": 100}}, ensure_ascii=False
                    ),
                    "streaming": False,
                    "message_id": "orphan-stats-only",
                },
            )
            await webchat_queue_mgr.put_system_event(
                session_id,
                {
                    "type": "end",
                    "streaming": False,
                    "message_id": "orphan-stats-only",
                },
            )
            await _wait_until(lambda: service.save_bot_message.await_count == 1)
            args = service.save_bot_message.await_args.args
            assert args[2] == {"token_usage": {"output": 100}}
            # message_parts may be empty (no plain text emitted) — the
            # important invariant is that the stats slot reached DB.
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await stream.aclose()


class TestRegisterSyntheticChatRun:
    @pytest.mark.asyncio
    async def test_register_creates_run_and_announces_run_started(self):
        """register_synthetic_chat_run puts the injected turn on the primary
        pipeline: back_queue + ChatRunState exist, active_runs exposes it
        with the injected checkpoint, and run_started is announced to the
        conversation's system mirror so an open page attaches to the run
        stream."""
        service = _make_service()
        session_id = f"conv-{id(service)}-reg"
        await service.register_synthetic_chat_run(
            session_id, "synth-run-1", "alice", "checkpoint-1"
        )
        try:
            run = service.chat_runs["synth-run-1"]
            assert run.session_id == session_id
            assert run.llm_checkpoint_id == "checkpoint-1"
            assert service.chat_runs_by_session[session_id] == {"synth-run-1"}
            # Active-run recovery covers the turn (switch-back / refresh).
            snapshots = service.get_active_chat_runs("alice", session_id)
            assert snapshots[0]["run_id"] == "synth-run-1"
            assert snapshots[0]["llm_checkpoint_id"] == "checkpoint-1"
        finally:
            await webchat_queue_mgr.put_back_queue(
                "synth-run-1",
                {
                    "type": "end",
                    "data": "",
                    "streaming": False,
                    "message_id": "synth-run-1",
                },
            )
            await asyncio.wait_for(run.task, timeout=2)

        # Consumed: the run is cleaned up like any primary run.
        assert "synth-run-1" not in service.chat_runs
        assert session_id not in service.chat_runs_by_session

    @pytest.mark.asyncio
    async def test_run_owned_payloads_are_hidden_from_system_stream(self):
        """Registered-run payloads must not leak into the system stream —
        the run stream owns their rendering — while the run_started
        announcement passes through so pages can attach."""
        service = _make_service()
        session_id = f"conv-{id(service)}-hide"
        await service.register_synthetic_chat_run(
            session_id, "synth-run-2", "alice", "checkpoint-2"
        )
        stream = await service.build_system_stream("alice", session_id)
        sink: list = []
        pump = asyncio.create_task(_pump(stream, sink))
        try:
            await _wait_until(
                lambda: webchat_queue_mgr.has_system_subscribers(session_id)
            )
            await webchat_queue_mgr.put_system_event(
                session_id,
                {
                    "type": "plain",
                    "data": "owned chunk",
                    "streaming": True,
                    "message_id": "synth-run-2",
                },
            )
            # The adapter's run_started mirror (no synthetic flag) stays
            # filtered for run-owned message ids — the initiating page
            # renders the turn via its own request-scoped stream.
            await webchat_queue_mgr.put_system_event(
                session_id,
                {
                    "type": "run_started",
                    "data": {"run_id": "synth-run-2"},
                    "streaming": False,
                    "message_id": "synth-run-2",
                },
            )
            # ...while the synthetic registration announcement passes so
            # other pages can attach to the run stream.
            await webchat_queue_mgr.put_system_event(
                session_id,
                {
                    "type": "run_started",
                    "data": {"run_id": "synth-run-2"},
                    "streaming": False,
                    "message_id": "synth-run-2",
                    "synthetic": True,
                },
            )
            await _wait_until(lambda: any(p.get("type") == "run_started" for p in sink))
            passed = [p for p in sink if p.get("type") == "run_started"]
            assert all(p.get("synthetic") for p in passed)
            assert not any(p.get("type") == "plain" for p in sink)
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await stream.aclose()
            await webchat_queue_mgr.put_back_queue(
                "synth-run-2",
                {
                    "type": "end",
                    "data": "",
                    "streaming": False,
                    "message_id": "synth-run-2",
                },
            )
            await asyncio.wait_for(service.chat_runs.pop("synth-run-2").task, timeout=2)


class TestSyntheticRunResumeStream:
    @pytest.mark.asyncio
    async def test_resume_stream_delivers_snapshot_chunks_and_end(self):
        """A synthetic run registered via register_synthetic_chat_run must be
        fully served by the run stream: snapshot on attach, live chunks while
        streaming, and a terminal end — exactly like a user-initiated run."""
        service = _make_service()
        session_id = f"conv-{id(service)}-resume"
        await service.register_synthetic_chat_run(
            session_id, "synth-run-3", "alice", "checkpoint-3"
        )
        run = service.chat_runs["synth-run-3"]

        stream = await service.build_chat_run_stream("alice", "synth-run-3")
        sink: list = []
        pump = asyncio.create_task(_pump(stream, sink))

        async def _feed():
            await webchat_queue_mgr.put_back_queue(
                "synth-run-3",
                {
                    "type": "plain",
                    "data": "streamed reply",
                    "streaming": True,
                    "message_id": "synth-run-3",
                },
            )
            await webchat_queue_mgr.put_back_queue(
                "synth-run-3",
                {
                    "type": "end",
                    "data": "",
                    "streaming": False,
                    "message_id": "synth-run-3",
                },
            )

        try:
            # Wait for the snapshot so we know the subscriber is attached
            # before feeding chunks.
            await _wait_until(
                lambda: any(p.get("type") == "run_snapshot" for p in sink)
            )
            await _feed()
            await asyncio.wait_for(run.task, timeout=2)
            await _wait_until(lambda: any(p.get("type") == "end" for p in sink))

            types = [p.get("type") for p in sink]
            assert "run_snapshot" in types
            assert "plain" in types
            assert "end" in types
            plain = next(p for p in sink if p.get("type") == "plain")
            assert plain.get("data") == "streamed reply"
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await stream.aclose()


class TestPublishChatRun:
    @pytest.mark.asyncio
    async def test_slow_subscriber_keeps_receiving_after_overflow(self):
        """Regression: a slow subscriber used to be EVICTED on queue overflow,
        killing its SSE connection — the bubble froze on 思考中 with no text
        until a manual refresh. Overflow now drops the oldest buffered events
        and keeps the subscriber attached, always holding the newest event."""
        import asyncio

        from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
            webchat_queue_mgr,
        )

        service = _make_service()
        session_id = f"conv-{id(service)}-slow"
        await service.register_synthetic_chat_run(
            session_id, "slow-run-1", "alice", "checkpoint-slow"
        )
        run = service.chat_runs["slow-run-1"]
        try:
            slow = asyncio.Queue(maxsize=1)
            run.subscribers.add(slow)
            for text in ("one", "two", "three"):
                ChatService._publish_chat_run(
                    run,
                    {
                        "type": "plain",
                        "data": text,
                        "streaming": True,
                        "message_id": "slow-run-1",
                    },
                )
            # The subscriber was never evicted and holds the newest event.
            assert slow in run.subscribers
            assert slow.qsize() == 1
            _, payload = slow.get_nowait()
            assert payload["data"] == "three"
            # The backlog drain does not touch the manager's back_queue.
            assert webchat_queue_mgr.back_queues["slow-run-1"].qsize() == 0
        finally:
            await webchat_queue_mgr.put_back_queue(
                "slow-run-1",
                {
                    "type": "end",
                    "data": "",
                    "streaming": False,
                    "message_id": "slow-run-1",
                },
            )
            await asyncio.wait_for(run.task, timeout=2)


class TestRunStreamOwnershipFallback:
    @pytest.mark.asyncio
    async def test_owner_mismatch_falls_back_to_session_creator(self):
        """Identity drift between auth dependencies: run.username is recorded
        from require_dashboard_user (dashboard-state username first) while
        the resume endpoint authenticates via require_chat_scope (JWT
        username). When the two differ, the attach must still succeed if the
        run's session belongs to the requester (get_session's ownership
        convention)."""
        from fastapi import FastAPI  # noqa: F401  (import guard parity)

        service = _make_service()
        session_id = f"conv-{id(service)}-own"
        await service.register_synthetic_chat_run(
            session_id, "own-run-1", "Alice", "cp-own"
        )
        # The chat-scope JWT resolves to "alice" while the run recorded the
        # dashboard-state representation "Alice" — same person, drifted form.
        session = MagicMock(creator="alice")
        service.db.get_platform_session_by_id = AsyncMock(return_value=session)

        stream = await service.build_chat_run_stream("alice", "own-run-1")
        sink: list = []
        pump = asyncio.create_task(_pump(stream, sink))
        try:
            await _wait_until(
                lambda: any(p.get("type") == "run_snapshot" for p in sink)
            )
            await webchat_queue_mgr.put_back_queue(
                "own-run-1",
                {
                    "type": "end",
                    "data": "",
                    "streaming": False,
                    "message_id": "own-run-1",
                },
            )
            await _wait_until(lambda: any(p.get("type") == "end" for p in sink))
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await stream.aclose()

    @pytest.mark.asyncio
    async def test_owner_mismatch_rejected_when_session_owned_by_other(self):
        service = _make_service()
        session_id = f"conv-{id(service)}-deny"
        await service.register_synthetic_chat_run(
            session_id, "deny-run-1", "Alice", "cp-deny"
        )
        session = MagicMock(creator="carol")
        service.db.get_platform_session_by_id = AsyncMock(return_value=session)

        from astrbot.dashboard.services.chat_service import ChatServiceError

        with pytest.raises(ChatServiceError, match="Permission denied"):
            await service.build_chat_run_stream("bob", "deny-run-1")
        # Cleanup the registered run so its consumer task ends quietly.
        await webchat_queue_mgr.put_back_queue(
            "deny-run-1",
            {
                "type": "end",
                "data": "",
                "streaming": False,
                "message_id": "deny-run-1",
            },
        )
        await asyncio.wait_for(service.chat_runs.pop("deny-run-1").task, timeout=2)
