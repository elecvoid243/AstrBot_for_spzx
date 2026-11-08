"""Long-horizon compaction targets for a single oversized round.

The fixture mirrors a coding-agent turn: one user request followed by many
completed tool batches. The scripted summary provider returns a checkpoint
whose size is a fixed fraction of the fixture, so the test can distinguish
"one pass reaches the target" from "the target needs a second pass".
"""

import pytest

from astrbot.core.agent.context.config import ContextConfig
from astrbot.core.agent.context.manager import ContextManager
from astrbot.core.agent.message import Message, ToolCall
from astrbot.core.provider.entities import LLMResponse
from tests.agent.test_context_manager import CharTokenCounter


class ScriptedSummaryProvider:
    """Return a fixed-size summary and count how often it was asked."""

    def __init__(self, ratio: float) -> None:
        self.ratio = ratio
        self.calls = 0
        self.provider_config = {
            "id": "summary",
            "model": "summary",
            "modalities": ["text"],
        }

    async def text_chat(self, **kwargs) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            role="assistant",
            completion_text="s" * int(18_000 * self.ratio),
        )

    def get_model(self) -> str:
        return "summary"


def long_round(closed_batches: int = 9, batch_chars: int = 2_000) -> list[Message]:
    """Build one user request followed by completed tool batches."""
    messages = [Message(role="user", content="task")]
    for index in range(closed_batches):
        call_id = f"call_{index}"
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
        messages.append(
            Message(role="tool", content="x" * batch_chars, tool_call_id=call_id)
        )
    messages.append(Message(role="assistant", content="done"))
    return messages


def make_manager(provider: ScriptedSummaryProvider) -> ContextManager:
    """Create a manager with the long-horizon test budgets."""
    return ContextManager(
        ContextConfig(
            max_context_tokens=20_000,
            compress_threshold_tokens=0,
            llm_compress_keep_recent_ratio=0.15,
            llm_compress_provider=provider,  # type: ignore[arg-type]
            custom_token_counter=CharTokenCounter(),
        )
    )


@pytest.mark.asyncio
async def test_single_long_round_reaches_target_in_one_pass():
    """A strong summary must land below the target water level in one pass."""
    provider = ScriptedSummaryProvider(ratio=0.25)
    manager = make_manager(provider)

    result = await manager.process(long_round(), output_token_budget=2_000)

    assert manager.token_counter.count_tokens(result) <= 11_000
    assert manager.last_compression["passes"] == 1
    assert manager.last_compression["yield_ratio"] >= 0.5


@pytest.mark.asyncio
async def test_low_yield_summary_stops_after_two_passes():
    """A weak summary gets one retry, then reports insufficiency."""
    provider = ScriptedSummaryProvider(ratio=0.6)
    manager = make_manager(provider)

    await manager.process(long_round(), output_token_budget=2_000)

    assert provider.calls == 2
    assert manager.last_compression["insufficient"] is True
