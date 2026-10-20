"""Tests for ChatService.branch_session (ChatUI session branching).

Author: elecvoid243
Date: 2026-07-24
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot.dashboard.services.chat_service import ChatService, ChatServiceError

SRC_SESSION_ID = "src-session"
NEW_SESSION_ID = "new-session"


def _history_record(
    record_id: int,
    type_: str,
    checkpoint: str | None = None,
    user_id: str = SRC_SESSION_ID,
):
    return SimpleNamespace(
        id=record_id,
        platform_id="webchat",
        user_id=user_id,
        content={
            "type": type_,
            "message": [{"type": "plain", "text": f"m{record_id}"}],
        },
        sender_id="alice" if type_ == "user" else "bot",
        sender_name="alice" if type_ == "user" else "bot",
        llm_checkpoint_id=checkpoint,
        created_at=datetime(2026, 7, 24, 0, 0, record_id, tzinfo=UTC),
    )


def _llm_history() -> list[dict]:
    # Two turns; each turn ends with an internal checkpoint message.
    return [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "_checkpoint", "content": {"id": "cp-1"}},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "_checkpoint", "content": {"id": "cp-2"}},
    ]


def _make_service(
    *,
    session_creator: str = "alice",
    target_record=None,
    llm_history: list[dict] | None = None,
    source_project=None,
) -> ChatService:
    """Build a ChatService with mocked persistence for branch tests."""
    session = SimpleNamespace(
        session_id=SRC_SESSION_ID,
        platform_id="webchat",
        creator=session_creator,
        is_group=0,
        display_name="源会话",
    )
    new_session = SimpleNamespace(
        session_id=NEW_SESSION_ID,
        platform_id="webchat",
        creator="alice",
        is_group=0,
        display_name="分支 · 源会话",
    )
    history_records = [
        _history_record(1, "user", "cp-1"),
        _history_record(2, "bot", "cp-1"),
        _history_record(3, "user", "cp-2"),
        _history_record(4, "bot", "cp-2"),
    ]
    if target_record is None:
        target_record = history_records[1]  # branch at turn-1 bot message

    db = Mock()
    db.get_platform_session_by_id = AsyncMock(return_value=session)
    db.get_platform_message_history_by_id = AsyncMock(return_value=target_record)
    db.create_platform_session = AsyncMock(return_value=new_session)
    db.get_project_by_session = AsyncMock(return_value=source_project)
    db.add_session_to_project = AsyncMock(
        return_value=SimpleNamespace(session_id=NEW_SESSION_ID, project_id="proj-1")
    )
    # 2026-08-13: branch-relation cache lives on the db layer now.
    db.get_branch_relations = AsyncMock(return_value={})
    db.update_branch_relation = Mock()

    conversation = SimpleNamespace(
        cid="conv-1",
        history="[]",  # unused; load_current_conversation_history is stubbed below
        title="源对话",
        persona_id="persona-1",
    )
    conv_mgr = Mock()
    conv_mgr.get_conversation = AsyncMock(return_value=conversation)
    conv_mgr.new_conversation = AsyncMock(return_value="new-conv-id")

    platform_history_mgr = Mock()
    platform_history_mgr.get = AsyncMock(return_value=history_records)
    platform_history_mgr.insert = AsyncMock(
        return_value=SimpleNamespace(id=99, created_at=datetime.now(UTC))
    )

    core_lifecycle = SimpleNamespace(
        conversation_manager=conv_mgr,
        platform_message_history_manager=platform_history_mgr,
        umop_config_router=Mock(),
    )
    service = ChatService(Mock(), core_lifecycle)
    service.db = db
    service.load_current_conversation_history = AsyncMock(
        return_value=(
            "conv-1",
            llm_history if llm_history is not None else _llm_history(),
        )
    )
    return service


@pytest.mark.asyncio
async def test_branch_session_copies_context_and_display_history():
    service = _make_service()

    result = await service.branch_session("alice", SRC_SESSION_ID, 2)

    assert result == {
        "session_id": NEW_SESSION_ID,
        "display_name": "分支 · 源会话",
        "inherited_count": 2,
    }

    # New platform session carries the branch-prefixed display name.
    create_kwargs = service.db.create_platform_session.await_args.kwargs
    assert create_kwargs["creator"] == "alice"
    assert create_kwargs["platform_id"] == "webchat"
    assert create_kwargs["display_name"] == "分支 · 源会话"

    # LLM history is truncated at the branch checkpoint (inclusive) and
    # persona/title are inherited.
    new_conv_kwargs = service.conv_mgr.new_conversation.await_args.kwargs
    assert new_conv_kwargs["content"] == _llm_history()[:3]
    assert new_conv_kwargs["persona_id"] == "persona-1"
    assert new_conv_kwargs["title"] == "源对话"

    # Display history: records 1-2 copied, then one branch_info divider.
    inserts = service.platform_history_mgr.insert.await_args_list
    assert len(inserts) == 3
    assert inserts[0].kwargs["user_id"] == NEW_SESSION_ID
    assert inserts[0].kwargs["content"]["type"] == "user"
    assert inserts[0].kwargs["llm_checkpoint_id"] == "cp-1"
    assert inserts[1].kwargs["content"]["type"] == "bot"
    divider = inserts[2].kwargs["content"]
    assert divider["type"] == "branch_info"
    assert divider["source_session_id"] == SRC_SESSION_ID
    assert divider["source_message_id"] == 2
    assert divider["inherited_count"] == 2


@pytest.mark.asyncio
async def test_branch_session_rejects_user_message():
    service = _make_service(target_record=_history_record(1, "user", "cp-1"))
    with pytest.raises(ChatServiceError, match="Only bot messages"):
        await service.branch_session("alice", SRC_SESSION_ID, 1)


@pytest.mark.asyncio
async def test_branch_session_rejects_message_without_checkpoint():
    service = _make_service(target_record=_history_record(2, "bot", None))
    with pytest.raises(ChatServiceError, match="not linked to LLM history"):
        await service.branch_session("alice", SRC_SESSION_ID, 2)


@pytest.mark.asyncio
async def test_branch_session_rejects_foreign_session():
    service = _make_service(session_creator="bob")
    with pytest.raises(ChatServiceError, match="Permission denied"):
        await service.branch_session("alice", SRC_SESSION_ID, 2)


@pytest.mark.asyncio
async def test_branch_session_rejects_unknown_checkpoint():
    service = _make_service(
        target_record=_history_record(2, "bot", "cp-missing"),
    )
    with pytest.raises(ChatServiceError, match="Linked checkpoint not found"):
        await service.branch_session("alice", SRC_SESSION_ID, 2)


@pytest.mark.asyncio
async def test_get_sessions_includes_branch_relations():
    service = _make_service()
    src_item = {
        "session": SimpleNamespace(
            session_id=SRC_SESSION_ID,
            platform_id="webchat",
            creator="alice",
            display_name="源会话",
            is_group=0,
            created_at=datetime(2026, 7, 24, tzinfo=UTC),
            updated_at=datetime(2026, 7, 24, tzinfo=UTC),
            starred=0,
        )
    }
    child_item = {
        "session": SimpleNamespace(
            session_id=NEW_SESSION_ID,
            platform_id="webchat",
            creator="alice",
            display_name="分支 · 源会话",
            is_group=0,
            created_at=datetime(2026, 7, 24, 1, tzinfo=UTC),
            updated_at=datetime(2026, 7, 24, 1, tzinfo=UTC),
            starred=0,
        )
    }
    service.db.get_platform_sessions_by_creator_paginated = AsyncMock(
        return_value=([src_item, child_item], 2)
    )
    service.db.get_branch_relations = AsyncMock(
        return_value={
            NEW_SESSION_ID: {
                "source_session_id": SRC_SESSION_ID,
                "source_message_id": 2,
            }
        }
    )

    result = await service.get_sessions("alice", "webchat")

    src = next(s for s in result if s["session_id"] == SRC_SESSION_ID)
    child = next(s for s in result if s["session_id"] == NEW_SESSION_ID)
    assert src["branch_source"] is None
    assert src["branches"] == [
        {"session_id": NEW_SESSION_ID, "display_name": "分支 · 源会话"}
    ]
    assert child["branch_source"] == {"session_id": SRC_SESSION_ID, "message_id": 2}
    assert child["branches"] == []


@pytest.mark.asyncio
async def test_get_sessions_filters_relations_to_listed_sessions():
    # The cached relation points to a source session that no longer exists
    # in the current list (e.g. deleted): the parent keeps no badge entry.
    service = _make_service()
    child_item = {
        "session": SimpleNamespace(
            session_id=NEW_SESSION_ID,
            platform_id="webchat",
            creator="alice",
            display_name="分支 · 源会话",
            is_group=0,
            created_at=datetime(2026, 7, 24, 1, tzinfo=UTC),
            updated_at=datetime(2026, 7, 24, 1, tzinfo=UTC),
            starred=0,
        )
    }
    service.db.get_platform_sessions_by_creator_paginated = AsyncMock(
        return_value=([child_item], 1)
    )
    service.db.get_branch_relations = AsyncMock(
        return_value={
            NEW_SESSION_ID: {
                "source_session_id": "deleted-source",
                "source_message_id": 2,
            }
        }
    )

    result = await service.get_sessions("alice", "webchat")

    assert result[0]["branches"] == []
    # The jump-to-source pointer is still exposed; the frontend handles a
    # dangling jump via the existing session-load error path.
    assert result[0]["branch_source"] == {
        "session_id": "deleted-source",
        "message_id": 2,
    }


@pytest.mark.asyncio
async def test_branch_session_updates_relations_cache_without_rescan():
    service = _make_service()

    await service.branch_session("alice", SRC_SESSION_ID, 2)

    # The db-level cache is updated incrementally; the expensive full-table
    # scan (get_webchat_branch_infos) is never touched by branch_session.
    service.db.update_branch_relation.assert_called_once_with(
        NEW_SESSION_ID, SRC_SESSION_ID, 2
    )
    service.db.get_webchat_branch_infos.assert_not_called()


@pytest.mark.asyncio
async def test_db_branch_relations_scan_once_and_update_incrementally():
    """BaseDatabase caches relations: one scan, then incremental updates."""
    from astrbot.core.db.sqlite import SQLiteDatabase

    db = SQLiteDatabase(":memory:")
    db._branch_relations = None
    db.get_webchat_branch_infos = AsyncMock(
        return_value=[
            SimpleNamespace(
                user_id=NEW_SESSION_ID,
                content={
                    "type": "branch_info",
                    "source_session_id": SRC_SESSION_ID,
                    "source_message_id": 2,
                },
            )
        ]
    )

    relations = await db.get_branch_relations()
    assert relations[NEW_SESSION_ID] == {
        "source_session_id": SRC_SESSION_ID,
        "source_message_id": 2,
    }
    db.get_webchat_branch_infos.assert_awaited_once()

    db.update_branch_relation("branch-2", "src-2", 5)
    relations = await db.get_branch_relations()
    assert relations["branch-2"] == {"source_session_id": "src-2", "source_message_id": 5}
    db.get_webchat_branch_infos.assert_awaited_once()  # no rescan


@pytest.mark.asyncio
async def test_branch_session_inherits_project_relation():
    """源会话属于项目时，分支会话继承相同的项目归属。"""
    project = SimpleNamespace(project_id="proj-1")
    service = _make_service(source_project=project)

    result = await service.branch_session("alice", SRC_SESSION_ID, 2)

    assert result["session_id"] == NEW_SESSION_ID
    service.db.get_project_by_session.assert_awaited_once_with(
        SRC_SESSION_ID, "alice"
    )
    service.db.add_session_to_project.assert_awaited_once_with(
        NEW_SESSION_ID, "proj-1"
    )


@pytest.mark.asyncio
async def test_branch_session_without_project_skips_relation():
    """源会话不属于任何项目时，不建立项目关联。"""
    service = _make_service(source_project=None)

    await service.branch_session("alice", SRC_SESSION_ID, 2)

    service.db.get_project_by_session.assert_awaited_once_with(
        SRC_SESSION_ID, "alice"
    )
    service.db.add_session_to_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_session_list_includes_branch_relations():
    """项目会话列表接口附带分支字段，供侧边栏渲染分支徽章。"""
    from unittest.mock import Mock

    from astrbot.dashboard.services.chatui_project_service import (
        ChatUIProjectService,
    )

    def _project_session(session_id: str, display_name: str):
        return SimpleNamespace(
            session_id=session_id,
            platform_id="webchat",
            creator="alice",
            display_name=display_name,
            is_group=0,
            created_at=datetime(2026, 7, 24, tzinfo=UTC),
            updated_at=datetime(2026, 7, 24, tzinfo=UTC),
            starred=0,
        )

    service = ChatUIProjectService(Mock())
    service.db.get_project_sessions = AsyncMock(
        return_value=[
            _project_session(SRC_SESSION_ID, "源会话"),
            _project_session(NEW_SESSION_ID, "分支 · 源会话"),
        ]
    )
    service.db.get_branch_relations = AsyncMock(
        return_value={
            NEW_SESSION_ID: {
                "source_session_id": SRC_SESSION_ID,
                "source_message_id": 2,
            }
        }
    )
    service._get_owned_project = AsyncMock()

    result = await service.get_project_sessions("alice", "proj-1")

    src = next(s for s in result if s["session_id"] == SRC_SESSION_ID)
    child = next(s for s in result if s["session_id"] == NEW_SESSION_ID)
    assert src["branches"] == [
        {"session_id": NEW_SESSION_ID, "display_name": "分支 · 源会话"}
    ]
    assert src["branch_source"] is None
    assert child["branch_source"] == {"session_id": SRC_SESSION_ID, "message_id": 2}
    assert child["branches"] == []


@pytest.mark.asyncio
async def test_project_session_list_filters_relations_to_project_members():
    """分支指向项目外的会话时，徽章被过滤但跳转源仍保留。"""
    from unittest.mock import Mock

    from astrbot.dashboard.services.chatui_project_service import (
        ChatUIProjectService,
    )

    service = ChatUIProjectService(Mock())
    service.db.get_project_sessions = AsyncMock(
        return_value=[
            SimpleNamespace(
                session_id=NEW_SESSION_ID,
                platform_id="webchat",
                creator="alice",
                display_name="分支 · 源会话",
                is_group=0,
                created_at=datetime(2026, 7, 24, tzinfo=UTC),
                updated_at=datetime(2026, 7, 24, tzinfo=UTC),
                starred=0,
            )
        ]
    )
    service.db.get_branch_relations = AsyncMock(
        return_value={
            NEW_SESSION_ID: {
                "source_session_id": "outside-project-src",
                "source_message_id": 2,
            }
        }
    )
    service._get_owned_project = AsyncMock()

    result = await service.get_project_sessions("alice", "proj-1")

    assert result[0]["branches"] == []
    assert result[0]["branch_source"] == {
        "session_id": "outside-project-src",
        "message_id": 2,
    }


@pytest.mark.asyncio
async def test_branch_route_returns_ok_payload():
    from astrbot.dashboard.api.chat import branch_chat_message

    service = SimpleNamespace(
        branch_session=AsyncMock(
            return_value={
                "session_id": NEW_SESSION_ID,
                "display_name": "分支 · 源会话",
                "inherited_count": 2,
            }
        )
    )
    auth = SimpleNamespace(username="alice")

    response = await branch_chat_message(SRC_SESSION_ID, "2", auth, service)

    # `_run` wraps the result via `ok()` which returns a plain dict.
    assert response["status"] == "ok"
    assert response["data"]["session_id"] == NEW_SESSION_ID
    service.branch_session.assert_awaited_once_with("alice", SRC_SESSION_ID, "2")


@pytest.mark.asyncio
async def test_branch_route_surfaces_service_error():
    from astrbot.dashboard.api.chat import branch_chat_message

    service = SimpleNamespace(
        branch_session=AsyncMock(side_effect=ChatServiceError("Permission denied"))
    )
    auth = SimpleNamespace(username="alice")

    response = await branch_chat_message(SRC_SESSION_ID, "2", auth, service)

    assert response["status"] == "error"
    assert response["message"] == "Permission denied"
