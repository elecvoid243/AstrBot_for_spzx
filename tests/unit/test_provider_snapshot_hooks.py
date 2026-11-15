"""Capture-hook tests for the four LLM provider sources.

Each provider source records the final provider-native request body through
``request_snapshots.capture`` right before the network call. The tests drive
the real ``_query``/``_query_stream`` code paths with fake SDK clients and a
spy capture implementation, so the hook position, the adapter name and the
``ToolSet`` hand-off are covered without touching the network.

Author: impl_task4
Date: 2026-11-15
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import anthropic
import pytest
from anthropic.types import Message, TextBlock, Usage
from google.genai import types as genai_types
from openai.types.chat.chat_completion import ChatCompletion
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
from openai.types.responses import Response

from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.provider import request_snapshot
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.provider.request_snapshot import (
    request_snapshot_scope,
    request_snapshots,
)
from astrbot.core.provider.sources.anthropic_source import ProviderAnthropic
from astrbot.core.provider.sources.gemini_source import ProviderGoogleGenAI
from astrbot.core.provider.sources.openai_responses_source import (
    ProviderOpenAIResponses,
)
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
from astrbot.core.provider.sources import anthropic_source, gemini_source


class CaptureSpy:
    """Stand-in for ``request_snapshots.capture`` that records its arguments."""

    def __init__(self) -> None:
        self.calls = 0
        self.raw: object | None = None
        self.adapter: str | None = None
        self.tool_set: object | None = None

    def __call__(self, provider, raw, **kwargs) -> str:  # noqa: ANN001
        self.calls += 1
        self.raw = raw
        self.adapter = kwargs.get("adapter")
        self.tool_set = kwargs.get("tool_set")
        return "snapshot-1"


def _tool_set() -> ToolSet:
    return ToolSet(
        [
            FunctionTool(
                name="read_file",
                description="Read a local file.",
                parameters={"type": "object", "properties": {}},
            )
        ]
    )


def _chat_completion() -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        }
    )


def _responses_response() -> Response:
    return Response.model_validate(
        {
            "id": "resp_1",
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": "gpt-test",
            "output": [
                {
                    "type": "message",
                    "id": "msg_1",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "ok",
                            "annotations": [],
                        }
                    ],
                }
            ],
            "usage": {
                "input_tokens": 1,
                "input_tokens_details": {
                    "cached_tokens": 0,
                    "cache_write_tokens": 0,
                },
                "output_tokens": 1,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 2,
            },
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
        }
    )


def _anthropic_message() -> Message:
    return Message(
        id="msg_1",
        content=[TextBlock(text="ok", type="text")],
        model="claude-test",
        role="assistant",
        stop_reason="end_turn",
        stop_sequence=None,
        type="message",
        usage=Usage(input_tokens=1, output_tokens=1),
    )


def _gemini_response() -> genai_types.GenerateContentResponse:
    return genai_types.GenerateContentResponse(
        candidates=[
            genai_types.Candidate(
                content=genai_types.Content(
                    role="model",
                    parts=[genai_types.Part(text="ok")],
                )
            )
        ]
    )


def _gemini_stream_chunk() -> SimpleNamespace:
    part = SimpleNamespace(
        text="ok",
        thought=None,
        function_call=None,
        inline_data=None,
        thought_signature=None,
    )
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=[part]),
                finish_reason=None,
            )
        ],
        text="ok",
        response_id="resp-1",
        usage_metadata=None,
    )


def _openai_payloads() -> dict:
    return {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
        ],
    }


def _responses_payloads() -> dict:
    return {
        "model": "gpt-test",
        "instructions": "be brief",
        "input": [{"type": "message", "role": "user", "content": "hello"}],
    }


def _anthropic_payloads() -> dict:
    return {
        "model": "claude-test",
        "system": [{"type": "text", "text": "system prompt"}],
        "messages": [{"role": "user", "content": "hello"}],
    }


def _gemini_payloads() -> dict:
    return {
        "model": "gemini-3.7-flash",
        "messages": [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
        ],
    }


def _make_openai_provider() -> ProviderOpenAIOfficial:
    return ProviderOpenAIOfficial(
        provider_config={
            "id": "test-openai",
            "type": "openai_chat_completion",
            "model": "gpt-4o-mini",
            "key": ["test-key"],
        },
        provider_settings={},
    )


async def _run_openai_chat(monkeypatch, capture) -> LLMResponse | list[LLMResponse]:
    """Run one OpenAI Chat request against a fake SDK client."""

    monkeypatch.setattr(request_snapshots, "capture", capture)
    provider = _make_openai_provider()

    async def fake_create(**kwargs):
        return _chat_completion()

    monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)
    try:
        return await provider._query(_openai_payloads(), _tool_set())
    finally:
        await provider.terminate()


async def _run_openai_chat_stream(monkeypatch, capture) -> list[LLMResponse]:
    """Run one streaming OpenAI Chat request against a fake SDK client."""

    monkeypatch.setattr(request_snapshots, "capture", capture)
    provider = _make_openai_provider()

    async def fake_stream():
        yield ChatCompletionChunk.model_validate(
            {
                "id": "chatcmpl-stream",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    async def fake_create(**kwargs):
        return fake_stream()

    monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)
    try:
        return [
            item
            async for item in provider._query_stream(_openai_payloads(), _tool_set())
        ]
    finally:
        await provider.terminate()


async def _run_openai_responses(monkeypatch, capture) -> LLMResponse:
    """Run one Responses API request against a fake SDK client."""

    monkeypatch.setattr(request_snapshots, "capture", capture)
    provider = ProviderOpenAIResponses(
        {
            "id": "test-responses",
            "provider": "openai",
            "type": "openai_responses",
            "model": "gpt-test",
            "key": ["test-key"],
            "api_base": "https://api.openai.com/v1",
        },
        {},
    )

    async def fake_create(**kwargs):
        return _responses_response()

    monkeypatch.setattr(provider.client.responses, "create", fake_create)
    try:
        return await provider._query(_responses_payloads(), _tool_set())
    finally:
        await provider.terminate()


async def _run_openai_responses_stream(monkeypatch, capture) -> list[LLMResponse]:
    """Run one streaming Responses API request against a fake SDK client."""

    monkeypatch.setattr(request_snapshots, "capture", capture)
    provider = ProviderOpenAIResponses(
        {
            "id": "test-responses",
            "provider": "openai",
            "type": "openai_responses",
            "model": "gpt-test",
            "key": ["test-key"],
            "api_base": "https://api.openai.com/v1",
        },
        {},
    )

    async def fake_stream():
        yield SimpleNamespace(
            type="response.created",
            response=SimpleNamespace(id="resp_1"),
        )
        yield SimpleNamespace(type="response.output_text.delta", delta="ok")
        yield SimpleNamespace(
            type="response.completed",
            response=_responses_response(),
        )

    async def fake_create(**kwargs):
        return fake_stream()

    monkeypatch.setattr(provider.client.responses, "create", fake_create)
    try:
        return [
            item
            async for item in provider._query_stream(
                _responses_payloads(), _tool_set()
            )
        ]
    finally:
        await provider.terminate()


def _make_anthropic_provider() -> ProviderAnthropic:
    provider = ProviderAnthropic(
        provider_config={
            "id": "test-anthropic",
            "type": "anthropic_chat_completion",
            "model": "claude-test",
            "key": ["test-key"],
        },
        provider_settings={},
    )

    class FakeMessages:
        async def create(self, **kwargs):
            return _anthropic_message()

    provider.client = SimpleNamespace(messages=FakeMessages())
    return provider


async def _run_anthropic(monkeypatch, capture) -> LLMResponse:
    """Run one Anthropic request against a fake SDK client."""

    monkeypatch.setattr(request_snapshots, "capture", capture)
    provider = _make_anthropic_provider()
    return await provider._query(_anthropic_payloads(), _tool_set())


async def _run_anthropic_stream(monkeypatch, capture) -> list[LLMResponse]:
    """Run one streaming Anthropic request against a fake SDK client."""

    class FakeStream:
        """Iterable stand-in for ``anthropic.AsyncMessageStream``."""

        def __init__(self, events: list) -> None:
            self._events = events

        def __aiter__(self):
            return self._iterate()

        async def _iterate(self):
            for event in self._events:
                yield event

    usage = SimpleNamespace(
        input_tokens=1,
        output_tokens=1,
        cache_read_input_tokens=None,
        cache_creation_input_tokens=None,
    )
    events = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(id="msg_1", usage=usage),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="text"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="ok"),
        ),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn", stop_sequence=None),
            usage=usage,
        ),
        SimpleNamespace(type="message_stop"),
    ]

    @contextlib.asynccontextmanager
    async def fake_context(provider_name, factory, max_attempts=None):
        yield FakeStream(events)

    monkeypatch.setattr(request_snapshots, "capture", capture)
    monkeypatch.setattr(
        anthropic,
        "AsyncMessageStream",
        FakeStream,
    )
    monkeypatch.setattr(
        anthropic_source,
        "retry_provider_request_context",
        fake_context,
    )
    provider = _make_anthropic_provider()
    return [
        item
        async for item in provider._query_stream(_anthropic_payloads(), _tool_set())
    ]


def _make_gemini_provider() -> ProviderGoogleGenAI:
    provider = ProviderGoogleGenAI.__new__(ProviderGoogleGenAI)
    provider.provider_config = {}
    provider.provider_settings = {}
    provider.model_name = "gemini-3.7-flash"
    provider.safety_settings = []
    provider.client = SimpleNamespace(
        models=SimpleNamespace(generate_content=lambda **kwargs: None)
    )
    return provider


async def _run_gemini(monkeypatch, capture) -> LLMResponse:
    """Run one Gemini request against a fake SDK client."""

    monkeypatch.setattr(request_snapshots, "capture", capture)
    provider = _make_gemini_provider()

    async def fake_generate_content(**kwargs):
        return _gemini_response()

    provider.client.models.generate_content = fake_generate_content
    return await provider._query(_gemini_payloads(), _tool_set())


async def _run_gemini_stream(monkeypatch, capture) -> list[LLMResponse]:
    """Run one streaming Gemini request against a fake SDK client."""

    monkeypatch.setattr(request_snapshots, "capture", capture)
    provider = _make_gemini_provider()

    async def fake_stream():
        yield _gemini_stream_chunk()

    async def fake_retry(provider_name, request_factory, max_attempts=None):
        return fake_stream()

    monkeypatch.setattr(gemini_source, "retry_provider_request", fake_retry)
    return [
        item async for item in provider._query_stream(_gemini_payloads(), _tool_set())
    ]


@pytest.mark.asyncio
async def test_openai_chat_capture_includes_payloads_and_extra_body(monkeypatch):
    """The OpenAI Chat hook captures the merged payloads/extra_body body."""

    spy = CaptureSpy()
    provider = _make_openai_provider()

    async def fake_create(**kwargs):
        return _chat_completion()

    monkeypatch.setattr(request_snapshots, "capture", spy)
    monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)
    tools = _tool_set()
    try:
        await provider._query(_openai_payloads(), tools)
    finally:
        await provider.terminate()

    assert spy.calls == 1
    assert spy.adapter == "openai_chat"
    assert spy.tool_set is tools
    assert isinstance(spy.raw, dict)
    assert spy.raw["messages"][0]["role"] == "system"
    assert spy.raw["tools"][0]["function"]["name"] == "read_file"
    assert spy.raw["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_openai_chat_capture_extra_body_overrides_payload_keys(monkeypatch):
    """extra_body wins over a same-named payload key in the captured body."""

    spy = CaptureSpy()
    provider = ProviderOpenAIOfficial(
        provider_config={
            "id": "test-openai",
            "type": "openai_chat_completion",
            "model": "gpt-4o-mini",
            "key": ["test-key"],
            "custom_extra_body": {
                "temperature": 0.25,
                "chat_template_kwargs": {"reasoning_effort": 100},
            },
        },
        provider_settings={},
    )

    async def fake_create(**kwargs):
        return _chat_completion()

    monkeypatch.setattr(request_snapshots, "capture", spy)
    monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)
    try:
        await provider._query(_openai_payloads(), _tool_set())
    finally:
        await provider.terminate()

    assert spy.raw["temperature"] == 0.25
    assert spy.raw["chat_template_kwargs"] == {"reasoning_effort": 100}


@pytest.mark.asyncio
async def test_openai_chat_stream_capture_matches_query_shape(monkeypatch):
    """The streaming OpenAI Chat hook captures the same effective body."""

    spy = CaptureSpy()
    responses = await _run_openai_chat_stream(monkeypatch, spy)

    assert spy.calls == 1
    assert spy.adapter == "openai_chat"
    assert spy.raw["messages"][0]["role"] == "system"
    assert spy.raw["tools"][0]["function"]["name"] == "read_file"
    assert responses[-1].completion_text == "ok"


@pytest.mark.asyncio
async def test_openai_responses_capture_uses_input_and_instructions(monkeypatch):
    """The Responses hook captures instructions, input and flattened tools."""

    spy = CaptureSpy()
    tools = _tool_set()
    monkeypatch.setattr(request_snapshots, "capture", spy)
    provider = ProviderOpenAIResponses(
        {
            "id": "test-responses",
            "provider": "openai",
            "type": "openai_responses",
            "model": "gpt-test",
            "key": ["test-key"],
            "api_base": "https://api.openai.com/v1",
        },
        {},
    )

    async def fake_create(**kwargs):
        return _responses_response()

    monkeypatch.setattr(provider.client.responses, "create", fake_create)
    try:
        await provider._query(_responses_payloads(), tools)
    finally:
        await provider.terminate()

    assert spy.calls == 1
    assert spy.adapter == "openai_responses"
    assert spy.tool_set is tools
    assert spy.raw["instructions"] == "be brief"
    assert spy.raw["input"][0]["role"] == "user"
    assert spy.raw["store"] is False
    assert spy.raw["tools"][0]["name"] == "read_file"


@pytest.mark.asyncio
async def test_openai_responses_stream_capture_uses_input_and_instructions(
    monkeypatch,
):
    """The streaming Responses hook captures the same effective body."""

    spy = CaptureSpy()
    responses = await _run_openai_responses_stream(monkeypatch, spy)

    assert spy.calls == 1
    assert spy.adapter == "openai_responses"
    assert spy.raw["instructions"] == "be brief"
    assert spy.raw["input"][0]["role"] == "user"
    assert responses[0].completion_text == "ok"


@pytest.mark.asyncio
async def test_anthropic_capture_uses_system_messages_and_tools(monkeypatch):
    """The Anthropic hook captures system blocks, messages, tools and limit."""

    spy = CaptureSpy()
    tools = _tool_set()
    monkeypatch.setattr(request_snapshots, "capture", spy)
    provider = _make_anthropic_provider()

    await provider._query(_anthropic_payloads(), tools)

    assert spy.calls == 1
    assert spy.adapter == "anthropic"
    assert spy.tool_set is tools
    assert spy.raw["system"][0]["text"] == "system prompt"
    assert spy.raw["messages"][0]["role"] == "user"
    assert spy.raw["tools"][0]["name"] == "read_file"
    assert isinstance(spy.raw["max_tokens"], int)
    assert "max_completion_tokens" not in spy.raw


@pytest.mark.asyncio
async def test_anthropic_stream_capture_uses_system_messages_and_tools(monkeypatch):
    """The streaming Anthropic hook captures the same effective body."""

    spy = CaptureSpy()
    responses = await _run_anthropic_stream(monkeypatch, spy)

    assert spy.calls == 1
    assert spy.adapter == "anthropic"
    assert spy.raw["system"][0]["text"] == "system prompt"
    assert spy.raw["messages"][0]["role"] == "user"
    assert responses[-1].completion_text == "ok"


@pytest.mark.asyncio
async def test_gemini_capture_uses_contents_and_config(monkeypatch):
    """The Gemini hook captures model, contents and the prepared config."""

    spy = CaptureSpy()
    tools = _tool_set()
    monkeypatch.setattr(request_snapshots, "capture", spy)
    provider = _make_gemini_provider()
    sdk_calls: dict = {}

    async def fake_generate_content(**kwargs):
        sdk_calls.update(kwargs)
        return _gemini_response()

    provider.client.models.generate_content = fake_generate_content

    await provider._query(_gemini_payloads(), tools)

    assert spy.calls == 1
    assert spy.adapter == "gemini"
    assert spy.tool_set is tools
    assert spy.raw["model"] == "gemini-3.7-flash"
    assert spy.raw["contents"] is sdk_calls["contents"]
    assert spy.raw["config"] is sdk_calls["config"]
    assert isinstance(spy.raw["config"], genai_types.GenerateContentConfig)


@pytest.mark.asyncio
async def test_gemini_stream_capture_uses_contents_and_config(monkeypatch):
    """The streaming Gemini hook captures the same model/contents/config body."""

    spy = CaptureSpy()
    responses = await _run_gemini_stream(monkeypatch, spy)

    assert spy.calls == 1
    assert spy.adapter == "gemini"
    assert spy.raw["model"] == "gemini-3.7-flash"
    assert isinstance(spy.raw["config"], genai_types.GenerateContentConfig)
    assert responses[-1].completion_text == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runner", "stream"),
    [
        (_run_openai_chat, False),
        (_run_openai_chat_stream, True),
        (_run_openai_responses, False),
        (_run_openai_responses_stream, True),
        (_run_anthropic, False),
        (_run_anthropic_stream, True),
        (_run_gemini, False),
        (_run_gemini_stream, True),
    ],
    ids=[
        "openai-chat",
        "openai-chat-stream",
        "openai-responses",
        "openai-responses-stream",
        "anthropic",
        "anthropic-stream",
        "gemini",
        "gemini-stream",
    ],
)
async def test_capture_failure_does_not_break_the_request(monkeypatch, runner, stream):
    """A raising capture hook is isolated from the provider request path."""

    calls: list[str] = []

    def exploding_capture(provider, raw, **kwargs):
        calls.append("capture")
        raise RuntimeError("boom")

    result = await runner(monkeypatch, exploding_capture)

    assert calls == ["capture"]
    if stream:
        assert isinstance(result, list) and result
    else:
        assert isinstance(result, LLMResponse)


@pytest.mark.asyncio
async def test_capture_is_a_noop_when_developer_mode_disabled(monkeypatch):
    """Disabled developer mode records nothing and keeps the request intact."""

    request_snapshots.reset()
    monkeypatch.setattr(request_snapshot, "developer_mode_enabled", lambda: False)

    with request_snapshot_scope(
        session_id="snapshot-hooks",
        umo="webchat:FriendMessage:snapshot-hooks",
    ):
        result = await _run_openai_chat(monkeypatch, request_snapshots.capture)

    assert result.role == "assistant"
    assert request_snapshots.list_for_session("snapshot-hooks")["items"] == []
