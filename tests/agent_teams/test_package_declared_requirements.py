"""Preserve declared compatibility when recomposing imported nested assets."""

import copy

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requirement",
    [
        "tool",
        "model_capability",
        "tool_schema",
        "used_tool_schema",
        "task_tool_schema",
        "task_tool_capability",
    ],
)
async def test_recomposed_import_enforces_declared_requirements(
    workflow_env, tmp_path, requirement
):
    """Keep imported contracts when a local parent is transferred to a new store.

    Args:
        workflow_env: Public persistent services and a mutable external catalog.
        tmp_path: Isolated directory for the independent receiving store.
        requirement: Portable compatibility requirement absent or changed locally.
    """
    env = workflow_env
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    env.inventory["models"][0].update(
        fingerprint="portable-model", capabilities=["vision"]
    )
    env.inventory["tools"] = [
        {
            "id": "local-search",
            "fingerprint": "portable-search",
            "capabilities": ["search"],
            "input_schema": schema,
        }
    ]
    source_role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Portable evidence",
                "prompt": "Read evidence",
                "tools": ["search"] if requirement == "used_tool_schema" else [],
            },
            "installation": {
                "bindings": {"model": "model-a", "search": "local-search"}
            },
        },
    )
    source = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Portable leaf",
                "task": "Read evidence",
                "role_id": source_role["role_id"],
                "role_version": 1,
                "overrides": {"tools": ["search"]}
                if requirement.startswith("task_")
                else {},
            }
        },
    )
    package = await env.service.export_asset("alice", source["asset_id"], 1)
    if requirement == "model_capability":
        next(
            dependency
            for dependency in package["roles"][0]["dependencies"]
            if dependency["kind"] == "models"
        )["capabilities"] = ["vision"]
    elif requirement == "task_tool_capability":
        next(
            dependency
            for dependency in package["roles"][0]["dependencies"]
            if dependency["kind"] == "tools"
        )["capabilities"] = ["search"]
    elif requirement in {"tool", "tool_schema"}:
        package["roles"][0]["dependencies"].append(
            {
                "slot": "declared_search",
                "kind": "tools",
                "fingerprint": "portable-search",
                "logical_slots": ["evidence_source"],
                "capabilities": ["search"],
                "input_schema": schema if requirement == "tool_schema" else None,
            }
        )
    installed = await env.service.import_asset("bob", {"package": package})
    child = installed["asset"]
    original = await env.service.export_asset("bob", child["asset_id"], 1)
    assert (await env.service.check_readiness("bob", child["asset_id"], 1))["ready"]
    coordinator = await env.roles.save_role(
        "bob",
        {
            "definition": {
                "name": "Wrapper coordinator",
                "prompt": "Call the leaf",
                "tools": [],
            },
            "installation": {"bindings": {"model": "model-a"}},
        },
    )
    reference = {"role_id": coordinator["role_id"], "role_version": 1}
    parent = await env.service.save_asset(
        "bob",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Locally authored wrapper",
                "coordinator": reference,
                "allowed_roles": [reference],
                "allowed_workflows": [
                    {
                        "asset_id": child["asset_id"],
                        "version": 1,
                        "result_task_id": "result",
                    }
                ],
            }
        },
    )
    if requirement in {"used_tool_schema", "task_tool_schema"}:
        env.inventory["tools"][0]["input_schema"] = {"type": "object"}
    wrapped = await env.service.export_asset("bob", parent["asset_id"], 1)
    inventory = copy.deepcopy(env.inventory)
    inventory["profiles"][0]["id"] = "carol-profile"
    inventory["models"][0]["id"] = "carol-model"
    inventory["tools"][0]["id"] = "carol-search"
    if requirement == "model_capability":
        inventory["models"][0]["capabilities"] = []
    elif requirement == "tool":
        inventory["tools"] = []
    elif requirement == "task_tool_capability":
        inventory["tools"][0]["capabilities"] = []
    else:
        inventory["tools"][0]["input_schema"] = {"type": "object"}

    async def catalog():
        """Return the receiving machine's independent resource inventory.

        Returns:
            Resources with identities different from the exporting machine.
        """
        return inventory

    receiver_path = tmp_path / "receiver" / "workflows.db"
    receiver_path.parent.mkdir()
    db = SQLiteDatabase(str(receiver_path))
    receiver = WorkflowService(db, RoleAssetService(db, catalog_loader=catalog))
    try:
        preview = await receiver.preview_import("carol", {"package": wrapped})
        assert not preview["ready"]
        with pytest.raises(RoleAssetError):
            await receiver.import_asset("carol", {"package": wrapped})
        assert (await receiver.list_assets("carol"))["assets"] == []

        inventory["models"][0]["capabilities"] = ["vision"]
        inventory["tools"] = [
            {
                "id": "carol-search",
                "fingerprint": "portable-search",
                "capabilities": ["search"],
                "input_schema": schema,
            }
        ]
        transferred = await receiver.import_asset("carol", {"package": wrapped})
        assert transferred["ready"]
        assert (
            await receiver.check_readiness("carol", transferred["asset"]["asset_id"], 1)
        )["ready"]
        assert await env.service.export_asset("bob", child["asset_id"], 1) == original
        assert (await env.service.get_asset("bob", child["asset_id"], 1))[
            "definition"
        ] == child["definition"]
    finally:
        await receiver.shutdown()
        await db.engine.dispose()
