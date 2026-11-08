"""Tests for the range-based LLM summary path."""

from types import SimpleNamespace

import pytest

from astrbot.core.agent.context.compaction_region import select_compaction_range
from astrbot.core.agent.context.compaction_spec import resolve_compaction_spec
from astrbot.core.agent.context.compressor import (
    CHECKPOINT_PREAMBLE,
    LLMSummaryCompressor,
)
from astrbot.core.agent.message import Message, ToolCall
from astrbot.core.provider.entities import LLMResponse
from tests.agent.test_context_manager import CharTokenCounter
from tests.agent.test_long_horizon_compaction import (
    ScriptedSummaryProvider,
    long_round,
)


class TruncatedSummaryProvider(ScriptedSummaryProvider):
    """Return a summary that hit the provider's output cap."""

    async def text_chat(self, **kwargs) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            role="assistant",
            completion_text="s" * 100,
            raw_completion=SimpleNamespace(
                choices=[SimpleNamespace(finish_reason="length")]
            ),
        )


def _spec(min_yield_ratio: float = 0.0):
    spec = resolve_compaction_spec(
        context_window=20_000,
        output_budget=2_000,
        headroom_tokens=2_000,
        min_yield_ratio=min_yield_ratio,
    )
    assert spec is not None
    return spec


def _full_history() -> list[Message]:
    """One old round plus the current request, with no current-round steps."""
    messages = [
        Message(role="system", content="s"),
        Message(role="user", content="old task"),
    ]
    for call_id in ("a", "b"):
        messages.append(
            Message(
                role="assistant",
                content="step",
                tool_calls=[
                    ToolCall(
                        id=call_id,
                        function=ToolCall.FunctionBody(name="read", arguments="{}"),
                    )
                ],
            )
        )
        messages.append(Message(role="tool", content="x" * 2_000, tool_call_id=call_id))
    messages.append(Message(role="user", content="current"))
    return messages


@pytest.mark.asyncio
async def test_range_summary_replaces_with_one_user_checkpoint():
    messages = _full_history()
    selected = select_compaction_range(
        messages, retain_tokens=0, token_counter=CharTokenCounter()
    )
    assert selected is not None

    provider = ScriptedSummaryProvider(ratio=0.05)
    compressor = LLMSummaryCompressor(
        provider=provider,  # type: ignore[arg-type]
        token_counter=CharTokenCounter(),
    )
    result = await compressor(messages, spec=_spec(), selected_range=selected)

    checkpoints = [m for m in result if m.compaction_id]
    assert len(checkpoints) == 1
    assert checkpoints[0].role == "user"
    assert CHECKPOINT_PREAMBLE in checkpoints[0].content
    assert "<compacted-summary>" in checkpoints[0].content
    assert result[-1] == messages[-1]


@pytest.mark.asyncio
async def test_range_summary_keeps_in_round_checkpoint_as_assistant():
    messages = long_round(closed_batches=8, batch_chars=2_000)
    selected = select_compaction_range(
        messages, retain_tokens=2_880, token_counter=CharTokenCounter()
    )
    assert selected is not None

    compressor = LLMSummaryCompressor(
        provider=ScriptedSummaryProvider(ratio=0.05),  # type: ignore[arg-type]
        token_counter=CharTokenCounter(),
    )
    result = await compressor(messages, spec=_spec(), selected_range=selected)

    checkpoints = [m for m in result if m.compaction_id]
    assert len(checkpoints) == 1
    assert checkpoints[0].role == "assistant"
    assert result[0] == messages[0]  # current user request stays exact


@pytest.mark.asyncio
async def test_truncated_summary_is_rejected():
    messages = _full_history()
    selected = select_compaction_range(
        messages, retain_tokens=0, token_counter=CharTokenCounter()
    )
    assert selected is not None

    compressor = LLMSummaryCompressor(
        provider=TruncatedSummaryProvider(ratio=0.05),  # type: ignore[arg-type]
        token_counter=CharTokenCounter(),
    )
    result = await compressor(messages, spec=_spec(), selected_range=selected)

    assert result is messages


@pytest.mark.asyncio
async def test_min_yield_rejects_an_oversized_checkpoint():
    messages = _full_history()
    selected = select_compaction_range(
        messages, retain_tokens=0, token_counter=CharTokenCounter()
    )
    assert selected is not None

    compressor = LLMSummaryCompressor(
        provider=ScriptedSummaryProvider(ratio=0.5),  # type: ignore[arg-type]
        token_counter=CharTokenCounter(),
    )
    result = await compressor(messages, spec=_spec(0.9), selected_range=selected)

    assert result is messages
