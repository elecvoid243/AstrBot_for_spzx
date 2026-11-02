"""Cover the pre-runner interval in idle browser admission."""

from astrbot.core.utils.active_event_registry import ActiveEventRegistry


def test_pending_turn_blocks_takeover_before_runner_callback():
    """Keep a turn preparing its provider request outside idle admission."""
    registry = ActiveEventRegistry()
    event = type("PendingEvent", (), {"unified_msg_origin": "owned-chat"})()
    registry.register(event)
    assert registry.has_active_agent("owned-chat")
    assert not registry.has_active_agent("other-chat")
    registry.unregister(event)
    assert not registry.has_active_agent("owned-chat")
