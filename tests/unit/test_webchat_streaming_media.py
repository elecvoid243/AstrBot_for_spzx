from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image as PILImage

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image, Plain
from astrbot.core.platform.sources.webchat import webchat_event
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
    OrphanRunRegistry,
    WebChatQueueMgr,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("send_mode", ["streaming", "orphan", "proactive"])
@pytest.mark.parametrize(
    ("chunks", "expected_text"),
    [
        pytest.param(((None,),), "", id="image-only"),
        pytest.param(((None,), ("Done.",)), "Done.", id="image-then-text"),
        pytest.param((("Done.",), (None,)), "Done.", id="text-then-image"),
        pytest.param((("Before ", None, "after."),), "Before after.", id="mixed-chain"),
        pytest.param((("One ", "two.", None),), "One two.", id="multiple-text-parts"),
        pytest.param(((None,), (None,)), "", id="multiple-images"),
        pytest.param(
            (("Literal [IMAGE]example.png", None),),
            "Literal [IMAGE]example.png",
            id="intentional-marker-text",
        ),
    ],
)
async def test_webchat_image_completion_contains_only_reply_text(
    tmp_path, monkeypatch, send_mode, chunks, expected_text
):
    """Keep image transport metadata out of completion and persisted reply text."""
    image_path = tmp_path / "source.png"
    PILImage.new("RGB", (2, 2), "blue").save(image_path)
    attachments_dir = tmp_path / "attachments"
    attachments_dir.mkdir()
    monkeypatch.setattr(webchat_event, "attachments_dir", str(attachments_dir))

    request_id = "image-completion-request"
    conversation_id = "image-completion-conversation"
    manager = WebChatQueueMgr()
    queue = manager.get_or_create_back_queue(
        request_id, conversation_id if send_mode != "orphan" else None
    )
    system_queue = manager.subscribe_system(conversation_id)
    registry = OrphanRunRegistry()
    persist_reply = AsyncMock()
    monkeypatch.setattr(webchat_event, "webchat_queue_mgr", manager)
    monkeypatch.setattr(webchat_event, "orphan_run_registry", registry)
    monkeypatch.setattr(webchat_event, "_persist_bot_reply_if_orphan", persist_reply)
    monkeypatch.setattr(webchat_event.AstrMessageEvent, "send_streaming", AsyncMock())

    chains = [
        MessageChain(
            [
                Image.fromFileSystem(str(image_path)) if text is None else Plain(text)
                for text in chunk
            ]
        )
        for chunk in chunks
    ]
    session_id = f"webchat!user!{conversation_id}"
    if send_mode == "proactive":
        result = await webchat_event.WebChatMessageEvent._send(
            request_id,
            MessageChain([part for chain in chains for part in chain.chain]),
            session_id,
            streaming=True,
            emit_complete=True,
        )
        assert result == expected_text
    else:
        event = webchat_event.WebChatMessageEvent.__new__(
            webchat_event.WebChatMessageEvent
        )
        event.message_obj = SimpleNamespace(message_id=request_id)
        event.session = SimpleNamespace(session_id=session_id)

        async def generate():
            emitted_text = ""
            for chain in chains:
                yield chain
                emitted_text += "".join(
                    part.text for part in chain.chain if isinstance(part, Plain)
                )
                if send_mode == "orphan":
                    run = registry.get(request_id)
                    assert run is not None
                    assert run.message_parts == (
                        [{"type": "plain", "text": emitted_text}]
                        if emitted_text
                        else []
                    )

        await event.send_streaming(generate())
        persist_reply.assert_awaited_once_with(
            session_id=session_id,
            request_id=request_id,
            plain_text=expected_text,
            reasoning_text="",
        )

    payloads = [queue.get_nowait() for _ in range(queue.qsize())]
    assert payloads == [system_queue.get_nowait() for _ in range(system_queue.qsize())]
    assert payloads[-1]["type"] == "complete"
    assert payloads[-1]["data"] == expected_text
    images = [payload for payload in payloads if payload["type"] == "image"]
    assert len(images) == sum(text is None for chunk in chunks for text in chunk)
    for payload in images:
        filename = payload["data"].removeprefix("[IMAGE]")
        assert filename.endswith(".png")
        assert (attachments_dir / filename).read_bytes() == image_path.read_bytes()
