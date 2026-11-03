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


def test_direct_role_preview_and_legacy_field_diagnostics(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "roles.db"))

    async def catalog():
        return {
            "profiles": [{"id": "default", "persona_id": "default-persona"}],
            "models": [{"id": "m"}],
            "personas": [{"id": "p", "prompt": "Persona"}],
            "tools": [],
            "skills": [],
            "knowledge_bases": [{"id": "kb", "name": "Displayed"}],
        }

    app = FastAPI()
    app.state.services = SimpleNamespace(
        role_assets=RoleAssetService(db, catalog_loader=catalog)
    )
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    with TestClient(app) as client:
        payload = {
            "definition": {
                "name": "Role",
                "prompt": "Duty",
                "provider_id": "m",
                "persona_id": "p",
                "tools": [],
                "knowledge_bases": ["kb"],
            }
        }
        preview = client.post("/api/v1/agent_roles/preview", json=payload).json()[
            "data"
        ]
        assert preview["ready"]
        assert preview["effective"]["prompt"] == "Duty"
        assert preview["effective"]["persona_prompt"] == "Persona"
        role = client.post("/api/v1/agent_roles", json=payload).json()["data"]
        assert (
            client.get(f"/api/v1/agent_roles/{role['role_id']}").json()["data"] == role
        )
        payload["definition"]["model_slot"] = "old"
        response = client.post("/api/v1/agent_roles/preview", json=payload)
        assert response.status_code == 400
        assert response.json()["data"]["fields"][0]["path"] == "definition.model_slot"
        payload["definition"].pop("model_slot")
        payload["definition"]["knowledge_bases"] = ["Displayed"]
        preview = client.post("/api/v1/agent_roles/preview", json=payload).json()[
            "data"
        ]
        assert preview["issues"][0]["path"] == "definition.knowledge_bases.0"


def test_resolve_pinned_role_http_keeps_overrides_and_checks_owner(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "resolve.db"))

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "m"}],
            "personas": [{"id": "p", "prompt": "Persona"}],
            "tools": [{"id": "search", "name": "Search"}],
            "skills": [],
            "knowledge_bases": [],
        }

    app = FastAPI()
    app.state.services = SimpleNamespace(
        role_assets=RoleAssetService(db, catalog_loader=catalog)
    )
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    with TestClient(app) as client:
        definition = {
            "name": "Writer",
            "prompt": "First",
            "provider_id": "m",
            "persona_id": "p",
            "tools": ["search"],
        }
        role_id = client.post(
            "/api/v1/agent_roles", json={"definition": definition}
        ).json()["data"]["role_id"]
        definition["prompt"] = "Second"
        assert (
            client.put(
                f"/api/v1/agent_roles/{role_id}",
                json={"definition": definition, "expected_version": 1},
            ).status_code
            == 200
        )
        response = client.post(
            f"/api/v1/agent_roles/{role_id}/resolve?version=1",
            json={"persona_id": None, "tools": []},
        )
        assert response.status_code == 200
        preview = response.json()["data"]
        assert preview["effective"]["prompt"] == "First"
        assert preview["effective"]["persona_id"] is None
        assert preview["effective"]["tools"] == []
        assert preview["sources"]["persona_id"] == "task"
        assert preview["sources"]["prompt"] == "role"
        assert (
            client.post(
                f"/api/v1/agent_roles/{role_id}/resolve?version=999", json={}
            ).status_code
            == 404
        )
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        assert (
            client.post(
                f"/api/v1/agent_roles/{role_id}/resolve?version=1", json={}
            ).status_code
            == 404
        )
