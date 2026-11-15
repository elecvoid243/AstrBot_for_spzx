"""Route contract tests for the ChatUI developer-mode snapshot API.

Task 8 of the developer-mode plan: the list/detail routes are admin-only
and scoped to an owned WebChat session, while the developer-mode toggle is
read on the chat scope and written on the config scope.

Author: elecvoid243, 2026-11-15
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from astrbot.dashboard.api import chat as chat_api
from astrbot.dashboard.api.auth import AuthContext
from astrbot.dashboard.responses import ApiError, error
from astrbot.dashboard.services.chat_service import ChatServiceError

SESSION_ID = "s1"
OWNER = "alice"
LIMITS = {
    "max_bytes_per_snapshot": 512 * 1024,
    "max_snapshots_per_session": 50,
    "max_bytes_per_session": 8 * 1024 * 1024,
    "max_total_bytes": 64 * 1024 * 1024,
    "max_message_bindings": 1024,
    "ttl_seconds": 3600,
}
SNAPSHOT_ENVELOPE = {
    "snapshot_id": "snap-1",
    "session_id": SESSION_ID,
    "turn_id": "turn-1",
    "status": "ok",
}


class _FakeChatService:
    """Async service stub mirroring the Task 7 snapshot readers."""

    def __init__(self) -> None:
        self.developer_mode = False

    async def get_llm_snapshots(
        self,
        username: str,
        session_id: str,
        *,
        message_id: int | str | None = None,
        turn_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        if username != OWNER or session_id != SESSION_ID:
            raise ChatServiceError("Permission denied")
        expired = message_id is not None and str(message_id) == "999"
        return {
            "enabled": True,
            "expired": expired,
            "items": [] if expired else [dict(SNAPSHOT_ENVELOPE)],
            "limits": dict(LIMITS),
            "dropped": 0,
        }

    async def get_llm_snapshot(self, username: str, snapshot_id: str) -> dict[str, Any]:
        if username != OWNER:
            raise ChatServiceError("Permission denied")
        if snapshot_id != "snap-1":
            raise ChatServiceError("Snapshot not found")
        return {
            "envelope": dict(SNAPSHOT_ENVELOPE),
            "raw": {"messages": []},
            "view": {"sections": []},
        }

    def get_llm_snapshot_limits(self) -> dict[str, int]:
        return dict(LIMITS)

    def get_chatui_developer_mode(self) -> bool:
        return self.developer_mode

    def set_chatui_developer_mode(self, enabled: object) -> dict[str, bool]:
        if not isinstance(enabled, bool):
            raise ChatServiceError("developer_mode must be a boolean")
        self.developer_mode = enabled
        return {"enabled": enabled}


def _auth_from_test_header(request: Request) -> AuthContext:
    role = request.headers.get("X-Test-Role", "user")
    return AuthContext(
        username=OWNER,
        scopes=["*"] if role == "admin" else ["chat"],
        role=role,
    )


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    service = _FakeChatService()
    app.include_router(chat_api.router)
    app.dependency_overrides[chat_api.require_chat_scope] = _auth_from_test_header
    app.dependency_overrides[chat_api.require_config_scope] = _auth_from_test_header
    app.dependency_overrides[chat_api.get_service] = lambda: service

    @app.exception_handler(ApiError)
    async def api_error_handler(_request: Request, exc: ApiError):
        return JSONResponse(error(exc.message), status_code=exc.status_code)

    with TestClient(app) as test_client:
        yield test_client


def test_list_rejects_non_admin(client: TestClient) -> None:
    res = client.get(
        f"/chat/sessions/{SESSION_ID}/llm-snapshots",
        headers={"X-Test-Role": "user"},
    )
    assert res.status_code == 403


def test_list_rejects_foreign_session(client: TestClient) -> None:
    res = client.get(
        "/chat/sessions/other/llm-snapshots",
        headers={"X-Test-Role": "admin"},
    )
    assert res.status_code == 403


def test_list_rejects_missing_session(client: TestClient) -> None:
    res = client.get(
        "/chat/sessions/missing/llm-snapshots",
        headers={"X-Test-Role": "admin"},
    )
    assert res.status_code == 403


def test_owner_admin_gets_items(client: TestClient) -> None:
    res = client.get(
        f"/chat/sessions/{SESSION_ID}/llm-snapshots",
        headers={"X-Test-Role": "admin"},
    )
    assert res.status_code == 200
    assert res.json()["data"]["items"]


def test_message_query_reports_expired(client: TestClient) -> None:
    res = client.get(
        f"/chat/sessions/{SESSION_ID}/llm-snapshots",
        params={"message_id": 999},
        headers={"X-Test-Role": "admin"},
    )
    assert res.status_code == 200
    assert res.json()["data"]["expired"] is True


def test_list_limit_bounds_are_validated(client: TestClient) -> None:
    res = client.get(
        f"/chat/sessions/{SESSION_ID}/llm-snapshots",
        params={"limit": 129},
        headers={"X-Test-Role": "admin"},
    )
    assert res.status_code == 422


def test_detail_rejects_non_admin(client: TestClient) -> None:
    res = client.get(
        "/chat/llm-snapshots/snap-1",
        headers={"X-Test-Role": "user"},
    )
    assert res.status_code == 403


def test_detail_returns_envelope_raw_and_view(client: TestClient) -> None:
    res = client.get(
        "/chat/llm-snapshots/snap-1",
        headers={"X-Test-Role": "admin"},
    )
    assert res.status_code == 200
    assert set(res.json()["data"]) == {"envelope", "raw", "view"}


def test_detail_missing_snapshot_returns_404(client: TestClient) -> None:
    res = client.get(
        "/chat/llm-snapshots/missing",
        headers={"X-Test-Role": "admin"},
    )
    assert res.status_code == 404


def test_developer_mode_get_is_chat_scope_and_reports_can_edit(
    client: TestClient,
) -> None:
    res = client.get("/chat/developer-mode")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["can_edit"] is False
    assert data["limits"] == LIMITS


def test_developer_mode_get_reports_admin_can_edit(client: TestClient) -> None:
    res = client.get("/chat/developer-mode", headers={"X-Test-Role": "admin"})
    assert res.status_code == 200
    assert res.json()["data"]["can_edit"] is True


def test_developer_mode_post_rejects_non_bool(client: TestClient) -> None:
    res = client.post("/chat/developer-mode", json={"enabled": "yes"})
    assert res.status_code == 422


def test_developer_mode_post_rejects_extra_fields(client: TestClient) -> None:
    res = client.post(
        "/chat/developer-mode",
        json={"enabled": True, "extra": 1},
    )
    assert res.status_code == 422


def test_developer_mode_post_accepts_valid_bool(client: TestClient) -> None:
    res = client.post("/chat/developer-mode", json={"enabled": True})
    assert res.status_code == 200
    assert res.json()["data"] == {"enabled": True}
