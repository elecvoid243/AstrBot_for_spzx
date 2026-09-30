"""Streams subagent execution progress into the WebChat SSE back queue.

The sink is a thin mapper: it converts subagent ``AgentResponse``s into
``subagent_event`` payloads written to the *main* run's back queue, so the
existing ChatService fan-out forwards them to the dashboard without any
new transport. Only instantiated for webchat foreground handoffs.
"""

# Author: elecvoid243
# Date: 2026-07-26
# Plan: docs/superpowers/plans/2026-07-26-subagent-chatui-progress.md (Task 1)

import time
import uuid

from astrbot.core.agent.response import AgentResponse
from astrbot.core.message.components import Json
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
    webchat_queue_mgr,
)

TOOL_RESULT_MAX_CHARS = 2000
"""Cap for displayed/persisted tool results inside a subagent run block."""


class SubAgentEventSink:
    """Maps subagent runner responses to ``subagent_event`` SSE payloads.

    Attributes:
        subagent_run_id: Correlation id shared by every event of one run.
    """

    def __init__(
        self,
        message_id: str,
        agent_name: str,
        input_preview: str = "",
        input_full: str = "",
    ):
        """Create a sink bound to the main run's back queue.

        Args:
            message_id: Main run message id (== back queue request id).
            agent_name: Display name of the subagent.
            input_preview: Truncated task description for the run header.
            input_full: Untruncated task text, shown when the user expands
                the task section in the dashboard.
        """
        self.subagent_run_id = f"sa_{uuid.uuid4().hex[:12]}"
        self._message_id = message_id
        self._agent_name = agent_name
        self._input_preview = input_preview
        self._input_full = input_full
        self._closed = False

    async def _emit(self, kind: str, payload: dict) -> None:
        if self._closed:
            return
        event = {
            "type": "subagent_event",
            "message_id": self._message_id,
            "streaming": True,
            "data": {
                "subagent_run_id": self.subagent_run_id,
                "agent_name": self._agent_name,
                "kind": kind,
                "payload": payload,
                "ts": time.time(),
            },
        }
        accepted = await webchat_queue_mgr.put_back_queue(self._message_id, event)
        if not accepted:
            # Main run queue is gone (client aborted); stop emitting cheaply.
            self._closed = True

    async def start(self) -> None:
        """Emit the ``started`` event (run header)."""
        await self._emit(
            "started",
            {"input_preview": self._input_preview, "input_full": self._input_full},
        )

    async def __call__(self, resp: AgentResponse) -> None:
        """Forward one runner response. Signature matches ``response_sink``.

        Args:
            resp: One response produced by the subagent's tool-loop runner.
        """
        chain = resp.data.get("chain") if isinstance(resp.data, dict) else None
        if chain is None:
            return
        if resp.type == "streaming_delta":
            text = chain.get_plain_text()
            if not text:
                return
            kind = "reasoning_delta" if chain.type == "reasoning" else "text_delta"
            await self._emit(kind, {"text": text})
        elif resp.type == "tool_call":
            for comp in chain.chain:
                if isinstance(comp, Json) and isinstance(comp.data, dict):
                    await self._emit(
                        "tool_call",
                        {
                            "id": comp.data.get("id"),
                            "name": comp.data.get("name"),
                            "args": comp.data.get("args") or {},
                        },
                    )
                    break
        elif resp.type == "tool_call_result":
            for comp in chain.chain:
                if isinstance(comp, Json) and isinstance(comp.data, dict):
                    result = comp.data.get("result")
                    await self._emit(
                        "tool_call_result",
                        {
                            "id": comp.data.get("id"),
                            "result": str(result)[:TOOL_RESULT_MAX_CHARS]
                            if result is not None
                            else "",
                        },
                    )
                    break

    async def user_message(self, seq: int, text: str) -> None:
        """Emit a ``user_message`` event: the user sent a follow-up to this run.

        Args:
            seq: The follow-up ticket sequence number within this run.
            text: The user's message text.
        """
        await self._emit("user_message", {"seq": seq, "text": text})

    async def user_message_relayed(self, seq: int) -> None:
        """Emit a ``user_message_relayed`` event: the run ended before the
        follow-up was consumed, so it was relayed to the main agent.

        Args:
            seq: The follow-up ticket sequence number to mark as relayed.
        """
        await self._emit("user_message_relayed", {"seq": seq})

    async def complete(self, result_text: str, execution_time: float) -> None:
        """Emit the terminal ``completed`` event.

        Args:
            result_text: Final subagent answer (fallback display text).
            execution_time: Wall-clock seconds the subagent run took.
        """
        await self._emit(
            "completed",
            {"result_text": result_text or "", "execution_time": execution_time},
        )
        self._closed = True

    async def fail(self, status: str, error: str) -> None:
        """Emit a terminal failure event.

        Args:
            status: ``"failed"`` or ``"timeout"``.
            error: Human-readable error message.
        """
        await self._emit(status, {"error": error})
        self._closed = True
