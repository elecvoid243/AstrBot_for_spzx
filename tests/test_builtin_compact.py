"""Builtin /compact command handler tests.

External-behavior tests at the command-handler seam: a fake Context and a
canned provider drive the real compression path (``LLMSummaryCompressor``),
asserting on the reply and the conversation write-back.

Migrated from the astrbot_plugin_compact handler tests (single-command
form; the subcommand and override tests died with those features).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.api.all import MessageEventResult
from astrbot.builtin_stars.compact.main import Main
from astrbot.core.agent.message import (
    AssistantMessageSegment,
    UserMessageSegment,
    dump_messages_with_checkpoints,
)


def _make_provider(text: str = "Mocked summary.") -> MagicMock:
    """A mock provider that returns a fixed summary on text_chat."""
    provider = MagicMock()
    provider.provider_config = {"modalities": ["text"]}
    response = MagicMock()
    response.completion_text = text
    provider.text_chat = AsyncMock(return_value=response)
    return provider


def _make_event(umo: str = "umo:test") -> MagicMock:
    """Mock AstrMessageEvent; set_result captures the MessageEventResult."""
    event = MagicMock()
    event.unified_msg_origin = umo
    event.send = AsyncMock()
    result_holder: dict = {}

    def _capture_result(result):
        result_holder["result"] = result

    event.set_result.side_effect = _capture_result
    event._result_holder = result_holder
    return event


def _result_texts(event) -> list[str]:
    result = event._result_holder["result"]
    assert isinstance(result, MessageEventResult)
    return [comp.text for comp in result.chain if hasattr(comp, "text")]


def _make_conv_manager(
    *,
    cid: str | None = "conv-1",
    history: list[dict] | None = None,
) -> MagicMock:
    mgr = MagicMock()
    mgr.get_curr_conversation_id = AsyncMock(return_value=cid)
    conv = MagicMock()
    conv.history = history if history is not None else []
    mgr.get_conversation = AsyncMock(return_value=conv)

    async def _update_conversation(umo, cid, history=None, **_kw):
        mgr.last_updated_history = history or []

    mgr.update_conversation = AsyncMock(side_effect=_update_conversation)
    return mgr


def _make_context(
    *,
    provider: MagicMock | None = None,
    conv_manager: MagicMock | None = None,
    providers_by_id: dict[str, MagicMock] | None = None,
    config: dict | None = None,
) -> MagicMock:
    ctx = MagicMock()
    ctx.get_using_provider.return_value = provider
    ctx.get_provider_by_id.side_effect = lambda pid: (providers_by_id or {}).get(pid)
    ctx.get_config.return_value = config if config is not None else {}
    ctx.conversation_manager = conv_manager or _make_conv_manager()
    return ctx


def _history(*contents: str) -> list[dict]:
    """Alternate user/assistant contents into a serialized history list."""
    segments = []
    for i, content in enumerate(contents):
        seg = UserMessageSegment if i % 2 == 0 else AssistantMessageSegment
        segments.append(seg(content=content))
    return dump_messages_with_checkpoints(segments)


def _compression_config(provider_id: str = "") -> dict:
    return {
        "agent_runner": {
            "config": {
                "compression": {
                    "provider_id": provider_id,
                    "keep_recent_ratio": 0.1,
                    "instruction": "",
                },
            },
        },
    }


def test_refuses_when_no_provider() -> None:
    plugin = Main(context=_make_context(provider=None))
    event = _make_event()

    asyncio.run(plugin.compact(event))

    event.set_result.assert_called_once()
    texts = _result_texts(event)
    assert any("provider" in t.lower() or "LLM" in t for t in texts)


def test_refuses_when_no_conversation() -> None:
    conv_manager = _make_conv_manager(cid=None)
    plugin = Main(
        context=_make_context(provider=_make_provider(), conv_manager=conv_manager)
    )
    event = _make_event()

    asyncio.run(plugin.compact(event))

    event.set_result.assert_called_once()
    conv_manager.update_conversation.assert_not_called()


def test_compresses_and_saves() -> None:
    history = _history("u1", "a1", "u2", "a2", "u3", "a3")
    conv_manager = _make_conv_manager(history=history)
    plugin = Main(
        context=_make_context(
            provider=_make_provider("Mocked summary."),
            conv_manager=conv_manager,
        ),
    )
    event = _make_event()

    asyncio.run(plugin.compact(event))

    conv_manager.update_conversation.assert_called_once()
    new_history = conv_manager.last_updated_history
    assert isinstance(new_history, list)
    assert 0 < len(new_history) < len(history)
    texts = _result_texts(event)
    assert any("压缩完成" in t for t in texts)


def test_no_op_when_llm_fails_keeps_history() -> None:
    history = _history("u1", "a1", "u2", "a2", "u3", "a3")
    conv_manager = _make_conv_manager(history=history)
    provider = _make_provider()
    provider.text_chat = AsyncMock(side_effect=RuntimeError("boom"))
    plugin = Main(
        context=_make_context(provider=provider, conv_manager=conv_manager),
    )
    event = _make_event()

    asyncio.run(plugin.compact(event))

    conv_manager.update_conversation.assert_called_once()
    assert conv_manager.last_updated_history == history
    texts = _result_texts(event)
    assert any("未生效" in t for t in texts)


def test_core_config_provider_wins_over_session_provider() -> None:
    conv_manager = _make_conv_manager(history=_history("u1", "a1", "u2", "a2"))
    configured = _make_provider()
    session = _make_provider()
    plugin = Main(
        context=_make_context(
            provider=session,
            conv_manager=conv_manager,
            providers_by_id={"cfg-provider": configured},
            config=_compression_config(provider_id="cfg-provider"),
        ),
    )
    event = _make_event()

    asyncio.run(plugin.compact(event))

    configured.text_chat.assert_called_once()
    session.text_chat.assert_not_called()


def test_falls_back_to_session_provider_without_core_provider() -> None:
    conv_manager = _make_conv_manager(history=_history("u1", "a1", "u2", "a2"))
    session = _make_provider()
    plugin = Main(
        context=_make_context(
            provider=session,
            conv_manager=conv_manager,
            config=_compression_config(provider_id=""),
        ),
    )
    event = _make_event()

    asyncio.run(plugin.compact(event))

    session.text_chat.assert_called_once()


def test_trailing_text_is_ignored() -> None:
    """Legacy `/compact run keep 0.2 ...` syntax must still compress."""
    history = _history("u1", "a1", "u2", "a2", "u3", "a3")
    conv_manager = _make_conv_manager(history=history)
    plugin = Main(
        context=_make_context(provider=_make_provider(), conv_manager=conv_manager),
    )
    event = _make_event()

    asyncio.run(plugin.compact(event))

    conv_manager.update_conversation.assert_called_once()
    assert any("压缩完成" in t for t in _result_texts(event))


def test_load_history_dicts_variants() -> None:
    assert Main._load_history_dicts(None) == []
    assert Main._load_history_dicts(MagicMock(history=[{"role": "user"}])) == [
        {"role": "user"},
    ]
    assert Main._load_history_dicts(
        MagicMock(history=json.dumps([{"role": "user"}])),
    ) == [{"role": "user"}]
    assert Main._load_history_dicts(MagicMock(history="not json")) == []
    assert Main._load_history_dicts(MagicMock(history=42)) == []


def test_core_compression_config_variants() -> None:
    plugin = Main(context=_make_context(config=_compression_config("p1")))
    assert plugin._core_compression_config("umo")["provider_id"] == "p1"

    broken = MagicMock()
    broken.get_config.return_value = broken
    broken.get.side_effect = RuntimeError("boom")
    plugin_broken = Main(context=broken)
    assert plugin_broken._core_compression_config("umo") == {}


@pytest.mark.parametrize("bad_config", [None, {}, {"agent_runner": None}])
def test_core_compression_config_missing_sections(bad_config) -> None:
    ctx = MagicMock()
    ctx.get_config.return_value = bad_config
    plugin = Main(context=ctx)
    assert plugin._core_compression_config("umo") == {}
