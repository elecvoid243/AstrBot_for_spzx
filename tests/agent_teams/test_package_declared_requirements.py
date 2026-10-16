"""Preserve declared compatibility when recomposing imported nested assets."""

import copy

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
@pytest.mark.parametrize("local_version", [False, True])
@pytest.mark.parametrize(
    "requirement",
    [
        "tool",
        "model_capability",
        "tool_schema",
        "used_tool_schema",
        "task_tool_schema",
        "task_tool_capability",
    ],
)
async def test_recomposed_import_enforces_declared_requirements(
    workflow_env, tmp_path, requirement, local_version
):
    """Keep imported contracts when a local parent is transferred to a new store.

    Args:
        workflow_env: Public persistent services and a mutable external catalog.
        tmp_path: Isolated directory for the independent receiving store.
        requirement: Portable compatibility requirement absent or changed locally.
        local_version: Reference a renamed local version of the imported child.
    """
    env = workflow_env
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    env.inventory["models"][0].update(
        fingerprint="portable-model", capabilities=["vision"]
    )
    env.inventory["tools"] = [
        {
            "id": "local-search",
            "fingerprint": "portable-search",
            "capabilities": ["search"],
            "input_schema": schema,
        }
    ]
    source_role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Portable evidence",
                "prompt": "Read evidence",
                "tools": ["search"] if requirement == "used_tool_schema" else [],
            },
            "installation": {
                "bindings": {"model": "model-a", "search": "local-search"}
            },
        },
    )
    source = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Portable leaf",
                "task": "Read evidence",
                "role_id": source_role["role_id"],
                "role_version": 1,
                "overrides": {"tools": ["search"]}
                if requirement.startswith("task_")
                else {},
            }
        },
    )
    package = await env.service.export_asset("alice", source["asset_id"], 1)
    if requirement == "model_capability":
        next(
            dependency
            for dependency in package["roles"][0]["dependencies"]
            if dependency["kind"] == "models"
        )["capabilities"] = ["vision"]
    elif requirement == "task_tool_capability":
        next(
            dependency
            for dependency in package["roles"][0]["dependencies"]
            if dependency["kind"] == "tools"
        )["capabilities"] = ["search"]
    elif requirement in {"tool", "tool_schema"}:
        package["roles"][0]["dependencies"].append(
            {
                "slot": "declared_search",
                "kind": "tools",
                "fingerprint": "portable-search",
                "logical_slots": ["evidence_source"],
                "capabilities": ["search"],
                "input_schema": schema if requirement == "tool_schema" else None,
            }
        )
    installed = await env.service.import_asset("bob", {"package": package})
    child = installed["asset"]
    selected = child
    original = await env.service.export_asset("bob", child["asset_id"], 1)
    assert (await env.service.check_readiness("bob", child["asset_id"], 1))["ready"]
    if local_version:
        selected = await env.service.save_asset(
            "bob",
            {
                "definition": {**child["definition"], "name": "Renamed local child"},
                "expected_version": 1,
            },
            child["asset_id"],
        )
    coordinator = await env.roles.save_role(
        "bob",
        {
            "definition": {
                "name": "Wrapper coordinator",
                "prompt": "Call the leaf",
                "tools": [],
            },
            "installation": {"bindings": {"model": "model-a"}},
        },
    )
    reference = {"role_id": coordinator["role_id"], "role_version": 1}
    parent = await env.service.save_asset(
        "bob",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Locally authored wrapper",
                "coordinator": reference,
                "allowed_roles": [reference],
                "allowed_workflows": [
                    {
                        "asset_id": child["asset_id"],
                        "version": selected["version"],
                        "result_task_id": "result",
                    }
                ],
            }
        },
    )
    if requirement in {"used_tool_schema", "task_tool_schema"}:
        env.inventory["tools"][0]["input_schema"] = {"type": "object"}
    wrapped = await env.service.export_asset("bob", parent["asset_id"], 1)
    inventory = copy.deepcopy(env.inventory)
    inventory["profiles"][0]["id"] = "carol-profile"
    inventory["models"][0]["id"] = "carol-model"
    inventory["tools"][0]["id"] = "carol-search"
    if requirement == "model_capability":
        inventory["models"][0]["capabilities"] = []
    elif requirement == "tool":
        inventory["tools"] = []
    elif requirement == "task_tool_capability":
        inventory["tools"][0]["capabilities"] = []
    else:
        inventory["tools"][0]["input_schema"] = {"type": "object"}

    async def catalog():
        """Return the receiving machine's independent resource inventory.

        Returns:
            Resources with identities different from the exporting machine.
        """
        return inventory

    receiver_path = tmp_path / "receiver" / "workflows.db"
    receiver_path.parent.mkdir()
    db = SQLiteDatabase(str(receiver_path))
    receiver = WorkflowService(db, RoleAssetService(db, catalog_loader=catalog))
    try:
        preview = await receiver.preview_import("carol", {"package": wrapped})
        assert not preview["ready"]
        with pytest.raises(RoleAssetError):
            await receiver.import_asset("carol", {"package": wrapped})
        assert (await receiver.list_assets("carol"))["assets"] == []

        inventory["models"][0]["capabilities"] = ["vision"]
        inventory["tools"] = [
            {
                "id": "carol-search",
                "fingerprint": "portable-search",
                "capabilities": ["search"],
                "input_schema": schema,
            }
        ]
        transferred = await receiver.import_asset("carol", {"package": wrapped})
        assert transferred["ready"]
        assert (
            await receiver.check_readiness("carol", transferred["asset"]["asset_id"], 1)
        )["ready"]
        assert await env.service.export_asset("bob", child["asset_id"], 1) == original
        assert (await env.service.get_asset("bob", child["asset_id"], 1))[
            "definition"
        ] == child["definition"]
    finally:
        await receiver.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [False, True])
async def test_recomposition_keeps_task_logical_choices(workflow_env, tmp_path, wrap):
    """Keep distinct task choices when one machine binds both to the same tool.

    Args:
        workflow_env: Public asset services and the exporting resource catalog.
        tmp_path: Independent receiving machine's persistent directory.
        wrap: Export through a newly authored parent rather than directly.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "portable-model"
    env.inventory["tools"] = [
        {"id": "alice-a", "fingerprint": "portable-tool", "capabilities": ["a"]},
        {"id": "alice-b", "fingerprint": "portable-tool", "capabilities": ["b"]},
    ]
    inventory = copy.deepcopy(env.inventory)
    inventory["models"][0]["id"] = "carol-model"
    inventory["tools"][0]["id"] = "carol-a"
    inventory["tools"][1]["id"] = "carol-b"
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Shared evidence role",
                "prompt": "Use the selected source",
                "tools": [],
                "max_instances": 2,
                "max_parallel": 2,
            },
            "installation": {
                "bindings": {"model": "model-a", "alpha": "alice-a", "beta": "alice-b"}
            },
        },
    )
    graph = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Separate evidence choices",
                "tasks": [
                    {
                        "id": name,
                        "name": name,
                        "member": name,
                        "task": name,
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "overrides": {"tools": [slot]},
                    }
                    for name, slot in [("a", "alpha"), ("b", "beta")]
                ],
            }
        },
    )
    package = await env.service.export_asset("alice", graph["asset_id"], 1)
    for dependency in package["roles"][0]["dependencies"]:
        if dependency["kind"] == "tools":
            dependency["capabilities"] = [
                "a" if "alpha" in dependency["logical_slots"] else "b"
            ]
    env.inventory["tools"] = [
        {
            "id": "bob-combined",
            "fingerprint": "portable-tool",
            "capabilities": ["a", "b"],
        }
    ]
    imported = await env.service.import_asset("bob", {"package": package})
    entry = imported["asset"]
    if wrap:
        coordinator = await env.roles.save_role(
            "bob",
            {
                "definition": {
                    "name": "Coordinator",
                    "prompt": "Call the graph",
                    "tools": [],
                },
                "installation": {"bindings": {"model": "model-a"}},
            },
        )
        reference = {"role_id": coordinator["role_id"], "role_version": 1}
        entry = await env.service.save_asset(
            "bob",
            {
                "definition": {
                    "kind": "autonomous",
                    "name": "Local wrapper",
                    "coordinator": reference,
                    "allowed_roles": [reference],
                    "allowed_workflows": [
                        {
                            "asset_id": entry["asset_id"],
                            "version": 1,
                            "result_task_id": "b",
                        }
                    ],
                }
            },
        )
    exported = await env.service.export_asset("bob", entry["asset_id"], 1)

    async def catalog():
        """Return two independently compatible receiving tools.

        Returns:
            A catalog whose tools separately satisfy the two declared capabilities.
        """
        return inventory

    db = SQLiteDatabase(str(tmp_path / "receiver.sqlite"))
    roles = RoleAssetService(db, catalog_loader=catalog)
    receiver = WorkflowService(db, roles)
    try:
        transferred = await receiver.import_asset("carol", {"package": exported})
        assert transferred["ready"]
        current = transferred["asset"]
        if wrap:
            child = current["definition"]["allowed_workflows"][0]
            current = await receiver.get_asset(
                "carol", child["asset_id"], child["version"]
            )
        selections = {}
        for task in current["definition"]["tasks"]:
            selected_role = await roles.get_role(
                "carol", task["role_id"], task["role_version"]
            )
            preview = await roles.preview_role(selected_role, task["overrides"])
            selections[task["id"]] = preview["effective"]["tools"]
        assert selections == {"a": ["carol-a"], "b": ["carol-b"]}
        assert (
            await receiver.check_readiness("carol", transferred["asset"]["asset_id"], 1)
        )["ready"]
    finally:
        await receiver.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_local_task_edit_omits_unselected_task_requirement(
    workflow_env, tmp_path
):
    """Transfer a local version that no longer selects an imported task tool.

    Args:
        workflow_env: Public services and the exporting machine's resources.
        tmp_path: Independent receiving store without the old task tool.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "portable-model"
    env.inventory["tools"] = [
        {
            "id": "local-search",
            "fingerprint": "portable-search",
            "capabilities": ["search"],
        }
    ]
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Task evidence",
                "prompt": "Read evidence",
                "tools": [],
            },
            "installation": {
                "bindings": {"model": "model-a", "search": "local-search"}
            },
        },
    )
    source = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Optional task evidence",
                "task": "Read evidence",
                "role_id": role["role_id"],
                "role_version": 1,
                "overrides": {"tools": ["search"]},
            }
        },
    )
    package = await env.service.export_asset("alice", source["asset_id"], 1)
    next(dep for dep in package["roles"][0]["dependencies"] if dep["kind"] == "tools")[
        "capabilities"
    ] = ["search"]
    imported = await env.service.import_asset("bob", {"package": package})
    child = imported["asset"]
    changed = await env.service.save_asset(
        "bob",
        {
            "definition": {**child["definition"], "overrides": {"tools": []}},
            "expected_version": 1,
        },
        child["asset_id"],
    )
    exported = await env.service.export_asset(
        "bob", child["asset_id"], changed["version"]
    )
    inventory = copy.deepcopy(env.inventory)
    inventory["models"][0]["id"] = "carol-model"
    inventory["tools"] = []

    async def catalog():
        """Return the receiver's model and no search tool.

        Returns:
            Resources sufficient for the edited version's current selections.
        """
        return inventory

    db = SQLiteDatabase(str(tmp_path / "receiver.sqlite"))
    receiver = WorkflowService(db, RoleAssetService(db, catalog_loader=catalog))
    try:
        assert not (await receiver.preview_import("old", {"package": package}))["ready"]
        installed = await receiver.import_asset("carol", {"package": exported})
        assert installed["ready"]
        assert (
            await receiver.check_readiness("carol", installed["asset"]["asset_id"], 1)
        )["ready"]
        assert await env.service.export_asset("bob", child["asset_id"], 1) == package
        assert (await env.service.get_asset("bob", child["asset_id"], 1))[
            "definition"
        ] == child["definition"]
    finally:
        await receiver.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "local_edit"),
    [
        ("tools", None),
        ("tools", "rename"),
        ("tools", "remove_selected"),
        ("tools", "import_upgrade"),
        ("skills", None),
        ("knowledge_bases", None),
    ],
)
async def test_recomposition_preserves_scoped_prerequisites(
    workflow_env, tmp_path, kind, local_edit
):
    """Keep declared prerequisites independently of executable resource choices.

    Args:
        workflow_env: Public source services and external resource inventory.
        tmp_path: Separate receiving SQLite directory.
        kind: Resource kind required without appearing in execution selections.
        local_edit: Local rename, selected-tool removal, or a new import contract.
    """
    env = workflow_env
    env.inventory["models"][0]["fingerprint"] = "portable-model"
    env.inventory[kind] = [
        {
            "id": "selected-a",
            "fingerprint": "portable-selected",
            "name": "Selected source",
        },
        {
            "id": "prerequisite-a",
            "fingerprint": "portable-prerequisite",
            "name": "Evidence source",
            "capabilities": ["evidence"],
        },
    ]
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Evidence reader",
                "prompt": "Read evidence",
                "tools": [],
                "skills": [],
                "knowledge_bases": [],
            },
            "installation": {"bindings": {"model": "model-a", "chosen": "selected-a"}},
        },
    )
    source = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Evidence leaf",
                "task": "Read evidence",
                "role_id": role["role_id"],
                "role_version": 1,
                "overrides": {kind: ["chosen"]}
                if local_edit == "remove_selected"
                else {},
            }
        },
    )
    package = await env.service.export_asset("alice", source["asset_id"], 1)
    package["format_version"] = 2
    package["roles"][0]["dependencies"].append(
        {
            "slot": "evidence_prerequisite",
            "kind": kind,
            "fingerprint": "portable-prerequisite",
            "capabilities": ["evidence"],
            "logical_slots": ["evidence_source"],
            "uses": {f"{source['asset_id']}:1": ["evidence_source"]},
        }
    )
    imported = await env.service.import_asset("bob", {"package": package})
    child = imported["asset"]
    assert (await env.service.check_readiness("bob", child["asset_id"], 1))["ready"]
    original = await env.service.export_asset("bob", child["asset_id"], 1)
    selected = child
    if local_edit == "import_upgrade":
        upgraded = copy.deepcopy(package)
        upgraded["version"] = 2
        upgraded["roles"][0]["dependencies"] = [
            dependency
            for dependency in upgraded["roles"][0]["dependencies"]
            if dependency["slot"] != "evidence_prerequisite"
        ]
        selected = (await env.service.import_asset("bob", {"package": upgraded}))[
            "asset"
        ]
    elif local_edit is not None:
        definition = {**child["definition"], "name": "Edited local leaf"}
        if local_edit == "remove_selected":
            definition["overrides"] = {**definition["overrides"], kind: []}
        selected = await env.service.save_asset(
            "bob", {"definition": definition, "expected_version": 1}, child["asset_id"]
        )
    coordinator = await env.roles.save_role(
        "bob",
        {
            "definition": {
                "name": "Coordinator",
                "prompt": "Call the leaf",
                "tools": [],
                "skills": [],
                "knowledge_bases": [],
            },
            "installation": {"bindings": {"model": "model-a"}},
        },
    )
    reference = {"role_id": coordinator["role_id"], "role_version": 1}
    if local_edit == "import_upgrade":
        reference = {"role_id": child["definition"]["role_id"], "role_version": 1}
    parent = await env.service.save_asset(
        "bob",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Evidence wrapper",
                "coordinator": reference,
                "allowed_roles": [reference],
                "allowed_workflows": [
                    {
                        "asset_id": child["asset_id"],
                        "version": selected["version"],
                        "result_task_id": "result",
                    }
                ],
            }
        },
    )
    wrapped = await env.service.export_asset("bob", parent["asset_id"], 1)
    inventory = copy.deepcopy(env.inventory)
    inventory["profiles"][0]["id"] = "carol-profile"
    inventory["models"][0]["id"] = "carol-model"
    prerequisite = {**inventory[kind][1], "id": "carol-prerequisite"}
    inventory[kind] = (
        [{**inventory[kind][0], "id": "carol-selected"}]
        if local_edit != "remove_selected"
        else []
    )

    async def catalog():
        """Return the independent receiver's currently available resources.

        Returns:
            Catalog with the prerequisite initially absent.
        """
        return inventory

    db = SQLiteDatabase(str(tmp_path / "receiver.sqlite"))
    receiver = WorkflowService(db, RoleAssetService(db, catalog_loader=catalog))
    try:
        assert not (await receiver.preview_import("direct", {"package": original}))[
            "ready"
        ]
        if local_edit != "import_upgrade":
            assert not (await receiver.preview_import("carol", {"package": wrapped}))[
                "ready"
            ]
            with pytest.raises(RoleAssetError):
                await receiver.import_asset("carol", {"package": wrapped})
            assert (await receiver.list_assets("carol"))["assets"] == []
            inventory[kind].append(prerequisite)
        else:
            assert (await receiver.preview_import("carol", {"package": wrapped}))[
                "ready"
            ]
        installed = await receiver.import_asset("carol", {"package": wrapped})
        assert installed["ready"]
        assert (
            await receiver.check_readiness("carol", installed["asset"]["asset_id"], 1)
        )["ready"]
        assert await env.service.export_asset("bob", child["asset_id"], 1) == original
        assert (await env.service.get_asset("bob", child["asset_id"], 1))[
            "definition"
        ] == child["definition"]
    finally:
        await receiver.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("optional_field", ["fingerprint", "input_schema"])
async def test_recomposition_preserves_unspecified_constraints(
    workflow_env, tmp_path, optional_field
):
    """Accept a compatible receiver choice when the source omits a restriction.

    Args:
        workflow_env: Public source services and mutable external resources.
        tmp_path: Independent receiving store directory.
        optional_field: Portable model or tool constraint explicitly left unset.
    """
    env = workflow_env
    env.inventory["models"] = [
        {"id": "leaf-a", "fingerprint": "provider-a", "capabilities": ["image"]},
        {"id": "coordinator-a", "fingerprint": "portable-coordinator"},
    ]
    env.inventory["tools"] = [
        {
            "id": "tool-a",
            "fingerprint": "portable-tool",
            "input_schema": {"type": "object", "required": ["query"]},
        }
    ]
    role = await env.roles.save_role(
        "alice",
        {
            "definition": {
                "name": "Optional leaf",
                "prompt": "Read evidence",
                "tools": ["search"] if optional_field == "input_schema" else [],
                "model_capabilities": ["image"],
            },
            "installation": {"bindings": {"model": "leaf-a", "search": "tool-a"}},
        },
    )
    leaf = await env.service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Optional leaf",
                "task": "Read",
                "role_id": role["role_id"],
                "role_version": 1,
            }
        },
    )
    package = await env.service.export_asset("alice", leaf["asset_id"], 1)
    declaration = next(
        dep
        for dep in package["roles"][0]["dependencies"]
        if dep["kind"] == ("models" if optional_field == "fingerprint" else "tools")
    )
    declaration[optional_field] = None
    choices = {
        f"{role['role_id']}:{role['version']}": {
            "profile_id": "default",
            "bindings": {
                dep["slot"]: "leaf-a" if dep["kind"] == "models" else "tool-a"
                for dep in role["dependencies"]
                if dep["kind"] != "profiles"
            },
        }
        for role in package["roles"]
    }
    imported = await env.service.import_asset(
        "bob", {"package": package, "installations": choices}
    )
    coordinator = await env.roles.save_role(
        "bob",
        {
            "definition": {
                "name": "Optional coordinator",
                "prompt": "Call the leaf",
                "tools": [],
            },
            "installation": {"bindings": {"model": "coordinator-a"}},
        },
    )
    reference = {"role_id": coordinator["role_id"], "role_version": 1}
    parent = await env.service.save_asset(
        "bob",
        {
            "definition": {
                "kind": "autonomous",
                "name": "Optional wrapper",
                "coordinator": reference,
                "allowed_roles": [reference],
                "allowed_workflows": [
                    {
                        "asset_id": imported["asset"]["asset_id"],
                        "version": 1,
                        "result_task_id": "result",
                    }
                ],
            }
        },
    )
    wrapped = await env.service.export_asset("bob", parent["asset_id"], 1)
    inventory = copy.deepcopy(env.inventory)
    inventory["profiles"][0]["id"] = "receiver-profile"
    inventory["models"][0]["id"] = "leaf-b"
    if optional_field == "fingerprint":
        inventory["models"][0]["fingerprint"] = "provider-b"
    inventory["models"][1]["id"] = "coordinator-b"
    inventory["tools"][0].update(id="tool-b", input_schema={"type": "object"})

    async def catalog():
        """Return a compatible receiving catalog with different optional metadata.

        Returns:
            Model capabilities and tool identity that satisfy the source contract.
        """
        return inventory

    db = SQLiteDatabase(str(tmp_path / "receiver.sqlite"))
    receiver = WorkflowService(db, RoleAssetService(db, catalog_loader=catalog))
    try:
        for owner, exported in [("direct", package), ("wrapped", wrapped)]:
            selected = {
                f"{role['role_id']}:{role['version']}": {
                    "profile_id": "receiver-profile",
                    "bindings": {
                        dep["slot"]: (
                            "leaf-b"
                            if role["definition"]["name"] == "Optional leaf"
                            else "coordinator-b"
                        )
                        if dep["kind"] == "models"
                        else "tool-b"
                        for dep in role["dependencies"]
                        if dep["kind"] != "profiles"
                    },
                }
                for role in exported["roles"]
            }
            preview = await receiver.preview_import(
                owner, {"package": exported, "installations": selected}
            )
            assert preview["ready"]
            installed = await receiver.import_asset(
                owner, {"package": exported, "installations": selected}
            )
            assert installed["ready"]
            assert (
                await receiver.check_readiness(owner, installed["asset"]["asset_id"], 1)
            )["ready"]
        assert (
            await env.service.export_asset("bob", imported["asset"]["asset_id"], 1)
            == package
        )
    finally:
        await receiver.shutdown()
        await db.engine.dispose()
