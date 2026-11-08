from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core import sp
from astrbot.core.db.po import PlatformSession
from astrbot.dashboard.services.session_management_service import (
    SessionManagementService,
)


@pytest.mark.asyncio
async def test_get_groups_preserves_legacy_preference_scope(monkeypatch):
    get_async = AsyncMock(return_value=None)
    monkeypatch.setattr(sp, "get_async", get_async)
    service = SessionManagementService(MagicMock(), MagicMock())

    assert await service.get_groups() == {}
    get_async.assert_awaited_once_with(
        "unknown",
        "unknown",
        "session_groups",
        {},
    )


@pytest.mark.asyncio
async def test_save_groups_preserves_legacy_preference_scope(monkeypatch):
    put_async = AsyncMock()
    monkeypatch.setattr(sp, "put_async", put_async)
    service = SessionManagementService(MagicMock(), MagicMock())
    groups = {"group-id": {"name": "Group", "umos": []}}

    await service.save_groups(groups)

    put_async.assert_awaited_once_with(
        "unknown",
        "unknown",
        "session_groups",
        groups,
    )


@pytest.mark.asyncio
async def test_list_active_umos_exposes_webchat_session_titles():
    db = MagicMock()
    db.get_platform_sessions_by_ids = AsyncMock(
        return_value=[
            PlatformSession(
                session_id="chat-1",
                creator="alice",
                display_name="Weekly report",
            )
        ]
    )
    service = SessionManagementService(MagicMock(), db)
    service.list_known_umos = AsyncMock(
        return_value=[
            "webchat:FriendMessage:webchat!alice!chat-1",
            "telegram:FriendMessage:42",
        ]
    )
    service.get_umo_alias_map = AsyncMock(return_value={})

    data = await service.list_active_umos()

    assert data["umo_infos"][0]["webchat_title"] == "Weekly report"
    assert "webchat_title" not in data["umo_infos"][1]
    db.get_platform_sessions_by_ids.assert_awaited_once_with(["chat-1"])


@pytest.mark.asyncio
async def test_list_active_umos_keeps_raw_umo_when_title_lookup_fails():
    db = MagicMock()
    db.get_platform_sessions_by_ids = AsyncMock(side_effect=RuntimeError("db down"))
    service = SessionManagementService(MagicMock(), db)
    service.list_known_umos = AsyncMock(
        return_value=["webchat:FriendMessage:webchat!alice!chat-1"]
    )
    service.get_umo_alias_map = AsyncMock(return_value={})

    data = await service.list_active_umos()

    assert "webchat_title" not in data["umo_infos"][0]
