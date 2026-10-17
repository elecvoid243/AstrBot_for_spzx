"""Tests for the Codex-style patch parser, engine, and planner."""

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
        "*** Update File: a.txt\n*** Move to: b.txt\n*** Update File: b.txt\n-x\n+y\n"
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
