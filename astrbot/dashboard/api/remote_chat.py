"""Personal exposure management and dedicated-credential remote discovery."""

import asyncio
import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlencode
from uuid import UUID, uuid4

import anyio
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlmodel import select

from astrbot.builtin_stars.session_messages.tools import (
    AgentMessageSource,
    SessionMessageChain,
)
from astrbot.core.db.po import (
    DashboardAccount,
    RemoteChatAttachment,
    RemoteChatCredential,
)
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.subagent_progress import subagent_progress
from astrbot.core.tools.interactive_choice_registry import registry
from astrbot.core.utils.webchat_access import (
    DashboardPrincipal,
    dashboard_account_context,
    webchat_account_role,
    webchat_resource_owner,
)
from astrbot.dashboard.responses import ApiError, ok
from astrbot.dashboard.services.chat_service import (
    ChatServiceError,
    build_webchat_unified_msg_origin,
)
from astrbot.dashboard.services.remote_chat_control import RemoteChainControl

from .auth import AuthContext, require_dashboard_user
from .multipart import single_upload

router = APIRouter(tags=["Remote Chat"])


class RemoteTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(default="", max_length=1000000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)
    request_id: UUID = Field(strict=False)


class ConnectionTextRequest(RemoteTextRequest):
    revision: str


class ConnectionStopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: str


class RemoteCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=255, pattern=r"\S")
    project_id: str | None = Field(default=None, min_length=1, max_length=255)
    project_name: str | None = Field(default=None, min_length=1, max_length=512)
    selected_provider: str | None = Field(default=None, min_length=1)
    selected_model: str | None = Field(default=None, min_length=1)
    thinking_effort: str | None = Field(default=None, min_length=1)


class ConnectionCreateRequest(RemoteCreateRequest):
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


async def require_remote_account(request: Request):
    """Require a dedicated connection key without accepting ambient login state.

    Args:
        request: Remote request with an explicit RemoteSession authorization header.

    Yields:
        The key's enabled account within its trusted ownership context.

    Raises:
        ApiError: The dedicated credential is absent, revoked, or unavailable.
    """
    scheme, _, raw_key = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "remotesession" or not raw_key.strip():
        raise ApiError("Dedicated remote credential required", status_code=401)
    account = await request.app.state.services.remote_chat.authenticate(raw_key.strip())
    principal = DashboardPrincipal(
        account.account_id, account.resource_owner, account.role, account.username
    )
    tokens = (
        dashboard_account_context.set(principal),
        webchat_resource_owner.set(account.resource_owner),
        webchat_account_role.set(account.role),
    )
    try:
        yield account
    finally:
        dashboard_account_context.reset(tokens[0])
        webchat_resource_owner.reset(tokens[1])
        webchat_account_role.reset(tokens[2])


class AgentChannelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    chain_id: UUID = Field(strict=False)
    session_id: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=512)


class AgentChannelMessage(RemoteTextRequest):
    session_id: str = Field(min_length=1, max_length=255)


class AgentControlDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: UUID = Field(strict=False)
    decision: dict


class AgentControlPoll(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    results: list[AgentControlDecision] = Field(default_factory=list, max_length=128)


@router.post("/remote-chat/agent/channels")
async def open_agent_channel(
    payload: AgentChannelRequest,
    request: Request,
    account: DashboardAccount = Depends(require_remote_account),
):
    """Bind peer-asserted source labels to an authenticated private control channel.

    Args:
        payload: Peer server's source labels; no caller-selected local identity.
        request: Request authenticated with a dedicated remote credential.
        account: Recipient account determined solely by that credential.

    Returns:
        Private channel capability and the current host send limit.

    Raises:
        ApiError: Host policy or bounded coordination capacity denies a channel.
    """
    service = request.app.state.services.remote_chat
    limit = service.chat.require_session_message_limit()
    now = time.monotonic()
    for key, channel in list(service.agent_channels.items()):
        if channel["control"].expires < now:
            channel["control"].closed = True
            del service.agent_channels[key]
    if len(service.agent_channels) >= 1024:
        raise ApiError(
            "Automatic message chain coordination capacity reached", status_code=400
        )
    identity = (account.account_id, str(payload.chain_id))
    chain = service.agent_chains.get(identity)
    control = RemoteChainControl()
    if chain is None:
        if len(service.agent_chains) >= 1024:
            raise ApiError(
                "Automatic message chain coordination capacity reached", status_code=400
            )
        chain = SessionMessageChain(chain_id=str(payload.chain_id), control=control)
        service.agent_chains[identity] = chain
    channel_id = secrets.token_urlsafe(32)
    raw_key = request.headers["Authorization"].partition(" ")[2].strip()
    # Bind the mailbox to this exact credential, including after key replacement.
    digest = hashlib.sha256(raw_key.encode()).hexdigest()
    async with service.db.get_db() as connection:
        credential = (
            await connection.execute(
                select(RemoteChatCredential).where(
                    RemoteChatCredential.key_hash == digest
                )
            )
        ).scalar_one()
    service.agent_channels[channel_id] = {
        "account_id": account.account_id,
        "key_digest": digest,
        "chain": chain,
        "control": control,
        "source": {
            "kind": "agent",
            "account_id": "remote-peer",
            "login_name": "Remote Agent",
            "session_id": payload.session_id,
            "display_name": payload.display_name,
            "remote": True,
            "connection_name": credential.name,
            "instance_name": credential.name,
        },
    }
    return ok({"channel_id": channel_id, "limit": limit})


@router.post("/remote-chat/agent/channels/{channel_id}/poll")
async def poll_agent_channel(
    channel_id: str,
    payload: AgentControlPoll,
    request: Request,
    account: DashboardAccount = Depends(require_remote_account),
):
    """Exchange private budget decisions over the authenticated peer's outbound poll.

    Args:
        channel_id: Private capability issued to this exact dedicated credential.
        payload: Authority decisions, containing no resource grants or identities.
        request: Credential-authenticated request.
        account: Credential-bound recipient account.

    Returns:
        Pending budget requests and the local stop state.

    Raises:
        ApiError: The channel, credential or account is no longer available.
    """
    service = request.app.state.services.remote_chat
    channel = service.agent_channels.get(channel_id)
    digest = hashlib.sha256(
        request.headers["Authorization"].partition(" ")[2].strip().encode()
    ).hexdigest()
    if (
        channel is None
        or channel["account_id"] != account.account_id
        or channel["key_digest"] != digest
        or channel["control"].expires < time.monotonic()
    ):
        raise ApiError(
            "Automatic message chain control is unavailable", status_code=404
        )
    # Coordination carries no resource access. Pausing Agent sends still allows
    # already accepted execution to resolve pending control without a reset.
    control = channel["control"]
    pending = control.poll(
        [
            {"request_id": str(row.request_id), "decision": row.decision}
            for row in payload.results
        ]
    )
    if not pending:
        control.dirty.clear()
        try:
            await asyncio.wait_for(control.dirty.wait(), 1)
        except asyncio.TimeoutError:
            pass
        await service.authenticate(
            request.headers["Authorization"].partition(" ")[2].strip()
        )
        pending = list(control.pending.values())
    return ok({"requests": pending, "stopped": bool(channel["chain"].stopped)})


@router.post("/remote-chat/agent/channels/{channel_id}/messages")
async def send_remote_agent_message(
    channel_id: str,
    payload: AgentChannelMessage,
    request: Request,
    account: DashboardAccount = Depends(require_remote_account),
):
    """Admit authenticated peer Agent text using ordinary execution and history.

    Args:
        channel_id: Server-issued channel bound to this dedicated credential.
        payload: Text, target and receipt identity; no caller-selected Agent source.
        request: Credential-authenticated peer request.
        account: Local recipient identity resolved from the credential.

    Returns:
        Acceptance receipt; acceptance does not imply execution completion.

    Raises:
        ApiError: Live grant, channel, Agent policy or exposure is unavailable.
    """
    service = request.app.state.services.remote_chat
    channel = service.agent_channels.get(channel_id)
    raw_key = request.headers["Authorization"].partition(" ")[2].strip()
    digest = hashlib.sha256(raw_key.encode()).hexdigest()
    if (
        channel is None
        or channel["account_id"] != account.account_id
        or channel["key_digest"] != digest
        or channel["control"].expires < time.monotonic()
    ):
        raise ApiError(
            "Automatic message chain control is unavailable", status_code=404
        )
    await service.require_session(account, payload.session_id)
    service.chat.require_session_message_limit()
    identity = (account.account_id, payload.session_id, str(payload.request_id))
    digest = hashlib.sha256((channel_id + payload.text).encode()).hexdigest()
    receipt = service.receipts.get(identity)
    if receipt is not None and receipt["digest"] != digest:
        raise ApiError("Request identity already used", status_code=409)
    if receipt is None:
        if len(service.receipts) >= 1024:
            raise ApiError("Admission is busy", status_code=400)
        receipt = {"status": "processing", "digest": digest, "run_id": str(uuid4())}
        service.receipts[identity] = receipt
        stop_revision = service.chat.session_message_stop_revisions.get(
            payload.session_id, 0
        )
        revision = service.grant_revisions.get(account.account_id, 0)

        async def check_admission():
            """Revalidate the live grant after preparation and budget relay."""
            current = await service.authenticate(raw_key)
            await service.require_session(current, payload.session_id)
            service.require_enabled()
            service.chat.require_session_message_limit()
            if (
                channel["control"].closed
                or time.monotonic() - channel["control"].last_poll > 10
            ):
                raise ApiError(
                    "Automatic message chain control is unavailable", status_code=400
                )
            if service.grant_revisions.get(account.account_id, 0) != revision:
                raise ApiError("Remote grant changed during admission", status_code=403)

        async def admit():
            """Use ordinary Agent history, follow-up and queue admission."""
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
                    {"session_id": payload.session_id, "message": payload.text},
                    message_id=receipt["run_id"],
                    admission_check=check_admission,
                    agent_source=AgentMessageSource(
                        principal,
                        channel["source"]["session_id"],
                        channel["source"]["display_name"],
                        channel["chain"],
                        target_stop_revision=stop_revision,
                        remote_source=channel["source"],
                    ),
                )
                receipt["status"] = "accepted"
                await stream.aclose()
            except (ChatServiceError, ApiError) as exc:
                if receipt["status"] != "accepted":
                    receipt["status"] = "rejected"
                    receipt["error"] = (
                        str(exc)
                        if isinstance(exc, ChatServiceError)
                        else "Remote authorization or control changed"
                    )
            except Exception:
                if receipt["status"] != "accepted":
                    receipt["status"] = "unknown"
            finally:
                dashboard_account_context.reset(tokens[0])
                webchat_resource_owner.reset(tokens[1])
                webchat_account_role.reset(tokens[2])

        receipt["task"] = asyncio.create_task(admit())
    await asyncio.shield(receipt["task"])
    return ok(
        {key: receipt[key] for key in ("status", "run_id", "error") if key in receipt}
    )


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


@router.post("/remote-chat/sessions")
@router.post("/remote-chat/agent/sessions")
async def create_remote_chat_session(
    payload: RemoteCreateRequest,
    request: Request,
    account: DashboardAccount = Depends(require_remote_account),
):
    """Create only under the credential-bound account and host creation policy.

    Args:
        payload: Title, explicit remote project and ordinary model choices.
        request: Dedicated-auth request; the Agent route adds the Agent gate.
        account: Account resolved from the dedicated credential, never caller input.

    Returns:
        Created ordinary session metadata without credentials.

    Raises:
        ApiError: Host creation, live authorization or owned project validation fails.
    """
    result = await request.app.state.services.remote_chat.create_session(
        account,
        payload.model_dump(exclude_unset=True),
        request.headers["Authorization"].partition(" ")[2].strip(),
        agent="/agent/" in request.url.path,
    )
    return JSONResponse(ok(result), headers={"Cache-Control": "no-store"})


@router.get("/remote-chat/agent/sessions")
async def discover_remote_agent_sessions(
    request: Request, account: DashboardAccount = Depends(require_remote_account)
):
    service = request.app.state.services.remote_chat
    service.chat.require_session_message_limit()
    rows = await service.sessions(account)
    await service.authenticate(
        request.headers["Authorization"].partition(" ")[2].strip()
    )
    service.chat.require_session_message_limit()
    return ok([row for row in rows if not row["archived"]])


@router.get("/remote-chat/agent/sessions/{session_id}/history")
async def read_remote_agent_history(
    session_id: str,
    request: Request,
    before_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=50),
    account: DashboardAccount = Depends(require_remote_account),
):
    """Read exposed display history for a directly authorized Agent caller.

    Args:
        session_id: Ordinary session in the credential account's exposure scope.
        request: Dedicated-auth request, independent of ambient browser identity.
        before_id: Exclusive saved-message cursor, or None for the newest page.
        limit: Maximum saved records; the calling tool applies display budgets.
        account: Current account bound to the dedicated credential.

    Returns:
        Safe session metadata and saved display records for tool projection.

    Raises:
        ApiError: The credential, account, exposure or remote access is unavailable.
        ChatServiceError: Agent access is disabled or the session is unavailable.
    """
    service = request.app.state.services.remote_chat
    service.chat.require_session_message_limit()
    await service.require_session(account, session_id)
    metadata = await service.chat.get_session(
        account.resource_owner, session_id, limit if before_id is None else 1
    )
    page = (
        metadata
        if before_id is None
        else await service.chat.get_history_before(
            account.resource_owner, session_id, before_id, limit
        )
    )
    await service.authenticate(
        request.headers["Authorization"].partition(" ")[2].strip()
    )
    await service.require_session(account, session_id)
    service.chat.require_session_message_limit()
    target = await service.chat.require_owned_chat_session(
        account.resource_owner, session_id
    )
    project = metadata.get("project") or {}
    return ok(
        {
            "session_id": session_id,
            "display_name": target.display_name,
            "project_id": project.get("project_id"),
            "project_name": project.get("title"),
            "archived": bool(target.archived),
            "is_running": bool(
                metadata.get("is_running") or metadata.get("active_runs")
            ),
            "history": page["history"],
            "has_more": page["has_more"],
        }
    )


@router.get("/chat/remote-connections/{connection_id}/creation")
async def connection_creation_options(
    connection_id: str,
    request: Request,
    revision: str = Query(),
    _owner: str = Depends(require_dashboard_user),
):
    data = await request.app.state.services.remote_chat.read_connection(
        request.state.dashboard_account, connection_id, "creation", revision=revision
    )
    return JSONResponse(ok(data), headers={"Cache-Control": "no-store"})


@router.get("/remote-chat/creation")
@router.get("/remote-chat/agent/creation")
async def remote_creation_options(
    request: Request,
    account: DashboardAccount = Depends(require_remote_account),
    session_id: str | None = Query(default=None),
):
    """Offer owned remote projects only while host creation is allowed.

    Args:
        request: Dedicated-auth request; the Agent path adds the Agent gate.
        account: Account bound to the dedicated credential.
        session_id: Optional created session whose exposure must still be valid.

    Returns:
        Live host permission and owned project titles/IDs, including empty projects.

    Raises:
        ApiError: Live credential, account or optional session exposure fails.
        ChatServiceError: The Agent route is disabled by target instance policy.
    """
    service = request.app.state.services.remote_chat
    allowed = (
        service.config.get("chatui", {}).get("remote_session_creation_enabled") is True
    )
    projects = []
    if allowed:
        page = 1
        while True:
            rows = await service.db.get_chatui_projects_by_creator(
                account.resource_owner, page=page, page_size=100
            )
            projects.extend(
                {"project_id": row.project_id, "title": row.title} for row in rows
            )
            if len(rows) < 100:
                break
            page += 1
    await service.authenticate(
        request.headers["Authorization"].partition(" ")[2].strip()
    )
    if session_id is not None:
        await service.require_session(account, session_id)
    if "/agent/" in request.url.path:
        service.chat.require_session_message_limit()
    service.require_enabled()
    allowed = (
        service.config.get("chatui", {}).get("remote_session_creation_enabled") is True
    )
    return ok({"allowed": allowed, "projects": projects if allowed else []})


@router.post("/chat/remote-connections/{connection_id}/sessions")
async def create_connection_session(
    connection_id: str,
    payload: ConnectionCreateRequest,
    request: Request,
    _owner: str = Depends(require_dashboard_user),
):
    result = await request.app.state.services.remote_chat.read_connection(
        request.state.dashboard_account,
        connection_id,
        "sessions",
        method="POST",
        payload=payload.model_dump(exclude={"revision"}, exclude_unset=True),
        revision=payload.revision,
    )
    if not isinstance(result, dict) or not isinstance(result.get("session_id"), str):
        raise ApiError(
            "Invalid remote creation response",
            status_code=502,
            data={"code": "remote_acceptance_unknown"},
        )
    return JSONResponse(
        ok(
            {
                key: result.get(key)
                for key in (
                    "status",
                    "session_id",
                    "display_name",
                    "project_id",
                    "project_name",
                )
            }
        ),
        headers={"Cache-Control": "no-store"},
    )


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


class RemoteChoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    choice_id: str = Field(min_length=1, max_length=256)
    free_text: str = Field(default="", max_length=100000)


class ConnectionChoiceRequest(RemoteChoiceRequest):
    revision: str


@router.post("/remote-chat/sessions/{session_id}/attachments")
async def upload_remote_attachment(
    session_id: str,
    request: Request,
    account: DashboardAccount = Depends(require_remote_account),
):
    """Save an ordinary upload under a dedicated account and exposed session.

    Args:
        session_id: Target ordinary session, never a caller-selected owner.
        request: Multipart request authenticated by the dedicated transport.
        account: Trusted remote account with request-local ownership context.

    Returns:
        Ordinary attachment metadata after a final live grant check.

    Raises:
        ApiError: File validation or live account/session authorization fails.
    """
    service = request.app.state.services.remote_chat
    await service.require_session(account, session_id)
    upload = await single_upload(request)
    if upload is None:
        raise ApiError("Missing file", status_code=400)
    try:
        data = await service.chat.save_uploaded_file(upload)
    except ChatServiceError as exc:
        raise ApiError(str(exc), status_code=400) from None
    raw_key = request.headers["Authorization"].partition(" ")[2].strip()
    async with service.db.get_db() as connection:
        connection.add(
            RemoteChatAttachment(
                filename=data["filename"],
                attachment_id=data["attachment_id"],
                account_id=account.account_id,
                session_id=session_id,
                credential_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            )
        )
        await connection.commit()
    await service.check_grant(account, raw_key, session_id)
    return JSONResponse(ok(data), headers={"Cache-Control": "no-store"})


@router.get("/remote-chat/sessions/{session_id}/attachments/{attachment_id}")
async def download_remote_attachment(
    session_id: str,
    attachment_id: str,
    request: Request,
    account: DashboardAccount = Depends(require_remote_account),
):
    """Read bytes only after verifying attachment membership and live exposure.

    Args:
        session_id: Exposed session containing the attachment.
        attachment_id: Opaque ordinary attachment ID, never a path or URL.
        request: Dedicated authenticated request.
        account: Trusted remote account and ownership context.

    Returns:
        Uncached attachment bytes after reauthorization following file I/O.

    Raises:
        ApiError: The file or its account/session grant is unavailable.
    """
    service = request.app.state.services.remote_chat
    await service.require_session(account, session_id)
    raw_key = request.headers["Authorization"].partition(" ")[2].strip()
    await service.require_attachment(account, session_id, attachment_id, raw_key)
    try:
        path, mime = await service.chat.resolve_attachment_file(attachment_id)
        data = await asyncio.to_thread(Path(path).read_bytes)
    except (ChatServiceError, OSError):
        raise ApiError("Attachment is unavailable", status_code=404) from None
    await service.check_grant(account, raw_key, session_id)
    return Response(
        data,
        media_type=mime or "application/octet-stream",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/remote-chat/sessions/{session_id}/human-choices/{request_id}")
async def answer_remote_human_choice(
    session_id: str,
    request_id: str,
    payload: RemoteChoiceRequest,
    request: Request,
    account: DashboardAccount = Depends(require_remote_account),
):
    """Resolve a human answer on the explicit WebUI transport path.

    Args:
        session_id: Currently exposed ordinary session.
        request_id: Choice whose server-recorded origin must match the session.
        payload: Answer fields only; caller-supplied provenance is forbidden.
        request: Dedicated authenticated WebUI protocol request.
        account: Account resolved by the dedicated credential.

    Returns:
        Resolution metadata; the ordinary collector persists the answer.

    Raises:
        ApiError: Authorization changed, the origin differs, or the choice is
            missing, expired or already resolved. Agent delivery cannot use a
            message field to select this human path.
    """
    service = request.app.state.services.remote_chat
    await service.require_session(account, session_id)
    pending = registry._pending.get(request_id)
    if pending is None:
        raise ApiError("Interactive choice not found or expired", status_code=404)
    session = await service.db.get_platform_session_by_id(session_id)
    if pending.umo != build_webchat_unified_msg_origin(session):
        raise ApiError("Interactive choice is unavailable", status_code=404)
    raw_key = request.headers["Authorization"].partition(" ")[2].strip()
    await service.check_grant(account, raw_key, session_id)
    # Only this human WebUI protocol route resolves a choice. Ordinary message
    # and future Agent delivery routes cannot select their provenance via JSON.
    if pending.timeout_at < time.time() or not registry.resolve(
        request_id, payload.model_dump()
    ):
        raise ApiError("Already resolved or expired", status_code=409)
    if pending.sse_message_id:
        # Record the answer through the ordinary collector before the tool wakes.
        # Its legacy resolution broadcast carries only the reason, not the answer.
        webchat_queue_mgr.get_or_create_back_queue(
            pending.sse_message_id, session_id
        ).put_nowait(
            {
                "type": "interactive_choice_resolved",
                "data": {
                    "request_id": request_id,
                    "reason": "submitted",
                    "umo": pending.umo,
                    **payload.model_dump(),
                },
                "message_id": pending.sse_message_id,
            }
        )
    return ok({"request_id": request_id, "resolved_at": time.time()})


@router.post(
    "/chat/remote-connections/{connection_id}/sessions/{session_id}/attachments"
)
async def upload_connection_attachment(
    connection_id: str,
    session_id: str,
    request: Request,
    revision: str = Query(),
    _owner: str = Depends(require_dashboard_user),
):
    if session_id in {".", ".."} or "/" in session_id or "\\" in session_id:
        raise ApiError("Session is unavailable", status_code=404)
    async with request.form() as form:
        upload = form.get("file")
        if not hasattr(upload, "file") or upload.size > 512 * 1024 * 1024:
            raise ApiError(
                "Invalid upload or file too large (limit 512 MB)", status_code=400
            )
        data = await request.app.state.services.remote_chat.read_connection(
            request.state.dashboard_account,
            connection_id,
            "sessions/" + quote(session_id, safe="") + "/attachments",
            method="POST",
            revision=revision,
            files={"file": (upload.filename, upload.file, upload.content_type)},
        )
    return JSONResponse(ok(data), headers={"Cache-Control": "no-store"})


@router.get(
    "/chat/remote-connections/{connection_id}/sessions/{session_id}/attachments/{attachment_id}"
)
async def download_connection_attachment(
    connection_id: str,
    session_id: str,
    attachment_id: str,
    request: Request,
    revision: str = Query(),
    _owner: str = Depends(require_dashboard_user),
):
    for value in (session_id, attachment_id):
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ApiError("Attachment is unavailable", status_code=404)
    data, mime = await request.app.state.services.remote_chat.read_connection(
        request.state.dashboard_account,
        connection_id,
        "sessions/"
        + quote(session_id, safe="")
        + "/attachments/"
        + quote(attachment_id, safe=""),
        revision=revision,
        binary=True,
    )
    # Never relay remote headers or render active content from attachment origins.
    safe_mime = (
        mime
        if mime
        in {
            "image/png",
            "image/jpeg",
            "image/gif",
            "image/webp",
            "image/bmp",
            "image/x-icon",
            "image/vnd.microsoft.icon",
        }
        else "application/octet-stream"
    )
    return Response(
        data,
        media_type=safe_mime,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post(
    "/chat/remote-connections/{connection_id}/sessions/{session_id}/human-choices/{request_id}"
)
async def answer_connection_human_choice(
    connection_id: str,
    session_id: str,
    request_id: str,
    payload: ConnectionChoiceRequest,
    request: Request,
    _owner: str = Depends(require_dashboard_user),
):
    for value in (session_id, request_id):
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ApiError("Interactive choice is unavailable", status_code=404)
    return ok(
        await request.app.state.services.remote_chat.read_connection(
            request.state.dashboard_account,
            connection_id,
            "sessions/"
            + quote(session_id, safe="")
            + "/human-choices/"
            + quote(request_id, safe=""),
            method="POST",
            revision=payload.revision,
            payload=payload.model_dump(exclude={"revision"}),
        )
    )


@router.post("/remote-chat/sessions/{session_id}/messages")
async def send_remote_text(
    session_id: str,
    payload: RemoteTextRequest,
    request: Request,
    account: DashboardAccount = Depends(require_remote_account),
):
    service = request.app.state.services.remote_chat
    await service.require_session(account, session_id)
    if not payload.text.strip() and not payload.attachment_ids:
        raise ApiError("Text is empty", status_code=400)
    identity = (account.account_id, session_id, str(payload.request_id))
    digest = hashlib.sha256(
        json.dumps([payload.text, payload.attachment_ids]).encode()
    ).hexdigest()
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
            await service.check_grant(account, raw_key, session_id)

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
                parts = (
                    [{"type": "plain", "text": payload.text}] if payload.text else []
                )
                for attachment_id in payload.attachment_ids:
                    attachment, filename = await service.require_attachment(
                        account, session_id, attachment_id, raw_key
                    )
                    parts.append(
                        {
                            "type": attachment.type,
                            "attachment_id": attachment_id,
                            "filename": filename,
                        }
                    )
                stream = await service.chat.build_chat_stream(
                    account.resource_owner,
                    {"session_id": session_id, "message": parts},
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


@router.get("/remote-chat/sessions/{session_id}/file-diff")
async def remote_file_diff(
    session_id: str,
    request: Request,
    path: str = Query(max_length=32768),
    message_id: int | None = Query(default=None, ge=1),
    run_id: str | None = Query(default=None, max_length=128),
    account: DashboardAccount = Depends(require_remote_account),
):
    """Read only a file diff published by this exposed conversation.

    Args:
        session_id: Exposed parent conversation.
        request: Current request carrying the dedicated credential.
        path: Exact file path in a server-produced change card.
        message_id: Persisted bot result containing the card.
        run_id: Active run containing the card.
        account: Credential owner.

    Returns:
        Authorized diff and optional baseline text.
    """
    from astrbot.dashboard.schemas import ChatFileChangeDiffRequest

    from .chat import chat_file_change_diff

    service = request.app.state.services.remote_chat
    await service.require_session(account, session_id)
    source = ChatFileChangeDiffRequest(
        session_id=session_id, message_id=message_id, run_id=run_id, path=path
    )
    try:
        entries = await service.chat.require_file_change_source(
            account.resource_owner, source, [{"path": path}]
        )
    except (ChatServiceError, OSError, ValueError) as exc:
        raise ApiError("File change is unavailable", status_code=404) from exc
    source.backup_id = entries[0].get("backup_id") or ""
    source.expect_sha256 = entries[0].get("sha256") or ""
    result = await chat_file_change_diff(
        source,
        AuthContext(username=account.resource_owner, scopes=["chat"]),
        service.chat,
    )
    await service.check_grant(
        account, request.headers["Authorization"].partition(" ")[2].strip(), session_id
    )
    return result


@router.get(
    "/remote-chat/sessions/{session_id}/shell-sessions/{shell_session_id}/output"
)
async def remote_shell_output(
    session_id: str,
    shell_session_id: str,
    request: Request,
    cursor: int = Query(default=0, ge=0),
    account: DashboardAccount = Depends(require_remote_account),
):
    """Peek at output without consuming the remote agent's shell cursor.

    Args:
        session_id: Exposed conversation owning the shell process.
        shell_session_id: Managed process identifier.
        request: Current request carrying the dedicated credential.
        cursor: Browser's independent output cursor.
        account: Credential owner.

    Returns:
        Bounded shell output and its next cursor.
    """
    service = request.app.state.services.remote_chat
    await service.require_session(account, session_id)
    try:
        data = await service.chat.get_shell_session_output(
            account.resource_owner,
            session_id,
            shell_session_id,
            cursor=cursor,
            yield_time_ms=1000,
        )
    except ChatServiceError as exc:
        raise ApiError("Shell output is unavailable", status_code=404) from exc
    await service.check_grant(
        account, request.headers["Authorization"].partition(" ")[2].strip(), session_id
    )
    return ok(data)


@router.get("/chat/remote-connections/{connection_id}/sessions/{session_id}/file-diff")
async def connection_file_diff(
    connection_id: str,
    session_id: str,
    request: Request,
    revision: str = Query(),
    path: str = Query(max_length=32768),
    message_id: int | None = Query(default=None, ge=1),
    run_id: str | None = Query(default=None, max_length=128),
    _owner: str = Depends(require_dashboard_user),
):
    if session_id in {".", ".."} or "/" in session_id or "\\" in session_id:
        raise ApiError("Session is unavailable", status_code=404)
    query = {"path": path}
    if message_id is not None:
        query["message_id"] = str(message_id)
    if run_id:
        query["run_id"] = run_id
    data = await request.app.state.services.remote_chat.read_connection(
        request.state.dashboard_account,
        connection_id,
        "sessions/" + quote(session_id, safe="") + "/file-diff?" + urlencode(query),
        revision=revision,
    )
    return JSONResponse(ok(data), headers={"Cache-Control": "no-store"})


@router.get(
    "/chat/remote-connections/{connection_id}/sessions/{session_id}/shell-sessions/{shell_session_id}/output"
)
async def connection_shell_output(
    connection_id: str,
    session_id: str,
    shell_session_id: str,
    request: Request,
    revision: str = Query(),
    cursor: int = Query(default=0, ge=0),
    _owner: str = Depends(require_dashboard_user),
):
    if any(
        value in {".", ".."} or "/" in value or "\\" in value
        for value in (session_id, shell_session_id)
    ):
        raise ApiError("Session is unavailable", status_code=404)
    data = await request.app.state.services.remote_chat.read_connection(
        request.state.dashboard_account,
        connection_id,
        "sessions/"
        + quote(session_id, safe="")
        + "/shell-sessions/"
        + quote(shell_session_id, safe="")
        + "/output?cursor="
        + str(cursor),
        revision=revision,
    )
    return JSONResponse(ok(data), headers={"Cache-Control": "no-store"})


@router.get("/remote-chat/sessions/{session_id}/subagent-runs/events")
async def remote_subagent_events(
    session_id: str,
    request: Request,
    run_id: str | None = Query(default=None, max_length=128),
    account: DashboardAccount = Depends(require_remote_account),
):
    """Stream the selected exposed session's invocation list or execution trace.

    Args:
        session_id: Exposed parent session, never a caller-supplied child session.
        request: Authenticated remote transport.
        run_id: Invocation belonging to the parent, or None for navigation metadata.
        account: Account resolved from the dedicated connection credential.

    Returns:
        Ordinary subagent events with continuous grant validation.

    Raises:
        ApiError: The session or invocation is unavailable.
    """
    service = request.app.state.services.remote_chat
    raw_key = request.headers["Authorization"].partition(" ")[2].strip()
    await service.require_session(account, session_id)
    if run_id and await subagent_progress.get_run(session_id, run_id) is None:
        raise ApiError("Invocation is unavailable", status_code=404)
    await service.check_grant(account, raw_key, session_id)

    async def events():
        stream = subagent_progress.stream(session_id, run_id)
        pending = None
        try:
            pending = asyncio.create_task(anext(stream))
            while True:
                await asyncio.wait({pending}, timeout=0.25)
                with anyio.CancelScope(shield=True):
                    await service.check_grant(account, raw_key, session_id)
                if pending.done():
                    try:
                        payload = pending.result()
                    except StopAsyncIteration:
                        return
                    yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                    pending = asyncio.create_task(anext(stream))
                else:
                    yield ": heartbeat\n\n"
        except ApiError:
            yield 'data: {"type":"access_error","code":"remote_unavailable"}\n\n'
        finally:
            with anyio.CancelScope(shield=True):
                if pending is not None:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                await stream.aclose()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/remote-chat/sessions/{session_id}/subagent-runs/{run_id}/configuration")
async def remote_subagent_configuration(
    session_id: str,
    run_id: str,
    request: Request,
    account: DashboardAccount = Depends(require_remote_account),
):
    """Read a credential-free invocation configuration within an exposed parent.

    Args:
        session_id: Exposed ordinary parent session.
        run_id: Invocation whose configuration is requested.
        request: Dedicated remote transport.
        account: Authenticated remote account.

    Returns:
        The ordinary sanitized configuration snapshot.

    Raises:
        ApiError: Parent access or invocation membership is missing.
    """
    service = request.app.state.services.remote_chat
    await service.require_session(account, session_id)
    configuration = await subagent_progress.get_configuration(session_id, run_id)
    if configuration is None:
        raise ApiError("Invocation is unavailable", status_code=404)
    await service.check_grant(
        account, request.headers["Authorization"].partition(" ")[2].strip(), session_id
    )
    return ok(configuration)


@router.get(
    "/chat/remote-connections/{connection_id}/sessions/{session_id}/subagent-runs/events"
)
async def connection_subagent_events(
    connection_id: str,
    session_id: str,
    request: Request,
    revision: str = Query(),
    run_id: str | None = Query(default=None, max_length=128),
    _owner: str = Depends(require_dashboard_user),
):
    if session_id in {".", ".."} or "/" in session_id or "\\" in session_id:
        raise ApiError("Session is unavailable", status_code=404)
    path = "sessions/" + quote(session_id, safe="") + "/subagent-runs/events"
    if run_id:
        path += "?run_id=" + quote(run_id, safe="")
    stream = await request.app.state.services.remote_chat.read_connection(
        request.state.dashboard_account,
        connection_id,
        path,
        revision=revision,
        stream=True,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/chat/remote-connections/{connection_id}/sessions/{session_id}/subagent-runs/{run_id}/configuration"
)
async def connection_subagent_configuration(
    connection_id: str,
    session_id: str,
    run_id: str,
    request: Request,
    revision: str = Query(),
    _owner: str = Depends(require_dashboard_user),
):
    for value in (session_id, run_id):
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ApiError("Invocation is unavailable", status_code=404)
    return ok(
        await request.app.state.services.remote_chat.read_connection(
            request.state.dashboard_account,
            connection_id,
            "sessions/"
            + quote(session_id, safe="")
            + "/subagent-runs/"
            + quote(run_id, safe="")
            + "/configuration",
            revision=revision,
        )
    )


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

        compression = None
        context_limit = 0
        provider_refresh = 0.0

        async def observe(run_id):
            """Wake the session snapshot subscriber using ordinary run events."""
            nonlocal compression
            try:
                stream = await service.chat.build_chat_run_stream(
                    account.resource_owner, run_id
                )
                try:
                    async for chunk in stream:
                        for line in chunk.splitlines():
                            if not line.startswith("data: "):
                                continue
                            try:
                                event = json.loads(line[6:])
                            except (ValueError, TypeError):
                                continue
                            if event.get("type") == "context_compression":
                                compression = {
                                    "run_id": run_id,
                                    "notice": event.get("data"),
                                }
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
                        for field in (
                            "history",
                            "has_more",
                            "active_runs",
                            "is_running",
                        )
                    }
                    if time.monotonic() - provider_refresh > 5:
                        from astrbot.core.utils.llm_metadata import LLM_METADATAS

                        context_limit = 0
                        try:
                            selection = await service.chat.session_selection(
                                current.resource_owner, session_id
                            )
                            providers = (
                                request.app.state.services.providers.list_providers(
                                    capability="chat_completion", enabled=True
                                )["providers"]
                            )
                            provider = next(
                                (
                                    item
                                    for item in providers
                                    if item["id"] == selection["selected_provider"]
                                ),
                                providers[0] if providers else {},
                            )
                            metadata = LLM_METADATAS.get(
                                selection["selected_model"] or provider.get("model"), {}
                            )
                            context_limit = (
                                provider.get("max_context_tokens")
                                or metadata.get("limit", {}).get("context")
                                or 0
                            )
                        except ChatServiceError:
                            pass
                        provider_refresh = time.monotonic()
                    data["context_limit"] = context_limit
                    data["context_compression"] = compression
                    data["goal"] = (
                        await service.chat.get_session_goal(
                            current.resource_owner, session_id
                        )
                    )["goal"]
                    data[
                        "shell_sessions"
                    ] = await service.chat.get_session_shell_sessions(
                        current.resource_owner, session_id
                    )
                    await service.check_grant(account, raw_key, session_id)
                # Only explicitly scoped remote resources may become interactive locally.
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
        payload={
            "text": payload.text,
            "request_id": str(payload.request_id),
            "attachment_ids": payload.attachment_ids,
        },
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
