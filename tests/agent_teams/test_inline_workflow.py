"""Directly configured workflow agents through the public services."""

import asyncio
import copy
import json

import pytest

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_ports import TeamPorts


@pytest.mark.asyncio
async def test_inline_agent_version_and_run(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "inline.db"))
    inventory = {
        "profiles": [{"id": "default"}],
        "models": [{"id": "model-a"}, {"id": "model-b"}],
        "personas": [{"id": "persona", "prompt": "Persona"}],
        "tools": [],
        "skills": [],
        "knowledge_bases": [],
    }

    async def catalog():
        return inventory

    observed = []
    service = WorkflowService(db, RoleAssetService(db, catalog_loader=catalog))

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        observed.append(await service.get_run("alice", binding.run_id))
        return message_id

    async def collect(session_id, message_id, member_id):
        tool = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[0]
        await tool.call(None, text="Result")
        return "Done", []

    service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    definition = {
        "name": "Inline",
        "result_task_id": "agent",
        "default_provider_id": "model-a",
        "required_inputs": ["topic"],
        "tasks": [
            {
                "id": "agent",
                "name": "Agent",
                "member": "agent",
                "role": {"name": "Writer", "prompt": "Write carefully", "tools": []},
                "installation": {"profile_id": "default"},
                "task": "Write {topic}",
                "inputs": {"topic": {"input": "topic"}},
            }
        ],
    }
    try:
        asset = await service.save_asset("alice", {"definition": definition})
        reopened = await service.get_asset("alice", asset["asset_id"], 1)
        assert reopened["definition"]["tasks"][0]["role"]["provider_id"] is None
        inventory["profiles"][0]["provider_pool"] = ["model-b"]
        denied = await service.check_readiness("alice", asset["asset_id"], 1)
        assert not denied["ready"]
        assert denied["issues"][0]["path"] == "definition.default_provider_id"
        with pytest.raises(RoleAssetError):
            await service.start_run(
                "alice",
                asset["asset_id"],
                {"version": 1, "inputs": {"topic": "energy"}},
            )
        inventory["profiles"][0]["provider_pool"] = ["*"]
        assert (await service.check_readiness("alice", asset["asset_id"], 1))["ready"]
        run = await service.start_run(
            "alice", asset["asset_id"], {"version": 1, "inputs": {"topic": "energy"}}
        )
        for _ in range(200):
            current = await service.get_run("alice", run["run_id"])
            if current["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)
        assert current["status"] == "completed", current
        assert observed[0]["snapshot"]["task"] == "Write energy"
        assert observed[0]["snapshot"]["effective"]["provider_id"] == "model-a"
        assert (
            observed[0]["snapshot"]["role"]["sources"]["provider_id"]
            == "workflow:default_provider_id"
        )
        assert observed[0]["snapshot"]["role"]["definition"]["provider_id"] is None
        assert observed[0]["snapshot"]["effective"]["persona_id"] is None
        assert observed[0]["snapshot"]["role"]["role_id"] is None
        assert current["tasks"][0]["result"] == {"text": "Result"}
        assert await service.get_result("alice", run["run_id"]) == {"text": "Result"}
        assert current["result_ref"] == current["tasks"][0]["result_ref"]
        overview = await service.get_run("alice", run["run_id"], include_details=False)
        assert overview["result_ref"] == current["result_ref"]
        no_default = copy.deepcopy(definition)
        no_default["default_provider_id"] = None
        missing = await service.save_asset("alice", {"definition": no_default})
        assert not (await service.check_readiness("alice", missing["asset_id"], 1))[
            "ready"
        ]
        with pytest.raises(RoleAssetError):
            await service.start_run(
                "alice",
                missing["asset_id"],
                {"version": 1, "inputs": {"topic": "energy"}},
            )
        no_default["tasks"][0]["role"]["provider_id"] = "model-b"
        overridden = await service.save_asset(
            "alice",
            {"definition": no_default, "expected_version": 1},
            missing["asset_id"],
        )
        assert (await service.check_readiness("alice", overridden["asset_id"], 2))[
            "ready"
        ]
        overridden_run = await service.start_run(
            "alice",
            overridden["asset_id"],
            {"version": 2, "inputs": {"topic": "energy"}},
        )
        for _ in range(200):
            overridden_state = await service.get_run("alice", overridden_run["run_id"])
            if overridden_state["status"] in {"completed", "failed", "blocked"}:
                break
            await asyncio.sleep(0.01)
        assert overridden_state["status"] == "completed"
        assert observed[1]["snapshot"]["effective"]["provider_id"] == "model-b"
        assert observed[1]["snapshot"]["role"]["sources"]["provider_id"] == "role"
        await service.shutdown()
        await db.engine.dispose()
        reopened_db = SQLiteDatabase(str(tmp_path / "inline.db"))
        reopened_service = WorkflowService(
            reopened_db, RoleAssetService(reopened_db, catalog_loader=catalog)
        )
        try:
            assert (await reopened_service.get_asset("alice", asset["asset_id"], 1))[
                "definition"
            ] == reopened["definition"]
            assert await reopened_service.get_result("alice", run["run_id"]) == {
                "text": "Result"
            }
        finally:
            await reopened_db.engine.dispose()
    finally:
        await service.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_inline_members_have_independent_capacity_and_validate_sources(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "capacity.db"))

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "m"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    started = set()
    both = asyncio.Event()
    release = asyncio.Event()

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        started.add(text)
        if len(started) == 2:
            both.set()
        return message_id

    async def collect(session_id, message_id, member_id):
        await release.wait()
        await AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[
            0
        ].call(None, text="Done")
        return "Done", []

    service = WorkflowService(
        db,
        RoleAssetService(db, catalog_loader=catalog),
        ports_factory=lambda _: TeamPorts(
            deliver=deliver,
            collect=collect,
            is_busy=lambda _: False,
            emit=lambda _: None,
        ),
    )
    tasks = [
        {
            "id": key,
            "name": key,
            "member": key,
            "task": key,
            "role": {
                "name": key,
                "prompt": "Work",
                "max_instances": 1,
                "max_parallel": 1,
            },
            "installation": {"profile_id": "default"},
        }
        for key in ("a", "b")
    ]
    definition = {
        "name": "Independent",
        "result_task_id": "b",
        "default_provider_id": "m",
        "max_parallel": 2,
        "tasks": tasks,
    }
    try:
        for invalid_task in (
            {**tasks[0], "role_id": "partial"},
            {**tasks[0], "role_id": "template", "role_version": 1},
            {**tasks[0], "installation": {}},
        ):
            invalid = {**definition, "tasks": [invalid_task]}
            with pytest.raises(RoleAssetError):
                await service.save_asset("alice", {"definition": invalid})
        asset = await service.save_asset("alice", {"definition": definition})
        await service.start_run("alice", asset["asset_id"], {"version": 1})
        await asyncio.wait_for(both.wait(), 5)
        assert started == {"a", "b"}
    finally:
        release.set()
        await service.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_explicit_fields_project_artifacts_and_select_result(tmp_path, nested):
    db = SQLiteDatabase(str(tmp_path / "fields.db"))

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "m"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    observed = {}
    bindings = {}
    service = WorkflowService(db, RoleAssetService(db, catalog_loader=catalog))

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        bindings[session_id] = binding
        observed[binding.node_id] = {
            "text": text,
            "session": session_id,
            "conversation": binding.conversation_id,
        }
        return message_id

    async def collect(session_id, message_id, member_id):
        binding = bindings[session_id]
        tools = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")
        if binding.node_id == "first":
            assert (
                "accepted"
                in (
                    await tools[0].call(
                        None,
                        result_json=json.dumps(
                            {
                                "public": {
                                    "value": "visible",
                                    "private": "hidden-nested",
                                }
                                if nested
                                else "visible",
                                "secret": "hidden",
                            }
                        ),
                    )
                ).lower()
            )
        else:
            run = await service.get_run("alice", binding.run_id)
            ref = run["snapshot"]["input_artifacts"][0]
            reader = next(tool for tool in tools if tool.name == "read_task_artifact")
            observed["projection"] = await reader.call(None, **ref)
            observed["file_attempt"] = await reader.call(None, **ref, file_id="secret")
            await tools[0].call(None, text="Final")
        return "Done", []

    service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    role = {"name": "Agent", "prompt": "Work", "tools": []}
    task = {"member": "same", "role": role, "installation": {"profile_id": "default"}}
    definition = {
        "name": "Fields",
        "default_provider_id": "m",
        "result_task_id": "second",
        "tasks": [
            {
                **task,
                "id": "first",
                "name": "First",
                "task": "first secret history",
                "output_contract": {
                    "type": "object",
                    "properties": {
                        "public": {
                            "type": "object",
                            "properties": {
                                "value": {"type": "string"},
                                "private": {"type": "string"},
                            },
                            "required": ["value", "private"],
                        }
                        if nested
                        else {"type": "string"},
                        "secret": {"type": "string"},
                    },
                    "required": ["public", "secret"],
                    "additionalProperties": False,
                },
            },
            {
                **task,
                "id": "second",
                "name": "Second",
                "task": "Use {value}",
                "inputs": {
                    "value": {
                        "task_id": "first",
                        "field": "public.value" if nested else "public",
                    }
                },
            },
        ],
        "edges": [{"source": "first", "target": "second"}],
    }
    try:
        asset = await service.save_asset("alice", {"definition": definition})
        reopened = await service.get_asset("alice", asset["asset_id"], 1)
        assert reopened["definition"]["result_task_id"] == "second"
        run = await service.start_run("alice", asset["asset_id"], {"version": 1})
        for _ in range(500):
            current = await service.get_run("alice", run["run_id"])
            if current["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert current["status"] == "completed", current
        assert observed["second"]["text"] == "Use visible"
        assert observed["first"]["session"] != observed["second"]["session"]
        assert observed["first"]["conversation"] != observed["second"]["conversation"]
        assert (
            "visible" in observed["projection"]
            and "hidden" not in observed["projection"]
        )
        assert "hidden-nested" not in observed["projection"]
        assert "Error" in observed["file_attempt"]
        first, second = current["tasks"]
        assert (
            first["snapshot"]["member"]["member_id"]
            == second["snapshot"]["member"]["member_id"]
        )
        assert current["result"] == {"text": "Final"}
        assert current["result_ref"] == second["result_ref"]
        assert (await service.read_artifact("alice", **first["result_ref"]))["content"][
            "secret"
        ] == "hidden"
        assert "conversation_id" not in current["snapshot"]["members"]["same"]["member"]
        invalid = copy.deepcopy(definition)
        invalid["result_task_id"] = "deleted"
        broken = await service.save_asset("alice", {"definition": invalid})
        readiness = await service.check_readiness("alice", broken["asset_id"], 1)
        assert (
            not readiness["ready"]
            and readiness["issues"][0]["path"] == "definition.result_task_id"
        )
        wrong_type = copy.deepcopy(definition)
        wrong_type["tasks"][0]["output_contract"]["properties"]["public"] = {
            "type": "array",
            "items": {"type": "string"},
        }
        with pytest.raises(RoleAssetError) as wrong:
            await service.save_asset("alice", {"definition": wrong_type})
        assert any(
            issue["path"] == "definition.tasks.1.inputs.value"
            for issue in wrong.value.fields
        )
        invalid["tasks"][1]["inputs"]["value"]["field"] = "deleted"
        with pytest.raises(RoleAssetError) as error:
            await service.save_asset("alice", {"definition": invalid})
        assert any(
            issue["path"] == "definition.tasks.1.inputs.value"
            for issue in error.value.fields
        )
    finally:
        await service.shutdown()
        await db.engine.dispose()
