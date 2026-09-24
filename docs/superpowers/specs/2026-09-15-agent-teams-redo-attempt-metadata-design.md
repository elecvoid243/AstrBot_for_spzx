# Agent Teams 重做注入尝试元数据设计

- 日期：2026-09-15
- 状态：设计中
- 来源：[2026-09-14 评审建议 §2.3](../../2026-09-14-agent-teams-improvement-recommendations.md)、[2026-09-15 改进总纲 §2.3](../../2026-09-15-agent-teams-improvement-design.md)（批次 1 / PR-3）
- 基线：`specs/2026-09-05-agent-teams-design.md` §6.5（Tier-1 恢复，已落地）

## 1. 背景与问题

成员会话历史是持久的（`persist_user_history: True`）。DAG 节点失败/中断后经 `retry_node`（`agent_team_run_service.py:359-380`）或重启后重试时，同一任务文本被**原样重派**给该成员——成员看到的是同一任务的第二遍，却不知道上一遍的产出是被中断的残次品。典型症状：回答"我已经做过了"，或基于上一遍的中断现场给出混乱拼接。

现状注入点：`_execute_node` 经 `ports.deliver(session_id, task_text, None, execution_token=token)` 派发（`:556`），**`context` 参数恒为 `None`**（auto 模式的 `[团队任务]` 前缀不走此路径）。`team_context` 以 `mark_as_temp()` 临时注入、不污染成员历史的机制已就绪，本设计只是在该缝隙上补齐语义。

## 2. 范围裁定

- **仅 DAG 模式**。auto 模式没有"重做"概念：协调者轮次模型下，恢复即开启新一轮、协调者凭 digest 自行决定重派内容；其跨轮重派是正常流而非异常重做，注入 attempt 元数据反而会污染语义。
- 三个触发入口中两个归属本 spec：`retry_node`（手动重试）与 Tier-1 热恢复后的重试（boot_sweep 标记 interrupted → 用户 retry，同经 `retry_node`，天然覆盖）。第三入口 `resume_chat`（续聊恢复）属批次 3 的 `recovery_strategy` spec，不在本 spec。

## 3. 设计

### 3.1 节点状态扩展

`node_states[id]` 新增两个可选键（JSON，无迁移）：

| 键 | 类型 | 语义 |
|---|---|---|
| `attempt` | int | 已派发次数，缺省视为 1 |
| `prior_tail` | str \| None | 上一次尝试的产出/错误尾部锚点（≤300 字），仅在下一次派发时消费，节点到达终态后清除 |

### 3.2 retry_node 的锚点捕获

现状 `retry_node` 直接 `result=None, error=None` 重置（`:372-378`），锚点信息随之丢失。改为重置前暂存：

```python
tail = ((state.get("result") or "") + "\n" + (state.get("error") or "")).strip()
state.update(
    status="pending", error=None, result=None,
    started_at=None, finished_at=None,
    attempt=int(state.get("attempt") or 1) + 1,
    prior_tail=tail[-300:] or None,
)
```

- 锚点取"上次产出尾部 + 上次错误"拼接的**尾部** 300 字（中断现场多在末尾；错误信息通常比残缺正文更有定位价值）。
- `_execute_node` 到达终态（done/failed/skipped）时置 `prior_tail=None`（`attempt` 保留供展示）。

### 3.3 派发注入

`_execute_node` 组装 context（现恒 `None` 处）：

- `attempt == 1` 且无 `prior_tail`：维持 `None`（首派发零变化）。
- 否则：

```
（任务重试：第 {attempt} 次尝试。此前尝试被中断且未确认完成。
若你已有部分进展请继续完成；否则重新完成本任务。）
[上次尝试的尾部片段]
{prior_tail}
```

- 经 `ports.deliver` 的 `context` 参数走既有 `team_context` 临时注入通道；截断上限沿用 300 字，不引入新配置项。

### 3.4 事件与展示

- `node_status` 事件（running/终态）附带可选 `attempt` 字段；`node_states` 本就随 `dag_progress`/快照下发，前端可在节点卡片显示"第 N 次"徽标——**前端改动不在本 spec 范围**，字段先行。

## 4. 数据模型 / API / SSE

- 无表结构变更、无 API 变更；`node_states` 增可选键。
- SSE `node_status` 增可选 `attempt` 字段（向后兼容）。

## 5. 测试策略

1. 首派发：`context` 为 `None`，状态无 `attempt`/`prior_tail` 键（或缺省语义）。
2. retry 一次：`attempt=2`；deliver 收到的 context 含"第 2 次尝试"与上次尾部片段；节点完成后 `prior_tail` 清空。
3. retry 多次：attempt 累加；锚点始终是最近一次尝试的尾部。
4. 失败（error 无 result）后 retry：锚点含错误信息。
5. 重启恢复：persist 的 `attempt` 重建后保留；interrupted → retry 路径同 §5.2。
6. auto 模式回归：`_member_wave` 的 context 组装不受影响。

## 6. 实施切分与回滚

- 单 PR，改动集中在 `retry_node` 与 `_execute_node` 的 context 组装两处，约几十行 + 测试；与 PR-1/PR-2 无依赖（若 PR-1 先落地，`retry_node` 的状态重置逻辑按其版本适配）。
- 回滚：revert 单 commit；`node_states` 中残留的 `attempt`/`prior_tail` 键对旧代码不可见（JSON 未知键被忽略），双向兼容。

## 7. 已否决的备选方案

- **在任务模板文本中内联重试说明**：污染 `task_rendered` 与模板语义（模板是用户产物，重试元数据是运行时状态），且 `{{}}` 渲染管线要为此增加隐藏变量。
- **auto 模式同步注入 attempt**：协调者轮次模型无重做语义（§2），注入反而让"协调者主动重派"与"异常重做"无法区分。
- **携带上次完整产出**：成员历史里已有上次回复全文（会话历史持久），锚点只需帮助成员定位"上次做到哪"，300 字尾部足够；全文注入浪费窗口且诱导复读。
