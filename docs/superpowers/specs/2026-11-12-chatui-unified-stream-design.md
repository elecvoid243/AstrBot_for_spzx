# ChatUI 单连接流式架构（Unified Stream）设计文档

- Date: 2026-11-12
- Status: Approved（grilling 已收敛，待实现）
- Related: `2026-07-25-webchat-system-event-stream-design.md`、`2026-08-12-agent-collab-design.md`

## 0. 背景与问题

ChatUI 当前是「每个运行中的会话一条 SSE」：

- 发送走 `POST /api/v1/chat`，响应流就是这一轮的 SSE；
- 当前会话额外常驻一条 `system-stream`；
- 管理员登录后还有一条 `/api/v1/logs/live`；
- 切换会话**不释放**后台 run 的连接。

AstrBot 默认用 Hypercorn 明文 HTTP（`dashboard.ssl.enable = False`），浏览器对同一 origin 的 HTTP/1.1 并发上限是 6。5~6 个会话同时流式时连接池被打满，任何新的 `fetch`（切会话、停止、发送、附件）都会在 DevTools 里显示 `Queueing/Stalled`，表现为「按钮点了没反应」。

同时，每个 token 在主线程上还有一笔与连接数无关的固定开销：

- `Chat.vue` 与 `ChatMessageList.vue` 各有一个 `deep: true` 的全量 watcher；
- `isMessageStreaming` 每次调用都 `slice()` 复制整段尾部数组，模板里每个 bot 行调用多次；
- `updateScrollMarkers`、`MutationObserver`、`ChatScrollMarkers.updateReadingPosition` 每 token 触发同步布局；
- `activeMessagePreviews` 每 token 对整段会话文本做一次 join + 正则。

本设计同时解决这两类问题：传输层收敛为**一条 SSE**，渲染层去掉**每 token 的全量扫描与强制重排**。

## 1. 目标与范围

### 1.1 目标

| 指标 | 目标 |
|---|---|
| 单 tab 连接数 | ≤2（chat 统一流 + admin 日志），与运行中会话数无关 |
| 6 会话并发时按钮请求 | DevTools Queueing < 10ms，请求 P95 < 100ms 发出 |
| 控制事件端到端延迟 | `stop` / 选择框 P95 < 200ms |
| 控制帧丢失 | 0；`plain` 帧允许丢，但必须能被 snapshot 补齐且 `dropped` 可观测 |
| 200 条历史 + 流式回复 | per-token 主线程耗时降 ≥10×；Layout 从 2~3 次/token 降到 ≤1 次/帧 |
| 长会话内存 | 500 条消息下稳定，无持续增长 |

### 1.2 范围决策（已与用户确认）

| 决策点 | 结论 |
|---|---|
| 修复范围 | 连接池 + 单会话渲染；不做消息列表虚拟化 |
| 后台会话实时性 | 后台会话的 token 继续实时，走同一条统一流，不额外开连接 |
| 传输载体 | 新增一条 SSE 多路复用端点，payload 带 `session_id` |
| 多标签页 | 每个 tab 一条流；服务端按连接订阅 |
| 兼容 | 新端点 + 旧端点冻结一版；前端本地开关回退；`open_api` / `remote_chat` 不迁移 |
| 订阅集合 | 所有 owned 且 running 的会话 + system 事件；日志流不并入 |
| 背压 | 每会话有界队列 + `plain`/`reasoning` 合并 + 丢最旧 + `resync_required` 兜底 |
| 大帧 | 序列化后 >64KB 改 out-of-band，帧里只带 ref |
| 控制事件 | 每会话 control/data 双队列，control 优先 |
| 重连 | per-session cursor + 环形缓冲 + snapshot 兜底 |
| 发送 | 改普通 POST 返回 `{run_id, message_id}`，输出全走统一流 |
| 跨 tab | 服务端 fan-out 到所有订阅者；一致性范围 = 本 tab 打开过的会话 |
| 旧流 | unified 模式关闭 system-stream、resume stream、WS；切换开关后刷新页面 |
| 默认值 | 所有用户默认开启 |
| 可观测性 | debug 日志记录 `dropped` / 队列深度 |

### 1.3 非目标（YAGNI）

- 消息列表虚拟化；
- 服务端 delta 合并（见 §8 触发条件）；
- `open_api` / `remote_chat` 迁移到统一流；
- 删除 WebSocket 传输代码（隐藏一个版本）；
- 替换 markstream 渲染组件；
- 跨设备/跨 tab 的「未打开会话」token 物化。

## 2. 总体架构

```
┌─ Browser tab ─────────────────────────────────────────────┐
│ POST /api/v1/chat/stream   (SSE, 1 条, 所有 running 会话)  │
│ POST /api/v1/chat/send     (JSON, 发送/regenerate/continue)│
│ GET  /chat/runs/{id}/...   (大帧按需拉取)                  │
│ GET  /api/v1/logs/live     (admin, 1 条, 独立)             │
└───────────────┬───────────────────────────────────────────┘
                │
┌───────────────▼───────────────────────────────────────────┐
│ UnifiedStreamHub (dashboard/services)                     │
│  · connections: connection_id -> ConnectionState          │
│  · per-session: control/data 双队列 + 环形缓冲             │
│  · dispatcher: 会话间轮转，control 优先                    │
│  · cursors / epoch / resync_required                      │
└───────┬───────────────────────────────▲───────────────────┘
        │ run_started / 帧               │ system 事件
┌───────▼───────────────────────────────┴───────────────────┐
│ ChatService（既有）                                        │
│  · chat_runs / chat_runs_by_session                       │
│  · _consume_chat_run（与订阅者解耦，断流不影响落库）        │
└───────┬───────────────────────────────▲───────────────────┘
        │                               │
┌───────▼───────────────────────────────┴───────────────────┐
│ WebChatQueueMgr（既有）                                    │
│  · subscribe_system / put_system_event（fan-out）          │
└───────────────────────────────────────────────────────────┘
```

### 2.1 连接模型

- 每个 tab 一条 `POST /api/v1/chat/stream`；管理员另有 1 条日志流。
- 一条流承载该账号所有 owned running 会话的帧 + 会话级 system 事件。
- 一元操作（发送、停止、切换）走普通 HTTP，不占长连接。

### 2.2 组件职责

| 组件 | 职责 |
|---|---|
| `UnifiedStreamHub` | 连接注册、订阅集合、per-session 队列、轮转调度、环形缓冲、cursor 重放 |
| `ChatService` | run 生命周期、`_consume_chat_run`、`run_started` 广播、大帧 ref 生成 |
| `WebChatQueueMgr` | system 事件 fan-out（沿用现有 `subscribe_system`） |
| 前端 `useMessages` | 统一流消费、按 `session_id` 分发、物化策略、发送路径 |
| 前端 `Chat.vue` / `ChatMessageList.vue` / `ChatScrollMarkers.vue` | 渲染层性能改造 |

## 3. 后端设计

### 3.1 文件清单

| 文件 | 变更 |
|---|---|
| `astrbot/dashboard/services/chat_service.py` | 新增 `UnifiedStreamHub`、统一流生成器、`run_started` 广播、大帧 ref、epoch |
| `astrbot/dashboard/api/chat.py` | 新增 `POST /chat/stream`、`POST /chat/send`、大帧拉取端点 |
| `astrbot/core/platform/sources/webchat/webchat_queue_mgr.py` | 增加 run 注册/注销的轻量通知（或由 `ChatService` 直接调用 hub） |
| `tests/unit/test_unified_stream_hub.py` | 队列、调度、重放、背压 |
| `tests/test_unified_stream_integration.py` | 6 个 fake run + 单流、跨 tab、重连 |

### 3.2 端点契约

#### `POST /api/v1/chat/stream`

- Auth：`require_chat_scope`。
- Body：

```json
{
  "epoch": "2026-11-12T09:00:00Z",
  "cursors": {
    "webchat!ravic!conv-a": 128,
    "webchat!ravic!conv-b": 64
  }
}
```

- 响应：`text/event-stream`，`Cache-Control: no-cache`，`X-Accel-Buffering: no`。
- 服务端按账号自动订阅所有 owned running 会话；body 不接受任意 `session_ids`，避免越权订阅。
- `epoch` 只用于校验；缺失或与当前服务实例不一致时忽略 `cursors`，全部走 snapshot。

#### `POST /api/v1/chat/send`

- Auth：`require_chat_scope`。
- Body：

```json
{
  "mode": "send",
  "session_id": "webchat!ravic!conv-a",
  "message_id": "client-uuid",
  "content": [{ "type": "plain", "text": "..." }],
  "config_id": "...",
  "selected_provider": "...",
  "selected_model": "...",
  "thinking_effort": "...",
  "target_message_id": null,
  "source_message_id": null
}
```

- `mode`：`send` / `regenerate` / `continue`。
- 响应（HTTP 200）：

```json
{
  "status": "accepted",
  "session_id": "webchat!ravic!conv-a",
  "message_id": "client-uuid",
  "run_id": "client-uuid",
  "input_message_id": 123,
  "detail_path": "/#/chat/webchat!ravic!conv-a"
}
```

- 失败：HTTP 4xx/5xx + `{ "status": "error", "message": "..." }`。
- 复用现有 `prepare_*_payload` 校验；`POST /api/v1/chat`（OpenAPI）保持不变。

#### 大帧拉取

| 端点 | 用途 |
|---|---|
| `GET /api/v1/chat/runs/{run_id}/tool-results/{tool_call_id}` | run 存续期间的工具结果 |
| `GET /api/v1/chat/messages/{message_id}/tool-results/{tool_call_id}` | run 结束后的历史回查 |
| `GET /api/v1/chat/runs/{run_id}/snapshot` | `snapshot_ref` 指向的完整快照 |

三个端点都做归属校验（run 的 `username` 或消息所属会话的 owner）。

### 3.3 帧格式

复用现有 webchat payload，外层只加 `session_id` 与 `seq`：

```json
{
  "session_id": "webchat!ravic!conv-a",
  "seq": 128,
  "type": "plain",
  "data": "……",
  "streaming": true,
  "chain_type": "reasoning",
  "message_id": "client-uuid"
}
```

- `seq`：per-session 单调递增，由 hub 在 fan-out 时分配。
- `epoch`：只在首帧 `stream_ready` 中下发。
- `type` / `data` / `streaming` / `chain_type` / `message_id` 保持原样，前端 `processStreamPayload` 可原样复用。
- `stream_ready`：`{ "type": "stream_ready", "epoch": "..." }`。
- `resync_required`：`{ "type": "resync_required", "session_id": "...", "from_seq": N }`。

**control 集合**（走 control 队列）：

`stream_ready`、`run_started`、`end`、`error`、`interactive_choice`、`interactive_choice_resolved`、`goal_state_changed`、`shell_sessions_changed`、`chat_session_created`、`resync_required`。

**data 集合**（走 data 队列）：

`plain`（含 `chain_type` 为 `reasoning` / `tool_call` / `interactive_choice` 的载荷）、`complete`、`agent_stats`、`file_changes`、`llm_request_injections`、`message_saved`、`user_message_saved`、`run_snapshot`、`message_segment`。

### 3.4 运行注册与 fan-out

- `ChatService.chat_runs` 仍是唯一事实源；`chat_runs_by_session` 提供会话到 run 的索引。
- `UnifiedStreamHub` 维护 `connections: dict[str, ConnectionState]`，每个连接记录 `username`、`cursors`、`per_session` 队列。
- 连接建立时：
  1. 生成连接 id，写入 hub；
  2. 发送 `stream_ready`；
  3. 对账号下所有 owned running run 发送 `run_started` + `run_snapshot`；
  4. 对每个 running 会话调用 `webchat_queue_mgr.subscribe_system(session_id)`。
- 新 run 注册时（`build_chat_stream`、`register_synthetic_chat_run`、`inject_cron_turn`）：
  - 调用 `hub.publish_run_started(username, session_id, run_id, input_message)`；
  - 向该账号所有连接 fan-out；发起 tab 按 `message_id` 复用本地记录。
- run 结束时：发送 `end`，从 `runningBySession` 移除；环形缓冲保留一段时间用于重放。
- 连接断开时：取消所有 system 订阅，移除连接与队列。

### 3.5 调度与背压

每个连接、每个会话维护两条队列：

```
control: deque[Frame]        # 无界（频率极低）
data:    deque[Frame]        # 有界：2000 帧 / 2MB，先到为准
```

调度器：

1. 会话间轮转（round-robin）；
2. 每个会话先 drain control，再最多发送 8 个 data 帧或 64KB；
3. 发送后推进该会话的 `seq` 与环形缓冲。

合并规则：

- 同会话、同 `message_id`、同 `chain_type` 的连续 `plain` 帧，在入队时合并为一个帧（文本拼接），上限 64KB；
- control 帧不合并；
- `tool_call` / `interactive_choice` / `message_segment` 不合并。

溢出规则：

- data 队列满时丢最旧帧，`dropped += 1`；
- 每个丢弃突发只入队一次 `resync_required`（带当前 `from_seq`）；
- `dropped` 与队列深度写入 debug 日志。

心跳：15s 内无任何帧发送时，写 `: heartbeat\n\n`。

### 3.6 大帧 out-of-band

统一规则：**序列化后 >64KB 的帧，payload 替换为 ref**。

| 原载荷 | 替换为 | 拉取方式 |
|---|---|---|
| `tool_call` 的 `result` | `{ "result_ref": { "run_id", "tool_call_id", "bytes" }, "result_preview": "前 4KB" }` | `GET /chat/runs/{run_id}/tool-results/{tool_call_id}`；run 结束后用 message 版端点 |
| `run_snapshot` 的 `content` | `{ "snapshot_ref": { "run_id", "bytes" } }` | `GET /chat/runs/{run_id}/snapshot` |
| `complete` 的最终文本 | `{ "text_ref": { "run_id", "message_id", "bytes" } }` | 先取 snapshot，再取其中文本 |

保留 4KB 预览是为了让工具卡在未展开时仍有内容，不出现空白卡。

### 3.7 重连与环形缓冲

- 客户端重连时带 `{ epoch, cursors: { session_id: last_seq } }`。
- `epoch` 不一致：清空 cursors，全部走 snapshot。
- 每个会话的环形缓冲保存**已序列化的 wire frame**，避免重放时二次编码。
- 容量：2000 帧 / 60s / 2MB，先到为准；被淘汰后 `min_seq` 前移。
- cursor ≥ `min_seq`：重放 `seq > cursor` 的帧；
- cursor < `min_seq` 或会话无缓冲：发送该会话所有 active run 的 `run_snapshot` + `session_state`（goal / shell）。
- 重放期间新产生的帧继续入队，不阻塞。

### 3.8 发送路径

`POST /api/v1/chat/send` 的处理流程：

1. 按 `mode` 调用现有 `prepare_*_payload` 做校验与归一化；
2. 注册 `ChatRunState`，启动 `_consume_chat_run`；
3. 通过 hub 广播 `run_started`；
4. 立即返回 JSON（不等首帧、不等结束）。

竞态：

- `run_started` 可能早于 POST 响应到达；前端按 `message_id` 匹配本地乐观记录，不依赖响应里的 `run_id`。
- POST 失败时前端把本地气泡标记为错误；成功但长时间无帧时按 §8 处理（当前决策为不设超时，依赖心跳/重连）。

`POST /api/v1/chat`（OpenAPI 流式端点）不变；`open_api` 与 `remote_chat` 继续走旧路径。

### 3.9 兼容与回退

- 新端点全部为新增，旧端点不改签名、不改行为。
- 旧端点冻结：只修安全/数据正确性 bug，不回流新能力（大帧 ref、`run_started`）。
- 前端开关：
  - `localStorage["chat.streamMode"] = "unified" | "legacy"`，默认 `unified`；
  - `?stream=legacy` 覆盖，用于排查；
  - 切换后要求刷新页面。
- unified 模式下前端不打开：per-session 发送流、`system-stream`、resume stream、WebSocket。
- WS 传输菜单在 unified 模式下隐藏，代码保留一个版本。

## 4. 前端设计

### 4.1 统一流消费

- `useMessages` 增加 `startUnifiedStream()`：
  - `fetchWithAuth("/api/v1/chat/stream", { method: "POST", body: JSON.stringify({ epoch, cursors }) })`；
  - 用现有 `readSseStream` 解析；
  - 按 `session_id` 分发。
- 分发规则：

| 帧类型 | 已加载会话 | 未加载会话 |
|---|---|---|
| `run_started` | 物化/复用记录 | 只更新 `runningBySession` |
| `plain` / `complete` / `tool_call` / `agent_stats` / `file_changes` / `llm_request_injections` / `run_snapshot` / `message_segment` | `processStreamPayload` | 丢弃 |
| `interactive_choice` / `interactive_choice_resolved` | `processStreamPayload` + store | 只写 Pinia store + attention |
| `end` / `error` | 结算记录 + attention | 只更新 `runningBySession` + attention |
| `goal_state_changed` / `shell_sessions_changed` / `chat_session_created` | 现有处理 | 现有处理 |
| `resync_required` | 拉 snapshot | 忽略 |

- 重连：500ms 起指数退避，上限 10s；带 cursors；`epoch` 不一致时清空 cursors。
- 心跳：30s 无任何帧视为断线，主动重连。
- 活跃会话的帧按 rAF 合并后调用 `processStreamPayload`；control 帧立即处理。

### 4.2 物化策略

- 只有 `messagesBySession[session_id]` 已存在（本 tab 打开过）的会话才 materialize token。
- `run_started` 的 `input_message` 按 `id` 去重；已存在则复用。
- 未加载会话只维护 `runningBySession` 与 attention，切回时走 `loadSessionMessages` + `active_runs` resume。

### 4.3 发送路径

- `sendMessageStream` 改为 `sendMessage`：
  1. 创建本地乐观 exchange；
  2. `POST /api/v1/chat/send`；
  3. 成功只记录 `run_id`，不读响应流；失败结算气泡并展示错误。
- `continueEditedMessage` 与 `handleRegenerateMessage` 复用同一端点，分别传 `mode: "continue"` / `"regenerate"`。
- `isSessionRunning` 在 unified 模式下改读 `runningBySession`（`run_started` 加、`end` 删）。
- `activeConnections` 仅保留用于 legacy 模式；unified 模式下不再创建。

### 4.4 渲染层改造（Q1-B）

| 文件 | 改动 |
|---|---|
| `Chat.vue` | `watch(activeMessages, deep)` → `watch(streamRevision)`；`updateScrollMarkers` 200ms 节流 + 结构/结束立即；MutationObserver 去掉 `characterData`；`syncDistanceToBottom` 进 rAF |
| `ChatMessageList.vue` | `lastNonUserIndex` 预计算，`isMessageStreaming` O(1)；deep watch 拆成 `structureRevision`（历史迁移）与 `choiceRevision`（reconcile） |
| `ChatScrollMarkers.vue` | 不再接收 `replyPreview`；hover 时按 `marker.id` 现读，打开期间 200ms 刷新 |
| `useMessages.ts` | `ensureBotRecordVisible` 在 `botVisible === true` 时提前返回；`appendReasoningPart` 改增量拼接 |

这些改动只影响显示层，不改协议、不改模型上下文。

### 4.5 旧流关闭

- unified 模式不调用 `startSystemStream`、`restoreNextActiveRun` / `startResumeStream`、`getOrCreateChatWebSocket`。
- 传输方式菜单隐藏。
- 切换 `chat.streamMode` 后刷新页面。

## 5. 关键时序

### 5.1 首次连接

```
Client                          Server
  | POST /chat/stream {cursors}    |
  |------------------------------->|
  |<-- stream_ready {epoch} -------|
  |<-- run_started (per run) ------|
  |<-- run_snapshot (per run) -----|
  |<-- live frames ----------------|
```

### 5.2 发送一轮

```
Client                                Server
  | create optimistic user+bot record     |
  | POST /chat/send {mode:"send"}         |
  |-------------------------------------->|
  |<-- 200 {run_id, message_id} ----------|
  |<-- run_started {input_message} -------|  (可能早于 200)
  |<-- plain / tool_call / ... -----------|
  |<-- end -------------------------------|
```

### 5.3 切会话

- 连接不变；
- 目标会话若已加载，直接渲染 `messagesBySession[sid]`；
- 未加载则 `loadSessionMessages` + `active_runs` resume；
- 后台会话的帧继续写入 `messagesBySession`（若已加载）。

### 5.4 断线重连

```
Client                                Server
  | POST /chat/stream {epoch, cursors}    |
  |-------------------------------------->|
  |<-- stream_ready ----------------------|
  |<-- replay seq > cursor ---------------|  若缓冲覆盖
  |<-- run_snapshot ----------------------|  若缓冲不足
  |<-- live frames -----------------------|
```

## 6. 验收指标

| 指标 | 目标 | 测量方式 |
|---|---|---|
| 单 tab 连接数 | ≤2 | DevTools Network |
| 按钮请求 Queueing | < 10ms | DevTools Timing |
| 按钮请求 P95 | < 100ms | DevTools / Performance |
| 控制事件 P95 | < 200ms | 打点：服务端产生 → 前端处理 |
| 控制帧丢失 | 0 | 日志 + 前端断言 |
| per-token 主线程耗时 | 降 ≥10× | Performance 录制同一段回复 |
| Layout 次数 | ≤1 次/帧 | Performance |
| 500 条消息内存 | 稳定 | DevTools Memory |

## 7. 测试策略

### 7.1 后端单元

- 环形缓冲淘汰与 `min_seq`；
- `plain` 合并（同 message/chain_type，跨 chain_type 不合并）；
- control 优先于 data；
- cursor 重放与 snapshot 兜底；
- 大帧 ref 生成与归属校验；
- `resync_required` 每个丢弃突发只发一次。

### 7.2 后端集成

- 6 个 fake run 并发写入，单连接收到全部会话的帧；
- 两个连接（跨 tab）收到同一 run 的帧；
- 断开后按 cursor 重放；
- 断开超过缓冲后收到 snapshot；
- 大帧超过 64KB 时客户端收到 ref。

### 7.3 前端单元

- 按 `session_id` 分发；
- 未加载会话只处理 control/attention；
- `run_started` 按 `message_id` 去重；
- 发送失败结算气泡；
- `streamRevision` 触发滚动、`structureRevision` 触发迁移；
- `lastNonUserIndex` 等价性（连续 user、branch divider、tool-only 尾行）；
- `ChatScrollMarkers` 按需预览。

### 7.4 手动清单

6 会话并发 + 200 条历史，逐项检查：

1. 逐字输出；2. 首 token 转圈；3. 尾部大圈；4. 贴底跟随；5. 上滚停跟随；6. 标记条位置；7. 思考块展开；8. 工具卡状态；9. 选择框出现/点击；10. agent work 折叠；11. 分支切换；12. 切会话再回来不丢消息。

## 8. 风险与硬依赖

1. **不设发送超时**（Q4-C）：前提是 15s 心跳 + cursor/snapshot 重连可靠。任一失效会回到「思考中永远转圈」，需要重新评估。
2. **跨 tab 一致性只覆盖已加载会话**：这是 Q1-B 的刻意取舍。
3. **单连接 HOL**：靠大帧 ref、control 优先、会话轮转缓解；若测量显示仍明显，再启用服务端 delta 合并（16~33ms），触发条件为「单帧 >16ms 或队列积压持续 >1s」。
4. **环形缓冲内存**：2000 帧 / 60s / 2MB 每会话，6 个运行会话约 12MB。
5. **旧端点冻结**：新能力不回流，回退后行为与现在一致。

## 9. 实施顺序（tickets）

| # | 内容 | 可独立验收 |
|---|---|---|
| 1 | 前端 O(1) 流式判定 + deep watch 拆分 | 是 |
| 2 | 前端布局/预览/数据层 O(n) 修复 | 是 |
| 3 | 后端 `UnifiedStreamHub` + 环形缓冲 + 背压 + 端点 | 是（单元/集成） |
| 4 | 前端统一流消费 + 发送路径切换 | 是 |
| 5 | 跨 tab / attention / 回退开关 / WS 隐藏 | 是 |
| 6 | 压测与验收 | 是 |

## 10. 附录：决策记录

| 轮次 | 问题 | 结论 |
|---|---|---|
| 1 | 修复范围 | B：连接池 + 单会话渲染 |
| 1 | 后台会话实时性 | B：token 继续实时，走同一条流 |
| 1 | 传输载体 | A：单条 SSE 多路复用 |
| 1 | 多标签页 | A：每 tab 一条 |
| 1 | 兼容 | A：新端点 + 旧端点冻结 + 本地开关 |
| 2 | 订阅与承载 | A：所有 owned running + system；日志不并入 |
| 2 | 背压 | A+B+C：有界队列 + 合并 + resync |
| 2 | 大帧 | B：>64KB out-of-band |
| 2 | 控制优先 | A：control/data 双队列 |
| 2 | 重连 | A+B：cursor 增量 + snapshot 兜底 |
| 2 | 服务端合并 | B：本次不做，量完再定 |
| 2 | 节奏/旧端点 | 直接统一流；旧端点冻结一版；localStorage + query |
| 2 | 验收指标 | 按推荐 |
| 2 | 标记条预览 | A：按需计算 |
| 3 | 发送路径 | B：普通 POST 返回 run_id，输出走统一流 |
| 3 | 端点形态 | A：POST /chat/stream + cursors body + 自动订阅 |
| 3 | 帧格式 | A：复用 payload + session_id/seq/epoch |
| 3 | 重放缓冲 | A：2000 帧 / 60s |
| 3 | 跨 tab | A：fan-out |
| 3 | WS 模式 | A：隐藏，代码留一版 |
| 3 | 默认/可观测 | 默认全量开启；debug 日志 |
| 3 | useMessages O(n) | A：包含 |
| 3 | 大帧契约 | A：result_ref + 按 run/message 回查 |
| 4 | 未加载会话 | B：只处理 control/attention |
| 4 | run_started | A：所有 run 都发，带 input_message |
| 4 | 旧流关闭 | A：全关，切换刷新 |
| 4 | 首帧超时 | C：不设超时 |
| 4 | 缓冲字节上限 | A：2000 帧 / 60s / 2MB |
