"""New package versions cannot damage previously installed fixed versions."""

import copy

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_invalid_loop_upgrade_does_not_install_or_change_existing_version(
    workflow_env,
):
    """An excessive loop is rejected without staging an incomplete version.

    Args:
        workflow_env: Public asset services and mutable local resource inventory.
    """
    env = workflow_env
    env.inventory["profiles"][0]["max_iterations"] = 1
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    selections = [
        {
            "kind": item["kind"],
            "source_id": item["source_id"],
            "target_id": item["source_id"],
        }
        for item in package["requirements"]
    ]
    payload = {"package": package, "selections": selections}
    installed = await env.service.import_asset("bob", payload)
    identity = installed["asset"]["asset_id"]
    upgrade = copy.deepcopy(package)
    upgrade["version"] = 2
    upgrade["definition"]["loops"] = [
        {
            "id": "review",
            "name": "Review",
            "body": ["writer"],
            "exit_task_id": "writer",
            "max_iterations": 2,
            "exit_condition": {"field": "text", "value": "done"},
        }
    ]
    preview = await env.service.preview_import(
        "bob", {"package": upgrade, "selections": selections}
    )
    assert not preview["ready"] and any(
        issue["code"] == "LIMIT" for issue in preview["issues"]
    )
    with pytest.raises(RoleAssetError):
        await env.service.import_asset(
            "bob", {"package": upgrade, "selections": selections}
        )
    assert (await env.service.check_readiness("bob", identity, 1))["ready"]
    assert await env.service.get_asset("bob", identity, 1) == installed["asset"]
    with pytest.raises(RoleAssetError):
        await env.service.get_asset("bob", identity, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("member_count,ready", [(1, True), (2, False)])
async def test_member_limits_are_checked_for_each_new_graph(
    workflow_env, member_count, ready
):
    """Distinct members sharing a role cannot exceed its effective instance limit.

    Args:
        workflow_env: Public services and local resource catalog.
        member_count: Number of distinct members using one pinned role.
        ready: Expected preview readiness.
    """
    env = workflow_env
    env.inventory["profiles"][0]["max_instances"] = 1
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    task = package["definition"]["tasks"][0]
    package["definition"]["tasks"] = [
        {**task, "id": f"writer_{i}", "member": f"member_{i}"}
        for i in range(member_count)
    ]
    package["definition"]["result_task_id"] = "writer_0"
    selections = [
        {
            "kind": item["kind"],
            "source_id": item["source_id"],
            "target_id": item["source_id"],
        }
        for item in package["requirements"]
    ]
    preview = await env.service.preview_import(
        "bob", {"package": package, "selections": selections}
    )
    assert preview["ready"] is ready
    if not ready:
        assert any(issue["code"] == "LIMIT" for issue in preview["issues"])
        with pytest.raises(RoleAssetError):
            await env.service.import_asset(
                "bob", {"package": package, "selections": selections}
            )
        assert (await env.service.list_assets("bob"))["assets"] == []
