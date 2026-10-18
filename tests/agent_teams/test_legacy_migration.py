"""Definition migration through public services and real persistence."""

import json
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from sqlalchemy import event
from types import SimpleNamespace
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from astrbot.dashboard.api.agent_teams import router as team_router
from astrbot.dashboard.api.auth import require_dashboard_user
from astrbot.dashboard.api.workflow_assets import router as workflow_router

import pytest
from astrbot.core.agent_teams.assets import RoleAssetError

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.migration import LegacyMigrationService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase


@pytest.mark.asyncio
async def test_preview_confirm_read_and_repeat_preserve_legacy_definition(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "migration.db"))
    await db.create_agent_team(
        team_id="old-team",
        owner_username="alice",
        name="Research team",
        coordinator_member_id="lead",
        config={"max_parallel": 2},
        members=[
            {
                "member_id": "lead",
                "name": "Lead",
                "session_id": "old-lead",
                "provider_id": "model-a",
                "system_prompt": "Coordinate.",
            },
            {
                "member_id": "writer",
                "name": "Writer",
                "session_id": "old-writer",
                "provider_id": "model-a",
                "system_prompt": "Write evidence.",
                "runner_config": {"tools": [], "max_steps": 9},
            },
        ],
    )
    await db.create_agent_team_workflow(
        workflow_id="old-report",
        team_id="old-team",
        name="Report",
        graph={
            "nodes": [
                {
                    "id": "research",
                    "member_id": "lead",
                    "title": "Research",
                    "task": "Research {{input}}",
                },
                {
                    "id": "write",
                    "member_id": "writer",
                    "title": "Write",
                    "task": "Write from {{research}}",
                    "execution": {"max_steps": 11},
                },
            ],
            "edges": [{"from": "research", "to": "write"}],
        },
        layout={"research": {"x": 40, "y": 80}, "write": {"x": 340, "y": 80}},
    )

    async def catalog():
        return {
            "profiles": [{"id": "default", "max_steps": 30}],
            "models": [{"id": "model-a", "capabilities": ["tool_use"]}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    roles = RoleAssetService(db, catalog_loader=catalog)
    workflows = WorkflowService(db, roles)
    migration = LegacyMigrationService(db, roles, workflows)
    try:
        preview = await migration.preview("alice", "old-team")
        assert (await workflows.list_assets("alice"))["assets"] == []
        assert (await roles.list_roles("alice"))["roles"] == []
        report = next(a for a in preview["assets"] if a["source_id"] == "old-report")
        assert report["definition"]["edges"] == [
            {"source": "research", "target": "write"}
        ]
        assert report["definition"]["tasks"][1]["position"] == {"x": 340, "y": 80}
        assert report["definition"]["tasks"][1]["inputs"] == {
            "research": {"task_id": "research", "field": "text"}
        }
        result = await migration.confirm(
            "alice",
            "old-team",
            {"preview_token": preview["preview_token"], "save_pending": True},
        )
        assert result["existing"] is False
        mapping = result["mapping"]
        saved = await workflows.get_asset(
            "alice", mapping["workflows"]["old-report"]["asset_id"]
        )
        writer = await roles.get_role(
            "alice", saved["definition"]["tasks"][1]["role_id"]
        )
        assert (await roles.preview_role(writer))["effective"]["max_steps"] == 11
        assert (await roles.preview_role(writer))["effective"]["tools"] == []
        assert "old-writer" not in json.dumps(saved)
        assert (await workflows.check_readiness("alice", saved["asset_id"]))[
            "ready"
        ] is True
        repeated = await migration.confirm(
            "alice",
            "old-team",
            {"preview_token": preview["preview_token"], "save_pending": True},
        )
        assert repeated["existing"] is True
        assert repeated["mapping"] == mapping
        assert len((await workflows.list_assets("alice"))["assets"]) == 2
        assert (await db.get_agent_team_workflow("old-report")).layout["write"] == {
            "x": 340,
            "y": 80,
        }
        assert await db.get_platform_sessions_by_creator("alice") == []
    finally:
        await workflows.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing", ["profile", "persona", "tool", "capability", "legacy_setting"]
)
async def test_missing_or_incompatible_dependencies_cannot_start_via_migrated_assets(
    tmp_path, missing
):
    """Keep every unavailable or unmappable role blocked at the public launch boundary.

    Args:
        tmp_path: Isolated durable database.
        missing: The resource or legacy semantic that cannot be satisfied.
    """
    db = SQLiteDatabase(str(tmp_path / "dependencies.db"))
    await db.create_agent_team(
        team_id="dependencies",
        owner_username="alice",
        name="Dependencies",
        coordinator_member_id="lead",
        config={},
        members=[
            {
                "member_id": "lead",
                "name": "Lead",
                "provider_id": "model",
                "system_prompt": "Coordinate.",
            },
            {
                "member_id": "worker",
                "name": "Worker",
                "provider_id": "model",
                "persona_id": "editor",
                "system_prompt": "Write.",
                "runner_config": {
                    "config_id": "strict",
                    "tools": ["search"],
                    **(
                        {"unsupported_exit": "return early"}
                        if missing == "legacy_setting"
                        else {}
                    ),
                },
            },
        ],
    )
    inventory = {
        "profiles": [{"id": "default"}, {"id": "strict"}],
        "models": [{"id": "model", "capabilities": ["tool_use"]}],
        "personas": [{"id": "editor", "prompt": "Review.", "tools": ["search"]}],
        "tools": [{"id": "search"}],
        "skills": [],
        "knowledge_bases": [],
    }

    async def catalog():
        return inventory

    roles = RoleAssetService(db, catalog_loader=catalog)
    workflows = WorkflowService(db, roles)
    migration = LegacyMigrationService(db, roles, workflows)
    try:
        if missing == "profile":
            inventory["profiles"].pop()
        elif missing == "persona":
            inventory["personas"].clear()
        elif missing == "tool":
            inventory["tools"][0]["enabled"] = False
        preview = await migration.preview("alice", "dependencies")
        result = await migration.confirm(
            "alice",
            "dependencies",
            {"preview_token": preview["preview_token"], "save_pending": True},
        )
        if missing == "capability":
            inventory["models"][0]["capabilities"] = []
        identity = result["mapping"]["team"]["asset_id"]
        asset = await workflows.get_asset("alice", identity)
        reviewed = dict(asset["definition"])
        reviewed.pop("migration", None)
        await workflows.save_asset(
            "alice", {"definition": reviewed, "expected_version": 1}, identity
        )
        assert not (await workflows.check_readiness("alice", identity))["ready"]
        with pytest.raises(RoleAssetError):
            await workflows.start_run(
                "alice", identity, {"version": 2, "inputs": {"goal": "Fresh task"}}
            )
        assert (await workflows.list_runs("alice"))["runs"] == []
        if missing == "legacy_setting":
            worker = await roles.get_role(
                "alice", result["mapping"]["members"]["worker"]["role_id"]
            )
            assert not (await roles.preview_role(worker))["ready"]
            assert (
                worker["definition"]["migration"]["source"]["execution"][
                    "unsupported_exit"
                ]
                == "return early"
            )
    finally:
        await workflows.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_confirmation_reuses_receipt_and_stale_source_is_rejected(
    tmp_path,
):
    """Retry concurrent confirmations without overwriting edits or duplicating definitions.

    Args:
        tmp_path: Isolated shared SQLite persistence.
    """
    db = SQLiteDatabase(str(tmp_path / "concurrent.db"))
    await db.create_agent_team(
        team_id="concurrent",
        owner_username="alice",
        name="Concurrent",
        coordinator_member_id="lead",
        config={},
        members=[
            {
                "member_id": "lead",
                "name": "Lead",
                "provider_id": "model",
                "system_prompt": "Coordinate.",
            },
            {
                "member_id": "worker",
                "name": "Worker",
                "provider_id": "model",
                "system_prompt": "Work.",
            },
        ],
    )

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "model"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    roles = RoleAssetService(db, catalog_loader=catalog)
    workflows = WorkflowService(db, roles)
    migration = LegacyMigrationService(db, roles, workflows)
    try:
        preview = await migration.preview("alice", "concurrent")
        results = await asyncio.gather(
            *(
                migration.confirm(
                    "alice",
                    "concurrent",
                    {"preview_token": preview["preview_token"], "save_pending": True},
                )
                for _ in range(2)
            )
        )
        assert results[0]["mapping"] == results[1]["mapping"]
        assert sorted(r["existing"] for r in results) == [False, True]
        assert len((await workflows.list_assets("alice"))["assets"]) == 1
        assert len((await roles.list_roles("alice"))["roles"]) == 2
        await db.update_agent_team("concurrent", name="Edited old definition")
        changed = await migration.preview("alice", "concurrent")
        with pytest.raises(RoleAssetError) as conflict:
            await migration.confirm(
                "alice", "concurrent", {"preview_token": changed["preview_token"]}
            )
        assert conflict.value.status == 409
        assert (await workflows.list_assets("alice"))["assets"][0]["definition"][
            "name"
        ] == "Concurrent"
    finally:
        await workflows.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_declarative_subflow_with_one_result_maps_to_existing_graph_execution(
    tmp_path,
):
    db = SQLiteDatabase(str(tmp_path / "subflow.db"))
    await db.create_agent_team(
        team_id="nested",
        owner_username="alice",
        name="Nested",
        coordinator_member_id="lead",
        config={},
        members=[
            {
                "member_id": "lead",
                "name": "Lead",
                "provider_id": "model",
                "system_prompt": "Coordinate.",
            },
            {
                "member_id": "worker",
                "name": "Worker",
                "provider_id": "model",
                "system_prompt": "Write.",
            },
        ],
    )
    await db.create_agent_team_workflow(
        workflow_id="nested-flow",
        team_id="nested",
        name="Nested report",
        graph={
            "nodes": [
                {
                    "id": "group",
                    "title": "Evidence",
                    "subflow": {
                        "nodes": [
                            {
                                "id": "draft",
                                "member_id": "worker",
                                "task": "Draft {{input}}",
                            },
                            {
                                "id": "review",
                                "member_id": "lead",
                                "task": "Review {{draft}}",
                            },
                        ],
                        "edges": [{"from": "draft", "to": "review"}],
                        "layout": {
                            "draft": {"x": 0, "y": 0},
                            "review": {"x": 200, "y": 0},
                        },
                    },
                },
                {"id": "publish", "member_id": "worker", "task": "Publish {{group}}"},
            ],
            "edges": [{"from": "group", "to": "publish"}],
        },
        layout={"group": {"x": 20, "y": 50}, "publish": {"x": 440, "y": 50}},
    )

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "model"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    roles = RoleAssetService(db, catalog_loader=catalog)
    workflows = WorkflowService(db, roles)
    migration = LegacyMigrationService(db, roles, workflows)
    try:
        preview = await migration.preview("alice", "nested")
        assert not next(
            asset for asset in preview["assets"] if asset["kind"] == "workflow"
        )["issues"], preview["issues"]
        result = await migration.confirm(
            "alice",
            "nested",
            {"preview_token": preview["preview_token"], "save_pending": True},
        )
        saved = await workflows.get_asset(
            "alice", result["mapping"]["workflows"]["nested-flow"]["asset_id"]
        )
        assert saved["definition"]["edges"] == [
            {"source": "group-draft", "target": "group-review"},
            {"source": "group-review", "target": "publish"},
        ]
        assert saved["definition"]["tasks"][1]["position"] == {"x": 220, "y": 50}
        assert saved["definition"]["tasks"][2]["inputs"] == {
            "group": {"task_id": "group-review", "field": "text"}
        }
        assert result["mapping"]["nodes"]["nested-flow:group/review"] == "group-review"
        assert (await workflows.check_readiness("alice", saved["asset_id"]))["ready"]
    finally:
        await workflows.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_failed_commit_and_killed_process_leave_no_executable_partial_assets(
    tmp_path,
):
    db = SQLiteDatabase(str(tmp_path / "atomic.db"))
    await db.create_agent_team(
        team_id="atomic",
        owner_username="alice",
        name="Atomic team",
        coordinator_member_id="lead",
        config={},
        members=[
            {
                "member_id": "lead",
                "name": "Lead",
                "provider_id": "model",
                "system_prompt": "Coordinate.",
            },
            {
                "member_id": "worker",
                "name": "Worker",
                "provider_id": "model",
                "system_prompt": "Work.",
            },
        ],
    )

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "model"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    roles = RoleAssetService(db, catalog_loader=catalog)
    workflows = WorkflowService(db, roles)
    migration = LegacyMigrationService(db, roles, workflows)

    def fail_commit(connection):
        raise OSError("Controlled storage commit failure")

    try:
        preview = await migration.preview("alice", "atomic")
        event.listen(db.engine.sync_engine, "commit", fail_commit, once=True)
        with pytest.raises(OSError, match="Controlled storage"):
            await migration.confirm(
                "alice",
                "atomic",
                {"preview_token": preview["preview_token"], "save_pending": True},
            )
        assert (await roles.list_roles("alice"))["roles"] == []
        assert (await workflows.list_assets("alice"))["assets"] == []
        assert not (await migration.preview("alice", "atomic"))["existing"]
    finally:
        await workflows.shutdown()
        await db.engine.dispose()
    root = Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        str(Path(__file__).with_name("migration_worker.py")),
        str(tmp_path),
    ]
    environment = {**os.environ, "PYTHONUTF8": "1"}
    with (tmp_path / "killed-worker.log").open("w", encoding="utf-8") as output:
        worker = subprocess.Popen(
            [*command, "hold"],
            cwd=root,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(300):
                if (tmp_path / "commit-ready.json").exists():
                    break
                assert worker.poll() is None, (
                    tmp_path / "killed-worker.log"
                ).read_text(encoding="utf-8")
                await asyncio.sleep(0.05)
            assert (tmp_path / "commit-ready.json").exists()
            worker.kill()
            worker.wait(timeout=10)
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait(timeout=10)
    restarted = await asyncio.to_thread(
        subprocess.run,
        [*command, "confirm"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    (tmp_path / "restarted-worker.log").write_text(
        restarted.stdout + restarted.stderr, encoding="utf-8"
    )
    assert restarted.returncode == 0, restarted.stdout + restarted.stderr
    evidence = json.loads((tmp_path / "restarted.json").read_text(encoding="utf-8"))
    assert evidence["before"] == {"roles": {"roles": []}, "assets": {"assets": []}}
    assert not evidence["result"]["existing"]
    assert len(evidence["assets"]["assets"]) == 1
    assert len(evidence["roles"]["roles"]) == 2


@pytest.mark.asyncio
async def test_http_preview_confirm_and_asset_read_respect_legacy_ownership(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "http.db"))
    await db.create_agent_team(
        team_id="http",
        owner_username="alice",
        name="HTTP team",
        coordinator_member_id="lead",
        config={},
        members=[
            {
                "member_id": "lead",
                "name": "Lead",
                "provider_id": "model",
                "system_prompt": "Coordinate.",
            },
            {
                "member_id": "worker",
                "name": "Worker",
                "provider_id": "model",
                "system_prompt": "Work.",
            },
        ],
    )

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
    workflows = WorkflowService(db, roles)
    app = FastAPI()
    app.state.services = SimpleNamespace(
        legacy_migration=LegacyMigrationService(db, roles, workflows),
        workflow_assets=workflows,
    )
    app.include_router(team_router, prefix="/api/v1")
    app.include_router(workflow_router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    try:
        async with AsyncClient(
            transport=ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/agent_teams/http/migration/preview")
            assert response.status_code == 200
            preview = response.json()["data"]
            stale = await client.post(
                "/api/v1/agent_teams/http/migration", json={"preview_token": "0" * 64}
            )
            assert stale.status_code == 409
            response = await client.post(
                "/api/v1/agent_teams/http/migration",
                json={"preview_token": preview["preview_token"], "save_pending": True},
            )
            assert response.status_code == 200
            identity = response.json()["data"]["mapping"]["team"]["asset_id"]
            assert (
                await client.get(f"/api/v1/workflow_assets/{identity}")
            ).status_code == 200
            app.dependency_overrides[require_dashboard_user] = lambda: "bob"
            assert (
                await client.get("/api/v1/agent_teams/http/migration/preview")
            ).status_code == 404
            assert (
                await client.post(
                    "/api/v1/agent_teams/http/migration",
                    json={
                        "preview_token": preview["preview_token"],
                        "save_pending": True,
                    },
                )
            ).status_code == 404
    finally:
        await workflows.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_compatible_bounded_loop_retains_contract_feedback_and_layout(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "loop.db"))
    await db.create_agent_team(
        team_id="loops",
        owner_username="alice",
        name="Loop team",
        coordinator_member_id="lead",
        config={},
        members=[
            {
                "member_id": "lead",
                "name": "Lead",
                "provider_id": "model",
                "system_prompt": "Coordinate.",
            },
            {
                "member_id": "worker",
                "name": "Worker",
                "provider_id": "model",
                "system_prompt": "Write.",
            },
        ],
    )
    loop = {
        "id": "revision",
        "name": "Revise",
        "body": ["draft", "review"],
        "exit_task_id": "review",
        "exit_condition": {"field": "approved", "operator": "eq", "value": True},
        "max_iterations": 3,
        "feedback": [
            {"target": "draft", "input": "input", "source": "review", "field": "text"}
        ],
    }
    await db.create_agent_team_workflow(
        workflow_id="loop-flow",
        team_id="loops",
        name="Review loop",
        graph={
            "nodes": [
                {"id": "draft", "member_id": "worker", "task": "Draft {{input}}"},
                {
                    "id": "review",
                    "member_id": "lead",
                    "task": "Review {{draft}}",
                    "output_contract": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "approved": {"type": "boolean"},
                        },
                        "required": ["text", "approved"],
                    },
                },
            ],
            "edges": [{"from": "draft", "to": "review"}],
            "loops": [loop],
        },
        layout={"review": {"x": 240, "y": 60}},
    )

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "model"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    roles = RoleAssetService(db, catalog_loader=catalog)
    workflows = WorkflowService(db, roles)
    migration = LegacyMigrationService(db, roles, workflows)
    try:
        preview = await migration.preview("alice", "loops")
        assert not next(
            asset for asset in preview["assets"] if asset["kind"] == "workflow"
        )["issues"], preview["issues"]
        result = await migration.confirm(
            "alice",
            "loops",
            {"preview_token": preview["preview_token"], "save_pending": True},
        )
        saved = await workflows.get_asset(
            "alice", result["mapping"]["workflows"]["loop-flow"]["asset_id"]
        )
        assert saved["definition"]["loops"] == [loop]
        assert saved["definition"]["tasks"][1]["position"] == {"x": 240, "y": 60}
        assert (await workflows.check_readiness("alice", saved["asset_id"]))["ready"]
    finally:
        await workflows.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_profile_persona_and_node_override_are_frozen_into_new_run(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "inherit.db"))
    await db.create_agent_team(
        team_id="inherit",
        owner_username="alice",
        name="Inherited",
        coordinator_member_id="lead",
        config={},
        members=[
            {
                "member_id": "lead",
                "name": "Lead",
                "provider_id": "model",
                "system_prompt": "Coordinate.",
            },
            {
                "member_id": "writer",
                "name": "Writer",
                "provider_id": "model",
                "persona_id": "editor",
                "system_prompt": "Cite evidence.",
                "session_id": "old-history-session",
                "runner_config": {"config_id": "strict", "skills": [], "max_steps": 9},
            },
        ],
    )
    await db.create_agent_team_workflow(
        workflow_id="inherit-flow",
        team_id="inherit",
        name="Frozen report",
        graph={
            "nodes": [
                {
                    "id": "write",
                    "member_id": "writer",
                    "task": "Write {{input}}",
                    "execution": {"max_steps": 12},
                }
            ],
            "edges": [],
        },
        layout={},
    )
    await db.create_agent_team_run(
        run_id="old-active",
        team_id="inherit",
        workflow_id="inherit-flow",
        mode="dag",
        input="Previous goal",
        status="running",
        graph_snapshot={"secret": "old context"},
        node_states={},
        rounds=[],
    )
    inventory = {
        "profiles": [
            {"id": "default"},
            {
                "id": "strict",
                "max_steps": 40,
                "context_length": 8000,
                "knowledge_bases": ["Facts"],
            },
        ],
        "models": [{"id": "model", "capabilities": ["tool_use"]}],
        "personas": [
            {
                "id": "editor",
                "prompt": "Review carefully.",
                "tools": ["search"],
                "skills": ["writing"],
            }
        ],
        "tools": [{"id": "search"}],
        "skills": [{"id": "writing"}],
        "knowledge_bases": [{"id": "facts-id", "name": "Facts"}],
    }

    async def catalog():
        return inventory

    roles = RoleAssetService(db, catalog_loader=catalog)
    workflows = WorkflowService(db, roles)
    migration = LegacyMigrationService(db, roles, workflows)
    try:
        preview = await migration.preview("alice", "inherit")
        assert preview["active_runs"] == [{"run_id": "old-active", "status": "running"}]
        result = await migration.confirm(
            "alice",
            "inherit",
            {"preview_token": preview["preview_token"], "save_pending": True},
        )
        identity = result["mapping"]["workflows"]["inherit-flow"]["asset_id"]
        asset = await workflows.get_asset("alice", identity)
        role = await roles.get_role("alice", asset["definition"]["tasks"][0]["role_id"])
        resolved = await roles.preview_role(role)
        assert resolved["effective"]["prompt"] == "Review carefully.\n\nCite evidence."
        assert resolved["effective"]["max_steps"] == 12
        assert resolved["effective"]["context_length"] == 8000
        assert resolved["effective"]["tools"] == ["search"]
        assert resolved["effective"]["skills"] == []
        assert resolved["effective"]["knowledge_bases"] == ["facts-id"]
        first = await workflows.start_run(
            "alice", identity, {"version": 1, "inputs": {"input": "Fresh goal"}}
        )
        await workflows.shutdown()
        task = first["snapshot"]["definition"]["tasks"][0]
        member = first["snapshot"]["members"][task["member"]]["member"]
        assert member["session_id"] != "old-history-session"
        assert "old context" not in json.dumps(first)
        await roles.save_role(
            "alice",
            {
                "definition": {**role["definition"], "prompt": "Changed template"},
                "installation": role["installation"],
                "expected_version": 1,
            },
            role["role_id"],
        )
        unchanged = await workflows.get_run("alice", first["run_id"])
        assert (
            unchanged["snapshot"]["members"][task["member"]]["effective"]["prompt"]
            == "Review carefully.\n\nCite evidence."
        )
        assert (await db.get_agent_team_run("old-active")).status == "running"
        assert (await db.get_agent_team_run("old-active")).graph_snapshot == {
            "secret": "old context"
        }
    finally:
        await workflows.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_unmappable_cycle_is_a_readable_blocked_asset_without_losing_edges(
    tmp_path,
):
    db = SQLiteDatabase(str(tmp_path / "cycle.db"))
    await db.create_agent_team(
        team_id="cycle",
        owner_username="alice",
        name="Cycle team",
        coordinator_member_id="lead",
        config={"failure_policy": "auto_skip"},
        members=[
            {
                "member_id": "lead",
                "name": "Lead",
                "provider_id": "model",
                "system_prompt": "Coordinate.",
            },
            {
                "member_id": "worker",
                "name": "Worker",
                "provider_id": "model",
                "system_prompt": "Work.",
            },
        ],
    )
    edges = [{"from": "first", "to": "second"}, {"from": "second", "to": "first"}]
    await db.create_agent_team_workflow(
        workflow_id="cycle-flow",
        team_id="cycle",
        name="Old loop",
        graph={
            "nodes": [
                {"id": "first", "member_id": "lead", "task": "First"},
                {"id": "second", "member_id": "worker", "task": "Second"},
            ],
            "edges": edges,
        },
        layout={},
    )

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "model"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    roles = RoleAssetService(db, catalog_loader=catalog)
    workflows = WorkflowService(db, roles)
    migration = LegacyMigrationService(db, roles, workflows)
    try:
        preview = await migration.preview("alice", "cycle")
        result = await migration.confirm(
            "alice",
            "cycle",
            {"preview_token": preview["preview_token"], "save_pending": True},
        )
        identity = result["mapping"]["workflows"]["cycle-flow"]["asset_id"]
        pending = await workflows.get_asset("alice", identity)
        assert pending["definition"]["migration"]["source"]["graph"]["edges"] == edges
        assert pending["definition"]["edges"] == [
            {"source": "first", "target": "second"},
            {"source": "second", "target": "first"},
        ]
        assert any(
            i["code"] == "CYCLE"
            for i in (await workflows.check_readiness("alice", identity))["issues"]
        )
        with pytest.raises(RoleAssetError):
            await workflows.start_run(
                "alice", identity, {"version": 1, "inputs": {"input": "New task"}}
            )
        with pytest.raises(RoleAssetError):
            await workflows.start_run(
                "alice",
                result["mapping"]["team"]["asset_id"],
                {"version": 1, "inputs": {"goal": "New task"}},
            )
        assert (await workflows.list_runs("alice"))["runs"] == []
    finally:
        await workflows.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_missing_resource_stays_pending_and_retry_reuses_saved_assets(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "pending.db"))
    await db.create_agent_team(
        team_id="pending",
        owner_username="alice",
        name="Pending team",
        coordinator_member_id="lead",
        config={},
        members=[
            {
                "member_id": "lead",
                "name": "Lead",
                "provider_id": "lost-model",
                "system_prompt": "Coordinate.",
            },
            {
                "member_id": "worker",
                "name": "Worker",
                "provider_id": "lost-model",
                "system_prompt": "Work.",
            },
        ],
    )
    inventory = {
        "profiles": [{"id": "default"}],
        "models": [{"id": "other-model"}],
        "personas": [],
        "tools": [],
        "skills": [],
        "knowledge_bases": [],
    }

    async def catalog():
        return inventory

    roles = RoleAssetService(db, catalog_loader=catalog)
    workflows = WorkflowService(db, roles)
    migration = LegacyMigrationService(db, roles, workflows)
    try:
        preview = await migration.preview("alice", "pending")
        assert not preview["ready"]
        assert {r["effective"]["provider_id"] for r in preview["roles"]} == {
            "lost-model"
        }
        with pytest.raises(RoleAssetError, match="pending"):
            await migration.confirm(
                "alice", "pending", {"preview_token": preview["preview_token"]}
            )
        result = await migration.confirm(
            "alice",
            "pending",
            {"preview_token": preview["preview_token"], "save_pending": True},
        )
        identity = result["mapping"]["team"]["asset_id"]
        asset = await workflows.get_asset("alice", identity)
        reviewed = dict(asset["definition"])
        reviewed.pop("migration", None)
        await workflows.save_asset(
            "alice", {"definition": reviewed, "expected_version": 1}, identity
        )
        assert not (await workflows.check_readiness("alice", identity))["ready"]
        with pytest.raises(RoleAssetError):
            await workflows.start_run(
                "alice", identity, {"version": 2, "inputs": {"goal": "New task"}}
            )
        assert (await workflows.list_runs("alice"))["runs"] == []
        inventory["models"].append({"id": "lost-model", "capabilities": ["tool_use"]})
        after = await migration.preview("alice", "pending")
        repeated = await migration.confirm(
            "alice", "pending", {"preview_token": after["preview_token"]}
        )
        assert repeated["existing"] and repeated["mapping"] == result["mapping"]
        assert (await workflows.check_readiness("alice", identity))["ready"]
        assert len((await workflows.list_assets("alice"))["assets"]) == 1
    finally:
        await workflows.shutdown()
        await db.engine.dispose()
