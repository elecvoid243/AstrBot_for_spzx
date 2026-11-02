"""Validate every path through shared fixed child versions."""

import pytest

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "short_first,leaf_limit,ready",
    [(True, 2, False), (False, 2, False), (True, 3, True), (False, 3, True)],
)
async def test_shared_intermediate_checks_descendants_on_every_path(
    tmp_path, short_first, leaf_limit, ready
):
    """The long path enforces the leaf limit even if a short path visits it first.

    Args:
        tmp_path: Independent source and target databases.
        short_first: Ordering of shared-child calls in the parent.
        leaf_limit: Maximum depth of the leaf role.
        ready: Expected package readiness.
    """

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "model"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    source_db = SQLiteDatabase(str(tmp_path / "source.db"))
    target_db = SQLiteDatabase(str(tmp_path / "target.db"))
    source = WorkflowService(
        source_db, RoleAssetService(source_db, catalog_loader=catalog)
    )
    target = WorkflowService(
        target_db, RoleAssetService(target_db, catalog_loader=catalog)
    )
    try:
        leaf = await source.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Leaf",
                    "max_depth": 6,
                    "result_task_id": "write",
                    "tasks": [
                        {
                            "id": "write",
                            "name": "Write",
                            "member": "writer",
                            "role": {
                                "name": "Writer",
                                "prompt": "Write",
                                "provider_id": "model",
                                "max_depth": leaf_limit,
                            },
                            "installation": {"profile_id": "default"},
                            "task": "Write",
                        }
                    ],
                }
            },
        )
        shared = await source.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Shared",
                    "max_depth": 6,
                    "result_task_id": "child",
                    "tasks": [
                        {
                            "id": "child",
                            "name": "Child",
                            "workflow": {"asset_id": leaf["asset_id"], "version": 1},
                        }
                    ],
                }
            },
        )
        bridge = await source.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Bridge",
                    "max_depth": 6,
                    "result_task_id": "child",
                    "tasks": [
                        {
                            "id": "child",
                            "name": "Child",
                            "workflow": {"asset_id": shared["asset_id"], "version": 1},
                        }
                    ],
                }
            },
        )
        children = [("short", shared), ("long", bridge)]
        if not short_first:
            children.reverse()
        root = await source.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Root",
                    "max_depth": 6,
                    "result_task_id": "long",
                    "tasks": [
                        {
                            "id": name,
                            "name": name,
                            "workflow": {"asset_id": asset["asset_id"], "version": 1},
                        }
                        for name, asset in children
                    ],
                }
            },
        )
        package = await source.export_asset("alice", root["asset_id"], 1)
        selections = [
            {
                "kind": item["kind"],
                "source_id": item["source_id"],
                "target_id": item["source_id"],
            }
            for item in package["requirements"]
        ]
        preview = await target.preview_import(
            "bob", {"package": package, "selections": selections}
        )
        assert preview["ready"] is ready
        if ready:
            installed = await target.import_asset(
                "bob", {"package": package, "selections": selections}
            )
            assert (
                await target.check_readiness("bob", installed["asset"]["asset_id"], 1)
            )["ready"]
        else:
            assert any(issue["code"] == "LIMIT" for issue in preview["issues"])
            assert (await target.list_assets("bob"))["assets"] == []
    finally:
        await source_db.engine.dispose()
        await target_db.engine.dispose()
