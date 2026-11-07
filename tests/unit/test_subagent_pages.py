"""Exercise independent subagent persistence, subscriptions and lifecycle identity."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from astrbot.core.agent.response import AgentResponse
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.db.po import PlatformMessageHistory
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import WebChatQueueMgr
from astrbot.core.subagent_event_sink import SubAgentEventSink
from astrbot.core.subagent_manager import (
    SubAgentConfig,
    SubAgentManager,
    SubAgentSession,
    SubAgentStatus,
)
from astrbot.core.subagent_progress import SubagentProgress

UMO = "webchat:FriendMessage:webchat!owner!parent"


@pytest_asyncio.fixture
async def progress(tmp_path, monkeypatch):
    database = SQLiteDatabase(str(tmp_path / "progress.db"))
    await database.initialize()
    database.inited = True
    registry = SubagentProgress(database)
    monkeypatch.setattr("astrbot.core.subagent_progress.subagent_progress", registry)
    config = SubAgentConfig(name="worker")
    agent_session = SubAgentSession(session_id=UMO)
    agent_session.subagents["worker"] = config
    agent_session.subagent_status["worker"] = "RUNNING"
    monkeypatch.setattr(SubAgentManager, "_sessions", {UMO: agent_session})
    yield registry, config
    if registry.tasks:
        await asyncio.gather(*list(registry.tasks), return_exceptions=True)
    await database.engine.dispose()


@pytest.mark.asyncio
async def test_configuration_snapshot_survives_completion_and_cleanup(
    progress, monkeypatch
):
    registry, config = progress
    queues = WebChatQueueMgr()
    queues.get_or_create_back_queue("request", "parent")
    monkeypatch.setattr("astrbot.core.subagent_event_sink.webchat_queue_mgr", queues)
    configuration = {
        "system_prompt": "Original instructions",
        "tools": ["read_file"],
        "skills": ["grilling"],
        "provider_id": "provider-original",
    }
    sink = SubAgentEventSink(
        "request",
        "worker",
        umo=UMO,
        instance_id=config.instance_id,
        configuration=configuration,
    )
    await sink.start()
    card = queues.back_queues["request"].get_nowait()
    assert "configuration" not in card["data"]["payload"]
    configuration["system_prompt"] = "Changed after start"
    first = await registry.get_configuration("parent", sink.subagent_run_id)
    assert first["system_prompt"] == "Original instructions"
    first["tools"].append("modified-copy")
    assert (await registry.get_configuration("parent", sink.subagent_run_id))[
        "tools"
    ] == ["read_file"]
    await sink.complete("done", 1)
    registry.schedule_cleaned(UMO, [config.instance_id])
    await asyncio.gather(*list(registry.tasks))
    SubAgentManager.get_session(UMO).subagents["worker"] = SubAgentConfig(
        name="worker", system_prompt="Replacement"
    )
    stored = await registry.get_configuration("parent", sink.subagent_run_id)
    assert stored["system_prompt"] == "Original instructions"
    assert stored["provider_id"] == "provider-original"
    assert await registry.get_configuration("other-owner", sink.subagent_run_id) is None
    assert all(
        "configuration" not in entry for entry in await registry.list_runs("parent")
    )


@pytest.mark.asyncio
async def test_background_trace_survives_parent_queue_completion(progress, monkeypatch):
    registry, config = progress
    queues = WebChatQueueMgr()
    queues.get_or_create_back_queue("parent-request", "parent")
    monkeypatch.setattr("astrbot.core.subagent_event_sink.webchat_queue_mgr", queues)
    sink = SubAgentEventSink(
        "parent-request",
        "worker",
        "task",
        umo=UMO,
        instance_id=config.instance_id,
        background=True,
    )
    await sink.start()
    card = queues.back_queues["parent-request"].get_nowait()
    assert card["data"]["detached"] and card["data"]["background"]
    stream = registry.stream("parent", sink.subagent_run_id)
    snapshot = await anext(stream)
    assert snapshot["data"]["status"] == "running"
    queues.remove_back_queue("parent-request")
    await sink(
        AgentResponse(
            type="streaming_delta",
            data={"chain": MessageChain().message("still working")},
        )
    )
    delta = await anext(stream)
    assert delta["type"] == "event"
    assert delta["data"]["payload"]["text"] == "still working"
    await sink.complete("finished after the parent", 2)
    completed = await registry.get_run("parent", sink.subagent_run_id)
    assert completed["status"] == "completed"
    assert completed["text"] == "finished after the parent"
    assert completed["background"] is True
    assert await registry.get_run("other-owner", sink.subagent_run_id) is None
    assert not registry.active
    await stream.aclose()
    assert not registry.subscribers


@pytest.mark.asyncio
async def test_late_delta_cannot_reopen_completion_waiting_on_parent_backpressure(
    progress, monkeypatch
):
    registry, config = progress
    blocked, release = asyncio.Event(), asyncio.Event()

    async def put_back(_request_id, payload):
        if payload["data"]["kind"] == "completed":
            blocked.set()
            await release.wait()
        return True

    monkeypatch.setattr(
        "astrbot.core.subagent_event_sink.webchat_queue_mgr",
        SimpleNamespace(put_back_queue=put_back),
    )
    sink = SubAgentEventSink(
        "parent-request", "worker", umo=UMO, instance_id=config.instance_id
    )
    await sink.start()
    completing = asyncio.create_task(sink.complete("final", 1))
    await asyncio.wait_for(blocked.wait(), timeout=5)
    await sink(
        AgentResponse(
            type="streaming_delta", data={"chain": MessageChain().message("late delta")}
        )
    )
    release.set()
    await completing
    saved = await registry.get_run("parent", sink.subagent_run_id)
    assert saved["status"] == "completed" and saved["text"] == "final"
    assert sink.subagent_run_id not in registry.active


@pytest.mark.asyncio
async def test_metadata_has_no_trace_and_same_name_lifetimes_are_distinct(progress):
    registry, config = progress
    data = {
        "subagent_run_id": "old",
        "agent_name": "worker",
        "kind": "started",
        "payload": {"input_full": "full task"},
        "ts": 1,
    }
    await registry.record(UMO, config.instance_id, False, data)
    await registry.record(
        UMO,
        config.instance_id,
        False,
        {**data, "kind": "completed", "payload": {"result_text": "old result"}},
    )
    SubAgentManager.set_subagent_status(UMO, "worker", SubAgentStatus.COMPLETED)
    SubAgentManager.remove_subagent(UMO, "worker")
    replacement = SubAgentConfig(name="worker")
    SubAgentManager.get_session(UMO).subagents["worker"] = replacement
    await registry.record(
        UMO, replacement.instance_id, True, {**data, "subagent_run_id": "new", "ts": 2}
    )
    await asyncio.gather(*list(registry.tasks))
    entries = await registry.list_runs("parent")
    assert [item["subagent_run_id"] for item in entries] == ["new", "old"]
    assert entries[0]["cleaned"] is False
    assert entries[1]["cleaned"] is True
    assert all("activity" not in item and "data" not in item for item in entries)
    assert (await registry.get_run("parent", "old"))["text"] == "old result"
    assert (await registry.get_run("parent", "new"))["input_full"] == "full task"


@pytest.mark.asyncio
async def test_live_agent_kept_during_turn_cleanup_is_not_archived(progress):
    registry, config = progress
    await registry.record(
        UMO,
        config.instance_id,
        True,
        {
            "subagent_run_id": "running",
            "agent_name": "worker",
            "kind": "started",
            "payload": {},
        },
    )
    cleaned = SubAgentManager.cleanup_session_turn_end(UMO)
    assert "worker" not in cleaned["cleaned_agents"]
    assert (await registry.list_runs("parent"))[0]["cleaned"] is False


@pytest.mark.asyncio
async def test_legacy_traces_are_indexed_as_cleaned_and_reopenable(progress):
    registry, _ = progress
    async with registry.database().get_db() as session:
        session.add(
            PlatformMessageHistory(
                platform_id="webchat",
                user_id="parent",
                content={
                    "type": "bot",
                    "message": [
                        {
                            "type": "subagent_run",
                            "subagent_run_id": "legacy",
                            "agent_name": "old worker",
                            "status": "completed",
                            "text": "saved result",
                        }
                    ],
                },
            )
        )
        await session.commit()
    entries = await registry.list_runs("parent")
    assert entries[0]["cleaned"] is True
    assert (await registry.get_run("parent", "legacy"))["text"] == "saved result"
    assert await registry.get_configuration("parent", "legacy") == {}
    assert len(await registry.list_runs("parent")) == 1


@pytest.mark.asyncio
async def test_overflow_recovers_an_authoritative_snapshot(progress):
    registry, config = progress
    data = {
        "subagent_run_id": "busy",
        "agent_name": "worker",
        "kind": "started",
        "payload": {},
    }
    await registry.record(UMO, config.instance_id, True, data)
    stream = registry.stream("parent", "busy")
    await anext(stream)
    for _ in range(270):
        await registry.record(
            UMO,
            config.instance_id,
            True,
            {**data, "kind": "reasoning_delta", "payload": {"text": "x"}},
        )
    snapshot = await anext(stream)
    assert snapshot["type"] == "snapshot"
    assert snapshot["data"]["reasoning"] == "x" * 270
    assert snapshot["data"]["revision"] == 271
    await stream.aclose()


@pytest.mark.asyncio
async def test_session_deletion_does_not_resurrect_a_late_background_trace(progress):
    registry, config = progress
    data = {
        "subagent_run_id": "deleted",
        "agent_name": "worker",
        "kind": "started",
        "payload": {},
    }
    await registry.record(UMO, config.instance_id, True, data)
    await registry.delete_session("parent")
    await registry.record(
        UMO,
        config.instance_id,
        True,
        {**data, "kind": "completed", "payload": {"result_text": "late"}},
    )
    assert await registry.get_run("parent", "deleted") is None


@pytest.mark.asyncio
async def test_a_start_waiting_behind_deletion_cannot_recreate_its_snapshot(progress):
    registry, config = progress
    await registry.write_lock.acquire()
    deleting = asyncio.create_task(registry.delete_session("parent"))
    await asyncio.sleep(0)
    starting = asyncio.create_task(
        registry.record(
            UMO,
            config.instance_id,
            True,
            {
                "subagent_run_id": "late-start",
                "agent_name": "worker",
                "kind": "started",
                "payload": {},
            },
        )
    )
    await asyncio.sleep(0)
    registry.write_lock.release()
    await asyncio.gather(deleting, starting)
    assert await registry.get_run("parent", "late-start") is None


@pytest.mark.asyncio
async def test_finished_pipeline_wakes_even_when_runner_did_not_reach_done(monkeypatch):
    wake = AsyncMock()
    monkeypatch.setattr(
        FunctionToolExecutor, "_wake_main_agent_for_background_result", wake
    )
    event = SimpleNamespace(
        get_extra=lambda key, default=None: key == "_main_agent_execution_finished"
    )
    context = SimpleNamespace(
        context=SimpleNamespace(
            event=event,
            extra={"main_agent_runner": SimpleNamespace(done=lambda: False)},
        )
    )
    assert await FunctionToolExecutor._maybe_wake_main_agent_after_background(
        run_context=context,
        tool=SimpleNamespace(name="handoff"),
        task_id="task",
        agent_name="worker",
        result_text="done",
        tool_args={},
    )
    wake.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_run_id_in_an_imported_session_cannot_receive_foreign_events(
    progress,
):
    registry, config = progress
    data = {
        "subagent_run_id": "shared",
        "agent_name": "worker",
        "kind": "started",
        "payload": {},
    }
    await registry.record(UMO, config.instance_id, True, data)
    async with registry.database().get_db() as session:
        session.add(
            PlatformMessageHistory(
                platform_id="webchat",
                user_id="other",
                content={
                    "type": "bot",
                    "message": [
                        {
                            "type": "subagent_run",
                            "subagent_run_id": "shared",
                            "agent_name": "old copy",
                            "status": "completed",
                        }
                    ],
                },
            )
        )
        await session.commit()
    await asyncio.gather(registry.list_runs("other"), registry.list_runs("other"))
    stream = registry.stream("other", "shared")
    assert (await anext(stream))["data"]["agent_name"] == "old copy"
    await registry.record(
        UMO,
        config.instance_id,
        True,
        {**data, "kind": "text_delta", "payload": {"text": "private source text"}},
    )
    (queue,) = registry.subscribers["run:other:shared"]
    assert queue.empty()
    await stream.aclose()


@pytest.mark.asyncio
async def test_routes_check_session_ownership_before_storage_access(monkeypatch):
    from astrbot.dashboard.api.chat import (
        get_chat_subagent_configuration,
        list_chat_subagent_runs,
        stream_chat_subagent_runs,
    )
    from astrbot.dashboard.responses import ApiError
    from astrbot.dashboard.services.chat_service import ChatServiceError

    storage = SimpleNamespace(list_runs=AsyncMock())
    monkeypatch.setattr("astrbot.dashboard.api.chat.subagent_progress", storage)
    service = SimpleNamespace(
        require_owned_chat_session=AsyncMock(side_effect=ChatServiceError("foreign"))
    )
    auth = SimpleNamespace(username="owner")
    for route in (list_chat_subagent_runs, stream_chat_subagent_runs):
        with pytest.raises(ApiError):
            await route("foreign", auth=auth, service=service)
    with pytest.raises(ApiError):
        await get_chat_subagent_configuration(
            "foreign", "run", auth=auth, service=service
        )
    storage.list_runs.assert_not_awaited()
