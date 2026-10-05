# Shell 会话回收路径挂死修复记录

| 项目 | 内容 |
|------|------|
| 主题 | `astrbot_shell_session` poll 在会话关闭后无限等待（ChatUI 表现为工具卡片永久计时） |
| 日期 | 2026-10-05 |
| 作者 | elecvoid243 |
| 状态 | Implemented — 已合入 `all`（commit `bb660a59a`） |
| 关联代码 | `astrbot/core/computer/booters/local.py`（`_cancel_task_bounded` / `_bounded_await` / `_remove_session`） |
| 测试 | `tests/test_local_shell_component.py`（3 个新回归测试） |
| 前序工作 | "Fix Local Shell Session Hangs"（已在 poll 主路径全面引入 `_bounded_await`，本次为该工作的收尾缺口） |

---

## 1. 现象

用户在 ChatUI 中关闭一个托管 shell 会话后，Agent 随后执行的 `astrbot_shell_session`（`action=poll`）**永不返回**：工具卡片计时持续增长（实测 5m38s），工具 `Result` 日志从未出现，用户手动点击停止也无法结束该轮次。

日志（2026-10-05 21:13，会话 `sh_c6187ecb94f0443a`）关键证据：

- `21:13:06.196` poll 开始（`yield_time_ms=3000`），此后再无该工具的 Result；
- `21:13:08.645` 用户此前的终止操作报 `Managed local shell process did not exit after kill: pid=20096`（进程树没杀干净，存在脱管孙进程）；
- 46 秒后（21:13:55）另一次 `list` 返回 `{"sessions": []}` —— 会话**已被 pop 出注册表**，但 poll 仍未返回；
- `21:13:42` 用户请求停止，无效。

## 2. 根因

**`_remove_session` 在取消 reader task 之后的裸 `await`（无任何超时）。**

会话回收路径（`poll_session` 判定 `session_closed=True` 后调用 `_remove_session`）的既有顺序：

```python
pop 会话（注册表）        # 先移除 → 所以后续 list 显示为空
...
reader_task.cancel()
try:
    await reader_task    # ← 无界等待：取消不落地即永久挂起
except (asyncio.CancelledError, Exception):
    pass                 # ← 还顺带吞掉外部取消（stop / 超时皆失效）
```

当托管 shell 的 stdout 管道仍被**脱管的孙进程**持有（Windows + Git Bash 下 MSYS2 子进程逃过进程树 kill，属本文件注释中已记录的已知顽疾），reader task 永远等不到 EOF；而 Windows Proactor 上对挂起管道读的取消不能保证及时落地——此处的 `await` 便永久挂住 **整个 poll 协程**。

**触发条件（三者同时）**：

1. 会话走到回收（任何调用方：Agent poll、Agent 自主 terminate 的内部 poll、用户终止的内部 poll、超时杀进程后的首次 poll）；
2. 回收那一刻 reader task 仍存活（管道未 EOF——通常意味着有脱管后代持有写端；自然退出且无脱管后代时 reader 自行结束，不会触发）；
3. 对 reader 的取消未及时落地。

同一模式另有第二处：`_bounded_await` 的 `cancel_on_timeout=True` 分支内部同样是 `cancel()` 后的裸 `await task`——即"有界助手自己无界"，`shutdown_sessions` 路径受此牵连。

**为什么 update 表现"像无限等待"**：工具协程不返回 → runner 的 tool-result yield 不恢复 → 结果日志（在 yield 之后打印）缺失 → UI 卡片没有结果可渲染 → 计时器一直跑；且宽 `except` 吞外部取消，stop 也无法中断。

## 3. 修复方案

统一原则：**取消后绝不裸 await 被取消的任务；改为有界等待，超时即放弃该任务**。

新增模块级助手（3 处共用，满足提取标准）：

```python
async def _cancel_task_bounded(task: asyncio.Task, timeout: float = 5) -> None:
    task.cancel()
    await asyncio.wait({task}, timeout=timeout)
    if task.done() and not task.cancelled():
        task.exception()   # 消费结果，避免 "exception was never retrieved"
```

要点：

- `asyncio.wait` 不抛异常、不消费结果、天然有界；
- **不再吞外部取消**：外层取消（stop/超时）能正常穿透，而旧代码的 `except (CancelledError, Exception): pass` 会把它吞掉后继续执行；
- 超时后任务被**放弃**（继续 pending，自行结束），不再拖住调用链。

### 改动内容

| 位置 | 改动 |
|---|---|
| `local.py` `_cancel_task_bounded` | 新增（上示） |
| `local.py` `_bounded_await` cancel 分支 | `cancel(); await task; except …pass` → `await _cancel_task_bounded(task)`（`shutdown_sessions` 顺带修复） |
| `local.py` `_remove_session` | `timeout_task` 与 `reader_task` 两个 block 均替换为 `await _cancel_task_bounded(...)` |

公开签名、工具 schema、行为语义均不变。

### 测试（TDD）

`tests/test_local_shell_component.py` 新增 3 个回归测试，用"吞掉第一次取消的任务"模拟不落地的取消：

1. `test_poll_closed_session_returns_when_reader_refuses_cancel` —— 事件现场路径：会话已退出 + 顽固 reader → poll 在有界时间内返回、`session_closed=True`、会话已移除；
2. `test_remove_session_propagates_outer_cancellation` —— 外部取消可穿透回收路径（stop 救得回卡住的 poll）；
3. `test_bounded_await_cancel_branch_is_bounded` —— 助手自身在有界时间内返回 `False`。

**测试方法学修正**：早期版本用 `asyncio.wait_for(coro, timeout)` 做断言，结果"假通过"——外层超时的取消恰好被被测代码的宽 `except` 吞掉，变成正常返回，掩盖了挂起。最终采用 `asyncio.create_task` + `asyncio.wait({task}, timeout)` **外部观察**，不向被测代码注入取消，超时即判 FAIL。

验证：`tests/test_local_shell_component.py` 86 passed；关联套件（chat_shell_sessions / shell_session_push）15 passed；ruff 全过。

## 4. Trade-offs（明知代价）

| 代价 | 说明 | 为何可接受 |
|---|---|---|
| **被放弃的 reader task 残留** | 取消不落地时 5s 后放弃：泄漏一个 pending task + 一个输出文件句柄，直到进程重启；期间临时文件可能删不掉（现有代码已容忍文件被占用） | 对比现状（进程内调用链永久卡死）是严格改善；同一会话再次回收会再发一次 cancel，第二次取消往往能真正落地（实验证实） |
| **回收路径最坏 +约 10s** | timeout_task 5s + reader 5s 都踩满时 | 正常毫秒级（任务已 done 则跳过）；远低于工具 120s 上限 |
| **极端下可能丢最后一段输出** | reader 恰在"退出后刷盘"时被 5s 边界截断 | 与 poll 既有 `reader_stuck` 处理同一哲学；仅发生在管道被脱管进程持有的病态场景 |
| **关机期日志噪音** | 多会话卡住时，每个残留 task 在事件循环关闭时产生 "Task was destroyed but it is pending" | 仅退出期、仅病态场景 |

**未解决（明确边界）**：本修复是"有界 + 放弃"，不是"让取消可靠落地"。根因场景本身（脱管孙进程持有管道 + Proactor 取消不可靠）依旧存在——只是不再能拖死调用链。要让任务真正回收需要主动关闭管道 transport，属高风险手术，不在本次范围。

## 5. 复现与验证方法（供后续维护）

- 单测：`pytest tests/test_local_shell_component.py -k "refuses_cancel or propagates_outer or cancel_branch" -v`
- 现场复现：`ping -t` 起会话 → 窗口终止（或 `terminate`）→ 对已关闭会话 poll；若 kill 日志出现 `did not exit after kill`，即处于触发条件中
