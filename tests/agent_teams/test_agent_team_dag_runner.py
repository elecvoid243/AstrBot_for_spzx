"""DAGRunner end-to-end over scripted ports + persistence checks."""

import asyncio

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import WebChatQueueMgr
from astrbot.dashboard.services.agent_team_ports import (
    TeamPorts,
    build_ports_for_test,
)
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

    async def collect(
        session_id: str, message_id: str, member_id: str | None = None
    ) -> tuple[str, list]:
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
async def test_stream_events_carry_member_id(tmp_path):
    """Regression (spec §6.6): live stream deltas emitted during collect()
    must reach the event bus tagged with the executing member's member_id —
    the dashboard reducer drops message events without one."""
    make_members()
    mgr = WebChatQueueMgr()

    async def fake_listener(data):
        _username, conv_id, payload = data
        mid = payload["message_id"]
        for chunk, t in (("流式", "plain"), ("", "end")):
            await mgr.put_system_event(
                conv_id,
                {
                    "type": t,
                    "message_id": mid,
                    "data": chunk,
                    "streaming": False,
                    "chain_type": "normal",
                },
            )

    mgr.set_listener(fake_listener)
    events: list = []
    bus = RunEventBus()
    ports = build_ports_for_test(mgr, "alice", emit=events.append)
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    runner = DAGRunner(
        run_id="rm",
        team_id="t1",
        graph={"nodes": [{"id": "n1", "member_id": "mA", "task": "t"}], "edges": []},
        config=CONFIG,
        members=list(MEMBER_BY_SESSION.values()),
        ports=ports,
        db=db,
        bus=bus,
        username="alice",
        run_input="x",
    )
    await runner.run()

    assert runner.status == "completed"
    streams = [e for e in events if e.get("direction") == "stream"]
    assert streams, "collect() must emit live stream deltas"
    assert all(e.get("member_id") == "mA" for e in streams)
    assert "".join(e["text"] for e in streams) == "流式"
    # The reply event (emitted by the runner itself) carries the member too.
    replies = [e for e in bus.history() if e.get("direction") == "reply"]
    assert replies and replies[0]["member_id"] == "mA"


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
    # Snapshots carry the graph + workflow id so the dashboard can rebuild
    # the DAG view after a reload (active-run monitor recovery).
    assert snapshot["workflow_id"] == wf["workflow_id"]
    assert snapshot["graph"] == graph
    runner = run_svc._runners[snapshot["run_id"]]
    await wait_terminal(runner)
    snap = await run_svc.get_run_snapshot("alice", snapshot["run_id"])
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


LINEAR_GRAPH = {
    "nodes": [
        {"id": "n1", "member_id": "mA", "task": "a {{input}}"},
        {"id": "n2", "member_id": "mB", "task": "b {{n1}}"},
        {"id": "n3", "member_id": "mC", "task": "c {{n2}}"},
    ],
    "edges": [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}],
}


@pytest.mark.asyncio
async def test_resume_during_pause_persist_does_not_deadlock(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    make_members()
    events: list = []
    delivered: list = []
    ports = scripted_ports(
        {"主管": "一", "写手": "二", "审校": "三"}, events, delivered
    )
    bus = RunEventBus()
    runner = DAGRunner(
        run_id="rr1",
        team_id="t1",
        graph=LINEAR_GRAPH,
        config=CONFIG,
        members=list(MEMBER_BY_SESSION.values()),
        ports=ports,
        db=db,
        bus=bus,
        username="alice",
        run_input="x",
    )
    # Pause from inside the wave so the next loop iteration enters the pause
    # branch while ready nodes are still queued.
    original_deliver = ports.deliver
    paused = {"fired": False}

    async def pausing_deliver(session_id, text, context=None):
        message_id = await original_deliver(session_id, text, context)
        if not paused["fired"]:
            paused["fired"] = True
            runner.pause()
        return message_id

    ports.deliver = pausing_deliver
    # Fire resume() inside the pause branch's persist window: the first
    # paused persist is n1's done-transition, the second is the pause
    # branch's own persist. With the old clear-after-persist order the wake
    # signal was erased there and run() deadlocked.
    original_persist = runner._persist
    paused_persists = {"n": 0}
    resumed = {"fired": False}

    async def resuming_persist():
        if runner.status == "paused":
            paused_persists["n"] += 1
            if paused_persists["n"] == 2 and not resumed["fired"]:
                resumed["fired"] = True
                runner.resume()
        await original_persist()

    runner._persist = resuming_persist

    await asyncio.wait_for(runner.run(), timeout=5.0)
    assert resumed["fired"]  # resume raced the pause-branch persist
    assert runner.status == "completed"
    assert all(s["status"] == "done" for s in runner.node_states.values())


@pytest.mark.asyncio
async def test_bare_resume_on_failure_pause_lands_paused(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    make_members()
    events: list = []
    delivered: list = []
    responses = {"主管": RuntimeError("boom"), "写手": "x", "审校": "y"}
    ports = scripted_ports(responses, events, delivered)
    runner = DAGRunner(
        run_id="rz",
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
    assert runner.status == "paused"

    # Bare resume + re-run: n1 failed so nothing is runnable; the run must
    # re-park on paused instead of persisting a zombie "running" row.
    runner.resume()
    assert runner.status == "running"
    runner.task = asyncio.create_task(runner.run())
    await wait_terminal(runner)
    assert runner.status == "paused"
    row = await db.get_agent_team_run("rz")
    assert row.status == "paused"


@pytest.mark.asyncio
async def test_terminal_run_closes_ports_paused_keeps_them(tmp_path):
    """Ports cleanup: terminal runs release resources, paused runs keep them
    (run() is re-callable after retry/skip and still needs the ports)."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    make_members()
    events: list = []
    delivered: list = []
    closed = {"n": 0}
    responses = {"主管": "ok", "写手": RuntimeError("session gone"), "审校": "x"}
    ports = scripted_ports(responses, events, delivered)

    async def close() -> None:
        closed["n"] += 1

    ports.close = close
    runner = DAGRunner(
        run_id="rc1",
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
    assert runner.status == "paused"
    assert closed["n"] == 0  # paused: subscriptions must survive

    # n2 (写手) is the failed node; after retry the run goes terminal
    responses["写手"] = "修好了"
    await runner.retry_node("n2")
    runner.task = asyncio.create_task(runner.run())
    for _ in range(250):
        if runner.status == "completed":
            break
        await asyncio.sleep(0.02)
    assert runner.status == "completed"
    if runner.task is not None:
        # status flips before run()'s finally cleanup runs; await the task
        # so the close is guaranteed to have happened.
        await runner.task
    assert closed["n"] == 1  # terminal: released exactly once


@pytest.mark.asyncio
async def test_deliver_failure_fails_node_without_escaping(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    make_members()
    events: list = []
    delivered: list = []
    ports = scripted_ports(
        {"主管": "ok", "写手": "fine", "审校": "z"}, events, delivered
    )
    bus = RunEventBus()
    runner = DAGRunner(
        run_id="rd",
        team_id="t1",
        graph=GRAPH,  # n1 + n2 roots in one wave, n3 joins them
        config=CONFIG,
        members=list(MEMBER_BY_SESSION.values()),
        ports=ports,
        db=db,
        bus=bus,
        username="alice",
        run_input="主题",
    )
    # n2's member session is conv-1; make its delivery blow up.
    original_deliver = ports.deliver

    async def failing_deliver(session_id, text, context=None):
        if session_id == "conv-1":
            raise RuntimeError("deliver exploded")
        return await original_deliver(session_id, text, context)

    ports.deliver = failing_deliver

    # The exception must stay contained: run() settles instead of raising.
    await asyncio.wait_for(runner.run(), timeout=5.0)
    # failure_policy=pause parked the run on the failed node
    assert runner.status == "paused"
    assert runner.node_states["n2"]["status"] == "failed"
    assert "deliver exploded" in runner.node_states["n2"]["error"]
    # the sibling wave coroutine settled before the terminal transition
    assert runner.node_states["n1"]["status"] == "done"
    assert runner.node_states["n3"]["status"] == "pending"
    assert all(d[0] != "conv-2" for d in delivered)  # n3 never executed
    # "sent" is only emitted after a successful deliver
    sent_sessions = [
        e.get("session_id") for e in bus.history() if e.get("direction") == "sent"
    ]
    assert sent_sessions == ["conv-0"]


@pytest.mark.asyncio
async def test_run_service_enforces_ownership(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    chat = FakeChatService()
    team_svc = AgentTeamService(
        db=db, core_lifecycle=FakeCoreLifecycle(), chat_service=chat
    )
    team = await team_svc.create_team(
        "alice", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    MEMBER_BY_SESSION.clear()
    for m in team["members"]:
        MEMBER_BY_SESSION[m["session_id"]] = m
    ids = [m["member_id"] for m in team["members"]]
    graph = {"nodes": [{"id": "n1", "member_id": ids[0], "task": "做 {{input}}"}]}
    wf = await team_svc.create_workflow(
        "alice", team["team_id"], {"name": "w", "graph": graph}
    )
    run_svc = AgentTeamRunService(db=db, chat_service=chat)
    responses = {m["name"]: f"{m['name']}-done" for m in team["members"]}
    run_svc.ports_factory = lambda username, emit: scripted_ports(responses, [], [])
    snapshot = await run_svc.start_run(
        "alice",
        team["team_id"],
        {"mode": "dag", "input": "主题", "workflow_id": wf["workflow_id"]},
    )
    run_id = snapshot["run_id"]
    runner = run_svc._runners[run_id]
    await wait_terminal(runner)
    assert runner.status == "completed"

    for call in (
        run_svc.get_run_snapshot("bob", run_id),
        run_svc.pause_run("bob", run_id),
        run_svc.request_stop_run("bob", run_id),
        run_svc.retry_node("bob", run_id, "n1"),
        run_svc.skip_node("bob", run_id, "n1"),
        run_svc.get_event_bus("bob", run_id),
    ):
        with pytest.raises(AgentTeamsServiceError, match="不存在"):
            await call

    # the owner gets through on the same calls
    assert (await run_svc.pause_run("alice", run_id))["message"] == "已暂停"
    snap = await run_svc.get_run_snapshot("alice", run_id)
    assert snap["run_id"] == run_id
    bus = await run_svc.get_event_bus("alice", run_id)
    assert bus is run_svc._buses[run_id]
