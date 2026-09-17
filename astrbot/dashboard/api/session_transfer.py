"""ChatUI session export/import endpoints.

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from astrbot.dashboard.async_utils import run_maybe_async
from astrbot.dashboard.responses import ApiError, ok
from astrbot.dashboard.schemas import ChatSessionImportConfirmRequest
from astrbot.dashboard.services.session_transfer_service import (
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


def _zip_response(export) -> StreamingResponse:
    """Stream a packaged session export as a zip download.

    Args:
        export: SessionExport produced by the service.

    Returns:
        A streaming response carrying the zip bytes. ``FileResponse`` cannot
        be used here because the archive is built in memory (the same reason
        ``_export_response`` in conversations.py streams a BytesIO).
    """
    export.file_obj.seek(0)

    def iter_file():
        while chunk := export.file_obj.read(8192):
            yield chunk

    return StreamingResponse(
        iter_file(),
        media_type=export.mimetype,
        headers={"Content-Disposition": f'attachment; filename="{export.filename}"'},
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
