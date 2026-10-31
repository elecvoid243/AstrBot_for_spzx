"""Verify paired, capacity-checked deliveries across kernel compression."""

import copy

import pytest
from mcp.types import CallToolResult, TextContent

from astrbot.core.agent.context.compressor import (
    LLMSummaryCompressor,
    TruncateByTurnsCompressor,
)
from astrbot.core.agent.context.config import ContextConfig
from astrbot.core.agent.context.manager import ContextManager, ContextUsageSnapshot
from astrbot.core.agent.context.truncator import ContextTruncator
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.message import (
    ImageURLPart,
    Message,
    TextPart,
    bind_checkpoint_messages,
)
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.agent_team_execution import (
    NodeExecutionBinding,
    current_team_execution,
)
from astrbot.core.exceptions import ContextCapacityError, ToolProtocolIntegrityError
from astrbot.core.provider.entities import LLMResponse, ProviderRequest, TokenUsage
from tests.agent.test_truncator import pairing_batch, pairing_result
from tests.test_tool_loop_agent_runner import PairingToolExecutor
from tests.test_tool_loop_context_budget import (
    BudgetProvider,
    BudgetToolExecutor,
    CharacterTokenCounter,
)


def assert_paired(contexts):
    """Check complete protocol groups in an actual delivered request.

    Args:
        contexts: Messages recorded by a fake provider.
    """
    messages = [Message.model_validate(message) for message in contexts]
    pending = set()
    for message in messages:
        if message.role == "tool":
            assert message.tool_call_id in pending
            pending.remove(message.tool_call_id)
        else:
            assert not pending
            if message.tool_calls:
                pending = {call.id for call in message.tool_calls}
    assert not pending


async def compression_runner(
    provider, *, request=None, executor=None, streaming=False, **kwargs
):
    """Initialize the real runner for combined compression scenarios.

    Args:
        provider: Recording main provider.
        request: Optional provider request.
        executor: Optional recording tool executor.
        streaming: Select the main streaming delivery interface.
        **kwargs: Existing runner reset options.

    Returns:
        Runner and recording executor.
    """
    runner = ToolLoopAgentRunner()
    executor = executor or BudgetToolExecutor("unused")
    await runner.reset(
        provider=provider,
        request=request or ProviderRequest(prompt="task"),
        run_context=ContextWrapper(context=None),
        tool_executor=executor,
        agent_hooks=BaseAgentRunHooks(),
        streaming=streaming,
        **kwargs,
    )
    return runner, executor


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("summary_fails", [False, True])
async def test_partial_batch_crosses_budget_with_paired_summary_and_main_requests(
    streaming, summary_fails
):
    main = BudgetProvider(
        [
            LLMResponse(
                role="assistant",
                tools_call_name=["tool_a", "tool_b"],
                tools_call_ids=["A", "B"],
                tools_call_args=[{}, {}],
                usage=TokenUsage(input_other=600),
            ),
            LLMResponse(
                role="assistant",
                tools_call_name=["tool_c"],
                tools_call_ids=["C"],
                tools_call_args=[{}],
                usage=TokenUsage(input_other=50),
            ),
            LLMResponse(role="assistant", completion_text="done"),
        ],
        max_context_tokens=1000,
    )
    summary = BudgetProvider(
        [
            RuntimeError("summary unavailable")
            if summary_fails
            else LLMResponse(
                role="assistant",
                completion_text="short summary",
                usage=TokenUsage(input_other=900000),
            )
        ],
        max_context_tokens=12000,
    )
    executor = PairingToolExecutor(
        {
            "tool_a": [
                CallToolResult(content=[TextContent(type="text", text="a" * 1500)])
            ],
            "tool_b": [],
            "tool_c": [
                CallToolResult(content=[TextContent(type="text", text="small")])
            ],
        }
    )
    runner, _ = await compression_runner(
        main,
        streaming=streaming,
        executor=executor,
        llm_compress_provider=summary,
        llm_compress_keep_recent_ratio=0,
        request=ProviderRequest(
            prompt="task",
            contexts=[
                {"role": "user", "content": "old " * 150},
                {"role": "assistant", "content": "old " * 150},
            ],
            func_tool=ToolSet(
                tools=[
                    FunctionTool(name=name, description=name, parameters={})
                    for name in ("tool_a", "tool_b", "tool_c")
                ]
            ),
            llm_params={"max_tokens": 10},
        ),
    )
    async for _ in runner.step():
        pass
    orphan = pairing_result("orphan", "orphan execution evidence")
    runner.run_context.messages.append(orphan)
    events = [event async for event in runner.step_until_done(3)]

    assert len(main.requests) == 3
    assert len(summary.requests) == 1
    assert executor.calls == ["tool_a", "tool_b", "tool_c"]
    assert any(event.type == "context_compression" for event in events)
    for request in [*main.requests, *summary.requests]:
        assert_paired(request["contexts"])
        assert "orphan execution evidence" not in str(request["contexts"])
    results = {
        m.tool_call_id: m.content
        for m in bind_checkpoint_messages(summary.requests[0]["contexts"])
        if m.role == "tool"
    }
    assert results["A"] == "a" * 1500
    assert "execution status is unknown" in results["B"]
    assert orphan.content == "orphan execution evidence"
    if summary_fails:
        assert any(
            Message.model_validate(m).tool_call_id == "B"
            for m in main.requests[1]["contexts"]
        )
    else:
        assert "short summary" in str(main.requests[1]["contexts"])


class ReplacementCompressor(TruncateByTurnsCompressor):
    """Return a scripted replacement through the existing compressor contract."""

    def __init__(self, replacement):
        super().__init__()
        self.replacement = replacement

    async def __call__(self, messages, func_tool=None):
        return self.replacement


@pytest.mark.asyncio
@pytest.mark.parametrize("overhead", ["system", "schema", "output"])
async def test_replacement_capacity_includes_system_schema_and_final_output(overhead):
    replacement = [
        Message(role="system", content="s" * (400 if overhead == "system" else 6)),
        Message(role="user", content="u" * (201 if overhead == "system" else 400)),
    ]
    tools = (
        ToolSet(
            tools=[FunctionTool(name="probe", description="x" * 600, parameters={})]
        )
        if overhead == "schema"
        else None
    )
    main = BudgetProvider([], max_context_tokens=600)
    runner, executor = await compression_runner(
        main,
        custom_compressor=ReplacementCompressor(replacement),
        custom_token_counter=CharacterTokenCounter(),
        request=ProviderRequest(
            prompt="task",
            contexts=[
                {"role": "user", "content": "old" * 400},
                {"role": "assistant", "content": "old" * 400},
            ],
            func_tool=tools,
            llm_params={"max_completion_tokens": 200 if overhead == "output" else 10},
        ),
    )
    with pytest.raises(ContextCapacityError):
        async for _ in runner.step():
            pass
    assert main.requests == []
    assert executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replacement_kind", ["partial", "oversized", "conflict", "media"]
)
async def test_replacement_is_adapted_paired_and_recounted_before_delivery(
    replacement_kind,
):
    replacement = [
        Message(role="user", content="task"),
        pairing_batch("A", "B"),
        pairing_result("A", "a" * (500 if replacement_kind == "oversized" else 10)),
    ]
    if replacement_kind == "conflict":
        replacement.append(pairing_result("A", "different"))
    if replacement_kind == "media":
        replacement[-1].content = [
            TextPart(text="evidence"),
            ImageURLPart(
                image_url=ImageURLPart.ImageURL(url="data:image/png;base64,dGVzdA==")
            ),
        ]
    original = copy.deepcopy(replacement)
    main = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")],
        max_context_tokens=600,
        modalities=["text", "tool_use"],
    )
    runner, executor = await compression_runner(
        main,
        custom_compressor=ReplacementCompressor(replacement),
        custom_token_counter=CharacterTokenCounter(),
        request=ProviderRequest(
            prompt="current",
            contexts=[
                {"role": "user", "content": "x" * 400},
                {"role": "assistant", "content": "y" * 400},
            ],
            system_prompt="system",
            llm_params={"max_tokens": 10},
        ),
    )
    if replacement_kind in {"oversized", "conflict"}:
        with pytest.raises(
            ContextCapacityError
            if replacement_kind == "oversized"
            else ToolProtocolIntegrityError
        ):
            async for _ in runner.step():
                pass
        assert main.requests == []
    else:
        async for _ in runner.step():
            pass
        assert len(main.requests) == 1
        sent = [Message.model_validate(m) for m in main.requests[0]["contexts"]]
        assert_paired(sent)
        assert [m.tool_call_id for m in sent if m.role == "tool"] == ["A", "B"]
        if replacement_kind == "media":
            assert "[Image]" in str(sent)
            assert "image_url" not in str([m.model_dump() for m in sent])
    assert executor.calls == []
    assert replacement == original


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "oversized-summary"])
async def test_failed_summary_and_truncation_cannot_send_oversized_latest_round(
    failure,
):
    summary = BudgetProvider(
        [
            RuntimeError("unavailable")
            if failure == "exception"
            else LLMResponse(role="assistant", completion_text="s" * 5000)
        ],
        max_context_tokens=12000,
    )
    main = BudgetProvider([], max_context_tokens=600)
    runner, executor = await compression_runner(
        main,
        llm_compress_provider=summary,
        llm_compress_keep_recent_ratio=0,
        request=ProviderRequest(
            prompt="z" * 3000,
            contexts=[
                {"role": "user", "content": "old" * 500},
                {"role": "assistant", "content": "old" * 500},
            ],
            llm_params={"max_tokens": 10},
        ),
    )
    with pytest.raises(ContextCapacityError, match="Context capacity"):
        async for _ in runner.step():
            pass
    assert len(summary.requests) == 1
    assert_paired(summary.requests[0]["contexts"])
    assert main.requests == []
    assert executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limited_by",
    [
        "input",
        "schema",
        "max_tokens",
        "max_completion_tokens",
        "configured-output",
        "headroom",
        "admission",
    ],
)
async def test_summary_uses_its_own_capacity_without_recursive_compression(limited_by):
    summary = BudgetProvider([], max_context_tokens=2000)
    tools = None
    params = {"max_tokens": 10}
    if limited_by == "input":
        summary.provider_config["max_context_tokens"] = 50
    elif limited_by == "schema":
        tools = ToolSet(
            tools=[FunctionTool(name="large", description="x" * 6000, parameters={})]
        )
    elif limited_by == "configured-output":
        summary.provider_config["custom_extra_body"] = {"max_tokens": 1990}
    elif limited_by == "headroom":
        params = {}
        summary.provider_config["max_context_tokens"] = 1500
    elif limited_by == "admission":
        params = {"max_completion_tokens": 10}
    else:
        params = {limited_by: 1990}
    main = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")],
        max_context_tokens=10000,
        compress_threshold_tokens=800,
    )
    runner, executor = await compression_runner(
        main,
        llm_compress_provider=summary,
        request=ProviderRequest(
            prompt="task",
            contexts=[
                {"role": "user", "content": "old " * 400},
                {"role": "assistant", "content": "old " * 400},
            ],
            func_tool=tools,
            llm_params=params,
        ),
    )
    reports = []

    async def reserve(request):
        """Return the effective execution allowance for a delivery."""
        return {
            **request,
            "max_output_tokens": 1990 if request.get("name") == "summary" else 10,
        }

    async def report(receipt_id, usage):
        """Record the accounting outcome of each admitted delivery."""
        reports.append(usage)

    summary.provider_config["id"] = "summary"
    binding = (
        current_team_execution.set(
            NodeExecutionBinding(
                run_id="r",
                team_id="t",
                member_id="m",
                node_id=None,
                umo="test",
                owner_username="test",
                config_id=None,
                begin_usage=reserve,
                report_usage=report,
            )
        )
        if limited_by == "admission"
        else None
    )
    try:
        async for _ in runner.step():
            pass
    finally:
        if binding is not None:
            current_team_execution.reset(binding)
    assert summary.requests == []
    assert len(main.requests) == 1
    assert executor.calls == []
    if limited_by == "admission":
        assert {} in reports


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_final_wrap_up_uses_paired_summary_and_keeps_the_actual_prompt(streaming):
    main = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")], max_context_tokens=1000
    )
    summary = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="summary")],
        max_context_tokens=12000,
    )
    runner, executor = await compression_runner(
        main,
        streaming=streaming,
        llm_compress_provider=summary,
        llm_compress_keep_recent_ratio=0,
        request=ProviderRequest(prompt="task", llm_params={"max_tokens": 10}),
    )
    runner.run_context.messages = [
        Message(role="user", content="x" * 3000),
        pairing_batch("A", "B"),
        pairing_result("A", "real A"),
    ]
    async for _ in runner.step_until_done(0):
        pass
    assert len(main.requests) == len(summary.requests) == 1
    assert_paired(main.requests[0]["contexts"])
    assert_paired(summary.requests[0]["contexts"])
    assert runner.MAX_STEPS_REACHED_PROMPT in str(main.requests[0]["contexts"])
    assert "execution status is unknown" in str(summary.requests[0]["contexts"])
    assert executor.calls == []


@pytest.mark.asyncio
async def test_recent_legal_media_round_survives_summary_unchanged():
    main = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")], max_context_tokens=2000
    )
    summary = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="summary")],
        max_context_tokens=12000,
    )
    media = pairing_result(
        "A",
        [
            TextPart(text="visual evidence"),
            ImageURLPart(
                image_url=ImageURLPart.ImageURL(url="data:image/png;base64,dGVzdA==")
            ),
        ],
    )
    runner, executor = await compression_runner(
        main,
        llm_compress_provider=summary,
        llm_compress_keep_recent_ratio=0.3,
        request=ProviderRequest(prompt="task"),
    )
    runner.run_context.messages = [
        Message(role="user", content="old " * 1250),
        Message(role="assistant", content="old answer"),
        Message(role="user", content="current"),
        pairing_batch("A"),
        media,
    ]
    original = media.model_dump()
    async for _ in runner.step():
        pass
    assert len(main.requests) == len(summary.requests) == 1
    sent = [Message.model_validate(m) for m in main.requests[0]["contexts"]]
    assert_paired(sent)
    assert next(m for m in sent if m.role == "tool").model_dump() == original
    assert executor.calls == []


@pytest.mark.parametrize(
    "truncate",
    [
        "truncate_by_turns",
        "truncate_by_dropping_oldest_turns",
        "truncate_by_token_budget",
    ],
)
def test_public_truncation_retains_repaired_batch_and_idempotence(truncate):
    messages = [
        Message(role="user", content="old"),
        Message(role="assistant", content="answer"),
        Message(role="user", content="current"),
        pairing_batch("A", "B"),
        pairing_result("A", "real A"),
    ]
    truncator = ContextTruncator()
    if truncate == "truncate_by_turns":
        result = truncator.truncate_by_turns(messages, keep_most_recent_turns=1)
    elif truncate == "truncate_by_dropping_oldest_turns":
        result = truncator.truncate_by_dropping_oldest_turns(messages)
    else:
        result = truncator.truncate_by_token_budget(
            messages,
            budget_tokens=300,
            token_counter=CharacterTokenCounter(),
            min_drop_turns=1,
        )
    assert_paired(result)
    assert [m.tool_call_id for m in result if m.role == "tool"] == ["A", "B"]
    assert truncator.fix_messages(result) == result
    assert len(messages) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_direct_kernel_compressor_inherits_pairing_without_mutating_history(
    invalid,
):
    summary = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="summary")],
        max_context_tokens=12000,
    )
    history = [
        Message(role="user", content="task"),
        pairing_result("orphan", "raw record"),
        pairing_batch("A", "B"),
        pairing_result("A", "real A"),
    ]
    if invalid:
        history.append(pairing_result("A", "conflict"))
    original = copy.deepcopy(history)
    compressor = LLMSummaryCompressor(summary, keep_recent_ratio=0)
    if invalid:
        with pytest.raises(ToolProtocolIntegrityError):
            await compressor(history)
        assert summary.requests == []
    else:
        await compressor(history)
        assert_paired(summary.requests[0]["contexts"])
        assert "raw record" not in str(summary.requests[0]["contexts"])
        assert "execution status is unknown" in str(summary.requests[0]["contexts"])
    assert history == original


@pytest.mark.asyncio
async def test_repaired_prefix_invalidates_usage_and_counts_placeholder():
    provider = BudgetProvider([])
    history = [
        Message(role="user", content="task"),
        pairing_batch("A", "B"),
        pairing_result("A", "a" * 400),
        Message(role="user", content="latest"),
    ]
    snapshot = ContextUsageSnapshot(
        provider=provider,
        model=provider.get_model(),
        messages=[m.model_dump(mode="json") for m in history],
        tool_schema=[],
        input_tokens=1,
    )
    manager = ContextManager(
        ContextConfig(
            max_context_tokens=500, custom_token_counter=CharacterTokenCounter()
        )
    )
    result = await manager.process(history, usage_snapshot=snapshot)
    assert result == [history[-1]]
    assert history[2].content == "a" * 400


@pytest.mark.asyncio
@pytest.mark.parametrize("media_supported", [False, True])
async def test_summary_and_final_request_keep_media_only_when_supported(
    media_supported,
):
    main = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")], max_context_tokens=2000
    )
    summary = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="summary")],
        max_context_tokens=12000,
        modalities=["text", "tool_use", *(["image"] if media_supported else [])],
    )
    history = [
        Message(role="user", content="old " * 1000),
        pairing_batch("A"),
        pairing_result(
            "A",
            [
                TextPart(text="visual evidence"),
                ImageURLPart(
                    image_url=ImageURLPart.ImageURL(
                        url="data:image/png;base64,dGVzdA=="
                    )
                ),
            ],
        ),
    ]
    runner, executor = await compression_runner(
        main,
        llm_compress_provider=summary,
        llm_compress_keep_recent_ratio=0,
        request=ProviderRequest(prompt="task"),
    )
    runner.run_context.messages = history
    async for _ in runner.step():
        pass
    assert len(summary.requests) == len(main.requests) == 1
    sent = bind_checkpoint_messages(summary.requests[0]["contexts"])
    assert_paired(sent)
    assert (
        any(
            isinstance(part, ImageURLPart)
            for m in sent
            if isinstance(m.content, list)
            for part in m.content
        )
        is media_supported
    )
    assert isinstance(history[-1].content[-1], ImageURLPart)
    assert executor.calls == []
