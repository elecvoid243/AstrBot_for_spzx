"""Boot sweep + hot resume semantics (spec §6.5)."""

import asyncio

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from astrbot.dashboard.services.agent_team_run_service import AgentTeamRunService
from astrbot.dashboard.services.agent_team_service import (
    AgentTeamService,
    AgentTeamsServiceError,
)
from tests.agent_teams.test_agent_team_service import (
    MEMBERS,
    FakeChatService,
    FakeCoreLifecycle,
)


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
async def test_lost_legacy_owner_preserves_done_results_and_refuses_redo(tmp_path):
    """Keep original done and running records read-only after owner loss.

    Args:
        tmp_path: Isolated durable legacy database.
    """
    db = SQLiteDatabase(str(tmp_path / "legacy.db"))
    await db.create_agent_team(
        team_id="t1",
        owner_username="alice",
        name="Original team",
        coordinator_member_id="m1",
        members=MEMBERS,
        config={},
    )
    await db.create_agent_team_run(
        run_id="r1",
        team_id="t1",
        workflow_id=None,
        mode="dag",
        input="Original",
        status="interrupted",
        graph_snapshot={"nodes": [], "edges": []},
        node_states={
            "n1": {"status": "done", "result": "Original predecessor evidence"},
            "n2": {"status": "running", "result": None},
        },
        rounds=[],
    )
    service = AgentTeamRunService(db, None)
    try:
        before = await service.get_history_run("alice", "r1")
        with pytest.raises(AgentTeamsServiceError) as retired:
            await service.resume_run("alice", "r1")
        assert retired.value.status == 410
        assert await service.get_history_run("alice", "r1") == before
    finally:
        await db.engine.dispose()


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


async def make_failure_paused_run(tmp_path):
    """Drive a run into the failure-pause dead-task end state.

    n1 fails with failure_policy=pause; n3 stays blocked behind it, the loop
    breaks, re-parks on paused and run() RETURNS — the in-memory task is
    done while the status still says paused.

    Returns:
        (db, run_svc, run_id, runner, responses) where responses can be
        rewritten to script the retry outcome.
    """
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    chat = FakeChatService()
    team_svc = AgentTeamService(
        db=db, core_lifecycle=FakeCoreLifecycle(), chat_service=chat
    )
    team = await team_svc.create_team(
        "alice", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    ids = [m["member_id"] for m in team["members"]]
    graph = {
        "nodes": [
            {"id": "n1", "member_id": ids[0], "task": "a {{input}}"},
            {"id": "n2", "member_id": ids[1], "task": "b"},
            {"id": "n3", "member_id": ids[2], "task": "c {{n1}} {{n2}}"},
        ],
        "edges": [{"from": "n1", "to": "n3"}, {"from": "n2", "to": "n3"}],
    }
    wf = await team_svc.create_workflow(
        "alice", team["team_id"], {"name": "w", "graph": graph}
    )
    run_svc = AgentTeamRunService(db=db, chat_service=chat)
    responses = {"主管": RuntimeError("boom"), "写手": "写手-ok", "审校": "审校-ok"}
    by_session = {m["session_id"]: m for m in team["members"]}
    delivered: list = []

    async def deliver(session_id, text, context=None, execution_token=None):
        delivered.append((session_id, text))
        return f"mid-{len(delivered)}"

    async def collect(
        session_id: str,
        message_id: str,
        member_id: str | None = None,
        on_event=None,
    ) -> tuple[str, list]:
        result = responses[by_session[session_id]["name"]]
        await asyncio.sleep(0.01)
        if isinstance(result, Exception):
            raise result
        return result, [{"type": "plain", "data": result}]

    run_svc.ports_factory = lambda username, emit: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda sid: False, emit=emit
    )
    snapshot = await run_svc.start_run(
        "alice",
        team["team_id"],
        {"mode": "dag", "input": "主题", "workflow_id": wf["workflow_id"]},
    )
    run_id = snapshot["run_id"]
    runner = run_svc._runners[run_id]
    await runner.task  # run() returned naturally: paused with a dead task
    assert runner.status == "paused"
    assert runner.task.done()
    return db, run_svc, run_id, runner, responses


@pytest.mark.asyncio
async def test_resume_run_respawns_dead_paused_task(tmp_path):
    db, run_svc, run_id, runner, responses = await make_failure_paused_run(tmp_path)
    responses["主管"] = "修好了"

    snap = await run_svc.resume_run("alice", run_id)
    assert snap["status"] == "running"
    # THE regression: a dead task must be replaced by a live one, otherwise
    # the resume only flips a flag nothing will ever observe.
    assert runner.task is not None and not runner.task.done()

    # The respawned task re-parks paused (the failed node still blocks n3)
    # and exits; retrying through the service then respawns again and the
    # remaining nodes complete.
    for _ in range(250):
        if runner.task.done():
            break
        await asyncio.sleep(0.02)
    await run_svc.retry_node("alice", run_id, "n1")
    for _ in range(250):
        if runner.status == "completed":
            break
        await asyncio.sleep(0.02)
    assert runner.status == "completed"
    if runner.task is not None:
        # status flips before the final row persist inside run(); await the
        # task so the DB write is guaranteed to have landed.
        await runner.task
    row = await db.get_agent_team_run(run_id)
    assert row.status == "completed"


@pytest.mark.asyncio
async def test_stop_run_lands_terminal_on_dead_paused_task(tmp_path):
    db, run_svc, run_id, runner, _responses = await make_failure_paused_run(tmp_path)

    await run_svc.request_stop_run("alice", run_id)

    assert runner.status == "stopped"
    row = await db.get_agent_team_run(run_id)
    assert row.status == "stopped"
    stopped = [e for e in run_svc._buses[run_id].history() if e["type"] == "stopped"]
    assert any(e.get("reason") == "user stop" for e in stopped)


@pytest.mark.asyncio
async def test_lost_legacy_owner_does_not_prefail_or_redispatch_nodes(tmp_path):
    """Do not rewrite old task history when a previous profile has disappeared.

    Args:
        tmp_path: Isolated durable legacy database.
    """
    db = SQLiteDatabase(str(tmp_path / "legacy.db"))
    await db.create_agent_team(
        team_id="t1",
        owner_username="alice",
        name="Original team",
        coordinator_member_id="m1",
        members=MEMBERS,
        config={},
    )
    await db.create_agent_team_run(
        run_id="rseed",
        team_id="t1",
        workflow_id=None,
        mode="dag",
        input="Original",
        status="interrupted",
        graph_snapshot={
            "nodes": [
                {
                    "id": "n1",
                    "member_id": "m1",
                    "task": "Original task",
                    "execution": {"config_id": "gone"},
                },
            ],
            "edges": [],
        },
        node_states={"n1": {"status": "pending", "result": None}},
        rounds=[],
    )
    service = AgentTeamRunService(db, None, config_checker=lambda _: False)
    try:
        before = await service.get_history_run("alice", "rseed")
        with pytest.raises(AgentTeamsServiceError) as retired:
            await service.resume_run("alice", "rseed")
        assert retired.value.status == 410
        assert await service.get_history_run("alice", "rseed") == before
    finally:
        await db.engine.dispose()
