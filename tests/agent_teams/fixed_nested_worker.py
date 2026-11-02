"""Exercise a fixed nested graph across an actual process termination."""

import asyncio
import json
import sys
from pathlib import Path

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase


async def main(folder: Path, action: str) -> None:
    """Persist a child human wait and recover it from another process.

    Args:
        folder: Isolated database and handoff directory.
        action: Start the run or recover the terminated process.
    """
    db = SQLiteDatabase(str(folder / "nested.db"))
    service = WorkflowService(db, RoleAssetService(db))
    try:
        if action == "start":
            child = await service.save_asset(
                "alice",
                {
                    "definition": {
                        "name": "Child",
                        "result_task_id": "question",
                        "tasks": [
                            {
                                "id": "question",
                                "name": "Question",
                                "human": {"kind": "question", "question": "Continue?"},
                            }
                        ],
                    }
                },
            )
            parent = await service.save_asset(
                "alice",
                {
                    "definition": {
                        "name": "Parent",
                        "result_task_id": "child",
                        "tasks": [
                            {
                                "id": "child",
                                "name": "Child",
                                "workflow": {
                                    "asset_id": child["asset_id"],
                                    "version": 1,
                                },
                            }
                        ],
                    }
                },
            )
            run = await service.start_run("alice", parent["asset_id"], {"version": 1})
            for _ in range(500):
                run = await service.get_run("alice", run["run_id"])
                if run["todos"]:
                    break
                await asyncio.sleep(0.01)
            assert run["todos"], run
            (folder / "handoff.json").write_text(
                json.dumps(
                    {
                        "run_id": run["run_id"],
                        "scope_id": run["scopes"][0]["run_id"],
                        "todo_id": run["todos"][0]["todo_id"],
                    }
                ),
                encoding="utf-8",
            )
            await asyncio.Event().wait()
        else:
            handoff = json.loads((folder / "handoff.json").read_text(encoding="utf-8"))
            await service.boot_sweep()
            restored = await service.get_run("alice", handoff["run_id"])
            assert restored["scopes"][0]["run_id"] == handoff["scope_id"]
            assert restored["todos"][0]["todo_id"] == handoff["todo_id"]
            await service.answer_todo(
                "alice",
                handoff["run_id"],
                handoff["todo_id"],
                {"action": "answer", "answer": "Yes", "request_id": "answer"},
            )
            for _ in range(500):
                run = await service.get_run("alice", handoff["run_id"])
                if run["status"] != "running":
                    break
                await asyncio.sleep(0.01)
            assert run["status"] == "completed", run
            assert run["result"]["text"] == "Yes"
            assert run["result_ref"] == run["scopes"][0]["tasks"][0]["result_ref"]
            (folder / "recovered.json").write_text(
                json.dumps({"run_id": run["run_id"], "result_ref": run["result_ref"]}),
                encoding="utf-8",
            )
    finally:
        await service.shutdown()
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
