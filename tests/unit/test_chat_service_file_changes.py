"""Unit tests for ``BotMessageAccumulator`` file_changes handling.

The end-of-turn file change summary arrives as a
``type:"plain" + chain_type:"file_changes"`` payload. The accumulator must
parse it into ``pending_file_changes`` instead of letting the JSON blob fall
through as a literal plain-text part.

Plan: docs/superpowers/plans/2026-09-13-chatui-file-change-summary.md
"""

from __future__ import annotations

import json

from astrbot.dashboard.services.chat_service import BotMessageAccumulator


def test_file_changes_captured_not_text():
    acc = BotMessageAccumulator()
    acc.add_plain(
        json.dumps({"files": [{"path": "a.txt", "kind": "edit"}]}),
        chain_type="file_changes",
        streaming=False,
    )
    assert acc.pending_file_changes["files"][0]["path"] == "a.txt"
    # Must NOT fall through to a literal JSON text part.
    assert acc.parts == []
    assert acc.has_content() is True


def test_file_changes_malformed_dropped():
    acc = BotMessageAccumulator()
    acc.add_plain("not-json", chain_type="file_changes", streaming=False)
    assert acc.pending_file_changes == {}
    assert acc.parts == []


def test_file_changes_flushed_via_build_parts():
    acc = BotMessageAccumulator()
    acc.add_plain("final answer", chain_type=None, streaming=False)
    acc.add_plain(
        json.dumps({"files": [{"path": "a.txt", "kind": "edit"}]}),
        chain_type="file_changes",
        streaming=False,
    )
    parts = acc.build_message_parts(include_pending_tool_calls=True)
    # The persisted parts must contain the plain text but no JSON blob part.
    plain = [p for p in parts if p.get("type") == "plain"]
    assert [p.get("text") for p in plain] == ["final answer"]
