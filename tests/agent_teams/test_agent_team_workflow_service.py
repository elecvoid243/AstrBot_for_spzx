"""Workflow CRUD and validation tests."""

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_service import (
    AgentTeamService,
    AgentTeamsServiceError,
)
from tests.agent_teams.test_agent_team_service import (
    MEMBERS,
    FakeChatService,
    FakeCoreLifecycle,
)

GRAPH = {
    "nodes": [
        {"id": "n1", "member_id": "unbound", "task": "调研 {{input}}"},
        {"id": "n2", "member_id": "unbound", "task": "根据 {{n1}} 写作"},
    ],
    "edges": [{"from": "n1", "to": "n2"}],
}


async def make_service(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    return db, AgentTeamService(
        db=db, core_lifecycle=FakeCoreLifecycle(), chat_service=FakeChatService()
    )


def bind_graph(graph: dict, team: dict) -> dict:
    """Point every node at a real, non-coordinator member id of the team."""
    member_ids = [m["member_id"] for m in team["members"]][1:]
    nodes = [
        {**n, "member_id": member_ids[i % len(member_ids)]}
        for i, n in enumerate(graph["nodes"])
    ]
    return {**graph, "nodes": nodes}


async def make_team(svc):
    return await svc.create_team(
        "alice", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )


@pytest.mark.asyncio
async def test_workflow_crud_roundtrip(tmp_path):
    db, svc = await make_service(tmp_path)
    team = await make_team(svc)
    wf = await svc.create_workflow(
        "alice", team["team_id"], {"name": "流水线", "graph": bind_graph(GRAPH, team)}
    )
    assert wf["graph"]["edges"] == [{"from": "n1", "to": "n2"}]

    new_graph = bind_graph(
        {"nodes": [{"id": "a", "member_id": "x", "task": "t"}], "edges": []}, team
    )
    updated = await svc.update_workflow(
        "alice", team["team_id"], wf["workflow_id"], {"name": "v2", "graph": new_graph}
    )
    assert updated["name"] == "v2" and len(updated["graph"]["nodes"]) == 1

    await svc.delete_workflow("alice", team["team_id"], wf["workflow_id"])
    assert (await svc.get_workflows("alice", team["team_id"]))["workflows"] == []


def field_by_path(exc: AgentTeamsServiceError, path: str) -> dict:
    """Return the field error entry recorded for a path."""
    return next(e for e in exc.field_errors if e["path"] == path)


@pytest.mark.asyncio
async def test_workflow_rejects_cycle_and_bad_member(tmp_path):
    _, svc = await make_service(tmp_path)
    team = await make_team(svc)
    cyclic = bind_graph(
        {
            "nodes": [
                {"id": "n1", "member_id": "x", "task": "t"},
                {"id": "n2", "member_id": "x", "task": "t"},
            ],
            "edges": [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n1"}],
        },
        team,
    )
    with pytest.raises(AgentTeamsServiceError) as exc_info:
        await svc.create_workflow(
            "alice", team["team_id"], {"name": "c", "graph": cyclic}
        )
    cycle = field_by_path(exc_info.value, "edges")
    assert cycle["code"] == "CYCLE"
    assert "cycle" in cycle["message"]

    ghost = bind_graph(GRAPH, team)
    ghost["nodes"][0]["member_id"] = "missing-member"
    with pytest.raises(AgentTeamsServiceError) as exc_info:
        await svc.create_workflow(
            "alice", team["team_id"], {"name": "g", "graph": ghost}
        )
    member = field_by_path(exc_info.value, "nodes.n1.member_id")
    assert member["code"] == "NOT_FOUND"
    assert "节点绑定的成员不存在: missing-member" in member["message"]


@pytest.mark.asyncio
async def test_workflow_validation_collects_all_field_errors(tmp_path):
    """Empty task + unknown member + cycle are reported TOGETHER as one
    field_errors raise (collect-all, not first-raise)."""
    _, svc = await make_service(tmp_path)
    team = await make_team(svc)
    bad = {
        "nodes": [
            {"id": "n1", "member_id": "ghost-member", "task": "   "},
            {"id": "n2", "member_id": "ghost-member", "task": "根据 {{n1}} 写作"},
        ],
        "edges": [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n1"}],
    }
    with pytest.raises(AgentTeamsServiceError) as exc_info:
        await svc.create_workflow("alice", team["team_id"], {"name": "c", "graph": bad})
    exc = exc_info.value
    assert "工作流校验失败" in str(exc)
    assert len(exc.field_errors) >= 3
    task = field_by_path(exc, "nodes.n1.task")
    assert task["code"] == "REQUIRED"
    assert "缺少任务模板" in task["message"]
    for node_id in ("n1", "n2"):
        member = field_by_path(exc, f"nodes.{node_id}.member_id")
        assert member["code"] == "NOT_FOUND"
        assert "节点绑定的成员不存在: ghost-member" in member["message"]
    edges = field_by_path(exc, "edges")
    assert edges["code"] == "CYCLE"
    assert "cycle" in edges["message"]


@pytest.mark.asyncio
async def test_workflow_validation_collects_execution_field_errors(tmp_path):
    """Execution-block problems become per-field errors keeping the Plan 1 T7
    message wording, collected alongside other problems."""
    _, svc = await make_service(tmp_path)
    team = await make_team(svc)
    ids = [m["member_id"] for m in team["members"]]
    graph = {
        "nodes": [
            {"id": "n1", "member_id": ids[0], "task": "调研"},
            {
                "id": "n2",
                "member_id": ids[1],
                "task": "写作",
                "execution": {
                    "config_id": "cfg-gone",
                    "persona_id": "ghost-persona",
                    "tools": "web_search",
                },
            },
        ],
        "edges": [{"from": "n1", "to": "n2"}],
    }
    with pytest.raises(AgentTeamsServiceError) as exc_info:
        await svc.create_workflow(
            "alice", team["team_id"], {"name": "w", "graph": graph}
        )
    exc = exc_info.value
    config = field_by_path(exc, "nodes.n2.execution.config_id")
    assert config["code"] == "NOT_FOUND"
    assert config["message"] == "节点 n2 的配置档案不存在: cfg-gone"
    persona = field_by_path(exc, "nodes.n2.execution.persona_id")
    assert persona["code"] == "NOT_FOUND"
    assert persona["message"] == "节点 n2 的角色不存在: ghost-persona"
    tools = field_by_path(exc, "nodes.n2.execution.tools")
    assert tools["code"] == "INVALID"
    assert tools["message"] == "节点 n2 的 tools 必须是非空字符串列表"


@pytest.mark.asyncio
async def test_workflow_validation_reports_duplicate_node_ids(tmp_path):
    _, svc = await make_service(tmp_path)
    team = await make_team(svc)
    dup = bind_graph(
        {
            "nodes": [
                {"id": "n1", "member_id": "x", "task": "t"},
                {"id": "n1", "member_id": "x", "task": "t2"},
            ],
            "edges": [],
        },
        team,
    )
    with pytest.raises(AgentTeamsServiceError) as exc_info:
        await svc.create_workflow("alice", team["team_id"], {"name": "d", "graph": dup})
    entries = [e for e in exc_info.value.field_errors if e["code"] == "DUPLICATE"]
    assert entries and "duplicate" in entries[0]["message"]


def test_plain_service_error_has_no_field_errors():
    exc = AgentTeamsServiceError("boom")
    assert exc.field_errors == []
    assert str(exc) == "boom"


@pytest.mark.asyncio
async def test_update_revalidates_against_current_members(tmp_path):
    db, svc = await make_service(tmp_path)
    team = await make_team(svc)
    wf = await svc.create_workflow(
        "alice", team["team_id"], {"name": "w", "graph": bind_graph(GRAPH, team)}
    )
    victim = next(
        m
        for m in team["members"]
        if m["member_id"] == wf["graph"]["nodes"][0]["member_id"]
    )
    await svc.remove_member("alice", team["team_id"], victim["member_id"])
    with pytest.raises(AgentTeamsServiceError):
        await svc.update_workflow(
            "alice", team["team_id"], wf["workflow_id"], {"name": "v2"}
        )


@pytest.mark.asyncio
async def test_delete_team_purges_workflows(tmp_path):
    db, svc = await make_service(tmp_path)
    team = await make_team(svc)
    wf = await svc.create_workflow(
        "alice", team["team_id"], {"name": "w", "graph": bind_graph(GRAPH, team)}
    )
    await svc.delete_team("alice", team["team_id"])
    assert await db.get_agent_team_workflow(wf["workflow_id"]) is None
