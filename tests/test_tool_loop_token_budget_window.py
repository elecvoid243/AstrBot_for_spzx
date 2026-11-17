"""Window state read, advance and provider-side budget resolution."""

from types import SimpleNamespace

import pytest

from astrbot.core.agent import history_archive
from astrbot.core.agent.context.config import resolve_budget_strategy
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
    assert runner.window_state.notice_level == 0
    assert runner.window_state.exhausted_sent is False


def test_runner_reads_state_from_loaded_history():
    history = [Message(role="user", content="hi")]
    history[0].window = WindowState(id="w7", seq=7, notice_level=85)
    runner = _runner()

    state = runner._load_window_state(history)

    assert state.seq == 7
    assert state.notice_level == 85


def test_loaded_history_without_marker_starts_a_fresh_window():
    runner = _runner()

    state = runner._load_window_state([Message(role="user", content="hi")])

    assert state.seq == 0
    assert state.id


def test_subagent_ignores_the_main_window_marker():
    history = [Message(role="user", content="hi")]
    history[0].window = WindowState(id="w9", seq=4)
    runner = _runner(
        req=SimpleNamespace(session_id="umo:a"),
        run_context=SimpleNamespace(
            context=SimpleNamespace(extra={"is_subagent": True})
        ),
    )

    state = runner._load_window_state(history)

    assert state.seq == 0
    assert state.id != "w9"
    assert state.prev_id is None


def test_subagent_never_resets_its_window():
    runner = _runner(
        window_state=WindowState(id="w1", seq=0),
        req=SimpleNamespace(session_id="umo:a"),
        run_context=SimpleNamespace(
            context=SimpleNamespace(extra={"is_subagent": True})
        ),
    )

    runner._on_context_reset([Message(role="user", content="old")])

    assert runner.window_state.id == "w1"
    assert runner.window_state.seq == 0


@pytest.mark.asyncio
async def test_missing_marker_continues_the_archived_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(
        history_archive, "get_astrbot_agent_history_path", lambda: str(tmp_path)
    )
    await history_archive.append_window(
        "umo:a", WindowState(id="w1", seq=0), [Message(role="user", content="one")]
    )
    await history_archive.append_window(
        "umo:a",
        WindowState(id="w2", seq=1, prev_id="w1"),
        [Message(role="user", content="two")],
    )
    runner = _runner(req=SimpleNamespace(session_id="umo:a"))

    state = runner._load_window_state([Message(role="user", content="hi")])

    assert state.seq == 2
    assert state.prev_id == "w2"
    assert state.id not in {"w1", "w2"}


@pytest.mark.asyncio
async def test_a_restarted_chain_is_repaired_by_the_window_count(tmp_path, monkeypatch):
    monkeypatch.setattr(
        history_archive, "get_astrbot_agent_history_path", lambda: str(tmp_path)
    )
    await history_archive.append_window(
        "umo:a", WindowState(id="w1", seq=0), [Message(role="user", content="one")]
    )
    await history_archive.append_window(
        "umo:a", WindowState(id="w2", seq=0), [Message(role="user", content="two")]
    )
    runner = _runner(req=SimpleNamespace(session_id="umo:a"))

    state = runner._load_window_state([Message(role="user", content="hi")])

    assert state.seq == 2
    assert state.prev_id in {"w1", "w2"}


@pytest.mark.asyncio
async def test_persisted_marker_wins_over_the_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(
        history_archive, "get_astrbot_agent_history_path", lambda: str(tmp_path)
    )
    await history_archive.append_window(
        "umo:a", WindowState(id="w1", seq=0), [Message(role="user", content="one")]
    )
    history = [Message(role="user", content="hi")]
    history[0].window = WindowState(id="w9", seq=1)
    runner = _runner(req=SimpleNamespace(session_id="umo:a"))

    state = runner._load_window_state(history)

    assert state.id == "w9"
    assert state.seq == 1


def test_budget_strategy_falls_back_without_window():
    assert resolve_budget_strategy("token_budget", {"max_context_tokens": 0}) == (
        "llm_compress"
    )
    assert (
        resolve_budget_strategy("token_budget", {"max_context_tokens": 128000})
        == "token_budget"
    )
