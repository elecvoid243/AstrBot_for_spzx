"""Tests for dashboard static gzip encoding."""

import gzip

import httpx
import pytest

from astrbot.dashboard.static_encoding import (
    DashboardGZipMiddleware,
    accepts_gzip,
    gzip_static_body,
    is_compressible_media_type,
    with_gzip_headers,
)


def test_accepts_gzip_only_when_explicitly_allowed():
    assert accepts_gzip("gzip")
    assert accepts_gzip("br, gzip, deflate")
    assert accepts_gzip("gzip;q=0.8")
    assert not accepts_gzip(None)
    assert not accepts_gzip("")
    assert not accepts_gzip("br")
    assert not accepts_gzip("gzip;q=0")
    assert not accepts_gzip("identity")


def test_only_js_css_and_html_are_compressible():
    assert is_compressible_media_type("text/javascript")
    assert is_compressible_media_type("text/javascript; charset=utf-8")
    assert is_compressible_media_type("application/javascript")
    assert is_compressible_media_type("text/css")
    assert is_compressible_media_type("text/html")
    assert not is_compressible_media_type("font/woff2")
    assert not is_compressible_media_type("image/png")
    assert not is_compressible_media_type("image/svg+xml")
    assert not is_compressible_media_type("text/event-stream")
    assert not is_compressible_media_type("application/json")
    assert not is_compressible_media_type(None)


def test_gzip_static_body_shrinks_text_and_skips_tiny_payloads():
    raw = b"function boot(){return 'dashboard';}" * 80
    compressed = gzip_static_body(raw)
    assert compressed is not None
    assert len(compressed) < len(raw)
    assert gzip.decompress(compressed) == raw
    assert gzip_static_body(b"tiny") is None


def test_gzip_headers_keep_cache_control():
    headers = with_gzip_headers({"Cache-Control": "no-cache", "Vary": "Origin"})
    assert headers["Cache-Control"] == "no-cache"
    assert headers["Content-Encoding"] == "gzip"
    assert headers["Vary"] == "Origin, Accept-Encoding"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("accept_encoding", "media_type", "range_header", "already_encoded", "expected"),
    [
        ("gzip", "application/json", "", False, "gzip"),
        ("gzip", "text/javascript", "", False, "gzip"),
        ("gzip;q=0", "application/json", "", False, None),
        ("identity", "application/json", "", False, None),
        ("gzip", "text/javascript", "bytes=0-2047", False, None),
        ("gzip", "text/event-stream", "", False, None),
        ("gzip", "text/javascript", "", True, "gzip"),
    ],
)
async def test_dashboard_response_compression_preserves_client_and_stream_semantics(
    accept_encoding, media_type, range_header, already_encoded, expected
):
    raw = b"dashboard payload\n" * 200
    body = gzip.compress(raw) if already_encoded else raw
    headers = [(b"content-type", media_type.encode())]
    if already_encoded:
        headers.append((b"content-encoding", b"gzip"))

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    transport = httpx.ASGITransport(app=DashboardGZipMiddleware(app, minimum_size=1024))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        request_headers = {"Accept-Encoding": accept_encoding}
        if range_header:
            request_headers["Range"] = range_header
        response = await client.get("/", headers=request_headers)
    assert response.status_code == 200
    assert response.headers.get("Content-Encoding") == expected
    assert response.content == raw


@pytest.mark.asyncio
async def test_dashboard_gzip_does_not_buffer_sse_events():
    sent = []

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream; charset=utf-8")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"data: first\n\n",
                "more_body": True,
            }
        )
        assert sent[-1]["body"] == b"data: first\n\n"
        await send({"type": "http.response.body", "body": b"data: last\n\n"})

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    await DashboardGZipMiddleware(app, minimum_size=1024)(
        {"type": "http", "headers": [(b"accept-encoding", b"gzip")]}, receive, send
    )
    assert len(sent) == 3
    assert not any(name == b"content-encoding" for name, _ in sent[0]["headers"])
