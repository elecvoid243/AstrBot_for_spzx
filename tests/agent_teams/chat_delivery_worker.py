"""Independent process exercising chat delivery around a persistent human wait."""

import asyncio
import json
import sys
from pathlib import Path

import httpx

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.chat import ChatCaller
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase


async def main() -> None:
    """Create or resume the actual persisted run and deliver through HTTP.

    Returns:
        None after resume evidence is saved, or waits for forced termination.
    """
    directory, endpoint, mode, boundary = sys.argv[1:]
    root = Path(directory)
    db = SQLiteDatabase(str(root / "delivery.db"))
    service = WorkflowService(db, RoleAssetService(db))
    caller = ChatCaller(
        platform_id="qq",
        sender_id="12345",
        source="qq:FriendMessage:12345",
        profile_id="default",
    )

    async def send(actual: ChatCaller, text: str) -> bool:
        """Send to an independent external ledger that survives worker death.

        Args:
            actual: Persisted verified source identity.
            text: Projected message without execution internals.

        Returns:
            Whether the external service accepted this message.
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                endpoint, json={"caller": actual.model_dump(), "text": text}
            )
            response.raise_for_status()
        return True

    try:
        if mode == "start":
            asset = await service.save_asset(
                "operator",
                {
                    "definition": {
                        "name": "Human decision",
                        "result_task_id": "question",
                        "tasks": [
                            {
                                "id": "question",
                                "name": "Region",
                                "human": {
                                    "kind": "question",
                                    "question": "Which region?",
                                    "choices": ["Asia", "Europe"],
                                },
                            }
                        ],
                    }
                },
            )
            await service.chat.save_access(
                "operator",
                asset["asset_id"],
                {
                    **caller.model_dump(),
                    "version": 1,
                },
            )
            accepted = await service.chat.invoke(caller, asset["asset_id"], {})
            run = await service.get_run("operator", accepted["run_id"])
            if boundary == "after_wait_notice":
                await service.chat.deliver_pending(send)
            (root / "ready.json").write_text(
                json.dumps(
                    {"run_id": run["run_id"], "todo_id": run["todos"][0]["todo_id"]}
                ),
                encoding="utf-8",
            )
            await asyncio.Future()
        else:
            saved = json.loads((root / "ready.json").read_text(encoding="utf-8"))
            await service.boot_sweep()
            before = await service.get_run("operator", saved["run_id"])
            assert before["todos"][0]["todo_id"] == saved["todo_id"]
            assert before["todos"][0]["status"] == "pending"
            await service.chat.deliver_pending(send)
            await service.answer_todo(
                "operator",
                saved["run_id"],
                saved["todo_id"],
                {
                    "request_id": "answer-after-restart",
                    "action": "answer",
                    "answer": "Asia",
                },
            )
            projection = await service.chat.get_run(caller, saved["run_id"])
            assert projection["status"] == "completed"
            await service.chat.deliver_pending(send)
            await service.chat.deliver_pending(send)
            (root / "resumed.json").write_text(json.dumps(projection), encoding="utf-8")
    finally:
        await service.shutdown()
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
