"""Nested scope acceptance reuses the established real AstrBot pipeline worker."""

import asyncio
import json
import time
from pathlib import Path

from sqlalchemy import event as sql_event
from sqlalchemy.orm import Session

from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.po import AgentWorkflowCheckpoint, AgentWorkflowEvent


async def run_child_scenario(
    folder: Path,
    action: str,
    scenario: str,
    service: WorkflowService,
    roles: RoleAssetService,
) -> None:
    """Kill or restore declared child calls through public services and real SQLite.

    Args:
        folder: Persistent database and independent worker communication files.
        action: Start or recover the saved execution.
        scenario: A selected scope or pipeline persistence boundary.
        service: Actual shared service connected to the main pipeline.
        roles: Public role service with real local bindings.
    """
    if action == "start":

        def after_flush(session, flush_context):
            """Mark or stop at an actual uncommitted transaction boundary.

            Args:
                session: Real SQLAlchemy transaction.
                flush_context: SQLAlchemy flush context.
            """
            for row in [*session.new, *session.dirty]:
                selected = isinstance(row, AgentWorkflowEvent) and (
                    scenario.startswith("child_create_")
                    and row.kind == "scope.started"
                    or scenario.startswith("child_complete_")
                    and row.kind == "run.completed"
                    and row.run_id != session.info.get("root_id")
                    or scenario.startswith("child_return_")
                    and row.kind == "child.returned"
                )
                selected |= (
                    isinstance(row, AgentWorkflowCheckpoint)
                    and scenario == "child_tool_result"
                    and row.phase == "tool_result"
                    and row.data.get("response", {}).get("id") == "child-effect-first"
                )
                selected |= (
                    isinstance(row, AgentWorkflowCheckpoint)
                    and scenario == "child_pause"
                    and row.phase == "tool_result"
                    and row.data.get("response", {}).get("id") == "child-effect-first"
                )
                selected |= (
                    isinstance(row, AgentWorkflowEvent)
                    and scenario == "child_cancel"
                    and row.kind == "run.cancel"
                )
                if (
                    isinstance(row, AgentWorkflowEvent)
                    and row.kind == "run.started"
                    and row.data.get("task_runs")
                ):
                    session.info["root_id"] = row.run_id
                    (folder / "child-run.json").write_text(
                        json.dumps({"run_id": row.run_id}), encoding="utf-8"
                    )
                if not selected:
                    continue
                if scenario.endswith("_pre"):
                    (folder / "kill-ready.json").write_text(
                        "uncommitted", encoding="utf-8"
                    )
                    while True:
                        time.sleep(0.1)
                session.info["child_boundary"] = True

        def after_commit(session):
            """Stop after commit and before any dependent execution or handoff.

            Args:
                session: Completed actual storage transaction.
            """
            if session.info.pop("child_boundary", False):
                (folder / "kill-ready.json").write_text("committed", encoding="utf-8")
                while True:
                    time.sleep(0.1)

        sql_event.listen(Session, "after_flush", after_flush)
        sql_event.listen(Session, "after_commit", after_commit)
        writer = (await roles.list_roles("alice"))["roles"][0]
        writer = await roles.save_role(
            "alice",
            {
                "definition": {
                    **writer["definition"],
                    "max_steps": 8,
                    "max_instances": 4,
                },
                "installation": writer["installation"],
            },
        )
        coordinator = await roles.save_role(
            "alice",
            {
                "definition": {
                    **writer["definition"],
                    "name": "Child coordinator",
                    "prompt": "Coordinate only the declared child inputs.",
                    "max_steps": 30,
                },
                "installation": writer["installation"],
            },
        )
        team = await service.save_asset(
            "alice",
            {
                "definition": {
                    "kind": "autonomous",
                    "name": "Pinned independent research team with a long name for narrow screens",
                    "coordinator": {
                        "role_id": coordinator["role_id"],
                        "role_version": 1,
                    },
                    "allowed_roles": [
                        {"role_id": writer["role_id"], "role_version": 1}
                    ],
                    "required_inputs": ["goal", "notes"],
                    "max_parallel": 1,
                    **(
                        {"approval_question": "Approve the child team's exact version?"}
                        if scenario == "child_human"
                        else {}
                    ),
                }
            },
        )
        asset = await service.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Workflow calling a fixed team at root concurrency one",
                    "required_inputs": ["goal", "notes"],
                    "max_parallel": 1,
                    "tasks": [
                        {
                            "id": "call",
                            "name": "Research child team with a deliberately long name",
                            "team": {"asset_id": team["asset_id"], "version": 1},
                            "inputs": {
                                "goal": {"input": "goal"},
                                "notes": {"input": "notes"},
                            },
                        },
                        {
                            "id": "independent",
                            "name": "Independent parent evidence",
                            "member": "parent",
                            "role_id": writer["role_id"],
                            "role_version": 1,
                            "task": "Parent independent PRIVATE_PARENT_CONTEXT",
                        },
                        {
                            "id": "dependent",
                            "name": "Read the declared child output",
                            "member": "parent",
                            "role_id": writer["role_id"],
                            "role_version": 1,
                            "task": "Parent dependent",
                            "inputs": {"report": {"task_id": "call", "field": "text"}},
                        },
                    ],
                    "edges": [{"source": "call", "target": "dependent"}],
                }
            },
        )
        (folder / "child-assets.json").write_text(
            json.dumps({"asset_id": asset["asset_id"], "team_id": team["asset_id"]}),
            encoding="utf-8",
        )
        run = await service.start_run(
            "alice",
            asset["asset_id"],
            {
                "version": 1,
                "inputs": {
                    "goal": "研究 {notes}\nLiteral braces",
                    "notes": '中文\n{"year": 2025}',
                },
            },
        )
        run_id = run["run_id"]
    else:
        run_id = json.loads((folder / "child-run.json").read_text(encoding="utf-8"))[
            "run_id"
        ]
        before = await service.get_run("alice", run_id)
        await service.boot_sweep()
        if scenario == "child_pause":
            paused = await service.get_run("alice", run_id)
            assert paused["control"]["status"] == "paused"
            assert all(
                task["control"]["root_run_id"] == run_id
                for scope in paused["scopes"]
                for task in scope["tasks"]
            )
            await service.control_run(
                "alice", run_id, {"request_id": "continue-child", "action": "continue"}
            )
        if scenario == "child_human":
            await service.control_run(
                "alice", run_id, {"request_id": "pause-human", "action": "pause"}
            )
            answered = await service.answer_todo(
                "alice",
                run_id,
                before["todos"][0]["todo_id"],
                {"request_id": "child-approval", "action": "approve"},
            )
            assert answered["tasks"][0]["status"] == "waiting_child"
            assert answered["todos"][0]["status"] == "answered"
            await service.control_run(
                "alice", run_id, {"request_id": "continue-human", "action": "continue"}
            )
        unknown_rejections = []
        if scenario == "child_unknown":
            interrupted = await service.get_run("alice", run_id)
            for scope in interrupted["scopes"]:
                for task in scope["tasks"]:
                    if any(
                        call["status"] == "unknown"
                        and call["name"] == "external_evidence"
                        for call in task["tool_calls"]
                    ):
                        try:
                            await service.recover_run("alice", task["run_id"])
                        except RoleAssetError as exc:
                            unknown_rejections.append(exc.status)
        # Edits after process loss must not replace any referenced launch version.
        original = await service.get_asset("alice", before["snapshot"]["asset_id"])
        await service.save_asset(
            "alice",
            {
                "expected_version": original["version"],
                "definition": {**original["definition"], "name": "Edited after crash"},
            },
            original["asset_id"],
        )
        for _ in range(800):
            current = await service.get_run("alice", run_id)
            tasks = [
                *current["tasks"],
                *[
                    task
                    for scope in current.get("scopes", [])
                    for task in scope["tasks"]
                ],
            ]
            for task in tasks:
                if (
                    scenario != "child_unknown"
                    and task["status"] in {"interrupted", "failed", "blocked"}
                    and task["recovery"]["available"]
                ):
                    try:
                        await service.recover_run("alice", task["run_id"])
                    except RoleAssetError:
                        pass
            if current["status"] == "completed":
                break
            if scenario == "child_unknown" and current["status"] == "blocked":
                await service.control_run(
                    "alice", run_id, {"request_id": "pause-unknown", "action": "pause"}
                )
                await service.control_run(
                    "alice",
                    run_id,
                    {"request_id": "continue-unknown", "action": "continue"},
                )
                current = await service.get_run("alice", run_id)
                break
            if scenario == "child_cancel" and current["status"] == "cancelled":
                break
            await asyncio.sleep(0.02)
        (folder / "recovered.json").write_text(
            json.dumps(
                {
                    "before": before,
                    "after": current,
                    "unknown_rejections": unknown_rejections,
                }
            ),
            encoding="utf-8",
        )
        return
    for _ in range(800):
        current = await service.get_run("alice", run_id)
        if current["status"] != "running":
            break
        if scenario == "child_human" and current["todos"]:
            (folder / "kill-ready.json").write_text("human", encoding="utf-8")
            await asyncio.Future()
        await asyncio.sleep(0.02)
    (folder / "completed.json").write_text(
        json.dumps({"after": current}), encoding="utf-8"
    )
