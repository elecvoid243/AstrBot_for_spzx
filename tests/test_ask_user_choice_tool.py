"""ask_user_choice tool tests.

External-behavior tests: parameter validation soft errors, the blocking
wait flow against a fake SSE back queue, and timeout fallback.

Migrated from the astrbot_plugin_ask_user tool tests.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from astrbot.builtin_stars.ask_user_choice.ask_user_choice_tool import (
    AskUserChoiceTool,
)
from astrbot.core.tools.interactive_choice_registry import registry


def _make_context(umo: str, message_id: str = "sse-1") -> MagicMock:
    event = MagicMock()
    event.unified_msg_origin = umo
    event.message_obj.message_id = message_id
    wrapper = MagicMock()
    wrapper.context.event = event
    wrapper.context.get_config.return_value = {}
    return wrapper


@pytest.fixture(autouse=True)
def _clean_registry():
    registry._pending.clear()
    registry._by_umo.clear()
    yield
    registry._pending.clear()
    registry._by_umo.clear()


@pytest.fixture
def fake_queue(monkeypatch) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    mgr = MagicMock()
    mgr.get_or_create_back_queue = MagicMock(return_value=queue)
    # The tool holds a module-level binding of webchat_queue_mgr (kept at
    # module top precisely so tests can patch it), while the registry's push
    # helper imports it lazily from the source module — patch both.
    monkeypatch.setattr(
        "astrbot.builtin_stars.ask_user_choice.ask_user_choice_tool.webchat_queue_mgr",
        mgr,
    )
    monkeypatch.setattr(
        "astrbot.core.platform.sources.webchat.webchat_queue_mgr.webchat_queue_mgr",
        mgr,
    )
    return queue


WEBCHAT_UMO = "webchat:FriendMessage:webchat!alice!sess-123"
VALID_KWARGS = {
    "prompt": "请选择",
    "options": [
        {"id": "a", "label": "A", "value": "1"},
        {"id": "b", "label": "B", "value": "2"},
    ],
}


def test_validate_rejects_empty_prompt() -> None:
    result = AskUserChoiceTool()._validate_and_build_spec({"prompt": "", "options": []})
    assert isinstance(result, str)
    assert "prompt" in result


def test_validate_rejects_wrong_option_count() -> None:
    tool = AskUserChoiceTool()
    result = tool._validate_and_build_spec(
        {"prompt": "p", "options": [{"id": "a", "label": "A"}]}
    )
    assert isinstance(result, str) and "2-10" in result
    result = tool._validate_and_build_spec(
        {
            "prompt": "p",
            "options": [{"id": str(i), "label": str(i)} for i in range(11)],
        },
    )
    assert isinstance(result, str) and "2-10" in result


def test_validate_rejects_duplicate_ids() -> None:
    result = AskUserChoiceTool()._validate_and_build_spec(
        {
            "prompt": "p",
            "options": [
                {"id": "a", "label": "A"},
                {"id": "a", "label": "B"},
            ],
        },
    )
    assert isinstance(result, str)
    assert "duplicate" in result


def test_validate_truncates_fields() -> None:
    result = AskUserChoiceTool()._validate_and_build_spec(
        {
            "prompt": "x" * 300,
            "title": "t" * 100,
            "input_placeholder": "i" * 100,
            "options": [
                {"id": "a", "label": "L" * 100, "description": "D" * 1200},
                {"id": "b", "label": "B"},
            ],
            "extra_content": "E" * 6000,
        },
    )
    assert isinstance(result, dict)
    assert len(result["prompt"]) == 200
    assert len(result["title"]) == 80
    assert len(result["input_placeholder"]) == 60
    assert len(result["options"][0]["label"]) == 80
    assert len(result["options"][0]["description"]) == 1000
    assert len(result["extra_content"]) == 5000


def test_validate_omits_empty_optional_fields() -> None:
    result = AskUserChoiceTool()._validate_and_build_spec(
        {
            "prompt": "p",
            "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        },
    )
    assert isinstance(result, dict)
    assert "title" not in result
    assert "input_placeholder" not in result
    assert "extra_content" not in result


def test_rejects_non_webchat_platform(fake_queue) -> None:
    result = asyncio.run(
        AskUserChoiceTool().call(
            _make_context("aiocqhttp:FriendMessage:x"), **VALID_KWARGS
        ),
    )
    assert result.startswith("Error:")
    assert "webchat" in result


def test_rejects_webchat_event_without_message_id(fake_queue) -> None:
    result = asyncio.run(
        AskUserChoiceTool().call(
            _make_context(WEBCHAT_UMO, message_id=""), **VALID_KWARGS
        ),
    )
    assert result.startswith("Error:")
    assert "message_id" in result


def test_success_path_returns_user_choice(fake_queue) -> None:
    tool = AskUserChoiceTool()

    async def _scenario():
        call_task = asyncio.create_task(
            tool.call(_make_context(WEBCHAT_UMO), **VALID_KWARGS)
        )
        await asyncio.sleep(0.01)
        pending = registry.find_pending_by_umo(WEBCHAT_UMO)
        assert pending is not None
        registry.resolve(pending.request_id, {"choice_id": "b", "free_text": ""})
        return await call_task

    result = asyncio.run(_scenario())

    assert result == "User selected: B (id=b)"
    # The finally-block cleanup removes the entry once the call finishes.
    assert registry.find_pending_by_umo(WEBCHAT_UMO) is None


def test_free_text_is_attached_as_additional_note(fake_queue) -> None:
    tool = AskUserChoiceTool()

    async def _scenario():
        call_task = asyncio.create_task(
            tool.call(_make_context(WEBCHAT_UMO), **VALID_KWARGS)
        )
        await asyncio.sleep(0.01)
        pending = registry.find_pending_by_umo(WEBCHAT_UMO)
        assert pending is not None
        registry.resolve(pending.request_id, {"choice_id": "b", "free_text": "第二个"})
        return await call_task

    result = asyncio.run(_scenario())

    assert result == "User selected: B (id=b)\nAdditional note: 第二个"


def test_timeout_returns_fallback_and_broadcasts_cancelled(
    fake_queue, monkeypatch
) -> None:
    monkeypatch.setattr(
        "astrbot.builtin_stars.ask_user_choice.ask_user_choice_tool.AskUserChoiceTool._load_tool_config",
        lambda self, context: {
            "timeout_seconds": 1,
            "timeout_fallback_message": "no answer in {timeout}s",
        },
    )
    tool = AskUserChoiceTool()

    result = asyncio.run(tool.call(_make_context(WEBCHAT_UMO), **VALID_KWARGS))

    assert result == "no answer in 1s"
    events = []
    while not fake_queue.empty():
        events.append(fake_queue.get_nowait())
    # [0] is the interactive_choice spec push, [1] the timeout broadcast.
    assert events[0]["chain_type"] == "interactive_choice"
    assert events[1]["type"] == "interactive_choice_resolved"
    assert events[1]["data"]["reason"] == "cancelled"


def test_interactive_choice_event_uses_chain_type_envelope(fake_queue) -> None:
    tool = AskUserChoiceTool()

    asyncio.run(
        tool._push_to_webchat_back_queue(
            request_id="req-1",
            umo=WEBCHAT_UMO,
            spec={"type": "interactive_choice", "prompt": "请选择", "options": []},
            expires_at=12345.0,
            sse_message_id="sse-1",
        ),
    )

    event = fake_queue.get_nowait()
    assert event["type"] == "plain"
    assert event["chain_type"] == "interactive_choice"
    assert event["message_id"] == "sse-1"
    import json

    payload = json.loads(event["data"])
    assert payload["spec"]["type"] == "interactive_choice"
    assert payload["spec"]["prompt"] == "请选择"
    assert payload["request_id"] == "req-1"
    assert payload["umo"] == WEBCHAT_UMO
    assert payload["expires_at"] == 12345.0


def test_config_loaded_from_agent_runner_section(monkeypatch) -> None:
    context = _make_context(WEBCHAT_UMO)
    context.context.get_config.return_value = {
        "agent_runner": {
            "config": {
                "ask_user_choice": {"timeout_seconds": 60},
            },
        },
    }
    tool = AskUserChoiceTool()
    config = tool._load_tool_config(context)

    assert config["timeout_seconds"] == 60

    broken = MagicMock()
    broken.context.get_config.side_effect = RuntimeError("boom")
    assert tool._load_tool_config(broken) == {}
