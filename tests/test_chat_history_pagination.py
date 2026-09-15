"""ChatUI history pagination: DB cursor + get_session windowing.

Author: elecvoid243
Date: 2026-09-07
Updated: 2026-09-15 — window is opt-in (legacy callers keep the full page),
cursor paging follows insertion order, and route defaults are pinned.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from inspect import signature
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot.core.db.po import PlatformMessageHistory, WebChatThread
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.api.chat import get_chat_session, get_chat_session_history
from astrbot.dashboard.services.chat_service import (
    HISTORY_WINDOW_SIZE,
    ChatService,
    ChatServiceError,
)

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

    older = await db.get_platform_message_history(
        "webchat",
        SRC_SESSION_ID,
        page=1,
        page_size=3,
        before_id=page[-1].id,
    )
    assert [r.content["message"][0]["text"] for r in older] == ["m1", "m0"]


@pytest.mark.asyncio
async def test_db_cursor_paging_follows_id_order_not_timestamps(tmp_path):
    """游标页按 id 排序：created_at 与插入顺序相反时也不跳行/重复。

    回填/导入的会话（created_at 被写成与 id 不一致的值）下，若 ORDER BY 用
    created_at 而游标用 id，翻页会重复返回已看过的行、永远走不到更旧的一页。
    """
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

    # 反转 created_at 相对 id 的顺序（在库层面改写，模拟显式时间戳的回填）。
    conn = sqlite3.connect(str(tmp_path / "history.db"))
    rows = conn.execute(
        "SELECT id, created_at FROM platform_message_history ORDER BY id"
    ).fetchall()
    stamps = [row[1] for row in rows]
    for (record_id, _), stamp in zip(rows, reversed(stamps)):
        conn.execute(
            "UPDATE platform_message_history SET created_at = ? WHERE id = ?",
            (stamp, record_id),
        )
    conn.commit()
    conn.close()

    # 用游标一直翻到最旧一页：每一行必须恰好出现一次。
    seen: list[str] = []
    cursor = None
    for _ in range(10):  # 5 行 / 每页 2 条 = 3 页；余量给足同时防止死循环
        page = await db.get_platform_message_history(
            "webchat", SRC_SESSION_ID, page=1, page_size=2, before_id=cursor
        )
        if not page:
            break
        seen.extend(r.content["message"][0]["text"] for r in page)
        assert len(seen) == len(set(seen)), f"cursor paging repeated rows: {seen}"
        cursor = page[-1].id

    assert seen == ["m4", "m3", "m2", "m1", "m0"]


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
async def test_get_session_returns_recent_window_when_limit_given():
    """ChatUI 显式请求窗口时只取最近的 N 条，并返回总数与 has_more。"""
    service = _make_service()
    service.db.count_platform_message_history = AsyncMock(return_value=120)
    service.platform_history_mgr.get = AsyncMock(
        return_value=[_record(i) for i in range(71, 121)]
    )

    result = await service.get_session("alice", SRC_SESSION_ID, limit=50)

    get_kwargs = service.platform_history_mgr.get.await_args.kwargs
    assert get_kwargs["page_size"] == 50
    assert len(result["history"]) == 50
    assert result["history"][0]["id"] == 71
    assert result["history"][-1]["id"] == 120
    assert result["total_messages"] == 120
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_get_session_without_limit_keeps_legacy_full_page():
    """不传 limit 时保持历史行为（整页 1000），避免静默截断。

    v1 dashboard query 路由与归档会话预览都走这条无参路径，它们不会翻页，
    因此默认值必须是旧的全量页而不是 ChatUI 的 50 条窗口。
    """
    service = _make_service()
    service.db.count_platform_message_history = AsyncMock(return_value=120)
    service.platform_history_mgr.get = AsyncMock(
        return_value=[_record(i) for i in range(71, 121)]
    )

    result = await service.get_session("alice", SRC_SESSION_ID)

    get_kwargs = service.platform_history_mgr.get.await_args.kwargs
    assert get_kwargs["page_size"] == 1000
    assert result["total_messages"] == 120
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_get_session_clamps_window_limit():
    """limit 被夹到 [1, 1000]，服务层不会收到离谱的窗口。"""
    service = _make_service()
    service.db.count_platform_message_history = AsyncMock(return_value=0)

    await service.get_session("alice", SRC_SESSION_ID, limit=9999)
    assert service.platform_history_mgr.get.await_args.kwargs["page_size"] == 1000

    await service.get_session("alice", SRC_SESSION_ID, limit=0)
    assert service.platform_history_mgr.get.await_args.kwargs["page_size"] == 1


def test_session_routes_pin_default_window():
    """路由层默认值：会话路由 opt-in（None），游标路由固定 50。"""
    assert signature(get_chat_session).parameters["limit"].default.default is None
    assert (
        signature(get_chat_session_history).parameters["limit"].default
        == HISTORY_WINDOW_SIZE
    )


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
