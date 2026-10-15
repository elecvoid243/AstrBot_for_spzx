"""Portable workflow behavior at the shared asset and run service boundary."""

import asyncio
import json
import copy

import pytest

from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env
from astrbot.core.agent_teams.assets import RoleAssetService, RoleAssetError
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase


@pytest.mark.asyncio
async def test_export_includes_definition_but_excludes_local_installation(workflow_env):
    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    assert package["format_version"] == 1
    assert package["asset_id"] == env.asset["asset_id"]
    assert package["version"] == 1
    assert package["roles"][0]["definition"]["prompt"] == "Original instructions"
    assert package["definition"]["task"] == "Write a report"
    serialized = json.dumps(package)
    for private in (
        "model-a",
        "profile_id",
        "bindings",
        "session_id",
        "conversation_id",
    ):
        assert private not in serialized
    assert (await env.service.list_runs("alice"))["runs"] == []


@pytest.mark.asyncio
async def test_export_makes_inherited_persona_prompt_visible_without_knowledge_content(
    workflow_env,
):
    env = workflow_env
    env.inventory["personas"] = [
        {"id": "private-persona", "prompt": "Actual persona instructions."}
    ]
    env.inventory["tools"] = [{"id": "private-tool"}]
    env.inventory["knowledge_bases"] = [
        {
            "id": "private-kb",
            "name": "Private evidence",
            "content": "DO_NOT_EXPORT_KNOWLEDGE",
        }
    ]
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Persona writer",
                "persona_slot": "persona",
                "tools": ["evidence"],
                "knowledge_bases": ["facts"],
            },
            "installation": {
                "bindings": {
                    "model": "model-a",
                    "persona": "private-persona",
                    "evidence": "private-tool",
                    "facts": "private-kb",
                }
            },
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Persona report",
                "role_id": role["role_id"],
                "role_version": 1,
                "task": "Use evidence",
            }
        },
    )
    package = await env.service.export_asset("alice", asset["asset_id"], 1)
    exported = package["roles"][0]
    assert exported["definition"]["prompt"] == "Actual persona instructions."
    assert exported["prompt_source"] == "persona"
    assert exported["definition"]["tools"] == ["tools_1"]
    assert exported["definition"]["knowledge_bases"] == ["knowledge_bases_1"]
    assert {dependency["kind"] for dependency in exported["dependencies"]} == {
        "profiles",
        "models",
        "tools",
        "knowledge_bases",
    }
    for private in [
        "private-persona",
        "private-tool",
        "private-kb",
        "DO_NOT_EXPORT_KNOWLEDGE",
    ]:
        assert private not in json.dumps(package)


@pytest.mark.asyncio
async def test_different_instance_previews_unique_compatible_resources_before_install(
    workflow_env, tmp_path
):
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "same-adapter-model-source"
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    catalog = copy.deepcopy(env.inventory)
    catalog["profiles"][0]["id"] = "target-profile"
    catalog["models"][0]["id"] = "different-model-id"

    async def inventory():
        return catalog

    (tmp_path / "instance-b").mkdir()
    db = SQLiteDatabase(str(tmp_path / "instance-b" / "workflow.db"))
    service = WorkflowService(
        db,
        RoleAssetService(db, catalog_loader=inventory),
        ports_factory=env.service.ports_factory,
    )
    try:
        preview = await service.preview_import("bob", {"package": package})
        assert preview["ready"] is True
        binding = preview["roles"][0]["installation"]
        assert binding == {
            "profile_id": "target-profile",
            "bindings": {"model": "different-model-id"},
        }
        assert (await service.list_assets("bob"))["assets"] == []
        assert (await service.roles.list_roles("bob"))["roles"] == []
        installed = await service.import_asset("bob", {"package": package})
        assert installed["existing"] is False
        run = await service.start_run(
            "bob", installed["asset"]["asset_id"], {"version": 1}
        )
        for _ in range(200):
            current = await service.get_run("bob", run["run_id"])
            if current["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert current["status"] == "completed"
        assert current["snapshot"]["effective"]["provider_id"] == "different-model-id"
        assert (await service.get_result("bob", run["run_id"]))[
            "text"
        ] == "Complete report. " * 1000
        duplicate = await service.import_asset("bob", {"package": package})
        assert duplicate["existing"] is True
        assert duplicate["asset"] == installed["asset"]
        assert len((await service.list_assets("bob"))["assets"]) == 1
        assert len((await service.roles.list_roles("bob"))["roles"]) == 1
    finally:
        await service.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_pending_import_can_be_configured_without_duplicate_assets(workflow_env):
    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    preview = await env.service.preview_import("alice", {"package": package})
    assert preview["ready"] is False
    assert preview["roles"][0]["dependencies"][1]["status"] == "confirmation_required"
    installed = await env.service.import_asset(
        "alice", {"package": package, "save_pending": True}
    )
    asset_id = installed["asset"]["asset_id"]
    with pytest.raises(RoleAssetError):
        await env.service.start_run("alice", asset_id, {"version": 1})
    assert (await env.service.list_runs("alice"))["runs"] == []
    key = preview["roles"][0]["key"]
    configured = await env.service.import_asset(
        "alice",
        {
            "package": package,
            "installations": {
                key: {"profile_id": "default", "bindings": {"model": "model-a"}}
            },
        },
    )
    assert configured["existing"] is True
    assert configured["asset"]["asset_id"] == asset_id
    assert (await env.service.check_readiness("alice", asset_id, 1))["ready"] is True
    assert (await env.service.preview_import("alice", {"package": package}))[
        "ready"
    ] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["version", "reference", "contract", "extra", "dependency", "inheritance"]
)
async def test_invalid_packages_create_neither_assets_nor_roles(workflow_env, change):
    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    if change == "version":
        package["format_version"] = 999
    elif change == "reference":
        package["definition"]["role_version"] = 42
    elif change == "contract":
        package["definition"]["output_contract"] = {
            "type": "object",
            "$ref": "https://example.invalid/schema",
        }
    elif change == "extra":
        package["script"] = "install-plugin"
    elif change == "dependency":
        package["roles"][0]["dependencies"] = []
    else:
        package["roles"][0]["definition"]["tools"] = None
    before_assets = await env.service.list_assets("alice")
    before_roles = await env.roles.list_roles("alice")
    with pytest.raises(RoleAssetError):
        await env.service.import_asset(
            "alice", {"package": package, "save_pending": True}
        )
    assert await env.service.list_assets("alice") == before_assets
    assert await env.roles.list_roles("alice") == before_roles
    assert (await env.service.list_runs("alice"))["runs"] == []


@pytest.mark.asyncio
async def test_same_name_is_not_equivalent_and_multiple_compatible_models_are_ambiguous(
    workflow_env,
):
    env = workflow_env
    env.inventory["models"][0].update(name="Shared name", fingerprint="source-one")
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    env.inventory["models"] = [
        {"id": "wrong", "name": "Shared name", "fingerprint": "source-two"}
    ]
    preview = await env.service.preview_import("alice", {"package": package})
    assert preview["roles"][0]["dependencies"][1]["status"] == "missing"
    env.inventory["models"] = [
        {"id": "b", "name": "Different label", "fingerprint": "source-one"},
        {"id": "c", "name": "Third label", "fingerprint": "source-one"},
    ]
    preview = await env.service.preview_import("alice", {"package": package})
    dep = preview["roles"][0]["dependencies"][1]
    assert dep["status"] == "ambiguous"
    assert dep["selected"] is None
    assert {candidate["id"] for candidate in dep["candidates"]} == {"b", "c"}
    with pytest.raises(RoleAssetError):
        await env.service.import_asset("alice", {"package": package})
    assert len((await env.service.list_assets("alice"))["assets"]) == 1


@pytest.mark.asyncio
async def test_same_identity_different_content_never_overwrites_installed_definition(
    workflow_env,
):
    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    installed = await env.service.import_asset(
        "alice", {"package": package, "save_pending": True}
    )
    changed = copy.deepcopy(package)
    changed["definition"]["task"] = "Changed responsibility"
    with pytest.raises(RoleAssetError) as conflict:
        await env.service.import_asset(
            "alice", {"package": changed, "save_pending": True}
        )
    assert conflict.value.status == 409
    assert (
        await env.service.get_asset("alice", installed["asset"]["asset_id"], 1)
        == installed["asset"]
    )
    assert (
        await env.service.export_asset("alice", installed["asset"]["asset_id"], 1)
        == package
    )


@pytest.mark.asyncio
async def test_confirmation_failure_leaves_no_partial_installation_and_retry_succeeds(
    workflow_env,
):
    """Fail the storage commit, then observe recovery only through public assets.

    Args:
        workflow_env: Isolated services and controllable external inventory.
    """
    from sqlalchemy import event

    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    before_assets = await env.service.list_assets("alice")
    before_roles = await env.roles.list_roles("alice")

    def unavailable_commit(connection):
        raise OSError("Storage unavailable at commit")

    event.listen(env.db.engine.sync_engine, "commit", unavailable_commit)
    try:
        with pytest.raises(OSError, match="Storage unavailable"):
            await env.service.import_asset(
                "alice", {"package": package, "save_pending": True}
            )
    finally:
        event.remove(env.db.engine.sync_engine, "commit", unavailable_commit)
    assert await env.service.list_assets("alice") == before_assets
    assert await env.roles.list_roles("alice") == before_roles
    assert (await env.service.list_runs("alice"))["runs"] == []
    installed = await env.service.import_asset(
        "alice", {"package": package, "save_pending": True}
    )
    assert installed["existing"] is False


@pytest.mark.asyncio
async def test_package_http_routes_share_ownership_and_validation(workflow_env):
    from types import SimpleNamespace

    import httpx
    from fastapi import FastAPI

    from astrbot.dashboard.api.auth import require_dashboard_user
    from astrbot.dashboard.api.workflow_assets import router

    env = workflow_env
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        exported = await client.get(
            f"/api/v1/workflow_assets/{env.asset['asset_id']}/export?version=1"
        )
        assert exported.status_code == 200
        package = exported.json()["data"]
        preview = await client.post(
            "/api/v1/workflow_assets/import/preview", json={"package": package}
        )
        assert preview.status_code == 200
        assert preview.json()["data"]["ready"] is False
        imported = await client.post(
            "/api/v1/workflow_assets/import",
            json={"package": package, "save_pending": True},
        )
        assert imported.status_code == 200
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        assert (
            await client.get(
                f"/api/v1/workflow_assets/{imported.json()['data']['asset']['asset_id']}/export"
            )
        ).status_code == 404
