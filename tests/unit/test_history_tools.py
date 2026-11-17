"""Archived-window tools resolve the umo from the tool context."""

import json
from types import SimpleNamespace

import pytest

from astrbot.core.agent import history_archive
from astrbot.core.agent.message import Message, WindowState
from astrbot.core.tools.history_tools import (
    HistoryListWindowsTool,
    HistoryReadTool,
    HistorySearchTool,
    NewContextTool,
)
from astrbot.core.tools.registry import get_builtin_tool_class


@pytest.fixture
def archive_root(tmp_path, monkeypatch):
    monkeypatch.setattr(
        history_archive, "get_astrbot_agent_history_path", lambda: str(tmp_path)
    )
    return tmp_path


def _context(umo: str):
    return SimpleNamespace(
        context=SimpleNamespace(event=SimpleNamespace(unified_msg_origin=umo))
    )


def test_tools_are_registered_as_builtin_tools():
    assert get_builtin_tool_class("new_context") is NewContextTool
    assert get_builtin_tool_class("history_list_windows") is HistoryListWindowsTool
    assert get_builtin_tool_class("history_search") is HistorySearchTool
    assert get_builtin_tool_class("history_read") is HistoryReadTool


@pytest.mark.asyncio
async def test_new_context_requests_a_reset_on_the_main_runner():
    requested = []
    runner = SimpleNamespace(request_new_context_window=lambda: requested.append(True))
    context = SimpleNamespace(
        context=SimpleNamespace(extra={"main_agent_runner": runner})
    )

    result = await NewContextTool().call(context)

    assert requested == [True]
    assert "next model call" in result


@pytest.mark.asyncio
async def test_new_context_without_a_runner_reports_unavailable():
    context = SimpleNamespace(context=SimpleNamespace(extra={}))

    result = await NewContextTool().call(context)

    assert result.startswith("Error:")


@pytest.mark.asyncio
async def test_list_reports_an_empty_archive(archive_root):
    result = await HistoryListWindowsTool().call(_context("umo:a"))

    assert result == "No archived windows in this conversation."


@pytest.mark.asyncio
async def test_list_returns_window_metadata(archive_root):
    await history_archive.append_window(
        "umo:a", WindowState(id="w1"), [Message(role="user", content="hello")]
    )

    records = json.loads(await HistoryListWindowsTool().call(_context("umo:a")))

    assert records[0]["window_id"] == "w1"
    assert records[0]["item_count"] == 1


@pytest.mark.asyncio
async def test_search_requires_a_query(archive_root):
    result = await HistorySearchTool().call(_context("umo:a"), query="   ")

    assert result.startswith("Error:")


@pytest.mark.asyncio
async def test_search_returns_hits(archive_root):
    await history_archive.append_window(
        "umo:a", WindowState(id="w1"), [Message(role="user", content="alpha beta")]
    )

    hits = json.loads(await HistorySearchTool().call(_context("umo:a"), query="beta"))

    assert hits[0]["window_id"] == "w1"
    assert hits[0]["role"] == "user"


@pytest.mark.asyncio
async def test_read_refuses_unknown_and_cross_umo_windows(archive_root):
    await history_archive.append_window(
        "umo:a", WindowState(id="w1"), [Message(role="user", content="alpha")]
    )
    tool = HistoryReadTool()

    assert await tool.call(_context("umo:a"), window_id="nope") == "No such window."
    assert await tool.call(_context("umo:b"), window_id="w1") == "No such window."


@pytest.mark.asyncio
async def test_read_returns_items(archive_root):
    await history_archive.append_window(
        "umo:a", WindowState(id="w1"), [Message(role="user", content="alpha")]
    )

    items = json.loads(await HistoryReadTool().call(_context("umo:a"), window_id="w1"))

    assert items[0]["content"] == "alpha"
