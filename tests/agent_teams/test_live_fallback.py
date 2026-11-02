"""Exercise mixed provider capabilities through the real team runner."""

import asyncio
import copy

import pytest

from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.provider.provider import Provider
from tests.test_tool_loop_agent_runner import (
    ContextWrapper,
    MockHooks,
    MockProvider,
    MockToolExecutor,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("primary_streaming", [False, True])
async def test_team_sink_uses_each_fallback_provider_stream_capability(primary_streaming):
    from astrbot.core.agent_team_execution import (
        NodeExecutionBinding,
        current_team_execution,
    )

    first_visible = asyncio.Event()
    release = asyncio.Event()
    records = []

    async def record_process(identity, kind, data):
        records.append((identity, kind, copy.deepcopy(data)))
        if kind == "model_delta":
            first_visible.set()

    async def begin_usage(payload):
        return {"receipt_id": payload["receipt_id"], "request": payload, "max_output_tokens": 2048}

    async def report_usage(identity, report):
        pass

    class NonStreaming(MockProvider):
        text_chat_stream = Provider.text_chat_stream

        def __init__(self, fail):
            super().__init__()
            self.fail = fail

        async def text_chat(self, **kwargs):
            self.call_count += 1
            return LLMResponse(role="err" if self.fail else "assistant", completion_text="unavailable" if self.fail else "Final answer")

    class Streaming(MockProvider):
        def __init__(self, fail):
            super().__init__()
            self.fail = fail

        async def text_chat_stream(self, **kwargs):
            self.call_count += 1
            if self.fail:
                yield LLMResponse(role="err", completion_text="unavailable")
                return
            yield LLMResponse(role="assistant", is_chunk=True, completion_text="First ")
            await release.wait()
            yield LLMResponse(role="assistant", is_chunk=True, completion_text="answer")
            yield LLMResponse(role="assistant", completion_text="First answer")

    primary = Streaming(True) if primary_streaming else NonStreaming(True)
    fallback = NonStreaming(False) if primary_streaming else Streaming(False)
    binding = NodeExecutionBinding(run_id="run", team_id="team", member_id="member", node_id=None,
        umo="test:team", owner_username="owner", config_id=None, record_process=record_process,
        begin_usage=begin_usage, report_usage=report_usage)
    token = current_team_execution.set(binding)
    try:
        runner = ToolLoopAgentRunner()
        await runner.reset(provider=primary, request=ProviderRequest(prompt="test"),
            run_context=ContextWrapper(context=None), tool_executor=MockToolExecutor(),
            agent_hooks=MockHooks(), streaming=primary_streaming, fallback_providers=[fallback])
        output = []
        async def consume():
            async for event in runner.step_until_done(3):
                output.append(event)
        task = asyncio.create_task(consume())
        if not primary_streaming:
            waiter = asyncio.create_task(first_visible.wait())
            done, _ = await asyncio.wait({waiter, task}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                await task
                pytest.fail(f"No streaming observation: {records!r}")
            assert waiter in done
            assert not any(event.type == "streaming_delta" for event in output)
            assert not task.done()
            release.set()
        await asyncio.wait_for(task, 5)
        assert primary.call_count == fallback.call_count == 1
        ends = [data for _, kind, data in records if kind == "model_delivery" and data["status"] == "completed"]
        assert len(ends) == 1
        assert ends[0]["text"] == ("Final answer" if primary_streaming else "First answer")
        assert len([event for event in output if event.type == "llm_result"]) == 1
        if primary_streaming:
            assert not any(kind == "model_delta" for _, kind, _ in records)
        else:
            assert not any(event.type == "streaming_delta" for event in output)
            assert "".join(data["text"] for _, kind, data in records if kind == "model_delta") == "First answer"
    finally:
        current_team_execution.reset(token)
