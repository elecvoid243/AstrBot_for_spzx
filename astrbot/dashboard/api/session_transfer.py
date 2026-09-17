"""ChatUI session export/import endpoints.

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from astrbot import logger
from astrbot.dashboard.async_utils import run_maybe_async
from astrbot.dashboard.responses import ApiError, ok
from astrbot.dashboard.schemas import ChatSessionImportConfirmRequest
from astrbot.dashboard.services.session_transfer_service import (
    SessionExport,
    SessionTransferError,
    SessionTransferService,
)

from .auth import AuthContext, ScopeDependency
from .multipart import single_upload

router = APIRouter(tags=["Chat"])

require_chat_scope = ScopeDependency("chat")


def get_service(request: Request) -> SessionTransferService:
    return request.app.state.services.session_transfer


async def _run(operation):
    try:
        return ok(await run_maybe_async(operation))
    except SessionTransferError as exc:
        raise ApiError(str(exc)) from exc


def _delete_export_file(path) -> None:
    """Remove a delivered export archive from the service temp directory.

    Args:
        path: Archive path handed to ``FileResponse``.
    """
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(f"Failed to delete export archive {path}: {exc!s}")


def _zip_response(export: SessionExport) -> FileResponse:
    """Serve a packaged export from disk and delete it after delivery.

    Args:
        export: SessionExport produced by the service.

    Returns:
        A file response streaming the archive path; a background task removes
        the temporary archive once the response has been sent.
    """
    return FileResponse(
        export.path,
        media_type=export.mimetype,
        filename=export.filename,
        background=BackgroundTask(_delete_export_file, export.path),
    )


@router.get("/chat/sessions/{session_id}/export")
async def export_chat_session(
    session_id: str,
    request: Request,
    _auth: AuthContext = Depends(require_chat_scope),
    service: SessionTransferService = Depends(get_service),
):
    try:
        export = await service.export_session(_auth.username, session_id)
    except SessionTransferError as exc:
        raise ApiError(str(exc)) from exc
    return _zip_response(export)


@router.post("/chat/sessions/import")
async def import_chat_sessions(
    request: Request,
    _auth: AuthContext = Depends(require_chat_scope),
    service: SessionTransferService = Depends(get_service),
):
    upload = await single_upload(request, field_name="file")
    return await _run(lambda: service.stage_import(upload))


@router.post("/chat/sessions/import/confirm")
async def confirm_import_chat_sessions(
    payload: ChatSessionImportConfirmRequest,
    request: Request,
    _auth: AuthContext = Depends(require_chat_scope),
    service: SessionTransferService = Depends(get_service),
):
    return await _run(lambda: service.confirm_import(_auth.username, payload.import_id))
