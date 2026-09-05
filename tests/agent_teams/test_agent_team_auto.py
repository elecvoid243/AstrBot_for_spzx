"""AutoOrchestrator round-engine tests over scripted ports (spec §6.4)."""

import asyncio

import pytest

from astrbot.core import agent_team_tools
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from astrbot.dashboard.services.agent_team_run_service import (
    AgentTeamRunService,
    AutoOrchestrator,
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
    "max_rounds": 5,
    "max_parallel": 5,
    "inject_max_length": 4000,
}


@pytest.fixture(autouse=True)
def _clean_tool_registry():
    """The tool registry is process-global; keep tests independent."""
    yield
    # _TOOLS is a module-level dict on purpose (single event loop); clear it
    # so a failing test cannot leak registrations into other tests.
    agent_team_tools._TOOLS.clear()


def make_team_members() -> list[dict]:
    """Three scripted members; conv-0 (主管) acts as the coordinator."""
    members = []
    for i, raw in enumerate(MEMBERS):
        members.append(
            {
                "member_id": f"m{'ABC'[i]}",
                "name": raw["name"],
                "session_id": f"conv-{i}",
                "umo": f"webchat:FriendMessage:conv-{i}",
                "persona_id": raw.get("persona_id"),
                "provider_id": raw.get("provider_id"),
                "system_prompt": raw.get("system_prompt"),
            }
        )
    return members


def auto_ports(
    members: list[dict],
    coordinator: dict,
    script: dict,
    responses: dict,
    events: list,
    delivered: list,
    registry_log: list,
) -> TeamPorts:
    """Ports whose coordinator turns fire scripted tool calls inside deliver().

    The orchestrator registers the team tools right before delivering the
    coordinator's turn body, so ``script[n](tools)`` runs while the registry
    entry is live — the reliable way to drive multi-round flows. Every
    deliver/collect side-effect also records whether the tools were visible,
    pinning the "registered iff a coordinator turn is in flight" contract.
    """
    turns = {"n": 0}
    by_session = {m["session_id"]: m for m in members}

    def _live() -> bool:
        return bool(AgentTeamToolRegistry.get_tools(coordinator["umo"]))

    async def deliver(session_id: str, text: str, context=None) -> str:
        delivered.append((session_id, text, context))
        member = by_session[session_id]
        if member["member_id"] == coordinator["member_id"]:
            turns["n"] += 1
            registry_log.append(("coordinator-deliver", _live()))
            tools = AgentTeamToolRegistry.get_tools(coordinator["umo"])
            action = script.get(turns["n"])
            if action is not None:
                await action(tools)
        else:
            registry_log.append(("member-deliver", _live()))
        return f"mid-{len(delivered)}"

    async def collect(
        session_id: str, message_id: str, member_id: str | None = None
    ) -> tuple[str, list]:
        member = by_session[session_id]
        role = (
            "coordinator"
            if member["member_id"] == coordinator["member_id"]
            else "member"
        )
        registry_log.append((f"{role}-collect", _live()))
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


async def wait_terminal(runner, timeout_s: float = 5.0) -> None:
    for _ in range(int(timeout_s / 0.02)):
        if runner.status in ("completed", "paused", "stopped", "failed"):
            return
        await asyncio.sleep(0.02)


def make_orchestrator(
    run_id: str, members: list[dict], ports, db, bus
) -> AutoOrchestrator:
    """Build a directly-constructed orchestrator (conv-0 is the coordinator)."""
    coordinator = members[0]
    return AutoOrchestrator(
        run_id=run_id,
        team_id="t1",
        team_name="内容团队",
        members=members,
        coordinator=coordinator,
        config=CONFIG,
        run_input="写一首关于秋天的诗",
        ports=ports,
        db=db,
        bus=bus,
        username="alice",
    )


@pytest.mark.asyncio
async def test_auto_run_single_round_finish(tmp_path):
    """One dispatch round + a finish turn completes the run with the summary."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_team_members()
    coordinator = members[0]
    events: list = []
    delivered: list = []
    registry_log: list = []

    async def turn1(tools):
        await next(t for t in tools if t.name == "team_dispatch").call(
            None, assignments=[{"member": "写手", "task": "写一首关于秋天的诗"}]
        )

    async def turn2(tools):
        await next(t for t in tools if t.name == "team_finish").call(
            None, summary="诗已写好"
        )

    ports = auto_ports(
        members,
        coordinator,
        {1: turn1, 2: turn2},
        {"主管": "已派发", "写手": "秋水共长天一色", "审校": "未分配"},
        events,
        delivered,
        registry_log,
    )
    bus = RunEventBus()
    orchestrator = make_orchestrator("ra1", members, ports, db, bus)
    await orchestrator.run()

    assert orchestrator.status == "completed"
    row = await db.get_agent_team_run("ra1")
    assert row.status == "completed"
    assert row.result_summary == "诗已写好"
    # The dispatched round is persisted with the member's result; the finish
    # turn does not append another round entry.
    assert len(row.rounds) == 1
    entry = row.rounds[0]
    assert entry["n"] == 1
    assert entry["assignments"] == [{"member": "写手", "task": "写一首关于秋天的诗"}]
    assert entry["results"] == [{"member": "写手", "result": "秋水共长天一色"}]
    # Registry fully released once the run is over.
    assert AgentTeamToolRegistry.get_tools(coordinator["umo"]) == []
    stopped = [e for e in bus.history() if e.get("type") == "stopped"]
    assert stopped and stopped[-1]["reason"] == "finished"
    # The dispatch event carries notes: None when the coordinator sent none.
    dispatches = [e for e in bus.history() if e.get("type") == "dispatch"]
    assert len(dispatches) == 1
    assert dispatches[0]["notes"] is None


@pytest.mark.asyncio
async def test_auto_run_member_wave(tmp_path):
    """A dispatch to two members records both results and emits tagged events."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_team_members()
    coordinator = members[0]
    events: list = []
    delivered: list = []
    registry_log: list = []

    async def turn1(tools):
        await next(t for t in tools if t.name == "team_dispatch").call(
            None,
            assignments=[
                {"member": "写手", "task": "写初稿"},
                {"member": "审校", "task": "校对初稿"},
            ],
            notes="注意语气",
        )

    async def turn2(tools):
        await next(t for t in tools if t.name == "team_finish").call(
            None, summary="初稿与校对完成"
        )

    ports = auto_ports(
        members,
        coordinator,
        {1: turn1, 2: turn2},
        {"主管": "已派发", "写手": "写手成果", "审校": "审校成果"},
        events,
        delivered,
        registry_log,
    )
    bus = RunEventBus()
    orchestrator = make_orchestrator("ra2", members, ports, db, bus)
    # Pin the per-round AND per-wave persistence: the entry is flushed before
    # the wave (results empty) and again after it (results filled).
    persist_log: list = []
    original_persist = orchestrator._persist

    async def logging_persist():
        await original_persist()
        entry = orchestrator.rounds[-1] if orchestrator.rounds else None
        persist_log.append((len(orchestrator.rounds), bool(entry and entry["results"])))

    orchestrator._persist = logging_persist
    await orchestrator.run()

    assert orchestrator.status == "completed"
    row = await db.get_agent_team_run("ra2")
    entry = row.rounds[0]
    assert entry["n"] == 1
    assert entry["notes"] == "注意语气"
    assert entry["results"] == [
        {"member": "写手", "result": "写手成果"},
        {"member": "审校", "result": "审校成果"},
    ]
    assert persist_log == [(1, False), (1, True)]
    # Member deliveries carry the per-turn team framing with the round number.
    wave_delivery = next(d for d in delivered if d[0] == "conv-1")
    assert wave_delivery[1] == "写初稿"
    assert wave_delivery[2] == "[团队任务] 来自协调者（第 1 轮）"
    # Reply events are tagged with the executing member's member_id.
    replies = [e for e in bus.history() if e.get("direction") == "reply"]
    assert {e["member_id"] for e in replies if e["member_id"] != "mA"} == {"mB", "mC"}
    # Event vocabulary: round and dispatch events for the coordinator turn.
    rounds = [e for e in bus.history() if e.get("type") == "round"]
    assert [e["n"] for e in rounds] == [1, 2]
    assert all(e["max_rounds"] == 5 for e in rounds)
    dispatches = [e for e in bus.history() if e.get("type") == "dispatch"]
    assert len(dispatches) == 1
    assert dispatches[0]["round"] == 1
    assert dispatches[0]["assignments"] == [
        {"member": "写手", "task": "写初稿"},
        {"member": "审校", "task": "校对初稿"},
    ]
    assert dispatches[0]["notes"] == "注意语气"


@pytest.mark.asyncio
async def test_auto_run_member_wave_waits_if_busy(tmp_path):
    """Per-member busy-wait (spec §6.4 每人独立忙等): a wave member that
    reports busy is polled and its turn deferred until it frees up, before
    deliver starts."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_team_members()
    coordinator = members[0]
    events: list = []
    delivered: list = []
    registry_log: list = []

    async def turn1(tools):
        await next(t for t in tools if t.name == "team_dispatch").call(
            None, assignments=[{"member": "写手", "task": "写初稿"}]
        )

    async def turn2(tools):
        await next(t for t in tools if t.name == "team_finish").call(
            None, summary="完成"
        )

    ports = auto_ports(
        members,
        coordinator,
        {1: turn1, 2: turn2},
        {"主管": "已派发", "写手": "写手成果", "审校": "未分配"},
        events,
        delivered,
        registry_log,
    )
    # The writer (conv-1) reports busy exactly once: the wave must poll
    # is_busy, observe the busy state, and wait it out before delivering.
    polls = {"n": 0}

    def is_busy(session_id: str) -> bool:
        if session_id != "conv-1":
            return False
        polls["n"] += 1
        return polls["n"] == 1

    ports.is_busy = is_busy
    ports.busy_poll_interval = 0.01
    bus = RunEventBus()
    orchestrator = make_orchestrator("rabusy", members, ports, db, bus)
    await orchestrator.run()

    assert orchestrator.status == "completed"
    # The busy poll is emitted strictly before the writer's turn is delivered
    # (only the wave's busy-wait can emit a busy event for conv-1).
    history = bus.history()
    busy_idx = next(
        i
        for i, e in enumerate(history)
        if e.get("type") == "busy" and e.get("session_id") == "conv-1"
    )
    sent_idx = next(
        i
        for i, e in enumerate(history)
        if e.get("type") == "message"
        and e.get("direction") == "sent"
        and e.get("session_id") == "conv-1"
    )
    assert busy_idx < sent_idx
    row = await db.get_agent_team_run("rabusy")
    assert row.rounds[0]["results"] == [{"member": "写手", "result": "写手成果"}]


@pytest.mark.asyncio
async def test_auto_run_two_no_tool_rounds_pause(tmp_path):
    """Two consecutive text-only turns pause the run; resume_run restarts it
    with a clean no-tool counter."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    chat = FakeChatService()
    team_svc = AgentTeamService(
        db=db, core_lifecycle=FakeCoreLifecycle(), chat_service=chat
    )
    team = await team_svc.create_team(
        "alice", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    coordinator = next(
        m for m in team["members"] if m["member_id"] == team["coordinator_member_id"]
    )
    events: list = []
    delivered: list = []
    registry_log: list = []
    responses = {m["name"]: f"{m['name']}-回复" for m in team["members"]}

    svc = AgentTeamRunService(db=db, chat_service=chat)
    svc.ports_factory = lambda username, emit: auto_ports(
        team["members"], coordinator, {}, responses, events, delivered, registry_log
    )
    snap = await svc.start_run(
        "alice", team["team_id"], {"mode": "auto", "input": "无人响应的目标"}
    )
    run_id = snap["run_id"]
    runner = svc._runners[run_id]
    await wait_terminal(runner)
    await runner.task

    assert runner.status == "paused"
    assert runner._no_tool_rounds == 2
    assert AgentTeamToolRegistry.get_tools(coordinator["umo"]) == []
    paused = [e for e in svc._buses[run_id].history() if e.get("type") == "paused"]
    assert paused and paused[-1]["reason"] == "no dispatch two rounds in a row"
    row = await db.get_agent_team_run(run_id)
    assert row.status == "paused"
    assert row.rounds == []  # no dispatches, no round entries

    # Post-restart resume (fresh service → runner not in memory): the rebuilt
    # orchestrator carries the persisted rounds and a reset no-tool counter.
    events2: list = []
    delivered2: list = []
    registry_log2: list = []
    responses2 = {m["name"]: f"{m['name']}-成果" for m in team["members"]}

    async def turn1(tools):
        await next(t for t in tools if t.name == "team_dispatch").call(
            None, assignments=[{"member": "写手", "task": "再试一次"}]
        )

    async def turn2(tools):
        await next(t for t in tools if t.name == "team_finish").call(
            None, summary="这次成功了"
        )

    svc2 = AgentTeamRunService(db=db, chat_service=chat)
    svc2.ports_factory = lambda username, emit: auto_ports(
        team["members"],
        coordinator,
        {1: turn1, 2: turn2},
        responses2,
        events2,
        delivered2,
        registry_log2,
    )
    snap2 = await svc2.resume_run("alice", run_id)
    assert snap2["status"] == "running"
    runner2 = svc2._runners[run_id]
    assert runner2._no_tool_rounds == 0  # reset on rebuild
    await wait_terminal(runner2)
    await runner2.task
    assert runner2.status == "completed"
    row2 = await db.get_agent_team_run(run_id)
    assert row2.result_summary == "这次成功了"
    assert len(row2.rounds) == 1
    # The rebuilt turn's prompt carries the stronger one-shot resume reminder
    # (the plain counter-based reminder cannot fire after the reset).
    assert delivered2[0][0] == coordinator["session_id"]
    assert "without dispatching any tasks" in delivered2[0][1]


@pytest.mark.asyncio
async def test_auto_run_registry_scoped_to_turn(tmp_path):
    """Team tools are visible iff a coordinator turn is in flight: live during
    the coordinator's deliver/collect, gone during member waves and after the
    run."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_team_members()
    coordinator = members[0]
    umo = coordinator["umo"]
    events: list = []
    delivered: list = []
    registry_log: list = []

    async def turn1(tools):
        assert AgentTeamToolRegistry.get_tools(umo), "tools must be live mid-turn"
        await next(t for t in tools if t.name == "team_dispatch").call(
            None, assignments=[{"member": "写手", "task": "写"}]
        )

    async def turn2(tools):
        await next(t for t in tools if t.name == "team_finish").call(None, summary="好")

    ports = auto_ports(
        members,
        coordinator,
        {1: turn1, 2: turn2},
        {"主管": "ok", "写手": "写完了", "审校": "未分配"},
        events,
        delivered,
        registry_log,
    )
    orchestrator = make_orchestrator("ra4", members, ports, db, RunEventBus())
    await orchestrator.run()

    assert orchestrator.status == "completed"
    coordinator_logs = [e for e in registry_log if e[0].startswith("coordinator")]
    member_logs = [e for e in registry_log if e[0].startswith("member")]
    # Both coordinator turns saw the tools live for their whole duration.
    assert coordinator_logs and all(live for _, live in coordinator_logs)
    # Every observation after a coordinator turn returned (the member wave)
    # saw the registry empty — the finally-unregister held.
    assert member_logs and not any(live for _, live in member_logs)
    assert AgentTeamToolRegistry.get_tools(umo) == []


@pytest.mark.asyncio
async def test_start_run_auto_mode(tmp_path):
    """mode=auto starts a workflow-less run; a provided workflow_id is rejected."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    chat = FakeChatService()
    team_svc = AgentTeamService(
        db=db, core_lifecycle=FakeCoreLifecycle(), chat_service=chat
    )
    team = await team_svc.create_team(
        "alice", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    coordinator = next(
        m for m in team["members"] if m["member_id"] == team["coordinator_member_id"]
    )
    events: list = []
    delivered: list = []
    registry_log: list = []
    responses = {m["name"]: f"{m['name']}-成果" for m in team["members"]}

    async def turn1(tools):
        await next(t for t in tools if t.name == "team_finish").call(
            None, summary="直接完成"
        )

    svc = AgentTeamRunService(db=db, chat_service=chat)
    svc.ports_factory = lambda username, emit: auto_ports(
        team["members"],
        coordinator,
        {1: turn1},
        responses,
        events,
        delivered,
        registry_log,
    )
    snap = await svc.start_run(
        "alice", team["team_id"], {"mode": "auto", "input": "自动目标"}
    )
    run_id = snap["run_id"]
    assert snap["status"] == "running"
    assert snap["rounds"] == []
    assert snap["progress"]["round"] == 0
    assert snap["progress"]["max_rounds"] == 20
    row = await db.get_agent_team_run(run_id)
    assert row.mode == "auto"
    assert row.workflow_id is None
    assert row.node_states == {}
    assert row.rounds == []

    runner = svc._runners[run_id]
    await wait_terminal(runner)
    await runner.task
    assert runner.status == "completed"
    row = await db.get_agent_team_run(run_id)
    assert row.result_summary == "直接完成"

    with pytest.raises(AgentTeamsServiceError, match="自动编排无需选择工作流"):
        await svc.start_run(
            "alice",
            team["team_id"],
            {"mode": "auto", "input": "x", "workflow_id": "wf-x"},
        )


@pytest.mark.asyncio
async def test_auto_run_coordinator_timeout_pauses(tmp_path):
    """A coordinator turn whose reply never arrives pauses the run with the
    timeout reason and frees the registry."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_team_members()
    coordinator = members[0]
    events: list = []
    delivered: list = []
    gate = asyncio.Event()  # the coordinator's reply never arrives

    async def deliver(session_id: str, text: str, context=None) -> str:
        delivered.append((session_id, text, context))
        return f"mid-{len(delivered)}"

    async def collect(
        session_id: str, message_id: str, member_id: str | None = None
    ) -> tuple[str, list]:
        await gate.wait()
        return "", []

    ports = TeamPorts(
        deliver=deliver,
        collect=collect,
        is_busy=lambda sid: False,
        emit=events.append,
    )
    orchestrator = AutoOrchestrator(
        run_id="rt1",
        team_id="t1",
        team_name="内容团队",
        members=members,
        coordinator=coordinator,
        config={**CONFIG, "reply_timeout": 0.2},
        run_input="目标",
        ports=ports,
        db=db,
        bus=RunEventBus(),
        username="alice",
    )
    await orchestrator.run()

    assert orchestrator.status == "paused"
    row = await db.get_agent_team_run("rt1")
    assert row.status == "paused"
    paused = [e for e in orchestrator.bus.history() if e.get("type") == "paused"]
    assert paused and paused[-1]["reason"] == "coordinator timeout"
    assert AgentTeamToolRegistry.get_tools(coordinator["umo"]) == []


@pytest.mark.asyncio
async def test_auto_run_stop_mid_round(tmp_path):
    """request_stop during a coordinator turn lands the run on stopped and
    unregisters the tools."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_team_members()
    coordinator = members[0]
    events: list = []
    delivered: list = []
    registry_log: list = []

    async def turn1(tools):
        orchestrator.request_stop()

    ports = auto_ports(
        members,
        coordinator,
        {1: turn1},
        {"主管": "ok", "写手": "x", "审校": "x"},
        events,
        delivered,
        registry_log,
    )
    bus = RunEventBus()
    orchestrator = make_orchestrator("rs1", members, ports, db, bus)
    await orchestrator.run()

    assert orchestrator.status == "stopped"
    row = await db.get_agent_team_run("rs1")
    assert row.status == "stopped"
    assert row.rounds == []
    stopped = [e for e in bus.history() if e.get("type") == "stopped"]
    assert stopped and stopped[-1]["reason"] == "user stop"
    assert AgentTeamToolRegistry.get_tools(coordinator["umo"]) == []


@pytest.mark.asyncio
async def test_auto_run_crash_lands_failed(tmp_path):
    """A non-timeout I/O crash in the coordinator turn fails the run; the
    registry is still released (turn finally + crash handler)."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_team_members()
    coordinator = members[0]
    events: list = []

    async def deliver(session_id: str, text: str, context=None) -> str:
        raise RuntimeError("deliver exploded")

    async def collect(
        session_id: str, message_id: str, member_id: str | None = None
    ) -> tuple[str, list]:
        return "", []

    ports = TeamPorts(
        deliver=deliver,
        collect=collect,
        is_busy=lambda sid: False,
        emit=events.append,
    )
    bus = RunEventBus()
    orchestrator = make_orchestrator("rf1", members, ports, db, bus)
    await orchestrator.run()

    assert orchestrator.status == "failed"
    row = await db.get_agent_team_run("rf1")
    assert row.status == "failed"
    stopped = [e for e in bus.history() if e.get("type") == "stopped"]
    assert stopped and "runner error" in stopped[-1]["reason"]
    assert AgentTeamToolRegistry.get_tools(coordinator["umo"]) == []


@pytest.mark.asyncio
async def test_auto_run_max_rounds_pauses(tmp_path):
    """Once every allowed round has dispatched, the run parks on max_rounds."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_team_members()
    coordinator = members[0]
    events: list = []
    delivered: list = []
    registry_log: list = []
    counter = {"n": 0}

    async def dispatch_every_turn(tools):
        counter["n"] += 1
        await next(t for t in tools if t.name == "team_dispatch").call(
            None,
            assignments=[{"member": "写手", "task": f"任务 {counter['n']}"}],
        )

    ports = auto_ports(
        members,
        coordinator,
        {1: dispatch_every_turn, 2: dispatch_every_turn, 3: dispatch_every_turn},
        {"主管": "ok", "写手": "写完了", "审校": "未分配"},
        events,
        delivered,
        registry_log,
    )
    bus = RunEventBus()
    orchestrator = AutoOrchestrator(
        run_id="rmr",
        team_id="t1",
        team_name="内容团队",
        members=members,
        coordinator=coordinator,
        config={**CONFIG, "max_rounds": 2},
        run_input="目标",
        ports=ports,
        db=db,
        bus=bus,
        username="alice",
    )
    await orchestrator.run()

    assert orchestrator.status == "paused"
    row = await db.get_agent_team_run("rmr")
    assert row.status == "paused"
    assert [entry["n"] for entry in row.rounds] == [1, 2]
    assert all(entry["results"] for entry in row.rounds)
    paused = [e for e in bus.history() if e.get("type") == "paused"]
    assert paused and paused[-1]["reason"] == "max_rounds"
    assert paused[-1]["round"] == 3
    assert AgentTeamToolRegistry.get_tools(coordinator["umo"]) == []


@pytest.mark.asyncio
async def test_auto_run_resume_after_pause_recalls_run(tmp_path):
    """run() is re-callable: an in-memory resume continues the same engine."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_team_members()
    coordinator = members[0]
    events: list = []
    delivered: list = []
    registry_log: list = []

    async def turn3(tools):
        await next(t for t in tools if t.name == "team_dispatch").call(
            None, assignments=[{"member": "写手", "task": "继续"}]
        )

    async def turn4(tools):
        await next(t for t in tools if t.name == "team_finish").call(
            None, summary="续上了"
        )

    ports = auto_ports(
        members,
        coordinator,
        {3: turn3, 4: turn4},  # turns 1-2 stay text-only → pause
        {"主管": "嗯", "写手": "写完了", "审校": "未分配"},
        events,
        delivered,
        registry_log,
    )
    orchestrator = make_orchestrator("rr2", members, ports, db, RunEventBus())
    await orchestrator.run()
    assert orchestrator.status == "paused"
    assert orchestrator._no_tool_rounds == 2

    orchestrator.resume()
    orchestrator.task = asyncio.create_task(orchestrator.run())
    await wait_terminal(orchestrator)
    await orchestrator.task
    assert orchestrator.status == "completed"
    row = await db.get_agent_team_run("rr2")
    assert row.status == "completed"
    assert row.result_summary == "续上了"
    assert len(row.rounds) == 1


@pytest.mark.asyncio
async def test_auto_run_terminal_guard_prevents_rerun(tmp_path):
    """run() on an already-terminal orchestrator returns immediately instead
    of live-looping coordinator turns."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_team_members()
    coordinator = members[0]
    events: list = []
    delivered: list = []
    registry_log: list = []
    ports = auto_ports(
        members,
        coordinator,
        {},
        {"主管": "x", "写手": "x", "审校": "x"},
        events,
        delivered,
        registry_log,
    )
    orchestrator = make_orchestrator("rg1", members, ports, db, RunEventBus())
    orchestrator.status = "failed"
    # Without the loop-top terminal guard the round engine re-enters forever
    # with a terminal status (skipped turns never await, so the live loop is
    # synchronous and uninterruptible). Count the turns and force-break so
    # the pre-fix RED is a clean assertion failure instead of a hang.
    calls = {"n": 0}
    original_turn = orchestrator._coordinator_turn

    async def counting_turn(n):
        calls["n"] += 1
        if calls["n"] > 3:
            raise RuntimeError("terminal guard missing: live loop")
        return await original_turn(n)

    orchestrator._coordinator_turn = counting_turn
    await asyncio.wait_for(orchestrator.run(), timeout=5.0)

    assert calls["n"] == 0  # the guard must return before any turn
    assert orchestrator.status == "failed"
    assert delivered == []  # no coordinator turn ever ran
    assert AgentTeamToolRegistry.get_tools(coordinator["umo"]) == []


@pytest.mark.asyncio
async def test_auto_run_finish_truncates_summary(tmp_path):
    """result_summary is capped at 2000 chars, matching DAGRunner."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    members = make_team_members()
    coordinator = members[0]
    events: list = []
    delivered: list = []
    registry_log: list = []

    async def turn1(tools):
        await next(t for t in tools if t.name == "team_finish").call(
            None, summary="长" * 2500
        )

    ports = auto_ports(
        members,
        coordinator,
        {1: turn1},
        {"主管": "ok", "写手": "x", "审校": "x"},
        events,
        delivered,
        registry_log,
    )
    orchestrator = make_orchestrator("rc1", members, ports, db, RunEventBus())
    await orchestrator.run()

    assert orchestrator.status == "completed"
    row = await db.get_agent_team_run("rc1")
    assert row.result_summary == "长" * 2000
