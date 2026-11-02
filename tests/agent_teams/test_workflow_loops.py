"""Bounded revision histories through the shared public asset/run boundary."""

import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase
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
        "result_task_id": "publish",
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
        env=env,
        observations=[],
        approved_round=2,
        holds={},
        failures=set(),
        started={},
        recommendation=False,
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
        script.started.setdefault((node, iteration), asyncio.Event()).set()
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
        if node == "review" and script.recommendation:
            result = {
                "action": "reject" if iteration == 1 else "approve",
                "reason": f"Review {iteration}",
            }
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
    for initial, following in zip(first, second):
        assert (
            initial["snapshot"]["member"]["member_id"]
            == following["snapshot"]["member"]["member_id"]
        )
        assert (
            initial["snapshot"]["member"]["conversation_id"]
            != following["snapshot"]["member"]["conversation_id"]
        )
        assert (
            initial["snapshot"]["member"]["session_id"]
            != following["snapshot"]["member"]["session_id"]
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
@pytest.mark.parametrize("loop_enabled", [False, True])
async def test_unselected_whole_loop_preserves_merge_and_reopened_history(
    loop_script, tmp_path, loop_enabled
):
    """Keep an unselected path neutral across reads and service reconstruction.

    Args:
        loop_script: External execution observations with real public services.
        tmp_path: Persistent test data reopened by a fresh database and service.
        loop_enabled: Whether the otherwise identical conditional DAG has a loop.
    """
    env = loop_script.env
    definition = revision_definition(env.role["role_id"])
    gate = {
        **definition["tasks"][0],
        "id": "gate",
        "name": "Gate",
        "task": "Choose {draft}",
        "inputs": {"draft": {"input": "draft"}},
    }
    definition["tasks"] = [
        gate,
        {**gate, "id": "selected", "name": "Selected path"},
        *definition["tasks"],
    ]
    definition["tasks"][-1]["inputs"] = {"draft": {"task_id": "selected"}}
    definition["edges"] += [
        {
            "source": "gate",
            "target": "write",
            "condition": {"field": "text", "value": "unselected"},
        },
        {"source": "gate", "target": "selected"},
        {"source": "selected", "target": "publish"},
    ]
    if not loop_enabled:
        definition["loops"] = []
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {"version": 1, "inputs": {"draft": "Original", "feedback": "Check"}},
    )
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["status"] == "completed"
    assert [(task["node_id"], task["status"]) for task in finished["tasks"]] == [
        ("gate", "completed"),
        ("selected", "completed"),
        ("write", "skipped"),
        ("review", "skipped"),
        ("publish", "completed"),
    ]
    assert await env.service.get_result("alice", run["run_id"]) == {"text": "Original"}
    assert finished["tasks"][-1]["snapshot"]["input_artifacts"] == [
        finished["tasks"][1]["result_ref"]
    ]
    if loop_enabled:
        loop = finished["loops"][0]
        assert loop["status"] == "skipped"
        assert len(loop["iterations"]) == 1
        iteration = loop["iterations"][0]
        assert iteration["status"] == "skipped"
        assert "exit_reference" not in iteration
        assert "exit_value" not in iteration
        assert all(task["result"] is None for task in iteration["tasks"])
        assert all(task["artifacts"] == [] for task in iteration["tasks"])
    await env.service.advance_run("alice", run["run_id"])
    refreshed = await env.service.get_run("alice", run["run_id"])
    assert refreshed["loops"] == finished["loops"]
    await env.service.shutdown()
    await env.db.engine.dispose()
    reopened_db = SQLiteDatabase(str(tmp_path / "workflows.db"))
    reopened = WorkflowService(
        reopened_db,
        RoleAssetService(reopened_db),
        ports_factory=env.service.ports_factory,
    )
    try:
        await reopened.boot_sweep()
        await reopened.advance_run("alice", run["run_id"])
        restored = await reopened.get_run("alice", run["run_id"])
        assert restored["status"] == "completed"
        assert restored["loops"] == finished["loops"]
        assert restored["result"] == finished["result"]
        assert [task["run_id"] for task in restored["tasks"]] == [
            task["run_id"] for task in finished["tasks"]
        ]
        if loop_enabled:
            assert (
                len(
                    [
                        event
                        for event in restored["events"]
                        if event["kind"] == "loop.skipped"
                    ]
                )
                == 1
            )
            assert not any(
                event["kind"] == "loop.evaluated" for event in restored["events"]
            )
        assert [item["snapshot"]["node_id"] for item in loop_script.observations] == [
            "gate",
            "selected",
            "publish",
        ]
    finally:
        await reopened.shutdown()
        await reopened_db.engine.dispose()


@pytest.mark.asyncio
async def test_executed_loop_with_skipped_exit_cannot_release_final_consumer(
    loop_script,
):
    env = loop_script.env
    definition = revision_definition(env.role["role_id"])
    definition["edges"][0]["condition"] = {"field": "text", "value": "unselected"}
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {"version": 1, "inputs": {"draft": "Original", "feedback": "Check"}},
    )
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["status"] == "blocked"
    assert finished["result"] is None
    assert [task["status"] for task in finished["tasks"]] == [
        "completed",
        "skipped",
        "dependency_failed",
    ]
    loop = finished["loops"][0]
    assert loop["status"] == "blocked"
    assert loop["iterations"][0]["status"] == "blocked"
    assert len(loop["iterations"]) == 1
    assert "exit_reference" not in loop["iterations"][0]
    assert len(loop_script.observations) == 1


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
    assert "maximum iterations" in finished["loops"][0]["reason"]
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
async def test_skipped_feedback_source_cannot_release_an_unvalidated_next_round(
    loop_script,
):
    env = loop_script.env
    definition = revision_definition(env.role["role_id"])
    definition["tasks"].insert(
        1, {**definition["tasks"][0], "id": "optional", "name": "Optional evidence"}
    )
    definition["loops"][0]["body"].append("optional")
    definition["loops"][0]["feedback"][0]["source"] = "optional"
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
    assert finished["status"] == "blocked"
    assert finished["loops"][0]["status"] == "blocked"
    assert "optional" in finished["loops"][0]["reason"]
    assert len(finished["loops"][0]["iterations"]) == 1
    await env.service.advance_run("alice", run["run_id"])
    reread = await env.service.get_run("alice", run["run_id"])
    assert (
        len([event for event in reread["events"] if event["kind"] == "loop.evaluated"])
        == 1
    )
    assert len(loop_script.observations) == 2


@pytest.mark.asyncio
async def test_wait_inside_loop_releases_single_slot_to_independent_branch(loop_script):
    """Keep a round's human wait intact across whole-run pause and continue.

    Args:
        loop_script: Real public services with independently controlled execution.
    """
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
        assert len(pending) == 1
        paused = await env.service.control_run(
            "alice",
            run["run_id"],
            {"request_id": f"pause-wait-{iteration}", "action": "pause"},
        )
        await env.service.advance_run("alice", run["run_id"])
        continued = await env.service.control_run(
            "alice",
            run["run_id"],
            {"request_id": f"continue-wait-{iteration}", "action": "continue"},
        )
        assert paused["control"]["status"] == "paused"
        assert continued["control"]["status"] == "active"
        assert continued["snapshot"] == run["snapshot"]
        assert continued["todos"] == paused["todos"] == current["todos"]
        assert continued["tasks"][0]["status"] == "waiting_human"
        assert len(continued["loops"][0]["iterations"]) == iteration
        assert continued["tasks"][0]["snapshot"] == current["tasks"][0]["snapshot"]
        assert continued["tasks"][0]["state"] == current["tasks"][0]["state"]
        assert [task["run_id"] for task in continued["tasks"]] == [
            task["run_id"] for task in current["tasks"]
        ]
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
    definition["result_task_id"] = "approve"
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
        {
            "request_id": "confirm-reviewed",
            "action": "approve",
            "artifact_ref": approved,
        },
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
    """Control an in-flight review at an explicit external execution barrier.

    Args:
        loop_script: Real scheduling and storage with controlled external responses.
        action: Whole-run decision after the paused review finishes safely.
    """
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
    # Elapsed time cannot establish that the reviewer has actually started.
    await asyncio.wait_for(
        loop_script.started.setdefault(("review", 1), asyncio.Event()).wait(), 5
    )
    before = await env.service.get_run("alice", run["run_id"])
    assert [task["status"] for task in before["tasks"]] == [
        "completed",
        "running",
        "pending",
    ]
    paused = await env.service.control_run(
        "alice", run["run_id"], {"request_id": "pause", "action": "pause"}
    )
    assert paused["control"]["status"] == "paused"
    assert paused["tasks"][1]["status"] == "running"
    loop_script.holds["review", 1].set()
    for _ in range(200):
        paused = await env.service.get_run("alice", run["run_id"])
        if paused["tasks"][1]["status"] == "completed":
            break
        await asyncio.sleep(0.01)
    assert paused["tasks"][1]["status"] == "completed"
    assert len(paused["loops"][0]["iterations"]) == 1
    assert len(loop_script.observations) == 2
    assert paused["tasks"][0]["result_ref"] == before["tasks"][0]["result_ref"]
    assert paused["tasks"][1]["result"] == {
        "approved": False,
        "text": "Review 1: add evidence",
    }
    parked = await env.service.advance_run("alice", run["run_id"])
    assert len(parked["loops"][0]["iterations"]) == 1
    assert [task["status"] for task in parked["tasks"]] == [
        "completed",
        "completed",
        "pending",
    ]
    assert len(loop_script.observations) == 2
    await env.service.control_run(
        "alice", run["run_id"], {"request_id": action, "action": action}
    )
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["snapshot"] == before["snapshot"]
    for original, retained in zip(
        paused["loops"][0]["iterations"][0]["tasks"],
        finished["loops"][0]["iterations"][0]["tasks"],
        strict=True,
    ):
        for field in ["run_id", "snapshot", "state", "result", "result_ref", "events"]:
            assert retained[field] == original[field]
    if action == "continue":
        assert finished["status"] == "completed"
        assert len(finished["loops"][0]["iterations"]) == 2
        assert len(loop_script.observations) == 5
    else:
        assert finished["control"]["status"] == "cancelled"
        assert finished["loops"][0]["status"] == "cancelled"
        assert len(finished["loops"][0]["iterations"]) == 1
        assert len(loop_script.observations) == 2


@pytest.mark.asyncio
async def test_control_nodes_in_loop_recreate_without_conversations_and_apply_feedback(
    loop_script,
):
    env = loop_script.env
    definition = revision_definition(env.role["role_id"])
    definition["tasks"][1:1] = [
        {
            "id": "condition",
            "name": "Condition",
            "control": {"kind": "condition", "operator": "ne", "value": ""},
            "inputs": {"value": {"task_id": "write", "field": "text"}},
        },
        {"id": "join", "name": "Join", "control": {"kind": "join"}},
    ]
    definition["edges"] = [
        {"source": "write", "target": "condition"},
        {
            "source": "condition",
            "target": "join",
            "condition": {"field": "matched", "value": True},
        },
        {"source": "join", "target": "review"},
        {"source": "review", "target": "publish"},
    ]
    loop = definition["loops"][0]
    loop["body"] = ["write", "condition", "join", "review"]
    loop["feedback"].append(
        {"target": "condition", "input": "value", "source": "review", "field": "text"}
    )
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {"version": 1, "inputs": {"draft": "Original", "feedback": "Initial"}},
    )
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["status"] == "completed", finished
    rounds = finished["loops"][0]["iterations"]
    assert len(rounds) == 2
    for iteration in rounds:
        tasks = {task["node_id"]: task for task in iteration["tasks"]}
        for node in ("condition", "join"):
            assert tasks[node]["status"] == "completed"
            assert "member" not in tasks[node]["snapshot"]
            assert tasks[node]["result_ref"] is None
        assert tasks["condition"]["result"] == {"matched": True}


@pytest.mark.asyncio
async def test_human_rejection_drives_bounded_rework_and_exact_approval(loop_script):
    """A rejected version follows feedback into a distinct round and request."""
    env = loop_script.env
    definition = revision_definition(env.role["role_id"])
    definition["tasks"][1] = {
        "id": "review",
        "name": "Human approval",
        "human": {
            "kind": "approval",
            "question": "Approve?",
            "artifact_task_id": "write",
        },
    }
    definition["edges"][-1]["condition"] = {"field": "approved", "value": True}
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {"version": 1, "inputs": {"draft": "Original", "feedback": "Check"}},
    )
    refs = []
    requests = []
    for iteration in (1, 2):
        for _ in range(300):
            current = await env.service.get_run("alice", run["run_id"])
            pending = [todo for todo in current["todos"] if todo["status"] == "pending"]
            if pending:
                break
            await asyncio.sleep(0.01)
        todo = pending[0]
        ref = todo["artifact_ref"]
        refs.append(ref)
        requests.append(todo["todo_id"])
        assert (await env.service.read_artifact("alice", **ref))["content"][
            "text"
        ] == f"Report revision {iteration}"
        with pytest.raises(RoleAssetError):
            await env.service.export_artifact("alice", **ref)
        with pytest.raises(RoleAssetError) as mismatch:
            await env.service.answer_todo(
                "alice",
                run["run_id"],
                todo["todo_id"],
                {
                    "request_id": f"wrong-{iteration}",
                    "action": "approve",
                    "artifact_ref": {**ref, "version": ref["version"] + 1},
                },
            )
        assert mismatch.value.status == 409
        response = {
            "request_id": f"decision-{iteration}",
            "action": "reject" if iteration == 1 else "approve",
            "artifact_ref": ref,
        }
        if iteration == 1:
            response["feedback"] = "Add evidence"
        with pytest.raises(RoleAssetError):
            await env.service.answer_todo(
                "bob", run["run_id"], todo["todo_id"], response
            )
        await env.service.control_run(
            "alice",
            run["run_id"],
            {"request_id": f"pause-{iteration}", "action": "pause"},
        )
        saved = await env.service.answer_todo(
            "alice", run["run_id"], todo["todo_id"], response
        )
        assert (
            next(item for item in saved["todos"] if item["todo_id"] == todo["todo_id"])[
                "status"
            ]
            == "answered"
        )
        with pytest.raises(RoleAssetError):
            await env.service.answer_todo(
                "alice",
                run["run_id"],
                todo["todo_id"],
                {
                    **response,
                    "artifact_ref": {**ref, "version": ref["version"] + 1},
                },
            )
        await env.service.answer_todo("alice", run["run_id"], todo["todo_id"], response)
        await env.service.control_run(
            "alice",
            run["run_id"],
            {"request_id": f"continue-{iteration}", "action": "continue"},
        )
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["status"] == "completed"
    assert requests[0] != requests[1] and refs[0] != refs[1]
    assert (await env.service.export_artifact("alice", **refs[1]))["content"][
        "text"
    ] == "Report revision 2"
    with pytest.raises(RoleAssetError):
        await env.service.export_artifact("alice", **refs[0])
    assert finished["loops"][0]["iterations"][0]["status"] == "rejected"


@pytest.mark.asyncio
async def test_rejected_approval_exhausts_loop_without_delivery(loop_script):
    """An exhausted human rework loop remains blocked and preserves its reason."""
    env = loop_script.env
    definition = revision_definition(env.role["role_id"], max_iterations=1)
    definition["tasks"][1] = {
        "id": "review",
        "name": "Human approval",
        "human": {
            "kind": "approval",
            "question": "Approve?",
            "artifact_task_id": "write",
        },
    }
    definition["edges"][-1]["condition"] = {"field": "approved", "value": True}
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {
            "version": 1,
            "inputs": {"draft": "Original", "feedback": "Check"},
        },
    )
    for _ in range(300):
        current = await env.service.get_run("alice", run["run_id"])
        if current["todos"]:
            break
        await asyncio.sleep(0.01)
    todo = current["todos"][0]
    await env.service.answer_todo(
        "alice",
        run["run_id"],
        todo["todo_id"],
        {
            "request_id": "reject",
            "action": "reject",
            "feedback": "Add evidence",
            "artifact_ref": todo["artifact_ref"],
        },
    )
    finished = await wait_graph(env.service, run["run_id"])
    assert finished["status"] == "blocked"
    assert finished["loops"][0]["status"] == "exhausted"
    assert "maximum iterations" in finished["loops"][0]["reason"]
    assert not any(
        item["snapshot"]["node_id"] == "publish" for item in loop_script.observations
    )
    with pytest.raises(RoleAssetError):
        await env.service.export_artifact("alice", **todo["artifact_ref"])


@pytest.mark.asyncio
async def test_explicit_review_recommendation_reaches_each_approval(loop_script):
    """Read recommendations from the explicit upstream agent in each round."""
    env = loop_script.env
    loop_script.recommendation = True
    definition = revision_definition(env.role["role_id"])
    definition["tasks"][1]["output_contract"] = {
        "type": "object",
        "properties": {"action": {"type": "string"}, "reason": {"type": "string"}},
        "required": ["action", "reason"],
    }
    definition["tasks"].insert(
        2,
        {
            "id": "approve",
            "name": "Approval",
            "human": {
                "kind": "approval",
                "question": "Approve?",
                "artifact_task_id": "write",
                "recommendation_task_id": "review",
                "recommendation_value_field": "action",
                "recommendation_reason_field": "reason",
            },
        },
    )
    definition["edges"][-1] = {
        "source": "approve",
        "target": "publish",
        "condition": {"field": "approved", "value": True},
    }
    definition["edges"].append({"source": "review", "target": "approve"})
    loop = definition["loops"][0]
    loop["body"].append("approve")
    loop["exit_task_id"] = "approve"
    loop["feedback"][1]["source"] = "approve"
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {
            "version": 1,
            "inputs": {"draft": "Original", "feedback": "Check"},
        },
    )
    for iteration in (1, 2):
        for _ in range(300):
            current = await env.service.get_run("alice", run["run_id"])
            pending = [item for item in current["todos"] if item["status"] == "pending"]
            if pending:
                break
            await asyncio.sleep(0.01)
        todo = pending[0]
        action = "reject" if iteration == 1 else "approve"
        assert todo["recommendation"] == {
            "value": action,
            "reason": f"Review {iteration}",
        }
        answer = {
            "request_id": f"review-{iteration}",
            "action": action,
            "artifact_ref": todo["artifact_ref"],
        }
        if action == "reject":
            answer["feedback"] = "Add evidence"
        await env.service.answer_todo("alice", run["run_id"], todo["todo_id"], answer)
    assert (await wait_graph(env.service, run["run_id"]))["status"] == "completed"


@pytest.mark.asyncio
async def test_rejected_selected_artifact_is_not_published(loop_script):
    """Selecting the writer result cannot bypass a rejected approval."""
    env = loop_script.env
    definition = revision_definition(env.role["role_id"])
    definition["result_task_id"] = "write"
    definition["tasks"] = [
        definition["tasks"][0],
        {
            "id": "approve",
            "name": "Approval",
            "human": {
                "kind": "approval",
                "question": "Approve?",
                "artifact_task_id": "write",
            },
        },
    ]
    definition["edges"] = [{"source": "write", "target": "approve"}]
    definition["loops"] = []
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {
            "version": 1,
            "inputs": {"draft": "Original", "feedback": "Check"},
        },
    )
    for _ in range(300):
        current = await env.service.get_run("alice", run["run_id"])
        if current["todos"]:
            break
        await asyncio.sleep(0.01)
    todo = current["todos"][0]
    await env.service.answer_todo(
        "alice",
        run["run_id"],
        todo["todo_id"],
        {
            "request_id": "reject",
            "action": "reject",
            "feedback": "Revise",
            "artifact_ref": todo["artifact_ref"],
        },
    )
    current = await wait_graph(env.service, run["run_id"])
    assert current["status"] == "blocked"
    assert current["result"] is None
    assert "not been approved" in current["state"]["reason"]


@pytest.mark.asyncio
async def test_cancelled_approval_cannot_release_result(loop_script):
    """Cancellation preserves the waiting request without consuming a decision."""
    env = loop_script.env
    definition = revision_definition(env.role["role_id"])
    definition["tasks"][1] = {
        "id": "review",
        "name": "Approval",
        "human": {
            "kind": "approval",
            "question": "Approve?",
            "artifact_task_id": "write",
        },
    }
    definition["edges"][-1]["condition"] = {"field": "approved", "value": True}
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {"version": 1, "inputs": {"draft": "Original", "feedback": "Check"}},
    )
    for _ in range(300):
        current = await env.service.get_run("alice", run["run_id"])
        if current["todos"]:
            break
        await asyncio.sleep(0.01)
    todo = current["todos"][0]
    await env.service.control_run(
        "alice", run["run_id"], {"request_id": "cancel", "action": "cancel"}
    )
    with pytest.raises(RoleAssetError):
        await env.service.answer_todo(
            "alice",
            run["run_id"],
            todo["todo_id"],
            {
                "request_id": "approve",
                "action": "approve",
                "artifact_ref": todo["artifact_ref"],
            },
        )
    current = await env.service.get_run("alice", run["run_id"])
    assert current["todos"][0]["status"] == "cancelled"
    assert current["result"] is None
    with pytest.raises(RoleAssetError):
        await env.service.export_artifact("alice", **todo["artifact_ref"])


@pytest.mark.asyncio
async def test_root_detail_includes_child_scope_loop_rounds(loop_script):
    env = loop_script.env
    child = await env.service.save_asset("alice", {"definition": revision_definition(env.role["role_id"])})
    parent = await env.service.save_asset("alice", {"definition": {
        "name": "Parent", "required_inputs": ["draft", "feedback"], "result_task_id": "call",
        "tasks": [{"id": "call", "name": "Call child", "workflow": {"asset_id": child["asset_id"], "version": 1},
                   "inputs": {"draft": {"input": "draft"}, "feedback": {"input": "feedback"}}}],
    }})
    started = await env.service.start_run("alice", parent["asset_id"], {"version": 1, "inputs": {"draft": "Original", "feedback": "Check"}})
    root = await wait_graph(env.service, started["run_id"])
    assert root["status"] == "completed"
    scope = root["scopes"][0]
    iterations = scope["loops"][0]["iterations"]
    assert len(iterations) == 2
    assert [iteration["iteration"] for iteration in iterations] == [1, 2]
    assert iterations[0]["tasks"][0]["run_id"] != iterations[1]["tasks"][0]["run_id"]
    assert iterations[0]["tasks"][0]["state"]["scope_id"] == scope["state"]["scope_id"]
