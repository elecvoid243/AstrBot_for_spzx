from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrbot.core.agent.tool import ToolSet
from astrbot.core.astr_main_agent import _apply_subagent_manager_tools
from astrbot.core.subagent_manager import SubAgentManager
from astrbot.core.subagent_tools import ORCHESTRATE_TASKS_TOOL


@pytest.mark.asyncio
async def test_legacy_dag_configuration_cannot_enable_or_register_tool(monkeypatch):
    # configure mutates class-level settings; restore them after the test.
    for name in (
        "_dag_enabled",
        "_max_subagent_count",
        "_auto_cleanup_per_turn",
        "_shared_context_enabled",
        "_history_enabled",
        "_shared_context_maxlen",
        "_execution_timeout",
        "_rule_prompt",
        "_time_prompt_enabled",
        "_timezone",
        "_default_provider_id",
        "_context_inherit_mode",
        "_tools_inherent",
        "_tools_blacklist",
        "_mcp_inherent",
        "_mcp_blacklist",
    ):
        monkeypatch.setattr(SubAgentManager, name, getattr(SubAgentManager, name))
    monkeypatch.setattr(SubAgentManager, "set_history_enabled", MagicMock())
    monkeypatch.setattr(SubAgentManager, "set_shared_context_enabled", MagicMock())
    monkeypatch.setattr(SubAgentManager, "get_session", MagicMock(return_value=None))
    toolset = ToolSet()
    toolset.add_tool(ORCHESTRATE_TASKS_TOOL)
    req = SimpleNamespace(func_tool=toolset, system_prompt="")
    event = SimpleNamespace(unified_msg_origin="test")
    config = {
        "subagent_orchestrator": {
            "main_enable": True,
            "dag_enabled": True,
            "dynamic_agents": {"enabled": True},
        }
    }

    await _apply_subagent_manager_tools(config, req, event, MagicMock())

    assert not SubAgentManager._dag_enabled
    assert req.func_tool.get_tool("orchestrate_tasks") is None
    assert "DAG Orchestration" not in req.system_prompt
    assert await ORCHESTRATE_TASKS_TOOL.call(None, tasks=[{}]) == (
        "Error: DAG orchestration is disabled."
    )
