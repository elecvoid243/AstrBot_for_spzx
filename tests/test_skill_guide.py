"""Skill-guide kernel module tests.

One-shot queue semantics, load validation, guidance building, and the
native injection into the LLM request assembly.

Migrated from the astrbot_plugin_skill_guide tests.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from astrbot.core.skills import skill_guide
from astrbot.core.skills.skill_guide import (
    GuideState,
    build_active_payload,
    build_guidance,
    inject_pending_guidance,
    validate_load_skill,
)


def _skill(name: str, description: str = "desc") -> MagicMock:
    skill = MagicMock()
    skill.name = name
    skill.description = description
    skill.path = f"/data/skills/{name}/SKILL.md"
    skill.source_type = "local"
    return skill


@pytest.fixture(autouse=True)
def _clean_state():
    # Clear the singleton in place: the dashboard module holds its own
    # reference, so replacing the attribute would desync other test modules
    # in full-suite runs.
    skill_guide.guide_state._pending.clear()
    yield
    skill_guide.guide_state._pending.clear()


@pytest.fixture
def fake_skill_manager(monkeypatch):
    """Patch the lazy SkillManager import with a canned skill list."""

    class FakeSkillManager:
        def __init__(self) -> None:
            self.skills = [_skill("alpha"), _skill("beta"), _skill("gamma")]

        def list_skills(self, *, active_only: bool = True, runtime: str = "local"):
            return list(self.skills)

    manager = FakeSkillManager()
    monkeypatch.setattr(
        "astrbot.core.skills.skill_manager.SkillManager",
        lambda: manager,
    )
    return manager


def test_queue_is_deduplicated() -> None:
    state = GuideState()
    assert state.queue("umo:a", "alpha") is True
    assert state.queue("umo:a", "alpha") is False
    assert state.drain("umo:a") == ["alpha"]
    assert state.drain("umo:a") == []


def test_drain_is_one_shot() -> None:
    state = GuideState()
    state.queue("umo:a", "alpha")
    state.queue("umo:a", "beta")

    assert state.drain("umo:a") == ["alpha", "beta"]
    assert state.drain("umo:a") == []
    # Other sessions are untouched.
    state.queue("umo:b", "gamma")
    assert state.drain("umo:a") == []
    assert state.drain("umo:b") == ["gamma"]


def test_clear_drops_without_consuming() -> None:
    state = GuideState()
    state.queue("umo:a", "alpha")

    assert state.clear("umo:a") == ["alpha"]
    assert state.drain("umo:a") == []
    assert state.clear("umo:a") == []


def test_validate_load_skill() -> None:
    skills = [_skill("alpha"), _skill("beta")]
    assert validate_load_skill(skills, "alpha") is None
    assert validate_load_skill(skills, "missing") == "skill not found: missing"
    assert validate_load_skill(skills, "  ") == "missing skill_name"


def test_build_guidance_mentions_name_description_and_path() -> None:
    text = build_guidance(_skill("alpha", "Do things"))

    assert "**alpha**: Do things" in text
    assert "/data/skills/alpha/SKILL.md" in text
    assert "[User skill request]" in text


def test_build_active_payload_serializes_skills_and_persona() -> None:
    persona = {"persona_id": "p1", "name": "Dev", "prompt": "..."}

    payload = build_active_payload([_skill("alpha")], persona)

    assert payload["persona"] == {"id": "p1", "name": "Dev"}
    assert payload["skills"][0]["name"] == "alpha"
    assert payload["skills"][0]["source_type"] == "local"

    assert build_active_payload([], None)["persona"] is None


def test_inject_appends_persisted_text_parts(fake_skill_manager) -> None:
    guide_state = skill_guide.guide_state
    guide_state.queue("umo:a", "alpha")
    guide_state.queue("umo:a", "missing-skill")
    req = MagicMock()
    req.extra_user_content_parts = []

    injected = inject_pending_guidance("umo:a", req, runtime="local")

    assert injected == ["alpha"]
    assert len(req.extra_user_content_parts) == 1
    part = req.extra_user_content_parts[0]
    # The part must be a persisted TextPart (prefix-cache stability), not a
    # temp part.
    from astrbot.core.agent.message import TextPart

    assert isinstance(part, TextPart)
    assert "alpha" in part.text
    # One-shot: a second injection drains nothing.
    assert inject_pending_guidance("umo:a", req, runtime="local") == []


def test_inject_drains_even_when_nothing_resolves(fake_skill_manager) -> None:
    guide_state = skill_guide.guide_state
    guide_state.queue("umo:a", "ghost-skill")
    req = MagicMock()
    req.extra_user_content_parts = []

    injected = inject_pending_guidance("umo:a", req, runtime="local")

    assert injected == []
    assert req.extra_user_content_parts == []
    assert guide_state.drain("umo:a") == []


def test_inject_without_pending_is_noop() -> None:
    req = MagicMock()
    req.extra_user_content_parts = []

    assert inject_pending_guidance("umo:a", req, runtime="local") == []
    assert req.extra_user_content_parts == []


def test_resolve_active_skills_applies_persona_whitelist(
    monkeypatch,
    fake_skill_manager,
) -> None:
    context = MagicMock()
    context.get_config.return_value = {"provider_settings": {}}

    async def _cid(umo):
        return None

    async def _persona(**kwargs):
        return (
            None,
            {"persona_id": "p1", "name": "Dev", "skills": ["alpha"]},
            None,
            False,
        )

    context.conversation_manager.get_curr_conversation_id = _cid
    context.persona_manager.resolve_selected_persona = _persona

    # The plugin-filter reads the real star registry; keep it empty.
    monkeypatch.setattr(
        "astrbot.core.star.star.star_registry",
        [],
        raising=False,
    )

    skills, persona = asyncio.run(
        skill_guide.resolve_active_skills(context, "umo:a"),
    )

    assert [s.name for s in skills] == ["alpha"]
    assert persona["name"] == "Dev"


def test_resolve_active_skills_persona_none_keeps_all(
    monkeypatch,
    fake_skill_manager,
) -> None:
    context = MagicMock()
    context.get_config.return_value = {"provider_settings": {}}

    async def _cid(umo):
        return None

    async def _persona(**kwargs):
        return (None, None, None, False)

    context.conversation_manager.get_curr_conversation_id = _cid
    context.persona_manager.resolve_selected_persona = _persona
    monkeypatch.setattr(
        "astrbot.core.star.star.star_registry",
        [],
        raising=False,
    )

    skills, persona = asyncio.run(
        skill_guide.resolve_active_skills(context, "umo:a"),
    )

    assert len(skills) == 3
    assert persona is None
