"""Human persistence faults hosted by the real main-pipeline checkpoint worker."""

import asyncio
import json
import time
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.orm import Session

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.po import AgentWorkflowEvent


async def run_human_scenario(
    folder: Path,
    action: str,
    scenario: str,
    service: WorkflowService,
    roles: RoleAssetService,
) -> None:
    """Exercise human boundaries without replacing scheduling or persistence.

    Args:
        folder: Isolated durable database and public observation files.
        action: Start or rebuild independently terminated services.
        scenario: Waiting, answering or consumption transaction boundary.
        service: Actual public workflow service connected to the main pipeline.
        roles: Actual role assets bound to the externally recorded model/tool.
    """

    def stop(session, flush_context, instances):
        """Hold before a selected real transaction is committed.

        Args:
            session: Real persistent session.
            flush_context: SQLAlchemy flush context.
            instances: Optional explicit flush list.
        """
        for row in list(session.new):
            if isinstance(row, AgentWorkflowEvent) and row.kind == {
                "human_wait_pre": "human.waiting",
                "human_wait_post": "human.waiting",
                "human_answer_pre": "human.answered",
                "human_answer_post": "human.answered",
                "human_consume_pre": "human.consumed",
                "human_consume_post": "human.consumed",
            }.get(scenario):
                if scenario.endswith("pre"):
                    (folder / "kill-ready.json").write_text("ready", encoding="utf-8")
                    while True:
                        time.sleep(1)
                session.info["human_stop"] = True

    def committed(session):
        """Hold after the selected real transaction commits.

        Args:
            session: Real persistent session.
        """
        if session.info.pop("human_stop", None):
            (folder / "kill-ready.json").write_text("ready", encoding="utf-8")
            while True:
                time.sleep(1)

    if action == "start":
        role = await roles.save_role(
            "alice",
            {
                "definition": {
                    "name": "Writer",
                    "prompt": "Use the verified answer.",
                    "tools": ["evidence"],
                    "max_steps": 4,
                },
                "installation": {
                    "bindings": {
                        "model": "model-original",
                        "evidence": "external_evidence",
                    }
                },
            },
        )
        asset = await service.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Persistent human request",
                    "max_parallel": 1,
                    "tasks": [
                        {
                            "id": "question",
                            "name": "Region question",
                            "human": {
                                "kind": "question",
                                "question": "Which region?",
                                "choices": ["Asia", "Europe"],
                            },
                        },
                        {
                            "id": "write",
                            "name": "Report",
                            "member": "writer",
                            "role_id": role["role_id"],
                            "role_version": 1,
                            "task": "Read evidence for {region} and submit a report.",
                            "inputs": {
                                "region": {"task_id": "question", "field": "text"}
                            },
                        },
                    ],
                    "edges": [{"source": "question", "target": "write"}],
                }
            },
        )
        if "wait_" in scenario:
            event.listen(Session, "before_flush", stop)
            event.listen(Session, "after_commit", committed)
        run = await service.start_run("alice", asset["asset_id"], {"version": 1})
        if scenario == "human_shutdown_wait":
            (folder / "kill-ready.json").write_text("ready", encoding="utf-8")
            return
        if "wait_" not in scenario:
            await service.control_run(
                "alice", run["run_id"], {"request_id": "pause", "action": "pause"}
            )
            if scenario != "human_shutdown":
                event.listen(Session, "before_flush", stop)
                event.listen(Session, "after_commit", committed)
            await service.answer_todo(
                "alice",
                run["run_id"],
                run["todos"][0]["todo_id"],
                {"request_id": "answer", "action": "answer", "answer": "Asia"},
            )
            if "consume_" in scenario:
                await service.control_run(
                    "alice",
                    run["run_id"],
                    {"request_id": "continue", "action": "continue"},
                )
            elif scenario == "human_shutdown":
                (folder / "kill-ready.json").write_text("ready", encoding="utf-8")
                return
    else:
        runs = (await service.list_runs("alice"))["runs"]
        assert len(runs) == 1
        run_id = runs[0]["run_id"]
        before = await service.get_run("alice", run_id)
        await service.boot_sweep()
        current = await service.get_run("alice", run_id)
        if not current["todos"]:
            await service.control_run(
                "alice", run_id, {"request_id": "wake", "action": "continue"}
            )
            current = await service.get_run("alice", run_id)
        todo = current["todos"][0]
        await service.answer_todo(
            "alice",
            run_id,
            todo["todo_id"],
            {"request_id": "answer", "action": "answer", "answer": "Asia"},
        )
        await service.answer_todo(
            "alice",
            run_id,
            todo["todo_id"],
            {"request_id": "answer", "action": "answer", "answer": "Asia"},
        )
        await service.control_run(
            "alice", run_id, {"request_id": "continue", "action": "continue"}
        )
        for _ in range(1500):
            current = await service.get_run("alice", run_id)
            if current["status"] != "running":
                break
            await asyncio.sleep(0.01)
        (folder / "recovered.json").write_text(
            json.dumps({"before": before, "after": current}), encoding="utf-8"
        )
