"""In-memory LLM request snapshots for the ChatUI developer mode.

The recorder keeps the final provider payload of each network attempt in a
process-local ring buffer. It is a debugging aid: capture is disabled by
default, only ``webchat:`` sessions are recorded, and nothing is persisted.
All entry points are best effort and never raise into the request path.

Author: elecvoid243
Date: 2026-11-15
"""

from __future__ import annotations

import copy
import hashlib
import json
import platform
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from astrbot import __version__
from astrbot.core import astrbot_config, logger
from astrbot.core.pipeline.llm_request_trace import bounded_context_value

if TYPE_CHECKING:
    from astrbot.core.provider.entities import LLMResponse  # noqa: F401
    from astrbot.core.provider.provider import Provider

MAX_BYTES_PER_SNAPSHOT = 524288
"""Serialized UTF-8 byte budget of one sanitized request body."""

MAX_SNAPSHOTS_PER_SESSION = 128
"""Maximum number of snapshots kept per session."""

MAX_BYTES_PER_SESSION = 16777216
"""Maximum total serialized bytes kept per session."""

MAX_TOTAL_BYTES = 67108864
"""Maximum total serialized bytes kept across all sessions."""

TTL_SECONDS = 7200
"""Retention window of a snapshot and a message binding."""

MAX_MESSAGE_BINDINGS = 1024
"""Maximum number of ``message_id -> turn_id`` bindings."""

_MAX_TRACKED_SESSIONS = 1024
"""Safety valve so idle sessions cannot grow the store without bound."""

_MAX_TRACKED_ATTEMPTS = 4096
"""Safety valve for per-invocation attempt counters."""

_DEFAULT_LIST_LIMIT = 50


@dataclass(frozen=True, slots=True)
class LLMRequestScope:
    """Ambient description of the model request currently being made.

    Attributes:
        session_id: ChatUI session the request belongs to.
        umo: Unified message origin of the triggering message.
        turn_id: ChatUI turn spanning the tool loop.
        invocation_id: One model invocation inside a turn.
        kind: ``chat``, ``compression``, ``title`` or ``aux``.
        step: Tool-loop step number.
        stage: ``main``, ``param``, ``repair`` or ``None``.
        fallback_index: Zero-based index of the provider fallback used.
        is_subagent: Whether the request runs inside a subagent.
    """

    session_id: str | None = None
    umo: str | None = None
    turn_id: str | None = None
    invocation_id: str | None = None
    kind: str = "chat"
    step: int | None = None
    stage: str | None = None
    fallback_index: int = 0
    is_subagent: bool = False


_SCOPE_STACK: ContextVar[tuple[LLMRequestScope, ...]] = ContextVar(
    "astrbot_llm_request_scope_stack",
    default=(),
)


def current_scope() -> LLMRequestScope | None:
    """Return the innermost request scope, if any."""

    stack = _SCOPE_STACK.get()
    return stack[-1] if stack else None


@contextmanager
def request_snapshot_scope(**overrides: Any) -> Iterator[LLMRequestScope]:
    """Push a request scope that inherits from the enclosing one.

    Args:
        **overrides: :class:`LLMRequestScope` fields to change. Nested scopes
            keep the parent's values for fields that are not overridden, so an
            auxiliary call only has to set ``kind``.

    Yields:
        The active scope for the ``with`` block.
    """

    parent = current_scope() or LLMRequestScope()
    scope = replace(parent, **overrides)
    token = _SCOPE_STACK.set((*_SCOPE_STACK.get(), scope))
    try:
        yield scope
    finally:
        _SCOPE_STACK.reset(token)


def developer_mode_enabled() -> bool:
    """Return whether the ChatUI developer mode is currently enabled.

    The configuration is read on every call so toggling the setting takes
    effect without a restart.
    """

    try:
        chatui = astrbot_config.get("chatui") or {}
        return bool(chatui.get("developer_mode", False))
    except Exception:
        logger.debug("Failed to read chatui.developer_mode", exc_info=True)
        return False


def _primitive(value: object) -> str | int | float | bool | None:
    """Return a small JSON primitive or ``None`` for unsupported values."""

    if isinstance(value, (str, int, float, bool)):
        return value
    return None


def _number(value: object) -> int | float | None:
    """Return a numeric configuration value, ignoring bools and strings."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _clean_host(api_base: object) -> str | None:
    """Strip credentials, query and fragment from a provider base URL."""

    if not isinstance(api_base, str) or not api_base:
        return None
    try:
        parsed = urlsplit(api_base)
    except ValueError:
        return None
    if not parsed.scheme or not parsed.hostname:
        return None
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _key_fingerprint(api_key: object) -> str | None:
    """Build a non-reversible fingerprint, keeping the tail only for long keys.

    Short keys (eight characters or fewer) would be almost fully disclosed by
    their last four characters, so they get a hash-only fingerprint.
    """

    if not isinstance(api_key, str) or not api_key:
        return None
    digest = hashlib.sha1(api_key.encode("utf-8")).hexdigest()
    if len(api_key) > 8:
        return f"{digest[:8]}…{api_key[-4:]}"
    return digest[:8]


def _build_provider_info(provider: Provider | object | None) -> dict:
    """Collect non-secret provider metadata, tolerating partial providers."""

    info: dict[str, Any] = {
        "id": None,
        "type": None,
        "class": type(provider).__name__ if provider is not None else None,
        "model": None,
        "returned_model": None,
        "host": None,
        "proxy": False,
        "key_fp": None,
        "key_index": None,
        "key_count": None,
    }
    try:
        meta = provider.meta()
        info["id"] = getattr(meta, "id", None)
        info["type"] = getattr(meta, "type", None)
        info["model"] = getattr(meta, "model", None)
    except Exception:
        logger.debug("Failed to read provider metadata", exc_info=True)

    try:
        model = provider.get_model()
        if isinstance(model, str) and model:
            info["model"] = info["model"] or model
    except Exception:
        logger.debug("Failed to read provider model", exc_info=True)

    provider_config = getattr(provider, "provider_config", None) or {}
    if isinstance(provider_config, dict):
        try:
            info["host"] = _clean_host(provider_config.get("api_base"))
            info["proxy"] = bool(provider_config.get("proxy"))
        except Exception:
            logger.debug("Failed to read provider config", exc_info=True)

    try:
        keys = list(provider.get_keys() or [])
    except Exception:
        keys = []
    try:
        current_key = provider.get_current_key()
    except Exception:
        current_key = None
    info["key_count"] = len(keys) if keys else None
    info["key_fp"] = _key_fingerprint(current_key)
    if keys and current_key in keys:
        info["key_index"] = keys.index(current_key)
    return info


def _first_value(mapping: dict, *keys: str) -> object:
    """Return the first non-``None`` value among ``keys``."""

    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _build_request_info(
    scope: LLMRequestScope,
    raw: object,
    provider_config: dict,
    attempt: int,
) -> dict:
    """Describe the sampling parameters of one attempt.

    Reads the original request body directly, so the cheap fields survive
    truncation of the sanitized copy.
    """

    body = raw if isinstance(raw, dict) else {}
    reasoning = body.get("reasoning")
    reasoning_effort = body.get("reasoning_effort")
    if reasoning_effort is None and isinstance(reasoning, dict):
        reasoning_effort = reasoning.get("effort")
    return {
        "stream": _primitive(body.get("stream")),
        "temperature": _primitive(body.get("temperature")),
        "top_p": _primitive(body.get("top_p")),
        "max_tokens": _primitive(
            _first_value(
                body,
                "max_tokens",
                "max_completion_tokens",
                "max_output_tokens",
            )
        ),
        "tool_choice": _primitive(body.get("tool_choice")),
        "reasoning_effort": _primitive(reasoning_effort),
        "attempt": attempt,
        "fallback_index": scope.fallback_index,
        "timeout_s": _number(provider_config.get("timeout")),
        "retry_max": _number(
            _first_value(provider_config, "request_max_retries", "max_retries")
        ),
    }


def _build_counts(raw: object) -> dict:
    """Derive cheap counters from the original request body.

    Only container lengths and one string length are read, so this stays valid
    even when the sanitized body is later truncated by the byte budget.
    """

    body = raw if isinstance(raw, dict) else {}
    messages = body.get("messages")
    if not isinstance(messages, list):
        messages = body.get("input")
    if not isinstance(messages, list):
        messages = body.get("contents")
    tools = body.get("tools")
    config = body.get("config")
    if not isinstance(tools, list) and isinstance(config, dict):
        tools = config.get("tools")
    system = _first_value(
        body,
        "system",
        "instructions",
        "system_instruction",
    )
    if system is None and isinstance(config, dict):
        system = config.get("system_instruction")
    system_chars = len(system) if isinstance(system, str) else 0
    return {
        "messages": len(messages) if isinstance(messages, list) else 0,
        "tools": len(tools) if isinstance(tools, list) else 0,
        "system_chars": system_chars,
        "estimated_input_tokens": None,
        "omitted_messages": 0,
    }


def _build_env() -> dict:
    """Collect best-effort environment details for the envelope."""

    env: dict[str, Any] = {
        "file_access_mode": None,
        # Per-session concepts, not root configuration keys: ChatService fills
        # these in later once the snapshot is attached to a turn.
        "project": None,
        "workspace": None,
        "config_profile": None,
        "astrbot_version": __version__,
        "python": platform.python_version(),
        "env_source": "current",
    }
    try:
        provider_settings = astrbot_config.get("provider_settings") or {}
        if isinstance(provider_settings, dict):
            env["file_access_mode"] = provider_settings.get("file_access_default_mode")
    except Exception:
        logger.debug("Failed to read snapshot environment", exc_info=True)
    return env


def _light_envelope(envelope: dict) -> dict:
    """Return a detached, list-friendly envelope without large optional payloads."""

    light = {
        key: value
        for key, value in envelope.items()
        if key not in {"injections", "provenance", "raw"}
    }
    return copy.deepcopy(light)


SYSTEM_BYTES = 65536
"""Upper bound of the system prompt kept in one snapshot."""

TOOL_SCHEMA_BYTES = 16384
"""Upper bound of one tool schema kept in one snapshot."""

MESSAGE_BYTES = 32768
"""Upper bound of one provider-native message kept in one snapshot."""

MESSAGE_HEAD_BYTES = 8192
"""Head bytes kept when one oversized message is trimmed."""

MESSAGE_TAIL_BYTES = 8192
"""Tail bytes kept when one oversized message is trimmed."""

EXTRA_BYTES = 16384
"""Upper bound of provider-specific extension fields kept in one snapshot."""

_SANITIZER_LIMIT = MAX_BYTES_PER_SNAPSHOT * 2
"""Character budget handed to the sanitizer.

The structural pass already enforces the serialized byte cap, so this budget
only bounds pathological non-text shapes. It is deliberately larger than the
byte cap: a body close to the cap must not be cut from the tail, where the
newest message lives.
"""

_PRIMARY_SHRINK_LADDER = (8192, 4096, 2048, 1024, 512, 256)
"""Tool schema limits tried when system plus tools alone exceed the budget."""


def _serialized_bytes(value: object) -> int:
    """Return the serialized UTF-8 byte size of a JSON-compatible value."""

    try:
        payload = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = str(value)
    return len(payload.encode("utf-8"))


def _truncation_marker(removed: int) -> str:
    """Return the marker that replaces removed bytes."""

    return f"…[truncated {removed} bytes]…"


def _utf8_head(text: str, max_bytes: int) -> str:
    """Return a prefix of ``text`` that fits ``max_bytes`` UTF-8 bytes."""

    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", "ignore")


def _utf8_tail(text: str, max_bytes: int) -> str:
    """Return a suffix of ``text`` that fits ``max_bytes`` UTF-8 bytes."""

    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[-max_bytes:].decode("utf-8", "ignore")


def _head_cap_text(text: str, max_bytes: int) -> str:
    """Keep the head of ``text`` and append the truncation marker."""

    size = len(text.encode("utf-8"))
    if size <= max_bytes:
        return text
    reserve = len(_truncation_marker(size).encode("utf-8")) + 8
    head = _utf8_head(text, max(0, max_bytes - reserve))
    removed = size - len(head.encode("utf-8"))
    if removed <= 0:
        return text
    return f"{head}{_truncation_marker(removed)}"


def _head_tail_text(text: str, head_bytes: int, tail_bytes: int) -> str:
    """Keep the head and tail of ``text``, replacing the middle with a marker."""

    size = len(text.encode("utf-8"))
    if size <= head_bytes + tail_bytes + 64:
        return text
    head = _utf8_head(text, head_bytes)
    tail = _utf8_tail(text, tail_bytes)
    removed = size - len(head.encode("utf-8")) - len(tail.encode("utf-8"))
    if removed <= 0:
        return text
    replacement = f"{head}{_truncation_marker(removed)}{tail}"
    if len(replacement.encode("utf-8")) >= size:
        return text
    return replacement


def _get_at(container: object, path: tuple) -> object:
    """Read a nested value by path."""

    current = container
    for key in path:
        current = current[key]
    return current


def _set_at(container: object, path: tuple, value: object) -> None:
    """Write a nested value by path."""

    parent = container
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value


def _longest_string(value: object) -> tuple[tuple, str] | None:
    """Return the path and value of the longest string inside a structure."""

    best: tuple[tuple, str] | None = None
    stack: list[tuple[tuple, object]] = [((), value)]
    while stack:
        path, current = stack.pop()
        if isinstance(current, str):
            if path and (best is None or len(current) > len(best[1])):
                best = (path, current)
        elif isinstance(current, dict):
            for key, child in current.items():
                stack.append(((*path, key), child))
        elif isinstance(current, list):
            for index, child in enumerate(current):
                stack.append(((*path, index), child))
    return best


def _cap_structure(
    value: object,
    max_bytes: int,
    head_bytes: int | None = None,
    tail_bytes: int | None = None,
) -> tuple[object, bool]:
    """Shrink one JSON value so its serialized size fits ``max_bytes``.

    Args:
        value: JSON-compatible value; containers are mutated in place.
        max_bytes: Serialized UTF-8 byte budget.
        head_bytes: Head bytes kept per trimmed string, when trimming with a
            head and tail is requested.
        tail_bytes: Tail bytes kept per trimmed string.

    Returns:
        The possibly replaced value and whether anything changed.
    """

    if isinstance(value, str):
        if head_bytes is not None and tail_bytes is not None:
            capped = _head_tail_text(value, head_bytes, tail_bytes)
            if capped != value:
                return capped, True
        capped = _head_cap_text(value, max_bytes)
        return capped, capped != value
    if not isinstance(value, (dict, list)):
        return value, False
    if _serialized_bytes(value) <= max_bytes:
        return value, False

    changed = False
    while _serialized_bytes(value) > max_bytes:
        slot = _longest_string(value)
        if slot is None:
            break
        path, text = slot
        if head_bytes is not None and tail_bytes is not None:
            replacement = _head_tail_text(text, head_bytes, tail_bytes)
            if replacement == text:
                replacement = _head_cap_text(
                    text,
                    max(1024, len(text.encode("utf-8")) // 2),
                )
        else:
            replacement = _head_cap_text(
                text,
                max(1024, len(text.encode("utf-8")) // 2),
            )
        if replacement == text:
            break
        _set_at(value, path, replacement)
        changed = True

    if isinstance(value, (dict, list)) and _serialized_bytes(value) > max_bytes:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        value = _head_cap_text(serialized, max_bytes)
        changed = True
    return value, changed


def _system_slots(body: object) -> list[tuple]:
    """Return the paths of the system-prompt fields of a provider body."""

    if not isinstance(body, dict):
        return []
    slots: list[tuple] = [
        (key,)
        for key in ("system", "instructions", "system_instruction")
        if key in body
    ]
    config = body.get("config")
    if isinstance(config, dict):
        for key in ("system_instruction", "systemInstruction"):
            if key in config:
                slots.append(("config", key))
    return slots


def _messages_container(body: object) -> list | None:
    """Return the provider-native message list of a body, when present."""

    if not isinstance(body, dict):
        return None
    for key in ("messages", "input", "contents"):
        value = body.get(key)
        if isinstance(value, list):
            return value
    return None


def _tool_containers(body: object) -> list[list]:
    """Return every provider-native tool list of a body."""

    containers: list[list] = []
    if not isinstance(body, dict):
        return containers
    tools = body.get("tools")
    if isinstance(tools, list):
        containers.append(tools)
    config = body.get("config")
    if isinstance(config, dict):
        for key in ("tools", "toolConfig"):
            value = config.get(key)
            if isinstance(value, list):
                containers.append(value)
    return containers


def _cap_system(body: object, limit: int = SYSTEM_BYTES) -> bool:
    """Cap the system prompt to ``limit`` bytes, keeping a marker."""

    changed = False
    for path in _system_slots(body):
        value = _get_at(body, path)
        capped, slot_changed = _cap_structure(value, limit)
        if slot_changed:
            _set_at(body, path, capped)
            changed = True
    messages = _messages_container(body)
    if messages and _is_system_message(messages[0]):
        capped, slot_changed = _cap_structure(messages[0], limit)
        if slot_changed:
            messages[0] = capped
            changed = True
    return changed


def _is_system_message(message: object) -> bool:
    """Return whether a provider-native message is the system prompt."""

    return isinstance(message, dict) and message.get("role") == "system"


def _protected_head(messages: list | None) -> int:
    """Return how many leading messages must never be dropped."""

    if messages and _is_system_message(messages[0]):
        return 1
    return 0


def _cap_tools(body: object, limit: int = TOOL_SCHEMA_BYTES) -> bool:
    """Cap every tool schema to ``limit`` bytes, keeping all tools."""

    changed = False
    for container in _tool_containers(body):
        for index, tool in enumerate(container):
            capped, tool_changed = _cap_structure(tool, limit)
            if tool_changed:
                container[index] = capped
                changed = True
    return changed


def _cap_extras(body: object, limit: int = EXTRA_BYTES) -> bool:
    """Cap provider-specific extension fields to ``limit`` bytes."""

    if not isinstance(body, dict):
        return False
    changed = False
    extra_body = body.get("extra_body")
    if isinstance(extra_body, (dict, list)):
        capped, slot_changed = _cap_structure(extra_body, limit)
        if slot_changed:
            body["extra_body"] = capped
            changed = True
    config = body.get("config")
    if isinstance(config, dict):
        skip = {"system_instruction", "systemInstruction", "tools", "toolConfig"}
        for key in list(config):
            if key in skip:
                continue
            capped, slot_changed = _cap_structure(config[key], limit)
            if slot_changed:
                config[key] = capped
                changed = True
    return changed


def _cap_message_strings(body: object) -> bool:
    """Cap provider bodies whose messages are one flat string."""

    if not isinstance(body, dict):
        return False
    changed = False
    for key in ("input", "contents", "messages"):
        value = body.get(key)
        if not isinstance(value, str):
            continue
        capped, slot_changed = _cap_structure(
            value,
            MESSAGE_BYTES,
            MESSAGE_HEAD_BYTES,
            MESSAGE_TAIL_BYTES,
        )
        if slot_changed:
            body[key] = capped
            changed = True
    return changed


def _force_under_budget(body: object, budget: int) -> bool:
    """Enforce the byte budget as a last resort.

    The normal structural pass already fits the budget. This guard exists so a
    snapshot can never exceed the cap, even when one field alone is oversized.
    System and tools are trimmed with markers first; only when nothing else can
    shrink are tool schemas and then the oldest messages (never the newest)
    removed.

    Args:
        body: Mutable sanitized body.
        budget: Serialized UTF-8 byte budget.

    Returns:
        Whether the body was changed.
    """

    if _serialized_bytes(body) <= budget:
        return False
    changed = False
    messages = _messages_container(body)
    tool_containers = _tool_containers(body)
    head = _protected_head(messages)
    while _serialized_bytes(body) > budget:
        progress = False
        for path in _system_slots(body):
            value = _get_at(body, path)
            if _serialized_bytes(value) <= 256:
                continue
            serialized = (
                value
                if isinstance(value, str)
                else json.dumps(value, ensure_ascii=False, default=str)
            )
            _set_at(body, path, _head_cap_text(serialized, 256))
            progress = changed = True
        if progress:
            continue
        for container in tool_containers:
            if len(container) > 1:
                container.pop()
                progress = changed = True
                break
        if progress:
            continue
        if messages is not None and len(messages) > head + 1:
            del messages[head]
            progress = changed = True
        if progress:
            continue
        if messages:
            capped, slot_changed = _cap_structure(messages[head], 512)
            if slot_changed:
                messages[head] = capped
                progress = changed = True
        if progress:
            continue
        if isinstance(body, dict):
            for key in list(body):
                value = body[key]
                if _serialized_bytes(value) <= 1024:
                    continue
                capped, slot_changed = _cap_structure(value, 1024)
                if slot_changed:
                    body[key] = capped
                    progress = changed = True
        if progress:
            continue
        marker = _truncation_marker(_serialized_bytes(body))
        if isinstance(body, dict):
            body.clear()
            body["truncated"] = marker
        elif isinstance(body, list):
            body.clear()
            body.append(marker)
        changed = True
        break
    return changed


def _apply_structural_truncation(
    body: object,
    budget: int = MAX_BYTES_PER_SNAPSHOT,
) -> tuple[int, bool]:
    """Trim one provider-native body to the serialized byte budget.

    The order follows the recorder spec: bound system and tool schemas, drop
    the oldest messages, trim oversized messages to head plus tail, bound
    provider extras, and finally shrink system/tools with markers when they
    alone exceed the budget.

    Args:
        body: Deep copy of the request body; mutated in place so the caller's
            object is never touched.
        budget: Serialized UTF-8 byte budget.

    Returns:
        ``(omitted_messages, truncated)``.
    """

    if not isinstance(body, (dict, list)):
        return 0, False
    truncated = _cap_system(body)
    truncated = _cap_tools(body) or truncated

    messages = _messages_container(body)
    omitted = 0
    head = _protected_head(messages)
    while messages is not None and len(messages) > head + 1:
        if _serialized_bytes(body) <= budget:
            break
        del messages[head]
        omitted += 1
        truncated = True

    if messages is not None:
        for index, message in enumerate(messages):
            capped, message_changed = _cap_structure(
                message,
                MESSAGE_BYTES,
                MESSAGE_HEAD_BYTES,
                MESSAGE_TAIL_BYTES,
            )
            if message_changed:
                messages[index] = capped
                truncated = True

    if _cap_message_strings(body):
        truncated = True

    if _cap_extras(body):
        truncated = True

    if _serialized_bytes(body) > budget:
        for tool_limit in _PRIMARY_SHRINK_LADDER:
            if _serialized_bytes(body) <= budget:
                break
            _cap_system(body, min(SYSTEM_BYTES, tool_limit * 4))
            _cap_tools(body, tool_limit)
            truncated = True

    if _serialized_bytes(body) > budget:
        truncated = _force_under_budget(body, budget) or truncated
    return omitted, truncated


def _canonical_json(value: object) -> str:
    """Serialize a value with stable key order for hashing."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False)


def _schema_fingerprint(schema: object) -> tuple[str, int]:
    """Return the sha1 and byte size of a canonical tool schema."""

    canonical = _canonical_json(schema)
    encoded = canonical.encode("utf-8")
    return hashlib.sha1(encoded).hexdigest(), len(encoded)


def _classify_tool_origin(tool: object) -> str:
    """Infer where a function tool comes from.

    Args:
        tool: A ``FunctionTool``-like object.

    Returns:
        One of ``builtin``, ``plugin``, ``mcp``, ``skill``, ``subagent``,
        ``team`` or ``unknown``.
    """

    cls = type(tool)
    module = (getattr(cls, "__module__", "") or "").lower()
    class_name = (getattr(cls, "__name__", "") or "").lower()
    if "mcp" in module or "mcp" in class_name:
        return "mcp"
    if "subagent" in module or "handoff" in module:
        return "subagent"
    if "agent_team" in module:
        return "team"
    if "skill" in module:
        return "skill"
    if module.startswith(("astrbot.core.tools", "astrbot.builtin_stars")):
        return "builtin"
    if getattr(tool, "handler_module_path", None):
        return "plugin"
    return "unknown"


def _provider_tool_set(provider: object | None) -> object | None:
    """Return the provider's ``ToolSet`` when it exposes one."""

    if provider is None:
        return None
    for attribute in ("func_tool", "tool_set", "snapshot_tool_set"):
        candidate = getattr(provider, attribute, None)
        if candidate is not None and hasattr(candidate, "tools"):
            return candidate
    return None


def _raw_tool_schemas(raw: object) -> list[tuple[str, dict]]:
    """Return ``(name, schema)`` pairs found in a provider-native body."""

    schemas: list[tuple[str, dict]] = []
    seen: set[str] = set()

    def add(name: object, schema: object) -> None:
        if not isinstance(name, str) or not name or name in seen:
            return
        seen.add(name)
        schemas.append((name, schema if isinstance(schema, dict) else {}))

    for container in _tool_containers(raw):
        for item in container:
            if not isinstance(item, dict):
                continue
            function = item.get("function")
            if isinstance(function, dict):
                add(function.get("name"), function.get("parameters"))
                continue
            declarations = item.get("function_declarations") or item.get(
                "functionDeclarations"
            )
            if isinstance(declarations, list):
                for declaration in declarations:
                    if isinstance(declaration, dict):
                        add(
                            declaration.get("name"),
                            declaration.get("parameters")
                            or declaration.get("parametersJsonSchema"),
                        )
                continue
            add(
                item.get("name") or item.get("type"),
                item.get("parameters")
                or item.get("input_schema")
                or item.get("parametersJsonSchema"),
            )
    return schemas


def _tool_meta_row(
    origin: str,
    active: bool,
    denied: bool,
    background: bool,
    schema: object,
) -> dict:
    """Build one ``tool_meta`` row."""

    schema_hash, schema_bytes = _schema_fingerprint(schema)
    return {
        "origin": origin,
        "active": active,
        "denied": denied,
        "background": background,
        "schema_hash": schema_hash,
        "schema_bytes": schema_bytes,
    }


def _build_tool_meta(raw: object, provider: object | None) -> dict:
    """Describe the tools a provider request carried.

    Rich metadata comes from the provider's ``ToolSet`` when it exposes one.
    Otherwise only the serialized schema is known, which is reported with
    ``origin="unknown"`` and ``active=True``.

    Args:
        raw: Provider-native request body.
        provider: Provider that is about to perform the call.

    Returns:
        ``tool name -> meta`` mapping.
    """

    meta: dict[str, dict] = {}
    denied_tools = getattr(provider, "denied_tools", None) or ()
    try:
        denied = {str(name) for name in denied_tools}
    except TypeError:
        denied = set()

    tool_set = _provider_tool_set(provider)
    for tool in getattr(tool_set, "tools", None) or ():
        name = getattr(tool, "name", None)
        if not isinstance(name, str) or not name:
            continue
        meta[name] = _tool_meta_row(
            origin=_classify_tool_origin(tool),
            active=bool(getattr(tool, "active", True)),
            denied=name in denied,
            background=bool(getattr(tool, "is_background_task", False)),
            schema=getattr(tool, "parameters", None) or {"type": "object"},
        )

    for name, schema in _raw_tool_schemas(raw):
        if name in meta:
            continue
        meta[name] = _tool_meta_row(
            origin="unknown",
            active=True,
            denied=name in denied,
            background=False,
            schema=schema,
        )
    return meta


_TEXT_KEYS = {
    "text",
    "think",
    "thinking",
    "content",
    "output",
    "arguments",
    "input",
    "response",
    "summary",
}


def _content_chars(value: object) -> int:
    """Count displayable characters in a provider-native content value."""

    if isinstance(value, str):
        return len(value)
    if isinstance(value, (list, tuple)):
        return sum(_content_chars(item) for item in value)
    if isinstance(value, dict):
        total = 0
        for key, child in value.items():
            if not isinstance(key, str) or key not in _TEXT_KEYS:
                continue
            if isinstance(child, (str, list, tuple)):
                total += _content_chars(child)
            elif isinstance(child, (dict, int, float)) and key in {
                "input",
                "arguments",
            }:
                total += len(json.dumps(child, ensure_ascii=False, default=str))
        return total
    return 0


def _text_from_parts(value: object) -> str:
    """Flatten provider-native text blocks into one string."""

    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "".join(_text_from_parts(item) for item in value)
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return text
        for key in ("think", "thinking", "output", "content", "parts"):
            if key in value:
                flattened = _text_from_parts(value[key])
                if flattened:
                    return flattened
    return ""


def _message_entry(
    *,
    role: object,
    content: object = None,
    name: object = None,
    tool_call_id: object = None,
    tool_calls: object = None,
    source: object = None,
    native: object = None,
    flags: tuple[str, ...] = (),
) -> dict:
    """Normalize one provider-native message into the shared entry shape."""

    return {
        "role": role if isinstance(role, str) and role else "user",
        "name": name if isinstance(name, str) else None,
        "tool_call_id": tool_call_id if isinstance(tool_call_id, str) else None,
        "content": content,
        "tool_calls": list(tool_calls) if isinstance(tool_calls, list) else [],
        "source": source if isinstance(source, str) else None,
        "native": native,
        "flags": list(flags),
    }


def _tool_entry(name: object, description: object, parameters: object) -> dict | None:
    """Normalize one provider-native tool schema."""

    if not isinstance(name, str) or not name:
        return None
    return {
        "name": name,
        "description": description if isinstance(description, str) else None,
        "parameters": parameters
        if isinstance(parameters, dict)
        else {"type": "object"},
    }


def _pick(mapping: object, keys: tuple[str, ...]) -> dict:
    """Return the present, non-null keys of ``mapping``."""

    if not isinstance(mapping, dict):
        return {}
    return {key: mapping[key] for key in keys if mapping.get(key) is not None}


def _extension_fields(raw: object, keys: tuple[str, ...]) -> dict:
    """Return provider-specific extension fields, flattened from ``extra_body``."""

    if not isinstance(raw, dict):
        return {}
    extra = {key: raw[key] for key in keys if raw.get(key) is not None}
    extra_body = raw.get("extra_body")
    if isinstance(extra_body, dict) and extra_body:
        extra.update(extra_body)
    if not extra:
        return {}
    extra["extra_body_keys"] = sorted(key for key in extra if key != "extra_body_keys")
    return extra


class ProviderAdapter:
    """Normalize one provider-native request body for the developer view.

    Subclasses implement the five hooks the view needs. All hooks are
    side-effect free and tolerate partially truncated payloads.
    """

    name: str = "unknown"

    def system_text(self, raw: dict) -> str | None:
        """Return the system prompt text of the request."""

        return None

    def system_source_field(self, raw: dict) -> str | None:
        """Return where the system prompt lives in the native payload."""

        return None

    def messages(self, raw: dict) -> list[dict]:
        """Return normalized message entries in provider order."""

        return []

    def tools(self, raw: dict) -> list[dict]:
        """Return normalized tool schemas in provider order."""

        return []

    def params(self, raw: dict) -> dict:
        """Return sampling and request parameters."""

        return {}

    def extra(self, raw: dict) -> dict:
        """Return provider-specific extension fields."""

        return {}


class OpenAIChatAdapter(ProviderAdapter):
    """Adapter for OpenAI Chat Completions style payloads."""

    name = "openai_chat"
    _PARAM_KEYS = (
        "temperature",
        "top_p",
        "max_tokens",
        "max_completion_tokens",
        "tool_choice",
        "stream",
        "stream_options",
        "presence_penalty",
        "frequency_penalty",
        "seed",
        "stop",
        "response_format",
        "logit_bias",
        "reasoning_effort",
        "n",
    )
    _EXTRA_KEYS = ("reasoning", "chat_template_kwargs", "think", "thinking")

    def system_text(self, raw: dict) -> str | None:
        messages = raw.get("messages") if isinstance(raw, dict) else None
        if not isinstance(messages, list) or not messages:
            return None
        first = messages[0]
        if isinstance(first, dict) and first.get("role") == "system":
            return _text_from_parts(first.get("content")) or None
        return None

    def system_source_field(self, raw: dict) -> str | None:
        return "messages[0]" if self.system_text(raw) is not None else None

    def messages(self, raw: dict) -> list[dict]:
        messages = raw.get("messages") if isinstance(raw, dict) else None
        if not isinstance(messages, list):
            return []
        entries = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            entries.append(
                _message_entry(
                    role=message.get("role"),
                    content=message.get("content"),
                    name=message.get("name"),
                    tool_call_id=message.get("tool_call_id"),
                    tool_calls=message.get("tool_calls"),
                    source=message.get("source"),
                    native=message,
                )
            )
        return entries

    def tools(self, raw: dict) -> list[dict]:
        tools = raw.get("tools") if isinstance(raw, dict) else None
        if not isinstance(tools, list):
            return []
        entries = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function")
            if isinstance(function, dict):
                entry = _tool_entry(
                    function.get("name"),
                    function.get("description"),
                    function.get("parameters"),
                )
            else:
                entry = _tool_entry(
                    tool.get("name"),
                    tool.get("description"),
                    tool.get("parameters"),
                )
            if entry is not None:
                entries.append(entry)
        return entries

    def params(self, raw: dict) -> dict:
        params = _pick(raw, self._PARAM_KEYS)
        extra_body = raw.get("extra_body") if isinstance(raw, dict) else None
        if (
            isinstance(extra_body, dict)
            and "reasoning_effort" in extra_body
            and "reasoning_effort" not in params
        ):
            params["reasoning_effort"] = extra_body["reasoning_effort"]
        return params

    def extra(self, raw: dict) -> dict:
        return _extension_fields(raw, self._EXTRA_KEYS)


class OpenAIResponsesAdapter(ProviderAdapter):
    """Adapter for OpenAI Responses style payloads."""

    name = "openai_responses"
    _PARAM_KEYS = (
        "temperature",
        "top_p",
        "max_output_tokens",
        "tool_choice",
        "stream",
        "parallel_tool_calls",
        "max_tool_calls",
    )
    _EXTRA_KEYS = (
        "store",
        "text",
        "truncation",
        "include",
        "metadata",
        "prompt_cache_key",
    )

    def system_text(self, raw: dict) -> str | None:
        value = raw.get("instructions") if isinstance(raw, dict) else None
        return (_text_from_parts(value) or None) if value is not None else None

    def system_source_field(self, raw: dict) -> str | None:
        return "instructions" if self.system_text(raw) is not None else None

    def messages(self, raw: dict) -> list[dict]:
        items = raw.get("input") if isinstance(raw, dict) else None
        if isinstance(items, str):
            return [_message_entry(role="user", content=items, native=items)]
        if not isinstance(items, list):
            return []
        entries = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "function_call":
                entries.append(
                    _message_entry(
                        role="assistant",
                        tool_calls=[
                            {
                                "id": item.get("call_id"),
                                "type": "function",
                                "name": item.get("name"),
                                "arguments": item.get("arguments"),
                            }
                        ],
                        native=item,
                    )
                )
            elif item_type == "function_call_output":
                entries.append(
                    _message_entry(
                        role="tool",
                        content=item.get("output"),
                        tool_call_id=item.get("call_id"),
                        native=item,
                    )
                )
            elif item_type == "reasoning":
                entries.append(
                    _message_entry(
                        role="assistant",
                        content=item.get("summary") or item.get("content"),
                        native=item,
                        flags=("reasoning",),
                    )
                )
            else:
                entries.append(
                    _message_entry(
                        role=item.get("role") or "user",
                        content=item.get("content"),
                        name=item.get("name"),
                        native=item,
                    )
                )
        return entries

    def tools(self, raw: dict) -> list[dict]:
        tools = raw.get("tools") if isinstance(raw, dict) else None
        if not isinstance(tools, list):
            return []
        entries = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name")
            if not isinstance(name, str) or not name:
                name = tool.get("type")
            entry = _tool_entry(name, tool.get("description"), tool.get("parameters"))
            if entry is not None:
                entries.append(entry)
        return entries

    def params(self, raw: dict) -> dict:
        params = _pick(raw, self._PARAM_KEYS)
        reasoning = raw.get("reasoning") if isinstance(raw, dict) else None
        if isinstance(reasoning, dict) and reasoning.get("effort") is not None:
            params["reasoning_effort"] = reasoning["effort"]
        return params

    def extra(self, raw: dict) -> dict:
        return _extension_fields(raw, self._EXTRA_KEYS)


class AnthropicAdapter(ProviderAdapter):
    """Adapter for Anthropic Messages style payloads."""

    name = "anthropic"
    _PARAM_KEYS = (
        "temperature",
        "top_p",
        "top_k",
        "max_tokens",
        "tool_choice",
        "stream",
        "stop_sequences",
        "metadata",
        "service_tier",
    )
    _EXTRA_KEYS = (
        "thinking",
        "betas",
        "mcp_servers",
        "context_management",
        "container",
    )

    def system_text(self, raw: dict) -> str | None:
        value = raw.get("system") if isinstance(raw, dict) else None
        return (_text_from_parts(value) or None) if value is not None else None

    def system_source_field(self, raw: dict) -> str | None:
        return "system" if self.system_text(raw) is not None else None

    def messages(self, raw: dict) -> list[dict]:
        messages = raw.get("messages") if isinstance(raw, dict) else None
        if not isinstance(messages, list):
            return []
        entries = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            tool_calls: list[dict] = []
            tool_call_id = None
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "tool_use":
                        tool_calls.append(
                            {
                                "id": block.get("id"),
                                "type": "tool_use",
                                "name": block.get("name"),
                                "input": block.get("input"),
                            }
                        )
                    elif block_type == "tool_result" and tool_call_id is None:
                        tool_call_id = block.get("tool_use_id")
            entries.append(
                _message_entry(
                    role=message.get("role"),
                    content=content,
                    tool_call_id=tool_call_id,
                    tool_calls=tool_calls,
                    native=message,
                )
            )
        return entries

    def tools(self, raw: dict) -> list[dict]:
        tools = raw.get("tools") if isinstance(raw, dict) else None
        if not isinstance(tools, list):
            return []
        entries = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            entry = _tool_entry(
                tool.get("name"),
                tool.get("description"),
                tool.get("input_schema"),
            )
            if entry is not None:
                entries.append(entry)
        return entries

    def params(self, raw: dict) -> dict:
        return _pick(raw, self._PARAM_KEYS)

    def extra(self, raw: dict) -> dict:
        return _extension_fields(raw, self._EXTRA_KEYS)


class GeminiAdapter(ProviderAdapter):
    """Adapter for Google GenAI (Gemini) style payloads."""

    name = "gemini"
    _EXTRA_SKIP = {
        "system_instruction",
        "systemInstruction",
        "tools",
        "toolConfig",
        "generation_config",
        "generationConfig",
    }

    @staticmethod
    def _config(raw: object) -> dict:
        config = raw.get("config") if isinstance(raw, dict) else None
        return config if isinstance(config, dict) else {}

    def system_text(self, raw: dict) -> str | None:
        config = self._config(raw)
        value = config.get("system_instruction")
        if value is None:
            value = config.get("systemInstruction")
        return (_text_from_parts(value) or None) if value is not None else None

    def system_source_field(self, raw: dict) -> str | None:
        return (
            "config.system_instruction" if self.system_text(raw) is not None else None
        )

    def messages(self, raw: dict) -> list[dict]:
        contents = raw.get("contents") if isinstance(raw, dict) else None
        if not isinstance(contents, list):
            return []
        entries = []
        for item in contents:
            if not isinstance(item, dict):
                continue
            parts = item.get("parts")
            tool_calls: list[dict] = []
            if isinstance(parts, list):
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    call = part.get("functionCall") or part.get("function_call")
                    if isinstance(call, dict):
                        tool_calls.append(
                            {
                                "id": part.get("id"),
                                "type": "function_call",
                                "name": call.get("name"),
                                "args": call.get("args"),
                            }
                        )
            role = item.get("role")
            entries.append(
                _message_entry(
                    role="assistant" if role == "model" else role,
                    content=parts if isinstance(parts, list) else item.get("content"),
                    tool_calls=tool_calls,
                    native=item,
                )
            )
        return entries

    def tools(self, raw: dict) -> list[dict]:
        tools = self._config(raw).get("tools")
        if not isinstance(tools, list):
            return []
        entries = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            declarations = tool.get("function_declarations") or tool.get(
                "functionDeclarations"
            )
            if isinstance(declarations, list):
                for declaration in declarations:
                    if not isinstance(declaration, dict):
                        continue
                    entry = _tool_entry(
                        declaration.get("name"),
                        declaration.get("description"),
                        declaration.get("parameters")
                        or declaration.get("parametersJsonSchema"),
                    )
                    if entry is not None:
                        entries.append(entry)
                continue
            entry = _tool_entry(
                tool.get("name"),
                tool.get("description"),
                tool.get("parameters"),
            )
            if entry is not None:
                entries.append(entry)
        return entries

    def params(self, raw: dict) -> dict:
        config = self._config(raw)
        generation = config.get("generation_config") or config.get("generationConfig")
        params = dict(generation) if isinstance(generation, dict) else {}
        for key in (
            "temperature",
            "top_p",
            "topP",
            "max_output_tokens",
            "maxOutputTokens",
            "top_k",
            "topK",
        ):
            if key in config:
                params.setdefault(key, config[key])
        tool_config = config.get("tool_config") or config.get("toolConfig")
        if isinstance(tool_config, dict):
            params["tool_choice"] = tool_config
        return params

    def extra(self, raw: dict) -> dict:
        config = self._config(raw)
        extra = {
            key: value for key, value in config.items() if key not in self._EXTRA_SKIP
        }
        extra_body = raw.get("extra_body") if isinstance(raw, dict) else None
        if isinstance(extra_body, dict):
            extra.update(extra_body)
        return extra


ADAPTERS: dict[str, ProviderAdapter] = {
    "openai_chat": OpenAIChatAdapter(),
    "openai_responses": OpenAIResponsesAdapter(),
    "anthropic": AnthropicAdapter(),
    "gemini": GeminiAdapter(),
}
"""Snapshot adapters keyed by the ``adapter`` field of the envelope."""


def _detect_adapter(raw: object) -> str:
    """Infer the adapter name from the shape of a provider-native body."""

    if not isinstance(raw, dict):
        return "openai_chat"
    if "contents" in raw:
        return "gemini"
    if "instructions" in raw:
        return "openai_responses"
    if "input" in raw and "messages" not in raw:
        return "openai_responses"
    if "system" in raw:
        return "anthropic"
    return "openai_chat"


def _provenance_parts(provenance: object) -> list[dict]:
    """Project envelope provenance entries into view system parts."""

    parts: list[dict] = []
    if not isinstance(provenance, list):
        return parts
    for entry in provenance:
        if not isinstance(entry, dict):
            continue
        label = entry.get("label")
        if not isinstance(label, str) or not label:
            continue
        chars = entry.get("chars")
        parts.append(
            {
                "label": label,
                "chars": chars
                if isinstance(chars, int) and not isinstance(chars, bool)
                else 0,
                "approx": bool(entry.get("approx", True)),
            }
        )
    return parts


def _estimate_tokens(native: object, chars: int) -> int:
    """Estimate tokens of one native message.

    Uses :class:`EstimateTokenCounter` when the provider-native message can be
    validated as an AstrBot :class:`Message`; otherwise falls back to a
    character-based estimate.

    Args:
        native: Provider-native message payload.
        chars: Displayable character count of the message.

    Returns:
        Estimated token count for display.
    """

    if isinstance(native, dict):
        try:
            from astrbot.core.agent.context.token_counter import (
                EstimateTokenCounter,
            )
            from astrbot.core.agent.message import Message

            message = Message.model_validate(native)
        except Exception:
            message = None
        if message is not None:
            try:
                return EstimateTokenCounter().count_tokens([message])
            except Exception:
                logger.debug("Failed to estimate snapshot tokens", exc_info=True)
    return max(1, chars // 3)


def _message_flags(native: object, entry: dict) -> list[str]:
    """Return internal markers still present on one native message."""

    flags: list[str] = []
    if isinstance(native, dict):
        if native.get("role") == "_checkpoint":
            flags.append("checkpoint")
        if native.get("_window") is not None:
            flags.append("window")
        if native.get("_internal"):
            flags.append("internal")
        if native.get("_tool_result_unknown"):
            flags.append("tool_result_unknown")
        if native.get("type") == "reasoning":
            flags.append("reasoning")
    for flag in entry.get("flags") or ():
        if flag not in flags:
            flags.append(flag)
    return flags


def _message_row(entry: dict, position: int, offset: int) -> dict:
    """Build one normalized view message row."""

    content = entry.get("content")
    tool_calls = entry.get("tool_calls") or []
    chars = _content_chars(content) + _content_chars(tool_calls)
    return {
        "index": position + offset,
        "role": entry.get("role") or "user",
        "name": entry.get("name"),
        "tool_call_id": entry.get("tool_call_id"),
        "source": entry.get("source"),
        "content": content,
        "tool_calls": tool_calls,
        "chars": chars,
        "estimated_tokens": _estimate_tokens(entry.get("native"), chars),
        "flags": _message_flags(entry.get("native"), entry),
    }


def _tool_view_row(row: dict, meta: dict | None) -> dict:
    """Merge one normalized tool schema with its captured metadata."""

    parameters = row.get("parameters") or {"type": "object"}
    schema_hash, schema_bytes = _schema_fingerprint(parameters)
    meta = meta if isinstance(meta, dict) else {}
    return {
        "name": row.get("name"),
        "description": row.get("description"),
        "parameters": parameters,
        "origin": meta.get("origin") or "unknown",
        "active": bool(meta.get("active", True)),
        "denied": bool(meta.get("denied", False)),
        "background": bool(meta.get("background", False)),
        "schema_hash": schema_hash,
        "schema_bytes": schema_bytes,
    }


def build_view(snapshot: dict) -> dict:
    """Derive the normalized developer view from a stored snapshot.

    Args:
        snapshot: Snapshot returned by :meth:`RequestSnapshotRecorder.get`;
            it must carry the sanitized ``raw`` body.

    Returns:
        ``{"system", "messages", "tools", "params", "extra"}``. The view is
        derived on every call and never stored. Message ``index`` values stay
        relative to the request before truncation, because head-dropped
        messages are reported through ``counts.omitted_messages``.
    """

    snapshot = snapshot if isinstance(snapshot, dict) else {}
    raw = snapshot.get("raw")
    raw = raw if isinstance(raw, dict) else {}
    adapter_name = snapshot.get("adapter")
    adapter = ADAPTERS.get(adapter_name) if isinstance(adapter_name, str) else None
    if adapter is None:
        adapter = ADAPTERS[_detect_adapter(raw)]

    system_text = adapter.system_text(raw) or ""
    system = {
        "text": system_text,
        "hash": (
            hashlib.sha1(system_text.encode("utf-8")).hexdigest()
            if system_text
            else None
        ),
        "source_field": adapter.system_source_field(raw),
        "parts": _provenance_parts(snapshot.get("provenance")),
    }

    counts = snapshot.get("counts")
    omitted = counts.get("omitted_messages") if isinstance(counts, dict) else 0
    try:
        offset = int(omitted or 0)
    except (TypeError, ValueError):
        offset = 0
    messages = [
        _message_row(entry, position, offset)
        for position, entry in enumerate(adapter.messages(raw))
    ]

    tool_meta = snapshot.get("tool_meta")
    tool_meta = tool_meta if isinstance(tool_meta, dict) else {}
    tools = [
        _tool_view_row(row, tool_meta.get(row.get("name")))
        for row in adapter.tools(raw)
    ]

    return {
        "system": system,
        "messages": messages,
        "tools": tools,
        "params": adapter.params(raw),
        "extra": adapter.extra(raw),
    }


class RequestSnapshotRecorder:
    """Process-local ring buffer of sanitized LLM request snapshots."""

    def __init__(self, clock: Any = time.time) -> None:
        """Initialize an empty store.

        Args:
            clock: Time source returning epoch seconds; injectable for tests.
        """

        self._clock = clock
        self._snapshots: OrderedDict[str, dict] = OrderedDict()
        self._raw: dict[str, object] = {}
        self._sessions: OrderedDict[str, deque[str]] = OrderedDict()
        self._session_bytes: dict[str, int] = {}
        self._dropped: OrderedDict[str, int] = OrderedDict()
        self._total_bytes = 0
        self._attempts: OrderedDict[str, int] = OrderedDict()
        self._by_invocation: dict[str, list[str]] = {}
        self._bindings: OrderedDict[str, tuple[str, str | None, float]] = OrderedDict()

    def capture(
        self,
        provider: Provider | object | None,
        raw: object,
        *,
        adapter: str | None = None,
        session_id: str | None = None,
        umo: str | None = None,
    ) -> str | None:
        """Store one sanitized request attempt.

        Args:
            provider: Provider that is about to perform the network call.
            raw: Final provider-native request body.
            adapter: Snapshot adapter name; ``None`` uses the provider's
                ``SNAPSHOT_ADAPTER`` attribute.
            session_id: Overrides the scope session id.
            umo: Overrides the scope unified message origin.

        Returns:
            The new snapshot id, or ``None`` when the attempt is not recorded.
            This method never raises.
        """

        try:
            if not developer_mode_enabled():
                return None
            scope = current_scope()
            if scope is None or scope.is_subagent:
                return None
            effective_session = session_id or scope.session_id
            effective_umo = umo or scope.umo
            if not effective_session or not effective_umo:
                return None
            if not effective_umo.startswith("webchat:"):
                return None

            now = self._clock()
            self._purge(now)
            # Cheap sampling fields and counters come from the original body so
            # a huge leading block can never turn them into None/0 when the
            # sanitized copy is truncated.
            attempt = self._next_attempt(scope)
            provider_config = getattr(provider, "provider_config", None)
            if not isinstance(provider_config, dict):
                provider_config = {}
            request_info = _build_request_info(
                scope,
                raw,
                provider_config,
                attempt,
            )
            counts = _build_counts(raw)
            # Structural truncation runs on a deep copy so the caller's request
            # object is never mutated. It enforces the serialized byte cap; the
            # sanitizer only redacts and bounds pathological shapes afterwards.
            body = raw
            original_bytes = _serialized_bytes(raw)
            omitted_messages, structurally_truncated = 0, False
            try:
                prepared = copy.deepcopy(raw) if isinstance(raw, (dict, list)) else raw
            except Exception:
                # Non-copyable provider objects still get a sanitized snapshot;
                # the sanitizer copies detached values itself.
                logger.debug("Snapshot body is not deep-copyable", exc_info=True)
                prepared = None
            if prepared is not None:
                body = prepared
                omitted_messages, structurally_truncated = _apply_structural_truncation(
                    body
                )
            sanitized = bounded_context_value(body, limit=_SANITIZER_LIMIT)
            body = sanitized.get("value")
            if _serialized_bytes(body) > MAX_BYTES_PER_SNAPSHOT:
                structurally_truncated = (
                    _force_under_budget(body, MAX_BYTES_PER_SNAPSHOT)
                    or structurally_truncated
                )
            size = _serialized_bytes(body)
            snapshot_id = uuid.uuid4().hex
            envelope = {
                "snapshot_id": snapshot_id,
                "session_id": effective_session,
                "umo": effective_umo,
                "turn_id": scope.turn_id,
                "invocation_id": scope.invocation_id,
                "kind": scope.kind,
                "stage": scope.stage,
                "step": scope.step,
                "status": "pending",
                "created_at": now,
                "duration_ms": None,
                "bytes": size,
                "original_bytes": original_bytes,
                "truncated": bool(sanitized.get("truncated")) or structurally_truncated,
                "redactions": int(sanitized.get("redactions") or 0),
                "provider": _build_provider_info(provider),
                "request": request_info,
                "counts": {**counts, "omitted_messages": omitted_messages},
                "response": None,
                "tool_executions": [],
                "diagnostics": {
                    "compression": None,
                    "cache": None,
                    "budget": None,
                    "retries": None,
                    "stop_reason": None,
                    "omitted_messages": omitted_messages,
                },
                "provenance": [],
                "injections": [],
                "tool_meta": _build_tool_meta(raw, provider),
                "env": _build_env(),
                "adapter": adapter
                if adapter is not None
                else getattr(provider, "SNAPSHOT_ADAPTER", None),
            }
            self._store(effective_session, envelope, body)
            return snapshot_id
        except Exception:
            logger.debug("Failed to capture LLM request snapshot", exc_info=True)
            return None

    def list_for_session(
        self,
        session_id: str,
        *,
        message_id: int | str | None = None,
        turn_id: str | None = None,
        limit: int = _DEFAULT_LIST_LIMIT,
    ) -> dict:
        """List lightweight envelopes for a session, newest first.

        Args:
            session_id: ChatUI session id.
            message_id: Resolve ``turn_id`` through a recorded message binding.
            turn_id: Explicit turn filter.
            limit: Maximum number of items, clamped to the per-session cap.

        Returns:
            ``{"enabled", "expired", "items", "limits", "dropped"}``. When a
            ``message_id`` has no live binding, ``expired`` is ``True`` and the
            item list is empty. Items are detached lightweight copies: they
            never carry ``injections``/``provenance`` payloads and mutating them
            cannot change the stored state.
        """

        try:
            now = self._clock()
            self._purge(now)
            result = {
                "enabled": developer_mode_enabled(),
                "expired": False,
                "items": [],
                "limits": self.limits(),
                "dropped": self._dropped.get(str(session_id), 0),
            }
            target_turn = turn_id
            if message_id is not None:
                binding = self._resolve_binding(message_id, now)
                if binding is None or binding[0] != str(session_id):
                    result["expired"] = True
                    return result
                target_turn = binding[1] or target_turn
            try:
                requested = int(limit)
            except (TypeError, ValueError):
                requested = _DEFAULT_LIST_LIMIT
            requested = max(1, min(requested, MAX_SNAPSHOTS_PER_SESSION))
            ids = self._sessions.get(str(session_id))
            if not ids:
                return result
            items: list[dict] = []
            for snapshot_id in reversed(ids):
                envelope = self._snapshots.get(snapshot_id)
                if envelope is None:
                    continue
                if target_turn is not None and envelope.get("turn_id") != target_turn:
                    continue
                items.append(_light_envelope(envelope))
                if len(items) >= requested:
                    break
            result["items"] = items
            return result
        except Exception:
            logger.debug("Failed to list LLM request snapshots", exc_info=True)
            return {
                "enabled": developer_mode_enabled(),
                "expired": False,
                "items": [],
                "limits": self.limits(),
                "dropped": 0,
            }

    def get(self, snapshot_id: str) -> dict | None:
        """Return the full envelope plus its sanitized raw body.

        Args:
            snapshot_id: Id returned by :meth:`capture`.

        Returns:
            A deep copy of the stored envelope with a ``raw`` key, or ``None``
            when the snapshot is unknown or expired. Copying keeps consumers
            from mutating the recorder's state.
        """

        try:
            self._purge(self._clock())
            envelope = self._snapshots.get(str(snapshot_id))
            if envelope is None:
                return None
            full = copy.deepcopy(envelope)
            full["raw"] = copy.deepcopy(self._raw.get(str(snapshot_id)))
            return full
        except Exception:
            logger.debug("Failed to read LLM request snapshot", exc_info=True)
            return None

    def bind_message(
        self,
        message_id: int | str,
        turn_id: str,
        session_id: str,
    ) -> None:
        """Bind a persisted ChatUI message to the turn that produced it.

        Args:
            message_id: Persisted bot history row id.
            turn_id: Turn the snapshots belong to.
            session_id: Session the message belongs to.
        """

        try:
            if message_id is None:
                return
            now = self._clock()
            self._purge_bindings(now)
            key = str(message_id)
            self._bindings[key] = (str(session_id), turn_id, now + TTL_SECONDS)
            self._bindings.move_to_end(key)
            while len(self._bindings) > MAX_MESSAGE_BINDINGS:
                self._bindings.popitem(last=False)
        except Exception:
            logger.debug("Failed to bind LLM snapshot message", exc_info=True)

    def metadata_for_invocation(self, invocation_id: str) -> list[dict]:
        """Return lightweight envelopes of every attempt of an invocation.

        Args:
            invocation_id: Invocation id set on the request scope.

        Returns:
            Envelopes in attempt order, without raw payloads.
        """

        if not invocation_id:
            return []
        try:
            self._purge(self._clock())
            items = []
            for snapshot_id in self._by_invocation.get(invocation_id, ()):
                envelope = self._snapshots.get(snapshot_id)
                if envelope is not None:
                    items.append(_light_envelope(envelope))
            return items
        except Exception:
            logger.debug("Failed to read invocation snapshots", exc_info=True)
            return []

    @staticmethod
    def limits() -> dict:
        """Return the public retention limits.

        The 512 KB per-snapshot budget is enforced on the serialized UTF-8 bytes
        of the sanitized body.
        """

        return {
            "max_bytes_per_snapshot": MAX_BYTES_PER_SNAPSHOT,
            "max_snapshots_per_session": MAX_SNAPSHOTS_PER_SESSION,
            "max_bytes_per_session": MAX_BYTES_PER_SESSION,
            "max_total_bytes": MAX_TOTAL_BYTES,
            "max_message_bindings": MAX_MESSAGE_BINDINGS,
            "ttl_seconds": TTL_SECONDS,
        }

    def reset(self) -> None:
        """Drop every snapshot, binding and counter."""

        self._snapshots.clear()
        self._raw.clear()
        self._sessions.clear()
        self._session_bytes.clear()
        self._dropped.clear()
        self._total_bytes = 0
        self._attempts.clear()
        self._by_invocation.clear()
        self._bindings.clear()

    def _next_attempt(self, scope: LLMRequestScope) -> int:
        """Increment and return the attempt counter of the scope."""

        key = scope.invocation_id or "|".join(
            str(part)
            for part in (
                scope.session_id,
                scope.turn_id,
                scope.kind,
                scope.step,
                scope.stage,
            )
        )
        attempt = self._attempts.get(key, 0) + 1
        self._attempts[key] = attempt
        self._attempts.move_to_end(key)
        while len(self._attempts) > _MAX_TRACKED_ATTEMPTS:
            self._attempts.popitem(last=False)
        return attempt

    def _store(self, session_id: str, envelope: dict, raw: object) -> None:
        """Insert one envelope and enforce all retention limits."""

        snapshot_id = envelope["snapshot_id"]
        size = int(envelope.get("bytes") or 0)
        self._snapshots[snapshot_id] = envelope
        self._raw[snapshot_id] = raw
        ids = self._sessions.get(session_id)
        if ids is None:
            ids = deque()
            self._sessions[session_id] = ids
        ids.append(snapshot_id)
        self._sessions.move_to_end(session_id)
        self._session_bytes[session_id] = self._session_bytes.get(session_id, 0) + size
        self._total_bytes += size
        invocation_id = envelope.get("invocation_id")
        if invocation_id:
            self._by_invocation.setdefault(invocation_id, []).append(snapshot_id)
        self._enforce_limits(session_id)

    def _enforce_limits(self, session_id: str) -> None:
        """Evict oldest snapshots until count/byte caps hold."""

        while True:
            ids = self._sessions.get(session_id)
            if not ids or len(ids) <= MAX_SNAPSHOTS_PER_SESSION:
                break
            self._remove(session_id, ids[0], dropped=True)
        while self._session_bytes.get(session_id, 0) > MAX_BYTES_PER_SESSION:
            ids = self._sessions.get(session_id)
            if not ids:
                break
            self._remove(session_id, ids[0], dropped=True)
        while self._total_bytes > MAX_TOTAL_BYTES:
            owner = next(
                (session for session, ids in self._sessions.items() if ids),
                None,
            )
            if owner is None:
                break
            self._remove(owner, self._sessions[owner][0], dropped=True)
        while len(self._sessions) > _MAX_TRACKED_SESSIONS:
            oldest = next(iter(self._sessions))
            ids = self._sessions[oldest]
            while ids:
                self._remove(oldest, ids[0], dropped=True)
            self._sessions.pop(oldest, None)
            self._session_bytes.pop(oldest, None)

    def _remove(self, session_id: str, snapshot_id: str, *, dropped: bool) -> None:
        """Remove one snapshot from every index.

        A session whose deque becomes empty is dropped entirely so idle
        sessions cannot accumulate bookkeeping entries.
        """

        envelope = self._snapshots.pop(snapshot_id, None)
        if envelope is None:
            ids = self._sessions.get(session_id)
            if ids and snapshot_id in ids:
                try:
                    ids.remove(snapshot_id)
                except ValueError:
                    pass
                if not ids:
                    self._sessions.pop(session_id, None)
                    self._session_bytes.pop(session_id, None)
            return
        self._raw.pop(snapshot_id, None)
        size = int(envelope.get("bytes") or 0)
        self._total_bytes = max(0, self._total_bytes - size)
        remaining = self._session_bytes.get(session_id, 0) - size
        if remaining > 0:
            self._session_bytes[session_id] = remaining
        else:
            self._session_bytes.pop(session_id, None)
        ids = self._sessions.get(session_id)
        if ids is not None:
            try:
                ids.remove(snapshot_id)
            except ValueError:
                pass
            if not ids:
                self._sessions.pop(session_id, None)
                self._session_bytes.pop(session_id, None)
        invocation_id = envelope.get("invocation_id")
        if invocation_id:
            invocation_ids = self._by_invocation.get(invocation_id)
            if invocation_ids:
                try:
                    invocation_ids.remove(snapshot_id)
                except ValueError:
                    pass
                if not invocation_ids:
                    self._by_invocation.pop(invocation_id, None)
        if dropped:
            self._dropped[session_id] = self._dropped.get(session_id, 0) + 1
            self._dropped.move_to_end(session_id)
            while len(self._dropped) > _MAX_TRACKED_SESSIONS:
                self._dropped.popitem(last=False)

    def _purge(self, now: float) -> None:
        """Drop expired snapshots from every session."""

        for session_id in list(self._sessions):
            ids = self._sessions.get(session_id)
            while ids:
                envelope = self._snapshots.get(ids[0])
                if envelope is None:
                    self._remove(session_id, ids[0], dropped=False)
                    continue
                if float(envelope.get("created_at") or 0) + TTL_SECONDS > now:
                    break
                self._remove(session_id, ids[0], dropped=False)
        self._purge_bindings(now)

    def _purge_bindings(self, now: float) -> None:
        """Drop expired message bindings."""

        for key in list(self._bindings):
            if self._bindings[key][2] <= now:
                self._bindings.pop(key, None)

    def _resolve_binding(
        self, message_id: int | str, now: float
    ) -> tuple[str, str | None, float] | None:
        """Return a live message binding, or ``None`` when expired/unknown."""

        key = str(message_id)
        binding = self._bindings.get(key)
        if binding is None:
            return None
        if binding[2] <= now:
            self._bindings.pop(key, None)
            return None
        return binding


request_snapshots = RequestSnapshotRecorder()
"""Singleton recorder used by providers, the runner and the dashboard."""
