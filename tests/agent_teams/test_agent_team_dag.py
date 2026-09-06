# tests/agent_teams/test_agent_team_dag.py
"""Unit tests for pure DAG helpers."""

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_dag import (
    TeamDAGError,
    downstream_of,
    referenced_placeholders,
    render_task,
    topological_layers,
    validate_dag,
)
from astrbot.dashboard.services.agent_team_service import (
    AgentTeamService,
    AgentTeamsServiceError,
)
from tests.agent_teams.test_agent_team_service import (
    MEMBERS,
    FakeChatService,
    FakeCoreLifecycle,
)

NODES = [{"id": "n1"}, {"id": "n2"}, {"id": "n3"}, {"id": "n4"}]
EDGES = [
    {"from": "n1", "to": "n3"},
    {"from": "n2", "to": "n3"},
    {"from": "n3", "to": "n4"},
]


def test_layers_and_adjacency():
    adj = validate_dag(NODES, EDGES)
    assert adj["n1"] == ["n3"] and adj["n3"] == ["n4"]
    layers = topological_layers(NODES, EDGES)
    assert layers == [["n1", "n2"], ["n3"], ["n4"]]


def test_cycle_rejected():
    edges = EDGES + [{"from": "n4", "to": "n1"}]
    with pytest.raises(TeamDAGError, match="cycle"):
        validate_dag(NODES, edges)


def test_dangling_and_duplicate_rejected():
    with pytest.raises(TeamDAGError, match="unknown node"):
        validate_dag(NODES, [{"from": "nx", "to": "n1"}])
    with pytest.raises(TeamDAGError, match="duplicate"):
        validate_dag([{"id": "n1"}, {"id": "n1"}], [])


def test_downstream_transitive():
    assert sorted(downstream_of("n1", EDGES)) == ["n3", "n4"]
    assert downstream_of("n4", EDGES) == []


def test_render_task():
    out = render_task(
        "目标: {{input}}\n前驱结论: {{n1}}", "写诗", {"n1": "春天来了"}, max_length=100
    )
    assert out == "目标: 写诗\n前驱结论: 春天来了"


def test_render_unknown_placeholder():
    with pytest.raises(TeamDAGError, match="unknown placeholder"):
        render_task("{{nope}}", "x", {"n1": "y"}, max_length=10)


def test_render_truncates_tail():
    out = render_task("{{n1}}", "x", {"n1": "0123456789"}, max_length=4)
    assert out.endswith("6789") and "截断" in out


def test_referenced_placeholders():
    assert referenced_placeholders("a {{n1}} b {{ n2 }} c {{input}}") == {
        "n1",
        "n2",
        "input",
    }
    assert referenced_placeholders("no refs here") == set()


def test_render_task_auto_injects_unreferenced_predecessor():
    out = render_task(
        "总结 {{input}}",
        "写诗",
        {"n1": "春天来了"},
        max_length=100,
        auto_inject_predecessors=[("n1", "研究员")],
    )
    assert out == "总结 写诗\n\n[上游结果]\n◆ 研究员 (n1)：\n春天来了"


def test_render_task_referenced_predecessor_not_reinjected():
    out = render_task(
        "结论 {{n1}}",
        "x",
        {"n1": "春天来了"},
        max_length=100,
        auto_inject_predecessors=[("n1", "研究员")],
    )
    assert out == "结论 春天来了"


def test_render_task_all_referenced_no_block():
    out = render_task(
        "{{n1}} 然后 {{n2}}",
        "x",
        {"n1": "一", "n2": "二"},
        max_length=100,
        auto_inject_predecessors=[("n1", "甲"), ("n2", "乙")],
    )
    assert out == "一 然后 二"
    assert "[上游结果]" not in out


def test_render_task_injects_multiple_predecessors_with_blank_lines():
    out = render_task(
        "汇总",
        "x",
        {"n1": "A结果", "n2": "B结果"},
        max_length=100,
        auto_inject_predecessors=[("n1", "甲"), ("n2", "乙")],
    )
    assert out == (
        "汇总\n\n[上游结果]\n◆ 甲 (n1)：\nA结果\n\n[上游结果]\n◆ 乙 (n2)：\nB结果"
    )


def test_render_task_inject_skips_preds_without_results():
    # A pred absent from results (non-done / empty) contributes no block.
    out = render_task(
        "任务",
        "x",
        {},
        max_length=100,
        auto_inject_predecessors=[("n1", "甲"), ("n2", "乙")],
    )
    assert out == "任务"


def test_render_task_inject_truncates_each_result_with_marker():
    out = render_task(
        "任务",
        "x",
        {"n1": "0123456789", "n2": "短结果"},
        max_length=4,
        auto_inject_predecessors=[("n1", "甲"), ("n2", "乙")],
    )
    assert out == (
        "任务\n\n"
        "[上游结果]\n◆ 甲 (n1)：\n…[已截断，仅保留尾部]\n6789\n\n"
        "[上游结果]\n◆ 乙 (n2)：\n短结果"
    )


def test_render_task_default_and_none_inject_identical():
    a = render_task("{{n1}}", "x", {"n1": "y"}, max_length=10)
    b = render_task(
        "{{n1}}", "x", {"n1": "y"}, max_length=10, auto_inject_predecessors=None
    )
    assert a == b == "y"


# ---------- TeamDAGError kinds + workflow field-error classification ----------
# (fold-in seed: kind-based mapping fixes the missing-id -> edges/CYCLE
# misclassification from Plans 1-2 reviews)


def test_validate_dag_raises_with_kinds():
    with pytest.raises(TeamDAGError) as missing:
        validate_dag([{"id": ""}], [])
    assert missing.value.kind == "missing_id"
    with pytest.raises(TeamDAGError) as dup:
        validate_dag([{"id": "n1"}, {"id": "n1"}], [])
    assert dup.value.kind == "duplicate"
    with pytest.raises(TeamDAGError) as dangling:
        validate_dag(NODES, [{"from": "nx", "to": "n1"}])
    assert dangling.value.kind == "dangling"
    with pytest.raises(TeamDAGError) as cycle:
        validate_dag(NODES, EDGES + [{"from": "n4", "to": "n1"}])
    assert cycle.value.kind == "cycle"


@pytest.mark.asyncio
async def test_workflow_field_errors_classified_by_dag_error_kind(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    svc = AgentTeamService(
        db=db,
        core_lifecycle=FakeCoreLifecycle(),
        chat_service=FakeChatService(),
    )
    team = await svc.create_team(
        "alice", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    member_id = team["members"][0]["member_id"]
    # A node without an id maps to nodes/INVALID (previously edges/CYCLE).
    with pytest.raises(AgentTeamsServiceError) as missing:
        await svc.create_workflow(
            "alice",
            team["team_id"],
            {
                "name": "w1",
                "graph": {
                    "nodes": [{"task": "x", "member_id": member_id}],
                    "edges": [],
                },
            },
        )
    assert missing.value.field_errors[0]["path"] == "nodes"
    assert missing.value.field_errors[0]["code"] == "INVALID"
    # A dangling edge maps to edges/INVALID.
    with pytest.raises(AgentTeamsServiceError) as dangling:
        await svc.create_workflow(
            "alice",
            team["team_id"],
            {
                "name": "w2",
                "graph": {
                    "nodes": [{"id": "n1", "task": "x", "member_id": member_id}],
                    "edges": [{"from": "n1", "to": "ghost"}],
                },
            },
        )
    assert dangling.value.field_errors[0]["path"] == "edges"
    assert dangling.value.field_errors[0]["code"] == "INVALID"
