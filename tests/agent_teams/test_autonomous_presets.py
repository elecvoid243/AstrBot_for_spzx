"""Autonomous presets preserve identities and frozen direct resources."""

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.autonomous_tools import TeamOperationRequest
from astrbot.core.agent_teams.workflows import (
    WorkflowService,
    WorkflowStartRequest,
    _capacity_key,
)
from astrbot.core.db.sqlite import SQLiteDatabase


@pytest.mark.asyncio
async def test_presets_round_trip_and_freeze_with_shared_template(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "presets.db"))

    async def catalog():
        return {
            "profiles": [{"id": "default", "persona_id": "chat-persona"}],
            "models": [{"id": "model", "capabilities": ["tool_use"]}],
            "personas": [
                {"id": "chat-persona", "prompt": "Wrong default"},
                {"id": "persona", "prompt": "Role persona"},
            ],
            "tools": [{"id": "tool"}],
            "skills": [],
            "knowledge_bases": [],
        }

    roles = RoleAssetService(db, catalog_loader=catalog)
    service = WorkflowService(db, roles)
    try:
        template = await roles.save_role(
            "alice",
            {
                "definition": {
                    "name": "Template",
                    "prompt": "Base",
                    "persona_id": "persona",
                    "tools": ["tool"],
                    "max_instances": 2,
                },
                "installation": {"profile_id": "default"},
            },
        )
        definition = {
            "kind": "autonomous",
            "name": "Team",
            "default_provider_id": "model",
            "coordinator": {
                "id": "coord",
                "role": {"name": "Coordinator", "prompt": "Coordinate"},
                "installation": {"profile_id": "default"},
            },
            "allowed_roles": [
                {
                    "id": "a",
                    "role_id": template["role_id"],
                    "role_version": 1,
                    "overrides": {"persona_id": None, "tools": []},
                },
                {
                    "id": "b",
                    "role_id": template["role_id"],
                    "role_version": 1,
                    "overrides": {"prompt": "Other"},
                },
                {
                    "id": "c",
                    "role": {"name": "Direct", "prompt": "Work", "tools": []},
                    "installation": {"profile_id": "default"},
                },
            ],
        }
        asset = await service.save_asset("alice", {"definition": definition})
        reopened = await service.get_asset("alice", asset["asset_id"], 1)
        assert reopened["definition"]["allowed_roles"][0]["overrides"] == {
            "persona_id": None,
            "tools": [],
        }
        readiness = await service.check_readiness("alice", asset["asset_id"], 1)
        assert readiness["ready"], readiness
        frozen = await service._freeze_graph(
            "alice", asset, WorkflowStartRequest(version=1, inputs={"goal": "x"})
        )
        assert set(frozen["allowed_roles"]) == {"a", "b", "c"}
        assert frozen["allowed_roles"]["a"]["effective"]["persona_id"] is None
        assert frozen["allowed_roles"]["a"]["effective"]["tools"] == []
        assert frozen["allowed_roles"]["b"]["effective"]["prompt"] == "Other"
        assert frozen["allowed_roles"]["c"]["effective"]["persona_id"] is None
        assert all(
            item["effective"]["provider_id"] == "model"
            for item in frozen["allowed_roles"].values()
        )
        with pytest.raises(RoleAssetError):
            await service.save_asset(
                "alice",
                {
                    "definition": {
                        **definition,
                        "coordinator": {
                            "id": "bad",
                            "role": {"name": "Missing profile"},
                            "installation": {},
                        },
                    }
                },
            )
        duplicate = {
            **definition,
            "allowed_roles": [
                definition["allowed_roles"][0],
                definition["allowed_roles"][0],
            ],
        }
        with pytest.raises(RoleAssetError):
            await service.save_asset("alice", {"definition": duplicate})
        inline = {
            "asset_id": asset["asset_id"],
            "definition": {},
            "preset_id": "c",
            "member": {"member_id": "first"},
        }
        assert _capacity_key(inline) == _capacity_key(
            {**inline, "member": {"member_id": "second"}}
        )
        with pytest.raises(ValueError):
            TeamOperationRequest.model_validate(
                {"action": "create_member", "request_id": "x", "role_id": "old"}
            )
    finally:
        await service.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_template_parallel_limit_is_shared_by_distinct_presets(tmp_path):
    """Schedule two real member tasks against a shared template quota."""
    import asyncio
    import json

    from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
    from astrbot.core.agent_team_tools import AgentTeamToolRegistry
    from astrbot.dashboard.services.agent_team_ports import TeamPorts

    db = SQLiteDatabase(str(tmp_path / "parallel.db"))

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "model", "capabilities": ["tool_use"]}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    roles = RoleAssetService(db, catalog_loader=catalog)
    bindings = {}
    first_started = asyncio.Event()
    release = asyncio.Event()
    started = []
    failures = []

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        bindings[session_id] = AgentTeamExecutionRegistry.resolve(execution_token)
        return message_id

    async def collect(session_id, message_id, member_id):
        tools = {
            tool.name: tool
            for tool in AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
        }
        try:
            if bindings[session_id].node_id == "coordinator":
                members = []
                for preset in ("first", "second"):
                    result = await tools["team_manage"].call(
                        None,
                        action="create_member",
                        request_id=preset,
                        preset_id=preset,
                        name="Same name",
                    )
                    members.append(json.loads(result)["member_id"])
                tasks = []
                for index, member in enumerate(members):
                    result = await tools["team_manage"].call(
                        None,
                        action="dispatch",
                        request_id=f"task-{index}",
                        member_id=member,
                        name="Task",
                        task="Work",
                    )
                    tasks.append(json.loads(result)["task_id"])
                await tools["team_manage"].call(
                    None, action="wait", request_id="wait", task_ids=tasks
                )
                await tools["submit_task_result"].call(None, text="Done")
            else:
                started.append(member_id)
                first_started.set()
                await release.wait()
                await tools["submit_task_result"].call(None, text=member_id)
        except Exception as exc:
            failures.append(exc)
            raise
        return "Done", []

    service = WorkflowService(
        db,
        roles,
        ports_factory=lambda _: TeamPorts(
            deliver=deliver,
            collect=collect,
            is_busy=lambda _: False,
            emit=lambda _: None,
            reply_timeout=5,
        ),
    )
    try:
        template = await roles.save_role(
            "alice",
            {
                "definition": {
                    "name": "Worker",
                    "prompt": "Work",
                    "provider_id": "model",
                    "max_instances": 2,
                    "max_parallel": 1,
                },
                "installation": {"profile_id": "default"},
            },
        )
        asset = await service.save_asset(
            "alice",
            {
                "definition": {
                    "kind": "autonomous",
                    "name": "Parallel",
                    "default_provider_id": "model",
                    "max_parallel": 2,
                    "coordinator": {
                        "id": "coordinator",
                        "role": {"name": "Coordinator", "prompt": "Coordinate"},
                        "installation": {"profile_id": "default"},
                    },
                    "allowed_roles": [
                        {
                            "id": "first",
                            "role_id": template["role_id"],
                            "role_version": 1,
                        },
                        {
                            "id": "second",
                            "role_id": template["role_id"],
                            "role_version": 1,
                            "overrides": {"max_parallel": 2},
                        },
                    ],
                }
            },
        )
        run = await service.start_run(
            "alice", asset["asset_id"], {"version": 1, "inputs": {"goal": "Work"}}
        )
        await asyncio.wait_for(first_started.wait(), 10)
        await asyncio.sleep(0.3)
        assert len(started) == 1
        release.set()
        for _ in range(500):
            current = await service.get_run("alice", run["run_id"])
            if current["status"] in {"completed", "failed", "blocked"}:
                break
            await asyncio.sleep(0.02)
        assert not failures
        assert current["status"] == "completed", current
        assert len(started) == len(set(started)) == 2
        member_tasks = [
            task for task in current["tasks"] if not task["snapshot"].get("coordinator")
        ]
        assert {task["result"]["text"] for task in member_tasks} == set(started)
    finally:
        release.set()
        await service.shutdown()
        await db.engine.dispose()
