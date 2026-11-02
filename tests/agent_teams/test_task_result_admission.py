"""Intrinsic task result admission through persisted public asset services."""

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
@pytest.mark.parametrize("runner", ["coze", "deerflow", "dify", "dashscope"])
@pytest.mark.parametrize("declaration", [None, []])
@pytest.mark.parametrize(
    "kind",
    ["single", "graph", "coordinator", "allowed", "nested_team", "nested_workflow"],
)
async def test_default_text_task_requires_result_protocol_before_dispatch(
    tmp_path, runner, declaration, kind
):
    """Reject an otherwise configured remote runner before creating active work.

    Args:
        tmp_path: Isolated durable database and public observation record.
        runner: Integrated executor without the result protocol.
        declaration: Omitted or explicitly empty optional role requirements.
        kind: Public task, graph, autonomous or nested asset entry.
    """
    db = SQLiteDatabase(str(tmp_path / "assets.db"))

    async def inventory():
        return {
            "profiles": [
                {
                    "id": "default",
                    "runner_type": runner,
                    "runner_config": {
                        "coze_api_key": "controlled-test-key",
                        "bot_id": "controlled-bot",
                        "dify_api_type": "chat",
                        "dashscope_app_id": "controlled-app",
                        "dashscope_api_key": "controlled-test-key",
                    },
                },
                {"id": "local"},
            ],
            "models": [{"id": "model-a"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    roles = RoleAssetService(db, catalog_loader=inventory)
    definition = {"name": "Researcher", "prompt": "Check evidence."}
    if declaration is not None:
        definition["runner_capabilities"] = declaration
    role = await roles.save_role("alice", {"definition": definition})
    calls = []

    async def deliver(session_id, text, context=None, **kwargs):
        calls.append(text)
        return kwargs["message_id"]

    async def collect(session_id, message_id, member_id):
        return "Ordinary answer.", [{"type": "plain", "text": "Ordinary answer."}]

    service = WorkflowService(
        db,
        roles,
        ports_factory=lambda owner: TeamPorts(
            deliver=deliver,
            collect=collect,
            is_busy=lambda sid: False,
            emit=lambda e: None,
        ),
    )
    try:
        local = await roles.save_role(
            "alice",
            {
                "definition": {
                    "name": "Coordinator",
                    "prompt": "Coordinate evidence.",
                    "provider_id": "model-a",
                },
                "installation": {"profile_id": "local"},
            },
        )
        ref = {"role_id": role["role_id"], "role_version": 1}
        local_ref = {"role_id": local["role_id"], "role_version": 1}
        task = {"name": "Research", **ref, "task": "Check evidence."}
        team = {
            "kind": "autonomous",
            "name": "Team",
            "coordinator": {
                "id": "lead",
                **(ref if kind == "coordinator" else local_ref),
            },
            "allowed_roles": [
                {"id": "worker", **(local_ref if kind == "coordinator" else ref)}
            ],
        }
        definition = (
            {
                "name": "Graph",
                "result_task_id": "research",
                "tasks": [{"id": "research", "member": "research", **task}],
            }
            if kind in {"single", "graph"}
            else team
        )
        if kind == "graph":
            definition["tasks"].insert(
                0,
                {
                    "id": "local",
                    "name": "Local",
                    "member": "local",
                    **local_ref,
                    "task": "Prepare evidence.",
                },
            )
            definition["edges"] = [{"source": "local", "target": "research"}]
        if kind == "nested_team":
            child = await service.save_asset("alice", {"definition": team})
            definition = {
                "name": "Parent graph",
                "result_task_id": "team",
                "required_inputs": ["goal"],
                "tasks": [
                    {
                        "id": "team",
                        "name": "Child team",
                        "team": {"asset_id": child["asset_id"], "version": 1},
                        "inputs": {"goal": {"input": "goal"}},
                    }
                ],
            }
        elif kind == "nested_workflow":
            child = await service.save_asset(
                "alice",
                {
                    "definition": {
                        "name": "Child graph",
                        "result_task_id": "research",
                        "tasks": [{"id": "research", "member": "research", **task}],
                    }
                },
            )
            definition = {
                **team,
                "allowed_roles": [{"id": "worker", **local_ref}],
                "allowed_workflows": [
                    {
                        "asset_id": child["asset_id"],
                        "version": 1,
                    }
                ],
            }
        asset = await service.save_asset(
            "alice",
            {"definition": definition},
        )
        readiness = await service.check_readiness("alice", asset["asset_id"], 1)
        error = None
        run = None
        try:
            run = await service.start_run(
                "alice",
                asset["asset_id"],
                {
                    "version": 1,
                    "inputs": {"goal": "Check evidence."}
                    if definition.get("kind") == "autonomous"
                    or definition.get("required_inputs")
                    else {},
                },
            )
        except RoleAssetError as exc:
            error = exc.fields
        if run is not None:
            for _ in range(200):
                run = await service.get_run("alice", run["run_id"])
                if run["status"] != "running":
                    break
                await asyncio.sleep(0.01)
        observed = {
            "readiness": readiness,
            "rejected": error,
            "calls": calls,
            "run": run,
        }
        (tmp_path / "admission.json").write_text(
            json.dumps(observed, indent=2), encoding="utf-8"
        )
        assert readiness["ready"] is False, observed
        assert (await roles.resolve_role("alice", role["role_id"], 1))["ready"] is True
        assert readiness["issues"] == error
        assert "task_result" in error[0]["message"]
        assert calls == []
        assert (await service.list_runs("alice"))["runs"] == []
        assert await db.get_platform_sessions_by_creator("alice") == []
        assert error[0]["path"] == (
            "definition.tasks.1.role_id"
            if kind == "graph"
            else "definition.tasks.0.role_id"
            if kind == "single"
            else "definition.coordinator.role_id"
            if kind == "coordinator"
            else "definition.allowed_roles.0.role_id"
            if kind == "allowed"
            else "definition.tasks.0.team.allowed_roles.0.role_id"
            if kind == "nested_team"
            else "definition.allowed_workflows.0.tasks.0.role_id"
        )
    finally:
        await service.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_frozen_graph_rechecks_current_resources_before_recovery(tmp_path):
    """Block a frozen graph when its selected model disappears, then recover it.

    Args:
        tmp_path: Isolated durable database reopened for recovery.
    """
    db = SQLiteDatabase(str(tmp_path / "recovery.db"))
    catalog = {
        "profiles": [{"id": "default"}],
        "models": [{"id": "model-a"}],
        "personas": [],
        "tools": [],
        "skills": [],
        "knowledge_bases": [],
    }

    async def inventory():
        return catalog

    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        if binding.checkpoint is None:
            calls.append(message_id)
            await binding.save_checkpoint(
                {
                    "phase": "response",
                    "invocation_id": "original",
                    "model_calls": 1,
                    "steps_used": 1,
                    "messages": [],
                    "tools": [],
                    "response": {"role": "assistant", "completion_text": "Evidence"},
                }
            )
            entered.set()
            await release.wait()
        await binding.admit_work()
        return message_id

    async def collect(session_id, message_id, member_id):
        tools = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")
        submit = next(tool for tool in tools if tool.name == "submit_task_result")
        await submit.call(None, text="Evidence")
        return "Evidence", []

    ports = TeamPorts(
        deliver=deliver,
        collect=collect,
        is_busy=lambda sid: False,
        emit=lambda event: None,
    )
    roles = RoleAssetService(db, catalog_loader=inventory)
    service = WorkflowService(db, roles, ports_factory=lambda owner: ports)
    reopened_db = None
    try:
        asset = await service.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Research",
                    "result_task_id": "research",
                    "tasks": [
                        {
                            "id": "research",
                            "name": "Research",
                            "member": "research",
                            "role": {
                                "name": "Researcher",
                                "prompt": "Check evidence.",
                                "provider_id": "model-a",
                            },
                            "installation": {"profile_id": "default"},
                            "task": "Check evidence.",
                        }
                    ],
                }
            },
        )
        run = await service.start_run("alice", asset["asset_id"], {"version": 1})
        await asyncio.wait_for(entered.wait(), 5)
        before = await service.get_run("alice", run["run_id"])
        catalog["models"] = []
        release.set()
        for _ in range(200):
            blocked = await service.get_run("alice", run["run_id"])
            if blocked["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert blocked["status"] == "blocked", blocked
        assert blocked["snapshot"] == before["snapshot"]
        assert len(calls) == 1
        await service.shutdown()
        await db.engine.dispose()
        reopened_db = SQLiteDatabase(str(tmp_path / "recovery.db"))
        service = WorkflowService(
            reopened_db,
            RoleAssetService(reopened_db, catalog_loader=inventory),
            ports_factory=lambda owner: ports,
        )
        await service.boot_sweep()
        task_run_id = blocked["tasks"][0]["run_id"]
        with pytest.raises(RoleAssetError):
            await service.recover_run("bob", task_run_id)
        await service.recover_run("alice", task_run_id)
        for _ in range(200):
            still_blocked = await service.get_run("alice", task_run_id)
            blocked_events = [
                event
                for event in still_blocked["events"]
                if event["kind"] == "task.blocked"
            ]
            if len(blocked_events) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(blocked_events) >= 2, still_blocked
        assert still_blocked["status"] == "blocked", still_blocked
        assert (await service.get_run("alice", run["run_id"]))["snapshot"] == before[
            "snapshot"
        ]
        assert len(calls) == 1
        catalog["models"] = [{"id": "model-a"}]
        await service.recover_run("alice", task_run_id)
        for _ in range(200):
            recovered = await service.get_run("alice", run["run_id"])
            if recovered["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        assert recovered["status"] == "completed", recovered
        assert recovered["snapshot"] == before["snapshot"]
        assert len(calls) == 1
    finally:
        release.set()
        await service.shutdown()
        await (reopened_db or db).engine.dispose()


@pytest.mark.asyncio
async def test_import_requires_result_protocol_before_atomic_install(tmp_path):
    """A target executor without result submission cannot install a package.

    Args:
        tmp_path: Isolated durable source and target installations.
    """
    db = SQLiteDatabase(str(tmp_path / "import.db"))
    catalog = {
        "profiles": [
            {
                "id": "default",
                "runner_type": "dify",
                "runner_config": {"dify_api_type": "workflow"},
            }
        ],
        "models": [],
        "personas": [],
        "tools": [],
        "skills": [],
        "knowledge_bases": [],
    }

    async def inventory():
        return catalog

    roles = RoleAssetService(db, catalog_loader=inventory)
    service = WorkflowService(
        db, roles, ports_factory=lambda owner: pytest.fail("Import must not dispatch")
    )
    try:
        role = await roles.save_role(
            "alice", {"definition": {"name": "Remote", "prompt": "Check evidence."}}
        )
        asset = await service.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Evidence",
                    "result_task_id": "check",
                    "tasks": [
                        {
                            "id": "check",
                            "name": "Check",
                            "member": "check",
                            "role_id": role["role_id"],
                            "role_version": 1,
                            "task": "Check evidence.",
                        }
                    ],
                }
            },
        )
        package = await service.export_asset("alice", asset["asset_id"], 1)
        dependency = next(
            item for item in package["requirements"] if item["kind"] == "profiles"
        )
        assert "task_result" in dependency["capabilities"]
        untrusted = copy.deepcopy(package)
        untrusted["requirements"][0]["capabilities"] = []
        with pytest.raises(RoleAssetError):
            await service.preview_import("bob", {"package": untrusted})
        catalog["profiles"][0]["runner_config"]["dify_api_type"] = "chat"
        selections = [
            {"kind": "profiles", "source_id": "default", "target_id": "default"}
        ]
        payload = {"package": package, "selections": selections}
        preview = await service.preview_import("bob", payload)
        assert not preview["ready"]
        assert preview["requirements"][0]["candidates"][0]["disabled"]
        with pytest.raises(RoleAssetError):
            await service.import_asset("bob", payload)
        assert (await service.list_assets("bob"))["assets"] == []
        assert (await service.list_runs("bob"))["runs"] == []
        assert await db.get_platform_sessions_by_creator("bob") == []
    finally:
        await service.shutdown()
        await db.engine.dispose()
