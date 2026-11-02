"""Exercise manual handoff through the actual local Agent runner."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.base import AgentState
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from test_tool_loop_agent_runner import MockEvent, MockHooks, MockProvider


async def make_runner(provider, executor=None):
    """Create a real runner with a trusted chat and a short tool deadline.

    Args:
        provider: Deterministic model implementation.
        executor: Optional controlled tool executor.

    Returns:
        A runner whose pause is shared with the browser owner.
    """
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=ProviderRequest(
            prompt="continue browser task",
            func_tool=ToolSet(tools=[FunctionTool(name="browser_click", description="Click", parameters={"type": "object", "properties": {"x": {"type": "integer"}}})]),
        ),
        run_context=ContextWrapper(
            context=SimpleNamespace(event=MockEvent("handoff-chat", "owner")),
            tool_call_timeout=0.05,
        ),
        tool_executor=executor,
        agent_hooks=MockHooks(),
        streaming=False,
    )
    return runner


async def collect(runner, max_step=8):
    """Consume normal Agent events until completion.

    Args:
        runner: Initialized local runner.
        max_step: Model round budget for the current turn.

    Returns:
        All emitted Agent events.
    """
    return [item async for item in runner.step_until_done(max_step)]


@pytest.mark.asyncio
@pytest.mark.parametrize("completion_race", [False, True])
async def test_takeover_discards_pending_model_and_waits_for_explicit_handback(completion_race):
    """Discard even a model response completing in the same turn as takeover.

    Args:
        completion_race: Complete the model concurrently with takeover.
    """
    provider = MockProvider()
    entered, ready, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    observed = []

    async def model(**kwargs):
        observed.append(kwargs["contexts"])
        if len(observed) == 1:
            entered.set()
            try:
                await ready.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return LLMResponse(role="assistant", completion_text="stale final")
        return LLMResponse(role="assistant", completion_text="fresh final")

    provider.text_chat = model
    runner = await make_runner(provider)
    running = asyncio.create_task(collect(runner, 1))
    await asyncio.wait_for(entered.wait(), 2)
    if completion_race:
        ready.set()
    gate = runner._manual_pause
    gate.pause()
    await asyncio.sleep(0.15)
    assert not running.done()
    assert runner.state == AgentState.PAUSED
    assert len(observed) == 1
    runner.follow_up(message_text="A new message must not resume the paused turn")
    await asyncio.sleep(0.1)
    assert len(observed) == 1
    gate.resume('{"url":"http://fixture/new","interactive_elements":["new target"]}')
    responses = await asyncio.wait_for(running, 2)
    assert runner.get_final_llm_resp().completion_text == "fresh final"
    assert all("stale final" not in str(item) for item in responses)
    assert "new target" in str(observed[-1])
    assert len(observed) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("pause_at", ["before_batch", "during_tool", "after_result"])
async def test_takeover_pairs_and_discards_remaining_parallel_batch(pause_at):
    """Preserve call IDs and truthful results while preventing queued effects.

    Args:
        pause_at: Takeover boundary within a model's parallel call batch.
    """
    provider = MockProvider()
    entered = asyncio.Event()
    executed, contexts = [], []
    runner = None

    async def model(**kwargs):
        contexts.append(kwargs["contexts"])
        if len(contexts) == 1:
            return LLMResponse(
                role="assistant",
                tools_call_name=["browser_click", "browser_click"],
                tools_call_args=[{"x": 1}, {"x": 2}],
                tools_call_ids=["first", "queued"],
            )
        return LLMResponse(role="assistant", completion_text="observed new page")

    class Executor:
        @classmethod
        async def execute(cls, tool, run_context, **kwargs):
            executed.append(kwargs["x"])
            entered.set()
            if pause_at == "during_tool":
                await asyncio.Event().wait()
            yield CallToolResult(content=[TextContent(type="text", text="real result")])
            if pause_at == "after_result":
                runner._manual_pause.pause()

    provider.text_chat = model
    runner = await make_runner(provider, Executor)
    if pause_at == "before_batch":
        runner.agent_hooks.on_tool_start = AsyncMock(side_effect=lambda *args: runner._manual_pause.pause())
    running = asyncio.create_task(collect(runner))
    if pause_at == "during_tool":
        await asyncio.wait_for(entered.wait(), 2)
        runner._manual_pause.pause()
    await asyncio.sleep(0.15)
    assert not running.done()
    assert 2 not in executed
    assert runner.state == AgentState.PAUSED
    runner._manual_pause.resume("fresh browser state")
    await asyncio.wait_for(running, 2)
    results = [m for m in runner.run_context.messages if m.role == "tool"]
    assert [m.tool_call_id for m in results] == ["first", "queued"]
    assert "Not executed" in str(results[-1].content)
    if pause_at != "after_result":
        assert "Interrupted" in str(results[0].content)
    assert len(contexts) == 2
    assert "fresh browser state" in str(contexts[-1])


@pytest.mark.asyncio
async def test_stop_during_human_wait_does_not_revive_cancelled_turn():
    """Keep cancellation responsive and prevent handback from starting new work."""
    provider = MockProvider()
    provider.text_chat = AsyncMock()
    runner = await make_runner(provider)
    gate = runner._manual_pause
    gate.pause()
    running = asyncio.create_task(collect(runner))
    await asyncio.sleep(0.1)
    runner.request_stop()
    await asyncio.wait_for(running, 1)
    assert runner.was_aborted()
    gate.resume("new page")
    assert runner.done()
    provider.text_chat.assert_not_awaited()
    next_runner = await make_runner(provider)
    assert next_runner._manual_pause is gate
    assert not next_runner._manual_pause.requested.is_set()


@pytest.mark.asyncio
async def test_pause_before_runner_start_observes_handback_and_keeps_begin_hook():
    """A run still preparing its request must not escape the chat-owned gate."""
    provider = MockProvider()
    provider.text_chat = AsyncMock(return_value=LLMResponse(role="assistant", completion_text="done"))
    owner_runner = await make_runner(provider)
    gate = owner_runner._manual_pause
    gate.pause()
    pending_runner = await make_runner(provider)
    pending_runner.agent_hooks.on_agent_begin = AsyncMock()
    running = asyncio.create_task(collect(pending_runner))
    await asyncio.sleep(0.1)
    provider.text_chat.assert_not_awaited()
    gate.resume("page changed before the runner began")
    await asyncio.wait_for(running, 2)
    pending_runner.agent_hooks.on_agent_begin.assert_awaited_once()
    assert "page changed before the runner began" in str(provider.text_chat.call_args)
