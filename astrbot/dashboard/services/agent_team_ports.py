"""TeamPorts: delivery/collection seam for Agent Teams runners (spec §6.2).

Extracted from AgentCollabService so the DAGRunner (and the Plan-3
AutoOrchestrator) run over the same injectable I/O boundary; tests script
deliver/collect directly.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from astrbot.core.platform.sources.webchat.request_flags import (
    resolve_webchat_request_flags,
)
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
    _extract_conversation_id,
    webchat_queue_mgr,
)
from astrbot.core.umo_alias import parse_umo


def _conversation_id(session_id: str) -> str:
    """Map a member session id (UMO or raw webchat conversation id) to the
    raw conversation id used by the queue manager."""
    if session_id.startswith("webchat!"):
        return _extract_conversation_id(session_id)
    parsed = parse_umo(session_id)
    if parsed["platform"] == "webchat":
        return parsed["session_id"]
    return session_id


@dataclass
class TeamPorts:
    """I/O ports for team runners; injected for testability."""

    deliver: Callable[[str, str, str | None], Awaitable[str]]
    collect: Callable[[str, str], Awaitable[tuple[str, list]]]
    is_busy: Callable[[str], bool]
    emit: Callable[[dict], None]
    reply_timeout: float = 600.0
    busy_poll_interval: float = 2.0


def build_ports_for_test(
    mgr,
    username: str,
    emit: Callable[[dict], None],
    reply_timeout: float = 600.0,
    busy_poll_interval: float = 2.0,
    is_busy_override: Callable[[str], bool] | None = None,
    run_registrar: Callable[[str, str, str], Awaitable[None]] | None = None,
) -> TeamPorts:
    """Build TeamPorts over a given WebChatQueueMgr (test seam).

    Args:
        mgr: WebChatQueueMgr instance.
        username: Sender name recorded on injected turns.
        emit: Runner event sink (SSE multiplexer).
        reply_timeout: Per-turn collection timeout in seconds.
        busy_poll_interval: Busy-wait poll interval in seconds.
        is_busy_override: Optional busy predicate override.
        run_registrar: Optional async (cid, message_id, checkpoint_id) hook
            awaited BEFORE the input is queued; production wires it to
            ChatService.register_synthetic_chat_run.

    Returns:
        Configured TeamPorts.
    """
    # One system-event subscription per conversation, created in deliver()
    # BEFORE the input item is queued, mirroring build_chat_stream's
    # register-then-inject ordering.
    subscriptions: dict[str, asyncio.Queue] = {}

    async def deliver(session_id: str, text: str, context: str | None = None) -> str:
        cid = _conversation_id(session_id)
        message_id = uuid.uuid4().hex
        llm_checkpoint_id = uuid.uuid4().hex
        queue = mgr.get_or_create_queue(cid)
        subscriptions.setdefault(cid, mgr.subscribe_system(cid))
        if run_registrar is not None:
            await run_registrar(cid, message_id, llm_checkpoint_id)
        await queue.put(
            (
                username,
                cid,
                {
                    "message": [{"type": "plain", "text": text}],
                    "selected_provider": None,
                    "selected_model": None,
                    "flags": resolve_webchat_request_flags({}),
                    "message_id": message_id,
                    "llm_checkpoint_id": llm_checkpoint_id,
                    "thread_selected_text": None,
                    "_api_key_allow_admin_role": None,
                    # Per-turn team framing, injected by build_main_agent as
                    # a temp extra (provider-facing, not persisted).
                    "team_context": context,
                    "persist_user_history": True,
                },
            )
        )
        return message_id

    async def collect(session_id: str, message_id: str) -> tuple[str, list]:
        from astrbot.dashboard.services.chat_service import BotMessageAccumulator

        cid = _conversation_id(session_id)
        queue = subscriptions.get(cid)
        if queue is None:
            raise asyncio.TimeoutError(
                f"no subscription for {cid}; deliver() must run first"
            )
        acc = BotMessageAccumulator()
        prev_text = ""
        while True:
            payload = await queue.get()
            if not isinstance(payload, dict) or payload.get("message_id") != message_id:
                continue
            msg_type = payload.get("type")
            if msg_type == "plain":
                acc.add_plain(
                    payload.get("data", ""),
                    chain_type=payload.get("chain_type"),
                    streaming=bool(payload.get("streaming")),
                )
                full = acc.plain_text()
                delta = full[len(prev_text) :] if full.startswith(prev_text) else full
                prev_text = full
                if delta:
                    emit(
                        {
                            "type": "message",
                            "direction": "stream",
                            "session_id": session_id,
                            "text": delta,
                            "ts": time.time(),
                        }
                    )
            elif msg_type in ("complete", "end"):
                if (
                    msg_type == "complete"
                    and not payload.get("streaming")
                    and payload.get("data")
                ):
                    acc.add_plain(
                        payload["data"],
                        chain_type=payload.get("chain_type"),
                        streaming=False,
                    )
                return acc.plain_text(), acc.build_message_parts()

    return TeamPorts(
        deliver=deliver,
        collect=collect,
        is_busy=is_busy_override or (lambda sid: False),
        emit=emit,
        reply_timeout=reply_timeout,
        busy_poll_interval=busy_poll_interval,
    )


def build_ports(chat_service, username: str, emit: Callable[[dict], None]) -> TeamPorts:
    """Build production TeamPorts bound to the real ChatService.

    Args:
        chat_service: Dashboard ChatService; injected turns register through
            register_synthetic_chat_run and busy detection reads
            chat_runs_by_session.
        username: Run owner, sender of injected turns.
        emit: Runner event sink (SSE multiplexer).

    Returns:
        TeamPorts wired to the global webchat queue manager.
    """
    ports = build_ports_for_test(
        webchat_queue_mgr,
        username,
        emit,
        run_registrar=chat_service.register_synthetic_chat_run,
    )
    ports.is_busy = lambda sid: bool(
        chat_service.chat_runs_by_session.get(_conversation_id(sid))
    )
    return ports
