"""Coordinator workflow calls use the public asset and durable run protocols."""

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
async def test_coordinator_calls_pinned_workflow_at_one_capacity(workflow_env):
    """Return complete declared output from an isolated pinned workflow.

    Args:
        workflow_env: Public services with real SQLite and an execution boundary.
    """
    env = workflow_env
    child = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Saved research",
                "result_task_id": "writer",
                "tasks": [
                    {
                        "id": "writer",
                        "name": "Writer",
                        "member": "writer",
                        "role_id": env.role["role_id"],
                        "role_version": 1,
                        "task": "Research {topic}",
                        "inputs": {"topic": {"input": "topic"}},
                    }
                ],
                "required_inputs": ["topic"],
            }
        },
    )
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Coordinator",
                "coordinator": {
                    "id": "coordinator",
                    "role_id": env.role["role_id"],
                    "role_version": 1,
                },
                "allowed_roles": [
                    {"id": "writer", "role_id": env.role["role_id"], "role_version": 1}
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
    await env.service.save_asset(
        "alice",
        {
            "expected_version": 1,
            "definition": {
                **child["definition"],
                "tasks": [
                    {**child["definition"]["tasks"][0], "task": "CHANGED {topic}"}
                ],
            },
        },
        child["asset_id"],
    )
    bindings, requests, receipts = {}, [], []
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
            decision = {
                "action": "call_workflow",
                "request_id": "research-call",
                "asset_id": child["asset_id"],
                "version": 1,
                "name": "Research the declared topic",
                "inputs": {"topic": "  Literal {notes}\n"},
            }
            first = json.loads(await tools["team_manage"].call(None, **decision))
            repeated = json.loads(await tools["team_manage"].call(None, **decision))
            receipts.extend([first, repeated])
            waited = json.loads(
                await tools["team_manage"].call(
                    None,
                    action="wait",
                    request_id="research-wait",
                    task_ids=[first["task_id"]],
                )
            )
            assert waited["tasks"][0]["result"] == {"text": report}
            read = json.loads(
                await tools["read_task_artifact"].call(
                    None, **waited["tasks"][0]["result_ref"]
                )
            )
            assert read["content"] == {"text": report}
            await tools["submit_task_result"].call(None, text="Delivered")
        else:
            await tools["submit_task_result"].call(None, text=report)
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run(
        "alice",
        team["asset_id"],
        {"version": 1, "inputs": {"goal": "PRIVATE_PARENT_GOAL"}},
    )
    await asyncio.gather(
        *(env.service.advance_run("alice", run["run_id"]) for _ in range(3))
    )
    current = await completed_run(env.service, run["run_id"])
    assert current["status"] == "completed", current
    assert receipts[0] == receipts[1]
    assert len(current["scopes"]) == 1
    scope = current["scopes"][0]
    assert scope["snapshot"]["asset_version"] == 1
    coordinator, call = current["tasks"]
    child_task = scope["tasks"][0]
    assert call["result"] == {"text": report}
    assert call["state"]["child_result_ref"] == child_task["result_ref"]
    assert child_task["control"]["root_run_id"] == run["run_id"]
    assert (
        child_task["snapshot"]["member"]["conversation_id"]
        != coordinator["snapshot"]["member"]["conversation_id"]
    )
    assert [text for _, text, _ in requests] == [
        "PRIVATE_PARENT_GOAL",
        "Research   Literal {notes}\n",
    ]
    assert "PRIVATE_PARENT_GOAL" not in requests[1][2]
    assert len([e for e in current["events"] if e["kind"] == "scope.started"]) == 1
    assert len([e for e in current["events"] if e["kind"] == "child.returned"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "missing_input",
        "blank_input",
        "extra_input",
        "version",
        "unallowed",
        "contract",
        "artifact",
    ],
)
async def test_invalid_workflow_decision_leaves_no_child(workflow_env, change):
    """Reject invalid or unauthorized calls without saving partial tasks.

    Args:
        workflow_env: Public services and real SQLite persistence.
        change: Invalid field or fixed workflow authorization.
    """
    env = workflow_env
    child = await env.service.save_asset(
        "alice",
        {
            "definition": {
                **env.asset["definition"],
                "required_inputs": ["topic"],
                "tasks": [
                    {
                        **env.asset["definition"]["tasks"][0],
                        "task": "Read {topic}",
                        "inputs": {"topic": {"input": "topic"}},
                    }
                ],
            }
        },
    )
    await env.service.save_asset(
        "alice",
        {"definition": child["definition"], "expected_version": 1},
        child["asset_id"],
    )
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Restricted coordinator",
                "coordinator": {
                    "id": "coordinator",
                    "role_id": env.role["role_id"],
                    "role_version": 1,
                },
                "allowed_roles": [
                    {"id": "writer", "role_id": env.role["role_id"], "role_version": 1}
                ],
                "allowed_workflows": [
                    {
                        "asset_id": child["asset_id"],
                        "version": 1,
                    }
                ],
            }
        },
    )
    ready, release = asyncio.Event(), asyncio.Event()

    async def collect(session_id, message_id, member_id):
        ready.set()
        await release.wait()
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=lambda *args, **kwargs: asyncio.sleep(
            0, result=kwargs.get("message_id")
        ),
        collect=collect,
        is_busy=lambda _: False,
        emit=lambda _: None,
    )
    run = await env.service.start_run(
        "alice", team["asset_id"], {"version": 1, "inputs": {"goal": "Read"}}
    )
    await asyncio.wait_for(ready.wait(), 2)
    caller = (await env.service.get_run("alice", run["run_id"]))["tasks"][0]
    decision = {
        "action": "call_workflow",
        "request_id": "invalid",
        "name": "Read",
        "asset_id": child["asset_id"],
        "version": 1,
        "inputs": {"topic": "Evidence"},
    }
    if change == "missing_input":
        decision["inputs"] = {}
    elif change == "blank_input":
        decision["inputs"]["topic"] = " \n"
    elif change == "extra_input":
        decision["inputs"]["secret"] = "No"
    elif change == "version":
        decision["version"] = 2
    elif change == "unallowed":
        decision["asset_id"] = env.asset["asset_id"]
    elif change == "contract":
        decision["output_contract"] = {"type": "object", "required": ["other"]}
    else:
        decision["input_artifacts"] = [{"artifact_id": "foreign", "version": "latest"}]
    with pytest.raises(RoleAssetError) as invalid:
        await env.service.manage_team(
            "alice",
            caller["run_id"],
            caller["state"]["attempt_id"],
            caller["state"]["execution_id"],
            decision,
        )
    assert invalid.value.fields
    unchanged = await env.service.get_run("alice", run["run_id"])
    assert len(unchanged["tasks"]) == 1
    assert unchanged["scopes"] == []
    assert "team.call_workflow" not in [event["kind"] for event in unchanged["events"]]
    release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["allowed", "root", "caller", "child_role"])
async def test_mixed_workflow_team_nesting_uses_root_capacity_and_depth(
    workflow_env, limit
):
    """Apply root and role depth limits across both call directions.

    Args:
        workflow_env: Public asset and runtime services with real SQLite.
        limit: The permitted scenario or frozen restriction reached at depth two.
    """
    env = workflow_env
    role = {"role_id": env.role["role_id"], "role_version": 1}
    restricted = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Bounded role",
                "prompt": "Coordinate within one level",
                "provider_id": "model-a",
                "max_depth": 1,
            },
            "installation": {"profile_id": "default"},
        },
    )
    restricted_role = {"role_id": restricted["role_id"], "role_version": 1}
    grandchild = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Nested team",
                "coordinator": {
                    "id": "coordinator",
                    **(restricted_role if limit == "child_role" else role),
                },
                "allowed_roles": [{"id": "writer", **role}],
                "max_parallel": 1,
            }
        },
    )
    child = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Mixed workflow",
                "result_task_id": "team",
                "required_inputs": ["topic"],
                "max_parallel": 1,
                "tasks": [
                    {
                        "id": "team",
                        "name": "Team call",
                        "team": {"asset_id": grandchild["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "topic"}},
                    },
                    {
                        "id": "independent",
                        "name": "Independent",
                        "member": "independent",
                        **role,
                        "task": "Independent work",
                    },
                ],
            }
        },
    )
    parent = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Root team",
                "coordinator": {
                    "id": "coordinator",
                    **(restricted_role if limit == "caller" else role),
                },
                "allowed_roles": [{"id": "writer", **role}],
                "max_parallel": 1,
                "max_depth": 1 if limit == "root" else 3,
                "allowed_workflows": [
                    {
                        "asset_id": child["asset_id"],
                        "version": 1,
                    }
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
        binding = bindings[session_id]
        current = await env.service.get_run("alice", binding.run_id)
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        if current["snapshot"]["asset_id"] == parent["asset_id"]:
            call = json.loads(
                await tools["team_manage"].call(
                    None,
                    action="call_workflow",
                    request_id="mixed-call",
                    name="Mixed workflow",
                    asset_id=child["asset_id"],
                    version=1,
                    inputs={"topic": "Explicit nested goal"},
                )
            )
            result = json.loads(
                await tools["team_manage"].call(
                    None,
                    action="wait",
                    request_id="mixed-wait",
                    task_ids=[call["task_id"]],
                )
            )
            if result["tasks"][0]["status"] == "completed":
                await tools["submit_task_result"].call(None, text="Delivered")
        else:
            assert "team_manage" not in tools or binding.node_id == "coordinator"
            await tools["submit_task_result"].call(
                None,
                text="Independent"
                if binding.node_id == "independent"
                else "Nested result",
            )
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run(
        "alice", parent["asset_id"], {"version": 1, "inputs": {"goal": "Root goal"}}
    )
    current = await completed_run(env.service, run["run_id"])
    assert current["status"] == ("completed" if limit == "allowed" else "blocked"), (
        current
    )
    assert len(current["scopes"]) == (2 if limit == "allowed" else 1)
    workflow = current["scopes"][0]
    assert workflow["tasks"][1]["status"] == "completed"
    if limit != "allowed":
        assert "depth" in workflow["tasks"][0]["state"]["reason"]
    else:
        assert current["scopes"][1]["snapshot"]["depth"] == 2
        assert current["tasks"][1]["result"] == {"text": "Nested result"}


@pytest.mark.asyncio
async def test_skipped_workflow_return_task_is_explicitly_blocked(workflow_env):
    """A completed optional branch cannot manufacture the selected result.

    Args:
        workflow_env: Real public asset and runtime services.
    """
    env = workflow_env
    role = {"role_id": env.role["role_id"], "role_version": 1}
    child = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Optional return",
                "result_task_id": "optional",
                "tasks": [
                    {
                        "id": "decision",
                        "name": "Decision",
                        "member": "writer",
                        **role,
                        "task": "No matching return",
                    },
                    {
                        "id": "optional",
                        "name": "Optional report",
                        "member": "writer",
                        **role,
                        "task": "Optional report",
                    },
                ],
                "edges": [
                    {
                        "source": "decision",
                        "target": "optional",
                        "condition": {
                            "field": "text",
                            "operator": "eq",
                            "value": "Run optional report",
                        },
                    }
                ],
            }
        },
    )
    parent = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Parent",
                "coordinator": {"id": "coordinator", **role},
                "allowed_roles": [{"id": "writer", **role}],
                "max_parallel": 1,
                "allowed_workflows": [
                    {
                        "asset_id": child["asset_id"],
                        "version": 1,
                    }
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
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        if bindings[session_id].node_id == "coordinator":
            await tools["team_manage"].call(
                None,
                action="call_workflow",
                request_id="optional",
                asset_id=child["asset_id"],
                version=1,
                name="Optional return",
                inputs={},
            )
        else:
            await tools["submit_task_result"].call(None, text="Other path selected")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run(
        "alice", parent["asset_id"], {"version": 1, "inputs": {"goal": "Return"}}
    )
    for _ in range(200):
        current = await env.service.get_run("alice", run["run_id"])
        if current.get("scopes") and current["scopes"][0]["status"] == "blocked":
            break
        await asyncio.sleep(0.01)
    await env.service.advance_run("alice", run["run_id"])
    current = await env.service.get_run("alice", run["run_id"])
    assert current["tasks"][1]["status"] == "blocked"
    assert (
        "selected result task did not produce a validated result"
        in current["tasks"][1]["state"]["reason"]
    )
    assert (
        next(
            task
            for task in current["scopes"][0]["tasks"]
            if task["snapshot"]["node_id"] == "optional"
        )["status"]
        == "skipped"
    )
    assert current["tasks"][1]["result_ref"] is None
    await env.service.advance_run("alice", run["run_id"])
    await env.service.advance_run("alice", run["run_id"])
    assert [
        event["kind"]
        for event in (await env.service.get_run("alice", run["run_id"]))["events"]
    ].count("child.returned") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["duplicate", "missing_version", "caller_selected_return", "wrong_kind"]
)
async def test_allowed_workflow_definition_rejects_invalid_fixed_references(
    workflow_env, change
):
    """Save no partial executable asset when fixed workflow declarations are invalid.

    Args:
        workflow_env: Public SQLite asset and runtime services.
        change: Invalid reference, version or selected return task.
    """
    env = workflow_env
    role = {"role_id": env.role["role_id"], "role_version": 1}
    definition = {
        "kind": "autonomous",
        "name": "Coordinator",
        "coordinator": {"id": "coordinator", **role},
        "allowed_roles": [{"id": "writer", **role}],
        "allowed_workflows": [
            {
                "asset_id": env.asset["asset_id"],
                "version": 1,
            }
        ],
    }
    if change == "duplicate":
        definition["allowed_workflows"].append(dict(definition["allowed_workflows"][0]))
    elif change == "missing_version":
        definition["allowed_workflows"][0]["version"] = 2
    elif change == "caller_selected_return":
        definition["allowed_workflows"][0]["result_task_id"] = "other"
    else:
        team = await env.service.save_asset(
            "alice",
            {
                "definition": {
                    "kind": "autonomous",
                    "name": "Other team",
                    "coordinator": {"id": "coordinator", **role},
                    "allowed_roles": [{"id": "writer", **role}],
                }
            },
        )
        definition["allowed_workflows"][0]["asset_id"] = team["asset_id"]
    before = await env.service.list_assets("alice")
    with pytest.raises(RoleAssetError) as invalid:
        await env.service.save_asset("alice", {"definition": definition})
    assert any("allowed_workflows" in field["path"] for field in invalid.value.fields)
    assert await env.service.list_assets("alice") == before
    assert (await env.service.list_runs("alice"))["runs"] == []
