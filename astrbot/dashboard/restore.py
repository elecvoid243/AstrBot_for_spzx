"""Admission and complete response lifetimes during a full instance restore."""

import asyncio
from contextvars import ContextVar

from starlette.responses import JSONResponse

from astrbot.dashboard.responses import error

restore_response_done: ContextVar[asyncio.Event | None] = ContextVar(
    "restore_response_done", default=None
)


class FullRestoreMiddleware:
    """Keep admitted requests alive in the restore barrier through body completion."""

    def __init__(self, app, backups):
        self.app = app
        self.backups = backups

    async def __call__(self, scope, receive, send):
        """Admit recovery operations and track other complete ASGI lifetimes.

        Args:
            scope: HTTP or WebSocket connection scope.
            receive: Connection receive callable.
            send: Connection send callable.
        """
        path = scope.get("path", "")
        if scope["type"] not in {"http", "websocket"} or not path.startswith("/api"):
            await self.app(scope, receive, send)
            return
        backup = self.backups
        recovery = (
            path.startswith(("/api/v1/backups", "/api/backup/"))
            or path.startswith(("/api/v1/logs", "/api/log/"))
            or path
            in {
                "/api/v1/auth/login",
                "/api/auth/login",
                "/api/v1/auth/logout",
                "/api/auth/logout",
                "/api/v1/auth/setup-status",
                "/api/auth/setup-status",
                "/api/v1/auth/session",
                "/api/v1/system/restart",
                "/api/stat/restart-core",
                "/api/v1/stats/start-time",
                "/api/stat/start-time",
            }
        )
        phase = backup.restore_phase
        if phase == "pending" and getattr(
            backup, "restore_previous_phase", "ready"
        ) in {"restart_required", "failed"}:
            phase = backup.restore_previous_phase
        blocked = phase in {"stopping", "restoring", "restarting"} or (
            phase in {"restart_required", "failed"} and not recovery
        )
        if blocked:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1013})
            else:
                await JSONResponse(
                    error(
                        "Full restore maintenance: wait, then sign in as administrator to inspect the result. If data replacement failed or was interrupted, restarting only restarts maintenance; retry with a valid backup to complete restoration."
                    ),
                    status_code=503,
                    headers={"Retry-After": "2"},
                )(scope, receive, send)
            return
        task = asyncio.current_task()
        # Recovery requests cannot mutate Chat and must remain usable after failure.
        importing = scope.get("method") == "POST" and (
            path == "/api/backup/import"
            or path.endswith("/import")
            and path.startswith("/api/v1/backups/")
        )
        if not importing:
            backup.restore_requests.add(task)
        response_done = asyncio.Event()
        context_token = restore_response_done.set(response_done)
        try:
            await self.app(scope, receive, send)
        finally:
            response_done.set()
            restore_response_done.reset(context_token)
            backup.restore_requests.discard(task)
