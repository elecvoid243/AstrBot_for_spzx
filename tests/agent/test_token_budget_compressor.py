"""Token budget compressor: window reset without an LLM request."""

import pytest

from astrbot.core.agent.context.compressor import TokenBudgetCompressor
from astrbot.core.agent.context.config import ContextConfig
from astrbot.core.agent.context.manager import ContextManager
from astrbot.core.agent.message import Message
from astrbot.core.exceptions import ContextCapacityError


class CharTokenCounter:
    def count_tokens(self, messages, trusted_token_usage: int = 0) -> int:
        return sum(len(m.content) for m in messages if isinstance(m.content, str))


@pytest.mark.asyncio
async def test_compressor_resets_and_reports_shadowed_tokens():
    compressor = TokenBudgetCompressor(CharTokenCounter())
    messages = [
        Message(role="system", content="sys"),
        Message(role="user", content="x" * 40),
        Message(role="assistant", content="y" * 40),
        Message(role="user", content="z" * 4),
    ]
    result = await compressor(messages)
    assert [m.content for m in result] == ["sys", "z" * 4]
    assert compressor.last_shadowed_tokens == 80
    assert compressor.supports_retries is False
    assert compressor.strategy == "token_budget"


@pytest.mark.asyncio
async def test_single_round_over_window_still_raises_capacity_error():
    manager = ContextManager(
        ContextConfig(
            max_context_tokens=100,
            threshold_ratio=0.8,
            headroom_tokens=0,
            strategy="token_budget",
            custom_token_counter=CharTokenCounter(),
        )
    )
    with pytest.raises(ContextCapacityError):
        await manager.process([Message(role="user", content="x" * 200)])
