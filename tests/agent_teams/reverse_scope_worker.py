"""Reverse scope fault scenarios reuse the actual AstrBot checkpoint pipeline."""

import asyncio
import json
import time
from pathlib import Path

from sqlalchemy import event as sql_event
from sqlalchemy.orm import Session

from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.po import AgentWorkflowCheckpoint, AgentWorkflowEvent


async def run_reverse_scenario(
    folder: Path,
    action: str,
    scenario: str,
    service: WorkflowService,
    roles: RoleAssetService,
) -> None:
    """Observe public reverse calls around real SQLite transaction boundaries.

    Args:
        folder: Independent persistence and worker communication directory.
        action: Start or recover the original run.
        scenario: Selected durable decision, child or external effect boundary.
        service: Real shared runtime attached to the AstrBot main pipeline.
        roles: Actual asset and local resource service.
    """
    if action == "start":

        def after_flush(session, flush_context):
            """Stop before commit or select a committed boundary for process loss.

            Args:
                session: Real SQLAlchemy transaction.
                flush_context: SQLAlchemy flush context.
            """
            for row in [*session.new, *session.dirty]:
                if (
                    isinstance(row, AgentWorkflowEvent)
                    and row.kind == "run.started"
                    and row.data.get("task_runs")
                ):
                    session.info["reverse_root"] = row.run_id
                    (folder / "reverse-run.json").write_text(
                        json.dumps({"run_id": row.run_id}), encoding="utf-8"
                    )
                selected = isinstance(row, AgentWorkflowEvent) and (
                    scenario.startswith("reverse_call_")
                    and row.kind == "team.call_workflow"
                    or scenario.startswith("reverse_create_")
                    and row.kind == "scope.started"
                    or scenario.startswith("reverse_complete_")
                    and row.kind == "run.completed"
                    and row.run_id != session.info.get("reverse_root")
                    or scenario.startswith("reverse_return_")
                    and row.kind == "child.returned"
                    or scenario == "reverse_cancel"
                    and row.kind == "run.cancel"
                )
                selected |= (
                    isinstance(row, AgentWorkflowCheckpoint)
                    and scenario
                    in {
                        "reverse_tool_result",
                        "reverse_pause",
                        "reverse_coordinator_first",
                    }
                    and row.phase == "tool_result"
                    and row.data.get("response", {}).get("id") == "reverse-effect"
                )
                if not selected:
                    continue
                if scenario.endswith("_pre"):
                    (folder / "kill-ready.json").write_text(
                        "uncommitted", encoding="utf-8"
                    )
                    while True:
                        time.sleep(0.1)
                session.info["reverse_boundary"] = True

        def after_commit(session):
            """Stop after a chosen transaction commits, before its next side effect.

            Args:
                session: Completed actual storage transaction.
            """
            if session.info.pop("reverse_boundary", False):
                (folder / "kill-ready.json").write_text("committed", encoding="utf-8")
                while True:
                    time.sleep(0.1)

        writer = (await roles.list_roles("alice"))["roles"][0]
        writer = await roles.save_role(
            "alice",
            {
                "definition": {
                    **writer["definition"],
                    "max_steps": 8,
                    "max_instances": 5,
                },
                "installation": writer["installation"],
            },
        )
        coordinator = await roles.save_role(
            "alice",
            {
                "definition": {
                    **writer["definition"],
                    "name": "Workflow coordinator",
                    "prompt": "Coordinate only declared results.",
                    "max_steps": 30,
                },
                "installation": writer["installation"],
            },
        )
        contract = {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"file_id": {"type": "string"}},
                        "required": ["file_id"],
                    },
                },
            },
            "required": ["text", "files"],
        }
        child = await service.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Fixed research workflow with a long name for small screens",
                    "result_task_id": "evidence",
                    "required_inputs": ["topic", "evidence_text"],
                    "max_parallel": 1,
                    "tasks": [
                        {
                            "id": name,
                            "name": name,
                            "member": name,
                            "role_id": writer["role_id"],
                            "role_version": 1,
                            "task": "Reverse effect {topic} {evidence_text}"
                            if name == "evidence"
                            else "Reverse independent"
                            if name == "independent"
                            else "Reverse dependent {evidence}",
                            **(
                                {"output_contract": contract}
                                if name == "evidence"
                                else {}
                            ),
                            "inputs": {
                                "evidence": {"task_id": "evidence", "field": "text"}
                            }
                            if name == "dependent"
                            else {"topic": {"input": "topic"}, "evidence_text": {"input": "evidence_text"}} if name == "evidence" else {},
                        }
                        for name in ["evidence", "independent", "dependent"]
                    ]
                    + (
                        [
                            {
                                "id": "confirm",
                                "name": "Approve research",
                                "human": {
                                    "kind": "approval",
                                    "question": "Approve this exact workflow result?",
                                    "artifact_task_id": "evidence",
                                    "artifact_version": 1,
                                },
                            }
                        ]
                        if scenario == "reverse_human"
                        else []
                    ),
                    "edges": [{"source": "evidence", "target": "dependent"}]
                    + (
                        [{"source": "evidence", "target": "confirm"}]
                        if scenario == "reverse_human"
                        else []
                    ),
                }
            },
        )
        team = await service.save_asset(
            "alice",
            {
                "definition": {
                    "kind": "autonomous",
                    "name": "Team invoking a pinned workflow at concurrency one",
                    "coordinator": {
                        "id": "coordinator",
                        "role_id": coordinator["role_id"],
                        "role_version": 1,
                    },
                    "allowed_roles": [
                        {"id": "writer", "role_id": writer["role_id"], "role_version": 1}
                    ],
                    "allowed_workflows": [
                        {
                            "asset_id": child["asset_id"],
                            "version": 1,
                        }
                    ],
                    "max_parallel": 1,
                }
            },
        )
        (folder / "reverse-assets.json").write_text(
            json.dumps(
                {"asset_id": team["asset_id"], "workflow_id": child["asset_id"]}
            ),
            encoding="utf-8",
        )
        input_asset = await service.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Pinned input source",
                    "result_task_id": "source",
                    "tasks": [{"id": "source", "name": "Source", "member": "source", "role_id": writer["role_id"], "role_version": 1, "task": "Reverse input source", "output_contract": contract}],
                }
            },
        )
        source = await service.start_run(
            "alice", input_asset["asset_id"], {"version": 1}
        )
        for _ in range(1200):
            source = await service.get_run("alice", source["run_id"])
            if source["status"] != "running":
                break
            await asyncio.sleep(0.02)
        assert source["status"] == "completed", source
        pinned_input = {"reference": source["result_ref"], "content": source["result"]}
        (folder / "reverse-input.json").write_text(
            json.dumps(pinned_input), encoding="utf-8"
        )
        sql_event.listen(Session, "after_flush", after_flush)
        sql_event.listen(Session, "after_commit", after_commit)
        run = await service.start_run(
            "alice",
            team["asset_id"],
            {
                "version": 1,
                "inputs": {"goal": "PRIVATE_PARENT_GOAL"},
                "input_artifacts": [pinned_input["reference"]],
            },
        )
        run_id = run["run_id"]
        await service.revise_artifact(
            "alice",
            pinned_input["reference"]["artifact_id"],
            {
                "expected_version": 1,
                "request_id": "source-v2",
                "content": {**pinned_input["content"], "text": "Changed source v2"},
            },
        )
    else:
        run_id = json.loads((folder / "reverse-run.json").read_text(encoding="utf-8"))[
            "run_id"
        ]
        before = await service.get_run("alice", run_id)
        pinned_input = json.loads(
            (folder / "reverse-input.json").read_text(encoding="utf-8")
        )
        await service.revise_artifact(
            "alice",
            pinned_input["reference"]["artifact_id"],
            {
                "expected_version": 1,
                "request_id": "source-v2",
                "content": {**pinned_input["content"], "text": "Changed source v2"},
            },
        )
        # New role, asset and default-resource values cannot replace frozen state.
        assets = json.loads(
            (folder / "reverse-assets.json").read_text(encoding="utf-8")
        )
        original = await service.get_asset("alice", assets["workflow_id"])
        await service.save_asset(
            "alice",
            {
                "definition": {
                    **original["definition"],
                    "name": "Edited after process loss",
                },
                "expected_version": 1,
            },
            original["asset_id"],
        )
        role = (await roles.list_roles("alice"))["roles"][0]
        await roles.save_role(
            "alice",
            {
                "definition": {**role["definition"], "prompt": "CHANGED ROLE"},
                "installation": role["installation"],
                "expected_version": role["version"],
            },
            role["role_id"],
        )
        await service.boot_sweep()
        unknown_rejections = []
        if scenario == "reverse_coordinator_first":
            coordinator = (await service.get_run("alice", run_id))["tasks"][0]
            await service.recover_run("alice", coordinator["run_id"])
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                observed = await service.get_run("alice", run_id)
                if observed["tasks"][0]["status"] == "awaiting_result":
                    break
                await asyncio.sleep(0.02)
            assert observed["tasks"][0]["status"] == "awaiting_result", observed
            (folder / "parent-before-child.json").write_text(
                json.dumps(observed), encoding="utf-8"
            )
        if scenario == "reverse_pause":
            paused = await service.get_run("alice", run_id)
            assert paused["control"]["status"] == "paused"
            await service.control_run(
                "alice",
                run_id,
                {"action": "continue", "request_id": "reverse-continue"},
            )
        if scenario == "reverse_human":
            waiting = await service.get_run("alice", run_id)
            assert waiting["todos"][0]["status"] == "pending"
            assert waiting["todos"] == before["todos"]
            assert waiting["status"] != "completed"
            assert waiting["tasks"][1]["status"] == "waiting_child"
            assert waiting["tasks"][1]["result"] is None
            await service.control_run(
                "alice",
                run_id,
                {"action": "pause", "request_id": "reverse-pause-human"},
            )
            decision = {
                "action": "approve",
                "request_id": "reverse-answer",
                "artifact_ref": waiting["todos"][0]["artifact_ref"],
            }
            first = await service.answer_todo(
                "alice", run_id, waiting["todos"][0]["todo_id"], decision
            )
            repeated = await service.answer_todo(
                "alice", run_id, waiting["todos"][0]["todo_id"], decision
            )
            assert first["todos"] == repeated["todos"]
            assert first["todos"][0]["status"] == "answered"
            assert first["tasks"][1]["status"] == "waiting_child"
            await service.control_run(
                "alice",
                run_id,
                {"action": "continue", "request_id": "reverse-continue-human"},
            )
        if scenario == "reverse_unknown":
            for scope in (await service.get_run("alice", run_id))["scopes"]:
                for task in scope["tasks"]:
                    if any(call["status"] == "unknown" for call in task["tool_calls"]):
                        try:
                            await service.recover_run("alice", task["run_id"])
                        except RoleAssetError as exc:
                            unknown_rejections.append(exc.status)
            await service.control_run(
                "alice",
                run_id,
                {"action": "pause", "request_id": "reverse-unknown-pause"},
            )
            await service.control_run(
                "alice",
                run_id,
                {"action": "continue", "request_id": "reverse-unknown-continue"},
            )
        for _ in range(1200):
            current = await service.get_run("alice", run_id)
            for task in [
                *(task for scope in current["scopes"] for task in scope["tasks"]),
                *current["tasks"],
            ]:
                if (
                    scenario in {"reverse_tool_result", "reverse_pause"}
                    and task["node_id"] == "coordinator"
                    and any(
                        scope["status"] != "completed" for scope in current["scopes"]
                    )
                ):
                    # This scripted coordinator ends on attention. Complete the
                    # explicitly requested child recovery before resuming its wait.
                    continue
                if (
                    scenario not in {"reverse_unknown", "reverse_cancel"}
                    and task["status"] in {"interrupted", "failed", "blocked"}
                    and task["recovery"]["available"]
                ):
                    try:
                        await service.recover_run("alice", task["run_id"])
                    except RoleAssetError:
                        pass
            if (
                current["status"] == "completed"
                or scenario == "reverse_unknown"
                and current["status"] == "blocked"
                or scenario == "reverse_cancel"
                and current["status"] == "cancelled"
                or scenario == "reverse_coordinator_first"
                and current["scopes"][0]["status"] == "completed"
            ):
                break
            await asyncio.sleep(0.02)
        for _ in range(3):
            await service.advance_run("alice", run_id)
        current = await service.get_run("alice", run_id)
        unsubmitted = None
        recover_rejections = []
        if scenario == "reverse_coordinator_first":
            unsubmitted = current
            assert current["status"] == "blocked"
            assert current["tasks"][0]["status"] == "awaiting_result"
            coordinator = current["tasks"][0]
            try:
                await service.recover_run("alice", coordinator["run_id"])
            except RoleAssetError as exc:
                recover_rejections.append(exc.status)
            else:
                raise AssertionError("An unsubmitted result was recovered as success")
            (folder / "retry-parent.json").write_text(
                "explicit retry", encoding="utf-8"
            )
            await service.retry_task(
                "alice",
                coordinator["run_id"],
                {
                    "request_id": "retry-unsubmitted-parent",
                    "attempt_id": coordinator["state"]["attempt_id"],
                    "input_policy": "original",
                },
            )
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                current = await service.get_run("alice", run_id)
                if current["status"] == "completed":
                    break
                await asyncio.sleep(0.02)
            assert current["status"] == "completed", current
        file_verified = False
        if current["status"] == "completed":
            call = current["tasks"][1]
            source = current["scopes"][0]["tasks"][0]
            ref = call["result_ref"]
            file_id = call["result"]["files"][0]["file_id"]
            original_file = await service.get_file(
                "alice", **ref, file_id=file_id, download=False
            )
            assert original_file["content"].decode("utf-8") == source["result"]["text"]
            revision = {
                "expected_version": 1,
                "request_id": "retain-returned-file",
                "content": {**call["result"], "text": "Explicit revision"},
            }
            revised = await service.revise_artifact(
                "alice", ref["artifact_id"], revision
            )
            assert (
                await service.revise_artifact("alice", ref["artifact_id"], revision)
                == revised
            )
            assert (await service.read_artifact("alice", **ref))["content"] == call[
                "result"
            ]
            assert (
                await service.get_file(
                    "alice", ref["artifact_id"], 2, file_id, download=False
                )
            )["content"] == original_file["content"]
            private_call = next(
                call
                for call in source["tool_calls"]
                if call["call_id"] == "reverse-private-file"
            )
            private_file = json.loads(
                private_call["raw_results"][0]["content"][0]["text"]
            )["file_id"]
            for version in (1, 2):
                try:
                    await service.get_file("alice", ref["artifact_id"], version, private_file, download=False)
                except RoleAssetError as exc:
                    assert exc.status == 404
                else:
                    raise AssertionError("Unsubmitted child file was silently inherited")
            await service.revise_artifact(
                "alice",
                ref["artifact_id"],
                {
                    "expected_version": 2,
                    "request_id": "include-private-file",
                    "content": {"text": "Explicit revision", "files": [{"file_id": private_file}]},
                },
            )
            assert (await service.get_file("alice", ref["artifact_id"], 3, private_file, download=False))["content"] == b"UNSUBMITTED_PRIVATE"
            for username, version in (("alice", 1), ("alice", 2), ("other", 3)):
                try:
                    await service.get_file(username, ref["artifact_id"], version, private_file, download=False)
                except RoleAssetError as exc:
                    assert exc.status == 404
                else:
                    raise AssertionError("Private file crossed its version or owner boundary")
            file_verified = True
        (folder / "recovered.json").write_text(
            json.dumps(
                {
                    "before": before,
                    "after": current,
                    "unknown_rejections": unknown_rejections,
                    "file_verified": file_verified,
                    "unsubmitted": unsubmitted,
                    "recover_rejections": recover_rejections,
                }
            ),
            encoding="utf-8",
        )
        return
    for _ in range(1200):
        current = await service.get_run("alice", run_id)
        if current["status"] != "running":
            break
        if scenario == "reverse_human" and current["todos"]:
            (folder / "kill-ready.json").write_text("human", encoding="utf-8")
            await asyncio.Future()
        await asyncio.sleep(0.02)
    (folder / "completed.json").write_text(
        json.dumps({"after": current}), encoding="utf-8"
    )
