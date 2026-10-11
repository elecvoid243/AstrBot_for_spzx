"""Minimal v2 asset and persisted run HTTP boundary."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.workflows import (
    WorkflowSaveRequest,
    WorkflowService,
    WorkflowStartRequest,
)
from astrbot.dashboard.responses import error, ok

from .auth import require_dashboard_user

router = APIRouter(tags=["Agent Teams workflows"])


def get_workflow_service(request: Request) -> WorkflowService:
    """Resolve the core-owned service used by every entry point.

    Args:
        request: Current HTTP request.

    Returns:
        Shared workflow asset and run service.
    """
    return request.app.state.services.workflow_assets


@router.get("/workflow_assets")
async def list_assets(
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    return ok(await service.list_assets(username))


@router.get("/workflow_assets/{asset_id}")
async def get_asset(
    asset_id: str,
    version: int | None = Query(default=None, ge=1),
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    try:
        return ok(await service.get_asset(username, asset_id, version))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflow_assets")
@router.put("/workflow_assets/{asset_id}")
async def save_asset(
    payload: WorkflowSaveRequest,
    asset_id: str | None = None,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    try:
        return ok(await service.save_asset(username, payload.model_dump(), asset_id))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflow_assets/{asset_id}/runs")
async def start_run(
    asset_id: str,
    payload: WorkflowStartRequest,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    try:
        return ok(await service.start_run(username, asset_id, payload.model_dump()))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get("/workflow_runs")
async def list_runs(
    limit: int = Query(default=50, ge=1, le=100),
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    return ok(await service.list_runs(username, limit))


@router.get("/workflow_runs/{run_id}")
async def get_run(
    run_id: str,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    try:
        return ok(await service.get_run(username, run_id))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get("/workflow_runs/{run_id}/result")
async def get_result(
    run_id: str,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    try:
        return ok(await service.get_result(username, run_id))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )
