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
    MAX_ENTRY_BYTES,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
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
async def test_stage_import_accepts_other_version_with_warning():
    """A package from another AstrBot version stages, with a warning.

    The schema contract is ``format_version``; the exporter's AstrBot version
    is informational, because cross-instance migration routinely crosses
    minor releases (``4.28.1`` -> ``4.29.0``). Task 8's dialog renders the
    resulting warning.
    """
    service = _make_service()
    upload = _FakeUpload(
        build_package_bytes(
            astrbot_version="3.0.0", sessions=[_EXPORTED_SESSION]
        ).getvalue()
    )

    preview = await service.stage_import(upload)

    assert preview["can_import"] is True
    assert preview["version_status"]["compatible"] is True
    assert preview["version_status"]["package_version"] == "3.0.0"
    assert preview["version_status"]["upgrade_advised"] is True
    assert any("3.0.0" in warning for warning in preview["warnings"])


@pytest.mark.asyncio
async def test_stage_import_warns_when_version_is_absent():
    """A package that declares no version still stages, but says so."""
    service = _make_service()
    upload = _FakeUpload(
        build_package_bytes(astrbot_version="", sessions=[_EXPORTED_SESSION]).getvalue()
    )

    preview = await service.stage_import(upload)

    assert preview["can_import"] is True
    assert preview["version_status"]["package_version"] == ""
    assert preview["version_status"]["upgrade_advised"] is False
    assert any("does not declare" in warning for warning in preview["warnings"])


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


@pytest.mark.asyncio
async def test_stage_import_rejects_oversized_upload_without_content_length(
    monkeypatch,
):
    """The on-disk size check is the only guard when the client omits the header.

    A chunked upload carries no ``content-length``, so the declared-length
    check is skipped and the size read after saving is all that stands between
    the server and an oversized package.
    """
    monkeypatch.setattr(session_transfer_service, "MAX_UPLOAD_BYTES", 1024)
    service = _make_service()
    upload = _FakeUpload(b"x" * 4096)
    # A chunked upload declares no length at all — the helper's default maps
    # None to len(blob), so clear it explicitly to take the header guard out
    # of the picture and leave the post-save check as the only defence.
    upload.content_length = None

    with pytest.raises(SessionTransferError, match="too large"):
        await service.stage_import(upload)


@pytest.mark.asyncio
async def test_stage_import_rejects_corrupt_zip():
    """A non-zip upload is converted, not leaked as a raw archive error."""
    service = _make_service()
    upload = _FakeUpload(b"not a zip at all")

    with pytest.raises(SessionTransferError, match="not a valid zip"):
        await service.stage_import(upload)


@pytest.mark.asyncio
async def test_stage_import_rejects_package_missing_required_entries():
    service = _make_service()
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(MANIFEST_NAME, json.dumps({"kind": EXPORT_KIND}))
    buffer.seek(0)
    upload = _FakeUpload(buffer.getvalue())

    with pytest.raises(SessionTransferError, match="missing required entries"):
        await service.stage_import(upload)


@pytest.mark.asyncio
async def test_stage_import_rejects_malformed_payload():
    """A structurally valid archive whose payload is not an object is refused."""
    service = _make_service()
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            MANIFEST_NAME,
            json.dumps(
                {
                    "kind": EXPORT_KIND,
                    "format_version": EXPORT_FORMAT_VERSION,
                    "astrbot_version": VERSION,
                }
            ),
        )
        zf.writestr(EXPORT_DATA_NAME, json.dumps([]))
    buffer.seek(0)
    upload = _FakeUpload(buffer.getvalue())

    with pytest.raises(SessionTransferError, match="payload is malformed"):
        await service.stage_import(upload)


# ---------------------------------------------------------------
# Zip-bomb guards: the limits are read from the archive's declared
# sizes, so a fake archive can drive the real arithmetic without
# materialising half a gigabyte.
# ---------------------------------------------------------------


class _FakeArchive:
    """Stand-in exposing only what ``_verify_zip_safety`` reads."""

    def __init__(self, infos):
        self._infos = infos

    def infolist(self):
        return self._infos


def _info(name: str, size: int) -> zipfile.ZipInfo:
    """Build a ZipInfo whose declared uncompressed size is ``size``."""
    info = zipfile.ZipInfo(name)
    info.file_size = size
    return info


def test_verify_zip_safety_rejects_oversized_entry():
    archive = _FakeArchive([_info("files/attachments/a.bin", MAX_ENTRY_BYTES + 1)])

    with pytest.raises(SessionTransferError, match="Entry too large"):
        SessionTransferService._verify_zip_safety(archive)


def test_verify_zip_safety_rejects_total_expansion():
    # Each entry is legal on its own; only their sum breaks the total limit.
    # Derive the count from the limits so the test cannot drift from them.
    entries_needed = MAX_TOTAL_UNCOMPRESSED_BYTES // MAX_ENTRY_BYTES + 1
    archive = _FakeArchive(
        [
            _info(f"files/attachments/{i}.bin", MAX_ENTRY_BYTES)
            for i in range(entries_needed)
        ]
    )

    with pytest.raises(SessionTransferError, match="size limit"):
        SessionTransferService._verify_zip_safety(archive)


def test_verify_zip_safety_accepts_entries_within_limits():
    archive = _FakeArchive(
        [
            _info("manifest.json", 128),
            _info("export.json", 4096),
            _info("files/attachments/a.bin", MAX_ENTRY_BYTES),
        ]
    )

    SessionTransferService._verify_zip_safety(archive)


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
