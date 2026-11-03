"""Execution resolves installed definitions without reading import receipts."""

import pytest

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.po import AgentWorkflowPackage
from astrbot.core.db.sqlite import SQLiteDatabase


@pytest.mark.asyncio
async def test_readiness_uses_local_definition_not_package_receipt(tmp_path):
    """A receipt is audit data and cannot change the frozen local resources.

    Args:
        tmp_path: Isolated database directory.
    """
    db = SQLiteDatabase(str(tmp_path / "receipt.db"))

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "local-model"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    roles = RoleAssetService(db, catalog_loader=catalog)
    service = WorkflowService(db, roles)
    try:
        role = await roles.save_role(
            "alice",
            {
                "definition": {
                    "name": "Writer",
                    "prompt": "Write evidence.",
                    "provider_id": "local-model",
                },
                "installation": {"profile_id": "default"},
            },
        )
        asset = await service.save_asset(
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
        async with db.get_db() as session:
            session.add(
                AgentWorkflowPackage(
                    owner_username="alice",
                    package_id="source",
                    version=1,
                    asset_id=asset["asset_id"],
                    installed_version=1,
                    digest="audit",
                    package={"format_version": 3},
                    role_ids={"source-role:1": role["role_id"]},
                )
            )
            await session.commit()
        result = await service.check_readiness("alice", asset["asset_id"], 1)
        assert result["ready"], result["issues"]
    finally:
        await db.engine.dispose()
