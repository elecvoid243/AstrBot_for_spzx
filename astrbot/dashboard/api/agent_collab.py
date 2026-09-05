"""Agent collab dashboard routes (legacy /api/agent_collab/*).

Superseded by Agent Teams (spec §2.2); kept for the transition period —
do not extend.
"""

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from astrbot import logger
from astrbot.dashboard.responses import error, ok
from astrbot.dashboard.services.agent_collab_service import (
    AgentCollabService,
    AgentCollabServiceError,
)

from .auth import require_dashboard_user

router = APIRouter(tags=["Agent Collab"])
legacy_router = APIRouter(
    prefix="/api/agent_collab",
    tags=["Dashboard Agent Collab"],
    include_in_schema=False,
)


def get_service(request: Request) -> AgentCollabService:
    return request.app.state.services.agent_collab


def _err(e: AgentCollabServiceError) -> dict:
    return error(str(e))


async def _json_body(request: Request) -> dict:
    try:
        payload = await request.json()
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


@legacy_router.get("/groups")
async def list_groups(
    username: str = Depends(require_dashboard_user),
    service: AgentCollabService = Depends(get_service),
):
    return ok(await service.list_groups(username))


@legacy_router.post("/groups")
async def create_group(
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: AgentCollabService = Depends(get_service),
):
    try:
        return ok(await service.create_group(username, await _json_body(request)))
    except AgentCollabServiceError as e:
        return _err(e)


@legacy_router.put("/groups/{group_id}")
async def update_group(
    group_id: str,
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: AgentCollabService = Depends(get_service),
):
    try:
        return ok(
            await service.update_group(username, group_id, await _json_body(request))
        )
    except AgentCollabServiceError as e:
        return _err(e)


@legacy_router.delete("/groups/{group_id}")
async def delete_group(
    group_id: str,
    username: str = Depends(require_dashboard_user),
    service: AgentCollabService = Depends(get_service),
):
    try:
        return ok(await service.delete_group(username, group_id))
    except AgentCollabServiceError as e:
        return _err(e)


@legacy_router.post("/groups/{group_id}/discussions")
async def start_discussion(
    group_id: str,
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: AgentCollabService = Depends(get_service),
):
    payload = await _json_body(request)
    topic = str(payload.get("topic") or "")

    # Fan-out event bus: every emitted event is retained in entry["events"]
    # (replayed to each new SSE subscriber) and pushed to live subscribers.
    subscribers: list[asyncio.Queue] = []
    events: list[dict] = []

    def emit(event: dict) -> None:
        # Live fan-out first — verbatim per-token deltas.
        for sub in subscribers:
            try:
                sub.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("collab event subscriber queue full, dropping event")
        # Retained history: coalesce consecutive stream deltas for the same
        # session. A long reply otherwise accumulates thousands of
        # per-character events, blowing both memory and the 512-event
        # subscriber queue on replay (the replay used to crash the whole SSE
        # request with QueueFull). The frontend appends consecutive stream
        # deltas identically, so the rendered transcript is unchanged.
        if event.get("type") == "message" and event.get("direction") == "stream":
            last = events[-1] if events else None
            if (
                isinstance(last, dict)
                and last.get("type") == "message"
                and last.get("direction") == "stream"
                and last.get("session_id") == event.get("session_id")
            ):
                last["text"] = last.get("text", "") + str(event.get("text", ""))
                last["ts"] = event.get("ts", last.get("ts"))
                return
        events.append(event)

    try:
        state = await service.start_discussion(
            username,
            group_id,
            topic,
            service.build_ports(request.app.state.services.chat, username, emit),
        )
    except AgentCollabServiceError as e:
        return _err(e)
    entry = service.discussions[state["id"]]
    entry["events"] = events
    entry["subscribers"] = subscribers
    state["task"] = asyncio.create_task(
        entry["runner"].run(), name=f"collab_{state['id']}"
    )
    return ok({"discussion_id": state["id"]})


@legacy_router.get("/discussions/active")
async def active_discussion(
    username: str = Depends(require_dashboard_user),
    service: AgentCollabService = Depends(get_service),
):
    """Return the caller's still-running discussion, if any.

    Lets the frontend re-attach (controls + SSE replay) after a page reload
    even though the runner kept going server-side.
    """
    active = service.list_active_discussions(username)
    return ok({"discussion": active[0] if active else None})


@legacy_router.post("/discussions/{discussion_id}/stop")
async def stop_discussion(
    discussion_id: str,
    service: AgentCollabService = Depends(get_service),
):
    try:
        return ok(service.stop_discussion(discussion_id))
    except AgentCollabServiceError as e:
        return _err(e)


@legacy_router.post("/discussions/{discussion_id}/resume")
async def resume_discussion(
    discussion_id: str,
    request: Request,
    service: AgentCollabService = Depends(get_service),
):
    payload = await _json_body(request)
    try:
        return ok(
            service.resume_discussion(
                discussion_id, reset_hops=bool(payload.get("reset_hops"))
            )
        )
    except AgentCollabServiceError as e:
        return _err(e)


@legacy_router.post("/discussions/{discussion_id}/route")
async def route_discussion(
    discussion_id: str,
    request: Request,
    service: AgentCollabService = Depends(get_service),
):
    payload = await _json_body(request)
    try:
        return ok(
            service.route_discussion(
                discussion_id,
                str(payload.get("target_session_id") or ""),
                str(payload.get("message") or ""),
            )
        )
    except AgentCollabServiceError as e:
        return _err(e)


@legacy_router.get("/discussions/{discussion_id}/stream")
async def stream_discussion(
    discussion_id: str,
    request: Request,
    service: AgentCollabService = Depends(get_service),
):
    entry = service.discussions.get(discussion_id)
    if not entry:
        return error(f"讨论 '{discussion_id}' 不存在")
    subscribers = entry.get("subscribers")
    if subscribers is None:
        return error("该讨论无事件流")

    # Unbounded on purpose: emit() coalesces consecutive stream deltas (so
    # the retained history — and therefore the replay — stays small), and a
    # replay into a bounded queue used to crash the whole SSE request with
    # QueueFull once a discussion outgrew 512 events.
    queue: asyncio.Queue = asyncio.Queue()
    # Replay the full history first so late subscribers (e.g. the group
    # transcript panel opened mid-discussion) still see every turn.
    for event in entry.get("events", []):
        queue.put_nowait(event)
    subscribers.append(queue)

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            subscribers.remove(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


def _history_text(content: dict) -> str:
    """Collect the plain prose of a history content dict (mirrors the
    conversation-service search extractor: plain/markdown part text)."""
    parts = content.get("message")
    if isinstance(parts, str):
        return parts
    if not isinstance(parts, list):
        return ""
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, str):
            chunks.append(part)
            continue
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str):
            chunks.append(text)
    return "".join(chunks)


def _record_ts(record) -> str:
    """Serialize a history record's created_at to an ISO string."""
    created = getattr(record, "created_at", None)
    if created is None:
        return ""
    if hasattr(created, "isoformat"):
        return created.isoformat()
    return str(created)


def _is_stats_record(content: dict, text: str) -> bool:
    """True for telemetry-only bot records (agent_stats blobs).

    An agent_stats chain is persisted as a single plain JSON part whose text
    carries ``token_usage``; it must not be treated as a collab reply.
    """
    parts = content.get("message")
    if not isinstance(parts, list) or len(parts) != 1:
        return False
    part = parts[0]
    if not isinstance(part, dict) or part.get("type") != "plain":
        return False
    return '"token_usage"' in str(part.get("text", ""))


def _extract_session_transcript(history) -> list[dict]:
    """Reduce one session's history to its collab turns.

    Injected inputs are user messages starting with the ``[来自 `` marker;
    each is paired with the next bot reply in the same session. Everything
    else (ordinary conversation) is skipped, and directive fences are
    stripped from replies.
    """
    messages: list[dict] = []
    in_turn = False
    for record in history:
        content = getattr(record, "content", None)
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except Exception:
                continue
        if not isinstance(content, dict):
            continue
        text = _history_text(content)
        ctype = content.get("type")
        if ctype == "user" and text.startswith("[来自 "):
            messages.append(
                {
                    "direction": "sent",
                    "text": text,
                    "ts": _record_ts(record),
                }
            )
            in_turn = True
        elif in_turn and ctype == "bot" and text:
            if _is_stats_record(content, text):
                continue  # telemetry blob; keep waiting for the real reply
            # Keep the full parts (thinking / tool calls / output, incl. the
            # moderator's routing directive) so the panel renders the agent's
            # complete activity, not just the plain text.
            messages.append(
                {
                    "direction": "reply",
                    "text": text,
                    "parts": content.get("message") or [],
                    "ts": _record_ts(record),
                }
            )
            in_turn = False
    return messages


@legacy_router.get("/groups/{group_id}/transcript")
async def group_transcript(
    group_id: str,
    request: Request,
    username: str = Depends(require_dashboard_user),
    service: AgentCollabService = Depends(get_service),
):
    """Aggregate the persistent per-session history into the group transcript.

    Each member session already persists its own collab turns; this endpoint
    reads those records, keeps only collab turns and merges them in global
    chronological order — no extra storage is involved.
    """
    try:
        group = await service.load_group(username, group_id)
    except AgentCollabServiceError as e:
        return _err(e)
    chat = request.app.state.services.chat
    history_mgr = chat.platform_history_mgr
    messages: list[dict] = []
    for member in group["members"]:
        session_id = member["session_id"]
        try:
            history = await history_mgr.get(
                platform_id="webchat",
                user_id=session_id,
                page=1,
                page_size=100000,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "collab transcript: history read failed for %s: %s",
                session_id,
                exc,
            )
            continue
        for msg in _extract_session_transcript(history):
            msg["session_id"] = session_id
            messages.append(msg)
    messages.sort(key=lambda m: m["ts"])
    return ok({"messages": messages})
