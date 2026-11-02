"""Persisted observation through the shared run service, with real SQLite."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.po import (
    AgentWorkflowAttempt,
    AgentWorkflowCheckpoint,
    AgentWorkflowEvent,
    AgentWorkflowMessage,
    AgentWorkflowToolCall,
    AgentWorkflowUsage,
)
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.workflow_assets import router
from tests.agent_teams.test_workflow_assets import (
    completed_run,
)
from tests.agent_teams.test_workflow_assets import (
    workflow_env as workflow_env,
)


@pytest.mark.asyncio
async def test_reopening_run_reads_committed_progress_and_continues_cursor(
    workflow_env,
):
    env = workflow_env
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    finished = await completed_run(env.service, run["run_id"])
    observer = WorkflowService(env.db, RoleAssetService(env.db))
    reopened = await observer.get_run("alice", run["run_id"])
    assert reopened["cursor"] == finished["events"][-1]["cursor"]
    assert reopened["progress"] == {
        "total": 1,
        "completed": 1,
        "running": 0,
        "waiting": 0,
    }
    assert reopened["events"][0]["kind"] == "run.started"
    assert reopened["tasks"][0]["state"]["response"]["text"] == "Done"
    changes = await observer.get_events("alice", run["run_id"], 0)
    assert changes["snapshot"]["status"] == "completed"
    assert [event["cursor"] for event in changes["events"]] == sorted(
        {event["cursor"] for event in changes["events"]}
    )
    assert await observer.get_events("alice", run["run_id"], reopened["cursor"]) == {
        "cursor": reopened["cursor"],
        "reset": False,
        "events": [],
        "records": [],
        "messages": [],
        "snapshot": None,
    }


@pytest.mark.asyncio
async def test_run_list_and_unchanged_poll_omit_history_but_detail_and_changes_keep_it(
    workflow_env,
):
    env = workflow_env
    evidence = "INITIAL_INPUT " + "Long evidence. " * 4000 + "UNSELECTED_INPUT_END"
    asset = await env.service.save_asset(
        "alice",
        {"definition": {**env.asset["definition"], "required_inputs": ["evidence"]}},
    )
    historical = await env.service.start_run(
        "alice", asset["asset_id"], {"version": 1, "inputs": {"evidence": evidence}}
    )
    historical = await completed_run(env.service, historical["run_id"])
    task = historical["tasks"][0]
    selected = await env.service.start_run(
        "alice", env.asset["asset_id"], {"version": 1}
    )
    await completed_run(env.service, selected["run_id"])
    complete = {"text": "UNSELECTED_HISTORY " * 4000}
    await env.service.record_process(
        "alice",
        task["run_id"],
        task["state"]["attempt_id"],
        "long-history",
        "tool_result",
        complete,
    )
    observer = WorkflowService(env.db, RoleAssetService(env.db))
    listed = await observer.list_runs("alice")
    assert len(listed["runs"]) == 2
    summary = next(
        run for run in listed["runs"] if run["run_id"] == historical["run_id"]
    )
    assert summary["status"] == "completed"
    assert "process" not in summary and "events" not in summary
    assert "UNSELECTED_HISTORY" not in json.dumps(listed)
    assert "UNSELECTED_INPUT_END" not in json.dumps(listed)
    selected_detail = await observer.get_run("alice", selected["run_id"])
    unchanged = await observer.get_events(
        "alice", selected["run_id"], selected_detail["cursor"]
    )
    assert unchanged["snapshot"] is None and unchanged["events"] == []
    detail = await observer.get_run("alice", historical["run_id"])
    assert detail["tasks"][0]["process"][0]["data"] == complete
    assert detail["snapshot"]["inputs"] == {"evidence": evidence}
    await env.service.record_process(
        "alice",
        task["run_id"],
        task["state"]["attempt_id"],
        "next-history",
        "tool_result",
        {"text": "NEW_COMPLETE_HISTORY " * 4000},
    )
    changes = await observer.get_events("alice", historical["run_id"], detail["cursor"])
    assert changes["reset"] is False and changes["snapshot"] is None
    assert len(changes["events"]) == len(changes["records"]) == 1
    assert changes["records"][0]["data"]["text"] == "NEW_COMPLETE_HISTORY " * 4000
    reset = await observer.get_events(
        "alice", historical["run_id"], changes["cursor"] + 1
    )
    assert reset["reset"] is True
    assert reset["snapshot"]["tasks"][0]["process"][0]["data"] == complete
    assert (await observer.list_runs("bob"))["runs"] == []


@pytest.mark.asyncio
async def test_complete_process_records_keep_identity_and_duplicate_delivery_is_idempotent(
    workflow_env,
):
    env = workflow_env
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    run = (await completed_run(env.service, run["run_id"]))["tasks"][0]
    response = {
        "text": "Evidence. " * 4000,
        "reasoning_content": "Provider returned this.",
        "reasoning_signature": "provider-signature",
        "tool_calls": [
            {"id": "call-1", "name": "search", "args": {"query": "evidence"}}
        ],
    }
    record = await env.service.record_process(
        "alice",
        run["run_id"],
        run["state"]["attempt_id"],
        "response-1",
        "model_response",
        response,
    )
    duplicate, replay = await asyncio.gather(
        *[
            env.service.record_process(
                "alice",
                run["run_id"],
                run["state"]["attempt_id"],
                "response-1",
                "model_response",
                response,
            )
            for _ in range(2)
        ]
    )
    assert record == duplicate
    assert record == replay
    observer = WorkflowService(env.db, RoleAssetService(env.db))
    reopened = await observer.get_run("alice", run["run_id"])
    assert len(reopened["process"]) == 1
    saved = reopened["process"][0]
    assert saved["data"] == response
    assert saved["run_id"] == run["run_id"]
    assert saved["task_id"] == run["state"]["task_id"]
    assert saved["attempt_id"] == run["state"]["attempt_id"]
    assert saved["member_id"] == run["snapshot"]["member"]["member_id"]
    assert (
        len(
            [
                event
                for event in reopened["events"]
                if event["kind"] == "process.model_response"
            ]
        )
        == 1
    )
    with pytest.raises(RoleAssetError):
        await observer.record_process(
            "bob",
            run["run_id"],
            run["state"]["attempt_id"],
            "foreign",
            "model_response",
            {},
        )
    with pytest.raises(RoleAssetError, match="stale"):
        await observer.record_process(
            "alice", run["run_id"], "former-attempt", "late", "model_response", {}
        )


@pytest.mark.asyncio
async def test_old_cursor_replays_saved_records_in_bounded_batches(
    workflow_env,
):
    env = workflow_env
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    run = (await completed_run(env.service, run["run_id"]))["tasks"][0]
    initial = await env.service.get_run("alice", run["run_id"])
    for number in range(1001):
        await env.service.record_process(
            "alice",
            run["run_id"],
            run["state"]["attempt_id"],
            f"response-{number}",
            "model_response",
            {"text": f"Saved response {number}"},
        )
    cursor = initial["cursor"]
    records = []
    while len(records) < 1001:
        reconnect = await env.service.get_events("alice", run["run_id"], cursor)
        assert reconnect["reset"] is False and reconnect["snapshot"] is None
        assert len(reconnect["records"]) <= 128
        records.extend(reconnect["records"])
        cursor = reconnect["cursor"]
    assert records[-1]["data"]["text"] == "Saved response 1000"
    assert cursor > initial["cursor"]


@pytest.mark.asyncio
async def test_failed_event_save_leaves_no_visible_process_fact_or_advanced_cursor(
    workflow_env,
):
    env = workflow_env
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    before = (await completed_run(env.service, run["run_id"]))["tasks"][0]
    run = before
    before = await env.service.get_run("alice", run["run_id"])
    # Inject a storage fault at the real transaction boundary, without observing tables.
    async with env.db.engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE TRIGGER reject_process_event BEFORE INSERT ON agent_workflow_events WHEN NEW.kind = 'process.model_response' BEGIN SELECT RAISE(ABORT, 'storage fault'); END"
            )
        )
    with pytest.raises(IntegrityError):
        await env.service.record_process(
            "alice",
            run["run_id"],
            run["state"]["attempt_id"],
            "lost",
            "model_response",
            {"text": "Must not appear"},
        )
    after = await env.service.get_run("alice", run["run_id"])
    assert after == before


@pytest.mark.asyncio
async def test_cursor_http_entry_preserves_owner_and_validates_cursor(workflow_env):
    env = workflow_env
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    current = await completed_run(env.service, run["run_id"])
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    async with AsyncClient(
        transport=ASGITransport(app), base_url="http://test"
    ) as client:
        url = f"/api/v1/workflow_runs/{run['run_id']}/events"
        response = await client.get(url, params={"after": 0})
        assert response.status_code == 200
        assert response.json()["data"]["snapshot"]["status"] == "completed"
        assert response.json()["data"]["cursor"] == current["cursor"]
        assert (await client.get(url, params={"after": -1})).status_code == 422
        page_url = f"/api/v1/workflow_runs/{run['run_id']}/process"
        params = {
            "task_run_id": current["tasks"][0]["run_id"],
            "before": current["cursor"] + 1,
        }
        assert (await client.get(page_url, params=params)).status_code == 200
        assert (
            await client.get(page_url, params={**params, "limit": 129})
        ).status_code == 422
        history_url = f"/api/v1/workflow_runs/{run['run_id']}/history"
        history_params = {
            "task_run_id": current["tasks"][0]["run_id"],
            "kind": "events",
            "before": str(current["cursor"] + 1),
        }
        assert (await client.get(history_url, params=history_params)).status_code == 200
        assert (
            await client.get(history_url, params={**history_params, "kind": "invalid"})
        ).status_code == 422
        assert (
            await client.get(
                history_url, params={**history_params, "before": "invalid"}
            )
        ).status_code == 400
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        assert (await client.get(url)).status_code == 404
        assert (await client.get(page_url, params=params)).status_code == 404
        assert (await client.get(history_url, params=history_params)).status_code == 404


@pytest.mark.asyncio
async def test_same_member_in_another_scope_does_not_receive_message(workflow_env):
    env = workflow_env
    root = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    task = (await completed_run(env.service, root["run_id"]))["tasks"][0]
    async with env.db.get_db() as session:
        for scope, identity in [
            (task["state"]["scope_id"], "local"),
            ("other-scope", "foreign"),
        ]:
            session.add(
                AgentWorkflowMessage(
                    message_id=identity,
                    root_run_id=root["run_id"],
                    scope_id=scope,
                    sender_member_id="sender",
                    recipient_member_id=task["snapshot"]["member"]["member_id"],
                    sender_task_run_id="sender-run",
                    sender_attempt_id="sender-attempt",
                    request_id=identity,
                    payload={"text": identity},
                )
            )
        await session.commit()
    observed = await env.service.get_run("alice", task["run_id"])
    assert [message["message_id"] for message in observed["messages"]] == ["local"]
    observed_root = await env.service.get_run("alice", root["run_id"])
    assert [
        message["message_id"] for message in observed_root["tasks"][0]["messages"]
    ] == ["local"]


@pytest.mark.asyncio
async def test_bounded_snapshot_and_older_pages_share_event_cursor(workflow_env):
    env = workflow_env
    root = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    task = (await completed_run(env.service, root["run_id"]))["tasks"][0]
    for number in range(132):
        await env.service.record_process(
            "alice",
            task["run_id"],
            task["state"]["attempt_id"],
            str(number),
            "tool_result",
            {"number": number},
        )
    snapshot = await env.service.get_run("alice", root["run_id"], history_limit=128)
    records = snapshot["tasks"][0]["process"]
    assert len(records) == 128
    assert [r["data"]["number"] for r in records] == list(range(4, 132))
    await env.service.record_process(
        "alice",
        task["run_id"],
        task["state"]["attempt_id"],
        "later",
        "tool_result",
        {"number": 132},
    )
    older = await env.service.get_process_page(
        "alice", root["run_id"], task["run_id"], records[0]["cursor"], 2
    )
    assert [r["data"]["number"] for r in older["records"]] == [2, 3]
    oldest = await env.service.get_process_page(
        "alice", root["run_id"], task["run_id"], older["next_before"], 2
    )
    assert [r["data"]["number"] for r in oldest["records"]] == [0, 1]
    assert oldest["next_before"] is None
    changes = await env.service.get_events("alice", root["run_id"], snapshot["cursor"])
    assert [r["data"]["number"] for r in changes["records"]] == [132]
    with pytest.raises(RoleAssetError):
        await env.service.get_process_page(
            "bob", root["run_id"], task["run_id"], snapshot["cursor"] + 1
        )
    with pytest.raises(RoleAssetError):
        await env.service.get_process_page(
            "alice", root["run_id"], "not-a-descendant", snapshot["cursor"] + 1
        )


@pytest.mark.asyncio
async def test_snapshot_boundary_during_parallel_writes_and_two_reconnects(
    workflow_env,
):
    from contextlib import asynccontextmanager

    env = workflow_env
    definition = env.asset["definition"].copy()
    first = definition["tasks"][0]
    second_role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Second",
                "prompt": "Write",
                "provider_id": "model-a",
            },
            "installation": {"profile_id": "default"},
        },
    )
    definition["tasks"] = [
        first,
        {
            **first,
            "id": "parallel",
            "member": "parallel",
            "role_id": second_role["role_id"],
        },
    ]
    definition["max_parallel"] = 2
    asset = await env.service.save_asset("alice", {"definition": definition})
    root = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    finished = await completed_run(env.service, root["run_id"])
    tasks = finished["tasks"]
    assert len(tasks) == 2
    boundary = asyncio.Event()
    release = asyncio.Event()

    class SnapshotDatabase:
        engine = env.db.engine

        @asynccontextmanager
        async def get_db(self):
            async with env.db.get_db() as session:
                original = session.execute

                async def execute(statement, *args, **kwargs):
                    if (
                        "FROM agent_workflow_process" in str(statement)
                        and not boundary.is_set()
                    ):
                        boundary.set()
                        await release.wait()
                    return await original(statement, *args, **kwargs)

                session.execute = execute
                yield session

    observer = WorkflowService(SnapshotDatabase(), RoleAssetService(env.db))
    snapshot_task = asyncio.create_task(
        observer.get_run("alice", root["run_id"], history_limit=128)
    )
    await asyncio.wait_for(boundary.wait(), 5)
    gate = asyncio.Event()

    async def write(task, identity):
        await gate.wait()
        return await env.service.record_process(
            "alice",
            task["run_id"],
            task["state"]["attempt_id"],
            identity,
            "model_delta",
            {"receipt_id": identity, "sequence": 0, "text": identity},
        )

    writes = [
        asyncio.create_task(write(task, f"parallel-{index}"))
        for index, task in enumerate(tasks)
    ]
    gate.set()
    saved = await asyncio.gather(*writes)
    release.set()
    snapshot = await snapshot_task
    assert all(not task["process"] for task in snapshot["tasks"])
    for _ in range(2):
        reconnect = await env.service.get_events(
            "alice", root["run_id"], snapshot["cursor"]
        )
        assert {record["record_id"] for record in reconnect["records"]} == {
            record["record_id"] for record in saved
        }
        assert len({record["run_id"] for record in reconnect["records"]}) == 2
        assert (
            await env.service.get_events("alice", root["run_id"], reconnect["cursor"])
        )["records"] == []


@pytest.mark.asyncio
async def test_state_event_does_not_skip_older_process_batches(workflow_env):
    env = workflow_env
    root = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    current = await completed_run(env.service, root["run_id"])
    task = current["tasks"][0]
    cursor = current["cursor"]
    async with env.db.get_db() as session:
        session.add(
            AgentWorkflowEvent(run_id=task["run_id"], kind="task.changed", data={})
        )
        await session.commit()
    for number in range(132):
        await env.service.record_process(
            "alice",
            task["run_id"],
            task["state"]["attempt_id"],
            f"batch-{number}",
            "tool_result",
            {"number": number},
        )
    records = []
    for _ in range(2):
        page = await env.service.get_events("alice", root["run_id"], cursor)
        assert not page["reset"] and page["snapshot"] is None
        cursor = page["cursor"]
        records.extend(page["records"])
    assert [record["data"]["number"] for record in records] == list(range(132))


@pytest.mark.asyncio
async def test_stream_observers_are_bounded_and_release_on_disconnect(
    workflow_env, monkeypatch
):
    from astrbot.dashboard.api import workflow_assets

    env = workflow_env
    root = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    await completed_run(env.service, root["run_id"])
    monkeypatch.setattr(workflow_assets, "_process_observers", asyncio.Semaphore(2))
    request = SimpleNamespace(is_disconnected=lambda: asyncio.sleep(0, result=False))
    first = await workflow_assets.stream_events(
        root["run_id"], request, 0, "alice", env.service
    )
    second = await workflow_assets.stream_events(
        root["run_id"], request, 0, "alice", env.service
    )
    rejected = await workflow_assets.stream_events(
        root["run_id"], request, 0, "alice", env.service
    )
    assert rejected.status_code == 429
    assert (await first.body_iterator.__anext__()).startswith("data: ")
    assert (await second.body_iterator.__anext__()).startswith("data: ")
    await first.body_iterator.aclose()
    third = await workflow_assets.stream_events(
        root["run_id"], request, 0, "alice", env.service
    )
    assert (await third.body_iterator.__anext__()).startswith("data: ")
    await second.body_iterator.aclose()
    await third.body_iterator.aclose()
    abandoned = await workflow_assets.stream_events(
        root["run_id"], request, 0, "alice", env.service
    )
    await abandoned.background()
    available = [
        await workflow_assets.stream_events(
            root["run_id"], request, 0, "alice", env.service
        )
        for _ in range(2)
    ]
    for response in available:
        assert (await response.body_iterator.__anext__()).startswith("data: ")
    for response in available:
        await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_process_cursor_survives_an_independent_reader_restart(workflow_env):
    import sys

    env = workflow_env
    root = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    current = await completed_run(env.service, root["run_id"])
    task = current["tasks"][0]
    cursor = current["cursor"]
    saved = await env.service.record_process(
        "alice",
        task["run_id"],
        task["state"]["attempt_id"],
        "after-disconnect",
        "model_delta",
        {
            "receipt_id": "delivery",
            "sequence": 0,
            "text": "Persisted while disconnected",
        },
    )
    code = """import asyncio, json, sys
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
async def read():
    db = SQLiteDatabase(sys.argv[1])
    service = WorkflowService(db, RoleAssetService(db))
    print('PAGE=' + json.dumps(await service.get_events('alice', sys.argv[2], int(sys.argv[3]))))
    await db.engine.dispose()
asyncio.run(read())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        env.db.db_path,
        root["run_id"],
        str(cursor),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
    assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
    page = json.loads(
        next(
            line[5:]
            for line in stdout.decode("utf-8").splitlines()
            if line.startswith("PAGE=")
        )
    )
    assert [record["record_id"] for record in page["records"]] == [saved["record_id"]]
    assert page["records"][0]["data"]["text"] == "Persisted while disconnected"


@pytest.mark.asyncio
async def test_bounded_message_event_and_usage_history_pages(workflow_env):
    from datetime import datetime

    env = workflow_env
    root = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    current = await completed_run(env.service, root["run_id"])
    task = current["tasks"][0]
    task_id, scope_id = task["run_id"], task["state"]["scope_id"]
    async with env.db.get_db() as session:
        session.add(
            AgentWorkflowEvent(
                run_id=task_id,
                kind="result.rejected",
                data={"fields": [{"path": "result", "message": "bad"}]},
            )
        )
        for number in range(132):
            identity = f"msg-{number:03}"
            session.add(
                AgentWorkflowMessage(
                    message_id=identity,
                    root_run_id=root["run_id"],
                    scope_id=scope_id,
                    sender_member_id="sender",
                    recipient_member_id=task["snapshot"]["member"]["member_id"],
                    sender_task_run_id="sender-run",
                    sender_attempt_id="sender-attempt",
                    request_id=identity,
                    payload={"text": identity},
                )
            )
            session.add(
                AgentWorkflowEvent(
                    run_id=task_id,
                    kind="member.message_sent",
                    data={
                        "message_id": identity,
                        "scope_id": scope_id,
                        "recipient_member_id": task["snapshot"]["member"]["member_id"],
                    },
                )
            )
            attempt_id = f"attempt-{number:03}"
            session.add(
                AgentWorkflowAttempt(
                    attempt_id=attempt_id,
                    run_id=task_id,
                    data={
                        "status": "failed",
                        "state": {"reason": "example"},
                        "snapshot": {"inputs": {}},
                        "checkpoint": None,
                    },
                )
            )
            session.add(
                AgentWorkflowEvent(
                    run_id=task_id,
                    kind="attempt.retried",
                    data={"previous_attempt_id": attempt_id},
                )
            )
            session.add(
                AgentWorkflowUsage(
                    receipt_id=f"receipt-{number:03}",
                    created_at=datetime(2026, 1, 1),
                    root_run_id=root["run_id"],
                    run_id=task_id,
                    attempt_id="attempt",
                    execution_id="executor",
                    invocation_id="invocation",
                    data={
                        "kind": "model",
                        "quality": "reported",
                        "reported_tokens": 1,
                        "estimated_tokens": 0,
                        "reserved_tokens": 0,
                    },
                )
            )
        await session.commit()
    cursor = current["cursor"]
    resumed_messages = []
    while len(resumed_messages) < 132:
        batch = await env.service.get_events("alice", root["run_id"], cursor)
        assert batch["cursor"] > cursor
        cursor = batch["cursor"]
        resumed_messages.extend(batch["messages"])
    assert len({message["message_id"] for message in resumed_messages}) == 132
    snapshot = await env.service.get_run("alice", root["run_id"], history_limit=128)
    view = snapshot["tasks"][0]
    assert len(view["messages"]) == 128
    assert len(view["attempts"]) == 128
    assert len(view["events"]) <= 256
    assert view["validation_errors"][0]["message"] == "bad"
    assert view["usage"]["tokens"]["reported"] == 132
    assert len(view["usage"]["ledger"]) == 128
    with pytest.raises(RoleAssetError, match="Invalid usage cursor"):
        await env.service.get_run_history_page(
            "alice", root["run_id"], task_id, "usage", ""
        )
    for kind, before in [
        ("messages", str(view["message_history_before"])),
        ("events", str(view["event_history_before"])),
        ("usage", view["usage"]["ledger_before"]),
        ("attempts", str(view["attempt_history_before"])),
    ]:
        page = await env.service.get_run_history_page(
            "alice", root["run_id"], task_id, kind, before, 2
        )
        assert len(page["items"]) == 2
        assert page["next_before"] is not None
        boundary = page["next_before"]
        seen_boundaries = {boundary}
        while boundary is not None:
            next_page = await env.service.get_run_history_page(
                "alice", root["run_id"], task_id, kind, boundary, 128
            )
            assert next_page["items"]
            boundary = next_page["next_before"]
            assert boundary is None or boundary not in seen_boundaries
            seen_boundaries.add(boundary)
        with pytest.raises(RoleAssetError):
            await env.service.get_run_history_page(
                "bob", root["run_id"], task_id, kind, before
            )


@pytest.mark.asyncio
async def test_real_stream_disconnect_and_reconnect_reads_saved_events(workflow_env):
    import socket

    import uvicorn

    env = workflow_env
    root = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    current = await completed_run(env.service, root["run_id"])
    task = current["tasks"][0]
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))

    async def ready():
        while not server.started:
            if serving.done():
                await serving
                raise AssertionError("HTTP server exited before startup")
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(ready(), 10)
        async with AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=10
        ) as client:
            url = f"/api/v1/workflow_runs/{root['run_id']}/events/stream"
            async with client.stream(
                "GET", url, params={"after": current["cursor"]}
            ) as response:
                assert response.status_code == 200
                lines = response.aiter_lines()
                first = await env.service.record_process(
                    "alice",
                    task["run_id"],
                    task["state"]["attempt_id"],
                    "live-first",
                    "model_delta",
                    {"receipt_id": "call", "sequence": 0, "text": "First"},
                )
                line = await asyncio.wait_for(lines.__anext__(), 5)
                batch = json.loads(line.removeprefix("data: "))
                assert [record["record_id"] for record in batch["records"]] == [
                    first["record_id"]
                ]
                cursor = batch["cursor"]
            second = await env.service.record_process(
                "alice",
                task["run_id"],
                task["state"]["attempt_id"],
                "offline-second",
                "model_delta",
                {"receipt_id": "call", "sequence": 1, "text": "Second"},
            )
            async with client.stream("GET", url, params={"after": cursor}) as response:
                assert response.status_code == 200
                line = await asyncio.wait_for(response.aiter_lines().__anext__(), 5)
                replay = json.loads(line.removeprefix("data: "))
                assert [record["record_id"] for record in replay["records"]] == [
                    second["record_id"]
                ]
                assert replay["cursor"] > cursor
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, 10)
        listener.close()


@pytest.mark.asyncio
async def test_bounded_detail_retains_the_complete_current_tool_batch(workflow_env):
    env = workflow_env
    root = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    task = (await completed_run(env.service, root["run_id"]))["tasks"][0]
    async with env.db.get_db() as session:
        checkpoint = await session.get(AgentWorkflowCheckpoint, task["run_id"])
        if checkpoint is None:
            checkpoint = AgentWorkflowCheckpoint(
                run_id=task["run_id"],
                attempt_id=task["state"]["attempt_id"],
                phase="tool_progress",
                invocation_id="current",
                data={},
            )
        else:
            checkpoint.invocation_id = "current"
        session.add(checkpoint)
        for number, invocation in enumerate(["old", "current", "current"]):
            session.add(
                AgentWorkflowToolCall(
                    record_id=f"tool-{number}",
                    run_id=task["run_id"],
                    attempt_id=task["state"]["attempt_id"],
                    invocation_id=invocation,
                    call_id=f"call-{number}",
                    call_index=number,
                    name="tool",
                    args={},
                    execution_id="executor",
                    status="intent",
                )
            )
        await session.commit()
    full = await env.service.get_run("alice", task["run_id"])
    bounded = await env.service.get_run("alice", task["run_id"], history_limit=128)
    assert {call["record_id"] for call in full["tool_calls"]} == {
        "tool-0",
        "tool-1",
        "tool-2",
    }
    assert {call["record_id"] for call in bounded["tool_calls"]} == {"tool-1", "tool-2"}


@pytest.mark.asyncio
async def test_two_live_agents_persist_parallel_observations_without_a_reader(
    workflow_env,
):
    from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
    from astrbot.core.agent_team_tools import AgentTeamToolRegistry
    from astrbot.dashboard.services.agent_team_ports import TeamPorts

    env = workflow_env
    other = await env.roles.save_role(
        "alice",
        {
            "definition": {"name": "Other", "prompt": "Work", "provider_id": "model-a"},
            "installation": {"profile_id": "default"},
        },
    )
    definition = env.asset["definition"].copy()
    first = definition["tasks"][0]
    definition["tasks"] = [
        first,
        {**first, "id": "other", "member": "other", "role_id": other["role_id"]},
    ]
    definition["max_parallel"] = 2
    asset = await env.service.save_asset("alice", {"definition": definition})
    bindings = {}
    ready = asyncio.Queue()
    written = asyncio.Queue()
    gate, finish = asyncio.Event(), asyncio.Event()

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        bindings[message_id] = AgentTeamExecutionRegistry.resolve(execution_token)
        return message_id

    async def collect(session_id, message_id, member_id):
        binding = bindings[message_id]
        ready.put_nowait(binding.run_id)
        await gate.wait()
        await binding.record_process(
            "shared-delivery",
            "model_delta",
            {
                "receipt_id": "same-provider-receipt",
                "sequence": 0,
                "text": binding.run_id,
            },
        )
        written.put_nowait(binding.run_id)
        await finish.wait()
        await AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[
            0
        ].call(None, text="Done")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    root = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    try:
        agents = {
            await asyncio.wait_for(ready.get(), 10),
            await asyncio.wait_for(ready.get(), 10),
        }
        assert len(agents) == 2
        snapshot = await env.service.get_run("alice", root["run_id"], history_limit=128)
        gate.set()
        assert {
            await asyncio.wait_for(written.get(), 10),
            await asyncio.wait_for(written.get(), 10),
        } == agents
        observer = WorkflowService(env.db, RoleAssetService(env.db))
        changes = await observer.get_events("alice", root["run_id"], snapshot["cursor"])
        records = [
            record
            for record in changes["records"]
            if record["data"].get("receipt_id") == "same-provider-receipt"
        ]
        assert {record["run_id"] for record in records} == agents
        assert len({record["record_id"] for record in records}) == 2
        assert {record["data"]["text"] for record in records} == agents
    finally:
        finish.set()
