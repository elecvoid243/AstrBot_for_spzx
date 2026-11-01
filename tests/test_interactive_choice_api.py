"""Interactive-choice REST endpoint contract tests.

The endpoints live on the dashboard chat API's legacy router (paths
``/api/chat/interactive-choice/*``, unchanged from the plugin era so the
frontend needs no modification). Auth is bypassed with a dependency
override returning a fixed dashboard user.

Migrated from the astrbot_plugin_ask_user API tests.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from astrbot.core.tools.interactive_choice_registry import registry

ALICE_UMO = "webchat:FriendMessage:webchat!alice!sess-123"
BOB_UMO = "webchat:FriendMessage:webchat!bob!sess-456"


@pytest.fixture
def client():
    from astrbot.dashboard.api.auth import require_dashboard_user
    from astrbot.dashboard.api.chat import get_service, legacy_router
    from astrbot.dashboard.responses import ApiError, error
    from astrbot.dashboard.services.chat_service import ChatService

    service = ChatService.__new__(ChatService)
    service.db = SimpleNamespace(
        get_platform_session_by_id=AsyncMock(
            side_effect=lambda sid: SimpleNamespace(
                creator="alice" if sid == "sess-123" else "bob", platform_id="webchat"
            )
        )
    )
    test_app = FastAPI()
    test_app.dependency_overrides[get_service] = lambda: service
    test_app.include_router(legacy_router)

    @test_app.exception_handler(ApiError)
    async def api_error_handler(_request, exc: ApiError):
        return JSONResponse(error(exc.message), status_code=exc.status_code)

    test_app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    with TestClient(test_app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _clean_registry():
    registry._pending.clear()
    registry._by_umo.clear()
    yield
    registry._pending.clear()
    registry._by_umo.clear()


def _add_pending(rid: str, umo: str = ALICE_UMO):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    future = loop.create_future()
    registry.add(
        request_id=rid,
        umo=umo,
        future=future,
        spec={"type": "interactive_choice", "prompt": "p", "options": []},
        created_at=time.time(),
        timeout_at=time.time() + 600,
        sse_message_id="sse-1",
    )
    return future


def test_submit_404_when_unknown(client) -> None:
    resp = client.post(
        "/api/chat/interactive-choice/missing",
        json={"choice_id": "a"},
    )
    assert resp.status_code == 404


def test_submit_400_when_missing_choice_id(client) -> None:
    _add_pending("r1")
    resp = client.post("/api/chat/interactive-choice/r1", json={})
    assert resp.status_code == 400


def test_submit_403_for_other_users_choice(client) -> None:
    _add_pending("r1", umo=BOB_UMO)
    resp = client.post("/api/chat/interactive-choice/r1", json={"choice_id": "a"})
    assert resp.status_code == 403


def test_submit_success_resolves_future(client) -> None:
    future = _add_pending("r1")

    resp = client.post(
        "/api/chat/interactive-choice/r1",
        json={"choice_id": "b", "free_text": "note"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["data"]["request_id"] == "r1"
    assert future.result() == {"choice_id": "b", "free_text": "note"}


def test_submit_twice_returns_409(client) -> None:
    _add_pending("r1")
    client.post("/api/chat/interactive-choice/r1", json={"choice_id": "a"})
    resp = client.post("/api/chat/interactive-choice/r1", json={"choice_id": "a"})
    assert resp.status_code == 409


def test_cancel_404_when_unknown(client) -> None:
    resp = client.delete("/api/chat/interactive-choice/missing")
    assert resp.status_code == 404


def test_cancel_403_keeps_future(client) -> None:
    future = _add_pending("r1", umo=BOB_UMO)
    resp = client.delete("/api/chat/interactive-choice/r1")
    assert resp.status_code == 403
    assert not future.done()


def test_cancel_success_cancels_future(client, monkeypatch) -> None:
    future = _add_pending("r1")
    broadcasts: list[dict] = []

    async def _fake_push(**kwargs):
        broadcasts.append(kwargs)

    monkeypatch.setattr(
        "astrbot.dashboard.api.chat.push_resolved_event_to_back_queue",
        _fake_push,
    )

    resp = client.delete("/api/chat/interactive-choice/r1")

    assert resp.status_code == 200
    assert future.cancelled()
    assert len(broadcasts) == 1
    assert broadcasts[0]["reason"] == "cancelled"


def test_cancel_twice_second_returns_404(client) -> None:
    _add_pending("r1")
    client.delete("/api/chat/interactive-choice/r1")
    resp = client.delete("/api/chat/interactive-choice/r1")
    assert resp.status_code == 404


def test_pending_400_when_missing_session_id(client) -> None:
    resp = client.post("/api/chat/interactive-choice/pending")
    assert resp.status_code == 400


def test_pending_400_for_non_webchat_session(client) -> None:
    resp = client.post(
        "/api/chat/interactive-choice/pending",
        params={"session_id": "aiocqhttp:FriendMessage:x"},
    )
    assert resp.status_code == 400


def test_pending_403_for_other_user(client) -> None:
    _add_pending("r1", umo=BOB_UMO)
    resp = client.post(
        "/api/chat/interactive-choice/pending",
        params={"session_id": BOB_UMO},
    )
    assert resp.status_code == 403


def test_pending_returns_specs_with_request_id_and_expiry(client) -> None:
    _add_pending("r1")

    resp = client.post(
        "/api/chat/interactive-choice/pending",
        params={"session_id": ALICE_UMO},
    )

    assert resp.status_code == 200
    pending = resp.json()["data"]["pending"]
    assert len(pending) == 1
    assert pending[0]["request_id"] == "r1"
    assert pending[0]["expires_at"] > time.time()
    assert pending[0]["prompt"] == "p"
