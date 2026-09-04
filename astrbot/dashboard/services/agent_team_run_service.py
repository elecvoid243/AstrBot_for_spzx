"""Agent Teams run lifecycle: event bus, DAGRunner, run service (spec §6.3/§6.5/§6.6)."""

import asyncio


class RunEventBus:
    """Per-run in-memory event history plus live subscriber fan-out.

    Mirrors the agent-collab SSE multiplexer pattern: late subscribers get a
    full history replay from the SSE layer, live events flow through
    per-subscriber bounded queues with drop-oldest on slow consumers.
    """

    _SUB_QUEUE_MAX = 256

    def __init__(self) -> None:
        self._history: list[dict] = []
        self._subscribers: list[asyncio.Queue] = []

    def emit(self, event: dict) -> None:
        """Record an event and fan it out to all subscribers."""
        self._history.append(event)
        for queue in list(self._subscribers):
            if queue.qsize() >= self._SUB_QUEUE_MAX:
                try:
                    queue.get_nowait()  # drop-oldest
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    def subscribe(self) -> asyncio.Queue:
        """Register a live subscriber queue (post-subscription events only)."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._SUB_QUEUE_MAX)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Remove a previously registered subscriber queue."""
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def history(self) -> list[dict]:
        """Return a defensive copy of all emitted events."""
        return list(self._history)
