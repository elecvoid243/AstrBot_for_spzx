"""Portable workflow behavior at the shared asset and run service boundary."""

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
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_export_includes_definition_but_excludes_local_installation(workflow_env):
    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    assert package["format_version"] == 1
    assert package["asset_id"] == env.asset["asset_id"]
    assert package["version"] == 1
    assert package["roles"][0]["definition"]["prompt"] == "Original instructions"
    assert package["definition"]["task"] == "Write a report"
    serialized = json.dumps(package)
    for private in (
        "model-a",
        "profile_id",
        "bindings",
        "session_id",
        "conversation_id",
    ):
        assert private not in serialized
    assert (await env.service.list_runs("alice"))["runs"] == []


@pytest.mark.asyncio
async def test_export_makes_inherited_persona_prompt_visible_without_knowledge_content(
    workflow_env,
):
    env = workflow_env
    env.inventory["personas"] = [
        {"id": "private-persona", "prompt": "Actual persona instructions."}
    ]
    env.inventory["tools"] = [{"id": "private-tool"}]
    env.inventory["knowledge_bases"] = [
        {
            "id": "private-kb",
            "name": "Private evidence",
            "content": "DO_NOT_EXPORT_KNOWLEDGE",
        }
    ]
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Persona writer",
                "persona_slot": "persona",
                "tools": ["evidence"],
                "knowledge_bases": ["facts"],
            },
            "installation": {
                "bindings": {
                    "model": "model-a",
                    "persona": "private-persona",
                    "evidence": "private-tool",
                    "facts": "private-kb",
                }
            },
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Persona report",
                "role_id": role["role_id"],
                "role_version": 1,
                "task": "Use evidence",
            }
        },
    )
    package = await env.service.export_asset("alice", asset["asset_id"], 1)
    exported = package["roles"][0]
    assert exported["definition"]["prompt"] == "Actual persona instructions."
    assert exported["prompt_source"] == "persona"
    assert exported["definition"]["tools"] == ["tools_1"]
    assert exported["definition"]["knowledge_bases"] == ["knowledge_bases_1"]
    assert {dependency["kind"] for dependency in exported["dependencies"]} == {
        "profiles",
        "models",
        "tools",
        "knowledge_bases",
    }
    for private in [
        "private-persona",
        "private-tool",
        "private-kb",
        "DO_NOT_EXPORT_KNOWLEDGE",
    ]:
        assert private not in json.dumps(package)


@pytest.mark.asyncio
async def test_export_accepts_roles_completed_by_actual_task_overrides(workflow_env):
    env = workflow_env
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Task-defined responsibility",
                "tools": [],
                "skills": [],
                "knowledge_bases": [],
            },
            "installation": {"bindings": {"model": "model-a"}},
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Task prompt",
                "role_id": role["role_id"],
                "role_version": 1,
                "task": "Write evidence",
                "overrides": {"prompt": "Actual task responsibility."},
            }
        },
    )
    assert (await env.service.check_readiness("alice", asset["asset_id"], 1))[
        "ready"
    ] is True
    package = await env.service.export_asset("alice", asset["asset_id"], 1)
    assert package["roles"][0]["definition"]["prompt"] == "Actual task responsibility."
    assert package["roles"][0]["prompt_source"] == "task"
    assert package["definition"]["overrides"]["prompt"] == "Actual task responsibility."


@pytest.mark.asyncio
async def test_dependencies_keep_logical_purpose_and_reject_incompatible_tool_contracts(
    workflow_env,
):
    env = workflow_env
    schema = {
        "type": "object",
        "properties": {"company": {"type": "string"}},
        "required": ["company"],
    }
    env.inventory["tools"] = [
        {
            "id": "private-financial-tool",
            "description": "Search approved company financial filings",
            "input_schema": schema,
        }
    ]
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Financial researcher",
                "prompt": "Review filings",
                "tools": ["financial-filings-search"],
            },
            "installation": {
                "bindings": {
                    "model": "model-a",
                    "financial-filings-search": "private-financial-tool",
                }
            },
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Filings",
                "role_id": role["role_id"],
                "role_version": 1,
                "task": "Review evidence",
            }
        },
    )
    package = await env.service.export_asset("alice", asset["asset_id"], 1)
    dependency = next(
        dep for dep in package["roles"][0]["dependencies"] if dep["kind"] == "tools"
    )
    assert dependency["logical_slots"] == ["financial-filings-search"]
    assert dependency["description"] == "Search approved company financial filings"
    assert dependency["input_schema"] == schema
    assert "private-financial-tool" not in json.dumps(package)
    env.inventory["tools"] = [
        {
            "id": "weather",
            "description": "Weather only",
            "input_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
            },
        }
    ]
    key = f"{role['role_id']}:1"
    preview = await env.service.preview_import(
        "bob",
        {
            "package": package,
            "installations": {
                key: {"bindings": {"model": "model-a", "tools_1": "weather"}}
            },
        },
    )
    assert preview["ready"] is False
    assert (
        next(
            dep for dep in preview["roles"][0]["dependencies"] if dep["kind"] == "tools"
        )["candidates"]
        == []
    )


@pytest.mark.asyncio
async def test_export_keeps_inherited_knowledge_purpose_without_local_identity(
    workflow_env,
):
    env = workflow_env
    env.inventory["profiles"][0]["knowledge_bases"] = ["Corporate financial evidence"]
    env.inventory["knowledge_bases"] = [
        {
            "id": "source-kb-uuid",
            "name": "Corporate financial evidence",
            "description": "",
        }
    ]
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {"name": "Inherited evidence", "prompt": "Review the facts"},
            "installation": {"bindings": {"model": "model-a"}},
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Financial facts",
                "role_id": role["role_id"],
                "role_version": 1,
                "task": "Review evidence",
            }
        },
    )
    assert (await env.service.check_readiness("alice", asset["asset_id"], 1))[
        "ready"
    ] is True
    package = await env.service.export_asset("alice", asset["asset_id"], 1)
    dependency = next(
        dep
        for dep in package["roles"][0]["dependencies"]
        if dep["kind"] == "knowledge_bases"
    )
    assert dependency["display_name"] == "Corporate financial evidence"
    assert "source-kb-uuid" not in json.dumps(package)


@pytest.mark.asyncio
async def test_different_instance_previews_unique_compatible_resources_before_install(
    workflow_env, tmp_path
):
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "same-adapter-model-source"
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    catalog = copy.deepcopy(env.inventory)
    catalog["profiles"][0]["id"] = "target-profile"
    catalog["models"][0]["id"] = "different-model-id"

    async def inventory():
        return catalog

    (tmp_path / "instance-b").mkdir()
    db = SQLiteDatabase(str(tmp_path / "instance-b" / "workflow.db"))
    service = WorkflowService(
        db,
        RoleAssetService(db, catalog_loader=inventory),
        ports_factory=env.service.ports_factory,
    )
    try:
        preview = await service.preview_import("bob", {"package": package})
        assert preview["ready"] is True
        binding = preview["roles"][0]["installation"]
        assert binding == {
            "profile_id": "target-profile",
            "bindings": {"model": "different-model-id"},
        }
        assert (await service.list_assets("bob"))["assets"] == []
        assert (await service.roles.list_roles("bob"))["roles"] == []
        installed = await service.import_asset("bob", {"package": package})
        assert installed["existing"] is False
        run = await service.start_run(
            "bob", installed["asset"]["asset_id"], {"version": 1}
        )
        for _ in range(200):
            current = await service.get_run("bob", run["run_id"])
            if current["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert current["status"] == "completed"
        assert current["snapshot"]["effective"]["provider_id"] == "different-model-id"
        assert (await service.get_result("bob", run["run_id"]))[
            "text"
        ] == "Complete report. " * 1000
        duplicate = await service.import_asset("bob", {"package": package})
        assert duplicate["existing"] is True
        assert duplicate["asset"] == installed["asset"]
        assert len((await service.list_assets("bob"))["assets"]) == 1
        assert len((await service.roles.list_roles("bob"))["roles"]) == 1
    finally:
        await service.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_pending_import_can_be_configured_without_duplicate_assets(workflow_env):
    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    preview = await env.service.preview_import("alice", {"package": package})
    assert preview["ready"] is False
    assert preview["roles"][0]["dependencies"][1]["status"] == "confirmation_required"
    installed = await env.service.import_asset(
        "alice", {"package": package, "save_pending": True}
    )
    asset_id = installed["asset"]["asset_id"]
    with pytest.raises(RoleAssetError):
        await env.service.start_run("alice", asset_id, {"version": 1})
    assert (await env.service.list_runs("alice"))["runs"] == []
    key = preview["roles"][0]["key"]
    configured = await env.service.import_asset(
        "alice",
        {
            "package": package,
            "installations": {
                key: {"profile_id": "default", "bindings": {"model": "model-a"}}
            },
        },
    )
    assert configured["existing"] is True
    assert configured["asset"]["asset_id"] == asset_id
    assert (await env.service.check_readiness("alice", asset_id, 1))["ready"] is True
    assert (await env.service.preview_import("alice", {"package": package}))[
        "ready"
    ] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["version", "reference", "contract", "extra", "dependency", "inheritance"]
)
async def test_invalid_packages_create_neither_assets_nor_roles(workflow_env, change):
    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    if change == "version":
        package["format_version"] = 999
    elif change == "reference":
        package["definition"]["role_version"] = 42
    elif change == "contract":
        package["definition"]["output_contract"] = {
            "type": "object",
            "$ref": "https://example.invalid/schema",
        }
    elif change == "extra":
        package["script"] = "install-plugin"
    elif change == "dependency":
        package["roles"][0]["dependencies"] = []
    else:
        package["roles"][0]["definition"]["tools"] = None
    before_assets = await env.service.list_assets("alice")
    before_roles = await env.roles.list_roles("alice")
    with pytest.raises(RoleAssetError):
        await env.service.import_asset(
            "alice", {"package": package, "save_pending": True}
        )
    assert await env.service.list_assets("alice") == before_assets
    assert await env.roles.list_roles("alice") == before_roles
    assert (await env.service.list_runs("alice"))["runs"] == []


@pytest.mark.asyncio
async def test_same_name_is_not_equivalent_and_multiple_compatible_models_are_ambiguous(
    workflow_env,
):
    env = workflow_env
    env.inventory["models"][0].update(name="Shared name", fingerprint="source-one")
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    env.inventory["models"] = [
        {"id": "wrong", "name": "Shared name", "fingerprint": "source-two"}
    ]
    preview = await env.service.preview_import("alice", {"package": package})
    assert preview["roles"][0]["dependencies"][1]["status"] == "missing"
    env.inventory["models"] = [
        {"id": "b", "name": "Different label", "fingerprint": "source-one"},
        {"id": "c", "name": "Third label", "fingerprint": "source-one"},
    ]
    preview = await env.service.preview_import("alice", {"package": package})
    dep = preview["roles"][0]["dependencies"][1]
    assert dep["status"] == "ambiguous"
    assert dep["selected"] is None
    assert {candidate["id"] for candidate in dep["candidates"]} == {"b", "c"}
    with pytest.raises(RoleAssetError):
        await env.service.import_asset("alice", {"package": package})
    assert len((await env.service.list_assets("alice"))["assets"]) == 1


@pytest.mark.asyncio
async def test_same_identity_different_content_never_overwrites_installed_definition(
    workflow_env,
):
    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    installed = await env.service.import_asset(
        "alice", {"package": package, "save_pending": True}
    )
    changed = copy.deepcopy(package)
    changed["definition"]["task"] = "Changed responsibility"
    with pytest.raises(RoleAssetError) as conflict:
        await env.service.import_asset(
            "alice", {"package": changed, "save_pending": True}
        )
    assert conflict.value.status == 409
    assert (
        await env.service.get_asset("alice", installed["asset"]["asset_id"], 1)
        == installed["asset"]
    )
    assert (
        await env.service.export_asset("alice", installed["asset"]["asset_id"], 1)
        == package
    )


@pytest.mark.asyncio
async def test_confirmation_failure_leaves_no_partial_installation_and_retry_succeeds(
    workflow_env,
):
    """Fail the storage commit, then observe recovery only through public assets.

    Args:
        workflow_env: Isolated services and controllable external inventory.
    """
    from sqlalchemy import event

    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    before_assets = await env.service.list_assets("alice")
    before_roles = await env.roles.list_roles("alice")

    def unavailable_commit(connection):
        raise OSError("Storage unavailable at commit")

    event.listen(env.db.engine.sync_engine, "commit", unavailable_commit)
    try:
        with pytest.raises(OSError, match="Storage unavailable"):
            await env.service.import_asset(
                "alice", {"package": package, "save_pending": True}
            )
    finally:
        event.remove(env.db.engine.sync_engine, "commit", unavailable_commit)
    assert await env.service.list_assets("alice") == before_assets
    assert await env.roles.list_roles("alice") == before_roles
    assert (await env.service.list_runs("alice"))["runs"] == []
    installed = await env.service.import_asset(
        "alice", {"package": package, "save_pending": True}
    )
    assert installed["existing"] is False


@pytest.mark.asyncio
async def test_pending_package_dependencies_survive_service_restart(
    workflow_env, tmp_path
):
    """Keep unused declared resources mandatory after rebuilding the services.

    Args:
        workflow_env: Source services and controllable resource inventory.
        tmp_path: Data root used by the persistent public-service fixture.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "model-source-a"
    env.inventory["tools"] = [{"id": "tool-a", "description": "Company evidence"}]
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Researcher",
                "prompt": "Write evidence",
                "tools": ["evidence"],
            },
            "installation": {"bindings": {"model": "model-a", "evidence": "tool-a"}},
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "No tools for this task",
                "role_id": role["role_id"],
                "role_version": 1,
                "task": "Write report",
                "overrides": {"tools": []},
            }
        },
    )
    package = await env.service.export_asset("alice", asset["asset_id"], 1)
    env.inventory["tools"] = []
    installed = await env.service.import_asset(
        "bob", {"package": package, "save_pending": True}
    )
    assert installed["ready"] is False
    await env.service.shutdown()
    await env.db.engine.dispose()

    async def inventory():
        return env.inventory

    db = SQLiteDatabase(str(tmp_path / "workflows.db"))
    service = WorkflowService(
        db,
        RoleAssetService(db, catalog_loader=inventory),
        ports_factory=env.service.ports_factory,
    )
    try:
        ready = await service.check_readiness("bob", installed["asset"]["asset_id"], 1)
        preview = await service.preview_import("bob", {"package": package})
        assert ready["ready"] is False
        assert ready["issues"] == preview["issues"]
        with pytest.raises(RoleAssetError):
            await service.start_run(
                "bob", installed["asset"]["asset_id"], {"version": 1}
            )
        assert (await service.list_runs("bob"))["runs"] == []
        assert (
            await service.get_asset("bob", installed["asset"]["asset_id"], 1)
            == installed["asset"]
        )
    finally:
        await service.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["model_fingerprint", "tool_schema"])
@pytest.mark.parametrize("graph", [False, True])
async def test_installed_constraints_block_drift_after_restart_without_changing_runs(
    workflow_env, tmp_path, drift, graph
):
    """Recheck stored requirements while retaining the previously frozen run.

    Args:
        workflow_env: Source services and controllable resource inventory.
        tmp_path: Persistent database directory shared across service lifecycles.
        drift: Compatibility metadata changed without changing the bound ID.
        graph: Whether the imported asset uses the graph launch path.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "model-source-a"
    env.inventory["tools"] = [
        {
            "id": "tool-a",
            "description": "Company evidence",
            "input_schema": {
                "type": "object",
                "properties": {"company": {"type": "string"}},
            },
        }
    ]
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Researcher",
                "prompt": "Write evidence",
                "tools": ["evidence"],
            },
            "installation": {"bindings": {"model": "model-a", "evidence": "tool-a"}},
        },
    )
    task = {
        "name": "Research",
        "role_id": role["role_id"],
        "role_version": 1,
        "task": "Write report",
    }
    definition = (
        {
            "name": "Research graph",
            "tasks": [{**task, "id": "research", "member": "researcher"}],
        }
        if graph
        else task
    )
    asset = await env.service.save_asset("alice", {"definition": definition})
    package = await env.service.export_asset("alice", asset["asset_id"], 1)
    key = f"{role['role_id']}:1"
    installed = await env.service.import_asset(
        "bob",
        {
            "package": package,
            "installations": {
                key: {
                    "profile_id": "default",
                    "bindings": {"model": "model-a", "tools_1": "tool-a"},
                }
            },
        },
    )
    run = await env.service.start_run(
        "bob", installed["asset"]["asset_id"], {"version": 1}
    )
    for _ in range(200):
        prior = await env.service.get_run("bob", run["run_id"])
        if prior["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert prior["status"] == "completed"
    frozen = copy.deepcopy(prior["snapshot"])
    await env.service.shutdown()
    await env.db.engine.dispose()
    if drift == "model_fingerprint":
        env.inventory["models"][0]["fingerprint"] = "different-source"
    else:
        env.inventory["tools"][0]["input_schema"] = {
            "type": "object",
            "properties": {"city": {"type": "string"}},
        }

    async def inventory():
        return env.inventory

    db = SQLiteDatabase(str(tmp_path / "workflows.db"))
    service = WorkflowService(
        db,
        RoleAssetService(db, catalog_loader=inventory),
        ports_factory=env.service.ports_factory,
    )
    try:
        preview = await service.preview_import("bob", {"package": package})
        ready = await service.check_readiness("bob", installed["asset"]["asset_id"], 1)
        assert preview["ready"] is False
        assert ready["ready"] is False
        assert ready["issues"] == preview["issues"]
        with pytest.raises(RoleAssetError):
            await service.start_run(
                "bob", installed["asset"]["asset_id"], {"version": 1}
            )
        assert len((await service.list_runs("bob"))["runs"]) == 1
        assert (await service.get_run("bob", run["run_id"]))["snapshot"] == frozen
        assert (
            await service.export_asset("bob", installed["asset"]["asset_id"], 1)
            == package
        )
    finally:
        await service.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["model_fingerprint", "tool_schema"])
async def test_waiting_package_rechecks_frozen_bindings_before_execution_and_retry(
    workflow_env, drift
):
    """Block incompatible execution until the original binding is restored.

    Args:
        workflow_env: Public services, execution observations and local inventory.
        drift: Same-ID dependency that becomes incompatible during human waiting.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "original-source"
    schema = {"type": "object", "properties": {"company": {"type": "string"}}}
    env.inventory["tools"] = [{"id": "tool-a", "input_schema": schema}]
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Waiting researcher",
                "prompt": "Submit evidence",
                "tools": ["evidence"],
            },
            "installation": {"bindings": {"model": "model-a", "evidence": "tool-a"}},
        },
    )
    asset = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Wait before research",
                "tasks": [
                    {
                        "id": "gate",
                        "name": "Question",
                        "human": {"kind": "question", "question": "Continue?"},
                    },
                    {
                        "id": "research",
                        "name": "Research",
                        "member": "researcher",
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": "Write evidence",
                        "overrides": {"tools": []},
                    },
                ],
                "edges": [{"source": "gate", "target": "research"}],
            }
        },
    )
    package = await env.service.export_asset("alice", asset["asset_id"], 1)
    key = f"{role['role_id']}:1"
    installed = await env.service.import_asset(
        "bob",
        {
            "package": package,
            "installations": {
                key: {
                    "profile_id": "default",
                    "bindings": {"model": "model-a", "tools_1": "tool-a"},
                }
            },
        },
    )
    identity = installed["asset"]["asset_id"]
    run = await env.service.start_run("bob", identity, {"version": 1})
    for _ in range(200):
        waiting = await env.service.get_run("bob", run["run_id"])
        if waiting["todos"]:
            break
        await asyncio.sleep(0.01)
    assert waiting["todos"]
    frozen = copy.deepcopy(waiting["snapshot"])
    if drift == "model_fingerprint":
        env.inventory["models"][0]["fingerprint"] = "incompatible-source"
    else:
        env.inventory["tools"][0]["input_schema"] = {"type": "array"}
    await env.service.answer_todo(
        "bob",
        run["run_id"],
        waiting["todos"][0]["todo_id"],
        {"request_id": "answer", "action": "answer", "answer": "Continue"},
    )
    for _ in range(200):
        blocked = await env.service.get_run("bob", run["run_id"])
        if blocked["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert blocked["status"] == "blocked", blocked
    assert env.observations == []
    assert blocked["snapshot"] == frozen
    research = next(
        task for task in blocked["tasks"] if task["snapshot"]["node_id"] == "research"
    )
    assert research["status"] == "blocked"
    task_snapshot = copy.deepcopy(research["snapshot"])
    assert research["state"]["issues"]

    env.inventory["models"].append(
        {"id": "replacement-model", "fingerprint": "original-source"}
    )
    env.inventory["tools"].append({"id": "replacement-tool", "input_schema": schema})
    await env.service.import_asset(
        "bob",
        {
            "package": package,
            "installations": {
                key: {
                    "profile_id": "default",
                    "bindings": {
                        "model": "replacement-model",
                        "tools_1": "replacement-tool",
                    },
                }
            },
        },
    )
    assert (await env.service.check_readiness("bob", identity, 1))["ready"] is True
    for request_id, restored in [
        ("retry-with-current-bindings", False),
        ("retry-restored-original", True),
    ]:
        if restored:
            env.inventory["models"][0]["fingerprint"] = "original-source"
            env.inventory["tools"][0]["input_schema"] = schema
        await env.service.retry_task(
            "bob",
            research["run_id"],
            {
                "request_id": request_id,
                "attempt_id": research["state"]["attempt_id"],
                "input_policy": "original",
            },
        )
        for _ in range(200):
            current = await env.service.get_run("bob", run["run_id"])
            if current["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert current["status"] == ("completed" if restored else "blocked"), current
        assert len(env.observations) == (1 if restored else 0)
        assert current["snapshot"] == frozen
        research = next(
            task
            for task in current["tasks"]
            if task["snapshot"]["node_id"] == "research"
        )
        assert research["snapshot"] == task_snapshot
        assert research["snapshot"]["effective"]["provider_id"] == "model-a"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("boundary", "continuation"),
    [
        ("response", "recover"),
        ("request", "recover"),
        ("tools_started", "recover"),
        ("tool_result", "recover"),
        ("tools_applied", "recover"),
        ("usage", "recover"),
        ("admit_work", "recover"),
        ("response", "retry"),
    ],
)
async def test_inflight_package_block_retains_issues_and_recovers_saved_response(
    workflow_env, tmp_path, boundary, continuation
):
    """Keep resource rejection distinct from storage failure at a saved response.

    Args:
        workflow_env: Public services and controllable resource inventory.
        tmp_path: Original SQLite directory reopened before response recovery.
        boundary: Normal checkpoint or admission callback after resource drift.
        continuation: Recover the saved Attempt or explicitly retry a new one.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "original-source"
    entered, release = asyncio.Event(), asyncio.Event()
    model_calls = []
    ports = env.service.ports_factory("bob")

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        """Save the original response after the external model call returns.

        Args:
            session_id: Frozen member conversation.
            text: Task input.
            context: Contracted result instructions.
            execution_token: Authorized normal execution binding.
            message_id: Current delivery identity.

        Returns:
            Delivery identity after the response is safe to apply.
        """
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        if binding.checkpoint is None:
            model_calls.append(env.inventory["models"][0]["fingerprint"])
            checkpoint = {
                "phase": "response",
                "invocation_id": "original-model-call",
                "model_calls": 1,
                "steps_used": 1,
                "messages": [],
                "tools": [],
                "response": {"role": "assistant", "completion_text": "Evidence"},
            }
            if boundary != "response":
                await binding.save_checkpoint(checkpoint)
            entered.set()
            await release.wait()
            if boundary == "usage":
                await binding.begin_usage(
                    {
                        "receipt_id": "next-call",
                        "invocation_id": "next-call",
                        "kind": "model",
                    }
                )
            elif boundary == "admit_work":
                await binding.admit_work()
            else:
                await binding.save_checkpoint({**checkpoint, "phase": boundary})
        else:
            assert binding.checkpoint["phase"] == (
                boundary if boundary in {"tool_result", "tools_applied"} else "response"
            )
            assert binding.checkpoint["invocation_id"] == "original-model-call"
        return message_id

    ports.deliver = deliver
    service = WorkflowService(env.db, env.roles, ports_factory=lambda username: ports)
    reopened_db = None
    try:
        package = await service.export_asset("alice", env.asset["asset_id"], 1)
        installed = await service.import_asset("bob", {"package": package})
        run = await service.start_run(
            "bob", installed["asset"]["asset_id"], {"version": 1}
        )
        await asyncio.wait_for(entered.wait(), 2)
        before = await service.get_run("bob", run["run_id"])
        env.inventory["models"][0]["fingerprint"] = "incompatible-source"
        release.set()
        for _ in range(200):
            blocked = await service.get_run("bob", run["run_id"])
            if blocked["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert blocked["status"] == "blocked", blocked
        assert blocked["state"]["reason"] == "Frozen resources are no longer available"
        assert any(
            issue["path"] == "installation.bindings.model"
            and issue["code"] == "INCOMPATIBLE"
            for issue in blocked["state"]["issues"]
        )
        assert blocked["snapshot"] == before["snapshot"]
        assert blocked["state"]["attempt_id"] == before["state"]["attempt_id"]
        assert blocked["pending_result"] is None
        assert blocked["artifacts"] == []
        assert blocked["recovery"]["boundary"] == (
            boundary if boundary in {"tool_result", "tools_applied"} else "response"
        )
        assert blocked["recovery"]["available"] is True
        assert model_calls == ["original-source"]
        await service.shutdown()
        await env.db.engine.dispose()
        reopened_db = SQLiteDatabase(str(tmp_path / "workflows.db"))
        service = WorkflowService(
            reopened_db,
            RoleAssetService(reopened_db, catalog_loader=env.roles.catalog_loader),
            ports_factory=lambda username: ports,
        )
        env.inventory["models"][0]["fingerprint"] = "original-source"
        if continuation == "recover":
            await service.recover_run("bob", run["run_id"])
        else:
            await service.retry_task(
                "bob",
                run["run_id"],
                {
                    "request_id": "retry-restored-original",
                    "attempt_id": before["state"]["attempt_id"],
                    "input_policy": "original",
                },
            )
        for _ in range(200):
            recovered = await service.get_run("bob", run["run_id"])
            if recovered["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert recovered["status"] == "completed", recovered
        assert recovered["snapshot"] == before["snapshot"]
        if continuation == "recover":
            assert recovered["state"]["attempt_id"] == before["state"]["attempt_id"]
            assert model_calls == ["original-source"]
        else:
            assert recovered["state"]["attempt_id"] != before["state"]["attempt_id"]
            assert model_calls == ["original-source", "original-source"]
            previous = next(
                attempt
                for attempt in recovered["attempts"]
                if attempt["attempt_id"] == before["state"]["attempt_id"]
            )
            assert previous["state"]["reason"] == blocked["state"]["reason"]
            assert previous["state"]["issues"] == blocked["state"]["issues"]
        assert any(event["kind"] == "task.blocked" for event in recovered["events"])
    finally:
        release.set()
        await service.shutdown()
        if reopened_db is not None:
            await reopened_db.engine.dispose()


@pytest.mark.asyncio
async def test_paused_package_rechecks_resources_on_continue_and_recovers_original_attempt(
    workflow_env, tmp_path
):
    """Resume a saved execution only while its original resources are compatible.

    Args:
        workflow_env: Public asset services and controllable resource inventory.
        tmp_path: Original SQLite data directory reopened before recovery.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "original-source"
    entered, release = asyncio.Event(), asyncio.Event()
    permitted = []

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        """Park at the execution port before its next external work boundary.

        Args:
            session_id: Frozen member conversation.
            text: Task input.
            context: Task result instructions.
            execution_token: Authorized execution binding.
            message_id: Stable message identity.

        Returns:
            Original delivery identity after permission is granted.
        """
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        entered.set()
        await release.wait()
        await binding.admit_work()
        permitted.append(message_id)
        return message_id

    async def collect(session_id, message_id, member_id):
        """Submit a contracted result through the actual execution tool.

        Args:
            session_id: Frozen member conversation.
            message_id: Stable delivery identity.
            member_id: Frozen member identity.

        Returns:
            Collected response and message parts.
        """
        tool = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")[0]
        await tool.call(None, text="Compatible evidence")
        return "Done", []

    service = WorkflowService(
        env.db,
        env.roles,
        ports_factory=lambda username: TeamPorts(
            deliver=deliver,
            collect=collect,
            is_busy=lambda sid: False,
            emit=lambda event: None,
        ),
    )
    reopened_db = None
    try:
        package = await service.export_asset("alice", env.asset["asset_id"], 1)
        installed = await service.import_asset("bob", {"package": package})
        run = await service.start_run(
            "bob", installed["asset"]["asset_id"], {"version": 1}
        )
        await asyncio.wait_for(entered.wait(), 2)
        before = await service.get_run("bob", run["run_id"])
        frozen = copy.deepcopy(before["snapshot"])
        attempt = before["state"]["attempt_id"]
        await service.control_run(
            "bob", run["run_id"], {"request_id": "pause", "action": "pause"}
        )
        env.inventory["models"][0]["fingerprint"] = "incompatible-source"
        release.set()
        await service.control_run(
            "bob", run["run_id"], {"request_id": "continue", "action": "continue"}
        )
        for _ in range(200):
            blocked = await service.get_run("bob", run["run_id"])
            if blocked["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert blocked["status"] == "blocked", blocked
        assert permitted == []
        assert blocked["snapshot"] == frozen
        assert blocked["state"]["attempt_id"] == attempt
        await service.shutdown()
        await env.db.engine.dispose()
        reopened_db = SQLiteDatabase(str(tmp_path / "workflows.db"))
        service = WorkflowService(
            reopened_db,
            RoleAssetService(reopened_db, catalog_loader=env.roles.catalog_loader),
            ports_factory=service.ports_factory,
        )
        env.inventory["models"][0]["fingerprint"] = "original-source"
        await service.recover_run("bob", run["run_id"])
        for _ in range(200):
            recovered = await service.get_run("bob", run["run_id"])
            if recovered["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert recovered["status"] == "completed", recovered
        assert len(permitted) == 1
        assert recovered["snapshot"] == frozen
        assert recovered["state"]["attempt_id"] == attempt
    finally:
        release.set()
        await service.shutdown()
        if reopened_db is not None:
            await reopened_db.engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_confirmation_persists_bindings_after_pending_import(
    workflow_env,
):
    """Force a pending import to commit while a ready confirmation is previewing.

    Args:
        workflow_env: Isolated services and controllable resource inventory.
    """
    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    observed, release = asyncio.Event(), asyncio.Event()
    blocked = False

    async def inventory():
        nonlocal blocked
        if asyncio.current_task().get_name() == "ready-import" and not blocked:
            # Pause the first inventory read after the registry lookup so the
            # competing pending import wins the initial transaction.
            blocked = True
            observed.set()
            await release.wait()
        return env.inventory

    roles = RoleAssetService(env.db, catalog_loader=inventory)
    service = WorkflowService(env.db, roles)
    key = f"{package['roles'][0]['role_id']}:1"
    proposed = {"profile_id": "default", "bindings": {"model": "model-a"}}
    confirmation = asyncio.create_task(
        service.import_asset(
            "bob",
            {
                "package": package,
                "installations": {key: proposed},
            },
        ),
        name="ready-import",
    )
    try:
        await asyncio.wait_for(observed.wait(), 5)
        pending = await service.import_asset(
            "bob", {"package": package, "save_pending": True}
        )
        assert pending["ready"] is False
        release.set()
        confirmed = await asyncio.wait_for(confirmation, 5)
        assert confirmed["existing"] is True
        assert confirmed["ready"] is True
        assert confirmed["asset"]["asset_id"] == pending["asset"]["asset_id"]
        assert (
            await service.check_readiness("bob", confirmed["asset"]["asset_id"], 1)
        )["ready"] is True
        assert len((await service.list_assets("bob"))["assets"]) == 1
        saved_roles = (await roles.list_roles("bob"))["roles"]
        assert len(saved_roles) == 1
        assert saved_roles[0]["installation"] == proposed
    finally:
        release.set()
        await confirmation
        await service.shutdown()


@pytest.mark.asyncio
async def test_package_http_reports_invalid_contract_before_resolving_graph_fields(
    workflow_env,
):
    """Reject the malformed predecessor schema before its successor reads a field.

    Args:
        workflow_env: Public services used by the authenticated HTTP routes.
    """
    from types import SimpleNamespace

    import httpx
    from fastapi import FastAPI

    from astrbot.dashboard.api.auth import require_dashboard_user
    from astrbot.dashboard.api.workflow_assets import router

    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    task = {
        "id": "first",
        "name": "First",
        "member": "first",
        "role_id": env.role["role_id"],
        "role_version": 1,
        "task": "Write report",
    }
    package["definition"] = {
        "name": "Malformed contract",
        "tasks": [
            {**task, "output_contract": {"type": "object", "allOf": 1}},
            {
                **task,
                "id": "second",
                "member": "second",
                "inputs": {"evidence": {"task_id": "first", "field": "text"}},
            },
        ],
        "edges": [{"source": "first", "target": "second"}],
    }
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "bob"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        for route in ("import/preview", "import"):
            response = await client.post(
                f"/api/v1/workflow_assets/{route}",
                json={"package": package, "save_pending": True},
            )
            assert response.status_code in {400, 422}, response.text
            assert "definition.tasks.0.output_contract" in json.dumps(response.json())
    assert (await env.service.list_assets("bob"))["assets"] == []
    assert (await env.roles.list_roles("bob"))["roles"] == []
    assert (await env.service.list_runs("bob"))["runs"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("format_version", [True, 1.0])
async def test_package_http_rejects_noninteger_format_without_installation(
    workflow_env, format_version
):
    """Keep original JSON types significant at the typed HTTP boundary.

    Args:
        workflow_env: Public services used by the authenticated HTTP routes.
        format_version: A boolean or float that compares equal to integer one.
    """
    from types import SimpleNamespace

    import httpx
    from fastapi import FastAPI

    from astrbot.dashboard.api.auth import require_dashboard_user
    from astrbot.dashboard.api.workflow_assets import router

    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "same-source"
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    package["format_version"] = format_version
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "bob"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        for route in ("import/preview", "import"):
            response = await client.post(
                f"/api/v1/workflow_assets/{route}",
                json={"package": package, "save_pending": True},
            )
            assert response.status_code in {400, 422}, response.text
    assert (await env.service.list_assets("bob"))["assets"] == []
    assert (await env.roles.list_roles("bob"))["roles"] == []
    assert (await env.service.list_runs("bob"))["runs"] == []


@pytest.mark.asyncio
async def test_package_http_routes_share_ownership_and_validation(workflow_env):
    from types import SimpleNamespace

    import httpx
    from fastapi import FastAPI

    from astrbot.dashboard.api.auth import require_dashboard_user
    from astrbot.dashboard.api.workflow_assets import router

    env = workflow_env
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        exported = await client.get(
            f"/api/v1/workflow_assets/{env.asset['asset_id']}/export?version=1"
        )
        assert exported.status_code == 200
        package = exported.json()["data"]
        preview = await client.post(
            "/api/v1/workflow_assets/import/preview", json={"package": package}
        )
        assert preview.status_code == 200
        assert preview.json()["data"]["ready"] is False
        imported = await client.post(
            "/api/v1/workflow_assets/import",
            json={"package": package, "save_pending": True},
        )
        assert imported.status_code == 200
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        assert (
            await client.get(
                f"/api/v1/workflow_assets/{imported.json()['data']['asset']['asset_id']}/export"
            )
        ).status_code == 404
