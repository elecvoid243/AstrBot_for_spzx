"""ChatUI session import: staging, safety guards and round-trip.

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

import json
import os
import time
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
    IMPORT_ID_TTL_SECONDS,
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


class _FakeDbTransaction:
    """Stand-in for ``AsyncSession.begin()``; can fail on exit (commit)."""

    def __init__(self, fail_commit=False):
        self._fail_commit = fail_commit

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        if self._fail_commit:
            raise RuntimeError("commit failed")
        return False


class _FakeDbSession:
    """Stand-in for the AsyncSession that ``_import_one_session`` writes to.

    Autoincrement ids are emulated for ``PlatformMessageHistory`` only:
    ``PlatformSession`` / ``Attachment`` / ``ConversationV2`` carry their own
    ``inner_*`` primary keys and reject a stray ``id`` (pydantic raises).
    """

    def __init__(self, added, fail_commit=False):
        self._added = added
        self._fail_commit = fail_commit

    async def flush(self):
        for index, row in enumerate(self._added):
            if row.__class__.__name__ != "PlatformMessageHistory":
                continue
            if row.id is None:
                row.id = 900 + index

    def add(self, row):
        self._added.append(row)

    def begin(self):
        return _FakeDbTransaction(self._fail_commit)


class _FakeDbContext:
    """Stand-in for ``db.get_db()``: one fresh session per call."""

    def __init__(self, added, fail_commit=False):
        self._added = added
        self._fail_commit = fail_commit

    async def __aenter__(self):
        return _FakeDbSession(self._added, self._fail_commit)

    async def __aexit__(self, *exc):
        return False


def _install_fake_db(service, added, fail_commit=False):
    """Route ``service.db.get_db`` into a session collecting rows into ``added``."""
    service.db.get_db = lambda: _FakeDbContext(added, fail_commit)


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
async def test_read_package_converts_escaped_archive_errors(tmp_path):
    """A corrupted archive must surface as SessionTransferError, never raw.

    zipfile's read path can raise zlib.error (bad deflate stream), EOFError
    (``_read2``) or NotImplementedError (unsupported compression / zip
    version) — none of which are OSError, so an enumerated except chain lets
    them escape as a generic 500 *and* skips the staged-file cleanup. Sweeping
    every byte of a real package asserts the normalisation holds for all three
    families and that a failing attempt never leaves a staged zip behind.
    """
    original = build_package_bytes().getvalue()
    saw_a_failure = False

    for offset in range(len(original)):
        corrupted = bytearray(original)
        corrupted[offset] ^= 0xFF
        # A fresh service per offset keeps each attempt's temp dir isolated,
        # so a leftover from one attempt cannot mask a leak in the next.
        service = _make_service()
        service.temp_dir = tmp_path / f"corrupt-{offset}"
        try:
            await service.stage_import(_FakeUpload(bytes(corrupted)))
        except SessionTransferError:
            saw_a_failure = True
            assert service.pending_imports == {}, f"pending leak at {offset}"
            assert not list(service.temp_dir.glob("session_import_*.zip")), (
                f"staged zip leaked on disk at offset {offset}"
            )
        except Exception as exc:  # noqa: BLE001 - the point of this test
            pytest.fail(f"raw {type(exc).__name__} escaped at offset {offset}: {exc!s}")

    assert saw_a_failure, "no corruption broke the package - test is vacuous"


def _seed_export_sessions():
    """One session with one history row, one attachment and one thread."""
    return [
        {
            "session": {
                "session_id": "sess-1",
                "display_name": "会话 A",
                "is_group": 0,
                "archived": 0,
                "created_at": "2026-09-01T00:00:00+00:00",
                "updated_at": "2026-09-01T00:00:00+00:00",
            },
            "conversations": [
                {
                    "conversation_id": "conv-1",
                    "platform_id": "webchat",
                    "user_id": "webchat:FriendMessage:webchat!alice!sess-1",
                    "content": [{"role": "user", "content": "hi"}],
                    "title": "t",
                    "persona_id": None,
                    "token_usage": 0,
                }
            ],
            "history": [
                {
                    "id": 11,
                    "sender_id": "alice",
                    "sender_name": "alice",
                    "content": {
                        "type": "user",
                        "message": [
                            {
                                "type": "image",
                                "attachment_id": "att-1",
                                "filename": "a.png",
                            }
                        ],
                    },
                    "llm_checkpoint_id": "ck-1",
                    "created_at": "2026-09-01T00:00:00+00:00",
                }
            ],
            "threads": [
                {
                    "thread": {
                        "thread_id": "thr-1",
                        "creator": "alice",
                        "parent_session_id": "sess-1",
                        "parent_message_id": 11,
                        "base_checkpoint_id": "ck-1",
                        "selected_text": "hi",
                    },
                    "history": [],
                    "conversations": [],
                }
            ],
            "preferences": [],
            "attachments": [
                {
                    "attachment_id": "att-1",
                    "type": "image",
                    "mime_type": "image/png",
                    "ext": ".png",
                    "size": 7,
                    "zip_path": "files/attachments/att-1.png",
                }
            ],
        }
    ]


@pytest.mark.asyncio
async def test_confirm_import_remaps_ids_and_ownership(tmp_path):
    service = _make_service()
    service.attachments_dir = tmp_path
    package = build_package_bytes(
        sessions=_seed_export_sessions(),
        extra_entries=[("files/attachments/att-1.png", b"PNGDATA")],
    )
    preview = await service.stage_import(_FakeUpload(package.getvalue()))

    added = []
    _install_fake_db(service, added)
    service.db.get_attachments = AsyncMock(return_value=[])
    service.db.get_platform_session_by_id = AsyncMock(return_value=None)

    result = await service.confirm_import("bob", preview["import_id"])

    assert result["errors"] == []
    assert len(result["created"]) == 1
    created = result["created"][0]
    assert created["display_name"] == "会话 A"
    assert created["new_session_id"] != "sess-1"

    sessions = [row for row in added if row.__class__.__name__ == "PlatformSession"]
    histories = [
        row for row in added if row.__class__.__name__ == "PlatformMessageHistory"
    ]
    threads = [row for row in added if row.__class__.__name__ == "WebChatThread"]
    assert sessions[0].creator == "bob"
    assert sessions[0].session_id == created["new_session_id"]
    assert histories[0].user_id == created["new_session_id"]
    assert histories[0].content["message"][0]["attachment_id"] == "att-1"
    assert threads[0].parent_session_id == created["new_session_id"]
    assert threads[0].parent_message_id == histories[0].id
    # The staged zip is always cleaned up.
    assert preview["import_id"] not in service.pending_imports


@pytest.mark.asyncio
async def test_confirm_import_reissues_colliding_attachment_id(tmp_path):
    service = _make_service()
    service.attachments_dir = tmp_path
    package = build_package_bytes(
        sessions=_seed_export_sessions(),
        extra_entries=[("files/attachments/att-1.png", b"PNGDATA")],
    )
    preview = await service.stage_import(_FakeUpload(package.getvalue()))

    service.db.get_attachments = AsyncMock(
        return_value=[SimpleNamespace(attachment_id="att-1")]
    )

    added = []
    _install_fake_db(service, added)
    service.db.get_platform_session_by_id = AsyncMock(return_value=None)

    await service.confirm_import("bob", preview["import_id"])

    attachments = [row for row in added if row.__class__.__name__ == "Attachment"]
    assert attachments[0].attachment_id != "att-1"
    assert attachments[0].path.endswith(f"{attachments[0].attachment_id}.png")
    histories = [
        row for row in added if row.__class__.__name__ == "PlatformMessageHistory"
    ]
    assert histories[0].content["message"][0]["attachment_id"] == (
        attachments[0].attachment_id
    )


@pytest.mark.asyncio
async def test_confirm_import_drops_thread_without_parent(tmp_path):
    service = _make_service()
    service.attachments_dir = tmp_path
    sessions = _seed_export_sessions()
    sessions[0]["history"] = []
    sessions[0]["threads"][0]["thread"]["parent_message_id"] = 11
    package = build_package_bytes(sessions=sessions)
    preview = await service.stage_import(_FakeUpload(package.getvalue()))

    added = []
    _install_fake_db(service, added)
    service.db.get_attachments = AsyncMock(return_value=[])

    result = await service.confirm_import("bob", preview["import_id"])

    # No parent history row was imported, so the thread must be dropped.
    assert result["errors"] == []
    assert not [row for row in added if row.__class__.__name__ == "WebChatThread"]
    assert any("parent message missing" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_confirm_import_ignores_a_session_whose_commit_fails(tmp_path):
    """A commit-time failure must never be reported as a created session.

    ``created.append`` runs only after ``begin()`` exits, so a session whose
    transaction rolled back is not handed to Tasks 5/8 as importable.
    """
    service = _make_service()
    service.attachments_dir = tmp_path
    package = build_package_bytes(sessions=_seed_export_sessions())
    preview = await service.stage_import(_FakeUpload(package.getvalue()))

    added = []
    _install_fake_db(service, added, fail_commit=True)
    service.db.get_attachments = AsyncMock(return_value=[])

    result = await service.confirm_import("bob", preview["import_id"])

    # The import itself ran (rows were staged), only the commit failed.
    assert any(row.__class__.__name__ == "PlatformSession" for row in added)
    assert result["created"] == []
    assert len(result["errors"]) == 1
    assert "commit failed" in result["errors"][0]


@pytest.mark.asyncio
async def test_confirm_import_reports_a_non_dict_session_entry(tmp_path):
    """A malformed element is reported, not raised out of the route.

    ``entry.get`` inside the error handler would raise ``AttributeError`` and
    escape as a 500, hiding the failure of the other sessions.
    """
    service = _make_service()
    service.attachments_dir = tmp_path
    package = build_package_bytes(sessions=[_EXPORTED_SESSION, "not-a-dict"])
    preview = await service.stage_import(_FakeUpload(package.getvalue()))
    assert preview["can_import"] is True

    added = []
    _install_fake_db(service, added)
    service.db.get_attachments = AsyncMock(return_value=[])

    result = await service.confirm_import("bob", preview["import_id"])

    assert len(result["created"]) == 1
    assert len(result["errors"]) == 1
    assert "Malformed session entry" in result["errors"][0]


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


@pytest.mark.asyncio
async def test_stage_import_sweeps_expired_pending_and_orphaned_zips():
    """An abandoned staged package is reclaimed on the next upload.

    The TTL only ran when the same ``import_id`` was looked up again, so a
    package the user never confirms kept its file (up to ``MAX_UPLOAD_BYTES``
    each) in the temp directory forever, with no bound on how many.
    """
    service = _make_service()
    stale_preview = await service.stage_import(
        _FakeUpload(build_package_bytes().getvalue())
    )
    stale_id = stale_preview["import_id"]
    stale_path = service.pending_imports[stale_id].zip_path
    service.pending_imports[stale_id].created_at -= IMPORT_ID_TTL_SECONDS + 60
    # An orphan no pending entry points at, older than the TTL.
    orphan = service.temp_dir / "session_import_orphan.zip"
    orphan.write_bytes(b"orphan")
    aged = time.time() - IMPORT_ID_TTL_SECONDS - 60
    os.utime(orphan, (aged, aged))

    preview = await service.stage_import(_FakeUpload(build_package_bytes().getvalue()))

    assert stale_id not in service.pending_imports
    assert not stale_path.exists()
    assert not orphan.exists()
    # The package staged by this very call is untouched.
    assert set(service.pending_imports) == {preview["import_id"]}
    assert service.pending_imports[preview["import_id"]].zip_path.exists()


@pytest.mark.asyncio
async def test_sweep_keeps_fresh_orphans_and_live_pending():
    """A concurrent upload mid-write, and a live preview, must survive.

    The fresh orphan stands for an upload another request is still writing.
    The aged live file proves a registered package is protected by the pending
    map itself, not by its mtime.
    """
    service = _make_service()
    live_preview = await service.stage_import(
        _FakeUpload(build_package_bytes().getvalue())
    )
    live_path = service.pending_imports[live_preview["import_id"]].zip_path
    aged = time.time() - IMPORT_ID_TTL_SECONDS - 60
    os.utime(live_path, (aged, aged))
    fresh_orphan = service.temp_dir / "session_import_fresh.zip"
    fresh_orphan.write_bytes(b"mid-write")

    preview = await service.stage_import(_FakeUpload(build_package_bytes().getvalue()))

    assert live_preview["import_id"] in service.pending_imports
    assert live_path.exists()
    assert fresh_orphan.exists()
    assert preview["import_id"] in service.pending_imports
