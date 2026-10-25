"""Interactive-choice pending registry and SSE push helpers.

In-memory pool managing the asyncio futures behind the ``ask_user_choice``
tool: the tool awaits the future, dashboard REST endpoints resolve it.

Migrated from the ``astrbot_plugin_ask_user`` plugin.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass(slots=True)
class PendingChoice:
    """A single waiting interactive choice."""

    request_id: str
    umo: str
    sse_message_id: str
    """message_id of the webchat event that triggered the tool call; used as
    the back-queue key so chat_service polls the SSE notifications."""
    future: asyncio.Future
    spec: dict
    created_at: float
    timeout_at: float


class InteractiveChoiceRegistry:
    """In-memory pending pool with O(1) lookup and a per-umo index.

    Attributes:
        _pending: request_id -> PendingChoice.
        _by_umo: umo -> set[request_id].
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingChoice] = {}
        self._by_umo: dict[str, set[str]] = {}

    def add(
        self,
        request_id: str,
        umo: str,
        future: asyncio.Future,
        spec: dict,
        created_at: float,
        timeout_at: float,
        sse_message_id: str = "",
    ) -> None:
        """Register a waiting choice (called before the tool awaits).

        Args:
            request_id: Unique request ID.
            umo: Unified message origin.
            future: The future the tool blocks on.
            spec: Choice spec dict rendered by the frontend.
            created_at: Creation timestamp.
            timeout_at: Expiry timestamp.
            sse_message_id: message_id of the originating webchat event, used
                for SSE notifications. Empty when unavailable.
        """
        self._pending[request_id] = PendingChoice(
            request_id=request_id,
            umo=umo,
            sse_message_id=sse_message_id,
            future=future,
            spec=spec,
            created_at=created_at,
            timeout_at=timeout_at,
        )
        self._by_umo.setdefault(umo, set()).add(request_id)

    def remove(self, request_id: str) -> None:
        """Remove a choice from the pool, cancelling an unfinished future."""
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return
        ids = self._by_umo.get(pending.umo)
        if ids is not None:
            ids.discard(request_id)
            if not ids:
                self._by_umo.pop(pending.umo, None)
        if not pending.future.done():
            pending.future.cancel()

    def list_pending_for_umo(self, umo: str) -> list[dict]:
        """List all still-pending choices for a umo.

        Args:
            umo: Unified message origin.

        Returns:
            ``[{request_id, spec, created_at, timeout_at}, ...]``; resolved,
            expired and removed entries are excluded.
        """
        ids = self._by_umo.get(umo, set())
        now = time.time()
        result = []
        for rid in list(ids):
            p = self._pending.get(rid)
            if p is None or p.future.done() or p.timeout_at < now:
                continue
            result.append(
                {
                    "request_id": p.request_id,
                    "spec": p.spec,
                    "created_at": p.created_at,
                    "timeout_at": p.timeout_at,
                }
            )
        return result

    def find_pending_by_umo(self, umo: str) -> PendingChoice | None:
        """Return the newest still-pending choice for a umo, or None.

        Used by the message interceptor to consume a plain-text reply as the
        pending choice's free-text answer.
        """
        ids = self._by_umo.get(umo, set())
        now = time.time()
        best: PendingChoice | None = None
        for rid in ids:
            p = self._pending.get(rid)
            if p is None or p.future.done() or p.timeout_at < now:
                continue
            if best is None or p.created_at > best.created_at:
                best = p
        return best

    def resolve(self, request_id: str, payload: dict) -> bool:
        """Set the future result.

        Args:
            request_id: ID registered via add().
            payload: User response, usually ``{choice_id, free_text}``.

        Returns:
            True on success, False when unknown or already done.
        """
        pending = self._pending.get(request_id)
        if pending is None:
            return False
        if pending.future.done():
            return False
        pending.future.set_result(payload)
        return True

    async def shutdown(self) -> None:
        """Graceful shutdown: cancel all pending futures."""
        for pending in list(self._pending.values()):
            if not pending.future.done():
                pending.future.cancel()
        self._pending.clear()
        self._by_umo.clear()


def extract_conversation_id(umo: str) -> str | None:
    """Extract the conversation_id (last segment of the session key).

    Example:
        ``"webchat:FriendMessage:webchat!alice!sess-abc"`` -> ``"sess-abc"``;
        ``None`` for non-webchat or malformed umos.
    """
    parts = umo.split(":", 2)
    if len(parts) < 3:
        return None
    session_key = parts[2]
    chunks = session_key.split("!")
    if len(chunks) < 3:
        return None
    return chunks[-1]


async def push_resolved_event_to_back_queue(
    request_id: str,
    umo: str,
    reason: str,
    sse_message_id: str,
) -> None:
    """Push an ``interactive_choice_resolved`` event to the webchat SSE queue.

    Shared by the tool (submitted/cancelled), the message interceptor and the
    dashboard cancel endpoint. Best-effort by design: callers wrap this in
    try/except where a broadcast failure must not fail the main flow.

    Args:
        request_id: Unique ID of the interactive-choice request.
        umo: Unified message origin.
        reason: Resolution reason (e.g. submitted / cancelled).
        sse_message_id: message_id of the originating webchat event, used as
            the back-queue key.
    """
    from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
        webchat_queue_mgr,
    )

    conversation_id = extract_conversation_id(umo)
    if not conversation_id:
        return
    back_queue = webchat_queue_mgr.get_or_create_back_queue(
        request_id=sse_message_id,
        conversation_id=conversation_id,
    )
    await back_queue.put(
        {
            "type": "interactive_choice_resolved",
            "data": {
                "request_id": request_id,
                "reason": reason,
                "umo": umo,
            },
            "message_id": sse_message_id,
        }
    )


# Global singleton.
registry = InteractiveChoiceRegistry()
