"""Bounded revision histories through the shared public asset/run boundary."""

import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env
from tests.agent_teams.test_workflow_graph import wait_graph


def revision_definition(role_id, max_iterations=3):
    """Declare a two-task body and explicit previous-round feedback.

    Args:
        role_id: Pinned role shared by the writer and independent reviewer.
        max_iterations: Finite number of permitted rounds.

    Returns:
        Public asset definition used by the loop scenarios.
    """
    return {
        "name": "Reviewed report",
        "required_inputs": ["draft", "feedback"],
        "max_parallel": 1,
        "tasks": [
            {
                "id": "write",
                "name": "Writer",
                "member": "writer",
                "role_id": role_id,
                "role_version": 1,
                "overrides": {"max_instances": 2},
                "task": "Revise {draft}. Feedback: {feedback}",
                "inputs": {
                    "draft": {"input": "draft"},
                    "feedback": {"input": "feedback"},
                },
            },
            {
                "id": "review",
                "name": "Independent review",
                "member": "reviewer",
                "role_id": role_id,
                "role_version": 1,
                "overrides": {"max_instances": 2},
                "task": "Review {draft}",
                "inputs": {"draft": {"task_id": "write"}},
                "output_contract": {
                    "type": "object",
                    "properties": {
                        "approved": {"type": "boolean"},
                        "text": {"type": "string"},
                    },
                    "required": ["approved", "text"],
                },
            },
            {
                "id": "publish",
                "name": "Final consumer",
                "member": "writer",
                "role_id": role_id,
                "role_version": 1,
                "overrides": {"max_instances": 2},
                "task": "Deliver {draft}",
                "inputs": {"draft": {"task_id": "write"}},
            },
        ],
        "edges": [
            {"source": "write", "target": "review"},
            {"source": "review", "target": "publish"},
        ],
        "loops": [
            {
                "id": "revision",
                "name": "Review and revise",
                "body": ["write", "review"],
                "exit_task_id": "review",
                "exit_condition": {"field": "approved", "value": True},
                "max_iterations": max_iterations,
                "feedback": [
                    {
                        "target": "write",
                        "input": "draft",
                        "source": "write",
                        "field": "text",
                    },
                    {
                        "target": "write",
                        "input": "feedback",
                        "source": "review",
                        "field": "text",
                    },
                ],
            }
        ],
    }


@pytest_asyncio.fixture
async def loop_script(workflow_env):
    """Control only external execution while retaining real scheduling and storage.

    Args:
        workflow_env: Existing public services and temporary persistent database.

    Yields:
        External observations and configurable review outcomes.
    """
    env = workflow_env
    script = SimpleNamespace(
        env=env, observations=[], approved_round=2, holds={}, failures=set()
    )
    bindings = {}

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        """Observe the committed Task and Attempt at the external delivery boundary.

        Args:
            session_id: Frozen member session receiving this execution.
            text: Rendered task instructions sent to the external Agent port.
            context: Existing port's optional conversation override.
            execution_token: Registered main-pipeline execution capability.
            message_id: Persisted delivery identity for the current Attempt.

        Returns:
            The existing delivery identity without creating another execution.
        """
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        saved = await env.service.get_run("alice", binding.run_id)
        bindings[message_id] = saved
        script.observations.append(saved)
        return message_id

    async def collect(session_id, message_id, member_id):
        """Hold or fail external work and submit through the real result protocol.

        Args:
            session_id: Isolated member session with the public submission tool.
            message_id: Delivery whose frozen Task snapshot was observed.
            member_id: Existing port's execution member identity.

        Returns:
            A complete external response after the result was accepted.

        Raises:
            RuntimeError: The scripted external model fails in this round.
        """
        saved = bindings[message_id]
        node = saved["snapshot"]["node_id"]
        iteration = saved["snapshot"].get("iteration", 1)
        if (node, iteration) in script.holds:
            await script.holds[node, iteration].wait()
        if (node, iteration) in script.failures:
            raise RuntimeError("External model failed")
        result = (
            {"text": f"Report revision {iteration}"}
            if node == "write"
            else (
                {
                    "approved": iteration >= script.approved_round,
                    "text": f"Review {iteration}: add evidence",
                }
                if node == "review"
                else {"text": saved["snapshot"]["inputs"]["draft"]}
            )
        )
        tool = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[0]
        arguments = {"result_json": json.dumps(result)} if node == "review" else result
        assert await tool.call(None, **arguments) == "Result accepted."
        return "Submitted", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    try:
        yield script
    finally:
        for hold in script.holds.values():
            hold.set()


@pytest.mark.asyncio
async def test_failed_review_creates_new_tasks_and_pins_explicit_revision(loop_script):
    env = loop_script.env
    asset = await env.service.save_asset(
        "alice", {"definition": revision_definition(env.role["role_id"])}
    )
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {
            "version": 1,
            "inputs": {"draft": "Original draft", "feedback": "Initial instructions"},
        },
    )
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["status"] == "completed"
    rounds = finished["loops"][0]["iterations"]
    assert [item["status"] for item in rounds] == ["rejected", "passed"]
    first, second = rounds[0]["tasks"], rounds[1]["tasks"]
    assert {task["run_id"] for task in first}.isdisjoint(
        task["run_id"] for task in second
    )
    assert {task["state"]["attempt_id"] for task in first}.isdisjoint(
        task["state"]["attempt_id"] for task in second
    )
    assert second[0]["snapshot"]["inputs"] == {
        "draft": "Report revision 1",
        "feedback": "Review 1: add evidence",
    }
    assert second[0]["snapshot"]["input_artifacts"] == [
        first[0]["result_ref"],
        first[1]["result_ref"],
    ]
    assert second[1]["snapshot"]["input_artifacts"] == [second[0]["result_ref"]]
    assert first[1]["result"] == {"approved": False, "text": "Review 1: add evidence"}
    assert finished["tasks"][-1]["result"] == {"text": "Report revision 2"}
    assert len(loop_script.observations) == 5

    await env.service.advance_run("alice", run["run_id"])
    await env.service.revise_artifact(
        "alice",
        first[0]["result_ref"]["artifact_id"],
        {
            "expected_version": 1,
            "request_id": "outside-revision",
            "content": {"text": "Later manual edit"},
        },
    )
    reread = await env.service.get_run("alice", run["run_id"])
    assert (
        reread["loops"][0]["iterations"][0]["tasks"][1]["snapshot"]
        == first[1]["snapshot"]
    )
    assert len(loop_script.observations) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 2])
async def test_exhausted_loop_keeps_history_and_never_releases_final_consumer(
    loop_script, limit
):
    env = loop_script.env
    loop_script.approved_round = 99
    asset = await env.service.save_asset(
        "alice", {"definition": revision_definition(env.role["role_id"], limit)}
    )
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {"version": 1, "inputs": {"draft": "Original", "feedback": "Check"}},
    )
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["status"] == "blocked"
    assert finished["loops"][0]["status"] == "exhausted"
    assert len(finished["loops"][0]["iterations"]) == limit
    assert finished["tasks"][-1]["status"] == "dependency_failed"
    with pytest.raises(RoleAssetError, match="no completed result"):
        await env.service.get_result("alice", run["run_id"])
    await env.service.advance_run("alice", run["run_id"])
    assert len(loop_script.observations) == limit * 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "expression",
        "optional",
        "type",
        "bound",
        "overlap",
        "missing",
        "feedback",
        "exit",
    ],
)
async def test_invalid_loop_cannot_be_saved(workflow_env, change):
    env = workflow_env
    definition = revision_definition(env.role["role_id"])
    loop = definition["loops"][0]
    if change == "expression":
        loop["exit_condition"]["field"] = "approved == true"
    elif change == "optional":
        definition["tasks"][1]["output_contract"]["required"] = ["text"]
    elif change == "type":
        loop["exit_condition"]["value"] = "true"
    elif change == "bound":
        loop["max_iterations"] = 0
    elif change == "overlap":
        definition["loops"].append({**loop, "id": "other"})
    elif change == "missing":
        loop["body"].append("absent")
    elif change == "feedback":
        loop["feedback"][0]["input"] = "undeclared"
    else:
        loop["exit_task_id"] = "write"
    before = await env.service.list_assets("alice")
    with pytest.raises(RoleAssetError):
        await env.service.save_asset("alice", {"definition": definition})
    assert await env.service.list_assets("alice") == before


@pytest.mark.asyncio
async def test_loop_bound_cannot_exceed_effective_member_iteration_limit(workflow_env):
    env = workflow_env
    definition = revision_definition(env.role["role_id"])
    for task in definition["tasks"]:
        task["overrides"]["max_iterations"] = 1
    asset = await env.service.save_asset("alice", {"definition": definition})
    with pytest.raises(RoleAssetError, match="iteration limit"):
        await env.service.start_run(
            "alice",
            asset["asset_id"],
            {"version": 1, "inputs": {"draft": "Original", "feedback": "Check"}},
        )


@pytest.mark.asyncio
async def test_skipped_optional_body_path_does_not_block_validated_passing_exit(
    loop_script,
):
    env = loop_script.env
    loop_script.approved_round = 1
    definition = revision_definition(env.role["role_id"])
    definition["tasks"].insert(
        1, {**definition["tasks"][0], "id": "optional", "name": "Optional evidence"}
    )
    definition["loops"][0]["body"].append("optional")
    definition["edges"] += [
        {
            "source": "write",
            "target": "optional",
            "condition": {"field": "text", "value": "not selected"},
        },
        {"source": "optional", "target": "review"},
    ]
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {"version": 1, "inputs": {"draft": "Original", "feedback": "Check"}},
    )
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["status"] == "completed"
    assert finished["loops"][0]["status"] == "passed"
    assert finished["tasks"][1]["status"] == "skipped"


@pytest.mark.asyncio
async def test_skipped_feedback_source_cannot_release_an_unvalidated_next_round(loop_script):
    env = loop_script.env
    definition = revision_definition(env.role["role_id"])
    definition["tasks"].insert(1, {**definition["tasks"][0], "id": "optional", "name": "Optional evidence"})
    definition["loops"][0]["body"].append("optional")
    definition["loops"][0]["feedback"][0]["source"] = "optional"
    definition["edges"] += [{"source": "write", "target": "optional", "condition": {"field": "text", "value": "not selected"}}, {"source": "optional", "target": "review"}]
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run("alice", asset["asset_id"], {"version": 1, "inputs": {"draft": "Original", "feedback": "Check"}})
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["status"] == "blocked"
    assert finished["loops"][0]["status"] == "blocked"
    assert "optional" in finished["loops"][0]["reason"]
    assert len(finished["loops"][0]["iterations"]) == 1
    await env.service.advance_run("alice", run["run_id"])
    reread = await env.service.get_run("alice", run["run_id"])
    assert len([event for event in reread["events"] if event["kind"] == "loop.evaluated"]) == 1
    assert len(loop_script.observations) == 2


@pytest.mark.asyncio
async def test_wait_inside_loop_releases_single_slot_to_independent_branch(loop_script):
    env = loop_script.env
    definition = revision_definition(env.role["role_id"])
    question = {
        "id": "question",
        "name": "Round question",
        "human": {
            "kind": "question",
            "question": "Which evidence?",
            "choices": ["Approved evidence"],
        },
    }
    definition["tasks"].insert(0, question)
    definition["loops"][0]["body"].insert(0, "question")
    definition["edges"].insert(0, {"source": "question", "target": "write"})
    definition["tasks"].append(
        {
            **definition["tasks"][-1],
            "id": "independent",
            "name": "Independent branch",
            "inputs": {"draft": {"input": "draft"}},
        }
    )
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {"version": 1, "inputs": {"draft": "Original", "feedback": "Check"}},
    )
    for _ in range(200):
        current = await env.service.get_run("alice", run["run_id"])
        if current["tasks"][-1]["status"] == "completed":
            break
        await asyncio.sleep(0.01)
    assert current["tasks"][0]["status"] == "waiting_human"
    assert current["tasks"][-1]["status"] == "completed"
    for iteration in [1, 2]:
        for _ in range(200):
            current = await env.service.get_run("alice", run["run_id"])
            pending = [todo for todo in current["todos"] if todo["status"] == "pending"]
            if pending:
                break
            await asyncio.sleep(0.01)
        assert current["loops"][0]["iterations"][-1]["iteration"] == iteration
        await env.service.answer_todo(
            "alice",
            run["run_id"],
            pending[0]["todo_id"],
            {
                "request_id": f"answer-round-{iteration}",
                "action": "answer",
                "answer": "Approved evidence",
            },
        )
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["status"] == "completed"
    assert len(finished["todos"]) == 2
    assert (
        len(
            [
                item
                for item in loop_script.observations
                if item["snapshot"]["node_id"] == "independent"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_approval_after_loop_pins_the_passed_revision(loop_script):
    env = loop_script.env
    definition = revision_definition(env.role["role_id"])
    definition["tasks"][-1] = {
        "id": "approve",
        "name": "Approve final report",
        "human": {
            "kind": "approval",
            "question": "Confirm the reviewed version",
            "artifact_task_id": "write",
        },
    }
    definition["edges"][-1]["target"] = "approve"
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {"version": 1, "inputs": {"draft": "Original", "feedback": "Check"}},
    )
    for _ in range(200):
        current = await env.service.get_run("alice", run["run_id"])
        if current["todos"]:
            break
        await asyncio.sleep(0.01)
    rounds = current["loops"][0]["iterations"]
    approved = rounds[1]["tasks"][0]["result_ref"]
    assert current["todos"][0]["artifact_ref"] == approved
    await env.service.answer_todo(
        "alice",
        run["run_id"],
        current["todos"][0]["todo_id"],
        {"request_id": "confirm-reviewed", "action": "approve"},
    )
    assert (await wait_graph(env.service, run["run_id"]))["status"] == "completed"
    exported = await env.service.export_artifact("alice", **approved)
    assert exported["content"] == {"text": "Report revision 2"}
    with pytest.raises(RoleAssetError):
        await env.service.export_artifact(
            "alice", **rounds[0]["tasks"][0]["result_ref"]
        )


@pytest.mark.asyncio
async def test_retry_in_second_round_preserves_task_and_frozen_feedback(loop_script):
    env = loop_script.env
    loop_script.failures.add(("write", 2))
    asset = await env.service.save_asset(
        "alice", {"definition": revision_definition(env.role["role_id"])}
    )
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {"version": 1, "inputs": {"draft": "Original", "feedback": "Check"}},
    )
    failed = await wait_graph(env.service, run["run_id"])
    task = failed["tasks"][0]
    assert task["status"] == "failed"
    loop_script.failures.clear()
    retried = await env.service.retry_task(
        "alice",
        task["run_id"],
        {
            "request_id": "retry-current-round",
            "attempt_id": task["state"]["attempt_id"],
            "input_policy": "original",
        },
    )
    assert retried["run_id"] == task["run_id"]
    assert retried["state"]["attempt_id"] != task["state"]["attempt_id"]
    assert retried["snapshot"] == task["snapshot"]
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["status"] == "completed"
    assert len(finished["loops"][0]["iterations"]) == 2
    assert len(finished["loops"][0]["iterations"][1]["tasks"][0]["attempts"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["continue", "cancel"])
async def test_pause_or_cancel_preserves_round_boundary_and_history(
    loop_script, action
):
    env = loop_script.env
    loop_script.holds["review", 1] = asyncio.Event()
    asset = await env.service.save_asset(
        "alice", {"definition": revision_definition(env.role["role_id"])}
    )
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {"version": 1, "inputs": {"draft": "Original", "feedback": "Check"}},
    )
    for _ in range(200):
        if len(loop_script.observations) == 2:
            break
        await asyncio.sleep(0.01)
    await env.service.control_run(
        "alice", run["run_id"], {"request_id": "pause", "action": "pause"}
    )
    loop_script.holds["review", 1].set()
    await asyncio.sleep(0.1)
    paused = await env.service.get_run("alice", run["run_id"])
    assert len(paused["loops"][0]["iterations"]) == 1
    assert len(loop_script.observations) == 2
    await env.service.control_run(
        "alice", run["run_id"], {"request_id": action, "action": action}
    )
    finished = await wait_graph(env.service, run["run_id"])
    if action == "continue":
        assert finished["status"] == "completed"
        assert len(finished["loops"][0]["iterations"]) == 2
    else:
        assert finished["control"]["status"] == "cancelled"
        assert finished["loops"][0]["status"] == "cancelled"
        assert len(finished["loops"][0]["iterations"]) == 1
        assert len(loop_script.observations) == 2
