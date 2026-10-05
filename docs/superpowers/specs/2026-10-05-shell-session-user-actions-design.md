# Shell 会话用户操作（peek/terminate）+ Agent 感知 — 设计文档

| 项目 | 内容 |
|------|------|
| 主题 | ChatUI 用户手动查看（peek）与终止（terminate）托管 shell 会话；用户操作通过 [SYSTEM NOTICE] 让活动中的 Agent 感知 |
| 日期 | 2026-10-05 |
| 作者 | elecvoid243 |
| 状态 | Implemented — 已合入 `all`（merge `c2cc3feea`） |
| 前序 | `2026-10-04-shell-session-indicator-design.md`（Scope A 感知层，已实施） |
| 关联代码 | `astrbot/core/computer/booters/local.py`（`poll_session` / `terminate_session` / `_get_owned_session`）<br>`astrbot/core/pipeline/process_stage/follow_up.py`（`_ACTIVE_AGENT_RUNNERS` / `register_active_runner`）<br>`astrbot/core/agent/runners/tool_loop_agent_runner.py`（`follow_up` / `_merge_follow_up_notice`，L841；消费点 L1287）<br>`astrbot/dashboard/services/chat_service.py`、`astrbot/dashboard/api/chat.py`<br>`dashboard/src/components/chat/ShellSessionIndicator.vue`（popover 入口）<br>`dashboard/src/components/chat/message_list_comps/GoalSidebar.vue`（抽屉先例）<br>`dashboard/src/stores/chatHeader.ts` |
| 测试 | `tests/test_local_shell_component.py`、`tests/unit/test_chat_shell_sessions.py`（扩展）、`tests/unit/test_shell_session_push.py`、runner 通知合并测试 |

---

## 1. 背景与目标

Scope A 已落地感知层（指示器 + 推送）。本次扩展两个用户操作：

1. **peek**：用户查看会话输出（实时跟随）。
2. **terminate**：用户终止失控会话。

并要求：**用户操作发生时若 Agent 正在活动 run 中操作同一会话，Agent 应通过工具结果中的 `[SYSTEM NOTICE]` 得知**。

展示形态：不新开页面，复用 GoalSidebar 的**右侧抽屉**模式（用户评审时修正）。

## 2. 关键设计决策

| 决策点 | 结论 | 理由 |
|---|---|---|
| peek 的游标语义 | **绝不写 `session.cursor`** | `poll_session` 会推进游标；用户看一眼，Agent 的下一次增量 poll 就丢输出。peek 必须是非破坏性的 |
| peek 是否通知 Agent | **不通知** | peek 不改变任何 Agent 可见状态，通知是纯噪音（用户已确认） |
| terminate 通知通道 | runner 新增独立 `_pending_system_notices`，**不复用 follow-up 模板** | follow-up 模板措辞是"用户发来跟进消息"，对 dashboard 动作语义不符 |
| 无活动 run 时 | 通知静默丢弃（返回 False），不排队 | 通知是瞬时信号；Agent 下次 poll 自然看到会话消失 |
| 展示形态 | **多实例悬浮窗**（todo-summary-bar 弹层同族，可拖动），用户评审两次修正：侧边栏 → 弹窗 → 多窗口并存 | 每个会话独立窗口，不打断聊天流 |
| 推送 | 零新增 | terminate 触发 exit/remove 通知 → 既有 `shell_sessions_changed` 快照推送自动刷新指示器 |

## 3. 后端设计

### 3.1 `LocalShellComponent.peek_session_output`（local.py）

```python
async def peek_session_output(
    self,
    *,
    owner_id: str,
    requester_id: str,
    requester_is_admin: bool,
    session_id: str,
    cursor: int = 0,
    yield_time_ms: int = 0,
    max_output_chars: int = 50_000,
) -> dict[str, Any]:
```

- 权限复用 `_get_owned_session`（owner 匹配 + requester/admin 过滤）。
- 读取 `session.output_path` 的 `[cursor, cursor+max_output_chars)`，返回 `{session_id, pid, status, stdout, exit_code, cursor(next), has_more, session_closed}`——**字段与 poll 结果同形**，但全程不触碰 `session.cursor`。
- `yield_time_ms > 0` 时等待 `output_event` / `wait_task`（长轮询，上限 120s 同 poll）。结束时若进程已退出且读取到文件尾，`session_closed=True`——但**peek 不调用 `_remove_session`**（回收是 poll 的职责；peek 只读）。
- `poll_session` 内联的 `_read_output` 闭包提取为组件私有方法 `_read_output_range(session, cursor, max_chars) -> tuple[bytes, int, int]`，poll 与 peek 共用（两处复用满足提取标准）。

### 3.2 REST 端面（chat.py + chat_service.py）

```
GET  /api/v1/chat/sessions/{sid}/shell-sessions/{shid}/output
     ?cursor=0&max_chars=50000&yield_time_ms=2000
POST /api/v1/chat/sessions/{sid}/shell-sessions/{shid}/terminate
```

`ChatService` 两个方法，前置校验与 `get_session_shell_sessions` 相同（session 归属 + local booter 守卫）：

- `get_shell_session_output(...)` → 透传 peek 结果。
- `terminate_shell_session(...)` → `component.terminate_session(...)`；成功后调用 §3.3 的通知注入。返回 terminate 的结果 dict。

OpenAPI yaml 同步 + `pnpm generate:api` 重新生成客户端。

### 3.3 Agent 感知（follow_up.py + tool_loop_agent_runner.py）

**runner 侧**：

```python
_SYSTEM_NOTICE_TEMPLATE = "\n\n[SYSTEM NOTICE] {text}"

def inject_system_notice(self, text: str) -> bool:
    """Queue a system notice for the next tool result. Returns False when
    the run is done/stopped or the text is empty."""
```

- `_pending_system_notices: list[str]`（init 处与 `_pending_follow_ups` 并列）。
- `_merge_follow_up_notice` 扩展：先消费 `_pending_system_notices`（各格式化为 `[SYSTEM NOTICE] {text}`），再拼接 follow-up notice。消费点不变（L1287），因此**每条工具结果都会携带积压的通知**。

**注入入口**（follow_up.py，公开函数）：

```python
def inject_system_notice(umo: str, text: str) -> bool:
    """Inject a system notice into the umo's active agent run.

    Returns False when no run is active — notices are transient signals,
    never queued for future runs.
    """
    runner = _ACTIVE_AGENT_RUNNERS.get(umo)
    if runner is None:
        return False
    return runner.inject_system_notice(text)
```

**terminate 的通知文本**（chat_service 调用处组装）：

```
[ChatUI] User terminated managed shell session sh_xxx (pid 1234).
If you were polling it, expect "not found" errors — do not retry it.
```

## 4. 前端设计

### 4.1 `ShellSessionWindow.vue`（新建，多实例悬浮窗）

形态参照 todo-summary-bar 的弹层（`position: fixed` 浮动窗口，头部可拖动），**每个会话一个窗口实例，可同时存在多个**：

- 窗口宿主在 `Chat.vue`；窗口集合状态存 `chatHeader` store：`openShellWindows: string[]`（session_id 列表）+ `SET_SHELL_WINDOW_OPEN(id, open)` + `FOCUS_SHELL_WINDOW(id)`（置顶：移到数组尾部，渲染时 z-index 按序递增）。指示器在 ChatInput 内，经 store 通信，与既有 `shellSessions` 推送同通道。
- 窗口组件按 session_id key 渲染（`v-for`），每个实例自持：peek 轮询循环（cursor 从 0，`yield_time_ms=2000` 长轮询追加）、位置（初始按窗口数级联偏移，避免重叠）、跟随输出开关、终止按钮（二次确认）。
- 重复打开同一 session：不新建，置顶已有窗口。
- 头部：状态点（running 脉冲）+ 完整 session_id（flex 省略）+ 状态文本 + pid/相对时间 + terminate（二次确认）+ 关闭。日志区深色终端样式；底栏显示已读字节数/轮询状态。
- 会话被回收（推送列表中消失）：窗口**不自动关闭**，定格显示终态与已读输出，由用户手动关闭。

### 4.2 指示器 popover 入口（ShellSessionIndicator.vue 修改）

每行整体可点击（或行尾"查看输出"图标钮）→ `SET_SHELL_WINDOW_OPEN(session_id, true)`。chip 本体点击仍开合 popover（会话列表总览）。terminate 不放在 popover（避免误触，窗口内二次确认）。

### 4.3 i18n

`shellSession.window.*`：view / terminate / terminateConfirm / terminated / followOutput / closed 等，四语言同步（ja-JP 补齐同块）。

## 5. 故障域与边界

| 场景 | 行为 |
|---|---|
| peek 期间进程退出 | 返回已读输出 + `session_closed`；不删除会话（poll 才回收） |
| peek 期间会话被 poll 回收 | `_get_owned_session` 抛 ValueError → REST 404 语义，抽屉显示"会话已关闭" |
| terminate 时无活动 Agent run | 通知注入返回 False，操作本身不受影响 |
| 通知注入时 run 恰好结束 | `inject_system_notice` 返回 False，静默 |
| 用户 terminate 与 Agent poll 竞态 | 组件层 `_sessions_lock` 已串行化；Agent 得到 not-found 错误 + SYSTEM NOTICE 解释 |
| 输出文件巨大 | peek 默认 50KB/次，客户端 cursor 分页；不引入全量加载 |

## 6. 测试计划

**后端**：

- `peek_session_output`：读取不推进 `session.cursor`（peek 后 poll 仍能拿到相同增量）；权限拒绝路径；yield 长轮询返回新输出；进程退出后 `session_closed=True` 但会话仍在 `_sessions`。
- runner：`inject_system_notice` 后下一条工具结果携带 `[SYSTEM NOTICE]`；消费后清空；done/stopped 时返回 False。
- `inject_system_notice(umo)`：无活动 run 返回 False；有 run 时到达 runner。
- ChatService：terminate 成功路径调用通知注入（mock 断言文本含 session_id）；peek 透传。

**前端**：

- 抽屉轮询逻辑（mock chatApi：cursor 推进、长轮询追加、closed 后停止）。
- 指示器"查看输出"按钮驱动 store。

## 7. 改动文件清单

| 文件 | 改动 |
|---|---|
| `astrbot/core/computer/booters/local.py` | +`peek_session_output`、提取 `_read_output_range` |
| `astrbot/core/agent/runners/tool_loop_agent_runner.py` | +`inject_system_notice` / `_pending_system_notices` / 合并点扩展 |
| `astrbot/core/pipeline/process_stage/follow_up.py` | +公开 `inject_system_notice(umo, text)` |
| `astrbot/dashboard/services/chat_service.py` | +2 方法 |
| `astrbot/dashboard/api/chat.py` | +2 路由 |
| `openspec/openapi-v1.yaml` + 生成客户端 | +2 端点 + scope 文档再生成 |
| `dashboard/src/components/chat/ShellSessionWindow.vue` | 新建（多实例悬浮窗） |
| `dashboard/src/components/chat/ShellSessionIndicator.vue` | +查看入口 |
| `dashboard/src/components/chat/Chat.vue` / `stores/chatHeader.ts` | 窗口宿主与开窗状态 |
| `dashboard/src/i18n/locales/*/features/chat.json` | `shellSession.window.*` ×4 |
| 测试 | 后端 3 个文件扩展 + runner 测试 + 前端 spec |
