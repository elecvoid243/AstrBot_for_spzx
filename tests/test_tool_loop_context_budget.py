"""Exercise context budgets through actual tool-loop provider requests."""

import copy
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, TextContent

from astrbot.core.agent.context.compressor import TruncateByTurnsCompressor
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.message import ImageURLPart, TextPart
from astrbot.core.agent.notes_store import write_note
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.agent_team_execution import (
    NodeExecutionBinding,
    current_team_execution,
)
from astrbot.core.exceptions import AstrBotError, EmptyModelOutputError
from astrbot.core.provider.entities import LLMResponse, ProviderRequest, TokenUsage
from astrbot.core.provider.provider import Provider


class BudgetProvider(Provider):
    """Record delivered requests and return a finite script of responses."""

    def __init__(self, responses, **config):
        super().__init__(config, {})
        self.responses = iter(responses)
        self.requests = []
        self.set_model("budget-model")

    def get_current_key(self):
        return "unused"

    def set_key(self, key):
        pass

    async def get_models(self):
        return [self.get_model()]

    async def text_chat(self, **kwargs):
        self.requests.append(
            {
                **kwargs,
                "contexts": copy.deepcopy(kwargs["contexts"]),
                "func_tool": copy.deepcopy(kwargs.get("func_tool")),
                "llm_params": copy.deepcopy(kwargs.get("llm_params")),
            }
        )
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    async def text_chat_stream(self, **kwargs):
        response = await self.text_chat(**kwargs)
        yield response


class BudgetToolExecutor:
    """Return configured tool output while recording real executions."""

    def __init__(self, text):
        self.text = text
        self.calls = []

    async def execute(self, tool, run_context, **kwargs):
        self.calls.append((tool.name, kwargs))
        yield CallToolResult(content=[TextContent(type="text", text=self.text)])


async def _reset_budget_runner(provider, contexts, **kwargs):
    """Build a runner over a real provider, with history assembled by reset."""
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=ProviderRequest(
            prompt="current task",
            contexts=contexts,
            conversation=SimpleNamespace(cid="budget", token_usage=0),
            func_tool=None,
            llm_params={"max_tokens": 1},
        ),
        run_context=ContextWrapper(context=None),
        tool_executor=BudgetToolExecutor(""),
        agent_hooks=BaseAgentRunHooks(),
        custom_token_counter=CharacterTokenCounter(),
        **kwargs,
    )
    return runner


def _persisted(messages, window_state):
    """Mirror the save path: drop control text, stamp the window, then dump."""
    kept = [message for message in messages if not message.internal]
    kept[-1] = kept[-1].model_copy(update={"window": window_state.model_copy()})
    return [message.model_dump_for_context() for message in kept]


class CharacterTokenCounter:
    """Give text-only fixtures exact thresholds without a real tokenizer."""

    def count_tokens(self, messages, trusted_token_usage=0):
        return sum(
            len(message.content)
            if isinstance(message.content, str)
            else sum(
                len(part.text)
                for part in message.content or []
                if isinstance(part, TextPart)
            )
            for message in messages
        )


class LegacyCompressor(TruncateByTurnsCompressor):
    """Retain the original single-argument custom compressor contract."""

    def __init__(self, fail=False):
        super().__init__()
        self.fail = fail

    async def __call__(self, messages):
        if self.fail:
            raise RuntimeError("Compression unavailable")
        return await super().__call__(messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "result_chars,tool_count,usage,reasoning,exact_counter,compress",
    [
        (100, 1, TokenUsage(input_other=600), None, False, False),
        (900, 1, TokenUsage(input_other=600), None, False, True),
        (300, 2, TokenUsage(input_other=600), None, False, True),
        (300, 1, TokenUsage(input_other=50, input_cached=700), None, False, True),
        (100, 1, TokenUsage(input_other=100, output=100000), None, False, False),
        (100, 1, TokenUsage(input_other=600), "r" * 900, False, True),
        (2200, 1, None, None, False, True),
        (220, 1, TokenUsage(input_other=600), None, True, False),
        (221, 1, TokenUsage(input_other=600), None, True, True),
    ],
)
async def test_new_tool_result_triggers_compression_before_next_delivery(
    streaming, result_chars, tool_count, usage, reasoning, exact_counter, compress
):
    provider = BudgetProvider(
        [
            LLMResponse(
                role="assistant",
                tools_call_name=["read"] * tool_count,
                tools_call_ids=[f"read-{i}" for i in range(tool_count)],
                tools_call_args=[{}] * tool_count,
                usage=usage,
                reasoning_content=reasoning,
            ),
            LLMResponse(role="assistant", completion_text="done"),
        ],
        max_context_tokens=1000,
    )
    provider.provider_config["compress_threshold_ratio"] = 0.82
    provider.provider_config["compaction_headroom_tokens"] = 0
    executor = BudgetToolExecutor("x" * result_chars)
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=ProviderRequest(
            prompt="current task",
            contexts=[
                {"role": "user", "content": "old history " * 20},
                {"role": "assistant", "content": "old answer " * 20},
            ],
            conversation=SimpleNamespace(cid="budget", token_usage=0),
            func_tool=ToolSet(
                tools=[FunctionTool(name="read", description="Read", parameters={})]
            ),
            llm_params={"max_tokens": 1},
        ),
        run_context=ContextWrapper(context=None),
        tool_executor=executor,
        agent_hooks=BaseAgentRunHooks(),
        streaming=streaming,
        custom_token_counter=CharacterTokenCounter() if exact_counter else None,
    )

    events = [event async for event in runner.step_until_done(3)]

    assert len(provider.requests) == 2
    assert len(executor.calls) == tool_count
    second = provider.requests[1]["contexts"]
    assert (
        any("old history" in str(message.content) for message in second) is not compress
    )
    assert sum(message.role == "tool" for message in second) == tool_count
    assert all(
        "x" * result_chars in str(message.content)
        for message in second
        if message.role == "tool"
    )
    assert sum(event.type == "context_compression" for event in events) == int(compress)


@pytest.mark.asyncio
async def test_token_budget_replaces_history_without_a_summary_request():
    provider = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")],
        max_context_tokens=1000,
    )
    provider.provider_config["compress_threshold_ratio"] = 0.5
    provider.provider_config["compaction_headroom_tokens"] = 0
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=ProviderRequest(
            prompt="current task",
            contexts=[
                {"role": "user", "content": "old history " * 60},
                {"role": "assistant", "content": "old answer " * 60},
            ],
            conversation=SimpleNamespace(cid="budget", token_usage=0),
            func_tool=None,
            llm_params={"max_tokens": 1},
        ),
        run_context=ContextWrapper(context=None),
        tool_executor=BudgetToolExecutor(""),
        agent_hooks=BaseAgentRunHooks(),
        context_limit_reached_strategy="token_budget",
        custom_token_counter=CharacterTokenCounter(),
    )

    events = [event async for event in runner.step_until_done(2)]

    delivered = provider.requests[0]["contexts"]
    assert all("old history" not in str(message.content) for message in delivered)
    assert any("current task" in str(message.content) for message in delivered)
    # The report is surfaced to the UI instead of being read off the manager:
    # a later unchanged request clears the manager's copy.
    assert sum(event.type == "context_compression" for event in events) == 1
    assert runner.window_state.seq == 1
    assert all(
        "_window" not in message.model_dump(mode="json") for message in delivered
    )
    # A single request: the reset never asked a model for a summary.
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_reset_resumes_the_persisted_window_state():
    provider = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")],
        max_context_tokens=1000,
    )
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=ProviderRequest(
            prompt="current task",
            contexts=[
                {
                    "role": "user",
                    "content": "hi",
                    "_window": {
                        "id": "w9",
                        "seq": 3,
                        "notice_level": 50,
                        "exhausted_sent": False,
                    },
                },
            ],
            conversation=SimpleNamespace(cid="budget", token_usage=0),
            func_tool=None,
            llm_params={"max_tokens": 1},
        ),
        run_context=ContextWrapper(context=None),
        tool_executor=BudgetToolExecutor(""),
        agent_hooks=BaseAgentRunHooks(),
        context_limit_reached_strategy="token_budget",
        custom_token_counter=CharacterTokenCounter(),
    )

    assert runner.window_state.seq == 3
    assert runner.window_state.notice_level == 50


@pytest.mark.asyncio
async def test_window_state_survives_a_saved_history_round_trip():
    provider = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")],
        max_context_tokens=1000,
    )
    provider.provider_config["compress_threshold_ratio"] = 0.5
    provider.provider_config["compaction_headroom_tokens"] = 0
    runner = await _reset_budget_runner(
        provider,
        [
            {"role": "user", "content": "old history " * 60},
            {"role": "assistant", "content": "old answer " * 60},
        ],
        context_limit_reached_strategy="token_budget",
    )
    _ = [event async for event in runner.step_until_done(2)]
    assert runner.window_state.seq == 1
    persisted = _persisted(runner.run_context.messages, runner.window_state)
    assert persisted[-1]["_window"]["seq"] == 1

    provider_second = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")],
        max_context_tokens=1000,
    )
    restored = await _reset_budget_runner(
        provider_second,
        persisted,
        context_limit_reached_strategy="token_budget",
    )

    assert restored.window_state.seq == 1
    assert restored.window_state.id == persisted[-1]["_window"]["id"]


@pytest.mark.asyncio
async def test_control_notice_never_enters_the_saved_history():
    provider = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")],
        max_context_tokens=1000,
    )
    provider.provider_config["compress_threshold_ratio"] = 0.5
    provider.provider_config["compaction_headroom_tokens"] = 0
    runner = await _reset_budget_runner(
        provider,
        [
            {"role": "user", "content": "old history " * 60},
            {"role": "assistant", "content": "old answer " * 60},
        ],
        context_limit_reached_strategy="token_budget",
    )
    _ = [event async for event in runner.step_until_done(2)]

    # The window identity rides on the request's system prompt, not on history.
    assert not any(message.internal for message in runner.run_context.messages)
    system = provider.requests[-1]["contexts"][0]
    role = system.get("role") if isinstance(system, dict) else system.role
    content = system.get("content") if isinstance(system, dict) else system.content
    assert role == "system"
    assert "<context_window>" in content
    persisted = _persisted(runner.run_context.messages, runner.window_state)
    assert "<context_window>" not in str(persisted)
    assert "[context budget]" not in str(persisted)
    assert "_internal" not in str(persisted)


@pytest.mark.asyncio
async def test_provider_payload_carries_no_kernel_markers():
    provider = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")],
        max_context_tokens=1000,
    )
    provider.provider_config["compress_threshold_ratio"] = 0.5
    provider.provider_config["compaction_headroom_tokens"] = 0
    runner = await _reset_budget_runner(
        provider,
        [
            {"role": "user", "content": "old history " * 60},
            {"role": "assistant", "content": "old answer " * 60},
        ],
        context_limit_reached_strategy="token_budget",
    )
    _ = [event async for event in runner.step_until_done(2)]

    for request in provider.requests:
        dumps = [message.model_dump(mode="json") for message in request["contexts"]]
        assert all("_window" not in dump for dump in dumps)
        assert all("_internal" not in dump for dump in dumps)


@pytest.mark.asyncio
async def test_token_budget_strategy_reaches_the_request_config():
    provider = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")],
        max_context_tokens=1000,
    )

    runner = await _reset_budget_runner(
        provider,
        [{"role": "user", "content": "hi"}],
        context_limit_reached_strategy="token_budget",
    )

    assert runner.request_context_manager.config.strategy == "token_budget"


@pytest.mark.asyncio
async def test_token_budget_without_a_window_falls_back_to_llm_compression():
    provider = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")],
        max_context_tokens=0,
    )

    runner = await _reset_budget_runner(
        provider,
        [{"role": "user", "content": "hi"}],
        context_limit_reached_strategy="token_budget",
    )

    assert runner.request_context_manager.config.strategy == "llm_compress"


@pytest.mark.asyncio
@pytest.mark.parametrize("compression_fails", [False, True])
async def test_request_that_cannot_fit_is_rejected_without_model_or_tool_execution(
    compression_fails,
):
    provider = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="must not be sent")],
        max_context_tokens=1000,
    )
    executor = BudgetToolExecutor("unused")
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=ProviderRequest(prompt="x" * 4000),
        run_context=ContextWrapper(context=None),
        tool_executor=executor,
        agent_hooks=BaseAgentRunHooks(),
        custom_compressor=LegacyCompressor(fail=True) if compression_fails else None,
    )

    with pytest.raises(AstrBotError, match="Context capacity"):
        async for _ in runner.step():
            pass

    assert provider.requests == []
    assert executor.calls == []


@pytest.mark.asyncio
async def test_schema_overhead_is_counted_when_usage_is_missing():
    provider = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")],
        max_context_tokens=1000,
    )
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=ProviderRequest(
            prompt="current task",
            contexts=[
                {"role": "user", "content": "old history " * 50},
                {"role": "assistant", "content": "old answer " * 50},
            ],
            func_tool=ToolSet(
                tools=[
                    FunctionTool(
                        name="read",
                        description="d" * 1600,
                        parameters={
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "p" * 300}
                            },
                        },
                    )
                ]
            ),
        ),
        run_context=ContextWrapper(context=None),
        tool_executor=BudgetToolExecutor("unused"),
        agent_hooks=BaseAgentRunHooks(),
    )

    events = [event async for event in runner.step()]

    assert len(provider.requests) == 1
    assert not any(
        "old history" in str(message.content)
        for message in provider.requests[0]["contexts"]
    )
    assert any(event.type == "context_compression" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("media", [False, True])
async def test_full_estimation_counts_extra_content_once_and_uses_media_estimates(
    media,
):
    provider = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")],
        max_context_tokens=1000,
    )
    provider.provider_config["compress_threshold_ratio"] = 0.82
    provider.provider_config["compaction_headroom_tokens"] = 0
    part = (
        ImageURLPart(image_url={"url": "data:image/png;base64," + "A" * 100000})
        if media
        else TextPart(text="x" * 2000)
    )
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=ProviderRequest(
            prompt="task",
            extra_user_content_parts=[] if media else [part],
            contexts=[{"role": "user", "content": [part.model_dump()]}]
            if media
            else [],
            conversation=SimpleNamespace(cid="budget", token_usage=1000000),
        ),
        run_context=ContextWrapper(context=None),
        tool_executor=BudgetToolExecutor("unused"),
        agent_hooks=BaseAgentRunHooks(),
    )

    events = [event async for event in runner.step()]

    assert len(provider.requests) == 1
    assert not any(event.type == "context_compression" for event in events)
    assert sum(
        str(message.content).count("x" * 2000)
        for message in provider.requests[0]["contexts"]
    ) == (0 if media else 1)


@pytest.mark.asyncio
async def test_same_length_prefix_edit_invalidates_usage_before_next_step():
    provider = BudgetProvider(
        [
            LLMResponse(
                role="assistant",
                tools_call_name=["read"],
                tools_call_args=[{}],
                tools_call_ids=["one"],
                usage=TokenUsage(input_other=100),
            ),
            LLMResponse(role="assistant", completion_text="done"),
        ],
        max_context_tokens=1000,
    )
    executor = BudgetToolExecutor("small result")
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=ProviderRequest(
            prompt="task",
            contexts=[
                {"role": "user", "content": "a" * 1000},
                {"role": "assistant", "content": "b" * 1000},
            ],
            func_tool=ToolSet(
                tools=[FunctionTool(name="read", description="Read", parameters={})]
            ),
        ),
        run_context=ContextWrapper(context=None),
        tool_executor=executor,
        agent_hooks=BaseAgentRunHooks(),
    )
    async for _ in runner.step():
        pass
    runner.run_context.messages[0].content = "中" * 1000

    events = [event async for event in runner.step()]

    assert len(provider.requests) == 2
    assert not any(
        "中" in str(message.content) for message in provider.requests[1]["contexts"]
    )
    assert sum(event.type == "context_compression" for event in events) == 1
    assert len(executor.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_retry_preserves_the_request_without_recounting_or_reexecuting_tools(
    streaming,
):
    provider = BudgetProvider(
        [
            LLMResponse(
                role="assistant",
                tools_call_name=["read"],
                tools_call_ids=["one"],
                tools_call_args=[{}],
                usage=TokenUsage(input_other=600),
            ),
            EmptyModelOutputError("empty response"),
            LLMResponse(role="assistant", completion_text="done"),
        ],
        max_context_tokens=1000,
    )
    provider.provider_config["compress_threshold_ratio"] = 0.82
    provider.provider_config["compaction_headroom_tokens"] = 0
    executor = BudgetToolExecutor("x" * 220)
    runner = ToolLoopAgentRunner()
    runner.EMPTY_OUTPUT_RETRY_WAIT_MIN_S = 0
    runner.EMPTY_OUTPUT_RETRY_WAIT_MAX_S = 0
    await runner.reset(
        provider=provider,
        request=ProviderRequest(
            prompt="task",
            func_tool=ToolSet(
                tools=[FunctionTool(name="read", description="Read", parameters={})]
            ),
            llm_params={"max_tokens": 1},
        ),
        run_context=ContextWrapper(context=None),
        tool_executor=executor,
        agent_hooks=BaseAgentRunHooks(),
        streaming=streaming,
        custom_token_counter=CharacterTokenCounter(),
    )

    events = [event async for event in runner.step_until_done(3)]

    assert len(provider.requests) == 3
    assert provider.requests[1]["contexts"] == provider.requests[2]["contexts"]
    assert len(executor.calls) == 1
    assert not any(event.type == "context_compression" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_output_reservation_compresses_history_when_it_can_make_room(streaming):
    provider = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")], max_context_tokens=1000
    )
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=ProviderRequest(
            prompt="task",
            contexts=[
                {"role": "user", "content": "old history " * 50},
                {"role": "assistant", "content": "old answer " * 50},
            ],
            llm_params={"max_completion_tokens": 700},
        ),
        run_context=ContextWrapper(context=None),
        tool_executor=BudgetToolExecutor("unused"),
        agent_hooks=BaseAgentRunHooks(),
        streaming=streaming,
    )

    events = [event async for event in runner.step()]

    assert len(provider.requests) == 1
    assert provider.requests[0]["llm_params"]["max_completion_tokens"] == 700
    assert not any(
        "old history" in str(message.content)
        for message in provider.requests[0]["contexts"]
    )
    assert sum(event.type == "context_compression" for event in events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config,compress",
    [
        ({"max_context_tokens": 0}, False),
        ({"max_context_tokens": 0, "compress_threshold_tokens": 500}, True),
    ],
)
async def test_disabled_and_absolute_only_budget_semantics(config, compress):
    provider = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")], **config
    )
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=ProviderRequest(
            prompt="task",
            contexts=[
                {"role": "user", "content": "old history " * 100},
                {"role": "assistant", "content": "old answer " * 100},
                {"role": "user", "content": "old history " * 100},
                {"role": "assistant", "content": "old answer " * 100},
            ],
            llm_params={"max_tokens": 100000},
        ),
        run_context=ContextWrapper(context=None),
        tool_executor=BudgetToolExecutor("unused"),
        agent_hooks=BaseAgentRunHooks(),
    )

    events = [event async for event in runner.step()]

    assert len(provider.requests) == 1
    assert (
        any(
            "old history" in str(message.content)
            for message in provider.requests[0]["contexts"]
        )
        is not compress
    )
    assert sum(event.type == "context_compression" for event in events) == int(compress)


@pytest.mark.asyncio
@pytest.mark.parametrize("admitted_output", [100, 400])
async def test_capacity_uses_the_admitted_output_limit(admitted_output):
    admissions = []

    async def begin_usage(request):
        admissions.append(request)
        return {
            "receipt_id": request["receipt_id"],
            "request": request,
            "max_output_tokens": admitted_output,
        }

    binding = NodeExecutionBinding(
        run_id="run",
        team_id="team",
        member_id="member",
        node_id=None,
        umo="test:FriendMessage:budget",
        owner_username="test",
        config_id=None,
        begin_usage=begin_usage,
    )
    provider = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")], max_context_tokens=1000
    )
    runner = ToolLoopAgentRunner()
    token = current_team_execution.set(binding)
    try:
        await runner.reset(
            provider=provider,
            request=ProviderRequest(prompt="x" * 2500, llm_params={"max_tokens": 400}),
            run_context=ContextWrapper(context=None),
            tool_executor=BudgetToolExecutor("unused"),
            agent_hooks=BaseAgentRunHooks(),
        )
        if admitted_output == 400:
            with pytest.raises(AstrBotError, match="Context capacity"):
                async for _ in runner.step():
                    pass
            assert provider.requests == []
        else:
            events = [event async for event in runner.step()]
            assert len(provider.requests) == 1
            assert provider.requests[0]["llm_params"]["max_tokens"] == 100
            assert not any(event.type == "context_compression" for event in events)
    finally:
        current_team_execution.reset(token)
    assert len(admissions) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("limit_field", ["max_tokens", "max_completion_tokens"])
@pytest.mark.parametrize("native_override", [False, True])
async def test_output_reservation_rejects_an_input_that_otherwise_fits(
    limit_field, native_override
):
    provider = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="must not be sent")],
        max_context_tokens=1000,
        custom_extra_body={limit_field: 400} if native_override else {},
    )
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=ProviderRequest(
            prompt="x" * 2500, llm_params={limit_field: 100 if native_override else 400}
        ),
        run_context=ContextWrapper(context=None),
        tool_executor=BudgetToolExecutor("unused"),
        agent_hooks=BaseAgentRunHooks(),
    )

    with pytest.raises(AstrBotError, match="Context capacity"):
        async for _ in runner.step():
            pass

    assert provider.requests == []


@pytest.mark.asyncio
async def test_final_output_check_does_not_repeat_a_futile_compression():
    provider = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")], max_context_tokens=1000
    )
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=ProviderRequest(prompt="x" * 2850, llm_params={"max_tokens": 1}),
        run_context=ContextWrapper(context=None),
        tool_executor=BudgetToolExecutor("unused"),
        agent_hooks=BaseAgentRunHooks(),
        custom_compressor=LegacyCompressor(),
    )

    events = [event async for event in runner.step()]

    assert len(provider.requests) == 1
    assert sum(event.type == "context_compression" for event in events) == 1


@pytest.mark.asyncio
async def test_usage_baseline_remains_valid_across_multiple_tool_loop_requests():
    provider = BudgetProvider(
        [
            LLMResponse(
                role="assistant",
                tools_call_name=["read"],
                tools_call_ids=["one"],
                tools_call_args=[{}],
                usage=TokenUsage(input_other=100),
            ),
            LLMResponse(
                role="assistant",
                tools_call_name=["read"],
                tools_call_ids=["two"],
                tools_call_args=[{}],
                usage=TokenUsage(input_other=700),
            ),
            LLMResponse(role="assistant", completion_text="done"),
        ],
        max_context_tokens=1000,
    )
    executor = BudgetToolExecutor("x" * 600)
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=ProviderRequest(
            prompt="task",
            contexts=[
                {"role": "user", "content": "old history " * 20},
                {"role": "assistant", "content": "old answer " * 20},
            ],
            func_tool=ToolSet(
                tools=[FunctionTool(name="read", description="Read", parameters={})]
            ),
        ),
        run_context=ContextWrapper(context=None),
        tool_executor=executor,
        agent_hooks=BaseAgentRunHooks(),
    )

    events = [event async for event in runner.step_until_done(4)]

    assert len(provider.requests) == 3
    assert any(
        "old history" in str(message.content)
        for message in provider.requests[1]["contexts"]
    )
    assert not any(
        "old history" in str(message.content)
        for message in provider.requests[2]["contexts"]
    )
    assert sum(event.type == "context_compression" for event in events) == 1
    assert len(executor.calls) == 2


@pytest.mark.asyncio
async def test_note_written_mid_window_leaves_the_system_prompt_alone(
    tmp_path, monkeypatch
):
    """A note tool call must not invalidate the prompt prefix of its window."""
    import astrbot.core.agent.notes_store as notes_store

    monkeypatch.setattr(
        notes_store, "get_astrbot_agent_notes_path", lambda: str(tmp_path)
    )
    provider = BudgetProvider(
        [
            LLMResponse(
                role="assistant",
                tools_call_name=["write_note"],
                tools_call_args=[{}],
                tools_call_ids=["one"],
            ),
            LLMResponse(role="assistant", completion_text="done"),
        ],
        max_context_tokens=1000,
    )

    class NoteWritingExecutor(BudgetToolExecutor):
        async def execute(self, tool, run_context, **kwargs):
            write_note("budget", "plan.md", "a\nb")
            async for result in super().execute(tool, run_context, **kwargs):
                yield result

    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=ProviderRequest(
            prompt="current task",
            session_id="budget",
            contexts=[],
            conversation=SimpleNamespace(cid="budget", token_usage=0),
            func_tool=ToolSet(
                tools=[
                    FunctionTool(
                        name="write_note", description="Write a note", parameters={}
                    )
                ]
            ),
            llm_params={"max_tokens": 1},
        ),
        run_context=ContextWrapper(context=None),
        tool_executor=NoteWritingExecutor("saved"),
        agent_hooks=BaseAgentRunHooks(),
        context_limit_reached_strategy="token_budget",
        custom_token_counter=CharacterTokenCounter(),
    )

    _ = [event async for event in runner.step_until_done(3)]

    assert len(provider.requests) == 2
    first_system = provider.requests[0]["contexts"][0]
    second_system = provider.requests[1]["contexts"][0]
    if not isinstance(first_system, dict):
        first_system = first_system.content
        second_system = second_system.content
    assert "<context_window>" in first_system
    assert first_system == second_system
    assert "Private notes" not in second_system
