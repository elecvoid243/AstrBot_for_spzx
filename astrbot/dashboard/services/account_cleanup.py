"""Account-bound request lifetimes and retryable ordinary-account data cleanup."""

import asyncio
import shutil
from pathlib import Path

from sqlalchemy import delete, or_
from sqlmodel import col, select
from starlette.responses import JSONResponse

from astrbot.core import file_token_service, sp
from astrbot.core.db.po import (
    Attachment,
    ChatUIProject,
    ConversationV2,
    CronJob,
    DashboardAccount,
    PlatformMessageHistory,
    PlatformSession,
    Preference,
    ProviderStat,
    RemoteChatConnection,
    RemoteChatCredential,
    RemoteChatExposure,
    SessionProjectRelation,
    UmoAlias,
    WebChatSubagentRun,
    WebChatThread,
)
from astrbot.core.goal import goal_service
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.tools import fs_access
from astrbot.core.utils.active_event_registry import active_event_registry
from astrbot.core.utils.astrbot_path import (
    get_astrbot_temp_path,
    get_astrbot_workspaces_path,
)
from astrbot.core.workspace import normalize_umo_for_workspace, parse_webchat_umo
from astrbot.dashboard.responses import error


class AccountRequestMiddleware:
    """Track complete ordinary-account ASGI requests, including streamed bodies."""

    def __init__(self, app, dashboard):
        self.app = app
        self.dashboard = dashboard

    async def __call__(self, scope, receive, send):
        """Register an authenticated request before dispatch under the account lock.

        Args:
            scope: ASGI connection scope populated by account admission.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        account = scope.get("state", {}).get("dashboard_account")
        if scope["type"] != "http" or account is None or account.role != "user":
            await self.app(scope, receive, send)
            return
        state = self.dashboard.state
        task = asyncio.current_task()
        async with state.db.dashboard_account_lock:
            async with state.db.get_db() as session:
                current = await session.get(DashboardAccount, account.account_id)
            if (
                current is None
                or not current.enabled
                or current.token_version != account.token_version
                or account.account_id in state.deleting_accounts
            ):
                await JSONResponse(error("Invalid token"), status_code=401)(
                    scope, receive, send
                )
                return
            requests = state.account_requests.setdefault(account.account_id, set())
            requests.add(task)
        response_started = False
        response_finished = False

        async def track_response(message):
            nonlocal response_started, response_finished
            if message["type"] == "http.response.start":
                response_started = True
            elif message["type"] == "http.response.body" and not message.get(
                "more_body", False
            ):
                response_finished = True
            await send(message)

        try:
            await self.app(scope, receive, track_response)
        except asyncio.CancelledError:
            # Account deletion cancels the complete inner ASGI lifetime. Close
            # the HTTP exchange normally so BaseHTTPMiddleware does not report
            # "No response returned" after cancellation cleanup has settled.
            if account.account_id not in state.deleting_accounts:
                raise
            if not response_started:
                await JSONResponse(error("Account deleted"), status_code=401)(
                    scope, receive, send
                )
            elif not response_finished:
                await send(
                    {"type": "http.response.body", "body": b"", "more_body": False}
                )
        finally:
            requests.discard(task)
            if not requests:
                state.account_requests.pop(account.account_id, None)


async def delete_account_data(app, account: DashboardAccount) -> None:
    """Stop owned work and remove managed data before removing the login.

    Filesystem operations run before the final database transaction. A failure
    leaves the disabled account and resource metadata available for safe retry.
    Only deterministic AstrBot workspaces and owned attachment files are removed;
    custom host directories are never traversed.

    Args:
        app: Dashboard application containing services and admitted request tasks.
        account: Already disabled ordinary account whose sessions were revoked.

    Raises:
        OSError: A managed file or workspace could not be removed.
        RuntimeError: Owned work could not be stopped, or deletion lost its guard.
    """
    state = app.state
    db = state.db
    chat = state.services.chat
    owner = account.resource_owner
    live = state.services.live_chat
    # Socket finalizers may detach the session and clear its recording path.
    # Retain both until strict account cleanup has verified file removal.
    voices = [
        (voice, voice.temp_audio_path)
        for voice in live.sessions.values()
        if voice.username == owner
    ]
    requests = set(state.account_requests.get(account.account_id, set()))
    for task in requests:
        task.cancel()
    if requests:
        _, pending = await asyncio.wait(requests, timeout=10)
        if pending:
            raise RuntimeError("Owned requests are still stopping; retry deletion")

    for voice, recording in voices:
        voice.is_connected = False
        voice_tasks = set(voice.chat_request_tasks.values()) | set(
            voice.chat_subscription_tasks.values()
        )
        if voice.live_request_task is not None:
            voice_tasks.add(voice.live_request_task)
        for task in voice_tasks:
            if not task.done():
                task.cancel()
        if voice_tasks:
            _, pending = await asyncio.wait(voice_tasks, timeout=10)
            if pending:
                raise RuntimeError("Owned voice work is still stopping; retry deletion")
        if recording:
            target = Path(recording).resolve()
            root = Path(get_astrbot_temp_path()).resolve()
            if target == root or not target.is_relative_to(root):
                raise RuntimeError("Unsafe managed voice recording")
            try:
                target.unlink(missing_ok=True)
            except OSError:
                voice.temp_audio_path = recording
                live.sessions[voice.session_id] = voice
                raise
        await live.cleanup_session(voice)
        live.sessions.pop(voice.session_id, None)

    # Match literal owner prefixes: account IDs contain underscores, which must
    # not become SQL LIKE wildcards and accidentally include another account.
    prefixes = [
        f"webchat:{kind}:webchat!{owner}!" for kind in ("FriendMessage", "GroupMessage")
    ]
    origin_filter = or_(
        *(col(ConversationV2.user_id).startswith(p, autoescape=True) for p in prefixes)
    )
    preference_filter = or_(
        (Preference.scope == "dashboard_account")
        & (Preference.scope_id == account.account_id),
        (Preference.scope == "umo")
        & or_(
            *(col(Preference.scope_id).startswith(p, autoescape=True) for p in prefixes)
        ),
    )
    async with db.get_db() as session:
        sessions = list(
            (
                await session.execute(
                    select(PlatformSession).where(
                        PlatformSession.creator == owner,
                        PlatformSession.platform_id == "webchat",
                    )
                )
            )
            .scalars()
            .all()
        )
        threads = list(
            (
                await session.execute(
                    select(WebChatThread).where(WebChatThread.creator == owner)
                )
            )
            .scalars()
            .all()
        )
        projects = list(
            (
                await session.execute(
                    select(ChatUIProject).where(ChatUIProject.creator == owner)
                )
            )
            .scalars()
            .all()
        )
        origins = set(
            (
                await session.execute(
                    select(ConversationV2.user_id).where(
                        ConversationV2.platform_id == "webchat", origin_filter
                    )
                )
            )
            .scalars()
            .all()
        )
        preference_filter = or_(
            preference_filter,
            (Preference.scope == "webchat_session")
            & col(Preference.scope_id).in_([item.session_id for item in sessions]),
        )
        preferences = list(
            (
                await session.execute(
                    select(Preference.scope, Preference.scope_id).where(
                        preference_filter
                    )
                )
            ).all()
        )
    ids = {s.session_id for s in sessions} | {t.thread_id for t in threads}
    origins.update(f"webchat:FriendMessage:webchat!{owner}!{sid}" for sid in ids)
    origins.update(
        f"webchat:GroupMessage:webchat!{owner}!{s.session_id}"
        for s in sessions
        if s.is_group
    )
    origins.update(scope_id for scope, scope_id in preferences if scope == "umo")
    origins.update(
        umo
        for umo in chat.umop_config_router.umop_to_conf_id
        if (parsed := parse_webchat_umo(umo)) and parsed[0] == owner
    )
    origins.update(
        umo
        for umo in active_event_registry._events
        if (parsed := parse_webchat_umo(umo)) and parsed[0] == owner
    )
    origins.update(
        umo
        for umo in active_event_registry._background_tasks
        if (parsed := parse_webchat_umo(umo)) and parsed[0] == owner
    )
    origins.update(
        umo
        for umo in await goal_service.goals.list_tracked()
        if (parsed := parse_webchat_umo(umo)) and parsed[0] == owner
    )
    async with db.get_db() as session:
        jobs = list((await session.execute(select(CronJob))).scalars().all())
        goal_keys = list(
            (
                await session.execute(
                    select(Preference.key).where(
                        Preference.scope == "goal",
                        Preference.scope_id == "kernel",
                        or_(
                            *(
                                col(Preference.key).startswith(
                                    f"goal:{p}", autoescape=True
                                )
                                for p in prefixes
                            )
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
    origins.update(key.removeprefix("goal:") for key in goal_keys)
    for job in jobs:
        umo = str((job.payload or {}).get("session") or "")
        parsed = parse_webchat_umo(umo)
        if parsed and parsed[0] == owner:
            origins.add(umo)
            cron = getattr(chat.core_lifecycle, "cron_manager", None)
            if cron is not None:
                await cron.delete_job(job.job_id)
            else:
                async with db.get_db() as session:
                    await session.execute(
                        delete(CronJob).where(CronJob.job_id == job.job_id)
                    )
                    await session.commit()
    for umo in origins:
        await goal_service.clear_goal(umo)
    ids.update(parsed[1] for umo in origins if (parsed := parse_webchat_umo(umo)))
    tasks = set()
    for umo in origins:
        active_event_registry.stop_all(umo)
        tasks.update(active_event_registry._background_tasks.get(umo, {}))
    for run in list(chat.chat_runs.values()):
        if run.username == owner:
            ids.add(run.session_id)
            if run.task and not run.task.done():
                run.task.cancel()
                tasks.add(run.task)
    # Remove queued turns before their listener can admit another pipeline run.
    for sid in ids:
        listener = webchat_queue_mgr._listener_tasks.get(sid)
        if listener is not None and not listener.done():
            tasks.add(listener)
        webchat_queue_mgr.remove_queues(sid)
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=10)
        if pending:
            raise RuntimeError("Owned agent work is still stopping; retry deletion")
    # Foreground pipeline tasks own their final persistence. Stop callbacks are
    # cooperative, so refuse success until they have unregistered as well.
    deadline = asyncio.get_running_loop().time() + 10
    while any(active_event_registry._events.get(umo) for umo in origins):
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError("Owned events are still stopping; retry deletion")
        await asyncio.sleep(0.01)

    from astrbot.core.computer import computer_client
    from astrbot.core.computer.booters.local import LocalShellComponent

    for umo, sandbox in list(computer_client.session_booter.items()):
        parsed = parse_webchat_umo(umo)
        if not parsed or parsed[0] != owner:
            continue
        from astrbot.core.computer.booters.shipyard_neo import ShipyardNeoBooter

        if isinstance(sandbox, ShipyardNeoBooter):
            await sandbox.shutdown(delete_sandbox=True)
        else:
            await sandbox.shutdown()
        computer_client._clear_cua_idle_state(umo)
        computer_client.session_booter.pop(umo, None)

    booter = computer_client.local_booter
    if booter is not None and isinstance(booter.shell, LocalShellComponent):
        shell = booter.shell
        for shell_session in list(shell._sessions.values()):
            parsed = parse_webchat_umo(shell_session.owner_id)
            if not parsed or parsed[0] != owner:
                continue
            output = shell_session.output_path
            shell_session.terminated = True
            await shell._terminate_process(shell_session)
            if shell_session.process.returncode is None:
                raise RuntimeError("Owned shell process is still stopping")
            _, pending = await asyncio.wait({shell_session.reader_task}, timeout=5)
            if pending:
                shell_session.reader_task.cancel()
                _, pending = await asyncio.wait(pending, timeout=5)
                if pending:
                    raise RuntimeError("Owned shell output is still stopping")
            # The runtime's normal reap tolerates file errors; account deletion
            # must surface them so the account cannot disappear prematurely.
            output.unlink(missing_ok=True)
            await shell.discard_session(
                owner_id=shell_session.owner_id,
                requester_id=owner,
                requester_is_admin=True,
                session_id=shell_session.session_id,
            )

    for upload in list(chat.chunked_uploads.sessions.values()):
        if upload.owner == owner:
            root = chat.chunked_uploads.chunks_root.resolve()
            target = upload.chunk_dir.resolve()
            if target == root or not target.is_relative_to(root):
                raise RuntimeError("Unsafe managed upload directory")
            if target.exists():
                await asyncio.to_thread(shutil.rmtree, target)
            chat.chunked_uploads.sessions.pop(upload.id, None)

    async with file_token_service.lock:
        for token, token_owner in list(file_token_service.account_owners.items()):
            if token_owner == owner:
                file_token_service.staged_files.pop(token, None)
                file_token_service.account_owners.pop(token, None)

    transfer = state.services.session_transfer
    for import_id, pending in list(transfer.pending_imports.items()):
        if pending.owner == owner:
            root = transfer.temp_dir.resolve()
            target = pending.zip_path.resolve()
            if target == root or not target.is_relative_to(root):
                raise RuntimeError("Unsafe managed import archive")
            target.unlink(missing_ok=True)
            transfer.pending_imports.pop(import_id, None)

    # Stop all producers before reading attachment records or flushing queued
    # preferences; otherwise cancellation cleanup could recreate deleted data.
    await sp.flush()
    await chat.umop_config_router.sp.flush()
    async with db.get_db() as session:
        attachments = list(
            (await session.execute(select(Attachment).where(Attachment.owner == owner)))
            .scalars()
            .all()
        )
        foreign_paths = set(
            (
                await session.execute(
                    select(Attachment.path).where(
                        or_(Attachment.owner != owner, Attachment.owner.is_(None))
                    )
                )
            )
            .scalars()
            .all()
        )
        custom_paths = list(
            (
                await session.execute(
                    select(ChatUIProject.workspace_path).where(
                        ChatUIProject.workspace_type == "custom",
                        ChatUIProject.workspace_path.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    foreign_paths = {Path(path).resolve() for path in foreign_paths}
    attachment_roots = [
        Path(chat.attachments_dir).resolve(),
        Path(chat.webchat_img_dir).resolve(),
    ]
    for attachment in attachments:
        target = Path(attachment.path).resolve()
        if target in foreign_paths:
            continue
        if any(
            target != root and target.is_relative_to(root) for root in attachment_roots
        ):
            await asyncio.to_thread(target.unlink, missing_ok=True)

    workspace_root = Path(get_astrbot_workspaces_path()).resolve()
    protected = []
    for path in custom_paths:
        candidate = Path(path).expanduser()
        protected.append(
            (
                candidate if candidate.is_absolute() else workspace_root / candidate
            ).resolve()
        )
    workspace_names = {normalize_umo_for_workspace(umo) for umo in origins}
    workspace_names.update(
        normalize_umo_for_workspace(f"project_{p.project_id}") for p in projects
    )
    for name in workspace_names:
        target = workspace_root / name
        resolved = target.resolve()
        # Preserve explicitly configured custom host directories, including
        # overlaps with a legacy deterministic workspace.
        if any(
            resolved == path
            or path.is_relative_to(resolved)
            or resolved.is_relative_to(path)
            for path in protected
        ):
            continue
        if target.is_symlink():
            target.unlink()
            continue
        if getattr(target, "is_junction", lambda: False)():
            target.rmdir()
            continue
        if resolved == workspace_root or not resolved.is_relative_to(workspace_root):
            raise RuntimeError("Unsafe managed workspace directory")
        if target.exists():
            await asyncio.to_thread(shutil.rmtree, target)

    for umo in origins:
        await chat.umop_config_router.delete_route(umo)
        await sp.clear_async("umo", umo)
        fs_access.clear_mode_for_umo(umo)
        fs_access.set_dynamic_roots(umo, None)
        fs_access._custom_roots_cache.pop(umo, None)
        chat.conv_mgr.session_conversations.pop(umo, None)
    await sp.clear_async("dashboard_account", account.account_id)
    project_ids = [p.project_id for p in projects]
    async with db.get_db() as session:
        current = await session.get(DashboardAccount, account.account_id)
        if current is None or current.enabled or current.role != "user":
            raise RuntimeError("Account deletion guard changed")
        for model, condition in (
            (ConversationV2, (ConversationV2.platform_id == "webchat") & origin_filter),
            (
                PlatformMessageHistory,
                or_(
                    (
                        PlatformMessageHistory.platform_id.in_(
                            ["webchat", "webchat_thread"]
                        )
                    )
                    & col(PlatformMessageHistory.user_id).in_(ids),
                    (
                        PlatformMessageHistory.platform_id.in_(
                            ["webchat", "webchat_thread"]
                        )
                    )
                    & (PlatformMessageHistory.sender_id == owner),
                ),
            ),
            (
                SessionProjectRelation,
                or_(
                    col(SessionProjectRelation.session_id).in_(ids),
                    col(SessionProjectRelation.project_id).in_(project_ids),
                ),
            ),
            (WebChatThread, WebChatThread.creator == owner),
            (WebChatSubagentRun, col(WebChatSubagentRun.session_id).in_(ids)),
            (ChatUIProject, ChatUIProject.creator == owner),
            (
                PlatformSession,
                (PlatformSession.creator == owner)
                & (PlatformSession.platform_id == "webchat"),
            ),
            (Attachment, Attachment.owner == owner),
            (Preference, preference_filter),
            (ProviderStat, col(ProviderStat.umo).in_(origins)),
            (UmoAlias, col(UmoAlias.umo).in_(origins)),
            (
                RemoteChatCredential,
                RemoteChatCredential.account_id == account.account_id,
            ),
            (RemoteChatExposure, RemoteChatExposure.account_id == account.account_id),
            (
                RemoteChatConnection,
                RemoteChatConnection.account_id == account.account_id,
            ),
        ):
            await session.execute(delete(model).where(condition))
        await session.delete(current)
        await session.commit()
    for run_id, run in list(chat.chat_runs.items()):
        if run.username == owner:
            chat.chat_runs.pop(run_id, None)
    for sid in ids:
        chat.chat_runs_by_session.pop(sid, None)
        chat.running_convs.pop(sid, None)
    if db._branch_relations is not None:
        for child, relation in list(db._branch_relations.items()):
            if child in ids or relation.get("source_session_id") in ids:
                db._branch_relations.pop(child, None)
