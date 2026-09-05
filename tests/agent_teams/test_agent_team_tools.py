"""Agent team tool + registry tests (spec §6.4)."""

import pytest

from astrbot.core.agent_team_tools import (
    AgentTeamToolRegistry,
    TeamDispatchTool,
    TeamFinishTool,
    build_team_tools,
)

MEMBERS = ["主管", "写手", "审校"]


@pytest.mark.asyncio
async def test_dispatch_tool_validates_and_invokes_callback():
    pushed = []

    async def on_dispatch(assignments, notes):
        pushed.append((assignments, notes))

    tool = TeamDispatchTool(MEMBERS, on_dispatch)
    assert tool.name == "team_dispatch"
    result = await tool.call(
        None,
        assignments=[{"member": "写手", "task": "写初稿"}],
        notes="注意语气",
    )
    assert "Dispatched 1" in result
    assert pushed == [([{"member": "写手", "task": "写初稿"}], "注意语气")]

    # case-insensitive match
    result = await tool.call(
        None, assignments=[{"member": "写 手 ".strip(), "task": "x"}]
    )
    assert "Dispatched" in result or "写手" in result

    # unknown member -> error string listing valid names, callback NOT called
    result = await tool.call(None, assignments=[{"member": "不存在", "task": "x"}])
    assert "不存在" in result and "写手" in result
    assert len(pushed) == 1

    # malformed payloads -> error string, not an exception
    for bad in (None, [], "x", [{"member": "写手"}], [{"task": "no member"}]):
        result = await tool.call(None, assignments=bad)
        assert isinstance(result, str) and "Dispatched" not in result
    assert len(pushed) == 1


@pytest.mark.asyncio
async def test_finish_tool_and_factory():
    finished = []

    async def on_finish(summary):
        finished.append(summary)

    tool = TeamFinishTool(on_finish)
    assert tool.name == "team_finish"
    assert "finished" in (await tool.call(None, summary="完成")).lower()
    assert finished == ["完成"]
    bad = await tool.call(None, summary="  ")
    assert isinstance(bad, str) and "finished" not in bad.lower()

    tools = build_team_tools(MEMBERS, on_dispatch=None, on_finish=on_finish)
    assert {t.name for t in tools} == {"team_dispatch", "team_finish"}
    assert all(t.description for t in tools)
    assert "assignments" in tools[0].parameters["properties"] or any(
        "assignments" in t.parameters.get("properties", {}) for t in tools
    )


def test_registry_scoping():
    tools = build_team_tools(MEMBERS, on_dispatch=None, on_finish=None)
    AgentTeamToolRegistry.register("umo-a", tools)
    try:
        assert AgentTeamToolRegistry.get_tools("umo-a") == tools
        assert AgentTeamToolRegistry.get_tools("umo-b") == []
    finally:
        AgentTeamToolRegistry.unregister("umo-a")
    assert AgentTeamToolRegistry.get_tools("umo-a") == []
    AgentTeamToolRegistry.unregister("umo-a")  # idempotent
