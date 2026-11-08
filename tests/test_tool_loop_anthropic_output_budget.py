"""Compare runner reservations with actual offline Anthropic SDK requests."""

import copy
import json

import anthropic
import pytest
import pytest_asyncio
from anthropic import _base_client as anthropic_base_client

from astrbot.core.agent.context.token_counter import EstimateTokenCounter
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.message import bind_checkpoint_messages
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent_team_execution import (
    NodeExecutionBinding,
    current_team_execution,
)
from astrbot.core.exceptions import ContextCapacityError
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.provider.sources.anthropic_source import ProviderAnthropic
from tests.test_tool_loop_context_budget import BudgetProvider, BudgetToolExecutor


@pytest_asyncio.fixture
async def native_anthropic():
    """Record native SDK JSON without making network requests.

    Yields:
        Real Anthropic provider and its recorded native request bodies.
    """
    requests = []
    sdk_httpx = getattr(
        anthropic_base_client, "httpx", getattr(anthropic_base_client, "httpx2", None)
    )
    message = {
        "id": "offline",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": "done"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 630, "output_tokens": 1},
    }

    def record(request):
        """Respond to the real SDK's serialized JSON or streaming request.

        Args:
            request: Native HTTP request intercepted by the offline transport.

        Returns:
            SDK-compatible JSON or server-sent events without network access.
        """
        payload = json.loads(request.content)
        requests.append(payload)
        if not payload.get("stream"):
            return sdk_httpx.Response(200, json=message)
        events = [
            {
                "type": "message_start",
                "message": {**message, "content": [], "stop_reason": None},
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "done"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
            {"type": "message_stop"},
        ]
        return sdk_httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join(
                f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                for event in events
            ),
        )

    provider = ProviderAnthropic(
        {
            "id": "offline-anthropic",
            "model": "claude-test",
            "max_context_tokens": 1000,
            "modalities": ["text", "tool_use"],
        },
        {},
        use_api_key=False,
    )
    provider.client = anthropic.AsyncAnthropic(
        api_key="offline",
        max_retries=0,
        timeout=60,
        http_client=sdk_httpx.AsyncClient(transport=sdk_httpx.MockTransport(record)),
    )
    try:
        yield provider, requests
    finally:
        await provider.client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_explicit_output_limit_reaches_native_request(
    native_anthropic, streaming
):
    provider, requests = native_anthropic
    runner = ToolLoopAgentRunner()
    executor = BudgetToolExecutor("unused")
    await runner.reset(
        provider=provider,
        request=ProviderRequest(prompt="x" * 2500, llm_params={"max_tokens": 100}),
        run_context=ContextWrapper(context=None),
        tool_executor=executor,
        agent_hooks=BaseAgentRunHooks(),
        streaming=streaming,
    )
    async for _ in runner.step():
        pass
    assert len(requests) == 1
    assert requests[0]["max_tokens"] == 100
    assert runner.get_final_llm_resp().completion_text == "done"
    assert executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("fits", [False, True])
@pytest.mark.parametrize(
    "params,overrides,admitted,expected",
    [
        ({"max_tokens": 100}, {}, None, 100),
        ({"max_completion_tokens": 100}, {}, None, 100),
        ({"max_tokens": 100}, {"max_tokens": 200}, None, 200),
        ({"max_tokens": 200}, {"max_tokens": 100}, None, 100),
        ({}, {"max_completion_tokens": 100}, None, 100),
        ({}, {}, None, 65536),
        ({"max_tokens": 70000}, {}, None, 70000),
        ({}, {"max_tokens": 70000}, None, 70000),
        ({"max_tokens": 100}, {"max_tokens": 200}, 500, 100),
        ({"max_tokens": 200}, {"max_tokens": 100}, 500, 100),
        ({"max_tokens": 500}, {"max_tokens": 400}, 200, 200),
        (
            {"max_completion_tokens": 100, "max_tokens": 200},
            {"max_tokens": 300},
            500,
            100,
        ),
        ({}, {}, 70000, 70000),
    ],
)
async def test_runner_capacity_boundary_matches_the_native_limit(
    native_anthropic, streaming, fits, params, overrides, admitted, expected
):
    provider, requests = native_anthropic
    provider.provider_config.update(
        max_context_tokens=750 + expected - int(not fits),
        custom_extra_body=copy.deepcopy(overrides),
    )
    original_config = copy.deepcopy(provider.provider_config)
    original_params = copy.deepcopy(params)

    async def begin_usage(request):
        return {"receipt_id": request["receipt_id"], "max_output_tokens": admitted}

    binding = NodeExecutionBinding(
        run_id="run",
        team_id="team",
        member_id="member",
        node_id=None,
        umo="test:FriendMessage:native-budget",
        owner_username="test",
        config_id=None,
        begin_usage=begin_usage,
    )
    token = current_team_execution.set(binding if admitted else None)
    executor = BudgetToolExecutor("unused")
    try:
        runner = ToolLoopAgentRunner()
        await runner.reset(
            provider=provider,
            request=ProviderRequest(
                prompt="x" * 2500, llm_params=copy.deepcopy(params)
            ),
            run_context=ContextWrapper(context=None),
            tool_executor=executor,
            agent_hooks=BaseAgentRunHooks(),
            streaming=streaming,
        )
        if fits:
            async for _ in runner.step():
                pass
            assert len(requests) == 1
            assert requests[0]["max_tokens"] == expected
            assert "max_completion_tokens" not in requests[0]
            assert runner.get_final_llm_resp().completion_text == "done"
        else:
            with pytest.raises(ContextCapacityError):
                async for _ in runner.step():
                    pass
            assert requests == []
        assert provider.provider_config == original_config
        assert runner.req.llm_params == original_params
        assert executor.calls == []
    finally:
        current_team_execution.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("fits", [False, True])
@pytest.mark.parametrize(
    "params,overrides,admitted,expected",
    [
        ({"max_tokens": 100}, {}, None, 100),
        ({"max_completion_tokens": 100}, {}, None, 100),
        ({"max_tokens": 100}, {"max_tokens": 200}, None, 200),
        ({}, {}, None, 65536),
        ({"max_tokens": 100}, {"max_tokens": 200}, 500, 100),
        ({"max_tokens": 200}, {"max_tokens": 100}, 500, 100),
        ({}, {}, 70000, 70000),
    ],
)
async def test_runner_summary_checks_the_same_native_output_limit(
    native_anthropic, fits, params, overrides, admitted, expected
):
    summary, requests = native_anthropic
    summary.provider_config.update(
        max_context_tokens=1000000, custom_extra_body=copy.deepcopy(overrides)
    )

    async def begin_usage(request):
        limit = admitted if request["name"] == "offline-anthropic" else 50
        return {"receipt_id": request["receipt_id"], "max_output_tokens": limit}

    async def run_main():
        """Trigger automatic compression through the actual runner.

        Returns:
            Main model input after summarization or the existing truncation fallback.
        """
        main = BudgetProvider(
            [LLMResponse(role="assistant", completion_text="main done")],
            max_context_tokens=1000,
        )
        executor = BudgetToolExecutor("unused")
        runner = ToolLoopAgentRunner()
        await runner.reset(
            provider=main,
            request=ProviderRequest(
                prompt="current task",
                llm_params=copy.deepcopy(params),
                contexts=[
                    {"role": "user", "content": "old user " * 200},
                    {"role": "assistant", "content": "old answer " * 200},
                ],
            ),
            run_context=ContextWrapper(context=None),
            tool_executor=executor,
            agent_hooks=BaseAgentRunHooks(),
            llm_compress_provider=summary,
        )
        async for _ in runner.step():
            pass
        assert len(main.requests) == 1
        assert executor.calls == []
        return main.requests[0]["contexts"]

    binding = NodeExecutionBinding(
        run_id="run",
        team_id="team",
        member_id="member",
        node_id=None,
        umo="test:FriendMessage:summary-budget",
        owner_username="test",
        config_id=None,
        begin_usage=begin_usage,
    )
    token = current_team_execution.set(binding if admitted else None)
    try:
        await run_main()
        assert len(requests) == 1
        assert requests[0]["max_tokens"] == expected
        summary_input = EstimateTokenCounter().count_tokens(
            bind_checkpoint_messages(requests[0]["messages"])
        )
        requests.clear()
        summary.provider_config["max_context_tokens"] = (
            summary_input + expected - int(not fits)
        )
        sent = await run_main()
        assert len(requests) == int(fits)
        if fits:
            assert requests[0]["max_tokens"] == expected
        assert (
            any(
                "This is an automatically generated checkpoint" in str(message.content)
                for message in sent
            )
            is fits
        )
        assert summary.provider_config["custom_extra_body"] == overrides
    finally:
        current_team_execution.reset(token)
