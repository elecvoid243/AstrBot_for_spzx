"""Agent team run service: member runner_config merge into the execution
binding (spec §3.4).

Covers `_merged_member_execution` precedence (node execution wins over
member-level config, member runner_config fills the gaps) and the
`NodeExecutionBinding` the DAGRunner registers per turn: every new field
threads through, and default nodes (no execution block, no member config)
still dispatch with no token exactly as before.
"""

import pytest

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from astrbot.dashboard.services.agent_team_run_service import (
    DAGRunner,
    RunEventBus,
    _merged_member_execution,
)

RUNNER_CONFIG = {
    "failure_policy": "pause",
    "reply_timeout": 5.0,
    "max_parallel": 5,
    "inject_max_length": 4000,
}


def member_with_config(**runner_config) -> dict:
    """One single-session member carrying top-level pins plus runner_config.

    Args:
        **runner_config: The member's runner_config block keys (already in
            the normalized shape `update_member` persists).

    Returns:
        The member dict as stored on the team row.
    """
    member = {
        "member_id": "mA",
        "name": "甲",
        "session_id": "conv-0",
        "umo": "webchat:FriendMessage:conv-0",
        "persona_id": None,
        "provider_id": None,
        "system_prompt": None,
    }
    if runner_config:
        member["runner_config"] = runner_config
    return member


def scripted_ports_of(deliver, collect, events: list) -> TeamPorts:
    return TeamPorts(
        deliver=deliver,
        collect=collect,
        is_busy=lambda sid: False,
        emit=events.append,
    )


# ---------------------------------------------------------------------------
# Merge helper precedence (spec §3.4)
# ---------------------------------------------------------------------------


def test_merge_member_execution_precedence():
    merged = _merged_member_execution(
        {
            "persona_id": "persona_a",
            "provider_id": "prov_a",
            "runner_config": {"max_steps": 12, "tools": ["t1"]},
        },
        node_execution={"persona_id": "persona_b", "tools": ["t2"]},
    )
    assert merged == {
        "persona_id": "persona_b",  # node wins
        "provider_id": "prov_a",  # member top-level
        "max_steps": 12,  # member runner_config
        "tools": ["t2"],  # node wins
    }


def test_merge_member_execution_without_node_block_keeps_member():
    merged = _merged_member_execution(
        {
            "persona_id": "persona_a",
            "provider_id": "prov_a",
            "runner_config": {"config_id": "conf0", "max_steps": 12},
        },
        node_execution=None,
    )
    assert merged == {
        "persona_id": "persona_a",
        "provider_id": "prov_a",
        "config_id": "conf0",
        "max_steps": 12,
    }


def test_merge_member_execution_without_config_is_all_none():
    """A default member still yields persona/provider keys (None); the runner
    guards on non-None values so no token is registered for default nodes."""
    merged = _merged_member_execution(
        {"persona_id": None, "provider_id": None, "name": "甲"}, None
    )
    assert merged == {"persona_id": None, "provider_id": None}


# ---------------------------------------------------------------------------
# DAGRunner wiring: merged profile lands on the registered binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_node_merges_member_runner_config_under_node(tmp_path):
    """Node execution wins; member runner_config fills the gaps (spec §3.4)."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    captured: dict = {}

    async def deliver(session_id, text, context=None, execution_token=None):
        captured["token"] = execution_token
        captured["binding"] = AgentTeamExecutionRegistry.resolve(
            execution_token, umo="webchat:FriendMessage:conv-0"
        )
        return "mid-1"

    async def collect(session_id, message_id, member_id=None, on_event=None):
        return "回复", []

    members = [
        member_with_config(
            config_id="conf0",
            max_steps=12,
            tools=["t1"],
        )
    ]
    members[0]["persona_id"] = "persona_a"
    members[0]["provider_id"] = "prov_a"
    runner = DAGRunner(
        run_id="rmerge",
        team_id="team-1",
        graph={
            "nodes": [
                {
                    "id": "n1",
                    "member_id": "mA",
                    "task": "t",
                    "execution": {"persona_id": "persona_b", "tools": ["t2"]},
                }
            ],
            "edges": [],
        },
        config=RUNNER_CONFIG,
        members=members,
        ports=scripted_ports_of(deliver, collect, []),
        db=db,
        bus=RunEventBus(),
        username="alice",
        run_input="x",
    )
    await runner.run()

    assert runner.status == "completed"
    binding = captured["binding"]
    assert binding is not None
    assert binding.config_id == "conf0"  # member runner_config
    assert binding.persona_id == "persona_b"  # node wins
    assert binding.provider_id == "prov_a"  # member top-level
    assert binding.max_steps == 12  # member runner_config
    assert binding.tools == ["t2"]  # node wins
    # Fields neither side set stay None.
    assert binding.skills is None
    assert binding.tool_call_timeout is None
    assert binding.kb_names is None
    assert binding.context_length is None


@pytest.mark.asyncio
async def test_execute_node_registers_member_runner_config_without_node_block(
    tmp_path,
):
    """A node without an execution block still binds member-level config."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    captured: dict = {}

    async def deliver(session_id, text, context=None, execution_token=None):
        captured["token"] = execution_token
        captured["binding"] = AgentTeamExecutionRegistry.resolve(
            execution_token, umo="webchat:FriendMessage:conv-0"
        )
        return "mid-1"

    async def collect(session_id, message_id, member_id=None, on_event=None):
        return "回复", []

    members = [
        member_with_config(
            config_id="conf0",
            tools=["t1"],
            skills=["s1"],
            max_steps=12,
            tool_call_timeout=30.5,
            kb_names=["kb1"],
            context_length=8000,
        )
    ]
    members[0]["persona_id"] = "persona_a"
    members[0]["provider_id"] = "prov_a"
    runner = DAGRunner(
        run_id="rmember",
        team_id="team-1",
        graph={"nodes": [{"id": "n1", "member_id": "mA", "task": "t"}], "edges": []},
        config=RUNNER_CONFIG,
        members=members,
        ports=scripted_ports_of(deliver, collect, []),
        db=db,
        bus=RunEventBus(),
        username="alice",
        run_input="x",
    )
    await runner.run()

    assert runner.status == "completed"
    binding = captured["binding"]
    assert binding is not None
    assert binding.config_id == "conf0"
    assert binding.persona_id == "persona_a"  # member top-level, gap filled
    assert binding.provider_id == "prov_a"
    assert binding.tools == ["t1"]
    assert binding.skills == ["s1"]
    assert binding.max_steps == 12
    assert binding.tool_call_timeout == 30.5
    assert binding.kb_names == ["kb1"]
    assert binding.context_length == 8000


@pytest.mark.asyncio
async def test_execute_node_default_node_dispatches_no_token(tmp_path):
    """No execution block + no member config: no token, no binding (as before)."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    captured: dict = {}

    async def deliver(session_id, text, context=None, execution_token=None):
        captured["token"] = execution_token
        return "mid-1"

    async def collect(session_id, message_id, member_id=None, on_event=None):
        return "回复", []

    runner = DAGRunner(
        run_id="rdefault",
        team_id="team-1",
        graph={"nodes": [{"id": "n1", "member_id": "mA", "task": "t"}], "edges": []},
        config=RUNNER_CONFIG,
        members=[member_with_config()],
        ports=scripted_ports_of(deliver, collect, []),
        db=db,
        bus=RunEventBus(),
        username="alice",
        run_input="x",
    )
    await runner.run()

    assert runner.status == "completed"
    assert captured["token"] is None


@pytest.mark.asyncio
async def test_execute_node_member_config_id_prefails_deleted_profile(tmp_path):
    """A member-runner config_id that fails the checker fails the node before
    dispatch, exactly like a node-level config_id (spec §2.3/§3.4)."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    delivered: list = []
    events: list = []

    async def deliver(session_id, text, context=None, execution_token=None):
        delivered.append(session_id)
        return "mid-1"

    async def collect(session_id, message_id, member_id=None, on_event=None):
        return "回复", []

    runner = DAGRunner(
        run_id="rcfgmember",
        team_id="team-1",
        graph={"nodes": [{"id": "n1", "member_id": "mA", "task": "t"}], "edges": []},
        config=RUNNER_CONFIG,
        members=[member_with_config(config_id="cfg-gone")],
        ports=scripted_ports_of(deliver, collect, events),
        db=db,
        bus=RunEventBus(),
        username="alice",
        run_input="x",
        config_checker=lambda cid: cid == "cfg-live",
    )
    await runner.run()

    state = runner.node_states["n1"]
    assert state["status"] == "failed"
    assert state["error"] == "配置档案已删除"
    assert delivered == []  # failed before the busy-wait, never delivered
