"""Per-umo, per-window archive of history discarded by window resets."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from astrbot.core.agent import history_archive
from astrbot.core.agent.context.truncator import ContextTruncator
from astrbot.core.agent.message import Message, WindowState
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner


@pytest.fixture
def archive_root(tmp_path, monkeypatch):
    monkeypatch.setattr(
        history_archive, "get_astrbot_agent_history_path", lambda: str(tmp_path)
    )
    return tmp_path


def _window(window_id: str = "w1", seq: int = 0, prev_id: str | None = None):
    return WindowState(id=window_id, seq=seq, prev_id=prev_id)


def test_umo_key_is_portable_and_distinct():
    first = history_archive.umo_key("webchat:FriendMessage:webchat!astrbot!a")
    second = history_archive.umo_key("webchat:FriendMessage:webchat!astrbot!b")

    assert first != second
    assert first.startswith("webchat_")
    assert ":" not in first
    assert "!" not in first


def test_umo_key_is_stable_for_long_and_non_ascii_umos():
    umo = "telegram:FriendMessage:" + "会话" * 200

    key = history_archive.umo_key(umo)

    assert key == history_archive.umo_key(umo)
    assert len(key) < 64


@pytest.mark.asyncio
async def test_append_window_writes_items_and_index(archive_root):
    messages = [
        Message(role="system", content="system prompt"),
        Message(role="user", content="first question"),
        Message(role="assistant", content="answer"),
    ]

    await history_archive.append_window("umo:a", _window(), messages)

    windows = history_archive.list_windows("umo:a")
    assert [window["window_id"] for window in windows] == ["w1"]
    assert windows[0]["item_count"] == 2
    assert windows[0]["first_user_text"] == "first question"

    items = history_archive.read_window("umo:a", "w1")
    assert [item["role"] for item in items] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_append_window_is_idempotent(archive_root):
    messages = [Message(role="user", content="hello")]

    await history_archive.append_window("umo:a", _window(), messages)
    await history_archive.append_window("umo:a", _window(), messages)

    assert len(history_archive.list_windows("umo:a")) == 1
    assert len(history_archive.read_window("umo:a", "w1")) == 1


@pytest.mark.asyncio
async def test_internal_and_unsaved_messages_are_not_archived(archive_root):
    internal = Message(role="user", content="[context budget] wrap up")
    internal.internal = True
    unsaved = Message(role="user", content="hidden")
    unsaved._no_save = True

    await history_archive.append_window(
        "umo:a",
        _window(),
        [
            Message(role="system", content="system prompt"),
            internal,
            unsaved,
            Message(role="user", content="kept"),
        ],
    )

    items = history_archive.read_window("umo:a", "w1")
    assert [item["content"] for item in items] == ["kept"]


@pytest.mark.asyncio
async def test_long_content_is_truncated_before_storage(archive_root):
    long_text = "x" * (history_archive.MAX_ITEM_CHARS + 10)

    await history_archive.append_window(
        "umo:a", _window(), [Message(role="user", content=long_text)]
    )

    items_path = (
        archive_root
        / history_archive.umo_key("umo:a")
        / "w1"
        / history_archive._ITEMS_FILENAME
    )
    stored = json.loads(items_path.read_text(encoding="utf-8").splitlines()[0])
    assert len(stored["content"]) == history_archive.MAX_ITEM_CHARS
    assert stored["truncated"] is True
    assert stored["original_chars"] == len(long_text)

    returned = history_archive.read_window("umo:a", "w1")[0]
    assert len(returned["content"]) == history_archive.MAX_READ_ITEM_CHARS


@pytest.mark.asyncio
async def test_windows_are_isolated_per_umo(archive_root):
    await history_archive.append_window(
        "umo:a", _window(), [Message(role="user", content="alpha")]
    )

    assert history_archive.list_windows("umo:b") == []
    assert history_archive.read_window("umo:b", "w1") is None
    assert history_archive.search("umo:b", "alpha") == []


@pytest.mark.asyncio
async def test_search_finds_hits_and_rejects_blank_queries(archive_root):
    await history_archive.append_window(
        "umo:a",
        _window(),
        [Message(role="user", content="Please refactor the ARCHIVE layout")],
    )

    hits = history_archive.search("umo:a", "archive")
    assert len(hits) == 1
    assert hits[0]["window_id"] == "w1"
    assert "ARCHIVE" in hits[0]["snippet"]
    assert history_archive.search("umo:a", "   ") == []


def test_discarded_prefix_matches_the_reset_boundary():
    truncator = ContextTruncator()
    messages = [
        Message(role="system", content="system"),
        Message(role="user", content="old"),
        Message(role="assistant", content="old answer"),
        Message(role="user", content="new"),
        Message(role="assistant", content="new answer"),
    ]

    discarded = truncator.discarded_prefix(messages)
    kept = truncator.reset_to_latest_round(messages)

    assert [message.content for message in discarded] == ["old", "old answer"]
    assert [message.content for message in kept] == [
        "system",
        "new",
        "new answer",
    ]


def test_discarded_prefix_is_empty_for_a_single_round():
    truncator = ContextTruncator()
    messages = [
        Message(role="system", content="system"),
        Message(role="user", content="only"),
        Message(role="assistant", content="answer"),
    ]

    assert truncator.discarded_prefix(messages) == []


@pytest.mark.asyncio
async def test_reset_archives_the_discarded_prefix_and_links_windows(archive_root):
    runner = object.__new__(ToolLoopAgentRunner)
    runner.window_state = WindowState(id="w1", seq=0)
    runner._window_just_reset = False
    runner._archived_window_ids = set()
    runner._archive_tasks = set()
    runner.req = SimpleNamespace(session_id="umo:a")

    runner._on_context_reset([Message(role="user", content="old")])
    await asyncio.gather(*runner._archive_tasks)

    assert runner.window_state.prev_id == "w1"
    assert runner.window_state.seq == 1
    assert [
        window["window_id"] for window in history_archive.list_windows("umo:a")
    ] == ["w1"]


@pytest.mark.asyncio
async def test_reset_archives_each_window_once(archive_root):
    runner = object.__new__(ToolLoopAgentRunner)
    runner.window_state = WindowState(id="w1", seq=0)
    runner._window_just_reset = False
    runner._archived_window_ids = set()
    runner._archive_tasks = set()
    runner.req = SimpleNamespace(session_id="umo:a")
    discarded = [Message(role="user", content="old")]

    runner._on_context_reset(discarded)
    await asyncio.gather(*runner._archive_tasks)
    # A second preparation pass of the same model call must not append again.
    runner._schedule_archive(WindowState(id="w1", seq=0), discarded)
    await asyncio.gather(*runner._archive_tasks)

    assert len(history_archive.read_window("umo:a", "w1")) == 1
