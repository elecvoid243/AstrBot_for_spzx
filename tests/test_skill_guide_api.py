"""Skill-guide dashboard API contract tests.

The endpoints live on the skills v1 router (paths
``/skill-guide/*`` (mounted under the v1 prefix)); auth is bypassed with dependency overrides
and the service layer is stubbed with a fake star context.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from astrbot.core.skills import skill_guide


def _skill(name: str) -> MagicMock:
    skill = MagicMock()
    skill.name = name
    skill.description = f"{name} description"
    skill.path = f"/data/skills/{name}/SKILL.md"
    skill.source_type = "local"
    return skill


class _FakeSkillManager:
    def __init__(self, skills: list[MagicMock]) -> None:
        self._skills = skills

    def list_skills(self, *, active_only: bool = True, runtime: str = "local"):
        return list(self._skills)


@pytest.fixture(autouse=True)
def _clean_state():
    # Clear the singleton in place: the dashboard module holds its own
    # reference, so replacing the attribute would desync test and endpoint.
    skill_guide.guide_state._pending.clear()
    yield
    skill_guide.guide_state._pending.clear()


@pytest.fixture
def client(monkeypatch):
    from astrbot.dashboard.api.skills import (
        require_skill_scope,
    )
    from astrbot.dashboard.api.skills import (
        router as skills_router,
    )

    context = MagicMock()
    context.get_config.return_value = {"provider_settings": {}}

    async def _cid(umo):
        return None

    async def _persona(**kwargs):
        return (
            None,
            {"persona_id": "p1", "name": "Default", "skills": None},
            None,
            False,
        )

    context.conversation_manager.get_curr_conversation_id = _cid
    context.persona_manager.resolve_selected_persona = _persona

    monkeypatch.setattr(
        "astrbot.core.skills.skill_manager.SkillManager",
        lambda: _FakeSkillManager([_skill("alpha"), _skill("beta")]),
    )
    monkeypatch.setattr(
        "astrbot.core.star.star.star_registry",
        [],
        raising=False,
    )

    app = FastAPI()
    app.include_router(skills_router)
    app.state.services = SimpleNamespace(
        skills=SimpleNamespace(
            core_lifecycle=SimpleNamespace(star_context=context),
        ),
    )
    app.dependency_overrides[require_skill_scope] = lambda: SimpleNamespace(
        username="alice",
        via="api_key",
    )
    with TestClient(app) as test_client:
        yield test_client


def test_active_returns_persona_and_skills(client) -> None:
    resp = client.get(
        "/skill-guide/active",
        params={"umo": "webchat:FriendMessage:webchat!alice!s1"},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["persona"]["name"] == "Default"
    assert [s["name"] for s in data["skills"]] == ["alpha", "beta"]
    assert data["skills"][0]["path"].endswith("SKILL.md")


def test_load_queues_a_known_skill(client) -> None:
    umo = "webchat:FriendMessage:webchat!alice!s1"

    resp = client.post(
        "/skill-guide/load",
        json={"umo": umo, "skill_name": "alpha"},
    )

    assert resp.status_code == 200
    assert resp.json()["data"] == {"skill_name": "alpha", "queued": True}
    assert skill_guide.guide_state.drain(umo) == ["alpha"]


def test_load_rejects_unknown_skill(client) -> None:
    resp = client.post(
        "/skill-guide/load",
        json={"umo": "umo", "skill_name": "ghost"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert "skill not found" in body["message"]


def test_load_rejects_missing_fields(client) -> None:
    resp = client.post("/skill-guide/load", json={})

    body = resp.json()
    assert body["status"] == "error"


def test_clear_drops_the_queue(client) -> None:
    umo = "webchat:FriendMessage:webchat!alice!s1"
    skill_guide.guide_state.queue(umo, "alpha")

    resp = client.post("/skill-guide/clear", json={"umo": umo})

    assert resp.status_code == 200
    assert resp.json()["data"] == {"cleared": ["alpha"]}
    assert skill_guide.guide_state.drain(umo) == []
