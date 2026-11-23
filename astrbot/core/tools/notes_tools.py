"""Private notes tools: model-only memory shared across context windows.

Notes are not deliverables. The non-disclosure rule is stated once in
``CONTEXT_TOOLS_SYSTEM_PROMPT`` instead of in every description: repeating it
four times binds "use notes" to "hide something from the user", which suppresses
the calls this mechanism depends on, and it costs the space each description
needs for when to call it.
"""

import asyncio
import json
from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.notes_store import (
    list_notes,
    read_note,
    search_notes,
    write_note,
)
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
class NotesListTool(FunctionTool[AstrAgentContext]):
    name: str = "notes_list"
    description: str = (
        "List private model-only notes that survive context-window resets in "
        "this conversation. Paths are virtual, not filesystem paths. Call it "
        "when a window opens, or when you need to know what earlier work "
        "already recorded."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "prefix": {
                    "type": "string",
                    "description": "Virtual path prefix to list.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum files to return (default 50).",
                },
            },
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        prefix: str = "",
        max_results: int = 50,
        **kwargs: Any,
    ) -> ToolExecResult:
        """Return stored note metadata for this conversation.

        Args:
            context: Tool execution context.
            prefix: Virtual path prefix to filter by.
            max_results: Maximum number of files.
            **kwargs: Unused.

        Returns:
            JSON list of note entries, or a note when nothing is stored.
        """
        entries = await asyncio.to_thread(
            list_notes, _umo(context), prefix, max_results
        )
        if not entries:
            return "No notes yet."
        return json.dumps(entries, ensure_ascii=False)


@builtin_tool
@dataclass
class NotesReadTool(FunctionTool[AstrAgentContext]):
    name: str = "notes_read"
    description: str = (
        "Read a line range from a private model-only note. Use the note path "
        "from the window notice, notes_list, or notes_search. Lines are 1-based. "
        "Negative values count backward from the end."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Virtual note path to read.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "First line to return (default 1).",
                },
                "stop_line": {
                    "type": "integer",
                    "description": "Last line to return, inclusive.",
                },
            },
            "required": ["path"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        path: str = "",
        start_line: int = 1,
        stop_line: int | None = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        """Return a bounded slice of one note.

        Args:
            context: Tool execution context.
            path: Virtual note path.
            start_line: First line, 1-based.
            stop_line: Last line, 1-based.
            **kwargs: Unused.

        Returns:
            Header with the line range, then the content; or an error text.
        """
        try:
            note = await asyncio.to_thread(
                read_note, _umo(context), path, start_line, stop_line
            )
        except ValueError as exc:
            return f"Error: {exc}"
        if note is None:
            return "Error: no such note."
        header = (
            f"{note['path']} (lines {note['start']}-{note['end']} "
            f"of {note['total_lines']})"
        )
        return f"{header}\n---\n{note['content']}"


@builtin_tool
@dataclass
class NotesSearchTool(FunctionTool[AstrAgentContext]):
    name: str = "notes_search"
    description: str = (
        "Search private model-only note lines with a case-insensitive substring. "
        "Returns path, line number and a snippet. Use it before re-deriving a "
        "fact an earlier window may have recorded."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to look for.",
                },
                "path_prefix": {
                    "type": "string",
                    "description": "Virtual path prefix to search.",
                },
                "max_files": {
                    "type": "integer",
                    "description": "Maximum matching files (default 10).",
                },
                "max_matches_per_file": {
                    "type": "integer",
                    "description": "Maximum hits per file (default 3).",
                },
            },
            "required": ["query"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        query: str = "",
        path_prefix: str = "",
        max_files: int = 10,
        max_matches_per_file: int = 3,
        **kwargs: Any,
    ) -> ToolExecResult:
        """Search this conversation's notes.

        Args:
            context: Tool execution context.
            query: Non-empty search text.
            path_prefix: Virtual path prefix to search.
            max_files: Maximum matching files.
            max_matches_per_file: Maximum hits per file.
            **kwargs: Unused.

        Returns:
            JSON hits, or a note when nothing matches.
        """
        if not query.strip():
            return "Error: query must not be empty."
        hits = await asyncio.to_thread(
            search_notes,
            _umo(context),
            query,
            path_prefix,
            max_files,
            max_matches_per_file,
        )
        if not hits:
            return "No matches."
        return json.dumps(hits, ensure_ascii=False)


@builtin_tool
@dataclass
class NotesWriteTool(FunctionTool[AstrAgentContext]):
    name: str = "notes_write"
    description: str = (
        "Create, replace, or append to a private model-only note. Notes are "
        "shared by every context window of this conversation. `overwrite` "
        "replaces the whole file; `append` adds to its end. Write durable state "
        "before the budget fills."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Virtual note path; `.md` is added when missing.",
                },
                "text": {
                    "type": "string",
                    "description": "Content to write.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["overwrite", "append"],
                    "description": "Replace the file or append to it.",
                },
            },
            "required": ["path", "text"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        path: str = "",
        text: str = "",
        mode: str = "overwrite",
        **kwargs: Any,
    ) -> ToolExecResult:
        """Write one note under the conversation's size limits.

        Args:
            context: Tool execution context.
            path: Virtual note path.
            text: Content to write or append.
            mode: ``overwrite`` or ``append``.
            **kwargs: Unused.

        Returns:
            Confirmation with the resulting size and line count, or an error.
        """
        try:
            result = await asyncio.to_thread(
                write_note, _umo(context), path, text, mode
            )
        except ValueError as exc:
            return f"Error: {exc}"
        verb = "Appended to" if mode == "append" else "Wrote"
        return (
            f"{verb} {result['path']} ({result['bytes']} bytes, "
            f"{result['lines']} lines)."
        )
