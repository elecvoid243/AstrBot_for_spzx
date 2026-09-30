"""Tests for SubAgentEventSink AgentResponse -> SSE payload mapping."""

# Author: elecvoid243
# Date: 2026-07-26
# Plan: docs/superpowers/plans/2026-07-26-subagent-chatui-progress.md (Task 1)

import pytest

from astrbot.core.agent.response import AgentResponse
from astrbot.core.message.components import Json
from astrbot.core.message.message_event_result import MessageChain


class _QueueStub:
    def __init__(self, accepted=True):
        self.payloads = []
        self.accepted = accepted

    async def put_back_queue(self, request_id, payload):
        if self.accepted:
            self.payloads.append((request_id, payload))
        return self.accepted


def _delta(text, reasoning=False):
    chain_type = "reasoning" if reasoning else None
    return AgentResponse(
        type="streaming_delta",
        data={"chain": MessageChain(type=chain_type).message(text)},
    )


def _tool_call(call_id="c1", name="web_search", args=None):
    return AgentResponse(
        type="tool_call",
        data={
            "chain": MessageChain(
                type="tool_call",
                chain=[Json(data={"id": call_id, "name": name, "args": args or {}})],
            )
        },
    )


def _tool_result(call_id="c1", result="ok"):
    return AgentResponse(
        type="tool_call_result",
        data={
            "chain": MessageChain(
                type="tool_call",
                chain=[Json(data={"id": call_id, "result": result})],
            )
        },
    )


@pytest.mark.asyncio
async def test_start_emits_started_event(monkeypatch):
    import astrbot.core.subagent_event_sink as sink_mod

    stub = _QueueStub()
    monkeypatch.setattr(sink_mod, "webchat_queue_mgr", stub)
    sink = sink_mod.SubAgentEventSink("msg-1", "researcher", "do research")
    await sink.start()
    ((request_id, payload),) = stub.payloads
    assert request_id == "msg-1"
    assert payload["type"] == "subagent_event"
    assert payload["message_id"] == "msg-1"
    assert payload["streaming"] is True
    data = payload["data"]
    assert data["subagent_run_id"] == sink.subagent_run_id
    assert data["agent_name"] == "researcher"
    assert data["kind"] == "started"
    assert data["payload"]["input_preview"] == "do research"
    assert isinstance(data["ts"], float)


@pytest.mark.asyncio
async def test_start_emits_full_input(monkeypatch):
    import astrbot.core.subagent_event_sink as sink_mod

    stub = _QueueStub()
    monkeypatch.setattr(sink_mod, "webchat_queue_mgr", stub)
    full_input = "x" * 500
    sink = sink_mod.SubAgentEventSink(
        "msg-1", "researcher", full_input[:200], input_full=full_input
    )
    await sink.start()
    ((_, payload),) = stub.payloads
    assert payload["data"]["payload"]["input_preview"] == "x" * 200
    assert payload["data"]["payload"]["input_full"] == full_input


@pytest.mark.asyncio
async def test_text_and_reasoning_deltas(monkeypatch):
    import astrbot.core.subagent_event_sink as sink_mod

    stub = _QueueStub()
    monkeypatch.setattr(sink_mod, "webchat_queue_mgr", stub)
    sink = sink_mod.SubAgentEventSink("msg-1", "researcher")
    await sink(_delta("hello"))
    await sink(_delta("thinking...", reasoning=True))
    kinds = [p["data"]["kind"] for _, p in stub.payloads]
    assert kinds == ["text_delta", "reasoning_delta"]
    assert stub.payloads[0][1]["data"]["payload"] == {"text": "hello"}
    assert stub.payloads[1][1]["data"]["payload"] == {"text": "thinking..."}


@pytest.mark.asyncio
async def test_tool_call_and_truncated_result(monkeypatch):
    import astrbot.core.subagent_event_sink as sink_mod

    stub = _QueueStub()
    monkeypatch.setattr(sink_mod, "webchat_queue_mgr", stub)
    sink = sink_mod.SubAgentEventSink("msg-1", "researcher")
    await sink(_tool_call(args={"q": "astrbot"}))
    await sink(_tool_result(result="x" * 5000))
    call_payload = stub.payloads[0][1]["data"]["payload"]
    assert call_payload == {"id": "c1", "name": "web_search", "args": {"q": "astrbot"}}
    result_payload = stub.payloads[1][1]["data"]["payload"]
    assert result_payload["id"] == "c1"
    assert len(result_payload["result"]) == 2000


@pytest.mark.asyncio
async def test_complete_and_fail(monkeypatch):
    import astrbot.core.subagent_event_sink as sink_mod

    stub = _QueueStub()
    monkeypatch.setattr(sink_mod, "webchat_queue_mgr", stub)
    sink = sink_mod.SubAgentEventSink("msg-1", "researcher")
    await sink.complete("final answer", 12.5)
    # complete() is terminal: later events on the same sink are suppressed.
    await sink.fail("timeout", "suppressed")
    failing_sink = sink_mod.SubAgentEventSink("msg-1", "researcher")
    await failing_sink.fail("timeout", "timed out")
    completed = stub.payloads[0][1]["data"]
    assert completed["kind"] == "completed"
    assert completed["payload"]["result_text"] == "final answer"
    assert completed["payload"]["execution_time"] == 12.5
    failed = stub.payloads[1][1]["data"]
    assert failed["kind"] == "timeout"
    assert failed["payload"]["error"] == "timed out"


@pytest.mark.asyncio
async def test_stops_emitting_after_queue_closed(monkeypatch):
    import astrbot.core.subagent_event_sink as sink_mod

    stub = _QueueStub(accepted=False)
    monkeypatch.setattr(sink_mod, "webchat_queue_mgr", stub)
    sink = sink_mod.SubAgentEventSink("msg-1", "researcher")
    await sink(_delta("a"))
    await sink(_delta("b"))
    await sink.complete("done", 1.0)
    assert stub.payloads == []  # nothing recorded, no exception raised


@pytest.mark.asyncio
async def test_ignores_unrelated_response_types(monkeypatch):
    import astrbot.core.subagent_event_sink as sink_mod

    stub = _QueueStub()
    monkeypatch.setattr(sink_mod, "webchat_queue_mgr", stub)
    sink = sink_mod.SubAgentEventSink("msg-1", "researcher")
    await sink(AgentResponse(type="agent_stats", data={"chain": MessageChain()}))
    await sink(AgentResponse(type="llm_result", data={"chain": MessageChain()}))
    assert stub.payloads == []


@pytest.mark.asyncio
async def test_sink_emits_user_message_and_relayed(monkeypatch):
    import astrbot.core.subagent_event_sink as sink_mod

    stub = _QueueStub()
    monkeypatch.setattr(sink_mod, "webchat_queue_mgr", stub)
    sink = sink_mod.SubAgentEventSink("msg-1", "researcher", "do research")

    await sink.user_message(0, "please also run the tests")
    await sink.user_message_relayed(0)

    payloads = [p for _, p in stub.payloads]
    kinds = [p["data"]["kind"] for p in payloads]
    assert kinds == ["user_message", "user_message_relayed"]
    assert payloads[0]["data"]["payload"] == {
        "seq": 0,
        "text": "please also run the tests",
    }
    assert payloads[1]["data"]["payload"] == {"seq": 0}


@pytest.mark.asyncio
async def test_sink_user_message_not_emitted_after_close(monkeypatch):
    import astrbot.core.subagent_event_sink as sink_mod

    stub = _QueueStub()
    monkeypatch.setattr(sink_mod, "webchat_queue_mgr", stub)
    sink = sink_mod.SubAgentEventSink("msg-1", "researcher", "do research")
    await sink.complete("done", 1.0)

    await sink.user_message(0, "too late")
    assert stub.payloads and all(
        p["data"]["kind"] != "user_message" for _, p in stub.payloads
    )
