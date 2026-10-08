"""Live-only context compression notice.

The agent runner emits a ``type:"plain" + chain_type:"context_compression"``
payload when it compresses the request context. The dashboard turns it into a
transient toast on the frontend, so the accumulator must DROP it: letting the
JSON blob fall through as plain text would persist wire data into the chat
history (and into the run snapshot the page re-attaches to).

Author: elecvoid243
Date: 2026-10-08
"""

from __future__ import annotations

import json

from astrbot.dashboard.services.chat_service import BotMessageAccumulator


def _report() -> str:
    return json.dumps(
        {"strategy": "llm_compress", "tokens_before": 900, "tokens_after": 348}
    )


def test_context_compression_never_becomes_a_part():
    """A notice adds no part, no text, and no savable content."""
    acc = BotMessageAccumulator()

    acc.add_plain(_report(), chain_type="context_compression", streaming=False)

    assert acc.parts == []
    assert acc.pending_text == ""
    # `has_content()` gates the history write: a notice must not trigger one.
    assert acc.has_content() is False


def test_context_compression_keeps_streamed_text_intact():
    """A notice between two deltas must not split or swallow the text."""
    acc = BotMessageAccumulator()

    acc.add_plain("answer so far ", chain_type=None, streaming=True)
    acc.add_plain(_report(), chain_type="context_compression", streaming=False)
    acc.add_plain("answer continues", chain_type=None, streaming=True)

    parts = acc.build_message_parts()

    assert [p.get("text") for p in parts if p.get("type") == "plain"] == [
        "answer so far answer continues"
    ]
