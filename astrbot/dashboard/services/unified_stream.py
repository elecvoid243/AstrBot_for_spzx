"""Unified chat stream transport with backpressure and replay.

One SSE connection per browser tab carries every owned running chat run.
Each connection keeps a control queue and a bounded data queue per session;
control frames jump the token backlog, while consecutive plain deltas are
coalesced before they reach the wire. A per-session ring buffer lets a
reconnect replay only the frames after its cursor, falling back to a fresh
snapshot when the gap is too large.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

__all__ = ["SSE_HEARTBEAT", "UnifiedConnection", "UnifiedStreamHub"]

logger = logging.getLogger(__name__)

SSE_HEARTBEAT = ": heartbeat\n\n"
_HEARTBEAT_SECONDS = 15

_CONTROL_TYPES = {
    "stream_ready",
    "run_started",
    "end",
    "error",
    "interactive_choice",
    "interactive_choice_resolved",
    "goal_state_changed",
    "shell_sessions_changed",
    "chat_session_created",
    "session_state",
    "resync_required",
}
_TERMINAL_TYPES = {"end", "error"}
_NO_MERGE_CHAIN_TYPES = {"tool_call", "interactive_choice", "context_compression"}

_RING_MAX_FRAMES = 2000
_RING_MAX_BYTES = 2 * 1024 * 1024
_RING_MAX_AGE_SECONDS = 60.0
_DATA_MAX_FRAMES = 2000
_DATA_MAX_BYTES = 2 * 1024 * 1024
_MERGE_MAX_BYTES = 64 * 1024
_BATCH_MAX_FRAMES = 8
_BATCH_MAX_BYTES = 64 * 1024


@dataclass(slots=True)
class _Frame:
    """One queued frame; ``seq`` is None for connection-local signals."""

    seq: int | None
    payload: dict[str, Any]


@dataclass(slots=True)
class _StoredFrame:
    """One wire frame retained for cursor replay."""

    seq: int
    payload: dict[str, Any]
    size: int
    created_at: float


class _SessionRing:
    """Bounded per-session history of already-serialized frames."""

    def __init__(self) -> None:
        self.frames: deque[_StoredFrame] = deque()
        self.bytes = 0
        self.min_seq: int | None = None

    def append(
        self,
        seq: int,
        payload: dict[str, Any],
        size: int,
        now: float,
    ) -> None:
        """Append one frame and evict from the oldest side.

        Args:
            seq: Per-session sequence number.
            payload: Wire payload without ``seq``.
            size: Serialized wire size in bytes.
            now: Monotonic timestamp used for the age cap.
        """
        self.frames.append(_StoredFrame(seq, payload, size, now))
        self.bytes += size
        self._evict(now)

    def _evict(self, now: float) -> None:
        while self.frames and (
            len(self.frames) > _RING_MAX_FRAMES
            or self.bytes > _RING_MAX_BYTES
            or now - self.frames[0].created_at > _RING_MAX_AGE_SECONDS
        ):
            self.bytes -= self.frames.popleft().size
        self.min_seq = self.frames[0].seq if self.frames else None

    def covers(self, cursor: int, now: float) -> bool:
        """Return whether the ring can replay everything after ``cursor``.

        Args:
            cursor: Last sequence number received by the client.
            now: Monotonic timestamp used for the age cap.

        Returns:
            True when the cursor is still inside the retained window.
        """
        self._evict(now)
        return self.min_seq is not None and cursor >= self.min_seq

    def replay_after(self, cursor: int, now: float) -> list[_StoredFrame]:
        """Return retained frames newer than ``cursor``.

        Args:
            cursor: Last sequence number received by the client.
            now: Monotonic timestamp used for the age cap.

        Returns:
            Frames in ascending sequence order.
        """
        self._evict(now)
        return [frame for frame in self.frames if frame.seq > cursor]


@dataclass(slots=True)
class UnifiedConnection:
    """One browser tab's unified stream state."""

    connection_id: str
    username: str
    epoch: str
    cursors: dict[str, int] = field(default_factory=dict)
    control_queues: dict[str, deque[_Frame]] = field(default_factory=dict)
    data_queues: dict[str, deque[_Frame]] = field(default_factory=dict)
    data_bytes: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)
    dropping: set[str] = field(default_factory=set)
    scheduler_sessions: deque[str] = field(default_factory=deque)
    scheduler_set: set[str] = field(default_factory=set)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    closed: bool = False
    replayed_sessions: set[str] = field(default_factory=set)
    last_sent_at: float = 0.0


def _is_control(payload: dict[str, Any]) -> bool:
    return payload.get("type") in _CONTROL_TYPES


def _is_terminal(payload: dict[str, Any]) -> bool:
    return payload.get("type") in _TERMINAL_TYPES


def _mergeable(payload: dict[str, Any]) -> bool:
    return (
        payload.get("type") == "plain"
        and payload.get("chain_type") not in _NO_MERGE_CHAIN_TYPES
    )


def _wire(frame: _Frame) -> str:
    payload = frame.payload
    if frame.seq is not None:
        payload = {**payload, "seq": frame.seq}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _wire_size(payload: dict[str, Any], seq: int | None) -> int:
    return len(_wire(_Frame(seq, payload)).encode("utf-8"))


class UnifiedStreamHub:
    """Fan chat frames out to every unified stream of one account."""

    def __init__(self) -> None:
        self.epoch = str(uuid.uuid4())
        self.connections: dict[str, UnifiedConnection] = {}
        self.seq_by_session: dict[str, int] = {}
        self.rings: dict[str, _SessionRing] = {}

    def connect(
        self,
        username: str,
        epoch: str = "",
        cursors: dict[str, int] | None = None,
    ) -> UnifiedConnection:
        """Register a browser tab's stream and replay covered cursors.

        Args:
            username: Trusted account that owns the stream.
            epoch: Client epoch from the last ``stream_ready`` frame.
            cursors: Last seq per session.

        Returns:
            The registered connection.
        """
        clean_cursors: dict[str, int] = {}
        for session_id, cursor in (cursors or {}).items():
            try:
                value = int(cursor)
            except (TypeError, ValueError):
                continue
            if value >= 0:
                clean_cursors[str(session_id)] = value
        if epoch != self.epoch:
            clean_cursors = {}

        connection = UnifiedConnection(
            connection_id=str(uuid.uuid4()),
            username=username,
            epoch=epoch,
            cursors=clean_cursors,
        )
        self.connections[connection.connection_id] = connection

        now = time.monotonic()
        for session_id, cursor in clean_cursors.items():
            ring = self.rings.get(session_id)
            if ring is None or not ring.covers(cursor, now):
                continue
            for stored in ring.replay_after(cursor, now):
                self._enqueue(
                    connection,
                    session_id,
                    stored.payload,
                    stored.seq,
                    merge=False,
                )
            connection.replayed_sessions.add(session_id)
        return connection

    def disconnect(self, connection: UnifiedConnection) -> None:
        """Remove a connection and wake its stream generator.

        Args:
            connection: Connection to close.
        """
        connection.closed = True
        self.connections.pop(connection.connection_id, None)
        connection.wake.set()

    def publish(
        self,
        username: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Store and fan one payload out to every stream of the account.

        Args:
            username: Trusted account that owns the run.
            session_id: Chat session the payload belongs to.
            payload: Existing ChatService payload.
        """
        seq = self._next_seq(session_id)
        frame_payload = {**payload, "session_id": session_id}
        self._store(session_id, seq, frame_payload)
        for connection in list(self.connections.values()):
            if connection.username != username or connection.closed:
                continue
            self._enqueue(connection, session_id, frame_payload, seq, merge=True)

    def publish_to(
        self,
        connection: UnifiedConnection,
        session_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Store and queue one payload on a single connection.

        The frame still enters the session ring so a later reconnect can
        replay the snapshot that this connection just received.

        Args:
            connection: Target connection.
            session_id: Chat session the payload belongs to.
            payload: Existing ChatService payload.
        """
        if connection.closed:
            return
        seq = self._next_seq(session_id)
        frame_payload = {**payload, "session_id": session_id}
        self._store(session_id, seq, frame_payload)
        self._enqueue(connection, session_id, frame_payload, seq, merge=False)

    def _next_seq(self, session_id: str) -> int:
        seq = self.seq_by_session.get(session_id, 0) + 1
        self.seq_by_session[session_id] = seq
        return seq

    def _store(
        self,
        session_id: str,
        seq: int,
        payload: dict[str, Any],
    ) -> None:
        ring = self.rings.setdefault(session_id, _SessionRing())
        ring.append(seq, payload, _wire_size(payload, seq), time.monotonic())

    def _schedule(self, connection: UnifiedConnection, session_id: str) -> None:
        if session_id in connection.scheduler_set:
            return
        connection.scheduler_sessions.append(session_id)
        connection.scheduler_set.add(session_id)

    def _enqueue(
        self,
        connection: UnifiedConnection,
        session_id: str,
        payload: dict[str, Any],
        seq: int | None,
        *,
        merge: bool,
    ) -> None:
        if connection.closed:
            return
        if _is_control(payload):
            connection.control_queues.setdefault(session_id, deque()).append(
                _Frame(seq, payload)
            )
        else:
            queue = connection.data_queues.setdefault(session_id, deque())
            if not (merge and self._merge_into(queue, payload, seq)):
                queue.append(_Frame(seq, payload))
                connection.data_bytes[session_id] = connection.data_bytes.get(
                    session_id, 0
                ) + _wire_size(payload, seq)
            self._evict_data(connection, session_id)
        self._schedule(connection, session_id)
        connection.wake.set()

    @staticmethod
    def _merge_into(
        queue: deque[_Frame],
        payload: dict[str, Any],
        seq: int | None,
    ) -> bool:
        if not queue:
            return False
        last = queue[-1]
        if not _mergeable(last.payload) or not _mergeable(payload):
            return False
        if last.payload.get("message_id") != payload.get("message_id"):
            return False
        if last.payload.get("chain_type") != payload.get("chain_type"):
            return False
        left = last.payload.get("data")
        right = payload.get("data")
        if not isinstance(left, str) or not isinstance(right, str):
            return False
        merged = left + right
        if len(merged.encode("utf-8")) > _MERGE_MAX_BYTES:
            return False
        last.payload = {**payload, "data": merged}
        last.seq = seq
        return True

    @staticmethod
    def _evict_data(connection: UnifiedConnection, session_id: str) -> None:
        queue = connection.data_queues.get(session_id)
        if not queue:
            connection.data_bytes.pop(session_id, None)
            connection.dropping.discard(session_id)
            return
        dropped_from: int | None = None
        dropped = 0
        while queue and (
            len(queue) > _DATA_MAX_FRAMES
            or connection.data_bytes.get(session_id, 0) > _DATA_MAX_BYTES
        ):
            old = queue.popleft()
            connection.data_bytes[session_id] = max(
                0,
                connection.data_bytes.get(session_id, 0)
                - _wire_size(old.payload, old.seq),
            )
            dropped += 1
            if dropped_from is None and old.seq is not None:
                dropped_from = old.seq
        if not dropped:
            return
        connection.dropped[session_id] = connection.dropped.get(session_id, 0) + dropped
        logger.debug(
            "Unified stream dropped %d frames for %s (queue=%d, bytes=%d)",
            dropped,
            session_id,
            len(queue),
            connection.data_bytes.get(session_id, 0),
        )
        if session_id not in connection.dropping:
            connection.dropping.add(session_id)
            connection.control_queues.setdefault(session_id, deque()).append(
                _Frame(
                    None,
                    {
                        "type": "resync_required",
                        "session_id": session_id,
                        "from_seq": dropped_from or 0,
                    },
                )
            )
        if not queue:
            connection.data_bytes.pop(session_id, None)
            connection.dropping.discard(session_id)

    def _cleanup(self, connection: UnifiedConnection, session_id: str) -> None:
        connection.control_queues.pop(session_id, None)
        connection.data_queues.pop(session_id, None)
        connection.data_bytes.pop(session_id, None)
        connection.dropping.discard(session_id)
        connection.scheduler_set.discard(session_id)

    def _next_batch(self, connection: UnifiedConnection) -> list[str] | None:
        while connection.scheduler_sessions:
            session_id = connection.scheduler_sessions.popleft()
            connection.scheduler_set.discard(session_id)
            control = connection.control_queues.get(session_id)
            data = connection.data_queues.get(session_id)
            if not control and not data:
                self._cleanup(connection, session_id)
                continue

            wires: list[str] = []
            while control:
                frame = control[0]
                if _is_terminal(frame.payload) and data:
                    break
                control.popleft()
                wires.append(_wire(frame))

            sent = 0
            sent_bytes = 0
            while data and sent < _BATCH_MAX_FRAMES:
                frame = data[0]
                wire = _wire(frame)
                size = len(wire.encode("utf-8"))
                if sent and sent_bytes + size > _BATCH_MAX_BYTES:
                    break
                data.popleft()
                connection.data_bytes[session_id] = max(
                    0,
                    connection.data_bytes.get(session_id, 0)
                    - _wire_size(frame.payload, frame.seq),
                )
                sent += 1
                sent_bytes += size
                wires.append(wire)

            if not data:
                connection.data_bytes.pop(session_id, None)
                connection.dropping.discard(session_id)
            if control or data:
                self._schedule(connection, session_id)
            else:
                self._cleanup(connection, session_id)
            if wires:
                return wires
        return None

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
            connection.last_sent_at = time.monotonic()
            while not connection.closed:
                batch = self._next_batch(connection)
                if batch:
                    for wire in batch:
                        yield wire
                        connection.last_sent_at = time.monotonic()
                    continue
                elapsed = time.monotonic() - connection.last_sent_at
                timeout = max(0.0, _HEARTBEAT_SECONDS - elapsed)
                if timeout <= 0:
                    yield SSE_HEARTBEAT
                    connection.last_sent_at = time.monotonic()
                    continue
                try:
                    await asyncio.wait_for(connection.wake.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    yield SSE_HEARTBEAT
                    connection.last_sent_at = time.monotonic()
                    continue
                connection.wake.clear()
        finally:
            self.disconnect(connection)
