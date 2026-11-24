from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrbot.core.agent.tool import ToolSet
from astrbot.core.astr_main_agent import _apply_subagent_manager_tools
from astrbot.core.subagent_manager import SubAgentManager
from astrbot.core.subagent_tools import ORCHESTRATE_TASKS_TOOL


@pytest.mark.asyncio
async def test_legacy_dag_configuration_cannot_enable_or_register_tool(monkeypatch):
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auto_cleanup_per_turn", "notice_present"),
    [(True, True), (False, False)],
)
async def test_auto_cleanup_notice_follows_effective_policy(
    auto_cleanup_per_turn: bool, notice_present: bool
):
    """The injected prompt must describe the cleanup the policy will actually do."""
    orchestrator = MagicMock()
    orchestrator.available_handoffs.return_value = []
    req = SimpleNamespace(func_tool=ToolSet(), system_prompt="")
    event = SimpleNamespace(unified_msg_origin="test")
    config = {
        "subagent_policy": {
            "main_enable": True,
            "dynamic_agents": {
                "enabled": True,
                "auto_cleanup_per_turn": auto_cleanup_per_turn,
            },
        }
    }

    await _apply_subagent_manager_tools(config, req, event, orchestrator)

    assert ("auto-removed at the end of each turn" in req.system_prompt) is (
        notice_present
    )
