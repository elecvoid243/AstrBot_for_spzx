"""Tests for the Codex-style patch parser, engine, and planner."""

from __future__ import annotations

import pytest

from astrbot.core.tools.computer_tools.patch_engine import (
    Hunk,
    HunkApplyError,
    PatchParseError,
    apply_hunks_to_text,
    parse_patch,
    plan_patch,
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


def test_apply_exact_update():
    assert (
        apply_hunks_to_text("a\nb\nc\n", [Hunk(old_lines=["b"], new_lines=["B"])])
        == "a\nB\nc\n"
    )


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
    files = parse_patch("*** Update File: a.txt\n*** Move to: b.txt\n-old\n+new\n")
    plan = plan_patch(files, lambda p: b"old\n" if p in ("a.txt", "b.txt") else None)
    assert plan.files[0].destructive_reason == "overwrite_move_target"
    assert plan.files[0].target_original == b"old\n"


def test_plan_rejects_missing_update_target():
    with pytest.raises(PatchParseError, match="does not exist"):
        plan_patch(parse_patch("*** Update File: a.txt\n-x\n+y\n"), lambda p: None)


def test_plan_rejects_python_syntax_regression():
    files = parse_patch("*** Update File: a.py\n-def f():\n-    return 1\n+def f(:\n")
    with pytest.raises(PatchParseError, match="Python syntax"):
        plan_patch(files, lambda p: b"def f():\n    return 1\n")


def test_plan_preserves_crlf_and_bom():
    files = parse_patch("*** Update File: a.txt\n-a\n+b\n")
    plan = plan_patch(files, lambda p: b"\xef\xbb\xbfa\r\n")
    assert plan.files[0].new_bytes == b"\xef\xbb\xbfb\r\n"


def test_apply_rejects_tab_in_new_when_file_uses_spaces():
    hunk = Hunk(old_lines=["    return 1"], new_lines=["\treturn 2"])
    with pytest.raises(ValueError, match="indentation style"):
        apply_hunks_to_text("def f():\n    return 1\n", [hunk])


def test_apply_rejects_tab_when_unindented_context_hides_common_indent():
    hunk = Hunk(
        old_lines=["def f():", "    return 1"],
        new_lines=["def f():", "\treturn 2"],
    )
    with pytest.raises(ValueError, match="indentation style"):
        apply_hunks_to_text("def f():\n    return 1\n", [hunk])


def test_apply_rejects_tab_insertion_into_spaces_file():
    hunk = Hunk(context="def f():", new_lines=["\tpass"])
    with pytest.raises(ValueError, match="indentation style"):
        apply_hunks_to_text("def f():\n    return 1\n", [hunk])


def test_apply_matches_near_end_of_large_file():
    text = "".join(f"line_{i} = {i}\n" for i in range(20000))
    hunk = Hunk(old_lines=["line_19998 = 19998"], new_lines=["line_19998 = 0"])
    out = apply_hunks_to_text(text, [hunk])
    assert "line_19998 = 0" in out
    assert out.count("line_19998 = 0") == 1


def test_parse_error_message_includes_line_number():
    with pytest.raises(PatchParseError) as exc:
        parse_patch("*** Update File: a.txt\n?bad\n")
    assert "(line 2)" in str(exc.value)


def test_hunk_error_message_includes_file_and_hunk_number():
    files = parse_patch("*** Update File: a.txt\n-zzz\n+y\n")
    with pytest.raises(PatchParseError) as exc:
        plan_patch(files, lambda p: b"old\n")
    message = str(exc.value)
    assert "a.txt" in message
    assert "hunk #1" in message
