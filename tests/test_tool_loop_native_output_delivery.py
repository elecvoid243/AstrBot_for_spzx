"""Verify checked output limits at actual native SDK delivery boundaries."""

import asyncio
import copy
import json

import openai
import pytest
import pytest_asyncio
from openai import _base_client as openai_base_client

from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.agent.context.token_counter import EstimateTokenCounter
from astrbot.core.agent.message import bind_checkpoint_messages
from astrbot.core.agent_team_execution import (
    NodeExecutionBinding,
    current_team_execution,
    model_delivery,
)
from astrbot.core.exceptions import ContextCapacityError
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.provider.sources.openai_responses_source import (
    ProviderOpenAIResponses,
)
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
from tests.test_tool_loop_context_budget import BudgetProvider, BudgetToolExecutor


@pytest_asyncio.fixture
async def native_openai(request):
    """Intercept the real OpenAI SDK's JSON and SSE transports.

    Args:
        request: Native adapter selected by pytest.

    Yields:
        Real provider, captured JSON bodies, and the native output field.
    """
    requests = []
    sdk_httpx = getattr(
        openai_base_client, "httpx", getattr(openai_base_client, "httpx2", None)
    )
    response_api = getattr(request, "param", "chat") == "responses"
    tool_plan = getattr(request, "param", "chat") in {"chat-tools", "chat-repair"}
    repair = getattr(request, "param", "chat") == "chat-repair"
    config = {
        "id": "native-openai",
        "model": "test",
        "key": ["offline"],
        "api_base": "https://example.invalid/v1",
        "max_context_tokens": 1000,
    }
    provider = (ProviderOpenAIResponses if response_api else ProviderOpenAIOfficial)(
        config, {}
    )
    await provider.client.close()

    def record(http_request):
        """Return offline native responses after SDK serialization.

        Args:
            http_request: Actual native SDK HTTP request.

        Returns:
            A completion/response or its server-sent event representation.
        """
        payload = json.loads(http_request.content)
        requests.append(payload)
        if response_api:
            body = {
                "id": "resp_1",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "test",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_1",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "done", "annotations": []}
                        ],
                    }
                ],
                "parallel_tool_calls": True,
                "tool_choice": "auto",
                "tools": [],
                "usage": {
                    "input_tokens": 750,
                    "output_tokens": 1,
                    "total_tokens": 751,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            }
            events = [
                {
                    "type": "response.created",
                    "response": {**body, "output": []},
                    "sequence_number": 0,
                },
                {
                    "type": "response.output_text.delta",
                    "delta": "done",
                    "item_id": "msg_1",
                    "output_index": 0,
                    "content_index": 0,
                    "sequence_number": 1,
                },
                {"type": "response.completed", "response": body, "sequence_number": 2},
            ]
        else:
            body = {
                "id": "chat_1",
                "object": "chat.completion",
                "created": 1,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 1,
                    "total_tokens": 51,
                },
            }
            if tool_plan and len(requests) <= (3 if repair else 2):
                message = body["choices"][0]
                message["message"] = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "read-one",
                            "type": "function",
                            "function": {
                                "name": "read",
                                "arguments": json.dumps(
                                    {"value": "actual"} if len(requests) == 2 else {}
                                ),
                            },
                        }
                    ],
                }
                message["finish_reason"] = "tool_calls"
                if repair and len(requests) == 2:
                    message["message"] = {
                        "role": "assistant",
                        "content": None,
                        "reasoning": "Need another parameter query.",
                    }
                    message["finish_reason"] = "stop"
            events = [
                {
                    **body,
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": copy.deepcopy(body["choices"][0]["message"]),
                            "finish_reason": body["choices"][0]["finish_reason"],
                        }
                    ],
                }
            ]
            for call in events[0]["choices"][0]["delta"].get("tool_calls", []):
                call["index"] = 0
        if not payload.get("stream"):
            return sdk_httpx.Response(200, json=body)
        return sdk_httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join("data: " + json.dumps(event) + "\n\n" for event in events)
            + "data: [DONE]\n\n",
        )

    provider.client = openai.AsyncOpenAI(
        api_key="offline",
        base_url="https://example.invalid/v1",
        max_retries=0,
        http_client=sdk_httpx.AsyncClient(transport=sdk_httpx.MockTransport(record)),
    )
    try:
        yield provider, requests, "max_output_tokens" if response_api else "max_tokens"
    finally:
        await provider.terminate()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("override", [None, "max_tokens", "max_completion_tokens"])
async def test_late_limit_is_delivered_without_widening_the_checked_budget(
    native_openai, streaming, override
):
    provider, requests, field = native_openai
    if override:
        provider.provider_config["custom_extra_body"] = {override: 500}
    original_config = copy.deepcopy(provider.provider_config)
    receipts = []

    async def begin_usage(request):
        # Admission sees a legitimate initial cap of 500 before a late update.
        assert request["max_output_tokens"] == 500
        runner.req.llm_params["max_tokens"] = 100
        receipt = {"receipt_id": request["receipt_id"], "max_output_tokens": 500}
        receipts.append(receipt)
        return receipt

    binding = NodeExecutionBinding(
        run_id="run",
        team_id="team",
        member_id="member",
        node_id=None,
        umo="test:FriendMessage:late-native-cap",
        owner_username="test",
        config_id=None,
        begin_usage=begin_usage,
    )
    token = current_team_execution.set(binding)
    executor = BudgetToolExecutor("unused")
    runner = ToolLoopAgentRunner()
    try:
        await runner.reset(
            provider=provider,
            request=ProviderRequest(prompt="x" * 2500, llm_params={"max_tokens": 500}),
            run_context=ContextWrapper(context=None),
            tool_executor=executor,
            agent_hooks=BaseAgentRunHooks(),
            streaming=streaming,
        )
        events = [event async for event in runner.step()]
    finally:
        current_team_execution.reset(token)
    assert len(requests) == 1
    assert (requests[0].get("max_completion_tokens") or requests[0].get(field)) == 100
    assert not any(event.type == "context_compression" for event in events)
    assert runner.get_final_llm_resp().completion_text == "done"
    assert executor.calls == []
    # A checked delivery limit must not rewrite accounting's original receipt.
    assert receipts[0]["max_output_tokens"] == 500
    assert provider.provider_config == original_config


async def native_runner(provider, *, streaming=False, request=None, **kwargs):
    """Initialize the real runner for native delivery scenarios.

    Args:
        provider: Native provider using an offline SDK transport.
        streaming: Select the existing streaming driver.
        request: Optional request replacing the shared capacity fixture.
        **kwargs: Existing runner reset options.

    Returns:
        Runner and recording tool executor.
    """
    runner = ToolLoopAgentRunner()
    executor = BudgetToolExecutor("unused")
    await runner.reset(
        provider=provider,
        request=request
        or ProviderRequest(prompt="x" * 2500, llm_params={"max_tokens": 100}),
        run_context=ContextWrapper(context=None),
        tool_executor=executor,
        agent_hooks=BaseAgentRunHooks(),
        streaming=streaming,
        **kwargs,
    )
    return runner, executor


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("fits", [False, True])
@pytest.mark.parametrize("field", ["max_tokens", "max_completion_tokens"])
async def test_unmetered_output_budget_is_enforced_at_its_capacity_boundary(
    native_openai, streaming, fits, field
):
    provider, requests, _ = native_openai
    provider.provider_config["max_context_tokens"] = 850 - int(not fits)
    runner, executor = await native_runner(
        provider,
        streaming=streaming,
        request=ProviderRequest(prompt="x" * 2500, llm_params={field: 100}),
    )
    if fits:
        async for _ in runner.step():
            pass
        assert len(requests) == 1
        assert requests[0].get("max_tokens") == 100
    else:
        with pytest.raises(ContextCapacityError):
            async for _ in runner.step():
                pass
        assert requests == []
    assert executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_nested_and_unspecified_deliveries_do_not_inherit_request_caps(
    native_openai, streaming
):
    provider, requests, field = native_openai
    with model_delivery(None, output_limit=700):
        runner, _ = await native_runner(provider, streaming=streaming)
        async for _ in runner.step():
            pass
        await provider.text_chat(prompt="outer")
        for unset in (None, 0):
            with model_delivery(None, output_limit=unset):
                await provider.text_chat(prompt="unbounded")
        with pytest.raises(RuntimeError):
            with model_delivery(None, output_limit=50):
                raise RuntimeError("failed preparation")
        await provider.text_chat(prompt="restored outer")
    await provider.text_chat(prompt="outside")
    assert [request.get(field) for request in requests] == [
        100,
        700,
        None,
        None,
        700,
        None,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_cancelled_native_delivery_resets_the_checked_cap(
    native_openai, streaming, monkeypatch
):
    provider, requests, field = native_openai
    started = asyncio.Event()
    cancelled = asyncio.Event()
    captured = []
    transport = provider.client._client._transport
    original = transport.handle_async_request

    async def block(request):
        captured.append(json.loads(request.content))
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(transport, "handle_async_request", block)
    runner, executor = await native_runner(provider, streaming=streaming)

    async def run():
        return [event async for event in runner.step()]

    task = asyncio.create_task(run())
    await asyncio.wait_for(started.wait(), 5)
    runner.request_stop()
    events = await asyncio.wait_for(task, 5)
    assert cancelled.is_set()
    assert captured[0][field] == 100
    assert runner.was_aborted()
    assert any(event.type == "aborted" for event in events)
    assert executor.calls == []
    monkeypatch.setattr(transport, "handle_async_request", original)
    await provider.text_chat(prompt="after cancellation")
    assert len(requests) == 1 and field not in requests[0]


@pytest.mark.asyncio
async def test_closing_native_stream_resets_the_checked_cap(native_openai):
    provider, requests, field = native_openai
    runner, _ = await native_runner(provider, streaming=True)
    async for event in runner.step():
        if event.type == "streaming_delta":
            runner.request_stop()
    assert runner.was_aborted()
    await provider.text_chat(prompt="after streaming close")
    assert [request.get(field) for request in requests] == [100, None]


@pytest.mark.asyncio
@pytest.mark.parametrize("metered", [False, True])
@pytest.mark.parametrize("fits", [False, True])
async def test_summary_budget_is_delivered_to_the_native_sdk(
    native_openai, metered, fits
):
    summary, requests, field = native_openai
    summary.provider_config["max_context_tokens"] = 1000000

    async def begin_usage(request):
        return {
            "receipt_id": request["receipt_id"],
            "max_output_tokens": 500 if request["name"] == "native-openai" else 50,
        }

    async def run_main():
        main = BudgetProvider(
            [LLMResponse(role="assistant", completion_text="main done")],
            max_context_tokens=1000,
        )
        runner, executor = await native_runner(
            main,
            request=ProviderRequest(
                prompt="task",
                llm_params={"max_tokens": 100},
                contexts=[
                    {"role": "user", "content": "old user " * 200},
                    {"role": "assistant", "content": "old answer " * 200},
                ],
            ),
            llm_compress_provider=summary,
            llm_compress_instruction="Summarize briefly.",
        )
        async for _ in runner.step():
            pass
        assert len(main.requests) == 1 and executor.calls == []
        return main.requests[0]["contexts"]

    binding = NodeExecutionBinding(
        run_id="run",
        team_id="team",
        member_id="member",
        node_id=None,
        umo="test:FriendMessage:native-summary",
        owner_username="test",
        config_id=None,
        begin_usage=begin_usage,
    )
    token = current_team_execution.set(binding if metered else None)
    try:
        await run_main()
        assert len(requests) == 1 and requests[0][field] == 100
        input_tokens = EstimateTokenCounter().count_tokens(
            bind_checkpoint_messages(requests[0]["messages"])
        )
        requests.clear()
        summary.provider_config["max_context_tokens"] = (
            input_tokens + 100 - int(not fits)
        )
        sent = await run_main()
        assert len(requests) == int(fits)
        if fits:
            assert requests[0][field] == 100
        assert (
            any(
                "Our previous history conversation summary: done"
                in str(message.content)
                for message in sent
            )
            is fits
        )
    finally:
        current_team_execution.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("native_openai", ["responses"], indirect=True)
@pytest.mark.parametrize("streaming", [False, True])
async def test_responses_native_limiter_honors_the_checked_cap(
    native_openai, streaming
):
    provider, requests, field = native_openai
    receipt = {
        "receipt_id": "unchanged",
        "max_output_tokens": 500,
        "transport_started": False,
    }
    original = copy.deepcopy(receipt)
    with model_delivery(receipt, output_limit=100):
        if streaming:
            async for _ in provider._query_stream(
                {"model": "test", "input": "hello"}, None
            ):
                pass
        else:
            await provider._query({"model": "test", "input": "hello"}, None)
    assert len(requests) == 1 and requests[0][field] == 100
    assert receipt == original


@pytest.mark.asyncio
@pytest.mark.parametrize("native_openai", ["chat-tools", "chat-repair"], indirect=True)
@pytest.mark.parametrize("streaming", [False, True])
async def test_auxiliary_request_delivers_its_own_final_output_limit(
    native_openai, streaming
):
    provider, requests, field = native_openai

    async def begin_usage(request):
        if len(requests) == 1:
            runner.req.llm_params["max_tokens"] = 100
        return {"receipt_id": request["receipt_id"], "max_output_tokens": 500}

    binding = NodeExecutionBinding(
        run_id="run",
        team_id="team",
        member_id="member",
        node_id=None,
        umo="test:FriendMessage:native-parameters",
        owner_username="test",
        config_id=None,
        begin_usage=begin_usage,
    )
    token = current_team_execution.set(binding)
    try:
        runner, executor = await native_runner(
            provider,
            streaming=streaming,
            tool_schema_mode="skills_like",
            request=ProviderRequest(
                prompt="task",
                llm_params={"max_tokens": 500},
                func_tool=ToolSet(
                    tools=[
                        FunctionTool(
                            name="read",
                            description="Read",
                            parameters={
                                "type": "object",
                                "properties": {"value": {"type": "string"}},
                            },
                        ),
                    ]
                ),
            ),
        )
        async for _ in runner.step():
            pass
    finally:
        current_team_execution.reset(token)
    expected_calls = (
        3
        if any(
            "Do not return an empty response." in str(request) for request in requests
        )
        else 2
    )
    assert len(requests) == expected_calls
    assert [request[field] for request in requests] == [500] + [100] * (
        expected_calls - 1
    )
    assert len(executor.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_gemini_runner_delivers_the_checked_cap_to_its_sdk(
    streaming, monkeypatch
):
    from google.genai import types
    from astrbot.core.provider.sources.gemini_source import ProviderGoogleGenAI

    provider = ProviderGoogleGenAI(
        {
            "id": "gemini-offline",
            "model": "gemini-test",
            "key": ["offline"],
            "api_base": "https://example.invalid",
            "max_context_tokens": 1000,
        },
        {},
    )
    requests = []
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text="done")]),
                finish_reason="STOP",
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=750, candidates_token_count=1, total_token_count=751
        ),
    )

    async def record(**kwargs):
        requests.append(kwargs)
        return response

    async def record_stream(**kwargs):
        requests.append(kwargs)

        async def chunks():
            yield response

        return chunks()

    monkeypatch.setattr(provider.client.models, "generate_content", record)
    monkeypatch.setattr(
        provider.client.models, "generate_content_stream", record_stream
    )

    async def begin_usage(request):
        assert request["max_output_tokens"] == 500
        runner.req.llm_params["max_tokens"] = 100
        return {"receipt_id": request["receipt_id"], "max_output_tokens": 500}

    binding = NodeExecutionBinding(
        run_id="run",
        team_id="team",
        member_id="member",
        node_id=None,
        umo="test:FriendMessage:native-gemini",
        owner_username="test",
        config_id=None,
        begin_usage=begin_usage,
    )
    token = current_team_execution.set(binding)
    try:
        runner, executor = await native_runner(
            provider,
            streaming=streaming,
            request=ProviderRequest(prompt="x" * 2500, llm_params={"max_tokens": 500}),
        )
        async for _ in runner.step():
            pass
        assert len(requests) == 1
        assert requests[0]["config"].max_output_tokens == 100
        assert runner.get_final_llm_resp().completion_text == "done"
        assert executor.calls == []
    finally:
        current_team_execution.reset(token)
        await provider.terminate()
