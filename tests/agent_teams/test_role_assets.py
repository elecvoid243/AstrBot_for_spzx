"""Role assets through the shared public service and real SQLite storage."""

from types import SimpleNamespace

import pytest

from astrbot.api import AstrBotConfig
from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager
from astrbot.core.persona_mgr import PersonaManager
from astrbot.core.provider.manager import ProviderManager
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
