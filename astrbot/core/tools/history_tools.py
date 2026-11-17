"""Context window tools: archive access and manual window resets.

The umo is resolved from the tool context, never from arguments: a model may
only look at windows of the conversation it is running in.
"""

import json
from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.history_archive import list_windows, read_window, search
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.tools.registry import builtin_tool


def _umo(context: ContextWrapper[AstrAgentContext]) -> str:
    """Return the session that owns this tool call.

    Args:
        context: Tool execution context.

    Returns:
        The unified message origin of the current event.
    """
    return str(context.context.event.unified_msg_origin)


@builtin_tool
@dataclass
class NewContextTool(FunctionTool[AstrAgentContext]):
    name: str = "new_context"
    description: str = (
        "Start a new context window. History before the current request is "
        "archived and dropped; the current request and its tool results are kept. "
        "Use it when the current task is finished and the next step does not need "
        "the accumulated history."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        """Ask the running agent to reset its window on the next model call.

        Args:
            context: Tool execution context.
            **kwargs: Unused; the tool takes no arguments.

        Returns:
            Confirmation that names what is kept, or an error when this run has
            no resettable main runner.
        """
        runner = (context.context.extra or {}).get("main_agent_runner")
        if runner is None or not hasattr(runner, "request_new_context_window"):
            return "Error: context windows are not available in this run."
        runner.request_new_context_window()
        return (
            "A new context window will start with the next model call. "
            "History before the current request is archived; the current request "
            "and its results are kept."
        )


@builtin_tool
@dataclass
class HistoryListWindowsTool(FunctionTool[AstrAgentContext]):
    name: str = "history_list_windows"
    description: str = (
        "List the archived context windows of this conversation. A window is a "
        "closed segment of history that started when the context was reset. "
        "Read one with history_read, or search all of them with history_search."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        """Return archived window metadata for this conversation.

        Args:
            context: Tool execution context.
            **kwargs: Unused; the tool takes no arguments.

        Returns:
            JSON list of window records, or a note when nothing is archived.
        """
        windows = list_windows(_umo(context))
        if not windows:
            return "No archived windows in this conversation."
        return json.dumps(windows, ensure_ascii=False)


@builtin_tool
@dataclass
class HistorySearchTool(FunctionTool[AstrAgentContext]):
    name: str = "history_search"
    description: str = (
        "Search archived context windows of this conversation with a "
        "case-insensitive substring. Returns the window, item index, role and a "
        "snippet. Use it to recall something discussed before the current "
        "window started."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to look for.",
                },
                "window_id": {
                    "type": "string",
                    "description": "Restrict the search to one archived window.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of hits (default 10).",
                },
            },
            "required": ["query"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        query: str = "",
        window_id: str | None = None,
        limit: int = 10,
        **kwargs: Any,
    ) -> ToolExecResult:
        """Search this conversation's archive.

        Args:
            context: Tool execution context.
            query: Non-empty search text.
            window_id: Optional window to restrict the search to.
            limit: Maximum number of hits.
            **kwargs: Unused.

        Returns:
            JSON hits, or a note when nothing matches.
        """
        if not query.strip():
            return "Error: query must not be empty."
        hits = search(_umo(context), query, window_id=window_id, limit=limit)
        if not hits:
            return "No archived message matches that query."
        return json.dumps(hits, ensure_ascii=False)


@builtin_tool
@dataclass
class HistoryReadTool(FunctionTool[AstrAgentContext]):
    name: str = "history_read"
    description: str = (
        "Read messages from one archived context window of this conversation. "
        "Use history_list_windows first to find a window id."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "window_id": {
                    "type": "string",
                    "description": "Window to read.",
                },
                "offset": {
                    "type": "integer",
                    "description": "First item index (default 0).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of items (default 50).",
                },
            },
            "required": ["window_id"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        window_id: str = "",
        offset: int = 0,
        limit: int = 50,
        **kwargs: Any,
    ) -> ToolExecResult:
        """Return a slice of one archived window.

        Args:
            context: Tool execution context.
            window_id: Window to read.
            offset: First item index.
            limit: Maximum number of items.
            **kwargs: Unused.

        Returns:
            JSON items, or a refusal that does not reveal whether the window
            exists under another conversation.
        """
        items = read_window(_umo(context), window_id, offset=offset, limit=limit)
        if items is None:
            return "No such window."
        if not items:
            return "That window has no messages in this range."
        return json.dumps(items, ensure_ascii=False)
