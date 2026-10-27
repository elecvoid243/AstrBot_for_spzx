"""Patch injection requires patch mode; registration alone does not select it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrbot.core.agent.tool import ToolSet
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.astr_main_agent import _apply_local_env_tools
from astrbot.core.provider.func_tool_manager import FunctionToolManager

_PATCH_TOOL_NAME = "astrbot_apply_patch"


class _StubPluginContext:
    """Supply the two context methods used by local tool assembly."""

    def get_llm_tool_manager(self) -> FunctionToolManager:
        """Return a real builtin tool manager.

        Returns:
            A manager with the registered builtin tools.
        """
        return FunctionToolManager()

    def get_config(self, umo: str | None = None) -> dict:
        """Select patch mode explicitly.

        Args:
            umo: Optional message origin.

        Returns:
            The behavior settings for this assembly test.
        """
        return {"provider_settings": {"file_tool_mode": "patch"}}


def test_local_env_tools_include_apply_patch() -> None:
    """The local assembly exposes patch when patch mode is selected."""
    req = SimpleNamespace(func_tool=ToolSet(), system_prompt="")
    _apply_local_env_tools(req, _StubPluginContext(), "webchat:FriendMessage:test")
    names = {tool.name for tool in req.func_tool.tools}
    assert _PATCH_TOOL_NAME in names


def test_local_runtime_computer_tools_include_apply_patch() -> None:
    """Local runtime dispatch supports the selected patch capability."""
    tools = FunctionToolExecutor._get_runtime_computer_tools(
        "local", FunctionToolManager(), mode="patch"
    )
    assert _PATCH_TOOL_NAME in tools


def test_sandbox_runtime_computer_tools_include_apply_patch() -> None:
    """Sandbox runtime dispatch supports the selected patch capability."""
    tools = FunctionToolExecutor._get_runtime_computer_tools(
        "sandbox", FunctionToolManager(), mode="patch"
    )
    assert _PATCH_TOOL_NAME in tools


def test_tool_set_accepts_apply_patch() -> None:
    """Adding a registered patch tool preserves its identity."""
    tool_set = ToolSet()
    tool_set.add_tool(FunctionToolManager().get_builtin_tool(_PATCH_TOOL_NAME))
    assert [tool.name for tool in tool_set.tools] == [_PATCH_TOOL_NAME]


@pytest.mark.parametrize("runtime", ["local", "sandbox"])
def test_default_runtime_keeps_legacy_group(runtime: str) -> None:
    """Default mode does not silently inject the alternate file capability.

    Args:
        runtime: Supported computer environment.
    """
    tools = FunctionToolExecutor._get_runtime_computer_tools(
        runtime, FunctionToolManager()
    )
    assert _PATCH_TOOL_NAME not in tools
    assert "astrbot_file_write_tool" in tools
    assert "astrbot_file_edit_tool" in tools
