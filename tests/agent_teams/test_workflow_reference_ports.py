"""Exact version and managed file propagation over the workflow API."""

import json
from dataclasses import replace

import pytest

from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets_api import (
    publication_client as publication_client,
)
from tests.agent_teams.test_workflow_assets_api import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_typed_ports_freeze_exact_history_and_managed_files(
    workflow_env, publication_client
):
    service = workflow_env.service
    ports = service.ports_factory("alice")
    message = {"text": "Selected message"}
    message_schema = {"type": "object", "format": "astrbot-message"}
    artifact_schema = {"type": "object", "format": "astrbot-artifact"}
    file_schema = {"type": "object", "format": "astrbot-file"}
    source_contract = {
        "type": "object",
        "properties": {
            "message": message_schema,
            "private": True,
            "files": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["message", "private", "files"],
    }
    selected = {
        "message": message_schema,
        "report": artifact_schema,
        "attachment": file_schema,
    }
    selection_contract = {
        "type": "object",
        "properties": selected,
        "required": list(selected),
    }
    producer = {
        "id": "producer",
        "name": "Producer",
        "member": "writer",
        "role_id": workflow_env.role["role_id"],
        "role_version": 1,
        "task": "Produce a report",
        "output_contract": source_contract,
    }
    definition = {
        "name": "Selected evidence",
        "port_contract_version": 2,
        "input_contract": {"type": "object", "properties": {}},
        "result_task_id": "map",
        "tasks": [
            producer,
            {
                "id": "map",
                "name": "Map",
                "transform": "map_fields",
                "input_contract": selection_contract,
                "output_contract": selection_contract,
                "inputs": {
                    "message": {"task_id": "producer", "field": "message"},
                    "report": {"task_id": "producer", "field": "$artifact"},
                    "attachment": {"task_id": "producer", "field": "$file"},
                },
            },
        ],
        "edges": [{"source": "producer", "target": "map"}],
    }

    async def collect(session_id, message_id, member_id):
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        stored = json.loads(
            await tools["store_task_file"].call(
                None,
                filename="report.txt",
                content="Original file",
            )
        )
        await tools["submit_task_result"].call(
            None,
            result_json=json.dumps(
                {
                    "message": message,
                    "private": "unbound producer content",
                    "files": [{"file_id": stored["file_id"]}],
                }
            ),
        )
        return "Done", []

    service.ports_factory = lambda _: replace(ports, collect=collect)
    api = publication_client
    created = await api.post(
        "/api/v1/agent_drafts/workflow", json={"body": {"definition": definition}}
    )
    assert created.status_code == 200, created.text
    asset_id = created.json()["data"]["asset_id"]
    draft = f"/api/v1/agent_drafts/workflow/{asset_id}"
    asset = f"/api/v1/workflow_assets/{asset_id}"
    request = {"kind": "workflow", "revision": 1, "mode": "full"}
    saved = await api.post(f"{draft}/publish", json={"revision": 1})
    assert saved.status_code == 200, saved.text

    for response in (
        await api.post(f"{asset}/trials", json=request),
        await api.post(f"{asset}/runs", json={"version": 1}),
    ):
        assert response.status_code == 200, response.text
        run = await completed_run(service, response.json()["data"]["run_id"])
        assert run["status"] == "completed", run
        source = run["tasks"][0]
        ref = source["result_ref"]
        file_id = source["result"]["files"][0]["file_id"]
        assert run["result"] == {
            "message": message,
            "report": ref,
            "attachment": {**ref, "file_id": file_id},
        }

    partial = {**request, "mode": "single", "node_id": "map"}
    preview = await api.post(f"{asset}/trials/preview", json=partial)
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["requirements"]["producer"]["pinned_only"] is True
    updated = await service.revise_artifact(
        "alice",
        ref["artifact_id"],
        {
            "request_id": "revision",
            "expected_version": 1,
            "content": {
                **source["result"],
                "message": {"text": "New message"},
                "files": [],
            },
        },
    )
    assert updated["version"] == 2
    bounded = await api.post(
        f"{asset}/trials",
        json={**partial, "sources": {"producer": {"artifact": ref}}},
    )
    assert bounded.status_code == 200, bounded.text
    historical = await completed_run(service, bounded.json()["data"]["run_id"])
    assert historical["result"] == run["result"]
    assert "unbound producer content" not in json.dumps(historical)
    file_url = f"/api/v1/workflow_artifacts/{ref['artifact_id']}/versions"
    assert (
        await api.get(f"{file_url}/1/files/{file_id}?download=false")
    ).content == b"Original file"
    assert (await api.get(f"{file_url}/1/files/{file_id}")).content == b"Original file"
    assert (
        await api.get(f"{file_url}/2/files/{file_id}?download=false")
    ).status_code == 404
    unavailable = await api.post(
        f"{asset}/trials",
        json={**partial, "sources": {"producer": {"artifact": updated}}},
    )
    assert unavailable.status_code == 400
    assert (
        unavailable.json()["data"]["fields"][0]["path"]
        == "definition.tasks.1.inputs.attachment"
    )

    definition["tasks"][1]["input_contract"]["required"].remove("attachment")
    updated_draft = await api.put(
        draft, json={"expected_revision": 1, "body": {"definition": definition}}
    )
    assert updated_draft.status_code == 200, updated_draft.text
    optional = await api.post(
        f"{asset}/trials",
        json={**partial, "revision": 2, "sources": {"producer": {"artifact": updated}}},
    )
    assert optional.status_code == 200, optional.text
    optional_run = await completed_run(service, optional.json()["data"]["run_id"])
    assert optional_run["result"] == {
        "message": {"text": "New message"},
        "report": updated,
    }
    assert optional_run["status"] == "completed"
    with pytest.raises(RoleAssetError) as denied:
        foreign_draft = await workflow_env.roles.create_draft(
            "bob", "workflow", {"definition": definition}
        )
        await service.start_trial(
            "bob",
            foreign_draft["asset_id"],
            {
                **partial,
                "sources": {"producer": {"artifact": ref}},
            },
        )
    assert denied.value.status == 404


@pytest.mark.asyncio
async def test_agent_reads_only_its_bound_fields_and_exact_file(
    workflow_env, publication_client
):
    service = workflow_env.service
    ports = service.ports_factory("alice")
    messages = {"type": "object", "format": "astrbot-message"}
    files = {"type": "object", "format": "astrbot-file"}
    inputs = {"type": "object", "properties": {"attachment": files}}
    reads = []
    evidence = {}
    agent = {
        "member": "writer",
        "role_id": workflow_env.role["role_id"],
        "role_version": 1,
    }
    definition = {
        "name": "Scoped reads",
        "port_contract_version": 2,
        "input_contract": inputs,
        "result_task_id": "reader",
        "edges": [{"source": "author", "target": "reader"}],
        "tasks": [
            {
                **agent,
                "id": "author",
                "name": "Author",
                "task": "Prepare",
                "output_contract": {
                    "type": "object",
                    "properties": {
                        "message": messages,
                        "private": {"type": "string"},
                        "files": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["message", "private", "files"],
                },
            },
            {
                **agent,
                "id": "reader",
                "name": "Reader",
                "task": "Read the selected fields",
                "input_contract": {
                    "type": "object",
                    "properties": {**inputs["properties"], "message": messages},
                    "required": ["message"],
                },
                "output_contract": {
                    "type": "object",
                    "properties": {"attachment": files},
                    "required": ["attachment"],
                },
                "inputs": {
                    "message": {"task_id": "author", "field": "message"},
                    "attachment": {"input": "attachment"},
                },
            },
        ],
    }

    async def collect(session_id, message_id, member_id):
        binding, _, context = next(
            item
            for item in reversed(workflow_env.observations)
            if item[0].member_id == member_id
        )
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        submit = tools["submit_task_result"]
        if binding.node_id == "author":
            stored = json.loads(
                await tools["store_task_file"].call(
                    None,
                    filename="scoped.txt",
                    content="File bytes from the selected version",
                )
            )
            await submit.call(
                None,
                result_json=json.dumps(
                    {
                        "message": {"text": "Bound message"},
                        "private": "Private sibling",
                        "files": [{"file_id": stored["file_id"]}],
                    }
                ),
            )
        else:
            read = tools["read_task_artifact"]
            provenance = await service.get_run("alice", binding.run_id)
            upstream = next(
                ref
                for ref in provenance["snapshot"]["input_artifacts"]
                if ref["artifact_id"] != evidence["artifact_id"] or ref["version"] == 2
            )
            lookup = {k: v for k, v in evidence.items() if k != "file_id"}
            reads.append(
                (
                    await read.call(None, **upstream),
                    await read.call(None, **evidence),
                    await read.call(None, **{**evidence, "version": 2}),
                    await read.call(None, **{**evidence, "file_id": "other-file"}),
                    await read.call(None, **lookup),
                    context,
                )
            )
            wrong = {**evidence, "version": 2}
            rejected = await submit.call(
                None, result_json=json.dumps({"attachment": wrong})
            )
            assert "Select a reference bound to this attempt" in rejected
            await submit.call(None, result_json=json.dumps({"attachment": evidence}))
        return "Done", []

    service.ports_factory = lambda _: replace(ports, collect=collect)
    source_asset = await service.save_asset(
        "alice",
        {
            "definition": {
                **definition,
                "tasks": definition["tasks"][:1],
                "result_task_id": "author",
                "edges": [],
            },
        },
    )
    source = await service.start_run("alice", source_asset["asset_id"], {"version": 1})
    source = await completed_run(service, source["run_id"])
    evidence.update(
        source["result_ref"], file_id=source["result"]["files"][0]["file_id"]
    )
    await service.revise_artifact(
        "alice",
        evidence["artifact_id"],
        {
            "request_id": "second",
            "expected_version": 1,
            "content": {**source["result"], "message": {"text": "Version 2 message"}},
        },
    )
    asset = await service.save_asset("alice", {"definition": definition})
    path = f"/api/v1/workflow_assets/{asset['asset_id']}/runs"
    response = await publication_client.post(
        path, json={"version": 1, "inputs": {"attachment": evidence}}
    )
    assert response.status_code == 200, response.text
    run = await completed_run(service, response.json()["data"]["run_id"])
    assert run["status"] == "completed", run
    projection, attachment, wrong_version, wrong_file, pointer, context = reads[0]
    assert json.loads(projection)["content"] == {"message": {"text": "Bound message"}}
    assert json.loads(attachment)["content"] == "File bytes from the selected version"
    assert wrong_version.startswith("Error:")
    assert wrong_file.startswith("Error:")
    assert json.loads(pointer)["content"] == {}
    assert "Private sibling" not in context
    assert "Bound message" in context
    for value in (
        {**evidence, "version": True},
        {**evidence, "unexpected": "metadata"},
        {**evidence, "file_id": "other-file"},
    ):
        bad = await publication_client.post(
            path, json={"version": 1, "inputs": {"attachment": value}}
        )
        assert bad.status_code == 400, bad.text
        assert bad.json()["data"]["fields"][0]["path"] == "inputs.attachment"

    revision = await publication_client.post(
        f"/api/v1/workflow_artifacts/{run['result_ref']['artifact_id']}/versions",
        json={
            "request_id": "swap",
            "expected_version": 1,
            "content": {"attachment": {**evidence, "version": 2}},
        },
    )
    assert revision.status_code == 400, revision.text
    assert revision.json()["data"]["fields"][0]["path"] == "content.attachment"

    draft = await workflow_env.roles.create_draft(
        "alice", "workflow", {"definition": definition}
    )
    trial_path = f"/api/v1/workflow_assets/{draft['asset_id']}/trials"
    trial_payload = {
        "kind": "workflow",
        "revision": draft["revision"],
        "mode": "single",
        "node_id": "reader",
        "inputs": {"attachment": evidence},
        "sources": {"author": {"artifact": {**source["result_ref"], "version": 2}}},
    }
    trial_preview = await publication_client.post(
        trial_path + "/preview", json=trial_payload
    )
    assert trial_preview.status_code == 200, trial_preview.text
    assert trial_preview.json()["data"]["requirements"]["author"]["fields"] == {
        "message": ["object"]
    }
    bounded = await publication_client.post(trial_path, json=trial_payload)
    assert bounded.status_code == 200, bounded.text
    trial = await completed_run(service, bounded.json()["data"]["run_id"])
    assert trial["status"] == "completed", trial
    projection, attachment, wrong_version, wrong_file, pointer, context = reads[1]
    assert json.loads(projection) == {
        **source["result_ref"],
        "version": 2,
        "content": {"message": {"text": "Version 2 message"}},
        "files": [],
    }
    assert json.loads(pointer) == {**source["result_ref"], "content": {}, "files": []}
    assert json.loads(attachment)["content"] == "File bytes from the selected version"
    assert wrong_version.startswith("Error:")
    assert wrong_file.startswith("Error:")
    assert "Private sibling" not in context
    assert "Private sibling" not in json.dumps(trial)

    typed_contract = {
        "type": "object",
        "properties": {"attachment": files},
        "required": ["attachment"],
    }
    plain_contract = {
        **typed_contract,
        "properties": {"attachment": {"type": "object"}},
    }
    history_node = {
        "id": "history",
        "name": "History",
        "transform": "map_fields",
        "input_contract": typed_contract,
        "output_contract": typed_contract,
        "inputs": {"attachment": {"input": "attachment"}},
    }
    plain_asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Plain JSON",
                "port_contract_version": 2,
                "input_contract": plain_contract,
                "result_task_id": "history",
                "tasks": [
                    {
                        **history_node,
                        "input_contract": plain_contract,
                        "output_contract": plain_contract,
                    },
                ],
            }
        },
    )
    plain = await service.start_run(
        "alice",
        plain_asset["asset_id"],
        {
            "version": 1,
            "inputs": {"attachment": evidence},
        },
    )
    plain = await completed_run(service, plain["run_id"])
    assert plain["result"] == {"attachment": evidence}
    boundary = {
        "name": "Historical managed reference",
        "port_contract_version": 2,
        "input_contract": typed_contract,
        "result_task_id": "sink",
        "tasks": [
            history_node,
            {
                **history_node,
                "id": "sink",
                "name": "Sink",
                "inputs": {"attachment": {"task_id": "history", "field": "attachment"}},
            },
        ],
        "edges": [{"source": "history", "target": "sink"}],
    }
    draft = await workflow_env.roles.create_draft(
        "alice", "workflow", {"definition": boundary}
    )
    path = f"/api/v1/workflow_assets/{draft['asset_id']}/trials"
    request = {
        "kind": "workflow",
        "revision": draft["revision"],
        "mode": "single",
        "node_id": "sink",
    }
    forged = await publication_client.post(
        path,
        json={
            **request,
            "sources": {"history": {"artifact": plain["result_ref"]}},
        },
    )
    assert forged.status_code == 400, forged.text
    assert (
        forged.json()["data"]["fields"][0]["path"]
        == "definition.tasks.1.inputs.attachment"
    )
    assert "did not publish" in forged.json()["message"]
    genuine = await publication_client.post(
        path,
        json={
            **request,
            "sources": {"history": {"artifact": run["result_ref"]}},
        },
    )
    assert genuine.status_code == 200, genuine.text
    genuine_run = await completed_run(service, genuine.json()["data"]["run_id"])
    assert genuine_run["result"] == {"attachment": evidence}
    assert genuine_run["status"] == "completed"

    boundary["tasks"][1]["input_contract"] = {
        **typed_contract,
        "properties": {"attachment": False},
    }
    rejected = await publication_client.post(
        "/api/v1/workflow_assets", json={"definition": boundary}
    )
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["data"]["fields"][0]["code"] == "CONTRACT"
