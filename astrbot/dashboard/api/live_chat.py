from __future__ import annotations

import asyncio

import jwt
from fastapi import APIRouter, WebSocket

from astrbot.core.db.po import DashboardAccount
from astrbot.core.utils.webchat_access import (
    DashboardPrincipal,
    dashboard_account_context,
    webchat_account_role,
    webchat_resource_owner,
)
from astrbot.dashboard.services.live_chat_service import LiveChatService

router = APIRouter(tags=["Live Chat"])
legacy_router = APIRouter(
    prefix="/api",
    tags=["Dashboard Live Chat"],
    include_in_schema=False,
)


def get_service(websocket: WebSocket) -> LiveChatService:
    return websocket.app.state.services.live_chat


async def _run_live_chat_ws(
    websocket: WebSocket,
    *,
    force_ct: str | None,
) -> None:
    await websocket.accept()
    service = get_service(websocket)
    token = websocket.query_params.get("token")
    try:
        claims = jwt.decode(
            token or "", websocket.app.state.jwt_secret, algorithms=["HS256"]
        )
        account = await websocket.app.state.services.auth.accounts.resolve(claims)
        if account is None or claims.get("token_type") is not None:
            await websocket.close(
                code=1008, reason="WebSocket chat is not available for this account"
            )
            return
    except jwt.InvalidTokenError:
        await websocket.close(code=1008, reason="Invalid token")
        return
    principal = DashboardPrincipal(
        account.account_id, account.resource_owner, account.role, account.username
    )
    requests = None
    task = asyncio.current_task()
    if account.role == "user":
        state = websocket.app.state
        async with state.db.dashboard_account_lock:
            async with state.db.get_db() as session:
                current = await session.get(DashboardAccount, account.account_id)
            if (
                current is None
                or not current.enabled
                or current.token_version != account.token_version
                or account.account_id in state.deleting_accounts
            ):
                await websocket.close(code=1008, reason="Account is unavailable")
                return
            requests = state.account_requests.setdefault(account.account_id, set())
            requests.add(task)
    account_token = dashboard_account_context.set(principal)
    owner_token = webchat_resource_owner.set(principal.resource_owner)
    role_token = webchat_account_role.set(principal.role)
    try:
        await service.run_websocket_session(
            token=websocket.query_params.get("token"),
            force_ct=force_ct,
            receive_json=websocket.receive_json,
            send_json=websocket.send_json,
            close=websocket.close,
        )
    except asyncio.CancelledError:
        if account.account_id not in websocket.app.state.deleting_accounts:
            raise
        await websocket.close(code=1008, reason="Account deleted")
    finally:
        if requests is not None:
            requests.discard(task)
            if not requests:
                websocket.app.state.account_requests.pop(account.account_id, None)
        webchat_account_role.reset(role_token)
        webchat_resource_owner.reset(owner_token)
        dashboard_account_context.reset(account_token)


@router.websocket("/live-chat/ws")
async def live_chat_ws(websocket: WebSocket) -> None:
    await _run_live_chat_ws(websocket, force_ct="live")


@router.websocket("/unified-chat/ws")
async def unified_chat_ws(websocket: WebSocket) -> None:
    await _run_live_chat_ws(websocket, force_ct=None)


@legacy_router.websocket("/live_chat/ws")
async def dashboard_live_chat_ws(websocket: WebSocket) -> None:
    await _run_live_chat_ws(websocket, force_ct="live")


@legacy_router.websocket("/unified_chat/ws")
async def dashboard_unified_chat_ws(websocket: WebSocket) -> None:
    await _run_live_chat_ws(websocket, force_ct=None)
