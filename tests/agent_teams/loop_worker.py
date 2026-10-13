"""Loop persistence boundaries exercised inside the actual main pipeline."""

import asyncio
import json
import time
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.orm import Session

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.po import (
    AgentWorkflowCheckpoint,
    AgentWorkflowEvent,
    AgentWorkflowRun,
)
from tests.agent_teams.test_workflow_loops import revision_definition


async def run_loop_scenario(
    folder: Path,
    action: str,
    scenario: str,
    service: WorkflowService,
    roles: RoleAssetService,
) -> None:
    """Terminate at the real iteration transaction and rebuild through public APIs.

    Args:
        folder: Isolated persistent worker evidence directory.
        action: Start or rebuild the independent worker process.
        scenario: Before/after persistence, pause, cancellation or round recovery.

    Returns:
        None after writing observable public snapshots for the parent.
    """
    if action == "start":
        role = await roles.save_role(
            "alice",
            {
                "definition": {
                    "name": "Revision role",
                    "prompt": "Follow the explicit task.",
                    "tools": ["evidence"],
                    "max_steps": 5,
                    "max_instances": 2,
                },
                "installation": {
                    "bindings": {
                        "model": "model-original",
                        "evidence": "external_evidence",
                    }
                },
            },
        )
        definition = revision_definition(role["role_id"])
        definition["tasks"][0]["task"] = "WRITE-ROUND {draft} FEEDBACK {feedback}"
        definition["tasks"][1]["task"] = "REVIEW-ROUND {draft}"
        definition["tasks"][-1] = {
            "id": "approve",
            "name": "Confirm reviewed version",
            "human": {
                "kind": "approval",
                "question": "Approve reviewed report",
                "artifact_task_id": "write",
            },
        }
        definition["edges"][-1]["target"] = "approve"
        asset = await service.save_asset("alice", {"definition": definition})

        def stop(session, flush_context, instances):
            """Select the production iteration transaction without changing its facts.

            Args:
                session: SQLAlchemy production transaction.
                flush_context: SQLAlchemy flush context.
                instances: Optional explicit flush instances.
            """
            for row in [*session.new, *session.dirty]:
                if scenario in {"loop_second_response", "loop_second_result"}:
                    if (
                        isinstance(row, AgentWorkflowCheckpoint)
                        and row.phase
                        == (
                            "response"
                            if scenario.endswith("response")
                            else "tool_result"
                        )
                        and row.data.get("response", {}).get("id") == "plan-write2"
                    ):
                        task = session.get(AgentWorkflowRun, row.run_id)
                        session.info["loop_boundary"] = task.snapshot["parent_run_id"]
                elif isinstance(row, AgentWorkflowEvent) and row.kind == "loop.started":
                    session.info["loop_boundary"] = row.run_id
                    if scenario == "loop_pre":
                        (folder / "kill-ready.json").write_text(
                            json.dumps({"run_id": row.run_id}), encoding="utf-8"
                        )
                        while True:
                            time.sleep(1)

        def committed(session):
            """Hold a committed round before any newly claimed model/tool work.

            Args:
                session: Transaction that saved the independent next-round tasks.
            """
            run_id = session.info.pop("loop_boundary", None)
            if run_id:
                (folder / "kill-ready.json").write_text(
                    json.dumps({"run_id": run_id}), encoding="utf-8"
                )
                while True:
                    time.sleep(1)

        event.listen(Session, "before_flush", stop)
        event.listen(Session, "after_commit", committed)
        await service.start_run(
            "alice",
            asset["asset_id"],
            {"version": 1, "inputs": {"draft": "seed", "feedback": "initial"}},
        )
        await asyncio.Future()
    else:
        run_id = json.loads((folder / "kill-ready.json").read_text(encoding="utf-8"))[
            "run_id"
        ]
        before = await service.get_run("alice", run_id)
        if scenario in {"loop_pause", "loop_cancel"}:
            await service.control_run(
                "alice",
                run_id,
                {
                    "request_id": "boundary-control",
                    "action": "pause" if scenario == "loop_pause" else "cancel",
                },
            )
        await service.boot_sweep()
        if scenario == "loop_cancel":
            after = await service.get_run("alice", run_id)
            (folder / "recovered.json").write_text(
                json.dumps({"before": before, "after": after}), encoding="utf-8"
            )
            return
        paused = None
        if scenario == "loop_pause":
            paused = await service.get_run("alice", run_id)
            await asyncio.sleep(0.1)
            assert not paused["todos"]
            await service.control_run(
                "alice",
                run_id,
                {"request_id": "boundary-continue", "action": "continue"},
            )
        for _ in range(2000):
            current = await service.get_run("alice", run_id)
            for task in current["tasks"]:
                if task["status"] == "interrupted" and task["recovery"]["available"]:
                    await service.recover_run("alice", task["run_id"])
            if current["todos"]:
                break
            await asyncio.sleep(0.01)
        assert current["todos"], current
        todo = current["todos"][0]
        await service.answer_todo(
            "alice",
            run_id,
            todo["todo_id"],
            {"request_id": "final-approval", "action": "approve"},
        )
        after = await service.get_run("alice", run_id)
        exported = await service.export_artifact("alice", **todo["artifact_ref"])
        (folder / "recovered.json").write_text(
            json.dumps(
                {
                    "before": before,
                    "paused": paused,
                    "after": after,
                    "exported": exported,
                }
            ),
            encoding="utf-8",
        )
