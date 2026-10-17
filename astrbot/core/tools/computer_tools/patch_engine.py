"""Parsing and application of Codex-style multi-file patches."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

_BEGIN = "*** Begin Patch"
_END = "*** End Patch"
_ADD = "*** Add File:"
_DELETE = "*** Delete File:"
_UPDATE = "*** Update File:"
_MOVE = "*** Move to:"
_EOF = "*** End of File"
_FENCE_RE = re.compile(r"^```[A-Za-z0-9_-]*\s*$")


@dataclass
class Hunk:
    """One contiguous change block with optional anchor and EOF requirement."""

    context: str | None = None
    old_lines: list[str] = field(default_factory=list)
    new_lines: list[str] = field(default_factory=list)
    is_end_of_file: bool = False


@dataclass
class FilePatch:
    """One file section of a patch."""

    kind: Literal["add", "update", "delete"]
    path: str
    move_to: str | None = None
    hunks: list[Hunk] = field(default_factory=list)
    add_lines: list[str] = field(default_factory=list)


class PatchParseError(ValueError):
    """Raised when a patch cannot be parsed or planned."""

    def __init__(self, message: str, line_number: int | None = None) -> None:
        super().__init__(message)
        self.line_number = line_number


def _marker_value(line: str, marker: str) -> str:
    return line[len(marker) :].strip()


def parse_patch(text: str) -> list[FilePatch]:
    """Parse a lenient Codex-style patch into file-level entries.

    Args:
        text: Patch text, optionally wrapped in a Markdown code fence.

    Returns:
        Parsed file sections in patch order.

    Raises:
        PatchParseError: If the patch is malformed or contains duplicate paths.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    files: list[FilePatch] = []
    seen_paths: set[str] = set()
    current: FilePatch | None = None
    hunk: Hunk | None = None
    in_patch = False

    def fail(message: str, index: int) -> None:
        raise PatchParseError(message, line_number=index + 1)

    def finish_hunk() -> None:
        nonlocal hunk
        if hunk is None:
            return
        # A blank line before the next marker is formatting, not context.
        while (
            hunk.old_lines
            and hunk.new_lines
            and hunk.old_lines[-1] == ""
            and hunk.new_lines[-1] == ""
        ):
            hunk.old_lines.pop()
            hunk.new_lines.pop()
        if current is not None and (hunk.old_lines or hunk.new_lines):
            current.hunks.append(hunk)
        hunk = None

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not in_patch:
            if stripped == _BEGIN:
                in_patch = True
                continue
            if not stripped or _FENCE_RE.match(stripped):
                continue
            if stripped.startswith((_ADD, _UPDATE, _DELETE)):
                in_patch = True
            else:
                continue

        if stripped == _END:
            finish_hunk()
            current = None
            in_patch = False
            continue
        if _FENCE_RE.match(stripped):
            continue
        if stripped.startswith(_ADD):
            finish_hunk()
            path = _marker_value(stripped, _ADD)
            if not path:
                fail("Add File requires a path", index)
            if path in seen_paths:
                fail(f"duplicate path: {path}", index)
            seen_paths.add(path)
            current = FilePatch(kind="add", path=path)
            files.append(current)
            continue
        if stripped.startswith(_DELETE):
            finish_hunk()
            path = _marker_value(stripped, _DELETE)
            if not path:
                fail("Delete File requires a path", index)
            if path in seen_paths:
                fail(f"duplicate path: {path}", index)
            seen_paths.add(path)
            current = FilePatch(kind="delete", path=path)
            files.append(current)
            continue
        if stripped.startswith(_UPDATE):
            finish_hunk()
            path = _marker_value(stripped, _UPDATE)
            if not path:
                fail("Update File requires a path", index)
            if path in seen_paths:
                fail(f"duplicate path: {path}", index)
            seen_paths.add(path)
            current = FilePatch(kind="update", path=path)
            files.append(current)
            continue
        if stripped.startswith(_MOVE):
            if current is None or current.kind != "update":
                fail("Move to must follow Update File", index)
            target = _marker_value(stripped, _MOVE)
            if not target:
                fail("Move to requires a path", index)
            if target in seen_paths:
                fail(f"duplicate path: {target}", index)
            seen_paths.add(target)
            current.move_to = target
            continue

        if current is None:
            fail("content outside a file section", index)
        if current.kind == "add":
            if not stripped.startswith("+"):
                fail("Add File lines must start with '+'", index)
            current.add_lines.append(line[1:])
            continue
        if current.kind == "delete":
            if stripped:
                fail("Delete File takes no content", index)
            continue

        if stripped.startswith("@@"):
            finish_hunk()
            hunk = Hunk(context=stripped[2:].strip() or None)
            continue
        if stripped == _EOF:
            if hunk is None:
                hunk = Hunk()
            hunk.is_end_of_file = True
            finish_hunk()
            continue
        if hunk is None:
            hunk = Hunk()
        if line.startswith("+"):
            hunk.new_lines.append(line[1:])
        elif line.startswith("-"):
            hunk.old_lines.append(line[1:])
        elif line.startswith(" ") or line == "":
            content = line[1:] if line else ""
            hunk.old_lines.append(content)
            hunk.new_lines.append(content)
        else:
            fail("hunk lines must start with ' ', '+', or '-'", index)

    finish_hunk()
    if not files:
        raise PatchParseError("patch contains no file sections")
    for entry in files:
        if entry.kind == "add" and not entry.add_lines:
            raise PatchParseError(f"Add File {entry.path!r} has no content")
        if entry.kind == "update" and not entry.hunks:
            raise PatchParseError(f"Update File {entry.path!r} has no hunks")
    return files
