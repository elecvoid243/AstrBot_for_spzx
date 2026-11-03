"""Whole-run controls through the persistent public application boundary."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.workflow_assets import router
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_pause_stops_dispatch_and_continue_keeps_the_original_attempt(
    workflow_env,
):
    """Pause admission and deduplicate controls without replacing the Attempt.

    Args:
        workflow_env: Public services and real SQLite with scripted execution ports.
    """
    env = workflow_env
    delivered = asyncio.Event()

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        delivered.set()
        return message_id

    async def collect(session_id, message_id, member_id):
        await asyncio.Event().wait()

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    paused = await env.service.control_run(
        "alice",
        run["run_id"],
        {
            "request_id": "pause-one",
            "action": "pause",
        },
    )
    assert paused["control"]["status"] == "paused"
    assert (
        await env.service.control_run(
            "alice",
            run["run_id"],
            {
                "request_id": "pause-one",
                "action": "pause",
            },
        )
    )["control"] == paused["control"]
    continued = await env.service.control_run(
        "alice",
        run["run_id"],
        {
            "request_id": "continue-one",
            "action": "continue",
        },
    )
    await asyncio.wait_for(delivered.wait(), 3)
    assert continued["snapshot"] == run["snapshot"]
    assert (
        continued["tasks"][0]["state"]["attempt_id"]
        == run["tasks"][0]["state"]["attempt_id"]
    )
    with pytest.raises(RoleAssetError):
        await env.service.control_run(
            "bob",
            run["run_id"],
            {
                "request_id": "cancel-other",
                "action": "cancel",
            },
        )


@pytest.mark.asyncio
async def test_retry_retains_failed_attempt_usage_and_fixed_inputs(workflow_env):
    """Retain failure evidence and exact artifact versions in a new Attempt.

    Args:
        workflow_env: Public services and real SQLite with scripted execution ports.
    """
    env = workflow_env
    source = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    source = await completed_run(env.service, source["run_id"])
    await env.service.revise_artifact(
        "alice",
        source["tasks"][0]["run_id"],
        {
            "request_id": "source-v2",
            "expected_version": 1,
            "content": {"text": "Changed source content"},
        },
    )
    bindings = []
    stopped = asyncio.Event()

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        bindings.append(AgentTeamExecutionRegistry.resolve(execution_token))
        return message_id

    async def collect(session_id, message_id, member_id):
        if len(bindings) == 1:
            binding = bindings[0]
            await binding.record_process(
                "response-one",
                "model_response",
                {
                    "text": "Provider failure evidence",
                    "usage": {"input_tokens": 17, "output_tokens": 9},
                },
            )
            raise RuntimeError("Provider rejected the request")
        stopped.set()
        await asyncio.Event().wait()

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    definition = {
        "kind": "autonomous",
        "name": "Pinned retry inputs",
        "coordinator": {
            "id": "coordinator",
            "role_id": env.role["role_id"],
            "role_version": 1,
        },
        "allowed_roles": [
            {"id": "writer", "role_id": env.role["role_id"], "role_version": 1}
        ],
    }
    asset = await env.service.save_asset("alice", {"definition": definition})
    original = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {
            "version": 1,
            "inputs": {"goal": "Frozen subject"},
            "input_artifacts": [source["result_ref"]],
        },
    )
    assert original["snapshot"]["input_artifacts"] == [source["result_ref"]]
    failed = await completed_run(env.service, original["run_id"])
    assert failed["status"] == "blocked"
    failed = failed["tasks"][0]
    payload = {
        "request_id": "retry-one",
        "attempt_id": failed["state"]["attempt_id"],
        "input_policy": "original",
    }
    retried = await env.service.retry_task("alice", failed["run_id"], payload)
    await asyncio.wait_for(stopped.wait(), 3)
    repeated = await env.service.retry_task("alice", failed["run_id"], payload)
    assert repeated["state"]["attempt_id"] == retried["state"]["attempt_id"]
    assert retried["state"]["attempt_id"] != failed["state"]["attempt_id"]
    assert retried["snapshot"] == failed["snapshot"]
    assert retried["snapshot"]["inputs"] == {"goal": "Frozen subject"}
    assert retried["snapshot"]["input_artifacts"] == [source["result_ref"]]
    assert retried["attempts"][0]["status"] == "failed"
    assert retried["attempts"][0]["state"]["reason"] == "Provider rejected the request"
    assert retried["process"] == failed["process"]
    with pytest.raises(RoleAssetError, match="Attempt"):
        await env.service.submit_result(
            "alice",
            failed["run_id"],
            payload["attempt_id"],
            {"text": "Late old result"},
        )
    with pytest.raises(RoleAssetError):
        await env.service.retry_task(
            "alice", failed["run_id"], {**payload, "input_policy": "latest"}
        )


@pytest.mark.asyncio
async def test_pause_preserves_local_failure_and_retry_uses_graph_capacity(
    workflow_env,
):
    """Keep local waits and queue graph retries behind occupied capacity.

    Args:
        workflow_env: Public services and real SQLite with scripted execution ports.
    """
    env = workflow_env
    started = []
    texts = {}
    sibling_started = asyncio.Event()
    release = asyncio.Event()

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        started.append(text)
        texts[session_id] = text
        return message_id

    async def collect(session_id, message_id, member_id):
        from astrbot.core.agent_team_tools import AgentTeamToolRegistry

        if texts[session_id] == "failed" and started.count("failed") == 1:
            raise RuntimeError("Local branch failure")
        if texts[session_id] == "sibling":
            sibling_started.set()
            await release.wait()
        await AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[
            0
        ].call(None, text="Verified result")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Controlled graph",
                "result_task_id": "successor",
                "max_parallel": 1,
                "tasks": [
                    {
                        "id": name,
                        "name": name,
                        "member": name,
                        "role_id": env.role["role_id"],
                        "role_version": 1,
                        "task": name,
                        "overrides": {"max_instances": 3},
                    }
                    for name in ["failed", "sibling", "successor"]
                ],
                "edges": [{"source": "failed", "target": "successor"}],
            }
        },
    )
    run = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    await asyncio.wait_for(sibling_started.wait(), 4)
    paused = await env.service.control_run(
        "alice", run["run_id"], {"request_id": "pause-graph", "action": "pause"}
    )
    failed = next(task for task in paused["tasks"] if task["node_id"] == "failed")
    assert failed["status"] == "failed"
    await env.service.control_run(
        "alice", run["run_id"], {"request_id": "continue-graph", "action": "continue"}
    )
    current = await env.service.get_run("alice", failed["run_id"])
    assert current["status"] == "failed"
    assert current["state"]["attempt_id"] == failed["state"]["attempt_id"]
    payload = {
        "request_id": "retry-graph",
        "attempt_id": failed["state"]["attempt_id"],
        "input_policy": "original",
    }
    responses = await asyncio.gather(
        env.service.retry_task("alice", failed["run_id"], payload),
        env.service.retry_task("alice", failed["run_id"], payload),
    )
    assert responses[0]["state"]["attempt_id"] == responses[1]["state"]["attempt_id"]
    assert started == ["failed", "sibling"]
    assert responses[0]["status"] == "pending"
    release.set()
    finished = await completed_run(env.service, run["run_id"])
    assert finished["status"] == "completed"
    assert started == ["failed", "sibling", "failed", "successor"]


@pytest.mark.asyncio
async def test_cancel_is_terminal_propagates_and_rejects_late_public_writes(
    workflow_env,
):
    """Propagate cancellation and reject late observations and submissions.

    Args:
        workflow_env: Public services and real SQLite with scripted execution ports.
    """
    env = workflow_env
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    binding = None

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        nonlocal binding
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        return message_id

    async def collect(session_id, message_id, member_id):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    await asyncio.wait_for(entered.wait(), 3)
    decision = {"request_id": "cancel-one", "action": "cancel"}
    results = await asyncio.gather(
        env.service.control_run("alice", run["run_id"], decision),
        env.service.control_run("alice", run["run_id"], decision),
    )
    assert all(result["status"] == "cancelled" for result in results)
    await asyncio.wait_for(cancelled.wait(), 3)
    for operation in [
        env.service.record_process(
            "alice",
            binding.run_id,
            binding.turn_id,
            "late-model",
            "model_response",
            {"text": "Late model"},
            execution_id=(await env.service.get_run("alice", binding.run_id))["state"][
                "execution_id"
            ],
        ),
        env.service.submit_result(
            "alice", binding.run_id, binding.turn_id, {"text": "Late result"}
        ),
    ]:
        with pytest.raises(RoleAssetError):
            await operation
    with pytest.raises(RoleAssetError):
        await env.service.control_run(
            "alice", run["run_id"], {"request_id": "cancel-one", "action": "continue"}
        )
    with pytest.raises(RoleAssetError):
        await env.service.control_run(
            "alice", run["run_id"], {"request_id": "too-late", "action": "continue"}
        )
    current = await env.service.get_run("alice", run["run_id"])
    assert current["status"] == "cancelled"
    assert (
        len([event for event in current["events"] if event["kind"] == "run.cancel"])
        == 1
    )


@pytest.mark.asyncio
async def test_http_controls_validate_owner_revision_and_reused_request(workflow_env):
    """Validate ownership, observed revisions and input policy over HTTP.

    Args:
        workflow_env: Public services and real SQLite with scripted execution ports.
    """
    env = workflow_env

    async def collect(session_id, message_id, member_id):
        await asyncio.Event().wait()

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        return message_id

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "bob"
    url = f"/api/v1/workflow_runs/{run['run_id']}/control"
    payload = {"request_id": "http-pause", "action": "pause", "expected_revision": 0}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.post(url, json=payload)).status_code == 404
        app.dependency_overrides[require_dashboard_user] = lambda: "alice"
        assert (
            await client.post(url, json={**payload, "action": "invalid"})
        ).status_code == 422
        assert (await client.post(url, json=payload)).status_code == 200
        assert (await client.post(url, json=payload)).status_code == 200
        assert (
            await client.post(url, json={**payload, "action": "continue"})
        ).status_code == 409
        assert (
            await client.post(
                url,
                json={**payload, "request_id": "stale-continue", "action": "continue"},
            )
        ).status_code == 409
        retry = f"/api/v1/workflow_runs/{run['tasks'][0]['run_id']}/retry"
        assert (
            await client.post(
                retry,
                json={
                    "request_id": "unsafe",
                    "attempt_id": run["tasks"][0]["state"]["attempt_id"],
                    "input_policy": "latest",
                },
            )
        ).status_code == 422


@pytest.mark.asyncio
async def test_paused_time_does_not_exhaust_the_reply_timeout(workflow_env):
    """Suspend the reply deadline during a persisted whole-run pause.

    Args:
        workflow_env: Public services and real SQLite with scripted execution ports.
    """
    env = workflow_env
    entered = asyncio.Event()
    release = asyncio.Event()

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        return message_id

    async def collect(session_id, message_id, member_id):
        from astrbot.core.agent_team_tools import AgentTeamToolRegistry

        entered.set()
        await release.wait()
        await AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[
            0
        ].call(None, text="Completed after pause")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver,
        collect=collect,
        is_busy=lambda _: False,
        emit=lambda _: None,
        reply_timeout=1.0,
    )
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    await asyncio.wait_for(entered.wait(), 3)
    await env.service.control_run(
        "alice", run["run_id"], {"request_id": "pause-timeout", "action": "pause"}
    )
    await asyncio.sleep(1.5)
    assert (await env.service.get_run("alice", run["run_id"]))["status"] == "running"
    await env.service.control_run(
        "alice", run["run_id"], {"request_id": "continue-timeout", "action": "continue"}
    )
    release.set()
    assert (await completed_run(env.service, run["run_id"]))["status"] == "completed"


@pytest.mark.asyncio
async def test_resumed_reply_budget_excludes_a_previously_paused_poll(workflow_env):
    """Do not charge a suspended polling interval after continuation.

    Args:
        workflow_env: Public services with real persistence and external execution.
    """
    env = workflow_env
    entered = asyncio.Event()
    release = asyncio.Event()

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        return message_id

    async def collect(session_id, message_id, member_id):
        from astrbot.core.agent_team_tools import AgentTeamToolRegistry

        entered.set()
        await release.wait()
        # Cross the resumed poll while staying within the active reply budget.
        await asyncio.sleep(1.8)
        await AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[
            0
        ].call(None, text="Completed after paused interval")
        return "Done", []

    env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver,
        collect=collect,
        is_busy=lambda _: False,
        emit=lambda _: None,
        reply_timeout=2,
    )
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    await asyncio.wait_for(entered.wait(), 3)
    await env.service.control_run(
        "alice", run["run_id"], {"request_id": "pause-sampling", "action": "pause"}
    )
    await asyncio.sleep(2.3)
    assert (await env.service.get_run("alice", run["run_id"]))["status"] == "running"
    await env.service.control_run(
        "alice",
        run["run_id"],
        {"request_id": "continue-sampling", "action": "continue"},
    )
    release.set()
    finished = await completed_run(env.service, run["run_id"])
    assert finished["status"] == "completed", finished
    assert finished["result"] == {"text": "Completed after paused interval"}
