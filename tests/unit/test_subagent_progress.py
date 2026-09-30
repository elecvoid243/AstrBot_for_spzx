"""Tests for subagent_run part accumulation and consume routing."""

# Author: elecvoid243
# Date: 2026-07-26
# Plan: docs/superpowers/plans/2026-07-26-subagent-chatui-progress.md (Task 4)

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from astrbot.dashboard.services.chat_service import (
    BotMessageAccumulator,
    ChatRunState,
    ChatService,
)


def test_sink_input_full_is_not_truncated():
    from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor

    event = SimpleNamespace(
        message_obj=SimpleNamespace(message_id="msg-1"),
        get_platform_name=lambda: "webchat",
    )
    long_input = "x" * 5000
    sink = FunctionToolExecutor._maybe_create_subagent_sink(
        event, {}, "agent", long_input
    )
    assert sink is not None
    assert sink._input_full == long_input
    assert sink._input_preview == long_input[:200]


def _event(run_id, kind, payload, agent="researcher"):
    return {
        "type": "subagent_event",
        "message_id": "run-1",
        "streaming": True,
        "data": {
            "subagent_run_id": run_id,
            "agent_name": agent,
            "kind": kind,
            "payload": payload,
            "ts": 1.0,
        },
    }


class TestAccumulator:
    def test_full_lifecycle_builds_single_part(self):
        acc = BotMessageAccumulator()
        acc.add_subagent_event(
            _event("sa_1", "started", {"input_preview": "task"})["data"]
        )
        acc.add_subagent_event(
            _event("sa_1", "text_delta", {"text": "Let me "})["data"]
        )
        acc.add_subagent_event(
            _event("sa_1", "reasoning_delta", {"text": "hmm"})["data"]
        )
        acc.add_subagent_event(
            _event("sa_1", "tool_call", {"id": "c1", "name": "web_search", "args": {}})[
                "data"
            ]
        )
        acc.add_subagent_event(
            _event("sa_1", "tool_call_result", {"id": "c1", "result": "found"})["data"]
        )
        acc.add_subagent_event(_event("sa_1", "text_delta", {"text": "Final "})["data"])
        acc.add_subagent_event(_event("sa_1", "text_delta", {"text": "answer"})["data"])
        acc.add_subagent_event(
            _event(
                "sa_1",
                "completed",
                {"result_text": "Final answer", "execution_time": 3.0},
            )["data"]
        )
        parts = acc.build_message_parts()
        (part,) = [p for p in parts if p["type"] == "subagent_run"]
        assert part["subagent_run_id"] == "sa_1"
        assert part["agent_name"] == "researcher"
        assert part["status"] == "completed"
        # Result shows only the final LLM output, not intermediate narration.
        assert part["text"] == "Final answer"
        assert part["reasoning"] == "hmm"
        assert part["tool_calls"] == [
            {
                "id": "c1",
                "name": "web_search",
                "args": {},
                "result": "found",
                "ts": 1.0,
                "finished_ts": 1.0,
            }
        ]
        # Intermediate text stays in the chronological activity log; the
        # final turn's streamed text is dropped (it lives in part["text"]).
        assert part["activity"] == [
            {"kind": "text", "text": "Let me "},
            {"kind": "think", "text": "hmm"},
            {
                "kind": "tool_call",
                "call": {
                    "id": "c1",
                    "name": "web_search",
                    "args": {},
                    "result": "found",
                    "ts": 1.0,
                    "finished_ts": 1.0,
                },
            },
        ]
        assert part["execution_time"] == 3.0

    def test_tool_calls_carry_event_timestamps(self):
        acc = BotMessageAccumulator()
        acc.add_subagent_event(
            {
                **_event("sa_1", "tool_call", {"id": "c1", "name": "t1"})["data"],
                "ts": 10.0,
            }
        )
        acc.add_subagent_event(
            {
                **_event("sa_1", "tool_call_result", {"id": "c1", "result": "ok"})[
                    "data"
                ],
                "ts": 12.5,
            }
        )
        (part,) = acc.build_message_parts()
        # ToolCallCard renders elapsed time from ts->finished_ts; without
        # these stamps every call looks permanently "running".
        assert part["tool_calls"][0]["ts"] == 10.0
        assert part["tool_calls"][0]["finished_ts"] == 12.5

    def test_part_position_follows_event_order(self):
        acc = BotMessageAccumulator()
        acc.add_plain("before ", chain_type=None, streaming=True)
        acc.add_subagent_event(_event("sa_1", "started", {"input_preview": ""})["data"])
        acc.add_plain("after", chain_type=None, streaming=True)
        types = [p["type"] for p in acc.build_message_parts()]
        assert types == ["plain", "subagent_run", "plain"]

    def test_failure_status(self):
        acc = BotMessageAccumulator()
        acc.add_subagent_event(_event("sa_1", "started", {"input_preview": ""})["data"])
        acc.add_subagent_event(_event("sa_1", "timeout", {"error": "boom"})["data"])
        (part,) = acc.build_message_parts()
        assert part["status"] == "timeout"
        assert part["error"] == "boom"

    def test_has_content_includes_subagent_runs(self):
        acc = BotMessageAccumulator()
        assert not acc.has_content()
        acc.add_subagent_event(_event("sa_1", "started", {"input_preview": ""})["data"])
        assert acc.has_content()

    def test_multiple_runs_stay_separate(self):
        acc = BotMessageAccumulator()
        acc.add_subagent_event(_event("sa_1", "text_delta", {"text": "A"})["data"])
        acc.add_subagent_event(
            _event("sa_2", "text_delta", {"text": "B"}, agent="writer")["data"]
        )
        parts = [p for p in acc.build_message_parts() if p["type"] == "subagent_run"]
        assert len(parts) == 2
        assert parts[0]["activity"] == [{"kind": "text", "text": "A"}]
        assert parts[1]["activity"] == [{"kind": "text", "text": "B"}]
        assert parts[1]["agent_name"] == "writer"

    def test_ignores_malformed_data(self):
        acc = BotMessageAccumulator()
        acc.add_subagent_event(None)
        acc.add_subagent_event("junk")
        acc.add_subagent_event({"kind": "text_delta", "payload": {"text": "x"}})
        assert acc.build_message_parts() == []
        assert not acc.has_content()

    def test_user_message_persisted_and_marked_relayed(self):
        acc = BotMessageAccumulator()
        acc.add_subagent_event(
            _event("sa_1", "started", {"input_preview": "task"})["data"]
        )
        acc.add_subagent_event(
            _event("sa_1", "user_message", {"seq": 0, "text": "note"})["data"]
        )
        acc.add_subagent_event(
            _event("sa_1", "user_message_relayed", {"seq": 0})["data"]
        )
        part = acc.subagent_runs["sa_1"]
        entry = part["activity"][-1]
        assert entry == {
            "kind": "user_message",
            "text": "note",
            "seq": 0,
            "relayed": True,
        }


class TestConsumeRouting:
    @pytest.mark.asyncio
    async def test_subagent_event_published_and_persisted(self):
        service = ChatService.__new__(ChatService)
        service.chat_runs = {}
        service.chat_runs_by_session = {}
        service.running_convs = {}
        saved = []

        async def _save(
            session_id, parts, agent_stats, refs, checkpoint_id, platform_id
        ):
            saved.append(parts)
            return SimpleNamespace(id=7, created_at=datetime.now(UTC))

        service.save_bot_message = _save

        run = ChatRunState(
            run_id="run-1",
            username="u",
            session_id="conv-1",
            llm_checkpoint_id="cp-1",
            platform_history_id="webchat",
            back_queue=asyncio.Queue(),
        )
        subscriber: asyncio.Queue = asyncio.Queue()
        run.subscribers.add(subscriber)

        # _consume_chat_run's finally block drains subscriber queues and
        # replaces their contents with a single None close-sentinel, so
        # published items must be collected while the run is still active.
        published = []

        async def _collect():
            while True:
                item = await subscriber.get()
                if item is None:
                    break
                published.append(item[1])

        collector = asyncio.create_task(_collect())
        task = asyncio.create_task(service._consume_chat_run(run))
        await run.back_queue.put(_event("sa_1", "started", {"input_preview": "t"}))
        await run.back_queue.put(_event("sa_1", "text_delta", {"text": "hi"}))
        await run.back_queue.put(
            {
                "type": "complete",
                "data": "hi",
                "streaming": True,
                "message_id": "run-1",
            }
        )
        await run.back_queue.put(
            {"type": "end", "data": "", "streaming": False, "message_id": "run-1"}
        )
        await asyncio.wait_for(task, timeout=5)
        await asyncio.wait_for(collector, timeout=5)

        kinds = [
            p["data"]["kind"] for p in published if p.get("type") == "subagent_event"
        ]
        assert kinds == ["started", "text_delta"]
        assert any(p.get("type") == "message_saved" for p in published)

        assert saved, "bot message should be persisted"
        final_parts = saved[-1]
        (sub_part,) = [p for p in final_parts if p["type"] == "subagent_run"]
        # Streamed text lands in the chronological activity log; part["text"]
        # is reserved for the final answer (completed.result_text).
        assert sub_part["activity"] == [{"kind": "text", "text": "hi"}]
        assert sub_part["status"] == "running"  # no completed event arrived
