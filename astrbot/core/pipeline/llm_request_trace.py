"""Per-handler observation of plugin context injections on ``on_llm_request``.

The hook mutates a shared ``ProviderRequest`` in place, so the only way to
attribute a change to the plugin that made it is to fingerprint the request
around each handler call. Fingerprints stay cheap (lengths plus hashes) and
the per-handler diffs are recorded onto the event extras for the turn to
forward to ChatUI.
"""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit

EVENT_EXTRA_KEY = "_llm_request_injections"
"""Event extra key holding the per-turn list of injection records."""

PREVIEW_CHARS = 80
"""Upper bound of the injected-text preview carried on the wire."""

FULL_CHARS = 8192
"""Upper bound of the expandable full text per change. Longer text is cut and
marked with an ellipsis so the panel can still disclose the whole injection."""


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


def _cap_full(text: str) -> str:
    """Cap an expandable text, marking the cut with an ellipsis.

    Args:
        text: The complete injected text.

    Returns:
        ``text`` when it fits, otherwise a capped copy ending in ``…``.
    """
    if len(text) <= FULL_CHARS:
        return text
    return text[:FULL_CHARS] + "…"


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
        full = ""
        if delta > 0:
            text = getattr(request, "system_prompt", "") or ""
            appended = text[before["system_prompt"][0] :]
            preview = appended[:PREVIEW_CHARS]
            full = _cap_full(appended)
        changes.append(
            {
                "field": "system_prompt",
                "delta": delta,
                "lossy": delta < 0,
                "preview": preview,
                "full": full,
            }
        )

    if before["contexts"] != after["contexts"]:
        delta = after["contexts"][0] - before["contexts"][0]
        preview = ""
        full = ""
        if delta > 0:
            contexts = list(getattr(request, "contexts", None) or [])
            if contexts:
                preview = _part_text(contexts[-1])[:PREVIEW_CHARS]
            appended = [
                text
                for text in (
                    _part_text(entry) for entry in contexts[before["contexts"][0] :]
                )
                if text
            ]
            full = _cap_full("\n\n".join(appended))
        changes.append(
            {
                "field": "contexts",
                "delta": delta,
                "lossy": delta < 0,
                "preview": preview,
                "full": full,
            }
        )

    if before["extra_user_content_parts"] != after["extra_user_content_parts"]:
        delta = after["extra_user_content_parts"] - before["extra_user_content_parts"]
        preview = ""
        full = ""
        if delta > 0:
            parts = list(getattr(request, "extra_user_content_parts", None) or [])
            if parts:
                preview = _part_text(parts[-1])[:PREVIEW_CHARS]
            appended = [
                text
                for text in (
                    _part_text(entry)
                    for entry in parts[before["extra_user_content_parts"] :]
                )
                if text
            ]
            full = _cap_full("\n\n".join(appended))
        changes.append(
            {
                "field": "extra_user_content_parts",
                "delta": delta,
                "lossy": delta < 0,
                "preview": preview,
                "full": full,
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
                "full": _cap_full(", ".join(added or removed)),
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


TEAM_SOURCES_KEY = "_team_context_sources"


def record_team_source(
    event: object, kind: str, content: str | None, **metadata
) -> None:
    """Record an actually assembled team input on the current event.

    Args:
        event: Current team event with a trusted execution binding.
        kind: Origin of the assembled input.
        content: Text actually assembled, or None when absent.
        **metadata: Non-secret source identities.
    """
    if not getattr(event, "get_extra", lambda *_: None)("agent_team_execution"):
        return
    items = event.get_extra(TEAM_SOURCES_KEY) or []
    items.append({"kind": kind, "content": content, **metadata})
    event.set_extra(TEAM_SOURCES_KEY, items)


def bounded_context_value(value: object) -> dict:
    """Bound and sanitize a provider-facing observation without altering the request.

    Args:
        value: JSON-compatible observation containing only explicitly chosen fields.

    Returns:
        Sanitized value and truncation information.
    """
    budget = FULL_CHARS
    truncated = False

    def visit(item):
        nonlocal budget, truncated
        budget -= 1
        if budget <= 0:
            truncated = True
            return None
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="json")
        if isinstance(item, dict):
            part_type = item.get("type")
            if isinstance(part_type, str) and part_type in {
                "image_url",
                "audio_url",
                "input_audio",
                "input_image",
                "image",
                "audio",
                "video",
                "video_url",
                "file",
                "input_file",
            }:
                result = {"type": part_type, "omitted": "binary or local media content"}
                pending = [item]
                while pending:
                    part = pending.pop()
                    if isinstance(part, dict):
                        for key, child in part.items():
                            if key in {
                                "url",
                                "image_url",
                                "audio_url",
                                "video_url",
                            } and isinstance(child, str):
                                result["original_length"] = len(child)
                                try:
                                    parsed = urlsplit(child)
                                    if (
                                        parsed.scheme in {"http", "https"}
                                        and parsed.hostname
                                    ):
                                        host = parsed.hostname
                                        if ":" in host:
                                            host = f"[{host}]"
                                        if parsed.port:
                                            host += f":{parsed.port}"
                                        result["url"] = visit(
                                            urlunsplit(
                                                (
                                                    parsed.scheme,
                                                    host,
                                                    parsed.path,
                                                    "",
                                                    "",
                                                )
                                            )
                                        )
                                        result.pop("omitted", None)
                                        if (
                                            parsed.username
                                            or parsed.password
                                            or parsed.query
                                            or parsed.fragment
                                        ):
                                            result["reference_redacted"] = True
                                    elif child.startswith("data:"):
                                        result["omitted"] = "binary data URI"
                                    else:
                                        result["omitted"] = (
                                            "no authorized file reference"
                                        )
                                except ValueError:
                                    result["omitted"] = "invalid URL"
                            elif key in {
                                "data",
                                "file_data",
                                "bytes",
                                "base64",
                                "b64_json",
                            } and isinstance(child, str):
                                result["original_length"] = len(child)
                                result["omitted"] = "binary media content"
                                result.pop("url", None)
                            elif isinstance(child, (dict, list)):
                                pending.append(child)
                    elif isinstance(part, list):
                        pending.extend(part)
                return result
            result = {}
            for k, v in item.items():
                key = str(k)
                if budget <= 0 or len(key) > budget:
                    truncated = True
                    break
                budget -= len(key)
                if key.lower() in {
                    "provider_config",
                    "api_key",
                    "authorization",
                    "password",
                    "secret",
                    "token",
                    "encrypted",
                } or (
                    item.get("encoding") == "base64"
                    and key.lower() in {"content", "data"}
                ):
                    result[key] = {
                        "omitted": "sensitive or binary field",
                        "original_length": len(v)
                        if isinstance(v, (str, bytes))
                        else None,
                    }
                    budget -= 1
                else:
                    result[key] = visit(v)
            return result
        if isinstance(item, (list, tuple)):
            result = []
            for v in item:
                if budget <= 0:
                    truncated = True
                    break
                result.append(visit(v))
            return result
        if isinstance(item, str):
            if item.lstrip().startswith(("{", "[")) and re.search(
                r'"(?:provider_config|api_key|authorization|password|secret|token|encrypted)"\s*:'
                r'|"encoding"\s*:\s*"base64"'
                r'|"type"\s*:\s*"(?:image_url|audio_url|input_audio|input_image|image|audio|video|video_url|file|input_file)"',
                item,
                flags=re.IGNORECASE,
            ):
                try:
                    structured = json.loads(item)
                except ValueError:
                    structured = None
                if isinstance(structured, (dict, list)):
                    item = json.dumps(visit(structured), ensure_ascii=False)
                    if budget <= 0:
                        truncated = True
                        return ""
            item = re.sub(
                r"data:(?:image|audio|video|application)/[^\s,;]+(?:;[^\s,]*)?,[A-Za-z0-9+/=_-]+",
                "[binary data URI omitted]",
                item,
                flags=re.IGNORECASE,
            )
            length = len(item)
            result = item[:budget]
            budget -= len(result)
            if len(result) < length:
                truncated = True
            return result
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return "[unsupported value omitted]"

    # Count original content without retaining the original serialization.
    sanitized = visit(value)
    return {
        "value": sanitized,
        "truncated": truncated,
        "limit": FULL_CHARS,
        "original_length": len(json.dumps(value, ensure_ascii=False, default=str)),
        "reason": "content limit" if truncated else None,
    }
