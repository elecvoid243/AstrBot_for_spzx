# 模型私有笔记（notes）

> Author: elecvoid243 · 2026-11-17
> 状态：设计待确认
> 关联：[2026-11-17-context-window-archive-design.md](2026-11-17-context-window-archive-design.md) —— 窗归档是只读历史，本 spec 是可写的会话级记忆

## 1. 背景

换窗会丢弃旧历史，归档只能读。模型需要一块**自己写、跨窗保留**的地方：记录任务进度、待验证的假设、用户偏好，换窗后不必重新推导。

codex 的对应物是 `notes.*` 五个工具（`codex-rs/ext/history-notes/src/tools.rs`），存储是虚拟路径、模型私有、单文件 1 MB 上限。本 spec 取其语义，去掉 agent 命名空间与远端后端。

## 2. 目标与非目标

目标：

1. 每个 umo 一块笔记空间，同一会话的所有窗共享。
2. 四个工具覆盖列出、读取、搜索、写入。
3. 路径是虚拟路径，不允许逃出该 umo 的笔记根。
4. 单文件与单 umo 有字节上限，超限拒绝而不是截断。
5. 笔记存在时，`<context_window>` 带最近文件摘要，换窗后模型知道有东西可查。

非目标：

- 不做笔记的 WebUI 可视化（本轮只有工具面）。
- 不做语义检索 / 向量索引：逐文件子串匹配足够，笔记规模小。
- 不做版本历史与冲突合并：单模型顺序写入，后写覆盖先写。
- 不复用 tc_memory，也不写数据库。

## 3. 术语

| 术语 | 含义 |
|---|---|
| 笔记根 | `data/agent_notes/<umo_key>/`，一个 umo 一块 |
| 虚拟路径 | 相对笔记根的 `/` 分隔路径，如 `plan.md`、`tasks/api.md` |
| 覆盖写 | 整个文件替换 |
| 追加写 | 在文件末尾追加，不改已有内容 |

## 4. 已定决策

| 编号 | 决策 | 说明 |
|---|---|---|
| N1 | 存储是文件，`data/agent_notes/<umo_key>/<path>`，`umo_key` 复用 `history_archive.umo_key` | 与归档同一分区规则，单一真源 |
| N2 | 四个工具：`notes_list` / `notes_read` / `notes_search` / `notes_write` | codex 的 write/append 合成一个 `mode` 参数；命名即契约，模式显式 |
| N3 | 单文件上限 64 KiB，单 umo 上限 1 MiB | 64 KiB ≈ 16k token，超过应拆文件；1 MiB 防无限增长 |
| N4 | 超限拒绝，不截断 | 静默截断会让模型以为自己写成功了 |
| N5 | 路径校验在存储层做，工具层只透传 | 越权检查不能依赖每个调用方记得做 |
| N6 | 工具与 `<context_window>` 摘要同门注入：仅 `token_budget` 策略 | 与 history 工具一致，未开启不注入 |
| N7 | 工具描述写明“模型私有状态，不向用户披露” | 对齐 codex；笔记是内部记忆，不是交付物 |

## 5. 布局与配额

```
data/agent_notes/
  <umo_key>/
    plan.md
    tasks/
      api.md
```

- 无扩展名的路径补 `.md`。
- 目录按需创建；空目录不参与配额。
- 配额按 UTF-8 字节计。

## 6. 路径校验

按顺序执行，任一步失败返回错误文本：

1. 去首尾空白；空字符串拒绝。
2. 反斜杠统一为 `/`。
3. 以 `/` 开头、含盘符（`C:`）或 UNC 前缀一律拒绝。
4. 拆分组件；空组件、`.`、`..` 拒绝。
5. 组件含 Windows 保留名（`CON`、`PRN`、`AUX`、`NUL`、`COM1-9`、`LPT1-9`）拒绝。
6. 单组件 > 100 字符、总路径 > 200 字符拒绝。
7. `Path.resolve()` 后必须仍在笔记根内（最后一道防线）。

## 7. 工具契约

| 工具 | 参数 | 返回 |
|---|---|---|
| `notes_list` | `prefix: str = ""`、`max_results: int = 50` | JSON 列表：`path`、`bytes`、`lines`、`updated_at`，按 `updated_at` 倒序 |
| `notes_read` | `path: str`、`start_line: int = 1`、`stop_line: int \| None = None` | 头部 `path (lines a-b of n)` + 正文；行号 1-based，负数从末尾算 |
| `notes_search` | `query: str`、`path_prefix: str = ""`、`max_files: int = 10`、`max_matches_per_file: int = 3` | JSON：`path`、`line`、`snippet`（前后各 60 字符） |
| `notes_write` | `path: str`、`text: str`、`mode: str = "overwrite"` | 确认文本：写入后大小与行数 |

错误一律返回 `Error: ...` 文本，不抛异常：

- 空 `query` 拒绝；空 `text` 允许（覆盖写即清空文件，追加写为空则是无操作）。
- 未知文件读 → `Error: no such note.`
- 超配额 → `Error: note would exceed ...`

## 8. `<context_window>` 摘要

笔记根非空时，在 `<context_window>` 块末尾追加：

```
Private notes (shared across windows, newest first):
- plan.md (12 lines)
- tasks/api.md (4 lines)
```

最多 3 条，按 `updated_at` 倒序。没有笔记时整段不出现。

## 9. 接口变更清单

| 文件 | 改动 | 原因 |
|---|---|---|
| `astrbot/core/agent/notes_store.py`（新） | `notes_root`、`validate_path`、`list_notes`、`read_note`、`search_notes`、`write_note` | 5–7 |
| `astrbot/core/tools/notes_tools.py`（新） | 四个 `@builtin_tool` | 7 |
| `astrbot/core/tools/registry.py` | 模块加入 `_BUILTIN_TOOL_MODULES` | 7 |
| `astrbot/core/astr_main_agent.py` | `_apply_history_tools` 增四个工具，函数改名为 `_apply_context_tools` | 7、N6 |
| `astrbot/core/agent/runners/tool_loop_agent_runner.py` | `_context_window_notice` 接受 notes 摘要 | 8 |
| `tests/agent/test_notes_store.py`（新） | 路径、配额、读写搜 | 11 |
| `tests/unit/test_notes_tools.py`（新） | 工具契约与越权 | 11 |

## 10. 兼容与回滚

- 笔记根不存在：列表返回空，读返回 `no such note`，写入时创建。
- 回滚方式：删 `data/agent_notes/` 即完全失效，不影响历史、归档与配置。
- 风险面：新增写盘能力（行为）与四个工具（能力），不改配置结构、不改 DB。
- 隐私面：笔记是模型私有状态，工具描述要求不向用户披露；文件落在 `data/` 下，与归档同级。

## 11. 测试计划

1. `validate_path` 拒绝 `..`、绝对路径、盘符、保留名、超长组件。
2. `notes_write` 覆盖与追加的字节数、行数；无扩展名补 `.md`。
3. 单文件超 64 KiB 与单 umo 超 1 MiB 均拒绝且不落盘。
4. `notes_read` 的行区间与负索引。
5. `notes_search` 大小写不敏感、按文件与条数上限。
6. 跨 umo 读同一路径互不可见。
7. `<context_window>` 在有无笔记时的摘要差异。

## 12. 实施顺序

1. `notes_store.py` + 单测（纯函数与文件操作，不接工具）。
2. 四个工具 + 注册 + 工具注入。
3. `<context_window>` 摘要。
4. 回归与 ruff。
