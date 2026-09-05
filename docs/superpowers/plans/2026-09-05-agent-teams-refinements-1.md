# Agent Teams Refinements — Plan 1/3 (Error Visibility + Node Execution Config) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land refinements phases 1-2: make every backend error reason visible in the frontend (8 audited spots + monitor error rendering), and give workflow nodes a first-class `execution` block (config profile + persona override + tools/skills three-state overrides) resolved per-turn via a trusted execution-token registry.

**Architecture:** Nodes carry an optional `execution` field. At dispatch time the runner registers a `NodeExecutionBinding` in a core-side dumb registry and delivers the turn with a short-lived token; the WebChat adapter passes it as an event extra; `EventBus.dispatch` resolves it (umo-validated) to pick the node's config profile's own PipelineScheduler (each profile's scheduler already carries that profile's agent config — verified `core_lifecycle.py:455-468`); `build_main_agent` reads the resolved binding to apply persona override (top of the priority chain), thread `config_id` into the persona default tier, and apply three-state tools/skills overrides after all other tool injections. Token unregistration follows the same exit-path discipline as `team_dispatch`/`team_finish` registration.

**Tech Stack:** Python 3.10+/pytest (backend), Vue 3.3/Vuetify/vitest (frontend), pnpm.

**Spec:** `docs/superpowers/specs/2026-09-05-agent-teams-refinements-design.md` (§2 node execution config, §3.1 error visibility items 1-2+4; §3.2 edges, structured field errors for workflow validation, and the editor redesign are Plans 2-3 — NOT this plan)

## Global Constraints

- Python 3.10+; English code/comments; Google docstrings (`Args:`/`Returns:`/`Raises:`) on public functions/methods; conventional commits; `ruff format` + `ruff check` clean before each backend commit.
- Vue 3.3: NO `defineModel`; all UI strings via `useModuleI18n('features/agent-teams')` with exact key parity across zh-CN/en-US/ru-RU; frontend tests are `*.spec.ts` only; pnpm only.
- **Token security invariants** (§2.3): tokens are server-issued uuid4 hex, bound to `{run_id, team_id, member_id, node_id, umo, owner_username, config_id, persona_id?, tools?, skills?}`; resolution validates `umo` (foreign/unknown tokens are stripped with a warning, never fatal); unregistered on EVERY exit path of the turn (deliver failure, collect timeout/stop/abort, crash) — same discipline as the team tool registry; ordinary chat requests cannot grant themselves node config.
- **Config invariants**: `config_id` must exist in `AstrBotConfigManager.confs` at validation and at deliver time (missing → node `failed`, never a silent fallback to default); `AgentTeamRun` stores only IDs, never profile content; node `execution` is frozen into `graph_snapshot` (existing mechanism) so hot resume re-validates.
- Tools/skills three-state semantics (persona PO convention): `null` = inherit, `[]` = disable all, `[...]` = allowlist only.
- Persona priority with execution (spec §2.4): node `execution.persona_id` > session rule > member conversation binding > profile(config_id) default > platform default.
- Baselines: backend `uv run pytest tests/agent_teams/ -v` = 70 passing (full-repo has 39 pre-existing Windows failures, verified unrelated); frontend `pnpm exec vitest run` = 871 passing (3 pre-existing `DocumentManager.spec.ts` fullscreen failures); both baselines must not regress.

---

### Task 1: `extractApiError` utility (frontend)

**Files:**
- Create: `dashboard/src/utils/extractApiError.ts`
- Create: `dashboard/src/utils/extractApiError.spec.ts`

**Interfaces:**
- Produces: `extractApiError(err: unknown, fallback: string): { message: string; fields: Array<{ path: string; message: string }> }`
  - Resolution order: (1) `err?.response?.data?.message` (axios HTTP error with our envelope); (2) `err?.response?.data?.data?.fields` → field array (structured validation, Plan-2 consumption — pass through if present); (3) `err` is a plain string (the 429 case: `normalizeAxiosError` rejects with `data.message` as a string — see `dashboard/src/api/http.ts:50-107`); (4) `err instanceof Error` → `err.message`; (5) `fallback`.
  - `message` is always a non-empty string; `fields` defaults to `[]`.
- Pure module, no Vue imports.

- [ ] **Step 1: Write the failing spec** — cases: axios-shaped error with envelope message; error with `data.fields` (returns both message and fields); string rejection (429); generic Error; unknown (fallback used); empty-string envelope message falls back.

- [ ] **Step 2: RED** (`pnpm exec vitest run src/utils/extractApiError.spec.ts` → module not found).

- [ ] **Step 3: Implement** the pure function.

- [ ] **Step 4: GREEN** + full `pnpm exec vitest run src/utils` green.

- [ ] **Step 5: Commit** — `feat(dashboard): add extractApiError utility`

---

### Task 2: Apply error visibility to the audited spots (frontend)

**Files:**
- Modify: `dashboard/src/composables/useAgentTeams.ts` (`unwrapEnvelope` catch, ~L62-71)
- Modify: `dashboard/src/components/agent_teams/TeamCreateDialog.vue` (save path ~L328-351 — only if it has its own catch; the composable fix may cover it — audit, don't double-toast)
- Modify: `dashboard/src/components/agent_teams/MemberAddDialog.vue` (save catch ~L168-169 toasts generic `errors.saveFailed`; `loadOptions` ~L115-131 silently swallows)
- Modify: `dashboard/src/views/AgentTeamsPage.vue` (`onRemoveMember` catch ~L170-172 generic)
- Modify: `dashboard/src/components/agent_teams/RunsHistory.vue` (HTTP catch ~L126-128 generic)
- Spec test files for each touched component

**Rules:**
- Every catch that toasts a user-facing failure switches to `extractApiError(err, <existing fallback i18n string>).message`.
- Envelope branches that are dead for HTTP errors (e.g. `MemberAddDialog`'s `res.data.status === 'error'` comment claiming HTTP-200 errors) — keep the branch if harmless but FIX the misleading comments to state the real contract: "backend validation errors arrive as HTTP 400/409 with an error envelope body; axios rejects and extractApiError surfaces `response.data.message`".
- No double-toast: if the composable already toasts and returns null, component catches must not toast again (preserve the TeamCreateDialog guard pattern).
- `useAgentTeamsRun.ts` already correct — do not touch except to switch it onto `extractApiError` IF that simplifies it without behavior change; otherwise leave.

- [ ] **Step 1: Update/add component specs** — for each touched spot, a test that mocks the API to reject with `{ response: { data: { message: "后端具体原因" } } }` and asserts the toast received "后端具体原因" (not "Request failed with status code 400"). Where a spec already asserts the generic message, update it.
- [ ] **Step 2: RED** → **Step 3: apply the changes** → **Step 4: GREEN** — `pnpm exec vitest run src/components/agent_teams src/composables src/views src/utils`; full suite for regressions (3 known DocumentManager failures acceptable).
- [ ] **Step 5: Commit** — `fix(dashboard): surface backend error reasons across agent teams`

---

### Task 3: Monitor error rendering (frontend)

**Files:**
- Modify: `dashboard/src/components/agent_teams/AgentWindow.vue` (new optional prop `nodeError?: string | null`; render an inline error block under the header when present — member-color-agnostic red tint, `tm('monitor.nodeError')` label + the error text)
- Modify: `dashboard/src/components/agent_teams/RunMonitor.vue`:
  - Pass `nodeError` per window from `runState.nodeStates[nodeId].error` — mapping member→its current/most recent node (dag mode: nodes reference members; find the member's node in `runState.nodeStates` with status failed/interrupted; auto mode: from the round results if present — v1 dag-only is acceptable, document it);
  - Top warning banner when `runState.lastError` or `stoppedReason` (excluding `"done"` and `"user stop"`) is set — dismissible, `tm('monitor.runError')`;
  - SSE attach: `useAgentTeamsRun` reconnect exhaustion currently console-only — add an `onError` callback prop or emit: simplest is a new optional `onAttachFailed?: (runId: string) => void` on the composable's `attach`, invoked when attempts are exhausted; `RunMonitor` wires it to a toast (`tm('monitor.attachFailed')`). Modify `useAgentTeamsRun.ts` minimally (one callback field + invocation).
- Modify: `dashboard/src/composables/useAgentTeamsRun.ts` (attach-failure callback)
- i18n keys ×3: `monitor.nodeError`, `monitor.runError`, `monitor.attachFailed`
- Modify: `dashboard/src/components/agent_teams/RunMonitor.spec.ts` (+banner/carousel cases), `AgentWindow` coverage via RunMonitor spec

**Rules:** reducer already folds `nodeStates[].error`/`lastError`/`stoppedReason` (Plan 2 T3) — no reducer change. Banner must not render for normal `done`/`user stop` completion.

- [ ] **Step 1: failing specs** (banner shows on lastError; hidden on "done"; nodeError forwarded to AgentWindow; attach failure toasts) → **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (`pnpm exec vitest run src/components/agent_teams src/composables`) + `pnpm build`.
- [ ] **Step 5: Commit** — `feat(dashboard): render agent teams run and node errors`

---

### Task 4: Core — `AgentTeamExecutionRegistry` (`agent_team_execution.py`)

**Files:**
- Create: `astrbot/core/agent_team_execution.py`
- Test: `tests/agent_teams/test_agent_team_execution.py`

**Interfaces:**
- `@dataclass NodeExecutionBinding`: `run_id: str`, `team_id: str`, `member_id: str`, `node_id: str | None`, `umo: str`, `owner_username: str`, `config_id: str | None`, `persona_id: str | None = None`, `tools: list[str] | None = None`, `skills: list[str] | None = None`.
- `class AgentTeamExecutionRegistry` (class-level dict, mirroring `AgentTeamToolRegistry` style in `astrbot/core/agent_team_tools.py`):
  - `register(binding: NodeExecutionBinding) -> str` — issues uuid4 hex token;
  - `resolve(token: str, *, umo: str | None = None) -> NodeExecutionBinding | None` — unknown token → None; `umo` provided and mismatched → None (forged/foreign token defense);
  - `unregister(token: str) -> None` — idempotent (`pop(token, None)`).

- [ ] **Step 1: failing tests** — register/resolve roundtrip; unknown token → None; umo mismatch → None; unregister idempotent; resolve does NOT unregister (runner owns lifecycle).
- [ ] **Step 2: RED** → **Step 3: implement** (module docstring explains the trust model per spec §2.3) → **Step 4: GREEN** (`uv run pytest tests/agent_teams/test_agent_team_execution.py -v`) → **Step 5: Commit** — `feat: add agent team node execution registry`

---

### Task 5: Core — EventBus token routing + webchat adapter passthrough

**Files:**
- Modify: `astrbot/core/event_bus.py` (`dispatch`, lines 39-56)
- Modify: `astrbot/core/platform/sources/webchat/webchat_adapter.py` (after the `team_context` passthrough block, ~line 308)
- Test: `tests/agent_teams/test_agent_team_execution.py` (append EventBus-level tests)

**Interfaces:**
- Adapter (+3 lines, identical pattern to `team_context`): `execution_token` payload key → `message_event.set_extra("execution_token", ...)`.
- `EventBus.dispatch`: BEFORE the existing `conf_info = ...` resolution:
  ```python
  execution_binding = None
  token = event.get_extra("execution_token")
  if token:
      from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
      binding = AgentTeamExecutionRegistry.resolve(token, umo=event.unified_msg_origin)
      if binding is None:
          logger.warning(
              "agent team execution token invalid or foreign (umo=%s); ignoring",
              event.unified_msg_origin,
          )
      else:
          execution_binding = binding
          event.set_extra("agent_team_execution", binding)
          if binding.config_id:
              scheduler = self.pipeline_scheduler_mapping.get(binding.config_id)
              if scheduler is None:
                  logger.warning(
                      "agent team execution config %r not found; falling back to umo routing",
                      binding.config_id,
                  )
  # ... existing conf_info resolution ...
  if scheduler is None:
      scheduler = self.pipeline_scheduler_mapping.get(conf_id)
  if not scheduler:
      ... existing error continue ...
  ```
  (The resolved binding extra is what `build_main_agent` consumes; the runner owns token unregistration — EventBus must NOT unregister.)
- Tests: dispatch is hard to instantiate standalone — test the resolution contract through `AgentTeamExecutionRegistry` + a focused integration: build a minimal `EventBus` with a stubbed `pipeline_scheduler_mapping` (default + one profile scheduler recording `execute` calls) and a fake event carrying `execution_token`; assert the profile scheduler received the event and the `agent_team_execution` extra is set; assert foreign umo token falls back to default scheduler. If `AstrMessageEvent` is too heavy to construct, subclass it minimally (the repo has `CronMessageEvent` as a synthetic-event precedent, `astrbot/core/cron/events.py:14`).

- [ ] **Step 1: failing tests** → **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (full `uv run pytest tests/agent_teams/ -v` — 70 prior stay green; the new dispatch path must not affect existing events which carry no token) → **Step 5: Commit** — `feat: route agent team node turns by execution token`

---

### Task 6: Core — persona/tools/skills overrides in `build_main_agent`

**Files:**
- Modify: `astrbot/core/persona_mgr.py` (`resolve_selected_persona`: new optional keyword param `config_id: str | None = None`; in the default tier replace `cfg = self.acm.get_conf(umo)` with `self.acm.confs.get(config_id) or self.acm.get_conf(umo)` when config_id provided)
- Modify: `astrbot/core/astr_main_agent.py` (`_ensure_persona_and_skills` — three insertion points)
- Test: `tests/agent_teams/test_agent_team_execution.py` (append; test `_ensure_persona_and_skills` directly with a fake `plugin_context`/`req`/`event` — the fake pattern from `test_agent_team_tools.py`'s merge test)

**Interfaces (normative):**
1. **Read the binding** at the top of the persona block (after the `req.conversation` guard, ~line 556):
   ```python
   execution = event.get_extra("agent_team_execution")
   node_persona_id = execution.get("persona_id") if execution else None
   node_config_id = execution.get("config_id") if execution else None
   ```
2. **Persona override** replacing the `resolve_selected_persona` call (~line 558): if `node_persona_id` → `persona = plugin_context.persona_manager.get_persona_v3_by_id(node_persona_id)`; `persona_id = node_persona_id`; if persona is None → log warning and fall through to the normal resolution call. Else → the existing call with the new `config_id=node_config_id` argument.
3. **Skills override** — AFTER the workspace-skills merge block (immediately before `if skills:` at ~line 610), apply node skills when `execution` present and `execution.get("skills") is not None` (three-state, same shape as the persona filter, applied to the merged list).
4. **Tools override** — at the very END of `_ensure_persona_and_skills` (after the `_apply_agent_team_tools(req, event)` call at ~line 711, so node whitelists bind ALL injected tools including subagent/team tools):
   ```python
   node_tools = execution.get("tools") if execution else None
   if node_tools is not None and req.func_tool is not None:
       allowed = set(node_tools)
       req.func_tool = ToolSet(
           tools=[tool for tool in list(req.func_tool) if tool.name in allowed]
       )
   ```
   (`ToolSet` is iterable — `tool.py:360`; `[]` empties it — three-state honored.)
- `resolve_selected_persona`'s new `config_id` param must be keyword-only and default None so all existing callers are unaffected.

- [ ] **Step 1: failing tests** — (a) node persona override: binding with `persona_id` → `persona["prompt"]` of that persona lands in `req.system_prompt` and `resolve_selected_persona` NOT called (monkeypatch-count); persona not found → falls back to resolution; (b) config_id threading: binding with `config_id` (no persona override) → `resolve_selected_persona` receives `config_id`; assert via monkeypatched persona_manager; (c) tools three-state: binding `tools: []` → `req.func_tool` empty; `tools: ["x"]` → only x; `tools: None` → persona default untouched; override applies AFTER team tools merge (register a fake team tool via the tool registry and assert the whitelist can keep or drop it); (d) skills override: `[]` empties the skills prompt; allowlist filters.
- [ ] **Step 2: RED** → **Step 3: implement** (read the current `_ensure_persona_and_skills` block at astr_main_agent.py:541-716 first; keep every existing branch intact) → **Step 4: GREEN** (full backend suite; 70 prior + new) → **Step 5: Commit** — `feat: apply node execution persona and capability overrides in main agent`

---

### Task 7: Backend — workflow `execution` schema validation + config checks

**Files:**
- Modify: `astrbot/dashboard/services/agent_team_service.py` (`_validate_workflow_payload` + node-level validation helper; ctor already holds `core_lifecycle`)
- Modify: `astrbot/dashboard/services/agent_team_run_service.py` (`AgentTeamRunService` + `DAGRunner` ctor: `config_checker: Callable[[str], bool] | None = None`; `resume_run` post-rebuild check; `_execute_node` pre-deliver check)
- Modify: `astrbot/dashboard/api/app.py` (wire `config_checker=lambda cid: cid in core_lifecycle.astrbot_config_mgr.confs` — verify the attribute name on `AstrBotCoreLifecycle` first: the EventBus is constructed with `self.astrbot_config_mgr`, core_lifecycle.py:280)
- Test: `tests/agent_teams/test_agent_team_execution.py` (append service/runner-level tests)

**Interfaces:**
- `_validate_workflow_payload`: for each node, `execution = node.get("execution")`; if present:
  - `config_id` must be a non-empty string resolving in `self.core_lifecycle.astrbot_config_mgr.confs` → else `AgentTeamsServiceError(f"节点 {node_id} 的配置档案不存在: {config_id}")`;
  - `persona_id` (if set) must resolve via `self.core_lifecycle.persona_manager.get_persona_v3_by_id` → else error;
  - `tools`/`skills` (if set and non-empty) must be lists of non-empty strings; unknown tool names are NOT rejected at save time (tool availability is dynamic) — document in the docstring.
- `AgentTeamService` needs `self.core_lifecycle.astrbot_config_mgr` / `.persona_manager` — verify attribute paths by reading core_lifecycle (both exist: `astrbot_config_mgr` used at :280, `persona_mgr` at :248 — note the run service may receive `persona_manager` name差异; match the actual attributes).
- `DAGRunner` ctor gains `config_checker: Callable[[str], bool] | None = None`; `_execute_node` before the busy-wait: if the node's `execution.config_id` is set and `self.config_checker` returns False → node `failed` (error `"配置档案已删除"`), persist + emit, return (mirrors the existing member-missing path).
- `resume_run` (dag branch): after rebuilding node_states, for each pending node whose `execution.config_id` fails `config_checker` → mark `failed` with the same error before spawning (so a deleted profile surfaces immediately on resume).
- `AgentTeamRunService` ctor passes `config_checker` into `_build_runner`; app.py wiring (Task 7 scope).

- [ ] **Step 1: failing tests** — save workflow with unknown config_id → error naming the node; unknown persona_id → error; valid execution saves; runner with `config_checker=lambda cid: False` → node failed "配置档案已删除" without deliver; resume with checker False → nodes pre-failed. → **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (full backend suite) → **Step 5: Commit** — `feat: validate and enforce node execution config for agent teams`

---

### Task 8: Backend — runner token wiring (`DAGRunner` + `TeamPorts`)

**Files:**
- Modify: `astrbot/dashboard/services/agent_team_ports.py` (`deliver` gains 4th keyword param `execution_token: str | None = None` → payload key `execution_token`; update the `TeamPorts.deliver` Callable type to match)
- Modify: `astrbot/dashboard/services/agent_team_run_service.py` (`DAGRunner._execute_node`: build binding + register + deliver(token) + unregister in a `finally` spanning deliver+collect race; `AutoOrchestrator` deliver calls updated for the new signature with `execution_token=None`)
- Modify: `astrbot/dashboard/api/app.py`: none (token lifecycle is runner-internal)
- Test: `tests/agent_teams/test_agent_team_execution.py` (append) + fix existing scripted-ports fixtures (`test_agent_team_dag_runner.py`, `test_agent_team_auto.py`, `test_agent_team_stop.py`, `test_agent_team_resume.py` — their fake `deliver` functions must accept the 4th param)

**Interfaces:**
- `_execute_node` (after the busy-wait + status check, wrapping the existing deliver+collect race):
  ```python
  execution = node.get("execution") or {}
  token = None
  if execution:
      binding = NodeExecutionBinding(
          run_id=self.run_id, team_id=self.team_id,
          member_id=member["member_id"], node_id=node_id,
          umo=member["umo"], owner_username=self.username,
          config_id=execution.get("config_id"),
          persona_id=execution.get("persona_id"),
          tools=execution.get("tools"), skills=execution.get("skills"),
      )
      token = AgentTeamExecutionRegistry.register(binding)
  try:
      message_id = await self.ports.deliver(
          member["session_id"], task_text, None, execution_token=token
      )
      ... existing collect race + result handling ...
  finally:
      if token:
          AgentTeamExecutionRegistry.unregister(token)
  ```
  (Import from `astrbot.core.agent_team_execution`; keep every existing persistence/emit statement inside the try exactly where they are today.)
- AutoOrchestrator: no execution blocks exist — calls pass `execution_token=None` (or rely on the default); note in a comment that auto-mode turns run under member defaults by design (spec §2.2).
- Test additions: runner with a node carrying `execution` → inside the scripted `deliver`, `AgentTeamExecutionRegistry.resolve(token_from_payload, umo=member umo)` returns the binding with the node's config_id; after the node settles → registry empty; deliver-failure path still unregisters (reuse the Plan-1 deliver-exception test shape).

- [ ] **Step 1: failing tests + fixture signature updates** → **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (full backend suite — all 70+ prior stay green; the fixture signature updates are mechanical) → **Step 5: Commit** — `feat: deliver agent team node turns with execution tokens`

---

### Task 9: Frontend — node `execution` property group (editor inspector)

**Files:**
- Modify: `dashboard/src/components/agent_teams/WorkflowEditor.vue` (inspector: new "执行配置" collapsible group under the member select — config profile `v-select` (options from `configProfileApi.list` → `{id, name}`; default option "跟随默认配置"), persona override (`PersonaSelector` from `@/components/shared/PersonaSelector.vue`, with "跟随档案默认" empty value), tools three-state + `toolApi.listTools` multi-select, skills three-state + `skillApi` multi-select; save payload serializes `execution` per node — omit the whole block when all fields are inherit/null)
- Modify: `dashboard/src/utils/dagCheck.ts` (none — execution is not graph structure)
- Modify: `dashboard/src/api/v1.ts` `agentTeamsApi` — none (config/tools/skills lists come from existing facades: `configProfileApi.list` v1.ts:348, `toolApi.listTools` v1.ts:1142, `skillApi` v1.ts:1616 — verify exact method names by reading them)
- i18n keys ×3: `editor.executionConfig`, `editor.configProfile`, `editor.configProfileDefault`, `editor.personaOverride`, `editor.personaFollowProfile`, `editor.toolsOverride`, `editor.toolsInherit`, `editor.toolsDisableAll`, `editor.toolsAllowlist`, `editor.skillsOverride` (+ whatever labels the three-state UI needs)
- Modify: `WorkflowEditor.spec.ts` (execution group renders; save payload carries execution; inherit omits it; three-state toggles)
- Backend contract note: the save payload is `{name, graph: {nodes: [{id, member_id, task, execution?}], edges}, layout}` — server validation lands via Task 7; editor surfaces `extractApiError(...).fields` (Task 1 shape) by mapping `path: "nodes.n2.execution.config_id"` → focus that node (field-level focus UI is Plan 2; here only render the message list)

- [ ] **Step 1: failing specs** → **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (`pnpm exec vitest run src/components/agent_teams src/utils`) + `pnpm build` → **Step 5: Commit** — `feat(dashboard): add node execution config to workflow editor`

---

### Task 10: Verification pass

- [ ] **Step 1:** backend `uv run pytest tests/agent_teams/ -v` (all green; baseline 70 + new) and `uv run pytest tests/ -q` triaged against the 39-failure Windows baseline; `ruff format . && ruff check .`.
- [ ] **Step 2:** frontend `pnpm exec vitest run` (871 + new; only the 3 known DocumentManager failures) + `pnpm build`.
- [ ] **Step 3:** manual smoke (deferred to the human): node bound to a profile with `max_steps=3` finishes in 3 steps; node persona override shows the overridden persona's prompt behavior; whitelist `tools: []` → the member agent has no tools; stop mid-run leaves no lingering token (check logs).

---

## Plan Notes

- **Out of scope (Plans 2-3):** edge-driven result injection + `unreferenced_placeholders` + workflow field-level structured errors UI (Plan 2 §3.2); editor visual redesign (Plan 2 §3.3); `agent_team_run_messages` transcript table + turn_id/seq/parts event enrichment + choice mirroring + dialog + fullscreen (Plan 3 §4-5).
- The `execution_token` payload key is internal-only: the adapter copies it to an event extra verbatim; it must never be accepted from user-facing chat sends (chat send payloads are constructed server-side from the POST body and never carry this key — verified `build_chat_stream` shape).
- AutoOrchestrator turns run under member defaults by design (no node execution in auto mode) — spec §2.2.
- The existing scripted-ports fixtures across 4 test files take `(session_id, text, context)` — Task 8's signature change is the only mechanical breaking change; update all of them in the same commit.
