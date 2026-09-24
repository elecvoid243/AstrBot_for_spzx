# Agent Teams 事件驱动调度设计

- 日期：2026-09-15
- 状态：设计中
- 来源：[2026-09-14 评审建议 §2.1](../../2026-09-14-agent-teams-improvement-recommendations.md)、[2026-09-15 改进总纲 §2.1](../../2026-09-15-agent-teams-improvement-design.md)（批次 1 / PR-1）
- 基线：`specs/2026-09-05-agent-teams-design.md` §6.3（已落地）

## 1. 背景与问题

`DAGRunner._run_loop`（`agent_team_run_service.py:765-795`）是波次屏障调度：每轮计算就绪集 → 截取前 `max_parallel` 个 → `asyncio.gather` 等待**整批完成** → 应用失败策略 → 下一轮。两个结构性问题：

1. **慢节点拖住同波快节点的后继**。A(快)→C 与 B(慢) 同波时，A 完成后 C 仍需等 B 结束才能入波。`max_parallel=5` 且节点耗时不均时吞吐损失最大——而多成员协作恰恰耗时不均（有的成员跑长工具调用）。
2. **同成员串行靠 2s 忙等轮询**。同一成员绑定多个就绪节点时，`_wait_if_busy`（`:405-408`，auto 模式 `:1085-1088`）以 `busy_poll_interval=2s` 轮询 `ports.is_busy`，既引入平均 1s 的额外派发延迟，又在等待期持续发射 `busy` 事件。

## 2. 目标与非目标

### 2.1 目标

- G1 就绪即调度：节点前驱全部到达 `done/skipped` 后立即进入执行队列，不等待任何无关节点。
- G2 同成员互斥零轮询：团队成员回合按派发顺序 FIFO 串行，不再轮询等待。
- G3 外部行为兼容：SSE 事件形状、逐转换落盘时序、`TeamPorts` 签名、DAG 校验（`agent_team_dag.py`）、REST 控制语义（pause/resume/stop/retry/skip）均不变；`pause` 语义有意收紧为"新节点不再放行 + 在途排空"（§4.7）。
- G4 恢复等价：从 `node_states` 重建的行为与现状一致（已完成节点不重执行、interrupted 节点等 retry）。

### 2.2 非目标

- 不改 `TeamPorts`（deliver/collect/is_busy 签名与生产实现）。
- 不改 `AutoOrchestrator` 的协调者轮次结构（仅成员波内部串行机制替换，§4.6）。
- 不引入节点重试、超时策略等新功能（分别属批次 1 其余 spec 与后续批次）。
- 不做状态机抽象/第三方任务图库（stdlib 足够，KISS）。

## 3. 总体设计：数据流协程图

波次屏障替换为**每节点一个 flow 协程 + 单一 `asyncio.Condition` 状态同步**：

- 所有非终态节点在 `run()` 入口一次性拉起 flow 协程（≤20 个，恢复路径即"重建任务图"）；
- flow 通过 `Condition.wait_for` 等待"前驱全部 done/skipped"这一谓词（无粘性事件、无丢唤醒、无轮询）；
- 同成员串行用 per-member `asyncio.Lock`（外层），全局并发用 `asyncio.Semaphore(max_parallel)`（内层）；
- 节点完成时**内联**应用失败策略，不再按波批次。

### 3.1 新增状态

```python
self._state_cond = asyncio.Condition()      # guards node_states transitions
self._member_locks: dict[str, asyncio.Lock] = {}   # session_id -> lock
self._slots = asyncio.Semaphore(int(config["max_parallel"]))
self._flows: dict[str, asyncio.Task] = {}   # node_id -> live flow task
# _resume_wake 移除（park 迁移到 Condition 上）；_stop_requested 保留
```

`busy_poll_interval` 保留（仍用于外 来源忙等待，§4.4）。

## 4. 详细设计

### 4.1 flow 协程

```python
async def _node_flow(self, node: dict) -> None:
    node_id = node["id"]
    try:
        # 1. Wait until every predecessor reached done/skipped (or stop).
        async with self._state_cond:
            await self._state_cond.wait_for(
                lambda: self._preds_satisfied(node_id)
                or self._stop_requested.is_set()
            )
        if self._stop_requested.is_set():
            return
        # 2. Cascade-skip / retry may have flipped the status while waiting.
        if self.node_states[node_id]["status"] != "pending":
            return
        member = self._member_by_id(node["member_id"])
        if member is None:
            ...  # 现状逻辑：failed + persist + emit（原 _execute_node 头部迁移至此）
            return
        lock = self._member_locks.setdefault(member["session_id"], asyncio.Lock())
        async with lock:                      # 外层：同成员 FIFO
            await self._wait_if_busy(member["session_id"])   # 仅外 来源（用户直接占用会话）
            async with self._slots:           # 内层：全局并发闸
                if self._stop_requested.is_set():
                    return                    # 节点保持 pending
                if self.status == "paused":   # §4.7：pause 闸门
                    async with self._state_cond:
                        await self._state_cond.wait_for(
                            lambda: self.status != "paused"
                            or self._stop_requested.is_set()
                        )
                if self._stop_requested.is_set():
                    return
                if self.node_states[node_id]["status"] != "pending":
                    return                    # 等待期间被级联 skip
                await self._execute_node(node)
        await self._apply_failure_policy_node(node_id)
    except Exception:                         # noqa: BLE001
        # flow 崩溃等价于节点失败，绝不外溢（原 run() 的 crash 兜底下沉）
        logger.exception(...)
        await self._fail_node_safely(node_id, "runner error")
    finally:
        self._flows.pop(node_id, None)
        self._notify_state()                  # cond.notify_all()
```

要点：

- **锁序固定为 member lock → semaphore**。信号量持有者只可能是正在 `_execute_node`（收集受超时约束、有限必达）的 flow，它不再等待任何锁 → 无死锁（§4.8）。反序（先信号量后锁）会出现"A 持信号量等锁、B 持锁等信号量"的死锁，实施时禁止改动锁序。
- **外 来源忙等待保留**：成员会话可能被用户直接占用（非团队回合在途），`ports.is_busy` 的 2s 轮询仅服务这一场景；团队内部的排队改由 lock 承担，常见路径不再轮询。
- `busy` 事件保留：flow 在 `lock` 等待处与 `_wait_if_busy` 轮询处发射（形状不变），dashboard reducer 的 `busySessionIds` 指示不受影响（回复到达时的清除逻辑沿用）。

### 4.2 状态同步谓词与通知

```python
def _preds_satisfied(self, node_id: str) -> bool:
    return all(
        self.node_states[p]["status"] in ("done", "skipped")
        for p in self._preds.get(node_id, [])
    )

def _notify_state(self) -> None:
    # 由状态变更方在锁外调用；内部获取 cond 并 notify_all
    ...
```

通知点：`_execute_node` 终态落盘后、`retry_node`/`skip_node`（含级联）、失败策略变更 run 状态后、`resume()`。flow 数 ≤20，`notify_all` 成本可忽略。

### 4.3 调度主循环（替代 `_run_loop`）

```python
async def _run_loop(self) -> None:
    self._launch_pending_flows()      # 全部 pending 节点拉起 flow
    while True:
        if self._stop_requested.is_set():
            self.status = "stopped"
            await self._persist()
            return
        if all(s["status"] in ("done", "skipped") for s in self.node_states.values()):
            self.status = "completed"
            ...  # 现状 result_summary 逻辑（:797-808）不变
            return
        if self.status == "paused":
            await self._persist()
            self._emit({"type": "paused", "reason": "user pause"})
            async with self._state_cond:
                await self._state_cond.wait_for(
                    lambda: self.status != "paused" or self._stop_requested.is_set()
                )
            continue
        async with self._state_cond:
            await self._state_cond.wait_for(lambda: True, )  # 等任意状态变更唤醒
```

主循环仅负责：终态判定、stop、pause 停泊；调度本身由 flow 的完成通知驱动。原 `:810-814` 的"僵尸 running"防御随裸 `resume()` 路径消失而移除（resume 只做状态翻转 + notify，不再绕过 flow 重建）。

`_launch_pending_flows()`：为每个 `status == "pending"` 且无存活 flow 的节点创建 `_node_flow` 任务；在 `run()` 入口与 `retry_node` 后调用（幂等）。

### 4.4 `_execute_node` 的迁移

`_execute_node` 主体不变，仅两处调整：

1. 头部的"成员缺失 / config 档案守卫"上移至 flow（在获取锁之前快速失败，不占并发槽）；
2. `:509-517` 的 pause 竞态回退（running→pending）删除——pause 竞态已在 flow 的闸门处处理，进入 `_execute_node` 时 run 必为 running。

### 4.5 失败策略内联

```python
async def _apply_failure_policy_node(self, node_id: str) -> None:
    """Per-node replacement for the wave-level _apply_failure_policy."""
```

- 该节点 `status == "failed"` 时：
  - `auto_skip`：节点标记 skipped + 级联 skip pending 后继（沿用 `downstream_of`），persist + emit progress；
  - `pause`（默认）：run 转 paused + persist + emit `paused(reason="node failed", node_id)`。
- 与现状的差异：auto_skip 下，失败节点的下游在失败**当时**即被剪掉（原实现等整波结束）。这是行为改进，见 §4.7 差异清单。
- 波次版 `_apply_failure_policy`（`:817-839`）删除。

### 4.6 AutoOrchestrator._member_wave 的串行替换

现状：每 assignment 一个 `run_one` 协程 + 信号量 + `_wait_if_busy` 轮询。同一成员被派多个任务时靠轮询串行。

替换：**按成员分组、每组一个协程顺序处理**（派发顺序即执行顺序）：

```python
queues: dict[str, list[dict]] = {}   # session_id -> assignments (dispatch order)
for assignment in assignments: ...
roster = [by_name[a["member"]] for a in assignments]   # 去重，stop 快照语义不变
await asyncio.gather(*(self._run_member_queue(sid, queue) for ...))
```

- `_run_member_queue`：循环取任务 → deliver/collect（现有的 turn_id、transcript、事件发射逻辑原样保留）；
- 全局 `Semaphore(max_parallel)` 保留在任务执行点；
- `_in_flight_members` 在波开始前置满 roster（现状 `:1231-1239` 的 stop 快照语义不变）；
- `_wait_if_busy` 在 auto 模式的调用点移除（外 来源场景在 auto 波内极罕见，不保留轮询——若成员被用户占用，deliver 会排队在 webchat 队列管理器层，行为可接受）。
- 协调者回合（`_coordinator_turn`）不改动。

### 4.7 与现状的行为差异清单（changelog 用）

| # | 差异 | 性质 |
|---|---|---|
| 1 | `pause` 从"波间暂停"变为"未起跑节点在闸门停泊 + 在途排空"；恢复后停泊节点继续 | 有意收紧，UI 无感 |
| 2 | auto_skip 级联从"每波末"提前到"节点失败时" | 行为改进 |
| 3 | `busy` 事件新增发射点（成员 lock 排队处） | 事件形状不变，reducer 兼容 |
| 4 | 同波节点的完成事件不再按批次对齐，`dag_progress` 更新更细粒度 | reducer 按节点折叠，兼容 |
| 5 | `reply_timeout` 到期时的失败文案、错误字段、transcript 行为完全不变 | 不变 |

### 4.8 无死锁与活跃性

- **锁序**：condition → member lock → semaphore，单向；`_execute_node` 内部不再获取这三者。信号量持有者必然在有限时间内释放（collect 受 reply_timeout 约束、deliver 失败即返回）。
- **活跃性**：每个 pending 节点恒有一个存活 flow（run() 入口拉起 + retry 后重拉）；flow 退出仅发生在 stop 或自身终态。主循环由通知驱动，无轮询、无丢唤醒（`Condition.wait_for` 在锁内复查谓词）。

## 5. 数据模型 / API / SSE

- 无表结构变更；`node_states` 键集不变。
- REST 语义不变；`pause` 的时序语义变化见 §4.7。
- SSE 事件类型与形状不变（§4.7-3/4 的发射点差异对 reducer 透明）。

## 6. 测试策略

现有 scripted ports 用例（`tests/agent_teams/`）全部保留并必须通过。新增用例：

1. **重叠调度**：A(慢)→C、B(快) 同图，断言 C 在 A 完成前启动（总墙钟 < 串行下界）。
2. **同成员 FIFO**：同成员两节点，记录 deliver 时间序，断言严格串行且无 `is_busy` 轮询（以假 ports 计数 `busy_poll` 调用为 0——外 来源场景单独用例）。
3. **并发上限**：5 个独立节点 + `max_parallel=2`，断言同时在途数峰值 ≤2。
4. **pause 排空**：在途节点完成、未起跑节点停泊；resume 后继续；期间无新 deliver。
5. **stop**：停泊中/排队中 flow 直接退出且节点保持 pending；在途节点走 interrupt 路径（现状回归）。
6. **retry/skip**：retry 后仅该节点 flow 重拉；skip 级联唤醒下游 flow 且下游按 skipped 前驱放行。
7. **失败策略内联**：auto_skip 下失败节点下游即时剪枝（其余节点不受阻）；pause 下 run 即时 paused。
8. **恢复重建**：done/paused 混合状态的 run 重新 `run()` 后不重执行已完成节点。
9. **崩溃隔离**：单个 flow 异常只 fail 该节点，run 按失败策略收敛。

测试时间源：涉及 deadline 的用例沿用既有极小超时值方式；本 spec 不改时间源（空闲超时 spec 另行注入 clock）。

## 7. 实施切分与回滚

- 单 PR 交付（runner 内部自洽，无法安全拆分更细）；改动范围：`agent_team_run_service.py`（DAGRunner 为主、`_member_wave` 次之）+ 测试。
- 回滚：revert 单 commit 即恢复波次调度；无数据迁移，`node_states`/run 行双向兼容。
- 落地后观察项：长任务团队的墙钟对比（建议用同图同输入跑改造前后对比一次）。

## 8. 已否决的备选方案

- **保留波次模型、仅缩小波粒度**（如按拓扑层多波推进）：仍存在层内屏障，G1 不成立。
- **每节点粘性 `asyncio.Event` + 下游循环复查**：事件 set 后节点被 retry 重置回 pending 时无法反 set，下游需轮询或复杂失效协议；`Condition.wait_for` 谓词模型天然规避。
- **仅就绪即拉起 + 完成钩子重拉（增量启动）**：与全量拉起等价但状态机分支更多（retry/skip/cascade 各需触发重拉）；全量拉起 + 谓词等待更简单。
- **第三方任务图/调度库**：stdlib 足够，避免新依赖（KISS）。
