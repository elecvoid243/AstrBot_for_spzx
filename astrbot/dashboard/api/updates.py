from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from astrbot.core import logger
from astrbot.core.desktop_runtime import DESKTOP_MANAGED_RESTART_MESSAGE
from astrbot.dashboard.async_utils import run_maybe_async
from astrbot.dashboard.schemas import (
    PipInstallRequest,
    UpdateRequest,
    UpdateSourcesRequest,
)
from astrbot.dashboard.services.update_service import (
    UpdateService,
    UpdateServiceError,
    UpdateServiceResult,
)

from .auth import AuthContext, require_dashboard_user, require_scope

router = APIRouter(tags=["Updates"])
legacy_router = APIRouter(
    prefix="/api/update",
    tags=["Dashboard Updates"],
    include_in_schema=False,
)
# Dedicated legacy router for the announcement endpoint.
# The frontend hard-codes ``/api/system/announcement`` (see
# ``dashboard/src/composables/useAnnouncement.ts``), which does NOT match the
# ``/api/update`` prefix on ``legacy_router``. A separate router with its own
# prefix avoids the path collision and keeps the existing update routes intact.
system_announcement_legacy_router = APIRouter(
    prefix="/api/system",
    tags=["Dashboard System"],
    include_in_schema=False,
)
# Legacy router for the user feedback proxy endpoint. Same prefix as the
# announcement router (no path collision); the frontend hard-codes
# ``/api/system/feedback``.
feedback_legacy_router = APIRouter(
    prefix="/api/system",
    tags=["Dashboard System"],
    include_in_schema=False,
)

# Hard caps enforced before proxying, so a runaway upload is rejected without
# buffering it. The update server applies its own (smaller) limits as well.
FEEDBACK_MAX_FILE_BYTES = 50 * 1024 * 1024
FEEDBACK_MAX_TOTAL_BYTES = 100 * 1024 * 1024


def get_service(request: Request) -> UpdateService:
    return request.app.state.services.updates


async def require_system_scope(request: Request) -> AuthContext:
    return await require_scope(request, "system")


def _model_dict(payload) -> dict:
    return payload.model_dump(exclude_none=True)


def _result_payload(result: UpdateServiceResult) -> dict:
    if result.status == "success":
        return {
            "status": "success",
            "message": result.message,
            "data": result.data,
        }
    return {
        "status": "ok",
        "message": result.message,
        "data": {} if result.data is None else result.data,
    }


def _service_response(result: UpdateServiceResult) -> JSONResponse:
    return JSONResponse(
        _result_payload(result),
        status_code=200,
        headers=result.headers or None,
    )


def _service_error(exc: UpdateServiceError) -> JSONResponse:
    # User input problems (e.g. an invalid update source URL) must reach the
    # frontend verbatim, and are not server faults worth a stack trace.
    if exc.code == "invalid_update_source":
        return JSONResponse(
            {"status": "error", "message": str(exc), "data": None},
            status_code=200,
        )
    logger.error(f"Dashboard update operation failed: {exc}", exc_info=True)
    if exc.code == "desktop_managed":
        return JSONResponse(
            {
                "status": "error",
                "message": DESKTOP_MANAGED_RESTART_MESSAGE,
                "data": None,
            },
            status_code=200,
        )
    return JSONResponse(
        {"status": "error", "message": "An internal error has occurred.", "data": None},
        status_code=200,
    )


async def _run(operation) -> JSONResponse:
    try:
        result = await run_maybe_async(operation)
        return _service_response(result)
    except UpdateServiceError as exc:
        return _service_error(exc)


@router.get("/updates/check")
async def check_updates(
    update_type: str | None = Query(default=None, alias="type"),
    _auth: AuthContext = Depends(require_system_scope),
    service: UpdateService = Depends(get_service),
):
    return await _run(lambda: service.check_update(update_type))


@legacy_router.get("/check")
async def check_dashboard_updates(
    update_type: str | None = Query(default=None, alias="type"),
    _username: str = Depends(require_dashboard_user),
    service: UpdateService = Depends(get_service),
):
    return await _run(lambda: service.check_update(update_type))


@router.get("/updates/releases")
async def update_releases(
    _auth: AuthContext = Depends(require_system_scope),
    service: UpdateService = Depends(get_service),
):
    return await _run(service.get_releases)


@legacy_router.get("/releases")
async def dashboard_update_releases(
    _username: str = Depends(require_dashboard_user),
    service: UpdateService = Depends(get_service),
):
    return await _run(service.get_releases)


@router.get("/updates/progress/{task_id}")
async def update_progress(
    task_id: str,
    _auth: AuthContext = Depends(require_system_scope),
    service: UpdateService = Depends(get_service),
):
    return await _run(lambda: service.get_update_progress(task_id))


@legacy_router.get("/progress")
async def dashboard_update_progress(
    progress_id: str | None = Query(default=None, alias="id"),
    _username: str = Depends(require_dashboard_user),
    service: UpdateService = Depends(get_service),
):
    return await _run(lambda: service.get_update_progress(progress_id or ""))


@router.post("/updates/core")
async def update_core(
    payload: UpdateRequest,
    _auth: AuthContext = Depends(require_system_scope),
    service: UpdateService = Depends(get_service),
):
    return await _run(lambda: service.update_project(_model_dict(payload)))


@legacy_router.post("/do")
async def update_dashboard_core(
    payload: UpdateRequest,
    _username: str = Depends(require_dashboard_user),
    service: UpdateService = Depends(get_service),
):
    return await _run(lambda: service.update_project(_model_dict(payload)))


@router.post("/updates/dashboard")
async def update_dashboard(
    _auth: AuthContext = Depends(require_system_scope),
    service: UpdateService = Depends(get_service),
):
    return await _run(service.update_dashboard)


@legacy_router.post("/dashboard")
async def update_dashboard_assets(
    _username: str = Depends(require_dashboard_user),
    service: UpdateService = Depends(get_service),
):
    return await _run(service.update_dashboard)


@router.get("/updates/sources")
async def get_update_sources(
    _auth: AuthContext = Depends(require_system_scope),
    service: UpdateService = Depends(get_service),
):
    return await _run(service.get_update_sources)


@router.put("/updates/sources")
async def save_update_sources(
    payload: UpdateSourcesRequest,
    _auth: AuthContext = Depends(require_system_scope),
    service: UpdateService = Depends(get_service),
):
    return await _run(lambda: service.save_update_sources(_model_dict(payload)))


@router.post("/pip/install")
async def install_pip_package(
    payload: PipInstallRequest,
    _auth: AuthContext = Depends(require_system_scope),
    service: UpdateService = Depends(get_service),
):
    return await _run(lambda: service.install_pip_package(_model_dict(payload)))


@legacy_router.post("/pip-install")
async def install_dashboard_pip_package(
    payload: PipInstallRequest,
    _username: str = Depends(require_dashboard_user),
    service: UpdateService = Depends(get_service),
):
    return await _run(lambda: service.install_pip_package(_model_dict(payload)))


def _announcement_response(result: UpdateServiceResult) -> JSONResponse:
    """Serialize an announcement result, preserving 404 from upstream.

    The default ``_run`` wrapper collapses every UpdateServiceError to HTTP 200,
    which would lose the 404 ("no announcement") signal the frontend relies on
    to hide the bar. This helper keeps the v1 + legacy endpoints thin while
    letting us pass through the upstream status code.
    """
    return JSONResponse(
        {
            "status": result.status,
            "message": result.message,
            "data": result.data,
        },
        status_code=200,
    )


def _announcement_error(exc: UpdateServiceError) -> JSONResponse:
    """Map announcement errors to HTTP status codes the frontend can act on."""
    message = str(exc)
    # 404 from upstream → 404; everything else (5xx / network / parse) → 502.
    status_code = 404 if "没有公告" in message else 502
    return JSONResponse(
        {"status": "error", "message": message, "data": None},
        status_code=status_code,
    )


async def _get_announcement(service: UpdateService) -> JSONResponse:
    try:
        result = await service.get_announcement()
    except UpdateServiceError as exc:
        return _announcement_error(exc)
    return _announcement_response(result)


@router.get("/system/announcement")
async def get_system_announcement(
    _auth: AuthContext = Depends(require_system_scope),
    service: UpdateService = Depends(get_service),
):
    return await _get_announcement(service)


@system_announcement_legacy_router.get("/announcement")
async def dashboard_system_announcement(
    _username: str = Depends(require_dashboard_user),
    service: UpdateService = Depends(get_service),
):
    return await _get_announcement(service)


def _feedback_error(exc: UpdateServiceError) -> JSONResponse:
    """Map feedback proxy errors to 502 (unreachable upstream / bad payload)."""
    return JSONResponse(
        {"status": "error", "message": str(exc), "data": None},
        status_code=502,
    )


@feedback_legacy_router.post("/feedback")
async def submit_user_feedback(
    content: str = Form(...),
    astrbot_version: str = Form(""),
    dashboard_version: str = Form(""),
    contact: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    _username: str = Depends(require_dashboard_user),
    service: UpdateService = Depends(get_service),
):
    """Proxy dashboard user feedback to the configured update server."""
    prepared: list[tuple[str, bytes]] = []
    total = 0
    for f in files:
        data = await f.read(FEEDBACK_MAX_FILE_BYTES + 1)
        if len(data) > FEEDBACK_MAX_FILE_BYTES:
            return JSONResponse(
                {
                    "status": "error",
                    "message": f"单个附件不能超过 {FEEDBACK_MAX_FILE_BYTES // (1024 * 1024)} MB。",
                    "data": None,
                },
                status_code=413,
            )
        total += len(data)
        if total > FEEDBACK_MAX_TOTAL_BYTES:
            return JSONResponse(
                {
                    "status": "error",
                    "message": f"附件总大小不能超过 {FEEDBACK_MAX_TOTAL_BYTES // (1024 * 1024)} MB。",
                    "data": None,
                },
                status_code=413,
            )
        prepared.append((f.filename or "unnamed", data))

    try:
        status, payload = await service.submit_feedback(
            content=content,
            astrbot_version=astrbot_version,
            dashboard_version=dashboard_version,
            contact=contact,
            attachments=prepared,
        )
    except UpdateServiceError as exc:
        return _feedback_error(exc)
    return JSONResponse(payload, status_code=status)
