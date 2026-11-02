"""Draft trials through the public workflow service and real scheduler."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.workflow_assets import router
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from tests.agent_teams.test_autonomous_assets import team_env as team_env
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env
from tests.agent_teams.test_workflow_loops import loop_script as loop_script
from tests.agent_teams.test_workflow_loops import revision_definition


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected", [("full", ["a", "b"]), ("single", ["b"]), ("partial", ["b"])]
)
async def test_trial_scope_runs_only_selected_nodes(workflow_env, mode, expected):
    env = workflow_env
    calls = []

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        calls.append(
            (AgentTeamExecutionRegistry.resolve(execution_token).node_id, text)
        )
        return message_id

    async def collect(session_id, message_id, member_id):
        await AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[
            0
        ].call(None, text="produced")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    base = {"member": "writer", "role_id": env.role["role_id"], "role_version": 1}
    definition = {
        "name": "Trial graph",
        "result_task_id": "b",
        "tasks": [
            {**base, "id": "a", "name": "A", "task": "first"},
            {
                **base,
                "id": "b",
                "name": "B",
                "task": "second {value}",
                "inputs": {"value": {"task_id": "a", "field": "text"}},
            },
        ],
        "edges": [{"source": "a", "target": "b"}],
    }
    draft = await env.roles.create_draft(
        "alice", "workflow", {"definition": definition}
    )
    request = {"kind": "workflow", "revision": draft["revision"], "mode": mode}
    if mode != "full":
        request.update(node_id="b", sources={"a": {"content": {"text": "fixed"}}})
    preview = await env.service.preview_trial("alice", draft["asset_id"], request)
    assert preview["selected_nodes"] == expected
    run = await env.service.start_trial("alice", draft["asset_id"], request)
    for _ in range(200):
        current = await env.service.get_run("alice", run["run_id"])
        if current["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert current["status"] == "completed", current
    assert [node for node, _ in calls] == expected
    assert calls[-1][1] == ("second produced" if mode == "full" else "second fixed")
    assert current["snapshot"]["asset_version"] is None
    assert current["snapshot"]["trial"]["draft_revision"] == draft["revision"]
    with pytest.raises(RoleAssetError):
        await env.service.start_trial("bob", draft["asset_id"], request)


@pytest.mark.asyncio
async def test_control_trial_uses_typed_boundary_without_result_artifact(workflow_env):
    env = workflow_env
    definition = {
        "name": "Condition trial",
        "result_task_id": "producer",
        "tasks": [
            {
                "id": "producer",
                "name": "Producer",
                "member": "writer",
                "role_id": env.role["role_id"],
                "role_version": 1,
                "task": "produce",
                "output_contract": {
                    "type": "object",
                    "properties": {"score": {"type": "integer"}},
                    "required": ["score"],
                },
            },
            {
                "id": "condition",
                "name": "Condition",
                "control": {"kind": "condition", "operator": "gt", "value": 3},
                "inputs": {"value": {"task_id": "producer", "field": "score"}},
            },
        ],
        "edges": [{"source": "producer", "target": "condition"}],
    }
    draft = await env.roles.create_draft(
        "alice", "workflow", {"definition": definition}
    )
    request = {
        "kind": "workflow",
        "revision": draft["revision"],
        "mode": "single",
        "node_id": "condition",
        "sources": {"producer": {"content": {"score": "5"}}},
    }
    with pytest.raises(RoleAssetError):
        await env.service.start_trial("alice", draft["asset_id"], request)
    request["sources"]["producer"]["content"]["score"] = 5
    run = await env.service.start_trial("alice", draft["asset_id"], request)
    assert run["status"] == "completed"
    assert run["result"] is None
    assert run["tasks"][0]["result"] == {"matched": True}
    assert not run["tasks"][0]["artifacts"]
    await env.roles.update_draft(
        "alice",
        "workflow",
        draft["asset_id"],
        draft["revision"],
        {"definition": {**definition, "name": "Edited"}},
    )
    restored = await env.service.get_run("alice", run["run_id"])
    assert (
        restored["snapshot"]["trial"]["draft"]["definition"]["name"]
        == "Condition trial"
    )
    with pytest.raises(RoleAssetError):
        await env.service.start_trial("alice", draft["asset_id"], request)


@pytest.mark.asyncio
async def test_trial_approval_requires_and_preserves_exact_artifact(workflow_env):
    env = workflow_env
    definition = {
        "name": "Approval trial",
        "result_task_id": "approval",
        "tasks": [
            {
                "id": "report",
                "name": "Report",
                "member": "writer",
                "role_id": env.role["role_id"],
                "role_version": 1,
                "task": "report",
            },
            {
                "id": "approval",
                "name": "Approval",
                "human": {
                    "kind": "approval",
                    "question": "Approve?",
                    "artifact_task_id": "report",
                },
            },
        ],
        "edges": [{"source": "report", "target": "approval"}],
    }
    draft = await env.roles.create_draft(
        "alice", "workflow", {"definition": definition}
    )
    base = {"kind": "workflow", "revision": draft["revision"], "mode": "single"}
    producer = await env.service.start_trial(
        "alice", draft["asset_id"], {**base, "node_id": "report"}
    )
    for _ in range(200):
        producer = await env.service.get_run("alice", producer["run_id"])
        if producer["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert producer["status"] == "completed"
    first_ref = producer["tasks"][0]["result_ref"]
    ref = await env.service.revise_artifact(
        "alice",
        first_ref["artifact_id"],
        {
            "expected_version": 1,
            "request_id": "revision",
            "content": {"text": "Revised"},
        },
    )
    request = {
        **base,
        "node_id": "approval",
        "sources": {"report": {"content": {"text": "fake"}}},
    }
    with pytest.raises(RoleAssetError):
        await env.service.start_trial("alice", draft["asset_id"], request)
    request["sources"] = {"report": {"artifact": ref}}
    run = await env.service.start_trial("alice", draft["asset_id"], request)
    assert run["todos"][0]["artifact_ref"] == ref
    assert (await env.service.list_human_requests("alice"))["requests"][0][
        "trial"
    ] is True
    run = await env.service.answer_todo(
        "alice",
        run["run_id"],
        run["todos"][0]["todo_id"],
        {"request_id": "approve", "action": "approve", "artifact_ref": ref},
    )
    assert run["status"] == "completed"
    assert (await env.service.read_artifact("alice", **first_ref))["content"][
        "text"
    ] != "Revised"


@pytest.mark.asyncio
async def test_trial_loop_feedback_and_scope_rules(loop_script):
    env = loop_script.env
    definition = revision_definition(env.role["role_id"])
    draft = await env.roles.create_draft(
        "alice", "workflow", {"definition": definition}
    )
    base = {"kind": "workflow", "revision": draft["revision"]}
    with pytest.raises(RoleAssetError):
        await env.service.preview_trial(
            "alice", draft["asset_id"], {**base, "mode": "partial", "node_id": "review"}
        )
    single = {
        **base,
        "mode": "single",
        "node_id": "write",
        "sources": {
            "write": {"content": {"text": "previous"}},
            "review": {"content": {"approved": False, "text": "revise"}},
        },
    }
    run = await env.service.start_trial("alice", draft["asset_id"], single)
    for _ in range(300):
        run = await env.service.get_run("alice", run["run_id"])
        if run["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert run["status"] == "completed"
    assert len(loop_script.observations) == 1
    assert (
        loop_script.observations[0]["snapshot"]["task"]
        == "Revise previous. Feedback: revise"
    )
    loop_script.observations.clear()
    run = await env.service.start_trial(
        "alice",
        draft["asset_id"],
        {**base, "mode": "full", "inputs": {"draft": "seed", "feedback": "initial"}},
    )
    for _ in range(500):
        run = await env.service.get_run("alice", run["run_id"])
        if run["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert run["status"] == "completed", run
    assert [item["snapshot"]["node_id"] for item in loop_script.observations] == [
        "write",
        "review",
        "write",
        "review",
        "publish",
    ]
    assert "Report revision 1" in loop_script.observations[2]["snapshot"]["task"]


@pytest.mark.asyncio
async def test_autonomous_full_trial_dispatches_real_dynamic_member(team_env):
    env = team_env

    async def script(tools, binding):
        member = json.loads(
            await tools["team_manage"].call(
                None,
                action="create_member",
                request_id="member",
                preset_id="writer",
                name="Writer",
            )
        )
        task = json.loads(
            await tools["team_manage"].call(
                None,
                action="dispatch",
                request_id="dispatch",
                member_id=member["member_id"],
                name="Evidence",
                task="Find evidence",
            )
        )
        await tools["team_manage"].call(
            None, action="wait", request_id="wait", task_ids=[task["task_id"]]
        )
        await tools["submit_task_result"].call(None, text="Complete")

    env.script = script
    draft = await env.roles.create_draft(
        "alice", "autonomous", {"definition": env.asset["definition"]}
    )
    base = {"kind": "autonomous", "revision": draft["revision"]}
    with pytest.raises(RoleAssetError):
        await env.service.preview_trial(
            "alice",
            draft["asset_id"],
            {**base, "mode": "single", "node_id": "coordinator"},
        )
    run = await env.service.start_trial(
        "alice",
        draft["asset_id"],
        {**base, "mode": "full", "inputs": {"goal": "Research"}},
    )
    for _ in range(400):
        run = await env.service.get_run("alice", run["run_id"])
        if run["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert not env.errors, env.errors
    assert run["status"] == "completed", run
    assert len(env.observations) == 2
    assert run["snapshot"]["asset_version"] is None
    assert run["snapshot"]["trial"]["mode"] == "full"


@pytest.mark.asyncio
async def test_trial_executes_pinned_nested_workflow_with_only_selected_input(
    workflow_env,
):
    env = workflow_env
    calls = []
    nested_contexts = []

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        calls.append(text)
        nested_contexts.append(context)
        return message_id

    async def collect(session_id, message_id, member_id):
        await AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[
            0
        ].call(None, text="nested result")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    agent = {"member": "writer", "role_id": env.role["role_id"], "role_version": 1}
    child = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Child",
                "required_inputs": ["topic"],
                "result_task_id": "worker",
                "tasks": [
                    {
                        **agent,
                        "id": "worker",
                        "name": "Worker",
                        "task": "Nested {topic}",
                        "inputs": {"topic": {"input": "topic"}},
                    }
                ],
            }
        },
    )
    definition = {
        "name": "Parent",
        "result_task_id": "call",
        "tasks": [
            {**agent, "id": "upstream", "name": "Upstream", "task": "Not called"},
            {
                "id": "call",
                "name": "Child",
                "workflow": {"asset_id": child["asset_id"], "version": 1},
                "inputs": {"topic": {"task_id": "upstream", "field": "text"}},
            },
        ],
        "edges": [{"source": "upstream", "target": "call"}],
    }
    draft = await env.roles.create_draft(
        "alice", "workflow", {"definition": definition}
    )
    run = await env.service.start_trial(
        "alice",
        draft["asset_id"],
        {
            "kind": "workflow",
            "revision": draft["revision"],
            "mode": "single",
            "node_id": "call",
            "sources": {"upstream": {"content": {"text": "fixed"}}},
        },
    )
    for _ in range(400):
        run = await env.service.get_run("alice", run["run_id"])
        if run["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert run["status"] == "completed", run
    assert calls == ["Nested fixed"]
    assert '"task_id": "upstream"' in nested_contexts[0]
    assert '"value": "fixed"' in nested_contexts[0]
    assert run["scopes"][0]["snapshot"]["asset_version"] == 1


@pytest.mark.asyncio
async def test_trial_freezes_only_selected_resources_and_saved_revision(workflow_env):
    env = workflow_env
    started, release = asyncio.Event(), asyncio.Event()
    observed = []

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        observed.append((text, context))
        started.set()
        return message_id

    async def collect(session_id, message_id, member_id):
        await release.wait()
        await AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[
            0
        ].call(None, text="finished")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    definition = {
        "name": "Frozen",
        "result_task_id": "b",
        "tasks": [
            {
                "id": "a",
                "name": "Offline",
                "member": "offline",
                "role_id": "missing-role",
                "role_version": 1,
                "task": "never",
                "output_contract": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "secret": {"type": "string"},
                    },
                    "required": ["text", "secret"],
                },
            },
            {
                "id": "b",
                "name": "Chosen",
                "member": "writer",
                "role_id": env.role["role_id"],
                "role_version": 1,
                "task": "Use {text}",
                "inputs": {"text": {"task_id": "a"}},
            },
        ],
        "edges": [{"source": "a", "target": "b"}],
    }
    draft = await env.roles.create_draft(
        "alice", "workflow", {"definition": definition}
    )
    request = {
        "kind": "workflow",
        "revision": draft["revision"],
        "mode": "single",
        "node_id": "b",
        "sources": {"a": {"content": {"text": "fixed", "secret": "not selected"}}},
    }
    run = await env.service.start_trial("alice", draft["asset_id"], request)
    try:
        await asyncio.wait_for(started.wait(), 5)
        await env.roles.update_draft(
            "alice",
            "workflow",
            draft["asset_id"],
            draft["revision"],
            {"definition": {**definition, "name": "Edited"}},
        )
        saved = await env.service.get_run("alice", run["run_id"])
        assert saved["snapshot"]["trial"]["draft"]["definition"]["name"] == "Frozen"
        assert saved["snapshot"]["trial"]["sources"]["a"]["content"] == {
            "text": "fixed",
            "secret": "not selected",
        }
        assert observed[0][0] == "Use fixed"
        assert '"task_id": "a"' in observed[0][1]
        assert '"value": "fixed"' in observed[0][1]
        assert "not selected" not in observed[0][1]
        assert saved["tasks"][0]["snapshot"]["input_artifacts"] == []
    finally:
        release.set()
    for _ in range(300):
        saved = await env.service.get_run("alice", run["run_id"])
        if saved["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert saved["status"] == "completed"
    assert saved["result"] == {"text": "finished"}


@pytest.mark.asyncio
async def test_trial_http_owner_and_formal_snapshot_injection_rejected(workflow_env):
    env = workflow_env
    definition = {
        "name": "HTTP",
        "result_task_id": "a",
        "tasks": [
            {
                "id": "a",
                "name": "A",
                "member": "writer",
                "role_id": env.role["role_id"],
                "role_version": 1,
                "task": "Work",
            }
        ],
    }
    draft = await env.roles.create_draft(
        "alice", "workflow", {"definition": definition}
    )
    asset = await env.service.save_asset("alice", {"definition": definition})
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    path = f"/api/v1/workflow_assets/{draft['asset_id']}/trials"
    payload = {"kind": "workflow", "revision": draft["revision"], "mode": "full"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        preview = await client.post(path + "/preview", json=payload)
        assert preview.status_code == 200
        assert preview.json()["data"]["selected_nodes"] == ["a"]
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        assert (await client.post(path, json=payload)).status_code == 404
        app.dependency_overrides[require_dashboard_user] = lambda: "alice"
        formal_path = f"/api/v1/workflow_assets/{asset['asset_id']}/runs"
        assert (
            await client.post(formal_path, json={"version": 1, "trial": {}})
        ).status_code == 422
        formal = await client.post(formal_path, json={"version": 1, "source": "trial"})
        assert formal.status_code == 200
        overview = (await client.get("/api/v1/workflow_runs")).json()["data"]["runs"][0]
        assert "trial" not in overview["snapshot"]


@pytest.mark.asyncio
async def test_partial_condition_and_parallel_join_boundaries(workflow_env):
    env = workflow_env
    observed = []

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        observed.append(text)
        return message_id

    async def collect(session_id, message_id, member_id):
        await AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[
            0
        ].call(None, text="ok")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    agent = {"member": "writer", "role_id": env.role["role_id"], "role_version": 1}
    definition = {
        "name": "Branch",
        "result_task_id": "yes",
        "tasks": [
            {
                **agent,
                "id": "source",
                "name": "Source",
                "task": "source",
                "output_contract": {
                    "type": "object",
                    "properties": {"flag": {"type": "boolean"}},
                    "required": ["flag"],
                },
            },
            {
                "id": "condition",
                "name": "Condition",
                "control": {"kind": "condition", "operator": "eq", "value": True},
                "inputs": {"value": {"task_id": "source", "field": "flag"}},
            },
            {**agent, "id": "yes", "name": "Yes", "task": "yes"},
            {**agent, "id": "no", "name": "No", "task": "no"},
        ],
        "edges": [
            {"source": "source", "target": "condition"},
            {
                "source": "condition",
                "target": "yes",
                "condition": {"field": "matched", "value": True},
            },
            {
                "source": "condition",
                "target": "no",
                "condition": {"field": "matched", "value": False},
            },
        ],
    }
    draft = await env.roles.create_draft(
        "alice", "workflow", {"definition": definition}
    )
    request = {
        "kind": "workflow",
        "revision": draft["revision"],
        "mode": "partial",
        "node_id": "condition",
        "sources": {"source": {"content": {"flag": True}}},
    }
    run = await env.service.start_trial("alice", draft["asset_id"], request)
    for _ in range(300):
        run = await env.service.get_run("alice", run["run_id"])
        if run["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert run["status"] == "completed", run
    assert observed == ["yes"]
    assert (
        next(task for task in run["tasks"] if task["node_id"] == "no")["status"]
        == "skipped"
    )
    join = {
        "name": "Join",
        "tasks": [
            {**agent, "id": "a", "name": "A", "task": "a"},
            {**agent, "id": "b", "name": "B", "task": "b"},
            {"id": "join", "name": "Join", "control": {"kind": "join"}},
        ],
        "edges": [{"source": "a", "target": "join"}, {"source": "b", "target": "join"}],
    }
    draft = await env.roles.create_draft("alice", "workflow", {"definition": join})
    for mode, node in [("partial", "a"), ("single", "join")]:
        with pytest.raises(RoleAssetError):
            await env.service.preview_trial(
                "alice",
                draft["asset_id"],
                {
                    "kind": "workflow",
                    "revision": draft["revision"],
                    "mode": mode,
                    "node_id": node,
                },
            )


@pytest.mark.asyncio
async def test_trial_recommendation_uses_pinned_result_without_extra_model_call(
    workflow_env,
):
    env = workflow_env
    calls = []

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        calls.append(text)
        return message_id

    async def collect(session_id, message_id, member_id):
        await AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[
            0
        ].call(None, result_json=json.dumps({"choice": "yes", "reason": "evidence"}))
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    definition = {
        "name": "Recommendation",
        "result_task_id": "question",
        "tasks": [
            {
                "id": "recommend",
                "name": "Recommend",
                "member": "writer",
                "role_id": env.role["role_id"],
                "role_version": 1,
                "task": "review",
                "output_contract": {
                    "type": "object",
                    "properties": {
                        "choice": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["choice", "reason"],
                },
            },
            {
                "id": "question",
                "name": "Question",
                "human": {
                    "kind": "question",
                    "question": "Proceed?",
                    "choices": ["yes", "no"],
                    "recommendation_task_id": "recommend",
                    "recommendation_value_field": "choice",
                    "recommendation_reason_field": "reason",
                },
            },
        ],
        "edges": [{"source": "recommend", "target": "question"}],
    }
    draft = await env.roles.create_draft(
        "alice", "workflow", {"definition": definition}
    )
    base = {"kind": "workflow", "revision": draft["revision"], "mode": "single"}
    source = await env.service.start_trial(
        "alice", draft["asset_id"], {**base, "node_id": "recommend"}
    )
    for _ in range(300):
        source = await env.service.get_run("alice", source["run_id"])
        if source["status"] != "running":
            break
        await asyncio.sleep(0.01)
    ref = source["tasks"][0]["result_ref"]
    payload = {
        **base,
        "node_id": "question",
        "sources": {"recommend": {"artifact": ref}},
    }
    run = await env.service.start_trial("alice", draft["asset_id"], payload)
    assert run["todos"][0]["recommendation"] == {"value": "yes", "reason": "evidence"}
    assert run["todos"][0]["recommendation_ref"] == ref
    assert calls == ["review"]


@pytest.mark.asyncio
async def test_trial_artifact_source_does_not_grant_full_artifact_access(workflow_env):
    env = workflow_env
    bindings, contexts, denial = {}, [], []
    source_ref = {}

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        bindings[session_id] = AgentTeamExecutionRegistry.resolve(
            execution_token
        ).node_id
        contexts.append((text, context))
        return message_id

    async def collect(session_id, message_id, member_id):
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        if bindings[session_id] == "producer":
            await tools["submit_task_result"].call(
                None, result_json=json.dumps({"text": "visible", "secret": "hidden"})
            )
        else:
            denial.append(await tools["read_task_artifact"].call(None, **source_ref))
            await tools["submit_task_result"].call(None, text="done")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    base = {"member": "writer", "role_id": env.role["role_id"], "role_version": 1}
    definition = {
        "name": "Projection",
        "result_task_id": "consumer",
        "tasks": [
            {
                **base,
                "id": "producer",
                "name": "Producer",
                "task": "produce",
                "output_contract": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "secret": {"type": "string"},
                    },
                    "required": ["text", "secret"],
                },
            },
            {
                **base,
                "id": "consumer",
                "name": "Consumer",
                "task": "Consume",
                "inputs": {"selected": {"task_id": "producer", "field": "text"}},
            },
        ],
        "edges": [{"source": "producer", "target": "consumer"}],
    }
    draft = await env.roles.create_draft(
        "alice", "workflow", {"definition": definition}
    )
    request = {"kind": "workflow", "revision": draft["revision"], "mode": "single"}
    producer = await env.service.start_trial(
        "alice", draft["asset_id"], {**request, "node_id": "producer"}
    )
    for _ in range(300):
        producer = await env.service.get_run("alice", producer["run_id"])
        if producer["status"] != "running":
            break
        await asyncio.sleep(0.01)
    source_ref.update(producer["tasks"][0]["result_ref"])
    other_draft = await env.roles.create_draft(
        "bob", "workflow", {"definition": definition}
    )
    with pytest.raises(RoleAssetError) as denied:
        await env.service.start_trial(
            "bob",
            other_draft["asset_id"],
            {
                **request,
                "revision": other_draft["revision"],
                "node_id": "consumer",
                "sources": {"producer": {"artifact": source_ref}},
            },
        )
    assert denied.value.status == 404
    assert len(contexts) == 1
    consumer = await env.service.start_trial(
        "alice",
        draft["asset_id"],
        {
            **request,
            "node_id": "consumer",
            "sources": {"producer": {"artifact": source_ref}},
        },
    )
    for _ in range(300):
        consumer = await env.service.get_run("alice", consumer["run_id"])
        if consumer["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert consumer["status"] == "completed", consumer
    assert denial and denial[0].startswith("Error:")
    assert '"value": "visible"' in contexts[-1][1]
    assert "hidden" not in contexts[-1][1]


@pytest.mark.asyncio
async def test_trial_and_published_run_enforce_same_nested_depth(workflow_env):
    env = workflow_env
    leaf = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Leaf",
                "result_task_id": "agent",
                "tasks": [
                    {
                        "id": "agent",
                        "name": "Agent",
                        "member": "writer",
                        "role_id": env.role["role_id"],
                        "role_version": 1,
                        "task": "Must not run",
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
                "result_task_id": "call",
                "tasks": [
                    {
                        "id": "call",
                        "name": "Call leaf",
                        "workflow": {"asset_id": leaf["asset_id"], "version": 1},
                    }
                ],
            }
        },
    )
    definition = {
        "name": "Root",
        "max_depth": 1,
        "result_task_id": "call",
        "tasks": [
            {
                "id": "call",
                "name": "Call middle",
                "workflow": {"asset_id": middle["asset_id"], "version": 1},
            }
        ],
    }
    parent = await env.service.save_asset("alice", {"definition": definition})
    draft = await env.roles.create_draft(
        "alice", "workflow", {"definition": definition}
    )
    published = await env.service.start_run("alice", parent["asset_id"], {"version": 1})
    trial = await env.service.start_trial(
        "alice",
        draft["asset_id"],
        {"kind": "workflow", "revision": draft["revision"], "mode": "full"},
    )
    for run in (published, trial):
        for _ in range(300):
            current = await env.service.get_run("alice", run["run_id"])
            if current["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert current["status"] == "blocked"
        assert len(current["scopes"]) == 1
        assert "depth" in current["scopes"][0]["tasks"][0]["state"]["reason"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revision", "deleted"])
async def test_trial_rechecks_draft_in_run_creation_transaction(workflow_env, change):
    env = workflow_env
    body = {"definition": env.asset["definition"]}
    draft = await env.roles.create_draft("alice", "workflow", body)
    entered, release = asyncio.Event(), asyncio.Event()
    catalog = env.roles.catalog_loader

    async def held_catalog():
        entered.set()
        await release.wait()
        return await catalog()

    env.roles.catalog_loader = held_catalog
    launch = asyncio.create_task(
        env.service.start_trial(
            "alice",
            draft["asset_id"],
            {"kind": "workflow", "revision": draft["revision"], "mode": "full"},
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if change == "revision":
            await env.roles.update_draft(
                "alice",
                "workflow",
                draft["asset_id"],
                draft["revision"],
                {"definition": {**body["definition"], "name": "Edited"}},
            )
        else:
            await env.roles.delete_asset("alice", "workflow", draft["asset_id"])
    finally:
        release.set()
    with pytest.raises(RoleAssetError) as conflict:
        await launch
    assert conflict.value.status == 409
    assert not (await env.service.list_runs("alice"))["runs"]
    assert not env.observations
