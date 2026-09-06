"""Transcript tests: repo rows against SQLite + runner-level sink wiring."""

import logging

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import WebChatQueueMgr
from astrbot.dashboard.services.agent_team_ports import build_ports_for_test
from astrbot.dashboard.services.agent_team_run_service import (
    AgentTeamRunService,
    AutoOrchestrator,
    DAGRunner,
    RunEventBus,
)
from astrbot.dashboard.services.agent_team_service import AgentTeamService
from tests.agent_teams.test_agent_team_auto import auto_ports, make_team_members
from tests.agent_teams.test_agent_team_dag_runner import (
    CONFIG as DAG_CONFIG,
)
from tests.agent_teams.test_agent_team_dag_runner import (
    MEMBER_BY_SESSION,
    make_members,
    scripted_ports,
    wait_terminal,
)
from tests.agent_teams.test_agent_team_service import (
    MEMBERS,
    FakeChatService,
    FakeCoreLifecycle,
)


@pytest.mark.asyncio
async def test_transcript_roundtrip_order_before_id_and_limit(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()

    for i in range(5):
        await db.append_agent_team_run_message(
            run_id="r1",
            member_id="m1",
            node_id="n1" if i % 2 else None,
            round=1 if i % 2 else None,
            turn_id=f"turn-{i}",
            direction="sent" if i == 0 else "reply",
            text=None if i == 0 else f"回复 {i}",
            parts=[{"type": "interactive_choice", "options": [1, 2]}]
            if i == 3
            else None,
            metadata={"reason": "test"} if i == 4 else None,
        )

    rows = await db.get_agent_team_run_transcript("r1", "m1", before_id=None)
    assert [r.id for r in rows] == [5, 4, 3, 2, 1]  # newest first (id DESC)
    assert rows[-1].direction == "sent"
    # None-able fields and JSON columns roundtrip
    assert rows[0].meta == {"reason": "test"}
    assert rows[1].parts == [{"type": "interactive_choice", "options": [1, 2]}]
    assert rows[1].node_id == "n1" and rows[1].round == 1
    assert rows[-1].node_id is None and rows[-1].round is None

    # before_id is exclusive; ordering stays id DESC
    page = await db.get_agent_team_run_transcript("r1", "m1", before_id=3)
    assert [r.id for r in page] == [2, 1]

    page = await db.get_agent_team_run_transcript("r1", "m1", before_id=None, limit=2)
    assert [r.id for r in page] == [5, 4]

    # Transcripts are scoped per (run_id, member_id)
    await db.append_agent_team_run_message(
        run_id="r1",
        member_id="m2",
        node_id=None,
        round=None,
        turn_id="turn-0",
        direction="sent",
        text="other member",
        parts=None,
        metadata=None,
    )
    assert await db.get_agent_team_run_transcript("r1", "m2", before_id=None) != []
    rows = await db.get_agent_team_run_transcript("r1", "m1", before_id=None)
    assert [r.id for r in rows] == [5, 4, 3, 2, 1]
    assert all(r.member_id == "m1" for r in rows)


@pytest.mark.asyncio
async def test_trim_keeps_newest_rows_per_member(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    for i in range(7):
        await db.append_agent_team_run_message(
            run_id="r1",
            member_id="m1",
            node_id=None,
            round=None,
            turn_id=f"turn-{i}",
            direction="reply",
            text=f"t{i}",
            parts=None,
            metadata=None,
        )
    # Another member's transcript must be untouched by m1's trim.
    await db.append_agent_team_run_message(
        run_id="r1",
        member_id="m2",
        node_id=None,
        round=None,
        turn_id="turn-0",
        direction="sent",
        text="keep me",
        parts=None,
        metadata=None,
    )

    deleted = await db.trim_agent_team_run_transcript("r1", "m1", keep=5)
    assert deleted == 2

    rows = await db.get_agent_team_run_transcript("r1", "m1", before_id=None)
    assert [r.id for r in rows] == [7, 6, 5, 4, 3]  # newest 5 kept
    # Trimming again below the current size deletes nothing.
    assert await db.trim_agent_team_run_transcript("r1", "m1", keep=500) == 0
    assert len(await db.get_agent_team_run_transcript("r1", "m2", before_id=None)) == 1


# ---------- runner-level transcript sink + event enrichment ----------


@pytest.fixture(autouse=True)
def _clean_tool_registry():
    """The tool registry is process-global; keep tests independent."""
    yield
    from astrbot.core import agent_team_tools

    agent_team_tools._TOOLS.clear()


def recording_sink(rows: list):
    """A scripted transcript sink appending every row to `rows`."""

    async def sink(row: dict) -> None:
        rows.append(row)

    return sink


CHAIN_GRAPH = {
    "nodes": [
        {"id": "n1", "member_id": "mA", "task": "做 {{input}}"},
        {"id": "n2", "member_id": "mB", "task": "查 {{n1}}"},
    ],
    "edges": [{"from": "n1", "to": "n2"}],
}


@pytest.mark.asyncio
async def test_dag_runner_transcript_rows_and_enriched_events(tmp_path):
    """A 2-node chain sinks sent/reply rows per node and enriches the sent /
    reply message events with turn_id + run_id (reply adds parts)."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    make_members()
    events: list = []
    delivered: list = []
    ports = scripted_ports(
        {"主管": "调研结果", "写手": "校对结果", "审校": "x"}, events, delivered
    )
    rows: list = []
    bus = RunEventBus()
    runner = DAGRunner(
        run_id="rt-chain",
        team_id="t1",
        graph=CHAIN_GRAPH,
        config=DAG_CONFIG,
        members=list(MEMBER_BY_SESSION.values()),
        ports=ports,
        db=db,
        bus=bus,
        username="alice",
        run_input="主题",
        transcript_sink=recording_sink(rows),
    )
    await runner.run()
    assert runner.status == "completed"

    assert [r["direction"] for r in rows] == ["sent", "reply", "sent", "reply"]
    assert [r["node_id"] for r in rows] == ["n1", "n1", "n2", "n2"]
    assert [r["member_id"] for r in rows] == ["mA", "mA", "mB", "mB"]
    assert all(r["run_id"] == "rt-chain" for r in rows)
    assert all(r["round"] is None and r["metadata"] is None for r in rows)
    assert rows[0]["text"] == "做 主题"
    assert rows[1]["text"] == "调研结果"
    assert rows[1]["parts"] == [{"type": "plain", "data": "调研结果"}]
    assert rows[2]["text"] == "查 调研结果"
    assert rows[3]["text"] == "校对结果"
    # One turn_id per node execution, shared by its sent/reply rows.
    assert rows[0]["turn_id"] == rows[1]["turn_id"]
    assert rows[2]["turn_id"] == rows[3]["turn_id"]
    assert rows[0]["turn_id"] != rows[2]["turn_id"]
    assert all(len(r["turn_id"]) == 12 for r in rows)

    history = bus.history()
    sents = [e for e in history if e.get("direction") == "sent"]
    replies = [e for e in history if e.get("direction") == "reply"]
    assert [e["text"] for e in sents] == ["做 主题", "查 调研结果"]
    for e in sents:
        assert e["run_id"] == "rt-chain" and e["turn_id"]
    assert [e["text"] for e in replies] == ["调研结果", "校对结果"]
    assert replies[0]["parts"] == [{"type": "plain", "data": "调研结果"}]
    assert replies[1]["parts"] == [{"type": "plain", "data": "校对结果"}]
    # Event and row agree on the turn each belongs to.
    assert [e["turn_id"] for e in sents] == [rows[0]["turn_id"], rows[2]["turn_id"]]
    assert [e["turn_id"] for e in replies] == [rows[1]["turn_id"], rows[3]["turn_id"]]


@pytest.mark.asyncio
async def test_dag_runner_stream_events_carry_turn_id(tmp_path):
    """Live stream deltas emitted during collect() carry the in-flight
    turn's turn_id and the run_id."""
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
    rows: list = []
    bus = RunEventBus()
    # Ports capture bus.emit as their emit — the same wiring the service
    # path uses, so the runner's bus-level stream enrichment applies.
    ports = build_ports_for_test(mgr, "alice", emit=bus.emit)
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    runner = DAGRunner(
        run_id="rt-stream",
        team_id="t1",
        graph={"nodes": [{"id": "n1", "member_id": "mA", "task": "t"}], "edges": []},
        config=DAG_CONFIG,
        members=list(MEMBER_BY_SESSION.values()),
        ports=ports,
        db=db,
        bus=bus,
        username="alice",
        run_input="x",
        transcript_sink=recording_sink(rows),
    )
    await runner.run()
    assert runner.status == "completed"

    streams = [e for e in bus.history() if e.get("direction") == "stream"]
    assert streams, "collect() must emit live stream deltas"
    assert all(e["run_id"] == "rt-stream" for e in streams)
    assert all(e["turn_id"] == rows[0]["turn_id"] for e in streams)
    assert "".join(e["text"] for e in streams) == "流式"


@pytest.mark.asyncio
async def test_dag_runner_failure_writes_system_row(tmp_path):
    """A collect failure sinks a system row carrying the error, with no
    reply row for the failed turn."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    make_members()
    events: list = []
    delivered: list = []
    responses = {"主管": RuntimeError("collect exploded"), "写手": "x", "审校": "y"}
    ports = scripted_ports(responses, events, delivered)
    rows: list = []
    runner = DAGRunner(
        run_id="rt-fail",
        team_id="t1",
        graph={
            "nodes": [{"id": "n1", "member_id": "mA", "task": "做 {{input}}"}],
            "edges": [],
        },
        config=DAG_CONFIG,
        members=list(MEMBER_BY_SESSION.values()),
        ports=ports,
        db=db,
        bus=RunEventBus(),
        username="alice",
        run_input="主题",
        transcript_sink=recording_sink(rows),
    )
    await runner.run()
    await wait_terminal(runner)
    assert runner.status == "paused"

    assert [r["direction"] for r in rows] == ["sent", "system"]
    system = rows[1]
    assert system["text"] is None
    assert system["metadata"] == {"error": "collect exploded"}
    assert system["node_id"] == "n1" and system["member_id"] == "mA"
    assert system["run_id"] == "rt-fail"
    assert system["turn_id"] == rows[0]["turn_id"]


@pytest.mark.asyncio
async def test_transcript_sink_failure_does_not_fail_run(tmp_path, caplog):
    """A raising sink is isolated: the run completes and a warning is logged."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    make_members()
    events: list = []
    delivered: list = []
    ports = scripted_ports(
        {"主管": "调研结果", "写手": "x", "审校": "y"}, events, delivered
    )

    async def bad_sink(row: dict) -> None:
        raise RuntimeError("sink exploded")

    runner = DAGRunner(
        run_id="rt-sinkfail",
        team_id="t1",
        graph={
            "nodes": [{"id": "n1", "member_id": "mA", "task": "做 {{input}}"}],
            "edges": [],
        },
        config=DAG_CONFIG,
        members=list(MEMBER_BY_SESSION.values()),
        ports=ports,
        db=db,
        bus=RunEventBus(),
        username="alice",
        run_input="主题",
        transcript_sink=bad_sink,
    )
    with caplog.at_level(logging.WARNING, logger="astrbot"):
        await runner.run()
    assert runner.status == "completed"
    warnings = [
        rec.getMessage() for rec in caplog.records if rec.levelno >= logging.WARNING
    ]
    assert any("transcript sink failed" in msg for msg in warnings)


@pytest.mark.asyncio
async def test_auto_orchestrator_transcript_rows_carry_round(tmp_path):
    """Auto mode: the coordinator turn, the member wave turn, and the finish
    turn each sink sent/reply rows with the round number filled."""
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
    rows: list = []
    bus = RunEventBus()
    orchestrator = AutoOrchestrator(
        run_id="rt-auto",
        team_id="t1",
        team_name="内容团队",
        members=members,
        coordinator=coordinator,
        config={
            "failure_policy": "pause",
            "reply_timeout": 5.0,
            "max_rounds": 5,
            "max_parallel": 5,
            "inject_max_length": 4000,
        },
        run_input="写一首关于秋天的诗",
        ports=ports,
        db=db,
        bus=bus,
        username="alice",
        transcript_sink=recording_sink(rows),
    )
    await orchestrator.run()
    assert orchestrator.status == "completed"

    # Round 1: coordinator sent/reply + the dispatched member's sent/reply.
    round1 = [r for r in rows if r["round"] == 1]
    assert [r["direction"] for r in round1] == ["sent", "reply", "sent", "reply"]
    assert [r["member_id"] for r in round1] == ["mA", "mA", "mB", "mB"]
    assert all(r["run_id"] == "rt-auto" for r in round1)
    assert all(r["node_id"] is None for r in round1)
    assert "团队目标" in round1[0]["text"]
    assert round1[1]["text"] == "已派发"
    assert round1[1]["parts"] == [{"type": "plain", "data": "已派发"}]
    assert round1[2]["text"] == "写一首关于秋天的诗"
    assert round1[3]["text"] == "秋水共长天一色"
    assert round1[3]["parts"] == [{"type": "plain", "data": "秋水共长天一色"}]
    # Distinct turn ids: coordinator turn vs member wave turn.
    assert round1[0]["turn_id"] == round1[1]["turn_id"]
    assert round1[2]["turn_id"] == round1[3]["turn_id"]
    assert round1[0]["turn_id"] != round1[2]["turn_id"]
    # The finish turn is its own turn in round 2.
    finish_turns = {
        r["turn_id"] for r in rows if r["round"] == 2 and r["member_id"] == "mA"
    }
    assert len(finish_turns) == 1
    assert {r["direction"] for r in rows if r["round"] == 2} == {"sent", "reply"}

    # Event enrichment mirrors the DAG runner (runner message events flow
    # through the run bus).
    history = bus.history()
    replies = [e for e in history if e.get("direction") == "reply"]
    assert replies
    for e in replies:
        assert e["run_id"] == "rt-auto" and e["turn_id"]
        assert e["parts"]
    sents = [e for e in history if e.get("direction") == "sent"]
    assert all(e["run_id"] == "rt-auto" and e["turn_id"] for e in sents)


@pytest.mark.asyncio
async def test_service_wires_production_transcript_sink(tmp_path):
    """The service-built production sink persists rows through the repo: the
    runner-built row dicts match append_agent_team_run_message's kwargs."""
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
    graph = {
        "nodes": [
            {
                "id": "n1",
                "member_id": team["members"][0]["member_id"],
                "task": "做 {{input}}",
            }
        ]
    }
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
    runner = run_svc._runners[snapshot["run_id"]]
    await wait_terminal(runner)
    assert runner.status == "completed"

    member_id = team["members"][0]["member_id"]
    rows = await db.get_agent_team_run_transcript(
        snapshot["run_id"], member_id, before_id=None
    )
    assert [r.direction for r in rows] == ["reply", "sent"]  # newest first
    assert rows[-1].text == "做 主题"
    assert rows[0].text == "主管-done"
    assert rows[0].parts == [{"type": "plain", "data": "主管-done"}]
    assert rows[0].node_id == "n1"
    assert rows[0].turn_id == rows[-1].turn_id
    assert all(r.turn_id for r in rows)
