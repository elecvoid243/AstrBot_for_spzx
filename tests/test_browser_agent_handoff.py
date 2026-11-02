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
from astrbot.core.astr_agent_run_util import run_agent
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.utils.active_event_registry import active_event_registry
from test_tool_loop_agent_runner import MockEvent, MockHooks, MockProvider


class HandoffEvent(MockEvent):
    """Provide the external platform event contract used by the production driver."""

    def __init__(self):
        super().__init__("handoff-chat", "owner")
        self.extras = {}
        self.send = AsyncMock()
        self.set_result = lambda result: None
        self.clear_result = lambda: None

    def get_extra(self, key, default=None):
        """Read event-scoped metadata.

        Args:
            key: Metadata name.
            default: Value used when the name is absent.

        Returns:
            Stored metadata or the supplied default.
        """
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        """Store event-scoped metadata.

        Args:
            key: Metadata name.
            value: Metadata value.
        """
        self.extras[key] = value

    def is_stopped(self):
        """Report whether platform processing has stopped.

        Returns:
            False, since Agent cancellation uses its separate event flag.
        """
        return False

    def get_platform_id(self):
        """Return the test platform identity.

        Returns:
            The external test platform's identifier.
        """
        return "test"


async def make_runner(provider, executor=None, event=None):
    """Create a real runner with a trusted chat and a short tool deadline.

    Args:
        provider: Deterministic model implementation.
        executor: Optional controlled tool executor.
        event: Optional event admitted before request construction.

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
            context=SimpleNamespace(event=event or MockEvent("handoff-chat", "owner")),
            tool_call_timeout=0.05,
        ),
        tool_executor=executor,
        agent_hooks=MockHooks(),
        streaming=False,
    )
    return runner


async def collect(runner, max_step=8, production=False):
    """Consume normal Agent events until completion.

    Args:
        runner: Initialized local runner.
        max_step: Model round budget for the current turn.
        production: Use the ChatUI production driver instead of the runner helper.

    Returns:
        All emitted Agent events.
    """
    if production:
        return [item async for item in run_agent(runner, max_step=max_step, stream_to_general=True)]
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


@pytest.mark.asyncio
async def test_completed_handback_before_reset_refreshes_only_the_already_started_turn():
    """Retain a handback during construction without leaking it into future turns."""
    provider = MockProvider()
    provider.text_chat = AsyncMock(return_value=LLMResponse(role="assistant", completion_text="done"))
    event = HandoffEvent()
    active_event_registry.register(event)
    gate = active_event_registry.get_agent_manual_pause(event.unified_msg_origin)
    try:
        gate.pause()
        gate.resume("current page: use the new green button")
        active_event_registry.register(event)
        runner = await make_runner(provider, event=event)
        _ = [item async for item in run_agent(runner, max_step=1, stream_to_general=True)]
        assert "new green button" in str(provider.text_chat.call_args.kwargs["contexts"])
    finally:
        active_event_registry.unregister(event)

    provider.text_chat.reset_mock()
    future_event = HandoffEvent()
    active_event_registry.register(future_event)
    try:
        future_runner = await make_runner(provider, event=future_event)
        _ = [item async for item in run_agent(future_runner, max_step=1, stream_to_general=True)]
        assert "new green button" not in str(provider.text_chat.call_args.kwargs["contexts"])
    finally:
        active_event_registry.unregister(future_event)


@pytest.mark.asyncio
@pytest.mark.parametrize("pause_round", [1, 4])
@pytest.mark.parametrize("cancel", [False, True])
async def test_production_driver_refunds_interrupted_model_at_normal_and_last_recovery_boundary(pause_round, cancel):
    """Keep the real driver alive without spending cancelled model round budget.

    Args:
        pause_round: First normal request or last denial recovery request.
        cancel: Stop the paused turn instead of completing its handback.
    """
    provider = MockProvider()
    entered, cancelled = asyncio.Event(), asyncio.Event()
    requests, effects = [], []

    async def model(**kwargs):
        """Return completed rounds around one externally blocked model request.

        Args:
            **kwargs: Real runner request including its model-facing context.

        Returns:
            A tool request or final completion.
        """
        requests.append(kwargs)
        number = len(requests)
        if number == pause_round:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        if number < pause_round or pause_round == 1 and number == 2:
            return LLMResponse(role="assistant", tools_call_name=["browser_click"], tools_call_args=[{"x": number}], tools_call_ids=[f"call-{number}"])
        return LLMResponse(role="assistant", completion_text="fresh completion")

    class Executor:
        @classmethod
        async def execute(cls, tool, run_context, **kwargs):
            """Record the externally observable browser action.

            Args:
                tool: Selected browser tool.
                run_context: Current Agent context.
                **kwargs: Browser action arguments.

            Yields:
                A real tool protocol result for the recorded effect.
            """
            effects.append(kwargs["x"])
            yield CallToolResult(content=[TextContent(type="text", text="effect completed")])

    provider.text_chat = model
    event = HandoffEvent()
    runner = await make_runner(provider, Executor, event)
    running = asyncio.create_task(collect(runner, 1, production=True))
    await asyncio.wait_for(entered.wait(), 2)
    gate = active_event_registry.get_agent_manual_pause(event.unified_msg_origin)
    gate.pause()
    await asyncio.wait_for(cancelled.wait(), 2)
    await asyncio.sleep(0.1)
    assert not running.done(), "The last recovery boundary must retain its paused turn"
    if cancel:
        event.set_extra("agent_stop_requested", True)
        await asyncio.wait_for(running, 2)
        assert runner.was_aborted() and runner.done()
        gate.resume("new green button")
        assert len(requests) == pause_round
    else:
        gate.resume("current page: use the new green button")
        await asyncio.wait_for(running, 2)
        assert runner.done()
        assert runner.get_final_llm_resp().completion_text == "fresh completion"
        assert "new green button" in str(requests[pause_round]["contexts"])
        assert effects == ([2] if pause_round == 1 else [1])
