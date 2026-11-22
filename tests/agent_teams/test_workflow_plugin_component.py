"""Exercise installed plugin components through the workflow asset API."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from astrbot.api.star import WorkflowComponent
from astrbot.core.db.po import DashboardAccount
from astrbot.core.star.star import StarMetadata
from astrbot.dashboard.services.api_key_service import ApiKeyService
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets_api import (
    publication_client as publication_client,
)
from tests.agent_teams.test_workflow_assets_api import workflow_env as workflow_env
from tests.agent_teams.test_workflow_plugin_restart import (
    configure_owner,
    fixture_service,
)


@pytest.mark.asyncio
async def test_installed_component_is_pinned_and_passes_structured_values(
    workflow_env, publication_client
):
    service = workflow_env.service
    schema = {
        "type": "object",
        "properties": {
            "value": {"type": "object", "properties": {"count": {"type": "integer"}}}
        },
        "required": ["value"],
    }

    async def execute(context, inputs):
        return {"value": {"count": inputs["value"]["count"] + 1}}

    plugin = StarMetadata(name="Ledger", author="Test", activated=True)
    plugin.workflow_components = (
        WorkflowComponent(
            name="increment",
            version="1",
            label="Increment",
            input_contract=schema,
            output_contract=schema,
            execute=execute,
            effect="read_only",
        ),
    )
    service.roles.core_lifecycle = SimpleNamespace(
        star_context=SimpleNamespace(get_all_stars=lambda: [plugin]),
    )
    async with service.db.get_db() as session:
        session.add(
            DashboardAccount(
                account_id="alice",
                username="alice",
                password_hash="unused",
                role="admin",
            )
        )
        await session.commit()
    api = publication_client
    entries = (await api.get("/api/v1/workflow_components")).json()["data"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["reference"]["id"] == "test/ledger/increment"
    body = {
        "definition": {
            "name": "Count records",
            "port_contract_version": 2,
            "input_contract": schema,
            "result_task_id": "result",
            "edges": [{"source": "increment", "target": "result"}],
            "tasks": [
                {
                    "id": "increment",
                    "name": "Increment",
                    "transform": "plugin_component_v1",
                    "component": entry["reference"],
                    "input_contract": entry["input_contract"],
                    "output_contract": entry["output_contract"],
                    "inputs": {"value": {"input": "value"}},
                },
                {
                    "id": "result",
                    "name": "Read count",
                    "transform": "map_fields",
                    "input_contract": {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                        "required": ["count"],
                    },
                    "output_contract": {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                        "required": ["count"],
                    },
                    "inputs": {
                        "count": {"task_id": "increment", "field": "value.count"}
                    },
                },
            ],
        }
    }
    created = await api.post("/api/v1/agent_drafts/workflow", json={"body": body})
    assert created.status_code == 200, created.text
    asset_id = created.json()["data"]["asset_id"]
    runs = f"/api/v1/workflow_assets/{asset_id}"
    inputs = {"value": {"count": 4}}
    trial = await api.post(
        f"{runs}/trials",
        json={
            "kind": "workflow",
            "revision": 1,
            "mode": "full",
            "inputs": inputs,
        },
    )
    assert trial.status_code == 200, trial.text
    result = await completed_run(service, trial.json()["data"]["run_id"])
    assert result["result"] == {"count": 5}, result
    published = await api.post(
        f"/api/v1/agent_drafts/workflow/{asset_id}/publish", json={"revision": 1}
    )
    assert published.status_code == 200, published.text
    started = await api.post(f"{runs}/runs", json={"version": 1, "inputs": inputs})
    assert started.status_code == 200, started.text
    result = await completed_run(service, started.json()["data"]["run_id"])
    assert result["result"] == {"count": 5}, result

    key = await ApiKeyService(service.db).create_api_key(
        {"scopes": ["workflow"]}, created_by="alice"
    )
    refused = await api.put(f"{runs}/api_access/{key['key_id']}", json={"version": 1})
    assert refused.status_code == 403, refused.text

    plugin.activated = False
    blocked = await api.post(f"{runs}/runs", json={"version": 1, "inputs": inputs})
    assert blocked.status_code == 400, blocked.text
    assert "component" in blocked.text


@pytest.mark.asyncio
@pytest.mark.parametrize("semantic_input", [False, True])
async def test_component_requires_an_authorized_reference_port(
    tmp_path, semantic_input
):
    """A lookalike object grants no ability to return a protected reference."""
    service, plugin = await fixture_service(tmp_path, "source-kb")
    await configure_owner(service)
    try:
        plain = {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        }
        seed = await service.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Prepare source reference",
                    "port_contract_version": 2,
                    "result_task_id": "seed",
                    "input_contract": plain,
                    "tasks": [
                        {
                            "id": "seed",
                            "name": "Seed",
                            "transform": "map_fields",
                            "input_contract": plain,
                            "output_contract": plain,
                            "inputs": {"count": {"input": "count"}},
                        }
                    ],
                }
            },
        )
        run = await service.start_run(
            "alice", seed["asset_id"], {"version": 1, "inputs": {"count": 1}}
        )
        ref = (await completed_run(service, run["run_id"]))["tasks"][0]["result_ref"]
        semantic = {"type": "object", "format": "astrbot-artifact"}
        input_contract = {
            "type": "object",
            "properties": {"value": semantic if semantic_input else {"type": "object"}},
            "required": ["value"],
        }
        output_contract = {
            "type": "object",
            "properties": {"value": semantic},
            "required": ["value"],
        }

        async def pass_value(context, inputs):
            return inputs

        plugin.workflow_components = (
            replace(
                plugin.workflow_components[0],
                input_contract=input_contract,
                output_contract=output_contract,
                knowledge_base=False,
                effect="read_only",
                recovery=None,
                execute=pass_value,
            ),
        )
        entry = (await service.list_components("alice"))[0]
        asset = await service.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Test reference provenance",
                    "port_contract_version": 2,
                    "result_task_id": "plugin",
                    "input_contract": input_contract,
                    "tasks": [
                        {
                            "id": "plugin",
                            "name": "Plugin",
                            "transform": "plugin_component_v1",
                            "component": entry["reference"],
                            "input_contract": input_contract,
                            "output_contract": output_contract,
                            "inputs": {"value": {"input": "value"}},
                        }
                    ],
                }
            },
        )
        run = await service.start_run(
            "alice", asset["asset_id"], {"version": 1, "inputs": {"value": ref}}
        )
        result = await completed_run(service, run["run_id"])
        if semantic_input:
            assert result["result"] == {"value": ref}, result["tasks"][0]["state"]
        else:
            assert result["result"] is None
            assert result["tasks"][0]["state"]["issues"] == [
                {
                    "path": "definition.tasks.0.output_contract.value",
                    "code": "REFERENCE",
                    "message": "Select a reference bound to this attempt",
                }
            ]
    finally:
        await service.shutdown()
