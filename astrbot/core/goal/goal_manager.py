# Author: elecvoid243 · Created: 2026-07-25 (ported from the astrbot_plugin_goal plugin)
"""Goal state model and per-session goal manager.

Core logic is AstrBot-free (stdlib only) so it can be unit tested without
the AstrBot SDK. Mirrors the Hermes ``hermes_cli/goals.py`` design.

Concurrency protocol (state machine v2): every control-plane mutation runs
under a per-UMO lock and bumps ``state.epoch``. ``evaluate_after_turn``
charges the turn under the lock, runs the judge OUTSIDE the lock, then
re-checks ``(goal_id, epoch)`` before applying the verdict — a verdict
computed against a cleared/paused/replaced goal is discarded instead of
resurrecting stale state.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from .goal_judge import build_continuation_prompt
from .goal_state import DEFAULT_MAX_TURNS, GoalState

DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES = 3
MAX_CONSECUTIVE_TRANSPORT_FAILURES = 2

JudgeFn = Callable[[str, str, list[str]], Awaitable[tuple[str, str, bool, bool]]]
"""Async judge callable: (goal, response, subgoals) ->
(verdict, reason, parse_failed, transport_failed)."""

INDEX_KEY = "goal:index"


class KVStorage(Protocol):
    """Async key-value storage contract (wired to the shared preferences KV)."""

    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: Any) -> None: ...
    async def delete(self, key: str) -> None: ...


class GoalManager:
    """Per-UMO goal state CRUD backed by KV storage."""

    def __init__(
        self,
        storage: KVStorage,
        *,
        default_max_turns: int = DEFAULT_MAX_TURNS,
        max_parse_failures: int = DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES,
        on_change: Callable[[str, GoalState | None], Awaitable[None]] | None = None,
    ) -> None:
        self._storage = storage
        self.default_max_turns = int(default_max_turns or DEFAULT_MAX_TURNS)
        self.max_parse_failures = int(
            max_parse_failures or DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES
        )
        self._on_change = on_change
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, umo: str) -> asyncio.Lock:
        if umo not in self._locks:
            self._locks[umo] = asyncio.Lock()
        return self._locks[umo]

    @staticmethod
    def _key(umo: str) -> str:
        return f"goal:{umo}"

    async def _save(self, umo: str, state: GoalState) -> None:
        await self._storage.set(self._key(umo), state.to_dict())
        if self._on_change:
            await self._on_change(umo, state)

    async def _track(self, umo: str) -> None:
        index = await self._storage.get(INDEX_KEY) or []
        if umo not in index:
            index.append(umo)
            await self._storage.set(INDEX_KEY, index)

    async def _untrack(self, umo: str) -> None:
        index = await self._storage.get(INDEX_KEY) or []
        if umo in index:
            index.remove(umo)
            await self._storage.set(INDEX_KEY, index)

    async def list_tracked(self) -> list[str]:
        """Return UMOs with a non-cleared goal record (for restart recovery)."""
        return list(await self._storage.get(INDEX_KEY) or [])

    async def get(self, umo: str) -> GoalState | None:
        """Load the goal for a UMO, or None. Read-only, intentionally lock-free."""
        raw = await self._storage.get(self._key(umo))
        if not raw:
            return None
        if isinstance(raw, dict) and raw.get("status") == "cleared":
            # Legacy tombstone value; "cleared" is not a v2 status and would
            # otherwise be downgraded to paused by GoalState.from_dict.
            return None
        try:
            state = GoalState.from_dict(raw)
        except Exception:
            return None
        return state

    async def set(
        self, umo: str, goal: str, *, max_turns: int | None = None
    ) -> GoalState:
        """Start a new standing goal for a UMO.

        Raises:
            ValueError: If the goal text is empty.
        """
        goal = (goal or "").strip()
        if not goal:
            raise ValueError("goal text is empty")
        async with self._lock_for(umo):
            state = GoalState(
                goal=goal,
                goal_id=uuid.uuid4().hex,
                status="active",
                epoch=1,
                turns_used=0,
                max_turns=int(max_turns) if max_turns else self.default_max_turns,
                created_at=time.time(),
            )
            await self._save(umo, state)
            await self._track(umo)
            return state

    async def pause(self, umo: str, reason: str = "user-paused") -> GoalState | None:
        async with self._lock_for(umo):
            state = await self.get(umo)
            if state is None:
                return None
            state.status = "paused"
            state.paused_reason = reason
            state.epoch += 1
            await self._save(umo, state)
            return state

    async def resume(self, umo: str, *, reset_budget: bool = True) -> GoalState | None:
        """Resume a paused/blocked goal; returns None for any other status."""
        async with self._lock_for(umo):
            state = await self.get(umo)
            if state is None or state.status not in {"paused", "blocked"}:
                return None
            state.status = "active"
            state.paused_reason = None
            state.epoch += 1
            if reset_budget:
                state.turns_used = 0
            await self._save(umo, state)
            return state

    async def clear(self, umo: str) -> bool:
        async with self._lock_for(umo):
            state = await self.get(umo)
            had = state is not None
            await self._storage.delete(self._key(umo))
            await self._untrack(umo)
            if had and self._on_change:
                await self._on_change(umo, None)
            return had

    async def add_subgoal(self, umo: str, text: str) -> str:
        async with self._lock_for(umo):
            state = await self.get(umo)
            if state is None or state.status not in {"active", "paused", "blocked"}:
                raise RuntimeError("no active goal")
            text = (text or "").strip()
            if not text:
                raise ValueError("subgoal text is empty")
            state.subgoals.append(text)
            state.epoch += 1
            await self._save(umo, state)
            return text

    async def remove_subgoal(self, umo: str, index_1based: int) -> str:
        async with self._lock_for(umo):
            state = await self.get(umo)
            if state is None or state.status not in {"active", "paused", "blocked"}:
                raise RuntimeError("no active goal")
            idx = int(index_1based) - 1
            if idx < 0 or idx >= len(state.subgoals):
                raise IndexError(f"index out of range (1..{len(state.subgoals)})")
            removed = state.subgoals.pop(idx)
            state.epoch += 1
            await self._save(umo, state)
            return removed

    async def clear_subgoals(self, umo: str) -> int:
        async with self._lock_for(umo):
            state = await self.get(umo)
            if state is None or state.status not in {"active", "paused", "blocked"}:
                raise RuntimeError("no active goal")
            prev = len(state.subgoals)
            state.subgoals = []
            state.epoch += 1
            await self._save(umo, state)
            return prev

    async def evaluate_after_turn(
        self,
        umo: str,
        last_response: str,
        judge: JudgeFn,
        max_parse_failures: int | None = None,
    ) -> dict:
        """Run the judge after a finished turn and decide what happens next.

        Three-phase CAS protocol: charge the turn under the per-UMO lock,
        await the judge outside the lock, then apply the verdict only when
        the state is still the same goal generation.

        Args:
            umo: Unified message origin of the session.
            last_response: The agent's final assistant text for this turn.
            judge: Async callable returning (verdict, reason, parse_failed,
                transport_failed).
            max_parse_failures: Parse-failure threshold override; falls back
                to the constructor default when None or falsy.

        Returns:
            Decision dict with keys: status, should_continue,
            continuation_prompt, verdict, reason, message.
        """
        lock = self._lock_for(umo)
        async with lock:
            state = await self.get(umo)
            if state is None or state.status != "active":
                return {
                    "status": state.status if state else None,
                    "should_continue": False,
                    "continuation_prompt": None,
                    "verdict": "inactive",
                    "reason": "no active goal",
                    "message": "",
                }
            state.turns_used += 1
            state.last_turn_at = time.time()
            await self._save(umo, state)
            snapshot = (state.goal_id, state.epoch)
            goal, subgoals = state.goal, list(state.subgoals)

        # The judge call may take seconds; never hold the lock across it, or
        # /goal pause would block for the whole judge timeout.
        verdict, reason, parse_failed, transport_failed = await judge(
            goal, last_response, subgoals
        )

        async with lock:
            state = await self.get(umo)
            if (
                state is None
                or state.status != "active"
                or (state.goal_id, state.epoch) != snapshot
            ):
                # The goal was cleared/paused/replaced while the judge was in
                # flight: discard the verdict instead of resurrecting state.
                return {
                    "status": "stale",
                    "should_continue": False,
                    "continuation_prompt": None,
                    "verdict": "stale",
                    "reason": "state changed during evaluation",
                    "message": "",
                }

            state.last_verdict = verdict
            state.last_reason = reason
            # Parse and transport failures are distinct failure classes; a
            # success of either kind resets the other's streak.
            if parse_failed:
                state.consecutive_parse_failures += 1
                state.consecutive_transport_failures = 0
            elif transport_failed:
                state.consecutive_transport_failures += 1
                state.consecutive_parse_failures = 0
            else:
                state.consecutive_parse_failures = 0
                state.consecutive_transport_failures = 0

            parse_limit = int(max_parse_failures or self.max_parse_failures)

            if verdict == "done":
                state.status = "done"
                await self._save(umo, state)
                return {
                    "status": "done",
                    "should_continue": False,
                    "continuation_prompt": None,
                    "verdict": "done",
                    "reason": reason,
                    "message": f"✓ 目标达成：{reason}",
                }

            if verdict == "blocked":
                state.status = "blocked"
                await self._save(umo, state)
                return {
                    "status": "blocked",
                    "should_continue": False,
                    "continuation_prompt": None,
                    "verdict": "blocked",
                    "reason": reason,
                    "message": (
                        f"⏸ 目标被阻塞：{reason}。处理后可 /goal resume 继续。"
                    ),
                }

            if (
                state.consecutive_transport_failures
                >= MAX_CONSECUTIVE_TRANSPORT_FAILURES
            ):
                state.status = "paused"
                state.paused_reason = "judge 连续调用失败（传输错误）"
                await self._save(umo, state)
                return {
                    "status": "paused",
                    "should_continue": False,
                    "continuation_prompt": None,
                    "verdict": "continue",
                    "reason": reason,
                    "message": (
                        "⏸ 目标已暂停：judge 模型连续调用失败（传输错误）。"
                        "请检查 judge provider 可用性后 /goal resume 继续。"
                    ),
                }

            if state.consecutive_parse_failures >= parse_limit:
                state.status = "paused"
                state.paused_reason = (
                    f"judge 输出连续 {state.consecutive_parse_failures} 轮无法解析"
                )
                await self._save(umo, state)
                return {
                    "status": "paused",
                    "should_continue": False,
                    "continuation_prompt": None,
                    "verdict": "continue",
                    "reason": reason,
                    "message": (
                        f"⏸ 目标已暂停：judge 模型连续 {state.consecutive_parse_failures} 轮"
                        "未返回合法 JSON verdict。请在管理面板「系统配置 → 目标循环」中为 "
                        "judge 指定一个能严格遵守输出格式的模型，然后 /goal resume 继续。"
                    ),
                }

            if state.turns_used >= state.max_turns:
                state.status = "paused"
                state.paused_reason = (
                    f"轮次预算耗尽（{state.turns_used}/{state.max_turns}）"
                )
                await self._save(umo, state)
                return {
                    "status": "paused",
                    "should_continue": False,
                    "continuation_prompt": None,
                    "verdict": "continue",
                    "reason": reason,
                    "message": (
                        f"⏸ 目标已暂停：已用 {state.turns_used}/{state.max_turns} 轮预算。"
                        "/goal resume 继续（重置预算），/goal clear 停止。"
                    ),
                }

            await self._save(umo, state)
            return {
                "status": "active",
                "should_continue": True,
                "continuation_prompt": build_continuation_prompt(
                    state.goal, state.subgoals
                ),
                "verdict": "continue",
                "reason": reason,
                "message": f"↻ 继续推进目标（{state.turns_used}/{state.max_turns}）：{reason}",
            }
