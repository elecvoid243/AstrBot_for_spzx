"""RunEventBus fan-out, history and slow-consumer semantics."""

import asyncio

import pytest

from astrbot.dashboard.services.agent_team_run_service import RunEventBus


@pytest.mark.asyncio
async def test_history_replay_and_live_fanout():
    bus = RunEventBus()
    bus.emit({"type": "node_status", "node_id": "n1", "status": "running"})
    q = bus.subscribe()
    bus.emit({"type": "node_status", "node_id": "n1", "status": "done"})

    assert bus.history()[0]["node_id"] == "n1"
    event = await asyncio.wait_for(q.get(), timeout=1)
    assert event["status"] == "done"  # subscriber only sees post-subscription events
    bus.unsubscribe(q)


@pytest.mark.asyncio
async def test_slow_consumer_drops_oldest():
    bus = RunEventBus()
    q = bus.subscribe()
    for i in range(300):
        bus.emit({"type": "tick", "i": i})
    first = await asyncio.wait_for(q.get(), timeout=1)
    assert first["i"] > 0  # oldest events were dropped, emit never blocked
