"""Latest-round reset primitive used by the token budget strategy."""

from astrbot.core.agent.context.truncator import ContextTruncator
from astrbot.core.agent.message import Message

TOOL_CALL = {
    "id": "c1",
    "type": "function",
    "function": {"name": "f", "arguments": "{}"},
}


def test_reset_keeps_system_and_latest_round():
    messages = [
        Message(role="system", content="sys"),
        Message(role="user", content="old"),
        Message(role="assistant", content="old reply"),
        Message(role="user", content="now"),
    ]
    result = ContextTruncator().reset_to_latest_round(messages)
    assert [m.content for m in result] == ["sys", "now"]


def test_reset_keeps_the_current_round_tool_batch_intact():
    messages = [
        Message(role="system", content="sys"),
        Message(role="user", content="old"),
        Message(role="assistant", content="old reply"),
        Message(role="user", content="now"),
        Message(role="assistant", content=None, tool_calls=[TOOL_CALL]),
        Message(role="tool", tool_call_id="c1", content="ok"),
    ]
    result = ContextTruncator().reset_to_latest_round(messages)
    assert [m.role for m in result] == ["system", "user", "assistant", "tool"]
    assert result[-1].tool_call_id == "c1"


def test_reset_completes_missing_tool_results():
    messages = [
        Message(role="user", content="now"),
        Message(
            role="assistant",
            content=None,
            tool_calls=[TOOL_CALL, {**TOOL_CALL, "id": "c2"}],
        ),
        Message(role="tool", tool_call_id="c1", content="ok"),
    ]
    result = ContextTruncator().reset_to_latest_round(messages)
    results = {m.tool_call_id: m for m in result if m.role == "tool"}
    assert set(results) == {"c1", "c2"}
    assert results["c2"].tool_result_unknown is True


def test_reset_drops_older_summary():
    summary = Message(role="assistant", content="summary", _compaction_id="s1")
    messages = [
        Message(role="system", content="sys"),
        summary,
        Message(role="user", content="now"),
    ]
    assert [m.content for m in ContextTruncator().reset_to_latest_round(messages)] == [
        "sys",
        "now",
    ]


def test_reset_returns_input_when_nothing_to_drop():
    messages = [Message(role="system", content="sys")]
    assert ContextTruncator().reset_to_latest_round(messages) == messages


def test_reset_keeps_the_request_when_a_notice_trails_it():
    notice = Message(role="user", content="[context budget] wrap up")
    notice.internal = True
    messages = [
        Message(role="user", content="do the task"),
        Message(role="assistant", content=None, tool_calls=[TOOL_CALL]),
        Message(role="tool", tool_call_id="c1", content="ok"),
        notice,
    ]

    result = ContextTruncator().reset_to_latest_round(messages)

    # The notice must not open a round of its own; otherwise the real request
    # would be dropped and the model would see only the kernel reminder.
    assert [m.role for m in result] == ["user", "assistant", "tool", "user"]
    assert result[0].content == "do the task"


def test_drop_oldest_round_ignores_the_trailing_notice():
    notice = Message(role="user", content="[context budget] wrap up")
    notice.internal = True
    messages = [
        Message(role="user", content="old"),
        Message(role="assistant", content="old reply"),
        Message(role="user", content="current"),
        Message(role="assistant", content="a"),
        notice,
    ]

    result = ContextTruncator().drop_oldest_round(messages)

    assert [m.content for m in result] == ["current", "a", "[context budget] wrap up"]
