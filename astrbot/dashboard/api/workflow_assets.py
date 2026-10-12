"""Minimal v2 asset and persisted run HTTP boundary."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.workflows import (
    ArtifactRevisionRequest,
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


@router.get("/workflow_runs/{run_id}/events")
async def get_events(
    run_id: str,
    after: int = Query(default=0, ge=0),
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Read committed changes for reconnecting run observers.

    Args:
        run_id: Persisted run or graph identity.
        after: Last successfully applied event cursor.
        username: Authenticated owner.
        service: Shared public run service.

    Returns:
        Changes with their snapshot, or a reset for an expired cursor.
    """
    try:
        return ok(await service.get_events(username, run_id, after))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get("/workflow_artifacts/{artifact_id}/versions/{version}")
async def read_artifact(
    artifact_id: str,
    version: int,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Read complete immutable content for the authenticated owner.

    Args:
        artifact_id: Stable output identity.
        version: Exact version to read.
        username: Authenticated owner.
        service: Core-owned public service.

    Returns:
        Complete result content or a structured access error.
    """
    try:
        return ok(await service.read_artifact(username, artifact_id, version))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflow_artifacts/{artifact_id}/versions")
async def revise_artifact(
    artifact_id: str,
    payload: ArtifactRevisionRequest,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Create an explicit version without scheduling downstream work.

    Args:
        artifact_id: Output identity to revise.
        payload: Validated revision request envelope.
        username: Authenticated owner.
        service: Core-owned public service.

    Returns:
        Exact version reference or structured field errors.
    """
    try:
        return ok(
            await service.revise_artifact(username, artifact_id, payload.model_dump())
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflow_runs/{run_id}/files")
async def store_file(
    run_id: str,
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Store a ready file body for submission or an explicit revision.

    Args:
        run_id: Owning output run.
        request: Raw complete file body.
        filename: Display filename, without a local directory.
        username: Authenticated owner.
        service: Core-owned public service.

    Returns:
        Stable file metadata or a readiness error.
    """
    try:
        run = await service.get_run(username, run_id)
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 50 * 1024 * 1024:
                raise RoleAssetError("Files must be at most 50 MiB", status=413)
        attempt = (
            None if run["status"] == "completed" else run["state"].get("attempt_id")
        )
        return ok(
            await service.store_file(
                username, run_id, attempt, filename, bytes(content)
            )
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get("/workflow_artifacts/{artifact_id}/versions/{version}/files/{file_id}")
async def get_file(
    artifact_id: str,
    version: int,
    file_id: str,
    download: bool = True,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Preview or download a file belonging to a specific committed version.

    Args:
        artifact_id: Stable output identity.
        version: Exact committed version.
        file_id: Managed file reference declared in that version.
        download: Whether the browser should save the file.
        username: Authenticated owner.
        service: Core-owned public service.

    Returns:
        Complete bytes and safe display headers, or an access error.
    """
    try:
        file = await service.get_file(username, artifact_id, version, file_id)
        disposition = "attachment" if download else "inline"
        return Response(
            file["content"],
            media_type=file["media_type"],
            headers={
                "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(file['filename'], safe='')}",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )
