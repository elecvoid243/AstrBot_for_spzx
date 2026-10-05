# ChatUI 后台 Shell 会话感知 — 设计文档

| 项目 | 内容 |
|------|------|
| 主题 | 让 ChatUI 用户对 Agent 托管的后台 shell 会话（`astrbot_execute_shell` 超时托管 / `astrbot_shell_session` 管理）具备持续感知能力 |
| 日期 | 2026-10-04 |
| 作者 | elecvoid243 |
| 状态 | Implemented — 已合入 `all`（merge `a79c6d991`） |
| 范围 | **Scope A：纯感知**（指示条 + 状态变化实时刷新 + 只读列表）。不含输出查看面板、terminate/write 操作 |
| 关联代码 | `astrbot/core/computer/booters/local.py`（`LocalShellComponent` / `_LocalShellSession` / `exec_managed` / `list_sessions` / `poll_session` / `_remove_session` / `add_change_listener` / `_notify_change`）<br>`astrbot/core/tools/computer_tools/shell.py`（`LocalExecuteShellTool` / `ShellSessionTool`）<br>`astrbot/core/platform/sources/webchat/webchat_queue_mgr.py`（`put_system_event` / `has_system_subscribers`）<br>`astrbot/dashboard/api/app.py`（`_shell_sessions_changed` 推送接线）<br>`astrbot/dashboard/api/chat.py`、`astrbot/dashboard/services/chat_service.py`（`get_session_shell_sessions`）<br>`dashboard/src/composables/useShellSessions.ts`（store）<br>`dashboard/src/composables/useMessages.ts`（system-stream 分发）<br>`dashboard/src/components/chat/ShellSessionIndicator.vue`（header 指示器）<br>`dashboard/src/stores/chatHeader.ts`（`shellSessions`）<br>`dashboard/src/components/chat/message_list_comps/shell_session_tools/format.ts`（`ShellSessionListItem` 类型） |
| 实施计划 | `docs/superpowers/plans/2026-10-04-shell-session-indicator.md` |
| 测试 | `tests/test_local_shell_component.py`（通知触发用例）、`dashboard/src/composables/__tests__/`（store 单测） |

---

## 1. 背景与问题

Agent 通过 `astrbot_execute_shell` 启动的命令若超过 `yield_time_ms` 仍在运行，会被托管为 `_LocalShellSession`（`local.py:472`），之后 Agent 用 `astrbot_shell_session` 交互（poll / write / interrupt / terminate）。这些会话的内核态（运行中、已退出、超时、被终止）**只在 Agent 恰好调用工具的那一刻**，以一条静态工具结果消息进入对话历史。

由此产生三个感知缺口：

1. **无持续状态**：用户不知道"此刻有 N 个后台进程在跑"。
2. **无变化通知**：进程退出 / 超时不产生任何 UI 信号，除非 Agent 主动 poll。
3. **历史快照失真**：`ShellSessionToolResultView.vue` 渲染的是调用时刻的状态，事后看会过期。

## 2. 目标与非目标

**目标**

- ChatUI 常驻显示当前会话的托管 shell 会话数量与状态，状态变化实时刷新。
- 点开可见只读会话列表（短 id、状态、pid、启动时间、未读字节数）。
- 冷启动（刷新页面 / 切换会话）能恢复到正确状态。

**非目标（明确不做）**

- 输出内容的实时推送与终端面板（Scope B，二期候选）。
- 用户在 UI 上 terminate / interrupt / write stdin（Scope C，二期候选）。
- 非 webchat 平台的适配（消息平台的推送由消息链路自行承担，不在本期）。
- 沙箱（docker）runtime 的会话（不走 `LocalShellComponent`，无此通道）。

## 3. 现状机制摘要（设计依据）

### 3.1 会话生命周期（`local.py`）

- **创建**：`exec_managed`（L611）注册 `_sessions[session_id] = session`（L769），随后用 `yield_time_ms` 短等，未退出则以 `{session_id, status: "running", ...}` 形态返回。
- **退出检测**：`wait_task = process.wait()`，退出时 `add_done_callback` 置位 `output_event`（L735）。**注意：退出不会自动产生任何通知**，状态只在下次 `poll_session` 时被读取。
- **滞留语义**：进程退出后会话**仍留在 `_sessions`**（status 变为 `completed` / `failed` / `timed_out` / `terminated`，见 L824-838 的状态推导），直到某次 poll 返回 `session_closed = true` 才由 `_remove_session`（L1283）移除并删除输出文件。
- **终止**：`terminate_session` 杀进程组后内部走一次 `poll_session`，因此正常会触发移除。

### 3.2 推送通道（现成，零新增基建）

`webchat_queue_mgr.put_system_event(conversation_id, payload)`（`webchat_queue_mgr.py:140`）：按会话向 SSE `/chat/sessions/{id}/system-stream` 的所有订阅者广播；无订阅者零开销，队列满则丢弃（best-effort）。goal 机制已用同一通道推 `goal_state_changed`。

### 3.3 前端消费先例（goal）

- `useMessages.ts` L445：system-stream 收到 `goal_state_changed` → 回调 `options.onGoalStateChanged`。
- `Chat.vue` L2027：`onGoalStateChanged: applyPushedGoal`；L1372 `useSessionGoal(currSessionId)` 持有按会话缓存的 store。
- 冷启动：`GET /chat/sessions/{session_id}/goal`（`chat.py:361`），`ChatService.get_session_goal`（`chat_service.py:2933`）做 `session.creator != username` 权限校验后用 `build_webchat_unified_msg_origin(session)` 还原 umo。
- UI 入口：header Goal 按钮（`chatHeader.goalSidebarOpen`，带 badge）+ `GoalSidebar` 抽屉（`Chat.vue:1104`）。

### 3.4 dashboard 访问内核组件的现成路径

`computer_client.local_booter.shell`（`config_service.py:487` 已在用），类型为 `LocalShellComponent` 时具备全部会话管理能力。

## 4. 方案选型

| | 方案 1：推送优先 + 全量快照（**采用**） | 方案 2：前端轮询 |
|---|---|---|
| 机制 | 状态跃迁时后端推 `shell_sessions_changed`（含全量列表快照）；REST 仅冷启动 | ChatUI 定时 GET |
| 实时性 | 即时 | 取决于间隔 |
| 改动面 | 内核组件 + 一个监听回调 | 仅 REST + 前端定时器 |
| 一致性 | 快照即权威，乱序/丢包自愈 | 同左但有延迟 |
| 架构契合 | 与 `goal_state_changed` 完全同构 | 与 push-first 架构相悖 |

**采用方案 1**。快照载荷极小（会话数通常 <10），全量替换优于增量 diff（无需处理乱序与丢失补偿）。**只推状态跃迁、不推输出内容**，事件频率天然有界（每个会话一生最多约 3 次：创建、退出、移除）。

## 5. 详细设计

### 5.1 后端：变更通知钩子（`local.py`）

`LocalShellComponent` 新增：

```python
# 字段
_change_listeners: list[Callable[[str], None]]  # 参数为 owner_id

def add_change_listener(self, listener: Callable[[str], None]) -> None: ...

def _notify_change(self, owner_id: str) -> None:
    """Fire-and-forget: listener 异常只记 warning，绝不影响 shell 管理路径。"""
```

三个触发点（覆盖 §3.1 的全部状态跃迁）：

| 时机 | 位置 | 说明 |
|---|---|---|
| 会话创建 | `exec_managed` 注册 `_sessions` 之后（L769 之后） | 注意此时进程可能已在 yield 等待期内退出，由下一时机兜底 |
| 进程退出 | `wait_task` 的 done 回调（L735 现有 callback 旁追加） | 覆盖正常退出 / 超时杀（`_enforce_timeout` 走 `_terminate_process`，最终也完成 `wait_task`）/ terminate |
| 会话移除 | `_remove_session` | 覆盖 poll 回收与 shutdown 清理 |

约束：

- 通知发射用 `asyncio.create_task` 或同步回调，**不 await**、不持 `_sessions_lock`（listener 内部会再取锁读快照，避免死锁）。
- done 回调里不能直接跑协程，用 `loop.call_soon` / `create_task` 包装。
- 内核组件**不感知 webchat/conversation 概念**，只传 `owner_id`；路由与序列化全部在 dashboard 层。

### 5.2 后端：推送接线（`app.py`）

仿 `_goal_state_changed`（L228）注册一个 listener：

```python
async def _shell_sessions_changed(owner_id: str) -> None:
    if not owner_id.startswith("webchat"):  # 非 webchat 会话直接忽略
        return
    cid = owner_id.rsplit("!", 1)[-1]
    booter = computer_client.local_booter
    if booter is None or not isinstance(booter.shell, LocalShellComponent):
        return
    result = await booter.shell.list_sessions(
        owner_id=owner_id, requester_id="", requester_is_admin=True,
    )
    await webchat_queue_mgr.put_system_event(
        cid, {"type": "shell_sessions_changed",
              "data": {"sessions": result["sessions"]}},
    )
```

快照生成放在 dashboard 层的理由：内核组件零依赖；`list_sessions` 已有 admin 视角的完整过滤逻辑，直接复用。

### 5.3 后端：REST 冷启动

```
GET /api/chat/sessions/{session_id}/shell-sessions
→ {"status": "ok", "data": {"sessions": [ShellSessionListItem...]}}
```

`ChatService.get_session_shell_sessions(username, session_id)`，与 `get_session_goal` 逐行同构：

1. `db.get_platform_session_by_id` + `session.creator != username` → 403 语义（`ChatServiceError`）。
2. `build_webchat_unified_msg_origin(session)` 还原 umo。
3. `computer_client.local_booter` 为 None 或 shell 非 `LocalShellComponent`（docker runtime）→ 返回 `{"sessions": []}`，不报错。
4. 调 `list_sessions(owner_id=umo, requester_id=username, requester_is_admin=True)`。

**权限说明**：dashboard 用户即 admin 视角（与 goal action 一致），但仍做 creator 校验保证会话归属。`requester_is_admin=True` 使 admin 可见该 umo 下全部会话——这正是"感知"的诉求。

### 5.4 前端：store（新建 `useShellSessions.ts`）

逐行镜像 `useSessionGoal.ts`：

```typescript
export function useShellSessions(currentSessionId: Ref<string | undefined>) {
  const sessionsBySession = ref<Record<string, ShellSessionListItem[]>>({});
  const inflight = new Set<string>();

  const currentSessions = computed(() => /* 缺省 [] */);
  const runningCount = computed(() => /* status === "running" */);
  const finishedCount = computed(() => /* 非 running 且仍在列表中 */);

  function applyPushedShellSessions(sessionId: string, sessions: ShellSessionListItem[]): void
  async function refreshShellSessions(sessionId: string): Promise<void>  // 冷启动 GET
  // watch(currentSessionId)：进入未缓存会话时 refresh
}
```

类型直接 import 自 `@/components/chat/message_list_comps/shell_session_tools/format`（`ShellSessionListItem`，L48），不重复定义。

### 5.5 前端：推送分发（`useMessages.ts`）

在 L445 `goal_state_changed` 分支旁新增：

```typescript
if (payload?.type === "shell_sessions_changed") {
  const data = payload.data as { sessions?: ShellSessionListItem[] } | undefined;
  options.onShellSessionsChanged?.(sessionId, data?.sessions ?? []);
  return;
}
```

`UseMessagesOptions` 增加可选回调 `onShellSessionsChanged`；`Chat.vue` 在 L2027 旁接线 `onShellSessionsChanged: applyPushedShellSessions`。

### 5.6 前端：UI（新建 `ShellSessionIndicator.vue`）

**放置决策（相对批准稿的细化）**：批准稿写"输入区上方、goal chip 旁"；核实代码后 goal 的实际入口是 **header 按钮 + badge + 抽屉**（`Chat.vue:1104, 1373`）。为与既有模式一致，本组件采用 **header 图标按钮 + badge + popover 列表**，与 Goal 按钮相邻。意图不变（常驻感知入口），仅落点对齐真实代码结构。

形态：

- `runningCount > 0`：图标按钮（`mdi-console` 类），badge 显示 running 数，running 时图标带脉冲动画（复用 `StateChip` 的 pulse 模式）。
- `runningCount === 0 && finishedCount > 0`：弱化显示（无脉冲），badge 显示滞留的已结束会话数。
- 列表为空：不渲染（零噪音）。
- 点击弹 popover（非抽屉——信息量小，抽屉过重）：逐行渲染会话摘要，**复用 `ShellSessionToolResultView` list 区的视觉元素**（`CopyableText` 短 id、`StateChip` 状态、exit code、pid、相对启动时间、`formatBytes(unread_output_bytes)`）。
- 全部只读，无操作按钮。

### 5.7 事件载荷契约

```json
{
  "type": "shell_sessions_changed",
  "data": {
    "sessions": [
      {
        "session_id": "sh_0b5539964fa34f1a",
        "pid": 22968,
        "status": "running",
        "exit_code": null,
        "started_at": 1759656000.0,
        "sandboxed": false,
        "unread_output_bytes": 1024
      }
    ]
  }
}
```

字段与 `list_sessions` 返回完全一致（`local.py:843-856`），前端类型与 `ShellSessionListItem` 一一对应。**全量快照语义**：收到即整体替换该会话的缓存。

## 6. 故障域与边界

| 场景 | 行为 |
|---|---|
| listener 抛异常 | `_notify_change` 捕获记 warning，shell 生命周期不受影响 |
| 无前端订阅 | `put_system_event` 零开销 no-op |
| 订阅队列满 | 丢事件（best-effort）；下次跃迁或冷启动自愈（快照语义） |
| 推送丢失 / 乱序 | 全量快照天然自愈，无需补偿 |
| docker runtime | REST 返回空列表；推送接线处直接 return |
| AstrBot 重启 | 会话全部消亡；前端冷启动 GET 得空列表，指示条消失 |
| 非 webchat 平台 | 推送接线处忽略（`owner_id` 前缀检查） |

## 7. 测试计划

**后端**（`tests/test_local_shell_component.py` 扩展）：

- `exec_managed` 创建会话后 listener 被调用一次，owner_id 正确。
- 进程退出后（wait_task 完成）listener 再次触发。
- poll 至 `session_closed` / `terminate_session` 后触发移除通知。
- listener 抛异常不影响 `exec_managed` / `poll_session` 正常返回。

**前端**（`dashboard/src/composables/__tests__/useShellSessions.spec.ts`）：

- `applyPushedShellSessions` 权威替换语义。
- `refreshShellSessions` 冷启动填充 + inflight 去重。
- running/finished 计数正确性。

**手工验收**：ChatUI 发一条让 Agent 启动长命令的消息（如 `ping -t`），观察 header 指示条出现 → 让 Agent terminate → 观察状态变化实时刷新 → 刷新页面，状态经冷启动恢复。

## 8. 改动文件清单

| 文件 | 改动 |
|---|---|
| `astrbot/core/computer/booters/local.py` | +`add_change_listener` / `_notify_change`，3 个触发点 |
| `astrbot/dashboard/api/app.py` | +listener 注册与推送接线 |
| `astrbot/dashboard/api/chat.py` | +1 GET 路由 |
| `astrbot/dashboard/services/chat_service.py` | +`get_session_shell_sessions` |
| `dashboard/src/api/v1`（生成的 client） | `pnpm generate:api` 重新生成 |
| `dashboard/src/composables/useShellSessions.ts` | 新建 |
| `dashboard/src/composables/useMessages.ts` | +分发分支与 options 回调 |
| `dashboard/src/components/chat/Chat.vue` | +接线与组件挂载 |
| `dashboard/src/components/chat/ShellSessionIndicator.vue` | 新建 |
| `dashboard/src/i18n/locales/{zh-CN,en-US,ru-RU,ja-JP}/features/chat.json` | 指示条文案，**四语言同步** |
| `tests/test_local_shell_component.py` | +通知触发用例 |
| `dashboard/src/composables/__tests__/useShellSessions.spec.ts` | 新建 |

## 9. 已决策项记录

| 决策点 | 结论 |
|---|---|
| 可见性范围 | Scope A 纯感知（用户在 ask_user_choice 中显式选择） |
| 同步机制 | 推送优先 + 全量快照；REST 仅冷启动 |
| 快照生成位置 | dashboard 层（app.py listener 内调 `list_sessions`），内核只发信号 |
| 事件频率 | 仅状态跃迁（创建/退出/移除），不含输出 |
| UI 落点 | header 图标按钮 + badge + popover（对齐 goal 的真实结构，见 §5.6） |
| 权限 | REST 做 creator 校验；`list_sessions` 用 admin 视角 |
