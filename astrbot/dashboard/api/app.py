from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import jwt
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from astrbot import logger
from astrbot.core import DEMO_MODE, LogBroker
from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.history import RunHistoryService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.db import BaseDatabase
from astrbot.core.log import LogManager
from astrbot.core.utils.webchat_access import (
    DashboardPrincipal,
    dashboard_account_context,
    webchat_account_role,
    webchat_resource_owner,
)
from astrbot.dashboard.access import require_account_operation, validate_account_request
from astrbot.dashboard.plugin_page_auth import PluginPageAuth
from astrbot.dashboard.responses import ApiError, error
from astrbot.dashboard.services.account_cleanup import AccountRequestMiddleware
from astrbot.dashboard.services.agent_collab_service import AgentCollabService
from astrbot.dashboard.services.api_key_service import ApiKeyService
from astrbot.dashboard.services.auth_service import AuthService
from astrbot.dashboard.services.backup_service import BackupService
from astrbot.dashboard.services.chat_service import ChatService
from astrbot.dashboard.services.chatui_project_service import ChatUIProjectService
from astrbot.dashboard.services.command_service import CommandService
from astrbot.dashboard.services.config_service import (
    BotConfigService,
    ConfigDisplayService,
    ConfigFileService,
    ConfigProfileService,
    ConfigRoutingService,
    ProviderConfigService,
)
from astrbot.dashboard.services.conversation_service import ConversationService
from astrbot.dashboard.services.cron_service import CronService
from astrbot.dashboard.services.file_service import FileService
from astrbot.dashboard.services.knowledge_base_service import KnowledgeBaseService
from astrbot.dashboard.services.live_chat_service import LiveChatService
from astrbot.dashboard.services.log_service import LogService
from astrbot.dashboard.services.open_api_service import OpenApiService
from astrbot.dashboard.services.persona_service import PersonaService
from astrbot.dashboard.services.platform_service import PlatformService
from astrbot.dashboard.services.plugin_page_service import PluginPageService
from astrbot.dashboard.services.plugin_service import PluginService
from astrbot.dashboard.services.remote_chat_service import RemoteChatService
from astrbot.dashboard.services.session_management_service import (
    SessionManagementService,
)
from astrbot.dashboard.services.session_transfer_service import SessionTransferService
from astrbot.dashboard.services.skills_service import SkillsService
from astrbot.dashboard.services.stat_service import StatService
from astrbot.dashboard.services.subagent_service import SubAgentService
from astrbot.dashboard.services.t2i_service import T2iService
from astrbot.dashboard.services.tools_service import ToolsService
from astrbot.dashboard.services.update_service import (
    UpdateService,
    call_get_dashboard_version,
    call_pip_install,
)
from astrbot.dashboard.static_encoding import DashboardGZipMiddleware

from .agent_collab import legacy_router as legacy_agent_collab_router
from .api_keys import legacy_router as legacy_api_keys_router
from .auth import _extract_dashboard_jwt, _extract_raw_api_key
from .auth import legacy_router as legacy_auth_router
from .backups import legacy_router as legacy_backups_router
from .bots import legacy_router as legacy_bots_router
from .chat import legacy_router as legacy_chat_router
from .chat_projects import legacy_router as legacy_chat_projects_router
from .config_profiles import legacy_router as legacy_config_profiles_router
from .conversations import legacy_router as legacy_conversations_router
from .cron import legacy_router as legacy_cron_router
from .extensions import legacy_router as legacy_extensions_router
from .files import legacy_router as legacy_files_router
from .knowledge_bases import legacy_router as legacy_knowledge_bases_router
from .live_chat import legacy_router as legacy_live_chat_router
from .logs import legacy_router as legacy_logs_router
from .personas import legacy_router as legacy_personas_router
from .platform import legacy_router as legacy_platform_router
from .plugins import legacy_router as legacy_plugins_router
from .providers import legacy_router as legacy_providers_router
from .router import API_V1_PREFIX, build_api_router
from .sessions import legacy_router as legacy_sessions_router
from .skills import legacy_router as legacy_skills_router
from .static_files import router as static_files_router
from .stats import legacy_router as legacy_stats_router
from .subagents import legacy_router as legacy_subagents_router
from .t2i import legacy_router as legacy_t2i_router
from .tools import legacy_router as legacy_tools_router
from .updates import (
    feedback_legacy_router as legacy_feedback_router,
)
from .updates import (
    legacy_router as legacy_updates_router,
)
from .updates import (
    system_announcement_legacy_router as legacy_system_announcement_router,
)

CLEAR_SITE_DATA_HEADERS = {"Clear-Site-Data": '"cache"'}


async def _shell_sessions_changed(owner_id: str) -> None:
    """Push a shell-session snapshot to the owning session's system stream.

    Registered as a ``LocalShellComponent`` change listener; module-level so
    unit tests can drive it directly. Snapshot generation lives here (not in
    the kernel component) so the kernel stays free of webchat concepts.

    Args:
        owner_id: Unified message origin owning the changed sessions;
            non-webchat origins are ignored (no dashboard stream exists).
    """
    if not owner_id.startswith("webchat:"):
        return
    from astrbot.core.computer import computer_client
    from astrbot.core.computer.booters.local import LocalShellComponent
    from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
        webchat_queue_mgr,
    )

    cid = owner_id.rsplit("!", 1)[-1]
    if not webchat_queue_mgr.has_system_subscribers(cid):
        # Nobody is watching this conversation: skip the snapshot work
        # entirely. The next transition or the cold-start GET re-converges.
        return
    booter = computer_client.local_booter
    if booter is None or not isinstance(booter.shell, LocalShellComponent):
        return
    # The push is fire-and-forget (scheduled from a sync listener); catch
    # everything here so the discarded task never ends with an unretrieved
    # exception. CancelledError (BaseException) still propagates.
    try:
        result = await booter.shell.list_sessions(
            owner_id=owner_id,
            # The snapshot is conversation-scoped, not sender-scoped: every
            # dashboard viewer of the conversation sees the same session list.
            requester_id="",
            requester_is_admin=True,
        )
        await webchat_queue_mgr.put_system_event(
            cid,
            {
                "type": "shell_sessions_changed",
                "data": {"sessions": result["sessions"]},
            },
        )
    except Exception as exc:  # noqa: BLE001 - best-effort push
        logger.warning("Failed to push shell session snapshot: %s", exc)


def create_dashboard_asgi_app(
    *,
    core_lifecycle: AstrBotCoreLifecycle,
    db: BaseDatabase,
    jwt_secret: str,
    static_folder: str | None = None,
) -> FastAPI:
    app = FastAPI(
        title="AstrBot OpenAPI",
        version="1.0.0",
        openapi_url=f"{API_V1_PREFIX}/openapi.json",
        docs_url=f"{API_V1_PREFIX}/docs",
        redoc_url=f"{API_V1_PREFIX}/redoc",
    )
    # Compress JS/CSS and other sizable responses; Starlette's GZipMiddleware
    # skips text/event-stream, so SSE endpoints are unaffected.
    app.add_middleware(DashboardGZipMiddleware, minimum_size=1024)
    app.state.core_lifecycle = core_lifecycle
    app.state.db = db
    app.state.account_requests = {}
    app.state.deleting_accounts = set()
    # Keep tracking inside account admission so complete streamed responses and
    # endpoint cancellation cleanup remain visible to account deletion.
    app.add_middleware(AccountRequestMiddleware, dashboard=app)
    app.state.jwt_secret = jwt_secret
    app.state.dashboard_static_folder = static_folder
    log_broker = getattr(core_lifecycle, "log_broker", None) or LogBroker()
    stats = StatService(
        db,
        core_lifecycle,
        core_lifecycle.astrbot_config,
        dashboard_static_folder=static_folder,
    )
    chat = ChatService(db, core_lifecycle)
    core_lifecycle.plugin_manager.context.session_message_service = chat
    services = SimpleNamespace(
        config_profiles=ConfigProfileService(core_lifecycle, db, runtime=stats.runtime),
        config_display=ConfigDisplayService(core_lifecycle),
        config_files=ConfigFileService(core_lifecycle),
        config_routes=ConfigRoutingService(core_lifecycle),
        agent_collab=AgentCollabService(),
        api_keys=ApiKeyService(db),
        auth=AuthService(db, core_lifecycle.astrbot_config),
        backups=BackupService(db, core_lifecycle),
        chat=chat,
        remote_chat=RemoteChatService(db, core_lifecycle.astrbot_config, chat),
        chat_projects=ChatUIProjectService(db),
        commands=CommandService(core_lifecycle.astrbot_config, core_lifecycle),
        conversations=ConversationService(db, core_lifecycle),
        cron=CronService(core_lifecycle),
        files=FileService(),
        knowledge_bases=KnowledgeBaseService(core_lifecycle),
        live_chat=LiveChatService(db, core_lifecycle, chat_service=chat),
        logs=LogService(log_broker, core_lifecycle.astrbot_config),
        bots=BotConfigService(core_lifecycle),
        platforms=PlatformService(core_lifecycle),
        providers=ProviderConfigService(core_lifecycle),
        personas=PersonaService(core_lifecycle),
        plugins=PluginService(core_lifecycle, core_lifecycle.plugin_manager),
        plugin_pages=PluginPageService(
            core_lifecycle.plugin_manager,
            core_lifecycle=core_lifecycle,
            db=db,
        ),
        open_api=OpenApiService(db, core_lifecycle),
        sessions=SessionManagementService(core_lifecycle, db),
        session_transfer=SessionTransferService(db, core_lifecycle),
        skills=SkillsService(core_lifecycle),
        stats=stats,
        subagents=SubAgentService(core_lifecycle),
        t2i=T2iService(core_lifecycle),
        tools=ToolsService(core_lifecycle),
        updates=UpdateService(
            core_lifecycle.astrbot_updater,
            core_lifecycle,
            get_dashboard_version_func=call_get_dashboard_version,
            pip_install_func=call_pip_install,
            demo_mode=DEMO_MODE,
            clear_site_data_headers=CLEAR_SITE_DATA_HEADERS,
            dashboard_static_folder=static_folder,
        ),
    )
    app.state.services = services
    core_lifecycle.restore_backup_service = services.backups
    services.backups.services = services
    services.restore_app = app
    from astrbot.dashboard.restore import FullRestoreMiddleware

    app.add_middleware(FullRestoreMiddleware, backups=services.backups)

    cron_manager = getattr(core_lifecycle, "cron_manager", None)
    if cron_manager is not None:
        # Jobs delivered as webchat user turns are injected through the chat
        # service: that path registers a first-class run (back queue +
        # standard persistence + run stream) instead of a lossy proactive send.
        cron_manager.set_webchat_turn_injector(chat.inject_cron_turn)

    services.role_assets = RoleAssetService(db, core_lifecycle)
    services.workflow_assets = getattr(core_lifecycle, "workflow_service", None)
    if services.workflow_assets is None:
        services.workflow_assets = WorkflowService(db, services.role_assets)
    services.run_history = RunHistoryService(db, services.workflow_assets)

    # Kernel goal loop: injected goal turns on webchat sessions register as
    # first-class chat runs so they render and recover through the exact
    # primary-run pipeline (same as agent-collab injections).
    from astrbot.core.goal.goal_service import goal_service as _goal_service

    async def _goal_run_registrar(
        umo: str, message_id: str, llm_checkpoint_id: str
    ) -> None:
        if not umo.startswith("webchat:"):
            # Non-webchat platforms have no dashboard run stream.
            return
        cid = umo.rsplit("!", 1)[-1]
        session = await db.get_platform_session_by_id(cid)
        username = session.creator if session else "astrbot"
        await app.state.services.chat.register_synthetic_chat_run(
            session_id=cid,
            message_id=message_id,
            username=username,
            llm_checkpoint_id=llm_checkpoint_id,
        )

    async def _goal_turn_selection(umo: str) -> dict:
        """Resolve current preferences before a WebChat goal continuation.

        Args:
            umo: Stored goal target origin.

        Returns:
            Current owned-session model and thinking selections.

        Raises:
            ChatServiceError: The target or its selections are unavailable.
        """
        from astrbot.dashboard.services.chat_service import (
            ChatServiceError,
            build_webchat_unified_msg_origin,
        )

        cid = umo.rsplit("!", 1)[-1]
        session = await db.get_platform_session_by_id(cid)
        if session is None or build_webchat_unified_msg_origin(session) != umo:
            raise ChatServiceError("Goal session is unavailable")
        selection = await app.state.services.chat.validate_chat_selection(
            session.creator, cid, {}
        )
        selection["thinking_effort"] = selection["thinking_effort"] or selection.get(
            "default_thinking_effort"
        )
        return selection

    _goal_service.set_turn_selection_resolver(_goal_turn_selection)
    _goal_service.set_run_registrar(_goal_run_registrar)

    async def _goal_state_changed(umo: str, state) -> None:
        """Push goal state mutations to the session's webchat system stream."""
        if not umo.startswith("webchat:"):
            return
        from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
            webchat_queue_mgr,
        )

        cid = umo.rsplit("!", 1)[-1]
        await webchat_queue_mgr.put_system_event(
            cid,
            {
                "type": "goal_state_changed",
                "data": {"goal": state.to_public_dict() if state else None},
            },
        )

    _goal_service.set_state_change_listener(_goal_state_changed)

    # Shell session awareness: forward LocalShellComponent change signals to
    # the owning conversation's system stream as full snapshots. The sync
    # wrapper matches the component's listener contract; create_task needs
    # the running loop, which all three trigger points (create/exit/remove)
    # have. get_local_booter() construction is side-effect-free (component
    # objects only, no processes).
    from astrbot.core.computer.booters.local import LocalShellComponent
    from astrbot.core.computer.computer_client import get_local_booter

    _booter = get_local_booter()
    if isinstance(_booter.shell, LocalShellComponent):
        _booter.shell.add_change_listener(
            lambda owner_id: asyncio.create_task(_shell_sessions_changed(owner_id))
        )

    @app.exception_handler(ApiError)
    async def api_error_handler(_request: Request, exc: ApiError):
        return JSONResponse(
            error(exc.message, exc.data),
            status_code=exc.status_code,
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError):
        return JSONResponse(error(str(exc)), status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def starlette_http_error_handler(
        _request: Request, exc: StarletteHTTPException
    ):
        if isinstance(exc.detail, str):
            return JSONResponse(
                error(exc.detail), status_code=exc.status_code, headers=exc.headers
            )
        return JSONResponse(
            error("Request failed", exc.detail),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def catch_all_handler(_request: Request, exc: Exception):
        LogManager.GetLogger("astrbot.dashboard").error(
            "Unhandled exception in dashboard API",
            exc_info=exc,
        )
        return JSONResponse(
            error("Internal server error"),
            status_code=500,
        )

    @app.middleware("http")
    async def account_access(request: Request, call_next):
        # Scope dependencies alone do not cover compatibility extensions and
        # bearer download capabilities. Apply account admission before dispatch.
        # Production middleware can authenticate the shared plugin bridge via
        # its scoped asset token even when a stale Dashboard cookie is present.
        # Do not re-authenticate that already verified administrator cookie.
        owner_token = None
        role_token = None
        account_token = None
        try:
            # This surface accepts only dedicated remote credentials. Ambient
            # cookies and global API keys must not select an account for it.
            if request.url.path.startswith("/api/v1/remote-chat/"):
                return await call_next(request)
            if (
                request.headers.get("Authorization", "").partition(" ")[0].lower()
                == "remotesession"
            ):
                raise ApiError(
                    "Remote credentials are restricted to remote chat", status_code=401
                )
            public_auth = request.url.path in {
                "/api/auth/login",
                "/api/v1/auth/login",
                "/api/auth/logout",
                "/api/v1/auth/logout",
                "/api/auth/setup-status",
                "/api/v1/auth/setup-status",
            }
            if (
                request.url.path.startswith("/api")
                and not public_auth
                and (raw_key := _extract_raw_api_key(request)) is not None
            ):
                # Explicit independent credentials take precedence over ambient
                # Dashboard cookies, including invalid/expired cookies. Invalid
                # keys must fail rather than fall back to the cookie identity.
                from astrbot.dashboard.services.api_key_service import ApiKeyService

                if not await db.get_active_api_key_by_hash(
                    ApiKeyService.hash_key(raw_key)
                ):
                    raise ApiError("Invalid API key", status_code=401)
                return await call_next(request)
            cached_account = getattr(request.state, "dashboard_account", None)
            if (
                cached_account is not None
                and cached_account.role == "admin"
                and PluginPageAuth.is_protected_path(request.url.path)
                and request.query_params.get("asset_token")
            ):
                return await call_next(request)
            if (
                request.url.path.startswith("/api")
                and not public_auth
                and (
                    _extract_dashboard_jwt(request) or request.query_params.get("token")
                )
            ):
                token = _extract_dashboard_jwt(request) or request.query_params.get(
                    "token"
                )
                try:
                    claims = jwt.decode(
                        token, app.state.jwt_secret, algorithms=["HS256"]
                    )
                except jwt.InvalidTokenError as exc:
                    # Independent API keys may use Bearer authentication.
                    from astrbot.dashboard.services.api_key_service import ApiKeyService

                    if not await db.get_active_api_key_by_hash(
                        ApiKeyService.hash_key(token)
                    ):
                        raise ApiError("Invalid token", status_code=401) from exc
                    claims = None
                if claims is not None:
                    if claims.get("token_type") is not None:
                        raise ApiError("Invalid token", status_code=401)
                    account = await services.auth.accounts.resolve(claims)
                    if account is None:
                        raise ApiError("Invalid token", status_code=401)
                    request.state.dashboard_account = account
                    require_account_operation(request, account)
                    owner_token = webchat_resource_owner.set(account.resource_owner)
                    role_token = webchat_account_role.set(account.role)
                    account_token = dashboard_account_context.set(
                        DashboardPrincipal(
                            account_id=account.account_id,
                            resource_owner=account.resource_owner,
                            role=account.role,
                            login_name=account.username,
                        )
                    )
                    await validate_account_request(request, account)
            return await call_next(request)
        except json.JSONDecodeError:
            return JSONResponse(error("Invalid JSON payload"), status_code=400)
        except ApiError as exc:
            return JSONResponse(error(exc.message), status_code=exc.status_code)
        except PermissionError:
            return JSONResponse(error("Resource is unavailable"), status_code=403)
        finally:
            if account_token is not None:
                dashboard_account_context.reset(account_token)
            if role_token is not None:
                webchat_account_role.reset(role_token)
            if owner_token is not None:
                webchat_resource_owner.reset(owner_token)

    # Legacy dashboard routes keep old /api/* callers working without entering OpenAPI.
    app.include_router(legacy_api_keys_router)
    app.include_router(legacy_agent_collab_router)
    app.include_router(legacy_auth_router)
    app.include_router(legacy_backups_router)
    app.include_router(legacy_config_profiles_router)
    app.include_router(legacy_bots_router)
    app.include_router(legacy_providers_router)
    app.include_router(legacy_chat_router)
    app.include_router(legacy_chat_projects_router)
    app.include_router(legacy_conversations_router)
    app.include_router(legacy_cron_router)
    app.include_router(legacy_extensions_router)
    app.include_router(legacy_files_router)
    app.include_router(legacy_knowledge_bases_router)
    app.include_router(legacy_live_chat_router)
    app.include_router(legacy_logs_router)
    app.include_router(legacy_sessions_router)
    app.include_router(legacy_skills_router)
    app.include_router(legacy_stats_router)
    app.include_router(legacy_subagents_router)
    app.include_router(legacy_tools_router)
    app.include_router(legacy_platform_router)
    app.include_router(legacy_plugins_router)
    app.include_router(legacy_t2i_router)
    app.include_router(legacy_personas_router)
    app.include_router(legacy_updates_router)
    app.include_router(legacy_system_announcement_router)
    app.include_router(legacy_feedback_router)
    app.include_router(build_api_router())
    app.include_router(static_files_router)
    return app
