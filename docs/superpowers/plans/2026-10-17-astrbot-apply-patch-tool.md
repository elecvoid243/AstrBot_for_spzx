# AstrBot apply_patch Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a core builtin `astrbot_apply_patch` tool that applies Codex-style multi-file patches atomically, with preview, confirmation gating, backups, and rollback — a strict capability superset of `astrbot_file_edit_tool` except for the intentionally omitted `replace_all`.

**Architecture:** Two new modules: `patch_engine.py` (pure parsing / hunk application / planning) and `patch_tool.py` (I/O, guards, locks, backups, atomic commit). `edit_engine.py` gains match primitives and text decode/encode helpers; `robust_replace` and `bytes_edit_file` are refactored onto them without behavior change.

**Tech Stack:** Python 3.10+, asyncio, dataclasses, difflib, pytest, ruff.

## Global Constraints

- No new third-party dependencies.
- AstrBot's tool layer only supports JSON-schema `FunctionTool`; the patch is a single `patch: string` parameter.
- Do not change the external behavior, signatures, or error strings of `robust_replace`, `bytes_edit_file`, `FileEditTool`, `FileWriteTool`, `FileRemoveTool`.
- Code comments and log messages in English; tool description in English.
- Use `pathlib.Path` for local path manipulation.
- All commits are local only; never push.
- Conventional commit messages.
- Run `uv run ruff format` and `uv run ruff check` on touched Python files before each commit.
- The tool must never write when parsing, planning, AST validation, confirmation, or path validation fails.

---

## File Structure

| File | Responsibility |
|---|---|
| `astrbot/core/tools/computer_tools/edit_engine.py` | Add `iter_match_positions`, `find_match_at_or_after`, `adjusted_replacement`, `replace_match_at`, `decode_text_bytes`, `encode_text_bytes`; refactor `robust_replace` / `bytes_edit_file` onto them |
| `astrbot/core/tools/computer_tools/patch_engine.py` | `Hunk`, `FilePatch`, `PatchParseError`, `parse_patch`, `HunkApplyError`, `apply_hunks_to_text`, `FilePlan`, `PatchPlan`, `plan_patch` |
| `astrbot/core/tools/computer_tools/patch_tool.py` | `ApplyPatchTool`, path normalization, locks, backups, atomic commit, rollback, formatting |
| `astrbot/core/tools/computer_tools/__init__.py` | Export `ApplyPatchTool` |
| `astrbot/core/config/default.py` | Add `astrbot_apply_patch` to `tools_inherent` |
| `tests/unit/test_edit_engine.py` | Tests for new primitives and helpers |
| `tests/unit/test_patch_engine.py` | Parser / engine / planner tests |
| `tests/test_apply_patch_tool.py` | Tool-level tests |

---

### Task 1: Reusable Match Primitives

**Files:**
- Modify: `astrbot/core/tools/computer_tools/edit_engine.py`
- Test: `tests/unit/test_edit_engine.py`

**Interfaces:**
- Consumes: existing `_REPLACERS`, `_adjust_replacement_indent`
- Produces:
  - `iter_match_positions(content, old_string) -> Iterator[tuple[int, str]]`
  - `find_match_at_or_after(content, old_string, start) -> tuple[int, str] | None`
  - `adjusted_replacement(content, idx, old_string, matched, new_string, *, strict_indent=True) -> str`
  - `replace_match_at(content, idx, old_string, matched, new_string, *, strict_indent=True) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_edit_engine.py`:

```python
from astrbot.core.tools.computer_tools.edit_engine import (
    adjusted_replacement,
    find_match_at_or_after,
    iter_match_positions,
    replace_match_at,
)


def test_iter_match_positions_returns_first_matching_tier():
    content = "alpha\nbeta\ngamma\n"
    assert list(iter_match_positions(content, "beta")) == [(6, "beta")]


def test_find_match_at_or_after_skips_earlier_occurrence():
    content = "foo\nbar\nfoo\n"
    found = find_match_at_or_after(content, "foo", 4)
    assert found is not None
    assert found[0] == content.rindex("foo")


def test_find_match_at_or_after_returns_none_when_missing():
    assert find_match_at_or_after("abc\n", "zzz", 0) is None


def test_find_match_at_or_after_uses_later_tier_after_start():
    content = "        return 1\n"
    found = find_match_at_or_after(content, "    return 1", 0)
    assert found is not None
    assert found[1].strip() == "return 1"


def test_adjusted_replacement_aligns_to_file_indent():
    content = "def f():\n    return 1\n"
    old = "        return 1"
    found = find_match_at_or_after(content, old, 0)
    assert found is not None
    idx, matched = found
    assert adjusted_replacement(content, idx, old, matched, "return 2") == "    return 2"


def test_replace_match_at_splices_adjusted_text():
    content = "def f():\n    return 1\n"
    old = "        return 1"
    idx, matched = find_match_at_or_after(content, old, 0)
    assert replace_match_at(content, idx, old, matched, "return 2") == (
        "def f():\n    return 2\n"
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_edit_engine.py -k "iter_match_positions or find_match_at_or_after or adjusted_replacement or replace_match_at" -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the primitives**

Insert above `robust_replace`:

```python
def iter_match_positions(
    content: str, old_string: str
) -> Iterator[tuple[int, str]]:
    """Yield (position, matched_text) for the first replacer tier that matches."""
    for replacer in _REPLACERS:
        matches = list(replacer(content, old_string))
        if not matches:
            continue
        positions: list[tuple[int, str]] = []
        for match in matches:
            cursor = 0
            while True:
                idx = content.find(match, cursor)
                if idx == -1:
                    break
                if not any(pos <= idx < pos + len(m) for pos, m in positions):
                    positions.append((idx, match))
                cursor = idx + 1
        if positions:
            yield from positions
            return


def find_match_at_or_after(
    content: str, old_string: str, start: int
) -> tuple[int, str] | None:
    """Return the first match at or after ``start``, honoring replacer tiers."""
    for replacer in _REPLACERS:
        matches = list(replacer(content, old_string))
        if not matches:
            continue
        candidates: list[tuple[int, str]] = []
        for match in matches:
            idx = content.find(match, start)
            while idx != -1:
                candidates.append((idx, match))
                idx = content.find(match, idx + 1)
        if candidates:
            return min(candidates, key=lambda item: item[0])
    return None


def adjusted_replacement(
    content: str,
    idx: int,
    old_string: str,
    matched: str,
    new_string: str,
    *,
    strict_indent: bool = True,
) -> str:
    """Return ``new_string`` reconciled with the matched block's indentation."""
    return _adjust_replacement_indent(
        new_string,
        old_string,
        matched,
        content,
        idx,
        strict_indent=strict_indent,
    )


def replace_match_at(
    content: str,
    idx: int,
    old_string: str,
    matched: str,
    new_string: str,
    *,
    strict_indent: bool = True,
) -> str:
    """Replace one known match with indent-adjusted text."""
    adjusted = adjusted_replacement(
        content, idx, old_string, matched, new_string, strict_indent=strict_indent
    )
    return content[:idx] + adjusted + content[idx + len(matched) :]
```

- [ ] **Step 4: Refactor `robust_replace`**

Replace the body after the `old_string == new_string` guard with:

```python
    positions = list(iter_match_positions(content, old_string))
    if not positions:
        raise ValueError(
            "Could not find oldString in the file. It must match exactly, "
            "including whitespace, indentation, and line endings. "
            "Try providing more surrounding context to make the match unique."
        )

    if replace_all:
        new_content = content
        for idx, match in sorted(positions, key=lambda item: item[0], reverse=True):
            new_content = replace_match_at(
                new_content,
                idx,
                old_string,
                match,
                new_string,
                strict_indent=strict_indent,
            )
        return new_content, len(positions)

    if len(positions) == 1:
        idx, match = positions[0]
        return (
            replace_match_at(
                content,
                idx,
                old_string,
                match,
                new_string,
                strict_indent=strict_indent,
            ),
            1,
        )

    raise ValueError(
        "Found multiple matches for oldString. Provide more surrounding context "
        "to make the match unique, or use replace_all=True to change every "
        "instance."
    )
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_edit_engine.py -v`
Expected: all PASS.

- [ ] **Step 6: Format, lint, commit**

```bash
uv run ruff format astrbot/core/tools/computer_tools/edit_engine.py tests/unit/test_edit_engine.py
uv run ruff check astrbot/core/tools/computer_tools/edit_engine.py tests/unit/test_edit_engine.py
git add astrbot/core/tools/computer_tools/edit_engine.py tests/unit/test_edit_engine.py
git commit -m "refactor(edit-engine): extract reusable match primitives"
```

---

### Task 2: Text Decode/Encode Helpers

**Files:**
- Modify: `astrbot/core/tools/computer_tools/edit_engine.py`
- Test: `tests/unit/test_edit_engine.py`

**Interfaces:**
- Produces:
  - `decode_text_bytes(raw_bytes, encoding="utf-8") -> tuple[str, str, bool]`
  - `encode_text_bytes(text, eol, has_bom, encoding="utf-8") -> bytes`

- [ ] **Step 1: Write the failing tests**

```python
from astrbot.core.tools.computer_tools.edit_engine import (
    decode_text_bytes,
    encode_text_bytes,
)


def test_decode_text_bytes_strips_bom_and_normalizes_crlf():
    text, eol, has_bom = decode_text_bytes(b"\xef\xbb\xbfa\r\nb\r\n")
    assert text == "a\nb\n"
    assert eol == "\r\n"
    assert has_bom is True


def test_encode_text_bytes_restores_crlf_and_bom():
    assert encode_text_bytes("a\nb\n", "\r\n", True) == b"\xef\xbb\xbfa\r\nb\r\n"


def test_decode_encode_roundtrip_without_bom():
    original = b"a\r\nb\r\n"
    text, eol, has_bom = decode_text_bytes(original)
    assert encode_text_bytes(text, eol, has_bom) == original
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_edit_engine.py -k "decode_text_bytes or encode_text_bytes" -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement helpers**

Add below `_convert_to_line_ending`:

```python
def decode_text_bytes(
    raw_bytes: bytes, encoding: str = "utf-8"
) -> tuple[str, str, bool]:
    """Decode file bytes into LF-normalized text, eol, and BOM flag."""
    has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    if has_bom:
        raw_bytes = raw_bytes[3:]
    text = raw_bytes.decode(encoding)
    eol = _detect_line_ending(text)
    return _normalize_line_endings(text), eol, has_bom


def encode_text_bytes(
    text: str, eol: str, has_bom: bool, encoding: str = "utf-8"
) -> bytes:
    """Encode LF-normalized text with the original eol and BOM."""
    output = _convert_to_line_ending(text, "\r\n") if eol == "\r\n" else text
    prefix = b"\xef\xbb\xbf" if has_bom else b""
    return prefix + output.encode(encoding)
```

- [ ] **Step 4: Refactor `bytes_edit_file`**

Replace everything from `has_bom = ...` through `write_bytes += new_content.encode(encoding)` with:

```python
    normalized_content, original_ending, has_bom = decode_text_bytes(
        raw_bytes, encoding
    )
    old_content = (
        _convert_to_line_ending(normalized_content, "\r\n")
        if original_ending == "\r\n"
        else normalized_content
    )

    normalized_old = _normalize_line_endings(old_string)
    normalized_new = _normalize_line_endings(new_string)

    new_content_lf, replacements = robust_replace(
        normalized_content,
        normalized_old,
        normalized_new,
        replace_all=replace_all,
        strict_indent=strict_indent,
    )

    new_content = (
        _convert_to_line_ending(new_content_lf, "\r\n")
        if original_ending == "\r\n"
        else new_content_lf
    )
    write_bytes = encode_text_bytes(
        new_content_lf, original_ending, has_bom, encoding
    )
```

`EditResult` keeps `old_content=old_content` and `new_content=new_content` exactly as before.

- [ ] **Step 5: Run the full edit-engine suite**

Run: `uv run pytest tests/unit/test_edit_engine.py -v`
Expected: all PASS, including pre-existing `bytes_edit_file` cases.

- [ ] **Step 6: Format, lint, commit**

```bash
uv run ruff format astrbot/core/tools/computer_tools/edit_engine.py tests/unit/test_edit_engine.py
uv run ruff check astrbot/core/tools/computer_tools/edit_engine.py tests/unit/test_edit_engine.py
git add astrbot/core/tools/computer_tools/edit_engine.py tests/unit/test_edit_engine.py
git commit -m "refactor(edit-engine): extract text decode and encode helpers"
```

---

### Task 3: Patch Parser

**Files:**
- Create: `astrbot/core/tools/computer_tools/patch_engine.py`
- Test: `tests/unit/test_patch_engine.py`

**Interfaces:**
- Produces: `Hunk`, `FilePatch`, `PatchParseError`, `parse_patch(text) -> list[FilePatch]`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_patch_engine.py`:

```python
from __future__ import annotations

import pytest

from astrbot.core.tools.computer_tools.patch_engine import (
    PatchParseError,
    parse_patch,
)


def test_parse_add_update_delete_and_move():
    patch = """*** Begin Patch
*** Add File: src/new.py
+print("hi")
*** Update File: src/a.py
*** Move to: src/b.py
@@ def foo(
-    return 1
+    return 2
*** Delete File: src/old.py
*** End Patch"""
    files = parse_patch(patch)
    assert [f.kind for f in files] == ["add", "update", "delete"]
    assert files[0].add_lines == ['print("hi")']
    assert files[1].path == "src/a.py"
    assert files[1].move_to == "src/b.py"
    assert files[1].hunks[0].context == "def foo("
    assert files[1].hunks[0].old_lines == ["    return 1"]
    assert files[1].hunks[0].new_lines == ["    return 2"]


def test_parse_accepts_missing_markers_and_code_fence():
    patch = """```text
*** Update File: a.txt
 old
-new
+new
```"""
    files = parse_patch(patch)
    assert files[0].path == "a.txt"
    assert files[0].hunks[0].old_lines == ["old", "new"]
    assert files[0].hunks[0].new_lines == ["old", "new"]


def test_parse_treats_blank_line_as_empty_context():
    patch = """*** Update File: a.txt
 line1

 line2
+line3
*** Delete File: b.txt
"""
    files = parse_patch(patch)
    assert files[0].hunks[0].old_lines == ["line1", "", "line2"]
    assert files[0].hunks[0].new_lines == ["line1", "", "line2", "line3"]


def test_parse_rejects_duplicate_paths():
    patch = """*** Update File: a.txt
-a
+b
*** Update File: a.txt
-c
+d
"""
    with pytest.raises(PatchParseError, match="duplicate path"):
        parse_patch(patch)


def test_parse_rejects_add_without_content():
    with pytest.raises(PatchParseError, match="Add File"):
        parse_patch("*** Add File: a.txt\n")


def test_parse_reports_line_number_for_unknown_marker():
    with pytest.raises(PatchParseError) as exc:
        parse_patch("*** Update File: a.txt\n?bad\n")
    assert exc.value.line_number == 2


def test_parse_rejects_move_target_collision():
    patch = (
        "*** Update File: a.txt\n*** Move to: b.txt\n"
        "*** Update File: b.txt\n-x\n+y\n"
    )
    with pytest.raises(PatchParseError, match="duplicate path"):
        parse_patch(patch)


def test_parse_end_of_file_marker():
    patch = """*** Update File: a.txt
-last
+last2
*** End of File
"""
    hunks = parse_patch(patch)[0].hunks
    assert hunks[0].is_end_of_file is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_patch_engine.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the parser**

Create `patch_engine.py`:

```python
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
    context: str | None = None
    old_lines: list[str] = field(default_factory=list)
    new_lines: list[str] = field(default_factory=list)
    is_end_of_file: bool = False


@dataclass
class FilePatch:
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
    """Parse a lenient Codex-style patch into file-level entries."""
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
```

- [ ] **Step 4: Run parser tests**

Run: `uv run pytest tests/unit/test_patch_engine.py -v`
Expected: all PASS.

- [ ] **Step 5: Format, lint, commit**

```bash
uv run ruff format astrbot/core/tools/computer_tools/patch_engine.py tests/unit/test_patch_engine.py
uv run ruff check astrbot/core/tools/computer_tools/patch_engine.py tests/unit/test_patch_engine.py
git add astrbot/core/tools/computer_tools/patch_engine.py tests/unit/test_patch_engine.py
git commit -m "feat(patch-engine): parse lenient codex-style patches"
```

---

### Task 4: Hunk Application Engine

**Files:**
- Modify: `astrbot/core/tools/computer_tools/patch_engine.py`
- Test: `tests/unit/test_patch_engine.py`

**Interfaces:**
- Consumes: `find_match_at_or_after`, `adjusted_replacement` from `edit_engine`
- Produces: `HunkApplyError(ValueError)` with `.hunk_index`; `apply_hunks_to_text(text, hunks) -> str`

- [ ] **Step 1: Write the failing tests**

```python
from astrbot.core.tools.computer_tools.patch_engine import (
    Hunk,
    HunkApplyError,
    apply_hunks_to_text,
)


def test_apply_exact_update():
    assert apply_hunks_to_text(
        "a\nb\nc\n", [Hunk(old_lines=["b"], new_lines=["B"])]
    ) == "a\nB\nc\n"


def test_apply_context_anchor_moves_cursor():
    text = "def foo():\n    x = 1\ndef bar():\n    x = 1\n"
    hunk = Hunk(context="def bar():", old_lines=["    x = 1"], new_lines=["    x = 2"])
    assert apply_hunks_to_text(text, [hunk]) == (
        "def foo():\n    x = 1\ndef bar():\n    x = 2\n"
    )


def test_apply_repeated_hunks_hit_successive_occurrences():
    hunks = [
        Hunk(old_lines=["x = 1"], new_lines=["x = 2"]),
        Hunk(old_lines=["x = 1"], new_lines=["x = 3"]),
    ]
    assert apply_hunks_to_text("x = 1\nx = 1\n", hunks) == "x = 2\nx = 3\n"


def test_apply_falls_back_to_fuzzy_indentation():
    hunk = Hunk(old_lines=["        return 1"], new_lines=["return 2"])
    assert apply_hunks_to_text("def f():\n    return 1\n", [hunk]) == (
        "def f():\n    return 2\n"
    )


def test_apply_end_of_file_marker():
    hunk = Hunk(old_lines=["last"], new_lines=["second"], is_end_of_file=True)
    assert apply_hunks_to_text("a\nlast\n", [hunk]) == "a\nsecond\n"


def test_apply_insert_with_context():
    hunk = Hunk(context="a", old_lines=[], new_lines=["middle"])
    assert apply_hunks_to_text("a\nb\n", [hunk]) == "a\nmiddle\nb\n"


def test_apply_insert_without_context_appends():
    assert apply_hunks_to_text("a\n", [Hunk(new_lines=["b"])]) == "a\nb\n"


def test_apply_raises_with_hunk_index():
    with pytest.raises(HunkApplyError) as exc:
        apply_hunks_to_text("a\n", [Hunk(old_lines=["zzz"], new_lines=["b"])])
    assert exc.value.hunk_index == 0
    assert "context not found" in str(exc.value)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_patch_engine.py -k "apply_" -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the engine**

Add to `patch_engine.py`:

```python
from .edit_engine import adjusted_replacement, find_match_at_or_after

_UNICODE_MAP = str.maketrans(
    {
        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
        "\u2014": "-", "\u2015": "-", "\u2212": "-",
        "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
        "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
        "\u00a0": " ", "\u3000": " ",
    }
)


class HunkApplyError(ValueError):
    """Raised when a hunk cannot be located or applied."""

    def __init__(self, message: str, hunk_index: int) -> None:
        super().__init__(message)
        self.hunk_index = hunk_index


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
    starts = _line_starts(text)
    for tier in range(4):
        for pos in starts:
            if pos < start:
                continue
            candidate = text[pos:].split("\n")[: len(expected)]
            if len(candidate) < len(expected):
                continue
            if _lines_equal(candidate, expected, tier):
                return pos, "\n".join(candidate)
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
                adjusted = adjusted_replacement(
                    text, idx, old_text, old_text, new_text
                )
                return (
                    text[:idx] + adjusted + text[idx + len(old_text) :],
                    idx + len(adjusted),
                )

    found = _find_exact_ladder(text, old_text, cursor)
    if found is None:
        found = find_match_at_or_after(text, old_text, cursor)
    if found is None:
        first_line = hunk.old_lines[0] if hunk.old_lines else ""
        raise HunkApplyError(
            f"context not found: {first_line!r}", hunk_index=index
        )

    idx, matched = found
    adjusted = adjusted_replacement(text, idx, old_text, matched, new_text)
    insertion = text[:idx] + adjusted + text[idx + len(matched) :]
    return insertion, idx + len(adjusted)


def apply_hunks_to_text(text: str, hunks: list[Hunk]) -> str:
    """Apply hunks using a monotonically advancing cursor."""
    cursor = 0
    for index, hunk in enumerate(hunks):
        text, cursor = _apply_one(text, hunk, cursor, index)
    return text
```

- [ ] **Step 4: Run engine tests**

Run: `uv run pytest tests/unit/test_patch_engine.py -k "apply_" -v`
Expected: all PASS.

- [ ] **Step 5: Format, lint, commit**

```bash
uv run ruff format astrbot/core/tools/computer_tools/patch_engine.py tests/unit/test_patch_engine.py
uv run ruff check astrbot/core/tools/computer_tools/patch_engine.py tests/unit/test_patch_engine.py
git add astrbot/core/tools/computer_tools/patch_engine.py tests/unit/test_patch_engine.py
git commit -m "feat(patch-engine): apply hunks with cursor and fuzzy fallback"
```

---

### Task 5: Planning, Destructive Detection, AST Validation

**Files:**
- Modify: `astrbot/core/tools/computer_tools/patch_engine.py`
- Test: `tests/unit/test_patch_engine.py`

**Interfaces:**
- Produces:
  - `FilePlan(path, kind, original, target_original, new_bytes, move_to, destructive_reason, diff, added, removed)`
  - `PatchPlan(files, destructive, added, removed)`
  - `plan_patch(patches, read: Callable[[str], bytes | None]) -> PatchPlan`

- [ ] **Step 1: Write the failing tests**

```python
from astrbot.core.tools.computer_tools.patch_engine import plan_patch


def test_plan_update_counts_and_diff():
    files = parse_patch("*** Update File: a.py\n-x = 1\n+x = 2\n+y = 3\n")
    plan = plan_patch(files, lambda p: b"x = 1\n")
    assert plan.files[0].new_bytes == b"x = 2\ny = 3\n"
    assert plan.added == 2
    assert plan.removed == 1
    assert "+x = 2" in plan.files[0].diff


def test_plan_add_over_existing_is_destructive():
    plan = plan_patch(parse_patch("*** Add File: a.txt\n+x\n"), lambda p: b"old\n")
    assert plan.files[0].destructive_reason == "overwrite_add"


def test_plan_delete_is_destructive():
    plan = plan_patch(parse_patch("*** Delete File: a.txt\n"), lambda p: b"old\n")
    assert plan.files[0].destructive_reason == "delete"


def test_plan_move_to_existing_is_destructive():
    files = parse_patch("*** Update File: a.txt\n*** Move to: b.txt\n-x\n+y\n")
    plan = plan_patch(
        files, lambda p: b"old\n" if p in ("a.txt", "b.txt") else None
    )
    assert plan.files[0].destructive_reason == "overwrite_move_target"
    assert plan.files[0].target_original == b"old\n"


def test_plan_rejects_missing_update_target():
    with pytest.raises(PatchParseError, match="does not exist"):
        plan_patch(
            parse_patch("*** Update File: a.txt\n-x\n+y\n"), lambda p: None
        )


def test_plan_rejects_python_syntax_regression():
    files = parse_patch("*** Update File: a.py\n-def f():\n-    return 1\n+def f(:\n")
    with pytest.raises(PatchParseError, match="Python syntax"):
        plan_patch(files, lambda p: b"def f():\n    return 1\n")


def test_plan_preserves_crlf_and_bom():
    files = parse_patch("*** Update File: a.txt\n-a\n+b\n")
    plan = plan_patch(files, lambda p: b"\xef\xbb\xbfa\r\n")
    assert plan.files[0].new_bytes == b"\xef\xbb\xbfb\r\n"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_patch_engine.py -k "plan_" -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement planning**

Add to `patch_engine.py`:

```python
import ast
import difflib
from collections.abc import Callable
from dataclasses import dataclass

from .edit_engine import build_unified_diff, decode_text_bytes, encode_text_bytes


@dataclass
class FilePlan:
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
    files: list[FilePlan]
    destructive: list[FilePlan]
    added: int
    removed: int


def _count_changes(old: str, new: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.unified_diff(
        old.splitlines(), new.splitlines(), lineterm=""
    ):
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
    """Compute new contents and destructive flags without touching disk."""
    plans: list[FilePlan] = []
    for entry in patches:
        original = read(entry.path)
        if entry.kind in ("update", "delete") and original is None:
            raise PatchParseError(f"file does not exist: {entry.path}")

        target_original = (
            read(entry.move_to) if entry.move_to is not None else None
        )
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
            new_text = apply_hunks_to_text(old_text, entry.hunks)
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
```

- [ ] **Step 4: Run planner tests**

Run: `uv run pytest tests/unit/test_patch_engine.py -k "plan_" -v`
Expected: all PASS.

- [ ] **Step 5: Format, lint, commit**

```bash
uv run ruff format astrbot/core/tools/computer_tools/patch_engine.py tests/unit/test_patch_engine.py
uv run ruff check astrbot/core/tools/computer_tools/patch_engine.py tests/unit/test_patch_engine.py
git add astrbot/core/tools/computer_tools/patch_engine.py tests/unit/test_patch_engine.py
git commit -m "feat(patch-engine): plan file changes and detect destructive edits"
```

---

### Task 6: Tool Skeleton, Guards, Dry-Run, Confirmation

**Files:**
- Create: `astrbot/core/tools/computer_tools/patch_tool.py`
- Test: `tests/test_apply_patch_tool.py`

**Interfaces:**
- Consumes: `parse_patch`, `plan_patch`, `PatchParseError`; `fs` helpers
- Produces: `ApplyPatchTool` (`name = "astrbot_apply_patch"`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_apply_patch_tool.py`:

```python
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.tools.computer_tools import fs as fs_tools
from astrbot.core.tools.computer_tools.patch_tool import ApplyPatchTool


def _context(*, runtime="local", umo="qq:friend:user-1", mode="full"):
    holder = SimpleNamespace(
        get_config=lambda umo=None: {
            "provider_settings": {
                "computer_use_require_admin": False,
                "computer_use_runtime": runtime,
                "file_access_default_mode": mode,
            }
        }
    )
    event = SimpleNamespace(
        role="admin", unified_msg_origin=umo, get_sender_id=lambda: "user-1"
    )
    return ContextWrapper(context=SimpleNamespace(context=holder, event=event))


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    monkeypatch.setattr(
        fs_tools, "get_astrbot_workspaces_path", lambda: str(workspaces)
    )
    monkeypatch.setattr(
        fs_tools.fs_access,
        "sp",
        SimpleNamespace(
            session_get=AsyncMock(return_value=None),
            session_put=AsyncMock(),
        ),
    )
    ws = workspaces / "qq_friend_user-1"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.mark.asyncio
async def test_dry_run_changes_nothing(workspace):
    target = workspace / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    result = await ApplyPatchTool().call(
        _context(), patch="*** Update File: a.txt\n-old\n+new\n", dry_run=True
    )
    assert "Dry run" in result
    assert target.read_text(encoding="utf-8") == "old\n"


@pytest.mark.asyncio
async def test_delete_requires_confirm_but_dry_run_reports_it(workspace):
    target = workspace / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    result = await ApplyPatchTool().call(
        _context(), patch="*** Delete File: a.txt\n", dry_run=True
    )
    assert "confirm=true" in result
    assert target.exists()


@pytest.mark.asyncio
async def test_delete_without_confirm_does_not_write(workspace):
    target = workspace / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    result = await ApplyPatchTool().call(
        _context(), patch="*** Delete File: a.txt\n"
    )
    assert "NOT applied" in result
    assert target.exists()


@pytest.mark.asyncio
async def test_readonly_mode_rejects(workspace):
    target = workspace / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    result = await ApplyPatchTool().call(
        _context(mode="readonly"),
        patch="*** Update File: a.txt\n-old\n+new\n",
    )
    assert "readonly" in result.lower()
    assert target.read_text(encoding="utf-8") == "old\n"


@pytest.mark.asyncio
async def test_path_escape_rejected(workspace):
    result = await ApplyPatchTool().call(
        _context(), patch="*** Update File: ../outside.txt\n-a\n+b\n"
    )
    assert "denied" in result.lower() or "restricted" in result.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_apply_patch_tool.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the skeleton**

Create `patch_tool.py`:

```python
"""Builtin apply_patch tool: atomic multi-file patch application."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.tools import fs_access
from astrbot.core.tools.computer_tools.fs import (
    _READONLY_WRITE_ERROR,
    _is_forbidden_path,
    _is_user_blacklisted,
    _normalize_rw_path,
    _read_file_bytes,
    _write_guard,
)
from astrbot.core.tools.computer_tools.patch_engine import (
    PatchParseError,
    plan_patch,
    parse_patch,
)
from astrbot.core.tools.computer_tools.util import (
    is_local_runtime,
    workspace_root_for_context,
)
from astrbot.core.tools.registry import builtin_tool


@builtin_tool(config={"provider_settings.computer_use_runtime": ("local", "sandbox")})
@dataclass
class ApplyPatchTool(FunctionTool):
    """Apply a Codex-style patch atomically."""

    name: str = "astrbot_apply_patch"
    description: str = (
        "Apply a multi-file patch. Use for structural edits (add, delete, move) "
        "or changes spanning several files; for a single small edit prefer "
        "`astrbot_file_edit_tool`.\n\n"
        "Format:\n"
        "*** Begin Patch\n"
        "*** Update File: path\n"
        "-old line\n"
        "+new line\n"
        "*** Add File: path\n"
        "+content\n"
        "*** Delete File: path\n"
        "*** End Patch\n\n"
        "Context lines must be copied from the file. Destructive changes "
        "(delete, overwrite) are not applied unless `confirm=true`."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "patch": {"type": "string", "description": "Patch text."},
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview only; never write.",
                    "default": False,
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Required for destructive changes.",
                    "default": False,
                },
            },
            "required": ["patch"],
        }
    )

    async def call(
        self,
        context: ContextWrapper,
        patch: str,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> ToolExecResult:
        if fs_access.get_mode(context) is fs_access.FileAccessMode.READONLY:
            return _READONLY_WRITE_ERROR

        local_env = is_local_runtime(context)
        restricted, extra_roots = await _write_guard(context)
        umo = str(context.context.event.unified_msg_origin)
        workspace_root = (
            await workspace_root_for_context(context) if local_env else None
        )

        try:
            entries = parse_patch(patch)
        except PatchParseError as exc:
            return f"Error: invalid patch: {exc}"

        resolved: dict[str, str] = {}
        try:
            for entry in entries:
                resolved[entry.path] = _normalize_rw_path(
                    entry.path,
                    restricted=restricted,
                    local_env=local_env,
                    umo=umo,
                    write=True,
                    current_workspace_root=workspace_root,
                    extra_write_roots=extra_roots,
                )
                if entry.move_to is not None:
                    resolved[entry.move_to] = _normalize_rw_path(
                        entry.move_to,
                        restricted=restricted,
                        local_env=local_env,
                        umo=umo,
                        write=True,
                        current_workspace_root=workspace_root,
                        extra_write_roots=extra_roots,
                    )
        except (ValueError, PermissionError) as exc:
            return f"Error: {exc}"

        def read(path: str) -> bytes | None:
            real = resolved.get(path, path)
            try:
                return _read_file_bytes(real)
            except FileNotFoundError:
                return None

        try:
            plan = plan_patch(entries, read)
        except (PatchParseError, ValueError) as exc:
            return f"Error: patch cannot be applied: {exc}"

        blacklist = (
            context.context.context.get_config(umo=umo)
            .get("provider_settings", {})
            .get("file_remove_blacklist")
            or []
        )
        for entry in entries:
            if entry.kind != "delete" and entry.move_to is None:
                continue
            source = Path(resolved[entry.path])
            if _is_forbidden_path(source) or _is_user_blacklisted(source, blacklist):
                return f"Error: deleting or moving {entry.path} is not allowed."

        if plan.destructive and not dry_run and not confirm:
            return self._confirmation(plan)
        if dry_run:
            return self._preview(plan)
        return await self._commit(plan, resolved, context=context, local_env=local_env)

    @staticmethod
    def _confirmation(plan) -> str:
        lines = ["This patch contains destructive changes and was NOT applied."]
        for item in plan.destructive:
            lines.append(f"- {item.destructive_reason}: {item.path}")
        lines.append("Retry with confirm=true after the user approves.")
        return "\n".join(lines)

    @staticmethod
    def _preview(plan) -> str:
        lines = [
            f"Dry run: no files were written. {len(plan.files)} files, "
            f"+{plan.added} -{plan.removed}"
        ]
        for item in plan.files:
            suffix = f" -> {item.move_to}" if item.move_to else ""
            lines.append(f"{item.kind.upper()} {item.path}{suffix}")
        if plan.destructive:
            lines.extend(["", "Destructive changes (need confirm=true when applying):"])
            for item in plan.destructive:
                lines.append(f"- {item.destructive_reason}: {item.path}")
        body = "\n\n".join(item.diff for item in plan.files if item.diff)
        if body:
            lines.extend(["", body[:40000]])
        return "\n".join(lines)

    async def _commit(self, plan, resolved, *, context, local_env) -> str:
        return "Error: commit path not implemented"
```

> The `_commit` stub keeps Task 6 independently testable (preview/guards only). Task 7 replaces it.

- [ ] **Step 4: Run tool tests**

Run: `uv run pytest tests/test_apply_patch_tool.py -v`
Expected: all PASS (the four preview/guard tests; the delete-without-confirm test hits `_confirmation`).

- [ ] **Step 5: Format, lint, commit**

```bash
uv run ruff format astrbot/core/tools/computer_tools/patch_tool.py tests/test_apply_patch_tool.py
uv run ruff check astrbot/core/tools/computer_tools/patch_tool.py tests/test_apply_patch_tool.py
git add astrbot/core/tools/computer_tools/patch_tool.py tests/test_apply_patch_tool.py
git commit -m "feat(patch-tool): add apply_patch skeleton with dry-run and confirm gate"
```

---

### Task 7: Atomic Commit, Backups, Rollback (Local)

**Files:**
- Modify: `astrbot/core/tools/computer_tools/patch_tool.py`
- Test: `tests/test_apply_patch_tool.py`

**Interfaces:**
- Consumes: `edit_engine.get_file_lock`, `edit_history.get_history_manager`, `fs._record_changed_file`
- Produces: `ApplyPatchTool._commit(plan, resolved, *, context, local_env) -> str`

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path

from astrbot.core.tools.computer_tools import patch_tool as patch_tool_module


@pytest.mark.asyncio
async def test_commit_writes_update(workspace):
    target = workspace / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    result = await ApplyPatchTool().call(
        _context(), patch="*** Update File: a.txt\n-old\n+new\n"
    )
    assert "Applied patch" in result
    assert target.read_text(encoding="utf-8") == "new\n"


@pytest.mark.asyncio
async def test_commit_add_and_delete(workspace):
    doomed = workspace / "old.txt"
    doomed.write_text("bye\n", encoding="utf-8")
    result = await ApplyPatchTool().call(
        _context(),
        patch=(
            "*** Add File: new.txt\n+hi\n"
            "*** Delete File: old.txt\n"
        ),
        confirm=True,
    )
    assert "Applied patch" in result
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "hi\n"
    assert not doomed.exists()


@pytest.mark.asyncio
async def test_failed_second_write_rolls_back_first(workspace, monkeypatch):
    first = workspace / "a.txt"
    second = workspace / "b.txt"
    first.write_text("a\n", encoding="utf-8")
    second.write_text("b\n", encoding="utf-8")

    original_write = patch_tool_module._write_file_bytes

    def flaky_write(path, data):
        if Path(path) == second:
            raise OSError("disk full")
        return original_write(path, data)

    monkeypatch.setattr(patch_tool_module, "_write_file_bytes", flaky_write)
    result = await ApplyPatchTool().call(
        _context(),
        patch=(
            "*** Update File: a.txt\n-a\n+A\n"
            "*** Update File: b.txt\n-b\n+B\n"
        ),
    )
    assert "No files were modified" in result
    assert first.read_text(encoding="utf-8") == "a\n"
    assert second.read_text(encoding="utf-8") == "b\n"


@pytest.mark.asyncio
async def test_changed_since_plan_aborts(workspace, monkeypatch):
    target = workspace / "a.txt"
    target.write_text("old\n", encoding="utf-8")

    original_plan = patch_tool_module.plan_patch

    def plan_then_mutate(entries, read):
        result = original_plan(entries, read)
        target.write_text("externally changed\n", encoding="utf-8")
        return result

    monkeypatch.setattr(patch_tool_module, "plan_patch", plan_then_mutate)
    result = await ApplyPatchTool().call(
        _context(), patch="*** Update File: a.txt\n-old\n+new\n"
    )
    assert "changed since plan" in result
    assert target.read_text(encoding="utf-8") == "externally changed\n"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_apply_patch_tool.py -k "commit or rollback or changed_since" -v`
Expected: FAIL (`commit path not implemented`).

- [ ] **Step 3: Add module-level I/O helpers**

At module level in `patch_tool.py` (add `import asyncio` to the import block first):

```python
from astrbot.core.tools.computer_tools.edit_engine import get_file_lock
from astrbot.core.tools.computer_tools.edit_history import get_history_manager
from astrbot.core.tools.computer_tools.fs import _record_changed_file


def _write_file_bytes(path: str, data: bytes) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def _remove_file(path: str) -> None:
    Path(path).unlink()
```

- [ ] **Step 4: Implement `_commit` and `_success`**

Replace the stub with:

```python
    async def _commit(self, plan, resolved, *, context, local_env) -> str:
        if not local_env:
            return "Error: sandbox runtime is not supported yet"

        async def read_at(path: str) -> bytes | None:
            return await asyncio.to_thread(self._read_optional, path)

        async def write_at(path: str, data: bytes) -> None:
            await asyncio.to_thread(_write_file_bytes, path, data)

        async def remove_at(path: str) -> None:
            await asyncio.to_thread(_remove_file, path)

        writes = []
        moves = []
        deletes = []
        for item in plan.files:
            real = resolved[item.path]
            if item.kind == "delete":
                deletes.append((item, real))
            elif item.move_to is not None:
                moves.append((item, real, resolved[item.move_to]))
            elif item.new_bytes is not None:
                writes.append((item, real))

        paths = sorted(
            {real for _, real in writes}
            | {src for _, src, _ in moves}
            | {dst for _, _, dst in moves}
            | {real for _, real in deletes}
        )
        locks = []
        committed: list[tuple[str, bytes | None]] = []
        try:
            for path in paths:
                lock = get_file_lock(path)
                await lock.acquire()
                locks.append(lock)

            for item, real in writes:
                if await read_at(real) != item.original:
                    raise RuntimeError(f"file changed since plan: {item.path}")
            for item, src, dst in moves:
                if await read_at(src) != item.original:
                    raise RuntimeError(f"file changed since plan: {item.path}")
                if await read_at(dst) != item.target_original:
                    raise RuntimeError(
                        f"file changed since plan: {item.move_to}"
                    )
            for item, real in deletes:
                if await read_at(real) != item.original:
                    raise RuntimeError(f"file changed since plan: {item.path}")

            history = get_history_manager()
            for item, real in writes:
                backup_id = ""
                if item.original is not None:
                    entry = await asyncio.to_thread(
                        history.save_backup, real, item.original, "local"
                    )
                    backup_id = entry.id
                await write_at(real, item.new_bytes or b"")
                committed.append((real, item.original))
                _record_changed_file(
                    context,
                    real,
                    "created" if item.original is None else "edit",
                    "local",
                    backup_id,
                )
            for item, src, dst in moves:
                backup_id = ""
                if item.target_original is not None:
                    entry = await asyncio.to_thread(
                        history.save_backup, dst, item.target_original, "local"
                    )
                    backup_id = entry.id
                await write_at(dst, item.new_bytes or b"")
                committed.append((dst, item.target_original))
                _record_changed_file(context, dst, "write", "local", backup_id)
            for item, real in deletes:
                entry = await asyncio.to_thread(
                    history.save_backup, real, item.original or b"", "local"
                )
                await remove_at(real)
                committed.append((real, item.original))
                _record_changed_file(context, real, "remove", "local", entry.id)
            for item, src, _ in moves:
                entry = await asyncio.to_thread(
                    history.save_backup, src, item.original or b"", "local"
                )
                await remove_at(src)
                committed.append((src, item.original))
                _record_changed_file(context, src, "remove", "local", entry.id)

            return self._success(plan)
        except Exception as exc:
            failures = await self._rollback(committed, write_at, remove_at)
            if failures:
                return (
                    f"Error: {exc}\nRollback failed for: {', '.join(failures)}\n"
                    "Manual cleanup required."
                )
            return f"Error: {exc}\nNo files were modified."
        finally:
            for lock in reversed(locks):
                lock.release()

    @staticmethod
    def _read_optional(path: str) -> bytes | None:
        try:
            return _read_file_bytes(path)
        except FileNotFoundError:
            return None

    @staticmethod
    async def _rollback(committed, write_at, remove_at) -> list[str]:
        failures: list[str] = []
        for path, original in reversed(committed):
            try:
                if original is None:
                    await remove_at(path)
                else:
                    await write_at(path, original)
            except Exception:
                failures.append(path)
        return failures

    @staticmethod
    def _success(plan) -> str:
        lines = [
            f"Applied patch: {len(plan.files)} files changed "
            f"(+{plan.added} -{plan.removed})"
        ]
        for item in plan.files:
            suffix = f" -> {item.move_to}" if item.move_to else ""
            lines.append(f"{item.kind.upper()} {item.path}{suffix}")
        body = "\n\n".join(item.diff for item in plan.files if item.diff)
        if body:
            lines.extend(["", body[:40000]])
        return "\n".join(lines)
```

- [ ] **Step 5: Run tool tests**

Run: `uv run pytest tests/test_apply_patch_tool.py -v`
Expected: all PASS.

- [ ] **Step 6: Format, lint, commit**

```bash
uv run ruff format astrbot/core/tools/computer_tools/patch_tool.py tests/test_apply_patch_tool.py
uv run ruff check astrbot/core/tools/computer_tools/patch_tool.py tests/test_apply_patch_tool.py
git add astrbot/core/tools/computer_tools/patch_tool.py tests/test_apply_patch_tool.py
git commit -m "feat(patch-tool): commit atomically with backups and rollback"
```

---

### Task 8: Sandbox Runtime Support

**Files:**
- Modify: `astrbot/core/tools/computer_tools/patch_tool.py`
- Modify: `astrbot/core/tools/computer_tools/fs.py`
- Test: `tests/test_apply_patch_tool.py`

**Interfaces:**
- Consumes: `computer_client.get_booter`, `fs._sandbox_read_bytes`, `fs._sandbox_write_bytes`
- Produces: `fs._sandbox_write_bytes(..., *, create_parents=False)`; `patch_tool._sandbox_remove_file`

- [ ] **Step 1: Write the failing sandbox test**

```python
@pytest.mark.asyncio
async def test_sandbox_update_uses_remote_io(monkeypatch):
    writes = []

    class FakeBooter:
        pass

    async def fake_read(sb, path):
        return b"old\n"

    async def fake_write(sb, path, data, *, create_parents=False):
        writes.append((path, data, create_parents))

    async def fake_remove(sb, path):
        writes.append((path, None, False))

    monkeypatch.setattr(patch_tool_module, "_sandbox_read_bytes", fake_read)
    monkeypatch.setattr(patch_tool_module, "_sandbox_write_bytes", fake_write)
    monkeypatch.setattr(patch_tool_module, "_sandbox_remove_file", fake_remove)
    monkeypatch.setattr(
        patch_tool_module,
        "get_booter",
        AsyncMock(return_value=FakeBooter()),
    )

    result = await ApplyPatchTool().call(
        _context(runtime="sandbox"),
        patch="*** Update File: /remote/a.txt\n-old\n+new\n",
    )
    assert "Applied patch" in result
    assert writes == [("/remote/a.txt", b"new\n", True)]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_apply_patch_tool.py -k sandbox -v`
Expected: FAIL ("sandbox runtime is not supported yet").

- [ ] **Step 3: Extend `_sandbox_write_bytes`**

In `fs.py`, change the signature to:

```python
async def _sandbox_write_bytes(
    sb, path: str, data: bytes, *, create_parents: bool = False
) -> None:
```

and inject a mkdir block into the script's `try` section:

```python
    mkdir_block = ""
    if create_parents:
        mkdir_block = (
            "    import os\n"
            "    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)\n"
        )
    code = (
        "import base64 as _b64, sys\n"
        f"path = {path!r}\n"
        f"b64 = {b64_data!r}\n"
        "try:\n"
        f"{mkdir_block}"
        "    _raw = _b64.b64decode(b64)\n"
        "    with open(path, 'wb') as _f:\n"
        "        _f.write(_raw)\n"
        "    print('ok', end='')\n"
        "except Exception as _e:\n"
        "    print('ERROR:' + str(_e), end='', file=sys.stderr)\n"
        "    sys.exit(1)\n"
    )
```

- [ ] **Step 4: Add sandbox adapters and wire `_commit`**

In `patch_tool.py`, import:

```python
from astrbot.core.computer.computer_client import get_booter
from astrbot.core.tools.computer_tools.fs import (
    _sandbox_read_bytes,
    _sandbox_write_bytes,
)
```

Add:

```python
async def _sandbox_remove_file(sb, path: str) -> None:
    code = (
        "import os, sys\n"
        f"path = {path!r}\n"
        "try:\n"
        "    os.remove(path)\n"
        "except Exception as e:\n"
        "    print('ERROR:' + str(e), end='', file=sys.stderr)\n"
        "    sys.exit(1)\n"
    )
    result = await sb.python.exec(code, timeout=30)
    if not result.get("success", False):
        raise OSError(f"Failed to remove sandbox file: {path}")
```

Change `_commit`'s signature to accept `umo`, and replace the local-only preamble:

```python
        if local_env:
            async def read_at(path: str) -> bytes | None:
                return await asyncio.to_thread(self._read_optional, path)

            async def write_at(path: str, data: bytes) -> None:
                await asyncio.to_thread(_write_file_bytes, path, data)

            async def remove_at(path: str) -> None:
                await asyncio.to_thread(_remove_file, path)

            runtime = "local"
        else:
            sb = await get_booter(context.context.context, umo)

            async def read_at(path: str) -> bytes | None:
                try:
                    return await _sandbox_read_bytes(sb, path)
                except OSError:
                    return None

            async def write_at(path: str, data: bytes) -> None:
                await _sandbox_write_bytes(sb, path, data, create_parents=True)

            async def remove_at(path: str) -> None:
                await _sandbox_remove_file(sb, path)

            runtime = "sandbox"
```

Replace every hard-coded `"local"` in `_record_changed_file` / `save_backup` with `runtime`.

Update the call site in `call()`:

```python
        return await self._commit(
            plan, resolved, context=context, local_env=local_env, umo=umo
        )
```

- [ ] **Step 5: Run the full tool suite**

Run: `uv run pytest tests/test_apply_patch_tool.py -v`
Expected: all PASS (local + sandbox).

- [ ] **Step 6: Format, lint, commit**

```bash
uv run ruff format astrbot/core/tools/computer_tools/patch_tool.py astrbot/core/tools/computer_tools/fs.py tests/test_apply_patch_tool.py
uv run ruff check astrbot/core/tools/computer_tools/patch_tool.py astrbot/core/tools/computer_tools/fs.py tests/test_apply_patch_tool.py
git add astrbot/core/tools/computer_tools/patch_tool.py astrbot/core/tools/computer_tools/fs.py tests/test_apply_patch_tool.py
git commit -m "feat(patch-tool): support sandbox runtime io"
```

---

### Task 9: Registration and Default Configuration

**Files:**
- Modify: `astrbot/core/tools/computer_tools/__init__.py`
- Modify: `astrbot/core/config/default.py`
- Test: `tests/test_apply_patch_tool.py`

- [ ] **Step 1: Write the failing registration test**

```python
from astrbot.core.tools.registry import get_builtin_tool_class


def test_tool_is_registered():
    assert get_builtin_tool_class("astrbot_apply_patch") is ApplyPatchTool


def test_tool_is_in_inherent_list():
    from astrbot.core.config.default import DEFAULT_CONFIG

    inherent = DEFAULT_CONFIG["subagent_orchestrator"]["dynamic_agents"][
        "tools_inherent"
    ]
    assert "astrbot_apply_patch" in inherent
```

> The path `subagent_orchestrator.dynamic_agents.tools_inherent` is the dynamic-subagent
> inheritance list that already contains `astrbot_file_edit_tool`; verify it still exists
> before running, and fail loudly rather than silently skipping if the structure changed.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_apply_patch_tool.py -k "registered or inherent" -v`
Expected: FAIL.

- [ ] **Step 3: Register**

In `computer_tools/__init__.py`:

```python
from .patch_tool import ApplyPatchTool
```

Add `"ApplyPatchTool"` to `__all__` in alphabetical position.

In `astrbot/core/config/default.py`, append `"astrbot_apply_patch"` to the `tools_inherent` list immediately after `"astrbot_file_edit_tool"`.

- [ ] **Step 4: Run registration and regression**

Run:
```bash
uv run pytest tests/test_apply_patch_tool.py -v
uv run pytest tests/test_computer_fs_tools.py -v
```
Expected: all PASS.

- [ ] **Step 5: Format, lint, commit**

```bash
uv run ruff format astrbot/core/tools/computer_tools/__init__.py astrbot/core/config/default.py
uv run ruff check astrbot/core/tools/computer_tools/__init__.py astrbot/core/config/default.py
git add astrbot/core/tools/computer_tools/__init__.py astrbot/core/config/default.py tests/test_apply_patch_tool.py
git commit -m "feat(patch-tool): register astrbot_apply_patch builtin"
```

---

### Task 10: Full Verification

**Files:** any file needing fixes from the checks below

- [ ] **Step 1: Run the related suites**

```bash
uv run pytest tests/unit/test_edit_engine.py tests/unit/test_patch_engine.py tests/test_apply_patch_tool.py tests/test_computer_fs_tools.py -v
```
Expected: all PASS. If a pre-existing `test_computer_fs_tools.py` failure appears, confirm it also fails on `HEAD` before attributing it to this change.

- [ ] **Step 2: Run the unit suite**

```bash
uv run pytest tests/unit -q
```
Expected: no new failures.

- [ ] **Step 3: Format and lint the touched surface**

```bash
uv run ruff format astrbot/core/tools/computer_tools astrbot/core/config/default.py tests/unit/test_edit_engine.py tests/unit/test_patch_engine.py tests/test_apply_patch_tool.py
uv run ruff check astrbot/core/tools/computer_tools astrbot/core/config/default.py tests/unit/test_edit_engine.py tests/unit/test_patch_engine.py tests/test_apply_patch_tool.py
```

- [ ] **Step 4: Smoke-test registration**

```bash
uv run python -c "from astrbot.core.tools.registry import get_builtin_tool_class; print(get_builtin_tool_class('astrbot_apply_patch').name)"
```
Expected: `astrbot_apply_patch`.

- [ ] **Step 5: Commit final fixes**

```bash
git add astrbot/core/tools/computer_tools astrbot/core/config/default.py tests/unit/test_edit_engine.py tests/unit/test_patch_engine.py tests/test_apply_patch_tool.py
git commit -m "test(patch-tool): finalize apply_patch coverage"
```

---

## Self-Review

**Spec coverage:**
- 模块划分 → Tasks 1–3, 6
- 输入格式与宽松解析 → Task 3
- 引擎算法（游标 / 阶梯 / 锚点 / EOF / 插入 / 缩进）→ Task 4
- 计划 / 破坏性 / AST / diff / 统计 → Task 5
- 参数 / 门控 / dry-run → Task 6
- 原子提交 / 备份 / 回滚 / 写入顺序 / 重读比对 → Task 7
- 沙箱 → Task 8
- 注册与默认配置 → Task 9
- 全量验证 → Task 10

**Placeholder scan:** no TBD/TODO. Task 6 intentionally ships `_commit` as a stub that returns an error; Task 7 replaces it with the real implementation, and no other task depends on the stub's behavior.

**Type consistency:** `Hunk`, `FilePatch`, `FilePlan`, `PatchPlan`, `parse_patch`, `apply_hunks_to_text`, `plan_patch`, `adjusted_replacement`, `replace_match_at` signatures are identical across tasks. The `tools_inherent` path in Task 9 was verified against `default.py` (`subagent_orchestrator.dynamic_agents.tools_inherent`), and the readonly-mode assertion matches the real `_READONLY_WRITE_ERROR` wording ("readonly").
