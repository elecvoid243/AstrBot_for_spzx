"""Per-umo, per-window archive for history discarded by window resets.

Layout::

    data/agent_history/<umo_key>/windows.json
    data/agent_history/<umo_key>/<window_id>/items.jsonl

A closed window has no other copy: the active window lives in
``conversations.content`` and a reset replaces it. Each window file is written
once through a temporary file, and the index is replaced atomically, so a crash
leaves a window marked open instead of losing its content.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from astrbot.core.agent.message import Message, WindowState
from astrbot.core.utils.astrbot_path import get_astrbot_agent_history_path

MAX_ITEM_CHARS = 8000
"""Longest stored content field; longer values are cut and flagged."""
MAX_FIRST_USER_CHARS = 120
"""Longest ``first_user_text`` kept in the index for window listing."""
MAX_READ_ITEM_CHARS = 2000
"""Longest content returned by a read, keeping one tool result bounded."""
MAX_SEARCH_SNIPPET_CHARS = 80
"""Characters kept on each side of a search hit."""

_INDEX_FILENAME = "windows.json"
_ITEMS_FILENAME = "items.jsonl"
_CHECKPOINT_ROLE = "_checkpoint"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def umo_key(umo: str) -> str:
    """Return the directory name that isolates one umo's archive.

    A umo contains ``:`` and ``!``, which are not portable directory names, so
    the readable part keeps only the platform prefix and a digest makes the
    mapping stable. The original umo is stored in ``windows.json``.

    Args:
        umo: Unified message origin of the session.

    Returns:
        A filesystem-safe key that is stable for the same umo.
    """
    platform = umo.split(":", 1)[0] or "unknown"
    safe = re.sub(r"[^A-Za-z0-9_-]", "", platform)[:24] or "umo"
    digest = hashlib.sha1(umo.encode("utf-8")).hexdigest()[:12]
    return f"{safe}_{digest}"


def archive_dir(umo: str) -> Path:
    """Return the archive directory of one umo.

    Args:
        umo: Unified message origin of the session.

    Returns:
        ``<data>/agent_history/<umo_key>``; not created until a write happens.
    """
    return Path(get_astrbot_agent_history_path()) / umo_key(umo)


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def _archivable(messages: list[Message]) -> list[Message]:
    """Apply the persistence view so archived text matches saved history.

    Args:
        messages: Messages a reset is about to discard.

    Returns:
        Messages without the leading system prompt, internal control text,
        unsaved turns or checkpoints.
    """
    result: list[Message] = []
    skipped_initial_system = False
    for message in messages:
        if message.role == "system" and not skipped_initial_system:
            skipped_initial_system = True
            continue
        if message.role == _CHECKPOINT_ROLE:
            continue
        if message.role in ("assistant", "user") and message._no_save:
            continue
        if message.internal:
            continue
        result.append(message)
    return result


def _bounded_content(content: Any) -> tuple[Any, int | None]:
    """Return storage-safe content plus its original length when cut.

    Args:
        content: Serialized message content (string, part list or null).

    Returns:
        The content to store, and the original length when it was truncated.
    """
    if content is None:
        return None, None
    text = (
        content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    )
    if len(text) <= MAX_ITEM_CHARS:
        return content, None
    return text[:MAX_ITEM_CHARS], len(text)


def _item_payload(message: Message, fallback_ts: int) -> dict[str, Any]:
    """Build one stored archive item from a message.

    Args:
        message: Message being discarded by a reset.
        fallback_ts: Batch timestamp used when the message carries no time.

    Returns:
        JSON-serializable item without window or internal markers.
    """
    data = message.model_dump(mode="json")
    content, original_chars = _bounded_content(data.get("content"))
    created_at = message.created_at
    payload: dict[str, Any] = {
        "ts": int(created_at) if created_at is not None else fallback_ts,
        "role": data.get("role"),
    }
    if created_at is None:
        # Old messages carry no production time; say so instead of pretending
        # the batch time is when they were written.
        payload["ts_estimated"] = True
    if content is not None:
        payload["content"] = content
    if original_chars is not None:
        payload["truncated"] = True
        payload["original_chars"] = original_chars
    for key in ("tool_calls", "tool_call_id"):
        value = data.get(key)
        if value:
            payload[key] = value
    return payload


def _read_index(directory: Path, umo: str) -> dict[str, Any]:
    """Read the window index, tolerating a missing or damaged file.

    Args:
        directory: Archive directory of one umo.
        umo: Unified message origin, kept for the first write.

    Returns:
        Index with a ``windows`` list.
    """
    try:
        data = json.loads((directory / _INDEX_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["umo"] = umo
    if not isinstance(data.get("windows"), list):
        data["windows"] = []
    return data


def _write_index(directory: Path, data: dict[str, Any]) -> None:
    """Replace the index atomically so a reader never sees a partial file.

    Args:
        directory: Archive directory of one umo.
        data: Complete index payload.
    """
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f".{_INDEX_FILENAME}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, directory / _INDEX_FILENAME)


def _first_user_text(items: list[dict[str, Any]]) -> str | None:
    """Return the first textual user message, cut for listing.

    Args:
        items: Stored items of one window.

    Returns:
        The trimmed first user text, or ``None`` when the window has none.
    """
    for item in items:
        if item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()[:MAX_FIRST_USER_CHARS]
    return None


def _append_window_sync(umo: str, window: WindowState, messages: list[Message]) -> None:
    """Write one closed window and its index record.

    Args:
        umo: Unified message origin of the session.
        window: Window that is being replaced.
        messages: Messages the reset discards.
    """
    batch_ts = int(time.time())
    items = [_item_payload(message, batch_ts) for message in _archivable(messages)]
    if not items:
        return
    directory = archive_dir(umo)
    window_dir = directory / window.id
    payload = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items)
    with _lock_for(umo_key(umo)):
        window_dir.mkdir(parents=True, exist_ok=True)
        tmp = window_dir / f".{_ITEMS_FILENAME}.tmp"
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, window_dir / _ITEMS_FILENAME)
        index = _read_index(directory, umo)
        record = {
            "window_id": window.id,
            "seq": window.seq,
            "prev_id": window.prev_id,
            "closed_at": int(time.time()),
            "item_count": len(items),
            "bytes": len(payload.encode("utf-8")),
            "first_user_text": _first_user_text(items),
        }
        # Replacing the record by identity keeps a retried write idempotent.
        index["windows"] = [
            entry
            for entry in index["windows"]
            if isinstance(entry, dict) and entry.get("window_id") != window.id
        ]
        index["windows"].append(record)
        index["windows"].sort(key=lambda entry: entry.get("seq", 0))
        _write_index(directory, index)


async def append_window(umo: str, window: WindowState, messages: list[Message]) -> None:
    """Archive the messages a reset discards, off the event loop.

    Args:
        umo: Unified message origin of the session.
        window: Window that is being replaced.
        messages: Messages the reset discards.
    """
    await asyncio.to_thread(_append_window_sync, umo, window, messages)


def list_windows(umo: str) -> list[dict[str, Any]]:
    """Return every archived window of one umo, oldest first.

    Args:
        umo: Unified message origin of the session.

    Returns:
        Index records; empty when the umo has no archive.
    """
    directory = archive_dir(umo)
    return list(_read_index(directory, umo)["windows"])


def _read_items(directory: Path, window_id: str) -> list[dict[str, Any]] | None:
    """Read one window's items, refusing unknown window ids.

    Args:
        directory: Archive directory of one umo.
        window_id: Window to read.

    Returns:
        Stored items, or ``None`` when the id is not in the index.
    """
    index = _read_index(directory, umo="")
    if not any(entry.get("window_id") == window_id for entry in index["windows"]):
        return None
    path = directory / window_id / _ITEMS_FILENAME
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    items: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def _bounded_item(item: dict[str, Any]) -> dict[str, Any]:
    """Cut one item's content for a read response.

    Args:
        item: Stored item.

    Returns:
        Item whose content is at most ``MAX_READ_ITEM_CHARS`` characters.
    """
    content = item.get("content")
    if isinstance(content, str) and len(content) > MAX_READ_ITEM_CHARS:
        return {**item, "content": content[:MAX_READ_ITEM_CHARS], "truncated": True}
    return item


def read_window(
    umo: str, window_id: str, offset: int = 0, limit: int = 50
) -> list[dict[str, Any]] | None:
    """Return a slice of one archived window.

    Args:
        umo: Unified message origin of the session.
        window_id: Window to read.
        offset: First item index.
        limit: Maximum number of items.

    Returns:
        Items in order, or ``None`` when the window is unknown to this umo.
    """
    items = _read_items(archive_dir(umo), window_id)
    if items is None:
        return None
    start = max(0, offset)
    end = start + max(1, limit)
    return [_bounded_item(item) for item in items[start:end]]


def _item_text(item: dict[str, Any]) -> str:
    """Render one item's searchable text.

    Args:
        item: Stored item.

    Returns:
        Content as text; tool calls when the message has no content.
    """
    content = item.get("content")
    if isinstance(content, str):
        return content
    if content is None:
        return json.dumps(item.get("tool_calls") or "", ensure_ascii=False)
    return json.dumps(content, ensure_ascii=False)


def _snippet(text: str, start: int, length: int) -> str:
    """Return the hit with a bounded amount of surrounding text.

    Args:
        text: Full item text.
        start: Match start offset.
        length: Match length.

    Returns:
        Window of text around the match.
    """
    begin = max(0, start - MAX_SEARCH_SNIPPET_CHARS)
    end = min(len(text), start + length + MAX_SEARCH_SNIPPET_CHARS)
    return text[begin:end]


def search(
    umo: str,
    query: str,
    window_id: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search archived windows of one umo with a case-insensitive substring.

    Args:
        umo: Unified message origin of the session.
        query: Non-empty search text.
        window_id: Restrict the search to one window.
        limit: Maximum number of hits.

    Returns:
        Hits with window, item index, role and snippet; empty when nothing
        matches or the umo has no archive.
    """
    needle = query.strip().lower()
    if not needle:
        return []
    directory = archive_dir(umo)
    hits: list[dict[str, Any]] = []
    for entry in _read_index(directory, umo)["windows"]:
        current_id = entry.get("window_id")
        if window_id is not None and current_id != window_id:
            continue
        items = _read_items(directory, str(current_id))
        if items is None:
            continue
        for index, item in enumerate(items):
            text = _item_text(item)
            position = text.lower().find(needle)
            if position < 0:
                continue
            hits.append(
                {
                    "window_id": current_id,
                    "seq": entry.get("seq", 0),
                    "index": index,
                    "role": item.get("role"),
                    "snippet": _snippet(text, position, len(needle)),
                }
            )
            if len(hits) >= max(1, limit):
                return hits
    return hits


__all__ = [
    "MAX_FIRST_USER_CHARS",
    "MAX_ITEM_CHARS",
    "MAX_READ_ITEM_CHARS",
    "append_window",
    "archive_dir",
    "list_windows",
    "read_window",
    "search",
    "umo_key",
]
