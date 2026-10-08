# LLM Request Injection Trace (ChatUI gutter marker) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every plugin context injection performed through the `on_llm_request` hook detectable, attributable (plugin + handler), and reviewable per turn in ChatUI as a silent gutter marker with an expandable detail panel.

**Architecture:** `call_event_hook` fingerprints the shared `ProviderRequest` around each handler and records per-handler deltas onto the event extras; the tool-loop runner forwards them as a new `llm_request_injections` agent response (exact `agent_stats` pattern: runner → `astr_agent_run_util` → `webchat_event` → `ChatService` → SSE → history record). The frontend renders a 16px left rail per bot message, a 6px resting dot, and an inline panel on click.

**Tech Stack:** Python 3.10+ / pytest; Vue 3 + Pinia + Vuetify / vitest.

**Spec:** `docs/superpowers/specs/2026-10-08-llm-request-injection-trace-design.md`

## Global Constraints

- Base the branch on the current working branch; if executing in a git worktree use `.worktrees/llm-request-injection-trace`, branch `feat/llm-request-injection-trace`.
- English comments/logs; Google-style docstrings; `ruff format .` + `ruff check .` before every backend commit; conventional commits.
- Fixed strings (do not rename anywhere): chain type `llm_request_injections`; event extra key `_llm_request_injections`; history field `content["llm_request_injections"]`; frontend record field `llmRequestInjections`.
- No OpenAPI surface changes are expected (message content is an untyped dict). Do **not** run `pnpm generate:api`; if a client diff appears, stop and inspect.
- i18n keys must exist in all four locales (`zh-CN`, `en-US`, `ja-JP`, `ru-RU`).
- Backend tests: `pytest <path> -v`. Frontend tests: `cd dashboard && node ./node_modules/vitest/vitest.mjs run <spec>`.
- Do not add report/summary files.

## Review Focus

1. **A plugin handler raises mid-hook** — the turn must continue and other handlers' injections must still be traced; the raiser leaves no entry. Pinned by Task 1 `test_raising_handler_leaves_no_entry_and_others_traced`.
2. **In-place rewrite of `system_prompt` with unchanged length** — must surface as "已改写" (rewritten), not vanish. Pinned by Task 1 `test_equal_length_rewrite_is_reported_as_rewritten`.
3. **The payload must never render as literal JSON text in any consumer** — primary accumulator, OpenAPI and live transports, system stream. Pinned by Task 3 `test_injections_payload_never_falls_through_as_text` and Task 4 Step 5's system-stream case.
4. **A turn with no injections** — no event, no history field, no marker. Pinned by Task 3 `test_history_content_omits_empty_injections` and Task 5's "renders no marker for a message without injections" case.
5. **A malformed or partial payload on the wire** — marker absent, no crash, no leaked text. Pinned by Task 4 `parseLlmRequestInjections` tests.

---

### Task 1: Trace module + `call_event_hook` observation

**Files:**
- Create: `astrbot/core/pipeline/llm_request_trace.py`
- Modify: `astrbot/core/pipeline/context_utils.py` (`call_event_hook`, L78-112)
- Test: `tests/unit/test_llm_request_trace.py`

**Interfaces:**
- Consumes: nothing new (`ProviderRequest` fields `system_prompt: str`, `contexts: list[dict]`, `extra_user_content_parts: list[ContentPart]`, `func_tool: ToolSet | None`; `ToolSet.names()`).
- Produces:
  - `EVENT_EXTRA_KEY: str = "_llm_request_injections"`, `PREVIEW_CHARS: int = 80`
  - `snapshot(request: object) -> dict` — keys `system_prompt` `(len, sha1)`, `contexts` `(len, sha1)`, `extra_user_content_parts` `int`, `func_tool` `tuple[str, ...]`.
  - `collect_changes(request: object, before: dict, after: dict) -> list[dict]` — items `{"field": str, "delta": int, "lossy": bool, "preview": str}`.
  - `record_injections(event: object, plugin: str, handler: str, changes: list[dict]) -> None` — appends `{"plugin", "handler", "changes"}` to the `EVENT_EXTRA_KEY` list on the event.
  - `read_injections(event: object | None) -> list[dict]` — tolerant of `None` and missing extras.

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for the LLM request injection trace (spec §4)."""

from __future__ import annotations

import pytest

from astrbot.core.pipeline.llm_request_trace import (
    EVENT_EXTRA_KEY,
    collect_changes,
    read_injections,
    record_injections,
    snapshot,
)
from astrbot.core.provider.entities import ProviderRequest


class _FakeEvent:
    plugins_name = None

    def __init__(self) -> None:
        self.extras: dict = {}

    def is_stopped(self) -> bool:
        return False

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value) -> None:
        self.extras[key] = value


def test_system_prompt_append_reports_delta_and_preview():
    req = ProviderRequest()
    req.system_prompt = "base"
    before = snapshot(req)

    req.system_prompt = "base" + "X" * 412
    changes = collect_changes(req, before, snapshot(req))

    assert changes == [
        {"field": "system_prompt", "delta": 412, "lossy": False, "preview": "X" * 80}
    ]


def test_no_change_collects_nothing():
    req = ProviderRequest()
    before = snapshot(req)
    assert collect_changes(req, before, snapshot(req)) == []


def test_equal_length_rewrite_is_reported_as_rewritten():
    req = ProviderRequest()
    req.system_prompt = "base"
    before = snapshot(req)

    req.system_prompt = "BASE"
    changes = collect_changes(req, before, snapshot(req))

    assert changes == [
        {"field": "system_prompt", "delta": 0, "lossy": False, "preview": ""}
    ]


def test_contexts_append_reports_count_and_last_text():
    req = ProviderRequest()
    before = snapshot(req)

    req.contexts = [{"role": "user", "content": "hello context"}]
    changes = collect_changes(req, before, snapshot(req))

    assert changes == [
        {"field": "contexts", "delta": 1, "lossy": False, "preview": "hello context"}
    ]


def test_record_and_read_round_trip():
    event = _FakeEvent()
    record_injections(
        event,
        "tc_memory",
        "decorate_llm_req",
        [{"field": "contexts", "delta": 1, "lossy": False, "preview": ""}],
    )
    record_injections(
        event,
        "spcode_toolkit",
        "inject",
        [{"field": "func_tool", "delta": 1, "lossy": False, "preview": "codegraph"}],
    )

    items = read_injections(event)
    assert [i["plugin"] for i in items] == ["tc_memory", "spcode_toolkit"]
    assert read_injections(None) == []
    assert event.extras[EVENT_EXTRA_KEY] == items


@pytest.mark.asyncio
async def test_call_event_hook_traces_each_handler():
    from astrbot.core.pipeline.context_utils import call_event_hook
    from astrbot.core.star.star import StarMetadata, star_map
    from astrbot.core.star.star_handler import (
        EventType,
        StarHandlerMetadata,
        star_handlers_registry,
    )

    event = _FakeEvent()
    req = ProviderRequest()

    async def _inject(_event, _req):
        _req.system_prompt += " injected"

    module_path = "test_llm_request_trace_mod"
    star_map[module_path] = StarMetadata(name="demo_plugin", module_path=module_path)
    meta = StarHandlerMetadata(
        event_type=EventType.OnLLMRequestEvent,
        handler_full_name=f"{module_path}_inject",
        handler_name="inject",
        handler_module_path=module_path,
        handler=_inject,
        event_filters=[],
    )
    star_handlers_registry.append(meta)
    try:
        stopped = await call_event_hook(event, EventType.OnLLMRequestEvent, req)
    finally:
        star_handlers_registry._handlers.remove(meta)
        star_handlers_registry.star_handlers_map.pop(meta.handler_full_name, None)
        star_map.pop(module_path, None)

    assert stopped is False
    items = [i for i in read_injections(event) if i["plugin"] == "demo_plugin"]
    assert len(items) == 1
    assert items[0]["handler"] == "inject"
    assert items[0]["changes"][0]["delta"] == len(" injected")


@pytest.mark.asyncio
async def test_raising_handler_leaves_no_entry_and_others_traced():
    # Same registry dance as above with two handlers: a raiser registered first,
    # then a working injector. Assert: exactly one item, plugin == "demo_plugin",
    # and the turn did not raise.
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_llm_request_trace.py -v`
Expected: FAIL — `ModuleNotFoundError: astrbot.core.pipeline.llm_request_trace`

- [ ] **Step 3: Implement `astrbot/core/pipeline/llm_request_trace.py`**

Bodies for the four functions; the only non-obvious parts are (a) `contexts`/`extra_user_content_parts` preview extraction — first 80 chars of the appended tail's text (`dict["content"]` string, list of part dicts with `"text"`, or object with `.text`; anything else renders `""`), and (b) `func_tool` preview — join added/removed names with `", "` and cap at `PREVIEW_CHARS`. `collect_changes` compares fields in the fixed order `system_prompt, contexts, extra_user_content_parts, func_tool` and skips unchanged fields.

- [ ] **Step 4: Wire the loop in `context_utils.call_event_hook`**

```python
    tracing = hook_type is EventType.OnLLMRequestEvent and bool(args)
    for handler in handlers:
        before = snapshot(args[0]) if tracing else None
        try:
            assert inspect.iscoroutinefunction(handler.handler)
            logger.debug(...)
            await handler.handler(event, *args, **kwargs)
        except BaseException:
            logger.error(traceback.format_exc())
        if before is not None:
            changes = collect_changes(args[0], before, snapshot(args[0]))
            star = star_map.get(handler.handler_module_path)
            if changes and star is not None:
                record_injections(event, star.name or "", handler.handler_name, changes)
        if event.is_stopped():
            ...
```

The `star_map.get(...)` guard is load-bearing: a `KeyError` after the `try` would abort dispatch for every remaining handler.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_llm_request_trace.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add astrbot/core/pipeline/llm_request_trace.py astrbot/core/pipeline/context_utils.py tests/unit/test_llm_request_trace.py
git commit -m "feat: trace plugin context injections in the llm request hook"
```

---

### Task 2: Emit the trace on the webchat run stream

**Files:**
- Modify: `astrbot/core/agent/runners/tool_loop_agent_runner.py` (new method near the `file_changes` builder ~L220-245; yield immediately after the `agent_stats` yield ~L1081)
- Modify: `astrbot/core/astr_agent_run_util.py` (new branch after the `file_changes` branch, L205-208)
- Modify: `astrbot/core/platform/sources/webchat/webchat_event.py` (skip tuple in `_persist_bot_reply_if_orphan`, L54-58)
- Test: `tests/unit/test_agent_runner_injection_trace.py`

**Interfaces:**
- Consumes: `read_injections(event)` from Task 1.
- Produces: `ToolLoopAgentRunner._injection_trace_response() -> AgentResponse | None` — returns `None` when nothing was recorded; otherwise an `AgentResponse(type="llm_request_injections")` whose `data["chain"]` is `MessageChain(type="llm_request_injections", chain=[Json(data={"items": <items>})])`.

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for the runner's injection-trace response (spec §4.3)."""

from __future__ import annotations

from types import SimpleNamespace

from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.pipeline.llm_request_trace import EVENT_EXTRA_KEY


def _runner_with_items(items: list[dict]) -> ToolLoopAgentRunner:
    runner = ToolLoopAgentRunner()
    event = SimpleNamespace(
        get_extra=lambda key, default=None: {EVENT_EXTRA_KEY: items}.get(key, default)
    )
    runner.run_context = SimpleNamespace(context=SimpleNamespace(event=event))
    return runner


def test_returns_none_without_recorded_items():
    assert _runner_with_items([])._injection_trace_response() is None


def test_builds_json_chain_payload():
    items = [{"plugin": "tc_memory", "handler": "decorate_llm_req", "changes": []}]
    resp = _runner_with_items(items)._injection_trace_response()

    assert resp is not None
    assert resp.type == "llm_request_injections"
    chain = resp.data["chain"]
    assert chain.type == "llm_request_injections"
    assert chain.chain[0].data == {"items": items}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_agent_runner_injection_trace.py -v`
Expected: FAIL (`AttributeError: _injection_trace_response`)

- [ ] **Step 3: Implement the method and the yield**

Method body: read the event with `getattr(self.run_context.context, "event", None)`, call `read_injections`, build the response via the same imports the `agent_stats` yield already uses (`AgentResponse`, `AgentResponseData`, `MessageChain`, `Json`). At the `agent_stats` yield site add:

```python
            injection_resp = self._injection_trace_response()
            if injection_resp is not None:
                yield injection_resp
```

- [ ] **Step 4: Add the transport branches**

`astr_agent_run_util.py` (after the `file_changes` branch):

```python
                if resp.type == "llm_request_injections":
                    if astr_event.get_platform_name() == "webchat":
                        await astr_event.send(resp.data["chain"])
                    continue
```

`webchat_event.py`: add `"llm_request_injections"` to the tuple in `_persist_bot_reply_if_orphan` (next to `agent_stats` / `context_compression`).

- [ ] **Step 5: Run tests and the regression set**

Run: `pytest tests/unit/test_agent_runner_injection_trace.py tests/test_tool_loop_agent_runner.py tests/unit/test_system_notice_injection.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add astrbot/core/agent/runners/tool_loop_agent_runner.py astrbot/core/astr_agent_run_util.py astrbot/core/platform/sources/webchat/webchat_event.py tests/unit/test_agent_runner_injection_trace.py
git commit -m "feat: emit llm request injection trace on the webchat run stream"
```

---

### Task 3: Persist on the bot record; ignore in non-ChatUI transports

**Files:**
- Modify: `astrbot/dashboard/services/chat_service.py` (`build_bot_history_content` L279; `save_bot_message` L1504; `ChatRunState` L1114; `_consume_chat_run` branch after `file_changes` ~L1904; flush call sites ~L1844/L1856; snapshot call sites ~L1549/L1648; `BotMessageAccumulator` drop ~L425)
- Modify: `astrbot/dashboard/services/open_api_service.py` (branch after the `agent_stats` branch ~L489)
- Modify: `astrbot/dashboard/services/live_chat_service.py` (branch after the `agent_stats` branch ~L701)
- Test: `tests/unit/test_chat_service_llm_request_injections.py`

**Interfaces:**
- Consumes: chain type `llm_request_injections`, payload `{"items": [...]}` (Task 2).
- Produces: `build_bot_history_content(message_parts, *, ..., llm_request_injections: dict | None = None) -> dict`; `ChatService.save_bot_message(..., llm_request_injections: dict | None = None)`; `ChatRunState.llm_request_injections: dict`.

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for injections on the ChatUI history record (spec §4.4)."""

from __future__ import annotations

import json

from astrbot.dashboard.services.chat_service import (
    BotMessageAccumulator,
    build_bot_history_content,
)


def test_injections_payload_never_falls_through_as_text():
    acc = BotMessageAccumulator()
    acc.add_plain(
        json.dumps({"items": [{"plugin": "tc_memory"}]}),
        chain_type="llm_request_injections",
        streaming=False,
    )
    assert acc.parts == []


def test_history_content_carries_injections():
    content = build_bot_history_content(
        [{"type": "plain", "text": "hi"}],
        llm_request_injections={"items": [{"plugin": "tc_memory"}]},
    )
    assert content["llm_request_injections"]["items"][0]["plugin"] == "tc_memory"


def test_history_content_omits_empty_injections():
    content = build_bot_history_content([{"type": "plain", "text": "hi"}])
    assert "llm_request_injections" not in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_chat_service_llm_request_injections.py -v`
Expected: FAIL (`TypeError: build_bot_history_content() got an unexpected keyword argument`)

- [ ] **Step 3: Implement the chat_service wiring**

1. `build_bot_history_content`: new keyword param, `if llm_request_injections: content["llm_request_injections"] = llm_request_injections` (mirror `agent_stats`).
2. `save_bot_message`: new keyword param forwarded to `build_bot_history_content`.
3. `ChatRunState`: `llm_request_injections: dict = field(default_factory=dict)`.
4. `_consume_chat_run`, immediately after the `file_changes` branch:

```python
                if chain_type == "llm_request_injections":
                    try:
                        run.llm_request_injections = json.loads(result_text)
                    except (TypeError, json.JSONDecodeError):
                        run.llm_request_injections = {}
                    self._publish_chat_run(
                        run,
                        {"type": "llm_request_injections", "data": run.llm_request_injections},
                    )
                    continue
```

5. Flush: pass `llm_request_injections=run.llm_request_injections` at the `save_bot_message` and `platform_history_mgr.update` call sites; snapshot builders (~L1549, ~L1648): `llm_request_injections=deepcopy(run.llm_request_injections)`.
6. `BotMessageAccumulator` (system-stream path, ~L425): add `if chain_type == "llm_request_injections": return` next to the `context_compression` drop (orphan goal turns must not persist a JSON text part).

- [ ] **Step 4: Guard the non-ChatUI transports**

In `open_api_service.py` and `live_chat_service.py`, add an early `continue` for `chain_type == "llm_request_injections"` right after their `agent_stats` branches (both consume the same back-queue payloads; without the guard the JSON string renders as response text).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_chat_service_llm_request_injections.py tests/unit/test_chat_service_file_changes.py tests/unit/test_chat_service_context_compression.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add astrbot/dashboard/services/chat_service.py astrbot/dashboard/services/open_api_service.py astrbot/dashboard/services/live_chat_service.py tests/unit/test_chat_service_llm_request_injections.py
git commit -m "feat: persist llm request injection trace on webchat bot records"
```

---

### Task 4: Frontend leaf module + stream/history wiring

**Files:**
- Create: `dashboard/src/composables/llmRequestInjections.ts`
- Create: `dashboard/src/composables/llmRequestInjections.spec.ts`
- Create: `dashboard/src/composables/__tests__/useMessagesLlmRequestInjections.spec.ts`
- Modify: `dashboard/src/composables/useMessages.ts` (`ChatContent` ~L200; primary dispatch after the `agent_stats` branch ~L2013; `normalizeHistoryRecord` ~L1278; system-stream inline drop ~L498)
- Modify: `dashboard/src/components/chat/ThreadPanel.vue` (~L198)

**Interfaces:**
- Consumes: `formatTokenCount` from `@/composables/contextCompressionNotice`.
- Produces (all exported from `llmRequestInjections.ts`):

```ts
export interface LlmRequestInjectionChange { field: string; delta: number; lossy: boolean; preview: string }
export interface LlmRequestInjectionItem { plugin: string; handler: string; changes: LlmRequestInjectionChange[] }
export interface LlmRequestInjections { items: LlmRequestInjectionItem[] }
export function parseLlmRequestInjections(data: unknown): LlmRequestInjections | null;
export function injectionChangeLabel(
  change: LlmRequestInjectionChange,
  translate: (key: string, params?: Record<string, string | number>) => string,
): string;
```

`injectionChangeLabel` rules: `delta === 0` → `translate("llmRequestInjections.rewritten")`; otherwise unit key by field (`system_prompt` → `units.chars`, `contexts` / `extra_user_content_parts` → `units.items`, `func_tool` → `units.tools`), `delta` formatted as `+`/`-` + `formatTokenCount(Math.abs(delta))`, and when `lossy` append `" · " + translate("llmRequestInjections.lossySuffix")`.

- [ ] **Step 1: Write the failing leaf tests**

```ts
import { describe, expect, it } from "vitest";
import {
  injectionChangeLabel,
  parseLlmRequestInjections,
} from "./llmRequestInjections";

const tm = (key: string, params?: Record<string, string | number>) =>
  params ? `${key}|${params.delta}` : key;

const ITEM = {
  plugin: "tc_memory",
  handler: "decorate_llm_req",
  changes: [{ field: "system_prompt", delta: 412, lossy: false, preview: "## 长期记忆" }],
};

describe("parseLlmRequestInjections", () => {
  it("parses a JSON string payload", () => {
    expect(parseLlmRequestInjections(JSON.stringify({ items: [ITEM] }))?.items[0].plugin)
      .toBe("tc_memory");
  });
  it("accepts an already decoded object", () => {
    expect(parseLlmRequestInjections({ items: [ITEM] })?.items.length).toBe(1);
  });
  it("rejects malformed JSON and payloads without items", () => {
    expect(parseLlmRequestInjections("not-json")).toBeNull();
    expect(parseLlmRequestInjections({})).toBeNull();
    expect(parseLlmRequestInjections(null)).toBeNull();
  });
});

describe("injectionChangeLabel", () => {
  it("labels an append with sign and unit", () => {
    expect(injectionChangeLabel(ITEM.changes[0], tm))
      .toBe("llmRequestInjections.units.chars|+412");
  });
  it("labels a lossy shrink with the trimmed suffix", () => {
    expect(injectionChangeLabel({ field: "system_prompt", delta: -1200, lossy: true, preview: "" }, tm))
      .toBe("llmRequestInjections.units.chars|-1.2k · llmRequestInjections.lossySuffix");
  });
  it("labels an equal-length rewrite", () => {
    expect(injectionChangeLabel({ field: "system_prompt", delta: 0, lossy: false, preview: "" }, tm))
      .toBe("llmRequestInjections.rewritten");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd dashboard && node ./node_modules/vitest/vitest.mjs run src/composables/llmRequestInjections.spec.ts`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `llmRequestInjections.ts`**

- [ ] **Step 4: Wire `useMessages.ts`**

1. `ChatContent`: add `llmRequestInjections?: LlmRequestInjections | null;`.
2. Primary dispatch, right after the `agent_stats` branch:

```ts
    if (msgType === "llm_request_injections" || chainType === "llm_request_injections") {
      markMessageStarted(botRecord);
      messageContent(botRecord).llmRequestInjections =
        parseLlmRequestInjections(data) ?? undefined;
      return;
    }
```

3. `normalizeHistoryRecord`: `llmRequestInjections: parseLlmRequestInjections(content.llm_request_injections ?? content.llmRequestInjections) ?? undefined`.
4. System-stream inline handling next to `context_compression` (~L498): drop the payload (`return`) so the JSON never reaches the leaf's plain-text fall-through.
5. `ThreadPanel.vue` record mapping: add `llmRequestInjections: content.llmRequestInjections || content.llm_request_injections`.

- [ ] **Step 5: Write and run the dispatch spec**

Model the harness on `__tests__/useMessagesPlaceholderSettle.spec.ts` (mocks `@/api/v1` + `@/api/http`, calls `composable.sendMessageStream({...})`, observes `composable.activeMessages.value`). Feed one SSE frame:

```ts
const PAYLOAD = {
  type: "llm_request_injections",
  data: { items: [ITEM] },
  message_id: "m1",
};
```

Assertions: the bot record's `content.llmRequestInjections?.items[0].plugin === "tc_memory"`, and no `content.message` plain part contains `"tc_memory"`.

Also cover the system stream in the same spec file: drive the system-stream harness the way `__tests__/useMessagesContextCompression.spec.ts` does, feed the same payload shape with `chain_type: "llm_request_injections"`, and assert no record gains a plain part containing `"tc_memory"` (the inline drop).

Run: `cd dashboard && node ./node_modules/vitest/vitest.mjs run src/composables/llmRequestInjections.spec.ts src/composables/__tests__/useMessagesLlmRequestInjections.spec.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/composables/llmRequestInjections.ts dashboard/src/composables/llmRequestInjections.spec.ts dashboard/src/composables/__tests__/useMessagesLlmRequestInjections.spec.ts dashboard/src/composables/useMessages.ts dashboard/src/components/chat/ThreadPanel.vue
git commit -m "feat: wire llm request injection trace into chat message records"
```

---

### Task 5: Gutter rail UI + i18n

**Files:**
- Create: `dashboard/src/components/chat/LlmRequestInjectionsPanel.vue`
- Create: `dashboard/src/components/chat/ChatMessageList.injectionTrail.spec.ts`
- Modify: `dashboard/src/components/chat/ChatMessageList.vue`
- Modify: `dashboard/src/i18n/locales/{zh-CN,en-US,ja-JP,ru-RU}/features/chat.json`
- Modify: `dashboard/src/i18n/i18n.completeness.spec.ts`

**Interfaces:**
- Consumes: `LlmRequestInjections` / `LlmRequestInjectionItem` / `injectionChangeLabel` (Task 4); `ChatContent.llmRequestInjections` (Task 4).
- Produces: `LlmRequestInjectionsPanel.vue` — props `{ items: LlmRequestInjectionItem[] }`, no emits, root class `injection-panel`; `ChatMessageList.vue` helpers `injectionItems(msg)`, `injectionTrailOpen(msg, msgIndex)`, `toggleInjectionTrail(msg, msgIndex)`.

- [ ] **Step 1: Write the failing component test**

Model on `ChatMessageList.revealWork.spec.ts` (store mocks + `mount`). Cases:

```ts
it("renders no marker for a message without injections", ...);      // find(".injection-marker") → false
it("renders the rail marker and expands the panel on click", ...);  // click → find(".injection-panel") → true
it("lists plugin, handler and a lossy change with its suffix", ...); // text contains "tc_memory", "已截断"
```

Fixture: a bot `ChatRecord` whose `content.llmRequestInjections` is `{ items: [{ plugin: "tc_memory", handler: "decorate_llm_req", changes: [{ field: "system_prompt", delta: 412, lossy: false, preview: "## 长期记忆" }, { field: "system_prompt", delta: -1200, lossy: true, preview: "" }] }] }`. Panel starts **collapsed**.

- [ ] **Step 2: Run to verify it fails**

Run: `cd dashboard && node ./node_modules/vitest/vitest.mjs run src/components/chat/ChatMessageList.injectionTrail.spec.ts`
Expected: FAIL (`.injection-marker` not found)

- [ ] **Step 3: Add the i18n keys**

All four locales get the `llmRequestInjections` block exactly as specified in the spec §5.5 (`tooltip`, `title`, `count`, `note`, `units.{chars,items,tools}`, `lossySuffix`, `rewritten`). Extend `i18n.completeness.spec.ts` with a block asserting those keys are strings in every locale (mirror the existing `contextCompression` completeness block).

- [ ] **Step 4: Implement the rail, marker and panel**

- `ChatMessageList.vue`: insert a fixed 16px `.message-rail` flex column between the bot avatar and `.message-stack` (bot rows only, always present to avoid layout shift); inside it the `.injection-marker` button when `injectionItems(msg).length > 0`, wrapped in a `v-tooltip` (location `right`) whose text is `tm("llmRequestInjections.tooltip", { count })`. Render `<LlmRequestInjectionsPanel v-if="injectionTrailOpen(msg, msgIndex)" :items="injectionItems(msg)" />` at the end of `.message-stack`. Resting state = 6px `#d3d8df` square (`.injection-marker-dot`); hover = 8px ink-2; open = dot + `--bg-2` button background. Hide the rail under `@media (max-width: 560px)`.
- `LlmRequestInjectionsPanel.vue`: header (title / count / note), one block per item (plugin bold + handler right-aligned muted), one row per change (monospace field + `injectionChangeLabel`), preview block when `preview` is non-empty, amber foreground + `--warn-bg`-style background for `lossy` rows (reuse the `rgba(var(--v-theme-warning), …)` approach from `.compression-chip--lossy`).

- [ ] **Step 5: Run the frontend suite**

Run: `cd dashboard && node ./node_modules/vitest/vitest.mjs run src/components/chat/ChatMessageList.injectionTrail.spec.ts src/i18n/i18n.completeness.spec.ts src/composables/llmRequestInjections.spec.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/components/chat/LlmRequestInjectionsPanel.vue dashboard/src/components/chat/ChatMessageList.vue dashboard/src/components/chat/ChatMessageList.injectionTrail.spec.ts dashboard/src/i18n/locales dashboard/src/i18n/i18n.completeness.spec.ts
git commit -m "feat: show llm request injection marker and panel in chatui"
```

---

## Manual verification (after all tasks)

1. Enable a plugin that injects on `on_llm_request` (e.g. `tc_memory`), send one message in ChatUI.
2. After the reply completes: a dim 6px dot appears in the left rail; hover shows "上下文注入 · N 项"; click expands the panel with plugin / handler / `+N 字符` / preview.
3. A turn where no plugin injects: the rail stays empty.
4. Hard refresh: the marker persists (history carries `content.llm_request_injections`).
5. OpenAPI stream and live chat output for the same turn contain no JSON text.
