"""Public workflow tests for persistent human requests."""

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_two_parallel_questions_are_independently_answered(workflow_env):
    service = workflow_env.service
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Parallel questions",
                "tasks": [
                    {
                        "id": "first",
                        "name": "First",
                        "human": {"kind": "question", "question": "First?"},
                    },
                    {
                        "id": "second",
                        "name": "Second",
                        "human": {"kind": "question", "question": "Second?"},
                    },
                ],
                "edges": [],
                "result_task_id": "first",
            }
        },
    )
    run = await service.start_run("alice", asset["asset_id"], {"version": 1})
    assert len(run["todos"]) == 2
    requests = {todo["question"]: todo for todo in run["todos"]}
    assert all(todo["source_kind"] == "static_node" for todo in requests.values())
    await service.answer_todo(
        "alice",
        run["run_id"],
        requests["First?"]["todo_id"],
        {"request_id": "first-answer", "action": "answer", "answer": "One"},
    )
    run = await service.get_run("alice", run["run_id"])
    assert (
        next(t for t in run["todos"] if t["question"] == "Second?")["status"]
        == "pending"
    )
    run = await service.answer_todo(
        "alice",
        run["run_id"],
        requests["Second?"]["todo_id"],
        {"request_id": "second-answer", "action": "answer", "answer": "Two"},
    )
    assert {task["result"]["text"] for task in run["tasks"]} == {"One", "Two"}


@pytest.mark.asyncio
async def test_cancel_marks_pending_request_closed(workflow_env):
    service = workflow_env.service
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Question",
                "result_task_id": "question",
                "tasks": [
                    {
                        "id": "question",
                        "name": "Question",
                        "human": {"kind": "question", "question": "Continue?"},
                    }
                ],
                "edges": [],
            }
        },
    )
    run = await service.start_run("alice", asset["asset_id"], {"version": 1})
    todo = run["todos"][0]
    run = await service.control_run(
        "alice", run["run_id"], {"request_id": "cancel", "action": "cancel"}
    )
    assert run["todos"][0]["status"] == "cancelled"
    with pytest.raises(RoleAssetError, match="cancelled"):
        await service.answer_todo(
            "alice",
            run["run_id"],
            todo["todo_id"],
            {"request_id": "late", "action": "answer", "answer": "Yes"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("suggested, expected", [("Asia", True), ("Unknown", False)])
async def test_inbox_reads_only_explicit_upstream_recommendation(
    workflow_env, suggested, expected
):
    import asyncio
    import json
    from dataclasses import replace

    from astrbot.core.agent_team_tools import AgentTeamToolRegistry

    service, role = workflow_env.service, workflow_env.role
    ports = service.ports_factory("alice")

    async def collect(session_id, message_id, member_id):
        tool = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[0]
        await tool.call(
            None,
            result_json=json.dumps(
                {"choice": suggested, "reason": "Demand", "secret": "unselected"}
            ),
        )
        return "Done", []

    service.ports_factory = lambda _: replace(ports, collect=collect)
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Recommendation",
                "result_task_id": "question",
                "tasks": [
                    {
                        "id": "source",
                        "name": "Source",
                        "member": "writer",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "Advise",
                        "output_contract": {
                            "type": "object",
                            "properties": {
                                "choice": {"type": "string"},
                                "reason": {"type": "string"},
                                "secret": {"type": "string"},
                            },
                            "required": ["choice", "reason", "secret"],
                        },
                    },
                    {
                        "id": "question",
                        "name": "Question",
                        "human": {
                            "kind": "question",
                            "question": "Region?",
                            "choices": ["Asia", "Europe"],
                            "recommendation_task_id": "source",
                            "recommendation_value_field": "choice",
                            "recommendation_reason_field": "reason",
                        },
                    },
                ],
                "edges": [{"source": "source", "target": "question"}],
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
    assert (
        todo["run_name"],
        todo["task_name"],
        todo["recommendation_source_name"],
    ) == ("Recommendation", "Question", "Source")
    inbox = (await service.list_human_requests("alice"))["requests"]
    assert len(inbox) == 1 and inbox[0]["todo_id"] == todo["todo_id"]
    assert (await service.list_human_requests("bob"))["requests"] == []
    assert todo["recommendation_ref"] == {
        "artifact_id": run["tasks"][0]["run_id"],
        "version": 1,
    }
    assert todo["recommendation"] == (
        {"value": "Asia", "reason": "Demand"} if expected else None
    )
    assert bool(todo["recommendation_error"]) is not expected
    assert "unselected" not in str(todo)
    await service.answer_todo(
        "alice",
        run["run_id"],
        todo["todo_id"],
        {"request_id": "answer", "action": "answer", "answer": "Europe"},
    )
    assert (await service.list_human_requests("alice"))["requests"] == []


@pytest.mark.asyncio
async def test_competing_answers_keep_one_decision(workflow_env):
    import asyncio

    service = workflow_env.service
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Race",
                "result_task_id": "q",
                "tasks": [
                    {
                        "id": "q",
                        "name": "Question",
                        "human": {"kind": "question", "question": "Where?"},
                    }
                ],
                "edges": [],
            }
        },
    )
    run = await service.start_run("alice", asset["asset_id"], {"version": 1})
    await service.control_run(
        "alice", run["run_id"], {"request_id": "pause", "action": "pause"}
    )
    todo_id = run["todos"][0]["todo_id"]
    outcomes = await asyncio.gather(
        *[
            service.answer_todo(
                "alice",
                run["run_id"],
                todo_id,
                {"request_id": str(i), "action": "answer", "answer": answer},
            )
            for i, answer in enumerate(["Asia", "Europe"])
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(item, RoleAssetError) for item in outcomes) == 1
    current = await service.get_run("alice", run["run_id"])
    decision = current["todos"][0]["response"]
    assert current["todos"][0]["status"] == "answered"
    assert current["todos"][0]["consumed_at"] is None
    assert (await service.list_human_requests("alice"))["requests"] == []
    await service.answer_todo(
        "alice", run["run_id"], todo_id, {**decision, "request_id": "another-client"}
    )
    assert (
        len([event for event in current["events"] if event["kind"] == "human.answered"])
        == 1
    )


@pytest.mark.asyncio
async def test_dynamic_source_is_not_consumed_as_a_static_node(workflow_env):
    from astrbot.core.db.po import AgentWorkflowTodo

    service = workflow_env.service
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Dynamic source",
                "result_task_id": "q",
                "tasks": [
                    {
                        "id": "q",
                        "name": "Question",
                        "human": {"kind": "question", "question": "Where?"},
                    }
                ],
                "edges": [],
            }
        },
    )
    run = await service.start_run("alice", asset["asset_id"], {"version": 1})
    async with workflow_env.db.get_db() as session:
        todo = await session.get(AgentWorkflowTodo, run["todos"][0]["todo_id"])
        todo.data = {
            **todo.data,
            "source_kind": "tool_call",
            "invocation_id": "inv-1",
            "tool_record_id": "record-1",
            "status": "answered",
            "response": {
                "request_id": "r",
                "action": "answer",
                "answer": "Asia",
                "feedback": None,
            },
        }
        session.add(todo)
        await session.commit()
    current = await service.advance_run("alice", run["run_id"])
    assert current["tasks"][0]["status"] == "waiting_human"
    assert current["todos"][0]["status"] == "answered"
    assert current["todos"][0]["consumed_at"] is None
    assert current["todos"][0]["tool_record_id"] == "record-1"


@pytest.mark.parametrize(
    "source_id,edge,field",
    [("other", False, "text"), ("source", False, "text"), ("source", True, "missing")],
)
def test_recommendation_requires_connected_declared_upstream_field(
    source_id, edge, field
):
    from astrbot.core.agent_teams.graph import GraphDefinition, validate_graph

    definition = GraphDefinition.model_validate(
        {
            "name": "Validation",
            "result_task_id": "q",
            "tasks": [
                {
                    "id": "source",
                    "name": "Source",
                    "member": "source",
                    "role": {"name": "Source", "prompt": "Recommend"},
                    "installation": {"profile_id": "default"},
                    "task": "Recommend",
                },
                {
                    "id": "q",
                    "name": "Question",
                    "human": {
                        "kind": "question",
                        "question": "Where?",
                        "recommendation_task_id": source_id,
                        "recommendation_value_field": field,
                        "recommendation_reason_field": "text",
                    },
                },
            ],
            "edges": [{"source": "source", "target": "q"}] if edge else [],
        }
    )
    with pytest.raises(RoleAssetError) as exc:
        validate_graph(definition)
    assert any("recommendation_task_id" in item["path"] for item in exc.value.fields)


@pytest.mark.asyncio
async def test_multiple_invocations_share_task_without_sharing_answers(workflow_env):
    from astrbot.core.db.po import AgentWorkflowTodo

    service = workflow_env.service
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Invocations",
                "result_task_id": "q",
                "tasks": [
                    {
                        "id": "q",
                        "name": "Question",
                        "human": {"kind": "question", "question": "Static question"},
                    }
                ],
                "edges": [],
            }
        },
    )
    run = await service.start_run("alice", asset["asset_id"], {"version": 1})
    original = run["todos"][0]
    async with workflow_env.db.get_db() as session:
        row = await session.get(AgentWorkflowTodo, original["todo_id"])
        for i in (1, 2):
            session.add(
                AgentWorkflowTodo(
                    todo_id=f"tool-{i}",
                    root_run_id=run["run_id"],
                    task_run_id=row.task_run_id,
                    data={
                        **row.data,
                        "source_kind": "tool_call",
                        "invocation_id": f"inv-{i}",
                        "tool_record_id": f"record-{i}",
                        "question": f"Tool question {i}",
                    },
                )
            )
        await session.commit()
    requests = (await service.list_human_requests("alice"))["requests"]
    assert {item["todo_id"] for item in requests} == {
        original["todo_id"],
        "tool-1",
        "tool-2",
    }
    for i in (1, 2):
        await service.answer_todo(
            "alice",
            run["run_id"],
            f"tool-{i}",
            {
                "request_id": f"answer-{i}",
                "action": "answer",
                "choice_id": "__free_text__",
                "free_text": f"Result {i}",
            },
        )
    current = await service.get_run("alice", run["run_id"])
    entries = {item["todo_id"]: item for item in current["todos"]}
    assert entries["tool-1"]["response"]["free_text"] == "Result 1"
    assert entries["tool-2"]["response"]["free_text"] == "Result 2"
    assert all(entries[f"tool-{i}"]["consumed_at"] is None for i in (1, 2))
    assert entries[original["todo_id"]]["status"] == "pending"


@pytest.mark.asyncio
async def test_answerable_identity_is_enforced_in_both_views(workflow_env):
    from astrbot.core.db.po import AgentWorkflowTodo

    service = workflow_env.service
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Restricted",
                "result_task_id": "q",
                "tasks": [
                    {
                        "id": "q",
                        "name": "Question",
                        "human": {"kind": "question", "question": "Private?"},
                    }
                ],
                "edges": [],
            }
        },
    )
    run = await service.start_run("alice", asset["asset_id"], {"version": 1})
    todo_id = run["todos"][0]["todo_id"]
    async with workflow_env.db.get_db() as session:
        todo = await session.get(AgentWorkflowTodo, todo_id)
        todo.data = {**todo.data, "answerable_by": []}
        session.add(todo)
        await session.commit()
    assert (await service.get_run("alice", run["run_id"]))["todos"] == []
    assert (await service.list_human_requests("alice"))["requests"] == []
    with pytest.raises(RoleAssetError) as exc:
        await service.answer_todo(
            "alice",
            run["run_id"],
            todo_id,
            {"request_id": "a", "action": "answer", "answer": "Yes"},
        )
    assert exc.value.status == 404
