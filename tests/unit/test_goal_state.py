"""Tests for GoalState serialization and rendering (ported from the
astrbot_plugin_goal plugin when the goal loop moved into the kernel)."""

from astrbot.core.goal.goal_manager import DEFAULT_MAX_TURNS
from astrbot.core.goal.goal_state import GoalState


def test_default_fields():
    s = GoalState(goal="write report")
    assert s.status == "active"
    assert s.turns_used == 0
    assert s.max_turns == DEFAULT_MAX_TURNS
    assert s.subgoals == []
    assert s.consecutive_parse_failures == 0


def test_round_trip():
    s = GoalState(
        goal="g",
        status="paused",
        turns_used=3,
        max_turns=10,
        created_at=1.0,
        last_turn_at=2.0,
        last_verdict="continue",
        last_reason="r",
        paused_reason="budget",
        consecutive_parse_failures=1,
        subgoals=["a", "b"],
    )
    s2 = GoalState.from_dict(s.to_dict())
    assert s2 == s


def test_from_dict_tolerates_missing_and_bad_subgoals():
    s = GoalState.from_dict({"goal": "g", "subgoals": ["  x ", "", None, 5]})
    assert s.max_turns == DEFAULT_MAX_TURNS
    assert s.subgoals == ["x", "5"]


def test_render_subgoals_block():
    assert GoalState(goal="g").render_subgoals_block() == ""
    block = GoalState(goal="g", subgoals=["a", "b"]).render_subgoals_block()
    assert block == "- 1. a\n- 2. b"


def test_status_line_variants():
    active = GoalState(goal="g", turns_used=2, max_turns=5)
    assert "进行中" in active.status_line() and "2/5" in active.status_line()
    paused = GoalState(goal="g", status="paused", paused_reason="budget exhausted")
    assert (
        "已暂停" in paused.status_line() and "budget exhausted" in paused.status_line()
    )
    done = GoalState(goal="g", status="done")
    assert "已完成" in done.status_line()


def test_new_fields_default():
    s = GoalState(goal="g", goal_id="id1")
    assert s.epoch == 1 and s.consecutive_transport_failures == 0


def test_from_dict_backfills_goal_id_and_epoch():
    s = GoalState.from_dict({"goal": "g", "status": "active"})
    assert s.goal_id and len(s.goal_id) == 32 and s.epoch == 1


def test_from_dict_invalid_status_becomes_paused():
    s = GoalState.from_dict({"goal": "g", "status": "weird"})
    assert s.status == "paused"
    assert s.paused_reason == "recovered from invalid status"


def test_to_public_dict_shape():
    s = GoalState(goal="g", goal_id="id1", created_at=1_700_000_000.0)
    d = s.to_public_dict()
    assert d["goal_id"] == "id1" and "T" in d["created_at"]
    assert set(d) == {
        "goal",
        "goal_id",
        "status",
        "turns_used",
        "max_turns",
        "subgoals",
        "last_verdict",
        "last_reason",
        "paused_reason",
        "created_at",
    }
