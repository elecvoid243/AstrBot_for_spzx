"""Tests for the file tool-group selector."""

from __future__ import annotations

from astrbot.core.tools.file_tool_mode import (
    file_tool_allowed,
    file_tool_names,
    resolve_file_tool_mode,
)


def test_default_is_legacy():
    assert resolve_file_tool_mode({}) == "legacy"
    assert resolve_file_tool_mode(None) == "legacy"


def test_patch_mode_and_invalid_fallback():
    assert resolve_file_tool_mode({"file_tool_mode": "patch"}) == "patch"
    assert resolve_file_tool_mode({"file_tool_mode": "bogus"}) == "legacy"


def test_file_tool_names():
    assert file_tool_names("legacy") == (
        "astrbot_file_write_tool",
        "astrbot_file_edit_tool",
    )
    assert file_tool_names("patch") == ("astrbot_apply_patch",)


def test_file_tool_allowed():
    assert file_tool_allowed("astrbot_apply_patch", "legacy") is False
    assert file_tool_allowed("astrbot_file_edit_tool", "patch") is False
    assert file_tool_allowed("astrbot_file_edit_tool", "legacy") is True
    assert file_tool_allowed("astrbot_file_read_tool", "patch") is True


def test_default_config_has_mode():
    from astrbot.core.config.default import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["provider_settings"]["file_tool_mode"] == "legacy"
