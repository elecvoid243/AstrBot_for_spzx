# Agent Teams Refinements — Plan 2/3 (Edges = Data Flow + Field-Level Validation + Editor Redesign) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land refinements phases 3+6 (spec §3.2/§3.3/§3.1-item-3): edges carry data-flow semantics (direct predecessors' results auto-injected unless explicitly referenced), workflow validation errors become field-level structured objects mapped back to nodes in the editor, and the editor gets the professional workbench redesign (custom member node cards, drag-drop, right inspector drawer, canvas chrome).

**Architecture:** Backend: `render_task` gains `auto_inject_predecessors` (pure function, caller filters done-with-result and not-explicitly-referenced preds); `AgentTeamsServiceError` gains optional `field_errors` collected across the whole workflow validation pass (all problems at once, not first-fail) and surfaced in the error envelope's `data.fields`. Frontend: `MemberFlowNode.vue` custom Vue Flow node (handles, member color, status ring, error tooltip) registered via `nodeTypes` in `TeamsFlowCanvas` for BOTH edit and monitor modes; member panel drag-drop; Background/Controls/MiniMap (separate `@vue-flow/*` packages — core 1.48.2 does NOT ship them); inspector moves to a right drawer with variable chips and click-to-focus field errors.

**Tech Stack:** Python 3.10+/pytest, Vue 3.3/Vuetify/vitest, `@vue-flow/core` 1.48.2 + new deps `@vue-flow/background`, `@vue-flow/minimap`, `@vue-flow/controls`, pnpm.

**Spec:** `docs/superpowers/specs/2026-09-05-agent-teams-refinements-design.md` (§3.2 edges, §3.3 editor redesign, §3.1 item 3 field-level errors; §2 config and §3.1 items 1-2/4 are Plan 1 — done; §4/§5 transcript+dialog are Plan 3 — NOT this plan)

## Global Constraints

- Python 3.10+; English code/comments; Google docstrings; conventional commits; `ruff format` + `ruff check` clean (backend).
- Vue 3.3: NO `defineModel`; all UI strings via `useModuleI18n('features/agent-teams')` with exact key parity ×3 locales; tests `*.spec.ts`; pnpm only.
- Baselines: backend `uv run pytest tests/agent_teams/ -v` = 111 passing; frontend `pnpm exec vitest run` = 907 passing (3 pre-existing `DocumentManager.spec.ts` fullscreen failures); `pnpm build` green. No regressions.
- **Edge-injection semantics (§3.2)**: only predecessors with `status == "done"` AND non-empty `result` are injected; predecessors explicitly referenced via `{{<node_id>}}` in the template are skipped (no double injection); ALL preds explicitly referenced → no `[上游结果]` block; user task text is never modified on disk; per-result truncation reuses `inject_max_length` with the existing tail-truncation marker; skipped/interrupted preds never injected.
- **Field-error semantics (§3.1 item 3)**: one validation pass collects ALL problems; `field_errors` entries are `{path, code, message}` with paths `name`, `nodes.{id}.task`, `nodes.{id}.member_id`, `nodes.{id}.execution.config_id|persona_id`, `edges`; existing error-message substrings (e.g. `cycle`, `节点绑定的成员不存在`) move into the field `message` values so old assertions migrate; single non-workflow errors keep their current shape (no field_errors).
- Unconnected `{{<node_id>}}` references (target exists in graph but is not a direct predecessor) are an **editor inline warning only** — never a save blocker, never a runtime error (results of any done node resolve at runtime).
- Custom Vue Flow nodes: `nodeTypes` must be non-reactive (module-level `markRaw`); the member node component must render without a Vue Flow provider in tests (handles conditional or stubbed — implementer's choice, documented).
- Working directory for frontend: `dashboard/`.

---

### Task 1: Backend — field-level workflow validation errors

**Files:**
- Modify: `astrbot/dashboard/services/agent_team_service.py` (`AgentTeamsServiceError`, `_validate_workflow_payload`, `validate_member_bindings` integration)
- Modify: `astrbot/dashboard/api/agent_teams.py` (`_handle`)
- Test: `tests/agent_teams/test_agent_team_workflow_service.py` (append/adjust) + `tests/agent_teams/test_agent_team_api.py` (fields in envelope)

**Interfaces:**
- `class AgentTeamsServiceError(Exception)`: `__init__(self, message: str, field_errors: list[dict] | None = None)`; stores `self.field_errors = field_errors or []`. All existing single-string raise sites keep working.
- `_validate_workflow_payload(team, graph)`: rework from first-raise to **collect-all**:
  - `nodes.{id}.task` / `REQUIRED` — task template empty (message keeps the current wording `节点 {id} 缺少任务模板`);
  - `nodes.{id}.member_id` / `NOT_FOUND` — member not on roster (message keeps `节点绑定的成员不存在: {member_id}` — migrate `validate_member_bindings`'s aggregate raise into per-node field errors);
  - `nodes.{id}.execution.config_id` / `NOT_FOUND` and `nodes.{id}.execution.persona_id` / `NOT_FOUND` — migrate `_validate_node_execution`'s raises (Plan 1 T7) into field errors with the same messages;
  - `edges` / `CYCLE` — `validate_dag`'s cycle error (message keeps `cycle detected...`);
  - `edges` / `INVALID` — dangling edge (message keeps `edge references unknown node: ...`);
  - `nodes` / `DUPLICATE` — duplicate node id;
  - node-count-over-max and missing-task-shape errors stay as plain raises (structural, not field-mappable) — EXCEPT keep collecting where a node id exists.
  - If any field errors collected → raise `AgentTeamsServiceError(f"工作流校验失败（{len(field_errors)} 项）", field_errors=field_errors)`. The per-field `message` values MUST retain the substrings existing tests assert (`cycle`, `节点绑定的成员不存在`) — existing tests are updated in this task to assert via `exc.field_errors` instead of `match=` where the message moved.
- `_handle(e)` in `agent_teams.py`: `data = {"fields": e.field_errors} if e.field_errors else None`; `JSONResponse(error(str(e), data), status_code=status)` — `error()` already supports `data` (`responses.py:18-21`).

- [ ] **Step 1: Write/adjust failing tests** — save workflow with empty task + unknown member + cycle simultaneously → ONE raise with 3+ field_errors covering all paths (collect-all proof); each migrated message substring present in the right field; envelope test: workflow save rejection body `data.fields` present; non-workflow errors (e.g. bad coordinator) have `field_errors == []` / absent data.
- [ ] **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (`uv run pytest tests/agent_teams/ -v`, 111 prior + adjusted + new) → **Step 5: Commit** — `feat: collect field-level workflow validation errors for agent teams`

---

### Task 2: Backend — edges as data flow (auto-inject predecessors)

**Files:**
- Modify: `astrbot/dashboard/services/agent_team_dag.py` (`referenced_placeholders` helper + `render_task` keyword param)
- Modify: `astrbot/dashboard/services/agent_team_run_service.py` (`DAGRunner._execute_node` builds the inject list; `AutoOrchestrator` untouched — no graph)
- Test: `tests/agent_teams/test_agent_team_dag.py` (append) + `tests/agent_teams/test_agent_team_dag_runner.py` (append integration)

**Interfaces:**
- `referenced_placeholders(template: str) -> set[str]` — public; `set(_PLACEHOLDER_RE.findall(template))`.
- `render_task(template, run_input, results, max_length, *, auto_inject_predecessors: list[tuple[str, str]] | None = None) -> str`:
  - Keyword-only, default None → existing behavior byte-identical.
  - After the placeholder substitution, for each `(node_id, display_name)` in the list WHERE `node_id in results` AND `node_id not in referenced_placeholders(template)` (the caller may pre-filter, but render_task re-checks defensively), append a block:
    ```text
    [上游结果]
    ◆ {display_name} ({node_id})：
    {result tail-truncated to max_length with the existing marker}
    ```
    Multiple predecessors → one block per predecessor, joined with a blank line, appended after the substituted template separated by `\n\n`. Zero eligible predecessors → nothing appended.
  - Google docstring documenting the §3.2 semantics.
- `DAGRunner._execute_node`: build the inject list BEFORE calling render_task:
  ```python
  referenced = referenced_placeholders(node.get("task", ""))
  inject_list: list[tuple[str, str]] = []
  for pred in self._preds.get(node_id, []):
      if pred in referenced:
          continue
      p_state = self.node_states.get(pred) or {}
      if p_state.get("status") != "done" or not p_state.get("result"):
          continue
      pred_node = self._nodes_by_id.get(pred, {})
      member = self._member_by_id(pred_node.get("member_id") or "")
      display = pred_node.get("title") or (member or {}).get("name") or pred
      inject_list.append((pred, display))
  task_text = render_task(..., auto_inject_predecessors=inject_list or None)
  ```
  `self._nodes_by_id = {n["id"]: n for n in graph.get("nodes", [])}` added to `__init__` (check whether `_run_loop` already builds one locally — reuse/consolidate). Auto mode: untouched.

- [ ] **Step 1: failing tests** — dag unit: pred injected with display name; explicitly-referenced pred skipped; all-referenced → no block; pred without result/skipped skipped; per-result truncation marker; block appended after template. Runner integration: 3-node chain (n1→n2→n3), n2 template WITHOUT `{{n1}}` → its delivered task contains `[上游结果]` and n1's result; n3 template WITH `{{n1}}` → n1 NOT re-injected, n2 IS (auto); run completes. → **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (full backend suite) → **Step 5: Commit** — `feat: auto-inject predecessor results along agent team workflow edges`

---

### Task 3: Frontend — `MemberFlowNode` custom node + canvas chrome

**Files:**
- Add deps: `cd dashboard && pnpm add @vue-flow/background @vue-flow/minimap @vue-flow/controls`
- Create: `dashboard/src/components/agent_teams/MemberFlowNode.vue`
- Modify: `dashboard/src/components/agent_teams/TeamsFlowCanvas.vue` (nodeTypes registration, `displayNodes` sets `type: 'member'` + rich data, `displayEdges` computed with arrow markers + monitor running animation, Background/Controls/MiniMap)
- Modify: `dashboard/src/components/agent_teams/RunMonitor.vue` (dagNodes data enriched: memberName/color/taskPreview/status/error), `WorkflowEditor.vue` (graphNodes data enriched; full layout rework lands in Task 4 — this task only feeds richer node data)
- Specs for `MemberFlowNode` + canvas integration

**Interfaces:**
- `MemberFlowNode.vue`: receives Vue Flow custom-node props (`{ id, data, selected? }` — verify the exact prop shape against the installed typings `node_modules/@vue-flow/core/dist/types/node.d.ts`). `MemberFlowNodeData`: `{ label?, memberName, memberColor, nodeTitle?, nodeNumber?, taskPreview?, status? ('pending'|'running'|'done'|'failed'|'skipped'|'interrupted'), error?, configLabel?, personaLabel?, missingMember?, inDegree?, interactive? }`. Template: card with header (nodeTitle/编号 + status dot AND status text — not color-only), body (member color dot + memberName, config/persona chips when present, task preview 2-line clamp), footer (入站依赖 n), `Handle type="target" position="left"` + `Handle type="source" position="right"` (large connect area via CSS). Classes: `at-node-*` status ring (reuse existing colors), `is-missing` red border + 缺失成员 label, `has-error` (title attr = error). Handles hidden when `data.interactive === false` (test seam; production always true/undefined).
- Registration: `nodeTypes` MUST be non-reactive — module-level `const nodeTypes = { member: markRaw(MemberFlowNode) }` in TeamsFlowCanvas, passed `:node-types="nodeTypes"`. `displayNodes` maps every node to `type: 'member'` with the enriched data; keep the existing `domAttributes` tooltip ONLY if the node component doesn't already render its own title (prefer the component's `:title` — remove domAttributes from monitor mapping, keep behavior).
- Canvas chrome: `import { Background, Controls, MiniMap } from '@vue-flow/background'/'@vue-flow/minimap'/'@vue-flow/controls'` (+ each package's `dist/style.css` — verify each ships one; the implementer checks `node_modules/<pkg>/dist/`); `<Background :gap="16" />`, `<Controls />`, `<MiniMap pannable zoomable />`; monitor mode: MiniMap/Controls present but canvas not draggable (existing).
- Edges: `displayEdges` computed (new) — `markerEnd: MarkerType.ArrowClosed` (`MarkerType` from core) on all edges; monitor mode: `animated: true` when the SOURCE node's status is `running`. Wire `:edges="displayEdges"` (keep the existing one-way binding + emits contract).
- Data enrichment: `RunMonitor.vue` dagNodes gain memberName (from team members), memberColor (`collabMemberColor`), taskPreview (task_rendered or task, truncated ~60), status, error, inDegree, config/persona labels (v1: omit config/persona labels if not easily available in monitor — they are NOT in node_states; leave undefined, document). `WorkflowEditor.vue` graphNodes gain memberName/color/taskPreview/configLabel/personaLabel (from the node's execution + team members + `configProfileApi.list` cache if loaded), missingMember flag (existing), inDegree, nodeNumber (index), interactive: true.
- i18n keys ×3: `editor.memberMissing` (缺失成员), `editor.inDegree` (入站依赖), `monitor.nodeStatusText.*` — REUSE existing `monitor.node.*` keys for status text (no new keys unless a gap).

- [ ] **Step 1: failing specs** — MemberFlowNode renders header/body/footer/chips/missing/error states (handles conditional); TeamsFlowCanvas integration: nodes get `type: 'member'`, edges get markers, monitor running edge animated, chrome components present (stub-level assertions via the canvas spec style used in Plan 2's main build). → **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (`pnpm exec vitest run src/components/agent_teams`) + `pnpm build` → **Step 5: Commit** — `feat(dashboard): add member node cards and canvas chrome to agent teams editor`

---

### Task 4: Frontend — drag-drop member panel + right inspector drawer + toolbar

**Files:**
- Modify: `dashboard/src/components/agent_teams/WorkflowEditor.vue` (three-column layout: member strip | canvas | inspector drawer; toolbar rework; drag-drop; validation badge + summary)
- Modify: `dashboard/src/components/agent_teams/TeamsFlowCanvas.vue` (canvas root `@dragover.prevent`/`@drop` forwarding with flow-coordinate conversion — expose a `dropAt(memberId, screenX, screenY)` via emit, or forward the raw drop event and let the editor convert via `useVueFlow().project`/`screenToFlowCoordinate` — READ the installed typings `node_modules/@vue-flow/core/dist/composables/*.d.ts` and use whichever exists in 1.48.2)
- Specs

**Interfaces:**
- Member strip (left, ~200px, inside the editor tab): team members with color dot + name + persona/config summary line; `draggable="true"` + `dragstart` sets `application/x-member-id`; click = legacy add-node fallback (existing 添加节点 behavior preserved as a button). Coordinator low-key badge; referenced-count chip per member.
- Drop: canvas wrapper `@dragover.prevent` + `@drop` → resolve member_id from dataTransfer → convert screen→flow coords (`useVueFlow().project` or `screenToFlowCoordinate` — verify in typings; Vue Flow 1.48 has `project` on the flow instance) → `addNode(memberId, position)` (existing addNode logic parameterized with position).
- Inspector drawer: `v-navigation-drawer location="right"` — persistent (width ~340) when the viewport is wide (`useDisplay().lgAndUp`), temporary overlay otherwise; contains the ENTIRE existing inspector (member select, execution group from Plan 1, task editor) unchanged except placement; opens on node select (existing behavior), closes on deselect.
- Toolbar: workflow select + inline-editable name + unsaved dot (dirty state exists? if not, derive: current graph != loaded/saved snapshot — implement a lightweight dirty flag) + validation badge (local problem count from existing `bannerMessage` + new unconnected-reference warnings; click → summary list) + 保存 button. NO 撤销/重做 (spec: explicitly not).
- Unconnected-reference warnings: `unconnectedReferences` moved/duplicated to `dashboard/src/utils/dagCheck.ts` (pure, exported, spec-tested): for the selected node's template, every `{{<id>}}` where id is a graph node but NOT a direct predecessor → warning string `引用了未连接的节点 {id}`; rendered in the inspector under the task editor (local-only, never save-blocking).

- [ ] **Step 1: failing specs** — drop adds node at converted position with correct member; member strip dragstart sets data; dirty dot toggles on edit; validation badge counts local problems; drawer opens on select and is temporary on narrow viewport (mock `useDisplay`). → **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** + `pnpm build` → **Step 5: Commit** — `feat(dashboard): rework agent teams editor into three-column workbench`

---

### Task 5: Frontend — variable chips, field-error mapping, validation summary

**Files:**
- Modify: `dashboard/src/utils/dagCheck.ts` (+ `unconnectedReferences` if not landed in Task 4 — place it here if Task 4 kept it local; single source of truth in dagCheck, spec-tested)
- Modify: `dashboard/src/components/agent_teams/WorkflowEditor.vue` (task-editor variable chips: `{{input}}` + one chip per DIRECT predecessor `{{<pred_id>}}` labeled `{{pred_id}} · <title|memberName>`, click inserts at cursor reusing the existing insert logic; server field-error mapping: on save failure with `fields`, banner lists `{path → message}` entries; clicking an entry with path `nodes.{id}.*` selects that node and opens the inspector; local validation errors and server fields coexist, deduped by message)
- Modify: `dashboard/src/composables/useAgentTeams.ts` — none (lastErrorFields exists from Plan 1)
- Specs

**Interfaces:**
- Chips: derive direct predecessors from the current graph edges for the selected node; chips render ABOVE the textarea next to the existing `{{input}}` button.
- Field mapping: path grammar `nodes.{node_id}(.task|.member_id|.execution.config_id|.execution.persona_id)|name|edges` — parse node id, `selectNode(id)`; entries without a node path render non-clickable.
- Dedupe: server field messages already shown locally (same message text) are not duplicated — compare by message string.

- [ ] **Step 1: failing specs** (chips per predecessor; click inserts; field entry click selects node; dedupe) → **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (full `pnpm exec vitest run` — 3 known failures acceptable) + `pnpm build` → **Step 5: Commit** — `feat(dashboard): add variable chips and field-error mapping to agent teams editor`

---

### Task 6: Verification pass

- [ ] **Step 1:** backend `uv run pytest tests/agent_teams/ -v` (111 prior + new) + `ruff format . && ruff check .`; frontend `pnpm exec vitest run` (907 prior + new; 3 known DocumentManager failures) + `pnpm build`.
- [ ] **Step 2:** manual smoke (deferred to the human): draw n1→n2 without any `{{n1}}` — n2 receives n1's full result in `[上游结果]`; save with empty name + empty task + a cycle → banner lists all field problems, clicking each focuses the right node; drag a member onto the canvas → node appears; monitor mode shows member cards with status rings and running-edge animation; minimap/controls present; narrow viewport collapses panels to drawers.

---

## Plan Notes

- **Out of scope (Plan 3):** `agent_team_run_messages` transcript table, turn_id/seq/parts event enrichment, choice mirroring + collect suspension, node dialog (speak/interrupt/InteractiveChoiceBox/fullscreen) — spec §4/§5.
- **Deferred (from Plan 1's final review, folded here where natural):** unlabeled multi-selects → Task 4's inspector rework adds labels; validation click-to-focus → Task 5. Remaining Plan-1 minors (untrimmed message, banner lastError precedence, attach-handler cleanup, HTTP-200 silent option lists) stay deferred.
- The custom-node registration must avoid Vue Flow's reactivity warning — module-scope `markRaw`; if `nodeTypes` as a plain module const triggers per-instance typing issues with `MemberFlowNodeData`, type the component props loosely (`data: any` with an internal interface) and document.
- `@vue-flow/background/minimap/controls` each ship their own `dist/style.css` — import all three (verify per package; missing CSS renders unstyled chrome).
- If `project` is absent in 1.48.2 typings, `screenToFlowCoordinate` is the replacement — verify against `node_modules/@vue-flow/core/dist/composables/` before implementing Task 4's drop handler.
