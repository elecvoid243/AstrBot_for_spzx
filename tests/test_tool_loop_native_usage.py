"""Validate usage provenance at the native OpenAI SDK delivery boundary."""

import copy

import httpx
import pytest
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.message import ImageURLPart, TextPart
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.provider.sources import request_retry
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
from tests.test_tool_loop_context_budget import BudgetToolExecutor


class OpenAIUsageRecorder:
    """Record real adapter deliveries and emulate an SDK completion or stream."""

    def __init__(self, first_error=None, *, tool_plan=True, on_first_error=None):
        self.first_error = first_error
        self.tool_plan = tool_plan
        self.on_first_error = on_first_error
        self.requests = []
        self.successes = 0

    async def create(self, **kwargs):
        """Return one tool plan followed by a final answer.

        Args:
            **kwargs: Native SDK request produced by the real adapter.

        Returns:
            An SDK completion or asynchronous stream of SDK chunks.

        Raises:
            Exception: The configured first-attempt error.
        """
        self.requests.append(copy.deepcopy(kwargs))
        if len(self.requests) == 1 and self.first_error is not None:
            if self.on_first_error is not None:
                self.on_first_error(**kwargs)
            raise self.first_error
        self.successes += 1
        tool_plan = self.tool_plan and self.successes == 1
        message = {"role": "assistant", "content": "planning" if tool_plan else "done"}
        if tool_plan:
            message["tool_calls"] = [
                {
                    "id": "read-one",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ]
        usage = {"prompt_tokens": 50, "completion_tokens": 1, "total_tokens": 51}
        response = {
            "id": "native-response",
            "created": 0,
            "model": "test",
            "usage": usage,
        }
        finish_reason = "tool_calls" if tool_plan else "stop"
        if kwargs.get("stream"):

            async def stream():
                if tool_plan:
                    message["tool_calls"][0]["index"] = 0
                yield ChatCompletionChunk.model_validate(
                    {
                        **response,
                        "object": "chat.completion.chunk",
                        "choices": [
                            {
                                "index": 0,
                                "delta": message,
                                "finish_reason": finish_reason,
                            }
                        ],
                    }
                )

            return stream()
        return ChatCompletion.model_validate(
            {
                **response,
                "object": "chat.completion",
                "choices": [
                    {"index": 0, "message": message, "finish_reason": finish_reason}
                ],
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("recovery", ["truncate", "none", "transport"])
async def test_native_recovery_preserves_usage_only_for_matching_requests(
    monkeypatch, streaming, recovery
):
    """Only an unchanged successful input can calibrate the next tool request."""
    provider = ProviderOpenAIOfficial(
        {
            "id": "test-openai",
            "model": "test",
            "key": ["test-key"],
            "api_base": "https://example.invalid/v1",
            "max_context_tokens": 1000,
        },
        {},
    )
    errors = {
        "truncate": RuntimeError("maximum context length"),
        "none": None,
        "transport": httpx.ConnectError("temporary connection failure"),
    }
    recorder = OpenAIUsageRecorder(errors[recovery])
    monkeypatch.setattr(request_retry, "REQUEST_RETRY_WAIT_MIN_S", 0)
    monkeypatch.setattr(request_retry, "REQUEST_RETRY_WAIT_MAX_S", 0)
    monkeypatch.setattr(provider.client.chat.completions, "create", recorder.create)
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
    assert any(
        message.get("content") == "a" * 1100
        for message in recorder.requests[0]["messages"]
    )
    assert len(recorder.requests[1]["messages"]) == (
        1 if recovery == "truncate" else (5 if recovery == "none" else 3)
    )
    assert any(
        message.get("content") == "a" * 1100
        for message in recorder.requests[-1]["messages"]
    ) is (recovery != "truncate")
    assert any(
        message["role"] == "tool" and "x" * 1100 in str(message["content"])
        for message in recorder.requests[-1]["messages"]
    )
    assert sum(event.type == "context_compression" for event in events) == int(
        recovery == "truncate"
    )
    assert len(executor.calls) == 1
    assert runner.stats.token_usage.input == 100
    assert runner.stats.current_context_tokens == 50
    if streaming:
        assert any(event.type == "streaming_delta" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "change", ["tools_removed", "tool_schema_changed", "image_removed"]
)
async def test_native_recovery_reports_changed_input_without_losing_usage(
    monkeypatch, streaming, change
):
    """Recovery reports changes to schemas or media on the complete response."""
    provider = ProviderOpenAIOfficial(
        {
            "id": "test-openai",
            "model": "test",
            "key": ["test-key-one", "test-key-two"],
            "api_base": "https://example.invalid/v1",
            "max_context_tokens": 1000,
        },
        {},
    )
    tool = FunctionTool(name="read", description="Read", parameters={})
    errors = {
        "tools_removed": RuntimeError("Function calling is not enabled"),
        "tool_schema_changed": RuntimeError("429"),
        "image_removed": RuntimeError("The model is not a VLM"),
    }
    recorder = OpenAIUsageRecorder(
        errors[change],
        tool_plan=False,
        # Mutate the native binding in place, rather than replacing its ToolSet.
        # The independent original schema must not share this dictionary.
        on_first_error=(
            lambda **request: request["tools"][0]["function"]["parameters"].update(
                {"description": "changed"}
            )
        )
        if change == "tool_schema_changed"
        else None,
    )
    monkeypatch.setattr(provider.client.chat.completions, "create", recorder.create)
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
                prompt="current", contexts=contexts, func_tool=ToolSet(tools=[tool])
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
    if change == "tools_removed":
        assert first["tools"]
        assert "tools" not in final
    elif change == "tool_schema_changed":
        assert first["tools"][0]["function"]["parameters"] == {}
        assert final["tools"][0]["function"]["parameters"] == {"description": "changed"}
    else:
        assert "image_url" in str(first["messages"])
        assert "image_url" not in str(final["messages"])
    assert runner.final_llm_resp.usage_matches_request is False
    assert runner.stats.token_usage.input == 50
    assert runner.stats.current_context_tokens == 50
    assert executor.calls == []
