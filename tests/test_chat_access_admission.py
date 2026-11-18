"""Admission of the unified ChatUI transport for ordinary accounts."""

from functools import lru_cache

import pytest
from starlette.requests import Request

from astrbot.dashboard.access import require_account_operation
from astrbot.dashboard.api.router import build_api_router
from astrbot.dashboard.responses import ApiError


class _RouterApp:
    """Minimal ASGI app exposing the production route table."""

    def __init__(self) -> None:
        self.routes = _routes()


@lru_cache(maxsize=1)
def _routes():
    """Build the production route table once per test session."""
    return tuple(build_api_router().routes)


class _Account:
    """Ordinary account resolved from a signed Dashboard session."""

    role = "user"


def _request(method: str, path: str) -> Request:
    """Build a request that the admission middleware can route.

    Args:
        method: HTTP method of the call.
        path: Concrete request path, including any path parameters.

    Returns:
        Request bound to the production route table.
    """
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [],
            "client": ("test", 1),
            "server": ("test", 80),
            "app": _RouterApp(),
        }
    )


@pytest.mark.parametrize(
    ("method", "path", "operation"),
    [
        ("POST", "/api/v1/chat/send", "unified_send_chat"),
        ("POST", "/api/v1/chat/stream", "unified_chat_stream"),
        ("GET", "/api/v1/chat/active-runs", "active_chat_runs"),
        ("GET", "/api/v1/chat/runs/run-1/snapshot", "unified_run_snapshot"),
        (
            "GET",
            "/api/v1/chat/runs/run-1/tool-results/call-1",
            "unified_run_tool_result",
        ),
        (
            "GET",
            "/api/v1/chat/messages/1/tool-results/call-1",
            "unified_message_tool_result",
        ),
    ],
)
def test_unified_chat_operations_are_admitted_for_ordinary_accounts(
    method: str, path: str, operation: str
) -> None:
    """The ChatUI transport must pass admission for a non-admin account."""
    request = _request(method, path)
    require_account_operation(request, _Account())
    assert request.state.dashboard_operation == operation


def test_ordinary_accounts_still_cannot_reach_administrator_operations() -> None:
    """Admission stays closed for operations outside the audited surface."""
    request = _request("GET", "/api/v1/accounts")
    with pytest.raises(ApiError) as excinfo:
        require_account_operation(request, _Account())
    assert excinfo.value.status_code == 403
    assert "not available for this account" in excinfo.value.message
