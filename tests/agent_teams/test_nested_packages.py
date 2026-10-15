"""Complete nested packages through the shared asset service boundary."""

import asyncio
import copy

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.workflow_assets import get_workflow_service, router
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest_asyncio.fixture
async def nested_package(workflow_env):
    """Build a team with a separately versioned role in its workflow child.

    Args:
        workflow_env: Real public services and resource inventory.

    Returns:
        Service environment and the complete portable package.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "portable-model"
    leaf_role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Leaf researcher",
                "prompt": "Read complete evidence",
            },
            "installation": {"bindings": {"model": "model-a"}},
        },
    )
    leaf = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Leaf evidence",
                "role_id": leaf_role["role_id"],
                "role_version": 1,
                "task": "Research evidence",
            }
        },
    )
    role = {"role_id": env.role["role_id"], "role_version": 1}
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Nested evidence",
                "coordinator": role,
                "allowed_roles": [role],
                "allowed_workflows": [
                    {
                        "asset_id": leaf["asset_id"],
                        "version": 1,
                        "result_task_id": "result",
                    }
                ],
            }
        },
    )
    return env, await env.service.export_asset("alice", team["asset_id"], 1)


@pytest.mark.asyncio
async def test_export_pins_complete_mixed_nested_closure(workflow_env):
    """Include the exact workflow called by a team inside an outer loop.

    Args:
        workflow_env: Public services backed by an isolated SQLite database.
    """
    env = workflow_env
    role = {"role_id": env.role["role_id"], "role_version": 1}
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Evidence team",
                "coordinator": role,
                "allowed_roles": [role],
                "allowed_workflows": [
                    {
                        "asset_id": env.asset["asset_id"],
                        "version": 1,
                        "result_task_id": "result",
                    }
                ],
            }
        },
    )
    graph = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Mixed report",
                "required_inputs": ["goal"],
                "tasks": [
                    {
                        "id": "evidence",
                        "name": "Collect evidence",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                        "position": {"x": 80.0, "y": 60.0},
                    }
                ],
                "loops": [
                    {
                        "id": "review",
                        "name": "Bounded review",
                        "body": ["evidence"],
                        "exit_task_id": "evidence",
                        "max_iterations": 2,
                        "exit_condition": {"field": "text", "value": "Approved"},
                    }
                ],
            }
        },
    )
    changed = copy.deepcopy(env.asset["definition"])
    changed["task"] = "New instructions excluded from pinned closure"
    await env.service.save_asset(
        "alice", {"definition": changed, "expected_version": 1}, env.asset["asset_id"]
    )
    package = await env.service.export_asset("alice", graph["asset_id"], 1)
    assert package["format_version"] == 2
    included = {
        (item["asset_id"], item["version"]): item["definition"]
        for item in package["assets"]
    }
    assert set(included) == {(team["asset_id"], 1), (env.asset["asset_id"], 1)}
    assert included[(env.asset["asset_id"], 1)]["task"] == "Write a report"
    assert package["definition"]["loops"][0]["max_iterations"] == 2
    assert package["definition"]["tasks"][0]["position"] == {"x": 80.0, "y": 60.0}
    assert [(item["role_id"], item["version"]) for item in package["roles"]] == [
        (env.role["role_id"], 1)
    ]


@pytest.mark.asyncio
async def test_import_installs_and_reuses_every_pinned_definition(workflow_env):
    """Import a team and its workflow without source machine references.

    Args:
        workflow_env: Public services and a mutable resource catalog.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "portable-model"
    role = {"role_id": env.role["role_id"], "role_version": 1}
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Research team",
                "coordinator": role,
                "allowed_roles": [role],
                "allowed_workflows": [
                    {
                        "asset_id": env.asset["asset_id"],
                        "version": 1,
                        "result_task_id": "result",
                    }
                ],
            }
        },
    )
    package = await env.service.export_asset("alice", team["asset_id"], 1)
    preview = await env.service.preview_import("bob", {"package": package})
    assert preview["ready"] is True
    assert len(preview["assets"]) == 2
    installed = await env.service.import_asset("bob", {"package": package})
    reference = installed["asset"]["definition"]["allowed_workflows"][0]
    assert reference["asset_id"] != env.asset["asset_id"]
    child = await env.service.get_asset(
        "bob", reference["asset_id"], reference["version"]
    )
    assert child["definition"]["task"] == "Write a report"
    assert child["definition"]["role_id"] != env.role["role_id"]
    assert (
        await env.service.check_readiness("bob", installed["asset"]["asset_id"], 1)
    )["ready"]
    again = await env.service.import_asset("bob", {"package": package})
    assert again["existing"] is True
    assert again["asset"] == installed["asset"]
    assert len((await env.service.list_assets("bob"))["assets"]) == 2
    assert len((await env.roles.list_roles("bob"))["roles"]) == 2
    assert (
        child["definition"]["role_id"]
        != installed["asset"]["definition"]["coordinator"]["role_id"]
    )
    assert (
        await env.service.export_asset("bob", installed["asset"]["asset_id"], 1)
        == package
    )


@pytest.mark.asyncio
async def test_new_local_role_version_keeps_imported_compatibility_requirements(
    workflow_env,
):
    """A role edit cannot discard the imported resource compatibility contract.

    Args:
        workflow_env: Public services and mutable local compatibility metadata.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "original-source"
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    installed = await env.service.import_asset("bob", {"package": package})
    definition = copy.deepcopy(installed["asset"]["definition"])
    role = await env.roles.get_role("bob", definition["role_id"], 1)
    await env.roles.save_role(
        "bob",
        {
            "definition": {**role["definition"], "prompt": "Edited responsibility"},
            "installation": role["installation"],
            "expected_version": 1,
        },
        role["role_id"],
    )
    definition["role_version"] = 2
    changed = await env.service.save_asset(
        "bob",
        {
            "definition": definition,
            "expected_version": 1,
        },
        installed["asset"]["asset_id"],
    )
    env.inventory["models"][0]["fingerprint"] = "incompatible-source"
    readiness = await env.service.check_readiness("bob", changed["asset_id"], 2)
    assert readiness["ready"] is False
    with pytest.raises(RoleAssetError):
        await env.service.start_run("bob", changed["asset_id"], {"version": 2})
    assert (await env.service.list_runs("bob"))["runs"] == []


@pytest.mark.asyncio
async def test_package_loop_cannot_exceed_included_role_iteration_limit(workflow_env):
    """Reject impossible loop limits before saving even a pending package.

    Args:
        workflow_env: Public services with no imported definitions yet.
    """
    env = workflow_env
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Review",
                "tasks": [
                    {
                        "id": "write",
                        "name": "Write",
                        "member": "writer",
                        "role_id": env.role["role_id"],
                        "role_version": 1,
                        "task": "Write",
                        "overrides": {"max_iterations": 1},
                    }
                ],
                "loops": [
                    {
                        "id": "review",
                        "name": "Review",
                        "body": ["write"],
                        "exit_task_id": "write",
                        "max_iterations": 1,
                        "exit_condition": {"field": "text", "value": "Approved"},
                    }
                ],
            }
        },
    )
    package = await env.service.export_asset("alice", asset["asset_id"], 1)
    package["definition"]["loops"][0]["max_iterations"] = 2
    with pytest.raises(RoleAssetError, match="iteration"):
        await env.service.import_asset(
            "bob", {"package": package, "save_pending": True}
        )
    assert (await env.service.list_assets("bob"))["assets"] == []
    assert (await env.roles.list_roles("bob"))["roles"] == []


@pytest.mark.asyncio
async def test_upgrade_appends_version_and_preserves_existing_result(workflow_env):
    """Keep the old definition and artifact while selecting the upgraded version.

    Args:
        workflow_env: Public asset and execution services.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "original-source"
    original = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    installed = await env.service.import_asset("bob", {"package": original})
    run = await env.service.start_run(
        "bob", installed["asset"]["asset_id"], {"version": 1}
    )
    for _ in range(200):
        before = await env.service.get_run("bob", run["run_id"])
        if before["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert before["status"] == "completed"
    result = await env.service.get_result("bob", run["run_id"])
    changed = await env.service.save_asset(
        "alice",
        {
            "definition": {**env.asset["definition"], "task": "Write upgraded report"},
            "expected_version": 1,
        },
        env.asset["asset_id"],
    )
    package = await env.service.export_asset("alice", changed["asset_id"], 2)
    preview = await env.service.preview_import("bob", {"package": package})
    assert preview["assets"][0]["action"] == "new_version"
    upgrade = await env.service.import_asset("bob", {"package": package})
    assert upgrade["asset"]["asset_id"] == installed["asset"]["asset_id"]
    assert upgrade["asset"]["version"] == 2
    assert upgrade["asset"]["versions"] == [2, 1]
    assert (await env.service.get_asset("bob", installed["asset"]["asset_id"], 1))[
        "definition"
    ] == installed["asset"]["definition"]
    assert (await env.service.get_run("bob", run["run_id"]))["snapshot"] == before[
        "snapshot"
    ]
    assert await env.service.get_result("bob", run["run_id"]) == result
    assert (await env.service.import_asset("bob", {"package": package}))[
        "existing"
    ] is True
    assert (
        await env.service.export_asset("bob", installed["asset"]["asset_id"], 1)
        == original
    )
    assert (
        await env.service.export_asset("bob", installed["asset"]["asset_id"], 2)
        == package
    )
    new_run = await env.service.start_run(
        "bob", installed["asset"]["asset_id"], {"version": 2}
    )
    assert new_run["snapshot"]["definition"]["task"] == "Write upgraded report"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "contract",
        "cycle",
        "depth",
        "return",
        "script",
        "provenance",
        "task_prompt",
    ],
)
async def test_invalid_nested_package_has_no_partial_installation(
    nested_package, change
):
    """Validate the whole reference graph before persisting any definition.

    Args:
        nested_package: Complete team and workflow package.
        change: Invalid reference, contract, provenance or unsupported script field.
    """
    env, package = nested_package
    if change == "missing":
        package["assets"] = []
    elif change == "contract":
        package["assets"][0]["definition"]["output_contract"] = {
            "$ref": "https://invalid.test/schema"
        }
    elif change == "cycle":
        package["assets"][0]["definition"] = copy.deepcopy(package["definition"])
        package["assets"][0]["definition"]["allowed_workflows"][0]["asset_id"] = (
            package["asset_id"]
        )
    elif change == "depth":
        leaf = copy.deepcopy(package["assets"][0])
        team = {
            "asset_id": "middle",
            "version": 1,
            "definition": copy.deepcopy(package["definition"]),
        }
        graph = {
            "asset_id": "graph",
            "version": 1,
            "definition": {
                "name": "Middle graph",
                "required_inputs": ["goal"],
                "tasks": [
                    {
                        "id": "team",
                        "name": "Team",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                    }
                ],
            },
        }
        package["assets"] = [leaf, team, graph]
        package["definition"]["max_depth"] = 1
        package["definition"]["allowed_workflows"] = [
            {"asset_id": "graph", "version": 1, "result_task_id": "team"}
        ]
    elif change == "return":
        package["definition"]["allowed_workflows"][0]["result_task_id"] = "absent"
    elif change == "provenance":
        package["roles"][0]["dependencies"][0]["uses"] = {"absent:1": []}
    elif change == "task_prompt":
        coordinator = package["definition"]["coordinator"]
        role = next(
            role
            for role in package["roles"]
            if role["role_id"] == coordinator["role_id"]
        )
        role["prompt_source"] = "task"
        coordinator["overrides"]["prompt"] = None
    else:
        package["assets"][0]["install_script"] = "install-plugin"
    with pytest.raises(RoleAssetError):
        await env.service.import_asset(
            "bob", {"package": package, "save_pending": True}
        )
    assert (await env.service.list_assets("bob"))["assets"] == []
    assert (await env.roles.list_roles("bob"))["roles"] == []


@pytest.mark.asyncio
async def test_deep_missing_dependencies_are_grouped_and_block_every_entry(
    nested_package,
):
    """Expose a leaf dependency and retain a consistently pending installation.

    Args:
        nested_package: Complete package with a separate leaf role.
    """
    env, package = nested_package
    leaf_id = package["assets"][0]["definition"]["role_id"]
    role = next(role for role in package["roles"] if role["role_id"] == leaf_id)
    next(dep for dep in role["dependencies"] if dep["kind"] == "models")[
        "fingerprint"
    ] = "unavailable-model"
    preview = await env.service.preview_import("bob", {"package": package})
    assert preview["ready"] is False
    assert any(
        leaf_id in issue["path"] and "model" in issue["path"]
        for issue in preview["issues"]
    )
    installed = await env.service.import_asset(
        "bob", {"package": package, "save_pending": True}
    )
    assert installed["ready"] is False
    for asset in (await env.service.list_assets("bob"))["assets"]:
        assert (
            await env.service.check_readiness(
                "bob", asset["asset_id"], asset["version"]
            )
        )["ready"] is False
    assert (await env.service.list_runs("bob"))["runs"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("upgrade", [False, True])
async def test_actual_storage_failure_rolls_back_whole_nested_install(
    nested_package, upgrade
):
    """A transaction failure leaves no isolated roles or executable children.

    Args:
        nested_package: Valid package ready to install with local resources.
        upgrade: Whether an existing entry version must also remain unchanged.
    """
    env, package = nested_package
    if upgrade:
        await env.service.import_asset("bob", {"package": package})
        package["version"] = 2
        package["definition"]["name"] = "Upgraded nested evidence"
    before_assets = await env.service.list_assets("bob")
    before_roles = await env.roles.list_roles("bob")

    def fail_save(conn, cursor, statement, parameters, context, executemany):
        """Inject a real SQL execution failure at the persistence boundary."""
        if statement.lower().startswith("insert into agentworkflowpackage"):
            raise RuntimeError("Controlled package storage failure")

    event.listen(env.db.engine.sync_engine, "before_cursor_execute", fail_save)
    try:
        with pytest.raises(RuntimeError, match="Controlled package storage failure"):
            await env.service.import_asset("bob", {"package": package})
    finally:
        event.remove(env.db.engine.sync_engine, "before_cursor_execute", fail_save)
    assert await env.service.list_assets("bob") == before_assets
    assert await env.roles.list_roles("bob") == before_roles
    installed = await env.service.import_asset("bob", {"package": package})
    assert installed["ready"] is True
    assert len((await env.service.list_assets("bob"))["assets"]) == 2


@pytest.mark.asyncio
async def test_two_pinned_child_versions_share_identity_without_retargeting(
    nested_package,
):
    """Two fixed child versions remain independently selectable after import.

    Args:
        nested_package: A team and one pinned workflow version.
    """
    env, package = nested_package
    newer = copy.deepcopy(package["assets"][0])
    newer["version"] = 2
    newer["definition"]["task"] = "Upgraded leaf instructions"
    package["assets"].append(newer)
    package["definition"]["allowed_workflows"].append(
        {
            "asset_id": newer["asset_id"],
            "version": 2,
            "result_task_id": "result",
        }
    )
    installed = await env.service.import_asset("bob", {"package": package})
    first, second = installed["asset"]["definition"]["allowed_workflows"]
    assert first["asset_id"] == second["asset_id"]
    assert (first["version"], second["version"]) == (1, 2)
    assert (await env.service.get_asset("bob", first["asset_id"], 1))["definition"][
        "task"
    ] == "Research evidence"
    assert (await env.service.get_asset("bob", second["asset_id"], 2))["definition"][
        "task"
    ] == "Upgraded leaf instructions"


@pytest.mark.asyncio
async def test_child_identity_conflict_prevents_parent_upgrade(nested_package):
    """A new parent cannot silently overwrite an existing child version.

    Args:
        nested_package: Complete nested package and public services.
    """
    env, package = nested_package
    installed = await env.service.import_asset("bob", {"package": package})
    before_assets = await env.service.list_assets("bob")
    before_roles = await env.roles.list_roles("bob")
    package["version"] = 2
    package["assets"][0]["definition"]["task"] = "Conflicting child content"
    with pytest.raises(RoleAssetError) as conflict:
        await env.service.import_asset("bob", {"package": package})
    assert conflict.value.status == 409
    assert await env.service.list_assets("bob") == before_assets
    assert await env.roles.list_roles("bob") == before_roles
    assert (
        await env.service.get_asset("bob", installed["asset"]["asset_id"], 1)
        == installed["asset"]
    )


@pytest.mark.asyncio
async def test_nested_package_http_validates_whole_closure_and_reports_conflict(
    nested_package,
):
    """The existing HTTP entry uses the same complete installation boundary.

    Args:
        nested_package: Actual public services and a portable nested package.
    """
    env, package = nested_package
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "bob"
    app.dependency_overrides[get_workflow_service] = lambda: env.service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        invalid = copy.deepcopy(package)
        invalid["assets"][0]["definition"]["role_version"] = 99
        rejected = await client.post(
            "/api/v1/workflow_assets/import", json={"package": invalid}
        )
        assert rejected.status_code == 400
        assert "role" in rejected.text
        assert (await env.service.list_assets("bob"))["assets"] == []
        preview = await client.post(
            "/api/v1/workflow_assets/import/preview", json={"package": package}
        )
        assert preview.status_code == 200
        assert len(preview.json()["data"]["assets"]) == 2
        installed = await client.post(
            "/api/v1/workflow_assets/import", json={"package": package}
        )
        assert installed.status_code == 200
        package["definition"]["name"] = "Conflicting identity"
        conflict = await client.post(
            "/api/v1/workflow_assets/import", json={"package": package}
        )
        assert conflict.status_code == 409
        assert "different content" in conflict.text


@pytest.mark.asyncio
async def test_unrelated_integrity_failure_is_not_treated_as_concurrent_confirmation(
    nested_package,
):
    """Retry only when another confirmation actually changed the registry.

    Args:
        nested_package: A ready nested package and persistent public services.
    """
    env, package = nested_package
    injected = False

    def fail_once(conn, cursor, statement, parameters, context, executemany):
        """Inject one storage constraint error without a competing confirmation."""
        nonlocal injected
        if not injected and statement.lower().startswith(
            "insert into agentworkflowpackage"
        ):
            injected = True
            raise IntegrityError(
                statement, parameters, RuntimeError("Controlled constraint failure")
            )

    event.listen(env.db.engine.sync_engine, "before_cursor_execute", fail_once)
    try:
        with pytest.raises(RoleAssetError, match="conflicted"):
            await env.service.import_asset("bob", {"package": package})
    finally:
        event.remove(env.db.engine.sync_engine, "before_cursor_execute", fail_once)
    assert (await env.service.list_assets("bob"))["assets"] == []
    assert (await env.roles.list_roles("bob"))["roles"] == []
    assert (await env.service.import_asset("bob", {"package": package}))["ready"]


@pytest.mark.asyncio
async def test_nested_coordinator_requires_team_tools_even_if_package_omits_capability(
    nested_package,
):
    """Intrinsic coordinator requirements participate in the whole-package preview.

    Args:
        nested_package: A valid team closure with distinct coordinator and leaf roles.
    """
    env, package = nested_package
    identity = package["definition"]["coordinator"]["role_id"]
    role = next(role for role in package["roles"] if role["role_id"] == identity)
    role["definition"]["runner_capabilities"] = []
    for field in ("max_steps", "tool_call_timeout", "context_length"):
        role["definition"][field] = None
        package["definition"]["coordinator"]["overrides"][field] = None
        for allowed in package["definition"]["allowed_roles"]:
            allowed["overrides"][field] = None
    profile = next(dep for dep in role["dependencies"] if dep["kind"] == "profiles")
    profile.update(runner_type="dify", fingerprint=None, capabilities=[])
    role["dependencies"] = [profile]
    env.inventory["profiles"].append(
        {
            "id": "remote",
            "runner_type": "dify",
            "runner_config": {"dify_api_type": "workflow"},
        }
    )
    payload = {
        "package": package,
        "installations": {f"{identity}:1": {"profile_id": "remote", "bindings": {}}},
    }
    preview = await env.service.preview_import("bob", payload)
    assert preview["ready"] is False
    assert any(identity in issue["path"] for issue in preview["issues"])
    with pytest.raises(RoleAssetError):
        await env.service.import_asset("bob", payload)
    assert (await env.service.list_assets("bob"))["assets"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing", "forbidden"])
async def test_shared_role_child_exports_independently_and_keeps_unused_requirements(
    workflow_env, change
):
    """Explicit unused role requirements survive an independent child transfer.

    Args:
        workflow_env: Public services backed by SQLite and an external resource catalog.
        change: Missing resource or revoked profile authorization after installation.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "portable-model"
    env.inventory["tools"] = [{"id": "local-search", "description": "Search evidence"}]
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Shared role",
                "prompt": "Read evidence",
                "tools": [],
            },
            "installation": {
                "bindings": {"model": "model-a", "search": "local-search"}
            },
        },
    )
    reference = {"role_id": role["role_id"], "role_version": 1}
    leaf = await env.service.save_asset(
        "alice", {"definition": {"name": "Leaf", "task": "Read evidence", **reference}}
    )
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Team",
                "coordinator": reference,
                "allowed_roles": [reference],
                "allowed_workflows": [
                    {
                        "asset_id": leaf["asset_id"],
                        "version": 1,
                        "result_task_id": "result",
                    }
                ],
            }
        },
    )
    root = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Parent",
                "required_inputs": ["goal"],
                "tasks": [
                    {
                        "id": "search",
                        "name": "Search",
                        "member": "searcher",
                        "task": "Search evidence",
                        **reference,
                        "overrides": {"tools": ["search"]},
                    },
                    {
                        "id": "team",
                        "name": "Team",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                    },
                ],
            }
        },
    )
    package = await env.service.export_asset("alice", root["asset_id"], 1)
    installations = {
        f"{role['role_id']}:1": {
            "profile_id": "default",
            "bindings": {"model": "model-a", "tools_1": "local-search"},
        }
    }
    installed = await env.service.import_asset(
        "bob", {"package": package, "installations": installations}
    )
    child_ref = installed["asset"]["definition"]["tasks"][1]["team"]
    local_team = await env.service.get_asset(
        "bob", child_ref["asset_id"], child_ref["version"]
    )
    leaf_ref = local_team["definition"]["allowed_workflows"][0]
    child = await env.service.export_asset(
        "bob", leaf_ref["asset_id"], leaf_ref["version"]
    )
    if not any(dep["kind"] == "tools" for dep in child["roles"][0]["dependencies"]):
        declared = copy.deepcopy(
            next(
                dep
                for dep in package["roles"][0]["dependencies"]
                if dep["kind"] == "tools"
            )
        )
        declared.pop("uses", None)
        child["roles"][0]["dependencies"].append(declared)
    payload = {"package": child, "installations": installations}
    assert (await env.service.preview_import("carol", payload))["ready"]
    transferred = await env.service.import_asset("carol", payload)
    assert child["definition"]["overrides"]["tools"] == []
    assert any(dep["kind"] == "tools" for dep in child["roles"][0]["dependencies"])
    if change == "missing":
        env.inventory["tools"] = []
    else:
        env.inventory["tools"][0]["enabled_profiles"] = ["another-profile"]
    assert not (await env.service.preview_import("carol", payload))["ready"]
    assert not (
        await env.service.check_readiness("carol", transferred["asset"]["asset_id"], 1)
    )["ready"]
    with pytest.raises(RoleAssetError):
        await env.service.start_run(
            "carol", transferred["asset"]["asset_id"], {"version": 1}
        )
    assert (await env.service.list_runs("carol"))["runs"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("child_tool", ["none", "distinct", "alias"])
@pytest.mark.parametrize("task_prompt", [False, True])
async def test_parent_only_tool_upgrade_reuses_unchanged_child_and_binding(
    workflow_env, child_tool, task_prompt
):
    """Keep child contracts and bindings stable when a shared parent gains a tool.

    Args:
        workflow_env: Public asset services and a mutable local resource inventory.
        child_tool: No child tool, a distinct resource, or another alias for the same tool.
        task_prompt: Supply missing base instructions through each task's override.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "portable-model"
    env.inventory["tools"] = [
        {
            "id": "parent-search",
            "fingerprint": "parent-tool",
            "description": "Parent search",
        },
        {
            "id": "child-search",
            "fingerprint": "child-tool",
            "description": "Child search",
        },
    ]
    child_resource = "parent-search" if child_tool == "alias" else "child-search"
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Shared",
                "prompt": None if task_prompt else "Read evidence",
                "tools": [],
            },
            "installation": {
                "bindings": {
                    "model": "model-a",
                    "parent-slot": "parent-search",
                    "child-slot": child_resource,
                }
            },
        },
    )
    reference = {"role_id": role["role_id"], "role_version": 1}
    leaf = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Leaf",
                "task": "Read evidence",
                **reference,
                "overrides": {
                    "tools": [] if child_tool == "none" else ["child-slot"],
                    **({"prompt": "Leaf evidence"} if task_prompt else {}),
                },
            }
        },
    )
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Team",
                "coordinator": {**reference, "overrides": {"prompt": "Team evidence"}}
                if task_prompt
                else reference,
                "allowed_roles": [
                    {**reference, "overrides": {"prompt": "Team evidence"}}
                    if task_prompt
                    else reference
                ],
                "allowed_workflows": [
                    {
                        "asset_id": leaf["asset_id"],
                        "version": 1,
                        "result_task_id": "result",
                    }
                ],
            }
        },
    )
    root = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Parent",
                "required_inputs": ["goal"],
                "tasks": [
                    {
                        "id": "parent",
                        "name": "Parent",
                        "member": "parent",
                        "task": "Read evidence",
                        **reference,
                        "overrides": {
                            "tools": [],
                            **({"prompt": "Parent evidence"} if task_prompt else {}),
                        },
                    },
                    {
                        "id": "team",
                        "name": "Team",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                    },
                ],
            }
        },
    )
    original_role = await env.roles.get_role("alice", role["role_id"], 1)
    first = await env.service.export_asset("alice", root["asset_id"], 1)
    bindings = {"model": "model-a"}
    bindings.update(
        {
            dependency["slot"]: "parent-search"
            if dependency["fingerprint"] == "parent-tool"
            else "child-search"
            for dependency in first["roles"][0]["dependencies"]
            if dependency["kind"] == "tools"
        }
    )
    installations = {
        f"{role['role_id']}:1": {
            "profile_id": "default",
            "bindings": bindings,
        }
    }
    installed = await env.service.import_asset(
        "bob", {"package": first, "installations": installations}
    )
    old_parent = installed["asset"]
    team_ref = old_parent["definition"]["tasks"][1]["team"]
    local_team = await env.service.get_asset("bob", team_ref["asset_id"], 1)
    leaf_ref = local_team["definition"]["allowed_workflows"][0]
    local_leaf = await env.service.get_asset("bob", leaf_ref["asset_id"], 1)
    original_child_export = await env.service.export_asset(
        "bob", leaf_ref["asset_id"], 1
    )
    definition = copy.deepcopy(root["definition"])
    definition["tasks"][0]["overrides"]["tools"] = ["parent-slot"]
    await env.service.save_asset(
        "alice", {"definition": definition, "expected_version": 1}, root["asset_id"]
    )
    second = await env.service.export_asset("alice", root["asset_id"], 2)
    assert await env.roles.get_role("alice", role["role_id"], 1) == original_role
    assert await env.service.get_asset("alice", leaf["asset_id"], 1) == leaf
    assert await env.service.get_asset("alice", team["asset_id"], 1) == team
    installations[f"{role['role_id']}:1"]["bindings"] = {
        "model": "model-a",
        **{
            dependency["slot"]: "parent-search"
            if dependency["fingerprint"] == "parent-tool"
            else "child-search"
            for dependency in second["roles"][0]["dependencies"]
            if dependency["kind"] == "tools"
        },
    }
    payload = {"package": second, "installations": installations}
    preview = await env.service.preview_import("bob", payload)
    assert preview["ready"], preview
    assert {row["asset_id"]: row["action"] for row in preview["assets"]} == {
        root["asset_id"]: "new_version",
        team["asset_id"]: "reuse",
        leaf["asset_id"]: "reuse",
    }
    upgraded = await env.service.import_asset("bob", payload)
    assert upgraded["asset"]["asset_id"] == old_parent["asset_id"]
    assert upgraded["asset"]["version"] == 2
    assert (await env.service.import_asset("bob", payload))["existing"]
    assert (await env.service.get_asset("bob", old_parent["asset_id"], 1))[
        "definition"
    ] == old_parent["definition"]
    assert await env.service.export_asset("bob", old_parent["asset_id"], 1) == first
    assert (
        await env.service.export_asset("bob", leaf_ref["asset_id"], 1)
        == original_child_export
    )
    source_child = await env.service.export_asset("alice", leaf["asset_id"], 1)
    child_installations = {
        f"{role['role_id']}:1": {
            "profile_id": "default",
            "bindings": {
                "model": "model-a",
                **{
                    dependency["slot"]: "parent-search"
                    if dependency["fingerprint"] == "parent-tool"
                    else "child-search"
                    for dependency in source_child["roles"][0]["dependencies"]
                    if dependency["kind"] == "tools"
                },
            },
        }
    }
    repeated_child = await env.service.import_asset(
        "bob", {"package": source_child, "installations": child_installations}
    )
    assert repeated_child["existing"]
    assert repeated_child["asset"]["asset_id"] == leaf_ref["asset_id"]
    parent_task = upgraded["asset"]["definition"]["tasks"][0]
    parent_role = await env.roles.get_role(
        "bob", parent_task["role_id"], parent_task["role_version"]
    )
    child_role = await env.roles.get_role("bob", local_leaf["definition"]["role_id"], 1)
    assert parent_task["role_id"] != local_leaf["definition"]["role_id"]
    assert (await env.roles.preview_role(parent_role, parent_task["overrides"]))[
        "effective"
    ]["tools"] == ["parent-search"]
    assert (
        await env.roles.preview_role(child_role, local_leaf["definition"]["overrides"])
    )["effective"]["tools"] == ([] if child_tool == "none" else [child_resource])
    changed = copy.deepcopy(second)
    if task_prompt:
        changed["roles"][0]["definition"]["max_steps"] += 1
    else:
        changed["roles"][0]["definition"]["prompt"] = (
            "Changed immutable role instructions"
        )
    with pytest.raises(RoleAssetError) as conflict:
        await env.service.preview_import(
            "bob", {"package": changed, "installations": installations}
        )
    assert conflict.value.status == 409
    if child_tool != "alias":
        env.inventory["tools"] = [
            tool for tool in env.inventory["tools"] if tool["id"] != "parent-search"
        ]
        assert (await env.service.check_readiness("bob", leaf_ref["asset_id"], 1))[
            "ready"
        ]
        assert not (
            await env.service.check_readiness("bob", upgraded["asset"]["asset_id"], 2)
        )["ready"]


@pytest.mark.asyncio
@pytest.mark.parametrize("pinned_version", [1, 3])
async def test_import_keeps_selected_entry_latest_with_pinned_self_version(
    workflow_env, pinned_version
):
    """Keep the selected entry visible without retargeting its fixed child.

    Args:
        workflow_env: Public asset services and real isolated SQLite persistence.
        pinned_version: Same-identity child version declared in the portable closure.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "portable-model"
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Stable role",
                "prompt": "Read evidence",
                "tools": [],
                "skills": [],
                "knowledge_bases": [],
                "max_instances": 4,
            },
            "installation": {"bindings": {"model": "model-a"}},
        },
    )
    reference = {"role_id": role["role_id"], "role_version": 1}
    old = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Pinned leaf",
                "tasks": [
                    {
                        "id": "leaf",
                        "name": "Read evidence",
                        "member": "leaf",
                        "task": "Read evidence",
                        **reference,
                    }
                ],
            }
        },
    )
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Pinned team",
                "coordinator": reference,
                "allowed_roles": [reference],
                "allowed_workflows": [
                    {
                        "asset_id": old["asset_id"],
                        "version": 1,
                        "result_task_id": "leaf",
                    }
                ],
            }
        },
    )
    entry = await env.service.save_asset(
        "alice",
        {
            "expected_version": 1,
            "definition": {
                "name": "Selected entry",
                "required_inputs": ["goal"],
                "tasks": [
                    {
                        "id": "team",
                        "name": "Call pinned team",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                    }
                ],
            },
        },
        old["asset_id"],
    )
    package = copy.deepcopy(
        await env.service.export_asset("alice", entry["asset_id"], 2)
    )
    for asset in package["assets"]:
        if asset["asset_id"] == entry["asset_id"]:
            asset["version"] = pinned_version
        else:
            asset["definition"]["allowed_workflows"][0]["version"] = pinned_version
    assert (await env.service.preview_import("bob", {"package": package}))["ready"]
    installed = await env.service.import_asset("bob", {"package": package})
    current = await env.service.get_asset("bob", installed["asset"]["asset_id"])
    assert current["version"] == installed["asset"]["version"]
    assert current["definition"]["name"] == "Selected entry"
    assert set(current["versions"]) == {1, 2}
    team_ref = current["definition"]["tasks"][0]["team"]
    local_team = await env.service.get_asset(
        "bob", team_ref["asset_id"], team_ref["version"]
    )
    leaf_ref = local_team["definition"]["allowed_workflows"][0]
    assert leaf_ref["asset_id"] == current["asset_id"]
    assert leaf_ref["version"] == 1
    pinned = await env.service.get_asset("bob", leaf_ref["asset_id"], 1)
    assert pinned["definition"]["name"] == "Pinned leaf"
    assert (
        await env.service.export_asset("bob", leaf_ref["asset_id"], 1)
    )["version"] == pinned_version
    assert await env.service.export_asset("bob", current["asset_id"], 2) == package
    repeated = await env.service.import_asset("bob", {"package": package})
    assert repeated["existing"]
    assert repeated["asset"] == current
    assert (
        await env.service.check_readiness("bob", current["asset_id"], 2)
    )["ready"]
