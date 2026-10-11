"""HTTP integration for the public role asset service."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.role_assets import router


def test_role_versions_field_errors_and_ownership(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "roles.db"))
    app = FastAPI()
    app.state.services = SimpleNamespace(role_assets=RoleAssetService(db))
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent_roles", json={"definition": {"name": "", "max_steps": True}}
        )
        assert response.status_code == 400
        assert {field["path"] for field in response.json()["data"]["fields"]} == {
            "definition.name",
            "definition.max_steps",
        }
        role = client.post(
            "/api/v1/agent_roles",
            json={"definition": {"name": "Researcher", "prompt": "Check evidence."}},
        ).json()["data"]
        role_id = role["role_id"]
        assert (
            client.get(f"/api/v1/agent_roles/{role_id}?version=1").json()["data"]
            == role
        )
        assert (
            client.put(
                f"/api/v1/agent_roles/{role_id}",
                json={"definition": {"name": "New"}, "expected_version": 9},
            ).status_code
            == 409
        )
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        assert client.get(f"/api/v1/agent_roles/{role_id}").status_code == 404
        assert client.get("/api/v1/agent_roles").json()["data"] == {"roles": []}
