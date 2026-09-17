"""ChatUI session export/import routes: wiring and error mapping.

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

import inspect
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.params import Depends

from astrbot.dashboard.api.auth import AuthContext, ScopeDependency
from astrbot.dashboard.api.session_transfer import (
    confirm_import_chat_sessions,
    export_chat_session,
    import_chat_sessions,
)
from astrbot.dashboard.responses import ApiError
from astrbot.dashboard.services.session_transfer_service import (
    SessionExport,
    SessionTransferError,
)


def _request(service):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(services=SimpleNamespace(session_transfer=service))
        )
    )


def _auth(username="alice"):
    return AuthContext(username=username, scopes=["chat"], via="jwt")


def _declared_scopes(endpoint):
    """Collect the scope markers FastAPI sees on an endpoint's parameters.

    FastAPI injects ``ScopeDependency`` through ``Depends(...)``, so the
    marker has to be unwrapped from the parameter default.

    Args:
        endpoint: Route handler function.

    Returns:
        The ``scope`` of every ``ScopeDependency`` the handler depends on.
    """
    scopes = []
    for parameter in inspect.signature(endpoint).parameters.values():
        dependency = parameter.default
        if isinstance(dependency, Depends):
            dependency = dependency.dependency
        if isinstance(dependency, ScopeDependency):
            scopes.append(dependency.scope)
    return scopes


@pytest.mark.parametrize(
    "endpoint",
    [export_chat_session, import_chat_sessions, confirm_import_chat_sessions],
)
def test_routes_declare_chat_scope_dependency(endpoint):
    """build_api_router derives x-astrbot-scope from the ScopeDependency marker."""
    assert _declared_scopes(endpoint) == ["chat"]


@pytest.mark.asyncio
async def test_export_route_maps_service_error_to_api_error():
    service = Mock()
    service.export_session = AsyncMock(
        side_effect=SessionTransferError("Permission denied")
    )

    with pytest.raises(ApiError, match="Permission denied"):
        await export_chat_session(
            session_id="sess-1",
            request=_request(service),
            _auth=_auth(),
            service=service,
        )


@pytest.mark.asyncio
async def test_export_route_streams_zip():
    service = Mock()
    service.export_session = AsyncMock(
        return_value=SessionExport(file_obj=BytesIO(b"zip"), filename="pkg.zip")
    )

    response = await export_chat_session(
        session_id="sess-1",
        request=_request(service),
        _auth=_auth(),
        service=service,
    )

    assert response.media_type == "application/zip"
    assert "pkg.zip" in response.headers["content-disposition"]
    body = b"".join([chunk async for chunk in response.body_iterator])
    assert body == b"zip"
    service.export_session.assert_awaited_once_with("alice", "sess-1")


@pytest.mark.asyncio
async def test_import_route_surfaces_missing_upload(monkeypatch):
    service = Mock()
    service.stage_import = AsyncMock(
        side_effect=SessionTransferError("Missing key: file")
    )

    async def _no_upload(request, *, field_name="file"):
        return None

    import astrbot.dashboard.api.session_transfer as st_api

    monkeypatch.setattr(st_api, "single_upload", _no_upload)

    with pytest.raises(ApiError, match="file"):
        await import_chat_sessions(
            request=_request(service),
            _auth=_auth("bob"),
            service=service,
        )


@pytest.mark.asyncio
async def test_confirm_route_passes_username_and_id():
    service = Mock()
    service.confirm_import = AsyncMock(return_value={"created": []})

    result = await confirm_import_chat_sessions(
        payload=SimpleNamespace(import_id="import-1"),
        request=_request(service),
        _auth=_auth("bob"),
        service=service,
    )

    service.confirm_import.assert_awaited_once_with("bob", "import-1")
    assert result["status"] == "ok"
