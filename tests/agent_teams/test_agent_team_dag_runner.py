"""DAGRunner end-to-end over scripted ports + persistence checks."""

import asyncio

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from astrbot.dashboard.services.agent_team_run_service import (
    AgentTeamRunService,
    DAGRunner,
    RunEventBus,
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

GRAPH = {
    "nodes": [
        {"id": "n1", "member_id": "mA", "task": "调研 {{input}}"},
        {"id": "n2", "member_id": "mB", "task": "校对 {{input}}"},
        {"id": "n3", "member_id": "mC", "task": "汇总 {{n1}} 与 {{n2}}"},
    ],
    "edges": [{"from": "n1", "to": "n3"}, {"from": "n2", "to": "n3"}],
}

MEMBER_BY_SESSION: dict[str, dict] = {}


def make_members():
    members = []
    for i, raw in enumerate(MEMBERS):
        members.append(
            {
                "member_id": f"m{'ABC'[i]}",
                "name": raw["name"],
                "session_id": f"conv-{i}",
                "umo": f"webchat:FriendMessage:conv-{i}",
                "persona_id": raw.get("persona_id"),
                "provider_id": None,
                "system_prompt": None,
            }
        )
    MEMBER_BY_SESSION.clear()
    for m in members:
        MEMBER_BY_SESSION[m["session_id"]] = m
    return members


def scripted_ports(responses: dict, events: list, delivered: list) -> TeamPorts:
    """Ports whose collect() returns scripted per-member replies."""

    async def deliver(session_id: str, text: str, context=None) -> str:
        delivered.append((session_id, text))
        return f"mid-{len(delivered)}"

    async def collect(session_id: str, message_id: str) -> tuple[str, list]:
        member = MEMBER_BY_SESSION[session_id]
        result = responses[member["name"]]
        await asyncio.sleep(0.01)
        if isinstance(result, Exception):
            raise result
        return result, [{"type": "plain", "data": result}]

    return TeamPorts(
        deliver=deliver,
        collect=collect,
        is_busy=lambda sid: False,
        emit=events.append,
    )


CONFIG = {
    "failure_policy": "pause",
    "reply_timeout": 5.0,
    "max_parallel": 5,
    "inject_max_length": 4000,
}


async def wait_terminal(runner: DAGRunner, timeout_s: float = 5.0):
    for _ in range(int(timeout_s / 0.02)):
        if runner.status in ("completed", "paused", "stopped", "failed"):
            return
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_dag_runner_happy_path(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    make_members()
    events: list = []
    delivered: list = []
    ports = scripted_ports(
        {"主管": "调研完成", "写手": "校对完成", "审校": "汇总完成"}, events, delivered
    )
    bus = RunEventBus()
    runner = DAGRunner(
        run_id="r1",
        team_id="t1",
        graph=GRAPH,
        config=CONFIG,
        members=list(MEMBER_BY_SESSION.values()),
        ports=ports,
        db=db,
        bus=bus,
        username="alice",
        run_input="测试主题",
    )
    await runner.run()

    assert runner.status == "completed"
    assert all(s["status"] == "done" for s in runner.node_states.values())
    # n3's rendered task received BOTH predecessor results
    n3_delivery = next(d for d in delivered if d[0] == "conv-2")
    assert "调研完成" in n3_delivery[1] and "校对完成" in n3_delivery[1]
    row = await db.get_agent_team_run("r1")
    assert row.status == "completed"  # persisted
    assert any(e["type"] == "dag_progress" for e in bus.history())


@pytest.mark.asyncio
async def test_failure_policy_pause_then_retry(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    make_members()
    events: list = []
    delivered: list = []
    responses = {"主管": "ok", "写手": RuntimeError("session gone"), "审校": "x"}
    ports = scripted_ports(responses, events, delivered)
    runner = DAGRunner(
        run_id="r2",
        team_id="t1",
        graph=GRAPH,
        config=CONFIG,
        members=list(MEMBER_BY_SESSION.values()),
        ports=ports,
        db=db,
        bus=RunEventBus(),
        username="alice",
        run_input="主题",
    )
    await runner.run()
    await wait_terminal(runner)
    assert runner.status == "paused"
    failed = [n for n, s in runner.node_states.items() if s["status"] == "failed"]
    assert failed

    responses["写手"] = "修好了"
    await runner.retry_node(failed[0])
    runner.task = asyncio.create_task(runner.run())
    for _ in range(250):
        if runner.status == "completed":
            break
        await asyncio.sleep(0.02)
    assert runner.status == "completed"


@pytest.mark.asyncio
async def test_failure_policy_auto_skip_cascades(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    make_members()
    events: list = []
    delivered: list = []
    responses = {"主管": "ok", "写手": RuntimeError("boom"), "审校": "x"}
    ports = scripted_ports(responses, events, delivered)
    runner = DAGRunner(
        run_id="r3",
        team_id="t1",
        graph=GRAPH,
        config={**CONFIG, "failure_policy": "auto_skip"},
        members=list(MEMBER_BY_SESSION.values()),
        ports=ports,
        db=db,
        bus=RunEventBus(),
        username="alice",
        run_input="主题",
    )
    await runner.run()
    assert runner.status == "completed"
    assert runner.node_states["n3"]["status"] == "skipped"  # cascade from n2


@pytest.mark.asyncio
async def test_start_run_full_lifecycle(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    chat = FakeChatService()
    team_svc = AgentTeamService(
        db=db, core_lifecycle=FakeCoreLifecycle(), chat_service=chat
    )
    team = await team_svc.create_team(
        "alice", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    # Scripted collect resolves replies through the shared session map; the
    # team's real member sessions must be registered there.
    MEMBER_BY_SESSION.clear()
    for m in team["members"]:
        MEMBER_BY_SESSION[m["session_id"]] = m
    ids = [m["member_id"] for m in team["members"]]
    graph = {
        "nodes": [
            {"id": "n1", "member_id": ids[0], "task": "做 {{input}}"},
            {"id": "n2", "member_id": ids[1], "task": "查 {{n1}}"},
        ],
        "edges": [{"from": "n1", "to": "n2"}],
    }
    wf = await team_svc.create_workflow(
        "alice", team["team_id"], {"name": "w", "graph": graph}
    )
    run_svc = AgentTeamRunService(db=db, chat_service=chat)
    responses = {m["name"]: f"{m['name']}-done" for m in team["members"]}
    events: list = []
    delivered: list = []
    run_svc.ports_factory = lambda username, emit: scripted_ports(
        responses, events, delivered
    )

    snapshot = await run_svc.start_run(
        "alice",
        team["team_id"],
        {"mode": "dag", "input": "主题", "workflow_id": wf["workflow_id"]},
    )
    runner = run_svc._runners[snapshot["run_id"]]
    await wait_terminal(runner)
    snap = run_svc.get_run_snapshot("alice", snapshot["run_id"])
    assert snap["status"] == "completed"

    # a second run is rejected while an active row exists (API maps to 409)
    await db.create_agent_team_run(
        run_id="rbad",
        team_id=team["team_id"],
        workflow_id=None,
        mode="dag",
        input="x",
        status="running",
        graph_snapshot={},
        node_states={},
        rounds=[],
    )
    with pytest.raises(AgentTeamsServiceError, match="active run"):
        await run_svc.start_run(
            "alice",
            team["team_id"],
            {"mode": "dag", "input": "y", "workflow_id": wf["workflow_id"]},
        )
