"""Private note tools resolve the umo and keep errors as text."""

import json
from types import SimpleNamespace

import pytest

from astrbot.core.agent import notes_store
from astrbot.core.tools.notes_tools import (
    NotesListTool,
    NotesReadTool,
    NotesSearchTool,
    NotesWriteTool,
)
from astrbot.core.tools.registry import get_builtin_tool_class


@pytest.fixture
def notes_root(tmp_path, monkeypatch):
    monkeypatch.setattr(
        notes_store, "get_astrbot_agent_notes_path", lambda: str(tmp_path)
    )
    return tmp_path


def _context(umo: str):
    return SimpleNamespace(
        context=SimpleNamespace(event=SimpleNamespace(unified_msg_origin=umo))
    )


def test_tools_are_registered_as_builtin_tools():
    assert get_builtin_tool_class("notes_list") is NotesListTool
    assert get_builtin_tool_class("notes_read") is NotesReadTool
    assert get_builtin_tool_class("notes_search") is NotesSearchTool
    assert get_builtin_tool_class("notes_write") is NotesWriteTool


@pytest.mark.asyncio
async def test_list_reports_an_empty_store(notes_root):
    result = await NotesListTool().call(_context("umo:a"))

    assert result == "No notes yet."


@pytest.mark.asyncio
async def test_write_then_list_returns_metadata(notes_root):
    confirmation = await NotesWriteTool().call(
        _context("umo:a"), path="plan", text="first\nsecond"
    )
    entries = json.loads(await NotesListTool().call(_context("umo:a")))

    assert confirmation.startswith("Wrote plan.md")
    assert entries[0]["path"] == "plan.md"
    assert entries[0]["lines"] == 2


@pytest.mark.asyncio
async def test_read_reports_unknown_notes_and_bad_paths(notes_root):
    tool = NotesReadTool()

    assert await tool.call(_context("umo:a"), path="missing.md") == (
        "Error: no such note."
    )
    assert (await tool.call(_context("umo:a"), path="../escape.md")).startswith(
        "Error:"
    )


@pytest.mark.asyncio
async def test_search_requires_a_query_and_returns_hits(notes_root):
    await NotesWriteTool().call(_context("umo:a"), path="a.md", text="alpha beta")

    empty = await NotesSearchTool().call(_context("umo:a"), query="  ")
    hits = json.loads(await NotesSearchTool().call(_context("umo:a"), query="beta"))

    assert empty.startswith("Error:")
    assert hits[0]["path"] == "a.md"
    assert hits[0]["line"] == 1


@pytest.mark.asyncio
async def test_notes_are_not_visible_across_umos(notes_root):
    await NotesWriteTool().call(_context("umo:a"), path="plan.md", text="secret")

    result = await NotesReadTool().call(_context("umo:b"), path="plan.md")

    assert result == "Error: no such note."
