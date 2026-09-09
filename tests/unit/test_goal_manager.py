"""Tests for GoalManager mutation operations and the post-turn decision
engine (ported from the astrbot_plugin_goal plugin)."""

import pytest

from astrbot.core.goal.goal_manager import GoalManager

pytestmark = pytest.mark.asyncio


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


@pytest.fixture
def kv():
    return InMemoryKV()


async def judge_done(goal, response, subgoals):
    return "done", "finished", False


async def judge_continue(goal, response, subgoals):
    return "continue", "keep going", False


async def judge_parse_fail(goal, response, subgoals):
    return "continue", "judge returned empty response", True


async def test_set_and_get(kv):
    mgr = GoalManager(kv)
    state = await mgr.set("umo1", "write report")
    assert state.status == "active"
    assert (await mgr.get("umo1")).goal == "write report"
    assert "umo1" in await mgr.list_tracked()


async def test_set_empty_raises(kv):
    mgr = GoalManager(kv)
    with pytest.raises(ValueError):
        await mgr.set("umo1", "   ")


async def test_pause_resume(kv):
    mgr = GoalManager(kv)
    await mgr.set("umo1", "g")
    paused = await mgr.pause("umo1", reason="user-paused")
    assert paused.status == "paused"
    assert paused.paused_reason == "user-paused"
    resumed = await mgr.resume("umo1")
    assert resumed.status == "active"
    assert resumed.paused_reason is None


async def test_done(kv):
    mgr = GoalManager(kv)
    await mgr.set("umo1", "g")
    d = await mgr.evaluate_after_turn("umo1", "resp", judge_done)
    assert d["should_continue"] is False
    assert d["verdict"] == "done"
    assert "目标达成" in d["message"]
    assert (await mgr.get("umo1")).status == "done"


async def test_continue_under_budget(kv):
    mgr = GoalManager(kv)
    await mgr.set("umo1", "g")
    d = await mgr.evaluate_after_turn("umo1", "resp", judge_continue)
    assert d["should_continue"] is True
    assert "g" in d["continuation_prompt"]
    assert (await mgr.get("umo1")).turns_used == 1


async def test_budget_exhausted_pauses(kv):
    mgr = GoalManager(kv)
    await mgr.set("umo1", "g", max_turns=1)
    d = await mgr.evaluate_after_turn("umo1", "resp", judge_continue)
    assert d["should_continue"] is False
    state = await mgr.get("umo1")
    assert state.status == "paused"


async def test_parse_failures_pause_after_threshold(kv):
    mgr = GoalManager(kv, max_parse_failures=2)
    await mgr.set("umo1", "g")
    d1 = await mgr.evaluate_after_turn("umo1", "resp", judge_parse_fail)
    assert d1["should_continue"] is True  # first failure: fail-open
    d2 = await mgr.evaluate_after_turn("umo1", "resp", judge_parse_fail)
    assert d2["should_continue"] is False
    assert (await mgr.get("umo1")).status == "paused"


async def test_parse_failure_override_threshold(kv):
    mgr = GoalManager(kv)  # constructor default is 3
    await mgr.set("umo1", "g")
    d = await mgr.evaluate_after_turn(
        "umo1", "resp", judge_parse_fail, max_parse_failures=1
    )
    assert d["should_continue"] is False
    assert (await mgr.get("umo1")).status == "paused"


async def test_parse_failure_none_falls_back_to_constructor_default(kv):
    mgr = GoalManager(kv, max_parse_failures=1)
    await mgr.set("umo1", "g")
    d = await mgr.evaluate_after_turn(
        "umo1", "resp", judge_parse_fail, max_parse_failures=None
    )
    assert d["should_continue"] is False
    assert (await mgr.get("umo1")).status == "paused"


async def test_inactive_goal(kv):
    mgr = GoalManager(kv)
    d = await mgr.evaluate_after_turn("umo1", "resp", judge_continue)
    assert d["verdict"] == "inactive"
    assert d["should_continue"] is False
