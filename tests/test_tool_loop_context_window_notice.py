"""Window identity injected into the model-visible history."""

from types import SimpleNamespace

from astrbot.core.agent import notes_store
from astrbot.core.agent.message import Message, WindowState
from astrbot.core.agent.notes_store import write_note
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner


def _runner(window_state: WindowState) -> ToolLoopAgentRunner:
    runner = object.__new__(ToolLoopAgentRunner)
    runner.window_state = window_state
    return runner


def test_first_window_announces_only_its_identity():
    runner = _runner(WindowState(id="w1", seq=0))

    injected = runner._inject_context_window([Message(role="user", content="hi")])

    assert len(injected) == 2
    notice = injected[-1]
    assert notice.internal is True
    assert "<context_window>" in notice.content
    assert "Current window id: w1" in notice.content
    assert "Window seq: 0" in notice.content
    assert "Previous window id:" not in notice.content
    assert "archived" not in notice.content


def test_later_window_links_the_previous_one_and_points_at_the_archive():
    runner = _runner(WindowState(id="w2", seq=1, prev_id="w1"))

    injected = runner._inject_context_window([Message(role="user", content="hi")])

    content = injected[-1].content
    assert "Previous window id: w1" in content
    assert "archived" in content


def test_identity_is_announced_once_per_window():
    runner = _runner(WindowState(id="w1"))

    first = runner._inject_context_window([Message(role="user", content="hi")])
    second = runner._inject_context_window(first)

    assert len(first) == 2
    assert len(second) == 2


def test_a_new_window_is_announced_again():
    runner = _runner(WindowState(id="w1"))
    first = runner._inject_context_window([Message(role="user", content="hi")])

    runner.window_state = WindowState(id="w2", seq=1, prev_id="w1")
    second = runner._inject_context_window(first)

    assert len(second) == 3
    assert "Current window id: w2" in second[-1].content


def test_identity_is_internal_and_never_sent_as_a_normal_turn():
    runner = _runner(WindowState(id="w1"))

    injected = runner._inject_context_window([Message(role="user", content="hi")])

    assert injected[-1].internal is True
    assert "_internal" not in injected[-1].model_dump(mode="json")


def test_notes_summary_lists_the_newest_files(tmp_path, monkeypatch):
    monkeypatch.setattr(
        notes_store, "get_astrbot_agent_notes_path", lambda: str(tmp_path)
    )
    write_note("umo:a", "plan.md", "line one\nline two")
    runner = _runner(WindowState(id="w1"))
    runner.req = SimpleNamespace(session_id="umo:a")

    injected = runner._inject_context_window([Message(role="user", content="hi")])

    content = injected[-1].content
    assert "Private notes (shared across windows, newest first):" in content
    assert "- plan.md (2 lines)" in content


def test_no_notes_summary_without_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        notes_store, "get_astrbot_agent_notes_path", lambda: str(tmp_path)
    )
    runner = _runner(WindowState(id="w1"))
    runner.req = SimpleNamespace(session_id="umo:a")

    injected = runner._inject_context_window([Message(role="user", content="hi")])

    assert "Private notes" not in injected[-1].content
