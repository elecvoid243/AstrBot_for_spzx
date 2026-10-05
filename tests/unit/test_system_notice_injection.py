"""System-notice injection channel on the tool-loop agent runner.

Dashboard-originated events (e.g. the user terminating a managed shell
session) must reach the active agent as ``[SYSTEM NOTICE]`` suffixes on the
next tool result, framed distinctly from user follow-up messages.
"""

from __future__ import annotations

import asyncio

import pytest

from astrbot.core.agent.runners.base import AgentState
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.pipeline.process_stage.follow_up import (
    inject_system_notice_to_active_run,
    register_active_runner,
    unregister_active_runner,
)

pytestmark = pytest.mark.asyncio


def _runner() -> ToolLoopAgentRunner:
    runner = ToolLoopAgentRunner()
    # Notice plumbing lives in per-run state that reset() normally
    # initializes; these unit tests exercise that plumbing, not lifecycle.
    runner._state = AgentState.RUNNING
    runner._abort_signal = asyncio.Event()
    runner.run_context = None  # register_active_runner tolerates this
    runner._pending_system_notices = []
    runner._pending_follow_ups = []
    runner._unconsumed_follow_ups = []
    runner._all_follow_ups = []
    runner._follow_up_seq = 0
    return runner


async def test_system_notice_uses_its_own_template():
    runner = _runner()

    assert runner.inject_system_notice("hello from dashboard") is True
    content = runner._merge_follow_up_notice("tool result")

    assert content == "tool result\n\n[SYSTEM NOTICE] hello from dashboard"
    # Must not borrow the follow-up framing ("user sent follow-up messages").
    assert "follow-up" not in content


async def test_system_notice_consumed_once():
    runner = _runner()
    runner.inject_system_notice("once")

    first = runner._merge_follow_up_notice("a")
    second = runner._merge_follow_up_notice("b")

    assert "[SYSTEM NOTICE] once" in first
    assert second == "b"


async def test_system_notice_merges_alongside_follow_ups():
    runner = _runner()
    runner.follow_up(message_text="user follow-up")
    runner.inject_system_notice("dashboard event")

    content = runner._merge_follow_up_notice("result")

    # System notice first, then the follow-up block.
    assert content.index("[SYSTEM NOTICE] dashboard event") < content.index("follow-up")
    assert "user follow-up" in content


async def test_inject_returns_false_when_done():
    runner = _runner()
    runner._state = AgentState.DONE

    assert runner.inject_system_notice("too late") is False
    assert runner._merge_follow_up_notice("x") == "x"


async def test_inject_returns_false_when_stop_requested():
    runner = _runner()
    runner.request_stop()

    assert runner.inject_system_notice("too late") is False


async def test_inject_returns_false_for_blank_text():
    runner = _runner()
    assert runner.inject_system_notice("   ") is False


async def test_inject_to_active_run():
    runner = _runner()
    umo = "webchat:FriendMessage:webchat!u!cid-test"
    register_active_runner(umo, runner)
    try:
        assert inject_system_notice_to_active_run(umo, "ping") is True
        assert "[SYSTEM NOTICE] ping" in runner._merge_follow_up_notice("r")
        assert inject_system_notice_to_active_run("webchat:none", "x") is False
    finally:
        unregister_active_runner(umo, runner)
