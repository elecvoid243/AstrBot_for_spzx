"""Exercise Responses usage evidence through the actual Runner and SDK boundary."""

import copy
from importlib import import_module
from types import SimpleNamespace

import httpx
import pytest

from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.message import ImageURLPart, TextPart
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.provider.sources import request_retry
from tests.test_openai_responses_source import _make_provider, _make_response
from tests.test_tool_loop_context_budget import BudgetToolExecutor
from tests.test_tool_loop_native_usage import OpenAIUsageRecorder


class ResponsesUsageRecorder:
    """Return real Responses SDK objects and record the adapter's native payload."""

    def __init__(self, first_error=None, *, tool_plan=True, on_first_error=None):
        self.first_error = first_error
        self.tool_plan = tool_plan
        self.on_first_error = on_first_error
        self.requests = []
        self.successes = 0

    async def create(self, **kwargs):
        """Deliver an optional error, one tool plan, then a final answer.

        Args:
            **kwargs: Native Responses SDK request produced by the real adapter.

        Returns:
            An SDK Response or a stream of Responses SDK events.

        Raises:
            Exception: The configured first-attempt error.
        """
        self.requests.append(copy.deepcopy(kwargs))
        if len(self.requests) == 1 and self.first_error is not None:
            if self.on_first_error:
                self.on_first_error(**kwargs)
            raise self.first_error
        self.successes += 1
        tool_plan = self.tool_plan and self.successes == 1
        output = (
            [
                {
                    "type": "function_call",
                    "id": "fc-one",
                    "call_id": "read-one",
                    "name": "read",
                    "arguments": "{}",
                    "status": "completed",
                }
            ]
            if tool_plan
            else [
                {
                    "id": "msg-one",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": "done", "annotations": []}
                    ],
                }
            ]
        )
        response = _make_response(output)
        if kwargs.get("stream"):

            async def stream():
                yield SimpleNamespace(
                    type="response.output_text.delta",
                    delta="planning" if tool_plan else "done",
                )
                yield SimpleNamespace(type="response.completed", response=response)

            return stream()
        return response


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_responses_public_runner_entry_reaches_native_sdk(monkeypatch, streaming):
    """A plain request reaches the Responses SDK in both delivery modes."""
    provider = _make_provider({"max_context_tokens": 1000})
    requests = []
    response = _make_response(
        [
            {
                "id": "msg-one",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": "hello", "annotations": []}
                ],
            }
        ]
    )

    async def create(**kwargs):
        requests.append(copy.deepcopy(kwargs))
        if kwargs.get("stream"):

            async def stream():
                yield SimpleNamespace(type="response.output_text.delta", delta="hello")
                yield SimpleNamespace(type="response.completed", response=response)

            return stream()
        return response

    monkeypatch.setattr(provider.client.responses, "create", create)
    runner = ToolLoopAgentRunner()
    try:
        await runner.reset(
            provider=provider,
            request=ProviderRequest(prompt="hello"),
            run_context=ContextWrapper(context=None),
            tool_executor=BudgetToolExecutor("unused"),
            agent_hooks=BaseAgentRunHooks(),
            streaming=streaming,
        )
        events = [event async for event in runner.step()]
    finally:
        await provider.terminate()

    assert len(requests) == 1
    assert "messages" not in requests[0]
    assert "hello" in str(requests[0]["input"])
    assert runner.final_llm_resp.completion_text == "hello"
    assert runner.final_llm_resp.usage_matches_request is True
    assert not any(event.type == "err" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("recovery", ["none", "transport", "truncate"])
async def test_responses_usage_reuse_matches_native_input_and_flat_tools(
    monkeypatch, streaming, recovery
):
    """Only unchanged Responses input calibrates the next tool-loop request."""
    provider = _make_provider({"max_context_tokens": 1000})
    recorder = ResponsesUsageRecorder(
        {
            "none": None,
            "transport": httpx.ConnectError("temporary connection failure"),
            "truncate": RuntimeError("maximum context length"),
        }[recovery]
    )
    monkeypatch.setattr(provider.client.responses, "create", recorder.create)
    monkeypatch.setattr(request_retry, "REQUEST_RETRY_WAIT_MIN_S", 0)
    monkeypatch.setattr(request_retry, "REQUEST_RETRY_WAIT_MAX_S", 0)
    executor = BudgetToolExecutor("x" * 1100)
    runner = ToolLoopAgentRunner()
    try:
        await runner.reset(
            provider=provider,
            request=ProviderRequest(
                prompt="current",
                contexts=[
                    {"role": "user", "content": "a" * 1100},
                    {"role": "assistant", "content": "b" * 1100},
                ],
                func_tool=ToolSet(
                    tools=[FunctionTool(name="read", description="Read", parameters={})]
                ),
            ),
            run_context=ContextWrapper(context=None),
            tool_executor=executor,
            agent_hooks=BaseAgentRunHooks(),
            streaming=streaming,
            request_max_retries=2,
        )
        events = [event async for event in runner.step_until_done(3)]
    finally:
        await provider.terminate()

    assert len(recorder.requests) == (2 if recovery == "none" else 3)
    first, final = recorder.requests[0], recorder.requests[-1]
    assert all("messages" not in request for request in recorder.requests)
    assert first["tools"] == [
        {"type": "function", "name": "read", "description": "Read", "parameters": {}}
    ]
    assert "a" * 1100 in str(first["input"])
    assert ("a" * 1100 in str(final["input"])) is (recovery != "truncate")
    if recovery == "truncate":
        assert len(recorder.requests[1]["input"]) == 1
    assert any(
        item["type"] == "function_call" and item["call_id"] == "read-one"
        for item in final["input"]
    )
    assert any(
        item["type"] == "function_call_output" and item["output"] == "x" * 1100
        for item in final["input"]
    )
    assert sum(event.type == "context_compression" for event in events) == int(
        recovery == "truncate"
    )
    assert len(executor.calls) == 1
    assert runner.stats.token_usage.input == 20
    assert runner.stats.current_context_tokens == 10
    assert runner.final_llm_resp.usage_matches_request is True
    if streaming:
        assert any(event.type == "streaming_delta" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "change", ["tools_removed", "tool_schema_changed", "image_removed"]
)
async def test_responses_recovery_invalidates_native_schema_or_media_changes(
    monkeypatch, streaming, change
):
    """Protocol-specific tool or input changes retain usage without certifying it."""
    provider = _make_provider(
        {"key": ["test-one", "test-two"], "max_context_tokens": 1000}
    )
    recorder = ResponsesUsageRecorder(
        {
            "tools_removed": RuntimeError("Function calling is not enabled"),
            "tool_schema_changed": RuntimeError("429"),
            "image_removed": RuntimeError("The model is not a VLM"),
        }[change],
        tool_plan=False,
        on_first_error=(
            lambda **request: request["tools"][0]["parameters"].update(
                {"description": "changed"}
            )
        )
        if change == "tool_schema_changed"
        else None,
    )
    monkeypatch.setattr(provider.client.responses, "create", recorder.create)
    contexts = []
    if change == "image_removed":
        contexts = [
            {
                "role": "user",
                "content": [
                    TextPart(text="describe").model_dump(),
                    ImageURLPart(
                        image_url={
                            "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR6cAAAAASUVORK5CYII="
                        }
                    ).model_dump(),
                ],
            }
        ]
    executor = BudgetToolExecutor("unused")
    runner = ToolLoopAgentRunner()
    try:
        await runner.reset(
            provider=provider,
            request=ProviderRequest(
                prompt="current",
                contexts=contexts,
                func_tool=ToolSet(
                    tools=[FunctionTool(name="read", description="Read", parameters={})]
                ),
            ),
            run_context=ContextWrapper(context=None),
            tool_executor=executor,
            agent_hooks=BaseAgentRunHooks(),
            streaming=streaming,
        )
        async for _ in runner.step():
            pass
    finally:
        await provider.terminate()

    assert len(recorder.requests) == 2
    first, final = recorder.requests
    assert all("messages" not in request for request in recorder.requests)
    if change == "tools_removed":
        assert first["tools"]
        assert "tools" not in final
    elif change == "tool_schema_changed":
        assert first["tools"][0]["parameters"] == {}
        assert final["tools"][0]["parameters"] == {"description": "changed"}
    else:
        assert "input_image" in str(first["input"])
        assert "input_image" not in str(final["input"])
    assert runner.final_llm_resp.usage_matches_request is False
    assert runner.stats.token_usage.input == 10
    assert runner.stats.current_context_tokens == 10
    assert executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "module_name,class_name,model,protocol",
    [
        ("groq_source", "ProviderGroq", "test", "chat"),
        ("longcat_source", "ProviderLongCat", "test", "chat"),
        ("mirarouter_source", "ProviderMiraRouter", "test", "chat"),
        ("oai_aihubmix_source", "ProviderAIHubMix", "test", "chat"),
        ("openrouter_source", "ProviderOpenRouter", "test", "chat"),
        ("ssycloud_source", "ProviderSSYCloud", "test", "chat"),
        ("xiaomi_source", "ProviderXiaomi", "test", "chat"),
        ("xai_source", "ProviderXAI", "test", "chat"),
        ("zhipu_source", "ProviderZhipu", "test", "chat"),
        ("openai_source", "ProviderOpenAIOfficial", "gemini-2.5-flash", "chat"),
        (
            "openai_responses_source",
            "ProviderOpenAIResponses",
            "gemini-2.5-flash",
            "responses",
        ),
    ],
)
async def test_inherited_native_protocol_keeps_tool_shape_and_order(
    monkeypatch, streaming, module_name, class_name, model, protocol
):
    """Derived adapters keep their native input key, schema format and ordering."""
    provider_class = getattr(
        import_module(f"astrbot.core.provider.sources.{module_name}"), class_name
    )
    provider = provider_class(
        {
            "id": "test-derived",
            "key": ["test-key"],
            "model": model,
            "api_base": "https://example.invalid/v1",
            "max_context_tokens": 1000,
        },
        {},
    )
    recorder = (
        ResponsesUsageRecorder(tool_plan=False)
        if protocol == "responses"
        else OpenAIUsageRecorder(tool_plan=False)
    )
    endpoint = (
        provider.client.responses
        if protocol == "responses"
        else provider.client.chat.completions
    )
    monkeypatch.setattr(endpoint, "create", recorder.create)
    runner = ToolLoopAgentRunner()
    try:
        await runner.reset(
            provider=provider,
            request=ProviderRequest(
                prompt="hello",
                func_tool=ToolSet(
                    tools=[
                        FunctionTool(name="zeta", description="Zeta", parameters={}),
                        FunctionTool(name="alpha", description="Alpha", parameters={}),
                    ]
                ),
            ),
            run_context=ContextWrapper(context=None),
            tool_executor=BudgetToolExecutor("unused"),
            agent_hooks=BaseAgentRunHooks(),
            streaming=streaming,
        )
        events = [event async for event in runner.step()]
    finally:
        await provider.terminate()

    assert len(recorder.requests) == 1
    request = recorder.requests[0]
    assert ("input" in request) is (protocol == "responses")
    assert ("messages" in request) is (protocol == "chat")
    definitions = [
        {"name": "alpha", "description": "Alpha"},
        {"name": "zeta", "description": "Zeta"},
    ]
    if model == "test" or protocol == "responses":
        for definition in definitions:
            definition["parameters"] = {}
    assert request["tools"] == [
        {"type": "function", **definition}
        if protocol == "responses"
        else {"type": "function", "function": definition}
        for definition in definitions
    ]
    assert runner.final_llm_resp.usage_matches_request is True
    assert runner.final_llm_resp.completion_text == "done"
    assert not any(event.type == "err" for event in events)
