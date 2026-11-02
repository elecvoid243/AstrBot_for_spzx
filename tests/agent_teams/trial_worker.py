"""Run a draft trial in the existing real-pipeline process test environment."""

import asyncio
import json
import sys
import time
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.orm import Session

from astrbot.core.db.po import AgentWorkflowEvent
from tests.agent_teams import loop_worker
from tests.agent_teams.checkpoint_worker import run_worker
from tests.agent_teams.test_workflow_loops import revision_definition


async def run_trial_scenario(folder, action, scenario, service, roles):
    """Start or recover the trial at a committed loop boundary.

    Args:
        folder: Independent persistent database and evidence directory.
        action: Start or recover in a separate process.
        scenario: Pause or cancellation decision after termination.
        service: Real workflow service and main execution pipeline.
        roles: Real role asset service.
    """
    if scenario in {"loop_trial_full", "loop_trial_single", "loop_trial_partial"}:
        role = await roles.save_role(
            "alice",
            {
                "definition": {
                    "name": "Trial role",
                    "prompt": "Follow the task.",
                    "provider_id": "model-original",
                    "tools": ["external_evidence"],
                    "max_steps": 5,
                    "max_instances": 2,
                },
                "installation": {"profile_id": "default"},
            },
        )
        shared = {"member": "writer", "role_id": role["role_id"], "role_version": 1}
        definition = {
            "name": "Trial pipeline",
            "result_task_id": "b",
            "tasks": [
                {**shared, "id": "a", "name": "A", "task": "TRIAL-A"},
                {
                    **shared,
                    "id": "b",
                    "name": "B",
                    "task": "TRIAL-B {value}",
                    "inputs": {"value": {"task_id": "a"}},
                },
            ],
            "edges": [{"source": "a", "target": "b"}],
        }
        draft = await roles.create_draft(
            "alice", "workflow", {"definition": definition}
        )
        mode = scenario.removeprefix("loop_trial_")
        request = {"kind": "workflow", "revision": draft["revision"], "mode": mode}
        if mode != "full":
            request.update(
                node_id="b", sources={"a": {"content": {"text": "fixed boundary"}}}
            )
        run = await service.start_trial("alice", draft["asset_id"], request)
        for _ in range(2000):
            run = await service.get_run("alice", run["run_id"])
            if run["status"] != "running":
                break
            await asyncio.sleep(0.01)
        (folder / "completed.json").write_text(json.dumps(run), encoding="utf-8")
        return
    if action == "start":
        role = await roles.save_role(
            "alice",
            {
                "definition": {
                    "name": "Trial role",
                    "prompt": "Follow the task.",
                    "provider_id": "model-original",
                    "tools": ["external_evidence"],
                    "max_steps": 5,
                    "max_instances": 2,
                },
                "installation": {"profile_id": "default"},
            },
        )
        definition = revision_definition(role["role_id"])
        definition["tasks"][0]["task"] = "WRITE-ROUND {draft} FEEDBACK {feedback}"
        definition["tasks"][1]["task"] = "REVIEW-ROUND {draft}"
        definition["tasks"][0]["inputs"]["draft"] = {"task_id": "seed"}
        definition["required_inputs"] = ["feedback"]
        definition["tasks"].insert(
            0,
            {
                "id": "seed",
                "name": "Unselected seed",
                "member": "seed",
                "role_id": role["role_id"],
                "role_version": 1,
                "task": "Should not run",
            },
        )
        definition["edges"].insert(0, {"source": "seed", "target": "write"})
        draft = await roles.create_draft(
            "alice", "workflow", {"definition": definition}
        )

        def before_flush(session, flush_context, instances):
            for row in [*session.new, *session.dirty]:
                if isinstance(row, AgentWorkflowEvent) and row.kind == "loop.started":
                    session.info["trial_boundary"] = row.run_id

        def after_commit(session):
            run_id = session.info.pop("trial_boundary", None)
            if run_id:
                (folder / "kill-ready.json").write_text(
                    json.dumps({"run_id": run_id}), encoding="utf-8"
                )
                while True:
                    time.sleep(1)

        event.listen(Session, "before_flush", before_flush)
        event.listen(Session, "after_commit", after_commit)
        await service.start_trial(
            "alice",
            draft["asset_id"],
            {
                "kind": "workflow",
                "revision": draft["revision"],
                "mode": "partial",
                "node_id": "write",
                "inputs": {"feedback": "initial"},
                "sources": {"seed": {"content": {"text": "seed"}}},
            },
        )
        await asyncio.Future()
    else:
        run_id = json.loads((folder / "kill-ready.json").read_text(encoding="utf-8"))[
            "run_id"
        ]
        before = await service.get_run("alice", run_id)
        decision = "cancel" if scenario.endswith("cancel") else "pause"
        await service.control_run(
            "alice", run_id, {"request_id": "control", "action": decision}
        )
        await service.boot_sweep()
        controlled = await service.get_run("alice", run_id)
        if decision == "pause":
            await service.control_run(
                "alice", run_id, {"request_id": "continue", "action": "continue"}
            )
            for _ in range(2000):
                current = await service.get_run("alice", run_id)
                for task in current["tasks"]:
                    if (
                        task["status"] == "interrupted"
                        and task["recovery"]["available"]
                    ):
                        await service.recover_run("alice", task["run_id"])
                if current["status"] != "running":
                    break
                await asyncio.sleep(0.01)
        else:
            current = await service.get_run("alice", run_id)
        (folder / "recovered.json").write_text(
            json.dumps({"before": before, "controlled": controlled, "after": current}),
            encoding="utf-8",
        )


if __name__ == "__main__":
    loop_worker.run_loop_scenario = run_trial_scenario
    asyncio.run(run_worker(Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]))
