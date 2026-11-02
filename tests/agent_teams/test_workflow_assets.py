"""Single-task assets and runs through the shared public service boundary."""

import asyncio
import copy
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_ports import TeamPorts


@pytest.mark.asyncio
async def test_saved_workflow_versions_reopen_and_pin_the_role(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "workflows.db"))
    roles = RoleAssetService(db)
    role = await roles.save_role(
        "alice", {"definition": {"name": "Writer", "prompt": "Write evidence."}}
    )
    service = WorkflowService(db, roles)
    payload = {
        "definition": {
            "name": "Report",
            "required_inputs": ["topic"],
            "tasks": [
                {
                    "id": "writer",
                    "name": "Writer",
                    "member": "writer",
                    "role_id": role["role_id"],
                    "role_version": 1,
                    "task": "Research {topic}.",
                    "inputs": {"topic": {"input": "topic"}},
                }
            ],
        }
    }
    asset = await service.save_asset("alice", payload)
    payload["definition"]["name"] = "Report v2"
    payload["expected_version"] = 1
    await service.save_asset("alice", payload, asset["asset_id"])
    await db.engine.dispose()
    reopened_db = SQLiteDatabase(str(tmp_path / "workflows.db"))
    reopened = WorkflowService(reopened_db, RoleAssetService(reopened_db))
    try:
        first = (await reopened.get_asset("alice", asset["asset_id"], 1))["definition"]
        assert first["name"] == "Report"
        assert first["tasks"][0]["role_id"] == role["role_id"]
        assert first["tasks"][0]["role_version"] == 1
        assert (await reopened.list_assets("alice"))["assets"][0]["version"] == 2
        with pytest.raises(RoleAssetError, match="not found"):
            await reopened.get_asset("bob", asset["asset_id"])
        assert await reopened_db.get_platform_sessions_by_creator("alice") == []
    finally:
        await reopened_db.engine.dispose()


@pytest.mark.asyncio
async def test_run_persists_before_dispatch_and_finishes_only_by_explicit_submission(
    tmp_path,
):
    db = SQLiteDatabase(str(tmp_path / "runs.db"))

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "model-a"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    roles = RoleAssetService(db, catalog_loader=catalog)
    role = await roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Writer",
                "prompt": "Check evidence.",
                "provider_id": "model-a",
                "tools": [],
            },
            "installation": {"profile_id": "default"},
        },
    )
    observed = []
    collected = asyncio.Event()
    release = asyncio.Event()

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        saved = await service.get_run("alice", binding.run_id)
        assert saved["state"]["message_id"] == message_id
        assert saved["snapshot"]["member"]["session_id"] == session_id
        assert saved["state"]["attempt_id"] == binding.turn_id
        assert saved["snapshot"]["member"]["conversation_id"] == binding.conversation_id
        observed.append(saved)
        return message_id

    async def collect(session_id, message_id, member_id):
        collected.set()
        await release.wait()
        tools = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")
        submit = next(tool for tool in tools if tool.name == "submit_task_result")
        assert "Error" in await submit.call(None, text=" ")
        assert await submit.call(None, text="Full report.") == "Result accepted."
        assert await submit.call(None, text="Full report.") == "Result accepted."
        return "Done", [{"type": "plain", "text": "Done"}]

    service = WorkflowService(
        db,
        roles,
        ports_factory=lambda username: TeamPorts(
            deliver=deliver,
            collect=collect,
            is_busy=lambda sid: False,
            emit=lambda e: None,
        ),
    )
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Report",
                "required_inputs": ["topic"],
                "tasks": [
                    {
                        "id": "writer",
                        "name": "Writer",
                        "member": "writer",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "Research {topic}.",
                        "inputs": {"topic": {"input": "topic"}},
                    }
                ],
            }
        },
    )
    try:
        with pytest.raises(RoleAssetError) as missing:
            await service.start_run("alice", asset["asset_id"], {"version": 1})
        assert missing.value.fields[0]["path"] == "inputs.topic"
        assert (await service.list_runs("alice"))["runs"] == []
        run = await service.start_run(
            "alice",
            asset["asset_id"],
            {
                "version": 1,
                "inputs": {"topic": "energy"},
                "source": "dashboard",
            },
        )
        await asyncio.wait_for(collected.wait(), 2)
        assert (await service.get_run("alice", run["run_id"]))["status"] == "running"
        release.set()
        for _ in range(200):
            current = await service.get_run("alice", run["run_id"])
            if current["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert current["status"] == "completed", current
        assert await service.get_result("alice", run["run_id"]) == {
            "text": "Full report."
        }
        assert observed[0]["snapshot"]["task"] == "Research energy."
        assert observed[0]["snapshot"]["effective"]["provider_id"] == "model-a"
        await service.finish_submission("alice", current["tasks"][0]["run_id"])
        assert await service.get_result("alice", run["run_id"]) == {
            "text": "Full report."
        }
        with pytest.raises(RoleAssetError, match="not found"):
            await service.get_result("bob", run["run_id"])
    finally:
        await service.shutdown()
        await db.engine.dispose()


@pytest_asyncio.fixture
async def workflow_env(tmp_path):
    """Provide real persistence and a scripted execution port for public scenarios.

    Args:
        tmp_path: Isolated data directory.

    Yields:
        Public services, mutable external resource inventory and model observations.
    """
    db = SQLiteDatabase(str(tmp_path / "workflows.db"))
    inventory = {
        "profiles": [{"id": "default"}],
        "models": [{"id": "model-a"}],
        "personas": [],
        "tools": [],
        "skills": [],
        "knowledge_bases": [],
    }

    async def catalog():
        return inventory

    roles = RoleAssetService(db, catalog_loader=catalog)
    role = await roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Writer",
                "prompt": "Original instructions",
                "provider_id": "model-a",
                "max_steps": 4,
            },
            "installation": {"profile_id": "default"},
        },
    )
    observations = []

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        observations.append((binding, text, context))
        return message_id

    async def collect(session_id, message_id, member_id):
        tool = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[0]
        await tool.call(None, text="Complete report. " * 1000)
        return "Done", [{"type": "think", "text": "Verified."}]

    service = WorkflowService(
        db,
        roles,
        ports_factory=lambda username: TeamPorts(
            deliver=deliver,
            collect=collect,
            is_busy=lambda sid: False,
            emit=lambda e: None,
        ),
    )
    asset = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Report",
                "tasks": [
                    {
                        "id": "writer",
                        "name": "Writer",
                        "member": "writer",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "Write a report",
                    }
                ],
            }
        },
    )
    try:
        yield SimpleNamespace(
            db=db,
            service=service,
            roles=roles,
            role=role,
            asset=asset,
            inventory=inventory,
            observations=observations,
        )
    finally:
        await service.shutdown()
        await db.engine.dispose()


async def completed_run(service, run_id):
    """Wait for the public query to report the deterministic execution outcome.

    Args:
        service: Shared public run service.
        run_id: Execution identity.

    Returns:
        Persisted terminal state.
    """
    for _ in range(200):
        run = await service.get_run("alice", run_id)
        if run["status"] != "running":
            return run
        await asyncio.sleep(0.01)
    pytest.fail("Run did not finish")


@pytest.mark.asyncio
async def test_new_asset_versions_and_runs_keep_prior_snapshot_and_member_context_isolated(
    workflow_env,
):
    env = workflow_env
    first = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    await completed_run(env.service, first["run_id"])
    payload = {
        "definition": {
            "name": "New Writer",
            "prompt": "New instructions",
            "max_steps": 2,
            "provider_id": "model-a",
        },
        "installation": {"profile_id": "default"},
        "expected_version": 1,
    }
    await env.roles.save_role("alice", payload, env.role["role_id"])
    definition = copy.deepcopy(env.asset["definition"])
    definition["name"] = "Report v2"
    definition["tasks"][0]["role_version"] = 2
    await env.service.save_asset(
        "alice",
        {"definition": definition, "expected_version": 1},
        env.asset["asset_id"],
    )
    second = await env.service.start_run("alice", env.asset["asset_id"], {"version": 2})
    await completed_run(env.service, second["run_id"])
    unchanged = (await env.service.get_run("alice", first["run_id"]))["tasks"][0]
    first = first["tasks"][0]
    second = second["tasks"][0]
    assert unchanged["snapshot"]["effective"]["prompt"] == "Original instructions"
    assert unchanged["snapshot"]["effective"]["max_steps"] == 4
    assert second["snapshot"]["effective"]["prompt"] == "New instructions"
    assert second["snapshot"]["effective"]["max_steps"] == 2
    for field in ("member_id", "session_id", "umo", "conversation_id"):
        assert first["snapshot"]["member"][field] != second["snapshot"]["member"][field]
    assert first["state"]["attempt_id"] != second["state"]["attempt_id"]


@pytest.mark.asyncio
async def test_full_result_and_process_reopen_without_active_execution(workflow_env):
    env = workflow_env
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    await completed_run(env.service, run["run_id"])
    await env.service.shutdown()
    await env.db.engine.dispose()
    reopened_db = SQLiteDatabase(env.db.db_path)
    reopened = WorkflowService(reopened_db, RoleAssetService(reopened_db))
    try:
        assert await reopened.get_result("alice", run["run_id"]) == {
            "text": "Complete report. " * 1000
        }
        queried = await reopened.get_run("alice", run["run_id"])
        assert queried["tasks"][0]["state"]["response"]["parts"] == [
            {"type": "think", "text": "Verified."}
        ]
        assert (await reopened.list_runs("alice"))["runs"][0]["status"] == "completed"
    finally:
        await reopened_db.engine.dispose()


@pytest.mark.asyncio
async def test_saved_result_intent_can_finish_idempotently_after_completion_write_fails(
    workflow_env,
):
    env = workflow_env
    async with env.db.get_db() as session:
        await session.execute(
            text(
                "CREATE TRIGGER reject_completion BEFORE UPDATE OF status ON "
                "agent_workflow_runs WHEN NEW.status = 'completed' "
                "BEGIN SELECT RAISE(FAIL, 'completion write failed'); END"
            )
        )
        await session.commit()
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    failed = await completed_run(env.service, run["run_id"])
    assert failed["status"] == "blocked"
    failed = failed["tasks"][0]
    assert failed["status"] == "failed"
    assert failed["pending_result"] == {"text": "Complete report. " * 1000}
    assert failed["result"] is None
    assert failed["state"]["safe_to_finish"] is True
    async with env.db.get_db() as session:
        await session.execute(text("DROP TRIGGER reject_completion"))
        await session.commit()
    await env.service.shutdown()
    reopened = WorkflowService(env.db, env.roles)
    await reopened.boot_sweep()
    completed = await reopened.finish_submission("alice", failed["run_id"])
    assert completed["status"] == "completed"
    assert completed["result"] == failed["pending_result"]
    assert await reopened.finish_submission("alice", failed["run_id"]) == completed
    assert len(env.observations) == 1


@pytest.mark.asyncio
async def test_concurrent_idempotent_finish_does_not_exhaust_real_database_pool(
    workflow_env,
):
    env = workflow_env
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    expected = await completed_run(env.service, run["run_id"])
    snapshots = await asyncio.wait_for(
        asyncio.gather(
            *(env.service.finish_submission("alice", run["run_id"]) for _ in range(15))
        ),
        timeout=5,
    )
    assert all(snapshot == expected for snapshot in snapshots)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "forbidden", "disabled"])
async def test_removed_or_disallowed_bindings_cannot_fall_back_to_another_model(
    workflow_env, failure
):
    env = workflow_env
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    await completed_run(env.service, run["run_id"])
    if failure == "missing":
        env.inventory["models"] = [{"id": "replacement-model"}]
    elif failure == "forbidden":
        env.inventory["profiles"][0]["provider_pool"] = ["replacement-model"]
    else:
        env.inventory["models"][0]["enabled"] = False
    with pytest.raises(RoleAssetError) as rejected:
        await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    assert rejected.value.fields[0]["path"] == "definition.tasks.0.role.provider_id"
    assert len(env.observations) == 1
    assert (await env.service.get_run("alice", run["run_id"]))["tasks"][0]["snapshot"][
        "effective"
    ]["provider_id"] == "model-a"


@pytest.mark.asyncio
async def test_model_losing_required_capability_before_dispatch_is_blocked(
    workflow_env,
):
    env = workflow_env
    env.inventory["models"][0]["capabilities"] = ["image"]
    definition = dict(env.role["definition"], model_capabilities=["image"])
    await env.roles.save_role(
        "alice",
        {
            "definition": definition,
            "installation": env.role["installation"],
            "expected_version": 1,
        },
        env.role["role_id"],
    )
    changed = copy.deepcopy(env.asset["definition"])
    changed["tasks"][0]["role_version"] = 2
    await env.service.save_asset(
        "alice",
        {
            "definition": changed,
            "expected_version": 1,
        },
        env.asset["asset_id"],
    )
    lookups = 0

    async def inventory():
        nonlocal lookups
        lookups += 1
        catalog = copy.deepcopy(env.inventory)
        if lookups >= 3:
            catalog["models"][0]["capabilities"] = []
        return catalog

    env.roles.catalog_loader = inventory
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 2})
    blocked = await completed_run(env.service, run["run_id"])
    assert blocked["status"] == "blocked"
    blocked = blocked["tasks"][0]
    assert blocked["state"]["issues"][0]["code"] == "INCOMPATIBLE"
    assert blocked["snapshot"]["role"]["definition"]["model_capabilities"] == ["image"]
    assert env.observations == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task",
    ["Read {topic.name}", "Read {unknown}", "Read {topic!r}", "Read {topic:>20}"],
)
async def test_task_templates_cannot_evaluate_attributes_or_undeclared_inputs(
    workflow_env, task
):
    env = workflow_env
    definition = copy.deepcopy(env.asset["definition"])
    definition["required_inputs"] = ["topic"]
    definition["tasks"][0]["task"] = task
    definition["tasks"][0]["inputs"] = {"topic": {"input": "topic"}}
    with pytest.raises(RoleAssetError) as invalid:
        await env.service.save_asset("alice", {"definition": definition})
    assert invalid.value.fields[0]["path"] == "definition.tasks.0.task"
    assert len((await env.service.list_assets("alice"))["assets"]) == 1
