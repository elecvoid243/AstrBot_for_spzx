"""Agent Teams run lifecycle: event bus, DAGRunner, AutoOrchestrator, run service (spec §6.3/§6.4/§6.5/§6.6)."""

import asyncio
import time
import uuid
from collections.abc import Callable

from astrbot import logger
from astrbot.core.agent_team_tools import AgentTeamToolRegistry, build_team_tools
from astrbot.dashboard.services.agent_team_dag import (
    TeamDAGError,
    downstream_of,
    render_task,
)
from astrbot.dashboard.services.agent_team_ports import TeamPorts, build_ports
from astrbot.dashboard.services.agent_team_service import (
    DEFAULT_TEAM_CONFIG,
    AgentTeamsServiceError,
    _team_to_dict,
)

TERMINAL_RUN_STATUSES = ("completed", "stopped", "failed")


def _run_to_dict(row) -> dict:
    return {
        "run_id": row.run_id,
        "team_id": row.team_id,
        "workflow_id": row.workflow_id,
        "mode": row.mode,
        "input": row.input,
        "status": row.status,
        "result_summary": row.result_summary,
        "graph": row.graph_snapshot,
        "node_states": row.node_states,
        "rounds": row.rounds,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class RunEventBus:
    """Per-run in-memory event history plus live subscriber fan-out.

    Mirrors the agent-collab SSE multiplexer pattern: late subscribers get a
    full history replay from the SSE layer, live events flow through
    per-subscriber bounded queues with drop-oldest on slow consumers.
    """

    _SUB_QUEUE_MAX = 256

    def __init__(self) -> None:
        self._history: list[dict] = []
        self._subscribers: list[asyncio.Queue] = []

    def emit(self, event: dict) -> None:
        """Record an event and fan it out to all subscribers."""
        self._history.append(event)
        for queue in list(self._subscribers):
            if queue.qsize() >= self._SUB_QUEUE_MAX:
                try:
                    queue.get_nowait()  # drop-oldest
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    def subscribe(self) -> asyncio.Queue:
        """Register a live subscriber queue (post-subscription events only)."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._SUB_QUEUE_MAX)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Remove a previously registered subscriber queue."""
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def history(self) -> list[dict]:
        """Return a defensive copy of all emitted events."""
        return list(self._history)


class DAGRunner:
    """Wave-scheduled DAG execution over TeamPorts (spec §6.3).

    The runner owns one run row; every node transition is persisted
    immediately so an interrupted process can resume (Task 9). `run()` is
    re-callable after `retry_node`/`resume`.
    """

    def __init__(
        self,
        *,
        run_id: str,
        team_id: str,
        graph: dict,
        config: dict,
        members: list[dict],
        ports: TeamPorts,
        db,
        bus: RunEventBus,
        username: str,
        run_input: str = "",
        node_states: dict[str, dict] | None = None,
        on_member_stop: Callable[[str], object] | None = None,
        workflow_id: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.team_id = team_id
        self.graph = graph
        self.workflow_id = workflow_id
        self.config = {**DEFAULT_TEAM_CONFIG, **(config or {})}
        self.members = members
        self.ports = ports
        self.db = db
        self.bus = bus
        self.username = username
        self.run_input = run_input
        self.on_member_stop = on_member_stop
        self.status = "running"
        self.node_states: dict[str, dict] = dict(node_states or {})
        # Nodes absent from the provided states (e.g. a runner built without
        # persisted states) start as pending so the graph executes fully.
        for node in graph.get("nodes", []):
            if node["id"] not in self.node_states:
                self.node_states[node["id"]] = {
                    "status": "pending",
                    "member_id": node["member_id"],
                    "task_rendered": None,
                    "result": None,
                    "error": None,
                    "started_at": None,
                    "finished_at": None,
                }
        self.task: asyncio.Task | None = None
        self._resume_wake = asyncio.Event()
        self._stop_requested = asyncio.Event()
        self._results: dict[str, str] = {}
        # Resumed runs: results preserved from persisted done nodes feed
        # placeholder rendering without re-executing those nodes (spec §6.5
        # redo semantics). A no-op for fresh runs (all states pending).
        for node_id, state in self.node_states.items():
            if state["status"] == "done" and state.get("result"):
                self._results[node_id] = state["result"]
        self._preds: dict[str, list[str]] = {}
        for edge in graph.get("edges", []):
            src, dst = str(edge["from"]), str(edge["to"])
            self._preds.setdefault(dst, []).append(src)

    def _member_by_id(self, member_id: str) -> dict | None:
        return next((m for m in self.members if m["member_id"] == member_id), None)

    def _emit(self, event: dict) -> None:
        self.bus.emit({"ts": time.time(), **event})

    async def _persist(self) -> None:
        """Flush status + node_states to the run row (per-transition)."""
        await self.db.update_agent_team_run(
            self.run_id, status=self.status, node_states=self.node_states
        )

    def _progress(self) -> dict:
        counts: dict[str, int] = {}
        for state in self.node_states.values():
            counts[state["status"]] = counts.get(state["status"], 0) + 1
        return {
            "type": "dag_progress",
            "done": counts.get("done", 0),
            "running": counts.get("running", 0),
            "pending": counts.get("pending", 0),
            "skipped": counts.get("skipped", 0),
            "failed": counts.get("failed", 0),
            "total": len(self.node_states),
        }

    def snapshot(self) -> dict:
        """Return a JSON-safe summary for API and SSE consumers.

        `graph` and `workflow_id` let dashboard consumers (active-run monitor
        recovery, history handoff) rebuild the DAG view without extra lookups.
        """
        return {
            "run_id": self.run_id,
            "team_id": self.team_id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "node_states": self.node_states,
            "graph": self.graph,
            "progress": {k: v for k, v in self._progress().items() if k != "type"},
        }

    # ---------- controls ----------

    def pause(self) -> None:
        """Halt scheduling; in-flight nodes settle, then run() returns."""
        if self.status == "running":
            self.status = "paused"

    def resume(self) -> None:
        if self.status == "paused":
            self.status = "running"
            self._resume_wake.set()

    def request_stop(self) -> None:
        self._stop_requested.set()
        self._resume_wake.set()

    async def retry_node(self, node_id: str) -> None:
        """Re-queue a failed node and resume the run (spec §6.3).

        Args:
            node_id: The failed node to re-execute.

        Raises:
            AgentTeamsServiceError: If the node is not in `failed` state.
        """
        state = self.node_states.get(node_id)
        if state is None or state["status"] != "failed":
            raise AgentTeamsServiceError(f"节点 {node_id} 不可重试")
        state.update(
            status="pending",
            error=None,
            result=None,
            started_at=None,
            finished_at=None,
        )
        await self._persist()
        self.resume()

    async def skip_node(self, node_id: str) -> None:
        """Mark a failed node skipped and cascade-skip pending downstream.

        Args:
            node_id: The failed node to skip.

        Raises:
            AgentTeamsServiceError: If the node is not in `failed` state.
        """
        state = self.node_states.get(node_id)
        if state is None or state["status"] != "failed":
            raise AgentTeamsServiceError(f"节点 {node_id} 不可跳过")
        state["status"] = "skipped"
        for downstream in downstream_of(node_id, self.graph.get("edges", [])):
            if self.node_states[downstream]["status"] == "pending":
                self.node_states[downstream]["status"] = "skipped"
        await self._persist()
        self._emit(self._progress())
        self.resume()

    # ---------- node execution ----------

    async def _wait_if_busy(self, session_id: str) -> None:
        while self.ports.is_busy(session_id) and self.status == "running":
            self._emit({"type": "busy", "session_id": session_id})
            await asyncio.sleep(self.ports.busy_poll_interval)

    async def _execute_node(self, node: dict) -> None:
        """Deliver one node's task to its member and collect the reply."""
        node_id = node["id"]
        member = self._member_by_id(node["member_id"])
        state = self.node_states[node_id]
        if member is None:
            state.update(status="failed", error=f"member {node['member_id']} missing")
            await self._persist()
            self._emit({"type": "node_status", "node_id": node_id, "status": "failed"})
            self._emit(self._progress())
            return
        try:
            task_text = render_task(
                node.get("task", ""),
                self.run_input,
                self._results,
                int(self.config["inject_max_length"]),
            )
        except TeamDAGError as e:
            state.update(status="failed", error=str(e))
            await self._persist()
            self._emit(
                {
                    "type": "node_status",
                    "node_id": node_id,
                    "status": "failed",
                    "error": str(e),
                }
            )
            self._emit(self._progress())
            return

        state.update(
            status="running",
            task_rendered=task_text,
            started_at=time.time(),
            error=None,
        )
        await self._persist()
        self._emit(
            {
                "type": "node_status",
                "node_id": node_id,
                "member_id": member["member_id"],
                "status": "running",
            }
        )
        self._emit(self._progress())

        await self._wait_if_busy(member["session_id"])
        if self.status != "running":
            # paused/stop raced the delivery: put the node back to pending
            state.update(status="pending", started_at=None)
            await self._persist()
            return
        try:
            # deliver stays inside the try so a delivery failure fails the
            # node instead of escaping gather while sibling wave coroutines
            # keep persisting transitions after the run went terminal.
            message_id = await self.ports.deliver(member["session_id"], task_text, None)
            self._emit(
                {
                    "type": "message",
                    "direction": "sent",
                    "member_id": member["member_id"],
                    "session_id": member["session_id"],
                    "text": task_text,
                }
            )
            reply, _parts = await asyncio.wait_for(
                self.ports.collect(
                    member["session_id"], message_id, member["member_id"]
                ),
                timeout=float(self.config["reply_timeout"]),
            )
        except asyncio.TimeoutError:
            state.update(
                status="failed", error="reply timeout", finished_at=time.time()
            )
        except Exception as e:  # noqa: BLE001
            state.update(status="failed", error=str(e), finished_at=time.time())
        else:
            state.update(status="done", result=reply, finished_at=time.time())
            self._results[node_id] = reply
            self._emit(
                {
                    "type": "message",
                    "direction": "reply",
                    "member_id": member["member_id"],
                    "session_id": member["session_id"],
                    "text": reply,
                }
            )
        await self._persist()
        self._emit(
            {
                "type": "node_status",
                "node_id": node_id,
                "member_id": member["member_id"],
                "status": state["status"],
                "error": state.get("error"),
            }
        )
        self._emit(self._progress())

    # ---------- main loop ----------

    async def run(self) -> None:
        """Execute pending layers until completion, pause, or stop.

        Never raises: a crash lands the run on terminal `failed` so the UI
        never sticks on a running state.
        """
        try:
            try:
                await self._ensure_row()
                await self._run_loop()
            except Exception as exc:  # noqa: BLE001
                logger.exception("agent team run %s crashed", self.run_id)
                self.status = "failed"
                await self._persist()
                self._emit({"type": "stopped", "reason": f"runner error: {exc}"})
                return
            if self.status == "completed":
                self._emit({"type": "stopped", "reason": "done"})
            elif self.status == "stopped":
                self._emit({"type": "stopped", "reason": "user stop"})
        finally:
            # Terminal runs release the ports' resources (e.g. per-conversation
            # system-event subscriptions). A paused run keeps them: run() is
            # re-callable after retry/skip/resume and still needs the ports.
            if self.status in TERMINAL_RUN_STATUSES and self.ports.close is not None:
                try:
                    await self.ports.close()
                except Exception:  # noqa: BLE001
                    # Cleanup must never mask the run outcome.
                    logger.exception(
                        "agent team run %s: ports close failed", self.run_id
                    )

    async def _ensure_row(self) -> None:
        """Create the run row if it does not exist yet.

        The service path persists the row before starting the runner; a
        directly-constructed runner (tests, embedded usage) still gets one so
        every later transition is actually persisted.
        """
        if await self.db.get_agent_team_run(self.run_id) is not None:
            return
        await self.db.create_agent_team_run(
            run_id=self.run_id,
            team_id=self.team_id,
            workflow_id=None,
            mode="dag",
            input=self.run_input,
            status=self.status,
            graph_snapshot=self.graph,
            node_states=self.node_states,
            rounds=[],
        )

    async def _run_loop(self) -> None:
        nodes_by_id = {n["id"]: n for n in self.graph.get("nodes", [])}
        while True:
            if self._stop_requested.is_set():
                self.status = "stopped"
                await self._persist()
                return
            ready = sorted(
                node_id
                for node_id, state in self.node_states.items()
                if state["status"] == "pending"
                and all(
                    self.node_states[p]["status"] in ("done", "skipped")
                    for p in self._preds.get(node_id, [])
                )
            )
            if not ready:
                break
            if self.status == "paused":
                self._resume_wake.clear()
                if self.status == "paused":
                    # resume() may have fired between the check and the clear
                    # above; re-check so the wake signal is never erased.
                    await self._persist()
                    self._emit({"type": "paused", "reason": "user pause"})
                    await self._resume_wake.wait()
                continue
            wave = [nodes_by_id[n] for n in ready[: int(self.config["max_parallel"])]]
            await asyncio.gather(*(self._execute_node(n) for n in wave))
            await self._apply_failure_policy()

        if all(s["status"] in ("done", "skipped") for s in self.node_states.values()):
            self.status = "completed"
            done = [
                s.get("result") or ""
                for s in self.node_states.values()
                if s["status"] == "done"
            ]
            await self.db.update_agent_team_run(
                self.run_id,
                status=self.status,
                result_summary="\n\n".join(done)[:2000],
            )
        else:
            if self.status == "running":
                # A bare resume() re-entered the loop but blocked nodes
                # remain; re-park on paused instead of persisting a zombie
                # "running" row with no live task behind it.
                self.status = "paused"
            await self._persist()

    async def _apply_failure_policy(self) -> None:
        """After each wave, apply the team failure policy to failed nodes."""
        failed = [n for n, s in self.node_states.items() if s["status"] == "failed"]
        if not failed:
            return
        if self.config["failure_policy"] == "auto_skip":
            for node_id in failed:
                self.node_states[node_id]["status"] = "skipped"
                for downstream in downstream_of(node_id, self.graph.get("edges", [])):
                    if self.node_states[downstream]["status"] == "pending":
                        self.node_states[downstream]["status"] = "skipped"
            await self._persist()
            self._emit(self._progress())
        else:
            self.status = "paused"
            await self._persist()
            self._emit(
                {
                    "type": "paused",
                    "reason": "node failed",
                    "node_id": failed[0],
                }
            )


class AutoOrchestrator:
    """Rounds-based coordinator engine for auto mode (spec §6.4).

    Each round delivers the goal/digest to the coordinator with the
    `team_dispatch`/`team_finish` tools registered for exactly that turn's
    duration; a dispatch spawns a concurrent member wave, `team_finish`
    completes the run. Everything is persisted in `rounds` (no DAG nodes), so
    an interrupted run resumes by rebuilding from the row.
    """

    def __init__(
        self,
        *,
        run_id: str,
        team_id: str,
        team_name: str,
        members: list[dict],
        coordinator: dict,
        config: dict,
        run_input: str,
        ports: TeamPorts,
        db,
        bus: RunEventBus,
        username: str,
        rounds: list[dict] | None = None,
        on_member_stop: Callable[[str], object] | None = None,
    ) -> None:
        self.run_id = run_id
        self.team_id = team_id
        self.team_name = team_name
        self.members = members
        self.coordinator = coordinator
        self.config = {**DEFAULT_TEAM_CONFIG, **(config or {})}
        self.run_input = run_input
        self.ports = ports
        self.db = db
        self.bus = bus
        self.username = username
        # Round records carried over from a previous process on resume; the
        # next round number is len(self.rounds) + 1.
        self.rounds: list[dict] = list(rounds or [])
        self.on_member_stop = on_member_stop
        self.status = "running"
        self.task: asyncio.Task | None = None
        # Set by the coordinator tool callbacks for the turn in flight; the
        # dispatch handling after the turn consumes exactly one of them.
        self._pending_dispatch: list | None = None
        self._pending_notes: str | None = None
        self._finish: dict | None = None
        self._no_tool_rounds = 0
        # One-shot: set by resume_run's rebuild so the first resumed turn
        # carries the stronger no-dispatch reminder (the counter itself
        # starts clean, so the regular reminder cannot fire).
        self._resume_reminder = False
        self._resume_wake = asyncio.Event()
        self._stop_requested = asyncio.Event()

    def _emit(self, event: dict) -> None:
        self.bus.emit({"ts": time.time(), **event})

    async def _persist(self) -> None:
        """Flush status + rounds to the run row (per round and per wave)."""
        await self.db.update_agent_team_run(
            self.run_id, status=self.status, rounds=self.rounds
        )

    def snapshot(self) -> dict:
        """Return a JSON-safe summary for API and SSE consumers."""
        return {
            "run_id": self.run_id,
            "team_id": self.team_id,
            "workflow_id": None,
            "mode": "auto",
            "status": self.status,
            "rounds": self.rounds,
            "progress": {
                "round": len(self.rounds),
                "max_rounds": int(self.config["max_rounds"]),
            },
        }

    # ---------- controls ----------

    def pause(self) -> None:
        """Halt scheduling; the in-flight phase settles, then run() returns."""
        if self.status == "running":
            self.status = "paused"

    def resume(self) -> None:
        if self.status == "paused":
            self.status = "running"
            self._resume_wake.set()

    def request_stop(self) -> None:
        self._stop_requested.set()
        self._resume_wake.set()

    # ---------- coordinator tool callbacks ----------

    async def _on_dispatch(self, assignments: list[dict[str, str]], notes) -> None:
        self._pending_dispatch = assignments
        self._pending_notes = notes

    async def _on_finish(self, summary: str) -> None:
        self._finish = {"summary": summary}

    # ---------- coordinator context ----------

    def _results_digest(self) -> str:
        """Digest of prior rounds: per member the latest result (~500 chars),
        plus the coordinator's last notes so it can carry its own hints."""
        latest: dict[str, str] = {}
        notes = ""
        for entry in self.rounds:
            if entry.get("notes"):
                notes = str(entry["notes"])
            for item in entry.get("results", []):
                name = str(item.get("member") or "?")
                if "result" in item:
                    latest[name] = str(item["result"])[:500]
                elif "error" in item:
                    latest[name] = f"[错误] {item['error']}"[:500]
        lines = [f"{name}: {text}" for name, text in latest.items()]
        if notes:
            lines.append(f"协调者备注：{notes[:500]}")
        return "\n".join(lines)

    def _coordinator_context(self, n: int) -> str:
        """Build the coordinator turn body: roster, goal/digest, instructions."""
        max_rounds = int(self.config["max_rounds"])
        roster = "\n".join(
            f"- {m['name']}: {m.get('persona_id') or '自定义成员'}"
            for m in self.members
        )
        if n <= 1:
            task_block = f"团队目标：\n{self.run_input}"
        else:
            digest = self._results_digest()
            task_block = (
                f"团队目标：\n{self.run_input}\n\n"
                f"各成员最新结果：\n{digest or '（暂无）'}"
            )
        reminder = ""
        if self._no_tool_rounds > 0:
            reminder = (
                "\n\nReminder: your previous turn did not call team_dispatch "
                "or team_finish. Act now: either dispatch this round's tasks "
                "with team_dispatch, or end the run with team_finish."
            )
        # One-shot stronger reminder for a resumed no-dispatch pause (the
        # counter-based reminder above cannot fire right after the rebuild).
        resume_reminder = ""
        if self._resume_reminder:
            self._resume_reminder = False
            resume_reminder = (
                "\n\nYou have previously completed two rounds without "
                "dispatching any tasks. You MUST call team_dispatch with "
                "concrete assignments this round, or the run will be paused "
                "again."
            )
        return (
            f"你是团队「{self.team_name}」的协调者（第 {n}/{max_rounds} 轮）。\n"
            f"团队成员：\n{roster}\n\n{task_block}\n\n"
            "Instructions: call the team_dispatch tool once with THIS round's "
            "assignments (one {member, task} pair per member task, using exact "
            "member names). When the overall goal is fully achieved and all "
            "member results are in, call team_finish with a final summary to "
            "end the run." + reminder + resume_reminder
        )

    async def _wait_if_busy(self, session_id: str) -> None:
        while self.ports.is_busy(session_id) and self.status == "running":
            self._emit({"type": "busy", "session_id": session_id})
            await asyncio.sleep(self.ports.busy_poll_interval)

    # ---------- phases ----------

    async def _coordinator_turn(self, n: int) -> str:
        """Deliver one coordinator turn with the team tools registered.

        Returns:
            "ok" when the turn completed, "timeout" when the reply timed out
            (the run was paused and persisted here), or "skipped" when the run
            was paused/stopped before the turn's I/O started.

        The registry entry lives exactly for this turn: registered before the
        round event, unregistered in `finally` after collect returns.
        """
        body = self._coordinator_context(n)
        tools = build_team_tools(
            [m["name"] for m in self.members],
            on_dispatch=self._on_dispatch,
            on_finish=self._on_finish,
        )
        umo = self.coordinator["umo"]
        AgentTeamToolRegistry.register(umo, tools)
        try:
            self._emit(
                {"type": "round", "n": n, "max_rounds": int(self.config["max_rounds"])}
            )
            self._pending_dispatch = None
            self._pending_notes = None
            self._finish = None
            await self._wait_if_busy(self.coordinator["session_id"])
            if self.status != "running" or self._stop_requested.is_set():
                return "skipped"
            session_id = self.coordinator["session_id"]
            member_id = self.coordinator["member_id"]

            async def _turn_io() -> None:
                message_id = await self.ports.deliver(session_id, body, None)
                self._emit(
                    {
                        "type": "message",
                        "direction": "sent",
                        "member_id": member_id,
                        "session_id": session_id,
                        "text": body,
                    }
                )
                reply, _parts = await self.ports.collect(
                    session_id, message_id, member_id
                )
                self._emit(
                    {
                        "type": "message",
                        "direction": "reply",
                        "member_id": member_id,
                        "session_id": session_id,
                        "text": reply,
                    }
                )

            try:
                await asyncio.wait_for(
                    _turn_io(), timeout=float(self.config["reply_timeout"])
                )
            except asyncio.TimeoutError:
                self.status = "paused"
                await self._persist()
                self._emit({"type": "paused", "reason": "coordinator timeout"})
                return "timeout"
            return "ok"
        finally:
            AgentTeamToolRegistry.unregister(umo)

    async def _member_wave(self, n: int, assignments: list[dict]) -> None:
        """Deliver one assignment per member concurrently and record results."""
        semaphore = asyncio.Semaphore(int(self.config["max_parallel"]))
        by_name = {m["name"].strip().casefold(): m for m in self.members}
        context = f"[团队任务] 来自协调者（第 {n} 轮）"

        async def run_one(assignment: dict) -> dict:
            name = str(assignment.get("member") or "").strip()
            member = by_name.get(name.casefold())
            if member is None:
                return {"member": name, "error": "unknown member"}
            # Cap concurrent member turns; unknown members skip the I/O and
            # never need a slot.
            async with semaphore:
                try:
                    task = str(assignment.get("task") or "")
                    message_id = await self.ports.deliver(
                        member["session_id"], task, context
                    )
                    self._emit(
                        {
                            "type": "message",
                            "direction": "sent",
                            "member_id": member["member_id"],
                            "session_id": member["session_id"],
                            "text": task,
                        }
                    )
                    reply, _parts = await asyncio.wait_for(
                        self.ports.collect(
                            member["session_id"], message_id, member["member_id"]
                        ),
                        timeout=float(self.config["reply_timeout"]),
                    )
                except asyncio.TimeoutError:
                    return {"member": name, "error": "reply timeout"}
                except Exception as e:  # noqa: BLE001
                    return {"member": name, "error": str(e)}
                self._emit(
                    {
                        "type": "message",
                        "direction": "reply",
                        "member_id": member["member_id"],
                        "session_id": member["session_id"],
                        "text": reply,
                    }
                )
                return {"member": name, "result": reply}

        results = await asyncio.gather(*(run_one(a) for a in assignments))
        self.rounds[-1]["results"] = list(results)

    # ---------- main loop ----------

    async def run(self) -> None:
        """Drive rounds until finish, pause, or stop.

        Never raises: a crash lands the run on terminal `failed` so the UI
        never sticks on a running state.
        """
        try:
            try:
                await self._ensure_row()
                await self._run_loop()
            except Exception as exc:  # noqa: BLE001
                logger.exception("agent team run %s crashed", self.run_id)
                AgentTeamToolRegistry.unregister(self.coordinator["umo"])
                self.status = "failed"
                await self._persist()
                self._emit({"type": "stopped", "reason": f"runner error: {exc}"})
                return
            if self.status == "stopped":
                self._emit({"type": "stopped", "reason": "user stop"})
        finally:
            # Terminal runs release the ports' resources; a paused run keeps
            # them (run() is re-callable after resume).
            if self.status in TERMINAL_RUN_STATUSES and self.ports.close is not None:
                try:
                    await self.ports.close()
                except Exception:  # noqa: BLE001
                    # Cleanup must never mask the run outcome.
                    logger.exception(
                        "agent team run %s: ports close failed", self.run_id
                    )

    async def _ensure_row(self) -> None:
        """Create the run row if it does not exist yet.

        The service path persists the row before starting the orchestrator; a
        directly-constructed orchestrator (tests, embedded usage) still gets
        one so every later transition is actually persisted.
        """
        if await self.db.get_agent_team_run(self.run_id) is not None:
            return
        await self.db.create_agent_team_run(
            run_id=self.run_id,
            team_id=self.team_id,
            workflow_id=None,
            mode="auto",
            input=self.run_input,
            status=self.status,
            graph_snapshot={},
            node_states={},
            rounds=list(self.rounds),
        )

    async def _run_loop(self) -> None:
        while True:
            if self.status not in ("running", "paused", "stopping"):
                # Terminal runs must never re-enter the round engine: a
                # direct re-run() would live-loop skipped coordinator turns.
                return
            if self._stop_requested.is_set():
                # Defensive: the turn's finally already unregisters, this
                # covers exits between phases.
                AgentTeamToolRegistry.unregister(self.coordinator["umo"])
                self.status = "stopped"
                await self._persist()
                return
            if self.status == "paused":
                self._resume_wake.clear()
                if self.status == "paused":
                    # resume() may have fired between the check and the clear
                    # above; re-check so the wake signal is never erased.
                    await self._persist()
                    self._emit({"type": "paused", "reason": "user pause"})
                    await self._resume_wake.wait()
                continue
            n = len(self.rounds) + 1
            max_rounds = int(self.config["max_rounds"])
            if n > max_rounds:
                self.status = "paused"
                await self._persist()
                self._emit({"type": "paused", "reason": "max_rounds", "round": n})
                return
            outcome = await self._coordinator_turn(n)
            if outcome == "timeout":
                # Already paused, persisted and announced by the turn.
                return
            if self._stop_requested.is_set() or self.status != "running":
                continue
            if self._finish is not None:
                self.status = "completed"
                await self.db.update_agent_team_run(
                    self.run_id,
                    status=self.status,
                    result_summary=self._finish["summary"][:2000],
                    rounds=self.rounds,
                )
                self._emit({"type": "stopped", "reason": "finished"})
                return
            assignments = self._pending_dispatch
            if not assignments:
                # No tool call this turn: remind via the next turn's context;
                # two in a row park the run (resumable, counter resets).
                self._no_tool_rounds += 1
                if self._no_tool_rounds >= 2:
                    self.status = "paused"
                    await self._persist()
                    self._emit(
                        {
                            "type": "paused",
                            "reason": "no dispatch two rounds in a row",
                        }
                    )
                    return
                continue
            self._no_tool_rounds = 0
            self._emit(
                {
                    "type": "dispatch",
                    "round": n,
                    "assignments": assignments,
                    "notes": self._pending_notes or None,
                }
            )
            self.rounds.append(
                {
                    "n": n,
                    "assignments": assignments,
                    "notes": self._pending_notes,
                    "results": [],
                }
            )
            await self._persist()
            if self._stop_requested.is_set() or self.status != "running":
                continue
            await self._member_wave(n, assignments)
            await self._persist()


class AgentTeamRunService:
    """Run lifecycle: start/resume/stop DAG and auto runs, expose snapshots."""

    def __init__(self, db, chat_service, busy_checker=None, on_member_stop=None):
        """Args:
        db: Database helper.
        chat_service: Dashboard ChatService (ports wiring + busy detection).
        busy_checker: Optional session_id -> bool override (tests).
        on_member_stop: Optional sync (session_id) hook invoked on run stop
            to cancel in-flight member turns; wired to the chat stop API in
            the follow-up plan.
        """
        self.db = db
        self.chat_service = chat_service
        self.busy_checker = busy_checker
        self.on_member_stop = on_member_stop
        # Overridden in tests; production uses build_ports over the real
        # webchat queue manager.
        self.ports_factory: Callable[[str, Callable], TeamPorts] = (
            lambda username, emit: build_ports(chat_service, username, emit)
        )
        self._runners: dict[str, DAGRunner | AutoOrchestrator] = {}
        self._buses: dict[str, RunEventBus] = {}

    async def boot_sweep(self) -> int:
        """Mark stale runs interrupted at startup (spec §6.5).

        In-memory runners do not survive a restart; rows still marked
        running/paused cannot make progress and must surface as
        `interrupted` so the panel offers resume.

        Returns:
            Number of rows transitioned to `interrupted`.
        """
        stale = await self.db.get_agent_team_runs_by_status(["running", "paused"])
        for row in stale:
            await self.db.update_agent_team_run(row.run_id, status="interrupted")
        return len(stale)

    async def resume_run(self, username: str, run_id: str) -> dict:
        """Resume a paused (in-memory) or interrupted (post-restart) run.

        Args:
            username: Requesting dashboard user.
            run_id: Persisted run id.

        Returns:
            The snapshot of the resumed (or rebuilt) runner.

        Raises:
            AgentTeamsServiceError: On ownership errors or a status that
                cannot be resumed.
        """
        row, team = await self._require_run(username, run_id)
        runner = self._runners.get(run_id)
        if runner is not None:
            if runner.status != "paused":
                raise AgentTeamsServiceError("运行未处于暂停状态")
            runner.resume()
            # A failure-paused run's loop already returned, so its task is
            # done: resume() alone would flip a flag nothing will ever
            # observe. Spawn a fresh task, mirroring retry/skip.
            if runner.task is None or runner.task.done():
                self._start_runner_task(runner)
            return runner.snapshot()

        if row.status not in ("interrupted", "paused"):
            raise AgentTeamsServiceError(f"运行 '{run_id}' 当前状态不可恢复")
        config = {**DEFAULT_TEAM_CONFIG, **(team["config"] or {})}
        if row.mode == "auto":
            # Rebuild from the persisted rounds (next round = len+1). A fresh
            # orchestrator starts with the no-tool counter at 0, so a
            # no-dispatch pause gets a fresh chance — flagged so its first
            # turn injects the stronger no-dispatch reminder.
            await self.db.update_agent_team_run(run_id, status="running")
            runner = self._build_orchestrator(
                run_id=run_id,
                team=team,
                config=config,
                run_input=row.input,
                username=username,
                rounds=list(row.rounds or []),
            )
            runner._resume_reminder = True
            self._start_runner_task(runner)
            return runner.snapshot()
        # Redo semantics: in-flight nodes at interruption are re-dispatched
        # from scratch; done/skipped nodes and their results are preserved.
        node_states = {
            node_id: (
                {**state, "status": "pending"}
                if state["status"] == "running"
                else state
            )
            for node_id, state in (row.node_states or {}).items()
        }
        await self.db.update_agent_team_run(
            run_id, status="running", node_states=node_states
        )
        runner = self._build_runner(
            run_id=run_id,
            team=team,
            graph=row.graph_snapshot,
            config=config,
            run_input=row.input,
            node_states=node_states,
            username=username,
            workflow_id=row.workflow_id,
        )
        self._start_runner_task(runner)
        return runner.snapshot()

    async def start_run(self, username: str, team_id: str, payload: dict) -> dict:
        """Start a DAG or auto run for a team.

        Args:
            username: Requesting dashboard user.
            team_id: Owning team.
            payload: {mode: "dag" | "auto", input: str, workflow_id: str};
                workflow_id is required for dag and rejected for auto.

        Returns:
            The initial run snapshot.

        Raises:
            AgentTeamsServiceError: On ownership/validation errors, or when
                the team already has an active run (message contains
                "active run"; the API layer maps it to 409).
        """
        team = await self._require_team(username, team_id)
        mode = str(payload.get("mode") or "dag")
        if mode not in ("dag", "auto"):
            raise AgentTeamsServiceError(f"不支持的运行模式：{mode}")
        workflow_id = str(payload.get("workflow_id") or "")
        if mode == "auto":
            if workflow_id:
                raise AgentTeamsServiceError("自动编排无需选择工作流")
            workflow = None
        else:
            workflow = await self.db.get_agent_team_workflow(workflow_id)
            if workflow is None or workflow.team_id != team_id:
                raise AgentTeamsServiceError(f"工作流 '{workflow_id}' 不存在")
        if await self.db.get_active_agent_team_run(team_id):
            raise AgentTeamsServiceError("该团队已有 active run，无法重复启动")
        run_input = str(payload.get("input") or "").strip()
        if not run_input:
            raise AgentTeamsServiceError("运行输入不能为空")

        config = {**DEFAULT_TEAM_CONFIG, **(team["config"] or {})}
        run_id = uuid.uuid4().hex[:12]
        if mode == "auto":
            # Auto mode persists everything in `rounds`; no workflow, no DAG.
            await self.db.create_agent_team_run(
                run_id=run_id,
                team_id=team_id,
                workflow_id=None,
                mode=mode,
                input=run_input,
                status="running",
                graph_snapshot={},
                node_states={},
                rounds=[],
            )
            orchestrator = self._build_orchestrator(
                run_id=run_id,
                team=team,
                config=config,
                run_input=run_input,
                username=username,
            )
            self._start_runner_task(orchestrator)
            return orchestrator.snapshot()
        node_states = {
            node["id"]: {
                "status": "pending",
                "member_id": node["member_id"],
                "task_rendered": None,
                "result": None,
                "error": None,
                "started_at": None,
                "finished_at": None,
            }
            for node in workflow.graph.get("nodes", [])
        }
        await self.db.create_agent_team_run(
            run_id=run_id,
            team_id=team_id,
            workflow_id=workflow_id,
            mode=mode,
            input=run_input,
            status="running",
            graph_snapshot=workflow.graph,
            node_states=node_states,
            rounds=[],
        )
        runner = self._build_runner(
            run_id=run_id,
            team=team,
            graph=workflow.graph,
            config=config,
            run_input=run_input,
            node_states=node_states,
            username=username,
            workflow_id=workflow_id,
        )
        self._start_runner_task(runner)
        return runner.snapshot()

    def _build_runner(
        self,
        *,
        run_id,
        team,
        graph,
        config,
        run_input,
        node_states,
        username,
        workflow_id=None,
    ) -> DAGRunner:
        bus = RunEventBus()
        runner = DAGRunner(
            run_id=run_id,
            team_id=team["team_id"],
            graph=graph,
            config=config,
            members=team["members"],
            ports=self.ports_factory(username, bus.emit),
            db=self.db,
            bus=bus,
            username=username,
            run_input=run_input,
            node_states=node_states,
            on_member_stop=self.on_member_stop,
            workflow_id=workflow_id,
        )
        self._runners[run_id] = runner
        self._buses[run_id] = bus
        return runner

    def _build_orchestrator(
        self,
        *,
        run_id,
        team,
        config,
        run_input,
        username,
        rounds: list[dict] | None = None,
    ) -> AutoOrchestrator:
        bus = RunEventBus()
        members = team["members"]
        coordinator = next(
            (m for m in members if m["member_id"] == team.get("coordinator_member_id")),
            members[0],
        )
        orchestrator = AutoOrchestrator(
            run_id=run_id,
            team_id=team["team_id"],
            team_name=team["name"],
            members=members,
            coordinator=coordinator,
            config=config,
            run_input=run_input,
            ports=self.ports_factory(username, bus.emit),
            db=self.db,
            bus=bus,
            username=username,
            rounds=rounds,
            on_member_stop=self.on_member_stop,
        )
        self._runners[run_id] = orchestrator
        self._buses[run_id] = bus
        return orchestrator

    def _start_runner_task(self, runner: DAGRunner | AutoOrchestrator) -> None:
        runner.task = asyncio.create_task(
            runner.run(), name=f"agent_team_run_{runner.run_id}"
        )

    async def _require_run(self, username: str, run_id: str):
        """Load a run row and its owning team, enforcing ownership."""
        row = await self.db.get_agent_team_run(run_id)
        if row is None:
            raise AgentTeamsServiceError(f"运行 '{run_id}' 不存在")
        team = await self._require_team(username, row.team_id)
        return row, team

    async def _require_team(self, username: str, team_id: str) -> dict:
        team = await self.db.get_agent_team(team_id)
        if team is None or team.owner_username != username:
            raise AgentTeamsServiceError(f"团队 '{team_id}' 不存在")
        return _team_to_dict(team)

    async def get_event_bus(self, username: str, run_id: str) -> RunEventBus:
        await self._require_run(username, run_id)
        bus = self._buses.get(run_id)
        if bus is None:
            raise AgentTeamsServiceError(f"运行 '{run_id}' 不存在或已不在内存中")
        return bus

    async def get_run_snapshot(self, username: str, run_id: str) -> dict:
        await self._require_run(username, run_id)
        runner = self._runners.get(run_id)
        if runner is None:
            raise AgentTeamsServiceError(f"运行 '{run_id}' 不存在或已结束")
        return runner.snapshot()

    async def list_active_runs(self, username: str) -> dict:
        active = []
        for run_id, runner in list(self._runners.items()):
            if runner.status not in ("running", "paused"):
                continue
            row = await self.db.get_agent_team_run(run_id)
            if row is None:
                continue
            try:
                await self._require_team(username, row.team_id)
            except AgentTeamsServiceError:
                continue
            active.append(runner.snapshot())
        return {"runs": active}

    async def list_team_runs(self, username: str, team_id: str) -> dict:
        await self._require_team(username, team_id)
        rows = await self.db.get_agent_team_runs_by_team(team_id)
        return {"runs": [_run_to_dict(r) for r in rows]}

    async def pause_run(self, username: str, run_id: str) -> dict:
        await self._require_run(username, run_id)
        runner = self._require_runner(run_id)
        runner.pause()
        return {"message": "已暂停"}

    async def request_stop_run(self, username: str, run_id: str) -> dict:
        await self._require_run(username, run_id)
        runner = self._require_runner(run_id)
        if runner.task is not None and not runner.task.done():
            runner.request_stop()
            if self.on_member_stop is not None:
                # DAG runs track in-flight members via node_states; auto runs
                # carry no node states (cancel propagation for both lands in
                # the stop-semantics task).
                for state in getattr(runner, "node_states", {}).values():
                    if state["status"] != "running":
                        continue
                    member = runner._member_by_id(state["member_id"])
                    if member is not None:
                        self.on_member_stop(member["session_id"])
            return {"message": "停止中"}
        # Dead task (e.g. a failure-paused run whose loop already returned):
        # the stop flag would never be observed, so land the terminal state
        # directly instead of silently doing nothing.
        if runner.status not in TERMINAL_RUN_STATUSES:
            runner.status = "stopped"
            await self.db.update_agent_team_run(run_id, status="stopped")
            runner._emit({"type": "stopped", "reason": "user stop"})
        return {"message": "已停止"}

    async def retry_node(self, username: str, run_id: str, node_id: str) -> dict:
        await self._require_run(username, run_id)
        runner = self._require_runner(run_id)
        await runner.retry_node(node_id)
        if runner.task is None or runner.task.done():
            self._start_runner_task(runner)
        return {"message": "已重试"}

    async def skip_node(self, username: str, run_id: str, node_id: str) -> dict:
        await self._require_run(username, run_id)
        runner = self._require_runner(run_id)
        await runner.skip_node(node_id)
        if runner.task is None or runner.task.done():
            self._start_runner_task(runner)
        return {"message": "已跳过"}

    def _require_runner(self, run_id: str) -> DAGRunner | AutoOrchestrator:
        runner = self._runners.get(run_id)
        if runner is None:
            raise AgentTeamsServiceError(f"运行 '{run_id}' 不存在或已结束")
        return runner
