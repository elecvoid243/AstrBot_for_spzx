# LLM 请求注入痕迹（ChatUI 可见） — 设计文档

| 项目 | 内容 |
|------|------|
| 主题 | 插件经 LLM 运行时 hook（`on_llm_request`）对 `ProviderRequest` 的上下文注入，在 ChatUI 中按回合可见：消息左缘 gutter 静默标记 + 点击展开明细面板（B 方案） |
| 日期 | 2026-10-08 |
| 作者 | elecvoid243 |
| 状态 | Design — 视觉方案已评审（B 方案），待实现 |
| 关联代码 | `astrbot/core/pipeline/context_utils.py`（`call_event_hook` L78）<br>`astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py`（`OnLLMRequestEvent` 常规触发点 L465）<br>`astrbot/core/agent/runners/tool_loop_agent_runner.py`（`agent_stats` / `file_changes` 透出范式）<br>`astrbot/core/astr_agent_run_util.py`（webchat 分支 L196 / L205）<br>`astrbot/core/platform/sources/webchat/webchat_event.py`（`_send` 的 Json 分支、`_persist_bot_reply_if_orphan` L54）<br>`astrbot/dashboard/services/chat_service.py`（`build_bot_history_content` L279 / `save_bot_message` L1504 / `_consume_chat_run` L1892 / 累加器 L425）<br>`dashboard/src/composables/useMessages.ts`（主链路分发 L2013、历史归一化 L1278）<br>`dashboard/src/components/chat/ChatMessageList.vue` |
| 测试 | `tests/unit/test_llm_request_trace.py`、`tests/unit/test_agent_runner_injection_trace.py`、`tests/unit/test_chat_service_llm_request_injections.py`、`dashboard/src/composables/llmRequestInjections.spec.ts`、`dashboard/src/composables/__tests__/useMessagesLlmRequestInjections.spec.ts`、`dashboard/src/components/chat/ChatMessageList.injectionTrail.spec.ts`、`dashboard/src/i18n/i18n.completeness.spec.ts`（扩展） |

---

## 1. 背景与目标

插件的 `on_llm_request` hook 可以在请求装配阶段改写 `ProviderRequest`（追加系统提示、插入上下文消息、附加内容块、注入工具）。用户提出问题：**每次注入，ChatUI 能否检测到并显示？**

目标（三条）：

1. **可检测**：每次注入产生一条结构化痕迹，包含插件名、handler、字段与变化量；
2. **可归属**：痕迹必须指认到具体插件与 handler，而不是一个匿名的"上下文已变化"；
3. **可见而静默**：默认只有一个极轻的标记，点击才展开明细——注入是每轮都可能发生的高频事件，不能以"消息"或"toast"的强度呈现。

## 2. 机制现状（为什么现在检测不到）

- **注入是原地修改**：`call_event_hook(event, EventType.OnLLMRequestEvent, req)`（`context_utils.py:78`）逐个调用 handler，插件直接改 `req.system_prompt / contexts / extra_user_content_parts / func_tool`。`req` 既不落库也不带指纹，hook 宿主不产出任何差异信息。
- **触发频率（实现期核实）**：常规内部 agent 每轮**只触发一次**（`internal.py:465`，在 `collect_initial_request` 之后、agent runner 启动之前）；三方 runner（dify / coze）在 `third_party.py:325` 各触发一次。**因此注入痕迹是回合级数据，不存在"按步骤分组"**——评审预览稿中的 step 分组作废。
- **归属信息现成**：`call_event_hook` 遍历时持有 `handler.handler_module_path → star_map[...].name`（插件名）与 `handler_name`，目前仅打印 debug 日志。
- **可视化先例**：上下文压缩提示走 `context_compression` chain 的瞬时 chip；`agent_stats` / `file_changes` 是"结构化数据随 bot 记录持久化"的成熟范式，本次逐字照抄后者。

## 3. 关键设计决策

| 决策点 | 结论 | 理由 |
|---|---|---|
| 采集点 | `call_event_hook` 内，仅对 `OnLLMRequestEvent`；逐 handler 前后轻量指纹 diff | 唯一 choke point，且天然带归属信息；O(1) 指纹，不深拷贝 |
| 指纹 | `system_prompt (len, sha1)`；`contexts (len, sha1(末条前 4096 字符))`；`extra_user_content_parts` 条数；`func_tool` 名字元组 | 覆盖追加/截断/等长改写；对百 KB 级 prompt 成本可忽略 |
| 载体 | `event.set_extra("_llm_request_injections", items)` | 随事件对象走，按回合天然隔离，不新增存储 |
| 透出 | 新增 chain_type `llm_request_injections`，范式照抄 `agent_stats` | 已跑通的链路（runner → run_util → webchat_event → chat_service → SSE） |
| 持久化 | 随 bot 记录存档（`content["llm_request_injections"]`）；空则**不写字段** | 刷新后可回看；与 `agent_stats` 一致 |
| 非 ChatUI 传输 | OpenAPI / live / system-stream 三类消费端**显式忽略**该 chain_type | 防止 JSON 被当作纯文本渲染（JSON 文本气泡） |
| 前端形态 | A 版：消息左缘 20px 轨道 + 20px 描边图标钮 + 消息头部展开面板 | 评审后迭代（2026-10-08）：6px 静默点太小不易察觉；面板移入消息开头紧贴触发点 |
| 开关 | 不加 | 与 `agent_stats` / `context_compression` 一致（KISS）；采集成本在噪声级 |
| 预览与全文 | 折叠行 ≤80 字符 `preview`；另带 `full`（≤8192 字符，超限以 `…` 结尾）供面板就地展开 | 大 prompt 不走流；明细深度（system_prompt 分段、contexts 逐条）留待 Inspector |
| 颜色语义 | 追加 = 中性墨色；缩减 / 截断（lossy）= 琥珀，沿用 `compression-chip--lossy` 的 `--v-theme-warning` 用法 | 沿用既有语义，且文字（"已截断"）独立承载含义，不依赖颜色 |

## 4. 后端设计

### 4.1 采集模块 `astrbot/core/pipeline/llm_request_trace.py`（新建）

```python
EVENT_EXTRA_KEY = "_llm_request_injections"
PREVIEW_CHARS = 80

def snapshot(request: object) -> dict: ...
def collect_changes(request: object, before: dict, after: dict) -> list[dict]: ...
def record_injections(event: object, plugin: str, handler: str, changes: list[dict]) -> None: ...
def read_injections(event: object | None) -> list[dict]: ...
```

字段变化规则（`collect_changes`）：

| field | delta 语义 | preview 规则（折叠态） | full 规则（展开态） | lossy |
|---|---|---|---|---|
| `system_prompt` | 字符数差 | `delta > 0` 时取追加尾段前 80 字符；否则空串 | `delta > 0` 时取完整追加尾段，上限 8192 字符，超限以 `…` 结尾 | `delta < 0` |
| `contexts` | 条数差 | `delta > 0` 时取末条消息文本前 80 字符 | `delta > 0` 时按 `\n\n` 拼合全部新增条目文本 | `delta < 0` |
| `extra_user_content_parts` | 条数差 | `delta > 0` 时取最后一个 part 的文本前 80 字符 | `delta > 0` 时按 `\n\n` 拼合全部新增 part 文本 | `delta < 0` |
| `func_tool` | 工具数差 | 新增/移除的工具名（逗号连接，≤80） | 同左（完整名单） | `delta < 0` |

边界：字段指纹变化但长度不变（等长改写）→ `delta = 0`、`preview = ""`、`full = ""`、`lossy = False`，前端显示"已改写"且不可展开。

### 4.2 接入 `context_utils.call_event_hook`

在逐 handler 循环内：`tracing = hook_type is EventType.OnLLMRequestEvent and bool(args)`；调用前取 `snapshot(args[0])`，调用后 diff 并落库。**`star_map` 查询用 `.get()` 守卫**——循环外的查询一旦 KeyError 会打断整个 hook 派发（既有代码只把该查询放在 try 内）。

### 4.3 透出链路（照抄 `agent_stats`）

1. **runner**：`_injection_trace_response() -> AgentResponse | None`，在与 `agent_stats` yield 相同的位置（同一次非流式响应之后）产出 `type="llm_request_injections"` 的 `MessageChain`（内含一个 `Json`）。
2. **`astr_agent_run_util`**：新增分支，平台为 `webchat` 时 `await astr_event.send(resp.data["chain"])`。
3. **`webchat_event`**：`_send` 的 Json 分支自动携带 `chain_type`；同时把新 chain_type 加入 `_persist_bot_reply_if_orphan` 的跳过元组（孤儿回合不得把它存成 JSON 气泡）。
4. **`chat_service._consume_chat_run`**：新增早分支（紧邻 `file_changes`）：解析 → 存 `run.llm_request_injections` → `_publish_chat_run(run, {"type": "llm_request_injections", "data": ...})` → `continue`。
5. **持久化**：`build_bot_history_content` / `save_bot_message` 增加 `llm_request_injections` 参数；`_consume_chat_run` 的 flush 与 `_subscribe_chat_run` 的两处快照一并透传。

### 4.4 数据契约

wire（`webchat_event._send` 的 Json 分支）：

```json
{"type": "plain", "chain_type": "llm_request_injections",
 "data": "{\"items\": [{\"plugin\": \"tc_memory\", \"handler\": \"decorate_llm_req\", \"changes\": [{\"field\": \"system_prompt\", \"delta\": 412, \"lossy\": false, \"preview\": \"## 长期记忆 …\", \"full\": \"## 长期记忆\\n- 用户偏好：…\"}]}]}",
 "streaming": false, "message_id": "<run id>"}
```

SSE（`_publish_chat_run` 重新封装后）：`{"type": "llm_request_injections", "data": {"items": [...]}}`。

历史记录：`content["llm_request_injections"] = {"items": [...]}`（无注入时字段不存在）。

### 4.5 边界行为

| 场景 | 行为 |
|---|---|
| handler 抛错 | `call_event_hook` 既有语义：记日志、继续后续 handler；抛错 handler 不产生痕迹 |
| 无注入 | 无 extra、无 AgentResponse、无历史字段、无标记 |
| 非 webchat 平台 | 不发送（不落任何痕迹），与 `agent_stats` 相同 |
| 等长改写 | 记一条 `delta=0` 的痕迹，前端显示"已改写" |
| 系统流 / goal 孤儿回合 | 累加器与前端系统流**丢弃**该 chain_type（防泄漏），本期不渲染 |

## 5. 前端设计（A 版：描边图标钮 + 消息头部展开面板）

> 2026-10-08 迭代（用户反馈）：B 方案的 6px 静默点在实际使用中太小、不易察觉，改为 A 版描边图标钮；面板同时从消息**末尾**移到消息**开头**（紧贴轨道标记），避免长回复把披露内容与触发点分离。

### 5.1 三态视觉规格

| 状态 | 表现 |
|---|---|
| 静默（默认） | 20px 轨道内一个 20×20 描边圆角钮（1px、on-surface 0.22）+ 11px 线描图标（方框加号，0.55）；与消息顶部基线对齐；无注入时轨道留空 |
| hover | 描边加深至 0.34、图标 0.82、浅底 0.07；`v-tooltip` 出现："上下文注入 · {count} 项" |
| 展开 | 钮墨色填充、图标反白（`--v-theme-surface`）；面板在**消息开头**（内容列内、气泡之前）就地展开 |

### 5.2 面板内容

- 头部：标题（"本轮上下文注入"）+ 计数（"{count} 项"）+ 右侧备注（"插件在 LLM 请求时注入"）；
- 每个 handler 一条：插件名（加粗） + handler 名（右对齐灰字）；
- 每条内每个字段一行：字段名（等宽）+ 变化标签（`+412 字符` / `+2 条` / `+1 个工具` / `-1.2k 字符 · 已截断` / `已改写`）；
- 字段行是行级 disclosure：默认折叠只显示单行 `preview`（ellipsis 截断）；带 `full` 的行出现 chevron，点击就地展开为完整文本（`pre-wrap`、`max-height: 260px` 可滚动、可选中复制），再点收起；
- 面板整体紧随轨道标记、渲染在**消息开头**（气泡之前）；
- 预览块：等宽小字、单行截断（`preview` 非空时渲染）；lossy 行使用琥珀前景 + 琥珀浅底（展开块同样着琥珀色）；
- 移动端（≤560px）：轨道整体隐藏。摘要条内联计数（最初的方案 A）作为后续 fallback，**本期不实现**。

### 5.3 数据流（前端）

1. 历史加载：`normalizeHistoryRecord` 读 `content.llm_request_injections`（同时兼容 camelCase），写入 `ChatContent.llmRequestInjections`；
2. 实时：主链路 dispatch 处理 `type/chain_type === "llm_request_injections"`，`markMessageStarted` 后写入同一字段；
3. 系统流：内联**丢弃**（与 `context_compression` 的内联处理相邻），防止 JSON 落入纯文本兜底；
4. `ThreadPanel.vue` 归一化补一行字段透传（thread 视图复用 ChatMessageList）。

### 5.4 组件划分

- 新建 `LlmRequestInjectionsPanel.vue`（props：`items`；无 emits）——头部、条目、行级展开/折叠、lossy 样式；
- `ChatMessageList.vue`：新增轨道列、标记按钮与展开态（`injectionItems / injectionTrailOpen / toggleInjectionTrail`，键用 `msgIndex`，与既有"工作胶囊"展开态同一模式）；
- 纯逻辑抽到 `composables/llmRequestInjections.ts`：`parseLlmRequestInjections(data) -> LlmRequestInjections | null`、`injectionChangeLabel(change, tm) -> string`（复用 `formatTokenCount`）。

### 5.5 i18n（四语言齐备）

```json
"llmRequestInjections": {
  "tooltip": "上下文注入 · {count} 项",
  "title": "本轮上下文注入",
  "count": "{count} 项",
  "note": "插件在 LLM 请求时注入",
  "units": { "chars": "{delta} 字符", "items": "{delta} 条", "tools": "{delta} 个工具" },
  "lossySuffix": "已截断",
  "rewritten": "已改写"
}
```

## 6. 明确不做（Out of scope）

- 系统流 / goal 孤儿回合的渲染（仅做防泄漏丢弃）；
- 移动端 A 方案 fallback；
- Inspector：system_prompt 分段、contexts 逐条、工具集合的全量视图；
- 三方 runner（dify / coze）的透出；
- `on_agent_begin` / `on_llm_response` 等其它 hook 的间接上下文变化（不在 `req` 上体现）。

## 7. 验收

1. 后端：`pytest tests/unit/test_llm_request_trace.py tests/unit/test_agent_runner_injection_trace.py tests/unit/test_chat_service_llm_request_injections.py -v`；
2. 回归：`pytest tests/test_tool_loop_agent_runner.py tests/unit/test_system_notice_injection.py tests/unit/test_chat_service_file_changes.py -v`；
3. 前端：`cd dashboard && node ./node_modules/vitest/vitest.mjs run src/composables/llmRequestInjections.spec.ts src/composables/__tests__/useMessagesLlmRequestInjections.spec.ts src/components/chat/ChatMessageList.injectionTrail.spec.ts src/i18n/i18n.completeness.spec.ts`；
4. 手工验证：启用一个会注入 system_prompt 的插件（如 tc_memory）发送一条消息 → 回复完成后左缘出现标记 → hover 出提示 → 点击展开面板（插件名 / handler / `+N 字符` / 预览）→ 刷新页面标记仍在；无插件注入的回合轨道为空。
