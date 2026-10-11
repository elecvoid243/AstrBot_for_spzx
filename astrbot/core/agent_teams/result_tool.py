"""Explicit default text submission; natural-language output cannot finish tasks."""

from collections.abc import Awaitable, Callable

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent_teams.assets import RoleAssetError


class SubmitTextResultTool(FunctionTool):
    """Accept a durable submission intent for the currently bound attempt."""

    def __init__(self, accept: Callable[[dict], Awaitable[None]]):
        """Bind this tool to one authorized task attempt.

        Args:
            accept: Public service operation validating and persisting the intent.
        """
        super().__init__(
            name="submit_task_result",
            description="Submit the final task result as non-empty text. Call once after completing the work.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string", "minLength": 1}},
                "required": ["text"],
                "additionalProperties": False,
            },
        )
        self.accept = accept

    async def call(
        self, context: ContextWrapper, text: str | None = None, **kwargs
    ) -> str:
        """Validate and persist; the runtime completes only after all tools finish.

        Args:
            context: Existing Agent context.
            text: Complete default text result.
            **kwargs: Unknown result fields, rejected rather than ignored.

        Returns:
            Acceptance or field errors for correction within the current budget.
        """
        try:
            await self.accept({"text": text, **kwargs})
        except RoleAssetError as exc:
            return f"Error: {exc}; fields={exc.fields}"
        return "Result accepted."
