"""Tests for GoalManager mutation operations and the post-turn decision
engine (ported from the astrbot_plugin_goal plugin)."""

import asyncio

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
    return "done", "finished", False, False


async def judge_continue(goal, response, subgoals):
    return "continue", "keep going", False, False


async def judge_parse_fail(goal, response, subgoals):
    return "continue", "judge returned empty response", True, False


async def test_set_and_get(kv):
    mgr = GoalManager(kv)
    state = await mgr.set("umo1", "write report")
    assert state.status == "active"
    assert state.goal_id and state.epoch == 1
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


async def test_legacy_cleared_record_reads_as_none(kv):
    # "cleared" is a legacy status value: such records must never resurrect.
    await kv.set("goal:umo1", {"goal": "g", "status": "cleared"})
    mgr = GoalManager(kv)
    assert await mgr.get("umo1") is None


# ---------------------------------------------------------------------------
# CAS evaluation: verdicts computed against a stale state must be discarded
# ---------------------------------------------------------------------------


async def _run_with_slow_judge(mgr, mutate):
    """Start evaluation, run ``mutate`` while the judge is in flight."""
    release = asyncio.Event()

    async def slow_judge(goal, response, subgoals):
        await release.wait()
        return "continue", "late", False, False

    task = asyncio.create_task(mgr.evaluate_after_turn("umo1", "resp", slow_judge))
    await asyncio.sleep(0)  # judge has started and is now in flight
    await mutate()
    release.set()
    return await task


async def test_clear_during_slow_judge_discards_verdict(kv):
    mgr = GoalManager(kv)
    await mgr.set("umo1", "g")
    decision = await _run_with_slow_judge(mgr, lambda: mgr.clear("umo1"))
    assert decision["status"] == "stale" and decision["should_continue"] is False
    assert await mgr.get("umo1") is None  # state must not be resurrected


async def test_pause_during_slow_judge_keeps_paused(kv):
    mgr = GoalManager(kv)
    await mgr.set("umo1", "g")
    decision = await _run_with_slow_judge(mgr, lambda: mgr.pause("umo1"))
    assert decision["status"] == "stale"
    assert (await mgr.get("umo1")).status == "paused"


async def test_set_new_goal_during_slow_judge_discards_old_verdict(kv):
    mgr = GoalManager(kv)
    old = await mgr.set("umo1", "old goal")

    async def replace():
        await mgr.set("umo1", "new goal")

    decision = await _run_with_slow_judge(mgr, replace)
    assert decision["status"] == "stale"
    state = await mgr.get("umo1")
    assert state.goal == "new goal"
    assert state.turns_used == 0
    assert state.goal_id != old.goal_id


# ---------------------------------------------------------------------------
# state machine transitions
# ---------------------------------------------------------------------------


async def test_resume_only_from_paused_or_blocked(kv):
    mgr = GoalManager(kv)
    await mgr.set("umo1", "g")
    assert await mgr.resume("umo1") is None  # no-op on active
    await mgr.pause("umo1")
    assert (await mgr.resume("umo1")).status == "active"


async def test_blocked_verdict_sets_blocked_status(kv):
    mgr = GoalManager(kv)
    await mgr.set("umo1", "g")

    async def judge_blocked(g, r, s):
        return "blocked", "need user input", False, False

    d = await mgr.evaluate_after_turn("umo1", "resp", judge_blocked)
    assert d["status"] == "blocked" and d["should_continue"] is False
    state = await mgr.get("umo1")
    assert state.status == "blocked"
    # blocked is resumable
    assert (await mgr.resume("umo1")).status == "active"


async def test_transport_failures_pause_after_two(kv):
    mgr = GoalManager(kv)
    await mgr.set("umo1", "g")

    async def judge_transport(g, r, s):
        return "continue", "judge unavailable", False, True

    d1 = await mgr.evaluate_after_turn("umo1", "resp", judge_transport)
    assert d1["should_continue"] is True  # first failure: let the turn through
    d2 = await mgr.evaluate_after_turn("umo1", "resp", judge_transport)
    assert d2["status"] == "paused" and d2["should_continue"] is False
    state = await mgr.get("umo1")
    assert state.consecutive_transport_failures == 2


async def test_control_mutation_bumps_epoch(kv):
    mgr = GoalManager(kv)
    state = await mgr.set("umo1", "g")
    assert state.epoch == 1
    await mgr.pause("umo1")
    assert (await mgr.get("umo1")).epoch == 2
    # evaluation bookkeeping must NOT bump the epoch (CAS would always miss)
    await mgr.resume("umo1")
    epoch_after_resume = (await mgr.get("umo1")).epoch
    await mgr.evaluate_after_turn("umo1", "resp", judge_continue)
    assert (await mgr.get("umo1")).epoch == epoch_after_resume


async def test_manager_emits_on_change(kv):
    events = []

    async def listener(umo, state):
        events.append((umo, state))

    mgr = GoalManager(kv, on_change=listener)
    await mgr.set("umo1", "g")
    await mgr.clear("umo1")
    assert events[0][1].goal == "g" and events[1] == ("umo1", None)
