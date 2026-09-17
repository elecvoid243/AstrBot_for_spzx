"""ChatUI session export -> import round trip on a real sqlite database.

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from astrbot.core.conversation_mgr import ConversationManager
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.platform_message_history_mgr import PlatformMessageHistoryManager
from astrbot.dashboard.services.session_transfer_service import SessionTransferService


class _Upload:
    """UploadFileAdapter stand-in backed by bytes."""

    def __init__(self, blob: bytes, filename: str = "pkg.zip") -> None:
        self._blob = blob
        self.filename = filename
        self.content_type = "application/zip"
        self.content_length = len(blob)

    async def save(self, destination) -> None:
        with open(destination, "wb") as handle:
            handle.write(self._blob)


async def _seed(tmp_path):
    """Create a webchat session for alice with one attachment and one LLM turn."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    history_mgr = PlatformMessageHistoryManager(db)
    conv_mgr = ConversationManager(db)
    lifecycle = SimpleNamespace(
        conversation_manager=conv_mgr,
        platform_message_history_manager=history_mgr,
    )
    service = SessionTransferService(db, lifecycle)
    service.attachments_dir = tmp_path / "attachments"
    service.attachments_dir.mkdir(parents=True, exist_ok=True)
    service.temp_dir = tmp_path / "temp"

    session = await db.create_platform_session(
        creator="alice",
        platform_id="webchat",
        display_name="会话 A",
    )
    attachment_path = service.attachments_dir / "src-image.png"
    attachment_path.write_bytes(b"PNGDATA")
    attachment = await db.insert_attachment(
        path=str(attachment_path), type="image", mime_type="image/png"
    )

    await history_mgr.insert(
        platform_id="webchat",
        user_id=session.session_id,
        content={
            "type": "user",
            "message": [
                {"type": "plain", "text": "hi"},
                {
                    "type": "image",
                    "attachment_id": attachment.attachment_id,
                    "filename": "src-image.png",
                },
            ],
        },
        sender_id="alice",
        sender_name="alice",
        llm_checkpoint_id="ck-1",
    )
    source_umo = f"webchat:FriendMessage:webchat!alice!{session.session_id}"
    await conv_mgr.new_conversation(
        unified_msg_origin=source_umo,
        platform_id="webchat",
        content=[{"role": "user", "content": "hi"}],
        title="t",
    )
    return service, db, session, attachment


@pytest.mark.asyncio
async def test_export_import_round_trip_migrates_session_to_another_user(tmp_path):
    service, db, session, attachment = await _seed(tmp_path)

    export = await service.export_session("alice", session.session_id)
    preview = await service.stage_import(_Upload(export.file_obj.getvalue()))
    assert preview["can_import"] is True

    result = await service.confirm_import("bob", preview["import_id"])
    assert result["errors"] == []
    assert len(result["created"]) == 1
    new_session_id = result["created"][0]["new_session_id"]
    assert new_session_id != session.session_id

    # Ownership moved to the importer; metadata survived.
    imported = await db.get_platform_session_by_id(new_session_id)
    assert imported is not None
    assert imported.creator == "bob"
    assert imported.display_name == "会话 A"

    # The message stream came across with its checkpoint id intact.
    imported_history = await service.platform_history_mgr.get(
        platform_id="webchat", user_id=new_session_id, page=1, page_size=100
    )
    assert len(imported_history) == 1
    assert imported_history[0].llm_checkpoint_id == "ck-1"

    # Same-database import means the source attachment id was taken, so the
    # part now points at a freshly issued id and a rewritten on-disk path.
    image_part = imported_history[0].content["message"][1]
    assert image_part["attachment_id"] != attachment.attachment_id
    new_attachment = await db.get_attachment_by_id(image_part["attachment_id"])
    assert new_attachment is not None
    assert Path(new_attachment.path).read_bytes() == b"PNGDATA"

    # LLM context follows the new UMO so the conversation can continue.
    new_umo = f"webchat:FriendMessage:webchat!bob!{new_session_id}"
    conversations = await db.get_conversations(user_id=new_umo)
    assert len(conversations) == 1

    # The source session is untouched.
    source_history = await service.platform_history_mgr.get(
        platform_id="webchat", user_id=session.session_id, page=1, page_size=100
    )
    assert len(source_history) == 1
    source_attachment = await db.get_attachment_by_id(attachment.attachment_id)
    assert source_attachment is not None
