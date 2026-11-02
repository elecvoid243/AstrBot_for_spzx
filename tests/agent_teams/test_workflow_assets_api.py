"""Ownership, input diagnostics and pinned workflow versions over HTTP."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.role_assets import router as roles_router
from astrbot.dashboard.api.workflow_assets import router
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_inline_graph_http_start_and_read_original_result(workflow_env):
    env = workflow_env
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    definition = {
        "name": "Inline HTTP",
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
            "coordinator": {"role_id": role["role_id"], "role_version": 1},
            "allowed_roles": [{"role_id": role["role_id"], "role_version": 1}],
            "allowed_workflows": [
                {
                    "asset_id": workflow["asset_id"],
                    "version": 1,
                    "result_task_id": "result",
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
        assert events["snapshot"]["todos"] == after["todos"]
        assert len([e for e in events["events"] if e["kind"] == "human.consumed"]) == 1
