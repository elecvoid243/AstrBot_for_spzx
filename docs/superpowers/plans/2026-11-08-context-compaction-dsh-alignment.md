# AstrBot 上下文压缩（长程任务）改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 AstrBot 的上下文压缩从“超过阈值做一次摘要”改造成 dsh 式的“按预算选区 + 严格收益校验 + 目标水位重试 + 可观测结果”，使单轮长程任务能持续压缩且不抖动。

**Architecture:** 新增两个纯函数层：`compaction_spec`（阈值/保留/目标/重试预算）与 `compaction_region`（按工具配对边界选区）。`ContextManager` 只负责编排：修剪 → 最多 N 次摘要 → 目标水位判定 → 硬降级/容量错误。`LLMSummaryCompressor` 只负责“给定区间生成 checkpoint 并做产物校验”。所有阈值只由 spec 解析一次，压缩器不再各自持有 0.82。

**Tech Stack:** Python 3.10+、pytest、ruff；前端 Vue 3 + vitest（仅报告字段展示）。

**Spec:** 本文件 §设计依据（对照 `G:/github/deepseek-harness-master/packages/compaction/compaction-basic/src/` 的 `config.ts` / `region.ts` / `summarizer.ts` / `index.ts`）。行为细节以 dsh 源码为准，不凭记忆。

## Global Constraints

- 仅本地提交，**禁止 push / PR / 构建 dashboard**；提交信息用英文 conventional commits。
- 注释、日志用英文；面板文案用中文并同步 en-US / ja-JP / ru-RU。
- Google-style docstrings；路径用 `pathlib.Path`；不新增第三方依赖。
- 单一真源：`threshold_tokens` / `retain_tokens` / `target_tokens` 只在 `resolve_compaction_spec()` 计算；`LLMSummaryCompressor` 与 `TruncateByTurnsCompressor` 不得再读比例常量。
- 每次自动压缩最多 `1 + compaction_retries` 次摘要调用；任何失败不得落下半截 checkpoint。
- 旧配置键 `max_turns`、`instruction` 继续在迁移时清理；`keep_recent_ratio` 迁移为 `retain_ratio`，保留原值。
- 当前工作区已包含三项未提交改动（移除 max_turns、内置结构化提示词、移除 instruction 配置项）。执行前先确认基线提交，后续每个 Task 单独提交。

---

## 设计依据：dsh 做了什么，哪些照搬

| dsh 机制 | 源码事实 | AstrBot 处置 |
|---|---|---|
| 压力阈值 | `threshold = floor(min(W × ratio, W − O − B))`，默认 ratio 0.8、B 65536 | **照搬公式**；B 改为 provider 级可配，默认 16384 |
| 保留尾部 | `retain = floor((W − O) × 0.16)`，加载期校验 `retain < threshold` | **照搬**；`retain_ratio` 默认 0.16 |
| 选区 | 排除 system 头；从尾反向累加 token 到 retain；回退到 `toolPairingBalancedBefore`；结尾必须 `toolPairingBalancedAfter`；turn 边界不参与 | **照搬**；用“未应答 tool-call 计数”实现，不用轮边界 |
| 单轮超长 | 可以压该轮内更早的、已闭合的 step；整段只有一对巨大 call/result 时返回 null | **照搬**；返回 null 时不发摘要请求 |
| 溢出/手动 | `retainTokens = 0`，压到最近一个平衡切点（最大化头部削减） | **照搬**为硬降级路径 |
| 摘要请求 | 复用当前请求前缀（system + tools + 被遮区间），末尾追加一条 user 指令；`purpose: compaction`；`maxTokens = headroom` | **照搬**结构；AstrBot 已有前缀复用，`maxTokens = min(headroom, 8192)`（AstrBot 的收敛项，避免 16k/64k 长摘要） |
| 产物校验 | 空文本、含图像、`max-tokens` 截断均失败；checkpoint 必须严格小于被遮区间 | **照搬**；另加 AstrBot 扩展 `min_yield_ratio`（默认 0）供调参 |
| checkpoint 形态 | 一条 `user/message`：preamble + `<compacted-summary>` + 摘要 + 闭合标签；`source: compact-checkpoint` | **照搬文案、标签与全文压缩的单 user 形态**；轮内压缩仍用 assistant（当前 user 请求保留在区间外），不新增 source 字段 |
| 重试 | `compactionRetries=1`（同一步额外尝试）、`maxOverflowRetries=1`（同一 agent 溢出重试）；摘要失败本身不重取 | **照搬语义**；AstrBot 的“一步”= 一次 `process()` |
| 事务稳定性 | 摘要期间 surface 变了就中止；失败不落半截 | AstrBot 已有 `messages != input_snapshot` 检查，**保留** |
| 事件观测 | `compaction/start|summary|end` 为 log-only；summary 事件带 range/tokens | AstrBot 无 surface 日志；**改为扩展 `last_compression` 报告**，前端沿用 chip |
| 表面日志 / replaceGeneration / modelPolicies | dsh 架构专有 | **不照搬**；AstrBot 用消息列表快照 + `compaction_id` 识别 |
| 定价域 | shadow 价格用固定启发式，shrink 比较用路由价格；两者可能不等 | AstrBot 全链路只用 `EstimateTokenCounter`，`yield_ratio` 内部一致；**不引入双价格** |

> dsh 自身也会出现“只小 5%、下一步再次触发”：它不设最小收益，严格小于即接受，然后 `measure()` 重估、重试耗尽后 warn 并继续。AstrBot 要解决这个问题，必须靠 **目标水位 + 阈值/保留量** 收敛，而不是只加一个收益检查。

### 术语：checkpoint role 与“严格交替”

- dsh 的 checkpoint 是**单条 user 消息**：`user(preamble + <compacted-summary> + 摘要)`，没有 assistant 回应。
- 已核实：目标部署（DeepSeek-V4.1-Flash + vLLM）支持连续 user 消息。**全文压缩采用单 user checkpoint**；轮内压缩仍用 **assistant checkpoint**（AstrBot 有意把当前 user 请求保留在压缩区间外）。
- “严格交替”指部分 provider 要求 user/assistant 严格交替，连续两条同角色可能被拒绝或被错误合并。AstrBot 的 `fix_messages()` 只修 tool-call/result 配对，不修角色交替。
- 取舍已关闭：全文压缩删除 assistant ack，避免与 dsh 提示词的“不要确认 checkpoint”冲突；轮内路径已经会产生 assistant→assistant，本就依赖同角色相邻可被接受。
- vLLM 链路：AstrBot 的 `openai_chat_completion` 适配器把 `messages` 原样发出，不做角色合并；vLLM 服务端也不校验交替。目标部署已实测支持连续 user，因此全文压缩采用单 user，不再需要探测。
- DeepSeek-V4.1-Flash-1017（`G:/llm/DeepSeek-V4.1-Flash-1017`）**不提供 Jinja chat template**：`tokenizer_config.json` 无 `chat_template`，官方参考实现是 `encoding/encoding.py`；该编码器不做角色交替校验，连续 `<｜User｜>` / `<｜Assistant｜>` 按前缀原样拼接，与实测一致。`tool` 消息必须先 `merge_tool_messages()` 合并进前一条 user。另：该模型窗口 1,048,576，`compress_threshold_tokens` 应显式配置为服务端 `--max-model-len` 之下的预算，不要依赖 0.8×W。

## 现状与目标

当前链路：`process()` 统计 token → `should_compress`（绝对阈值或 0.82×W）→ `prune_tool_results` → `LLMSummaryCompressor` 一次摘要 → 仍超阈值则逐轮丢弃 → 容量错误。

已知问题：

1. 触发线不扣输出与 headroom，压完可能仍接近窗口。
2. 保留量按“当前总量”的 15% 算，且最新批次无条件保留，可压区间小。
3. 摘要产物无上限、不检查截断，checkpoint 可能很大。
4. 没有目标水位/冷却，下一步一个大工具结果就再次触发。
5. 单轮内只做一次 `compact_current_round`，不保证收敛。

验收指标（Task 1 的集成夹具钉住）：

- 单轮 8 个已闭合工具批次 + 1 个最新批次，摘要产物为被遮区间的 25% 时，一次压缩后总 token ≤ `target_tokens`。
- 摘要产物为 80% 时，最多 2 次调用后停止；若仍超阈值，报告 `insufficient=true`，不进行第 3 次调用。
- 任何 `max-tokens` 截断/空摘要/含图像摘要，`last_compression` 不更新，历史不改变。
- 压缩后直到 token 再涨 `min_growth_tokens` 才允许下一次自动压缩。

## 配置改前 / 改后

| 位置 | 改前 | 改后 |
|---|---|---|
| provider | `max_context_tokens`、`compress_threshold_tokens` | 新增 `compress_threshold_ratio`（0.8）、`compaction_headroom_tokens`（16384） |
| compression | `keep_recent_ratio`（0.15，按当前总量） | 改名 `retain_ratio`（0.16，按 `W−O`），迁移旧键 |
| compression | `target_usage_ratio`（0.4，仅截断策略） | 删除用户配置；仅保留内部兜底常量 |
| compression | `trim_turns` | 删除用户配置；仅保留内部兜底常量 |
| compression | `overflow_strategy`、`provider_id`、`fallback_max_tokens` | 保留 |
| 代码常量 | `0.82` 出现在两个 compressor | 删除，统一走 spec |

`max_summary_tokens`、`compaction_retries`（1）、`min_growth_tokens`（0.1W）、`min_yield_ratio`（0）先作为模块常量，不进 UI。

## File Structure

**Create**
- `astrbot/core/agent/context/compaction_spec.py`：`CompactionSpec`、`resolve_compaction_spec()`
- `astrbot/core/agent/context/compaction_region.py`：`CompactionRange`、`select_compaction_range()`、`tool_pairing_balanced_before/after()`
- `tests/agent/test_compaction_spec.py`
- `tests/agent/test_compaction_region.py`
- `tests/agent/test_long_horizon_compaction.py`（集成夹具）

**Modify**
- `astrbot/core/agent/context/compressor.py`：`LLMSummaryCompressor` 用 spec/range；产物校验；checkpoint framing
- `astrbot/core/agent/context/manager.py`：编排、目标水位、重试、冷却、报告
- `astrbot/core/agent/context/config.py`：新增 spec 输入字段
- `astrbot/core/agent/runners/tool_loop_agent_runner.py`：把 provider policy 传入 ContextManager；溢出重试（Task 5）
- `astrbot/core/config/agent_runner.py`、`astrbot/core/config/default.py`、`internal.py`、`astr_main_agent.py`、`astr_agent_tool_exec.py`、`migra_helper.py`：配置链路与迁移
- `dashboard/src/i18n/locales/*/features/config-metadata.json`：四语文案
- `dashboard/src/composables/contextCompressionNotice.ts` 及 spec：新报告字段
- `docs/zh|en/use/context-compress.md`、`docs/zh|en/dev/astrbot-config.md`、`changelogs/internal/2026-11-08-*.md`

## Interfaces 总表

| 名称 | 定义处 | 签名 |
|---|---|---|
| `CompactionSpec` | Task 2 `compaction_spec.py` | frozen dataclass：`context_window, output_budget, headroom_tokens, threshold_tokens, retain_tokens, target_tokens, max_summary_tokens, compaction_retries, min_growth_tokens, min_yield_ratio` |
| `resolve_compaction_spec` | Task 2 | `(*, context_window: int, output_budget: int, threshold_ratio: float, headroom_tokens: int, absolute_threshold_tokens: int, retain_ratio: float, target_ratio: float) -> CompactionSpec \| None` |
| `CompactionRange` | Task 3 | frozen dataclass：`start: int, end: int, shadowed_tokens: int, retained_tokens: int` |
| `tool_pairing_balanced_before` | Task 3 | `(messages: list[Message], index: int) -> bool` |
| `tool_pairing_balanced_after` | Task 3 | `(messages: list[Message], index: int) -> bool` |
| `select_compaction_range` | Task 3 | `(messages, *, retain_tokens: int, token_counter: TokenCounter, protected_messages: list[Message] \| None = None) -> CompactionRange \| None`；`start` 含、`end` 不含 |
| `LLMSummaryCompressor.__call__` | Task 4 | `(messages, func_tool=None, *, spec: CompactionSpec, selected_range: CompactionRange, protected_messages=None) -> list[Message]` |
| `TruncateByTurnsCompressor.__call__` | Task 5 | `(messages, func_tool=None, *, spec: CompactionSpec \| None = None) -> list[Message]` |
| `CompactionOutcome` | Task 4 `manager.py` | dataclass：`messages, strategy, tokens_before, tokens_after, shadowed_tokens, checkpoint_tokens, passes, reason, insufficient` |
| `last_compression` 扩展字段 | Task 4 | `shadowed_tokens, checkpoint_tokens, yield_ratio, passes, reason, threshold_tokens, retain_tokens, target_tokens, context_window, output_budget` |

---

### Task 1: 长程任务基线夹具（先失败）

**Files:**
- Create: `tests/agent/test_long_horizon_compaction.py`
- Modify: 无

**Interfaces:**
- Consumes: `ContextManager`, `ContextConfig`, 现有 `MockProvider`
- Produces: 可复用的 `long_round(closed_batches=8, latest_tokens=4000)` 与 `ScriptedSummaryProvider(ratio)`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_single_long_round_reaches_target_in_one_pass():
    # W=20000, O=2000, B=2000 -> threshold=16000, retain=2880, target=11000
    provider = ScriptedSummaryProvider(ratio=0.25)
    manager = ContextManager(ContextConfig(
        max_context_tokens=20000,
        compress_threshold_tokens=0,
        llm_compress_provider=provider,
        custom_token_counter=CharTokenCounter(),
    ))
    result = await manager.process(long_round(), output_token_budget=2000)
    assert manager.token_counter.count_tokens(result) <= 11000
    assert manager.last_compression["passes"] == 1
    assert manager.last_compression["yield_ratio"] >= 0.5


@pytest.mark.asyncio
async def test_low_yield_summary_stops_after_two_passes():
    provider = ScriptedSummaryProvider(ratio=0.8)
    ...
    assert provider.calls == 2
    assert manager.last_compression["insufficient"] is True
```

- [ ] **Step 2: 运行，确认失败**

Run: `pytest tests/agent/test_long_horizon_compaction.py -q`
Expected: FAIL（当前只压一次、无 `passes`/`yield_ratio` 字段）

- [ ] **Step 3: 只提交夹具，不实现**

```bash
git add tests/agent/test_long_horizon_compaction.py
git commit -m "test(agent): pin long-horizon compaction targets"
```

---

### Task 2: `CompactionSpec` 解析器

**Files:**
- Create: `astrbot/core/agent/context/compaction_spec.py`
- Test: `tests/agent/test_compaction_spec.py`

**Interfaces:**
- Produces: 上表 `CompactionSpec` / `resolve_compaction_spec`

- [ ] **Step 1: 写失败测试**

```python
def test_threshold_subtracts_output_and_headroom():
    spec = resolve_compaction_spec(
        context_window=20000, output_budget=2000, threshold_ratio=0.8,
        headroom_tokens=2000, absolute_threshold_tokens=0,
        retain_ratio=0.16, target_ratio=0.55,
    )
    assert spec.threshold_tokens == 16000
    assert spec.retain_tokens == 2880
    assert spec.target_tokens == 11000


def test_absolute_threshold_cannot_exceed_pressure_budget():
    spec = resolve_compaction_spec(..., absolute_threshold_tokens=999999)
    assert spec.threshold_tokens == 16000


def test_no_pressure_budget_returns_none():
    assert resolve_compaction_spec(
        context_window=1000, output_budget=800, headroom_tokens=400, ...
    ) is None


def test_ratio_retention_must_be_below_threshold():
    spec = resolve_compaction_spec(..., retain_ratio=0.99, ...)
    assert spec.retain_tokens < spec.threshold_tokens  # 钳制而非抛错
```

- [ ] **Step 2: 运行确认失败，Step 3 实现**

实现要点：
- `W <= 0` → `None`。
- `message_budget = W - O`；`pressure_budget = message_budget - B`。
- `pressure_budget <= 0`：先尝试 `B = 0`；仍 `<= 0` → `None` 并由调用方 warn 一次。
- `threshold = min(floor(W * ratio), pressure_budget)`；绝对阈值 > 0 时 `threshold = min(absolute, pressure_budget)`。
- `retain = floor(message_budget * retain_ratio)`；若 `retain >= threshold`，钳制为 `max(1, threshold // 2)`。
- `target = floor(W * target_ratio)`；若 `target >= threshold`，置 0（禁用目标）并 warn。
- 所有输入做 `int`/`float` 防御，非法值回默认。

- [ ] **Step 4: 跑测试**

Run: `pytest tests/agent/test_compaction_spec.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add astrbot/core/agent/context/compaction_spec.py tests/agent/test_compaction_spec.py
git commit -m "feat(agent): resolve dsh-style compaction budgets"
```

---

### Task 3: 工具配对边界与选区

**Files:**
- Create: `astrbot/core/agent/context/compaction_region.py`
- Test: `tests/agent/test_compaction_region.py`

**Interfaces:**
- Produces: 上表 `CompactionRange`、`select_compaction_range`、`tool_pairing_balanced_before/after`

- [ ] **Step 1: 写失败测试**

```python
def test_never_cuts_between_tool_call_and_result():
    messages = [user, assistant(tool_calls=[A]), tool(A), assistant(text)]
    # 在 tool(A) 之前下刀非法；平衡前移
    assert not tool_pairing_balanced_before(messages, 2)
    assert tool_pairing_balanced_before(messages, 3)


def test_compacts_closed_steps_inside_one_long_round():
    messages = user_round_with_batches(8)  # 同一 user 下 8 个闭合批次
    rng = select_compaction_range(messages, retain_tokens=500, token_counter=...)
    assert rng is not None and rng.start >= 1
    assert rng.end < len(messages)  # 至少保留最新批次
    assert tool_pairing_balanced_before(messages, rng.start)
    assert tool_pairing_balanced_after(messages, rng.end - 1)


def test_single_giant_pair_returns_none():
    messages = [user, assistant(tool_calls=[A]), tool(A)]
    assert select_compaction_range(..., retain_tokens=0) is None
```

- [ ] **Step 2: 实现要点**

1. 跳过头部连续 `system`。
2. 从尾向前按“可压单元”累加 token：一个单元 = 一个 assistant 文本消息，或一个完整 tool 批次；累计 ≥ `retain_tokens` 停止。
3. 切点前移直到 `tool_pairing_balanced_before(messages, index)` 为真。
4. `end` 必须满足 `tool_pairing_balanced_after`；否则返回 `None`。
5. 区间内若含 `protected_messages` / `system` / 当前未闭合批次，返回 `None`。
6. 配对计数规则：`assistant.tool_calls` 每个 call +1，`tool` 每条 -1，其他 0；计数为 0 才是平衡边界。

- [ ] **Step 3: 跑测试并提交**

Run: `pytest tests/agent/test_compaction_region.py -q`
Expected: PASS

```bash
git commit -m "feat(agent): select compaction ranges on tool-pair boundaries"
```

---

### Task 4: 摘要产物校验与 checkpoint 形态

**Files:**
- Modify: `astrbot/core/agent/context/compressor.py`
- Test: `tests/agent/test_context_manager.py`、`tests/agent/test_compaction_retention.py`

**Interfaces:**
- Consumes: Task 2/3
- Produces: `LLMSummaryCompressor.__call__` 继续返回 `list[Message]`；新增属性 `last_shadowed_tokens`、`last_checkpoint_tokens`、`last_summary_tokens`

- [ ] **Step 1: 写失败测试**

```python
async def test_truncated_summary_is_rejected(monkeypatch):
    # provider 返回 finish_reason=length
    result = await compressor(messages)
    assert result is messages


async def test_summary_must_be_strictly_smaller():
    # checkpoint >= shadowed -> 返回原 messages


async def test_checkpoint_uses_preamble_and_tags():
    assert "<compacted-summary>" in result[0].content
    assert result[0].content.startswith("This is an automatically generated checkpoint")


async def test_full_history_checkpoint_is_one_user_message():
    assert sum(1 for m in result if m.compaction_id) == 1
    assert result[0].role == "user"
```

- [ ] **Step 2: 实现要点**

- 选区由 `ContextManager` 完成并作为参数传入：`compressor(messages, spec=spec, selected_range=selected)`。参数名不能叫 `range`，否则遮蔽内建 `range()`，破坏旧路径的循环。
- 新增 `_summary_finish_reason(response) -> str | None`：OpenAI `choices[0].finish_reason`、Anthropic `stop_reason`、Gemini `candidates[0].finish_reason`；无法识别时返回 `None`。
- `max_tokens = spec.max_summary_tokens`（默认 `min(headroom, 8192)`）写入摘要请求 `llm_params`，覆盖聊天请求的 `max_tokens`。
- 空文本、含图像、`MAX_TOKENS` → 返回原 `messages`，不写 `last_compression`。
- 计算 `shadowed_tokens`（选区）与 `checkpoint_tokens`（framed checkpoint）；`checkpoint >= shadowed * (1 - min_yield_ratio)` → 返回原 `messages`。`min_yield_ratio` 默认 0（dsh 行为），AstrBot 配置可调。
- checkpoint 统一为 `preamble + "\n\n<compacted-summary>\n" + summary + "\n</compacted-summary>"`；全文压缩只插入一条 user checkpoint（不再补 assistant ack），轮内压缩保留 assistant。
- 删除 `keep_recent_ratio` 的旧算法，改用 `select_compaction_range`。

- [ ] **Step 3: 跑测试并提交**

Run: `pytest tests/agent/test_context_manager.py tests/agent/test_compaction_retention.py -q`
Expected: PASS（原有用例按新签名更新）

```bash
git commit -m "feat(agent): validate compaction summaries before installing them"
```

---

### Task 5: ContextManager 编排、目标水位与配置

**Files:**
- Modify: `astrbot/core/agent/context/manager.py`、`context/config.py`
- Modify: `astrbot/builtin_stars/compact/main.py`（手动 `/compact` 走同一 spec/选区）
- Modify: `astrbot/core/config/agent_runner.py`、`astrbot/core/config/default.py`
- Modify: `astrbot/core/pipeline/.../internal.py`、`astrbot/core/astr_main_agent.py`、`astrbot/core/astr_agent_tool_exec.py`、`astrbot/core/utils/migra_helper.py`
- Test: `tests/agent/test_context_manager.py`、`tests/unit/test_agent_runner_config.py`、`tests/unit/test_cron_context_compression.py`

**Interfaces:**
- Consumes: Task 2/3/4
- Produces: `CompactionOutcome`、扩展后的 `last_compression`

- [ ] **Step 1: 写失败测试**

```python
async def test_target_water_level_reached_before_stopping():
    ...
    assert manager.token_counter.count_tokens(result) <= spec.target_tokens
    assert manager.last_compression["reason"] == "threshold"


async def test_retries_bounded_and_no_third_summary():
    ...
    assert provider.calls == 2
    assert manager.last_compression["insufficient"] is True


async def test_cooldown_prevents_immediate_recompaction():
    await manager.process(messages, output_token_budget=2000)
    before = provider.calls
    await manager.process(messages, output_token_budget=2000)
    assert provider.calls == before
```

- [ ] **Step 2: 实现编排**

```
process():
  spec = resolve_compaction_spec(...)
  if spec is None: 保持现状（只做容量校验）
  if tokens <= spec.threshold_tokens and not capacity_pressure: 返回
  if tokens < last_after + spec.min_growth_tokens: 返回（冷却）
  _run_compression(spec, reason="threshold"|"capacity")

_run_compression(spec):
  prune_tool_results（可提前返回）
  for pass in range(1 + spec.compaction_retries):
      range = select_compaction_range(...)
      if range is None:
          # automatic pressure: stop; capacity pressure: hard-drop / ContextCapacityError
          # model switch: runner 侧的容量校验会拒绝
          # manual /compact: 直接回复"没有可安全压缩的区间"，不发摘要请求
          break
      messages = compressor(messages)   # 失败则 break
      tokens = recount()
      if spec.target_tokens and tokens <= spec.target_tokens: break
      if tokens <= spec.threshold_tokens and not capacity_pressure: break
  if tokens > spec.threshold_tokens and capacity_pressure:
      drop_oldest_round 循环 → 仍超则 ContextCapacityError
  last_compression = {...}
```

- [ ] **Step 3: 配置与迁移**

- `ContextConfig` 增加 `threshold_ratio`、`headroom_tokens`、`retain_ratio`、`target_ratio`、`min_yield_ratio`；删除旧的 `llm_compress_keep_recent_ratio`。
- `/compact` 先 `resolve_compaction_spec()` 再 `select_compaction_range()`；`range is None` 时回复"当前上下文没有可安全压缩的区间"，不调用摘要模型；有区间时走同一目标水位循环。
- `ContextManager.__init__` 保留一个 compressor 原型（供既有测试/插件读取）；每次 `_run_compression` 把当次 `spec` 与 `range` 传给 `__call__`。`LLMSummaryCompressor` 构造参数删除 `keep_recent_ratio`；`TruncateByTurnsCompressor.should_compress()` 改用 `spec.threshold_tokens`，不再读 0.82。
- `agent_runner.resolve_context_compression_config()` 输出新字段；`internal.py` 读取 provider 级 `compress_threshold_ratio` / `compaction_headroom_tokens`。
- 迁移：`keep_recent_ratio` → `retain_ratio`；`compress_threshold_tokens` 保留；新键缺省用默认值。
- 面板：`keep_recent_ratio` 改名"压缩时保留最近原文比例"（0–0.3，默认 0.16）；新增"压缩目标占用比例"（0–0.8，默认 0.55）；provider 级新增"压缩 headroom"与"压缩触发比例"。

- [ ] **Step 4: 跑测试并提交**

Run: `pytest tests/agent/test_context_manager.py tests/unit/test_agent_runner_config.py tests/unit/test_cron_context_compression.py -q`
Expected: PASS（既有 background 失败按 HEAD 对照排除）

```bash
git commit -m "feat(agent): compact long-horizon turns toward a target water level"
```

---

### Task 6: 溢出恢复（P2，可独立回退）

> **状态（2026-11-08，elecvoid243）：决定不做。** 保留现有 provider 侧 `pop_record` 与预发送容量校验；只有在真实日志出现服务端 `maximum context length` 且现有兜底破坏工具配对时，才按本节实施。

**Files:**
- Modify: `astrbot/core/provider/provider.py`、`astrbot/core/provider/sources/openai_source.py`、`astrbot/core/agent/runners/tool_loop_agent_runner.py`、`astrbot/core/agent/context/manager.py`
- Test: `tests/test_tool_loop_context_recovery.py`、`tests/test_tool_loop_context_budget.py`

**Interfaces:**
- Produces: `ContextWindowExceededError`（`AstrBotError` 子类）；`ContextManager.force_compaction(messages, reason="overflow") -> list[Message]`

- [ ] **Step 1: 写失败测试**

```python
async def test_overflow_retries_once_after_forced_head_cut():
    # provider 第一次抛 ContextWindowExceededError，压缩后第二次成功
    assert provider.calls == 2
    assert report["reason"] == "overflow"


async def test_overflow_without_progress_preserves_original_error():
    ...
```

- [ ] **Step 2: 实现要点**

- OpenAI 的 `"maximum context length"` 分支不再静默 `pop_record`，改为抛 `ContextWindowExceededError`（仅 agent 请求路径；非 agent 调用保留原行为）。
- runner 捕获该错误，记录 `overflow_retries[agent]`；`< maxOverflowRetries` 时调 `force_compaction(retain_tokens=0, reason="overflow")`，消息必须变短才 retry，否则抛原错误。
- 成功响应后清零计数（对应 dsh 的 `assistant/message` 清零）。

- [ ] **Step 3: 跑测试并提交**

```bash
git commit -m "feat(agent): recover once from provider context overflow"
```

---

### Task 7: 观测、前端与文档

**Files:**
- Modify: `astrbot/core/agent/runners/tool_loop_agent_runner.py`（透传报告）
- Modify: `dashboard/src/composables/contextCompressionNotice.ts` 及 spec
- Modify: `dashboard/src/i18n/locales/*/features/config-metadata.json`
- Modify: `docs/zh|en/use/context-compress.md`、`docs/zh|en/dev/astrbot-config.md`
- Create: `changelogs/internal/2026-11-08-compaction-target-water-level.md`

**Interfaces:**
- Consumes: Task 5 的 `last_compression`
- Produces: 前端可解析并展示 `yield_ratio` / `passes` / `reason`

- [ ] **Step 1: 写失败测试**

```ts
it("parses compaction yield and passes", () => {
  const notice = parseContextCompressionNotice({
    strategy: "llm_compress", tokens_before: 16000, tokens_after: 9000,
    shadowed_tokens: 12000, checkpoint_tokens: 1800, yield_ratio: 0.85, passes: 1,
  });
  expect(notice?.yieldRatio).toBe(0.85);
  expect(notice?.passes).toBe(1);
});
```

- [ ] **Step 2: 实现**

- 后端 `_output_compression_report` 原样透传新字段。
- `ContextCompressionNotice` 增加可选字段；旧后端缺字段时不显示对应行。
- 圆环 tooltip 显示"上次压缩：16k → 9k · 摘要 · 收益 85% · 1 轮"。
- 四语文案与文档同步；文档写明"压缩只由 token 预算触发、目标水位、headroom"。

- [ ] **Step 3: 跑测试并提交**

Run: `cd dashboard && pnpm vitest run src/composables/contextCompressionNotice.spec.ts`
Expected: PASS

```bash
git commit -m "feat(chatui): expose compaction yield and pass count"
```

---

## Review Focus

1. **单轮只有一对巨大 tool call/result**：`select_compaction_range` 返回 `None`，不得发摘要请求、不得循环；按容量规则发送或报 `ContextCapacityError`。
2. **摘要被 `max-tokens` 截断**：必须整体失败，绝不能把残缺 checkpoint 写回历史；下一次请求仍使用原历史。
3. **`retain >= threshold` 的错误配置**：必须钳制并 warn，不能无限压缩或每次请求都压。
4. **受保护消息/系统注入落在选区内**：必须排除，宁可返回 `None`，不能丢未确认送达的输入。
5. **压缩后立即再次触发**：冷却与目标水位必须生效；同一 `process()` 内摘要调用不超过 `1+retries`。
6. **旧前端收到新字段**：解析必须容错，未知字段忽略，不能把整条通知丢弃。

## 风险与回滚

- 本改造是行为变更，不是文案：阈值、保留量、摘要长度都会变。建议按 Task 独立提交，Task 5 之后先在本机跑一轮长任务，再继续 Task 6/7。
- 回滚点：Task 2/3 是纯函数，可直接 revert；Task 5 的配置迁移保留旧值，revert 后 `keep_recent_ratio` 仍可用（迁移只写新键时需反向映射）。
- Task 6 改动 provider 错误路径，风险最高；若 provider 生态不支持 typed error，直接放弃该 Task，保留现有 `pop_record` 与预发送容量校验。

## Self-Review

- Spec coverage：阈值公式、选区、产物校验、重试、checkpoint 形态、溢出恢复、观测均有对应 Task。
- 命名一致性：`threshold_tokens` / `retain_tokens` / `target_tokens` / `shadowed_tokens` / `checkpoint_tokens` 全文一致。
- 未覆盖项：dsh 的 surface 事务日志与 `replaceGeneration` 明确不照搬，AstrBot 用消息快照与 `compaction_id` 等价替代。
