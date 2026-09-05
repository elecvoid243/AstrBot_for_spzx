"""Per-turn node execution registry for agent teams (spec §2.3).

When an agent team node dispatches a member turn, it issues a short-lived
execution token bound to a `NodeExecutionBinding` (the run, team, member, node,
umo, and the config/persona/tools/skills profile the node must execute with).
The token travels with the event it produces; the EventBus resolves it before
the usual umo-based routing so the turn runs on the binding's own
PipelineScheduler instead of the sender conversation's default one.

Trust model: tokens are in-process secrets. `resolve` refuses to hand out a
binding whose registered umo does not match the requesting event's umo, so a
token replayed or forged into another conversation resolves to nothing and the
event falls back to default routing. The registry is deliberately dumb: it only
stores and retrieves bindings. Lifecycle ownership (register on node dispatch,
unregister when the runner finishes the turn) lives in the team runner, never
in `resolve`.
"""

import uuid
from dataclasses import dataclass


@dataclass
class NodeExecutionBinding:
    """Execution profile a dispatched team node turn must run with."""

    run_id: str
    """The agent team run the turn belongs to."""
    team_id: str
    """The team definition the member belongs to."""
    member_id: str
    """The team member whose agent executes the turn."""
    node_id: str | None
    """The plan node this turn executes, if the run tracks one."""
    umo: str
    """The unified message origin the token is scoped to."""
    owner_username: str
    """The username that owns the run (authorization boundary)."""
    config_id: str | None
    """The config profile id whose PipelineScheduler should execute the turn."""
    persona_id: str | None = None
    """The persona override for this member, if any."""
    tools: list[str] | None = None
    """The tool names enabled for this member, if restricted."""
    skills: list[str] | None = None
    """The skill names enabled for this member, if restricted."""


# Registry storage keyed by execution token. Module-level on purpose: the
# process runs a single event loop, so a plain dict is sufficient (same
# rationale as _TOOLS in astrbot/core/agent_team_tools.py).
_EXECUTIONS: dict[str, NodeExecutionBinding] = {}


class AgentTeamExecutionRegistry:
    """Registry mapping per-turn execution tokens to node execution bindings.

    The registry is deliberately dumb: it only issues, stores, and retrieves
    bindings keyed by opaque tokens. Lifecycle ownership (register on node
    dispatch, unregister when the runner finishes the turn) lives in the team
    runner; the EventBus resolves without unregistering.
    """

    @classmethod
    def register(cls, binding: NodeExecutionBinding) -> str:
        """Register `binding` under a fresh token.

        Args:
            binding: The execution profile to bind to the new token.

        Returns:
            A uuid4 hex token that resolves to `binding` on this process.
        """
        token = uuid.uuid4().hex
        _EXECUTIONS[token] = binding
        return token

    @classmethod
    def resolve(
        cls, token: str, *, umo: str | None = None
    ) -> NodeExecutionBinding | None:
        """Resolve `token` to its binding, enforcing the umo scope.

        Args:
            token: The execution token carried by the event.
            umo: The unified message origin of the requesting event. When
                provided and mismatched with the registered binding's umo, the
                token is treated as forged/foreign and not resolved.

        Returns:
            The registered binding, or None when the token is unknown or
            foreign to `umo`. Resolution never unregisters — the runner owns
            the token lifecycle.
        """
        binding = _EXECUTIONS.get(token)
        if binding is None:
            return None
        if umo is not None and binding.umo != umo:
            return None
        return binding

    @classmethod
    def unregister(cls, token: str) -> None:
        """Remove the entry for `token`. Missing entries are ignored (idempotent).

        Args:
            token: The execution token to clean up.
        """
        _EXECUTIONS.pop(token, None)
