"""Control activation and ordered aggregation through the workflow HTTP API."""

import asyncio
import copy

import pytest

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from tests.agent_teams.test_workflow_assets import completed_run

from tests.agent_teams.test_workflow_assets_api import (
    publication_client as publication_client,
)
from tests.agent_teams.test_workflow_assets_api import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_control_paths_gate_structured_dependencies(
    workflow_env, publication_client
):
    text = {"type": "string"}
    contract = {
        "type": "object",
        "properties": {"text": text},
        "required": ["text"],
    }
    tasks = [
        {
            "id": "choose",
            "name": "Choose",
            "control": {"kind": "condition", "value": True},
            "inputs": {"value": {"input": "choose"}},
        },
        {
            "id": "right",
            "name": "Right",
            "transform": "parse_json",
            "output_contract": contract,
            "inputs": {"text": {"input": "value"}},
        },
        *[
            {
                "id": node,
                "name": node,
                "transform": "map_fields",
                "input_contract": contract,
                "output_contract": contract,
                "inputs": {"text": binding},
            }
            for node, binding in (
                ("left", {"input": "value"}),
                ("data_only", {"task_id": "left", "field": "text"}),
                ("needs_right", {"task_id": "right", "field": "text"}),
                ("independent", {"input": "value"}),
            )
        ],
        {"id": "collect", "name": "Collect", "control": {"kind": "join"}},
    ]
    aggregate_contract = {
        "type": "object",
        "properties": {"results": {"type": "array"}, "annotation": text},
        "required": ["results"],
    }
    tasks.append(
        {
            "id": "output",
            "name": "Output",
            "transform": "map_fields",
            "input_contract": aggregate_contract,
            "output_contract": aggregate_contract,
            "inputs": {
                "results": {"task_id": "collect", "field": "results"},
                "annotation": {"task_id": "data_only", "field": "text"},
            },
        }
    )
    definition = {
        "name": "Control diamond",
        "port_contract_version": 2,
        "entry_task_ids": ["choose", "independent"],
        "input_contract": {
            "type": "object",
            "properties": {"choose": {"type": "boolean"}, "value": text},
            "required": ["choose", "value"],
        },
        "result_task_id": "output",
        "tasks": tasks,
        "edges": [
            {
                "source": "choose",
                "target": branch,
                "condition": {"field": "matched", "value": value},
            }
            for branch, value in (("left", True), ("right", False))
        ]
        + [
            {"source": "left", "target": "collect"},
            {"source": "right", "target": "collect"},
            {"source": "collect", "target": "output"},
            {
                "source": "left",
                "target": "needs_right",
                "condition": {"field": "text", "value": "missing"},
            },
        ],
    }
    created = await publication_client.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": definition}}
    )
    assert created.status_code == 200, created.text
    asset_id = created.json()["data"]["asset_id"]
    asset = f"/api/v1/workflow_assets/{asset_id}"
    inputs = {"choose": True, "value": "selected"}
    trial = await publication_client.post(
        f"{asset}/trials",
        json={"kind": "workflow", "revision": 1, "mode": "full", "inputs": inputs},
    )
    assert trial.status_code == 200, trial.text
    published = await publication_client.post(
        f"/api/v1/agent_drafts/workflow/{asset_id}/publish", json={"revision": 1}
    )
    assert published.status_code == 200, published.text
    fixed = await publication_client.post(
        f"{asset}/runs", json={"version": 1, "inputs": inputs}
    )
    for response in (trial, fixed):
        assert response.status_code == 200, response.text
        run = response.json()["data"]
        assert run["status"] == "completed"
        assert run["result"] == {
            "results": [{"node_id": "left", "result": {"text": "selected"}}]
        }
        states = {task["snapshot"]["node_id"]: task for task in run["tasks"]}
        assert states["right"]["status"] == "skipped"
        assert states["data_only"]["status"] == "skipped"
        assert "control" in states["data_only"]["state"]["reason"]

    boundary = {
        "kind": "workflow",
        "revision": 1,
        "mode": "single",
        "node_id": "output",
    }
    preview = await publication_client.post(f"{asset}/trials/preview", json=boundary)
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["requirements"]["collect"]["fields"] == {
        "results": ["array"]
    }
    partial = await publication_client.post(
        f"{asset}/trials",
        json={
            **boundary,
            "sources": {
                "collect": {"content": trial.json()["data"]["result"]},
                "data_only": {"content": {"text": "boundary"}},
            },
        },
    )
    assert partial.status_code == 200, partial.text
    assert partial.json()["data"]["result"] == {
        **trial.json()["data"]["result"],
        "annotation": "boundary",
    }

    unentered = copy.deepcopy(definition)
    unentered["entry_task_ids"] = []
    other = await publication_client.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": unentered}}
    )
    assert other.status_code == 200, other.text
    other_id = other.json()["data"]["asset_id"]
    other_preview = f"/api/v1/workflow_assets/{other_id}/trials/preview"
    assert (
        await publication_client.post(other_preview, json=boundary)
    ).status_code == 200
    assert (
        await publication_client.post(
            other_preview, json={**boundary, "mode": "full", "node_id": None}
        )
    ).status_code == 400

    invalid = copy.deepcopy(definition)
    invalid["tasks"][-1]["input_contract"]["required"].append("annotation")
    broken = await publication_client.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": invalid}}
    )
    broken_id = broken.json()["data"]["asset_id"]
    blocked = await publication_client.post(
        f"/api/v1/agent_drafts/workflow/{broken_id}/publish", json={"revision": 1}
    )
    assert blocked.status_code == 400, blocked.text
    assert {
        "path": "definition.tasks.7.inputs.annotation",
        "code": "UNREACHABLE_DATA",
        "message": "No execution path can produce data_only.text.",
    } in blocked.json()["data"]["fields"]

    missing = await publication_client.post(
        f"{asset}/runs", json={"version": 1, "inputs": {**inputs, "value": "missing"}}
    )
    assert missing.status_code == 200, missing.text
    run = missing.json()["data"]
    assert run["status"] == "blocked"
    states = {task["snapshot"]["node_id"]: task for task in run["tasks"]}
    assert states["independent"]["status"] == "completed"
    assert states["needs_right"]["status"] == "dependency_failed"
    assert states["needs_right"]["state"]["issues"] == [
        {
            "path": "definition.tasks.4.inputs.text",
            "code": "DATA_UNAVAILABLE",
            "message": "Input port text could not read right.text because the source was skipped.",
        }
    ]

    failed_path = await publication_client.post(
        f"{asset}/runs", json={"version": 1, "inputs": {**inputs, "choose": False}}
    )
    assert failed_path.status_code == 200, failed_path.text
    failed = failed_path.json()["data"]
    states = {task["snapshot"]["node_id"]: task for task in failed["tasks"]}
    assert failed["status"] == "blocked"
    assert states["right"]["status"] == "failed"
    assert states["collect"]["status"] == "dependency_failed"
    assert states["collect"]["state"]["waiting_on"] == ["right"]
    assert states["independent"]["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "restart", [False, True], ids=["online-wake", "service-restart"]
)
async def test_list_dispatch_preserves_order_capacity_and_item_retry(
    workflow_env, publication_client, monkeypatch, restart
):
    starts = asyncio.Queue()
    permits = {}
    attempts = {}

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        permits[message_id] = asyncio.Future()
        attempts[message_id] = (binding.run_id, text)
        starts.put_nowait((binding.node_id, message_id))
        return message_id

    async def collect(session_id, message_id, member_id):
        success = await permits[message_id]
        if success:
            tool = next(
                tool
                for tool in AgentTeamToolRegistry.get_tools(
                    f"webchat:FriendMessage:{session_id}"
                )
                if tool.name == "submit_task_result"
            )
            assert (
                await tool.call(None, text=attempts[message_id][1])
                == "Result accepted."
            )
        return "done", []

    workflow_env.service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    definition = {
        "name": "List dispatch",
        "port_contract_version": 2,
        "entry_task_ids": ["batch"],
        "max_parallel": 3 if restart else 2,
        "input_contract": {
            "type": "object",
            "properties": {"items": {"type": "array"}},
            "required": ["items"],
        },
        "result_task_id": "batch",
        "tasks": [
            {
                "id": "batch",
                "name": "B" * 128,
                "member": "writer",
                "role_id": workflow_env.role["role_id"],
                "role_version": 1,
                "overrides": {"max_parallel": 3, "max_instances": 2},
                "task": "Process {item}",
                "inputs": {"items": {"input": "items"}},
                "dispatch": {"max_items": 3, "max_parallel": 2 if restart else 3},
            }
        ],
    }
    created = await publication_client.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": definition}}
    )
    assert created.status_code == 200, created.text
    asset_id = created.json()["data"]["asset_id"]
    asset = f"/api/v1/workflow_assets/{asset_id}"
    publish = await publication_client.post(
        f"/api/v1/agent_drafts/workflow/{asset_id}/publish", json={"revision": 1}
    )
    assert publish.status_code == 200, publish.text
    args = {"items": ["same", "same", {"id": 3}]}
    trial = await publication_client.post(
        f"{asset}/trials",
        json={"kind": "workflow", "revision": 1, "mode": "full", "inputs": args},
    )
    assert trial.status_code == 200, trial.text
    trial_id = trial.json()["data"]["run_id"]
    trial_run = trial.json()["data"]
    assert trial_run["status"] == "running", [
        (task["status"], task["state"]) for task in trial_run["tasks"]
    ]
    try:
        first = dict([await asyncio.wait_for(starts.get(), 5) for _ in range(2)])
    except TimeoutError:
        trial_run = await workflow_env.service.get_run("alice", trial_id)
        batch = trial_run["tasks"][0]
        child = await workflow_env.service.get_run(
            "alice", batch["state"]["child_scope_run_id"]
        )
        pytest.fail(
            str(
                (attempts, [(task["status"], task["state"]) for task in child["tasks"]])
            )
        )
    assert set(first) == {"item-0", "item-1"}
    assert attempts[first["item-0"]][0] != attempts[first["item-1"]][0]
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(starts.get(), 0.1)
    permits[first["item-1"]].set_result(True)
    permits[first["item-0"]].set_result(False)
    node_id, third = await asyncio.wait_for(starts.get(), 5)
    assert node_id == "item-2"
    permits[third].set_result(True)
    failed = await completed_run(workflow_env.service, trial_id)
    assert failed["status"] == "blocked"
    batch = failed["tasks"][0]
    assert "item-0" in batch["state"]["reason"]
    failed_id = attempts[first["item-0"]][0]
    failed_task = await workflow_env.service.get_run("alice", failed_id)
    retried = await publication_client.post(
        f"/api/v1/workflow_runs/{failed_id}/retry",
        json={
            "request_id": "retry-item-zero",
            "attempt_id": failed_task["state"]["attempt_id"],
            "input_policy": "original",
        },
    )
    assert retried.status_code == 200, retried.text
    node_id, retry = await asyncio.wait_for(starts.get(), 5)
    assert (node_id, attempts[retry]) == ("item-0", attempts[first["item-0"]])
    permits[retry].set_result(True)
    trial_result = await completed_run(workflow_env.service, trial_id)
    assert trial_result["status"] == "completed"
    assert starts.empty()

    service = workflow_env.service
    advance_run = service.advance_run

    async def drop_initial_child_wake(username, run_id, *, wake_parent=True):
        if not wake_parent:
            raise RuntimeError("lost wake before first item claim")
        return await advance_run(username, run_id, wake_parent=wake_parent)

    with monkeypatch.context() as outage:
        outage.setattr(service, "advance_run", drop_initial_child_wake)
        with pytest.raises(RuntimeError, match="before first item claim"):
            await publication_client.post(
                f"{asset}/runs", json={"version": 1, "inputs": args}
            )
    fixed_id = next(
        run["run_id"]
        for run in (await service.list_runs("alice"))["runs"]
        if run["run_id"] != trial_id
    )
    pending = await service.get_run("alice", fixed_id)
    assert pending["tasks"][0]["status"] == "waiting_child"
    child_id = pending["tasks"][0]["state"]["child_scope_run_id"]
    persisted = await service.get_run("alice", child_id)
    assert all(task["status"] == "pending" for task in persisted["tasks"])
    items = [
        task
        for task in persisted["tasks"]
        if task["snapshot"]["node_id"].startswith("item-")
    ]
    assert len({task["snapshot"]["member"]["member_id"] for task in items}) == 2
    assert len({task["snapshot"]["member"]["session_id"] for task in items}) == 3
    assert starts.empty()

    resumed = (
        WorkflowService(
            workflow_env.db, workflow_env.roles, ports_factory=service.ports_factory
        )
        if restart
        else service
    )
    try:
        await resumed.sweep_pending_dispatches()
        await resumed.sweep_pending_dispatches()
        first = dict([await asyncio.wait_for(starts.get(), 5) for _ in range(2)])
        assert set(first) == {"item-0", "item-1"}
        permits[first["item-0"]].set_result(True)
        node_id, third = await asyncio.wait_for(starts.get(), 5)
        assert node_id == "item-2"
        permits[first["item-1"]].set_result(True)
        permits[third].set_result(True)
        fixed_result = await completed_run(resumed, fixed_id)
        assert fixed_result["status"] == "completed"
        assert starts.empty()
    finally:
        if restart:
            await resumed.shutdown()
    assert (
        trial_result["result"]
        == fixed_result["result"]
        == {
            "results": [
                {"node_id": "item-0", "result": {"text": "Process same"}},
                {"node_id": "item-1", "result": {"text": "Process same"}},
                {"node_id": "item-2", "result": {"text": 'Process {"id": 3}'}},
            ]
        }
    )

    oversized = await publication_client.post(
        f"{asset}/runs", json={"version": 1, "inputs": {"items": [0, 1, 2, 3]}}
    )
    assert oversized.status_code == 200, oversized.text
    assert oversized.json()["data"]["tasks"][0]["state"]["issues"][0]["code"] == "LIMIT"
    assert starts.empty()

    empty = await publication_client.post(
        f"{asset}/runs", json={"version": 1, "inputs": {"items": []}}
    )
    assert empty.status_code == 200, empty.text
    assert empty.json()["data"]["result"] == {"results": []}

    invalid = copy.deepcopy(definition)
    invalid["tasks"][0]["output_contract"] = {"type": "object", "properties": {}}
    broken = await publication_client.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": invalid}}
    )
    broken_id = broken.json()["data"]["asset_id"]
    invalid_publish = await publication_client.post(
        f"/api/v1/agent_drafts/workflow/{broken_id}/publish", json={"revision": 1}
    )
    assert invalid_publish.status_code == 400, invalid_publish.text
    assert "default text output" in invalid_publish.text
