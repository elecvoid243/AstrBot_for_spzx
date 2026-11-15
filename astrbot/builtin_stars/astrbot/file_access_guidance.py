"""Turn the per-conversation file access mode into request-time effects.

The mode itself is owned by :mod:`astrbot.core.tools.fs_access` (per-umo,
persisted). This module adds the two things the mode needs at LLM-request time:

- readonly: merge the configured write tools into ``req.denied_tools``. The
  runner rejects them at execution time while the tool schema stays byte
  identical, so provider prefix caches are not invalidated.
- every mode: append the guidance to ``req.extra_user_content_parts``. It is
  stored with the conversation (``mark_as_temp`` is deliberately not called),
  so the model keeps seeing the constraint after history truncation and the
  provider prefix cache stays warm.
- workspace mode: the guidance embeds the concrete writable roots. Dedupe is
  an exact-text match, so an unchanged whitelist is not appended again while an
  edited one produces a new block and takes effect on the next request.
"""

from __future__ import annotations

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.agent.message import TextPart
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.provider.request_snapshot import record_provenance
from astrbot.core.tools import fs_access
from astrbot.core.workspace import resolve_workspace_root_for_umo

_MODE_MARKER = "[File access mode: "


def _iter_text_blocks(content: object):
    """Yield the text blocks of a persisted message content value."""
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    yield text


def _last_guidance_text(req: ProviderRequest) -> str | None:
    """Return the most recently injected file-access guidance text.

    Guidance is persisted with the conversation, so blocks written for earlier
    modes stay in history. Only the newest block describes the mode in effect
    now; comparing against older ones would suppress a legitimate re-injection
    when the user goes readonly -> workspace -> readonly.

    Args:
        req: Request about to be handed to the agent runner.

    Returns:
        The newest guidance text, or None when nothing was injected yet.
    """
    for part in reversed(req.extra_user_content_parts):
        part_text = (
            part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
        )
        if isinstance(part_text, str) and _MODE_MARKER in part_text:
            return part_text
    for message in reversed(req.contexts):
        if not isinstance(message, dict):
            continue
        for candidate in _iter_text_blocks(message.get("content")):
            if _MODE_MARKER in candidate:
                return candidate
    return None


def _already_injected(req: ProviderRequest, text: str) -> bool:
    """Return whether the newest injected guidance already equals ``text``.

    Args:
        req: Request about to be handed to the agent runner.
        text: Guidance generated for the current mode and whitelist.

    Returns:
        True when the most recent guidance block is this text.
    """
    last = _last_guidance_text(req)
    if last is None:
        return False
    return last == text or text in last


async def _workspace_writable_roots(cfg: dict, umo: str) -> tuple[str, ...]:
    """Resolve the effective writable roots for workspace mode.

    Mirrors :func:`astrbot.core.tools.fs_access.write_roots` but starts from a
    plain umo instead of a tool context. Custom roots come from the same
    in-memory cache the dashboard updates via
    ``POST /chat/file-access-mode/roots``, so a whitelist edit applies on the
    next request.

    Args:
        cfg: Effective config for the conversation.
        umo: Unified message origin.

    Returns:
        Absolute roots, deduplicated case-insensitively, in display order.
    """
    try:
        workspace = await resolve_workspace_root_for_umo(umo)
    except Exception:
        logger.warning(
            "[fs_access] failed to resolve workspace root for %s", umo, exc_info=True
        )
        return ()

    extra = (
        *fs_access.configured_extra_roots(cfg),
        *fs_access.get_dynamic_roots(umo),
        *(await fs_access.get_custom_roots(umo)),
    )
    return tuple(
        str(root) for root in fs_access.compose_workspace_roots(workspace, extra=extra)
    )


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
        logger.warning("[fs_access] failed to read config for %s", umo, exc_info=True)
        return

    await fs_access.load_mode_for_umo(umo)
    mode = fs_access.get_mode_for_umo(umo, default=fs_access.resolve_default(cfg))

    if mode is fs_access.FileAccessMode.READONLY:
        blocked = fs_access.readonly_blocked_tools(cfg)
        if blocked:
            req.denied_tools = set(req.denied_tools or ()) | set(blocked)

    roots: tuple[str, ...] = ()
    if mode is fs_access.FileAccessMode.WORKSPACE:
        roots = await _workspace_writable_roots(cfg, umo)

    guidance = fs_access.mode_guidance(mode, writable_roots=roots)
    if not _already_injected(req, guidance):
        req.extra_user_content_parts.append(TextPart(text=guidance))
        session_id = getattr(getattr(req, "conversation", None), "cid", None)
        if session_id:
            # Developer-mode attribution only; the recorder ignores empty text
            # and disabled mode, and never raises into the request path.
            record_provenance(str(session_id), "file_access", guidance)
