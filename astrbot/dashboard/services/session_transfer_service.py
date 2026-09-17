"""ChatUI session export/import (cross-user migration).

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

from __future__ import annotations

import posixpath
import re
import uuid

from astrbot.core.platform.message_type import MessageType

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
