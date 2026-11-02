"""Frozen human deadlines use saved evidence under the existing decision lock."""

import asyncio
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
import pytest_asyncio

from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.workflows import WorkflowService
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest_asyncio.fixture
async def deadline_env(workflow_env, monkeypatch):
    """Supply a real service, fixed wall clock and counted upstream evidence.

    Args:
        workflow_env: Real SQLite asset and execution fixture.
        monkeypatch: Scoped clock replacement.

    Returns:
        A builder and clock shared by public decision tests.
    """
    clock = [1000.0]
    monkeypatch.setattr(
        "astrbot.core.agent_teams.workflows.time",
        SimpleNamespace(time=lambda: clock[0], monotonic=time.monotonic),
    )
    calls = []
    service, role = workflow_env.service, workflow_env.role
    ports = service.ports_factory("alice")

    async def create(
        kind="question",
        action="use_recommendation",
        suggested=None,
        seconds=10,
        artifact_version=1,
    ):
        """Save and start a graph with an explicit evidence-producing Agent.

        Args:
            kind: Question or fixed artifact approval.
            action: Configured deadline action.
            suggested: Explicit upstream recommendation, including invalid cases.
            seconds: Configured delay or unlimited waiting.
            artifact_version: Exact artifact version requested for approval.

        Returns:
            The persisted root run with its pending human request.
        """
        choice = (
            suggested
            if suggested is not None
            else "approve"
            if kind == "approval"
            else "Asia"
        )

        async def collect(session_id, message_id, member_id):
            calls.append(member_id)
            tool = AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )[0]
            await tool.call(
                None,
                result_json=json.dumps(
                    {"choice": choice, "reason": "Verified evidence"}
                ),
            )
            return "Done", []

        service.ports_factory = lambda _: replace(ports, collect=collect)
        human = {
            "kind": kind,
            "question": "Choose from this evidence",
            "human_wait": {"timeout_seconds": seconds, "timeout_action": action},
            "recommendation_task_id": "source",
            "recommendation_value_field": "choice",
            "recommendation_reason_field": "reason",
            **(
                {"artifact_task_id": "source", "artifact_version": artifact_version}
                if kind == "approval"
                else {"choices": ["Asia", "Europe"]}
            ),
        }
        asset = await service.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Deadline review",
                    "result_task_id": "human",
                    "tasks": [
                        {
                            "id": "source",
                            "name": "Evidence",
                            "member": "source",
                            "role_id": role["role_id"],
                            "role_version": 1,
                            "task": "Evaluate once",
                            "output_contract": {
                                "type": "object",
                                "properties": {
                                    "choice": {"type": "string"},
                                    "reason": {"type": "string"},
                                },
                                "required": ["choice", "reason"],
                            },
                        },
                        {"id": "human", "name": "Decision", "human": human},
                    ],
                    "edges": [{"source": "source", "target": "human"}],
                }
            },
        )
        run = await service.start_run("alice", asset["asset_id"], {"version": 1})
        for _ in range(200):
            run = await service.get_run("alice", run["run_id"])
            if run["todos"]:
                return run
            await asyncio.sleep(0.01)
        raise AssertionError(run)

    return SimpleNamespace(
        service=service,
        create=create,
        clock=clock,
        calls=calls,
        roles=workflow_env.roles,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["question", "approval"])
@pytest.mark.parametrize(
    "action,seconds,expected",
    [
        ("use_recommendation", 10, "completed"),
        ("wait", 10, "running"),
        ("wait", None, "running"),
    ],
)
async def test_deadline_uses_only_explicit_frozen_recommendation(
    deadline_env, kind, action, seconds, expected
):
    """Apply only the frozen policy and evidence without another model call.

    Args:
        deadline_env: Real service with a counted upstream Agent.
        kind: Static question or artifact approval.
        action: Persisted timeout policy.
        seconds: Deadline duration, or unlimited waiting.
        expected: Expected root status after expiration.
    """
    env = deadline_env
    run = await env.create(kind, action, seconds=seconds)
    todo = run["todos"][0]
    assert todo["deadline"] == (1010 if seconds else None)
    env.clock[0] = 1009
    assert await env.service.sweep_human_deadlines() == 0
    env.clock[0] = 1011
    await env.service.sweep_human_deadlines()
    after = await env.service.get_run("alice", run["run_id"])
    assert after["status"] == expected
    assert len(env.calls) == 1
    todo = after["todos"][0]
    if expected == "completed":
        assert todo["decision_source"]["type"] == "recommendation"
        assert todo["response"]["action"] == (
            "approve" if kind == "approval" else "answer"
        )
        assert todo["consumed_at"] == 1011
    else:
        assert todo["status"] == "pending" and todo["response"] is None
        assert todo["timeout_reason"] == ("wait_policy" if seconds else None)
    assert await env.service.sweep_human_deadlines() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,suggested,version,reason",
    [
        ("question", "invalid", 1, "missing_recommendation"),
        ("approval", "invalid", 1, "missing_recommendation"),
        ("approval", "approve", 2, "evidence_unavailable"),
    ],
)
async def test_invalid_or_missing_evidence_keeps_request_pending(
    deadline_env, kind, suggested, version, reason
):
    """Leave unverifiable recommendations available for a later human answer.

    Args:
        deadline_env: Real service and controllable clock.
        kind: Human request kind.
        suggested: Upstream recommendation value.
        version: Requested immutable artifact version.
        reason: Persisted explanation for continued waiting.
    """
    env = deadline_env
    run = await env.create(kind, suggested=suggested, artifact_version=version)
    env.clock[0] = 1011
    await env.service.sweep_human_deadlines()
    after = await env.service.get_run("alice", run["run_id"])
    todo = after["todos"][0]
    assert todo["status"] == "pending" and todo["response"] is None
    assert todo["timeout_reason"] == reason
    assert len(env.calls) == 1


@pytest.mark.asyncio
async def test_restart_keeps_original_deadline_and_pause_defers_consumption(
    deadline_env,
):
    """Reopen the database without resetting the deadline or bypassing pause.

    Args:
        deadline_env: Real service and controllable clock.
    """
    env = deadline_env
    run = await env.create()
    await env.service.control_run(
        "alice", run["run_id"], {"request_id": "pause", "action": "pause"}
    )
    await env.service.shutdown()
    rebuilt = WorkflowService(
        env.service.db, env.service.roles, env.service.ports_factory
    )
    env.clock[0] = 1011
    await rebuilt.boot_sweep()
    after = await rebuilt.get_run("alice", run["run_id"])
    todo = after["todos"][0]
    assert todo["deadline"] == 1010 and todo["status"] == "answered"
    assert (
        todo["consumed_at"] is None and after["tasks"][1]["status"] == "waiting_human"
    )
    await rebuilt.control_run(
        "alice", run["run_id"], {"request_id": "continue", "action": "continue"}
    )
    assert (await rebuilt.get_run("alice", run["run_id"]))["status"] == "completed"
    assert len(env.calls) == 1
    await rebuilt.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("competing", ["answer", "cancel"])
async def test_deadline_competes_with_one_human_or_cancel_decision(
    deadline_env, competing
):
    """Serialize expiration and human control against the same durable request.

    Args:
        deadline_env: Real service and controllable clock.
        competing: Human answer or cancellation racing with expiration.
    """
    env = deadline_env
    run = await env.create()
    todo = run["todos"][0]
    await env.service.control_run(
        "alice", run["run_id"], {"request_id": "pause", "action": "pause"}
    )
    env.clock[0] = 1011
    command = (
        env.service.answer_todo(
            "alice",
            run["run_id"],
            todo["todo_id"],
            {"request_id": "human", "action": "answer", "answer": "Europe"},
        )
        if competing == "answer"
        else env.service.control_run(
            "alice", run["run_id"], {"request_id": "cancel", "action": "cancel"}
        )
    )
    outcomes = await asyncio.gather(
        env.service.sweep_human_deadlines(), command, return_exceptions=True
    )
    assert all(
        not isinstance(item, Exception)
        or isinstance(item, RoleAssetError)
        and item.status == 409
        for item in outcomes
    ), outcomes
    after = await env.service.get_run("alice", run["run_id"])
    todo = after["todos"][0]
    assert todo["consumed_at"] is None
    if competing == "cancel":
        assert after["status"] == "cancelled" and todo["status"] == "cancelled"
    else:
        assert todo["response"]["answer"] in {"Asia", "Europe"}
        assert todo["decision_source"]["type"] == (
            "human" if todo["response"]["answer"] == "Europe" else "recommendation"
        )
    assert (
        len([event for event in after["events"] if event["kind"] == "human.answered"])
        <= 1
    )


@pytest.mark.asyncio
async def test_automatic_rejection_preserves_exact_artifact_and_feedback(deadline_env):
    """Use the existing rejection path with its fixed evidence and rationale.

    Args:
        deadline_env: Real service and controllable clock.
    """
    env = deadline_env
    run = await env.create("approval", suggested="reject")
    original = run["todos"][0]
    env.clock[0] = 1011
    await env.service.sweep_human_deadlines()
    after = await env.service.get_run("alice", run["run_id"])
    todo = after["todos"][0]
    assert after["status"] == "completed"
    assert todo["response"]["action"] == "reject"
    assert todo["response"]["feedback"] == "Verified evidence"
    assert todo["response"]["artifact_ref"] == original["artifact_ref"]
    assert todo["decision_source"]["type"] == "recommendation"
    assert todo["consumed_at"] == 1011
    assert len(env.calls) == 1


@pytest.mark.asyncio
async def test_core_watcher_handles_deadlines_without_viewers_and_stops(deadline_env):
    """Drive expiration from the core lifetime and release its task on shutdown.

    Args:
        deadline_env: Real service and controllable clock.
    """
    env = deadline_env
    run = await env.create()
    env.clock[0] = 1011
    watcher = asyncio.create_task(env.service.run_human_deadlines())
    try:
        for _ in range(200):
            after = await env.service.get_run("alice", run["run_id"])
            if after["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        assert after["status"] == "completed"
        assert after["todos"][0]["decision_source"]["type"] == "recommendation"
        assert len(env.calls) == 1
        assert not watcher.done()
    finally:
        await env.service.shutdown()
    assert watcher.done()
    assert not env.service._tasks
    assert await env.service.sweep_human_deadlines() == 0


@pytest.mark.asyncio
async def test_role_waiting_policy_inherits_and_can_reset_without_chat_defaults(
    workflow_env,
):
    """Keep explicit Agent overrides independent of ordinary chat defaults.

    Args:
        workflow_env: Real versioned role and resource services.
    """
    roles = workflow_env.roles
    definition = {
        **workflow_env.role["definition"],
        "human_wait": {"timeout_seconds": 60, "timeout_action": "use_recommendation"},
    }
    role = await roles.save_role("alice", {"definition": definition})
    inherited = await roles.resolve_role("alice", role["role_id"], 1)
    assert inherited["effective"]["human_wait"] == definition["human_wait"]
    reset = await roles.resolve_role("alice", role["role_id"], 1, {"human_wait": None})
    assert reset["effective"]["human_wait"] == {
        "timeout_seconds": None,
        "timeout_action": "wait",
    }
    assert reset["sources"]["human_wait"] == "task"
    for invalid in [
        {"timeout_action": "use_recommendation"},
        {"timeout_seconds": 0},
        {"timeout_seconds": 10, "timeout_action": "guess"},
    ]:
        with pytest.raises(RoleAssetError):
            await roles.save_role(
                "alice", {"definition": {**definition, "human_wait": invalid}}
            )
