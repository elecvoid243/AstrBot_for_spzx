"""ChatUI session message marker index tests.

Author: elecvoid243
Date: 2026-09-07
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot.core.db.po import PlatformMessageHistory
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.platform_message_history_mgr import PlatformMessageHistoryManager
from astrbot.dashboard.services.chat_service import ChatService, ChatServiceError

SRC_SESSION_ID = "src-session"


def _record(
    record_id: int,
    record_type: str = "user",
    text: str | None = None,
) -> PlatformMessageHistory:
    """Build a minimal history record with an explicit id."""
    return PlatformMessageHistory(
        id=record_id,
        platform_id="webchat",
        user_id=SRC_SESSION_ID,
        content={
            "type": record_type,
            "message": [{"type": "plain", "text": text or f"m{record_id}"}],
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
    """ChatService with mocked db + history manager for marker tests."""
    db = Mock()
    db.get_platform_session_by_id = AsyncMock(return_value=_session())
    db.count_platform_message_history = AsyncMock(return_value=0)
    core_lifecycle = SimpleNamespace(
        conversation_manager=Mock(),
        platform_message_history_manager=Mock(),
        umop_config_router=Mock(),
    )
    service = ChatService(Mock(), core_lifecycle)
    service.db = db
    service.platform_history_mgr.get = AsyncMock(return_value=[])
    return service


# ---------------------------------------------------------------
# Real DB: cursor iteration + absolute indices
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_markers_iterate_all_history_in_absolute_order(tmp_path):
    """遍历全部历史，用户消息的绝对下标与 1:1 枚举一致，并跳过 bot 记录。"""
    db = SQLiteDatabase(str(tmp_path / "markers.db"))
    await db.initialize()
    inserted = []
    for record_type in ["bot", "user", "user", "bot", "user"]:
        inserted.append(
            await db.insert_platform_message_history(
                platform_id="webchat",
                user_id=SRC_SESSION_ID,
                content={
                    "type": record_type,
                    "message": [{"type": "plain", "text": "m"}],
                },
                sender_id="alice",
                sender_name="alice",
            )
        )
    db.get_platform_session_by_id = AsyncMock(return_value=_session())
    lifecycle = SimpleNamespace(
        conversation_manager=Mock(),
        platform_message_history_manager=Mock(),
        umop_config_router=Mock(),
    )
    service = ChatService(db, lifecycle)
    service.db = db
    service.platform_history_mgr = PlatformMessageHistoryManager(db)

    result = await service.get_message_markers("alice", SRC_SESSION_ID)

    assert result["total_messages"] == 5
    assert [(m["id"], m["index"]) for m in result["markers"]] == [
        (inserted[1].id, 1),
        (inserted[2].id, 2),
        (inserted[4].id, 4),
    ]
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_markers_flag_inherited_before_branch_divider():
    """branch_info 分隔记录之前的用户消息标记为 inherited，之后的不是。"""
    service = _make_service()
    service.db.count_platform_message_history = AsyncMock(return_value=6)
    # Ascending history: two inherited records, the divider, then the
    # session's own messages.
    service.platform_history_mgr.get = AsyncMock(
        return_value=[
            _record(1, "user"),
            _record(2, "bot"),
            _record(3, "user"),
            _record(4, "branch_info"),
            _record(5, "user"),
            _record(6, "bot"),
        ]
    )

    result = await service.get_message_markers("alice", SRC_SESSION_ID)

    assert [(m["id"], m["index"], m["inherited"]) for m in result["markers"]] == [
        (1, 0, True),
        (3, 2, True),
        (5, 4, False),
    ]


# ---------------------------------------------------------------
# Service: cursor pass-through, truncation, permission
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_markers_walk_pages_with_before_id_cursor():
    """第一页取最新，随后用 oldest id 作游标翻到更早页。"""
    service = _make_service()
    service.db.count_platform_message_history = AsyncMock(return_value=1000)
    # First page is full (500 rows): ids 501..1000, only the two newest are
    # user messages; the older page therefore must be fetched with a cursor.
    page1 = [_record(i, "user" if i > 998 else "bot") for i in range(501, 1001)]
    page2 = [_record(i, "bot") for i in range(1, 501)]
    service.platform_history_mgr.get = AsyncMock(side_effect=[page1, page2, []])

    result = await service.get_message_markers("alice", SRC_SESSION_ID)

    calls = service.platform_history_mgr.get.await_args_list
    assert calls[0].kwargs["before_id"] is None
    assert calls[1].kwargs["before_id"] == 501
    assert calls[2].kwargs["before_id"] == 1
    assert [(m["id"], m["index"]) for m in result["markers"]] == [
        (999, 998),
        (1000, 999),
    ]
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_markers_truncate_at_limit():
    """超过 limit 后停止扫描并标记 truncated。"""
    service = _make_service()
    service.db.count_platform_message_history = AsyncMock(return_value=5)
    service.platform_history_mgr.get = AsyncMock(
        return_value=[
            _record(3, "user"),
            _record(4, "user"),
            _record(5, "user"),
        ]
    )

    result = await service.get_message_markers("alice", SRC_SESSION_ID, limit=1)

    assert result["markers"][0]["id"] == 5
    assert result["truncated"] is True
    assert service.platform_history_mgr.get.call_count == 1


@pytest.mark.asyncio
async def test_markers_truncate_snippet_at_40_chars():
    """snippet 最长 40 字符。"""
    service = _make_service()
    service.db.count_platform_message_history = AsyncMock(return_value=2)
    service.platform_history_mgr.get = AsyncMock(
        return_value=[_record(2, "user", "x" * 100)]
    )

    result = await service.get_message_markers("alice", SRC_SESSION_ID)

    assert len(result["markers"][0]["snippet"]) == 40
    assert result["markers"][0]["snippet"] == "x" * 40


@pytest.mark.asyncio
async def test_markers_denies_non_creator():
    """非会话创建者不能读取标记索引。"""
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(
        return_value=_session(creator="bob")
    )

    with pytest.raises(ChatServiceError):
        await service.get_message_markers("alice", SRC_SESSION_ID)


@pytest.mark.asyncio
async def test_markers_rejects_missing_session():
    """会话不存在时抛出 ChatServiceError。"""
    service = _make_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=None)

    with pytest.raises(ChatServiceError):
        await service.get_message_markers("alice", SRC_SESSION_ID)
