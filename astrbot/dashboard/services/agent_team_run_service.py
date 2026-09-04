"""Agent Teams run lifecycle: event bus, DAGRunner, run service (spec §6.3/§6.5/§6.6)."""

import asyncio
import time
import uuid
from collections.abc import Callable

from astrbot import logger
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
    ) -> None:
        self.run_id = run_id
        self.team_id = team_id
        self.graph = graph
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
        """Return a JSON-safe summary for API and SSE consumers."""
        return {
            "run_id": self.run_id,
            "team_id": self.team_id,
            "status": self.status,
            "node_states": self.node_states,
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
                self.ports.collect(member["session_id"], message_id),
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


class AgentTeamRunService:
    """Run lifecycle: start/resume/stop DAG runs and expose snapshots."""

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
        self._runners: dict[str, DAGRunner] = {}
        self._buses: dict[str, RunEventBus] = {}

    async def start_run(self, username: str, team_id: str, payload: dict) -> dict:
        """Start a DAG run for a team.

        Args:
            username: Requesting dashboard user.
            team_id: Owning team.
            payload: {mode: "dag", input: str, workflow_id: str}.

        Returns:
            The initial run snapshot.

        Raises:
            AgentTeamsServiceError: On ownership/validation errors, or when
                the team already has an active run (message contains
                "active run"; the API layer maps it to 409).
        """
        team = await self._require_team(username, team_id)
        mode = str(payload.get("mode") or "dag")
        if mode != "dag":
            raise AgentTeamsServiceError("Plan 1 仅支持 mode=dag（自动编排见后续计划）")
        workflow_id = str(payload.get("workflow_id") or "")
        workflow = await self.db.get_agent_team_workflow(workflow_id)
        if workflow is None or workflow.team_id != team_id:
            raise AgentTeamsServiceError(f"工作流 '{workflow_id}' 不存在")
        if await self.db.get_active_agent_team_run(team_id):
            raise AgentTeamsServiceError("该团队已有 active run，无法重复启动")
        run_input = str(payload.get("input") or "").strip()
        if not run_input:
            raise AgentTeamsServiceError("运行输入不能为空")

        config = {**DEFAULT_TEAM_CONFIG, **(team["config"] or {})}
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
        run_id = uuid.uuid4().hex[:12]
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
        )
        self._runners[run_id] = runner
        self._buses[run_id] = bus
        return runner

    def _start_runner_task(self, runner: DAGRunner) -> None:
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
        runner.request_stop()
        if self.on_member_stop is not None:
            for state in runner.node_states.values():
                if state["status"] != "running":
                    continue
                member = runner._member_by_id(state["member_id"])
                if member is not None:
                    self.on_member_stop(member["session_id"])
        return {"message": "停止中"}

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

    def _require_runner(self, run_id: str) -> DAGRunner:
        runner = self._runners.get(run_id)
        if runner is None:
            raise AgentTeamsServiceError(f"运行 '{run_id}' 不存在或已结束")
        return runner
