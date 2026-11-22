"""Verify workflow outcomes when the execution port cannot clean up."""

import asyncio

import pytest

from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from tests.agent_teams.test_workflow_assets import (
    completed_run,
)
from tests.agent_teams.test_workflow_assets import (
    workflow_env as workflow_env,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "root_status", "successor_status"),
    [
        ("completed", "completed", "completed"),
        ("failed", "blocked", "dependency_failed"),
        ("cancelled", "cancelled", "cancelled"),
    ],
)
async def test_cleanup_failure_preserves_workflow_outcome(
    workflow_env, outcome, root_status, successor_status
):
    """Keep dependency scheduling and recorded results independent of cleanup.

    Args:
        workflow_env: Workflow service with real SQLite and controlled execution ports.
        outcome: Actual predecessor outcome.
        root_status: Expected workflow status after the predecessor ends.
        successor_status: Expected status under the existing dependency rules.
    """
    service = workflow_env.service
    started = []
    task_by_session = {}
    collecting = asyncio.Event()
    release = asyncio.Event()
    fail_execution = outcome == "failed"

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        started.append(text)
        task_by_session[session_id] = text
        return message_id

    async def collect(session_id, message_id, member_id):
        collecting.set()
        await release.wait()
        if fail_execution:
            raise RuntimeError("Model execution failed")
        submit = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[
            0
        ]
        assert (
            await submit.call(None, text=task_by_session[session_id])
            == "Result accepted."
        )
        return "Done", []

    async def close():
        raise RuntimeError("Execution subscription cleanup failed")

    service.ports_factory = lambda _: TeamPorts(
        deliver=deliver,
        collect=collect,
        is_busy=lambda _: False,
        emit=lambda _: None,
        close=close,
    )
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Two-step workflow",
                "result_task_id": "successor",
                "max_parallel": 1,
                "tasks": [
                    {
                        "id": node,
                        "name": node,
                        "member": "writer",
                        "role_id": workflow_env.role["role_id"],
                        "role_version": 1,
                        "task": node,
                    }
                    for node in ("predecessor", "successor")
                ],
                "edges": [{"source": "predecessor", "target": "successor"}],
            }
        },
    )
    run = await service.start_run("alice", asset["asset_id"], {"version": 1})
    run_id = run["run_id"]
    await asyncio.wait_for(collecting.wait(), 3)
    before_cleanup = await service.get_run("alice", run_id)
    cancel = {"request_id": "cancel-during-collection", "action": "cancel"}
    if outcome == "cancelled":
        await service.control_run("alice", run_id, cancel)
    else:
        release.set()

    finished = await completed_run(service, run_id)
    # Cancellation is committed before the interrupted executor can finish cleanup.
    for _ in range(200):
        if any(
            event["kind"] == "task.cleanup_failed"
            for event in finished["tasks"][0]["events"]
        ):
            break
        await asyncio.sleep(0.01)
        finished = await service.get_run("alice", run_id)

    predecessor, successor = finished["tasks"]
    assert finished["status"] == root_status
    assert [predecessor["status"], successor["status"]] == [outcome, successor_status]
    executed = [predecessor, successor] if outcome == "completed" else [predecessor]
    assert started == [task["node_id"] for task in executed]

    changes = await service.get_events("alice", run_id, before_cleanup["cursor"])
    assert [
        (event["run_id"], event["data"])
        for event in changes["events"]
        if event["kind"] == "task.cleanup_failed"
    ] == [
        (
            task["run_id"],
            {
                "attempt_id": task["state"]["attempt_id"],
                "reason": "Execution subscription cleanup failed",
            },
        )
        for task in executed
    ]

    predecessor_id = predecessor["run_id"]
    attempt_id = predecessor["state"]["attempt_id"]
    if outcome == "completed":
        assert await service.get_result("alice", run_id) == {"text": "successor"}
        await service.submit_result(
            "alice", predecessor_id, attempt_id, {"text": "predecessor"}
        )
        await service.finish_submission("alice", predecessor_id)
    else:
        with pytest.raises(RoleAssetError, match="Attempt is no longer active"):
            await service.submit_result(
                "alice", predecessor_id, attempt_id, {"text": "late"}
            )
        if outcome == "failed":
            assert predecessor["state"]["reason"] == "Model execution failed"
        else:
            await service.control_run("alice", run_id, cancel)

    await asyncio.gather(
        service.advance_run("alice", run_id), service.advance_run("alice", run_id)
    )
    replayed = await service.get_run("alice", run_id)
    assert replayed["status"] == root_status
    assert replayed["result_ref"] == finished["result_ref"]
    assert replayed["tasks"] == finished["tasks"]
    assert started == [task["node_id"] for task in executed]

    fail_execution = False
    release.set()
    followup = await service.start_run("alice", asset["asset_id"], {"version": 1})
    assert (await completed_run(service, followup["run_id"]))["status"] == "completed"
    assert started[-2:] == ["predecessor", "successor"]
