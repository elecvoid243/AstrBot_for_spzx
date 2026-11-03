"""WebChat consumers must be released when a pipeline aborts."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.pipeline.scheduler import (
    PipelineScheduler,
    WebChatMessageEvent,
    active_event_registry,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [None, "live"])
@pytest.mark.parametrize("error_type", [asyncio.CancelledError, RuntimeError])
async def test_aborted_webchat_pipeline_sends_end(monkeypatch, action, error_type):
    event = MagicMock(spec=WebChatMessageEvent)
    event.get_extra.side_effect = lambda key: action if key == "action_type" else None
    event.send = AsyncMock()
    monkeypatch.setattr(active_event_registry, "register", MagicMock())
    monkeypatch.setattr(active_event_registry, "unregister", MagicMock())

    async def fail(_event):
        raise error_type()

    pipeline = PipelineScheduler.__new__(PipelineScheduler)
    pipeline.stages = [SimpleNamespace(process=fail)]
    with pytest.raises(error_type):
        await pipeline.execute(event)
    event.send.assert_awaited_once_with(None)
    event.cleanup_temporary_local_files.assert_called_once()
