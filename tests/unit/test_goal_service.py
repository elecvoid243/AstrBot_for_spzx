"""Tests for the kernel GoalService: blocking-tool guard, synthetic-turn
contract, permission gate, and the on_turn_done injection path (ported from
the astrbot_plugin_goal plugin tests)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from astrbot.core.goal.goal_manager import GoalManager
from astrbot.core.goal.goal_service import (
    DEFAULT_BLOCKED_TOOLS_DURING_GOAL,
    GOAL_CONTINUATION_EXTRA,
    GoalService,
    build_continuation_event,
)
from astrbot.core.goal.goal_state import DEFAULT_MAX_TURNS

# ---------------------------------------------------------------------------
# test doubles
# ---------------------------------------------------------------------------


class InMemoryKV:
    """Async dict implementing the KVStorage protocol."""

    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)


class _StubConfig:
    """Minimal stand-in for AstrBotConfig — only .get() matters here."""

    def __init__(self, data: dict | None = None) -> None:
        self._data = dict(data or {})

    def get(self, key, default=None):
        return self._data.get(key, default)


class _StubContext:
    """Minimal core-Context stand-in: goal config + injectable event queue."""

    def __init__(self, goal_cfg: dict | None = None):
        self._cfg = {"goal": dict(goal_cfg or {})}
        self.event_queue = asyncio.Queue()
        self.sent: list[tuple[str, str]] = []

    def get_config(self, umo=None):
        return self._cfg

    def get_event_queue(self):
        return self.event_queue

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain.get_plain_text()))


class _StubTool:
    def __init__(self, name: str) -> None:
        self.name = name


class _StubToolSet:
    def __init__(self, tools: list[_StubTool] | None = None) -> None:
        self.tools = list(tools or [])

    def remove_tool(self, name: str) -> None:
        self.tools = [t for t in self.tools if t.name != name]


class _StubRequest:
    def __init__(self, tool_set) -> None:
        self.func_tool = tool_set


class _StubEvent:
    """Event double with the surface the service reads/mutates."""

    def __init__(self, umo: str = "umo1", **extras) -> None:
        self.message_str = ""
        self.message_obj = SimpleNamespace(message_id="orig-1")
        self.unified_msg_origin = umo
        self.role = "admin"
        self._extras = dict(extras)
        self._stopped = False

    def set_extra(self, key, value) -> None:
        self._extras[key] = value

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def stop_event(self) -> None:
        self._stopped = True

    def clear_result(self) -> None:
        pass


class BareToolSet:
    """ToolSet double WITHOUT remove_tool — exercises the fallback path."""

    def __init__(self, tools):
        self.tools = list(tools)


def _make_service(config_data: dict | None = None) -> GoalService:
    """GoalService with a stub context and in-memory goal storage."""
    service = GoalService()
    service._context = _StubContext(config_data)
    service.goals = GoalManager(InMemoryKV())
    return service


# ---------------------------------------------------------------------------
# check_permission
# ---------------------------------------------------------------------------


def test_check_permission_admin_only_blocks_non_admin():
    service = _make_service({"admin_only": True})
    event = _StubEvent()
    event.role = "member"
    assert service.check_permission(event) is not None


def test_check_permission_admin_only_allows_admin():
    service = _make_service({"admin_only": True})
    event = _StubEvent()
    event.role = "admin"
    assert service.check_permission(event) is None


# ---------------------------------------------------------------------------
# set_goal config wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_goal_applies_configured_turn_budget():
    service = _make_service({"max_turns": 5})
    state = await service.set_goal("umo1", "write the report")
    assert state.max_turns == 5


@pytest.mark.asyncio
async def test_set_goal_falls_back_to_default_budget_without_config():
    service = _make_service()
    state = await service.set_goal("umo1", "write the report")
    assert state.max_turns == DEFAULT_MAX_TURNS


@pytest.mark.asyncio
async def test_on_turn_done_applies_configured_parse_failure_limit():
    """The ``goal.max_parse_failures`` config must reach evaluate_after_turn:
    with a limit of 1 the goal pauses after a single unparseable judge round
    and no continuation turn is injected."""
    service = _make_service({"max_parse_failures": 1})

    async def _garbage_judge_caller(umo, system_prompt, user_prompt):
        return "sorry, here is my opinion but definitely not the expected JSON"

    service._judge_llm_caller = _garbage_judge_caller
    await service.goals.set("umo1", "g")

    event = _StubEvent(umo="umo1")
    event.message_obj.message_id = "m1"
    response = SimpleNamespace(completion_text="did work")

    await service.on_turn_done(event, response)

    state = await service.goals.get("umo1")
    assert state.status == "paused"
    assert service._context.event_queue.qsize() == 0


# ---------------------------------------------------------------------------
# _get_blocked_tools
# ---------------------------------------------------------------------------


def test_get_blocked_tools_default_when_config_missing():
    service = _make_service()
    assert service._get_blocked_tools() == DEFAULT_BLOCKED_TOOLS_DURING_GOAL


def test_get_blocked_tools_default_when_config_empty_list():
    service = _make_service({"block_tools_during_goal": []})
    assert service._get_blocked_tools() == DEFAULT_BLOCKED_TOOLS_DURING_GOAL


def test_get_blocked_tools_default_when_config_wrong_type():
    service = _make_service({"block_tools_during_goal": "ask_user_choice"})
    assert service._get_blocked_tools() == DEFAULT_BLOCKED_TOOLS_DURING_GOAL
    service2 = _make_service({"block_tools_during_goal": {"name": "x"}})
    assert service2._get_blocked_tools() == DEFAULT_BLOCKED_TOOLS_DURING_GOAL


def test_get_blocked_tools_unions_user_extras_with_default():
    service = _make_service({"block_tools_during_goal": ["my_custom_blocker"]})
    blocked = service._get_blocked_tools()
    assert "my_custom_blocker" in blocked
    assert "ask_user_choice" in blocked
    assert blocked == DEFAULT_BLOCKED_TOOLS_DURING_GOAL | frozenset(
        {"my_custom_blocker"}
    )


def test_get_blocked_tools_strips_blank_entries():
    service = _make_service(
        {"block_tools_during_goal": ["valid_tool", "  ", "", "  another  "]}
    )
    blocked = service._get_blocked_tools()
    assert "valid_tool" in blocked
    assert "another" in blocked
    assert "" not in blocked
    assert "  " not in blocked


# ---------------------------------------------------------------------------
# _strip_blocked_tools
# ---------------------------------------------------------------------------


def test_strip_blocked_tools_removes_blocked_from_request():
    tool_set = _StubToolSet(
        [
            _StubTool("ask_user_choice"),
            _StubTool("web_search"),
            _StubTool("request_human"),
            _StubTool("code_exec"),
        ]
    )
    req = _StubRequest(tool_set)
    service = _make_service()
    removed = service._strip_blocked_tools(req)
    remaining = {t.name for t in req.func_tool.tools}
    assert removed == 2
    assert remaining == {"web_search", "code_exec"}


def test_strip_blocked_tools_handles_missing_func_tool():
    service = _make_service()
    assert service._strip_blocked_tools(_StubRequest(None)) == 0


def test_strip_blocked_tools_handles_none_request():
    service = _make_service()
    assert service._strip_blocked_tools(None) == 0


def test_strip_blocked_tools_handles_empty_tool_list():
    service = _make_service()
    assert service._strip_blocked_tools(_StubRequest(_StubToolSet([]))) == 0


def test_strip_blocked_tools_handles_no_match():
    service = _make_service()
    req = _StubRequest(_StubToolSet([_StubTool("web_search"), _StubTool("code_exec")]))
    assert service._strip_blocked_tools(req) == 0
    assert {t.name for t in req.func_tool.tools} == {"web_search", "code_exec"}


def test_strip_blocked_tools_falls_back_when_remove_tool_missing():
    tool_set = BareToolSet([_StubTool("ask_user_choice"), _StubTool("web_search")])
    req = _StubRequest(tool_set)
    service = _make_service()
    removed = service._strip_blocked_tools(req)
    assert removed == 1
    assert {t.name for t in req.func_tool.tools} == {"web_search"}


def test_strip_blocked_tools_respects_user_extras():
    service = _make_service({"block_tools_during_goal": ["my_custom_blocker"]})
    req = _StubRequest(
        _StubToolSet(
            [
                _StubTool("ask_user_choice"),
                _StubTool("my_custom_blocker"),
                _StubTool("web_search"),
            ]
        )
    )
    service._strip_blocked_tools(req)
    assert {t.name for t in req.func_tool.tools} == {"web_search"}


def test_strip_blocked_tools_fallback_tolerates_tool_without_name_attr():
    class BareToolSet:
        def __init__(self, tools):
            self.tools = list(tools)

    class NamelessTool:
        pass

    tool_set = BareToolSet([NamelessTool(), _StubTool("ask_user_choice")])
    req = _StubRequest(tool_set)
    service = _make_service()
    service._strip_blocked_tools(req)
    assert len(req.func_tool.tools) == 1
    assert isinstance(req.func_tool.tools[0], NamelessTool)


# ---------------------------------------------------------------------------
# guard_continuation integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guard_strips_tools_on_active_goal():
    service = _make_service()
    service.goals = GoalManager(InMemoryKV())
    await service.goals.set("umo1", "ship the feature")

    tool_set = _StubToolSet([_StubTool("ask_user_choice"), _StubTool("web_search")])
    req = _StubRequest(tool_set)
    event = _StubEvent(umo="umo1", **{GOAL_CONTINUATION_EXTRA: True})

    await service.guard_continuation(event, req)

    assert {t.name for t in req.func_tool.tools} == {"web_search"}
    assert event._stopped is False


@pytest.mark.asyncio
async def test_guard_drops_inactive_goal_without_stripping():
    service = _make_service()
    service.goals = GoalManager(InMemoryKV())
    # No goal set for umo1 → guard must stop the event and leave req alone.

    req = _StubRequest(_StubToolSet([_StubTool("ask_user_choice")]))
    event = _StubEvent(umo="umo1", **{GOAL_CONTINUATION_EXTRA: True})

    await service.guard_continuation(event, req)

    assert {t.name for t in req.func_tool.tools} == {"ask_user_choice"}
    assert event._stopped is True


@pytest.mark.asyncio
async def test_guard_passes_through_non_continuation_events():
    service = _make_service()
    service.goals = GoalManager(InMemoryKV())
    await service.goals.set("umo1", "ship the feature")

    req = _StubRequest(_StubToolSet([_StubTool("ask_user_choice")]))
    event = _StubEvent(umo="umo1")  # NO goal_continuation extra

    await service.guard_continuation(event, req)

    assert {t.name for t in req.func_tool.tools} == {"ask_user_choice"}
    assert event._stopped is False


# ---------------------------------------------------------------------------
# build_continuation_event contract
# ---------------------------------------------------------------------------


def test_continuation_event_carries_goal_continuation_extra():
    class _EventSrc:
        """Stub with the minimal interface build_continuation_event reads."""

        def __init__(self):
            self.message_str = "/goal set foo"
            self.message_obj = SimpleNamespace(message_id="orig-1")
            self._extras: dict = {}

        def set_extra(self, key, value) -> None:
            self._extras[key] = value

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def clear_result(self) -> None:
            pass

    src = _EventSrc()
    new_event = build_continuation_event(src, "请继续完成目标")
    assert new_event.get_extra(GOAL_CONTINUATION_EXTRA) is True
    assert new_event.message_str == "请继续完成目标"


# ---------------------------------------------------------------------------
# on_turn_done injection path
# ---------------------------------------------------------------------------


async def _judge_done(goal, response, subgoals):
    return "done", "finished", False


async def _judge_continue(goal, response, subgoals):
    return "continue", "keep going", False


@pytest.mark.asyncio
async def test_on_turn_done_registers_webchat_run_and_injects():
    """A continuing goal turn on a webchat session must register the next
    synthetic turn as a first-class chat run (registrar) and queue it."""
    service = _make_service()
    context = _StubContext()
    service._context = context
    service.goals = GoalManager(InMemoryKV())
    await service.goals.set("webchat:FriendMessage:webchat!u!cid1", "g")

    registered: list[tuple[str, str, str]] = []

    async def registrar(umo, message_id, checkpoint):
        registered.append((umo, message_id, checkpoint))

    service.set_run_registrar(registrar)

    event = _StubEvent(umo="webchat:FriendMessage:webchat!u!cid1")
    event.message_obj.message_id = "finished-turn"
    response = SimpleNamespace(completion_text="did some work")

    await service.on_turn_done(event, response)
    assert context.event_queue.qsize() == 1
    queued = context.event_queue.get_nowait()
    assert queued.get_extra("goal_continuation") is True
    assert registered and registered[0][0] == "webchat:FriendMessage:webchat!u!cid1"
    assert registered[0][1] == queued.message_obj.message_id


@pytest.mark.asyncio
async def test_on_turn_done_calls_registrar_for_every_platform():
    """GoalService calls the registrar for every injected turn — the
    platform filter (webchat-only run registration) lives in the registrar
    bridge the dashboard wires at startup, not in the service."""
    service = _make_service()
    context = _StubContext()
    service._context = context
    service.goals = GoalManager(InMemoryKV())
    await service.goals.set("aiocqhttp:GroupMessage:xxx", "g")

    registered: list[tuple] = []

    async def registrar(umo, message_id, checkpoint):
        registered.append((umo, message_id, checkpoint))

    service.set_run_registrar(registrar)

    event = _StubEvent(umo="aiocqhttp:GroupMessage:xxx")
    event.message_obj.message_id = "m1"
    response = SimpleNamespace(completion_text="done work")

    await service.on_turn_done(event, response)
    # The registrar receives the NEXT synthetic turn's identity: the queued
    # event's own message_id (not the finished turn's "m1").
    assert len(registered) == 1
    assert registered[0][0] == "aiocqhttp:GroupMessage:xxx"
    queued = context.event_queue.get_nowait()
    assert registered[0][1] == str(queued.message_obj.message_id)
    assert registered[0][2]
    assert context.event_queue.qsize() == 0


@pytest.mark.asyncio
async def test_on_turn_done_interrupted_goal_auto_pauses():
    service = _make_service()
    service.goals = GoalManager(InMemoryKV())
    await service.goals.set("umo1", "g")

    event = _StubEvent(umo="umo1", agent_stop_requested=True)
    event.message_obj.message_id = "m1"
    response = SimpleNamespace(completion_text="partial")

    await service.on_turn_done(event, response)
    state = await service.goals.get("umo1")
    assert state.status == "paused"
    assert (
        any("已暂停" in text for _, text in context.sent)
        if (context := service._context)
        else True
    )
