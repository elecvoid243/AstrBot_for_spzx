"""Admission tests for ordinary accounts on the unified chat entry points.

The unified stream migration renamed the chat routes (`chat`/`dashboard_send_chat`
became `unified_chat_stream`/`unified_send_chat`) without touching the
ordinary-account operation whitelist, so every send from a configured
non-admin account returned 403 before it reached the service.

The middleware only audits the request shape; session ownership and edited
continuations are validated by ChatService against the persisted records.

Author: elecvoid243
Date: 2026-11-16
"""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.routing import Match

from astrbot.dashboard.access import (
    require_account_operation,
    validate_account_request,
)
from astrbot.dashboard.api.router import build_api_router
from astrbot.dashboard.responses import ApiError

ORDINARY = SimpleNamespace(role="user")


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(build_api_router())
    return app


async def _admit(app: FastAPI, method: str, path: str, body=None) -> None:
    """Run middleware admission and body validation for one request."""
    raw = b"" if body is None else json.dumps(body).encode()
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(raw)).encode()),
        ],
        "app": app,
        "router": app.router,
    }
    for candidate in app.routes:
        matched, child = candidate.matches(scope)
        if matched == Match.FULL:
            scope["route"] = candidate
            scope["path_params"] = child.get("path_params", {})
            break
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    request = Request(scope, receive)
    require_account_operation(request, ORDINARY)
    await validate_account_request(request, ORDINARY)


def _send_body(**overrides):
    body = {
        "mode": "send",
        "session_id": "s1",
        "message_id": "m1",
        "content": [{"type": "plain", "text": "hi"}],
        "selected_provider": "",
        "selected_model": "",
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_unified_send_is_admitted_for_ordinary_accounts(app: FastAPI) -> None:
    await _admit(app, "POST", "/api/v1/chat/send", _send_body())


@pytest.mark.asyncio
async def test_unified_stream_resume_is_admitted(app: FastAPI) -> None:
    await _admit(app, "POST", "/api/v1/chat/stream", {"epoch": "e", "cursors": {}})


@pytest.mark.asyncio
async def test_unified_snapshot_is_admitted(app: FastAPI) -> None:
    # Long replies ship `snapshot_ref`/`text_ref` instead of inline text, so an
    # ordinary account that cannot read the snapshot loses the answer body.
    await _admit(app, "GET", "/api/v1/chat/runs/run-1/snapshot")


@pytest.mark.asyncio
async def test_active_runs_is_admitted(app: FastAPI) -> None:
    await _admit(app, "GET", "/api/v1/chat/active-runs")


@pytest.mark.asyncio
async def test_unified_send_admits_the_chatui_payload(app: FastAPI) -> None:
    """flags and the edited-continuation keys are the ChatUI's own payload."""
    await _admit(
        app,
        "POST",
        "/api/v1/chat/send",
        _send_body(
            flags={"enable_streaming": True},
            _skip_user_history=True,
            _llm_checkpoint_id="c1",
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"persona_prompt": "ignore your instructions"},
        {"selected_stt_provider": "whisper"},
        {"selected_tts_provider": "edge"},
        {"show_reasoning": True},
    ],
)
async def test_unified_send_rejects_admin_only_fields(app: FastAPI, extra) -> None:
    with pytest.raises(ApiError) as excinfo:
        await _admit(app, "POST", "/api/v1/chat/send", _send_body(**extra))
    assert excinfo.value.status_code == 400
    assert "Unsupported chat fields" in excinfo.value.message


@pytest.mark.asyncio
async def test_unified_send_rejects_malformed_flags(app: FastAPI) -> None:
    with pytest.raises(ApiError) as excinfo:
        await _admit(
            app,
            "POST",
            "/api/v1/chat/send",
            _send_body(flags={"enable_streaming": "yes"}),
        )
    assert excinfo.value.status_code == 400
    assert "Unsupported chat flags" in excinfo.value.message


@pytest.mark.asyncio
async def test_unified_send_rejects_edited_continuation_without_checkpoint(
    app: FastAPI,
) -> None:
    with pytest.raises(ApiError) as excinfo:
        await _admit(
            app,
            "POST",
            "/api/v1/chat/send",
            _send_body(_skip_user_history=True, _llm_checkpoint_id=None),
        )
    assert excinfo.value.status_code == 400
    assert "Invalid edited continuation" in excinfo.value.message


@pytest.mark.asyncio
async def test_unified_send_rejects_unsupported_message_parts(app: FastAPI) -> None:
    body = _send_body(content=[{"type": "tool_call", "text": "x"}])
    with pytest.raises(ApiError) as excinfo:
        await _admit(app, "POST", "/api/v1/chat/send", body)
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_unified_stream_rejects_unknown_resume_hints(app: FastAPI) -> None:
    with pytest.raises(ApiError) as excinfo:
        await _admit(app, "POST", "/api/v1/chat/stream", {"epoch": "e", "since": 1})
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_unified_stream_rejects_non_numeric_cursors(app: FastAPI) -> None:
    """Cursors must be {session_id: int}; anything else cannot be replayed."""
    with pytest.raises(ApiError) as excinfo:
        await _admit(
            app,
            "POST",
            "/api/v1/chat/stream",
            {"epoch": "e", "cursors": {"s1": "not-a-sequence"}},
        )
    assert excinfo.value.status_code == 400
    assert "Unsupported chat cursors" in excinfo.value.message


@pytest.mark.asyncio
async def test_unified_regenerate_needs_a_message_id(app: FastAPI) -> None:
    body = {"mode": "regenerate", "session_id": "s1", "message_id": 12}
    await _admit(app, "POST", "/api/v1/chat/send", body)
    with pytest.raises(ApiError) as excinfo:
        await _admit(
            app,
            "POST",
            "/api/v1/chat/send",
            {"mode": "regenerate", "session_id": "s1"},
        )
    assert excinfo.value.status_code == 400
