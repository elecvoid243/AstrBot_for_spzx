"""Tests for ChatUI session archive (ChatService / ChatUIProjectService).

Author: elecvoid243
Date: 2026-08-13
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot.core.db.po import PlatformSession
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.chat_service import ChatService, ChatServiceError
from astrbot.dashboard.services.chatui_project_service import ChatUIProjectService

SRC_SESSION_ID = "src-session"


def _session(
    session_id: str = SRC_SESSION_ID,
    creator: str = "alice",
    display_name: str = "会话",
    archived: int = 0,
):
    return SimpleNamespace(
        session_id=session_id,
        platform_id="webchat",
        creator=creator,
        is_group=0,
        display_name=display_name,
        archived=archived,
        created_at=datetime(2026, 8, 13, tzinfo=UTC),
        updated_at=datetime(2026, 8, 13, tzinfo=UTC),
    )


def _make_chat_service() -> ChatService:
    """Build a ChatService with a mocked db for archive tests."""
    db = Mock()
    db.get_platform_session_by_id = AsyncMock(return_value=_session())
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
async def test_get_sessions_excludes_archived():
    """普通会话列表必须排除已归档会话。"""
    service = _make_chat_service()
    service.db.get_platform_sessions_by_creator_paginated = AsyncMock(
        return_value=([], 0)
    )
    service.get_branch_relations = AsyncMock(return_value={})

    await service.get_sessions("alice", "webchat")

    kwargs = service.db.get_platform_sessions_by_creator_paginated.await_args.kwargs
    assert kwargs["archived"] is False
    assert kwargs["exclude_project_sessions"] is True


@pytest.mark.asyncio
async def test_get_archived_sessions_keeps_project_members():
    """归档列表包含项目内会话，并保留项目归属字段。"""
    service = _make_chat_service()
    item = {
        "session": _session(),
        "project_id": "proj-1",
        "project_title": "项目A",
        "project_emoji": "📁",
    }
    service.db.get_platform_sessions_by_creator_paginated = AsyncMock(
        return_value=([item], 1)
    )
    service.get_branch_relations = AsyncMock(return_value={})

    result = await service.get_archived_sessions("alice", "webchat")

    kwargs = service.db.get_platform_sessions_by_creator_paginated.await_args.kwargs
    assert kwargs["archived"] is True
    assert result["items"][0]["project_id"] == "proj-1"
    assert result["items"][0]["project_title"] == "项目A"
    assert result["pagination"] == {
        "page": 1,
        "page_size": 20,
        "total": 1,
        "total_pages": 1,
    }


@pytest.mark.asyncio
async def test_get_archived_sessions_paginates_at_db_layer():
    """归档列表把 page/page_size/archived 下推到 DB，不再内存切片。"""
    service = _make_chat_service()
    page_items = [
        {
            "session": _session(session_id="arch-2"),
            "project_id": None,
            "project_title": None,
            "project_emoji": None,
        }
    ]
    service.db.get_platform_sessions_by_creator_paginated = AsyncMock(
        return_value=(page_items, 5)
    )
    service.get_branch_relations = AsyncMock(return_value={})

    result = await service.get_archived_sessions(
        "alice", "webchat", page=2, page_size=2
    )

    kwargs = service.db.get_platform_sessions_by_creator_paginated.await_args.kwargs
    assert kwargs["page"] == 2
    assert kwargs["page_size"] == 2
    assert kwargs["archived"] is True
    assert kwargs["exclude_project_sessions"] is False
    # 服务层不再自行切片：返回 DB 已分页的项。
    assert [item["session_id"] for item in result["items"]] == ["arch-2"]
    assert result["pagination"] == {
        "page": 2,
        "page_size": 2,
        "total": 5,
        "total_pages": 3,
    }


@pytest.mark.asyncio
async def test_get_archived_sessions_forwards_search_to_db():
    """归档搜索下推到 DB 层（search 透传给分页查询）。"""
    service = _make_chat_service()
    filtered = [
        {
            "session": _session(session_id="s1", display_name="分支 · 需求分析"),
            "project_id": None,
            "project_title": None,
            "project_emoji": None,
        }
    ]
    service.db.get_platform_sessions_by_creator_paginated = AsyncMock(
        return_value=(filtered, 1)
    )
    service.get_branch_relations = AsyncMock(return_value={})

    result = await service.get_archived_sessions("alice", "webchat", search="需求")

    kwargs = service.db.get_platform_sessions_by_creator_paginated.await_args.kwargs
    assert kwargs["search"] == "需求"
    assert kwargs["page"] == 1
    assert [item["session_id"] for item in result["items"]] == ["s1"]
    assert result["pagination"]["total"] == 1


@pytest.mark.asyncio
async def test_set_session_archived_updates_db():
    service = _make_chat_service()

    await service.set_session_archived("alice", SRC_SESSION_ID, True)

    service.db.update_platform_session.assert_awaited_once_with(
        SRC_SESSION_ID, archived=1
    )


@pytest.mark.asyncio
async def test_set_session_archived_restores():
    service = _make_chat_service()

    await service.set_session_archived("alice", SRC_SESSION_ID, False)

    service.db.update_platform_session.assert_awaited_once_with(
        SRC_SESSION_ID, archived=0
    )


@pytest.mark.asyncio
async def test_set_session_archived_rejects_foreign_session():
    service = _make_chat_service()
    service.db.get_platform_session_by_id = AsyncMock(
        return_value=_session(creator="bob")
    )

    with pytest.raises(ChatServiceError, match="Permission denied"):
        await service.set_session_archived("alice", SRC_SESSION_ID, True)


@pytest.mark.asyncio
async def test_set_session_archived_rejects_missing_session():
    service = _make_chat_service()
    service.db.get_platform_session_by_id = AsyncMock(return_value=None)

    with pytest.raises(ChatServiceError, match="not found"):
        await service.set_session_archived("alice", SRC_SESSION_ID, True)


@pytest.mark.asyncio
async def test_project_sessions_exclude_archived():
    """项目会话列表排除归档会话。"""
    service = ChatUIProjectService(Mock())
    service.db.get_project_sessions = AsyncMock(return_value=[])
    service.db.get_branch_relations = AsyncMock(return_value={})
    service._get_owned_project = AsyncMock()

    await service.get_project_sessions("alice", "proj-1")

    service.db.get_project_sessions.assert_awaited_once_with(
        "proj-1", exclude_archived=True
    )


@pytest.mark.asyncio
async def test_message_search_excludes_archived_sessions():
    """侧边栏消息搜索只覆盖未归档会话。"""
    from astrbot.dashboard.services.conversation_service import ConversationService

    core_lifecycle = SimpleNamespace(
        conversation_manager=Mock(), platform_message_history_manager=Mock()
    )
    service = ConversationService(Mock(), core_lifecycle)
    service.db_helper.get_platform_sessions_by_creator_paginated = AsyncMock(
        return_value=([], 0)
    )

    await service.search_messages(q="keyword", page=1, page_size=20, username="alice")

    kwargs = (
        service.db_helper.get_platform_sessions_by_creator_paginated.await_args.kwargs
    )
    assert kwargs["archived"] is False


@pytest.mark.asyncio
async def test_batch_archive_sessions_archives_all():
    """批量归档：全部成功。"""
    service = _make_chat_service()
    service.db.get_platform_session_by_id = AsyncMock(
        side_effect=lambda session_id: _session(session_id=session_id)
    )

    result = await service.batch_archive_sessions_from_dashboard_payload(
        "alice", {"session_ids": ["s-1", "s-2"]}
    )

    assert result["archived_count"] == 2
    assert result["failed_count"] == 0
    assert result["failed_items"] == []
    assert service.db.update_platform_session.await_count == 2


@pytest.mark.asyncio
async def test_batch_archive_sessions_reports_failures():
    """批量归档：缺失会话计入失败，不中断整批。"""
    service = _make_chat_service()
    service.db.get_platform_session_by_id = AsyncMock(
        side_effect=[_session(session_id="ok"), None]
    )

    result = await service.batch_archive_sessions_from_dashboard_payload(
        "alice", {"session_ids": ["ok", "missing"]}
    )

    assert result["archived_count"] == 1
    assert result["failed_count"] == 1
    assert result["failed_items"][0]["session_id"] == "missing"


@pytest.mark.asyncio
async def test_batch_archive_sessions_rejects_bad_payload():
    """批量归档：缺少 session_ids 时抛错。"""
    service = _make_chat_service()

    with pytest.raises(ChatServiceError, match="session_ids"):
        await service.batch_archive_sessions_from_dashboard_payload("alice", {})


@pytest.mark.asyncio
async def test_batch_unarchive_sessions_restores_all():
    """批量取消归档：全部成功。"""
    service = _make_chat_service()
    service.db.get_platform_session_by_id = AsyncMock(
        side_effect=lambda session_id: _session(session_id=session_id)
    )

    result = await service.batch_unarchive_sessions_from_dashboard_payload(
        "alice", {"session_ids": ["s-1", "s-2"]}
    )

    assert result["unarchived_count"] == 2
    assert result["failed_count"] == 0
    assert service.db.update_platform_session.await_count == 2


@pytest.mark.asyncio
async def test_batch_unarchive_sessions_reports_failures():
    """批量取消归档：缺失会话计入失败。"""
    service = _make_chat_service()
    service.db.get_platform_session_by_id = AsyncMock(
        side_effect=[_session(session_id="ok"), None]
    )

    result = await service.batch_unarchive_sessions_from_dashboard_payload(
        "alice", {"session_ids": ["ok", "missing"]}
    )

    assert result["unarchived_count"] == 1
    assert result["failed_count"] == 1
    assert result["failed_items"][0]["session_id"] == "missing"


@pytest.mark.asyncio
async def test_get_session_exposes_archived_flag():
    """get_session 返回 archived 标志，供前端只读渲染。"""
    service = _make_chat_service()
    service.db.get_project_by_session = AsyncMock(return_value=None)
    service.db.get_webchat_threads_by_parent_session = AsyncMock(return_value=[])
    service.db.count_platform_message_history = AsyncMock(return_value=0)
    service.platform_history_mgr.get = AsyncMock(return_value=[])
    service.get_active_chat_runs = AsyncMock(return_value=[])

    service.db.get_platform_session_by_id = AsyncMock(return_value=_session(archived=1))
    result = await service.get_session("alice", SRC_SESSION_ID)
    assert result["archived"] is True

    service.db.get_platform_session_by_id = AsyncMock(return_value=_session(archived=0))
    result = await service.get_session("alice", SRC_SESSION_ID)
    assert result["archived"] is False


async def _seed_archived_sessions(db: SQLiteDatabase) -> None:
    """Insert 4 archived sessions with distinct display names."""
    # updated_at ascending by index, so the newest session is s3.
    names = ["Project Draft", "需求分析", "Project Notes", "项目会话"]
    async with db.get_db() as session:
        async with session.begin():
            session.add_all(
                [
                    PlatformSession(
                        session_id=f"s{i}",
                        platform_id="webchat",
                        creator="alice",
                        display_name=name,
                        is_group=0,
                        archived=1,
                        created_at=datetime(2026, 8, 1, tzinfo=UTC),
                        updated_at=datetime(2026, 8, 1 + i, tzinfo=UTC),
                    )
                    for i, name in enumerate(names)
                ]
            )


def _session_ids(items: list[dict]) -> set[str]:
    return {item["session"].session_id for item in items}


@pytest.mark.asyncio
async def test_db_paginated_search_filters_by_display_name(tmp_path):
    """DB 层按 display_name（大小写不敏感子串）过滤并统计总数。"""
    db = SQLiteDatabase(str(tmp_path / "archive.db"))
    await db.initialize()
    await _seed_archived_sessions(db)

    page1, total = await db.get_platform_sessions_by_creator_paginated(
        creator="alice",
        platform_id="webchat",
        page=1,
        page_size=10,
        archived=True,
        search="需求",
    )

    assert total == 1
    assert _session_ids(page1) == {"s1"}


@pytest.mark.asyncio
async def test_db_paginated_search_pages_and_counts(tmp_path):
    """DB 层同时应用搜索 + LIMIT/OFFSET 分页，total 反映过滤后的总数。"""
    db = SQLiteDatabase(str(tmp_path / "archive.db"))
    await db.initialize()
    await _seed_archived_sessions(db)

    # search 匹配 s0/s2（大小写不敏感），每页 1 条，验证分页切分与总数。
    page1, total = await db.get_platform_sessions_by_creator_paginated(
        creator="alice",
        platform_id="webchat",
        page=1,
        page_size=1,
        archived=True,
        search="project",
    )
    page2, _ = await db.get_platform_sessions_by_creator_paginated(
        creator="alice",
        platform_id="webchat",
        page=2,
        page_size=1,
        archived=True,
        search="project",
    )

    assert total == 2
    assert len(page1) == 1
    assert len(page2) == 1
    assert _session_ids(page1) | _session_ids(page2) == {"s0", "s2"}
    assert _session_ids(page1) & _session_ids(page2) == set()


@pytest.mark.asyncio
async def test_db_paginated_search_case_insensitive(tmp_path):
    """DB 层搜索大小写不敏感（ASCII）。"""
    db = SQLiteDatabase(str(tmp_path / "archive.db"))
    await db.initialize()
    await _seed_archived_sessions(db)

    _, total_lower = await db.get_platform_sessions_by_creator_paginated(
        creator="alice",
        platform_id="webchat",
        page=1,
        page_size=10,
        archived=True,
        search="draft",
    )
    _, total_upper = await db.get_platform_sessions_by_creator_paginated(
        creator="alice",
        platform_id="webchat",
        page=1,
        page_size=10,
        archived=True,
        search="DRAFT",
    )

    assert total_lower == 1
    assert total_upper == 1
