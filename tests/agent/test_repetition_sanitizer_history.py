import logging
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.agent.context.repetition import (
    RepetitionSanitizeConfig,
    sanitize_message_think_parts,
)
from astrbot.core.agent.message import Message, TextPart, ThinkPart
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (
    InternalAgentSubStage,
)
from astrbot.core.provider.entities import LLMResponse

REPEATED = ("OK.\nLet me write.\nLet me go.\nLet me go.\n") * 20


def _make_stage():
    stage = object.__new__(InternalAgentSubStage)
    stage.conv_manager = SimpleNamespace(update_conversation=AsyncMock())
    event = SimpleNamespace(
        unified_msg_origin="webchat:FriendMessage:test",
        get_extra=lambda key: None,
    )
    req = SimpleNamespace(
        conversation=SimpleNamespace(cid="cid", token_usage=0),
        tool_calls_result=None,
    )
    return stage, event, req


@contextmanager
def _capture_astrbot_logs(caplog):
    logger = logging.getLogger("astrbot")
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="astrbot"):
            yield
    finally:
        logger.removeHandler(caplog.handler)


def test_sanitizes_assistant_think_parts_only():
    messages = [
        Message(
            role="assistant",
            content=[ThinkPart(think=REPEATED), TextPart(text="answer")],
        ),
        Message(role="user", content=[TextPart(text=REPEATED)]),
    ]
    messages[0]._no_save = True

    result = sanitize_message_think_parts(messages, RepetitionSanitizeConfig())

    assert result[0] is not messages[0]
    assert result[0]._no_save is True
    assert result[0].content[0].think.count("Let me go.") == 2
    assert result[1] is messages[1]
    assert result[1].content[0].text == REPEATED


@pytest.mark.asyncio
async def test_save_to_history_sanitizes_before_dump():
    stage, event, req = _make_stage()
    messages = [Message(role="assistant", content=[ThinkPart(think=REPEATED)])]
    response = LLMResponse(role="assistant", completion_text="done")

    await stage._save_to_history(event, req, response, messages, None)

    history = stage.conv_manager.update_conversation.call_args.kwargs["history"]
    think_parts = [
        part
        for message in history
        for part in (message.get("content") or [])
        if isinstance(part, dict) and part.get("type") == "think"
    ]
    assert think_parts
    assert think_parts[0]["think"].count("Let me go.") == 2


@pytest.mark.asyncio
async def test_save_to_history_logs_when_sanitized(caplog):
    stage, event, req = _make_stage()
    messages = [Message(role="assistant", content=[ThinkPart(think=REPEATED)])]
    response = LLMResponse(role="assistant", completion_text="done")

    with _capture_astrbot_logs(caplog):
        await stage._save_to_history(event, req, response, messages, None)

    assert "context sanitizer" in caplog.text


@pytest.mark.asyncio
async def test_save_to_history_does_not_log_when_unchanged(caplog):
    stage, event, req = _make_stage()
    messages = [
        Message(
            role="assistant",
            content=[ThinkPart(think="A single unique thought.\n")],
        )
    ]
    response = LLMResponse(role="assistant", completion_text="done")

    with _capture_astrbot_logs(caplog):
        await stage._save_to_history(event, req, response, messages, None)

    assert "context sanitizer" not in caplog.text


@pytest.mark.asyncio
async def test_save_to_history_does_not_log_when_disabled(caplog):
    stage, event, req = _make_stage()
    messages = [Message(role="assistant", content=[ThinkPart(think=REPEATED)])]
    response = LLMResponse(role="assistant", completion_text="done")

    with _capture_astrbot_logs(caplog):
        await stage._save_to_history(
            event,
            req,
            response,
            messages,
            None,
            repetition_cfg=RepetitionSanitizeConfig(enabled=False),
        )

    assert "context sanitizer" not in caplog.text


@pytest.mark.asyncio
async def test_save_to_history_keeps_encrypted_think():
    stage, event, req = _make_stage()
    messages = [
        Message(
            role="assistant",
            content=[ThinkPart(think=REPEATED, encrypted="sig")],
        )
    ]
    response = LLMResponse(role="assistant", completion_text="done")

    await stage._save_to_history(event, req, response, messages, None)

    history = stage.conv_manager.update_conversation.call_args.kwargs["history"]
    think_parts = [
        part
        for message in history
        for part in (message.get("content") or [])
        if isinstance(part, dict) and part.get("type") == "think"
    ]
    assert think_parts
    assert think_parts[0]["think"] == REPEATED
    assert think_parts[0]["encrypted"] == "sig"
