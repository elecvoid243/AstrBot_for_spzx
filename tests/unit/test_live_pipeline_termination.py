"""Scope and exception semantics of Live terminal pipeline notifications."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.pipeline.context import PipelineContext
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.platform import AstrMessageEvent
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.webchat import webchat_event
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import WebChatQueueMgr
from astrbot.core.utils.active_event_registry import active_event_registry
from astrbot.core.utils.webchat_access import (
    dashboard_account_context,
    webchat_account_role,
    webchat_resource_owner,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["live", "chat", "qq"])
@pytest.mark.parametrize("failure_type", [RuntimeError, asyncio.CancelledError])
async def test_terminal_notification_is_live_only_and_keeps_original_error(
    kind, failure_type
):
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    event = adapter.create_event(
        await adapter.convert_message(
            (
                "alice",
                "terminal-scope",
                {"message_id": "scope", "message": [{"type": "plain", "text": "hi"}]},
            )
        )
    )
    if kind == "qq":
        event = AstrMessageEvent(
            event.message_str,
            event.message_obj,
            PlatformMetadata("qq", "QQ", "qq-main"),
            "alice",
        )
    # A non-WebChat event claiming the same action must remain unaffected.
    event.set_extra("action_type", "live" if kind != "chat" else None)
    event.send = AsyncMock()
    failure = failure_type("Controlled original failure")

    async def stage(current):
        raise failure

    pipeline = PipelineScheduler(PipelineContext({}, SimpleNamespace(), "default"))
    pipeline.stages = [SimpleNamespace(process=stage)]
    previous = (
        dashboard_account_context.get(),
        webchat_resource_owner.get(),
        webchat_account_role.get(),
    )
    with pytest.raises(failure_type) as raised:
        await pipeline.execute(event)
    assert raised.value is failure
    assert event not in active_event_registry._events.get(
        event.unified_msg_origin, set()
    )
    assert (
        dashboard_account_context.get(),
        webchat_resource_owner.get(),
        webchat_account_role.get(),
    ) == previous
    if kind == "live":
        event.send.assert_awaited_once_with(None)
    else:
        event.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_type", [RuntimeError, asyncio.CancelledError])
@pytest.mark.parametrize("notification_error", [RuntimeError, asyncio.CancelledError])
async def test_terminal_notification_failure_never_replaces_core_error(
    failure_type, notification_error
):
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    event = adapter.create_event(
        await adapter.convert_message(
            (
                "alice",
                "terminal-failure",
                {
                    "message_id": "notify",
                    "message": [{"type": "plain", "text": "hi"}],
                    "action_type": "live",
                },
            )
        )
    )
    event.send = AsyncMock(
        side_effect=notification_error("Controlled notification failure")
    )
    failure = failure_type("Original core failure")

    async def stage(current):
        raise failure

    pipeline = PipelineScheduler(PipelineContext({}, SimpleNamespace(), "default"))
    pipeline.stages = [SimpleNamespace(process=stage)]
    with pytest.raises(failure_type) as raised:
        await pipeline.execute(event)
    assert raised.value is failure
    event.send.assert_awaited_once_with(None)
    assert event not in active_event_registry._events.get(
        event.unified_msg_origin, set()
    )


@pytest.mark.asyncio
async def test_successful_live_pipeline_sends_only_one_terminal_notification():
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    event = adapter.create_event(
        await adapter.convert_message(
            (
                "alice",
                "terminal-success",
                {
                    "message_id": "success",
                    "message": [{"type": "plain", "text": "hi"}],
                    "action_type": "live",
                },
            )
        )
    )
    event.send = AsyncMock()
    pipeline = PipelineScheduler(PipelineContext({}, SimpleNamespace(), "default"))
    pipeline.stages = []
    await pipeline.execute(event)
    event.send.assert_awaited_once_with(None)


@pytest.mark.asyncio
async def test_terminal_notification_follows_already_queued_output(monkeypatch):
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    event = adapter.create_event(
        await adapter.convert_message(
            (
                "alice",
                "terminal-order",
                {
                    "message_id": "order",
                    "message": [{"type": "plain", "text": "hi"}],
                    "action_type": "live",
                },
            )
        )
    )
    queues = WebChatQueueMgr(back_queue_maxsize=1)
    monkeypatch.setattr(webchat_event, "webchat_queue_mgr", queues)
    monkeypatch.setattr(AstrMessageEvent, "send", AsyncMock())
    queue = queues.get_or_create_back_queue("order", "terminal-order")
    first = {"type": "plain", "message_id": "order", "data": "already produced"}
    queue.put_nowait(first)
    failure = RuntimeError("Core failed with a full output queue")
    entered = asyncio.Event()

    async def stage(current):
        entered.set()
        raise failure

    pipeline = PipelineScheduler(PipelineContext({}, SimpleNamespace(), "default"))
    pipeline.stages = [SimpleNamespace(process=stage)]
    worker = asyncio.create_task(pipeline.execute(event))
    try:
        await entered.wait()
        await asyncio.sleep(0)
        assert not worker.done()
        assert await queue.get() == first
        assert (await asyncio.wait_for(queue.get(), 5))["type"] == "end"
        with pytest.raises(RuntimeError) as raised:
            await worker
        assert raised.value is failure
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        queues.remove_queues("terminal-order")
