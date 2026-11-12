# ChatUI 单连接流式架构（Unified Stream）

- Date: 2026-11-12
- Status: Ready for agent
- Tracker: GitHub `elecvoid243/AstrBot_for_spzx`（本机无 `gh`、无 remote，尚未发布）
- Detailed design: `docs/superpowers/specs/2026-11-12-chatui-unified-stream-design.md`

## Problem Statement

ChatUI 用户同时开 5~6 个会话、每个都在流式输出时，页面会明显卡顿，点按钮（停止、切会话、发送、附件）经常没有反应，要等某条流结束才恢复。即使只开一个长会话，流式输出也会越用越卡，滚动和输入逐渐变得迟钝。

用户需要的不是「少开几个会话」，而是在多会话并行时仍然保持可操作，并且长会话的流式体验不随历史增长而退化。

## Solution

ChatUI 只保留一条流式连接：所有运行中的会话、所有会话级系统事件都复用这一条 SSE，前端按 `session_id` 分发。发送、停止、切会话改走普通 HTTP，不再各自占一条长连接。

后台会话的 token 继续实时到达，切回时立即可见；未在本标签页打开过的会话只推送需要人介入的事件和运行状态，不浪费带宽和内存。大体积的工具结果和快照改成按需拉取，避免一个大会话堵住其他会话。

同时去掉渲染层的每 token 全量扫描与强制重排：流式判定改为 O(1)，deep watch 改为显式信号，滚动标记条改为节流更新，回复预览改为悬浮时按需计算。流式文字、滚动跟随、思考块、工具卡、选择框等视觉行为保持不变。

## User Stories

1. 作为 ChatUI 用户，我希望同时开 5~6 个会话流式输出时页面不卡顿，以便我可以并行处理多个任务。
2. 作为 ChatUI 用户，我希望多会话流式时点「停止」「切会话」「发送」能立即响应，以便我不用等某条流结束。
3. 作为 ChatUI 用户，我希望切走一个正在流式的会话再切回来时能看到期间到达的全部输出，以便我不丢内容。
4. 作为 ChatUI 用户，我希望后台会话的输出继续实时到达，以便切回时不需要等待重新加载。
5. 作为 ChatUI 用户，我希望侧边栏对任何正在运行的会话显示运行指示，包括在另一个标签页发起的会话，以便我知道哪里还有工作在跑。
6. 作为 ChatUI 用户，我希望选择框、审批请求等需要人介入的事件在会话未打开时也能提醒我，以便我不会错过阻塞中的回合。
7. 作为 ChatUI 用户，我希望后台会话结束时收到完成提醒，以便我知道什么时候切回去看结果。
8. 作为 ChatUI 用户，我希望流式文字仍然平滑出现，以便升级后视觉体验没有退化。
9. 作为 ChatUI 用户，我希望流式时自动贴底跟随，向上滚动后停止跟随，以便阅读历史时不被拉回底部。
10. 作为 ChatUI 用户，我希望右侧滚动标记条的数量、位置和当前轮高亮保持正常，以便我仍能快速跳转长会话。
11. 作为 ChatUI 用户，我希望悬浮标记条时能看到该轮的问题和回复摘要，且摘要是最新的，以便我确认要跳到哪一轮。
12. 作为 ChatUI 用户，我希望思考块、工具调用卡、agent work 折叠、分支分隔等渲染行为保持不变，以便升级不影响既有习惯。
13. 作为 ChatUI 用户，我希望 200 条以上历史的会话流式时仍然流畅，以便长对话不会越用越卡。
14. 作为 ChatUI 用户，我希望 500 条消息的会话保持内存稳定，以便标签页长时间打开不会持续变慢。
15. 作为 ChatUI 用户，我希望发送被拒绝时看到明确的错误，而不是一直停在「思考中」，以便我知道下一步该做什么。
16. 作为 ChatUI 用户，我希望在任意标签页都能停止正在运行的回合，以便我能及时收回控制权。
17. 作为 ChatUI 用户，我希望打开第二个标签页时，已经打开过的会话能看到一致的实时输出，以便多标签页协作不出现内容分歧。
18. 作为 ChatUI 用户，我希望新方案出问题时能一键回退到旧的流式行为，以便我不被卡住。
19. 作为 ChatUI 管理员，我希望日志流继续独立，以便日志量不会挤占聊天 token 的通道。
20. 作为 ChatUI 用户，我希望网络短暂断开后自动重连并补齐缺失的输出，以便我不需要手动刷新。
21. 作为 ChatUI 用户，我希望断线期间已经完成的回合在重连后仍然可见，以便我不丢已经产生的结果。
22. 作为 ChatUI 用户，我希望切换回退开关后页面行为明确（刷新一次），以便不会出现两套流同时工作的混乱状态。
23. 作为外部 API 使用者，我希望现有的流式聊天端点保持兼容，以便我的集成不会因为 ChatUI 改版而中断。
24. 作为开发者，我希望统一流通过 HTTP API 就能验证 fan-out、背压、重放和大帧引用，以便不依赖浏览器做后端回归。
25. 作为开发者，我希望前端分发、物化和发送路径能通过 `useMessages` 的公开接口验证，以便覆盖跨会话行为。
26. 作为开发者，我希望流式判定、折叠和按需预览能通过聊天组件测试验证，以便渲染层改动不引入回归。
27. 作为开发者，我希望丢弃帧和队列深度有日志可查，以便定位慢消费者。
28. 作为开发者，我希望大帧有明确的按需拉取端点，以便单连接不会被一个工具结果堵死。

## Implementation Decisions

### 传输与连接

- 每个标签页只保留一条聊天 SSE；管理员日志流保持独立，不并入。
- 新增统一流端点，客户端以 `POST` 提交 `{epoch, cursors}`，服务端按账号自动订阅所有 owned 且 running 的会话；不接受客户端任意指定 `session_ids`。
- 帧格式复用现有 webchat payload，外层只加 `session_id` 与 per-session 单调递增的 `seq`；首帧 `stream_ready` 下发 `epoch`。
- `epoch` 变化时客户端丢弃 cursors，全部走快照。

### 运行注册与 fan-out

- 所有 run（用户发起、cron 注入、goal-loop、collab）在统一流上广播 `run_started`，携带 `input_message`（触发该轮的那一条用户消息）。
- 发起标签页按 `message_id` 复用本地乐观记录；其他标签页据此物化记录。
- 同一账号的多个标签页都收到同一 run 的帧；跨标签页一致性范围限定为「本标签页打开过的会话」。
- 未在本标签页打开过的会话只处理 `run_started`（更新运行状态）、`end` / `error`、`interactive_choice`、`goal_state_changed`、`shell_sessions_changed`、`chat_session_created` 等控制事件；token 与工具帧丢弃。

### 调度与背压

- 每个连接、每个会话维护 control / data 两条队列；control 优先，data 有界。
- 会话间轮转调度，每个会话一次最多发送固定数量的 data 帧或固定字节数。
- 同会话、同 `message_id`、同 `chain_type` 的连续 `plain` 帧在入队时合并；control 帧和结构化帧不合并。
- data 队列满时丢该会话最旧帧，记录 `dropped`；每个丢弃突发只发一次 `resync_required`。
- 15 秒无帧时发送心跳注释行。

### 重连与重放

- 每个会话维护环形缓冲，保存已序列化的 wire frame；容量按帧数、时长、字节数三者的先到者计算。
- 客户端重连时提交 `{epoch, cursors}`；cursor 在缓冲范围内则补发增量，否则发送该会话所有 active run 的快照与会话级系统状态。
- 重放期间新帧继续入队，不阻塞。

### 大帧

- 序列化后超过阈值的帧，payload 替换为引用：工具结果带 `result_ref` 与短预览，快照带 `snapshot_ref`，最终文本带 `text_ref`。
- 提供按 run 与按 message 两种工具结果读取端点，以及快照读取端点；全部做归属校验。
- 预览保留少量前缀，避免工具卡在未展开时出现空白。

### 发送路径

- 新增 ChatUI 专用发送端点，普通 `POST` 返回 `{status, session_id, message_id, run_id, input_message_id, detail_path}`；输出全部走统一流。
- `mode` 支持 `send` / `regenerate` / `continue`，复用现有校验与归一化逻辑。
- 失败使用 HTTP 4xx/5xx 与 `{status: "error", message}`。
- 现有 OpenAPI 流式端点保持不变；`open_api` 与 `remote_chat` 不迁移。
- 发送后不设首帧超时；依赖心跳与重连补齐。这是硬依赖，心跳或重连失效时需要重新评估。

### 前端消费

- 统一流按 `session_id` 分发；已加载会话复用现有 `processStreamPayload`，未加载会话只走控制/提醒路径。
- 活跃会话的 data 帧按 rAF 合并后处理；control 帧立即处理。
- `runningBySession` 由 `run_started` / `end` 维护，替代 unified 模式下的 `activeConnections` 运行判定。
- 未加载会话的选择框写入 Pinia store 并触发 attention；切回后再渲染。
- unified 模式关闭 per-session 发送流、system-stream、resume stream、WebSocket；传输方式菜单隐藏。
- 回退开关使用 `localStorage` 与查询参数，切换后刷新页面。
- 默认所有用户开启统一流。

### 渲染层

- `Chat.vue` 的全量 deep watch 改为 `streamRevision` 显式信号；`updateScrollMarkers` 节流，结构变化与流结束立即执行；MutationObserver 去掉 `characterData`；`syncDistanceToBottom` 进入 rAF。
- `ChatMessageList.vue` 预计算最后一个非用户消息下标，流式判定改为 O(1)；deep watch 拆成 `structureRevision`（历史迁移）与 `choiceRevision`（选择框 reconcile）。
- `ChatScrollMarkers.vue` 不再每 token 接收回复预览，悬浮时按 marker id 现读，打开期间节流刷新。
- `useMessages` 的 `ensureBotRecordVisible` 在已可见时提前返回；`appendReasoningPart` 改为增量拼接，去掉每 chunk 的全量 reasoning 提取。
- 不引入消息列表虚拟化。

### 可观测性与回退

- `dropped`、队列深度写入 debug 日志。
- 旧端点冻结一个版本，只修安全与数据正确性问题，不回流新能力。
- 统一流异常时通过本地开关或查询参数回退到旧路径。

## Testing Decisions

### 好测试的标准

- 只断言外部可观察行为：客户端收到的帧、最终消息内容、运行状态、渲染出的流式状态、悬浮预览内容。
- 不断言内部实现细节（队列长度、watcher 调用次数、调度顺序），除非该细节本身就是契约（例如 control 优先、`dropped` 计数）。
- 性能指标不放进单元测试；用同一段回复做浏览器 Performance 前后对比，按验收指标判定。

### Seams（已与用户确认：3 个）

1. **后端 HTTP API**：统一流端点、发送端点、大帧读取端点。覆盖 hub 调度、fan-out、背压、重放、大帧引用。先例：`tests/test_webchat_system_stream.py`。
2. **前端 `useMessages` composable**：喂入伪造的 SSE 流，断言按会话分发、物化策略、发送路径、运行状态。先例：`useMessagesAgentFollowUp.spec.ts`、`useMessagesHistoryMerge.spec.ts`。
3. **前端聊天组件**：`ChatMessageList` / `ChatScrollMarkers`。断言流式状态、折叠行、按需预览。先例：`ChatMessageList.generationMark.spec.ts`、`ChatScrollMarkers.spec.ts`。

### 后端测试

- 环形缓冲淘汰、cursor 重放、快照兜底。
- 同会话 `plain` 合并、control 优先、data 溢出丢最旧、`resync_required` 去重。
- 6 个 fake run 并发写入，单连接收到全部会话的帧。
- 两个连接收到同一 run 的帧。
- 大帧超过阈值时客户端收到引用，读取端点返回完整内容并校验归属。

### 前端测试

- 按 `session_id` 分发；未加载会话只处理控制/提醒。
- `run_started` 按 `message_id` 去重。
- 发送成功只记录 run id，发送失败结算气泡。
- `streamRevision` 触发滚动，`structureRevision` 触发历史迁移。
- `lastNonUserIndex` 等价性：连续用户消息、分支分隔、只有工具调用的尾行。
- 标记条悬浮预览按需读取，不再依赖每 token props。

### 手动验收

- 6 个会话并发 + 200 条历史，逐项检查逐字输出、首 token 转圈、尾部大圈、贴底跟随、上滚停跟随、标记条、思考块、工具卡、选择框、agent work 折叠、分支切换、切回不丢消息。
- DevTools Network 确认单标签页连接数 ≤2，按钮请求无长时间 Queueing。
- Performance 录制同一段回复，对比 per-token 主线程耗时与 Layout 次数。

## Out of Scope

- 消息列表虚拟化。
- 服务端 delta 合并（本次不做；触发条件为单帧超过 16ms 或队列积压持续超过 1s）。
- `open_api` / `remote_chat` 迁移到统一流。
- 删除 WebSocket 传输代码（隐藏一个版本）。
- 替换 markstream 渲染组件。
- 未在本标签页打开过的会话的 token 物化。
- 跨设备一致性；每个标签页独立订阅。
- 改变浏览器每 origin 的连接数上限；本方案是绕开它，不是修改它。

## Further Notes

- 技术背景：传输层每个运行中的会话独占一条 SSE，切走不释放，叠加当前会话的 system-stream 和管理员日志流，很快打满浏览器对同一 origin 的 6 条 HTTP/1.1 连接；渲染层每个 token 都会触发两次全量 deep watch、多次尾部数组复制、两到三次同步布局和一次整段会话文本的正则扫描，成本随会话长度增长。
- 详细设计（端点、帧格式、调度、时序、风险）见 `docs/superpowers/specs/2026-11-12-chatui-unified-stream-design.md`。
- 本 spec 原计划发布到 GitHub issue tracker，但本机没有 `gh` CLI，仓库也没有 remote；待 tracker 可用后，用 `ready-for-agent` 标签发布本文。
- 硬依赖：发送不设首帧超时，前提是心跳与重连可靠；任一失效都要重新评估该决策。
- 跨标签页一致性只覆盖本标签页打开过的会话，这是刻意取舍。
- 旧端点冻结意味着统一流的新能力（大帧引用、`run_started`）不会回流；回退后的行为与当前版本一致。
