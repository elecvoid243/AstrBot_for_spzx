# Goal Mechanism Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 goal 常驻循环重构为"会话级状态机 + 一等内部回合"，修复状态竞态与权限缺口，完成信号改为控制工具优先、judge 兜底，UI 改推送。

**Architecture:** GoalState v2 引入 `goal_id`/`epoch`，GoalManager 用 per-UMO 锁 + CAS 评估协议消除竞态；续轮不再伪造用户消息，改由管道识别 `internal_turn` extra 跳过过滤器激活；goal 回合注入 `goal_done`/`goal_blocked` 控制工具，judge 仅在无工具信号时兜底；状态变更通过 webchat system stream 推送 `goal_state_changed`。

**Tech Stack:** Python 3.10+（asyncio、pydantic dataclass）、FastAPI、Vue 3 + Vuetify、pytest。

**Spec:** `docs/superpowers/specs/2026-10-04-goal-mechanism-redesign-design.md`

## Global Constraints

- 工作目录：`G:/github/AstrBot_for_spzx/.worktrees/feat-goal-redesign`（分支 `feat/goal-redesign`），所有改动在此进行。
- 测试命令（从 worktree 根运行，import 解析已验证指向 worktree）：
  `G:/github/AstrBot_for_spzx/venv/python.exe -m pytest tests/unit/test_goal_state.py -v`
- 每个任务提交前运行 `G:/github/AstrBot_for_spzx/venv/python.exe -m ruff format <改动文件> && G:/github/AstrBot_for_spzx/venv/python.exe -m ruff check <改动文件>`。
- 代码注释与日志用英文；Google 风格 docstring；conventional commit 消息用英文。
- 不新增任何用户配置键；`goal.*` 现有 7 个配置键语义不变（除 `admin_only` 修复为全覆盖）。
- `pathlib.Path` 处理路径；不要在计划外加 helper（遵守 AGENTS.md 的 Inline-First 规则）。

## Review Focus

1. 目标文本以 `/` 开头时，goal 内部回合不得激活任何命令/正则 handler → Task 5 测试钉死。
2. slow judge 期间 `clear`/`pause`/`set` 三种交错，裁决必须丢弃、状态不复活 → Task 2 测试钉死。
3. provider 不支持 function calling（`req.func_tool is None`）时，不注入控制工具、每轮走 judge 兜底且不报错 → Task 7 测试钉死。
4. judge 返回缺 `status`/`reason` 字段或类型错误的合法 JSON → 必须 `parse_failed=True` → Task 8 测试钉死。
5. dashboard resume 没有源事件，kickoff 经 webchat 会话队列注入且 `internal_turn`/`goal_id` 被 adapter 提升为 extras → Task 10 测试钉死。

---

### Task 1: GoalState schema v2

**Files:**
- Modify: `astrbot/core/goal/goal_state.py`
- Test: `tests/unit/test_goal_state.py`

**Interfaces:**
- Produces: `GoalState` 新字段 `goal_id: str = ""`、`epoch: int = 1`、`consecutive_transport_failures: int = 0`；status 取值域 `active | paused | blocked | done`；`VALID_STATUS` 移至本模块并被 `from_dict` 使用；新方法 `to_public_dict() -> dict`（键：`goal, goal_id, status, turns_used, max_turns, subgoals, last_verdict, last_reason, paused_reason, created_at`，`created_at` 为 UTC ISO 字符串或 None）。

- [ ] **Step 1: Write the failing tests**（追加到 `tests/unit/test_goal_state.py`）

```python
def test_new_fields_default():
    s = GoalState(goal="g", goal_id="id1")
    assert s.epoch == 1 and s.consecutive_transport_failures == 0

def test_from_dict_backfills_goal_id_and_epoch():
    s = GoalState.from_dict({"goal": "g", "status": "active"})
    assert s.goal_id and len(s.goal_id) == 32 and s.epoch == 1

def test_from_dict_invalid_status_becomes_paused():
    s = GoalState.from_dict({"goal": "g", "status": "weird"})
    assert s.status == "paused"
    assert s.paused_reason == "recovered from invalid status"

def test_to_public_dict_shape():
    s = GoalState(goal="g", goal_id="id1", created_at=1_700_000_000.0)
    d = s.to_public_dict()
    assert d["goal_id"] == "id1" and "T" in d["created_at"]
    assert set(d) == {"goal", "goal_id", "status", "turns_used", "max_turns",
                      "subgoals", "last_verdict", "last_reason", "paused_reason",
                      "created_at"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `G:/github/AstrBot_for_spzx/venv/python.exe -m pytest tests/unit/test_goal_state.py -v`
Expected: FAIL（新字段/方法不存在）

- [ ] **Step 3: Implement GoalState v2 in `astrbot/core/goal/goal_state.py`**

新增字段并更新 `to_dict`/`from_dict`；`from_dict` 中：`goal_id` 缺失用 `uuid.uuid4().hex` 回填，`epoch` 缺失默认 1，`status not in VALID_STATUS` 时降级 `paused` 并写 `paused_reason="recovered from invalid status"`；新增 `to_public_dict()`（`datetime.fromtimestamp(..., tz=timezone.utc).isoformat()`）。`VALID_STATUS = {"active", "paused", "blocked", "done"}` 定义在本模块（`goal_manager.py` 中原有的同名死常量由 Task 2 移除）。

- [ ] **Step 4: Run tests to verify they pass**（含既有 4 个测试，注意既有 `test_round_trip`/`test_default_fields` 需同步补新字段构造参数）

Run: `G:/github/AstrBot_for_spzx/venv/python.exe -m pytest tests/unit/test_goal_state.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/goal/goal_state.py tests/unit/test_goal_state.py
git commit -m "feat(goal): add goal_id, epoch and blocked status to GoalState"
```

---

### Task 2: GoalManager 锁 + CAS 评估协议 + 状态机转移

**Files:**
- Modify: `astrbot/core/goal/goal_manager.py`
- Test: `tests/unit/test_goal_manager.py`

**Interfaces:**
- Consumes: Task 1 的 `GoalState` v2。
- Produces:
  - `JudgeFn = Callable[[str, str, list[str]], Awaitable[tuple[str, str, bool, bool]]]`——四元组 `(verdict, reason, parse_failed, transport_failed)`，verdict ∈ `done | blocked | continue | skipped`。
  - `GoalManager._lock_for(umo: str) -> asyncio.Lock`（内部）。
  - `resume(umo) -> GoalState | None`：仅 `paused | blocked` 可恢复，否则返回 None。
  - `evaluate_after_turn` 的 decision dict 新增可能值 `status="blocked"`；裁决被 epoch 丢弃时返回 `{"status": "stale", "should_continue": False, "continuation_prompt": None, "verdict": "stale", "reason": "state changed during evaluation", "message": ""}`。
  - 控制面变更（`set/pause/resume/add_subgoal/remove_subgoal/clear_subgoals`）在锁内 `epoch += 1`；`clear` 删除记录不递增。

- [ ] **Step 1: Write the failing tests**（追加到 `tests/unit/test_goal_manager.py`；既有 judge stub 升级为四元组）

竞态回归（本会话已复现的用例转正）：

```python
async def test_clear_during_slow_judge_discards_verdict(kv):
    mgr = GoalManager(kv)
    await mgr.set("umo1", "g")
    release = asyncio.Event()

    async def slow_judge(goal, response, subgoals):
        await release.wait()
        return "continue", "late", False, False

    task = asyncio.create_task(mgr.evaluate_after_turn("umo1", "resp", slow_judge))
    await asyncio.sleep(0)
    await mgr.clear("umo1")
    release.set()
    decision = await task
    assert decision["status"] == "stale" and decision["should_continue"] is False
    assert await mgr.get("umo1") is None  # 状态不复活
```

同样模式补两个：`test_pause_during_slow_judge_keeps_paused`（judge 返回后状态仍 paused）、`test_set_new_goal_during_slow_judge_discards_old_verdict`（新目标 `turns_used == 0` 且 `goal_id` 不同）。

状态机：

```python
async def test_resume_only_from_paused_or_blocked(kv):
    mgr = GoalManager(kv)
    await mgr.set("umo1", "g")
    assert await mgr.resume("umo1") is None  # active 上 no-op
    await mgr.pause("umo1")
    assert (await mgr.resume("umo1")).status == "active"

async def test_blocked_verdict_sets_blocked_status(kv):
    mgr = GoalManager(kv)
    await mgr.set("umo1", "g")
    async def judge_blocked(g, r, s): return "blocked", "need user input", False, False
    d = await mgr.evaluate_after_turn("umo1", "resp", judge_blocked)
    assert d["status"] == "blocked" and d["should_continue"] is False
    assert (await mgr.get("umo1")).status == "blocked"

async def test_transport_failures_pause_after_two(kv):
    mgr = GoalManager(kv)
    await mgr.set("umo1", "g")
    async def judge_transport(g, r, s): return "continue", "judge unavailable", False, True
    d1 = await mgr.evaluate_after_turn("umo1", "resp", judge_transport)
    assert d1["should_continue"] is True   # 第 1 次：放行
    d2 = await mgr.evaluate_after_turn("umo1", "resp", judge_transport)
    assert d2["status"] == "paused" and d2["should_continue"] is False
    state = await mgr.get("umo1")
    assert state.consecutive_transport_failures == 2
```

另补 `test_control_mutation_bumps_epoch`（pause 后 epoch 增加、turns_used 记账不增加 epoch）。

- [ ] **Step 2: Run tests to verify they fail**

Run: `G:/github/AstrBot_for_spzx/venv/python.exe -m pytest tests/unit/test_goal_manager.py -v`
Expected: FAIL

- [ ] **Step 3: Implement in `astrbot/core/goal/goal_manager.py`**

- `__init__` 增加 `self._locks: dict[str, asyncio.Lock] = {}`；`_lock_for` 惰性创建。
- 所有控制面方法：`async with self._lock_for(umo)` 包裹读-改-写，写前 `state.epoch += 1`（`set` 新建 `epoch=1` + `goal_id=uuid4().hex`）。
- `resume` 增加状态白名单 `{paused, blocked}`。
- `evaluate_after_turn` 改为三段式（spec §4）：
  1. 锁内：校验 active、`turns_used += 1`、`last_turn_at`、保存、记 `epoch_snapshot`；
  2. 锁外：`await judge(...)`；
  3. 锁内：重读，`state is None or state.epoch != epoch_snapshot or state.status != "active"` → 返回 stale decision；否则应用裁决（done/blocked/continue/parse 阈值/transport 阈值≥2 暂停/预算），保存。
- transport 与 parse 计数互斥重置：transport_failed 时 parse 计数清零，反之亦然。
- 删除未使用的 `mark_done` 与旧 `VALID_STATUS` 常量（已移至 goal_state）。

- [ ] **Step 4: Run tests to verify they pass**（含既有测试全部迁移为四元组 stub）

Run: `G:/github/AstrBot_for_spzx/venv/python.exe -m pytest tests/unit/test_goal_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/goal/goal_manager.py tests/unit/test_goal_manager.py
git commit -m "fix(goal): serialize state mutations with per-UMO locks and CAS evaluation"
```

---

### Task 3: judge 四元组契约落地（goal_judge + service 接线）

**Files:**
- Modify: `astrbot/core/goal/goal_judge.py`（仅 `judge_goal` 返回值）、`astrbot/core/goal/goal_service.py`（lambda 接线）
- Test: `tests/unit/test_goal_judge.py`

**Interfaces:**
- Consumes: Task 2 的 `JudgeFn` 四元组。
- Produces: `judge_goal(...) -> tuple[str, str, bool, bool]`；transport 错误（`llm_caller` 返回 None）→ `("continue", "judge unavailable (transport error)", False, True)`；空 goal → `("skipped", "empty goal", False, False)`。其余解析逻辑本任务不变（v2 协议在 Task 8）。

- [ ] **Step 1: Update tests** —— `tests/unit/test_goal_judge.py` 中 `test_judge_goal_*` 断言改四元组；`test_judge_goal_transport_error_fails_open` 断言末位为 `True`。

- [ ] **Step 2: Run to verify fail** —— `... -m pytest tests/unit/test_goal_judge.py -v`，Expected: FAIL。

- [ ] **Step 3: Implement** —— `judge_goal` 返回四元组；`goal_service.on_turn_done` 的 judge lambda 直通四元组（不再包装）。

- [ ] **Step 4: Run to verify pass**（`test_goal_judge.py` + `test_goal_manager.py` 一起跑）。

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/goal/goal_judge.py astrbot/core/goal/goal_service.py tests/unit/test_goal_judge.py
git commit -m "feat(goal): return transport failure flag from judge contract"
```

---

### Task 4: 统一权限门禁 + 重启 sweep

**Files:**
- Modify: `astrbot/builtin_stars/goal/main.py`、`astrbot/core/goal/goal_service.py`
- Test: `tests/unit/test_goal_service.py`（新增 `tests/unit/test_goal_commands.py` 亦可，二选一，倾向后者保持文件职责）

**Interfaces:**
- Consumes: Task 2 的 manager。
- Produces:
  - `GoalService.sweep_stale_states() -> dict`：`{"paused": n, "cleaned": m}`；`goal:index` 中 `active → paused`（`paused_reason="interrupted by restart"`），`done` 且 `created_at` 早于 30 天 → 删除并 untrack；幂等。
  - `Main` 的 9 个指令 handler（goal set/status/pause/resume/clear；subgoal add/list/remove/clear）统一先过 `goal_service.check_permission(event)`。

- [ ] **Step 1: Write the failing tests**

权限矩阵（stub event：`role="member"`、`plain_result` 记录文本；`Main` 用 stub context 实例化）：

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("handler_name,args", [
    ("goal_set", ("target",)), ("goal_status", ()), ("goal_pause", ()),
    ("goal_resume", ()), ("goal_clear", ()),
    ("subgoal_add", ("x",)), ("subgoal_list", ()),
    ("subgoal_remove", (1,)), ("subgoal_clear", ()),
])
async def test_member_rejected_everywhere(handler_name, args):
    ...  # 每个 handler 只产出一条含 "仅管理员" 的拒绝消息，不触碰状态
```

sweep：

```python
async def test_sweep_pauses_active_and_cleans_old_done():
    ...  # active → paused(reason="interrupted by restart")；
    ...  # created_at=now-31d 的 done 被删除；created_at=now 的 done 保留；
    ...  # 第二次调用返回 {"paused": 0, "cleaned": 0}
```

- [ ] **Step 2: Run to verify fail** —— Expected: FAIL。

- [ ] **Step 3: Implement**

- `goal_service.py` 增加 `sweep_stale_states()`（遍历 `goals.list_tracked()`，锁内变更）。
- `main.py`：`initialize()` 在 `migrate_legacy_states()` 后调用 `sweep_stale_states()`；9 个 handler 补权限检查（抽取单个模块级小函数 `_refuse_if_not_admin(event)` 供全部 handler 使用，调用点 ≥3 满足提取条件）。

- [ ] **Step 4: Run to verify pass** —— `test_goal_commands.py` + `test_goal_service.py`。

- [ ] **Step 5: Commit**

```bash
git add astrbot/builtin_stars/goal/main.py astrbot/core/goal/goal_service.py tests/unit/test_goal_commands.py tests/unit/test_goal_service.py
git commit -m "fix(goal): enforce admin_only on all goal commands and sweep stale active states"
```

---

### Task 5: 管道 internal_turn 旁路

**Files:**
- Modify: `astrbot/core/pipeline/waking_check/stage.py`
- Test: 新增 `tests/unit/test_waking_internal_turn.py`

**Interfaces:**
- Produces: 事件 extras 约定 `internal_turn: str`（值 `"goal"`）；携带该 extra 的事件在 `WakingCheckStage.process` 中：设置 `is_wake=True`、`is_at_or_wake_command=True`、`activated_handlers=[]`，随后跳过全部唤醒前缀与 handler 匹配逻辑；bot-self 忽略与管理员角色设置仍在旁路之前生效。

- [ ] **Step 1: Write the failing test**

参照 `tests/test_process_stage_images.py` 的 stage 测试脚手架（stub PipelineContext，config 含 `wake_prefix=["/"]`、`admins_id`、`platform_settings`、plugin_manager 返回一个匹配 `/goal` 的 stub handler）：

```python
async def test_internal_turn_skips_all_handler_activation():
    event = make_event(message_str="/goal clear", extras={"internal_turn": "goal"})
    await run_stage(WakingCheckStage, event)
    assert event.is_wake is True and event.is_at_or_wake_command is True
    assert event.get_extra("activated_handlers") == []
```

对照用例：无 `internal_turn` 的 `/goal clear` 仍正常激活 handler（防回归）。

- [ ] **Step 2: Run to verify fail** —— Expected: FAIL（命令 handler 被激活）。

- [ ] **Step 3: Implement** —— `WakingCheckStage.process` 中，在"设置 sender 身份"之后、唤醒前缀循环之前插入旁路分支；旁路内完成上述 extras 设置后直接走到阶段末尾（保持 `yield` 语义与阶段返回结构一致，先读完整 `process` 方法再落点）。

- [ ] **Step 4: Run to verify pass** —— 新测试 + `tests/test_process_stage_images.py`（防回归）。

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/pipeline/waking_check/stage.py tests/unit/test_waking_internal_turn.py
git commit -m "feat(pipeline): bypass filter activation for internal turns"
```

---

### Task 6: 内部回合构造 + goal_id 守卫 + 立即取消

**Files:**
- Modify: `astrbot/core/goal/goal_service.py`、`astrbot/builtin_stars/goal/main.py`
- Test: `tests/unit/test_goal_service.py`

**Interfaces:**
- Consumes: Task 5 的 `internal_turn` 旁路；`astrbot.core.utils.active_event_registry.active_event_registry.request_agent_stop_all(umo, exclude=None) -> int`。
- Produces:
  - `build_goal_turn_event(event: AstrMessageEvent, text: str, goal_id: str) -> AstrMessageEvent`（替代 `build_continuation_event`；extras 恰为 `{"internal_turn": "goal", "goal_id": goal_id}`；新 `TraceSpan(name="GoalTurn", ...)`、`created_at=time.time()`、`_temporary_local_files=[]`）。
  - `GoalService.guard_goal_turn(event, req) -> None`（替代 `guard_continuation`）：非 internal goal turn 直接返回；`goal_id` 与当前状态不匹配或非 active → `stop_event()`。
  - `GoalService.pause_goal(umo) -> GoalState | None`、`clear_goal(umo) -> bool`：manager 操作后调用 `active_event_registry.request_agent_stop_all(umo)`。
  - `inject_continuation(event, text, goal_id)` 增加 `goal_id` 参数。
  - 常量 `GOAL_CONTINUATION_EXTRA` 删除，统一 `INTERNAL_TURN_EXTRA = "internal_turn"`、`GOAL_ID_EXTRA = "goal_id"`。

- [ ] **Step 1: Write the failing tests**（`tests/unit/test_goal_service.py` 改写/新增）

```python
def test_goal_turn_event_is_fresh():
    ...  # extras == {"internal_turn": "goal", "goal_id": "g1"}；
    ...  # new_event.trace is not src.trace；created_at 更新；_temporary_local_files == []

async def test_guard_drops_stale_goal_id():
    ...  # 当前状态 goal_id="g2"，事件携带 goal_id="g1" → event.stop_event() 被调用

async def test_pause_requests_agent_stop(monkeypatch):
    ...  # stub active_event_registry.request_agent_stop_all 记录调用；
    ...  # await service.pause_goal("umo1") 后被调用一次且参数为 "umo1"
```

既有测试中所有 `GOAL_CONTINUATION_EXTRA` 引用同步迁移到新契约。

- [ ] **Step 2: Run to verify fail** —— Expected: FAIL。

- [ ] **Step 3: Implement** —— 按上述签名改造；`main.py` 的 `goal_pause/goal_clear` 改调 service 包装方法；`_guard_continuation` hook 改名 `_guard_goal_turn` 并指向 `guard_goal_turn`；`on_turn_done` 注入时传 `state.goal_id`；`goal_set/goal_resume` 的 kickoff 同样走 `inject_continuation(..., state.goal_id)`。

- [ ] **Step 4: Run to verify pass** —— `test_goal_service.py` 全量。

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/goal/goal_service.py astrbot/builtin_stars/goal/main.py tests/unit/test_goal_service.py
git commit -m "feat(goal): build first-class internal turns with goal_id guard and instant cancel"
```

---

### Task 7: goal_done / goal_blocked 控制工具

**Files:**
- Modify: `astrbot/core/goal/goal_service.py`、`astrbot/core/goal/goal_judge.py`（continuation prompt）
- Test: `tests/unit/test_goal_service.py`

**Interfaces:**
- Consumes: `astrbot.core.agent.tool.FunctionTool`（pydantic dataclass，字段 `name/description/parameters/handler`；executor 以 `handler(event, **kwargs)` 调用）、`ToolSet.add_tool`；Task 6 的 `guard_goal_turn`。
- Produces:
  - `GOAL_VERDICT_EXTRA = "goal_verdict"`；工具 handler 写入 `{"status": "done"|"blocked", "reason": str}`。
  - `GoalService._inject_control_tools(event, req) -> bool`：`req.func_tool is None` → 返回 False（judge 兜底路径）；否则 `add_tool` 两个工具并返回 True。
  - `on_turn_done`：`verdict = event.get_extra("goal_verdict")` 存在时构造 `judge` 闭包直接返回 `(verdict["status"], verdict["reason"], False, False)`，不调用 judge LLM。

工具 parameters（两个工具同构）：

```python
{"type": "object",
 "properties": {"reason": {"type": "string", "description": "Evidence or blocker, one sentence"}},
 "required": ["reason"]}
```

- [ ] **Step 1: Write the failing tests**

```python
async def test_control_tools_injected_on_goal_turn():
    ...  # guard_goal_turn 后 req.func_tool.get_tool("goal_done")/("goal_blocked") 非 None

async def test_goal_done_tool_records_verdict():
    ...  # 直接调用 tool.handler(stub_event, reason="done it") →
    ...  # stub_event.get_extra("goal_verdict") == {"status": "done", "reason": "done it"}

async def test_on_turn_done_uses_tool_verdict_without_judge():
    ...  # event extra 带 goal_verdict；judge_llm_caller 若被调用则 raise → 不触发；
    ...  # 状态变 done

async def test_no_toolset_falls_back_to_judge():
    ...  # req.func_tool=None → _inject_control_tools 返回 False、不抛异常；
    ...  # on_turn_done 走 judge 路径（stub caller 断言被调用）
```

- [ ] **Step 2: Run to verify fail** —— Expected: FAIL。

- [ ] **Step 3: Implement**

- `guard_goal_turn` 在剥离阻塞工具后调用 `_inject_control_tools`。
- 工具 handler：校验 `reason` 非空（空则返回错误提示字符串，不写 extras），reason 截断 500 字符，写 extras，返回 `"Recorded."`。
- `goal_judge.py` 的 `CONTINUATION_PROMPT_TEMPLATE`/`..._WITH_SUBGOALS_TEMPLATE` 更新指令：完成时必须调用 `goal_done`（reason 含逐条证据）、被阻塞/需用户输入时调用 `goal_blocked`；不要在正文仅口头声称完成。
- `on_turn_done` 优先读 `GOAL_VERDICT_EXTRA`。

- [ ] **Step 4: Run to verify pass** —— `test_goal_service.py` + `test_goal_judge.py`（prompt 断言更新）。

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/goal/goal_service.py astrbot/core/goal/goal_judge.py tests/unit/test_goal_service.py tests/unit/test_goal_judge.py
git commit -m "feat(goal): add goal_done/goal_blocked control tools as primary completion signal"
```

---

### Task 8: judge v2 协议（严格三值 + 尾部截取 + 调用纪律）

**Files:**
- Modify: `astrbot/core/goal/goal_judge.py`、`astrbot/core/goal/goal_service.py`
- Test: `tests/unit/test_goal_judge.py`

**Interfaces:**
- Consumes: Task 3 的四元组契约。
- Produces:
  - `parse_judge_response(raw: str) -> tuple[str, str, bool]`：返回 `(status, reason, parse_failed)`，status ∈ `done | blocked | continue`；严格校验（见下）。
  - `JUDGE_MAX_TOKENS = 256`（替换未使用的 `DEFAULT_JUDGE_MAX_TOKENS`）；`_judge_llm_caller` 传 `max_tokens=JUDGE_MAX_TOKENS, temperature=0`。
  - 响应截取：`JUDGE_RESPONSE_SNIPPET_CHARS = 4000` 语义改为"头部 1000 + `\n… [middle truncated] …\n` + 尾部 3000"。

严格校验规则：JSON 必须是 dict；`status` 必须是三值字符串之一；`reason` 必须是非空字符串（`reason` 缺失/非字符串 → parse_failed；沿用 reason 消毒防泄漏）。

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_three_value_verdicts():
    assert parse_judge_response('{"status":"blocked","reason":"need input"}') == ("blocked", "need input", False)

@pytest.mark.parametrize("raw", [
    '{"reason": "no status"}',
    '{"status": "maybe", "reason": "x"}',
    '{"status": "done"}',
    '{"status": "done", "reason": 5}',
])
def test_parse_strict_schema_failures(raw):
    status, _, failed = parse_judge_response(raw)
    assert failed is True and status == "continue"

def test_response_truncation_keeps_tail():
    resp = "x" * 4000 + " FINAL_DELIVERABLE_READY"
    msgs = build_judge_messages("g", resp, None, "now")
    assert "FINAL_DELIVERABLE_READY" in msgs[1]["content"]
    assert "middle truncated" in msgs[1]["content"]
```

- [ ] **Step 2: Run to verify fail** —— Expected: FAIL。

- [ ] **Step 3: Implement**

- 重写 `JUDGE_SYSTEM_PROMPT` / 两个 user prompt 模板为三值协议（含 blocked 判定标准：unachievable / needs user input → blocked）。
- `parse_judge_response` 按上述严格规则重写（保留 `_strip_markup`/`_extract_json_object`/`_sanitize_reason`）。
- 新增尾部优先截取并在 `build_judge_messages` 中仅对 response 使用；goal/subgoals 仍头部截取。
- `judge_goal` 适配：`done/blocked/continue` 直通；`_judge_llm_caller` 加调用参数。
- 旧 `done` 布尔协议不保留（内置功能，无外部兼容负担）。

- [ ] **Step 4: Run to verify pass** —— `test_goal_judge.py` 全量 + `test_goal_manager.py` + `test_goal_service.py`。

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/goal/goal_judge.py astrbot/core/goal/goal_service.py tests/unit/test_goal_judge.py
git commit -m "feat(goal): adopt strict three-value judge protocol with tail-biased truncation"
```

---

### Task 9: goal_state_changed 推送

**Files:**
- Modify: `astrbot/core/goal/goal_manager.py`、`astrbot/core/goal/goal_service.py`、`astrbot/dashboard/api/app.py`、`dashboard/src/composables/useSessionGoal.ts`、`dashboard/src/components/chat/Chat.vue`、`dashboard/src/composables/useMessages.ts`
- Test: `tests/unit/test_goal_manager.py`、`tests/unit/test_goal_service.py`

**Interfaces:**
- Consumes: Task 1 的 `to_public_dict()`；`webchat_queue_mgr.put_system_event(session_id, payload)`。
- Produces:
  - `GoalManager.__init__` 新增可选参数 `on_change: Callable[[str, GoalState | None], Awaitable[None]] | None`；每次锁内保存/删除后调用（clear 传 None）。
  - `GoalService.set_state_change_listener(listener)`；构造 `GoalManager` 时接入。
  - 推送 payload：`{"type": "goal_state_changed", "data": {"goal": state.to_public_dict() | None}}`；仅 `umo.startswith("webchat:")` 时推送，session_id 取 `umo.rsplit("!", 1)[-1]`。
  - 前端 `useSessionGoal` 新增 `applyPushedGoal(sessionId: string, goal: SessionGoalState | null)`；删除 `refreshGoalAfterRun`；`SessionGoalState` 类型加 `goal_id: string`。
  - `useMessages.ts` system stream 分发点（现 `run_started` 分支附近）处理 `goal_state_changed`，调用 Chat.vue 注入的 handler；Chat.vue 将 `applyPushedGoal` 注入。

- [ ] **Step 1: Write the failing tests（后端）**

```python
async def test_manager_emits_on_change(kv):
    events = []
    async def listener(umo, state): events.append((umo, state))
    mgr = GoalManager(kv, on_change=listener)
    await mgr.set("umo1", "g")
    await mgr.clear("umo1")
    assert events[0][1].goal == "g" and events[1] == ("umo1", None)
```

service 层 stub listener 断言 pause/resume/clear 均触发。

- [ ] **Step 2: Run to verify fail** —— Expected: FAIL。

- [ ] **Step 3: Implement**

- 后端：`GoalManager` 接 `on_change`（锁内 save/delete 后 `await`）；`GoalService.__init__` 传入桥接 listener；`app.py` 在 registrar  wiring 旁 wiring 推送 listener。
- 前端：`useSessionGoal` 改推送模型；`useMessages.ts` 分发；`Chat.vue` 接线并删除 `refreshGoalAfterRun` 调用。

- [ ] **Step 4: Run to verify pass** —— 后端 pytest；前端 `cd dashboard && pnpm vue-tsc --noEmit`（或项目既有类型检查命令）通过。

- [ ] **Step 5: Commit**

```bash
git add astrbot/core/goal/ astrbot/dashboard/api/app.py dashboard/src/composables/ dashboard/src/components/chat/Chat.vue tests/
git commit -m "feat(goal): push goal state changes over the webchat system stream"
```

---

### Task 10: goal actions 端点 + Sidebar 操作 + i18n 补齐

**Files:**
- Modify: `openspec/openapi-v1.yaml`、`astrbot/dashboard/api/chat.py`、`astrbot/dashboard/services/chat_service.py`、`astrbot/core/platform/sources/webchat/webchat_adapter.py`、`dashboard/src/components/chat/message_list_comps/GoalSidebar.vue`、`dashboard/src/api/v1.ts`、`dashboard/src/i18n/locales/{zh-CN,en-US,ru-RU,ja-JP}/features/chat.json`
- Test: `tests/unit/test_goal_service.py` 或新增 `tests/unit/test_goal_actions.py`

**Interfaces:**
- Consumes: `register_synthetic_chat_run`（register-then-inject 顺序，参照 `agent_team_ports.py` 的 `deliver`）、`resolve_webchat_request_flags`、Task 6 的 service 包装方法。
- Produces:
  - `POST /api/v1/chat/sessions/{session_id}/goal/actions`，body `{"action": "pause" | "resume" | "clear"}`；会话属主鉴权（复用 `get_session_goal` 的检查模式）；响应 `{"goal": state.to_public_dict() | None}`。
  - `ChatService.apply_goal_action(username, session_id, action) -> dict`：pause/clear 调 service 包装；resume = `manager.resume` 成功后，先 `register_synthetic_chat_run` 再向 `webchat_queue_mgr` 会话队列 put kickoff payload（`message: [{"type":"plain","text": f"请继续完成目标：{state.goal}"}]`、`message_id`、`llm_checkpoint_id`、`internal_turn: "goal"`、`goal_id: state.goal_id`、`persist_user_history: True`、`flags: resolve_webchat_request_flags({})`、`selected_provider/model: None`）。
  - webchat adapter `create_event` 追加提升 `internal_turn`、`goal_id` 两个 payload 键（沿用 `execution_token` 的 isinstance 校验模式）。
  - GoalSidebar：按状态显示 暂停（active）/ 恢复（paused|blocked）/ 清除 按钮；`blocked` 状态 chip 用 `warning` 色并显示 `last_reason`；四种语言补齐 `goal.*`（ja-JP 新增整个 block）。

- [ ] **Step 1: Write the failing tests（后端）**

```python
async def test_apply_goal_action_requires_owner(): ...
async def test_pause_action_pauses_and_stops(): ...
async def test_resume_action_queues_internal_kickoff():
    ...  # resume 后 webchat 队列收到 payload：internal_turn == "goal"、
    ...  # goal_id 匹配、text 以 "请继续完成目标：" 开头；register 先于 put
async def test_adapter_lifts_internal_turn_keys():
    ...  # create_event 的 payload 含 internal_turn/goal_id → event extras 同名同值
```

- [ ] **Step 2: Run to verify fail** —— Expected: FAIL（路由/方法不存在）。

- [ ] **Step 3: Implement** —— 按上述接口；openapi-v1.yaml 加路径后 `cd dashboard && pnpm generate:api` 重新生成客户端；`dashboard/src/api/v1.ts` 加 `applyGoalAction(sessionId, action)`；GoalSidebar 按钮调它，成功后状态由 Task 9 的推送自动刷新（不本地手改缓存）。

- [ ] **Step 4: Run to verify pass** —— 后端 pytest；前端类型检查；手工冒烟：dashboard 设定目标 → Sidebar 暂停 → badge 即时变 paused。

- [ ] **Step 5: Commit**

```bash
git add openspec/openapi-v1.yaml astrbot/dashboard/ astrbot/core/platform/sources/webchat/ dashboard/src/ tests/
git commit -m "feat(dashboard): add goal actions endpoint and sidebar controls"
```

---

## 收尾

- [ ] 全量回归：`G:/github/AstrBot_for_spzx/venv/python.exe -m pytest tests/unit/test_goal_state.py tests/unit/test_goal_manager.py tests/unit/test_goal_judge.py tests/unit/test_goal_service.py tests/unit/test_goal_commands.py tests/unit/test_waking_internal_turn.py -v`
- [ ] `G:/github/AstrBot_for_spzx/venv/python.exe -m ruff format . && ... -m ruff check .`
- [ ] 按 finishing-a-development-branch 技能决定合并方式。
