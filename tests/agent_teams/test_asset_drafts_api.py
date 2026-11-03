"""Persistent draft behavior through authenticated asset endpoints."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.role_assets import router
from astrbot.dashboard.api.workflow_assets import router as workflow_router


def test_draft_revision_publication_and_owner(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "drafts.db"))
    roles = RoleAssetService(db)
    app = FastAPI()
    app.state.services = SimpleNamespace(
        role_assets=roles, workflow_assets=WorkflowService(db, roles)
    )
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/agent_drafts/role",
                json={"body": {"definition": {"model_slot": "legacy"}}},
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/v1/agent_drafts/workflow",
                json={"body": {"definition": {"tasks": [{"model_slot": "legacy"}]}}},
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/v1/agent_drafts/autonomous",
                json={
                    "body": {
                        "definition": {
                            "coordinator": {"installation": {"bindings": {}}}
                        }
                    }
                },
            ).status_code
            == 400
        )
        body = {"definition": {"name": ""}}
        draft = client.post("/api/v1/agent_drafts/role", json={"body": body}).json()[
            "data"
        ]
        path = f"/api/v1/agent_drafts/role/{draft['asset_id']}"
        assert (
            client.get("/api/v1/agent_drafts/role").json()["data"][0]["asset_id"]
            == draft["asset_id"]
        )
        assert client.post(f"{path}/publish", json={"revision": 1}).status_code == 400
        assert client.get(path).json()["data"]["revision"] == 1
        body["definition"]["name"] = "First"
        assert (
            client.put(path, json={"body": body, "expected_revision": 1}).json()[
                "data"
            ]["revision"]
            == 2
        )
        assert (
            client.put(path, json={"body": body, "expected_revision": 1}).status_code
            == 409
        )
        first = client.post(f"{path}/publish", json={"revision": 2}).json()["data"]
        assert first["version"] == 1
        assert (
            client.post(f"{path}/publish", json={"revision": 2}).json()["data"] == first
        )
        body["definition"]["name"] = "Second"
        assert (
            client.put(path, json={"body": body, "expected_revision": 2}).status_code
            == 200
        )
        second = client.post(f"{path}/publish", json={"revision": 3}).json()["data"]
        assert second["version"] == 2
        assert (
            client.post(f"{path}/publish", json={"revision": 2}).json()["data"][
                "version"
            ]
            == 1
        )
        assert (
            client.get(f"/api/v1/agent_roles/{draft['asset_id']}?version=1").json()[
                "data"
            ]["definition"]["name"]
            == "First"
        )
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        assert client.get(path).status_code == 404
        assert client.post(f"{path}/publish", json={"revision": 3}).status_code == 409
        assert (
            client.put(path, json={"body": body, "expected_revision": 3}).status_code
            == 409
        )
        assert client.get("/api/v1/agent_drafts/role").json()["data"] == []


def test_workflow_and_team_drafts_publish_pinned_versions(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "assets.db"))
    roles = RoleAssetService(db)
    app = FastAPI()
    app.state.services = SimpleNamespace(
        role_assets=roles, workflow_assets=WorkflowService(db, roles)
    )
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    app.include_router(workflow_router, prefix="/api/v1")
    with TestClient(app) as client:
        role = client.post(
            "/api/v1/agent_roles", json={"definition": {"name": "Writer"}}
        ).json()["data"]
        role_ref = {"role_id": role["role_id"], "role_version": 1}
        definitions = {
            "workflow": {
                "name": "Flow",
                "required_inputs": ["topic"],
                "tasks": [
                    {
                        "id": "writer",
                        "name": "Writer",
                        "member": "writer",
                        **role_ref,
                        "task": "Research {topic}",
                        "inputs": {"topic": {"input": "topic"}},
                    }
                ],
            },
            "autonomous": {
                "kind": "autonomous",
                "name": "Team",
                "coordinator": {"id": "lead", **role_ref},
                "allowed_roles": [{"id": "member", **role_ref}],
            },
        }
        for kind, definition in definitions.items():
            created = client.post(
                f"/api/v1/agent_drafts/{kind}",
                json={"body": {"definition": definition}},
            )
            assert created.status_code == 200
            draft = created.json()["data"]
            path = f"/api/v1/agent_drafts/{kind}/{draft['asset_id']}"
            result = client.post(f"{path}/publish", json={"revision": 1})
            assert result.status_code == 200, result.text
            first = result.json()["data"]
            assert first["version"] == 1
            assert (
                client.post(f"{path}/publish", json={"revision": 1}).json()["data"]
                == first
            )
            definition["name"] += " changed"
            assert (
                client.put(
                    path,
                    json={"body": {"definition": definition}, "expected_revision": 1},
                ).status_code
                == 200
            )
            assert (
                client.post(f"{path}/publish", json={"revision": 2}).json()["data"][
                    "version"
                ]
                == 2
            )
            assert first["definition"]["name"] != definition["name"]
            assert (
                client.put(
                    f"/api/v1/workflow_assets/{draft['asset_id']}",
                    json={"definition": definition, "expected_version": 2},
                ).status_code
                == 200
            )
            rebase = client.post(
                f"{path}/rebase", json={"expected_revision": 2, "published_version": 3}
            )
            assert rebase.status_code == 200, rebase.text
            assert (
                rebase.json()["data"]["body"]["definition"]["name"]
                == definition["name"]
            )
            assert (
                client.post(f"{path}/publish", json={"revision": 3}).json()["data"][
                    "version"
                ]
                == 4
            )


@pytest.mark.asyncio
async def test_concurrent_draft_updates_allow_only_one_revision(tmp_path):
    import asyncio

    db = SQLiteDatabase(str(tmp_path / "race.db"))
    service = RoleAssetService(db)
    draft = await service.create_draft("alice", "role", {"definition": {"name": ""}})
    results = await asyncio.gather(
        service.update_draft(
            "alice", "role", draft["asset_id"], 1, {"definition": {"name": "A"}}
        ),
        service.update_draft(
            "alice", "role", draft["asset_id"], 1, {"definition": {"name": "B"}}
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, Exception) for result in results) == 1
    assert (await service.get_draft("alice", "role", draft["asset_id"]))[
        "revision"
    ] == 2
    await db.engine.dispose()
    reopened = SQLiteDatabase(str(tmp_path / "race.db"))
    assert (
        await RoleAssetService(reopened).get_draft("alice", "role", draft["asset_id"])
    )["revision"] == 2
    await reopened.engine.dispose()


def test_failed_version_insert_rolls_back_draft_publication(tmp_path):
    from sqlalchemy import event

    db = SQLiteDatabase(str(tmp_path / "rollback.db"))
    roles = RoleAssetService(db)
    app = FastAPI()
    app.state.services = SimpleNamespace(
        role_assets=roles, workflow_assets=WorkflowService(db, roles)
    )
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    with TestClient(app, raise_server_exceptions=False) as client:
        draft = client.post(
            "/api/v1/agent_drafts/role", json={"body": {"definition": {"name": "Role"}}}
        ).json()["data"]
        path = f"/api/v1/agent_drafts/role/{draft['asset_id']}"

        def fail_insert(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lower().startswith("insert into agent_role_versions"):
                raise RuntimeError("simulated database failure")

        event.listen(db.engine.sync_engine, "before_cursor_execute", fail_insert)
        try:
            assert (
                client.post(f"{path}/publish", json={"revision": 1}).status_code == 500
            )
        finally:
            event.remove(db.engine.sync_engine, "before_cursor_execute", fail_insert)
        saved = client.get(path).json()["data"]
        assert saved["revision"] == 1
        assert saved["base_version"] is None
        assert saved["published_revision"] is None
        assert client.get("/api/v1/agent_roles").json()["data"]["roles"] == []
        assert (
            client.post(f"{path}/publish", json={"revision": 1}).json()["data"][
                "version"
            ]
            == 1
        )


def test_stale_published_base_keeps_the_draft(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "base.db"))
    roles = RoleAssetService(db)
    app = FastAPI()
    app.state.services = SimpleNamespace(
        role_assets=roles, workflow_assets=WorkflowService(db, roles)
    )
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    with TestClient(app) as client:
        role = client.post(
            "/api/v1/agent_roles", json={"definition": {"name": "First"}}
        ).json()["data"]
        role_id = role["role_id"]
        draft = client.post(
            "/api/v1/agent_drafts/role",
            json={"asset_id": role_id, "body": {"definition": {"name": "Draft"}}},
        ).json()["data"]
        assert (
            client.put(
                f"/api/v1/agent_roles/{role_id}",
                json={"definition": {"name": "Elsewhere"}, "expected_version": 1},
            ).status_code
            == 200
        )
        path = f"/api/v1/agent_drafts/role/{role_id}"
        assert (
            client.post(
                f"{path}/publish", json={"revision": draft["revision"]}
            ).status_code
            == 409
        )
        unchanged = client.get(path).json()["data"]
        assert unchanged["base_version"] == 1
        assert unchanged["body"]["definition"]["name"] == "Draft"
        assert unchanged["published_version"] is None
        # Reviewing a stale version or changing the draft during review never rebases it.
        assert (
            client.post(
                f"{path}/rebase", json={"expected_revision": 1, "published_version": 1}
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"{path}/rebase", json={"expected_revision": 2, "published_version": 2}
            ).status_code
            == 409
        )
        assert client.get(path).json()["data"] == unchanged
        rebased = client.post(
            f"{path}/rebase", json={"expected_revision": 1, "published_version": 2}
        ).json()["data"]
        assert rebased["revision"] == 2
        assert rebased["base_version"] == 2
        assert rebased["body"] == unchanged["body"]
        assert (
            client.put(
                f"/api/v1/agent_roles/{role_id}",
                json={"definition": {"name": "Later"}, "expected_version": 2},
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"{path}/rebase", json={"expected_revision": 2, "published_version": 2}
            ).status_code
            == 409
        )
        rebased = client.post(
            f"{path}/rebase", json={"expected_revision": 2, "published_version": 3}
        ).json()["data"]
        assert (
            client.post(
                f"{path}/publish", json={"revision": rebased["revision"]}
            ).json()["data"]["version"]
            == 4
        )
