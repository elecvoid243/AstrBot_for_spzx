"""Window identity appended to the provider-facing system prompt."""

from types import SimpleNamespace

from astrbot.core.agent import notes_store
from astrbot.core.agent.message import Message, WindowState
from astrbot.core.agent.notes_store import write_note
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner


def _runner(window_state: WindowState) -> ToolLoopAgentRunner:
    runner = object.__new__(ToolLoopAgentRunner)
    runner.window_state = window_state
    return runner


def _contexts(system: str = "base prompt") -> list[dict]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "hi"},
    ]


def test_first_window_appends_its_identity_to_the_system_prompt():
    runner = _runner(WindowState(id="w1", seq=0))
    contexts = _contexts()

    runner._append_window_notice(contexts)

    system = contexts[0]["content"]
    assert system.startswith("base prompt\n\n<context_window>")
    assert "Current window id: w1" in system
    assert "Window seq: 0" in system
    assert "Previous window id:" not in system
    assert "find its handoff note" not in system


def test_the_notice_never_reaches_the_history():
    runner = _runner(WindowState(id="w1"))
    contexts = _contexts()

    runner._append_window_notice(contexts)

    assert contexts[1] == {"role": "user", "content": "hi"}


def test_later_window_restores_the_checkpoint_before_searching_history():
    runner = _runner(WindowState(id="w2", seq=1, prev_id="w1"))
    contexts = _contexts()

    runner._append_window_notice(contexts)

    system = contexts[0]["content"]
    assert "Previous window id: w1" in system
    assert "find its handoff note with notes_list if needed" in system
    assert "read it first with notes_read" in system
    assert (
        system.index("notes_list")
        < system.index("notes_read")
        < system.index("history_read")
    )
    assert "history_search" in system


def test_a_new_window_renders_the_new_identity():
    runner = _runner(WindowState(id="w1"))
    first = _contexts()
    runner._append_window_notice(first)

    runner.window_state = WindowState(id="w2", seq=1, prev_id="w1")
    second = _contexts()
    runner._append_window_notice(second)

    assert "Current window id: w1" in first[0]["content"]
    assert "Current window id: w2" in second[0]["content"]


def test_a_restored_view_is_not_announced_twice():
    runner = _runner(WindowState(id="w1"))
    contexts = _contexts()

    runner._append_window_notice(contexts)
    once = contexts[0]["content"]
    runner._append_window_notice(contexts)

    assert contexts[0]["content"] == once


def test_missing_system_message_gets_one_carrying_the_notice():
    runner = _runner(WindowState(id="w1"))
    contexts = [{"role": "user", "content": "hi"}]

    runner._append_window_notice(contexts)

    assert contexts[0]["role"] == "system"
    assert "<context_window>" in contexts[0]["content"]
    assert contexts[1] == {"role": "user", "content": "hi"}


def test_message_contexts_are_supported():
    runner = _runner(WindowState(id="w1"))
    contexts = [
        Message(role="system", content="base"),
        Message(role="user", content="hi"),
    ]

    runner._append_window_notice(contexts)

    assert contexts[0].content.startswith("base\n\n<context_window>")
    assert contexts[1].content == "hi"


def test_multimodal_system_content_gets_a_text_part():
    runner = _runner(WindowState(id="w1"))
    contexts = [{"role": "system", "content": [{"type": "text", "text": "base"}]}]

    runner._append_window_notice(contexts)

    parts = contexts[0]["content"]
    assert parts[0] == {"type": "text", "text": "base"}
    assert "<context_window>" in parts[-1]["text"]


def test_a_window_lists_the_notes_it_opened_with():
    runner = _runner(WindowState(id="w1", notes=[{"path": "plan.md", "lines": 2}]))
    contexts = _contexts()

    runner._append_window_notice(contexts)

    system = contexts[0]["content"]
    assert "Private notes (shared across windows, newest first):" in system
    assert "- plan.md (2 lines)" in system


def test_writing_a_note_does_not_rewrite_the_open_window(tmp_path, monkeypatch):
    monkeypatch.setattr(
        notes_store, "get_astrbot_agent_notes_path", lambda: str(tmp_path)
    )
    write_note("umo:a", "plan.md", "line one\nline two")
    runner = _runner(WindowState(id="w1", notes=[{"path": "plan.md", "lines": 2}]))
    runner.req = SimpleNamespace(session_id="umo:a")
    first = _contexts()
    runner._append_window_notice(first)

    write_note("umo:a", "plan.md", "line one\nline two\nline three")
    second = _contexts()
    runner._append_window_notice(second)

    # A note written mid-window must not move a single byte of the system
    # prompt, or the provider prefix cache restarts at message zero.
    assert first[0]["content"] == second[0]["content"]
    assert "- plan.md (2 lines)" in second[0]["content"]


def test_a_new_window_freezes_the_notes_at_that_moment(tmp_path, monkeypatch):
    monkeypatch.setattr(
        notes_store, "get_astrbot_agent_notes_path", lambda: str(tmp_path)
    )
    write_note("umo:a", "plan.md", "line one\nline two\nline three")
    runner = _runner(WindowState(id="w1", notes=[]))
    runner.req = SimpleNamespace(session_id="umo:a")

    runner._on_context_reset()

    assert runner.window_state.notes == [{"path": "plan.md", "lines": 3}]


def test_no_notes_summary_without_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        notes_store, "get_astrbot_agent_notes_path", lambda: str(tmp_path)
    )
    runner = _runner(WindowState(id="w1"))
    runner.req = SimpleNamespace(session_id="umo:a")
    contexts = _contexts()

    runner._append_window_notice(contexts)

    assert "Private notes" not in contexts[0]["content"]
