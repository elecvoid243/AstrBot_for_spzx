"""Tests for the file tool-group selector."""

from __future__ import annotations

from types import SimpleNamespace

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


class FakeMgr:
    def get_builtin_tool(self, tool):
        name = tool if isinstance(tool, str) else tool.name
        return SimpleNamespace(name=name)


def test_runtime_tools_legacy_vs_patch():
    from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor

    legacy = FunctionToolExecutor._get_runtime_computer_tools(
        "local", FakeMgr(), mode="legacy"
    )
    assert "astrbot_file_edit_tool" in legacy
    assert "astrbot_apply_patch" not in legacy

    patch = FunctionToolExecutor._get_runtime_computer_tools(
        "local", FakeMgr(), mode="patch"
    )
    assert "astrbot_apply_patch" in patch
    assert "astrbot_file_edit_tool" not in patch


def test_sandbox_patch_includes_apply_patch():
    from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor

    tools = FunctionToolExecutor._get_runtime_computer_tools(
        "sandbox", FakeMgr(), mode="patch"
    )
    assert "astrbot_apply_patch" in tools
    assert "astrbot_file_write_tool" not in tools


def test_local_assembly_uses_mode():
    from astrbot.core.astr_main_agent import _apply_local_env_tools
    from astrbot.core.provider.entities import ProviderRequest

    class FakePluginCtx:
        def get_config(self, umo=None):
            return {"provider_settings": {"file_tool_mode": "patch"}}

        def get_llm_tool_manager(self):
            return FakeMgr()

    req = ProviderRequest()
    _apply_local_env_tools(req, FakePluginCtx(), "umo")
    names = {tool.name for tool in req.func_tool.tools}
    assert "astrbot_apply_patch" in names
    assert "astrbot_file_edit_tool" not in names
