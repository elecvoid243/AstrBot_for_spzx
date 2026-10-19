"""Public dependency review and installation consistency for reused children."""

import asyncio
import copy
import json

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
@pytest.mark.parametrize("dialog_payload", [False, True])
@pytest.mark.parametrize("child_tool", [False, True])
@pytest.mark.parametrize(
    "choice",
    [
        "unique",
        "saved",
        "explicit",
        "missing",
        "ambiguous",
        "incompatible",
        "divergent",
    ],
)
async def test_upgrade_reviews_reused_installations(
    workflow_env, tmp_path, child_tool, choice, dialog_payload
):
    """Keep automatic reviewed bindings consistent across reused variants.

    Args:
        workflow_env: Public asset services and mutable resource inventory.
        tmp_path: Owned directory for public observations.
        child_tool: Give the child an independent tool whose canonical slot moves.
        choice: Available resources and deliberate receiver binding behavior.
        dialog_payload: Send the dialog's unchanged copied preview proposals.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "compatible-model"
    env.inventory["tools"] = [
        {"id": "extra", "fingerprint": "aaa-parent-tool"},
        {"id": "child", "fingerprint": "zzz-child-tool"},
    ]
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Shared role",
                "prompt": "Read evidence",
                "tools": [],
                "skills": [],
                "knowledge_bases": [],
                "max_depth": 6,
            },
            "installation": {
                "bindings": {
                    "model": "model-a",
                    "extra": "extra",
                    "child-slot": "child",
                }
            },
        },
    )
    ref = {"role_id": role["role_id"], "role_version": 1}
    leaf = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Leaf",
                "task": "Read",
                **ref,
                "overrides": {"tools": ["child-slot"] if child_tool else []},
            }
        },
    )
    team = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Team",
                "kind": "autonomous",
                "max_depth": 6,
                "coordinator": ref,
                "allowed_roles": [ref],
                "allowed_workflows": [
                    {
                        "asset_id": leaf["asset_id"],
                        "version": 1,
                        "result_task_id": "result",
                    }
                ],
            }
        },
    )
    root_definition = {
        "name": "Parent",
        "max_depth": 6,
        "required_inputs": ["goal"],
        "tasks": [
            {
                "id": "parent",
                "name": "Parent task",
                "member": "parent",
                "task": "Read",
                **ref,
            },
            {
                "id": "team",
                "name": "Child team",
                "team": {"asset_id": team["asset_id"], "version": 1},
                "inputs": {"goal": {"input": "goal"}},
            },
        ],
    }
    root = await env.service.save_asset("alice", {"definition": root_definition})
    first = await env.service.export_asset("alice", root["asset_id"], 1)
    next_definition = copy.deepcopy(root["definition"])
    next_definition["tasks"][0]["overrides"]["tools"] = ["extra"]
    await env.service.save_asset(
        "alice",
        {"definition": next_definition, "expected_version": 1},
        root["asset_id"],
    )
    second = await env.service.export_asset("alice", root["asset_id"], 2)
    first_install = await env.service.import_asset("bob", {"package": first})
    before_readiness = await env.service.check_readiness(
        "bob", first_install["asset"]["asset_id"], 1
    )
    assert before_readiness["ready"], before_readiness
    local_team_ref = first_install["asset"]["definition"]["tasks"][1]["team"]
    local_team = await env.service.get_asset("bob", local_team_ref["asset_id"], 1)
    local_leaf_ref = local_team["definition"]["allowed_workflows"][0]
    old_run = await env.service.start_run(
        "bob", local_leaf_ref["asset_id"], {"version": 1}
    )
    for _ in range(200):
        finished = await env.service.get_run("bob", old_run["run_id"])
        if finished["status"] == "completed":
            break
        await asyncio.sleep(0.01)
    assert finished["status"] == "completed", finished
    old_snapshot = copy.deepcopy(finished["snapshot"])
    if choice in {"saved", "explicit", "divergent"}:
        env.inventory["models"].append(
            {"id": "model-b", "fingerprint": "compatible-model"}
        )
    else:
        env.inventory["models"] = [{"id": "model-b", "fingerprint": "compatible-model"}]
    if choice == "missing":
        env.inventory["models"] = []
    elif choice == "ambiguous":
        env.inventory["models"].append(
            {"id": "model-c", "fingerprint": "compatible-model"}
        )
    elif choice == "incompatible":
        env.inventory["models"].append(
            {"id": "model-a", "fingerprint": "incompatible-model"}
        )
    elif choice == "divergent":
        child_package = await env.service.export_asset("alice", leaf["asset_id"], 1)
        child_preview = await env.service.preview_import(
            "bob", {"package": child_package}
        )
        child_installation = copy.deepcopy(child_preview["roles"][0]["installation"])
        child_installation["bindings"]["model"] = "model-b"
        await env.service.import_asset(
            "bob",
            {
                "package": child_preview["package"],
                "installations": {child_preview["roles"][0]["key"]: child_installation},
            },
        )
    before_roles = await env.roles.list_roles("bob")
    before_assets = await env.service.list_assets("bob")
    payload = {"package": second}
    preview = await env.service.preview_import("bob", payload)
    if choice in {"missing", "ambiguous", "divergent"}:
        assert not preview["ready"], preview
        assert any(
            issue["code"]
            == ("CONFIRMATION_REQUIRED" if choice == "divergent" else choice.upper())
            for issue in preview["issues"]
        ), preview
        with pytest.raises(RoleAssetError, match="Configure package dependencies"):
            await env.service.import_asset("bob", payload)
        assert await env.roles.list_roles("bob") == before_roles
        assert await env.service.list_assets("bob") == before_assets
        pending_payload = {**payload, "save_pending": True}
        if dialog_payload:
            pending_payload["installations"] = {
                role["key"]: role["installation"] for role in preview["roles"]
            }
        pending = await env.service.import_asset("bob", pending_payload)
        assert not pending["ready"]
        for saved in before_roles["roles"]:
            assert (await env.roles.get_role("bob", saved["role_id"], 1))[
                "installation"
            ] == saved["installation"]
        assert (await env.service.get_run("bob", old_run["run_id"]))[
            "snapshot"
        ] == old_snapshot
        if choice != "divergent":
            assert not (
                await env.service.check_readiness(
                    "bob", pending["asset"]["asset_id"], pending["asset"]["version"]
                )
            )["ready"]
            return
    if choice in {"explicit", "divergent"}:
        installation = copy.deepcopy(preview["roles"][0]["installation"])
        installation["bindings"]["model"] = "model-b"
        payload = {
            "package": preview["package"],
            "installations": {preview["roles"][0]["key"]: installation},
        }
        preview = await env.service.preview_import("bob", payload)
    installed = await env.service.import_asset("bob", payload)
    actual_readiness = await env.service.check_readiness(
        "bob", installed["asset"]["asset_id"], installed["asset"]["version"]
    )
    evidence = {
        "first_payload": {"package": first},
        "upgrade_payload": payload,
        "before_readiness": before_readiness,
        "before_roles": before_roles,
        "resources": await env.roles.get_resources(),
        "preview": preview,
        "installed": installed,
        "actual_readiness": actual_readiness,
        "after_assets": await env.service.list_assets("bob"),
        "after_roles": await env.roles.list_roles("bob"),
    }
    (tmp_path / "observations.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert preview["ready"] and installed["ready"], evidence
    assert actual_readiness["ready"], evidence
    assert all(
        role["installation"]["bindings"]["model"]
        == ("model-a" if choice == "saved" else "model-b")
        for role in evidence["after_roles"]["roles"]
    ), evidence
    assert (await env.service.get_run("bob", old_run["run_id"]))[
        "snapshot"
    ] == old_snapshot
    assert (await env.service.get_asset("bob", first_install["asset"]["asset_id"], 1))[
        "definition"
    ] == first_install["asset"]["definition"]
    if child_tool:
        child = await env.service.get_asset("bob", local_leaf_ref["asset_id"], 1)
        installed_role = await env.roles.get_role(
            "bob", child["definition"]["role_id"], 1
        )
        assert (
            await env.roles.preview_role(
                installed_role, child["definition"]["overrides"]
            )
        )["effective"]["tools"] == ["child"]
