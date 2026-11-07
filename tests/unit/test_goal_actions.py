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


async def test_set_goal_queues_kickoff_and_returns_state(fresh_goals, monkeypatch):
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=_session())

    order = []
    register_mock = AsyncMock(side_effect=lambda **kw: order.append("register"))
    monkeypatch.setattr(service, "register_synthetic_chat_run", register_mock)

    queue = asyncio.Queue()

    class _FakeMgr:
        def get_or_create_queue(self, cid):
            order.append("get_queue")
            return queue

    monkeypatch.setattr(chat_service_mod, "webchat_queue_mgr", _FakeMgr())

    result = await service.set_session_goal(
        "alice", "cid1", "重构鉴权模块直到测试通过", max_turns=12
    )

    state = result["goal"]
    assert state["status"] == "active"
    assert state["goal"] == "重构鉴权模块直到测试通过"
    assert state["max_turns"] == 12
    assert (await fresh_goals.get(UMO)).goal_id == state["goal_id"]

    username, cid, payload = await asyncio.wait_for(queue.get(), timeout=1)
    assert username == "alice"
    assert cid == "cid1"
    assert payload["internal_turn"] == "goal"
    assert payload["goal_id"] == state["goal_id"]
    # The goal text itself becomes the first turn.
    assert payload["message"][0]["text"] == "重构鉴权模块直到测试通过"
    assert order[0] == "register"


async def test_set_goal_requires_owner_and_text(fresh_goals):
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=_session("bob"))
    with pytest.raises(ChatServiceError):
        await service.set_session_goal("alice", "cid1", "g")

    service.db.get_platform_session_by_id = AsyncMock(return_value=_session())
    with pytest.raises(ChatServiceError):
        await service.set_session_goal("alice", "cid1", "   ")


async def test_editing_an_existing_goal_updates_in_place_without_kickoff(
    fresh_goals, monkeypatch
):
    """Editing mid-run must not queue a second turn (the duplicate-turn bug)."""
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=_session())

    queue = asyncio.Queue()

    class _FakeMgr:
        def get_or_create_queue(self, cid):
            return queue

    monkeypatch.setattr(chat_service_mod, "webchat_queue_mgr", _FakeMgr())
    register_mock = AsyncMock()
    monkeypatch.setattr(service, "register_synthetic_chat_run", register_mock)

    original = await fresh_goals.set(UMO, "旧目标", max_turns=20)

    result = await service.set_session_goal("alice", "cid1", "新目标", max_turns=40)

    goal = result["goal"]
    assert goal["goal_id"] == original.goal_id
    assert goal["goal"] == "新目标"
    assert goal["max_turns"] == 40
    assert goal["status"] == "active"
    # The edit is state-only: nothing was registered or queued.
    assert queue.empty()
    register_mock.assert_not_awaited()


async def test_set_goal_without_budget_follows_config(fresh_goals, monkeypatch):
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=_session())

    class _FakeMgr:
        def get_or_create_queue(self, cid):
            return asyncio.Queue()

    monkeypatch.setattr(chat_service_mod, "webchat_queue_mgr", _FakeMgr())
    monkeypatch.setattr(service, "register_synthetic_chat_run", AsyncMock())
    monkeypatch.setattr(
        goal_service,
        "_config",
        lambda: {"max_turns": 33},
    )

    result = await service.set_session_goal("alice", "cid1", "g", max_turns=None)

    assert result["goal"]["max_turns"] == 33


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
