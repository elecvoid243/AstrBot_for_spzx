"""Deletion semantics across real SQLite assets, execution, and packages."""

import asyncio
import copy

import pytest
from sqlalchemy import text

from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.chat import ChatCaller
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from tests.agent_teams.test_nested_packages import nested_package as nested_package
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
async def test_deleted_role_preserves_fixed_parent_but_rejects_new_reference(
    workflow_env,
):
    env = workflow_env
    await env.roles.delete_asset("alice", "role", env.role["role_id"])
    assert not (await env.roles.list_roles("alice"))["roles"]
    assert (await env.roles.get_role("alice", env.role["role_id"], 1))["deleted"]
    definition = copy.deepcopy(env.asset["definition"])
    definition["name"] = "Updated parent"
    updated = await env.service.save_asset(
        "alice",
        {"definition": definition, "expected_version": 1},
        env.asset["asset_id"],
    )
    assert updated["version"] == 2
    with pytest.raises(RoleAssetError, match="newly referenced"):
        await env.service.save_asset("alice", {"definition": definition})
    with pytest.raises(RoleAssetError, match="deleted"):
        await env.roles.save_role(
            "alice",
            {"definition": env.role["definition"], "expected_version": 1},
            env.role["role_id"],
        )
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    completed = await completed_run(env.service, run["run_id"])
    assert completed["status"] == "completed"
    assert (await env.service.get_result("alice", run["run_id"]))["text"]
    assert (await env.service.read_artifact("alice", **completed["result_ref"]))[
        "content"
    ]["text"]


@pytest.mark.asyncio
async def test_draft_uses_its_fixed_base_for_deleted_references(workflow_env):
    env = workflow_env
    await env.roles.save_role(
        "alice",
        {
            "definition": env.role["definition"],
            "installation": env.role["installation"],
            "expected_version": 1,
        },
        env.role["role_id"],
    )
    draft = await env.roles.create_draft(
        "alice",
        "workflow",
        {"definition": copy.deepcopy(env.asset["definition"])},
        env.asset["asset_id"],
    )
    await env.roles.delete_asset("alice", "role", env.role["role_id"])
    body = copy.deepcopy(draft["body"])
    body["definition"]["name"] = "Edited"
    saved = await env.roles.update_draft(
        "alice", "workflow", env.asset["asset_id"], draft["revision"], body
    )
    body["definition"]["tasks"][0]["role_version"] = 2
    with pytest.raises(RoleAssetError, match="newly referenced"):
        await env.roles.update_draft(
            "alice", "workflow", env.asset["asset_id"], saved["revision"], body
        )
    with pytest.raises(RoleAssetError, match="newly referenced"):
        await env.roles.create_draft(
            "alice", "workflow", {"definition": body["definition"]}
        )
    await env.roles.delete_asset("alice", "workflow", env.asset["asset_id"])
    assert not await env.roles.list_drafts("alice", "workflow")
    with pytest.raises(RoleAssetError, match="deleted"):
        await env.roles.update_draft(
            "alice", "workflow", env.asset["asset_id"], saved["revision"], saved["body"]
        )


@pytest.mark.asyncio
async def test_deleted_root_keeps_pending_result_recoverable(workflow_env):
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
    blocked = await completed_run(env.service, run["run_id"])
    task = blocked["tasks"][0]
    assert task["state"]["safe_to_finish"]
    await env.roles.delete_asset("alice", "workflow", env.asset["asset_id"])
    await env.roles.delete_asset("alice", "role", env.role["role_id"])
    async with env.db.get_db() as session:
        await session.execute(text("DROP TRIGGER reject_completion"))
        await session.commit()
    finished = await env.service.finish_submission("alice", task["run_id"])
    assert finished["status"] == "completed"
    assert finished["result"] == task["pending_result"]


@pytest.mark.asyncio
async def test_deleted_root_rejects_launch_chat_grants_and_export(workflow_env):
    env = workflow_env
    caller = ChatCaller(
        platform_id="qq",
        sender_id="sender",
        source="qq:FriendMessage:sender",
        profile_id="default",
    )
    grant = {
        "platform_id": "qq",
        "sender_id": "sender",
        "source": caller.source,
        "profile_id": "default",
        "version": 1,
    }
    await env.service.chat.save_access("alice", env.asset["asset_id"], grant)
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    await env.roles.delete_asset("alice", "workflow", env.asset["asset_id"])
    await env.roles.delete_asset("alice", "workflow", env.asset["asset_id"])
    assert not (await env.service.list_assets("alice"))["assets"]
    assert not (await env.service.chat.list_assets(caller))["assets"]
    with pytest.raises(RoleAssetError, match="deleted"):
        await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    with pytest.raises(RoleAssetError, match="deleted"):
        await env.service.chat.save_access("alice", env.asset["asset_id"], grant)
    with pytest.raises(RoleAssetError, match="deleted"):
        await env.service.start_run(
            "alice",
            env.asset["asset_id"],
            {"version": 1, "source": caller.source},
            chat={
                "caller": caller.model_dump(),
                "asset_id": env.asset["asset_id"],
                "version": 1,
            },
        )
    with pytest.raises(RoleAssetError, match="deleted"):
        await env.service.export_asset("alice", env.asset["asset_id"], 1)
    assert (await completed_run(env.service, run["run_id"]))["status"] == "completed"
    assert (await env.service.get_result("alice", run["run_id"]))["text"]


@pytest.mark.asyncio
async def test_active_run_finishes_after_root_and_role_deletion(workflow_env):
    env = workflow_env
    entered, release = asyncio.Event(), asyncio.Event()
    original = env.service.ports_factory("alice")

    async def collect(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original.collect(*args, **kwargs)

    env.service.ports_factory = lambda username: TeamPorts(
        deliver=original.deliver,
        collect=collect,
        is_busy=original.is_busy,
        emit=original.emit,
    )
    run = await env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    await asyncio.wait_for(entered.wait(), 5)
    await env.roles.delete_asset("alice", "workflow", env.asset["asset_id"])
    await env.roles.delete_asset("alice", "role", env.role["role_id"])
    release.set()
    completed = await completed_run(env.service, run["run_id"])
    assert completed["status"] == "completed"
    assert (await env.service.read_artifact("alice", **completed["result_ref"]))[
        "content"
    ]["text"]


@pytest.mark.asyncio
async def test_deleted_child_retained_only_in_original_parent_version(nested_package):
    env, root, _ = nested_package
    ref = root["definition"]["tasks"][0]["team"]
    await env.roles.delete_asset("alice", "workflow", ref["asset_id"])
    definition = copy.deepcopy(root["definition"])
    definition["name"] = "Updated root"
    saved = await env.service.save_asset(
        "alice", {"definition": definition, "expected_version": 1}, root["asset_id"]
    )
    assert saved["version"] == 2
    with pytest.raises(RoleAssetError, match="newly referenced"):
        await env.service.save_asset("alice", {"definition": definition})
    run = await env.service.start_run(
        "alice", root["asset_id"], {"version": 1, "inputs": {"goal": "Research"}}
    )
    assert run["run_id"]


@pytest.mark.asyncio
async def test_deleted_dependencies_remain_in_package_but_import_receipts_conflict(
    nested_package,
):
    env, root, package = nested_package
    team = root["definition"]["tasks"][0]["team"]
    await env.roles.delete_asset("alice", "workflow", team["asset_id"])
    await env.roles.delete_asset("alice", "role", env.role["role_id"])
    exported = await env.service.export_asset("alice", root["asset_id"], 1)
    assert len(exported["assets"]) == 2 and len(exported["roles"]) == 1
    choices = [
        {"kind": r["kind"], "source_id": r["source_id"], "target_id": r["source_id"]}
        for r in package["requirements"]
    ]
    payload = {"package": package, "selections": choices}
    imported = await env.service.import_asset("bob", payload)
    local = imported["asset"]
    local_team = local["definition"]["tasks"][0]["team"]
    await env.roles.delete_asset("bob", "workflow", local_team["asset_id"])
    preview = await env.service.preview_import("bob", payload)
    assert not preview["ready"] and any(
        i["code"] == "CONFLICT" for i in preview["issues"]
    )
    with pytest.raises(RoleAssetError, match="deleted"):
        await env.service.import_asset("bob", payload)


@pytest.mark.asyncio
async def test_deleted_imported_role_receipt_is_a_conflict(nested_package):
    env, _, package = nested_package
    choices = [
        {"kind": r["kind"], "source_id": r["source_id"], "target_id": r["source_id"]}
        for r in package["requirements"]
    ]
    payload = {"package": package, "selections": choices}
    imported = await env.service.import_asset("bob", payload)
    local_team_ref = imported["asset"]["definition"]["tasks"][0]["team"]
    local_team = await env.service.get_asset("bob", local_team_ref["asset_id"], 1)
    local_role = local_team["definition"]["coordinator"]["role_id"]
    await env.roles.delete_asset("bob", "role", local_role)
    preview = await env.service.preview_import("bob", payload)
    assert not preview["ready"] and any(
        i["code"] == "CONFLICT" for i in preview["issues"]
    )
    with pytest.raises(RoleAssetError, match="deleted"):
        await env.service.import_asset("bob", payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_first", [True, False])
async def test_launch_and_delete_have_a_linear_order(
    workflow_env, monkeypatch, delete_first
):
    env = workflow_env
    entered, release = asyncio.Event(), asyncio.Event()
    if delete_first:
        original = env.service._freeze_graph

        async def freeze(*args, **kwargs):
            frozen = await original(*args, **kwargs)
            entered.set()
            await release.wait()
            return frozen

        monkeypatch.setattr(env.service, "_freeze_graph", freeze)
    else:
        original = env.service._create_scope

        async def create(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(env.service, "_create_scope", create)
    launching = asyncio.create_task(
        env.service.start_run("alice", env.asset["asset_id"], {"version": 1})
    )
    await asyncio.wait_for(entered.wait(), 5)
    deleting = asyncio.create_task(
        env.roles.delete_asset("alice", "workflow", env.asset["asset_id"])
    )
    if delete_first:
        await asyncio.wait_for(deleting, 5)
    release.set()
    if delete_first:
        with pytest.raises(RoleAssetError, match="deleted"):
            await launching
    else:
        run = await launching
        await asyncio.wait_for(deleting, 5)
        assert (await completed_run(env.service, run["run_id"]))[
            "status"
        ] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_first", [True, False])
async def test_save_and_delete_have_a_linear_order(
    workflow_env, monkeypatch, delete_first
):
    env = workflow_env
    entered, release = asyncio.Event(), asyncio.Event()
    if delete_first:
        original = env.service._validate_task_definition

        async def validate(*args, **kwargs):
            await original(*args, **kwargs)
            entered.set()
            await release.wait()

        monkeypatch.setattr(env.service, "_validate_task_definition", validate)
    else:
        original = env.roles.check_active_references

        async def check(*args, **kwargs):
            await original(*args, **kwargs)
            entered.set()
            await release.wait()

        monkeypatch.setattr(env.roles, "check_active_references", check)
    saving = asyncio.create_task(
        env.service.save_asset(
            "alice", {"definition": copy.deepcopy(env.asset["definition"])}
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    deleting = asyncio.create_task(
        env.roles.delete_asset("alice", "role", env.role["role_id"])
    )
    if delete_first:
        await asyncio.wait_for(deleting, 5)
    release.set()
    if delete_first:
        with pytest.raises(RoleAssetError, match="newly referenced"):
            await saving
    else:
        saved = await saving
        await asyncio.wait_for(deleting, 5)
        run = await env.service.start_run("alice", saved["asset_id"], {"version": 1})
        assert (await completed_run(env.service, run["run_id"]))[
            "status"
        ] == "completed"
