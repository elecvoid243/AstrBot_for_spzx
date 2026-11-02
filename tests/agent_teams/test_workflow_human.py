"""Durable human decisions through the public workflow application service."""

import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.db.po import AgentWorkflowEvent
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_question_releases_capacity_and_consumes_once_after_continue(
    workflow_env,
):
    service, role = workflow_env.service, workflow_env.role
    agent = {
        "id": "independent",
        "name": "Independent",
        "member": "writer",
        "role_id": role["role_id"],
        "role_version": 1,
        "task": "Independent",
    }
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Human input",
                "result_task_id": "dependent",
                "max_parallel": 1,
                "tasks": [
                    {
                        "id": "question",
                        "name": "Question",
                        "human": {
                            "kind": "question",
                            "question": "Which region?",
                            "choices": ["Asia", "Europe"],
                        },
                    },
                    agent,
                    {
                        **agent,
                        "id": "dependent",
                        "name": "Dependent",
                        "task": "Use {region}",
                        "inputs": {"region": {"task_id": "question", "field": "text"}},
                    },
                ],
                "edges": [{"source": "question", "target": "dependent"}],
            }
        },
    )
    run = await service.start_run("alice", asset["asset_id"], {"version": 1})
    for _ in range(200):
        run = await service.get_run("alice", run["run_id"])
        if run["tasks"][1]["status"] == "completed":
            break
        await asyncio.sleep(0.01)
    assert run["tasks"][0]["status"] == "waiting_human"
    assert run["tasks"][1]["status"] == "completed"
    assert run["tasks"][2]["status"] == "pending"
    todo = run["todos"][0]
    assert todo["question"] == "Which region?"
    assert todo["constraints"]["choices"] == ["Asia", "Europe"]
    with pytest.raises(RoleAssetError):
        await service.answer_todo(
            "bob",
            run["run_id"],
            todo["todo_id"],
            {
                "request_id": "answer",
                "action": "answer",
                "answer": "Asia",
            },
        )
    await service.control_run(
        "alice", run["run_id"], {"request_id": "pause", "action": "pause"}
    )
    answer = {"request_id": "answer", "action": "answer", "answer": "Asia"}
    saved = await service.answer_todo("alice", run["run_id"], todo["todo_id"], answer)
    assert saved["todos"][0]["status"] == "answered"
    assert saved["todos"][0]["consumed_at"] is None
    assert saved["tasks"][2]["status"] == "pending"
    await service.answer_todo("alice", run["run_id"], todo["todo_id"], answer)
    await service.control_run(
        "alice", run["run_id"], {"request_id": "continue", "action": "continue"}
    )
    for _ in range(200):
        run = await service.get_run("alice", run["run_id"])
        if run["status"] == "completed":
            break
        await asyncio.sleep(0.01)
    assert run["status"] == "completed"
    assert run["todos"][0]["consumed_at"] is not None
    assert run["tasks"][2]["snapshot"]["task"] == "Use Asia"
    await service.answer_todo("alice", run["run_id"], todo["todo_id"], answer)
    assert len([e for e in run["events"] if e["kind"] == "human.consumed"]) == 1


@pytest.mark.asyncio
async def test_approval_and_export_remain_bound_to_previewed_version(workflow_env):
    service, role = workflow_env.service, workflow_env.role
    ports = service.ports_factory("alice")

    async def collect(session_id, message_id, member_id):
        tool = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[0]
        await tool.call(None, result_json='{"text":"Complete approved report."}')
        return "Done", []

    service.ports_factory = lambda _: replace(ports, collect=collect)
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Report approval",
                "result_task_id": "approval",
                "tasks": [
                    {
                        "id": "report",
                        "name": "Report",
                        "member": "writer",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "Report",
                        "output_contract": {
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
                            "required": ["text"],
                        },
                    },
                    {
                        "id": "approval",
                        "name": "Approval",
                        "human": {
                            "kind": "approval",
                            "question": "Approve this report?",
                            "artifact_task_id": "report",
                        },
                    },
                ],
                "edges": [{"source": "report", "target": "approval"}],
            }
        },
    )
    run = await service.start_run("alice", asset["asset_id"], {"version": 1})
    for _ in range(200):
        run = await service.get_run("alice", run["run_id"])
        if run["todos"]:
            break
        await asyncio.sleep(0.01)
    todo = run["todos"][0]
    ref = todo["artifact_ref"]
    preview = await service.read_artifact("alice", **ref)
    file = await service.store_file(
        "alice", ref["artifact_id"], None, "revision.txt", b"Unapproved revision file"
    )
    await service.revise_artifact(
        "alice",
        ref["artifact_id"],
        {
            "expected_version": 1,
            "request_id": "v2",
            "content": {
                "text": "Unapproved revision",
                "files": [{"file_id": file["file_id"]}],
            },
        },
    )
    with pytest.raises(RoleAssetError):
        await service.export_artifact("alice", **ref)
    answered = await service.answer_todo(
        "alice",
        run["run_id"],
        todo["todo_id"],
        {"request_id": "approval-v1", "action": "approve", "artifact_ref": ref},
    )
    assert answered["status"] == "completed"
    assert await service.export_artifact("alice", **ref) == preview
    with pytest.raises(RoleAssetError):
        await service.export_artifact("alice", ref["artifact_id"], 2)
    with pytest.raises(RoleAssetError):
        await service.get_file("alice", ref["artifact_id"], 2, file["file_id"])
    assert (
        await service.get_file(
            "alice", ref["artifact_id"], 2, file["file_id"], download=False
        )
    )["content"] == b"Unapproved revision file"
    with pytest.raises(RoleAssetError):
        await service.export_artifact("bob", **ref)
    with pytest.raises(RoleAssetError):
        await service.answer_todo(
            "alice",
            run["run_id"],
            todo["todo_id"],
            {
                "request_id": "different",
                "action": "reject",
                "feedback": "No",
                "artifact_ref": ref,
            },
        )


@pytest.mark.asyncio
async def test_human_constraints_rejection_and_cancel_preserve_durable_history(
    workflow_env,
):
    service = workflow_env.service
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Question",
                "result_task_id": "q",
                "tasks": [
                    {
                        "id": "q",
                        "name": "Question",
                        "human": {
                            "kind": "question",
                            "question": "Choose",
                            "choices": ["A", "B"],
                            "max_length": 1,
                        },
                    }
                ],
            }
        },
    )
    run = await service.start_run(
        "alice", asset["asset_id"], {"version": 1, "source": "chat:alice"}
    )
    todo = run["todos"][0]
    for payload in [
        {"request_id": "invalid", "action": "answer", "answer": "C"},
        {"request_id": "invalid", "action": "answer", "answer": "AB"},
        {"request_id": "invalid", "action": "approve"},
        {"request_id": "invalid", "action": "reject"},
    ]:
        with pytest.raises(RoleAssetError):
            await service.answer_todo("alice", run["run_id"], todo["todo_id"], payload)
    rejection = {
        "request_id": "reject",
        "action": "reject",
        "feedback": "The question needs evidence.",
    }
    rejected = await service.answer_todo(
        "alice", run["run_id"], todo["todo_id"], rejection
    )
    assert rejected["status"] == "blocked"
    assert rejected["todos"][0]["status"] == "rejected"
    assert rejected["tasks"][0]["state"]["reason"] == "The question needs evidence."
    await service.control_run(
        "alice", run["run_id"], {"request_id": "cancel", "action": "cancel"}
    )
    repeated = await service.answer_todo(
        "alice", run["run_id"], todo["todo_id"], rejection
    )
    assert repeated["status"] == "cancelled"
    assert repeated["todos"] == rejected["todos"]


@pytest.mark.asyncio
async def test_duplicate_answer_and_root_control_compete_without_double_consumption(
    workflow_env,
):
    service = workflow_env.service
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Question",
                "result_task_id": "q",
                "tasks": [
                    {
                        "id": "q",
                        "name": "Question",
                        "human": {"kind": "question", "question": "Answer?"},
                    }
                ],
            }
        },
    )
    run = await service.start_run("alice", asset["asset_id"], {"version": 1})
    answer = {"request_id": "same", "action": "answer", "answer": "Yes"}
    await service.control_run(
        "alice", run["run_id"], {"request_id": "pause", "action": "pause"}
    )
    responses = await asyncio.gather(
        *[
            service.answer_todo(
                "alice", run["run_id"], run["todos"][0]["todo_id"], answer
            )
            for _ in range(4)
        ]
    )
    assert all(r["todos"][0]["status"] == "answered" for r in responses)
    await asyncio.gather(
        service.advance_run("alice", run["run_id"]),
        service.control_run(
            "alice", run["run_id"], {"request_id": "continue", "action": "continue"}
        ),
    )
    final = await service.get_run("alice", run["run_id"])
    assert final["status"] == "completed"
    assert len([e for e in final["events"] if e["kind"] == "human.answered"]) == 1
    assert len([e for e in final["events"] if e["kind"] == "human.consumed"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["human.answered", "human.consumed"])
async def test_storage_failure_keeps_response_boundary_truthful(workflow_env, boundary):
    service = workflow_env.service
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Question",
                "result_task_id": "q",
                "tasks": [
                    {
                        "id": "q",
                        "name": "Question",
                        "human": {"kind": "question", "question": "Answer?"},
                    }
                ],
            }
        },
    )
    run = await service.start_run("alice", asset["asset_id"], {"version": 1})
    answer = {"request_id": "answer", "action": "answer", "answer": "Saved evidence"}

    def fail(session, flush_context, instances):
        if any(
            isinstance(row, AgentWorkflowEvent) and row.kind == boundary
            for row in session.new
        ):
            raise RuntimeError("Storage unavailable at human boundary")

    event.listen(Session, "before_flush", fail)
    try:
        if boundary == "human.answered":
            with pytest.raises(RuntimeError, match="Storage unavailable"):
                await service.answer_todo(
                    "alice", run["run_id"], run["todos"][0]["todo_id"], answer
                )
        else:
            accepted = await service.answer_todo(
                "alice", run["run_id"], run["todos"][0]["todo_id"], answer
            )
            assert accepted["todos"][0]["status"] == "answered"
            assert accepted["todos"][0]["response"]["answer"] == "Saved evidence"
        saved = await service.get_run("alice", run["run_id"])
        assert saved["todos"][0]["consumed_at"] is None
        assert saved["tasks"][0]["result"] is None
    finally:
        event.remove(Session, "before_flush", fail)
    final = await service.answer_todo(
        "alice", run["run_id"], run["todos"][0]["todo_id"], answer
    )
    assert final["status"] == "completed"
    assert len([e for e in final["events"] if e["kind"] == "human.consumed"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("answer_first", [False, True])
async def test_cancel_serializes_with_answer_without_erasing_requests(
    workflow_env, answer_first
):
    service = workflow_env.service
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Question",
                "result_task_id": "q",
                "tasks": [
                    {
                        "id": "q",
                        "name": "Question",
                        "human": {"kind": "question", "question": "Answer?"},
                    }
                ],
            }
        },
    )
    run = await service.start_run("alice", asset["asset_id"], {"version": 1})
    await service.control_run(
        "alice", run["run_id"], {"request_id": "pause", "action": "pause"}
    )
    answer = {"request_id": "answer", "action": "answer", "answer": "Asia"}
    if answer_first:
        await service.answer_todo(
            "alice", run["run_id"], run["todos"][0]["todo_id"], answer
        )
    await service.control_run(
        "alice", run["run_id"], {"request_id": "cancel", "action": "cancel"}
    )
    if answer_first:
        await service.answer_todo(
            "alice", run["run_id"], run["todos"][0]["todo_id"], answer
        )
    else:
        with pytest.raises(RoleAssetError, match="cancelled"):
            await service.answer_todo(
                "alice", run["run_id"], run["todos"][0]["todo_id"], answer
            )
    final = await service.get_run("alice", run["run_id"])
    assert final["status"] == "cancelled"
    assert final["todos"][0]["question"] == "Answer?"
    assert final["todos"][0]["consumed_at"] is None
    assert final["todos"][0]["status"] == "cancelled"
    assert (final["todos"][0]["response"] is not None) is answer_first


@pytest.mark.asyncio
async def test_review_task_cannot_read_unselected_file_before_human_delivery_approval(
    workflow_env,
):
    service, role = workflow_env.service, workflow_env.role
    ports = service.ports_factory("alice")

    async def collect(session_id, message_id, member_id):
        binding = next(
            observation[0]
            for observation in reversed(workflow_env.observations)
            if observation[0].member_id == member_id
        )
        task = await service.get_run("alice", binding.run_id)
        tools = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")
        if task["snapshot"]["node_id"] == "report":
            file = await service.store_file(
                "alice",
                binding.run_id,
                task["state"]["attempt_id"],
                "evidence.txt",
                b"Complete evidence for independent review",
            )
            await tools[0].call(
                None,
                result_json='{"text":"Report","files":[{"file_id":"'
                + file["file_id"]
                + '"}]}',
            )
        else:
            ref = task["snapshot"]["input_artifacts"][0]
            artifact = await service.read_artifact("alice", **ref)
            read = next(tool for tool in tools if tool.name == "read_task_artifact")
            content = await read.call(
                None, **ref, file_id=artifact["files"][0]["file_id"]
            )
            assert "Graph inputs do not grant file access" in content
            await tools[0].call(None, text="Reviewed the selected field")
        return "Done", []

    service.ports_factory = lambda _: replace(ports, collect=collect)
    agent = {
        "id": "report",
        "name": "Report",
        "member": "writer",
        "role_id": role["role_id"],
        "role_version": 1,
        "task": "Report",
    }
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
        "required": ["text"],
    }
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Review before delivery",
                "result_task_id": "approve",
                "tasks": [
                    {**agent, "output_contract": contract},
                    {
                        **agent,
                        "id": "review",
                        "name": "Review",
                        "inputs": {"evidence": {"task_id": "report"}},
                        "task": "Review {evidence}",
                    },
                    {
                        "id": "approve",
                        "name": "Approve",
                        "human": {
                            "kind": "approval",
                            "question": "Approve reviewed report?",
                            "artifact_task_id": "report",
                        },
                    },
                ],
                "edges": [
                    {"source": "report", "target": "review"},
                    {"source": "review", "target": "approve"},
                ],
            }
        },
    )
    run = await service.start_run("alice", asset["asset_id"], {"version": 1})
    for _ in range(200):
        run = await service.get_run("alice", run["run_id"])
        if run["todos"] or run["status"] == "blocked":
            break
        await asyncio.sleep(0.01)
    assert run["tasks"][1]["status"] == "completed", run
    assert run["tasks"][1]["result"] == {"text": "Reviewed the selected field"}
    assert run["todos"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_requested_revision_can_be_supplied_after_approval_wait_begins(
    workflow_env,
):
    service, role = workflow_env.service, workflow_env.role
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Approve v2",
                "result_task_id": "approve",
                "tasks": [
                    {
                        "id": "report",
                        "name": "Report",
                        "member": "writer",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "Report",
                    },
                    {
                        "id": "approve",
                        "name": "Approval",
                        "human": {
                            "kind": "approval",
                            "question": "Approve revised report?",
                            "artifact_task_id": "report",
                            "artifact_version": 2,
                        },
                    },
                ],
                "edges": [{"source": "report", "target": "approve"}],
            }
        },
    )
    run = await service.start_run("alice", asset["asset_id"], {"version": 1})
    for _ in range(200):
        run = await service.get_run("alice", run["run_id"])
        if run["todos"] or run["status"] == "blocked":
            break
        await asyncio.sleep(0.01)
    assert run["status"] == "running"
    todo = run["todos"][0]
    assert todo["artifact_ref"]["version"] == 2
    with pytest.raises(RoleAssetError):
        await service.answer_todo(
            "alice",
            run["run_id"],
            todo["todo_id"],
            {
                "request_id": "approve",
                "action": "approve",
                "artifact_ref": todo["artifact_ref"],
            },
        )
    with pytest.raises(RoleAssetError):
        await service.answer_todo(
            "alice",
            run["run_id"],
            todo["todo_id"],
            {
                "request_id": "reject-unavailable",
                "action": "reject",
                "feedback": "Revise",
                "artifact_ref": todo["artifact_ref"],
            },
        )
    with pytest.raises(RoleAssetError):
        await service.recover_run("alice", todo["task_run_id"])
    await service.revise_artifact(
        "alice",
        todo["artifact_ref"]["artifact_id"],
        {
            "request_id": "revision",
            "expected_version": 1,
            "content": {"text": "Reviewed v2"},
        },
    )
    await service.control_run(
        "alice", run["run_id"], {"request_id": "continue", "action": "continue"}
    )
    current = await service.get_run("alice", run["run_id"])
    assert current["todos"] == run["todos"]
    approved = await service.answer_todo(
        "alice",
        run["run_id"],
        todo["todo_id"],
        {
            "request_id": "approve",
            "action": "approve",
            "artifact_ref": todo["artifact_ref"],
        },
    )
    assert approved["status"] == "completed"
    assert (await service.export_artifact("alice", **todo["artifact_ref"]))[
        "content"
    ] == {"text": "Reviewed v2"}
    with pytest.raises(RoleAssetError):
        await service.export_artifact("alice", todo["artifact_ref"]["artifact_id"], 1)
