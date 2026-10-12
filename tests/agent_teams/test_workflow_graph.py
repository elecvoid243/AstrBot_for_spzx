"""Dependency workflows through the shared public asset and run boundary."""

import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_graph_saves_pinned_roles_and_rejects_unbounded_back_edges(workflow_env):
    service, role = workflow_env.service, workflow_env.role
    task = {
        "id": "research",
        "name": "Research",
        "member": "researcher",
        "role_id": role["role_id"],
        "role_version": 1,
        "task": "Research {topic}.",
    }
    definition = {
        "name": "Report",
        "required_inputs": ["topic"],
        "tasks": [task, {**task, "id": "write", "name": "Write"}],
        "edges": [{"source": "research", "target": "write"}],
    }
    saved = await service.save_asset("alice", {"definition": definition})
    assert saved["definition"]["tasks"][1]["role_version"] == 1
    definition["edges"].append({"source": "write", "target": "research"})
    with pytest.raises(RoleAssetError) as invalid:
        await service.save_asset("alice", {"definition": definition})
    assert any(field["code"] == "CYCLE" for field in invalid.value.fields)


@pytest.mark.asyncio
async def test_fast_successor_starts_while_independent_slow_task_is_running(
    workflow_env,
):
    env = workflow_env
    started = []
    task_by_session = {}
    slow_release = asyncio.Event()
    successor_started = asyncio.Event()

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        started.append(text)
        task_by_session[session_id] = text
        if text == "successor":
            successor_started.set()
        return message_id

    async def collect(session_id, message_id, member_id):
        if task_by_session[session_id] == "slow":
            await slow_release.wait()
        tools = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")
        await tools[0].call(None, text="Evidence")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    tasks = [
        {
            "id": name,
            "name": name,
            "member": name,
            "role_id": env.role["role_id"],
            "role_version": 1,
            "task": name,
            "overrides": {"max_instances": 3, "max_parallel": 3},
        }
        for name in ["slow", "fast", "successor"]
    ]
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Parallel",
                "tasks": tasks,
                "edges": [{"source": "fast", "target": "successor"}],
                "max_parallel": 2,
            }
        },
    )
    run = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    try:
        await asyncio.wait_for(successor_started.wait(), 3)
        current = await env.service.get_run("alice", run["run_id"])
        assert (
            next(task for task in current["tasks"] if task["node_id"] == "slow")[
                "status"
            ]
            == "running"
        )
    finally:
        slow_release.set()


@pytest.mark.asyncio
async def test_conditions_skip_one_path_without_blocking_the_merge_and_pin_inputs(
    workflow_env,
):
    env = workflow_env
    observations = []
    task_by_session = {}

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        task_by_session[session_id] = binding.node_id
        observations.append((binding, text))
        return message_id

    async def collect(session_id, message_id, member_id):
        tools = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")
        if len(observations) == 1:
            assert "Error" in await tools[0].call(
                None, result_json='{"approved":"yes"}'
            )
            await tools[0].call(
                None, result_json='{"approved":true,"text":"Evidence v1"}'
            )
        else:
            await tools[0].call(None, text="Accepted result")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    tasks = [
        {
            "id": name,
            "name": name,
            "member": "writer",
            "role_id": env.role["role_id"],
            "role_version": 1,
            "task": name,
        }
        for name in ["review", "accept", "reject", "merge"]
    ]
    tasks[0]["output_contract"] = {
        "type": "object",
        "properties": {"approved": {"type": "boolean"}, "text": {"type": "string"}},
        "required": ["approved", "text"],
        "additionalProperties": False,
    }
    tasks[1]["inputs"] = {"evidence": {"task_id": "review", "field": "text"}}
    tasks[1]["task"] = "accept {evidence}"
    definition = {
        "name": "Review",
        "tasks": tasks,
        "edges": [
            {
                "source": "review",
                "target": "accept",
                "condition": {"field": "approved", "operator": "eq", "value": True},
            },
            {
                "source": "review",
                "target": "reject",
                "condition": {"field": "approved", "operator": "eq", "value": False},
            },
            {"source": "accept", "target": "merge"},
            {"source": "reject", "target": "merge"},
        ],
    }
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    for _ in range(200):
        current = await env.service.get_run("alice", run["run_id"])
        if current["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert current["status"] == "completed", current
    assert [text for _, text in observations] == [
        "review",
        "accept Evidence v1",
        "merge",
    ]
    assert (
        next(task for task in current["tasks"] if task["node_id"] == "reject")["status"]
        == "skipped"
    )
    consumer = next(task for task in current["tasks"] if task["node_id"] == "accept")
    producer = next(task for task in current["tasks"] if task["node_id"] == "review")
    assert consumer["snapshot"]["member"] == producer["snapshot"]["member"]
    assert consumer["snapshot"]["input_artifacts"] == [producer["result_ref"]]
    await env.service.revise_artifact(
        "alice",
        producer["result_ref"]["artifact_id"],
        {
            "expected_version": 1,
            "request_id": "v2",
            "content": {"approved": False, "text": "Evidence v2"},
        },
    )
    reread = await env.service.get_run("alice", run["run_id"])
    assert (
        next(task for task in reread["tasks"] if task["node_id"] == "accept")[
            "snapshot"
        ]
        == consumer["snapshot"]
    )
    assert len(observations) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,code",
    [
        ("missing-source", "REFERENCE"),
        ("not-upstream", "REFERENCE"),
        ("unknown-field", "CONDITION"),
        ("wrong-type", "CONDITION"),
        ("expression", "CONDITION"),
    ],
)
async def test_invalid_graph_fields_are_rejected_before_a_version_is_saved(
    workflow_env, change, code
):
    env = workflow_env
    task = {
        "id": "first",
        "name": "First",
        "member": "one",
        "role_id": env.role["role_id"],
        "role_version": 1,
        "task": "Task",
    }
    definition = {
        "name": "Invalid",
        "tasks": [task, {**task, "id": "second"}],
        "edges": [
            {
                "source": "first",
                "target": "second",
                "condition": {"field": "text", "operator": "eq", "value": "ok"},
            }
        ],
    }
    if change == "missing-source":
        definition["edges"][0]["source"] = "absent"
    elif change == "not-upstream":
        task["inputs"] = {"value": {"task_id": "second"}}
    elif change == "wrong-type":
        definition["edges"][0]["condition"]["value"] = True
    else:
        definition["edges"][0]["condition"]["field"] = (
            "missing" if change == "unknown-field" else "text == 'ok'"
        )
    before = await env.service.list_assets("alice")
    with pytest.raises(RoleAssetError) as invalid:
        await env.service.save_asset("alice", {"definition": definition})
    assert any(field["code"] == code for field in invalid.value.fields)
    assert await env.service.list_assets("alice") == before


@pytest_asyncio.fixture
async def graph_script(workflow_env):
    """Provide a controllable external execution port for graph behavior tests.

    Args:
        workflow_env: Real database and public asset/run services.

    Yields:
        Scripts, starts and public services shared by scheduling scenarios.
    """
    env = workflow_env
    script = SimpleNamespace(
        env=env,
        holds={},
        failures=set(),
        starts=[],
        texts=[],
        active=set(),
        overlap=False,
        outputs={},
    )
    bindings = {}

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        if session_id in script.active:
            script.overlap = True
        script.active.add(session_id)
        bindings[message_id] = binding
        script.starts.append(binding)
        script.texts.append(text)
        return message_id

    async def collect(session_id, message_id, member_id):
        binding = bindings[message_id]
        try:
            if binding.node_id in script.holds:
                await script.holds[binding.node_id].wait()
            if binding.node_id in script.failures:
                raise RuntimeError("External model failed")
            tools = AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
            if binding.node_id in script.outputs:
                await tools[0].call(
                    None, result_json=json.dumps(script.outputs[binding.node_id])
                )
            else:
                await tools[0].call(None, text="Validated " + binding.node_id)
            return "Done", []
        finally:
            script.active.remove(session_id)

    script.ports = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    env.service.ports_factory = script.ports
    try:
        yield script
    finally:
        for hold in script.holds.values():
            hold.set()


async def wait_graph(service, run_id):
    """Wait for a terminal public graph snapshot with a bounded timeout.

    Args:
        service: Public workflow application service.
        run_id: Root graph identity to observe.

    Returns:
        Persisted terminal run including its task results.

    Raises:
        AssertionError: Scheduling did not settle within five seconds.
    """
    for _ in range(500):
        run = await service.get_run("alice", run_id)
        if run["status"] != "running":
            return run
        await asyncio.sleep(0.01)
    raise AssertionError("Workflow did not settle")


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("source", ["workflow", "result"])
async def test_task_aliases_resolve_from_frozen_workflow_inputs(
    graph_script, reverse, source
):
    script, env = graph_script, graph_script.env
    aliases = {
        "a": {"input": "b"}
        if source == "workflow"
        else {"task_id": "first", "field": "text"},
        "copy_a": {"input": "a"},
    }
    if reverse:
        aliases = dict(reversed(list(aliases.items())))
    first = {
        "id": "first",
        "name": "First",
        "member": "writer",
        "role_id": env.role["role_id"],
        "role_version": 1,
        "task": "First",
    }
    consumer = {
        **first,
        "id": "consumer",
        "task": "alias={a}; original={copy_a}",
        "inputs": aliases,
    }
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Input aliases",
                "required_inputs": ["a", "b"],
                "tasks": [first, consumer] if source == "result" else [consumer],
                "edges": [{"source": "first", "target": "consumer"}]
                if source == "result"
                else [],
            }
        },
    )
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {"version": 1, "inputs": {"a": "ORIGINAL_A", "b": "ORIGINAL_B"}},
    )
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["status"] == "completed"
    expected_alias = "ORIGINAL_B" if source == "workflow" else "Validated first"
    assert script.texts[-1] == f"alias={expected_alias}; original=ORIGINAL_A"
    assert finished["snapshot"]["inputs"] == {"a": "ORIGINAL_A", "b": "ORIGINAL_B"}
    assert finished["tasks"][-1]["snapshot"]["inputs"] == {
        "a": expected_alias,
        "b": "ORIGINAL_B",
        "copy_a": "ORIGINAL_A",
    }


@pytest.mark.asyncio
async def test_same_member_is_serial_while_another_member_finishes(graph_script):
    script, env = graph_script, graph_script.env
    script.holds["first"] = asyncio.Event()
    tasks = [
        {
            "id": name,
            "name": name,
            "member": "shared" if name != "independent" else "other",
            "role_id": env.role["role_id"],
            "role_version": 1,
            "task": name,
            "overrides": {"max_instances": 3, "max_parallel": 3},
        }
        for name in ["first", "second", "independent"]
    ]
    asset = await env.service.save_asset(
        "alice", {"definition": {"name": "Members", "tasks": tasks, "max_parallel": 3}}
    )
    run = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    for _ in range(200):
        current = await env.service.get_run("alice", run["run_id"])
        if current["tasks"][2]["status"] == "completed":
            break
        await asyncio.sleep(0.01)
    assert [task["status"] for task in current["tasks"]] == [
        "running",
        "pending",
        "completed",
    ]
    script.holds["first"].set()
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["status"] == "completed"
    assert not script.overlap
    assert (
        finished["tasks"][0]["snapshot"]["member"]
        == finished["tasks"][1]["snapshot"]["member"]
    )
    assert (
        finished["tasks"][2]["snapshot"]["member"]["conversation_id"]
        != finished["tasks"][0]["snapshot"]["member"]["conversation_id"]
    )


@pytest.mark.asyncio
async def test_failure_only_blocks_related_dependencies(graph_script):
    script, env = graph_script, graph_script.env
    script.failures.add("failure")
    tasks = [
        {
            "id": name,
            "name": name,
            "member": name,
            "role_id": env.role["role_id"],
            "role_version": 1,
            "task": name,
            "overrides": {"max_instances": 4, "max_parallel": 4},
        }
        for name in ["failure", "blocked", "independent", "successor"]
    ]
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Local failure",
                "tasks": tasks,
                "edges": [
                    {"source": "failure", "target": "blocked"},
                    {"source": "independent", "target": "successor"},
                ],
            }
        },
    )
    run = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["status"] == "blocked"
    assert [task["status"] for task in finished["tasks"]] == [
        "failed",
        "dependency_failed",
        "completed",
        "completed",
    ]
    assert {binding.node_id for binding in script.starts} == {
        "failure",
        "independent",
        "successor",
    }
    overview = (await env.service.list_runs("alice"))["runs"][0]
    assert overview["run_id"] == run["run_id"]
    assert overview["progress"] == {
        "total": 4,
        "completed": 2,
        "running": 0,
        "waiting": 2,
    }
    assert [task["status"] for task in overview["tasks"]] == [
        "failed",
        "dependency_failed",
        "completed",
        "completed",
    ]
    assert overview["tasks"][1]["state"]["reason"] == (
        "An upstream task has no validated result."
    )
    assert (
        overview["tasks"][1]["state"]["attempt_id"]
        == (finished["tasks"][1]["state"]["attempt_id"])
    )
    assert len(overview["artifacts"]) == 2
    assert all(
        "process" not in task and "events" not in task for task in overview["tasks"]
    )
    unchanged = await env.service.get_events("alice", run["run_id"], finished["cursor"])
    assert unchanged["snapshot"] is None
    assert unchanged["cursor"] == finished["cursor"]


@pytest.mark.asyncio
async def test_duplicate_notifications_from_two_services_do_not_dispatch_twice(
    graph_script,
):
    script, env = graph_script, graph_script.env
    script.holds["first"] = asyncio.Event()
    task = {
        "id": "first",
        "name": "First",
        "member": "writer",
        "role_id": env.role["role_id"],
        "role_version": 1,
        "task": "First",
    }
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Notifications",
                "tasks": [task, {**task, "id": "next", "name": "Next"}],
                "edges": [{"source": "first", "target": "next"}],
            }
        },
    )
    run = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    observer = WorkflowService(env.db, env.roles, ports_factory=script.ports)
    try:
        await asyncio.gather(
            *(
                service.advance_run("alice", run["run_id"])
                for service in [env.service, observer] * 5
            )
        )
        script.holds["first"].set()
        await asyncio.gather(
            *(
                service.advance_run("alice", run["run_id"])
                for service in [env.service, observer] * 5
            )
        )
        finished = await wait_graph(env.service, run["run_id"])
        assert finished["status"] == "completed"
        assert [binding.node_id for binding in script.starts] == ["first", "next"]
        assert (
            len(
                [
                    event
                    for event in finished["events"]
                    if event["kind"] == "task.completed"
                ]
            )
            == 2
        )
        assert len(finished["artifacts"]) == 2
    finally:
        await observer.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field_contract,value",
    [
        ({"type": "null"}, None),
        ({"allOf": [{"type": "boolean"}, {"const": True}]}, True),
        ({"type": ["boolean", "null"]}, True),
        ({"type": ["boolean", "null"]}, None),
    ],
)
async def test_scalar_conditions_round_trip_and_execute(
    graph_script, field_contract, value
):
    script, env = graph_script, graph_script.env
    script.outputs["first"] = {"answer": value}
    task = {
        "id": "first",
        "name": "First",
        "member": "writer",
        "role_id": env.role["role_id"],
        "role_version": 1,
        "task": "First",
        "output_contract": {
            "type": "object",
            "properties": {"answer": field_contract},
            "required": ["answer"],
            "additionalProperties": False,
        },
    }
    next_task = {**task, "id": "next", "name": "Next", "output_contract": None}
    edge = {
        "source": "first",
        "target": "next",
        "condition": {"field": "answer", "operator": "eq", "value": value},
    }
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Scalar conditions",
                "tasks": [task, next_task],
                "edges": [edge],
            }
        },
    )
    reopened = WorkflowService(env.db, env.roles, ports_factory=script.ports)
    try:
        saved = await reopened.get_asset("alice", asset["asset_id"])
        assert saved["definition"]["edges"][0]["condition"]["value"] is value
        run = await reopened.start_run("alice", asset["asset_id"], {"version": 1})
        assert (await wait_graph(reopened, run["run_id"]))["status"] == "completed"
        assert [binding.node_id for binding in script.starts] == ["first", "next"]
    finally:
        await reopened.shutdown()


@pytest.mark.asyncio
async def test_non_scalar_union_condition_returns_a_field_error(workflow_env):
    env = workflow_env
    task = {
        "id": "first",
        "name": "First",
        "member": "writer",
        "role_id": env.role["role_id"],
        "role_version": 1,
        "task": "First",
        "output_contract": {
            "type": "object",
            "properties": {"answer": {"type": ["array"]}},
            "required": ["answer"],
        },
    }
    with pytest.raises(RoleAssetError) as invalid:
        await env.service.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Non-scalar",
                    "tasks": [task, {**task, "id": "next"}],
                    "edges": [
                        {
                            "source": "first",
                            "target": "next",
                            "condition": {"field": "answer", "value": None},
                        }
                    ],
                }
            },
        )
    assert invalid.value.fields[0]["code"] == "CONDITION"


@pytest.mark.asyncio
async def test_graph_root_cannot_submit_read_or_store_as_a_task(graph_script):
    script, env = graph_script, graph_script.env
    script.holds["second"] = asyncio.Event()
    task = {
        "id": "first",
        "name": "First",
        "member": "writer",
        "role_id": env.role["role_id"],
        "role_version": 1,
        "task": "First",
    }
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Root scope",
                "tasks": [task, {**task, "id": "second"}],
                "edges": [{"source": "first", "target": "second"}],
            }
        },
    )
    run = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    for _ in range(200):
        current = await env.service.get_run("alice", run["run_id"])
        if current["tasks"][0]["result_ref"]:
            break
        await asyncio.sleep(0.01)
    reference = current["tasks"][0]["result_ref"]
    assert reference is not None
    with pytest.raises(RoleAssetError) as invalid:
        await env.service.submit_result(
            "alice", run["run_id"], "root-attempt", {"text": "Wrong scope"}
        )
    assert invalid.value.status == 409

    with pytest.raises(RoleAssetError) as invalid:
        await env.service.read_artifact(
            "alice", reference["artifact_id"], 1, run["run_id"], None
        )
    assert invalid.value.status == 409
    with pytest.raises(RoleAssetError) as invalid:
        await env.service.store_file(
            "alice", run["run_id"], "root-attempt", "output.txt", b"Wrong scope"
        )
    assert invalid.value.status == 409
    script.holds["second"].set()
    assert (await wait_graph(env.service, run["run_id"]))["status"] == "completed"
    with pytest.raises(RoleAssetError) as invalid:
        await env.service.store_file(
            "alice", run["run_id"], None, "output.txt", b"Wrong scope"
        )
    assert invalid.value.status == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_allof_combines_same_property_constraints_across_branches(
    graph_script, nested
):
    script, env = graph_script, graph_script.env
    contract = {
        "type": "object",
        "allOf": [
            {"properties": {"approved": {"description": "Approval flag"}}},
            {"properties": {"approved": {"type": "boolean"}}},
        ],
        "required": ["approved"],
    }
    output = {"approved": True}
    if nested:
        contract = {
            "type": "object",
            "properties": {"decision": contract},
            "required": ["decision"],
        }
        output = {"decision": output}
    script.outputs["first"] = output
    task = {
        "id": "first",
        "name": "First",
        "member": "writer",
        "role_id": env.role["role_id"],
        "role_version": 1,
        "task": "First",
        "output_contract": contract,
    }
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Combined fields",
                "tasks": [task, {**task, "id": "next", "output_contract": None}],
                "edges": [
                    {
                        "source": "first",
                        "target": "next",
                        "condition": {
                            "field": "decision.approved" if nested else "approved",
                            "value": True,
                        },
                    }
                ],
            }
        },
    )
    run = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    assert (await wait_graph(env.service, run["run_id"]))["status"] == "completed"
    assert [binding.node_id for binding in script.starts] == ["first", "next"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actual,expected,operator,selected",
    [
        (True, 1, "eq", False),
        (True, 1, "ne", True),
        (1, True, "eq", False),
        (1, True, "ne", True),
        (1, 1.0, "eq", True),
        (1, 1.0, "ne", False),
    ],
)
async def test_union_conditions_distinguish_booleans_from_numbers(
    graph_script, actual, expected, operator, selected
):
    script, env = graph_script, graph_script.env
    script.outputs["first"] = {"answer": actual}
    task = {
        "id": "first",
        "name": "First",
        "member": "writer",
        "role_id": env.role["role_id"],
        "role_version": 1,
        "task": "First",
        "output_contract": {
            "type": "object",
            "properties": {"answer": {"type": ["boolean", "number"]}},
            "required": ["answer"],
        },
    }
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Typed equality",
                "tasks": [task, {**task, "id": "next", "output_contract": None}],
                "edges": [
                    {
                        "source": "first",
                        "target": "next",
                        "condition": {
                            "field": "answer",
                            "operator": operator,
                            "value": expected,
                        },
                    }
                ],
            }
        },
    )
    run = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["status"] == "completed"
    assert finished["tasks"][1]["status"] == ("completed" if selected else "skipped")
    assert [binding.node_id for binding in script.starts] == (
        ["first", "next"] if selected else ["first"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intermediate_type", [["object", "null"], ["object", "string"], None]
)
@pytest.mark.parametrize("reference_kind", ["condition", "input"])
async def test_nested_field_references_require_object_intermediates(
    workflow_env, intermediate_type, reference_kind
):
    env = workflow_env
    decision = {
        "properties": {"approved": {"type": "boolean"}},
        "required": ["approved"],
    }
    if intermediate_type is not None:
        decision["type"] = intermediate_type
    task = {
        "id": "first",
        "name": "First",
        "member": "writer",
        "role_id": env.role["role_id"],
        "role_version": 1,
        "task": "First",
        "output_contract": {
            "type": "object",
            "properties": {"decision": decision},
            "required": ["decision"],
        },
    }
    next_task = {**task, "id": "next", "output_contract": None}
    edge = {"source": "first", "target": "next"}
    if reference_kind == "condition":
        edge["condition"] = {"field": "decision.approved", "value": True}
    else:
        next_task["inputs"] = {
            "approved": {"task_id": "first", "field": "decision.approved"}
        }
    with pytest.raises(RoleAssetError) as invalid:
        await env.service.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Guaranteed nested fields",
                    "tasks": [task, next_task],
                    "edges": [edge],
                }
            },
        )
    assert invalid.value.fields[0]["code"] == (
        "CONDITION" if reference_kind == "condition" else "REFERENCE"
    )
