"""Unit tests for the runner's injection-trace response (spec §4.3)."""

from __future__ import annotations

from types import SimpleNamespace

from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.pipeline.llm_request_trace import EVENT_EXTRA_KEY


def _runner_with_items(items: list[dict]) -> ToolLoopAgentRunner:
    runner = ToolLoopAgentRunner()
    event = SimpleNamespace(
        get_extra=lambda key, default=None: {EVENT_EXTRA_KEY: items}.get(key, default)
    )
    runner.run_context = SimpleNamespace(context=SimpleNamespace(event=event))
    return runner


def test_returns_none_without_recorded_items():
    assert _runner_with_items([])._injection_trace_response() is None


def test_builds_json_chain_payload():
    items = [{"plugin": "tc_memory", "handler": "decorate_llm_req", "changes": []}]
    resp = _runner_with_items(items)._injection_trace_response()

    assert resp is not None
    assert resp.type == "llm_request_injections"
    chain = resp.data["chain"]
    assert chain.type == "llm_request_injections"
    assert chain.chain[0].data == {"items": items}
