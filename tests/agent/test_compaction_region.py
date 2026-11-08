"""Tests for tool-pair balanced compaction range selection."""

from astrbot.core.agent.context.compaction_region import (
    select_compaction_range,
    tool_pairing_balanced_before,
)
from astrbot.core.agent.message import Message, ToolCall
from tests.agent.test_context_manager import CharTokenCounter
from tests.agent.test_long_horizon_compaction import long_round


def _batch(call_id: str, size: int = 200) -> list[Message]:
    """Build one assistant tool call and its result."""
    return [
        Message(
            role="assistant",
            content="step",
            tool_calls=[
                ToolCall(
                    id=call_id,
                    function=ToolCall.FunctionBody(name="read", arguments="{}"),
                )
            ],
        ),
        Message(role="tool", content="x" * size, tool_call_id=call_id),
    ]


def test_never_cuts_between_tool_call_and_result():
    messages = [Message(role="user", content="task"), *_batch("a")]

    assert not tool_pairing_balanced_before(messages, 2)
    assert tool_pairing_balanced_before(messages, 3)


def test_compacts_closed_steps_inside_one_long_round():
    messages = long_round(closed_batches=8, batch_chars=200)
    counter = CharTokenCounter()

    selected = select_compaction_range(
        messages,
        retain_tokens=300,
        token_counter=counter,
    )

    assert selected is not None
    assert selected.start == 1  # current user request stays outside
    assert selected.end > selected.start
    assert selected.end < len(messages)
    assert tool_pairing_balanced_before(messages, selected.end)
    assert all(m.role != "user" for m in messages[selected.start : selected.end])


def test_single_giant_pair_returns_none():
    messages = [Message(role="user", content="task"), *_batch("a")]

    assert (
        select_compaction_range(
            messages,
            retain_tokens=0,
            token_counter=CharTokenCounter(),
        )
        is None
    )


def test_retention_can_consume_the_whole_history():
    messages = long_round(closed_batches=3, batch_chars=200)

    assert (
        select_compaction_range(
            messages,
            retain_tokens=10_000,
            token_counter=CharTokenCounter(),
        )
        is None
    )


def test_protected_tail_is_never_inside_the_range():
    messages = [Message(role="user", content="old"), *_batch("a", 400)]
    current = Message(role="user", content="current")
    messages.extend([current, *_batch("b", 400), *_batch("c", 400)])

    selected = select_compaction_range(
        messages,
        retain_tokens=0,
        token_counter=CharTokenCounter(),
        protected_messages=[current],
    )

    assert selected is not None
    assert all(id(m) != id(current) for m in messages[selected.start : selected.end])
