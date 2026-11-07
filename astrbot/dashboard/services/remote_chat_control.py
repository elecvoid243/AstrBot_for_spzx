"""Bounded process-local admission mailboxes, polled by the directly bound peer."""

import asyncio
import time


class RemoteChainControl:
    """Relay decisions without requiring an inbound connection to the origin."""

    def __init__(self):
        self.pending: dict[str, dict] = {}
        self.futures: dict[str, asyncio.Future] = {}
        self.last_poll = time.monotonic()
        self.expires = self.last_poll + 3600
        self.closed = False
        self.dirty = asyncio.Event()

    async def reserve(self, limit: int, request_id: str, consume: bool = True) -> dict:
        """Wait for the sole authority; never refund an uncertain reservation.

        Args:
            limit: Lowest participating limit known to the requesting peer.
            request_id: Idempotency identity preserved through every relay hop.
            consume: Whether this is a delivery reservation or a limit update.

        Returns:
            Authoritative admission result, or an explicit control failure.
        """
        if (
            self.closed
            or time.monotonic() > self.expires
            or time.monotonic() - self.last_poll > 10
            or len(self.pending) >= 128
        ):
            return {
                "error": "Automatic message chain control is unavailable; acceptance may be unknown"
            }
        if request_id not in self.futures:
            self.futures[request_id] = asyncio.get_running_loop().create_future()
            self.pending[request_id] = {
                "request_id": request_id,
                "limit": limit,
                "consume": consume,
            }
            self.dirty.set()
        try:
            return await asyncio.wait_for(asyncio.shield(self.futures[request_id]), 8)
        except asyncio.TimeoutError:
            self.closed = True
            return {
                "error": "Automatic message chain control timed out; acceptance is unknown"
            }
        finally:
            # A timed-out request is never sent again with a new identity.
            self.pending.pop(request_id, None)
            self.futures.pop(request_id, None)

    def poll(self, results: list[dict]) -> list[dict]:
        """Apply origin decisions and return outstanding requests to the poller.

        Args:
            results: Credential-bound peer's private authoritative decisions.

        Returns:
            Outstanding requests without any session or resource descriptors.
        """
        self.last_poll = time.monotonic()
        for result in results:
            request_id = result["request_id"]
            future = self.futures.get(request_id)
            if future is not None and not future.done():
                future.set_result(result["decision"])
                self.pending.pop(request_id, None)
        return list(self.pending.values())
