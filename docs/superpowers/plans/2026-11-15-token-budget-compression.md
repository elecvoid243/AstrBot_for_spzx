# Token Budget 上下文压缩 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `overflow_strategy=token_budget` 成为可真正执行的压缩策略：不调用模型摘要，直接把历史重置为“初始上下文 + 当前轮”，并在窗口内提供两条一次性提示。

**Architecture:** 预算判定仍由 `resolve_compaction_spec` / `ContextManager` 负责，新增 `TokenBudgetCompressor` 只做裁剪；窗口状态（窗口 id、序号、两条提示是否已发）随历史以 `_window` 内部字段落库，读取时反向扫描取最新。策略从压缩配置透传到 `ContextConfig.strategy`，与 `llm_compress` 共用同一套阈值配置。

**Tech Stack:** Python 3.12、pytest + pytest-asyncio、pydantic v2、Vue 3 + TypeScript（dashboard 侧只改常量与 locale）。

**Spec:** `docs/superpowers/specs/2026-11-15-token-budget-compression-design.md`

## Global Constraints

- 不新增第三方依赖。
- 不新增数据库表或列；窗口状态只写进既有 `conversations.content` 的 JSON。
- `config_version` 保持 `4`，不做配置迁移。
- 提醒阈值默认值 `8192`（`provider.compaction_reminder_tokens`），仅 `token_budget` 读取。
- `WindowState.seq` 从 `0` 开始，`0` 表示从未换窗。
- UI 文案（chip 策略名、配置元数据、文档）必须落在 `zh-CN` / `en-US` / `ru-RU` / `ja-JP` 四个 locale；模型可见的内核提示是英文常量（内核没有运行时 i18n）。
- 不改动 `llm_compress` 的阈值语义、`keep_recent_ratio` 的 `[0, 0.3]` 钳制、以及 `2026-10-31` spec 定义的预算与配对规则。
- 提交信息用英文、原子提交；不 push。工作区里与本次无关的未提交改动不得带入提交。
- 后端改动需重启生效；前端 `dashboard` 构建到 `data/dist` 即生效。

## Review Focus

1. 历史里已有摘要（从 `llm_compress` 切过来）时执行换窗：必须连摘要消息一起丢弃，不能保留 `compaction_id` 消息，也不能抛 `ToolProtocolIntegrityError`。（Task 2）
2. 当前轮含并行工具调用、部分结果缺失：换窗后仍满足协议，由 `fix_messages` 补齐 unknown 结果，不得劈开批次。（Task 2）
3. 消息列表只有 system、单轮或空历史：`reset_to_latest_round` 原样返回，不抛异常。（Task 2）
4. 旧版本或损坏的 `_window` 字段：不能炸掉整段历史读取。（Task 1）
5. 提示注入后同一次 run 的下一步再次 `process()`：提示不能被当成“当前真实请求”而选错压缩区间，也不能重复注入。（Task 5）

---

### Task 1: 窗口状态字段与内部消息标记

**Files:**
- Modify: `astrbot/core/agent/message.py`
- Test: `tests/agent/test_message_window_state.py`（新建）

**Interfaces:**
- Consumes: 现有 `Message` / `model_dump_for_context` / `_no_save` PrivateAttr 模式。
- Produces:
  - `class WindowState(BaseModel)`：`id: str`、`seq: int = 0`、`reminder_sent: bool = False`、`exhausted_sent: bool = False`
  - `Message.window: WindowState | None`（`Field(default=None, alias="_window", exclude=True)`）
  - `Message._internal: bool`（`PrivateAttr(default=False)`）
  - `latest_window_state(messages: list[Message]) -> WindowState | None`（反向扫描）

- [ ] **Step 1: 写失败的测试**

```python
# tests/agent/test_message_window_state.py
from astrbot.core.agent.message import Message, WindowState, latest_window_state


def test_window_state_round_trips_through_history():
    state = WindowState(id="w1", seq=2, reminder_sent=True)
    message = Message(role="user", content="hi")
    message.window = state
    dumped = message.model_dump_for_context()
    assert dumped["_window"] == {
        "id": "w1",
        "seq": 2,
        "reminder_sent": True,
        "exhausted_sent": False,
    }
    assert Message.model_validate(dumped).window == state


def test_window_state_never_reaches_the_provider():
    message = Message(role="user", content="hi")
    message.window = WindowState(id="w1")
    message._internal = True
    dumped = message.model_dump(mode="json")
    assert "_window" not in dumped
    assert "_internal" not in dumped


def test_latest_window_state_takes_newest_marker():
    first = Message(role="user", content="a")
    first.window = WindowState(id="w1", seq=1)
    last = Message(role="assistant", content="b")
    last.window = WindowState(id="w2", seq=2)
    assert latest_window_state([first, Message(role="user", content="c"), last]) == last.window


def test_latest_window_state_ignores_malformed_marker():
    broken = Message.model_validate(
        {"role": "user", "content": "a", "_window": {"seq": "not-an-int"}}
    )
    assert latest_window_state([broken]) is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/agent/test_message_window_state.py -v`
Expected: FAIL — `ImportError: cannot import name 'WindowState'`

- [ ] **Step 3: 实现**

在 `message.py` 中新增 `WindowState`（pydantic `BaseModel`，不额外加校验器），给 `Message` 加 `window` 字段与 `_internal` PrivateAttr；`model_dump_for_context` 中比照 `_compaction_id` 追加 `_window`；`latest_window_state` 用 `try/except (ValidationError, TypeError)` 跳过坏字段（返回值 `None` 而不是抛错）。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/agent/test_message_window_state.py tests/test_conversation_checkpoint.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add astrbot/core/agent/message.py tests/agent/test_message_window_state.py
git commit -m "feat(agent): add window state metadata to messages"
```

---

### Task 2: `reset_to_latest_round` 裁剪原语

**Files:**
- Modify: `astrbot/core/agent/context/truncator.py`
- Test: `tests/agent/test_truncator_reset.py`（新建）

**Interfaces:**
- Consumes: `Message`、`split_into_rounds`、`ContextTruncator._split_system_rest`、`ContextTruncator.fix_messages`
- Produces: `ContextTruncator.reset_to_latest_round(self, messages: list[Message]) -> list[Message]`

- [ ] **Step 1: 写失败的测试**

```python
# tests/agent/test_truncator_reset.py
from astrbot.core.agent.context.truncator import ContextTruncator
from astrbot.core.agent.message import Message


def test_reset_keeps_system_and_latest_round():
    messages = [
        Message(role="system", content="sys"),
        Message(role="user", content="old"),
        Message(role="assistant", content="old reply"),
        Message(role="user", content="now"),
    ]
    result = ContextTruncator().reset_to_latest_round(messages)
    assert [m.content for m in result] == ["sys", "now"]


def test_reset_preserves_tool_pairing():
    messages = [
        Message(role="user", content="old"),
        Message(role="assistant", content="old reply"),
        Message(
            role="assistant",
            content=None,
            tool_calls=[{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
        ),
        Message(role="tool", tool_call_id="c1", content="ok"),
        Message(role="user", content="now"),
    ]
    result = ContextTruncator().reset_to_latest_round(messages)
    assert [m.role for m in result] == ["user"]
    assert result[0].content == "now"


def test_reset_drops_older_summary():
    summary = Message(role="assistant", content="summary", _compaction_id="s1")
    messages = [
        Message(role="system", content="sys"),
        summary,
        Message(role="user", content="now"),
    ]
    assert [m.content for m in ContextTruncator().reset_to_latest_round(messages)] == [
        "sys",
        "now",
    ]


def test_reset_returns_input_when_nothing_to_drop():
    messages = [Message(role="system", content="sys")]
    assert ContextTruncator().reset_to_latest_round(messages) == messages
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/agent/test_truncator_reset.py -v`
Expected: FAIL — `AttributeError: 'ContextTruncator' object has no attribute 'reset_to_latest_round'`

- [ ] **Step 3: 实现**

`reset_to_latest_round` 复用 `_split_system_rest` 与 `split_into_rounds`，保留 `system_messages + rounds[-1]`，最后过 `fix_messages`；`rounds` 为空时返回原对象。**不要**调用 `truncate_by_token_budget(budget_tokens=0)`（该函数在 `<= 0` 时直接返回原列表）。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/agent/test_truncator_reset.py tests/agent/test_compaction_region.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add astrbot/core/agent/context/truncator.py tests/agent/test_truncator_reset.py
git commit -m "feat(agent): add latest-round reset primitive for token budget"
```

---

### Task 3: `TokenBudgetCompressor` 与策略选择

**Files:**
- Modify: `astrbot/core/agent/context/config.py`、`astrbot/core/agent/context/compressor.py`、`astrbot/core/agent/context/manager.py`
- Test: `tests/agent/test_token_budget_compressor.py`（新建）、`tests/agent/test_context_manager.py`（追加两个用例）

**Interfaces:**
- Consumes: Task 2 的 `reset_to_latest_round`。
- Produces:
  - `ContextConfig.strategy: str = "llm_compress"`
  - `TokenBudgetCompressor(token_counter: TokenCounter)`，属性 `strategy = "token_budget"`、`supports_retries = False`、`last_shadowed_tokens: int`
  - `LLMSummaryCompressor.supports_retries = True`、`TruncateByTurnsCompressor.supports_retries = False`
  - `ContextManager.last_remaining_tokens: int | None`（每次 `process()` 开头置 `None`）

- [ ] **Step 1: 写失败的测试**

```python
# tests/agent/test_token_budget_compressor.py
import pytest
from astrbot.core.agent.context.compressor import TokenBudgetCompressor
from astrbot.core.agent.context.config import ContextConfig
from astrbot.core.agent.context.manager import ContextManager
from astrbot.core.exceptions import ContextCapacityError
from astrbot.core.agent.message import Message


class CharTokenCounter:
    def count_tokens(self, messages, trusted_token_usage: int = 0) -> int:
        return sum(len(m.content) for m in messages if isinstance(m.content, str))


@pytest.mark.asyncio
async def test_compressor_resets_and_reports_shadowed_tokens():
    compressor = TokenBudgetCompressor(CharTokenCounter())
    messages = [
        Message(role="system", content="sys"),
        Message(role="user", content="x" * 40),
        Message(role="assistant", content="y" * 40),
        Message(role="user", content="z" * 4),
    ]
    result = await compressor(messages)
    assert [m.content for m in result] == ["sys", "z" * 4]
    assert compressor.last_shadowed_tokens == 80
    assert compressor.supports_retries is False
    assert compressor.strategy == "token_budget"


@pytest.mark.asyncio
async def test_single_round_over_window_still_raises_capacity_error():
    manager = ContextManager(
        ContextConfig(
            max_context_tokens=100,
            threshold_ratio=0.8,
            headroom_tokens=0,
            strategy="token_budget",
            custom_token_counter=CharTokenCounter(),
        )
    )
    with pytest.raises(ContextCapacityError):
        await manager.process([Message(role="user", content="x" * 200)])
```

`tests/agent/test_context_manager.py` 追加：

```python
@pytest.mark.asyncio
async def test_manager_uses_token_budget_compressor(self):
    manager = ContextManager(
        ContextConfig(
            max_context_tokens=1000,
            strategy="token_budget",
            custom_token_counter=CharTokenCounter(),
        )
    )
    messages = self.create_long_rounds(5)
    result = await manager.process(messages)
    assert isinstance(manager.compressor, TokenBudgetCompressor)
    assert manager.last_compression is not None
    assert manager.last_compression["strategy"] == "token_budget"
    assert len(result) == 1


@pytest.mark.asyncio
async def test_manager_reports_remaining_tokens(self):
    manager = ContextManager(
        ContextConfig(
            max_context_tokens=1000,
            threshold_ratio=0.8,
            headroom_tokens=0,
            strategy="token_budget",
            custom_token_counter=CharTokenCounter(),
        )
    )
    messages = [self.create_message("system", "s" * 100), self.create_message("user", "u" * 700)]
    await manager.process(messages)
    assert manager.last_remaining_tokens == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/agent/test_token_budget_compressor.py -v`
Expected: FAIL — `ImportError: cannot import name 'TokenBudgetCompressor'`

- [ ] **Step 3: 实现**

- `ContextConfig` 增 `strategy`；
- `compressor.py` 增 `TokenBudgetCompressor`（`__call__` 调 `reset_to_latest_round`，用 `last_shadowed_tokens` 记录节省量）；
- `manager.__init__` 在 `custom_compressor` 之后、`llm_compress_provider` 之前插入 `strategy == "token_budget"` 分支；
- `_run_compression` 的 `isinstance(LLMSummaryCompressor)` 改为 `self.compressor.supports_retries`；
- `process()` 在解析出 `spec` 后计算 `last_remaining_tokens`（公式见 spec §7.1，`max(0, min(...))`），并在函数开头重置为 `None`。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/agent/test_token_budget_compressor.py tests/agent/test_context_manager.py tests/agent/test_compaction_spec.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add astrbot/core/agent/context/config.py astrbot/core/agent/context/compressor.py astrbot/core/agent/context/manager.py tests/agent/test_token_budget_compressor.py tests/agent/test_context_manager.py
git commit -m "feat(agent): add token budget compressor and strategy selection"
```

---

### Task 4: 配置解析不再改写 `token_budget`

**Files:**
- Modify: `astrbot/core/config/agent_runner.py`
- Test: `tests/unit/test_agent_runner_config.py`（改写已有占位用例）

**Interfaces:**
- Produces: `IMPLEMENTED_OVERFLOW_STRATEGIES = ("llm_compress", "token_budget")`；删除 `PLANNED_OVERFLOW_STRATEGIES`。
- Produces: `resolve_context_compression_config(...)["context_limit_reached_strategy"]` 原样返回 `token_budget`。

- [ ] **Step 1: 改写测试**

把 `test_overflow_strategy_resolves_placeholder_and_legacy_values_to_llm` 中的 `token_budget` 移出回落断言，新增：

```python
def test_resolve_keeps_implemented_token_budget_strategy():
    resolved = resolve_context_compression_config({"overflow_strategy": "token_budget"})
    assert resolved["context_limit_reached_strategy"] == "token_budget"


def test_resolve_falls_back_unknown_strategy():
    resolved = resolve_context_compression_config({"overflow_strategy": "memento"})
    assert resolved["context_limit_reached_strategy"] == "llm_compress"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_agent_runner_config.py -v`
Expected: FAIL — `assert 'llm_compress' == 'token_budget'`

- [ ] **Step 3: 实现**

`IMPLEMENTED_OVERFLOW_STRATEGIES` 改为二元组；删除 `PLANNED_OVERFLOW_STRATEGIES` 与 `normalize_agent_runner` 中的“保留占位”分支；回落 warning 保留给未知取值。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/unit/test_agent_runner_config.py tests/unit/test_cron_context_compression.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add astrbot/core/config/agent_runner.py tests/unit/test_agent_runner_config.py
git commit -m "feat(config): treat token budget as an implemented overflow strategy"
```

---

### Task 5: 两条提示的注入

**Files:**
- Create: `astrbot/core/agent/context/budget_notice.py`
- Modify: `astrbot/core/agent/runners/tool_loop_agent_runner.py`、`astrbot/core/agent/context/compaction_region.py`
- Test: `tests/test_tool_loop_token_budget_notice.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `Message._internal`；Task 3 的 `ContextManager.last_remaining_tokens`。
- Produces:
  - `ToolLoopAgentRunner.window_state: WindowState`（本任务只读不推进）
  - `ToolLoopAgentRunner._inject_budget_notices(messages: list[Message]) -> list[Message]`
  - `compaction_region.select_compaction_range` 跳过 `_internal` 消息
  - `budget_notice.build_reminder(remaining: int) -> str`、`budget_notice.EXHAUSTED_TEXT: str`（英文常量，见 spec §7.4）

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_tool_loop_token_budget_notice.py
def test_notice_injected_once_per_window(runner_factory):
    runner = runner_factory(remaining=100, reminder_tokens=8192)
    first = runner._inject_budget_notices([Message(role="user", content="hi")])
    second = runner._inject_budget_notices(first)
    assert len(first) == 2
    assert first[-1]._internal is True
    assert len(second) == 2
    assert runner.window_state.reminder_sent is True


def test_exhausted_notice_requires_insufficient_report(runner_factory):
    runner = runner_factory(remaining=0, exhausted=True)
    injected = runner._inject_budget_notices([Message(role="user", content="hi")])
    assert injected[-1]._internal is True
    assert runner.window_state.exhausted_sent is True


def test_range_selector_ignores_internal_notice():
    notice = Message(role="user", content="[context budget] ...")
    notice._internal = True
    history = [Message(role="user", content="old"), Message(role="assistant", content="a"), notice]
    selected = select_compaction_range(history, retain_tokens=0, token_counter=CharTokenCounter())
    assert selected is not None
    assert selected.end <= 2
```

`runner_factory` 用 `object.__new__(ToolLoopAgentRunner)` 构造，只设置 `window_state` / `last_remaining_tokens` / `last_compression` 三个属性（沿用 `tests/agent/test_repetition_sanitizer_history.py` 的构造方式）。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_tool_loop_token_budget_notice.py -v`
Expected: FAIL — `AttributeError: ... _inject_budget_notices`

- [ ] **Step 3: 实现**

- 新建 `budget_notice.py`，放两条英文常量与 `build_reminder(remaining)`（`{n}` 用 `str.replace` 替换）；
- `_inject_budget_notices` 依据 `window_state` 的 `reminder_sent` / `exhausted_sent` 与 `last_remaining_tokens` / `last_compression["insufficient"]` 决定是否追加一条 `role="user"`、`_internal=True` 的消息；
- 在 `_prepare_llm_request` 中 `run_context.messages = checked` 之后调用它，并把结果回写给 `run_context.messages`；
- `select_compaction_range` 的 `last_user` 扫描增加 `getattr(message, "_internal", False)` 跳过条件。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_tool_loop_token_budget_notice.py tests/agent/test_compaction_region.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add astrbot/core/agent/context/budget_notice.py astrbot/core/agent/runners/tool_loop_agent_runner.py astrbot/core/agent/context/compaction_region.py tests/test_tool_loop_token_budget_notice.py
git commit -m "feat(agent): inject one-shot token budget notices per window"
```

---

### Task 6: 窗口状态的读取、推进与写回

**Files:**
- Modify: `astrbot/core/agent/context/config.py`、`astrbot/core/agent/runners/tool_loop_agent_runner.py`、`astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py`、`astrbot/core/astr_main_agent.py`
- Test: `tests/test_tool_loop_token_budget_window.py`（新建）、`tests/agent/test_repetition_sanitizer_history.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `latest_window_state` / `WindowState`；Task 5 的 `window_state`。
- Produces:
  - `reset(..., context_limit_reached_strategy: str = "llm_compress", ...)`
  - `InternalAgentSubStage._save_to_history(..., window_state: WindowState | None = None)`
  - `config.resolve_budget_strategy(strategy: str, provider_config: dict) -> tuple[str, int]`（返回生效策略与提醒阈值；阈值非法回落 `8192`，窗口 `<= 0` 时策略回落 `llm_compress`）

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_tool_loop_token_budget_window.py
def test_state_advances_only_after_reset(runner_factory):
    runner = runner_factory(window_state=WindowState(id="w1", seq=0))
    runner._on_context_reset()          # 换窗发生
    assert runner.window_state.seq == 1
    assert runner.window_state.id != "w1"
    assert runner.window_state.reminder_sent is False


def test_runner_reads_state_from_loaded_history(runner_factory):
    history = [Message(role="user", content="hi")]
    history[0].window = WindowState(id="w7", seq=7, reminder_sent=True)
    runner = runner_factory(messages=history)
    assert runner.window_state.seq == 7
    assert runner.window_state.reminder_sent is True


def test_budget_strategy_falls_back_without_window():
    assert resolve_budget_strategy("token_budget", {"max_context_tokens": 0}) == (
        "llm_compress",
        8192,
    )
    assert resolve_budget_strategy(
        "token_budget", {"max_context_tokens": 128000, "compaction_reminder_tokens": 0}
    ) == ("token_budget", 8192)
```

`tests/agent/test_repetition_sanitizer_history.py` 追加：

```python
@pytest.mark.asyncio
async def test_save_to_history_stamps_window_and_skips_internal():
    ...  # 复用该文件既有的 stage/event/req 构造
    notice = Message(role="user", content="[context budget] ...")
    notice._internal = True
    await stage._save_to_history(
        event, req, response, [Message(role="user", content="hi"), notice], None,
        window_state=WindowState(id="w2", seq=2),
    )
    saved = req.conversation.history if isinstance(req.conversation.history, list) else json.loads(req.conversation.history)
    assert all(item.get("content") != notice.content for item in saved)
    assert saved[-1]["_window"]["seq"] == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_tool_loop_token_budget_window.py -v`
Expected: FAIL — `AttributeError: ... _on_context_reset`

- [ ] **Step 3: 实现**

- runner `reset()` 新增 `context_limit_reached_strategy` 参数并存入 `self.context_limit_reached_strategy`，传入 `ContextConfig(strategy=...)`；`self.window_state = latest_window_state(run_context.messages) or WindowState(id=uuid4().hex)`；
- `config.py` 新增 `resolve_budget_strategy`，runner 构造 `ContextConfig` 前调用它，把返回的策略与提醒阈值写进 `ContextConfig`（新增 `reminder_tokens: int = 8192` 字段）；
- 换窗发生后（`checked != contexts` 且 `last_compression["strategy"] == "token_budget"`）调用 `_on_context_reset()`：`seq += 1`、新 `id`、两个 `sent` 复位；
- `internal.py` 两处 `_save_to_history` 调用补 `window_state=agent_runner.window_state`；
- `_save_to_history` 跳过 `_internal` 消息后，把 `window_state` 盖在最后一条待保存消息的 `window` 字段上，再 `dump_messages_with_checkpoints`；
- `astr_main_agent.py` 的 `agent_runner.reset(...)` 补 `context_limit_reached_strategy=config.context_limit_reached_strategy`。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_tool_loop_token_budget_window.py tests/agent/test_repetition_sanitizer_history.py tests/test_conversation_checkpoint.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add astrbot/core/agent/context/config.py astrbot/core/agent/runners/tool_loop_agent_runner.py astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py astrbot/core/astr_main_agent.py tests/test_tool_loop_token_budget_window.py tests/agent/test_repetition_sanitizer_history.py
git commit -m "feat(agent): persist token budget window state with history"
```

---

### Task 7: 子代理压缩参数按策略解析

**Files:**
- Modify: `astrbot/core/astr_agent_tool_exec.py`
- Test: `tests/unit/test_subagent_compression_kwargs.py`（新建）

**Interfaces:**
- Consumes: Task 4 的 `resolve_context_compression_config`。
- Produces: `build_subagent_compression_kwargs(compression_cfg: dict, ctx, provider_id: str) -> dict`
  - `llm_compress`：返回 `{"llm_compress_provider": <Provider | None>}`（沿用“配置的压缩模型优先，其次子代理自身模型”）
  - `token_budget`：返回 `{"context_limit_reached_strategy": "token_budget"}`，不解析摘要模型

- [ ] **Step 1: 写失败的测试**

```python
# tests/unit/test_subagent_compression_kwargs.py
from unittest.mock import MagicMock

from astrbot.core.astr_agent_tool_exec import build_subagent_compression_kwargs
from astrbot.core.provider.provider import Provider


class _Ctx:
    def __init__(self, providers):
        self._providers = providers

    def get_provider_by_id(self, provider_id):
        return self._providers.get(provider_id)


def test_token_budget_skips_summary_provider():
    ctx = _Ctx({"p1": MagicMock(spec=Provider)})
    kwargs = build_subagent_compression_kwargs(
        {"context_limit_reached_strategy": "token_budget", "llm_compress_provider_id": "p1"},
        ctx,
        "p2",
    )
    assert kwargs == {"context_limit_reached_strategy": "token_budget"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_subagent_compression_kwargs.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_subagent_compression_kwargs'`

- [ ] **Step 3: 实现**

把 `astr_agent_tool_exec.py` 中 `if compression_cfg["context_limit_reached_strategy"] == "llm_compress":` 起的整段内联逻辑提取为模块级函数（保持 `isinstance(candidate, Provider)` 校验与 warning 文案不变），`token_budget` 分支直接返回策略键；确认该 dict 最终传给子代理 runner 的 `reset(...)`。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/unit/test_subagent_compression_kwargs.py tests/unit/test_cron_context_compression.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add astrbot/core/astr_agent_tool_exec.py tests/unit/test_subagent_compression_kwargs.py
git commit -m "feat(subagent): resolve compression kwargs by overflow strategy"
```

---

### Task 8: 前端策略名、四语文案与文档

**Files:**
- Modify: `dashboard/src/composables/contextCompressionNotice.ts`、`dashboard/src/composables/contextCompressionNotice.spec.ts`
- Modify: `dashboard/src/i18n/locales/{zh-CN,en-US,ru-RU,ja-JP}/features/chat.json`
- Modify: `dashboard/src/i18n/locales/{zh-CN,en-US,ru-RU,ja-JP}/features/config-metadata.json`
- Modify: `astrbot/core/config/default.py`（metadata label/hint）
- Modify: `docs/zh/use/context-compress.md`、`docs/en/use/context-compress.md`

**Interfaces:**
- Consumes: 后端报告的 `strategy="token_budget"`。
- Produces: `STRATEGY_KEYS.token_budget = "token_budget"`；`LOSSY_STRATEGIES` 含 `token_budget`。

- [ ] **Step 1: 写失败的测试**

在 `contextCompressionNotice.spec.ts` 追加：

```ts
it("marks token budget resets lossy with their own label", () => {
  const chip = contextCompressionChip(
    { strategy: "token_budget", tokensBefore: 1000, tokensAfter: 100 },
    translate,
  );
  expect(chip.lossy).toBe(true);
  expect(chip.tooltip).toContain("contextCompression.strategy.token_budget");
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd dashboard && npm run test -- contextCompressionNotice`
Expected: FAIL — `chip.lossy` 为 `false` 或 tooltip 落到 `generic`

- [ ] **Step 3: 实现**

- `STRATEGY_KEYS` 与 `LOSSY_STRATEGIES` 各加一项；
- 四语 `chat.json` 增 `contextCompression.strategy.token_budget`：zh `换窗`、en `Window reset`、ru `Сброс окна`、ja `ウィンドウリセット`；
- 四语 `config-metadata.json` 与 `default.py` 的 `overflow_strategy` label 去掉“（尚未实现）”，hint 改为“换窗：不调用摘要模型，直接丢弃历史，只保留当前轮”；
- 新增 `compaction_reminder_tokens` 的 metadata（int，默认 `8192`，hint 说明“仅 Token Budget 策略使用，剩余预算低于该值时提醒一次”）；
- `docs/{zh,en}` 用真实语义替换“占位”段落，并写明“LLM 显示历史不受影响”。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd dashboard && npm run test -- contextCompressionNotice i18n.completeness`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add dashboard/src/composables/contextCompressionNotice.ts dashboard/src/composables/contextCompressionNotice.spec.ts dashboard/src/i18n/locales astrbot/core/config/default.py docs/zh/use/context-compress.md docs/en/use/context-compress.md
git commit -m "feat(chatui): surface token budget strategy and document it"
```

---

### Task 9: 回归与集成收尾

**Files:**
- Test: `tests/unit/test_cron_context_compression.py`、`tests/test_tool_loop_context_budget.py`、`tests/agent/test_long_horizon_compaction.py`

**Interfaces:**
- Consumes: 前八个任务的全部产物。

- [ ] **Step 1: 追加端到端用例**

在 `tests/test_tool_loop_context_budget.py` 追加一条：`ContextConfig(strategy="token_budget")` 下，超阈值的一轮请求结束后 `run_context.messages` 只含当前轮，且 `last_compression["strategy"] == "token_budget"`、`compressor` 未发起任何 provider 请求。

- [ ] **Step 2: 运行整组回归**

Run: `python -m pytest tests/agent tests/unit/test_agent_runner_config.py tests/unit/test_cron_context_compression.py tests/test_tool_loop_context_budget.py tests/test_tool_loop_token_budget_notice.py tests/test_tool_loop_token_budget_window.py tests/test_builtin_compact.py -v`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add tests
git commit -m "test(compression): cover token budget window end to end"
```

