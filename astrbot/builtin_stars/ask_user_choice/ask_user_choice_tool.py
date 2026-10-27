# Author: elecvoid243 · Created: 2026-10-25
"""Builtin ``ask_user_choice`` LLM tool (blocking).

The tool blocks until the dashboard user responds, then returns the choice
directly to the LLM as the tool result. Migrated from the
``astrbot_plugin_ask_user`` plugin (v1.0 blocking design).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from astrbot.api import FunctionTool, logger

# Module-level import so tests can monkeypatch this binding.
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.tools.interactive_choice_registry import (
    extract_conversation_id,
    push_resolved_event_to_back_queue,
    registry,
)

if TYPE_CHECKING:
    from astrbot.core.agent.run_context import ContextWrapper

# Field length caps.
_PROMPT_MAX = 200
_TITLE_MAX = 80
_LABEL_MAX = 80
_DESCRIPTION_MAX = 1000
_INPUT_PLACEHOLDER_MAX = 60
_EXTRA_CONTENT_MAX = 5000
_OPTIONS_MIN = 2
_OPTIONS_MAX = 10

# Hard cap on concurrent waiting choices. Not configurable: it only guards
# against a runaway LLM, and 32 is far beyond any interactive use.
_MAX_CONCURRENT_PENDING = 32

_DEFAULT_TIMEOUT_FALLBACK_MESSAGE = (
    "[SYSTEME NOTICE] User did not respond within {timeout} seconds. "
    "Immediately stop all behavior and wait for user's next insturction. "
    "No speculating on user intentions."
)


@dataclass
class AskUserChoiceTool(FunctionTool):
    """``ask_user_choice`` tool: blocking wait for the user's selection."""

    name: str = "ask_user_choice"
    description: str = (
        "Present the user with a question and a set of options to choose from. "
        "Use this when you need the user to make a decision before you can proceed. "
        "This tool blocks until the user responds, then returns their choice. "
        "The user's response is returned directly as this tool's result. "
        "If you want to show analysis, a recommended pick, reasoning, caveats, "
        "or pros/cons of the options, pass them via `extra_content`. "
        "The frontend renders it as Markdown."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Question displayed at the top of the option box",
                },
                "options": {
                    "type": "array",
                    "minItems": _OPTIONS_MIN,
                    "maxItems": _OPTIONS_MAX,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Unique option ID (e.g. A/B/C)",
                            },
                            "label": {
                                "type": "string",
                                "description": "Button text",
                            },
                            "description": {
                                "type": "string",
                                "description": "Optional detail",
                            },
                        },
                        "required": ["id", "label"],
                    },
                },
                "title": {
                    "type": "string",
                    "description": "Optional dialog title",
                },
                "input_placeholder": {
                    "type": "string",
                    "description": "Free-text input placeholder",
                },
                "extra_content": {
                    "type": "string",
                    "description": (
                        "Optional supplementary text shown to the user next to the options. "
                        "Use it for your recommended pick, reasoning, caveats, "
                        "or side-by-side pros/cons of the options. "
                        "The frontend renders it as Markdown (lists, code blocks, links all work). "
                        "The text is NOT echoed back in the tool result — the user already sees it."
                    ),
                },
            },
            "required": ["prompt", "options"],
        }
    )

    def _validate_and_build_spec(self, kwargs: dict) -> dict | str:
        """Validate parameters, apply truncation and build the spec dict.

        Args:
            kwargs: Tool call parameters.

        Returns:
            The spec dict on success, or an error string for the LLM to
            self-correct from (soft error, never an exception).
        """
        prompt = (kwargs.get("prompt") or "").strip()
        if not prompt:
            return "Error: prompt cannot be empty"

        options = kwargs.get("options") or []
        if not isinstance(options, list) or not (
            _OPTIONS_MIN <= len(options) <= _OPTIONS_MAX
        ):
            return (
                f"Error: options must be an array with "
                f"{_OPTIONS_MIN}-{_OPTIONS_MAX} elements."
            )

        normalized = []
        seen = set()
        for idx, opt in enumerate(options):
            if not isinstance(opt, dict):
                return f"Error: options[{idx}] is not an object"
            oid = str(opt.get("id") or "").strip()
            label = str(opt.get("label") or "").strip()
            if not oid or not label:
                return f"Error: options[{idx}] needs id/label"
            if oid in seen:
                return f"Error: duplicate option id: {oid!r}"
            seen.add(oid)
            normalized.append(
                {
                    "id": oid,
                    "label": label[:_LABEL_MAX],
                    "description": (opt.get("description") or "")[:_DESCRIPTION_MAX]
                    or None,
                }
            )

        spec: dict = {
            "type": "interactive_choice",
            "prompt": prompt[:_PROMPT_MAX],
            "options": normalized,
        }
        title = kwargs.get("title")
        if title and title.strip():
            spec["title"] = title.strip()[:_TITLE_MAX]
        placeholder = kwargs.get("input_placeholder")
        if placeholder and placeholder.strip():
            spec["input_placeholder"] = placeholder.strip()[:_INPUT_PLACEHOLDER_MAX]

        extra = kwargs.get("extra_content")
        if extra is not None:
            extra_str = str(extra).strip()
            if extra_str:
                if len(extra_str) > _EXTRA_CONTENT_MAX:
                    logger.warning(
                        f"ask_user_choice: extra_content truncated "
                        f"({len(extra_str)} -> {_EXTRA_CONTENT_MAX} chars)",
                    )
                    extra_str = extra_str[:_EXTRA_CONTENT_MAX]
                spec["extra_content"] = extra_str
        return spec

    async def call(self, context: ContextWrapper, **kwargs: Any) -> str:
        """Block until the user responds, then return the choice to the LLM.

        Args:
            context: AstrBot runtime context.
            **kwargs: Tool parameters (prompt, options, title?,
                input_placeholder?, extra_content?).

        Returns:
            On success: ``"User selected: <label> (id=<id>)
            [\\nAdditional note: <free_text>]"``.
            On timeout: the configured fallback message.
            On cancellation: ``"[User input was cancelled] ..."``.
            On invalid parameters: ``"Error: ..."``.
        """
        # 1. Platform guard.
        event = context.context.event
        umo = event.unified_msg_origin
        if not umo.startswith("webchat:"):
            return (
                "Error: ask_user_choice is only supported in the webchat dashboard. "
                f"Current platform: {umo.split(':', 1)[0]}. "
                "Please open the dashboard to make your selection."
            )

        # The message_id created by chat_service for the SSE stream is the
        # back-queue key it polls; using anything else would orphan the
        # events and the frontend would never see them.
        sse_message_id = str(
            getattr(getattr(event, "message_obj", None), "message_id", "") or ""
        )
        if not sse_message_id:
            return (
                "Error: ask_user_choice requires a non-empty message_id on "
                "the originating webchat event."
            )

        # 2. Parameter validation.
        spec_or_error = self._validate_and_build_spec(kwargs)
        if isinstance(spec_or_error, str):
            return spec_or_error
        spec = spec_or_error

        # 3. Load config (kernel agent_runner settings, read at call time).
        config = self._load_tool_config(context)
        timeout_s = int(config.get("timeout_seconds", 1800))
        fallback_msg = str(
            config.get(
                "timeout_fallback_message",
                _DEFAULT_TIMEOUT_FALLBACK_MESSAGE,
            )
        ).format(timeout=timeout_s)

        # 4. Concurrency cap.
        if len(registry._pending) >= _MAX_CONCURRENT_PENDING:
            return (
                f"Error: too many concurrent interactive choices "
                f"(max {_MAX_CONCURRENT_PENDING}). "
                "Please wait for some to resolve."
            )

        # 5. Register in the registry.
        request_id = str(uuid.uuid4())
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        expires_at = time.time() + timeout_s

        registry.add(
            request_id=request_id,
            umo=umo,
            future=future,
            spec=spec,
            created_at=time.time(),
            timeout_at=expires_at,
            sse_message_id=sse_message_id,
        )

        # 6. Push the interactive_choice event to the frontend SSE stream.
        try:
            await self._push_to_webchat_back_queue(
                request_id=request_id,
                umo=umo,
                spec=spec,
                expires_at=expires_at,
                sse_message_id=sse_message_id,
            )
        except Exception as exc:
            registry.remove(request_id)
            return f"Error: failed to push interactive choice to frontend: {exc}"

        # 7. Block until resolved, cancelled or timed out.
        try:
            user_choice = await asyncio.wait_for(future, timeout=timeout_s)
        except asyncio.TimeoutError:
            # Immediate grey-out on other tabs: broadcast resolved{cancelled};
            # best-effort, the frontend's optimistic countdown and reconcile
            # cover a failed broadcast.
            try:
                await push_resolved_event_to_back_queue(
                    request_id=request_id,
                    umo=umo,
                    reason="cancelled",
                    sse_message_id=sse_message_id,
                )
            except Exception:
                pass
            return fallback_msg
        except asyncio.CancelledError:
            return f"[User input was cancelled] {fallback_msg}"
        finally:
            registry.remove(request_id)

        # 8. Broadcast resolved (failure does not affect the main flow).
        try:
            await push_resolved_event_to_back_queue(
                request_id=request_id,
                umo=umo,
                reason="submitted",
                sse_message_id=sse_message_id,
            )
        except Exception:
            pass

        # 9. Format for the LLM.
        return self._format_choice_for_llm(user_choice, spec)

    def _load_tool_config(self, context: ContextWrapper) -> dict:
        """Read the ``agent_runner.config.ask_user_choice`` section.

        Args:
            context: AstrBot runtime context.

        Returns:
            The config section dict; empty when unavailable (callers fall
            back to the built-in defaults).
        """
        try:
            cfg = context.context.get_config() or {}
            return (
                (cfg.get("agent_runner", {}) or {})
                .get("config", {})
                .get("ask_user_choice", {})
            )
        except Exception:
            return {}

    async def _push_to_webchat_back_queue(
        self,
        request_id: str,
        umo: str,
        spec: dict,
        expires_at: float,
        sse_message_id: str,
    ) -> None:
        """Push the interactive_choice event to the webchat SSE stream.

        Wire format::

            {
                "type": "plain",                     # generic SSE channel
                "chain_type": "interactive_choice",  # chat_service persists it as a part
                "data": "<json string>",             # chat_service expects a string
                "message_id": sse_message_id,
            }

        Args:
            request_id: Unique ID of this interactive request (used by the
                frontend REST resolve API).
            umo: Unified message origin.
            spec: Spec dict from _validate_and_build_spec.
            expires_at: Unix timestamp the frontend counts down to.
            sse_message_id: message_id of the originating webchat event.
        """
        parts = umo.split(":", 2)
        if len(parts) < 3:
            raise ValueError(f"Invalid umo: {umo}")
        conversation_id = extract_conversation_id(umo) or parts[2]

        back_queue = webchat_queue_mgr.get_or_create_back_queue(
            request_id=sse_message_id,
            conversation_id=conversation_id,
        )
        await back_queue.put(
            {
                "type": "plain",
                "chain_type": "interactive_choice",
                "data": json.dumps(
                    {
                        "request_id": request_id,
                        "spec": spec,
                        "expires_at": expires_at,
                        "umo": umo,
                    },
                    ensure_ascii=False,
                ),
                "message_id": sse_message_id,
            }
        )

    def _format_choice_for_llm(self, user_choice: dict, spec: dict) -> str:
        """Format the user response as an LLM-visible string.

        Args:
            user_choice: ``{choice_id, free_text}``; ``choice_id`` may be a
                known option id, ``"__free_text__"`` or any unknown id.
            spec: Spec dict (carries the ``options`` list).

        Returns:
            ``"User selected: <label> (id=<id>)"`` plus
            ``"\\nAdditional note: <free_text>"`` when free text is present.
            The label comes from the matching spec option, falling back to
            the raw choice_id.
        """
        choice_id = str((user_choice or {}).get("choice_id") or "")
        free_text = str((user_choice or {}).get("free_text") or "").strip()
        label = choice_id
        for opt in spec.get("options") or []:
            if isinstance(opt, dict) and opt.get("id") == choice_id:
                label = str(opt.get("label") or choice_id)
                break
        if free_text:
            return (
                f"User selected: {label} (id={choice_id})\nAdditional note: {free_text}"
            )
        return f"User selected: {label} (id={choice_id})"
