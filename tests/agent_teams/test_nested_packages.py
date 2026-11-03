"""Pinned mixed asset closures through the version 3 package boundary."""

import copy

import pytest
import pytest_asyncio

from astrbot.core.agent_teams.assets import RoleAssetError
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest_asyncio.fixture
async def nested_package(workflow_env):
    """Build an autonomous team with a fixed workflow and a graph caller.

    Args:
        workflow_env: Public services backed by an isolated database.

    Returns:
        Source services, fixed root and complete exported closure.
    """
    env = workflow_env
    role = {"role_id": env.role["role_id"], "role_version": 1}
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Team",
                "coordinator": {"id": "lead", **role},
                "allowed_roles": [{"id": "writer", **role}],
                "allowed_workflows": [
                    {"asset_id": env.asset["asset_id"], "version": 1}
                ],
            }
        },
    )
    root = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Root",
                "required_inputs": ["goal"],
                "result_task_id": "team",
                "tasks": [
                    {
                        "id": "team",
                        "name": "Team",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                    }
                ],
            }
        },
    )
    return env, root, await env.service.export_asset("alice", root["asset_id"], 1)


@pytest.mark.asyncio
async def test_export_and_import_pin_the_complete_mixed_closure(nested_package):
    """Reused source roles and child versions receive exact local references.

    Args:
        nested_package: Public source services and an exported mixed closure.
    """
    env, root, package = nested_package
    assert package["format_version"] == 3
    assert len(package["assets"]) == 2 and len(package["roles"]) == 1
    assert {item["kind"] for item in package["requirements"]} == {"profiles", "models"}
    choices = [
        {
            "kind": item["kind"],
            "source_id": item["source_id"],
            "target_id": item["source_id"],
        }
        for item in package["requirements"]
    ]
    result = await env.service.import_asset(
        "bob", {"package": package, "selections": choices}
    )
    ref = result["asset"]["definition"]["tasks"][0]["team"]
    team = await env.service.get_asset("bob", ref["asset_id"], ref["version"])
    child_ref = team["definition"]["allowed_workflows"][0]
    child = await env.service.get_asset(
        "bob", child_ref["asset_id"], child_ref["version"]
    )
    assert (
        child["definition"]["tasks"][0]["role_id"]
        == team["definition"]["coordinator"]["role_id"]
    )
    assert (
        await env.service.check_readiness(
            "bob", result["asset"]["asset_id"], result["asset"]["version"]
        )
    )["ready"]
    assert (
        await env.service.import_asset(
            "bob", {"package": package, "selections": choices}
        )
    )["existing"]
    with pytest.raises(RoleAssetError):
        await env.service.get_asset("eve", root["asset_id"], 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "missing_child",
        "duplicate_child",
        "unreachable",
        "wrong_kind",
        "wrong_inputs",
        "cycle",
        "missing_role",
        "legacy_field",
    ],
)
async def test_invalid_nested_closures_never_install(nested_package, change):
    """Reject incomplete, unreachable, incompatible and obsolete definitions.

    Args:
        nested_package: Public source services and an exported mixed closure.
        change: Invalid mutation of the package.
    """
    env, _, original = nested_package
    package = copy.deepcopy(original)
    if change == "missing_child":
        package["assets"] = []
    elif change == "duplicate_child":
        package["assets"].append(copy.deepcopy(package["assets"][0]))
    elif change == "unreachable":
        extra = copy.deepcopy(package["assets"][0])
        extra["asset_id"] = "unreachable"
        package["assets"].append(extra)
    elif change == "wrong_kind":
        leaf = next(
            asset
            for asset in package["assets"]
            if asset["definition"].get("kind") != "autonomous"
        )
        package["definition"]["tasks"][0]["team"] = {
            "asset_id": leaf["asset_id"],
            "version": leaf["version"],
        }
    elif change == "wrong_inputs":
        package["definition"]["tasks"][0]["inputs"] = {}
    elif change == "cycle":
        package["definition"]["tasks"][0]["team"] = None
        package["definition"]["tasks"][0]["workflow"] = {
            "asset_id": package["asset_id"],
            "version": package["version"],
        }
    elif change == "missing_role":
        package["roles"] = []
    else:
        package["requirements"][0]["slot"] = "old"
    choices = [
        {
            "kind": item["kind"],
            "source_id": item["source_id"],
            "target_id": item["source_id"],
        }
        for item in original["requirements"]
    ]
    with pytest.raises(RoleAssetError):
        await env.service.import_asset(
            "bob", {"package": package, "selections": choices}
        )
    assert (await env.service.list_assets("bob"))["assets"] == []
    assert (await env.roles.list_roles("bob"))["roles"] == []
