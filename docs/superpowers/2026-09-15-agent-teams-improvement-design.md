# Agent Teams 改进方案（总纲）

- 日期：2026-09-15
- 性质：改进方案总纲（umbrella design）。响应 [2026-09-14 评审建议](2026-09-14-agent-teams-improvement-recommendations.md)；各批次实施前仍按惯例出独立 design spec，本文作为其共同基线与裁定记录。
- 评审基线：`specs/2026-09-05-agent-teams-design.md`（已落地）+ Refinements 1-3（已落地）；Phase 1-5（条件分支/Human Input/循环边/子工作流/模板库）spec 已入库（`5e28ff969`），实现代码在 `feat/agent-teams-workflow-phases` 分支、未合入主线。
- 事实核对：2026-09-14 报告中的全部代码论断已逐条核实（波次屏障 `agent_team_run_service.py:791-794`、500 字符摘要 `:1034`、忙等轮询 `:405-408`/`:1085-1088`、`reply_timeout=600` 默认值 `agent_team_service.py:26`、流事件自带 `ts` 时间戳 `agent_team_ports.py:210`、Phase 1 围栏 JSON 契约 `conditional-branching spec` §输出格式）。

## 1. 对 2026-09-14 建议的裁定

| 建议 | 裁定 | 说明 |
|---|---|---|
| 2.1 事件驱动调度 | **采纳** | 补充设计见 §2.1（per-member FIFO 链替代忙等、失败策略内联到节点完成点） |
| 2.2 空闲超时 | **采纳，需修正** | 原方案低估了工具执行期无流式增量的误杀面，改为两阶段落地，见 §2.2 |
| 2.3 重做注入尝试元数据 | **采纳** | 见 §2.3 |
| 3.1 submit_result 工具化输出契约 | **采纳，且为 Phase 3 合并前置** | 补充回退路径与失败语义修正，见 §3.1；需同步修订 Phase 1 spec |
| 3.2 team_read + digest 索引化 | **采纳** | 见 §3.2 |
| 3.3 受控直传 inputs | **采纳，降为批次 3 末位** | 仅 auto 模式（DAG 已有模板注入），见 §3.3 |
| 4.1 run 级预算与 deadline | **采纳** | 补充用量归集缝设计，见 §3.4 |
| 4.2 续聊恢复 resume_chat | **采纳** | 默认 redo，见 §3.5 |
| 4.3 成员记忆策略 | **采纳，收缩范围** | v1 只做 `carry`(现状)/`fresh`(每 run fork) 两档，摘要压缩缓行，见 §3.6 |
| 5.1-5.4（随 roadmap） | **采纳** | 并入 §4 批次 4，其中 5.4 被 §2.1 的成员链直接取代 |

### 对原报告的三处修正

1. **2.2 的活动信号覆盖不全**。`collect` 只在纯文本增量（`plain` streaming delta）上产生事件（`agent_team_ports.py:195-217`）；成员正在执行长工具调用（这正是 `reply_timeout` 被迫调大的另一主因）时没有文本增量，朴素"120s 无增量即失败"会把最需要保护的场景误杀。修正：活动信号必须覆盖工具调用阶段（复用 system 流中同 `message_id` 的非文本消息，spec 阶段核实消息类型清单），落地分两阶段（§2.2）。
2. **3.1 缺失败语义与回退路径**。Phase 1 spec 规定"JSON 格式错误 → run `paused`"，对单节点格式问题过重；且未定义成员绑定 `output` 后不调用工具的行为。修正：缺失/非法一律走节点 `failed` → 团队 `failure_policy`，并保留围栏解析为回退（§3.1）。
3. **4.1 未指明用量归集缝**。成员回合已经过 synthetic chat run 注册与 execution token 绑定两道既有缝，用量归集不需要新基础设施（§3.4）。

## 2. 批次 1（P0）：runner 内部

### 2.1 事件驱动调度（数据流协程图）

**目标行为**：快节点的后继不再被同波慢节点阻塞；同成员串行不再靠 2s 轮询；`max_parallel` 只约束同时在途的执行，不约束"一批"的边界。

**设计**：

- 为每个 pending 节点创建一个 `_node_flow(node)` 协程（节点 ≤20，一次性全部拉起；恢复路径同理"重建未完成节点的任务图"）：
  1. `await` 全部前驱的完成事件（`asyncio.Event`，节点到达终态 done/skipped/failed 时在 finally 中 set）；
  2. 复查自身状态：已被级联 skip / run 已停 → 直接返回；
  3. **per-member FIFO 链**：`await` 该成员会话上的上一个 flow 任务，然后把自己登记为链尾。替代 `_wait_if_busy` 轮询；
  4. `await asyncio.Semaphore(max_parallel)`；
  5. 若 run 处于 paused：等待 resume wake（在途排空语义）；
  6. 调用现有 `_execute_node`（TeamPorts、落盘时序、事件发射均不变）；
  7. 节点失败时**内联**执行失败策略（auto_skip 级联 / run 转 paused），不再按波批次执行。
- 调度主循环退化为：等待"任一 flow 完成 / retry / skip / resume / stop"的唤醒事件，然后判断终态（全部终态 → completed）或继续 parked。
- **无死锁论证**：flow 的等待对象只有前驱事件（有限必达）、成员链前驱任务（其收集受超时约束、有限必达）、信号量（finally 释放）、resume/stop（用户控制）；等待顺序恒为"先链后信号量"，链与前驱图均无环。
- **语义变化**（需在 changelog 注明）：`pause` 从"波间暂停"变为"停止放行新节点 + 在途排空"；`_apply_failure_policy` 从每波一次变为每节点完成时一次；`retry_node`/`skip_node` 在 set 状态的同时 set 对应前驱事件以唤醒下游 flow。
- `AutoOrchestrator._member_wave` 同步换用 per-wave 成员链（派发顺序即链序），信号量保留；协调者轮次结构不变。

**测试**：现有 scripted ports 用例全部保留；新增"快节点后继先于慢节点完成启动"的重叠断言、同成员两节点串行断言、pause 排空断言、resume 重建断言。

### 2.2 空闲超时（两阶段）

**阶段 A（随批次 1 落地）**：

- 配置新增 `idle_timeout: float = 300.0`（0 = 关闭）；`reply_timeout` 语义收窄为**硬顶**（默认维持 600，0 = 关闭），作为空闲超时的兜底背板。
- 现有 deadline 循环（`agent_team_run_service.py:614-636`）改造：固定 deadline → `min(最后活动时刻 + idle_timeout, 开始时刻 + reply_timeout)`；choice 挂起的 +300s 重臂逻辑保留且优先级最高。
- 活动信号（阶段 A 范围）：流式增量、choice shown/resolved。收集侧把 `_on_choice_event` 扩展为通用 `_on_event`（所有事件刷新 `last_activity`）。
- 超时错误信息区分 `idle timeout` / `reply timeout`，接入 §4-N3 失败原因结构化。

**阶段 B（核实后增强，可独立 spec）**：核实 system 流中同 `message_id` 的非文本消息类型（工具调用开始/结束、思考增量等）能否作为活动信号；若可用，`collect` 对未知 `msg_type` 补发轻量 `activity` 事件，使长工具调用期不被判空闲。若核实不可用，维持阶段 A 参数（idle 300 + 硬顶 600）并在文档注明局限。

### 2.3 重做/续聊注入尝试元数据

- `team_context` 附加行：`（第 <n> 次尝试，此前尝试被中断：若已有部分进展请继续完成，否则重做）`。
- 重做（redo）时附上次产出尾部片段 ≤300 字作为锚点（来自 `node_states[id].result`/`error`）。
- 覆盖三个入口：`retry_node`、Tier-1 热恢复重建、§3.5 续聊恢复。改动集中在 runner 组装 context 的两处（DAG `_execute_node` 与 auto `_member_wave`），无配置项。

## 3. 批次 2-3（P1）：输出契约、信息流、边界

### 3.1 submit_result：工具化结构化输出契约（Phase 1-3 合并前置）

**节点 schema 扩展**：节点新增可选 `output` 块：

```json
{ "output": { "schema": {"properties": {"approved": {"type": "boolean"}, "score": {"type": "number"}}, "required": ["approved"]}, "required": true } }
```

- v1 约束（KISS）：顶层仅 object；属性类型限 `string|number|boolean|array|object`；嵌套深度 ≤2；不做完整 JSON Schema。

**运行时协议**：

- 对声明了 `output` 的节点回合，经现有 per-umo registry（`AgentTeamToolRegistry`）注册 `submit_result(result: object)` 工具，仅该回合可见。
- 校验失败 → 工具回执 `{ok: false, error: "..."}` → 模型当轮自纠（与 `team_dispatch` 未知成员名的自纠同构）；成功 → 回执 `{ok: true}`，runner 写 `structured_output`。
- 终态判定优先级：工具提交 > 围栏回退解析（兼容 Phase 1 spec 已有 ` ```output ` 提取器）> 无。
- `required: true` 且两者皆无 → 节点 `failed`（error=`structured_output_missing`）→ 走团队 `failure_policy`，**不再直接 run paused**（修正 Phase 1 spec 语义）。
- 存储复用 Phase 1 已设计的 `structured_output` / `output_error` 字段，条件表达式求值 `{{node.output.field}}` 不变。

**编辑器校验**：条件边的源节点必须声明覆盖所引用字段的 `output.schema`；未声明 → 保存时校验错误。

**附带收益**：`{{节点}}` 模板注入可引用 `{{<node_id>.output.<field>}}`（取结构化字段的字符串化），`collect` 全文与"发布结果"自然分离。

**Phase 1 spec 修订项**（在 `feat/agent-teams-workflow-phases` 合并前完成）：输出契约一节改为"submit_result 为主、围栏为回退、缺失走 failure_policy"；条件求值错误处理保留。

### 3.2 team_read 与 digest 索引化

- 新工具 `team_read(member: string)`：返回该成员本 run 最新完整结果，截断至团队配置 `read_max_length: int = 8000`；仅 auto 模式协调者 umo 注册（沿用 `_apply_agent_team_tools` 的并入路径）。
- `_results_digest` 降级为索引：`<name>: [完成|出错|执行中] <一句话 ≤100 字> · 共 N 字 · team_read 可读全文`；协调者备注行保留。
- run 结束/中断时 coordinator 注销逻辑不变。

### 3.3 受控直传（inputs）

- `team_dispatch` 的 assignment 新增可选 `inputs: ["成员A", ...]`：runner 把被引用成员**最新一轮完整结果**（若已绑定 output 则取 `structured_output` 序列化）以 `【引用 成员A 的结果】` 块注入任务 context，受 `inject_max_length` 截断。
- 仅 auto 模式；DAG 模式已有模板注入 + 未引用前驱自动注入，不加。成员自身不获得任何通信能力，星型模型不变。
- 依赖：不硬依赖 3.1，但结构化字段引用在其之后才可用。

### 3.4 run 级预算与 deadline

- 团队配置新增 `run_budget_tokens: int = 0`（0 = 不限）、`run_deadline_seconds: int = 0`（0 = 不限）。
- **用量归集缝**（无新基础设施）：synthetic chat run 注册（`register_synthetic_chat_run`）扩展携带 `run_id`；agent 回合完成处按 `message_id` 归集 provider 回执 usage 到 run 级累加器，落 `agent_team_runs` 新增可空 JSON 列 `usage_stats`。v1 只统计主管线 LLM 用量（插件/subagent 附加调用不计入，文档注明"近似"）。
- 熔断语义：超预算/超时 → emit `budget_exhausted` / `deadline_exhausted` → run 转 `paused`（非 failed），`usage_stats` 保留已耗用量；用户上调配置后 resume，从已耗用量继续累计。
- auto 模式现有 `max_rounds` 保留；DAG 模式由此获得总闸。

### 3.5 续聊恢复（可选恢复策略）

- 团队配置新增 `recovery_strategy: "redo" | "resume_chat" = "redo"`（重启扫描/中断恢复时对在途节点生效）。
- `resume_chat`：向成员发续聊回合——"你此前的任务 `<task>` 因中断未确认完成，请报告当前进度并继续"，复用 2.3 的元数据注入；成员会话历史天然持久（`persist_user_history: True`），无需任何对账设施。
- 仅一次续聊回合；若续聊回合再次失败/超时 → 按节点 failed 走失败策略。

### 3.6 成员记忆策略（收缩版）

- 成员定义新增 `memory_policy: "carry" | "fresh" = "carry"`。`carry` 为现状（长生命周期会话跨 run 保留历史）；`fresh` 为每 run 启动时 fork 全新会话（对齐 conversation 服务现有 fork 能力，run 结束不回写原会话）。
- 摘要压缩档缓行：待真实跨 run 污染案例出现再设计（KISS）。

## 4. 批次 4：随 roadmap 落地项与新增功能

### 4.1 原报告 §5 各项

1. **干跑校验**（随 Phase 1-3）：保存/运行前静态可达性分析——human_input 终点必达检查、循环迭代预算收紧检查、条件字段覆盖检查（3.1 的编辑器校验是其子集）。
2. **Subworkflow 恢复原子性**（随 Phase 4）：定义父 run 视角下子 run 在途节点重做的状态（`running(child)`）；子 run `paused` 级联暂停父 run，恢复时先子后父。
3. **golden run 录制/回放**：`TeamPorts` 外包一层 recorder（记录 deliver/collect/is_busy 调用序列与返回为 JSON），回放驱动 runner 做回归断言（终态 + 事件形状）；工作流模板可带 `verified` 标记。
4. **同成员波内去重**：被 §2.1 per-member FIFO 链取代，无独立工作。

### 4.2 新增功能建议（原报告未覆盖）

| # | 功能 | 设计要点 | 成本 |
|---|---|---|---|
| N1 | **auto 模式 team_ask_user** | 协调者工具 `team_ask_user(question, options?)`：run 转 `paused`（reason=`coordinator_question`）+ 事件；用户经 Phase 2 human-input 的 submit API 作答后 resume，答案注入协调者下一轮 context。复用 choice/挂起机制与既有 API 形态，无新 UI | 低 |
| N2 | **节点自动重试** | 节点级 `retry: {max_attempts: 0=关, backoff_seconds: 5}`；仅对瞬态失败（idle/reply timeout、provider_error）自动重派，携带 2.3 尝试元数据；与 3.4 预算联动（重试计入预算） | 低 |
| N3 | **失败原因结构化** | 错误分类枚举 `{idle_timeout, reply_timeout, provider_error, config_missing, structured_output_missing, cancelled, ...}`，写入 `node_states[].error_reason` 与 SSE `node_status` 事件，RunsHistory 支持筛选；是 N2 的前置 | 低 |
| N4 | **团队导出/导入** | JSON bundle（team + members + workflows，persona/provider 按名称引用，导入时校验 + ID 重映射）；作为 Phase 5 模板库的文件格式前身先行落地 | 中 |
| N5 | **结果回发平台会话** | run 启动参数可选 `notify_umo`：终态（completed/failed/paused）时把 `result_summary` 经既有发送管线推回该平台会话。不打破"成员=WebChat"非目标（执行面不变，仅结果回发）——把团队入口延伸到 QQ/TG 等场景 | 中 |
| N6 | **外部/定时触发** | run 创建 API 已存在；补 API token 作用域约束 + （若面板已有定时任务基础设施则复用）cron 定时触发。需先核实定时基础设施，可后置 | 中 |
| N7 | **跨团队并发闸** | owner 级 `max_concurrent_runs`（默认 2）+ 全局信号量；当前仅同团队 409，跨团队无保护 | 低 |
| N8 | **运行中成员追加指令（排队 steer）** | service API `POST .../members/{mid}/steer`（text）→ `ports.deliver` 新回合（忙等/成员链保证排在当前回合之后），transcript 记 `direction=steer`；AgentWindow 加输入入口。dsh 的 steer 在 AstrBot 注入时机受限下的低成本近似 | 低 |

**v1 后继续列为非目标**：成员间点对点自由消息、事件溯源改造、平台会话作成员、完整 JSON Schema、记忆摘要压缩、多 owner 权限体系。

## 5. 配置与数据模型变更汇总

| 层 | 变更 | 批次 |
|---|---|---|
| 团队 config | `idle_timeout`(300)、`reply_timeout` 语义收窄为硬顶、`read_max_length`(8000)、`run_budget_tokens`(0)、`run_deadline_seconds`(0)、`recovery_strategy`(redo) | 1 / 3 |
| 成员定义 | `memory_policy`(carry) | 3 |
| 节点 schema | `output {schema, required}`、`retry {max_attempts, backoff_seconds}` | 2 / 4-N2 |
| assignment | `inputs[]` | 3 |
| DB | `agent_team_runs` 新增可空 JSON 列 `usage_stats`；`node_states` JSON 内新增 `error_reason`/`attempt` 键（无迁移） | 3 / 4-N3 |
| API | `POST .../members/{mid}/steer`、团队 export/import；Phase 2 human-input submit API 复用于 N1 | 4 |
| SSE | `budget_exhausted`、`deadline_exhausted`、`node_status.error_reason`、（可选）`activity` | 3 / 4 |

## 6. 测试策略

- **批次 1**：scripted ports 全量回归 + 新增调度重叠/串行/排空/重建断言；超时用注入时钟或极小超时值驱动（deadline 循环的时间源收敛为可注入）。
- **批次 2**：submit_result 校验回执-自纠回路（脚本化成员回合：先错后对）、围栏回退、`required` 缺失 → failed → failure_policy 矩阵；编辑器校验单测。
- **批次 3**：team_read 截断与索引 digest 快照测试；预算熔断 → paused → resume 续耗用例；inputs 注入与截断用例。
- **批次 4**：golden run 录制/回放作为工作流模板回归的长期载体；干跑校验的静态分析用例集。

## 7. 实施顺序（PR 切分）

1. PR-1：事件驱动调度 + per-member 链（§2.1）——spec：[specs/2026-09-15-agent-teams-event-driven-scheduling-design.md](specs/2026-09-15-agent-teams-event-driven-scheduling-design.md)
2. PR-2：空闲超时阶段 A + 失败原因结构化（§2.2 + N3）——spec：[specs/2026-09-15-agent-teams-idle-timeout-design.md](specs/2026-09-15-agent-teams-idle-timeout-design.md)
3. PR-3：尝试元数据（§2.3）——spec：[specs/2026-09-15-agent-teams-redo-attempt-metadata-design.md](specs/2026-09-15-agent-teams-redo-attempt-metadata-design.md)
4. PR-4：submit_result + Phase 1 spec 修订（§3.1，Phase 3 循环边合并的前置闸）
5. PR-5：team_read + digest 索引化（§3.2）
6. PR-6：run 预算/deadline + `usage_stats` 列（§3.4）
7. PR-7：inputs 直传（§3.3）→ 续聊恢复 + 记忆策略（§3.5/3.6）
8. PR-8+：批次 4 各项按 roadmap 对齐落地（N1 建议随 Phase 2 合并；N4 先于 Phase 5）

每个 PR 独立可回滚；批次 1 三个 PR 落地后建议做一次长任务实测（含长工具调用成员）校准 `idle_timeout` 默认值。
