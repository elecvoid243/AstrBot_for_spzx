# Author: elecvoid243 · Created: 2026-10-25
"""Builtin /compact command — manual LLM-summary context compression.

The compression behavior is identical to the automatic LLM compression: the
same request assembly (``build_main_agent``), the same configuration sources
(``agent_runner.config.compression``) and the same ``LLMSummaryCompressor``.
The only difference is the trigger: a manual command fires the compressor
directly, bypassing the ``should_compress`` threshold gate.

Migrated from the ``astrbot_plugin_compact`` plugin (single-command form;
the run/preview/status/config/set subcommands and per-invocation overrides
were dropped in favor of the kernel compression settings).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from astrbot import logger
from astrbot.api import star
from astrbot.api.all import MessageChain, MessageEventResult
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.agent.context.compressor import LLMSummaryCompressor
from astrbot.core.agent.context.token_counter import EstimateTokenCounter
from astrbot.core.agent.message import (
    Message,
    bind_checkpoint_messages,
    dump_messages_with_checkpoints,
)
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent

if TYPE_CHECKING:
    from astrbot.core.agent.tool import ToolSet
    from astrbot.core.provider.provider import Provider


class Main(star.Star):
    """Builtin /compact command surface."""

    def __init__(self, context: star.Context) -> None:
        self.context = context

    @filter.command("compact")
    async def compact(self, event: AstrMessageEvent) -> None:
        """Compress the current conversation history on demand.

        Any text after the command is ignored; behavior and settings come
        from the kernel compression configuration.
        """
        umo = event.unified_msg_origin

        provider = self._resolve_provider(umo)
        if provider is None:
            event.set_result(
                MessageEventResult().message(
                    "未找到可用的 LLM provider，无法压缩。请检查当前会话是否绑定了"
                    " provider，或在 WebUI 中配置压缩专用的 provider。",
                ),
            )
            return

        cid = await self.context.conversation_manager.get_curr_conversation_id(umo)
        if not cid:
            event.set_result(
                MessageEventResult().message(
                    "当前没有进行中的会话，请先发送一条消息。"
                ),
            )
            return

        conv = await self.context.conversation_manager.get_conversation(umo, cid)
        compression_config = self._core_compression_config(umo)

        # Rebuild the same request context the automatic compression flow
        # sees ([system prompt] + history + chat tool set + llm params) so
        # the summary LLM request shares its token prefix with previous chat
        # requests and hits the provider's prefix cache. On assembly failure,
        # fall back to the raw stored history.
        assembled = await self._assemble_builtin_request(event, umo)
        if assembled is not None:
            contexts, system_prompt, chat_func_tool, llm_params = assembled
        else:
            contexts, system_prompt, chat_func_tool, llm_params = (
                self._load_history_dicts(conv),
                "",
                None,
                {},
            )
        messages = bind_checkpoint_messages(contexts)
        if system_prompt:
            messages.insert(0, Message(role="system", content=system_prompt))

        try:
            await event.send(MessageChain().message("⏳ 正在压缩对话历史，请稍候..."))
        except Exception:
            logger.warning(
                "compact: failed to send processing message, continuing anyway",
            )

        compressor = LLMSummaryCompressor(
            provider=provider,
            keep_recent_ratio=float(
                compression_config.get("keep_recent_ratio", 0.15),
            ),
            instruction_text=str(compression_config.get("instruction", "")) or None,
            token_counter=EstimateTokenCounter(),
            llm_params=llm_params,
        )
        try:
            new_messages = await compressor(messages, func_tool=chat_func_tool)
        except TypeError:
            # Compressors implementing the legacy single-argument protocol.
            new_messages = await compressor(messages)
        except Exception as e:
            logger.error(f"compact: unexpected error during compression: {e}")
            event.set_result(MessageEventResult().message(f"压缩失败:{e}"))
            return

        # The built-in flow persists history WITHOUT the leading system prompt
        # (it is re-assembled per request), so strip it before writing back —
        # otherwise the next chat request would carry a duplicated system
        # prompt.
        messages_to_save: list[Message] = []
        skipped_initial_system = False
        for m in new_messages:
            if m.role == "system" and not skipped_initial_system:
                skipped_initial_system = True
                continue
            messages_to_save.append(m)
        new_history = dump_messages_with_checkpoints(messages_to_save)
        await self.context.conversation_manager.update_conversation(
            umo,
            cid,
            new_history,
        )

        # The inner LLMSummaryCompressor swallows LLM errors and returns the
        # original list unchanged; compare to detect a no-op compression.
        if new_messages != messages:
            event.set_result(
                MessageEventResult().message(
                    f"✅ 压缩完成:{len(messages)} → {len(new_messages)} 条消息。",
                ),
            )
        else:
            event.set_result(
                MessageEventResult().message(
                    "ℹ️ 压缩未生效(LLM 未返回有效摘要)，历史保持不变。",
                ),
            )

    def _resolve_provider(self, umo: str) -> Provider | None:
        """Resolve the provider for compression.

        Follows the automatic compression flow: the core compression setting
        (``agent_runner.config.compression.provider_id``) first, then the
        current chat provider of the session.

        Args:
            umo: Unified message origin (session id).

        Returns:
            The resolved provider, or None when neither source yields one.
        """
        provider_id = str(self._core_compression_config(umo).get("provider_id", ""))
        if provider_id:
            provider = self.context.get_provider_by_id(provider_id)
            if provider is not None:
                return provider
        return self.context.get_using_provider(umo=umo)

    def _core_compression_config(self, umo: str) -> dict:
        """Read the core compression config for a session.

        Args:
            umo: Unified message origin (session id).

        Returns:
            The ``agent_runner.config.compression`` dict of the umo-resolved
            core config, or an empty dict when unavailable.
        """
        try:
            cfg = self.context.get_config(umo=umo) or {}
            return (
                (cfg.get("agent_runner", {}) or {})
                .get("config", {})
                .get("compression", {})
            )
        except Exception:
            return {}

    async def _assemble_builtin_request(
        self,
        event: AstrMessageEvent,
        umo: str,
    ) -> tuple[list, str, ToolSet | None, dict] | None:
        """Rebuild the request context exactly as the automatic chat flow does.

        The automatic compression runs inside the agent runner on
        ``[system prompt] + history + current user message``, where the
        system prompt is assembled by ``build_main_agent`` (persona, skills,
        safety mode, tool prompts...). ``/compact`` runs outside that flow,
        so this method constructs ``MainAgentBuildConfig`` the same way the
        agent sub stage does and calls ``build_main_agent`` to obtain the
        byte-identical contexts and system prompt.

        Args:
            event: The current ``/compact`` command event.
            umo: Unified message origin (session id).

        Returns:
            ``(contexts, system_prompt, func_tool, llm_params)`` where
            contexts is the list the runner would receive as
            ``request.contexts`` (including persona begin dialogs), func_tool
            is the chat tool set the runner would send, and llm_params are
            the per-request LLM parameters. None when assembly fails — the
            caller then falls back to the raw stored history.
        """
        from astrbot.core.pipeline.context_utils import call_event_hook
        from astrbot.core.star.star_handler import EventType

        conf = self.context.get_config(umo=umo) or {}
        settings = conf.get("provider_settings", {}) or {}
        runner_config = (conf.get("agent_runner", {}) or {}).get("config", {}) or {}
        persona_config = runner_config.get("persona", {}) or {}
        misc_config = runner_config.get("misc", {}) or {}
        file_extract_conf = settings.get("file_extract", {}) or {}
        build_config = MainAgentBuildConfig(
            tool_call_timeout=misc_config.get("tool_call_timeout", 120),
            tool_schema_mode=misc_config.get("tool_schema_mode", "full"),
            kb_agentic_mode=conf.get("kb_agentic_mode", False),
            file_extract_enabled=file_extract_conf.get("enable", False),
            file_extract_prov=file_extract_conf.get("provider", "moonshotai"),
            file_extract_msh_api_key=file_extract_conf.get("moonshotai_api_key", ""),
            llm_safety_mode=persona_config.get("safety_mode", True),
            safety_mode_strategy=persona_config.get(
                "safety_mode_strategy",
                "system_prompt",
            ),
            computer_use_runtime=settings.get("computer_use_runtime"),
            sandbox_cfg=settings.get("sandbox", {}) or {},
            add_cron_tools=(settings.get("proactive_capability", {}) or {}).get(
                "add_cron_tools",
                True,
            ),
            provider_settings={
                **settings,
                "default_personality": persona_config.get("persona_id", "default"),
            },
            timezone=conf.get("timezone"),
            max_quoted_fallback_images=settings.get("max_quoted_fallback_images", 20),
        )
        try:
            result = await build_main_agent(
                event=event,
                plugin_context=self.context,
                config=build_config,
                apply_reset=False,
            )
        except Exception as e:
            logger.warning(
                f"compact: built-in request assembly failed, "
                f"falling back to raw history: {e}",
            )
            return None
        if result is None or result.provider_request is None:
            return None
        # reset() created an un-awaited coroutine (apply_reset=False); close it
        # so no "coroutine was never awaited" warning surfaces at GC time.
        if result.reset_coro is not None:
            result.reset_coro.close()
        req = result.provider_request
        # Replay the OnLLMRequestEvent hooks exactly as the chat pipeline does
        # before its runner consumes the request: plugins inject system-prompt
        # sections / tools / extra parts there, and skipping them makes the
        # rebuilt context diverge from the chat requests at token 0 (provider
        # prefix cache full miss).
        if await call_event_hook(event, EventType.OnLLMRequestEvent, req):
            logger.warning(
                "compact: an OnLLMRequestEvent hook stopped the event; the "
                "rebuilt context may not match the chat requests",
            )
        contexts = req.contexts
        if isinstance(contexts, str):
            contexts = json.loads(contexts)

        # Mirror the runner's request-time tool handling so the summary
        # request's tools section is byte-identical to previous chat requests
        # (provider prefix caches cover the tools block): skills_like mode
        # swaps in the light schema set, and providers without tool_use send
        # no tools at all.
        chat_func_tool = req.func_tool
        if (
            chat_func_tool is not None
            and build_config.tool_schema_mode == "skills_like"
        ):
            chat_func_tool = chat_func_tool.get_light_tool_set()
        modalities = (result.provider.provider_config or {}).get("modalities", None)
        if (
            chat_func_tool is not None
            and isinstance(modalities, list)
            and modalities
            and "tool_use" not in modalities
        ):
            chat_func_tool = None

        # Defense in depth: the summary is a one-shot text_chat with no tool
        # execution loop, but mark every tool denied on the assembled request
        # so the core permission gate holds even if this request is ever run
        # through an agent loop.
        if req.func_tool:
            req.denied_tools = set(req.func_tool.names())

        # The chat flow sends req.llm_params (e.g. thinking_effort) with every
        # request and providers derive payload fields from them (such as
        # reasoning_effort); the summary request must carry the same values or
        # the prefix cache diverges on backends that render them into the
        # prompt. Defensive extraction: older cores / mocks may not set it.
        llm_params = getattr(req, "llm_params", None)
        return (
            list(contexts or []),
            req.system_prompt or "",
            chat_func_tool,
            dict(llm_params) if isinstance(llm_params, dict) else {},
        )

    @staticmethod
    def _load_history_dicts(conv) -> list:
        """Load raw history dicts from a Conversation-like object.

        ``conv.history`` may be stored as a JSON-encoded string; parsing it
        here keeps ``bind_checkpoint_messages`` from iterating over
        individual characters when the storage is a string.

        Args:
            conv: A conversation object (or None).

        Returns:
            A list of message dicts. Empty when the conversation is missing
            or the history is malformed.
        """
        raw = conv.history if conv is not None else None
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                return []
        if isinstance(raw, list):
            return raw
        return []
