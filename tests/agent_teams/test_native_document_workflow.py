"""Run native document nodes through the saved workflow and trial APIs."""

import asyncio
import json
from dataclasses import replace
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.chat import ChatCaller
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.po import AgentWorkflowChatAccess, DashboardAccount
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets_api import (
    publication_client as publication_client,
)
from tests.agent_teams.test_workflow_assets_api import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_native_document_reads_the_bound_version(
    workflow_env, publication_client, monkeypatch
):
    api = publication_client
    service = workflow_env.service
    source = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Document fixture",
                "tasks": [
                    {
                        "id": "source",
                        "name": "Source",
                        "member": "writer",
                        "role_id": workflow_env.role["role_id"],
                        "role_version": 1,
                        "task": "Generate a file.",
                        "output_contract": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "files": {"type": "array"},
                            },
                            "required": ["text"],
                        },
                    }
                ],
                "result_task_id": "source",
            }
        },
    )
    ports = service.ports_factory("alice")

    async def collect(session_id, message_id, member_id):
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        file = json.loads(
            await tools["store_task_file"].call(
                None, filename="evidence.md", content="# Original evidence"
            )
        )
        assert (
            await tools["submit_task_result"].call(
                None,
                result_json=json.dumps(
                    {"text": "Fixture", "files": [{"file_id": file["file_id"]}]}
                ),
            )
            == "Result accepted."
        )
        return "", []

    service.ports_factory = lambda _: replace(ports, collect=collect)
    run = await service.start_run("alice", source["asset_id"], {"version": 1})
    run = await completed_run(service, run["run_id"])
    assert run["status"] == "completed", run
    task = run["tasks"][0]
    file_ref = {**task["result_ref"], "file_id": task["result"]["files"][0]["file_id"]}
    await service.revise_artifact(
        "alice",
        file_ref["artifact_id"],
        {
            "request_id": "replacement",
            "expected_version": 1,
            "content": {"text": "New version", "files": []},
        },
    )
    response = await api.post(
        "/api/v1/agent_drafts/workflow",
        json={
            "body": {
                "definition": {
                    "name": "Inspect a document",
                    "port_contract_version": 2,
                    "input_contract": {
                        "type": "object",
                        "properties": {
                            "document": {"type": "object", "format": "astrbot-file"}
                        },
                        "required": ["document"],
                    },
                    "result_task_id": "read",
                    "tasks": [
                        {
                            "id": "read",
                            "name": "Read document",
                            "transform": "read_document_v1",
                            "inputs": {"file": {"input": "document"}},
                        }
                    ],
                }
            }
        },
    )
    assert response.status_code == 200, response.text
    asset_id = response.json()["data"]["asset_id"]
    draft = f"/api/v1/agent_drafts/workflow/{asset_id}"
    asset = f"/api/v1/workflow_assets/{asset_id}"
    for mode in ("full", "single"):
        payload = {
            "kind": "workflow",
            "revision": 1,
            "mode": mode,
            "inputs": {"document": file_ref},
        }
        if mode == "single":
            payload["node_id"] = "read"
        response = await api.post(f"{asset}/trials", json=payload)
        assert response.status_code == 200, response.text
        result = await completed_run(service, response.json()["data"]["run_id"])
        assert result["status"] == "completed", result
        assert result["tasks"][0]["snapshot"]["inputs"]["file"] == file_ref
        assert result["tasks"][0]["result"]["text"].strip() == "# Original evidence"

    bad = await api.post(
        f"{asset}/trials",
        json={
            "kind": "workflow",
            "revision": 1,
            "mode": "full",
            "inputs": {"document": {**file_ref, "version": 2}},
        },
    )
    assert bad.status_code >= 400, bad.text
    assert bad.json()["data"]["fields"][0]["path"] == "inputs.document"

    publication = await api.post(f"{draft}/publish", json={"revision": 1})
    assert publication.status_code == 200, publication.text
    response = await api.post(
        f"{asset}/runs", json={"version": 1, "inputs": {"document": file_ref}}
    )
    assert response.status_code == 200, response.text
    result = await completed_run(service, response.json()["data"]["run_id"])
    assert result["status"] == "completed", result
    assert result["result"]["filename"] == "evidence.md"
    refused = await api.put(
        f"{asset}/chat_access",
        json={
            "platform_id": "qq-test",
            "sender_id": "caller",
            "source": "qq-test:FriendMessage:caller",
            "profile_id": "default",
            "version": 1,
        },
    )
    assert refused.status_code == 403, refused.text
    refused = await api.put(f"{asset}/api_access/fixture-key", json={"version": 1})
    assert refused.status_code == 403, refused.text

    conversion_started = Event()
    release_conversion = Event()
    second_conversion_started = Event()
    calls = []

    async def parse(content, filename):
        calls.append(filename)
        if len(calls) == 1:
            conversion_started.set()
            assert release_conversion.wait(10)
        else:
            second_conversion_started.set()
        return SimpleNamespace(text=content.decode())

    monkeypatch.setattr(
        "astrbot.core.agent_teams.workflows.select_parser",
        AsyncMock(return_value=SimpleNamespace(parse=parse)),
    )
    inputs = {"version": 1, "inputs": {"document": file_ref}}
    first = await service.start_run("alice", asset_id, inputs)
    try:
        assert await asyncio.to_thread(conversion_started.wait, 5)
        cancelled = await service.control_run(
            "alice",
            first["run_id"],
            {
                "request_id": "cancel-document",
                "action": "cancel",
            },
        )
        assert cancelled["status"] == "cancelled"
        second = await service.start_run("alice", asset_id, inputs)
        assert not await asyncio.to_thread(second_conversion_started.wait, 0.2)
    finally:
        release_conversion.set()
    assert (await completed_run(service, second["run_id"]))["status"] == "completed"
    saved = await service.get_run("alice", first["run_id"])
    assert saved["status"] == "cancelled"
    assert saved["tasks"][0]["result_ref"] is None


@pytest.mark.asyncio
async def test_native_retrieval_pins_ids_and_shares_root_admission(
    workflow_env, publication_client
):
    api = publication_client
    service = workflow_env.service
    matches = [
        {
            "kb_id": "kb-public",
            "doc_id": "reference",
            "chunk_id": "chunk-1",
            "content": "The threshold is 42.",
        }
    ]
    manager = SimpleNamespace(
        get_kb=AsyncMock(
            side_effect=lambda kb_id: (
                SimpleNamespace(init_error=None) if kb_id == "kb-public" else None
            )
        ),
        retrieve=AsyncMock(
            return_value={"results": matches, "context_text": "Threshold = 42"}
        ),
    )
    workflow_env.roles.core_lifecycle = SimpleNamespace(
        kb_manager=manager,
        platform_manager=SimpleNamespace(platform_insts=[]),
        astrbot_config_mgr=SimpleNamespace(get_conf_info=lambda _: {"id": "default"}),
    )
    definition = {
        "name": "Search an existing knowledge base",
        "port_contract_version": 2,
        "input_contract": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "max_parallel": 1,
        "limits": {"max_steps": 1},
        "result_task_id": "search",
        "tasks": [
            {
                "id": "search",
                "name": "Search",
                "transform": "retrieve_knowledge_v1",
                "knowledge_base_id": "kb-public",
                "inputs": {"query": {"input": "query"}},
            },
            {
                "id": "second",
                "name": "Second search",
                "transform": "retrieve_knowledge_v1",
                "knowledge_base_id": "kb-public",
                "inputs": {"query": {"task_id": "search", "field": "text"}},
            },
        ],
        "edges": [{"source": "search", "target": "second"}],
    }
    response = await api.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": definition}}
    )
    assert response.status_code == 200, response.text
    asset_id = response.json()["data"]["asset_id"]
    asset = f"/api/v1/workflow_assets/{asset_id}"
    payload = {
        "kind": "workflow",
        "revision": 1,
        "mode": "full",
        "inputs": {"query": "threshold"},
    }
    blocked = await api.post(f"{asset}/trials", json=payload)
    assert blocked.status_code == 403, blocked.text
    assert (
        blocked.json()["data"]["fields"][0]["path"]
        == "definition.tasks.0.knowledge_base_id"
    )
    manager.retrieve.assert_not_awaited()

    async with workflow_env.db.get_db() as session:
        session.add(
            DashboardAccount(
                username="alice",
                legacy_owner="alice",
                role="admin",
                password_hash="fixture",
            )
        )
        await session.commit()

    response = await api.post(f"{asset}/trials", json=payload)
    assert response.status_code == 200, response.text
    result = await completed_run(service, response.json()["data"]["run_id"])
    assert [task["status"] for task in result["tasks"]] == ["completed", "blocked"], (
        result
    )
    assert result["tasks"][0]["result"] == {
        "matches": matches,
        "text": "Threshold = 42",
        "warnings": [],
    }
    assert result["tasks"][1]["state"]["limit_stop"]["limit"] == "max_steps"
    assert result["usage"]["tokens"]["unknown_calls"] == 1
    manager.retrieve.assert_awaited_once_with(
        query="threshold", kb_ids=["kb-public"], warnings=[]
    )

    partial = {
        "kind": "workflow",
        "revision": 1,
        "mode": "partial",
        "node_id": "second",
        "sources": {"search": {"artifact": result["tasks"][0]["result_ref"]}},
    }
    response = await api.post(f"{asset}/trials", json=partial)
    assert response.status_code == 200, response.text
    result = await completed_run(service, response.json()["data"]["run_id"])
    assert [task["node_id"] for task in result["tasks"]] == ["second"]
    assert result["tasks"][0]["result"]["matches"] == matches
    assert result["usage"]["tokens"]["unknown_calls"] == 1
    manager.retrieve.assert_awaited_with(
        query="Threshold = 42", kb_ids=["kb-public"], warnings=[]
    )

    published = await api.post(
        f"/api/v1/agent_drafts/workflow/{asset_id}/publish", json={"revision": 1}
    )
    assert published.status_code == 200, published.text
    caller = ChatCaller(
        platform_id="qq-main",
        sender_id="12345",
        source="qq-main:FriendMessage:12345",
        profile_id="default",
    )
    grant = {**caller.model_dump(), "version": 1}
    refused = await api.put(f"{asset}/chat_access", json=grant)
    assert refused.status_code == 403, refused.text
    assert "from the Dashboard" in refused.text
    # A preloaded fixed grant must also fail at the real platform launch boundary.
    async with workflow_env.db.get_db() as session:
        session.add(
            AgentWorkflowChatAccess(asset_id=asset_id, owner_username="alice", **grant)
        )
        await session.commit()
    with pytest.raises(RoleAssetError, match="from the Dashboard"):
        await service.chat.invoke(caller, asset_id, {"query": "threshold"})

    manager.retrieve.reset_mock()
    definition["limits"] = {"token_budget": 1000}
    budgeted = await service.save_asset("alice", {"definition": definition})
    run = await service.start_run(
        "alice",
        budgeted["asset_id"],
        {
            "version": 1,
            "inputs": {"query": "threshold"},
        },
    )
    blocked = await completed_run(service, run["run_id"])
    first = blocked["tasks"][0]
    assert first["status"] == "blocked", blocked
    assert first["state"]["limit_stop"]["limit"] == "token_budget"
    assert "does not report token use" in first["state"]["reason"]
    manager.retrieve.assert_not_awaited()

    async def no_matches(*, query, kb_ids, warnings):
        warnings.append("DENSE_RETRIEVAL_FAILED")
        return None

    manager.retrieve.side_effect = no_matches
    response = await api.post(f"{asset}/trials", json=payload)
    assert response.status_code == 200, response.text
    failed = await completed_run(service, response.json()["data"]["run_id"])
    first = failed["tasks"][0]
    assert first["status"] == "blocked", failed
    assert first["state"]["issues"][0]["code"] == "DENSE_RETRIEVAL_FAILED"

    async def degraded(*, query, kb_ids, warnings):
        warnings.append("RERANK_FAILED")
        return {"results": matches, "context_text": "Threshold = 42"}

    manager.retrieve.side_effect = degraded
    response = await api.post(f"{asset}/trials", json=payload)
    assert response.status_code == 200, response.text
    repaired = await completed_run(service, response.json()["data"]["run_id"])
    assert repaired["tasks"][0]["result"]["matches"] == matches
    assert repaired["tasks"][0]["result"]["warnings"] == ["RERANK_FAILED"]

    first_call = asyncio.Event()
    allow_completion = asyncio.Event()
    calls = []

    async def serial_retrieve(*, query, kb_ids, warnings):
        calls.append(query)
        if len(calls) == 1:
            first_call.set()
            await allow_completion.wait()
        return {"results": [], "context_text": ""}

    manager.retrieve.side_effect = serial_retrieve
    definition["limits"] = {"max_steps": 2}
    definition["edges"] = []
    definition["tasks"][1]["inputs"] = {"query": {"input": "query"}}
    independent = await service.save_asset("alice", {"definition": definition})
    run = await service.start_run(
        "alice",
        independent["asset_id"],
        {
            "version": 1,
            "inputs": {"query": "independent"},
        },
    )
    try:
        await asyncio.wait_for(first_call.wait(), timeout=5)
        waiting = await service.get_run("alice", run["run_id"])
        assert waiting["tasks"][1]["status"] == "pending", waiting
        assert waiting["tasks"][1]["state"]["wait_kind"] == "capacity"
        assert calls == ["independent"]
    finally:
        allow_completion.set()
    result = await completed_run(service, run["run_id"])
    assert result["status"] == "completed"
    assert calls == ["independent", "independent"]


@pytest.mark.asyncio
async def test_native_call_receipt_survives_restart_without_silent_replay(
    workflow_env, monkeypatch
):
    service = workflow_env.service
    started = asyncio.Event()
    calls = []

    async def retrieve(*, query, kb_ids, warnings):
        calls.append((query, kb_ids))
        if len(calls) == 1:
            started.set()
            await asyncio.Event().wait()
        return {"results": [], "context_text": ""}

    workflow_env.roles.core_lifecycle = SimpleNamespace(
        kb_manager=SimpleNamespace(
            get_kb=AsyncMock(return_value=SimpleNamespace(init_error=None)),
            retrieve=retrieve,
        )
    )
    async with workflow_env.db.get_db() as session:
        session.add(
            DashboardAccount(
                username="alice",
                legacy_owner="alice",
                role="admin",
                password_hash="fixture",
            )
        )
        await session.commit()

    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Native restart",
                "port_contract_version": 2,
                "input_contract": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                "limits": {"max_steps": 2},
                "result_task_id": "lookup",
                "tasks": [
                    {
                        "id": "lookup",
                        "name": "Look up an existing document",
                        "transform": "retrieve_knowledge_v1",
                        "knowledge_base_id": "local-kb",
                        "inputs": {"query": {"input": "query"}},
                    }
                ],
            }
        },
    )
    reserving = asyncio.Event()
    allow_reservation = asyncio.Event()
    paused_at_boundary = asyncio.Event()
    begin_usage = service.begin_usage

    async def reserve(*args, **kwargs):
        reserving.set()
        await allow_reservation.wait()
        try:
            return await begin_usage(*args, **kwargs)
        except RoleAssetError as exc:
            if any(issue["code"] == "PAUSED" for issue in exc.fields):
                paused_at_boundary.set()
            raise

    monkeypatch.setattr(service, "begin_usage", reserve)
    admission = await service.start_run(
        "alice",
        asset["asset_id"],
        {
            "version": 1,
            "inputs": {"query": "inspection"},
        },
    )
    try:
        await asyncio.wait_for(reserving.wait(), timeout=5)
        await service.control_run(
            "alice",
            admission["run_id"],
            {
                "request_id": "pause-before-kb",
                "action": "pause",
            },
        )
    finally:
        allow_reservation.set()
    await asyncio.wait_for(paused_at_boundary.wait(), timeout=5)
    assert calls == []
    await service.control_run(
        "alice",
        admission["run_id"],
        {
            "request_id": "resume-kb",
            "action": "continue",
        },
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    await service.shutdown()
    reopened = WorkflowService(workflow_env.db, workflow_env.roles)
    try:
        await reopened.boot_sweep()
        snapshot = await completed_run(reopened, admission["run_id"])
        stopped = snapshot["tasks"][0]
        assert stopped["status"] == "failed", snapshot
        assert snapshot["usage"]["steps_used"] == 1
        assert snapshot["usage"]["tokens"]["unknown_calls"] == 1
        assert calls == [("inspection", ["local-kb"])]
        await reopened.retry_task(
            "alice",
            stopped["run_id"],
            {
                "request_id": "retry-native",
                "attempt_id": stopped["state"]["attempt_id"],
                "input_policy": "original",
            },
        )
        result = await completed_run(reopened, admission["run_id"])
        assert result["status"] == "completed", result
        assert result["usage"]["steps_used"] == 2
        assert calls == [("inspection", ["local-kb"]), ("inspection", ["local-kb"])]
    finally:
        await reopened.shutdown()
