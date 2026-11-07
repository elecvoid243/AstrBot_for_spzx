from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from astrbot import logger
from astrbot.core.computer.computer_client import (
    shutdown_local_shell_sessions_for_owner,
)
from astrbot.core.subagent_manager import SubAgentManager
from astrbot.core.subagent_progress import subagent_progress
from astrbot.core.tools import fs_access
from astrbot.core.tools.computer_tools.edit_history import (
    get_history_manager,
    render_unified_diff,
)
from astrbot.core.tools.computer_tools.recycle_restore import restore_from_recycle_bin
from astrbot.core.tools.interactive_choice_registry import (
    push_resolved_event_to_back_queue,
    registry,
)
from astrbot.core.utils.astrbot_path import (
    get_astrbot_system_tmp_path,
    get_astrbot_temp_path,
    get_astrbot_workspaces_path,
)
from astrbot.dashboard.async_utils import run_maybe_async
from astrbot.dashboard.responses import ApiError, error, ok
from astrbot.dashboard.schemas import (
    ChatFileChangeDiffRequest,
    ChatFileChangeRestoreRemovedRequest,
    ChatFileChangeRestoreRequest,
    ChatFileChangeStatusRequest,
    ChatHistoryResponse,
    ChatMessagePatchRequest,
    ChatMessageRegenerateRequest,
    ChatOpenFileRequest,
    ChatSessionBatchDeleteRequest,
    ChatSessionConfigRequest,
    ChatSessionPatchRequest,
    ChatSessionSelectionRequest,
    ChatSubagentFollowUpRequest,
    ChatThreadCreateRequest,
    ChatThreadMessageRequest,
    ChatUiQuickMessagesRequest,
    ChatUiThinkingEffortPresetsRequest,
    ChatUiThinkingEffortValueRequest,
    FileAccessModeSetRequest,
    FileAccessRootsSetRequest,
    GoalActionRequest,
    GoalSetRequest,
)
from astrbot.dashboard.services.auth_service import CONFIG_EDIT_ADMIN_SCOPE
from astrbot.dashboard.services.chat_service import (
    MAX_HISTORY_WINDOW_SIZE,
    MAX_UPLOAD_FILE_SIZE_BYTES,
    MAX_UPLOAD_FILE_SIZE_MB,
    ChatService,
    ChatServiceError,
)
from astrbot.dashboard.services.chatui_settings import (
    normalize_quick_messages,
    normalize_thinking_effort_value,
    validate_quick_messages,
)

from .auth import AuthContext, ScopeDependency, require_dashboard_user
from .multipart import single_upload

router = APIRouter(tags=["Chat"])
legacy_router = APIRouter(
    prefix="/api/chat",
    tags=["Dashboard Chat"],
    include_in_schema=False,
)

# Upper bound for the baseline text returned to the ChatUI diff overlay.
# Matches the spcode git-file cap so both diff surfaces behave the same.
_FILE_CHANGE_BASE_MAX_BYTES = 1024 * 1024


def get_service(request: Request) -> ChatService:
    return request.app.state.services.chat


require_chat_scope = ScopeDependency("chat")
require_config_scope = ScopeDependency("config")


async def _json_or_empty(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


async def _json_or_none(request: Request) -> dict[str, Any] | None:
    try:
        data = await request.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


async def _json_body(request: Request):
    try:
        return await request.json()
    except Exception:
        return None


def _model_dict(payload) -> dict[str, Any]:
    return payload.model_dump(exclude_unset=True, exclude_none=False)


async def _run(operation):
    try:
        result = await run_maybe_async(operation)
        return ok(result)
    except ChatServiceError as exc:
        return error(str(exc))


def _file_response(file_path: str, mimetype: str | None):
    if mimetype:
        return FileResponse(file_path, media_type=mimetype)
    return FileResponse(file_path)


async def _send_chat(
    *,
    request: Request,
    username: str,
    service: ChatService,
    payload: dict[str, Any] | None = None,
):
    post_data = payload if payload is not None else await _json_or_none(request)
    if post_data is None:
        return JSONResponse(error("Missing JSON body"))

    try:
        stream = await service.build_chat_stream(username, post_data)
    except ChatServiceError as exc:
        return JSONResponse(error(str(exc)))

    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Transfer-Encoding": "chunked",
            "Connection": "keep-alive",
        },
    )


async def _file_access_fixed_roots(request: Request, umo: str) -> list[dict]:
    """Implicit workspace-mode roots: session workspace + AstrBot temp dirs.

    Returns:
        [{"path": ..., "kind": "workspace" | "temp"}, ...] for the chip's
        whitelist dialog (the loaded project dir is added frontend-side
        from the spcode project status).
    """
    from astrbot.core.tools.computer_tools.util import workspace_root

    root: Path | None = None
    db = getattr(request.app.state, "db", None)
    if db is not None:
        try:
            from astrbot.core.db import BaseDatabase

            if isinstance(db, BaseDatabase):
                from astrbot.core.workspace import resolve_workspace_root_for_umo

                root = await resolve_workspace_root_for_umo(umo, db)
        except Exception:
            root = None
    if root is None:
        root = workspace_root(umo)
    return [
        {"path": str(root), "kind": "workspace"},
        {"path": get_astrbot_system_tmp_path(), "kind": "temp"},
        {"path": get_astrbot_temp_path(), "kind": "temp"},
    ]


@router.get("/chat/file-access-mode")
async def get_file_access_mode(
    request: Request,
    umo: str = Query(...),
    auth: AuthContext = Depends(require_chat_scope),
):
    # Resolve the default per-umo (config-profile aware), mirroring how
    # enforcement resolves it via star.Context.get_config(umo=...).
    default = fs_access.resolve_default(
        request.app.state.core_lifecycle.astrbot_config_mgr.get_conf(umo)
    )
    await fs_access.load_mode_for_umo(umo)
    mode = fs_access.get_mode_for_umo(umo, default=default)
    custom_roots = await fs_access.get_custom_roots(umo)
    return ok(
        {
            "umo": umo,
            "mode": mode.value,
            "default_mode": default.value,
            "fixed_roots": await _file_access_fixed_roots(request, umo),
            "custom_roots": [str(r) for r in custom_roots],
        }
    )


@router.post("/chat/file-access-mode")
async def set_file_access_mode(
    payload: FileAccessModeSetRequest,
    request: Request,
    auth: AuthContext = Depends(require_chat_scope),
):
    default = fs_access.resolve_default(
        request.app.state.core_lifecycle.astrbot_config_mgr.get_conf(payload.umo)
    )
    await fs_access.load_mode_for_umo(payload.umo)
    previous = fs_access.get_mode_for_umo(payload.umo, default=default)
    new_mode = fs_access.FileAccessMode(payload.mode)
    await fs_access.set_mode_for_umo_persisted(payload.umo, new_mode)
    if fs_access.is_tightening(previous, new_mode):
        try:
            # Tightening must not leave workspace-write shells running with
            # their old authority; the new mode only protects future calls.
            await shutdown_local_shell_sessions_for_owner(payload.umo)
        except Exception as exc:
            logger.warning(
                "Failed to terminate local shell sessions after tightening %s: %s",
                payload.umo,
                exc,
            )
    mode = fs_access.get_mode_for_umo(payload.umo, default=default)
    return ok({"umo": payload.umo, "mode": mode.value, "default_mode": default.value})


@router.post("/chat/file-access-mode/roots")
async def set_file_access_mode_roots(
    payload: FileAccessRootsSetRequest,
    auth: AuthContext = Depends(require_chat_scope),
):
    """Replace the session's custom workspace-whitelist roots."""
    roots = await fs_access.set_custom_roots(payload.umo, payload.roots)
    return ok({"umo": payload.umo, "custom_roots": [str(r) for r in roots]})


@router.post("/chat/sessions/{session_id}/workspace")
async def prepare_chat_session_workspace(
    session_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Prepare the owned session's working directory for the file browser."""
    from astrbot.core.workspace import resolve_workspace_root_for_umo

    try:
        umo = await service.shell_resource_origin(auth.username, session_id)
        root = await resolve_workspace_root_for_umo(umo, service.db)
        if not root.is_dir():
            workspaces_root = Path(get_astrbot_workspaces_path()).resolve()
            if root == workspaces_root or not root.is_relative_to(workspaces_root):
                raise ValueError("Workspace directory is unavailable")
            await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
        return ok({"path": str(root)})
    except (ChatServiceError, ValueError, OSError) as exc:
        logger.warning("Failed to prepare session workspace: %s", exc)
        return error("Failed to prepare session workspace")


@router.get("/chat/sessions/new")
async def create_chat_session(
    request: Request,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(
        lambda: service.new_session(
            auth.username,
            request.query_params.get("platform_id") or "webchat",
        )
    )


@router.post("/chat/sessions/batch-delete")
async def batch_delete_chat_sessions(
    payload: ChatSessionBatchDeleteRequest,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(
        lambda: service.batch_delete_sessions_from_dashboard_payload(
            auth.username,
            _model_dict(payload),
        )
    )


@router.post("/chat/sessions/batch-archive")
async def batch_archive_chat_sessions(
    payload: ChatSessionBatchDeleteRequest,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(
        lambda: service.batch_archive_sessions_from_dashboard_payload(
            auth.username,
            _model_dict(payload),
        )
    )


@router.post("/chat/sessions/batch-unarchive")
async def batch_unarchive_chat_sessions(
    payload: ChatSessionBatchDeleteRequest,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(
        lambda: service.batch_unarchive_sessions_from_dashboard_payload(
            auth.username,
            _model_dict(payload),
        )
    )


@router.get("/chat/sessions/archived")
async def get_archived_chat_sessions(
    request: Request,
    page: int = Query(default=1),
    page_size: int = Query(default=20),
    search: str = Query(default=""),
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(
        lambda: service.get_archived_sessions(
            auth.username,
            request.query_params.get("platform_id"),
            page,
            page_size,
            search,
        )
    )


@router.post("/chat/sessions/{session_id}/archive")
async def archive_chat_session(
    session_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=1000, ge=1, le=1000),
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(
        lambda: service.set_session_archived(auth.username, session_id, True)
    )


@router.post("/chat/sessions/{session_id}/unarchive")
async def unarchive_chat_session(
    session_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(
        lambda: service.set_session_archived(auth.username, session_id, False)
    )


@router.get(
    "/chat/sessions/{session_id}",
    response_model=ChatHistoryResponse,
    response_model_exclude_unset=True,
)
async def get_chat_session(
    session_id: str,
    limit: int | None = Query(default=None, ge=1, le=MAX_HISTORY_WINDOW_SIZE),
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Return a session with its newest history window.

    ``limit`` is opt-in: omitting it keeps the legacy full page so callers that
    do not page (the v1 dashboard query route, archived-session previews,
    third-party clients) are not silently truncated, while the ChatUI asks for
    the recent window and cursors the rest via ``/history``.
    """
    return await _run(
        lambda: service.get_session(auth.username, session_id, limit=limit)
    )


@router.get(
    "/chat/sessions/{session_id}/history",
    response_model=ChatHistoryResponse,
    response_model_exclude_unset=True,
)
async def get_chat_session_history(
    session_id: str,
    before_id: int | None = None,
    limit: int = 50,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Page one batch of history older than ``before_id`` for a session."""
    return await _run(
        lambda: service.get_history_before(
            auth.username,
            session_id,
            before_id,
            limit,
        )
    )


@router.get("/chat/sessions/{session_id}/markers")
async def get_chat_session_markers(
    session_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Return the user-message marker index for a session."""
    return await _run(lambda: service.get_message_markers(auth.username, session_id))


@router.get("/chat/sessions/{session_id}/goal")
async def get_chat_session_goal(
    session_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Return the standing-goal state for a session (null when none)."""
    return await _run(lambda: service.get_session_goal(auth.username, session_id))


@router.post("/chat/sessions/{session_id}/goal")
async def set_chat_session_goal(
    session_id: str,
    payload: GoalSetRequest,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Set (or replace) the session's standing goal and kick off the loop."""
    return await _run(
        lambda: service.set_session_goal(
            auth.username,
            session_id,
            payload.goal,
            payload.max_turns,
        )
    )


@router.post("/chat/sessions/{session_id}/goal/actions")
async def apply_chat_session_goal_action(
    session_id: str,
    payload: GoalActionRequest,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Apply a goal-loop control action (pause/resume/clear)."""
    return await _run(
        lambda: service.apply_goal_action(auth.username, session_id, payload.action)
    )


@router.get("/chat/sessions/{session_id}/shell-sessions")
async def get_chat_session_shell_sessions(
    session_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Return managed shell sessions for a session (cold-start path)."""
    return await _run(
        lambda: service.get_session_shell_sessions(auth.username, session_id)
    )


@router.get("/chat/sessions/{session_id}/shell-sessions/{shell_session_id}/output")
async def get_chat_shell_session_output(
    session_id: str,
    shell_session_id: str,
    cursor: int = 0,
    max_chars: int = 50_000,
    yield_time_ms: int = 0,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Peek at a shell session's output without consuming the agent cursor."""
    return await _run(
        lambda: service.get_shell_session_output(
            auth.username,
            session_id,
            shell_session_id,
            cursor=cursor,
            max_chars=max_chars,
            yield_time_ms=yield_time_ms,
        )
    )


@router.post("/chat/sessions/{session_id}/shell-sessions/{shell_session_id}/terminate")
async def terminate_chat_shell_session(
    session_id: str,
    shell_session_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Terminate a managed shell session on the user's behalf."""
    return await _run(
        lambda: service.terminate_shell_session(
            auth.username, session_id, shell_session_id
        )
    )


@router.post("/chat/sessions/{session_id}/shell-sessions/{shell_session_id}/discard")
async def discard_chat_shell_session(
    session_id: str,
    shell_session_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Remove a finished shell session's record (cleanup button)."""
    return await _run(
        lambda: service.discard_shell_session(
            auth.username, session_id, shell_session_id
        )
    )


@router.patch("/chat/sessions/{session_id}")
async def update_chat_session(
    session_id: str,
    payload: ChatSessionPatchRequest,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Patch a webchat session (display name and/or starred flag).

    ``starred`` takes precedence when both fields are present; the ChatUI
    sends them from two separate actions (rename / star toggle), so the
    combination is never produced by the frontend.
    """
    if payload.starred is not None:
        return await _run(
            lambda: service.set_session_starred(
                auth.username,
                session_id,
                payload.starred,
            )
        )
    return await _run(
        lambda: service.update_session_display_name(
            auth.username,
            session_id,
            payload.display_name,
        )
    )


@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    request: Request,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    result = await _run(
        lambda: service.delete_webchat_session(auth.username, session_id)
    )
    # A deleted session can no longer take part in a discussion: dissolve
    # every collab group that references it (active discussions are stopped).
    if isinstance(result, dict) and result.get("status") == "ok":
        collab = getattr(request.app.state.services, "agent_collab", None)
        if collab is not None:
            try:
                dissolved = await collab.dissolve_groups_for_session(session_id)
                if dissolved:
                    data = result.setdefault("data", {})
                    if isinstance(data, dict):
                        data["dissolved_groups"] = dissolved
            except Exception:
                logger.warning(
                    "Failed to dissolve collab groups for deleted session %s",
                    session_id,
                    exc_info=True,
                )
    return result


@router.post("/chat/sessions/{session_id}/stop")
async def stop_chat_session(
    session_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(lambda: service.stop_session(auth.username, session_id))


@router.post("/chat/subagent-follow-up")
async def post_subagent_follow_up(
    payload: ChatSubagentFollowUpRequest,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Deliver a user follow-up message into a running foreground subagent.

    The message is queued on the subagent's runner and injected at its next
    tool-result boundary. This deliberately bypasses the webchat message
    queue and the pipeline: the follow-up must not be captured by the main
    agent's follow-up mechanism nor persisted as a top-level user message.

    Returns:
        ok({"accepted": True, "seq": int}) when queued, otherwise
        ok({"accepted": False, "reason": "not_found" | "finished" | "empty_text"}).
    """
    text = payload.text.strip()
    if not text:
        return ok({"accepted": False, "reason": "empty_text"})
    try:
        session = await service.require_owned_chat_session(
            auth.username, payload.session_id, allow_thread=True
        )
    except ChatServiceError as exc:
        raise ApiError("Resource is unavailable", status_code=403) from exc
    message_type = (
        "GroupMessage" if getattr(session, "is_group", False) else "FriendMessage"
    )
    umo = f"webchat:{message_type}:webchat!{auth.username}!{payload.session_id}"
    handle = SubAgentManager.get_subagent_run_handle(umo, payload.subagent_run_id)
    if (
        handle is None
        or handle.umo != umo
        or handle.subagent_run_id != payload.subagent_run_id
    ):
        return ok({"accepted": False, "reason": "not_found"})
    ticket = handle.runner.follow_up(message_text=text)
    if ticket is None:
        return ok({"accepted": False, "reason": "finished"})
    if handle.sink is not None:
        await handle.sink.user_message(ticket.seq, text)
    return ok({"accepted": True, "seq": ticket.seq})


@router.post("/chat/open-file")
async def open_chat_local_file(
    payload: ChatOpenFileRequest,
    _auth: AuthContext = Depends(require_chat_scope),
):
    """Open a file on the AstrBot host with the OS default application.

    Used by the webchat file-change cards (2026-08-14) so users can open
    the file an agent edited/wrote directly from the reasoning sidebar.
    """
    raw_path = payload.path.strip()
    if not raw_path:
        return error("Missing file path")
    target = Path(raw_path).expanduser()
    if not target.exists():
        return error(f"File not found: {raw_path}")
    try:
        if sys.platform == "win32":
            os.startfile(target)  # noqa: S606
        else:
            # Popen equivalent that does not block the event loop.
            await asyncio.create_subprocess_exec(
                "open" if sys.platform == "darwin" else "xdg-open",
                str(target),
            )
    except OSError as exc:
        return error(f"Failed to open file: {exc}")
    return ok({"path": str(target)})


@router.post("/chat/open-folder")
async def open_chat_local_folder(
    payload: ChatOpenFileRequest,
    _auth: AuthContext = Depends(require_chat_scope),
):
    """Open the folder containing a file on the AstrBot host.

    Companion to /chat/open-file (2026-08-21) for the webchat file-change
    cards: the file is revealed and selected where the platform supports
    it. A missing file (e.g. removals) still opens its parent folder.
    """
    raw_path = payload.path.strip()
    if not raw_path:
        return error("Missing file path")
    target = Path(raw_path).expanduser()
    folder = target if target.is_dir() else target.parent
    if not folder.exists():
        return error(f"Folder not found: {folder}")
    try:
        if sys.platform == "win32" and target.is_file():
            # "explorer /select" reveals and selects the file in its folder.
            await asyncio.create_subprocess_exec("explorer", f"/select,{target}")
        elif sys.platform == "darwin" and target.is_file():
            # "open -R" reveals and selects the file in Finder.
            await asyncio.create_subprocess_exec("open", "-R", str(target))
        elif sys.platform == "win32":
            os.startfile(folder)  # noqa: S606
        else:
            # Linux has no cross-desktop "select file" convention; open the folder.
            await asyncio.create_subprocess_exec(
                "open" if sys.platform == "darwin" else "xdg-open",
                str(folder),
            )
    except OSError as exc:
        return error(f"Failed to open folder: {exc}")
    return ok({"path": str(folder)})


async def _authorize_file_change(payload, auth, service, files, *, removed=False):
    """Validate a whole request before reading or modifying any file.

    Args:
        payload: Source conversation and message/run reference.
        auth: Authenticated caller; independent API keys retain their semantics.
        service: Chat business service.
        files: All requested file descriptors.
        removed: Whether a deletion result is required.

    Returns:
        Trusted descriptors, or an empty list for independent API keys.

    Raises:
        ApiError: The source or any requested file is unavailable.
    """
    if auth is not None and auth.via != "api_key":
        try:
            return await service.require_file_change_source(
                auth.username, payload, files, removed=removed
            )
        except (ChatServiceError, OSError, ValueError) as exc:
            raise ApiError("File change is unavailable", status_code=403) from exc
    return []


@router.post("/chat/file-changes/diff")
async def chat_file_change_diff(
    payload: ChatFileChangeDiffRequest,
    _auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Unified diff between a turn's baseline backup and the current file.

    Backs the ChatUI end-of-turn file change summary card. When
    ``backup_id`` is empty (files created during the turn), the caller must
    prove knowledge of the content hash recorded at the end of the run —
    otherwise this endpoint would be an arbitrary file-read primitive.

    The baseline text is returned as ``base_content`` when it can back the
    card's full-file overlay view. The bytes are already read for the diff,
    so this adds no extra I/O; binary, oversized or truncated-patch cases
    omit the key and the UI disables the overlay slot.
    """
    await _authorize_file_change(payload, _auth, service, [payload.model_dump()])
    raw_path = payload.path.strip()
    if not raw_path:
        return error("Missing file path")
    target = Path(raw_path)
    if not target.is_file():
        return error(f"File not found: {raw_path}")
    history = get_history_manager()
    if payload.backup_id:
        try:
            _, baseline_bytes = await asyncio.to_thread(
                history.read_backup, raw_path, payload.backup_id
            )
        except (ValueError, FileNotFoundError) as exc:
            return error(str(exc))
        except OSError as exc:
            return error(f"Failed to read backup: {exc}")
    else:
        if not payload.expect_sha256:
            return error("expect_sha256 is required when backup_id is empty")
        try:
            probe = await asyncio.to_thread(target.read_bytes)
        except OSError as exc:
            return error(f"Failed to read file: {exc}")
        if hashlib.sha256(probe).hexdigest() != payload.expect_sha256:
            return error("File content does not match the recorded checksum")
        baseline_bytes = b""
    try:
        current_bytes = await asyncio.to_thread(target.read_bytes)
    except OSError as exc:
        return error(f"Failed to read file: {exc}")
    if (
        _auth is not None
        and _auth.via != "api_key"
        and (hashlib.sha256(current_bytes).hexdigest() != payload.expect_sha256)
    ):
        return error("File has changed since this result; reload its latest result")
    result = render_unified_diff(baseline_bytes, current_bytes, raw_path)
    # A truncated patch cannot drive a faithful overlay: the missing hunks
    # would silently disappear from the full-file view. Binary baselines have
    # no line structure either, so both cases omit `base_content` and let the
    # client disable the slot. NUL probing is done before decoding so a binary
    # blob is never turned into replacement characters.
    if (
        not result["truncated"]
        and len(baseline_bytes) <= _FILE_CHANGE_BASE_MAX_BYTES
        and b"\x00" not in baseline_bytes[:8192]
    ):
        result["base_content"] = baseline_bytes.decode("utf-8", errors="replace")
    return ok(result)


@router.post("/chat/file-changes/restore")
async def chat_file_change_restore(
    payload: ChatFileChangeRestoreRequest,
    _auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Restore a file to its pre-turn baseline backup (undo one file).

    Mirrors the tool-level rollback safety: the current content is saved as
    a pre-restore snapshot before overwriting, and a checksum mismatch
    (file changed after the agent run) aborts the restore.
    """
    await _authorize_file_change(payload, _auth, service, [payload.model_dump()])
    if _auth is not None and _auth.via != "api_key" and not payload.expect_sha256:
        return error("A recorded checksum is required to restore this file")
    raw_path = payload.path.strip()
    if not raw_path:
        return error("Missing file path")
    target = Path(raw_path)
    if not target.is_file():
        return error(f"File not found: {raw_path}")
    history = get_history_manager()
    try:
        _, baseline_bytes = await asyncio.to_thread(
            history.read_backup, raw_path, payload.backup_id
        )
    except (ValueError, FileNotFoundError) as exc:
        return error(str(exc))
    except OSError as exc:
        return error(f"Failed to read backup: {exc}")
    try:
        current_bytes = await asyncio.to_thread(target.read_bytes)
    except OSError as exc:
        return error(f"Failed to read file: {exc}")
    if payload.expect_sha256 and (
        hashlib.sha256(current_bytes).hexdigest() != payload.expect_sha256
    ):
        return error("File has been modified since the agent run; restore aborted.")
    try:
        await asyncio.to_thread(
            history.save_backup,
            raw_path,
            current_bytes,
            runtime="local",
            diff_preview="[pre-restore snapshot]",
        )
        await asyncio.to_thread(target.write_bytes, baseline_bytes)
    except OSError as exc:
        return error(f"Failed to restore file: {exc}")
    return ok({"path": raw_path, "restored_to": payload.backup_id})


@router.post("/chat/file-changes/restore-removed")
async def chat_file_change_restore_removed(
    payload: ChatFileChangeRestoreRemovedRequest,
    _auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Restore a file that was removed via the recycle bin (undo one delete).

    Delegates to the recycle-bin backend; a whitespace-only path is rejected
    before any filesystem work.
    """
    entries = await _authorize_file_change(
        payload, _auth, service, [{"path": payload.path}], removed=True
    )
    raw_path = payload.path.strip()
    if not raw_path:
        return error("Missing file path")
    if _auth is not None and _auth.via != "api_key":
        entry_id = entries[0].get("_recycle_entry") if entries else None
        if not entry_id:
            raise ApiError("Exact deletion record is unavailable", status_code=403)
        result = await asyncio.to_thread(
            restore_from_recycle_bin,
            raw_path,
            expected_entry=entry_id,
            expected_sha256=entries[0].get("_removed_sha256"),
        )
    else:
        result = await asyncio.to_thread(restore_from_recycle_bin, raw_path)
    if result.get("ok"):
        return ok({"restored_path": result["restored_path"]})
    return error(result["error"])


@router.post("/chat/file-changes/status")
async def chat_file_change_status(
    payload: ChatFileChangeStatusRequest,
    _auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Report which turn-changed files already match their baseline.

    Backs the "changes reverted" badge on the file change summary card: a
    file whose current bytes equal its pre-turn baseline is reported as
    reverted, no matter how it got that way (card undo, the LLM's own
    rollback tool, or a manual revert). Derived per request, so the badge
    survives reloads and never goes stale.
    """
    await _authorize_file_change(
        payload, _auth, service, [item.model_dump() for item in payload.files]
    )
    history = get_history_manager()
    result: list[dict] = []
    for item in payload.files[:50]:
        path = item.path.strip()
        reverted = False
        if path and item.backup_id:
            try:
                _, baseline_bytes = await asyncio.to_thread(
                    history.read_backup, path, item.backup_id
                )
                current_bytes = await asyncio.to_thread(Path(path).read_bytes)
                reverted = (
                    hashlib.sha256(baseline_bytes).hexdigest()
                    == hashlib.sha256(current_bytes).hexdigest()
                )
            except (ValueError, FileNotFoundError, OSError):
                reverted = False
        result.append({"path": path, "reverted": reverted})
    return ok({"files": result})


@router.get("/chat/runs/{run_id}/stream")
async def resume_chat_run(
    run_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    try:
        stream = await service.build_chat_run_stream(auth.username, run_id)
    except ChatServiceError as exc:
        logger.warning("Resume stream for run %s unavailable: %s", run_id, exc)
        return JSONResponse(error("Chat run is unavailable"))

    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Transfer-Encoding": "chunked",
            "Connection": "keep-alive",
        },
    )


@router.get("/chat/sessions/{session_id}/subagent-runs")
async def list_chat_subagent_runs(
    session_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """List compact invocation entries for an owned WebChat session.

    Args:
        session_id: Parent WebChat session or thread.
        auth: Authenticated dashboard owner.
        service: Session ownership authority.

    Returns:
        Navigation metadata, excluding execution traces.
    """
    try:
        await service.require_owned_chat_session(
            auth.username, session_id, allow_thread=True
        )
    except ChatServiceError as exc:
        raise ApiError("Resource is unavailable", status_code=403) from exc
    return ok(await subagent_progress.list_runs(session_id))


@router.get("/chat/sessions/{session_id}/subagent-runs/{run_id}/configuration")
async def get_chat_subagent_configuration(
    session_id: str,
    run_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Read configuration captured when an owned invocation started.

    Args:
        session_id: Parent WebChat session or thread.
        run_id: Invocation within that session.
        auth: Authenticated dashboard owner.
        service: Session ownership authority.

    Returns:
        Configuration snapshot, excluding execution traces and provider credentials.

    Raises:
        ApiError: The session is not owned or the invocation is missing.
    """
    try:
        await service.require_owned_chat_session(
            auth.username, session_id, allow_thread=True
        )
    except ChatServiceError as exc:
        raise ApiError("Resource is unavailable", status_code=403) from exc
    configuration = await subagent_progress.get_configuration(session_id, run_id)
    if configuration is None:
        raise ApiError("Resource is unavailable", status_code=404)
    return ok(configuration)


@router.get("/chat/sessions/{session_id}/subagent-runs/stream")
async def stream_chat_subagent_runs(
    session_id: str,
    run_id: str | None = Query(default=None, max_length=128),
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Subscribe to metadata or one trace independently of the main reply.

    Args:
        session_id: Parent WebChat session or thread.
        run_id: Optional invocation whose full trace is being viewed.
        auth: Authenticated dashboard owner.
        service: Session ownership authority.

    Returns:
        Snapshot-first event stream with bounded, recoverable live updates.

    Raises:
        ApiError: The invocation is not part of the owned session.
    """
    try:
        await service.require_owned_chat_session(
            auth.username, session_id, allow_thread=True
        )
    except ChatServiceError as exc:
        raise ApiError("Resource is unavailable", status_code=403) from exc
    entries = await subagent_progress.list_runs(session_id)
    if run_id and not any(entry["subagent_run_id"] == run_id for entry in entries):
        raise ApiError("Resource is unavailable", status_code=404)

    async def events():
        async for payload in subagent_progress.stream(session_id, run_id):
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/sessions/{session_id}/system-stream")
async def system_event_stream(
    session_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    if not service.system_stream_enabled():
        return JSONResponse(error("System event stream is disabled"))

    try:
        stream = await service.build_system_stream(auth.username, session_id)
    except ChatServiceError as exc:
        raise ApiError("Resource is unavailable", status_code=403) from exc
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Transfer-Encoding": "chunked",
            "Connection": "keep-alive",
        },
    )


@router.patch("/chat/sessions/{session_id}/messages/{message_id}")
async def update_chat_message(
    session_id: str,
    message_id: str,
    payload: ChatMessagePatchRequest,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(
        lambda: service.update_message(
            auth.username,
            {
                "session_id": session_id,
                "message_id": message_id,
                **_model_dict(payload),
            },
        )
    )


@router.post("/chat/sessions/{session_id}/messages/{message_id}/regenerate")
async def regenerate_chat_message(
    session_id: str,
    message_id: str,
    request: Request,
    payload: ChatMessageRegenerateRequest | None = None,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    body = _model_dict(payload) if payload is not None else {}
    try:
        chat_payload = await service.prepare_regenerate_message_payload(
            auth.username,
            {"session_id": session_id, "message_id": message_id, **body},
        )
    except ChatServiceError as exc:
        return JSONResponse(error(str(exc)))
    return await _send_chat(
        request=request,
        username=auth.username,
        service=service,
        payload=chat_payload,
    )


@router.post("/chat/sessions/{session_id}/messages/{message_id}/branch")
async def branch_chat_message(
    session_id: str,
    message_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(
        lambda: service.branch_session(auth.username, session_id, message_id)
    )


@router.get("/chat/configs")
async def chat_configs(
    request: Request,
    _auth: AuthContext = Depends(require_chat_scope),
):
    manager = request.app.state.core_lifecycle.astrbot_config_mgr
    return ok(
        {
            "info_list": [
                {
                    "id": info["id"],
                    "name": info["name"],
                    "agent_runner_type": manager.confs[info["id"]]
                    .get("agent_runner", {})
                    .get("runner_type", "local"),
                }
                for info in manager.get_conf_list()
                if info["id"] in manager.confs
            ]
        }
    )


@router.get("/chat/models")
async def chat_models(
    request: Request,
    _auth: AuthContext = Depends(require_chat_scope),
):
    providers = request.app.state.services.providers.list_providers(
        capability="chat_completion", enabled=True
    )["providers"]
    return ok(
        [
            {
                key: provider[key]
                for key in ("id", "model", "provider_source_id")
                if key in provider
            }
            for provider in providers
        ]
    )


@router.get("/chat/sessions/{session_id}/config")
async def get_chat_session_config(
    session_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(lambda: service.session_config(auth.username, session_id))


@router.put("/chat/sessions/{session_id}/config")
async def update_chat_session_config(
    session_id: str,
    payload: ChatSessionConfigRequest,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(
        lambda: service.session_config(auth.username, session_id, payload.config_id)
    )


@router.get("/chat/sessions/{session_id}/selection")
async def get_chat_session_selection(
    session_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(lambda: service.session_selection(auth.username, session_id))


@router.put("/chat/sessions/{session_id}/selection")
async def update_chat_session_selection(
    session_id: str,
    payload: ChatSessionSelectionRequest,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(
        lambda: service.session_selection(
            auth.username, session_id, payload.model_dump()
        )
    )


@router.get("/chat/ui-settings")
async def chat_ui_settings(
    request: Request,
    auth: AuthContext = Depends(require_chat_scope),
):
    """ChatUI input-row settings plus the caller's edit permission."""
    service = get_service(request)
    settings = {
        "thinking_effort": service.get_chatui_thinking_effort(),
        "quick_messages": service.get_chatui_quick_messages(),
        "can_edit": bool(set(auth.scopes or ()) & {"*", "config"}),
    }
    quick_messages = await request.app.state.db.get_preference(
        "dashboard_account", auth.account_id or auth.username, "quick_messages"
    )
    if quick_messages is not None:
        settings["quick_messages"] = normalize_quick_messages(
            quick_messages.value["val"]
        )
    preference = await request.app.state.db.get_preference(
        "dashboard_account", auth.account_id or auth.username, "thinking_effort"
    )
    if preference is not None:
        saved = preference.value["val"]
        if isinstance(saved, str):
            settings["thinking_effort"]["value"] = saved
        elif isinstance(saved, dict):
            settings["thinking_effort"]["value"] = saved["value"]
            if any(
                preset["id"] == saved.get("active_preset")
                for preset in settings["thinking_effort"]["presets"]
            ):
                settings["thinking_effort"]["active_preset"] = saved["active_preset"]
    return ok(settings)


@router.put("/chat/ui-settings/value")
async def update_chat_ui_effort_value(
    payload: ChatUiThinkingEffortValueRequest,
    request: Request,
    auth: AuthContext = Depends(require_chat_scope),
):
    """Store the selected effort value for the authenticated account.

    Preset definitions are admin territory, but *choosing* among them is a
    normal use of the input row, so it stays on the chat scope.
    """
    service = get_service(request)
    value = normalize_thinking_effort_value(payload.value)
    settings = service.get_chatui_thinking_effort()
    preference = await request.app.state.db.get_preference(
        "dashboard_account", auth.account_id or auth.username, "thinking_effort"
    )
    saved = preference.value["val"] if preference is not None else {}
    active = payload.active_preset
    if active is None:
        active = (
            saved.get("active_preset", settings["active_preset"])
            if isinstance(saved, dict)
            else settings["active_preset"]
        )
    if active and not any(preset["id"] == active for preset in settings["presets"]):
        raise ApiError("Thinking-effort preset is unavailable", status_code=400)
    await request.app.state.db.insert_preference_or_update(
        "dashboard_account",
        auth.account_id or auth.username,
        "thinking_effort",
        {"val": {"value": value, "active_preset": active}},
    )
    settings["value"] = value
    settings["active_preset"] = active
    return ok(settings)


@router.put(
    "/chat/ui-settings/presets",
    openapi_extra={"x-astrbot-sensitive-scopes": [CONFIG_EDIT_ADMIN_SCOPE]},
)
async def update_chat_ui_effort_presets(
    payload: ChatUiThinkingEffortPresetsRequest,
    request: Request,
    _auth: AuthContext = Depends(require_config_scope),
):
    """Replace the preset list and the active selection as one unit."""
    service = get_service(request)
    return await _run(
        lambda: service.replace_chatui_thinking_effort(
            payload.presets,
            payload.active_preset,
            payload.value,
        ),
    )


@router.put("/chat/ui-settings/quick-messages")
async def update_chat_ui_quick_messages(
    payload: ChatUiQuickMessagesRequest,
    request: Request,
    auth: AuthContext = Depends(require_chat_scope),
):
    """Replace only the authenticated account's click-to-send phrase list."""
    try:
        settings = {"items": validate_quick_messages(payload.items)}
    except ValueError as exc:
        raise ApiError(str(exc), status_code=400) from exc
    await request.app.state.db.insert_preference_or_update(
        "dashboard_account",
        auth.account_id or auth.username,
        "quick_messages",
        {"val": settings},
    )
    return ok(settings)


@router.post("/chat/threads")
async def create_chat_thread(
    payload: ChatThreadCreateRequest,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(
        lambda: service.create_thread(auth.username, _model_dict(payload))
    )


@router.get("/chat/threads/{thread_id}")
async def get_chat_thread(
    thread_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(lambda: service.get_thread(auth.username, thread_id))


@router.delete("/chat/threads/{thread_id}")
async def delete_chat_thread(
    thread_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(lambda: service.delete_thread(auth.username, thread_id))


@router.get("/chat/sessions/{session_id}/side-threads")
async def list_chat_side_threads(
    session_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(lambda: service.list_side_threads(auth.username, session_id))


@router.post("/chat/threads/{thread_id}/stop")
async def stop_chat_thread(
    thread_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    return await _run(lambda: service.stop_thread(auth.username, thread_id))


@router.post("/chat/threads/{thread_id}/messages")
async def send_chat_thread_message(
    thread_id: str,
    request: Request,
    payload: ChatThreadMessageRequest,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    try:
        chat_payload = await service.prepare_thread_chat_payload(
            auth.username,
            {"thread_id": thread_id, **_model_dict(payload)},
        )
    except ChatServiceError as exc:
        return JSONResponse(error(str(exc)))
    return await _send_chat(
        request=request,
        username=auth.username,
        service=service,
        payload=chat_payload,
    )


@legacy_router.post("/send")
async def dashboard_send_chat(
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    return await _send_chat(request=request, username=username, service=service)


@legacy_router.get("/new_session")
async def dashboard_new_session(
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    return await _run(
        lambda: service.new_session(
            username,
            request.query_params.get("platform_id") or "webchat",
        )
    )


@legacy_router.get("/sessions")
async def dashboard_get_sessions(
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    return await _run(
        lambda: service.get_sessions(username, request.query_params.get("platform_id"))
    )


@legacy_router.get("/get_session")
async def dashboard_get_session(
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    return await _run(
        lambda: service.get_session_from_dashboard_query(
            username,
            request.query_params.get("session_id"),
        )
    )


@legacy_router.post("/stop")
async def dashboard_stop_session(
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    body = await _json_or_empty(request)
    return await _run(
        lambda: service.stop_session_from_dashboard_payload(username, body)
    )


@legacy_router.get("/delete_session")
async def dashboard_delete_session(
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    return await _run(
        lambda: service.delete_webchat_session_from_dashboard_query(
            username,
            request.query_params.get("session_id"),
        )
    )


@legacy_router.post("/batch_delete_sessions")
async def dashboard_batch_delete_sessions(
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    body = await _json_body(request)
    return await _run(
        lambda: service.batch_delete_sessions_from_dashboard_payload(username, body)
    )


@legacy_router.post("/update_session_display_name")
async def dashboard_update_session_display_name(
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    body = await _json_or_empty(request)
    return await _run(
        lambda: service.update_session_display_name_from_dashboard_payload(
            username,
            body,
        )
    )


@legacy_router.post("/message/edit")
async def dashboard_update_message(
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    body = await _json_or_empty(request)
    return await _run(lambda: service.update_message(username, body))


@legacy_router.post("/message/regenerate")
async def dashboard_regenerate_message(
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    try:
        payload = (
            await service.prepare_regenerate_message_payload_from_dashboard_payload(
                username,
                await _json_or_empty(request),
            )
        )
    except ChatServiceError as exc:
        return JSONResponse(error(str(exc)))
    return await _send_chat(
        request=request,
        username=username,
        service=service,
        payload=payload,
    )


@legacy_router.post("/thread/create")
async def dashboard_create_thread(
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    body = await _json_or_empty(request)
    return await _run(lambda: service.create_thread(username, body))


@legacy_router.get("/thread/get")
async def dashboard_get_thread(
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    return await _run(
        lambda: service.get_thread_from_dashboard_query(
            username,
            request.query_params.get("thread_id"),
        )
    )


@legacy_router.post("/thread/send")
async def dashboard_send_thread_message(
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    try:
        payload = await service.prepare_thread_chat_payload_from_dashboard_payload(
            username,
            await _json_or_empty(request),
        )
    except ChatServiceError as exc:
        return JSONResponse(error(str(exc)))
    return await _send_chat(
        request=request,
        username=username,
        service=service,
        payload=payload,
    )


@legacy_router.post("/thread/delete")
async def dashboard_delete_thread(
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    body = await _json_or_empty(request)
    return await _run(
        lambda: service.delete_thread_from_dashboard_payload(username, body)
    )


@legacy_router.get("/get_file")
async def dashboard_get_file(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    try:
        file_path, mimetype = await service.resolve_webchat_file_from_dashboard_query(
            request.query_params.get("filename")
        )
        return _file_response(file_path, mimetype)
    except ChatServiceError as exc:
        return JSONResponse(error(str(exc)))
    except (FileNotFoundError, OSError):
        return JSONResponse(error("File access error"))


@legacy_router.get("/get_attachment")
async def dashboard_get_attachment(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    try:
        (
            file_path,
            mimetype,
        ) = await service.resolve_attachment_file_from_dashboard_query(
            request.query_params.get("attachment_id")
        )
        return _file_response(file_path, mimetype)
    except ChatServiceError as exc:
        return JSONResponse(error(str(exc)))
    except (FileNotFoundError, OSError):
        return JSONResponse(error("File access error"))


@legacy_router.post("/post_file")
async def dashboard_post_file(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    try:
        content_length = int(request.headers.get("content-length") or 0)
        if content_length > MAX_UPLOAD_FILE_SIZE_BYTES:
            raise ChatServiceError(
                f"File too large (limit {MAX_UPLOAD_FILE_SIZE_MB} MB)"
            )
        upload = await single_upload(request)
        if upload is None:
            raise ChatServiceError("Missing key: file")
        return ok(await service.save_uploaded_file(upload))
    except ChatServiceError as exc:
        return error(str(exc))


# --- Interactive choice (ask_user_choice tool) ------------------------------
# Route order matters (Starlette matches in registration order): the static
# /pending path must be registered before /{request_id}, or POST /pending
# would be swallowed by the submit endpoint.


async def _require_choice_origin(username: str, umo: str, service: ChatService) -> None:
    """Validate a pending question against its persisted conversation.

    Args:
        username: Trusted resource identity of the current account.
        umo: Exact origin recorded by the server-side choice registry.
        service: Chat service used to resolve the persisted resource.

    Raises:
        ApiError: The origin is malformed, forged or belongs to another account.
    """
    parts = umo.split(":", 2)
    if len(parts) != 3 or parts[0] != "webchat":
        raise ApiError("session_id must be a webchat UMO", status_code=400)
    origin = parts[2]
    session_id = origin.split("!", 2)[-1] if origin.startswith("webchat!") else origin
    try:
        session = await service.require_owned_chat_session(
            username, session_id, allow_thread=True
        )
    except ChatServiceError as exc:
        raise ApiError("Resource is unavailable", status_code=403) from exc
    message_type = (
        "GroupMessage" if getattr(session, "is_group", False) else "FriendMessage"
    )
    if parts[1] != message_type or origin not in {
        session_id,
        f"webchat!{username}!{session_id}",
    }:
        raise ApiError("Resource is unavailable", status_code=403)


@legacy_router.post("/interactive-choice/pending")
async def get_pending_choices(
    session_id: str | None = None,
    payload: dict | None = None,
    username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    """Return all pending choices for a given webchat UMO.

    Used by the frontend on mount/reconnect to reconcile any choices the
    user has not yet answered. Read-only; never mutates registry state.

    Args:
        session_id: Full UMO (query param) — ``None`` allowed so the
            endpoint can also accept it in the JSON body.
        payload: POST body, optional. Fallback source for ``session_id``
            when the query param is absent.
        username: Injected by ``require_dashboard_user``.
        service: Chat service resolving persisted session ownership.

    Returns:
        200: ``{status: "ok", data: {pending: [...]}}``.
        400: Missing ``session_id`` or session_id is not a webchat UMO.
        403: ``session_id`` belongs to a different dashboard user.
    """
    if (not session_id or not session_id.strip()) and payload:
        sid = payload.get("session_id")
        if isinstance(sid, str) and sid.strip():
            session_id = sid
    if not session_id or not session_id.strip():
        raise ApiError("Missing session_id (query or body)", status_code=400)

    await _require_choice_origin(username, session_id, service)

    pending_list = registry.list_pending_for_umo(session_id)
    parts = []
    for item in pending_list:
        spec = item["spec"].copy()
        spec["request_id"] = item["request_id"]
        spec["expires_at"] = item["timeout_at"]
        parts.append(spec)
    return ok({"pending": parts})


@legacy_router.post("/interactive-choice/{request_id}")
async def submit_interactive_choice(
    request_id: str,
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    """Resolve the pending choice future with the user's selection.

    Returns:
        200: ``{status: "ok", data: {request_id, resolved_at}}``.
        400: body missing ``choice_id``.
        403: pending belongs to another user.
        404: request_id unknown or expired.
        409: already resolved (guards double submission).
    """
    pending = registry._pending.get(request_id)
    if pending is None:
        raise ApiError("Interactive choice not found or expired", status_code=404)

    await _require_choice_origin(username, pending.umo, service)

    try:
        body = await request.json()
    except Exception:
        raise ApiError("Invalid JSON body", status_code=400)
    if not isinstance(body, dict):
        raise ApiError("Body must be a JSON object", status_code=400)

    choice_id = body.get("choice_id")
    if not isinstance(choice_id, str) or not choice_id.strip():
        raise ApiError("Missing key: choice_id", status_code=400)
    free_text = body.get("free_text") or ""
    if not isinstance(free_text, str):
        free_text = ""

    payload = {"choice_id": choice_id.strip(), "free_text": free_text.strip()}
    if not registry.resolve(request_id, payload):
        raise ApiError("Already resolved or expired", status_code=409)

    return ok({"request_id": request_id, "resolved_at": time.time()})


@legacy_router.delete("/interactive-choice/{request_id}")
async def cancel_interactive_choice(
    request_id: str,
    username: str = Depends(require_dashboard_user),
    service: ChatService = Depends(get_service),
):
    """Cancel a pending choice from the option box's cancel button.

    ``registry.remove()`` cancels the unfinished future, so the tool's
    ``CancelledError`` branch returns "[User input was cancelled] ..." to
    the LLM.

    Returns:
        200: ``{status: "ok", data: {request_id, cancelled_at}}``.
        403: pending belongs to another user (not cancelled).
        404: unknown/already resolved/expired (idempotent; the frontend
            ignores the error).
    """
    pending = registry._pending.get(request_id)
    if pending is None:
        raise ApiError("Interactive choice not found or expired", status_code=404)

    await _require_choice_origin(username, pending.umo, service)

    umo = pending.umo
    sse_message_id = pending.sse_message_id
    registry.remove(request_id)

    # Cross-tab consistency: broadcast resolved{cancelled} so other tabs of
    # the same session switch to cancelled. Best-effort; the frontend
    # reconcile covers a failed broadcast.
    try:
        await push_resolved_event_to_back_queue(
            request_id=request_id,
            umo=umo,
            reason="cancelled",
            sse_message_id=sse_message_id,
        )
    except Exception as exc:
        logger.warning(f"ask_user_choice: failed to broadcast cancel event ({exc})")

    return ok({"request_id": request_id, "cancelled_at": time.time()})
