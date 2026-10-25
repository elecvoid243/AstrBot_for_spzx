"""InteractiveChoiceRegistry and SSE push helper tests.

Migrated from the astrbot_plugin_ask_user registry tests.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from astrbot.core.tools.interactive_choice_registry import (
    extract_conversation_id,
    push_resolved_event_to_back_queue,
    registry,
)


def _make_future() -> asyncio.Future:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.create_future()


@pytest.fixture(autouse=True)
def _clean_registry():
    registry._pending.clear()
    registry._by_umo.clear()
    yield
    registry._pending.clear()
    registry._by_umo.clear()


def _add(
    rid: str = "r1", umo: str = "webchat:FriendMessage:webchat!alice!sess"
) -> asyncio.Future:
    future = _make_future()
    registry.add(
        request_id=rid,
        umo=umo,
        future=future,
        spec={"type": "interactive_choice", "prompt": "p"},
        created_at=1.0,
        timeout_at=time.time() + 600,
        sse_message_id="sse-1",
    )
    return future


def test_add_and_remove_roundtrip() -> None:
    future = _add()

    assert "r1" in registry._pending
    registry.remove("r1")
    assert "r1" not in registry._pending
    assert not future.done() or future.cancelled()


def test_remove_cancels_unfinished_future() -> None:
    future = _add()

    registry.remove("r1")

    assert future.cancelled()


def test_remove_unknown_is_noop() -> None:
    registry.remove("missing")


def test_resolve_sets_future_result() -> None:
    future = _add()

    assert registry.resolve("r1", {"choice_id": "a", "free_text": ""}) is True
    assert future.result() == {"choice_id": "a", "free_text": ""}


def test_resolve_unknown_or_done_returns_false() -> None:
    _add()
    registry.resolve("r1", {"choice_id": "a", "free_text": ""})
    assert registry.resolve("r1", {"choice_id": "b", "free_text": ""}) is False
    assert registry.resolve("missing", {"choice_id": "a", "free_text": ""}) is False


def test_find_pending_by_umo_returns_newest() -> None:
    _add("r1")
    registry.add(
        request_id="r2",
        umo="webchat:FriendMessage:webchat!alice!sess",
        future=_make_future(),
        spec={},
        created_at=2.0,
        timeout_at=time.time() + 600,
    )

    pending = registry.find_pending_by_umo("webchat:FriendMessage:webchat!alice!sess")

    assert pending is not None
    assert pending.request_id == "r2"


def test_find_pending_by_umo_excludes_expired() -> None:
    registry.add(
        request_id="r1",
        umo="webchat:FriendMessage:webchat!alice!sess",
        future=_make_future(),
        spec={},
        created_at=1.0,
        timeout_at=0.0,
    )

    assert (
        registry.find_pending_by_umo("webchat:FriendMessage:webchat!alice!sess") is None
    )


def test_list_pending_for_umo_shape() -> None:
    _add("r1")

    items = registry.list_pending_for_umo("webchat:FriendMessage:webchat!alice!sess")

    assert len(items) == 1
    assert items[0]["request_id"] == "r1"
    assert items[0]["timeout_at"] > time.time()


def test_shutdown_cancels_everything() -> None:
    futures = [_add("r1"), _add("r2")]

    asyncio.run(registry.shutdown())

    assert registry._pending == {}
    assert all(f.cancelled() for f in futures)


def test_extract_conversation_id() -> None:
    assert (
        extract_conversation_id("webchat:FriendMessage:webchat!alice!sess-abc")
        == "sess-abc"
    )
    assert extract_conversation_id("webchat:FriendMessage") is None
    assert extract_conversation_id("invalid") is None


def test_push_resolved_event_uses_back_queue(monkeypatch) -> None:
    queue: asyncio.Queue = asyncio.Queue()
    mgr = MagicMock()
    mgr.get_or_create_back_queue = MagicMock(return_value=queue)
    monkeypatch.setattr(
        "astrbot.core.platform.sources.webchat.webchat_queue_mgr.webchat_queue_mgr",
        mgr,
    )

    asyncio.run(
        push_resolved_event_to_back_queue(
            request_id="r1",
            umo="webchat:FriendMessage:webchat!alice!sess",
            reason="submitted",
            sse_message_id="sse-1",
        ),
    )

    event = queue.get_nowait()
    assert event["type"] == "interactive_choice_resolved"
    assert event["data"] == {
        "request_id": "r1",
        "reason": "submitted",
        "umo": ("webchat:FriendMessage:webchat!alice!sess"),
    }
    assert event["message_id"] == "sse-1"


def test_push_resolved_event_ignores_non_webchat_umo(monkeypatch) -> None:
    mgr = MagicMock()
    mgr.get_or_create_back_queue = MagicMock()
    monkeypatch.setattr(
        "astrbot.core.platform.sources.webchat.webchat_queue_mgr.webchat_queue_mgr",
        mgr,
    )

    asyncio.run(
        push_resolved_event_to_back_queue(
            request_id="r1",
            umo="lark:x:y",
            reason="submitted",
            sse_message_id="sse-1",
        ),
    )

    mgr.get_or_create_back_queue.assert_not_called()
