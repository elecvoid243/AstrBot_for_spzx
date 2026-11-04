"""Side-chat thread creation (2026-10-22, elecvoid243).

Covers the backend half of the temporary side chat: no parent message, an
empty seeded conversation, ownership checks, and the untouched branch-thread
validation.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core import sp
from astrbot.core.astr_main_agent import _get_session_conv
from astrbot.core.conversation_mgr import ConversationManager
from astrbot.core.platform_message_history_mgr import PlatformMessageHistoryManager
from astrbot.core.tools.fs_access import (
    FileAccessMode,
    clear_mode_for_umo,
    get_mode_for_umo,
    set_mode_for_umo,
)
from astrbot.core.utils.webchat_access import (
    DashboardPrincipal,
    dashboard_account_context,
    webchat_resource_owner,
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


@pytest.mark.asyncio
async def test_side_chat_menu_includes_quoted_and_empty_threads(temp_db) -> None:
    """List both entry types while keeping foreign threads out of the menu.

    Args:
        temp_db: Isolated SQLite database.
    """
    service = make_service()
    service.db = temp_db
    service.platform_history_mgr = SimpleNamespace(get=AsyncMock(return_value=[]))
    parent = await temp_db.create_platform_session(creator="alice")
    own_threads = []
    for creator, message_id, selected_text in (
        ("alice", 0, ""),
        ("alice", 7, "Quoted answer"),
        ("bob", 8, "Foreign quote"),
    ):
        thread = await temp_db.create_webchat_thread(
            creator=creator,
            parent_session_id=parent.session_id,
            parent_message_id=message_id,
            base_checkpoint_id="",
            selected_text=selected_text,
        )
        if creator == "alice":
            own_threads.append(thread.thread_id)

    result = await service.list_side_threads("alice", parent.session_id)

    assert [item["thread_id"] for item in result["threads"]] == own_threads[::-1]
    assert result["threads"][0]["title"] == "Quoted answer"
    assert result["threads"][1]["title"] == ""
    with pytest.raises(ChatServiceError):
        await service.list_side_threads("bob", parent.session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("side_chat", [True, False])
@pytest.mark.parametrize("cleanup", ["thread", "session", "edit", "retry"])
async def test_owned_thread_agent_reuses_and_cleans_up_conversation(
    temp_db, monkeypatch, side_chat, cleanup
) -> None:
    """Exercise real conversation access under the authenticated owner's scope.

    Args:
        temp_db: Isolated SQLite database.
        monkeypatch: Redirect shared preferences to the isolated database.
        side_chat: Create an empty side chat or a checkpoint-based branch thread.
        cleanup: Delete the thread or parent, edit the parent turn, or retry it.
    """
    monkeypatch.setattr(sp, "db_helper", temp_db)
    monkeypatch.setattr(sp, "_cache", {})
    monkeypatch.setattr(sp, "_cache_initialized", True)
    service = make_service()
    service.db = temp_db
    service.conv_mgr = ConversationManager(temp_db)
    service.platform_history_mgr = PlatformMessageHistoryManager(temp_db)
    service.chat_runs = {}
    service.chat_runs_by_session = {}
    service.running_convs = {}
    service.umop_config_router = service.core_lifecycle.umop_config_router
    service.core_lifecycle.astrbot_config_mgr.confs = {"default": {}}
    parent = await temp_db.create_platform_session(creator="alice")
    await service.conv_mgr.new_conversation(
        service._parent_session_umo("alice", parent.session_id),
        content=[
            {"role": "user", "content": "Question"},
            {"role": "assistant", "content": "Answer"},
            {"role": "_checkpoint", "content": {"id": "checkpoint-1"}},
        ],
    )
    user_message = await temp_db.insert_platform_message_history(
        platform_id="webchat",
        user_id=parent.session_id,
        content={"type": "user", "message": [{"type": "plain", "text": "Question"}]},
        llm_checkpoint_id="checkpoint-1",
    )
    message = await temp_db.insert_platform_message_history(
        platform_id="webchat",
        user_id=parent.session_id,
        content={"type": "bot", "message": [{"type": "plain", "text": "Answer"}]},
        llm_checkpoint_id="checkpoint-1",
    )
    principal = DashboardPrincipal("account-1", "alice", "user", "alice")
    account_token = dashboard_account_context.set(principal)
    owner_token = webchat_resource_owner.set("alice")
    try:
        thread = await service.create_thread(
            "alice",
            {
                "session_id": parent.session_id,
                "side_chat": side_chat,
                "parent_message_id": message.id,
                "selected_text": "Answer",
            },
        )
        origin = build_thread_unified_msg_origin("alice", thread["thread_id"])
        cid = await service.conv_mgr.get_curr_conversation_id(origin)
        event = SimpleNamespace(
            unified_msg_origin=origin,
            get_extra=lambda _: None,
            get_platform_id=lambda: "webchat",
        )
        context = SimpleNamespace(conversation_manager=service.conv_mgr)
        assert (await _get_session_conv(event, context)).cid == cid
        history = [{"role": "user", "content": "Side question"}]
        await service.conv_mgr.update_conversation(origin, cid, history=history)
        assert (await _get_session_conv(event, context)).history == (
            '[{"role": "user", "content": "Side question"}]'
        )
        assert len(await temp_db.get_conversations(user_id=origin)) == 1
        parent_origin = service._parent_session_umo("alice", parent.session_id)
        set_mode_for_umo(parent_origin, FileAccessMode.READONLY)
        try:
            payload = await service.prepare_thread_chat_payload(
                "alice", {"thread_id": thread["thread_id"], "message": "Follow up"}
            )
            assert payload["_thread_selected_text"] == ("" if side_chat else "Answer")
            assert get_mode_for_umo(origin) is FileAccessMode.READONLY
        finally:
            clear_mode_for_umo(parent_origin)
        if cleanup == "session":
            await service.delete_webchat_session("alice", parent.session_id)
        elif cleanup == "thread":
            await service.delete_thread("alice", thread["thread_id"])
        else:
            if cleanup == "edit":
                await service.update_message(
                    "alice",
                    {
                        "session_id": parent.session_id,
                        "message_id": user_message.id,
                        "content": user_message.content,
                    },
                )
            else:
                await service.prepare_regenerate_message_payload(
                    "alice",
                    {"session_id": parent.session_id, "message_id": message.id},
                )
            if side_chat:
                assert await temp_db.get_webchat_thread_by_id(thread["thread_id"])
                assert (await _get_session_conv(event, context)).cid == cid
                await service.delete_thread("alice", thread["thread_id"])
    finally:
        webchat_resource_owner.reset(owner_token)
        dashboard_account_context.reset(account_token)
    assert await temp_db.get_webchat_thread_by_id(thread["thread_id"]) is None
    assert await temp_db.get_conversations(user_id=origin) == []
    clear_mode_for_umo(origin)


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_message_id", [0, 7])
async def test_thread_conversation_access_requires_thread_and_parent_ownership(
    temp_db, parent_message_id
) -> None:
    """Keep foreign, forged and orphaned thread conversations inaccessible.

    Args:
        temp_db: Isolated SQLite database.
        parent_message_id: Sentinel for a side chat or a branch's message ID.
    """
    own = await temp_db.create_platform_session(creator="alice")
    foreign = await temp_db.create_platform_session(creator="bob")
    other_platform = await temp_db.create_platform_session(
        creator="alice", platform_id="qq"
    )
    records = []
    for creator, parent_id in (
        ("alice", own.session_id),
        ("bob", foreign.session_id),
        ("alice", foreign.session_id),
        ("bob", own.session_id),
        ("alice", "missing-parent"),
        ("alice", other_platform.session_id),
    ):
        thread = await temp_db.create_webchat_thread(
            creator=creator,
            parent_session_id=parent_id,
            parent_message_id=parent_message_id,
            base_checkpoint_id="",
            selected_text="",
        )
        records.append(
            await temp_db.create_conversation(
                user_id=build_thread_unified_msg_origin(creator, thread.thread_id),
                platform_id="webchat",
                content=[{"role": "user", "content": "Private history"}],
            )
        )
    records.append(
        await temp_db.create_conversation(
            user_id=records[0].user_id.replace("!alice!", "!bob!"),
            platform_id="webchat",
            content=[],
        )
    )
    token = webchat_resource_owner.set("alice")
    try:
        assert await temp_db.get_conversation_by_id(records[0].conversation_id)
        listed, total = await temp_db.get_filtered_conversations(page_size=1)
        assert total == 1
        assert [item.conversation_id for item in listed] == [records[0].conversation_id]
        for record in records[1:]:
            assert await temp_db.get_conversation_by_id(record.conversation_id) is None
            assert (
                await temp_db.update_conversation(record.conversation_id, content=[])
                is None
            )
            with pytest.raises(PermissionError):
                await temp_db.delete_conversation(record.conversation_id)
            await temp_db.delete_conversations_by_user_id(record.user_id)
    finally:
        webchat_resource_owner.reset(token)
    for record in records[1:]:
        stored = await temp_db.get_conversation_by_id(record.conversation_id)
        assert stored.content == record.content
