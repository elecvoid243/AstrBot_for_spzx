"""Runner integration tests for ChatUI developer-mode request snapshots.

Author: impl_task5
Date: 2026-11-15
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from mcp.types import CallToolResult, TextContent

from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.db.po import Conversation
from astrbot.core.provider import request_snapshot
from astrbot.core.provider.entities import LLMResponse, ProviderRequest, TokenUsage
from astrbot.core.provider.provider import Provider
from astrbot.core.provider.request_snapshot import request_snapshots


class FakeSnapshotEvent:
    """Minimal event carrying the per-turn extras used by the trace helpers."""

    def __init__(self) -> None:
        self.unified_msg_origin = "webchat:FriendMessage:session-1"
        self._extras: dict[str, object] = {}

    def get_extra(self, key: str, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key: str, value) -> None:
        self._extras[key] = value

    def get_platform_name(self) -> str:
        return "webchat"


class FakeSnapshotProvider(Provider):
    """Provider double that captures one snapshot per ``text_chat`` call."""

    SNAPSHOT_ADAPTER = "openai_chat"

    def __init__(
        self,
        *,
        tool_calls: bool = True,
        extra_captures: int = 0,
        fail: bool = False,
        stream_chunks: list[str] | None = None,
    ) -> None:
        super().__init__(
            {
                "id": "fake-snapshot-provider",
                "type": "openai_chat_completion",
                "max_context_tokens": 0,
            },
            {},
        )
        self.call_count = 0
        self.tool_calls = tool_calls
        self.extra_captures = extra_captures
        self.fail = fail
        self.stream_chunks = stream_chunks

    def get_current_key(self) -> str:
        return "test-key"

    def set_key(self, key: str) -> None:
        pass

    async def get_models(self) -> list[str]:
        return ["fake-model"]

    async def text_chat(self, **kwargs) -> LLMResponse:
        self.call_count += 1
        # Simulates the provider hook running once per network attempt. Extra
        # captures model provider-level retries sharing this invocation.
        for _ in range(1 + self.extra_captures):
            request_snapshots.capture(
                self,
                {
                    "model": "fake-model",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": False,
                },
                adapter=self.SNAPSHOT_ADAPTER,
                tool_set=kwargs.get("func_tool"),
            )
        if self.fail:
            raise RuntimeError("provider exploded")
        if self.tool_calls and self.call_count == 1:
            return LLMResponse(
                role="assistant",
                completion_text="",
                tools_call_name=["test_tool"],
                tools_call_args=[{"query": "test"}],
                tools_call_ids=["call_1"],
                usage=TokenUsage(input_other=10, output=5),
            )
        return LLMResponse(
            role="assistant",
            completion_text="final answer",
            usage=TokenUsage(input_other=12, output=4),
        )

    async def text_chat_stream(self, **kwargs):
        self.call_count += 1
        # The provider hook runs when the stream is iterated, so a missing
        # scope here would drop the snapshot entirely.
        for _ in range(1 + self.extra_captures):
            request_snapshots.capture(
                self,
                {
                    "model": "fake-model",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
                adapter=self.SNAPSHOT_ADAPTER,
                tool_set=kwargs.get("func_tool"),
            )
        if self.fail:
            raise RuntimeError("provider exploded")
        for text in self.stream_chunks or []:
            yield LLMResponse(
                role="assistant",
                completion_text=text,
                is_chunk=True,
            )
        yield LLMResponse(
            role="assistant",
            completion_text="final answer",
            usage=TokenUsage(input_other=12, output=4),
        )


class FakeSnapshotHooks(BaseAgentRunHooks):
    """No-op agent hooks for runner tests."""

    async def on_agent_begin(self, run_context) -> None:
        pass

    async def on_tool_start(self, run_context, tool, tool_args) -> None:
        pass

    async def on_tool_end(self, run_context, tool, tool_args, tool_result) -> None:
        pass

    async def on_agent_done(self, run_context, llm_response) -> None:
        pass


class FakeSnapshotToolExecutor:
    """Return one short text result for every tool call."""

    def execute(self, tool, run_context, **tool_args):
        async def generator():
            yield CallToolResult(content=[TextContent(type="text", text="tool result")])

        return generator()


def _tool_set() -> ToolSet:
    return ToolSet(
        tools=[
            FunctionTool(
                name="test_tool",
                description="test tool",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
                handler=AsyncMock(),
            )
        ]
    )


class FakeRunnerFixture:
    """Bundle of the runner and its doubles used by the tests."""

    def __init__(self, provider: FakeSnapshotProvider) -> None:
        self.provider = provider
        self.event = FakeSnapshotEvent()
        self.context = SimpleNamespace(event=self.event, extra={})
        self.run_context = ContextWrapper(context=self.context)
        self.runner = ToolLoopAgentRunner()

    async def reset(self, *, streaming: bool = False) -> ToolLoopAgentRunner:
        await self.runner.reset(
            provider=self.provider,
            request=ProviderRequest(
                prompt="hello",
                session_id="webchat:FriendMessage:session-1",
                conversation=Conversation(
                    platform_id="webchat",
                    user_id="user-1",
                    cid="session-1",
                ),
                func_tool=_tool_set(),
                contexts=[],
            ),
            run_context=self.run_context,
            tool_executor=FakeSnapshotToolExecutor(),
            agent_hooks=FakeSnapshotHooks(),
            streaming=streaming,
        )
        return self.runner


@pytest.fixture(autouse=True)
def recorder(monkeypatch: pytest.MonkeyPatch):
    """Enable developer mode and isolate the singleton recorder."""

    request_snapshots.reset()
    monkeypatch.setattr(request_snapshot, "developer_mode_enabled", lambda: True)
    yield
    request_snapshots.reset()


@pytest_asyncio.fixture
async def fake_runner() -> ToolLoopAgentRunner:
    fixture = FakeRunnerFixture(FakeSnapshotProvider())
    return await fixture.reset()


def _snapshot_items(event) -> list[dict]:
    chain = event.data["chain"]
    assert chain.type == "llm_request_snapshots"
    payload = chain.chain[0].data
    if not isinstance(payload, dict):
        payload = json.loads(payload)
    return payload["items"]


@pytest.mark.asyncio
async def test_two_model_calls_emit_two_snapshot_envelopes(fake_runner):
    events = [event async for event in fake_runner.step_until_done(max_step=4)]
    snapshots = [e for e in events if e.type == "llm_request_snapshots"]

    assert len(snapshots) == 2
    items = _snapshot_items(snapshots[0])
    assert items[0]["kind"] == "chat"
    assert items[0]["turn_id"] == fake_runner._snapshot_turn_id
    assert "raw" not in items[0]
    assert snapshots[0].data["chain"].chain[0].data == {"items": items}


@pytest.mark.asyncio
async def test_retry_attempts_share_invocation():
    fixture = FakeRunnerFixture(FakeSnapshotProvider(extra_captures=1))
    runner = await fixture.reset()

    events = [event async for event in runner.step_until_done(max_step=4)]
    snapshots = [e for e in events if e.type == "llm_request_snapshots"]

    assert len(snapshots) == 2
    final_items = _snapshot_items(snapshots[-1])
    assert [item["request"]["attempt"] for item in final_items] == [1, 2]
    assert {item["invocation_id"] for item in final_items} == {
        final_items[0]["invocation_id"]
    }


@pytest.mark.asyncio
async def test_subagent_scope_skips_capture_and_event(fake_runner):
    fake_runner.run_context.context.extra["is_subagent"] = True

    events = [event async for event in fake_runner.step_until_done(max_step=4)]

    assert not [e for e in events if e.type == "llm_request_snapshots"]
    assert request_snapshots.list_for_session("session-1")["items"] == []


@pytest.mark.asyncio
async def test_provider_exception_marks_error():
    fixture = FakeRunnerFixture(FakeSnapshotProvider(fail=True))
    runner = await fixture.reset()

    events = [event async for event in runner.step_until_done(max_step=4)]
    snapshots = [e for e in events if e.type == "llm_request_snapshots"]

    assert len(snapshots) == 1
    items = _snapshot_items(snapshots[0])
    assert items[0]["status"] == "error"
    assert items[0]["response"]["error"]["type"] == "RuntimeError"
    assert "provider exploded" in items[0]["response"]["error"]["message"]


@pytest.mark.asyncio
async def test_stream_scope_covers_iteration_and_counts_chunks():
    fixture = FakeRunnerFixture(
        FakeSnapshotProvider(tool_calls=False, stream_chunks=["one ", "two"])
    )
    runner = await fixture.reset(streaming=True)

    events = [event async for event in runner.step_until_done(max_step=4)]
    snapshots = [e for e in events if e.type == "llm_request_snapshots"]

    assert len(snapshots) == 1
    items = _snapshot_items(snapshots[0])
    assert items[0]["status"] == "ok"
    assert items[0]["response"]["chunks"] == 2


@pytest.mark.asyncio
async def test_disabled_mode_emits_no_event(
    fake_runner, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(request_snapshot, "developer_mode_enabled", lambda: False)

    events = [event async for event in fake_runner.step_until_done(max_step=4)]

    assert not [e for e in events if e.type == "llm_request_snapshots"]
    assert request_snapshots.list_for_session("session-1")["items"] == []


@pytest.mark.asyncio
async def test_event_carries_no_raw_payload(fake_runner):
    events = [event async for event in fake_runner.step_until_done(max_step=4)]
    snapshots = [e for e in events if e.type == "llm_request_snapshots"]
    items = [item for event in snapshots for item in _snapshot_items(event)]

    assert items
    for item in items:
        assert "raw" not in item
