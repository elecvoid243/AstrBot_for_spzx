"""Context tools ship with the prompt that tells the model when to use them."""

from types import SimpleNamespace

from astrbot.core.agent.tool import ToolSet
from astrbot.core.astr_main_agent import _apply_context_tools
from astrbot.core.astr_main_agent_resources import CONTEXT_TOOLS_SYSTEM_PROMPT
from astrbot.core.provider.entities import ProviderRequest


class _ToolManager:
    """Return a stand-in tool per class; only the name reaches the tool set."""

    def get_builtin_tool(self, tool_cls):
        return SimpleNamespace(name=tool_cls.__name__, active=True)


def test_context_tools_add_the_usage_guidance():
    req = ProviderRequest(prompt="hi", contexts=[], func_tool=ToolSet())
    plugin_context = SimpleNamespace(get_llm_tool_manager=lambda: _ToolManager())

    _apply_context_tools(req, plugin_context)

    assert req.system_prompt == CONTEXT_TOOLS_SYSTEM_PROMPT
    assert {tool.name for tool in req.func_tool.tools} == {
        "NewContextTool",
        "HistoryListWindowsTool",
        "HistorySearchTool",
        "HistoryReadTool",
        "NotesListTool",
        "NotesReadTool",
        "NotesSearchTool",
        "NotesWriteTool",
    }


def test_context_tools_append_after_an_existing_prompt():
    req = ProviderRequest(prompt="hi", contexts=[], func_tool=ToolSet())
    req.system_prompt = "persona"
    plugin_context = SimpleNamespace(get_llm_tool_manager=lambda: _ToolManager())

    _apply_context_tools(req, plugin_context)

    assert req.system_prompt == "persona" + CONTEXT_TOOLS_SYSTEM_PROMPT
