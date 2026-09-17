"""ChatUI session export: zip layout, manifest and checksums.

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

import hashlib
import json
import os
import re
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot.core.db.po import PlatformMessageHistory, PlatformSession, WebChatThread
from astrbot.dashboard.services import session_transfer_service
from astrbot.dashboard.services.session_transfer_service import (
    ATTACHMENTS_PREFIX,
    EXPORT_DATA_NAME,
    EXPORT_FORMAT_VERSION,
    EXPORT_KIND,
    IMPORT_ID_TTL_SECONDS,
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


@pytest.fixture(autouse=True)
def _isolated_temp_dir(tmp_path, monkeypatch):
    """Stream export archives into the test's temporary directory.

    ``SessionTransferService.__init__`` resolves the temp directory through the
    module-level ``get_astrbot_temp_path``, so patching that name keeps the
    archives written by ``export_session`` out of the real ``data/temp``.
    """
    monkeypatch.setattr(
        session_transfer_service,
        "get_astrbot_temp_path",
        lambda: str(tmp_path),
    )


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

    with zipfile.ZipFile(export.path) as zf:
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
        # `source_path` is exporter-internal: it names the exporting host's
        # install directory and OS user, and the data contract is public.
        assert "source_path" not in exported_session["attachments"][0]


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

    with zipfile.ZipFile(export.path) as zf:
        manifest = json.loads(zf.read(MANIFEST_NAME))
        assert manifest["warnings"]
        assert "att-1" in manifest["warnings"][0]
        assert f"{ATTACHMENTS_PREFIX}att-1.png" not in set(zf.namelist())


@pytest.mark.asyncio
async def test_export_packages_attachments_referenced_only_by_a_thread(tmp_path):
    """A file attached inside a side thread is packaged, not silently dropped.

    Thread history persists the same message-part shape as the main stream
    (chat_service.create_thread), so its attachment ids must be collected too.
    """
    thread_attachment_path = tmp_path / "thread-1.png"
    thread_attachment_path.write_bytes(b"THREADPNG")
    service = _make_service(attachment_dir=tmp_path)

    # A real PO: `_serialize_row` calls model_dump() on it.
    thread = WebChatThread(
        thread_id="thr-1",
        creator="alice",
        parent_session_id=SESSION_ID,
        parent_message_id=1,
        base_checkpoint_id="ck-1",
        selected_text="hi",
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        updated_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    service.db.get_webchat_threads_by_parent_session = AsyncMock(return_value=[thread])

    # Main stream carries no attachment; only the thread does.
    main_row = _row(
        content={"type": "user", "message": [{"type": "plain", "text": "hi"}]}
    )
    thread_row = _row(
        id=2,
        content={
            "type": "user",
            "message": [
                {"type": "image", "attachment_id": "att-thread", "filename": "t.png"}
            ],
        },
    )

    async def _history(platform_id, user_id, **kwargs):
        if platform_id == "webchat_thread":
            return [thread_row]
        return [main_row]

    service.platform_history_mgr.get = AsyncMock(side_effect=_history)
    service.db.get_attachment_by_id = AsyncMock(
        return_value=SimpleNamespace(
            attachment_id="att-thread",
            path=str(thread_attachment_path),
            type="image",
            mime_type="image/png",
        )
    )

    export = await service.export_session("alice", SESSION_ID)

    with zipfile.ZipFile(export.path) as zf:
        assert f"{ATTACHMENTS_PREFIX}att-thread.png" in set(zf.namelist())
        manifest = json.loads(zf.read(MANIFEST_NAME))
        stats = manifest["sessions"][0]["stats"]
        assert stats["attachments"] == 1
        assert stats["attachment_bytes"] == len(b"THREADPNG")
        assert manifest["warnings"] == []
        data = json.loads(zf.read(EXPORT_DATA_NAME))
        assert data["sessions"][0]["attachments"][0]["attachment_id"] == "att-thread"


@pytest.mark.asyncio
async def test_export_refuses_archives_over_the_import_limit(tmp_path, monkeypatch):
    """A package the importer would reject must never be handed out.

    ``stage_import`` refuses anything larger than ``MAX_UPLOAD_BYTES``, so an
    export above that cap would be a dead archive nobody could ever import.
    The refusal must name both numbers the user needs to act on - the size the
    archive really reached and the limit it breached - and must delete the
    temporary file it just wrote.
    """
    monkeypatch.setattr(session_transfer_service, "MAX_UPLOAD_BYTES", 64)
    service = _make_service(attachment_dir=tmp_path)

    # Capture the real on-disk size at the moment the refusal deletes it, so
    # the reported number can be checked against the artefact, not a literal.
    measured: list[int] = []
    real_unlink = Path.unlink

    def _recording_unlink(self, *args, **kwargs):
        if self.name.startswith("session_export_"):
            measured.append(self.stat().st_size)
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _recording_unlink)

    with pytest.raises(SessionTransferError) as excinfo:
        await service.export_session("alice", SESSION_ID)

    message = str(excinfo.value)
    assert "exceeds" in message
    numbers = [int(value) for value in re.findall(r"\d+", message)]
    assert session_transfer_service.MAX_UPLOAD_BYTES in numbers
    assert measured, "the refused archive was never deleted"
    assert measured[0] in numbers, (measured, message)
    assert measured[0] > session_transfer_service.MAX_UPLOAD_BYTES
    assert list(tmp_path.glob("session_export_*.zip")) == []


def _service_with_one_attachment(tmp_path, payload=b"PNGDATA"):
    """Build a service whose session references one real attachment on disk."""
    attachment = tmp_path / "att-1.png"
    attachment.write_bytes(payload)
    service = _make_service(attachment_dir=tmp_path)
    service.db.get_attachment_by_id = AsyncMock(
        return_value=SimpleNamespace(
            attachment_id="att-1",
            path=str(attachment),
            type="image",
            mime_type="image/png",
        )
    )
    return service


@pytest.mark.parametrize(
    ("limit_name", "limit_value", "message"),
    [
        ("MAX_ZIP_ENTRIES", 2, "entry import limit"),
        ("MAX_ENTRY_BYTES", 4, "per-entry import limit"),
        ("MAX_TOTAL_UNCOMPRESSED_BYTES", 8, "total import limit"),
    ],
)
@pytest.mark.asyncio
async def test_export_refuses_any_importer_zip_limit_it_would_breach(
    tmp_path, monkeypatch, limit_name, limit_value, message
):
    """Every cap ``_verify_zip_safety`` applies on import is mirrored here.

    ``stage_import`` also rejects entry counts, single entries and totals
    above their caps, so checking the archive size alone is not enough: a
    session holding large, highly compressible attachments stays far below
    ``MAX_UPLOAD_BYTES`` and is still unimportable. The message must name the
    breached limit *and* the value that breached it, and the partial archive
    must not survive.
    """
    monkeypatch.setattr(session_transfer_service, limit_name, limit_value)
    service = _service_with_one_attachment(tmp_path)

    with pytest.raises(SessionTransferError, match=message) as excinfo:
        await service.export_session("alice", SESSION_ID)

    numbers = [int(value) for value in re.findall(r"\d+", str(excinfo.value))]
    assert limit_value in numbers
    assert any(number > limit_value for number in numbers), excinfo.value
    assert list(tmp_path.glob("session_export_*.zip")) == []


@pytest.mark.asyncio
async def test_export_refuses_when_export_json_alone_breaches_the_entry_cap(
    tmp_path, monkeypatch
):
    """``export.json`` is an entry, so the per-entry cap applies to it too.

    The importer runs ``_verify_zip_safety`` over every entry, metadata
    included, so a session with no attachments at all can still produce a
    package it rejects with "Entry too large: export.json". Only checking
    attachments let exactly that dead archive out of the exporter.
    """
    service = _make_service()
    # No attachment ids anywhere: the only entries are the two metadata ones.
    service.platform_history_mgr.get = AsyncMock(
        return_value=[
            _row(content={"type": "user", "message": [{"type": "plain", "text": "hi"}]})
        ]
    )
    baseline = await service.export_session("alice", SESSION_ID)
    with zipfile.ZipFile(baseline.path) as zf:
        assert zf.namelist() == [EXPORT_DATA_NAME, MANIFEST_NAME]
        export_json_size = zf.getinfo(EXPORT_DATA_NAME).file_size
    baseline.path.unlink()

    monkeypatch.setattr(
        session_transfer_service, "MAX_ENTRY_BYTES", export_json_size - 1
    )

    with pytest.raises(SessionTransferError, match="per-entry import limit") as excinfo:
        await service.export_session("alice", SESSION_ID)

    assert EXPORT_DATA_NAME in str(excinfo.value)
    assert list(tmp_path.glob("session_export_*.zip")) == []


@pytest.mark.asyncio
async def test_export_keeps_the_writer_error_when_cleanup_fails(tmp_path, monkeypatch):
    """A failing cleanup must not mask the writer's error.

    Windows can refuse the unlink right when the writer failed (a lock or an
    AV scan), so letting that ``OSError`` escape would swap a clear
    ``SessionTransferError`` for a bare ``PermissionError`` and a generic 500.
    """
    real_unlink = Path.unlink

    def _locked_unlink(self, *args, **kwargs):
        if self.name.startswith("session_export_"):
            raise OSError("file is locked")
        return real_unlink(self, *args, **kwargs)

    def _explode(self, archive_path, *args, **kwargs):
        raise SessionTransferError("writer exploded")

    monkeypatch.setattr(Path, "unlink", _locked_unlink)
    monkeypatch.setattr(SessionTransferService, "_build_export_archive", _explode)
    service = _make_service()

    with pytest.raises(SessionTransferError, match="writer exploded"):
        await service.export_session("alice", SESSION_ID)


@pytest.mark.asyncio
async def test_export_sweeps_abandoned_export_archives(tmp_path):
    """A killed download is reclaimed by the next export; a live one is not.

    The route's ``BackgroundTask`` is the only other deleter of
    ``session_export_*.zip`` and never runs when the response is cancelled
    mid-stream, which strands up to ``MAX_UPLOAD_BYTES`` per aborted download
    with no TTL. Exports are never registered in ``pending_imports``, so the
    mtime guard is all that protects one still being written or streamed.
    """
    service = _make_service()
    stale = service.temp_dir / "session_export_stale.zip"
    stale.write_bytes(b"orphan")
    aged = time.time() - IMPORT_ID_TTL_SECONDS - 60
    os.utime(stale, (aged, aged))
    fresh = service.temp_dir / "session_export_fresh.zip"
    fresh.write_bytes(b"mid-write")

    export = await service.export_session("alice", SESSION_ID)

    assert not stale.exists()
    assert fresh.exists()
    assert export.path.exists()


@pytest.mark.asyncio
async def test_export_removes_the_archive_when_the_writer_raises(tmp_path, monkeypatch):
    """A crashed writer must not leave its archive behind in the temp dir.

    The archive exists on disk by the time the writer can still fail (its
    final steps run after the entries are written), so the cleanup around the
    ``to_thread`` call is what keeps ``data/temp`` from filling up.
    """
    real_build = SessionTransferService._build_export_archive

    def _crash_after_writing(self, archive_path, *args, **kwargs):
        real_build(self, archive_path, *args, **kwargs)
        assert archive_path.exists(), "the writer produced no archive to clean up"
        raise RuntimeError("writer exploded")

    monkeypatch.setattr(
        SessionTransferService, "_build_export_archive", _crash_after_writing
    )
    service = _make_service()

    with pytest.raises(RuntimeError, match="writer exploded"):
        await service.export_session("alice", SESSION_ID)

    assert list(tmp_path.glob("session_export_*.zip")) == []


class _FailingReader:
    """File stand-in whose second read fails, as a dying disk would."""

    def __init__(self, handle):
        self._handle = handle
        self._reads = 0

    def read(self, size=-1):
        self._reads += 1
        if self._reads > 1:
            raise OSError("disk error")
        return self._handle.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._handle.close()
        return False


@pytest.mark.asyncio
async def test_export_aborts_when_an_attachment_fails_mid_stream(tmp_path, monkeypatch):
    """A read error mid-stream must abort, not ship a truncated attachment.

    ``zf.open(..., "w")`` records whatever was written before the failure when
    its context manager closes, and the importer reads those bytes back as a
    normal blob - so degrading to a warning would silently corrupt the file.
    Only a file that was already absent may degrade (the missing-file case,
    handled by ``is_file()`` before the stream starts).
    """
    monkeypatch.setattr(session_transfer_service, "EXPORT_CHUNK_BYTES", 4)
    service = _service_with_one_attachment(tmp_path, payload=b"PNGDATA-0123456789")
    real_open = Path.open

    def _flaky_open(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        if self.name == "att-1.png":
            return _FailingReader(handle)
        return handle

    monkeypatch.setattr(Path, "open", _flaky_open)

    with pytest.raises(SessionTransferError) as excinfo:
        await service.export_session("alice", SESSION_ID)

    assert "att-1" in str(excinfo.value)
    assert "could not be read" in str(excinfo.value)
    assert list(tmp_path.glob("session_export_*.zip")) == []
