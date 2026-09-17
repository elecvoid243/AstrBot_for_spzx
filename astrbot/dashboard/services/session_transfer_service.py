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
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from astrbot.core.config.default import VERSION
from astrbot.core.platform.message_type import MessageType
from astrbot.core.utils.astrbot_path import (
    get_astrbot_data_path,
    get_astrbot_temp_path,
)
from astrbot.core.utils.datetime_utils import to_utc_isoformat
from astrbot.dashboard.services.chat_service import extract_attachment_ids

EXPORT_KIND = "astrbot-chatui-export"
EXPORT_FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
EXPORT_DATA_NAME = "export.json"
ATTACHMENTS_PREFIX = "files/attachments/"
WEBCHAT_PLATFORM_ID = "webchat"
THREAD_PLATFORM_ID = "webchat_thread"

# Import guards (zip bomb / oversized upload defence).
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_ZIP_ENTRIES = 20000
MAX_ENTRY_BYTES = 512 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
IMPORT_ID_TTL_SECONDS = 1800

# Export reads one session in a single page; matches the existing
# `page_size=100000` usage in chat_service.py.
HISTORY_PAGE_SIZE = 100000

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

    file_obj: BytesIO
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
            SessionExport holding the zip bytes, download filename and mimetype.

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

        attachments: list[dict] = []
        attachment_blobs: list[tuple[str, bytes]] = []
        seen_ids: set[str] = set()
        for record in history:
            for attachment_id in extract_attachment_ids([record]):
                if attachment_id in seen_ids:
                    continue
                seen_ids.add(attachment_id)
                attachment = await self.db.get_attachment_by_id(attachment_id)
                if attachment is None:
                    warnings.append(f"Attachment {attachment_id} metadata missing")
                    continue
                source = Path(attachment.path)
                ext = source.suffix if is_safe_attachment_ext(source.suffix) else ""
                zip_path = f"{ATTACHMENTS_PREFIX}{attachment_id}{ext}"
                entry = {
                    "attachment_id": attachment_id,
                    "type": attachment.type,
                    "mime_type": attachment.mime_type,
                    "ext": ext,
                    "size": source.stat().st_size if source.is_file() else 0,
                    "zip_path": zip_path,
                }
                if source.is_file():
                    try:
                        attachment_blobs.append(
                            (zip_path, await asyncio.to_thread(source.read_bytes))
                        )
                    except OSError as exc:
                        warnings.append(
                            f"Attachment {attachment_id} unreadable: {exc!s}"
                        )
                else:
                    warnings.append(f"Attachment {attachment_id} file missing on disk")
                attachments.append(entry)

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

        checksums: dict[str, str] = {
            EXPORT_DATA_NAME: f"sha256:{hashlib.sha256(data_bytes).hexdigest()}"
        }
        for zip_path, blob in attachment_blobs:
            checksums[zip_path] = f"sha256:{hashlib.sha256(blob).hexdigest()}"

        manifest = {
            "kind": EXPORT_KIND,
            "format_version": EXPORT_FORMAT_VERSION,
            "astrbot_version": VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "sessions": [
                {
                    "original_session_id": session.session_id,
                    "display_name": session.display_name,
                    "original_creator": session.creator,
                    "stats": {
                        "messages": len(history),
                        "conversations": len(
                            data_payload["sessions"][0]["conversations"]
                        ),
                        "threads": len(thread_payloads),
                        "attachments": len(attachments),
                        "attachment_bytes": sum(entry["size"] for entry in attachments),
                    },
                }
            ],
            "warnings": warnings,
            "checksums": checksums,
        }

        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            zf.writestr(EXPORT_DATA_NAME, data_bytes)
            for zip_path, blob in attachment_blobs:
                zf.writestr(zip_path, blob)
        buffer.seek(0)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return SessionExport(
            file_obj=buffer,
            filename=f"astrbot_chatui_export_{timestamp}.zip",
        )
