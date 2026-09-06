"""Stop semantics: abandon in-flight collection on stop + member cancel
propagation (spec §6.3/§10).

Timeout and stop must be distinguished: a reply timeout still lands the run
on paused ("reply timeout" / "coordinator timeout"), while a user stop
abandons the in-flight turn promptly and lands terminal `stopped`.
"""

import asyncio

import pytest

from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.utils.active_event_registry import active_event_registry
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from astrbot.dashboard.services.agent_team_run_service import (
    AgentTeamRunService,
    AutoOrchestrator,
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

CONFIG = {
    "failure_policy": "pause",
    "reply_timeout": 5.0,
    "max_parallel": 5,
    "max_rounds": 5,
    "inject_max_length": 4000,
}

STOP_POLL_S = 0.01


async def wait_until(predicate, timeout_s: float = 5.0) -> None:
    """Poll a condition, failing the test when it never becomes true."""
    for _ in range(int(timeout_s / STOP_POLL_S)):
        if predicate():
            return
        await asyncio.sleep(STOP_POLL_S)
    raise AssertionError("condition not reached within timeout")


def make_members() -> list[dict]:
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
    return members


def blocking_ports(members: list[dict], events: list, gate: asyncio.Event) -> TeamPorts:
    """Ports whose collect() blocks on `gate` (never set by the stop tests)."""

    async def deliver(
        session_id: str, text: str, context=None, execution_token=None
    ) -> str:
        events.append({"type": "sent", "session_id": session_id})
        return f"mid-{len(events)}"

    async def collect(
        session_id: str,
        message_id: str,
        member_id: str | None = None,
        on_event=None,
    ) -> tuple[str, list]:
        await gate.wait()
        return "", []

    return TeamPorts(
        deliver=deliver,
        collect=collect,
        is_busy=lambda sid: False,
        emit=events.append,
    )


def make_dag_runner(run_id: str, members: list[dict], ports, db, bus) -> DAGRunner:
    """Single-node DAG over the first member (conv-0)."""
    return DAGRunner(
        run_id=run_id,
        team_id="t1",
        graph={"nodes": [{"id": "n1", "member_id": "mA", "task": "t"}], "edges": []},
        config=CONFIG,
        members=members,
        ports=ports,
        db=db,
        bus=bus,
        username="alice",
        run_input="x",
    )


@pytest.mark.asyncio
async def test_stop_abandons_collection_mid_turn(tmp_path):
    """A stop during an in-flight collect must settle run() promptly (not
    wait out reply_timeout) and land terminal `stopped` (spec §6.3)."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_members()
    gate = asyncio.Event()  # the reply never arrives
    events: list = []
    ports = blocking_ports(members, events, gate)
    bus = RunEventBus()
    runner = make_dag_runner(
        "rstop1",
        members,
        ports,
        db,
        bus,
    )
    runner.config["reply_timeout"] = 60.0  # prove abandonment, not timeout
    task = asyncio.create_task(runner.run())

    await wait_until(lambda: runner.node_states["n1"]["status"] == "running")
    runner.request_stop()
    # Without collect-abandonment this waits out the 60s reply_timeout.
    await asyncio.wait_for(task, timeout=2.0)

    assert runner.status == "stopped"
    row = await db.get_agent_team_run("rstop1")
    assert row.status == "stopped"
    stopped = [e for e in bus.history() if e.get("type") == "stopped"]
    assert stopped and stopped[-1]["reason"] == "user stop"
    # The abandoned turn is never marked done/failed: nothing further is
    # emitted for the node, so its state stays "running" (honest record of
    # the abandonment; stopped rows cannot resume).
    assert runner.node_states["n1"]["status"] == "running"


@pytest.mark.asyncio
async def test_reply_timeout_still_pauses_not_stops(tmp_path):
    """No stop requested: a reply timeout keeps the old behavior — node
    failed "reply timeout", run paused per the failure policy."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_members()
    gate = asyncio.Event()
    events: list = []
    ports = blocking_ports(members, events, gate)
    runner = make_dag_runner("rstop2", members, ports, db, RunEventBus())
    runner.config["reply_timeout"] = 0.2
    await asyncio.wait_for(runner.run(), timeout=5.0)

    assert runner.status == "paused"
    assert runner.node_states["n1"]["status"] == "failed"
    assert runner.node_states["n1"]["error"] == "reply timeout"


@pytest.mark.asyncio
async def test_stop_mid_turn_lands_stopped_not_paused(tmp_path):
    """With both a timeout possible and a stop requested, the stop wins:
    the run must land `stopped`, never the paused timeout branch."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_members()
    gate = asyncio.Event()
    events: list = []
    ports = blocking_ports(members, events, gate)
    bus = RunEventBus()
    runner = make_dag_runner("rstop3", members, ports, db, bus)
    runner.config["reply_timeout"] = 30.0
    task = asyncio.create_task(runner.run())
    await wait_until(lambda: runner.node_states["n1"]["status"] == "running")
    runner.request_stop()
    await asyncio.wait_for(task, timeout=2.0)

    assert runner.status == "stopped"
    paused = [e for e in bus.history() if e.get("type") == "paused"]
    assert not paused


@pytest.mark.asyncio
async def test_stop_propagates_member_dict(tmp_path):
    """Service-level stop invokes on_member_stop once per in-flight node with
    the FULL member dict (the hook maps member -> umo for cancellation)."""
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
    wf = await team_svc.create_workflow(
        "alice",
        team["team_id"],
        {
            "name": "w",
            "graph": {"nodes": [{"id": "n1", "member_id": ids[0], "task": "做"}]},
        },
    )
    spy: list = []
    run_svc = AgentTeamRunService(db=db, chat_service=chat, on_member_stop=spy.append)
    gate = asyncio.Event()

    async def deliver(
        session_id: str, text: str, context=None, execution_token=None
    ) -> str:
        return "mid-1"

    async def collect(
        session_id: str,
        message_id: str,
        member_id: str | None = None,
        on_event=None,
    ) -> tuple[str, list]:
        await gate.wait()
        return "", []

    run_svc.ports_factory = lambda username, emit: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda sid: False, emit=emit
    )
    snap = await run_svc.start_run(
        "alice",
        team["team_id"],
        {"mode": "dag", "input": "主题", "workflow_id": wf["workflow_id"]},
    )
    run_id = snap["run_id"]
    runner = run_svc._runners[run_id]
    member_a = team["members"][0]
    await wait_until(lambda: runner.node_states["n1"]["status"] == "running")

    await run_svc.request_stop_run("alice", run_id)
    await asyncio.wait_for(runner.task, timeout=2.0)

    assert runner.status == "stopped"
    assert len(spy) == 1
    assert spy[0] is member_a or spy[0] == member_a
    assert spy[0]["member_id"] == member_a["member_id"]
    assert spy[0]["umo"] == member_a["umo"]
    assert spy[0]["umo"].endswith(member_a["session_id"])


@pytest.mark.asyncio
async def test_auto_stop_propagates_coordinator_and_members(tmp_path):
    """Auto mode stop propagation: mid-coordinator-turn stop calls the hook
    with the coordinator member dict; mid-wave stop calls it with the
    in-flight member dicts. In-flight tracking is an explicit
    `_in_flight_members` list maintained by the runner (coordinator turn and
    wave window) and snapshotted by request_stop()."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_members()
    coordinator = members[0]

    def auto_ports_with_gate(script: dict, gate: asyncio.Event, events: list):
        """Coordinator turns fire scripted actions in deliver(); collect()
        blocks for sessions listed in `blocking` (mutated by the test)."""
        turns = {"n": 0}
        blocking: set[str] = set()

        async def deliver(
            session_id: str, text: str, context=None, execution_token=None
        ) -> str:
            events.append({"type": "sent", "session_id": session_id})
            member = next(m for m in members if m["session_id"] == session_id)
            if member["member_id"] == coordinator["member_id"]:
                turns["n"] += 1
                action = script.get(turns["n"])
                if action is not None:
                    tools = AgentTeamToolRegistry.get_tools(coordinator["umo"])
                    await action(tools)
            return f"mid-{len(events)}"

        async def collect(
            session_id: str, message_id: str, member_id: str | None = None
        ) -> tuple[str, list]:
            if session_id in blocking:
                await gate.wait()
            return "回复", []

        return TeamPorts(
            deliver=deliver,
            collect=collect,
            is_busy=lambda sid: False,
            emit=events.append,
        ), blocking

    # Scenario A: stop while the coordinator turn is in flight.
    spy_a: list = []
    gate_a = asyncio.Event()
    events_a: list = []
    ports_a, blocking_a = auto_ports_with_gate({}, gate_a, events_a)
    # Gate the coordinator's collect so the turn is guaranteed to be in
    # flight when the stop lands: ungated, both no-tool turns can complete
    # before the first poll and the run reaches paused, not stopped.
    blocking_a.add(coordinator["session_id"])
    orch_a = AutoOrchestrator(
        run_id="rastop",
        team_id="t1",
        team_name="内容团队",
        members=members,
        coordinator=coordinator,
        config=CONFIG,
        run_input="目标",
        ports=ports_a,
        db=db,
        bus=RunEventBus(),
        username="alice",
        on_member_stop=spy_a.append,
    )
    task_a = asyncio.create_task(orch_a.run())
    await wait_until(
        lambda: any(
            e.get("type") == "sent" and e.get("session_id") == coordinator["session_id"]
            for e in events_a
        )
    )
    orch_a.request_stop()
    await asyncio.wait_for(task_a, timeout=2.0)

    assert orch_a.status == "stopped"
    assert len(spy_a) == 1
    assert spy_a[0]["member_id"] == coordinator["member_id"]
    assert spy_a[0]["umo"] == coordinator["umo"]

    # Scenario B: stop while a member wave turn is in flight.
    spy_b: list = []
    gate_b = asyncio.Event()
    events_b: list = []

    async def turn1(tools):
        await next(t for t in tools if t.name == "team_dispatch").call(
            None, assignments=[{"member": "写手", "task": "写初稿"}]
        )

    ports_b, blocking_b = auto_ports_with_gate({1: turn1}, gate_b, events_b)
    orch_b = AutoOrchestrator(
        run_id="rbstop",
        team_id="t1",
        team_name="内容团队",
        members=members,
        coordinator=coordinator,
        config=CONFIG,
        run_input="目标",
        ports=ports_b,
        db=db,
        bus=RunEventBus(),
        username="alice",
        on_member_stop=spy_b.append,
    )
    task_b = asyncio.create_task(orch_b.run())
    writer = next(m for m in members if m["name"] == "写手")
    blocking_b.add(writer["session_id"])
    await wait_until(
        lambda: any(
            e.get("type") == "sent" and e.get("session_id") == writer["session_id"]
            for e in events_b
        )
    )
    orch_b.request_stop()
    await asyncio.wait_for(task_b, timeout=2.0)

    assert orch_b.status == "stopped"
    assert len(spy_b) == 1
    assert spy_b[0]["member_id"] == writer["member_id"]
    assert spy_b[0]["umo"] == writer["umo"]
    row = await db.get_agent_team_run("rbstop")
    assert row.status == "stopped"


@pytest.mark.asyncio
async def test_service_without_hook_defaults_to_none(tmp_path):
    """on_member_stop stays optional: a service built without the hook stops
    runs normally (app wiring is responsible for supplying the real hook)."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_members()
    gate = asyncio.Event()
    events: list = []
    ports = blocking_ports(members, events, gate)
    bus = RunEventBus()
    runner = make_dag_runner("rnohook", members, ports, db, bus)
    runner.config["reply_timeout"] = 30.0
    task = asyncio.create_task(runner.run())
    await wait_until(lambda: runner.node_states["n1"]["status"] == "running")
    runner.request_stop()  # on_member_stop=None: must not raise
    await asyncio.wait_for(task, timeout=2.0)
    assert runner.status == "stopped"


# ---------- member interrupt (spec §4.4) ----------


async def make_running_dag_run(tmp_path):
    """Start a single-node DAG run via the service with a gated collect.

    Returns:
        (db, run_svc, run_id, runner, team, gate) with the node already in
        the `running` state and the reply collection blocked on `gate`.
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
    wf = await team_svc.create_workflow(
        "alice",
        team["team_id"],
        {
            "name": "w",
            "graph": {
                "nodes": [
                    {
                        "id": "n1",
                        "member_id": team["members"][0]["member_id"],
                        "task": "做",
                    }
                ]
            },
        },
    )
    run_svc = AgentTeamRunService(db=db, chat_service=chat)
    gate = asyncio.Event()

    async def deliver(session_id, text, context=None, execution_token=None):
        return "mid-1"

    async def collect(
        session_id: str,
        message_id: str,
        member_id: str | None = None,
        on_event=None,
    ) -> tuple[str, list]:
        await gate.wait()
        return "", []

    run_svc.ports_factory = lambda username, emit: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda sid: False, emit=emit
    )
    snap = await run_svc.start_run(
        "alice",
        team["team_id"],
        {"mode": "dag", "input": "主题", "workflow_id": wf["workflow_id"]},
    )
    runner = run_svc._runners[snap["run_id"]]
    await wait_until(lambda: runner.node_states["n1"]["status"] == "running")
    return db, run_svc, snap["run_id"], runner, team, gate


@pytest.mark.asyncio
async def test_interrupt_marks_running_node_interrupted(tmp_path, monkeypatch):
    """Service interrupt: the member's agent stop is requested, the running
    node lands the independent `interrupted` state, the run pauses (persisted
    + announced), and a late collect result never overwrites the state."""
    db, run_svc, run_id, runner, team, gate = await make_running_dag_run(tmp_path)
    member_a = team["members"][0]
    stops: list[str] = []
    monkeypatch.setattr(
        active_event_registry,
        "request_agent_stop_all",
        lambda umo, exclude=None: stops.append(umo),
    )

    result = await run_svc.interrupt_node("alice", run_id, member_a["member_id"])

    assert result == {"message": "已中断"}
    assert stops == [member_a["umo"]]
    state = runner.node_states["n1"]
    assert state["status"] == "interrupted"
    assert state["error"] == "用户中断"
    assert runner.status == "paused"
    assert runner._progress()["interrupted"] == 1
    row = await db.get_agent_team_run(run_id)
    assert row.status == "paused"
    assert row.node_states["n1"]["status"] == "interrupted"
    history = run_svc._buses[run_id].history()
    node_status = [
        e
        for e in history
        if e.get("type") == "node_status" and e.get("status") == "interrupted"
    ]
    assert node_status and node_status[-1]["node_id"] == "n1"
    paused = [e for e in history if e.get("type") == "paused"]
    assert paused[-1]["reason"] == "node interrupted"
    assert paused[-1]["node_id"] == "n1"

    # A collect resolving after the interrupt must NOT land the node on
    # done/failed: the interrupt owns the terminal state.
    gate.set()
    await asyncio.wait_for(runner.task, timeout=2.0)
    assert runner.node_states["n1"]["status"] == "interrupted"
    assert runner.node_states["n1"]["error"] == "用户中断"
    row = await db.get_agent_team_run(run_id)
    assert row.status == "paused"
    assert row.node_states["n1"]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_interrupt_auto_stops_member_turn_only(tmp_path, monkeypatch):
    """Auto mode has no node states: the interrupt only requests the member's
    agent stop; the orchestrator keeps running and records the partial
    result in the round (spec §4.4)."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    chat = FakeChatService()
    team_svc = AgentTeamService(
        db=db, core_lifecycle=FakeCoreLifecycle(), chat_service=chat
    )
    team = await team_svc.create_team(
        "alice", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    coordinator = team["members"][0]
    stops: list[str] = []
    monkeypatch.setattr(
        active_event_registry,
        "request_agent_stop_all",
        lambda umo, exclude=None: stops.append(umo),
    )
    run_svc = AgentTeamRunService(db=db, chat_service=chat)
    gate = asyncio.Event()
    events: list = []

    async def deliver(session_id, text, context=None, execution_token=None):
        events.append({"type": "sent", "session_id": session_id})
        return "mid-1"

    async def collect(
        session_id: str,
        message_id: str,
        member_id: str | None = None,
        on_event=None,
    ) -> tuple[str, list]:
        if session_id == coordinator["session_id"]:
            await gate.wait()
        return "回复", []

    run_svc.ports_factory = lambda username, emit: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda sid: False, emit=emit
    )
    snap = await run_svc.start_run(
        "alice", team["team_id"], {"mode": "auto", "input": "目标"}
    )
    orch = run_svc._runners[snap["run_id"]]
    await wait_until(
        lambda: any(
            e.get("type") == "sent" and e.get("session_id") == coordinator["session_id"]
            for e in events
        )
    )

    result = await run_svc.interrupt_node(
        "alice", snap["run_id"], coordinator["member_id"]
    )

    assert result == {"message": "已中断"}
    assert stops == [coordinator["umo"]]
    assert orch.status == "running"  # no pause, no node state in auto mode

    # Cleanup: release the turn and stop the orchestrator.
    orch.request_stop()
    await asyncio.wait_for(orch.task, timeout=2.0)
    gate.set()


@pytest.mark.asyncio
async def test_interrupt_unknown_member_errors(tmp_path, monkeypatch):
    """An unknown member raises before any agent stop is requested."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    chat = FakeChatService()
    team_svc = AgentTeamService(
        db=db, core_lifecycle=FakeCoreLifecycle(), chat_service=chat
    )
    team = await team_svc.create_team(
        "alice", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    await db.create_agent_team_run(
        run_id="rint9",
        team_id=team["team_id"],
        workflow_id=None,
        mode="dag",
        input="x",
        status="running",
        graph_snapshot={},
        node_states={},
        rounds=[],
    )
    run_svc = AgentTeamRunService(db=db, chat_service=chat)
    stops: list[str] = []
    monkeypatch.setattr(
        active_event_registry,
        "request_agent_stop_all",
        lambda umo, exclude=None: stops.append(umo),
    )

    with pytest.raises(AgentTeamsServiceError, match="成员"):
        await run_svc.interrupt_node("alice", "rint9", "m-ghost")
    assert stops == []


@pytest.mark.asyncio
async def test_interrupt_persists_after_runner_task_done(tmp_path):
    """Post-exit interrupt (dead runner task) still flips, persists, and
    announces the node state (mirror of the dead-task stop pattern)."""
    db, run_svc, run_id, runner, team, _gate = await make_running_dag_run(tmp_path)
    runner.request_stop()  # lands stopped with the node stuck on "running"
    await asyncio.wait_for(runner.task, timeout=2.0)
    assert runner.status == "stopped"
    assert runner.node_states["n1"]["status"] == "running"

    result = await run_svc.interrupt_node(
        "alice", run_id, team["members"][0]["member_id"]
    )

    assert result == {"message": "已中断"}
    assert runner.node_states["n1"]["status"] == "interrupted"
    row = await db.get_agent_team_run(run_id)
    assert row.node_states["n1"]["status"] == "interrupted"
    history = run_svc._buses[run_id].history()
    assert any(
        e.get("type") == "node_status" and e.get("status") == "interrupted"
        for e in history
    )


@pytest.mark.asyncio
async def test_retry_and_skip_accept_interrupted(tmp_path):
    """retry/skip treat `interrupted` like `failed` (spec §4.4: no cascade,
    but both recovery actions are available)."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_members()
    ports = blocking_ports(members, [], asyncio.Event())
    runner = make_dag_runner("rint2", members, ports, db, RunEventBus())
    runner.node_states["n1"].update(status="interrupted", error="用户中断")
    assert runner._progress()["interrupted"] == 1

    await runner.retry_node("n1")
    assert runner.node_states["n1"]["status"] == "pending"
    assert runner.node_states["n1"]["error"] is None

    runner.node_states["n1"].update(status="interrupted")
    await runner.skip_node("n1")
    assert runner.node_states["n1"]["status"] == "skipped"


@pytest.mark.asyncio
async def test_duplicate_edges_inject_single_block(tmp_path):
    """Fold-in seed: `_preds` dedupes duplicate edges so a repeated edge
    produces exactly one auto-inject block."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_members()
    delivered: list = []
    events: list = []

    async def deliver(session_id, text, context=None, execution_token=None):
        delivered.append((session_id, text))
        return "mid-1"

    async def collect(
        session_id: str,
        message_id: str,
        member_id: str | None = None,
        on_event=None,
    ) -> tuple[str, list]:
        member = next(m for m in members if m["session_id"] == session_id)
        return f"{member['member_id']}-done", []

    graph = {
        "nodes": [
            {"id": "n1", "member_id": "mA", "task": "一 {{input}}"},
            {"id": "n2", "member_id": "mB", "task": "二"},
        ],
        "edges": [{"from": "n1", "to": "n2"}, {"from": "n1", "to": "n2"}],
    }
    runner = DAGRunner(
        run_id="rdup",
        team_id="t1",
        graph=graph,
        config=CONFIG,
        members=members,
        ports=TeamPorts(
            deliver=deliver,
            collect=collect,
            is_busy=lambda sid: False,
            emit=events.append,
        ),
        db=db,
        bus=RunEventBus(),
        username="alice",
        run_input="x",
    )
    assert runner._preds["n2"] == ["n1"]
    await asyncio.wait_for(runner.run(), timeout=5.0)
    assert runner.status == "completed"
    n2_delivery = next(text for sid, text in delivered if sid == "conv-1")
    assert n2_delivery.count("[上游结果]") == 1


@pytest.mark.asyncio
async def test_dead_task_stop_closes_ports(tmp_path):
    """Fold-in seed: stopping a dead-task (failure-paused) run releases the
    ports' resources, like the runner's own terminal path."""
    from tests.agent_teams.test_agent_team_resume import make_failure_paused_run

    db, run_svc, run_id, runner, _responses = await make_failure_paused_run(tmp_path)
    closed: list = []

    async def close():
        closed.append(run_id)

    runner.ports.close = close

    await run_svc.request_stop_run("alice", run_id)

    assert runner.status == "stopped"
    assert closed == [run_id]
