"""Runner requirements through the shared role and asset boundaries."""

import pytest

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase


@pytest.mark.asyncio
async def test_role_preview_locates_unavailable_internal_recovery(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "roles.db"))

    async def inventory():
        return {
            "profiles": [
                {
                    "id": "remote",
                    "name": "Dify workflow",
                    "runner_type": "dify",
                    "runner_config": {"dify_api_type": "workflow"},
                }
            ],
            "models": [],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    service = RoleAssetService(db, catalog_loader=inventory)
    preview = await service.preview_role(
        {
            "definition": {
                "name": "Remote researcher",
                "prompt": "Check evidence.",
                "runner_capabilities": ["internal_checkpoint"],
            },
            "installation": {"profile_id": "remote"},
        }
    )
    assert preview["ready"] is False
    assert preview["issues"] == [
        {
            "path": "definition.runner_capabilities.0",
            "code": "INCOMPATIBLE",
            "message": "dify (workflow) does not support internal_checkpoint",
        }
    ]
    assert preview["effective"]["runner"]["recovery"] == "remote_query"
    assert preview["effective"]["provider_id"] is None
    await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["single", "graph", "autonomous"])
async def test_readiness_and_launch_use_the_same_required_runner_capability(
    tmp_path, kind
):
    db = SQLiteDatabase(str(tmp_path / "assets.db"))

    async def inventory():
        return {
            "profiles": [{"id": "default", "runner_type": "dashscope"}],
            "models": [],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    roles = RoleAssetService(db, catalog_loader=inventory)
    role = await roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Researcher",
                "prompt": "Check evidence.",
                "runner_capabilities": ["internal_checkpoint"],
            }
        },
    )
    task = {
        "name": "Research",
        "role_id": role["role_id"],
        "role_version": 1,
        "task": "Check evidence.",
    }
    definition = (
        task
        if kind == "single"
        else {
            "name": "Research",
            "tasks": [{"id": "research", "member": "research", **task}],
        }
        if kind == "graph"
        else {
            "kind": "autonomous",
            "name": "Research team",
            "coordinator": {"role_id": role["role_id"], "role_version": 1},
            "allowed_roles": [{"role_id": role["role_id"], "role_version": 1}],
        }
    )
    service = WorkflowService(
        db,
        roles,
        ports_factory=lambda owner: pytest.fail("Blocked assets must not dispatch"),
    )
    asset = await service.save_asset("alice", {"definition": definition})
    preview = await roles.resolve_role("alice", role["role_id"], 1)
    readiness = await service.check_readiness("alice", asset["asset_id"], 1)
    assert readiness["ready"] is False
    assert readiness["issues"] == preview["issues"]
    with pytest.raises(RoleAssetError) as rejected:
        await service.start_run(
            "alice",
            asset["asset_id"],
            {
                "version": 1,
                "inputs": {"goal": "Check evidence."} if kind == "autonomous" else {},
            },
        )
    assert rejected.value.fields == preview["issues"]
    assert (await service.list_runs("alice"))["runs"] == []
    assert await db.get_platform_sessions_by_creator("alice") == []
    await service.shutdown()
    await db.engine.dispose()
