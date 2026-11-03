"""Contract and immutable artifact behavior through the public workflow service."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.workflow_assets import router
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest_asyncio.fixture
async def scripted_workflow(workflow_env):
    """Reuse the public workflow seam with a scenario's external collection port.

    Args:
        workflow_env: Real database, public services and model observations.

    Yields:
        A service factory sharing persistence and delivery across public scenarios.
    """
    env = workflow_env
    created = []

    def create(collect):
        """Connect one scenario's external collection behavior.

        Args:
            collect: Scripted response collection at the existing execution port.

        Returns:
            A public workflow service using the real database.
        """
        service = WorkflowService(
            env.db,
            env.roles,
            ports_factory=lambda user: TeamPorts(
                deliver=lambda *args, **kwargs: env.service.ports_factory(user).deliver(
                    *args, **kwargs
                ),
                collect=collect,
                is_busy=lambda sid: False,
                emit=lambda event: None,
            ),
        )
        created.append(service)
        return service

    yield create
    for service in created:
        await service.shutdown()


@pytest.mark.asyncio
async def test_contract_errors_can_be_corrected_before_a_result_is_consumable(
    workflow_env,
    scripted_workflow,
):
    env = workflow_env
    contract = {
        "type": "object",
        "properties": {
            "approved": {"type": "boolean"},
            "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        },
        "required": ["approved", "evidence"],
        "additionalProperties": False,
    }
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                **env.asset["definition"],
                "tasks": [
                    {**env.asset["definition"]["tasks"][0], "output_contract": contract}
                ],
            }
        },
    )
    reached = asyncio.Event()
    release = asyncio.Event()
    errors = []

    async def collect(session_id, message_id, member_id):
        tool = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[0]
        for arguments in (
            {},
            {"result_json": {}},
            {"result_json": "{"},
            {"result_json": "[]"},
            {"result_json": "null"},
        ):
            assert "Error: result_json:" in await tool.call(None, **arguments)
        missing = await tool.call(None, result_json="{}")
        assert "result.approved" in missing and "result.evidence" in missing
        errors.append(
            await tool.call(None, result_json='{"approved":"yes","evidence":[]}')
        )
        reached.set()
        await release.wait()
        assert (
            await tool.call(
                None, result_json='{"approved":true,"evidence":["Verified source"]}'
            )
            == "Result accepted."
        )
        return "Done", []

    service = scripted_workflow(collect)
    try:
        run = await service.start_run("alice", asset["asset_id"], {"version": 1})
        await asyncio.wait_for(reached.wait(), 2)
        assert "result.approved" in errors[0] and "result.evidence" in errors[0]
        rejected = await service.get_run("alice", run["run_id"])
        assert next(task for task in rejected["tasks"] if task["node_id"] == "writer")[
            "validation_errors"
        ][0]["path"].startswith("result.")
        with pytest.raises(RoleAssetError, match="no completed result"):
            await service.get_result("alice", run["run_id"])
        release.set()
        done = await completed_run(service, run["run_id"])
        assert done["status"] == "completed", done
        assert await service.get_result("alice", run["run_id"]) == {
            "approved": True,
            "evidence": ["Verified source"],
        }
        assert done["validation_errors"] == []
    finally:
        release.set()
        await service.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("contract_form", ["properties", "allOf", "$ref"])
async def test_ready_file_reference_remains_readable_after_reopening(
    workflow_env, tmp_path, scripted_workflow, contract_form
):
    env = workflow_env
    contract = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"file_id": {"type": "string"}},
                    "required": ["file_id"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["text", "files"],
        "additionalProperties": False,
    }
    if contract_form == "allOf":
        contract = {"type": "object", "allOf": [contract]}
    elif contract_form == "$ref":
        contract = {
            "type": "object",
            "$defs": {"report": contract},
            "$ref": "#/$defs/report",
        }
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                **env.asset["definition"],
                "tasks": [
                    {**env.asset["definition"]["tasks"][0], "output_contract": contract}
                ],
            }
        },
    )
    path = tmp_path / "evidence.txt"
    path.write_text("Verified file content. " * 1000, encoding="utf-8")
    expected = path.read_bytes()

    async def collect(session_id, message_id, member_id):
        import json

        tools = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")
        assert "store_task_file" in [tool.name for tool in tools]
        assert "Error" in await tools[0].call(
            None, result_json='{"text":"Report","files":[{"file_id":"missing"}]}'
        )
        assert "Error" in await tools[1].call(None, path=str(path))
        stored = json.loads(
            await tools[1].call(None, filename=path.name, content=expected.decode())
        )
        path.unlink()
        await tools[0].call(
            None,
            result_json=json.dumps(
                {"text": "Report", "files": [{"file_id": stored["file_id"]}]}
            ),
        )
        return "Done", []

    service = scripted_workflow(collect)
    try:
        run = await service.start_run("alice", asset["asset_id"], {"version": 1})
        done = await completed_run(service, run["run_id"])
        assert done["status"] == "completed", done
        file_id = done["result"]["files"][0]["file_id"]
        reopened = WorkflowService(env.db, env.roles)
        file = await reopened.get_file("alice", **done["result_ref"], file_id=file_id)
        assert file["content"] == expected
        assert file["filename"] == "evidence.txt"
        assert str(tmp_path) not in str(done["result"])
        with pytest.raises(RoleAssetError, match="not found"):
            await reopened.get_file("bob", **done["result_ref"], file_id=file_id)
        app = FastAPI()
        app.state.services = SimpleNamespace(workflow_assets=reopened)
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[require_dashboard_user] = lambda: "alice"
        ref = done["result_ref"]
        base = f"/api/v1/workflow_artifacts/{ref['artifact_id']}/versions/1"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            preview = await client.get(f"{base}/files/{file_id}?download=false")
            assert preview.content == expected
            assert preview.headers["content-disposition"].startswith("inline;")
            download = await client.get(f"{base}/files/{file_id}")
            assert download.content == expected
            assert download.headers["content-disposition"].startswith("attachment;")
            stored = await client.post(
                f"/api/v1/workflow_runs/{ref['artifact_id']}/files?filename=revision.txt",
                content=b"Revised ready file.",
            )
            assert stored.status_code == 200
            content = {
                "text": "Revised report",
                "files": [{"file_id": stored.json()["data"]["file_id"]}],
            }
            revision = await client.post(
                f"/api/v1/workflow_artifacts/{ref['artifact_id']}/versions",
                json={
                    "expected_version": 1,
                    "request_id": "file-revision",
                    "content": content,
                },
            )
            assert revision.status_code == 200
            assert (await client.get(f"{base}/files/{file_id}")).content == expected
            assert (await client.get(base)).json()["data"]["content"] == done["result"]
            app.dependency_overrides[require_dashboard_user] = lambda: "bob"
            assert (await client.get(base)).status_code == 404
            assert (await client.get(f"{base}/files/{file_id}")).status_code == 404

        async def read_file(session_id, message_id, member_id):
            tools = AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
            by_name = {tool.name: tool for tool in tools}
            read = json.loads(
                await by_name["read_task_artifact"].call(None, **ref, file_id=file_id)
            )
            assert read["encoding"] == "utf-8"
            assert read["content"] == expected.decode()
            await by_name["submit_task_result"].call(
                None, text="Consumed full file evidence."
            )
            return "Done", []

        consumer = scripted_workflow(read_file)
        consumer_asset = await consumer.save_asset(
            "alice",
            {
                "definition": {
                    "kind": "autonomous",
                    "name": "File reader",
                    "coordinator": {
                        "id": "coordinator",
                        "role_id": env.role["role_id"],
                        "role_version": 1,
                    },
                    "allowed_roles": [
                        {
                            "id": "reader",
                            "role_id": env.role["role_id"],
                            "role_version": 1,
                        }
                    ],
                }
            },
        )
        try:
            task = await consumer.start_run(
                "alice",
                consumer_asset["asset_id"],
                {
                    "version": 1,
                    "inputs": {"goal": "Read evidence"},
                    "input_artifacts": [ref],
                },
            )
            assert (await completed_run(consumer, task["run_id"]))[
                "status"
            ] == "completed"
        finally:
            await consumer.shutdown()
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_revision_preserves_pinned_consumers_and_full_content(
    workflow_env, scripted_workflow
):
    env = workflow_env
    source = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    done = await completed_run(env.service, source["run_id"])
    ref = done["result_ref"]
    original = "Complete report. " * 1000
    assert (await env.service.read_artifact("alice", **ref))["content"] == {
        "text": original
    }
    reached = asyncio.Event()
    release = asyncio.Event()
    consumed = []

    async def collect(session_id, message_id, member_id):
        tools = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")
        reached.set()
        await release.wait()
        import json

        by_name = {tool.name: tool for tool in tools}
        consumed.append(
            json.loads(await by_name["read_task_artifact"].call(None, **ref))
        )
        assert "Error" in await by_name["read_task_artifact"].call(
            None, artifact_id=ref["artifact_id"], version=2
        )
        await by_name["submit_task_result"].call(
            None, text="Read the final complete evidence."
        )
        return "Done", []

    service = scripted_workflow(collect)
    consumer_asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Evidence reader",
                "coordinator": {
                    "id": "coordinator",
                    "role_id": env.role["role_id"],
                    "role_version": 1,
                },
                "allowed_roles": [
                    {"id": "reader", "role_id": env.role["role_id"], "role_version": 1}
                ],
            }
        },
    )
    try:
        consumer = await service.start_run(
            "alice",
            consumer_asset["asset_id"],
            {
                "version": 1,
                "inputs": {"goal": "Read evidence"},
                "input_artifacts": [ref],
            },
        )
        await asyncio.wait_for(reached.wait(), 2)
        revision = await env.service.revise_artifact(
            "alice",
            ref["artifact_id"],
            {
                "expected_version": 1,
                "request_id": "revision-1",
                "content": {"text": "Revised evidence."},
            },
        )
        repeated = await env.service.revise_artifact(
            "alice",
            ref["artifact_id"],
            {
                "expected_version": 1,
                "request_id": "revision-1",
                "content": {"text": "Revised evidence."},
            },
        )
        assert revision == repeated
        release.set()
        finished = await completed_run(service, consumer["run_id"])
        assert finished["snapshot"]["input_artifacts"] == [ref]
        assert consumed[0]["content"]["text"] == original
        assert next(
            task for task in finished["tasks"] if task["node_id"] == "coordinator"
        )["read_artifacts"] == [ref]
        assert len(env.observations) == 2
        assert (await env.service.read_artifact("alice", **revision))["content"] == {
            "text": "Revised evidence."
        }
        assert (await env.service.read_artifact("alice", **ref))["content"][
            "text"
        ] == original
        with pytest.raises(RoleAssetError, match="not found"):
            await env.service.read_artifact("bob", **ref)
    finally:
        release.set()
        await service.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema_location", ["properties", "const", "enum", "default", "referenced_default"]
)
async def test_schema_keywords_in_result_data_are_preserved(
    workflow_env, scripted_workflow, schema_location
):
    env = workflow_env
    result = {
        "$ref": "https://source.test/evidence",
        "$dynamicRef": "https://source.test/other",
        "$id": "evidence-1",
    }
    if schema_location in {"properties", "referenced_default"}:
        contract = {
            "type": "object",
            "properties": {name: {"type": "string"} for name in result},
            "required": list(result),
            "additionalProperties": False,
        }
        if schema_location == "referenced_default":
            contract = {"type": "object", "$ref": "#/default", "default": contract}
    else:
        contract = {
            "type": "object",
            schema_location: [result] if schema_location == "enum" else result,
        }
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                **env.asset["definition"],
                "tasks": [
                    {**env.asset["definition"]["tasks"][0], "output_contract": contract}
                ],
            }
        },
    )

    async def collect(session_id, message_id, member_id):
        tool = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[0]
        assert (
            await tool.call(None, result_json=json.dumps(result)) == "Result accepted."
        )
        return "Done", []

    service = scripted_workflow(collect)
    run = await service.start_run("alice", asset["asset_id"], {"version": 1})
    done = await completed_run(service, run["run_id"])
    assert done["status"] == "completed", done
    artifact = await service.read_artifact("alice", **done["result_ref"])
    assert artifact["content"] == result


@pytest.mark.asyncio
async def test_recursive_local_contract_can_be_saved_and_consumed(
    workflow_env, scripted_workflow
):
    env = workflow_env
    contract = {
        "type": "object",
        "properties": {"next": {"anyOf": [{"type": "null"}, {"$ref": "#"}]}},
        "required": ["next"],
        "additionalProperties": False,
    }
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                **env.asset["definition"],
                "tasks": [
                    {**env.asset["definition"]["tasks"][0], "output_contract": contract}
                ],
            }
        },
    )
    result = {"next": {"next": None}}

    async def collect(session_id, message_id, member_id):
        tool = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[0]
        assert (
            await tool.call(None, result_json=json.dumps(result)) == "Result accepted."
        )
        return "Done", []

    service = scripted_workflow(collect)
    run = await service.start_run("alice", asset["asset_id"], {"version": 1})
    done = await completed_run(service, run["run_id"])
    assert done["status"] == "completed", done
    assert (await service.read_artifact("alice", **done["result_ref"]))[
        "content"
    ] == result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string"},
        {"type": "object", "properties": {"value": {"type": "invalid"}}},
        {
            "type": "object",
            "properties": {"value": {"$ref": "https://example.test/schema"}},
        },
        {"type": "object", "properties": {"value": {"$ref": "#/$defs/missing"}}},
        {"type": "object", "allOf": [{"$ref": "https://example.test/schema"}]},
        {
            "type": "object",
            "$defs": {"value": {"$dynamicRef": "https://example.test/schema"}},
        },
        {
            "type": "object",
            "properties": {"value": {"$id": "https://example.test/schema"}},
        },
        {
            "type": "object",
            "$ref": "#/default",
            "default": {"$ref": "https://example.test/schema"},
        },
        {"type": "object", "$ref": "#/default", "default": {"type": "invalid"}},
        {"type": "object", "$ref": "#/default", "default": "ordinary data"},
    ],
)
async def test_invalid_contracts_are_rejected_before_an_asset_can_start(
    workflow_env, schema
):
    env = workflow_env
    with pytest.raises(RoleAssetError) as rejected:
        await env.service.save_asset(
            "alice",
            {
                "definition": {
                    **env.asset["definition"],
                    "tasks": [
                        {
                            **env.asset["definition"]["tasks"][0],
                            "output_contract": schema,
                        }
                    ],
                }
            },
        )
    assert rejected.value.fields[0]["path"] == "definition.tasks.0.output_contract"
    assert len((await env.service.list_assets("alice"))["assets"]) == 1


@pytest.mark.asyncio
async def test_duplicate_submissions_and_revisions_commit_one_version_and_completion(
    workflow_env,
    scripted_workflow,
):
    env = workflow_env
    reached = asyncio.Event()
    release = asyncio.Event()

    async def collect(session_id, message_id, member_id):
        reached.set()
        await release.wait()
        return "Done", []

    service = scripted_workflow(collect)
    try:
        run = await service.start_run("alice", env.asset["asset_id"], {"version": 1})
        await asyncio.wait_for(reached.wait(), 2)
        leaf = next(
            task
            for task in (await service.get_run("alice", run["run_id"]))["tasks"]
            if task["node_id"] == "writer"
        )
        await asyncio.gather(
            *(
                service.submit_result(
                    "alice",
                    leaf["run_id"],
                    leaf["state"]["attempt_id"],
                    {"text": "Verified result"},
                )
                for _ in range(5)
            )
        )
        pending = await service.get_run("alice", run["run_id"])
        assert pending["result_ref"] is None and pending["result"] is None
        release.set()
        completed = await completed_run(service, run["run_id"])
        repeats = await asyncio.gather(
            *(service.finish_submission("alice", leaf["run_id"]) for _ in range(5))
        )
        assert all(row == repeats[0] for row in repeats)
        assert repeats[0]["result_ref"] == completed["result_ref"]
        assert len(completed["artifacts"]) == 1
        assert [event["kind"] for event in completed["events"]].count(
            "task.completed"
        ) == 1
        request = {
            "expected_version": 1,
            "request_id": "edit-one",
            "content": {"text": "Revised"},
        }
        revisions = await asyncio.gather(
            *(
                service.revise_artifact("alice", leaf["run_id"], request)
                for _ in range(5)
            )
        )
        assert revisions == [{"artifact_id": leaf["run_id"], "version": 2}] * 5
        assert len((await service.get_run("alice", leaf["run_id"]))["artifacts"]) == 2
        with pytest.raises(RoleAssetError):
            await service.submit_result(
                "alice", leaf["run_id"], "stale-attempt", {"text": "Wrong"}
            )
        assert await service.get_result("alice", run["run_id"]) == {
            "text": "Verified result"
        }
    finally:
        release.set()
        await service.shutdown()
