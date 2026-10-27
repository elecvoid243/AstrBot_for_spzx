"""Whole-run controls at the public usage admission boundary."""

import asyncio
import json

import pytest

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["continue", "cancel"])
async def test_control_keeps_original_attempt_when_pause_races_usage_reservation(
    workflow_env, tmp_path, action
):
    """Continue or cancel a paused reservation without duplicating its external effect.

    Args:
        workflow_env: Public asset/runtime services with actual SQLite persistence.
        tmp_path: Complete public observations for this execution.
        action: Whole-run control following the forced admission race.
    """
    env = workflow_env
    ports = env.service.ports_factory("alice")
    original_reserve = env.service.begin_usage
    denied = asyncio.Event()
    paused_once = False
    external = []

    async def reserve(owner, run_id, attempt_id, execution_id, payload):
        """Schedule the real public pause after admission but before reservation.

        Args:
            owner: Frozen initiating owner.
            run_id: Original task/root identity.
            attempt_id: Original Attempt, retained across continue.
            execution_id: Existing executor fence.
            payload: Unmodified receipt request from the registered binding.

        Returns:
            Actual public reservation result.

        Raises:
            RoleAssetError: Actual public reservation rejection while paused.
        """
        nonlocal paused_once
        if not paused_once:
            paused_once = True
            await env.service.control_run(
                owner, run_id, {"action": "pause", "request_id": "pause-at-reserve"}
            )
        try:
            return await original_reserve(
                owner, run_id, attempt_id, execution_id, payload
            )
        except RoleAssetError:
            denied.set()
            raise

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        """Record an external effect only after the real usage reservation commits.

        Args:
            session_id: Frozen member session.
            text: Declared task text.
            context: Existing run context.
            execution_token: Actual registered execution binding.
            message_id: Original invocation identity.

        Returns:
            The unchanged original invocation identity.
        """
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        receipt = await binding.begin_usage(
            {
                "receipt_id": "pause-gap-delivery",
                "invocation_id": message_id,
                "kind": "model",
                "input_tokens": 1,
                "max_output_tokens": 1,
            }
        )
        external.append((message_id, binding.turn_id))
        await binding.report_usage(
            receipt["receipt_id"], {"input_tokens": 1, "output_tokens": 1}
        )
        return message_id

    env.service.begin_usage = reserve
    ports.deliver = deliver
    env.service.ports_factory = lambda owner: ports
    asset = await env.service.save_asset(
        "alice",
        {"definition": {**env.asset["definition"], "limits": {"max_steps": 1}}},
    )
    try:
        started = await env.service.start_run(
            "alice", asset["asset_id"], {"version": 1}
        )
        await asyncio.wait_for(denied.wait(), 5)
        before = await env.service.get_run("alice", started["run_id"])
        assert before["control"]["status"] == "paused"
        assert before["usage"]["ledger"] == [] and external == []
        await env.service.control_run(
            "alice",
            started["run_id"],
            {"action": action, "request_id": "control-original-reservation"},
        )
        final = await completed_run(env.service, started["run_id"])
        (tmp_path / "pause-observations.json").write_text(
            json.dumps(
                {"before": before, "final": final, "external": external}, indent=2
            ),
            encoding="utf-8",
        )
        if action == "cancel":
            assert final["status"] == "cancelled", final
            assert not final["usage"]["ledger"] and external == []
            assert final["state"]["attempt_id"] == before["state"]["attempt_id"]
            return
        assert final["status"] == "completed", final
        assert final["state"]["attempt_id"] == before["state"]["attempt_id"]
        assert external == [
            (final["state"]["message_id"], final["state"]["attempt_id"])
        ]
        assert len(final["usage"]["ledger"]) == 1
        assert final["usage"]["ledger"][0]["receipt_id"] == "pause-gap-delivery"
        assert not any(
            event["kind"] in {"task.failed", "task.blocked"}
            for event in final["events"]
        )
    finally:
        env.service.begin_usage = original_reserve
