"""Reimport fixed dependencies without mutating their resource selections."""

import copy

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_parent_reuses_previously_imported_child_and_rejects_changed_mapping(
    workflow_env,
):
    """A parent-only resource does not alter the child's fixed mapping.

    Args:
        workflow_env: Public assets and mutable resource catalog.
    """
    env = workflow_env
    env.inventory["tools"] = [{"id": "source-tool"}, {"id": "other-tool"}]
    leaf = env.asset
    leaf_package = await env.service.export_asset("alice", leaf["asset_id"], 1)
    leaf_choices = [
        {
            "kind": item["kind"],
            "source_id": item["source_id"],
            "target_id": item["source_id"],
        }
        for item in leaf_package["requirements"]
    ]
    imported = await env.service.import_asset(
        "bob", {"package": leaf_package, "selections": leaf_choices}
    )
    parent = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Parent",
                "result_task_id": "child",
                "tasks": [
                    {
                        "id": "child",
                        "name": "Child",
                        "workflow": {"asset_id": leaf["asset_id"], "version": 1},
                    },
                    {
                        "id": "extra",
                        "name": "Extra",
                        "member": "extra",
                        "role": {
                            "name": "Extra",
                            "prompt": "Write",
                            "provider_id": "model-a",
                            "tools": ["source-tool"],
                        },
                        "installation": {"profile_id": "default"},
                        "task": "Write",
                    },
                ],
            }
        },
    )
    package = await env.service.export_asset("alice", parent["asset_id"], 1)
    choices = [
        {
            "kind": item["kind"],
            "source_id": item["source_id"],
            "target_id": item["source_id"],
        }
        for item in package["requirements"]
    ]
    preview = await env.service.preview_import(
        "bob", {"package": package, "selections": choices}
    )
    assert preview["ready"]
    assert (
        next(
            asset
            for asset in preview["assets"]
            if asset["asset_id"] == leaf["asset_id"]
        )["action"]
        == "reuse"
    )
    installed = await env.service.import_asset(
        "bob", {"package": package, "selections": choices}
    )
    ref = installed["asset"]["definition"]["tasks"][0]["workflow"]
    assert ref["asset_id"] == imported["asset"]["asset_id"]
    changed = copy.deepcopy(choices)
    next(item for item in changed if item["kind"] == "models")["target_id"] = (
        "unavailable"
    )
    with pytest.raises(RoleAssetError):
        await env.service.import_asset(
            "bob", {"package": package, "selections": changed}
        )
    assert (
        await env.service.get_asset("bob", ref["asset_id"], ref["version"])
        == imported["asset"]
    )
