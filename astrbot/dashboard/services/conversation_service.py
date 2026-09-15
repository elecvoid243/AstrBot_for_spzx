from __future__ import annotations

import json
import traceback
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from astrbot.core import logger
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.db import BaseDatabase
from astrbot.core.umo_alias import build_umo_alias_map, parse_umo, serialize_umo_alias


class ConversationServiceError(Exception):
    pass


@dataclass
class ConversationExport:
    file_obj: BytesIO
    filename: str
    mimetype: str = "application/jsonl"


class ConversationService:
    def __init__(
        self,
        db_helper: BaseDatabase,
        core_lifecycle: AstrBotCoreLifecycle,
    ) -> None:
        self.db_helper = db_helper
        self.conv_mgr = core_lifecycle.conversation_manager
        self.core_lifecycle = core_lifecycle
        # Message search reads the same platform-message-history store the
        # ChatUI renders from (the legacy conversations table is no longer the
        # source of truth for webchat).
        self.platform_history_mgr = core_lifecycle.platform_message_history_manager

    async def list_conversations(
        self,
        *,
        page: int,
        page_size: int,
        platforms: str,
        message_types: str,
        search_query: str,
        exclude_ids: str,
        exclude_platforms: str,
        keyword_query: str = "",
        umo_query: str = "",
        sort_by: str = "created_at",
        sort_order: str = "desc",
        group_by_session: bool = False,
        include_history: bool = True,
    ) -> dict:
        platform_list = [item.strip() for item in platforms.split(",") if item.strip()]
        message_type_list = [
            item.strip() for item in message_types.split(",") if item.strip()
        ]
        exclude_id_list = [
            item.strip() for item in exclude_ids.split(",") if item.strip()
        ]
        exclude_platform_list = [
            item.strip() for item in exclude_platforms.split(",") if item.strip()
        ]

        page = max(page, 1)
        if page_size < 1:
            page_size = 20
        page_size = min(page_size, 100)

        try:
            conversations, total_count = await self.conv_mgr.get_filtered_conversations(
                page=page,
                page_size=page_size,
                platforms=platform_list,
                message_types=message_type_list,
                search_query=search_query,
                exclude_ids=exclude_id_list,
                exclude_platforms=exclude_platform_list,
                keyword_query=keyword_query.strip(),
                umo_query=umo_query.strip(),
                sort_by=sort_by,
                sort_order=sort_order,
                group_by_session=group_by_session,
                include_history=include_history,
            )
        except Exception as exc:
            logger.error(f"数据库查询出错: {exc!s}\n{traceback.format_exc()}")
            raise ConversationServiceError(f"数据库查询出错: {exc!s}") from exc

        total_pages = (
            (total_count + page_size - 1) // page_size if total_count > 0 else 1
        )
        umos = sorted({conv.user_id for conv in conversations if conv.user_id})
        alias_map = build_umo_alias_map(await self.db_helper.get_umo_aliases(umos))
        webchat_titles = await self._get_webchat_titles(conversations)

        return {
            "conversations": [
                self._serialize_conversation(
                    conversation,
                    alias_map,
                    include_history=include_history,
                    webchat_title=webchat_titles.get(conversation.user_id, ""),
                )
                for conversation in conversations
            ],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total_count,
                "total_pages": total_pages,
                "grouped_by_session": group_by_session,
            },
        }

    async def search_messages(
        self,
        *,
        q: str,
        page: int,
        page_size: int,
        username: str,
    ) -> dict:
        """Search message content across the current user's chat sessions.

        The ChatUI sidebar and message list are backed by the platform-session
        and platform-message-history tables, NOT the legacy conversations
        table. A clickable result must therefore carry a platform
        ``session_id`` and a ``message_index`` that line up with what
        ``ChatService.get_session`` returns; otherwise clicking a result lands
        on an empty "new chat" view (the old code returned a conversation
        ``cid`` that the chat-session read path cannot resolve).

        To guarantee that alignment this method iterates the same sessions the
        sidebar shows (``get_platform_sessions_by_creator_paginated`` for the
        authenticated user) and reads each session's history with the exact
        call ``get_session`` uses (``platform_history_mgr.get(..., page=1,
        page_size=1000)``). The enumerate index over that list is the
        ``message_index``, which matches the frontend ``data-message-index``
        because the read path is order-preserving and 1:1 (the sanitizer keeps
        order/length and the frontend maps history to records without
        filtering).

        Args:
            q: Search keyword (required, min 1 char).
            page: Page number (1-based).
            page_size: Items per page.
            username: Authenticated dashboard user; only their sessions are
                searched, mirroring the sidebar and avoiding cross-user leaks.

        Returns:
            Dict with 'results' (session groups with matches) and
            'pagination' metadata.
        """
        q = q.strip()
        if not q:
            return {
                "results": [],
                "pagination": {
                    "page": 1,
                    "page_size": page_size,
                    "total": 0,
                    "total_pages": 1,
                },
            }

        page = max(page, 1)
        page_size = max(1, min(page_size, 50))

        # Collect the user's sessions (same source as the sidebar). The
        # accessor is paginated, so loop; cap the scan to avoid runaway work.
        all_sessions: list = []
        scan_page = 1
        scan_limit = 1000
        while len(all_sessions) < scan_limit:
            try:
                (
                    batch,
                    _total,
                ) = await self.db_helper.get_platform_sessions_by_creator_paginated(
                    creator=username,
                    page=scan_page,
                    page_size=200,
                    # 2026-08-13: the sidebar search must only cover
                    # non-archived sessions.
                    archived=False,
                )
            except Exception as exc:
                logger.error(f"消息搜索查询会话出错: {exc!s}\n{traceback.format_exc()}")
                raise ConversationServiceError(
                    f"消息搜索查询会话出错: {exc!s}"
                ) from exc
            # Each row is {"session": <PlatformSession>, ...}; unwrap it.
            for item in batch:
                session = item.get("session") if isinstance(item, dict) else item
                if session is not None:
                    all_sessions.append(session)
            if len(batch) < 200:
                break
            scan_page += 1

        lower_q = q.lower()
        grouped_results: dict[str, dict] = {}

        for session in all_sessions:
            session_id = getattr(session, "session_id", "") or ""
            platform_id = getattr(session, "platform_id", "") or ""
            if not session_id:
                continue

            # Same read call as ChatService.get_session so indices align.
            try:
                history_ls = await self.platform_history_mgr.get(
                    platform_id=platform_id,
                    user_id=session_id,
                    page=1,
                    page_size=1000,
                )
            except Exception as exc:
                # One corrupt session must not break the whole search.
                logger.warning(f"消息搜索读取历史失败 session={session_id}: {exc!s}")
                continue

            conv_matches: list[dict] = []
            for idx, record in enumerate(history_ls):
                content = self._record_content(record)
                if not isinstance(content, dict):
                    continue
                ctype = content.get("type")
                if ctype == "user":
                    role = "user"
                elif ctype == "bot":
                    role = "assistant"
                else:
                    # system / branch_info / etc. carry no searchable prose.
                    continue

                full_text = self._extract_search_text(content)
                if not full_text:
                    continue

                lower_text = full_text.lower()
                pos = lower_text.find(lower_q)
                if pos == -1:
                    continue

                ctx_len = 50
                start = max(0, pos - ctx_len)
                end = min(len(full_text), pos + len(q) + ctx_len)
                snippet = full_text[start:end]

                if start > 0 and snippet[0] != " ":
                    space = snippet.find(" ", 10)
                    if space != -1:
                        snippet = snippet[space + 1 :]
                if end < len(full_text) and snippet[-1] != " ":
                    space = snippet.rfind(" ", -20)
                    if space != -1:
                        snippet = snippet[: space + 1]

                if start > 0:
                    snippet = "..." + snippet
                if end < len(full_text):
                    snippet = snippet + "..."

                match_offset = snippet.lower().find(lower_q)
                conv_matches.append(
                    {
                        "message_index": idx,
                        "role": role,
                        "snippet": snippet,
                        "match_offset": max(0, match_offset),
                    }
                )

            if conv_matches:
                display_sid = session_id[:8] if len(session_id) >= 8 else session_id
                title = getattr(session, "display_name", "") or f"会话 {display_sid}"
                grouped_results[session_id] = {
                    "session_id": session_id,
                    "title": title,
                    "updated_at": self._session_updated_at(session),
                    "matches": conv_matches,
                }

        sorted_results = sorted(
            grouped_results.values(),
            key=lambda x: x["updated_at"],
            reverse=True,
        )

        total = len(sorted_results)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        offset = (page - 1) * page_size
        page_results = sorted_results[offset : offset + page_size]

        return {
            "results": page_results,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
        }

    @staticmethod
    def _record_content(record: object) -> object:
        """Return the dict content of a platform-history record.

        The read path yields objects whose ``content`` is already a dict, but
        fall back to ``model_dump()`` for pydantic-style records and to
        ``json.loads`` if a backend ever stores it as a JSON string.
        """
        content = getattr(record, "content", None)
        if isinstance(content, dict):
            return content
        dumped = getattr(record, "model_dump", None)
        if callable(dumped):
            try:
                content = dumped().get("content")
            except Exception:
                content = None
        if isinstance(content, dict):
            return content
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def _extract_search_text(content: dict) -> str:
        """Collect user-visible prose from a history content dict.

        Mirrors what the ChatUI renders: the ``text`` of plain / markdown
        parts and the ``think`` of reasoning parts, joined into one string.
        Non-text parts (images, tool calls, interactive boxes) are skipped so
        searching matches what the user actually reads.
        """
        parts = content.get("message")
        if isinstance(parts, str):
            return parts
        if not isinstance(parts, list):
            return ""
        chunks: list[str] = []
        for part in parts:
            if isinstance(part, str):
                chunks.append(part)
                continue
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text:
                chunks.append(text)
            think = part.get("think")
            if isinstance(think, str) and think:
                chunks.append(think)
        return " ".join(chunks)

    @staticmethod
    def _session_updated_at(session: object) -> int:
        """Best-effort epoch seconds for a session's updated_at, for sorting."""
        raw = getattr(session, "updated_at", None)
        if raw is None:
            return 0
        ts = getattr(raw, "timestamp", None)
        if callable(ts):
            try:
                return int(ts())
            except Exception:
                return 0
        if isinstance(raw, (int, float)):
            return int(raw)
        return 0

    async def get_filter_options(self) -> dict:
        """Build robot filter options from configured conversation platforms.

        Returns:
            Robot IDs and adapter types that exist in both configuration and
            conversation history.
        """
        history_platform_ids = set(await self.db_helper.get_conversation_platform_ids())
        configured_platforms = self.core_lifecycle.astrbot_config.get("platform", [])
        bots = [
            {
                "id": str(platform.get("id", "")),
                "type": str(platform.get("type", "")),
            }
            for platform in configured_platforms
            if platform.get("id") in history_platform_ids
        ]

        # WebChat is a built-in platform that the platform manager always
        # starts regardless of config, so expose it as a filterable bot ID even
        # when it is missing from the configured platform list.
        if "webchat" in history_platform_ids and not any(
            bot["id"] == "webchat" for bot in bots
        ):
            bots.append({"id": "webchat", "type": "webchat"})

        return {"bots": bots}

    async def get_conversation_detail(self, data: object) -> dict:
        payload = self._payload(data)
        user_id, cid = self._require_user_and_cid(payload)

        conversation = await self.conv_mgr.get_conversation(
            unified_msg_origin=user_id,
            conversation_id=cid,
        )
        if not conversation:
            raise ConversationServiceError("对话不存在")

        webchat_titles = await self._get_webchat_titles([conversation])
        alias_map = build_umo_alias_map(await self.db_helper.get_umo_aliases([user_id]))
        return {
            "user_id": user_id,
            "cid": cid,
            "title": conversation.title
            or webchat_titles.get(conversation.user_id, "")
            or None,
            "persona_id": conversation.persona_id,
            "history": conversation.history,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
            "umo_info": self._build_umo_info(user_id, alias_map),
        }

    async def update_conversation(self, data: object) -> dict:
        payload = self._payload(data)
        user_id, cid = self._require_user_and_cid(payload)
        title = payload.get("title")

        conversation = await self.conv_mgr.get_conversation(
            unified_msg_origin=user_id,
            conversation_id=cid,
        )
        if not conversation:
            raise ConversationServiceError("对话不存在")

        persona_id = payload.get("persona_id", conversation.persona_id)

        if title is not None or persona_id is not None:
            await self.conv_mgr.update_conversation(
                unified_msg_origin=user_id,
                conversation_id=cid,
                title=title,
                persona_id=persona_id,
            )
        return {"message": "对话信息更新成功"}

    async def delete_conversation(self, data: object) -> dict:
        payload = self._payload(data)
        if "conversations" in payload:
            return await self._delete_conversations(payload.get("conversations", []))

        user_id, cid = self._require_user_and_cid(payload)
        await self.conv_mgr.delete_conversation(
            unified_msg_origin=user_id,
            conversation_id=cid,
        )
        return {"message": "对话删除成功"}

    async def update_history(self, data: object) -> dict:
        payload = self._payload(data)
        user_id, cid = self._require_user_and_cid(payload)
        history = payload.get("history")

        if history is None:
            raise ConversationServiceError("缺少必要参数: history")

        history = self._normalize_history(history)

        conversation = await self.conv_mgr.get_conversation(
            unified_msg_origin=user_id,
            conversation_id=cid,
        )
        if not conversation:
            raise ConversationServiceError("对话不存在")

        await self.conv_mgr.update_conversation(
            unified_msg_origin=user_id,
            conversation_id=cid,
            history=history,
        )

        return {"message": "对话历史更新成功"}

    async def export_conversations(self, data: object) -> ConversationExport:
        payload = self._payload(data)
        conversations_to_export = payload.get("conversations", [])

        if not conversations_to_export:
            raise ConversationServiceError("导出列表不能为空")

        jsonl_lines = []
        exported_count = 0
        failed_items = []

        for conv_info in conversations_to_export:
            user_id = conv_info.get("user_id")
            cid = conv_info.get("cid")

            if not user_id or not cid:
                failed_items.append(f"user_id:{user_id}, cid:{cid} - 缺少必要参数")
                continue

            try:
                conversation = await self.conv_mgr.get_conversation(
                    unified_msg_origin=user_id,
                    conversation_id=cid,
                )

                if not conversation:
                    failed_items.append(f"user_id:{user_id}, cid:{cid} - 对话不存在")
                    continue

                webchat_titles = await self._get_webchat_titles([conversation])
                content = json.loads(conversation.history)
                export_record = {
                    "cid": cid,
                    "user_id": user_id,
                    "platform_id": conversation.platform_id,
                    "title": conversation.title
                    or webchat_titles.get(conversation.user_id, "")
                    or None,
                    "persona_id": conversation.persona_id,
                    "created_at": conversation.created_at,
                    "updated_at": conversation.updated_at,
                    "content": content,
                }
                jsonl_lines.append(json.dumps(export_record, ensure_ascii=False))
                exported_count += 1
            except Exception as exc:
                failed_items.append(f"user_id:{user_id}, cid:{cid} - {exc!s}")
                logger.error(
                    f"导出对话失败: user_id={user_id}, cid={cid}, error={exc!s}"
                )

        if exported_count == 0:
            raise ConversationServiceError("没有成功导出任何对话")

        jsonl_content = "\n".join(jsonl_lines)
        file_obj = BytesIO(jsonl_content.encode("utf-8"))
        file_obj.seek(0)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return ConversationExport(
            file_obj=file_obj,
            filename=f"astrbot_conversations_export_{timestamp}.jsonl",
        )

    async def _delete_conversations(self, conversations: object) -> dict:
        if not isinstance(conversations, list) or not conversations:
            raise ConversationServiceError("批量删除时conversations参数不能为空")

        deleted_count = 0
        failed_items = []

        for conv in conversations:
            if not isinstance(conv, dict):
                failed_items.append(f"{conv!r} - 格式错误")
                continue
            user_id = conv.get("user_id")
            cid = conv.get("cid")

            if not user_id or not cid:
                failed_items.append(f"user_id:{user_id}, cid:{cid} - 缺少必要参数")
                continue

            try:
                await self.conv_mgr.delete_conversation(
                    unified_msg_origin=user_id,
                    conversation_id=cid,
                )
                deleted_count += 1
            except Exception as exc:
                failed_items.append(f"user_id:{user_id}, cid:{cid} - {exc!s}")

        message = f"成功删除 {deleted_count} 个对话"
        if failed_items:
            message += f"，失败 {len(failed_items)} 个"

        return {
            "message": message,
            "deleted_count": deleted_count,
            "failed_count": len(failed_items),
            "failed_items": failed_items,
        }

    @staticmethod
    def _webchat_session_id(user_id: str | None) -> str:
        """Extract the WebChat session ID from a unified message origin.

        Args:
            user_id: Unified message origin such as
                ``webchat:FriendMessage:webchat!creator!session_id``.

        Returns:
            The trailing session ID segment, or an empty string when the
            origin does not carry one.
        """
        umo = user_id or ""
        if "!" not in umo:
            return ""
        return umo.rsplit("!", 1)[-1]

    async def _get_webchat_titles(self, conversations) -> dict[str, str]:
        """Resolve WebChat session titles for conversations.

        WebChat generates and stores its title on the platform session while
        the conversation history reads the conversation title column, so the
        session display name is used as a fallback. Title lookup failures only
        degrade the fallback instead of failing the whole request.

        Args:
            conversations: Conversation objects returned by the conversation manager.

        Returns:
            Mapping from unified message origin to the WebChat session title.
        """
        session_ids: dict[str, str] = {}
        for conversation in conversations:
            if conversation.platform_id != "webchat":
                continue
            session_id = self._webchat_session_id(conversation.user_id)
            if session_id:
                session_ids[conversation.user_id] = session_id
        if not session_ids:
            return {}

        try:
            sessions = await self.db_helper.get_platform_sessions_by_ids(
                list(set(session_ids.values())),
            )
        except Exception as exc:
            logger.warning(f"查询 WebChat 会话标题失败: {exc!s}")
            return {}

        display_names = {
            session.session_id: session.display_name
            for session in sessions
            if session.display_name
        }
        return {
            user_id: display_names.get(session_id, "")
            for user_id, session_id in session_ids.items()
        }

    def _serialize_conversation(
        self,
        conversation,
        alias_map: dict,
        *,
        include_history: bool,
        webchat_title: str = "",
    ) -> dict:
        """Serialize a conversation for a list response.

        Args:
            conversation: Conversation object returned by the manager.
            alias_map: UMO aliases keyed by unified message origin.
            include_history: Whether to include the serialized message history.
            webchat_title: WebChat session title used when the conversation
                itself has no title.

        Returns:
            Conversation data suitable for a dashboard API response.
        """
        result = {
            "platform_id": conversation.platform_id,
            "user_id": conversation.user_id,
            "cid": conversation.cid,
            "title": conversation.title or webchat_title or None,
            "persona_id": conversation.persona_id,
            "token_usage": conversation.token_usage,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
            "umo_info": self._build_umo_info(conversation.user_id, alias_map),
        }
        if include_history:
            result["history"] = conversation.history
        return result

    @staticmethod
    def _build_umo_info(umo: str | None, alias_map: dict) -> dict:
        umo_str = umo or ""
        return {
            "umo": umo_str,
            **parse_umo(umo_str),
            **serialize_umo_alias(alias_map.get(umo_str), umo_str),
        }

    @staticmethod
    def _require_user_and_cid(payload: dict) -> tuple[str, str]:
        user_id = payload.get("user_id")
        cid = payload.get("cid")
        if not user_id or not cid:
            raise ConversationServiceError("缺少必要参数: user_id 和 cid")
        return user_id, cid

    @staticmethod
    def _normalize_history(history):
        try:
            if isinstance(history, list):
                history = json.dumps(history)
            else:
                json.loads(history)
        except json.JSONDecodeError as exc:
            raise ConversationServiceError(
                "history 必须是有效的 JSON 字符串或数组"
            ) from exc

        return json.loads(history) if isinstance(history, str) else history

    @staticmethod
    def _payload(data: object) -> dict:
        return data if isinstance(data, dict) else {}
