"""Independent migration transaction used for abrupt-process-loss acceptance."""

import asyncio
import json
import sys
import time
from pathlib import Path

from sqlalchemy import event

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.migration import LegacyMigrationService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase


async def main(folder: Path, action: str) -> None:
    """Confirm through real storage or hold an uncommitted transaction for killing.

    Args:
        folder: Isolated database and process evidence directory.
        action: Whether to stop before commit or finish the same public migration.
    """
    db = SQLiteDatabase(str(folder / "atomic.db"))

    async def catalog():
        """Return the controlled external resource inventory.

        Returns:
            Available model and local default profile.
        """
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "model"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    roles = RoleAssetService(db, catalog_loader=catalog)
    workflows = WorkflowService(db, roles)
    migration = LegacyMigrationService(db, roles, workflows)
    try:
        before = {
            "roles": await roles.list_roles("alice"),
            "assets": await workflows.list_assets("alice"),
        }
        preview = await migration.preview("alice", "atomic")
        if action == "hold":

            def hold_before_commit(connection):
                """Expose the real storage commit boundary to the parent process.

                Args:
                    connection: The database connection with uncommitted writes.
                """
                (folder / "commit-ready.json").write_text(
                    json.dumps({"pid": __import__("os").getpid()}), encoding="utf-8"
                )
                while True:
                    time.sleep(1)

            event.listen(db.engine.sync_engine, "commit", hold_before_commit, once=True)
        result = await migration.confirm(
            "alice",
            "atomic",
            {"preview_token": preview["preview_token"], "save_pending": True},
        )
        (folder / "restarted.json").write_text(
            json.dumps(
                {
                    "before": before,
                    "result": result,
                    "assets": await workflows.list_assets("alice"),
                    "roles": await roles.list_roles("alice"),
                }
            ),
            encoding="utf-8",
        )
    finally:
        await workflows.shutdown()
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
