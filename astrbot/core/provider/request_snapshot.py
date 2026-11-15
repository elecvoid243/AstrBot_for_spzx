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
"""Character budget handed to the sanitizer for one request body.

The serialized byte cap is structurally enforced in Task 2; until then this
value only bounds the sanitizer traversal.
"""

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
            adapter: Snapshot adapter name; defaults to the provider attribute.
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
            sanitized = bounded_context_value(raw, limit=MAX_BYTES_PER_SNAPSHOT)
            body = sanitized.get("value")
            serialized = json.dumps(body, ensure_ascii=False, default=str)
            size = len(serialized.encode("utf-8"))
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
                "truncated": bool(sanitized.get("truncated")),
                "redactions": int(sanitized.get("redactions") or 0),
                "provider": _build_provider_info(provider),
                "request": request_info,
                "counts": counts,
                "response": None,
                "tool_executions": [],
                "diagnostics": {
                    "compression": None,
                    "cache": None,
                    "budget": None,
                    "retries": None,
                    "stop_reason": None,
                    "omitted_messages": 0,
                },
                "provenance": [],
                "injections": [],
                "tool_meta": {},
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

        The 512 KB per-snapshot budget is currently a character budget on the
        sanitized copy; structural enforcement of the serialized byte cap lands
        in Task 2, so callers must not treat it as fully enforced yet.
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
