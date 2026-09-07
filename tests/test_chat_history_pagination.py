"""ChatUI history pagination: DB cursor + get_session windowing.

Author: elecvoid243
Date: 2026-09-07
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot.core.db.po import PlatformMessageHistory, WebChatThread
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.chat_service import ChatService, ChatServiceError

SRC_SESSION_ID = "src-session"


def _record(record_id: int, text: str | None = None) -> PlatformMessageHistory:
    """Build a minimal history record with an explicit id."""
    return PlatformMessageHistory(
        id=record_id,
        platform_id="webchat",
        user_id=SRC_SESSION_ID,
        content={
            "type": "user",
            "message": [{"type": "text", "text": text or f"m{record_id}"}],
        },
        sender_id="alice",
        sender_name="alice",
        created_at=datetime(2026, 9, 1, tzinfo=UTC) + timedelta(seconds=record_id),
        updated_at=datetime(2026, 9, 1, tzinfo=UTC) + timedelta(seconds=record_id),
    )


def _session(creator: str = "alice"):
    return SimpleNamespace(
        session_id=SRC_SESSION_ID,
        platform_id="webchat",
        creator=creator,
        is_group=0,
        display_name="会话",
        archived=0,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        updated_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def _make_service() -> ChatService:
    """ChatService with mocked db + history manager for pagination tests."""
    db = Mock()
    db.get_platform_session_by_id = AsyncMock(return_value=_session())
    db.get_project_by_session = AsyncMock(return_value=None)
    db.get_webchat_threads_by_parent_session = AsyncMock(return_value=[])
    db.count_platform_message_history = AsyncMock(return_value=0)
    core_lifecycle = SimpleNamespace(
        conversation_manager=Mock(),
        platform_message_history_manager=Mock(),
        umop_config_router=Mock(),
    )
    service = ChatService(Mock(), core_lifecycle)
    service.db = db
    service.platform_history_mgr.get = AsyncMock(return_value=[])
    service.get_active_chat_runs = Mock(return_value=[])
    return service


# ---------------------------------------------------------------
# DB layer: before_id cursor + count
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_cursor_before_id_returns_older_rows(tmp_path):
    """before_id 游标返回比游标更旧的一页，且不影响无游标行为。"""
    db = SQLiteDatabase(str(tmp_path / "history.db"))
    await db.initialize()
    for i in range(5):
        await db.insert_platform_message_history(
            platform_id="webchat",
            user_id=SRC_SESSION_ID,
            content={"type": "user", "message": [{"type": "text", "text": f"m{i}"}]},
            sender_id="alice",
            sender_name="alice",
        )

    page = await db.get_platform_message_history(
        "webchat", SRC_SESSION_ID, page=1, page_size=3
    )
    assert [r.content["message"][0]["text"] for r in page] == ["m4", "m3", "m2"]
    assert [r.content["message"][0]["text"] for r in page] == ["m4", "m3", "m2"]

    older = await db.get_platform_message_history(
        "webchat",
        SRC_SESSION_ID,
        page=1,
        page_size=3,
        before_id=page[-1].id,
    )
    assert [r.content["message"][0]["text"] for r in older] == ["m1", "m0"]


@pytest.mark.asyncio
async def test_db_count_platform_message_history_before_cursor(tmp_path):
    """count 总条数、以及比游标更旧条数。"""
    db = SQLiteDatabase(str(tmp_path / "history.db"))
    await db.initialize()
    for i in range(5):
        await db.insert_platform_message_history(
            platform_id="webchat",
            user_id=SRC_SESSION_ID,
            content={"type": "user", "message": [{"type": "text", "text": f"m{i}"}]},
            sender_id="alice",
            sender_name="alice",
        )

    total = await db.count_platform_message_history("webchat", SRC_SESSION_ID)
    assert total == 5

    # 最新的 3 条之外的旧条数：m4/m3/m2 之外还有 2 条
    page = await db.get_platform_message_history(
        "webchat", SRC_SESSION_ID, page=1, page_size=3
    )
    older_count = await db.count_platform_message_history(
        "webchat", SRC_SESSION_ID, before_id=page[-1].id
    )
    assert older_count == 2


# ---------------------------------------------------------------
# Service: get_session windowing
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session_returns_recent_window_only():
    """打开会话只取最近的窗口，并返回总数与 has_more。"""
    service = _make_service()
    service.db.count_platform_message_history = AsyncMock(return_value=120)
    service.platform_history_mgr.get = AsyncMock(
        return_value=[_record(i) for i in range(71, 121)]
    )

    result = await service.get_session("alice", SRC_SESSION_ID)

    get_kwargs = service.platform_history_mgr.get.await_args.kwargs
    assert get_kwargs["page_size"] == 50
    assert len(result["history"]) == 50
    assert result["history"][0]["id"] == 71
    assert result["history"][-1]["id"] == 120
    assert result["total_messages"] == 120
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_get_session_has_more_false_when_window_contains_all():
    """会话不足一页时不显示加载更多。"""
    service = _make_service()
    service.db.count_platform_message_history = AsyncMock(return_value=30)
    service.platform_history_mgr.get = AsyncMock(
        return_value=[_record(i) for i in range(1, 31)]
    )

    result = await service.get_session("alice", SRC_SESSION_ID)

    assert len(result["history"]) == 30
    assert result["total_messages"] == 30
    assert result["has_more"] is False


# ---------------------------------------------------------------
# Service: get_history_before
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_history_before_returns_older_page_with_cursor():
    """按 before_id 返回更旧一页，并给出 next_before_id 与 has_more。"""
    service = _make_service()
    service.platform_history_mgr.get = AsyncMock(
        return_value=[_record(i) for i in range(21, 71)]
    )
    service.db.count_platform_message_history = AsyncMock(return_value=20)

    result = await service.get_history_before("alice", SRC_SESSION_ID, 120, 50)

    get_kwargs = service.platform_history_mgr.get.await_args.kwargs
    assert get_kwargs["before_id"] == 120
    assert get_kwargs["page_size"] == 50
    assert result["history"][0]["id"] == 21
    assert result["history"][-1]["id"] == 70
    assert result["next_before_id"] == 21
    assert result["has_more"] is True
    count_kwargs = service.db.count_platform_message_history.await_args.kwargs
    assert count_kwargs["before_id"] == 21


@pytest.mark.asyncio
async def test_get_history_before_filters_threads_to_page():
    """向前翻页只返回该批消息对应的楼中楼。"""
    service = _make_service()
    service.platform_history_mgr.get = AsyncMock(
        return_value=[_record(i) for i in range(21, 71)]
    )
    in_page = WebChatThread(
        thread_id="t1",
        parent_session_id=SRC_SESSION_ID,
        parent_message_id=70,
        base_checkpoint_id="cp-1",
        selected_text="hello",
        creator="alice",
    )
    outside = WebChatThread(
        thread_id="t2",
        parent_session_id=SRC_SESSION_ID,
        parent_message_id=5,
        base_checkpoint_id="cp-2",
        selected_text="world",
        creator="alice",
    )
    service.db.get_webchat_threads_by_parent_session = AsyncMock(
        return_value=[in_page, outside]
    )

    result = await service.get_history_before("alice", SRC_SESSION_ID, 120, 50)

    assert [t["thread_id"] for t in result["threads"]] == ["t1"]


@pytest.mark.asyncio
async def test_get_history_before_denies_non_creator():
    """非会话创建者不能读取历史分页。"""
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(
        return_value=_session(creator="bob")
    )

    with pytest.raises(ChatServiceError):
        await service.get_history_before("alice", SRC_SESSION_ID, 120, 50)


@pytest.mark.asyncio
async def test_get_history_before_rejects_missing_session():
    """会话不存在时抛出 ChatServiceError。"""
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=None)

    with pytest.raises(ChatServiceError):
        await service.get_history_before("alice", SRC_SESSION_ID, 120, 50)


@pytest.mark.asyncio
async def test_get_history_before_rejects_missing_cursor():
    """分页接口必须携带 before_id 游标，否则返回最新页会与 bootstrap 重复。"""
    service = _make_service()

    with pytest.raises(ChatServiceError):
        await service.get_history_before("alice", SRC_SESSION_ID, None, 50)


@pytest.mark.asyncio
async def test_get_history_before_clamps_limit():
    """limit 超过上限时按 200 取页。"""
    service = _make_service()
    service.db.count_platform_message_history = AsyncMock(return_value=0)

    await service.get_history_before("alice", SRC_SESSION_ID, 120, 9999)

    get_kwargs = service.platform_history_mgr.get.await_args.kwargs
    assert get_kwargs["page_size"] == 200
