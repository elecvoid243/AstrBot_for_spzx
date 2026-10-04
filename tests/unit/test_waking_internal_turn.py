"""WakingCheckStage bypass for internal turns (goal-loop continuations)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrbot.core.pipeline.waking_check.stage import WakingCheckStage

pytestmark = pytest.mark.asyncio


class _StubEvent:
    def __init__(self, message_str="/goal clear", extras=None):
        self.message_str = message_str
        self.message_obj = SimpleNamespace(type=None)
        self._extras = dict(extras or {})
        self.is_wake = False
        self.is_at_or_wake_command = False
        self.role = "member"
        self.plugins_name = None
        self.stopped = False
        self.unified_msg_origin = "webchat:FriendMessage:webchat!u!c1"

    def get_extra(self, key=None, default=None):
        if key is None:
            return self._extras
        return self._extras.get(key, default)

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_self_id(self):
        return "bot"

    def get_sender_id(self):
        return "u1"

    def is_private_chat(self):
        return False

    def get_messages(self):
        from astrbot.core.message.components import Plain

        return [Plain(self.message_str)]

    def stop_event(self):
        self.stopped = True


def _make_stage():
    stage = object.__new__(WakingCheckStage)
    stage.unique_session = False
    stage.ignore_bot_self_message = False
    stage.ignore_at_all = False
    stage.disable_builtin_commands = False
    stage.no_permission_reply = False
    stage.friend_message_needs_wake_prefix = False
    stage.ctx = SimpleNamespace(
        astrbot_config={
            "admins_id": [],
            "wake_prefix": ["/"],
            "plugin_set": ["*"],
        }
    )
    stage._umo_auto_name_recorder = SimpleNamespace(schedule=lambda event: None)
    return stage


@pytest.fixture
def matching_registry(monkeypatch):
    """Registry returning one handler whose filter matches any event."""

    class _MatchAllFilter:
        def filter(self, event, config):
            return True

    handler = SimpleNamespace(
        event_filters=[_MatchAllFilter()],
        handler_module_path="some.plugin",
        handler_full_name="some.plugin.h",
        handler_name="h",
    )

    import astrbot.core.pipeline.waking_check.stage as stage_mod

    calls = []

    def fake_get_handlers(event_type, plugins_name=None):
        calls.append(event_type)
        return [handler]

    monkeypatch.setattr(
        stage_mod.star_handlers_registry,
        "get_handlers_by_event_type",
        fake_get_handlers,
    )

    async def identity_filter(event, handlers):
        return handlers

    monkeypatch.setattr(
        stage_mod.SessionPluginManager,
        "filter_handlers_by_session",
        identity_filter,
    )
    return calls


async def test_internal_turn_skips_all_handler_activation(matching_registry):
    stage = _make_stage()
    event = _StubEvent(extras={"internal_turn": "goal"})
    await stage.process(event)
    assert event.is_wake is True
    assert event.is_at_or_wake_command is True
    assert event.get_extra("activated_handlers") == []
    assert matching_registry == []  # handler matching never ran
    assert event.stopped is False


async def test_normal_message_still_activates_handlers(matching_registry):
    stage = _make_stage()
    event = _StubEvent(message_str="/goal clear")
    await stage.process(event)
    assert len(event.get_extra("activated_handlers")) == 1
    assert event.is_wake is True
