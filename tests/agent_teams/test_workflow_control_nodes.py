"""Condition and ALL nodes exercised through versioned graph execution."""

import asyncio

import pytest
from sqlalchemy import select

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.db.po import AgentWorkflowUsage, ConversationV2, PlatformSession
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env
from tests.agent_teams.test_workflow_graph import (
    graph_script as graph_script,
)
from tests.agent_teams.test_workflow_graph import (
    wait_graph,
)


@pytest.mark.asyncio
async def test_all_waits_for_entered_branches_and_duplicate_wakes_do_not_claim_twice(
    workflow_env,
):
    env = workflow_env
    starts = []
    started = {name: asyncio.Event() for name in ("fast", "slow", "result")}
    releases = {name: asyncio.Event() for name in ("fast", "slow")}
    bindings = {}

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        starts.append(binding.node_id)
        bindings[message_id] = binding.node_id
        started[binding.node_id].set()
        return message_id

    async def collect(session_id, message_id, member_id):
        node = bindings[message_id]
        if node in releases:
            await releases[node].wait()
        tool = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[0]
        await tool.call(None, text="value-" + node)
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    agents = [
        {
            "id": name,
            "name": name,
            "member": name,
            "role_id": env.role["role_id"],
            "role_version": 1,
            "task": "final {value}" if name == "result" else name,
            "inputs": {"value": {"task_id": "fast"}} if name == "result" else {},
            "overrides": {"max_instances": 3, "max_parallel": 3},
        }
        for name in ("fast", "slow", "result")
    ]
    definition = {
        "name": "Barrier",
        "result_task_id": "result",
        "required_inputs": ["choice"],
        "max_parallel": 3,
        "tasks": [
            {
                "id": "condition",
                "name": "Condition",
                "control": {"kind": "condition", "value": "yes"},
                "inputs": {"value": {"input": "choice"}},
            },
            *agents[:2],
            {"id": "join", "name": "Join", "control": {"kind": "join"}},
            agents[2],
        ],
        "edges": [
            {
                "source": "condition",
                "target": "fast",
                "condition": {"field": "matched", "value": True},
            },
            {"source": "fast", "target": "join"},
            {"source": "slow", "target": "join"},
            {"source": "join", "target": "result"},
        ],
    }
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run(
        "alice", asset["asset_id"], {"version": 1, "inputs": {"choice": "yes"}}
    )
    try:
        await asyncio.wait_for(
            asyncio.gather(started["fast"].wait(), started["slow"].wait()), 5
        )
        await asyncio.gather(
            *(env.service.advance_run("alice", run["run_id"]) for _ in range(5))
        )
        current = await env.service.get_run("alice", run["run_id"])
        join = next(task for task in current["tasks"] if task["node_id"] == "join")
        assert join["status"] == "pending"
        assert set(join["state"]["waiting_on"]) == {"fast", "slow"}
        assert join["state"]["wait_kind"] == "predecessors"
        assert not started["result"].is_set()
        cursor = current["cursor"]
        releases["fast"].set()

        async def fast_completed():
            while True:
                snapshot = await env.service.get_run("alice", run["run_id"])
                if (
                    next(
                        task for task in snapshot["tasks"] if task["node_id"] == "fast"
                    )["status"]
                    == "completed"
                ):
                    return snapshot
                await asyncio.sleep(0)

        await asyncio.wait_for(fast_completed(), 5)
        current = await env.service.advance_run("alice", run["run_id"])
        join = next(task for task in current["tasks"] if task["node_id"] == "join")
        assert join["state"]["waiting_on"] == ["slow"]
        changes = await env.service.get_events("alice", run["run_id"], cursor)
        assert any(
            event["kind"] == "task.waiting"
            and event["data"].get("node_id") == "join"
            and event["data"].get("waiting_on") == ["slow"]
            for event in changes["events"]
        )
        assert not started["result"].is_set()
        releases["slow"].set()
        finished = await wait_graph(env.service, run["run_id"])
        assert finished["status"] == "completed", finished
        assert sorted(starts) == ["fast", "result", "slow"]
        tasks = {task["node_id"]: task for task in finished["tasks"]}
        assert tasks["result"]["snapshot"]["inputs"] == {"value": "value-fast"}
        for name in ("condition", "join"):
            assert "member" not in tasks[name]["snapshot"]
            assert tasks[name]["result_ref"] is None and not tasks[name]["artifacts"]
            with pytest.raises(RoleAssetError):
                await env.service.get_result("alice", tasks[name]["run_id"])
        assert finished["result_ref"] == tasks["result"]["result_ref"]
        async with env.db.get_db() as session:
            control_ids = [tasks[name]["run_id"] for name in ("condition", "join")]
            receipts = (
                (
                    await session.execute(
                        select(AgentWorkflowUsage).where(
                            AgentWorkflowUsage.run_id.in_(control_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            conversations = (
                (await session.execute(select(ConversationV2.conversation_id)))
                .scalars()
                .all()
            )
            sessions = (
                (await session.execute(select(PlatformSession.session_id)))
                .scalars()
                .all()
            )
        assert not receipts
        assert set(conversations) == {
            tasks[name]["snapshot"]["member"]["conversation_id"]
            for name in ("fast", "slow", "result")
        }
        assert set(sessions) == {
            tasks[name]["snapshot"]["member"]["session_id"]
            for name in ("fast", "slow", "result")
        }
    finally:
        for release in releases.values():
            release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["one_selected", "all_skipped", "failed"])
async def test_join_paths_and_independent_failure(graph_script, scenario):
    script, env = graph_script, graph_script.env
    if scenario == "failed":
        script.failures.add("a")
    names = ("a", "b", "result", "independent")
    agents = [
        {
            "id": name,
            "name": name,
            "member": name,
            "role_id": env.role["role_id"],
            "role_version": 1,
            "task": name,
            "overrides": {"max_instances": 4, "max_parallel": 4},
        }
        for name in names
    ]
    definition = {
        "name": "Branch outcomes",
        "result_task_id": "independent",
        "required_inputs": ["choice"],
        "tasks": [
            {
                "id": "condition",
                "name": "Condition",
                "control": {"kind": "condition", "value": "yes"},
                "inputs": {"value": {"input": "choice"}},
            },
            *agents[:2],
            {"id": "join", "name": "Join", "control": {"kind": "join"}},
            *agents[2:],
        ],
        "edges": [
            {
                "source": "condition",
                "target": "a",
                "condition": {"field": "matched", "value": scenario != "all_skipped"},
            },
            {
                "source": "condition",
                "target": "b",
                "condition": {"field": "matched", "value": False},
            },
            {"source": "a", "target": "join"},
            {"source": "b", "target": "join"},
            {"source": "join", "target": "result"},
        ],
    }
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run(
        "alice", asset["asset_id"], {"version": 1, "inputs": {"choice": "yes"}}
    )
    result = await wait_graph(env.service, run["run_id"])
    tasks = {task["node_id"]: task for task in result["tasks"]}
    assert tasks["independent"]["status"] == "completed"
    assert tasks["independent"]["result_ref"] is not None
    assert tasks["b"]["status"] == "skipped"
    assert (
        tasks["join"]["status"]
        == {
            "one_selected": "completed",
            "all_skipped": "skipped",
            "failed": "dependency_failed",
        }[scenario]
    )
    assert (
        tasks["result"]["status"]
        == {
            "one_selected": "completed",
            "all_skipped": "skipped",
            "failed": "dependency_failed",
        }[scenario]
    )
    assert result["status"] == ("blocked" if scenario == "failed" else "completed")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        "unconditional_exit",
        "wrong_exit",
        "join_comparison",
        "control_result",
        "control_input",
        "approval_evidence",
        "feedback_artifact",
        "feedback_type",
        "control_loop_exit",
        "wrong_type",
    ],
)
async def test_invalid_control_references_are_rejected(workflow_env, invalid):
    env = workflow_env
    agent = {
        "id": "agent",
        "name": "Agent",
        "member": "writer",
        "role_id": env.role["role_id"],
        "role_version": 1,
        "task": "Write",
    }
    condition = {
        "id": "condition",
        "name": "Condition",
        "control": {"kind": "condition", "value": "yes"},
        "inputs": {"value": {"input": "choice"}},
    }
    join = {"id": "join", "name": "Join", "control": {"kind": "join"}}
    definition = {
        "name": "Invalid",
        "result_task_id": "agent",
        "required_inputs": ["choice"],
        "tasks": [condition, join, agent],
        "edges": [
            {
                "source": "condition",
                "target": "join",
                "condition": {"field": "matched", "value": True},
            },
            {"source": "join", "target": "agent"},
        ],
    }
    if invalid == "unconditional_exit":
        del definition["edges"][0]["condition"]
    elif invalid == "wrong_exit":
        definition["edges"][0]["condition"]["value"] = "true"
    elif invalid == "join_comparison":
        join["control"]["operator"] = "eq"
    elif invalid == "control_result":
        definition["result_task_id"] = "condition"
    elif invalid == "control_input":
        agent["inputs"] = {"value": {"task_id": "condition", "field": "matched"}}
    elif invalid == "approval_evidence":
        definition["tasks"].append(
            {
                "id": "approval",
                "name": "Approval",
                "human": {
                    "kind": "approval",
                    "question": "Approve?",
                    "artifact_task_id": "condition",
                },
            }
        )
        definition["edges"].append({"source": "join", "target": "approval"})
    elif invalid == "feedback_artifact":
        agent["inputs"] = {"value": {"input": "choice"}}
        definition["loops"] = [
            {
                "id": "loop",
                "name": "Loop",
                "body": ["condition", "join", "agent"],
                "exit_task_id": "agent",
                "exit_condition": {"field": "text", "value": "done"},
                "max_iterations": 2,
                "feedback": [
                    {
                        "source": "condition",
                        "field": "matched",
                        "target": "agent",
                        "input": "value",
                    }
                ],
            }
        ]
    elif invalid == "feedback_type":
        agent["output_contract"] = {
            "type": "object",
            "properties": {"approved": {"type": "boolean"}},
            "required": ["approved"],
        }
        definition["loops"] = [
            {
                "id": "loop",
                "name": "Loop",
                "body": ["condition", "join", "agent"],
                "exit_task_id": "agent",
                "exit_condition": {"field": "approved", "value": True},
                "max_iterations": 2,
                "feedback": [
                    {
                        "source": "agent",
                        "field": "approved",
                        "target": "condition",
                        "input": "value",
                    }
                ],
            }
        ]
    elif invalid == "control_loop_exit":
        definition["loops"] = [
            {
                "id": "loop",
                "name": "Loop",
                "body": ["condition"],
                "exit_task_id": "condition",
                "exit_condition": {"field": "matched", "value": True},
                "max_iterations": 2,
            }
        ]
    elif invalid == "wrong_type":
        condition["control"]["value"] = True
    with pytest.raises(RoleAssetError) as exc:
        await env.service.save_asset("alice", {"definition": definition})
    assert exc.value.fields


@pytest.mark.asyncio
async def test_condition_with_skipped_data_source_persists_skip_event(graph_script):
    env = graph_script.env
    agents = [
        {
            "id": name,
            "name": name,
            "member": name,
            "role_id": env.role["role_id"],
            "role_version": 1,
            "task": name,
            "overrides": {"max_instances": 2, "max_parallel": 2},
        }
        for name in ("skip", "selected")
    ]
    definition = {
        "name": "Skipped condition input",
        "result_task_id": "selected",
        "required_inputs": ["choice"],
        "tasks": [
            {
                "id": "outer",
                "name": "Outer",
                "control": {"kind": "condition", "value": "yes"},
                "inputs": {"value": {"input": "choice"}},
            },
            *agents,
            {
                "id": "inner",
                "name": "Inner",
                "control": {"kind": "condition", "value": "anything"},
                "inputs": {"value": {"task_id": "skip", "field": "text"}},
            },
        ],
        "edges": [
            {
                "source": "outer",
                "target": "skip",
                "condition": {"field": "matched", "value": False},
            },
            {
                "source": "outer",
                "target": "selected",
                "condition": {"field": "matched", "value": True},
            },
            {"source": "skip", "target": "inner"},
            {"source": "selected", "target": "inner"},
        ],
    }
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run(
        "alice", asset["asset_id"], {"version": 1, "inputs": {"choice": "yes"}}
    )
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["status"] == "completed"
    inner = next(task for task in finished["tasks"] if task["node_id"] == "inner")
    assert (
        inner["status"] == "skipped" and "input was skipped" in inner["state"]["reason"]
    )
    changes = await env.service.get_events("alice", run["run_id"])
    assert any(
        event["kind"] == "task.skipped"
        and event["data"].get("node_id") == "inner"
        and "input was skipped" in event["data"]["reason"]
        for event in changes["events"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "kind", "operator", "expected", "matched"),
    [
        (True, "boolean", "eq", True, True),
        (False, "boolean", "eq", True, False),
        (4, "integer", "gt", 3, True),
    ],
)
async def test_condition_compares_native_upstream_scalar(
    graph_script, value, kind, operator, expected, matched
):
    script, env = graph_script, graph_script.env
    script.outputs["source"] = {"answer": value}
    source = {
        "id": "source",
        "name": "Source",
        "member": "writer",
        "role_id": env.role["role_id"],
        "role_version": 1,
        "task": "source",
        "output_contract": {
            "type": "object",
            "properties": {"answer": {"type": kind}},
            "required": ["answer"],
        },
    }
    result = {
        "id": "result",
        "name": "Result",
        "member": "writer",
        "role_id": env.role["role_id"],
        "role_version": 1,
        "task": "result",
    }
    condition = {
        "id": "condition",
        "name": "Condition",
        "control": {"kind": "condition", "operator": operator, "value": expected},
        "inputs": {"value": {"task_id": "source", "field": "answer"}},
    }
    definition = {
        "name": "Scalar",
        "result_task_id": "result",
        "tasks": [source, condition, result],
        "edges": [
            {"source": "source", "target": "condition"},
            {
                "source": "condition",
                "target": "result",
                "condition": {"field": "matched", "value": matched},
            },
        ],
    }
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["status"] == "completed", finished
    actual = next(task for task in finished["tasks"] if task["node_id"] == "condition")
    assert actual["result"] == {"matched": matched}
    assert actual["result_ref"] is None


@pytest.mark.asyncio
async def test_join_without_incoming_paths_is_skipped(graph_script):
    env = graph_script.env
    agents = [
        {
            "id": name,
            "name": name,
            "member": "writer",
            "role_id": env.role["role_id"],
            "role_version": 1,
            "task": name,
        }
        for name in ("independent", "after")
    ]
    definition = {
        "name": "Empty join",
        "result_task_id": "independent",
        "tasks": [{"id": "join", "name": "Join", "control": {"kind": "join"}}, *agents],
        "edges": [{"source": "join", "target": "after"}],
    }
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    finished = await wait_graph(env.service, run["run_id"])
    tasks = {task["node_id"]: task for task in finished["tasks"]}
    assert finished["status"] == "completed"
    assert tasks["join"]["status"] == "skipped"
    assert tasks["after"]["status"] == "skipped"
    assert [binding.node_id for binding in graph_script.starts] == ["independent"]
