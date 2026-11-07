"""Personal exposure management and dedicated-credential remote discovery."""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from astrbot.core.db.po import DashboardAccount
from astrbot.dashboard.responses import ApiError, ok

from .auth import require_dashboard_user

router = APIRouter(tags=["Remote Chat"])


class RemoteExposureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["all", "selected"]
    session_ids: list[str] = Field(default_factory=list, max_length=10000)


class RemoteCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=128)


class RemoteEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool


async def require_remote_account(request: Request) -> DashboardAccount:
    """Require a dedicated connection key without accepting ambient login state.

    Args:
        request: Remote request with an explicit RemoteSession authorization header.

    Returns:
        The key's currently enabled account, never a caller-selected owner.

    Raises:
        ApiError: The dedicated credential is absent, revoked, or unavailable.
    """
    scheme, _, raw_key = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "remotesession" or not raw_key.strip():
        raise ApiError("Dedicated remote credential required", status_code=401)
    return await request.app.state.services.remote_chat.authenticate(raw_key.strip())


@router.get("/chat/remote-access")
async def get_remote_chat_access(
    request: Request, _owner: str = Depends(require_dashboard_user)
):
    return ok(
        await request.app.state.services.remote_chat.access(
            request.state.dashboard_account
        )
    )


@router.put("/chat/remote-access/exposure")
async def set_remote_chat_exposure(
    request: Request,
    payload: RemoteExposureRequest,
    _owner: str = Depends(require_dashboard_user),
):
    await request.app.state.services.remote_chat.set_exposure(
        request.state.dashboard_account, payload.mode, payload.session_ids
    )
    return ok()


@router.post("/chat/remote-access/credentials")
async def create_remote_chat_credential(
    request: Request,
    payload: RemoteCredentialRequest,
    _owner: str = Depends(require_dashboard_user),
):
    name = payload.name.strip()
    if not name:
        raise ApiError("Credential name is required", status_code=400)
    result = await request.app.state.services.remote_chat.create_credential(
        request.state.dashboard_account, name
    )
    return JSONResponse(ok(result), headers={"Cache-Control": "no-store"})


@router.delete("/chat/remote-access/credentials/{credential_id}")
async def revoke_remote_chat_credential(
    credential_id: str,
    request: Request,
    _owner: str = Depends(require_dashboard_user),
):
    await request.app.state.services.remote_chat.revoke_credential(
        request.state.dashboard_account, credential_id
    )
    return ok()


@router.put("/chat/remote-access/enabled")
async def set_remote_chat_enabled(
    request: Request,
    payload: RemoteEnabledRequest,
    _owner: str = Depends(require_dashboard_user),
):
    if request.state.dashboard_account.role != "admin":
        raise ApiError("Administrator permission required", status_code=403)
    config = request.app.state.services.remote_chat.config
    config.setdefault("chatui", {})["remote_sessions_enabled"] = payload.enabled
    config.save_config()
    return ok({"enabled": payload.enabled})


@router.get("/remote-chat/sessions")
async def discover_remote_chat_sessions(
    request: Request, account: DashboardAccount = Depends(require_remote_account)
):
    return ok(await request.app.state.services.remote_chat.sessions(account))


@router.get("/remote-chat/projects")
async def discover_remote_chat_projects(
    request: Request, account: DashboardAccount = Depends(require_remote_account)
):
    sessions = await request.app.state.services.remote_chat.sessions(account)
    projects = {}
    for item in sessions:
        if item["project_id"]:
            projects[item["project_id"]] = {
                "project_id": item["project_id"],
                "title": item["project_name"],
                "emoji": item["project_emoji"],
            }
    return ok(list(projects.values()))


@router.get("/remote-chat/projects/{project_id}/sessions")
async def discover_remote_chat_project_sessions(
    project_id: str,
    request: Request,
    account: DashboardAccount = Depends(require_remote_account),
):
    sessions = [
        item
        for item in await request.app.state.services.remote_chat.sessions(account)
        if item["project_id"] == project_id
    ]
    if not sessions:
        raise ApiError("Project is unavailable", status_code=404)
    return ok(sessions)


@router.get("/remote-chat/sessions/{session_id}/history")
async def read_remote_chat_history(
    session_id: str,
    request: Request,
    before_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    account: DashboardAccount = Depends(require_remote_account),
):
    service = request.app.state.services.remote_chat
    await service.require_session(account, session_id)
    if before_id is None:
        result = await service.chat.get_session(
            account.resource_owner, session_id, limit
        )
    else:
        result = await service.chat.get_history_before(
            account.resource_owner, session_id, before_id, limit
        )
    # Only history belongs to this grant. Associated thread, run and project
    # objects are intentionally not capabilities of the remote read surface.
    history = result["history"]
    return ok(
        {
            "history": history,
            "has_more": result["has_more"],
            "next_before_id": history[0]["id"] if history else None,
        }
    )
