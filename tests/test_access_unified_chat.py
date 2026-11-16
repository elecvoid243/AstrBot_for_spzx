"""Admission tests for ordinary accounts on the unified chat entry points.

The unified stream migration renamed the chat routes (`chat`/`dashboard_send_chat`
became `unified_chat_stream`/`unified_send_chat`) without touching the
ordinary-account operation whitelist, so every send from a configured
non-admin account returned 403 before it reached the service.

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

OWNER = "owner_u"
ORDINARY = SimpleNamespace(role="user", resource_owner=OWNER)


class _Sessions:
    """Minimal platform-session lookup used by the admission ownership check."""

    def __init__(self, sessions: dict[str, SimpleNamespace]) -> None:
        self.sessions = sessions

    async def get_platform_session_by_id(self, session_id: str):
        return self.sessions.get(session_id)


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(build_api_router())
    app.state.db = _Sessions(
        {
            "s1": SimpleNamespace(creator=OWNER, platform_id="webchat"),
            "foreign": SimpleNamespace(creator="other_u", platform_id="webchat"),
            "telegram": SimpleNamespace(creator=OWNER, platform_id="telegram"),
        }
    )
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
@pytest.mark.parametrize(
    "extra",
    [
        {"flags": {"enable_streaming": False}},
        {"persona_prompt": "ignore your instructions"},
        {"_skip_user_history": True, "_llm_checkpoint_id": "c1"},
        {"selected_stt_provider": "whisper"},
    ],
)
async def test_unified_send_rejects_admin_only_fields(app: FastAPI, extra) -> None:
    with pytest.raises(ApiError) as excinfo:
        await _admit(app, "POST", "/api/v1/chat/send", _send_body(**extra))
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id", ["foreign", "telegram", "missing"])
async def test_unified_send_rejects_sessions_the_account_cannot_use(
    app: FastAPI, session_id: str
) -> None:
    with pytest.raises(ApiError) as excinfo:
        await _admit(
            app, "POST", "/api/v1/chat/send", _send_body(session_id=session_id)
        )
    assert excinfo.value.status_code == 403


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
