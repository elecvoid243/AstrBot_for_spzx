# Author: elecvoid243 · Created: 2026-07-25 (ported from the astrbot_plugin_goal plugin)
"""Goal state model.

Core logic is AstrBot-free (stdlib only) so it can be unit tested without
the AstrBot SDK. Mirrors the Hermes ``hermes_cli/goals.py`` design.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

DEFAULT_MAX_TURNS = 20

VALID_STATUS = {"active", "paused", "blocked", "done"}
"""Legal GoalState.status values; anything else deserializes as paused."""


@dataclass
class GoalState:
    """Serializable per-session (per-UMO) goal state."""

    goal: str
    goal_id: str = ""
    """Unique id per goal instance; distinguishes a replaced goal."""
    status: str = "active"  # active | paused | blocked | done
    epoch: int = 1
    """Bumped on control-plane mutations only; evaluation bookkeeping does not
    bump it, otherwise the CAS check after the judge call would always miss."""
    turns_used: int = 0
    max_turns: int = DEFAULT_MAX_TURNS
    created_at: float = 0.0
    last_turn_at: float = 0.0
    last_verdict: str | None = None  # "done" | "blocked" | "continue" | "skipped"
    last_reason: str | None = None
    paused_reason: str | None = None
    consecutive_parse_failures: int = 0
    consecutive_transport_failures: int = 0
    subgoals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to a plain dict for KV storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> GoalState:
        """Deserialize, tolerating missing keys and malformed subgoals.

        Legacy records predate goal_id/epoch and are backfilled; an unknown
        status is downgraded to paused so a corrupt record can never silently
        resurrect a goal loop.

        Args:
            data: Dict previously produced by :meth:`to_dict` (or partial).

        Returns:
            A GoalState with defaults filled in for missing keys.
        """
        subgoals = [
            str(s).strip()
            for s in (data.get("subgoals") or [])
            if s is not None and str(s).strip()
        ]
        # Only backfill when the key is absent: an explicit "" (e.g. a state
        # constructed before set() assigned the id) must survive round-trips.
        goal_id = data.get("goal_id")
        if goal_id is None:
            goal_id = uuid.uuid4().hex
        status = str(data.get("status", "active"))
        paused_reason = data.get("paused_reason")
        if status not in VALID_STATUS:
            status = "paused"
            paused_reason = "recovered from invalid status"
        return cls(
            goal=str(data.get("goal", "")),
            goal_id=str(goal_id),
            status=status,
            epoch=int(data.get("epoch", 1) or 1),
            turns_used=int(data.get("turns_used", 0) or 0),
            max_turns=int(
                data.get("max_turns", DEFAULT_MAX_TURNS) or DEFAULT_MAX_TURNS
            ),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            last_turn_at=float(data.get("last_turn_at", 0.0) or 0.0),
            last_verdict=data.get("last_verdict"),
            last_reason=data.get("last_reason"),
            paused_reason=paused_reason,
            consecutive_parse_failures=int(
                data.get("consecutive_parse_failures", 0) or 0
            ),
            consecutive_transport_failures=int(
                data.get("consecutive_transport_failures", 0) or 0
            ),
            subgoals=subgoals,
        )

    def to_public_dict(self) -> dict:
        """Serialize for the dashboard API and goal_state_changed push events."""
        return {
            "goal": self.goal,
            "goal_id": self.goal_id,
            "status": self.status,
            "turns_used": self.turns_used,
            "max_turns": self.max_turns,
            "subgoals": list(self.subgoals),
            "last_verdict": self.last_verdict,
            "last_reason": self.last_reason,
            "paused_reason": self.paused_reason,
            "created_at": datetime.fromtimestamp(
                self.created_at, tz=timezone.utc
            ).isoformat()
            if self.created_at
            else None,
        }

    def render_subgoals_block(self) -> str:
        """Render subgoals as a numbered ``- N. text`` block (empty if none)."""
        if not self.subgoals:
            return ""
        return "\n".join(f"- {i}. {t}" for i, t in enumerate(self.subgoals, 1))

    def status_line(self) -> str:
        """One-line human-readable status for /goal status output."""
        turns = f"{self.turns_used}/{self.max_turns} turns"
        sub = f", {len(self.subgoals)} subgoal(s)" if self.subgoals else ""
        if self.status == "active":
            return f"⊙ 目标（进行中，{turns}{sub}）：{self.goal}"
        if self.status == "paused":
            extra = f" — {self.paused_reason}" if self.paused_reason else ""
            return f"⏸ 目标（已暂停，{turns}{sub}{extra}）：{self.goal}"
        if self.status == "done":
            return f"✓ 目标已完成（{turns}{sub}）：{self.goal}"
        return f"目标（{self.status}，{turns}{sub}）：{self.goal}"
