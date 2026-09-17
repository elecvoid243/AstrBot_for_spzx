"""Route tests for the dashboard-editable update sources.

Exercises ``GET/PUT /updates/sources`` end to end through a bare FastAPI app so
the envelope shape, the auth dependency, and the pass-through of validation
messages are all covered without booting the real dashboard.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from astrbot.core.config.update_config import DEFAULT_CONFIG, UpdateConfig
from astrbot.dashboard.api import updates as updates_api
from astrbot.dashboard.services import update_service as update_service_module
from astrbot.dashboard.services.update_service import UpdateService

DASHBOARD_REGISTRY_CUSTOM = "https://mirror.example.com/astrbot/{version}/dist.zip"


def _make_service() -> UpdateService:
    """Build a bare UpdateService; these routes never touch its dependencies."""
    return UpdateService(
        astrbot_updater=MagicMock(),
        core_lifecycle=MagicMock(),
        get_dashboard_version_func=AsyncMock(return_value=None),
        pip_install_func=AsyncMock(),
        demo_mode=False,
        clear_site_data_headers={},
    )


@pytest.fixture()
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the service at a throwaway config file instead of the data dir."""
    path = tmp_path / "update_config.json"
    monkeypatch.setattr(
        update_service_module,
        "UpdateConfig",
        lambda: UpdateConfig(config_path=str(path)),
    )
    return path


@pytest.fixture()
def client(config_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(updates_api.router)
    app.dependency_overrides[updates_api.require_system_scope] = lambda: (
        SimpleNamespace(username="tester")
    )
    app.dependency_overrides[updates_api.get_service] = _make_service
    return TestClient(app)


def test_get_returns_sources_with_defaults(client: TestClient) -> None:
    """A fresh install reports the built-in defaults, unlocked by env vars."""
    res = client.get("/updates/sources")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    sources = body["data"]["sources"]
    assert (
        sources["core_release_api_url"]["value"]
        == DEFAULT_CONFIG["core_update"]["release_api_url"]
    )
    assert sources["core_release_api_url"]["env_locked"] is False
    assert set(sources) == {
        "core_release_api_url",
        "core_package_base_url",
        "dashboard_registry_url_template",
    }


def test_put_persists_and_echoes_effective_values(
    client: TestClient, config_path: Path
) -> None:
    """Saving writes the file and returns the re-read values for the UI to sync."""
    res = client.put(
        "/updates/sources",
        json={"dashboard_registry_url_template": DASHBOARD_REGISTRY_CUSTOM},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["message"] == "更新源已保存。"
    assert (
        body["data"]["sources"]["dashboard_registry_url_template"]["value"]
        == DASHBOARD_REGISTRY_CUSTOM
    )
    assert DASHBOARD_REGISTRY_CUSTOM in config_path.read_text(encoding="utf-8")


def test_put_applies_only_the_submitted_fields(client: TestClient) -> None:
    """Omitting a field leaves it at its configured value (partial update)."""
    client.put(
        "/updates/sources", json={"core_release_api_url": "https://a.example.com/r"}
    )
    res = client.put(
        "/updates/sources",
        json={"core_package_base_url": "https://b.example.com/core"},
    )

    sources = res.json()["data"]["sources"]
    assert sources["core_release_api_url"]["value"] == "https://a.example.com/r"
    assert sources["core_package_base_url"]["value"] == "https://b.example.com/core"


def test_put_surfaces_validation_message(client: TestClient) -> None:
    """A bad template returns the actionable message rather than a generic error."""
    res = client.put(
        "/updates/sources",
        json={"dashboard_registry_url_template": "https://mirror.example.com/dist.zip"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "error"
    assert "{version}" in body["message"]


def test_put_rejects_empty_payload(client: TestClient) -> None:
    """An empty payload is a client mistake, not a silent success."""
    res = client.put("/updates/sources", json={})

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "error"
    assert "没有需要保存的更新源字段" in body["message"]


def test_put_rejects_empty_value(client: TestClient) -> None:
    """Clearing a field is rejected; the UI offers "restore default" instead."""
    res = client.put("/updates/sources", json={"core_release_api_url": ""})

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "error"
    assert "不能为空" in body["message"]


def test_put_ignores_unknown_extra_fields(client: TestClient) -> None:
    """Extra keys outside the allow-list cannot inject arbitrary config."""
    res = client.put(
        "/updates/sources",
        json={"proxy_url": "https://attacker.example.com"},
    )

    assert res.json()["status"] == "error"


def test_both_source_routes_are_registered() -> None:
    """GET and PUT must coexist on the same path on the v1 router."""
    methods_by_path: dict[str, set[str]] = {}
    for route in updates_api.router.routes:
        methods_by_path.setdefault(route.path, set()).update(route.methods)

    assert methods_by_path["/updates/sources"] == {"GET", "PUT"}
