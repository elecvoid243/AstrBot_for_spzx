"""Side-chat thread creation (2026-10-22, elecvoid243).

Covers the backend half of the temporary side chat: no parent message, an
empty seeded conversation, ownership checks, and the untouched branch-thread
validation.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.tools.fs_access import (
    FileAccessMode,
    clear_mode_for_umo,
    get_mode_for_umo,
    set_mode_for_umo,
)
from astrbot.dashboard.services.chat_service import (
    ChatService,
    ChatServiceError,
    build_thread_unified_msg_origin,
)


def make_service(session_creator: str = "alice") -> ChatService:
    service = object.__new__(ChatService)
    service.db = MagicMock()
    service.db.get_platform_session_by_id = AsyncMock(
        return_value=SimpleNamespace(creator=session_creator)
    )
    thread = SimpleNamespace(
        thread_id="thread-1",
        parent_session_id="session-1",
        parent_message_id=0,
        base_checkpoint_id="",
        selected_text="",
        created_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
        updated_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
    )
    service.db.create_webchat_thread = AsyncMock(return_value=thread)
    service.conv_mgr = MagicMock()
    service.conv_mgr.new_conversation = AsyncMock(return_value="conv-1")
    service.core_lifecycle = SimpleNamespace(
        umop_config_router=SimpleNamespace(
            get_conf_id_for_umop=MagicMock(return_value=None),
            update_route=AsyncMock(),
            delete_route=AsyncMock(),
        ),
        astrbot_config_mgr=SimpleNamespace(get_conf=MagicMock(return_value={})),
    )
    return service


@pytest.mark.asyncio
async def test_side_chat_skips_parent_and_starts_empty() -> None:
    service = make_service()

    result = await service.create_thread(
        "alice", {"session_id": "session-1", "side_chat": True}
    )

    service.db.create_webchat_thread.assert_awaited_once_with(
        creator="alice",
        parent_session_id="session-1",
        parent_message_id=0,
        base_checkpoint_id="",
        selected_text="",
    )
    kwargs = service.conv_mgr.new_conversation.await_args.kwargs
    assert kwargs["platform_id"] == "webchat"
    assert kwargs["content"] == []
    assert result["thread_id"] == "thread-1"
    assert result["parent_message_id"] == 0
    assert result["selected_text"] == ""


@pytest.mark.asyncio
async def test_side_chat_rejects_a_foreign_session() -> None:
    service = make_service(session_creator="bob")

    with pytest.raises(ChatServiceError):
        await service.create_thread(
            "alice", {"session_id": "session-1", "side_chat": True}
        )

    service.db.create_webchat_thread.assert_not_awaited()
    service.conv_mgr.new_conversation.assert_not_awaited()


@pytest.mark.asyncio
async def test_side_chat_inherits_config_profile_and_file_access() -> None:
    service = make_service()
    router = service.core_lifecycle.umop_config_router
    router.get_conf_id_for_umop.return_value = "profile-b"

    parent_umo = service._parent_session_umo("alice", "session-1")
    thread_umo = build_thread_unified_msg_origin("alice", "thread-1")
    set_mode_for_umo(parent_umo, FileAccessMode.READONLY)
    try:
        await service.create_thread(
            "alice", {"session_id": "session-1", "side_chat": True}
        )

        router.update_route.assert_awaited_once_with(thread_umo, "profile-b")
        assert get_mode_for_umo(thread_umo) is FileAccessMode.READONLY
    finally:
        clear_mode_for_umo(parent_umo)
        clear_mode_for_umo(thread_umo)


@pytest.mark.asyncio
async def test_branch_thread_still_requires_parent_and_selection() -> None:
    service = make_service()

    with pytest.raises(ChatServiceError):
        await service.create_thread("alice", {"session_id": "session-1"})

    with pytest.raises(ChatServiceError):
        await service.create_thread(
            "alice",
            {
                "session_id": "session-1",
                "parent_message_id": 7,
                "selected_text": "   ",
            },
        )


@pytest.mark.asyncio
async def test_side_thread_title_uses_the_first_user_message() -> None:
    service = make_service()
    history = [
        SimpleNamespace(
            content={"type": "bot", "message": [{"type": "plain", "text": "回复"}]}
        ),
        SimpleNamespace(
            content={"type": "user", "message": [{"type": "plain", "text": "第二问"}]}
        ),
        SimpleNamespace(
            content={"type": "user", "message": [{"type": "plain", "text": "第一问"}]}
        ),
    ]
    service.platform_history_mgr = SimpleNamespace(get=AsyncMock(return_value=history))

    assert await service._side_thread_title("thread-1") == "第一问"
