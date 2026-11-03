"""Verify skills-like requests through the real runner and context manager."""

import copy
import json

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
from astrbot.core.exceptions import (
    AstrBotError,
    ContextCapacityError,
    EmptyModelOutputError,
    ToolProtocolIntegrityError,
)
from astrbot.core.provider.entities import LLMResponse, ProviderRequest, TokenUsage
from astrbot.core.utils.prefix_diagnostics import (
    fingerprint_messages,
    fingerprint_tools,
    get_session_request,
)
from tests.agent.test_truncator import pairing_batch, pairing_result
from tests.test_tool_loop_compression_integrity import assert_paired
from tests.test_tool_loop_context_budget import (
    BudgetProvider,
    BudgetToolExecutor,
    CharacterTokenCounter,
)


def selected_tool(usage=50):
    """Return a complete selection or parameter response.

    Args:
        usage: Controlled successful input usage.

    Returns:
        Response selecting the existing read tool.
    """
    return LLMResponse(
        role="assistant",
        tools_call_name=["read"],
        tools_call_ids=["read-call"],
        tools_call_args=[{"value": "actual"}],
        usage=TokenUsage(input_other=usage) if usage is not None else None,
    )


async def parameter_runner(provider, *, streaming=False, request=None, **kwargs):
    """Configure the existing runner with recording model/tool boundaries.

    Args:
        provider: Scripted recording provider.
        streaming: Main-loop streaming mode; auxiliary calls remain non-streaming.
        request: Optional request with scenario-specific history or schemas.
        **kwargs: Existing runner reset options.

    Returns:
        Real runner and recording tool executor.
    """
    runner = ToolLoopAgentRunner()
    executor = BudgetToolExecutor("result")
    await runner.reset(
        provider=provider,
        request=request
        or ProviderRequest(
            prompt="task",
            contexts=[
                {"role": "user", "content": "old history " * 20},
                {"role": "assistant", "content": "old answer " * 20},
            ],
            func_tool=ToolSet(
                tools=[
                    FunctionTool(
                        name="read",
                        description="Read",
                        parameters={
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                        },
                    )
                ]
            ),
            llm_params={"max_tokens": 1},
        ),
        run_context=ContextWrapper(context=None),
        tool_executor=executor,
        agent_hooks=BaseAgentRunHooks(),
        tool_schema_mode="skills_like",
        streaming=streaming,
        **kwargs,
    )
    return runner, executor


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize(
    "main_usage,aux_usage,compress",
    [(50, 100000, False), (None, 100000, False), (700, 1, True)],
)
async def test_stage_schemas_and_usage_stay_independent(
    streaming, repair, main_usage, aux_usage, compress
):
    provider = BudgetProvider(
        [selected_tool(main_usage)]
        + (
            [LLMResponse(role="assistant", usage=TokenUsage(input_other=100000))]
            if repair
            else []
        )
        + [
            selected_tool(aux_usage),
            LLMResponse(role="assistant", completion_text="done"),
        ],
        max_context_tokens=1000,
    )
    runner, executor = await parameter_runner(provider, streaming=streaming)
    executor.text = "x" * 900

    events = [event async for event in runner.step_until_done(3)]

    assert len(provider.requests) == 3 + int(repair)
    assert executor.calls == [("read", {"value": "actual"})]
    for request in provider.requests:
        assert_paired(request["contexts"])
    for index, request in enumerate(provider.requests):
        properties = request["func_tool"].tools[0].parameters["properties"]
        assert properties == (
            {}
            if index in (0, len(provider.requests) - 1)
            else {"value": {"type": "string"}}
        )
    assert any(event.type == "context_compression" for event in events) is compress
    assert (
        any(
            "old history" in str(message.content)
            for message in provider.requests[-1]["contexts"]
        )
        is not compress
    )
    assert all(
        "Do not return an empty response" not in str(message.content)
        for message in runner.run_context.messages
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_parameter_schema_cannot_bypass_capacity_or_fallback(streaming):
    provider = BudgetProvider([selected_tool()], max_context_tokens=1000)
    fallback = BudgetProvider([selected_tool()], max_context_tokens=20000)
    tools = ToolSet(
        tools=[
            FunctionTool(
                name="read",
                description="Read",
                parameters={
                    "type": "object",
                    "properties": {
                        "value": {"type": "string", "description": "p" * 6000}
                    },
                },
            )
        ]
    )
    runner, executor = await parameter_runner(
        provider,
        streaming=streaming,
        fallback_providers=[fallback],
        request=ProviderRequest(prompt="task", func_tool=tools),
    )

    with pytest.raises(ContextCapacityError, match="Context capacity"):
        async for _ in runner.step():
            pass

    assert len(provider.requests) == 1
    assert fallback.requests == [] and executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fits", [False, True])
async def test_final_repair_instruction_is_counted_at_the_capacity_boundary(fits):
    tools = ToolSet(
        tools=[
            FunctionTool(
                name="read",
                description="Read",
                parameters={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                },
            )
        ]
    )
    instruction = ToolLoopAgentRunner.SKILLS_LIKE_REQUERY_INSTRUCTION_TEMPLATE.format(
        tool_names="read"
    )
    repair_instruction = (
        instruction + "\n" + ToolLoopAgentRunner.SKILLS_LIKE_REQUERY_REPAIR_INSTRUCTION
    )
    schema_chars = len(
        json.dumps(tools.get_param_only_tool_set().openai_schema(), ensure_ascii=False)
    )
    window = schema_chars + len(repair_instruction) + (1 if fits else -1)
    provider = BudgetProvider(
        [selected_tool(), LLMResponse(role="assistant"), selected_tool()],
        max_context_tokens=window,
        compress_threshold_tokens=window,
    )
    runner, executor = await parameter_runner(
        provider,
        request=ProviderRequest(
            prompt="task", func_tool=tools, llm_params={"max_tokens": 1}
        ),
        custom_token_counter=CharacterTokenCounter(),
    )

    if fits:
        events = [event async for event in runner.step()]
        assert len(provider.requests) == 3
        assert repair_instruction in str(provider.requests[-1]["contexts"][-1].content)
        assert len(executor.calls) == 1
        assert any(event.type == "context_compression" for event in events)
    else:
        with pytest.raises(ContextCapacityError):
            async for _ in runner.step():
                pass
        assert len(provider.requests) == 2 and executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["parameters", "repair"])
@pytest.mark.parametrize("ambiguous", [False, True])
async def test_each_auxiliary_stage_pairs_its_actual_history(stage, ambiguous):
    original = pairing_batch("A", "B")
    result = pairing_result("A", "real result")
    history = [original, result]
    if ambiguous:
        history.append(pairing_result("A", "conflicting result"))

    class InjectingProvider(BudgetProvider):
        async def text_chat(self, **kwargs):
            response = await super().text_chat(**kwargs)
            if len(self.requests) == (1 if stage == "parameters" else 2):
                runner.run_context.messages.extend(copy.deepcopy(history))
            return response

    provider = InjectingProvider(
        [selected_tool()]
        + ([LLMResponse(role="assistant")] if stage == "repair" else [])
        + [selected_tool()],
        max_context_tokens=10000,
    )
    runner, executor = await parameter_runner(provider)
    if ambiguous:
        with pytest.raises(ToolProtocolIntegrityError):
            async for _ in runner.step():
                pass
        assert len(provider.requests) == (1 if stage == "parameters" else 2)
        assert executor.calls == []
    else:
        async for _ in runner.step():
            pass
        request = provider.requests[-1]
        assert_paired(request["contexts"])
        assert any(
            message.role == "tool"
            and message.tool_call_id == "A"
            and "real result" in str(message.content)
            for message in request["contexts"]
        )
        assert any(
            message.role == "tool"
            and message.tool_call_id == "B"
            and "unknown" in str(message.content).lower()
            for message in request["contexts"]
        )
        assert len(executor.calls) == 1
        assert original in runner.run_context.messages
        assert result in runner.run_context.messages
        assert not any(
            message.role == "tool" and message.tool_call_id == "B"
            for message in runner.run_context.messages
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["schema", "window", "output", "system", "extra", "protocol"]
)
async def test_auxiliary_admission_rechecks_final_input_and_output(change):
    reports = []
    checkpoints = []

    async def save_checkpoint(value):
        checkpoints.append(copy.deepcopy(value))

    async def begin_usage(request):
        if len(provider.requests) == 1:
            if change == "schema":
                runner._tool_schema_param_set.tools[0].parameters["description"] = (
                    "s" * 6000
                )
            elif change == "window":
                provider.provider_config["max_context_tokens"] = 1
            elif change == "system":
                runner.run_context.messages.insert(
                    0, Message(role="system", content="s" * 6000)
                )
            elif change == "extra":
                runner.req.extra_user_content_parts.append(
                    TextPart(text="extra " * 1500)
                )
            elif change == "protocol":
                runner.run_context.messages.append(
                    pairing_batch("duplicate", "duplicate")
                )
        return {
            "receipt_id": request["receipt_id"],
            "max_output_tokens": 999
            if change == "output" and len(provider.requests) == 1
            else 1,
        }

    async def report_usage(receipt_id, value):
        reports.append((receipt_id, value))

    binding = NodeExecutionBinding(
        run_id="run",
        team_id="team",
        member_id="member",
        node_id=None,
        umo="test:FriendMessage:parameters",
        owner_username="test",
        config_id=None,
        begin_usage=begin_usage,
        report_usage=report_usage,
        save_checkpoint=save_checkpoint,
    )
    token = current_team_execution.set(binding)
    try:
        provider = BudgetProvider([selected_tool()], max_context_tokens=1000)
        runner, executor = await parameter_runner(provider)
        if change == "output":
            # Admission may tighten this requested cap but must never raise it.
            runner.req.llm_params = {"max_tokens": 999}
        with pytest.raises(
            ToolProtocolIntegrityError if change == "protocol" else ContextCapacityError
        ):
            async for _ in runner.step():
                pass
    finally:
        current_team_execution.reset(token)

    assert len(provider.requests) == 1 and executor.calls == []
    parameter_intents = [item for item in checkpoints if item["stage"] == "parameters"]
    assert len(parameter_intents) == 1 and parameter_intents[0]["phase"] == "request"
    assert len(reports) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["parameters", "repair"])
async def test_explicit_auxiliary_failure_never_executes_the_original_plan(stage):
    provider = BudgetProvider(
        [selected_tool()]
        + ([LLMResponse(role="assistant")] if stage == "repair" else [])
        + [LLMResponse(role="err", completion_text="parameters unavailable")],
    )
    runner, executor = await parameter_runner(provider)

    with pytest.raises(AstrBotError, match="parameters unavailable"):
        async for _ in runner.step():
            pass

    assert len(provider.requests) == (2 if stage == "parameters" else 3)
    assert executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("repair", [False, True])
async def test_parameter_admission_persists_the_exact_final_view_and_invocation(
    streaming, repair,
):
    checkpoints, admissions, observations = [], [], []

    async def record_process(record_id, kind, data):
        observations.append((record_id, kind, copy.deepcopy(data)))

    async def save_checkpoint(value):
        checkpoints.append(copy.deepcopy(value))

    async def begin_usage(request):
        admissions.append(copy.deepcopy(request))
        if len(provider.requests) == 1:
            runner.run_context.messages.insert(
                0, Message(role="system", content="late instruction")
            )
            runner.req.extra_user_content_parts.append(TextPart(text="late extra"))
            runner.req.llm_params = {"max_completion_tokens": 200}
        return {"receipt_id": request["receipt_id"], "max_output_tokens": 20}

    binding = NodeExecutionBinding(
        run_id="run",
        team_id="team",
        member_id="member",
        node_id=None,
        umo="test:FriendMessage:parameters",
        owner_username="test",
        config_id=None,
        begin_usage=begin_usage,
        save_checkpoint=save_checkpoint,
        record_process=record_process,
    )
    token = current_team_execution.set(binding)
    try:
        provider = BudgetProvider(
            [selected_tool()] + ([LLMResponse(role="assistant")] if repair else []) + [selected_tool()], max_context_tokens=20000
        )
        runner, executor = await parameter_runner(provider, streaming=streaming)
        runner._tool_schema_param_set.tools[0].parameters["properties"]["data"] = {
            "type": "string", "description": "Business data"
        }
        runner.req.session_id = f"parameter-checkpoint-{streaming}"
        async for _ in runner.step():
            pass
    finally:
        current_team_execution.reset(token)

    assert len(provider.requests) == 2 + int(repair) and len(executor.calls) == 1
    sent = provider.requests[1]
    contexts = [data for _, kind, data in observations if kind == "model_context"]
    assert [item["stage"] for item in contexts] == ["main", "parameters"] + (["repair"] if repair else [])
    if repair:
        assert contexts[2]["receipt_id"] == admissions[2]["receipt_id"]
        repair_messages = contexts[2]["observation"]["value"]["messages"]
        assert [item["content"] for item in repair_messages] == [message.model_dump(mode="json")["content"] for message in provider.requests[2]["contexts"]]
    parameter_context = contexts[1]
    assert parameter_context["receipt_id"] == admissions[1]["receipt_id"]
    assert parameter_context["invocation_id"] == admissions[1]["invocation_id"]
    recorded_tools = parameter_context["observation"]["value"]["tools"]
    assert recorded_tools == sent["func_tool"].openai_schema()
    assert recorded_tools[0]["function"]["parameters"]["properties"]["data"]["description"] == "Business data"
    recorded = parameter_context["observation"]["value"]["messages"]
    assert [item["content"] for item in recorded] == [message.model_dump(mode="json")["content"] for message in sent["contexts"]]
    assert "late instruction" in str(recorded) and "late extra" in str(recorded)
    requests = [
        item
        for item in checkpoints
        if item["stage"] == "parameters" and item["phase"] == "request"
    ]
    assert len(requests) == 2
    assert (
        requests[0]["invocation_id"]
        == requests[1]["invocation_id"]
        == admissions[1]["invocation_id"]
    )
    assert requests[0]["model_calls"] == requests[1]["model_calls"]
    assert (
        requests[1]["llm_params"] == sent["llm_params"] == {"max_completion_tokens": 20}
    )
    assert fingerprint_messages(
        bind_checkpoint_messages(requests[1]["input_contexts"])
    ) == fingerprint_messages(sent["contexts"])
    record = get_session_request(runner.req.session_id)
    assert record["msg_hashes"] == fingerprint_messages(provider.requests[-1]["contexts"])
    assert record["tools"] == fingerprint_tools(provider.requests[-1]["func_tool"])
    assert (
        sum(str(message.content).count("late extra") for message in sent["contexts"])
        == 1
    )
    assert any(
        "late instruction" in str(message.content) for message in sent["contexts"]
    )
    instruction = ToolLoopAgentRunner.SKILLS_LIKE_REQUERY_INSTRUCTION_TEMPLATE.format(
        tool_names="read"
    )
    assert all(
        instruction not in str(message.content)
        for message in runner.run_context.messages
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("fits", [False, True])
async def test_fallback_selection_uses_its_own_budget_for_parameters(streaming, fits):
    primary = BudgetProvider(
        [RuntimeError("primary unavailable")], id="primary", max_context_tokens=20000
    )
    fallback = BudgetProvider(
        [selected_tool(), selected_tool()],
        id="fallback",
        max_context_tokens=20000 if fits else 1000,
    )
    fallback.set_model("fallback-model")
    tools = ToolSet(
        tools=[
            FunctionTool(
                name="read",
                description="Read",
                parameters={
                    "type": "object",
                    "properties": {
                        "value": {"type": "string", "description": "s" * 6000}
                    },
                },
            )
        ]
    )
    runner, executor = await parameter_runner(
        primary,
        streaming=streaming,
        fallback_providers=[fallback],
        request=ProviderRequest(
            prompt="task",
            func_tool=tools,
            model="primary-only-model",
            llm_params={"max_tokens": 1},
        ),
    )
    if fits:
        async for _ in runner.step():
            pass
        assert len(fallback.requests) == 2 and len(executor.calls) == 1
        assert all(not request.get("model") for request in fallback.requests)
        assert (
            fallback.requests[-1]["func_tool"]
            .tools[0]
            .parameters["properties"]["value"]["description"]
            == "s" * 6000
        )
    else:
        with pytest.raises(ContextCapacityError):
            async for _ in runner.step():
                pass
        assert len(fallback.requests) == 1 and executor.calls == []
    assert len(primary.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_auxiliary_summary_usage_and_replacement_history_stay_local(streaming):
    provider = BudgetProvider(
        [
            selected_tool(),
            selected_tool(100000),
            LLMResponse(role="assistant", completion_text="done"),
        ],
        max_context_tokens=1800,
    )
    summary = BudgetProvider(
        [
            LLMResponse(
                role="assistant",
                completion_text="summary",
                usage=TokenUsage(input_other=100000),
            )
        ],
        max_context_tokens=20000,
    )
    tools = ToolSet(
        tools=[
            FunctionTool(
                name="read",
                description="Read",
                parameters={
                    "type": "object",
                    "properties": {
                        "value": {"type": "string", "description": "p" * 500}
                    },
                },
            )
        ]
    )
    runner, executor = await parameter_runner(
        provider,
        streaming=streaming,
        request=ProviderRequest(
            prompt="task",
            contexts=[
                {"role": "user", "content": "old history " * 65},
                {"role": "assistant", "content": "old answer " * 10},
            ],
            func_tool=tools,
            llm_params={"max_tokens": 1},
        ),
        custom_token_counter=CharacterTokenCounter(),
        llm_compress_provider=summary,
    )

    events = [event async for event in runner.step_until_done(3)]

    assert len(provider.requests) == 3 and len(summary.requests) == 1
    assert len(executor.calls) == 1
    assert any(event.type == "context_compression" for event in events)
    assert all(
        "old history" not in str(message.content)
        for message in provider.requests[1]["contexts"]
    )
    assert any(
        "old history" in str(message.content)
        for message in provider.requests[2]["contexts"]
    )
    assert all(
        "summary" not in str(message.content)
        for message in provider.requests[2]["contexts"]
    )
    assert (
        summary.requests[0]["func_tool"].openai_schema()
        == provider.requests[1]["func_tool"].openai_schema()
    )
    assert_paired(summary.requests[0]["contexts"])


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_selection_retry_and_parameter_repair_execute_the_tool_once(streaming):
    provider = BudgetProvider(
        [
            EmptyModelOutputError("retry selection"),
            selected_tool(),
            LLMResponse(role="assistant"),
            selected_tool(),
            LLMResponse(role="assistant", completion_text="done"),
        ],
        max_context_tokens=1000,
    )
    runner, executor = await parameter_runner(provider, streaming=streaming)
    runner.EMPTY_OUTPUT_RETRY_WAIT_MIN_S = runner.EMPTY_OUTPUT_RETRY_WAIT_MAX_S = 0

    events = [event async for event in runner.step_until_done(3)]

    assert len(provider.requests) == 5 and len(executor.calls) == 1
    assert fingerprint_messages(
        provider.requests[0]["contexts"]
    ) == fingerprint_messages(provider.requests[1]["contexts"])
    assert all(
        "old history" in str(request["contexts"]) for request in provider.requests
    )
    assert not any(event.type == "context_compression" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_delivery", [1, 2, 3])
async def test_context_persistence_failure_stops_the_provider_call(failed_delivery):
    from astrbot.core.agent_team_execution import ProcessPersistenceError

    async def begin_usage(request):
        return {"receipt_id": request["receipt_id"], "max_output_tokens": 20}

    contexts_seen = 0

    async def record_process(record_id, kind, data):
        nonlocal contexts_seen
        if kind == "model_context":
            contexts_seen += 1
            if contexts_seen == failed_delivery:
                raise ProcessPersistenceError("context persistence failed")

    binding = NodeExecutionBinding(
        run_id="run", team_id="team", member_id="member", node_id=None,
        umo="test:parameters", owner_username="owner", config_id=None,
        begin_usage=begin_usage, record_process=record_process,
    )
    provider = BudgetProvider([selected_tool(), LLMResponse(role="assistant"), selected_tool()], max_context_tokens=20000)
    token = current_team_execution.set(binding)
    try:
        runner, executor = await parameter_runner(provider)
        with pytest.raises(ProcessPersistenceError, match="context persistence failed"):
            async for _ in runner.step():
                pass
    finally:
        current_team_execution.reset(token)
    assert len(provider.requests) == failed_delivery - 1
    assert executor.calls == []
