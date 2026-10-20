"""Historical unsupported task snapshots across an actual process restart."""

import asyncio
import json
import sys
from pathlib import Path

from astrbot.core.agent.remote_checkpoint import RemoteAgentCheckpoint
from astrbot.core.agent_team_execution import NodeExecutionBinding
from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.po import AgentWorkflowCheckpoint, AgentWorkflowRun
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.provider.entities import ProviderRequest


async def run_worker(folder: Path, action: str, runner: str):
    """Seed an old frozen task, then query and reject recovery after process loss.

    Args:
        folder: Isolated persistent database and restart observations.
        action: Seed a historical fixture or rebuild its public service.
        runner: Previously admitted executor without a task result protocol.
    """
    db = SQLiteDatabase(str(folder / "history.db"))
    config = {"dify_api_type": "chat"}

    async def inventory():
        return {
            "profiles": [
                {"id": "default", "runner_type": runner, "runner_config": config}
            ],
            "models": [],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    def forbidden_dispatch(owner):
        """Expose any incorrect attempt to invoke the external boundary.

        Args:
            owner: Actual initiating user.
        """
        with (folder / "dispatch.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps({"owner": owner}) + "\n")
        raise AssertionError("Unsupported historical task must not dispatch")

    roles = RoleAssetService(db, catalog_loader=inventory)
    service = WorkflowService(db, roles, ports_factory=forbidden_dispatch)
    try:
        if action == "seed":
            role = await roles.save_role(
                "alice",
                {
                    "definition": {
                        "name": "Historical researcher",
                        "prompt": "Check evidence.",
                    }
                },
            )
            role = await roles.resolve_role("alice", role["role_id"], 1)
            asset = await service.save_asset(
                "alice",
                {
                    "definition": {
                        "name": "Old task",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "Check evidence.",
                    }
                },
            )
            snapshot = {
                "engine": "single_task_v2",
                "usage_protocol": 1,
                "asset_id": asset["asset_id"],
                "asset_version": 1,
                "definition": asset["definition"],
                "role": role,
                "effective": role["effective"],
                "profile_id": "default",
                "profile": {"agent_runner": {"runner_type": runner, "config": config}},
                "persona": None,
                "member": {
                    "member_id": "member-original",
                    "session_id": "session-original",
                    "conversation_id": "conversation-original",
                    "umo": "webchat:FriendMessage:session-original",
                    "name": "Historical researcher",
                    "system_prompt": "Check evidence.",
                },
                "task": "Check evidence.",
                "inputs": {},
                "input_artifacts": [],
                "source": "dashboard",
                "initiator": "alice",
            }
            state = {
                "scope_id": "scope-original",
                "task_id": "task-original",
                "attempt_id": "attempt-original",
                "message_id": "message-original",
                "safe_to_finish": False,
                "reason": None,
                "response": None,
            }
            binding = NodeExecutionBinding(
                run_id="run-original",
                team_id=asset["asset_id"],
                member_id="member-original",
                node_id=None,
                umo=snapshot["member"]["umo"],
                owner_username="alice",
                config_id="default",
            )
            checkpoint = RemoteAgentCheckpoint(
                binding,
                snapshot["profile"]["agent_runner"],
                ProviderRequest(prompt="Check evidence."),
            )
            checkpoint.data.update(phase="remote_running", model_calls=1, steps_used=1)
            checkpoint.data["remote"]["identity"] = {"external_run_id": "original"}
            # Fixture setup represents records written by the pre-admission engine.
            # All assertions below use the public service projection after restart.
            async with db.get_db() as session:
                session.add(
                    AgentWorkflowRun(
                        run_id="run-original",
                        owner_username="alice",
                        snapshot=snapshot,
                        state=state,
                    )
                )
                session.add(
                    AgentWorkflowCheckpoint(
                        run_id="run-original",
                        attempt_id="attempt-original",
                        phase="remote_running",
                        invocation_id=checkpoint.data["invocation_id"],
                        data=checkpoint.data,
                    )
                )
                await session.commit()
            (folder / "before.json").write_text(
                json.dumps(await service.get_run("alice", "run-original")),
                encoding="utf-8",
            )
            (folder / "kill-ready.json").write_text("ready", encoding="utf-8")
            await asyncio.Future()
        await service.boot_sweep()
        before = await service.get_run("alice", "run-original")
        error = None
        try:
            await service.recover_run("alice", "run-original")
        except RoleAssetError as exc:
            error = {"status": exc.status, "message": str(exc), "fields": exc.fields}
        after = await service.get_run("alice", "run-original")
        (folder / "recovered.json").write_text(
            json.dumps({"before": before, "after": after, "error": error}),
            encoding="utf-8",
        )
    finally:
        await service.shutdown()
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_worker(Path(sys.argv[1]), sys.argv[2], sys.argv[3]))
