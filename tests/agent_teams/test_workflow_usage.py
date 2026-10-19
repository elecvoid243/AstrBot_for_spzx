"""Root accounting and admission through the shared public run boundary."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.utils.session_lock import session_lock_manager
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.workflow_assets import router
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env
from tests.agent_teams.test_workflow_graph import wait_graph
from tests.agent_teams.test_workflow_loops import loop_script as loop_script
from tests.agent_teams.test_workflow_loops import revision_definition


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", ["flat", "root_ceiling", "local_ceiling"])
async def test_quota_wait_automatically_admits_ready_branches_without_reusing_member(
    workflow_env,
    layout,
):
    """Released slots start independent work without restarting the parked owner.

    Args:
        workflow_env: Public execution services with real SQLite persistence.
        layout: Flat graph or nested graph whose root/local ceiling is binding.
    """
    env = workflow_env
    ports = env.service.ports_factory("alice")
    original_collect = ports.collect
    bindings, deliveries = {}, []
    original_attempt = None
    b_reserved, c_started, e_finished = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    release_b, release_c = asyncio.Event(), asyncio.Event()

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        """Observe the original delivery identity at the public execution port.

        Args:
            session_id: Frozen member session.
            text: Frozen task text.
            context: Runner context.
            execution_token: Registered executor binding.
            message_id: Original delivery identity.

        Returns:
            The unchanged delivery identity.
        """
        nonlocal original_attempt
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        bindings[message_id] = binding
        if binding.node_id != "coordinator":
            deliveries.append(binding.node_id)
        if binding.node_id == "a" and original_attempt is None:
            original_attempt = binding.turn_id
        return message_id

    async def collect(session_id, message_id, member_id):
        """Keep real session ownership while external usage is awaiting release.

        Args:
            session_id: Actual member session.
            message_id: Observed delivery identity.
            member_id: Frozen member identity.

        Returns:
            A validated result from the original public collection port.
        """
        binding = bindings[message_id]
        node = binding.node_id
        async with session_lock_manager.acquire_lock(binding.umo):
            if node == "coordinator":
                tools = {
                    tool.name: tool
                    for tool in AgentTeamToolRegistry.get_tools(binding.umo)
                }
                called = json.loads(
                    await tools["team_manage"].call(
                        None,
                        action="call_workflow",
                        request_id="child-call",
                        name="Nested work",
                        asset_id=child["asset_id"],
                        version=1,
                        inputs={},
                    )
                )
                await tools["team_manage"].call(
                    None,
                    action="wait",
                    request_id="child-wait",
                    task_ids=[called["task_id"]],
                )
                return await original_collect(session_id, message_id, member_id)
            if node == "a":
                await b_reserved.wait()
            large = node in {"a", "b"}
            receipt = await binding.begin_usage(
                {
                    "receipt_id": "delivery-" + node,
                    "invocation_id": message_id,
                    "kind": "model",
                    "input_tokens": 10 if large else 1,
                    "max_output_tokens": 10 if large else 1,
                }
            )
            if node == "b":
                b_reserved.set()
                await release_b.wait()
            if node == "c":
                c_started.set()
                await release_c.wait()
            await binding.report_usage(
                receipt["receipt_id"],
                {
                    "input_tokens": 1,
                    "output_tokens": 1 if large else 0,
                },
            )
            result = await original_collect(session_id, message_id, member_id)
            if node == "e":
                e_finished.set()
            return result

    ports.deliver, ports.collect = deliver, collect
    env.service.ports_factory = lambda username: ports
    definition = {
        "name": "Quota release wakes ready work",
        "max_parallel": 2,
        "limits": {"token_budget": 25, "max_output_tokens": 10},
        "tasks": [
            {
                "id": key,
                "name": key,
                "member": "shared" if key in {"a", "d"} else key,
                "role_id": env.role["role_id"],
                "role_version": 1,
                "task": key,
                "overrides": {"max_parallel": 5, "max_instances": 6},
            }
            for key in ("b", "a", "d", "c", "e")
        ],
    }
    if layout != "flat":
        child = await env.service.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Nested ready work",
                    "max_parallel": 3 if layout == "root_ceiling" else 1,
                    "tasks": definition["tasks"][1:],
                }
            },
        )
        team = await env.service.save_asset(
            "alice",
            {
                "definition": {
                    "kind": "autonomous",
                    "name": "Nested coordinator",
                    "max_parallel": 3,
                    "coordinator": {
                        "role_id": env.role["role_id"],
                        "role_version": 1,
                        "overrides": {"max_parallel": 5, "max_instances": 6},
                    },
                    "allowed_roles": [
                        {"role_id": env.role["role_id"], "role_version": 1}
                    ],
                    "allowed_workflows": [
                        {
                            "asset_id": child["asset_id"],
                            "version": 1,
                            "result_task_id": "e",
                        }
                    ],
                }
            },
        )
        definition["max_parallel"] = 2 if layout == "root_ceiling" else 3
        definition["required_inputs"] = ["goal"]
        definition["tasks"] = [
            definition["tasks"][0],
            {
                "id": "nested",
                "name": "Nested",
                "team": {"asset_id": team["asset_id"], "version": 1},
                "inputs": {"goal": {"input": "goal"}},
            },
        ]
    asset = await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {"version": 1, "inputs": {"goal": "Work"} if layout != "flat" else {}},
    )
    try:
        await asyncio.wait_for(c_started.wait(), 5)
        current = await env.service.get_run("alice", run["run_id"])
        tasks = {
            task["node_id"]: task
            for task in [
                *current["tasks"],
                *[
                    task
                    for scope in current.get("scopes", [])
                    for task in scope["tasks"]
                ],
            ]
        }
        assert tasks["a"]["state"]["usage_wait"]
        assert tasks["a"]["state"]["attempt_id"] == original_attempt
        assert tasks["d"]["status"] == tasks["e"]["status"] == "pending"
        # Concurrent notifications cannot duplicate ownership or exceed root slots.
        await asyncio.gather(
            *(env.service.advance_run("alice", run["run_id"]) for _ in range(4))
        )
        assert sorted(deliveries) == ["a", "b", "c"]
        release_c.set()
        await asyncio.wait_for(e_finished.wait(), 3)
        current = await env.service.get_run("alice", run["run_id"])
        assert (
            next(
                task
                for task in [
                    *current["tasks"],
                    *[
                        task
                        for scope in current.get("scopes", [])
                        for task in scope["tasks"]
                    ],
                ]
                if task["node_id"] == "d"
            )["status"]
            == "pending"
        )
        release_b.set()
        final = await completed_run(env.service, run["run_id"])
        assert final["status"] == "completed"
        assert final["usage"]["model_calls"] == 5
        assert final["usage"]["tokens"]["reported"] == 7
        assert sorted(deliveries) == ["a", "b", "c", "d", "e"]
        assert (
            next(
                task
                for task in [
                    *final["tasks"],
                    *[
                        task
                        for scope in final.get("scopes", [])
                        for task in scope["tasks"]
                    ],
                ]
                if task["node_id"] == "a"
            )["state"]["attempt_id"]
            == original_attempt
        )
    finally:
        release_c.set()
        release_b.set()


@pytest.mark.asyncio
async def test_quota_wait_keeps_the_member_session_exclusive(workflow_env):
    """Released global slots cannot admit a sibling holding the same session.

    Args:
        workflow_env: Public services, actual SQLite storage and execution ports.
    """
    env = workflow_env
    ports = env.service.ports_factory("alice")
    original_collect = ports.collect
    bindings = {}
    reserved = asyncio.Event()
    release = asyncio.Event()

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        bindings[message_id] = AgentTeamExecutionRegistry.resolve(execution_token)
        return message_id

    async def collect(session_id, message_id, member_id):
        binding = bindings[message_id]
        # The real Agent pipeline retains this exact lock across quota waits.
        async with session_lock_manager.acquire_lock(binding.umo):
            if binding.node_id == "a":
                await reserved.wait()
            receipt = await binding.begin_usage(
                {
                    "receipt_id": binding.node_id + "-delivery",
                    "invocation_id": message_id,
                    "kind": "model",
                    "input_tokens": 0 if binding.node_id == "b" else 10,
                    "max_output_tokens": 1 if binding.node_id == "b" else 10,
                }
            )
            if binding.node_id == "c":
                reserved.set()
                await release.wait()
            await binding.report_usage(
                receipt["receipt_id"],
                {
                    "input_tokens": 0 if binding.node_id == "b" else 1,
                    "output_tokens": 1,
                },
            )
            return await original_collect(session_id, message_id, member_id)

    ports.deliver = deliver
    ports.collect = collect
    env.service.ports_factory = lambda username: ports
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Shared member quota wait",
                "max_parallel": 2,
                "limits": {"token_budget": 25, "max_output_tokens": 10},
                "tasks": [
                    {
                        "id": key,
                        "name": key,
                        "member": "shared" if key in {"a", "b"} else "other",
                        "role_id": env.role["role_id"],
                        "role_version": 1,
                        "task": key,
                        "overrides": {"max_parallel": 2, "max_instances": 2},
                    }
                    for key in ("c", "a", "b")
                ],
            }
        },
    )
    run = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    for _ in range(200):
        current = await env.service.get_run("alice", run["run_id"])
        parked = next(
            task for task in current["tasks"] if task["snapshot"]["node_id"] == "a"
        )
        if parked["state"].get("usage_wait"):
            break
        await asyncio.sleep(0.01)
    assert parked["state"].get("usage_wait")
    assert parked["state"]["capacity_released"] is True
    await env.service.advance_run("alice", run["run_id"])
    current = await env.service.get_run("alice", run["run_id"])
    assert (
        next(task for task in current["tasks"] if task["snapshot"]["node_id"] == "b")[
            "status"
        ]
        == "pending"
    )
    release.set()
    final = await completed_run(env.service, run["run_id"])
    assert final["status"] == "completed"
    assert final["usage"]["model_calls"] == 3
    assert final["usage"]["tokens"]["reported"] == 5


@pytest.mark.asyncio
async def test_http_limits_are_validated_saved_and_frozen_at_launch(workflow_env):
    """HTTP callers receive the same limits and unknown usage as public services.

    Args:
        workflow_env: Real shared services and SQLite storage.
    """
    env = workflow_env
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        definition = {
            **env.asset["definition"],
            "limits": {"token_budget": 100, "max_steps": 2, "max_output_tokens": 8},
        }
        saved = await client.post(
            "/api/v1/workflow_assets", json={"definition": definition}
        )
        assert saved.status_code == 200
        asset = saved.json()["data"]
        started = await client.post(
            f"/api/v1/workflow_assets/{asset['asset_id']}/runs", json={"version": 1}
        )
        assert started.status_code == 200
        root = started.json()["data"]
        assert root["usage"]["limits"] == definition["limits"]
        assert root["usage"]["tokens"]["reported"] is None
        definition["limits"]["max_steps"] = 1.5
        invalid = await client.post(
            "/api/v1/workflow_assets", json={"definition": definition}
        )
        assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_call_receipts_survive_duplicate_reports_and_keep_their_identity(
    workflow_env,
):
    """A persisted receipt counts once and remains traceable after completion.

    Args:
        workflow_env: Public asset/run services with a real SQLite database.
    """
    env = workflow_env
    ports = env.service.ports_factory("alice")
    release = asyncio.Event()
    original_collect = ports.collect

    async def collect(*args, **kwargs):
        await release.wait()
        return await original_collect(*args, **kwargs)

    ports.collect = collect
    env.service.ports_factory = lambda username: ports
    started = await env.service.start_run(
        "alice", env.asset["asset_id"], {"version": 1}
    )
    for _ in range(200):
        current = await env.service.get_run("alice", started["run_id"])
        if current["state"].get("execution_id"):
            break
        await asyncio.sleep(0.01)
    receipt = await env.service.begin_usage(
        "alice",
        current["run_id"],
        current["state"]["attempt_id"],
        current["state"]["execution_id"],
        {
            "receipt_id": "model-delivery-1",
            "invocation_id": "plan-1",
            "kind": "model",
            "input_tokens": 10,
            "max_output_tokens": 8,
        },
    )
    for _ in range(2):
        await env.service.report_usage(
            "alice",
            receipt["receipt_id"],
            current["state"]["execution_id"],
            {"input_tokens": 10, "output_tokens": 4},
        )
    observed = await env.service.get_run("alice", current["run_id"])
    assert observed["usage"]["tokens"] == {
        "reported": 14,
        "estimated": 0,
        "reserved": 0,
        "unknown_calls": 0,
    }
    assert observed["usage"]["ledger"][0]["invocation_id"] == "plan-1"
    assert (
        observed["usage"]["ledger"][0]["attempt_id"] == current["state"]["attempt_id"]
    )
    assert observed["usage"]["model_calls"] == 1
    with pytest.raises(RoleAssetError):
        await env.service.report_usage(
            "bob",
            receipt["receipt_id"],
            current["state"]["execution_id"],
            {"input_tokens": 1, "output_tokens": 1},
        )
    release.set()


@pytest.mark.asyncio
async def test_root_member_limit_blocks_initial_graph_instances(workflow_env):
    """A workflow cannot create more member contexts than its frozen root limit.

    Args:
        workflow_env: Public services and resource catalog.
    """
    env = workflow_env
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Two writers",
                "limits": {"max_members": 1},
                "tasks": [
                    {
                        "id": key,
                        "name": key,
                        "member": key,
                        "role_id": env.role["role_id"],
                        "role_version": 1,
                        "task": "Write",
                        "overrides": {"max_instances": 2},
                    }
                    for key in ("a", "b")
                ],
            }
        },
    )
    with pytest.raises(RoleAssetError, match="Member instance limit"):
        await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    assert (await env.service.list_runs("alice"))["runs"] == []


@pytest.mark.asyncio
async def test_root_iteration_limit_stops_a_loop_before_it_creates_another_round(
    loop_script,
):
    """The root ceiling restricts a larger local loop and survives reconstruction.

    Args:
        loop_script: Public loop executor with fixed review results.
    """
    env = loop_script.env
    definition = revision_definition(env.role["role_id"], max_iterations=3)
    definition["limits"] = {"max_iterations": 1}
    asset = await env.service.save_asset("alice", {"definition": definition})
    started = await env.service.start_run(
        "alice",
        asset["asset_id"],
        {"version": 1, "inputs": {"draft": "Original", "feedback": "Review"}},
    )
    observed = await wait_graph(env.service, started["run_id"])
    assert observed["status"] == "blocked"
    assert len(observed["loops"][0]["iterations"]) == 1
    assert observed["loops"][0]["status"] == "exhausted"
    assert observed["loops"][0]["limit_stop"]["limit"] == "max_iterations"
    assert [run["snapshot"]["node_id"] for run in loop_script.observations] == [
        "write",
        "review",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("max_steps", [1, 2])
async def test_explicit_retry_keeps_consumption_and_accepts_late_original_reports(
    workflow_env, max_steps
):
    """A new Attempt spends extra allowance and an old report never takes ownership.

    Args:
        workflow_env: Public service with real persistence and a scripted model port.
        max_steps: Whole-root call limit, including the original failed delivery.
    """
    env = workflow_env
    ports = env.service.ports_factory("alice")
    original_collect = ports.collect
    external = []

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        receipt = await binding.begin_usage(
            {
                "receipt_id": f"delivery-{len(external)}",
                "invocation_id": message_id,
                "kind": "model",
                "input_tokens": 10,
                "max_output_tokens": 8,
            }
        )
        current = await env.service.get_run("alice", binding.run_id)
        external.append((binding, receipt, current["state"]["execution_id"]))
        if len(external) > 1:
            await binding.report_usage(
                receipt["receipt_id"], {"input_tokens": 1, "output_tokens": 1}
            )
        return message_id

    async def collect(*args, **kwargs):
        if len(external) == 1:
            raise RuntimeError("Model connection lost after delivery")
        return await original_collect(*args, **kwargs)

    ports.deliver = deliver
    ports.collect = collect
    env.service.ports_factory = lambda username: ports
    asset = await env.service.save_asset(
        "alice",
        {"definition": {**env.asset["definition"], "limits": {"max_steps": max_steps}}},
    )
    started = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    first = await completed_run(env.service, started["run_id"])
    assert first["status"] == "failed"
    assert first["usage"]["tokens"]["unknown_calls"] == 1
    await env.service.retry_task(
        "alice",
        started["run_id"],
        {
            "request_id": "explicit-retry",
            "attempt_id": first["state"]["attempt_id"],
            "input_policy": "original",
        },
    )
    second = await completed_run(env.service, started["run_id"])
    assert second["status"] == ("blocked" if max_steps == 1 else "completed")
    assert len(external) == max_steps
    old = external[0]
    await env.service.report_usage(
        "alice", old[1]["receipt_id"], old[2], {"input_tokens": 7, "output_tokens": 2}
    )
    final = await env.service.get_run("alice", started["run_id"])
    assert final["state"] == second["state"]
    assert final["usage"]["tokens"]["reported"] == (9 if max_steps == 1 else 11)
    assert final["state"]["attempt_id"] != first["state"]["attempt_id"]
    assert (
        len({receipt["attempt_id"] for receipt in final["usage"]["ledger"]})
        == max_steps
    )


@pytest.mark.asyncio
async def test_last_token_allowance_is_shared_by_in_flight_calls(workflow_env):
    """A competing call waits and uses only the allowance the first call releases.

    Args:
        workflow_env: Public services and real SQLite persistence.
    """
    env = workflow_env
    definition = {
        **env.asset["definition"],
        "limits": {"token_budget": 18, "max_output_tokens": 8},
    }
    asset = await env.service.save_asset("alice", {"definition": definition})
    ports = env.service.ports_factory("alice")
    release = asyncio.Event()
    original_collect = ports.collect

    async def collect(*args, **kwargs):
        await release.wait()
        return await original_collect(*args, **kwargs)

    ports.collect = collect
    env.service.ports_factory = lambda username: ports
    started = await env.service.start_run("alice", asset["asset_id"], {"version": 1})
    for _ in range(200):
        current = await env.service.get_run("alice", started["run_id"])
        if current["state"].get("execution_id"):
            break
        await asyncio.sleep(0.01)
    identity = (
        "alice",
        current["run_id"],
        current["state"]["attempt_id"],
        current["state"]["execution_id"],
    )
    receipts = await asyncio.gather(
        *[
            env.service.begin_usage(
                *identity,
                {
                    "receipt_id": f"competing-{i}",
                    "invocation_id": f"plan-{i}",
                    "kind": "model",
                    "input_tokens": 10,
                    "max_output_tokens": 8,
                },
            )
            for i in range(2)
        ]
    )
    admitted = next(r for r in receipts if not r.get("waiting"))
    assert sum(r.get("waiting", False) for r in receipts) == 1
    observed = await env.service.get_run("alice", current["run_id"])
    assert observed["usage"]["tokens"]["reserved"] == 18
    await env.service.report_usage(
        "alice",
        admitted["receipt_id"],
        identity[-1],
        {"input_tokens": 1, "output_tokens": 1},
    )
    waiting_id = (
        "competing-1" if admitted["receipt_id"] == "competing-0" else "competing-0"
    )
    accepted = await env.service.begin_usage(
        *identity,
        {
            "receipt_id": waiting_id,
            "invocation_id": "last-plan",
            "kind": "model",
            "input_tokens": 6,
            "max_output_tokens": 8,
        },
    )
    assert not accepted.get("waiting")
    assert accepted["max_output_tokens"] == 8
    await env.service.report_usage(
        "alice", waiting_id, identity[-1], {"input_tokens": 6, "output_tokens": 8}
    )
    with pytest.raises(RoleAssetError, match="limit reached"):
        await env.service.begin_usage(
            *identity,
            {
                "receipt_id": "exhausted",
                "invocation_id": "last",
                "kind": "model",
                "input_tokens": 1,
                "max_output_tokens": 8,
            },
        )
    observed = await env.service.get_run("alice", current["run_id"])
    assert observed["usage"]["tokens"] == {
        "reported": 16,
        "estimated": 0,
        "reserved": 0,
        "unknown_calls": 0,
    }
    assert observed["state"]["limit_stop"]["limit"] == "token_budget"
    assert len(observed["usage"]["ledger"]) == 2
    release.set()
