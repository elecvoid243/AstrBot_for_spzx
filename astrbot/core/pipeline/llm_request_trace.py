"""Per-handler observation of plugin context injections on ``on_llm_request``.

The hook mutates a shared ``ProviderRequest`` in place, so the only way to
attribute a change to the plugin that made it is to fingerprint the request
around each handler call. Fingerprints stay cheap (lengths plus hashes) and
the per-handler diffs are recorded onto the event extras for the turn to
forward to ChatUI.
"""

from __future__ import annotations

import hashlib

EVENT_EXTRA_KEY = "_llm_request_injections"
"""Event extra key holding the per-turn list of injection records."""

PREVIEW_CHARS = 80
"""Upper bound of the injected-text preview carried on the wire."""


def _digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _part_text(part: object) -> str:
    """Best-effort text of one context entry or extra content part.

    Args:
        part: A context dict (``{"role": ..., "content": ...}``), a plain
            string, or any object exposing a ``text`` attribute.

    Returns:
        The first text fragment found, or an empty string.
    """
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        content = part.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for sub in content:
                if isinstance(sub, dict) and isinstance(sub.get("text"), str):
                    return sub["text"]
        text = part.get("text")
        return text if isinstance(text, str) else ""
    text = getattr(part, "text", None)
    return text if isinstance(text, str) else ""


def snapshot(request: object) -> dict:
    """Fingerprint the injectable surfaces of an LLM request.

    Args:
        request: The shared ``ProviderRequest`` a hook handler may mutate.

    Returns:
        ``{"system_prompt": (len, sha1), "contexts": (len, tail sha1),
        "extra_user_content_parts": int, "func_tool": tuple[str, ...]}``.
    """
    system_prompt = getattr(request, "system_prompt", "") or ""
    contexts = list(getattr(request, "contexts", None) or [])
    parts = list(getattr(request, "extra_user_content_parts", None) or [])
    func_tool = getattr(request, "func_tool", None)
    return {
        "system_prompt": (len(system_prompt), _digest(system_prompt)),
        "contexts": (
            len(contexts),
            _digest(repr(contexts[-1])[:4096]) if contexts else "",
        ),
        "extra_user_content_parts": len(parts),
        "func_tool": tuple(sorted(func_tool.names())) if func_tool else (),
    }


def collect_changes(request: object, before: dict, after: dict) -> list[dict]:
    """Diff two snapshots into injectable-change records.

    Args:
        request: The request in its post-handler state (source of previews).
        before: Fingerprint taken before the handler ran.
        after: Fingerprint taken after the handler ran.

    Returns:
        One record per changed field, in the fixed order
        ``system_prompt, contexts, extra_user_content_parts, func_tool``;
        items are ``{"field", "delta", "lossy", "preview"}``.
    """
    changes: list[dict] = []

    if before["system_prompt"] != after["system_prompt"]:
        delta = after["system_prompt"][0] - before["system_prompt"][0]
        preview = ""
        if delta > 0:
            text = getattr(request, "system_prompt", "") or ""
            preview = text[before["system_prompt"][0] :][:PREVIEW_CHARS]
        changes.append(
            {
                "field": "system_prompt",
                "delta": delta,
                "lossy": delta < 0,
                "preview": preview,
            }
        )

    if before["contexts"] != after["contexts"]:
        delta = after["contexts"][0] - before["contexts"][0]
        preview = ""
        if delta > 0:
            contexts = list(getattr(request, "contexts", None) or [])
            if contexts:
                preview = _part_text(contexts[-1])[:PREVIEW_CHARS]
        changes.append(
            {
                "field": "contexts",
                "delta": delta,
                "lossy": delta < 0,
                "preview": preview,
            }
        )

    if before["extra_user_content_parts"] != after["extra_user_content_parts"]:
        delta = after["extra_user_content_parts"] - before["extra_user_content_parts"]
        preview = ""
        if delta > 0:
            parts = list(getattr(request, "extra_user_content_parts", None) or [])
            if parts:
                preview = _part_text(parts[-1])[:PREVIEW_CHARS]
        changes.append(
            {
                "field": "extra_user_content_parts",
                "delta": delta,
                "lossy": delta < 0,
                "preview": preview,
            }
        )

    if before["func_tool"] != after["func_tool"]:
        added = [name for name in after["func_tool"] if name not in before["func_tool"]]
        removed = [
            name for name in before["func_tool"] if name not in after["func_tool"]
        ]
        delta = len(after["func_tool"]) - len(before["func_tool"])
        changes.append(
            {
                "field": "func_tool",
                "delta": delta,
                "lossy": delta < 0,
                "preview": ", ".join(added or removed)[:PREVIEW_CHARS],
            }
        )

    return changes


def record_injections(
    event: object, plugin: str, handler: str, changes: list[dict]
) -> None:
    """Append one handler's change record to the event extras.

    Args:
        event: The message event carrying the turn's extras.
        plugin: Plugin (star) name the handler belongs to.
        handler: Handler name that made the change.
        changes: Records produced by :func:`collect_changes`.
    """
    if event is None:
        return
    items = event.get_extra(EVENT_EXTRA_KEY) or []
    items.append({"plugin": plugin, "handler": handler, "changes": changes})
    event.set_extra(EVENT_EXTRA_KEY, items)


def read_injections(event: object | None) -> list[dict]:
    """Return the recorded injection items for a turn.

    Args:
        event: The message event, or ``None`` (subagent runs without one).

    Returns:
        The recorded items, never ``None``.
    """
    if event is None or not hasattr(event, "get_extra"):
        return []
    try:
        items = event.get_extra(EVENT_EXTRA_KEY)
    except AttributeError:
        # Events that bypass ``AstrMessageEvent.__init__`` carry no extras
        # store; a trace is telemetry and must never break the run.
        return []
    return items if isinstance(items, list) else []
