"""Account-private quick messages and shared announcement access over HTTP."""

import copy

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.update_service import (
    UpdateServiceError,
    UpdateServiceResult,
)
from tests.test_multi_user_chat import account_dashboard as account_dashboard
from tests.test_multi_user_chat import fake_core_lifecycle as fake_core_lifecycle
from tests.test_multi_user_chat import ordinary_chat as ordinary_chat


@pytest.mark.asyncio
async def test_quick_messages_are_private_persistent_and_clearable(
    ordinary_chat, monkeypatch
):
    client, server, core, db, admin, users = ordinary_chat
    original = copy.deepcopy(dict(core.astrbot_config))
    saved = []
    for index, account in enumerate([admin, *users]):
        headers = {"Authorization": f"Bearer {account['token']}"}
        settings = (
            await client.get("/api/v1/chat/ui-settings", headers=headers)
        ).json()["data"]
        assert settings["quick_messages"] == original["chatui"]["quick_messages"]
        items = [{"id": "same-id", "content": f"Private phrase {index}"}]
        response = await client.put(
            "/api/v1/chat/ui-settings/quick-messages",
            headers=headers,
            json={"items": items},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"] == {"items": items}
        saved.append((headers, items))
    reopened = SQLiteDatabase(db.db_path)
    await reopened.initialize()
    try:
        monkeypatch.setattr(server.asgi_app.state, "db", reopened)
        for headers, items in saved:
            response = await client.get("/api/v1/chat/ui-settings", headers=headers)
            assert response.json()["data"]["quick_messages"] == {"items": items}
            for body in [
                {"items": items, "account_id": admin["account_id"]},
                {"items": [{"id": "bad", "content": " "}]},
                {"items": [{"id": "bad", "content": "x" * 2001}]},
            ]:
                rejected = await client.put(
                    "/api/v1/chat/ui-settings/quick-messages",
                    headers=headers,
                    json=body,
                )
                assert rejected.status_code in {400, 422}, rejected.text
            response = await client.get("/api/v1/chat/ui-settings", headers=headers)
            assert response.json()["data"]["quick_messages"] == {"items": items}
        cleared = await client.put(
            "/api/v1/chat/ui-settings/quick-messages",
            headers=saved[1][0],
            json={"items": []},
        )
        assert cleared.json()["data"] == {"items": []}
        for index, (headers, items) in enumerate(saved):
            response = await client.get("/api/v1/chat/ui-settings", headers=headers)
            assert response.json()["data"]["quick_messages"] == {
                "items": [] if index == 1 else items
            }
        assert dict(core.astrbot_config) == original
    finally:
        await reopened.engine.dispose()


@pytest.mark.asyncio
async def test_members_read_announcements_without_system_management(
    ordinary_chat, monkeypatch
):
    client, server, core, db, admin, users = ordinary_chat
    announcement = {
        "title": "Service notice",
        "content": "Welcome",
        "enabled": True,
        "version": 1,
    }

    async def fetch():
        return UpdateServiceResult(data=announcement)

    monkeypatch.setattr(
        server.asgi_app.state.services.updates, "get_announcement", fetch
    )
    for account in [admin, *users]:
        headers = {"Authorization": f"Bearer {account['token']}"}
        for path in ["/api/system/announcement", "/api/v1/system/announcement"]:
            response = await client.get(path, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["data"] == announcement
        if account is not admin:
            for path in ["/api/v1/stats", "/api/v1/stats/version", "/api/update/check"]:
                response = await client.get(path, headers=headers)
                assert response.status_code == 403, response.text

    async def withdrawn():
        raise UpdateServiceError("没有公告")

    monkeypatch.setattr(
        server.asgi_app.state.services.updates, "get_announcement", withdrawn
    )
    headers = {"Authorization": f"Bearer {users[0]['token']}"}
    for path in ["/api/system/announcement", "/api/v1/system/announcement"]:
        assert (await client.get(path, headers=headers)).status_code == 404
        assert (await client.get(path)).status_code == 401
