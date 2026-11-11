"""Turn the per-conversation file access mode into request-time effects.

The mode itself is owned by :mod:`astrbot.core.tools.fs_access` (per-umo,
persisted). This module adds the two things the mode needs at LLM-request time:

- readonly: merge the configured write tools into ``req.denied_tools``. The
  runner rejects them at execution time while the tool schema stays byte
  identical, so provider prefix caches are not invalidated.
- every mode: append a short guidance block to ``req.extra_user_content_parts``.
  It is stored with the conversation (``mark_as_temp`` is deliberately not
  called), so the model keeps seeing the constraint after history truncation.
"""

from __future__ import annotations

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.agent.message import TextPart
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.tools import fs_access


def _already_injected(req: ProviderRequest, marker: str) -> bool:
    """Return whether a previous pass already added this mode's guidance."""
    for part in req.extra_user_content_parts:
        if isinstance(part, dict):
            text = part.get("text")
        else:
            text = getattr(part, "text", None)
        if isinstance(text, str) and marker in text:
            return True
    return False


async def inject_file_access_guidance(
    context, event: AstrMessageEvent, req: ProviderRequest
) -> None:
    """Apply readonly tool denials and mode guidance to one LLM request.

    Args:
        context: Star context used to read the effective config.
        event: Message event carrying the unified message origin.
        req: Request about to be handed to the agent runner.
    """
    umo = event.unified_msg_origin
    try:
        cfg = context.get_config(umo=umo)
    except Exception:
        # Failing to read config must not break the request. The tool layer and
        # the OS sandbox still enforce the mode; only the prompt hint is lost.
        logger.warning(
            "[fs_access] failed to read config for %s", umo, exc_info=True
        )
        return

    await fs_access.load_mode_for_umo(umo)
    mode = fs_access.get_mode_for_umo(umo, default=fs_access.resolve_default(cfg))

    if mode is fs_access.FileAccessMode.READONLY:
        blocked = fs_access.readonly_blocked_tools(cfg)
        if blocked:
            req.denied_tools = set(req.denied_tools or ()) | set(blocked)

    guidance = fs_access.mode_guidance(mode)
    marker = guidance.splitlines()[0]
    if _already_injected(req, marker):
        return
    req.extra_user_content_parts.append(TextPart(text=guidance))
