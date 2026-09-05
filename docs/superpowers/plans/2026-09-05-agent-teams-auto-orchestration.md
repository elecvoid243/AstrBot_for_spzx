# Agent Teams Auto-Orchestration + Collab Retirement (Plan 3/3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the spec's auto-orchestration mode (coordinator drives rounds via `team_dispatch`/`team_finish` LLM tools, scoped "if and only if" by a core-side registry), land the spec's stop semantics (collect abandonment + member cancel propagation), enable auto mode in the monitor UI, and retire the Agent Collab panel.

**Architecture:** A core-side dumb registry (`AgentTeamToolRegistry`, keyed by umo) holds per-run tool instances whose callbacks are injected by the dashboard run service — no core→dashboard import. `build_main_agent` merges registry tools into `req.func_tool` (one call next to `_apply_subagent_manager_tools`, astr_main_agent.py:685). Tools are registered ONLY around the coordinator's turn (deliver→collect), so they are never visible to ordinary chat turns. `AutoOrchestrator` reuses `TeamPorts` exactly like `DAGRunner`, persists `rounds` per round, and drives rounds until `team_finish`, a 2-consecutive no-tool pause, or `max_rounds`.

**Tech Stack:** Python 3.10+/pytest (backend), Vue 3.3/Vuetify/vitest (frontend), pnpm.

**Spec:** `docs/superpowers/specs/2026-09-05-agent-teams-design.md` (§6.4 auto-orchestration, §6.3 stop semantics, §2.2 Collab retirement, §8 auto UI enablement)

## Global Constraints

- Python 3.10+; English code/comments; Google docstrings on public methods; conventional commits; `ruff format` + `ruff check` clean (backend).
- Vue 3.3: NO `defineModel`; all UI strings via `useModuleI18n('features/agent-teams')` with key parity across zh-CN/en-US/ru-RU; frontend tests `*.spec.ts` only.
- **Registry scoping ("当且仅当"):** team tools are visible to an agent loop ONLY while a coordinator turn is in flight (register before deliver, unregister in a `finally` after collect, and on stop/finish/pause paths). Ordinary chat turns must never see them.
- Auto mode: `workflow_id` is None; `start_run(mode="auto")` ignores/rejects a provided workflow; run row keeps `node_states={}` and persists everything in `rounds`.
- Round persistence: append/UPDATE `rounds` after each coordinator turn and after each member-results batch (`update_agent_team_run(rounds=...)`).
- Stop semantics (spec §6.3/§10): stop = abandon collection (race collect vs `_stop_requested`, do not wait out `reply_timeout`) + cancel propagation via `on_member_stop(session_id)` + terminal `stopped`.
- Collab retirement is FRONTEND-ONLY: delete the collab UI components/integration; keep `/api/agent_collab/*` routes, `AgentCollabService`, `agentCollabApi`, and backend collab tests untouched (transition period per spec §2.2); add a deprecation comment atop `agent_collab.py`.
- Tests: backend `uv run pytest tests/agent_teams/ -v` (47 prior + new); frontend `cd dashboard && pnpm exec vitest run` (867 passing prior; the 3 pre-existing `DocumentManager.spec.ts` fullscreen failures are known-unrelated).

---

### Task 1: Core — `agent_team_tools.py` (tools + registry)

**Files:**
- Create: `astrbot/core/agent_team_tools.py`
- Test: `tests/agent_teams/test_agent_team_tools.py`

**Interfaces:**
- Produces:
  - `class AgentTeamToolRegistry` — class-level registry keyed by umo: `register(umo: str, tools: list[FunctionTool]) -> None`, `unregister(umo: str) -> None` (idempotent on missing), `get_tools(umo: str) -> list[FunctionTool]` (empty list when absent). Internal dict NOT class-shared-mutable across event loops concerns: plain class attribute is fine (single loop).
  - `class TeamDispatchTool(FunctionTool)` — instance-based; `__init__(self, member_names: list[str], on_dispatch)` stores both and sets dataclass fields `name="team_dispatch"`, `description` (English, tells the coordinator to dispatch this round's member tasks), `parameters` JSON schema: `{"type": "object", "properties": {"assignments": {"type": "array", "items": {"type": "object", "properties": {"member": {"type": "string"}, "task": {"type": "string"}}, "required": ["member", "task"]}}, "notes": {"type": "string"}}, "required": ["assignments"]}`. `async call(self, context, assignments=None, notes=None) -> str`: validate `assignments` is a non-empty list of dicts with `member`/`task` strings — invalid → return an error string listing valid member names (LLM self-corrects same-turn); unknown member name (case-insensitive match against `member_names`) → same error string; valid → `await on_dispatch(assignments, notes)` then return `f"Dispatched {len(assignments)} assignment(s)."` + reminder that calling `team_finish` ends the run when all results are in.
  - `class TeamFinishTool(FunctionTool)` — same pattern: `name="team_finish"`, parameters `{"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}`; `call` validates non-empty summary (else error string), `await on_finish(summary)`, returns `"Team task finished."`.
  - `build_team_tools(member_names: list[str], on_dispatch, on_finish) -> list[FunctionTool]` — convenience returning both instances.
- Read first: `astrbot/core/agent/tool.py` — `FunctionTool` is a `@dataclass` subclassing `ToolSchema` (fields `name`, `description`, `parameters`, plus `handler/handler_module_path/active/is_background_task`). Instance subclasses set those via `super().__init__(name=..., description=..., parameters=...)` or direct field assignment — match whatever `HandoffTool` (`astrbot/core/agent/handoff.py`) does and mirror it. `call` returns `ToolExecResult = str | mcp.types.CallToolResult` — a plain string is a valid result.
- If `ToolSchema`/`FunctionTool` is not actually a plain dataclass (e.g. pydantic), adapt construction to the real mechanism — the contract is only: instances carry name/description/parameters and an async `call`.

- [ ] **Step 1: Write the failing test**

```python
# tests/agent_teams/test_agent_team_tools.py
"""Agent team tool + registry tests (spec §6.4)."""

import pytest

from astrbot.core.agent_team_tools import (
    AgentTeamToolRegistry,
    TeamDispatchTool,
    TeamFinishTool,
    build_team_tools,
)

MEMBERS = ["主管", "写手", "审校"]


@pytest.mark.asyncio
async def test_dispatch_tool_validates_and_invokes_callback():
    pushed = []

    async def on_dispatch(assignments, notes):
        pushed.append((assignments, notes))

    tool = TeamDispatchTool(MEMBERS, on_dispatch)
    assert tool.name == "team_dispatch"
    result = await tool.call(
        None,
        assignments=[{"member": "写手", "task": "写初稿"}],
        notes="注意语气",
    )
    assert "Dispatched 1" in result
    assert pushed == [([{"member": "写手", "task": "写初稿"}], "注意语气")]

    # case-insensitive match
    result = await tool.call(None, assignments=[{"member": "写 手 ".strip(), "task": "x"}])
    assert "Dispatched" in result or "写手" in result

    # unknown member -> error string listing valid names, callback NOT called
    result = await tool.call(None, assignments=[{"member": "不存在", "task": "x"}])
    assert "不存在" in result and "写手" in result
    assert len(pushed) == 1

    # malformed payloads -> error string, not an exception
    for bad in (None, [], "x", [{"member": "写手"}], [{"task": "no member"}]):
        result = await tool.call(None, assignments=bad)
        assert isinstance(result, str) and "Dispatched" not in result
    assert len(pushed) == 1


@pytest.mark.asyncio
async def test_finish_tool_and_factory():
    finished = []

    async def on_finish(summary):
        finished.append(summary)

    tool = TeamFinishTool(on_finish)
    assert tool.name == "team_finish"
    assert "finished" in (await tool.call(None, summary="完成")).lower()
    assert finished == ["完成"]
    bad = await tool.call(None, summary="  ")
    assert isinstance(bad, str) and "finished" not in bad.lower()

    tools = build_team_tools(MEMBERS, on_dispatch=None, on_finish=on_finish)
    assert {t.name for t in tools} == {"team_dispatch", "team_finish"}
    assert all(t.description for t in tools)
    assert "assignments" in tools[0].parameters["properties"] or any(
        "assignments" in t.parameters.get("properties", {}) for t in tools
    )


def test_registry_scoping():
    tools = build_team_tools(MEMBERS, on_dispatch=None, on_finish=None)
    AgentTeamToolRegistry.register("umo-a", tools)
    try:
        assert AgentTeamToolRegistry.get_tools("umo-a") == tools
        assert AgentTeamToolRegistry.get_tools("umo-b") == []
    finally:
        AgentTeamToolRegistry.unregister("umo-a")
    assert AgentTeamToolRegistry.get_tools("umo-a") == []
    AgentTeamToolRegistry.unregister("umo-a")  # idempotent
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/agent_teams/test_agent_team_tools.py -v` → FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement `astrbot/core/agent_team_tools.py`** — module docstring explaining the "if and only if" scoping contract; registry with a module-level `_TOOLS: dict[str, list[FunctionTool]]`; both tool classes follow the `HandoffTool` construction pattern; full Google docstrings; English descriptions for the LLM (the tool descriptions are prompt-facing — write clear English instructions telling the coordinator WHEN to call each tool).

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/agent_teams/test_agent_team_tools.py -v` → PASS; full `uv run pytest tests/agent_teams/ -q` (47 prior stay green).

- [ ] **Step 5: Commit** — `git add astrbot/core/agent_team_tools.py tests/agent_teams/test_agent_team_tools.py && git commit -m "feat: add agent team dispatch tools and registry"`

---

### Task 2: Core — `build_main_agent` registry merge

**Files:**
- Modify: `astrbot/core/astr_main_agent.py` (new `_apply_agent_team_tools(req, event)` helper + one call right after the `_apply_subagent_manager_tools` call at line ~685 inside `_ensure_persona_and_skills`)
- Test: `tests/agent_teams/test_agent_team_tools.py` (append merge tests)

**Interfaces:**
- Consumes: `AgentTeamToolRegistry.get_tools` (Task 1), `ToolSet.add_tool` (`tool.py:90`), the existing `req.func_tool is None → ToolSet()` guard pattern (`astr_main_agent.py:1190-1192`).
- Produces: `_apply_agent_team_tools(req: ProviderRequest, event: AstrMessageEvent) -> None` — `tools = AgentTeamToolRegistry.get_tools(event.unified_msg_origin)`; if empty → return; else ensure `req.func_tool` exists and `add_tool` each (add_tool's active-overwrite rule makes duplicate registration harmless).

- [ ] **Step 1: Write the failing test** — append to `test_agent_team_tools.py`:

```python
async def test_build_main_agent_merges_registry_tools(monkeypatch):
    """Registry tools appear in req.func_tool only for the registered umo."""
    from astrbot.core.agent_team_tools import AgentTeamToolRegistry, build_team_tools
    from astrbot.core.astr_main_agent import _apply_agent_team_tools

    class FakeEvent:
        unified_msg_origin = "webchat:FriendMessage:conv-1"

    class FakeReq:
        func_tool = None

    req = FakeReq()
    # unregistered -> no tools, func_tool stays None (no empty ToolSet spam)
    await _apply_agent_team_tools(req, FakeEvent())
    assert req.func_tool is None

    tools = build_team_tools(["a", "b"], None, None)
    AgentTeamToolRegistry.register(FakeEvent.unified_msg_origin, tools)
    try:
        await _apply_agent_team_tools(req, FakeEvent())
        assert req.func_tool is not None
        assert {t.name for t in req.func_tool.tools} >= {"team_dispatch", "team_finish"}
        # idempotent re-merge (active-overwrite)
        await _apply_agent_team_tools(req, FakeEvent())
        assert len([t for t in req.func_tool.tools if t.name == "team_dispatch"]) == 1
    finally:
        AgentTeamToolRegistry.unregister(FakeEvent.unified_msg_origin)
```

- [ ] **Step 2: RED** (`AttributeError: cannot import name '_apply_agent_team_tools'`).

- [ ] **Step 3: Implement** — helper next to `_apply_subagent_manager_tools` (~line 1178), call inserted immediately after the existing call at line ~685 (`await _apply_agent_team_tools(req, event)`), import at top: `from astrbot.core.agent_team_tools import AgentTeamToolRegistry`.

- [ ] **Step 4: GREEN** — full backend suite green.

- [ ] **Step 5: Commit** — `git commit -m "feat: merge agent team tools into main agent by registry"`

---

### Task 3: Backend — `AutoOrchestrator` + auto mode in `AgentTeamRunService`

**Files:**
- Modify: `astrbot/dashboard/services/agent_team_run_service.py`
- Test: `tests/agent_teams/test_agent_team_auto.py` (new file; reuse the scripted-ports fixture style from `test_agent_team_dag_runner.py` — copy the `make_members`/`scripted_ports` helpers, extended for collect's 3-arg member_id tag which Task T3 of Plan 2's fix wave introduced)

**Interfaces:**
- Produces:
  - `class AutoOrchestrator` — keyword ctor `__init__(self, *, run_id, team_id, team_name, members, coordinator, config, run_input, ports, db, bus, username, rounds=None, on_member_stop=None)`; attributes `status`, `task`, `rounds: list[dict]`, `_pending_dispatch: list | None`, `_finish: dict | None`, `_no_tool_rounds: int`; methods `async run()`, `pause()`, `resume()`, `request_stop()`, `snapshot() -> dict` (`{run_id, team_id, status, rounds, progress: {"round": n, "max_rounds": m}}`), and re-callable like DAGRunner.
  - `AgentTeamRunService.start_run` accepts `mode: "auto"` — creates an AutoOrchestrator (workflow_id forced None; reject `workflow_id` provided with auto → `AgentTeamsServiceError("自动编排无需选择工作流")`), node_states `{}`, snapshot from the orchestrator.
  - `AgentTeamRunService.resume_run` — auto branch: row `mode == "auto"` + status `interrupted/paused` + not in memory → rebuild AutoOrchestrator from `row.rounds` (next round = len+1) + row.input; dag branch unchanged.
- Round mechanics (the heart — implement exactly):
  1. `_run_loop`: while status in ("running", "stopping") and not stopped: stop check → round guard (`max_rounds` reached → status paused, emit `{"type": "paused", "reason": "max_rounds", "round": n}`) → coordinator turn → dispatch handling → member wave → persist.
  2. Coordinator turn: `n = len(self.rounds) + 1`; context header built by `_coordinator_context(n)` — roster (`member name: persona_id or "自定义成员"` lines), task (round 1 = run_input; else prior-round digest built by `_results_digest()` — per member `name: first ~500 chars of latest result`), instruction block (English, telling the coordinator: use `team_dispatch` with assignments for THIS round; call `team_finish(summary)` when the overall goal is achieved; no-tool rounds are reminders). `tools = build_team_tools(names, on_dispatch=self._on_dispatch, on_finish=self._on_finish)`; `AgentTeamToolRegistry.register(coordinator["umo"], tools)`; **`try:`** emit `{"type": "round", "n": n, "max_rounds": max_rounds}` → `self._pending_dispatch = None; self._finish = None` → busy-wait → emit sent (message event, member_id coordinator) → `deliver(coordinator.session_id, body, context)` → `collect(session_id, message_id, coordinator.member_id)` (3-arg) → emit reply → **`finally: AgentTeamToolRegistry.unregister(coordinator["umo"])`**. Timeout: wrap deliver+collect in `asyncio.wait_for(..., reply_timeout)` → TimeoutError → pause with reason "coordinator timeout" (emit paused).
  3. Dispatch handling: if `self._finish` → status completed, `result_summary = summary`, persist, emit `{"type": "stopped", "reason": "finished"}`, return. `assignments = self._pending_dispatch`; if falsy → `_no_tool_rounds += 1`; if `>= 2` → status paused, persist, emit paused("no dispatch two rounds in a row") → return (resumable via resume_run which resets the counter and injects a stronger reminder); else continue to next round (the reminder IS the next round's context via `_coordinator_context` noticing the previous round had no dispatch). If assignments: `_no_tool_rounds = 0`; emit `{"type": "dispatch", "round": n, "assignments": assignments}`; append round entry `{"n": n, "assignments": assignments, "results": []}` and persist rounds.
  4. Member wave: resolve each assignment's member by case-insensitive name (unknown → record result `{"member": name, "error": "unknown member"}` and continue); run with `asyncio.Semaphore(max_parallel)` + `asyncio.gather`: per member — emit sent → `deliver(member.session_id, task, context=f"[团队任务] 来自协调者（第 {n} 轮）")` → `collect(..., member.member_id)` under `wait_for(reply_timeout)` → success: `{"member": name, "result": reply}` + emit reply; failure/timeout: `{"member": name, "error": str(e)}`. Append results to the round entry, persist rounds after the wave.
  5. Stop/pause: mirror DAGRunner — `_resume_wake`/`_stop_requested` events; pause pauses BETWEEN phases (checked at loop top and before the member wave); stop → unregister tools (defensive), status stopped, persist. `run()` never raises; crash → status failed + persist + emit stopped.
- Events reuse the existing vocabulary; frontend folds `round` (already) and `dispatch` (Task 5).

- [ ] **Step 1: Write the failing tests** (`test_agent_team_auto.py`) — scripted ports + a scripted coordinator flow:
  1. `test_auto_run_single_round_finish` — coordinator's scripted reply triggers the dispatch tool (drive it by making the ports' deliver record the turn and the TEST call the tool the orchestrator registered — simplest: the test imports `AgentTeamToolRegistry`, snapshots tools for the coordinator umo mid-run via a `deliver` side-effect, and invokes `tool.call(None, assignments=...)` then the finish tool on the second turn) → orchestrator completes with `result_summary`; round entry persisted with results; registry empty after run (`get_tools(coordinator_umo) == []`).
  2. `test_auto_run_member_wave` — dispatch to 2 members → both results recorded in the round entry; reply events emitted with member_id; rounds persisted.
  3. `test_auto_run_two_no_tool_rounds_pause` — coordinator replies text-only twice → status paused, registry unregistered, resume_run path (service-level) restarts with `_no_tool_rounds` reset.
  4. `test_auto_run_registry_scoped_to_turn` — during the coordinator turn (inside deliver side-effect) `get_tools(umo)` non-empty; after collect returns (inside collect side-effect after end) — and after the run — empty. This pins the "当且仅当" constraint.
  5. `test_start_run_auto_mode` — service-level: mode auto without workflow → run created (rounds=[]), orchestrator spawned, snapshot has `progress.round`; mode auto WITH workflow_id → service error.
  Test technique note: the tool callback path is the reliable way to script multi-round flows — in the fake `deliver`, after recording, call `AgentTeamToolRegistry.get_tools(umo)`, find `team_dispatch`, and `await tool.call(None, assignments=...)` (round 1) / `team_finish.call(None, summary=...)` (final round) before returning the message_id; the fake `collect` returns the coordinator's text. Verify this composes with the orchestrator's own unregister-in-finally (call the tools BEFORE collect's end event fires — i.e. inside deliver, which runs before collect subscribes).

- [ ] **Step 2: RED** (`ImportError: cannot import name 'AutoOrchestrator'`).

- [ ] **Step 3: Implement** per the mechanics above. Keep `start_run`'s dag path byte-identical; auto path branches on mode. `resume_run` gains the auto branch (and `_no_tool_rounds` resets to 0 on resume). Registry imports: `from astrbot.core.agent_team_tools import AgentTeamToolRegistry, build_team_tools`.

- [ ] **Step 4: GREEN** — `uv run pytest tests/agent_teams/ -v` (47 + new all pass) + ruff.

- [ ] **Step 5: Commit** — `git commit -m "feat: add auto orchestration rounds engine for agent teams"`

---

### Task 4: Backend — stop semantics (abandon collection + cancel propagation)

**Files:**
- Modify: `astrbot/dashboard/services/agent_team_run_service.py` (`DAGRunner._execute_node`, `AutoOrchestrator` member wave uses the same helper if extractable without contortion — otherwise apply inline to both)
- Modify: `astrbot/dashboard/api/app.py` (`on_member_stop` wiring, replacing the `None` placeholder)
- Test: `tests/agent_teams/test_agent_team_stop.py` (new)

**Interfaces:**
- `_execute_node` (and the orchestrator's member/coordinator turns): instead of a bare `await asyncio.wait_for(ports.collect(...), timeout)`, race collect against `self._stop_requested`: `collect_task = asyncio.ensure_future(wait_for(collect, timeout))`; `stop_task = asyncio.ensure_future(self._stop_requested.wait())`; `done, _ = await asyncio.wait({collect_task, stop_task}, return_when=FIRST_COMPLETED)` — stop wins → cancel collect_task, emit nothing further for the node, return (the loop's stop branch handles terminal state); collect wins → cancel stop_task, proceed as today. This mirrors collab's `_turn` race (`agent_collab_service.py:596-618`).
- `app.py`: `AgentTeamRunService(..., on_member_stop=...)` — replace `None`:

```python
    from astrbot.core.utils.active_event_registry import active_event_registry

    def _stop_member_turn(session_id: str) -> None:
        # Members are webchat sessions; their umo is stored on the member row.
        # Best-effort: look up the member's umo via the team rows is unnecessary —
        # request_agent_stop_all takes the full umo, so map session -> umo using
        # the same builder as chat_service.stop_session.
        ...
```

  The member dict already stores `umo` (Plan 1 Task 4), but `on_member_stop` receives only `session_id`. Wire it with a closure over `services.agent_team_runs` is wrong-direction; simplest correct implementation: change the run service to pass the FULL member dict to `on_member_stop` — extend `request_stop_run`'s call to `self.on_member_stop(member)` and in app.py:

```python
    def _stop_member_turn(member: dict) -> None:
        from astrbot.core.utils.active_event_registry import active_event_registry

        umo = member.get("umo") or ""
        if umo:
            active_event_registry.request_agent_stop_all(umo)
```

  Update the service signature docstring (`on_member_stop: Callable[[dict], None] | None` — sync, receives the member dict) and the existing `request_stop_run` loop (pass `member` instead of `member["session_id"]`). Verify `active_event_registry.request_agent_stop_all` exists with that name (grep `request_agent_stop_all` — used at chat_service.py:2037).

- [ ] **Step 1: Failing tests** (`test_agent_team_stop.py`):
  1. `test_stop_abandons_collection_mid_turn` — DAGRunner with a collect that blocks forever (asyncio.Event-gated); start run; wait until the node is `running` (poll via bus events); `request_stop()`; assert `run()` returns within ~1s (NOT reply_timeout) and status `stopped`.
  2. `test_stop_propagates_to_member` — service-level `request_stop_run` with `on_member_stop=spy` asserting it received the member dict (with umo) for in-flight nodes; and app-wiring smoke: build the closure `_stop_member_turn` logic directly (import the app module is heavy — instead assert the service calls the hook with the member dict, and unit-test the closure body by importing it if app.py exposes it, else accept the service-level pin + a comment).
- [ ] **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (full backend suite) → **Step 5: Commit** — `git commit -m "feat: abandon in-flight turns on agent team stop and propagate member cancellation"`

---

### Task 5: Frontend — reducer dispatch folding + run mode

**Files:**
- Modify: `dashboard/src/composables/agentTeamsRunReducer.ts`
- Test: `dashboard/src/composables/agentTeamsRunReducer.spec.ts` (append)

**Interfaces:**
- `TeamsRunState` gains: `mode: 'auto' | 'dag'` (seedable, default `'dag'`), `dispatches: Array<{round: number, assignments: Array<{member: string, task: string}>, notes?: string}>` (append on `dispatch` events; the parser now REQUIRES `assignments` to be an array of `{member, task}` objects — this also lands the deferred `Array.isArray` guard: non-conforming dispatch payloads parse to null).
- `createTeamsRunState(runId, seed?)` — seed gains `mode?`.
- Fold: `dispatch` → validate + push `{round, assignments, notes?}`.

- [ ] **Step 1: Append failing specs** — dispatch happy path pushes to `dispatches`; malformed dispatch (assignments not array / missing member) → `parseTeamsEvent` returns null; seed `{mode: 'auto'}` reflects in `state.mode` with default `'dag'`.
- [ ] **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (`pnpm exec vitest run src/composables/agentTeamsRunReducer.spec.ts` + full composables suite) → **Step 5: Commit** — `git commit -m "feat(dashboard): fold dispatch events and run mode into agent teams reducer"`

---

### Task 6: Frontend — auto mode in the monitor

**Files:**
- Modify: `dashboard/src/components/agent_teams/RunMonitor.vue` (+ i18n keys ×3 if needed: `monitor.roundLabel`, `monitor.noDispatchHint`)
- Test: `dashboard/src/components/agent_teams/RunMonitor.spec.ts` (append)

**Interfaces:**
- Mode select: `auto` option ENABLED (drop its `disabled: true`); the `monitor.autoModeDisabled` hint line removed from the select (key stays in locales for now — or repurpose: show it as the auto option's item subtitle; simplest: delete the hint binding and the constraint comment, keep key for parity).
- Workflow select + start guard become mode-conditional: `mode === 'auto'` → workflow select hidden (not just optional), start enabled without workflow (`:disabled="!goal.trim() || (mode === 'dag' && !workflowId)"`); start payload `workflow_id: mode === 'dag' ? workflowId : null`.
- Run display: `const isAuto = computed(() => runState.value?.mode === 'auto')`; when attached AND auto → hide the 窗口/DAG toggle and the DAG canvas; show a round indicator chip `tm('monitor.roundLabel', { n: runState.round?.n ?? 0, max: maxRoundsFromConfig })` (max: use the team config's max_rounds via `selectedTeam.config` fallback 20) + a simple dispatch log (last 5 dispatches from `runState.dispatches`, rendered as compact lines `第{n}轮 → member: task-truncated`); DAG strip/progress hidden for auto (node_states is empty).
- `useAgentTeamsRun.openRun` seed already passes the row — ensure `mode` flows: the composable's seed mapping must forward `row.mode` (one line; verify and add).
- Specs: auto option enabled + start without workflow succeeds with `workflow_id: null`; dag still requires workflow; auto attached run hides DAG toggle and shows round chip; dispatch log renders from `runState.dispatches`.

- [ ] **Step 1: failing specs** → **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (`pnpm exec vitest run src/components/agent_teams`) → **Step 5: Commit** — `git commit -m "feat(dashboard): enable auto orchestration mode in agent teams monitor"`

---

### Task 7: Frontend — Collab retirement

**Files:**
- Create: `dashboard/src/utils/memberColors.ts` (move `collabGroupColor` / `collabMemberColor` / `collabWithAlpha` verbatim from `useAgentCollab.ts:29-58`, with a comment noting origin)
- Modify: `dashboard/src/components/chat/Chat.vue` (remove ALL collab integration — see checklist)
- Modify: `dashboard/src/components/chat/chatMarkdownComponents.ts` (remove the `CollabAwareCodeBlock` import + `code_block` override at lines 3/21 — first READ `CollabAwareCodeBlock.vue` to learn what it wraps: if it only adds collab-route awareness around a standard block, removing the override restores markstream's default code block; confirm no other consumer relies on the override)
- Delete: `dashboard/src/components/chat/CollabPanel.vue`, `CollabTranscriptPanel.vue`, `CollabBindDialog.vue`, `CollabBindDialog.spec.ts`, `CollabRouteCard.vue` (check importers first), `message_list_comps/CollabAwareCodeBlock.vue`, `dashboard/src/composables/useAgentCollab.ts`
- Modify: `dashboard/src/components/agent_teams/AgentWindow.vue`, `dashboard/src/composables/useAgentTeams.ts`, `dashboard/src/composables/useAgentTeamsRun.ts` (imports → `@/utils/memberColors`)
- Modify: `astrbot/dashboard/api/agent_collab.py` (add a module-level deprecation comment: superseded by Agent Teams, kept for transition per spec §2.2)
- Check first (grep): any OTHER importer of the deleted files (`useAgentCollab`, `CollabRouteCard`, `CollabAwareCodeBlock`) beyond those listed — CollabTranscriptPanel imports CollabRouteCard and messageBlocks; `systemStream.ts` does NOT import collab. Also grep specs referencing collab fixtures (`tests/` + `dashboard/src/**/*.spec.ts`).

**Chat.vue removal checklist** (grep anchors, all verified present):
- Template: collab bind button block (~133-137), selection-mode collab button (~170-176), `collabBadges` loop in session rows (~320-333), sidebar collab group list section (~435-470, incl. dissolve), `<CollabPanel>` + `<CollabTranscriptPanel>` mounts (~842-855), `<CollabBindDialog>` (~1030-1034)
- Script: imports (agentCollabApi from v1:1079 keep other named imports, CollabBindDialog/CollabPanel/CollabTranscriptPanel:1112-1114, useAgentCollab:1117), all `collab*` refs/computed/handlers (`collabGroups`, `activeCollabGroupId`, `showCollabTranscript`, `collabBindMode`, `collabDialogOpen`, `collabBindPrefill`, `startCollabBinding`, `openCollabBindFromSelection`, `collabBadges`, `toggleActiveCollabGroup`, `dissolveCollabGroup`, `onCollabSaved`, recovery call ~2097-2099), collab CSS rules (grep `.collab-` in the style block)
- Do NOT touch: `useMessages`/`systemStream`/chat markdown beyond the code_block override; any non-collab behavior.

- [ ] **Step 1: move color helpers + update the 3 agent_teams importers** — run `pnpm exec vitest run src/components/agent_teams src/composables` (green) — commit-ready checkpoint.
- [ ] **Step 2: Chat.vue removal + chatMarkdownComponents fallback + delete the 6 collab files** — run `pnpm exec vitest run` (full; only the 3 known DocumentManager failures acceptable) and `pnpm build` (catches dead imports/type breaks).
- [ ] **Step 3: backend deprecation comment** — no test needed.
- [ ] **Step 4: Commit** — `git commit -m "feat(dashboard): retire agent collab panel in favor of agent teams"`

---

### Task 8: Full verification + final review

- [ ] **Step 1:** Backend: `uv run pytest tests/ -q` (new failures triaged against the Plan-1 baseline: 39 pre-existing); frontend: `pnpm exec vitest run` (3 known DocumentManager failures); `pnpm build`; `pnpm generate:api` idempotency (openapi yaml unchanged this plan — no drift expected).
- [ ] **Step 2:** Manual smoke (surfaced to the human): create an auto run (no workflow) → coordinator dispatches to members → windows show round activity and member replies → finish produces 总结; mid-run stop abandons the turn promptly; `/chat` page no longer shows any collab UI.
- [ ] **Step 3:** controller: final whole-branch review (base = Plan 3 start commit), one fix wave if findings, workspace cleanup.

---

## Plan Notes

- **Out of scope:** runs/bus eviction (backend, accepted-as-is), reconnect-budget reset, RunsHistory sequencing token — still deferred; if touched incidentally, keep the change minimal.
- The AutoOrchestrator deliberately does NOT emit node_status/dag_progress (no DAG); the frontend auto display is round/dispatch-based (Task 6). If a future plan wants a task board for auto runs, rounds already persist everything needed.
- `team_dispatch`'s `notes` field flows into the round entry (`{"n", "assignments", "notes"}`) and the next-round digest so the coordinator can carry its own coordination notes forward.
- Coordinator self-assignment: allowed and handled naturally (the wave delivers to the coordinator's session after its turn ends — busy-wait is a no-op since its turn completed).
