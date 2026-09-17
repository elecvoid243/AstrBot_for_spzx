import abc
import datetime
import typing as T
from contextlib import asynccontextmanager
from dataclasses import dataclass

from deprecated import deprecated
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from astrbot.core.db.po import (
    AgentTeam,
    AgentTeamRun,
    AgentTeamRunMessage,
    AgentTeamWorkflow,
    ApiKey,
    Attachment,
    ChatUIProject,
    CommandConfig,
    CommandConflict,
    ConversationV2,
    CronJob,
    Persona,
    PersonaFolder,
    PlatformMessageHistory,
    PlatformSession,
    PlatformStat,
    Preference,
    ProviderStat,
    SessionProjectRelation,
    Stats,
    UmoAlias,
    WebChatThread,
)
from astrbot.core.sentinels import NOT_GIVEN


@dataclass
class BaseDatabase(abc.ABC):
    """数据库基类"""

    DATABASE_URL = ""

    def __init__(self) -> None:
        # SQLite only supports a single writer at a time.  Without a busy
        # timeout the driver raises "database is locked" instantly when a
        # second write is attempted.  Setting timeout=30 tells SQLite to
        # wait up to 30 s for the lock, which is enough to ride out brief
        # write bursts from concurrent agent/metrics/session operations.
        is_sqlite = "sqlite" in self.DATABASE_URL
        connect_args = {"timeout": 30} if is_sqlite else {}
        self.engine = create_async_engine(
            self.DATABASE_URL,
            echo=False,
            future=True,
            connect_args=connect_args,
        )
        self.AsyncSessionLocal = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        # 2026-08-13: cached branch relations (child session -> source).
        # Lives here (not on a service) so every dashboard service sharing
        # this db instance sees the same cache; `branch_session` updates it
        # incrementally to avoid the expensive full-table scan.
        self._branch_relations: dict[str, dict] | None = None

    async def initialize(self) -> None:
        """初始化数据库连接"""

    @asynccontextmanager
    async def get_db(self) -> T.AsyncGenerator[AsyncSession, None]:
        """Get a database session."""
        if not self.inited:
            await self.initialize()
            self.inited = True
        async with self.AsyncSessionLocal() as session:
            yield session

    @deprecated(version="4.0.0", reason="Use get_platform_stats instead")
    @abc.abstractmethod
    def get_base_stats(self, offset_sec: int = 86400) -> Stats:
        """获取基础统计数据"""
        raise NotImplementedError

    @deprecated(version="4.0.0", reason="Use get_platform_stats instead")
    @abc.abstractmethod
    def get_total_message_count(self) -> int:
        """获取总消息数"""
        raise NotImplementedError

    @deprecated(version="4.0.0", reason="Use get_platform_stats instead")
    @abc.abstractmethod
    def get_grouped_base_stats(self, offset_sec: int = 86400) -> Stats:
        """获取基础统计数据(合并)"""
        raise NotImplementedError

    # New methods in v4.0.0

    @abc.abstractmethod
    async def insert_platform_stats(
        self,
        platform_id: str,
        platform_type: str,
        count: int = 1,
        timestamp: datetime.datetime | None = None,
    ) -> None:
        """Insert a new platform statistic record."""
        ...

    @abc.abstractmethod
    async def count_platform_stats(self) -> int:
        """Count the number of platform statistics records."""
        ...

    @abc.abstractmethod
    async def get_platform_stats(self, offset_sec: int = 86400) -> list[PlatformStat]:
        """Get platform statistics within the specified offset in seconds and group by platform_id."""
        ...

    @abc.abstractmethod
    async def insert_provider_stat(
        self,
        *,
        umo: str,
        provider_id: str,
        provider_model: str | None = None,
        conversation_id: str | None = None,
        status: str = "completed",
        stats: dict | None = None,
        agent_type: str = "internal",
    ) -> ProviderStat:
        """Insert a per-response provider stat record."""
        ...

    @abc.abstractmethod
    async def get_conversations(
        self,
        user_id: str | None = None,
        platform_id: str | None = None,
    ) -> list[ConversationV2]:
        """Get all conversations for a specific user and platform_id(optional).

        content is not included in the result.
        """
        ...

    @abc.abstractmethod
    async def get_conversation_by_id(self, cid: str) -> ConversationV2:
        """Get a specific conversation by its ID."""
        ...

    @abc.abstractmethod
    async def get_all_conversations(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> list[ConversationV2]:
        """Get all conversations with pagination."""
        ...

    @abc.abstractmethod
    async def get_filtered_conversations(
        self,
        page: int = 1,
        page_size: int = 20,
        platform_ids: list[str] | None = None,
        search_query: str = "",
        include_history: bool = True,
        **kwargs,
    ) -> tuple[list[ConversationV2], int]:
        """Filter conversations by platform IDs and search text.

        Args:
            page: Page number.
            page_size: Number of items per page.
            platform_ids: Platform IDs to include, if any.
            search_query: Search text, if any.
            include_history: Whether to load the full history for returned rows.
            **kwargs: Additional filters supported by the database backend.
        """
        ...

    @abc.abstractmethod
    async def get_conversation_platform_ids(self) -> list[str]:
        """Return distinct platform IDs referenced by conversation history.

        Returns:
            Sorted platform IDs that have at least one conversation.
        """
        ...

    @abc.abstractmethod
    async def create_conversation(
        self,
        user_id: str,
        platform_id: str,
        content: list[dict] | None = None,
        title: str | None = None,
        persona_id: str | None = None,
        cid: str | None = None,
        created_at: datetime.datetime | None = None,
        updated_at: datetime.datetime | None = None,
    ) -> ConversationV2:
        """Create a new conversation."""
        ...

    @abc.abstractmethod
    async def update_conversation(
        self,
        cid: str,
        title: str | None = None,
        persona_id: str | None = None,
        content: list[dict] | None = None,
        token_usage: int | None = None,
    ) -> None:
        """Update a conversation's history."""
        ...

    @abc.abstractmethod
    async def delete_conversation(self, cid: str) -> None:
        """Delete a conversation by its ID."""
        ...

    @abc.abstractmethod
    async def delete_conversations_by_user_id(self, user_id: str) -> None:
        """Delete all conversations for a specific user."""
        ...

    @abc.abstractmethod
    async def insert_platform_message_history(
        self,
        platform_id: str,
        user_id: str,
        content: dict,
        sender_id: str | None = None,
        sender_name: str | None = None,
        llm_checkpoint_id: str | None = None,
        max_messages: int | None = None,
    ) -> PlatformMessageHistory:
        """Insert a new platform message history record."""
        ...

    @abc.abstractmethod
    async def update_platform_message_history(
        self,
        message_id: int,
        content: dict | None = None,
        llm_checkpoint_id: str | None = None,
    ) -> None:
        """Update a platform message history record."""
        ...

    @abc.abstractmethod
    async def delete_platform_message_history_by_id(self, message_id: int) -> None:
        """Delete a platform message history record by its ID."""
        ...

    @abc.abstractmethod
    async def delete_platform_message_offset(
        self,
        platform_id: str,
        user_id: str,
        offset_sec: int = 86400,
    ) -> None:
        """Delete platform message history records newer than the specified offset."""
        ...

    @abc.abstractmethod
    async def get_platform_message_history(
        self,
        platform_id: str,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        before_id: int | None = None,
    ) -> list[PlatformMessageHistory]:
        """Get platform message history for a specific user.

        Args:
            platform_id: Platform instance ID.
            user_id: Unified message origin for the group.
            page: 1-based page number. Ignored when ``before_id`` is set.
            page_size: Number of rows per page.
            before_id: Exclusive cursor; only records with a smaller id are
                returned. None starts from the newest rows.
        """
        ...

    @abc.abstractmethod
    async def count_platform_message_history(
        self,
        platform_id: str,
        user_id: str,
        before_id: int | None = None,
    ) -> int:
        """Count platform message history records.

        Args:
            platform_id: Platform instance ID.
            user_id: Unified message origin for the group.
            before_id: When set, only count records with a smaller id.
        """
        ...

    @abc.abstractmethod
    async def get_platform_message_history_by_id(
        self,
        message_id: int,
    ) -> PlatformMessageHistory | None:
        """Get a platform message history record by its ID."""
        ...

    @abc.abstractmethod
    async def get_webchat_branch_infos(
        self,
    ) -> list[PlatformMessageHistory]:
        """Get all branch_info divider records in platform message history.

        This is a coarse full-table text match intended for one-time cache
        warm-up, not per-request use. Callers must still validate
        ``content["type"] == "branch_info"``.

        Returns:
            History records whose JSON content mentions ``branch_info``.
        """
        ...

    async def get_branch_relations(self) -> dict[str, dict]:
        """Return cached session branch relations (child -> source).

        Scans platform history once on first use and caches the result on
        this db instance, so all services sharing the database see the
        same relations. ``update_branch_relation`` keeps the cache in sync
        after a new branch is created without a rescan.

        Returns:
            Mapping of child session id to ``{"source_session_id": ...,
            "source_message_id": ...}``.
        """
        if self._branch_relations is None:
            relations: dict[str, dict] = {}
            for record in await self.get_webchat_branch_infos():
                content = record.content
                if (
                    not isinstance(content, dict)
                    or content.get("type") != "branch_info"
                ):
                    continue
                source_id = content.get("source_session_id")
                if not source_id:
                    continue
                relations[record.user_id] = {
                    "source_session_id": source_id,
                    "source_message_id": content.get("source_message_id"),
                }
            self._branch_relations = relations
        return self._branch_relations

    def update_branch_relation(
        self,
        child_session_id: str,
        source_session_id: str,
        source_message_id: int | str,
    ) -> None:
        """Incrementally update the cached branch relations.

        No-op while the cache is cold; the next ``get_branch_relations``
        scan picks the relation up anyway.

        Args:
            child_session_id: Newly created branch session id.
            source_session_id: Session the branch was created from.
            source_message_id: Bot message id the branch was created at.
        """
        if self._branch_relations is not None:
            self._branch_relations[child_session_id] = {
                "source_session_id": source_session_id,
                "source_message_id": source_message_id,
            }

    @abc.abstractmethod
    async def create_webchat_thread(
        self,
        creator: str,
        parent_session_id: str,
        parent_message_id: int,
        base_checkpoint_id: str,
        selected_text: str,
    ) -> WebChatThread:
        """Create a WebChat side thread."""
        ...

    @abc.abstractmethod
    async def get_webchat_thread_by_id(
        self,
        thread_id: str,
    ) -> WebChatThread | None:
        """Get a WebChat side thread by thread_id."""
        ...

    @abc.abstractmethod
    async def get_webchat_threads_by_parent_session(
        self,
        parent_session_id: str,
        creator: str | None = None,
    ) -> list[WebChatThread]:
        """Get side threads for a parent WebChat session."""
        ...

    @abc.abstractmethod
    async def get_webchat_thread_by_parent_message_and_text(
        self,
        parent_session_id: str,
        parent_message_id: int,
        selected_text: str,
        creator: str | None = None,
    ) -> WebChatThread | None:
        """Get an existing side thread for the same selected text."""
        ...

    @abc.abstractmethod
    async def delete_webchat_thread(self, thread_id: str) -> None:
        """Delete a WebChat side thread."""
        ...

    @abc.abstractmethod
    async def delete_webchat_threads_by_parent_session(
        self,
        parent_session_id: str,
    ) -> list[str]:
        """Delete side threads for a parent WebChat session."""
        ...

    @abc.abstractmethod
    async def delete_webchat_threads_by_parent_message_ids(
        self,
        parent_session_id: str,
        parent_message_ids: list[int],
    ) -> list[str]:
        """Delete side threads linked to parent message IDs."""
        ...

    @abc.abstractmethod
    async def insert_attachment(
        self,
        path: str,
        type: str,
        mime_type: str,
    ):
        """Insert a new attachment record."""
        ...

    @abc.abstractmethod
    async def get_attachment_by_id(self, attachment_id: str) -> Attachment:
        """Get an attachment by its ID."""
        ...

    @abc.abstractmethod
    async def get_attachments(self, attachment_ids: list[str]) -> list[Attachment]:
        """Get multiple attachments by their IDs."""
        ...

    @abc.abstractmethod
    async def delete_attachment(self, attachment_id: str) -> bool:
        """Delete an attachment by its ID.

        Returns True if the attachment was deleted, False if it was not found.
        """
        ...

    @abc.abstractmethod
    async def delete_attachments(self, attachment_ids: list[str]) -> int:
        """Delete multiple attachments by their IDs.

        Returns the number of attachments deleted.
        """
        ...

    @abc.abstractmethod
    async def create_api_key(
        self,
        name: str,
        key_hash: str,
        key_prefix: str,
        scopes: list[str] | None,
        created_by: str,
        expires_at: datetime.datetime | None = None,
    ) -> ApiKey:
        """Create a new API key record."""
        ...

    @abc.abstractmethod
    async def list_api_keys(self) -> list[ApiKey]:
        """List all API keys."""
        ...

    @abc.abstractmethod
    async def get_api_key_by_id(self, key_id: str) -> ApiKey | None:
        """Get an API key by key_id."""
        ...

    @abc.abstractmethod
    async def get_active_api_key_by_hash(self, key_hash: str) -> ApiKey | None:
        """Get an active API key by hash (not revoked, not expired)."""
        ...

    @abc.abstractmethod
    async def touch_api_key(self, key_id: str) -> None:
        """Update last_used_at of an API key."""
        ...

    @abc.abstractmethod
    async def revoke_api_key(self, key_id: str) -> bool:
        """Revoke an API key.

        Returns True when the key exists and is updated.
        """
        ...

    @abc.abstractmethod
    async def delete_api_key(self, key_id: str) -> bool:
        """Delete an API key.

        Returns True when the key exists and is deleted.
        """
        ...

    @abc.abstractmethod
    async def insert_persona(
        self,
        persona_id: str,
        system_prompt: str,
        begin_dialogs: list[str] | None = None,
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        custom_error_message: str | None = None,
        folder_id: str | None = None,
        sort_order: int = 0,
    ) -> Persona:
        """Insert a new persona record.

        Args:
            persona_id: Unique identifier for the persona
            system_prompt: System prompt for the persona
            begin_dialogs: Optional list of initial dialog strings
            tools: Optional list of tool names (None means all tools, [] means no tools)
            skills: Optional list of skill names (None means all skills, [] means no skills)
            custom_error_message: Optional persona-level fallback error message
            folder_id: Optional folder ID to place the persona in (None means root)
            sort_order: Sort order within the folder (default 0)
        """
        ...

    @abc.abstractmethod
    async def get_persona_by_id(self, persona_id: str) -> Persona:
        """Get a persona by its ID."""
        ...

    @abc.abstractmethod
    async def get_personas(self) -> list[Persona]:
        """Get all personas for a specific bot."""
        ...

    @abc.abstractmethod
    async def update_persona(
        self,
        persona_id: str,
        system_prompt: str | None = None,
        begin_dialogs: list[str] | None = None,
        tools: list[str] | None | object = NOT_GIVEN,
        skills: list[str] | None | object = NOT_GIVEN,
        custom_error_message: str | None | object = NOT_GIVEN,
    ) -> Persona | None:
        """Update a persona record.

        Args:
            persona_id: Persona ID to update.
            system_prompt: Optional replacement system prompt.
            begin_dialogs: Optional replacement begin dialogs.
            tools: Tool names, None for all tools, or NOT_GIVEN to leave unchanged.
            skills: Skill names, None for all skills, or NOT_GIVEN to leave unchanged.
            custom_error_message: Custom fallback message, None to clear, or NOT_GIVEN to leave unchanged.

        Returns:
            Updated persona, or None when no fields were updated.
        """
        ...

    @abc.abstractmethod
    async def delete_persona(self, persona_id: str) -> None:
        """Delete a persona by its ID."""
        ...

    # ====
    # Persona Folder Management
    # ====

    @abc.abstractmethod
    async def insert_persona_folder(
        self,
        name: str,
        parent_id: str | None = None,
        description: str | None = None,
        sort_order: int = 0,
    ) -> PersonaFolder:
        """Insert a new persona folder."""
        ...

    @abc.abstractmethod
    async def get_persona_folder_by_id(self, folder_id: str) -> PersonaFolder | None:
        """Get a persona folder by its folder_id."""
        ...

    @abc.abstractmethod
    async def get_persona_folders(
        self, parent_id: str | None = None
    ) -> list[PersonaFolder]:
        """Get all persona folders, optionally filtered by parent_id."""
        ...

    @abc.abstractmethod
    async def get_all_persona_folders(self) -> list[PersonaFolder]:
        """Get all persona folders."""
        ...

    @abc.abstractmethod
    async def update_persona_folder(
        self,
        folder_id: str,
        name: str | None = None,
        parent_id: T.Any = None,
        description: T.Any = None,
        sort_order: int | None = None,
    ) -> PersonaFolder | None:
        """Update a persona folder."""
        ...

    @abc.abstractmethod
    async def delete_persona_folder(self, folder_id: str) -> None:
        """Delete a persona folder by its folder_id."""
        ...

    @abc.abstractmethod
    async def move_persona_to_folder(
        self, persona_id: str, folder_id: str | None
    ) -> Persona | None:
        """Move a persona to a folder (or root if folder_id is None)."""
        ...

    @abc.abstractmethod
    async def get_personas_by_folder(
        self, folder_id: str | None = None
    ) -> list[Persona]:
        """Get all personas in a specific folder."""
        ...

    @abc.abstractmethod
    async def batch_update_sort_order(
        self,
        items: list[dict],
    ) -> None:
        """Batch update sort_order for personas and/or folders.

        Args:
            items: List of dicts with keys:
                - id: The persona_id or folder_id
                - type: Either "persona" or "folder"
                - sort_order: The new sort_order value
        """
        ...

    @abc.abstractmethod
    async def insert_preference_or_update(
        self,
        scope: str,
        scope_id: str,
        key: str,
        value: dict,
    ) -> Preference:
        """Insert a new preference record."""
        ...

    @abc.abstractmethod
    async def get_preference(self, scope: str, scope_id: str, key: str) -> Preference:
        """Get a preference by scope ID and key."""
        ...

    @abc.abstractmethod
    async def get_preferences(
        self,
        scope: str | None = None,
        scope_id: str | None = None,
        key: str | None = None,
    ) -> list[Preference]:
        """Get preferences, optionally filtered by scope, scope ID, or key."""
        ...

    @abc.abstractmethod
    async def remove_preference(self, scope: str, scope_id: str, key: str) -> None:
        """Remove a preference by scope ID and key."""
        ...

    @abc.abstractmethod
    async def clear_preferences(self, scope: str, scope_id: str) -> None:
        """Clear all preferences for a specific scope ID."""
        ...

    @abc.abstractmethod
    async def get_command_configs(self) -> list[CommandConfig]:
        """Get all stored command configurations."""
        ...

    @abc.abstractmethod
    async def get_command_config(self, handler_full_name: str) -> CommandConfig | None:
        """Fetch a single command configuration by handler."""
        ...

    @abc.abstractmethod
    async def upsert_command_config(
        self,
        handler_full_name: str,
        plugin_name: str,
        module_path: str,
        original_command: str,
        *,
        resolved_command: str | None = None,
        enabled: bool | None = None,
        keep_original_alias: bool | None = None,
        conflict_key: str | None = None,
        resolution_strategy: str | None = None,
        note: str | None = None,
        extra_data: dict | None = None,
        auto_managed: bool | None = None,
    ) -> CommandConfig:
        """Create or update a command configuration."""
        ...

    @abc.abstractmethod
    async def delete_command_config(self, handler_full_name: str) -> None:
        """Delete a single command configuration."""
        ...

    @abc.abstractmethod
    async def delete_command_configs(self, handler_full_names: list[str]) -> None:
        """Bulk delete command configurations."""
        ...

    @abc.abstractmethod
    async def list_command_conflicts(
        self,
        status: str | None = None,
    ) -> list[CommandConflict]:
        """List recorded command conflict entries."""
        ...

    @abc.abstractmethod
    async def upsert_command_conflict(
        self,
        conflict_key: str,
        handler_full_name: str,
        plugin_name: str,
        *,
        status: str | None = None,
        resolution: str | None = None,
        resolved_command: str | None = None,
        note: str | None = None,
        extra_data: dict | None = None,
        auto_generated: bool | None = None,
    ) -> CommandConflict:
        """Create or update a conflict record."""
        ...

    @abc.abstractmethod
    async def delete_command_conflicts(self, ids: list[int]) -> None:
        """Delete conflict records."""
        ...

    # @abc.abstractmethod
    # async def insert_llm_message(
    #     self,
    #     cid: str,
    #     role: str,
    #     content: list,
    #     tool_calls: list = None,
    #     tool_call_id: str = None,
    #     parent_id: str = None,
    # ) -> LLMMessage:
    #     """Insert a new LLM message into the conversation."""
    #     ...

    # @abc.abstractmethod
    # async def get_llm_messages(self, cid: str) -> list[LLMMessage]:
    #     """Get all LLM messages for a specific conversation."""
    #     ...

    @abc.abstractmethod
    async def get_session_conversations(
        self,
        page: int = 1,
        page_size: int = 20,
        search_query: str | None = None,
        platform: str | None = None,
    ) -> tuple[list[dict], int]:
        """Get paginated session conversations with joined conversation and persona details, support search and platform filter."""
        ...

    # ====
    # Cron Job Management
    # ====

    @abc.abstractmethod
    async def create_cron_job(
        self,
        name: str,
        job_type: str,
        cron_expression: str | None,
        *,
        timezone: str | None = None,
        payload: dict | None = None,
        description: str | None = None,
        enabled: bool = True,
        persistent: bool = True,
        run_once: bool = False,
        status: str | None = None,
        job_id: str | None = None,
    ) -> CronJob:
        """Create and persist a cron job definition."""
        ...

    @abc.abstractmethod
    async def update_cron_job(
        self,
        job_id: str,
        *,
        name: str | None = None,
        cron_expression: str | None = None,
        timezone: str | None = None,
        payload: dict | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        persistent: bool | None = None,
        run_once: bool | None = None,
        status: str | None = None,
        next_run_time: datetime.datetime | None = None,
        last_run_at: datetime.datetime | None = None,
        last_error: str | None = None,
    ) -> CronJob | None:
        """Update fields of a cron job by job_id."""
        ...

    @abc.abstractmethod
    async def delete_cron_job(self, job_id: str) -> None:
        """Delete a cron job by its public job_id."""
        ...

    @abc.abstractmethod
    async def get_cron_job(self, job_id: str) -> CronJob | None:
        """Fetch a cron job by job_id."""
        ...

    @abc.abstractmethod
    async def list_cron_jobs(self, job_type: str | None = None) -> list[CronJob]:
        """List cron jobs, optionally filtered by job_type."""
        ...

    # ====
    # Platform Session Management
    # ====

    @abc.abstractmethod
    async def create_platform_session(
        self,
        creator: str,
        platform_id: str = "webchat",
        session_id: str | None = None,
        display_name: str | None = None,
        is_group: int = 0,
    ) -> PlatformSession:
        """Create a new Platform session."""
        ...

    @abc.abstractmethod
    async def get_platform_session_by_id(
        self, session_id: str
    ) -> PlatformSession | None:
        """Get a Platform session by its ID."""
        ...

    @abc.abstractmethod
    async def get_platform_sessions_by_ids(
        self, session_ids: list[str]
    ) -> list[PlatformSession]:
        """Get platform sessions by IDs."""
        ...

    @abc.abstractmethod
    async def get_platform_sessions_by_creator(
        self,
        creator: str,
        platform_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> list[dict]:
        """Get all Platform sessions for a specific creator (username) and optionally platform.

        Returns a list of dicts containing session info and project info (if session belongs to a project).
        """
        ...

    @abc.abstractmethod
    async def get_platform_sessions_by_creator_paginated(
        self,
        creator: str,
        platform_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
        exclude_project_sessions: bool = False,
        archived: bool | None = None,
        search: str | None = None,
    ) -> tuple[list[dict], int]:
        """Get paginated platform sessions and total count for a creator.

        Args:
            creator: Username owning the sessions.
            platform_id: Optional platform filter.
            page: 1-based page number.
            page_size: Number of sessions per page.
            exclude_project_sessions: When True, hide sessions that belong
                to a ChatUI project.
            archived: When None keep both states; True returns only archived
                sessions; False excludes archived sessions.
            search: Optional case-insensitive substring filter on the
                session display name, applied at the database layer.

        Returns:
            tuple[list[dict], int]: (sessions_with_project_info, total_count)
        """
        ...

    @abc.abstractmethod
    async def update_platform_session(
        self,
        session_id: str,
        display_name: str | None = None,
        archived: int | None = None,
    ) -> None:
        """Update a Platform session's timestamp and optionally other fields.

        Args:
            session_id: Session to update.
            display_name: New display name, or None to keep it.
            archived: New archived flag (0/1), or None to keep it.
        """
        ...

    @abc.abstractmethod
    async def set_platform_session_starred(
        self, session_id: str, starred: int
    ) -> None:
        """Set the starred flag of a Platform session without touching it.

        Args:
            session_id: Session to update.
            starred: New starred flag (1 to star, 0 to unstar).
        """
        ...

    @abc.abstractmethod
    async def delete_platform_session(self, session_id: str) -> None:
        """Delete a Platform session by its ID."""
        ...

    # ====
    # UMO Alias Management
    # ====

    @abc.abstractmethod
    async def upsert_umo_alias(
        self,
        umo: str,
        creator_sender_id: str,
        auto_name: str | None,
        user_alias: str | None,
    ) -> UmoAlias:
        """Create or update the display alias metadata for a UMO."""
        ...

    @abc.abstractmethod
    async def upsert_umo_auto_name(
        self,
        umo: str,
        creator_sender_id: str,
        auto_name: str,
    ) -> None:
        """Create or update only the automatically discovered UMO name.

        Args:
            umo: Unified message origin to name.
            creator_sender_id: Sender that first caused the UMO to be recorded.
            auto_name: Name discovered from the inbound platform message.
        """
        ...

    @abc.abstractmethod
    async def get_umo_alias(self, umo: str) -> UmoAlias | None:
        """Get alias metadata for one UMO."""
        ...

    @abc.abstractmethod
    async def get_umo_aliases(self, umos: list[str] | None = None) -> list[UmoAlias]:
        """Get alias metadata, optionally restricted to the given UMO list."""
        ...

    # ====
    # ChatUI Project Management
    # ====

    @abc.abstractmethod
    async def create_chatui_project(
        self,
        creator: str,
        title: str,
        emoji: str | None = "📁",
        description: str | None = None,
        workspace_type: str = "session",
        workspace_path: str | None = None,
    ) -> ChatUIProject:
        """Create a new ChatUI project."""
        ...

    @abc.abstractmethod
    async def get_chatui_project_by_id(self, project_id: str) -> ChatUIProject | None:
        """Get a ChatUI project by its ID."""
        ...

    @abc.abstractmethod
    async def get_chatui_projects_by_creator(
        self,
        creator: str,
        page: int = 1,
        page_size: int = 100,
    ) -> list[ChatUIProject]:
        """Get all ChatUI projects for a specific creator."""
        ...

    @abc.abstractmethod
    async def update_chatui_project(
        self,
        project_id: str,
        title: str | None = None,
        emoji: str | None = None,
        description: str | None = None,
        workspace_type: str | None = None,
        workspace_path: str | None = None,
    ) -> None:
        """Update a ChatUI project."""
        ...

    @abc.abstractmethod
    async def delete_chatui_project(self, project_id: str) -> None:
        """Delete a ChatUI project by its ID."""
        ...

    @abc.abstractmethod
    async def add_session_to_project(
        self,
        session_id: str,
        project_id: str,
        position: int | None = None,
    ) -> SessionProjectRelation:
        """Add a session to a project, optionally at an ordered position."""
        ...

    @abc.abstractmethod
    async def remove_session_from_project(self, session_id: str) -> None:
        """Remove a session from its project."""
        ...

    @abc.abstractmethod
    async def get_project_sessions(
        self,
        project_id: str,
        page: int = 1,
        page_size: int = 100,
        exclude_archived: bool = False,
    ) -> list[PlatformSession]:
        """Get all sessions in a project.

        Args:
            project_id: Target project.
            page: 1-based page number.
            page_size: Sessions per page.
            exclude_archived: When True, hide archived sessions from the
                project session list.
        """
        ...

    @abc.abstractmethod
    async def get_project_by_session(
        self, session_id: str, creator: str
    ) -> ChatUIProject | None:
        """Get the project that a session belongs to."""
        ...

    # ====
    # Agent Teams
    # ====

    @abc.abstractmethod
    async def create_agent_team(
        self,
        *,
        team_id: str,
        owner_username: str,
        name: str,
        coordinator_member_id: str,
        members: list,
        config: dict,
    ) -> AgentTeam:
        """Create one agent team row."""
        ...

    @abc.abstractmethod
    async def get_agent_team(self, team_id: str) -> AgentTeam | None:
        """Get an agent team by its ID."""
        ...

    @abc.abstractmethod
    async def get_agent_teams_by_owner(self, owner_username: str) -> list[AgentTeam]:
        """Get all agent teams owned by a dashboard user."""
        ...

    @abc.abstractmethod
    async def update_agent_team(self, team_id: str, **updates) -> None:
        """Update an agent team; ``None`` values are skipped."""
        ...

    @abc.abstractmethod
    async def delete_agent_team(self, team_id: str) -> None:
        """Delete an agent team by its ID."""
        ...

    @abc.abstractmethod
    async def create_agent_team_workflow(
        self,
        *,
        workflow_id: str,
        team_id: str,
        name: str,
        graph: dict,
        layout: dict,
    ) -> AgentTeamWorkflow:
        """Create one agent team workflow row."""
        ...

    @abc.abstractmethod
    async def get_agent_team_workflow(
        self, workflow_id: str
    ) -> AgentTeamWorkflow | None:
        """Get an agent team workflow by its ID."""
        ...

    @abc.abstractmethod
    async def get_agent_team_workflows_by_team(
        self, team_id: str
    ) -> list[AgentTeamWorkflow]:
        """Get all workflows saved for a team."""
        ...

    @abc.abstractmethod
    async def update_agent_team_workflow(self, workflow_id: str, **updates) -> None:
        """Update an agent team workflow; ``None`` values are skipped."""
        ...

    @abc.abstractmethod
    async def delete_agent_team_workflow(self, workflow_id: str) -> None:
        """Delete an agent team workflow by its ID."""
        ...

    @abc.abstractmethod
    async def create_agent_team_run(
        self,
        *,
        run_id: str,
        team_id: str,
        workflow_id: str | None,
        mode: str,
        input: str,
        status: str,
        graph_snapshot: dict,
        node_states: dict,
        rounds: list,
    ) -> AgentTeamRun:
        """Create one agent team run row."""
        ...

    @abc.abstractmethod
    async def get_agent_team_run(self, run_id: str) -> AgentTeamRun | None:
        """Get an agent team run by its ID."""
        ...

    @abc.abstractmethod
    async def get_agent_team_runs_by_team(self, team_id: str) -> list[AgentTeamRun]:
        """Get all runs of a team, latest first."""
        ...

    @abc.abstractmethod
    async def get_active_agent_team_run(self, team_id: str) -> AgentTeamRun | None:
        """Get the latest active (running/paused) run of a team, if any."""
        ...

    @abc.abstractmethod
    async def update_agent_team_run(self, run_id: str, **updates) -> None:
        """Update an agent team run; ``None`` values are skipped."""
        ...

    @abc.abstractmethod
    async def get_agent_team_runs_by_status(
        self, statuses: list[str]
    ) -> list[AgentTeamRun]:
        """Get all runs whose status is in the given list, latest first."""
        ...

    @abc.abstractmethod
    async def append_agent_team_run_message(
        self,
        *,
        run_id: str,
        member_id: str,
        node_id: str | None,
        round: int | None,
        turn_id: str,
        direction: str,
        text: str | None,
        parts: list | None,
        metadata: dict | None,
    ) -> AgentTeamRunMessage:
        """Append one transcript row for a run member."""
        ...

    @abc.abstractmethod
    async def get_agent_team_run_transcript(
        self,
        run_id: str,
        member_id: str,
        before_id: int | None,
        limit: int = 50,
    ) -> list[AgentTeamRunMessage]:
        """Get one page of a member's transcript, newest first."""
        ...

    @abc.abstractmethod
    async def trim_agent_team_run_transcript(
        self, run_id: str, member_id: str, keep: int = 500
    ) -> int:
        """Trim a member's transcript, keeping only the newest rows."""
        ...
