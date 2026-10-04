"""Dashboard goal actions: service behavior + webchat adapter extra lift."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import astrbot.dashboard.services.chat_service as chat_service_mod
from astrbot.core.goal.goal_manager import GoalManager
from astrbot.core.goal.goal_service import goal_service
from astrbot.core.message.components import Plain
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.dashboard.services.chat_service import ChatService, ChatServiceError

pytestmark = pytest.mark.asyncio


class InMemoryKV:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)


def _make_service() -> ChatService:
    service = ChatService.__new__(ChatService)
    service.db = MagicMock()
    service.core_lifecycle = MagicMock()
    service.conv_mgr = MagicMock()
    service.platform_history_mgr = MagicMock()
    service.umop_config_router = MagicMock()
    service.running_convs = {}
    service.chat_runs = {}
    service.chat_runs_by_session = {}
    service.save_bot_message = AsyncMock()
    return service


def _session(creator="alice"):
    return SimpleNamespace(
        creator=creator,
        platform_id="webchat",
        is_group=False,
        session_id="cid1",
    )


UMO = "webchat:FriendMessage:webchat!alice!cid1"


@pytest.fixture
def fresh_goals(monkeypatch):
    """Isolate the global goal_service from other tests' state."""
    mgr = GoalManager(InMemoryKV())
    monkeypatch.setattr(goal_service, "goals", mgr)
    return mgr


async def test_apply_goal_action_requires_owner(fresh_goals):
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=_session("bob"))
    with pytest.raises(ChatServiceError):
        await service.apply_goal_action("alice", "cid1", "pause")


async def test_pause_and_clear_actions(fresh_goals, monkeypatch):
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=_session())
    await fresh_goals.set(UMO, "g")

    async def _pause(umo):
        return await fresh_goals.pause(umo)

    async def _clear(umo):
        return await fresh_goals.clear(umo)

    pause_mock = AsyncMock(side_effect=_pause)
    clear_mock = AsyncMock(side_effect=_clear)
    monkeypatch.setattr(goal_service, "pause_goal", pause_mock)
    monkeypatch.setattr(goal_service, "clear_goal", clear_mock)

    result = await service.apply_goal_action("alice", "cid1", "pause")
    pause_mock.assert_awaited_once_with(UMO)
    assert result["goal"]["status"] == "paused"

    result = await service.apply_goal_action("alice", "cid1", "clear")
    clear_mock.assert_awaited_once_with(UMO)
    assert result["goal"] is None


async def test_resume_action_queues_internal_kickoff(fresh_goals, monkeypatch):
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=_session())
    await fresh_goals.set(UMO, "g")
    await fresh_goals.pause(UMO)
    goal_id = (await fresh_goals.get(UMO)).goal_id

    order = []
    register_mock = AsyncMock(side_effect=lambda **kw: order.append("register"))
    monkeypatch.setattr(service, "register_synthetic_chat_run", register_mock)

    queue = asyncio.Queue()

    class _FakeMgr:
        def get_or_create_queue(self, cid):
            order.append("get_queue")
            return queue

    monkeypatch.setattr(chat_service_mod, "webchat_queue_mgr", _FakeMgr())

    result = await service.apply_goal_action("alice", "cid1", "resume")
    assert result["goal"]["status"] == "active"

    username, cid, payload = await asyncio.wait_for(queue.get(), timeout=1)
    assert cid == "cid1"
    assert payload["internal_turn"] == "goal"
    assert payload["goal_id"] == goal_id
    assert payload["message"][0]["text"].startswith("请继续完成目标：")
    # register-then-inject ordering (mirrors agent_team_ports.deliver)
    assert order[0] == "register"


async def test_unknown_action_rejected(fresh_goals):
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=_session())
    with pytest.raises(ChatServiceError):
        await service.apply_goal_action("alice", "cid1", "explode")


# ---------------------------------------------------------------------------
# adapter lifts internal_turn / goal_id payload keys into event extras
# ---------------------------------------------------------------------------


def test_adapter_lifts_internal_turn_keys():
    adapter = object.__new__(WebChatAdapter)
    adapter.metadata = PlatformMetadata(name="webchat", description="", id="webchat")
    abm = AstrBotMessage()
    abm.type = MessageType.FRIEND_MESSAGE
    abm.self_id = "bot"
    abm.session_id = "cid1"
    abm.message_id = "m1"
    abm.sender = MessageMember(user_id="alice", nickname="alice")
    abm.message = [Plain("hi")]
    abm.message_str = "hi"
    abm.raw_message = (
        "alice",
        "cid1",
        {"internal_turn": "goal", "goal_id": "g1", "flags": None},
    )
    event = adapter.create_event(abm)
    assert event.get_extra("internal_turn") == "goal"
    assert event.get_extra("goal_id") == "g1"
