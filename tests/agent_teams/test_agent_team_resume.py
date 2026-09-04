"""Boot sweep + hot resume semantics (spec §6.5)."""

import asyncio

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from astrbot.dashboard.services.agent_team_run_service import AgentTeamRunService
from astrbot.dashboard.services.agent_team_service import AgentTeamsServiceError
from tests.agent_teams.test_agent_team_service import FakeChatService


@pytest.mark.asyncio
async def test_boot_sweep_marks_stale_runs_interrupted(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    for run_id, status in (("r1", "running"), ("r2", "paused"), ("r3", "completed")):
        await db.create_agent_team_run(
            run_id=run_id,
            team_id="t1",
            workflow_id=None,
            mode="dag",
            input="x",
            status=status,
            graph_snapshot={},
            node_states={},
            rounds=[],
        )
    svc = AgentTeamRunService(db=db, chat_service=FakeChatService())
    assert await svc.boot_sweep() == 2
    assert (await db.get_agent_team_run("r1")).status == "interrupted"
    assert (await db.get_agent_team_run("r2")).status == "interrupted"
    assert (await db.get_agent_team_run("r3")).status == "completed"


@pytest.mark.asyncio
async def test_resume_preserves_done_and_redoes_running(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    events: list = []
    delivered: list = []

    async def deliver(session_id, text, context=None):
        delivered.append((session_id, text))
        return f"mid-{len(delivered)}"

    async def collect(session_id, message_id):
        await asyncio.sleep(0.01)
        return "重做完成", [{"type": "plain", "data": "重做完成"}]

    svc = AgentTeamRunService(db=db, chat_service=FakeChatService())
    svc.ports_factory = lambda username, emit: TeamPorts(
        deliver=deliver,
        collect=collect,
        is_busy=lambda sid: False,
        emit=events.append,
    )

    members = [
        {
            "member_id": "m1",
            "name": "a",
            "session_id": "c1",
            "umo": "webchat:FriendMessage:c1",
            "persona_id": None,
            "provider_id": None,
            "system_prompt": None,
        },
        {
            "member_id": "m2",
            "name": "b",
            "session_id": "c2",
            "umo": "webchat:FriendMessage:c2",
            "persona_id": None,
            "provider_id": None,
            "system_prompt": None,
        },
    ]
    graph = {
        "nodes": [
            {"id": "n1", "member_id": "m1", "task": "t1"},
            {"id": "n2", "member_id": "m2", "task": "t2 {{n1}}"},
        ],
        "edges": [{"from": "n1", "to": "n2"}],
    }
    node_states = {
        "n1": {
            "status": "done",
            "member_id": "m1",
            "task_rendered": "t1",
            "result": "前驱结果",
            "error": None,
            "started_at": None,
            "finished_at": None,
        },
        "n2": {
            "status": "running",
            "member_id": "m2",
            "task_rendered": None,
            "result": None,
            "error": None,
            "started_at": None,
            "finished_at": None,
        },
    }
    await db.create_agent_team_run(
        run_id="r1",
        team_id="t1",
        workflow_id=None,
        mode="dag",
        input="x",
        status="interrupted",
        graph_snapshot=graph,
        node_states=node_states,
        rounds=[],
    )
    await db.create_agent_team(
        team_id="t1",
        owner_username="alice",
        name="t",
        coordinator_member_id="m1",
        members=members,
        config={},
    )

    snapshot = await svc.resume_run("alice", "r1")
    runner = svc._runners[snapshot["run_id"]]
    for _ in range(250):
        if runner.status in ("completed", "paused", "stopped", "failed"):
            break
        await asyncio.sleep(0.02)
    snap = await svc.get_run_snapshot("alice", "r1")
    assert snap["status"] == "completed"
    # n1's done result was preserved (never re-delivered); n2 was redone and
    # received n1's preserved result in its rendered task
    assert snap["node_states"]["n1"]["result"] == "前驱结果"
    n2_delivery = next(d for d in delivered if d[0] == "c2")
    assert "前驱结果" in n2_delivery[1]


@pytest.mark.asyncio
async def test_resume_enforces_ownership(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    await db.create_agent_team_run(
        run_id="r1",
        team_id="t1",
        workflow_id=None,
        mode="dag",
        input="x",
        status="interrupted",
        graph_snapshot={},
        node_states={},
        rounds=[],
    )
    await db.create_agent_team(
        team_id="t1",
        owner_username="alice",
        name="t",
        coordinator_member_id="m1",
        members=[],
        config={},
    )
    svc = AgentTeamRunService(db=db, chat_service=FakeChatService())
    with pytest.raises(AgentTeamsServiceError):
        await svc.resume_run("mallory", "r1")
