"""Protect profile migration, concurrent dispatch and live tool authorization."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import mcp.types
import pytest

from astrbot.core.agent.agent import Agent
from astrbot.core.agent.handoff import HandoffTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.subagent_orchestrator import SubAgentOrchestrator
from astrbot.core.subagent_policy import current_subagent_policy


def test_legacy_policy_migrates_once_per_existing_profile():
    class Config(dict):
        def save_config(self):
            pass

    legacy = {
        "main_enable": True,
        "agents": [{"name": "reviewer", "enabled": True}],
        "dynamic_agents": {"max_subagent_count": 3},
    }
    profiles = {"default": Config(subagent_orchestrator=legacy), "review": Config()}
    SubAgentOrchestrator.migrate_profiles(profiles, legacy)
    agent_id = legacy["agents"][0]["id"]
    assert profiles["review"]["subagent_policy"]["agent_ids"] == [agent_id]
    profiles["default"]["subagent_policy"]["dynamic_agents"]["max_subagent_count"] = 9
    profiles["new"] = Config(subagent_policy=None)
    SubAgentOrchestrator.migrate_profiles(profiles, legacy)
    assert (
        profiles["review"]["subagent_policy"]["dynamic_agents"]["max_subagent_count"]
        == 3
    )
    assert (
        profiles["default"]["subagent_policy"]["dynamic_agents"]["max_subagent_count"]
        == 9
    )
    assert profiles["new"]["subagent_policy"] is None


def test_restart_preserves_the_one_time_migration_marker(tmp_path: Path):
    default_path = str(tmp_path / "cmd_config.json")
    default = AstrBotConfig(config_path=default_path)
    default["subagent_orchestrator"]["main_enable"] = True
    SubAgentOrchestrator.migrate_profiles(
        {"default": default}, default["subagent_orchestrator"]
    )
    assert default["subagent_policy"]["main_enable"] is True

    reopened = AstrBotConfig(config_path=default_path)
    imported = AstrBotConfig(config_path=str(tmp_path / "abconf_imported.json"))
    SubAgentOrchestrator.migrate_profiles(
        {"default": reopened, "imported": imported}, reopened["subagent_orchestrator"]
    )
    assert reopened["subagent_policy_migrated"] is True
    assert imported["subagent_policy"] is None


@pytest.mark.asyncio
async def test_concurrent_dispatches_keep_policy_but_recheck_live_tool_permissions(
    monkeypatch,
):
    entered = asyncio.Queue()
    release = asyncio.Event()
    observations = {}
    profiles = {
        "one": {
            "subagent_policy": {
                "main_enable": True,
                "agent_ids": ["reviewer"],
                "execution_timeout": 10,
            }
        },
        "two": {
            "subagent_policy": {
                "main_enable": True,
                "agent_ids": ["reviewer"],
                "execution_timeout": 20,
            }
        },
    }
    tool = HandoffTool(Agent(name="reviewer"))
    tool.subagent_config_id = "reviewer"
    ordinary_tool = FunctionTool(
        name="custom_tool", parameters={}, description="Fixture"
    )
    orchestrator = SubAgentOrchestrator(None, None)
    orchestrator.handoffs = [tool]
    context = SimpleNamespace(
        get_config=lambda umo: profiles[umo],
        provider_manager=SimpleNamespace(inst_map={}),
        subagent_orchestrator=orchestrator,
    )

    async def handoff(cls, tool, run_context, **tool_args):
        name = run_context.context.event.unified_msg_origin
        await entered.put(name)
        await release.wait()
        observations[name] = current_subagent_policy.get().execution_timeout
        child = ContextWrapper(
            context=SimpleNamespace(
                context=context,
                event=run_context.context.event,
                extra={"is_subagent": True},
            )
        )
        async for result in FunctionToolExecutor.execute(ordinary_tool, child):
            yield result

    async def local(cls, tool, run_context, **tool_args):
        yield mcp.types.CallToolResult(content=[])

    monkeypatch.setattr(FunctionToolExecutor, "_execute_handoff", classmethod(handoff))
    monkeypatch.setattr(FunctionToolExecutor, "_execute_local", classmethod(local))

    async def dispatch(name):
        event = SimpleNamespace(unified_msg_origin=name)
        run_context = ContextWrapper(
            context=SimpleNamespace(context=context, event=event)
        )
        return [
            result async for result in FunctionToolExecutor.execute(tool, run_context)
        ]

    tasks = [asyncio.create_task(dispatch(name)) for name in profiles]
    assert {await asyncio.wait_for(entered.get(), 5) for _ in tasks} == {"one", "two"}
    profiles["one"] = {
        "subagent_policy": {
            "dynamic_agents": {
                "tools_blacklist": ["custom_tool"],
            }
        }
    }
    release.set()
    one, two = await asyncio.gather(*tasks)
    assert observations == {"one": 10, "two": 20}
    assert one[0].isError and not two[0].isError
    assert (await dispatch("one"))[0].isError
