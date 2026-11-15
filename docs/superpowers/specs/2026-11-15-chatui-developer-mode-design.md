# ChatUI 开发者模式：LLM 请求快照

> Author: elecvoid243 · 2026-11-15
> 状态：设计已确认，待实现
> 关联：注入轨迹沿用 `astrbot/core/pipeline/llm_request_trace.py`；前缀指纹沿用 `astrbot/core/utils/prefix_diagnostics.py`；压缩报告沿用 `ContextManager.last_compression`；不新增数据库表

## 0. 背景与问题

ChatUI 目前能看到最终回复、工具调用、压缩提示和插件注入摘要，但看不到**实际发往 provider 的完整请求**。排查“模型为什么这样回”时，需要同时回答：

- system prompt 的实际文本与来源；
- 模型看到的工具定义与 schema；
- 完整消息序列（含 tool_call、tool result、注入提醒）；
- 本次请求的参数、端点、模型与尝试次数；
- 响应侧 finish、usage、TTFT、错误；
- 工具执行状态、耗时、结果大小与权限门禁；
- 压缩、前缀缓存、预算、重试/降级等诊断信息。

现有 `llm_request_injections` 只记录插件在 `on_llm_request` 上改了哪些字段，且是摘要；它不覆盖 provider 转换后的 payload、完整消息、工具 schema、响应与诊断。开发者模式补的就是这一块。

## 1. 目标与非目标

目标：

1. 每次模型请求（tool loop 的每一步、每次重试/降级尝试）产生一条快照，内容为**最终交给 provider SDK 的 payload**。
2. 同一回合内可查看：system prompt、工具定义、消息、额外注入、响应/usage、工具执行、来源归因、会话/环境、压缩/缓存/预算诊断。
3. 默认关闭，仅管理员可见；关闭时不序列化、不复制 payload，近零开销。
4. 快照只存内存：刷新页面可在 TTL 内回看；重启或过期后明确显示“已过期”，不伪装成仍可读。
5. 不写数据库、不进会话历史、不进导出与搜索；现有注入轨迹 UI 保持原样。
6. 四语 i18n、深浅色主题、窄屏全屏。

非目标：

- 不采集子代理/团队内部独立 run。
- 不采集 `raw_completion` 全量、逐 chunk 时间、成本估算。
- 不做请求重放/编辑、跨会话搜索、长期留存、历史导出。
- 不新增第二套 stats/compression 事件；用量、压缩、注入、前缀指纹一律复用现有通道。
- 不改变任何 provider 请求行为；关闭时不产生可观测延迟。

## 2. 术语与命名

| 术语 | 标识 | 含义 |
|---|---|---|
| 开发者模式 | `chatui.developer_mode` | 管理员开关；默认 `false` |
| 请求快照 | request snapshot / `snapshot_id` | 一次 provider 网络尝试及其结果与诊断 |
| 信封 | envelope | 小体积元数据 JSON，不含 payload；用于列表与实时事件 |
| 载荷 | raw | 脱敏后的最终 wire body，保真但受容量限制 |
| 视图 | view | 服务端从 raw 派生的统一分区（system/messages/tools/params/extra），不落内存第二份 |
| 回合 | turn / `turn_id` | 一条 ChatUI 用户消息到最终 bot 回复；由主 runner 生成 |
| 调用 | invocation / `invocation_id` | tool loop 中的一次模型调用 |
| 尝试 | attempt | 一次网络尝试；重试与 fallback 各自一条 |
| 诊断 | diagnostics | 压缩、前缀缓存、预算、重试/降级、stop reason |
| 归因 | provenance | system/extra 内容块来源；无法精确到区间时标 `approx` |

命名契约：

- 链事件：`llm_request_snapshots`（复数，一次调用完成后携带该调用的全部尝试信封）。
- 核心模块：`astrbot/core/provider/request_snapshot.py`。
- 单例：`request_snapshots`；类型 `RequestSnapshotRecorder`。
- API：`/chat/sessions/{session_id}/llm-snapshots`、`/chat/llm-snapshots/{snapshot_id}`、`/chat/developer-mode`。
- 配置：`chatui.developer_mode`。

## 3. 已定决策

| 编号 | 决策 | 说明 |
|---|---|---|
| D1 | 快照层取 **线上 payload** | 在 provider 网络边界、SDK 调用前抓取；含工具 schema、extra_body、provider 专有转换与重试后的降级 |
| D2 | 范围 = 主对话 + 辅助调用 | 主对话含 tool loop 每一步；辅助含压缩摘要、标题生成、其他 `aux`；子代理/团队 v1 不采集 |
| D3 | 留存 = 内存 ring buffer | TTL 120 分钟；每会话 128 条、64 MB；全局 256 MB；单条 1.5 MB；不落库 |
| D4 | 形态 = 右侧可拖宽 Inspector | 消息上的 DEV 标记 + 头部 DEV chip 两个入口；`ReasoningSidebar` 的交互为参照 |
| D5 | 信息边界 = B 档 | 请求态 + 响应摘要 + 工具执行基础字段 + 来源归因 + 会话/环境 + 诊断折叠；不含 raw_completion、逐 chunk、子代理、成本 |
| D6 | 分区顺序 | 差异 → 概览 → 响应 → 系统提示词 → 工具 → 消息 → 额外注入 → 诊断（默认折叠）→ 原始 JSON（默认折叠） |
| D7 | 工具定义与执行分开 | 「工具」只放模型看到的定义；「响应」放本步 tool_call 与执行状态、耗时、结果大小 |
| D8 | 默认关闭，仅管理员 | 配置默认 `false`；读取接口可被 chat scope 调用，写接口需 config/admin；会话访问沿用现有归属校验 |
| D9 | 不落库，刷新可回看 | 记录器持有 `message_id → turn_id` 内存映射；重启/过期后列表返回 `expired: true` |
| D10 | 复用现有通道 | `agent_stats`（回合用量/TTFT）、`context_compression`（压缩报告）、`llm_request_injections`（插件注入）、`prefix_diagnostics` 的哈希规则不重造 |
| D11 | 注入 UI 保留原样 | 消息上的注入面板不改；快照的「额外注入」区复用 `LlmRequestInjectionsPanel.vue`，并补来源归因 |
| D12 | 近似归因必须标注 | 找不到精确字符区间时显示 `△ 近似`，不伪造偏移 |
| D13 | 原始响应不采集 | 只记 finish_reason、返回 model、usage、错误摘要；不存 `raw_completion` |
| D14 | 采集失败不影响请求 | 所有记录调用 try/except 吞掉并 debug 日志；provider 请求路径行为不变 |

## 4. 总体架构

| 层 | 位置 | 职责 |
|---|---|---|
| 作用域 | `astrbot/core/provider/request_snapshot.py` | contextvar 携带 session/turn/invocation/kind/step/stage/attempt/fallback |
| 采集 | 4 个 provider 源（见 §6.2） | 在网络调用前抓最终 payload；不改变请求 |
| 结果回填 | 主 runner、压缩器、标题生成 | 回填响应摘要、工具执行、错误、stop reason |
| 留存 | `RequestSnapshotRecorder` | ring buffer、TTL、容量、脱敏、截断、message→turn 绑定 |
| 传输 | 主 runner → `AgentResponse("llm_request_snapshots")` → ChatService SSE | 只传信封；不传 raw |
| 读取 | ChatService + API | 列表按会话/回合/消息；详情按快照 id 取 raw + view |
| 展示 | `ChatDevModeInspector.vue` + `LlmSnapshotDetail.vue` | 右栏、请求轨、分区、差异、诊断、原始 JSON |

数据流：

```
runner 设置 scope(turn_id, invocation_id, kind=chat, step)
  → provider._query/_query_stream 组好最终 body
  → request_snapshots.capture(provider, raw, attempt)
  → SDK 调用（行为不变）
  → runner 回填 response / tool_execution / error
  → runner 发 llm_request_snapshots 信封事件
  → ChatService 存入 run.dev_snapshots + 广播 SSE
  → 回合落库时 request_snapshots.bind_message(message_id, turn_id)

UI 打开 Inspector
  → GET 列表（信封）→ 选一条
  → GET 详情（raw + view）→ 分区渲染
```

边界：

- 记录器不 import dashboard；ChatService 单向 import 记录器。
- `view` 在读取详情时从 `raw` 派生，不随快照常驻，避免双份内存。
- 记录器不感知 ChatUI 消息 id；绑定由 ChatService 在落库时写入记录器。

## 5. 数据模型

### 5.1 信封（envelope）

```json
{
  "snapshot_id": "01J8Z8Y6T9K3M2Q1",
  "session_id": "webchat!astrbot!7bf9e3b7-6335-4c03-8a63-8f26e4e7ee7f",
  "umo": "webchat:FriendMessage:webchat!astrbot!7bf9e3b7-6335-4c03-8a63-8f26e4e7ee7f",
  "turn_id": "9f2a4c1e",
  "invocation_id": "c4d8b7a2",
  "kind": "chat",
  "stage": "main",
  "step": 2,
  "status": "ok",
  "created_at": 1763200867.123,
  "duration_ms": 8300,
  "bytes": 21606,
  "truncated": false,
  "redactions": 3,
  "provider": {
    "id": "deepseek-official",
    "type": "openai_chat_completion",
    "class": "ProviderOpenAIOfficial",
    "model": "deepseek-v4.1",
    "returned_model": "deepseek-v4.1",
    "host": "api.deepseek.com",
    "proxy": true,
    "key_fp": "9f3a1c2b…7d21",
    "key_index": 0,
    "key_count": 2
  },
  "request": {
    "stream": true,
    "temperature": 0.7,
    "top_p": null,
    "max_tokens": 8192,
    "tool_choice": "auto",
    "reasoning_effort": "max",
    "attempt": 1,
    "fallback_index": 0,
    "timeout_s": 120,
    "retry_max": 2
  },
  "counts": {
    "messages": 9,
    "tools": 14,
    "system_chars": 18432,
    "estimated_input_tokens": 26300,
    "omitted_messages": 0
  },
  "response": {
    "response_id": "chatcmpl-9f2a",
    "finish_reason": "tool_calls",
    "created_at": 1763200867.0,
    "usage": {"input_other": 21000, "input_cached": 5300, "output": 642, "total": 26942},
    "usage_matches_request": true,
    "ttft_ms": 820,
    "chunks": 214,
    "error": null
  },
  "tool_executions": [
    {
      "call_id": "call_a1",
      "name": "read_file",
      "status": "ok",
      "started_at": 1763200868.1,
      "duration_ms": 312,
      "result_bytes": 1840,
      "result_preview": "1  \"\"\"File tools exposed to the agent. …",
      "result_ref": null,
      "permission": "allow",
      "executor": "main"
    }
  ],
  "diagnostics": {
    "compression": {
      "strategy": "llm_summary",
      "tokens_before": 61200,
      "tokens_after": 8400,
      "yield_ratio": 0.86,
      "reason": "threshold"
    },
    "cache": {
      "hit_ratio": 0.202,
      "prefix_match_ratio": 0.4,
      "prefix_warn_ratio": 0.8,
      "miss_warning": false,
      "system_changed": false,
      "tools_changed": true,
      "divergence_index": 6
    },
    "budget": {
      "step_used": 2,
      "step_max": 24,
      "context_tokens": 26300,
      "context_limit": 128000,
      "output_budget": 8000
    },
    "retries": {
      "count": 0,
      "fallback": false,
      "image_fallback": false,
      "schema_dropped": false,
      "history_popped": false
    },
    "stop_reason": null,
    "omitted_messages": 0
  },
  "provenance": [
    {"label": "persona", "chars": 6350, "hash": "…", "span": [0, 6350], "approx": false},
    {"label": "skills", "chars": 1840, "hash": "…", "span": null, "approx": true}
  ],
  "injections": [
    {
      "plugin": "astrbot",
      "handler": "on_llm_request",
      "changes": [
        {"field": "extra_user_content_parts", "delta": 1, "lossy": false, "preview": "…", "full": "…"}
      ]
    }
  ],
  "tool_meta": {
    "read_file": {"origin": "builtin", "active": true, "denied": false, "background": false, "schema_hash": "…"}
  },
  "env": {
    "file_access_mode": "full",
    "project": "AstrBot_for_spzx",
    "workspace": "G:/github/AstrBot_for_spzx",
    "config_profile": null,
    "astrbot_version": "4.31.4",
    "python": "3.12",
    "env_source": "request"
  }
}
```

字段约束：

- `kind`: `chat` | `compression` | `title` | `aux`。
- `stage`: `main` | `param` | `repair` | `null`。
- `status`: `pending` | `ok` | `error`。
- `provider.key_fp`: `sha1(api_key)[:8] + "…" + api_key[-4:]`；不存原文；取不到时为 `null`。
- `provider.host`: `urlsplit(base_url)` 的 scheme+host+port+path；去用户名、密码、query、fragment。
- `response.usage`: 与 `TokenUsage` 对齐；`total = input_other + input_cached + output`。
- 顶层 `duration_ms` 是模型调用耗时（请求开始到最终响应）；`chunks` 流式为实际块数，非流式为 `0`。
- `tool_executions.status`: `ok` | `error` | `denied` | `interrupted`；`permission`: `allow` | `deny`。
- `tool_meta[*].denied`: `true` | `false` | `null`；`null` 表示请求期拿不到禁用集合，UI 显示“未知”，不伪装成 `false`。
- `provenance[].span`: 精确字符区间 `[start, end]`；无精确位置时 `null` 且 `approx=true`。
- `injections`: 与现有 `llm_request_injections` 的 `items` 同形；由 runner 从回合事件读取，详情接口直接返回，UI 复用现有注入面板。
- `env.env_source`: `request`（请求时抓取）或 `current`（读取时补齐，UI 标“当前”）。
- `diagnostics.*` 允许为 `null`（未知/不适用）：`compression=null` 表示本步未压缩；`cache.*` 为 `null` 表示没有上一请求指纹；`retries.image_fallback/schema_dropped/history_popped` 为 `null` 表示 provider 未上报，不伪装成 `false`。
- `stop_reason`: `completed` | `user_stop` | `max_step` | `manual_pause` | `timeout` | `error` | `null`。
- 顶层 `bytes` 为脱敏后 raw 的序列化字节数；`truncated`/`redactions` 描述容量与脱敏结果。

### 5.2 raw

`raw` 是 provider-native 的最终 body，字段随适配器变化：

- OpenAI Chat Completions：`messages`、`model`、`tools`、`tool_choice`、采样参数、`extra_body`。
- OpenAI Responses：`input`、`instructions`、`model`、`tools`、`store` 等。
- Anthropic：`system`、`messages`、`tools`、`tool_choice`、`thinking` 等。
- Gemini：`contents`、`config.system_instruction`、`config.tools`、`config.generation_config` 等。

`raw` 只存脱敏后结果；`truncated`、`omitted_messages`、`original_bytes` 记录截断事实。原始响应不存。

### 5.3 view

详情接口返回服务端从 raw 派生的统一视图：

```json
{
  "system": {
    "text": "…",
    "hash": "…",
    "source_field": "messages[0]",
    "parts": [{"label": "persona", "chars": 6350, "approx": false}]
  },
  "messages": [
    {
      "index": 0,
      "role": "user",
      "name": null,
      "tool_call_id": null,
      "source": null,
      "content": [{"type": "text", "text": "…"}],
      "tool_calls": [],
      "chars": 18,
      "estimated_tokens": 9,
      "flags": []
    }
  ],
  "tools": [
    {
      "name": "read_file",
      "description": "Read a text file from the workspace.",
      "parameters": {"type": "object", "properties": {}},
      "origin": "builtin",
      "active": true,
      "denied": false,
      "background": false,
      "schema_hash": "…",
      "schema_bytes": 1820
    }
  ],
  "params": {"temperature": 0.7, "max_tokens": 8192, "tool_choice": "auto"},
  "extra": {"extra_body_keys": ["chat_template_kwargs"], "chat_template_kwargs": {"reasoning_effort": 100}}
}
```

`view.messages` 包含 system 消息；UI 的「系统提示词」区同时单独展示并注明 `source_field`，避免误以为消息区缺了 system。`estimated_tokens` 使用现有 token counter，标注“估算”。`source` 只在能归因时填充，无法归因时为 `null`，UI 以 role 兜底；`flags` 只反映仍在 payload 中的内部标记，发送前已被 provider 剥离的字段不可见。

## 6. 采集规格

### 6.1 作用域

```python
@dataclass(frozen=True, slots=True)
class LLMRequestScope:
    session_id: str | None
    umo: str | None
    turn_id: str | None
    invocation_id: str | None
    kind: Literal["chat", "compression", "title", "aux"]
    step: int | None
    stage: str | None
    fallback_index: int
    is_subagent: bool
```

`request_snapshot_scope(...)` 为 contextmanager；进入时 set，退出时 reset。默认 `kind="aux"`、`is_subagent=False`；未提供 `invocation_id` 时自动生成一个，供辅助调用回填响应使用。

`session_id` 必须是 **ChatUI 平台会话 id**（WebChat UMO
`webchat:<type>:webchat!<user>!<session_id>` 的最后一段），不是
`Conversation.cid`；两者是不同的 UUID。runner 用
`chatui_session_id_from_umo(req.session_id)` 取值，列表 API 用 ChatUI 的
`currSessionId` 查询，二者必须同键。

设置点：

| 调用方 | kind | 附加 |
|---|---|---|
| `ToolLoopAgentRunner` 主循环 | `chat` | `turn_id`（run 级 uuid）、`step`、`stage`、`invocation_id`、`fallback_index` |
| 参数生成/修复 | `chat` | `stage="param"` / `"repair"` |
| `LLMSummaryCompressor` | `compression` | 继承当前 `turn_id`（若在回合内） |
| 标题生成 | `title` | `turn_id=None`（回合之外） |
| 其他 `provider.text_chat` 调用 | `aux` | 尽量带 `session_id` |
| 子代理/团队 runner | 任意 | `is_subagent=True`；记录器直接丢弃 |

### 6.2 Provider 打点

以下 4 个源各自定义网络调用；其余 OpenAI 兼容源继承，不重复打点：

| 文件 | 方法 | 抓取点 |
|---|---|---|
| `openai_source.py` | `_query` / `_query_stream` | `payloads["tools"]` 写入、`extra_body` 合并之后，SDK `create` 之前 |
| `openai_responses_source.py` | `_query` / `_query_stream` | 最终 `input/instructions/tools/...` 组装完成、SDK 调用之前 |
| `anthropic_source.py` | `_query` / `_query_stream` | `system/messages/tools/...` 最终 kwargs 组装完成、SDK 调用之前 |
| `gemini_source.py` | `_query` / `_query_stream` | `contents/config` 最终组装完成、SDK 调用之前 |

规则：

- 传入的是**将要发送的对象**，不 deepcopy；记录器在 `enabled=false` 时立即返回，不读字段。
- OpenAI Chat 的 effective body 按 `{**payloads, **extra_body}` 还原（`extra_body` 同名键覆盖），再交给记录器；其余适配器直接抓最终 kwargs/config。
- 打开时由记录器做脱敏与截断；hook 不修改 payload、不 await、不抛异常。
- 每次 `_query`/`_query_stream` 调用即一次 attempt；记录器按 `invocation_id` 自增 `attempt`。
- `_query_stream` 在生成器开始迭代前抓取；流结束后的 usage 由上层回填。
- fallback 由 runner 设置 `fallback_index`；provider 仍按 attempt 记。

### 6.3 响应回填

`LLMResponse` 已含 `id`、`usage`、`usage_matches_request`、`tools_call_name/args/ids`、`reasoning_*`、`raw_completion`。回填规则：

- 主 runner 在每次调用拿到完整响应后调用 `request_snapshots.attach_response(invocation_id, response, chunks=...)`。
- `finish_reason`、`returned_model`、`created_at` 从 `raw_completion` 尽力解析；解析不到就留 `null`，不猜。
- `ttft_ms` 取 `AgentStats.time_to_first_token`；`chunks` 由流式循环计数；`duration_ms` 取调用起止。
- 同一次回填同时接收 `diagnostics`（compression/budget/stop_reason/retries），由 runner 从 `ContextManager.last_compression`、`_step_budget_*`、`_context_usage_snapshot` 与停止状态组装；缓存字段由记录器在 capture 时计算（§6.8）。
- 同一次回填附上 `read_injections(event)` 的结果（沿用现有 preview/full 上限），存入信封 `injections`，供 UI 的「额外注入」区复用 `LlmRequestInjectionsPanel`。
- 压缩器/标题生成若拿到 `LLMResponse`，同样回填；拿不到就保持 `response=null`，UI 显示“未记录”。
- 错误路径：runner 捕获异常后调用 `request_snapshots.mark_error(invocation_id, exc)`；只记异常类名与截断消息（≤1 KB），不记 traceback。

### 6.4 工具执行

主 runner 在工具执行前后回填：

```python
request_snapshots.record_tool_execution(
    invocation_id=invocation_id,
    call_id=func_tool_id,
    name=func_tool_name,
    status="ok" | "error" | "denied" | "interrupted",
    started_at=...,
    duration_ms=...,
    result_bytes=...,
    result_preview=...,
    result_ref=...,       # 复用现有大结果引用；没有则 None
    permission="allow" | "deny",
    executor="main",
)
```

- `result_preview` 上限 2 KB；完整结果仍走现有 `/chat/runs/{run_id}/tool-results/{tool_call_id}` 或 `/chat/messages/{message_id}/tool-results/{tool_call_id}`，不新增存储。
- `result_ref` 只作为后续版本的预留字段；v1 UI 只显示 `result_preview`，不在快照内跳转完整结果（完整结果仍在工具卡片里）。
- `permission` 依据 `denied_tools`、文件访问模式、max_step 强制收尾、MCP 策略等现有门禁结果。
- 工具执行发生在响应之后；信封已发出时允许记录器原地补字段，列表/详情读取最新值。

### 6.5 来源归因

在组装 system prompt 与 `extra_user_content_parts` 时，把**实际写入的文本**与标签登记到记录器的**会话级缓冲**：

```python
record_provenance(session_id, label="persona", text=exact_text_written)
```

缓冲按 `session_id` 保存（上限 32 条，TTL 10 分钟，不跨会话），记录器在 capture 时读取该会话的登记列表，在最终 system 文本中查找：

- 唯一出现 → 记 `span`，`approx=false`。
- 多处出现或找不到（被模板改写/拼接）→ `span=null`，`approx=true`，只保留 `chars`/`hash`。

登记在 capture 后保留到 TTL，供同回合后续请求复用；不使用 contextvar，避免组装与 provider 抓取不在同一任务时丢失。

已知登记点（实施时逐个接）：`astr_main_agent.py`（persona/环境/规则/资源常量拼接）、`file_access_guidance.py`、`group_chat_context.py`、技能/KB/goal 注入点。无法定位的注入点只做“近似”、不硬编码偏移。

标签集合（可扩展，UI 只做本地化映射，未知标签原样显示）：

`persona`、`environment`、`file_access`、`chatui_rules`、`tool_guidance`、`skills`、`goal`、`memory`、`knowledge`、`team`、`plugin`、`other`。

现有 `record_team_source` 已记录的 role/task/knowledge/skills/goal 继续复用；插件注入仍由 `llm_request_injections` 提供 plugin/handler/delta。

### 6.6 辅助调用

- 压缩摘要：`LLMSummaryCompressor` 调用 provider 前设置 `kind="compression"`；继承回合 `turn_id`（若在回合内）。
- 标题生成：`astr_main_agent` 的标题请求设置 `kind="title"`；`turn_id=None`，只在会话级列表出现。
- 其他辅助调用：默认 `kind="aux"`；若拿不到 session_id，v1 **不记录**（没有全局视图，避免无主内存占用）。

### 6.7 子代理与团队

- `is_subagent=True` 时记录器在 `capture()` 入口直接返回，不分配 id、不序列化。
- 团队主 run 若走主 runner，则正常记录；团队内部成员 run 不记录。
- 未来若要接入，复用同一信封，新增 `kind="subagent"` 与父 `invocation_id`，不破坏现有接口。

### 6.8 诊断计算

| 字段 | 来源 | 规则 |
|---|---|---|
| `cache.hit_ratio` | `response.usage.input_cached / (input_other + input_cached)` | 响应回填时算；无 usage 或 provider 不报 cached 则 `null` |
| `cache.prefix_match_ratio` | 上一请求消息 hash 前缀中仍保留的比例；system/tools 变化记 0 | 无上一请求则 `null`；逐条追加时保持 1.0 |
| `cache.prefix_warn_ratio` | 前缀命中告警阈值常量 | 默认 `0.8`；仅用于展示判定标准 |
| `cache.miss_warning` | 低前缀率 + 服务端命中率确认；服务端不返回时按前缀判定 | 无上一请求则 `null`；服务端高命中率可推翻前缀告警 |
| `cache.system_changed` / `tools_changed` | 记录器自持的上一请求指纹（capture 时读取并更新） | 无上一请求则 `null` |
| `cache.divergence_index` | 上一请求与当前请求的逐消息 hash | 第一个不同下标；找不到则 `null` |
| `compression` | runner 传入 `ContextManager.last_compression` | 本步未压缩则 `null` |
| `budget` | runner 的 `_step_budget_*`、`_context_usage_snapshot` | 辅助调用不适用则 `null` |
| `retries.count` | 同 `invocation_id` 的 attempt 数 − 1 | 0 表示无重试 |
| `retries.fallback` | `fallback_index > 0` | 布尔 |
| `retries.image_fallback` / `schema_dropped` / `history_popped` | provider 恢复分支 | 未上报则 `null`，不伪装成 `false` |
| `stop_reason` | runner 停止状态 | 见 §5.1 枚举；辅助调用 `null` |

缓存指纹复用 `prefix_diagnostics` 的 `fingerprint_messages`/`fingerprint_tools`/`system_text`，不新造 hash 规则。指纹由记录器在自己内部按会话保存上一份，**不依赖** `prefix_diagnostics.record_session_request` 的写入时序（runner 会在 provider 调用前覆盖它）。指纹基于 capture 收到的原始 payload 计算；截断只影响展示，不参与缓存比较。

## 7. 留存、容量与脱敏

### 7.1 容量常量

| 常量 | 值 | 说明 |
|---|---|---|
| `MAX_BYTES_PER_SNAPSHOT` | 1.5 MB（1,572,864 B） | 单条 raw 脱敏后的上限 |
| `MAX_SNAPSHOTS_PER_SESSION` | 128 | 含重试/辅助调用 |
| `MAX_BYTES_PER_SESSION` | 64 MB（67,108,864 B） | 会话总量 |
| `MAX_TOTAL_BYTES` | 256 MB（268,435,456 B） | 进程总量 |
| `TTL_SECONDS` | 7200 | 2 小时 |
| `MAX_MESSAGE_BINDINGS` | 1024 | `message_id → turn_id` 映射条数 |

常量放在 `request_snapshot.py` 顶部，不新增配置项；容量触顶时优先淘汰最旧会话/最旧快照，UI 显示“容量已满，已丢弃最早 N 条”的事实（记录器返回 `dropped` 计数）。

### 7.2 截断顺序

单条超 1.5 MB 时：

1. 保留 system 与工具 schema 的完整文本（各自上限 64 KB / 16 KB/工具）。
2. 从**最旧**消息开始丢弃，直到满足预算；记 `omitted_messages`。
3. 单条消息上限 32 KB；超长消息保留头部与尾部各 8 KB，中间标记 `…[truncated N bytes]…`。
4. 仍超限时，压缩 provider 专有扩展字段（`extra_body` 等）到 16 KB。
5. 最终标记 `truncated=true`、`original_bytes`。

若 system + 工具本身已超 1.5 MB，则对超限部分用 `…[truncated]…` 标记，仍不丢整条快照。绝不静默丢整条快照；也不截掉最新消息。

### 7.3 脱敏

- 复用 `llm_request_trace.bounded_context_value`，新增 `limit` 参数（默认保持 8192，现有调用零变化）与 `redactions` 计数。
- 始终脱敏：`api_key`、`authorization`、`password`、`secret`、`token`、`provider_config`、`encrypted`、base64 字段。
- 媒体：`data:` URI 只留类型与长度；URL 去 query/fragment，留 scheme+host+port+path；本地路径只留 basename。
- 不提供“查看原文”开关；管理员也不能在面板里还原密钥。

### 7.4 线程与生命周期

- 所有记录操作在事件循环线程执行，临界区内不 await；用 `OrderedDict` 维护会话顺序。
- 读写都在记录器单例内；ChatService 只拿副本。
- 进程重启即清空；无后台清理任务，TTL 在读写时惰性执行。

## 8. 传输、关联与 API

### 8.1 实时信封

主 runner 在每次调用完成后发：

```python
AgentResponse(
    type="llm_request_snapshots",
    data=AgentResponseData(
        chain=MessageChain(
            type="llm_request_snapshots",
            chain=[Json(data={"items": envelopes})],
        )
    ),
)
```

- `envelopes` 只含 §5.1 的信封，不含 raw。
- 一次调用若有多次重试/fallback，`items` 包含全部 attempt。
- 发送时机：该次调用的最终响应已到达、usage 已回填；流式请求在流结束后发送。
- ChatService 在 `_consume_chat_run` 中处理该 chain type：写入 `run.dev_snapshots`，广播 `{"type": "llm_request_snapshots", "data": {"items": ...}}`。
- 历史内容构建（`build_bot_history_content`）**不包含**该字段；`_persist_bot_reply_if_orphan`（`webchat_event.py`）与 ChatService 的 telemetry 跳过列表都加 `llm_request_snapshots`。

### 8.2 消息关联

- ChatService 在 bot 历史行落库后调用 `request_snapshots.bind_message(message_id, turn_id, session_id)`。
- 映射由记录器持有，条数上限 1024，TTL 与快照一致；过期即查不到。
- 刷新后按 `message_id` 查列表；查不到返回 `expired=true`，UI 不显示消息级 DEV 标记（v1 不在历史内容里写任何快照字段）。
- 头部 DEV chip 始终可打开会话级列表；每条信封带 `turn_id`，UI 可标注“锚点消息”但不保证可跳转。

### 8.3 API

| 方法 | 路径 | 权限 | 返回 |
|---|---|---|---|
| GET | `/chat/sessions/{session_id}/llm-snapshots?message_id=&turn_id=&limit=` | chat scope + 管理员 + 会话归属 | `{enabled, expired, items: [envelope], limits, dropped}` |
| GET | `/chat/llm-snapshots/{snapshot_id}` | chat scope + 管理员 + 会话归属 | `{envelope, raw, view}`；过期/不存在 404 |
| GET | `/chat/developer-mode` | chat scope | `{enabled, can_edit, limits}` |
| POST | `/chat/developer-mode` | config/admin scope | `{enabled}`；严格布尔校验 |

规则：

- `limit` 默认 50、上限 128；倒序（最新在前）。
- 列表不返回 raw；详情才返回 raw + view。
- 列表返回轻量信封：去掉 `injections`、`provenance` 的全文，只留计数/标签摘要；详情返回完整信封。避免 128 条 × 注入全文撑大列表响应。
- `expired=true` 只在按 `message_id` 查询、且内存绑定已不存在时返回；此时 `items` 为空。会话级空列表（刚开启、尚无请求或重启后）返回 `expired=false`，由 UI 显示“暂无快照”。
- 会话归属校验复用现有 snapshot/tool-result 的服务层逻辑；跨账号一律 403，非管理员一律 403。
- 列表/详情：非管理员 403；`GET /chat/developer-mode` 对所有 chat scope 返回 200，`can_edit=false`，由前端决定是否显示入口。
- 所有新接口加入 OpenAPI；前端用 `pnpm generate:api` 重新生成客户端。

## 9. 配置与权限

- `DEFAULT_CONFIG["chatui"]["developer_mode"] = False`。
- 读取：记录器每次 `capture()` 读 `astrbot_config["chatui"]["developer_mode"]`；关闭即返回，不缓存过期状态。
- 写入：`ChatService.set_chatui_developer_mode(enabled)` → `_write_chatui(developer_mode=bool)`；`_write_chatui` 已做浅合并，保留 `thinking_effort` 等同级键。
- 校验：只接受 `bool`；其他类型 400。无需重启。
- 权限：GET 走 chat scope（返回 `can_edit` 供 UI 决定是否显示开关）；POST 走 config/admin scope。
- UI 只对 `can_edit=true` 的账号显示 DEV chip 与开关；非管理员即使打开也不会有采集（配置只能由管理员写入）。
- 开关说明文案必须明确：快照含系统提示词与完整消息，仅保存在本机内存，仅管理员可见，2 小时后清空。

## 10. 前端设计

### 10.1 组件

| 组件/模块 | 职责 |
|---|---|
| `ChatDevModeInspector.vue` | 右栏壳：标题、开关入口、请求轨、详情容器、拖宽、窄屏全屏、空/禁用/过期状态 |
| `LlmSnapshotDetail.vue` | 单条详情：差异、概览、响应、系统提示词、工具、消息、额外注入、诊断、原始 JSON |
| `useLlmSnapshots.ts` | API 调用、列表/详情缓存（按 snapshot_id）、loading/error、刷新、`enabled` 状态 |
| `Chat.vue` | 挂载 Inspector；DEV chip；`devInspectorOpen`、`devTurnId`、`devSnapshotId` 状态；消息标记事件接入 |
| `ChatMessageList.vue` | bot 消息 meta 行的 `DEV · N` 标记；仅当该回合有实时信封时显示；点击打开对应 turn |

复用：

- 「额外注入」直接复用 `LlmRequestInjectionsPanel.vue`（现有 `items` 形状）。
- 右栏拖宽参照 `ReasoningSidebar.vue` 的交互；不新造第三套侧栏外观。
- 代码块/等宽字体沿用 ChatUI 现有 token；不引入新 UI 依赖。

### 10.2 入口与行为

- 头部 DEV chip：仅 `can_edit` 显示；关闭态为描边、开启态为强调色；点击打开 Inspector（会话级）。
- 消息标记：`DEV · N`，N 为该回合信封数；点击打开 Inspector 并选中该回合第一条/最后一条。
- 前端收到 `llm_request_snapshots` SSE 帧时，按 `snapshot_id` 去重合并到当前 bot 记录的 `llmRequestSnapshots`（仅内存）；历史重建不产生该字段。
- 刷新后消息标记不显示（无持久字段）；头部列表仍可用，直到过期。
- 开关 popover：开关、上限说明、内存提示、当前 `enabled`。切换后立即生效；失败回滚开关并 toast。

### 10.3 Inspector 布局

- 宽度 `min(820px, 66vw)`，可拖宽；`<1000px` 时全屏，请求轨改为横向滚动。
- 请求轨：分组「本回合」「本会话 · 辅助」；每项两行：kind/step 与 model/耗时；徽标显示 `cache %`、`+N tool`、`压缩`、`error`、`retry`；缓存未命中时用「缓存未命中」替代百分比。
- 详情头：kind、step、状态码、finish、模型、provider、host、attempt；右侧「复制 JSON」「下载」「刷新」。
- 分区顺序固定：差异（step ≥ 2）→ 概览 → 响应 → 系统提示词 → 工具 → 消息 → 额外注入 → 诊断 → 原始 JSON。
- 默认展开：概览、响应、系统提示词、工具、消息、注入；默认折叠：诊断、原始 JSON。
- 概览与响应之间显示「缓存未命中」告警条：前缀匹配率 `< 0.8` 时出现；服务端返回命中率时以其为准，未返回则按前缀判定。
- 长文本在卡片内滚动（系统提示词 124px、消息 112px），不撑长消息流；单块可展开/收起。
- 工具区：搜索框（name/desc/origin）、每行 name + origin 标签 + 新增/denied 标签 + 参数个数；点击展开 schema。
- 响应区：finish/response id/TTFT/chunks/usage 网格 + 工具调用行（状态点、耗时、结果字节、展开 result_preview）；v1 不在快照内跳转完整结果。
- 诊断区：压缩、前缀缓存、预算、重试/降级、stop reason、省略条数；每行两列，默认折叠。
- 差异：消息增删、工具增删、system 变化、参数变化；有压缩/截断时用 warning 色调。
- 归因：系统提示词上方 chip 列表（label + 字节数）；`approx` 加 `△` 与 tooltip。
- 复制/下载：复制 `{envelope, raw, view}` 或仅 raw；下载 `.json`；不提供“复制密钥”。

### 10.4 文案（zh-CN 基线）

| key | 文案 |
|---|---|
| `devMode.chip` | DEV |
| `devMode.title` | 请求快照 |
| `devMode.subtitle` | 本回合 {count} 次请求 · 另有 {aux} 次辅助调用 |
| `devMode.kind.chat` | 主对话 |
| `devMode.kind.compression` | 压缩摘要 |
| `devMode.kind.title` | 标题生成 |
| `devMode.kind.aux` | 辅助调用 |
| `devMode.section.overview` | 概览 |
| `devMode.section.response` | 响应 |
| `devMode.section.system` | 系统提示词 |
| `devMode.section.tools` | 工具 |
| `devMode.section.messages` | 消息 |
| `devMode.section.injections` | 额外注入 |
| `devMode.section.diagnostics` | 诊断 |
| `devMode.section.raw` | 原始 JSON |
| `devMode.diff.title` | 相对上一步 |
| `devMode.state.disabled` | 开发者模式未开启。开启后会记录每次 LLM 请求的完整上下文，仅保存在本机内存。 |
| `devMode.state.empty` | 本回合还没有请求快照。发送一条消息后再看。 |
| `devMode.state.expired` | 快照已过期。服务重启或超过 2 小时后会清空。 |
| `devMode.notice.memory` | 快照仅保存在内存 · 2 小时后过期 · 关闭开发者模式即停止采集 |
| `devMode.notice.redacted` | 已脱敏 {count} 处 |
| `devMode.action.copy` | 复制 JSON |
| `devMode.action.download` | 下载 |
| `devMode.action.refresh` | 刷新 |
| `devMode.settings.title` | 开发者模式 |
| `devMode.settings.switch` | 记录 LLM 请求快照 |
| `devMode.settings.limits` | 每会话最多 {count} 条 · {size} · {ttl} 后过期 |

en-US / ru-RU / ja-JP 同义翻译；未知归因标签原样显示，不硬编码中文。

### 10.5 交互与无障碍

- `Esc` 关闭 Inspector；点击遮罩（窄屏）关闭。
- 所有折叠头是 `<button>` 且带 `aria-expanded`；状态点有文字标签，不只靠颜色。
- 复制成功显示 1.2s 的“已复制”；失败提示“复制失败”。
- 主题用 Vuetify token；深浅色都不得出现硬编码白底/黑字。

## 11. 行为规格与边界

| 场景 | 行为 |
|---|---|
| 开发者模式关闭 | `capture()` 立即返回；不分配 id、不序列化；DEV chip 显示描边态并引导开启 |
| 开启后首个请求 | 列表接口可立即读到 `pending` 快照；信封在调用完成后出现；Inspector 可手动刷新 |
| 重试 | 每次 `_query` 一条快照，`attempt` 递增；请求轨按 `invocation_id` 分组，显示 attempt 徽标 |
| fallback | 候选 provider 各自一条；`fallback_index` > 0 显示 fallback 徽标；响应 model 如实显示 |
| 请求异常 | `status=error`，`response.error` 记类名 + 截断消息；仍可在诊断区看到 attempt/retry |
| 辅助调用 | 压缩在回合内出现于「本会话 · 辅助」；标题在回合外，只出现在会话列表；`response` 可为 null |
| 超容量 | 淘汰最旧；列表返回 `dropped`；UI 提示“容量已满，已丢弃最早 N 条” |
| 单条截断 | `truncated=true`；UI 在概览与原始 JSON 显示“已截断，原始大小 X”；最新消息优先保留 |
| 过期 | 按消息查询时 `expired=true`、items 空；会话级列表为空时 `expired=false`；详情 404；UI 区分“已过期”与“暂无快照”，不回退到历史内容 |
| 服务重启 | 同上；不尝试恢复 |
| 刷新页面 | 头部列表可用（TTL 内）；消息标记不显示；不发起 N 个按消息查询 |
| 并发回合 | 以 `turn_id` 隔离；列表按 turn 分组；message_id 绑定各自独立 |
| 非管理员 | 403；前端不显示入口 |
| 跨账号会话 | 403；不泄露会话是否存在 |
| 子代理 | 不记录；不产生空的 DEV 标记 |
| 复制/下载 | 只导出脱敏后的 envelope/raw/view；不导出密钥或原始响应 |

## 12. 接口变更清单

| 文件 | 改前 | 改后 |
|---|---|---|
| `astrbot/core/provider/request_snapshot.py` | 不存在 | 记录器、scope、信封、容量/TTL/脱敏/截断、message 绑定 |
| `astrbot/core/provider/sources/openai_source.py` | `_query`/`_query_stream` 直接调 SDK | 调用前 `request_snapshots.capture(...)`；行为不变 |
| `astrbot/core/provider/sources/openai_responses_source.py` | 同上 | 同上，抓 responses 最终 body |
| `astrbot/core/provider/sources/anthropic_source.py` | 同上 | 同上，抓 Anthropic kwargs |
| `astrbot/core/provider/sources/gemini_source.py` | 同上 | 同上，抓 Gemini contents/config |
| `astrbot/core/agent/runners/tool_loop_agent_runner.py` | 只发 agent_stats / 注入事件 | 设 scope；回填 response/tool execution/error；发 `llm_request_snapshots`；子代理跳过 |
| `astrbot/core/agent/context/compressor.py` | 直接调 provider | 设 `kind="compression"` scope |
| `astrbot/core/astr_main_agent.py` | 标题生成直接调 provider | 设 `kind="title"` scope；system 组装处用 `provenance_scope()` 登记 |
| `astrbot/core/astr_main_agent_resources.py` / `file_access_guidance.py` / `group_chat_context.py` | 直接拼接提示词 | 在 `provenance_scope()` 内登记实际写入文本 |
| `astrbot/core/pipeline/llm_request_trace.py` | `bounded_context_value` 固定 8192 | 新增 `limit` 参数与 `redactions` 计数；默认行为不变 |
| `astrbot/core/config/default.py` | `chatui` 无该键 | 新增 `chatui.developer_mode: false` |
| `astrbot/dashboard/services/chat_service.py` | run 状态只有 injections 等 | 增加 `dev_snapshots`、新 chain type 处理、message 绑定、列表/详情读取、开关读写 |
| `astrbot/core/platform/sources/webchat/webchat_event.py` | 孤儿回复跳过 agent_stats/context_compression/llm_request_injections | 跳过列表加 `llm_request_snapshots`，避免快照信封被当成回复落库 |
| `astrbot/dashboard/services/chatui_settings.py` | 只管 thinking/quick messages | 增加 developer_mode 的读取/严格校验 |
| `astrbot/dashboard/api/chat.py` | 无相关路由 | 4 条新路由；沿用 `require_chat_scope` / `require_config_scope` |
| `astrbot/dashboard/schemas.py` | 无相关模型 | 开关请求模型（严格 bool）与响应模型 |
| `dashboard/src/components/chat/ChatDevModeInspector.vue` | 不存在 | 右栏壳、请求轨、拖宽、状态 |
| `dashboard/src/components/chat/LlmSnapshotDetail.vue` | 不存在 | 分区详情、差异、诊断、原始 JSON |
| `dashboard/src/composables/useLlmSnapshots.ts` | 不存在 | API、缓存、刷新、错误 |
| `dashboard/src/components/chat/Chat.vue` | 无 DEV 入口 | DEV chip + Inspector 挂载 + 事件 |
| `dashboard/src/components/chat/ChatMessageList.vue` | 只有注入标记 | 增加 DEV 标记（仅实时信封） |
| `dashboard/src/composables/useMessages.ts`（及实际 SSE 消费方） | 不识别新 chain type | 把 `llm_request_snapshots` 挂到当前 bot 记录（仅内存） |
| `dashboard/src/i18n/locales/*/features/chat.json` | 无 devMode | 四语 `devMode.*` |
| `dashboard/src/api/v1.ts` + generated client | 无相关方法 | `pnpm generate:api` 重新生成 |

## 13. 兼容与回滚

- 配置键是新增项；旧配置加载后取默认 `false`，无迁移。
- 新 chain type 只被新 ChatService 处理；旧前端忽略未知事件，不影响聊天。
- 记录器与 API 都是只读增量；关闭开关即停止采集，已存快照仍可读至 TTL。
- 回滚：删除 UI 入口 + 保持 `developer_mode=false` 即可；无数据库变更、无历史数据残留。
- 不改变 `LLMResponse`、`ProviderRequest` 的公共字段；不改变 provider 请求内容。

## 14. 测试计划

### 14.1 后端

- 记录器：关闭时不分配 snapshot、不序列化 payload（用 spy/计数断言）；容量淘汰顺序；TTL 惰性清理；单条截断保留最新消息；脱敏计数；message 绑定 TTL。
- 信封：字段齐全；`key_fp` 不含原文；`host` 去凭据/query；`bytes`/`truncated` 一致。
- 4 个 provider 源 × `text_chat`/`text_chat_stream`：用假 client 断言 capture 收到最终 body（含 tools/extra_body）；关闭时 capture 未被调用；capture 抛异常不影响请求。
- runner：每步 scope 正确；重试 attempt 递增；response/usage/TTFT/chunks 回填；工具执行状态/耗时/结果大小；子代理跳过；错误路径 mark_error；`llm_request_snapshots` 只含信封。
- 压缩/标题：kind 正确；标题 `turn_id=null`；压缩继承回合。
- ChatService：新 chain type 入 run 状态并广播；不写入历史内容；`build_bot_history_content` 不含该字段；落库绑定 message→turn；过期返回。
- API：非管理员 403；跨账号 403；列表 limit/倒序；详情 404；开关 strict bool、写入保留同级键。
- 现有 `bounded_context_value` 回归：默认 8192 行为与输出不变。

### 14.2 前端

- Vitest：Inspector 空/禁用/过期；请求轨切换；分区默认折叠；工具搜索；消息展开；差异徽标；复制按钮反馈；DEV 标记仅实时信封出现；四语 key 存在。
- 不引入新的全局状态库；composable 单测用 mock API。

### 14.3 Web 自动化

1. 管理员开启开发者模式；发送一条会触发工具的消息。
2. 打开 Inspector：断言系统提示词、工具定义、消息、响应 usage、工具执行、诊断折叠存在；切换请求轨；展开原始 JSON。
3. 关闭开发者模式；再发一条；断言不产生新快照/新 DEV 标记。
4. 刷新页面：断言头部列表仍可打开且显示“已过期”或 TTL 内条目；消息标记不出现。
5. 用非管理员账号：断言无入口、接口 403。

构建到 `data/dist` 后在本地 ChatUI 执行；不把构建产物当提交内容（除非仓库约定）。

## 15. 实施顺序（tickets）

| Ticket | 内容 | 提交信息（英文） |
|---|---|---|
| T1 | 记录器 + scope + 配置 + 容量/脱敏/截断单测 | `feat(provider): add in-memory llm request snapshot recorder` |
| T2 | 4 个 provider 源的 capture 钩子 + 适配单测 | `feat(provider): capture final llm request payloads` |
| T3 | runner 响应/工具执行/错误/来源归因 + 信封事件 + 子代理跳过 | `feat(agent): report llm request snapshots and tool executions` |
| T4 | ChatService run 状态、message 绑定、4 条 API、OpenAPI 再生成 | `feat(dashboard): expose llm request snapshots in chat api` |
| T5 | 前端 composable + Inspector 壳/请求轨/详情分区 | `feat(chatui): add developer mode snapshot inspector` |
| T6 | DEV chip、消息标记、开关 popover、四语文案 | `feat(chatui): wire developer mode entry points and i18n` |
| T7 | 构建到 data/dist + Web 自动化 + 文档/更新日志 | `test(chatui): verify developer mode end to end` |

每个 ticket 独立可回滚；T1-T3 不依赖前端，T4 之后前端才可见。T5/T6 的视觉以已确认的 HTML 预览为准。

## 16. 风险与开放问题

| 风险 | 处理 |
|---|---|
| 内存压力 | 容量/TTL 常量；关闭即零成本；上线前测一条长会话的峰值 |
| 管理员可见敏感内容 | 默认关闭；始终脱敏；不存原始响应；文案明示 |
| provider SDK 转换差异 | 只在 4 个网络边界打点；每个源单独测最终 body |
| 归因不精确 | 精确 span 优先；否则 `approx` + `△`，不伪造 |
| 刷新后消息标记消失 | 设计如此；头部列表仍可用；在文案中说明“仅内存” |
| 多 worker/多进程 | 当前假设单进程；若未来多 worker，每个进程独立，UI 可能命中不同实例；需另立方案 |
| `bounded_context_value` 改动回归 | 默认参数保持不变；现有 agent teams 测试必须全绿 |
| 采集开销 | 关闭时仅一次配置读取；打开时只在 dev 模式，接受毫秒级序列化 |

开放问题（不阻塞 spec，实施计划中定）：

- 标题生成的具体调用点与是否总能拿到 `LLMResponse`（拿不到则 `response=null`）。
- `env.file_access_mode` 在请求期是否总能同步取到；取不到则 `env_source="current"`。
- 技能/KB/goal 的 provenance 登记点是否都能在装配处拿到“实际写入文本”；拿不到则只做近似归因。

## 17. 附录：已确认决策记录

| 轮次 | 选择 | 理由 |
|---|---|---|
| 快照层 | 线上 payload | 界面如实；provider 转换后的实际请求才是证据 |
| 采集范围 | 主对话 + 辅助调用 | 压缩/标题最容易被误判成主请求；子代理另立 spec |
| 留存 | 内存 ring buffer | 调试数据不是会话内容；不污染历史/导出/搜索 |
| 形态 | 右侧 Inspector | 视线不离开回复；可拖宽，长 payload 不撑坏消息流 |
| 信息边界 | B 档 | 覆盖“为什么这样回”的多数追问，又不把 raw response 塞进内存 |
| 命名 | 请求快照 | 与注入轨迹、TracePage 区分；回答“这次请求发了什么” |
| 响应位置 | 概览之后、系统提示词之前 | 先看结果再看输入；响应块短 |
| 注入 UI | 保留原样 | 避免两套注入 UI 互相打架；快照内做更完整归因 |
| 工具拆分 | 定义在「工具」，执行在「响应」 | 请求态与响应态不混 |
| 诊断粒度 | 单块默认折叠 | 头部摘要足够扫读；展开后六行诊断 |

已确认的界面预览包含：DEV chip、bot 消息 `DEV · 3` 标记、请求轨（3 主对话 + 1 压缩）、差异条、概览（含会话/环境）、响应（usage/TTFT/工具执行）、系统提示词归因 chips、工具定义（origin/denied/schema）、消息来源与 token、注入轨迹、诊断折叠、原始 JSON 折叠。实现以该预览为准，不新增未确认的分区。

