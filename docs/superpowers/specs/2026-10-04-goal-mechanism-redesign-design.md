# goal 机制重设计（机制重构）

- **日期**: 2026-10-04
- **作者**: elecvoid243
- **状态**: 待评审
- **分支**: `feat/goal-redesign`
- **前置**: 现有实现分析（本会话），发现 P0 状态竞态与权限缺口

## 1. 背景与现状问题

现有 `goal` 功能（`astrbot/builtin_stars/goal` + `astrbot/core/goal/*`）采用"续轮 = 伪造用户消息注入事件队列"的结构。源码分析确认以下结构性问题：

1. **状态竞态（P0）**：`evaluate_after_turn` 先读状态、`await judge`、再写回。judge 在途时 `clear/pause` 会被旧状态覆盖复活（已用最小脚本复现）。
2. **权限缺口（P0）**：`admin_only` 只作用于 `/goal set` 与 `/goal pause`，`status/resume/clear` 及全部 `/subgoal` 指令无检查，与配置说明"仅管理员可以使用 /goal 与 /subgoal 指令"矛盾。
3. **命令误触发风险**：kickoff 注入裸目标文本，若目标形如 `/some-command`，可触发第三方插件的命令/正则处理器；现有 `goal_continuation` 守卫只保护 goal 自己的指令。
4. **无法立即取消**：`pause/clear` 只阻止下一轮注入，当前回合继续跑完。
5. **重启语义不一致**：旧插件迁移会把 active 降级为 paused，但 kernel 自己的 active 状态在重启后原样保留，下一次任意回合结束即意外恢复循环。
6. **judge 成本与可靠性**：每轮固定一次额外 LLM 调用；transport 失败 fail-open 为 continue，静默烧主模型预算；响应截取保留开头（完成证据通常在末尾）；JSON schema 过宽（缺 `done` 字段不算解析失败）；阻塞被标记为 `done`，语义错误。
7. **UI 轮询脆弱**：固定"立即 + 4 秒"双刷，judge 超时默认 30 秒，慢 judge + inflight 去重可导致 sidebar 永久落后。

## 2. 设计目标与非目标

### 目标

- 从结构上消除状态竞态：任何时刻的 judge 结果只能作用于它被计算时的那一代状态。
- goal 回合成为管道的**一等内部回合**，不再伪装成用户消息，不参与命令/正则匹配。
- 完成信号结构化：控制工具优先，judge 兜底。
- `pause/clear` 立即停止当前回合。
- 重启后 active 状态统一降级为 paused。
- UI 改为推送，删除轮询 hack。
- 权限检查覆盖全部 `/goal` 与 `/subgoal` 指令。

### 非目标

- 不引入 planner/worker 任务图（能力超出现有需求证据，留待未来）。
- 不新增用户配置项；judge provider 沿用"留空则跟随当前会话"的单一真源策略。
- 不更换存储层（继续使用 shared-preferences KV）。
- 不改变 `/goal`、`/subgoal` 指令面与现有配置键。

## 3. 总体架构

```
/goal 指令面（builtin star，不变）
        │  统一权限门禁
        ▼
GoalService（控制面）
  ├─ GoalManager：状态机 v2（epoch + per-UMO 锁 + CAS）
  ├─ TurnDriver：构造内部回合 → 事件队列
  ├─ CompletionGate：goal_done/goal_blocked 工具信号 → 状态机
  └─ Judge（兜底）：严格三值协议 → 状态机
        │  状态变更
        ▼
webchat system stream：goal_state_changed 推送 → 前端缓存
```

## 4. 状态机 v2

### 状态与转移

```
idle ──set──▶ active ⇄ paused ──▶ done
                  │         └────▶ blocked   （judge/工具判定被阻塞、需用户输入）
                  ├─ parse/transport 连续失败 ─▶ paused(error)
                  └─ clear ─▶ idle
```

- `blocked` 与 `done` 分离：blocked 不展示"目标达成"，展示"需要你的输入：原因"。
- `resume` 仅允许 `paused | blocked → active`；对 active 执行 resume 为 no-op；done 不可 resume。
- 删除未使用的 `cleared` 状态值与 `mark_done` 死代码；`clear` 即删除记录。

### GoalState schema v2

```python
@dataclass
class GoalState:
    goal: str
    goal_id: str            # set 时生成的 uuid；区分"替换目标"
    status: str             # active | paused | blocked | done
    epoch: int              # 控制面变更（set/pause/resume/clear/subgoal 增删）时 +1；
                            # 评估记账（turns_used/last_verdict）不递增，否则 CAS 永远失配
    turns_used: int
    max_turns: int
    created_at: float
    last_turn_at: float
    last_verdict: str | None        # done | blocked | continue
    last_reason: str | None
    paused_reason: str | None
    consecutive_parse_failures: int
    consecutive_transport_failures: int   # 新增
    subgoals: list[str]
```

### 并发协议（消除竞态的关键）

1. 所有状态读取-修改-写入在 per-UMO `asyncio.Lock` 内完成。
2. 回合完成时的评估流程：
   - 加锁：读状态，`turns_used += 1`，记录 `epoch=N`，立即保存；解锁（轮次已真实消耗，诚实记账）。
   - **锁外**等待 judge/工具信号。
   - 加锁：重读状态；若 `epoch != N` 或 `status != active` 或记录已不存在 → **丢弃裁决**，结束。
   - 否则应用裁决、保存、解锁。
3. 因此 `clear/pause/set` 与在途 judge 的任意交错都不会复活旧状态。

## 5. 一等内部回合（替代伪造用户消息）

### 事件契约

- extras：`internal_turn="goal"`、`goal_id=<uuid>`。
- 新事件使用独立构造：新 `message_id`、新 `created_at`、新 `TraceSpan`、空临时文件列表；不再 `copy.copy` 旧事件继承其观测字段。

### 管道行为（唯一需要的核心改动）

- 当 `internal_turn` 存在时，waking/filter 阶段**不激活任何命令、正则、自定义过滤器 handler**（`activated_handlers` 为空），命令误触发从结构上消失。
- 事件仍正常流经 history、persona、provider 选择、工具装配、流式、持久化、内容安全与结果装饰——这些是必须保留的既有能力。
- `is_wake=True`、`is_at_or_wake_command=True` 直接设置，保证进入 Agent 阶段。

### 守卫与取消

- `on_llm_request` 守卫：校验 `goal_id` 与当前状态匹配且 `status == active`，否则 `stop_event()`；同时执行阻塞工具剥离（沿用 `block_tools_during_goal` 与内置黑名单的并集语义）。
- `pause/clear` 在写状态后调用 `active_event_registry.request_agent_stop_all(umo)`，**当前回合立即停止**。
- 用户手动中断（`agent_stop_requested`）沿用现状：自动 pause 并通知。

### webchat 渲染

沿用现有 `register_synthetic_chat_run`：内部回合仍是一等 chat run，渲染与恢复路径不变。非 webchat 平台维持原生回复路径。

## 6. 完成信号：控制工具优先，judge 兜底

### 主通道：goal 控制工具

- goal 回合向 Agent 注入两个工具（仅该回合的 ToolSet，不污染全局注册表）：
  - `goal_done(reason: str)`：宣告目标完成，reason 需包含完成证据。
  - `goal_blocked(reason: str)`：宣告被阻塞/需要用户输入。
- 工具 handler 将裁决写入 `event` extras（`goal_verdict`），返回简短确认；`on_agent_done` 读到该 extras 时直接驱动状态机，**跳过 judge**。
- continuation prompt 要求 Agent 每轮结束时：未完成则说明下一步；完成/阻塞必须调用对应工具并给出证据。
- 风险与取舍：Agent 可能过早宣告 done。缓解——prompt 要求逐条证据、reason 对用户可见、UI 展示 last_reason；judge 仍是缺信号时的兜底。

### 兜底通道：judge v2 协议

仅当回合结束且未收到控制工具信号时调用 judge。

```json
{"status": "done|blocked|continue", "reason": "<one sentence>"}
```

- 严格校验：`status` 必须是三值之一，`reason` 必须为非空字符串；任何缺失/类型错误 → `parse_failed=True`（修复"缺 done 字段不算失败"的漏洞）。
- 复用现有 hardened 解析（`_strip_markup` / `_extract_json_object` / reason 消毒防泄漏）。
- 响应截取改为尾部优先：保留开头 1000 字符 + 末尾 3000 字符，中间以标记连接（完成证据通常在长回复末尾）。
- 调用参数内部固定：`max_tokens=256`、`temperature=0`；provider 支持时启用 JSON mode。
- transport 失败单独计数：连续 2 次 → `paused(error)` 并通知，不再 fail-open 烧预算。
- 解析失败沿用 `max_parse_failures` 阈值暂停。
- provider 选择逻辑不变：`judge_provider_id` 留空则跟随当前会话 provider。
- 不支持 function calling 的 provider：无法注入控制工具，judge 承担每轮评估（自动降级，无需配置）。

## 7. 重启恢复与迁移

- `initialize()` 顺序：旧插件状态迁移（沿用现有逻辑）→ kernel sweep：`goal:index` 中所有 `active` → `paused`，`paused_reason="interrupted by restart"`。
- 旧状态迁移追加 backfill：`goal_id=uuid4`、`epoch=1`、`consecutive_transport_failures=0`；旧 `cleared` 值直接删除记录。
- done 记录生命周期：`/goal set` 新目标时覆盖；sweep 时清理超过 30 天的 done 记录，`goal:index` 不再无限增长。

## 8. Dashboard 数据流与 UI

### 推送替代轮询

- goal 状态每次变更，后端向该会话的 webchat system stream 推送：
  `{"type": "goal_state_changed", "data": {"goal": SessionGoalState | null}}`。
- 前端 `useSessionGoal` 订阅该事件直接更新缓存；删除"立即 + 4 秒后"双刷 hack（`refreshGoalAfterRun` 移除）。

### Sidebar 操作

- GoalSidebar 增加 暂停 / 恢复 / 清除 按钮（按状态显隐），调用新 REST 端点：

```
POST /api/v1/chat/sessions/{session_id}/goal/actions
body: {"action": "pause" | "resume" | "clear"}
```

- 权限：会话所有权校验（与 GET 一致）；不复用 `admin_only`（该配置约束平台消息指令，dashboard 用户本就是会话属主）。
- 路由变更后执行 `pnpm generate:api` 重新生成前端客户端。

### i18n

补齐 `ja-JP` 缺失的全部 `goal.*` 文案；新增 blocked 状态与按钮文案（四种语言）。

## 9. 权限统一

- `admin_only` 在指令入口处统一生效，覆盖：`/goal set|status|pause|resume|clear` 与 `/subgoal add|list|remove|clear`。
- 实现方式：指令 handler 统一经过权限门禁（调用点 ≥3，提取单个 helper 合理）。

## 10. 配置面（不变）

| 键 | 说明 | 变化 |
|---|---|---|
| `goal.admin_only` | 仅管理员可用 | 语义修复：覆盖全部指令 |
| `goal.max_turns` | 轮次预算 | 不变 |
| `goal.judge_provider_id` | judge 模型 | 不变（留空跟随会话） |
| `goal.judge_timeout` | judge 超时 | 不变 |
| `goal.max_parse_failures` | 解析失败容忍 | 不变 |
| `goal.verbose` | 推送每轮进度 | 不变 |
| `goal.block_tools_during_goal` | 循环中剥离的工具 | 不变 |

不新增任何配置键。

## 11. 涉及文件

| 文件 | 改动 |
|---|---|
| `astrbot/core/goal/goal_state.py` | schema v2（goal_id/epoch/blocked/transport 计数） |
| `astrbot/core/goal/goal_manager.py` | per-UMO 锁 + CAS 评估协议 + 状态机转移 |
| `astrbot/core/goal/goal_judge.py` | judge v2 三值协议、严格校验、尾部截取 |
| `astrbot/core/goal/goal_service.py` | 内部回合构造、控制工具注入、取消、sweep、推送回调 |
| `astrbot/core/pipeline/waking_check/stage.py` | `internal_turn` 跳过过滤器激活 |
| `astrbot/builtin_stars/goal/main.py` | 统一权限门禁；指令面不变 |
| `astrbot/dashboard/services/chat_service.py` | actions 服务、`goal_state_changed` 发布 |
| `astrbot/dashboard/api/chat.py` + `openspec/openapi-v1.yaml` | POST actions 路由 |
| `dashboard/src/composables/useSessionGoal.ts` | 订阅推送，移除轮询 |
| `dashboard/src/components/chat/message_list_comps/GoalSidebar.vue` | 操作按钮、blocked 状态展示 |
| `dashboard/src/i18n/locales/*/features/chat.json` | 补齐 ja-JP 与新增文案 |
| `tests/unit/test_goal_*.py` | 扩展（见 §12） |

## 12. 测试策略

1. **状态机转移表穷举**：每个状态 × 每个操作的合法/非法转移。
2. **竞态回归**（本会话已复现的用例转正）：
   - slow-judge 期间 `clear` → 裁决被丢弃，状态不复活；
   - slow-judge 期间 `pause` → 裁决被丢弃，保持 paused；
   - slow-judge 期间 `set` 新目标 → 旧裁决不污染新目标。
3. **管道测试**：`internal_turn` 事件不激活任何命令/正则 handler，且正常进入 Agent 阶段。
4. **控制工具集成**：`goal_done/goal_blocked` 信号优先于 judge；工具缺席时 judge 兜底。
5. **judge v2**：严格 schema（缺 status/reason 即 parse failure）、尾部截取断言、transport 连续失败暂停。
6. **重启 sweep**：active → paused 的幂等性。
7. **前端**：`goal_state_changed` 推送更新缓存；按钮调用正确端点。

## 13. 实施顺序

1. **P0 修复层**：状态机 v2 + epoch/CAS + 权限统一 + 重启 sweep。
2. **内部回合层**：管道 `internal_turn` 分支 + 事件构造 + 立即取消。
3. **信号层**：控制工具 + judge v2 协议。
4. **体验层**：推送、Sidebar 按钮、i18n 补齐。

每层独立可交付、独立可回滚。
