# Shell Session User Actions (peek / terminate + Agent notice) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let ChatUI users peek at (live-follow) and terminate managed shell sessions from multi-instance floating windows, and let an active agent learn of user terminations via a `[SYSTEM NOTICE]` in the next tool result.

**Architecture:** Backend adds a non-cursor-advancing `peek_session_output` to `LocalShellComponent`, two REST endpoints, and a system-notice channel on the agent runner (separate from follow-ups, merged into tool results at the existing consumption point). Frontend adds a multi-window host in Chat.vue with per-window peek polling; the indicator popover opens windows per session.

**Tech Stack:** Python 3.10+ asyncio, FastAPI, Vue 3 + Pinia + Vuetify, vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-10-05-shell-session-user-actions-design.md`

## Global Constraints

- Work in an isolated git worktree (`.worktrees/shell-session-user-actions`, branch `feat/shell-session-user-actions`, base = current `all`).
- English comments/logs; Google-style docstrings; `ruff format` + `ruff check` before each backend commit; conventional commits.
- Backend route changes require: edit `openspec/openapi-v1.yaml`, regenerate client via `npx openapi-ts` from main-workspace node_modules (pnpm rejects the junctioned worktree node_modules — established ruling), and run `docs/scripts/update_openapi_json.py` to refresh scope artifacts.
- i18n keys in all four locales (`zh-CN`, `en-US`, `ru-RU`, `ja-JP`); ja-JP's `shellSession` block currently only has `indicator`/`stateLabels`/`labels.unread` — add to that block.
- **peek never writes `session.cursor` and never removes sessions** — the agent's incremental polling must be unaffected by user viewing.
- Agent notices are transient: inject only into an active run, never queue for future runs.
- Frontend tests run via `node ./node_modules/vitest/vitest.mjs run <path>` from the worktree dashboard (pnpm refuses the junction).

## Review Focus

1. **User peek advancing the agent's cursor** — agent would silently lose output. Pinned by Task 1 `test_peek_does_not_advance_session_cursor`.
2. **Notice framing** — follow-up template says "user sent follow-up messages"; wrong for dashboard actions. Pinned by Task 2 `test_system_notice_uses_its_own_template`.
3. **Terminate/poll race** — user terminates while the agent polls. Pinned by Task 1/3: peek/terminate share `_sessions_lock` via `_get_owned_session`; agent gets not-found + notice.
4. **Window for a session that vanished from the push list** — must not auto-close (user is reading final output). Pinned by Task 5 window spec test.
5. **Peek on a session already removed by the agent's poll** — REST surfaces 404-style error, window shows closed state. Pinned by Task 3 `test_peek_removed_session_raises`.

---

### Task 1: `peek_session_output` in `LocalShellComponent`

**Files:**
- Modify: `astrbot/core/computer/booters/local.py` (`poll_session` ~L890-1010 area; `_read_output` closure extraction)
- Test: `tests/test_local_shell_component.py` (extend; reuse `_patch_managed_spawn` / `_ControllableProcess` helpers added by the indicator feature)

**Interfaces:**
- Produces:
  - `LocalShellComponent._read_output_range(session: _LocalShellSession, cursor: int, max_chars: int) -> tuple[bytes, int, int]` — shared file-read (returns `raw, next_cursor, output_size`); `poll_session` refactored to use it.
  - `LocalShellComponent.peek_session_output(*, owner_id, requester_id, requester_is_admin, session_id, cursor=0, yield_time_ms=0, max_output_chars=50_000) -> dict` returning `{session_id, pid, status, stdout, exit_code, cursor, has_more, session_closed}` — same shape as `poll_session` minus cursor persistence.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_peek_does_not_advance_session_cursor(monkeypatch, tmp_path):
    # controllable process; exec_managed with yield_time_ms=50 (running);
    # write output via holder["proc"].stdout chunks;
    # peek_session_output(cursor=0) → returns output; assert session.cursor == 0
    # then poll_session() → returns the SAME output (agent loses nothing)

async def test_peek_reports_closed_but_keeps_session(...):
    # process exits; peek until end of file → session_closed True,
    # but session still in component._sessions (poll owns removal)

async def test_peek_enforces_ownership(...):
    # wrong owner_id → ValueError

async def test_peek_yield_waits_for_new_output(...):
    # peek with yield_time_ms=2000 while no output → feed a chunk mid-wait
    # → returns promptly with the chunk
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_local_shell_component.py -k peek -v`
Expected: FAIL (`AttributeError: peek_session_output`)

- [ ] **Step 3: Implement**

Extract `poll_session`'s inline `_read_output` closure into `_read_output_range(session, cursor, max_chars)` (component method; the closure body moves verbatim). `poll_session` calls it. Add `peek_session_output`: `_get_owned_session` → loop of read + (optional) wait on `session.output_event`/`wait_task` like poll's wait path, but **never** assigns `session.cursor` and **never** calls `_remove_session`. `session_closed = exit_code is not None and not has_more`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_local_shell_component.py -v`
Expected: PASS (new peek tests + all pre-existing, incl. the 4 change-listener tests)

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/computer/booters/local.py tests/test_local_shell_component.py
git commit -m "feat: add non-destructive peek_session_output for shell sessions"
```

---

### Task 2: Agent system-notice channel

**Files:**
- Modify: `astrbot/core/agent/runners/tool_loop_agent_runner.py` (`follow_up` ~L770, `_merge_follow_up_notice` ~L841, consumption point ~L1287)
- Modify: `astrbot/core/pipeline/process_stage/follow_up.py` (after `unregister_active_runner` ~L49)
- Test: `tests/unit/test_system_notice_injection.py` (create)

**Interfaces:**
- Produces:
  - `ToolLoopAgentRunner.inject_system_notice(text: str) -> bool` — queues into `self._pending_system_notices: list[str]`; returns False when run is done/stopped or text blank.
  - Module-level in follow_up.py: `inject_system_notice_to_active_run(umo: str, text: str) -> bool` — looks up `_ACTIVE_AGENT_RUNNERS`, delegates; False when no active run.
  - Notice rendering: each pending notice becomes `"\n\n[SYSTEM NOTICE] {text}"` appended to the tool result at the existing `_merge_follow_up_notice` consumption point.
- Consumes: `_ACTIVE_AGENT_RUNNERS` (existing).

- [ ] **Step 1: Write the failing tests**

```python
def test_system_notice_uses_its_own_template(...):
    # runner instance (construct minimal, see existing runner tests);
    # inject_system_notice("hello"); content = runner._merge_follow_up_notice("tool result");
    # assert "tool result\n\n[SYSTEM NOTICE] hello" == content
    # assert "follow-up" not in content

def test_system_notice_consumed_once(...):
    # second _merge_follow_up_notice call returns content unchanged

def test_inject_returns_false_when_done(...):
    # runner marked done → inject_system_notice returns False, nothing queued

def test_inject_to_active_run(...):
    # register_active_runner(umo, runner) → inject_system_notice_to_active_run(umo, "x") is True
    # and runner received it; unknown umo → False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_system_notice_injection.py -v`
Expected: FAIL (`AttributeError: inject_system_notice`)

- [ ] **Step 3: Implement**

Runner: add `_pending_system_notices: list[str]` next to `_pending_follow_ups` (init ~L335); add `inject_system_notice` mirroring `follow_up`'s guards; extend `_merge_follow_up_notice` to consume system notices first (each wrapped in its own `[SYSTEM NOTICE]` line), then the follow-up template as before. follow_up.py: add the module function with a Google docstring.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_system_notice_injection.py tests/test_agent_runner.py -q 2>/dev/null || pytest tests/unit/test_system_notice_injection.py -q`
Expected: PASS (plus adjacent runner suites if they exist)

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/agent/runners/tool_loop_agent_runner.py astrbot/core/pipeline/process_stage/follow_up.py tests/unit/test_system_notice_injection.py
git commit -m "feat: add system notice injection channel to agent runner"
```

---

### Task 3: REST endpoints + terminate notice wiring

**Files:**
- Modify: `astrbot/dashboard/services/chat_service.py` (after `get_session_shell_sessions`)
- Modify: `astrbot/dashboard/api/chat.py` (after the shell-sessions GET route)
- Modify: `openspec/openapi-v1.yaml` (after the shell-sessions path)
- Test: `tests/unit/test_chat_shell_sessions.py` (extend)

**Interfaces:**
- Consumes: Task 1 `peek_session_output`; Task 2 `inject_system_notice_to_active_run`.
- Produces:
  - `ChatService.get_shell_session_output(username, session_id, shell_session_id, cursor=0, max_chars=50_000, yield_time_ms=0) -> dict`
  - `ChatService.terminate_shell_session(username, session_id, shell_session_id) -> dict` — on success calls `inject_system_notice_to_active_run(umo, notice)` with notice `"[ChatUI] User terminated managed shell session {shid} (pid {pid}). If you were polling it, expect \"not found\" errors — do not retry it."`
  - Routes: `GET /chat/sessions/{session_id}/shell-sessions/{shell_session_id}/output`, `POST /chat/sessions/{session_id}/shell-sessions/{shell_session_id}/terminate`
  - Client: `chatApi.getShellSessionOutput(sessionId, shellSessionId, { cursor?, maxChars?, yieldTimeMs? })`, `chatApi.terminateShellSession(sessionId, shellSessionId)`

- [ ] **Step 1: Write the failing tests**

```python
async def test_peek_removed_session_raises(...):
    # component without the session → ValueError → ChatServiceError

async def test_get_shell_session_output_passthrough(...):
    # fake shell peek returns dict; assert passthrough + requester_is_admin=True

async def test_terminate_shell_session_injects_notice(monkeypatch, ...):
    # fake shell.terminate_session returns {"status": "terminated", "pid": 1};
    # spy on follow_up.inject_system_notice_to_active_run;
    # assert called with (umo, text) and "sh_" in text

async def test_terminate_notice_skipped_without_active_run(...):
    # inject returns False; terminate still succeeds (no exception)
```

Reuse Task 2-era fakes (`_make_service`, `_session`); real `LocalShellComponent` with monkeypatched `peek_session_output`/`terminate_session` AsyncMocks.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_chat_shell_sessions.py -k "output or terminate" -v`
Expected: FAIL (`AttributeError`)

- [ ] **Step 3: Implement service methods + routes**

Service: same ownership preamble as `get_session_shell_sessions`; booter/type guard → for peek raise `ChatServiceError("Shell session unavailable")` when no local runtime; call component methods. Terminate: build umo via `build_webchat_unified_msg_origin(session)`; after successful terminate, `inject_system_notice_to_active_run(umo, NOTICE)` (ignore return). Routes mirror the existing shell-sessions GET; terminate takes no body.

- [ ] **Step 4: OpenAPI + client + scope docs**

Add both paths to `openspec/openapi-v1.yaml` (scope `chat`; query params `cursor`/`max_chars`/`yield_time_ms` for GET). Regenerate client (ruling from previous plan):

```bash
rm -rf via python; cd main-dashboard && npx openapi-ts -i <worktree>/openspec/openapi-v1.yaml -o <worktree>/dashboard/src/api/generated/openapi-v1 -c @hey-api/client-axios
```

Add `chatApi` wrappers in `dashboard/src/api/v1.ts` (mirror `getSessionShellSessions`). Run `docs/scripts/update_openapi_json.py`.

- [ ] **Step 5: Run tests, verify pass, commit**

Run: `pytest tests/unit/test_chat_shell_sessions.py tests/unit/test_openapi_scope_docs.py -q`
Expected: PASS

```bash
git add astrbot/dashboard openspec/openapi-v1.yaml docs/public/openapi.json docs/zh/dev/openapi-scopes.md docs/en/dev/openapi-scopes.md tests/unit/test_chat_shell_sessions.py dashboard/src/api
git commit -m "feat: add shell session peek and terminate endpoints"
```

---

### Task 4: Window state + indicator popover entry

**Files:**
- Modify: `dashboard/src/stores/chatHeader.ts`
- Modify: `dashboard/src/components/chat/ShellSessionIndicator.vue` (popover rows clickable)
- Test: `dashboard/src/composables/__tests__/useShellSessionWindows.spec.ts` (create — tests the store actions; store logic is the testable unit here)

**Interfaces:**
- Produces: `chatHeader.openShellWindows: string[]`; `SET_SHELL_WINDOW_OPEN(sessionId: string, open: boolean)` (open: append if absent; close: remove); `FOCUS_SHELL_WINDOW(sessionId: string)` (move to end = topmost); cleared in `CLEAR_CONTEXT`.
- Consumes: Task 5 renders windows from `openShellWindows`.

- [ ] **Step 1: Write the failing test**

```typescript
it("SET_SHELL_WINDOW_OPEN opens once, focuses on re-open, closes", () => {
  // setActivePinia(createPinia()); store = useChatHeaderStore();
  // open a, open b, open a again → ["b", "a"] (focus moves to end)
  // close b → ["a"]; CLEAR_CONTEXT → []
});
```

- [ ] **Step 2: Run test to verify it fails** — `node ./node_modules/vitest/vitest.mjs run src/composables/__tests__/useShellSessionWindows.spec.ts`; Expected: FAIL (actions missing).

- [ ] **Step 3: Implement store actions; wire the popover row click** to `SET_SHELL_WINDOW_OPEN(s.session_id, true)` + `FOCUS_SHELL_WINDOW(s.session_id)`; rows get `cursor: pointer` + an `mdi-text-box-outline` trailing icon.

- [ ] **Step 4: Run test, verify pass, commit** — `git commit -m "feat: open shell session windows from the indicator popover"`.

---

### Task 5: `ShellSessionWindow.vue` multi-instance host + i18n

**Files:**
- Create: `dashboard/src/components/chat/ShellSessionWindow.vue`
- Create: `dashboard/src/composables/useShellSessionOutput.ts` (polling loop, unit-testable)
- Modify: `dashboard/src/components/chat/Chat.vue` (host `v-for` over `chatHeader.openShellWindows`)
- Modify: 4 locale files (`shellSession.window.*`)
- Test: `dashboard/src/composables/__tests__/useShellSessionOutput.spec.ts` (create)

**Interfaces:**
- Consumes: `chatApi.getShellSessionOutput` (Task 3), `chatApi.terminateShellSession` (Task 3), `chatHeader` window state (Task 4), `ShellSessionListItem` status meta helpers (existing `shell_session_tools/icons`).
- Produces:
  - `useShellSessionOutput(sessionId: string, shellSessionId: string)` → `{ outputText: Ref<string>, status: Ref<string>, sessionClosed: Ref<boolean>, follow: Ref<boolean>, start(): void, stop(): void }` — internal client-side cursor from 0, loop: GET with `yieldTimeMs: 2000`, append `stdout`, advance cursor, stop when `sessionClosed`.
  - `ShellSessionWindow` props: `{ sessionId: string, shellSessionId: string, zIndex: number }`.

- [ ] **Step 1: Write the failing composable tests**

```typescript
// mock chatApi.getShellSessionOutput with a scripted sequence
it("appends incremental output and advances its own cursor", ...)
it("stops polling when sessionClosed", ...)
it("keeps showing content after a 404-style removal error", ...)
```

- [ ] **Step 2: Run tests to verify they fail** — module missing.

- [ ] **Step 3: Implement `useShellSessionOutput`**

Loop: `while (!stopped) { resp = await get(...cursor, yieldTimeMs: 2000); append; cursor = resp.cursor; status = resp.status; if (resp.session_closed) break; }` with try/catch (on error: mark closed, keep content, stop). `follow` toggles auto-scroll only (consumed by the window).

- [ ] **Step 4: Implement `ShellSessionWindow.vue` + host in Chat.vue**

Per spec §4.1: fixed-position floating window; draggable via header (pointer events, mirroring todo-summary-bar's drag); initial position cascades by index in `openShellWindows`; `:style="{ zIndex }"`; mousedown → `FOCUS_SHELL_WINDOW`. Header: status dot (pulse when running) + full session id (flex ellipsis) + status label + `pid` + relative time + terminate (two-click confirm, calls `chatApi.terminateShellSession`, then shows final state) + close (`SET_SHELL_WINDOW_OPEN(id, false)`). Body: dark terminal log bound to `outputText`; footer: follow toggle + bytes-read note. The window must NOT auto-close when the session leaves the push list.

- [ ] **Step 5: i18n — `shellSession.window.*` in 4 locales**

Keys: `view`, `terminate`, `terminateConfirm`, `terminated`, `followOutput`, `closed`, `unavailable`. Values per locale (zh/en/ru/ja).

- [ ] **Step 6: Verify**

Run: composable+store specs, `vue-tsc --noEmit`, full `vitest run` (expect only the pre-existing ProjectList failures).
Expected: green except known pre-existing.

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/components/chat/ShellSessionWindow.vue dashboard/src/composables/useShellSessionOutput.ts dashboard/src/composables/__tests__ dashboard/src/components/chat/Chat.vue dashboard/src/components/chat/ShellSessionIndicator.vue dashboard/src/stores/chatHeader.ts dashboard/src/i18n
git commit -m "feat: add floating shell session output windows with terminate"
```

---

## Self-Review Notes

- **Spec coverage:** §3.1 → Task 1; §3.3 → Task 2; §3.2 → Task 3; §4.2 + store → Task 4; §4.1/§4.3 → Task 5; §5 failure modes → Review Focus + per-task tests; §6 test plan → per-task steps. Full coverage.
- **Type consistency:** `peek_session_output` kwargs match `poll_session` conventions; notice function named `inject_system_notice_to_active_run` (module-level) vs `inject_system_notice` (runner method) — deliberate disambiguation.
- **Windows build:** `vite build` is broken on base (langium) — verification criterion is `vue-tsc` + vitest, same ruling as the indicator feature.
