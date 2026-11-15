# ChatUI Developer Mode: LLM Request Snapshots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin-only, in-memory LLM request snapshot recorder and a resizable ChatUI inspector that shows the final provider payload, response summary, tool executions, provenance, and diagnostics.

**Architecture:** A core singleton captures the final wire body at four provider network boundaries, keyed by a contextvar scope. The main runner attaches response, tool, and diagnostic metadata, then emits compact envelope events to ChatService. ChatService keeps envelopes in run state, binds bot message ids to turn ids in memory, and exposes admin-only list/detail endpoints. A Vue composable and two components render the inspector; nothing is persisted.

**Tech Stack:** Python 3.10+, FastAPI, pytest / pytest-asyncio, Vue 3 + TypeScript + Vuetify + Pinia, Vitest, browser automation.

**Spec:** [2026-11-15-chatui-developer-mode-design.md](../specs/2026-11-15-chatui-developer-mode-design.md)

## Global Constraints

- `chatui.developer_mode` defaults to `false`; only admin/config scope may write it. The flag is instance-global; read APIs are admin-only and enforce session ownership.
- Snapshots live only in memory: TTL 7200 s, 512 KB per snapshot, 128 per session, 16 MB per session, 64 MB total, 1024 message bindings. No database table, no history/export/search field.
- Capture must not change provider request behavior; every recorder call is wrapped and never raises into the request path.
- Do not persist snapshots in `build_bot_history_content`; `llm_request_snapshots` is live metadata only.
- Do not capture `raw_completion`, per-chunk timings, subagent/team internal runs, or cost estimates.
- Reuse `agent_stats`, `context_compression`, `llm_request_injections`, and `prefix_diagnostics` hash helpers; do not add a second stats/compression channel.
- Keep the existing injection UI; the snapshot's injection section reuses `LlmRequestInjectionsPanel.vue`.
- UI copy must exist in zh-CN, en-US, ru-RU, ja-JP; no gradients, glassmorphism, or emoji-as-icons.
- Python: `pathlib.Path`, Google-style docstrings, English comments/logs, Python 3.10+ compatible.
- New files carry `Author: elecvoid243, 2026-11-15` in the module/component header where the codebase uses that pattern.
- Run `ruff format .` and `ruff check .` before each commit; conventional commits in English.
- Frontend commands run from `dashboard/`; `pnpm build` writes `dashboard/dist`. Copy that folder to the AstrBot runtime `data/dist` for local hot verification.

## Review Focus

1. **Provider final-body fidelity** — a request where the SDK moves fields into `extra_body` or nests them under a provider config (OpenAI Chat, OpenAI Responses, Anthropic, Gemini) must show the body actually sent, not the pre-transform payload. Pinned in Task 4.
2. **Oversized context** — a payload over 512 KB must drop oldest messages first, keep the newest messages, system prompt, and tool schemas, and mark truncation; it must never present a cut payload as complete. Pinned in Task 2.
3. **Permission and ownership** — a non-admin, a foreign session, and an unknown snapshot must return 403/404 without leaking session existence; `GET /chat/developer-mode` must still return `can_edit=false` for chat-scope users. Pinned in Task 8.
4. **Toggle and history isolation** — turning the flag off mid-run stops capture immediately, and no snapshot field ever appears in persisted history or orphan-reply handling. Pinned in Tasks 1, 5, and 7.
5. **Refresh and expiry semantics** — after TTL/restart a session list is empty with `expired=false`; a message-scoped query returns `expired=true`; the UI distinguishes the two and never renders stale data. Pinned in Tasks 7, 8, and 11.

## File Structure

| Area | Files | Responsibility |
|---|---|---|
| Recorder | Create `astrbot/core/provider/request_snapshot.py` | Scope, envelope, ring buffer, truncation, redaction, provenance, diagnostics, bindings |
| Shared redaction | Modify `astrbot/core/pipeline/llm_request_trace.py` | `bounded_context_value(limit, redactions)`; default behavior unchanged |
| Config | Modify `astrbot/core/config/default.py` | `chatui.developer_mode = False` |
| Providers | Modify `openai_source.py`, `openai_responses_source.py`, `anthropic_source.py`, `gemini_source.py` | Capture final body before the SDK call |
| Runner | Modify `astrbot/core/agent/runners/tool_loop_agent_runner.py` | Scope, response/tool/diagnostics, envelope event, subagent skip |
| Aux/provenance | Modify `astrbot/core/agent/context/compressor.py`, `astrbot/core/astr_main_agent.py`, `file_access_guidance.py`, `group_chat_context.py` | Compression/title kinds and provenance registration |
| Chat service | Modify `astrbot/dashboard/services/chat_service.py`, `chatui_settings.py`, `webchat_event.py` | Run state, binding, list/detail, toggle, orphan skip |
| API | Modify `astrbot/dashboard/api/chat.py`, `schemas.py`, `openspec/openapi-v1.yaml` | Four routes, strict bool, OpenAPI |
| Frontend data | Create `dashboard/src/composables/llmRequestSnapshots.ts`, `useLlmSnapshots.ts`; modify `useMessages.ts` | Types, parser, merge, API, live field |
| Frontend UI | Create `ChatDevModeInspector.vue`, `LlmSnapshotDetail.vue` | Shell, rail, sections, states |
| Frontend entry | Modify `stores/chatHeader.ts`, `layouts/.../VerticalHeader.vue`, `Chat.vue`, `ChatMessageList.vue` | DEV chip, marker, inspector mount |
| i18n | Modify four `features/chat.json` | `devMode.*` copy |

---

### Task 1: Recorder core, scope, retention, bindings

**Files:**
- Create: `astrbot/core/provider/request_snapshot.py`
- Modify: `astrbot/core/pipeline/llm_request_trace.py` (`bounded_context_value`)
- Modify: `astrbot/core/config/default.py` (`chatui.developer_mode`)
- Test: `tests/unit/test_request_snapshot.py`

**Interfaces:**
- Consumes: `astrbot_config`, `Provider.meta()/get_model()/get_current_key()/get_keys()`, `LLMResponse` (type-only import).
- Produces:
  - `MAX_BYTES_PER_SNAPSHOT=524288`, `MAX_SNAPSHOTS_PER_SESSION=128`, `MAX_BYTES_PER_SESSION=16777216`, `MAX_TOTAL_BYTES=67108864`, `TTL_SECONDS=7200`, `MAX_MESSAGE_BINDINGS=1024`.
  - `LLMRequestScope(session_id, umo, turn_id, invocation_id, kind, step, stage, fallback_index, is_subagent)`.
  - `request_snapshot_scope(**overrides) -> Iterator[LLMRequestScope]`.
  - `developer_mode_enabled() -> bool`.
  - `RequestSnapshotRecorder(clock=time.time)` with `capture(provider, raw, *, adapter=None, session_id=None, umo=None) -> str | None`, `list_for_session(session_id, *, message_id=None, turn_id=None, limit=50) -> dict`, `get(snapshot_id) -> dict | None`, `bind_message(message_id, turn_id, session_id) -> None`, `limits() -> dict`, `reset() -> None`.
  - `metadata_for_invocation(invocation_id: str) -> list[dict]`.
  - `request_snapshots = RequestSnapshotRecorder()`.
- `capture` stores a sanitized raw body and an envelope with `snapshot_id, session_id, umo, turn_id, invocation_id, kind, stage, step, status, created_at, bytes, truncated, redactions, provider, request, counts, response, tool_executions, diagnostics, provenance, injections, tool_meta, env`.

- [ ] **Step 1: Write the failing recorder tests**

Create `tests/unit/test_request_snapshot.py` with these cases:

```python
def test_disabled_capture_is_a_noop(monkeypatch):
    monkeypatch.setattr(request_snapshot, "developer_mode_enabled", lambda: False)
    with request_snapshot_scope(session_id="s1", umo="webchat:FriendMessage:s1"):
        assert request_snapshots.capture(FakeProvider(), {"messages": []}) is None
    assert request_snapshots.list_for_session("s1")["items"] == []

def test_non_webchat_umo_is_not_captured(monkeypatch):
    monkeypatch.setattr(request_snapshot, "developer_mode_enabled", lambda: True)
    with request_snapshot_scope(session_id="s1", umo="qq:FriendMessage:123"):
        assert request_snapshots.capture(FakeProvider(), {"messages": []}) is None

def test_capture_records_scope_and_attempts(monkeypatch):
    monkeypatch.setattr(request_snapshot, "developer_mode_enabled", lambda: True)
    with request_snapshot_scope(session_id="s1", umo="webchat:FriendMessage:s1", turn_id="t1",
                                 invocation_id="i1", kind="chat", step=2):
        first = request_snapshots.capture(FakeProvider(), {"messages": []})
        second = request_snapshots.capture(FakeProvider(), {"messages": []})
    items = request_snapshots.list_for_session("s1")["items"]
    assert [item["snapshot_id"] for item in items] == [second, first]
    assert [item["request"]["attempt"] for item in items] == [2, 1]

def test_session_cap_evicts_oldest(monkeypatch):
    ...

def test_ttl_expires_entries():
    now = [1000.0]
    rec = RequestSnapshotRecorder(clock=lambda: now[0])
    ...
    now[0] += TTL_SECONDS + 1
    assert rec.list_for_session("s1")["items"] == []

def test_oversized_payload_is_truncated_and_redacted(monkeypatch):
    ...
    assert item["truncated"] is True
    assert item["redactions"] >= 1

def test_message_binding_resolves_turn_and_reports_expiry(monkeypatch):
    ...
    assert request_snapshots.list_for_session("s1", message_id=7)["items"]
    assert request_snapshots.list_for_session("s1", message_id=999)["expired"] is True
```

Use a `FakeProvider` with `meta()` returning `SimpleNamespace(id="p1", type="openai_chat", model="m1")`, `get_model() -> "m1"`, `get_current_key() -> "sk-secret-1234"`, `get_keys() -> ["sk-secret-1234"]`, and `provider_config={"api_base": "https://api.example.com/v1"}`.

Add an `autouse=True` fixture that calls `request_snapshots.reset()` before and after each test, so singleton state cannot leak.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_request_snapshot.py -v`
Expected: FAIL with `ModuleNotFoundError: astrbot.core.provider.request_snapshot`.

- [ ] **Step 3: Implement the recorder**

Create `astrbot/core/provider/request_snapshot.py` with the constants, `LLMRequestScope`, a contextvar stack, `request_snapshot_scope`, `developer_mode_enabled`, and `RequestSnapshotRecorder`. Implement `capture` to:

1. Return `None` when disabled, `is_subagent`, no `session_id`, or the UMO is not a `webchat:` session (this is a ChatUI-only tool).
2. Allocate `snapshot_id = uuid.uuid4().hex`; increment `attempt` per `invocation_id`; set `status="pending"`.
3. Copy `raw` through `bounded_context_value(raw, limit=MAX_BYTES_PER_SNAPSHOT)` (Task 1 also adds `limit` and `redactions` to that helper; default remains 8192).
4. Build `provider` from `provider.meta()`, `provider.provider_config`, `get_model()`, `get_current_key()`/`get_keys()`: id, type, class, model, host from `api_base` (credentials/query removed), proxy boolean, key fingerprint/index/count. Never store the key.
5. Store `adapter` as the explicit argument or `getattr(provider, "SNAPSHOT_ADAPTER", None)`.
6. Build `request` from the current scope and the raw body: stream, temperature, top_p, max_tokens, tool_choice, reasoning_effort, attempt, fallback_index, timeout_s, retry_max.
7. Build `env` best effort: `file_access_mode`, `project`, `workspace`, `config_profile`, `astrbot_version`, `python`, `env_source` (`request` or `current`).
8. Add the envelope to the session deque and the global id map; evict oldest by count/bytes/TTL.
9. Return the id.

Implement `list_for_session` with newest-first ordering. Resolve `message_id` through a bounded `OrderedDict[str, tuple[session_id, turn_id, expires_at]]`; a miss returns `{"enabled": True, "expired": True, "items": [], "limits": ..., "dropped": 0}`. A session-level call with no items returns `expired=False`.

`list_for_session` returns lightweight envelope copies: drop `injections` and `provenance` payloads (keep `counts`/`provenance` labels where useful); `get(snapshot_id)` returns the full envelope for the detail view.

Implement `bind_message` with the same TTL and 1024-entry cap. `limits()` returns the five public constants plus `ttl_seconds`.

- [ ] **Step 4: Add the config key**

In `DEFAULT_CONFIG["chatui"]`, add `"developer_mode": False` next to `"remote_sessions_enabled"`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_request_snapshot.py -v`
Expected: PASS.

- [ ] **Step 6: Run the existing redaction tests**

Run: `uv run pytest tests/unit/test_agent_runner_injection_trace.py -v`
Expected: PASS; default `bounded_context_value` output unchanged.

- [ ] **Step 7: Commit**

```bash
git add astrbot/core/provider/request_snapshot.py \
  astrbot/core/pipeline/llm_request_trace.py \
  astrbot/core/config/default.py tests/unit/test_request_snapshot.py
git commit -m "feat(provider): add in-memory llm request snapshot recorder"
```

---

### Task 2: Provider adapters, normalized view, structural truncation

**Files:**
- Modify: `astrbot/core/provider/request_snapshot.py`
- Test: `tests/unit/test_request_snapshot_adapters.py`

**Interfaces:**
- Consumes: `capture` and envelope from Task 1.
- Produces:
  - `ProviderAdapter` with `system_text(raw)`, `messages(raw)`, `tools(raw)`, `params(raw)`, `extra(raw)`.
  - `ADAPTERS = {"openai_chat": ..., "openai_responses": ..., "anthropic": ..., "gemini": ...}`.
  - `build_view(snapshot: dict) -> dict` returning `{"system", "messages", "tools", "params", "extra"}`.
  - `capture(..., adapter: str | None = None)`; when `adapter` is `None`, use `provider.SNAPSHOT_ADAPTER`.
  - `attach_tool_meta(invocation_id: str, *, tool_set=None, denied_tools=None) -> None`; Task 5 calls it with `req.func_tool`/`req.denied_tools`.

- [ ] **Step 1: Write the failing adapter tests**

Create `tests/unit/test_request_snapshot_adapters.py` with one raw payload per provider:

```python
def test_openai_chat_view_extracts_system_messages_tools_and_params():
    raw = {
        "model": "m1",
        "messages": [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "hi"},
        ],
        "tools": [{"type": "function", "function": {"name": "read_file",
                   "description": "read", "parameters": {"type": "object"}}}],
        "temperature": 0.7,
        "extra_body": {"chat_template_kwargs": {"reasoning_effort": 100}},
    }
    view = build_view(snapshot_with(raw, "openai_chat"))
    assert view["system"]["text"] == "SYS"
    assert view["messages"][1]["role"] == "user"
    assert view["tools"][0]["name"] == "read_file"
    assert view["params"]["temperature"] == 0.7
    assert view["extra"]["chat_template_kwargs"]["reasoning_effort"] == 100

def test_openai_responses_view_uses_instructions_and_input():
    ...

def test_anthropic_view_uses_system_and_input_schema():
    ...

def test_gemini_view_uses_config_system_instruction_and_contents():
    ...

def test_truncation_drops_oldest_messages_first(monkeypatch):
    ...
    assert view["messages"][0]["content"] == "newest"
    assert snapshot["counts"]["omitted_messages"] > 0
    assert snapshot["truncated"] is True
```

The truncation test should build 40 messages of ~20 KB, assert the first kept message is the newest, and assert system/tools remain present.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_request_snapshot_adapters.py -v`
Expected: FAIL with missing `build_view` / `ADAPTERS`.

- [ ] **Step 3: Implement adapters and view**

Add the four adapters. `build_view` reads the envelope's `adapter` field and the stored raw body. Normalize each message to:

`{"index", "role", "name", "tool_call_id", "source", "content", "tool_calls", "chars", "estimated_tokens", "flags"}`.

`content` keeps provider-native blocks after redaction. `estimated_tokens` is `max(1, chars // 3)` when the provider-native message cannot be validated as an AstrBot `Message`; otherwise use `EstimateTokenCounter`. Mark this in the view as an estimate via the UI label, not a separate field.

Tool rows merge `envelope["tool_meta"]` by name: `origin`, `active`, `denied`, `background`, `schema_hash`, `schema_bytes`.

At capture time, derive `tool_meta` from the provider's `ToolSet` when available: `origin` from `handler_module_path`/MCP class, `active`, `denied` from `req.denied_tools`, `background`, and a sha1 of the canonical schema. If a hook only has a schema dict, `origin="unknown"` and `active=True`.

`system.source_field` is one of `messages[0]`, `instructions`, `system`, `config.system_instruction`; `system.parts` comes from `envelope["provenance"]`.

- [ ] **Step 4: Implement structural truncation**

Before the final `bounded_context_value` pass, apply the spec's order:

1. Keep system up to 64 KB and each tool schema up to 16 KB.
2. Drop messages from index 0 until the serialized size fits 512 KB.
3. Trim any single message over 32 KB to head 8 KB + tail 8 KB with a `…[truncated N bytes]…` marker.
4. Trim provider extras to 16 KB.
5. Set `counts.omitted_messages`, `truncated`, and `original_bytes`.

Do not drop system/tools to satisfy the budget; if they alone exceed it, truncate them with the same marker.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_request_snapshot_adapters.py tests/unit/test_request_snapshot.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add astrbot/core/provider/request_snapshot.py tests/unit/test_request_snapshot_adapters.py
git commit -m "feat(provider): normalize snapshot payloads for chat providers"
```

---

### Task 3: Provenance and cache diagnostics

**Files:**
- Modify: `astrbot/core/provider/request_snapshot.py`
- Test: `tests/unit/test_request_snapshot_diagnostics.py`

**Interfaces:**
- Consumes: `capture`/`get` from Tasks 1-2.
- Produces:
  - `record_provenance(session_id: str, label: str, text: str) -> None`.
  - `attach_response(invocation_id: str, response: LLMResponse, *, chunks: int = 0) -> None`.
  - `attach_injections(invocation_id: str, items: list[dict]) -> None`.
  - `attach_diagnostics(invocation_id: str, diagnostics: dict) -> None`.
  - `mark_error(invocation_id: str, exc: BaseException) -> None`.
  - `capture` reads the current provenance buffer, computes `cache` against the recorder's own last per-session fingerprint, and stores `diagnostics`.

- [ ] **Step 1: Write the failing diagnostics tests**

Create `tests/unit/test_request_snapshot_diagnostics.py`:

```python
def test_unique_provenance_gets_exact_span(monkeypatch):
    record_provenance("s1", "persona", "PERSONA")
    snapshot = capture_with_system("PERSONA\nENV", session_id="s1")
    assert snapshot["provenance"][0]["span"] == [0, 7]
    assert snapshot["provenance"][0]["approx"] is False

def test_duplicate_or_missing_provenance_is_approximate():
    ...
    assert part["span"] is None and part["approx"] is True

def test_cache_reports_first_message_divergence(monkeypatch):
    ...
    assert diagnostics["cache"]["divergence_index"] == 1
    assert diagnostics["cache"]["system_changed"] is False

def test_cache_is_null_without_a_previous_request():
    ...
    assert diagnostics["cache"]["system_changed"] is None

def test_attach_diagnostics_merges_compression_and_usage(monkeypatch):
    ...
    assert snapshot["diagnostics"]["compression"]["strategy"] == "llm_summary"
    assert snapshot["response"]["usage"]["input_cached"] == 5300

def test_list_is_lightweight_and_detail_is_full(monkeypatch):
    ...
    assert "injections" not in request_snapshots.list_for_session("s1")["items"][0]
    assert request_snapshots.get(snapshot_id)["envelope"]["injections"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_request_snapshot_diagnostics.py -v`
Expected: FAIL with missing `provenance_scope` / `attach_diagnostics`.

- [ ] **Step 3: Implement provenance**

Use a recorder-owned per-session `OrderedDict[str, list[tuple[str, str, float]]]`, bounded to 32 entries per session and TTL 10 minutes. `record_provenance` ignores empty text and stores the entry under `session_id`. At capture, read that session's list, search the final system text:

- Unique occurrence -> `span=[start, end]`, `approx=False`.
- Multiple occurrences or missing -> `span=None`, `approx=True`.

Do not clear on a scope exit; keep entries until TTL so a later capture in the same turn can reuse them. Do not mix buffers across sessions.

- [ ] **Step 4: Implement cache diagnostics**

On capture, before sanitizing/truncating, compute:

- `message_hashes = fingerprint_messages(messages)`
- `tool_hashes = fingerprint_tools(tool_set_or_schema)`
- `system_text = adapter.system_text(raw)`

Compare with the recorder's own previous per-session record. Set `system_changed`, `tools_changed`, `divergence_index` (first differing message index), and later `hit_ratio` from `input_cached / (input_other + input_cached)`. Update the recorder's last record even when the new snapshot is later evicted.

Do not read `prefix_diagnostics.get_session_request`; the runner overwrites it before the provider call.

- [ ] **Step 5: Implement `attach_diagnostics`**

Merge `compression`, `budget`, `retries`, and `stop_reason` into every attempt of the invocation. Do not overwrite `response`; response attachment is Task 5.

Add `attach_injections(invocation_id, items)` to store the runner's `read_injections(event)` items on every attempt of the invocation; keep the existing preview/full caps.

Also implement `attach_response` and `mark_error` here:

- `attach_response` fills `response_id`, `finish_reason`, `returned_model`, `created_at`, `usage`, `usage_matches_request`, `chunks`, and `status="ok"`; parse `raw_completion` defensively (OpenAI `choices[0].finish_reason`/`model`, Anthropic `stop_reason`/`model`, Gemini `candidates[0].finish_reason`) and leave unknown fields `None`.
- `mark_error` sets `status="error"` and `response.error={type, message[:1024]}`, with `response=None`; it must never raise.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_request_snapshot_diagnostics.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add astrbot/core/provider/request_snapshot.py tests/unit/test_request_snapshot_diagnostics.py
git commit -m "feat(provider): attribute snapshot context and cache diagnostics"
```

---

### Task 4: Capture hooks in the four provider sources

**Files:**
- Modify: `astrbot/core/provider/sources/openai_source.py`
- Modify: `astrbot/core/provider/sources/openai_responses_source.py`
- Modify: `astrbot/core/provider/sources/anthropic_source.py`
- Modify: `astrbot/core/provider/sources/gemini_source.py`
- Test: `tests/unit/test_provider_snapshot_hooks.py`

**Interfaces:**
- Consumes: `request_snapshots.capture(provider, raw, adapter=...)`.
- Produces:
  - `ProviderOpenAI.SNAPSHOT_ADAPTER = "openai_chat"`
  - `ProviderOpenAIResponses.SNAPSHOT_ADAPTER = "openai_responses"`
  - `ProviderAnthropic.SNAPSHOT_ADAPTER = "anthropic"`
  - `ProviderGoogleGenAI.SNAPSHOT_ADAPTER = "gemini"`

- [ ] **Step 1: Write the failing hook tests**

Create `tests/unit/test_provider_snapshot_hooks.py`. Monkeypatch `astrbot.core.provider.sources.<module>.request_snapshots.capture` (or patch the singleton method) and invoke each `_query`/`_query_stream` with the existing fake client fixtures:

```python
def test_openai_chat_capture_includes_payloads_and_extra_body(monkeypatch):
    captured = {}
    monkeypatch.setattr(request_snapshots, "capture",
                        lambda provider, raw, **kw: captured.update(raw))
    ...
    assert captured["messages"][0]["role"] == "system"
    assert captured["tools"][0]["function"]["name"] == "read_file"
    assert captured["chat_template_kwargs"] == {"reasoning_effort": 100}

def test_openai_responses_capture_uses_input_and_instructions(monkeypatch):
    ...

def test_anthropic_capture_uses_system_messages_and_tools(monkeypatch):
    ...

def test_gemini_capture_uses_contents_and_config(monkeypatch):
    ...

def test_capture_failure_does_not_break_the_request(monkeypatch):
    monkeypatch.setattr(request_snapshots, "capture",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    ...  # the provider call still returns its normal response
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_provider_snapshot_hooks.py -v`
Expected: FAIL because `capture` is never called.

- [ ] **Step 3: Add the hooks**

- Set `SNAPSHOT_ADAPTER` on each base class: `"openai_chat"`, `"openai_responses"`, `"anthropic"`, `"gemini"`.
- OpenAI Chat `_query` / `_query_stream`: after `apply_model_output_limit(...)`, before `retry_provider_request(...)`, capture `{**payloads, **extra_body}`. `extra_body` overrides same-named keys.
- OpenAI Responses `_query` / `_query_stream`: after `apply_model_output_limit(...)`, before `retry_provider_request(...)`, capture `{**payloads, **extra_body}`.
- Anthropic `_query` / `_query_stream`: after the `max_tokens` alias cleanup, before `retry_provider_request` / `retry_provider_request_context`, capture `{**payloads, **extra_body}`.
- Gemini `_query` / `_query_stream`: after `config = await self._prepare_query_config(...)`, before the SDK call, capture `{"model": model, "contents": conversation, "config": config}`.

Each hook calls `request_snapshots.capture(self, effective_body, adapter=self.SNAPSHOT_ADAPTER, tool_set=tools)` and is wrapped in `try/except Exception: logger.debug(...)`; never log payload contents and never re-raise. Assert the adapter name in the tests.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_provider_snapshot_hooks.py -v`
Expected: PASS.

- [ ] **Step 5: Run existing provider suites**

Run: `uv run pytest tests/test_openai_source.py tests/test_anthropic_source.py tests/test_gemini_source.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add astrbot/core/provider/sources/openai_source.py \
  astrbot/core/provider/sources/openai_responses_source.py \
  astrbot/core/provider/sources/anthropic_source.py \
  astrbot/core/provider/sources/gemini_source.py \
  tests/unit/test_provider_snapshot_hooks.py
git commit -m "feat(provider): capture final llm request payloads"
```

---

### Task 5: Main runner scope, response, metadata event, subagent skip

**Files:**
- Modify: `astrbot/core/agent/runners/tool_loop_agent_runner.py`
- Test: `tests/unit/test_agent_runner_snapshot_scope.py`

**Interfaces:**
- Consumes: `request_snapshot_scope`, `chatui_session_id_from_umo`, `request_snapshots.attach_response/attach_diagnostics/attach_injections/mark_error/metadata_for_invocation`, `read_injections`.
- Produces:
  - `self._snapshot_turn_id` (uuid hex, set in `reset`) and `self._snapshot_fallback_index` (0 in `reset`, set to the fallback candidate index).
  - `self._snapshot_invocation_id` (set in `_iter_llm_responses`, consumed and cleared in `step()`).
  - `self._snapshot_stream_chunks` (reset in `_iter_llm_responses`, incremented per streamed chunk).
  - Scope around the main provider call in `_iter_llm_responses`.
  - `attach_response(invocation_id, response, chunks=...)` plus `attach_diagnostics(invocation_id, diagnostics)` after the final response.
  - `AgentResponse(type="llm_request_snapshots", ...)` with `{"items": envelopes}` after each invocation.

- [ ] **Step 1: Write the failing runner tests**

Create `tests/unit/test_agent_runner_snapshot_scope.py` using a fake provider that returns one tool call then a final text response:

```python
async def test_two_model_calls_emit_two_snapshot_envelopes(fake_runner):
    events = [event async for event in fake_runner.step_until_done(max_step=4)]
    snapshots = [e for e in events if e.type == "llm_request_snapshots"]
    assert len(snapshots) == 2
    items = json.loads(snapshots[0].data.chain.chain[0].data)["items"]
    assert items[0]["kind"] == "chat"
    assert items[0]["turn_id"] == fake_runner._snapshot_turn_id
    assert "raw" not in items[0]

async def test_retry_attempts_share_invocation(fake_runner):
    ...
    assert [item["request"]["attempt"] for item in items] == [1, 2]

async def test_subagent_scope_skips_capture_and_event(fake_runner):
    fake_runner.run_context.context.extra["is_subagent"] = True
    ...
    assert not [e for e in events if e.type == "llm_request_snapshots"]

async def test_provider_exception_marks_error(fake_provider_that_raises):
    ...
    assert snapshot["status"] == "error"

async def test_disabled_mode_emits_no_event(fake_runner, monkeypatch):
    monkeypatch.setattr(request_snapshot, "developer_mode_enabled", lambda: False)
    events = [event async for event in fake_runner.step_until_done(max_step=4)]
    assert not [e for e in events if e.type == "llm_request_snapshots"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_agent_runner_snapshot_scope.py -v`
Expected: FAIL with no `llm_request_snapshots` events.

- [ ] **Step 3: Set the turn id and fallback index**

In `reset()`, after `self.req = request`, add:

```python
self._snapshot_turn_id = uuid.uuid4().hex
self._snapshot_fallback_index = 0
self._snapshot_invocation_id = None
```

In `_iter_llm_responses_with_fallback`, set `self._snapshot_fallback_index = idx` before calling `_iter_llm_responses`.

- [ ] **Step 4: Wrap the main call in the scope**

In `_iter_llm_responses`, after `invocation_id` is known and before the stream/non-stream branch, enter:

```python
with request_snapshot_scope(
    session_id=chatui_session_id_from_umo(self.req.session_id),
    umo=self.req.session_id,
    turn_id=self._snapshot_turn_id,
    invocation_id=invocation_id,
    kind="chat",
    step=self._step_budget_used,
    stage="main",
    fallback_index=self._snapshot_fallback_index,
    is_subagent=bool((getattr(self.run_context.context, "extra", None) or {}).get("is_subagent")),
):
    ...  # existing stream / text_chat call
```

Keep the existing behavior inside the block unchanged.

Inside the same scope, after `invocation_id` is known, set `self._snapshot_invocation_id = invocation_id` and `self._snapshot_stream_chunks = 0`; increment the counter for each streamed chunk.

- [ ] **Step 5: Attach response and emit the envelope event**

After the final response is known in `step()` (before `agent_stats` is yielded), use `invocation_id = self._snapshot_invocation_id`; if it is `None`, skip attachment and event emission:

- `request_snapshots.attach_response(invocation_id, llm_response, chunks=self._snapshot_stream_chunks)`
- `request_snapshots.attach_diagnostics(invocation_id, {...})`
- `request_snapshots.attach_injections(invocation_id, read_injections(event))`
- `request_snapshots.attach_tool_meta(invocation_id, tool_set=req.func_tool, denied_tools=req.denied_tools)`
- `items = request_snapshots.metadata_for_invocation(invocation_id)`
- If `items`, yield `AgentResponse(type="llm_request_snapshots", ...)` with `{"items": items}`.

Diagnostics payload: `compression=self.request_context_manager.last_compression`, `budget={"step_used": self._step_budget_used, "step_max": self._step_budget_max, "context_tokens": ..., "context_limit": ..., "output_budget": ...}`, `stop_reason=None` or the runner's stop state.

For the error path (`role == "err"` or an exception), call `request_snapshots.mark_error(invocation_id, exc)` before yielding the error response.

Clear `self._snapshot_invocation_id = None` after the event is yielded, so a later step cannot attach its response to the previous invocation.

- [ ] **Step 6: Skip subagents**

The recorder already drops `is_subagent=True`; also guard the event emission so a subagent run yields no `llm_request_snapshots` event.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_agent_runner_snapshot_scope.py -v`
Expected: PASS.

- [ ] **Step 8: Run the existing runner suite**

Run: `uv run pytest tests/unit/test_agent_runner_config.py tests/unit/test_agent_runner_injection_trace.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add astrbot/core/agent/runners/tool_loop_agent_runner.py \
  tests/unit/test_agent_runner_snapshot_scope.py
git commit -m "feat(agent): report llm request snapshots for main calls"
```

---

### Task 6: Tool executions, auxiliary kinds, provenance registration

**Files:**
- Modify: `astrbot/core/agent/runners/tool_loop_agent_runner.py`
- Modify: `astrbot/core/agent/context/compressor.py`
- Modify: `astrbot/core/astr_main_agent.py`
- Modify: `astrbot/builtin_stars/astrbot/file_access_guidance.py`
- Modify: `astrbot/builtin_stars/astrbot/group_chat_context.py`
- Test: `tests/unit/test_agent_snapshot_tools_provenance.py`

**Interfaces:**
- Consumes: `record_tool_execution`, `request_snapshot_scope`, `record_provenance`.
- Produces:
  - `request_snapshots.record_tool_execution(invocation_id, *, call_id, name, status, started_at, duration_ms, result_bytes, result_preview="", result_ref=None, permission="allow", executor="main") -> None`.
  - Tool executions on the invoking attempt; `kind="compression"` / `"title"`; provenance entries on the captured system prompt.

- [ ] **Step 1: Write the failing tool/provenance tests**

Create `tests/unit/test_agent_snapshot_tools_provenance.py`:

```python
async def test_tool_execution_is_attached_to_the_request_that_called_it(fake_runner):
    ...
    assert snapshot["tool_executions"] == [{
        "call_id": "call_1", "name": "read_file", "status": "ok",
        "duration_ms": ..., "result_bytes": ..., "permission": "allow",
        "executor": "main", ...
    }]

async def test_denied_tool_is_recorded_as_denied(fake_runner):
    ...
    assert snapshot["tool_executions"][0]["status"] == "denied"

async def test_compression_and_title_kinds(monkeypatch):
    ...
    assert compression_snapshot["kind"] == "compression"
    assert title_snapshot["kind"] == "title"
    assert title_snapshot["turn_id"] is None

def test_provenance_marks_exact_and_approximate_parts():
    ...
    assert parts[0]["approx"] is False
    assert parts[1]["approx"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_agent_snapshot_tools_provenance.py -v`
Expected: FAIL.

- [ ] **Step 3: Record tool executions**

In the tool execution loop, capture `started_at` before execution and call `request_snapshots.record_tool_execution(self._snapshot_invocation_id, ...)` in all outcome branches: success, error, denied, interrupted; skip when `self._snapshot_invocation_id` is `None`. Use the existing `func_tool_id`, `func_tool_name`, result text length, and `result_ref` when present. `result_preview` is the first 2 KB of the result.

- [ ] **Step 4: Set auxiliary scopes**

- In `LLMSummaryCompressor`'s provider call, enter `request_snapshot_scope(kind="compression")` while preserving the current session/turn/invocation (use the existing scope and only override `kind`).
- In `astr_main_agent`'s title generation, enter `request_snapshot_scope(kind="title", turn_id=None, session_id=..., umo=...)` around the provider call, using the ChatUI session id and `webchat:` UMO.

- [ ] **Step 5: Register provenance**

In `astr_main_agent.py` around the system-prompt assembly, and in `file_access_guidance.py`, `group_chat_context.py` where text is appended, call `record_provenance(session_id, label, exact_text)` for:

`persona`, `environment`, `file_access`, `chatui_rules`, `tool_guidance`, `skills`, `goal`, `memory`, `knowledge`, `team`.

If a block is transformed after registration, leave it approximate; do not compute offsets by hand.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_agent_snapshot_tools_provenance.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add astrbot/core/agent/runners/tool_loop_agent_runner.py \
  astrbot/core/agent/context/compressor.py \
  astrbot/core/astr_main_agent.py \
  astrbot/builtin_stars/astrbot/file_access_guidance.py \
  astrbot/builtin_stars/astrbot/group_chat_context.py \
  tests/unit/test_agent_snapshot_tools_provenance.py
git commit -m "feat(agent): record tool executions and snapshot provenance"
```

---

### Task 7: ChatService run state, message binding, developer-mode toggle

**Files:**
- Modify: `astrbot/dashboard/services/chat_service.py`
- Modify: `astrbot/dashboard/services/chatui_settings.py`
- Modify: `astrbot/core/platform/sources/webchat/webchat_event.py`
- Test: `tests/unit/test_chat_service_snapshots.py`
- Test: extend `tests/unit/test_chatui_settings_service.py`

**Interfaces:**
- Consumes: `request_snapshots` from Tasks 1-6.
- Produces:
  - `ChatRunState.dev_snapshots: dict[str, dict]` and `dev_turn_id: str | None`.
  - `ChatService.get_llm_snapshots(username, session_id, *, message_id=None, turn_id=None, limit=50) -> dict`.
  - `ChatService.get_llm_snapshot(username, snapshot_id) -> dict`.
  - `ChatService.get_llm_snapshot_limits() -> dict`.
  - `ChatService.get_chatui_developer_mode() -> bool`.
  - `ChatService.set_chatui_developer_mode(enabled: bool) -> dict`.

- [ ] **Step 1: Write the failing service tests**

Create `tests/unit/test_chat_service_snapshots.py` using a `ChatService.__new__(ChatService)` with a fake config manager (same pattern as `test_chatui_settings_service.py`) and a real recorder:

```python
def test_chain_event_merges_by_snapshot_id_and_is_not_in_history(monkeypatch):
    ...
    service._merge_snapshot_event(run, [item_a, item_b])
    assert len(run.dev_snapshots) == 2
    content = build_bot_history_content(run.history_parts, ...)
    assert "llm_request_snapshots" not in content

def test_message_binding_resolves_turn_and_expiry(monkeypatch):
    ...
    assert service.get_llm_snapshots("alice", "s1", message_id=7)["items"]
    assert service.get_llm_snapshots("alice", "s1", message_id=999)["expired"] is True

def test_two_turns_in_one_session_stay_separate(monkeypatch):
    ...
    assert {item["turn_id"] for item in first["items"]} == {"t1"}
    assert {item["turn_id"] for item in second["items"]} == {"t2"}

def test_toggle_preserves_sibling_chatui_keys(tmp_path):
    ...
    assert service.set_chatui_developer_mode(True)["enabled"] is True
    assert _stored()["thinking_effort"] == ...
    assert _stored()["developer_mode"] is True

def test_orphan_telemetry_skip_includes_snapshots():
    source = inspect.getsource(webchat_event._persist_bot_reply_if_orphan)
    assert "llm_request_snapshots" in source
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_chat_service_snapshots.py -v`
Expected: FAIL.

- [ ] **Step 3: Add run state and chain handling**

- Add `dev_snapshots: dict[str, dict] = field(default_factory=dict)` and `dev_turn_id: str | None = None` to `ChatRunState`.
- Add `ChatService._merge_snapshot_event(run: ChatRunState, items: list[dict]) -> None` that merges by `snapshot_id`, sets `run.dev_turn_id` from the first non-null `turn_id`, and publishes `{"type": "llm_request_snapshots", "data": {"items": items}}`.
- In `_consume_chat_run`, add a branch before generic plain handling:

```python
if chain_type == "llm_request_snapshots":
    items = json.loads(result_text).get("items", [])
    self._merge_snapshot_event(run, items if isinstance(items, list) else [])
    continue
```

Handle malformed JSON as an empty list, never raise.

- [ ] **Step 4: Bind the bot message**

After `run.history_record` is created or updated, call:

```python
if run.dev_turn_id and run.history_record:
    request_snapshots.bind_message(run.history_record.id, run.dev_turn_id, run.session_id)
```

Also call it when the snapshot event arrives and `run.history_record` already exists.

- [ ] **Step 5: Add service readers and toggle**

- `get_llm_snapshots`: load `PlatformSession` by `session_id`; if missing or `creator != username`, raise `ChatServiceError("Permission denied")`. Delegate to `request_snapshots.list_for_session`.
- `get_llm_snapshot`: call `request_snapshots.get(snapshot_id)`; if missing, raise `ChatServiceError("Snapshot not found")`; verify the envelope's `session_id` belongs to `username`; return `{envelope, raw, view}`.
- `get_chatui_developer_mode`: read `self._default_config().get("chatui", {}).get("developer_mode", False)` and coerce to bool.
- `set_chatui_developer_mode`: reject non-bool with `ChatServiceError("developer_mode must be a boolean")`; call `self._write_chatui(developer_mode=enabled)`; return `{"enabled": enabled}`.
- `get_llm_snapshot_limits`: delegate to `request_snapshots.limits()`.
- In `chatui_settings.py`, add `normalize_developer_mode(raw: object) -> bool` returning `raw if isinstance(raw, bool) else False`; use it in `get_chatui_developer_mode`.

- [ ] **Step 6: Skip the new telemetry in orphan replies**

Add `"llm_request_snapshots"` to the telemetry tuple in `_persist_bot_reply_if_orphan` in `webchat_event.py`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_chat_service_snapshots.py tests/unit/test_chatui_settings_service.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add astrbot/dashboard/services/chat_service.py \
  astrbot/dashboard/services/chatui_settings.py \
  astrbot/core/platform/sources/webchat/webchat_event.py \
  tests/unit/test_chat_service_snapshots.py \
  tests/unit/test_chatui_settings_service.py
git commit -m "feat(dashboard): track llm request snapshots per chat run"
```

---

### Task 8: API routes, permission, OpenAPI, generated client

**Files:**
- Modify: `astrbot/dashboard/api/chat.py`
- Modify: `astrbot/dashboard/schemas.py`
- Modify: `openspec/openapi-v1.yaml`
- Create: `tests/test_llm_snapshot_routes.py`
- Generated: `dashboard/src/api/generated/openapi-v1/**`, `dashboard/src/api/v1.ts`

**Interfaces:**
- Consumes: ChatService methods from Task 7.
- Produces:
  - `GET /api/v1/chat/sessions/{session_id}/llm-snapshots?message_id=&turn_id=&limit=`
  - `GET /api/v1/chat/llm-snapshots/{snapshot_id}`
  - `GET /api/v1/chat/developer-mode`
  - `POST /api/v1/chat/developer-mode`
  - `DeveloperModeRequest(enabled: bool)` with `extra="forbid"` and strict bool.

- [ ] **Step 1: Write the failing route tests**

Create `tests/test_llm_snapshot_routes.py` with a FastAPI app, `chat_api.router`, and dependency overrides:

```python
def test_list_rejects_non_admin(client):
    res = client.get("/chat/sessions/s1/llm-snapshots",
                     headers={"X-Test-Role": "user"})
    assert res.status_code == 403

def test_list_rejects_foreign_session(client):
    res = client.get("/chat/sessions/other/llm-snapshots",
                     headers={"X-Test-Role": "admin"})
    assert res.status_code == 403

def test_owner_admin_gets_items(client):
    res = client.get("/chat/sessions/s1/llm-snapshots",
                     headers={"X-Test-Role": "admin"})
    assert res.status_code == 200
    assert res.json()["data"]["items"]

def test_message_query_reports_expired(client):
    res = client.get("/chat/sessions/s1/llm-snapshots",
                     params={"message_id": 999}, headers={"X-Test-Role": "admin"})
    assert res.json()["data"]["expired"] is True

def test_developer_mode_get_is_chat_scope_and_reports_can_edit(client):
    res = client.get("/chat/developer-mode")
    assert res.status_code == 200
    assert res.json()["data"]["can_edit"] is False

def test_developer_mode_post_rejects_non_bool(client):
    res = client.post("/chat/developer-mode", json={"enabled": "yes"})
    assert res.status_code == 422
```

Override `require_chat_scope` and `require_config_scope` to return an `AuthContext` with `role`/`scopes` from the test header; override `get_service` with a fake service.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_llm_snapshot_routes.py -v`
Expected: FAIL with 404.

- [ ] **Step 3: Add the routes**

In `chat.py`:

- `GET /chat/sessions/{session_id}/llm-snapshots` uses `require_chat_scope`, checks admin (`auth.role == "admin"` or `"*" in auth.scopes`), then `service.get_llm_snapshots(auth.username, session_id, message_id=..., turn_id=..., limit=...)`. Return `ok(...)`; map `ChatServiceError("Permission denied")` to 403.
- `GET /chat/llm-snapshots/{snapshot_id}` does the same admin check and calls `service.get_llm_snapshot`; 404 for missing.
- `GET /chat/developer-mode` uses `require_chat_scope`, returns `{enabled, can_edit, limits}`; `can_edit` is true only for admin; `limits` comes from `service.get_llm_snapshot_limits()`.
- `POST /chat/developer-mode` uses `require_config_scope`; validates `DeveloperModeRequest`.

Do not add a global `/llm-snapshots` list.

- [ ] **Step 4: Add the schema**

In `schemas.py`:

```python
class DeveloperModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
```

- [ ] **Step 5: Add OpenAPI paths and schemas**

Append the four paths to `openspec/openapi-v1.yaml` near the existing `/api/v1/chat/sessions/{session_id}/...` blocks. Use `x-astrbot-scope: chat` for GETs and `config` for POST; define `LlmSnapshotEnvelope`, `LlmSnapshotList`, `LlmSnapshotDetail`, `DeveloperModeState`, and `DeveloperModeRequest` components. Match the existing envelope/`Ok` response style.

- [ ] **Step 6: Regenerate the frontend client**

Run:

```powershell
cd dashboard
pnpm generate:api
pnpm generate:docs:openapi
```

Expected: generated client includes the four operations; `docs/public/openapi.json` and scope docs update.

- [ ] **Step 7: Add the `v1.ts` facade**

Add `llmSnapshotsApi` with `list(sessionId, params)`, `get(snapshotId)`, `getDeveloperMode()`, `setDeveloperMode(enabled)` using the generated client; export the response types.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_llm_snapshot_routes.py tests/test_fastapi_v1_dashboard.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add astrbot/dashboard/api/chat.py astrbot/dashboard/schemas.py \
  openspec/openapi-v1.yaml dashboard/src/api/generated \
  dashboard/src/api/v1.ts docs/public/openapi.json docs/zh/dev docs/en/dev \
  tests/test_llm_snapshot_routes.py
git commit -m "feat(dashboard): expose llm request snapshot api"
```

---

### Task 9: Frontend data layer and live SSE fold

**Files:**
- Create: `dashboard/src/composables/llmRequestSnapshots.ts`
- Create: `dashboard/src/composables/useLlmSnapshots.ts`
- Modify: `dashboard/src/composables/useMessages.ts`
- Test: `dashboard/src/composables/llmRequestSnapshots.spec.ts`

**Interfaces:**
- Produces:
  - `LlmRequestSnapshotEnvelope`, `LlmSnapshotDetail`, `LlmSnapshotLimits`.
  - `LlmRequestSnapshotEnvelope.injections?: LlmRequestInjectionItem[]` (list items omit it; detail envelope has it; reuse the existing item type).
  - `parseLlmRequestSnapshots(payload: unknown): LlmRequestSnapshotEnvelope[]`.
  - `mergeLlmRequestSnapshots(current, incoming): LlmRequestSnapshotEnvelope[]` (by `snapshot_id`).
  - `useLlmSnapshots()` with `items`, `expired`, `limits`, `loadList`, `loadDetail`, `refresh`, `error`.
  - `loadList(params?: { messageId?: number; turnId?: string })`, `loadDetail(snapshotId: string)`, `refresh()`.
  - `ChatContent.llmRequestSnapshots?: LlmRequestSnapshotEnvelope[]`.

- [ ] **Step 1: Write the failing parser/merge tests**

```ts
it("merges envelopes by snapshot_id without reordering", () => {
  const merged = mergeLlmRequestSnapshots(
    [{ snapshot_id: "a", bytes: 1 } as any],
    [{ snapshot_id: "a", bytes: 2 } as any, { snapshot_id: "b", bytes: 3 } as any],
  );
  expect(merged.map((x) => x.snapshot_id)).toEqual(["a", "b"]);
  expect(merged[0].bytes).toBe(2);
});

it("ignores malformed payloads", () => {
  expect(parseLlmRequestSnapshots({ nope: true })).toEqual([]);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dashboard; pnpm test -- src/composables/llmRequestSnapshots.spec.ts`
Expected: FAIL with missing module.

- [ ] **Step 3: Implement types, parser, merge, composable**

- Parse `{items: [...]}` or `[...]`; keep only objects with a string `snapshot_id`.
- Merge by id; incoming wins; preserve first-seen order.
- `useLlmSnapshots` calls `llmSnapshotsApi`; caches details in a `Map<string, LlmSnapshotDetail>`; exposes `loadDetail(id)` and a manual `refresh()`.
- `GET /chat/developer-mode` failure sets `canEdit=false`, never throws into the component.

- [ ] **Step 4: Fold the live SSE event**

In `useMessages.ts`, add a branch before `agent_stats`:

```ts
if (msgType === "llm_request_snapshots" || chainType === "llm_request_snapshots") {
  markMessageStarted(botRecord);
  messageContent(botRecord).llmRequestSnapshots = mergeLlmRequestSnapshots(
    messageContent(botRecord).llmRequestSnapshots || [],
    parseLlmRequestSnapshots(data),
  );
  return;
}
```

Extend `ChatContent` with the optional field. Do not persist it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd dashboard; pnpm test -- src/composables/llmRequestSnapshots.spec.ts src/composables/useMessages.askUserChoiceFilter.test.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/composables/llmRequestSnapshots.ts \
  dashboard/src/composables/useLlmSnapshots.ts \
  dashboard/src/composables/useMessages.ts \
  dashboard/src/composables/llmRequestSnapshots.spec.ts
git commit -m "feat(chatui): add llm snapshot client state"
```

---

### Task 10: Inspector shell and detail sections

**Files:**
- Create: `dashboard/src/components/chat/ChatDevModeInspector.vue`
- Create: `dashboard/src/components/chat/LlmSnapshotDetail.vue`
- Test: `dashboard/src/components/chat/ChatDevModeInspector.spec.ts`
- Test: `dashboard/src/components/chat/LlmSnapshotDetail.spec.ts`

**Interfaces:**
- Consumes: `useLlmSnapshots`, `LlmRequestInjectionsPanel.vue`.
- Produces:
  - `ChatDevModeInspector` props `{ modelValue: boolean; sessionId: string; enabled: boolean; canEdit: boolean; initialTurnId?: string }`, emits `update:modelValue` and `toggle`.
  - `LlmSnapshotDetail` props `{ snapshot: LlmSnapshotDetailData }`, emits `refresh`.

- [ ] **Step 1: Write the failing shell tests**

```ts
it("renders disabled, empty, and expired states distinctly", async () => {
  ...
  expect(wrapper.text()).toContain("开发者模式未开启");
  ...
  expect(wrapper.text()).toContain("本回合还没有请求快照");
  ...
  expect(wrapper.text()).toContain("快照已过期");
});

it("switches the detail when a rail item is clicked", async () => {
  ...
  await wrapper.findAll(".rail-item")[1].trigger("click");
  expect(wrapper.find(".detail-title").text()).toContain("step 2");
});

it("collapses diagnostics and raw json by default", async () => {
  ...
  expect(wrapper.find(".sec-diagnostics").classes()).toContain("collapsed");
  expect(wrapper.find(".sec-raw").classes()).toContain("collapsed");
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dashboard; pnpm test -- src/components/chat/ChatDevModeInspector.spec.ts`
Expected: FAIL.

- [ ] **Step 3: Implement the shell**

- Resizable right panel; width `min(820px, 66vw)`; below 1000px full-screen.
- Header: title, subtitle, refresh, close.
- Header actions include the developer-mode switch when `canEdit`; toggling emits `toggle` to the parent (Chat.vue owns the settings write and store sync).
- Rail: "本回合" and "本会话 · 辅助" groups; two-line rows with badges; active state.
- Fetch list on open/session change; fetch detail on selection; manual refresh.
- Reuse `ReasoningSidebar.vue`'s resizer behavior; do not introduce a new sidebar look.

- [ ] **Step 4: Implement the detail sections**

Render in this order: diff (step >= 2) → overview → response → system → tools → messages → injections → diagnostics (collapsed) → raw JSON (collapsed).

Use the approved preview's structure: key-value grids, origin/denied tags, role chips, source/token rows, tool call status rows, provenance chips with `△` for approximate, `LlmRequestInjectionsPanel` fed by `snapshot.envelope.injections`, monospace raw block.

Long text blocks scroll internally; every section head is a `button` with `aria-expanded`.

- [ ] **Step 5: Write and pass detail tests**

Cover: usage/TTFT fields; tool call status; system provenance chip; `truncated` badge; copy JSON button feedback; raw JSON toggle; injection panel presence.

Run: `cd dashboard; pnpm test -- src/components/chat/ChatDevModeInspector.spec.ts src/components/chat/LlmSnapshotDetail.spec.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/components/chat/ChatDevModeInspector.vue \
  dashboard/src/components/chat/LlmSnapshotDetail.vue \
  dashboard/src/components/chat/ChatDevModeInspector.spec.ts \
  dashboard/src/components/chat/LlmSnapshotDetail.spec.ts
git commit -m "feat(chatui): add developer mode snapshot inspector"
```

---

### Task 11: Entry points, header bridge, i18n

**Files:**
- Modify: `dashboard/src/stores/chatHeader.ts`
- Modify: `dashboard/src/layouts/full/vertical-header/VerticalHeader.vue`
- Modify: `dashboard/src/components/chat/Chat.vue`
- Modify: `dashboard/src/components/chat/ChatMessageList.vue`
- Modify: `dashboard/src/i18n/locales/zh-CN/features/chat.json`
- Modify: `dashboard/src/i18n/locales/en-US/features/chat.json`
- Modify: `dashboard/src/i18n/locales/ru-RU/features/chat.json`
- Modify: `dashboard/src/i18n/locales/ja-JP/features/chat.json`
- Test: `dashboard/src/components/chat/ChatMessageList.devMarker.spec.ts`
- Test: `dashboard/src/components/chat/Chat.devModeEntry.spec.ts`

**Interfaces:**
- Consumes: `ChatDevModeInspector`, `useLlmSnapshots`.
- Produces:
  - `chatHeader` state `devModeOpen`, `devModeEnabled`, `devModeCanEdit`; actions `SET_DEV_MODE_STATE`, `SET_DEV_MODE_OPEN`.
  - `ChatMessageList` emit `openDevSnapshots: [message: ChatRecord]`.
  - `Chat.vue` opens the inspector from the chip/marker.

- [ ] **Step 1: Write the failing entry tests**

```ts
it("renders a DEV marker only when live snapshots exist", async () => {
  ...
  expect(wrapper.find(".dev-mode-marker").exists()).toBe(false);
  ...
  expect(wrapper.find(".dev-mode-marker").text()).toContain("DEV · 2");
});

it("emits openDevSnapshots with the message", async () => {
  ...
  await wrapper.find(".dev-mode-marker").trigger("click");
  expect(wrapper.emitted("openDevSnapshots")?.[0]?.[0]).toBe(record);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dashboard; pnpm test -- src/components/chat/ChatMessageList.devMarker.spec.ts`
Expected: FAIL.

- [ ] **Step 3: Add the store bridge**

Extend `chatHeader` with the three fields/actions; clear them in `CLEAR_CONTEXT`. `Chat.vue` fetches `/chat/developer-mode` on mount/session change and calls `SET_DEV_MODE_STATE`; `VerticalHeader` renders the chip only when `devModeCanEdit`.

- [ ] **Step 4: Add the chip and inspector mount**

- `VerticalHeader`: a small `DEV` button in `header-actions`, outline when off, accent when on; click always calls `SET_DEV_MODE_OPEN(true)`; the inspector's header owns the switch and the disabled-state explanation.
- `Chat.vue`: mount `<ChatDevModeInspector v-model="chatHeader.devModeOpen" :session-id="currSessionId" :enabled="chatHeader.devModeEnabled" :can-edit="chatHeader.devModeCanEdit" ... />`; watch `devModeOpen` and refresh the list when opened. Handle `@toggle`: call `llmSnapshotsApi.setDeveloperMode`, update the store, and refresh the list on success; roll back on failure with a toast.
- `ChatMessageList`: add a `.dev-mode-marker` button next to the injection marker; emit `openDevSnapshots`.
- `Chat.vue` handles `@openDevSnapshots` by setting the initial turn id and opening the inspector.

- [ ] **Step 5: Add four-locale copy**

Add the `devMode` object from spec §10.4 to all four `features/chat.json` files. Keep keys identical; translate values. Add tests that each locale contains `devMode.title`, `devMode.state.disabled`, `devMode.state.empty`, `devMode.state.expired`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd dashboard; pnpm test -- src/components/chat/ChatMessageList.devMarker.spec.ts src/components/chat/Chat.devModeEntry.spec.ts`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/stores/chatHeader.ts \
  dashboard/src/layouts/full/vertical-header/VerticalHeader.vue \
  dashboard/src/components/chat/Chat.vue \
  dashboard/src/components/chat/ChatMessageList.vue \
  dashboard/src/i18n/locales/*/features/chat.json \
  dashboard/src/components/chat/ChatMessageList.devMarker.spec.ts \
  dashboard/src/components/chat/Chat.devModeEntry.spec.ts
git commit -m "feat(chatui): wire developer mode entry points and copy"
```

---

### Task 12: Full verification, build, browser check

**Files:**
- No new product files unless a fix is required.
- Docs/changelog: skip. This adds no sidebar/navigation entry, the WebUI doc has no ChatUI developer-settings section to extend, and the current release line has no changelog draft.

- [ ] **Step 1: Run the focused Python suites**

Run:

```powershell
uv run pytest tests/unit/test_request_snapshot.py tests/unit/test_request_snapshot_adapters.py tests/unit/test_request_snapshot_diagnostics.py tests/unit/test_provider_snapshot_hooks.py tests/unit/test_agent_runner_snapshot_scope.py tests/unit/test_agent_snapshot_tools_provenance.py tests/unit/test_chat_service_snapshots.py tests/test_llm_snapshot_routes.py -v
```

Expected: PASS.

- [ ] **Step 2: Run the broader regression suites**

Run:

```powershell
uv run pytest tests/unit/test_agent_runner_config.py tests/unit/test_agent_runner_injection_trace.py tests/unit/test_chatui_settings_service.py tests/test_fastapi_v1_dashboard.py -v
```

Expected: PASS.

- [ ] **Step 3: Run the frontend tests**

Run: `cd dashboard; pnpm test`
Expected: PASS, including the new specs.

- [ ] **Step 4: Format and lint**

Run: `ruff format .` then `ruff check .`
Expected: no changes needed / no errors.

- [ ] **Step 5: Build the dashboard**

Run: `cd dashboard; pnpm build`
Expected: exit 0; `dashboard/dist` updated. Copy `dashboard/dist` to the AstrBot runtime `data/dist` before the browser check.

- [ ] **Step 6: Browser verification**

Start/restart AstrBot (backend changes require a restart), open ChatUI as an admin, then:

1. Enable developer mode from the DEV chip.
2. Send a message that forces at least one tool call.
3. Open the inspector from the message marker and assert: system prompt, tool definitions, messages, response usage/TTFT, tool execution rows, and the collapsed diagnostics/raw sections are present.
4. Switch rail items; expand raw JSON; copy JSON.
5. Turn developer mode off; send another message; assert no new marker/snapshot.
6. Reload the page; assert the header list is either empty (restart/expired) or shows TTL-valid entries, and no message marker is rendered.
7. Log in as a non-admin; assert no DEV chip and a 403 from the list endpoint.

- [ ] **Step 7: Commit any verification fixes**

Stage only the files you changed and use a conventional `fix(...)` commit message that names the behavior you corrected. If no fixes are needed, do not create an empty commit.

---

## Spec Coverage

| Spec section | Task(s) |
|---|---|
| §3 decisions D1-D14 | Tasks 1-11 |
| §5.1 envelope | Tasks 1-3 |
| §5.2 raw / §5.3 view | Task 2 |
| §6.1 scope | Tasks 1, 5, 6 |
| §6.2 provider hooks | Task 4 |
| §6.3 response | Task 5 |
| §6.4 tool execution | Task 6 |
| §6.5 provenance | Tasks 3, 6 |
| §6.6 auxiliary | Task 6 |
| §6.7 subagents | Task 5 |
| §6.8 diagnostics | Tasks 3, 5 |
| §7 retention/redaction | Tasks 1-2 |
| §8 transport/association/API | Tasks 5, 7, 8 |
| §9 config/permissions | Tasks 1, 7, 8 |
| §10 frontend | Tasks 9-11 |
| §11 behavior/edges | Tasks 1, 5, 7, 8, 11 |
| §14 tests | Every task + Task 12 |

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-11-15-chatui-developer-mode.md`. Please review the plan. Which execution approach would you prefer?

- **Subagent-driven** — a fresh subagent implements each task and a fresh reviewer checks it before the next; most thorough, costs a fresh context per task and per review.
- **Native** — I implement every task in this session, then one fresh reviewer checks the whole branch; cheapest and fastest, no independent review until the end.

**For this plan I recommend Subagent-driven**, because the tasks cross core/provider/dashboard/frontend interfaces and a shipped mistake in the capture boundary would be expensive to debug; each task has its own tests and can be rejected independently. Does the plan capture what you want, and which approach should we use?

