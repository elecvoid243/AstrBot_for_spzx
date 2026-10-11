from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import astrbot.api  # noqa: F401  # import first to avoid a circular import
from astrbot.core import persona_mgr as pm
from astrbot.core.db.po import Persona


def make_acm(persona_id: str = "default") -> MagicMock:
    """Build a mocked config manager.

    Args:
        persona_id: Default persona selected by the local agent runner.

    Returns:
        A config manager exposing a default profile and profile lookup.
    """
    conf = {
        "agent_runner": {
            "runner_type": "local",
            "config": {"persona": {"persona_id": persona_id}},
        },
        "provider_settings": {},
    }
    acm = MagicMock()
    acm.default_conf = conf
    acm.confs = {}
    acm.get_conf.return_value = conf
    return acm


def make_persona(persona_id: str, prompt: str) -> Persona:
    """Build a stored persona fixture.

    Args:
        persona_id: Persona identity.
        prompt: System prompt to preserve.

    Returns:
        A persona with unrestricted tool and skill selections.
    """
    return Persona(
        persona_id=persona_id,
        system_prompt=prompt,
        begin_dialogs=[],
        tools=None,
        skills=None,
        custom_error_message=None,
        folder_id=None,
        sort_order=0,
    )


def make_manager(
    personas: list[Persona], persona_id: str = "default"
) -> pm.PersonaManager:
    """Build a manager with cached persona data.

    Args:
        personas: Stored persona fixtures.
        persona_id: Default persona selected by the config profile.

    Returns:
        A manager with both stored and v3 caches initialized.
    """
    manager = pm.PersonaManager(db_helper=MagicMock(), acm=make_acm(persona_id))
    manager.personas = personas
    manager.get_v3_persona_data()
    return manager


def make_seeding_db(personas: list[Persona]) -> MagicMock:
    """Build a database fixture for initialization.

    Args:
        personas: Existing records returned before default seeding.

    Returns:
        A mocked database supporting persona listing and insertion.
    """
    db = MagicMock()
    db.get_personas = AsyncMock(return_value=personas)
    db.insert_persona = AsyncMock(
        side_effect=lambda **kwargs: make_persona(
            kwargs["persona_id"], kwargs["system_prompt"]
        )
    )
    return db


@pytest.mark.asyncio
async def test_initialize_seeds_default_when_absent():
    db = make_seeding_db([make_persona("alice", "ALICE")])
    manager = pm.PersonaManager(db_helper=db, acm=make_acm())

    await manager.initialize()

    db.insert_persona.assert_awaited_once()
    assert {persona.persona_id for persona in manager.personas} == {
        "alice",
        "default",
    }
    seeded = manager.get_persona_v3_by_id("default")
    assert seeded is not None
    assert seeded["prompt"] == pm.DEFAULT_PERSONALITY["prompt"]


@pytest.mark.asyncio
async def test_initialize_adopts_existing_default():
    db = make_seeding_db([make_persona("default", "USER DEFAULT")])
    manager = pm.PersonaManager(db_helper=db, acm=make_acm())

    await manager.initialize()

    db.insert_persona.assert_not_awaited()
    adopted = manager.get_persona_v3_by_id("default")
    assert adopted is not None
    assert adopted["prompt"] == "USER DEFAULT"


@pytest.mark.asyncio
async def test_delete_default_persona_is_rejected():
    db = make_seeding_db([make_persona("default", "USER DEFAULT")])
    manager = pm.PersonaManager(db_helper=db, acm=make_acm())
    await manager.initialize()

    with pytest.raises(ValueError):
        await manager.delete_persona("default")


@pytest.mark.asyncio
async def test_create_default_persona_is_rejected():
    manager = pm.PersonaManager(db_helper=MagicMock(), acm=make_acm())

    with pytest.raises(ValueError):
        await manager.create_persona("default", "PROMPT")


def test_get_persona_v3_by_id_prefers_persona_named_default():
    manager = make_manager([make_persona("default", "USER DEFAULT")])

    resolved = manager.get_persona_v3_by_id("default")

    assert resolved is not None
    assert resolved["prompt"] == "USER DEFAULT"


def test_get_persona_v3_by_id_falls_back_to_builtin_default():
    manager = make_manager([make_persona("alice", "ALICE")])

    assert manager.get_persona_v3_by_id("default") == pm.DEFAULT_PERSONALITY


@pytest.mark.asyncio
async def test_get_default_persona_v3_uses_db_default():
    manager = make_manager([make_persona("default", "USER DEFAULT")])

    resolved = await manager.get_default_persona_v3(umo="test:umo")

    assert resolved["prompt"] == "USER DEFAULT"


@pytest.mark.asyncio
async def test_resolve_selected_persona_matches_db_default():
    manager = make_manager([make_persona("default", "USER DEFAULT")])

    with patch.object(pm.sp, "get_async", new=AsyncMock(return_value={})):
        persona_id, persona, _, _ = await manager.resolve_selected_persona(
            umo="test:umo",
            conversation_persona_id="default",
            platform_name="webchat",
            provider_settings={},
        )

    assert persona_id == "default"
    assert persona is not None
    assert persona["prompt"] == "USER DEFAULT"


@pytest.mark.asyncio
async def test_resolve_selected_persona_webchat_implicit_uses_chatui_default():
    manager = make_manager([make_persona("default", "USER DEFAULT")])

    with patch.object(pm.sp, "get_async", new=AsyncMock(return_value={})):
        (
            persona_id,
            persona,
            _,
            use_webchat_special_default,
        ) = await manager.resolve_selected_persona(
            umo="test:umo",
            conversation_persona_id=None,
            platform_name="webchat",
            provider_settings={},
        )

    assert persona_id == "_chatui_default_"
    assert persona is None
    assert use_webchat_special_default is True


@pytest.mark.asyncio
async def test_resolve_selected_persona_non_webchat_implicit_uses_db_default():
    manager = make_manager([make_persona("default", "USER DEFAULT")])

    with patch.object(pm.sp, "get_async", new=AsyncMock(return_value={})):
        (
            persona_id,
            persona,
            _,
            use_webchat_special_default,
        ) = await manager.resolve_selected_persona(
            umo="test:umo",
            conversation_persona_id=None,
            platform_name="telegram",
            provider_settings={},
        )

    assert persona_id == "default"
    assert persona is not None
    assert persona["prompt"] == "USER DEFAULT"
    assert use_webchat_special_default is False


@pytest.mark.asyncio
async def test_resolve_selected_persona_explicit_default_ignores_chatui_default():
    manager = make_manager([make_persona("default", "USER DEFAULT")])

    with patch.object(pm.sp, "get_async", new=AsyncMock(return_value={})):
        (
            persona_id,
            persona,
            _,
            use_webchat_special_default,
        ) = await manager.resolve_selected_persona(
            umo="test:umo",
            conversation_persona_id="default",
            platform_name="webchat",
            provider_settings={},
        )

    assert persona_id == "default"
    assert persona is not None
    assert persona["prompt"] == "USER DEFAULT"
    assert use_webchat_special_default is False


@pytest.mark.asyncio
async def test_resolve_selected_persona_session_rule_default_uses_db_default():
    manager = make_manager([make_persona("default", "USER DEFAULT")])

    with patch.object(
        pm.sp, "get_async", new=AsyncMock(return_value={"persona_id": "default"})
    ):
        (
            persona_id,
            persona,
            force_applied_persona_id,
            use_webchat_special_default,
        ) = await manager.resolve_selected_persona(
            umo="test:umo",
            conversation_persona_id=None,
            platform_name="webchat",
            provider_settings={},
        )

    assert persona_id == "default"
    assert persona is not None
    assert persona["prompt"] == "USER DEFAULT"
    assert force_applied_persona_id == "default"
    assert use_webchat_special_default is False


@pytest.mark.asyncio
@pytest.mark.parametrize("capabilities", [None, []], ids=["all", "none"])
async def test_editable_default_preserves_capabilities_after_restart(
    temp_db, capabilities
):
    """Editing the default prompt preserves local capability semantics on disk.

    Args:
        temp_db: Isolated SQLite database fixture.
        capabilities: All capabilities (None), or an explicitly empty selection.
    """
    manager = pm.PersonaManager(db_helper=temp_db, acm=make_acm())
    await manager.initialize()
    await manager.update_persona(
        "default",
        tools=capabilities,
        skills=capabilities,
        custom_error_message="Custom error",
    )
    await manager.update_persona("default", system_prompt="EDITED DEFAULT")
    restarted = pm.PersonaManager(db_helper=temp_db, acm=make_acm())
    await restarted.initialize()
    await restarted.initialize()
    rows = await temp_db.get_personas()
    assert [row.persona_id for row in rows] == ["default"]
    assert rows[0].system_prompt == "EDITED DEFAULT"
    assert rows[0].tools == capabilities
    assert rows[0].skills == capabilities
    assert rows[0].custom_error_message == "Custom error"
    with pytest.raises(ValueError, match="cannot be deleted"):
        await restarted.delete_persona("default")
    assert await temp_db.get_persona_by_id("default") is not None


@pytest.mark.asyncio
async def test_persona_resolution_keeps_local_config_profile_binding():
    """An explicit local config profile continues to select its own persona."""
    manager = make_manager(
        [make_persona("default", "DEFAULT"), make_persona("bound", "PROFILE")]
    )
    manager.acm.confs = {
        "selected": {
            "agent_runner": {
                "runner_type": "local",
                "config": {"persona": {"persona_id": "bound"}},
            }
        }
    }
    with patch.object(pm.sp, "get_async", new=AsyncMock(return_value={})):
        persona_id, persona, _, special = await manager.resolve_selected_persona(
            umo="webchat:FriendMessage:test",
            conversation_persona_id=None,
            platform_name="webchat",
            config_id="selected",
        )
    assert persona_id == "bound"
    assert persona["prompt"] == "PROFILE"
    assert special is False
