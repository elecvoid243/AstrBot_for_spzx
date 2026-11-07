from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from astrbot.core.tools import fs_access
from astrbot.dashboard.api import chat as chat_api


class _StubConfigMgr:
    """Config-manager stub mirroring AstrBotConfigManager.get_conf fallback."""

    def __init__(self) -> None:
        self.base = {"provider_settings": {"file_access_default_mode": "full"}}
        self.profiles: dict[str, dict] = {}

    def get_conf(self, umo):
        return self.profiles.get(umo, self.base)


class _FakeSp:
    """In-memory SharedPreferences stand-in for fs_access persistence."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], object] = {}

    async def session_get(self, umo, key, default=None):
        return self.store.get((umo, key), default)

    async def session_put(self, umo, key, value):
        self.store[(umo, key)] = value

    async def session_remove(self, umo, key):
        self.store.pop((umo, key), None)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    app = FastAPI()
    config_mgr = _StubConfigMgr()
    fake_sp = _FakeSp()
    app.state.core_lifecycle = SimpleNamespace(
        astrbot_config=config_mgr.base,
        astrbot_config_mgr=config_mgr,
    )
    app.state._fake_sp = fake_sp
    monkeypatch.setattr(fs_access, "sp", fake_sp)
    app.include_router(chat_api.router)
    app.dependency_overrides[chat_api.require_chat_scope] = lambda: SimpleNamespace(
        username="tester"
    )
    fs_access.reset()
    yield TestClient(app)
    fs_access.reset()


def test_get_returns_effective_mode(client):
    res = client.get("/chat/file-access-mode", params={"umo": "u1"})
    assert res.status_code == 200
    assert res.json()["data"]["mode"] == "full"
    assert res.json()["data"]["default_mode"] == "full"


def test_get_resolves_default_from_umo_config_profile(client):
    config_mgr = client.app.state.core_lifecycle.astrbot_config_mgr
    config_mgr.profiles["u1"] = {
        "provider_settings": {"file_access_default_mode": "workspace"}
    }
    res = client.get("/chat/file-access-mode", params={"umo": "u1"})
    assert res.status_code == 200
    assert res.json()["data"]["mode"] == "workspace"
    assert res.json()["data"]["default_mode"] == "workspace"


def test_post_sets_and_reads_back_with_config_profile(client):
    config_mgr = client.app.state.core_lifecycle.astrbot_config_mgr
    config_mgr.profiles["u1"] = {
        "provider_settings": {"file_access_default_mode": "workspace"}
    }
    res = client.post("/chat/file-access-mode", json={"umo": "u1", "mode": "full"})
    assert res.status_code == 200
    assert res.json()["data"]["mode"] == "full"
    assert res.json()["data"]["default_mode"] == "workspace"

    res = client.get("/chat/file-access-mode", params={"umo": "u1"})
    assert res.json()["data"]["mode"] == "full"
    assert res.json()["data"]["default_mode"] == "workspace"


def test_post_sets_and_reads_back(client):
    res = client.post("/chat/file-access-mode", json={"umo": "u1", "mode": "readonly"})
    assert res.status_code == 200
    assert res.json()["data"]["mode"] == "readonly"

    res = client.get("/chat/file-access-mode", params={"umo": "u1"})
    assert res.json()["data"]["mode"] == "readonly"


def test_post_tightening_terminates_owner_shell_sessions(client, monkeypatch):
    terminate = AsyncMock()
    monkeypatch.setattr(chat_api, "shutdown_local_execution_for_owner", terminate)
    fs_access.set_mode_for_umo("u1", fs_access.FileAccessMode.WORKSPACE)

    res = client.post("/chat/file-access-mode", json={"umo": "u1", "mode": "readonly"})

    assert res.status_code == 200
    terminate.assert_awaited_once_with("u1")


def test_post_loosening_keeps_owner_shell_sessions(client, monkeypatch):
    terminate = AsyncMock()
    monkeypatch.setattr(chat_api, "shutdown_local_execution_for_owner", terminate)
    fs_access.set_mode_for_umo("u1", fs_access.FileAccessMode.READONLY)

    res = client.post("/chat/file-access-mode", json={"umo": "u1", "mode": "workspace"})

    assert res.status_code == 200
    terminate.assert_not_awaited()


def test_post_persists_mode_across_memory_reset(client):
    res = client.post("/chat/file-access-mode", json={"umo": "u1", "mode": "readonly"})
    assert res.status_code == 200

    fs_access.reset()
    res = client.get("/chat/file-access-mode", params={"umo": "u1"})

    assert res.status_code == 200
    assert res.json()["data"]["mode"] == "readonly"


def test_post_rejects_invalid_mode(client):
    res = client.post("/chat/file-access-mode", json={"umo": "u1", "mode": "bogus"})
    assert res.status_code == 422


def test_get_returns_fixed_and_custom_roots(client):
    res = client.get("/chat/file-access-mode", params={"umo": "u1"})
    data = res.json()["data"]
    assert [r["kind"] for r in data["fixed_roots"]] == ["workspace", "temp", "temp"]
    assert all(r["path"] for r in data["fixed_roots"])
    assert data["custom_roots"] == []


def test_prepare_workspace_creates_the_owned_session_root(
    client, tmp_path, monkeypatch
):
    from astrbot.core import workspace

    root = tmp_path / "workspaces" / "owned-session"
    monkeypatch.setattr(
        chat_api, "get_astrbot_workspaces_path", lambda: str(root.parent)
    )
    resolver = AsyncMock(return_value=root)
    monkeypatch.setattr(workspace, "resolve_workspace_root_for_umo", resolver)
    service = SimpleNamespace(
        db=object(), shell_resource_origin=AsyncMock(return_value="owned-umo")
    )
    client.app.dependency_overrides[chat_api.get_service] = lambda: service

    response = client.post("/chat/sessions/owned-session/workspace")

    assert response.json()["data"]["path"] == str(root)
    assert root.is_dir()
    service.shell_resource_origin.assert_awaited_once_with("tester", "owned-session")
    resolver.assert_awaited_once_with("owned-umo", service.db)


def test_prepare_workspace_resolves_the_session_specific_default(
    client, tmp_path, monkeypatch
):
    from astrbot.core import workspace

    base = tmp_path / "workspaces"
    monkeypatch.setattr(chat_api, "get_astrbot_workspaces_path", lambda: str(base))
    monkeypatch.setattr(workspace, "get_astrbot_workspaces_path", lambda: str(base))
    service = SimpleNamespace(
        db=SimpleNamespace(
            get_project_by_session=AsyncMock(return_value=None),
            get_webchat_thread_by_id=AsyncMock(return_value=None),
        ),
        shell_resource_origin=AsyncMock(
            return_value="webchat:FriendMessage:webchat!tester!owned-session"
        ),
    )
    client.app.dependency_overrides[chat_api.get_service] = lambda: service

    response = client.post("/chat/sessions/owned-session/workspace")

    root = base / "webchat_FriendMessage_webchat_tester_owned-session"
    assert response.json()["data"]["path"] == str(root.resolve())
    assert root.is_dir()


def test_prepare_workspace_rejects_unowned_session(client, tmp_path, monkeypatch):
    from astrbot.core import workspace
    from astrbot.dashboard.services.chat_service import ChatServiceError

    resolver = AsyncMock(return_value=tmp_path / "unowned")
    monkeypatch.setattr(workspace, "resolve_workspace_root_for_umo", resolver)
    service = SimpleNamespace(
        db=object(),
        shell_resource_origin=AsyncMock(side_effect=ChatServiceError("denied")),
    )
    client.app.dependency_overrides[chat_api.get_service] = lambda: service

    response = client.post("/chat/sessions/unowned/workspace")

    assert response.json()["status"] == "error"
    resolver.assert_not_awaited()
    assert not (tmp_path / "unowned").exists()


def test_prepare_workspace_does_not_create_missing_external_directories(
    client, tmp_path, monkeypatch
):
    from astrbot.core import workspace

    root = tmp_path / "external" / "missing"
    monkeypatch.setattr(
        chat_api, "get_astrbot_workspaces_path", lambda: str(tmp_path / "workspaces")
    )
    monkeypatch.setattr(
        workspace, "resolve_workspace_root_for_umo", AsyncMock(return_value=root)
    )
    service = SimpleNamespace(
        db=object(), shell_resource_origin=AsyncMock(return_value="owned-umo")
    )
    client.app.dependency_overrides[chat_api.get_service] = lambda: service

    response = client.post("/chat/sessions/owned-session/workspace")

    assert response.json()["status"] == "error"
    assert not root.exists()


def test_post_roots_sets_normalizes_and_reads_back(client, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    res = client.post(
        "/chat/file-access-mode/roots",
        json={"umo": "u1", "roots": [str(repo), "  ", str(repo), str(other)]},
    )
    assert res.status_code == 200
    assert res.json()["data"]["custom_roots"] == [
        str(repo.resolve()),
        str(other.resolve()),
    ]

    res = client.get("/chat/file-access-mode", params={"umo": "u1"})
    assert res.json()["data"]["custom_roots"] == [
        str(repo.resolve()),
        str(other.resolve()),
    ]


def test_custom_roots_survive_in_memory_reset(client, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    client.post(
        "/chat/file-access-mode/roots", json={"umo": "u1", "roots": [str(repo)]}
    )
    # Simulate an in-memory state loss (restart): the cache is cleared but
    # SharedPreferences still holds the persisted list.
    fs_access.reset()
    res = client.get("/chat/file-access-mode", params={"umo": "u1"})
    assert res.json()["data"]["custom_roots"] == [str(repo.resolve())]
