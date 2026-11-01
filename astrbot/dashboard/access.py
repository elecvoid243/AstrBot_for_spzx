"""Explicit admission for account-backed Dashboard requests.

New Chat operations stay closed to ordinary accounts until their complete
resource ownership contract has been implemented. API keys keep their scopes.
"""

from fastapi import Request
from starlette.routing import Match

from astrbot.dashboard.responses import ApiError

# Endpoint names, rather than URL prefixes or the broad API-key chat scope.
USER_OPERATIONS = {
    "login",
    "dashboard_login",
    "logout",
    "dashboard_logout",
    "setup_status",
    "dashboard_setup_status",
    "current_account",
    "create_chat_session",
    "dashboard_new_session",
    "chat_sessions",
    "dashboard_get_sessions",
    "get_chat_session",
    "get_chat_session_history",
    "dashboard_get_session",
    "chat",
    "dashboard_send_chat",
}


def require_account_operation(request: Request, account) -> None:
    """Admit only audited operations for an ordinary account.

    Args:
        request: Current HTTP request, before or after route resolution.
        account: Trusted enabled account resolved from a signed credential.

    Raises:
        ApiError: The operation has not been adapted for ordinary accounts.
    """
    route = request.scope.get("route")
    if route is None:
        route = next(
            (
                r
                for r in request.app.routes
                if r.matches(request.scope)[0] == Match.FULL
            ),
            None,
        )
    operation = getattr(route, "name", None)
    request.state.dashboard_operation = operation
    if account.role != "admin" and operation not in USER_OPERATIONS:
        raise ApiError(
            "This operation is not available for this account", status_code=403
        )


async def validate_account_request(request: Request, account) -> None:
    """Validate the fields of the admitted basic-chat operations.

    Args:
        request: Current HTTP request, after operation admission.
        account: Trusted account resolved from the signed session.

    Raises:
        ApiError: The caller supplies internal fields or an unavailable resource.
    """
    if request.method == "POST" and getattr(
        request.state, "dashboard_operation", None
    ) in {"chat", "dashboard_send_chat"}:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ApiError("Invalid chat payload", status_code=400)
        for key, value in payload.items():
            if key.startswith("_") and key != "_client_message_id":
                # The chat service validates edited continuations against the
                # latest persisted user message in the administrator's session.
                if account.role == "admin" and key in {
                    "_skip_user_history",
                    "_llm_checkpoint_id",
                }:
                    continue
                if (key == "_skip_user_history" and value is False) or (
                    key == "_llm_checkpoint_id" and value is None
                ):
                    continue
                raise ApiError(
                    "Internal chat fields are not accepted",
                    status_code=400,
                )
    if account.role != "admin":
        if any(
            k
            not in {
                "session_id",
                "conversation_id",
                "limit",
                "before_id",
                "platform_id",
                "page",
                "page_size",
            }
            for k in request.query_params
        ):
            raise ApiError("Unsupported chat query", status_code=400)
        if request.query_params.get("platform_id", "webchat") != "webchat":
            raise ApiError("Unsupported chat platform", status_code=403)
        if request.method == "POST":
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) - {
                "session_id",
                "conversation_id",
                "message",
                "message_id",
            }:
                raise ApiError("Unsupported chat fields", status_code=400)
            message = payload.get("message")
            if not isinstance(message, str) or not message.strip():
                raise ApiError("Only text messages are available", status_code=400)
            session_id = payload.get("session_id", payload.get("conversation_id"))
            if not isinstance(session_id, str) or not session_id:
                raise ApiError("Invalid session ID", status_code=400)
            session = await request.app.state.db.get_platform_session_by_id(session_id)
            if (
                session is None
                or session.creator != account.resource_owner
                or session.platform_id != "webchat"
            ):
                raise ApiError("Resource is unavailable", status_code=403)
