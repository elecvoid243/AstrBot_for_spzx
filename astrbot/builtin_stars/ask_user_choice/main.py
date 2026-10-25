# Author: elecvoid243 · Created: 2026-10-25
"""Builtin ask_user_choice surface.

On load (when enabled):
- registers :class:`AskUserChoiceTool` into the LLM tool list;
- installs a priority webchat message interceptor that consumes a plain
  text message as the pending choice's free-text answer instead of
  triggering a new LLM request.

The REST endpoints live in the dashboard chat API; the pending registry and
SSE push helpers live in ``astrbot.core.tools.interactive_choice_registry``.

Migrated from the ``astrbot_plugin_ask_user`` plugin.
"""

from __future__ import annotations

from astrbot import logger
from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.builtin_stars.ask_user_choice.ask_user_choice_tool import AskUserChoiceTool
from astrbot.core.tools.interactive_choice_registry import (
    push_resolved_event_to_back_queue,
    registry,
)


class Main(star.Star):
    """Builtin ask_user_choice command/tool surface."""

    def __init__(self, context: star.Context) -> None:
        self.context = context

    async def initialize(self) -> None:
        """Register the tool unless disabled by configuration."""
        try:
            cfg = self.context.get_config() or {}
            enabled = bool(
                ((cfg.get("agent_runner", {}) or {}).get("config", {}) or {})
                .get(
                    "ask_user_choice",
                    {},
                )
                .get("enabled", True)
            )
        except Exception:
            enabled = True
        if not enabled:
            logger.info(
                "ask_user_choice tool disabled (agent_runner.config.ask_user_choice."
                "enabled=false); skipping registration",
            )
            return
        self.context.add_llm_tools(AskUserChoiceTool())

    async def terminate(self) -> None:
        """Cancel every pending future in the registry."""
        await registry.shutdown()
        logger.info("ask_user_choice: registry shut down")

    @filter.platform_adapter_type(filter.PlatformAdapterType.ALL, priority=10)
    async def on_message(self, event: AstrMessageEvent) -> None:
        """Consume a webchat message as a pending choice's free-text answer.

        When the user replies with plain text instead of clicking an option
        button, that message becomes the ``ask_user_choice`` tool's result
        rather than a new LLM request.

        Priority 10 keeps this ahead of the builtin command handlers.
        """
        umo = event.unified_msg_origin
        if not umo.startswith("webchat:"):
            return

        pending = registry.find_pending_by_umo(umo)
        if pending is None:
            return

        message_text = (event.message_str or "").strip()
        if not message_text:
            return

        logger.info(
            f"ask_user_choice: consuming message '{message_text[:60]}' "
            f"as the pending choice response for session {umo[:80]}",
        )

        registry.resolve(
            pending.request_id,
            {"choice_id": "__free_text__", "free_text": message_text},
        )

        # Push the SSE resolved event so the frontend closes the option box.
        try:
            await push_resolved_event_to_back_queue(
                request_id=pending.request_id,
                umo=umo,
                reason="submitted",
                sse_message_id=pending.sse_message_id,
            )
        except Exception:
            pass

        # The message has been consumed: no LLM request, stop propagation.
        event.should_call_llm(False)
        event.stop_event()
