# ChatUI Shell Session Indicator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface managed background shell sessions (`LocalShellComponent`) to ChatUI users as a live, read-only header indicator.

**Architecture:** Kernel `LocalShellComponent` fires fire-and-forget change notifications (owner_id only) on session create/exit/remove → dashboard `app.py` listener builds a full snapshot via `list_sessions` and pushes `shell_sessions_changed` over the existing webchat system-event stream → frontend `useShellSessions` store (mirrors `useSessionGoal`) replaces per-session cache → header badge + popover. A single GET endpoint serves cold-start only.

**Tech Stack:** Python 3.10+ (asyncio), FastAPI/Starlette, Vue 3 + Pinia + Vuetify, vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-10-04-shell-session-indicator-design.md`

## Global Constraints

- Comments and logs in **English**; docstrings in Google format.
- `ruff format . && ruff check .` must pass before every backend commit.
- Conventional commit messages (`feat:` / `test:` / `docs:`).
- Backend route/schema changes require `cd dashboard && pnpm generate:api`.
- i18n keys must be added to **all four** locales: `zh-CN`, `en-US`, `ru-RU`, `ja-JP` (`dashboard/src/i18n/locales/<lang>/features/chat.json`).
- No new dependencies. Use `pathlib.Path`, not string paths.
- Push notifications are best-effort: never awaited inline on the shell management path, listener exceptions are logged and swallowed.
- Event payload is the verbatim `list_sessions` item shape: `{session_id, pid, status, exit_code, started_at, sandboxed, unread_output_bytes}`; `status ∈ {running, completed, failed, timed_out, terminated}`.

## Review Focus

1. **Listener raising inside shell ops** — a throwing listener must not break `exec_managed`/`poll_session`/`_remove_session`. Pinned by Task 1 `test_change_listener_exception_does_not_break_shell_ops`.
2. **Notify from `wait_task` done callback** — runs on the event loop in sync context; notification must be scheduled, never awaited inline. Pinned by Task 1 `test_change_listener_fires_on_process_exit` (completes without hang).
3. **Non-webchat owner (e.g. `telegram!...`)** — push wiring must ignore it, no exception. Pinned by Task 3 `test_push_ignores_non_webchat_owner`.
4. **Docker runtime / booter absent** — REST returns `{"sessions": []}`, never 500. Pinned by Task 2 `test_get_session_shell_sessions_empty_when_no_local_booter`.
5. **Foreign session access** — REST rejects when `session.creator != username`. Pinned by Task 2 `test_get_session_shell_sessions_rejects_foreign_session`.

---

### Task 1: `LocalShellComponent` change notification

**Files:**
- Modify: `astrbot/core/computer/booters/local.py` (`LocalShellComponent`, L494; triggers at L769, L735, `_remove_session` L1283)
- Test: `tests/test_local_shell_component.py` (extend; reuse its existing component-construction fixtures)

**Interfaces:**
- Produces: `LocalShellComponent.add_change_listener(listener: Callable[[str], None]) -> None` — listener receives `owner_id`, is **sync**, exceptions are swallowed+log-warned. Notification fires on session create, process exit, and session removal. Later tasks rely on exactly this signature.

- [ ] **Step 1: Write the failing tests**

```python
async def test_change_listener_fires_on_session_create(...):
    # register listener collecting owner_ids; exec_managed a trivial
    # command (yield_time_ms=0); assert owner_id in collected

async def test_change_listener_fires_on_process_exit(...):
    # exec_managed a fast command (yield_time_ms=0); await until the
    # process exits (poll component internals or sleep-loop with timeout);
    # assert a second notification with the same owner_id arrived —
    # proves the wait_task done-callback path fires without hanging

async def test_change_listener_fires_on_session_removal(...):
    # exec_managed a fast command; poll_session until session_closed;
    # assert a removal notification arrived

async def test_change_listener_exception_does_not_break_shell_ops(...):
    # register a listener that raises RuntimeError; exec_managed and
    # poll_session must still return normal results
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_local_shell_component.py -k change_listener -v`
Expected: FAIL (`AttributeError: add_change_listener`)

- [ ] **Step 3: Implement**

In `LocalShellComponent`: add field `_change_listeners: list[Callable[[str], None]] = field(default_factory=list, init=False, repr=False)`; method `add_change_listener(listener)` (append); method `_notify_change(owner_id: str)` iterating a copy of the list, wrapping each call in try/except with `logger.warning`. Call sites:
1. `exec_managed`: immediately after `self._sessions[session_id] = session` (after the `_sessions_lock` block closes).
2. The existing `wait_task.add_done_callback(lambda _: output_event.set())` (L735): extend to also `self._notify_change(owner_id)` — sync call, safe in the callback.
3. `_remove_session`: after removal, `self._notify_change(session.owner_id)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_local_shell_component.py -v`
Expected: PASS (new + all pre-existing)

- [ ] **Step 5: Commit**

```bash
ruff format astrbot/core/computer/booters/local.py && ruff check astrbot/core/computer/booters/local.py
git add astrbot/core/computer/booters/local.py tests/test_local_shell_component.py
git commit -m "feat: add change listeners to LocalShellComponent"
```

---

### Task 2: REST cold-start endpoint

**Files:**
- Modify: `astrbot/dashboard/services/chat_service.py` (add method next to `get_session_goal`, L2933)
- Modify: `astrbot/dashboard/api/chat.py` (add route after the goal routes, L361-380)
- Test: `tests/unit/test_chat_shell_sessions.py` (create; follow the service-fake fixture pattern of `tests/unit/test_goal_actions.py`)

**Interfaces:**
- Consumes: `computer_client.local_booter` (module global, may be `None`), `LocalShellComponent.list_sessions(owner_id, requester_id, requester_is_admin)` (existing).
- Produces: `ChatService.get_session_shell_sessions(username: str, session_id: str) -> dict` returning `{"sessions": list[dict]}`; route `GET /chat/sessions/{session_id}/shell-sessions`; generated client method `chatApi.getSessionShellSessions(sessionId)`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_get_session_shell_sessions_returns_sessions(...):
    # fake db session owned by username; monkeypatch computer_client.local_booter
    # with a fake whose .shell.list_sessions returns {"sessions": [item]};
    # assert service returns that dict, and list_sessions was called with
    # requester_is_admin=True

async def test_get_session_shell_sessions_rejects_foreign_session(...):
    # session.creator != username → pytest.raises(ChatServiceError)

async def test_get_session_shell_sessions_empty_when_no_local_booter(...):
    # local_booter None → {"sessions": []}; also cover shell not being a
    # LocalShellComponent instance
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_chat_shell_sessions.py -v`
Expected: FAIL (`AttributeError: get_session_shell_sessions`)

- [ ] **Step 3: Implement `get_session_shell_sessions`**

Mirror `get_session_goal` line-for-line: `get_platform_session_by_id` → 404-style `ChatServiceError` when missing; `session.creator != username` → `ChatServiceError("Permission denied")`; then `booter = computer_client.local_booter`; if `booter is None or not isinstance(booter.shell, LocalShellComponent)` return `{"sessions": []}`; else `await booter.shell.list_sessions(owner_id=build_webchat_unified_msg_origin(session), requester_id=username, requester_is_admin=True)`.

- [ ] **Step 4: Add the route**

In `chat.py` after the goal routes:

```python
@router.get("/chat/sessions/{session_id}/shell-sessions")
async def get_chat_session_shell_sessions(
    session_id: str,
    auth: AuthContext = Depends(require_chat_scope),
    service: ChatService = Depends(get_service),
):
    """Return managed shell sessions for a session (cold-start path)."""
    return await _run(
        lambda: service.get_session_shell_sessions(auth.username, session_id)
    )
```

- [ ] **Step 5: Regenerate the frontend API client**

Run: `cd dashboard && pnpm generate:api`
Expected: `chatApi.getSessionShellSessions` appears in the generated client.

- [ ] **Step 6: Run tests, verify pass, commit**

Run: `pytest tests/unit/test_chat_shell_sessions.py -v`
Expected: PASS

```bash
ruff format astrbot/dashboard && ruff check astrbot/dashboard
git add astrbot/dashboard tests/unit/test_chat_shell_sessions.py dashboard/src/api
git commit -m "feat: add shell sessions cold-start endpoint for chatui"
```

---

### Task 3: System-stream push wiring

**Files:**
- Modify: `astrbot/dashboard/api/app.py` (next to the goal wiring, L205-245)
- Test: `tests/unit/test_shell_session_push.py` (create)

**Interfaces:**
- Consumes: Task 1's `add_change_listener`; `webchat_queue_mgr.put_system_event`; `computer_client.get_local_booter`.
- Produces: module-level `async def _shell_sessions_changed(owner_id: str) -> None` in `app.py`, registered on the local booter's shell component at dashboard startup.

- [ ] **Step 1: Write the failing tests**

```python
async def test_push_ignores_non_webchat_owner(...):
    # monkeypatch webchat_queue_mgr.put_system_event with a recording fake;
    # await _shell_sessions_changed("telegram!u!g"); assert no call

async def test_push_sends_snapshot_payload(...):
    # monkeypatch computer_client.local_booter with fake shell whose
    # list_sessions returns {"sessions": [ITEM]}; record put_system_event;
    # await _shell_sessions_changed("webchat!user!cid123");
    # assert call args == ("cid123", {"type": "shell_sessions_changed",
    #   "data": {"sessions": [ITEM]}})

async def test_push_no_booter_is_noop(...):
    # local_booter None → no put_system_event call, no exception
```

Note for the implementer: `_shell_sessions_changed` reads `computer_client.local_booter` **through the module attribute** (`from astrbot.core.computer import computer_client` then `computer_client.local_booter`) so monkeypatching works — same pattern as `config_service.py:502`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_shell_session_push.py -v`
Expected: FAIL (`ImportError` / `AttributeError`)

- [ ] **Step 3: Implement**

In `app.py`, next to `_goal_state_changed`:

```python
async def _shell_sessions_changed(owner_id: str) -> None:
    """Push a shell-session snapshot to the session's webchat system stream."""
```

Body per spec §5.2: ignore non-`webchat!`-prefixed owner_ids; `cid = owner_id.rsplit("!", 1)[-1]`; booter/type guard; `list_sessions(owner_id=owner_id, requester_id="", requester_is_admin=True)`; `put_system_event(cid, {"type": "shell_sessions_changed", "data": {"sessions": result["sessions"]}})`.

Registration, after `_goal_service.set_state_change_listener(...)`:

```python
_booter = get_local_booter()
if isinstance(_booter.shell, LocalShellComponent):
    _booter.shell.add_change_listener(
        lambda owner_id: asyncio.create_task(_shell_sessions_changed(owner_id))
    )
```

`get_local_booter()` here is deliberate: construction is side-effect-free (three component objects, no processes), and the singleton then lives for the process lifetime. The sync lambda wrapper exists because Task 1's listener contract is sync; `create_task` requires the running loop, which is present in all three trigger contexts.

- [ ] **Step 4: Run tests, verify pass, commit**

Run: `pytest tests/unit/test_shell_session_push.py -v`
Expected: PASS

```bash
ruff format astrbot/dashboard/api/app.py && ruff check astrbot/dashboard/api/app.py
git add astrbot/dashboard/api/app.py tests/unit/test_shell_session_push.py
git commit -m "feat: push shell session snapshots over webchat system stream"
```

---

### Task 4: `useShellSessions` composable

**Files:**
- Create: `dashboard/src/composables/useShellSessions.ts`
- Test: `dashboard/src/composables/__tests__/useShellSessions.spec.ts` (create)

**Interfaces:**
- Consumes: `chatApi.getSessionShellSessions` (Task 2), `ShellSessionListItem` type from `@/components/chat/message_list_comps/shell_session_tools/format`.
- Produces: `useShellSessions(currentSessionId: Ref<string | undefined>)` returning `{ currentSessions: ComputedRef<ShellSessionListItem[]>, runningCount: ComputedRef<number>, finishedCount: ComputedRef<number>, applyPushedShellSessions(sessionId: string, sessions: ShellSessionListItem[]): void, refreshShellSessions(sessionId: string): Promise<void> }`.

- [ ] **Step 1: Write the failing tests**

```typescript
// mock @/api/v1 chatApi.getSessionShellSessions
it("applyPushedShellSessions authoritatively replaces the session cache", ...)
it("refreshShellSessions fills the cache and dedupes inflight requests", ...)
it("runningCount counts status === 'running'; finishedCount counts the rest", ...)
it("unknown session yields empty sessions and zero counts", ...)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && pnpm vitest run src/composables/__tests__/useShellSessions.spec.ts`
Expected: FAIL (module does not exist)

- [ ] **Step 3: Implement `useShellSessions.ts`**

Mirror `useSessionGoal.ts` structure exactly: `sessionsBySession = ref<Record<string, ShellSessionListItem[]>>({})`, `inflight` set, `applyPushedShellSessions` (spread-replace), `refreshShellSessions` (GET, `response.data?.data?.sessions ?? []`, keep stale cache on error), `watch(currentSessionId, …, { immediate: true })` cold-start fetch. Counts derive from `currentSessions`.

- [ ] **Step 4: Run tests, verify pass, commit**

Run: `cd dashboard && pnpm vitest run src/composables/__tests__/useShellSessions.spec.ts`
Expected: PASS

```bash
git add dashboard/src/composables/useShellSessions.ts dashboard/src/composables/__tests__/useShellSessions.spec.ts
git commit -m "feat: add useShellSessions composable for live shell session state"
```

---

### Task 5: System-stream dispatch and store wiring

**Files:**
- Modify: `dashboard/src/composables/useMessages.ts` (`UseMessagesOptions` L290, dispatch branch near L445)
- Modify: `dashboard/src/stores/chatHeader.ts` (add shell badge state, next to `goalBadge`)
- Modify: `dashboard/src/components/chat/Chat.vue` (option wiring near L2027; badge push watcher mirroring L4919-4936)
- Test: `dashboard/src/composables/__tests__/useMessagesShellSessions.spec.ts` (create; follow the fetch/SSE-mock pattern of `useMessagesHistoryFailure.spec.ts`)

**Interfaces:**
- Consumes: Task 4's `useShellSessions`.
- Produces: `UseMessagesOptions.onShellSessionsChanged?: (sessionId: string, sessions: ShellSessionListItem[]) => void`; `chatHeader` store additions: `shellSessions: ShellSessionListItem[] | null` + `SET_SHELL_SESSIONS(sessions | null)`, cleared in `CLEAR_CONTEXT`. Task 6's header component reads `chatHeader.shellSessions` and derives the running/finished counts itself.

- [ ] **Step 1: Write the failing test**

```typescript
it("dispatches shell_sessions_changed payloads to onShellSessionsChanged and does not render them as messages", ...)
```

Feed a mocked SSE stream containing `{"type": "shell_sessions_changed", "data": {"sessions": [ITEM]}}`; assert the callback received `(sessionId, [ITEM])` and no bot record was created.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && pnpm vitest run src/composables/__tests__/useMessagesShellSessions.spec.ts`
Expected: FAIL (option not defined / payload rendered as message)

- [ ] **Step 3: Implement the dispatch branch**

In `useMessages.ts`, directly after the `goal_state_changed` branch (~L445):

```typescript
if (payload?.type === "shell_sessions_changed") {
  const data = payload.data as
    | { sessions?: ShellSessionListItem[] }
    | undefined;
  options.onShellSessionsChanged?.(sessionId, data?.sessions ?? []);
  return;
}
```

Add the `onShellSessionsChanged` field to `UseMessagesOptions` with a doc comment mirroring `onGoalStateChanged`.

- [ ] **Step 4: Extend `chatHeader` store**

Add `shellSessions: ShellSessionListItem[] | null` state, `SET_SHELL_SESSIONS` action, and clear it in `CLEAR_CONTEXT` — same wiring shape as `goalBadge`/`SET_GOAL_BADGE`, but storing the raw list so the header component can both badge and list from one source.

- [ ] **Step 5: Wire `Chat.vue`**

`const { currentSessions, applyPushedShellSessions } = useShellSessions(currSessionId);` next to L1372; add `onShellSessionsChanged: applyPushedShellSessions` next to L2027; add a watcher mirroring the goal badge watcher (L4919-4936) that pushes `SET_SHELL_SESSIONS(currentSessions.length ? [...currentSessions] : null)` with `{ immediate: true }`.

- [ ] **Step 6: Run tests, verify pass, commit**

Run: `cd dashboard && pnpm vitest run src/composables/__tests__`
Expected: PASS (new + pre-existing)

```bash
git add dashboard/src/composables/useMessages.ts dashboard/src/stores/chatHeader.ts dashboard/src/components/chat/Chat.vue dashboard/src/composables/__tests__/useMessagesShellSessions.spec.ts
git commit -m "feat: dispatch shell session push events to chat header badge"
```

---

### Task 6: Header indicator UI + i18n

**Files:**
- Create: `dashboard/src/components/chat/ShellSessionIndicator.vue`
- Modify: `dashboard/src/layouts/full/vertical-header/VerticalHeader.vue` (mount next to the goal trigger, L1196-1212)
- Modify: `dashboard/src/i18n/locales/{zh-CN,en-US,ru-RU,ja-JP}/features/chat.json` (new `shellSessions.*` keys)
- Modify: `dashboard/src/components/chat/Chat.vue` (popover-open state in `chatHeader` is NOT needed if the popover is self-contained in the indicator component — keep it local)

**Interfaces:**
- Consumes: `chatHeader.shellSessions` (Task 5) for both the badge counts (derived in the component) and the popover list.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Add i18n keys (all four locales)**

`shellSessions.title` ("后台进程" / "Background Processes" / …), `shellSessions.running` / `shellSessions.finished` badge tooltips, `shellSessions.empty`. Follow the existing `goal.*` block layout in each file.

- [ ] **Step 2: Implement `ShellSessionIndicator.vue`**

A `v-btn` (mirroring the goal trigger's classes: `chat-action-btn`, `variant="text"`, `size="small"`, `rounded="sm"`) with `v-if="isChatPath && chatHeader.shellSessions?.length"`, icon `mdi-console`, running count text, pulse animation while `running > 0` (reuse the `StateChip` pulse CSS pattern from `ShellSessionToolResultView.vue`). Clicking opens a `v-menu` popover listing sessions: short `session_id` via `CopyableText`, `StateChip`-style status, exit code, `pid`, relative `started_at`, `formatBytes(unread_output_bytes)` — reuse imports from `shell_session_tools/format.ts` and `__shared__/CopyableText.vue`. Read-only; no action buttons.

- [ ] **Step 3: Mount in `VerticalHeader.vue`**

Render `<ShellSessionIndicator />` immediately before the goal trigger `v-btn` (L1196), guarded by the same `isChatPath` condition. Import and register the component.

- [ ] **Step 4: Verify the frontend builds and existing tests pass**

Run: `cd dashboard && pnpm vitest run && pnpm build`
Expected: PASS; build succeeds.

- [ ] **Step 5: Manual acceptance**

Start AstrBot + dashboard; in ChatUI ask the agent to run a long command (e.g. `ping -t localhost` on Windows / `sleep 60` elsewhere); expect the header indicator to appear with running count; ask the agent to terminate it via `astrbot_shell_session`; expect the badge to flip to finished, then disappear after the agent's poll closes the session; reload the page mid-run; expect the indicator to restore via the cold-start GET.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/components/chat/ShellSessionIndicator.vue dashboard/src/layouts/full/vertical-header/VerticalHeader.vue dashboard/src/i18n dashboard/src/components/chat/Chat.vue dashboard/src/stores/chatHeader.ts
git commit -m "feat: show managed shell session indicator in chat header"
```

---

## Self-Review Notes

- **Spec coverage:** §5.1 → Task 1; §5.3 → Task 2; §5.2 → Task 3; §5.4 → Task 4; §5.5 → Task 5; §5.6/§5.7 → Tasks 5-6; §6 failure modes → Review Focus tests; §7 test plan → per-task steps + Task 6 Step 5. Full coverage.
- **Store contract:** the header store carries the raw `shellSessions` list (single source); the composable's `runningCount`/`finishedCount` remain available for in-chat use, while the header component derives its own counts from the store list.
