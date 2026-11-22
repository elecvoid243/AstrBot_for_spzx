"""Recover a committed workflow dispatch when its in-process wake is lost."""

import asyncio

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from astrbot.core.db.po import AgentWorkflowEvent
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["active", "paused", "cancelled"])
async def test_online_maintenance_recovers_only_eligible_dispatches(
    workflow_env, control
):
    """Lose the after-commit wake while retaining the real persisted task.

    Args:
        workflow_env: Isolated SQLite workflow and controlled execution port.
        control: Whole-run admission in effect when maintenance runs.
    """
    service = workflow_env.service
    committed = []
    collecting = asyncio.Event()
    release = asyncio.Event()
    ports = service.ports_factory("alice")
    collect = ports.collect

    async def hold_collection(session_id, message_id, member_id):
        """Keep the external turn active while duplicate wakes arrive.

        Args:
            session_id: Frozen member session.
            message_id: Dispatched message identity.
            member_id: Owner of the collected result.

        Returns:
            The controlled port's reply and message parts.
        """
        collecting.set()
        await release.wait()
        return await collect(session_id, message_id, member_id)

    ports.collect = hold_collection
    service.ports_factory = lambda _: ports

    def mark_claim(session, flush_context):
        """Record the dispatch event from the actual scheduling transaction.

        Args:
            session: SQLAlchemy session writing the workflow.
            flush_context: SQLAlchemy flush context.
        """
        for row in session.new:
            if isinstance(row, AgentWorkflowEvent) and row.kind == "task.running":
                session.info["lost_dispatch"] = row.run_id

    def lose_wake(session):
        """Stop the notification at the boundary after the actual SQLite commit.

        Args:
            session: SQLAlchemy session whose changes just committed.
        """
        task_id = session.info.pop("lost_dispatch", None)
        if task_id:
            committed.append(task_id)
            raise RuntimeError("Lost the post-commit dispatch")

    event.listen(Session, "after_flush", mark_claim)
    event.listen(Session, "after_commit", lose_wake)
    try:
        with pytest.raises(RuntimeError, match="Lost the post-commit dispatch"):
            await service.start_run(
                "alice", workflow_env.asset["asset_id"], {"version": 1}
            )
    finally:
        event.remove(Session, "after_commit", lose_wake)
        event.remove(Session, "after_flush", mark_claim)

    [task_id] = committed
    [overview] = (await service.list_runs("alice"))["runs"]
    run_id = overview["run_id"]
    before = await service.get_run("alice", run_id)
    [task] = before["tasks"]
    assert task["run_id"] == task_id
    assert task["status"] == "running"
    assert task["state"].get("execution_id") is None
    assert not workflow_env.observations

    if control != "active":
        await service.control_run(
            "alice",
            run_id,
            {
                "action": "pause" if control == "paused" else "cancel",
                "request_id": control,
            },
        )
        await service.sweep_pending_dispatches()
        assert not workflow_env.observations
        assert (await service.get_run("alice", run_id))["control"]["status"] == control
        if control == "cancelled":
            replayed = await service.advance_run("alice", run_id)
            assert replayed["status"] == "cancelled"
            assert replayed["tasks"][0]["status"] == "cancelled"
            assert not workflow_env.observations
            return
        await service.control_run(
            "alice", run_id, {"action": "continue", "request_id": "resume"}
        )

    # No notification or unrelated task is available to wake the active run.
    maintenance = asyncio.create_task(service.run_maintenance())
    try:
        await asyncio.wait_for(collecting.wait(), timeout=5)
        await asyncio.gather(
            service.sweep_pending_dispatches(),
            service.advance_run("alice", run_id),
            service.advance_run("alice", run_id),
        )
        release.set()
        finished = await completed_run(service, run_id)
        await service.sweep_pending_dispatches()
    finally:
        maintenance.cancel()
        await asyncio.gather(maintenance, return_exceptions=True)

    assert finished["status"] == "completed"
    [after] = finished["tasks"]
    assert after["status"] == "completed"
    assert after["state"]["attempt_id"] == task["state"]["attempt_id"]
    assert after["snapshot"] == task["snapshot"]
    assert await service.get_result("alice", run_id) == {
        "text": "Complete report. " * 1000
    }
    events = await service.get_events("alice", run_id, before["cursor"])
    assert [
        item["kind"]
        for item in events["events"]
        if item["run_id"] == task_id
        and item["kind"] in {"task.running", "result.accepted", "task.completed"}
    ] == ["result.accepted", "task.completed"]
    assert [
        item["kind"] for item in after["events"] if item["kind"] == "task.running"
    ] == ["task.running"]
    [(binding, prompt, _)] = workflow_env.observations
    assert binding.turn_id == task["state"]["attempt_id"]
    assert task["snapshot"]["task"] in prompt
