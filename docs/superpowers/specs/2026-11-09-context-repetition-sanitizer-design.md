# 上下文消毒：折叠历史中的重复推理

## 问题陈述

推理模型在长任务中会出现解码退化：同一条 `reasoning_content` 内形成稳定的重复循环，并把该循环作为历史再次发送给模型，形成自我强化。2026-11-09 的 `D40分选简化版C迁移方案设计` 会话提供了可复现的真实样本：

- 消息 2503：思维链 28,231 字符、1,741 个非空行，其中 93.2% 是重复行，`Let me go.` 出现 905 次。
- 消息 2314：思维链 108,654 字符、7,195 个非空行，96.9% 是重复行，`Let me go.` 出现 3,497 次；可见正文只有 508 字。
- 形态是 4 行极限环：`OK.` / `Let me write.` / `Let me go.` / `Let me go.`，周期 4 的行匹配率约 55%。
- `/compact`（373 → 111 条消息）之后，2507 / 2509 / 2512 的重复行占比仍为 81.5% / 85.5% / 81.4%。

AstrBot 当前没有任何输出重复检测。流式路径只把 `reasoning_content` 拼接到 `pending_reasoning`，随后包成 `ThinkPart` 写入 `run_context.messages`；下一轮请求时 `ThinkPart` 又被转回 `reasoning_content` 发给模型。退化推理因此被完整保留、完整回灌，只会被 `max_tokens`、Provider 超时或用户手动停止截断。

本设计只解决“历史中的重复推理不再回灌”，不解决生成过程中的实时中断。

## 解决方案

引入一个无 I/O 的纯函数消毒器，并在三个位置调用：

1. **写入点（A）**：构造 `ThinkPart` 之前消毒，使本轮后续步骤立即看不到重复内容。
2. **落库点（C）**：`_save_to_history()` 整份重写 conversation 之前消毒，使旧会话的脏历史在下一次保存时被惰性清洗。
3. **读取点（D）**：ChatUI 返回 `platform_message_history` 之前折叠 `think`，库中保留原文并随响应附带，供用户展开。

消毒范围仅限 assistant 历史中的 `ThinkPart`。可见正文、工具结果和 `result_chain` 均不改变。重复块保留第一遍，并追加一行英文标记。模型上下文（`conversations`）在写入时消毒；ChatUI 历史（`platform_message_history`）保持原始，但在读取时按同一规则折叠，并把原文附在响应里供用户展开。

## 用户故事

1. 作为用户，我希望历史中的退化推理不会再次进入模型请求，以便模型不再被自己的循环继续带偏。
2. 作为用户，我希望同一个 run 内后续步骤看不到前一步产生的重复推理，以便循环在当轮就被切断。
3. 作为用户，我希望旧会话不需要手动清理或迁移，以便下一次继续对话时自动恢复干净上下文。
4. 作为用户，我希望正常的代码块、Markdown 表格、构建日志和数学推导不被改写，以便消毒不会损失有效信息。
5. 作为用户，我希望带签名或加密状态的推理不被修改，以便 Anthropic、Gemini、OpenAI Responses 等 Provider 不会因签名失效而报错。
6. 作为用户，我希望 ChatUI 的思考面板默认显示消毒版，但可以展开查看原始内容，以便需要时还能诊断模型退化。
7. 作为维护者，我希望消毒逻辑是一个可独立单测的纯函数，以便不依赖 runner 或数据库即可验证判定正确性。
8. 作为维护者，我希望可以按 Provider 关闭该机制，以便遇到特殊推理格式时有逃生口。
9. 作为维护者，我希望不新增数据库字段、不写迁移脚本，以便改动范围收敛在三个调用点和两个展示组件。
10. 作为维护者，我希望日志能报告折叠了多少块、多少行，以便线上判断机制是否生效。
11. 作为用户，我希望刷新 ChatUI 后默认看到折叠后的思考内容，以便历史不再被几百行重复文本淹没。
12. 作为用户，我希望对某条被折叠的思考点“查看原文”时能看到完整原始内容，以便需要诊断时不被消毒器挡住。

## 实现决策

### 责任与现有接口

新增 `astrbot/core/agent/context/repetition.py`，只包含纯函数与数据结构：

- `RepetitionSanitizeConfig`：冻结 dataclass，字段见“配置与默认值”。
- `SanitizeResult`：`text`、`collapsed_blocks`、`collapsed_lines`、`original_chars`、`changed`。`collapsed_blocks` 是被移除的重复块次数，`collapsed_lines` 是原始与结果的非空行数之差（净减少）。
- `sanitize_repetitive_text(text, cfg) -> SanitizeResult`：核心检测与折叠。
- `sanitize_think_part(part, cfg) -> ThinkPart`：`encrypted` 非空时原样返回，否则替换 `think`。
- `sanitize_message_think_parts(messages, cfg) -> list[Message]`：返回新列表，不修改入参对象。
- `sanitize_history_think_parts(history_data, cfg) -> None`：就地处理 ChatUI 读路径的 bot 记录；被折叠的 `think` 会附加 `think_original`、`think_sanitized`、`think_collapsed_lines`。

调用点：

- **A1** `astrbot/core/agent/runners/tool_loop_agent_runner.py` 的 `_complete_with_assistant_response()`，在构造 `ThinkPart` 前调用。
- **A2** 同一文件的工具调用分支，在构造 `ThinkPart` 前调用。
- **C** `astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py` 的 `_save_to_history()`，在 `dump_messages_with_checkpoints()` 之前调用。
- **D** `astrbot/dashboard/services/chat_service.py` 的 `_sanitize_history_bot_records()`，在返回 ChatUI 历史前调用 `sanitize_history_think_parts()`；该函数已被 `get_session()`、`get_history_before()` 和侧边会话历史三条读路径共用。

`ToolLoopAgentRunner` 新增一个只读方法 `get_repetition_sanitize_config()`，从 `self.provider.provider_config.get("context_sanitizer")` 解析配置；缺省时返回默认配置。`_save_to_history()` 新增可选参数 `repetition_cfg`（`None` 时使用默认配置），由两个调用点从 `agent_runner.get_repetition_sanitize_config()` 传入。不新增 runner 构造参数，不改 `MainAgentBuildConfig`。

### 消毒范围与触发时机

- 只处理 `role == "assistant"` 消息中的 `ThinkPart`；不处理 `system`、`user`、`tool`，不处理 `TextPart` 或工具结果。
- A 在消息进入 `run_context.messages` 之前生效，因此 `ContextManager.process()`、压缩器、摘要器和 Provider 看到的都已是消毒后的内容。
- C 处理的是 `agent_runner.run_context.messages`。该列表同时包含本轮新消息和从 conversation 读回的旧消息；`_save_to_history()` 本来就是整份 history 重写，所以 C 不需要单独的迁移脚本。
- `LLMResponse.result_chain` 不改动。ChatUI 实时思考流来自 Provider 的 `reasoning_delta` 事件（由 `BotMessageAccumulator` 累加），仍是原始内容；刷新后来自 `platform_message_history`，读取时由 D 折叠。Dashboard 的“对话数据”预览读取 conversation history，会显示 C 消毒后的内容。
- 不改变消息角色、`tool_calls`、`tool_call_id` 或 `ThinkPart.encrypted`，不参与 `ContextTruncator.fix_messages()` 的配对判断。

### ChatUI 读路径与展示

`platform_message_history` 落库内容保持原始，D 只在读取时生成展示视图：

- 对每条 `type == "bot"` 记录里的 `think` 片段调用 `sanitize_repetitive_text()`；`encrypted` 非空的片段直接跳过，不折叠、不附加原文。
- 未发生折叠的片段原样返回，不附加额外字段。
- 发生折叠时：`think` 替换为折叠文本；保留 `think_original`（原文）；附加 `think_sanitized: true` 和 `think_collapsed_lines`。
- `get_message_markers()` 的 `reply_snippet` 本来就排除 reasoning，不受影响；删除、导出、附件提取等非展示路径不调用 D。

前端默认渲染 `think`（消毒版），并隐藏后端保留的第一遍块，只显示折叠标记；模型上下文仍保留第一遍作为锚点。`ReasoningTimeline.vue`（`ReasoningBlock` 展开后复用它）对 `think_sanitized` 的片段显示“已折叠 x 次重复”控制；展开后渲染 `think_original`，再次点击回到消毒版。第一版不新增全局设置项：“默认打开”指默认渲染消毒版，用户通过单条展开查看原文。新增文案键 `reasoning.showOriginal` / `reasoning.showSanitized`，补齐四个 locale。

实时流没有折叠：检测到周期块时前面的内容已经推送给前端，回滚或缓冲的复杂度不划算。因此同一轮对话在刷新前显示原始、刷新后显示消毒版；这是有意的行为差异，并在“查看原文”里保留完整证据。

### 检测与折叠规则

**第 0 步：守卫**。任一条件命中时直接返回原文：

- `cfg.enabled` 为 false。
- `part.encrypted` 非空。
- 文本长度小于 `min_chars`，或非空行数小于 `min_lines`。
- 行数超过 `max_scan_lines` 时跳过主导块扫描，只执行第 3 步的连续行折叠和第 4 步的重复率兜底，保证 CPU 有界。

**第 1 步：按行归一化**。使用 `splitlines()` 生成比较键（`line.strip()`），重建时使用 `splitlines(keepends=True)` 保留原始行尾；折叠时只删除整行，不改写保留行的内容。

**第 2 步：主导重复块检测**。对 `k` 从 `min_period` 到 `max_period`（含两端），用滑动窗口统计每个 `k` 行块的出现位置：

```text
positions[block] = [start, start + 1, ...]
```

对每个出现次数不少于 `min_repeats` 的块，先计算它的最小周期；最小周期小于 `min_period` 的块跳过，避免把 1–2 行的列表项/键值对当成退化循环。然后按起点贪心选择一组互不重叠的出现位置；若这些位置的公共步长大于 `max_period`，说明命中的是更长循环的子窗口（例如 9 行循环的 8 行窗口），跳过。候选的得分是 `(出现次数 - 1) * k`，即删除的行数。取得分最高的块，保留第一次出现，删除其余出现，并在第一次出现后插入标记；真实思考行即使夹在循环之间也会原样保留。随后对结果再跑一轮，最多 16 轮，直到没有新的主导块。

这里不用“连续重复块”是有实测依据的：D40 的 2503 中真实思考会不断打断 4 行循环，连续检测只能折叠 304/1,741 行，`Let me go.` 仍有 829 次；主导块检测把同一循环的所有出现都折叠，2503 从 1,741 行降到 400 行、`Let me go.` 从 905 降到 116，2314 从 7,195 行降到 390 行、`Let me go.` 从 3,497 降到 94，同时两边的唯一有效行 129 / 239 条全部保留。剩余 `Let me go.` 都是孤立行，不再是重复块；按“不做行级去重”的约束保留。

**第 3 步：连续相同行折叠**。块删除会把原本被真实思考隔开的相同行拼到一起。折叠前先记录原文中已经存在的 ≥ `min_repeats` 连续相同行（按行文本），这些行保持原样；只对**块删除后新拼出**的连续相同且非空的段，若长度不少于 `min_repeats`，保留第一行并追加标记：

```text
[context sanitizer: collapsed 38 x repeated line]
```

这样既不会误删原文中合法的代码、表格或清单重复行，也能清掉块删除拼出的伪重复段；孤立行始终不处理。

**第 4 步：非连续重复兜底**。块折叠后重新统计：若非空行数仍不少于 `min_lines`、剩余唯一行不超过 8 行、且重复行占比超过 `fallback_dup_ratio`，把整条文本替换为一条占位标记。唯一行上限是防止兜底误删仍然包含有效推理的长消息。

**第 5 步：折叠表示**。保留第一遍块，后面追加一行标记；标记中的次数是该轮被删除的重复遍数。多轮命中时会有多条标记：

```text
[context sanitizer: collapsed 900 x 4-line repeated block]
```

兜底标记；其中的行数和字符数是折叠前的原始统计：

```text
[context sanitizer: collapsed repetitive reasoning (7195 lines, 108654 chars)]
```

标记为英文单行，不包含被折叠的内容，因此不会再次构成重复块；消毒器对自身输出幂等。

### 配置与默认值

配置位于 Provider 配置的 `context_sanitizer` 字段；字段缺省时使用下列默认值并默认生效：

```json
{
  "enabled": true,
  "min_period": 3,
  "max_period": 8,
  "min_repeats": 8,
  "min_lines": 40,
  "min_chars": 512,
  "max_scan_lines": 20000,
  "fallback_dup_ratio": 0.8
}
```

- `enabled: false` 同时跳过 A 和 C；D 是展示层行为，不读取该 Provider 开关，始终生成折叠视图。
- 不新增全局开关；默认开启，按 Provider 关闭。
- 不新增 Dashboard 路由；历史响应只新增动态字段，不改变 OpenAPI 类型定义；不新增数据库字段。

### 加密推理与兼容边界

`ThinkPart.encrypted` 承载 Provider 的签名或加密推理状态：Anthropic 的 `signature`、Gemini 的 `thought_signature`、OpenAI Responses 的加密推理项。只要 `encrypted` 非空就完全跳过消毒，避免修改文本导致签名失效或请求被拒绝。

DeepSeek v4、MiMo 等要求 assistant 历史回传 `reasoning_content` 的模型不受影响：字段仍然存在，只是内容被折叠。若某个 Provider 要求逐字回传推理，使用 `enabled: false` 单独关闭。

### 日志与可观测性

- `sanitize_repetitive_text()` 永不抛异常；内部异常返回原文并记录一次 `warning`。
- 折叠行数不少于 200 时记录 `info`，否则记录 `debug`，内容包括块数、行数、原始字符数和结果字符数。
- C 只在确有消息被改写时记录一次 `info`，避免每轮刷屏。
- 不新增统计表、不修改 `AgentStats`、不新增前端提示。

## 测试决策

### 主验收 seam

主 seam 是纯函数 `sanitize_repetitive_text()`：输入文本和配置，输出文本与统计，不依赖 Provider、runner、数据库或时钟。所有判定规则在该 seam 上验证；两个调用点只做集成验证。

### 行为验收

**单元测试** `tests/agent/test_repetition_sanitizer.py`：

- 周期块：4 行块重复 100 次，保留 1 遍并追加标记。
- 幂等：连续执行两次，第二次输出与第一次一致。
- `encrypted` 非空时原样返回。
- `enabled: false` 时原样返回。
- 短文本、非空行不足时原样返回。
- 周期边界：`k=2` 不折叠，`k=9` 不折叠。
- 非连续重复触发兜底。
- 行数超过 `max_scan_lines` 时只跑兜底且不超时。

**对照语料（必须逐字节不变）**：

- 含重复 `}` 或 `break;` 的 C 代码块。
- Markdown 表格。
- 构建日志（相似但非整行相同的行）。
- 数学推导中的重复符号。

**真实数据回放** `tests/fixtures/context_sanitizer/d40_think.json`：

- 从 `platform_message_history` 导出消息 2503、2507、2509、2512、2314 的 `think` 内容。
- 2503：`Let me go.` 从 905 次降到不超过 120 次；净减少非空行不少于 1,300 行；折叠后不存在 8 行及以上的连续相同行；所有唯一非填充行保留。
- 2314：`Let me go.` 从 3,497 次降到不超过 100 次；净减少非空行不少于 6,800 行；折叠后不存在 8 行及以上的连续相同行；所有唯一非填充行保留。
- 折叠结果仍能通过 `ContextTruncator.fix_messages()`。

**集成测试**：

- `_complete_with_assistant_response()` 产出的 `ThinkPart` 已消毒。
- 工具调用分支产出的 `ThinkPart` 已消毒。
- `_save_to_history()` 能把 conversation 中已有的脏 `ThinkPart` 整份清洗。
- Provider 配置 `enabled: false` 时 A 和 C 都不生效。
- ChatUI 落库路径不受影响：`save_bot_message()` 收到的 `message_parts` 仍包含原始 `think`。
- `get_session()` / `get_history_before()` / 侧边会话历史的响应中，`think` 是折叠版，且 `think_original` 等于库中原文。
- 未发生折叠的历史记录不出现 `think_original` / `think_sanitized` 字段。

**前端测试**：

- `ReasoningTimeline` 默认渲染 `think`，不渲染 `think_original`。
- 点击“查看原文”后渲染 `think_original`，再次点击恢复消毒版。
- 没有 `think_sanitized` 的片段不显示该控制。

**性能**：7,195 行输入在 CI 上低于 50 ms；仅记录实测值，不作为门禁。读路径按消息逐个处理，命中折叠的大消息会增加一次序列化开销；第一版不做缓存，实测后再决定是否按内容哈希缓存。

### 测试原则

- 真实数据优先：以 D40 会话的实际 `think` 作为基线，不用合成样本替代主验收。
- 对照语料必须逐字节比较，证明“能治”之外还“不乱治”。
- 不在纯函数 seam 上 mock；集成测试使用现有 fake Provider / runner harness。

## 范围外

- 不处理 assistant 可见正文与工具结果中的重复。
- 不做流式生成过程中的实时中断或重试。
- 不修改 ChatUI 的实时思考流；实时仍是原始内容。
- 不修改 `platform_message_history` 的落库内容；折叠只发生在读取时。
- 不新增 ChatUI 全局设置项；第一版只提供单条“查看原文”。
- 不写历史数据迁移脚本；C 惰性清洗。
- 不调整 Provider 的 `frequency_penalty`、`presence_penalty` 或 vLLM 的 `repetition_penalty`。
- 不改变上下文预算、压缩策略或 token 计数规则。

## 进一步说明

- **为什么不做行级去重**：行级去重会把 CoT 中合法的代码、表格、清单行也计入，误伤面大；主导块只动“同一 k 行块出现 8 次以上”的那一组，且保留第一次出现。唯一的例外是块删除后**新拼出**的连续相同行：先记录原文中已有的 ≥ `min_repeats` 连续段并保持原样，只折叠新拼出的连续段，不碰孤立行，因此不会误删代码/表格中合法的重复行。
- **为什么保留第一遍**：第一遍通常包含真实意图（例如“准备写工具调用”），全部删除会让模型丢失“我本来要做什么”的锚点。
- **为什么插标记**：标记是“这里发生过退化”的信号，能让模型主动避开同一模式；完全不留痕迹可能让模型把缺失归因成“还没做”，继续补写。
- **为什么不消毒 `platform_message_history` 的落库内容**：该表是用户可见证据与诊断材料，不是模型上下文来源；模型上下文来自 conversation / `run_context.messages`。落库保持原始，读取时再折叠，才能事后验证消毒器是否误判。
- **为什么 ChatUI 实时不折叠、刷新才折叠**：实时内容来自 `reasoning_delta` 的逐块推送，检测到周期块时前面的内容已经发出；要折叠就得缓冲或回滚，复杂度和延迟都不划算。刷新走的是完整历史，可以一次性折叠，并通过 `think_original` 保留原文。
- **为什么原文附在响应里而不是另开接口**：只在发生折叠的片段上附带原文，未命中的历史没有额外体积；大消息会增加一次响应体积，但换取免二次请求的即时展开，也不会打乱滚动位置。
- **已知限制**：第一版不识别三反引号代码围栏；若对照语料出现误伤，再增加围栏感知，不提前引入复杂度。
