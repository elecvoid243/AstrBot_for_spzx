# Agent Teams Refinements — Plan 3/3 (Run Transcript + Node Dialog + Fullscreen) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land refinements phases 4-5 (spec §4/§5): the per-run message transcript table, choice-event mirroring so `TeamPorts.collect` sees (and suspends timeouts for) `ask_user_choice`, the interrupt endpoint with the independent `interrupted` node state, member speech via the chat send chain, the node dialog (`MemberTranscriptDialog`) with `InteractiveChoiceBox` and fullscreen, plus the folded-in deferral items from Plans 1-2.

**Architecture:** A new `agent_team_run_messages` table persists per-turn rows (sent/reply+parts/choice/system; stream deltas stay live-only — the reply row is the turn's final text). Runners receive an injected `transcript_sink`; message events gain `turn_id`/`run_id` and reply gains `parts`. `_consume_chat_run` mirrors choice payloads to the system stream (the single server-side chokepoint they flow through), `TeamPorts.collect` detects them (accumulator already stores them as parts, not text) and emits `choice` events; the runner replaces the fixed collect timeout with a re-arming deadline loop that suspends while a choice is pending. `interrupt` stops the member's agent run (`request_agent_stop_all`) and marks the DAG node `interrupted` (guarded against result overwrites). The dialog attaches the member's chat run stream for live fidelity (`interactive_choice` hydration, follow-up bubbles) and renders the Teams timeline, speaking through the standard chat send API.

**Tech Stack:** Python 3.10+/pytest (backend), Vue 3.3/Vuetify/vitest (frontend), pnpm.

**Spec:** `docs/superpowers/specs/2026-09-05-agent-teams-refinements-design.md` (§4 interaction dialog, §5 transcript+fullscreen; §2/§3 are Plans 1-2 — done)

## Global Constraints

- Python 3.10+; English code/comments; Google docstrings; conventional commits; `ruff format` + `ruff check` clean (backend).
- Vue 3.3: NO `defineModel`; i18n via `useModuleI18n('features/agent-teams')` with exact key parity ×3; tests `*.spec.ts`; pnpm only.
- Baselines: backend `uv run pytest tests/agent_teams/ -v` = 126 passing; frontend `pnpm exec vitest run` = 958 passing (3 pre-existing `DocumentManager.spec.ts` failures); `pnpm build` green. No regressions.
- **Interrupt semantics (user decision)**: interrupt = stop the member's current turn + node enters the independent `interrupted` state (NOT failed, no cascade, no failure policy); DAG run pauses; retry/skip ARE available on interrupted nodes; auto mode: only the member's turn stops (result recorded as the round's result); a node must NEVER be silently marked done after interruption.
- **Choice suspension**: while a member's turn has a pending choice, the collect deadline is suspended (re-armed); the node must not fail with "reply timeout" while the user is looking at the choice box. The choice part must land in the reply's `parts` (transcript) without polluting the plain text.
- **Transcript directions persisted**: `sent`, `reply` (full text + structured parts), `choice`, `system`. Stream deltas are NEVER persisted per-character; the reply row carries the turn's final text. `AgentTeamRun` row content unchanged (no streams in JSON columns).
- **Speech path**: the dialog sends via the standard chat send API targeting the member session — mid-run turns are captured server-side as follow-ups; idle turns start new runs. No new server arbitration.
- Table rows are the run view (not the session view); retention: per run, transcript rows are capped at the most recent 500 per member (enforced at append-time, oldest trimmed) — simple and bounded.

---

### Task 1: Backend — transcript table + repository

**Files:**
- Modify: `astrbot/core/db/po.py` (append after `AgentTeamRun`)
- Modify: `astrbot/core/db/__init__.py` + `astrbot/core/db/sqlite.py` (abstract + implementation; mirror the agent-team repo style from the main build — `_run_in_tx`, `session.execute(...).scalars()`)
- Test: `tests/agent_teams/test_agent_team_transcript.py` (new)

**Interfaces:**
- PO `AgentTeamRunMessage` (table `agent_team_run_messages`): `id: int PK autoincrement` (doubles as the pagination cursor), `run_id: str indexed`, `member_id: str indexed`, `node_id: str | None`, `round: int | None`, `turn_id: str`, `direction: str` (`sent|reply|choice|system`), `text: Text | None`, `parts: JSON | None`, `metadata: JSON | None`, + `TimestampMixin`.
- Repo (on `BaseDatabase` + `SQLiteDatabase`):
  - `async append_agent_team_run_message(*, run_id, member_id, node_id, round, turn_id, direction, text, parts, metadata) -> AgentTeamRunMessage` (None-able fields passed as None);
  - `async get_agent_team_run_transcript(run_id: str, member_id: str, before_id: int | None, limit: int = 50) -> list[AgentTeamRunMessage]` — `id < before_id` when given, ORDER BY id DESC, LIMIT; the caller reverses for chronological display;
  - `async trim_agent_team_run_transcript(run_id: str, member_id: str, keep: int = 500) -> int` — delete oldest rows beyond `keep` (by id), returns deleted count.

- [ ] **Step 1: failing tests** — roundtrip append/query (order + before_id + limit), trim keeps newest 500. → **Step 2: RED** (`ImportError`) → **Step 3: implement** (mirror the agent-team repo method style exactly) → **Step 4: GREEN** → **Step 5: Commit** — `feat: add agent team run transcript table and repository`

---

### Task 2: Backend — choice mirroring + collect detection

**Files:**
- Modify: `astrbot/dashboard/services/chat_service.py` (`_consume_chat_run` — the mirror)
- Modify: `astrbot/dashboard/services/agent_team_ports.py` (`collect` detects choice payloads, emits `choice` events via `emit`)
- Test: `tests/agent_teams/test_agent_team_ports.py` (append) + a focused chat_service mirror test (new or existing file — mirror the `test_agent_collab_integration.py` technique: real `WebChatQueueMgr` + scripted run consumer driving `_consume_chat_run`'s branch is heavy; prefer testing the mirror via the ports layer: push a choice payload through the system channel and assert collect's emitted events. The `_consume_chat_run` mirror itself gets a targeted unit test if a seam exists, else a documented manual-smoke item with the ports-level test as the behavioral pin)

**Interfaces:**
- Mirror (in `_consume_chat_run`, where the run payload is consumed — locate the `msg_type == "plain"` branch and the top-level type dispatch; add): when `payload.get("chain_type") == "interactive_choice"` OR `payload.get("type") == "interactive_choice_resolved"` → also `await webchat_queue_mgr.put_system_event(<conversation id>, payload)` (mirror verbatim, BEFORE/AFTER the existing handling — order irrelevant; +3 lines; comment cites spec §4.5).
- `TeamPorts.collect`: in the system-payload loop —
  - `msg_type == "plain" and chain_type == "interactive_choice"`: keep `acc.add_plain(...)` (the part lands in `build_message_parts` — transcript needs it) AND emit `{"type": "choice", "direction": "shown", "session_id", "member_id"?, "data": <parsed JSON spec dict>}` via `emit`; do NOT treat it as text (accumulator already excludes it from `plain_text` — verified);
  - `msg_type == "interactive_choice_resolved"`: emit `{"type": "choice", "direction": "resolved", "session_id", "member_id"?, "reason": payload.get("data", {}).get("reason")}`;
  - all existing branches untouched.
- `build_ports_for_test`'s emitted choice events must be asserted in specs (push a choice + resolved through the system channel, assert two emitted events with parsed spec/reason).

- [ ] **Step 1: failing tests** → **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (full backend suite; the chat_service change must not affect the chat page's own choice flow — the mirror is additive) → **Step 5: Commit** — `feat: mirror ask-user-choice events to the system stream for agent teams`

---

### Task 3: Backend — transcript sink + event enrichment (turn_id / run_id / reply parts)

**Files:**
- Modify: `astrbot/dashboard/services/agent_team_run_service.py` (`DAGRunner` + `AutoOrchestrator`: `transcript_sink` ctor param; `_execute_node` / coordinator turn / member wave build rows; message events enriched)
- Test: `tests/agent_teams/test_agent_team_transcript.py` (append runner-level tests; scripted sinks record rows)

**Interfaces:**
- `DAGRunner` / `AutoOrchestrator` ctor gains `transcript_sink: Callable[[dict], Awaitable[None]] | None = None` (single-row async callable; the service provides the DB-backed implementation; tests script recording sinks). All sink calls guarded `if self.transcript_sink: await ...` and wrapped best-effort (transcript failure must never fail the run — try/except + `logger.warning`).
- Row builder helpers on each runner: `_transcript_row(direction, text, *, member_id, node_id=None, round=None, turn_id, parts=None, metadata=None) -> dict`.
- **DAGRunner._execute_node**: `turn_id = uuid.uuid4().hex[:12]` per execution; on deliver success → sink `sent` row (text=task_text); on collect success → sink `reply` row (text=reply, parts=the collect parts — CHANGE: keep the parts from `collect_task.result()` instead of discarding `_parts`); on failure/timeout → sink `system` row (metadata `{error}`); interrupted guard unchanged (the row for an interrupted node is written by Task 4's interrupt path, not here).
- **AutoOrchestrator**: coordinator turn + member wave turns get their own `turn_id`; same sent/reply/system rows; round filled.
- **Event enrichment**: `message` events (sent/stream/reply) gain `turn_id` and `run_id`; `reply` gains `parts` (the collected parts). Stream events carry the current turn's `turn_id`.
- Existing persistence/emit statement positions unchanged (the sink calls are ADDITIVE alongside `_emit`).

- [ ] **Step 1: failing tests** — scripted sink records: sent row on deliver, reply row with text AND parts on collect, system row on failure; message events carry turn_id/run_id; reply event carries parts; auto-mode rows carry `round`. → **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** → **Step 5: Commit** — `feat: persist agent team run transcripts and enrich turn events`

---

### Task 4: Backend — interrupt endpoint, `interrupted` state, choice suspension + fold-in seeds

**Files:**
- Modify: `astrbot/dashboard/services/agent_team_run_service.py` (`interrupt_node` + service/runner wiring; `_execute_node` deadline loop replacing the fixed `wait_for` timeout; interrupted guards; `_progress` + completion; `request_stop_run` dead-task path calls `ports.close`; `resume_run` seeds pre-fail node_status events into the bus history)
- Modify: `astrbot/dashboard/services/agent_team_dag.py` (`TeamDAGError` gains `kind` attr: `"cycle" | "duplicate" | "dangling" | "missing_id"`; raise sites set it) + `agent_team_service.py` (classification switches to `e.kind` with substring fallback; `_preds` dedupe in run_service)
- Modify: `astrbot/dashboard/api/agent_teams.py` (interrupt + transcript routes)
- Test: `tests/agent_teams/test_agent_team_transcript.py` + `test_agent_team_dag.py` + `test_agent_team_stop.py` (append)

**Interfaces:**
- **Interrupt**: `AgentTeamRunService.interrupt_node(username, run_id, member_id) -> dict`:
  1. `_require_run(username, run_id)`; member = team members lookup (unknown → `AgentTeamsServiceError`);
  2. `active_event_registry.request_agent_stop_all(member["umo"])` (import from `astrbot.core.utils.active_event_registry`);
  3. DAG mode: find the member's node with `status == "running"` in the in-memory runner's node_states → set `status = "interrupted"`, `error = "用户中断"`; run → `paused`; persist + emit `node_status` + `paused {reason: "node interrupted", node_id}`; if the runner task is done (post-exit), still persist + emit (mirror the dead-task stop pattern);
  4. Auto mode: no node state — the stop lands and the running round records the partial result (spec §4.4);
  5. Returns `{"message": "已中断"}`.
- **`_execute_node` result guard**: after the collect race resolves, if `state["status"] == "interrupted"` → skip the done/failed update entirely (persist current, emit nothing new) — the interrupt path owns the terminal state.
- **`retry_node`/`skip_node`**: accept `interrupted` alongside `failed`.
- **`_progress` + completion**: counts include `interrupted`; the completion branch requires all `done/skipped` — an interrupted node keeps the run out of `completed` (lands paused via the existing incomplete-path).
- **Choice suspension**: `_execute_node`'s collect wait becomes a re-arming deadline loop:
  ```python
  collect_task = asyncio.ensure_future(self.ports.collect(member["session_id"], message_id, member["member_id"], on_event=on_event))
  stop_task = asyncio.ensure_future(self._stop_requested.wait())
  deadline = time.time() + float(self.config["reply_timeout"])
  while True:
      remaining = max(deadline - time.time(), 0.05)
      done, _ = await asyncio.wait({collect_task, stop_task}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
      if collect_task in done:
          break  # existing result handling (unchanged)
      if stop_task in done:
          ... existing abandon path ...
      if self._choice_pending:
          deadline = time.time() + 300.0  # choice pending: suspend (re-arm)
          continue
      → timeout: cancel collect_task, node failed "reply timeout" (existing semantics)
  ```
  `self._choice_pending` flipped by the `on_event` callback passed into collect (Task 2's emitted events route here: `direction == "shown"` → True + emit `node_status {waiting_choice: true}`; `"resolved"` → False). `TeamPorts.collect` gains the 4th kwarg `on_event: Callable[[dict], None] | None = None` (default None; existing callers/fixtures unaffected). **Decisive consumer contract**: `collect` emits choice events via its own `emit` (the run bus — reducer/transcript) AND invokes `on_event(event)` when provided (the runner signal) — two consumers, identical payload, no double-dispatch concerns (different sinks). The runner's closure flips the flag and emits `node_status {waiting_choice}`; it does NOT re-emit the choice event.
- **Fold-in seeds (ruled from Plans 1-2 final reviews)**:
  - `TeamDAGError` gains `kind` (`"cycle"|"duplicate"|"dangling"|"missing_id"`) set at each raise; `agent_team_service.py` classification switches to `e.kind` (substring fallback kept); `_node_ids`' missing-id raise sets `kind="missing_id"` (fixes the `edges`/`CYCLE` misclassification);
  - edges/INVALID branch gets a test (dangling edge → `path: "edges", code: "INVALID"`);
  - `DAGRunner._preds` dedupe (`dict.fromkeys` on successor lists);
  - `request_stop_run` dead-task branch: after landing `stopped`, `if runner.ports.close is not None: await runner.ports.close()` (Plan 1 residual);
  - `resume_run`: pre-failed nodes get seed `node_status` events appended to the new bus history (`bus.emit`) so a concurrently-attached monitor sees them.

- [ ] **Step 1: failing tests** — interrupt on running dag node → interrupted + paused + collect-not-overwrite (collect returns after interrupt → node stays interrupted); retry/skip on interrupted allowed; auto interrupt → member stop only; choice pending suspends timeout (choice shown, deadline re-armed ≥ reply_timeout, resolved → completes); timeout without choice still fails; missing-id → `nodes`/`INVALID` field error; dangling → `edges`/`INVALID`; `_preds` dedupe (duplicate edge → single inject block); dead-task stop closes ports; resume seed events. → **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (full backend suite) → **Step 5: Commit** — `feat: add agent team interrupt, choice-aware timeouts, and transcript-adjacent fixes`

---

### Task 5: Backend — transcript + interrupt routes

**Files:**
- Modify: `astrbot/dashboard/api/agent_teams.py` (two routes, v1 + legacy stacked; `_handle` reuse)
- Test: `tests/agent_teams/test_agent_team_api.py` (append)

**Interfaces:**
- `POST /agent_teams/runs/{run_id}/members/{member_id}/interrupt` → `service.interrupt_node(username, run_id, member_id)` (async; errors per `_handle`).
- `GET /agent_teams/runs/{run_id}/members/{member_id}/transcript?before_id=&limit=` → `service.get_transcript(username, run_id, member_id, before_id, limit)` (new service method: `_require_run` + db query + `ok({"messages": [...chronological...], "next_before_id": ...})`; empty → `{messages: [], next_before_id: null}`).
- OpenAPI: `openspec/openapi-v1.yaml` gains both paths (AgentTeams tag, `x-astrbot-scope: chat`, Ok responses) → regenerate (`cd dashboard && pnpm generate:api`, commit the generated output in the same commit).

- [ ] **Step 1: failing API tests** (auth override style from the existing api spec file; 400 unknown run; 200 shape) → **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** + generate → **Step 5: Commit** — `feat: add agent team interrupt and transcript routes`

---

### Task 6: Frontend — reducer timeline + member run stream composable

**Files:**
- Modify: `dashboard/src/composables/agentTeamsRunReducer.ts` (timeline upgrade)
- Modify: `dashboard/src/composables/agentTeamsRunReducer.spec.ts`
- Create: `dashboard/src/composables/useMemberRunStream.ts` + `.spec.ts`
- Modify: `dashboard/src/composables/agentTeamsRunReducer.ts` — the `choice` SSE event folds into the member timeline

**Interfaces:**
- Reducer: `MemberWindowState` gains `timeline: Array<{ turnId: string; nodeId?: string; kind: 'turn' | 'choice' | 'system'; direction?: string; text: string; parts?: any[]; streaming?: boolean; metadata?: any }>` (chronological append). Fold rules: `message sent` (with turn_id) → push a turn entry + keep the existing flat projection (window.sent/streamText = latest turn, AgentWindow unchanged); `message stream` → append to the entry matching turn_id (fallback: latest turn); `message reply` → finalize the turn entry (text/parts) + flat projection; `choice` → push `{kind: 'choice', direction, text: spec.prompt or reason, parts: [part]}`; `system` events (paused/error) → push `{kind: 'system', text: reason}`. Grid summary rendering (AgentWindow) keeps reading the flat projection — zero visual change there.
- `useMemberRunStream.ts`: `attach(umo: string, sessionId: string, runId: string)` → `fetchWithAuth(chatApi.resumeRunStreamUrl(runId))` → buffered SSE parse (copy the reader from `useAgentTeamsRun.ts` or extract a shared `readSseStream` util — prefer extraction to `dashboard/src/utils/sseReader.ts` used by both) → handle ONLY: top-level `interactive_choice` payloads → `applyInteractiveChoiceSse(umo, localBotRecord, normalized)` (reuse `dispatchInteractiveChoice.ts` — the store hydration makes the box interactive) and `interactive_choice_resolved` → `applyInteractiveChoiceResolved`; `user_message_saved` / `follow_up_captured` → expose as reactive `userBubbles` for the dialog; ignore everything else. Expose `{ record, userBubbles, detach }`; abort on detach; no reconnect (the dialog re-attaches on open). Export the umo used so the InteractiveChoiceBox binding is exact.
- Specs: reducer timeline folds (turn merge by turn_id, choice/system entries, flat projection compat); composable choice hydration + detach (TextEncoder stream technique from `useAgentTeamsRun.spec.ts`).

- [ ] **Step 1: failing specs** → **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (`pnpm exec vitest run src/composables src/utils`) → **Step 5: Commit** — `feat(dashboard): add run timeline and member run stream for agent teams`

---

### Task 7: Frontend — `MemberTranscriptDialog` (timeline, speak, interrupt, choice, fullscreen)

**Files:**
- Create: `dashboard/src/components/agent_teams/MemberTranscriptDialog.vue`
- Modify: `dashboard/src/components/agent_teams/AgentWindow.vue` (展开 button → emits `expand`)
- Modify: `dashboard/src/components/agent_teams/RunMonitor.vue` (hosts the dialog; wires interrupt + transcript + speak)
- Modify: `dashboard/src/api/v1.ts` `agentTeamsApi` (+ `interruptRunMember(runId, memberId)`, `memberTranscriptUrl(runId, memberId)` — the GET is cursor-paginated; add `getMemberTranscript(runId, memberId, beforeId?)`)
- Modify: `dashboard/src/composables/useAgentTeamsRun.ts` (+ `interruptMember(memberId)` action against the attached run)
- i18n ×3: `dialog.title`, `dialog.fullscreen`, `dialog.exitFullscreen`, `dialog.sendPlaceholder`, `dialog.send`, `dialog.interrupt`, `dialog.interruptConfirm`, `dialog.choiceWaiting`, `dialog.loadMore`, `dialog.speakingHint`, `dialog.empty`, `dialog.systemEntry` (+ any gaps)
- Specs: `MemberTranscriptDialog.spec.ts`

**Interfaces:**
- Props: `{ modelValue: boolean, member: {member_id, name, umo?, session_id?}, runState: TeamsRunState | null, fullscreen: boolean }`; emits `update:modelValue`, `update:fullscreen`, `interrupt`.
- Renders: header (member name + node/round info + `dialog.choiceWaiting` badge when a choice is pending + fullscreen toggle + close); timeline body = the member's reducer `timeline` (turns rendered via `messageBlocks` + `ReasoningBlock`/`MarkdownMessagePart` per the AgentWindow pattern; choice entries render `InteractiveChoiceBox` bound to `member.umo` with the stored part; system entries as muted lines) + `useMemberRunStream` live record (when attached) + 加载更多 button consuming the transcript API cursor; input bar: textarea + 发送 (`chatApi` send targeting the member session — the standard send flow; optimistic sending state; failure keeps the draft + toasts the reason via extractApiError; running hint `dialog.speakingHint` when busy) + 打断 button (visible when the member's node is running; confirm via `askForConfirmation`; calls interrupt; lands the node in 已中断).
- Fullscreen: `v-dialog` `:fullscreen="fullscreen"` + `transition` — toggled from header and from the AgentWindow expand (the window expand opens the dialog directly in fullscreen).
- AgentWindow: expand button (mdi-arrow-expand) in the header → emit; RunMonitor listens → opens the dialog for that member (state: `dialogMember`).
- Choice flow: the dialog does NOT own submission state — `InteractiveChoiceBox` + the Pinia store handle submit/cancel exactly like the chat page (`member.umo` binding); the dialog shows the `choiceWaiting` badge from the reducer's pending choice entry.

- [ ] **Step 1: failing specs** (renders timeline turns/choice/system from reducer state; send calls chatApi send with member session; interrupt confirm + call; fullscreen toggle; pagination load-more appends; choice box rendered with umo) → **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (`pnpm exec vitest run src/components/agent_teams src/composables`) + `pnpm build` → **Step 5: Commit** — `feat(dashboard): add agent teams member transcript dialog`

---

### Task 8: Frontend + backend — deferral fold-ins (§3.3 items + Plan 1/2 residuals)

**Files:**
- Modify: `dashboard/src/components/agent_teams/WorkflowEditor.vue` (①节点基本信息 + 上游/下游列表 groups in the inspector; ③保存并运行 button — saves then emits `save-and-run`; ④member strip → drawer on narrow viewports via `useDisplay()`; ⑤handle ARIA labels in `MemberFlowNode.vue` + ②missing-member card marker clickable → select node already selected — make it focus the member/exec field by opening the drawer section)
- Modify: `dashboard/src/views/AgentTeamsPage.vue` (`save-and-run` → switch to monitor tab + focus the goal input, preselecting that workflow)
- Modify: `astrbot/dashboard/services/agent_team_run_service.py` — none (T4 took the backend seeds); `MemberFlowNode.vue` handle `:aria-label`
- i18n ×3: `editor.basicInfo`, `editor.upstreamList`, `editor.downstreamList`, `editor.saveAndRun`, + labels
- Specs: WorkflowEditor (groups render; save-and-run emit; narrow drawer), MemberFlowNode (aria)

- [ ] **Step 1: failing specs** → **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (full frontend suite + build) → **Step 5: Commit** — `feat(dashboard): fold editor redesign deferrals into agent teams`

---

### Task 9: Verification pass

- [ ] **Step 1:** backend `uv run pytest tests/agent_teams/ -v` (126 prior + new) + `ruff format . && ruff check .`; frontend `pnpm exec vitest run` + `pnpm build`; `pnpm generate:api` idempotency (Task 5 committed the regenerated output).
- [ ] **Step 2:** manual smoke (deferred to the human): mid-DAG-run a member calls `ask_user_choice` → grid badge + dialog box → click → agent continues (no reply-timeout failure); dialog speech mid-run → follow-up merged (agent acknowledges); 打断 → node 已中断 → change task → retry; refresh → transcript pagination restores the timeline; fullscreen reading; save-and-run.

---

## Plan Notes

- **Deferred (not this plan):** Plan 1's parked collab registrar bug (legacy, retired); the Plan-1 `setOnAttachFailed` unmount cleanup (fold when touched); TeamDAGError substring-classification full removal (kind attr added in T4 covers the misclassification; substring fallback stays).
- The choice mirror is additive to the chat page's own flow: system-channel subscribers other than Teams collect ignore the mirrored payloads (their handlers filter by message_id/type).
- `_choice_pending` is per-node-execution state on the runner — concurrent member waves each have their own `_execute_node` scope (local closure, not runner-wide).
- The transcript sink is best-effort by contract: failures log and drop rows, never fail the run (test pins one sink-throwing case).
