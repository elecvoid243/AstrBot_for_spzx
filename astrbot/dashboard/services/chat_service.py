from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from astrbot.core import logger, sp
from astrbot.core.agent.message import get_checkpoint_id, is_checkpoint_message
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.db import BaseDatabase
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.webchat.message_parts_helper import (
    build_webchat_message_parts,
    create_attachment_part_from_existing_file,
    strip_message_parts_path_fields,
    webchat_message_parts_have_content,
)
from astrbot.core.platform.sources.webchat.request_flags import (
    resolve_webchat_request_flags,
)
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.utils.active_event_registry import active_event_registry
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.core.utils.datetime_utils import generate_timestamp_id, to_utc_isoformat
from astrbot.core.utils.media_utils import (
    MEDIA_MIME_EXTENSIONS,
    detect_image_mime_type_async,
)

SSE_HEARTBEAT = ": heartbeat\n\n"
CHAT_RUN_SUBSCRIBER_QUEUE_SIZE = 256
# ChatUI history windowing: opening a session fetches only the most recent
# messages; older pages are loaded on demand via /chat/sessions/{id}/history.
HISTORY_WINDOW_SIZE = 50
MAX_HISTORY_PAGE_SIZE = 200
# Message marker index (user-message dots on the ChatUI scroll strip):
# capped scan pages so a pathological session cannot run away.
MESSAGE_MARKER_PAGE_SIZE = 500
MAX_MESSAGE_MARKERS = 1000
WEBCHAT_IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def sanitize_upload_filename(filename: str | None) -> str:
    if not filename:
        return generate_timestamp_id()
    normalized = filename.replace("\\", "/")
    name = PurePosixPath(normalized).name.replace("\x00", "").strip()
    if name in ("", ".", ".."):
        return generate_timestamp_id()
    return name


def normalize_reasoning_message_parts(
    message_parts: list[dict] | None,
    reasoning: str = "",
) -> list[dict]:
    parts: list[dict] = []
    for part in message_parts or []:
        if not isinstance(part, dict):
            continue
        copied = dict(part)
        if copied.get("type") == "reasoning":
            copied = {"type": "think", "think": copied.get("text", "")}
        parts.append(copied)
    if reasoning and not any(part.get("type") == "think" for part in parts):
        parts.insert(0, {"type": "think", "think": reasoning})
    return parts


def extract_reasoning_from_message_parts(message_parts: list[dict]) -> str:
    reasoning_parts: list[str] = []
    for part in message_parts:
        if part.get("type") != "think":
            continue
        think = part.get("think")
        if isinstance(think, str) and think:
            reasoning_parts.append(think)
    return "".join(reasoning_parts)


def collect_plain_text_from_message_parts(message_parts: list[dict]) -> str:
    text_parts: list[str] = []
    for part in message_parts:
        if part.get("type") != "plain":
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            text_parts.append(text)
    return "".join(text_parts)


# Author: elecvoid243
# Date: 2026-07-05
# Plan: docs/superpowers/plans/2026-07-05-interactive-choice-history-roundtrip.md
# §2.2 Amendment F (read-path defence, v2 — see `tests/unit/test_chat_service_sanitize_history.py`
# for the cases that motivated widening the rule).
#
# `BotMessageAccumulator`'s write-path filter (see `_store_tool_call` /
# `_store_tool_call_result`) prevents NEW `tool_call` and `tool_call_result`
# events for `ask_user_choice` from materialising into history parts. Bot
# records persisted BEFORE that filter shipped — or while a still-running
# AstrBot still had the old code — carry two stale halves per invocation:
#
#   Part A (args only)::
#
#       {"type": "tool_call",
#        "tool_calls": [{"name": "ask_user_choice", "args": {...}, "ts": ...}]}
#
#   Part B (synthesised result fallback)::
#
#       {"type": "tool_call",
#        "tool_calls": [{"id": "...", "result": "...", "finished_ts": ...}]}
#
# Both render in the dashboard as separate ToolCallCards next to the
# InteractiveChoiceBox on a hard refresh. We strip them on the read path so
# the API response is clean regardless of when the record was created. The
# `interactive_choice` part written by the plugin's chain_type event is the
# canonical user-visible representation; the box state itself comes from
# the round-2 per-UMO `submissionStates` store, so no information is lost.
_ASK_USER_CHOICE_TOOL_NAME = "ask_user_choice"


def _is_ask_user_choice_stale_entry(tool_call_entry: object) -> bool:
    """True if a single tool_call entry looks like a stale ask_user_choice half.

    Used both for the interactive_choice-paired mode (drop when there's a
    matching `interactive_choice` part in the same message) and for the
    pairing-based mode (drop when paired with another entry in the same
    part). The runtime always sends ``name`` in tool_call events, so a
    name-less entry with a result is unambiguously the synthesised
    fallback created by `_store_tool_call_result` for an ask_user_choice
    call whose `tool_call` half was filtered out — never a legitimate
    real tool invocation.
    """
    if not isinstance(tool_call_entry, dict):
        return False
    if tool_call_entry.get("name") == _ASK_USER_CHOICE_TOOL_NAME:
        return True
    if not tool_call_entry.get("name") and tool_call_entry.get("result"):
        return True
    return False


def _drop_stale_entries_from_part(
    part: dict, drop_predicate: callable | None
) -> dict | None:
    """Return a new tool_call part with stale entries removed.

    Args:
        part: A tool_call part dict.
        drop_predicate: Callable returning True for stale entries. If
            ``None``, the entry is kept (used as the safe default — a
            malformed record must not silently drop more than it should).

    Returns:
        A new part with the surviving entries, or ``None`` if every entry
        was dropped (so the caller can omit the empty shell). For
        non-dict / id-less entries the predicate is bypassed and the
        entry passes through unchanged so we never reshape corrupted
        data into ``{}`` on the read path.
    """
    if drop_predicate is None:
        return part
    kept_tool_calls: list = []
    for tc in part.get("tool_calls") or []:
        if not isinstance(tc, dict) or not str(tc.get("id") or ""):
            kept_tool_calls.append(tc)
            continue
        if not drop_predicate(tc):
            kept_tool_calls.append(tc)
    if not kept_tool_calls:
        return None
    return {**part, "tool_calls": kept_tool_calls}


def _sanitize_ask_user_choice_tool_call_parts(parts: object) -> object:
    """Drop stale `ask_user_choice` `tool_call` parts from a bot message.

    Args:
        parts: The `content.message` list of a bot history record. May be
            ``None`` or non-list — those pass through untouched.

    Returns:
        A new list with the matching `tool_call` parts (or matching entries
        inside a multi-entry `tool_call` part) removed. Non-`tool_call`
        parts are preserved unchanged. When no stale entries are found the
        original list is returned so the no-op case stays allocation-free
        in the common (new-history) path.

    Notes:
        The rule is split by whether the message also carries an
        ``interactive_choice`` part written by the ask_user_choice plugin:

        * **Message HAS ``interactive_choice``**: every tool_call entry
          that *looks* like an ask_user_choice stale half is dropped
          unconditionally — this catches BOTH the original
          args-only half and the orphan synthesised result half (the
          latter no longer requires a paired `ask_user_choice` named
          half in the same message). The runtime always sends
          ``name`` in tool_call events, so a name-less entry with a
          result is unambiguously the synthesised fallback and never a
          legitimate other-tool call.

        * **Message has NO ``interactive_choice``**: apply the
          conservative pairing rule so a multi-tool message whose
          ask_user_choice part is somehow present without its box
          (a malformed pre-feature DB row) still gets cleaned, but a
          truly anonymous result with no ask_user_choice peer is
          left alone.
    """
    if not isinstance(parts, list):
        return parts

    has_interactive_choice = any(
        isinstance(p, dict) and p.get("type") == "interactive_choice" for p in parts
    )

    if has_interactive_choice:
        drop_predicate = _is_ask_user_choice_stale_entry
    else:
        # Conservative mode: collect ask_user_choice ids in the message
        # and drop only those PLUS paired anonymous result halves.
        ask_user_choice_ids: set[str] = set()
        anonymous_result_ids: set[str] = set()
        for part in parts:
            if not isinstance(part, dict) or part.get("type") != "tool_call":
                continue
            for tc in part.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                tc_id = str(tc.get("id") or "")
                if not tc_id:
                    continue
                if tc.get("name") == _ASK_USER_CHOICE_TOOL_NAME:
                    ask_user_choice_ids.add(tc_id)
                elif not tc.get("name") and tc.get("result"):
                    anonymous_result_ids.add(tc_id)
        paired_ids = ask_user_choice_ids & anonymous_result_ids
        drop_ids = ask_user_choice_ids | paired_ids
        if not drop_ids:
            return parts

        def drop_predicate(tc: dict, _drop: set[str] = drop_ids) -> bool:
            return str(tc.get("id") or "") in _drop

    sanitized: list[dict] = []
    mutated = False
    for part in parts:
        if not isinstance(part, dict) or part.get("type") != "tool_call":
            sanitized.append(part)
            continue
        new_part = _drop_stale_entries_from_part(part, drop_predicate)
        if new_part is None:
            mutated = True
            continue
        if new_part is not part:
            mutated = True
        sanitized.append(new_part)
    if not mutated:
        return parts
    return sanitized


def _sanitize_history_bot_records(history_data: list[dict]) -> list[dict]:
    """Apply :func:`_sanitize_ask_user_choice_tool_call_parts` to bot rows.

    Args:
        history_data: List of platform-history record dicts (as produced by
            ``record.model_dump()``). User rows and rows without a ``content``
            dict are passed through untouched.

    Returns:
        The same list with stale `tool_call` parts stripped from every bot
        record's `content.message`. Mutates the input dicts in place for
        the rows that need cleaning and returns the same list.
    """
    for record in history_data:
        if not isinstance(record, dict):
            continue
        content = record.get("content")
        if not isinstance(content, dict) or content.get("type") != "bot":
            continue
        message = content.get("message")
        sanitized = _sanitize_ask_user_choice_tool_call_parts(message)
        if sanitized is not message:
            content["message"] = sanitized
    return history_data


def build_bot_history_content(
    message_parts: list[dict],
    *,
    agent_stats: dict | None = None,
    refs: dict | None = None,
    include_reasoning_field: bool = True,
) -> dict[str, Any]:
    normalized_parts = normalize_reasoning_message_parts(message_parts)
    content: dict[str, Any] = {"type": "bot", "message": normalized_parts}
    reasoning = extract_reasoning_from_message_parts(normalized_parts)
    if reasoning and include_reasoning_field:
        content["reasoning"] = reasoning
    if agent_stats:
        content["agent_stats"] = agent_stats
    if refs:
        content["refs"] = refs
    return content


class BotMessageAccumulator:
    def __init__(self) -> None:
        self.parts: list[dict] = []
        self.pending_text = ""
        self.pending_tool_calls: dict[str, dict] = {}
        # Author: elecvoid243
        # Date: 2026-07-25
        # Plan: orphan goal-loop turn agent_stats persistence fix.
        # The primary `_consume_chat_run` path decouples `run.agent_stats`
        # from this accumulator (it stores on `ChatRunState` directly and
        # never calls `add_plain(chain_type="agent_stats")`). The system
        # event stream path (orphan goal-loop turns) DOES route
        # `chain_type="agent_stats"` payloads through `add_plain`, so the
        # accumulator needs its own slot to remember the latest stats
        # until `flush()` passes it to `save_bot_message`. Initialised
        # here as a purely additive field — Normal turn flow never reads
        # or writes it, so behavior is unchanged.
        self.pending_agent_stats: dict = {}
        # Author: elecvoid243
        # Date: 2026-07-05
        # Plan: docs/superpowers/plans/2026-07-05-interactive-choice-history-roundtrip.md
        # §2.2 Amendment F — `ask_user_choice` is rendered exclusively as an
        # `interactive_choice` part, so its underlying `tool_call` and
        # `tool_call_result` events must NOT produce a `tool_call` part.
        # We still need to remember which call_ids were filtered so the
        # later `tool_call_result` event can be silently discarded too
        # (otherwise the existing fallback in `_store_tool_call_result`
        # would synthesise a part with only {id, result, finished_ts} —
        # a name-less "tool" entry that renders next to the
        # InteractiveChoiceBox after a hard refresh).
        self._filtered_tool_call_ids: set[str] = set()
        # Author: elecvoid243
        # Date: 2026-07-26
        # Plan: docs/superpowers/plans/2026-07-26-subagent-chatui-progress.md
        # Task 4 — persist subagent execution progress as a structured
        # `subagent_run` part so a hard refresh restores the block.
        # run_id -> the SAME dict object appended to self.parts, mutated
        # in place as further events arrive.
        self.subagent_runs: dict[str, dict] = {}

    def has_content(self) -> bool:
        # Author: elecvoid243
        # Date: 2026-07-25
        # Plan: orphan goal-loop turn agent_stats persistence fix.
        # Include `pending_agent_stats` so orphan turns that consist
        # solely of `agent_stats` events (no plain text, no tool calls)
        # still flush. Safe for the primary path: Normal turn never
        # calls `add_plain(chain_type="agent_stats")`, so the
        # accumulator's `pending_agent_stats` stays `{}` (falsy) and
        # the OR-chain resolves identically to before this change.
        return bool(
            self.parts
            or self.pending_text
            or self.pending_tool_calls
            or self.pending_agent_stats
            or self.subagent_runs
        )

    def add_plain(
        self,
        result_text: str,
        *,
        chain_type: str | None,
        streaming: bool,
    ) -> None:
        if chain_type == "tool_call":
            self._flush_pending_text()
            self._store_tool_call(result_text)
            return

        if chain_type == "tool_call_result":
            self._flush_pending_text()
            self._store_tool_call_result(result_text)
            return

        if chain_type == "reasoning":
            self._flush_pending_text()
            self._append_think_part(result_text)
            return

        # Author: elecvoid243
        # Date: 2026-07-05
        # Bug fix: history round-trip for `ask_user_choice` (Plan
        # `docs/superpowers/plans/2026-07-05-interactive-choice-history-roundtrip.md`).
        # The plugin emits the box as a chain_type event so this
        # accumulator can persist an `interactive_choice` part into
        # the bot record. Without this, a hard refresh has no part
        # to render in the right slot and orphan-injection dumps
        # every box at the page tail.
        if chain_type == "interactive_choice":
            self._flush_pending_text()
            self._store_interactive_choice(result_text)
            return

        # Author: elecvoid243
        # Date: 2026-07-25
        # Plan: orphan goal-loop turn agent_stats persistence fix.
        # The system event stream forwards every payload (including
        # `type:"plain" + chain_type:"agent_stats"`) verbatim to
        # `add_plain`. Without this branch the JSON stats blob would
        # fall through to the default streaming/pending_text path and
        # end up as a literal `{"type":"plain","text":"{...}"}` part
        # on the persisted bot record. Parse it into a dict and stash
        # on the accumulator; `flush()` (system stream only) passes it
        # to `save_bot_message`.
        #
        # Normal turn path is unaffected: the primary `_consume_chat_run`
        # in this same module does `continue` on `chain_type=="agent_stats"`
        # BEFORE reaching `add_plain`, so this branch is dead code there.
        if chain_type == "agent_stats":
            try:
                self.pending_agent_stats = (
                    json.loads(result_text) if result_text else {}
                )
            except (TypeError, json.JSONDecodeError):
                # Better to drop a malformed stats blob than persist
                # a JSON literal as plain text. Mirrors the existing
                # `json.JSONDecodeError` handling in
                # `_consume_chat_run` (run.agent_stats = {} on failure).
                self.pending_agent_stats = {}
            return

        if streaming:
            self.pending_text += result_text
        else:
            self.pending_text = result_text

    def add_attachment(self, part: dict | None) -> None:
        if not part:
            return
        self._flush_pending_text()
        self.parts.append(part)

    def add_subagent_event(self, data: dict) -> None:
        """Fold one ``subagent_event`` payload into a ``subagent_run`` part.

        The part is appended to ``self.parts`` on first sight (preserving
        chronological position relative to plain text) and mutated in place
        afterwards, so ``build_message_parts`` always reflects the latest
        run state.

        Args:
            data: The ``data`` field of a ``subagent_event`` payload.
        """
        if not isinstance(data, dict):
            return
        run_id = str(data.get("subagent_run_id") or "")
        if not run_id:
            return
        kind = str(data.get("kind") or "")
        payload = data.get("payload")
        payload = payload if isinstance(payload, dict) else {}

        part = self.subagent_runs.get(run_id)
        if part is None:
            self._flush_pending_text()
            part = {
                "type": "subagent_run",
                "subagent_run_id": run_id,
                "agent_name": str(data.get("agent_name") or ""),
                "status": "running",
                "input_preview": "",
                "text": "",
                "reasoning": "",
                "tool_calls": [],
                "activity": [],
                "started_ts": data.get("ts"),
                "execution_time": None,
            }
            self.subagent_runs[run_id] = part
            self.parts.append(part)

        if kind == "started":
            part["input_preview"] = str(payload.get("input_preview") or "")
        elif kind == "text_delta":
            # Streamed assistant text is intermediate narration; keep it in
            # the chronological activity log. The final answer arrives via
            # the completed event's result_text.
            text = str(payload.get("text") or "")
            if part["activity"] and part["activity"][-1]["kind"] == "text":
                part["activity"][-1]["text"] += text
            else:
                part["activity"].append({"kind": "text", "text": text})
        elif kind == "reasoning_delta":
            text = str(payload.get("text") or "")
            part["reasoning"] += text
            # Append to the current think block, or open a new one when a
            # tool call happened in between — preserves the chronological
            # think -> tool_call order of the LLM loop.
            if part["activity"] and part["activity"][-1]["kind"] == "think":
                part["activity"][-1]["text"] += text
            else:
                part["activity"].append({"kind": "think", "text": text})
        elif kind == "tool_call":
            call_id = payload.get("id")
            if call_id is not None:
                existing = next(
                    (t for t in part["tool_calls"] if t.get("id") == call_id), None
                )
                if existing:
                    existing.update(payload)
                else:
                    call = dict(payload)
                    if data.get("ts") is not None:
                        call["ts"] = data["ts"]
                    part["tool_calls"].append(call)
                    part["activity"].append({"kind": "tool_call", "call": call})
        elif kind == "tool_call_result":
            call_id = payload.get("id")
            existing = next(
                (t for t in part["tool_calls"] if t.get("id") == call_id), None
            )
            if existing:
                existing["result"] = payload.get("result", "")
                if data.get("ts") is not None:
                    existing["finished_ts"] = data["ts"]
            elif call_id is not None:
                call = dict(payload)
                if data.get("ts") is not None:
                    call["finished_ts"] = data["ts"]
                part["tool_calls"].append(call)
                part["activity"].append({"kind": "tool_call", "call": call})
        elif kind == "completed":
            part["status"] = "completed"
            if payload.get("result_text"):
                part["text"] = str(payload["result_text"])
            # The final turn's streamed text duplicates result_text; drop it
            # from the activity log so the answer only shows in the Result
            # section.
            while part["activity"] and part["activity"][-1]["kind"] == "text":
                part["activity"].pop()
            if isinstance(payload.get("execution_time"), int | float):
                part["execution_time"] = payload["execution_time"]
        elif kind in ("failed", "timeout"):
            part["status"] = kind
            if payload.get("error"):
                part["error"] = str(payload["error"])

    def build_message_parts(
        self, *, include_pending_tool_calls: bool = False
    ) -> list[dict]:
        self._flush_pending_text()
        if include_pending_tool_calls and self.pending_tool_calls:
            for tool_call in self.pending_tool_calls.values():
                self.parts.append({"type": "tool_call", "tool_calls": [tool_call]})
            self.pending_tool_calls = {}
        return self.parts

    def plain_text(self) -> str:
        return collect_plain_text_from_message_parts(self.build_message_parts())

    def reasoning_text(self) -> str:
        return extract_reasoning_from_message_parts(self.build_message_parts())

    def _flush_pending_text(self) -> None:
        if not self.pending_text:
            return

        if self.parts and self.parts[-1].get("type") == "plain":
            last_text = self.parts[-1].get("text")
            self.parts[-1]["text"] = f"{last_text or ''}{self.pending_text}"
        else:
            self.parts.append({"type": "plain", "text": self.pending_text})
        self.pending_text = ""

    def _append_think_part(self, text: str) -> None:
        if not text:
            return

        if self.parts and self.parts[-1].get("type") == "think":
            last_text = self.parts[-1].get("think")
            self.parts[-1]["think"] = f"{last_text or ''}{text}"
        else:
            self.parts.append({"type": "think", "think": text})

    # Author: elecvoid243
    # Date: 2026-07-05
    # Plan: docs/superpowers/plans/2026-07-05-interactive-choice-history-roundtrip.md
    # §2.2 Amendment F.
    #
    # `ask_user_choice` is rendered as an `interactive_choice` part on
    # the dashboard. Persisting its `tool_call` as well would cause the
    # ToolCallCard to render a phantom "tool" entry next to the
    # InteractiveChoiceBox after a hard refresh.
    #
    # We still record the call_id in `_filtered_tool_call_ids` so the
    # later `tool_call_result` event is silently dropped (see
    # `_store_tool_call_result`). The runtime's `Json` payload for
    # `tool_call` always carries the `name` field, so this check is
    # reliable — unlike `tool_call_result` which omits `name`.
    _ASK_USER_CHOICE_NAME = "ask_user_choice"

    def _store_tool_call(self, result_text: str) -> None:
        tool_call = self._parse_json_object(result_text)
        if not tool_call:
            return
        tool_call_id = str(tool_call.get("id") or "")
        if not tool_call_id:
            return
        if tool_call.get("name") == self._ASK_USER_CHOICE_NAME:
            # Mark the id as filtered so the matching tool_call_result
            # is dropped in `_store_tool_call_result`. Do not add to
            # `pending_tool_calls` so a subsequent non-ask_user_choice
            # call cannot accidentally collide on the same id (the
            # `name` filter is per-event, not global).
            self._filtered_tool_call_ids.add(tool_call_id)
            return
        self.pending_tool_calls[tool_call_id] = tool_call

    def _store_tool_call_result(self, result_text: str) -> None:
        tool_result = self._parse_json_object(result_text)
        if not tool_result:
            return

        tool_call_id = str(tool_result.get("id") or "")
        if not tool_call_id:
            return

        # Drop the result if the original `tool_call` was filtered
        # (e.g. `ask_user_choice`). Without this branch the fallback
        # below would synthesise a `tool_call` part with only
        # `{id, result, finished_ts}` — a name-less entry that the
        # dashboard renders as "tool" (the ToolCallCard name
        # fallback), producing the duplicate visible in the bug
        # report.
        if tool_call_id in self._filtered_tool_call_ids:
            self._filtered_tool_call_ids.discard(tool_call_id)
            return

        tool_call = self.pending_tool_calls.pop(tool_call_id, None) or {
            "id": tool_call_id
        }
        tool_call["result"] = tool_result.get("result")
        tool_call["finished_ts"] = tool_result.get("ts")
        self.parts.append({"type": "tool_call", "tool_calls": [tool_call]})

    # Author: elecvoid243
    # Date: 2026-07-05
    # Plan: docs/superpowers/plans/2026-07-05-interactive-choice-history-roundtrip.md §2.2
    # Persist an `interactive_choice` part alongside the bot record
    # so the box round-trips through history reload. The plugin emits
    # the event with `type="plain"`, `chain_type="interactive_choice"`,
    # and `data` = json string of
    #   {"request_id": ..., "spec": {...}, "expires_at": ...}.
    def _store_interactive_choice(self, result_text: str) -> None:
        """Parse an interactive_choice chain_type event and append a part.

        Args:
            result_text: JSON-stringified envelope produced by
                ``astrbot_plugin_ask_user_choice.ask_user_choice_tool.
                _push_to_webchat_back_queue``. Schema::

                    {
                        "request_id": "<uuid>",
                        "spec": {
                            "type": "interactive_choice",
                            "prompt": "...",
                            "options": [{"id": "...", "label": "..."}],
                            "title": "...",            # optional
                            "input_placeholder": "..." # optional
                            "extra_content": "..."     # optional, v1.1,
                                                        # <= 5000 chars
                                                        # Markdown
                        },
                        "expires_at": <unix ts>       # optional
                    }
        """
        payload = self._parse_json_object(result_text)
        if not payload:
            return
        request_id = str(payload.get("request_id") or "").strip()
        spec = payload.get("spec")
        if not request_id or not isinstance(spec, dict):
            return
        prompt = str(spec.get("prompt") or "").strip()
        options = spec.get("options")
        if not prompt or not isinstance(options, list):
            return
        part: dict = {
            "type": "interactive_choice",
            "request_id": request_id,
            "prompt": prompt,
            "options": options,
        }
        title = spec.get("title")
        if isinstance(title, str) and title.strip():
            part["title"] = title
        placeholder = spec.get("input_placeholder")
        if isinstance(placeholder, str) and placeholder.strip():
            part["input_placeholder"] = placeholder
        # v1.1: persist the LLM-authored Markdown prose so the box
        # round-trips through a hard refresh. Length cap is enforced
        # by `_push_to_webchat_back_queue` in
        # `astrbot_plugin_ask_user_choice` (and re-checked on the
        # frontend by `truncateInteractiveChoice`); the dashboard
        # only mirrors the field, never re-validates the cap.
        extra_content = spec.get("extra_content")
        if isinstance(extra_content, str) and extra_content.strip():
            part["extra_content"] = extra_content
        expires_at = payload.get("expires_at")
        if isinstance(expires_at, (int, float)):
            part["expires_at"] = expires_at
        self.parts.append(part)

    @staticmethod
    def _parse_json_object(raw_text: str) -> dict | None:
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None


def extract_web_search_refs(accumulated_text: str, accumulated_parts: list) -> dict:
    supported = [
        "web_search_baidu",
        "web_search_tavily",
        "web_search_bocha",
        "web_search_brave",
    ]
    web_search_results = {}
    tool_call_parts = [
        p
        for p in accumulated_parts
        if p.get("type") == "tool_call" and p.get("tool_calls")
    ]

    for part in tool_call_parts:
        for tool_call in part["tool_calls"]:
            if tool_call.get("name") not in supported or not tool_call.get("result"):
                continue
            try:
                result_data = json.loads(tool_call["result"])
                for item in result_data.get("results", []):
                    if idx := item.get("index"):
                        web_search_results[idx] = {
                            "url": item.get("url"),
                            "title": item.get("title"),
                            "snippet": item.get("snippet"),
                        }
            except (json.JSONDecodeError, KeyError):
                pass

    if not web_search_results:
        return {}

    ref_indices = {m.strip() for m in re.findall(r"<ref>(.*?)</ref>", accumulated_text)}
    used_refs = []
    for ref_index in ref_indices:
        if ref_index not in web_search_results:
            continue
        payload = {"index": ref_index, **web_search_results[ref_index]}
        if favicon := sp.temporary_cache.get("_ws_favicon", {}).get(payload["url"]):
            payload["favicon"] = favicon
        used_refs.append(payload)

    return {"used": used_refs} if used_refs else {}


def sanitize_message_content(content: dict) -> dict:
    if not isinstance(content, dict):
        raise ValueError("Missing key: content")

    normalized = deepcopy(content)
    message_type = normalized.get("type")
    if message_type not in {"user", "bot"}:
        raise ValueError("Invalid key: content.type")

    message_parts = normalized.get("message")
    if not isinstance(message_parts, list):
        raise ValueError("Missing key: content.message")
    normalized["message"] = strip_message_parts_path_fields(message_parts)
    return normalized


def extract_platform_message_text(content: dict | None) -> str:
    if not isinstance(content, dict):
        return ""
    message_parts = content.get("message")
    if not isinstance(message_parts, list):
        return ""
    texts: list[str] = []
    for part in message_parts:
        if isinstance(part, dict) and part.get("type") == "plain":
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
    return "".join(texts)


def build_webchat_unified_msg_origin(session) -> str:
    message_type = (
        MessageType.GROUP_MESSAGE.value
        if session.is_group
        else MessageType.FRIEND_MESSAGE.value
    )
    return (
        f"{session.platform_id}:{message_type}:"
        f"{session.platform_id}!{session.creator}!{session.session_id}"
    )


def build_thread_unified_msg_origin(creator: str, thread_id: str) -> str:
    return f"webchat:{MessageType.FRIEND_MESSAGE.value}:webchat!{creator}!{thread_id}"


def serialize_thread(thread) -> dict:
    from astrbot.core.utils.datetime_utils import to_utc_isoformat

    return {
        "thread_id": thread.thread_id,
        "parent_session_id": thread.parent_session_id,
        "parent_message_id": thread.parent_message_id,
        "base_checkpoint_id": thread.base_checkpoint_id,
        "selected_text": thread.selected_text,
        "created_at": to_utc_isoformat(thread.created_at),
        "updated_at": to_utc_isoformat(thread.updated_at),
    }


def serialize_history_entry(history) -> dict:
    """Serialize a PlatformMessageHistory record with UTC-aware timestamps.

    Args:
        history: A PlatformMessageHistory instance. Must not be None.

    Returns:
        Dict with all model fields plus created_at/updated_at serialized as
        UTC-aware ISO strings (e.g. ``2026-07-06T04:00:00+00:00``).
    """
    return {
        **history.model_dump(),
        "created_at": to_utc_isoformat(history.created_at),
        "updated_at": to_utc_isoformat(history.updated_at),
    }


def find_checkpoint_index(history: list[dict], checkpoint_id: str) -> int | None:
    for index, message in enumerate(history):
        if get_checkpoint_id(message) == checkpoint_id:
            return index
    return None


def find_turn_range(history: list[dict], checkpoint_id: str) -> tuple[int, int] | None:
    checkpoint_index = find_checkpoint_index(history, checkpoint_id)
    if checkpoint_index is None:
        return None

    start = 0
    for index in range(checkpoint_index - 1, -1, -1):
        if is_checkpoint_message(history[index]):
            start = index + 1
            break
    return start, checkpoint_index


def is_latest_checkpoint(history: list[dict], checkpoint_id: str) -> bool:
    for message in reversed(history):
        current_checkpoint_id = get_checkpoint_id(message)
        if current_checkpoint_id:
            return current_checkpoint_id == checkpoint_id
    return False


def replace_user_conversation_content(original_content, edited_text: str):
    if isinstance(original_content, str):
        return edited_text
    if not isinstance(original_content, list):
        return edited_text

    result: list[dict] = []
    inserted_text = False
    for part in original_content:
        if not isinstance(part, dict):
            result.append(part)
            continue
        if part.get("type") != "text":
            result.append(part)
            continue
        text = part.get("text")
        if isinstance(text, str) and text.startswith("<system_reminder>"):
            result.append(part)
            continue
        if not inserted_text and edited_text:
            result.append({"type": "text", "text": edited_text})
            inserted_text = True

    if not inserted_text and edited_text:
        result.insert(0, {"type": "text", "text": edited_text})
    return result


def replace_assistant_conversation_content(
    original_content,
    edited_text: str,
    reasoning: str,
):
    if isinstance(original_content, str):
        return edited_text
    if not isinstance(original_content, list):
        return [{"type": "text", "text": edited_text}] if edited_text else []

    result: list[dict] = []
    inserted_text = False
    inserted_think = False
    for part in original_content:
        if not isinstance(part, dict):
            result.append(part)
            continue
        if part.get("type") == "text":
            if not inserted_text and edited_text:
                result.append({"type": "text", "text": edited_text})
                inserted_text = True
            continue
        if part.get("type") == "think":
            if not inserted_think and reasoning:
                result.append({"type": "think", "think": reasoning})
                inserted_think = True
            continue
        result.append(part)

    if reasoning and not inserted_think:
        result.insert(0, {"type": "think", "think": reasoning})
    if edited_text and not inserted_text:
        result.append({"type": "text", "text": edited_text})
    return result


def find_turn_user_index(history: list[dict], start: int, end: int) -> int | None:
    for index in range(start, end):
        message = history[index]
        if isinstance(message, dict) and message.get("role") == "user":
            return index
    return None


def find_turn_final_assistant_index(
    history: list[dict], start: int, end: int
) -> int | None:
    for index in range(end - 1, start - 1, -1):
        message = history[index]
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        if message.get("tool_calls") and not message.get("content"):
            continue
        return index
    return None


def extract_attachment_ids(history_list) -> list[str]:
    attachment_ids = []
    for history in history_list:
        content = history.content
        if not content or "message" not in content:
            continue
        message_parts = content.get("message", [])
        for part in message_parts:
            if isinstance(part, dict) and "attachment_id" in part:
                attachment_ids.append(part["attachment_id"])
    return attachment_ids


class ChatServiceError(Exception):
    pass


@dataclass(slots=True)
class ChatRunState:
    """State owned by a WebChat generation independently of its subscribers."""

    run_id: str
    username: str
    session_id: str
    llm_checkpoint_id: str
    platform_history_id: str
    back_queue: asyncio.Queue
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    message_parts: list[dict] = field(default_factory=list)
    agent_stats: dict = field(default_factory=dict)
    refs: dict = field(default_factory=dict)
    revision: int = 0
    status: str = "running"
    task: asyncio.Task[None] | None = None


class ChatService:
    def __init__(
        self,
        db: BaseDatabase,
        core_lifecycle: AstrBotCoreLifecycle,
    ) -> None:
        self.db = db
        self.core_lifecycle = core_lifecycle
        self.attachments_dir = os.path.join(get_astrbot_data_path(), "attachments")
        self.webchat_img_dir = os.path.join(get_astrbot_data_path(), "webchat", "imgs")
        os.makedirs(self.attachments_dir, exist_ok=True)

        self.conv_mgr = core_lifecycle.conversation_manager
        self.platform_history_mgr = core_lifecycle.platform_message_history_manager
        self.umop_config_router = core_lifecycle.umop_config_router
        self.running_convs: dict[str, bool] = {}
        self.chat_runs: dict[str, ChatRunState] = {}
        self.chat_runs_by_session: dict[str, set[str]] = {}
        # 2026-08-13: branch-relation cache moved to the db layer
        # (`BaseDatabase.get_branch_relations`) so every dashboard service
        # sharing the db instance sees the same relations — the project
        # session list (ChatUIProjectService) needs them too.

    async def get_branch_relations(self) -> dict[str, dict]:
        """Return cached branch relations (see ``BaseDatabase``).

        The cache is updated incrementally by `branch_session`; stale entries
        (e.g. deleted sessions) are filtered out by callers at read time.
        """
        return await self.db.get_branch_relations()

    async def build_user_message_parts(self, message: str | list) -> list[dict]:
        return await build_webchat_message_parts(
            message,
            get_attachment_by_id=self.db.get_attachment_by_id,
            strict=False,
        )

    async def create_attachment_from_file(
        self, filename: str, attach_type: str, display_name: str | None = None
    ) -> dict | None:
        return await create_attachment_part_from_existing_file(
            filename,
            attach_type=attach_type,
            insert_attachment=self.db.insert_attachment,
            attachments_dir=self.attachments_dir,
            fallback_dirs=[self.webchat_img_dir],
            display_name=display_name,
        )

    async def resolve_webchat_file(
        self, filename: str | None
    ) -> tuple[str, str | None]:
        if not filename:
            raise ChatServiceError("Missing key: filename")

        safe_name = os.path.basename(filename)
        attachments_dir = Path(self.attachments_dir).resolve(strict=False)
        file_path = (attachments_dir / safe_name).resolve(strict=False)
        file_root = attachments_dir

        if not file_path.exists():
            webchat_img_dir = Path(self.webchat_img_dir).resolve(strict=False)
            webchat_file_path = (webchat_img_dir / safe_name).resolve(strict=False)
            if webchat_file_path.exists():
                file_path = webchat_file_path
                file_root = webchat_img_dir

        if not file_path.is_relative_to(file_root):
            raise ChatServiceError("Invalid file path")
        if not file_path.exists():
            raise ChatServiceError("File access error")

        filename_ext = file_path.suffix.lower()
        if filename_ext == ".wav":
            return str(file_path), "audio/wav"
        if filename_ext in WEBCHAT_IMAGE_MIME_TYPES:
            return str(file_path), WEBCHAT_IMAGE_MIME_TYPES[filename_ext]
        return str(file_path), None

    async def resolve_webchat_file_from_dashboard_query(
        self,
        filename: str | None,
    ) -> tuple[str, str | None]:
        return await self.resolve_webchat_file(filename)

    async def resolve_attachment_file(
        self,
        attachment_id: str | None,
    ) -> tuple[str, str | None]:
        if not attachment_id:
            raise ChatServiceError("Missing key: attachment_id")

        attachment = await self.db.get_attachment_by_id(attachment_id)
        if not attachment:
            raise ChatServiceError("Attachment not found")

        file_path = Path(attachment.path).resolve(strict=False)
        if not file_path.exists():
            raise ChatServiceError("File access error")
        return str(file_path), attachment.mime_type

    async def resolve_attachment_file_from_dashboard_query(
        self,
        attachment_id: str | None,
    ) -> tuple[str, str | None]:
        return await self.resolve_attachment_file(attachment_id)

    async def save_uploaded_file(self, file) -> dict:
        filename = sanitize_upload_filename(file.filename)
        content_type = file.content_type or "application/octet-stream"

        if content_type.startswith("image"):
            attach_type = "image"
        elif content_type.startswith("audio"):
            attach_type = "record"
        elif content_type.startswith("video"):
            attach_type = "video"
        else:
            attach_type = "file"

        attachments_dir = Path(self.attachments_dir).resolve(strict=False)
        file_path = (attachments_dir / filename).resolve(strict=False)
        if not file_path.is_relative_to(attachments_dir):
            raise ChatServiceError("Invalid filename")

        await file.save(str(file_path))
        if attach_type == "image":
            detected_mime_type = await detect_image_mime_type_async(
                file_path,
                default_mime_type=None,
            )
            if detected_mime_type:
                content_type = detected_mime_type
                detected_suffix = MEDIA_MIME_EXTENSIONS.get(detected_mime_type)
                if detected_suffix and file_path.suffix.lower() != detected_suffix:
                    target_path = file_path.with_suffix(detected_suffix)
                    if target_path.exists():
                        target_path = (
                            attachments_dir
                            / f"{generate_timestamp_id()}{detected_suffix}"
                        )
                    await asyncio.to_thread(file_path.rename, target_path)
                    file_path = target_path
        attachment = await self.db.insert_attachment(
            path=str(file_path),
            type=attach_type,
            mime_type=content_type,
        )

        if not attachment:
            raise ChatServiceError("Failed to create attachment")

        return {
            "attachment_id": attachment.attachment_id,
            "filename": os.path.basename(attachment.path),
            "type": attach_type,
        }

    async def save_uploaded_file_from_dashboard_files(self, files) -> dict:
        if "file" not in files:
            raise ChatServiceError("Missing key: file")
        return await self.save_uploaded_file(files["file"])

    async def delete_threads_by_ids(self, thread_ids: list[str], creator: str) -> None:
        for thread_id in thread_ids:
            unified_msg_origin = build_thread_unified_msg_origin(creator, thread_id)
            active_event_registry.request_agent_stop_all(unified_msg_origin)
            tasks = []
            for run_id in list(self.chat_runs_by_session.get(thread_id, set())):
                run = self.chat_runs.get(run_id)
                if run and run.task and not run.task.done():
                    run.task.cancel()
                    tasks.append(run.task)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self.conv_mgr.delete_conversations_by_user_id(unified_msg_origin)
            await self.platform_history_mgr.delete(
                platform_id="webchat_thread",
                user_id=thread_id,
                offset_sec=99999999,
            )
            webchat_queue_mgr.remove_queues(thread_id)
            self.running_convs.pop(thread_id, None)

    async def load_current_conversation_history(self, session) -> tuple[str, list]:
        unified_msg_origin = build_webchat_unified_msg_origin(session)
        conversation_id = await self.conv_mgr.get_curr_conversation_id(
            unified_msg_origin
        )
        if not conversation_id:
            return "", []

        conversation = await self.conv_mgr.get_conversation(
            unified_msg_origin=unified_msg_origin,
            conversation_id=conversation_id,
        )
        if not conversation:
            return "", []

        try:
            history = json.loads(conversation.history or "[]")
        except json.JSONDecodeError:
            return "", []
        return conversation_id, history if isinstance(history, list) else []

    async def get_sorted_platform_history(self, session) -> list:
        history_list = await self.platform_history_mgr.get(
            platform_id=session.platform_id,
            user_id=session.session_id,
            page=1,
            page_size=100000,
        )
        history_list.sort(key=lambda item: (item.created_at, item.id))
        return history_list

    async def delete_platform_history_after(
        self, session, message_id: int
    ) -> list[int]:
        history_list = await self.get_sorted_platform_history(session)
        should_delete = False
        deleted_ids: list[int] = []
        for item in history_list:
            if should_delete:
                if item.id is not None:
                    deleted_ids.append(item.id)
                    await self.platform_history_mgr.delete_by_id(item.id)
                continue
            if item.id == message_id:
                should_delete = True
        return deleted_ids

    async def save_bot_message(
        self,
        webchat_conv_id: str,
        message_parts: list[dict],
        agent_stats: dict,
        refs: dict,
        llm_checkpoint_id: str | None = None,
        platform_history_id: str = "webchat",
    ):
        return await self.platform_history_mgr.insert(
            platform_id=platform_history_id,
            user_id=webchat_conv_id,
            content=build_bot_history_content(
                message_parts,
                agent_stats=agent_stats,
                refs=refs,
            ),
            sender_id="bot",
            sender_name="bot",
            llm_checkpoint_id=llm_checkpoint_id,
        )

    def get_active_chat_runs(self, username: str, session_id: str) -> list[dict]:
        """Return resumable runs owned by a user in one chat session.

        Args:
            username: Authenticated run owner.
            session_id: WebChat session or thread identifier.

        Returns:
            Active run snapshots in creation order.
        """
        snapshots = []
        for run in self.chat_runs.values():
            if run.username != username or run.session_id != session_id:
                continue
            snapshots.append(
                {
                    "run_id": run.run_id,
                    "session_id": run.session_id,
                    "llm_checkpoint_id": run.llm_checkpoint_id,
                    "status": run.status,
                    "revision": run.revision,
                    "content": build_bot_history_content(
                        deepcopy(run.message_parts),
                        agent_stats=deepcopy(run.agent_stats),
                        refs=deepcopy(run.refs),
                    ),
                }
            )
        # Orphan runs (goal-loop / collab synthetic turns) register in the
        # webchat queue manager; expose them so the frontend recovery
        # snapshot works for them too.
        from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
            orphan_run_registry,
        )

        for orphan in orphan_run_registry.active_for_conversation(session_id):
            snapshots.append(
                {
                    "run_id": orphan.run_id,
                    "session_id": session_id,
                    "llm_checkpoint_id": None,
                    "status": "running",
                    "revision": len(orphan.message_parts),
                    "content": build_bot_history_content(
                        deepcopy(orphan.message_parts),
                    ),
                }
            )
        return snapshots

    @staticmethod
    def _publish_chat_run(run: ChatRunState, payload: dict) -> None:
        """Publish one output event without coupling the run to subscribers.

        Args:
            run: Chat run producing the event.
            payload: Existing WebChat event payload.
        """
        run.revision += 1
        item = (run.revision, payload)
        for subscriber in list(run.subscribers):
            try:
                subscriber.put_nowait(item)
            except asyncio.QueueFull:
                # Slow consumer (the SSE socket drains slower than the LLM
                # produces per-character chunks, and the frontend re-renders
                # markdown per chunk). Drop the OLDEST buffered events to fit
                # the new one and KEEP the subscriber attached — evicting it
                # (the old behavior) killed the SSE connection permanently,
                # freezing the bubble on 思考中 with no way to recover short
                # of a manual refresh.
                dropped = 0
                while True:
                    try:
                        subscriber.put_nowait(item)
                        break
                    except asyncio.QueueFull:
                        try:
                            subscriber.get_nowait()
                            dropped += 1
                        except asyncio.QueueEmpty:
                            subscriber.put_nowait(item)
                            break
                if dropped:
                    logger.debug(
                        "chat run %s: dropped %d oldest events for a slow subscriber",
                        run.run_id,
                        dropped,
                    )

    def _subscribe_chat_run(
        self,
        run: ChatRunState,
        *,
        include_snapshot: bool,
        saved_user_record=None,
    ) -> AsyncIterator[str]:
        """Create an SSE subscriber for a running chat generation.

        Args:
            run: Chat run to observe.
            include_snapshot: Whether to begin with accumulated run state.
            saved_user_record: Newly persisted user record for the legacy stream.

        Returns:
            SSE iterator detached from the generation task lifecycle.
        """
        subscriber: asyncio.Queue = asyncio.Queue(
            maxsize=CHAT_RUN_SUBSCRIBER_QUEUE_SIZE
        )
        run.subscribers.add(subscriber)
        snapshot = None
        if include_snapshot:
            snapshot = {
                "run_id": run.run_id,
                "session_id": run.session_id,
                "llm_checkpoint_id": run.llm_checkpoint_id,
                "status": run.status,
                "revision": run.revision,
                "content": build_bot_history_content(
                    deepcopy(run.message_parts),
                    agent_stats=deepcopy(run.agent_stats),
                    refs=deepcopy(run.refs),
                ),
            }
        snapshot_revision = run.revision

        async def stream():
            try:
                if snapshot is not None:
                    payload = {"type": "run_snapshot", "data": snapshot}
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                else:
                    session_info = {
                        "type": "session_id",
                        "data": None,
                        "session_id": run.session_id,
                    }
                    yield f"data: {json.dumps(session_info, ensure_ascii=False)}\n\n"
                    if saved_user_record:
                        user_saved_info = {
                            "type": "user_message_saved",
                            "data": {
                                "id": saved_user_record.id,
                                "created_at": to_utc_isoformat(
                                    saved_user_record.created_at
                                ),
                                "llm_checkpoint_id": run.llm_checkpoint_id,
                            },
                        }
                        yield f"data: {json.dumps(user_saved_info, ensure_ascii=False)}\n\n"

                while True:
                    try:
                        item = await asyncio.wait_for(subscriber.get(), timeout=1)
                    except asyncio.TimeoutError:
                        yield SSE_HEARTBEAT
                        continue
                    if item is None:
                        break
                    revision, payload = item
                    if include_snapshot and revision <= snapshot_revision:
                        continue
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            finally:
                run.subscribers.discard(subscriber)

        return stream()

    async def build_chat_run_stream(
        self,
        username: str,
        run_id: str,
    ) -> AsyncIterator[str]:
        """Attach a new SSE subscriber to an active chat run.

        Args:
            username: Authenticated run owner.
            run_id: Active run identifier.

        Returns:
            SSE iterator beginning with a full accumulated snapshot.

        Raises:
            ChatServiceError: If the run is absent or owned by another user.
        """
        run = self.chat_runs.get(run_id)
        if run is None:
            # Orphan runs (goal-loop / collab synthetic turns) live in the
            # webchat queue manager's registry; attach to their event fan-out.
            from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
                orphan_run_registry,
            )

            orphan = orphan_run_registry.get(run_id)
            if orphan is None:
                raise ChatServiceError(f"Chat run {run_id} not found")
            return self._subscribe_orphan_run(orphan_run_registry, orphan)
        if run.username != username:
            # 2026-08-30: identity representations can drift between auth
            # dependencies — run.username is recorded from
            # require_dashboard_user (dashboard-state username first) while
            # this endpoint authenticates via require_chat_scope (JWT
            # username). Fall back to the canonical session-ownership check
            # (the same convention get_session uses) before rejecting, so a
            # same-user attach is not broken by the representation drift.
            session = await self.db.get_platform_session_by_id(run.session_id)
            if session is None or session.creator != username:
                logger.warning(
                    "chat run %s owner mismatch: run.username=%r requester=%r",
                    run_id,
                    run.username,
                    username,
                )
                raise ChatServiceError("Permission denied")
        return self._subscribe_chat_run(run, include_snapshot=True)

    def _subscribe_orphan_run(self, registry, orphan) -> AsyncIterator[str]:
        """SSE over an orphan run's mirrored event fan-out.

        The recovery snapshot (accumulated message parts) is delivered by
        get_active_chat_runs; this stream serves the subsequent chunks and
        the terminal end event.
        """

        async def stream():
            import json as _json

            queue = registry.subscribe(orphan.run_id)
            if queue is None:
                return
            try:
                while True:
                    payload = await queue.get()
                    yield f"data: {_json.dumps(payload, ensure_ascii=False)}\n\n"
                    if payload.get("type") in ("end", "complete"):
                        return
            finally:
                registry.unsubscribe(orphan.run_id, queue)

        return stream()

    async def _consume_chat_run(self, run: ChatRunState) -> None:
        """Drain runner output, persist it, and fan it out to subscribers.

        Args:
            run: Chat run owning the producer queue and durable state.
        """
        pending_accumulator = BotMessageAccumulator()
        display_accumulator = BotMessageAccumulator()
        pending_agent_stats = {}
        pending_refs = {}

        async def flush_pending_bot_message():
            nonlocal pending_accumulator, pending_agent_stats, pending_refs
            if not (
                pending_accumulator.has_content() or pending_refs or pending_agent_stats
            ):
                return None

            message_parts_to_save = pending_accumulator.build_message_parts(
                include_pending_tool_calls=True
            )
            plain_text = collect_plain_text_from_message_parts(message_parts_to_save)
            try:
                extracted_refs = extract_web_search_refs(
                    plain_text,
                    message_parts_to_save,
                )
            except Exception as exc:
                logger.exception(
                    f"Failed to extract web search refs: {exc}",
                    exc_info=True,
                )
                extracted_refs = pending_refs

            run.refs = extracted_refs
            saved_record = await self.save_bot_message(
                run.session_id,
                message_parts_to_save,
                pending_agent_stats,
                extracted_refs,
                run.llm_checkpoint_id,
                run.platform_history_id,
            )
            pending_accumulator = BotMessageAccumulator()
            pending_agent_stats = {}
            pending_refs = {}
            return saved_record

        self.running_convs[run.session_id] = True
        try:
            while True:
                result = await run.back_queue.get()
                if not result:
                    continue
                if result.get("message_id") and str(result["message_id"]) != run.run_id:
                    logger.warning("webchat stream message_id mismatch")
                    continue

                result_text = result.get("data", "")
                msg_type = result.get("type")
                streaming = result.get("streaming", False)
                chain_type = result.get("chain_type")

                if chain_type == "agent_stats":
                    try:
                        run.agent_stats = json.loads(result_text)
                    except (TypeError, json.JSONDecodeError):
                        run.agent_stats = {}
                    pending_agent_stats = run.agent_stats
                    self._publish_chat_run(
                        run,
                        {"type": "agent_stats", "data": run.agent_stats},
                    )
                    continue

                attachment_saved_payload = None
                if msg_type == "subagent_event":
                    # Structured subagent progress: not plain text, so it
                    # bypasses add_plain and folds into a dedicated part.
                    # The payload is still published verbatim below.
                    event_data = result.get("data")
                    if isinstance(event_data, dict):
                        for accumulator in (pending_accumulator, display_accumulator):
                            accumulator.add_subagent_event(event_data)
                elif msg_type == "plain":
                    for accumulator in (pending_accumulator, display_accumulator):
                        accumulator.add_plain(
                            result_text,
                            chain_type=chain_type,
                            streaming=streaming,
                        )
                elif msg_type in {"image", "record", "file", "video"}:
                    prefix = {
                        "image": "[IMAGE]",
                        "record": "[RECORD]",
                        "file": "[FILE]",
                        "video": "[VIDEO]",
                    }[msg_type]
                    filename = str(result_text).replace(prefix, "", 1)
                    display_name = None
                    if msg_type in {"file", "video"} and "|" in filename:
                        filename, display_name = filename.split("|", 1)
                    part = await self.create_attachment_from_file(
                        filename,
                        msg_type,
                        display_name=display_name,
                    )
                    for accumulator in (pending_accumulator, display_accumulator):
                        accumulator.add_attachment(part)
                    if part and part.get("attachment_id") and part.get("type"):
                        attachment_saved_payload = {
                            "type": "attachment_saved",
                            "data": {
                                "id": part["attachment_id"],
                                "type": part["type"],
                            },
                        }

                # Spec §4.5: ask_user_choice pushes its choice payloads
                # straight onto the run's back_queue, but Agent Teams
                # runners collect through the system stream — mirror the
                # choice events to that stream verbatim. Additive only:
                # the run stream and the chat page's own choice flow are
                # untouched.
                if (
                    chain_type == "interactive_choice"
                    or msg_type == "interactive_choice_resolved"
                ):
                    await webchat_queue_mgr.put_system_event(run.session_id, result)

                snapshot_accumulator = deepcopy(display_accumulator)
                run.message_parts = snapshot_accumulator.build_message_parts(
                    include_pending_tool_calls=True
                )
                self._publish_chat_run(run, result)
                if attachment_saved_payload:
                    self._publish_chat_run(run, attachment_saved_payload)

                should_save = False
                if msg_type == "end":
                    should_save = bool(
                        pending_accumulator.has_content()
                        or pending_refs
                        or pending_agent_stats
                    )
                elif (streaming and msg_type == "complete") or not streaming:
                    if chain_type not in ("tool_call", "tool_call_result"):
                        should_save = True

                if should_save:
                    saved_record = await flush_pending_bot_message()
                    if saved_record:
                        self._publish_chat_run(
                            run,
                            {
                                "type": "message_saved",
                                "data": {
                                    "id": saved_record.id,
                                    "created_at": to_utc_isoformat(
                                        saved_record.created_at
                                    ),
                                    "llm_checkpoint_id": run.llm_checkpoint_id,
                                },
                            },
                        )
                if msg_type == "end":
                    run.status = "completed"
                    break
        except asyncio.CancelledError:
            run.status = "stopped"
        except Exception as exc:
            run.status = "failed"
            logger.exception(f"WebChat run unexpected error: {exc}", exc_info=True)
            self._publish_chat_run(
                run,
                {"type": "error", "data": "WebChat run failed"},
            )
        finally:
            try:
                saved_record = await asyncio.shield(flush_pending_bot_message())
                if saved_record:
                    self._publish_chat_run(
                        run,
                        {
                            "type": "message_saved",
                            "data": {
                                "id": saved_record.id,
                                "created_at": to_utc_isoformat(saved_record.created_at),
                                "llm_checkpoint_id": run.llm_checkpoint_id,
                            },
                        },
                    )
            except Exception as exc:
                logger.exception(
                    f"Failed to persist pending webchat message: {exc}",
                    exc_info=True,
                )

            webchat_queue_mgr.remove_back_queue(run.run_id)
            if self.chat_runs.get(run.run_id) is run:
                self.chat_runs.pop(run.run_id, None)
            run_ids = self.chat_runs_by_session.get(run.session_id)
            if run_ids is not None:
                run_ids.discard(run.run_id)
                if not run_ids:
                    self.chat_runs_by_session.pop(run.session_id, None)
                    self.running_convs.pop(run.session_id, None)
            for subscriber in list(run.subscribers):
                while not subscriber.empty():
                    subscriber.get_nowait()
                subscriber.put_nowait(None)
            run.subscribers.clear()

    def system_stream_enabled(self) -> bool:
        """Return whether the conversation-level system event stream is on."""
        return bool(
            self.core_lifecycle.astrbot_config.get("dashboard", {}).get(
                "system_stream_enabled", True
            )
        )

    async def build_system_stream(
        self,
        username: str,
        session_id: str,
    ) -> AsyncIterator[str]:
        """Stream system-driven (non-user-initiated) webchat events.

        Consumes the conversation-level system event queue mirrored by the
        webchat platform adapter. Payloads whose ``message_id`` belongs to an
        active primary chat run are skipped (the primary SSE already serves
        and persists them). Orphan turns (goal-loop synthetic turns and
        future system flows) are streamed live and persisted with the same
        accumulate-then-flush policy as ``_consume_chat_run``.

        Args:
            username: Dashboard username owning the connection.
            session_id: Raw webchat conversation id.

        Returns:
            SSE iterator yielding ``data: {json}\\n\\n`` chunks.
        """
        queue = webchat_queue_mgr.subscribe_system(session_id)
        from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
            orphan_run_registry,
        )

        accumulators: dict[str, BotMessageAccumulator] = {}

        async def flush(message_id: str) -> None:
            acc = accumulators.pop(message_id, None)
            if acc is None or not acc.has_content():
                return
            # Orphan turns are persisted by the webchat event layer on
            # stream completion (_persist_bot_reply_if_orphan); persisting
            # here as well would duplicate the bot record.
            from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
                orphan_run_registry,
            )

            if orphan_run_registry.was_finished_orphan(str(message_id)):
                return
            parts = acc.build_message_parts(include_pending_tool_calls=True)
            # Author: elecvoid243
            # Date: 2026-07-25
            # Plan: orphan goal-loop turn agent_stats persistence fix.
            # Forward the per-accumulator `pending_agent_stats` (captured
            # by `BotMessageAccumulator.add_plain(chain_type="agent_stats")`)
            # to `save_bot_message` instead of the previous hard-coded
            # `{}`. The frontend `normalizeHistoryRecord` reads this
            # exact field as `content.agent_stats` to drive the hover
            # token-usage card, so a missing value here means the card
            # disappears after a hard refresh.
            #
            # The primary `_consume_chat_run` path is unaffected: it
            # never instantiates a `BotMessageAccumulator` inside
            # `build_system_stream` (different function, different
            # queue, different persistence call). Normal turn goes
            # through `save_bot_message(..., pending_agent_stats, ...)`
            # with the outer variable, completely separate.
            try:
                await self.save_bot_message(
                    session_id,
                    parts,
                    acc.pending_agent_stats,
                    {},
                    None,
                    "webchat",
                )
            except Exception as exc:
                logger.error(
                    f"Failed to persist system event turn {message_id}: {exc}",
                    exc_info=True,
                )

        async def stream():
            try:
                # 2026-08-27 switch-back catch-up: seed (re)subscribers with
                # the accumulated state of in-flight orphan turns (goal-loop
                # / collab synthetic turns). The system stream is a live-tail
                # channel — without this snapshot, switching back to a
                # session mid-turn only showed output from the switch moment
                # onward; everything streamed while another session was open
                # was dropped.
                for orphan in orphan_run_registry.active_for_conversation(session_id):
                    if not orphan.message_parts:
                        continue
                    snapshot = {
                        "type": "run_snapshot",
                        "message_id": orphan.run_id,
                        "streaming": True,
                        "data": {"message": deepcopy(orphan.message_parts)},
                    }
                    yield f"data: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
                while True:
                    payload = await queue.get()
                    if not isinstance(payload, dict):
                        continue
                    message_id = payload.get("message_id")
                    if not message_id:
                        continue
                    if message_id in self.chat_runs:
                        # Primary run stream owns this message_id — its
                        # payloads are served by the run stream. The only
                        # exception is the synthetic-run registration
                        # announcement (flagged by the sender): a page that
                        # did not initiate the run needs it in order to
                        # attach to the run stream. The adapter's own
                        # run_started mirror for user-initiated runs stays
                        # filtered — that page renders via its
                        # request-scoped stream.
                        if not (
                            payload.get("type") == "run_started"
                            and payload.get("synthetic")
                        ):
                            continue
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

                    msg_type = payload.get("type")
                    streaming = bool(payload.get("streaming"))
                    chain_type = payload.get("chain_type")
                    acc = accumulators.setdefault(message_id, BotMessageAccumulator())
                    if msg_type == "plain":
                        acc.add_plain(
                            payload.get("data", ""),
                            chain_type=chain_type,
                            streaming=streaming,
                        )
                    elif msg_type in ("complete", "end"):
                        if (
                            msg_type == "complete"
                            and not streaming
                            and payload.get("data")
                        ):
                            acc.add_plain(
                                payload["data"],
                                chain_type=chain_type,
                                streaming=False,
                            )
                        await flush(message_id)
            finally:
                for pending_id in list(accumulators):
                    await flush(pending_id)
                webchat_queue_mgr.unsubscribe_system(session_id, queue)

        return stream()

    async def register_synthetic_chat_run(
        self,
        session_id: str,
        message_id: str,
        username: str,
        llm_checkpoint_id: str | None = None,
    ) -> None:
        """Register a first-class chat run for a synthetic (non-POST /chat) turn.

        Agent-collab injections do not originate from a dashboard request, so
        nothing would own their back_queue: the webchat adapter would treat
        the turn as an orphan and it would render through the lossy
        system-mirror channel instead of the primary pipeline. Registering
        the run here puts it on the exact primary path — the standard
        consumer accumulates and persists the reply, `active_runs` exposes it
        for switch-back recovery, and the run stream serves snapshot + live
        chunks like any user-initiated turn.

        Args:
            session_id: Raw webchat conversation id.
            message_id: The injected turn's message id (becomes the run id).
              MUST be registered before the input item is queued so the
              pipeline's first chunk already has a back_queue to land in.
            username: Run owner (the discussion's dashboard user).
            llm_checkpoint_id: Checkpoint carried by the injected payload;
              a fresh one is generated when omitted.
        """
        back_queue = webchat_queue_mgr.get_or_create_back_queue(message_id, session_id)
        run = ChatRunState(
            run_id=message_id,
            username=username,
            session_id=session_id,
            llm_checkpoint_id=llm_checkpoint_id or str(uuid.uuid4()),
            platform_history_id="webchat",
            back_queue=back_queue,
        )
        self.chat_runs[message_id] = run
        self.chat_runs_by_session.setdefault(session_id, set()).add(message_id)
        run.task = asyncio.create_task(
            self._consume_chat_run(run),
            name=f"webchat_run_{message_id}",
        )
        # Announce to system-stream subscribers so a page with the session
        # open attaches to the run stream. The payload is flagged
        # `synthetic`: build_system_stream passes ONLY flagged announcements
        # through its run-ownership filter — the adapter's send_typing also
        # mirrors a run_started for this message id, and that copy must stay
        # filtered (the initiating page of a user-owned run already renders
        # via its request-scoped stream; attaching again would duplicate the
        # reply).
        await webchat_queue_mgr.put_system_event(
            session_id,
            {
                "type": "run_started",
                "data": {"run_id": message_id},
                "streaming": False,
                "message_id": message_id,
                "synthetic": True,
            },
        )

    async def build_chat_stream(
        self,
        username: str,
        post_data: dict,
    ) -> AsyncIterator[str]:
        if "message" not in post_data and "files" not in post_data:
            raise ChatServiceError("Missing key: message or files")
        if "session_id" not in post_data and "conversation_id" not in post_data:
            raise ChatServiceError("Missing key: session_id or conversation_id")

        message = post_data.get("message", post_data.get("files", []))
        session_id = post_data.get("session_id", post_data.get("conversation_id"))
        selected_provider = post_data.get("selected_provider")
        selected_model = post_data.get("selected_model")
        thinking_effort = post_data.get("thinking_effort")
        flags = resolve_webchat_request_flags(post_data)
        platform_history_id = post_data.get("_platform_history_id") or "webchat"
        thread_selected_text = post_data.get("_thread_selected_text")

        if not session_id:
            raise ChatServiceError("session_id is empty")

        webchat_conv_id = session_id
        message_parts = await self.build_user_message_parts(message)
        if not webchat_message_parts_have_content(message_parts):
            raise ChatServiceError(
                "Message content is empty (reply only is not allowed)"
            )

        if platform_history_id == "webchat":
            try:
                platform_session = await self.db.get_platform_session_by_id(
                    webchat_conv_id
                )
                if platform_session is None:
                    await self.db.create_platform_session(
                        creator=username,
                        platform_id="webchat",
                        session_id=webchat_conv_id,
                        is_group=0,
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to ensure WebChat platform session %s: %s",
                    webchat_conv_id,
                    exc,
                )

        message_id = str(uuid.uuid4())
        llm_checkpoint_id = post_data.get("_llm_checkpoint_id") or str(uuid.uuid4())
        skip_user_history = bool(post_data.get("_skip_user_history"))
        saved_user_record = None

        message_parts_for_storage = strip_message_parts_path_fields(message_parts)
        if not skip_user_history:
            saved_user_record = await self.platform_history_mgr.insert(
                platform_id=platform_history_id,
                user_id=webchat_conv_id,
                content={"type": "user", "message": message_parts_for_storage},
                sender_id=username,
                sender_name=username,
                llm_checkpoint_id=llm_checkpoint_id,
            )

        back_queue = webchat_queue_mgr.get_or_create_back_queue(
            message_id,
            webchat_conv_id,
        )
        run = ChatRunState(
            run_id=message_id,
            username=username,
            session_id=webchat_conv_id,
            llm_checkpoint_id=llm_checkpoint_id,
            platform_history_id=platform_history_id,
            back_queue=back_queue,
        )
        self.chat_runs[message_id] = run
        self.chat_runs_by_session.setdefault(webchat_conv_id, set()).add(message_id)
        stream = self._subscribe_chat_run(
            run,
            include_snapshot=False,
            saved_user_record=saved_user_record,
        )
        run.task = asyncio.create_task(
            self._consume_chat_run(run),
            name=f"webchat_run_{message_id}",
        )

        try:
            chat_queue = webchat_queue_mgr.get_or_create_queue(webchat_conv_id)
            await chat_queue.put(
                (
                    username,
                    webchat_conv_id,
                    {
                        "message": message_parts,
                        "selected_provider": selected_provider,
                        "selected_model": selected_model,
                        "thinking_effort": thinking_effort,
                        "flags": flags,
                        "message_id": message_id,
                        "llm_checkpoint_id": llm_checkpoint_id,
                        "thread_selected_text": thread_selected_text,
                        "_api_key_allow_admin_role": post_data.get(
                            "_api_key_allow_admin_role"
                        ),
                    },
                ),
            )
        except BaseException:
            run.task.cancel()
            await asyncio.gather(run.task, return_exceptions=True)
            raise

        return stream

    async def stop_session(self, username: str, session_id: str) -> dict:
        session = await self.db.get_platform_session_by_id(session_id)
        if not session:
            raise ChatServiceError(f"Session {session_id} not found")
        if session.creator != username:
            raise ChatServiceError("Permission denied")

        unified_msg_origin = build_webchat_unified_msg_origin(session)
        stopped_count = active_event_registry.request_agent_stop_all(unified_msg_origin)
        return {"stopped_count": stopped_count}

    async def stop_session_from_dashboard_payload(
        self,
        username: str,
        payload: object,
    ) -> dict:
        data = self._dashboard_payload(payload)
        session_id = data.get("session_id")
        if not session_id:
            raise ChatServiceError("Missing key: session_id")
        return await self.stop_session(username, session_id)

    async def delete_session_internal(self, session, username: str) -> None:
        session_id = session.session_id
        message_type = "GroupMessage" if session.is_group else "FriendMessage"
        unified_msg_origin = (
            f"{session.platform_id}:{message_type}:"
            f"{session.platform_id}!{username}!{session_id}"
        )
        active_event_registry.request_agent_stop_all(unified_msg_origin)
        tasks = []
        for run_id in list(self.chat_runs_by_session.get(session_id, set())):
            run = self.chat_runs.get(run_id)
            if run and run.task and not run.task.done():
                run.task.cancel()
                tasks.append(run.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.conv_mgr.delete_conversations_by_user_id(unified_msg_origin)

        history_list = await self.platform_history_mgr.get(
            platform_id=session.platform_id,
            user_id=session_id,
            page=1,
            page_size=100000,
        )
        attachment_ids = extract_attachment_ids(history_list)
        if attachment_ids:
            await self.delete_attachments(attachment_ids)

        await self.platform_history_mgr.delete(
            platform_id=session.platform_id,
            user_id=session_id,
            offset_sec=99999999,
        )
        thread_ids = await self.db.delete_webchat_threads_by_parent_session(session_id)
        await self.delete_threads_by_ids(thread_ids, username)

        try:
            await self.umop_config_router.delete_route(unified_msg_origin)
        except ValueError as exc:
            logger.warning(
                "Failed to delete UMO route %s during session cleanup: %s",
                unified_msg_origin,
                exc,
            )

        if session.platform_id == "webchat":
            webchat_queue_mgr.remove_queues(session_id)

        await self.db.delete_platform_session(session_id)

    async def delete_webchat_session(self, username: str, session_id: str) -> None:
        session = await self.db.get_platform_session_by_id(session_id)
        if not session:
            raise ChatServiceError(f"Session {session_id} not found")
        if session.creator != username:
            raise ChatServiceError("Permission denied")
        await self.delete_session_internal(session, username)

    async def delete_webchat_session_from_dashboard_query(
        self,
        username: str,
        session_id: str | None,
    ) -> None:
        if not session_id:
            raise ChatServiceError("Missing key: session_id")
        await self.delete_webchat_session(username, session_id)

    async def batch_delete_sessions(
        self,
        username: str,
        session_ids: list,
        delete_session=None,
    ) -> dict:
        delete_session = delete_session or self.delete_session_internal
        sessions = await self.db.get_platform_sessions_by_ids(session_ids)
        sessions_by_id = {session.session_id: session for session in sessions}
        deleted_count = 0
        failed_items = []

        for session_id in session_ids:
            session = sessions_by_id.get(session_id)
            if not session:
                failed_items.append({"session_id": session_id, "reason": "not found"})
                continue
            if session.creator != username:
                failed_items.append(
                    {"session_id": session_id, "reason": "permission denied"}
                )
                continue

            try:
                await delete_session(session, username)
                deleted_count += 1
                sessions_by_id.pop(session_id, None)
            except Exception:
                logger.warning("Failed to delete session %s", session_id)
                failed_items.append(
                    {"session_id": session_id, "reason": "internal_error"}
                )

        return {
            "deleted_count": deleted_count,
            "failed_count": len(failed_items),
            "failed_items": failed_items,
        }

    async def batch_delete_sessions_from_dashboard_payload(
        self,
        username: str,
        payload: object,
        delete_session=None,
    ) -> dict:
        data = self._dashboard_payload(payload)
        session_ids = data.get("session_ids")
        if not session_ids or not isinstance(session_ids, list):
            raise ChatServiceError("Missing or invalid key: session_ids")
        return await self.batch_delete_sessions(username, session_ids, delete_session)

    async def batch_archive_sessions_from_dashboard_payload(
        self,
        username: str,
        payload: object,
    ) -> dict:
        """Archive multiple sessions owned by the user in one request.

        Mirrors ``batch_delete_sessions_from_dashboard_payload``: failures
        are collected per session instead of aborting the whole batch.

        Args:
            username: Dashboard username; must own every session.
            payload: Dict with ``session_ids`` (list of session ids).

        Returns:
            Dict with ``archived_count``, ``failed_count`` and
            ``failed_items`` (``{"session_id", "reason"}``).
        """
        data = self._dashboard_payload(payload)
        session_ids = data.get("session_ids")
        if not session_ids or not isinstance(session_ids, list):
            raise ChatServiceError("Missing or invalid key: session_ids")

        archived_count = 0
        failed_items: list[dict] = []
        for session_id in session_ids:
            try:
                await self.set_session_archived(username, session_id, True)
                archived_count += 1
            except ChatServiceError as exc:
                failed_items.append({"session_id": session_id, "reason": str(exc)})

        return {
            "archived_count": archived_count,
            "failed_count": len(failed_items),
            "failed_items": failed_items,
        }

    async def batch_unarchive_sessions_from_dashboard_payload(
        self,
        username: str,
        payload: object,
    ) -> dict:
        """Restore multiple archived sessions owned by the user in one request.

        Mirror of ``batch_archive_sessions_from_dashboard_payload`` for the
        archived-conversations dialog's batch restore action.

        Args:
            username: Dashboard username; must own every session.
            payload: Dict with ``session_ids`` (list of session ids).

        Returns:
            Dict with ``unarchived_count``, ``failed_count`` and
            ``failed_items`` (``{"session_id", "reason"}``).
        """
        data = self._dashboard_payload(payload)
        session_ids = data.get("session_ids")
        if not session_ids or not isinstance(session_ids, list):
            raise ChatServiceError("Missing or invalid key: session_ids")

        unarchived_count = 0
        failed_items: list[dict] = []
        for session_id in session_ids:
            try:
                await self.set_session_archived(username, session_id, False)
                unarchived_count += 1
            except ChatServiceError as exc:
                failed_items.append({"session_id": session_id, "reason": str(exc)})

        return {
            "unarchived_count": unarchived_count,
            "failed_count": len(failed_items),
            "failed_items": failed_items,
        }

    async def delete_attachments(self, attachment_ids: list[str]) -> None:
        try:
            attachments = await self.db.get_attachments(attachment_ids)
            for attachment in attachments:
                if not os.path.exists(attachment.path):
                    continue
                try:
                    os.remove(attachment.path)
                except OSError as e:
                    logger.warning(
                        f"Failed to delete attachment file {attachment.path}: {e}"
                    )
        except Exception as e:
            logger.warning(f"Failed to get attachments: {e}")

        try:
            await self.db.delete_attachments(attachment_ids)
        except Exception as e:
            logger.warning(f"Failed to delete attachments: {e}")

    async def new_session(self, username: str, platform_id: str) -> dict:
        session = await self.db.create_platform_session(
            creator=username,
            platform_id=platform_id,
            is_group=0,
        )
        return {
            "session_id": session.session_id,
            "platform_id": session.platform_id,
        }

    async def new_session_from_dashboard_query(
        self,
        username: str,
        platform_id: str | None,
    ) -> dict:
        return await self.new_session(username, platform_id or "webchat")

    async def get_sessions(self, username: str, platform_id: str | None) -> list[dict]:
        sessions, _ = await self.db.get_platform_sessions_by_creator_paginated(
            creator=username,
            platform_id=platform_id,
            page=1,
            page_size=100,
            exclude_project_sessions=True,
            archived=False,
        )

        sessions_data = []
        for item in sessions:
            session = item["session"]
            sessions_data.append(
                {
                    "session_id": session.session_id,
                    "platform_id": session.platform_id,
                    "creator": session.creator,
                    "display_name": session.display_name,
                    "is_group": session.is_group,
                    "created_at": to_utc_isoformat(session.created_at),
                    "updated_at": to_utc_isoformat(session.updated_at),
                }
            )

        # Derive branch relations from the cached `branch_info` divider
        # records (see `get_branch_relations`). Entries pointing to or from
        # sessions outside the current list (e.g. deleted) are filtered out.
        session_ids = {item["session_id"] for item in sessions_data}
        name_by_id = {
            item["session_id"]: item["display_name"] for item in sessions_data
        }
        relations = await self.get_branch_relations()
        children: dict[str, list[str]] = {}
        for child_id, relation in relations.items():
            if child_id not in session_ids:
                continue
            source_id = relation["source_session_id"]
            if source_id in session_ids:
                children.setdefault(source_id, []).append(child_id)

        for item in sessions_data:
            sid = item["session_id"]
            relation = relations.get(sid)
            item["branch_source"] = (
                {
                    "session_id": relation["source_session_id"],
                    "message_id": relation["source_message_id"],
                }
                if relation
                else None
            )
            item["branches"] = [
                {"session_id": child_id, "display_name": name_by_id.get(child_id)}
                for child_id in children.get(sid, [])
            ]
        return sessions_data

    async def get_archived_sessions(
        self,
        username: str,
        platform_id: str | None,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
    ) -> dict:
        """List archived sessions with title search and pagination.

        Mirrors ``get_sessions`` but returns only archived sessions; project
        membership is kept so the frontend can show a project label and
        restore into the right project. ``search`` filters by display name
        (case-insensitive substring), and results are paginated for the
        archived-conversations dialog.

        Args:
            username: Dashboard username owning the sessions.
            platform_id: Optional platform filter.
            page: 1-based page number.
            page_size: Items per page (clamped to 1..100).
            search: Optional display-name substring filter.

        Returns:
            Dict with ``items`` (session dicts with branch relations) and
            ``pagination`` metadata.
        """
        page = max(page, 1)
        page_size = max(1, min(page_size, 100))
        # Normalize to None so an empty search is not passed to the DB query.
        search = (search or "").strip() or None

        # Pagination and the display-name search are both applied at the
        # database layer; only the requested page is loaded instead of the
        # full archived set (see get_platform_sessions_by_creator_paginated).
        sessions, total = await self.db.get_platform_sessions_by_creator_paginated(
            creator=username,
            platform_id=platform_id,
            page=page,
            page_size=page_size,
            exclude_project_sessions=False,
            archived=True,
            search=search,
        )

        items = []
        for item in sessions:
            session = item["session"]
            items.append(
                {
                    "session_id": session.session_id,
                    "platform_id": session.platform_id,
                    "creator": session.creator,
                    "display_name": session.display_name,
                    "is_group": session.is_group,
                    "created_at": to_utc_isoformat(session.created_at),
                    "updated_at": to_utc_isoformat(session.updated_at),
                    "project_id": item["project_id"],
                    "project_title": item["project_title"],
                    "project_emoji": item["project_emoji"],
                }
            )

        session_ids = {item["session_id"] for item in items}
        relations = await self.get_branch_relations()
        for item in items:
            relation = relations.get(item["session_id"])
            item["branch_source"] = (
                {
                    "session_id": relation["source_session_id"],
                    "message_id": relation["source_message_id"],
                }
                if relation
                else None
            )
            item["branches"] = [
                {"session_id": child_id, "display_name": None}
                for child_id, rel in relations.items()
                if rel["source_session_id"] == item["session_id"]
                and child_id in session_ids
            ]

        total_pages = (total + page_size - 1) // page_size if total else 1
        return {
            "items": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
        }

    async def set_session_archived(
        self, username: str, session_id: str, archived: bool
    ) -> None:
        """Archive or unarchive a webchat session owned by the user.

        Args:
            username: Dashboard username; must own the session.
            session_id: Session to update.
            archived: True to archive, False to restore.
        """
        session = await self.db.get_platform_session_by_id(session_id)
        if not session:
            raise ChatServiceError(f"Session {session_id} not found")
        if session.creator != username:
            raise ChatServiceError("Permission denied")
        await self.db.update_platform_session(session_id, archived=1 if archived else 0)

    async def get_sessions_from_dashboard_query(
        self,
        username: str,
        platform_id: str | None,
    ) -> list[dict]:
        return await self.get_sessions(username, platform_id)

    async def get_session(self, username: str, session_id: str) -> dict:
        session = await self.db.get_platform_session_by_id(session_id)
        if not session:
            raise ChatServiceError(f"Session {session_id} not found")
        if session.creator != username:
            raise ChatServiceError("Permission denied")
        platform_id = session.platform_id

        project_info = await self.db.get_project_by_session(
            session_id=session_id, creator=username
        )
        # Only the most recent window is loaded; older history is paged via
        # get_history_before triggered by the ChatUI scroll-to-top or button.
        history_ls = await self.platform_history_mgr.get(
            platform_id=platform_id,
            user_id=session_id,
            page=1,
            page_size=HISTORY_WINDOW_SIZE,
        )
        threads = await self.db.get_webchat_threads_by_parent_session(
            parent_session_id=session_id,
            creator=username,
        )

        # Author: elecvoid243
        # Date: 2026-07-05
        # Read-path defence: strip stale ask_user_choice `tool_call` parts
        # from bot records before sending to the dashboard (see
        # `_sanitize_ask_user_choice_tool_call_parts` for the rationale).
        # Timestamps are normalized to UTC ISO via `to_utc_isoformat` so the
        # payload matches what `serialize_history_entry` would have produced.
        history_payload = _sanitize_history_bot_records(
            [history.model_dump() for history in history_ls]
        )
        history_payload = [
            {
                **entry,
                "created_at": to_utc_isoformat(entry.get("created_at")),
                "updated_at": to_utc_isoformat(entry.get("updated_at")),
            }
            for entry in history_payload
        ]

        total_messages = await self.db.count_platform_message_history(
            platform_id=platform_id,
            user_id=session_id,
        )

        response_data = {
            "history": history_payload,
            "threads": [serialize_thread(thread) for thread in threads],
            "total_messages": total_messages,
            "has_more": total_messages > len(history_ls),
            "is_running": self.running_convs.get(session_id, False),
            "active_runs": self.get_active_chat_runs(username, session_id),
            # 2026-08-13: lets the frontend render archived sessions in a
            # read-only mode.
            "archived": bool(getattr(session, "archived", 0)),
        }
        if project_info:
            response_data["project"] = {
                "project_id": project_info.project_id,
                "title": project_info.title,
                "emoji": project_info.emoji,
            }
        return response_data

    async def get_history_before(
        self,
        username: str,
        session_id: str,
        before_id: int | None,
        limit: int = 50,
    ) -> dict:
        """Return one page of history older than ``before_id`` for a session.

        Args:
            username: Authenticated dashboard user; must own the session.
            session_id: WebChat session identifier.
            before_id: Exclusive cursor; records with a smaller id are returned.
            limit: Page size, capped at ``MAX_HISTORY_PAGE_SIZE``.

        Returns:
            History records (ascending) plus the next cursor and thread rows.

        Raises:
            ChatServiceError: If the session is missing or owned by another
                user, or when ``before_id`` is not provided.
        """
        if before_id is None:
            raise ChatServiceError("Missing key: before_id")
        session = await self.db.get_platform_session_by_id(session_id)
        if not session:
            raise ChatServiceError(f"Session {session_id} not found")
        if session.creator != username:
            raise ChatServiceError("Permission denied")
        platform_id = session.platform_id
        page_size = max(1, min(limit, MAX_HISTORY_PAGE_SIZE))

        history_ls = await self.platform_history_mgr.get(
            platform_id=platform_id,
            user_id=session_id,
            page=1,
            page_size=page_size,
            before_id=before_id,
        )
        history_payload = _sanitize_history_bot_records(
            [history.model_dump() for history in history_ls]
        )
        history_payload = [
            {
                **entry,
                "created_at": to_utc_isoformat(entry.get("created_at")),
                "updated_at": to_utc_isoformat(entry.get("updated_at")),
            }
            for entry in history_payload
        ]

        threads = await self.db.get_webchat_threads_by_parent_session(
            parent_session_id=session_id,
            creator=username,
        )
        parent_ids = {history.id for history in history_ls}
        page_threads = [
            thread for thread in threads if thread.parent_message_id in parent_ids
        ]

        oldest_id = history_ls[0].id if history_ls else None
        older_count = (
            await self.db.count_platform_message_history(
                platform_id=platform_id,
                user_id=session_id,
                before_id=oldest_id,
            )
            if oldest_id is not None
            else 0
        )
        return {
            "history": history_payload,
            "threads": [serialize_thread(thread) for thread in page_threads],
            "has_more": older_count > 0,
            "next_before_id": oldest_id,
        }

    async def get_session_goal(self, username: str, session_id: str) -> dict:
        """Return the standing-goal state for a ChatUI session.

        Args:
            username: Authenticated dashboard user; must own the session.
            session_id: WebChat session identifier.

        Returns:
            ``{"goal": None}`` when the session has no goal record (never
            set, or removed via /goal clear); otherwise the serialized
            GoalState for the session's UMO.
        """
        session = await self.db.get_platform_session_by_id(session_id)
        if not session:
            raise ChatServiceError(f"Session {session_id} not found")
        if session.creator != username:
            raise ChatServiceError("Permission denied")

        # Function-local import matching app.py: the goal module binds the
        # kernel Context at star startup and is not needed at module load.
        from astrbot.core.goal.goal_service import goal_service

        umo = build_webchat_unified_msg_origin(session)
        state = await goal_service.goals.get(umo)
        if state is None:
            return {"goal": None}
        return {
            "goal": {
                "goal": state.goal,
                "status": state.status,
                "turns_used": state.turns_used,
                "max_turns": state.max_turns,
                "subgoals": state.subgoals,
                "last_verdict": state.last_verdict,
                "last_reason": state.last_reason,
                "paused_reason": state.paused_reason,
                "created_at": datetime.fromtimestamp(
                    state.created_at, tz=timezone.utc
                ).isoformat()
                if state.created_at
                else None,
            }
        }

    async def get_message_markers(
        self,
        username: str,
        session_id: str,
        limit: int | None = None,
    ) -> dict:
        """Scan the session history and index user messages for the ChatUI.

        Args:
            username: Authenticated dashboard user; must own the session.
            session_id: WebChat session identifier.
            limit: Max markers to index; capped at ``MAX_MESSAGE_MARKERS``.

        Returns:
            Ascending marker list plus the total record count and a
            truncation flag. Indices are 1:1 positions in the full ascending
            history, matching the absolute index space the ChatUI renders.
            Markers older than a ``branch_info`` divider carry
            ``inherited=True`` so the ChatUI can color them red and hide
            them while the inherited history is collapsed.
        """
        session = await self.db.get_platform_session_by_id(session_id)
        if not session:
            raise ChatServiceError(f"Session {session_id} not found")
        if session.creator != username:
            raise ChatServiceError("Permission denied")
        platform_id = session.platform_id

        cap = max(1, min(limit or MAX_MESSAGE_MARKERS, MAX_MESSAGE_MARKERS))
        total = await self.db.count_platform_message_history(
            platform_id=platform_id,
            user_id=session_id,
        )
        markers: list[dict] = []
        truncated = False
        cursor = None
        position_from_newest = 0
        inherited = False
        while True:
            page = await self.platform_history_mgr.get(
                platform_id=platform_id,
                user_id=session_id,
                page=1,
                page_size=MESSAGE_MARKER_PAGE_SIZE,
                before_id=cursor,
            )
            if not page:
                break
            # The page is ascending (oldest first); walk it from the newest
            # end so a record's position-from-newest maps to its absolute
            # index (total - 1 - position) in the rendered history.
            finished = False
            for record in reversed(page):
                content = record.content
                if isinstance(content, dict):
                    # The branch divider sits between the inherited records
                    # and the session's own ones, so in this newest-first
                    # walk everything below it belongs to the source
                    # conversation.
                    if content.get("type") == "branch_info":
                        inherited = True
                    elif content.get("type") == "user":
                        if len(markers) >= cap:
                            truncated = True
                            finished = True
                            break
                        markers.append(
                            {
                                "id": record.id,
                                "index": total - 1 - position_from_newest,
                                "snippet": extract_platform_message_text(content)[:40],
                                "inherited": inherited,
                            }
                        )
                position_from_newest += 1
            if finished:
                break
            if len(page) < MESSAGE_MARKER_PAGE_SIZE:
                break
            cursor = page[0].id
        markers.reverse()
        return {
            "markers": markers,
            "total_messages": total,
            "truncated": truncated,
        }

    async def get_session_from_dashboard_query(
        self,
        username: str,
        session_id: str | None,
    ) -> dict:
        if not session_id:
            raise ChatServiceError("Missing key: session_id")
        return await self.get_session(username, session_id)

    async def create_thread(self, username: str, data: dict) -> dict:
        session_id = data.get("session_id")
        parent_message_id = data.get("parent_message_id")
        selected_text = str(data.get("selected_text") or "").strip()
        if not session_id:
            raise ChatServiceError("Missing key: session_id")
        if parent_message_id is None:
            raise ChatServiceError("Missing key: parent_message_id")
        if not selected_text:
            raise ChatServiceError("Missing key: selected_text")

        try:
            parent_message_id = int(parent_message_id)
        except (TypeError, ValueError) as exc:
            raise ChatServiceError("Invalid key: parent_message_id") from exc

        session = await self.db.get_platform_session_by_id(session_id)
        if not session:
            raise ChatServiceError(f"Session {session_id} not found")
        if session.creator != username:
            raise ChatServiceError("Permission denied")

        parent_record = await self.db.get_platform_message_history_by_id(
            parent_message_id
        )
        if (
            not parent_record
            or parent_record.platform_id != session.platform_id
            or parent_record.user_id != session_id
        ):
            raise ChatServiceError("Parent message not found")
        if not isinstance(parent_record.content, dict):
            raise ChatServiceError("Invalid parent message content")
        if parent_record.content.get("type") != "bot":
            raise ChatServiceError("Only bot messages can create threads")

        checkpoint_id = parent_record.llm_checkpoint_id
        if not checkpoint_id:
            raise ChatServiceError("Parent message is not linked to LLM history")

        existing = await self.db.get_webchat_thread_by_parent_message_and_text(
            parent_session_id=session_id,
            parent_message_id=parent_message_id,
            selected_text=selected_text,
            creator=username,
        )
        if existing:
            return serialize_thread(existing)

        conversation_id, history = await self.load_current_conversation_history(session)
        turn_range = find_turn_range(history, checkpoint_id)
        if not conversation_id or not turn_range:
            raise ChatServiceError("Linked checkpoint not found")

        _start, end = turn_range
        base_history = history[: end + 1]
        thread = await self.db.create_webchat_thread(
            creator=username,
            parent_session_id=session_id,
            parent_message_id=parent_message_id,
            base_checkpoint_id=checkpoint_id,
            selected_text=selected_text,
        )
        await self.conv_mgr.new_conversation(
            unified_msg_origin=build_thread_unified_msg_origin(
                username,
                thread.thread_id,
            ),
            platform_id="webchat",
            content=base_history,
        )
        return serialize_thread(thread)

    async def create_thread_from_dashboard_payload(
        self,
        username: str,
        payload: object,
    ) -> dict:
        return await self.create_thread(username, self._dashboard_payload(payload))

    async def get_thread(self, username: str, thread_id: str) -> dict:
        thread = await self.db.get_webchat_thread_by_id(thread_id)
        if not thread:
            raise ChatServiceError(f"Thread {thread_id} not found")
        if thread.creator != username:
            raise ChatServiceError("Permission denied")

        history_ls = await self.platform_history_mgr.get(
            platform_id="webchat_thread",
            user_id=thread_id,
            page=1,
            page_size=1000,
        )
        # See `get_session` — same read-path defence for stale
        # ask_user_choice `tool_call` parts. Timestamps are normalized
        # to UTC ISO to match the rest of the read paths.
        history_payload = _sanitize_history_bot_records(
            [history.model_dump() for history in history_ls]
        )
        history_payload = [
            {
                **entry,
                "created_at": to_utc_isoformat(entry.get("created_at")),
                "updated_at": to_utc_isoformat(entry.get("updated_at")),
            }
            for entry in history_payload
        ]
        return {
            "thread": serialize_thread(thread),
            "history": history_payload,
            "is_running": self.running_convs.get(thread_id, False),
            "active_runs": self.get_active_chat_runs(username, thread_id),
        }

    async def get_thread_from_dashboard_query(
        self,
        username: str,
        thread_id: str | None,
    ) -> dict:
        if not thread_id:
            raise ChatServiceError("Missing key: thread_id")
        return await self.get_thread(username, thread_id)

    async def prepare_thread_chat_payload(self, username: str, data: dict) -> dict:
        thread_id = data.get("thread_id")
        if not thread_id:
            raise ChatServiceError("Missing key: thread_id")

        thread = await self.db.get_webchat_thread_by_id(thread_id)
        if not thread:
            raise ChatServiceError(f"Thread {thread_id} not found")
        if thread.creator != username:
            raise ChatServiceError("Permission denied")

        return {
            "session_id": thread.thread_id,
            "message": data.get("message", []),
            "flags": resolve_webchat_request_flags(data),
            "selected_provider": data.get("selected_provider"),
            "selected_model": data.get("selected_model"),
            "_platform_history_id": "webchat_thread",
            "_thread_selected_text": thread.selected_text,
        }

    async def prepare_thread_chat_payload_from_dashboard_payload(
        self,
        username: str,
        payload: object,
    ) -> dict:
        return await self.prepare_thread_chat_payload(
            username,
            self._dashboard_payload(payload),
        )

    async def delete_thread(self, username: str, thread_id: str) -> dict:
        thread = await self.db.get_webchat_thread_by_id(thread_id)
        if not thread:
            raise ChatServiceError(f"Thread {thread_id} not found")
        if thread.creator != username:
            raise ChatServiceError("Permission denied")

        await self.db.delete_webchat_thread(thread_id)
        await self.delete_threads_by_ids([thread_id], username)
        return {"thread_id": thread_id}

    async def delete_thread_from_dashboard_payload(
        self,
        username: str,
        payload: object,
    ) -> dict:
        data = self._dashboard_payload(payload)
        thread_id = data.get("thread_id")
        if not thread_id:
            raise ChatServiceError("Missing key: thread_id")
        return await self.delete_thread(username, thread_id)

    async def update_message(self, username: str, data: dict) -> dict:
        session_id = data.get("session_id")
        message_id = data.get("message_id")
        content = data.get("content")
        if not session_id:
            raise ChatServiceError("Missing key: session_id")
        if message_id is None:
            raise ChatServiceError("Missing key: message_id")

        try:
            message_id = int(message_id)
            if not isinstance(content, dict):
                raise ValueError("Missing key: content")
            content = sanitize_message_content(content)
        except (TypeError, ValueError) as exc:
            raise ChatServiceError(str(exc)) from exc

        session = await self.db.get_platform_session_by_id(session_id)
        if not session:
            raise ChatServiceError(f"Session {session_id} not found")
        if session.creator != username:
            raise ChatServiceError("Permission denied")

        record = await self.db.get_platform_message_history_by_id(message_id)
        if not record:
            raise ChatServiceError(f"Message {message_id} not found")
        if record.platform_id != session.platform_id or record.user_id != session_id:
            raise ChatServiceError("Message does not belong to the session")
        if not isinstance(record.content, dict):
            raise ChatServiceError("Invalid message content")
        if record.content.get("type") != content.get("type"):
            raise ChatServiceError("Message type cannot be changed")
        if content.get("type") != "user":
            raise ChatServiceError("Only user messages can be edited")

        platform_history = await self.get_sorted_platform_history(session)
        latest_user_record = next(
            (
                item
                for item in reversed(platform_history)
                if isinstance(item.content, dict) and item.content.get("type") == "user"
            ),
            None,
        )
        if not latest_user_record or latest_user_record.id != message_id:
            raise ChatServiceError("Only the latest user message can be edited")

        checkpoint_id = record.llm_checkpoint_id
        if not checkpoint_id:
            raise ChatServiceError(
                "This message is not linked to LLM history and cannot be edited"
            )

        conversation_id, history = await self.load_current_conversation_history(session)
        turn_range = find_turn_range(history, checkpoint_id)
        if not conversation_id or not turn_range:
            raise ChatServiceError("Linked checkpoint not found")
        if not is_latest_checkpoint(history, checkpoint_id):
            raise ChatServiceError("Only the latest turn can be edited")

        start, end = turn_range
        target_index = find_turn_user_index(history, start, end)
        if target_index is None:
            raise ChatServiceError("Linked user message not found")

        new_checkpoint_id = str(uuid.uuid4())
        truncated_history = history[:start]
        await self.platform_history_mgr.update(
            message_id=message_id,
            content=content,
            llm_checkpoint_id=new_checkpoint_id,
        )
        deleted_message_ids = await self.delete_platform_history_after(
            session, message_id
        )
        thread_ids = await self.db.delete_webchat_threads_by_parent_message_ids(
            session_id,
            deleted_message_ids,
        )
        await self.delete_threads_by_ids(thread_ids, username)
        await self.conv_mgr.update_conversation(
            unified_msg_origin=build_webchat_unified_msg_origin(session),
            conversation_id=conversation_id,
            history=truncated_history,
        )
        await self.db.update_platform_session(session_id=session_id)
        updated = await self.db.get_platform_message_history_by_id(message_id)
        return {
            "message": serialize_history_entry(updated) if updated else None,
            "needs_regenerate": True,
            "truncated_after_message": True,
        }

    async def update_message_from_dashboard_payload(
        self,
        username: str,
        payload: object,
    ) -> dict:
        return await self.update_message(username, self._dashboard_payload(payload))

    async def prepare_regenerate_message_payload(
        self,
        username: str,
        data: dict,
    ) -> dict:
        session_id = data.get("session_id")
        message_id = data.get("message_id")
        if not session_id:
            raise ChatServiceError("Missing key: session_id")
        if message_id is None:
            raise ChatServiceError("Missing key: message_id")

        try:
            message_id = int(message_id)
        except (TypeError, ValueError) as exc:
            raise ChatServiceError("Invalid key: message_id") from exc

        session = await self.db.get_platform_session_by_id(session_id)
        if not session:
            raise ChatServiceError(f"Session {session_id} not found")
        if session.creator != username:
            raise ChatServiceError("Permission denied")

        target_record = await self.db.get_platform_message_history_by_id(message_id)
        if not target_record:
            raise ChatServiceError(f"Message {message_id} not found")
        if (
            target_record.platform_id != session.platform_id
            or target_record.user_id != session_id
        ):
            raise ChatServiceError("Message does not belong to the session")
        if not isinstance(target_record.content, dict):
            raise ChatServiceError("Invalid message content")
        if target_record.content.get("type") != "bot":
            raise ChatServiceError("Only bot messages can be regenerated")

        checkpoint_id = target_record.llm_checkpoint_id
        if not checkpoint_id:
            raise ChatServiceError("Message is not linked to LLM history")

        conversation_id, history = await self.load_current_conversation_history(session)
        turn_range = find_turn_range(history, checkpoint_id)
        if not conversation_id or not turn_range:
            raise ChatServiceError("Linked checkpoint not found")
        if not is_latest_checkpoint(history, checkpoint_id):
            raise ChatServiceError("Regenerating older turns requires branching")

        start, end = turn_range
        user_index = find_turn_user_index(history, start, end)
        if user_index is None:
            raise ChatServiceError("Linked user message not found")

        platform_history = await self.get_sorted_platform_history(session)
        source_user_record = next(
            (
                item
                for item in reversed(platform_history)
                if item.llm_checkpoint_id == checkpoint_id
                and isinstance(item.content, dict)
                and item.content.get("type") == "user"
            ),
            None,
        )
        if not source_user_record:
            raise ChatServiceError("Linked user display message not found")

        old_bot_record_ids = [
            item.id
            for item in platform_history
            if item.id is not None
            and item.llm_checkpoint_id == checkpoint_id
            and isinstance(item.content, dict)
            and item.content.get("type") == "bot"
        ]
        if not old_bot_record_ids:
            raise ChatServiceError("Linked bot display message not found")

        new_checkpoint_id = str(uuid.uuid4())
        new_history = history[:start] + history[end + 1 :]
        await self.conv_mgr.update_conversation(
            unified_msg_origin=build_webchat_unified_msg_origin(session),
            conversation_id=conversation_id,
            history=new_history,
        )
        thread_ids = await self.db.delete_webchat_threads_by_parent_message_ids(
            session_id,
            old_bot_record_ids,
        )
        await self.delete_threads_by_ids(thread_ids, username)
        for old_bot_record_id in old_bot_record_ids:
            await self.platform_history_mgr.delete_by_id(old_bot_record_id)
        await self.platform_history_mgr.update(
            message_id=source_user_record.id,
            llm_checkpoint_id=new_checkpoint_id,
        )

        return {
            "session_id": session_id,
            "message": source_user_record.content.get("message", []),
            "flags": resolve_webchat_request_flags(data),
            "selected_provider": data.get("selected_provider"),
            "selected_model": data.get("selected_model"),
            "thinking_effort": data.get("thinking_effort"),
            "_skip_user_history": True,
            "_llm_checkpoint_id": new_checkpoint_id,
        }

    async def prepare_regenerate_message_payload_from_dashboard_payload(
        self,
        username: str,
        payload: object,
    ) -> dict:
        return await self.prepare_regenerate_message_payload(
            username,
            self._dashboard_payload(payload),
        )

    async def branch_session(
        self,
        username: str,
        session_id: str,
        message_id: int | str,
    ) -> dict:
        """Create a new session inheriting context up to a given bot message.

        Snapshots the source session: truncates the LLM conversation history
        at the message's checkpoint (inclusive), copies platform display
        history up to the message, and appends a ``branch_info`` divider
        record so the dashboard can render the inherited part collapsible.

        Args:
            username: Dashboard username; must own the source session.
            session_id: Source webchat session ID.
            message_id: Platform message history ID of the bot message to
                branch at.

        Returns:
            Dict with ``session_id``, ``display_name`` and ``inherited_count``
            of the newly created session.

        Raises:
            ChatServiceError: On validation failures (missing session or
                message, wrong owner, non-bot message, missing checkpoint).
        """
        try:
            message_id = int(message_id)
        except (TypeError, ValueError) as exc:
            raise ChatServiceError("Invalid key: message_id") from exc

        session = await self.db.get_platform_session_by_id(session_id)
        if not session:
            raise ChatServiceError(f"Session {session_id} not found")
        if session.creator != username:
            raise ChatServiceError("Permission denied")

        target_record = await self.db.get_platform_message_history_by_id(message_id)
        if not target_record:
            raise ChatServiceError(f"Message {message_id} not found")
        if (
            target_record.platform_id != session.platform_id
            or target_record.user_id != session_id
        ):
            raise ChatServiceError("Message does not belong to the session")
        if not isinstance(target_record.content, dict):
            raise ChatServiceError("Invalid message content")
        if target_record.content.get("type") != "bot":
            raise ChatServiceError("Only bot messages can be branched")

        checkpoint_id = target_record.llm_checkpoint_id
        if not checkpoint_id:
            raise ChatServiceError("Message is not linked to LLM history")

        conversation_id, history = await self.load_current_conversation_history(session)
        checkpoint_index = find_checkpoint_index(history, checkpoint_id)
        if not conversation_id or checkpoint_index is None:
            raise ChatServiceError("Linked checkpoint not found")

        source_conversation = await self.conv_mgr.get_conversation(
            unified_msg_origin=build_webchat_unified_msg_origin(session),
            conversation_id=conversation_id,
        )

        source_title = (
            session.display_name
            or (source_conversation.title if source_conversation else None)
            or "新对话"
        )
        new_session = await self.db.create_platform_session(
            creator=username,
            platform_id=session.platform_id,
            is_group=session.is_group,
            display_name=f"分支 · {source_title}",
        )

        # 2026-08-13 fix: 分支会话继承源会话的项目归属。源会话属于
        # ChatUI 项目时，为新会话建立相同的 SessionProjectRelation，
        # 否则前端项目视图会把新分支会话渲染到项目外。
        source_project = await self.db.get_project_by_session(session_id, username)
        if source_project is not None:
            await self.db.add_session_to_project(
                new_session.session_id, source_project.project_id
            )

        await self.conv_mgr.new_conversation(
            build_webchat_unified_msg_origin(new_session),
            platform_id=session.platform_id,
            content=deepcopy(history[: checkpoint_index + 1]),
            title=source_conversation.title if source_conversation else None,
            persona_id=source_conversation.persona_id if source_conversation else None,
        )

        platform_history = await self.get_sorted_platform_history(session)
        inherited_count = 0
        for item in platform_history:
            if item.id is None or item.id > target_record.id:
                break
            await self.platform_history_mgr.insert(
                platform_id=session.platform_id,
                user_id=new_session.session_id,
                content=deepcopy(item.content),
                sender_id=item.sender_id,
                sender_name=item.sender_name,
                llm_checkpoint_id=item.llm_checkpoint_id,
            )
            inherited_count += 1

        await self.platform_history_mgr.insert(
            platform_id=session.platform_id,
            user_id=new_session.session_id,
            content={
                "type": "branch_info",
                "source_session_id": session_id,
                "source_message_id": message_id,
                "inherited_count": inherited_count,
            },
            sender_id="bot",
            sender_name="bot",
        )

        # Keep the lazily-built relations cache in sync so the next session
        # list reflects this branch without a rescan.
        self.db.update_branch_relation(new_session.session_id, session_id, message_id)

        return {
            "session_id": new_session.session_id,
            "display_name": new_session.display_name,
            "inherited_count": inherited_count,
        }

    async def update_session_display_name(
        self,
        username: str,
        session_id: str,
        display_name,
    ) -> dict:
        session = await self.db.get_platform_session_by_id(session_id)
        if not session:
            raise ChatServiceError(f"Session {session_id} not found")
        if session.creator != username:
            raise ChatServiceError("Permission denied")

        await self.db.update_platform_session(
            session_id=session_id,
            display_name=display_name,
        )
        return {}

    async def update_session_display_name_from_dashboard_payload(
        self,
        username: str,
        payload: object,
    ) -> dict:
        data = self._dashboard_payload(payload)
        session_id = data.get("session_id")
        display_name = data.get("display_name")
        if not session_id:
            raise ChatServiceError("Missing key: session_id")
        if display_name is None:
            raise ChatServiceError("Missing key: display_name")
        return await self.update_session_display_name(
            username, session_id, display_name
        )

    @staticmethod
    def _dashboard_payload(payload: object) -> dict:
        if payload is None:
            raise ChatServiceError("Missing JSON body")
        if not isinstance(payload, dict):
            raise ChatServiceError("Invalid JSON body: expected object")
        return payload
