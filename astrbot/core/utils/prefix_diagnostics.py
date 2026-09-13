"""Per-session request fingerprints for provider prefix-cache diagnostics.

The main-agent runner records a fingerprint of every chat request it sends
(model, per-tool schema hashes, per-message canonical-JSON hashes). Flows that
issue same-prefix requests on their own — e.g. the compact plugin's summary
request — can compare their payload against the recorded one to locate where a
provider prefix-cache miss starts. Subagent requests are skipped: their
payloads fork from the main context and would overwrite the record.
"""

import hashlib
import json
import time
from collections import OrderedDict
from typing import Any

from astrbot.core.agent.message import Message

# session_id -> fingerprint record; bounded LRU to cap memory.
_RECORDS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_MAX_SESSIONS = 64


def _canonical_text(message: Message | dict) -> str:
    """Canonical JSON text of one message (sorted keys, readable unicode)."""
    data = message.model_dump() if isinstance(message, Message) else message
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


def _sha16(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def fingerprint_messages(messages: list) -> list[str]:
    """Per-message canonical-JSON hashes.

    Args:
        messages: Message objects and/or dicts (payload-stage contexts).

    Returns:
        One sha1[:16] per message; empty string for unhashable items.
    """
    hashes: list[str] = []
    for message in messages:
        try:
            hashes.append(_sha16(_canonical_text(message)))
        except (TypeError, ValueError):
            hashes.append("")
    return hashes


def fingerprint_tools(func_tool) -> dict:
    """Overall + per-tool schema hashes of a tool set.

    Args:
        func_tool: The ToolSet attached to the request (None allowed).

    Returns:
        ``{"hash": sha1|None, "tools": {tool_name: sha1}}``; hash is None
        when no tools are attached.
    """
    if func_tool is None:
        return {"hash": None, "tools": {}}
    per_tool: dict[str, str] = {}
    for schema in func_tool.openai_schema():
        name = (schema.get("function") or {}).get("name", "")
        per_tool[name] = _sha16(json.dumps(schema, sort_keys=True, ensure_ascii=False))
    overall = _sha16(json.dumps(per_tool, sort_keys=True, ensure_ascii=False))
    return {"hash": overall, "tools": per_tool}


def _system_text_of(messages: list) -> str | None:
    """Raw text of the leading system message (None when there is none).

    Non-string content is canonicalized so the plugin can still diff it,
    though a plain string is the normal shape for a system message.
    """
    for message in messages:
        role = message.get("role") if isinstance(message, dict) else message.role
        if role != "system":
            continue
        content = (
            message.get("content") if isinstance(message, dict) else message.content
        )
        if isinstance(content, str):
            return content
        try:
            return json.dumps(content, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            return None
    return None


def record_session_request(
    session_id: str,
    *,
    model: str | None,
    func_tool,
    messages: list,
    is_subagent: bool = False,
) -> None:
    """Record the fingerprint of a main-agent chat request.

    Args:
        session_id: Unified message origin of the request.
        model: The model string the request carries (None = provider default).
        func_tool: The tool set sent with the request.
        messages: The payload-stage message list.
        is_subagent: Subagent runners are skipped so the record always
            reflects the main conversation's chat requests.
    """
    if is_subagent or not session_id:
        return
    _RECORDS.pop(session_id, None)
    _RECORDS[session_id] = {
        "model": model,
        "tools": fingerprint_tools(func_tool),
        "msg_hashes": fingerprint_messages(messages),
        "system_text": _system_text_of(messages),
        "ts": time.time(),
    }
    while len(_RECORDS) > _MAX_SESSIONS:
        _RECORDS.popitem(last=False)


def get_session_request(session_id: str) -> dict | None:
    """The most recently recorded main-agent request fingerprint, or None."""
    return _RECORDS.get(session_id)
