"""Asset owners manage exact local platform grants over authenticated HTTP."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.role_assets import router as roles_router
from astrbot.dashboard.api.workflow_assets import router


def test_http_owner_opens_and_revokes_fixed_version_without_granting_dashboard_identity(
    tmp_path,
):
    """Use the real HTTP service for owner authorization and grant persistence.

    Args:
        tmp_path: Isolated real database directory.
    """
    db = SQLiteDatabase(str(tmp_path / "chat-api.db"))
    roles = RoleAssetService(db)
    service = WorkflowService(db, roles)
    app = FastAPI()
    app.state.services = SimpleNamespace(role_assets=roles, workflow_assets=service)
    app.include_router(roles_router, prefix="/api/v1")
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "operator"
    with TestClient(app) as client:
        role = client.post(
            "/api/v1/agent_roles",
            json={"definition": {"name": "Writer", "prompt": "Write"}},
        ).json()["data"]
        asset = client.post(
            "/api/v1/workflow_assets",
            json={
                "definition": {
                    "name": "Report",
                    "role_id": role["role_id"],
                    "role_version": 1,
                    "task": "Write",
                }
            },
        ).json()["data"]
        url = f"/api/v1/workflow_assets/{asset['asset_id']}/chat_access"
        grant = {
            "version": 1,
            "platform_id": "qq-main",
            "sender_id": "12345",
            "source": "qq-main:FriendMessage:12345",
            "profile_id": "default",
        }
        assert client.put(url, json=grant).status_code == 200
        assert client.get(url).json()["data"] == {"grants": [grant]}
        assert (
            client.put(
                url, json={k: v for k, v in grant.items() if k != "source"}
            ).status_code
            == 422
        )
        assert client.get(url).json()["data"] == {"grants": [grant]}
        assert (
            client.put(url, json={**grant, "owner_username": "other"}).status_code
            == 422
        )
        assert client.put(url, json={**grant, "version": 9}).status_code == 404
        app.dependency_overrides[require_dashboard_user] = lambda: (
            "platform:qq-main:12345"
        )
        assert client.get(url).status_code == 404
        assert client.put(url, json=grant).status_code == 404
        assert client.delete(url + "/qq-main/12345").status_code == 404
        assert client.get("/api/v1/workflow_runs").json()["data"] == {"runs": []}
        app.dependency_overrides[require_dashboard_user] = lambda: "operator"
        assert client.delete(url + "/qq-main/12345").status_code == 200
        assert client.get(url).json()["data"] == {"grants": []}
