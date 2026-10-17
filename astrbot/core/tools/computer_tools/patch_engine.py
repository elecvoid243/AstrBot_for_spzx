"""Parsing and application of Codex-style multi-file patches."""

from __future__ import annotations

import ast
import difflib
import re
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from .edit_engine import (
    adjusted_replacement,
    build_unified_diff,
    decode_text_bytes,
    encode_text_bytes,
    find_match_at_or_after,
    validate_indent_style,
)

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

    def __str__(self) -> str:
        message = super().__str__()
        if self.line_number is not None:
            return f"{message} (line {self.line_number})"
        return message


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
            if line == "":
                continue
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


_UNICODE_MAP = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u00a0": " ",
        "\u3000": " ",
    }
)


class HunkApplyError(ValueError):
    """Raised when a hunk cannot be located or applied."""

    def __init__(self, message: str, hunk_index: int) -> None:
        super().__init__(message)
        self.hunk_index = hunk_index

    def __str__(self) -> str:
        return f"{super().__str__()} (hunk #{self.hunk_index + 1})"


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for idx, char in enumerate(text):
        if char == "\n":
            starts.append(idx + 1)
    return starts


def _lines_equal(candidate: list[str], expected: list[str], tier: int) -> bool:
    if tier == 0:
        return candidate == expected
    if tier == 1:
        return [line.rstrip() for line in candidate] == [
            line.rstrip() for line in expected
        ]
    if tier == 2:
        return [line.strip() for line in candidate] == [
            line.strip() for line in expected
        ]
    return [line.strip().translate(_UNICODE_MAP) for line in candidate] == [
        line.strip().translate(_UNICODE_MAP) for line in expected
    ]


def _find_exact_ladder(text: str, old: str, start: int) -> tuple[int, str] | None:
    """Try exact, rstrip, trim, then unicode-normalized line matching."""
    expected = old.split("\n")
    lines = text.split("\n")
    offsets = _line_starts(text)
    start_index = bisect_left(offsets, start)
    last_index = len(lines) - len(expected)
    for tier in range(4):
        for index in range(start_index, last_index + 1):
            candidate = lines[index : index + len(expected)]
            if _lines_equal(candidate, expected, tier):
                return offsets[index], "\n".join(candidate)
    return None


def _apply_one(text: str, hunk: Hunk, cursor: int, index: int) -> tuple[str, int]:
    if hunk.context is not None:
        anchor = _find_exact_ladder(text, hunk.context, cursor)
        if anchor is None:
            raise HunkApplyError(
                f"anchor not found: {hunk.context!r}", hunk_index=index
            )
        cursor = anchor[0] + len(anchor[1])
        if cursor < len(text) and text[cursor] == "\n":
            cursor += 1

    new_text = "\n".join(hunk.new_lines)

    if not hunk.old_lines:
        validate_indent_style(text, new_text)
        if hunk.context is not None:
            insertion = text[:cursor] + new_text + "\n" + text[cursor:]
            return insertion, cursor + len(new_text) + 1
        suffix = "" if text.endswith("\n") or not text else "\n"
        insertion = text + suffix + new_text + "\n"
        return insertion, len(insertion)

    old_text = "\n".join(hunk.old_lines)
    if hunk.is_end_of_file:
        stripped = text.rstrip("\n")
        if stripped.endswith(old_text):
            idx = len(stripped) - len(old_text)
            if idx >= cursor:
                adjusted = adjusted_replacement(text, idx, old_text, old_text, new_text)
                return (
                    text[:idx] + adjusted + text[idx + len(old_text) :],
                    idx + len(adjusted),
                )

    found = _find_exact_ladder(text, old_text, cursor)
    if found is None:
        found = find_match_at_or_after(text, old_text, cursor)
    if found is None:
        first_line = hunk.old_lines[0] if hunk.old_lines else ""
        hint = ""
        if (
            len(hunk.old_lines) > 1
            and _find_exact_ladder(text, first_line, cursor) is not None
        ):
            hint = "; if these are separate edits, separate them with '@@'"
        raise HunkApplyError(
            f"context not found: {first_line!r}{hint}", hunk_index=index
        )

    idx, matched = found
    adjusted = adjusted_replacement(text, idx, old_text, matched, new_text)
    insertion = text[:idx] + adjusted + text[idx + len(matched) :]
    return insertion, idx + len(adjusted)


def apply_hunks_to_text(text: str, hunks: list[Hunk]) -> str:
    """Apply hunks to LF-normalized text with a monotonically advancing cursor.

    Args:
        text: LF-normalized file content.
        hunks: Hunks in file order.

    Returns:
        The patched text.

    Raises:
        HunkApplyError: If a hunk cannot be located.
    """
    cursor = 0
    for index, hunk in enumerate(hunks):
        text, cursor = _apply_one(text, hunk, cursor, index)
    return text


@dataclass
class FilePlan:
    """Computed outcome for one file in a patch."""

    path: str
    kind: str
    original: bytes | None
    target_original: bytes | None
    new_bytes: bytes | None
    move_to: str | None
    destructive_reason: str | None
    diff: str
    added: int
    removed: int


@dataclass
class PatchPlan:
    """Computed outcome for a whole patch."""

    files: list[FilePlan]
    destructive: list[FilePlan]
    added: int
    removed: int


def _count_changes(old: str, new: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm=""):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def _validate_python(path: str, old_text: str, new_text: str) -> None:
    if not path.lower().endswith(".py"):
        return
    try:
        ast.parse(old_text)
    except SyntaxError:
        return
    try:
        ast.parse(new_text)
    except SyntaxError as exc:
        raise PatchParseError(
            f"Python syntax error after patch in {path}: "
            f"line {exc.lineno}, column {exc.offset}: {exc.msg}"
        ) from exc


def plan_patch(
    patches: list[FilePatch], read: Callable[[str], bytes | None]
) -> PatchPlan:
    """Compute new contents and destructive flags without touching disk.

    Args:
        patches: Parsed file sections.
        read: Callback returning file bytes, or ``None`` when absent.

    Returns:
        A plan describing every file, destructive operations, and line counts.

    Raises:
        PatchParseError: If a target is missing or produces invalid Python.
    """
    plans: list[FilePlan] = []
    for entry in patches:
        original = read(entry.path)
        if entry.kind in ("update", "delete") and original is None:
            raise PatchParseError(f"file does not exist: {entry.path}")

        target_original = read(entry.move_to) if entry.move_to is not None else None
        new_bytes: bytes | None = None
        new_text = ""
        old_text = ""
        destructive_reason: str | None = None

        if entry.kind == "delete":
            destructive_reason = "delete"
            old_text = decode_text_bytes(original or b"")[0]
        elif entry.kind == "add":
            if original is not None:
                destructive_reason = "overwrite_add"
                old_text = decode_text_bytes(original)[0]
            new_bytes = ("\n".join(entry.add_lines) + "\n").encode("utf-8")
            new_text = new_bytes.decode("utf-8")
            _validate_python(entry.path, old_text, new_text)
        else:
            old_text, eol, has_bom = decode_text_bytes(original or b"")
            try:
                new_text = apply_hunks_to_text(old_text, entry.hunks)
            except HunkApplyError as exc:
                raise PatchParseError(f"{entry.path}: {exc}") from exc
            new_bytes = encode_text_bytes(new_text, eol, has_bom)
            _validate_python(entry.move_to or entry.path, old_text, new_text)
            if entry.move_to is not None and target_original is not None:
                destructive_reason = "overwrite_move_target"

        added = removed = 0
        diff = ""
        if new_bytes is not None:
            added, removed = _count_changes(old_text, new_text)
            diff = build_unified_diff(entry.path, old_text, new_text)
        plans.append(
            FilePlan(
                path=entry.path,
                kind=entry.kind,
                original=original,
                target_original=target_original,
                new_bytes=new_bytes,
                move_to=entry.move_to,
                destructive_reason=destructive_reason,
                diff=diff,
                added=added,
                removed=removed,
            )
        )

    return PatchPlan(
        files=plans,
        destructive=[plan for plan in plans if plan.destructive_reason],
        added=sum(plan.added for plan in plans),
        removed=sum(plan.removed for plan in plans),
    )
