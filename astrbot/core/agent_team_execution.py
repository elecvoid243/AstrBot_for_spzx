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
event is rejected. The registry is deliberately dumb: it only
stores and retrieves bindings. Lifecycle ownership (register on node dispatch,
unregister when the runner finishes the turn) lives in the team runner, never
in `resolve`.
"""

import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.dashboard.services.agent_team_ports import TeamPorts


class ProcessPersistenceError(RuntimeError):
    """Stop dependent execution when a complete observation cannot be saved."""


class CheckpointRecoveryError(ProcessPersistenceError):
    """Block the original attempt until its saved capabilities can be restored."""


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
    provider_id: str | None = None
    """Provider override for this member turn, if any."""
    max_steps: int | None = None
    """Tool-loop step limit override (agent_runner.config.misc.max_steps)."""
    tool_call_timeout: float | None = None
    """Tool call timeout override in seconds."""
    kb_names: list[str] | None = None
    """Knowledge base names override (kb_names)."""
    context_length: int | None = None
    """Context length override (request-level model/compression cap)."""
    turn_id: str | None = None
    """Turn identity shared by execution, process events, and transcript rows."""
    message_id: str | None = None
    """Message identity used to cancel this invocation."""
    source_umo: str | None = None
    """Original transport source before routing to the member's conversation."""
    profile_snapshot: dict | None = None
    """Frozen v2 pipeline settings; absent for legacy team invocations."""
    persona_snapshot: dict | None = None
    """Frozen persona content; current authorization is checked before dispatch."""
    conversation_id: str | None = None
    """V2 member context identity saved before the first model invocation."""
    record_process: Callable[[str, str, dict], Awaitable[None]] | None = None
    """Durable structured observation sink; it does not resume execution."""
    save_checkpoint: Callable[[dict], Awaitable[None]] | None = None
    """Persist execution state before dependent model or tool work proceeds."""
    checkpoint: dict | None = None
    """Original attempt state to restore after rebuilding the normal main pipeline."""
    admit_work: Callable[[], Awaitable[None]] | None = None
    """Durable whole-run admission before fresh or restored external invocations."""
    pending_messages: Callable[[], Awaitable[list[dict]]] | None = None
    """Read member deliveries at the runner's serialized model-request boundary."""
    recovery_error: CheckpointRecoveryError | None = None
    """Restore failure retained when the main pipeline renders an error reply."""


current_team_execution: ContextVar[NodeExecutionBinding | None] = ContextVar(
    "current_team_execution", default=None
)


async def execute_team_turn(
    *,
    ports: "TeamPorts",
    member: dict,
    run_id: str,
    team_id: str,
    username: str,
    turn_id: str,
    text: str,
    execution: dict,
    node_id: str | None = None,
    context: str | None = None,
    config_checker: Callable[[str], bool] | None = None,
    on_delivered: Callable[[str], Awaitable[None]] | None = None,
    on_event: Callable[[dict], None] | None = None,
    record_process: Callable[[str, str, dict], Awaitable[None]] | None = None,
    save_checkpoint: Callable[[dict], Awaitable[None]] | None = None,
    checkpoint: dict | None = None,
    admit_work: Callable[[], Awaitable[None]] | None = None,
    pending_messages: Callable[[], Awaitable[list[dict]]] | None = None,
) -> tuple[str, list]:
    """Execute a member or coordinator turn through the existing pipeline ports.

    Args:
        ports: Existing injectable delivery and result collection boundary.
        member: Member identity and conversation, including its role prompt.
        run_id: Owning run.
        team_id: Owning team.
        username: Actual initiating user, never an implicit administrator.
        turn_id: Correlation shared with process events and transcript rows.
        text: Task input.
        execution: Effective member configuration with task overrides applied.
        node_id: Workflow node, when applicable.
        context: Temporary task framing.
        config_checker: Optional check that the selected profile still exists.
        on_delivered: Persists or publishes the dispatched message association.
        on_event: Receives interactive waiting events from collection.
        record_process: Saves complete model responses and original tool results.
        save_checkpoint: Saves the built-in runner's durable execution boundaries.
        checkpoint: Saved effective context and response for this original attempt.
        admit_work: Wait for durable whole-run permission before external work.
        pending_messages: Scoped deliveries to commit with the next fresh context.

    Returns:
        Full reply text and existing chat message parts.

    Raises:
        ValueError: When the selected configuration profile was deleted.
        CheckpointRecoveryError: Saved capabilities cannot currently be restored.
        Exception: When dispatch or collection fails. The invocation is stopped.
    """
    from astrbot.core.utils.active_event_registry import active_event_registry

    config_id = execution.get("config_id") or None
    if config_id and config_checker is not None and not config_checker(config_id):
        raise ValueError("Configuration profile has been deleted.")
    binding = NodeExecutionBinding(
        run_id=run_id,
        team_id=team_id,
        member_id=member["member_id"],
        node_id=node_id,
        umo=member["umo"],
        owner_username=username,
        config_id=config_id,
        persona_id=execution.get("persona_id") or None,
        provider_id=execution.get("provider_id") or None,
        tools=execution.get("tools"),
        skills=execution.get("skills"),
        max_steps=execution.get("max_steps"),
        tool_call_timeout=execution.get("tool_call_timeout"),
        kb_names=execution.get("kb_names"),
        context_length=execution.get("context_length"),
        turn_id=turn_id,
        message_id=execution.get("message_id"),
        profile_snapshot=execution.get("profile_snapshot"),
        persona_snapshot=execution.get("persona_snapshot"),
        conversation_id=member.get("conversation_id"),
        record_process=record_process,
        save_checkpoint=save_checkpoint,
        checkpoint=checkpoint,
        admit_work=admit_work,
        pending_messages=pending_messages,
    )
    token = AgentTeamExecutionRegistry.register(binding)
    framing = (
        "\n\n".join(value for value in (member.get("system_prompt"), context) if value)
        or None
    )
    try:
        delivery_options = {"execution_token": token}
        if binding.message_id is not None:
            delivery_options["message_id"] = binding.message_id
        binding.message_id = await ports.deliver(
            member["session_id"], text, framing, **delivery_options
        )
        if on_delivered is not None:
            await on_delivered(binding.message_id)
        if on_event is not None:
            result = await ports.collect(
                member["session_id"],
                binding.message_id,
                member["member_id"],
                on_event=on_event,
            )
        else:
            result = await ports.collect(
                member["session_id"], binding.message_id, member["member_id"]
            )
        if binding.recovery_error is not None:
            raise binding.recovery_error
        return result
    except BaseException:
        if binding.message_id is not None:
            active_event_registry.request_agent_stop_all(
                binding.umo, message_id=binding.message_id
            )
        raise
    finally:
        AgentTeamExecutionRegistry.unregister(token)


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
            # The WebChat adapter includes the sender in the transport source;
            # team definitions retain the raw conversation UMO. Accept only
            # the equivalent source for this binding's actual owner.
            prefix = "webchat:FriendMessage:"
            if not binding.umo.startswith(prefix) or umo != (
                f"{prefix}webchat!{binding.owner_username}!{binding.umo[len(prefix) :]}"
            ):
                return None
        return binding

    @classmethod
    def unregister(cls, token: str) -> None:
        """Remove the entry for `token`. Missing entries are ignored (idempotent).

        Args:
            token: The execution token to clean up.
        """
        _EXECUTIONS.pop(token, None)
