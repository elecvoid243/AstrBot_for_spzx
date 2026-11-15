# Token Budget 上下文压缩（换窗策略）

> Author: elecvoid243 · 2026-11-15
> 状态：设计已确认，待实现
> 关联：[2026-10-31 增量上下文预算与工具调用配对](2026-10-31-context-budget-tool-pairing-spec.md) —— 预算计算与工具配对的唯一来源，本 spec 不重新定义它们

## 1. 背景

`agent_runner.config.compression.overflow_strategy` 目前只实现了 `llm_compress`。`token_budget` 是一个占位选项：选中后 `resolve_context_compression_config` 会把它改写回 `llm_compress` 并打一条 warning，UI 标签写着“尚未实现”。

本 spec 让 `token_budget` 成为真正可执行的第二策略，语义取自 codex 的 token budget 压缩：**不调用模型生成摘要，直接把历史替换成一个新窗口**。

codex 侧的参照实现：

- `codex-rs/core/src/compact_token_budget.rs`：换窗只跑 hooks 与 turn item，不做摘要
- `codex-rs/core/src/session/mod.rs::start_new_context_window`：重建初始上下文并替换历史
- `codex-rs/core/src/session/context_window.rs`：预算判定（scope、输出预留、硬窗口）
- `codex-rs/core/src/session/token_budget.rs::maybe_record`：窗口内两条一次性提示
- `codex-rs/core/src/state/auto_compact_window.rs`：窗口状态与幂等标记

## 2. 目标与非目标

目标：

1. 选中 `token_budget` 后，一次压缩**不再发起任何摘要请求**。
2. 超阈值时把模型可见历史裁剪为“初始上下文 + 当前轮”，当前轮完整、工具调用与结果不劈开。
3. 窗口内提供 codex 的两条一次性提示（阈值提醒、收尾提示），且每窗各一次。

非目标：

- 不实现 codex 的 world state、`TokenBudgetContext`（窗口 id 注入模型）。
- 不实现 remote compaction / memento。
- 不新增数据库表或会话列。
- 不修改 `keep_recent_ratio` 的 `[0, 0.3]` 钳制（换窗不读它）。
- 不改动 2026-10-31 spec 定义的阈值规则与工具配对规则。
- 不做“换窗历史”可视化；前端只增加策略名与有损归类。

## 3. 术语

| 术语 | 含义 |
|---|---|
| 窗口 | 一份模型可见历史的生命周期单位，由换窗动作开始 |
| 换窗 | 丢弃历史、只保留初始上下文与当前轮，并递增窗口序号 |
| 当前轮 | 最后一条真实用户请求及其后的 assistant/tool 批次 |
| 阈值提醒 | 剩余预算低于提醒阈值时注入的一次性提示 |
| 收尾提示 | 换窗后仍超出容量时注入的一次性提示 |
| 初始上下文 | 每条请求重建的 system 指令（`req.system_prompt` + leading system 消息） |

## 4. 已定决策

| 编号 | 决策 | 说明 |
|---|---|---|
| D1 | 换窗严格沿用 codex：只留初始上下文与当前轮，不保留最近历史 | 取代早期“按预算保留最近完整轮”的方案 |
| D2 | `token_budget` 与 `llm_compress` **共用同一套压缩配置** | 取代早期“两套命名空间 + config 迁移”的方案；零迁移 |
| D3 | 范围包含两条提示（阈值提醒 + 收尾提示） | 对齐 codex 的完整语义 |
| D4 | 提醒阈值可配，文案内置（四语常量） | 与现有“压缩提示词不提供配置项”的口径一致 |
| D5 | 窗口状态**随历史落库**（内部字段 + 反向扫描取最新） | 取代早期“进程内 registry”；消除状态与历史不同源 |
| D6 | 插入点为 `ContextConfig.strategy` + `TokenBudgetCompressor` | 公共流程（工具结果剪枝、容量复查、报告、兜底）全部保留 |
| D7 | `config_version` 不 bump，无配置迁移 | 占位键已在位，只删除“未实现”回落 |
| D8 | 新增唯一配置项：`provider.compaction_reminder_tokens`（默认 8192） | 仅 `token_budget` 读取 |
| D9 | WebChat 展示历史独立于 LLM 历史（现状依据，非新决策） | `platform_message_history` 与 `conversations.content` 分离，裁剪不会回滚聊天记录 |

## 5. 架构

| 层 | 位置 | 职责 |
|---|---|---|
| 配置 | `agent_runner.config.compression` | 策略选择 + 共用参数 |
| 解析 | `resolve_context_compression_config` | 策略与参数原样透出，不再改写 |
| 组装 | `MainAgentBuildConfig` → runner ctor → `ContextConfig` | 把策略带进请求期 |
| 执行 | `ContextManager` + `TokenBudgetCompressor` | 预算判定、裁剪、报告 |
| 报告 | `last_compression` → `context_compression` → chip | 策略名与前后 token |

职责边界：

- `TokenBudgetCompressor` 是纯函数：输入消息与 spec，输出裁剪后的消息；不读数据库、不认识 `umo`。
- 窗口状态的**读取与写回**在 runner 与保存路径；状态本身存在历史里（D5）。
- `ContextManager` 只负责选压缩机、判定与报告，不持有会话状态。

`ContextManager.__init__` 改前 / 改后：

```python
# 改前：custom → llm provider → 兜底截断，策略不可见
if config.custom_compressor: ...
elif config.llm_compress_provider: LLMSummaryCompressor(...)
else: TruncateByTurnsCompressor(...)

# 改后：custom → 策略 → llm provider → 兜底，策略是一等公民
if config.custom_compressor: ...
elif config.strategy == "token_budget": TokenBudgetCompressor(...)
elif config.llm_compress_provider: LLMSummaryCompressor(...)
else: TruncateByTurnsCompressor(...)
```

`_run_compression` 里对 `LLMSummaryCompressor` 的 `isinstance` 判断改为能力位 `compressor.supports_retries`（`LLMSummaryCompressor.supports_retries = True`，其余为 `False`），避免每加一种策略就多一个 `isinstance`。

数据流（一次超预算请求）：

`resolve` → runner ctor(`strategy`) → `ContextConfig` → `process()` 判定 → `TokenBudgetCompressor` 裁剪 → `last_compression` → runner 依据报告更新窗口状态并决定是否注入提示 → 写回历史时随消息落库。

## 6. 配置

两策略共用一套配置，各自消费需要的子集：

| 键 | 所有权 | `llm_compress` | `token_budget` |
|---|---|---|---|
| `compression.overflow_strategy` | 选择器 | ✓ | ✓ |
| `compression.keep_recent_ratio` | 策略参数 | ✓ 摘要保留尾巴 | ✗ |
| `compression.provider_id` | 策略参数 | ✓ 摘要模型 | ✗ |
| `compression.fallback_max_tokens` | 窗口兜底 | ✓ | ✓ |
| `provider.max_context_tokens` | 窗口事实 | ✓ | ✓ |
| `provider.compress_threshold_tokens` / `_ratio` | 触发阈值 | ✓ | ✓ |
| `provider.compaction_headroom_tokens` | 触发点上方余量 | ✓ 摘要工作区 | ✓ 收尾余量 |
| `provider.compaction_reminder_tokens` | 提醒阈值（新增） | ✗ | ✓ |

自洽校验分两处，因为两侧的事实来源不同：

| 位置 | 规则 | 不满足时 |
|---|---|---|
| `resolve_context_compression_config` | 策略不在已实现集合内 | 回落 `llm_compress` + warning |
| `resolve_context_compression_config` | `keep_recent_ratio` 越界 | 钳制到 `[0, 0.3]`（现状） |
| runner 构造 `ContextConfig` | `compaction_reminder_tokens <= 0` | 回落 8192 + warning |
| runner 构造 `ContextConfig` | `max_context_tokens <= 0` | `token_budget` 不可用，回落 `llm_compress` + warning |

零迁移：占位键、字段名、默认值都不动，`config_version` 保持 4。删除 `PLANNED_OVERFLOW_STRATEGIES` 与“未实现”回落分支。

## 7. 行为规格

### 7.1 触发

完全复用 `resolve_compaction_spec`：`threshold = min(window × ratio, window − output_budget − headroom)`，绝对阈值覆盖比例阈值。`token_budget` 与 `llm_compress` 的差异只在触发后的动作，不改阈值规则。

供提示使用的剩余量：

```python
remaining = max(
    0,
    min(
        spec.threshold_tokens - total_tokens,
        (
            spec.context_window - total_tokens - output_token_budget
            if spec.context_window > 0
            else spec.threshold_tokens
        ),
    ),
)
```

### 7.2 换窗

```python
class TokenBudgetCompressor:
    strategy = "token_budget"
    supports_retries = False   # 换窗一次到位，重试无意义

    def __init__(self, token_counter: TokenCounter) -> None:
        self.token_counter = token_counter
        self.last_shadowed_tokens = 0

    async def __call__(self, messages, func_tool=None, spec=None, **_):
        before = self.token_counter.count_tokens(messages)
        result = ContextTruncator().truncate_by_token_budget(
            messages, budget_tokens=0, token_counter=self.token_counter
        )
        self.last_shadowed_tokens = before - self.token_counter.count_tokens(result)
        return result
```

`budget_tokens=0` 的语义即“只保留 leading system 与最新一轮”；轮粒度保证 assistant `tool_calls` 与其 `tool` 结果整批保留或整批丢弃，不会劈开配对。

### 7.3 窗口状态（随历史落库）

状态结构：

```python
class WindowState(BaseModel):
    id: str            # uuid4 hex，窗口身份
    seq: int           # 递增序号，0 = 从未换窗
    reminder_sent: bool
    exhausted_sent: bool
```

`Message` 新增内部字段：

```python
window: WindowState | None = Field(default=None, alias="_window", exclude=True)
```

读写规则：

- **起点**：一次 run 开始时从 `run_context.messages`（已加载的历史）反向扫描一次，得到当前状态；run 期间只改内存副本。
- **写**：每轮保存历史前，把当前 `WindowState` 盖在“最后一条会落库的消息”上（跳过 leading system、`_internal` 消息）。随 `model_dump_for_context` 一起进入 `conversations.content`。
- **读**：加载历史后从后往前扫描，取最新 `_window`；没有则视为 `seq=0` 的新窗口。
- **推进**：一次换窗后 `seq += 1`、`id` 重新生成、两个 `sent` 复位。
- **一致性**：状态只在历史真正保存时前进；本轮被中断而未写回时，下一轮读到的仍是旧状态，不会出现“状态说已换窗、历史没换”的错位。

不新增数据库列、不做迁移；旧历史缺该字段时按 `seq=0` 处理。

### 7.4 两条提示

| 提示 | 触发条件 | 幂等 |
|---|---|---|
| 阈值提醒 | `remaining <= compaction_reminder_tokens` 且 `reminder_sent == False` | 置 `reminder_sent` |
| 收尾提示 | 换窗后仍高于触发阈值 / 目标（`insufficient == True`）且 `exhausted_sent == False` | 置 `exhausted_sent` |

收尾提示的注入位置在 `ContextManager.process()` **返回之后**，因此它只在“请求仍可发送”的前提下出现：若换窗后连窗口都放不下，`process()` 直接抛 `ContextCapacityError`，请求不会发出，也就没有“提示发不出去”的分支。典型触发场景是当前轮自身很大、但仍在窗口内，换窗腾不出足够余量。

注入形态：追加在 `messages` 尾部的一条 `Message(role="user", ...)`，带 `internal=True`（新增字段 `Field(default=False, alias="_internal", exclude=True)`）。

`internal` 的三个消费者：

1. `select_compaction_range` 选择“当前真实请求”时跳过它；
2. `_save_to_history` 跳过它（不落库、不显示在历史）；
3. 模型侧照常可见（这正是提示的目的）。

文案（内置常量，四语各一份）：

```
zh-CN  [context budget] 本窗口剩余 {n} tokens。请优先收尾当前任务；如需继续，
       先给出结论，再拆到下一轮。
zh-CN  [context budget] 上下文已达上限且无法再裁剪。请直接给出当前结论，
       不要再发起新的工具调用。
en-US  [context budget] {n} tokens left in this window. Wrap up the current task
       first; if more work is needed, give a conclusion and continue next turn.
en-US  [context budget] The context is at its limit and cannot be trimmed further.
       Give your current conclusion without starting new tool calls.
```

`ru-RU` / `ja-JP` 按同义翻译；`{n}` 为原始整数。

### 7.5 不可解的容量

当前轮自身即超出窗口时，换窗返回原列表（无收益），`ContextManager` 的容量复查与 `drop_oldest_round` 兜底照常执行；仍不满足则抛 `ContextCapacityError`。保持现有明确报错，不静默降级。

### 7.6 报告

`last_compression` 字段集不变，只增加取值 `strategy="token_budget"`；`tokens_before` / `tokens_after` / `insufficient` / `reason` 与 `llm_compress` 同构。前端无需改协议。

## 8. 接口变更清单

| 文件 | 改动 | 原因 |
|---|---|---|
| `astrbot/core/agent/message.py` | 新增 `WindowState`；`Message` 增 `window` / `internal` 内部字段；`model_dump_for_context` 附带 `_window` | D5 状态落库、7.4 提示标记 |
| `astrbot/core/agent/context/config.py` | `ContextConfig.strategy: str = "llm_compress"` | D6 |
| `astrbot/core/agent/context/compressor.py` | 新增 `TokenBudgetCompressor`；补 `supports_retries` 能力位 | 7.2、5 |
| `astrbot/core/agent/context/manager.py` | 策略分支；`isinstance` → 能力位；计算并报告 `remaining`；选压缩机时传 strategy | D6 |
| `astrbot/core/agent/context/compaction_region.py` | 跳过 `internal` 消息 | 7.4 |
| `astrbot/core/agent/runners/tool_loop_agent_runner.py` | ctor 接收 strategy；注入两条提示；读/推进窗口状态；把状态交给保存路径 | 7.3、7.4 |
| `astrbot/core/astr_main_agent.py` | 把 strategy 传入 runner；`_get_compress_provider` 行为不变 | 1 |
| `astrbot/core/config/agent_runner.py` | 删除 `PLANNED_OVERFLOW_STRATEGIES` 与“未实现”回落；透出 strategy；normalize 保留 `token_budget` | 6 |
| `astrbot/core/config/default.py` | 更新 `overflow_strategy` 的 label/hint；新增 `compaction_reminder_tokens` 元数据 | 6 |
| `astrbot/core/astr_agent_tool_exec.py` | 子代理按策略传参，不再只判断 `llm_compress` | 子代理一致性 |
| `astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py` | 保存历史前把窗口状态交给 `_save_to_history` | 7.3 |
| `dashboard/src/composables/contextCompressionNotice.ts` | `STRATEGY_KEYS.token_budget`；加入 `LOSSY_STRATEGIES` | 7.6 |
| `dashboard/src/i18n/locales/*/features/chat.json` | `contextCompression.strategy.token_budget` 四语 | 7.6 |
| `dashboard/src/i18n/locales/*/features/config-metadata.json` | 新键与改后 label/hint 四语 | 6 |
| `docs/{zh,en}/use/context-compress.md` | 用真实语义替换“占位”段落 | 文档如实 |

## 9. 兼容与回滚

- 策略可随时切回 `llm_compress`；历史里的 `_window` 字段对被忽略，不影响读取。
- 旧历史无 `_window`：按 `seq=0` 处理，行为等于“从未换窗”。
- 回滚方式：改回 `overflow_strategy`，无需迁移或清理数据。
- 风险面：动的是压缩实现与提示注入（行为），不动配置结构（结构）；历史内部字段属于新增可选字段，导出/迁移路径需回归。

## 10. 测试计划

单元：

1. `resolve_compaction_spec` 阈值不随策略变化（回归）。
2. `TokenBudgetCompressor` 只保留 system 与最新一轮；tool 配对完整。
3. 单轮超窗时抛出 `ContextCapacityError`。
4. `WindowState` 写回 / 反向扫描 / 缺字段默认值。
5. 两条提示各只注入一次；`internal` 消息被选择器与保存路径跳过。
6. `resolve_context_compression_config` 校验与回落。

集成：

7. 连续请求触发换窗：chip 报告 `token_budget`，DB `conversations.content` 只剩当前轮，`platform_message_history` 完整。
8. 中断后未写回：下一轮读到的窗口状态未推进。
9. 子代理路径选择 `token_budget` 时同样不发起摘要请求。

前端：

10. `token_budget` 归入有损策略并显示正确策略名（四语 completeness 测试）。

## 11. 实施顺序

1. `WindowState` + `Message` 内部字段 + dump/读取（D5 的地基）。
2. `ContextConfig.strategy` + `TokenBudgetCompressor` + manager 分支 + 能力位。
3. 配置解析与 normalize 收口（删占位回落、新增提醒阈值）。
4. runner 注入两条提示 + 窗口状态读/推进/交接保存。
5. 前端策略名与有损归类 + 四语文案 + 文档。
6. 测试补齐与回归（含 2026-10-31 spec 的预算与配对用例）。
