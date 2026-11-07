"""Personal exposure management and dedicated-credential remote discovery."""

import asyncio
import hashlib
import json
from typing import Literal
from urllib.parse import quote
from uuid import UUID, uuid4

import anyio
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from astrbot.core.db.po import DashboardAccount
from astrbot.core.utils.webchat_access import (
    DashboardPrincipal,
    dashboard_account_context,
    webchat_account_role,
    webchat_resource_owner,
)
from astrbot.dashboard.responses import ApiError, ok
from astrbot.dashboard.services.chat_service import ChatServiceError

from .auth import require_dashboard_user

router = APIRouter(tags=["Remote Chat"])


class RemoteTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=1000000)
    request_id: UUID = Field(strict=False)


class ConnectionTextRequest(RemoteTextRequest):
    revision: str


class ConnectionStopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: str


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


class RemoteConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=2048)
    key: SecretStr | None = Field(default=None, max_length=512)


@router.post("/remote-chat/sessions/{session_id}/messages")
async def send_remote_text(
    session_id: str,
    payload: RemoteTextRequest,
    request: Request,
    account: DashboardAccount = Depends(require_remote_account),
):
    service = request.app.state.services.remote_chat
    await service.require_session(account, session_id)
    if not payload.text.strip():
        raise ApiError("Text is empty", status_code=400)
    identity = (account.account_id, session_id, str(payload.request_id))
    digest = hashlib.sha256(payload.text.encode()).hexdigest()
    receipt = service.receipts.get(identity)
    if receipt is not None and receipt["digest"] != digest:
        raise ApiError("Request identity already used", status_code=409)
    if receipt is None:
        if len(service.receipts) >= 1024:
            completed = next(
                (
                    key
                    for key, value in service.receipts.items()
                    if value["status"] != "processing"
                ),
                None,
            )
            if completed is None:
                raise ApiError("Admission is busy", status_code=400)
            del service.receipts[completed]
        receipt = {
            "status": "processing",
            "digest": digest,
            "run_id": str(uuid4()),
        }
        service.receipts[identity] = receipt
        raw_key = request.headers["Authorization"].partition(" ")[2].strip()

        async def check_admission():
            """Recheck the dedicated grant immediately before queue admission."""
            revision = service.grant_revisions.get(account.account_id, 0)
            current = await service.authenticate(raw_key)
            await service.require_session(current, session_id)
            # No await may separate these final invalidation checks from enqueue.
            service.require_enabled()
            if service.grant_revisions.get(account.account_id, 0) != revision:
                raise ApiError("Remote grant changed during admission", status_code=403)

        async def admit():
            """Finish one ordinary admission even if its HTTP response is lost."""
            principal = DashboardPrincipal(
                account.account_id,
                account.resource_owner,
                account.role,
                account.username,
            )
            tokens = (
                dashboard_account_context.set(principal),
                webchat_resource_owner.set(account.resource_owner),
                webchat_account_role.set(account.role),
            )
            try:
                stream = await service.chat.build_chat_stream(
                    account.resource_owner,
                    {"session_id": session_id, "message": payload.text},
                    message_id=receipt["run_id"],
                    admission_check=check_admission,
                )
                receipt["status"] = "accepted"
                try:
                    await anext(stream)
                finally:
                    await stream.aclose()
            except (ChatServiceError, ApiError):
                if receipt["status"] != "accepted":
                    receipt["status"] = "rejected"
            except Exception:
                # Unexpected failures cannot prove whether admission completed.
                if receipt["status"] != "accepted":
                    receipt["status"] = "unknown"
            finally:
                dashboard_account_context.reset(tokens[0])
                webchat_resource_owner.reset(tokens[1])
                webchat_account_role.reset(tokens[2])

        receipt["task"] = asyncio.create_task(admit())
    await asyncio.shield(receipt["task"])
    return ok({"status": receipt["status"], "run_id": receipt["run_id"]})


@router.get("/remote-chat/sessions/{session_id}/receipts/{request_id}")
async def remote_admission_receipt(
    session_id: str,
    request_id: UUID,
    request: Request,
    account: DashboardAccount = Depends(require_remote_account),
):
    service = request.app.state.services.remote_chat
    await service.require_session(account, session_id)
    receipt = service.receipts.get((account.account_id, session_id, str(request_id)))
    return ok({"status": receipt["status"] if receipt else "unknown"})


@router.post("/remote-chat/sessions/{session_id}/stop")
async def stop_remote_session(
    session_id: str,
    request: Request,
    account: DashboardAccount = Depends(require_remote_account),
):
    service = request.app.state.services.remote_chat
    await service.require_session(account, session_id)
    return ok(await service.chat.stop_session(account.resource_owner, session_id))


@router.get("/remote-chat/sessions/{session_id}/events")
async def remote_session_events(
    session_id: str,
    request: Request,
    account: DashboardAccount = Depends(require_remote_account),
):
    service = request.app.state.services.remote_chat
    await service.require_session(account, session_id)
    raw_key = request.headers["Authorization"].partition(" ")[2].strip()

    async def events():
        subscriptions = {}
        dirty = asyncio.Event()

        async def observe(run_id):
            """Wake the session snapshot subscriber using ordinary run events."""
            try:
                stream = await service.chat.build_chat_run_stream(
                    account.resource_owner, run_id
                )
                try:
                    async for _ in stream:
                        dirty.set()
                finally:
                    await stream.aclose()
            except ChatServiceError:
                pass
            finally:
                dirty.set()

        try:
            previous = None
            while True:
                # Finish database cleanup before a disconnected observer exits.
                with anyio.CancelScope(shield=True):
                    current = await service.authenticate(raw_key)
                    await service.require_session(current, session_id)
                    state = await service.chat.get_session(
                        current.resource_owner, session_id, 50
                    )
                data = {
                    field: state[field]
                    for field in ("history", "has_more", "active_runs", "is_running")
                }
                # Never relay local thread, project or interactive capabilities.
                encoded = json.dumps(
                    {"type": "snapshot", "data": data}, ensure_ascii=False
                )
                if encoded != previous:
                    yield "data: " + encoded + "\n\n"
                    previous = encoded
                else:
                    yield ": heartbeat\n\n"
                active_ids = {run["run_id"] for run in state["active_runs"]}
                for run_id in list(subscriptions):
                    if run_id not in active_ids and subscriptions[run_id].done():
                        del subscriptions[run_id]
                for run in state["active_runs"]:
                    if run["run_id"] not in subscriptions:
                        subscriptions[run["run_id"]] = asyncio.create_task(
                            observe(run["run_id"])
                        )
                dirty.clear()
                # Check access even when no output arrives; discover runs started
                # by the remote browser or another direct binding as well.
                try:
                    await asyncio.wait_for(dirty.wait(), timeout=0.25)
                except asyncio.TimeoutError:
                    pass
                await asyncio.sleep(0.05)
        except (ApiError, ChatServiceError) as exc:
            code = (
                "remote_unauthorized"
                if isinstance(exc, ApiError) and exc.status_code == 401
                else "remote_unavailable"
            )
            yield "data: " + json.dumps({"type": "access_error", "code": code}) + "\n\n"
        finally:
            for task in subscriptions.values():
                task.cancel()
            await asyncio.gather(*subscriptions.values(), return_exceptions=True)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/chat/remote-connections/{connection_id}/sessions/{session_id}/receipts/{request_id}"
)
async def connection_admission_receipt(
    connection_id: str,
    session_id: str,
    request_id: UUID,
    request: Request,
    revision: str = Query(),
    _owner: str = Depends(require_dashboard_user),
):
    if session_id in {".", ".."} or "/" in session_id or "\\" in session_id:
        raise ApiError("Session is unavailable", status_code=404)
    data = await request.app.state.services.remote_chat.read_connection(
        request.state.dashboard_account,
        connection_id,
        "sessions/" + quote(session_id, safe="") + "/receipts/" + str(request_id),
        revision=revision,
    )
    if not isinstance(data, dict) or data.get("status") not in {
        "accepted",
        "rejected",
        "processing",
        "unknown",
    }:
        raise ApiError(
            "Invalid remote receipt",
            status_code=502,
            data={"code": "remote_protocol_error"},
        )
    return JSONResponse(
        ok({"status": data["status"]}), headers={"Cache-Control": "no-store"}
    )


@router.api_route(
    "/chat/remote-connections/{connection_id}/sessions/{session_id}/messages",
    methods=["POST"],
)
async def send_connection_text(
    connection_id: str,
    session_id: str,
    payload: ConnectionTextRequest,
    request: Request,
    _owner: str = Depends(require_dashboard_user),
):
    if session_id in {".", ".."} or "/" in session_id or "\\" in session_id:
        raise ApiError("Session is unavailable", status_code=404)
    data = await request.app.state.services.remote_chat.read_connection(
        request.state.dashboard_account,
        connection_id,
        "sessions/" + quote(session_id, safe="") + "/messages",
        method="POST",
        payload={"text": payload.text, "request_id": str(payload.request_id)},
        revision=payload.revision,
    )
    if (
        not isinstance(data, dict)
        or data.get("status") not in {"accepted", "rejected", "unknown", "processing"}
        or not isinstance(data.get("run_id"), str)
    ):
        raise ApiError(
            "Acceptance could not be confirmed",
            status_code=502,
            data={"code": "remote_acceptance_unknown"},
        )
    return JSONResponse(
        ok({"status": data["status"], "run_id": data["run_id"]}),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/chat/remote-connections/{connection_id}/sessions/{session_id}/stop")
async def stop_connection_session(
    connection_id: str,
    session_id: str,
    payload: ConnectionStopRequest,
    request: Request,
    _owner: str = Depends(require_dashboard_user),
):
    if session_id in {".", ".."} or "/" in session_id or "\\" in session_id:
        raise ApiError("Session is unavailable", status_code=404)
    return ok(
        await request.app.state.services.remote_chat.read_connection(
            request.state.dashboard_account,
            connection_id,
            "sessions/" + quote(session_id, safe="") + "/stop",
            method="POST",
            revision=payload.revision,
        )
    )


@router.get("/chat/remote-connections/{connection_id}/sessions/{session_id}/events")
async def connection_session_events(
    connection_id: str,
    session_id: str,
    request: Request,
    revision: str = Query(),
    _owner: str = Depends(require_dashboard_user),
):
    if session_id in {".", ".."} or "/" in session_id or "\\" in session_id:
        raise ApiError("Session is unavailable", status_code=404)
    stream = await request.app.state.services.remote_chat.read_connection(
        request.state.dashboard_account,
        connection_id,
        "sessions/" + quote(session_id, safe="") + "/events",
        revision=revision,
        stream=True,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/remote-connections")
async def list_remote_connections(
    request: Request, _owner: str = Depends(require_dashboard_user)
):
    return JSONResponse(
        ok(
            await request.app.state.services.remote_chat.connections(
                request.state.dashboard_account
            )
        ),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/chat/remote-connections")
async def create_remote_connection(
    request: Request,
    payload: RemoteConnectionRequest,
    _owner: str = Depends(require_dashboard_user),
):
    identity = await request.app.state.services.remote_chat.save_connection(
        request.state.dashboard_account,
        payload.name,
        payload.base_url,
        payload.key.get_secret_value() if payload.key is not None else None,
    )
    return ok({"connection_id": identity})


@router.put("/chat/remote-connections/{connection_id}")
async def update_remote_connection(
    connection_id: str,
    request: Request,
    payload: RemoteConnectionRequest,
    _owner: str = Depends(require_dashboard_user),
):
    await request.app.state.services.remote_chat.save_connection(
        request.state.dashboard_account,
        payload.name,
        payload.base_url,
        payload.key.get_secret_value() if payload.key is not None else None,
        connection_id,
    )
    return ok()


@router.delete("/chat/remote-connections/{connection_id}")
async def delete_remote_connection(
    connection_id: str, request: Request, _owner: str = Depends(require_dashboard_user)
):
    await request.app.state.services.remote_chat.delete_connection(
        request.state.dashboard_account, connection_id
    )
    return ok()


@router.get("/chat/remote-connections/{connection_id}/sessions")
async def list_connection_sessions(
    connection_id: str, request: Request, _owner: str = Depends(require_dashboard_user)
):
    data = await request.app.state.services.remote_chat.read_connection(
        request.state.dashboard_account, connection_id, "sessions"
    )
    # Do not turn arbitrary server responses into additional local capabilities.
    if not isinstance(data, list) or any(
        not isinstance(row, dict)
        or not isinstance(row.get("session_id"), str)
        or any(
            row.get(field) is not None and not isinstance(row.get(field), str)
            for field in ("display_name", "project_id", "project_name", "project_emoji")
        )
        or not isinstance(row.get("archived"), bool)
        for row in data
    ):
        raise ApiError(
            "Remote server does not support the connection protocol",
            status_code=502,
            data={"code": "remote_protocol_error"},
        )
    return JSONResponse(
        ok(
            [
                {
                    field: row.get(field)
                    for field in (
                        "session_id",
                        "display_name",
                        "archived",
                        "project_id",
                        "project_name",
                        "project_emoji",
                    )
                }
                for row in data
            ]
        ),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/chat/remote-connections/{connection_id}/sessions/{session_id}/history")
async def read_connection_history(
    connection_id: str,
    session_id: str,
    request: Request,
    before_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    _owner: str = Depends(require_dashboard_user),
):
    if session_id in {".", ".."} or "/" in session_id or "\\" in session_id:
        raise ApiError("Session is unavailable", status_code=404)
    data = await request.app.state.services.remote_chat.read_connection(
        request.state.dashboard_account,
        connection_id,
        "sessions/" + quote(session_id, safe="") + "/history",
        {"limit": limit, **({"before_id": before_id} if before_id is not None else {})},
    )
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("history"), list)
        or not isinstance(data.get("has_more"), bool)
        or any(
            not isinstance(row, dict)
            or not isinstance(row.get("content"), dict)
            or not isinstance(row.get("id"), int)
            for row in data.get("history", [])
        )
        or (
            data.get("has_more")
            and (
                not isinstance(data.get("next_before_id"), int)
                or data["next_before_id"] < 1
                or (before_id is not None and data["next_before_id"] >= before_id)
            )
        )
    ):
        raise ApiError(
            "Remote server does not support the connection protocol",
            status_code=502,
            data={"code": "remote_protocol_error"},
        )
    return JSONResponse(
        ok(
            {
                field: data.get(field)
                for field in ("history", "has_more", "next_before_id")
            }
        ),
        headers={"Cache-Control": "no-store"},
    )
