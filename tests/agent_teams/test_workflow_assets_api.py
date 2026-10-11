"""Ownership, input diagnostics and pinned workflow versions over HTTP."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.role_assets import router as roles_router
from astrbot.dashboard.api.workflow_assets import router


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
        role = client.post(
            "/api/v1/agent_roles",
            json={"definition": {"name": "Writer", "prompt": "Write."}},
        ).json()["data"]
        definition = {
            "name": "Report",
            "role_id": role["role_id"],
            "role_version": 1,
            "task": "Read {topic}",
            "required_inputs": ["topic"],
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
