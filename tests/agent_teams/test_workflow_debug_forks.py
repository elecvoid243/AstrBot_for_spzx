"""Verify the original-definition debug fork through the Dashboard API."""

import json
from dataclasses import replace

import pytest

from astrbot.core.agent_team_tools import AgentTeamToolRegistry
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
