"""Public run-service coverage for the shared main-pipeline execution entry."""

import asyncio
import contextlib
import json

import pytest

from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.event_bus import EventBus
from astrbot.core.platform.sources.webchat import webchat_adapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import WebChatQueueMgr
from astrbot.dashboard.services.agent_team_ports import build_ports_for_test
from astrbot.dashboard.services.agent_team_run_service import AgentTeamRunService
from astrbot.dashboard.services.agent_team_service import AgentTeamService
from tests.agent_teams.test_agent_team_service import FakeChatService, FakeCoreLifecycle


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["dag", "auto", "dag_queued_cancel", "auto_queued_cancel"]
)
async def test_run_entries_use_member_profile_and_preserve_initiator(
    tmp_path, monkeypatch, mode
):
    """Both orchestration sources execute through the selected pipeline profile.

    Args:
        tmp_path: Isolated database directory.
        monkeypatch: Redirects the adapter's history storage to the test database.
        mode: Existing public run entry to exercise.
    """
    db = SQLiteDatabase(str(tmp_path / "execution.db"))
    await db.initialize()
    monkeypatch.setattr(webchat_adapter, "db_helper", db)
    chat = FakeChatService()
    lifecycle = FakeCoreLifecycle(confs={"research": {}})
    teams = AgentTeamService(db=db, core_lifecycle=lifecycle, chat_service=chat)
    team = await teams.create_team(
        "alice",
        {
            "name": "Research",
            "coordinator": "Lead",
            "members": [
                {"name": "Lead", "system_prompt": "Coordinate research."},
                {"name": "Writer", "system_prompt": "Write with evidence."},
            ],
        },
    )
    for member in team["members"]:
        team = await teams.update_member(
            "alice",
            team["team_id"],
            member["member_id"],
            {"runner_config": {"config_id": "research", "max_steps": 7}},
        )
    workflow = await teams.create_workflow(
        "alice",
        team["team_id"],
        {
            "name": "Report",
            "graph": {
                "nodes": [
                    {
                        "id": "write",
                        "member_id": team["members"][1]["member_id"],
                        "task": "Write a report.",
                    }
                ],
                "edges": [],
            },
        },
    )
    queue_manager = WebChatQueueMgr()
    adapter = webchat_adapter.WebChatAdapter(
        {"id": "webchat", "type": "webchat", "enable": True}, {}, asyncio.Queue()
    )
    coordinator_turns = 0
    queued = asyncio.Event()
    executed = []

    class ScriptedScheduler:
        """Injectable pipeline executor returning its observable execution profile."""

        def __init__(self, profile):
            self.profile = profile

        async def execute(self, event):
            nonlocal coordinator_turns
            executed.append(event.message_str)
            username, cid, payload = event.message_obj.raw_message
            if mode == "auto" and cid == team["members"][0]["session_id"]:
                coordinator_turns += 1
                tools = AgentTeamToolRegistry.get_tools(team["members"][0]["umo"])
                if coordinator_turns == 1:
                    await next(t for t in tools if t.name == "team_dispatch").call(
                        None,
                        assignments=[{"member": "Writer", "task": "Write a report."}],
                    )
                else:
                    await next(t for t in tools if t.name == "team_finish").call(
                        None, summary="Report completed."
                    )
            binding = event.get_extra("agent_team_execution")
            result = json.dumps(
                {
                    "profile": self.profile,
                    "sender": event.get_sender_id(),
                    "role": event.role,
                    "max_steps": getattr(binding, "max_steps", None),
                    "turn_id": getattr(binding, "turn_id", None),
                    "source": getattr(binding, "source_umo", None),
                    "origin": event.unified_msg_origin,
                }
            )
            for kind, text in (("plain", result), ("end", "")):
                await queue_manager.put_system_event(
                    cid,
                    {
                        "type": kind,
                        "message_id": payload["message_id"],
                        "data": text,
                        "streaming": False,
                    },
                )

    class ConfigManager:
        def get_conf_info(self, umo):
            return {"id": "default"}

    event_bus = EventBus(
        asyncio.Queue(),
        {
            "default": ScriptedScheduler("default"),
            "research": ScriptedScheduler("research"),
        },
        ConfigManager(),
    )

    async def listener(data):
        await event_bus.event_queue.put(
            adapter.create_event(await adapter.convert_message(data))
        )
        queued.set()

    queue_manager.set_listener(listener)
    runs = AgentTeamRunService(
        db, chat, config_checker=lambda config_id: config_id == "research"
    )
    runs.ports_factory = lambda username, emit: build_ports_for_test(
        queue_manager, username, emit
    )
    dispatch_task = (
        None
        if mode.endswith("_queued_cancel")
        else asyncio.create_task(event_bus.dispatch())
    )
    run_id = None
    try:
        snapshot = await runs.start_run(
            "alice",
            team["team_id"],
            {
                "mode": mode.removesuffix("_queued_cancel"),
                "input": "Research the topic.",
                **(
                    {"workflow_id": workflow["workflow_id"]}
                    if mode.startswith("dag")
                    else {}
                ),
            },
        )
        run_id = snapshot["run_id"]
        if mode.endswith("_queued_cancel"):
            await asyncio.wait_for(queued.wait(), 2)
            await runs.request_stop_run("alice", run_id)
            deadline = asyncio.get_running_loop().time() + 2
            while (await runs.get_run_snapshot("alice", run_id))["status"] == "running":
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.01)
            dispatch_task = asyncio.create_task(event_bus.dispatch())
            ports = build_ports_for_test(queue_manager, "alice", lambda event: None)
            mid = await ports.deliver("ordinary", "Ordinary marker.")
            await asyncio.wait_for(ports.collect("ordinary", mid), 2)
            await ports.close()
            assert executed == ["Ordinary marker."]
            assert (await runs.get_run_snapshot("alice", run_id))["status"] == "stopped"
            return
        deadline = asyncio.get_running_loop().time() + 5
        while (await runs.get_run_snapshot("alice", run_id))["status"] == "running":
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)
        assert (await runs.get_run_snapshot("alice", run_id))["status"] == "completed"
        for member in team["members"] if mode == "auto" else team["members"][1:]:
            transcript = await runs.get_transcript("alice", run_id, member["member_id"])
            replies = [
                row for row in transcript["messages"] if row["direction"] == "reply"
            ]
            assert replies
            for reply in replies:
                assert json.loads(reply["text"]) == {
                    "profile": "research",
                    "sender": "alice",
                    "role": "member",
                    "max_steps": 7,
                    "turn_id": reply["turn_id"],
                    "source": f"webchat:FriendMessage:webchat!alice!{member['session_id']}",
                    "origin": member["umo"],
                }
                events = (await runs.get_event_bus("alice", run_id)).history()
                assert any(
                    event.get("direction") == "stream"
                    and event.get("turn_id") == reply["turn_id"]
                    and event.get("member_id") == member["member_id"]
                    and event.get("run_id") == run_id
                    for event in events
                )
    finally:
        if run_id is not None:
            await runs.request_stop_run("alice", run_id)
        if dispatch_task is not None:
            dispatch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await dispatch_task
        for member in team["members"]:
            queue_manager.remove_queues(member["session_id"])
        queue_manager.remove_queues("ordinary")
        await db.engine.dispose()
