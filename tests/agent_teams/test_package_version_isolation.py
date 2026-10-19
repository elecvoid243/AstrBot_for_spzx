"""Keep imported version readiness independent of other package usage limits."""

import copy

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError
from tests.agent_teams.test_nested_packages import nested_package as nested_package
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_pending_loop_upgrade_keeps_installed_version_launchable(workflow_env):
    """A pending graph cannot impose its loop limit on an installed version.

    Args:
        workflow_env: Public asset services, actual SQLite, and external ports.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "portable-model"
    env.inventory["profiles"][0]["max_iterations"] = 1
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    package["roles"][0]["definition"]["max_iterations"] = None
    package["definition"]["overrides"]["max_iterations"] = None
    installed = await env.service.import_asset("bob", {"package": package})
    asset_id = installed["asset"]["asset_id"]
    original = await env.service.get_asset("bob", asset_id, 1)
    assert (await env.service.check_readiness("bob", asset_id, 1))["ready"]
    running = await env.service.start_run("bob", asset_id, {"version": 1})
    snapshot = copy.deepcopy(running["snapshot"])

    upgrade = copy.deepcopy(package)
    upgrade["version"] = 2
    task = upgrade["definition"]
    upgrade["definition"] = {
        "name": "Pending loop upgrade",
        "tasks": [
            {
                "id": "write",
                "member": "writer",
                **{
                    key: value
                    for key, value in task.items()
                    if key not in {"required_inputs", "limits"}
                },
            }
        ],
        "loops": [
            {
                "id": "review",
                "name": "Review",
                "body": ["write"],
                "exit_task_id": "write",
                "max_iterations": 2,
                "exit_condition": {"field": "text", "value": "done"},
            }
        ],
    }
    preview = await env.service.preview_import("bob", {"package": upgrade})
    assert not preview["ready"]
    assert any(issue["code"] == "LIMIT" for issue in preview["issues"])
    pending = await env.service.import_asset(
        "bob", {"package": upgrade, "save_pending": True}
    )
    assert not pending["ready"]
    assert pending["asset"]["version"] == 2

    assert (await env.service.check_readiness("bob", asset_id, 1))["ready"]
    assert (await env.service.get_asset("bob", asset_id, 1))["definition"] == original[
        "definition"
    ]
    launched = await env.service.start_run("bob", asset_id, {"version": 1})
    assert launched["snapshot"]["definition"] == original["definition"]
    assert (await env.service.get_run("bob", running["run_id"]))["snapshot"] == snapshot
    with pytest.raises(RoleAssetError) as rejected:
        await env.service.start_run("bob", asset_id, {"version": 2})
    assert any(issue["code"] == "LIMIT" for issue in rejected.value.fields)


@pytest.mark.asyncio
@pytest.mark.parametrize("separate_identity", [False, True])
@pytest.mark.parametrize("member_count", [1, 2])
async def test_other_graph_member_limits_keep_installed_version_launchable(
    workflow_env, separate_identity, member_count
):
    """Validate the selected graph's members while preserving prior versions.

    Args:
        workflow_env: Public asset services and mutable local resource inventory.
        separate_identity: Use an independent package rather than an upgrade.
        member_count: Distinct members with a selected profile limit of one.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "portable-model"
    env.inventory["profiles"][0]["max_instances"] = 1
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    package["roles"][0]["definition"]["max_instances"] = None
    package["definition"]["overrides"]["max_instances"] = None
    installed = await env.service.import_asset("bob", {"package": package})
    asset_id = installed["asset"]["asset_id"]
    original = installed["asset"]["definition"]

    other = copy.deepcopy(package)
    other["version"] = 2
    if separate_identity:
        other["asset_id"] = "independent-member-graph"
    task = other["definition"]
    other["definition"] = {
        "name": "Other member graph",
        "tasks": [
            {
                "id": f"write_{index}",
                "member": f"writer_{index}",
                **{
                    key: value
                    for key, value in task.items()
                    if key not in {"required_inputs", "limits"}
                },
            }
            for index in range(member_count)
        ],
    }
    ready = member_count == 1
    preview = await env.service.preview_import("bob", {"package": other})
    assert preview["ready"] is ready
    saved = await env.service.import_asset(
        "bob", {"package": other, "save_pending": not ready}
    )
    assert saved["ready"] is ready
    assert (await env.service.check_readiness("bob", asset_id, 1))["ready"]
    launched = await env.service.start_run("bob", asset_id, {"version": 1})
    assert launched["snapshot"]["definition"] == original
    selected = saved["asset"]
    assert (
        await env.service.check_readiness(
            "bob", selected["asset_id"], selected["version"]
        )
    )["ready"] is ready
    if not ready:
        with pytest.raises(RoleAssetError) as rejected:
            await env.service.start_run(
                "bob", selected["asset_id"], {"version": selected["version"]}
            )
        assert any(issue["code"] == "LIMIT" for issue in rejected.value.fields)


@pytest.mark.asyncio
async def test_pending_nested_loop_keeps_original_pinned_child_launchable(
    nested_package,
):
    """Keep the valid pinned closure ready while an upgraded child is pending.

    Args:
        nested_package: Portable team and leaf with public persistent services.
    """
    env, package = nested_package
    env.inventory["profiles"][0]["max_iterations"] = 1
    for role in package["roles"]:
        role["definition"]["max_iterations"] = None
    for task in [
        package["definition"]["coordinator"],
        *package["definition"]["allowed_roles"],
        package["assets"][0]["definition"],
    ]:
        task["overrides"]["max_iterations"] = None
    installed = await env.service.import_asset("bob", {"package": package})
    parent_id = installed["asset"]["asset_id"]
    ref = installed["asset"]["definition"]["allowed_workflows"][0]
    original = await env.service.get_asset("bob", ref["asset_id"], ref["version"])

    upgrade = copy.deepcopy(package)
    upgrade["version"] = 2
    upgrade["definition"]["allowed_workflows"][0].update(
        version=2, result_task_id="write"
    )
    child = upgrade["assets"][0]
    child["version"] = 2
    task = child["definition"]
    child["definition"] = {
        "name": "Pending nested loop",
        "tasks": [
            {
                "id": "write",
                "member": "writer",
                **{
                    key: value
                    for key, value in task.items()
                    if key not in {"required_inputs", "limits"}
                },
            }
        ],
        "loops": [
            {
                "id": "review",
                "name": "Review",
                "body": ["write"],
                "exit_task_id": "write",
                "max_iterations": 2,
                "exit_condition": {"field": "text", "value": "done"},
            }
        ],
    }
    assert not (await env.service.preview_import("bob", {"package": upgrade}))["ready"]
    pending = await env.service.import_asset(
        "bob", {"package": upgrade, "save_pending": True}
    )
    assert not pending["ready"]
    assert (await env.service.check_readiness("bob", parent_id, 1))["ready"]
    assert (await env.service.check_readiness("bob", ref["asset_id"], ref["version"]))[
        "ready"
    ]
    launched = await env.service.start_run(
        "bob", ref["asset_id"], {"version": ref["version"]}
    )
    assert launched["snapshot"]["definition"] == original["definition"]
    assert (await env.service.get_asset("bob", parent_id, 1))[
        "definition"
    ] == installed["asset"]["definition"]
    assert not (await env.service.check_readiness("bob", parent_id, 2))["ready"]
