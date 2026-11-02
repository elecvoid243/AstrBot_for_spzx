"""Authenticated dashboard access to the shared role asset service."""

from typing import Literal

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

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


class DraftCreateRequest(BaseModel):
    """Initial editable snapshot and optional existing asset identity."""

    model_config = ConfigDict(extra="forbid")
    body: dict
    asset_id: str | None = None


class DraftUpdateRequest(BaseModel):
    """Snapshot update guarded by an expected revision."""

    model_config = ConfigDict(extra="forbid")
    body: dict
    expected_revision: int = Field(ge=1)


class DraftPublishRequest(BaseModel):
    """Revision to publish as an immutable asset version."""

    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


class DraftRebaseRequest(BaseModel):
    """Expected draft revision and explicitly reviewed latest version."""

    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    published_version: int = Field(ge=1)


DraftKind = Literal["role", "workflow", "autonomous"]


@router.get("/agent_drafts/{kind}")
async def list_drafts(
    kind: DraftKind,
    username: str = Depends(require_dashboard_user),
    service: RoleAssetService = Depends(get_role_service),
):
    return ok(await service.list_drafts(username, kind))


@router.post("/agent_drafts/{kind}")
async def create_draft(
    kind: DraftKind,
    payload: DraftCreateRequest,
    username: str = Depends(require_dashboard_user),
    service: RoleAssetService = Depends(get_role_service),
):
    try:
        return ok(
            await service.create_draft(username, kind, payload.body, payload.asset_id)
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get("/agent_drafts/{kind}/{asset_id}")
async def get_draft(
    kind: DraftKind,
    asset_id: str,
    username: str = Depends(require_dashboard_user),
    service: RoleAssetService = Depends(get_role_service),
):
    try:
        return ok(await service.get_draft(username, kind, asset_id))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.put("/agent_drafts/{kind}/{asset_id}")
async def update_draft(
    kind: DraftKind,
    asset_id: str,
    payload: DraftUpdateRequest,
    username: str = Depends(require_dashboard_user),
    service: RoleAssetService = Depends(get_role_service),
):
    try:
        return ok(
            await service.update_draft(
                username, kind, asset_id, payload.expected_revision, payload.body
            )
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/agent_drafts/{kind}/{asset_id}/rebase")
async def rebase_draft(
    kind: DraftKind,
    asset_id: str,
    payload: DraftRebaseRequest,
    username: str = Depends(require_dashboard_user),
    service: RoleAssetService = Depends(get_role_service),
):
    try:
        return ok(
            await service.rebase_draft(
                username,
                kind,
                asset_id,
                payload.expected_revision,
                payload.published_version,
            )
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/agent_drafts/{kind}/{asset_id}/publish")
async def publish_draft(
    kind: DraftKind,
    asset_id: str,
    payload: DraftPublishRequest,
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: RoleAssetService = Depends(get_role_service),
):
    try:
        if kind == "role":
            return ok(await service.save_role(username, {}, asset_id, payload.revision))
        workflows = request.app.state.services.workflow_assets
        return ok(
            await workflows.save_asset(username, {}, asset_id, payload.revision, kind)
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


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


@router.delete("/agent_roles/{role_id}")
async def delete_role(
    role_id: str,
    username: str = Depends(require_dashboard_user),
    service: RoleAssetService = Depends(get_role_service),
):
    try:
        await service.delete_asset(username, "role", role_id)
        return ok({})
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
