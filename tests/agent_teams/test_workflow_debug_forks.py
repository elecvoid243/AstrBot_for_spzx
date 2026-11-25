"""Verify the original-definition debug fork through the Dashboard API."""

import pytest

from astrbot.core.db.po import AgentWorkflowRun
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
        assert unproven.status_code == 409, unproven.text
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
async def test_debug_fork_refuses_installed_external_write(
    tmp_path, publication_client, external_ledger, monkeypatch
):
    """A direct Dashboard request cannot invoke the original connector again.

    Args:
        tmp_path: Temporary database scope.
        publication_client: Authorized Dashboard API client.
        external_ledger: Independent recorder for connector calls.
        monkeypatch: Scope the fixture connector to its local ledger.
    """
    monkeypatch.setenv("ATN178_LEDGER_URL", external_ledger.url)
    monkeypatch.setenv("ATN178_LEDGER_KEY", "local-ledger-secret")
    service, _ = await fixture_service(tmp_path / "ledger", "debug-fork-ledger")
    await configure_owner(service)
    client = publication_client
    app = client._transport.app
    app.state.services.workflow_assets = service
    try:
        component = next(
            item
            for item in await service.list_components("alice")
            if item["reference"]["id"].endswith("/record")
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
        assert not line.json()["data"]["debug_fork_supported"]
        preview = await client.post(f"{url}/debug_forks/preview", json=body)
        direct = await client.post(
            f"{url}/debug_forks",
            json={**body, "preview_token": "0" * 64, "request_id": "direct"},
        )
        for response in (preview, direct):
            assert response.status_code == 409, response.text
            assert "external write preview" in response.json()["message"]
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
        assert len(external_ledger.deliveries) == 1
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
