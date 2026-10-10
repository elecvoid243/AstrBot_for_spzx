"""Gzip encoding for dashboard responses and static files.

Static file helpers compress JS, CSS, and HTML when the client accepts gzip.
The response middleware also handles API payloads, preserving event streams,
Range requests, and clients that explicitly decline gzip.
"""

from __future__ import annotations

import gzip
from collections.abc import Mapping

from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import Receive, Scope, Send

# text/javascript is what the stdlib reports for .js on current CPython.
# application/javascript is kept for older mimetypes maps.
COMPRESSIBLE_MEDIA_TYPES = frozenset(
    {
        "text/html",
        "text/css",
        "text/javascript",
        "application/javascript",
        "application/x-javascript",
    }
)

# Gzip of a few hundred bytes is often larger than the original.
MIN_GZIP_BYTES = 1024


class DashboardGZipMiddleware(GZipMiddleware):
    """Compress responses while preserving explicit encoding and Range requests."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Apply compression only when the request permits it.

        Args:
            scope: Current ASGI connection scope.
            receive: ASGI request receiver.
            send: ASGI response sender.
        """
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            # Starlette checks for the substring "gzip", including gzip;q=0.
            if headers.get("range") or not accepts_gzip(headers.get("accept-encoding")):
                await self.app(scope, receive, send)
                return
        await super().__call__(scope, receive, send)


def accepts_gzip(header: str | None) -> bool:
    """Return whether Accept-Encoding explicitly allows gzip with q > 0."""
    if not header:
        return False
    for part in header.split(","):
        bits = [item.strip() for item in part.split(";") if item.strip()]
        if not bits or bits[0].lower() != "gzip":
            continue
        quality = 1.0
        for param in bits[1:]:
            if not param.lower().startswith("q="):
                continue
            try:
                quality = float(param[2:])
            except ValueError:
                quality = 0.0
        return quality > 0
    return False


def is_compressible_media_type(media_type: str | None) -> bool:
    """Return whether this Content-Type is JS, CSS, or HTML."""
    if not media_type:
        return False
    base = media_type.split(";", 1)[0].strip().lower()
    return base in COMPRESSIBLE_MEDIA_TYPES


def gzip_static_body(data: bytes) -> bytes | None:
    """Return a smaller gzip body, or None when compression should be skipped."""
    if len(data) < MIN_GZIP_BYTES:
        return None
    compressed = gzip.compress(data, compresslevel=6)
    if len(compressed) >= len(data):
        return None
    return compressed


def with_gzip_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Add gzip headers without changing Cache-Control."""
    merged = dict(headers)
    merged["Content-Encoding"] = "gzip"
    vary = merged.get("Vary", "")
    parts = [part.strip() for part in vary.split(",") if part.strip()]
    if not any(part.lower() == "accept-encoding" for part in parts):
        parts.append("Accept-Encoding")
    merged["Vary"] = ", ".join(parts)
    return merged
