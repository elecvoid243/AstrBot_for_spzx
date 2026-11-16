"""Synthetic runs must stream to the unified hub, not only announce themselves.

Goal-loop, cron and Agent Teams turns register through
register_synthetic_chat_run. That path published run_started to the hub but
mirrored no frames and no terminal end, so a live unified tab showed an empty
bubble that never finished until a refresh re-read history.

Author: elecvoid243
Date: 2026-11-16
"""

import asyncio
import json

import pytest

from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.dashboard.services.chat_service import ChatService
from astrbot.dashboard.services.unified_stream import UnifiedStreamHub

pytestmark = pytest.mark.asyncio

SESSION_ID = "sess-synthetic"
RUN_ID = "run-synthetic"


def _bare_service() -> ChatService:
    """ChatService with only the state a synthetic registration touches."""
    service = ChatService.__new__(ChatService)
    service.chat_runs = {}
    service.chat_runs_by_session = {}
    service.running_convs = {}
    service.unified_stream_hub = UnifiedStreamHub()
    service._unified_publish_tasks = set()
    return service


def _frame(wire: str) -> dict:
    return json.loads(wire[len("data: ") :])


async def test_synthetic_run_streams_frames_and_end_to_the_hub() -> None:
    service = _bare_service()
    connection = service.unified_stream_hub.connect("alice", "epoch-1", {})
    frames = service.unified_stream_hub.stream(connection)
    assert _frame(await anext(frames))["type"] == "stream_ready"
    run = None
    try:
        await service.register_synthetic_chat_run(SESSION_ID, RUN_ID, "alice")
        run = service.chat_runs[RUN_ID]

        started = _frame(await asyncio.wait_for(anext(frames), timeout=1))
        assert started["type"] == "run_started"
        assert started["message_id"] == RUN_ID

        # The forwarder subscribes synchronously with the registration, before
        # the announcement, so frames published in between cannot be lost.
        assert run.unified_forwarded is True
        assert len(run.subscribers) == 1
        subscriber = next(iter(run.subscribers))
        subscriber.put_nowait((run.revision + 1, {"type": "plain", "data": "hi"}))

        frame = _frame(await asyncio.wait_for(anext(frames), timeout=1))
        assert frame["type"] == "plain"
        assert frame["data"] == "hi"
        assert frame["message_id"] == RUN_ID

        subscriber.put_nowait((run.revision + 2, {"type": "end", "data": None}))
        assert _frame(await asyncio.wait_for(anext(frames), timeout=1))["type"] == "end"
    finally:
        tasks = list(service._unified_publish_tasks)
        if run is not None:
            tasks.append(run.task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await frames.aclose()
        webchat_queue_mgr.remove_queues(SESSION_ID)


async def test_forwarder_registration_is_idempotent() -> None:
    """Two registration paths on one run must not double every frame."""
    service = _bare_service()
    service.unified_stream_hub.connect("alice", "epoch-1", {})
    run = None
    try:
        await service.register_synthetic_chat_run(SESSION_ID, RUN_ID, "alice")
        run = service.chat_runs[RUN_ID]
        service._start_unified_run_forwarder(run)

        assert len(run.subscribers) == 1
    finally:
        tasks = list(service._unified_publish_tasks)
        if run is not None:
            tasks.append(run.task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        webchat_queue_mgr.remove_queues(SESSION_ID)
