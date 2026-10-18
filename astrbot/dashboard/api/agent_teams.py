"""Agent Teams dashboard routes (spec §7)."""

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.migration import LegacyMigrationRequest
from astrbot.dashboard.responses import error, ok
from astrbot.dashboard.services.agent_team_run_service import AgentTeamRunService
from astrbot.dashboard.services.agent_team_service import (
    AgentTeamService,
    AgentTeamsServiceError,
)

from .auth import require_dashboard_user

router = APIRouter(tags=["Agent Teams"])
legacy_router = APIRouter(
    prefix="/api/agent_teams",
    tags=["Dashboard Agent Teams"],
    include_in_schema=False,
)

# Single override target for tests; both routers use this dependency.
_auth_dep = require_dashboard_user

# Service errors containing this marker are one-active-run-per-team
# conflicts (spec §6.3) and map to 409; every other service error is 400.
_CONFLICT_MARKER = "active run"

# Seconds without a live event before the SSE stream sends a heartbeat.
_SSE_HEARTBEAT_SECONDS = 15.0


def get_team_service(request: Request) -> AgentTeamService:
    return request.app.state.services.agent_teams


def get_run_service(request: Request) -> AgentTeamRunService:
    return request.app.state.services.agent_team_runs


def _handle(e: AgentTeamsServiceError):
    """Map a service error to an HTTP response; run conflicts become 409."""
    status = 409 if _CONFLICT_MARKER in str(e) else e.status
    # Field-level validation problems ride the error envelope as data.fields.
    data = {"fields": e.field_errors} if e.field_errors else None
    return JSONResponse(error(str(e), data), status_code=status)


async def _json_body(request: Request) -> dict:
    try:
        payload = await request.json()
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


@router.post("/agent_teams")
@legacy_router.post("")
async def create_team(
    request: Request,
    username: str = Depends(_auth_dep),
    service: AgentTeamService = Depends(get_team_service),
):
    try:
        return ok(await service.create_team(username, await _json_body(request)))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.get("/agent_teams/{team_id}/migration/preview")
async def preview_legacy_migration(
    team_id: str, request: Request, username: str = Depends(_auth_dep)
):
    """Preview definitions, effective roles, mappings and unresolved legacy items.

    Args:
        team_id: Legacy team owned by the current user.
        request: Dashboard request carrying shared application services.
        username: Authenticated owner; migration grants no additional access.

    Returns:
        A read-only proposal, active legacy run list and confirmation token.
    """
    try:
        return ok(
            await request.app.state.services.legacy_migration.preview(username, team_id)
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.post("/agent_teams/{team_id}/migration")
async def confirm_legacy_migration(
    team_id: str,
    payload: LegacyMigrationRequest,
    request: Request,
    username: str = Depends(_auth_dep),
):
    """Atomically migrate the reviewed definitions without starting execution.

    Args:
        team_id: Legacy team owned by the current user.
        payload: Reviewed token and explicit pending-save preference.
        request: Dashboard request carrying shared application services.
        username: Authenticated legacy and new asset owner.

    Returns:
        Durable old-to-new identities, pending items and reuse status.
    """
    try:
        return ok(
            await request.app.state.services.legacy_migration.confirm(
                username, team_id, payload.model_dump()
            )
        )
    except RoleAssetError as exc:
        return JSONResponse(
            error(str(exc), {"fields": exc.fields}), status_code=exc.status
        )


@router.get("/agent_teams")
@legacy_router.get("")
async def list_teams(
    username: str = Depends(_auth_dep),
    service: AgentTeamService = Depends(get_team_service),
):
    return ok(await service.list_teams(username))


@router.get("/agent_teams/{team_id}")
@legacy_router.get("/{team_id}")
async def get_team(
    team_id: str,
    username: str = Depends(_auth_dep),
    service: AgentTeamService = Depends(get_team_service),
):
    try:
        return ok(await service.get_team(username, team_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.patch("/agent_teams/{team_id}")
@legacy_router.patch("/{team_id}")
async def update_team(
    team_id: str,
    request: Request,
    username: str = Depends(_auth_dep),
    service: AgentTeamService = Depends(get_team_service),
):
    try:
        return ok(
            await service.update_team(username, team_id, await _json_body(request))
        )
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.delete("/agent_teams/{team_id}")
@legacy_router.delete("/{team_id}")
async def delete_team(
    team_id: str,
    username: str = Depends(_auth_dep),
    service: AgentTeamService = Depends(get_team_service),
):
    try:
        return ok(await service.delete_team(username, team_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.post("/agent_teams/{team_id}/members")
@legacy_router.post("/{team_id}/members")
async def add_member(
    team_id: str,
    request: Request,
    username: str = Depends(_auth_dep),
    service: AgentTeamService = Depends(get_team_service),
):
    try:
        return ok(
            await service.add_member(username, team_id, await _json_body(request))
        )
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.delete("/agent_teams/{team_id}/members/{member_id}")
@legacy_router.delete("/{team_id}/members/{member_id}")
async def remove_member(
    team_id: str,
    member_id: str,
    username: str = Depends(_auth_dep),
    service: AgentTeamService = Depends(get_team_service),
):
    try:
        return ok(await service.remove_member(username, team_id, member_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.patch("/agent_teams/{team_id}/members/{member_id}")
@legacy_router.patch("/{team_id}/members/{member_id}")
async def update_member(
    team_id: str,
    member_id: str,
    request: Request,
    username: str = Depends(_auth_dep),
    service: AgentTeamService = Depends(get_team_service),
):
    """Update a member of an agent team (name, persona/provider, runner_config)."""
    try:
        return ok(
            await service.update_member(
                username, team_id, member_id, await _json_body(request)
            )
        )
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.post("/agent_teams/{team_id}/workflows")
@legacy_router.post("/{team_id}/workflows")
async def create_workflow(
    team_id: str,
    request: Request,
    username: str = Depends(_auth_dep),
    service: AgentTeamService = Depends(get_team_service),
):
    try:
        return ok(
            await service.create_workflow(username, team_id, await _json_body(request))
        )
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.get("/agent_teams/{team_id}/workflows")
@legacy_router.get("/{team_id}/workflows")
async def list_workflows(
    team_id: str,
    username: str = Depends(_auth_dep),
    service: AgentTeamService = Depends(get_team_service),
):
    try:
        return ok(await service.get_workflows(username, team_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.put("/agent_teams/{team_id}/workflows/{workflow_id}")
@legacy_router.put("/{team_id}/workflows/{workflow_id}")
async def update_workflow(
    team_id: str,
    workflow_id: str,
    request: Request,
    username: str = Depends(_auth_dep),
    service: AgentTeamService = Depends(get_team_service),
):
    try:
        return ok(
            await service.update_workflow(
                username, team_id, workflow_id, await _json_body(request)
            )
        )
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.delete("/agent_teams/{team_id}/workflows/{workflow_id}")
@legacy_router.delete("/{team_id}/workflows/{workflow_id}")
async def delete_workflow(
    team_id: str,
    workflow_id: str,
    username: str = Depends(_auth_dep),
    service: AgentTeamService = Depends(get_team_service),
):
    try:
        return ok(await service.delete_workflow(username, team_id, workflow_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.post(
    "/agent_teams/{team_id}/runs",
    deprecated=True,
    responses={
        410: {"description": "Legacy launches are retired; start a versioned asset run"}
    },
)
@legacy_router.post("/{team_id}/runs")
async def start_run(
    team_id: str,
    request: Request,
    username: str = Depends(_auth_dep),
    service: AgentTeamRunService = Depends(get_run_service),
):
    """Retire both legacy launch URLs while retaining owner-scoped history.

    Args:
        team_id: Existing legacy definition owned by the authenticated user.
        request: Original request, retained for the retired route contract.
        username: Authenticated owner.
        service: Original legacy service used to enforce ownership.

    Returns:
        A gone response directing new work to the unified asset entry.
    """
    try:
        await service.list_team_runs(username, team_id)
        return JSONResponse(
            error(
                "Legacy launches are retired. Migrate the definition and start a new asset run."
            ),
            status_code=410,
        )
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.get("/agent_teams/runs/active")
@legacy_router.get("/runs/active")
async def active_runs(
    username: str = Depends(_auth_dep),
    service: AgentTeamRunService = Depends(get_run_service),
):
    try:
        return ok(await service.list_active_runs(username))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.get("/agent_teams/{team_id}/runs")
@legacy_router.get("/{team_id}/runs")
async def team_runs(
    team_id: str,
    username: str = Depends(_auth_dep),
    service: AgentTeamRunService = Depends(get_run_service),
):
    try:
        return ok(await service.list_team_runs(username, team_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.post("/agent_teams/runs/{run_id}/pause")
@legacy_router.post("/runs/{run_id}/pause")
async def pause_run(
    run_id: str,
    username: str = Depends(_auth_dep),
    service: AgentTeamRunService = Depends(get_run_service),
):
    try:
        return ok(await service.pause_run(username, run_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.post("/agent_teams/runs/{run_id}/resume")
@legacy_router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    username: str = Depends(_auth_dep),
    service: AgentTeamRunService = Depends(get_run_service),
):
    try:
        return ok(await service.resume_run(username, run_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.post("/agent_teams/runs/{run_id}/stop")
@legacy_router.post("/runs/{run_id}/stop")
async def stop_run(
    run_id: str,
    username: str = Depends(_auth_dep),
    service: AgentTeamRunService = Depends(get_run_service),
):
    try:
        return ok(await service.request_stop_run(username, run_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.post("/agent_teams/runs/{run_id}/nodes/{node_id}/retry")
@legacy_router.post("/runs/{run_id}/nodes/{node_id}/retry")
async def retry_node(
    run_id: str,
    node_id: str,
    username: str = Depends(_auth_dep),
    service: AgentTeamRunService = Depends(get_run_service),
):
    try:
        return ok(await service.retry_node(username, run_id, node_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.post("/agent_teams/runs/{run_id}/nodes/{node_id}/skip")
@legacy_router.post("/runs/{run_id}/nodes/{node_id}/skip")
async def skip_node(
    run_id: str,
    node_id: str,
    username: str = Depends(_auth_dep),
    service: AgentTeamRunService = Depends(get_run_service),
):
    try:
        return ok(await service.skip_node(username, run_id, node_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.post("/agent_teams/runs/{run_id}/members/{member_id}/interrupt")
@legacy_router.post("/runs/{run_id}/members/{member_id}/interrupt")
async def interrupt_member(
    run_id: str,
    member_id: str,
    username: str = Depends(_auth_dep),
    service: AgentTeamRunService = Depends(get_run_service),
):
    try:
        return ok(await service.interrupt_node(username, run_id, member_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.get("/agent_teams/runs/{run_id}/members/{member_id}/transcript")
@legacy_router.get("/runs/{run_id}/members/{member_id}/transcript")
async def member_transcript(
    run_id: str,
    member_id: str,
    before_id: int | None = None,
    limit: int = 50,
    username: str = Depends(_auth_dep),
    service: AgentTeamRunService = Depends(get_run_service),
):
    try:
        return ok(
            await service.get_transcript(
                username, run_id, member_id, before_id=before_id, limit=limit
            )
        )
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.get("/agent_teams/runs/{run_id}/stream")
@legacy_router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str,
    request: Request,
    username: str = Depends(_auth_dep),
    service: AgentTeamRunService = Depends(get_run_service),
):
    try:
        bus = await service.get_event_bus(username, run_id)
    except AgentTeamsServiceError as e:
        return _handle(e)

    async def generator():
        queue = bus.subscribe()
        try:
            for event in bus.history():
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=_SSE_HEARTBEAT_SECONDS
                    )
                    yield (
                        f"data: "
                        f"{json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                    )
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(generator(), media_type="text/event-stream")
