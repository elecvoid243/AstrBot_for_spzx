"""Check media tickets, revocation and cleanup independently of input leases."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from astrbot.dashboard.access import require_account_operation
from astrbot.dashboard.api import plugins
from astrbot.dashboard.responses import ApiError
from astrbot.dashboard.schemas import BrowserControlTarget, BrowserMediaOffer


@pytest.fixture
def media_app(monkeypatch):
    """Create a real ASGI ticket/socket boundary with a bounded private fixture.

    Args:
        monkeypatch: Test-local dispatch replacement; no plugin/runtime is launched.

    Returns:
        App, dispatch calls and persisted-account resolver.
    """
    app = FastAPI()
    account = SimpleNamespace(
        account_id="account", token_version=3, resource_owner="owner", role="user"
    )
    resolver = AsyncMock(return_value=account)
    app.state.services = SimpleNamespace(
        auth=SimpleNamespace(accounts=SimpleNamespace(resolve=resolver))
    )
    app.state.jwt_secret = "fixture-secret-" * 3
    calls = []

    async def user(request: Request):
        """Bind the enabled account at the ordinary Dashboard dependency seam.

        Args:
            request: ASGI request.

        Returns:
            Stable resource owner.
        """
        request.state.dashboard_account = account
        require_account_operation(request, account)
        return account.resource_owner

    async def control(scope, owner, identity, command):
        """Accept inspection only; media must never acquire or release input.

        Args:
            scope: Admitted ASGI scope.
            owner: Persisted account.
            identity: Signed browser/chat identity.
            command: Host command.

        Returns:
            Current protected browser capability.
        """
        assert command == {"operation": "inspect"}
        assert owner is account
        calls.append(("inspect", dict(identity)))
        return {
            **identity,
            "available": True,
            "video_stream_version": 1,
            "coordinate_contract_version": 2,
            "frame_provenance_version": 1,
        }

    async def dispatch(scope, owner, identity, command):
        """Provide native answer/heartbeat fixtures and record exact cleanup.

        Args:
            scope: Current socket scope.
            owner: Enabled account.
            identity: Host-signed browser/viewer.
            command: Host signaling operation.

        Returns:
            Answer, metadata stream or close acknowledgement.
        """
        calls.append((command["operation"], dict(identity)))
        if command["operation"] == "offer":
            return {
                "type": "answer",
                "sdp": "fixture-answer",
                "media_session_id": "a" * 32,
            }
        if command["operation"] == "events":

            async def events():
                """Yield bounded heartbeat data until socket cancellation.

                Yields:
                    Private heartbeat blocks.
                """
                while True:
                    await asyncio.sleep(0.01)
                    yield b": hb\n\n"

            return StreamingResponse(events())
        return {"closed": True}

    app.dependency_overrides[plugins.require_dashboard_user] = user
    monkeypatch.setattr(plugins, "_dispatch_browser_control", control)
    monkeypatch.setattr(plugins, "_dispatch_browser_media", dispatch)
    app.include_router(plugins.router, prefix="/api/v1")
    return app, calls, resolver


def test_media_ticket_is_one_use_and_never_changes_input_owner(media_app):
    """Keep media negotiation/cleanup independent of the manual channel.

    Args:
        media_app: Isolated ASGI fixture.
    """
    app, calls, _ = media_app
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/plugins/browser-media/session",
            json={
                "chat_id": "chat",
                "generation": "generation",
                "browser_session_id": "browser",
            },
        )
        assert response.status_code == 200
        issued = response.json()
        identity = jwt.decode(
            issued["ticket"], app.state.jwt_secret, algorithms=["HS256"]
        )
        assert identity["token_type"] == "browser-media-v1"
        assert identity["sub"] == "account" and identity["ver"] == 3
        assert 0 < identity["exp"] - time.time() <= 15
        with client.websocket_connect(
            issued["websocket_path"], headers={"origin": "http://testserver"}
        ) as socket:
            socket.send_json(
                {"ticket": issued["ticket"], "type": "offer", "sdp": "fixture-offer"}
            )
            assert socket.receive_json()["type"] == "answer"
            assert socket.receive_json()["type"] == "heartbeat"
        assert [name for name, _ in calls].count("close") == 1
        assert {name for name, _ in calls} == {"inspect", "offer", "events", "close"}
        with client.websocket_connect(issued["websocket_path"]) as replay:
            replay.send_json(
                {"ticket": issued["ticket"], "type": "offer", "sdp": "fixture-offer"}
            )
            assert replay.receive_json()["type"] == "revoked"
        assert [name for name, _ in calls].count("offer") == 1


@pytest.mark.parametrize(
    "patch", [{"exp": 1}, {"token_type": "browser-control-v1"}, {"sub": "unknown"}]
)
def test_invalid_media_ticket_never_allocates_peer(media_app, patch):
    """Reject expired/control/disabled-account tickets before private signaling.

    Args:
        media_app: Isolated ASGI fixture.
        patch: Invalid persisted identity or token claims.
    """
    app, calls, resolver = media_app
    claims = {
        "client_id": "b" * 32,
        "token_type": "browser-media-v1",
        "exp": time.time() + 15,
        **patch,
    }
    app.state.browser_media_tickets = {claims["client_id"]: time.time() + 15}
    if patch.get("sub") == "unknown":
        resolver.return_value = None
    ticket = jwt.encode(claims, app.state.jwt_secret, algorithm="HS256")
    with (
        TestClient(app) as client,
        client.websocket_connect("/api/v1/plugins/browser-media/ws") as socket,
    ):
        socket.send_json({"ticket": ticket, "type": "offer", "sdp": "fixture-offer"})
        assert socket.receive_json()["type"] == "revoked"
    assert not calls


def test_media_revocation_rechecks_account_and_closes_private_viewer(media_app):
    """Clear admission on account revocation while a static page has no frames.

    Args:
        media_app: Isolated ASGI fixture.
    """
    app, calls, resolver = media_app
    with TestClient(app) as client:
        issued = client.post(
            "/api/v1/plugins/browser-media/session",
            json={
                "chat_id": "chat",
                "generation": "generation",
                "browser_session_id": "browser",
            },
        ).json()
        with client.websocket_connect(issued["websocket_path"]) as socket:
            socket.send_json(
                {"ticket": issued["ticket"], "type": "offer", "sdp": "fixture-offer"}
            )
            assert socket.receive_json()["type"] == "answer"
            assert socket.receive_json()["type"] == "heartbeat"
            resolver.return_value = None
            socket.send_json({"operation": "heartbeat"})
            while socket.receive_json()["type"] != "revoked":
                pass
        assert [name for name, _ in calls].count("close") == 1


def test_media_offer_and_target_do_not_accept_arbitrary_fields():
    """Do not let signaling select CDP URLs or omit enabled pixel provenance."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BrowserMediaOffer(
            ticket="ticket", type="offer", sdp="offer", browser_url="http://other"
        )
    with pytest.raises(ValidationError):
        BrowserControlTarget(
            tab_id="t",
            document_id="d",
            viewport_width=1280,
            viewport_height=720,
            scroll_x=0,
            scroll_y=0,
            position_revision=0,
            coordinate_token="forged",
        )


@pytest.mark.asyncio
async def test_public_media_extension_cannot_forge_host_context():
    """Reject even an admin's direct media extension request without host state."""
    from starlette.requests import Request

    account = SimpleNamespace(role="admin")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "headers": [],
            "state": {"dashboard_account": account},
        }
    )
    with pytest.raises(ApiError, match="authenticated browser media"):
        await plugins._authorize_chat_extension(
            "browser-use/media", request, "owner", lambda: None
        )
