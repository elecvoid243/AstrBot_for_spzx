# 窗级会话归档与 `<context_window>` 注入

> Author: elecvoid243 · 2026-11-17
> 状态：设计待确认（介质已定：文件目录）
> 关联：[2026-11-15-token-budget-compression-design.md](2026-11-15-token-budget-compression-design.md) —— 换窗语义与 `WindowState` 的唯一来源，本 spec 不重新定义它们

## 1. 背景

`token_budget` 换窗把历史替换为“初始上下文 + 当前轮”，被丢弃的历史只存在于内存，请求结束即消失。方案 A（旧窗可检索）没有物理基础：模型换窗后无法回到任何更早的内容。

现状与缺口：

| 项 | 现状 | 缺口 |
|---|---|---|
| 窗身份 | `WindowState{id, seq, reminder_sent, exhausted_sent}` 随最后一条落库消息的 `_window` 持久化 | 无 `prev_id`，窗与窗不成链 |
| 换窗 | 唯一收口 `_on_context_reset()`，当前仅 `token_budget` 调用 | 不归档、不通知模型 |
| 旧窗正文 | 无 | 无落盘副本，无检索入口 |
| 提示词 | 只有两条预算提示（`internal`，模型可见） | 模型不知道当前窗 id、序号、是否有旧窗 |

## 2. 目标与非目标

目标：

1. 归档按 **umo → window** 两级划分，每个窗独立成一份可枚举、可读取的正文。
2. 换窗瞬间生成新窗 id，并把旧窗完整落盘（append-only，不覆盖）。
3. 每轮注入 `<context_window>` 元数据（当前窗 id、序号、上一窗 id）。
4. 提供按当前 umo 隔离的检索工具面，供模型回溯旧窗。

非目标：

- 不做归档的可视化界面（WebUI 读 `conversations.content`，不读归档）。
- 不做归档的自动清理 / 保留策略（本轮 append-only，磁盘增长由后续 spec 处理）。
- 不做 `notes` 私有笔记工具（独立 spec）。
- 不改 `llm_compress` 路径：摘要已经把内容压进新历史，无需归档。
- 不新增数据库表或会话列。

## 3. 术语

| 术语 | 含义 |
|---|---|
| umo | `unified_msg_origin`，会话的稳定身份，归档第一级分区 |
| 窗 | 一次换窗到下一次换窗之间的模型可见历史生命周期 |
| 窗 id | `WindowState.id`，32 位 uuid4 hex，归档第二级分区 |
| 归档 | 被换窗丢弃的旧窗正文的持久副本 |
| 活跃窗 | 当前 `conversations.content` 里的历史，与归档互不重叠 |

## 4. 已定决策

| 编号 | 决策 | 说明 |
|---|---|---|
| D1 | 归档介质是文件目录，两级 `umo_key/window_id` | 零迁移、可直接审计、按 umo 整目录清理；检索靠逐窗扫描，窗数量级小 |
| D2 | 窗 id 复用 `WindowState.id`；新增 `prev_id`；**不存** `first_id` | `seq=0` 即首窗，存 `first_id` 是第二份真源 |
| D3 | 归档时机是换窗瞬间，对象是压缩前列表里**被丢弃的前缀** | 当前轮已保留在新窗，整份 `contexts` 归档会让同一轮出现两次 |
| D4 | 归档内容 = 旧窗的“落库视图” | 跳过 leading system、`internal`、`_no_save`，与 `_save_to_history` 同规则，保证检索结果与 WebChat 历史同构 |
| D5 | `<context_window>` 追加在 system prompt 末尾，每次请求重建、不落库 | 窗 id 在窗内不变，放 system 前缀使每轮前缀逐字节一致；放历史里会因为不落库而在下一轮重放时消失，导致从该处起缓存断链 |
| D6 | 检索工具以运行时 umo 为隐式边界，不作为参数 | 模型只能检索自己这个会话，无法跨 umo 越权 |
| D7 | 无 `_window` 标记时，`seq` / `prev_id` 由归档窗口数与最近关闭的窗口推导 | 标记搭在“最后一条落库消息”上，换窗后那一轮没保存就会丢；归档目录是唯一存活的窗口链 |
| D8 | 归档条目的 `ts` 取消息自身的 `created_at`，缺失时用批次时间并标 `ts_estimated` | 伪造时间会让“按时间检索”建立在假数据上；宁可标明估算 |

## 5. 归档布局

```
data/agent_history/
  <umo_key>/
    windows.json
    <window_id>/
      items.jsonl
```

### 5.1 `umo_key`

umo 含 `:` 与 `!`，不能直接做目录名（Windows 上 `:` 非法）。规则：

```python
platform = umo.split(":", 1)[0] or "unknown"
safe = re.sub(r"[^A-Za-z0-9_-]", "", platform)[:24] or "umo"
umo_key = f"{safe}_{hashlib.sha1(umo.encode('utf-8')).hexdigest()[:12]}"
```

可读性由 `windows.json` 里的原 umo 承担，不靠目录名。

### 5.2 `windows.json`

```json
{
  "umo": "webchat:FriendMessage:webchat!astrbot!f0ee...",
  "windows": [
    {
      "window_id": "3f0a1c...",
      "seq": 0,
      "prev_id": null,
      "opened_at": 1763300000,
      "closed_at": 1763390000,
      "item_count": 42,
      "bytes": 18322,
      "first_user_text": "帮我改一下归档结构"
    }
  ]
}
```

- `closed_at is None` 表示归档写入中断，下次换窗时补写（`items.jsonl` 追加幂等，见 5.4）。
- `first_user_text` 截断到 120 字符，只用于列表展示与模型选择。

### 5.3 `items.jsonl`

一行一条，按时间顺序：

```json
{"ts": 1763300000, "role": "user", "content": "…"}
{"ts": 1763300001, "role": "assistant", "content": null, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "fs_read", "arguments": "{}"}}]}
{"ts": 1763300002, "role": "tool", "tool_call_id": "call_1", "content": "…"}
```

- 字段取自 `Message.model_dump(mode="json")`，剔除 `_window`、`_internal`、`_compaction_id`。
- `ts` 取自 `Message.created_at`（消息产生时刻）；老消息没有该字段时用归档批次时间并写 `"ts_estimated": true`，不伪装成消息时间。
- `content` 为 list 时原样保留（图片/文本分片）。
- 单条 `content` 文本超过 `MAX_ITEM_CHARS = 8000` 时截断，并追加 `"truncated": true, "original_chars": N`。
- 媒体只保留引用（URL / 本地路径字符串），不复制文件。

### 5.4 写入与幂等

| 环节 | 规则 |
|---|---|
| 幂等键 | 旧窗 `window_id`；`windows.json` 里已有 `closed_at` 的窗直接跳过 |
| 进程内去重 | runner 持有 `_archived_window_ids: set[str]`，同一次 run 的多次 prepare 只归档一次 |
| 顺序 | 先 append `items.jsonl`，成功后原子更新 `windows.json`（写临时文件 + `os.replace`） |
| 并发 | 按 `umo_key` 的 `asyncio.Lock`（进程内 dict 缓存），避免同 umo 并发 run 的 read-modify-write 丢更新 |
| 阻塞 | 文件 IO 走 `asyncio.to_thread` |
| 失败 | 打 warning，不阻断换窗；`closed_at` 留空待补写 |

## 6. 窗身份与换窗收口

`WindowState` 改前 / 改后：

```python
# 改前
class WindowState(BaseModel):
    id: str
    seq: int = 0
    reminder_sent: bool = False
    exhausted_sent: bool = False

# 改后
class WindowState(BaseModel):
    id: str
    seq: int = 0
    prev_id: str | None = None   # 新增：旧窗链
    reminder_sent: bool = False
    exhausted_sent: bool = False
```

`_on_context_reset()` 改前 / 改后：

```python
# 改前
def _on_context_reset(self) -> None:
    self.window_state = WindowState(
        id=uuid.uuid4().hex, seq=self.window_state.seq + 1
    )
    self._window_just_reset = True

# 改后：归档在重置之前，旧窗 id 还在 self.window_state 上
def _on_context_reset(self, discarded: list[Message] | None = None) -> None:
    previous = self.window_state
    if discarded:
        self._schedule_archive(previous, discarded)
    self.window_state = WindowState(
        id=uuid.uuid4().hex,
        seq=previous.seq + 1,
        prev_id=previous.id,
    )
    self._window_just_reset = True
```

调用点（`_prepare_request_payload`）改前 / 改后：

```python
# 改前
if stage == "main" and (...).get("strategy") == "token_budget":
    self._on_context_reset()

# 改后：contexts 是压缩前的旧窗全量，先切出被丢弃的前缀
if stage == "main" and (...).get("strategy") == "token_budget":
    self._on_context_reset(
        self.request_context_manager.truncator.discarded_prefix(contexts)
    )

无 `_window` 标记时（换窗那一轮未落库、进程崩溃），`_load_window_state` 走 `_window_from_archive()`：`seq = max(归档窗口数, 最大 seq + 1)`，`prev_id = 最近关闭的窗口 id`，而不是从 0 新建。

### 6.1 被丢弃前缀的边界

`contexts` 含当前轮，`checked` 也含当前轮——直接归档 `contexts` 会把当前轮复制进旧窗，`history_search` 立刻能搜到刚刚说过的话。归档对象必须是两者的差：

```python
def discarded_prefix(self, messages: list[Message]) -> list[Message]:
    """Return the messages a window reset would drop.

    Args:
        messages: Model-visible history, including the current request.

    Returns:
        Everything after the leading system messages and before the newest
        round; empty when there is only one round.
    """
```

实现复用 `_split_system_rest` 与 `split_into_rounds`，与 `reset_to_latest_round` 同一分段函数，因此“归档什么”和“丢弃什么”不可能分叉。`leading system` 不进归档（每轮重建，无检索价值，D4）。

长度校验用不变量断言而非相等比较：`len(discarded_prefix(contexts)) + len(kept_round) == len(contexts) - len(leading_system)`。

`new_context` 工具（方案 C）复用同一收口：工具只置 `_new_context_requested`，下一次 `_prepare_request_payload` 消费它，依次执行 `discarded_prefix` → `_on_context_reset` → `reset_to_latest_round`，并令 `reset_now=True` 抑制新窗的余额提醒。工具结果在消费前已进入 `contexts`，因此保留在新窗里。

## 7. `<context_window>` 注入

注入位置：`_prepare_llm_request` 定稿 payload 之后，`stage == "main"` 且 `strategy == "token_budget"`（只有该策略会换窗；其他策略下 `seq` 永远是 0，注入是噪声）。形态是追加到请求 system prompt 的末尾；请求没有 system prompt 时插入一条只含该块的 system 消息。

频率：每次请求重建，不进历史。窗 id 在窗内不变，所以同窗内每轮 system 前缀逐字节一致，前缀缓存可以跨轮命中；放进历史则相反——`internal` 不落库，下一轮从库里重放时该消息消失，从它的位置往后（含上一轮 assistant 输出）全部失效。

```
<context_window>
Current window id: 9c2e4b1f...
Window seq: 3
Previous window id: 3f0a1c22...
Earlier windows are archived; use the history tools to search them.
</context_window>
```

- `Previous window id` 行仅在 `prev_id is not None` 时出现。
- `Earlier windows are archived; …` 行仅在 `seq > 0` 时出现；`seq == 0` 时整段只有两行。
- 不注入 umo、agent 名：umo 是平台身份，与模型决策无关，且属于不该外泄的会话键。
- 与 codex 的差异：去掉 `Agent name` / `First context window id`（AstrBot 无 agent 路径概念；首窗由 `seq=0` 推出）。

### 7.1 子代理

子代理与主会话共享 umo，归档目录与 `_window` 标记描述的都是主会话，因此子代理：

- 永远不换窗：`_on_context_reset` 在 `is_subagent` 时直接返回，不归档、不递增 seq；
- 不继承窗口状态：`_load_window_state` 在 `is_subagent` 时直接返回新窗（`seq=0`），忽略标记与归档；
- 压缩策略固定为 LLM 压缩：`build_subagent_compression_kwargs` 不再透传 `token_budget`，优先用配置的摘要 provider，取不到时回退子代理自身 provider，仍取不到则由 runner 默认截断兜底。

## 8. 检索工具面

三个内置工具，`@builtin_tool` 注册，umo 从 `context.context.event.unified_msg_origin` 取：

| 工具 | 参数 | 返回 |
|---|---|---|
| `history_list_windows` | 无 | 窗列表：`seq`、`window_id`、起止时间、条数、`first_user_text` |
| `history_search` | `query: str`、`window_id: str \| None`、`limit: int = 10` | 匹配条目：`window_id`、`seq`、`index`、`role`、片段（前后各 80 字符） |
| `history_read` | `window_id: str`、`offset: int = 0`、`limit: int = 50` | 该窗消息序列，单条内容截断到 2000 字符 |

边界与错误：

- 未知 `window_id` 或不属于当前 umo：统一返回 `No such window.`，不区分“不存在”与“越权”。
- `query` 为空或纯空白：返回参数错误，不返回全量。
- 无归档时 `history_list_windows` 返回空列表说明文本，而不是报错。

## 9. 接口变更清单

| 文件 | 改动 | 原因 |
|---|---|---|
| `astrbot/core/agent/message.py` | `WindowState` 增 `prev_id` | D2 |
| `astrbot/core/agent/context/truncator.py` | 新增 `discarded_prefix(messages)` | 6.1 |
| `astrbot/core/agent/history_archive.py`（新） | `umo_key`、`append_window`、`list_windows`、`search`、`read_window` | 5 |
| `astrbot/core/agent/runners/tool_loop_agent_runner.py` | `_on_context_reset(discarded)`；新增 `_schedule_archive`、`_append_window_notice`；`_archived_window_ids` | 6、7 |
| `astrbot/core/tools/history_tools.py`（新）+ `astrbot/core/tools/registry.py` | 四个 `@builtin_tool`（含 `new_context`）；模块加入 `_BUILTIN_TOOL_MODULES` | 8 |
| `astrbot/core/utils/astrbot_path.py` | `get_astrbot_agent_history_path()` | 5 |
| `tests/agent/test_history_archive.py`（新） | 布局、幂等、截断、越权 | 11 |
| `tests/test_tool_loop_context_window_notice.py`（新） | 注入内容与频率 | 11 |

## 10. 兼容与回滚

- 旧历史无 `prev_id`：`WindowState` 默认 `None`，注入时省略该行，归档索引 `prev_id` 记 `null`。
- 归档目录不存在：首次写入时创建，读工具返回空列表。
- 回滚方式：删 `data/agent_history/` 即完全失效，不影响活跃窗、配置与 DB。
- 风险面：动的是换窗副作用（行为）与新增只读工具（能力），不改配置结构、不改 DB。
- 隐私面：归档含完整会话正文，落在 `data/` 下与 `conversations` 同级；访问边界由工具层 umo 隔离承担。

## 11. 测试计划

单元：

1. `umo_key` 对含 `:` / `!` / 中文 / 超长 umo 稳定且可区分。
2. `append_window` 两次同一 `window_id` 只落一份。
3. 索引原子更新：模拟 `windows.json` 写失败，`items.jsonl` 保留、`closed_at` 为空。
4. `content` 超限截断并带 `truncated` / `original_chars`。
5. `history_read` / `history_search` 跨 umo 访问返回 `No such window.`。
6. `_on_context_reset` 后 `prev_id == 旧 id`、`seq + 1`、两个 `sent` 复位。
7. 无 `_window` 标记时 `seq` 由归档窗口数推导、`prev_id` 指向最近关闭的窗口；标记存在时以标记为准。
8. 归档条目保留 `created_at`；缺失时 `ts_estimated` 为 true。

集成：

9. 连续两次换窗：`windows.json` 出现两条记录，`prev_id` 成链，`items.jsonl` 条数与裁剪量一致。
10. 换窗后首轮请求的 system prompt 末尾出现 `<context_window>`，历史与落库视图中都没有该块；同窗内连续两轮请求的 system 前缀完全一致。
11. `llm_compress` 触发压缩时不产生归档目录。
12. 子代理 runner 不换窗、不写归档、不继承主会话的 `_window` 标记；token_budget 会话下子代理仍拿到 LLM 压缩参数。

## 12. 实施顺序

1. `WindowState.prev_id` + `_on_context_reset(discarded)`（无归档副作用，先跑通链）。
2. `history_archive.py` + 路径工具 + 单测（纯函数，不接 runner）。
3. runner 接入归档（`_schedule_archive`、幂等、失败策略）。
4. `<context_window>` 注入 + 单测。
5. 三个检索工具 + 注册 + 越权测试。
6. 集成回归（换窗两次、`llm_compress` 不归档）。
7. `new_context` 工具（方案 C）单独一轮，复用 1–6 的收口。
