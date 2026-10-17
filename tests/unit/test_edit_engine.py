"""Characterization and primitive tests for the file edit engine."""

from __future__ import annotations

import pytest

from astrbot.core.tools.computer_tools.edit_engine import (
    adjusted_replacement,
    bytes_edit_file,
    decode_text_bytes,
    encode_text_bytes,
    find_match_at_or_after,
    iter_match_positions,
    replace_match_at,
    robust_replace,
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
    assert (
        adjusted_replacement(content, idx, old, matched, "return 2") == "    return 2"
    )


def test_replace_match_at_splices_adjusted_text():
    content = "def f():\n    return 1\n"
    old = "        return 1"
    idx, matched = find_match_at_or_after(content, old, 0)
    assert replace_match_at(content, idx, old, matched, "return 2") == (
        "def f():\n    return 2\n"
    )


# ---------------------------------------------------------------------------
# Characterization tests: lock the current behavior before refactoring.
# ---------------------------------------------------------------------------


def test_robust_replace_simple():
    content, count = robust_replace("a\nb\nc\n", "b", "B")
    assert content == "a\nB\nc\n"
    assert count == 1


def test_robust_replace_requires_unique_match():
    with pytest.raises(ValueError, match="multiple matches"):
        robust_replace("x\nx\n", "x", "y")


def test_robust_replace_all():
    content, count = robust_replace("x\nx\n", "x", "y", replace_all=True)
    assert content == "y\ny\n"
    assert count == 2


def test_robust_replace_missing_reports_not_found():
    with pytest.raises(ValueError, match="Could not find oldString"):
        robust_replace("a\n", "zzz", "b")


def test_robust_replace_identical_rejected():
    with pytest.raises(ValueError, match="identical"):
        robust_replace("a\n", "a", "a")


def test_robust_replace_tolerates_whitespace_drift():
    content, count = robust_replace("    value = 1\n", "    value = 1   ", "value = 2")
    assert count == 1
    assert "value = 2" in content


def test_robust_replace_strict_indent_uses_file_indent():
    content, _ = robust_replace(
        "def f():\n    return 1\n",
        "        return 1",
        "return 2",
    )
    assert content == "def f():\n    return 2\n"


def test_bytes_edit_file_preserves_crlf():
    raw = b"a\r\nb\r\n"
    out, result = bytes_edit_file(raw, "b", "B")
    assert out == b"a\r\nB\r\n"
    assert result.success is True
    assert result.replacements == 1


def test_bytes_edit_file_preserves_bom():
    raw = b"\xef\xbb\xbfa\nb\n"
    out, _ = bytes_edit_file(raw, "b", "B")
    assert out == b"\xef\xbb\xbfa\nB\n"


def test_bytes_edit_file_reports_contents_and_diff():
    raw = b"a\nb\n"
    _, result = bytes_edit_file(raw, "b", "B")
    assert result.old_content == "a\nb\n"
    assert result.new_content == "a\nB\n"
    assert "+B" in result.diff


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
