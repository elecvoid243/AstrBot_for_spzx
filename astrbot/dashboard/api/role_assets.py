"""Authenticated dashboard access to the shared role asset service."""

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.dashboard.responses import error, ok

from .auth import require_dashboard_user

router = APIRouter(tags=["Agent Teams roles"])


def get_role_service(request: Request) -> RoleAssetService:
    """Resolve the shared asset service from the application container.

    Args:
        request: Current HTTP request.

    Returns:
        The core role asset service.
    """
    return request.app.state.services.role_assets


@router.get("/agent_roles/resources")
async def role_resources(
    username: str = Depends(require_dashboard_user),
    service: RoleAssetService = Depends(get_role_service),
):
    return ok(await service.get_resources())


@router.get("/agent_roles")
async def list_roles(
    username: str = Depends(require_dashboard_user),
    service: RoleAssetService = Depends(get_role_service),
):
    try:
        return ok(await service.list_roles(username))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/agent_roles/preview")
async def preview_role(
    payload: dict = Body(...),
    username: str = Depends(require_dashboard_user),
    service: RoleAssetService = Depends(get_role_service),
):
    try:
        return ok(await service.preview_role(payload))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/agent_roles")
@router.put("/agent_roles/{role_id}")
async def save_role(
    payload: dict = Body(...),
    role_id: str | None = None,
    username: str = Depends(require_dashboard_user),
    service: RoleAssetService = Depends(get_role_service),
):
    try:
        return ok(await service.save_role(username, payload, role_id))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get("/agent_roles/{role_id}")
async def get_role(
    role_id: str,
    version: int | None = Query(default=None, ge=1),
    username: str = Depends(require_dashboard_user),
    service: RoleAssetService = Depends(get_role_service),
):
    try:
        return ok(await service.get_role(username, role_id, version))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/agent_roles/{role_id}/resolve")
async def resolve_role(
    role_id: str,
    version: int = Query(ge=1),
    overrides: dict = Body(default={}),
    username: str = Depends(require_dashboard_user),
    service: RoleAssetService = Depends(get_role_service),
):
    try:
        return ok(await service.resolve_role(username, role_id, version, overrides))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )
