"""Agent team node execution registry tests (spec §2.3).

Covers `AgentTeamExecutionRegistry` / `NodeExecutionBinding`: per-turn token
issuance, umo-scoped resolution (forged/foreign token defense), and
runner-owned lifecycle (resolve does not unregister; unregister is idempotent).
"""

import re

from astrbot.core.agent_team_execution import (
    AgentTeamExecutionRegistry,
    NodeExecutionBinding,
)

UMO = "webchat:FriendMessage:conv-1"


def make_binding(**overrides) -> NodeExecutionBinding:
    """Build a valid binding with defaults, overridable per test.

    Args:
        **overrides: Field values replacing the defaults.

    Returns:
        A `NodeExecutionBinding` instance.
    """
    fields = {
        "run_id": "run-1",
        "team_id": "team-1",
        "member_id": "coder",
        "node_id": "node-1",
        "umo": UMO,
        "owner_username": "admin",
        "config_id": "cfg-default",
    }
    fields.update(overrides)
    return NodeExecutionBinding(**fields)


def test_register_resolve_roundtrip():
    binding = make_binding(persona_id="p1", tools=["t1"], skills=["s1"])
    token = AgentTeamExecutionRegistry.register(binding)
    try:
        # uuid4 hex token
        assert re.fullmatch(r"[0-9a-f]{32}", token)
        resolved = AgentTeamExecutionRegistry.resolve(token, umo=UMO)
        assert resolved is binding
        assert resolved.run_id == "run-1"
        assert resolved.team_id == "team-1"
        assert resolved.member_id == "coder"
        assert resolved.node_id == "node-1"
        assert resolved.owner_username == "admin"
        assert resolved.config_id == "cfg-default"
        assert resolved.persona_id == "p1"
        assert resolved.tools == ["t1"]
        assert resolved.skills == ["s1"]
    finally:
        AgentTeamExecutionRegistry.unregister(token)


def test_register_issues_distinct_tokens():
    token_a = AgentTeamExecutionRegistry.register(make_binding())
    token_b = AgentTeamExecutionRegistry.register(make_binding(node_id="node-2"))
    try:
        assert token_a != token_b
    finally:
        AgentTeamExecutionRegistry.unregister(token_a)
        AgentTeamExecutionRegistry.unregister(token_b)


def test_resolve_unknown_token_returns_none():
    assert AgentTeamExecutionRegistry.resolve("0" * 32) is None


def test_resolve_umo_mismatch_returns_none():
    token = AgentTeamExecutionRegistry.register(make_binding())
    try:
        # A token forged/replayed from another conversation must not resolve.
        assert (
            AgentTeamExecutionRegistry.resolve(token, umo="webchat:FriendMessage:other")
            is None
        )
    finally:
        AgentTeamExecutionRegistry.unregister(token)


def test_resolve_without_umo_skips_umo_check():
    token = AgentTeamExecutionRegistry.register(make_binding())
    try:
        resolved = AgentTeamExecutionRegistry.resolve(token)
        assert resolved is not None
        assert resolved.member_id == "coder"
    finally:
        AgentTeamExecutionRegistry.unregister(token)


def test_resolve_does_not_unregister():
    """Resolution must not consume the token — the runner owns the lifecycle."""
    token = AgentTeamExecutionRegistry.register(make_binding())
    try:
        for _ in range(2):
            resolved = AgentTeamExecutionRegistry.resolve(token, umo=UMO)
            assert resolved is not None
    finally:
        AgentTeamExecutionRegistry.unregister(token)


def test_unregister_idempotent():
    token = AgentTeamExecutionRegistry.register(make_binding())
    AgentTeamExecutionRegistry.unregister(token)
    AgentTeamExecutionRegistry.unregister(token)  # must not raise
    assert AgentTeamExecutionRegistry.resolve(token) is None
