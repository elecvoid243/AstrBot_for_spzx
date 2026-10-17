"""Regression tests: ``astrbot_apply_patch`` must reach the runtime tool set.

作者: elecvoid243
时间: 2026-10-18 00:58 (CST)

背景: 该工具在注册表与 ``DEFAULT_CONFIG`` 中均存在, 但运行时注入点
(``_apply_local_env_tools`` / ``_get_runtime_computer_tools``) 是硬编码枚举,
遗漏了 ``ApplyPatchTool``, 导致模型在 schema 层面看不到该工具。
原注册测试只校验注册表与默认配置, 无法捕捉此类遗漏, 故单列本文件。
"""

from __future__ import annotations

from types import SimpleNamespace

from astrbot.core.agent.tool import ToolSet
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.astr_main_agent import _apply_local_env_tools
from astrbot.core.provider.func_tool_manager import FunctionToolManager

_PATCH_TOOL_NAME = "astrbot_apply_patch"


class _StubPluginContext:
    """仅实现 ``_apply_local_env_tools`` 所需的两个入口。"""

    def get_llm_tool_manager(self) -> FunctionToolManager:
        return FunctionToolManager()

    def get_config(self, umo: str | None = None) -> dict:
        return {"provider_settings": {}}


def test_local_env_tools_include_apply_patch() -> None:
    req = SimpleNamespace(func_tool=ToolSet(), system_prompt="")
    _apply_local_env_tools(req, _StubPluginContext(), "webchat:FriendMessage:test")
    names = {tool.name for tool in req.func_tool.tools}
    assert _PATCH_TOOL_NAME in names


def test_local_runtime_computer_tools_include_apply_patch() -> None:
    tools = FunctionToolExecutor._get_runtime_computer_tools(
        "local", FunctionToolManager()
    )
    assert _PATCH_TOOL_NAME in tools


def test_sandbox_runtime_computer_tools_include_apply_patch() -> None:
    tools = FunctionToolExecutor._get_runtime_computer_tools(
        "sandbox", FunctionToolManager()
    )
    assert _PATCH_TOOL_NAME in tools


def test_tool_set_accepts_apply_patch() -> None:
    """ToolSet.add_tool 不应因缺注册而静默丢弃该工具。"""
    tool_set = ToolSet()
    tool_set.add_tool(FunctionToolManager().get_builtin_tool(_PATCH_TOOL_NAME))
    assert [tool.name for tool in tool_set.tools] == [_PATCH_TOOL_NAME]
