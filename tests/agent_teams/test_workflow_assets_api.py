"""Ownership, input diagnostics and pinned workflow versions over HTTP."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.po import AgentWorkflowVersion
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.provider.provider import Provider
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.role_assets import router as roles_router
from astrbot.dashboard.api.workflow_assets import router
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest_asyncio.fixture
async def publication_client(workflow_env):
    """Expose the real asset and draft endpoints for the workflow owner.

    Args:
        workflow_env: Services backed by the test's isolated database.

    Yields:
        An HTTP client authenticated as the fixture's asset owner.
    """
    app = FastAPI()
    app.state.services = SimpleNamespace(
        role_assets=workflow_env.roles, workflow_assets=workflow_env.service
    )
    app.include_router(roles_router, prefix="/api/v1")
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_structured_ports_across_trial_and_published_run(
    workflow_env, publication_client
):
    query = {
        "type": "object",
        "properties": {"limit": {"type": "integer"}},
        "required": ["limit"],
    }
    rows = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    }
    nullable = {"type": ["string", "null"], "default": "fallback"}
    properties = {
        "query": query,
        "rows": rows,
        "label": nullable,
        "enabled": {"type": "boolean"},
        "note": {"type": "string"},
    }
    result_contract = {
        "type": "object",
        "properties": properties,
        "required": ["query", "rows", "enabled"],
        "additionalProperties": False,
    }
    definition = {
        "name": "Structured data",
        "port_contract_version": 2,
        "input_contract": {
            "type": "object",
            "properties": {
                "raw": {"type": "string"},
                "label": nullable,
                "enabled": {"type": "boolean"},
                "files": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["raw", "enabled"],
        },
        "result_task_id": "map",
        "edges": [{"source": "parse", "target": "map"}],
        "tasks": [
            {
                "id": "parse",
                "name": "Parse",
                "transform": "parse_json",
                "inputs": {"text": {"input": "raw"}},
                "output_contract": {
                    "type": "object",
                    "properties": {"query": query, "rows": rows},
                    "required": ["query", "rows"],
                },
            },
            {
                "id": "map",
                "name": "Map",
                "transform": "map_fields",
                "input_contract": result_contract,
                "output_contract": result_contract,
                "inputs": {
                    "query": {"task_id": "parse", "field": "query"},
                    "rows": {"task_id": "parse", "field": "rows"},
                    "label": {"input": "label"},
                    "enabled": {"input": "enabled"},
                    "note": {},
                },
            },
        ],
    }
    created = await publication_client.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": definition}}
    )
    assert created.status_code == 200, created.text
    asset_id = created.json()["data"]["asset_id"]
    draft = f"/api/v1/agent_drafts/workflow/{asset_id}"
    asset = f"/api/v1/workflow_assets/{asset_id}"
    reopened = await publication_client.get(draft)
    assert reopened.json()["data"]["body"]["definition"] == definition

    full = {"kind": "workflow", "revision": 1, "mode": "full"}
    partial = {**full, "mode": "partial", "node_id": "map"}
    payload = {
        "raw": '{"query":{"limit":0},"rows":[{"id":1},{"id":2}]}',
        "enabled": False,
        "files": ["notes.txt"],
    }
    structured = {"query": {"limit": 0}, "rows": [{"id": 1}, {"id": 2}]}
    trial = await publication_client.post(
        f"{asset}/trials", json={**full, "inputs": payload}
    )
    published = await publication_client.post(f"{draft}/publish", json={"revision": 1})
    assert published.status_code == 200, published.text
    assert published.json()["data"]["definition"]["port_contract_version"] == 2
    invalid_launch = await publication_client.post(
        f"{asset}/runs", json={"version": 1, "inputs": {**payload, "enabled": None}}
    )
    assert invalid_launch.status_code == 400, invalid_launch.text
    assert invalid_launch.json()["data"]["fields"][0]["path"] == "inputs.enabled"
    live = await publication_client.post(
        f"{asset}/runs", json={"version": 1, "inputs": {**payload, "label": None}}
    )
    preview = await publication_client.post(f"{asset}/trials/preview", json=partial)
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["requirements"]["parse"]["fields"] == {
        "query": ["object"],
        "rows": ["array"],
    }
    bounded = await publication_client.post(
        f"{asset}/trials",
        json={
            **partial,
            "inputs": {"enabled": False},
            "sources": {"parse": {"content": structured}},
        },
    )
    invalid = await publication_client.post(
        f"{asset}/trials",
        json={
            **partial,
            "inputs": {"enabled": False},
            "sources": {"parse": {"content": {**structured, "rows": [{"id": "oops"}]}}},
        },
    )
    assert invalid.status_code == 400, invalid.text
    assert [item["path"] for item in invalid.json()["data"]["fields"]] == [
        "definition.tasks.1.inputs.rows.0.id"
    ]
    for response, label in ((trial, "fallback"), (live, None), (bounded, "fallback")):
        assert response.status_code == 200, response.text
        run = await completed_run(
            workflow_env.service, response.json()["data"]["run_id"]
        )
        assert run["status"] == "completed", run
        expected = {**structured, "label": label, "enabled": False}
        assert run["tasks"][-1]["snapshot"]["inputs"] == expected
        assert await workflow_env.service.get_result("alice", run["run_id"]) == expected

    # A disconnected optional port is safe in full and bounded compilation. A
    # cross-field root constraint keeps the complete trial input form.
    definition["tasks"][1]["inputs"]["label"] = {}
    definition["input_contract"]["minProperties"] = 3
    saved = await publication_client.put(
        draft, json={"body": {"definition": definition}, "expected_revision": 1}
    )
    assert saved.status_code == 200, saved.text
    cross_field = await publication_client.post(
        f"{asset}/trials/preview", json={**partial, "revision": 2}
    )
    assert cross_field.json()["data"]["input_contract"] == definition["input_contract"]
    bounded = await publication_client.post(
        f"{asset}/trials",
        json={
            **partial,
            "revision": 2,
            "inputs": payload,
            "sources": {"parse": {"content": structured}},
        },
    )
    assert bounded.status_code == 200, bounded.text
    run = await completed_run(workflow_env.service, bounded.json()["data"]["run_id"])
    assert run["result"] == {**structured, "label": "fallback", "enabled": False}

    for raw, path in (
        ('{"query":{"limit":1e400},"rows":[]}', "inputs.text"),
        (r'{"query":{"limit":0,"bad":"\ud800"},"rows":[]}', "inputs.text"),
        (
            '{"query":{"limit":0},"rows":[],"files":[{"file_id":"unrelated"}]}',
            "output_contract.files",
        ),
    ):
        invalid_parse = await publication_client.post(
            f"{asset}/trials",
            json={**full, "revision": 2, "inputs": {**payload, "raw": raw}},
        )
        assert invalid_parse.status_code == 200, invalid_parse.text
        run = await completed_run(
            workflow_env.service, invalid_parse.json()["data"]["run_id"]
        )
        assert run["status"] == "blocked"
        assert (
            run["tasks"][0]["validation_errors"][0]["path"]
            == f"definition.tasks.0.{path}"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("source_kind", ["workflow-object", "task-array"])
async def test_structured_publication_checks_the_bound_shape(
    workflow_env, publication_client, source_kind
):
    """Locate incompatible structured bindings and let the draft be repaired.

    Args:
        workflow_env: Persistent workflow services backed by isolated SQLite.
        publication_client: Authenticated client for the real draft and run routes.
        source_kind: Workflow object input or an upstream node's typed list.
    """
    source_field = {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "hint": {"type": "string"}},
        "required": ["id"],
        "additionalProperties": False,
    }
    target_field = {
        **source_field,
        "properties": {"id": {"type": "string"}},
    }
    repaired_field = {
        **source_field,
        "properties": {"id": {"type": "number"}, "hint": {"type": "boolean"}},
    }
    value = {"id": 1}
    if source_kind == "task-array":
        source_field = {"type": "array", "items": source_field}
        target_field = {"type": "array", "items": target_field}
        repaired_field = {"type": "array", "items": repaired_field}
        value = [value]
    source_contract = {
        "type": "object",
        "properties": {"value": source_field},
        "required": ["value"],
    }
    target_contract = {**source_contract, "properties": {"value": target_field}}
    definition = {
        "name": "Structured bindings",
        "port_contract_version": 2,
        "input_contract": source_contract,
        "entry_task_ids": ["map"],
        "result_task_id": "map",
        "tasks": [
            {
                "id": "map",
                "name": "Map",
                "transform": "map_fields",
                "input_contract": target_contract,
                "output_contract": target_contract,
                "inputs": {"value": {"input": "value"}},
            }
        ],
    }
    target = definition["tasks"][0]
    inputs = {"value": value}
    if source_kind == "task-array":
        definition["input_contract"] = {
            "type": "object",
            "properties": {"raw": {"type": "string"}},
            "required": ["raw"],
        }
        inputs = {"raw": '{"value":[{"id":1}]}'}
        definition["entry_task_ids"] = ["parse"]
        definition["edges"] = [{"source": "parse", "target": "map"}]
        definition["tasks"].insert(
            0,
            {
                "id": "parse",
                "name": "Parse",
                "transform": "parse_json",
                "inputs": {"text": {"input": "raw"}},
                "output_contract": source_contract,
            },
        )
        target["inputs"]["value"] = {"task_id": "parse", "field": "value"}

    created = await publication_client.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": definition}}
    )
    assert created.status_code == 200, created.text
    asset_id = created.json()["data"]["asset_id"]
    draft = f"/api/v1/agent_drafts/workflow/{asset_id}"
    asset = f"/api/v1/workflow_assets/{asset_id}"
    path = f"definition.tasks.{definition['tasks'].index(target)}.inputs.value"
    for url, options in (
        (f"{draft}/publish", {}),
        (f"{asset}/trials/preview", {"kind": "workflow", "mode": "full"}),
    ):
        rejected = await publication_client.post(url, json={"revision": 1, **options})
        assert rejected.status_code == 400, rejected.text
        assert [
            (field["path"], field["code"])
            for field in rejected.json()["data"]["fields"]
        ] == [(path, "INCOMPATIBLE")]
    assert (await publication_client.get(asset)).status_code == 404

    if source_kind == "task-array":
        # An empty list used to satisfy both fixed contracts at runtime.
        legacy_id = "historical-structured-workflow"
        async with workflow_env.db.get_db() as session:
            session.add(
                AgentWorkflowVersion(
                    asset_id=legacy_id,
                    version=1,
                    owner_username="alice",
                    definition=definition,
                )
            )
            await session.commit()
        legacy = await publication_client.post(
            f"/api/v1/workflow_assets/{legacy_id}/runs",
            json={"version": 1, "inputs": {"raw": '{"value":[]}'}},
        )
        assert legacy.status_code == 200, legacy.text
        run = await completed_run(workflow_env.service, legacy.json()["data"]["run_id"])
        assert run["status"] == "completed", run
        assert run["result"] == {"value": []}

    # Integer values satisfy number ports, and either optional hint can be absent.
    repaired = {**source_contract, "properties": {"value": repaired_field}}
    target.update(input_contract=repaired, output_contract=repaired)
    saved = await publication_client.put(
        draft, json={"body": {"definition": definition}, "expected_revision": 1}
    )
    assert saved.status_code == 200, saved.text
    published = await publication_client.post(f"{draft}/publish", json={"revision": 2})
    assert published.status_code == 200, published.text
    assert published.json()["data"]["version"] == 1
    started = await publication_client.post(
        f"{asset}/runs", json={"version": 1, "inputs": inputs}
    )
    assert started.status_code == 200, started.text
    run = await completed_run(workflow_env.service, started.json()["data"]["run_id"])
    assert run["status"] == "completed", run
    assert run["result"] == {"value": value}


@pytest.mark.asyncio
async def test_structured_publication_reports_the_binding_in_a_dependency_cycle(
    publication_client,
):
    """A control path and reverse data binding identify the consumer's input.

    Args:
        publication_client: Authenticated client for the real draft routes.
    """
    contract = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    node = {
        "transform": "map_fields",
        "input_contract": contract,
        "output_contract": contract,
    }
    definition = {
        "name": "Cycle",
        "port_contract_version": 2,
        "input_contract": contract,
        "entry_task_ids": ["first"],
        "result_task_id": "second",
        "edges": [{"source": "first", "target": "second"}],
        "tasks": [
            {
                **node,
                "id": "first",
                "name": "First",
                "inputs": {"value": {"task_id": "second", "field": "value"}},
            },
            {
                **node,
                "id": "second",
                "name": "Second",
                "inputs": {"value": {"task_id": "first", "field": "value"}},
            },
        ],
    }
    created = await publication_client.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": definition}}
    )
    assert created.status_code == 200, created.text
    asset_id = created.json()["data"]["asset_id"]
    rejected = await publication_client.post(
        f"/api/v1/agent_drafts/workflow/{asset_id}/publish", json={"revision": 1}
    )
    assert rejected.status_code == 400, rejected.text
    assert [
        field["path"]
        for field in rejected.json()["data"]["fields"]
        if field["code"] == "CYCLE"
    ] == ["definition.tasks.0.inputs.value"]


@pytest.mark.asyncio
async def test_explicit_json_conversion_and_default_text_binding(
    workflow_env, publication_client
):
    writer = workflow_env.asset["definition"]["tasks"][0]
    definition = {
        "name": "Explicit JSON conversion",
        "port_contract_version": 2,
        "input_contract": {
            "type": "object",
            "properties": {
                "value": {"type": "object"},
                "prefix": {"type": "string", "default": "Resolved"},
            },
            "required": ["value"],
        },
        "result_task_id": writer["id"],
        "edges": [{"source": "serialize", "target": writer["id"]}],
        "tasks": [
            {
                "id": "serialize",
                "name": "JSON to text",
                "transform": "json_text",
                "inputs": {"value": {"input": "value"}},
            },
            {
                **writer,
                "task": "{prefix}: {subject}",
                "inputs": {
                    "prefix": {"input": "prefix"},
                    "subject": {"task_id": "serialize", "field": "text"},
                },
            },
        ],
    }
    for properties in (None, 1):
        saved = await publication_client.post(
            "/api/v1/workflow_assets",
            json={
                "definition": {
                    **definition,
                    "input_contract": {"type": "object", "properties": properties},
                }
            },
        )
        assert saved.status_code == 400, saved.text
        assert saved.json()["data"]["fields"][0]["path"] == "definition.input_contract"

    saved = await publication_client.post(
        "/api/v1/workflow_assets", json={"definition": definition}
    )
    assert saved.status_code == 200, saved.text
    child_id = saved.json()["data"]["asset_id"]
    started = await publication_client.post(
        f"/api/v1/workflow_assets/{child_id}/runs",
        json={"version": 1, "inputs": {"value": {"x": False}}},
    )
    assert started.status_code == 200, started.text
    run = await completed_run(workflow_env.service, started.json()["data"]["run_id"])
    assert run["status"] == "completed", run
    assert run["tasks"][0]["snapshot"]["inputs"] == {"value": {"x": False}}
    assert workflow_env.observations[-1][1] == 'Resolved: {"x": false}'

    parent = await publication_client.post(
        "/api/v1/workflow_assets",
        json={
            "definition": {
                "name": "Fixed call",
                "result_task_id": "child",
                "tasks": [
                    {
                        "id": "child",
                        "name": "Child",
                        "workflow": {"asset_id": child_id, "version": 1},
                    }
                ],
            },
        },
    )
    assert parent.status_code == 400, parent.text
    assert [
        (field["path"], field["code"]) for field in parent.json()["data"]["fields"]
    ] == [("definition.tasks.0.workflow", "INCOMPATIBLE")]


@pytest.mark.asyncio
async def test_text_port_draft_and_published_run_share_validation(
    workflow_env, publication_client
):
    definition = {
        **workflow_env.asset["definition"],
        "port_contract_version": 1,
        "required_inputs": ["topic", "other"],
        "result_task_id": "writer",
        "tasks": [
            {
                **workflow_env.asset["definition"]["tasks"][0],
                "task": "Write about {subject}",
                "inputs": {},
            }
        ],
    }
    created = await publication_client.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": definition}}
    )
    assert created.status_code == 200, created.text
    asset_id = created.json()["data"]["asset_id"]
    draft_path = f"/api/v1/agent_drafts/workflow/{asset_id}"
    asset_path = f"/api/v1/workflow_assets/{asset_id}"
    preview_path = f"{asset_path}/trials/preview"
    publish_path = f"{draft_path}/publish"
    trial = {"kind": "workflow", "mode": "full"}

    async def assert_rejected(revision, path, code):
        for url, payload in ((preview_path, trial), (publish_path, {})):
            response = await publication_client.post(
                url, json={**payload, "revision": revision}
            )
            assert response.status_code == 400, response.text
            assert [
                (f["path"], f["code"]) for f in response.json()["data"]["fields"]
            ] == [(path, code)]

    await assert_rejected(1, "definition.tasks.0.inputs.subject", "REQUIRED")
    writer = definition["tasks"][0]
    writer["inputs"]["subject"] = {"input": "deleted"}
    saved = await publication_client.put(
        draft_path, json={"body": {"definition": definition}, "expected_revision": 1}
    )
    assert saved.status_code == 200, saved.text
    await assert_rejected(2, "definition.tasks.0.inputs.subject", "REFERENCE")

    writer["inputs"]["subject"] = {"input": "topic"}
    writer["output_contract"] = {
        "type": "object",
        "properties": {"text": {"type": "boolean"}},
        "required": ["text"],
    }
    saved = await publication_client.put(
        draft_path, json={"body": {"definition": definition}, "expected_revision": 2}
    )
    assert saved.status_code == 200, saved.text
    await assert_rejected(3, "definition.result_task_id", "INCOMPATIBLE")

    writer["output_contract"] = None
    saved = await publication_client.put(
        draft_path, json={"body": {"definition": definition}, "expected_revision": 3}
    )
    assert saved.status_code == 200, saved.text
    preview = await publication_client.post(preview_path, json={**trial, "revision": 4})
    assert preview.json()["data"]["required_inputs"] == ["topic", "other"]
    for mode in ("single", "partial"):
        bounded = await publication_client.post(
            preview_path,
            json={**trial, "revision": 4, "mode": mode, "node_id": "writer"},
        )
        assert bounded.status_code == 200, bounded.text
        assert bounded.json()["data"]["required_inputs"] == ["topic"]
    tested = await publication_client.post(
        f"{asset_path}/trials",
        json={
            **trial,
            "revision": 4,
            "inputs": {"topic": "trial-topic", "other": "unbound-trial"},
        },
    )
    assert tested.status_code == 200, tested.text
    published = await publication_client.post(publish_path, json={"revision": 4})
    assert published.status_code == 200, published.text
    version = published.json()["data"]
    assert version["definition"]["port_contract_version"] == 1
    started = await publication_client.post(
        f"{asset_path}/runs",
        json={"version": 1, "inputs": {"topic": "live-topic", "other": "unbound-live"}},
    )
    assert started.status_code == 200, started.text

    for response, value in ((tested, "trial-topic"), (started, "live-topic")):
        run = await completed_run(
            workflow_env.service, response.json()["data"]["run_id"]
        )
        assert run["status"] == "completed", run
        assert run["result_ref"] == run["tasks"][0]["result_ref"]
        assert run["tasks"][0]["snapshot"]["inputs"] == {"subject": value}
    assert [turn[1] for turn in workflow_env.observations[-2:]] == [
        "Write about trial-topic",
        "Write about live-topic",
    ]


@pytest.mark.asyncio
async def test_typed_agent_loop_feedback_uses_the_same_text_rule_in_every_scope(
    workflow_env, publication_client
):
    agent = workflow_env.asset["definition"]["tasks"][0]
    definition = {
        "name": "Loop with typed input",
        "port_contract_version": 1,
        "required_inputs": ["topic"],
        "result_task_id": "review",
        "tasks": [
            {**agent, "task": "{topic}", "inputs": {"topic": {"input": "topic"}}},
            {
                **agent,
                "id": "review",
                "output_contract": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "approved": {"type": "boolean"},
                    },
                    "required": ["text", "approved"],
                },
            },
        ],
        "edges": [{"source": "writer", "target": "review"}],
        "loops": [
            {
                "id": "rework",
                "name": "Rework",
                "body": ["writer", "review"],
                "exit_task_id": "review",
                "exit_condition": {"field": "approved", "value": True},
                "max_iterations": 2,
                "feedback": [
                    {
                        "target": "writer",
                        "input": "topic",
                        "source": "review",
                        "field": "approved",
                    }
                ],
            }
        ],
    }
    created = await publication_client.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": definition}}
    )
    assert created.status_code == 200, created.text
    asset_id = created.json()["data"]["asset_id"]
    preview = f"/api/v1/workflow_assets/{asset_id}/trials/preview"
    for url, payload in (
        (f"/api/v1/agent_drafts/workflow/{asset_id}/publish", {}),
        (preview, {"kind": "workflow", "mode": "full"}),
        (preview, {"kind": "workflow", "mode": "single", "node_id": "writer"}),
    ):
        response = await publication_client.post(url, json={"revision": 1, **payload})
        assert response.status_code == 400, response.text
        fields = response.json()["data"]["fields"]
        assert [(field["path"], field["code"]) for field in fields] == [
            ("definition.tasks.0.inputs.topic", "INCOMPATIBLE")
        ]

    # Existing fixed graphs can still supply boolean feedback through a placeholder.
    del definition["port_contract_version"]
    legacy = await publication_client.post(
        "/api/v1/workflow_assets", json={"definition": definition}
    )
    assert legacy.status_code == 200, legacy.text
    assert "port_contract_version" not in legacy.json()["data"]["definition"]


@pytest.mark.asyncio
async def test_deletion_http_checks_ownership_and_hides_both_directories(workflow_env):
    env = workflow_env
    app = FastAPI()
    app.state.services = SimpleNamespace(
        role_assets=env.roles, workflow_assets=env.service
    )
    app.include_router(roles_router, prefix="/api/v1")
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "bob"
    role_path = f"/api/v1/agent_roles/{env.role['role_id']}"
    asset_path = f"/api/v1/workflow_assets/{env.asset['asset_id']}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.delete(role_path)).status_code == 404
        assert (await client.delete(asset_path)).status_code == 404
        app.dependency_overrides[require_dashboard_user] = lambda: "alice"
        assert (await client.delete(role_path)).status_code == 200
        assert (await client.delete(role_path)).status_code == 200
        assert (await client.delete(asset_path)).status_code == 200
        assert (await client.get("/api/v1/agent_roles")).json()["data"]["roles"] == []
        assert (await client.get("/api/v1/workflow_assets")).json()["data"][
            "assets"
        ] == []
        assert (await client.get(role_path)).json()["data"]["deleted"]
        assert (await client.get(asset_path)).json()["data"]["deleted"]
        assert (
            await client.post(asset_path + "/runs", json={"version": 1})
        ).status_code == 409


@pytest.mark.asyncio
async def test_draft_with_deleted_result_can_be_reopened_repaired_and_started(
    workflow_env,
    publication_client,
):
    env = workflow_env
    definition = {
        **env.asset["definition"],
        "result_task_id": "deleted",
        "required_inputs": ["topic"],
        "tasks": [
            {
                **env.asset["definition"]["tasks"][0],
                "task": "Write about {topic}",
                "inputs": {"topic": {"input": "topic", "field": "text"}},
            }
        ],
    }
    created = await publication_client.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": definition}}
    )
    assert created.status_code == 200, created.text
    asset_id = created.json()["data"]["asset_id"]
    draft_path = f"/api/v1/agent_drafts/workflow/{asset_id}"
    asset_path = f"/api/v1/workflow_assets/{asset_id}"
    invalid = await publication_client.post(
        f"{draft_path}/publish", json={"revision": 1}
    )
    assert invalid.status_code == 400, invalid.text
    assert invalid.json()["data"]["fields"][0]["path"] == "definition.result_task_id"
    assert (await publication_client.get(asset_path)).status_code == 404
    reopened = (await publication_client.get(draft_path)).json()["data"]
    assert reopened["revision"] == 1
    assert reopened["body"]["definition"]["result_task_id"] == "deleted"

    trial_path = f"{asset_path}/trials"
    trial = {"kind": "workflow", "revision": 1}
    full = await publication_client.post(
        f"{trial_path}/preview", json={**trial, "mode": "full"}
    )
    assert full.status_code == 400, full.text
    assert full.json()["data"]["fields"][0]["path"] == "definition.result_task_id"
    trial.update(mode="partial", node_id="writer")
    preview = await publication_client.post(f"{trial_path}/preview", json=trial)
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["selected_nodes"] == ["writer"]
    assert preview.json()["data"]["required_inputs"] == ["topic"]
    tested = await publication_client.post(
        trial_path, json={**trial, "inputs": {"topic": "energy"}}
    )
    assert tested.status_code == 200, tested.text
    result = await completed_run(env.service, tested.json()["data"]["run_id"])
    assert result["status"] == "completed"
    assert result["snapshot"]["asset_version"] is None

    definition["result_task_id"] = "writer"
    corrected = await publication_client.put(
        draft_path,
        json={"body": {"definition": definition}, "expected_revision": 1},
    )
    assert corrected.status_code == 200, corrected.text
    published = await publication_client.post(
        f"{draft_path}/publish", json={"revision": 2}
    )
    assert published.status_code == 200, published.text
    assert published.json()["data"]["version"] == 1
    started = await publication_client.post(
        f"{asset_path}/runs", json={"version": 1, "inputs": {"topic": "energy"}}
    )
    assert started.status_code == 200, started.text
    finished = await completed_run(env.service, started.json()["data"]["run_id"])
    assert finished["status"] == "completed"
    assert finished["result_ref"] == finished["tasks"][0]["result_ref"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result_task_id", "graph_case", "expected_code"),
    [
        (None, None, "REFERENCE"),
        ("join", None, "REFERENCE"),
        ("writer", None, "UNREACHABLE"),
        ("output", None, "UNREACHABLE"),
        ("output", "approval", "UNREACHABLE"),
        ("output", "exit", "UNREACHABLE"),
        ("output", "missing-feedback", "UNREACHABLE"),
        ("output", "available-feedback", None),
    ],
)
async def test_asset_publication_requires_reachable_result(
    workflow_env, publication_client, result_task_id, graph_case, expected_code
):
    env = workflow_env
    writer = env.asset["definition"]["tasks"][0]
    definition = {
        **env.asset["definition"],
        "result_task_id": result_task_id,
        "tasks": [
            writer,
            {"id": "join", "name": "Join", "control": {"kind": "join"}},
            {**writer, "id": "active", "name": "Active"},
            {"id": "merge", "name": "Merge", "control": {"kind": "join"}},
            {
                **writer,
                "id": "output",
                "name": "Output",
                "task": "Write from {draft}",
                "inputs": {"draft": {"task_id": "writer", "field": "text"}},
            },
        ],
        "edges": [
            {"source": "join", "target": "writer"},
            {"source": "writer", "target": "merge"},
            {"source": "active", "target": "merge"},
            {"source": "merge", "target": "output"},
        ],
    }
    if graph_case == "approval":
        definition["tasks"][-1].update(task=writer["task"], inputs={})
        definition["tasks"].append(
            {
                "id": "approval",
                "name": "Approval",
                "human": {
                    "kind": "approval",
                    "question": "Approve this draft?",
                    "artifact_task_id": "writer",
                    "artifact_version": 1,
                },
            }
        )
        definition["edges"][-1] = {"source": "merge", "target": "approval"}
        definition["edges"].append(
            {
                "source": "approval",
                "target": "output",
                "condition": {"field": "approved", "value": True},
            }
        )
    elif graph_case:
        exit_task = {**writer, "id": "exit", "name": "Exit"}
        if graph_case == "exit":
            output = definition["tasks"][-1]
            exit_task.update(task=output["task"], inputs=output["inputs"])
            output.update(task=writer["task"], inputs={})
            definition["edges"][-1] = {"source": "merge", "target": "exit"}
            definition["edges"].append({"source": "active", "target": "output"})
            body = ["join", "writer", "active", "merge", "exit"]
            feedback = []
        else:
            definition["edges"].extend(
                [
                    {"source": "output", "target": "exit"},
                    {"source": "active", "target": "exit"},
                ]
            )
            body = ["join", "writer", "active", "merge", "output", "exit"]
            feedback = [
                {
                    "source": "active"
                    if graph_case == "available-feedback"
                    else "writer",
                    "target": "output",
                    "input": "draft",
                }
            ]
        definition["tasks"].append(exit_task)
        definition["loops"] = [
            {
                "id": "review",
                "name": "Review",
                "body": body,
                "exit_task_id": "exit",
                "exit_condition": {"field": "text", "value": "finished"},
                "max_iterations": 2,
                "feedback": feedback,
            }
        ]
    asset_path = f"/api/v1/workflow_assets/{env.asset['asset_id']}"
    published = await publication_client.put(
        asset_path, json={"definition": definition, "expected_version": 1}
    )
    if expected_code is None:
        assert published.status_code == 200, published.text
        assert published.json()["data"]["version"] == 2
        return
    assert published.status_code == 400, published.text
    fields = published.json()["data"]["fields"]
    assert {(field["path"], field["code"]) for field in fields} == {
        ("definition.result_task_id", expected_code)
    }
    original = (await publication_client.get(asset_path)).json()["data"]
    assert original["versions"] == [1]
    assert original["definition"]["edges"] == []


@pytest.mark.asyncio
async def test_incomplete_fixed_version_can_be_read_and_repaired(
    workflow_env, publication_client
):
    env = workflow_env
    asset_id = "incomplete-workflow"
    old = {**env.asset["definition"], "result_task_id": None}
    async with env.db.get_db() as session:
        session.add(
            AgentWorkflowVersion(
                asset_id=asset_id, version=1, owner_username="alice", definition=old
            )
        )
        await session.commit()

    asset_path = f"/api/v1/workflow_assets/{asset_id}"
    env.inventory["profiles"] = []
    old_version = await publication_client.get(asset_path, params={"version": 1})
    assert old_version.status_code == 200, old_version.text
    assert old_version.json()["data"]["definition"] == old
    blocked = await publication_client.get(
        f"{asset_path}/readiness", params={"version": 1}
    )
    assert blocked.status_code == 200, blocked.text
    diagnostic = blocked.json()["data"]
    assert not diagnostic["ready"]
    assert {(issue["path"], issue["code"]) for issue in diagnostic["issues"]} == {
        ("definition.result_task_id", "REFERENCE")
    }
    started = await publication_client.post(f"{asset_path}/runs", json={"version": 1})
    assert started.status_code == 400, started.text
    assert started.json()["data"]["fields"] == diagnostic["issues"]

    fixed = {**old, "result_task_id": "writer"}
    draft = await publication_client.post(
        "/api/v1/agent_drafts/workflow",
        json={"asset_id": asset_id, "body": {"definition": fixed}},
    )
    assert draft.status_code == 200, draft.text
    draft_path = f"/api/v1/agent_drafts/workflow/{asset_id}"
    published = await publication_client.post(
        f"{draft_path}/publish", json={"revision": 1}
    )
    assert published.status_code == 200, published.text
    assert published.json()["data"]["versions"] == [2, 1]
    assert (await publication_client.get(asset_path, params={"version": 1})).json()[
        "data"
    ]["definition"] == old
    readiness = await publication_client.get(
        f"{asset_path}/readiness", params={"version": 2}
    )
    assert readiness.status_code == 200, readiness.text
    diagnostic = readiness.json()["data"]
    assert not diagnostic["ready"]
    assert ("definition.tasks.0.installation.profile_id", "MISSING") in {
        (issue["path"], issue["code"]) for issue in diagnostic["issues"]
    }
    assert all(
        issue["path"] != "definition.result_task_id" for issue in diagnostic["issues"]
    )


@pytest.mark.asyncio
async def test_legacy_unreachable_result_keeps_run_semantics_but_cannot_be_pinned(
    workflow_env,
    publication_client,
):
    env = workflow_env
    asset_id = "unreachable-workflow"
    legacy = {
        **env.asset["definition"],
        "tasks": [
            *env.asset["definition"]["tasks"],
            {"id": "join", "name": "Join", "control": {"kind": "join"}},
        ],
        "edges": [{"source": "join", "target": "writer"}],
    }
    async with env.db.get_db() as session:
        session.add(
            AgentWorkflowVersion(
                asset_id=asset_id, version=1, owner_username="alice", definition=legacy
            )
        )
        await session.commit()

    run = await env.service.start_run("alice", asset_id, {"version": 1})
    finished = await completed_run(env.service, run["run_id"])
    assert finished["status"] == "blocked"
    assert finished["result_ref"] is None

    parent_path = f"/api/v1/workflow_assets/{env.asset['asset_id']}"
    definition = {
        "name": "Parent",
        "result_task_id": "call",
        "tasks": [
            {
                "id": "call",
                "name": "Call",
                "workflow": {"asset_id": asset_id, "version": 1},
            }
        ],
    }
    failed = await publication_client.put(
        parent_path, json={"definition": definition, "expected_version": 1}
    )
    assert failed.status_code == 400, failed.text
    assert {
        (field["path"], field["code"]) for field in failed.json()["data"]["fields"]
    } == {("definition.tasks.0.workflow", "UNREACHABLE")}
    assert (await publication_client.get(parent_path)).json()["data"]["versions"] == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "select_call", [True, False], ids=["public-result", "conditional-branch"]
)
async def test_draft_publication_checks_nested_pinned_public_result(
    workflow_env, publication_client, select_call
):
    env = workflow_env
    grandchild = {
        **env.asset["definition"],
        "tasks": [
            *env.asset["definition"]["tasks"],
            {"id": "join", "name": "Join", "control": {"kind": "join"}},
        ],
        "edges": [{"source": "join", "target": "writer"}],
    }
    child = {
        "name": "Historical child",
        "result_task_id": "call",
        "tasks": [
            {
                "id": "call",
                "name": "Call",
                "workflow": {"asset_id": "historical-grandchild", "version": 1},
            }
        ],
    }
    async with env.db.get_db() as session:
        session.add_all(
            [
                AgentWorkflowVersion(
                    asset_id=asset_id,
                    version=1,
                    owner_username="alice",
                    definition=definition,
                )
                for asset_id, definition in (
                    ("historical-grandchild", grandchild),
                    ("historical-child", child),
                )
            ]
        )
        await session.commit()

    repaired = await publication_client.put(
        "/api/v1/workflow_assets/historical-grandchild",
        json={"definition": env.asset["definition"], "expected_version": 1},
    )
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["data"]["version"] == 2

    parent = {
        "name": "New parent",
        "result_task_id": "call",
        "tasks": [
            {
                "id": "call",
                "name": "Call",
                "workflow": {"asset_id": "historical-child", "version": 1},
            }
        ],
    }
    if not select_call:
        parent["result_task_id"] = "writer"
        parent["tasks"].extend(env.asset["definition"]["tasks"])
        parent["edges"] = [
            {
                "source": "writer",
                "target": "call",
                "condition": {"field": "text", "value": "Follow up"},
            }
        ]
    saved = await publication_client.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": parent}}
    )
    assert saved.status_code == 200, saved.text
    asset_id = saved.json()["data"]["asset_id"]
    draft_path = f"/api/v1/agent_drafts/workflow/{asset_id}"
    asset_path = f"/api/v1/workflow_assets/{asset_id}"
    published = await publication_client.post(
        f"{draft_path}/publish", json={"revision": 1}
    )
    if not select_call:
        assert published.status_code == 200, published.text
        started = await publication_client.post(
            f"{asset_path}/runs", json={"version": 1}
        )
        assert started.status_code == 200, started.text
        finished = await completed_run(env.service, started.json()["data"]["run_id"])
        assert finished["status"] == "completed"
        assert {task["node_id"]: task["status"] for task in finished["tasks"]} == {
            "call": "skipped",
            "writer": "completed",
        }
        return
    assert published.status_code == 400, published.text
    assert {
        (field["path"], field["code"]) for field in published.json()["data"]["fields"]
    } == {("definition.tasks.0.workflow", "UNREACHABLE")}
    assert (await publication_client.get(asset_path)).status_code == 404
    assert (await publication_client.get(draft_path)).json()["data"]["revision"] == 1


@pytest.mark.asyncio
async def test_inline_graph_http_start_and_read_original_result(workflow_env):
    env = workflow_env
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    definition = {
        "name": "Inline HTTP",
        "result_task_id": "agent",
        "default_provider_id": "model-a",
        "required_inputs": ["topic"],
        "tasks": [
            {
                "id": "agent",
                "name": "Writer",
                "member": "writer",
                "role": {"name": "Writer", "prompt": "Write"},
                "installation": {"profile_id": "default"},
                "task": "Write {topic}",
                "inputs": {"topic": {"input": "topic"}},
            }
        ],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        saved = (
            await client.post(
                "/api/v1/workflow_assets", json={"definition": definition}
            )
        ).json()["data"]
        path = f"/api/v1/workflow_assets/{saved['asset_id']}"
        assert (await client.get(path, params={"version": 1})).json()["data"][
            "definition"
        ]["tasks"][0]["role"]["prompt"] == "Write"
        start = await client.post(
            path + "/runs", json={"version": 1, "inputs": {"topic": "energy"}}
        )
        assert start.status_code == 200
        run_id = start.json()["data"]["run_id"]
        finished = await completed_run(env.service, run_id)
        assert finished["status"] == "completed"
        assert (await client.get(f"/api/v1/workflow_runs/{run_id}/result")).json()[
            "data"
        ] == {"text": "Complete report. " * 1000}
        assert finished["result_ref"] == finished["tasks"][0]["result_ref"]


def test_workflow_http_validation_and_ownership(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "api.db"))
    roles = RoleAssetService(db)
    app = FastAPI()
    app.state.services = SimpleNamespace(
        role_assets=roles, workflow_assets=WorkflowService(db, roles)
    )
    app.include_router(roles_router, prefix="/api/v1")
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    with TestClient(app) as client:
        definition = {
            "name": "Report",
            "result_task_id": "writer",
            "default_provider_id": "model-a",
            "required_inputs": ["topic"],
            "tasks": [
                {
                    "id": "writer",
                    "name": "Writer",
                    "member": "writer",
                    "role": {"name": "Writer", "prompt": "Write."},
                    "installation": {"profile_id": "default"},
                    "task": "Read {topic}",
                    "inputs": {"topic": {"input": "topic"}},
                }
            ],
        }
        response = client.post(
            "/api/v1/workflow_assets", json={"definition": definition}
        )
        assert response.status_code == 200
        asset = response.json()["data"]
        assert (
            client.get(f"/api/v1/workflow_assets/{asset['asset_id']}?version=1").json()[
                "data"
            ]
            == asset
        )
        invalid = client.post(
            f"/api/v1/workflow_assets/{asset['asset_id']}/runs", json={"version": 1}
        )
        assert invalid.status_code == 400
        assert invalid.json()["data"]["fields"][0]["path"] == "inputs.topic"
        assert client.get("/api/v1/workflow_runs").json()["data"] == {"runs": []}
        assert (
            client.put(
                f"/api/v1/workflow_assets/{asset['asset_id']}",
                json={"definition": definition, "expected_version": 9},
            ).status_code
            == 409
        )
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        assert (
            client.get(f"/api/v1/workflow_assets/{asset['asset_id']}").status_code
            == 404
        )
        assert client.get("/api/v1/workflow_assets").json()["data"] == {"assets": []}


def test_team_allowed_workflows_http_pin_and_owner_validation(tmp_path):
    """Save explicit workflow permissions and expose field-specific owner errors.

    Args:
        tmp_path: Isolated SQLite used by the real HTTP application service.
    """
    db = SQLiteDatabase(str(tmp_path / "reverse-api.db"))
    roles = RoleAssetService(db)
    app = FastAPI()
    app.state.services = SimpleNamespace(
        role_assets=roles, workflow_assets=WorkflowService(db, roles)
    )
    app.include_router(roles_router, prefix="/api/v1")
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    with TestClient(app) as client:
        role = client.post(
            "/api/v1/agent_roles",
            json={"definition": {"name": "Writer", "prompt": "Write"}},
        ).json()["data"]
        workflow = client.post(
            "/api/v1/workflow_assets",
            json={
                "definition": {
                    "name": "Saved workflow",
                    "result_task_id": "result",
                    "required_inputs": ["topic"],
                    "tasks": [
                        {
                            "id": "result",
                            "name": "Research",
                            "member": "writer",
                            "role_id": role["role_id"],
                            "role_version": 1,
                            "task": "Research {topic}",
                            "inputs": {"topic": {"input": "topic"}},
                        }
                    ],
                }
            },
        ).json()["data"]
        team = {
            "kind": "autonomous",
            "name": "Coordinator",
            "coordinator": {
                "id": "coordinator",
                "role_id": role["role_id"],
                "role_version": 1,
            },
            "allowed_roles": [
                {"id": "writer", "role_id": role["role_id"], "role_version": 1}
            ],
            "allowed_workflows": [
                {
                    "asset_id": workflow["asset_id"],
                    "version": 1,
                }
            ],
        }
        saved = client.post("/api/v1/workflow_assets", json={"definition": team})
        assert saved.status_code == 200
        assert (
            saved.json()["data"]["definition"]["allowed_workflows"]
            == team["allowed_workflows"]
        )
        team["allowed_workflows"][0]["version"] = 2
        missing = client.post("/api/v1/workflow_assets", json={"definition": team})
        assert missing.status_code == 404
        assert (
            missing.json()["data"]["fields"][0]["path"]
            == "definition.allowed_workflows.0"
        )
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        bob_role = client.post(
            "/api/v1/agent_roles",
            json={"definition": {"name": "Bob", "prompt": "Write"}},
        ).json()["data"]
        team["coordinator"]["role_id"] = team["allowed_roles"][0]["role_id"] = bob_role[
            "role_id"
        ]
        team["allowed_workflows"][0]["version"] = 1
        denied = client.post("/api/v1/workflow_assets", json={"definition": team})
        assert denied.status_code == 404
        assert denied.json()["data"]["fields"][0]["code"] == "REFERENCE"
        assert client.get("/api/v1/workflow_assets").json()["data"]["assets"] == []


def test_canvas_graph_http_save_and_field_diagnostics(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "graph-api.db"))
    roles = RoleAssetService(db)
    app = FastAPI()
    app.state.services = SimpleNamespace(
        role_assets=roles, workflow_assets=WorkflowService(db, roles)
    )
    app.include_router(roles_router, prefix="/api/v1")
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    with TestClient(app) as client:
        role = client.post(
            "/api/v1/agent_roles",
            json={"definition": {"name": "Writer", "prompt": "Write."}},
        ).json()["data"]
        task = {
            "id": "first",
            "name": "First",
            "member": "writer",
            "role_id": role["role_id"],
            "role_version": 1,
            "task": "Write",
        }
        definition = {
            "name": "Canvas",
            "result_task_id": "next",
            "tasks": [task, {**task, "id": "next"}],
            "edges": [{"source": "first", "target": "next"}],
        }
        response = client.post(
            "/api/v1/workflow_assets", json={"definition": definition}
        )
        assert response.status_code == 200
        asset = response.json()["data"]
        assert (
            client.get(f"/api/v1/workflow_assets/{asset['asset_id']}").json()["data"][
                "definition"
            ]["tasks"][0]["id"]
            == "first"
        )
        definition["edges"].append({"source": "next", "target": "first"})
        response = client.put(
            f"/api/v1/workflow_assets/{asset['asset_id']}",
            json={"definition": definition, "expected_version": 1},
        )
        assert response.status_code == 400
        assert any(
            field["code"] == "CYCLE" for field in response.json()["data"]["fields"]
        )
        assert (
            client.get(f"/api/v1/workflow_assets/{asset['asset_id']}").json()["data"][
                "version"
            ]
            == 1
        )


def test_human_http_uuid_answers_reconnect_and_owner_permissions(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "human-api.db"))
    service = WorkflowService(db, RoleAssetService(db))
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    with TestClient(app) as client:
        asset = client.post(
            "/api/v1/workflow_assets",
            json={
                "definition": {
                    "name": "Question",
                    "result_task_id": "q",
                    "tasks": [
                        {
                            "id": "q",
                            "name": "Question",
                            "human": {
                                "kind": "question",
                                "question": "Which region?",
                                "choices": ["Asia", "Europe"],
                            },
                        }
                    ],
                }
            },
        ).json()["data"]
        run = client.post(
            f"/api/v1/workflow_assets/{asset['asset_id']}/runs",
            json={"version": 1, "source": "chat:alice"},
        ).json()["data"]
        path = f"/api/v1/workflow_runs/{run['run_id']}"
        todo = run["todos"][0]
        payload = {
            "request_id": "2a6fa610-a005-4c3b-8a38-df62d534a3fe",
            "action": "answer",
            "answer": "Asia",
        }
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        assert client.get(path).status_code == 404
        assert (
            client.post(
                path + f"/todos/{todo['todo_id']}/answer", json=payload
            ).status_code
            == 404
        )
        app.dependency_overrides[require_dashboard_user] = lambda: "alice"
        assert (
            client.post(
                path + f"/todos/{todo['todo_id']}/answer", json=payload
            ).status_code
            == 200
        )
        assert (
            client.post(
                path + f"/todos/{todo['todo_id']}/answer", json=payload
            ).status_code
            == 200
        )
        after = client.get(path).json()["data"]
        assert after["todos"][0]["response"]["answer"] == "Asia"
        events = client.get(path + "/events", params={"after": run["cursor"]}).json()[
            "data"
        ]
        assert events["snapshot"] is None
        assert events["reset"] is False
        assert events["cursor"] >= after["cursor"]
        assert len([e for e in events["events"] if e["kind"] == "human.consumed"]) == 1


@pytest.mark.asyncio
async def test_human_inbox_http_owner_and_answer(workflow_env):
    env = workflow_env
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Question",
                "result_task_id": "q",
                "tasks": [
                    {
                        "id": "q",
                        "name": "Question",
                        "human": {"kind": "question", "question": "Where?"},
                    }
                ],
                "edges": [],
            }
        },
    )
    run = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        inbox = (await client.get("/api/v1/workflow_human_requests")).json()["data"][
            "requests"
        ]
        assert len(inbox) == 1 and inbox[0]["todo_id"] == run["todos"][0]["todo_id"]
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        assert (await client.get("/api/v1/workflow_human_requests")).json()["data"][
            "requests"
        ] == []
        answer_path = (
            f"/api/v1/workflow_runs/{run['run_id']}/todos/{inbox[0]['todo_id']}/answer"
        )
        payload = {"request_id": "answer", "action": "answer", "answer": "Asia"}
        assert (await client.post(answer_path, json=payload)).status_code == 404
        app.dependency_overrides[require_dashboard_user] = lambda: "alice"
        assert (await client.post(answer_path, json=payload)).status_code == 200
        assert (await client.get("/api/v1/workflow_human_requests")).json()["data"][
            "requests"
        ] == []


@pytest.mark.asyncio
async def test_fixed_version_restore_uses_draft_and_head_cas(
    publication_client, workflow_env
):
    """Restore the immutable source, preserving both fixed definitions on conflicts.

    Args:
        publication_client: Owner-authenticated HTTP client with isolated SQLite.
        workflow_env: Database holding the fixed definitions.
    """
    client = publication_client
    definition = {
        "name": "First",
        "port_contract_version": 2,
        "input_contract": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        "entry_task_ids": ["map"],
        "result_task_id": "map",
        "tasks": [
            {
                "id": "map",
                "name": "Map",
                "transform": "map_fields",
                "input_contract": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
                "output_contract": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
                "inputs": {"value": {"input": "value"}},
            }
        ],
        "canvas": {
            "groups": [{"id": "map-group", "title": "Mapping", "task_ids": ["map"]}],
            "notes": [{"id": "example", "text": "First note", "position": {"x": 42.0}}],
        },
    }
    created = await client.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": definition}}
    )
    draft_path = f"/api/v1/agent_drafts/workflow/{created.json()['data']['asset_id']}"
    first = (await client.post(f"{draft_path}/publish", json={"revision": 1})).json()[
        "data"
    ]
    updated = await client.put(
        draft_path,
        json={
            "expected_revision": 1,
            "body": {"definition": {**definition, "name": "Second"}},
        },
    )
    assert updated.status_code == 200, updated.text
    second = await client.post(f"{draft_path}/publish", json={"revision": 2})
    assert second.json()["data"]["version"] == 2
    payload = {"expected_revision": 2, "published_version": 2, "restore_version": 1}
    for stale in ({"expected_revision": 1}, {"published_version": 1}):
        response = await client.post(f"{draft_path}/rebase", json={**payload, **stale})
        assert response.status_code == 409
    current = (await client.get(draft_path)).json()["data"]
    assert current["body"]["definition"]["name"] == "Second"
    restored = await client.post(f"{draft_path}/rebase", json=payload)
    assert restored.status_code == 200, restored.text
    draft = restored.json()["data"]
    assert draft["body"]["definition"] == first["definition"]
    assert draft["base_version"] == 2
    published = await client.post(
        f"{draft_path}/publish", json={"revision": draft["revision"]}
    )
    assert published.json()["data"]["version"] == 3
    assert published.json()["data"]["definition"] == first["definition"]
    old = await client.get(f"/api/v1/workflow_assets/{first['asset_id']}?version=2")
    assert old.json()["data"]["definition"]["name"] == "Second"
    before = (await client.get(draft_path)).json()["data"]
    for shape in (
        {"name": "Old unsupported storage", "obsolete_slots": []},
        ["Old layout"],
    ):
        async with workflow_env.db.get_db() as session:
            damaged = await session.get(AgentWorkflowVersion, (first["asset_id"], 1))
            damaged.definition = shape
            await session.commit()
        rejected = await client.post(
            f"{draft_path}/rebase",
            json={
                "expected_revision": before["revision"],
                "published_version": 3,
                "restore_version": 1,
            },
        )
        assert rejected.status_code == 409
        assert rejected.json()["data"]["fields"][0]["code"] == "UNSUPPORTED_FORMAT"
        assert (await client.get(draft_path)).json()["data"] == before


@pytest.mark.asyncio
async def test_model_proposes_editable_draft_without_saving(
    publication_client, workflow_env, monkeypatch
):
    """Check native bindings and provider selection while leaving the draft list empty.

    Args:
        publication_client: Owner-authenticated HTTP client with isolated SQLite.
        workflow_env: Workflow services backing that client.
        monkeypatch: Temporary replacement for the local provider manager.
    """
    model = MagicMock(spec=Provider)
    model.text_chat = AsyncMock(
        return_value=SimpleNamespace(
            completion_text='{"name":"Research","steps":[{"name":"Read","instructions":"Read."},{"name":"Summarize","instructions":"Keep {labels}."}]}'
        )
    )
    select_model = AsyncMock(return_value=model)
    monkeypatch.setattr(
        workflow_env.roles,
        "core_lifecycle",
        SimpleNamespace(
            provider_manager=SimpleNamespace(get_provider_by_id=select_model)
        ),
    )
    workflow_env.inventory["models"][0]["capabilities"] = ["text", "tool_use"]
    catalog = await workflow_env.roles.get_resources()
    provider_id = catalog["models"][0]["id"]
    path = "/api/v1/workflow_assets/generate_draft"
    request = {"description": "Read and summarize", "provider_id": provider_id}
    response = await publication_client.post(path, json=request)
    assert response.status_code == 200, response.text
    graph = response.json()["data"]
    assert graph["default_provider_id"] == provider_id
    assert graph["tasks"][1]["inputs"]["input"]["task_id"] == "step-1"
    assert graph["tasks"][1]["task"] == "Keep {{labels}}.\n\n{input}"
    assert graph["edges"][0]["source"] == "step-1"
    select_model.assert_awaited_once_with(provider_id)
    assert (await publication_client.get("/api/v1/agent_drafts/workflow")).json()[
        "data"
    ] == []
    invalid = await publication_client.post(
        path, json={**request, "provider_id": "unknown"}
    )
    assert invalid.status_code == 422
    assert model.text_chat.await_count == 1
    workflow_env.inventory["profiles"][0]["provider_pool"] = ["some-other-model"]
    rejected = await publication_client.post(path, json=request)
    assert rejected.status_code == 422
    assert rejected.json()["data"]["fields"][0]["code"] == "FORBIDDEN"
    assert model.text_chat.await_count == 1
    workflow_env.inventory["profiles"][0]["provider_pool"] = [provider_id]
    workflow_env.inventory["models"][0]["capabilities"] = ["text"]
    rejected = await publication_client.post(path, json=request)
    assert rejected.status_code == 422
    assert rejected.json()["data"]["fields"][0]["code"] == "INCOMPATIBLE"
    assert model.text_chat.await_count == 1
    workflow_env.inventory["models"][0]["capabilities"] = ["text", "tool_use"]

    invalid_request = await publication_client.post(path, json={"description": "Read"})
    assert invalid_request.status_code == 422
    assert invalid_request.json()["detail"][0]["loc"] == ["body", "provider_id"]

    select_model.return_value = None
    unavailable = await publication_client.post(path, json=request)
    assert unavailable.status_code == 422
    assert unavailable.json()["data"]["fields"][0]["path"] == "provider_id"

    select_model.return_value = model
    model.text_chat.return_value = SimpleNamespace(completion_text="Invalid JSON")
    invalid_plan = await publication_client.post(path, json=request)
    assert invalid_plan.status_code == 422
    assert invalid_plan.json()["data"]["fields"] == []

    monkeypatch.setattr(workflow_env.roles, "core_lifecycle", None)
    missing = await publication_client.post(path, json=request)
    assert missing.status_code == 422
    assert missing.json()["data"]["fields"][0]["code"] == "MISSING"
    graph["name"] = "Reviewed report"
    draft = (
        await publication_client.post(
            "/api/v1/agent_drafts/workflow", json={"body": {"definition": graph}}
        )
    ).json()["data"]
    asset_path = f"/api/v1/workflow_assets/{draft['asset_id']}"
    tested = await publication_client.post(
        f"{asset_path}/trials",
        json={
            "revision": draft["revision"],
            "mode": "full",
            "kind": "workflow",
            "inputs": {"input": "A passage"},
        },
    )
    assert tested.status_code == 200, tested.text
    published = await publication_client.post(
        f"/api/v1/agent_drafts/workflow/{draft['asset_id']}/publish",
        json={"revision": draft["revision"]},
    )
    assert published.status_code == 200, published.text
    started = await publication_client.post(
        f"{asset_path}/runs", json={"version": 1, "inputs": {"input": "A passage"}}
    )
    assert started.status_code == 200, started.text
    for response in (tested, started):
        run = await completed_run(
            workflow_env.service, response.json()["data"]["run_id"]
        )
        assert run["status"] == "completed", run
        assert run["snapshot"]["definition"]["name"] == "Reviewed report"
        assert run["tasks"][1]["snapshot"]["inputs"]["input"].startswith(
            "Complete report."
        )
