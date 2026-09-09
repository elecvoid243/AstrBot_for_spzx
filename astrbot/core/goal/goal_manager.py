# Author: elecvoid243 · Created: 2026-07-25 (ported from the astrbot_plugin_goal plugin)
"""Goal state model and per-session goal manager.

Core logic is AstrBot-free (stdlib only) so it can be unit tested without
the AstrBot SDK. Mirrors the Hermes ``hermes_cli/goals.py`` design.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from .goal_judge import build_continuation_prompt
from .goal_state import DEFAULT_MAX_TURNS, GoalState

DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES = 3

VALID_STATUS = {"active", "paused", "done", "cleared"}

JudgeFn = Callable[[str, str, list[str]], Awaitable[tuple[str, str, bool]]]
"""Async judge callable: (goal, response, subgoals) -> (verdict, reason, parse_failed)."""


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
    ) -> None:
        self._storage = storage
        self.default_max_turns = int(default_max_turns or DEFAULT_MAX_TURNS)
        self.max_parse_failures = int(
            max_parse_failures or DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES
        )

    @staticmethod
    def _key(umo: str) -> str:
        return f"goal:{umo}"

    async def _save(self, umo: str, state: GoalState) -> None:
        await self._storage.set(self._key(umo), state.to_dict())

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
        """Load the goal for a UMO, or None."""
        raw = await self._storage.get(self._key(umo))
        if not raw:
            return None
        try:
            state = GoalState.from_dict(raw)
        except Exception:
            return None
        return None if state.status == "cleared" else state

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
        state = GoalState(
            goal=goal,
            status="active",
            turns_used=0,
            max_turns=int(max_turns) if max_turns else self.default_max_turns,
            created_at=time.time(),
        )
        await self._save(umo, state)
        await self._track(umo)
        return state

    async def pause(self, umo: str, reason: str = "user-paused") -> GoalState | None:
        state = await self.get(umo)
        if state is None:
            return None
        state.status = "paused"
        state.paused_reason = reason
        await self._save(umo, state)
        return state

    async def resume(self, umo: str, *, reset_budget: bool = True) -> GoalState | None:
        state = await self.get(umo)
        if state is None:
            return None
        state.status = "active"
        state.paused_reason = None
        if reset_budget:
            state.turns_used = 0
        await self._save(umo, state)
        return state

    async def clear(self, umo: str) -> bool:
        state = await self.get(umo)
        had = state is not None
        await self._storage.delete(self._key(umo))
        await self._untrack(umo)
        return had

    async def mark_done(self, umo: str, reason: str) -> None:
        state = await self.get(umo)
        if state is None:
            return
        state.status = "done"
        state.last_verdict = "done"
        state.last_reason = reason
        await self._save(umo, state)

    async def add_subgoal(self, umo: str, text: str) -> str:
        state = await self.get(umo)
        if state is None or state.status not in {"active", "paused"}:
            raise RuntimeError("no active goal")
        text = (text or "").strip()
        if not text:
            raise ValueError("subgoal text is empty")
        state.subgoals.append(text)
        await self._save(umo, state)
        return text

    async def remove_subgoal(self, umo: str, index_1based: int) -> str:
        state = await self.get(umo)
        if state is None or state.status not in {"active", "paused"}:
            raise RuntimeError("no active goal")
        idx = int(index_1based) - 1
        if idx < 0 or idx >= len(state.subgoals):
            raise IndexError(f"index out of range (1..{len(state.subgoals)})")
        removed = state.subgoals.pop(idx)
        await self._save(umo, state)
        return removed

    async def clear_subgoals(self, umo: str) -> int:
        state = await self.get(umo)
        if state is None or state.status not in {"active", "paused"}:
            raise RuntimeError("no active goal")
        prev = len(state.subgoals)
        state.subgoals = []
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

        Args:
            umo: Unified message origin of the session.
            last_response: The agent's final assistant text for this turn.
            judge: Async callable returning (verdict, reason, parse_failed).
            max_parse_failures: Parse-failure threshold override; falls back
                to the constructor default when None or falsy.

        Returns:
            Decision dict with keys: status, should_continue,
            continuation_prompt, verdict, reason, message.
        """
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
        verdict, reason, parse_failed = await judge(
            state.goal, last_response, state.subgoals
        )
        state.last_verdict = verdict
        state.last_reason = reason
        # Only unparseable judge output counts; transport errors are transient.
        state.consecutive_parse_failures = (
            state.consecutive_parse_failures + 1 if parse_failed else 0
        )

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
