"""Portable workflow behavior at the shared asset and run service boundary."""

import asyncio
import copy
import json

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_export_contains_direct_ids_but_no_credentials_or_sessions(workflow_env):
    """Export source identities while excluding credentials and run state.

    Args:
        workflow_env: Isolated public asset services.
    """
    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    assert package["format_version"] == 3
    assert package["roles"][0]["definition"]["provider_id"] == "model-a"
    assert package["roles"][0]["installation"]["profile_id"] == "default"
    assert package["definition"]["tasks"][0]["task"] == "Write a report"
    for forbidden in ("bindings", "logical_slots", "session_id", "conversation_id"):
        assert forbidden not in json.dumps(package)
    assert (await env.service.list_runs("alice"))["runs"] == []


@pytest.mark.asyncio
async def test_export_retains_persona_identity_without_knowledge_or_persona_content(
    workflow_env,
):
    """Explicit resource identities do not include private source contents.

    Args:
        workflow_env: Isolated public asset services.
    """
    env = workflow_env
    env.inventory["personas"] = [{"id": "source-persona", "prompt": "PRIVATE_PERSONA"}]
    env.inventory["tools"] = [{"id": "source-tool"}]
    env.inventory["knowledge_bases"] = [
        {"id": "source-kb", "name": "Evidence", "content": "PRIVATE_KNOWLEDGE"}
    ]
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Writer",
                "prompt": "Write",
                "provider_id": "model-a",
                "persona_id": "source-persona",
                "tools": ["source-tool"],
                "knowledge_bases": ["source-kb"],
            },
            "installation": {"profile_id": "default"},
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Report",
                "result_task_id": "writer",
                "tasks": [
                    {
                        "id": "writer",
                        "name": "Writer",
                        "member": "writer",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "Write",
                    }
                ],
            }
        },
    )
    package = await env.service.export_asset("alice", asset["asset_id"], 1)
    assert package["roles"][0]["definition"]["persona_id"] == "source-persona"
    assert {item["kind"] for item in package["requirements"]} == {
        "profiles",
        "models",
        "personas",
        "tools",
        "knowledge_bases",
    }
    assert "PRIVATE_PERSONA" not in json.dumps(package)
    assert "PRIVATE_KNOWLEDGE" not in json.dumps(package)


@pytest.mark.asyncio
async def test_export_keeps_task_overrides_separate_from_pinned_role(workflow_env):
    """Task responsibilities remain explicit and do not modify template content.

    Args:
        workflow_env: Isolated public asset services.
    """
    env = workflow_env
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {"name": "Writer", "provider_id": "model-a"},
            "installation": {"profile_id": "default"},
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Report",
                "result_task_id": "writer",
                "tasks": [
                    {
                        "id": "writer",
                        "name": "Writer",
                        "member": "writer",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "Write",
                        "overrides": {"prompt": "Task responsibility"},
                    }
                ],
            }
        },
    )
    package = await env.service.export_asset("alice", asset["asset_id"], 1)
    assert package["roles"][0]["definition"]["prompt"] is None
    assert package["definition"]["tasks"][0]["overrides"] == {
        "prompt": "Task responsibility"
    }


@pytest.mark.asyncio
async def test_requirements_reject_incompatible_tool_contracts(workflow_env):
    """Tool contracts follow source IDs and disable incompatible local candidates.

    Args:
        workflow_env: Isolated public asset services.
    """
    env = workflow_env
    schema = {"type": "object", "properties": {"company": {"type": "string"}}}
    env.inventory["tools"] = [
        {"id": "source-tool", "description": "Search filings", "input_schema": schema}
    ]
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Writer",
                "prompt": "Write",
                "provider_id": "model-a",
                "tools": ["source-tool"],
            },
            "installation": {"profile_id": "default"},
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Report",
                "result_task_id": "writer",
                "tasks": [
                    {
                        "id": "writer",
                        "name": "Writer",
                        "member": "writer",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "Write",
                    }
                ],
            }
        },
    )
    package = await env.service.export_asset("alice", asset["asset_id"], 1)
    requirement = next(
        item for item in package["requirements"] if item["kind"] == "tools"
    )
    assert (
        requirement["source_id"] == "source-tool"
        and requirement["input_schema"] == schema
    )
    env.inventory["tools"] = [{"id": "weather", "input_schema": {"type": "string"}}]
    preview = await env.service.preview_import("bob", {"package": package})
    candidate = next(
        item for item in preview["requirements"] if item["kind"] == "tools"
    )["candidates"][0]
    assert candidate["disabled"] and candidate["reasons"]


@pytest.mark.asyncio
async def test_export_preserves_inherited_knowledge_without_expanding_source_defaults(
    workflow_env,
):
    """Implicit profile defaults remain implicit and are not exported requirements.

    Args:
        workflow_env: Isolated public asset services.
    """
    env = workflow_env
    env.inventory["profiles"][0]["knowledge_bases"] = ["Corporate evidence"]
    env.inventory["knowledge_bases"] = [
        {"id": "source-kb", "name": "Corporate evidence"}
    ]
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    assert package["roles"][0]["definition"]["knowledge_bases"] is None
    assert not any(
        item["kind"] == "knowledge_bases" for item in package["requirements"]
    )


@pytest.mark.asyncio
async def test_different_instance_requires_explicit_selection_before_install(
    workflow_env, tmp_path
):
    """Explicitly select compatible resources, then execute through normal ports.

    Args:
        workflow_env: Source public services and controlled execution ports.
        tmp_path: Separate target database directory.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "same-source"
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    catalog = copy.deepcopy(env.inventory)
    catalog["profiles"][0]["id"] = "target-profile"
    catalog["models"][0]["id"] = "target-model"

    async def inventory():
        return catalog

    db = SQLiteDatabase(str(tmp_path / "target.db"))
    service = WorkflowService(
        db,
        RoleAssetService(db, catalog_loader=inventory),
        ports_factory=env.service.ports_factory,
    )
    try:
        assert not (await service.preview_import("bob", {"package": package}))["ready"]
        choices = [
            {"kind": "profiles", "source_id": "default", "target_id": "target-profile"},
            {"kind": "models", "source_id": "model-a", "target_id": "target-model"},
        ]
        payload = {"package": package, "selections": choices}
        assert (await service.preview_import("bob", payload))["ready"]
        installed = await service.import_asset("bob", payload)
        run = await service.start_run(
            "bob", installed["asset"]["asset_id"], {"version": 1}
        )
        for _ in range(200):
            current = await service.get_run("bob", run["run_id"])
            if current["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert current["status"] == "completed", current
        member = installed["asset"]["definition"]["tasks"][0]
        local_role = await service.roles.get_role(
            "bob", member["role_id"], member["role_version"]
        )
        assert local_role["definition"]["provider_id"] == "target-model"
        assert (await service.import_asset("bob", payload))["existing"]
    finally:
        await service.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_unselected_resources_cannot_create_pending_assets(workflow_env):
    """Missing selections never install an incomplete asset.

    Args:
        workflow_env: Isolated public asset services.
    """
    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    assert not (await env.service.preview_import("bob", {"package": package}))["ready"]
    with pytest.raises(RoleAssetError):
        await env.service.import_asset("bob", {"package": package})
    assert (await env.service.list_assets("bob"))["assets"] == []
    with pytest.raises(RoleAssetError):
        await env.service.import_asset(
            "bob", {"package": package, "save_pending": True}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["version", "reference", "contract", "extra", "dependency", "legacy_slot"]
)
async def test_invalid_packages_create_neither_assets_nor_roles(workflow_env, change):
    """Reject malformed packages before writing any target assets.

    Args:
        workflow_env: Isolated public services.
        change: Invalid package mutation under test.
    """
    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    selections = [
        {"kind": r["kind"], "source_id": r["source_id"], "target_id": r["source_id"]}
        for r in package["requirements"]
    ]
    if change == "version":
        package["format_version"] = 2
    elif change == "reference":
        package["definition"]["tasks"][0]["role_version"] = 42
    elif change == "contract":
        package["definition"]["tasks"][0]["output_contract"] = {
            "type": "object",
            "$ref": "https://example.invalid/schema",
        }
    elif change == "extra":
        package["script"] = "install-plugin"
    elif change == "dependency":
        package["requirements"] = []
    else:
        package["requirements"][0]["slot"] = "old-slot"
    with pytest.raises(RoleAssetError):
        await env.service.import_asset(
            "bob", {"package": package, "selections": selections}
        )
    assert (await env.service.list_assets("bob"))["assets"] == []
    assert (await env.roles.list_roles("bob"))["roles"] == []
    assert (await env.service.list_runs("bob"))["runs"] == []


@pytest.mark.asyncio
async def test_same_name_and_fingerprint_do_not_automatically_select_resources(
    workflow_env,
):
    """An explicit choice is required even for a single compatible candidate.

    Args:
        workflow_env: Isolated public services and mutable resource inventory.
    """
    env = workflow_env
    env.inventory["models"][0].update(name="Shared name", fingerprint="source-one")
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    env.inventory["models"] = [
        {"id": "wrong", "name": "Shared name", "fingerprint": "source-two"}
    ]
    preview = await env.service.preview_import("bob", {"package": package})
    model = next(r for r in preview["requirements"] if r["kind"] == "models")
    assert model["selected"] is None and model["candidates"][0]["warnings"]
    assert not model["candidates"][0]["disabled"]
    env.inventory["models"] = [
        {"id": "b", "fingerprint": "source-one"},
        {"id": "c", "fingerprint": "source-one"},
    ]
    preview = await env.service.preview_import("bob", {"package": package})
    model = next(r for r in preview["requirements"] if r["kind"] == "models")
    assert model["selected"] is None and all(
        not r["disabled"] for r in model["candidates"]
    )
    with pytest.raises(RoleAssetError):
        await env.service.import_asset("bob", {"package": package})
    assert (await env.service.list_assets("bob"))["assets"] == []


@pytest.mark.asyncio
async def test_same_identity_different_content_never_overwrites_installed_definition(
    workflow_env,
):
    """A fixed imported version rejects changes to source content.

    Args:
        workflow_env: Isolated public services.
    """
    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    selections = [
        {"kind": r["kind"], "source_id": r["source_id"], "target_id": r["source_id"]}
        for r in package["requirements"]
    ]
    installed = await env.service.import_asset(
        "bob", {"package": package, "selections": selections}
    )
    changed = copy.deepcopy(package)
    changed["definition"]["tasks"][0]["task"] = "Changed responsibility"
    with pytest.raises(RoleAssetError) as conflict:
        await env.service.import_asset(
            "bob", {"package": changed, "selections": selections}
        )
    assert conflict.value.status == 409
    assert (
        await env.service.get_asset("bob", installed["asset"]["asset_id"], 1)
        == installed["asset"]
    )


@pytest.mark.asyncio
async def test_confirmation_failure_leaves_no_partial_installation_and_retry_succeeds(
    workflow_env,
):
    """A failed storage commit leaves no partial assets or roles.

    Args:
        workflow_env: Isolated public services and mutable resource inventory.
    """
    from sqlalchemy import event

    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    selections = [
        {"kind": r["kind"], "source_id": r["source_id"], "target_id": r["source_id"]}
        for r in package["requirements"]
    ]
    payload = {"package": package, "selections": selections}

    def unavailable_commit(connection):
        raise OSError("Storage unavailable at commit")

    event.listen(env.db.engine.sync_engine, "commit", unavailable_commit)
    try:
        with pytest.raises(OSError, match="Storage unavailable"):
            await env.service.import_asset("bob", payload)
    finally:
        event.remove(env.db.engine.sync_engine, "commit", unavailable_commit)
    assert (await env.service.list_assets("bob"))["assets"] == []
    assert (await env.roles.list_roles("bob"))["roles"] == []
    assert not (await env.service.import_asset("bob", payload))["existing"]


@pytest.mark.asyncio
async def test_imported_direct_resources_survive_service_restart(
    workflow_env, tmp_path
):
    """Restart resolves fixed local definitions without source receipt aliases.

    Args:
        workflow_env: Public services and mutable resource inventory.
        tmp_path: Persistent source and target database directory.
    """
    env = workflow_env
    env.inventory["tools"] = [{"id": "tool-a", "description": "Evidence"}]
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Writer",
                "prompt": "Write",
                "provider_id": "model-a",
                "tools": ["tool-a"],
            },
            "installation": {"profile_id": "default"},
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Report",
                "result_task_id": "writer",
                "tasks": [
                    {
                        "id": "writer",
                        "name": "Writer",
                        "member": "writer",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "Write",
                        "overrides": {"tools": []},
                    }
                ],
            }
        },
    )
    package = await env.service.export_asset("alice", asset["asset_id"], 1)
    selections = [
        {"kind": r["kind"], "source_id": r["source_id"], "target_id": r["source_id"]}
        for r in package["requirements"]
    ]
    installed = await env.service.import_asset(
        "bob", {"package": package, "selections": selections}
    )
    from sqlalchemy import select

    from astrbot.core.db.po import AgentWorkflowPackage

    async with env.db.get_db() as session:
        receipts = (
            (
                await session.execute(
                    select(AgentWorkflowPackage).where(
                        AgentWorkflowPackage.owner_username == "bob"
                    )
                )
            )
            .scalars()
            .all()
        )
        for receipt in receipts:
            await session.delete(receipt)
        await session.commit()
    await env.service.shutdown()
    await env.db.engine.dispose()
    env.inventory["tools"] = []

    async def inventory():
        return env.inventory

    db = SQLiteDatabase(str(tmp_path / "workflows.db"))
    service = WorkflowService(
        db,
        RoleAssetService(db, catalog_loader=inventory),
        ports_factory=env.service.ports_factory,
    )
    try:
        assert (
            await service.check_readiness("bob", installed["asset"]["asset_id"], 1)
        )["ready"]
        assert (
            await service.get_asset("bob", installed["asset"]["asset_id"], 1)
            == installed["asset"]
        )
    finally:
        await service.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_runtime_uses_local_resources_without_source_fingerprint_recheck(
    workflow_env,
):
    """Local availability is checked without resolving the source receipt.

    Args:
        workflow_env: Isolated public services and mutable resource inventory.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "source-fingerprint"
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    selections = [
        {"kind": r["kind"], "source_id": r["source_id"], "target_id": r["source_id"]}
        for r in package["requirements"]
    ]
    installed = await env.service.import_asset(
        "bob", {"package": package, "selections": selections}
    )
    identity = installed["asset"]["asset_id"]
    env.inventory["models"][0]["fingerprint"] = "different-local-source"
    assert (await env.service.check_readiness("bob", identity, 1))["ready"]
    env.inventory["models"][0]["enabled"] = False
    readiness = await env.service.check_readiness("bob", identity, 1)
    assert not readiness["ready"]
    with pytest.raises(RoleAssetError):
        await env.service.start_run("bob", identity, {"version": 1})
    assert (await env.service.list_runs("bob"))["runs"] == []


@pytest.mark.asyncio
async def test_package_http_reports_invalid_contract_before_resolving_graph_fields(
    workflow_env,
):
    """Reject the malformed predecessor schema before its successor reads a field.

    Args:
        workflow_env: Public services used by the authenticated HTTP routes.
    """
    from types import SimpleNamespace

    import httpx
    from fastapi import FastAPI

    from astrbot.dashboard.api.auth import require_dashboard_user
    from astrbot.dashboard.api.workflow_assets import router

    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    task = {
        "id": "first",
        "name": "First",
        "member": "first",
        "role_id": env.role["role_id"],
        "role_version": 1,
        "task": "Write report",
    }
    package["definition"] = {
        "name": "Malformed contract",
        "tasks": [
            {**task, "output_contract": {"type": "object", "allOf": 1}},
            {
                **task,
                "id": "second",
                "member": "second",
                "inputs": {"evidence": {"task_id": "first", "field": "text"}},
            },
        ],
        "edges": [{"source": "first", "target": "second"}],
    }
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "bob"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        for route in ("import/preview", "import"):
            response = await client.post(
                f"/api/v1/workflow_assets/{route}",
                json={"package": package},
            )
            assert response.status_code in {400, 422}, response.text
            assert "definition.tasks.0.output_contract" in json.dumps(response.json())
    assert (await env.service.list_assets("bob"))["assets"] == []
    assert (await env.roles.list_roles("bob"))["roles"] == []
    assert (await env.service.list_runs("bob"))["runs"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("format_version", [True, 3.0])
async def test_package_http_rejects_noninteger_format_without_installation(
    workflow_env, format_version
):
    """Keep original JSON types significant at the typed HTTP boundary.

    Args:
        workflow_env: Public services used by the authenticated HTTP routes.
        format_version: A boolean or float that must not be accepted as an integer version.
    """
    from types import SimpleNamespace

    import httpx
    from fastapi import FastAPI

    from astrbot.dashboard.api.auth import require_dashboard_user
    from astrbot.dashboard.api.workflow_assets import router

    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "same-source"
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    package["format_version"] = format_version
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "bob"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        for route in ("import/preview", "import"):
            response = await client.post(
                f"/api/v1/workflow_assets/{route}",
                json={"package": package},
            )
            assert response.status_code in {400, 422}, response.text
    assert (await env.service.list_assets("bob"))["assets"] == []
    assert (await env.roles.list_roles("bob"))["roles"] == []
    assert (await env.service.list_runs("bob"))["runs"] == []


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
        selections = [
            {
                "kind": r["kind"],
                "source_id": r["source_id"],
                "target_id": r["source_id"],
            }
            for r in package["requirements"]
        ]
        imported = await client.post(
            "/api/v1/workflow_assets/import",
            json={"package": package, "selections": selections},
        )
        assert imported.status_code == 200
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        assert (
            await client.get(
                f"/api/v1/workflow_assets/{imported.json()['data']['asset']['asset_id']}/export"
            )
        ).status_code == 404
