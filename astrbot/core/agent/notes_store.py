"""Per-umo private notes: virtual paths over a flat file store.

Notes survive context window resets and are shared by every window of one
umo. Paths are virtual (relative to the umo's notes root), so a model can
never address a real filesystem location.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from astrbot.core.agent.history_archive import umo_key
from astrbot.core.utils.astrbot_path import get_astrbot_agent_notes_path

MAX_FILE_BYTES = 64 * 1024
"""Largest note file; roughly 16k tokens, so a larger note should be split."""
MAX_TOTAL_BYTES = 1024 * 1024
"""Largest total size of one umo's notes, bounding unbounded growth."""
MAX_COMPONENT_CHARS = 100
"""Longest single path component."""
MAX_PATH_CHARS = 200
"""Longest virtual path."""
MAX_SNIPPET_CHARS = 60
"""Characters kept on each side of a search hit."""

DEFAULT_SUFFIX = ".md"
"""Suffix added when a virtual path has no extension."""

_WRITE_MODES = ("overwrite", "append")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def notes_root(umo: str) -> Path:
    """Return the notes directory of one umo.

    Args:
        umo: Unified message origin of the session.

    Returns:
        ``<data>/agent_notes/<umo_key>``; not created until a write happens.
    """
    return Path(get_astrbot_agent_notes_path()) / umo_key(umo)


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def _is_reserved(component: str) -> bool:
    """Return whether a component collides with a Windows device name.

    Args:
        component: One path component.

    Returns:
        True when the stem matches a reserved device name.
    """
    return component.split(".")[0].upper() in _RESERVED_NAMES


def validate_path(umo: str, raw: str) -> Path:
    """Resolve a virtual note path inside one umo's notes root.

    Args:
        umo: Unified message origin of the session.
        raw: Model-supplied virtual path.

    Returns:
        Absolute path of the note file.

    Raises:
        ValueError: The path is empty, absolute, reserved, too long, or would
            escape the notes root.
    """
    text = (raw or "").strip().replace("\\", "/")
    if not text:
        raise ValueError("path must not be empty")
    if _DRIVE_RE.match(text) or text.startswith("/"):
        raise ValueError("absolute paths are not supported")
    parts = [part for part in text.split("/") if part]
    if not parts:
        raise ValueError("path must not be empty")
    if any(part in (".", "..") for part in parts):
        raise ValueError("relative path components are not supported")
    if any(_is_reserved(part) for part in parts):
        raise ValueError("reserved path component")
    if any(len(part) > MAX_COMPONENT_CHARS for part in parts):
        raise ValueError("path component is too long")
    if len("/".join(parts)) > MAX_PATH_CHARS:
        raise ValueError("path is too long")
    if not Path(parts[-1]).suffix:
        parts[-1] += DEFAULT_SUFFIX
    root = notes_root(umo).resolve()
    target = (root / Path(*parts)).resolve()
    if target != root and root not in target.parents:
        raise ValueError("path escapes the notes root")
    return target


def _relative(root: Path, path: Path) -> str:
    """Return the virtual path of a stored note.

    Args:
        root: Notes root of the umo.
        path: Absolute note path.

    Returns:
        Slash-separated path relative to ``root``.
    """
    return path.resolve().relative_to(root.resolve()).as_posix()


def _line_count(text: str) -> int:
    """Return the number of lines, counting a trailing partial line.

    Args:
        text: File content.

    Returns:
        Line count; 0 for empty text.
    """
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _note_entry(root: Path, path: Path) -> dict[str, Any] | None:
    """Describe one stored note, tolerating files that vanish mid-scan.

    Args:
        root: Notes root of the umo.
        path: Absolute note path.

    Returns:
        Entry with path, bytes, lines and updated_at, or None when unreadable.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        stat = path.stat()
    except OSError:
        return None
    return {
        "path": _relative(root, path),
        "bytes": len(text.encode("utf-8")),
        "lines": _line_count(text),
        "updated_at": int(stat.st_mtime),
    }


def list_notes(umo: str, prefix: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """List stored notes of one umo, newest first.

    Args:
        umo: Unified message origin of the session.
        prefix: Virtual path prefix to filter by.
        limit: Maximum number of entries.

    Returns:
        Note entries sorted by ``updated_at`` descending.
    """
    root = notes_root(umo)
    if not root.is_dir():
        return []
    wanted = prefix.strip().replace("\\", "/").lstrip("/")
    entries: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        entry = _note_entry(root, path)
        if entry is None:
            continue
        if wanted and not entry["path"].startswith(wanted):
            continue
        entries.append(entry)
    entries.sort(key=lambda entry: entry["updated_at"], reverse=True)
    return entries[: max(1, limit)]


def read_note(
    umo: str,
    raw_path: str,
    start_line: int = 1,
    stop_line: int | None = None,
) -> dict[str, Any] | None:
    """Read a line range from one note.

    Args:
        umo: Unified message origin of the session.
        raw_path: Model-supplied virtual path.
        start_line: First line, 1-based; negative counts from the end.
        stop_line: Last line, 1-based; negative counts from the end.

    Returns:
        ``path``, ``total_lines``, ``start``, ``end`` and ``content``, or
        ``None`` when the note does not exist.

    Raises:
        ValueError: The path is invalid.
    """
    root = notes_root(umo)
    path = validate_path(umo, raw_path)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    total = len(lines)
    if total == 0:
        start = end = 0
    else:
        start = _resolve_line(start_line, total, default=1)
        end = _resolve_line(stop_line, total, default=total) if stop_line else total
        start = max(1, min(start, total))
        end = max(start, min(end, total))
    return {
        "path": _relative(root, path),
        "total_lines": total,
        "start": start,
        "end": end,
        "content": "\n".join(lines[start - 1 : end]),
    }


def _resolve_line(value: int, total: int, default: int) -> int:
    """Translate a possibly negative 1-based line number.

    Args:
        value: Caller-provided line number.
        total: Number of lines in the file.
        default: Value used when the caller passed 0.

    Returns:
        A 1-based line number, clamped by the caller.
    """
    if value == 0:
        return default
    if value < 0:
        return max(1, total + value + 1)
    return value


def _snippet(line: str, position: int, length: int) -> str:
    """Return the hit with a bounded amount of surrounding text.

    Args:
        line: Full line.
        position: Match start offset.
        length: Match length.

    Returns:
        Window of text around the match.
    """
    begin = max(0, position - MAX_SNIPPET_CHARS)
    end = min(len(line), position + length + MAX_SNIPPET_CHARS)
    return line[begin:end]


def search_notes(
    umo: str,
    query: str,
    path_prefix: str = "",
    max_files: int = 10,
    max_matches_per_file: int = 3,
) -> list[dict[str, Any]]:
    """Search note lines with a case-insensitive substring.

    Args:
        umo: Unified message origin of the session.
        query: Non-empty search text.
        path_prefix: Virtual path prefix to search.
        max_files: Maximum number of matching files.
        max_matches_per_file: Maximum hits returned per file.

    Returns:
        Hits with path, 1-based line number and snippet.
    """
    needle = query.strip().lower()
    if not needle:
        return []
    root = notes_root(umo)
    if not root.is_dir():
        return []
    wanted = path_prefix.strip().replace("\\", "/").lstrip("/")
    hits: list[dict[str, Any]] = []
    files = 0
    for entry in list_notes(umo, prefix=wanted, limit=1000):
        path = root / entry["path"]
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        matches = 0
        for index, line in enumerate(lines, 1):
            position = line.lower().find(needle)
            if position < 0:
                continue
            hits.append(
                {
                    "path": entry["path"],
                    "line": index,
                    "snippet": _snippet(line, position, len(needle)),
                }
            )
            matches += 1
            if matches >= max(1, max_matches_per_file):
                break
        if matches:
            files += 1
            if files >= max(1, max_files):
                break
    return hits


def _total_bytes(root: Path) -> int:
    """Return the total UTF-8 size of all notes under one root.

    Args:
        root: Notes root of the umo.

    Returns:
        Total bytes; 0 when the root does not exist.
    """
    if not root.is_dir():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _write_note_sync(umo: str, path: Path, text: str, mode: str) -> dict[str, Any]:
    """Write one note under the umo lock, enforcing both size limits.

    Args:
        umo: Unified message origin of the session.
        path: Validated absolute note path.
        text: Content to write or append.
        mode: ``overwrite`` or ``append``.

    Returns:
        ``path``, ``bytes``, ``lines`` and ``mode``.

    Raises:
        ValueError: The mode is unknown or a size limit would be exceeded.
    """
    root = notes_root(umo)
    encoded = text.encode("utf-8")
    with _lock_for(umo_key(umo)):
        existing = ""
        if mode == "append" and path.is_file():
            existing = path.read_text(encoding="utf-8", errors="replace")
        content = existing + text if mode == "append" else text
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > MAX_FILE_BYTES:
            raise ValueError(
                f"note would exceed {MAX_FILE_BYTES} bytes; split it into files"
            )
        previous = 0
        if path.is_file():
            try:
                previous = path.stat().st_size
            except OSError:
                previous = 0
        projected = _total_bytes(root) - previous + content_bytes
        if projected > MAX_TOTAL_BYTES:
            raise ValueError(
                f"notes would exceed {MAX_TOTAL_BYTES} bytes in this conversation"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return {
        "path": _relative(root, path),
        "bytes": content_bytes,
        "lines": _line_count(content),
        "mode": mode,
        "written": len(encoded),
    }


def write_note(
    umo: str, raw_path: str, text: str, mode: str = "overwrite"
) -> dict[str, Any]:
    """Write one note.

    Args:
        umo: Unified message origin of the session.
        raw_path: Model-supplied virtual path.
        text: Content to write or append.
        mode: ``overwrite`` or ``append``.

    Returns:
        ``path``, ``bytes``, ``lines``, ``mode`` and ``written``.

    Raises:
        ValueError: The path, mode, or a size limit is invalid.
    """
    if mode not in _WRITE_MODES:
        raise ValueError("mode must be 'overwrite' or 'append'")
    path = validate_path(umo, raw_path)
    return _write_note_sync(umo, path, text, mode)


__all__ = [
    "DEFAULT_SUFFIX",
    "MAX_FILE_BYTES",
    "MAX_TOTAL_BYTES",
    "list_notes",
    "notes_root",
    "read_note",
    "search_notes",
    "validate_path",
    "write_note",
]
