"""Unit tests for the per-request thinking-effort mapping in provider sources.

The canonical `thinking_effort` value (auto/off/low/medium/high) travels inside
`ProviderRequest.llm_params` and is mapped to provider-native request fields:
- OpenAI (chat completions / responses): `reasoning_effort`
- Anthropic: `thinking: {type: adaptive}` + `output_config.effort`
- Gemini: `thinking_level` (3.x) / `thinking_budget` (2.5)

Tests instantiate providers with fake SDK clients; no network is touched.
"""

from types import SimpleNamespace

import pytest

import astrbot.core.provider.sources.anthropic_source as anthropic_source
import astrbot.core.provider.sources.gemini_source as gemini_source
import astrbot.core.provider.sources.openai_source as openai_source


class _FakeAsyncAnthropic:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def close(self):
        return None


class _FakeCompletionsCreate:
    async def create(self, **kwargs):
        return None


class _FakeChatCompletions:
    create = _FakeCompletionsCreate().create


class _FakeChat:
    completions = _FakeChatCompletions()


class _FakeAsyncOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.base_url = SimpleNamespace(host="api.openai.com")
        self.chat = _FakeChat()


class _FakeGenAIClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        # the source uses genai.Client(...).aio as the async client handle
        self.aio = self
        # ...and drops the SDK's own user-agent header through the transport
        self._api_client = SimpleNamespace(_http_options=SimpleNamespace(headers={}))

    async def close(self):
        return None


def _make_anthropic_provider(monkeypatch, anth_thinking_config: dict | None = None):
    monkeypatch.setattr(anthropic_source, "AsyncAnthropic", _FakeAsyncAnthropic)
    provider_config = {
        "id": "anthropic-test",
        "type": "anthropic_chat_completion",
        "model": "claude-test",
        "key": ["test-key"],
    }
    if anth_thinking_config:
        provider_config["anth_thinking_config"] = anth_thinking_config
    return anthropic_source.ProviderAnthropic(provider_config, provider_settings={})


def _make_openai_provider(monkeypatch, provider_config_extra: dict | None = None):
    monkeypatch.setattr(openai_source, "AsyncOpenAI", _FakeAsyncOpenAI)
    provider_config = {
        "id": "openai-test",
        "type": "openai_chat_completion",
        "model": "gpt-test",
        "key": ["test-key"],
    }
    if provider_config_extra:
        provider_config.update(provider_config_extra)
    return openai_source.ProviderOpenAIOfficial(provider_config, provider_settings={})


def _make_gemini_provider(monkeypatch, provider_config_extra: dict | None = None):
    monkeypatch.setattr(gemini_source.genai, "Client", _FakeGenAIClient)
    provider_config = {
        "id": "gemini-test",
        "type": "google_genai_chat_completion",
        "model": "gemini-3-flash",
        "key": ["test-key"],
        "api_base": "https://generativelanguage.googleapis.com",
    }
    if provider_config_extra:
        provider_config.update(provider_config_extra)
    return gemini_source.ProviderGoogleGenAI(provider_config, provider_settings={})


# ---------------------------------------------------------------- Anthropic


def test_anthropic_thinking_effort_override_applies(monkeypatch):
    provider = _make_anthropic_provider(
        monkeypatch,
        {"type": "adaptive", "effort": "medium"},
    )
    payloads: dict = {}
    provider._apply_thinking_config(payloads, {"thinking_effort": "high"})
    assert payloads["thinking"] == {"type": "adaptive"}
    assert payloads["output_config"]["effort"] == "high"


def test_anthropic_thinking_effort_off_clears_config(monkeypatch):
    provider = _make_anthropic_provider(
        monkeypatch,
        {"type": "adaptive", "effort": "medium"},
    )
    payloads: dict = {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "medium"},
    }
    provider._apply_thinking_config(payloads, {"thinking_effort": "off"})
    assert "thinking" not in payloads
    assert "output_config" not in payloads


def test_anthropic_static_config_used_without_llm_params(monkeypatch):
    provider = _make_anthropic_provider(
        monkeypatch,
        {"type": "adaptive", "effort": "medium"},
    )
    for llm_params in (
        None,
        {},
        {"thinking_effort": "auto"},
        {"thinking_effort": "bogus"},
        {"thinking_effort": "max"},
        {"thinking_effort": "xhigh"},
    ):
        payloads: dict = {}
        provider._apply_thinking_config(payloads, llm_params)
        assert payloads["thinking"] == {"type": "adaptive"}
        assert payloads["output_config"]["effort"] == "medium"


# ---------------------------------------------------------------- OpenAI


@pytest.mark.asyncio
async def test_openai_thinking_effort_maps_to_reasoning_effort(monkeypatch):
    provider = _make_openai_provider(monkeypatch)

    # free-form passthrough: values differ per model / inference engine
    for effort in ("high", "max", "xhigh", "none"):
        payloads, _ = await provider._prepare_chat_payload(
            prompt="hello",
            contexts=[],
            llm_params={"thinking_effort": effort},
        )
        assert payloads["reasoning_effort"] == effort
        # the canonical key itself must never leak into the upstream payload
        assert "thinking_effort" not in payloads

    for llm_params in (
        None,
        {},
        {"thinking_effort": "auto"},
        {"thinking_effort": "off"},
        {"thinking_effort": ""},
    ):
        payloads, _ = await provider._prepare_chat_payload(
            prompt="hello",
            contexts=[],
            llm_params=llm_params,
        )
        assert "reasoning_effort" not in payloads


def test_openai_ollama_override_preserves_explicit_reasoning_effort(monkeypatch):
    provider = _make_openai_provider(
        monkeypatch,
        {"provider": "ollama", "ollama_disable_thinking": True},
    )
    # explicit per-request effort is kept (it lives in payloads for the SDK)
    extra_body: dict = {}
    provider._apply_provider_specific_request_overrides(
        {"reasoning_effort": "high"}, extra_body
    )
    assert extra_body.get("reasoning_effort") is None

    # explicit custom_extra_body effort is kept too
    extra_body = {"reasoning_effort": "low"}
    provider._apply_provider_specific_request_overrides({}, extra_body)
    assert extra_body["reasoning_effort"] == "low"

    # fallback: force "none" only when no explicit effort exists
    extra_body = {}
    provider._apply_provider_specific_request_overrides({}, extra_body)
    assert extra_body["reasoning_effort"] == "none"


# ---------------------------------------------------------------- Gemini


@pytest.mark.asyncio
async def test_gemini_3_thinking_effort_maps_to_level(monkeypatch):
    provider = _make_gemini_provider(monkeypatch)

    config = await provider._prepare_query_config(
        {"model": "gemini-3-flash"},
        llm_params={"thinking_effort": "high"},
    )
    assert (
        config.thinking_config.thinking_level == gemini_source.types.ThinkingLevel.HIGH
    )

    config = await provider._prepare_query_config(
        {"model": "gemini-3-flash"},
        llm_params={"thinking_effort": "off"},
    )
    assert (
        config.thinking_config.thinking_level
        == gemini_source.types.ThinkingLevel.MINIMAL
    )

    config = await provider._prepare_query_config(
        {"model": "gemini-3-flash"},
        llm_params={"thinking_effort": "low"},
    )
    assert (
        config.thinking_config.thinking_level == gemini_source.types.ThinkingLevel.LOW
    )

    # unknown custom values (e.g. "max" / "xhigh") are not sent to Gemini:
    # they fall back to the static provider config (default HIGH).
    config = await provider._prepare_query_config(
        {"model": "gemini-3-flash"},
        llm_params={"thinking_effort": "max"},
    )
    assert (
        config.thinking_config.thinking_level == gemini_source.types.ThinkingLevel.HIGH
    )


@pytest.mark.asyncio
async def test_gemini_2_5_thinking_effort_maps_to_budget(monkeypatch):
    provider = _make_gemini_provider(monkeypatch)

    config = await provider._prepare_query_config(
        {"model": "gemini-2.5-flash"},
        llm_params={"thinking_effort": "off"},
    )
    assert config.thinking_config.thinking_budget == 0

    config = await provider._prepare_query_config(
        {"model": "gemini-2.5-flash"},
        llm_params={"thinking_effort": "high"},
    )
    assert config.thinking_config.thinking_budget == 16384

    # no override → static config default (budget 0)
    config = await provider._prepare_query_config({"model": "gemini-2.5-flash"})
    assert config.thinking_config.thinking_budget == 0

    # unknown custom values are not sent to Gemini 2.5 either
    config = await provider._prepare_query_config(
        {"model": "gemini-2.5-flash"},
        llm_params={"thinking_effort": "xhigh"},
    )
    assert config.thinking_config.thinking_budget == 0
