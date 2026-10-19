"""Keep legacy resource identities and input meanings through public migration."""

import asyncio
import copy
from types import SimpleNamespace

import pytest

from astrbot.api import sp
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.migration import LegacyMigrationService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
from astrbot.core.config import AstrBotConfig
from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.persona_mgr import PersonaManager
from astrbot.core.umop_config_router import UmopConfigRouter
from astrbot.core.utils.shared_preferences import SharedPreferences
from astrbot.dashboard.services.agent_team_dag import render_task
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from astrbot.dashboard.services.agent_team_service import AgentTeamService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        "member",
        "node",
        "profile",
        "routed_profile",
        "session",
        "missing_session",
        "missing",
        "ambiguous",
    ],
)
@pytest.mark.parametrize("collision", [False, True], ids=["unique", "name_equals_id"])
async def test_migration_preserves_named_and_session_kb_identities(
    tmp_path, source, collision
):
    """Keep name-based selections distinct from existing session ID selections.

    Args:
        tmp_path: Isolated real configuration and SQLite storage.
        source: Legacy setting supplying the effective knowledge selection.
        collision: Whether knowledge names and IDs overlap.
    """
    first_id, second_id = "a" * 32, "b" * 32
    selected_name = first_id if collision else "Research evidence"
    inventory = [
        {"id": first_id, "name": "Other evidence"},
        {"id": second_id, "name": selected_name},
    ]

    class CatalogKnowledgeManager:
        async def get_kb_by_name(self, name):
            """Resolve an external resource by its legacy name.

            Args:
                name: Exact legacy knowledge-base name.

            Returns:
                Catalog resource identity, or None for an unavailable name.
            """
            item = next((item for item in inventory if item["name"] == name), None)
            return (
                SimpleNamespace(kb=SimpleNamespace(kb_id=item["id"])) if item else None
            )

    db = SQLiteDatabase(str(tmp_path / "knowledge.db"))
    preferences = SharedPreferences(db)
    config = AstrBotConfig(str(tmp_path / "config.json"), copy.deepcopy(DEFAULT_CONFIG))
    if source in ("profile", "routed_profile"):
        config["kb_names"] = [selected_name]
    configs = AstrBotConfigManager(config, UmopConfigRouter(preferences), preferences)
    await configs.initialize()
    personas = PersonaManager(db, configs)
    await personas.create_persona("editor", "Use the selected evidence.")
    core = SimpleNamespace(
        kb_manager=CatalogKnowledgeManager(),
        astrbot_config_mgr=configs,
        persona_mgr=personas,
    )

    async def catalog():
        """Expose the controlled external resource directory.

        Returns:
            Current resources and the actual profile's knowledge names.
        """
        return {
            "profiles": [{"id": "default", "knowledge_bases": config["kb_names"]}],
            "models": [{"id": "model"}],
            "personas": [{"id": "editor", "prompt": "Use the selected evidence."}],
            "tools": [],
            "skills": [],
            "knowledge_bases": inventory,
        }

    roles = RoleAssetService(db, core, catalog)
    workflows = WorkflowService(db, roles)
    migration = LegacyMigrationService(db, roles, workflows)
    legacy = AgentTeamService(db, core, None)
    umo = f"webchat:FriendMessage:identity-{source}-{collision}"
    try:
        await db.create_agent_team(
            team_id="identity-team",
            owner_username="alice",
            name="Knowledge identities",
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
                    "member_id": "reader",
                    "name": "Reader",
                    "provider_id": "model",
                    "persona_id": "editor",
                    "system_prompt": "Read evidence.",
                    "umo": umo,
                },
            ],
        )
        if source in ("member", "session", "missing_session", "missing", "ambiguous"):
            await legacy.update_member(
                "alice",
                "identity-team",
                "reader",
                {
                    "runner_config": {
                        "config_id": "default",
                        "kb_names": [
                            "Other evidence"
                            if source in ("session", "missing_session")
                            else selected_name
                        ],
                    }
                },
            )
            expected = [
                first_id if source in ("session", "missing_session") else second_id
            ]
            assert (await sp.session_get(umo, "kb_config"))["kb_ids"] == expected
            if source in ("session", "missing_session"):
                await legacy.update_member(
                    "alice",
                    "identity-team",
                    "reader",
                    {"runner_config": {"config_id": "default"}},
                )
        else:
            expected = [second_id]
            if source != "routed_profile":
                await legacy.update_member(
                    "alice",
                    "identity-team",
                    "reader",
                    {"runner_config": {"config_id": "default"}},
                )
        flow = await legacy.create_workflow(
            "alice",
            "identity-team",
            {
                "name": "Read evidence",
                "graph": {
                    "nodes": [
                        {
                            "id": "read",
                            "member_id": "reader",
                            "task": "Read {{input}}",
                            **(
                                {"execution": {"kb_names": [selected_name]}}
                                if source == "node"
                                else {}
                            ),
                        }
                    ],
                    "edges": [],
                },
            },
        )
        if source == "missing_session":
            inventory.pop(0)
        elif source == "missing":
            inventory.pop()
        elif source == "ambiguous":
            inventory.append({"id": "c" * 32, "name": selected_name})
        proposal = await migration.preview("alice", "identity-team")
        receipt = await migration.confirm(
            "alice",
            "identity-team",
            {"preview_token": proposal["preview_token"], "save_pending": True},
        )
        asset_id = receipt["mapping"]["workflows"][flow["workflow_id"]]["asset_id"]
        asset = await workflows.get_asset("alice", asset_id)
        role = await roles.get_role("alice", asset["definition"]["tasks"][0]["role_id"])
        resolved = await roles.preview_role(role)
        if source in ("missing", "ambiguous", "missing_session"):
            assert not resolved["ready"]
            if source == "missing_session":
                assert second_id not in resolved["effective"]["knowledge_bases"]
                assert role["installation"]["bindings"]["knowledge_bases_0"] == first_id
            else:
                assert first_id not in resolved["effective"]["knowledge_bases"]
                assert role["definition"]["migration"]["source"]["execution"][
                    "kb_names"
                ] == [selected_name]
            assert any(
                issue["code"] == ("AMBIGUOUS" if source == "ambiguous" else "MISSING")
                for issue in resolved["issues"]
            )
            assert not (await workflows.check_readiness("alice", asset_id))["ready"]
            with pytest.raises(RoleAssetError):
                await workflows.start_run(
                    "alice",
                    asset_id,
                    {"version": 1, "inputs": {"input": "Read evidence"}},
                )
            return
        assert resolved["effective"]["knowledge_bases"] == expected
        assert resolved["ready"], resolved["issues"]
        assert (await workflows.check_readiness("alice", asset_id))["ready"]
    finally:
        await workflows.shutdown()
        await preferences.close()
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["control", "collision", "repeated", "explicit_input", "root_input", "named_input"],
)
async def test_migrated_execution_preserves_placeholder_positions_and_explicit_inputs(
    tmp_path, case
):
    """Deliver each old result at its original position without replacing inputs.

    Args:
        tmp_path: Isolated real SQLite persistence.
        case: Unique names, generated-name collision, repetition or a saved input.
    """
    first_id = "first" if case in ("control", "named_input") else "1"
    second_id = "legacy_input_0" if case in ("collision", "repeated") else "second"
    template = "Compare {{" + first_id + "}} and {{" + second_id + "}}"
    if case == "repeated":
        template += "; repeat {{1}}"
    expected = (
        "Compare FIRST and SECOND; repeat FIRST"
        if case == "repeated"
        else "Compare FIRST and SECOND"
    )
    assert (
        render_task(
            template,
            "Compare evidence",
            {first_id: "FIRST", second_id: "SECOND"},
            10000,
        )
        == expected
    )
    delivered = []
    tasks_by_session = {}

    async def catalog():
        """Expose a controlled external resource directory.

        Returns:
            Available model and default profile without optional resources.
        """
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "model"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    async def deliver(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        """Record the externally delivered task.

        Args:
            session_id: New run member session.
            text: Fully rendered task delivered to the model boundary.
            context: Temporary execution framing.
            execution_token: Current registered execution scope.
            message_id: Correlation assigned by the production service.

        Returns:
            The admitted message identity.
        """
        delivered.append(text)
        tasks_by_session[session_id] = text
        return message_id

    async def collect(session_id, message_id, member_id):
        """Submit a controlled external result through the real public tool.

        Args:
            session_id: New run member session.
            message_id: Admitted message identity.
            member_id: Current member identity.

        Returns:
            External reply and message parts after real result submission.
        """
        task = tasks_by_session[session_id]
        result = (
            "FIRST"
            if task == "Produce FIRST"
            else "SECOND"
            if task == "Produce SECOND"
            else "Compared"
        )
        tools = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")
        assert await tools[0].call(None, text=result) == "Result accepted."
        return "Done", []

    db = SQLiteDatabase(str(tmp_path / "placeholders.db"))
    roles = RoleAssetService(db, catalog_loader=catalog)
    workflows = WorkflowService(
        db,
        roles,
        ports_factory=lambda _: TeamPorts(
            deliver=deliver,
            collect=collect,
            is_busy=lambda _: False,
            emit=lambda _: None,
        ),
    )
    migration = LegacyMigrationService(db, roles, workflows)
    legacy = AgentTeamService(db, None, None)
    try:
        await db.create_agent_team(
            team_id="sources",
            owner_username="alice",
            name="Sources",
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
                    "system_prompt": "Use both sources.",
                },
            ],
        )
        source = await legacy.create_workflow(
            "alice",
            "sources",
            {
                "name": "Compare sources",
                "graph": {
                    "nodes": [
                        {
                            "id": first_id,
                            "member_id": "worker",
                            "task": "Produce FIRST",
                        },
                        {
                            "id": second_id,
                            "member_id": "worker",
                            "task": "Produce SECOND",
                        },
                        {
                            "id": "compare",
                            "member_id": "worker",
                            "task": template,
                            **(
                                {
                                    "inputs": {
                                        "first"
                                        if case == "named_input"
                                        else "legacy_input_0": {"input": "input"}
                                    }
                                }
                                if case in ("explicit_input", "named_input")
                                else {}
                            ),
                        },
                    ],
                    "edges": [
                        {"from": first_id, "to": "compare"},
                        {"from": second_id, "to": "compare"},
                    ],
                    **(
                        {"required_inputs": ["input", "legacy_input_0"]}
                        if case == "root_input"
                        else {}
                    ),
                },
            },
        )
        if case == "root_input":
            # The old editor omits root contracts; retain this stored declarative extension.
            await db.update_agent_team_workflow(
                source["workflow_id"],
                graph={
                    **source["graph"],
                    "required_inputs": ["input", "legacy_input_0"],
                },
            )
        proposal = await migration.preview("alice", "sources")
        assert (await migration.preview("alice", "sources"))[
            "preview_token"
        ] == proposal["preview_token"]
        receipt = await migration.confirm(
            "alice",
            "sources",
            {"preview_token": proposal["preview_token"], "save_pending": True},
        )
        asset_id = receipt["mapping"]["workflows"][source["workflow_id"]]["asset_id"]
        assert (await workflows.check_readiness("alice", asset_id))["ready"]
        run = await workflows.start_run(
            "alice",
            asset_id,
            {
                "version": 1,
                "inputs": {
                    "input": "Compare evidence",
                    **(
                        {"legacy_input_0": "Original root input"}
                        if case == "root_input"
                        else {}
                    ),
                },
            },
        )
        for _ in range(750):
            current = await workflows.get_run("alice", run["run_id"])
            if current["status"] not in ("running", "pending"):
                break
            await asyncio.sleep(0.02)
        assert current["status"] == "completed", current
        assert (
            next(
                text for text in delivered if text.startswith("Compare ")
            ).splitlines()[0]
            == expected
        )
        if case in ("explicit_input", "root_input", "named_input"):
            compared = next(
                task
                for task in current["tasks"]
                if task["snapshot"].get("node_id") == "compare"
            )
            assert compared["snapshot"]["inputs"][
                "first" if case == "named_input" else "legacy_input_0"
            ] == ("Original root input" if case == "root_input" else "Compare evidence")
    finally:
        await workflows.shutdown()
        await db.engine.dispose()
