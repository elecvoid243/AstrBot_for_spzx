# Author: elecvoid243 · Created: 2026-08-30
"""Kernel-side standing-goal loop service.

Owns everything the astrbot_plugin_goal plugin used to own: goal state
storage (shared-preferences KV), the judge LLM caller, the synthetic-turn
injection, the on_llm_request guard, and the on_agent_done judge/loop
driver. The ``/goal`` command surface lives in the ``goal`` builtin star
(``astrbot/builtin_stars/goal``) and delegates here.

Rendering integration: when a run registrar is set (the dashboard wires it
to ``ChatService.register_synthetic_chat_run`` at startup), injected turns
on webchat sessions become first-class chat runs — they render through the
exact primary-run pipeline (back_queue → standard consume/persist → run
stream) instead of the orphan system-mirror channel. Non-webchat sessions
skip the registrar and keep the platform's native reply path.
"""

from __future__ import annotations

import asyncio
import copy
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.core import logger, sp
from astrbot.core.goal.goal_judge import judge_goal
from astrbot.core.goal.goal_manager import GoalManager
from astrbot.core.goal.goal_state import GoalState
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent

GOAL_CONTINUATION_EXTRA = "goal_continuation"

# Storage scope for kernel goal state (the legacy plugin used the
# ("plugin", "astrbot_plugin_goal") namespace; migrate_legacy_states()
# imports and cleans those keys once).
KV_SCOPE = "goal"
KV_SCOPE_ID = "kernel"

# Default tool names stripped from the LLM request during goal-loop
# continuation turns. These tools are *blocking* — they wait on user
# input — which contradicts the goal loop's design (Agent must complete
# decisions autonomously). Users can extend this list via the
# ``block_tools_during_goal`` config.
#
# "ask_user_choice" is the explicit blocker called out in the design
# review: it suspends the Agent until the user clicks an option in
# the dashboard, which would silently exhaust the turn budget.
DEFAULT_BLOCKED_TOOLS_DURING_GOAL: frozenset[str] = frozenset(
    {
        "ask_user_choice",
        "ask_user",
        "human_input",
        "request_human",
        "request_user_input",
    }
)

RegistrarFn = Callable[[str, str, str], Awaitable[None]]
"""Async callable (umo, message_id, llm_checkpoint_id) -> None; set by the
dashboard to register injected turns as first-class chat runs."""


def build_continuation_event(event: AstrMessageEvent, text: str) -> AstrMessageEvent:
    """Clone an event as a synthetic user turn carrying ``text``.

    The clone gets a fresh message_id (avoid platform dedup), a fresh
    extras dict (do not inherit one-shot keys from the previous turn),
    and wake flags set so the pipeline runs the LLM stage. Precedent:
    ``builtin_stars/astrbot/main.py`` (empty-mention re-injection) and
    ``core/cron/events.py`` (CronMessageEvent).

    Args:
        event: The source event of the just-finished turn.
        text: The user-role text for the synthetic turn.

    Returns:
        A new event ready for ``context.get_event_queue().put_nowait()``.
    """
    new_event = copy.copy(event)
    msg_obj = copy.deepcopy(event.message_obj)
    msg_obj.message = [Plain(text)]
    msg_obj.message_str = text
    msg_obj.message_id = uuid.uuid4().hex
    msg_obj.timestamp = int(time.time())
    new_event.message_obj = msg_obj
    # Event-level message_str must be updated too: waking_check /
    # CommandFilter read event.message_str (NOT message_obj.message_str).
    # Leaving the original command text here re-triggers the command
    # handler on every injected turn (self-sustaining loop).
    new_event.message_str = text
    # Reset per-run runtime flags inherited from the source event, or the
    # ProcessStage agent gate (`not event._has_send_oper and ...`) silently
    # skips the LLM stage for the synthetic turn:
    # - kickoff clones are made AFTER the command reply was sent (the
    #   handler's yield reaches the respond stage before resuming), and
    # - continuation clones are made after the turn's reply was streamed;
    #   both source events therefore have _has_send_oper=True at clone time.
    new_event._has_send_oper = False
    new_event.call_llm = False
    new_event._force_stopped = False
    # Fresh extras: shallow copy shares the dict; replace it outright.
    if hasattr(new_event, "_extras"):
        new_event._extras = {GOAL_CONTINUATION_EXTRA: True}
    else:
        new_event.set_extra(GOAL_CONTINUATION_EXTRA, True)
    try:
        new_event.clear_result()
    except Exception:
        pass
    new_event.is_at_or_wake_command = True
    new_event.is_wake = True
    return new_event


class _SpKVStorage:
    """GoalManager storage adapter over the shared-preferences KV."""

    async def get(self, key: str) -> Any:
        return await sp.get_async(KV_SCOPE, KV_SCOPE_ID, key, None)

    async def set(self, key: str, value: Any) -> None:
        await sp.put_async(KV_SCOPE, KV_SCOPE_ID, key, value)

    async def delete(self, key: str) -> None:
        await sp.remove_async(KV_SCOPE, KV_SCOPE_ID, key)


class GoalService:
    """Kernel-side standing-goal loop: state, judge, and turn injection."""

    def __init__(self) -> None:
        self.goals = GoalManager(_SpKVStorage())
        self._context = None
        self._judged_runs: set[str] = set()
        self._run_registrar: Callable[[str, str, str], Awaitable[None]] | None = None
        self._migrated = False

    def bind(self, context) -> None:
        """Bind the astrbot Context (event queue / llm_generate / send).

        Args:
            context: The core Context instance.
        """
        self._context = context

    def set_run_registrar(
        self, registrar: Callable[[str, str, str], Awaitable[None]] | None
    ) -> None:
        """Set the first-class run registrar (wired by the dashboard).

        Args:
            registrar: Async callable (umo, message_id, llm_checkpoint_id)
              that registers the injected turn as a first-class chat run.
        """
        self._run_registrar = registrar

    def _config(self) -> dict:
        if self._context is None:
            return {}
        try:
            return self._context.get_config().get("goal", {}) or {}
        except Exception:
            return {}

    def check_permission(self, event: AstrMessageEvent) -> str | None:
        """Return a refusal message when the sender may not use /goal."""
        if self._config().get("admin_only", True) and event.role != "admin":
            return "⛔ 仅管理员可以使用 /goal。"
        return None

    async def set_goal(self, umo: str, goal: str) -> GoalState:
        """Start a standing goal with the turn budget from config.

        Args:
            umo: Unified message origin of the session.
            goal: The goal text.

        Returns:
            The created goal state.

        Raises:
            ValueError: If the goal text is empty or max_turns is not an int.
        """
        return await self.goals.set(
            umo, goal, max_turns=self._config().get("max_turns")
        )

    async def _notify(self, event: AstrMessageEvent, text: str) -> None:
        """Proactively push a message to the session of ``event``."""
        try:
            await self._context.send_message(
                event.unified_msg_origin, MessageChain().message(text)
            )
        except Exception as e:
            logger.warning(f"goal loop: notify failed: {e}")

    async def _judge_llm_caller(self, umo: str, system_prompt: str, user_prompt: str):
        """Call the judge model; return raw text, or None on transport error.

        The returned ``completion_text`` is the provider-parsed body: thinking
        is already separated into ``LLMResponse.reasoning_content`` (and
        openai_source even strips ``<think>`` tags out of the text), so the
        verdict text is clean. ``goal_judge.parse_judge_response`` additionally
        strips any leftover tracing markup as a provider-agnostic fallback.
        """
        try:
            pid = self._config().get("judge_provider_id") or (
                await self._context.get_current_chat_provider_id(umo)
            )
            resp = await asyncio.wait_for(
                self._context.llm_generate(
                    chat_provider_id=pid,
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                ),
                timeout=float(self._config().get("judge_timeout", 30.0)),
            )
            return resp.completion_text or ""
        except Exception as e:
            logger.info(f"goal judge call failed ({type(e).__name__}): {e}")
            return None

    def _get_blocked_tools(self) -> frozenset[str]:
        """Return the effective set of tool names to strip on continuation turns.

        The config value ``block_tools_during_goal`` may be:
        - missing / empty list  → fall back to :data:`DEFAULT_BLOCKED_TOOLS_DURING_GOAL`
        - a list of strings     → merged with the default (union, not replace),
          so users only need to *add* niche tools without losing the built-in
          safety net.
        - any other type        → ignored, default is used.

        Returns:
            A frozenset of tool names to remove from ``req.func_tool`` before
            the LLM is invoked on a synthetic continuation turn.
        """
        cfg_value = self._config().get("block_tools_during_goal", None)
        if not isinstance(cfg_value, (list, tuple)):
            return DEFAULT_BLOCKED_TOOLS_DURING_GOAL
        extras = {str(name).strip() for name in cfg_value if str(name).strip()}
        if not extras:
            return DEFAULT_BLOCKED_TOOLS_DURING_GOAL
        return DEFAULT_BLOCKED_TOOLS_DURING_GOAL | frozenset(extras)

    def _strip_blocked_tools(self, req) -> int:
        """Remove blocking tools from ``req.func_tool``.

        Only mutates the request when ``req`` and ``req.func_tool`` are
        present and non-empty; otherwise it's a no-op. The mutation is
        in-place on the tool list (uses :meth:`ToolSet.remove_tool`) so
        the LLM never sees these tools in the function-calling schema
        for the current turn.

        Args:
            req: The LLM request object passed to ``on_llm_request``.

        Returns:
            Number of tools actually removed (0 if nothing matched).
        """
        if req is None:
            return 0
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None:
            return 0
        # `ToolSet` exposes the live list at `.tools` and a `.remove_tool(name)`
        # method; both are part of the public API used elsewhere in AstrBot.
        tools_list = getattr(tool_set, "tools", None)
        if not tools_list:
            return 0
        blocked = self._get_blocked_tools()
        present = {t.name for t in tools_list if getattr(t, "name", None)}
        targets = present & blocked
        if not targets:
            return 0
        for name in targets:
            # Prefer the structured API; fall back to list comprehension so
            # this stays robust against ToolSet variants in tests/mocks.
            # Both paths use getattr(..., "name", None) so a tool object
            # without a `.name` attribute (theoretically possible if a
            # future AstrBot release ships a heterogeneous ToolSet) is
            # silently preserved rather than crashing the goal loop.
            remover = getattr(tool_set, "remove_tool", None)
            if callable(remover):
                remover(name)
            else:
                tool_set.tools = [
                    t for t in tool_set.tools if getattr(t, "name", None) != name
                ]
        logger.info(
            f"goal loop: stripped blocking tool(s) for continuation turn: "
            f"{sorted(targets)}"
        )
        return len(targets)

    async def inject_continuation(self, event: AstrMessageEvent, text: str) -> None:
        """Inject a synthetic continuation turn into the event queue.

        On webchat sessions the turn is first registered as a first-class
        chat run (via the registrar wired by the dashboard), so it renders
        and recovers through the exact primary-run pipeline instead of the
        orphan system-mirror channel.

        Args:
            event: The source event of the just-finished turn.
            text: The user-role text for the synthetic turn.
        """
        new_event = build_continuation_event(event, text)
        message_id = str(new_event.message_obj.message_id)
        registrar = self._run_registrar
        if registrar is not None:
            try:
                await registrar(
                    event.unified_msg_origin,
                    message_id,
                    uuid.uuid4().hex,
                )
            except Exception as e:
                # Registration failure must not wedge the loop: the turn
                # degrades to the orphan system-mirror rendering path.
                logger.warning(
                    f"goal loop: run registration failed for {message_id}: {e}"
                )
        self._context.get_event_queue().put_nowait(new_event)

    async def guard_continuation(self, event: AstrMessageEvent, req) -> None:
        """``on_llm_request`` hook: drop stale continuations, strip tools.

        Drop queued continuation events whose goal is no longer active and
        strip blocking tools from the LLM request on synthetic turns.

        Two responsibilities, both gated on the ``goal_continuation`` extra
        (set by :func:`build_continuation_event`):

        1. A continuation event may sit in the queue while the user pauses
           or clears the goal; re-check the active state right before the
           LLM stage and ``stop_event()`` if no longer active.
        2. Remove any tool names in the configured block list from
           ``req.func_tool`` so the Agent cannot suspend the goal loop by
           calling a tool that waits on user input. Only applies to
           *continuation* turns; normal user-driven turns are never touched.
        """
        if not event.get_extra(GOAL_CONTINUATION_EXTRA):
            return
        state = await self.goals.get(event.unified_msg_origin)
        if not state or state.status != "active":
            logger.info(
                f"goal loop: continuation event dropped before LLM stage "
                f"(goal not active), umo={event.unified_msg_origin}"
            )
            event.stop_event()
            return
        # Strip blocking tools before the LLM sees the function-calling
        # schema. Failures are logged but never break the loop — at worst
        # the Agent retains the blocking tool, which still works (it just
        # stalls the loop, same as before this guard existed).
        try:
            self._strip_blocked_tools(req)
        except Exception as e:
            logger.warning(
                f"goal loop: failed to strip blocking tools ({type(e).__name__}): {e}"
            )
        logger.info(
            f"goal loop: continuation event entering LLM stage, "
            f"umo={event.unified_msg_origin}"
        )

    async def on_turn_done(self, event: AstrMessageEvent, response) -> None:
        """``on_agent_done`` hook: judge the turn and maybe inject a continuation."""
        umo = event.unified_msg_origin
        logger.info(
            f"goal loop: on_agent_done fired, umo={umo}, "
            f"msg_id={event.message_obj.message_id}"
        )
        state = await self.goals.get(umo)
        if not state or state.status != "active":
            return

        # Dedup: guard against runners firing on_agent_done twice per run.
        run_key = f"{umo}:{event.message_obj.message_id}"
        if run_key in self._judged_runs:
            return
        self._judged_runs.add(run_key)
        if len(self._judged_runs) > 500:
            self._judged_runs = set(list(self._judged_runs)[-250:])

        # User-interrupted turn: auto-pause instead of judging partial
        # output (mirrors Hermes Ctrl+C handling; otherwise the loop feels
        # unstoppable).
        if event.get_extra("agent_stop_requested"):
            await self.goals.pause(umo, reason="user interrupted")
            await self._notify(
                event,
                "⏸ 目标已暂停（本轮被中断）。/goal resume 继续，/goal clear 停止。",
            )
            return

        text = (getattr(response, "completion_text", None) or "").strip()
        if not text:
            return  # Empty response: transient failure, skip judging.

        decision = await self.goals.evaluate_after_turn(
            umo,
            text,
            judge=lambda g, r, s: judge_goal(
                llm_caller=lambda sp_prompt, up: self._judge_llm_caller(
                    umo, sp_prompt, up
                ),
                goal=g,
                last_response=r,
                subgoals=s,
            ),
            max_parse_failures=self._config().get("max_parse_failures"),
        )

        if decision.get("message") and self._config().get("verbose", True):
            await self._notify(event, decision["message"])

        if decision.get("should_continue") and decision.get("continuation_prompt"):
            await self.inject_continuation(event, decision["continuation_prompt"])
            logger.info(
                f"goal loop: continuation injected, umo={umo}, "
                f"turns_used={state.turns_used}, reason={decision.get('reason')}"
            )

    async def migrate_legacy_states(self) -> int:
        """One-time import of goal states stored by the legacy plugin.

        Reads the plugin KV namespace ("plugin" / "astrbot_plugin_goal"),
        rewrites every record into the kernel namespace with active goals
        downgraded to paused (a goal loop never survives a process
        restart), then removes the legacy keys. Idempotent: a second run
        finds no legacy keys and returns 0.

        Returns:
            Number of migrated goal states.
        """
        if self._migrated:
            return 0
        index = await sp.get_async("plugin", "astrbot_plugin_goal", "goal:index", None)
        if not index:
            self._migrated = True
            return 0
        migrated = 0
        for umo in index:
            raw = await sp.get_async(
                "plugin", "astrbot_plugin_goal", f"goal:{umo}", None
            )
            if raw is None:
                continue
            state = dict(raw)
            if state.get("status") == "active":
                state["status"] = "paused"
                state["paused_reason"] = "migrated from goal plugin (restart)"
            await sp.put_async(KV_SCOPE, KV_SCOPE_ID, f"goal:{umo}", state)
            migrated += 1
            await sp.remove_async("plugin", "astrbot_plugin_goal", f"goal:{umo}")
        await sp.remove_async("plugin", "astrbot_plugin_goal", "goal:index")
        self._migrated = True
        logger.info(f"goal loop: migrated {migrated} legacy goal state(s)")
        return migrated


goal_service = GoalService()
