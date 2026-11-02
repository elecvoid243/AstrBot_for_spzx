"""Fixed nested graph calls through the public workflow service."""

import pytest

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_fixed_graph_calls_reuse_and_isolate_public_results(workflow_env):
    """Two direct and one nested calls get separate scopes and selected inputs.

    Args:
        workflow_env: Services backed by a temporary database.
    """
    env = workflow_env
    leaf = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Leaf",
                "required_inputs": ["value"],
                "result_task_id": "public",
                "tasks": [
                    {
                        "id": "public",
                        "name": "Public",
                        "member": "writer",
                        "role_id": env.role["role_id"],
                        "role_version": 1,
                        "task": "Leaf {value}",
                        "inputs": {"value": {"input": "value"}},
                    }
                ],
            }
        },
    )
    middle = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Middle",
                "required_inputs": ["value"],
                "result_task_id": "nested",
                "tasks": [
                    {
                        "id": "nested",
                        "name": "Nested",
                        "workflow": {"asset_id": leaf["asset_id"], "version": 1},
                        "inputs": {"value": {"input": "value"}},
                    }
                ],
            }
        },
    )
    outer = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Outer",
                "required_inputs": ["first", "second", "third", "secret"],
                "result_task_id": "third",
                "max_depth": 4,
                "tasks": [
                    {
                        "id": name,
                        "name": name,
                        "workflow": {"asset_id": asset["asset_id"], "version": 1},
                        "inputs": {"value": {"input": name}},
                    }
                    for name, asset in [
                        ("first", leaf),
                        ("second", leaf),
                        ("third", middle),
                    ]
                ],
            }
        },
    )
    observed = {}

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        observed[session_id] = (
            text,
            context,
            AgentTeamExecutionRegistry.resolve(execution_token),
        )
        return message_id

    async def collect(session_id, message_id, member_id):
        tools = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")
        submit = next(tool for tool in tools if tool.name == "submit_task_result")
        await submit.call(None, text=observed[session_id][0])
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run(
        "alice",
        outer["asset_id"],
        {
            "version": 1,
            "inputs": {
                "first": "ONE",
                "second": "TWO",
                "third": "THREE",
                "secret": "HIDDEN",
            },
        },
    )
    result = await completed_run(env.service, run["run_id"])
    assert result["status"] == "completed", result
    assert sorted(item[0] for item in observed.values()) == [
        "Leaf ONE",
        "Leaf THREE",
        "Leaf TWO",
    ]
    assert all("HIDDEN" not in str(item[:2]) for item in observed.values())
    assert len({item[2].conversation_id for item in observed.values()}) == 3
    assert len(result["scopes"]) == 4
    assert result["result"]["text"] == "Leaf THREE"
    assert (
        result["result_ref"]
        == next(task for task in result["tasks"] if task["node_id"] == "third")[
            "result_ref"
        ]
    )
    with pytest.raises(RoleAssetError):
        await env.service.get_asset("bob", leaf["asset_id"], 1)


@pytest.mark.asyncio
async def test_child_calls_validate_exact_inputs_and_version(workflow_env):
    """Reject missing child inputs and missing pinned versions at the call field.

    Args:
        workflow_env: Services backed by a temporary database.
    """
    env = workflow_env
    leaf = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Leaf",
                "required_inputs": ["value"],
                "result_task_id": "a",
                "tasks": [
                    {
                        "id": "a",
                        "name": "A",
                        "member": "a",
                        "role_id": env.role["role_id"],
                        "role_version": 1,
                        "task": "{value}",
                        "inputs": {"value": {"input": "value"}},
                    }
                ],
            }
        },
    )
    payload = {
        "definition": {
            "name": "Parent",
            "result_task_id": "child",
            "tasks": [
                {
                    "id": "child",
                    "name": "Child",
                    "workflow": {"asset_id": leaf["asset_id"], "version": 1},
                    "inputs": {},
                }
            ],
        }
    }
    with pytest.raises(RoleAssetError) as error:
        await env.service.save_asset("alice", payload)
    assert error.value.fields[0]["path"] == "definition.tasks.0.inputs"
    payload["definition"]["tasks"][0]["workflow"]["version"] = 100
    with pytest.raises(RoleAssetError) as error:
        await env.service.save_asset("alice", payload)
    assert error.value.fields[0]["path"] == "definition.tasks.0.workflow"


@pytest.mark.asyncio
async def test_coordinator_uses_pinned_workflow_public_result(workflow_env):
    """The coordinator dispatches an allowed graph and reads its original result.

    Args:
        workflow_env: Services backed by a temporary database.
    """
    import json

    env = workflow_env
    definition = {
        "name": "Leaf",
        "required_inputs": ["topic"],
        "result_task_id": "public",
        "tasks": [
            {
                "id": "public",
                "name": "Public",
                "member": "writer",
                "role_id": env.role["role_id"],
                "role_version": 1,
                "task": "Old {topic}",
                "inputs": {"topic": {"input": "topic"}},
            }
        ],
    }
    leaf = await env.service.save_asset("alice", {"definition": definition})
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Team",
                "coordinator": {
                    "id": "coord",
                    "role_id": env.role["role_id"],
                    "role_version": 1,
                },
                "allowed_roles": [
                    {"id": "worker", "role_id": env.role["role_id"], "role_version": 1}
                ],
                "allowed_workflows": [{"asset_id": leaf["asset_id"], "version": 1}],
                "max_parallel": 1,
            }
        },
    )
    definition["tasks"][0]["task"] = "New {topic}"
    await env.service.save_asset(
        "alice", {"definition": definition, "expected_version": 1}, leaf["asset_id"]
    )
    bindings, requests = {}, []

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        bindings[session_id] = AgentTeamExecutionRegistry.resolve(execution_token)
        requests.append((text, context))
        return message_id

    async def collect(session_id, message_id, member_id):
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        if bindings[session_id].node_id == "coordinator":
            raw = await tools["team_manage"].call(
                None,
                action="call_workflow",
                request_id="call",
                asset_id=leaf["asset_id"],
                version=1,
                name="Call",
                inputs={"topic": "chosen"},
            )
            called = json.loads(raw)
            waited = json.loads(
                await tools["team_manage"].call(
                    None, action="wait", request_id="wait", task_ids=[called["task_id"]]
                )
            )
            assert waited["tasks"][0]["result"] == {"text": "Child result"}
            raw = await tools["read_task_artifact"].call(
                None, **waited["tasks"][0]["result_ref"]
            )
            read = json.loads(raw)
            assert read["content"] == {"text": "Child result"}
            await tools["submit_task_result"].call(None, text="Done")
        else:
            await tools["submit_task_result"].call(None, text="Child result")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run(
        "alice", team["asset_id"], {"version": 1, "inputs": {"goal": "PRIVATE"}}
    )
    result = await completed_run(env.service, run["run_id"])
    assert result["status"] == "completed", result
    assert requests[1][0] == "Old chosen"
    assert "PRIVATE" not in requests[1][1]
    call = next(
        task
        for task in result["tasks"]
        if task["snapshot"]["engine"] == "workflow_call_v2"
    )
    assert call["result_ref"] == result["scopes"][0]["tasks"][0]["result_ref"]


def test_nested_graph_human_wait_survives_process_termination(tmp_path):
    """Resume the same child scope and original artifact after process termination.

    Args:
        tmp_path: Isolated persistent database and subprocess markers.
    """
    import os
    import subprocess
    import sys
    import time

    environment = {**os.environ, "PYTHONUTF8": "1", "ASTRBOT_DISABLE_METRICS": "1"}
    command = [
        sys.executable,
        "-m",
        "tests.agent_teams.fixed_nested_worker",
        str(tmp_path),
    ]
    with (tmp_path / "start.log").open("w", encoding="utf-8") as log:
        worker = subprocess.Popen(
            [*command, "start"], stdout=log, stderr=subprocess.STDOUT, env=environment
        )
        try:
            for _ in range(600):
                if (tmp_path / "handoff.json").exists() or worker.poll() is not None:
                    break
                time.sleep(0.05)
            assert (tmp_path / "handoff.json").exists(), (
                tmp_path / "start.log"
            ).read_text(encoding="utf-8")
        finally:
            worker.terminate()
            worker.wait(timeout=15)
    completed = subprocess.run(
        [*command, "recover"],
        capture_output=True,
        text=True,
        env=environment,
        timeout=45,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (tmp_path / "recovered.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["cancel", "continue"])
async def test_static_child_control_preserves_scope_and_wait(workflow_env, action):
    """Root pause and cancellation control the actual waiting child scope.

    Args:
        workflow_env: Public services and temporary database.
        action: Cancel the family or resume and answer its child.
    """
    env = workflow_env
    child = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Child",
                "result_task_id": "question",
                "tasks": [
                    {
                        "id": "question",
                        "name": "Question",
                        "human": {"kind": "question", "question": "Continue?"},
                    }
                ],
            }
        },
    )
    parent = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Parent",
                "result_task_id": "call",
                "tasks": [
                    {
                        "id": "call",
                        "name": "Call",
                        "workflow": {"asset_id": child["asset_id"], "version": 1},
                    }
                ],
            }
        },
    )
    run = await env.service.start_run("alice", parent["asset_id"], {"version": 1})
    scope_id = run["scopes"][0]["run_id"]
    paused = await env.service.control_run(
        "alice", run["run_id"], {"action": "pause", "request_id": "pause"}
    )
    assert paused["scopes"][0]["control"]["status"] == "paused"
    if action == "cancel":
        cancelled = await env.service.control_run(
            "alice", run["run_id"], {"action": "cancel", "request_id": "cancel"}
        )
        assert cancelled["status"] == "cancelled"
        assert cancelled["scopes"][0]["status"] == "cancelled"
        assert cancelled["scopes"][0]["tasks"][0]["status"] == "cancelled"
    else:
        todo = paused["todos"][0]
        await env.service.answer_todo(
            "alice",
            run["run_id"],
            todo["todo_id"],
            {"action": "answer", "answer": "Yes", "request_id": "answer"},
        )
        still_paused = await env.service.get_run("alice", run["run_id"])
        assert still_paused["tasks"][0]["status"] == "waiting_child"
        await env.service.control_run(
            "alice", run["run_id"], {"action": "continue", "request_id": "continue"}
        )
        complete = await completed_run(env.service, run["run_id"])
        assert complete["status"] == "completed"
        assert complete["scopes"][0]["run_id"] == scope_id
        assert complete["result_ref"] == complete["scopes"][0]["tasks"][0]["result_ref"]


@pytest.mark.asyncio
async def test_repeated_team_through_graph_scopes_isolates_identity_and_inputs(
    workflow_env,
):
    """Invoke one fixed team directly and through a graph without sharing context.

    Args:
        workflow_env: Public services and temporary database.
    """
    env = workflow_env
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Team",
                "coordinator": {
                    "id": "coordinator",
                    "role_id": env.role["role_id"],
                    "role_version": 1,
                },
                "allowed_roles": [
                    {"id": "member", "role_id": env.role["role_id"], "role_version": 1}
                ],
            }
        },
    )
    middle = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Middle",
                "required_inputs": ["goal"],
                "result_task_id": "team",
                "tasks": [
                    {
                        "id": "team",
                        "name": "Team",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                    }
                ],
            }
        },
    )
    parent = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Parent",
                "required_inputs": ["first", "second", "private"],
                "result_task_id": "nested",
                "max_depth": 4,
                "tasks": [
                    {
                        "id": "direct",
                        "name": "Direct",
                        "team": {"asset_id": team["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "first"}},
                    },
                    {
                        "id": "nested",
                        "name": "Nested",
                        "workflow": {"asset_id": middle["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "second"}},
                    },
                ],
            }
        },
    )
    observed = {}

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        observed[session_id] = (
            text,
            context,
            AgentTeamExecutionRegistry.resolve(execution_token),
        )
        return message_id

    async def collect(session_id, message_id, member_id):
        tools = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")
        await next(tool for tool in tools if tool.name == "submit_task_result").call(
            None, text=observed[session_id][0]
        )
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run(
        "alice",
        parent["asset_id"],
        {
            "version": 1,
            "inputs": {"first": "ONE", "second": "TWO", "private": "HIDDEN"},
        },
    )
    result = await completed_run(env.service, run["run_id"])
    assert result["status"] == "completed", result
    assert sorted(item[0] for item in observed.values()) == ["ONE", "TWO"]
    assert all("HIDDEN" not in str(item[:2]) for item in observed.values())
    assert len({item[2].conversation_id for item in observed.values()}) == 2
    assert len(result["scopes"]) == 3
    nested = next(task for task in result["tasks"] if task["node_id"] == "nested")
    assert result["result_ref"] == nested["result_ref"]
    assert not nested["artifacts"]


@pytest.mark.asyncio
async def test_static_child_field_projection_and_original_artifact_permissions(
    workflow_env,
):
    """Pass one typed field into a child while denying its producer artifact.

    Args:
        workflow_env: Public services and temporary database.
    """
    import json

    env = workflow_env
    child = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Child",
                "required_inputs": ["flag"],
                "result_task_id": "consumer",
                "tasks": [
                    {
                        "id": "consumer",
                        "name": "Consumer",
                        "member": "same",
                        "role_id": env.role["role_id"],
                        "role_version": 1,
                        "task": "Flag {flag}",
                        "inputs": {"flag": {"input": "flag"}},
                    }
                ],
            }
        },
    )
    contract = {
        "type": "object",
        "properties": {"flag": {"type": "boolean"}, "secret": {"type": "string"}},
        "required": ["flag", "secret"],
    }
    definition = {
        "name": "Parent",
        "result_task_id": "call",
        "tasks": [
            {
                "id": "producer",
                "name": "Producer",
                "member": "same",
                "role_id": env.role["role_id"],
                "role_version": 1,
                "task": "Produce",
                "output_contract": contract,
            },
            {
                "id": "call",
                "name": "Call",
                "workflow": {"asset_id": child["asset_id"], "version": 1},
                "inputs": {"flag": {"task_id": "producer", "field": "flag"}},
            },
        ],
        "edges": [{"source": "producer", "target": "call"}],
    }
    bad = {
        **definition,
        "tasks": [
            definition["tasks"][0],
            {**definition["tasks"][1], "output_contract": contract},
        ],
    }
    with pytest.raises(RoleAssetError) as mismatch:
        await env.service.save_asset("alice", {"definition": bad})
    assert mismatch.value.fields[0]["path"] == "definition.tasks.1.output_contract"
    parent = await env.service.save_asset("alice", {"definition": definition})
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
        if binding.node_id == "producer":
            await tools["submit_task_result"].call(
                None, result_json=json.dumps({"flag": True, "secret": "HIDDEN"})
            )
        else:
            observed["denied"] = await tools["read_task_artifact"].call(
                None, artifact_id=observed["producer"]["run_id"], version=1
            )
            await tools["submit_task_result"].call(None, text="done")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run("alice", parent["asset_id"], {"version": 1})
    result = await completed_run(env.service, run["run_id"])
    assert result["status"] == "completed", result
    assert observed["consumer"]["text"] == "Flag true"
    assert "HIDDEN" not in observed["consumer"]["context"]
    assert "Error" in observed["denied"]
    assert result["scopes"][0]["snapshot"]["input_artifacts"] == []
    assert result["result_ref"] == result["scopes"][0]["tasks"][0]["result_ref"]


@pytest.mark.asyncio
async def test_call_returns_only_pinned_public_exit_original_artifact(workflow_env):
    """A later child version and later internal task cannot alter the return.

    Args:
        workflow_env: Public services and temporary database.
    """
    env = workflow_env
    base = {
        "name": "Child",
        "result_task_id": "public",
        "tasks": [
            {
                "id": name,
                "name": name,
                "member": "same",
                "role_id": env.role["role_id"],
                "role_version": 1,
                "task": name,
            }
            for name in ["public", "private"]
        ],
    }
    child = await env.service.save_asset("alice", {"definition": base})
    parent = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Parent",
                "result_task_id": "call",
                "tasks": [
                    {
                        "id": "call",
                        "name": "Call",
                        "workflow": {"asset_id": child["asset_id"], "version": 1},
                    }
                ],
            }
        },
    )
    await env.service.save_asset(
        "alice",
        {"definition": {**base, "result_task_id": "private"}, "expected_version": 1},
        child["asset_id"],
    )
    bindings = {}

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        bindings[session_id] = AgentTeamExecutionRegistry.resolve(execution_token)
        return message_id

    async def collect(session_id, message_id, member_id):
        tools = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")
        await next(tool for tool in tools if tool.name == "submit_task_result").call(
            None, text=bindings[session_id].node_id
        )
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run("alice", parent["asset_id"], {"version": 1})
    result = await completed_run(env.service, run["run_id"])
    assert result["status"] == "completed", result
    assert result["result"] == {"text": "public"}
    selected = next(
        task for task in result["scopes"][0]["tasks"] if task["node_id"] == "public"
    )
    assert result["result_ref"] == selected["result_ref"]
    assert result["tasks"][0]["result_ref"] == selected["result_ref"]
    assert result["tasks"][0]["artifacts"] == []
    assert (await env.service.read_artifact("alice", **result["result_ref"]))[
        "content"
    ] == {"text": "public"}


@pytest.mark.asyncio
async def test_static_child_failure_blocks_parent_and_preserves_independent_branch(
    workflow_env,
):
    """A failing child propagates to its caller without suppressing another branch.

    Args:
        workflow_env: Public services and temporary database.
    """
    env = workflow_env
    child = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Child",
                "result_task_id": "fail",
                "tasks": [
                    {
                        "id": "fail",
                        "name": "Fail",
                        "member": "worker",
                        "role_id": env.role["role_id"],
                        "role_version": 1,
                        "task": "Fail",
                    }
                ],
            }
        },
    )
    parent = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Parent",
                "result_task_id": "independent",
                "tasks": [
                    {
                        "id": "call",
                        "name": "Call",
                        "workflow": {"asset_id": child["asset_id"], "version": 1},
                    },
                    {
                        "id": "independent",
                        "name": "Independent",
                        "member": "worker",
                        "role_id": env.role["role_id"],
                        "role_version": 1,
                        "task": "Succeed",
                    },
                ],
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
        if bindings[session_id].node_id == "fail":
            raise RuntimeError("Child failed")
        tools = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")
        await next(tool for tool in tools if tool.name == "submit_task_result").call(
            None, text="Independent"
        )
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run("alice", parent["asset_id"], {"version": 1})
    result = await completed_run(env.service, run["run_id"])
    assert result["status"] == "blocked"
    assert (
        next(task for task in result["tasks"] if task["node_id"] == "call")["status"]
        == "blocked"
    )
    assert (
        next(task for task in result["tasks"] if task["node_id"] == "independent")[
            "status"
        ]
        == "completed"
    )
    assert result["scopes"][0]["tasks"][0]["state"]["reason"] == "Child failed"
