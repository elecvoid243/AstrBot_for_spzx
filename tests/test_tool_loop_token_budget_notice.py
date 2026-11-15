"""One-shot token budget notices injected into the model-visible history."""

from types import SimpleNamespace

from astrbot.core.agent.context.compaction_region import select_compaction_range
from astrbot.core.agent.message import Message, WindowState
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner


class CharTokenCounter:
    def count_tokens(self, messages, trusted_token_usage: int = 0) -> int:
        return sum(len(m.content) for m in messages if isinstance(m.content, str))


def runner_factory(
    *,
    remaining=None,
    reminder_tokens=8192,
    strategy="token_budget",
    window_state=None,
    last_compression=None,
):
    """Build a runner with only the compression state the injector reads."""
    runner = object.__new__(ToolLoopAgentRunner)
    runner.window_state = window_state or WindowState(id="w1")
    runner.request_context_manager = SimpleNamespace(
        config=SimpleNamespace(strategy=strategy, reminder_tokens=reminder_tokens),
        last_compression=last_compression,
        last_remaining_tokens=remaining,
    )
    return runner


def test_notice_injected_once_per_window():
    runner = runner_factory(remaining=100)

    first = runner._inject_budget_notices([Message(role="user", content="hi")])
    second = runner._inject_budget_notices(first)

    assert len(first) == 2
    assert first[-1]._internal is True
    assert "100 tokens left" in first[-1].content
    assert len(second) == 2
    assert runner.window_state.reminder_sent is True


def test_exhausted_notice_wins_over_the_reminder():
    runner = runner_factory(
        remaining=0,
        last_compression={"strategy": "token_budget", "insufficient": True},
    )

    injected = runner._inject_budget_notices([Message(role="user", content="hi")])

    assert injected[-1]._internal is True
    assert "cannot be trimmed further" in injected[-1].content
    assert runner.window_state.exhausted_sent is True
    assert runner.window_state.reminder_sent is False


def test_llm_compress_strategy_receives_no_notice():
    runner = runner_factory(remaining=0, strategy="llm_compress")
    messages = [Message(role="user", content="hi")]

    assert runner._inject_budget_notices(messages) == messages


def test_range_selector_ignores_internal_notice():
    notice = Message(role="user", content="[context budget] ...")
    notice._internal = True
    history = [
        Message(role="user", content="old"),
        Message(role="assistant", content="a"),
        notice,
    ]

    selected = select_compaction_range(
        history, retain_tokens=0, token_counter=CharTokenCounter()
    )

    assert selected is not None
    # The notice must not be mistaken for the current request: the real user
    # message stays outside the compactable range.
    assert selected.start == 1
    assert selected.end == 2
