"""Keep system-separated results in requests delivered by the real provider."""

import copy

import pytest
from openai.types.chat.chat_completion import ChatCompletion
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk

from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.message import Message
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
from tests.agent.test_truncator import pairing_batch, pairing_result
from tests.test_tool_loop_agent_runner import PairingToolExecutor


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("reversed_results", [False, True])
async def test_system_separated_results_reach_openai_sdk_without_replay(
    monkeypatch, streaming, reversed_results
):
    provider = ProviderOpenAIOfficial(
        {"id": "system-pairing", "model": "gpt-4o-mini", "key": ["offline-key"]},
        {},
    )
    requests = []

    async def record_sdk_request(**kwargs):
        """Record the native SDK payload and return a typed offline response.

        Args:
            **kwargs: Payload produced by the real OpenAI provider adapter.

        Returns:
            A completed SDK response or its typed chunk stream.
        """
        requests.append(copy.deepcopy(kwargs))
        response = {
            "id": "system-pairing-response",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "done"},
                    "finish_reason": "stop",
                }
            ],
        }
        if not kwargs["stream"]:
            return ChatCompletion.model_validate(response)

        async def chunks():
            yield ChatCompletionChunk.model_validate(
                {
                    **response,
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "done"},
                            "finish_reason": None,
                        }
                    ],
                }
            )
            yield ChatCompletionChunk.model_validate(
                {
                    **response,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
            )

        return chunks()

    monkeypatch.setattr(provider.client.chat.completions, "create", record_sdk_request)
    ids = ["B", "A"] if reversed_results else ["A", "B"]
    results = {"A": "real A - read completed", "B": "real B - write succeeded"}
    history = [
        Message(role="user", content="finish the existing task"),
        pairing_batch("A", "B"),
        pairing_result(ids[0], results[ids[0]]),
        Message(role="system", content="system injection"),
        pairing_result(ids[1], results[ids[1]]),
    ]
    original = copy.deepcopy(history)
    request = ProviderRequest(
        contexts=[message.model_dump() for message in history],
        func_tool=ToolSet(
            tools=[
                FunctionTool(
                    name="probe",
                    description="Existing call",
                    parameters={"type": "object", "properties": {}},
                )
            ]
        ),
    )
    executor = PairingToolExecutor()
    runner = ToolLoopAgentRunner()
    try:
        await runner.reset(
            provider=provider,
            request=request,
            run_context=ContextWrapper(context=None),
            tool_executor=executor,
            agent_hooks=BaseAgentRunHooks(),
            streaming=streaming,
        )
        async for _ in runner.step_until_done(2):
            pass

        assert runner.get_final_llm_resp().completion_text == "done"
        assert len(requests) == 1
        messages = requests[0]["messages"]
        assert [message["role"] for message in messages] == [
            "user",
            "assistant",
            "tool",
            "tool",
            "system",
        ]
        assert [
            (message["tool_call_id"], message["content"])
            for message in messages
            if message["role"] == "tool"
        ] == [(call_id, results[call_id]) for call_id in ids]
        assert messages[-1]["content"] == "system injection"
        assert executor.calls == []
        assert history == original
        assert request.contexts == [message.model_dump() for message in original]
    finally:
        await provider.terminate()
