"""Minimal v2 asset and persisted run HTTP boundary."""

import asyncio
import json
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Security
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError
from starlette.background import BackgroundTask

from astrbot.core.agent_teams import WORKFLOW_API_SCOPE
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.chat import ChatAccessRequest, ChatSourceAccessError
from astrbot.core.agent_teams.graph import GraphDefinition, validate_graph
from astrbot.core.agent_teams.history import RunHistoryService
from astrbot.core.agent_teams.workflows import (
    ArtifactRevisionRequest,
    DebugForkAttempt,
    DebugForkPreviewRequest,
    DebugForkRequest,
    HumanAnswerRequest,
    ToolResolutionRequest,
    TrialRunRequest,
    WorkflowControlRequest,
    WorkflowImportRequest,
    WorkflowRerunRequest,
    WorkflowRetryRequest,
    WorkflowSaveRequest,
    WorkflowService,
    WorkflowStartRequest,
)
from astrbot.core.provider.provider import Provider
from astrbot.dashboard.responses import ApiError, error, ok

from .auth import AuthContext, ScopeDependency, require_dashboard_user

router = APIRouter(tags=["Agent Teams workflows"])
require_workflow_scope = ScopeDependency(WORKFLOW_API_SCOPE)
_process_observers = asyncio.Semaphore(32)
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
        Owner-scoped history queries over persisted root runs.
    """
    return request.app.state.services.run_history


@router.get("/agent_history")
async def list_history(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    username: str = Depends(require_dashboard_user),
    service: RunHistoryService = Depends(get_history_service),
):
    """Page owned workflow and autonomous team root runs.

    Args:
        limit: Maximum records in one page.
        offset: Number of records already read.
        username: Actual authenticated Dashboard user.
        service: Shared history query boundary.

    Returns:
        Source-labelled overviews without full results or process content.
    """
    return ok(await service.list_records(username, limit, offset))


_CHAT_NO_STORE = {"Cache-Control": "no-store"}


def _chat_json(data: object) -> JSONResponse:
    """Keep restricted chat data and credentials out of HTTP caches.

    Args:
        data: Result of a restricted chat operation.

    Returns:
        The success envelope with caching disabled.
    """
    return JSONResponse(jsonable_encoder(ok(data)), headers=_CHAT_NO_STORE)


def _chat_error_response(
    exc: RoleAssetError, *, include_fields: bool = False
) -> JSONResponse:
    """Mark a connection denial even when the client is expecting file bytes.

    Args:
        exc: Error from a restricted run endpoint.
        include_fields: Preserve the answer endpoint's validation diagnostics.

    Returns:
        The original failure with a reason header for scope-wide denials.
    """
    headers = _CHAT_NO_STORE.copy()
    if isinstance(exc, ChatSourceAccessError):
        headers["X-AstrBot-Chat-Access-Error"] = exc.reason
    return JSONResponse(
        error(str(exc), {"fields": exc.fields} if include_fields else None),
        status_code=exc.status,
        headers=headers,
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
        return _chat_json(await service.chat.open_browser(run_id))
    except RoleAssetError as exc:
        return _chat_error_response(exc)


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
        caller, _ = await service.chat.authenticate_browser(
            run_id, credential or "", allow_pending=True
        )
        return _chat_json({"confirmed": caller is not None})
    except RoleAssetError as exc:
        return _chat_error_response(exc)


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
        caller, _ = await service.chat.authenticate_browser(run_id, credential or "")
        assert caller is not None
        return _chat_json(
            await service.chat.get_run(caller, run_id, include_todos=True)
        )
    except RoleAssetError as exc:
        return _chat_error_response(exc)


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
        caller, expires_at = await service.chat.authenticate_browser(
            run_id, credential or ""
        )
        assert caller is not None
        return _chat_json(
            await service.chat.answer_browser_todo(
                caller,
                run_id,
                todo_id,
                payload.model_dump(),
                browser_expires_at=expires_at,
            )
        )
    except RoleAssetError as exc:
        return _chat_error_response(exc, include_fields=True)


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
        caller, _ = await service.chat.authenticate_browser(run_id, credential or "")
        assert caller is not None
        return _chat_json(
            await service.chat.read_browser_artifact(caller, run_id, todo_id)
        )
    except RoleAssetError as exc:
        return _chat_error_response(exc)


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
        caller, _ = await service.chat.authenticate_browser(run_id, credential or "")
        assert caller is not None
        file = await service.chat.read_browser_artifact(
            caller, run_id, todo_id, file_id
        )
        return Response(
            file["content"],
            media_type=file["media_type"],
            headers={
                **_CHAT_NO_STORE,
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Content-Disposition": "inline; filename*=UTF-8''"
                + quote(file["filename"], safe=""),
            },
        )
    except RoleAssetError as exc:
        return _chat_error_response(exc)


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
        caller, _ = await service.chat.authenticate_browser(run_id, credential or "")
        assert caller is not None
        file = await service.chat.read_browser_result_file(caller, run_id, file_id)
        disposition = "attachment" if download else "inline"
        return Response(
            file["content"],
            media_type=file["media_type"],
            headers={
                **_CHAT_NO_STORE,
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Content-Disposition": f"{disposition}; filename*=UTF-8''"
                + quote(file["filename"], safe=""),
            },
        )
    except RoleAssetError as exc:
        return _chat_error_response(exc)


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


@router.get("/workflow_components")
async def list_workflow_components(
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Return the current installed workflow component catalog.

    Args:
        username: Authenticated Dashboard resource owner.
        service: Shared workflow service.

    Returns:
        The portable registered declarations or an authorization error.
    """
    try:
        return ok(await service.list_components(username))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get("/workflow_assets")
async def list_assets(
    include_example: bool = False,
    include_team_example: bool = False,
    summary_only: bool = False,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """List the current owner's published workflows and teams.

    Args:
        include_example: Provision the editable starter workflow on first access.
        include_team_example: Provision the autonomous starter team.
        summary_only: Return card summaries without full graph definitions.
        username: Stable resource owner of the authorized Dashboard account.
        service: Core-owned workflow service.

    Returns:
        Active assets with their latest versions and example identity.
    """
    return ok(
        await service.list_assets(
            username,
            include_example=include_example,
            include_team_example=include_team_example,
            summary_only=summary_only,
        )
    )


class WorkflowDraftGenerationRequest(BaseModel):
    """A workflow description and the locally configured model to answer it."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    description: str = Field(min_length=1, max_length=4000)
    provider_id: str = Field(min_length=1, max_length=128)


class GeneratedWorkflowStep(BaseModel):
    """One reviewable Agent task proposed by the model."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=128)
    instructions: str = Field(min_length=1, max_length=6000)


class GeneratedWorkflowPlan(BaseModel):
    """A bounded sequence of tasks over a user-supplied text input."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=128)
    steps: list[GeneratedWorkflowStep] = Field(min_length=1, max_length=8)


@router.post("/workflow_assets/generate_draft")
async def generate_workflow_draft(
    payload: WorkflowDraftGenerationRequest,
    _username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Return an editable graph proposal without creating an asset or a run.

    Args:
        payload: Description and explicit local model selection.
        _username: Authenticated Dashboard user.
        service: Provider of the live workflow resource catalog.

    Returns:
        The generated native definition or a structured request error.
    """
    core = service.roles.core_lifecycle
    if not core:
        message = "Choose an available model"
        return JSONResponse(
            error(
                message,
                {
                    "fields": [
                        {"path": "provider_id", "code": "MISSING", "message": message}
                    ]
                },
            ),
            status_code=422,
        )
    role = {
        "name": "Workflow draft",
        "prompt": "Follow the task instructions and submit the result.",
        "tools": [],
        "skills": [],
        "knowledge_bases": [],
        "model_capabilities": ["tool_use"],
    }
    installation = {"profile_id": "default"}
    resolved = await service.roles.preview_role(
        {
            "definition": {**role, "provider_id": payload.provider_id},
            "installation": installation,
        }
    )
    if resolved["issues"] or resolved["effective"]["runner"]["type"] != "local":
        return JSONResponse(
            error(
                "Selected model is unavailable to the default local workflow profile",
                {"fields": resolved["issues"]},
            ),
            status_code=422,
        )
    provider = await core.provider_manager.get_provider_by_id(payload.provider_id)
    if not isinstance(provider, Provider):
        message = "Choose an available chat model"
        return JSONResponse(
            error(
                message,
                {
                    "fields": [
                        {
                            "path": "provider_id",
                            "code": "INCOMPATIBLE",
                            "message": message,
                        }
                    ]
                },
            ),
            status_code=422,
        )
    try:
        response = await asyncio.wait_for(
            provider.text_chat(
                system_prompt=(
                    "Design a short sequential workflow for the user's request. "
                    "Return only JSON with a name and a steps array of 1 to 8 objects. "
                    "Each step has a name and instructions in the user's language. "
                    "The first step gets the workflow's input text. Later steps get "
                    "the previous step's text. Do not name tools, resources or IDs."
                ),
                prompt=payload.description,
                request_max_retries=1,
            ),
            timeout=60,
        )
    except Exception:
        return JSONResponse(
            error("Model could not generate a workflow draft"), status_code=502
        )
    content = (response.completion_text or "").strip()
    if content.startswith("```") and content.endswith("```"):
        content = content.partition("\n")[2][:-3].strip()
    try:
        plan = GeneratedWorkflowPlan.model_validate_json(content)
        tasks = []
        edges = []
        for index, step in enumerate(plan.steps, start=1):
            identity = f"step-{index}"
            source = f"step-{index - 1}"
            if index > 1:
                edges.append({"source": source, "target": identity})
            instructions = step.instructions.replace("{", "{{").replace("}", "}}")
            tasks.append(
                {
                    "id": identity,
                    "name": step.name,
                    "member": identity,
                    "role": {**role, "name": step.name},
                    "installation": installation,
                    "task": instructions + "\n\n{input}",
                    "inputs": {
                        "input": {"input": "input"}
                        if index == 1
                        else {"task_id": source}
                    },
                    "position": {"x": index * 300.0, "y": 100.0},
                }
            )
        graph = GraphDefinition.model_validate(
            {
                "name": plan.name,
                "default_provider_id": payload.provider_id,
                "required_inputs": ["input"],
                "port_contract_version": 1,
                "result_task_id": tasks[-1]["id"],
                "tasks": tasks,
                "edges": edges,
            }
        )
        validate_graph(graph, result_requirement="reachable")
    except (ValidationError, RoleAssetError) as exc:
        return JSONResponse(
            error(
                "Model returned an invalid workflow draft",
                {"fields": exc.fields if isinstance(exc, RoleAssetError) else []},
            ),
            status_code=422,
        )
    return ok(graph.model_dump())


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


@router.delete("/workflow_assets/{asset_id}")
async def delete_asset(
    asset_id: str,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    try:
        await service.roles.delete_asset(username, "workflow", asset_id)
        return ok({})
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
    """Download a fixed definition closure and source resource requirements.

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
        Source requirements, local candidates and readiness diagnostics.
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
    """Confirm one atomic installation of the complete selected closure.

    Args:
        payload: Package and reviewed local resource choices.
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


class DashboardWorkflowStartRequest(WorkflowStartRequest):
    replay_only: bool = False


class WorkflowApiGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=1)


class WorkflowApiStartRequest(WorkflowApiGrantRequest):
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^\S+$")
    inputs: dict[str, JsonValue] = Field(default_factory=dict)


class WorkflowApiRun(BaseModel):
    run_id: str
    asset_id: str
    asset_version: int
    status: str
    result: dict | None


class WorkflowApiRunResponse(BaseModel):
    status: Literal["ok"] = "ok"
    message: str | None = None
    data: WorkflowApiRun


@router.put("/workflow_assets/{asset_id}/api_access/{key_id}")
async def save_workflow_api_access(
    asset_id: str,
    key_id: str,
    payload: WorkflowApiGrantRequest,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Delegate launch and result access for one published asset version.

    Args:
        asset_id: Owned workflow asset.
        key_id: Identifier of the authorized API key.
        payload: Fixed asset version being opened.
        username: Dashboard asset owner.
        service: Shared workflow service.

    Returns:
        The authorized version.
    """
    try:
        await service.set_api_access(username, asset_id, key_id, payload.version)
        return ok(payload.model_dump())
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.delete("/workflow_assets/{asset_id}/api_access/{key_id}")
async def revoke_workflow_api_access(
    asset_id: str,
    key_id: str,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Revoke launches, replays and result access under an API key grant.

    Args:
        asset_id: Owned workflow asset.
        key_id: Identifier of the previously authorized key.
        username: Dashboard asset owner.
        service: Shared workflow service.

    Returns:
        An empty confirmation.
    """
    try:
        await service.set_api_access(username, asset_id, key_id, None)
        return ok()
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflows/{asset_id}/runs", response_model=WorkflowApiRunResponse)
async def start_public_workflow(
    asset_id: str,
    payload: WorkflowApiStartRequest,
    auth: AuthContext = Depends(require_workflow_scope),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Start a fixed published workflow on behalf of an explicitly granted key.

    Args:
        asset_id: Authorized workflow asset.
        payload: Exact version, declared JSON inputs and request identity.
        auth: Authenticated caller with workflow scope.
        service: Shared workflow service.

    Returns:
        Root run identity and restricted result projection.
    """
    key_id = auth.api_key_id
    if key_id is None:
        raise ApiError("Workflow API key required", status_code=401)
    try:
        async with service.db.get_db() as session:
            grant = await service.require_api_access(
                session, key_id, asset_id, payload.version
            )
        run = await service.start_run(
            grant.owner_username,
            asset_id,
            {**payload.model_dump(), "source": "api"},
            api_key_id=key_id,
        )
        return ok(await service.get_api_run(key_id, run["run_id"]))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get("/workflows/runs/{run_id}", response_model=WorkflowApiRunResponse)
async def get_public_workflow_run(
    run_id: str,
    auth: AuthContext = Depends(require_workflow_scope),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Read the root result using the same key and current fixed-version grant.

    Args:
        run_id: Root identity returned by the launch.
        auth: Authenticated caller with workflow scope.
        service: Shared workflow service.

    Returns:
        Restricted status and the completed root's result.
    """
    key_id = auth.api_key_id
    if key_id is None:
        raise ApiError("Workflow API key required", status_code=401)
    try:
        return ok(await service.get_api_run(key_id, run_id))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflow_assets/{asset_id}/runs")
async def start_run(
    asset_id: str,
    payload: DashboardWorkflowStartRequest,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    try:
        return ok(
            await service.start_run(
                username,
                asset_id,
                payload.model_dump(exclude={"source", "replay_only"}),
                replay_only=payload.replay_only,
            )
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflow_runs/{run_id}/rerun")
async def rerun_workflow(
    run_id: str,
    payload: WorkflowRerunRequest,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Start an independent run with the completed root's version and inputs.

    Args:
        run_id: Completed published root run.
        payload: Idempotency identity for the new run.
        username: Current authenticated resource owner.
        service: Shared public run service.

    Returns:
        The new run's persisted snapshot.
    """
    try:
        return ok(await service.rerun(username, run_id, payload))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflow_assets/{asset_id}/trials/preview")
async def preview_trial(
    asset_id: str,
    payload: TrialRunRequest,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Preview a saved draft trial scope without dispatching tasks.

    Args:
        asset_id: Saved draft identity.
        payload: Requested revision and scope.
        username: Authenticated dashboard owner.
        service: Shared workflow service.

    Returns:
        Selected nodes and the required input contracts.
    """
    try:
        return ok(await service.preview_trial(username, asset_id, payload.model_dump()))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflow_assets/{asset_id}/trials")
async def start_trial(
    asset_id: str,
    payload: TrialRunRequest,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Start a draft trial using the shared workflow scheduler.

    Args:
        asset_id: Saved draft identity.
        payload: Requested scope and explicitly selected inputs.
        username: Authenticated dashboard owner.
        service: Shared workflow service.

    Returns:
        The independent trial run snapshot.
    """
    try:
        return ok(await service.start_trial(username, asset_id, payload.model_dump()))
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
    summary_only: bool = False,
    include_artifacts: bool = False,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """List run overviews without complete configuration, results or process history.

    Args:
        limit: Maximum number of recent root runs.
        summary_only: Select the compact workbench listing.
        include_artifacts: Include artifact choices on demand in the compact listing.
        username: Authenticated owner.
        service: Shared public run service.

    Returns:
        Goal previews of at most 200 characters, statuses, task progress, waiting
        reasons, attempts and artifact entries. Complete input values are omitted.
        Read a selected run's detail for complete frozen inputs and saved records.
    """
    return ok(
        await service.list_runs(
            username,
            limit,
            summary_only=summary_only,
            include_artifacts=include_artifacts,
        )
    )


@router.get("/workflow_runs/{run_id}")
async def get_run(
    run_id: str,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    try:
        return ok(await service.get_run(username, run_id, history_limit=128))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get("/workflow_runs/{run_id}/data_line")
async def inspect_workflow_data_line(
    run_id: str,
    scope_run_id: str,
    task_run_id: str,
    input_name: str,
    attempt_id: str | None = None,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Inspect the recorded read on a data line in an owned root run.

    Args:
        run_id: Root run containing the selected scope.
        scope_run_id: Invocation containing the consumer.
        task_run_id: Consumer instance in the selected round.
        input_name: Named input port on that consumer.
        attempt_id: Exact attempt to inspect, or the current attempt by default.
        username: Authenticated Dashboard owner.
        service: Shared workflow service.

    Returns:
        The read status, evidence and any separately available upstream value.
    """
    try:
        return JSONResponse(
            ok(
                await service.inspect_data_line(
                    username, run_id, scope_run_id, task_run_id, input_name, attempt_id
                )
            ),
            headers={"Cache-Control": "no-store"},
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get("/workflow_runs/{run_id}/debug_forks/failed_child")
async def list_failed_child_debug_attempts(
    run_id: str,
    scope_run_id: str = Query(min_length=1, max_length=128),
    task_run_id: str = Query(min_length=1, max_length=128),
    attempt_id: str = Query(min_length=1, max_length=128),
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """List the recorded failures inside one owned fixed child invocation.

    Args:
        run_id: Source root run.
        scope_run_id: Scope containing the parent call.
        task_run_id: Exact call task in its parent round.
        attempt_id: Attempt whose saved state identifies the child.
        username: Authenticated Dashboard owner.
        service: Shared workflow service.

    Returns:
        Candidate task and attempt identities inside that child scope.
    """
    try:
        return JSONResponse(
            ok(
                await service.failed_child_debug_attempts(
                    username,
                    run_id,
                    DebugForkAttempt(
                        scope_run_id=scope_run_id,
                        task_run_id=task_run_id,
                        attempt_id=attempt_id,
                    ),
                )
            ),
            headers={"Cache-Control": "no-store"},
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflow_runs/{run_id}/debug_forks/preview")
async def preview_workflow_debug_fork(
    run_id: str,
    payload: DebugForkPreviewRequest,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Preview the recorded inputs for one deterministic node.

    Args:
        run_id: Source root run.
        payload: Exact scope, task, and attempt to preview.
        username: Authenticated Dashboard owner.
        service: Shared workflow service.

    Returns:
        The original definition, input evidence and result.
    """
    try:
        return JSONResponse(
            ok(await service.preview_debug_fork(username, run_id, payload)),
            headers={"Cache-Control": "no-store"},
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/workflow_runs/{run_id}/debug_forks")
async def start_workflow_debug_fork(
    run_id: str,
    payload: DebugForkRequest,
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Start the confirmed debug fork and return its independent identity.

    Args:
        run_id: Source root run.
        payload: Exact confirmed attempt and optional replacement.
        username: Authenticated Dashboard owner.
        service: Shared workflow service.

    Returns:
        The persisted fork's root run identity.
    """
    try:
        run = await service.start_debug_fork(username, run_id, payload)
        return ok({"run_id": run["run_id"], "status": run["status"]})
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
        run_id: Task with a resumable checkpoint in its original attempt.
        username: Authenticated owner of the original run.
        service: Shared public run service.

    Returns:
        Persisted progress with per-call intents, results, context positions and
        execution ownership. Saved results are reused, unstarted calls may run,
        and unknown effects require confirmation. A failed model request may be
        sent again with the saved tool results in context. Contracted submissions
        finish through the existing idempotent result protocol.
    """
    try:
        await service.recover_run(username, run_id)
        return ok(await service.get_run(username, run_id, history_limit=128))
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
        await service.control_run(username, run_id, payload.model_dump())
        return ok(await service.get_run(username, run_id, history_limit=128))
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
        await service.answer_todo(username, run_id, todo_id, payload.model_dump())
        return ok(await service.get_run(username, run_id, history_limit=128))
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
        New attempt and preserved failure history, or HTTP 409 when uncertain
        effects or saved tool results require handling in the original attempt.
    """
    try:
        await service.retry_task(username, run_id, payload.model_dump())
        return ok(await service.get_run(username, run_id, history_limit=128))
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
        await service.resolve_tool_call(
            username, run_id, record_id, payload.model_dump()
        )
        return ok(await service.get_run(username, run_id, history_limit=128))
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get("/workflow_runs/{run_id}/history")
async def get_run_history_page(
    run_id: str,
    task_run_id: str,
    kind: Literal["events", "messages", "usage", "attempts"],
    before: str | None = None,
    limit: int = Query(default=128, ge=1, le=128),
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Read a bounded page of authorized execution history.

    Args:
        run_id: Root or scope to observe.
        task_run_id: Selected task or subtree.
        kind: Event, scoped message, attempt or usage history.
        before: Exclusive page boundary.
        limit: Maximum entries in the page.
        username: Authenticated owner.
        service: Shared workflow service.

    Returns:
        Ordered entries and the next older page boundary.
    """
    try:
        return ok(
            await service.get_run_history_page(
                username, run_id, task_run_id, kind, before, limit
            )
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get("/workflow_runs/{run_id}/process")
async def get_process_page(
    run_id: str,
    task_run_id: str,
    before: int = Query(ge=1),
    limit: int = Query(default=128, ge=1, le=128),
    username: str = Depends(require_dashboard_user),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Read older committed process observations of an authorized task.

    Args:
        run_id: Root or scope to observe.
        task_run_id: Task execution in that subtree.
        before: Exclusive event cursor.
        limit: Maximum number of records.
        username: Authenticated owner.
        service: Shared workflow service.

    Returns:
        Ordered records and the next older page boundary.
    """
    try:
        return ok(
            await service.get_process_page(username, run_id, task_run_id, before, limit)
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

    if _process_observers.locked():
        return JSONResponse(error("Too many process observers"), status_code=429)
    await _process_observers.acquire()
    released = False

    def release_observer():
        """Release the slot once after disconnect or response cleanup."""
        nonlocal released
        if not released:
            released = True
            _process_observers.release()

    async def observe():
        try:
            cursor = after
            batch = initial
            while not await request.is_disconnected():
                if batch["snapshot"] is not None or batch["events"]:
                    yield "data: " + json.dumps(batch, ensure_ascii=False) + "\n\n"
                    cursor = batch["cursor"]
                await asyncio.sleep(0.5)
                batch = await service.get_events(username, run_id, cursor)
        finally:
            release_observer()

    return StreamingResponse(
        observe(),
        media_type="text/event-stream",
        background=BackgroundTask(release_observer),
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
