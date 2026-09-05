"""Agent team LLM tools and their per-umo registry (spec §6.4).

This module provides the coordinator-facing tools for agent team
auto-orchestration:

- `TeamDispatchTool` (`team_dispatch`): the coordinator assigns this round's
  tasks to the team members.
- `TeamFinishTool` (`team_finish`): the coordinator ends the run with a final
  summary once the overall goal is achieved.

`AgentTeamToolRegistry` scopes tool availability per conversation
(`unified_msg_origin`, "umo") under an if-and-only-if contract: the tools are
exposed to a conversation's coordinator LLM if and only if an agent team run is
currently active on that umo. The run lifecycle registers the tools on run
start and unregisters them on run end; the main agent build step merges the
registered tools into its toolset if and only if the registry holds an entry
for the request's umo, so unrelated conversations never see these tools.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool

DispatchCallback = Callable[[list[dict[str, str]], str | None], Awaitable[Any]]
"""Invoked on a valid dispatch with (assignments, notes)."""

FinishCallback = Callable[[str], Awaitable[Any]]
"""Invoked on a valid finish with the coordinator's final summary."""

# Registry storage keyed by unified_msg_origin (umo). Module-level on purpose:
# the process runs a single event loop, so a plain dict is sufficient (see the
# scoping contract in the module docstring).
_TOOLS: dict[str, list[FunctionTool]] = {}

_DISPATCH_PARAMETERS = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "member": {"type": "string"},
                    "task": {"type": "string"},
                },
                "required": ["member", "task"],
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["assignments"],
}

_FINISH_PARAMETERS = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
    },
    "required": ["summary"],
}


class AgentTeamToolRegistry:
    """Registry mapping each umo to the agent team tools active on it.

    The registry is deliberately dumb: it only stores and retrieves tool lists
    keyed by umo. Lifecycle ownership (register on run start, unregister on run
    end) lives in the team run management layer.
    """

    @classmethod
    def register(cls, umo: str, tools: list[FunctionTool]) -> None:
        """Register `tools` for `umo`, replacing any previous entry.

        Args:
            umo: The unified message origin of the conversation running the team.
            tools: The tools to expose to the coordinator LLM on this umo.
        """
        _TOOLS[umo] = tools

    @classmethod
    def unregister(cls, umo: str) -> None:
        """Remove the entry for `umo`. Missing entries are ignored (idempotent).

        Args:
            umo: The unified message origin to clean up.
        """
        _TOOLS.pop(umo, None)

    @classmethod
    def get_tools(cls, umo: str) -> list[FunctionTool]:
        """Return the tools registered for `umo`.

        Args:
            umo: The unified message origin to look up.

        Returns:
            A copy of the registered tool list; empty when nothing is
            registered for `umo`.
        """
        return list(_TOOLS.get(umo, []))


class TeamDispatchTool(FunctionTool):
    """LLM tool that dispatches this round's tasks to the team members."""

    def __init__(
        self,
        member_names: list[str],
        on_dispatch: DispatchCallback | None,
    ) -> None:
        """Create the dispatch tool for one team.

        Args:
            member_names: Names of the team members the coordinator may assign
                tasks to. Matching is case-insensitive.
            on_dispatch: Callback invoked with the validated
                (assignments, notes) on success.
        """
        super().__init__(
            name="team_dispatch",
            description=(
                "Dispatch this round's tasks to the team members. Provide "
                "'assignments': one item per member task, each assigning a "
                "'member' (exactly a team member name) a 'task' (clear, "
                "self-contained instructions for that member). Optionally add "
                "'notes' to share context or coordination hints with all "
                "members. Call this once per round with the assignments for "
                "that round."
            ),
            parameters=_DISPATCH_PARAMETERS,
        )
        # Assign after super().__init__() (same pattern as HandoffTool) so these
        # instance attributes are not treated as dataclass fields.
        self.member_names = member_names
        self.on_dispatch = on_dispatch

    def _error(self, reason: str) -> str:
        """Build the self-correction error string shown to the coordinator LLM.

        Args:
            reason: A short description of what was wrong with the payload.

        Returns:
            An error string that also lists the valid member names so the LLM
            can fix and retry in the same turn.
        """
        return (
            f"Error: {reason} "
            f"Valid team members: {', '.join(self.member_names)}. "
            "Please fix the assignments and call team_dispatch again."
        )

    async def call(
        self,
        context: ContextWrapper,
        assignments: list[dict[str, str]] | None = None,
        notes: str | None = None,
    ) -> str:
        """Validate the payload and forward it to the team run.

        Args:
            context: The agent run context (unused).
            assignments: The member/task pairs to dispatch this round.
            notes: Optional shared context for all members.

        Returns:
            A confirmation string on success, or an error string (never an
            exception) listing the valid member names so the LLM can
            self-correct in the same turn.
        """
        if not isinstance(assignments, list) or not assignments:
            return self._error(
                "assignments must be a non-empty list of {member, task} objects."
            )
        for i, item in enumerate(assignments):
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("member"), str)
                or not isinstance(item.get("task"), str)
            ):
                return self._error(
                    f"assignments[{i}] must be an object with string fields "
                    "'member' and 'task'."
                )
        normalized_names = {name.strip().casefold() for name in self.member_names}
        for item in assignments:
            if item["member"].strip().casefold() not in normalized_names:
                return self._error(f'unknown team member "{item["member"]}".')

        if self.on_dispatch is not None:
            await self.on_dispatch(assignments, notes)
        return (
            f"Dispatched {len(assignments)} assignment(s). "
            "Once every member's result is in and the overall goal is achieved, "
            "call team_finish with a final summary to end the run."
        )


class TeamFinishTool(FunctionTool):
    """LLM tool that ends the team run with a final summary."""

    def __init__(self, on_finish: FinishCallback | None) -> None:
        """Create the finish tool for one team run.

        Args:
            on_finish: Callback invoked with the validated summary on success.
        """
        super().__init__(
            name="team_finish",
            description=(
                "Finish the team run. Call this when the overall goal is fully "
                "achieved and all member results have been collected. Provide a "
                "'summary' describing the work done and the outcome. This ends "
                "the run."
            ),
            parameters=_FINISH_PARAMETERS,
        )
        # Assign after super().__init__() (same pattern as HandoffTool).
        self.on_finish = on_finish

    async def call(self, context: ContextWrapper, summary: str | None = None) -> str:
        """Validate the summary and complete the team run.

        Args:
            context: The agent run context (unused).
            summary: The coordinator's final summary of the team's outcome.

        Returns:
            `"Team task finished."` on success, or an error string (never an
            exception) when the summary is missing or blank.
        """
        if not isinstance(summary, str) or not summary.strip():
            return (
                "Error: summary must be a non-empty string describing the "
                "team's outcome. Please call team_finish again with a "
                "meaningful summary."
            )
        if self.on_finish is not None:
            await self.on_finish(summary)
        return "Team task finished."


def build_team_tools(
    member_names: list[str],
    on_dispatch: DispatchCallback | None,
    on_finish: FinishCallback | None,
) -> list[FunctionTool]:
    """Build the coordinator tool pair for one team run.

    Args:
        member_names: Names of the team members the coordinator may dispatch to.
        on_dispatch: Callback invoked on a valid dispatch (assignments, notes).
        on_finish: Callback invoked on a valid finish with the final summary.

    Returns:
        A list containing a `TeamDispatchTool` and a `TeamFinishTool`.
    """
    return [
        TeamDispatchTool(member_names, on_dispatch),
        TeamFinishTool(on_finish),
    ]
