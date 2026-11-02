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
        "snapshot": None,
    }


@pytest.mark.asyncio
async def test_run_list_and_unchanged_poll_omit_history_but_detail_and_changes_keep_it(workflow_env):
    env = workflow_env
    evidence = "INITIAL_INPUT " + "Long evidence. " * 4000 + "UNSELECTED_INPUT_END"
    asset = await env.service.save_asset("alice", {"definition": {**env.asset["definition"], "required_inputs": ["evidence"]}})
    historical = await env.service.start_run("alice", asset["asset_id"], {"version": 1, "inputs": {"evidence": evidence}})
    historical = await completed_run(env.service, historical["run_id"])
    task = historical["tasks"][0]
    selected = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    await completed_run(env.service, selected["run_id"])
    complete = {"text": "UNSELECTED_HISTORY " * 4000}
    await env.service.record_process("alice", task["run_id"], task["state"]["attempt_id"], "long-history", "tool_result", complete)
    observer = WorkflowService(env.db, RoleAssetService(env.db))
    listed = await observer.list_runs("alice")
    assert len(listed["runs"]) == 2
    summary = next(run for run in listed["runs"] if run["run_id"] == historical["run_id"])
    assert summary["status"] == "completed"
    assert "process" not in summary and "events" not in summary
    assert "UNSELECTED_HISTORY" not in json.dumps(listed)
    assert "UNSELECTED_INPUT_END" not in json.dumps(listed)
    selected_detail = await observer.get_run("alice", selected["run_id"])
    unchanged = await observer.get_events("alice", selected["run_id"], selected_detail["cursor"])
    assert unchanged["snapshot"] is None and unchanged["events"] == []
    detail = await observer.get_run("alice", historical["run_id"])
    assert detail["tasks"][0]["process"][0]["data"] == complete
    assert detail["snapshot"]["inputs"] == {"evidence": evidence}
    await env.service.record_process("alice", task["run_id"], task["state"]["attempt_id"], "next-history", "tool_result", {"text": "NEW_COMPLETE_HISTORY " * 4000})
    changes = await observer.get_events("alice", historical["run_id"], detail["cursor"])
    assert changes["reset"] is False and changes["snapshot"] is None
    assert len(changes["events"]) == len(changes["records"]) == 1
    assert changes["records"][0]["data"]["text"] == "NEW_COMPLETE_HISTORY " * 4000
    reset = await observer.get_events("alice", historical["run_id"], changes["cursor"] + 1)
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
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        assert (await client.get(url)).status_code == 404
