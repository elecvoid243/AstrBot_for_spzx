"""Workflow team calls share public persistence, scheduling and result semantics."""

import asyncio
import json

import pytest

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_one_capacity_workflow_calls_pinned_team_with_declared_inputs(
    workflow_env,
):
    """Return complete output from an isolated child without occupying parent capacity.

    Args:
        workflow_env: Public services with real SQLite and an execution boundary.
    """
    env = workflow_env
    inputs = {"goal": "研究 {notes}\nLiteral braces", "notes": '中文\n{"year": 2025}'}
    contract = {
        "type": "object",
        "properties": {"text": {"type": "string"}, "verified": {"type": "boolean"}},
        "required": ["text", "verified"],
        "additionalProperties": False,
    }
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Research child",
                "coordinator": {
                    "id": "coordinator",
                    "role_id": env.role["role_id"],
                    "role_version": 1,
                },
                "allowed_roles": [
                    {"id": "worker", "role_id": env.role["role_id"], "role_version": 1}
                ],
                "required_inputs": ["goal", "notes"],
                "max_parallel": 1,
                "output_contract": contract,
            }
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Parent",
                "result_task_id": "write",
                "required_inputs": ["goal", "notes"],
                "max_parallel": 1,
                "tasks": [
                    {
                        "id": "research",
                        "name": "Research call",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {key: {"input": key} for key in inputs},
                        "output_contract": contract,
                    },
                    {
                        "id": "write",
                        "name": "Write",
                        "member": "writer",
                        "role_id": env.role["role_id"],
                        "role_version": 1,
                        "task": "Write {evidence}",
                        "inputs": {
                            "evidence": {"task_id": "research", "field": "text"}
                        },
                    },
                ],
                "edges": [{"source": "research", "target": "write"}],
            }
        },
    )
    bindings, requests = {}, []
    report = "完整证据\n" * 2000 + "COMPLETE_END"

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        bindings[session_id] = binding
        requests.append((binding, text, context))
        return message_id

    async def collect(session_id, message_id, member_id):
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        if bindings[session_id].node_id == "coordinator":
            await tools["submit_task_result"].call(
                None, result_json=json.dumps({"text": report, "verified": True})
            )
        else:
            await tools["submit_task_result"].call(None, text="Delivered")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run(
        "alice", asset["asset_id"], {"version": 1, "inputs": inputs}
    )
    await asyncio.gather(
        *(env.service.advance_run("alice", run["run_id"]) for _ in range(3))
    )
    current = await completed_run(env.service, run["run_id"])
    assert current["status"] == "completed", current
    assert [text for _, text, _ in requests] == [inputs["goal"], "Write " + report]
    decoded, _ = json.JSONDecoder().raw_decode(
        requests[0][2].split("Declared run inputs (JSON):\n", 1)[1]
    )
    assert decoded == inputs
    call, writer = current["tasks"]
    assert call["result"] == {"text": report, "verified": True}
    assert writer["snapshot"]["input_artifacts"] == [call["result_ref"]]
    assert len(current["scopes"]) == 1
    scope = current["scopes"][0]
    assert scope["snapshot"]["asset_version"] == 1
    assert scope["snapshot"]["parent_run_id"] == call["run_id"]
    coordinator = scope["tasks"][0]
    assert coordinator["control"]["root_run_id"] == run["run_id"]
    assert coordinator["state"]["scope_id"] != writer["state"]["scope_id"]
    assert (
        coordinator["snapshot"]["member"]["conversation_id"]
        != writer["snapshot"]["member"]["conversation_id"]
    )
    assert (await env.service.list_runs("alice"))["runs"][0]["run_id"] == run["run_id"]
    assert len((await env.service.list_runs("alice"))["runs"]) == 1
    assert (
        len([event for event in current["events"] if event["kind"] == "scope.started"])
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", " \n\t"])
async def test_child_required_input_rejects_empty_upstream_result(workflow_env, value):
    """Validate required child inputs before creating or dispatching its scope.

    Args:
        workflow_env: Public services with an external execution boundary.
        value: A valid custom result that cannot satisfy a required text input.
    """
    env = workflow_env
    contract = {
        "type": "object",
        "properties": {"text": {"type": "string"}, "valid": {"type": "boolean"}},
        "required": ["text", "valid"],
        "additionalProperties": False,
    }
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Child",
                "coordinator": {
                    "id": "coordinator",
                    "role_id": env.role["role_id"],
                    "role_version": 1,
                },
                "allowed_roles": [
                    {"id": "worker", "role_id": env.role["role_id"], "role_version": 1}
                ],
            }
        },
    )
    with pytest.raises(RoleAssetError) as direct:
        await env.service.start_run(
            "alice", team["asset_id"], {"version": 1, "inputs": {"goal": value}}
        )
    assert direct.value.fields == [
        {"path": "inputs.goal", "code": "REQUIRED", "message": "Provide non-empty text"}
    ]
    agent = {
        "role_id": env.role["role_id"],
        "role_version": 1,
        "task": "Produce",
    }
    parent = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Input boundary",
                "result_task_id": "independent",
                "max_parallel": 1,
                "tasks": [
                    {
                        "id": "produce",
                        "name": "Produce",
                        "member": "worker",
                        **agent,
                        "output_contract": contract,
                    },
                    {
                        "id": "call",
                        "name": "Child call",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"task_id": "produce", "field": "text"}},
                    },
                    {
                        "id": "dependent",
                        "name": "Dependent",
                        "member": "worker",
                        **agent,
                    },
                    {
                        "id": "independent",
                        "name": "Independent",
                        "member": "worker",
                        **agent,
                    },
                ],
                "edges": [
                    {"source": "produce", "target": "call"},
                    {"source": "call", "target": "dependent"},
                ],
            }
        },
    )
    delivered, bindings = [], {}

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        bindings[session_id] = binding
        delivered.append(binding.node_id)
        return message_id

    async def collect(session_id, message_id, member_id):
        tool = next(
            tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
            if tool.name == "submit_task_result"
        )
        response = (
            await tool.call(
                None, result_json=json.dumps({"text": value, "valid": True})
            )
            if bindings[session_id].node_id == "produce"
            else await tool.call(None, text="Complete")
        )
        assert response == "Result accepted."
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run("alice", parent["asset_id"], {"version": 1})
    sessions = await env.db.get_platform_sessions_by_creator("alice")
    current = await completed_run(env.service, run["run_id"])
    assert current["status"] == "blocked", current
    tasks = {task["node_id"]: task for task in current["tasks"]}
    assert tasks["call"]["status"] == "blocked"
    assert tasks["call"]["state"]["issues"] == direct.value.fields
    assert tasks["dependent"]["status"] == "dependency_failed"
    assert tasks["independent"]["status"] == "completed"
    assert current["scopes"] == []
    assert delivered == ["produce", "independent"]
    assert await env.db.get_platform_sessions_by_creator("alice") == sessions


@pytest.mark.asyncio
async def test_root_cancel_reaches_child_scope_and_keeps_effects(workflow_env):
    """A whole-run cancellation fences every child without deleting prior effects.

    Args:
        workflow_env: Public services and the injected external execution boundary.
    """
    env = workflow_env
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Child",
                "coordinator": {
                    "id": "coordinator",
                    "role_id": env.role["role_id"],
                    "role_version": 1,
                },
                "allowed_roles": [
                    {"id": "worker", "role_id": env.role["role_id"], "role_version": 1}
                ],
            }
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Root cancel",
                "result_task_id": "call",
                "required_inputs": ["goal"],
                "max_parallel": 1,
                "tasks": [
                    {
                        "id": "call",
                        "name": "Call",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                    }
                ],
            }
        },
    )
    ready = asyncio.Event()
    effects = []

    async def collect(*args):
        effects.append("external receipt")
        ready.set()
        await asyncio.Event().wait()

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=lambda *args, **kwargs: asyncio.sleep(0, result=kwargs["message_id"]),
        collect=collect,
        is_busy=lambda _: False,
        emit=lambda _: None,
    )
    run = await env.service.start_run(
        "alice", asset["asset_id"], {"version": 1, "inputs": {"goal": "Research"}}
    )
    await asyncio.wait_for(ready.wait(), 5)
    before = await env.service.get_run("alice", run["run_id"])
    current = await env.service.control_run(
        "alice", run["run_id"], {"request_id": "cancel", "action": "cancel"}
    )
    assert current["status"] == "cancelled"
    assert current["tasks"][0]["status"] == "cancelled"
    assert current["scopes"][0]["status"] == "cancelled"
    assert current["scopes"][0]["tasks"][0]["status"] == "cancelled"
    assert (
        current["scopes"][0]["tasks"][0]["state"]["attempt_id"]
        == before["scopes"][0]["tasks"][0]["state"]["attempt_id"]
    )
    assert effects == ["external receipt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_child_wait_or_failure_leaves_independent_branch_ready(
    workflow_env, failure
):
    """Only dependencies wait; root controls retain a child's persistent approval.

    Args:
        workflow_env: Public services and controlled execution adapter.
        failure: Reject the child model instead of waiting for human approval.
    """
    env = workflow_env
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Child",
                "coordinator": {
                    "id": "coordinator",
                    "role_id": env.role["role_id"],
                    "role_version": 1,
                },
                "allowed_roles": [
                    {"id": "worker", "role_id": env.role["role_id"], "role_version": 1}
                ],
                **(
                    {}
                    if failure
                    else {"approval_question": "Approve this exact version?"}
                ),
            }
        },
    )
    agent = {
        "member": "writer",
        "role_id": env.role["role_id"],
        "role_version": 1,
        "task": "Write",
    }
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Independent branch",
                "result_task_id": "independent",
                "required_inputs": ["goal"],
                "max_parallel": 1,
                "tasks": [
                    {
                        "id": "call",
                        "name": "Call",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                    },
                    {"id": "dependent", "name": "Dependent", **agent},
                    {"id": "independent", "name": "Independent", **agent},
                ],
                "edges": [{"source": "call", "target": "dependent"}],
            }
        },
    )
    bindings = {}

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        bindings[session_id] = AgentTeamExecutionRegistry.resolve(execution_token)
        return message_id

    async def collect(session_id, message_id, member_id):
        if failure and bindings[session_id].node_id == "coordinator":
            raise RuntimeError("Verified child provider failure")
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        await tools["submit_task_result"].call(
            None,
            text="Pinned child report"
            if bindings[session_id].node_id == "coordinator"
            else "Independent result",
        )
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run(
        "alice", asset["asset_id"], {"version": 1, "inputs": {"goal": "Research"}}
    )
    for _ in range(300):
        current = await env.service.get_run("alice", run["run_id"])
        if next(task for task in current["tasks"] if task["node_id"] == "independent")[
            "status"
        ] == "completed" and (
            current["status"] == "blocked" if failure else bool(current["todos"])
        ):
            break
        await asyncio.sleep(0.01)
    tasks = {task["node_id"]: task for task in current["tasks"]}
    assert tasks["independent"]["status"] == "completed"
    if failure:
        assert current["status"] == "blocked"
        assert tasks["call"]["status"] == "blocked"
        assert tasks["dependent"]["status"] == "dependency_failed"
        assert (
            current["scopes"][0]["tasks"][0]["state"]["reason"]
            == "Verified child provider failure"
        )
    else:
        todo = current["todos"][0]
        assert todo["root_run_id"] == run["run_id"]
        paused = await env.service.control_run(
            "alice", run["run_id"], {"action": "pause", "request_id": "pause"}
        )
        assert paused["scopes"][0]["control"]["status"] == "paused"
        answered = await env.service.answer_todo(
            "alice",
            run["run_id"],
            todo["todo_id"],
            {
                "action": "approve",
                "request_id": "approval",
                "artifact_ref": todo["artifact_ref"],
            },
        )
        assert answered["todos"][0]["status"] == "answered"
        assert answered["tasks"][0]["status"] == "waiting_child"
        await env.service.control_run(
            "alice", run["run_id"], {"action": "continue", "request_id": "continue"}
        )
        completed = await completed_run(env.service, run["run_id"])
        assert completed["status"] == "completed"
        assert completed["tasks"][0]["result"] == {"text": "Pinned child report"}
        assert (
            completed["tasks"][0]["result_ref"]
            == completed["scopes"][0]["tasks"][0]["result_ref"]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "depth",
        "invisible",
        "missing_version",
        "missing_input",
        "extra_input",
        "output",
        "mixed",
        "wrong_kind",
    ],
)
async def test_invalid_child_call_is_rejected_without_partial_execution(
    workflow_env, change
):
    """Reject inaccessible or ambiguous child definitions before creating any run.

    Args:
        workflow_env: Public persistence and asset services.
        change: One invalid definition or execution bound.
    """
    import copy

    env = workflow_env
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Child",
                "coordinator": {
                    "id": "coordinator",
                    "role_id": env.role["role_id"],
                    "role_version": 1,
                },
                "allowed_roles": [
                    {"id": "worker", "role_id": env.role["role_id"], "role_version": 1}
                ],
            }
        },
    )
    definition = {
        "name": "Validation",
        "required_inputs": ["goal"],
        "tasks": [
            {
                "id": "call",
                "name": "Call",
                "team": {"asset_id": team["asset_id"], "version": 1},
                "inputs": {"goal": {"input": "goal"}},
            }
        ],
    }
    if change == "depth":
        definition["max_depth"] = 0
    elif change == "invisible":
        definition["tasks"][0]["team"]["asset_id"] = "other-owner-team"
    elif change == "missing_version":
        del definition["tasks"][0]["team"]["version"]
    elif change == "missing_input":
        definition["tasks"][0]["inputs"] = {}
    elif change == "extra_input":
        definition["tasks"][0]["inputs"]["notes"] = {"input": "goal"}
    elif change == "output":
        definition["tasks"][0]["output_contract"] = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        }
    elif change == "mixed":
        definition["tasks"][0]["member"] = "parent-member"
    elif change == "wrong_kind":
        definition["tasks"][0]["team"]["asset_id"] = env.asset["asset_id"]
    before = copy.deepcopy(await env.service.list_assets("alice"))
    with pytest.raises(RoleAssetError) as invalid:
        await env.service.save_asset("alice", {"definition": definition})
    assert invalid.value.fields, "The editor needs a field-specific rejection"
    assert await env.service.list_assets("alice") == before
    assert (await env.service.list_runs("alice"))["runs"] == []
    assert await env.db.get_platform_sessions_by_creator("alice") == []


@pytest.mark.asyncio
async def test_later_child_creation_uses_original_asset_and_role_snapshot(workflow_env):
    """Editing the child while the parent waits cannot change its launch snapshot.

    Args:
        workflow_env: Public services with real SQLite and controlled execution.
    """
    env = workflow_env
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Original child",
                "coordinator": {
                    "id": "coordinator",
                    "role_id": env.role["role_id"],
                    "role_version": 1,
                },
                "allowed_roles": [
                    {"id": "worker", "role_id": env.role["role_id"], "role_version": 1}
                ],
            }
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Delayed call",
                "result_task_id": "call",
                "required_inputs": ["goal"],
                "tasks": [
                    {
                        "id": "confirm",
                        "name": "Confirm",
                        "human": {"kind": "question", "question": "Start?"},
                    },
                    {
                        "id": "call",
                        "name": "Call",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                    },
                ],
                "edges": [{"source": "confirm", "target": "call"}],
            }
        },
    )
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {"version": 1, "inputs": {"goal": "Frozen input {goal}"}},
    )
    await env.roles.save_role(
        "alice",
        {
            "expected_version": 1,
            "definition": {
                "name": "New role",
                "prompt": "Changed instructions",
                "provider_id": "model-a",
            },
            "installation": {"profile_id": "default"},
        },
        env.role["role_id"],
    )
    await env.service.save_asset(
        "alice",
        {
            "expected_version": 1,
            "definition": {
                **team["definition"],
                "name": "Edited child",
                "coordinator": {
                    "id": "coordinator",
                    "role_id": env.role["role_id"],
                    "role_version": 2,
                },
            },
        },
        team["asset_id"],
    )
    await env.service.answer_todo(
        "alice",
        run["run_id"],
        run["todos"][0]["todo_id"],
        {"action": "answer", "answer": "Yes", "request_id": "answer"},
    )
    current = await completed_run(env.service, run["run_id"])
    assert current["status"] == "completed"
    scope = current["scopes"][0]
    assert scope["snapshot"]["asset_version"] == 1
    assert scope["snapshot"]["definition"]["name"] == "Original child"
    coordinator = scope["tasks"][0]
    assert coordinator["snapshot"]["effective"]["prompt"] == "Original instructions"
    assert coordinator["snapshot"]["inputs"] == {"goal": "Frozen input {goal}"}
    assert current["snapshot"] == run["snapshot"]
    lightweight = (await env.service.list_runs("alice"))["runs"][0]
    assert "Original instructions" not in json.dumps(lightweight)
    assert "Changed instructions" not in json.dumps(lightweight)


@pytest.mark.asyncio
async def test_loop_creates_new_child_scope_and_task_without_sharing_context(
    workflow_env,
):
    """A declared next iteration creates fresh child members and pinned feedback.

    Args:
        workflow_env: Public services backed by real SQLite.
    """
    env = workflow_env
    contract = {
        "type": "object",
        "properties": {"text": {"type": "string"}, "approved": {"type": "boolean"}},
        "required": ["text", "approved"],
    }
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Review child",
                "coordinator": {
                    "id": "coordinator",
                    "role_id": env.role["role_id"],
                    "role_version": 1,
                },
                "allowed_roles": [
                    {"id": "worker", "role_id": env.role["role_id"], "role_version": 1}
                ],
                "output_contract": contract,
            }
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Child revision",
                "result_task_id": "call",
                "required_inputs": ["goal"],
                "max_parallel": 1,
                "tasks": [
                    {
                        "id": "call",
                        "name": "Call",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                        "output_contract": contract,
                    }
                ],
                "loops": [
                    {
                        "id": "review",
                        "name": "Review twice",
                        "body": ["call"],
                        "exit_task_id": "call",
                        "exit_condition": {"field": "approved", "value": True},
                        "max_iterations": 2,
                        "feedback": [
                            {
                                "source": "call",
                                "field": "text",
                                "target": "call",
                                "input": "goal",
                            }
                        ],
                    }
                ],
            }
        },
    )
    deliveries = []

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        deliveries.append((session_id, text))
        return message_id

    async def collect(session_id, message_id, member_id):
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        await tools["submit_task_result"].call(
            None,
            result_json=json.dumps(
                {"text": "Reviewed previous version", "approved": len(deliveries) == 2}
            ),
        )
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run(
        "alice", asset["asset_id"], {"version": 1, "inputs": {"goal": "Original goal"}}
    )
    current = await completed_run(env.service, run["run_id"])
    assert current["status"] == "completed", current["status"]
    assert [text for _, text in deliveries] == [
        "Original goal",
        "Reviewed previous version",
    ]
    assert len(current["scopes"]) == 2
    assert len({scope["state"]["scope_id"] for scope in current["scopes"]}) == 2
    assert (
        len(
            {
                scope["tasks"][0]["snapshot"]["member"]["conversation_id"]
                for scope in current["scopes"]
            }
        )
        == 2
    )
    iterations = current["loops"][0]["iterations"]
    first, second = [iteration["tasks"][0] for iteration in iterations]
    assert first["state"]["task_id"] != second["state"]["task_id"]
    assert first["state"]["attempt_id"] != second["state"]["attempt_id"]
    second_scope = next(
        scope
        for scope in current["scopes"]
        if scope["snapshot"]["parent_run_id"] == second["run_id"]
    )
    assert second_scope["tasks"][0]["snapshot"]["input_artifacts"] == []


@pytest.mark.asyncio
async def test_retry_child_task_keeps_scope_and_parent_attempt(workflow_env):
    """A failed child task gets a new Attempt while the declared call stays stable.

    Args:
        workflow_env: Public services and controlled external provider failure.
    """
    env = workflow_env
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Retry child",
                "coordinator": {
                    "id": "coordinator",
                    "role_id": env.role["role_id"],
                    "role_version": 1,
                },
                "allowed_roles": [
                    {"id": "worker", "role_id": env.role["role_id"], "role_version": 1}
                ],
            }
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Retry parent",
                "result_task_id": "call",
                "required_inputs": ["goal"],
                "tasks": [
                    {
                        "id": "call",
                        "name": "Call",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                    }
                ],
            }
        },
    )
    failures = 0

    async def collect(session_id, message_id, member_id):
        nonlocal failures
        failures += 1
        if failures == 1:
            raise RuntimeError("Verified first attempt failure")
        tool = next(
            tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
            if tool.name == "submit_task_result"
        )
        await tool.call(None, text="Recovered child report")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=lambda *args, **kwargs: asyncio.sleep(0, result=kwargs["message_id"]),
        collect=collect,
        is_busy=lambda _: False,
        emit=lambda _: None,
    )
    run = await env.service.start_run(
        "alice", asset["asset_id"], {"version": 1, "inputs": {"goal": "Research"}}
    )
    failed = await completed_run(env.service, run["run_id"])
    assert failed["status"] == "blocked"
    child = failed["scopes"][0]["tasks"][0]
    await env.service.retry_task(
        "alice",
        child["run_id"],
        {
            "request_id": "child-retry",
            "attempt_id": child["state"]["attempt_id"],
            "input_policy": "original",
        },
    )
    current = await completed_run(env.service, run["run_id"])
    assert current["status"] == "completed"
    assert len(current["scopes"]) == 1
    assert current["scopes"][0]["run_id"] == failed["scopes"][0]["run_id"]
    retried = current["scopes"][0]["tasks"][0]
    assert retried["state"]["attempt_id"] != child["state"]["attempt_id"]
    assert retried["attempts"][0]["attempt_id"] == child["state"]["attempt_id"]
    assert (
        current["tasks"][0]["state"]["attempt_id"]
        == failed["tasks"][0]["state"]["attempt_id"]
    )
    assert current["tasks"][0]["result"] == {"text": "Recovered child report"}


@pytest.mark.asyncio
async def test_parent_ancestry_does_not_grant_child_member_visibility(workflow_env):
    """Only explicit pinned input refs cross scopes; arbitrary messages and reads fail.

    Args:
        workflow_env: Public services with real SQLite and bound Agent tools.
    """
    env = workflow_env
    external = await env.service.start_run(
        "alice", env.asset["asset_id"], {"version": 1}
    )
    external = await completed_run(env.service, external["run_id"])
    common_ref = external["result_ref"]
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Scope child",
                "coordinator": {
                    "id": "coordinator",
                    "role_id": env.role["role_id"],
                    "role_version": 1,
                },
                "allowed_roles": [
                    {"id": "worker", "role_id": env.role["role_id"], "role_version": 1}
                ],
            }
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Scoped inputs",
                "result_task_id": "call",
                "required_inputs": ["goal"],
                "max_parallel": 1,
                "tasks": [
                    {
                        "id": "private",
                        "name": "Private",
                        "member": "parent",
                        "role_id": env.role["role_id"],
                        "role_version": 1,
                        "task": "PRIVATE_PARENT",
                    },
                    {
                        "id": "call",
                        "name": "Call",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                    },
                ],
                "edges": [{"source": "private", "target": "call"}],
            }
        },
    )
    bindings, rejections = {}, []

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        bindings[session_id] = AgentTeamExecutionRegistry.resolve(execution_token)
        return message_id

    async def collect(session_id, message_id, member_id):
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        caller = await env.service.get_run("alice", bindings[session_id].run_id)
        if caller["snapshot"].get("coordinator"):
            parent = await env.service.get_run(
                "alice", caller["control"]["root_run_id"]
            )
            private = next(
                task for task in parent["tasks"] if task["node_id"] == "private"
            )
            inspected = json.loads(
                await tools["team_communicate"].call(
                    None, action="inspect", request_id="inspect"
                )
            )
            assert [member["member_id"] for member in inspected["members"]] == [
                caller["snapshot"]["member"]["member_id"]
            ]
            rejections.append(
                await tools["team_communicate"].call(
                    None,
                    action="send",
                    request_id="cross-scope",
                    recipient_member_id=private["snapshot"]["member"]["member_id"],
                    text="Forbidden",
                )
            )
            rejections.append(
                await tools["read_task_artifact"].call(None, **private["result_ref"])
            )
            rejections.append(
                await tools["read_task_artifact"].call(None, **common_ref)
            )
            with pytest.raises(RoleAssetError) as denied:
                await env.service.get_run("bob", caller["run_id"])
            assert denied.value.status == 404
        await tools["submit_task_result"].call(None, text="Completed")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {
            "version": 1,
            "inputs": {"goal": "Declared only"},
            "input_artifacts": [common_ref],
        },
    )
    current = await completed_run(env.service, run["run_id"])
    assert current["status"] == "completed"
    assert len(rejections) == 3 and all(
        "Error:" in rejection for rejection in rejections
    )
    assert current["messages"] == []


@pytest.mark.asyncio
async def test_missing_child_resources_cannot_leave_a_started_root(workflow_env):
    """Freeze all child resources before any root context or execution is created.

    Args:
        workflow_env: Public services and mutable external resource inventory.
    """
    env = workflow_env
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Resource child",
                "coordinator": {
                    "id": "coordinator",
                    "role_id": env.role["role_id"],
                    "role_version": 1,
                },
                "allowed_roles": [
                    {"id": "worker", "role_id": env.role["role_id"], "role_version": 1}
                ],
            }
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Resource parent",
                "required_inputs": ["goal"],
                "tasks": [
                    {
                        "id": "call",
                        "name": "Call",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                    }
                ],
            }
        },
    )
    env.inventory["models"] = []
    with pytest.raises(RoleAssetError):
        await env.service.start_run(
            "alice", asset["asset_id"], {"version": 1, "inputs": {"goal": "Research"}}
        )
    assert (await env.service.list_runs("alice"))["runs"] == []
    assert await env.db.get_platform_sessions_by_creator("alice") == []
    assert env.observations == []


@pytest.mark.asyncio
async def test_graph_field_handoff_does_not_grant_child_full_artifact(workflow_env):
    env = workflow_env
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Child",
                "coordinator": {
                    "id": "coordinator",
                    "role_id": env.role["role_id"],
                    "role_version": 1,
                },
                "allowed_roles": [
                    {"id": "worker", "role_id": env.role["role_id"], "role_version": 1}
                ],
            }
        },
    )
    contract = {
        "type": "object",
        "properties": {"public": {"type": "string"}, "secret": {"type": "string"}},
        "required": ["public", "secret"],
    }
    parent = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Field handoff",
                "result_task_id": "call",
                "tasks": [
                    {
                        "id": "produce",
                        "name": "Produce",
                        "member": "writer",
                        "role_id": env.role["role_id"],
                        "role_version": 1,
                        "task": "Produce",
                        "output_contract": contract,
                    },
                    {
                        "id": "call",
                        "name": "Call",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"task_id": "produce", "field": "public"}},
                    },
                ],
                "edges": [{"source": "produce", "target": "call"}],
            }
        },
    )
    bindings, observed = {}, {}

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        bindings[session_id] = binding
        observed[binding.node_id] = {
            "text": text,
            "context": context,
            "run_id": binding.run_id,
        }
        return message_id

    async def collect(session_id, message_id, member_id):
        binding = bindings[session_id]
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        if binding.node_id == "produce":
            await tools["submit_task_result"].call(
                None, result_json=json.dumps({"public": "visible", "secret": "hidden"})
            )
        else:
            observed["read"] = await tools["read_task_artifact"].call(
                None, artifact_id=observed["produce"]["run_id"], version=1
            )
            await tools["submit_task_result"].call(None, text="Done")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run("alice", parent["asset_id"], {"version": 1})
    finished = await completed_run(env.service, run["run_id"])
    assert finished["status"] == "completed", finished
    assert observed["coordinator"]["text"] == "visible"
    assert "hidden" not in observed["coordinator"]["context"]
    assert "Error" in observed["read"]
    assert finished["scopes"][0]["snapshot"]["input_artifacts"] == []
