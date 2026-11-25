"""Publication boundaries and read costs observed through the workbench."""

import json
from pathlib import Path

import pytest
from sqlalchemy import event, text

from astrbot.core.agent_teams.workflows import WorkflowSaveRequest
from astrbot.core.db.po import AgentWorkflowVersion
from tests.agent_teams.test_workflow_assets_api import (
    publication_client as publication_client,
)
from tests.agent_teams.test_workflow_assets_api import workflow_env as workflow_env


def contract(field):
    """Create a closed single-port object for the graph fixtures.

    Args:
        field: Schema of its required value.

    Returns:
        The native input or output contract.
    """
    return {"type": "object", "properties": {"value": field}, "required": ["value"]}


def mapping(name, source, schema):
    """Build a field-mapping task with a single data dependency.

    Args:
        name: Node identity.
        source: Source binding for the value port.
        schema: Declared input and output contract.

    Returns:
        The native task definition.
    """
    return {
        "id": name,
        "name": name,
        "transform": "map_fields",
        "inputs": {"value": source},
        "input_contract": schema,
        "output_contract": schema,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    json.loads(
        (Path(__file__).parent / "fixtures/port_domain_cases.json").read_text(
            encoding="utf-8"
        )
    ),
    ids=lambda case: case["name"],
)
async def test_finite_domains_at_publication(publication_client, case):
    client = publication_client
    graph = {
        "name": "Domain",
        "port_contract_version": 2,
        "entry_task_ids": ["copy"],
        "input_contract": contract(case["source"]),
        "result_task_id": "copy",
        "tasks": [mapping("copy", {"input": "value"}, contract(case["target"]))],
    }
    draft = (
        await client.post(
            "/api/v1/agent_drafts/workflow", json={"body": {"definition": graph}}
        )
    ).json()["data"]
    response = await client.post(
        f"/api/v1/agent_drafts/workflow/{draft['asset_id']}/publish",
        json={"revision": draft["revision"]},
    )
    assert response.status_code == (200 if case["compatible"] else 400), response.text
    if not case["compatible"]:
        assert any(
            f["path"] == "definition.tasks.0.inputs.value"
            and f["code"] == "INCOMPATIBLE"
            for f in response.json()["data"]["fields"]
        )


@pytest.mark.asyncio
async def test_contradictory_control_and_data_paths(publication_client, workflow_env):
    client = publication_client
    value = contract({"type": "string"})
    graph = {
        "name": "Branches",
        "port_contract_version": 2,
        "entry_task_ids": ["gate"],
        "result_task_id": "delivery",
        "input_contract": {
            "type": "object",
            "properties": {"flag": {"type": "boolean"}, "value": {"type": "string"}},
            "required": ["flag", "value"],
        },
        "tasks": [
            {
                "id": "gate",
                "name": "Gate",
                "control": {"kind": "condition", "value": True},
                "inputs": {"value": {"input": "flag"}},
            },
            mapping("left", {"input": "value"}, value),
            mapping("right", {"input": "value"}, value),
            mapping("delivery", {"task_id": "right", "field": "value"}, value),
        ],
        "edges": [
            {
                "source": "gate",
                "target": name,
                "condition": {"field": "matched", "value": outcome},
            }
            for name, outcome in [("left", True), ("right", False)]
        ]
        + [{"source": "left", "target": "delivery"}],
    }
    draft = (
        await client.post(
            "/api/v1/agent_drafts/workflow", json={"body": {"definition": graph}}
        )
    ).json()["data"]
    publish = await client.post(
        f"/api/v1/agent_drafts/workflow/{draft['asset_id']}/publish",
        json={"revision": draft["revision"]},
    )
    assert publish.status_code == 400
    assert any(
        f["code"] == "UNREACHABLE_DATA"
        and f["path"] == "definition.tasks.3.inputs.value"
        for f in publish.json()["data"]["fields"]
    )
    trial = await client.post(
        f"/api/v1/workflow_assets/{draft['asset_id']}/trials",
        json={
            "revision": draft["revision"],
            "kind": "workflow",
            "mode": "full",
            "inputs": {"flag": True, "value": "x"},
        },
    )
    assert trial.status_code == 400
    assert trial.json()["data"]["fields"] == publish.json()["data"]["fields"]
    # A pre-upgrade fixed version keeps its runtime diagnosis.
    async with workflow_env.db.get_db() as session, session.begin():
        session.add(
            AgentWorkflowVersion(
                owner_username="alice",
                asset_id="old-branch",
                version=1,
                definition=WorkflowSaveRequest.model_validate(
                    {"definition": graph}
                ).definition.model_dump(mode="json"),
            )
        )
    previous = await client.post(
        "/api/v1/workflow_assets/old-branch/runs",
        json={"version": 1, "inputs": {"flag": True, "value": "x"}},
    )
    assert (
        previous.status_code == 200 and previous.json()["data"]["status"] == "blocked"
    )
    graph["edges"].append({"source": "right", "target": "delivery"})
    valid = await client.post("/api/v1/workflow_assets", json={"definition": graph})
    assert valid.status_code == 200, valid.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "conditions",
    [
        [("lt", 0, "producer"), ("ge", 0, "consumer")],
        [("ne", False, "producer"), ("ne", True, "consumer")],
        [("eq", "a", "producer"), ("ne", "a", "consumer")],
        [
            ("eq", "a", "producer"),
            ("eq", "b", "producer"),
            ("eq", "c", "consumer"),
            ("eq", "d", "consumer"),
        ],
    ],
)
async def test_exclusive_alternative_paths(publication_client, conditions):
    first_value = conditions[0][1]
    kind = {bool: "boolean", int: "integer", str: "string"}[type(first_value)]
    schema = contract({"type": kind})
    source = {"input": "value"}
    nodes = [
        mapping("gate", source, schema),
        mapping("producer", source, schema),
        mapping("consumer", {"task_id": "producer", "field": "value"}, schema),
    ]
    edges = []
    for i, (operator, value, side) in enumerate(conditions):
        name = f"path{i}"
        nodes.append(mapping(name, source, schema))
        edges.extend(
            [
                {
                    "source": "gate",
                    "target": name,
                    "condition": {
                        "operator": operator,
                        "field": "value",
                        "value": value,
                    },
                },
                {"source": name, "target": side},
            ]
        )
    graph = {
        "name": "Alternatives",
        "port_contract_version": 2,
        "entry_task_ids": ["gate"],
        "input_contract": schema,
        "result_task_id": "consumer",
        "tasks": nodes,
        "edges": edges,
    }
    response = await publication_client.post(
        "/api/v1/workflow_assets", json={"definition": graph}
    )
    assert response.status_code == 400
    assert (
        response.json()["data"]["fields"][0]["path"]
        == "definition.tasks.2.inputs.value"
    )
    graph["edges"].append({"source": "path0", "target": "consumer"})
    response = await publication_client.post(
        "/api/v1/workflow_assets", json={"definition": graph}
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_batch_cards_isolate_bad_assets_and_bound_query_count(workflow_env):
    env = workflow_env
    sample = (await env.service.list_assets("alice", include_example=True))["assets"]
    example = next(row for row in sample if row["is_builtin_example"])
    for i in range(4):
        asset = await env.service.save_asset(
            "alice", {"definition": {**example["definition"], "name": f"Release {i}"}}
        )
        await env.service.start_run(
            "alice",
            asset["asset_id"],
            {"version": 1, "inputs": {"project": "中文摘要"}},
        )
    async with env.db.get_db() as session, session.begin():
        session.add(
            AgentWorkflowVersion(
                asset_id="broken",
                owner_username="alice",
                version=1,
                definition={"name": "Old"},
            )
        )
        session.add(
            AgentWorkflowVersion(
                asset_id="broken-json", owner_username="alice", version=1, definition={}
            )
        )
        await session.flush()
        await session.execute(
            text(
                "UPDATE agent_workflow_versions SET definition = :body WHERE asset_id = 'broken-json'"
            ),
            {"body": "{"},
        )
    queries = []

    def observe(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(env.db.engine.sync_engine, "before_cursor_execute", observe)
    try:
        cards = await env.service.list_assets("alice", summary_only=True)
        assert len(queries) == 1
        assert cards["unavailable_assets"] == [
            {"asset_id": "broken", "version": 1, "code": "UNSUPPORTED_FORMAT"},
            {"asset_id": "broken-json", "version": 1, "code": "UNSUPPORTED_FORMAT"},
        ]
        assert len(cards["assets"]) == len(sample) + 4 and all(
            "definition" not in row for row in cards["assets"]
        )
        assert (await env.service.list_assets("bob", summary_only=True)) == {
            "assets": []
        }
        queries.clear()
        runs = (await env.service.list_runs("alice", summary_only=True))["runs"]
        assert len(runs) == 4 and len(queries) == 3
        queries.clear()
        picker = (
            await env.service.list_runs(
                "alice", summary_only=True, include_artifacts=True
            )
        )["runs"]
        assert len(queries) == 3
        for run, with_artifacts in zip(runs, picker, strict=True):
            original = await env.service.get_run(
                "alice", run["run_id"], include_details=False
            )
            assert original["progress"] == run["progress"]
            assert "artifacts" not in run
            assert run["artifact_count"] == len(original["artifacts"]) > 0
            assert original["artifacts"] == with_artifacts["artifacts"]
            assert "中文摘要" in with_artifacts["artifacts"][0]["summary"]
        assert not (await env.service.list_runs("bob", summary_only=True))["runs"]
    finally:
        event.remove(env.db.engine.sync_engine, "before_cursor_execute", observe)
