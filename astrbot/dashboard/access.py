"""Explicit admission for account-backed Dashboard requests.

New Chat operations stay closed to ordinary accounts until their complete
resource ownership contract has been implemented. API keys keep their scopes.
"""

from fastapi import Request
from starlette.routing import Match

from astrbot.dashboard.responses import ApiError

FILE_OPERATIONS = {
    "upload_file",
    "init_file_upload",
    "upload_file_chunk",
    "complete_file_upload",
    "abort_file_upload",
    "status_file_upload",
    "get_file_by_name",
    "get_file",
    "delete_file",
    "dashboard_post_file",
    "dashboard_get_file",
    "dashboard_get_attachment",
    "get_token_file",
    "get_dashboard_token_file",
}

PROJECT_FIELDS = {
    "title",
    "emoji",
    "description",
    "workspace_type",
    "workspace_path",
    "spcode_auto_load",
    "spcode_force",
    "spcode_no_agentsmd",
    "spcode_no_codegraph",
}

CHAT_TOOL_OPERATIONS = {
    "get_chat_session_shell_sessions",
    "get_chat_shell_session_output",
    "terminate_chat_shell_session",
    "discard_chat_shell_session",
    "chat_file_change_diff",
    "chat_file_change_restore",
    "chat_file_change_restore_removed",
    "chat_file_change_status",
    "skill_guide_active",
    "skill_guide_inventory",
    "skill_guide_load",
    "skill_guide_clear",
}
PROJECT_THREAD_OPERATIONS = {
    "list_chat_projects": (set(), set()),
    "list_dashboard_chat_projects": (set(), set()),
    "create_chat_project": (PROJECT_FIELDS, set()),
    "create_dashboard_chat_project": (PROJECT_FIELDS, set()),
    "get_chat_project": (set(), set()),
    "get_dashboard_chat_project": (set(), {"project_id"}),
    "update_chat_project": (PROJECT_FIELDS, set()),
    "update_dashboard_chat_project": (PROJECT_FIELDS | {"project_id"}, set()),
    "delete_chat_project": (set(), set()),
    "delete_dashboard_chat_project": (set(), {"project_id"}),
    "list_chat_project_sessions": (set(), set()),
    "list_dashboard_chat_project_sessions": (set(), {"project_id"}),
    "list_chat_project_workspace_files": (set(), {"path"}),
    "get_chat_project_workspace_file": (set(), {"path"}),
    "download_chat_project_workspace_file": (set(), {"path"}),
    "add_chat_project_session": (set(), {"position"}),
    "add_dashboard_chat_project_session": (
        {"project_id", "session_id", "position"},
        set(),
    ),
    "remove_chat_project_session": (set(), set()),
    "remove_dashboard_chat_project_session": ({"session_id"}, set()),
    "branch_chat_message": (set(), set()),
    "create_chat_thread": (
        {"session_id", "parent_message_id", "selected_text", "side_chat"},
        set(),
    ),
    "dashboard_create_thread": (
        {"session_id", "parent_message_id", "selected_text", "side_chat"},
        set(),
    ),
    "get_chat_thread": (set(), set()),
    "dashboard_get_thread": (set(), {"thread_id"}),
    "delete_chat_thread": (set(), set()),
    "dashboard_delete_thread": ({"thread_id"}, set()),
    "list_chat_side_threads": (set(), set()),
    "stop_chat_thread": (set(), set()),
    "send_chat_thread_message": (
        {
            "message",
            "config_id",
            "selected_provider",
            "selected_model",
            "thinking_effort",
            "enable_streaming",
            "flags",
        },
        set(),
    ),
    "dashboard_send_thread_message": (
        {
            "thread_id",
            "message",
            "config_id",
            "selected_provider",
            "selected_model",
            "thinking_effort",
            "enable_streaming",
            "flags",
        },
        set(),
    ),
}

CHAT_EXTENSION_OPERATIONS = {
    "create_browser_control_session",
    "get_plugin_extension_route",
    "post_plugin_extension_route",
    "put_plugin_extension_route",
    "patch_plugin_extension_route",
    "delete_plugin_extension_route",
    "dashboard_plugin_extension_route",
}

# Endpoint names, rather than URL prefixes or the broad API-key chat scope.
USER_OPERATIONS = (
    FILE_OPERATIONS
    | PROJECT_THREAD_OPERATIONS.keys()
    | CHAT_TOOL_OPERATIONS
    | CHAT_EXTENSION_OPERATIONS
    | {
        "login",
        "dashboard_login",
        "logout",
        "dashboard_logout",
        "setup_status",
        "dashboard_setup_status",
        "current_account",
        "create_chat_session",
        "dashboard_new_session",
        "chat_sessions",
        "dashboard_get_sessions",
        "get_chat_session",
        "get_chat_session_history",
        "dashboard_get_session",
        "chat",
        "dashboard_send_chat",
        "chat_configs",
        "chat_models",
        "get_chat_session_config",
        "update_chat_session_config",
        "chat_ui_settings",
        "update_chat_ui_effort_value",
        "update_chat_ui_quick_messages",
        "get_system_announcement",
        "dashboard_system_announcement",
        "batch_delete_chat_sessions",
        "batch_archive_chat_sessions",
        "batch_unarchive_chat_sessions",
        "get_archived_chat_sessions",
        "archive_chat_session",
        "unarchive_chat_session",
        "get_chat_session_markers",
        "update_chat_session",
        "delete_chat_session",
        "update_chat_message",
        "regenerate_chat_message",
        "search_messages",
        "dashboard_delete_session",
        "dashboard_batch_delete_sessions",
        "dashboard_update_session_display_name",
        "dashboard_update_message",
        "dashboard_regenerate_message",
        "stop_chat_session",
        "dashboard_stop_session",
        "resume_chat_run",
        "system_event_stream",
        "get_chat_session_goal",
        "set_chat_session_goal",
        "apply_chat_session_goal_action",
        "post_subagent_follow_up",
        "get_pending_choices",
        "submit_interactive_choice",
        "cancel_interactive_choice",
        "export_chat_session",
        "import_chat_sessions",
        "confirm_import_chat_sessions",
    }
)


def require_account_operation(request: Request, account) -> None:
    """Admit only audited operations for an ordinary account.

    Args:
        request: Current HTTP request, before or after route resolution.
        account: Trusted enabled account resolved from a signed credential.

    Raises:
        ApiError: The operation has not been adapted for ordinary accounts.
    """
    route = request.scope.get("route")
    if route is None:
        route = next(
            (
                r
                for r in request.app.routes
                if r.matches(request.scope)[0] == Match.FULL
            ),
            None,
        )
    operation = getattr(route, "name", None)
    request.state.dashboard_operation = operation
    # Middleware admission runs before FastAPI populates request.path_params.
    request.state.dashboard_path_params = (
        route.matches(request.scope)[1].get("path_params", {}) if route else {}
    )
    if account.role != "admin" and operation not in USER_OPERATIONS:
        raise ApiError(
            "This operation is not available for this account", status_code=403
        )


async def validate_account_request(request: Request, account) -> None:
    """Validate the fields of the admitted basic-chat operations.

    Args:
        request: Current HTTP request, after operation admission.
        account: Trusted account resolved from the signed session.

    Raises:
        ApiError: The caller supplies internal fields or an unavailable resource.
    """
    operation = getattr(request.state, "dashboard_operation", None)
    if operation in {
        "export_chat_session",
        "import_chat_sessions",
        "confirm_import_chat_sessions",
    }:
        # Archive multipart parsing and confirmation schemas belong to the route;
        # the service binds all staged data and restored resources to its owner.
        return
    if account.role != "admin" and operation in PROJECT_THREAD_OPERATIONS:
        fields, query_fields = PROJECT_THREAD_OPERATIONS[operation]
        if set(request.query_params) - query_fields:
            raise ApiError("Unsupported chat query", status_code=400)
        try:
            payload = await request.json() if await request.body() else {}
        except ValueError as exc:
            raise ApiError("Invalid JSON body", status_code=400) from exc
        if not isinstance(payload, dict) or set(payload) - fields:
            raise ApiError("Unsupported chat fields", status_code=400)
        if operation in {"send_chat_thread_message", "dashboard_send_thread_message"}:
            message = payload.get("message")
            if not isinstance(message, (str, list)) or not message:
                raise ApiError("Invalid message", status_code=400)
            for part in message if isinstance(message, list) else []:
                if (
                    not isinstance(part, dict)
                    or part.get("type")
                    not in {
                        "plain",
                        "reply",
                        "image",
                        "record",
                        "audio",
                        "video",
                        "file",
                    }
                    or set(part)
                    - {
                        "type",
                        "text",
                        "attachment_id",
                        "filename",
                        "stored_filename",
                        "message_id",
                        "selected_text",
                    }
                ):
                    raise ApiError("Unsupported message part", status_code=400)
            flags = payload.get("flags")
            if flags is not None and (
                not isinstance(flags, dict)
                or set(flags)
                - {
                    "enable_inline_genui",
                    "enable_default_system_prompt",
                    "enable_streaming",
                }
                or any(not isinstance(value, bool) for value in flags.values())
            ):
                raise ApiError("Unsupported chat flags", status_code=400)
        return
    if operation in CHAT_EXTENSION_OPERATIONS:
        # The shared dispatcher validates exact methods, fields, owned origins
        # and secondary resources before invoking an audited consumer handler.
        return
    if operation in CHAT_TOOL_OPERATIONS:
        query_fields = {
            "get_chat_shell_session_output": {"cursor", "max_chars", "yield_time_ms"},
            "skill_guide_active": {"umo"},
            "skill_guide_inventory": {"umo"},
        }.get(operation, set())
        if set(request.query_params) - query_fields:
            raise ApiError("Unsupported chat query", status_code=400)
        if request.method == "POST":
            fields = {
                "skill_guide_load": {"umo", "skill_name"},
                "skill_guide_clear": {"umo"},
                "chat_file_change_diff": {
                    "session_id",
                    "message_id",
                    "run_id",
                    "path",
                    "backup_id",
                    "expect_sha256",
                },
                "chat_file_change_restore": {
                    "session_id",
                    "message_id",
                    "run_id",
                    "path",
                    "backup_id",
                    "expect_sha256",
                },
                "chat_file_change_restore_removed": {
                    "session_id",
                    "message_id",
                    "run_id",
                    "path",
                },
                "chat_file_change_status": {
                    "session_id",
                    "message_id",
                    "run_id",
                    "files",
                },
            }.get(operation, set())
            try:
                payload = await request.json() if await request.body() else {}
            except ValueError as exc:
                raise ApiError("Invalid chat payload", status_code=400) from exc
            if not isinstance(payload, dict) or set(payload) - fields:
                raise ApiError("Unsupported chat fields", status_code=400)
        return
    runtime_fields = {
        "stop_chat_session": set(),
        "dashboard_stop_session": {"session_id"},
        "resume_chat_run": set(),
        "system_event_stream": set(),
        "get_chat_session_goal": set(),
        "set_chat_session_goal": {"goal", "max_turns"},
        "apply_chat_session_goal_action": {"action"},
        "post_subagent_follow_up": {"session_id", "subagent_run_id", "text"},
        "get_pending_choices": {"session_id"},
        "submit_interactive_choice": {"choice_id", "free_text"},
        "cancel_interactive_choice": set(),
    }
    if account.role != "admin" and operation in runtime_fields:
        if set(request.query_params) - (
            {"session_id"} if operation == "get_pending_choices" else set()
        ):
            raise ApiError("Unsupported chat query", status_code=400)
        body = await request.body()
        if body:
            try:
                payload = await request.json()
            except ValueError as exc:
                raise ApiError("Invalid JSON body", status_code=400) from exc
            if (
                not isinstance(payload, dict)
                or set(payload) - runtime_fields[operation]
            ):
                raise ApiError("Unsupported chat fields", status_code=400)
        return
    if request.method == "POST" and getattr(
        request.state, "dashboard_operation", None
    ) in {"chat", "dashboard_send_chat"}:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ApiError("Invalid chat payload", status_code=400)
        for key, value in payload.items():
            if key.startswith("_") and key != "_client_message_id":
                # The chat service validates edited continuations against the
                # latest persisted user message in the authenticated session.
                if key in {
                    "_skip_user_history",
                    "_llm_checkpoint_id",
                }:
                    continue
                if (key == "_skip_user_history" and value is False) or (
                    key == "_llm_checkpoint_id" and value is None
                ):
                    continue
                raise ApiError(
                    "Internal chat fields are not accepted",
                    status_code=400,
                )
    operation = getattr(request.state, "dashboard_operation", None)
    if operation in FILE_OPERATIONS:
        # Typed upload schemas and multipart parsers validate file operations.
        # Ownership is enforced by the upload service and attachment database.
        return
    if account.role != "admin" and operation in {
        "batch_delete_chat_sessions",
        "batch_archive_chat_sessions",
        "batch_unarchive_chat_sessions",
        "get_archived_chat_sessions",
        "archive_chat_session",
        "unarchive_chat_session",
        "get_chat_session_markers",
        "update_chat_session",
        "delete_chat_session",
        "update_chat_message",
        "regenerate_chat_message",
        "search_messages",
        "dashboard_delete_session",
        "dashboard_batch_delete_sessions",
        "dashboard_update_session_display_name",
        "dashboard_update_message",
        "dashboard_regenerate_message",
    }:
        query_fields = {
            "get_archived_chat_sessions": {
                "page",
                "page_size",
                "search",
                "platform_id",
            },
            "search_messages": {"q", "page", "page_size"},
            "dashboard_delete_session": {"session_id"},
        }.get(operation, set())
        if set(request.query_params) - query_fields:
            raise ApiError("Unsupported chat query", status_code=400)
        if request.query_params.get("platform_id", "webchat") != "webchat":
            raise ApiError("Unsupported chat platform", status_code=403)
        if request.method in {"POST", "PATCH"}:
            try:
                payload = await request.json() if await request.body() else {}
            except ValueError as exc:
                raise ApiError("Invalid chat payload", status_code=400) from exc
            fields = {
                "batch_delete_chat_sessions": {"session_ids"},
                "batch_archive_chat_sessions": {"session_ids"},
                "batch_unarchive_chat_sessions": {"session_ids"},
                "dashboard_batch_delete_sessions": {"session_ids"},
                "update_chat_session": {"display_name", "starred"},
                "update_chat_message": {"content"},
                "dashboard_update_session_display_name": {"session_id", "display_name"},
                "dashboard_update_message": {"session_id", "message_id", "content"},
                "dashboard_regenerate_message": {
                    "session_id",
                    "message_id",
                    "config_id",
                    "thinking_effort",
                    "selected_provider",
                    "selected_model",
                },
                "regenerate_chat_message": {
                    "config_id",
                    "thinking_effort",
                    "selected_provider",
                    "selected_model",
                },
            }.get(operation, set())
            if not isinstance(payload, dict) or set(payload) - fields:
                raise ApiError("Unsupported chat fields", status_code=400)
        else:
            payload = {}
        session_id = (
            request.state.dashboard_path_params.get("session_id")
            or payload.get("session_id")
            or request.query_params.get("session_id")
        )
        if session_id:
            session = await request.app.state.db.get_platform_session_by_id(session_id)
            if (
                session is None
                or session.creator != account.resource_owner
                or session.platform_id != "webchat"
            ):
                raise ApiError("Resource is unavailable", status_code=403)
        return
    if account.role != "admin":
        if any(
            k
            not in {
                "session_id",
                "conversation_id",
                "limit",
                "before_id",
                "platform_id",
                "page",
                "page_size",
            }
            for k in request.query_params
        ):
            raise ApiError("Unsupported chat query", status_code=400)
        if request.query_params.get("platform_id", "webchat") != "webchat":
            raise ApiError("Unsupported chat platform", status_code=403)
        if request.method == "POST":
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) - {
                "session_id",
                "conversation_id",
                "message",
                "message_id",
                "config_id",
                "thinking_effort",
                "selected_provider",
                "selected_model",
                "_skip_user_history",
                "_llm_checkpoint_id",
            }:
                raise ApiError("Unsupported chat fields", status_code=400)
            if payload.get("_skip_user_history") is True and not isinstance(
                payload.get("_llm_checkpoint_id"), str
            ):
                raise ApiError("Invalid edited continuation", status_code=400)
            message = payload.get("message")
            if not isinstance(message, (str, list)) or not message:
                raise ApiError("Invalid message", status_code=400)
            if isinstance(message, list):
                for part in message:
                    if (
                        not isinstance(part, dict)
                        or part.get("type")
                        not in {
                            "plain",
                            "reply",
                            "image",
                            "record",
                            "audio",
                            "video",
                            "file",
                        }
                        or set(part)
                        - {
                            "type",
                            "text",
                            "attachment_id",
                            "filename",
                            "stored_filename",
                            "message_id",
                            "selected_text",
                        }
                    ):
                        raise ApiError("Unsupported message part", status_code=400)
                    if part["type"] not in {"plain", "reply"} and not isinstance(
                        part.get("attachment_id"), str
                    ):
                        raise ApiError("Attachment ID is required", status_code=400)
            session_id = payload.get("session_id", payload.get("conversation_id"))
            if not isinstance(session_id, str) or not session_id:
                raise ApiError("Invalid session ID", status_code=400)
            session = await request.app.state.db.get_platform_session_by_id(session_id)
            if (
                session is None
                or session.creator != account.resource_owner
                or session.platform_id != "webchat"
            ):
                raise ApiError("Resource is unavailable", status_code=403)
