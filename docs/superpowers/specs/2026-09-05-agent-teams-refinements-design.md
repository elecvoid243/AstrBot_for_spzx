# Agent Teams 体验与配置增强设计

- 日期：2026-09-05
- 前作：[2026-09-05-agent-teams-design.md](./2026-09-05-agent-teams-design.md)（主体功能，已随 Plan 1-3 落地）
- 状态：已评审（含与另一候选方案的合并裁决，见 §1.2）
- 适用基线：`322057dd9..32d4f5962`（Agent Teams Plan 1-3 已落地实现）

## 1. 背景与目标

Agent Teams 主体功能已落地。本设计解决使用中暴露的四类问题：

1. **节点配置粒度不足**：只能给成员绑人格与模型，无法让**节点**复用 AstrBot 完整配置档案（不同节点需要不同的工具调用次数、工具超时等 Agent 参数）；人格/工具/Skill 缺少"档案默认之上临时覆盖"的能力。
2. **前端交互缺陷**：结果引用只能手写 `{{节点ID}}`；保存失败时后端错误原因不显示（审计出 8 处吞错误点）；编辑器观感粗糙。
3. **执行过程只读**：无法在监控页对成员中途打断/追加指令；`ask_user_choice` 的交互选择框无法在 Teams 页渲染与回应。
4. **查看不便**：节点执行内容无法全屏细看，跨刷新无完整运行转录。

### 1.1 已确认决策

| 决策点 | 结论 |
|---|---|
| 边的语义 | **边 = 数据流**：连线即自动注入直接前驱节点的完整结果（仅已完成且有结果的前驱）；`{{节点ID}}`/`{{input}}` 保留用于显式控制插入位置 |
| 节点打断语义 | 打断 = 停止该成员当前回合，节点进入**独立的"已中断"态**（不进失败策略、不级联跳过）；run 暂停，由用户决定重派/跳过 |
| 成员发言语义 | **复用 chat 发送链路**：成员运行中 → 服务端 follow-up 捕获合并进当前回合；成员空闲 → 注入新回合。零新机制 |
| 编辑器视觉 | **专业工作流工具风**：自定义节点卡片、拖拽入画布、右侧属性抽屉、网格背景/小地图/缩放控件、箭头连线 |
| 执行历史 | **新表完整转录**（`agent_team_run_messages`）：一次性做完整——逐消息持久化 + 游标分页 + 重连去重 + 全屏回放 |
| 错误显示 | 统一错误提取工具 + 8 处吞错误点全量修复 + 监控渲染节点级错误；工作流校验升级为字段级结构化错误 |

### 1.2 与候选方案的合并裁决

本设计由两份独立方案合并而成。裁决结论（已按此重写）：

| 议题 | 裁决 |
|---|---|
| 配置绑定机制 | **采纳节点级 execution + execution-token registry**（替换初稿的成员级 UmopConfigRouter 路由方案——后者被证明污染普通聊天、同成员并行节点互覆、崩溃残留） |
| 打断语义 | 维持独立 `interrupted` 态（用户决策）；吸收对方"不得静默标记 done"不变量与 auto 模式处理 |
| 成员发言路径 | 维持复用 chat 发送链路（用户决策）；吸收对方发送乐观 UI（发送中→已送达→失败保留原因） |
| 选择框数据通道 | **合并**：初稿"附加成员 run 流"存在 collect 盲区漏洞（选择框直推 back_queue 不镜像 system 流 → collect 等满超时）；采纳对方发现，在 `_consume_chat_run` 咽喉处镜像 + collect 感知挂起计时；前端渲染维持 run 流附加（零冗余） |
| 执行历史 | **采纳新表完整转录**（用户决策）；吸收对方 turn_id/seq 事件增强与重连协议 |
| 边注入格式 | v1 用追加式文本块（对既有模板侵入最小）；对方的 `dependency_only` 边列为 fast-follow；去重逻辑一致 |
| 错误协议 | 分层采纳：message 提取全量修复；工作流校验升级字段级结构化错误；全量 HTTP 状态重构（404/422）不做 |
| 配置选项接口 | 复用既有 `/api/v1/config-profiles` 列表（已核实仅返回 id/name/path 元数据，无密钥泄露），不新增脱敏端点；成员对话框不调用返回全文的 get 端点 |

## 2. 需求一：节点级执行配置（配置档案 + 人格/工具/Skill 覆盖）

### 2.1 现状机制（已核实）

- **配置档案**：每个档案是一个完整 `AstrBotConfig`（`agent_runner.config.misc.max_steps`、`tool_call_timeout`、`streaming_response`、档案级默认人格、provider 等）。`AstrBotConfigManager.get_conf(umo)`（`astrbot_config_mgr.py:156`）按 umo 经 `UmopConfigRouter` 解析档案；**EventBus（`event_bus.py:39-56`）按 umo 把事件路由到该档案专属的 PipelineScheduler**——每个档案的调度器拥有独立初始化的 Agent 阶段实例（`internal.py:53-80` 在 initialize 时读取所属档案配置）。
- **人格优先级**（`persona_mgr.py:83`）：会话规则强制（sp `session_service_config.persona_id`）→ 会话对话绑定（conversation.persona_id）→ 档案默认。
- **工具/Skill 白名单**：Persona 的 `tools`/`skills`（None=全部、[]=禁用、[...]=允许列表）在 `build_main_agent` 的 `_ensure_persona_and_skills` 中过滤。

### 2.2 设计：节点 execution 字段（节点级，非成员级）

用户需求是"每个**节点**从配置文件载入"。同一成员可在同一工作流的不同节点使用不同配置；成员会话历史与交互保持共享。工作流节点扩展为：

```json
{
  "id": "n2",
  "member_id": "m-test",
  "title": "编写测试方案",
  "task": "根据目标制定测试方案，并指出风险。",
  "execution": {
    "config_id": "config-1",
    "persona_id": "software-testing-expert",
    "tools": null,
    "skills": ["pytest", "browser-testing"]
  }
}
```

- `execution` 整体可缺省（等价现状）。字段语义：
  - `config_id`：配置档案 ID（缺省 = default 档案）。该节点的 Runner、模型、上下文、调用限制、流式策略全部来自档案。
  - `persona_id`：节点人格覆盖。缺省 = 档案默认人格 → 成员会话默认人格 → 平台默认（继承链）。
  - `tools` / `skills`：三态语义与 Persona PO 一致——`null`=继承（档案/人格默认）、`[]`=显式全部禁用、`[...]`=仅允许列表内。
- 成员保留其既有字段（名称、会话、默认人格、provider_id）作为未指定 execution 时的缺省层；provider 不作为与 config_id 并列的入口（避免与配置页不一致的隐式配置）。
- **不做**自由字段 overrides 映射（YAGNI：用户举例的"工具调用次数"即档案字段，config_id 已覆盖；敏感配置绝不从节点写入）。

### 2.3 运行时：execution-token registry（不改全局路由）

**明确否决**修改 `UmopConfigRouter`/会话默认配置承载节点配置（污染普通聊天、同成员并行节点互覆、崩溃残留——见 §10）。改为**回合级受信任令牌**：

1. 新增 `astrbot/core/agent_team_execution.py`：`AgentTeamExecutionRegistry`——
   - `register(token, binding) -> None`：binding = `{run_id, team_id, member_id, node_id, umo, owner_username, config_id, persona_id, tools, skills}`；
   - `resolve(token) -> binding | None`、`unregister(token) -> None`；
   - token 由服务端生成（uuid），仅存在于合成回合 payload 中；**普通聊天请求不得伪造**（webchat adapter 仅在 internal-only payload 键存在时透传，且 resolve 时校验 owner 与 umo 绑定）。
2. 传递链：`DAGRunner._execute_node` / `AutoOrchestrator` 派发前，按节点 execution 解析出 binding → `registry.register(token, binding)` → `deliver(..., execution_token=token)` → `TeamPorts` payload 增加 internal-only 键 `execution_token` → `webchat_adapter.py` 按 `team_context` 既有三行模式透传为 event extra → turn 结束（collect 完成/超时/停止/崩溃的 `finally`）`unregister(token)`——与 `team_dispatch` 工具注册相同的退出路径纪律。
3. **调度器选择**：`EventBus.dispatch`（`event_bus.py:41-47`）在按 umo 解析档案前，先检查 event extra 中的 execution token：命中 → 用 binding 的 `config_id` 从 `pipeline_scheduler_mapping` 取该档案的调度器（不写路由表）；未命中 → 现状逻辑。约 5 行改动。档案已删除 → binding 解析时报错 → 节点 `failed`（不静默回退 default，见 §2.5）。
4. **人格/能力覆盖应用**：`build_main_agent` → `_ensure_persona_and_skills` 读取 execution extra：
   - `persona_id`：**置于优先级链顶端**——直接取该人格对象，跳过 `resolve_selected_persona` 的会话/档案解析（约 5 行；同时把档案默认人格的解析源从 `acm.get_conf(umo)` 切换为 `acm.confs[config_id]`）；
   - `tools`/`skills`：在既有人格过滤**之后**应用三态覆盖（约 8 行）。
5. 安全不变量：`team_dispatch`/`team_finish` 仍只对协调者回合注册（既有 registry），节点配置覆盖不得使普通成员节点获得编排工具。

### 2.4 成员级缺省与节点级覆盖的优先级

```
节点 execution.persona_id / tools / skills        （最高，仅本节点本回合）
  ↓
成员会话规则（sp session_service_config，通常未设置）
  ↓
成员 conversation 人格绑定（成员默认人格）/ 成员 provider_id
  ↓
节点 execution.config_id 档案默认人格/能力
  ↓
平台 / WebChat 默认
```

（档案默认人格位于成员 conversation 绑定之下——与 AstrBot 既有链一致：显式的成员人格身份优先于其运行所用档案的默认人格；`resolve_selected_persona` 仅需在默认档位支持按 config_id 取档案。）

工具与 Skill 的完整解析顺序：先加载配置档案的运行能力 → 再解析最终人格的 `tools`/`skills` → 最后应用节点三态覆盖 → 过滤不存在/未激活/被档案禁用的能力（沿既有过滤管线，不新建第二套人格加载逻辑）。

### 2.5 配置变更、热恢复与失效处理

- 运行启动时把每个节点的 `execution`（含 config_id）冻结进 `graph_snapshot`（既有快照机制，零新表）；恢复时按保存的 execution 重建 binding。
- 恢复时校验 `config_id` 仍存在：不存在 → 该节点标记 `failed`（原因"配置档案已删除"），**不静默回退 default**；已完成节点不重执行（Tier-1 语义不变）。
- **实施裁决（三层防线中"档案中途被删"的竞态窗口）**：deliver 前检查（config_checker）与保存校验覆盖绝大多数路径；若档案恰好在 deliver 检查之后、EventBus 路由之前被删除，EventBus 记录警告并回退到 umo 默认调度器（binding extra 仍生效）——该亚秒级竞态窗口接受回退而非丢弃事件，后续节点会被 deliver 检查拦住。已确认为有意行为。
- 不把 Provider 密钥或档案全文写入 `AgentTeamRun`——节点只存 ID 引用。
- 同一成员被同波次多个节点引用：既有忙等 + 会话锁保证串行；UI 显示"等待该成员上一个节点完成"（busy 事件已有）。

### 2.6 UI：成员对话框与节点属性面板分工

- **成员创建/编辑对话框**（瘦身）：名称、成员默认人格（PersonaSelector）、模型覆盖（provider 下拉，保留现状）。配置档案选择**移至节点属性面板**。
- **节点属性面板**（编辑器右侧抽屉，见 §3.3）新增"执行配置"分组：
  1. 配置档案下拉——数据源 `/api/v1/config-profiles` 列表（已核实仅含 id/name/path 元数据；**不调用**返回全文的 get 端点），缺省"默认配置"；
  2. 人格覆盖——缺省"跟随档案默认"，切换后 PersonaSelector；
  3. 工具覆盖——三态（继承/仅允许/禁用全部）+ 工具多选（工具清单复用既有 tools API，含搜索与不可用原因展示）；
  4. Skill 覆盖——三态 + 多选（skills API）。
- 团队创建对话框的成员行保持精简；配置微调在节点上完成。

## 3. 需求二：前端体验修复

### 3.1 错误可见性（审计结论：8 处）

后端事实（已核实）：Teams 全部校验/冲突错误是 **HTTP 400/409 + `{status:'error', message}` body**，axios 抛异常——`useAgentTeams.ts` 的 HTTP-200 envelope 分支是死代码，catch 只显示 axios 通用文案；`useAgentTeamsRun.ts` 是唯一正确实现。

**修复方案（分层）**：

1. 新建 `dashboard/src/utils/extractApiError.ts`（无 Vue 依赖）：`extractApiError(err: unknown, fallback: string): { message: string; fields: Array<{path, message}> }`——依次读 `err?.response?.data?.message`、`data.data.fields`（字段级，见下）、429 时 `normalizeAxiosError` 抛出的字符串特例、`err?.message`、fallback。配 vitest 单测。
2. **8 处逐点替换**（全部显示后端原因）：`useAgentTeams.ts` unwrapEnvelope catch、`WorkflowEditor` 保存、`TeamCreateDialog`/`MemberAddDialog` 保存（含 loadOptions 静默失败改 toast）、`AgentTeamsPage` removeMember、`RunsHistory` HTTP catch。
3. **工作流校验升级为字段级结构化错误**：`AgentTeamsServiceError` 增加可选 `field_errors: [{path, code, message}]`（如 `nodes.n2.task / REQUIRED / 节点"n2"的任务模板不能为空`）；`_handle` 把它放进 envelope `data.fields`。编辑器侧：顶部问题摘要（数量+首条原因）→ 点击错误项聚焦对应节点/边/名称输入框；服务端字段错误与本地即时校验合并去重。仅工作流端点升级，其余端点维持 message 级。
4. **监控渲染执行期错误**（此前完全不可见）：失败/已中断节点在状态 chip title 与对话框内联展示 `nodeStates[nodeId].error`；`TeamsFlowCanvas` 节点 prop 类型补 `error` 并悬浮提示；`RunMonitor` 顶部在 `lastError`/非正常 `stoppedReason` 时渲染可关闭警告横幅；SSE 附加重连耗尽 → toast + 横幅（替代 console-only）。

### 3.2 边 = 数据流（自动注入前驱结果）

- **执行语义**（`agent_team_dag.py` + `DAGRunner`）：渲染节点任务时，若模板未显式引用某直接前驱，自动在任务文本末尾追加"上游结果"块（**仅含 done 且持有结果的前驱**——skipped/interrupted 前驱不注入）：

  ```
  [上游结果]
  ◆ <节点标题|成员名> (n1)：
  <前驱 result 文本，按 inject_max_length 截断>
  ```

  显式写了 `{{n1}}` 的前驱不再重复注入；全部前驱都被显式引用则不追加。`render_task` 签名扩展 `auto_inject_predecessors: list[tuple[str, str]] | None`。老模板行为不变；不修改用户手写的任务文本、不回写 `{{source_id}}`。
- **编辑器 UI**：选中节点显示"上游节点"chips（点击插入 `{{节点ID}}`）与 `{{input}}` chip；引用了未相连节点的占位符行内警告（新增 `unreferenced_placeholders` 校验函数，与 `findCycle` 同层）。
- **fast-follow（本设计不实现）**：边级 `inject: "context"|"dependency_only"` 与 `label` 字段、`<upstream_results>` 结构化包裹。

### 3.3 编辑器专业工作流工具风重设计

> **实施延期清单（Plan 2 终审裁决，2026-09-05）**：以下 §3.3 子项延后至 Plan 3 或后续（非功能缺陷，届时与 §4/§5 的对话框工作同文件顺带落地）：① 检查器"节点基本信息"与"上游/下游列表"分组；② 节点卡"缺失成员"标记可点击定位属性项；③ 工具条"保存并运行"；④ 窄屏成员面板折叠为抽屉（当前折行为 wrap 行）；⑤ handle 的文本/ARIA 标签。已实现：成员卡自定义节点、拖拽、右抽屉、画布 chrome、脏状态、校验徽标、响应式检查器。

- **自定义节点**（Vue Flow `member` 节点类型）：上部节点标题+编号+状态点；中部成员色点+名称+配置档案/人格覆盖标签；底部任务预览两行+入站依赖数；左入右出 handle（大连接区）。选中清晰边框；monitor 态五色+interrupted 橙；成员缺失/档案不存在 → 顶部可点击错误标记（定位到属性项）【②已延期】。节点宽约 220px。
- **左侧成员面板**：成员按名称+人格+模型摘要展示；**HTML5 拖拽入画布**（dragstart 带 member_id，`@drop` 经 `project()` 换算坐标建节点）；被工作流引用的成员显示使用次数；被删除的成员保留为"缺失成员"占位节点便于修复。
- **右侧属性抽屉**（`v-navigation-drawer`，分组折叠）：节点基本信息 → 执行成员 → 执行配置（§2.6）→ 任务模板（变量 chips：`{{input}}`+各直接前驱）→ 上游/下游列表 → 校验问题。
- **画布观感**：`<Background>` 点阵网格、`<Controls>`、`<MiniMap>`（monitor 只读）、连线箭头 + 选中高亮 + monitor 态运行中边流动画；暗色适配（theme vars）。
- **顶部工具条**：工作流选择+名称（可编辑）｜ 未保存状态点 ｜ 校验状态徽标（点击打开问题摘要）｜ 保存/保存并运行。不以 disabled 让用户猜原因。
- **响应式与可访问性**：宽屏三栏；中窄屏成员面板/属性面板折叠为抽屉；handle 与状态均带文本/ARIA 说明；不以颜色作唯一状态表达。

## 4. 需求三：节点交互对话框（打断 / 追加指令 / 交互选择框）

### 4.1 总体形态

每个成员窗格（AgentWindow）增加"展开"按钮 → **节点对话框**（`v-dialog`，max-width 900px；需求四的全屏即同对话框的 `fullscreen` 切换）。对话框 = 该成员会话的"迷你 chat 页"：完整执行时间线（§5 转录）+ `InteractiveChoiceBox` + 输入栏（发言）+ 打断按钮 + 全屏切换。

### 4.2 数据面：双层来源（Teams 转录 + 成员 run 流）

- **Teams 层**：`agent_team_run_messages` 转录（§5）提供跨刷新、可分页的本 run 完整时间线。
- **chat 层**：成员的合成回合是**一等 chat run**（`register_synthetic_chat_run`），`deliver` 返回的 `message_id` 即 run_id。新 composable `useMemberRunStream`：`fetchWithAuth(chatApi.resumeRunStreamUrl(runId))` → 复用 `processStreamPayload` 喂入本地 botRecord → 获得流式增量、thinking、工具明细、`follow_up_captured`、`interactive_choice` part 的**完整保真实时数据**（复用 chat 页既有的全部处理分支，零协议新增）。
- 事件增强（连接两层）：`message`/`node_status` 事件增加 `turn_id`、`run_id`（成员 chat run id）字段（后端派发处 +2 行；reducer 存入窗口/时间线）——对话框由此知道附加哪个 run、时间线按 turn 合并。
- `message` 事件的 `reply` 目前只带 text 不带 parts：后端派发处保留 `collect` 已返回的 `parts` 并随 reply 事件发送（+2 行），时间线得以渲染结构化 parts。

### 4.3 成员发言（追加指令 / 新消息）——复用 chat 发送链路

输入框发送 → **直接调用 chat 页同款发送 API**（`chatApi` send → `build_chat_stream`），目标会话为成员 session_id：

- 成员运行中：管道 `try_capture_follow_up`（`internal.py:217-233`）捕获为 FollowUpTicket 合并进当前回合（发送者同为 dashboard 用户，满足捕获条件）；`follow_up_captured` 事件出现在成员 run 流，对话框即时把用户气泡挂到运行记录下（复用 chat 页既有分支）。
- 成员空闲：作为新回合注入；对话框 attach 新 run。
- UI：发送不禁用（与 chat 页一致）；运行中发送提示"将作为追加指令合并进当前回合"；**乐观 UI**：发送中 → 已送达（服务端确认）→ 失败保留消息与后端原因；发送期间禁用按钮防重复（幂等以客户端防抖实现，不新增服务端幂等键）。
- 并发安全：无第二并发回合风险——会话锁串行化 + follow-up 捕获 + webchat 队列天然仲裁（不新建仲裁器）。
- 运行整体 stop 已请求后：发送返回明确错误（管道行为），UI 呈现原因。

### 4.4 打断（独立"已中断"态）

- **后端**：新端点 `POST /agent_teams/runs/{run_id}/members/{member_id}/interrupt`（v1+legacy 双注册）。`AgentTeamRunService.interrupt_node(username, run_id, member_id)`：
  1. 所有权校验（`_require_run`）；
  2. `active_event_registry.request_agent_stop_all(member.umo)`——成员回合被 "Stop output." 注入并 abort（历史仍保存，chat 页可见；不直接取消整个 WebChat 事件以破坏历史保存）；
  3. DAG 模式：定位该成员当前 `running` 的节点 → `node_states[nid].status = "interrupted"`（`error="用户中断"`）→ run 置 `paused` + emit `node_status`/`paused {reason:"node interrupted", node_id}` → 持久化。**不变量：节点不得被静默标记 done**——`_execute_node` 的 collect 返回路径遇已 interrupted 节点不覆盖状态；完成判定与 `_progress` 计数纳入 `interrupted`（存在 interrupted 节点时 run 不判 completed）；interrupted 节点不级联跳过；
  4. Auto 模式：无节点概念——只停成员回合，打断结果记录为本轮结果，协调者下一轮可继续派发或结束；不打断 run。
- **节点状态机**：新增 `interrupted`（枚举、`_progress`、前端橙着色）；`retry`/`skip` 均可用于 interrupted。
- **与选择框的交互**：节点正在等待 `ask_user_choice` 时打断 → 先取消 pending choice（既有 DELETE 语义，工具返回"[User input was cancelled]"）再走停止流程；追加指令在 choice 等待期间照常捕获合并（choice 解除后 Agent 下一动作可见）。
- **前端**：对话框"打断"按钮（运行中显示，二次确认）→ interrupt API；窗格与 DAG 节点显示"已中断"；run paused 后既有控制条（继续/重试/跳过）自然可用。

### 4.5 interactive_choice 渲染与回应

**关键修复（本设计初稿的漏洞，由候选方案审阅发现）**：`ask_user_choice` 插件把选择框**直推 back_queue**（不经 adapter、不镜像 system 流，`ask_user_choice_tool.py:320-381`）——`TeamPorts.collect` 只监听 system 流，**看不见选择框**，会等满 `reply_timeout` 把节点标 failed，而用户还在看框。修复三件套：

1. **镜像咽喉**：`chat_service._consume_chat_run`（选择事件流经的唯一服务端咽喉，`chat_service.py:1580` 附近）在处理 `chain_type=interactive_choice` 的 payload 时，向该会话的 system 流镜像一份（+2 行）——`TeamPorts.collect` 由此可见。不改 ask_user_choice 插件（位于 data/plugins，非仓库代码）。
2. **collect 感知挂起计时**：`TeamPorts.collect` 识别选择事件后通过 emit 通知 runner，runner 将该节点的 `reply_timeout` 计时**挂起**（切换为选择框自身的 expires_at 语义；用户回应后恢复计时）。DAG/成员波等待选择期间不判超时；`node_status` 附带 `waiting_choice: true`（DAG 视图"等待你的选择"徽标）。
3. **前端渲染**：对话框经 `useMemberRunStream` 收到 `interactive_choice` part（chat 页既有分发分支零改动）→ 渲染共享 `InteractiveChoiceBox`（props：`part`、`umo=member.umo`、`isDark`）→ 用户点击 → `interactiveChoiceStore.submitChoice(member.umo, request_id, payload)` → 既有 `POST /api/chat/interactive-choice/{request_id}` → future resolve → 工具返回 → Agent 继续。store 按 UMO 键控天然支持多会话并存；从 `ChatMessageList.vue` 的提交/取消处理器提取共享函数供两处使用。插件推送时已带 `umo`，前端不得替换为其他会话的 UMO（沿用组件现有约束）。

## 5. 需求四：完整执行历史（新表转录）与全屏

### 5.1 为什么需要新表

现有 `MemberWindowState`（sent/streamText/parts）只保留**最后一个回合**的摘要，无法表达：同一成员多节点/多轮次的多次回合、用户追加指令与回复的顺序、工具调用与选择框、页面刷新后的恢复。`node_states` 只存最终 result。因此新增逐消息转录表（**不**把流式 delta 塞进 `AgentTeamRun` 单行 JSON——运行行会膨胀且并发更新困难）。

### 5.2 转录数据模型（表 `agent_team_run_messages`）

```text
id          INTEGER PK autoincrement   # 同时作为分页游标
run_id      TEXT  indexed
member_id   TEXT  indexed
node_id     TEXT  nullable
round       INTEGER nullable            # auto 模式轮次
turn_id     TEXT                        # 一回合内合并流式的键
direction   TEXT                        # sent | reply | choice | system
text        TEXT nullable               # sent=任务全文 / reply=完整回复
parts       JSON nullable               # reply 的结构化 parts（think/工具/附件）
metadata    JSON nullable               # system 事件的 reason 等
created_at  TIMESTAMP
```

持久化策略：

- **写哪些**：`sent`（派发任务全文）、`reply`（完整回复 + 结构化 parts——`collect` 已返回 parts，runner 现弃之，改为保留）、`choice`（出现/解除）、`system`（暂停、打断、错误、取消）。**流式 delta 不逐字符入库**（内存与 SSE 实时传输；reply 行即该回合最终全文，历史回放无损）。
- **写接缝**：runner 事件发射点注入可选 `transcript_sink` 回调（由 `AgentTeamRunService` 提供，批量缓冲 + 每回合结束 flush；run 终态兜底 flush）——与 ports/bus 同为注入接缝，单测可脚本化。
- **保留策略**：按 run 上限截断（如每 run 每成员最近 500 条），超限提示导出；不做跨 run 清理（历史 runs 已有整行）。
- 成员会话本体（conversation/platform_message_history）不受影响——本表是**运行视图**，非会话视图。

### 5.3 转录 API 与重连协议

```http
GET /api/v1/agent_teams/runs/{run_id}/members/{member_id}/transcript?before_id=&limit=50
```

返回按 `id desc` 的分页行 + `next_before_id` 游标。重连/刷新恢复顺序：**run snapshot → transcript 首页 → 订阅实时 SSE**；去重以行 `id`（SSE 事件带 `seq`=行 id）为准，避免重放重复。

### 5.4 reducer 与组件升级

- `agentTeamsRunReducer.ts`：`MemberWindowState` 升级为**按成员的回合时间线**（`turns: Array<{turn_id, node_id?, round?, sent, streamText, parts, streaming}>`），网格窗格摘要视图渲染最近回合（兼容现状外观）；按 `turn_id` 合并流式增量、reply 替换同 turn 临时流内容；`choice`/`system` 事件入时间线。网格摘要保持轻量，完整时间线在对话框中渲染。
- 新增 `MemberTranscriptDialog.vue`（全屏容器）：

  ```text
  ┌──────────────────────────────────────────────────────────────┐
  │ 成员名称  节点/轮次  状态        [等待你的选择] 追加指令 全屏 关闭 │
  ├──────────────────────────────────────────────────────────────┤
  │ 时间线：[任务][思考(折叠)][工具(安全展示)][回复(Markdown/parts)] │
  │        [InteractiveChoiceBox][系统(暂停/打断/错误)]            │
  ├──────────────────────────────────────────────────────────────┤
  │ 输入追加指令…                                    发送   打断   │
  └──────────────────────────────────────────────────────────────┘
  ```

- 交互细节：顶部栏固定、正文独立滚动、输入栏贴底；默认自动滚动、用户上翻暂停 + "回到底部"按钮；长消息复制/思考折叠/结构化 parts 查看；从网格打开保留成员与滚动位置，关闭还原；Vuetify 对话框规范（`text-h3 pa-4 pb-0 pl-6` 标题、text/tonal 按钮）；暗色适配。渲染复用聊天页组件（`ReasoningBlock`/`MarkdownMessagePart`/`InteractiveChoiceBox`），不另造渲染器。
- 全屏：对话框 `fullscreen` 切换按钮（窗格展开按钮与对话框内切换共用状态）；全屏下时间线占满、输入贴底。

## 6. API 变更清单

| 变更 | 类型 | 说明 |
|---|---|---|
| workflow `graph.nodes[].execution` | 扩展 | `{config_id?, persona_id?, tools?, skills?}`；校验：config_id 存在、persona/tools/skills 引用有效、三态合法 |
| `POST /agent_teams/runs/{run_id}/members/{member_id}/interrupt` | 新增 | 打断成员回合；DAG 模式节点 interrupted + run paused |
| `GET /agent_teams/runs/{run_id}/members/{member_id}/transcript` | 新增 | 游标分页转录（§5.3） |
| `message`/`node_status` 事件 | 扩展 | +`turn_id`、`run_id`；reply +`parts`；node_status +`waiting_choice`；新增 `choice` 事件（collect 感知后广播） |
| 错误 envelope | 扩展 | 工作流校验端点的 `data.fields` 字段级错误 |
| 表 `agent_team_run_messages` | 新增 | 转录表 + 仓储方法（append 批量 / 游标查询） |
| OpenAPI YAML | 更新 | 上述全部 + `pnpm generate:api` |

## 7. 测试策略

- **后端 pytest**：
  - execution registry：token 绑定/解析/注销、跨用户与伪造拒绝、全部退出路径清理（对齐 team 工具 registry 的测试范式）；
  - EventBus token 路由：命中选档案调度器、未命中回退、档案删除 → 节点 failed；
  - 人格/能力覆盖：节点 persona 顶置优先级、档案默认人格解析源切换、三态工具/Skill 过滤（继承/禁用全部/允许列表）；
  - `render_task` 自动注入（仅 done 前驱、显式引用去重、全显式不追加、截断）；
  - `interrupt_node`：DAG 节点 interrupted + run paused + collect 不覆盖 + 不级联；auto 模式仅停成员；
  - choice 镜像与 collect 挂起计时：`_consume_chat_run` 镜像后 collect 可见、等待期间不超时、回应后恢复；
  - 转录：写入批次、游标分页、保留截断、恢复协议（snapshot→transcript→SSE 去重）；
  - 字段级校验错误的 envelope 形状。
- **前端 vitest**：`extractApiError`（含 429 字符串、fields）；8 处错误点逐点断言；reducer：turn 合并/时间线/choice/system 折叠、`run_id` 附加；RunMonitor：auto 显示、interrupt 按钮、错误横幅、等待选择徽标；WorkflowEditor：拖拽建节点、变量 chips、未连接引用警告、字段错误定位聚焦；`MemberTranscriptDialog`：分页加载、自动滚动暂停/回底、全屏切换、choice 渲染与提交。
- **手工验收**：节点绑"配置1"（max_steps=3）→ 3 步收尾；节点覆盖人格"软件测试专家"→ 执行采用之；只画箭头不写 {{id}} → 后继获得前驱全文；保存空名称/空任务 → 字段错误定位到节点；运行中追加指令 → 合并进当前回合；打断 → "已中断" → 改任务重试；`ask_user_choice` 在网格徽标与对话框/全屏出现 → 点击 → Agent 解除阻塞继续；刷新页面 → 转录分页恢复完整时间线。

## 8. 实施分期（每期独立可验收）

1. **错误可见性**（§3.1 第 1-2、4 条）：extractApiError + 8 处修复 + 监控错误渲染——独立快赢。
2. **节点级执行配置**（§2）：registry + EventBus 路由 + build_main_agent 覆盖 + schema/校验 + 节点属性面板执行配置组。
3. **边 = 数据流**（§3.2）：render_task 自动注入 + 变量 chips/未连接警告 + 工作流校验字段级错误（§3.1 第 3 条，与编辑器同期落地）。
4. **转录与事件增强**（§5.2-5.4 前半）：新表 + 写接缝 + turn_id/seq/parts 事件增强 + choice 镜像与 collect 挂起 + 分页 API + reducer 时间线。
5. **节点交互对话框**（§4 + §5.4 后半）：useMemberRunStream、发言、打断 interrupted 态、InteractiveChoiceBox、MemberTranscriptDialog 全屏。
6. **编辑器视觉重设计**（§3.3）：自定义节点/拖拽/抽屉/画布组件/校验定位。

## 9. 已否决的备选方案

| 备选 | 否决理由 |
|---|---|
| 修改 `UmopConfigRouter`/会话默认配置承载节点配置（本设计初稿方案） | 污染成员普通聊天；同成员并行节点互覆路由（last-writer-wins）；崩溃后路由残留；无法表达同成员不同节点不同配置 |
| 成员级 `MemberOverrideRegistry` 覆盖工具/Skill（初稿方案） | 粒度错位：需求是节点级；且同样作用于成员直接对话回合（范围失控）。被节点 execution 取代 |
| 节点自由字段 `overrides`（候选方案 §4.2） | YAGNI：用户举例的"工具调用次数"即档案字段，config_id 已覆盖；自由写 Runner 参数引入校验与安全面 |
| 复制完整配置 JSON 到节点 | 重复、密钥泄露风险、配置页修改无法同步 |
| 连线时把 `{{source_id}}` 追加进任务文本 | 用户文本不可逆、删边无法辨识系统生成文字、无法自然支持多前驱 |
| 为 Teams 重写简化选择框/提交接口 | 与聊天页 request_id、UMO 隔离、取消、过期、持久化逻辑分叉 |
| 流式 delta 逐字符入库 / 塞进 `AgentTeamRun` 单行 JSON | 行膨胀、并发更新困难、分页不可能 |
| 运行转录只做简化版（摘要+跳转，初稿 §4.2） | 用户裁决：一次性做完整——跨刷新可回放的完整时间线是需求四的本意 |
| 打断→立即重派 / 打断→走失败策略 | 用户已否决（浪费调用；与真实失败混淆）——interrupted 为独立态 |
| 只用 disabled 保存按钮提示校验失败 | 用户看不到原因；保留可见校验摘要与字段定位 |
