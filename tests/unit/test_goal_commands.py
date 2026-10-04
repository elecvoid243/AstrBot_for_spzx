"""Permission matrix for the builtin /goal and /subgoal command surface."""

from __future__ import annotations

import pytest

from astrbot.builtin_stars.goal.main import Main
from astrbot.core.goal.goal_service import goal_service

pytestmark = pytest.mark.asyncio


class _StubConfig:
    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


class _StubContext:
    def __init__(self, goal_cfg=None):
        self._cfg = {"goal": dict(goal_cfg or {"admin_only": True})}

    def get_config(self, umo=None):
        return self._cfg


class _StubEvent:
    """Member-role event; records yielded plain results."""

    def __init__(self, role="member"):
        self.role = role
        self.unified_msg_origin = "umo1"
        self.sent = []

    def get_extra(self, key, default=None):
        return default

    def plain_result(self, text):
        self.sent.append(text)
        return text


@pytest.mark.parametrize(
    "handler_name,args",
    [
        ("goal_set", ("target",)),
        ("goal_status", ()),
        ("goal_pause", ()),
        ("goal_resume", ()),
        ("goal_clear", ()),
        ("subgoal_add", ("x",)),
        ("subgoal_list", ()),
        ("subgoal_remove", (1,)),
        ("subgoal_clear", ()),
    ],
)
async def test_member_rejected_everywhere(handler_name, args):
    goal_service.bind(_StubContext())
    plugin = Main(goal_service._context)
    event = _StubEvent(role="member")
    handler = getattr(plugin, handler_name)
    results = [r async for r in handler(event, *args)]
    assert len(results) == 1
    assert "仅管理员" in event.sent[0]


async def test_admin_passes_permission_gate():
    goal_service.bind(_StubContext())
    plugin = Main(goal_service._context)
    event = _StubEvent(role="admin")
    results = [r async for r in plugin.goal_status(event)]
    assert len(results) == 1
    assert "仅管理员" not in event.sent[0]
