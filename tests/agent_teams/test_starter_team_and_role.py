"""Starter assets through the dashboard catalog and the native team execution service."""

import asyncio
import json

import pytest
from sqlalchemy import text

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase
from tests.agent_teams.test_autonomous_assets import team_env as team_env
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets_api import (
    publication_client as publication_client,
)
from tests.agent_teams.test_workflow_assets_api import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_directories_provision_independent_examples_once(
    publication_client, workflow_env, tmp_path
):
    """Exercise first access and independent deletion across service restarts.

    Args:
        publication_client: Authenticated client for the asset directories.
        workflow_env: Owner-scoped services and mutable resource catalog.
        tmp_path: Directory containing the isolated database.
    """
    client = publication_client
    service = workflow_env.service
    workflow_path = "/api/v1/workflow_assets"
    role_path = "/api/v1/agent_roles"
    assert all(
        not a["is_builtin_example"]
        for a in (await client.get(workflow_path)).json()["data"]["assets"]
    )
    assert all(
        not r.get("is_builtin_example")
        for r in (await client.get(role_path)).json()["data"]["roles"]
    )
    workflow_env.inventory["models"].clear()
    teams, roles = await asyncio.gather(
        asyncio.gather(
            *(
                client.get(
                    workflow_path,
                    params={"include_team_example": True, "summary_only": True},
                )
                for _ in range(3)
            )
        ),
        asyncio.gather(
            *(client.get(role_path, params={"include_example": True}) for _ in range(3))
        ),
    )
    ids = set()
    for resp in teams:
        assert resp.status_code == 200, resp.text
        (card,) = [a for a in resp.json()["data"]["assets"] if a["is_builtin_example"]]
        assert card["summary"]["kind"] == "autonomous"
        ids.add(card["asset_id"])
    (team_id,) = ids
    ids.clear()
    for resp in roles:
        assert resp.status_code == 200, resp.text
        (role,) = [
            r for r in resp.json()["data"]["roles"] if r.get("is_builtin_example")
        ]
        ids.add(role["role_id"])
    (role_id,) = ids
    assert not (await service.check_readiness("alice", team_id))["ready"]
    assert not (await service.list_runs("alice"))["runs"]
    role = await workflow_env.roles.get_role("alice", role_id)
    await workflow_env.roles.save_role(
        "alice",
        {
            "definition": {**role["definition"], "name": "My editor"},
            "installation": role["installation"],
            "expected_version": 1,
        },
        role_id,
    )
    assert (
        next(
            r
            for r in (await client.get(role_path)).json()["data"]["roles"]
            if r.get("is_builtin_example")
        )["definition"]["name"]
        == "My editor"
    )
    assert (await client.delete(f"{role_path}/{role_id}")).status_code == 200
    assert (await client.get(f"{workflow_path}/{team_id}")).json()["data"][
        "definition"
    ]["allowed_roles"][0]["role"]["name"] == "Release note editor"
    assert (await client.delete(f"{workflow_path}/{team_id}")).status_code == 200
    db = SQLiteDatabase(str(tmp_path / "workflows.db"))
    reopened = WorkflowService(db, RoleAssetService(db))
    try:
        assert not any(
            r.get("is_builtin_example")
            for r in (await reopened.roles.list_roles("alice", include_example=True))[
                "roles"
            ]
        )
        directory = (
            await reopened.list_assets(
                "alice", include_team_example=True, include_example=True
            )
        )["assets"]
        assert [
            a["definition"]["name"] for a in directory if a["is_builtin_example"]
        ] == ["Release brief"]
        (other,) = (await reopened.list_assets("bob", include_team_example=True))[
            "assets"
        ]
        assert other["asset_id"] != team_id and other["is_builtin_example"]
        (other_role,) = (await reopened.roles.list_roles("bob", include_example=True))[
            "roles"
        ]
        assert other_role["role_id"] != role_id
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("pin_template", [False, True])
async def test_starter_team_delegates_with_inline_or_pinned_editor(
    team_env, pin_template
):
    """Run a native coordinator and editor via a controlled execution boundary.

    Args:
        team_env: Real team services with scripted external execution.
        pin_template: Attach the starter role by exact version instead of using the inline preset.
    """
    env = team_env
    env.inventory["models"][0]["capabilities"] = ["text", "tool_use"]
    (team,) = [
        a
        for a in (await env.service.list_assets("alice", include_team_example=True))[
            "assets"
        ]
        if a["is_builtin_example"]
    ]
    (role,) = [
        r
        for r in (await env.roles.list_roles("alice", include_example=True))["roles"]
        if r.get("is_builtin_example")
    ]
    editor = {
        "id": "editor",
        "role_id": role["role_id"],
        "role_version": role["version"],
    }
    await env.service.save_asset(
        "alice",
        {
            "definition": {
                **team["definition"],
                "default_provider_id": "model",
                "allowed_roles": [editor]
                if pin_template
                else team["definition"]["allowed_roles"],
            },
            "expected_version": 1,
        },
        team["asset_id"],
    )
    assert (await env.service.check_readiness("alice", team["asset_id"], 2))["ready"]

    async def coordinate(tools, binding):
        """Collect the dispatched editor result before submitting the final note.

        Args:
            tools: Tools bound to this coordinator attempt.
            binding: Execution identity supplied by the controlled port.
        """
        manage = tools["team_manage"]
        member = json.loads(
            await manage.call(
                None,
                action="create_member",
                request_id="editor",
                preset_id="editor",
                name="Editor",
            )
        )
        task = json.loads(
            await manage.call(
                None,
                action="dispatch",
                request_id="write",
                member_id=member["member_id"],
                name="Write release note",
                task="Write the note from the supplied facts.",
            )
        )
        completed = json.loads(
            await manage.call(
                None, action="wait", request_id="collect", task_ids=[task["task_id"]]
            )
        )
        assert completed["tasks"][0]["result"]["text"].endswith("COMPLETE_END")
        await tools["submit_task_result"].call(
            None, text="Drafts are saved automatically."
        )

    env.script = coordinate
    run = await env.service.start_run(
        "alice",
        team["asset_id"],
        {"version": 2, "inputs": {"goal": "Explain automatic draft saving."}},
    )
    final = await completed_run(env.service, run["run_id"])
    assert not env.errors, env.errors
    assert (
        final["status"] == "completed"
        and final["result"]["text"] == "Drafts are saved automatically."
    )
    assert len(final["members"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted", [False, True])
async def test_upgrade_preserves_workflow_example(workflow_env, tmp_path, deleted):
    """Import the old owner pointer without duplicating or reviving a workflow.

    Args:
        workflow_env: Services used to prepare the existing workflow definition.
        tmp_path: Directory containing the isolated database.
        deleted: Whether the owner had deleted the example before upgrade.
    """
    env = workflow_env
    (example,) = [
        a
        for a in (await env.service.list_assets("alice", include_example=True))[
            "assets"
        ]
        if a["is_builtin_example"]
    ]
    await env.service.save_asset(
        "alice",
        {
            "definition": {**example["definition"], "name": "My original brief"},
            "expected_version": 1,
        },
        example["asset_id"],
    )
    if deleted:
        await env.roles.delete_asset("alice", "workflow", example["asset_id"])
    async with env.db.get_db() as session, session.begin():
        await session.execute(
            text(
                "CREATE TABLE agent_workflow_examples(owner_username TEXT PRIMARY KEY, asset_id TEXT UNIQUE NOT NULL)"
            )
        )
        await session.execute(
            text("INSERT INTO agent_workflow_examples VALUES ('alice',:id)"),
            {"id": example["asset_id"]},
        )
        await session.execute(
            text("DELETE FROM agent_asset_examples WHERE owner_username = 'alice'")
        )
    db = SQLiteDatabase(str(tmp_path / "workflows.db"))
    try:
        service = WorkflowService(db, RoleAssetService(db))
        assets = (
            await service.list_assets(
                "alice", include_example=True, include_team_example=True
            )
        )["assets"]
        expected = (
            {"Release notes desk"}
            if deleted
            else {"Release notes desk", "My original brief"}
        )
        assert {
            a["definition"]["name"] for a in assets if a["is_builtin_example"]
        } == expected
        assert (await service.get_asset("alice", example["asset_id"]))[
            "is_builtin_example"
        ]
    finally:
        await db.engine.dispose()
