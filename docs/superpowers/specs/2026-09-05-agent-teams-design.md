# Agent Teams 设计方案

- 日期：2026-09-05
- 前作：[2026-08-12-agent-collab-design.md](./2026-08-12-agent-collab-design.md)
- 状态：已评审（设计决策见 §2.2）

## 1. 背景与目标

现有 Agent 协作（Collab）将多个 WebChat 会话组成讨论组，由主持人以星型拓扑轮转路由对话。Agent Teams 将其升级为一个完整的多 Agent 团队子系统：

1. **团队成员自助创建**：用户通过面板选择人格或自定义配置批量创建成员，每个成员是一个独立的 WebChat 会话（独立 conversation / persona / provider），无需手动逐个"新建对话"。团队中必须存在且仅存在一个协调者（Coordinator），协调者本身也是团队成员。
2. **两种编排执行**：
   - **自动编排**：协调者通过 LLM 工具（`team_dispatch` / `team_finish`）拆解任务、并行派发给成员会话、收集结果、多轮迭代，直到调用 `team_finish` 结束。
   - **手动编排**：ComfyUI 式可拖拽 Workflow 编辑器。节点绑定成员，有向连线构成 DAG；运行时由 DAG 引擎按依赖分层并行执行，前驱结果注入后继任务模板。
3. **全程可视化**：同一页面内以可调大小的多窗格网格展示所有成员的实时执行过程（注入消息、流式回复、工具活动）；DAG 运行额外提供节点着色的进度视图。

### 1.1 与 Subagent 的关系

Subagent 体系（`astrbot/core/subagent_*.py`）与本功能**完全解耦且保持不动**：subagent 仍是"某个会话的 Agent 可临时调用的工具"（按 umo 隔离命名空间、每轮自动清理）。团队成员会话经完整主管道运行，天然拥有各自的 subagent 命名空间，可照常使用。

## 2. 需求与已确认决策

### 2.1 功能需求

| # | 需求 |
|---|---|
| R1 | 团队 CRUD；成员由面板创建（从人格创建 / 自定义 system_prompt 创建），可选模型覆盖 |
| R2 | 团队内恰好一个协调者，可切换 |
| R3 | 自动编排：协调者多轮派发-收集-汇总，结构化 LLM 工具驱动 |
| R4 | 手动编排：Workflow 编辑器（拖拽节点/连线/DAG），保存为可复用模板，DAG 引擎执行 |
| R5 | 运行监控：多窗格自适应网格 + 手动调整大小 + 单窗格全屏；DAG 进度视图 |
| R6 | 运行历史与重启热恢复（Tier 1：完成节点不重跑、在途节点重做） |
| R7 | 失败策略可配置：默认暂停等待用户决定（重试/跳过/终止），可选自动级联跳过 |

### 2.2 已确认的设计决策

| 决策点 | 结论 |
|---|---|
| 协调者定位 | 协调者也是团队成员，有自己的会话窗口，既派发也可被派发任务 |
| 与 Collab 的关系 | Teams 替换 Collab：隐藏旧面板入口，旧 `/api/agent_collab/*` 保留过渡期后移除，旧分组数据不迁移 |
| 节点失败处理 | 可配置（团队级 `failure_policy`）：默认 `pause`；可选 `auto_skip`（级联跳过后继继续） |
| 成员会话范围 | v1 仅 WebChat 会话 |
| 协调者协议 | **注册 LLM 工具**（非文本指令协议），当且仅当 Teams 运行活跃时才分配给协调者会话 |
| 重启恢复 | 做 Tier 1 热恢复（见 §6.5） |
| 成员来源 | v1 只能新建会话，不允许绑定已有会话（结构上杜绝跨团队会话引用）；存储层加 session_id 唯一性校验兜底 |

## 3. 非目标（Non-goals）

- 平台会话（Telegram/Discord 等）作为团队成员——留待后续版本（collect 无统一 run 概念，需按平台适配）。
- 运行中途（回合级）崩溃对账——采用重做语义，见 §6.5。
- 成员间点对点自由消息、成员主动唤醒协调者。
- 团队模板市场、跨用户共享。
- 运行编排的"最终汇总节点"（用户阅读成员窗口即结果；未来可加可选汇总节点）。
- subagent 体系任何改动。

## 4. 总体架构

Agent Teams 是 **dashboard 服务层子系统**，驱动 core 的 WebChat 会话管道。原则：**成员即普通会话，运行走既有主管道**（合成回合注入 → 完整 Agent 循环 → 流式回收）；Teams 层只负责团队定义、编排、调度、可视化，不改动 agent 执行引擎（唯一 core 改动见 §6.4）。

```
┌─ dashboard 前端 ─────────────────────────────────────────┐
│  AgentTeamsView                                          │
│  ├─ 团队/成员管理      ├─ Workflow 编辑器（Vue Flow）      │
│  └─ 运行监控（多窗格 grid + DAG 进度）←──── SSE ──────────┐│
└──────────────────────────────────────────────────────────┼┤
┌─ dashboard 服务层 ────────────────────────────────────────┤│
│  agent_team_service（团队/成员/工作流 CRUD + 校验）        ││
│  agent_team_run_service                                   ││
│   ├─ TeamPorts（deliver/collect/is_busy/emit）            ││
│   ├─ DAGRunner（手动编排）                                 ││
│   ├─ AutoOrchestrator（协调者轮次，LLM 工具驱动）           ││
│   └─ RunEventBus（事件历史 + 订阅 + 重放）─────────────────┘│
└───────────────┬───────────────────────────────────────────┘
                │ 注册/注销（运行期）
┌─ core ────────▼───────────────────────────────────────────┐
│  AgentTeamToolRegistry ← build_main_agent 合并（唯一改动）  │
│  webchat_queue_mgr → adapter → pipeline → 各成员 Agent 循环 │
│  register_synthetic_chat_run · persona/provider 绑定        │
│  subagent 体系（不动）                                       │
└────────────────────────────────────────────────────────────┘
```

## 5. 数据模型

3 张新表（`astrbot/core/db/po.py` SQLModel + `astrbot/core/db/migration/` 迁移，沿 `cron_jobs` 表先例）。选表而非 preferences KV：工作流是用户资产、运行需按团队查询历史，KV（collab 现状）不利于查询与清理。

### 5.1 AgentTeam（表 `agent_teams`）

| 字段 | 类型 | 说明 |
|---|---|---|
| team_id | str PK | uuid hex[:8] |
| owner_username | str | 创建者 |
| name | str | 团队名 |
| coordinator_member_id | str | 协调者成员 id（成员之一） |
| members | str(JSON) | `[{member_id, name, session_id, persona_id, provider_id}]` |
| config | str(JSON) | `{failure_policy: "pause"|"auto_skip", reply_timeout: 600, max_rounds: 20, max_parallel: 5, inject_max_length: 4000}` |
| created_at / updated_at | str | ISO 时间 |

成员约束：`name` 团队内唯一（≤32 字符，即派发路由键）；`session_id` 全局唯一（唯一性校验，为将来"绑定已有会话"兜底）；成员数 ≤10。`member_id` 为 uuid hex[:8]。

### 5.2 AgentTeamWorkflow（表 `agent_team_workflows`）

| 字段 | 类型 | 说明 |
|---|---|---|
| workflow_id | str PK | uuid hex[:8] |
| team_id | str | 归属团队 |
| name | str | 模板名 |
| graph | str(JSON) | `{nodes: [{id, member_id, task, title?}], edges: [{from, to}]}` |
| layout | str(JSON) | `{node_id: {x, y}}` 编辑器画布位置 |
| created_at / updated_at | str | |

节点约束：`id` 为用户侧唯一字符串（前端生成 `n1..nN`）；`task` 为任务模板，支持 `{{input}}`（运行输入）与 `{{<node_id>}}`（指定前驱节点的完整回复文本）；节点数 ≤20。

### 5.3 AgentTeamRun（表 `agent_team_runs`）

| 字段 | 类型 | 说明 |
|---|---|---|
| run_id | str PK | uuid hex[:12] |
| team_id / workflow_id | str / str\|None | workflow_id 为 None 表示自动编排 |
| mode | str | `"auto"` \| `"dag"` |
| input | str | 用户输入的目标/主题 |
| status | str | `running \| paused \| completed \| stopped \| failed \| interrupted` |
| result_summary | str | 自动编排的 `team_finish` 总结 / DAG 完成摘要 |
| graph_snapshot | str(JSON) | 运行时冻结的节点/边（含成员 name/session_id 快照） |
| node_states | str(JSON) | `{node_id: {status, member_id, task_rendered, result, error, started_at, finished_at}}`；自动编排时键为轮次派发 id |
| rounds | str(JSON) | 自动编排专用：`[{n, assignments, results_digest}]` |
| created_at / updated_at | str | |

`node_states.result` 存完整回复文本（后继模板渲染与轮次摘要依赖它）；`status ∈ pending|running|done|failed|skipped`。**落盘时机：每次节点状态转换即 UPDATE**（非运行结束才写），这是热恢复的基础。同一团队同时只允许一个活跃 run（创建时校验，冲突返回 409）。

## 6. 后端设计

新文件：`astrbot/dashboard/services/agent_team_service.py`、`astrbot/dashboard/services/agent_team_run_service.py`、`astrbot/dashboard/api/agent_teams.py`；core 侧新增 `astrbot/core/agent_team_tools.py`（工具类 + 注册表）。

### 6.1 agent_team_service — 团队/成员/工作流 CRUD

- 团队校验：≥2 成员、`name` 唯一、恰好一个协调者。
- 成员创建（替代手动"新建对话"）：走与 `GET /chat/sessions/new` 相同的内部路径创建 WebChat 会话 → conversation 绑定 persona → 可选 `set_provider` 定向模型 → 写入成员。**只允许新建，不提供绑定已有会话**。
- 成员删除：忙闸（`chat_runs_by_session` 非空或团队有活跃 run 时拒绝）；被工作流引用的成员删除后不自动改图，由工作流校验报"成员缺失"。
- 协调者切换：PATCH 设置新 `coordinator_member_id`。
- 团队删除：有活跃 run 时拒绝；删除仅解绑，保留成员会话。
- 工作流校验（保存时 + 运行前各一次）：Kahn 判环（算法取自 `subagent_dag.py` `_kahn_sort`）、边引用合法节点、成员绑定存在、节点数上限。

### 6.2 TeamPorts — 执行接缝

从 `agent_collab_service.py` 迁移改造 `RunnerPorts`（`deliver` / `collect` / `is_busy` / `emit` + `reply_timeout` / `busy_poll_interval`）及其两个工厂到 team 模块：

- `deliver(session_id, text, context)`：**先** `register_synthetic_chat_run` 注册一等运行（保持 register-then-inject 顺序），再向 `webchat_queue_mgr` 会话队列注入回合；payload 携带 `team_context`（per-turn 协作上下文，`mark_as_temp()` 注入不污染历史）与 `persist_user_history: True`。
- `collect(session_id, message_id)`：`subscribe_system` + `BotMessageAccumulator` 增量收集，返回 (回复全文, 完整 message parts)。
- `is_busy(session_id)`：读 `chat_service.chat_runs_by_session`。
- `build_ports_for_test` 测试注入缝**原样保留**，是 runner 单测的基础。
- WebChat adapter 改动（约 3 行）：按 `webchat_adapter.py` 中 `collab_context` 的既有模式，透传 `team_context` extra。

### 6.3 DAGRunner — 手动编排引擎

`SubAgentDAGEngine` 的调度思想在端口上重实现（不直接复用 in-process 引擎：它执行 handoff 工具调用，这里执行跨会话消息注入）：

1. 冻结 `graph_snapshot`，建 run 行，Kahn 分层。
2. 就绪集 → 波次执行：`asyncio.gather` 并行派发（`max_parallel` 信号量限流）。每节点：忙等 → 渲染任务模板 → 节点 `running`（落盘）→ emit → deliver → collect → 存 `result`、`done|failed`（落盘）→ 解锁后继。
3. 模板渲染：`{{input}}` = 运行输入；`{{<node_id>}}` = 该节点 `result` 全文，超 `inject_max_length` 尾部截断；未知占位符 → 渲染错误 → 节点 `failed` → 走失败策略。
4. 失败策略（团队 `config.failure_policy`）：
   - `pause`（默认）：节点 `failed`，run 置 `paused`，emit 暂停事件；API 提供 `retry_node`（同输入重渲染重跑）/ `skip`（标记 `skipped` 后按级联规则跳过全部后继）/ `abort`。
   - `auto_skip`：直接 `failed`→`skipped` 并级联跳过后继，其余分支继续。
5. 全部节点 `done|skipped` → `completed`，写 `result_summary`，emit 汇总。
6. 成员回复超时（`reply_timeout`，默认 600s）视为节点 `failed`。`stop`：放弃收集 + 对在跑成员调 `POST /chat/sessions/{id}/stop` 传播取消（`active_event_registry`）+ run 置 `stopped`。

### 6.4 AutoOrchestrator — 协调者轮次（LLM 工具协议）

#### 工具定义（`astrbot/core/agent_team_tools.py`）

```python
class TeamDispatchTool(FunctionTool):
    name = "team_dispatch"
    # parameters(JSON Schema): assignments: [{member: str, task: str}], notes?: str
    # 构造时注入回调 on_dispatch（dashboard 运行服务提供），call() 校验 member
    # 别名（未知别名时返回错误并列出有效别名，模型当轮自纠）、推入该 run 的
    # 派发队列、回执"已派发 N 项任务"。

class TeamFinishTool(FunctionTool):
    name = "team_finish"
    # parameters: summary: str；标记 run 完成，summary 写入 result_summary。
```

工具类与注册表均在 core，回调闭包由 dashboard 注入，**不产生 core→dashboard 依赖**。

#### 注册表与分配时机（"当且仅当 Teams 模式"）

`AgentTeamToolRegistry`（同模块，按 umo 键的哑注册表）：运行启动/恢复时注册协调者会话的工具实例；**pause/stop/end 时注销**。`build_main_agent`（`astr_main_agent.py`，在 `_apply_subagent_manager_tools` 同位置）增加约 3 行：registry 命中该 umo 则把工具并入 `req.func_tool`。成员会话与普通聊天回合 registry 无记录 → 工具不出现。

#### 轮次循环

1. 每轮向协调者会话注入（temp 上下文）：团队名册（name + persona 概述）+ 任务 + （第 2 轮起）上轮各成员结果摘要。协调者调 `team_dispatch` 派发。
2. Runner 从派发队列取 assignments，并行投递（每人独立忙等）；协调者派给自己的任务在其本回合结束后按普通成员流程投递。
3. 收齐成员回复 → 写 `rounds`（落盘）→ 注入下一轮（含摘要）。
4. 协调者调 `team_finish` → run `completed`。整轮结束未调任何工具 → 注入一次提醒回合；连续 2 次 → `paused`。轮次达 `max_rounds` → `paused`（同 collab hop_limit 语义）。

### 6.5 运行生命周期与热恢复（Tier 1）

- 状态机：`running ⇄ paused`，终态 `completed | stopped | failed`；启动清扫把遗留的 `running/paused` 标为 `interrupted`。
- **逐转换落盘**：node_states / rounds 每次状态变化即 UPDATE（§5.3）。
- **恢复**：前端对 `interrupted`（及 `paused`）运行显示"恢复运行"；后端从持久化状态重建 runner——DAG 模式对剩余节点（非 `done/skipped`）重算 Kahn，`done` 节点结果文本保留供模板渲染；自动编排从 `rounds` 历史注入下一轮。**崩溃时在途（`running`）节点直接重新派发一次新回合（重做语义）**，不做会话历史对账。
- 运行态 runner 对象在内存（每 run 一个 `asyncio.Task`，运行服务单例持有）；重启即失，由上述恢复机制补偿。
- `failed` 仅用于不可恢复错误（如协调者/成员会话已不存在且无替代恢复路径）；可恢复的节点失败一律走 `paused` + 用户处理（重试/跳过）。

### 6.6 事件流（SSE）

按 collab 的"事件历史 + 活订阅者 + 重放 + 心跳"模式实现每 run 的多路器。事件：

| 事件 | 载荷要点 | 模式 |
|---|---|---|
| `node_status` | node_id, member_id, status, error? | dag |
| `dag_progress` | done/running/pending/skipped/failed/total | dag |
| `round` | n, max_rounds | auto |
| `dispatch` | coordinator → assignments[] | auto |
| `message` | direction: sent\|stream\|reply, member_id, session_id, text, parts? | 两者 |
| `busy` / `paused` / `error` / `stopped` | reason, node_id? | 两者 |

重启后内存事件历史丢失，`GET stream` 退化为：从 run 行的 node_states 构造快照 + 实时事件。

## 7. API 设计

`astrbot/dashboard/api/agent_teams.py`，v1 router（`ScopeDependency("chat")`）+ legacy router 双注册（`router.py` 规范），错误经 `astrbot.dashboard.responses`。

| 路由 | 说明 |
|---|---|
| `POST/GET/PATCH/DELETE /agent_teams[/{id}]` | 团队 CRUD（DELETE 保留成员会话） |
| `POST /agent_teams/{id}/members`、`DELETE .../members/{mid}` | 成员增删（服务端自动建会话/绑人格） |
| `GET/POST /agent_teams/{id}/workflows`、`GET/PUT/DELETE /agent_teams/workflows/{wid}` | 工作流 CRUD（PUT 含 graph+layout，服务端校验） |
| `POST /agent_teams/{id}/runs` `{mode, input, workflow_id?}` | 启动运行 → `{run_id}`；同团队活跃 run 冲突 → 409 |
| `GET /agent_teams/{id}/runs`、`GET /agent_teams/runs/active` | 历史 / 活跃 |
| `POST /agent_teams/runs/{rid}/stop｜pause｜resume` | 运行控制 |
| `POST /agent_teams/runs/{rid}/nodes/{nid}/retry｜skip` | 失败节点处理 |
| `GET /agent_teams/runs/{rid}/stream` | SSE：重放 + 实时 + 心跳 |

## 8. 前端设计

新视图 `dashboard/src/views/AgentTeamsView.vue`（路由 + 菜单入口，替换 collab 入口），组件置于 `dashboard/src/components/agent_teams/`，文案走 i18n（`features/agentTeams.json`，en-US/zh-CN/ru-RU）。

布局：左侧栏（团队列表 / 成员列表带协调者徽标与模型标签 / 新建入口）+ 主区三 Tab：**工作流编排**、**运行监控**、**历史**。

- **成员创建对话框**：模式二选一——"从人格创建"（persona 下拉 + 名称 + 可选模型覆盖）/"自定义创建"（system_prompt + 模型）；协调者开关（全团唯一，启用时自动替换原协调者）。
- **Workflow 编辑器**：**Vue Flow**（`@vue-flow/core`）。节点=成员卡；选中节点右侧属性面板编辑任务模板（`{{input}}`/`{{节点}}` 变量快捷插入）；前端即时环检测提示 + 保存时后端终审；画布位置随 `layout` 持久化；成员被删 → 节点红框 + 顶部校验横幅。
- **运行监控**：
  - 每成员一窗格：名称/状态角标 + 消息流（本 run 的 `message` 事件按成员过滤，复用 collab transcript 渲染链——`useAgentCollab.ts` 与 `systemStream.ts` 的 chain_type 分发组件抽公共化）。
  - 布局：自适应网格（列数=⌈√n⌉）+ `grid-layout-plus` 拖拽调整大小/位置（localStorage 持久化）+ 点击全屏浮层。
  - DAG 视图：同一 Vue Flow 画布的只读监控模式，节点着色 pending 灰 / running 蓝脉冲 / done 绿 / failed 红 / skipped 黄，顶栏进度条（`dag_progress`）。**编辑器与进度视图是同一组件的编辑/监控双模式**。
  - 控制条：目标输入 + 模式选择 + 开始/暂停/恢复/停止/恢复运行（interrupted）；paused 且有失败节点时弹节点操作（重试/跳过/终止）。

## 9. 复用清单（均已核实存在）

| Teams 需求 | 复用设施 | 位置 |
|---|---|---|
| 成员会话创建 + 人格绑定 | session create + conversation persona | chat_service / conversation_mgr |
| 注入回合成为一等运行 | `register_synthetic_chat_run` | chat_service.py:1851 |
| 投递/流式回收 | webchat_queue_mgr + subscribe_system + BotMessageAccumulator | webchat_queue_mgr / chat_service.py:314 |
| 忙检测 | `chat_runs_by_session` | chat_service.py:1017 |
| DAG 分层/级联跳过/注入限长 | 算法移植 | subagent_dag.py |
| 会话内工具按需注入先例 | `_apply_subagent_manager_tools` | astr_main_agent.py:1178 |
| temp 上下文注入模式 | `collab_context` extra + `mark_as_temp()` | webchat_adapter.py:305 / astr_main_agent.py:1715 |
| 停止传播 | `/chat/sessions/{id}/stop` → active_event_registry | chat.py:354 |
| per-成员模型/人格 | persona_id 绑定 + set_provider | persona_mgr / provider manager |
| SSE 重放模式 | 事件历史+订阅+心跳 | agent_collab.py:99 |

## 10. 错误处理与边界

- 上限（模块常量，v1 不进 `default.py` 配置节）：成员 ≤10、节点 ≤20、并行 ≤5、轮次 ≤20；`reply_timeout` 默认 600s（长于 collab 180s，成员可能调工具/subagent 干长活）；忙等轮询 2s。
- 成员会话在 chat 页被外部删除 → collect 失败 → 节点 `failed` → 走失败策略；团队校验接口列出缺失成员。
- `team_dispatch` 未知成员名：工具回执错误 + 有效名册，模型当轮自纠；仍失败则轮次失败 → paused。
- 停止语义：stop = 放弃收集 + 传播成员会话取消 + 终态；pause = 在途回合完成后挂起（结果保留，可恢复）。
- 运行期注册的工具必须在所有终态/暂停路径上注销，防止泄漏到普通聊天回合。

## 11. 测试策略

- **后端 pytest**：沿 collab 的 ports 注入范式，脚本化 fake deliver/collect 覆盖——DAGRunner：分层/并行上限/失败暂停/auto_skip/重试/级联跳过/落盘时序；AutoOrchestrator：工具派发/未知成员自纠/多轮回传/`team_finish`/无工具提醒/轮次上限；热恢复：kill→重启→恢复→完成节点不重跑、在途重做；registry：分配与注销时序（普通回合不可见）。
- **API**：FastAPI TestClient 冒烟（CRUD/鉴权/校验报错/409）。
- **前端 vitest**（沿 `*.spec.ts` 既有模式）：环检测提示、grid 布局计算、SSE 事件→窗格过滤映射、双模式画布状态着色。

## 12. 实施分期（每片独立可验收）

1. 数据模型（3 表 + 迁移）+ 团队/成员 CRUD API（会话自动创建、人格绑定、唯一性校验）
2. TeamPorts 抽取 + DAGRunner + runs API + SSE + 逐转换落盘 + 启动清扫 + resume 端点（热恢复随 runner 基础一并落地）
3. 前端面板框架 + 成员管理 + 运行监控 grid（先接 DAG 模式端到端，含恢复按钮）
4. Workflow CRUD/校验 + Vue Flow 编辑器（编辑/监控双模式）+ DAG 进度视图
5. AutoOrchestrator：`agent_team_tools.py`（core）+ registry + build_main_agent 合并 + 前端自动编排模式
6. stop 传播完善、布局持久化打磨、i18n 补全、Collab 入口退役与清理

## 13. 已否决的备选方案

| 备选 | 否决理由 |
|---|---|
| 协调者文本指令协议（collab-route 式） | 依赖模型遵循格式，解析失败整轮报废；JSON-Schema 工具可在当轮自纠 |
| 直接复用 `SubAgentDAGEngine` | 它执行 in-process handoff 工具调用，与跨会话消息注入的执行模型不匹配；仅移植调度思想 |
| 团队/工作流/运行存 preferences KV | 不利于查询/清理/规模增长；collab 现状即债，不延续 |
| 绑定已有会话为成员 | 结构上产生跨团队会话引用与历史污染；v1 create-only 杜绝 |
| 崩溃在途回合做会话历史对账 | 进程重启时成员 agent 循环同死，历史只有残片；重做语义简单且正确 |
