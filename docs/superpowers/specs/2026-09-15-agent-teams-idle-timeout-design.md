# Agent Teams 空闲超时设计（阶段 A）

- 日期：2026-09-15
- 状态：设计中
- 来源：[2026-09-14 评审建议 §2.2](../../2026-09-14-agent-teams-improvement-recommendations.md)、[2026-09-15 改进总纲 §2.2](../../2026-09-15-agent-teams-improvement-design.md)（批次 1 / PR-2，含失败原因结构化的最小子集）
- 基线：`specs/2026-09-05-agent-teams-design.md` §6.3/§10（已落地）

## 1. 背景与问题

成员回合的收集超时是固定墙钟 `reply_timeout`（默认 600s，`agent_team_service.py:26`）：成员正活跃地流式输出或连续调用工具时也可能被判超时。这正是默认值从前身 collab 的 180s 翻倍的根因——不是成员真需要 600s，而是怕误杀。三个生效点：

| 生效点 | 位置 | 现状行为 |
|---|---|---|
| DAG 节点收集 | `agent_team_run_service.py:614-636` | deadline 循环；ask_user_choice 挂起期间 +300s 重臂；超时 → 节点 failed `"reply timeout"` |
| auto 成员波收集 | `:1292-1300` | `asyncio.wait_for(collect, reply_timeout)`；超时结果写入该成员本轮 error |
| auto 协调者回合 | `:1186-1215` | `asyncio.wait_for`；超时 → run `paused`（reason=`coordinator timeout`） |

**关键约束（已核实）**：`collect` 所消费的 system 流只承载 `plain`（含流式增量，带 `ts`）、媒体附件、`end`/`complete`、`interactive_choice(_resolved)`（`webchat_event.py` `_send` 镜像清单 + `chat_service.py:1673-1677` 的 choice 定向镜像）。**think 增量、tool_call、subagent 进度事件只存在于 dashboard chat-run 流，不进 system 流**。因此"无文本增量 = 空闲"的朴素判据会把最需要保护的长工具调用期误杀——这是本设计分两阶段的根由。

## 2. 目标与非目标

### 2.1 目标（阶段 A）

- G1 活跃即豁免：只要回合有（system 流可见的）活动，就不因墙钟到点被砍；`reply_timeout` 语义收窄为**硬顶兜底**。
- G2 挂死仍被剪：真挂死的回合在空闲窗口内失败，默认窗口可比 600s 更激进。
- G3 语义兼容：choice 挂起的 +300s 重臂、stop 竞态、超时后的节点/run 状态收敛全部保留；`TeamPorts` 签名不变。
- G4 失败原因可区分：超时错误区分 `idle_timeout` / `reply_timeout`，进入结构化字段。

### 2.2 非目标

- 不在阶段 A 解决工具执行期的活动信号（system 流当前无此数据，阶段 B 另出 spec，§6）。
- 不改 `TeamPorts`、不改 webchat 队列管理器。
- 不引入费用/预算类熔断（批次 3 的 run 预算 spec）。

## 3. 设计

### 3.1 配置

`DEFAULT_TEAM_CONFIG` 新增/修订（`agent_team_service.py`）：

| 字段 | 默认 | 语义 |
|---|---|---|
| `idle_timeout` | `300.0` | 无活动判失败的空闲窗口；`0` = 关闭空闲判定（退化为纯硬顶模式） |
| `reply_timeout` | `600.0`（保留） | **硬顶**：单回合从开始计时的最大墙钟；`0` = 关闭硬顶 |

- 校验：`idle_timeout` 为 0 或 ≥30；`reply_timeout` 沿用既有数值校验。两者均 >0 时实际生效闸取 `min` 语义（§3.2），不强制 idle ≤ hard。
- 两者均为 0 合法但危险（无任何超时），API 文档与 `--help` 级说明注明。

### 3.2 双闸 deadline 循环

DAG 生效点改造（`:614-636`）：

```python
clock: Callable[[], float] = time.time   # 可注入，默认 time.time
started = clock()
last_activity = started
# _on_choice_event 扩展为 _on_event：任意 collect 事件（stream delta /
# choice shown/resolved）刷新 last_activity
while True:
    if choice_pending:
        deadline = clock() + 300.0            # 现状重臂语义，优先级最高
    else:
        candidates = []
        if idle_timeout > 0:
            candidates.append(last_activity + idle_timeout)
        if reply_timeout > 0:
            candidates.append(started + reply_timeout)
        deadline = min(candidates) if candidates else clock() + 3600.0  # 双关兜底轮询
    done, _ = await asyncio.wait({collect_task, stop_task},
                                timeout=max(deadline - clock(), 0.05), ...)
    if collect_task in done: ...              # 现状
    if stop_task in done: return              # 现状
    if not choice_pending and clock() - last_activity >= idle_timeout > 0:
        raise TeamTimeout("idle_timeout", f"no activity for {idle_timeout:.0f}s")
    if 0 < reply_timeout <= clock() - started:
        raise TeamTimeout("reply_timeout", "reply timeout")
```

- 超时异常收敛为 `TeamTimeout(kind, message)`（服务内轻量异常，继承现有异常习惯）；捕获处按 `kind` 写节点失败文案：`idle timeout (no activity for Ns)` / `reply timeout`。
- choice 事件同时刷新 `last_activity`（用户交互本身就是活动）；choice 挂起时双闸均不判，仅重臂逻辑生效——与现状完全一致。

### 3.3 三处生效点统一

三个生效点的 deadline 逻辑同构（DAG 与 auto 两处现为重复实现）。按仓库 No Unnecessary Helpers 规则的复用标准（同一逻辑 ≥3 处），抽取**一个**服务内辅助：

```python
async def _collect_turn_with_deadlines(
    *, collect_factory, stop_event, on_event, idle_timeout, hard_timeout,
    clock=time.time,
) -> tuple[str, list]:
    """Race one member/coordinator turn's collection against idle and
    hard-ceiling deadlines, with the existing choice re-arm semantics.

    Args:
        collect_factory: Zero-arg callable building the collect task.
        stop_event: Runner stop signal.
        on_event: Callback receiving every collect event (activity source
            and choice suspension signal in one).
        idle_timeout: Idle window seconds; 0 disables.
        hard_timeout: Wall-clock ceiling seconds; 0 disables.
        clock: Time source, injectable for tests.

    Returns:
        (reply_text, message_parts) of the finished turn.

    Raises:
        TeamTimeout: With kind ``idle_timeout`` or ``reply_timeout``.
    """
```

- 三个调用点改为使用该辅助：DAG `_execute_node`、auto `_member_wave.run_one`、auto `_coordinator_turn`。
- 行为对齐收益：协调者回合当前**没有** choice 挂起处理（裸 `wait_for`），统一后获得同等的 +300s 重臂——协调者触发 ask_user_choice 时不再被墙钟误杀（修正现状不一致）。
- auto 波/协调者超时处的错误 metadata 同步携带 `kind`。

### 3.4 失败原因结构化（最小子集）

- `node_states[id]` 新增可选键 `error_reason`（JSON，无迁移）；本 spec 只引入两个值：`idle_timeout`、`reply_timeout`（`provider_error` 等其余枚举随批次 4 的 N3 补全）。
- SSE `node_status`（failed）事件附带可选 `error_reason` 字段；transcript system 行 `metadata` 同步。
- auto 模式：wave 结果项与 rounds 记录的 error 侧同样附 `error_reason`。
- 前端展示（RunsHistory 筛选等）不在本 spec 范围，随批次 4 N3 一并做。

## 4. 数据模型 / API / SSE

- 无表结构变更；`node_states` 增可选键。
- 团队 config 新增 `idle_timeout`（PATCH 校验生效）；OpenAPI 变更后按仓库惯例 `cd dashboard && pnpm generate:api`。
- SSE：仅 `node_status` 增可选字段，形状向后兼容。

## 5. 测试策略

deadline 循环时间源收敛为可注入 `clock`（本 spec 交付），用假时钟驱动：

1. 活跃豁免：持续流式增量 > `reply_timeout`（原墙钟必杀场景）→ 正常完成。
2. 空闲剪除：注入 N 秒无事件 → `idle_timeout` 触发，`error_reason=idle_timeout`，文案含窗口值。
3. 硬顶兜底：持续活动超过硬顶 → `reply_timeout` 触发。
4. 双 0 关闭：两闸均关时不超时（长 collect 由测试显式结束）。
5. choice 交互：choice 挂起期间空闲/硬顶均不判；+300s 重臂；resolve 后按剩余预算继续。
6. stop 竞态：stop 与超时同时就绪时 stop 优先（现状语义回归）。
7. 三个生效点参数传递矩阵（DAG 节点 / auto 波 / 协调者）。
8. 配置校验：`idle_timeout` 非法值（如 5、-1、字符串）拒绝。

## 6. 阶段 B 展望（另行出 spec，不在本 spec 实施）

目标：工具执行期不计为空闲。前置核实项与候选缝（本次已勘察）：

- 工具/子代理活动事件现状只写 dashboard chat-run 流（`subagent_event_sink.py` 直写 back 队列；`chat_service.py:1673-1677` 仅定向镜像 choice）。
- 候选方案：仿 `subagent_event_sink` 增加轻量 team 活动镜像（工具开始/结束 → `put_system_event`），或扩展 `chat_service` 的 system 流镜像清单。落地后 `collect` 补发 `activity` 事件即可被 §3.2 的 `_on_event` 自动消费——阶段 A 的活动通道无需再改。
- 阶段 A 默认 300s 的校准：批次 1（事件驱动调度）落地后，用含长工具调用成员的真实团队实测，决定是否收紧默认值或提前阶段 B。

## 7. 实施切分与回滚

- 单 PR：配置 + 辅助函数 + 三处调用点迁移 + `error_reason` 最小集 + 测试。
- 与 PR-1（事件驱动调度）无代码依赖，可并行；建议在其后合并以复用其测试基建节奏。
- 回滚：revert 单 commit；`idle_timeout` 字段残留于旧 config 无害（未知字段被合并默认值覆盖）。

## 8. 已否决的备选方案

- **在 `TeamPorts` 上暴露"最后活跃时刻"**（原建议方案）：活动已经由 collect 的 `on_event` 回调与 delta 事件到达 runner，再加一个 ports 级时刻字段是冗余接缝；且心跳属回合私有状态，放 runner 侧更内聚。
- **完全移除 `reply_timeout` 只留空闲超时**：在工具执行期无活动信号的现状下，空闲闸对"静默挂死的长工具调用"无能为力，硬顶是唯一的兜底——阶段 B 之前不可移除。
- **把默认 `idle_timeout` 直接定为 120s**：阶段 A 的活动信号不含工具期，120s 会重新引入误杀；以 300s 起步、实测校准（§6）。
