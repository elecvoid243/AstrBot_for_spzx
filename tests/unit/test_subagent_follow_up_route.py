"""Unit tests for the subagent follow-up delivery route.

The handler is exercised via direct calls (auth Depends passed explicitly),
mirroring tests/unit/test_chat_file_change_routes.py.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.subagent_manager import (
    SubAgentConfig,
    SubAgentConversation,
    SubAgentManager,
    SubAgentRunHandle,
)
from astrbot.core.utils.webchat_access import (
    DashboardPrincipal,
    dashboard_account_context,
)

UMO = "webchat:FriendMessage:webchat!tester!sess-1"


@pytest.fixture(autouse=True)
def _clean_sessions():
    SubAgentManager._sessions.clear()
    yield
    SubAgentManager._sessions.clear()


class _FakeRunner:
    """Minimal stand-in exposing the runner's follow_up contract."""

    def __init__(self, accept: bool = True):
        self.accept = accept
        self.received: list[str] = []

    def follow_up(self, *, message_text: str):
        if not self.accept:
            return None
        self.received.append(message_text)
        return SimpleNamespace(seq=len(self.received) - 1)


class _RecordingSink:
    def __init__(self):
        self.user_messages: list[tuple[int, str]] = []

    async def user_message(self, seq: int, text: str) -> None:
        self.user_messages.append((seq, text))


def _register(runner, sink) -> None:
    SubAgentManager.register_subagent_runner(
        UMO,
        SubAgentRunHandle(
            umo=UMO,
            subagent_run_id="sa_1",
            agent_name="coder",
            runner=runner,
            sink=sink,
        ),
    )


def _auth(username: str = "tester"):
    return SimpleNamespace(username=username, via="jwt")


@pytest.fixture
def service():
    return SimpleNamespace(
        require_owned_chat_session=AsyncMock(
            return_value=SimpleNamespace(is_group=False)
        )
    )


def _payload(chat_api, **overrides):
    data = {"session_id": "sess-1", "subagent_run_id": "sa_1", "text": "steer it"}
    data.update(overrides)
    return chat_api.ChatSubagentFollowUpRequest(**data)


@pytest.mark.asyncio
async def test_follow_up_accepted_and_echoed(service):
    from astrbot.dashboard.api import chat as chat_api

    runner = _FakeRunner()
    sink = _RecordingSink()
    _register(runner, sink)

    resp = await chat_api.post_subagent_follow_up(_payload(chat_api), _auth(), service)

    assert resp["status"] == "ok"
    assert resp["data"] == {"accepted": True, "seq": 0}
    assert runner.received == ["steer it"]
    assert sink.user_messages == [(0, "steer it")]


@pytest.mark.asyncio
async def test_follow_up_rejected_states(service):
    from astrbot.dashboard.api import chat as chat_api

    # Unknown run id -> not_found
    resp = await chat_api.post_subagent_follow_up(_payload(chat_api), _auth(), service)
    assert resp["data"] == {"accepted": False, "reason": "not_found"}

    # Whitespace-only text -> empty_text (and no registry lookup needed)
    resp = await chat_api.post_subagent_follow_up(
        _payload(chat_api, text="   "), _auth(), service
    )
    assert resp["data"] == {"accepted": False, "reason": "empty_text"}

    # Runner done (follow_up returns None) -> finished
    runner = _FakeRunner(accept=False)
    sink = _RecordingSink()
    _register(runner, sink)
    resp = await chat_api.post_subagent_follow_up(_payload(chat_api), _auth(), service)
    assert resp["data"] == {"accepted": False, "reason": "finished"}
    assert sink.user_messages == []


@pytest.mark.asyncio
async def test_follow_up_cross_session_isolation(service):
    from astrbot.dashboard.api import chat as chat_api

    runner = _FakeRunner()
    _register(runner, _RecordingSink())

    # Same session_id and run id, but another user's umo never resolves.
    resp = await chat_api.post_subagent_follow_up(
        _payload(chat_api), _auth("other"), service
    )
    assert resp["data"] == {"accepted": False, "reason": "not_found"}
    assert runner.received == []


@pytest.mark.asyncio
async def test_retained_subagent_waits_for_current_turn_and_survives_cleanup(service):
    from astrbot.dashboard.api import chat as chat_api

    runtime = SubAgentManager._get_or_create_session(UMO)
    config = SubAgentConfig(name="coder")
    runtime.subagents[config.name] = config
    conversation = SubAgentConversation(
        config.instance_id, asyncio.Event(), AsyncMock()
    )
    runtime.conversations["sa_1"] = conversation
    principal = DashboardPrincipal("tester", "tester", "user", "tester")
    token = dashboard_account_context.set(principal)
    try:
        payload = _payload(chat_api, continue_conversation=True)
        response = await chat_api.post_subagent_follow_up(payload, _auth(), service)
        assert response["data"]["reason"] == "not_retained"
        await chat_api.post_subagent_follow_up(
            _payload(chat_api, keep_alive=True), _auth(), service
        )
        assert SubAgentManager.cleanup_session_turn_end(UMO)["cleaned_agents"] == []
        response = await chat_api.post_subagent_follow_up(payload, _auth(), service)
        assert response["data"]["accepted"] is True
        task = conversation.pending
        await asyncio.sleep(0)
        conversation.resume.assert_not_awaited()
        response = await chat_api.post_subagent_follow_up(payload, _auth(), service)
        assert response["data"]["reason"] == "busy"
        conversation.ready.set()
        await asyncio.wait_for(task, 5)
        conversation.resume.assert_awaited_once_with("steer it", principal)
        await chat_api.post_subagent_follow_up(
            _payload(chat_api, keep_alive=False), _auth(), service
        )
        assert SubAgentManager.cleanup_session_turn_end(UMO)["cleaned_agents"] == [
            "coder"
        ]
        assert runtime.conversations == {}
    finally:
        dashboard_account_context.reset(token)
