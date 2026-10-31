"""Cancel and resume real runner requests without inventing execution evidence."""

import asyncio
import copy
from types import SimpleNamespace

import pytest
from mcp.types import AudioContent, CallToolResult, ImageContent, TextContent

from astrbot.core.agent.call_checkpoint import dump_call_messages
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.message import Message, bind_checkpoint_messages
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import (
    FunctionTool,
    ToolRecovery,
    ToolRecoveryResult,
    ToolSet,
)
from astrbot.core.agent_team_execution import (
    CheckpointRecoveryError,
    NodeExecutionBinding,
    current_team_execution,
)
from astrbot.core.exceptions import ContextCapacityError
from astrbot.core.provider.entities import LLMResponse, ProviderRequest, TokenUsage
from astrbot.core.utils.prefix_diagnostics import fingerprint_messages
from tests.agent.test_truncator import pairing_batch, pairing_result
from tests.test_tool_loop_compression_integrity import assert_paired
from tests.test_tool_loop_context_budget import BudgetProvider, BudgetToolExecutor


class PausingProvider(BudgetProvider):
    """Record an in-flight request until the runner cancels its delivery."""

    def __init__(self, responses, **config):
        super().__init__(responses, **config)
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def text_chat(self, **kwargs):
        response = await super().text_chat(**kwargs)
        if response == "pause":
            self.started.set()
            try:
                await asyncio.Future()
            finally:
                self.cancelled.set()
        return response

    async def text_chat_stream(self, **kwargs):
        response = await BudgetProvider.text_chat(self, **kwargs)
        if response == "pause":
            self.started.set()
            try:
                yield LLMResponse(
                    role="assistant",
                    completion_text="unfinished",
                    is_chunk=True,
                    usage=TokenUsage(input_other=900000),
                )
                await asyncio.Future()
            finally:
                self.cancelled.set()
        else:
            yield response


@pytest.fixture
def checkpoint_store():
    """Provide existing checkpoint callbacks without a database or network."""
    checkpoints = []

    async def save(value):
        checkpoints.append(copy.deepcopy(value))

    binding = NodeExecutionBinding(
        run_id="run",
        team_id="team",
        member_id="member",
        node_id=None,
        umo="test:FriendMessage:recovery",
        owner_username="test",
        config_id=None,
        save_checkpoint=save,
    )
    return binding, checkpoints


def plan(*names, usage=50):
    """Build a complete scripted tool plan.

    Args:
        *names: Names also used as durable protocol identities.
        usage: Successful input usage, including any cached input.

    Returns:
        A complete model response selecting the specified tools.
    """
    return LLMResponse(
        role="assistant",
        tools_call_name=list(names),
        tools_call_ids=list(names),
        tools_call_args=[{} for _ in names],
        usage=TokenUsage(input_other=usage, output=2),
    )


async def recovery_runner(
    provider, *, streaming=False, tools=None, executor=None, request=None, **options
):
    """Initialize the public runner driver with the real context manager.

    Args:
        provider: Scripted recording model boundary.
        streaming: Select the streaming main-loop interface.
        tools: Original authorized capabilities.
        executor: Recording executor, optionally shared across simulated restarts.
        request: Optional history or legacy conversation state.
        **options: Existing reset options for compression and schema mode.

    Returns:
        Runner and the executor used for actual tool calls.
    """
    runner = ToolLoopAgentRunner()
    executor = executor or BudgetToolExecutor("evidence")
    await runner.reset(
        provider=provider,
        request=request
        or ProviderRequest(
            prompt="continue the task",
            func_tool=tools,
            llm_params={"max_tokens": 10},
        ),
        run_context=ContextWrapper(context=None),
        tool_executor=executor,
        agent_hooks=BaseAgentRunHooks(),
        streaming=streaming,
        **options,
    )
    return runner, executor


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("stage", ["main", "parameters", "repair", "summary"])
async def test_cancel_in_each_stage_stops_without_repair_delivery(
    checkpoint_store, streaming, stage
):
    binding, checkpoints = checkpoint_store
    token = current_team_execution.set(binding)
    try:
        provider = PausingProvider(
            ([plan("read")] if stage in {"parameters", "repair"} else [])
            + ([LLMResponse(role="assistant")] if stage == "repair" else [])
            + ["pause"],
            max_context_tokens=1000,
        )
        summary = PausingProvider(["pause"], max_context_tokens=12000)
        tools = ToolSet(
            tools=[FunctionTool(name="read", description="Read", parameters={})]
        )
        runner, executor = await recovery_runner(
            provider,
            streaming=streaming,
            request=ProviderRequest(
                prompt="task",
                func_tool=tools,
                contexts=[
                    {"role": "user", "content": "old " * 1500},
                    {"role": "assistant", "content": "old answer"},
                ]
                if stage == "summary"
                else [],
            ),
            tool_schema_mode="skills_like"
            if stage in {"parameters", "repair"}
            else "full",
            llm_compress_provider=summary if stage == "summary" else None,
        )
        events = []

        async def consume():
            events.extend([event async for event in runner.step()])

        pending = asyncio.create_task(consume())
        blocked = summary if stage == "summary" else provider
        await asyncio.wait_for(blocked.started.wait(), 3)
        runner.request_stop()
        await asyncio.wait_for(pending, 3)
        assert blocked.cancelled.is_set()
        assert runner.was_aborted() and runner.done()
        assert sum(event.type == "aborted" for event in events) == 1
        assert executor.calls == []
        assert (
            len(provider.requests)
            == {"main": 1, "parameters": 2, "repair": 3, "summary": 0}[stage]
        )
        assert len(summary.requests) == int(stage == "summary")
        assert runner.stats.token_usage.input < 900000
        if checkpoints:
            assert checkpoints[-1]["phase"] == "request"
            assert checkpoints[-1]["response"] is None
    finally:
        current_team_execution.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("cancel_stage", ["main", "summary"])
async def test_long_task_partial_results_compress_cancel_resume_and_finish(
    checkpoint_store, streaming, cancel_stage
):
    binding, checkpoints = checkpoint_store
    token = current_team_execution.set(binding)
    try:
        tools = ToolSet(
            tools=[
                FunctionTool(name=name, description=name, parameters={})
                for name in ("read", "missing", "finish")
            ]
        )

        class ReadingExecutor(BudgetToolExecutor):
            async def execute(self, tool, run_context, **kwargs):
                self.calls.append((tool.name, kwargs))
                if tool.name != "missing":
                    yield CallToolResult(
                        content=[
                            TextContent(
                                type="text",
                                text="evidence " * 300
                                if tool.name == "read"
                                else "verified",
                            )
                        ]
                    )

        executor = ReadingExecutor("")
        main = PausingProvider(
            [
                plan("read", "missing", usage=600),
                plan("read", "missing", usage=30),
                "pause",
            ],
            max_context_tokens=1000,
        )
        summary = PausingProvider(
            ["pause"]
            if cancel_stage == "summary"
            else [
                LLMResponse(
                    role="assistant",
                    completion_text="read complete; missing is unknown",
                    usage=TokenUsage(input_other=800000),
                )
            ],
            max_context_tokens=12000,
        )
        request = ProviderRequest(
            prompt="read and finish",
            func_tool=tools,
            llm_params={"max_tokens": 10},
            contexts=[
                {"role": "user", "content": "old " * 100},
                {"role": "assistant", "content": "old answer"},
            ],
        )
        runner, _ = await recovery_runner(
            main,
            request=request,
            executor=executor,
            streaming=streaming,
            tool_schema_mode="skills_like",
            llm_compress_provider=summary,
            llm_compress_keep_recent_ratio=0,
        )
        first = [event async for event in runner.step()]
        applied = copy.deepcopy(checkpoints[-1])
        assert applied["phase"] == "tools_applied"
        assert applied["tool_calls"][1]["raw_results"] == []
        assert (
            applied["tool_calls"][1]["protocol_results"][0]["content"]
            == "The tool returned no content."
        )
        # Simulate a missing historical protocol message at the public history
        # boundary. The authoritative checkpoint still retains the original
        # empty-return evidence; request repair must never rewrite that evidence.
        runner.run_context.messages = [
            message
            for message in runner.run_context.messages
            if message.tool_call_id != "missing"
        ]
        events = []

        async def consume():
            events.extend([event async for event in runner.step()])

        pending = asyncio.create_task(consume())
        blocked = summary if cancel_stage == "summary" else main
        await asyncio.wait_for(blocked.started.wait(), 3)
        runner.request_stop()
        await asyncio.wait_for(pending, 3)
        assert any(event.type == "aborted" for event in events)
        assert len(main.requests) == (2 if cancel_stage == "summary" else 3)
        assert executor.calls == [("read", {}), ("missing", {})]
        saved = copy.deepcopy(checkpoints[-1])
        binding.checkpoint = saved
        restored_summary = BudgetProvider(
            [
                LLMResponse(
                    role="assistant",
                    completion_text="read complete; missing is unknown",
                    usage=TokenUsage(input_other=800000),
                )
            ],
            max_context_tokens=12000,
        )
        restored = BudgetProvider(
            [
                plan("finish", usage=50),
                plan("finish", usage=40),
                LLMResponse(
                    role="assistant",
                    completion_text="done",
                    usage=TokenUsage(input_other=100, output=3),
                ),
            ],
            max_context_tokens=1000,
        )
        resumed, _ = await recovery_runner(
            restored,
            tools=tools,
            executor=executor,
            streaming=streaming,
            tool_schema_mode="skills_like",
            llm_compress_provider=restored_summary,
            llm_compress_keep_recent_ratio=0,
        )
        tail = [event async for event in resumed.step_until_done(5)]
        assert resumed.get_final_llm_resp().completion_text == "done"
        assert not resumed.was_aborted()
        assert executor.calls == [("read", {}), ("missing", {}), ("finish", {})]
        assert resumed.stats.token_usage == TokenUsage(input_other=820, output=11)
        assert resumed.stats.current_context_tokens == 100
        assert any(event.type == "tool_call" for event in first)
        assert any(event.type == "context_compression" for event in [*events, *tail])
        assert len(restored_summary.requests) == int(cancel_stage == "summary")
        for sent in [
            *main.requests,
            *summary.requests,
            *restored.requests,
            *restored_summary.requests,
        ]:
            assert_paired(sent["contexts"])
        summary_input = bind_checkpoint_messages(summary.requests[0]["contexts"])
        results = {
            message.tool_call_id: message.content
            for message in summary_input
            if message.role == "tool"
        }
        assert "evidence " * 300 in results["read"]
        assert "execution status is unknown" in results["missing"]
        assert (
            summary.requests[0]["func_tool"].openai_schema()
            == main.requests[2 if cancel_stage == "main" else 0][
                "func_tool"
            ].openai_schema()
        )
        request_boundaries = [
            item for item in checkpoints if item["phase"] == "request"
        ]
        for sent in restored.requests:
            assert any(
                fingerprint_messages(bind_checkpoint_messages(item["input_contexts"]))
                == fingerprint_messages(sent["contexts"])
                for item in request_boundaries
            )
        assert (
            applied["tool_calls"][1]["protocol_results"][0]["content"]
            == "The tool returned no content."
        )
    finally:
        current_team_execution.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["unknown", "failed", "cancel-query", "cancel-query-admission"]
)
async def test_interrupted_tool_query_preserves_evidence_and_never_replays(
    checkpoint_store, outcome
):
    binding, checkpoints = checkpoint_store
    token = current_team_execution.set(binding)
    try:
        queries = []
        outputs = [
            CallToolResult(content=[TextContent(type="text", text="first")]),
            CallToolResult(
                content=[
                    AudioContent(type="audio", data="YQ==", mimeType="audio/wav"),
                    ImageContent(type="image", data="Yg==", mimeType="image/png"),
                    TextContent(type="text", text="last"),
                ],
                isError=True,
            ),
        ]

        async def query(context, identity, arguments):
            queries.append(identity)
            if outcome == "cancel-query":
                querying.set()
                await asyncio.Future()
            return ToolRecoveryResult(
                status=outcome,
                evidence="external ledger",
                results=outputs if outcome == "failed" else [],
            )

        tools = ToolSet(
            tools=[
                FunctionTool(
                    name="read",
                    description="Read",
                    parameters={},
                    recovery=ToolRecovery(query=query),
                )
            ]
        )

        class PartialExecutor(BudgetToolExecutor):
            async def execute(self, tool, run_context, **kwargs):
                self.calls.append((tool.name, kwargs))
                yield outputs[0]
                blocked.set()
                await asyncio.Future()

        blocked = asyncio.Event()
        querying = asyncio.Event()
        executor = PartialExecutor("")
        runner, _ = await recovery_runner(
            BudgetProvider([plan("read")]), tools=tools, executor=executor
        )

        async def consume():
            return [event async for event in runner.step()]

        pending = asyncio.create_task(consume())
        await asyncio.wait_for(blocked.wait(), 3)
        runner.request_stop()
        events = await asyncio.wait_for(pending, 3)
        assert events[-1].type == "aborted"
        saved = copy.deepcopy(checkpoints[-1])
        original = copy.deepcopy(saved["tool_calls"][0])
        assert original["status"] == "started"
        assert len(original["raw_results"]) == 1
        binding.checkpoint = saved
        provider = BudgetProvider(
            [LLMResponse(role="assistant", completion_text="done")]
        )
        resumed, _ = await recovery_runner(provider, tools=tools, executor=executor)
        if outcome == "cancel-query-admission":

            async def stop_query_admission():
                resumed.request_stop()

            binding.admit_work = stop_query_admission
            events = [event async for event in resumed.step()]
            assert events[-1].type == "aborted"
            assert provider.requests == []
            assert checkpoints[-1]["tool_calls"][0] == original
        elif outcome == "cancel-query":

            async def consume_resumed():
                return [event async for event in resumed.step()]

            pending = asyncio.create_task(consume_resumed())
            await asyncio.wait_for(querying.wait(), 3)
            resumed.request_stop()
            events = await asyncio.wait_for(pending, 3)
            assert events[-1].type == "aborted"
            assert provider.requests == []
            assert checkpoints[-1]["tool_calls"][0] == original
        elif outcome == "unknown":
            with pytest.raises(CheckpointRecoveryError):
                async for _ in resumed.step_until_done(3):
                    pass
            assert provider.requests == []
            latest = checkpoints[-1]["tool_calls"][0]
            assert latest["status"] == "started"
            assert latest["raw_results"] == original["raw_results"]
            assert latest["protocol_results"] == original["protocol_results"]
        else:
            async for _ in resumed.step_until_done(3):
                pass
            assert len(provider.requests) == 1
            assert_paired(provider.requests[0]["contexts"])
            visible = str(provider.requests[0]["contexts"])
            assert visible.count("first") == visible.count("last") == 1
            assert "Tool result status: error" in visible
            assert "data:audio/wav;base64,YQ==" in visible
            assert "data:image/png;base64,Yg==" in visible
        assert executor.calls == [("read", {})]
        assert queries == (
            [] if outcome == "cancel-query-admission" else [original["record_id"]]
        )
    finally:
        current_team_execution.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["before-step", "admission"])
async def test_stop_precedes_saved_response_and_preparation(checkpoint_store, boundary):
    binding, checkpoints = checkpoint_store
    token = current_team_execution.set(binding)
    try:
        tools = ToolSet(
            tools=[FunctionTool(name="read", description="Read", parameters={})]
        )
        source, executor = await recovery_runner(
            BudgetProvider([plan("read")]), tools=tools
        )
        stream = source.step()
        while (await anext(stream)).type != "tool_call":
            pass
        await stream.aclose()
        saved = copy.deepcopy(checkpoints[-1])
        binding.checkpoint = saved
        provider = BudgetProvider([])
        runner, _ = await recovery_runner(provider, tools=tools, executor=executor)
        if boundary == "before-step":
            runner.request_stop()
        else:
            # Stop during admission of a normal request before an invalid
            # history can trigger repair, compression, or a protocol error.
            binding.checkpoint = None
            runner, _ = await recovery_runner(provider, tools=tools, executor=executor)

            async def stop_admission():
                runner.request_stop()
                runner.run_context.messages.append(pairing_batch("A", "A"))

            binding.admit_work = stop_admission
        count = len(checkpoints)
        events = [event async for event in runner.step()]
        assert [event.type for event in events] == ["aborted"]
        assert provider.requests == [] and executor.calls == []
        assert len(checkpoints) == count
        assert binding.checkpoint == (saved if boundary == "before-step" else None)
    finally:
        current_team_execution.reset(token)


@pytest.mark.asyncio
async def test_legacy_conversation_scalar_does_not_hide_large_continuation():
    provider = BudgetProvider([], max_context_tokens=1000)
    runner, executor = await recovery_runner(
        provider,
        request=ProviderRequest(
            prompt="x" * 20000,
            conversation=SimpleNamespace(cid="legacy", token_usage=1),
        ),
    )
    with pytest.raises(ContextCapacityError):
        async for _ in runner.step():
            pass
    assert provider.requests == [] and executor.calls == []


@pytest.mark.asyncio
async def test_round_truncation_reestimates_instead_of_reusing_previous_usage():
    provider = BudgetProvider(
        [
            plan("read", usage=900),
            LLMResponse(role="assistant", completion_text="done"),
        ],
        max_context_tokens=1000,
    )
    runner, executor = await recovery_runner(
        provider,
        enforce_max_turns=2,
        request=ProviderRequest(
            prompt="task",
            contexts=[
                {"role": "user", "content": "old request"},
                {"role": "assistant", "content": "old answer"},
            ],
            func_tool=ToolSet(
                tools=[FunctionTool(name="read", description="Read", parameters={})]
            ),
        ),
    )
    async for _ in runner.step():
        pass
    runner.run_context.messages.append(Message(role="user", content="late request"))
    events = [event async for event in runner.step()]
    assert len(provider.requests) == 2
    assert executor.calls == [("read", {})]
    assert not any(event.type == "context_compression" for event in events)
    assert "late request" in str(provider.requests[-1]["contexts"])
    assert "old request" not in str(provider.requests[-1]["contexts"])
    assert_paired(provider.requests[-1]["contexts"])


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["main", "parameters", "repair"])
@pytest.mark.parametrize("fits", [False, True])
async def test_legacy_checkpoint_reestimates_actual_stage_and_saves_sent_view(
    checkpoint_store, stage, fits
):
    binding, checkpoints = checkpoint_store
    token = current_team_execution.set(binding)
    try:
        tools = ToolSet(
            tools=[FunctionTool(name="read", description="Read", parameters={})]
        )
        source = PausingProvider(
            ([plan("read")] if stage != "main" else [])
            + ([LLMResponse(role="assistant")] if stage == "repair" else [])
            + ["pause"]
        )
        runner, _ = await recovery_runner(
            source,
            tools=tools,
            tool_schema_mode="skills_like" if stage != "main" else "full",
        )

        async def consume():
            return [event async for event in runner.step()]

        pending = asyncio.create_task(consume())
        await asyncio.wait_for(source.started.wait(), 3)
        runner.request_stop()
        await pending
        saved = copy.deepcopy(checkpoints[-1])
        stage_instruction = bind_checkpoint_messages(saved["input_contexts"])[-1]
        history = [
            pairing_batch("A", "B"),
            pairing_result("A", "real evidence"),
            Message(role="user", content="x" * (100 if fits else 20000)),
        ]
        if stage != "main":
            history[-1].content += "\n" + stage_instruction.content
        saved["input_contexts"] = dump_call_messages(history)
        saved["stats"]["current_context_tokens"] = 1
        saved["stats"]["token_usage"] = {
            "input_other": 500000,
            "input_cached": 0,
            "output": 0,
        }
        binding.checkpoint = saved
        provider = BudgetProvider(
            [plan("read"), LLMResponse(role="assistant", completion_text="done")]
            if stage != "main"
            else [LLMResponse(role="assistant", completion_text="done")],
            max_context_tokens=1000,
        )
        resumed, executor = await recovery_runner(
            provider,
            tools=tools,
            tool_schema_mode="skills_like" if stage != "main" else "full",
        )
        if fits:
            async for _ in resumed.step_until_done(4):
                pass
            sent = provider.requests[0]
            assert_paired(sent["contexts"])
            visible = str(sent["contexts"])
            assert visible.count("execution status is unknown") == 1
            assert visible.count("real evidence") == 1
            assert "x" * 100 in visible
            if stage != "main":
                assert Message.model_validate(sent["contexts"][-1]).content.endswith(
                    stage_instruction.content
                )
            assert saved["stage"] == stage
            assert any(
                item["invocation_id"] == saved["invocation_id"]
                and fingerprint_messages(
                    bind_checkpoint_messages(item["input_contexts"])
                )
                == fingerprint_messages(sent["contexts"])
                for item in checkpoints
                if item["phase"] == "request"
            )
        else:
            with pytest.raises(ContextCapacityError):
                async for _ in resumed.step():
                    pass
            assert provider.requests == [] and executor.calls == []
    finally:
        current_team_execution.reset(token)
