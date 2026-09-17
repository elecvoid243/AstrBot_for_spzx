from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from astrbot import logger
from astrbot.core.tools import fs_access
from astrbot.core.tools.computer_tools.edit_history import (
    get_history_manager,
    render_unified_diff,
)
from astrbot.core.utils.astrbot_path import (
    get_astrbot_system_tmp_path,
    get_astrbot_temp_path,
)
from astrbot.dashboard.async_utils import run_maybe_async
from astrbot.dashboard.responses import error, ok
from astrbot.dashboard.schemas import (
    ChatFileChangeDiffRequest,
    ChatFileChangeRestoreRequest,
    ChatFileChangeStatusRequest,
    ChatMessagePatchRequest,
    ChatMessageRegenerateRequest,
    ChatOpenFileRequest,
    ChatSessionBatchDeleteRequest,
    ChatSessionPatchRequest,
    ChatThreadCreateRequest,
    ChatThreadMessageRequest,
    FileAccessModeSetRequest,
    FileAccessRootsSetRequest,
)
from astrbot.dashboard.services.chat_service import (
    MAX_HISTORY_WINDOW_SIZE,
    ChatService,
    ChatServiceError,
)

from .auth import AuthContext, ScopeDependency, require_dashboard_user
from .multipart import single_upload

router = APIRouter(tags=["Chat"])
legacy_router = APIRouter(
    prefix="/api/chat",
    tags=["Dashboard Chat"],
    include_in_schema=False,
)


def get_service(request: Request) -> ChatService:
    return request.app.state.services.chat


require_chat_scope = ScopeDependency("chat")


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
    fs_access.set_mode_for_umo(payload.umo, fs_access.FileAccessMode(payload.mode))
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


@router.get("/chat/sessions/{session_id}")
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


@router.get("/chat/sessions/{session_id}/history")
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


@router.post("/chat/file-changes/diff")
async def chat_file_change_diff(
    payload: ChatFileChangeDiffRequest,
    _auth: AuthContext = Depends(require_chat_scope),
):
    """Unified diff between a turn's baseline backup and the current file.

    Backs the ChatUI end-of-turn file change summary card. When
    ``backup_id`` is empty (files created during the turn), the caller must
    prove knowledge of the content hash recorded at the end of the run —
    otherwise this endpoint would be an arbitrary file-read primitive.
    """
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
    return ok(render_unified_diff(baseline_bytes, current_bytes, raw_path))


@router.post("/chat/file-changes/restore")
async def chat_file_change_restore(
    payload: ChatFileChangeRestoreRequest,
    _auth: AuthContext = Depends(require_chat_scope),
):
    """Restore a file to its pre-turn baseline backup (undo one file).

    Mirrors the tool-level rollback safety: the current content is saved as
    a pre-restore snapshot before overwriting, and a checksum mismatch
    (file changed after the agent run) aborts the restore.
    """
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


@router.post("/chat/file-changes/status")
async def chat_file_change_status(
    payload: ChatFileChangeStatusRequest,
    _auth: AuthContext = Depends(require_chat_scope),
):
    """Report which turn-changed files already match their baseline.

    Backs the "changes reverted" badge on the file change summary card: a
    file whose current bytes equal its pre-turn baseline is reported as
    reverted, no matter how it got that way (card undo, the LLM's own
    rollback tool, or a manual revert). Derived per request, so the badge
    survives reloads and never goes stale.
    """
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


@router.get("/chat/sessions/{session_id}/system-stream")
async def system_event_stream(
    session_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    if not service.system_stream_enabled():
        return JSONResponse(error("System event stream is disabled"))

    stream = await service.build_system_stream(auth.username, session_id)
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
    return ok(request.app.state.services.config_profiles.list_profiles())


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
        upload = await single_upload(request)
        if upload is None:
            raise ChatServiceError("Missing key: file")
        return ok(await service.save_uploaded_file(upload))
    except ChatServiceError as exc:
        return error(str(exc))
