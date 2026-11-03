"""Runtime checks the local resources used by each fixed configuration."""

import pytest
from sqlalchemy import select

from astrbot.core.db.po import AgentWorkflowPackage
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_unused_source_requirements_do_not_reappear_at_runtime(workflow_env):
    """Two overrides use their actual local resources even without a receipt.

    Args:
        workflow_env: Public asset services and mutable local resource inventory.
    """
    env = workflow_env
    env.inventory["tools"] = [{"id": "base-tool"}, {"id": "active-tool"}]
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Writer",
                "prompt": "Write",
                "provider_id": "model-a",
                "tools": ["base-tool"],
                "max_instances": 2,
            },
            "installation": {"profile_id": "default"},
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Contexts",
                "result_task_id": "active",
                "tasks": [
                    {
                        "id": "empty",
                        "name": "Empty",
                        "member": "empty",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "overrides": {"tools": []},
                        "task": "Write",
                    },
                    {
                        "id": "active",
                        "name": "Active",
                        "member": "active",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "overrides": {"tools": ["active-tool"]},
                        "task": "Write",
                    },
                ],
            }
        },
    )
    package = await env.service.export_asset("alice", asset["asset_id"], 1)
    selections = [
        {
            "kind": item["kind"],
            "source_id": item["source_id"],
            "target_id": item["source_id"],
        }
        for item in package["requirements"]
    ]
    imported = await env.service.import_asset(
        "bob", {"package": package, "selections": selections}
    )
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
    env.inventory["tools"] = [{"id": "active-tool"}]
    assert (await env.service.check_readiness("bob", imported["asset"]["asset_id"], 1))[
        "ready"
    ]
    env.inventory["tools"] = []
    assert not (
        await env.service.check_readiness("bob", imported["asset"]["asset_id"], 1)
    )["ready"]
