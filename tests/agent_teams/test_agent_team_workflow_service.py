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
    with pytest.raises(AgentTeamsServiceError, match="cycle"):
        await svc.create_workflow(
            "alice", team["team_id"], {"name": "c", "graph": cyclic}
        )
    ghost = bind_graph(GRAPH, team)
    ghost["nodes"][0]["member_id"] = "missing-member"
    with pytest.raises(AgentTeamsServiceError, match="missing-member"):
        await svc.create_workflow(
            "alice", team["team_id"], {"name": "g", "graph": ghost}
        )


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
