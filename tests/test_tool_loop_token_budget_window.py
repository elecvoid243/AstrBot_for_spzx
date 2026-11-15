"""Window state read, advance and provider-side budget resolution."""

from astrbot.core.agent.context.config import (
    DEFAULT_COMPACTION_REMINDER_TOKENS,
    resolve_budget_strategy,
)
from astrbot.core.agent.message import Message, WindowState
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner


def _runner(**attrs):
    runner = object.__new__(ToolLoopAgentRunner)
    for key, value in attrs.items():
        setattr(runner, key, value)
    return runner


def test_state_advances_only_after_reset():
    runner = _runner(window_state=WindowState(id="w1", seq=0))

    runner._on_context_reset()

    assert runner.window_state.seq == 1
    assert runner.window_state.id != "w1"
    assert runner.window_state.reminder_sent is False
    assert runner.window_state.exhausted_sent is False


def test_runner_reads_state_from_loaded_history():
    history = [Message(role="user", content="hi")]
    history[0].window = WindowState(id="w7", seq=7, reminder_sent=True)
    runner = _runner()

    state = runner._load_window_state(history)

    assert state.seq == 7
    assert state.reminder_sent is True


def test_loaded_history_without_marker_starts_a_fresh_window():
    runner = _runner()

    state = runner._load_window_state([Message(role="user", content="hi")])

    assert state.seq == 0
    assert state.id


def test_budget_strategy_falls_back_without_window():
    assert resolve_budget_strategy("token_budget", {"max_context_tokens": 0}) == (
        "llm_compress",
        DEFAULT_COMPACTION_REMINDER_TOKENS,
    )
    assert resolve_budget_strategy(
        "token_budget",
        {"max_context_tokens": 128000, "compaction_reminder_tokens": 0},
    ) == ("token_budget", DEFAULT_COMPACTION_REMINDER_TOKENS)
    assert resolve_budget_strategy(
        "token_budget",
        {"max_context_tokens": 128000, "compaction_reminder_tokens": 2048},
    ) == ("token_budget", 2048)
