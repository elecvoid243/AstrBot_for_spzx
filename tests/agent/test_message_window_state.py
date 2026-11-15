"""Window state metadata carried by persisted history messages."""

from astrbot.core.agent.message import Message, WindowState, latest_window_state
from astrbot.core.provider.modalities import sanitize_contexts_by_modalities


def test_window_state_round_trips_through_history():
    state = WindowState(id="w1", seq=2, reminder_sent=True)
    message = Message(role="user", content="hi")
    message.window = state
    dumped = message.model_dump_for_context()
    assert dumped["_window"] == {
        "id": "w1",
        "seq": 2,
        "reminder_sent": True,
        "exhausted_sent": False,
    }
    assert Message.model_validate(dumped).window == state


def test_window_state_never_reaches_the_provider():
    message = Message(role="user", content="hi")
    message.window = WindowState(id="w1")
    message.internal = True
    dumped = message.model_dump(mode="json")
    assert "_window" not in dumped
    assert "_internal" not in dumped


def test_latest_window_state_takes_newest_marker():
    first = Message(role="user", content="a")
    first.window = WindowState(id="w1", seq=1)
    last = Message(role="assistant", content="b")
    last.window = WindowState(id="w2", seq=2)
    assert (
        latest_window_state([first, Message(role="user", content="c"), last])
        == last.window
    )


def test_malformed_window_marker_is_dropped_on_load():
    broken = Message.model_validate(
        {"role": "user", "content": "a", "_window": {"seq": "not-an-int"}}
    )
    assert broken.window is None
    assert latest_window_state([broken]) is None


def test_latest_window_state_without_markers_is_none():
    assert latest_window_state([Message(role="user", content="a")]) is None


def test_internal_marker_survives_a_history_round_trip():
    message = Message(role="user", content="[context budget] wrap up")
    message.internal = True

    restored = Message.model_validate(message.model_dump_for_context())

    assert restored.internal is True


def test_provider_payload_drops_internal_markers():
    message = Message(role="user", content="[context budget] wrap up")
    message.internal = True
    message.window = WindowState(id="w1")

    delivered, _ = sanitize_contexts_by_modalities([message], ["text"])

    assert "_internal" not in delivered[0]
    assert "_window" not in delivered[0]
