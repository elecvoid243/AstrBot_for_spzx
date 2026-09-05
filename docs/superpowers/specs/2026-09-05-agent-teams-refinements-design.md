# Agent Teams 体验与配置增强设计

- 日期：2026-09-05
- 前作：[2026-09-05-agent-teams-design.md](./2026-09-05-agent-teams-design.md)（主体功能，已随 Plan 1-3 落地）
- 状态：待评审

## 1. 背景与目标

Agent Teams 主体功能已落地（团队/成员管理、DAG 与自动双编排、多窗格监控）。本设计解决使用中暴露的四类问题：

1. **节点配置粒度不足**：成员只能绑人格与模型，无法复用 AstrBot 的完整配置档案（不同节点需要不同的工具调用次数、工具超时等 Agent 参数）；且人格/工具/Skill 缺少"档案默认之上临时覆盖"的能力。
2. **前端交互缺陷**：结果引用只能手写 `{{节点ID}}`；保存失败时后端错误原因不显示（审计出 8 处吞错误点）；编辑器观感粗糙。
3. **执行过程只读**：用户无法在监控页对成员中途打断/追加指令；`ask_user_choice` 弹出的交互选择框无法在 Teams 页渲染与回应。
4. **查看不便**：节点执行内容无法全屏细看。

### 1.1 已确认决策

| 决策点 | 结论 |
|---|---|
| 边的语义 | **边 = 数据流**：连线即自动注入直接前驱节点的完整结果；`{{节点ID}}`/`{{input}}` 保留用于显式控制插入位置 |
| 节点打断语义 | 打断 = 停止该成员当前回合，节点进入**独立的"已中断"态**（不进失败策略、不级联跳过）；run 暂停，由用户在对话框中决定重派/跳过 |
| 成员发言语义 | **复用 chat 发送链路**：成员运行中 → 服务端 follow-up 捕获合并进当前回合；成员空闲 → 注入新回合。零新机制 |
| 编辑器视觉 | **专业工作流工具风**：自定义节点卡片、拖拽入画布、右侧属性抽屉、网格背景/小地图/缩放控件、箭头连线 |
| 错误显示 | 统一错误提取工具 + 全量修复 8 处吞错误点 + 监控渲染节点级错误 |

## 2. 需求一：节点配置三层模型（档案绑定 + 人格覆盖 + 工具/Skill 覆盖）

### 2.1 现状机制（已核实）

- **配置档案**：每个档案是一个完整 `AstrBotConfig`（含 `agent_runner.config.misc.max_steps`、`tool_call_timeout`、`streaming_response`、档案级默认人格 `agent_runner.config.persona.persona_id`、provider 等）。`UmopConfigRouter`（`astrbot/core/umop_config_router.py:6`）维护 umo 模式→conf_id 路由（支持 `*` 通配，持久化于 sp 键 `umop_config_routing`，`update_routing_data()` 可运行时全量改写）；`AstrBotConfigManager.get_conf(umo)`（`astrbot_config_mgr.py:156`）按 umo 解析档案，未命中回退 default。**EventBus（`event_bus.py:39-56`）按 umo 把事件路由到该档案专属的 PipelineScheduler**——每个档案的调度器拥有独立的 Agent 阶段实例（以该档案配置初始化，`internal.py:53-80`）。因此：**把成员会话的 umo 写入路由表，该成员的所有回合即自动采用档案的全部 Agent 参数，无需任何执行层改动**。
- **人格优先级**（`persona_mgr.py:83` `resolve_selected_persona`）：会话规则强制（sp `session_service_config.persona_id`，由会话规则 API 写入）→ 会话对话绑定（conversation.persona_id，Plan 1 建成员时已写）→ 档案默认。**"档案默认软件开发专家、节点指定测试专家"由既有优先级天然满足**。
- **工具/Skill 白名单**：Persona PO 的 `tools`/`skills`（None=全部、[]=禁用）在 `build_main_agent` 的 `_ensure_persona_and_skills` 中过滤 `req.func_tool` 与技能菜单。目前不存在会话级/请求级覆盖。

### 2.2 设计：成员配置三字段

成员数据结构（`agent_teams.members` JSON）新增三个可选字段：

```
{ ..., config_profile_id?: str | null,   # 绑定配置档案（umop 路由）
  persona_id 已有（conversation 绑定，即"覆盖档案默认人格"）
  tool_overrides?: { mode: "all"|"whitelist"|"blacklist", tools: string[] } | null,
  skill_overrides?: { mode: "all"|"whitelist", skills: string[] } | null }
```

- **绑定档案**（可选）：成员创建/编辑时从档案列表选择。写入 `UmopConfigRouter`（以成员 umo 为精确键；读取现有路由→合并→`update_routing_data`）。成员删除/团队删除时清理对应路由条目。校验 conf_id 存在于 `AstrBotConfigManager.abconf_data`。不绑定 → 沿用默认档案（现状）。
- **人格覆盖**（可选）：沿用现有 conversation persona 绑定；成员编辑 UI 用 `PersonaSelector`（`dashboard/src/components/shared/PersonaSelector.vue`，已被 `ConfigItemRenderer`/`SubAgentPage` 使用）替换现在的简单下拉，支持"跟随档案默认"空选项。
- **工具/Skill 覆盖**（可选）：三种模式——`all`（不限制，跟随人格/档案）、`whitelist`（仅列表内）、`blacklist`（排除列表内，仅工具）。工具候选列表来自 `subagents/available-tools` 同源的工具清单 API（`tools_service`），Skill 列表来自 `skills_service`。

### 2.3 运行时生效路径（工具/Skill 覆盖）

覆盖是**成员属性**（对该成员会话上发生的所有回合生效——含 Teams 派发回合与用户在 chat 页的直接对话），因此采用与本功能既有桥接模式一致的 **registry** 方案，而非随回合携带（extras 只存在于合成回合，无法覆盖直接对话回合）：

1. `astrbot/core/agent_team_tools.py` 同模块新增 `MemberOverrideRegistry`（按 umo 键的哑注册表）：`set_overrides(umo, tool_overrides, skill_overrides)` / `clear(umo)` / `get(umo)`。
2. Dashboard 侧：成员创建/编辑/删除时调用（`AgentTeamService` 持有 `core_lifecycle` 引用，直调注册表；团队删除时逐一 clear）。
3. `build_main_agent` → `_ensure_persona_and_skills`：在既有人格过滤**之后**应用覆盖（约 8 行）——`tool_overrides.mode=whitelist` 时把 `req.func_tool` 过滤为列表内工具；`blacklist` 时 `remove_tool` 掉列表内工具；`skill_overrides.whitelist` 时覆盖传给 `build_skills_prompt` 的技能列表。
4. 配置档案绑定走 `UmopConfigRouter`（见 §2.2），对成员会话上所有回合自动生效（EventBus 按 umo 路由调度器），无需执行层改动。

### 2.4 UI：成员编辑对话框改造

`MemberAddDialog` 扩展为"添加/编辑成员"双模式（对齐 TeamCreateDialog 的编辑模式形态），分三段：

- **身份**：名称、（编辑态只读 member_id）
- **执行配置**：配置档案下拉（"默认配置"空选项 + 档案列表）、PersonaSelector（"跟随档案默认"空选项）、模型覆盖（既有 provider 下拉）
- **能力覆盖**（折叠面板）：工具三态选择（radio: 全部/仅允许/排除 + 工具多选）、Skill 白名单（多选）

团队创建对话框中的成员行保持精简（名称+人格），创建后经成员编辑完善配置——避免创建对话框过度膨胀。

## 3. 需求二：前端体验修复

### 3.1 错误可见性（审计结论：8 处）

后端事实（已核实）：Teams 的全部校验/冲突错误都是 **HTTP 400/409 + `{status:'error', message}` body**（`agent_teams.py:44-47`），axios 会抛异常——`useAgentTeams.ts` 的 envelope 分支（HTTP 200 error）是死代码，catch 分支只显示 axios 通用文案。`useAgentTeamsRun.ts` 是唯一正确的实现（读 `err.response.data.message`）。

**修复方案**：

1. 新建 `dashboard/src/utils/apiError.ts`：`extractApiErrorMessage(err: unknown, fallback: string): string` —— 依次读 `err?.response?.data?.message`、429 时 `normalizeAxiosError` 抛出的字符串、`err?.message`、fallback。配 vitest 单测（含 429 字符串特例）。
2. 逐点替换（全部改为显示后端原因）：
   - `useAgentTeams.ts` `unwrapEnvelope` catch 分支（影响全部团队/工作流 CRUD）
   - `WorkflowEditor.vue` 保存（composable 修好后自动受益；前端校验 banner 保留）
   - `TeamCreateDialog.vue` / `MemberAddDialog.vue` 保存
   - `AgentTeamsPage.vue` removeMember
   - `RunsHistory.vue` HTTP catch；`MemberAddDialog.loadOptions` 静默失败改为 toast
3. **监控渲染执行期错误**（此前完全不可见）：
   - 失败/已中断节点：`AgentWindow` 头部状态 chip 的 title 与节点对话框内联展示 `nodeStates[nodeId].error`（如 "reply timeout"、"member missing"、异常文本）；`TeamsFlowCanvas` 的节点 prop 类型补 `error`，失败节点悬浮 tooltip 显示原因
   - run 级横幅：`RunMonitor` 顶部在 `state.lastError` / `state.stoppedReason`（非 "done"/"user stop"）时渲染可关闭的警告横幅
   - SSE 附加持续失败（重连 5 次耗尽）：toast 一次 + 横幅，替代 console-only

### 3.2 边 = 数据流（自动注入前驱结果）

- **执行语义**（`agent_team_dag.py` + `DAGRunner`）：渲染节点任务时，若模板未显式引用某直接前驱，自动在任务文本末尾追加"上游结果"块（**仅含已完成（done）且持有结果的前驱**——被跳过/中断的前驱不注入）：

  ```
  [上游结果]
  ◆ <节点标题|成员名> (n1)：
  <前驱 result 文本，按 inject_max_length 截断>
  ```

  显式写了 `{{n1}}` 的前驱不再重复注入；全部前驱都被显式引用则不追加。`render_task` 签名扩展：`render_task(template, run_input, results, max_length, *, auto_inject_predecessors: list[tuple[str, str]] | None)`（传入 `(node_id, 显示名)` 列表）。老模板（含 {{id}}）行为不变。
- **编辑器 UI**：选中节点时，检查器显示"上游节点"chips（直接前驱，点击插入 `{{节点ID}}`）与 `{{input}}` chip；模板中引用了**未与该节点相连**的节点 ID 时行内警告（复用 `findCycle` 同层的校验函数 `unreferenced_placeholders`）。
- **文档/提示**：`nodeTaskHint` 文案更新为"连线即自动注入上游结果；{{节点ID}} 用于显式控制位置"。

### 3.3 编辑器专业工作流工具风重设计

- **自定义节点**：Vue Flow 自定义节点类型 `member`（`template #node-member`）：成员色点 + 名称 + 任务预览两行（hover 展开全文）+ 状态环（编辑态灰/monitor 态五色 + interrupted 橙）+ 未绑定成员红边。节点尺寸固定宽 220px。
- **成员面板**：左侧栏成员列表支持 **HTML5 拖拽入画布**（dragstart 带 member_id，画布 `@drop` 换算画布坐标 `project()` 后 `addNode`）；原"添加节点"按钮保留为备选。
- **属性抽屉**：选中节点的检查器从浮层改为右侧固定抽屉（`v-navigation-drawer` location="right"，临时态），含：成员切换、任务模板（变量 chips：`{{input}}` + 各直接前驱）、上游/下游列表、删除节点。
- **画布观感**：`<Background>`（点阵网格）、`<Controls>`、`<MiniMap>`（pannable，monitor 模式只读）、连线 `markerEnd` 箭头 + 选中高亮；monitor 态运行中的边流动画（CSS stroke-dashoffset）。全部暗色适配（跟随现有 theme vars）。
- **工具条**：顶部工具条重排（工作流选择 + 名称 ｜ 撤销/重做不做（YAGNI）｜ 校验状态徽标 ｜ 保存）。

## 4. 需求三：节点交互对话框（打断 / 追加指令 / 交互选择框）

### 4.1 总体形态

每个成员窗格（AgentWindow）增加"展开"按钮 → **节点对话框**（`v-dialog`，宽度 max 900px；需求四的全屏即同对话框的 `fullscreen` 切换按钮）。对话框 = 该成员会话的"迷你 chat 页"：

- **上半部**：完整执行历史（本 run 内该成员的 sent / 流式 / 回复 / 工具活动，含 thinking 块——见 4.3 的数据面升级）
- **中部**：`interactive_choice` 渲染（复用 `InteractiveChoiceBox`）
- **底部**：输入框 + 发送按钮（追加指令/新消息）+ 打断按钮（运行中）+ 全屏切换

### 4.2 数据面：附加成员会话的 chat run 流

关键事实（已核实）：成员的合成回合通过 `register_synthetic_chat_run` 成为**一等 chat run**，`deliver` 返回的 `message_id` 即 run_id；`ask_user_choice` 插件把选择框直推到该 run 的 back_queue，`chat_service._consume_chat_run` 消费后把 `interactive_choice` part 发布到 **run stream** 订阅者——即：**附加成员的 run stream 即可获得完整保真数据**（含交互选择框、follow_up_captured、工具调用明细），无需修改 ask_user_choice 插件，无需扩展 Teams SSE。

实现：新 composable `useMemberRunStream`（从 `useMessages` 提取可复用部分——`readSseStream` 已在两处复制，本次顺带收敛为从 `useMessages` 导出共享）：

- `attach(sessionId, runId)`：`fetchWithAuth(chatApi.resumeRunStreamUrl(runId))` → `processStreamPayload` 喂入本地 `botRecord`（其结构即 ChatRecord，渲染组件全部兼容）→ 暴露 `{record, choiceParts, running}`。
- Teams 后端在 `message.sent/reply` 事件与 dispatch 中已带 `session_id`；`DAGRunner._execute_node` / AutoOrchestrator 派发处把 `message_id`（即 run_id）附加到 `node_status` 与 `message` 事件（+2 行），reducer 存入 `MemberWindowState.runId`——对话框由此知道要附加哪个 run。
- 历史范围：对话框的"执行历史"分两层——**Teams 层**（本 run 内该成员的 sent/流式/回复，来自 reducer 累积；重启后从持久化 node_states/rounds 恢复摘要）+ **chat 层**（附加成员 run 流获得完整保真记录）。v1 简化：运行中附加实时流；已结束/重启恢复的运行显示 Teams 层摘要 + "在 Chat 页查看完整会话"跳转链接（成员 session_id 已知）；完整历史回看列为后续项。

### 4.3 成员发言（追加指令 / 新消息）

输入框发送 → **直接调用 chat 页同款发送 API**（`chatApi` 的 send → `build_chat_stream`），目标会话为成员 session_id：

- 成员运行中：管道 `try_capture_follow_up`（`internal.py:217-233`）捕获为 FollowUpTicket 合并进当前回合（发送者同为 dashboard 用户，匹配捕获条件）；`follow_up_captured` 事件出现在成员 run 流上，对话框即时把用户气泡挂到运行记录下（复用 chat 页的既有分支）。
- 成员空闲：作为新回合注入（`build_chat_stream` 正常创建 run），对话框 attach 新 run。
- 忙碌态 UI：`runState.busySessionIds` 已有；发送不禁用（与 chat 页一致），本地提示"运行中，将作为追加指令合并"。

### 4.4 打断（独立"已中断"态）

- **后端**：新端点 `POST /agent_teams/runs/{run_id}/members/{member_id}/interrupt`（v1+legacy 双注册）。`AgentTeamRunService.interrupt_node(username, run_id, member_id)`：
  1. 所有权校验（复用 `_require_run`）；
  2. `active_event_registry.request_agent_stop_all(member.umo)` —— 成员回合被 "Stop output." 注入并 abort（历史仍保存，chat 页可见）；
  3. DAG 模式：定位该成员当前 `running` 的节点 → `node_states[nid].status = "interrupted"`（`error="用户中断"`）→ run 置 `paused` + emit `node_status`/`paused {reason: "node interrupted", node_id}`；持久化。完成判定与 `_progress` 计数纳入 `interrupted`（有 interrupted/paused 节点时 run 不判 completed）。`_execute_node` 的 collect 返回路径遇已 interrupted 的节点**不覆盖**状态。
  4. Auto 模式：无节点概念——只停成员回合，不打断 run；rounds 不变。
- **节点状态机**：node status 增加 `interrupted`（枚举、`_progress`、前端着色橙、retry/skip 可用性：**retry/skip 均可用于 interrupted**）。
- **前端**：对话框"打断"按钮（运行中显示）→ 调 interrupt API；窗格与 DAG 节点显示"已中断"；run 进入 paused 后既有控制条（继续/重试/跳过）自然可用。

### 4.5 interactive_choice 回应

- 对话框渲染 `InteractiveChoiceBox`（props：`part`、`umo=member.umo`、`isDark`）——该组件与 `interactiveChoiceStore` 本就按完整 UMO 键控，天然支持多会话并存。
- 用户点击选项 → `store.submitChoice(member.umo, request_id, payload)` → `POST /api/chat/interactive-choice/{request_id}` → future resolve → 成员的 `ask_user_choice` 工具返回 → Agent 继续。**复用 chat 页的提交处理器逻辑**（从 `ChatMessageList.vue` 的 `onInteractiveChoiceSubmit`/cancel 抽为共享函数供两处使用）。
- 成员会话的 interactive_choice 持久化状态（localStorage 按 umo）互不干扰。

## 5. 需求四：全屏

对话框头部"全屏"切换 → `v-dialog` 的 `fullscreen` prop 切换（保留关闭按钮与 ESC）。全屏下历史区占满、输入框贴底（chat 页布局）。窗格上的展开按钮与对话框内切换共用同一状态。

## 6. API 变更清单

| 变更 | 类型 | 说明 |
|---|---|---|
| `PATCH /agent_teams/{team_id}` 成员字段扩展 | 修改 | members JSON 增 `config_profile_id` / `tool_overrides` / `skill_overrides`；档案列表与 Skill 列表**复用既有 API**（`/api/v1/config-profiles` 列表含 id+name；skills 列表用 skills 服务既有端点），不新增代理端点 |
| `POST /agent_teams/runs/{run_id}/members/{member_id}/interrupt` | 新增 | 打断成员回合；DAG 模式标记节点 interrupted + run paused |
| `node_status` 事件 | 扩展 | 新增 `run_id` 字段（成员 chat run id）；`interrupted` 状态值 |
| OpenAPI YAML | 更新 | 上述变更 + `pnpm generate:api` |

## 7. 错误可见性修复清单（验收用）

| # | 位置 | 现状 | 修复后 |
|---|---|---|---|
| 1 | `useAgentTeams.ts` unwrapEnvelope catch | axios 通用文案 | 后端 message |
| 2 | `WorkflowEditor.vue` 保存 | 同上（经 composable） | 同上 + banner |
| 3 | `TeamCreateDialog.vue` 保存 | 同上 | 后端 message |
| 4 | `MemberAddDialog.vue` 保存 / loadOptions | 通用文案 / 静默 | 后端 message / toast |
| 5 | `AgentTeamsPage.vue` removeMember | 通用文案 | 后端 message |
| 6 | `RunsHistory.vue` HTTP catch | 通用文案 | 后端 message |
| 7 | 监控节点级 error / lastError / stoppedReason | 完全不渲染 | chip title + 对话框内联 + run 横幅 |
| 8 | SSE 附加持续失败 | console-only | toast + 横幅 |

## 8. 测试策略

- **后端 pytest**：`render_task` 自动注入（显式引用不重复、全显式则不追加、截断）；`interrupt_node`（DAG 节点 interrupted + run paused + collect 不覆盖；auto 模式仅停成员）；成员配置路由写入/清理（umop 路由 mock 或真 sp）；工具/Skill 覆盖 extra 透传（webchat_adapter + build_main_agent 层的单测，沿 registry 测试的桥接模式）。
- **前端 vitest**：`extractApiErrorMessage`（含 429 字符串）；8 处错误点逐点断言（mock axios rejection 携带 `response.data.message`）；reducer：`run_id`/`interrupted` 折叠；RunMonitor：auto 显示切换、interrupt 按钮、错误横幅；WorkflowEditor：拖拽入画布、变量 chips、未连接引用警告。
- **手工验收**：成员绑"配置1"（max_steps=3）→ 该节点 3 步即收尾；打断→"已中断"→改任务重试；ask_user_choice 在对话框出现→点击→Agent 继续；发言运行中被合并（Agent 下一动作确认收到）。

## 9. 实施分期

1. 错误可见性全量修复（§3.1，独立可验收、立竿见影）
2. 节点配置三层模型（§2：后端路由/覆盖 + 成员编辑 UI）
3. 边=数据流（§3.2 后端注入 + 编辑器变量 chips/警告）
4. 节点交互对话框（§4+§5：useMemberRunStream、发言、打断+interrupted 态、InteractiveChoiceBox、全屏）
5. 编辑器视觉重设计（§3.3，自定义节点/拖拽/抽屉/画布组件）

## 10. 已否决的备选方案

| 备选 | 否决理由 |
|---|---|
| 为工具/Skill 覆盖创建临时 Persona | 污染人格列表；覆盖是成员属性，随合成回合走 extra 更内聚 |
| 扩展 Teams SSE 携带完整 parts/choice（替代附加 run 流） | Teams 流与 chat run 流双轨重复；run 流已有一等管道与全部组件兼容性，复用零后端改动 |
| 修改 ask_user_choice 插件镜像 system 流 | 该插件在 data/plugins（用户数据目录），不应由本功能改动；run 流路径已覆盖 |
| 打断→立即重派 | 用户已否决（浪费调用；选独立 interrupted 态由用户决定） |
| 中断语义走失败策略 pause | 用户已否决——interrupted 不进失败策略，避免与真实失败混淆 |
