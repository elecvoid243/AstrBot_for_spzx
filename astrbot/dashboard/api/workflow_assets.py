"""Minimal v2 asset and persisted run HTTP boundary."""

import asyncio
import json
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Security
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.security import APIKeyHeader

from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.chat import ChatAccessRequest
from astrbot.core.agent_teams.history import RunHistoryService
from astrbot.core.agent_teams.workflows import (
    ArtifactRevisionRequest,
    HumanAnswerRequest,
    ToolResolutionRequest,
    WorkflowControlRequest,
    WorkflowImportRequest,
    WorkflowRetryRequest,
    WorkflowSaveRequest,
    WorkflowService,
    WorkflowStartRequest,
)
from astrbot.dashboard.responses import error, ok

from .auth import require_dashboard_user

router = APIRouter(tags=["Agent Teams workflows"])
chat_browser_header = APIKeyHeader(
    name="X-AstrBot-Chat-Session",
    scheme_name="ChatBrowserSession",
    auto_error=False,
    description="Browser-only credential confirmed by the initiating sender in the original chat.",
)


def get_workflow_service(request: Request) -> WorkflowService:
    """Resolve the core-owned service used by every entry point.

    Args:
        request: Current HTTP request.

    Returns:
        Shared workflow asset and run service.
    """
    return request.app.state.services.workflow_assets


def get_history_service(request: Request) -> RunHistoryService:
    """Resolve the shared read-only history service.

    Args:
        request: Current authenticated HTTP request.

    Returns:
        Source-aware history queries over the original stores.
    """
    return request.app.state.services.run_history


@router.get("/agent_history")
async def list_history(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    username: str = Depends(require_dashboard_user),
    service: RunHistoryService = Depends(get_history_service),
):
    """Page owned v2 runs, legacy runs and legacy member conversations.

    Args:
        limit: Maximum records in one page.
        offset: Number of records already read.
        username: Actual authenticated Dashboard user.
        service: Shared history query boundary.

    Returns:
        Source-labelled overviews without full results or process content.
    """
    return ok(await service.list_records(username, limit, offset))


@router.get("/agent_history/{kind}/{identity}")
async def get_history_record(
    kind: Literal["v2_run", "legacy_run", "legacy_conversation"],
    identity: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    username: str = Depends(require_dashboard_user),
    service: RunHistoryService = Depends(get_history_service),
):
    """Read an original source without offering legacy execution controls.

    Args:
        kind: Source discriminator, never inferred from an overlapping ID.
        identity: Original source identity.
        offset: First long-term conversation message to read.
        limit: Maximum long-term messages in one page.
        username: Actual authenticated Dashboard user.
        service: Shared history query boundary.

    Returns:
        Original content and source links, or an ownership error.
    """
    try:
        return ok(await service.get_record(username, kind, identity, offset, limit))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get("/agent_history/legacy_run/{identity}/members/{member_id}")
async def get_history_process(
    identity: str,
    member_id: str,
    before_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    username: str = Depends(require_dashboard_user),
    service: RunHistoryService = Depends(get_history_service),
):
    """Read original legacy transcript rows, including their stored source IDs.

    Args:
        identity: Original legacy run identity.
        member_id: Original member identity in that run.
        before_id: Exclusive original transcript cursor.
        limit: Maximum rows in one page.
        username: Actual authenticated Dashboard user.
        service: Shared history query boundary.

    Returns:
        Chronological original rows and an optional older-page cursor.
    """
    try:
        return ok(
            await service.get_process(username, identity, member_id, before_id, limit)
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflow_chat_runs/{run_id}/connection")
async def open_chat_browser(
    run_id: str, service: WorkflowService = Depends(get_workflow_service)
):
    """Create an unconfirmed browser challenge with no Dashboard authorization.

    Args:
        run_id: Existing chat invocation root.
        service: Shared application service.

    Returns:
        A browser credential and source confirmation command, without run content.
    """
    try:
        return ok(await service.chat.open_browser(run_id))
    except RoleAssetError as exc:
        return JSONResponse(error(str(exc)), status_code=exc.status)


@router.get("/workflow_chat_runs/{run_id}/connection")
async def chat_browser_status(
    run_id: str,
    credential: str | None = Security(chat_browser_header),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Poll source confirmation using the browser-only secret.

    Args:
        run_id: Root run to which this connection is restricted.
        credential: Browser-only session confirmed through the original source.
        service: Shared application service.

    Returns:
        Confirmation state without private execution data.
    """
    try:
        caller = await service.chat.authenticate_browser(
            run_id, credential or "", allow_pending=True
        )
        return ok({"confirmed": caller is not None})
    except RoleAssetError as exc:
        return JSONResponse(error(str(exc)), status_code=exc.status)


@router.get("/workflow_chat_runs/{run_id}")
async def get_chat_run(
    run_id: str,
    credential: str | None = Security(chat_browser_header),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Read the confirmed initiator's restricted view of the original run.

    Args:
        run_id: Root chat invocation.
        credential: Browser-only session confirmed through the original source.
        service: Shared application service.

    Returns:
        Safe status, final result and explicit human prompts from the same run.
    """
    try:
        caller = await service.chat.authenticate_browser(run_id, credential or "")
        assert caller is not None
        return ok(await service.chat.get_run(caller, run_id, include_todos=True))
    except RoleAssetError as exc:
        return JSONResponse(error(str(exc)), status_code=exc.status)


@router.post("/workflow_chat_runs/{run_id}/todos/{todo_id}/answer")
async def answer_chat_todo(
    run_id: str,
    todo_id: str,
    payload: HumanAnswerRequest,
    credential: str | None = Security(chat_browser_header),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Save a source-authenticated answer to the existing persistent request.

    Args:
        run_id: Root chat invocation.
        todo_id: Existing request in that root.
        credential: Browser-only session confirmed through the original source.
        payload: Shared validated answer or explicit version decision.
        service: Shared application service.

    Returns:
        Restricted progress after the shared service accepts the answer.
    """
    try:
        caller = await service.chat.authenticate_browser(run_id, credential or "")
        assert caller is not None
        return ok(
            await service.chat.answer_browser_todo(
                caller, run_id, todo_id, payload.model_dump()
            )
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get("/workflow_chat_runs/{run_id}/todos/{todo_id}/artifact")
async def preview_chat_approval(
    run_id: str,
    todo_id: str,
    credential: str | None = Security(chat_browser_header),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Preview only the version selected by this initiator's approval request.

    Args:
        run_id: Original invocation root.
        todo_id: Existing approval request selecting the artifact version.
        credential: Browser-only session confirmed through the original source.
        service: Shared application service.

    Returns:
        The requested review content without arbitrary artifact access.
    """
    try:
        caller = await service.chat.authenticate_browser(run_id, credential or "")
        assert caller is not None
        return ok(await service.chat.read_browser_artifact(caller, run_id, todo_id))
    except RoleAssetError as exc:
        return JSONResponse(error(str(exc)), status_code=exc.status)


@router.get("/workflow_chat_runs/{run_id}/todos/{todo_id}/artifact/files/{file_id}")
async def preview_chat_approval_file(
    run_id: str,
    todo_id: str,
    file_id: str,
    credential: str | None = Security(chat_browser_header),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Preview a file declared by the exact requested approval version.

    Args:
        run_id: Original invocation root.
        todo_id: Existing approval request selecting the artifact version.
        file_id: Managed file declared by that exact version.
        credential: Browser-only session confirmed through the original source.
        service: Shared application service.

    Returns:
        Restricted preview bytes; this route grants no export or download action.
    """
    try:
        caller = await service.chat.authenticate_browser(run_id, credential or "")
        assert caller is not None
        file = await service.chat.read_browser_artifact(
            caller, run_id, todo_id, file_id
        )
        return Response(
            file["content"],
            media_type=file["media_type"],
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Content-Disposition": "inline; filename*=UTF-8''"
                + quote(file["filename"], safe=""),
            },
        )
    except RoleAssetError as exc:
        return JSONResponse(error(str(exc)), status_code=exc.status)


@router.get(
    "/workflow_chat_runs/{run_id}/result/files/{file_id}",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            }
        }
    },
)
async def get_chat_result_file(
    run_id: str,
    file_id: str,
    download: bool = True,
    credential: str | None = Security(chat_browser_header),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Preview or download a file explicitly published in the completed root result.

    Args:
        run_id: Original completed chat invocation.
        file_id: File declared by the exact published result version.
        download: Use an attachment response instead of an inline preview.
        credential: Browser-only session confirmed through the original source.
        service: Shared application service.

    Returns:
        Scoped final file bytes without arbitrary artifact or owner access.
    """
    try:
        caller = await service.chat.authenticate_browser(run_id, credential or "")
        assert caller is not None
        file = await service.chat.read_browser_result_file(caller, run_id, file_id)
        disposition = "attachment" if download else "inline"
        return Response(
            file["content"],
            media_type=file["media_type"],
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Content-Disposition": f"{disposition}; filename*=UTF-8''"
                + quote(file["filename"], safe=""),
            },
        )
    except RoleAssetError as exc:
        return JSONResponse(error(str(exc)), status_code=exc.status)


@router.get("/workflow_assets/{asset_id}/chat_access")
async def list_chat_access(
    asset_id: str,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """List the asset owner's explicit local platform grants.

    Args:
        asset_id: Owned asset identity.
        username: Authenticated Dashboard owner.
        service: Shared application service.

    Returns:
        Local chat grants or an ownership error.
    """
    try:
        return ok(await service.chat.list_access(username, asset_id))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.put("/workflow_assets/{asset_id}/chat_access")
async def save_chat_access(
    asset_id: str,
    payload: ChatAccessRequest,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Open one fixed version and result conversation to an exact platform sender.

    Args:
        asset_id: Owned asset identity.
        payload: Caller, source conversation, source profile and fixed version.
        username: Authenticated Dashboard owner.
        service: Shared application service.

    Returns:
        Saved local grant or an ownership/validation error.
    """
    try:
        return ok(
            await service.chat.save_access(username, asset_id, payload.model_dump())
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.delete("/workflow_assets/{asset_id}/chat_access/{platform_id}/{sender_id}")
async def revoke_chat_access(
    asset_id: str,
    platform_id: str,
    sender_id: str,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Revoke subsequent platform execution and result delivery.

    Args:
        asset_id: Owned asset identity.
        platform_id: Exact platform instance.
        sender_id: Exact sender on that instance.
        username: Authenticated Dashboard owner.
        service: Shared application service.

    Returns:
        Empty success or an ownership error.
    """
    try:
        await service.chat.revoke_access(username, asset_id, platform_id, sender_id)
        return ok({})
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


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


@router.get("/workflow_assets/{asset_id}/readiness")
async def check_readiness(
    asset_id: str,
    version: int | None = Query(default=None, ge=1),
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Check required local runner capabilities without creating execution.

    Args:
        asset_id: Saved team or workflow identity.
        version: Pinned definition version, or latest when omitted.
        username: Authenticated owner.
        service: Shared asset and run service.

    Returns:
        Readiness and field-level blocking reasons used by launch.
    """
    try:
        return ok(await service.check_readiness(username, asset_id, version))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get("/workflow_assets/{asset_id}/export")
async def export_asset(
    asset_id: str,
    version: int | None = Query(default=None, ge=1),
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Download a validated definition package without local installation data.

    Args:
        asset_id: Workflow owned by the authenticated user.
        version: Exact definition version, or latest.
        username: Authenticated owner.
        service: Shared public asset service.

    Returns:
        Portable JSON content or field-specific errors.
    """
    try:
        return ok(await service.export_asset(username, asset_id, version))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflow_assets/import/preview")
async def preview_import(
    payload: WorkflowImportRequest,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Validate untrusted definitions before showing compatible local candidates.

    Args:
        payload: Package and optional deliberate local resource selections.
        username: Authenticated installing owner.
        service: Shared public asset service.

    Returns:
        Grouped dependency choices, issues and existing-installation identity.
    """
    try:
        return ok(await service.preview_import(username, payload.model_dump()))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflow_assets/import")
async def import_asset(
    payload: WorkflowImportRequest,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Confirm one atomic installation or retain a non-runnable pending asset.

    Args:
        payload: Package, reviewed local choices and pending-save preference.
        username: Authenticated installing owner; package content grants no rights.
        service: Shared public asset service.

    Returns:
        The installed or existing workflow without creating any execution.
    """
    try:
        return ok(await service.import_asset(username, payload.model_dump()))
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


@router.get("/workflow_human_requests")
async def list_human_requests(
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Return pending human requests visible to the authenticated owner.

    Args:
        username: Authenticated dashboard user.
        service: Shared workflow service.

    Returns:
        Persisted pending requests.
    """
    return ok(await service.list_human_requests(username))


@router.get("/workflow_runs")
async def list_runs(
    limit: int = Query(default=50, ge=1, le=100),
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """List run overviews without complete configuration, results or process history.

    Args:
        limit: Maximum number of recent root runs.
        username: Authenticated owner.
        service: Shared public run service.

    Returns:
        Goal previews of at most 200 characters, statuses, task progress, waiting
        reasons, attempts and artifact entries. Complete input values are omitted.
        Read a selected run's detail for complete frozen inputs and saved records.
    """
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


@router.post("/workflow_runs/{run_id}/recover")
async def recover_run(
    run_id: str,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Continue the same attempt from saved model and per-tool boundaries.

    Args:
        run_id: Interrupted task; no new attempt or iteration is created.
        username: Authenticated owner of the original run.
        service: Shared public run service.

    Returns:
        Persisted progress with per-call intents, results, context positions and
        execution ownership. Saved results are reused, unstarted calls may run,
        and unknown effects require confirmation. Contracted submissions finish
        through the existing idempotent result protocol.
    """
    try:
        return ok(await service.recover_run(username, run_id))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflow_runs/{run_id}/control")
async def control_run(
    run_id: str,
    payload: WorkflowControlRequest,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Persist pause, continue or cancellation for the entire existing run.

    Args:
        run_id: Root run whose child tasks share admission and cancellation.
        payload: Idempotent decision and optional observed control revision.
        username: Authenticated owner.
        service: Core-owned public run service.

    Returns:
        Persisted control and progress; in-flight external effects are not undone.
    """
    try:
        return ok(await service.control_run(username, run_id, payload.model_dump()))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflow_runs/{run_id}/todos/{todo_id}/answer")
async def answer_todo(
    run_id: str,
    todo_id: str,
    payload: HumanAnswerRequest,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Save a response to a frozen human request, including while paused.

    Args:
        run_id: Root run owned by the authenticated user.
        todo_id: Persistent human request identity.
        payload: Idempotent answer or approval/rejection with feedback.
        username: Authenticated owner.
        service: Shared application service.

    Returns:
        Persisted run with the saved response and its consumption state.
    """
    try:
        return ok(
            await service.answer_todo(username, run_id, todo_id, payload.model_dump())
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get("/workflow_artifacts/{artifact_id}/versions/{version}/export")
async def export_artifact(
    artifact_id: str,
    version: int,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Read an approved immutable version for delivery.

    Args:
        artifact_id: Stable artifact identity.
        version: Exact approved version.
        username: Authenticated owner.
        service: Shared application service.

    Returns:
        Full approved content and exact file references, or an access error.
    """
    try:
        return ok(await service.export_artifact(username, artifact_id, version))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflow_runs/{run_id}/retry")
async def retry_task(
    run_id: str,
    payload: WorkflowRetryRequest,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Explicitly retry a failed task with its original frozen input references.

    Args:
        run_id: Failed task within the root run or an existing workflow.
        payload: Idempotent request with the failed attempt and input policy.
        username: Authenticated owner.
        service: Core-owned public run service.

    Returns:
        New attempt and preserved failure history; uncertain tools require resolution.
    """
    try:
        return ok(await service.retry_task(username, run_id, payload.model_dump()))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflow_runs/{run_id}/tool_calls/{record_id}/resolve")
async def resolve_tool_call(
    run_id: str,
    record_id: str,
    payload: ToolResolutionRequest,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Record an explicit decision with evidence before resuming an uncertain call.

    Args:
        run_id: Original affected task run.
        record_id: Durable tool invocation requiring confirmation.
        payload: Unique decision identity, chosen action, evidence and observed result.
        username: Authenticated owner of the original task.
        service: Shared durable run service.

    Returns:
        Current task detail, preserving the original call and replacement association.
    """
    try:
        return ok(
            await service.resolve_tool_call(
                username, run_id, record_id, payload.model_dump()
            )
        )
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
        A consistent snapshot on initial or reset reads, otherwise a bounded
        batch of committed events and their process records.
    """
    try:
        return ok(await service.get_events(username, run_id, after))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get(
    "/workflow_runs/{run_id}/events/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def stream_events(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Observe the same authorized persisted cursor without controlling execution.

    Args:
        run_id: Persisted run or graph identity.
        request: HTTP connection used to detect observer disconnects.
        after: Last committed cursor applied by the observer.
        username: Authenticated owner.
        service: Shared public run service.

    Returns:
        Server-sent batches from durable storage, or an authorization error.
    """
    try:
        initial = await service.get_events(username, run_id, after)
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )

    async def observe():
        cursor = after
        batch = initial
        while not await request.is_disconnected():
            if batch["snapshot"] is not None or batch["events"]:
                yield "data: " + json.dumps(batch, ensure_ascii=False) + "\n\n"
                cursor = batch["cursor"]
            await asyncio.sleep(0.5)
            batch = await service.get_events(username, run_id, cursor)

    return StreamingResponse(observe(), media_type="text/event-stream")


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
        file = await service.get_file(
            username, artifact_id, version, file_id, download=download
        )
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
