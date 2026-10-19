"""Durable tool-call identity and ownership through public run services."""

import asyncio
import copy
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.workflow_assets import router
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_saved_tool_result_rejects_conflicting_duplicates_and_displaced_writers(
    workflow_env,
):
    """Public detail preserves full results while fencing stale writes.

    Args:
        workflow_env: Real SQLite and the approved injectable execution seam.
    """
    env = workflow_env
    dispatched = asyncio.Event()

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        """Hold the scripted executor after its durable claim.

        Args:
            session_id: Member conversation.
            text: Frozen task input.
            context: Temporary framing.
            execution_token: Normal pipeline execution binding.
            message_id: Persisted message identity.

        Returns:
            The original delivery identity.
        """
        dispatched.set()
        return message_id

    async def collect(session_id, message_id, member_id):
        """Keep execution active while the public checkpoint boundary is exercised.

        Args:
            session_id: Member conversation.
            message_id: Actual dispatched message.
            member_id: Frozen member.

        Returns:
            No response; shutdown cancels this pending execution.
        """
        await asyncio.Future()

    service = WorkflowService(
        env.db,
        env.roles,
        ports_factory=lambda username: TeamPorts(
            deliver=deliver,
            collect=collect,
            is_busy=lambda sid: False,
            emit=lambda event: None,
        ),
    )
    try:
        started = await service.start_run(
            "alice", env.asset["asset_id"], {"version": 1}
        )
        await asyncio.wait_for(dispatched.wait(), 2)
        run = await service.get_run("alice", started["run_id"])
        run_id, attempt_id, execution_id = (
            run["run_id"],
            run["state"]["attempt_id"],
            run["state"]["execution_id"],
        )
        call = {
            "record_id": "durable-call",
            "call_id": "provider-call",
            "name": "publish",
            "args": {"title": "Original"},
            "status": "intent",
            "raw_results": [],
            "context_position": None,
        }
        boundary = {
            "phase": "response",
            "invocation_id": "original-model-step",
            "response": {"tools_call_name": ["publish"]},
            "tool_calls": [call],
        }
        await service.save_checkpoint(
            "alice", run_id, attempt_id, execution_id, copy.deepcopy(boundary)
        )
        call["status"] = "started"
        boundary.update(phase="tools_started", started_tools=["provider-call"])
        await service.save_checkpoint(
            "alice", run_id, attempt_id, execution_id, copy.deepcopy(boundary)
        )
        call["status"] = "result_saved"
        call["raw_results"] = [
            {
                "content": [
                    {"type": "text", "text": "Receipt 731. " * 4000 + "COMPLETE_END"}
                ]
            }
        ]
        boundary["phase"] = "tool_result"
        await service.save_checkpoint(
            "alice", run_id, attempt_id, execution_id, copy.deepcopy(boundary)
        )
        await service.save_checkpoint(
            "alice", run_id, attempt_id, execution_id, copy.deepcopy(boundary)
        )
        duplicated_result = copy.deepcopy(boundary)
        duplicated_result["tool_calls"][0]["raw_results"] *= 2
        with pytest.raises(RoleAssetError) as duplicate_rejected:
            await service.save_checkpoint(
                "alice", run_id, attempt_id, execution_id, duplicated_result
            )
        assert duplicate_rejected.value.status == 409
        call["status"] = "applied"
        call["context_position"] = 3
        boundary.update(phase="tools_applied", model_calls=1)
        current_boundary = {
            "phase": "request",
            "invocation_id": "next-model-step",
            "model_calls": 2,
            "tool_calls": [],
        }
        await service.save_checkpoint(
            "alice", run_id, attempt_id, execution_id, copy.deepcopy(boundary)
        )
        await service.save_checkpoint(
            "alice",
            run_id,
            attempt_id,
            execution_id,
            current_boundary,
        )
        original = await service.get_run("alice", run_id)
        assert len(original["tool_calls"]) == 1
        assert original["tool_calls"][0]["raw_results"][0]["content"][0][
            "text"
        ].endswith("COMPLETE_END")
        assert original["tool_calls"][0]["started_execution_id"] == execution_id
        corrupted = copy.deepcopy(boundary)
        corrupted["tool_calls"][0]["raw_results"] = [
            {"content": [{"type": "text", "text": "Changed receipt"}]}
        ]
        stale = copy.deepcopy(boundary)
        stale["tool_calls"][0]["status"] = "intent"
        operations = [
            service.save_checkpoint(
                "alice", run_id, attempt_id, execution_id, copy.deepcopy(boundary)
            ),
            service.save_checkpoint(
                "alice", run_id, attempt_id, execution_id, corrupted
            ),
            service.save_checkpoint("alice", run_id, attempt_id, execution_id, stale),
            service.save_checkpoint(
                "alice", run_id, "retired-attempt", execution_id, current_boundary
            ),
            service.save_checkpoint(
                "alice", run_id, attempt_id, "old-executor", current_boundary
            ),
            service.record_process(
                "alice",
                run_id,
                "retired-attempt",
                "late",
                "tool_result",
                {},
                execution_id=execution_id,
            ),
            service.submit_result(
                "alice",
                run_id,
                "retired-attempt",
                {"text": "Late"},
                execution_id=execution_id,
            ),
            service.store_file(
                "alice",
                run_id,
                "retired-attempt",
                "late.txt",
                b"Late",
                execution_id=execution_id,
            ),
        ]
        rejected_writes = await asyncio.gather(*operations, return_exceptions=True)
        assert all(
            isinstance(rejected, RoleAssetError) and rejected.status == 409
            for rejected in rejected_writes
        )
        assert (await service.get_run("alice", run_id))["tool_calls"] == original[
            "tool_calls"
        ]
        assert "tool_calls" not in (await service.list_runs("alice"))["runs"][0]
        app = FastAPI()
        app.state.services = SimpleNamespace(workflow_assets=service)
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[require_dashboard_user] = lambda: "alice"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            detail = await client.get(f"/api/v1/workflow_runs/{run_id}")
            assert detail.status_code == 200
            assert detail.json()["data"]["tool_calls"] == original["tool_calls"]
            app.dependency_overrides[require_dashboard_user] = lambda: "bob"
            assert (
                await client.get(f"/api/v1/workflow_runs/{run_id}")
            ).status_code == 404
    finally:
        await service.shutdown()
