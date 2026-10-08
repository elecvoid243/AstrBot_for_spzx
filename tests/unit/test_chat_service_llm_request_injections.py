"""Unit tests for injections on the ChatUI history record (spec §4.4)."""

from __future__ import annotations

import json

from astrbot.dashboard.services.chat_service import (
    BotMessageAccumulator,
    build_bot_history_content,
)


def test_injections_payload_never_falls_through_as_text():
    acc = BotMessageAccumulator()
    acc.add_plain(
        json.dumps({"items": [{"plugin": "tc_memory"}]}),
        chain_type="llm_request_injections",
        streaming=False,
    )
    # Must not fall through to a literal JSON text part.
    assert acc.build_message_parts(include_pending_tool_calls=True) == []


def test_history_content_carries_injections():
    content = build_bot_history_content(
        [{"type": "plain", "text": "hi"}],
        llm_request_injections={"items": [{"plugin": "tc_memory"}]},
    )
    assert content["llm_request_injections"]["items"][0]["plugin"] == "tc_memory"


def test_history_content_omits_empty_injections():
    content = build_bot_history_content([{"type": "plain", "text": "hi"}])
    assert "llm_request_injections" not in content
