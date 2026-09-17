"""ChatUI session import: staging, safety guards and round-trip.

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

import json
import zipfile
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot.core.config.default import VERSION
from astrbot.dashboard.services import session_transfer_service
from astrbot.dashboard.services.session_transfer_service import (
    EXPORT_DATA_NAME,
    EXPORT_FORMAT_VERSION,
    EXPORT_KIND,
    MANIFEST_NAME,
    MAX_ZIP_ENTRIES,
    SessionTransferError,
    SessionTransferService,
)


def build_package_bytes(
    *,
    kind=EXPORT_KIND,
    format_version=EXPORT_FORMAT_VERSION,
    astrbot_version=VERSION,
    extra_entries=(),
    sessions=None,
):
    """Build an import zip in memory for staging tests."""
    manifest = {
        "kind": kind,
        "format_version": format_version,
        "astrbot_version": astrbot_version,
        "exported_at": "2026-09-17T00:00:00+00:00",
        "sessions": [
            {
                "original_session_id": "sess-1",
                "display_name": "会话 A",
                "original_creator": "alice",
                "stats": {
                    "messages": 0,
                    "conversations": 0,
                    "threads": 0,
                    "attachments": 0,
                    "attachment_bytes": 0,
                },
            }
        ],
        "warnings": [],
        "checksums": {},
    }
    payload = {"sessions": sessions if sessions is not None else []}
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest))
        zf.writestr(EXPORT_DATA_NAME, json.dumps(payload))
        for name, blob in extra_entries:
            zf.writestr(name, blob)
    buffer.seek(0)
    return buffer


class _FakeUpload:
    """Minimal UploadFileAdapter stand-in backed by bytes."""

    def __init__(self, blob: bytes, filename="pkg.zip", content_length=None):
        self._blob = blob
        self.filename = filename
        self.content_type = "application/zip"
        self.content_length = (
            content_length if content_length is not None else len(blob)
        )

    async def save(self, destination):
        with open(destination, "wb") as handle:
            handle.write(self._blob)


def _make_service():
    db = Mock()
    db.get_preferences = AsyncMock(return_value=[])
    db.get_conversations = AsyncMock(return_value=[])
    db.get_attachment_by_id = AsyncMock(return_value=None)
    core_lifecycle = SimpleNamespace(
        conversation_manager=Mock(),
        platform_message_history_manager=Mock(),
    )
    return SessionTransferService(db, core_lifecycle)


@pytest.fixture(autouse=True)
def _isolated_temp_dir(tmp_path, monkeypatch):
    """Stage packages under tmp_path, never the real ``data/temp`` directory.

    ``SessionTransferService.__init__`` resolves the temp directory through the
    module-level ``get_astrbot_temp_path``, so patching that name keeps every
    staged zip inside the test's temporary directory and leaves no litter
    behind for the next run.
    """
    monkeypatch.setattr(
        session_transfer_service,
        "get_astrbot_temp_path",
        lambda: str(tmp_path),
    )


@pytest.mark.asyncio
async def test_stage_import_rejects_wrong_kind():
    service = _make_service()
    upload = _FakeUpload(build_package_bytes(kind="something-else").getvalue())

    with pytest.raises(SessionTransferError, match="Unsupported package"):
        await service.stage_import(upload)


@pytest.mark.asyncio
async def test_stage_import_rejects_unknown_format_version():
    service = _make_service()
    upload = _FakeUpload(build_package_bytes(format_version=99).getvalue())

    with pytest.raises(SessionTransferError, match="format_version"):
        await service.stage_import(upload)


@pytest.mark.asyncio
async def test_stage_import_rejects_incompatible_major_version():
    service = _make_service()
    upload = _FakeUpload(build_package_bytes(astrbot_version="3.0.0").getvalue())

    with pytest.raises(SessionTransferError):
        await service.stage_import(upload)


@pytest.mark.asyncio
async def test_stage_import_rejects_oversized_upload():
    service = _make_service()
    upload = _FakeUpload(b"x", content_length=1024 * 1024 * 1024)

    with pytest.raises(SessionTransferError, match="too large"):
        await service.stage_import(upload)


@pytest.mark.asyncio
async def test_stage_import_rejects_too_many_entries():
    service = _make_service()
    entries = [(f"files/attachments/{i}.bin", b"x") for i in range(MAX_ZIP_ENTRIES + 1)]
    upload = _FakeUpload(build_package_bytes(extra_entries=entries).getvalue())

    with pytest.raises(SessionTransferError, match="entries"):
        await service.stage_import(upload)


# One exported session body: only its presence matters for ``can_import``,
# which reports whether the package holds anything to import.
_EXPORTED_SESSION = {"session": {"session_id": "sess-1", "display_name": "会话 A"}}


@pytest.mark.asyncio
async def test_stage_import_returns_preview_and_can_import():
    service = _make_service()
    upload = _FakeUpload(build_package_bytes(sessions=[_EXPORTED_SESSION]).getvalue())

    preview = await service.stage_import(upload)

    assert preview["can_import"] is True
    assert preview["import_id"] in service.pending_imports
    assert preview["sessions"][0]["display_name"] == "会话 A"
    assert preview["sessions"][0]["original_creator"] == "alice"
    assert preview["version_status"]["compatible"] is True


@pytest.mark.asyncio
async def test_stage_import_marks_empty_package_not_importable():
    """A structurally valid package holding no sessions is not importable.

    Task 4's ``confirm_import`` refuses a preview whose ``can_import`` is false
    and reports "no importable sessions", so an empty payload must stage
    cleanly yet report False, instead of raising during staging.
    """
    service = _make_service()
    upload = _FakeUpload(build_package_bytes(sessions=[]).getvalue())

    preview = await service.stage_import(upload)

    assert preview["can_import"] is False
    assert preview["import_id"] in service.pending_imports


@pytest.mark.asyncio
async def test_pending_import_expires():
    service = _make_service()
    preview = await service.stage_import(_FakeUpload(build_package_bytes().getvalue()))
    service.pending_imports[preview["import_id"]].created_at -= 99999

    with pytest.raises(SessionTransferError, match="expired"):
        service._pending(preview["import_id"])
