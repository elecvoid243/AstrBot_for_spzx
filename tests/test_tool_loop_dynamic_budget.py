"""Validate dynamic budgets against actual runner deliveries and tool effects."""

import copy

import pytest

from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.message import Message, TextPart, bind_checkpoint_messages
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.agent_team_execution import (
    NodeExecutionBinding,
    current_team_execution,
)
from astrbot.core.exceptions import ContextCapacityError, EmptyModelOutputError
from astrbot.core.provider.entities import LLMResponse, ProviderRequest, TokenUsage
from astrbot.core.utils.prefix_diagnostics import (
    fingerprint_messages,
    fingerprint_tools,
    get_session_request,
)
from tests.test_tool_loop_context_budget import BudgetProvider, BudgetToolExecutor


def tool_response(call_id="one", usage=50):
    """Create a scripted successful tool plan.

    Args:
        call_id: Identity used by the scripted tool plan.
        usage: Input usage, or None to omit the usage report.

    Returns:
        Complete provider response requesting one tool execution.
    """
    return LLMResponse(
        role="assistant",
        tools_call_name=["read"],
        tools_call_ids=[call_id],
        tools_call_args=[{}],
        usage=TokenUsage(input_other=usage) if usage else None,
    )


async def make_runner(provider, *, request=None, streaming=False, **kwargs):
    """Build the real runner with an observable executor.

    Args:
        provider: Scripted provider that records actual deliveries.
        request: Optional request overriding the shared history fixture.
        streaming: Exercise the streaming provider interface.
        **kwargs: Existing runner reset options for the scenario.

    Returns:
        Initialized runner and its recording tool executor.
    """
    runner = ToolLoopAgentRunner()
    executor = BudgetToolExecutor("small result")
    await runner.reset(
        provider=provider,
        request=request
        or ProviderRequest(
            prompt="task",
            system_prompt="system",
            contexts=[
                {"role": "user", "content": "old history " * 50},
                {"role": "assistant", "content": "old answer " * 50},
            ],
            func_tool=ToolSet(
                tools=[FunctionTool(name="read", description="Read", parameters={})]
            ),
        ),
        run_context=ContextWrapper(context=None),
        tool_executor=executor,
        agent_hooks=BaseAgentRunHooks(),
        streaming=streaming,
        **kwargs,
    )
    return runner, executor


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("change", ["prefix", "system", "schema", "model"])
async def test_dynamic_request_changes_invalidate_successful_usage(streaming, change):
    provider = BudgetProvider(
        [tool_response(), LLMResponse(role="assistant", completion_text="done")],
        max_context_tokens=1000,
    )
    runner, executor = await make_runner(provider, streaming=streaming)
    if change in {"prefix", "model"}:
        runner.run_context.messages[1].content = "a" * 1000
        runner.run_context.messages[2].content = "b" * 1000
    async for _ in runner.step():
        pass
    if change == "prefix":
        runner.run_context.messages[1].content = "中" * 1200
    elif change == "system":
        runner.run_context.messages[0].content = "system " * 280
    elif change == "schema":
        runner.req.func_tool.tools[0].description = "schema " * 280
    else:
        runner.req.model = "different-model"
        # A low usage from the previous model must not cover this content.
        runner.run_context.messages.append(Message(role="user", content="x" * 600))
    events = [event async for event in runner.step()]

    assert len(provider.requests) == 2
    assert len(executor.calls) == 1
    assert any(event.type == "context_compression" for event in events)
    sent = provider.requests[-1]
    assert not any(
        message.role == "user"
        and ("old history" in str(message.content) or "中" in str(message.content))
        for message in sent["contexts"]
    )
    if change == "system":
        assert sent["contexts"][0].content == "system " * 280
    elif change == "schema":
        assert sent["func_tool"].tools[0].description == "schema " * 280
    elif change == "model":
        assert sent["model"] == "different-model"


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("window,compress", [(1000, True), (20000, False), (0, False)])
@pytest.mark.parametrize("failure", ["exception", "error_response"])
async def test_fallback_and_subsequent_steps_use_the_target_model_and_window(
    streaming, window, compress, failure
):
    failed = (
        RuntimeError("switch provider")
        if failure == "exception"
        else LLMResponse(
            role="err", completion_text="failed", usage=TokenUsage(input_other=100000)
        )
    )
    primary = BudgetProvider(
        [tool_response(), failed],
        id="primary",
        max_context_tokens=5000,
        compress_threshold_tokens=4000,
    )
    fallback = BudgetProvider(
        [tool_response("two"), LLMResponse(role="assistant", completion_text="done")],
        id="fallback",
        max_context_tokens=window,
    )
    fallback.set_model("fallback-model")
    runner, executor = await make_runner(
        primary, streaming=streaming, fallback_providers=[fallback]
    )
    runner.req.model = "primary-only-model"
    executor.text = "x" * 1700
    events = [event async for event in runner.step_until_done(4)]

    assert len(primary.requests) == len(fallback.requests) == 2
    assert len(executor.calls) == 2
    assert primary.requests[0]["model"] == "primary-only-model"
    assert all(not request.get("model") for request in fallback.requests)
    assert (
        any(
            "old history" in str(message.content)
            for message in fallback.requests[0]["contexts"]
        )
        is not compress
    )
    assert any(event.type == "context_compression" for event in events) is compress


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_late_member_message_is_checked_and_saved_as_the_sent_view(streaming):
    checkpoints = []
    pending = [{"message_id": "late", "text": "x" * 1800}]

    async def pending_messages():
        messages = list(pending)
        pending.clear()
        return messages

    async def save_checkpoint(value):
        checkpoints.append(copy.deepcopy(value))

    binding = NodeExecutionBinding(
        run_id="run",
        team_id="team",
        member_id="member",
        node_id=None,
        umo="test:FriendMessage:late",
        owner_username="test",
        config_id=None,
        pending_messages=pending_messages,
        save_checkpoint=save_checkpoint,
    )
    token = current_team_execution.set(binding)
    try:
        provider = BudgetProvider(
            [LLMResponse(role="assistant", completion_text="done")],
            max_context_tokens=1000,
        )
        runner, executor = await make_runner(provider, streaming=streaming)
        runner.req.session_id = f"late-member-{streaming}"
        events = [event async for event in runner.step()]
    finally:
        current_team_execution.reset(token)

    sent = provider.requests[0]
    assert len(provider.requests) == 1 and executor.calls == []
    assert any(event.type == "context_compression" for event in events)
    assert not any(
        "old history" in str(message.content) for message in sent["contexts"]
    )
    assert sum("x" * 1800 in str(message.content) for message in sent["contexts"]) == 1
    saved = next(item for item in reversed(checkpoints) if item["phase"] == "request")
    position = saved["member_messages"][0]["context_position"]
    assert "Team member message" in str(saved["messages"][position]["content"])
    assert fingerprint_messages(
        bind_checkpoint_messages(saved["input_contexts"])
    ) == fingerprint_messages(sent["contexts"])
    record = get_session_request(runner.req.session_id)
    assert record["msg_hashes"] == fingerprint_messages(sent["contexts"])
    assert record["tools"] == fingerprint_tools(sent["func_tool"])


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "change", ["prefix", "system", "schema", "extra", "output", "window"]
)
async def test_changes_during_admission_reach_the_final_budget_check(streaming, change):
    async def begin_usage(request):
        if change == "prefix":
            runner.run_context.messages[1].content = "中" * 1200
        elif change == "system":
            runner.run_context.messages[0].content = "s" * 2500
        elif change == "schema":
            runner.req.func_tool.tools[0].description = "d" * 2500
        elif change == "extra":
            runner.req.extra_user_content_parts.append(TextPart(text="late " * 400))
        elif change == "window":
            provider.provider_config["max_context_tokens"] = 400
        else:
            runner.req.llm_params = {"max_completion_tokens": 700}
        return {
            "receipt_id": request["receipt_id"],
            "max_output_tokens": 700 if change == "output" else 1,
        }

    binding = NodeExecutionBinding(
        run_id="run",
        team_id="team",
        member_id="member",
        node_id=None,
        umo="test:FriendMessage:mutation",
        owner_username="test",
        config_id=None,
        begin_usage=begin_usage,
    )
    token = current_team_execution.set(binding)
    try:
        provider = BudgetProvider(
            [LLMResponse(role="assistant", completion_text="done")],
            max_context_tokens=1000,
        )
        runner, executor = await make_runner(provider, streaming=streaming)
        events = [event async for event in runner.step()]
    finally:
        current_team_execution.reset(token)
    assert len(provider.requests) == 1 and executor.calls == []
    assert any(event.type == "context_compression" for event in events)
    sent = provider.requests[0]
    assert not any(
        "old history" in str(message.content) for message in sent["contexts"]
    )
    if change == "schema":
        assert sent["func_tool"].tools[0].description == "d" * 2500
    elif change == "extra":
        assert (
            sum(
                str(message.content).count("late " * 400)
                for message in sent["contexts"]
            )
            == 1
        )
    elif change == "output":
        assert sent["llm_params"]["max_completion_tokens"] == 700


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("cap", [100, 400])
async def test_admission_changes_are_checked_before_checkpoint_and_delivery(
    streaming, cap
):
    checkpoints = []

    async def save_checkpoint(value):
        checkpoints.append(copy.deepcopy(value))

    async def begin_usage(request):
        # This callback runs after the previous request view was assembled.
        runner.run_context.messages[-1].content = "x" * 2500
        return {"receipt_id": request["receipt_id"], "max_output_tokens": cap}

    binding = NodeExecutionBinding(
        run_id="run",
        team_id="team",
        member_id="member",
        node_id=None,
        umo="test:FriendMessage:cap",
        owner_username="test",
        config_id=None,
        save_checkpoint=save_checkpoint,
        begin_usage=begin_usage,
    )
    token = current_team_execution.set(binding)
    try:
        provider = BudgetProvider(
            [LLMResponse(role="assistant", completion_text="done")],
            max_context_tokens=1000,
        )
        runner, executor = await make_runner(
            provider,
            streaming=streaming,
            request=ProviderRequest(
                prompt="small", llm_params={"max_completion_tokens": 400}
            ),
        )
        runner.req.session_id = f"admission-{streaming}-{cap}"
        if cap == 400:
            with pytest.raises(ContextCapacityError):
                async for _ in runner.step():
                    pass
            assert provider.requests == []
            assert get_session_request(runner.req.session_id) is None
        else:
            async for _ in runner.step():
                pass
            sent = provider.requests[0]
            assert "x" * 2500 in str(sent["contexts"][-1].content)
            saved = next(
                item for item in reversed(checkpoints) if item["phase"] == "request"
            )
            assert (
                saved["llm_params"]
                == sent["llm_params"]
                == {"max_completion_tokens": 100}
            )
            assert fingerprint_messages(
                bind_checkpoint_messages(saved["input_contexts"])
            ) == fingerprint_messages(sent["contexts"])
        assert executor.calls == []
    finally:
        current_team_execution.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_late_extra_content_is_visible_once_and_rechecked_on_retry(streaming):
    provider = BudgetProvider(
        [
            tool_response(usage=600),
            EmptyModelOutputError("retry"),
            LLMResponse(role="assistant", completion_text="done"),
        ],
        max_context_tokens=1000,
    )
    runner, executor = await make_runner(provider, streaming=streaming)
    runner.EMPTY_OUTPUT_RETRY_WAIT_MIN_S = runner.EMPTY_OUTPUT_RETRY_WAIT_MAX_S = 0
    async for _ in runner.step():
        pass
    runner.req.extra_user_content_parts.append(TextPart(text="late extra " * 180))
    events = [event async for event in runner.step()]
    assert len(provider.requests) == 3 and len(executor.calls) == 1
    assert sum(event.type == "context_compression" for event in events) == 1
    for sent in provider.requests[1:]:
        assert (
            sum(
                str(message.content).count("late extra " * 180)
                for message in sent["contexts"]
            )
            == 1
        )
        assert not any(
            "old history" in str(message.content) for message in sent["contexts"]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("late", [False, True])
async def test_temporary_extra_content_does_not_create_repeated_user_turns(
    streaming, late
):
    provider = BudgetProvider(
        [
            tool_response(),
            tool_response("two"),
            LLMResponse(role="assistant", completion_text="done"),
        ],
        max_context_tokens=1000,
    )
    extra = TextPart(text="temporary framing").mark_as_temp()
    runner, executor = await make_runner(
        provider,
        streaming=streaming,
        request=ProviderRequest(
            prompt="task",
            extra_user_content_parts=[] if late else [extra],
            func_tool=ToolSet(
                tools=[FunctionTool(name="read", description="Read", parameters={})]
            ),
        ),
    )
    async for _ in runner.step():
        pass
    if late:
        runner.req.extra_user_content_parts.append(extra)
    async for _ in runner.step_until_done(3):
        pass
    assert len(provider.requests) == 3 and len(executor.calls) == 2
    for request in provider.requests[1:]:
        assert (
            sum(
                str(message.content).count("temporary framing")
                for message in request["contexts"]
            )
            == 1
        )
    assert provider.requests[-1]["contexts"][-1].role == "tool"


class ChunkUsageProvider(BudgetProvider):
    """Expose misleading chunk usage before each complete scripted response."""

    async def text_chat_stream(self, **kwargs):
        yield LLMResponse(
            role="assistant",
            is_chunk=True,
            completion_text="partial",
            usage=TokenUsage(input_other=100000),
        )
        response = await self.text_chat(**kwargs)
        yield response


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_missing_final_usage_discards_previous_and_chunk_usage(streaming):
    provider = ChunkUsageProvider(
        [
            tool_response(usage=600),
            tool_response("two", usage=None),
            LLMResponse(role="assistant", completion_text="done"),
        ],
        max_context_tokens=1000,
    )
    runner, executor = await make_runner(provider, streaming=streaming)
    executor.text = "x" * 350
    events = [event async for event in runner.step_until_done(4)]
    assert len(provider.requests) == 3 and len(executor.calls) == 2
    assert not any(event.type == "context_compression" for event in events)
    assert any(
        "old history" in str(message.content)
        for message in provider.requests[-1]["contexts"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_final_step_prompt_participates_in_the_budget(streaming):
    provider = BudgetProvider(
        [
            tool_response(usage=750),
            LLMResponse(role="assistant", completion_text="done"),
        ],
        max_context_tokens=1000,
    )
    runner, executor = await make_runner(provider, streaming=streaming)
    events = [event async for event in runner.step_until_done(1)]
    assert len(provider.requests) == 2 and len(executor.calls) == 1
    sent = provider.requests[-1]
    assert any(
        message.content == runner.MAX_STEPS_REACHED_PROMPT
        for message in sent["contexts"]
    )
    assert any(event.type == "context_compression" for event in events)
    assert not any(
        "old history" in str(message.content) for message in sent["contexts"]
    )
