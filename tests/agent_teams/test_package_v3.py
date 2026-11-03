"""Version 3 packages relocate fixed definitions across real databases."""

import copy

import pytest
from sqlalchemy import select

from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.po import AgentWorkflowPackage, AgentWorkflowVersion
from astrbot.core.db.sqlite import SQLiteDatabase


@pytest.mark.asyncio
async def test_package_relocates_and_reimports_without_overwriting(tmp_path):
    """Different databases require explicit local resource choices and immutable content.

    Args:
        tmp_path: Isolated source and target databases.
    """
    source_db = SQLiteDatabase(str(tmp_path / "source.db"))
    target_db = SQLiteDatabase(str(tmp_path / "target.db"))

    def inventory(model):
        async def catalog():
            return {
                "profiles": [{"id": "default"}],
                "models": [{"id": model}, {"id": "same"}],
                "personas": [{"id": "persona"}],
                "tools": [],
                "skills": [],
                "knowledge_bases": [],
            }

        return catalog

    source_roles = RoleAssetService(source_db, catalog_loader=inventory("source-model"))
    target_roles = RoleAssetService(target_db, catalog_loader=inventory("target-model"))
    source = WorkflowService(source_db, source_roles)
    target = WorkflowService(target_db, target_roles)
    try:
        role = await source_roles.save_role(
            "alice",
            {
                "definition": {
                    "name": "Writer",
                    "prompt": "Write evidence",
                    "provider_id": "source-model",
                    "persona_id": "persona",
                    "tools": [],
                },
                "installation": {"profile_id": "default"},
            },
        )
        asset = await source.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Report",
                    "result_task_id": "writer",
                    "tasks": [
                        {
                            "id": "writer",
                            "name": "Writer",
                            "member": "writer",
                            "role_id": role["role_id"],
                            "role_version": 1,
                            "task": "Write",
                        }
                    ],
                }
            },
        )
        package = await source.export_asset("alice", asset["asset_id"], 1)
        assert package["format_version"] == 3
        assert {(r["kind"], r["source_id"]) for r in package["requirements"]} == {
            ("profiles", "default"),
            ("models", "source-model"),
            ("personas", "persona"),
        }
        incomplete = copy.deepcopy(package)
        incomplete["requirements"].pop()
        with pytest.raises(RoleAssetError, match="requirements do not match"):
            await target.preview_import("bob", {"package": incomplete})
        extra = copy.deepcopy(package)
        extra["assets"].append(
            {
                "asset_id": "unreachable",
                "version": 1,
                "definition": copy.deepcopy(package["definition"]),
            }
        )
        with pytest.raises(RoleAssetError, match="unreachable"):
            await target.preview_import("bob", {"package": extra})
        cycle = {
            "format": "astrbot.workflow",
            "format_version": 3,
            "asset_id": "cycle",
            "version": 1,
            "definition": {
                "name": "Cycle",
                "result_task_id": "child",
                "tasks": [
                    {
                        "id": "child",
                        "name": "Child",
                        "workflow": {"asset_id": "cycle", "version": 1},
                    }
                ],
            },
            "roles": [],
            "assets": [],
            "requirements": [],
        }
        with pytest.raises(RoleAssetError, match="cycle"):
            await target.preview_import("bob", {"package": cycle})
        unsupported = copy.deepcopy(package)
        unsupported["requirements"][0]["slot"] = "legacy"
        with pytest.raises(RoleAssetError, match="Invalid workflow package"):
            await target.preview_import("bob", {"package": unsupported})
        before = await target.preview_import("bob", {"package": package})
        assert not before["ready"]
        assert all(r["selected"] is None for r in before["requirements"])
        selections = [
            {"kind": kind, "source_id": source_id, "target_id": target_id}
            for kind, source_id, target_id in [
                ("profiles", "default", "default"),
                ("models", "source-model", "target-model"),
                ("personas", "persona", "persona"),
            ]
        ]
        payload = {"package": package, "selections": selections}
        assert (await target.preview_import("bob", payload))["ready"]
        installed = await target.import_asset("bob", payload)
        assert not installed["existing"]
        local_asset = installed["asset"]
        member = local_asset["definition"]["tasks"][0]
        local_role = await target_roles.get_role(
            "bob", member["role_id"], member["role_version"]
        )
        assert local_role["definition"]["provider_id"] == "target-model"
        assert local_role["definition"]["persona_id"] == "persona"
        assert (await target.check_readiness("bob", local_asset["asset_id"], 1))[
            "ready"
        ]
        import asyncio

        concurrent = await asyncio.gather(
            target.import_asset("dave", payload),
            target.import_asset("dave", payload),
            return_exceptions=True,
        )
        assert any(isinstance(result, dict) for result in concurrent)
        assert all(
            isinstance(result, dict)
            or isinstance(result, RoleAssetError)
            and result.status == 409
            for result in concurrent
        )
        stable = await target.import_asset("dave", payload)
        assert stable["existing"]
        assert len((await target.list_assets("dave"))["assets"]) == 1
        again = await target.import_asset("bob", payload)
        assert (
            again["existing"] and again["asset"]["asset_id"] == local_asset["asset_id"]
        )
        changed = copy.deepcopy(payload)
        changed["selections"][1]["target_id"] = "same"
        with pytest.raises(
            RoleAssetError, match="different content or resource selections"
        ):
            await target.import_asset("bob", changed)
        altered_role = copy.deepcopy(payload)
        altered_role["package"]["asset_id"] = "independent-entry"
        altered_role["package"]["roles"][0]["definition"]["prompt"] = (
            "Different responsibility"
        )
        conflict = await target.preview_import("bob", altered_role)
        assert not conflict["ready"] and any(
            issue["code"] == "CONFLICT" for issue in conflict["issues"]
        )
        with pytest.raises(
            RoleAssetError, match="different content or resource selections"
        ):
            await target.import_asset("bob", altered_role)
        changed = copy.deepcopy(payload)
        changed["package"]["definition"]["name"] = "Changed"
        with pytest.raises(
            RoleAssetError, match="different content or resource selections"
        ):
            await target.import_asset("bob", changed)
        async with target_db.get_db() as session:
            assert (
                len(
                    (
                        await session.execute(
                            select(AgentWorkflowPackage).where(
                                AgentWorkflowPackage.owner_username == "bob"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                == 1
            )
            assert (
                len(
                    (
                        await session.execute(
                            select(AgentWorkflowVersion).where(
                                AgentWorkflowVersion.owner_username == "bob"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                == 1
            )
        assert (await target.export_asset("bob", local_asset["asset_id"], 1))[
            "requirements"
        ][0]["source_id"] != "source-model"
        old = copy.deepcopy(package)
        old["format_version"] = 2
        with pytest.raises(RoleAssetError, match="Invalid workflow package"):
            await target.preview_import("bob", {"package": old})
    finally:
        await source_db.engine.dispose()
        await target_db.engine.dispose()


@pytest.mark.asyncio
async def test_nested_package_runs_and_failed_commit_rolls_back(tmp_path):
    """Relocate nested workflow and team, then run the ordinary public entry.

    Args:
        tmp_path: Independent resource databases.
    """
    import asyncio

    from sqlalchemy import event
    from sqlalchemy.exc import IntegrityError

    from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
    from astrbot.core.agent_team_tools import AgentTeamToolRegistry
    from astrbot.dashboard.services.agent_team_ports import TeamPorts

    async def source_catalog():
        return {
            "profiles": [{"id": "source-profile"}],
            "models": [{"id": "same"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    async def target_catalog():
        return {
            "profiles": [{"id": "target-profile"}],
            "models": [{"id": "same"}, {"id": "target-model"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    source_db = SQLiteDatabase(str(tmp_path / "source-nested.db"))
    target_db = SQLiteDatabase(str(tmp_path / "target-nested.db"))
    source_roles = RoleAssetService(source_db, catalog_loader=source_catalog)
    target_roles = RoleAssetService(target_db, catalog_loader=target_catalog)
    source = WorkflowService(source_db, source_roles)
    target = WorkflowService(target_db, target_roles)
    try:
        role = await source_roles.save_role(
            "alice",
            {
                "definition": {
                    "name": "Writer",
                    "prompt": "Responsibility",
                    "provider_id": "same",
                    "tools": None,
                },
                "installation": {"profile_id": "source-profile"},
            },
        )
        leaf = await source.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Leaf",
                    "required_inputs": ["goal"],
                    "result_task_id": "writer",
                    "tasks": [
                        {
                            "id": "writer",
                            "name": "Writer",
                            "member": "writer",
                            "role_id": role["role_id"],
                            "role_version": 1,
                            "overrides": {"persona_id": None, "tools": []},
                            "task": "Write {goal}",
                            "inputs": {"goal": {"input": "goal"}},
                        }
                    ],
                }
            },
        )
        team = await source.save_asset(
            "alice",
            {
                "definition": {
                    "kind": "autonomous",
                    "name": "Team",
                    "coordinator": {
                        "id": "coordinator",
                        "role": {
                            "name": "Lead",
                            "prompt": "Coordinate",
                            "provider_id": "same",
                        },
                        "installation": {"profile_id": "source-profile"},
                    },
                    "allowed_roles": [
                        {"id": "member", "role_id": role["role_id"], "role_version": 1}
                    ],
                    "allowed_workflows": [{"asset_id": leaf["asset_id"], "version": 1}],
                }
            },
        )
        parent = await source.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Parent",
                    "required_inputs": ["goal"],
                    "result_task_id": "team",
                    "max_depth": 4,
                    "tasks": [
                        {
                            "id": "team",
                            "name": "Team",
                            "team": {"asset_id": team["asset_id"], "version": 1},
                            "inputs": {"goal": {"input": "goal"}},
                        }
                    ],
                }
            },
        )
        package = await source.export_asset("alice", parent["asset_id"], 1)
        assert len(package["assets"]) == 2 and len(package["roles"]) == 1
        selections = [
            {
                "kind": "profiles",
                "source_id": "source-profile",
                "target_id": "target-profile",
            },
            {"kind": "models", "source_id": "same", "target_id": "target-model"},
        ]
        payload = {"package": package, "selections": selections}
        assert (await target.preview_import("bob", payload))["ready"]

        def fail_insert(
            connection, cursor, statement, parameters, context, executemany
        ):
            if (
                statement.lstrip()
                .upper()
                .startswith("INSERT INTO AGENTWORKFLOWPACKAGE")
            ):
                raise IntegrityError(statement, parameters, Exception("injected"))

        event.listen(target_db.engine.sync_engine, "before_cursor_execute", fail_insert)
        try:
            with pytest.raises(RoleAssetError, match="conflicted"):
                await target.import_asset("bob", payload)
        finally:
            event.remove(
                target_db.engine.sync_engine, "before_cursor_execute", fail_insert
            )
        async with target_db.get_db() as session:
            assert (
                not (await session.execute(select(AgentWorkflowVersion)))
                .scalars()
                .all()
            )
            assert (
                not (await session.execute(select(AgentWorkflowPackage)))
                .scalars()
                .all()
            )
        assert (await target_roles.list_roles("bob"))["roles"] == []
        installed = await target.import_asset("bob", payload)
        local_parent = installed["asset"]
        leaf_package = await source.export_asset("alice", leaf["asset_id"], 1)
        leaf_result = await target.import_asset(
            "bob", {"package": leaf_package, "selections": selections}
        )
        assert leaf_result["existing"]
        local_team_ref = local_parent["definition"]["tasks"][0]["team"]
        local_team = await target.get_asset(
            "bob", local_team_ref["asset_id"], local_team_ref["version"]
        )
        assert (
            local_team["definition"]["coordinator"]["role"]["provider_id"]
            == "target-model"
        )
        child_ref = local_team["definition"]["allowed_workflows"][0]
        child = await target.get_asset(
            "bob", child_ref["asset_id"], child_ref["version"]
        )
        assert child["definition"]["tasks"][0]["overrides"] == {
            "persona_id": None,
            "tools": [],
        }
        observed = {}

        async def deliver(
            session_id, text, context=None, execution_token=None, message_id=None
        ):
            binding = AgentTeamExecutionRegistry.resolve(execution_token)
            snapshot = (await target.get_run("bob", binding.run_id))["snapshot"]
            observed[session_id] = (text, context, snapshot["effective"]["provider_id"])
            return message_id

        async def collect(session_id, message_id, member_id):
            tools = AgentTeamToolRegistry.get_tools(
                f"webchat:FriendMessage:{session_id}"
            )
            await next(
                tool for tool in tools if tool.name == "submit_task_result"
            ).call(None, text=observed[session_id][0])
            return "Done", []

        target.ports_factory = lambda _: TeamPorts(
            deliver=deliver,
            collect=collect,
            is_busy=lambda _: False,
            emit=lambda _: None,
        )
        run = await target.start_run(
            "bob",
            local_parent["asset_id"],
            {"version": 1, "inputs": {"goal": "Test goal"}},
        )
        for _ in range(200):
            result = await target.get_run("bob", run["run_id"])
            if result["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert result["status"] == "completed", result
        assert result["result"]["text"] == "Test goal"
        assert observed
        assert {item[2] for item in observed.values()} == {"target-model"}
    finally:
        await target.shutdown()
        await source_db.engine.dispose()
        await target_db.engine.dispose()


@pytest.mark.asyncio
async def test_same_identity_child_version_keeps_entry_latest(tmp_path):
    """The selected entry stays latest when its earlier version is also included.

    Args:
        tmp_path: Independent databases for the source and target.
    """

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "model"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    source_db = SQLiteDatabase(str(tmp_path / "versions-source.db"))
    target_db = SQLiteDatabase(str(tmp_path / "versions-target.db"))
    source = WorkflowService(
        source_db, RoleAssetService(source_db, catalog_loader=catalog)
    )
    target = WorkflowService(
        target_db, RoleAssetService(target_db, catalog_loader=catalog)
    )
    try:
        first = await source.save_asset(
            "alice",
            {
                "definition": {
                    "name": "First",
                    "result_task_id": "writer",
                    "tasks": [
                        {
                            "id": "writer",
                            "name": "Writer",
                            "member": "writer",
                            "role": {
                                "name": "Writer",
                                "prompt": "Write",
                                "provider_id": "model",
                            },
                            "installation": {"profile_id": "default"},
                            "task": "Write",
                        }
                    ],
                }
            },
        )
        await source.save_asset(
            "alice",
            {
                "expected_version": 1,
                "definition": {
                    "name": "Second",
                    "result_task_id": "child",
                    "tasks": [
                        {
                            "id": "child",
                            "name": "Child",
                            "workflow": {"asset_id": first["asset_id"], "version": 1},
                        }
                    ],
                },
            },
            first["asset_id"],
        )
        package = await source.export_asset("alice", first["asset_id"], 2)
        selections = [
            {
                "kind": r["kind"],
                "source_id": r["source_id"],
                "target_id": r["source_id"],
            }
            for r in package["requirements"]
        ]
        result = await target.import_asset(
            "bob", {"package": package, "selections": selections}
        )
        local = result["asset"]
        assert local["definition"]["name"] == "Second"
        assert (await target.get_asset("bob", local["asset_id"]))["version"] == local[
            "version"
        ]
        ref = local["definition"]["tasks"][0]["workflow"]
        assert (
            ref["asset_id"] == local["asset_id"] and ref["version"] != local["version"]
        )
        assert (await target.get_asset("bob", ref["asset_id"], ref["version"]))[
            "definition"
        ]["name"] == "First"
        with pytest.raises(RoleAssetError):
            await target.get_asset("eve", local["asset_id"], local["version"])
    finally:
        await source_db.engine.dispose()
        await target_db.engine.dispose()


@pytest.mark.asyncio
async def test_package_http_validates_format_and_installs_selected_resources(tmp_path):
    """The authenticated HTTP endpoints share version and selection validation.

    Args:
        tmp_path: Independent source and target databases.
    """
    from types import SimpleNamespace

    import httpx
    from fastapi import FastAPI

    from astrbot.dashboard.api.auth import require_dashboard_user
    from astrbot.dashboard.api.workflow_assets import router

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "model"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    source_db = SQLiteDatabase(str(tmp_path / "http-source.db"))
    target_db = SQLiteDatabase(str(tmp_path / "http-target.db"))
    source = WorkflowService(
        source_db, RoleAssetService(source_db, catalog_loader=catalog)
    )
    target = WorkflowService(
        target_db, RoleAssetService(target_db, catalog_loader=catalog)
    )
    try:
        asset = await source.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Report",
                    "result_task_id": "writer",
                    "tasks": [
                        {
                            "id": "writer",
                            "name": "Writer",
                            "member": "writer",
                            "role": {
                                "name": "Writer",
                                "prompt": "Write",
                                "provider_id": "model",
                            },
                            "installation": {"profile_id": "default"},
                            "task": "Write",
                        }
                    ],
                }
            },
        )
        package = await source.export_asset("alice", asset["asset_id"], 1)
        app = FastAPI()
        app.state.services = SimpleNamespace(workflow_assets=target)
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[require_dashboard_user] = lambda: "bob"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for value in (1, 2, 3.0, True):
                old = {**package, "format_version": value}
                response = await client.post(
                    "/api/v1/workflow_assets/import/preview", json={"package": old}
                )
                assert response.status_code == 422
            response = await client.post(
                "/api/v1/workflow_assets/import",
                json={"package": package, "save_pending": True},
            )
            assert response.status_code == 422
            response = await client.post(
                "/api/v1/workflow_assets/import/preview", json={"package": package}
            )
            assert response.status_code == 200 and not response.json()["data"]["ready"]
            selections = [
                {
                    "kind": r["kind"],
                    "source_id": r["source_id"],
                    "target_id": r["source_id"],
                }
                for r in package["requirements"]
            ]
            response = await client.post(
                "/api/v1/workflow_assets/import",
                json={"package": package, "selections": selections},
            )
            assert response.status_code == 200, response.text
            assert response.json()["data"]["ready"]
    finally:
        await source_db.engine.dispose()
        await target_db.engine.dispose()


@pytest.mark.asyncio
async def test_six_resource_kinds_require_explicit_compatible_authorized_choices(
    tmp_path,
):
    """Identical IDs across kinds remain distinct and permissions are rechecked.

    Args:
        tmp_path: Source and target database directory.
    """
    kinds = ("profiles", "models", "personas", "tools", "skills", "knowledge_bases")
    source_catalog = {kind: [{"id": "same"}] for kind in kinds}
    source_catalog["models"][0]["fingerprint"] = "model-fingerprint"
    source_catalog["tools"][0]["input_schema"] = {"type": "object"}
    target_catalog = {kind: [{"id": f"local-{kind}"}] for kind in kinds}
    target_catalog["profiles"][0]["provider_pool"] = ["local-models"]
    target_catalog["models"][0]["fingerprint"] = "model-fingerprint"
    target_catalog["models"].append({"id": "same", "fingerprint": "different"})
    target_catalog["tools"][0]["input_schema"] = {"type": "object"}

    async def source_loader():
        return source_catalog

    async def target_loader():
        return target_catalog

    source_db = SQLiteDatabase(str(tmp_path / "all-source.db"))
    target_db = SQLiteDatabase(str(tmp_path / "all-target.db"))
    source = WorkflowService(
        source_db, RoleAssetService(source_db, catalog_loader=source_loader)
    )
    target = WorkflowService(
        target_db, RoleAssetService(target_db, catalog_loader=target_loader)
    )
    try:
        definition = {
            "name": "Writer",
            "prompt": "Write",
            "provider_id": "same",
            "persona_id": "same",
            "tools": ["same"],
            "skills": ["same"],
            "knowledge_bases": ["same"],
        }
        asset = await source.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Report",
                    "result_task_id": "writer",
                    "tasks": [
                        {
                            "id": "writer",
                            "name": "Writer",
                            "member": "writer",
                            "role": definition,
                            "installation": {"profile_id": "same"},
                            "task": "Write",
                        }
                    ],
                }
            },
        )
        package = await source.export_asset("alice", asset["asset_id"], 1)
        assert {(r["kind"], r["source_id"]) for r in package["requirements"]} == {
            (kind, "same") for kind in kinds
        }
        preview = await target.preview_import("bob", {"package": package})
        model = next(
            item for item in preview["requirements"] if item["kind"] == "models"
        )
        same_id = next(c for c in model["candidates"] if c["id"] == "same")
        assert not same_id["disabled"] and same_id["warnings"]
        selections = [
            {"kind": kind, "source_id": "same", "target_id": f"local-{kind}"}
            for kind in kinds
        ]
        assert (
            await target.preview_import(
                "bob", {"package": package, "selections": selections}
            )
        )["ready"]
        target_catalog["profiles"][0]["provider_pool"] = []
        denied = await target.preview_import(
            "bob", {"package": package, "selections": selections}
        )
        assert not denied["ready"] and any(
            i["code"] == "FORBIDDEN" for i in denied["issues"]
        )
        with pytest.raises(RoleAssetError):
            await target.import_asset(
                "bob", {"package": package, "selections": selections}
            )
        assert (await target.list_assets("bob"))["assets"] == []
        target_catalog["profiles"][0]["provider_pool"] = ["local-models", "same"]
        different_model = copy.deepcopy(selections)
        next(item for item in different_model if item["kind"] == "models")[
            "target_id"
        ] = "same"
        assert (
            await target.preview_import(
                "charlie", {"package": package, "selections": different_model}
            )
        )["ready"]
        different_import = await target.import_asset(
            "charlie", {"package": package, "selections": different_model}
        )
        assert (
            different_import["asset"]["definition"]["tasks"][0]["role"]["provider_id"]
            == "same"
        )
        result = await target.import_asset(
            "bob", {"package": package, "selections": selections}
        )
        member = result["asset"]["definition"]["tasks"][0]
        assert member["installation"]["profile_id"] == "local-profiles"
        assert member["role"]["provider_id"] == "local-models"
        assert member["role"]["persona_id"] == "local-personas"
        assert all(
            member["role"][kind] == [f"local-{kind}"]
            for kind in ("tools", "skills", "knowledge_bases")
        )
    finally:
        await source_db.engine.dispose()
        await target_db.engine.dispose()


@pytest.mark.asyncio
async def test_preview_rejects_nested_effective_depth_limit(tmp_path):
    """A shared terminal member must satisfy limits at every reachable depth.

    Args:
        tmp_path: Source and target database directory.
    """

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "model"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    source_db = SQLiteDatabase(str(tmp_path / "depth-source.db"))
    target_db = SQLiteDatabase(str(tmp_path / "depth-target.db"))
    source = WorkflowService(
        source_db, RoleAssetService(source_db, catalog_loader=catalog)
    )
    target = WorkflowService(
        target_db, RoleAssetService(target_db, catalog_loader=catalog)
    )
    try:
        leaf = await source.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Leaf",
                    "result_task_id": "writer",
                    "tasks": [
                        {
                            "id": "writer",
                            "name": "Writer",
                            "member": "writer",
                            "role": {
                                "name": "Writer",
                                "prompt": "Write",
                                "provider_id": "model",
                                "max_depth": 1,
                            },
                            "installation": {"profile_id": "default"},
                            "task": "Write",
                        }
                    ],
                }
            },
        )
        middle = await source.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Middle",
                    "result_task_id": "child",
                    "tasks": [
                        {
                            "id": "child",
                            "name": "Child",
                            "workflow": {"asset_id": leaf["asset_id"], "version": 1},
                        }
                    ],
                }
            },
        )
        outer = await source.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Outer",
                    "max_depth": 4,
                    "result_task_id": "deep",
                    "tasks": [
                        {
                            "id": "direct",
                            "name": "Direct",
                            "workflow": {"asset_id": leaf["asset_id"], "version": 1},
                        },
                        {
                            "id": "deep",
                            "name": "Deep",
                            "workflow": {"asset_id": middle["asset_id"], "version": 1},
                        },
                    ],
                }
            },
        )
        package = await source.export_asset("alice", outer["asset_id"], 1)
        selections = [
            {
                "kind": r["kind"],
                "source_id": r["source_id"],
                "target_id": r["source_id"],
            }
            for r in package["requirements"]
        ]
        preview = await target.preview_import(
            "bob", {"package": package, "selections": selections}
        )
        assert not preview["ready"] and any(
            issue["code"] == "LIMIT" for issue in preview["issues"]
        )
        assert (await target.list_assets("bob"))["assets"] == []
    finally:
        await source_db.engine.dispose()
        await target_db.engine.dispose()


@pytest.mark.asyncio
async def test_distinct_pinned_role_versions_share_local_identity(tmp_path):
    """Two versions of one source role remain versions of one local role.

    Args:
        tmp_path: Separate source and target databases.
    """

    async def catalog():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "model"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    source_db = SQLiteDatabase(str(tmp_path / "role-source.db"))
    target_db = SQLiteDatabase(str(tmp_path / "role-target.db"))
    roles = RoleAssetService(source_db, catalog_loader=catalog)
    source = WorkflowService(source_db, roles)
    target = WorkflowService(
        target_db, RoleAssetService(target_db, catalog_loader=catalog)
    )
    try:
        role = await roles.save_role(
            "alice",
            {
                "definition": {
                    "name": "Writer",
                    "prompt": "Version one",
                    "provider_id": "model",
                },
                "installation": {"profile_id": "default"},
            },
        )
        await roles.save_role(
            "alice",
            {
                "definition": {
                    "name": "Writer",
                    "prompt": "Version two",
                    "provider_id": "model",
                },
                "installation": {"profile_id": "default"},
                "expected_version": 1,
            },
            role["role_id"],
        )
        leaves = []
        for version in (1, 2):
            leaves.append(
                await source.save_asset(
                    "alice",
                    {
                        "definition": {
                            "name": f"Leaf {version}",
                            "result_task_id": "writer",
                            "tasks": [
                                {
                                    "id": "writer",
                                    "name": "Writer",
                                    "member": "writer",
                                    "role_id": role["role_id"],
                                    "role_version": version,
                                    "task": "Write",
                                }
                            ],
                        }
                    },
                )
            )
        root = await source.save_asset(
            "alice",
            {
                "definition": {
                    "name": "Both",
                    "result_task_id": "second",
                    "tasks": [
                        {
                            "id": name,
                            "name": name,
                            "workflow": {"asset_id": leaf["asset_id"], "version": 1},
                        }
                        for name, leaf in zip(("first", "second"), leaves)
                    ],
                }
            },
        )
        package = await source.export_asset("alice", root["asset_id"], 1)
        selections = [
            {
                "kind": r["kind"],
                "source_id": r["source_id"],
                "target_id": r["source_id"],
            }
            for r in package["requirements"]
        ]
        installed = await target.import_asset(
            "bob", {"package": package, "selections": selections}
        )
        members = []
        for task in installed["asset"]["definition"]["tasks"]:
            ref = task["workflow"]
            child = await target.get_asset("bob", ref["asset_id"], ref["version"])
            members.append(child["definition"]["tasks"][0])
        assert members[0]["role_id"] == members[1]["role_id"]
        assert {member["role_version"] for member in members} == {1, 2}
        assert (
            await target.roles.get_role(
                "bob", members[0]["role_id"], members[0]["role_version"]
            )
        )["definition"]["prompt"] == "Version one"
        assert (
            await target.roles.get_role(
                "bob", members[1]["role_id"], members[1]["role_version"]
            )
        )["definition"]["prompt"] == "Version two"
    finally:
        await source_db.engine.dispose()
        await target_db.engine.dispose()
