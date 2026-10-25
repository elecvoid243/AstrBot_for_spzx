"""Builtin ask_user_choice star tests: registration gating + interceptor.

Migrated from the astrbot_plugin_ask_user main plugin tests.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from astrbot.builtin_stars.ask_user_choice.main import Main
from astrbot.core.tools.interactive_choice_registry import registry

WEBCHAT_UMO = "webchat:FriendMessage:webchat!alice!sess-123"


def _make_context(config: dict | None = None) -> MagicMock:
    context = MagicMock()
    context.get_config.return_value = config or {}
    context.add_llm_tools = MagicMock()
    return context


def _make_event(
    umo: str = WEBCHAT_UMO,
    message_str: str = "hello",
) -> MagicMock:
    event = MagicMock()
    event.unified_msg_origin = umo
    event.message_str = message_str
    event.should_call_llm = MagicMock()
    event.stop_event = MagicMock()
    return event


@pytest.fixture(autouse=True)
def _clean_registry():
    registry._pending.clear()
    registry._by_umo.clear()
    yield
    registry._pending.clear()
    registry._by_umo.clear()


def _add_pending() -> str:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    future = loop.create_future()
    registry.add(
        request_id="r1",
        umo=WEBCHAT_UMO,
        future=future,
        spec={},
        created_at=1.0,
        timeout_at=time.time() + 600,
        sse_message_id="sse-1",
    )
    return "r1"


def test_initialize_registers_tool_by_default() -> None:
    star = Main(context=_make_context())

    asyncio.run(star.initialize())

    star.context.add_llm_tools.assert_called_once()
    (tool,) = star.context.add_llm_tools.call_args[0]
    assert tool.name == "ask_user_choice"


def test_initialize_skips_tool_when_disabled() -> None:
    star = Main(
        context=_make_context(
            {
                "agent_runner": {
                    "config": {"ask_user_choice": {"enabled": False}},
                },
            },
        ),
    )

    asyncio.run(star.initialize())

    star.context.add_llm_tools.assert_not_called()


def test_interceptor_consumes_message_as_free_text() -> None:
    request_id = _add_pending()
    star = Main(context=_make_context())
    event = _make_event(message_str="我想选 B 但是改一下")

    asyncio.run(star.on_message(event))

    event.should_call_llm.assert_called_once_with(False)
    event.stop_event.assert_called_once()
    # resolve() only completes the future; the tool's finally-block remove()
    # drops the entry, so the pending entry may still be present here.
    assert registry._pending[request_id].future.done()


def test_interceptor_resolves_the_future() -> None:
    request_id = _add_pending()
    star = Main(context=_make_context())
    event = _make_event(message_str="custom answer")
    future = registry._pending[request_id].future

    asyncio.run(star.on_message(event))

    assert future.result() == {
        "choice_id": "__free_text__",
        "free_text": "custom answer",
    }


def test_interceptor_ignores_non_webchat() -> None:
    _add_pending()
    star = Main(context=_make_context())
    event = _make_event(umo="aiocqhttp:FriendMessage:x")

    asyncio.run(star.on_message(event))

    event.should_call_llm.assert_not_called()
    event.stop_event.assert_not_called()


def test_interceptor_ignores_when_no_pending() -> None:
    star = Main(context=_make_context())
    event = _make_event()

    asyncio.run(star.on_message(event))

    event.should_call_llm.assert_not_called()
    event.stop_event.assert_not_called()


def test_interceptor_ignores_empty_message() -> None:
    _add_pending()
    star = Main(context=_make_context())
    event = _make_event(message_str="   ")

    asyncio.run(star.on_message(event))

    event.should_call_llm.assert_not_called()


def test_terminate_shuts_down_registry() -> None:
    _add_pending()
    star = Main(context=_make_context())

    asyncio.run(star.terminate())

    assert registry._pending == {}
