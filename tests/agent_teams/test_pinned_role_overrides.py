"""Pinned role overrides across persistence and execution."""

import asyncio
import copy

import pytest
from sqlalchemy import delete

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.po import AgentRoleVersion
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_ports import TeamPorts


@pytest.mark.asyncio
async def test_pinned_versions_keep_explicit_overrides_and_frozen_run(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "roles.db"))

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "model-a"}, {"id": "model-b"}],
            "personas": [{"id": "persona", "prompt": "Persona"}],
            "tools": [{"id": "search", "name": "Search"}],
            "skills": [{"id": "writing", "name": "Writing"}],
            "knowledge_bases": [{"id": "library", "name": "Library"}],
        }

    roles = RoleAssetService(db, catalog_loader=catalog)
    role_definition = {
        "name": "Writer",
        "prompt": "Version one",
        "provider_id": "model-b",
        "persona_id": "persona",
        "tools": ["search"],
        "skills": ["writing"],
        "knowledge_bases": ["library"],
    }
    role = await roles.save_role("alice", {"definition": role_definition})
    observed = []
    service = WorkflowService(db, roles)

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        observed.append(await service.get_run("alice", binding.run_id))
        return message_id

    async def collect(session_id, message_id, member_id):
        await AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[
            0
        ].call(None, text="Done")
        return "Done", []

    service.ports_factory = lambda _: TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda _: False, emit=lambda _: None
    )
    definition = {
        "name": "Workflow",
        "default_provider_id": "model-a",
        "tasks": [
            {
                "id": "writer",
                "name": "Writer",
                "member": "writer",
                "task": "Write",
                "role_id": role["role_id"],
                "role_version": 1,
                "overrides": {
                    "persona_id": None,
                    "provider_id": None,
                    "tools": [],
                    "skills": [],
                    "knowledge_bases": [],
                },
            }
        ],
    }
    try:
        asset = await service.save_asset("alice", {"definition": definition})
        role_definition["prompt"] = "Version two"
        await roles.save_role(
            "alice",
            {"definition": role_definition, "expected_version": 1},
            role["role_id"],
        )
        reopened = await service.get_asset("alice", asset["asset_id"], 1)
        assert (
            reopened["definition"]["tasks"][0]["overrides"]
            == definition["tasks"][0]["overrides"]
        )
        run = await service.start_run("alice", asset["asset_id"], {"version": 1})
        for _ in range(200):
            state = await service.get_run("alice", run["run_id"])
            if state["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)
        assert state["status"] == "completed", state
        effective = observed[0]["snapshot"]["effective"]
        assert effective["prompt"] == "Version one"
        assert effective["persona_id"] is None
        assert effective["provider_id"] == "model-a"
        assert effective["tools"] == []
        assert effective["skills"] == []
        assert effective["knowledge_bases"] == []
        upgraded = copy.deepcopy(reopened["definition"])
        upgraded["tasks"][0]["role_version"] = 2
        saved = await service.save_asset(
            "alice", {"definition": upgraded, "expected_version": 1}, asset["asset_id"]
        )
        assert (
            saved["definition"]["tasks"][0]["overrides"]
            == definition["tasks"][0]["overrides"]
        )
        second = await service.start_run("alice", asset["asset_id"], {"version": 2})
        for _ in range(200):
            state = await service.get_run("alice", second["run_id"])
            if state["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)
        assert state["status"] == "completed", state
        assert observed[1]["snapshot"]["effective"]["prompt"] == "Version two"
        assert observed[1]["snapshot"]["effective"]["persona_id"] is None
        assert observed[1]["snapshot"]["effective"]["skills"] == []
        assert observed[1]["snapshot"]["effective"]["knowledge_bases"] == []
        assert (await service.get_run("alice", observed[0]["run_id"]))["snapshot"][
            "effective"
        ] == effective
        conflict = copy.deepcopy(upgraded)
        conflict["tasks"].append(
            {**conflict["tasks"][0], "id": "other", "overrides": {}}
        )
        with pytest.raises(RoleAssetError):
            await service.save_asset("alice", {"definition": conflict})
        inherited = copy.deepcopy(upgraded)
        inherited["tasks"][0]["overrides"] = {}
        inherited_asset = await service.save_asset("alice", {"definition": inherited})
        inherited_run = await service.start_run(
            "alice", inherited_asset["asset_id"], {"version": 1}
        )
        for _ in range(200):
            state = await service.get_run("alice", inherited_run["run_id"])
            if state["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)
        assert state["status"] == "completed", state
        effective_inherited = observed[2]["snapshot"]["effective"]
        assert effective_inherited["persona_id"] == "persona"
        assert effective_inherited["provider_id"] == "model-b"
        assert effective_inherited["tools"] == ["search"]
        assert effective_inherited["skills"] == ["writing"]
        assert effective_inherited["knowledge_bases"] == ["library"]
        cleared = copy.deepcopy(inherited)
        cleared["tasks"][0]["overrides"] = {"prompt": ""}
        cleared_asset = await service.save_asset("alice", {"definition": cleared})
        cleared_run = await service.start_run(
            "alice", cleared_asset["asset_id"], {"version": 1}
        )
        for _ in range(200):
            state = await service.get_run("alice", cleared_run["run_id"])
            if state["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)
        assert state["status"] == "completed", state
        assert observed[3]["snapshot"]["effective"]["prompt"] == ""
        inaccessible = copy.deepcopy(inherited)
        with pytest.raises(RoleAssetError) as forbidden:
            await service.save_asset("bob", {"definition": inaccessible})
        assert forbidden.value.fields[0]["path"] == "definition.tasks.0.role_id"
        inaccessible["tasks"][0]["role_version"] = 999
        with pytest.raises(RoleAssetError) as missing:
            await service.save_asset("alice", {"definition": inaccessible})
        assert missing.value.fields[0]["path"] == "definition.tasks.0.role_id"
        with pytest.raises(RoleAssetError):
            await roles.resolve_role("bob", role["role_id"], 1, {})
        with pytest.raises(RoleAssetError):
            await roles.resolve_role("alice", role["role_id"], 999, {})

        direct_role = {"name": "Reusable", "prompt": "Reusable duties", "tools": []}
        saved_role = await roles.save_role(
            "alice",
            {"definition": direct_role, "installation": {"profile_id": "default"}},
        )
        assert saved_role["definition"]["provider_id"] is None
        reused = copy.deepcopy(inherited)
        reused["tasks"][0].update(
            role_id=saved_role["role_id"], role_version=saved_role["version"]
        )
        reused_asset = await service.save_asset("alice", {"definition": reused})
        reused_run = await service.start_run(
            "alice", reused_asset["asset_id"], {"version": 1}
        )
        for _ in range(200):
            state = await service.get_run("alice", reused_run["run_id"])
            if state["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)
        assert state["status"] == "completed", state
        assert observed[4]["snapshot"]["effective"]["prompt"] == "Reusable duties"
        assert observed[4]["snapshot"]["effective"]["provider_id"] == "model-a"
    finally:
        await service.shutdown()
        await db.engine.dispose()

    reopened_db = SQLiteDatabase(str(tmp_path / "roles.db"))
    reopened_service = WorkflowService(
        reopened_db, RoleAssetService(reopened_db, catalog_loader=catalog)
    )
    try:
        reopened_v2 = await reopened_service.get_asset("alice", asset["asset_id"], 2)
        assert reopened_v2["definition"]["tasks"][0]["role_version"] == 2
        assert (
            reopened_v2["definition"]["tasks"][0]["overrides"]
            == definition["tasks"][0]["overrides"]
        )
        assert (await reopened_service.get_run("alice", observed[0]["run_id"]))[
            "snapshot"
        ]["effective"] == effective
        async with reopened_db.get_db() as session:
            await session.execute(
                delete(AgentRoleVersion).where(
                    AgentRoleVersion.role_id == role["role_id"],
                    AgentRoleVersion.version == 2,
                )
            )
            await session.commit()
        readiness = await reopened_service.check_readiness(
            "alice", asset["asset_id"], 2
        )
        assert not readiness["ready"]
        assert readiness["issues"][0]["path"] == "definition.tasks.0.role_id"
        with pytest.raises(RoleAssetError) as unavailable:
            await reopened_service.start_run("alice", asset["asset_id"], {"version": 2})
        assert unavailable.value.fields[0]["path"] == "definition.tasks.0.role_id"
    finally:
        await reopened_service.shutdown()
        await reopened_db.engine.dispose()
