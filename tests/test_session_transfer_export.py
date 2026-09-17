"""ChatUI session export: zip layout, manifest and checksums.

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

import hashlib
import json
import tempfile
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot.core.db.po import PlatformMessageHistory, PlatformSession
from astrbot.dashboard.services.session_transfer_service import (
    ATTACHMENTS_PREFIX,
    EXPORT_DATA_NAME,
    EXPORT_FORMAT_VERSION,
    EXPORT_KIND,
    MANIFEST_NAME,
    SessionTransferError,
    SessionTransferService,
)

SESSION_ID = "sess-1"


def _row(**overrides):
    """Build a real PlatformMessageHistory row (same style as
    tests/test_chat_history_pagination.py)."""
    fields = {
        "id": 1,
        "platform_id": "webchat",
        "user_id": SESSION_ID,
        "sender_id": "alice",
        "sender_name": "alice",
        "content": {
            "type": "user",
            "message": [
                {"type": "plain", "text": "hi"},
                {"type": "image", "attachment_id": "att-1", "filename": "a.png"},
            ],
        },
        "llm_checkpoint_id": "ck-1",
        "created_at": datetime(2026, 9, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 1, tzinfo=UTC),
    }
    fields.update(overrides)
    return PlatformMessageHistory(**fields)


def _session(**overrides):
    fields = {
        "session_id": SESSION_ID,
        "platform_id": "webchat",
        "creator": "alice",
        "is_group": 0,
        "display_name": "会话 A",
        "archived": 0,
        "created_at": datetime(2026, 9, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 1, tzinfo=UTC),
    }
    fields.update(overrides)
    return PlatformSession(**fields)


def _make_service(attachment_dir=None):
    db = Mock()
    db.get_platform_session_by_id = AsyncMock(return_value=_session())
    db.get_webchat_threads_by_parent_session = AsyncMock(return_value=[])
    db.get_conversations = AsyncMock(return_value=[])
    db.get_preferences = AsyncMock(return_value=[])
    db.get_attachment_by_id = AsyncMock(return_value=None)
    core_lifecycle = SimpleNamespace(
        conversation_manager=Mock(),
        platform_message_history_manager=Mock(),
    )
    service = SessionTransferService(db, core_lifecycle)
    service.platform_history_mgr.get = AsyncMock(return_value=[_row()])
    service.attachments_dir = Path(attachment_dir or tempfile.gettempdir())
    return service


@pytest.mark.asyncio
async def test_export_rejects_non_owner():
    service = _make_service()
    db_session = _session()
    db_session.creator = "someone-else"
    service.db.get_platform_session_by_id = AsyncMock(return_value=db_session)

    with pytest.raises(SessionTransferError, match="Permission denied"):
        await service.export_session("alice", SESSION_ID)


@pytest.mark.asyncio
async def test_export_rejects_non_webchat_platform():
    service = _make_service()
    db_session = _session()
    db_session.platform_id = "aiocqhttp"
    service.db.get_platform_session_by_id = AsyncMock(return_value=db_session)

    with pytest.raises(SessionTransferError, match="webchat"):
        await service.export_session("alice", SESSION_ID)


@pytest.mark.asyncio
async def test_export_package_layout_and_checksums(tmp_path):
    attachment = tmp_path / "att-1.png"
    attachment.write_bytes(b"PNGDATA")
    service = _make_service(attachment_dir=tmp_path)
    service.db.get_attachment_by_id = AsyncMock(
        return_value=SimpleNamespace(
            attachment_id="att-1",
            path=str(attachment),
            type="image",
            mime_type="image/png",
        )
    )

    export = await service.export_session("alice", SESSION_ID)

    assert export.mimetype == "application/zip"
    assert export.filename.startswith("astrbot_chatui_export_")
    assert export.filename.endswith(".zip")

    with zipfile.ZipFile(BytesIO(export.file_obj.getvalue())) as zf:
        names = set(zf.namelist())
        assert MANIFEST_NAME in names
        assert EXPORT_DATA_NAME in names
        assert f"{ATTACHMENTS_PREFIX}att-1.png" in names

        manifest = json.loads(zf.read(MANIFEST_NAME))
        assert manifest["kind"] == EXPORT_KIND
        assert manifest["format_version"] == EXPORT_FORMAT_VERSION
        assert manifest["warnings"] == []
        assert manifest["sessions"][0]["original_session_id"] == SESSION_ID
        assert manifest["sessions"][0]["display_name"] == "会话 A"
        assert manifest["sessions"][0]["original_creator"] == "alice"
        stats = manifest["sessions"][0]["stats"]
        assert stats["messages"] == 1
        assert stats["attachments"] == 1
        assert stats["attachment_bytes"] == len(b"PNGDATA")

        payload = zf.read(EXPORT_DATA_NAME)
        digest = hashlib.sha256(payload).hexdigest()
        assert manifest["checksums"][EXPORT_DATA_NAME] == f"sha256:{digest}"
        blob = zf.read(f"{ATTACHMENTS_PREFIX}att-1.png")
        assert manifest["checksums"][f"{ATTACHMENTS_PREFIX}att-1.png"] == (
            f"sha256:{hashlib.sha256(blob).hexdigest()}"
        )

        data = json.loads(payload)
        exported_session = data["sessions"][0]
        assert exported_session["session"]["session_id"] == SESSION_ID
        assert exported_session["history"][0]["id"] == 1
        assert exported_session["history"][0]["created_at"].startswith("2026-09-01")
        assert exported_session["attachments"][0]["attachment_id"] == "att-1"
        assert exported_session["attachments"][0]["ext"] == ".png"
        assert exported_session["attachments"][0]["zip_path"] == (
            f"{ATTACHMENTS_PREFIX}att-1.png"
        )


@pytest.mark.asyncio
async def test_export_records_warning_when_attachment_file_is_missing(tmp_path):
    service = _make_service(attachment_dir=tmp_path)
    service.db.get_attachment_by_id = AsyncMock(
        return_value=SimpleNamespace(
            attachment_id="att-1",
            path=str(tmp_path / "gone.png"),
            type="image",
            mime_type="image/png",
        )
    )

    export = await service.export_session("alice", SESSION_ID)

    with zipfile.ZipFile(BytesIO(export.file_obj.getvalue())) as zf:
        manifest = json.loads(zf.read(MANIFEST_NAME))
        assert manifest["warnings"]
        assert "att-1" in manifest["warnings"][0]
        assert f"{ATTACHMENTS_PREFIX}att-1.png" not in set(zf.namelist())
