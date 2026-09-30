# Subagent Follow-Up Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let ChatUI users type follow-up messages into a running foreground subagent run from its `SubAgentRunBlock`, delivered into the subagent's LLM context at the next tool-call boundary.

**Architecture:** Reuse the existing `ToolLoopAgentRunner.follow_up()` ticket mechanism for subagent runners. A new registry on `SubAgentManager` maps `subagent_run_id` → live runner + sink; a new dashboard POST route delivers text into the ticket queue; the sink echoes `user_message` / `user_message_relayed` `subagent_event` kinds that the existing SSE fan-out, persistence accumulator, and frontend reducer render in the run block.

**Tech Stack:** Python 3.10+ (FastAPI, asyncio), Vue 3 + Vuetify + TypeScript, pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-09-30-subagent-follow-up-input-design.md`

## Global Constraints

- All comments, logs, docstrings in **English**; docstrings in Google format (`Args:`/`Returns:`/`Raises:`).
- Conventional commit messages (e.g. `feat: ...`, `fix: ...`).
- After each backend task: `ruff format . && ruff check .` must pass.
- Python 3.10+ compatibility; use `pathlib.Path` for paths.
- Scope: foreground webchat handoffs only (registry entries exist only when a `SubAgentEventSink` exists). No background-subagent UI.
- The follow-up endpoint must NOT go through the webchat message queue or the pipeline; subagent follow-ups never become top-level user history records.
- KISS: no new helper functions beyond what the tasks define.

## Review Focus

1. **Fork mode + follow-up injection**: a follow-up accepted during a fork-context handoff must still be injected (merged into the next tool result) without mutating historical messages — pinned by Task 5's fork test.
2. **`on_runner_ready` fired before `reset()`** would hand out a runner with uninitialized `_pending_follow_ups` — pinned by Task 2's ordering assertion.
3. **Timeout path drops user input**: a follow-up accepted just before the execution timeout must still be relayed to the main agent, not silently lost — pinned by Task 5's timeout test.
4. **Cross-session oracle**: a `subagent_run_id` from another user's session must return `not_found`, never the other run's state — pinned by Task 7's isolation test.
5. **Hard refresh mid-run**: `user_message` entries must survive persistence round-trip and re-render after reload — pinned by Task 6's accumulator test.

---

### Task 1: Runner unconsumed-follow-up accessor

**Files:**
- Modify: `astrbot/core/agent/runners/tool_loop_agent_runner.py` (near `follow_up()`, ~L765)
- Test: `tests/test_tool_loop_agent_runner.py`

**Interfaces:**
- Produces: `ToolLoopAgentRunner.unconsumed_follow_up_texts() -> list[tuple[int, str]]` — returns `(seq, text)` for every ticket still in `_pending_follow_ups` (consumed tickets are already popped by `_consume_follow_up_notice`, so the remaining list IS the unconsumed set). Read-only; does not resolve or consume.

- [ ] **Step 1: Write the failing test**

```python
def test_unconsumed_follow_up_texts_lists_pending_tickets():
    # build a minimally-reset ToolLoopAgentRunner via the file's existing fixtures
    t1 = runner.follow_up(message_text="add a constraint")
    t2 = runner.follow_up(message_text="also check edge cases")
    assert runner.unconsumed_follow_up_texts() == [
        (t1.seq, "add a constraint"),
        (t2.seq, "also check edge cases"),
    ]
    runner._consume_follow_up_notice()
    assert runner.unconsumed_follow_up_texts() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tool_loop_agent_runner.py::test_unconsumed_follow_up_texts_lists_pending_tickets -v`
Expected: FAIL with `AttributeError: ... no attribute 'unconsumed_follow_up_texts'`

- [ ] **Step 3: Implement the accessor**

One method on `ToolLoopAgentRunner` returning `[(t.seq, t.text) for t in self._pending_follow_ups]`, with a Google-style docstring.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tool_loop_agent_runner.py::test_unconsumed_follow_up_texts_lists_pending_tickets -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/agent/runners/tool_loop_agent_runner.py tests/test_tool_loop_agent_runner.py
git commit -m "feat: expose unconsumed follow-up tickets on tool loop runner"
```

### Task 2: `on_runner_ready` hook in `Context.tool_loop_agent`

**Files:**
- Modify: `astrbot/core/star/context.py` (`tool_loop_agent`, ~L215-375)
- Test: `tests/unit/test_astr_agent_tool_exec.py` (reuse its fake-provider fixtures)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `tool_loop_agent(..., on_runner_ready: Callable[[AgentRunner], None] | None = None)` — kwarg popped from `kwargs` (NOT forwarded to `runner.reset()`), invoked synchronously exactly once **after** `await agent_runner.reset(...)` and **before** `agent_runner.step_until_done(...)`. Used by Task 5.

- [ ] **Step 1: Write the failing test**

```python
async def test_tool_loop_agent_invokes_on_runner_ready_after_reset():
    seen = []

    def _cb(runner):
        # Fires only after reset(): request and run context already exist.
        seen.append((runner, runner.req is not None, runner.run_context is not None))

    # run Context.tool_loop_agent(..., on_runner_ready=_cb) against the
    # module's fake provider, then:
    assert seen and all(flag for _, *flags in seen for flag in flags)
    assert len(seen) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_astr_agent_tool_exec.py::test_tool_loop_agent_invokes_on_runner_ready_after_reset -v`
Expected: FAIL (`TypeError: ... unexpected keyword` or empty `seen` — the kwarg is currently swallowed into `other_kwargs` and never invoked)

- [ ] **Step 3: Implement the hook**

In `tool_loop_agent`: read `on_runner_ready = kwargs.get("on_runner_ready")` next to the existing `response_sink` extraction, add it to the `other_kwargs` exclusion list, document it in the docstring kwargs block, and invoke it right after `await agent_runner.reset(...)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_astr_agent_tool_exec.py::test_tool_loop_agent_invokes_on_runner_ready_after_reset -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/star/context.py tests/unit/test_astr_agent_tool_exec.py
git commit -m "feat: add on_runner_ready hook to tool_loop_agent"
```

### Task 3: Subagent runner registry on `SubAgentManager`

**Files:**
- Modify: `astrbot/core/subagent_manager.py` (`SubAgentSession` ~L77-100; classmethods near `get_subagent_status` ~L1485)
- Test: `tests/unit/test_subagent_runner_registry.py` (new)

**Interfaces:**
- Produces (used by Tasks 5 and 7):
  - `@dataclass class SubAgentRunHandle` with fields `umo: str`, `subagent_run_id: str`, `agent_name: str`, `runner: Any`, `sink: Any` (typing: `TYPE_CHECKING` imports of `AgentRunner` / `SubAgentEventSink` to avoid import cycles).
  - `SubAgentManager.register_subagent_runner(session_id: str, handle: SubAgentRunHandle) -> None`
  - `SubAgentManager.unregister_subagent_runner(session_id: str, subagent_run_id: str, runner: Any) -> None` — removes only when the stored handle's runner IS the passed runner (identity check, same pattern as `unregister_active_runner`).
  - `SubAgentManager.get_subagent_run_handle(session_id: str, subagent_run_id: str) -> SubAgentRunHandle | None`
- New `SubAgentSession` field: `subagent_runners: dict = field(default_factory=dict)`.

- [ ] **Step 1: Write the failing test**

```python
def test_register_get_unregister_subagent_runner():
    handle = SubAgentRunHandle(umo="webchat:FriendMessage:webchat!u!s",
                               subagent_run_id="sa_abc", agent_name="coder",
                               runner=object(), sink=None)
    SubAgentManager.register_subagent_runner("webchat:FriendMessage:webchat!u!s", handle)
    assert SubAgentManager.get_subagent_run_handle("webchat:FriendMessage:webchat!u!s", "sa_abc") is handle
    # identity check: a different runner must not unregister
    SubAgentManager.unregister_subagent_runner("webchat:FriendMessage:webchat!u!s", "sa_abc", object())
    assert SubAgentManager.get_subagent_run_handle("webchat:FriendMessage:webchat!u!s", "sa_abc") is handle
    SubAgentManager.unregister_subagent_runner("webchat:FriendMessage:webchat!u!s", "sa_abc", handle.runner)
    assert SubAgentManager.get_subagent_run_handle("webchat:FriendMessage:webchat!u!s", "sa_abc") is None
```

Also assert isolation: `get_subagent_run_handle` with a different session id returns `None` even for the same run id.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_subagent_runner_registry.py -v`
Expected: FAIL (`SubAgentRunHandle` not defined)

- [ ] **Step 3: Implement registry**

Add the dataclass, the session field, and the three classmethods. Registration uses `_get_or_create_session`; lookup uses `get_session` and returns `None` for unknown session/run id. All classmethods get Google-style docstrings.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_subagent_runner_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/subagent_manager.py tests/unit/test_subagent_runner_registry.py
git commit -m "feat: add live subagent runner registry to SubAgentManager"
```

### Task 4: Sink `user_message` / `user_message_relayed` emitters

**Files:**
- Modify: `astrbot/core/subagent_event_sink.py`
- Test: `tests/unit/test_subagent_event_sink.py`

**Interfaces:**
- Produces (used by Tasks 5 and 7):
  - `SubAgentEventSink.user_message(seq: int, text: str) -> None` (async) — emits kind `user_message`, payload `{"seq": seq, "text": text}`.
  - `SubAgentEventSink.user_message_relayed(seq: int) -> None` (async) — emits kind `user_message_relayed`, payload `{"seq": seq}`.
- Both go through the existing `_emit`, so the closed-queue guard applies unchanged.

- [ ] **Step 1: Write the failing test**

```python
async def test_sink_emits_user_message_and_relayed():
    # reuse the module's captured-queue fixture
    await sink.user_message(0, "please also run the tests")
    await sink.user_message_relayed(0)
    kinds = [e["data"]["kind"] for e in captured]
    assert kinds == ["user_message", "user_message_relayed"]
    assert captured[0]["data"]["payload"] == {"seq": 0, "text": "please also run the tests"}
    assert captured[1]["data"]["payload"] == {"seq": 0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_subagent_event_sink.py -k user_message -v`
Expected: FAIL (`AttributeError`)

- [ ] **Step 3: Implement the two emitters**

Two thin async methods delegating to `_emit`, with Google-style docstrings.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_subagent_event_sink.py -v`
Expected: PASS (whole file)

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/subagent_event_sink.py tests/unit/test_subagent_event_sink.py
git commit -m "feat: emit subagent user-message events from progress sink"
```

### Task 5: Wire registry + relay fallback into `_execute_handoff`

**Files:**
- Modify: `astrbot/core/astr_agent_tool_exec.py` (`_execute_handoff`, ~L509-887)
- Test: `tests/unit/test_astr_agent_tool_exec.py`

**Interfaces:**
- Consumes: Task 1 `unconsumed_follow_up_texts()`, Task 2 `on_runner_ready`, Task 3 registry, Task 4 sink emitters.
- Produces: while a foreground webchat handoff runs, `SubAgentManager.get_subagent_run_handle(umo, sink.subagent_run_id)` returns the live handle (Task 7 relies on this). Unconsumed follow-ups are relayed by appending this exact block to the result text returned to the main agent:

```
RELAY_NOTICE_TEMPLATE = (
    "\n\n[SYSTEM NOTICE] While the subagent was finishing, the user sent "
    "additional message(s) that the subagent did not consume:\n"
    "{follow_up_lines}\n"
    "Decide whether to act on them (e.g. delegate a follow-up task) or "
    "acknowledge them in your reply."
)
```

- [ ] **Step 1: Write the failing tests**

```python
async def test_handoff_registers_runner_while_running_and_unregisters_after():
    # drive _execute_handoff with the module's fake provider;
    # inside the fake provider's first call, assert
    # SubAgentManager.get_subagent_run_handle(umo, run_id) is not None
    # (capture run_id from the sink's started event);
    # after the handoff returns, assert the same lookup is None.

async def test_handoff_relays_unconsumed_follow_up_to_main_agent():
    # fake provider answers immediately without tool calls;
    # after the runner is ready (poll the registry), call
    # handle.runner.follow_up(message_text="too late note");
    # assert the yielded CallToolResult text contains
    # "[SYSTEM NOTICE]" and "too late note";
    # assert a user_message_relayed event with the ticket seq was emitted.

async def test_handoff_relays_follow_up_on_timeout():
    # same as above but force the execution timeout path;
    # assert the error result text still carries the relay notice.

async def test_handoff_follow_up_injected_in_fork_mode():
    # configure context_inherit_mode="fork" (see tests/test_subagent_fork_mode.py);
    # queue a follow-up, let the fake provider issue one tool call;
    # assert the subagent runner's next tool-result message content
    # contains the follow-up text, and no historical message was mutated.
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_astr_agent_tool_exec.py -k "registers_runner or relays or fork_mode" -v`
Expected: FAIL (registry empty; no relay notice)

- [ ] **Step 3: Implement the wiring**

In `_execute_handoff`, after `sink` is created:

1. Define `def _on_runner_ready(runner):` — when `sink is not None`, register `SubAgentRunHandle(umo=umo, subagent_run_id=sink.subagent_run_id, agent_name=agent_name, runner=runner, sink=sink)` via `SubAgentManager.register_subagent_runner`.
2. Pass `on_runner_ready=_on_runner_ready` into `ctx.tool_loop_agent(...)`.
3. Define `async def _relay_unconsumed(text: str) -> str:` — when `sink is None`, return `text` unchanged; otherwise read the handle's runner, get `unconsumed_follow_up_texts()`, emit `sink.user_message_relayed(seq)` per ticket, and append `RELAY_NOTICE_TEMPLATE.format(follow_up_lines=...)` (numbered lines) when non-empty.
4. Apply `_relay_unconsumed` to the result text at **both** terminal yields: the success `CallToolResult` and the `asyncio.TimeoutError` error result.
5. Restructure the `try/except` minimally so a `finally:` unregisters the runner (move the completion tail into an `else:` clause; identity-check unregister).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_astr_agent_tool_exec.py tests/test_subagent_fork_mode.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/astr_agent_tool_exec.py tests/unit/test_astr_agent_tool_exec.py
git commit -m "feat: register live subagent runners and relay unconsumed follow-ups"
```

### Task 6: Persist `user_message` events in the history accumulator

**Files:**
- Modify: `astrbot/dashboard/services/chat_service.py` (`BotMessageAccumulator.add_subagent_event`, ~L477-537)
- Test: extend the existing `test_subagent_event_published_and_persisted` test module (grep it first; it lives near the ChatService tests)

**Interfaces:**
- Consumes: Task 4 event shapes (`user_message`: `{seq, text}`; `user_message_relayed`: `{seq}`).
- Produces: persisted `subagent_run` parts whose `activity` entries include `{"kind": "user_message", "text": str, "seq": int, "relayed": bool}`; the frontend reducer (Task 8) renders exactly this shape after a hard refresh.

- [ ] **Step 1: Write the failing test**

```python
def test_add_subagent_event_persists_user_message_and_relayed():
    acc.add_subagent_event({"subagent_run_id": "sa_1", "agent_name": "coder",
                            "kind": "started", "payload": {}, "ts": 1})
    acc.add_subagent_event({"subagent_run_id": "sa_1", "kind": "user_message",
                            "payload": {"seq": 0, "text": "note"}, "ts": 2})
    acc.add_subagent_event({"subagent_run_id": "sa_1", "kind": "user_message_relayed",
                            "payload": {"seq": 0}, "ts": 3})
    part = acc.subagent_runs["sa_1"]
    entry = part["activity"][-1]
    assert entry == {"kind": "user_message", "text": "note", "seq": 0, "relayed": True}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest <owning test module> -k user_message -v`
Expected: FAIL (events ignored / no such activity entry)

- [ ] **Step 3: Implement the two kinds in `add_subagent_event`**

`user_message`: append the activity entry with `relayed: False`. `user_message_relayed`: find the activity entry with matching `seq` and set `relayed = True` (no-op when absent).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest <owning test module> -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add astrbot/dashboard/services/chat_service.py <test file>
git commit -m "feat: persist subagent user-message events in chat history"
```

### Task 7: Follow-up delivery API route

**Files:**
- Modify: `astrbot/dashboard/schemas/` (add `ChatSubagentFollowUpRequest` beside `ChatFileChangeDiffRequest`)
- Modify: `astrbot/dashboard/api/chat.py`
- Test: `tests/unit/test_subagent_follow_up_route.py` (new; model on `tests/unit/test_chat_file_change_routes.py`)

**Interfaces:**
- Consumes: Task 3 registry, Task 4 `sink.user_message`, Task 1 ticket (`follow_up()` returns a `FollowUpTicket` with `.seq` or `None`).
- Produces: `POST /api/chat/subagent-follow-up` with body `ChatSubagentFollowUpRequest{session_id: str, subagent_run_id: str, text: str}`, response `{"accepted": bool, "reason"?: "not_found"|"finished"|"empty_text", "seq"?: int}` wrapped in the dashboard `ok()` envelope. Task 9's frontend calls this via the regenerated client.

- [ ] **Step 1: Write the failing tests**

```python
async def test_follow_up_accepted_and_echoed(...):
    # register a handle whose runner is a real minimally-reset
    # ToolLoopAgentRunner and whose sink is a recording stub;
    # POST {session_id, subagent_run_id, text: "steer it"};
    # assert {"accepted": True, "seq": 0}, ticket queued on the runner,
    # and sink.user_message awaited with (0, "steer it").

async def test_follow_up_rejected_states(...):
    # unknown run id -> accepted=False, reason="not_found"
    # whitespace text -> accepted=False, reason="empty_text"
    # runner done (follow_up returns None) -> accepted=False, reason="finished"

async def test_follow_up_cross_session_isolation(...):
    # register the handle under user A's umo; POST as user B with the same
    # session_id/run_id -> accepted=False, reason="not_found"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_subagent_follow_up_route.py -v`
Expected: FAIL (404 — route does not exist)

- [ ] **Step 3: Implement the route**

Add the pydantic schema, then in `chat.py`:

```python
@router.post("/chat/subagent-follow-up")
async def post_subagent_follow_up(
    payload: ChatSubagentFollowUpRequest,
    auth: AuthContext = Depends(require_chat_scope),
):
```

Body: strip text → `empty_text`; derive `umo = f"webchat:FriendMessage:webchat!{auth.username}!{payload.session_id}"`; `SubAgentManager.get_subagent_run_handle(...)` → `not_found`; `handle.runner.follow_up(message_text=text)` → `None` means `finished`; otherwise `await handle.sink.user_message(ticket.seq, text)` and return accepted with `seq`. No pipeline, no webchat queue, no history write.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_subagent_follow_up_route.py -v`
Expected: PASS

- [ ] **Step 5: Regenerate the frontend API client**

Run: `cd dashboard && pnpm generate:api`
Expected: the new route appears in `dashboard/src/api/generated/**`; commit the generated diff.

- [ ] **Step 6: Commit**

```bash
git add astrbot/dashboard/schemas astrbot/dashboard/api/chat.py tests/unit/test_subagent_follow_up_route.py dashboard/src/api/generated
git commit -m "feat: add subagent follow-up delivery endpoint"
```

### Task 8: Frontend reducer — `user_message` activity kind

**Files:**
- Modify: `dashboard/src/composables/subagentRunReducer.ts`
- Test: `dashboard/src/composables/subagentRunReducer.spec.ts`

**Interfaces:**
- Consumes: Task 4 event shapes; Task 6's persisted activity entry shape.
- Produces: `SubAgentActivity` gains `{ kind: "user_message"; text: string; seq: number; relayed?: boolean }`. Task 9 renders entries of this kind.

- [ ] **Step 1: Write the failing tests**

```typescript
it("appends user_message activity and marks it relayed", () => {
  const parts: MessagePart[] = [];
  applySubAgentEvent(parts, ev({ kind: "started", payload: {} }));
  applySubAgentEvent(parts, ev({ kind: "user_message", payload: { seq: 0, text: "note" } }));
  applySubAgentEvent(parts, ev({ kind: "user_message_relayed", payload: { seq: 0 } }));
  const part = parts[0] as any;
  expect(part.activity.at(-1)).toEqual({ kind: "user_message", text: "note", seq: 0, relayed: true });
});

it("keeps user_message in chronological order between think and tool_call", () => {
  // started -> reasoning_delta -> user_message -> tool_call
  // expect activity kinds: ["think", "user_message", "tool_call"]
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && pnpm vitest run src/composables/subagentRunReducer.spec.ts`
Expected: FAIL

- [ ] **Step 3: Implement the reducer branches**

In `applySubAgentEvent`: `user_message` pushes `{kind: "user_message", text, seq, relayed: false}`; `user_message_relayed` sets `relayed = true` on the activity entry with matching `seq` (scan `part.activity` backwards; no-op when absent). Extend the `SubAgentActivity` union.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && pnpm vitest run src/composables/subagentRunReducer.spec.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/composables/subagentRunReducer.ts dashboard/src/composables/subagentRunReducer.spec.ts
git commit -m "feat: render subagent follow-up events in run reducer"
```

### Task 9: `SubAgentRunBlock` follow-up input + prop threading + i18n

**Files:**
- Modify: `dashboard/src/components/chat/message_list_comps/SubAgentRunBlock.vue`
- Modify: `dashboard/src/components/chat/ChatMessageList.vue`, `dashboard/src/components/chat/MessageList.vue`, `dashboard/src/components/chat/StandaloneChat.vue` (each renders `<SubAgentRunBlock>` — thread one new prop)
- Modify: the i18n locale files backing the module `SubAgentRunBlock` already uses via `useModuleI18n` (read the file header to find the module, then add keys for en-US and zh-CN)
- Test: extend the `SubAgentRunBlock` mount tests in `dashboard/src/composables/subagentRunReducer.spec.ts`

**Interfaces:**
- Consumes: Task 7 endpoint (via the regenerated client in `@/api/generated`), Task 8 activity kind.
- Produces: `SubAgentRunBlock` accepts a new optional prop `sessionId: String`. When `part.status === "running"` and `sessionId` is set, it renders the follow-up input row.

- [ ] **Step 1: Write the failing tests**

```typescript
it("shows the follow-up input only while running", () => {
  // mount with part.status "running" + sessionId -> input exists
  // mount with status "completed" -> no input
  // mount running without sessionId -> no input
});

it("posts the follow-up and clears the field", async () => {
  // mock the generated client call to resolve { data: { accepted: true, seq: 0 } };
  // type text, trigger submit; assert the mock received
  // { session_id, subagent_run_id: part.subagent_run_id, text } and the field cleared.
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && pnpm vitest run src/composables/subagentRunReducer.spec.ts`
Expected: FAIL (no input element)

- [ ] **Step 3: Implement the input row**

In `SubAgentRunBlock.vue`:

- New prop `sessionId: { type: String, default: "" }`.
- A compact single-row input (plain `<input>` + icon button styled to match the block's existing toolbar — no Vuetify card chrome) shown when `part.status === "running" && sessionId`.
- Submit handler: guard empty/in-flight; call the generated endpoint function with `{ session_id: sessionId, subagent_run_id: part.subagent_run_id, text }`; on `accepted: false` show the existing snackbar/notice pattern with the "run finished, message not delivered" copy. Do NOT optimistically append to `part.activity` — the SSE `user_message` echo renders it.
- Render `user_message` activity entries in the timeline as a compact user-styled line; when `relayed`, append the muted "not delivered to subagent · relayed to main agent" suffix.
- i18n keys (add to the block's existing i18n module, en-US + zh-CN): placeholder (`Ask {name}…` / `向 {name} 追加指令…`), relayed suffix, and the rejection notice.
- Thread `sessionId` from the three parent lists (they all have the current session id in scope).

- [ ] **Step 4: Run tests + typecheck to verify they pass**

Run: `cd dashboard && pnpm vitest run src/composables/subagentRunReducer.spec.ts && pnpm vue-tsc --noEmit` (or the repo's typecheck script if different)
Expected: PASS, no type errors

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/chat dashboard/src/i18n dashboard/src/composables/subagentRunReducer.spec.ts
git commit -m "feat: add follow-up input to running subagent blocks"
```

### Task 10: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Backend lint + targeted tests**

Run: `ruff format . && ruff check . && uv run pytest tests/test_tool_loop_agent_runner.py tests/unit/test_astr_agent_tool_exec.py tests/unit/test_subagent_runner_registry.py tests/unit/test_subagent_event_sink.py tests/unit/test_subagent_follow_up_route.py tests/test_subagent_fork_mode.py -v`
Expected: all PASS, no lint errors

- [ ] **Step 2: Frontend tests + typecheck**

Run: `cd dashboard && pnpm vitest run src/composables/subagentRunReducer.spec.ts && pnpm vue-tsc --noEmit`
Expected: PASS

- [ ] **Step 3: Manual smoke**

Run `uv run main.py` + `cd dashboard && pnpm dev`; create a subagent, delegate a slow task (e.g. a shell sleep loop), then verify: (a) input appears on the running block; (b) a follow-up sent mid-tool-loop appears as `user_message` and is visibly acknowledged by the subagent's next step; (c) a follow-up sent during the final answer is marked relayed and the main agent mentions it; (d) hard refresh restores the `user_message` entries.

- [ ] **Step 4: Final commit (if any fixes)**

```bash
git commit -m "test: verify subagent follow-up input end to end"
```
