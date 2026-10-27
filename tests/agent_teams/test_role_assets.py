"""Role assets through the shared public service and real SQLite storage."""

from types import SimpleNamespace

import pytest

from astrbot.api import AstrBotConfig
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager
from astrbot.core.persona_mgr import PersonaManager
from astrbot.core.provider.manager import ProviderManager
from astrbot.core.skills.skill_manager import SkillManager
from astrbot.core.star import star
from astrbot.core.umop_config_router import UmopConfigRouter
from astrbot.core.utils.shared_preferences import SharedPreferences


@pytest.mark.asyncio
async def test_role_reopens_without_a_chat_session(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "roles.db"))
    service = RoleAssetService(db)
    role = await service.save_role(
        "alice", {"definition": {"name": "Researcher", "prompt": "Check evidence."}}
    )
    await db.engine.dispose()
    reopened_db = SQLiteDatabase(str(tmp_path / "roles.db"))
    reopened = await RoleAssetService(reopened_db).get_role("alice", role["role_id"], 1)
    assert reopened["definition"] == role["definition"]
    assert reopened["role_id"] == role["role_id"]
    assert reopened["version"] == 1
    assert await reopened_db.get_platform_sessions_by_creator("alice") == []
    await reopened_db.engine.dispose()


@pytest.mark.asyncio
async def test_unsaved_preview_distinguishes_persona_content_and_disabled_capabilities(
    tmp_path,
):
    db = SQLiteDatabase(str(tmp_path / "roles.db"))
    inventory = {
        "profiles": [{"id": "default", "name": "Default"}],
        "models": [{"id": "m", "name": "Model", "capabilities": []}],
        "personas": [
            {
                "id": "local-persona",
                "name": "Reviewer",
                "prompt": "Find errors.",
                "tools": ["search"],
                "skills": [],
            }
        ],
        "tools": [{"id": "search", "name": "Search"}],
        "skills": [],
        "knowledge_bases": [],
    }

    async def load_inventory():
        return inventory

    service = RoleAssetService(db, catalog_loader=load_inventory)
    payload = {
        "definition": {
            "name": "Reviewer",
            "persona_slot": "review-persona",
            "tools": [],
        },
        "installation": {"bindings": {"model": "m", "review-persona": "local-persona"}},
    }
    preview = await service.preview_role(payload)
    assert preview["ready"] is True
    assert preview["effective"]["prompt"] == "Find errors."
    assert preview["sources"]["prompt"] == "persona:review-persona"
    assert preview["effective"]["tools"] == []
    assert preview["export_content"] == {
        "prompt": None,
        "persona_slot": "review-persona",
    }
    assert (await service.list_roles("alice"))["roles"] == []
    inventory["models"][0]["enabled"] = False
    assert (await service.preview_role(payload))["issues"][0]["code"] == "FORBIDDEN"
    inventory["models"][0]["enabled"] = True
    payload["definition"]["model_capabilities"] = ["image"]
    assert (await service.preview_role(payload))["issues"][0]["code"] == "INCOMPATIBLE"
    inventory["models"].append(dict(inventory["models"][0]))
    assert (await service.preview_role(payload))["issues"][0]["code"] == "AMBIGUOUS"
    await db.engine.dispose()


@pytest.mark.asyncio
async def test_rejects_conflicting_logical_slot_kinds_with_a_field_error(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "roles.db"))
    service = RoleAssetService(db)
    with pytest.raises(RoleAssetError) as caught:
        await service.save_role(
            "alice",
            {
                "definition": {
                    "name": "Researcher",
                    "prompt": "Check evidence.",
                    "model_slot": "shared",
                    "tools": ["shared"],
                }
            },
        )
    assert caught.value.fields[0]["path"] == "definition.tools.0"
    assert (await service.list_roles("alice"))["roles"] == []
    await db.engine.dispose()


@pytest.mark.asyncio
async def test_effective_role_shows_inheritance_overrides_and_missing_resources(
    tmp_path,
):
    db = SQLiteDatabase(str(tmp_path / "roles.db"))
    # This inventory represents external models/tools available on this machine.
    inventory = {
        "profiles": [
            {
                "id": "default",
                "name": "Default",
                "max_steps": 30,
                "tools": ["search"],
                "skills": [],
                "knowledge_bases": [],
            }
        ],
        "models": [
            {"id": "local-model", "name": "Model", "capabilities": ["tool_use"]}
        ],
        "personas": [],
        "tools": [{"id": "search", "name": "Search"}],
        "skills": [],
        "knowledge_bases": [],
    }

    async def load_inventory():
        return inventory

    service = RoleAssetService(db, catalog_loader=load_inventory)
    role = await service.save_role(
        "alice",
        {
            "definition": {
                "name": "Researcher",
                "prompt": "Check evidence.",
                "model_capabilities": ["tool_use"],
                "max_steps": 12,
            },
            "installation": {"bindings": {"model": "local-model"}},
        },
    )
    resolved = await service.resolve_role("alice", role["role_id"], 1, {"max_steps": 8})
    assert resolved["ready"] is True
    assert resolved["effective"]["max_steps"] == 8
    assert resolved["sources"]["max_steps"] == "task"
    assert resolved["effective"]["tools"] == ["search"]
    assert resolved["sources"]["tools"] == "profile:default"
    assert resolved["effective"]["provider_id"] == "local-model"
    inventory["models"] = []
    missing = await service.resolve_role("alice", role["role_id"], 1)
    assert missing["ready"] is False
    assert {
        "path": "installation.bindings.model",
        "code": "MISSING",
        "message": "Resource is missing: local-model",
    } in missing["issues"]
    await db.engine.dispose()


@pytest.mark.asyncio
async def test_edits_keep_the_old_version_and_list_the_latest(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "roles.db"))
    service = RoleAssetService(db)
    first = await service.save_role(
        "alice", {"definition": {"name": "Researcher", "prompt": "v1"}}
    )
    second = await service.save_role(
        "alice",
        {
            "definition": {"name": "Researcher", "prompt": "v2"},
            "expected_version": 1,
        },
        first["role_id"],
    )
    assert second["version"] == 2
    assert (await service.get_role("alice", first["role_id"], 1))["definition"][
        "prompt"
    ] == "v1"
    assert (await service.list_roles("alice"))["roles"] == [second]
    assert (await service.list_roles("bob"))["roles"] == []
    await db.engine.dispose()


@pytest.mark.asyncio
async def test_profile_disabled_tools_are_excluded_from_inheritance_and_reported_when_bound(
    tmp_path,
):
    db = SQLiteDatabase(str(tmp_path / "roles.db"))

    async def load_inventory():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "m"}],
            "personas": [],
            "tools": [{"id": "shell", "enabled_profiles": []}],
            "skills": [],
            "knowledge_bases": [],
        }

    service = RoleAssetService(db, catalog_loader=load_inventory)
    payload = {
        "definition": {"name": "Researcher", "prompt": "Read evidence."},
        "installation": {"bindings": {"model": "m", "local-shell": "shell"}},
    }
    assert (await service.preview_role(payload))["effective"]["tools"] == []
    payload["definition"]["tools"] = ["local-shell"]
    assert (await service.preview_role(payload))["issues"] == [
        {
            "path": "installation.bindings.local-shell",
            "code": "FORBIDDEN",
            "message": "Resource is unavailable: shell",
        }
    ]
    await db.engine.dispose()


@pytest.mark.asyncio
async def test_preview_inherits_the_live_profile_context_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path))
    db = SQLiteDatabase(str(tmp_path / "roles.db"))
    preferences = SharedPreferences(db, str(tmp_path / "preferences.json"))
    config = AstrBotConfig(config_path=str(tmp_path / "config.json"))
    config["agent_runner"]["config"]["compression"]["fallback_max_tokens"] = 54321
    profiles = AstrBotConfigManager(config, UmopConfigRouter(preferences), preferences)
    personas = PersonaManager(db, profiles)
    providers = ProviderManager(profiles, db, personas)
    # Compose real resource managers without replacing any internal methods.
    core = SimpleNamespace(
        astrbot_config_mgr=profiles,
        provider_manager=providers,
        persona_mgr=personas,
        kb_manager=KnowledgeBaseManager(providers),
    )
    service = RoleAssetService(db, core)
    try:
        resources = await service.get_resources()
        assert resources["profiles"][0]["context_length"] == 54321
        preview = await service.preview_role(
            {"definition": {"name": "Researcher", "prompt": "Check evidence."}}
        )
        assert preview["effective"]["context_length"] == 54321
        assert preview["sources"]["context_length"] == "profile:default"
        del config["agent_runner"]["config"]["compression"]["fallback_max_tokens"]
        assert (await service.get_resources())["profiles"][0][
            "context_length"
        ] == 128000
        overridden = await service.preview_role(
            {"definition": {"name": "Researcher", "prompt": "Check evidence."}},
            {"context_length": 8192},
        )
        assert overridden["effective"]["context_length"] == 8192
        assert overridden["sources"]["context_length"] == "task"
    finally:
        await preferences.close()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_capability_readiness_uses_the_selected_runtime_and_plugin_scope(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path))
    db = SQLiteDatabase(str(tmp_path / "roles.db"))
    preferences = SharedPreferences(db, str(tmp_path / "preferences.json"))
    config = AstrBotConfig(config_path=str(tmp_path / "config.json"))
    config["provider_settings"]["computer_use_runtime"] = "local"
    config["plugin_set"] = []
    profiles = AstrBotConfigManager(config, UmopConfigRouter(preferences), preferences)
    sandbox = AstrBotConfig(config_path=str(tmp_path / "sandbox.json"))
    sandbox["provider_settings"]["computer_use_runtime"] = "sandbox"
    sandbox["plugin_set"] = ["research-plugin"]
    profiles.confs["sandbox"] = sandbox
    personas = PersonaManager(db, profiles)
    providers = ProviderManager(profiles, db, personas)
    skills = SkillManager()
    plugin_skill = (
        tmp_path / "data" / "plugins" / "research_plugin" / "skills" / "evidence"
    )
    plugin_skill.mkdir(parents=True)
    (plugin_skill / "SKILL.md").write_text("# Evidence\n", encoding="utf-8")
    plugin = star.StarMetadata(
        name="research-plugin", root_dir_name="research_plugin", activated=True
    )
    monkeypatch.setattr(star, "star_registry", [plugin])
    monkeypatch.setitem(star.star_map, "research_plugin.tools", plugin)
    providers.llm_tools.func_list = [
        FunctionTool(
            name="plugin-search",
            description="Search plugin evidence.",
            parameters={"type": "object", "properties": {}},
            handler_module_path="research_plugin.tools",
        )
    ]
    skills.set_sandbox_skills_cache(
        [{"name": "sandbox-evidence", "description": "Sandbox evidence"}]
    )
    service = RoleAssetService(
        db,
        SimpleNamespace(
            astrbot_config_mgr=profiles,
            provider_manager=providers,
            persona_mgr=personas,
            kb_manager=KnowledgeBaseManager(providers),
        ),
    )
    payload = {
        "definition": {"name": "Researcher", "prompt": "Check evidence."},
        "installation": {"profile_id": "sandbox"},
    }
    try:
        assert (await service.preview_role(payload))["effective"]["skills"] == [
            "evidence",
            "sandbox-evidence",
        ]
        assert (
            "plugin-search"
            in (await service.preview_role(payload))["effective"]["tools"]
        )
        payload["installation"]["profile_id"] = "default"
        assert (await service.preview_role(payload))["effective"]["skills"] == []
        assert (
            "plugin-search"
            not in (await service.preview_role(payload))["effective"]["tools"]
        )
        payload["definition"]["tools"] = ["search"]
        payload["installation"]["bindings"] = {"search": "plugin-search"}
        assert {
            "path": "installation.bindings.search",
            "code": "FORBIDDEN",
            "message": "Resource is unavailable: plugin-search",
        } in (await service.preview_role(payload))["issues"]
        payload["definition"]["skills"] = ["remote"]
        payload["installation"]["bindings"]["remote"] = "sandbox-evidence"
        blocked = await service.preview_role(payload)
        assert {
            "path": "installation.bindings.remote",
            "code": "FORBIDDEN",
            "message": "Resource is unavailable: sandbox-evidence",
        } in blocked["issues"]
        payload["installation"]["profile_id"] = "sandbox"
        assert not any(
            issue["path"] == "installation.bindings.remote"
            for issue in (await service.preview_role(payload))["issues"]
        )
        payload["definition"]["skills"] = None
        plugin.activated = False
        assert (await service.preview_role(payload))["effective"]["skills"] == [
            "sandbox-evidence"
        ]
    finally:
        await preferences.close()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_explicit_capabilities_cannot_expand_persona_allowlists(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "roles.db"))

    async def load_inventory():
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "m"}],
            "personas": [{"id": "restricted", "tools": ["a"], "skills": ["a"]}],
            "tools": [
                {"id": "a"},
                {"id": "b"},
                {"id": "builtin", "persona_restricted": False},
            ],
            "skills": [{"id": "a"}, {"id": "b"}],
            "knowledge_bases": [],
        }

    service = RoleAssetService(db, catalog_loader=load_inventory)
    try:
        preview = await service.preview_role(
            {
                "definition": {
                    "name": "Researcher",
                    "prompt": "Check evidence.",
                    "persona_slot": "persona",
                    "tools": ["tool", "builtin-tool"],
                    "skills": ["skill"],
                },
                "installation": {
                    "bindings": {
                        "model": "m",
                        "persona": "restricted",
                        "tool": "b",
                        "builtin-tool": "builtin",
                        "skill": "b",
                    }
                },
            }
        )
        assert preview["ready"] is False
        assert preview["effective"]["tools"] == ["builtin"]
        assert preview["effective"]["skills"] == []
        assert preview["issues"] == [
            {
                "path": "installation.bindings.tool",
                "code": "FORBIDDEN",
                "message": "Resource is excluded by persona: b",
            },
            {
                "path": "installation.bindings.skill",
                "code": "FORBIDDEN",
                "message": "Resource is excluded by persona: b",
            },
        ]
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_capability_inheritance_uses_the_profiles_default_persona(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path))
    db = SQLiteDatabase(str(tmp_path / "roles.db"))
    await db.initialize()
    preferences = SharedPreferences(db, str(tmp_path / "preferences.json"))
    config = AstrBotConfig(config_path=str(tmp_path / "config.json"))
    config["agent_runner"]["config"]["persona"]["persona_id"] = "restricted"
    profiles = AstrBotConfigManager(config, UmopConfigRouter(preferences), preferences)
    personas = PersonaManager(db, profiles)
    await personas.create_persona(
        "restricted", "Be careful.", tools=["a"], skills=["a"]
    )
    providers = ProviderManager(profiles, db, personas)
    providers.llm_tools.func_list = [
        FunctionTool(
            name=name,
            description="Research evidence.",
            parameters={"type": "object", "properties": {}},
        )
        for name in ("a", "b")
    ]
    for name in ("a", "b"):
        skill = tmp_path / "data" / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    service = RoleAssetService(
        db,
        SimpleNamespace(
            astrbot_config_mgr=profiles,
            provider_manager=providers,
            persona_mgr=personas,
            kb_manager=KnowledgeBaseManager(providers),
        ),
    )
    payload = {"definition": {"name": "Researcher", "prompt": "Check evidence."}}
    try:
        preview = await service.preview_role(payload)
        assert preview["effective"]["tools"] == ["a"]
        assert preview["effective"]["skills"] == ["a"]
        assert preview["effective"]["persona_id"] == "restricted"
        assert preview["sources"]["skills"] == "profile:default"
        assert preview["effective"]["prompt"] == "Check evidence."
        await personas.delete_persona("restricted")
        assert {
            "path": "installation.profile_id",
            "code": "MISSING",
            "message": "Resource is missing: restricted",
        } in (await service.preview_role(payload))["issues"]
        # Webchat's implicit system default has no stored persona allowlist.
        config["agent_runner"]["config"]["persona"]["persona_id"] = "default"
        assert (await service.preview_role(payload))["effective"]["skills"] == [
            "a",
            "b",
        ]
    finally:
        await preferences.close()
        await db.engine.dispose()
