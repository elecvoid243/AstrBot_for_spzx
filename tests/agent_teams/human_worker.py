"""Human persistence faults hosted by the real main-pipeline checkpoint worker."""

import asyncio
import json
import time
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.orm import Session

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.po import AgentWorkflowEvent, AgentWorkflowRun


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
    shutdown_task = None

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
                "human_approval_pre": "human.answered",
                "human_approval_post": "human.answered",
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
        nonlocal shutdown_task
        acquired = scenario in {
            "human_consume_owned_post",
            "human_consume_owned_shutdown",
        } and any(
            isinstance(row, AgentWorkflowRun)
            and row.snapshot.get("node_id") == "write"
            and row.state.get("execution_id")
            for row in session.identity_map.values()
        )
        if acquired and scenario == "human_consume_owned_shutdown":
            if shutdown_task is None:
                shutdown_task = asyncio.create_task(service.shutdown())
            return
        if acquired or session.info.pop("human_stop", None):
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
        if scenario.startswith("human_approval"):
            asset = await service.save_asset(
                "alice",
                {
                    "definition": {
                        "name": "Exact version approval",
                        "max_parallel": 1,
                        "tasks": [
                            {
                                "id": "write",
                                "name": "Report",
                                "member": "writer",
                                "role_id": role["role_id"],
                                "role_version": 1,
                                "task": "Read evidence for Asia and submit a report.",
                            },
                            {
                                "id": "approval",
                                "name": "Approval",
                                "human": {
                                    "kind": "approval",
                                    "question": "Approve report v1?",
                                    "artifact_task_id": "write",
                                },
                            },
                        ],
                        "edges": [{"source": "write", "target": "approval"}],
                    }
                },
            )
        if "wait_" in scenario:
            event.listen(Session, "before_flush", stop)
            event.listen(Session, "after_commit", committed)
        run = await service.start_run("alice", asset["asset_id"], {"version": 1})
        if scenario.startswith("human_approval"):
            for _ in range(1500):
                run = await service.get_run("alice", run["run_id"])
                if run["todos"]:
                    break
                await asyncio.sleep(0.01)
            assert run["todos"][0]["artifact_ref"]["version"] == 1
            await service.revise_artifact(
                "alice",
                run["todos"][0]["artifact_ref"]["artifact_id"],
                {
                    "request_id": "v2",
                    "expected_version": 1,
                    "content": {"text": "Unapproved version two"},
                },
            )
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
                {"request_id": "answer", "action": "approve"}
                if scenario.startswith("human_approval")
                else {"request_id": "answer", "action": "answer", "answer": "Asia"},
            )
            if "consume_" in scenario:
                await service.control_run(
                    "alice",
                    run["run_id"],
                    {"request_id": "continue", "action": "continue"},
                )
                if scenario == "human_consume_owned_post":
                    await asyncio.Future()
                if scenario == "human_consume_owned_shutdown":
                    for _ in range(1500):
                        if shutdown_task is not None:
                            break
                        await asyncio.sleep(0.01)
                    assert shutdown_task is not None
                    await shutdown_task
                    (folder / "kill-ready.json").write_text("ready", encoding="utf-8")
                    return
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
        answer = (
            {"request_id": "answer", "action": "approve"}
            if todo["kind"] == "approval"
            else {"request_id": "answer", "action": "answer", "answer": "Asia"}
        )
        await service.answer_todo(
            "alice",
            run_id,
            todo["todo_id"],
            answer,
        )
        await service.answer_todo(
            "alice",
            run_id,
            todo["todo_id"],
            answer,
        )
        await service.control_run(
            "alice", run_id, {"request_id": "continue", "action": "continue"}
        )
        for _ in range(1500):
            current = await service.get_run("alice", run_id)
            if current["status"] != "running":
                break
            await asyncio.sleep(0.01)
        exported = None
        if todo["kind"] == "approval":
            from astrbot.core.agent_teams.assets import RoleAssetError

            exported = await service.export_artifact("alice", **todo["artifact_ref"])
            try:
                await service.export_artifact(
                    "alice", todo["artifact_ref"]["artifact_id"], 2
                )
            except RoleAssetError as exc:
                assert exc.status == 409
            else:
                raise AssertionError("The revised version must not inherit approval")
        if scenario in {"human_consume_owned_post", "human_consume_owned_shutdown"}:
            from astrbot.core.agent_teams.assets import RoleAssetError

            old_task = before["tasks"][1]
            try:
                await service.submit_result(
                    "alice",
                    old_task["run_id"],
                    old_task["state"]["attempt_id"],
                    {"text": "Late original executor"},
                    execution_id=old_task["state"]["execution_id"],
                )
            except RoleAssetError as exc:
                assert exc.status == 409
            else:
                raise AssertionError("The original executor must remain fenced")
        (folder / "recovered.json").write_text(
            json.dumps({"before": before, "after": current, "exported": exported}),
            encoding="utf-8",
        )
