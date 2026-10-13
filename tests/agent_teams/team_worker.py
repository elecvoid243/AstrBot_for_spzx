"""Autonomous acceptance through the existing real Agent pipeline worker."""

import asyncio
import json
import time
from pathlib import Path

from sqlalchemy import event as sql_event
from sqlalchemy import text
from sqlalchemy.orm import Session

from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.po import AgentWorkflowCheckpoint, AgentWorkflowEvent


async def run_team_scenario(
    folder: Path,
    action: str,
    scenario: str,
    service: WorkflowService,
    roles: RoleAssetService,
) -> None:
    """Create or recover a team using real persistence and external model responses.

    Args:
        folder: Shared durable storage for independent process restarts.
        action: Initial start or recovery.
        scenario: Selected committed failure boundary, or normal pipeline exercise.
        service: Existing public runtime with the actual main pipeline connected.
        roles: Existing role assets with real providers and configuration.
    """
    if action == "start":

        def after_flush(session, flush_context):
            """Select an actual durable boundary for external termination.

            Args:
                session: Real storage transaction.
                flush_context: SQLAlchemy flush context.
            """
            for row in [*session.new, *session.dirty]:
                if isinstance(row, AgentWorkflowEvent) and row.kind == {
                    "team_create": "team.create_member",
                    "team_dispatch": "team.dispatch",
                    "team_wait": "team.wait",
                    "team_inputs": "team.wait",
                }.get(scenario):
                    session.info["team_boundary"] = row.run_id
                if (
                    isinstance(row, AgentWorkflowCheckpoint)
                    and scenario == "team_tool_result"
                    and row.phase == "tool_result"
                    and row.data.get("response", {}).get("id") == "team-create"
                ):
                    session.info["team_boundary"] = row.run_id
                if (
                    isinstance(row, AgentWorkflowEvent)
                    and scenario == "team_finish"
                    and row.kind == "result.accepted"
                ):
                    session.info["team_boundary"] = row.run_id

        def after_commit(session):
            """Expose committed state then await forced process termination.

            Args:
                session: Completed transaction.
            """
            if session.info.pop("team_boundary", None):
                (folder / "kill-ready.json").write_text("ready", encoding="utf-8")
                while True:
                    time.sleep(1)

        sql_event.listen(Session, "after_flush", after_flush)
        sql_event.listen(Session, "after_commit", after_commit)
        writer = (await roles.list_roles("alice"))["roles"][0]
        writer = await roles.save_role(
            "alice",
            {
                "definition": {
                    **writer["definition"],
                    "name": "Researcher",
                    "max_instances": 2,
                    "max_parallel": 1,
                },
                "installation": writer["installation"],
            },
        )
        coordinator = await roles.save_role(
            "alice",
            {
                "definition": {
                    **writer["definition"],
                    "name": "Coordinator",
                    "prompt": "Coordinate the research team.",
                    "max_steps": 1 if scenario == "team_steps" else 30,
                    "max_instances": 1,
                    **({"tool_call_timeout": 0.5} if scenario == "team_slow" else {}),
                },
                "installation": writer["installation"],
            },
        )
        asset = await service.save_asset(
            "alice",
            {
                "definition": {
                    "kind": "autonomous",
                    "name": "Autonomous research team with a deliberately long name to verify wrapping",
                    "coordinator": {
                        "role_id": coordinator["role_id"],
                        "role_version": 1,
                    },
                    "allowed_roles": [
                        {"role_id": writer["role_id"], "role_version": 1}
                    ],
                    "max_parallel": 1,
                    "max_members": 3,
                    "max_tasks": 5,
                    "required_inputs": ["goal", "constraints"]
                    if scenario == "team_inputs"
                    else ["goal"],
                }
            },
        )
        if scenario == "team_save_failure":
            async with service.db.engine.begin() as connection:
                await connection.execute(
                    text(
                        "CREATE TRIGGER reject_team_decision BEFORE INSERT ON agent_workflow_events WHEN NEW.kind = 'team.create_member' BEGIN SELECT RAISE(ABORT, 'team storage unavailable'); END"
                    )
                )
        run = await service.start_run(
            "alice",
            asset["asset_id"],
            {
                "version": 1,
                "inputs": {
                    "goal": "Research energy with two independent writers, then revise with the first writer.",
                    **(
                        {
                            "constraints": 'ONLY_USE_2025_DATA\nKeep {"year": 2025} and {goal} literally.'
                        }
                        if scenario == "team_inputs"
                        else {}
                    ),
                },
            },
        )
        (folder / "team-run.json").write_text(
            json.dumps({"run_id": run["run_id"]}), encoding="utf-8"
        )
        # Change local resources after launch. Dynamic creation and recovery still
        # use the original role, profile and persona snapshots.
        await roles.save_role(
            "alice",
            {
                "definition": {
                    **writer["definition"],
                    "prompt": "Edited writer prompt",
                },
                "installation": writer["installation"],
                "expected_version": 1,
            },
            writer["role_id"],
        )
    else:
        run_id = json.loads((folder / "team-run.json").read_text(encoding="utf-8"))[
            "run_id"
        ]
        before = await service.get_run("alice", run_id)
        if scenario == "team_inputs":
            original = await service.get_asset(
                "alice",
                before["snapshot"]["asset_id"],
                before["snapshot"]["asset_version"],
            )
            await service.save_asset(
                "alice",
                {
                    "definition": {
                        **original["definition"],
                        "required_inputs": ["goal", "replacement"],
                    },
                    "expected_version": 1,
                },
                original["asset_id"],
            )
        if scenario == "team_save_failure":
            async with service.db.engine.begin() as connection:
                await connection.execute(
                    text("DROP TRIGGER IF EXISTS reject_team_decision")
                )
        await service.boot_sweep()
        # Recovery remains an explicit same-Attempt operation on the shared API.
        for _ in range(500):
            current = await service.get_run("alice", run_id)
            for task in current["tasks"]:
                if (
                    task["status"] in {"interrupted", "failed", "blocked"}
                    and task["recovery"]["available"]
                ):
                    try:
                        await service.recover_run("alice", task["run_id"])
                    except RoleAssetError:
                        pass
            if current["status"] == "completed":
                break
            if scenario == "team_unknown" and current["status"] == "blocked":
                break
            await asyncio.sleep(0.02)
        (folder / "team-evidence.json").write_text(
            json.dumps({"before": before, "after": current}), encoding="utf-8"
        )
        return
    for _ in range(1000):
        current = await service.get_run("alice", run["run_id"])
        if current["status"] != "running":
            break
        await asyncio.sleep(0.02)
    if scenario in {"team_pipeline", "team_slow", "team_steps"}:
        (folder / "team-evidence.json").write_text(
            json.dumps({"after": current}), encoding="utf-8"
        )
        return
    if scenario == "team_save_failure":
        (folder / "kill-ready.json").write_text("ready", encoding="utf-8")
        await asyncio.Future()
