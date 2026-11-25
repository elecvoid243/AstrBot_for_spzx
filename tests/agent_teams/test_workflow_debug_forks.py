"""Verify the original-definition debug fork through the Dashboard API."""

import asyncio
import json
from dataclasses import replace

import pytest

from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.db.po import AgentWorkflowRun, AgentWorkflowToolCall
from astrbot.dashboard.api.auth import require_dashboard_user
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env
from tests.agent_teams.test_workflow_assets_api import (
    publication_client as publication_client,
)
from tests.agent_teams.test_workflow_plugin_restart import (
    configure_owner,
    fixture_service,
    ledger_graph,
)
from tests.agent_teams.test_workflow_plugin_restart import (
    external_ledger as external_ledger,
)


def debug_delivery_definitions(component, binding, child_id=None):
    """Build the fixed report workflow and its caller for the scope acceptance.

    Args:
        component: Installed controlled delivery connector descriptor.
        binding: Knowledge base selected for the connector.
        child_id: When supplied, build the parent with this pinned child.

    Returns:
        Portable workflow definition for the child or its parent.
    """
    input_names = {"text": "packet", "supplement": "source_notes"}
    child_inputs = {
        "type": "object",
        "properties": {name: {"type": "string"} for name in input_names},
        "required": list(input_names),
    }
    report = {
        "type": "object",
        "properties": {
            "report": {"type": "string"},
            "appendix": {"type": "string"},
        },
        "required": ["report", "appendix"],
    }
    if child_id:
        return {
            "name": "Multi-document report",
            "port_contract_version": 2,
            "input_contract": {
                "type": "object",
                "properties": {
                    name: {"type": "string"} for name in input_names.values()
                },
                "required": list(input_names.values()),
            },
            "entry_task_ids": ["review-report"],
            "result_task_id": "review-report",
            "tasks": [
                {
                    "id": "review-report",
                    "name": "Review the report",
                    "workflow": {"asset_id": child_id, "version": 1},
                    "input_contract": child_inputs,
                    "output_contract": report,
                    "inputs": {
                        port: {"input": source} for port, source in input_names.items()
                    },
                }
            ],
        }
    payload = {
        "type": "object",
        "properties": {
            "report": {"type": "string"},
            "send": {"type": "boolean"},
            **component["input_contract"]["properties"],
        },
        "required": ["report", "send", "record"],
    }
    supplement = {
        "type": "object",
        "properties": {"appendix": {"type": "string"}},
        "required": ["appendix"],
    }
    return {
        "name": "Citation report review",
        "port_contract_version": 2,
        "input_contract": child_inputs,
        "entry_task_ids": ["decode", "evidence"],
        "result_task_id": "compose",
        "edges": [
            {"source": "decode", "target": "join"},
            {"source": "evidence", "target": "join"},
            {"source": "join", "target": "compose"},
            {"source": "compose", "target": "review"},
            {
                "source": "review",
                "target": "send-check",
                "condition": {"field": "approved", "value": True},
            },
            {
                "source": "send-check",
                "target": "deliver",
                "condition": {"field": "matched", "value": True},
            },
            {
                "source": "send-check",
                "target": "archive",
                "condition": {"field": "matched", "value": False},
            },
        ],
        "tasks": [
            {
                "id": "decode",
                "name": "Decode citation packet",
                "transform": "parse_json",
                "output_contract": payload,
                "inputs": {"text": {"input": "text"}},
            },
            {
                "id": "evidence",
                "name": "Prepare source notes",
                "transform": "map_fields",
                "input_contract": supplement,
                "output_contract": supplement,
                "inputs": {"appendix": {"input": "supplement"}},
            },
            {
                "id": "join",
                "name": "Join citation sources",
                "control": {"kind": "join"},
            },
            {
                "id": "compose",
                "name": "Prepare reviewed report",
                "transform": "map_fields",
                "input_contract": report,
                "output_contract": report,
                "inputs": {
                    "report": {"task_id": "decode", "field": "report"},
                    "appendix": {"task_id": "evidence", "field": "appendix"},
                },
            },
            {
                "id": "review",
                "name": "Approve the exact report",
                "human": {
                    "kind": "approval",
                    "question": "Approve the report for delivery?",
                    "artifact_task_id": "compose",
                    "artifact_version": 1,
                },
            },
            {
                "id": "send-check",
                "name": "Check delivery choice",
                "control": {"kind": "condition", "value": True},
                "inputs": {"value": {"task_id": "decode", "field": "send"}},
            },
            {
                "id": "deliver",
                "name": "Deliver approved report",
                "transform": "plugin_component_v1",
                "component": component["reference"],
                "knowledge_base_id": binding,
                "input_contract": component["input_contract"],
                "output_contract": component["output_contract"],
                "inputs": {"record": {"task_id": "decode", "field": "record"}},
            },
            {
                "id": "archive",
                "name": "Keep report internal",
                "transform": "json_text",
                "inputs": {"value": {"task_id": "compose", "field": "report"}},
            },
        ],
    }


@pytest.mark.asyncio
async def test_downstream_debug_uses_boundary_and_requires_its_own_approval(
    tmp_path, publication_client, external_ledger, monkeypatch
):
    """Run the original and the fork via the Dashboard API and verify the ledger.

    Args:
        tmp_path: Disposable database and plugin installation.
        publication_client: Authenticated Dashboard API client.
        external_ledger: Independent recorder at the delivery boundary.
        monkeypatch: Point the fixture connector at the local recorder.
    """
    monkeypatch.setenv("ATN178_LEDGER_URL", external_ledger.url)
    monkeypatch.setenv("ATN178_LEDGER_KEY", "local-ledger-secret")
    service, _ = await fixture_service(tmp_path / "scope", "debug-scope")
    await configure_owner(service)
    client = publication_client
    client._transport.app.state.services.workflow_assets = service
    try:
        component = next(
            item
            for item in await service.list_components("alice")
            if item["reference"]["id"].endswith("/previewable")
        )
        graph = debug_delivery_definitions(component, "debug-scope")
        create = "/api/v1/workflow_assets"
        created = await client.post(create, json={"definition": graph})
        assert created.status_code == 200, created.text
        child_id = created.json()["data"]["asset_id"]
        created = await client.post(
            create,
            json={
                "definition": debug_delivery_definitions(
                    component, "debug-scope", child_id
                )
            },
        )
        assert created.status_code == 200, created.text
        parent_id = created.json()["data"]["asset_id"]
        launched = await client.post(
            f"{create}/{parent_id}/runs",
            json={
                "version": 1,
                "inputs": {
                    "packet": json.dumps(
                        {
                            "report": "Original report",
                            "send": False,
                            "record": {"count": 7},
                        }
                    ),
                    "source_notes": "Pinned source notes",
                },
            },
        )
        assert launched.status_code == 200, launched.text
        root = launched.json()["data"]["run_id"]
        url = f"/api/v1/workflow_runs/{root}"
        original = (await client.get(url)).json()["data"]
        (old_todo,) = original["todos"]
        old_answer = {
            "request_id": "original-approval",
            "action": "approve",
            "artifact_ref": old_todo["artifact_ref"],
        }
        answered = await client.post(
            f"{url}/todos/{old_todo['todo_id']}/answer", json=old_answer
        )
        assert answered.status_code == 200, answered.text
        original = answered.json()["data"]
        assert original["status"] == "completed"
        (child,) = original["scopes"]
        old_tasks = {task["node_id"]: task for task in child["tasks"]}
        assert old_tasks["deliver"]["status"] == "skipped"
        assert not external_ledger.deliveries
        decode = old_tasks["decode"]
        body = {
            "scope_run_id": child["run_id"],
            "task_run_id": decode["run_id"],
            "attempt_id": decode["state"]["attempt_id"],
        }
        single = await client.post(f"{url}/debug_forks/preview", json=body)
        assert single.status_code == 200, single.text
        assert single.json()["data"]["scope"] == "single"
        assert [node["id"] for node in single.json()["data"]["nodes"]] == ["decode"]
        # Simulate a historical read marker whose value was not retained.
        async with service.db.get_db() as session:
            saved_decode = await session.get(AgentWorkflowRun, decode["run_id"])
            saved_decode.snapshot = {**saved_decode.snapshot, "inputs": {}}
            await session.commit()
        body["scope"] = "downstream"
        preview = await client.post(f"{url}/debug_forks/preview", json=body)
        assert preview.status_code == 200, preview.text
        plan = preview.json()["data"]
        assert plan["scope"] == "downstream"
        (input_port,) = plan["inputs"]
        assert input_port["name"] == "text"
        assert input_port["source"] == "missing"
        assert "value" not in input_port
        assert {node["id"] for node in plan["nodes"]} == {
            "decode",
            "join",
            "compose",
            "review",
            "send-check",
            "deliver",
            "archive",
        }
        (boundary,) = plan["boundary"]
        assert boundary["id"] == "evidence"
        assert boundary["artifact"] == old_tasks["evidence"]["result_ref"]
        assert not external_ledger.deliveries
        wrong_scope = await client.post(
            f"{url}/debug_forks",
            json={
                **body,
                "scope": "single",
                "preview_token": plan["preview_token"],
                "request_id": "wrong-scope",
            },
        )
        assert wrong_scope.status_code == 409, wrong_scope.text
        replacement = json.dumps(
            {"report": "Edited report", "send": True, "record": {"count": 12}}
        )
        launched = await client.post(
            f"{url}/debug_forks",
            json={
                **body,
                "preview_token": plan["preview_token"],
                "request_id": "downstream-report",
                "replacement": {"name": "text", "value": replacement},
            },
        )
        assert launched.status_code == 200, launched.text
        fork_id = launched.json()["data"]["run_id"]
        fork_url = f"/api/v1/workflow_runs/{fork_id}"
        fork = (await client.get(fork_url)).json()["data"]
        (todo,) = fork["todos"]
        assert todo["status"] == "pending"
        assert todo["todo_id"] != old_todo["todo_id"]
        assert todo["artifact_ref"] != old_todo["artifact_ref"]
        tasks = {task["node_id"]: task for task in fork["tasks"]}
        assert "evidence" not in tasks
        assert tasks["decode"]["snapshot"]["inputs"]["text"] == replacement
        assert tasks["join"]["state"]["entered_from"] == ["decode", "evidence"]
        assert tasks["compose"]["result"] == {
            "report": "Edited report",
            "appendix": "Pinned source notes",
        }
        assert todo["artifact_ref"] == tasks["compose"]["result_ref"]
        answer_url = f"{fork_url}/todos/{todo['todo_id']}/answer"
        assert (await client.post(answer_url, json=old_answer)).status_code == 409
        assert not external_ledger.deliveries
        accepted = await client.post(
            answer_url,
            json={
                **old_answer,
                "request_id": "fork-approval",
                "artifact_ref": todo["artifact_ref"],
            },
        )
        assert accepted.status_code == 200, accepted.text
        fork = await completed_run(service, fork_id)
        tasks = {task["node_id"]: task for task in fork["tasks"]}
        assert tasks["archive"]["status"] == "skipped"
        delivery = tasks["deliver"]
        write = delivery["state"]["debug_write_preview"]
        assert write["status"] == "pending"
        assert write["target"] == "team/reports"
        assert write["parameters"]["record"] == {"count": 12}
        assert write["historical_receipts"] == []
        assert not external_ledger.deliveries

        confirmation_url = (
            f"/api/v1/workflow_runs/{delivery['run_id']}"
            f"/tool_calls/{write['record_id']}/execute"
        )
        confirmation = {"preview_token": write["preview_token"]}
        confirmed = await client.post(confirmation_url, json=confirmation)
        assert confirmed.status_code == 200, confirmed.text
        sent = await completed_run(service, delivery["run_id"])
        assert sent["status"] == "completed"
        (call,) = sent["tool_calls"]
        assert call["record_id"] == write["record_id"]
        assert call["status"] == "applied"
        assert call["raw_results"][0]["structuredContent"] == {"record": {"count": 13}}

        repeated = await client.post(confirmation_url, json=confirmation)
        assert repeated.status_code == 200, repeated.text
        assert (
            external_ledger.deliveries
            == external_ledger.effects
            == [
                {
                    "identity": write["record_id"],
                    "target": "team/reports",
                    "binding": "debug-scope",
                    "record": {"count": 12},
                }
            ]
        )
        assert not external_ledger.queries
        assert (await client.get(url)).json()["data"]["cursor"] == original["cursor"]
    finally:
        await service.shutdown()
        await service.db.engine.dispose()


@pytest.mark.asyncio
async def test_simulated_receipt_continues_only_the_previewed_write(
    tmp_path, publication_client, external_ledger, monkeypatch
):
    """Follow the simulated value through the Dashboard API and the independent ledger.

    Args:
        tmp_path: Disposable database and plugin installation.
        publication_client: Authenticated Dashboard API client.
        external_ledger: Independent recorder at the connector's delivery boundary.
        monkeypatch: Point the installed connector at the local recorder.
    """
    monkeypatch.setenv("ATN178_LEDGER_URL", external_ledger.url)
    monkeypatch.setenv("ATN178_LEDGER_KEY", "local-ledger-secret")
    service, _ = await fixture_service(tmp_path / "simulation", "debug-simulation")
    await configure_owner(service)
    client = publication_client
    client._transport.app.state.services.workflow_assets = service
    client._transport.app.state.services.role_assets = service.roles
    try:
        component = next(
            item
            for item in await service.list_components("alice")
            if item["reference"]["id"].endswith("/previewable")
        )
        assets = "/api/v1/workflow_assets"
        record_contract = component["output_contract"]
        capture_contract = {
            **record_contract,
            "properties": {
                **record_contract["properties"],
                "done": {"type": "boolean"},
            },
            "required": ["record", "done"],
        }
        round_contract = {
            **capture_contract,
            "properties": {**capture_contract["properties"], "next_done": {"type": "boolean"}},
            "required": ["record", "done", "next_done"],
        }
        child_inputs = {
            **record_contract,
            "properties": {
                **record_contract["properties"],
                "done": {"type": "boolean", "default": False},
                "finish": {"type": "boolean", "default": True},
            },
        }
        child = {
            "name": "Archive preparation",
            "port_contract_version": 2,
            "input_contract": child_inputs,
            "entry_task_ids": ["capture"],
            "result_task_id": "final",
            "edges": [
                {"source": "capture", "target": "round"},
                {"source": "round", "target": "final"},
                {
                    "source": "final",
                    "target": "unused",
                    "condition": {"field": "record.count", "value": 999},
                },
            ],
            "loops": [{
                "id": "archive-rounds",
                "name": "Archive rounds",
                "body": ["capture", "round"],
                "exit_task_id": "round",
                "exit_condition": {"field": "done", "value": True},
                "max_iterations": 2,
                "feedback": [
                    {"source": "round", "field": "record", "target": "capture", "input": "record"},
                    {"source": "round", "field": "next_done", "target": "capture", "input": "done"},
                ],
            }],
            "tasks": [
                {
                    "id": "capture",
                    "name": "Capture receipt",
                    "transform": "map_fields",
                    "input_contract": capture_contract,
                    "output_contract": capture_contract,
                    "inputs": {name: {"input": name} for name in ("record", "done")},
                },
                {
                    "id": "round",
                    "name": "Review receipt in this round",
                    "transform": "map_fields",
                    "input_contract": round_contract,
                    "output_contract": round_contract,
                    "inputs": {
                        **{name: {"task_id": "capture", "field": name} for name in ("record", "done")},
                        "next_done": {"input": "finish"},
                    },
                },
                {
                    "id": "final",
                    "name": "Return selected receipt",
                    "transform": "map_fields",
                    "input_contract": record_contract,
                    "output_contract": record_contract,
                    "inputs": {"record": {"task_id": "round", "field": "record"}},
                },
                {
                    "id": "unused",
                    "name": "Skip the unrelated archive condition",
                    "transform": "map_fields",
                    "input_contract": record_contract,
                    "output_contract": record_contract,
                    "inputs": {"record": {"task_id": "final", "field": "record"}},
                },
            ],
        }
        created_child = await client.post(assets, json={"definition": child})
        assert created_child.status_code == 200, created_child.text
        child_id = created_child.json()["data"]["asset_id"]
        graph = ledger_graph(component, "debug-simulation")
        graph["edges"] += [
            {"source": "result", "target": "prepare-archive"},
            {"source": "prepare-archive", "target": "archive"},
            {"source": "ledger", "target": "independent"},
            {"source": "independent", "target": "result"},
        ]
        delivery, result = graph["tasks"]
        graph["tasks"] += [
            {
                **delivery,
                "id": "archive",
                "name": "Archive the delivery receipt",
                "inputs": {"record": {"task_id": "prepare-archive", "field": "record"}},
            },
            {
                "id": "prepare-archive",
                "name": "Prepare archive in its fixed workflow",
                "workflow": {"asset_id": child_id, "version": 1},
                "input_contract": child_inputs,
                "output_contract": record_contract,
                "inputs": {"record": {"task_id": "ledger", "field": "record"}},
            },
            {
                **result,
                "id": "independent",
                "name": "Keep the original input",
                "inputs": {"count": {"input": "original_count"}},
            },
        ]
        graph["input_contract"] = {
            **graph["input_contract"],
            "properties": {
                **graph["input_contract"]["properties"],
                "original_count": {"type": "integer"},
            },
        }
        created = await client.post(
            "/api/v1/agent_drafts/workflow", json={"body": {"definition": graph}}
        )
        assert created.status_code == 200, created.text
        draft = created.json()["data"]
        asset = draft["asset_id"]
        published = await client.post(
            f"/api/v1/agent_drafts/workflow/{asset}/publish",
            json={"revision": draft["revision"]},
        )
        assert published.status_code == 200, published.text
        started = await client.post(
            f"{assets}/{asset}/runs",
            json={"version": 1, "inputs": {"record": {"count": 7}, "original_count": 3}},
        )
        assert started.status_code == 200, started.text
        source_id = started.json()["data"]["run_id"]
        source = await completed_run(service, source_id)
        assert source["status"] == "completed"
        original_deliveries = list(external_ledger.deliveries)
        assert len(original_deliveries) == 2
        source_task = next(task for task in source["tasks"] if task["node_id"] == "ledger")
        # An older real receipt can have no recorded executor ID.
        async with service.db.get_db() as session:
            stored = await session.get(
                AgentWorkflowToolCall, source_task["tool_calls"][0]["record_id"]
            )
            stored.started_execution_id = None
            await session.commit()
        source_url = f"/api/v1/workflow_runs/{source_id}"
        selection = {
            "scope_run_id": source_id,
            "task_run_id": source_task["run_id"],
            "attempt_id": source_task["state"]["attempt_id"],
            "scope": "downstream",
        }
        planned = await client.post(f"{source_url}/debug_forks/preview", json=selection)
        assert planned.status_code == 200, planned.text
        started = await client.post(
            f"{source_url}/debug_forks",
            json={
                **selection,
                "preview_token": planned.json()["data"]["preview_token"],
                "request_id": "simulated-delivery",
            },
        )
        assert started.status_code == 200, started.text
        fork_id = started.json()["data"]["run_id"]
        fork_url = f"/api/v1/workflow_runs/{fork_id}"
        fork = await completed_run(service, fork_id)
        task = next(item for item in fork["tasks"] if item["node_id"] == "ledger")
        preview = task["state"]["debug_write_preview"]
        assert preview["historical_receipts"][0]["record_id"] == source_task["tool_calls"][0]["record_id"]
        assert not source_task["state"].get("simulated_sources")
        record = preview["record_id"]
        token = preview["preview_token"]
        base = f"/api/v1/workflow_runs/{task['run_id']}/tool_calls/{record}"
        invalid = await client.post(
            f"{base}/simulate", json={"preview_token": token, "result": {"record": {"count": "bad"}}},
        )
        assert invalid.status_code == 422, invalid.text
        assert invalid.json()["data"]["fields"][0]["path"] == "result.record.count"
        forged = await client.post(
            f"{base}/simulate",
            json={
                "preview_token": token,
                "result": {"record": {"count": 81}, "files": [{"file_id": "fake"}]},
            },
        )
        assert forged.status_code == 422, forged.text
        assert forged.json()["data"]["fields"][0]["path"] == "result.files"
        receipt = {"preview_token": token, "result": {"record": {"count": 81}}}
        stale = await client.post(f"{base}/simulate", json={**receipt, "preview_token": "0" * 32})
        assert stale.status_code == 409, stale.text
        replies = await asyncio.gather(*(client.post(f"{base}/simulate", json=receipt) for _ in range(2)))
        assert [reply.status_code for reply in replies] == [200, 200], [reply.text for reply in replies]
        assert (await client.post(f"{base}/execute", json={"preview_token": token})).status_code == 409
        assert (await client.post(f"{base}/simulate", json={**receipt, "result": {"record": {"count": 82}}})).status_code == 409

        fork = await completed_run(service, fork_id)
        reopened = (await client.get(fork_url)).json()["data"]
        tasks = {task["node_id"]: task for task in reopened["tasks"]}
        delivery = tasks["ledger"]
        (call,) = delivery["tool_calls"]
        assert call["status"] == "applied"
        assert call["started_execution_id"] is None
        simulation = {
            "record_id": record,
            "task_run_id": delivery["run_id"],
            "attempt_id": delivery["state"]["attempt_id"],
        }
        assert delivery["state"]["simulated_sources"] == [simulation]
        assert any(event["kind"] == "tool.simulated" for event in delivery["events"])
        mapping = tasks["result"]
        assert mapping["result"] == {"count": 81}
        assert mapping["state"]["simulated_sources"] == [simulation]
        assert tasks["independent"]["result"] == {"count": 3}
        assert not tasks["independent"]["state"].get("simulated_sources")
        line = await client.get(
            f"{fork_url}/data_line",
            params={
                "scope_run_id": fork_id, "task_run_id": mapping["run_id"],
                "attempt_id": mapping["state"]["attempt_id"], "input_name": "count",
            },
        )
        assert line.status_code == 200, line.text
        assert line.json()["data"]["evidence"]["simulated_sources"] == [simulation]
        (child_scope,) = reopened["scopes"]
        _, second_round = child_scope["loops"][0]["iterations"]
        round_capture = next(task for task in second_round["tasks"] if task["node_id"] == "capture")
        for name, sources in (("record", [simulation]), ("done", None)):
            assert round_capture["snapshot"]["input_reads"][name].get("simulated_sources") == sources
            feedback_line = await client.get(
                f"{fork_url}/data_line",
                params={
                    "scope_run_id": child_scope["run_id"],
                    "task_run_id": round_capture["run_id"],
                    "input_name": name,
                },
            )
            assert feedback_line.status_code == 200, feedback_line.text
            assert feedback_line.json()["data"]["evidence"].get("simulated_sources") == sources
        unused = next(task for task in child_scope["tasks"] if task["node_id"] == "unused")
        assert unused["status"] == "skipped"
        assert not unused["state"].get("simulated_sources")
        unread = await client.get(
            f"{fork_url}/data_line",
            params={
                "scope_run_id": child_scope["run_id"],
                "task_run_id": unused["run_id"],
                "input_name": "record",
            },
        )
        assert unread.status_code == 200, unread.text
        assert unread.json()["data"]["read_status"] == "skipped"
        assert "evidence" not in unread.json()["data"]
        assert unread.json()["data"]["upstream"]["simulated_sources"] == [simulation]
        assert unread.json()["data"]["upstream"]["value"] == {"count": 81}
        assert tasks["prepare-archive"]["state"]["simulated_sources"] == [simulation]
        archive = tasks["archive"]
        second = archive["state"]["debug_write_preview"]
        assert second["parameters"]["record"] == {"count": 81}
        assert second["record_id"] != record
        assert not second.get("authorized_by")
        assert (await client.post(
            f"/api/v1/workflow_runs/{archive['run_id']}/tool_calls/{second['record_id']}/execute",
            json={"preview_token": token},
        )).status_code == 409
        replay = {
            "scope_run_id": fork_id,
            "task_run_id": mapping["run_id"],
            "attempt_id": mapping["state"]["attempt_id"],
        }
        for choice, mode, bindings, replacement in (
            ("same", "source", {}, None),
            ("mapped", "draft", {"count": {"source_port": "count"}}, None),
            ("replaced", "source", {}, {"name": "count", "value": 9}),
        ):
            replay_plan = {**replay, "definition_mode": mode, "bindings": bindings}
            if mode == "draft":
                replay_plan["draft_revision"] = draft["revision"]
            replay_preview = await client.post(f"{fork_url}/debug_forks/preview", json=replay_plan)
            assert replay_preview.status_code == 200, replay_preview.text
            replay_body = {
                **replay_plan,
                "preview_token": replay_preview.json()["data"]["preview_token"],
                "request_id": choice,
            }
            if replacement:
                replay_body["replacement"] = replacement
            replayed = await client.post(f"{fork_url}/debug_forks", json=replay_body)
            assert replayed.status_code == 200, replayed.text
            copied_root = replayed.json()["data"]["run_id"]
            (copied,) = (await client.get(f"/api/v1/workflow_runs/{copied_root}")).json()["data"]["tasks"]
            assert copied["result"] == {"count": 9 if replacement else 81}
            assert copied["state"].get("simulated_sources") == (None if replacement else [simulation])
        assert external_ledger.deliveries == original_deliveries
        assert not external_ledger.queries
        assert (await client.get(source_url)).json()["data"]["cursor"] == source["cursor"]
    finally:
        await service.shutdown()
        await service.db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "legacy", [False, True], ids=["native-read", "legacy-port-evidence"]
)
async def test_debug_fork_freezes_one_recorded_consumer_attempt(
    publication_client, workflow_env, legacy
):
    """Replay one pure node after a new asset version is published.

    Args:
        publication_client: Dashboard API backed by a disposable database.
        workflow_env: Storage for reproducing the pre-inspector snapshot layout.
        legacy: Remove the new markers and rely on the saved artifact and field.
    """
    client = publication_client
    text_contract = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    report_contract = {
        "type": "object",
        "properties": {"citation": {"type": "string"}},
        "required": ["citation"],
    }
    definition = {
        "name": "Citation workflow",
        "port_contract_version": 2,
        "input_contract": text_contract,
        "entry_task_ids": ["produce", "check", "format"],
        "result_task_id": "format",
        "tasks": [
            {
                "id": "produce",
                "name": "Produce",
                "transform": "parse_json",
                "inputs": {"text": {"input": "text"}},
                "output_contract": text_contract,
            },
            {
                "id": "check",
                "name": "Check citations",
                "transform": "parse_json",
                "inputs": {"text": {"task_id": "produce", "field": "text"}},
                "output_contract": report_contract,
            },
            {
                "id": "format",
                "name": "Format",
                "transform": "json_text",
                "inputs": {"value": {"task_id": "check", "field": "citation"}},
            },
        ],
    }
    published = await client.post(
        "/api/v1/workflow_assets", json={"definition": definition}
    )
    assert published.status_code == 200, published.text
    asset_id = published.json()["data"]["asset_id"]
    path = f"/api/v1/workflow_assets/{asset_id}"
    launched = await client.post(
        f"{path}/runs",
        json={"version": 1, "inputs": {"text": '{"text":"invalid JSON"}'}},
    )
    assert launched.status_code == 200, launched.text
    run = launched.json()["data"]
    root_id = run["run_id"]
    root_path = f"/api/v1/workflow_runs/{root_id}"
    baseline = (await client.get(root_path)).json()["data"]
    tasks = {item["node_id"]: item for item in baseline["tasks"]}
    failed = tasks["check"]
    assert failed["status"] == "failed"
    definition["tasks"][1]["name"] = "New checker"
    definition["tasks"][1]["output_contract"] = text_contract
    definition["tasks"][2]["inputs"]["value"]["field"] = "text"
    revision = await client.put(
        path, json={"definition": definition, "expected_version": 1}
    )
    assert revision.status_code == 200, revision.text
    assert revision.json()["data"]["version"] == 2

    identity = {
        "scope_run_id": root_id,
        "task_run_id": failed["run_id"],
        "attempt_id": failed["state"]["attempt_id"],
    }
    if legacy:
        async with workflow_env.db.get_db() as session:
            saved = await session.get(AgentWorkflowRun, failed["run_id"])
            assert saved.snapshot["port_sources"]["text"]["field"] == "text"
            legacy_snapshot = {
                key: value
                for key, value in saved.snapshot.items()
                if key != "input_reads"
            }
            saved.snapshot = {**legacy_snapshot, "port_sources": {}}
            await session.commit()
        unproven = await client.post(f"{root_path}/debug_forks/preview", json=identity)
        assert unproven.status_code == 200, unproven.text
        unproven_plan = unproven.json()["data"]
        assert unproven_plan["inputs"][0]["source"] == "missing"
        assert "original" not in unproven_plan["inputs"][0]
        filled = await client.post(
            f"{root_path}/debug_forks",
            json={
                **identity,
                "preview_token": unproven_plan["preview_token"],
                "request_id": "fill-unproven-text",
                "replacement": {"name": "text", "value": '{"citation":"Test"}'},
            },
        )
        assert filled.status_code == 200, filled.text
        async with workflow_env.db.get_db() as session:
            saved = await session.get(AgentWorkflowRun, failed["run_id"])
            saved.snapshot = legacy_snapshot
            await session.commit()
    retried = await client.post(
        f"/api/v1/workflow_runs/{failed['run_id']}/retry",
        json={
            "request_id": "retry-before-fork",
            "attempt_id": identity["attempt_id"],
            "input_policy": "original",
        },
    )
    assert retried.status_code == 200, retried.text
    baseline = (await client.get(root_path)).json()["data"]
    current = next(task for task in baseline["tasks"] if task["node_id"] == "check")
    assert current["state"]["attempt_id"] != identity["attempt_id"]
    url = f"{root_path}/debug_forks"
    overrides = client._transport.app.dependency_overrides
    overrides[require_dashboard_user] = lambda: "bob"
    assert (await client.post(f"{url}/preview", json=identity)).status_code == 404
    overrides[require_dashboard_user] = lambda: "alice"
    line = await client.get(
        f"{root_path}/data_line", params={**identity, "input_name": "text"}
    )
    assert line.status_code == 200, line.text
    assert line.json()["data"]["value"] == "invalid JSON"
    assert line.json()["data"]["debug_fork_supported"]
    response = await client.post(f"{url}/preview", json=identity)
    assert response.status_code == 200, response.text
    plan = response.json()["data"]
    assert plan["source"] == {
        **identity,
        "root_run_id": root_id,
        "asset_id": asset_id,
        "asset_version": 1,
        "workflow_name": "Citation workflow",
        "node_id": "check",
        "node_name": "Check citations",
        "iteration": None,
    }
    assert plan["original"]["status"] == "failed"
    assert "parse" in plan["original"]["reason"].lower()
    assert plan["inputs"][0]["original"] == "invalid JSON"
    assert plan["inputs"][0]["source"] == "recorded"
    assert plan["inputs"][0]["evidence"]["artifact"] == tasks["produce"]["result_ref"]
    assert (await client.get(root_path)).json()["data"]["cursor"] == baseline["cursor"]

    confirm = {**identity, "preview_token": plan["preview_token"]}
    original = await client.post(url, json={**confirm, "request_id": "original"})
    assert original.status_code == 200, original.text
    assert original.json()["data"]["status"] == "blocked"
    replacement = {
        **confirm,
        "request_id": "edited",
        "replacement": {"name": "text", "value": '{"citation":"Source A"}'},
    }
    invalid = await client.post(
        url, json={**replacement, "replacement": {"name": "text", "value": {}}}
    )
    assert invalid.status_code == 400, invalid.text
    stale = await client.post(url, json={**replacement, "preview_token": "0" * 64})
    assert stale.status_code == 409
    edited = await client.post(url, json=replacement)
    assert edited.status_code == 200, edited.text
    fork_id = edited.json()["data"]["run_id"]
    assert fork_id != root_id
    repeated = await client.post(url, json=replacement)
    assert repeated.json()["data"]["run_id"] == fork_id
    conflict = await client.post(
        url, json={**replacement, "replacement": {"name": "text", "value": "{}"}}
    )
    assert conflict.status_code == 409
    reopened = (await client.get(f"/api/v1/workflow_runs/{fork_id}")).json()["data"]
    assert reopened["status"] == "completed"
    (task,) = reopened["tasks"]
    assert task["result"] == {"citation": "Source A"}
    assert task["node_id"] == "check"
    assert reopened["scopes"] == []
    fork = reopened["snapshot"]["debug_fork"]
    assert fork["source"] == plan["source"]
    assert fork["original"] == plan["original"]
    assert fork["inputs"][0]["original"] == "invalid JSON"
    assert fork["inputs"][0]["value"] == '{"citation":"Source A"}'
    assert fork["inputs"][0]["changed"] is True
    assert (await client.get(root_path)).json()["data"]["tasks"] == baseline["tasks"]
    rerun = await client.post(
        f"/api/v1/workflow_runs/{fork_id}/rerun", json={"request_id": "full"}
    )
    assert rerun.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("component_name", "decision"),
    [
        ("previewable", "cancel"),
        ("record", "cancel"),
        ("previewable", "execute"),
        ("previewable", "unknown"),
    ],
)
async def test_debug_fork_requires_exact_approval_for_installed_external_write(
    tmp_path, publication_client, external_ledger, monkeypatch, component_name, decision
):
    """Confirm one new call independently from its source receipt.

    Args:
        tmp_path: Temporary database scope.
        publication_client: Authorized Dashboard API client.
        external_ledger: Independent recorder for connector calls.
        monkeypatch: Scope the fixture connector to its local ledger.
        component_name: Select whether the connector declares a previewable target.
        decision: Stop, execute, or lose and reconcile the execution receipt.
    """
    monkeypatch.setenv("ATN178_LEDGER_URL", external_ledger.url)
    monkeypatch.setenv("ATN178_LEDGER_KEY", "local-ledger-secret")
    service, plugin = await fixture_service(tmp_path / "ledger", "debug-fork-ledger")
    await configure_owner(service)
    client = publication_client
    app = client._transport.app
    app.state.services.workflow_assets = service
    try:
        component = next(
            item
            for item in await service.list_components("alice")
            if item["reference"]["id"].endswith(f"/{component_name}")
        )
        graph = ledger_graph(component, "debug-fork-ledger")
        created = await client.post(
            "/api/v1/workflow_assets", json={"definition": graph}
        )
        assert created.status_code == 200, created.text
        asset_id = created.json()["data"]["asset_id"]
        launched = await client.post(
            f"/api/v1/workflow_assets/{asset_id}/runs",
            json={"version": 1, "inputs": {"record": {"count": 7}}},
        )
        assert launched.status_code == 200, launched.text
        root_id = launched.json()["data"]["run_id"]
        run = await completed_run(service, root_id)
        task = next(
            item
            for item in run["tasks"]
            if item["snapshot"]["engine"] == "plugin_task_v2"
        )
        assert len(external_ledger.deliveries) == 1
        url = f"/api/v1/workflow_runs/{root_id}"
        body = {
            "scope_run_id": root_id,
            "task_run_id": task["run_id"],
            "attempt_id": task["state"]["attempt_id"],
        }
        line = await client.get(
            f"{url}/data_line", params={**body, "input_name": "record"}
        )
        assert line.status_code == 200, line.text
        assert line.json()["data"]["debug_fork_supported"]
        preview = await client.post(f"{url}/debug_forks/preview", json=body)
        assert preview.status_code == 200, preview.text
        direct = await client.post(
            f"{url}/debug_forks",
            json={
                **body,
                "preview_token": preview.json()["data"]["preview_token"],
                "request_id": "preview-write",
                "replacement": {"name": "record", "value": {"count": 12}},
            },
        )
        assert direct.status_code == 200, direct.text
        fork_id = direct.json()["data"]["run_id"]
        fork = await completed_run(service, fork_id)
        assert fork["status"] == "blocked"
        fork_url = f"/api/v1/workflow_runs/{fork_id}"
        read = (await client.get(fork_url)).json()["data"]
        (stopped,) = read["tasks"]
        write = stopped["state"]["debug_write_preview"]
        assert write["operation"] == component["reference"]["id"]
        assert write["parameters"]["record"] == {"count": 12}
        assert write["parameters"]["invocation_key"] == write["record_id"]
        assert write["target"] == (
            "team/reports" if component_name == "previewable" else None
        )
        assert write["status"] == (
            "pending" if component_name == "previewable" else "unavailable"
        )
        (receipt,) = write["historical_receipts"]
        assert receipt["status"] == "applied"
        assert receipt["results"][0]["structuredContent"] == {"record": {"count": 8}}
        assert len(external_ledger.deliveries) == 1
        assert not external_ledger.queries
        assert (await client.get(fork_url)).json()["data"]["tasks"][0]["state"][
            "debug_write_preview"
        ] == write
        task_url = f"/api/v1/workflow_runs/{stopped['run_id']}"
        assert (await client.post(f"{task_url}/recover")).status_code == 409
        confirm_url = f"{task_url}/tool_calls/{write['record_id']}/execute"
        stale = await client.post(confirm_url, json={"preview_token": "0" * 32})
        assert stale.status_code == 409, stale.text
        retry = {
            "request_id": "late-retry",
            "attempt_id": stopped["state"]["attempt_id"],
            "input_policy": "original",
        }
        assert (await client.post(f"{task_url}/retry", json=retry)).status_code == 409
        approval = {"preview_token": write["preview_token"] or "0" * 32}
        if decision == "cancel":
            cancelled = await client.post(
                f"{fork_url}/control", json={"request_id": "cancel", "action": "cancel"}
            )
            assert cancelled.status_code == 200, cancelled.text
            assert cancelled.json()["data"]["tasks"][0]["status"] == "cancelled"
            assert (await client.post(confirm_url, json=approval)).status_code == 409
            assert (
                await client.post(f"{task_url}/retry", json=retry)
            ).status_code == 409
        else:
            overrides = app.dependency_overrides
            overrides[require_dashboard_user] = lambda: "bob"
            unauthorized = await client.post(confirm_url, json=approval)
            overrides[require_dashboard_user] = lambda: "alice"
            assert unauthorized.status_code == 404
            plugin.activated = False
            revoked = await client.post(confirm_url, json=approval)
            plugin.activated = True
            assert revoked.status_code == 400, revoked.text
            assert len(external_ledger.deliveries) == 1

            if decision == "unknown":
                external_ledger.drop_receipt.set()
                external_ledger.query_unknown.set()
            replies = await asyncio.gather(
                client.post(confirm_url, json=approval),
                client.post(confirm_url, json=approval),
            )
            assert all(reply.status_code == 200 for reply in replies), [
                reply.text for reply in replies
            ]
            for _ in range(200):
                current = (await client.get(fork_url)).json()["data"]
                (executed,) = current["tasks"]
                (call,) = executed["tool_calls"]
                if (
                    call["status"] in {"applied", "unknown"}
                    and current["status"] != "running"
                ):
                    break
                await asyncio.sleep(0.02)
            assert len(external_ledger.deliveries) == 2
            assert external_ledger.deliveries[-1] == {
                "identity": write["record_id"],
                "target": "team/reports",
                "binding": "debug-fork-ledger",
                "record": {"count": 12},
            }
            assert call["started_execution_id"]
            assert call["record_id"] != receipt["record_id"]
            assert executed["state"]["debug_write_preview"]["authorized_by"] == "alice"
            assert (await client.post(confirm_url, json=approval)).status_code == 200
            if decision == "unknown":
                assert call["status"] == "unknown"
                assert not call["raw_results"]
                assert call["available_actions"] == ["reconcile"]
                simulated = await client.post(
                    f"{task_url}/tool_calls/{write['record_id']}/simulate",
                    json={**approval, "result": {"record": {"count": 8}}},
                )
                assert simulated.status_code == 409, simulated.text
                still_unknown = (await client.get(task_url)).json()["data"]["tool_calls"][0]
                assert still_unknown["status"] == "unknown"
                assert still_unknown["available_actions"] == ["reconcile"]
                resolve = f"{task_url}/tool_calls/{write['record_id']}/resolve"
                for action in ("retry", "confirm_result"):
                    fabricated = await client.post(
                        resolve,
                        json={
                            "request_id": action,
                            "action": action,
                            "evidence": "Source receipt",
                            "result": '{"record":{"count":8}}',
                        },
                    )
                    assert fabricated.status_code == 409, fabricated.text
                for request_id, query_unknown in (
                    ("ambiguous", True),
                    ("authoritative", False),
                ):
                    if not query_unknown:
                        external_ledger.query_unknown.clear()
                    queried = await client.post(
                        resolve,
                        json={
                            "request_id": request_id,
                            "action": "reconcile",
                            "evidence": "Query the loopback ledger",
                        },
                    )
                    assert queried.status_code == 200, queried.text
                    current = await completed_run(service, fork_id)
                    (call,) = current["tasks"][0]["tool_calls"]
                    assert call["recovery"]["decisions"][-1]["execution_id"]
                    if query_unknown:
                        assert call["status"] == "unknown"
                        assert not call["raw_results"]
                assert external_ledger.queries == [external_ledger.deliveries[-1]] * 2
            assert call["status"] == "applied"
            assert call["raw_results"][0]["structuredContent"] == {
                "record": {"count": 13}
            }
            assert len(external_ledger.deliveries) == 2
        consumer = next(item for item in run["tasks"] if item["node_id"] == "result")
        safe = {
            **body,
            "task_run_id": consumer["run_id"],
            "attempt_id": consumer["state"]["attempt_id"],
        }
        planned = await client.post(f"{url}/debug_forks/preview", json=safe)
        assert planned.status_code == 200, planned.text
        replayed = await client.post(
            f"{url}/debug_forks",
            json={
                **safe,
                "preview_token": planned.json()["data"]["preview_token"],
                "request_id": "replay-data-only",
            },
        )
        assert replayed.status_code == 200, replayed.text
        assert replayed.json()["data"]["status"] == "completed"
        assert len(external_ledger.deliveries) == (1 if decision == "cancel" else 2)
        assert (await client.get(url)).json()["data"]["cursor"] == run["cursor"]
    finally:
        await service.shutdown()
        await service.db.engine.dispose()


@pytest.mark.asyncio
async def test_debug_fork_replays_a_recorded_input_contract_failure(publication_client):
    """Use the scheduler to reproduce a known invalid original value.

    Args:
        publication_client: Dashboard API backed by a disposable database.
    """
    client = publication_client
    text = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    constrained = {**text, "properties": {"text": {"type": "string", "minLength": 2}}}
    asset = await client.post(
        "/api/v1/workflow_assets",
        json={
            "definition": {
                "name": "Check citation length",
                "port_contract_version": 2,
                "input_contract": text,
                "entry_task_ids": ["produce", "check"],
                "result_task_id": "check",
                "tasks": [
                    {
                        "id": "produce",
                        "name": "Produce",
                        "transform": "map_fields",
                        "input_contract": text,
                        "output_contract": text,
                        "inputs": {"text": {"input": "text"}},
                    },
                    {
                        "id": "check",
                        "name": "Check",
                        "transform": "map_fields",
                        "input_contract": constrained,
                        "output_contract": text,
                        "inputs": {"text": {"task_id": "produce", "field": "text"}},
                    },
                ],
            }
        },
    )
    assert asset.status_code == 200, asset.text
    asset_id = asset.json()["data"]["asset_id"]
    launched = await client.post(
        f"/api/v1/workflow_assets/{asset_id}/runs",
        json={"version": 1, "inputs": {"text": "x"}},
    )
    assert launched.status_code == 200, launched.text
    original = launched.json()["data"]
    failed = next(task for task in original["tasks"] if task["node_id"] == "check")
    assert failed["status"] == "failed"
    root_id = original["run_id"]
    url = f"/api/v1/workflow_runs/{root_id}/debug_forks"
    identity = {
        "scope_run_id": root_id,
        "task_run_id": failed["run_id"],
        "attempt_id": failed["state"]["attempt_id"],
    }
    preview = await client.post(f"{url}/preview", json=identity)
    assert preview.status_code == 200, preview.text
    started = await client.post(
        url,
        json={
            **identity,
            "request_id": "reproduce-invalid-value",
            "preview_token": preview.json()["data"]["preview_token"],
        },
    )
    assert started.status_code == 200, started.text
    fork_id = started.json()["data"]["run_id"]
    fork = (await client.get(f"/api/v1/workflow_runs/{fork_id}")).json()["data"]
    (task,) = fork["tasks"]
    assert task["status"] == "failed"
    assert task["state"]["reason"] == failed["state"]["reason"] == "Invalid port values"


def failed_child_definitions(child_id=None):
    """Return the child graph, or the parent that runs it in two rounds.

    Args:
        child_id: Published child to pin in the parent, if supplied.

    Returns:
        Workflow definition for the requested asset.
    """
    payload = {
        "type": "object",
        "properties": {"text": {"type": "string"}, "approved": {"type": "boolean"}},
        "required": ["text", "approved"],
    }
    if child_id is None:
        return {
            "name": "Citation review",
            "port_contract_version": 2,
            "input_contract": payload,
            "entry_task_ids": ["prepare", "check", "cross-check"],
            "result_task_id": "check",
            "tasks": [
                {
                    "id": "prepare",
                    "name": "Prepare citations",
                    "transform": "map_fields",
                    "input_contract": payload,
                    "output_contract": payload,
                    "inputs": {
                        "text": {"input": "text"},
                        "approved": {"input": "approved"},
                    },
                },
                *[
                    {
                        "id": node_id,
                        "name": name,
                        "transform": "parse_json",
                        "output_contract": payload,
                        "inputs": {"text": {"task_id": "prepare", "field": "text"}},
                    }
                    for node_id, name in (
                        ("check", "Check citations"),
                        ("cross-check", "Check bibliography"),
                    )
                ],
            ],
        }
    return {
        "name": "Document report",
        "port_contract_version": 2,
        "input_contract": payload,
        "entry_task_ids": ["review"],
        "result_task_id": "review",
        "tasks": [
            {
                "id": "review",
                "name": "Review citations",
                "workflow": {"asset_id": child_id, "version": 1},
                "input_contract": payload,
                "output_contract": payload,
                "inputs": {
                    "text": {"input": "text"},
                    "approved": {"input": "approved"},
                },
            }
        ],
        "loops": [
            {
                "id": "review-rounds",
                "name": "Review rounds",
                "body": ["review"],
                "exit_task_id": "review",
                "exit_condition": {"field": "approved", "value": True},
                "max_iterations": 2,
                "feedback": [
                    {
                        "source": "review",
                        "field": "text",
                        "target": "review",
                        "input": "text",
                    }
                ],
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("active", "status"), [(True, "completed"), (False, "skipped")]
)
async def test_downstream_debug_reads_control_boundary_without_an_artifact(
    publication_client, active, status
):
    """Replay an omitted condition's recorded decision without inventing an artifact.

    Args:
        publication_client: Dashboard API backed by a disposable database.
        active: Whether the boundary condition activates its edge.
        status: Expected status of the downstream result node.
    """
    client = publication_client
    definition = failed_child_definitions()
    definition["tasks"] = [
        {
            **definition["tasks"][1],
            "inputs": {"text": {"input": "text"}},
        },
        {
            "id": "gate",
            "name": "Historical gate",
            "control": {"kind": "condition", "value": True},
            "inputs": {"value": {"input": "approved"}},
        },
        {
            "id": "format",
            "name": "Format result",
            "transform": "json_text",
            "inputs": {"value": {"task_id": "check", "field": "text"}},
        },
    ]
    definition["entry_task_ids"] = ["check", "gate"]
    definition["result_task_id"] = "format"
    definition["edges"] = [
        {
            "source": "gate",
            "target": "format",
            "condition": {"field": "matched", "value": True},
        }
    ]
    path = "/api/v1/workflow_assets"
    created = await client.post(path, json={"definition": definition})
    assert created.status_code == 200, created.text
    asset_id = created.json()["data"]["asset_id"]
    launched = await client.post(
        f"{path}/{asset_id}/runs",
        json={
            "version": 1,
            "inputs": {
                "text": '{"text":"Original","approved":true}',
                "approved": active,
            },
        },
    )
    assert launched.status_code == 200, launched.text
    source = launched.json()["data"]
    by_node = {item["node_id"]: item for item in source["tasks"]}
    assert by_node["gate"]["result"] == {"matched": active}
    assert by_node["format"]["status"] == status
    task = by_node["check"]
    path = f"/api/v1/workflow_runs/{source['run_id']}/debug_forks"
    request = {
        "scope_run_id": source["run_id"],
        "task_run_id": task["run_id"],
        "attempt_id": task["state"]["attempt_id"],
        "scope": "downstream",
    }
    preview = await client.post(f"{path}/preview", json=request)
    assert preview.status_code == 200, preview.text
    plan = preview.json()["data"]
    assert plan["boundary"] == [
        {"id": "gate", "name": "Historical gate", "status": "completed"}
    ]
    launched = await client.post(
        path,
        json={
            **request,
            "request_id": "control-boundary",
            "preview_token": plan["preview_token"],
        },
    )
    assert launched.status_code == 200, launched.text
    fork = launched.json()["data"]
    reopened = (await client.get(f"/api/v1/workflow_runs/{fork['run_id']}")).json()[
        "data"
    ]
    tasks = {item["node_id"]: item for item in reopened["tasks"]}
    assert "gate" not in tasks
    assert tasks["format"]["status"] == status


@pytest.mark.asyncio
async def test_downstream_debug_requires_the_approved_artifact_producer_in_scope(
    publication_client,
):
    """A fork cannot open an approval for a rejected original artifact.

    Args:
        publication_client: Dashboard API backed by a disposable database.
    """
    client = publication_client
    definition = failed_child_definitions()
    definition["tasks"] = [
        definition["tasks"][0],
        {**definition["tasks"][1], "inputs": {"text": {"input": "text"}}},
        {
            "id": "review",
            "name": "Approve prepared report",
            "human": {
                "kind": "approval",
                "question": "Approve original report?",
                "artifact_task_id": "prepare",
                "artifact_version": 1,
            },
        },
    ]
    definition["entry_task_ids"] = ["prepare", "check"]
    definition["result_task_id"] = "prepare"
    definition["edges"] = [
        {"source": "prepare", "target": "review"},
        {"source": "check", "target": "review"},
    ]
    path = "/api/v1/workflow_assets"
    created = await client.post(path, json={"definition": definition})
    assert created.status_code == 200, created.text
    asset_id = created.json()["data"]["asset_id"]
    launched = await client.post(
        f"{path}/{asset_id}/runs",
        json={
            "version": 1,
            "inputs": {
                "text": '{"text":"Original","approved":true}',
                "approved": False,
            },
        },
    )
    assert launched.status_code == 200, launched.text
    source = launched.json()["data"]
    root_path = f"/api/v1/workflow_runs/{source['run_id']}"
    (todo,) = source["todos"]
    rejected = await client.post(
        f"{root_path}/todos/{todo['todo_id']}/answer",
        json={
            "request_id": "reject-original",
            "action": "reject",
            "artifact_ref": todo["artifact_ref"],
            "feedback": "Fix the report.",
        },
    )
    assert rejected.status_code == 200, rejected.text
    source = rejected.json()["data"]
    task = next(item for item in source["tasks"] if item["node_id"] == "check")
    request = {
        "scope_run_id": source["run_id"],
        "task_run_id": task["run_id"],
        "attempt_id": task["state"]["attempt_id"],
        "scope": "downstream",
    }
    preview = await client.post(f"{root_path}/debug_forks/preview", json=request)
    assert preview.status_code == 409, preview.text
    assert "artifact producer" in preview.json()["message"]
    assert "Approve prepared report" in preview.json()["message"]
    admission = await client.post(
        f"{root_path}/debug_forks",
        json={
            **request,
            "request_id": "try-reviewing-original",
            "preview_token": "0" * 64,
        },
    )
    assert admission.status_code == 409, admission.text
    assert "artifact producer" in admission.json()["message"]
    reopened = (await client.get(root_path)).json()["data"]
    assert reopened["todos"][0]["status"] == "rejected"
    assert reopened["cursor"] == source["cursor"]
    old_ref = todo["artifact_ref"]
    old_export = f"/api/v1/workflow_artifacts/{old_ref['artifact_id']}/versions/{old_ref['version']}/export"
    assert (await client.get(old_export)).status_code == 409

    producer = next(item for item in source["tasks"] if item["node_id"] == "prepare")
    request.update(
        task_run_id=producer["run_id"], attempt_id=producer["state"]["attempt_id"]
    )
    preview = await client.post(f"{root_path}/debug_forks/preview", json=request)
    assert preview.status_code == 200, preview.text
    new_run = await client.post(
        f"{root_path}/debug_forks",
        json={
            **request,
            "request_id": "review-new-artifact",
            "preview_token": preview.json()["data"]["preview_token"],
        },
    )
    assert new_run.status_code == 200, new_run.text
    fork_path = f"/api/v1/workflow_runs/{new_run.json()['data']['run_id']}"
    (fork_todo,) = (await client.get(fork_path)).json()["data"]["todos"]
    new_ref = fork_todo["artifact_ref"]
    assert new_ref != old_ref
    approved = await client.post(
        f"{fork_path}/todos/{fork_todo['todo_id']}/answer",
        json={
            "request_id": "approve-new",
            "action": "approve",
            "artifact_ref": new_ref,
        },
    )
    assert approved.status_code == 200, approved.text
    new_export = f"/api/v1/workflow_artifacts/{new_ref['artifact_id']}/versions/{new_ref['version']}/export"
    assert (await client.get(new_export)).status_code == 200
    assert (await client.get(old_export)).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_downstream_debug_preserves_the_selected_round_exit(
    publication_client, approved
):
    """Use recorded feedback and require the exit before releasing downstream work.

    Args:
        publication_client: Dashboard API backed by a disposable database.
        approved: Replacement decision for the replayed round's exit.
    """
    client = publication_client
    definition = failed_child_definitions()
    definition["tasks"] = [
        definition["tasks"][1],
        {
            **definition["tasks"][0],
            "id": "capture",
            "inputs": {
                "text": {"task_id": "check", "field": "text"},
                "approved": {"task_id": "check", "field": "approved"},
            },
        },
        {
            "id": "format",
            "name": "Format passed result",
            "transform": "json_text",
            "inputs": {"value": {"task_id": "capture", "field": "text"}},
        },
    ]
    definition["tasks"][0]["inputs"] = {"text": {"input": "text"}}
    definition["entry_task_ids"] = ["check"]
    definition["result_task_id"] = "format"
    definition["edges"] = [
        {"source": "check", "target": "capture"},
        {"source": "capture", "target": "format"},
    ]
    definition["loops"] = [
        {
            "id": "review-rounds",
            "name": "Review rounds",
            "body": ["check", "capture"],
            "exit_task_id": "capture",
            "exit_condition": {"field": "approved", "value": True},
            "max_iterations": 2,
            "feedback": [
                {"source": "capture", "field": "text", "target": node, "input": "text"}
                for node in ("check", "capture")
            ],
        }
    ]
    created = await client.post(
        "/api/v1/workflow_assets", json={"definition": definition}
    )
    assert created.status_code == 200, created.text
    asset_id = created.json()["data"]["asset_id"]
    second_packet = '{"text":"Verified","approved":true}'
    launched = await client.post(
        f"/api/v1/workflow_assets/{asset_id}/runs",
        json={
            "version": 1,
            "inputs": {
                "text": json.dumps({"text": second_packet, "approved": False}),
                "approved": True,
            },
        },
    )
    assert launched.status_code == 200, launched.text
    source = launched.json()["data"]
    assert source["status"] == "completed"
    _, second = source["loops"][0]["iterations"]
    task = next(item for item in second["tasks"] if item["node_id"] == "check")
    path = f"/api/v1/workflow_runs/{source['run_id']}/debug_forks"
    request = {
        "scope_run_id": source["run_id"],
        "task_run_id": task["run_id"],
        "attempt_id": task["state"]["attempt_id"],
        "scope": "downstream",
    }
    preview = await client.post(f"{path}/preview", json=request)
    assert preview.status_code == 200, preview.text
    launched = await client.post(
        path,
        json={
            **request,
            "request_id": "loop-exit-fork",
            "preview_token": preview.json()["data"]["preview_token"],
            "replacement": {
                "name": "text",
                "value": json.dumps({"text": "Edited packet", "approved": approved}),
            },
        },
    )
    assert launched.status_code == 200, launched.text
    fork = launched.json()["data"]
    assert fork["status"] == ("completed" if approved else "blocked")
    reopened = (await client.get(f"/api/v1/workflow_runs/{fork['run_id']}")).json()[
        "data"
    ]
    assert reopened["loops"][0]["status"] == ("passed" if approved else "exhausted")
    tasks = {item["node_id"]: item for item in reopened["tasks"]}
    assert tasks["check"]["status"] == "completed"
    assert tasks["capture"]["result"] == {"text": second_packet, "approved": approved}
    assert tasks["format"]["status"] == (
        "completed" if approved else "dependency_failed"
    )


@pytest.mark.asyncio
async def test_debug_runs_entire_legacy_fixed_child(publication_client):
    """Use the exact parent mapping when the original child has text ports.

    Args:
        publication_client: Dashboard API backed by a disposable database.
    """
    client = publication_client
    path = "/api/v1/workflow_assets"
    child = await client.post(
        path,
        json={
            "definition": {
                "name": "Legacy child",
                "required_inputs": ["value"],
                "result_task_id": "answer",
                "tasks": [
                    {
                        "id": "answer",
                        "name": "Collect source notes",
                        "human": {
                            "kind": "question",
                            "question": "What are the source notes?",
                        },
                    }
                ],
            }
        },
    )
    assert child.status_code == 200, child.text
    child_id = child.json()["data"]["asset_id"]
    parent = await client.post(
        path,
        json={
            "definition": {
                "name": "Legacy parent",
                "required_inputs": ["packet"],
                "result_task_id": "child",
                "tasks": [
                    {
                        "id": "child",
                        "name": "Call legacy child",
                        "workflow": {"asset_id": child_id, "version": 1},
                        "inputs": {"value": {"input": "packet"}},
                    }
                ],
            }
        },
    )
    assert parent.status_code == 200, parent.text
    parent_id = parent.json()["data"]["asset_id"]
    launched = await client.post(
        f"{path}/{parent_id}/runs",
        json={"version": 1, "inputs": {"packet": "Parent input"}},
    )
    assert launched.status_code == 200, launched.text
    source = launched.json()["data"]
    root_path = f"/api/v1/workflow_runs/{source['run_id']}"
    (original,) = source["todos"]
    answered = await client.post(
        f"{root_path}/todos/{original['todo_id']}/answer",
        json={
            "request_id": "original-notes",
            "action": "answer",
            "answer": "Source notes",
        },
    )
    assert answered.status_code == 200, answered.text
    source = answered.json()["data"]
    assert source["status"] == "completed"
    (task,) = source["tasks"]
    request = {
        "scope_run_id": source["run_id"],
        "task_run_id": task["run_id"],
        "attempt_id": task["state"]["attempt_id"],
        "scope": "child_workflow",
    }
    preview = await client.post(f"{root_path}/debug_forks/preview", json=request)
    assert preview.status_code == 200, preview.text
    plan = preview.json()["data"]
    assert plan["source"]["asset_version"] == 1
    assert plan["inputs"][0]["name"] == "value"
    assert plan["inputs"][0]["original"] == "Parent input"
    forked = await client.post(
        f"{root_path}/debug_forks",
        json={
            **request,
            "preview_token": plan["preview_token"],
            "request_id": "run-legacy-child",
        },
    )
    assert forked.status_code == 200, forked.text
    fork_id = forked.json()["data"]["run_id"]
    fork = (await client.get(f"/api/v1/workflow_runs/{fork_id}")).json()["data"]
    (todo,) = fork["todos"]
    assert todo["status"] == "pending"
    assert todo["todo_id"] != original["todo_id"]


@pytest.mark.asyncio
async def test_debug_fork_selects_failures_from_the_exact_fixed_call(
    publication_client,
    workflow_env,
):
    """Follow the failed second call and replay only the selected internal node.

    Args:
        publication_client: Dashboard API backed by a disposable database.
        workflow_env: Storage for simulating an incomplete historical read.
    """
    client = publication_client
    create_path = "/api/v1/workflow_assets"
    definitions = failed_child_definitions()
    child = await client.post(create_path, json={"definition": definitions})
    assert child.status_code == 200, child.text
    child_id = child.json()["data"]["asset_id"]
    parent = await client.post(
        create_path, json={"definition": failed_child_definitions(child_id)}
    )
    assert parent.status_code == 200, parent.text
    parent_id = parent.json()["data"]["asset_id"]
    started = await client.post(
        f"{create_path}/{parent_id}/runs",
        json={
            "version": 1,
            "inputs": {
                "text": '{"text":"bad citations","approved":false}',
                "approved": False,
            },
        },
    )
    assert started.status_code == 200, started.text
    root_id = started.json()["data"]["run_id"]
    path = f"/api/v1/workflow_runs/{root_id}"
    run = (await client.get(path)).json()["data"]
    first, second = [round_["tasks"][0] for round_ in run["loops"][0]["iterations"]]
    assert (first["status"], second["status"]) == ("completed", "blocked")
    child_scopes = {scope["run_id"]: scope for scope in run["scopes"]}
    first_scope = child_scopes[first["state"]["child_scope_run_id"]]
    failed_scope = child_scopes[second["state"]["child_scope_run_id"]]
    tasks = {task["node_id"]: task for task in failed_scope["tasks"]}
    failed = tasks["check"]
    retried = await client.post(
        f"/api/v1/workflow_runs/{failed['run_id']}/retry",
        json={
            "request_id": "retry-inner",
            "attempt_id": failed["state"]["attempt_id"],
            "input_policy": "original",
        },
    )
    assert retried.status_code == 200, retried.text
    async with workflow_env.db.get_db() as session:
        current = await session.get(AgentWorkflowRun, failed["run_id"])
        current.snapshot = {
            key: value
            for key, value in current.snapshot.items()
            if key not in {"input_reads", "port_sources"}
        }
        await session.commit()
    baseline = (await client.get(path)).json()["data"]
    call = {
        "scope_run_id": root_id,
        "task_run_id": second["run_id"],
        "attempt_id": second["state"]["attempt_id"],
    }
    failures_path = f"{path}/debug_forks/failed_child"
    unauthorized = client._transport.app.dependency_overrides
    unauthorized[require_dashboard_user] = lambda: "bob"
    assert (await client.get(failures_path, params=call)).status_code == 404
    unauthorized[require_dashboard_user] = lambda: "alice"
    wrong_round = await client.get(
        failures_path, params={**call, "task_run_id": first["run_id"]}
    )
    assert wrong_round.status_code == 404, wrong_round.text
    success = await client.get(
        failures_path,
        params={
            **call,
            "task_run_id": first["run_id"],
            "attempt_id": first["state"]["attempt_id"],
        },
    )
    assert success.status_code == 409, success.text
    response = await client.get(failures_path, params=call)
    assert response.status_code == 200, response.text
    candidates = response.json()["data"]
    assert len(candidates) == 3
    assert {item["scope_run_id"] for item in candidates} == {failed_scope["run_id"]}
    assert {item["node_name"] for item in candidates} == {
        "Check citations",
        "Check bibliography",
    }
    assert candidates[0]["node_name"] == "Check bibliography"
    assert candidates[1]["attempt_id"] == failed["state"]["attempt_id"]
    assert not {task["run_id"] for task in first_scope["tasks"]} & {
        item["task_run_id"] for item in candidates
    }
    selected = next(
        item
        for item in candidates
        if item["attempt_id"] == failed["state"]["attempt_id"]
    )
    attempt = {
        key: selected[key] for key in ("scope_run_id", "task_run_id", "attempt_id")
    }
    artifact = tasks["prepare"]["result_ref"]
    artifact_path = f"/api/v1/workflow_artifacts/{artifact['artifact_id']}/versions/{artifact['version']}"
    unauthorized[require_dashboard_user] = lambda: "bob"
    assert (
        await client.get(f"/api/v1/workflow_runs/{failed_scope['run_id']}")
    ).status_code == 404
    assert (await client.get(artifact_path)).status_code == 404
    assert (
        await client.post(f"{path}/debug_forks/preview", json=attempt)
    ).status_code == 404
    unauthorized[require_dashboard_user] = lambda: "alice"
    exact_artifact = await client.get(artifact_path)
    assert exact_artifact.status_code == 200, exact_artifact.text
    assert exact_artifact.json()["data"]["content"]["text"] == "bad citations"
    preview = await client.post(f"{path}/debug_forks/preview", json=attempt)
    assert preview.status_code == 200, preview.text
    plan = preview.json()["data"]
    assert plan["source"]["asset_version"] == 1
    assert plan["source"]["node_name"] == "Check citations"
    assert plan["source"]["parent_call"] == {
        **call,
        "node_name": "Review citations",
        "iteration": 2,
    }
    assert plan["inputs"][0]["original"] == "bad citations"
    assert plan["inputs"][0]["evidence"]["artifact"] == artifact
    assert (await client.get(path)).json()["data"]["cursor"] == baseline["cursor"]
    definitions["tasks"][1]["name"] = "Published replacement"
    revised = await client.put(
        f"{create_path}/{child_id}",
        json={"definition": definitions, "expected_version": 1},
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["data"]["version"] == 2

    original = await client.post(
        f"{path}/debug_forks",
        json={
            **attempt,
            "request_id": "repeat-recorded-failure",
            "preview_token": plan["preview_token"],
        },
    )
    assert original.status_code == 200, original.text
    original_fork_id = original.json()["data"]["run_id"]
    original_fork = (
        await client.get(f"/api/v1/workflow_runs/{original_fork_id}")
    ).json()["data"]
    (replayed_failure,) = original_fork["tasks"]
    assert (replayed_failure["node_id"], replayed_failure["status"]) == (
        "check",
        "failed",
    )
    assert replayed_failure["state"]["reason"] == failed["state"]["reason"]
    assert original_fork["snapshot"]["debug_fork"]["source"] == plan["source"]

    forked = await client.post(
        f"{path}/debug_forks",
        json={
            **attempt,
            "request_id": "fork-second-round",
            "preview_token": plan["preview_token"],
            "replacement": {
                "name": "text",
                "value": '{"text":"verified","approved":true}',
            },
        },
    )
    assert forked.status_code == 200, forked.text
    fork_id = forked.json()["data"]["run_id"]
    reopened = (await client.get(f"/api/v1/workflow_runs/{fork_id}")).json()["data"]
    assert reopened["snapshot"]["debug_fork"]["source"] == plan["source"]
    (node,) = reopened["tasks"]
    assert node["node_id"] == "check"
    assert node["result"] == {"text": "verified", "approved": True}
    assert reopened["scopes"] == []
    whole = {**call, "scope": "child_workflow"}
    child_preview = await client.post(f"{path}/debug_forks/preview", json=whole)
    assert child_preview.status_code == 200, child_preview.text
    child_plan = child_preview.json()["data"]
    assert child_plan["scope"] == "child_workflow"
    assert child_plan["source"]["asset_version"] == 1
    assert child_plan["source"]["iteration"] == 2
    assert {node["id"] for node in child_plan["nodes"]} == {
        "prepare",
        "check",
        "cross-check",
    }
    assert {port["name"]: port["original"] for port in child_plan["inputs"]} == {
        "text": "bad citations",
        "approved": False,
    }
    child_fork = await client.post(
        f"{path}/debug_forks",
        json={
            **whole,
            "request_id": "fork-entire-second-call",
            "preview_token": child_plan["preview_token"],
            "replacement": {
                "name": "text",
                "value": '{"text":"mapped and verified","approved":true}',
            },
        },
    )
    assert child_fork.status_code == 200, child_fork.text
    fork = (
        await client.get(f"/api/v1/workflow_runs/{child_fork.json()['data']['run_id']}")
    ).json()["data"]
    assert fork["status"] == "completed"
    assert fork["result"] == {"text": "mapped and verified", "approved": True}
    assert {task["snapshot"]["definition"]["name"] for task in fork["tasks"]} == {
        "Prepare citations",
        "Check citations",
        "Check bibliography",
    }
    after = (await client.get(path)).json()["data"]
    assert after["cursor"] == baseline["cursor"]
    assert after["scopes"] == baseline["scopes"]


@pytest.mark.asyncio
async def test_debug_fork_fills_missing_history_with_exact_file(
    publication_client, workflow_env, monkeypatch
):
    """Keep the report and admit only an authorized exact attachment.

    Args:
        publication_client: Dashboard API backed by a disposable database.
        workflow_env: Storage for a historical attempt with incomplete evidence.
        monkeypatch: Pause execution between admission and scheduling.
    """
    client = publication_client
    service = workflow_env.service
    ports = service.ports_factory("alice")
    original_draft = {"text": "First draft", "confidence": 1.0}
    draft_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "minLength": 2},
            "confidence": {"type": "number"},
        },
        "required": ["text", "confidence"],
    }

    async def collect(session_id, message_id, member_id):
        """Submit the fixed producer result and exact file through task tools.

        Args:
            session_id: Session with the attempt's registered tools.
            message_id: Message passed by the runner.
            member_id: Member passed by the runner.

        Returns:
            Fixed completion text and an empty trace.
        """
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        stored = json.loads(
            await tools["store_task_file"].call(
                None, filename="citations.pdf", content="Citation evidence"
            )
        )
        await tools["submit_task_result"].call(
            None,
            result_json=json.dumps(
                {"draft": original_draft, "files": [{"file_id": stored["file_id"]}]}
            ),
        )
        return "Done", []

    service.ports_factory = lambda _: replace(ports, collect=collect)
    artifact_contract = {
        "type": "object",
        "properties": {
            "draft": draft_schema,
            "files": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["draft", "files"],
    }
    contract = {
        "type": "object",
        "properties": {
            "draft": draft_schema,
            "pdf": {"type": "object", "format": "astrbot-file"},
            "report": {"type": "object", "format": "astrbot-artifact"},
        },
        "required": ["draft", "pdf", "report"],
    }
    definition = {
        "name": "Report review",
        "port_contract_version": 2,
        "input_contract": {"type": "object", "properties": {}},
        "entry_task_ids": ["produce", "check"],
        "result_task_id": "check",
        "tasks": [
            {
                "id": "produce",
                "name": "Prepare draft and citations",
                "member": "writer",
                "role_id": workflow_env.role["role_id"],
                "role_version": 1,
                "task": "Prepare draft and citations",
                "output_contract": artifact_contract,
            },
            {
                "id": "check",
                "name": "Check citations",
                "transform": "map_fields",
                "input_contract": contract,
                "output_contract": contract,
                "inputs": {
                    "draft": {"task_id": "produce", "field": "draft"},
                    "pdf": {"task_id": "produce", "field": "$file"},
                    "report": {"task_id": "produce", "field": "$artifact"},
                },
            },
        ],
    }
    created = await client.post(
        "/api/v1/workflow_assets", json={"definition": definition}
    )
    assert created.status_code == 200, created.text
    asset_id = created.json()["data"]["asset_id"]
    launched = await client.post(
        f"/api/v1/workflow_assets/{asset_id}/runs", json={"version": 1}
    )
    assert launched.status_code == 200, launched.text
    root = await completed_run(service, launched.json()["data"]["run_id"])
    tasks = {task["node_id"]: task for task in root["tasks"]}
    source, check = tasks["produce"], tasks["check"]
    ref = source["result_ref"]
    file = {**ref, "file_id": source["result"]["files"][0]["file_id"]}
    async with service.db.get_db() as session:
        row = await session.get(AgentWorkflowRun, check["run_id"])
        row.snapshot = {
            **row.snapshot,
            "input_reads": {
                name: evidence
                for name, evidence in row.snapshot["input_reads"].items()
                if name != "draft"
            },
            "inputs": {
                **row.snapshot["inputs"],
                "pdf": {"filename": "citations.pdf"},
            },
        }
        await session.commit()

    newer = await service.revise_artifact(
        "alice",
        ref["artifact_id"],
        {
            "request_id": "remove-attachment",
            "expected_version": 1,
            "content": {
                "draft": {**original_draft, "text": "Newer draft"},
                "files": [],
            },
        },
    )
    root_id = root["run_id"]
    url = f"/api/v1/workflow_runs/{root_id}/debug_forks"
    identity = {
        "scope_run_id": root_id,
        "task_run_id": check["run_id"],
        "attempt_id": check["state"]["attempt_id"],
    }
    preview = await client.post(f"{url}/preview", json=identity)
    assert preview.status_code == 200, preview.text
    plan = preview.json()["data"]
    inputs = {port["name"]: port for port in plan["inputs"]}
    line = await client.get(
        f"/api/v1/workflow_runs/{root_id}/data_line",
        params={**identity, "input_name": "draft"},
    )
    assert line.json()["data"]["value"] == original_draft
    assert inputs["draft"]["original"] == original_draft
    assert inputs["pdf"]["source"] == "missing"
    assert "original" not in inputs["pdf"]
    assert inputs["report"]["original"] == ref
    confirm = {
        **identity,
        "preview_token": plan["preview_token"],
        "request_id": "exact-file",
    }
    missing = await client.post(url, json=confirm)
    assert missing.status_code == 400, missing.text
    assert missing.json()["data"]["fields"][0]["path"] == "inputs.pdf"
    nonfinite = await client.post(
        url,
        headers={"Content-Type": "application/json"},
        content=json.dumps(
            {
                **confirm,
                "replacement": {
                    "name": "draft",
                    "value": {**original_draft, "confidence": "1e400"},
                },
            }
        ).replace('"1e400"', "1e400"),
    )
    assert nonfinite.status_code == 400, nonfinite.text
    assert "finite JSON numbers" in nonfinite.json()["message"]
    for name, value in (
        ("draft", {**original_draft, "text": "x"}),
        ("pdf", {**file, "version": newer["version"]}),
        ("pdf", {**file, "version": 999}),
        ("pdf", {**ref, "file_id": "wrong-file"}),
    ):
        invalid = await client.post(
            url, json={**confirm, "replacement": {"name": name, "value": value}}
        )
        assert invalid.status_code in {400, 404}, invalid.text
        diagnostic_path = "inputs.draft.text" if name == "draft" else "inputs.pdf"
        assert invalid.json()["data"]["fields"][0]["path"] == diagnostic_path
    started = await client.post(
        url,
        json={
            **confirm,
            "replacements": [
                {"name": "pdf", "value": file},
                {"name": "report", "value": newer},
            ],
        },
    )
    assert started.status_code == 200, started.text
    fork_id = started.json()["data"]["run_id"]
    await service.revise_artifact(
        "alice",
        ref["artifact_id"],
        {
            "request_id": "newer-again",
            "expected_version": newer["version"],
            "content": {
                "draft": {**original_draft, "text": "Third draft"},
                "files": [],
            },
        },
    )
    saved = (await client.get(f"/api/v1/workflow_runs/{fork_id}")).json()["data"]
    assert saved["result"] == {"draft": original_draft, "pdf": file, "report": newer}
    comparisons = {
        port["name"]: port for port in saved["snapshot"]["debug_fork"]["inputs"]
    }
    assert comparisons["draft"]["selection"] == "reused"
    assert comparisons["pdf"]["selection"] == "filled"
    assert comparisons["report"]["selection"] == "replaced"
    assert comparisons["report"]["original"] == ref

    async def delay_execution(username, run_id):
        """Leave an admitted fork pending until permissions change.

        Args:
            username: Owner passed to the scheduler.
            run_id: Pending fork passed to the scheduler.
        """

    with monkeypatch.context() as fixture:
        fixture.setattr(service, "advance_run", delay_execution)
        deferred = await client.post(
            url,
            json={
                **confirm,
                "request_id": "permission-at-execution",
                "replacements": [
                    {"name": "draft", "value": {**original_draft, "confidence": 1}},
                    {"name": "pdf", "value": file},
                    {
                        "name": "report",
                        "value": {
                            "version": ref["version"],
                            "artifact_id": ref["artifact_id"],
                        },
                    },
                ],
            },
        )
    assert deferred.status_code == 200, deferred.text
    deferred_id = deferred.json()["data"]["run_id"]

    pending = (await client.get(f"/api/v1/workflow_runs/{deferred_id}")).json()["data"]
    selections = {
        port["name"]: port["selection"]
        for port in pending["snapshot"]["debug_fork"]["inputs"]
    }
    assert selections == {"draft": "reused", "pdf": "filled", "report": "reused"}
    assert type(pending["snapshot"]["inputs"]["draft"]["confidence"]) is float

    # An exact historical value may become unreadable between preview and admission.
    async with service.db.get_db() as session:
        producer = await session.get(AgentWorkflowRun, source["run_id"])
        producer.owner_username = "bob"
        await session.commit()
    await service.advance_run("alice", deferred_id)
    blocked = (await client.get(f"/api/v1/workflow_runs/{deferred_id}")).json()["data"]
    (blocked_task,) = blocked["tasks"]
    assert blocked_task["status"] == "failed"
    assert blocked_task["state"]["reason"] == "Artifact version not found"
    assert blocked_task["result"] is None
    denied = await client.post(f"{url}/preview", json=identity)
    assert denied.status_code == 200, denied.text
    unavailable_plan = denied.json()["data"]
    report = next(
        port for port in unavailable_plan["inputs"] if port["name"] == "report"
    )
    assert report["source"] == "unavailable"
    assert "original" not in report and "evidence" not in report
    line = await client.get(
        f"/api/v1/workflow_runs/{root_id}/data_line",
        params={**identity, "input_name": "report"},
    )
    assert line.json()["data"]["read_status"] == "unavailable"
    assert "value" not in line.json()["data"] and "upstream" not in line.json()["data"]
    stale = await client.post(url, json={**confirm, "request_id": "lost-access"})
    assert stale.status_code == 409, stale.text
    rejected = await client.post(
        url,
        json={
            **confirm,
            "preview_token": unavailable_plan["preview_token"],
            "request_id": "foreign-file",
            "replacement": {"name": "pdf", "value": file},
        },
    )
    assert rejected.status_code == 404, rejected.text
    assert rejected.json()["message"] == "Artifact version not found"

    async with service.db.get_db() as session:
        row = await session.get(AgentWorkflowRun, check["run_id"])
        row.status = "skipped"
        await session.commit()
    skipped = await client.post(f"{url}/preview", json=identity)
    assert skipped.status_code == 409, skipped.text
    assert "skipped" in skipped.json()["message"]


@pytest.mark.asyncio
async def test_debug_fork_requires_confirmation_of_the_current_draft(
    publication_client,
):
    """Run a saved node configuration against the same historical input.

    Args:
        publication_client: Authenticated Dashboard routes with isolated storage.
    """
    client = publication_client
    text = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    definition = {
        "name": "Citation workflow",
        "port_contract_version": 2,
        "input_contract": text,
        "entry_task_ids": ["check"],
        "result_task_id": "check",
        "tasks": [
            {
                "id": "check",
                "name": "Check citations",
                "transform": "parse_json",
                "inputs": {"text": {"input": "text"}},
                "output_contract": {
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                    "minProperties": 2,
                    "required": ["count"],
                },
            }
        ],
    }
    created = await client.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": definition}}
    )
    assert created.status_code == 200, created.text
    draft = created.json()["data"]
    asset_id = draft["asset_id"]
    draft_path = f"/api/v1/agent_drafts/workflow/{asset_id}"
    published = await client.post(
        f"{draft_path}/publish", json={"revision": draft["revision"]}
    )
    assert published.status_code == 200, published.text
    launched = await client.post(
        f"/api/v1/workflow_assets/{asset_id}/runs",
        json={"version": 1, "inputs": {"text": '{"count":2}'}},
    )
    assert launched.status_code == 200, launched.text
    source = launched.json()["data"]
    (attempt,) = source["tasks"]
    assert attempt["status"] == "failed"
    fork_path = f"/api/v1/workflow_runs/{source['run_id']}/debug_forks"
    identity = {
        "scope_run_id": source["run_id"],
        "task_run_id": attempt["run_id"],
        "attempt_id": attempt["state"]["attempt_id"],
    }
    original = await client.post(f"{fork_path}/preview", json=identity)
    assert original.status_code == 200, original.text
    assert original.json()["data"]["source"]["asset_version"] == 1
    assert "definition" not in original.json()["data"]

    checker = definition["tasks"][0]
    checker["output_contract"]["minProperties"] = 1
    current = draft["revision"]
    saved = await client.put(
        draft_path,
        json={"body": {"definition": definition}, "expected_revision": current},
    )
    assert saved.status_code == 200, saved.text
    current = saved.json()["data"]["revision"]
    draft_request = {**identity, "definition_mode": "draft"}
    preview = await client.post(f"{fork_path}/preview", json=draft_request)
    assert preview.status_code == 200, preview.text
    plan = preview.json()["data"]
    assert plan["definition"]["draft_revision"] == current
    assert plan["definition"]["changes"] == [
        {"kind": "output_contract_changed", "name": "Check citations"}
    ]
    assert plan["ready"]
    assert plan["inputs"][0]["value"] == '{"count":2}'

    checker["name"] = "Latest checker"
    saved = await client.put(
        draft_path,
        json={"body": {"definition": definition}, "expected_revision": current},
    )
    assert saved.status_code == 200, saved.text
    new_revision = saved.json()["data"]["revision"]
    confirm = {
        **draft_request,
        "draft_revision": current,
        "preview_token": plan["preview_token"],
        "request_id": "draft",
    }
    stale = await client.post(fork_path, json=confirm)
    assert stale.status_code == 409, stale.text

    preview = await client.post(f"{fork_path}/preview", json=draft_request)
    assert preview.status_code == 200, preview.text
    plan = preview.json()["data"]
    confirm.update(draft_revision=new_revision, preview_token=plan["preview_token"])
    fork = await client.post(fork_path, json=confirm)
    assert fork.status_code == 200, fork.text
    fork_id = fork.json()["data"]["run_id"]
    reopened = (await client.get(f"/api/v1/workflow_runs/{fork_id}")).json()["data"]
    assert reopened["snapshot"]["debug_fork"]["definition"] == plan["definition"]
    assert reopened["snapshot"]["definition"]["tasks"][0]["name"] == "Latest checker"
    (result,) = reopened["tasks"]
    assert result["result"] == {"count": 2}

    # A new required port can explicitly reuse the former port. A type-changed
    # port must use a value that matches its draft contract.
    checker.update(
        transform="map_fields",
        input_contract={
            "type": "object",
            "properties": {"body": {"type": "string"}, "text": {"type": "integer"}},
            "required": ["body", "text"],
        },
        inputs={},
        output_contract={
            "type": "object",
            "properties": {"body": {"type": "string"}, "text": {"type": "integer"}},
            "required": ["body", "text"],
        },
    )
    saved = await client.put(
        draft_path,
        json={"body": {"definition": definition}, "expected_revision": new_revision},
    )
    assert saved.status_code == 200, saved.text
    latest = saved.json()["data"]["revision"]
    # Idempotent retries still reopen the originally admitted, frozen revision.
    repeated = await client.post(fork_path, json=confirm)
    assert repeated.json()["data"]["run_id"] == fork_id
    preview = await client.post(f"{fork_path}/preview", json=draft_request)
    assert preview.status_code == 200, preview.text
    plan = preview.json()["data"]
    assert not plan["ready"]
    assert {
        "kind": "input_added",
        "name": "body",
        "after": "string",
        "required": True,
    } in plan["definition"]["changes"]
    assert {
        "kind": "input_changed",
        "name": "text",
        "before": "string",
        "after": "integer",
    } in plan["definition"]["changes"]
    ports = {port["name"]: port for port in plan["inputs"]}
    assert ports["body"]["available_sources"] == ["text"]
    assert ports["text"]["available_sources"] == []
    assert ports["text"]["original"] == '{"count":2}'
    assert "value" not in ports["text"]
    assert {issue["path"] for issue in plan["issues"]} == {"inputs.body", "inputs.text"}
    blocked = await client.post(
        fork_path,
        json={
            **draft_request,
            "draft_revision": latest,
            "preview_token": plan["preview_token"],
            "request_id": "blocked",
        },
    )
    assert blocked.status_code == 409, blocked.text

    repaired = {
        **draft_request,
        "draft_revision": latest,
        "bindings": {"body": {"source_port": "text"}, "text": {"value": 7}},
    }
    preview = await client.post(f"{fork_path}/preview", json=repaired)
    assert preview.status_code == 200, preview.text
    plan = preview.json()["data"]
    assert plan["ready"], plan["issues"]
    planned = {port["name"]: port for port in plan["inputs"]}
    assert "evidence" in planned["body"]
    assert "original_evidence" not in planned["body"]
    assert "original_evidence" in planned["text"]
    assert "evidence" not in planned["text"]
    wrong_binding = {
        **repaired,
        "bindings": {**repaired["bindings"], "text": {"value": 8}},
    }
    changed = await client.post(
        fork_path,
        json={
            **wrong_binding,
            "preview_token": plan["preview_token"],
            "request_id": "repaired",
        },
    )
    assert changed.status_code == 409, changed.text
    fork = await client.post(
        fork_path,
        json={
            **repaired,
            "preview_token": plan["preview_token"],
            "request_id": "repaired",
        },
    )
    assert fork.status_code == 200, fork.text
    reopened = (
        await client.get(f"/api/v1/workflow_runs/{fork.json()['data']['run_id']}")
    ).json()["data"]
    (result,) = reopened["tasks"]
    assert result["result"] == {"body": '{"count":2}', "text": 7}
    mapped, provided = reopened["snapshot"]["debug_fork"]["inputs"]
    assert (mapped["source"], mapped["source_port"], mapped["value"]) == (
        "mapped",
        "text",
        '{"count":2}',
    )
    assert (provided["source"], provided["original"], provided["value"]) == (
        "test_value",
        '{"count":2}',
        7,
    )

    definition["tasks"] = []
    saved = await client.put(
        draft_path,
        json={"body": {"definition": definition}, "expected_revision": latest},
    )
    assert saved.status_code == 200, saved.text
    preview = await client.post(f"{fork_path}/preview", json=draft_request)
    plan = preview.json()["data"]
    assert not plan["ready"]
    assert plan["definition"]["changes"] == [
        {"kind": "node_removed", "name": "Check citations"}
    ]
    original_again = await client.post(f"{fork_path}/preview", json=identity)
    assert original_again.json()["data"] == original.json()["data"]


@pytest.mark.asyncio
async def test_debug_fork_draft_compares_json_defaults_by_type(publication_client):
    """Keep boolean and integer defaults distinct in the draft comparison.

    Args:
        publication_client: Authenticated workflow, draft, and fork routes.
    """
    client = publication_client
    inputs = {
        "type": "object",
        "properties": {
            "value": {"type": "boolean"},
            "setting": {"type": ["integer", "boolean"], "default": 0},
        },
        "required": ["value"],
    }
    node = {
        "id": "check",
        "name": "Check",
        "transform": "map_fields",
        "input_contract": inputs,
        "inputs": {"value": {"input": "value"}},
        "output_contract": inputs,
    }
    graph = {
        "name": "Typed defaults",
        "port_contract_version": 2,
        "input_contract": inputs,
        "entry_task_ids": ["check"],
        "result_task_id": "check",
        "tasks": [node],
    }
    created = await client.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": graph}}
    )
    assert created.status_code == 200, created.text
    draft = created.json()["data"]
    asset_id = draft["asset_id"]
    draft_path = f"/api/v1/agent_drafts/workflow/{asset_id}"
    published = await client.post(
        f"{draft_path}/publish", json={"revision": draft["revision"]}
    )
    assert published.status_code == 200, published.text
    launched = await client.post(
        f"/api/v1/workflow_assets/{asset_id}/runs",
        json={"version": 1, "inputs": {"value": True}},
    )
    assert launched.status_code == 200, launched.text
    run = launched.json()["data"]
    (attempt,) = run["tasks"]
    assert attempt["status"] == "completed"
    contracts = {
        **inputs,
        "properties": {
            "value": {"type": "integer", "default": 1},
            "setting": {"type": ["integer", "boolean"], "default": False},
        },
    }
    node.update(input_contract=contracts, output_contract=contracts, inputs={})
    saved = await client.put(
        draft_path,
        json={"body": {"definition": graph}, "expected_revision": draft["revision"]},
    )
    assert saved.status_code == 200, saved.text
    preview = await client.post(
        f"/api/v1/workflow_runs/{run['run_id']}/debug_forks/preview",
        json={
            "scope_run_id": run["run_id"],
            "task_run_id": attempt["run_id"],
            "attempt_id": attempt["state"]["attempt_id"],
            "definition_mode": "draft",
        },
    )
    assert preview.status_code == 200, preview.text
    plan = preview.json()["data"]
    assert plan["ready"], plan["issues"]
    defaulted = next(port for port in plan["inputs"] if port["name"] == "value")
    assert defaulted["source"] == "default"
    assert defaulted["original"] is True
    assert type(defaulted["value"]) is int and defaulted["value"] == 1
    assert defaulted["changed"] is True
    assert any(
        change["name"] == "setting" and change["kind"] == "input_changed"
        for change in plan["definition"]["changes"]
    )
