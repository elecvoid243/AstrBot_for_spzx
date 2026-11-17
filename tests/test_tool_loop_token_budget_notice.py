"""Budget water-level notices injected into the model-visible history."""

from types import SimpleNamespace

from astrbot.core.agent.context.compaction_region import select_compaction_range
from astrbot.core.agent.message import Message, WindowState
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner


class CharTokenCounter:
    def count_tokens(self, messages, trusted_token_usage: int = 0) -> int:
        return sum(len(m.content) for m in messages if isinstance(m.content, str))


def runner_factory(
    *,
    usage=None,
    strategy="token_budget",
    window_state=None,
    last_compression=None,
):
    """Build a runner with only the compression state the injector reads."""
    runner = object.__new__(ToolLoopAgentRunner)
    runner.window_state = window_state or WindowState(id="w1")
    runner._window_just_reset = False
    runner.request_context_manager = SimpleNamespace(
        config=SimpleNamespace(strategy=strategy),
        last_compression=last_compression,
        last_budget_usage=usage,
    )
    return runner


def test_planning_notice_injected_once_at_half_budget():
    runner = runner_factory(usage=0.5)

    first = runner._inject_budget_notices([Message(role="user", content="hi")])
    second = runner._inject_budget_notices(first)

    assert len(first) == 2
    assert first[-1].internal is True
    assert first[-1].content.startswith("[SYSTEM NOTICE]")
    assert "50%" in first[-1].content
    assert len(second) == 2
    assert runner.window_state.notice_level == 50


def test_early_notice_at_a_quarter_budget():
    runner = runner_factory(usage=0.3)

    injected = runner._inject_budget_notices([Message(role="user", content="hi")])

    assert injected[-1].internal is True
    assert injected[-1].content.startswith("[SYSTEM NOTICE]")
    assert "25%" in injected[-1].content
    assert runner.window_state.notice_level == 25


def test_wrap_up_notice_follows_the_planning_one():
    runner = runner_factory(usage=0.5)
    first = runner._inject_budget_notices([Message(role="user", content="hi")])

    runner.request_context_manager.last_budget_usage = 0.9
    second = runner._inject_budget_notices(
        first
        + [Message(role="assistant", content="a"), Message(role="user", content="next")]
    )

    assert second[-1].internal is True
    assert "85%" in second[-1].content
    assert runner.window_state.notice_level == 85


def test_skipping_a_level_does_not_backfill_it():
    runner = runner_factory(usage=0.9)
    first = runner._inject_budget_notices([Message(role="user", content="hi")])
    assert "85%" in first[-1].content

    # A window that dropped back under 50% must not announce the lower level.
    runner.request_context_manager.last_budget_usage = 0.4
    second = runner._inject_budget_notices(
        first
        + [Message(role="assistant", content="a"), Message(role="user", content="next")]
    )

    assert len(second) == len(first) + 2
    assert runner.window_state.notice_level == 85


def test_exhausted_notice_wins_over_the_water_level():
    runner = runner_factory(
        usage=0.9,
        last_compression={
            "strategy": "token_budget",
            "insufficient": True,
            "tokens_after": 1200,
            "threshold_tokens": 1000,
        },
    )

    injected = runner._inject_budget_notices([Message(role="user", content="hi")])

    assert injected[-1].internal is True
    assert "cannot be trimmed further" in injected[-1].content
    assert runner.window_state.exhausted_sent is True
    assert runner.window_state.notice_level == 0


def test_llm_compress_strategy_receives_no_notice():
    runner = runner_factory(usage=0.9, strategy="llm_compress")
    messages = [Message(role="user", content="hi")]

    assert runner._inject_budget_notices(messages) == messages


def test_missing_the_summary_target_does_not_announce_exhaustion():
    """A reset that beats the trigger but misses the summary target is fine."""
    runner = runner_factory(
        usage=0.2,
        last_compression={
            "strategy": "token_budget",
            "insufficient": True,
            "tokens_after": 900,
            "threshold_tokens": 8000,
        },
    )

    injected = runner._inject_budget_notices([Message(role="user", content="hi")])

    assert len(injected) == 1
    assert runner.window_state.exhausted_sent is False


def test_no_water_level_on_the_request_that_reset_the_window():
    runner = runner_factory(usage=0.9)

    injected = runner._inject_budget_notices(
        [Message(role="user", content="hi")], just_reset=True
    )

    assert len(injected) == 1
    assert runner.window_state.notice_level == 0


def test_exhausted_notice_still_fires_right_after_a_reset():
    runner = runner_factory(
        usage=0.1,
        last_compression={
            "strategy": "token_budget",
            "insufficient": True,
            "tokens_after": 1200,
            "threshold_tokens": 1000,
        },
    )

    injected = runner._inject_budget_notices(
        [Message(role="user", content="hi")], just_reset=True
    )

    assert injected[-1].internal is True
    assert "cannot be trimmed further" in injected[-1].content


def test_range_selector_ignores_internal_notice():
    notice = Message(role="user", content="[SYSTEM NOTICE] ...")
    notice.internal = True
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
