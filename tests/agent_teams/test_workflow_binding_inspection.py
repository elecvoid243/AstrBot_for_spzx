"""Inspect persisted data lines through the authenticated run API."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from astrbot.core.db.po import AgentWorkflowRun
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.workflow_assets import router
from astrbot.dashboard.responses import ApiError, error
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env
from tests.agent_teams.test_workflow_assets_api import (
    publication_client as publication_client,
)
from tests.agent_teams.test_workflow_graph import wait_graph
from tests.agent_teams.test_workflow_loops import (
    loop_script as loop_script,
)
from tests.agent_teams.test_workflow_loops import (
    revision_definition,
)


@pytest.mark.asyncio
async def test_inspection_reads_the_selected_round_and_attempt(
    loop_script, monkeypatch
):
    """Keep a failed review and its exact upstream artifact after a retry.

    Args:
        loop_script: Real workflow scheduler with a controlled model boundary.
        monkeypatch: Drop one post-commit dispatch for the cancellation case.
    """
    env = loop_script.env
    loop_script.failures.add(("review", 2))
    asset = await env.service.save_asset(
        "alice", {"definition": revision_definition(env.role["role_id"])}
    )
    started = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {"version": 1, "inputs": {"draft": "Original draft", "feedback": "None"}},
    )
    root_id = started["run_id"]
    run = await wait_graph(env.service, root_id)
    assert run["status"] == "blocked"
    first, second = run["loops"][0]["iterations"]
    failed = next(task for task in second["tasks"] if task["node_id"] == "review")
    failed_attempt = failed["state"]["attempt_id"]
    loop_script.failures.clear()
    await env.service.retry_task(
        "alice",
        failed["run_id"],
        {
            "request_id": "inspect-retry",
            "attempt_id": failed_attempt,
            "input_policy": "original",
        },
    )
    assert (await wait_graph(env.service, root_id))["status"] == "completed"

    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")

    @app.exception_handler(ApiError)
    async def api_error(request, exc):
        """Apply the production HTTP error status when testing the router.

        Args:
            request: Request handled by the isolated application.
            exc: Error returned by the Dashboard authorization boundary.

        Returns:
            The status and error envelope used by the production API.
        """
        return JSONResponse(error(exc.message, exc.data), status_code=exc.status_code)

    url = f"/api/v1/workflow_runs/{root_id}/data_line"
    request = {
        "scope_run_id": root_id,
        "task_run_id": failed["run_id"],
        "attempt_id": failed_attempt,
        "input_name": "draft",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get(url, params=request)).status_code == 401
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        assert (await client.get(url, params=request)).status_code == 404
        app.dependency_overrides[require_dashboard_user] = lambda: "alice"
        response = await client.get(url, params=request)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["iteration"] == 2
        assert data["attempt_id"] == failed_attempt
        assert data["task_status"] == "failed"
        assert data["read_status"] == "read"
        assert data["value"] == "Report revision 2"
        assert data["source"] == {"node_id": "write", "port": "text"}
        assert data["target"] == {
            "node_id": "review",
            "port": "draft",
            "type": "string",
        }
        assert data["evidence"]["artifact"] == {
            "artifact_id": second["task_runs"]["write"],
            "version": 1,
        }
        assert {entry["status"] for entry in data["attempts"]} == {
            "failed",
            "completed",
        }

        previous = await client.get(
            url,
            params={
                **request,
                "task_run_id": first["task_runs"]["review"],
                "attempt_id": "",
            },
        )
        assert previous.status_code == 200, previous.text
        assert previous.json()["data"]["value"] == "Report revision 1"
        wrong_identity = await client.get(
            url, params={**request, "task_run_id": first["task_runs"]["review"]}
        )
        assert wrong_identity.status_code == 404

        loop_script.failures.add(("review", 2))
        next_run = await env.service.start_run(
            "alice",
            asset["asset_id"],
            {"version": 1, "inputs": {"draft": "Other draft", "feedback": "None"}},
        )
        other_id = next_run["run_id"]
        blocked = await wait_graph(env.service, other_id)
        other = next(task for task in blocked["tasks"] if task["node_id"] == "review")
        attempted = other["state"]["attempt_id"]

        async def drop_dispatch(*args):
            """Interrupt the notification after transaction commit.

            Args:
                *args: The owner and scope arguments for the scheduled wake.

            Raises:
                RuntimeError: The committed retry notification was dropped.
            """
            raise RuntimeError("Lost retry dispatch")

        with monkeypatch.context() as fault:
            fault.setattr(env.service, "advance_run", drop_dispatch)
            with pytest.raises(RuntimeError, match="Lost retry dispatch"):
                await env.service.retry_task(
                    "alice",
                    other["run_id"],
                    {
                        "request_id": "unclaimed-inspection-retry",
                        "attempt_id": attempted,
                        "input_policy": "original",
                    },
                )
        await env.service.control_run(
            "alice", other_id, {"request_id": "cancel-unclaimed", "action": "cancel"}
        )
        cancelled_url = f"/api/v1/workflow_runs/{other_id}/data_line"
        cancelled_params = {
            "scope_run_id": other_id,
            "task_run_id": other["run_id"],
            "input_name": "draft",
        }
        unread = await client.get(cancelled_url, params=cancelled_params)
        assert unread.status_code == 200, unread.text
        assert unread.json()["data"]["task_status"] == "cancelled"
        assert unread.json()["data"]["read_status"] == "not_read"
        assert "value" not in unread.json()["data"]
        original = await client.get(
            cancelled_url,
            params={**cancelled_params, "attempt_id": attempted},
        )
        assert original.status_code == 200, original.text
        assert original.json()["data"]["value"] == "Report revision 2"


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream_passes", [True, False], ids=["passed", "exhausted"])
async def test_inspection_distinguishes_null_unread_and_skipped(
    workflow_env,
    publication_client,
    upstream_passes,
):
    """Separate a native null from an absent port and a skipped consumer.

    Args:
        workflow_env: Storage for constructing a pre-inspector persisted record.
        publication_client: Authenticated Dashboard client with isolated services.
        upstream_passes: Whether the last upstream result satisfies its exit condition.
    """
    payload = {
        "type": "object",
        "properties": {
            "nullable": {"type": ["string", "null"]},
            "omitted": {"type": "string"},
        },
        "required": ["nullable"],
    }
    source = {
        "id": "source",
        "name": "Source",
        "transform": "parse_json",
        "output_contract": payload,
        "inputs": {"text": {"input": "data"}},
    }
    source_inputs = {
        "type": "object",
        "properties": {"data": {"type": "string"}},
        "required": ["data"],
    }
    child_draft = await publication_client.post(
        "/api/v1/agent_drafts/workflow",
        json={
            "body": {
                "definition": {
                    "name": "Nullable source workflow",
                    "port_contract_version": 2,
                    "entry_task_ids": ["source"],
                    "result_task_id": "source",
                    "input_contract": source_inputs,
                    "tasks": [source],
                }
            }
        },
    )
    assert child_draft.status_code == 200, child_draft.text
    child_id = child_draft.json()["data"]["asset_id"]
    child = await publication_client.post(
        f"/api/v1/agent_drafts/workflow/{child_id}/publish", json={"revision": 1}
    )
    assert child.status_code == 200, child.text
    skipped_ports = {
        **payload,
        "properties": {
            **payload["properties"],
            "reference": {"type": "object", "format": "astrbot-artifact"},
        },
    }
    reader_ports = {
        **payload,
        "properties": {
            **payload["properties"],
            "nullable": {"allOf": [payload["properties"]["nullable"]]},
            "omitted": {"type": "string", "default": "Local default"},
        },
    }
    definition = {
        "name": "Inspect structured data",
        "port_contract_version": 2,
        "entry_task_ids": ["source", "gate", "reader"],
        "result_task_id": "reader",
        "input_contract": {
            "type": "object",
            "properties": {"data": {"type": "string"}, "take": {"type": "boolean"}},
            "required": ["data", "take"],
        },
        "tasks": [
            {
                "id": "source",
                "name": "Source",
                "workflow": {"asset_id": child_id, "version": 1},
                "input_contract": source_inputs,
                "output_contract": payload,
                "inputs": {"data": {"input": "data"}},
            },
            {
                "id": "gate",
                "name": "Gate",
                "control": {"kind": "condition", "value": True},
                "inputs": {"value": {"input": "take"}},
            },
            *[
                {
                    "id": node,
                    "name": node.capitalize(),
                    "transform": "map_fields",
                    "input_contract": skipped_ports
                    if node == "skipped"
                    else reader_ports,
                    "output_contract": skipped_ports if node == "skipped" else payload,
                    "inputs": {
                        key: {"task_id": "source", "field": key}
                        for key in payload["properties"]
                    }
                    | (
                        {"reference": {"task_id": "source", "field": "$artifact"}}
                        if node == "skipped"
                        else {}
                    ),
                }
                for node in ("reader", "skipped")
            ],
        ],
        "edges": [
            {
                "source": "gate",
                "target": "skipped",
                "condition": {"field": "matched", "value": True},
            }
        ],
        "loops": [
            {
                "id": key,
                "name": key,
                "body": [node],
                "exit_task_id": node,
                "exit_condition": {
                    "field": "nullable",
                    "value": None if upstream_passes else "expected",
                },
                "max_iterations": 1,
            }
            for key, node in (("upstream", "source"), ("downstream", "skipped"))
        ],
    }
    draft = await publication_client.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": definition}}
    )
    assert draft.status_code == 200, draft.text
    asset_id = draft.json()["data"]["asset_id"]
    published = await publication_client.post(
        f"/api/v1/agent_drafts/workflow/{asset_id}/publish", json={"revision": 1}
    )
    assert published.status_code == 200, published.text
    launched = await publication_client.post(
        f"/api/v1/workflow_assets/{asset_id}/runs",
        json={"version": 1, "inputs": {"data": '{"nullable": null}', "take": False}},
    )
    assert launched.status_code == 200, launched.text
    run = launched.json()["data"]
    assert run["status"] == ("completed" if upstream_passes else "blocked")
    by_node = {task["node_id"]: task["run_id"] for task in run["tasks"]}
    assert run["loops"][0]["status"] == ("passed" if upstream_passes else "exhausted")
    assert run["loops"][1]["status"] == "skipped"
    returned_artifact = run["scopes"][0]["tasks"][0]["result_ref"]
    assert returned_artifact["artifact_id"] != by_node["source"]
    url = f"/api/v1/workflow_runs/{run['run_id']}/data_line"
    reads = (
        (("reader", "nullable", "null"), ("reader", "omitted", "not_read"))
        if upstream_passes
        else (("reader", "nullable", "not_read"),)
    )
    for node, port, status in (
        *reads,
        ("skipped", "nullable", "skipped"),
        ("skipped", "reference", "skipped"),
    ):
        response = await publication_client.get(
            url,
            params={
                "scope_run_id": run["run_id"],
                "task_run_id": by_node[node],
                "input_name": port,
            },
        )
        assert response.status_code == 200, response.text
        line = response.json()["data"]
        assert line["read_status"] == status
        if port == "nullable":
            assert set(line["target"]["type"]) == {"null", "string"}
        if status == "null":
            assert line["value"] is None
        else:
            assert "value" not in line
        if status == "skipped":
            assert line["iteration"] == 1
            assert line["upstream"] == {
                "value": returned_artifact if port == "reference" else None,
                "source_run_id": by_node["source"],
                "artifact": returned_artifact,
            }

    if not upstream_passes:
        return

    # The pre-inspector snapshot kept selected_fields even if the port was
    # omitted. Removing only the new marker reproduces that persisted layout.
    async with workflow_env.db.get_db() as session:
        saved = await session.get(AgentWorkflowRun, by_node["reader"])
        assert saved.snapshot["inputs"]["omitted"] == "Local default"
        assert (
            "omitted"
            in saved.snapshot["selected_fields"][returned_artifact["artifact_id"]]
        )
        saved.snapshot = {
            key: value for key, value in saved.snapshot.items() if key != "input_reads"
        }
        session.add(saved)
        await session.commit()
    legacy_params = {"scope_run_id": run["run_id"], "task_run_id": by_node["reader"]}
    for port, status in (("omitted", "missing"), ("nullable", "null")):
        legacy = await publication_client.get(
            url, params={**legacy_params, "input_name": port}
        )
        assert legacy.status_code == 200, legacy.text
        assert legacy.json()["data"]["read_status"] == status
        if status == "missing":
            assert "evidence" not in legacy.json()["data"]
