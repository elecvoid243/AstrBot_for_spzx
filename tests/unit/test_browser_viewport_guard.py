"""Verify viewport updates retain the owned-chat extension boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from starlette.requests import Request

from astrbot.dashboard.api.plugins import _authorize_chat_extension
from astrbot.dashboard.responses import ApiError
from astrbot.dashboard.services.chat_service import ChatServiceError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["allowed", "foreign_chat", "forged_field", "wrong_method", "disabled"]
)
async def test_viewport_keeps_chat_authorization(case):
    """Reject unowned or disabled resize requests before invoking the plugin.

    Args:
        case: Authorization boundary exercised by this request.
    """
    owned = AsyncMock(
        return_value=SimpleNamespace(
            is_group=False, platform_id="webchat", creator="user", session_id="chat"
        )
    )
    if case == "foreign_chat":
        owned.side_effect = ChatServiceError("Permission denied")
    app = SimpleNamespace(
        state=SimpleNamespace(
            services=SimpleNamespace(
                chat=SimpleNamespace(require_owned_chat_session=owned)
            ),
            core_lifecycle=SimpleNamespace(
                star_context=SimpleNamespace(
                    get_config=lambda **kwargs: {
                        "plugin_set": [] if case == "disabled" else ["*"]
                    }
                )
            ),
        )
    )
    query = {
        "chat_id": "chat",
        "generation": "generation",
        "browser_session_id": "browser",
        "tab_id": "tab",
        "width": "900",
        "height": "600",
        "device_scale_factor": "2",
    }
    if case == "forged_field":
        query["owner"] = "another-user"
    request = Request(
        {
            "type": "http",
            "method": "GET" if case == "wrong_method" else "POST",
            "path": "/api/v1/plugins/extensions/browser-use/viewport",
            "headers": [],
            "query_string": urlencode(query).encode(),
            "app": app,
            "state": {"dashboard_account": SimpleNamespace(role="user")},
        }
    )
    request._body = b""
    handler = SimpleNamespace(__astrbot_chat_guard__="browser-owned-chat-v1")
    if case == "allowed":
        await _authorize_chat_extension(
            "browser-use/viewport", request, "user", handler
        )
        owned.assert_awaited_once_with("user", "chat")
        assert request.state.chat_origin.endswith("!user!chat")
    else:
        with pytest.raises(ApiError):
            await _authorize_chat_extension(
                "browser-use/viewport", request, "user", handler
            )
