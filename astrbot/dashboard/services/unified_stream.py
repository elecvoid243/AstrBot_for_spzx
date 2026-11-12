"""Minimal unified chat stream fan-out for ChatUI.

One SSE connection per browser tab carries every owned running chat run. The
full design adds per-session queues, replay, and out-of-band payloads; this
module intentionally implements only the transport needed by ticket 02.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

__all__ = ["SSE_HEARTBEAT", "UnifiedConnection", "UnifiedStreamHub"]

SSE_HEARTBEAT = ": heartbeat\n\n"
_HEARTBEAT_SECONDS = 15


@dataclass(slots=True)
class UnifiedConnection:
    """One browser tab's unified stream state."""

    connection_id: str
    username: str
    epoch: str
    cursors: dict[str, int] = field(default_factory=dict)
    queue: asyncio.Queue[dict[str, Any] | None] = field(default_factory=asyncio.Queue)
    seq_by_session: dict[str, int] = field(default_factory=dict)
    closed: bool = False


class UnifiedStreamHub:
    """Fan chat frames out to every unified stream of one account."""

    def __init__(self) -> None:
        self.epoch = str(uuid.uuid4())
        self.connections: dict[str, UnifiedConnection] = {}

    def connect(
        self,
        username: str,
        epoch: str = "",
        cursors: dict[str, int] | None = None,
    ) -> UnifiedConnection:
        """Register a browser tab's stream.

        Args:
            username: Trusted account that owns the stream.
            epoch: Client epoch from the last stream_ready frame, if any.
            cursors: Last seq per session, reserved for replay in ticket 04.

        Returns:
            The registered connection.
        """
        connection = UnifiedConnection(
            connection_id=str(uuid.uuid4()),
            username=username,
            epoch=epoch,
            cursors=dict(cursors or {}),
        )
        self.connections[connection.connection_id] = connection
        return connection

    def disconnect(self, connection: UnifiedConnection) -> None:
        """Remove a connection and wake its stream generator.

        Args:
            connection: Connection to close.
        """
        connection.closed = True
        self.connections.pop(connection.connection_id, None)
        try:
            connection.queue.put_nowait(None)
        except asyncio.QueueFull:
            # Ticket 04 replaces this queue with a bounded one.
            pass

    def publish(
        self,
        username: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Fan one chat payload out to every stream of the account.

        Args:
            username: Trusted account that owns the run.
            session_id: Chat session the payload belongs to.
            payload: Existing ChatService payload; session_id/seq are added.
        """
        for connection in list(self.connections.values()):
            if connection.username != username or connection.closed:
                continue
            seq = connection.seq_by_session.get(session_id, 0) + 1
            connection.seq_by_session[session_id] = seq
            frame = {**payload, "session_id": session_id, "seq": seq}
            connection.queue.put_nowait(frame)

    async def stream(self, connection: UnifiedConnection) -> AsyncIterator[str]:
        """Yield SSE frames for one connection until it closes.

        Args:
            connection: Registered connection to serve.

        Yields:
            Wire-ready SSE events, including periodic heartbeats.
        """
        try:
            ready = {"type": "stream_ready", "epoch": self.epoch}
            yield f"data: {json.dumps(ready, ensure_ascii=False)}\n\n"
            while not connection.closed:
                try:
                    frame = await asyncio.wait_for(
                        connection.queue.get(),
                        timeout=_HEARTBEAT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    yield SSE_HEARTBEAT
                    continue
                if frame is None:
                    break
                yield f"data: {json.dumps(frame, ensure_ascii=False)}\n\n"
        finally:
            self.disconnect(connection)
