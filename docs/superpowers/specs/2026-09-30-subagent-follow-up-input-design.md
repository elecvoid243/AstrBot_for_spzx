# Subagent Follow-Up Input — Design

- Date: 2026-09-30
- Status: Draft (pending review)
- Scope: foreground webchat subagent runs only

## 1. Problem

The ChatUI renders live subagent execution (thinking, tool calls, replies)
through `SubAgentRunBlock`, but the channel is strictly one-way: while a
subagent runs, the user has no way to send it an additional instruction. The
main composer only targets the main agent (its mid-run messages become
main-agent follow-ups via the pending-follow-up queue), so a user who wants
to steer a running subagent — correct its direction, add a constraint, answer
a question it is implicitly working around — must wait for the run to finish
and re-delegate.

## 2. Goals

- Let the user type a follow-up message into a **running** foreground
  subagent run, directly from its `SubAgentRunBlock` in the ChatUI.
- Deliver that message into the subagent's LLM context at the next tool-call
  boundary, reusing the existing follow-up ticket mechanism.
- Echo the message in the run block's activity timeline, persisted so a hard
  refresh restores it.
- Handle the "run finished before consuming the message" case without
  silently dropping user input.

## 3. Non-goals

- Background subagents (`background_task=true`). They have no
  `SubAgentEventSink` and no ChatUI run block today; the registry is shaped
  to allow them later, but no UI or endpoint behavior is built for them.
- Routing subagent messages through the main composer (e.g. `@subagent`).
- Non-webchat platforms (the sink and the endpoint are webchat/dashboard
  only, same as the existing progress display).
- Editing or cancelling an already-accepted follow-up.

## 4. Background: existing mechanisms this design reuses

### 4.1 Subagent execution and progress streaming

- `transfer_to_subagent` is intercepted by
  `FunctionToolExecutor.execute()` (`astrbot/core/astr_agent_tool_exec.py`),
  which resolves the `HandoffTool` from `SubAgentManager` and calls
  `_execute_handoff()` (foreground) or `_execute_handoff_background()`.
- `_execute_handoff()` creates a `SubAgentEventSink`
  (`astrbot/core/subagent_event_sink.py`) via `_maybe_create_subagent_sink()`
  — only for webchat foreground handoffs with
  `provider_settings.show_subagent_progress` enabled — then runs the subagent
  through `Context.tool_loop_agent(..., response_sink=sink)`.
- `tool_loop_agent()` (`astrbot/core/star/context.py`) instantiates a
  `ToolLoopAgentRunner` internally and returns only the final `LLMResponse`;
  the runner handle is currently not exposed to the caller.
- The sink maps runner `AgentResponse`s to `subagent_event` payloads written
  to the **main run's** webchat back queue. `ChatService._consume_chat_run`
  folds them into a persisted `subagent_run` part via
  `BotMessageAccumulator.add_subagent_event`, and the dashboard's
  `applySubAgentEvent` reducer
  (`dashboard/src/composables/subagentRunReducer.ts`) renders them live in
  `SubAgentRunBlock.vue`.

### 4.2 Main-agent follow-up capture

`astrbot/core/pipeline/process_stage/follow_up.py` already implements
mid-run user messages for the **main** agent:

- The main runner is registered per-UMO via `register_active_runner`.
- A new user message arriving mid-run is captured by
  `try_capture_follow_up`, which calls `runner.follow_up(message_text=...)`
  on the `ToolLoopAgentRunner`, producing a `FollowUpTicket`.
- The runner merges queued tickets into the next tool result via
  `_merge_follow_up_notice()` (`FOLLOW_UP_NOTICE_TEMPLATE`), marks them
  consumed, and resolves them; unconsumed tickets are activated as new
  normal turns in strict arrival order.

Because a subagent runner **is** a `ToolLoopAgentRunner`, `follow_up()` and
the notice-merge path work for it unchanged. What is missing: a handle to
the subagent runner, a delivery channel from the dashboard, and UI.

## 5. Design

### 5.1 Runner registry (backend)

`SubAgentManager`'s per-session state gains a registry:

```python
# SubAgentSession
subagent_runners: dict[str, SubAgentRunHandle]

@dataclass
class SubAgentRunHandle:
    umo: str
    subagent_run_id: str
    agent_name: str
    runner: AgentRunner            # ToolLoopAgentRunner
    sink: SubAgentEventSink | None
```

Keyed by `subagent_run_id` (globally unique, `sa_<12 hex>`), storing the UMO
so the API layer can verify session ownership.

- **Register**: in `_execute_handoff()`, when the runner becomes available
  (see 5.2) and a sink exists (foreground webchat handoff — scope boundary).
- **Unregister**: in a `finally` around the handoff execution, covering
  complete / fail / timeout / abort.

### 5.2 Exposing the runner handle

`Context.tool_loop_agent()` creates the runner internally. Add one optional
kwarg:

```python
on_runner_ready: Callable[[AgentRunner], None] | None = None
```

It is popped from `kwargs` alongside `response_sink` (so it is not forwarded
to `runner.reset()`) and invoked once, **after** `runner.reset(...)` has
initialized the runner's mutable state (`_pending_follow_ups` etc.) and
before the run loop starts. `_execute_handoff()` passes a closure that
stores the runner into the registry entry together with the sink.

This mirrors the existing `response_sink` observer pattern and changes
nothing for other `tool_loop_agent` callers.

### 5.3 Delivery API (dashboard)

New route in `astrbot/dashboard/api/chat.py`:

```
POST /api/chat/subagent-follow-up
body: { "session_id": str, "subagent_run_id": str, "text": str }
auth: require_chat_scope
```

Behavior:

1. Derive the UMO from `auth.username` + `session_id` (same construction as
   the existing webchat session routes).
2. Look up `subagent_run_id` in the registry; verify the entry's UMO matches.
3. Call `runner.follow_up(message_text=text)`:
   - returns a ticket → accepted;
   - returns `None` (runner done / stop requested) → rejected.
4. On accept, emit the echo event (5.4) through the entry's sink.

Responses:

- `200 {accepted: true, seq: int}`
- `200 {accepted: false, reason: "not_found" | "finished" | "empty_text"}`
  (not an error from the user's perspective — the run simply ended)
- `403/404` for cross-user or unknown sessions, following existing chat
  route conventions.

The endpoint deliberately **does not** go through the webchat message queue
or the pipeline: the follow-up must not be captured by the main agent's
`try_capture_follow_up`, must not spawn a new turn, and must not persist a
top-level user history record. The message lives inside the subagent run
block only.

### 5.4 Echo events

`SubAgentEventSink` gains:

```python
async def user_message(self, seq: int, text: str) -> None
async def user_message_relayed(self, seq: int) -> None
```

emitting `subagent_event` payloads with new kinds:

- `user_message` — `{seq, text}`: the user sent a follow-up; append a
  `user_message` entry to the run block's activity timeline.
- `user_message_relayed` — `{seq}`: the run ended before consuming this
  follow-up; it was relayed to the main agent (see 5.5). The UI marks the
  entry "未送达子代理 · 已转达主 Agent".

Because events flow through the main run's back queue, every attached
client (including a reconnected one) sees them, and
`BotMessageAccumulator.add_subagent_event` persists them into the
`subagent_run` part for hard-refresh restoration.

### 5.5 Unconsumed follow-up fallback

Follow-up notices are injected at **tool-result boundaries**. If the
subagent is already producing its final answer (no further tool calls), an
accepted ticket may never be consumed. On handoff completion in
`_execute_handoff()`:

1. Read the runner's remaining unconsumed tickets (small read-only accessor
   on the runner, e.g. `unconsumed_follow_up_texts() -> list[tuple[int,
   str]]`).
2. For each, emit `user_message_relayed` via the sink.
3. Append their text to the `CallToolResult` returned to the **main** agent:

   ```
   <original subagent result>

   [SYSTEM NOTICE] While the subagent was finishing, the user sent
   additional message(s) that the subagent did not consume:
   1. ...
   Decide whether to act on them (e.g. delegate a follow-up task) or
   acknowledge them in your reply.
   ```

This guarantees user input is never silently dropped, and the decision of
"what to do with it" stays with the main agent, which owns the conversation.

Consumed tickets need no fallback: their text is merged into a tool result,
which `_save_subagent_history()` already persists as part of
`runner_messages`.

### 5.6 Frontend

`dashboard/src/composables/subagentRunReducer.ts`:

- `SubAgentActivity` gains `{ kind: "user_message"; text: string; seq: number; relayed?: boolean }`.
- `applySubAgentEvent` handles `user_message` (push activity entry) and
  `user_message_relayed` (mark matching `seq` entry `relayed = true`).

`dashboard/src/components/chat/message_list_comps/SubAgentRunBlock.vue`:

- When `status === "running"`, render a compact input row at the bottom of
  the block: placeholder "向 {agent_name} 追加指令…", Enter to send, send
  button, no attachments.
- On submit: POST the endpoint; **do not** optimistically append — the SSE
  echo (`user_message`) renders it, avoiding duplicates. Disable the input
  while a submit is in flight; on `accepted: false`, surface the reason
  ("该任务已结束，消息未送达") via the existing snackbar pattern.
- `user_message` entries render as right-aligned-ish compact user bubbles
  inside the activity timeline; `relayed` entries get a muted status suffix.

`dashboard/src/components/chat/ChatMessageList.vue`, `MessageList.vue`,
`StandaloneChat.vue`: pass the current `session_id` down to
`SubAgentRunBlock` (prop threading only; no logic).

API client: after the route lands, regenerate with
`cd dashboard && pnpm generate:api`.

### 5.7 Consistency and safety notes

- **Single-task invariant**: the existing RUNNING guard guarantees one
  active run per subagent; multiple follow-ups to the same run append
  tickets in arrival order. The main agent's `order_seq` state machine is
  not needed here — it exists to activate unconsumed follow-ups as new
  turns, which 5.5 replaces with relay-to-main-agent.
- **Fork-mode prefix cache**: the notice is merged into a *new* tool-result
  message appended at the tail; no historical message is mutated, so the
  byte-identical prefix invariant is preserved.
- **Stop interaction**: `follow_up()` already returns `None` when a stop was
  requested, so a follow-up cannot resurrect a stopping run.
- **Config gating**: the registry entry only exists when the sink exists, so
  `show_subagent_progress: false` or non-webchat sessions simply have no
  input affordance and the endpoint returns `not_found`.

## 6. Edge cases

| Case | Behavior |
|---|---|
| Run ends between UI render and POST | `accepted: false, reason: "finished"`; UI shows a hint |
| Unknown / other user's `subagent_run_id` | 404/403, no oracle about other sessions |
| Empty / whitespace-only text | `accepted: false, reason: "empty_text"` |
| Multiple rapid follow-ups | All queued as tickets; merged into the next tool result(s) in order |
| Follow-up during final answer | Accepted, then relayed to main agent at completion (5.5) |
| Hard refresh mid-run | Persisted `user_message` entries restore with the run block |
| Main agent follow-up sent at the same time | Unchanged existing behavior; the two channels are independent |

## 7. Testing

Backend (`tests/`):

- Registry: register on handoff start, unregister on all terminal paths
  (complete/fail/timeout/abort).
- Endpoint: accept path calls `runner.follow_up` and emits `user_message`;
  reject paths (`not_found`, `finished`, `empty_text`); cross-user
  rejection.
- Injection: a follow-up accepted mid-run appears in the subagent's next
  tool-result content (reuse the fake-provider patterns from
  `tests/test_tool_loop_agent_runner.py`).
- Fallback: an unconsumed follow-up is appended to the handoff
  `CallToolResult` and `user_message_relayed` is emitted.
- Sink: extend `tests/unit/test_subagent_event_sink.py` for the two new
  kinds.

Frontend:

- `subagentRunReducer.spec.ts`: `user_message` push, `user_message_relayed`
  marking, ordering vs think/tool_call entries.
- `SubAgentRunBlock` mount test: input visible only while `running`; submit
  posts the payload; disabled state during flight.

Manual: `uv run main.py` + `pnpm dev`; create a subagent, delegate a long
task (e.g. a slow shell loop), type follow-ups mid-run, verify injection in
the subagent's next step, echo in the block, hard-refresh restore, and the
relay path when sent during the final answer.

## 8. Files touched (estimate)

| File | Change |
|---|---|
| `astrbot/core/star/context.py` | `tool_loop_agent` `on_runner_ready` kwarg |
| `astrbot/core/agent/runners/tool_loop_agent_runner.py` | read-only accessor for unconsumed follow-ups |
| `astrbot/core/subagent_manager.py` | `SubAgentRunHandle` + registry accessors |
| `astrbot/core/astr_agent_tool_exec.py` | register/unregister; relay fallback in `_execute_handoff` |
| `astrbot/core/subagent_event_sink.py` | `user_message` / `user_message_relayed` emitters |
| `astrbot/dashboard/api/chat.py` (+ schemas) | new POST route |
| `astrbot/dashboard/services/chat_service.py` | `add_subagent_event`: persist new kinds |
| `dashboard/src/composables/subagentRunReducer.ts` | new activity kind + events |
| `dashboard/src/components/chat/message_list_comps/SubAgentRunBlock.vue` | input row + user_message rendering |
| `dashboard/src/components/chat/{ChatMessageList,MessageList,StandaloneChat}.vue` | thread `session_id` prop |
| `dashboard/src/api/generated/**` | `pnpm generate:api` |
| `tests/**`, `dashboard/src/composables/subagentRunReducer.spec.ts` | coverage per §7 |

## 9. Rollout

- No migration; pure additive change behind existing config
  (`show_subagent_progress`).
- Conventional commit: `feat: allow follow-up messages to running subagents from ChatUI`.
