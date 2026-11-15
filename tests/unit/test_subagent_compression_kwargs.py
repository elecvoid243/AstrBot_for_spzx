"""Subagent compression kwargs follow the session's overflow strategy."""

from unittest.mock import MagicMock

from astrbot.core.astr_agent_tool_exec import build_subagent_compression_kwargs
from astrbot.core.provider.provider import Provider


class _Ctx:
    def __init__(self, providers):
        self._providers = providers

    def get_provider_by_id(self, provider_id):
        return self._providers.get(provider_id)


def test_token_budget_skips_summary_provider():
    ctx = _Ctx({"p1": MagicMock(spec=Provider)})

    kwargs = build_subagent_compression_kwargs(
        {
            "context_limit_reached_strategy": "token_budget",
            "llm_compress_provider_id": "p1",
            "llm_compress_keep_recent_ratio": 0.15,
        },
        ctx,
        "p2",
    )

    assert kwargs == {"context_limit_reached_strategy": "token_budget"}


def test_llm_compress_resolves_the_configured_provider_first():
    provider = MagicMock(spec=Provider)
    ctx = _Ctx({"p1": provider, "p2": MagicMock(spec=Provider)})

    kwargs = build_subagent_compression_kwargs(
        {
            "context_limit_reached_strategy": "llm_compress",
            "llm_compress_provider_id": "p1",
            "llm_compress_keep_recent_ratio": 0.2,
        },
        ctx,
        "p2",
    )

    assert kwargs["llm_compress_provider"] is provider
    assert kwargs["llm_compress_keep_recent_ratio"] == 0.2


def test_llm_compress_falls_back_to_the_subagent_provider():
    fallback = MagicMock(spec=Provider)
    ctx = _Ctx({"p2": fallback})

    kwargs = build_subagent_compression_kwargs(
        {
            "context_limit_reached_strategy": "llm_compress",
            "llm_compress_provider_id": "",
            "llm_compress_keep_recent_ratio": 0.15,
        },
        ctx,
        "p2",
    )

    assert kwargs["llm_compress_provider"] is fallback
