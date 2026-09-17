"""Tests for ChatUI session starring (ChatService / SQLiteDatabase).

Author: elecvoid243
Date: 2026-09-18
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.chat_service import ChatService, ChatServiceError

SRC_SESSION_ID = "src-session"


def _session(creator: str = "alice", starred: int = 0):
    return SimpleNamespace(
        session_id=SRC_SESSION_ID,
        platform_id="webchat",
        creator=creator,
        is_group=0,
        display_name="会话",
        archived=0,
        starred=starred,
        created_at=datetime(2026, 9, 18, tzinfo=UTC),
        updated_at=datetime(2026, 9, 18, tzinfo=UTC),
    )


def _make_chat_service() -> ChatService:
    """Build a ChatService with a mocked db for starring tests."""
    db = Mock()
    db.get_platform_session_by_id = AsyncMock(return_value=_session())
    db.set_platform_session_starred = AsyncMock()
    db.update_platform_session = AsyncMock()
    core_lifecycle = SimpleNamespace(
        conversation_manager=Mock(),
        platform_message_history_manager=Mock(),
        umop_config_router=Mock(),
    )
    service = ChatService(Mock(), core_lifecycle)
    service.db = db
    return service


@pytest.mark.asyncio
async def test_set_session_starred_flags_session():
    service = _make_chat_service()

    await service.set_session_starred("alice", SRC_SESSION_ID, True)

    service.db.set_platform_session_starred.assert_awaited_once_with(SRC_SESSION_ID, 1)
    # 星标不得走会 bump updated_at 的通用更新接口,否则侧栏会重排序。
    service.db.update_platform_session.assert_not_called()


@pytest.mark.asyncio
async def test_set_session_starred_unstars():
    service = _make_chat_service()

    await service.set_session_starred("alice", SRC_SESSION_ID, False)

    service.db.set_platform_session_starred.assert_awaited_once_with(SRC_SESSION_ID, 0)


@pytest.mark.asyncio
async def test_set_session_starred_rejects_foreign_session():
    service = _make_chat_service()
    service.db.get_platform_session_by_id = AsyncMock(
        return_value=_session(creator="bob")
    )

    with pytest.raises(ChatServiceError, match="Permission denied"):
        await service.set_session_starred("alice", SRC_SESSION_ID, True)


@pytest.mark.asyncio
async def test_set_session_starred_missing_session():
    service = _make_chat_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=None)

    with pytest.raises(ChatServiceError, match="not found"):
        await service.set_session_starred("alice", SRC_SESSION_ID, True)


@pytest.mark.asyncio
async def test_set_platform_session_starred_keeps_updated_at():
    """星标只写标记位:updated_at 必须保持原值(侧栏按它倒序排)。"""
    db = SQLiteDatabase(":memory:")
    await db.initialize()
    session = await db.create_platform_session(creator="alice")
    before = session.updated_at

    await db.set_platform_session_starred(session.session_id, 1)

    stored = await db.get_platform_session_by_id(session.session_id)
    assert stored.starred == 1
    assert stored.updated_at == before


@pytest.mark.asyncio
async def test_legacy_database_gains_starred_column():
    """老库(建表时无 starred 列)启动后应被自动补列。"""
    import sqlite3
    import uuid
    from pathlib import Path

    from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

    db_path = Path(get_astrbot_temp_path()) / f"legacy_star_{uuid.uuid4().hex}.db"
    # 手工建一个缺少 starred 列的 platform_sessions,模拟升级前的老库。
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE platform_sessions ("
        "inner_id INTEGER PRIMARY KEY, session_id TEXT, "
        "archived INTEGER NOT NULL DEFAULT 0)"
    )
    connection.commit()
    connection.close()

    db = SQLiteDatabase(str(db_path))
    try:
        await db.initialize()

        assert "starred" in await _table_columns(db, "platform_sessions")
    finally:
        await db.engine.dispose()
        db_path.unlink(missing_ok=True)


async def _table_columns(db: SQLiteDatabase, table: str) -> set[str]:
    from sqlalchemy import text

    async with db.engine.begin() as conn:
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        return {row[1] for row in result.fetchall()}
