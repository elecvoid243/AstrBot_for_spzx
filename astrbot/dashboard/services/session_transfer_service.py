"""ChatUI session export/import (cross-user migration).

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import posixpath
import re
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from astrbot import logger
from astrbot.core.config.default import VERSION
from astrbot.core.db import po as db_po
from astrbot.core.platform.message_type import MessageType
from astrbot.core.utils.astrbot_path import (
    get_astrbot_data_path,
    get_astrbot_temp_path,
)
from astrbot.core.utils.datetime_utils import to_utc_isoformat
from astrbot.core.utils.version_comparator import VersionComparator
from astrbot.dashboard.services.chat_service import extract_attachment_ids

EXPORT_KIND = "astrbot-chatui-export"
EXPORT_FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
EXPORT_DATA_NAME = "export.json"
ATTACHMENTS_PREFIX = "files/attachments/"
WEBCHAT_PLATFORM_ID = "webchat"
THREAD_PLATFORM_ID = "webchat_thread"

# Preferences whose stored value is a conversation id. When one of these is
# imported, a pointer that cannot be mapped onto a conversation the import
# created must be dropped rather than left aiming at a foreign/dead row.
CONVERSATION_POINTER_PREFERENCE_KEYS = frozenset({"sel_conv_id"})

# Import guards (zip bomb / oversized upload defence).
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_ZIP_ENTRIES = 20000
MAX_ENTRY_BYTES = 512 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
IMPORT_ID_TTL_SECONDS = 1800

# Export reads one session in a single page; matches the existing
# `page_size=100000` usage in chat_service.py.
HISTORY_PAGE_SIZE = 100000

# Attachment streaming chunk size: one chunk (never a whole file) is in RAM at
# a time while the archive is written.
EXPORT_CHUNK_BYTES = 1024 * 1024

_ATTACHMENT_EXT_PATTERN = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


def new_umo(platform_id: str, is_group: int, creator: str, entity_id: str) -> str:
    """Rebuild a unified message origin for an imported session or thread.

    Args:
        platform_id: Platform identifier, always ``webchat`` for ChatUI.
        is_group: 1 for group sessions, 0 for private ones.
        creator: Owner of the imported entity (the importing user).
        entity_id: New session id or thread id.

    Returns:
        A UMO in the ``{platform}:{type}:{platform}!{creator}!{id}`` shape
        produced by ``build_webchat_unified_msg_origin``.
    """
    message_type = (
        MessageType.GROUP_MESSAGE.value
        if is_group
        else MessageType.FRIEND_MESSAGE.value
    )
    return f"{platform_id}:{message_type}:{platform_id}!{creator}!{entity_id}"


def build_attachment_id_map(
    old_ids: list[str],
    existing_ids: set[str],
    uuid_factory=uuid.uuid4,
) -> dict[str, str]:
    """Map exported attachment IDs onto IDs that are free in the target DB.

    Args:
        old_ids: Attachment IDs referenced by the export package.
        existing_ids: Attachment IDs already present in the target database.
        uuid_factory: Callable returning a fresh unique id; injectable for tests.

    Returns:
        Mapping from exported ID to the ID to use on import. Exported IDs
        that are still free are reused verbatim; colliding ones get a new id.
    """
    mapping: dict[str, str] = {}
    for old_id in old_ids:
        if not old_id or old_id in mapping:
            continue
        mapping[old_id] = old_id if old_id not in existing_ids else str(uuid_factory())
    return mapping


def rewrite_attachment_refs(content: dict, id_map: dict[str, str]) -> dict:
    """Replace ``attachment_id`` fields inside a history content payload.

    Args:
        content: Stored platform history content (``{"type", "message"}``).
        id_map: Mapping from exported attachment ID to the imported one.

    Returns:
        A copy of ``content`` with mapped attachment IDs. The input is never
        mutated. Non-dict content and payloads without a parts list pass
        through unchanged.
    """
    if not isinstance(content, dict):
        return content
    parts = content.get("message")
    if not isinstance(parts, list):
        return content
    rewritten: list = []
    for part in parts:
        if isinstance(part, dict) and part.get("attachment_id") in id_map:
            part = {**part, "attachment_id": id_map[part["attachment_id"]]}
        rewritten.append(part)
    return {**content, "message": rewritten}


def resolve_parent_message_id(old_id: int, id_map: dict[int, int]) -> int | None:
    """Look up the imported message id for a thread's parent message.

    Args:
        old_id: ``parent_message_id`` recorded in the export package.
        id_map: Mapping from exported history id to imported history id.

    Returns:
        The imported message id, or ``None`` when the parent was not imported
        (the thread must then be dropped with a warning).
    """
    return id_map.get(old_id)


def is_safe_attachment_zip_path(zip_path: str) -> bool:
    """Check a manifest-declared zip path cannot escape the attachments dir.

    Args:
        zip_path: Path recorded in the export manifest.

    Returns:
        True when the path is a normalized ``files/attachments/...`` path
        without traversal segments or Windows separators.
    """
    if not isinstance(zip_path, str) or not zip_path:
        return False
    if ".." in zip_path or "\\" in zip_path:
        return False
    if not zip_path.startswith(ATTACHMENTS_PREFIX):
        return False
    return posixpath.normpath(zip_path) == zip_path


def is_safe_attachment_ext(ext: str) -> bool:
    """Check an attachment suffix is safe to append to a generated filename.

    Args:
        ext: Suffix such as ``.png`` taken from the export package.

    Returns:
        True when the suffix matches ``^\\.[A-Za-z0-9]{1,10}$``.
    """
    return bool(_ATTACHMENT_EXT_PATTERN.fullmatch(ext or ""))


class SessionTransferError(Exception):
    """User-visible session export/import failure."""


@dataclass
class SessionExport:
    """A packaged session export ready to stream to the browser."""

    path: Path
    filename: str
    mimetype: str = "application/zip"


@dataclass
class _PendingImport:
    """A staged, pre-checked import zip awaiting confirmation."""

    zip_path: Path
    manifest: dict
    payload: dict
    preview: dict
    created_at: float


class SessionTransferService:
    """Export and import single ChatUI (webchat) sessions as zip packages."""

    def __init__(self, db, core_lifecycle) -> None:
        """Bind the service to the database and core lifecycle.

        Args:
            db: BaseDatabase instance used for direct row access.
            core_lifecycle: Provides the conversation and platform history
                managers shared with the rest of the dashboard.
        """
        self.db = db
        self.core_lifecycle = core_lifecycle
        self.conv_mgr = core_lifecycle.conversation_manager
        self.platform_history_mgr = core_lifecycle.platform_message_history_manager
        self.attachments_dir = Path(get_astrbot_data_path()) / "attachments"
        self.temp_dir = Path(get_astrbot_temp_path())
        self.pending_imports: dict[str, _PendingImport] = {}

    @staticmethod
    def _serialize_row(row) -> dict:
        """Serialize a SQLModel row, normalizing datetimes to UTC ISO strings.

        Args:
            row: A PlatformSession / PlatformMessageHistory / ConversationV2 /
                WebChatThread / Preference instance.

        Returns:
            Dict of the row's columns with ``created_at`` / ``updated_at``
            converted to UTC ISO strings when present.
        """
        data = {
            key: value for key, value in row.model_dump().items() if key != "inner_id"
        }
        for key in ("created_at", "updated_at"):
            if key in data:
                data[key] = to_utc_isoformat(data[key])
        return data

    async def _owned_webchat_session(self, username: str, session_id: str):
        """Load a webchat session owned by ``username``.

        Args:
            username: Authenticated dashboard user.
            session_id: ChatUI session identifier.

        Returns:
            The PlatformSession row.

        Raises:
            SessionTransferError: If the session is missing, owned by another
                user, or not a webchat session.
        """
        session = await self.db.get_platform_session_by_id(session_id)
        if not session:
            raise SessionTransferError(f"Session {session_id} not found")
        if session.creator != username:
            raise SessionTransferError("Permission denied")
        if session.platform_id != WEBCHAT_PLATFORM_ID:
            raise SessionTransferError(
                "Only webchat sessions can be exported or imported"
            )
        return session

    async def _session_conversations(self, umo: str) -> list[dict]:
        """Collect every LLM conversation row belonging to a UMO.

        Args:
            umo: Unified message origin of the session or thread.

        Returns:
            Serialized ConversationV2 rows including their ``content``.
        """
        rows = await self.db.get_conversations(user_id=umo)
        collected: list[dict] = []
        for row in rows:
            full = await self.db.get_conversation_by_id(row.conversation_id)
            if full is not None:
                collected.append(self._serialize_row(full))
        return collected

    async def export_session(self, username: str, session_id: str) -> SessionExport:
        """Package one ChatUI session into a zip export.

        Args:
            username: Authenticated dashboard user; must own the session.
            session_id: ChatUI session identifier.

        Returns:
            SessionExport pointing at the temporary archive on disk, plus the
            download filename and mimetype.

        Raises:
            SessionTransferError: If the session is missing, owned by another
                user, or not a webchat session.
        """
        session = await self._owned_webchat_session(username, session_id)
        session_umo = new_umo(
            session.platform_id, session.is_group, session.creator, session_id
        )

        history = await self.platform_history_mgr.get(
            platform_id=WEBCHAT_PLATFORM_ID,
            user_id=session_id,
            page=1,
            page_size=HISTORY_PAGE_SIZE,
        )
        threads = await self.db.get_webchat_threads_by_parent_session(
            parent_session_id=session_id,
            creator=username,
        )

        warnings: list[str] = []
        thread_payloads: list[dict] = []
        thread_attachment_ids: list[str] = []
        umos = [session_umo]
        for thread in threads:
            thread_umo = new_umo(
                session.platform_id, 0, session.creator, thread.thread_id
            )
            umos.append(thread_umo)
            thread_history = await self.platform_history_mgr.get(
                platform_id=THREAD_PLATFORM_ID,
                user_id=thread.thread_id,
                page=1,
                page_size=HISTORY_PAGE_SIZE,
            )
            thread_attachment_ids.extend(extract_attachment_ids(thread_history))
            thread_payloads.append(
                {
                    "thread": self._serialize_row(thread),
                    "history": [self._serialize_row(row) for row in thread_history],
                    "conversations": await self._session_conversations(thread_umo),
                }
            )

        preferences: list[dict] = []
        for umo in umos:
            rows = await self.db.get_preferences(scope="umo", scope_id=umo)
            preferences.extend(self._serialize_row(row) for row in rows)

        # Attachments hang off the main stream *and* off side threads: both
        # persist the same message-part shape, so scanning `history` alone
        # would silently drop thread images from the package (no warning,
        # no bytes) and leave them permanently broken after a migration.
        all_attachment_ids = extract_attachment_ids(history) + thread_attachment_ids

        attachments: list[dict] = []
        seen_ids: set[str] = set()
        for attachment_id in all_attachment_ids:
            if attachment_id in seen_ids:
                continue
            seen_ids.add(attachment_id)
            attachment = await self.db.get_attachment_by_id(attachment_id)
            if attachment is None:
                warnings.append(f"Attachment {attachment_id} metadata missing")
                continue
            source = Path(attachment.path)
            ext = source.suffix if is_safe_attachment_ext(source.suffix) else ""
            attachments.append(
                {
                    "attachment_id": attachment_id,
                    "type": attachment.type,
                    "mime_type": attachment.mime_type,
                    "ext": ext,
                    "size": 0,
                    "zip_path": f"{ATTACHMENTS_PREFIX}{attachment_id}{ext}",
                    # Path-only: the archive writer streams from disk so the
                    # bytes never sit in RAM and deflate stays off the loop.
                    "source_path": str(source),
                }
            )

        data_payload = {
            "sessions": [
                {
                    "session": self._serialize_row(session),
                    "conversations": await self._session_conversations(session_umo),
                    "history": [self._serialize_row(row) for row in history],
                    "threads": thread_payloads,
                    "preferences": preferences,
                    "attachments": attachments,
                }
            ]
        }
        data_bytes = json.dumps(data_payload, ensure_ascii=False).encode("utf-8")

        manifest_sessions = [
            {
                "original_session_id": session.session_id,
                "display_name": session.display_name,
                "original_creator": session.creator,
                "stats": {
                    "messages": len(history),
                    "conversations": len(data_payload["sessions"][0]["conversations"]),
                    "threads": len(thread_payloads),
                    "attachments": len(attachments),
                    "attachment_bytes": 0,  # patched by the writer, see below
                },
            }
        ]

        self.temp_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self.temp_dir / f"session_export_{uuid.uuid4()}.zip"
        try:
            warnings = await asyncio.to_thread(
                self._build_export_archive,
                archive_path,
                data_bytes,
                manifest_sessions,
                attachments,
                warnings,
            )
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise

        # A package larger than the import cap can never be imported by anyone,
        # so refuse it here rather than handing the user a dead archive.
        archive_bytes = archive_path.stat().st_size
        if archive_bytes > MAX_UPLOAD_BYTES:
            archive_path.unlink(missing_ok=True)
            raise SessionTransferError(
                f"Session export is {archive_bytes} bytes, which exceeds the "
                f"{MAX_UPLOAD_BYTES} byte import limit; the package could never "
                "be imported. Remove some attachments and try again."
            )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return SessionExport(
            path=archive_path,
            filename=f"astrbot_chatui_export_{timestamp}.zip",
        )

    def _build_export_archive(
        self,
        archive_path: Path,
        data_bytes: bytes,
        manifest_sessions: list[dict],
        attachment_entries: list[dict],
        base_warnings: list[str],
    ) -> list[str]:
        """Write the export zip, streaming attachments from disk.

        Runs in a worker thread: the deflate work must not block the event
        loop, and streaming keeps peak memory at one chunk per attachment.

        Args:
            archive_path: Destination zip path in the service temp directory.
            data_bytes: Serialized ``export.json`` payload.
            manifest_sessions: Per-session manifest entries (stats are patched
                in place as attachments are written).
            attachment_entries: Attachment metadata dicts carrying an internal
                ``source_path`` key.
            base_warnings: Warnings collected before packaging.

        Returns:
            The final warning list (base warnings plus per-file problems).
        """
        warnings = list(base_warnings)
        checksums = {
            EXPORT_DATA_NAME: f"sha256:{hashlib.sha256(data_bytes).hexdigest()}"
        }
        written_bytes = 0

        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(EXPORT_DATA_NAME, data_bytes)
            for entry in attachment_entries:
                source = Path(entry.pop("source_path"))
                zip_path = entry["zip_path"]
                attachment_id = entry["attachment_id"]
                if not source.is_file():
                    warnings.append(f"Attachment {attachment_id} file missing on disk")
                    continue
                hasher = hashlib.sha256()
                size = 0
                try:
                    with source.open("rb") as src, zf.open(zip_path, "w") as dest:
                        while chunk := src.read(EXPORT_CHUNK_BYTES):
                            hasher.update(chunk)
                            dest.write(chunk)
                            size += len(chunk)
                except OSError as exc:
                    # The entry may be truncated: a mid-stream disk error cannot
                    # be undone once the local header is written. The importer
                    # treats an unreadable blob as metadata-only with a warning,
                    # so this degrades rather than corrupting the package.
                    warnings.append(f"Attachment {attachment_id} unreadable: {exc!s}")
                    continue
                entry["size"] = size
                written_bytes += size
                checksums[zip_path] = f"sha256:{hasher.hexdigest()}"

            manifest_sessions[0]["stats"]["attachment_bytes"] = written_bytes
            manifest = {
                "kind": EXPORT_KIND,
                "format_version": EXPORT_FORMAT_VERSION,
                "astrbot_version": VERSION,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "sessions": manifest_sessions,
                "warnings": warnings,
                "checksums": checksums,
            }
            zf.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        return warnings

    def _drop_pending(self, import_id: str) -> None:
        """Forget a staged import and delete its temporary zip.

        Args:
            import_id: Identifier returned by ``stage_import``.
        """
        pending = self.pending_imports.pop(import_id, None)
        if pending is None:
            return
        try:
            pending.zip_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"Failed to delete staged import {import_id}: {exc!s}")

    def _sweep_stale_imports(self) -> None:
        """Drop expired staged imports and orphaned staged archives.

        The TTL is otherwise only enforced when the same ``import_id`` is
        looked up again, so a package the user never confirms would sit in the
        temp directory forever (up to ``MAX_UPLOAD_BYTES`` each, unbounded
        count). A file that is still registered stays untouched; an orphan
        younger than the TTL is left alone on purpose, because a concurrent
        upload may be mid-write.
        """
        for import_id in [
            key
            for key, pending in self.pending_imports.items()
            if time.monotonic() - pending.created_at > IMPORT_ID_TTL_SECONDS
        ]:
            self._drop_pending(import_id)

        try:
            candidates = list(self.temp_dir.glob("session_import_*.zip"))
        except OSError as exc:
            logger.warning(f"Failed to scan staged imports: {exc!s}")
            return

        live = {pending.zip_path for pending in self.pending_imports.values()}
        cutoff = time.time() - IMPORT_ID_TTL_SECONDS
        for path in candidates:
            if path in live:
                continue
            try:
                if path.stat().st_mtime > cutoff:
                    continue
                path.unlink(missing_ok=True)
                logger.info(f"Removed orphaned staged import {path.name}")
            except OSError as exc:
                logger.warning(
                    f"Failed to remove orphaned staged import {path}: {exc!s}"
                )

    def _pending(self, import_id: str) -> _PendingImport:
        """Look up a staged import, enforcing the TTL.

        Args:
            import_id: Identifier returned by ``stage_import``.

        Returns:
            The staged import record.

        Raises:
            SessionTransferError: If the id is unknown or has expired.
        """
        pending = self.pending_imports.get(import_id)
        if pending is None:
            raise SessionTransferError("Import preview expired, please upload again")
        if time.monotonic() - pending.created_at > IMPORT_ID_TTL_SECONDS:
            self._drop_pending(import_id)
            raise SessionTransferError("Import preview expired, please upload again")
        return pending

    @staticmethod
    def _verify_zip_safety(archive) -> None:
        """Reject archives that could exhaust disk or expand beyond limits.

        Args:
            archive: Open zip archive, or any object exposing ``infolist()``
                (tests drive this with a duck-typed stand-in).

        Raises:
            SessionTransferError: If the entry count or uncompressed size
                exceeds the configured limits.
        """
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise SessionTransferError(
                f"Package has too many entries (> {MAX_ZIP_ENTRIES})"
            )
        total = 0
        for info in infos:
            if info.file_size > MAX_ENTRY_BYTES:
                raise SessionTransferError(f"Entry too large: {info.filename}")
            total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise SessionTransferError("Package expands beyond the size limit")

    def _read_package(self, zip_path: Path) -> tuple[dict, dict]:
        """Read and validate a staged import zip.

        Args:
            zip_path: Path of the temporary zip on disk.

        Returns:
            ``(manifest, payload)`` parsed from the archive.

        Raises:
            SessionTransferError: If the zip is malformed, of an unexpected
                kind, or of an unrecognised ``format_version``.
        """
        try:
            with zipfile.ZipFile(zip_path) as zf:
                self._verify_zip_safety(zf)
                names = set(zf.namelist())
                if MANIFEST_NAME not in names or EXPORT_DATA_NAME not in names:
                    raise SessionTransferError("Package is missing required entries")
                manifest = json.loads(zf.read(MANIFEST_NAME))
                payload = json.loads(zf.read(EXPORT_DATA_NAME))
        except zipfile.BadZipFile as exc:
            raise SessionTransferError("Uploaded file is not a valid zip") from exc
        except json.JSONDecodeError as exc:
            raise SessionTransferError("Package metadata is not valid JSON") from exc
        except SessionTransferError:
            raise
        except Exception as exc:
            # A corrupted archive escapes the two classes above in three other
            # ways (zlib.error on a bad deflate stream, EOFError from
            # zipfile._read2, NotImplementedError for an unsupported
            # compression method / zip version). Convert at this boundary so
            # the service keeps its error contract and the caller's cleanup
            # runs, instead of a generic 500 plus a leaked staged file.
            raise SessionTransferError(
                f"Package archive is unreadable: {exc!s}"
            ) from exc

        if not isinstance(manifest, dict) or manifest.get("kind") != EXPORT_KIND:
            raise SessionTransferError("Unsupported package: wrong kind")
        if manifest.get("format_version") != EXPORT_FORMAT_VERSION:
            raise SessionTransferError(
                f"Unsupported package format_version: {manifest.get('format_version')}"
            )
        # The data contract is `format_version` (enforced above). The
        # exporter's AstrBot version is informational: cross-instance
        # migration routinely crosses minor releases, so a version
        # difference becomes a warning the dialog renders, not a rejection.
        if not isinstance(manifest.get("astrbot_version"), str):
            raise SessionTransferError("Package is missing astrbot_version")
        if not isinstance(payload, dict) or not isinstance(
            payload.get("sessions"), list
        ):
            raise SessionTransferError("Package payload is malformed")
        return manifest, payload

    async def stage_import(self, upload) -> dict:
        """Persist an uploaded package and return its pre-check preview.

        Args:
            upload: UploadFileAdapter carrying the export zip.

        Returns:
            Preview dict with ``import_id``, per-session stats, the version
            status and whether the package can be imported.

        Raises:
            SessionTransferError: If the upload is missing, too large, or
                fails any package validation.
        """
        if upload is None or not getattr(upload, "filename", None):
            raise SessionTransferError("Missing key: file")
        if upload.content_length and upload.content_length > MAX_UPLOAD_BYTES:
            raise SessionTransferError("Uploaded package is too large")

        self._sweep_stale_imports()

        import_id = str(uuid.uuid4())
        zip_path = self.temp_dir / f"session_import_{import_id}.zip"
        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            await upload.save(str(zip_path))
            if zip_path.stat().st_size > MAX_UPLOAD_BYTES:
                raise SessionTransferError("Uploaded package is too large")
            manifest, payload = self._read_package(zip_path)
        except Exception as exc:
            # Cleanup is unconditional and the error family is normalised here
            # rather than enumerated: any non-service failure must still become
            # a SessionTransferError so the route maps it, and must still
            # remove the staged file so nothing is left untracked in data/temp.
            zip_path.unlink(missing_ok=True)
            if isinstance(exc, SessionTransferError):
                raise
            raise SessionTransferError(f"Failed to stage upload: {exc!s}") from exc

        warnings = list(manifest.get("warnings") or [])
        sessions = []
        for entry in manifest.get("sessions") or []:
            sessions.append(
                {
                    "display_name": entry.get("display_name"),
                    "original_creator": entry.get("original_creator"),
                    "stats": entry.get("stats") or {},
                }
            )
        exported_version = str(manifest.get("astrbot_version") or "")
        version_cmp = (
            VersionComparator.compare_version(exported_version, VERSION)
            if exported_version
            else None
        )
        if not exported_version:
            warnings.append("Package does not declare an AstrBot version")
        elif version_cmp != 0:
            warnings.append(
                f"Package was exported from AstrBot {exported_version}, "
                f"current version is {VERSION}"
            )
        # `compatible` is a genuine constant, not a computed verdict: the
        # schema contract (`format_version`) already passed `_read_package`.
        version_status = {
            "compatible": True,
            "package_version": exported_version,
            "current_version": VERSION,
            "upgrade_advised": version_cmp is not None and version_cmp != 0,
        }
        preview = {
            "import_id": import_id,
            "sessions": sessions,
            "version_status": version_status,
            "can_import": bool(payload.get("sessions")),
            "warnings": warnings,
        }
        self.pending_imports[import_id] = _PendingImport(
            zip_path=zip_path,
            manifest=manifest,
            payload=payload,
            preview=preview,
            created_at=time.monotonic(),
        )
        return preview

    @staticmethod
    def _read_attachment_blob(archive_path: Path, entry_path: str) -> bytes | None:
        """Read one attachment blob from the staged import zip.

        Args:
            archive_path: Path of the staged import zip.
            entry_path: Manifest-declared path inside the archive.

        Returns:
            The file bytes, or None when the entry is absent.
        """
        with zipfile.ZipFile(archive_path) as zf:
            try:
                return zf.read(entry_path)
            except KeyError:
                return None

    async def _import_one_session(
        self,
        dbsession,
        entry: dict,
        username: str,
        warnings: list[str],
        created_files: list[Path],
        archive_path: Path,
    ) -> dict:
        """Import one exported session inside the caller's transaction.

        Args:
            dbsession: AsyncSession already inside a ``begin()`` block.
            entry: One element of ``export.json["sessions"]``.
            username: Importing dashboard user; becomes the new creator.
            warnings: Mutable list collecting user-visible warnings.
            created_files: Mutable list receiving every file written to disk,
                so the caller can delete them when the transaction fails.
            archive_path: Path of the staged import zip holding the blobs.

        Returns:
            ``{"new_session_id", "display_name"}`` for the imported session.
        """
        session_data = entry.get("session") or {}
        is_group = int(session_data.get("is_group") or 0)
        new_session_id = str(uuid.uuid4())
        session_umo = new_umo(WEBCHAT_PLATFORM_ID, is_group, username, new_session_id)
        # Exported preference rows carry the ORIGINAL scope ids, so the import
        # needs the old-UMO -> new-UMO mapping to place each row correctly.
        original_creator = session_data.get("creator") or username
        umo_map = {
            new_umo(
                WEBCHAT_PLATFORM_ID,
                is_group,
                original_creator,
                session_data.get("session_id") or "",
            ): session_umo
        }

        new_session = db_po.PlatformSession(
            session_id=new_session_id,
            platform_id=WEBCHAT_PLATFORM_ID,
            creator=username,
            display_name=session_data.get("display_name"),
            is_group=is_group,
            archived=0,
        )
        dbsession.add(new_session)

        # Attachments: reissue colliding ids, then write files and rows.
        exported_attachments = entry.get("attachments") or []
        old_attachment_ids = [
            item.get("attachment_id")
            for item in exported_attachments
            if item.get("attachment_id")
        ]
        existing = await self.db.get_attachments(old_attachment_ids)
        attachment_map = build_attachment_id_map(
            old_attachment_ids, {row.attachment_id for row in existing}
        )
        for item in exported_attachments:
            old_id = item.get("attachment_id")
            new_id = attachment_map.get(old_id)
            if not new_id:
                continue
            ext = item.get("ext") or ""
            # An empty suffix is legitimate: the exporter could not derive a
            # safe one, so the target file is simply "{new_id}" with no
            # suffix. Only a *provided* suffix must match the strict pattern.
            if ext and not is_safe_attachment_ext(ext):
                warnings.append(f"Attachment {old_id}: skipped unsafe suffix {ext!r}")
                continue
            zip_path = item.get("zip_path") or ""
            if not is_safe_attachment_zip_path(zip_path):
                warnings.append(
                    f"Attachment {old_id}: skipped unsafe path {zip_path!r}"
                )
                continue
            target = self.attachments_dir / f"{new_id}{ext}"
            blob = self._read_attachment_blob(archive_path, zip_path)
            if blob is None:
                warnings.append(f"Attachment {old_id}: file missing in package")
                continue
            await asyncio.to_thread(target.write_bytes, blob)
            dbsession.add(
                db_po.Attachment(
                    attachment_id=new_id,
                    path=str(target),
                    type=item.get("type") or "file",
                    mime_type=item.get("mime_type") or "application/octet-stream",
                )
            )
            created_files.append(target)

        # LLM conversations (session + threads share the same shape). The
        # exported id -> new id map matters beyond the conversation rows:
        # the runtime's `sel_conv_id` preference still points at the exported
        # id and must be rewritten with it.
        conversation_id_map: dict[str, str] = {}
        exported_conversation_ids: set[str] = set()
        for conversation in entry.get("conversations") or []:
            old_conversation_id = conversation.get("conversation_id")
            new_conversation_id = str(uuid.uuid4())
            if old_conversation_id:
                conversation_id_map[old_conversation_id] = new_conversation_id
                exported_conversation_ids.add(old_conversation_id)
            dbsession.add(
                db_po.ConversationV2(
                    conversation_id=new_conversation_id,
                    platform_id=WEBCHAT_PLATFORM_ID,
                    user_id=session_umo,
                    content=conversation.get("content") or [],
                    title=conversation.get("title"),
                    persona_id=conversation.get("persona_id"),
                    token_usage=int(conversation.get("token_usage") or 0),
                )
            )

        # Main history, remembering old -> new ids for thread backfill.
        history_rows = []
        for record in entry.get("history") or []:
            row = db_po.PlatformMessageHistory(
                platform_id=WEBCHAT_PLATFORM_ID,
                user_id=new_session_id,
                content=rewrite_attachment_refs(
                    record.get("content") or {}, attachment_map
                ),
                sender_id=record.get("sender_id"),
                sender_name=record.get("sender_name"),
                llm_checkpoint_id=record.get("llm_checkpoint_id"),
            )
            dbsession.add(row)
            history_rows.append((record.get("id"), row))
        await dbsession.flush()
        history_id_map = {
            old_id: row.id for old_id, row in history_rows if old_id is not None
        }

        # Threads: drop the ones whose parent message was not imported.
        for thread_entry in entry.get("threads") or []:
            thread_data = thread_entry.get("thread") or {}
            # Record the thread's exported conversation ids even when the
            # thread is dropped: a preference still pointing at one of them is
            # a dead pointer and must be skipped, not repointed at a foreign id.
            for conversation in thread_entry.get("conversations") or []:
                old_conversation_id = conversation.get("conversation_id")
                if old_conversation_id:
                    exported_conversation_ids.add(old_conversation_id)
            parent_id = resolve_parent_message_id(
                thread_data.get("parent_message_id"), history_id_map
            )
            if parent_id is None:
                warnings.append(
                    f"Thread {thread_data.get('thread_id')}: parent message missing"
                )
                continue
            new_thread_id = str(uuid.uuid4())
            dbsession.add(
                db_po.WebChatThread(
                    thread_id=new_thread_id,
                    creator=username,
                    parent_session_id=new_session_id,
                    parent_message_id=parent_id,
                    base_checkpoint_id=thread_data.get("base_checkpoint_id") or "",
                    selected_text=thread_data.get("selected_text") or "",
                )
            )
            thread_umo = new_umo(WEBCHAT_PLATFORM_ID, 0, username, new_thread_id)
            umo_map[
                new_umo(
                    WEBCHAT_PLATFORM_ID,
                    0,
                    original_creator,
                    thread_data.get("thread_id") or "",
                )
            ] = thread_umo
            for conversation in thread_entry.get("conversations") or []:
                old_conversation_id = conversation.get("conversation_id")
                new_conversation_id = str(uuid.uuid4())
                if old_conversation_id:
                    conversation_id_map[old_conversation_id] = new_conversation_id
                dbsession.add(
                    db_po.ConversationV2(
                        conversation_id=new_conversation_id,
                        platform_id=WEBCHAT_PLATFORM_ID,
                        user_id=thread_umo,
                        content=conversation.get("content") or [],
                        title=conversation.get("title"),
                        persona_id=conversation.get("persona_id"),
                        token_usage=int(conversation.get("token_usage") or 0),
                    )
                )
            for record in thread_entry.get("history") or []:
                dbsession.add(
                    db_po.PlatformMessageHistory(
                        platform_id=THREAD_PLATFORM_ID,
                        user_id=new_thread_id,
                        content=rewrite_attachment_refs(
                            record.get("content") or {}, attachment_map
                        ),
                        sender_id=record.get("sender_id"),
                        sender_name=record.get("sender_name"),
                        llm_checkpoint_id=record.get("llm_checkpoint_id"),
                    )
                )

        # Preferences follow their original scope into the new account.
        # The exporter collects rows for the session UMO *and* every thread
        # UMO (spec §5.1), so each row must be remapped through the UMO it
        # actually belonged to — collapsing them all onto the session UMO
        # would misassign thread-scoped config AND could collide on
        # (scope, scope_id, key), rolling back the whole session.
        for preference in entry.get("preferences") or []:
            exported_scope_id = preference.get("scope_id") or ""
            # A scope the import did not reissue belongs to an entity that was
            # dropped (e.g. a thread whose parent message was missing).
            # Re-homing its rows onto the session UMO would collide on
            # (scope, scope_id, key) and roll back the entire session.
            if exported_scope_id and exported_scope_id not in umo_map:
                warnings.append(
                    f"Preference {preference.get('key')!r}: scope "
                    f"{exported_scope_id} was not imported"
                )
                continue
            key = preference.get("key") or ""
            value = preference.get("value") or {}
            if isinstance(value, dict) and isinstance(value.get("val"), str):
                pointed_conversation_id = value["val"]
                if pointed_conversation_id in conversation_id_map:
                    value = {
                        **value,
                        "val": conversation_id_map[pointed_conversation_id],
                    }
                elif pointed_conversation_id and (
                    pointed_conversation_id in exported_conversation_ids
                    or key in CONVERSATION_POINTER_PREFERENCE_KEYS
                ):
                    # The pointed conversation exists in the package but was
                    # not imported, or the key only ever holds a conversation
                    # id and that id is foreign. Either way the pointer would
                    # be dead: skip the row rather than leave it dangling.
                    warnings.append(
                        f"Preference {key!r}: conversation "
                        f"{pointed_conversation_id} was not imported"
                    )
                    continue
            dbsession.add(
                db_po.Preference(
                    scope=preference.get("scope") or "umo",
                    scope_id=umo_map.get(exported_scope_id, session_umo),
                    key=key,
                    value=value,
                )
            )

        return {
            "new_session_id": new_session_id,
            "display_name": session_data.get("display_name"),
        }

    async def confirm_import(self, username: str, import_id: str) -> dict:
        """Import every session of a staged package into ``username``'s account.

        Args:
            username: Authenticated dashboard user receiving the sessions.
            import_id: Identifier returned by ``stage_import``.

        Returns:
            ``{"created", "warnings", "errors"}`` where ``created`` lists the
            new session ids and display names.

        Raises:
            SessionTransferError: If the staged package expired, is no longer
                importable, or its files changed since pre-check.
        """
        pending = self._pending(import_id)
        if not pending.preview.get("can_import"):
            self._drop_pending(import_id)
            raise SessionTransferError("Package contains no importable sessions")
        try:
            # Re-validate: the staged zip may have changed since pre-check.
            manifest, payload = self._read_package(pending.zip_path)
        except SessionTransferError:
            # Never leave a staged package behind on a failure path.
            self._drop_pending(import_id)
            raise
        pending.manifest, pending.payload = manifest, payload

        created: list[dict] = []
        warnings: list[str] = []
        errors: list[str] = []
        created_files: list[Path] = []
        try:
            for entry in pending.payload.get("sessions") or []:
                mark = len(created_files)
                try:
                    if not isinstance(entry, dict):
                        raise SessionTransferError("Malformed session entry")
                    async with self.db.get_db() as dbsession:
                        async with dbsession.begin():
                            imported = await self._import_one_session(
                                dbsession,
                                entry,
                                username,
                                warnings,
                                created_files,
                                pending.zip_path,
                            )
                    # Appended only after `begin()` exits: a failure at
                    # commit time must never leave a phantom "created"
                    # session that Tasks 5/8 would render or link.
                    created.append(imported)
                except Exception as exc:
                    logger.error(f"Session import failed: {exc!s}", exc_info=True)
                    # The entry may not even be a dict: reporting the failure
                    # must not raise a second time and escape as a 500.
                    entry_session = (
                        entry.get("session") if isinstance(entry, dict) else None
                    )
                    session_id = (
                        entry_session.get("session_id")
                        if isinstance(entry_session, dict)
                        else None
                    )
                    errors.append(f"{session_id}: {exc!s}")
                    # Drop the files this session wrote before its tx rolled back.
                    for path in created_files[mark:]:
                        try:
                            path.unlink(missing_ok=True)
                        except OSError as unlink_exc:
                            logger.warning(
                                f"Failed to roll back {path}: {unlink_exc!s}"
                            )
                    del created_files[mark:]
        finally:
            self._drop_pending(import_id)
        return {"created": created, "warnings": warnings, "errors": errors}
