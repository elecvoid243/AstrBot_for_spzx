# ChatUI 文件变更总结（End-of-Turn File Change Summary）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agent Loop 完全结束后，在 ChatUI 的最终回复下方渲染一张"文件变更总结"卡片：按文件聚合本轮所有文件工具调用的**净 diff**（多次编辑同一文件只显示最终结果），每行支持 查看 diff / 在磁盘打开 / 撤销。

**Architecture:** 后端文件工具（`astrbot_file_write_tool` / `astrbot_file_edit_tool`）在每次成功修改文件时向 `AstrAgentContext.extra["changed_files"]` 追加一条记录（含本轮首个基线备份的 `backup_id`）；runner 在 DONE 收尾时用 `EditHistoryManager` 的基线备份与当前文件内容计算净 diff，通过新增 `file_changes` 流事件（模仿现有 `agent_stats` 事件的全链路模式）推送并随消息持久化；前端在 `ChatMessageList.vue` 最终回复块之后挂载新组件 `FileChangeSummaryCard.vue`，diff 正文与撤销通过两个新 HTTP 端点懒加载/执行。

**Tech Stack:** Python 3.10+ / FastAPI（后端）、Vue 3 + Vuetify 3 + TypeScript（dashboard）、pytest + vitest、EditHistoryManager（现有备份引擎，`difflib` 计算净 diff）。

**Spec:** 本文档"背景与数据契约"一节（无独立 spec 文件，契约以本文为准）。

---

## Global Constraints

- Python 代码提交前运行 `ruff format .` && `ruff check .`（pre-commit 钩子会自动执行）。
- 所有注释、日志、docstring 用英文；docstring 使用 Google 格式（`Args:` / `Returns:` / `Raises:`）。
- 路径处理使用 `pathlib.Path`。
- 兼容 Python 3.10+，兼容 Windows / macOS / Linux（注意 `os.startfile` 已有平台分支的写法）。
- commit message 使用 conventional commits（`feat:` / `fix:` / `test:` / `chore:`）。
- 不新建报告类文件（`xxx_SUMMARY.md` 等）。
- KISS / Inline-First：不提取本计划之外的新 helper；每个新 helper 在计划中已给出 ≥3 调用点或复杂度理由。
- 后端路由/请求 schema 变更后必须同步 `openspec/openapi-v1.yaml` 并运行 `cd dashboard && pnpm generate:api` 重新生成前端 client。
- i18n 三个 locale 都要补键：`dashboard/src/i18n/locales/{zh-CN,en-US,ru-RU}/features/chat.json`。
- WebUI 对话框按钮使用 `variant="text"` 或 `variant="tonal"`；本方案用行内两步确认，不新建 dialog。

---

## 背景与数据契约（Spec）

### 现状（为什么不能纯前端做）

- 状态行"思考了x次，使用了y次工具，变更了z次文件"由前端 `useMessages.ts` 的 `reasoningActivityCounts()` 从 tool_call 消息推导，文件数 = 文件变更工具**调用次数**（不去重文件）。
- 每次 edit 工具结果里内嵌一段 unified diff，但**后端截断到 2000 字符**（`fs.py` `_format_result`），且同一文件多次编辑产生多段独立 diff；write 工具结果只有路径没有 diff。前端 `FileChangeCard.vue` 因此只能展示"每次调用"的卡片。
- 结论：**净 diff 必须由后端在轮次结束时对照基线现算**。基线用现有 `EditHistoryManager`（每次 edit 前自动备份 `.bak`）。

### 事件与持久化契约（模仿 `agent_stats` 全链路）

| 环节 | agent_stats 现状 | file_changes 新增 |
|---|---|---|
| runner 产出 | `AgentResponse(type="agent_stats", chain=MessageChain(type="agent_stats", chain=[Json(stats)]))` | `AgentResponse(type="file_changes", chain=MessageChain(type="file_changes", chain=[Json({"files": [...]})]))` |
| webchat 序列化 | `webchat_event.py` Json 分支自动透传为 `{type:"plain", chain_type:"agent_stats", data:"<json>"}`（无需改） | 同上自动透传（无需改） |
| chat_service 消费 | `_consume_chat_run` 按 `chain_type` 捕获→改写发布 `{type:"agent_stats", data:<obj>}`→持久化到 `content["agent_stats"]` | 镜像：捕获→改写发布 `{type:"file_changes", data:<obj>}`→持久化到 `content["file_changes"]` |
| 前端流分发 | `msgType === "agent_stats" \|\| chainType === "agent_stats"` → `ChatContent.agentStats` | `msgType === "file_changes" \|\| chainType === "file_changes"` → `ChatContent.fileChangeSummary: FileChangeSummaryFile[]` |
| 历史重载 | `normalizeHistoryRecord` 读 `content.agent_stats` | 读 `content.file_changes.files` |

### 数据契约

**`extra["changed_files"]` 记录条目**（fs.py 工具写入，按时间顺序 append）：

```python
{"path": str, "kind": "edit"|"write"|"created"|"rollback", "runtime": "local"|"sandbox", "backup_id": str, "ts": float}
```

- `backup_id` = 该次修改**之前**内容在 `EditHistoryManager` 中的备份 id（`created` 为 `""`，因为新建文件无前像）。
- 同一轮内某路径的**第一条**记录的 `backup_id` 即该文件的净 diff 基线。

**`file_changes` 事件 payload**（前端收到 `data.files` 数组，持久化为 `content["file_changes"] = {"files": [...]}`）：

```python
{
  "files": [
    {
      "path": str,              # 绝对路径
      "kind": "edit"|"write"|"created"|"rollback",
      "adds": int|None,         # 净新增行数；无法计算时 None
      "dels": int|None,         # 净删除行数；无法计算时 None
      "backup_id": str,         # 净 diff 基线备份 id；created 为 ""
      "sha256": str,            # 轮次结束时文件内容的 sha256 hex；不可用时 ""
      "runtime": "local"|"sandbox",
      "diff_available": bool,   # False = 沙箱/缺基线/读失败，只显示文件名
    }
  ]
}
```

**新 HTTP 端点**（均 `x-astrbot-scope: chat`，`ok()/error()` 信封，同 `/chat/open-file` 风格）：

1. `POST /api/v1/chat/file-changes/diff` — body `{path, backup_id, expect_sha256}` → `ok({path, diff, adds, dels, truncated})`。`backup_id` 非空：diff = 该备份 vs 当前文件；`backup_id` 为空（created 文件）：必须提供 `expect_sha256` 且与当前文件 hash 一致才计算（防止变成任意文件读取原语），diff = 空 vs 当前。
2. `POST /api/v1/chat/file-changes/restore` — body `{path, backup_id, expect_sha256}`。校验当前内容 sha256 与 `expect_sha256` 一致（不一致返回 conflict error，防止覆盖轮次之后的人工修改）；先把当前内容存为 pre-restore 备份（复用 `fs.py` rollback 的 `[pre-rollback snapshot]` 模式），再写回备份内容 → `ok({path, restored_to})`。

### v1 明确的非目标

- **中止（stop）的轮次不出卡片**：用户点停止后 `_finalize_aborted_step` 路径不发射事件（后续可加）。
- **沙箱 runtime 文件**：出现在卡片上但 `diff_available=false`，无 查看/撤销 按钮。
- **created 文件不可撤销**（撤销=删除文件太危险），只显示 `+N`。
- 状态行"变更了z次文件"语义保持不变（按调用次数计）。
- openapi WS / agent-collab 等旁路消费者只做"不损坏消息"的最小处理（转发事件、跳过 accumulator），不做持久化增强。

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `astrbot/core/tools/computer_tools/edit_history.py` | Modify | 新增 `count_unified_diff_changes` / `render_unified_diff` / `build_turn_change_summary`（备份感知的净 diff 计算，全部可注入 `base_dir` 测试） |
| `astrbot/core/tools/computer_tools/fs.py` | Modify | 工具成功修改文件后向 `extra["changed_files"]` 记录；write 工具补齐"覆盖前备份" |
| `astrbot/core/agent/runners/tool_loop_agent_runner.py` | Modify | DONE 收尾处组装并 yield `file_changes` 事件（两条收尾路径） |
| `astrbot/dashboard/services/chat_service.py` | Modify | `_consume_chat_run` 新 chain_type 分支、accumulator `pending_file_changes`、`save_bot_message`/`build_bot_history_content` 落库、系统流 flush 透传 |
| `astrbot/dashboard/services/open_api_service.py` | Modify | 同一 chain_type 的最小分支（防 JSON 字面量污染消息） |
| `astrbot/dashboard/schemas.py` | Modify | `ChatFileChangeDiffRequest` / `ChatFileChangeRestoreRequest` |
| `astrbot/dashboard/api/chat.py` | Modify | 两个新路由 |
| `openspec/openapi-v1.yaml` | Modify | 两个新 path + 两个 schema |
| `tests/unit/test_file_change_summary.py` | Create | 净 diff 汇总函数单测 |
| `tests/test_computer_fs_tools.py` | Modify | 工具记录行为单测 |
| `tests/unit/test_chat_service_file_changes.py` | Create | accumulator 分支单测 |
| `dashboard/src/utils/fileChangeTool.ts` | Modify | `FileChangeSummaryFile` 类型 + `parseFileChangesPayload` 纯函数 |
| `dashboard/src/utils/fileChangeTool.spec.ts` | Modify | 上述纯函数单测 |
| `dashboard/src/composables/useMessages.ts` | Modify | `ChatContent.fileChangeSummary`、流分发分支、`normalizeHistoryRecord` |
| `dashboard/src/api/v1.ts` | Modify | `chatApi.fileChangeDiff` / `chatApi.restoreFileChange` 门面 |
| `dashboard/src/components/chat/message_list_comps/FileChangeSummaryCard.vue` | Create | 总结卡片组件（查看/打开/撤销） |
| `dashboard/src/components/chat/ChatMessageList.vue` | Modify | 挂载卡片 |
| `dashboard/src/i18n/locales/{zh-CN,en-US,ru-RU}/features/chat.json` | Modify | `fileChanges` 文案块 |

---

### Task 1: edit_history.py — 净 diff 计算函数

**Files:**
- Modify: `astrbot/core/tools/computer_tools/edit_history.py`
- Test: `tests/unit/test_file_change_summary.py`（新建）

**Interfaces:**
- Consumes: 现有 `EditHistoryManager.read_backup(path, backup_id) -> tuple[BackupEntry, bytes]`、`get_history_manager()`。
- Produces（后续 Task 3/5 依赖，签名锁定）:
  - `count_unified_diff_changes(old_text: str, new_text: str) -> tuple[int, int]`
  - `render_unified_diff(old_bytes: bytes, new_bytes: bytes, path: str, max_chars: int = 200000) -> dict`，返回 `{"path": str, "diff": str, "adds": int, "dels": int, "truncated": bool}`
  - `async def build_turn_change_summary(entries: list[dict], *, since_ts: float = 0.0, history: EditHistoryManager | None = None) -> list[dict]`，返回"数据契约"中的 files 数组。（执行修订：新增可注入 `history` 参数——函数原用 `get_history_manager()` 单例，无法对隔离 `base_dir` 的测试生效；默认仍取单例，生产行为不变。）

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_file_change_summary.py`：

```python
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from astrbot.core.tools.computer_tools.edit_history import (
    EditHistoryManager,
    build_turn_change_summary,
    count_unified_diff_changes,
    render_unified_diff,
)


@pytest.fixture()
def history(tmp_path: Path) -> EditHistoryManager:
    return EditHistoryManager(base_dir=tmp_path / "history")


def test_count_unified_diff_changes():
    adds, dels = count_unified_diff_changes("a\nb\nc\n", "a\nB\nc\nd\n")
    assert (adds, dels) == (2, 1)


def test_render_unified_diff_truncates():
    old = "".join(f"line{i}\n" for i in range(10))
    new = "".join(f"line{i}\n" for i in range(20000))
    result = render_unified_diff(old.encode(), new.encode(), "x.txt", max_chars=500)
    assert result["truncated"] is True
    assert len(result["diff"]) <= 500
    assert result["adds"] > 0


def test_summary_edit_net_diff(tmp_path: Path, history: EditHistoryManager):
    target = tmp_path / "a.txt"
    target.write_text("v1\n", encoding="utf-8")
    entry = history.save_backup(str(target), target.read_bytes())
    target.write_text("v1\nv2\nv3\n", encoding="utf-8")

    summary = asyncio.run(
        build_turn_change_summary(
            [
                {
                    "path": str(target),
                    "kind": "edit",
                    "runtime": "local",
                    "backup_id": entry.id,
                    "ts": 1.0,
                }
            ]
        )
    )
    assert len(summary) == 1
    assert summary[0]["kind"] == "edit"
    assert summary[0]["adds"] == 2
    assert summary[0]["dels"] == 0
    assert summary[0]["diff_available"] is True
    assert summary[0]["backup_id"] == entry.id
    assert len(summary[0]["sha256"]) == 64


def test_summary_created_file(tmp_path: Path):
    target = tmp_path / "new.txt"
    target.write_text("hello\nworld\n", encoding="utf-8")
    summary = asyncio.run(
        build_turn_change_summary(
            [
                {
                    "path": str(target),
                    "kind": "created",
                    "runtime": "local",
                    "backup_id": "",
                    "ts": 1.0,
                }
            ]
        )
    )
    assert summary[0]["adds"] == 2
    assert summary[0]["dels"] == 0
    assert summary[0]["diff_available"] is True


def test_summary_multiple_edits_dedup_first_baseline(
    tmp_path: Path, history: EditHistoryManager
):
    target = tmp_path / "b.txt"
    target.write_text("one\n", encoding="utf-8")
    first = history.save_backup(str(target), target.read_bytes())
    target.write_text("one\ntwo\n", encoding="utf-8")
    history.save_backup(str(target), target.read_bytes())
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")

    summary = asyncio.run(
        build_turn_change_summary(
            [
                {"path": str(target), "kind": "edit", "runtime": "local",
                 "backup_id": first.id, "ts": 1.0},
                {"path": str(target), "kind": "edit", "runtime": "local",
                 "backup_id": "later", "ts": 2.0},
            ]
        )
    )
    # Net diff vs the FIRST backup of the turn: +2 lines.
    assert summary[0]["adds"] == 2
    assert summary[0]["backup_id"] == first.id


def test_summary_since_ts_filters_old_entries(tmp_path: Path):
    target = tmp_path / "c.txt"
    target.write_text("x\n", encoding="utf-8")
    summary = asyncio.run(
        build_turn_change_summary(
            [
                {"path": str(target), "kind": "created", "runtime": "local",
                 "backup_id": "", "ts": 1.0},
            ],
            since_ts=5.0,
        )
    )
    assert summary == []


def test_summary_sandbox_not_computable(tmp_path: Path, history: EditHistoryManager):
    target = tmp_path / "s.txt"
    target.write_text("v1\n", encoding="utf-8")
    entry = history.save_backup(str(target), target.read_bytes())
    target.write_text("v2\n", encoding="utf-8")
    summary = asyncio.run(
        build_turn_change_summary(
            [
                {"path": str(target), "kind": "edit", "runtime": "sandbox",
                 "backup_id": entry.id, "ts": 1.0},
            ]
        )
    )
    assert summary[0]["diff_available"] is False
    assert summary[0]["adds"] is None


def test_summary_missing_baseline_degrades(tmp_path: Path):
    target = tmp_path / "d.txt"
    target.write_text("v2\n", encoding="utf-8")
    summary = asyncio.run(
        build_turn_change_summary(
            [
                {"path": str(target), "kind": "edit", "runtime": "local",
                 "backup_id": "nonexistent", "ts": 1.0},
            ]
        )
    )
    assert summary[0]["diff_available"] is False
    assert summary[0]["adds"] is None
    assert len(summary[0]["sha256"]) == 64
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_file_change_summary.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_turn_change_summary'`

- [ ] **Step 3: 实现**

在 `astrbot/core/tools/computer_tools/edit_history.py` 顶部 import 区补 `asyncio` 与 `difflib`（`hashlib`、`Path` 已有）。在 `EditHistoryManager` 类定义之后、模块级 `_manager` 单例区之前追加：

```python
# ---------------------------------------------------------------------------
# Turn-level net diff computation (file change summary, 2026-09-13)
# ---------------------------------------------------------------------------


def count_unified_diff_changes(old_text: str, new_text: str) -> tuple[int, int]:
    """Count added/deleted lines between two text versions.

    Args:
        old_text: Baseline text (e.g. pre-turn backup content).
        new_text: Current text.

    Returns:
        Tuple of (added_lines, deleted_lines).
    """
    adds = 0
    dels = 0
    for line in difflib.unified_diff(
        old_text.splitlines(), new_text.splitlines(), lineterm=""
    ):
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            adds += 1
        elif line.startswith("-"):
            dels += 1
    return adds, dels


def render_unified_diff(
    old_bytes: bytes,
    new_bytes: bytes,
    path: str,
    max_chars: int = 200_000,
) -> dict[str, T.Any]:
    """Render a unified diff between two file versions.

    Args:
        old_bytes: Baseline file bytes.
        new_bytes: Current file bytes.
        path: Display path used in the diff headers.
        max_chars: Hard cap for the diff text; longer diffs are truncated
            and the ``truncated`` flag is set.

    Returns:
        Dict with keys ``path``, ``diff``, ``adds``, ``dels``, ``truncated``.
    """
    old_text = old_bytes.decode("utf-8", errors="replace")
    new_text = new_bytes.decode("utf-8", errors="replace")
    diff_text = "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{Path(path).name}",
            tofile=f"b/{Path(path).name}",
        )
    )
    truncated = False
    if len(diff_text) > max_chars:
        diff_text = diff_text[:max_chars]
        truncated = True
    adds, dels = count_unified_diff_changes(old_text, new_text)
    return {
        "path": path,
        "diff": diff_text,
        "adds": adds,
        "dels": dels,
        "truncated": truncated,
    }


async def build_turn_change_summary(
    entries: list[dict],
    *,
    since_ts: float = 0.0,
) -> list[dict]:
    """Aggregate per-file net changes recorded by the file tools in one turn.

    The first recorded entry of each path provides the baseline backup id,
    so multiple edits of the same file collapse into one net diff.

    Args:
        entries: ``changed_files`` entries recorded by the file tools, each
            ``{"path", "kind", "runtime", "backup_id", "ts"}``.
        since_ts: Only entries with ``ts >= since_ts`` are included. This
            scopes the summary to the current turn when the agent context
            outlives a single turn.

    Returns:
        One summary dict per touched path in first-touch order. Sandbox
        files and files whose baseline is unavailable get
        ``diff_available=False`` and ``None`` line counts.
    """
    by_path: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "")
        if not path or path in by_path:
            continue
        try:
            ts = float(entry.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        if since_ts and ts < since_ts:
            continue
        by_path[path] = entry

    history = get_history_manager()
    summaries: list[dict] = []
    for path, entry in by_path.items():
        runtime = str(entry.get("runtime") or "local")
        summary = {
            "path": path,
            "kind": str(entry.get("kind") or "edit"),
            "adds": None,
            "dels": None,
            "backup_id": str(entry.get("backup_id") or ""),
            "sha256": "",
            "runtime": runtime,
            "diff_available": False,
        }
        if runtime != "local":
            summaries.append(summary)
            continue
        try:
            current_bytes = await asyncio.to_thread(Path(path).read_bytes)
        except OSError:
            summaries.append(summary)
            continue
        summary["sha256"] = hashlib.sha256(current_bytes).hexdigest()
        if summary["kind"] == "created":
            current_text = current_bytes.decode("utf-8", errors="replace")
            summary["adds"] = len(current_text.splitlines())
            summary["dels"] = 0
            summary["diff_available"] = True
            summaries.append(summary)
            continue
        if not summary["backup_id"]:
            summaries.append(summary)
            continue
        try:
            _, baseline_bytes = await asyncio.to_thread(
                history.read_backup, path, summary["backup_id"]
            )
        except (ValueError, FileNotFoundError, OSError):
            summaries.append(summary)
            continue
        adds, dels = count_unified_diff_changes(
            baseline_bytes.decode("utf-8", errors="replace"),
            current_bytes.decode("utf-8", errors="replace"),
        )
        summary["adds"] = adds
        summary["dels"] = dels
        summary["diff_available"] = True
        summaries.append(summary)
    return summaries
```

注意：文件顶部 `typing` 已 `from typing import Any`？核查——现文件是 `from typing import Any`（是）。若 `T.Any` 写法不匹配则用 `Any`。`count_unified_diff_changes` 的返回注解用 `tuple[int, int]`（3.10+ OK，文件已有 `from __future__ import annotations`）。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_file_change_summary.py -v`
Expected: 7 PASS

- [ ] **Step 5: 格式检查 + 提交**

```bash
ruff format astrbot/core/tools/computer_tools/edit_history.py tests/unit/test_file_change_summary.py
ruff check astrbot/core/tools/computer_tools/edit_history.py tests/unit/test_file_change_summary.py
git add astrbot/core/tools/computer_tools/edit_history.py tests/unit/test_file_change_summary.py
git commit -m "feat(tools): add turn-level net diff computation to edit history"
```

---

### Task 2: fs.py — 工具记录 changed_files + write 覆盖前备份

**Files:**
- Modify: `astrbot/core/tools/computer_tools/fs.py`
- Test: `tests/test_computer_fs_tools.py`

**Interfaces:**
- Consumes: `get_history_manager()`（fs.py:80 已导入）、`EditHistoryManager.save_backup`。
- Produces: `AstrAgentContext.extra["changed_files"]` 列表条目 `{"path", "kind", "runtime", "backup_id", "ts"}`（Task 3 消费）；模块级 `_record_changed_file(context, path, kind, runtime, backup_id)`。

设计说明（为什么这样改）：
- 记录用 `getattr(context.context, "extra", None)` 防御式访问——现有测试的 `SimpleNamespace` 上下文没有 `extra` 字段，必须保持它们不炸（与 runner 中 `_agent_ctx` 的 getattr 风格一致）。
- write 工具此前**不做备份**：本任务给它补上"覆盖已存在文件前备份"，与 edit 工具对齐，使覆盖型 write 也有净 diff 基线与撤销能力；新建文件（`existed_before=False`）不备份，记 `kind="created"`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_computer_fs_tools.py` 末尾追加（复用文件里已有的 `_make_context` helper 与导入）：

```python
class TestChangedFilesRecording:
    """File tools must record touches into AstrAgentContext.extra."""

    @pytest.mark.asyncio
    async def test_write_new_file_records_created(self, tmp_path):
        target = tmp_path / "brand_new.txt"
        ctx = _make_context()
        ctx.context.extra = {}
        tool = fs_tools.FileWriteTool()
        result = await tool.call(
            context=ctx, path=str(target), content="hello\nworld\n"
        )
        assert "File written successfully" in result
        entries = ctx.context.extra["changed_files"]
        assert len(entries) == 1
        assert entries[0]["kind"] == "created"
        assert entries[0]["backup_id"] == ""
        assert Path(entries[0]["path"]) == target

    @pytest.mark.asyncio
    async def test_write_existing_file_saves_backup(self, tmp_path):
        target = tmp_path / "exists.txt"
        target.write_text("old\n", encoding="utf-8")
        ctx = _make_context()
        ctx.context.extra = {}
        tool = fs_tools.FileWriteTool()
        await tool.call(context=ctx, path=str(target), content="new\n")
        entries = ctx.context.extra["changed_files"]
        assert entries[0]["kind"] == "write"
        assert entries[0]["backup_id"] != ""
        # Backup holds the pre-write content.
        from astrbot.core.tools.computer_tools.edit_history import (
            get_history_manager,
        )

        _, baseline = get_history_manager().read_backup(
            str(target), entries[0]["backup_id"]
        )
        assert baseline == b"old\n"

    @pytest.mark.asyncio
    async def test_failed_write_records_nothing(self, tmp_path):
        tool = fs_tools.FileWriteTool()
        # READONLY mode short-circuits before any write.
        readonly_ctx = _make_context(file_access_default_mode="readonly")
        readonly_ctx.context.extra = {}
        await tool.call(
            context=readonly_ctx,
            path=str(tmp_path / "nope.txt"),
            content="x",
        )
        assert readonly_ctx.context.extra.get("changed_files", []) == []
```

注：若 `_make_context` 的 `file_access_default_mode="readonly"` 不足以让 `FileWriteTool.call` 短路（以 `fs_access.get_mode` 实际读取的配置键为准，执行时读 `fs.py:506` 与 `fs_access` 模块确认），改为 monkeypatch `fs_access.get_mode` 返回 `FileAccessMode.READONLY`。断言目标不变：失败路径不产生记录。

edit 工具的记录行为已由 Task 1 的"多份备份 + 基线"测试间接覆盖数据面；此处再补一条端到端记录测试：

```python
    @pytest.mark.asyncio
    async def test_edit_records_touch(self, tmp_path):
        target = tmp_path / "edit_me.txt"
        target.write_text("alpha\n", encoding="utf-8")
        ctx = _make_context()
        ctx.context.extra = {}
        tool = fs_tools.FileEditTool()
        result = await tool.call(
            context=ctx,
            path=str(target),
            old="alpha",
            new="beta",
        )
        assert "Edited" in result
        entries = ctx.context.extra["changed_files"]
        assert entries[0]["kind"] == "edit"
        assert entries[0]["backup_id"] != ""
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_computer_fs_tools.py::TestChangedFilesRecording -v`
Expected: FAIL — `KeyError: 'changed_files'`（工具尚未记录）

- [ ] **Step 3: 实现 fs.py**

3a. 模块级记录函数。放在 `_format_result`（fs.py:667）之前：

```python
def _record_changed_file(
    context: ContextWrapper[AstrAgentContext],
    path: str,
    kind: str,
    runtime: str,
    backup_id: str,
) -> None:
    """Record one successful file mutation for the turn summary.

    Entries land in ``AstrAgentContext.extra["changed_files"]``; the agent
    runner aggregates them into the end-of-turn ``file_changes`` event.
    Defensive ``getattr`` keeps lightweight test contexts (which lack
    ``extra``) working unchanged.

    Args:
        context: Tool execution context.
        path: Normalized absolute path that was modified.
        kind: One of ``edit`` / ``write`` / ``created`` / ``rollback``.
        runtime: ``local`` or ``sandbox``.
        backup_id: Id of the pre-change backup; empty when none exists.
    """
    extra = getattr(context.context, "extra", None)
    if not isinstance(extra, dict):
        return
    extra.setdefault("changed_files", []).append(
        {
            "path": path,
            "kind": kind,
            "runtime": runtime,
            "backup_id": backup_id,
            "ts": time.time(),
        }
    )
```

fs.py 顶部需补 `import time`（核查现有 import，缺则加）。

3b. `FileWriteTool.call`（fs.py:499-572）：在 `normalized_path` 判空之后、`sb = await get_booter(...)` 之前插入备份逻辑；成功返回前插入记录：

```python
            # Backup-before-write for existing local files so overwrite
            # writes get a net-diff baseline and undo, mirroring the edit
            # tool. New files get no backup; they are recorded as "created".
            backup_id = ""
            existed_before = False
            if local_env:
                existed_before = Path(normalized_path).exists()
                if existed_before:
                    try:
                        pre_bytes = await asyncio.to_thread(
                            _read_file_bytes, normalized_path
                        )
                        entry = await asyncio.to_thread(
                            get_history_manager().save_backup,
                            normalized_path,
                            pre_bytes,
                            runtime="local",
                        )
                        backup_id = entry.id
                    except Exception as exc:
                        logger.warning(
                            f"Failed to save pre-write backup for "
                            f"{normalized_path}: {exc}"
                        )
```

成功分支 `return f"File written successfully: ..."` 之前：

```python
            _record_changed_file(
                context,
                normalized_path,
                "created" if (local_env and not existed_before) else "write",
                "local" if local_env else "sandbox",
                backup_id,
            )
```

3c. `FileEditTool._do_edit`（fs.py:950-1057）：把 :1035-1041 的备份调用改为捕获返回值，并在 `write_fn` 成功后记录：

```python
            # 3. Save backup (only after successful validation, async)
            backup_entry = None
            try:
                backup_entry = await asyncio.to_thread(
                    history_mgr.save_backup,
                    normalized_path,
                    raw_bytes,
                    runtime="local" if local_env else "sandbox",
                )
            except Exception as exc:
                logger.warning(
                    f"Failed to save edit backup for {normalized_path}: {exc}"
                )

            # 4. Write the edited content
            try:
                await write_fn(write_bytes)
            except OSError as exc:
                return f"Error editing file: {exc}"

            _record_changed_file(
                context,
                normalized_path,
                "edit",
                "local" if local_env else "sandbox",
                backup_entry.id if backup_entry else "",
            )
```

3d. `FileEditTool._do_rollback`（fs.py:894 起）：把 :927-939 的 pre-rollback snapshot `save_backup` 调用改为捕获返回值（变量 `snapshot_entry`），在回滚写回成功、return 结果之前追加：

```python
            _record_changed_file(
                context,
                normalized_path,
                "rollback",
                "local" if local_env else "sandbox",
                snapshot_entry.id if snapshot_entry else "",
            )
```

（执行时读整个 `_do_rollback` 确认局部变量名与 `local_env` 在该作用域的可用性；若该函数内没有 `local_env` 变量，用 `runtime="local"` 的同一来源表达式替换。）

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_computer_fs_tools.py -v`
Expected: 新增 4 条 PASS，且该文件原有测试全部 PASS（若有原有用例因 write 行为变化失败——例如断言 write 不产生备份——按新行为修正断言并在 commit message 里注明）

- [ ] **Step 5: 格式检查 + 提交**

```bash
ruff format astrbot/core/tools/computer_tools/fs.py tests/test_computer_fs_tools.py
ruff check astrbot/core/tools/computer_tools/fs.py tests/test_computer_fs_tools.py
git add astrbot/core/tools/computer_tools/fs.py tests/test_computer_fs_tools.py
git commit -m "feat(tools): record file tool mutations for turn change summary"
```

---

### Task 3: runner — DONE 收尾发射 `file_changes` 事件

**Files:**
- Modify: `astrbot/core/agent/runners/tool_loop_agent_runner.py`
- Test: `tests/unit/test_runner_file_changes.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `extra["changed_files"]` 条目、Task 1 的 `build_turn_change_summary(entries, since_ts=...)`。
- Produces: `AgentResponse(type="file_changes", data={"chain": MessageChain(type="file_changes", chain=[Json({"files": [...]})])})`——Task 4 的 chat_service 按此 `chain_type` 消费。

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_runner_file_changes.py`：

```python
from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner


def _runner_with_entries(entries: list[dict]) -> ToolLoopAgentRunner:
    runner = ToolLoopAgentRunner.__new__(ToolLoopAgentRunner)
    runner.run_context = SimpleNamespace(
        context=SimpleNamespace(extra={"changed_files": entries})
    )
    runner.stats = SimpleNamespace(start_time=100.0)
    return runner


@pytest.mark.asyncio
async def test_builds_response_from_changed_files(monkeypatch):
    files = [
        {"path": "a.txt", "kind": "edit", "adds": 1, "dels": 0,
         "backup_id": "b1", "sha256": "x", "runtime": "local",
         "diff_available": True}
    ]

    async def fake_summary(entries, *, since_ts=0.0):
        assert since_ts == 100.0
        return files

    monkeypatch.setattr(
        "astrbot.core.tools.computer_tools.edit_history."
        "build_turn_change_summary",
        fake_summary,
    )
    runner = _runner_with_entries([{"path": "a.txt", "ts": 101.0}])
    resp = await runner._build_file_changes_response()
    assert resp is not None
    assert resp.type == "file_changes"
    comp = resp.data["chain"].chain[0]
    assert comp.data == {"files": files}


@pytest.mark.asyncio
async def test_no_entries_returns_none():
    runner = _runner_with_entries([])
    assert await runner._build_file_changes_response() is None


@pytest.mark.asyncio
async def test_summary_failure_returns_none(monkeypatch):
    async def boom(entries, *, since_ts=0.0):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "astrbot.core.tools.computer_tools.edit_history."
        "build_turn_change_summary",
        boom,
    )
    runner = _runner_with_entries([{"path": "a.txt", "ts": 101.0}])
    assert await runner._build_file_changes_response() is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_runner_file_changes.py -v`
Expected: FAIL — `AttributeError: ... _build_file_changes_response`

- [ ] **Step 3: 实现 runner**

3a. 在 `ToolLoopAgentRunner` 类中、`_complete_with_assistant_response`（:183）之后新增方法：

```python
    async def _build_file_changes_response(self) -> AgentResponse | None:
        """Build the end-of-turn file change summary event.

        Reads the entries that file tools appended to
        ``AstrAgentContext.extra["changed_files"]`` during this run and
        aggregates them into a per-file net diff via the edit-history
        backup engine. Imported lazily: the runner is agent-generic
        machinery and must not import computer tools at module load.

        Returns:
            The ``file_changes`` AgentResponse, or None when the run
            touched no files or summary computation failed.
        """
        agent_ctx = getattr(self.run_context, "context", None)
        extra = getattr(agent_ctx, "extra", None) or {}
        entries = extra.get("changed_files") or []
        if not entries:
            return None
        try:
            from astrbot.core.tools.computer_tools.edit_history import (
                build_turn_change_summary,
            )

            files = await build_turn_change_summary(
                entries, since_ts=self.stats.start_time
            )
        except Exception:
            logger.warning("Failed to build file change summary", exc_info=True)
            return None
        if not files:
            return None
        return AgentResponse(
            type="file_changes",
            data=AgentResponseData(
                chain=MessageChain(
                    type="file_changes",
                    chain=[Json(data={"files": files})],
                )
            ),
        )
```

3b. DONE 路径一（普通收尾）：`step()` 中最终文本 yield 结束后、`# 如果有工具调用` 注释（:996）之前插入。锚点：`yield AgentResponse(type="llm_result", ... MessageChain().message(llm_resp.completion_text))` 块（:988-994）结束之后：

```python
        if not llm_resp.tools_call_name:
            file_changes_resp = await self._build_file_changes_response()
            if file_changes_resp:
                yield file_changes_resp
```

3c. DONE 路径二（skills_like re-query 收尾）：:1030 `await self._complete_with_assistant_response(llm_resp)` 与 `return` 之间插入：

```python
                    await self._complete_with_assistant_response(llm_resp)
                    file_changes_resp = await self._build_file_changes_response()
                    if file_changes_resp:
                        yield file_changes_resp
                    return
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_runner_file_changes.py -v`
Expected: 3 PASS

- [ ] **Step 5: 回归 + 提交**

Run: `uv run pytest tests/agent tests/unit -q`（确认 runner 相关存量测试不受影响）

```bash
git add astrbot/core/agent/runners/tool_loop_agent_runner.py tests/unit/test_runner_file_changes.py
git commit -m "feat(agent): emit file_changes event at end of agent loop"
```

---

### Task 4: chat_service / open_api_service — 消费、改写发布与持久化

**Files:**
- Modify: `astrbot/dashboard/services/chat_service.py`
- Modify: `astrbot/dashboard/services/open_api_service.py`
- Test: `tests/unit/test_chat_service_file_changes.py`（新建）

**Interfaces:**
- Consumes: Task 3 事件经 webchat 序列化后的两种形态：原始 `{type:"plain", chain_type:"file_changes", data:"<json string>"}`（系统流镜像）与改写后 `{type:"file_changes", data:<obj>}`（主路径，Task 4 自己发布）。
- Produces:
  - SSE/WS 顶层事件 `{type: "file_changes", data: {"files": [...]}}`（Task 6 前端消费）。
  - 历史记录 `content["file_changes"] = {"files": [...]}`（`build_bot_history_content`）。
  - `BotMessageAccumulator.pending_file_changes: dict`。

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_chat_service_file_changes.py`（accumulator 可直接实例化，参照 `tests/unit/test_chat_service_interactive_choice.py` 的构造方式；若该文件从别的模块导入 accumulator，跟随其导入路径）：

```python
from __future__ import annotations

import json

from astrbot.dashboard.services.chat_service import BotMessageAccumulator


def test_file_changes_captured_not_text():
    acc = BotMessageAccumulator()
    acc.add_plain(
        json.dumps({"files": [{"path": "a.txt", "kind": "edit"}]}),
        chain_type="file_changes",
        streaming=False,
    )
    assert acc.pending_file_changes["files"][0]["path"] == "a.txt"
    # Must NOT fall through to a literal JSON text part.
    assert acc.parts == []
    assert acc.has_content() is True


def test_file_changes_malformed_dropped():
    acc = BotMessageAccumulator()
    acc.add_plain("not-json", chain_type="file_changes", streaming=False)
    assert acc.pending_file_changes == {}
    assert acc.parts == []


def test_file_changes_flushed_via_build_parts():
    acc = BotMessageAccumulator()
    acc.add_plain("final answer", chain_type=None, streaming=False)
    acc.add_plain(
        json.dumps({"files": [{"path": "a.txt", "kind": "edit"}]}),
        chain_type="file_changes",
        streaming=False,
    )
    parts = acc.build_message_parts(include_pending_tool_calls=True)
    # The persisted parts must contain the plain text but no JSON blob part.
    plain = [p for p in parts if p.get("type") == "plain"]
    assert [p.get("text") for p in plain] == ["final answer"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_chat_service_file_changes.py -v`
Expected: FAIL — `AttributeError: ... pending_file_changes`；且 `has_content` 为 False

- [ ] **Step 3: 实现 chat_service.py**

3a. `BotMessageAccumulator.__init__`（:324-361）：`self.pending_agent_stats: dict = {}` 之后加：

```python
        # 2026-09-13 file change summary: system-stream path routes
        # `chain_type="file_changes"` payloads through `add_plain`, so the
        # accumulator needs its own slot (mirrors `pending_agent_stats`).
        self.pending_file_changes: dict = {}
```

3b. `has_content()`（:363-379）：OR 链加 `or self.pending_file_changes`。

3c. `add_plain`（:381-448）：`agent_stats` 分支（:432-443）之后加：

```python
        # 2026-09-13 file change summary: same rationale as the
        # `agent_stats` branch — parse the JSON blob into a dict instead of
        # letting it fall through as a literal-JSON plain text part.
        if chain_type == "file_changes":
            try:
                self.pending_file_changes = (
                    json.loads(result_text) if result_text else {}
                )
            except (TypeError, json.JSONDecodeError):
                self.pending_file_changes = {}
            return
```

3d. `build_bot_history_content`（:304-320）：签名加 `file_changes: dict | None = None`，函数体 `if agent_stats:` 之后加：

```python
    if file_changes:
        content["file_changes"] = file_changes
```

3e. `save_bot_message`（:1241-1259）：签名加 `file_changes: dict | None = None`（放在 `agent_stats: dict,` 之后），并把 `file_changes=file_changes` 传入 `build_bot_history_content`。

3f. `_consume_chat_run`（:1505-1551）：
- `pending_agent_stats = {}`（:1513）之后加 `pending_file_changes = {}`。
- `flush_pending_bot_message` 的 `nonlocal` 行加 `pending_file_changes`；条件（:1518-1521）加 `or pending_file_changes`；`save_bot_message(...)` 调用（:1540-1547）加实参 `file_changes=pending_file_changes`（注意该调用当前是位置参数，`agent_stats`/`refs` 之后都是带名的——保持既有样式）；flush 末尾重置 `pending_file_changes = {}`。
- `agent_stats` 分支（:1568-1578）之后加：

```python
                if chain_type == "file_changes":
                    try:
                        run.file_changes = json.loads(result_text)
                    except (TypeError, json.JSONDecodeError):
                        run.file_changes = {}
                    pending_file_changes = run.file_changes
                    self._publish_chat_run(
                        run,
                        {"type": "file_changes", "data": run.file_changes},
                    )
                    continue
```

- `msg_type == "end"` 的 `should_save` 条件（:1645-1649）加 `or pending_file_changes`。

3g. `ChatRunState`（:1002 附近）：`agent_stats: dict = field(default_factory=dict)` 之后加：

```python
    file_changes: dict = field(default_factory=dict)
```

3h. 系统流 flush（`build_system_stream` 内 `flush`，:1786-1793）：`save_bot_message` 调用加实参 `file_changes=acc.pending_file_changes`。

3i. 同文件其余 `save_bot_message(` 调用点（用 `grep -n "save_bot_message(" astrbot/dashboard/services/chat_service.py` 全量列出，已知 :1286、:1384 附近传 `agent_stats=deepcopy(run.agent_stats)`）：凡属于持久化 agent 回复的调用点，同样传 `file_changes=deepcopy(run.file_changes)`；与 `_consume_chat_run` 保持一致。

- [ ] **Step 4: 实现 open_api_service.py（最小分支）**

`agent_stats` 分支（:478-489）之后加（转发事件但绝不进 accumulator，防止 JSON 字面量变成 plain 文本 part；持久化与主路径对齐）：

```python
                if chain_type == "file_changes":
                    try:
                        file_changes = json.loads(result_text)
                    except Exception:
                        file_changes = {}
                    await send_json(
                        {"type": "file_changes", "data": file_changes}
                    )
                    continue
```

同函数内：初始化区（:458 `agent_stats = {}` 旁）加 `file_changes = {}`；`should_save`（end 分支，:508-510）加 `or file_changes`；`chat_bridge.save_bot_message(...)`（:530-536）加 `file_changes=file_changes`；循环尾部重置区（:553）加 `file_changes = {}`。

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_chat_service_file_changes.py tests/unit/test_chat_service_interactive_choice.py tests/test_chat_system_stream.py -v`
Expected: 全部 PASS（存量系统流测试不受影响）

- [ ] **Step 6: 格式检查 + 提交**

```bash
ruff format astrbot/dashboard/services/chat_service.py astrbot/dashboard/services/open_api_service.py tests/unit/test_chat_service_file_changes.py
ruff check astrbot/dashboard/services/chat_service.py astrbot/dashboard/services/open_api_service.py tests/unit/test_chat_service_file_changes.py
git add astrbot/dashboard/services/chat_service.py astrbot/dashboard/services/open_api_service.py tests/unit/test_chat_service_file_changes.py
git commit -m "feat(dashboard): persist and fan out file_changes chat event"
```

---

### Task 5: HTTP 端点 + OpenAPI + 前端 client 再生成

**Files:**
- Modify: `astrbot/dashboard/schemas.py`（`ChatOpenFileRequest` 附近，:140）
- Modify: `astrbot/dashboard/api/chat.py`
- Modify: `openspec/openapi-v1.yaml`
- Regenerate: `dashboard/src/api/generated/openapi-v1/`（`pnpm generate:api` 产物）
- Test: `tests/unit/test_chat_file_change_routes.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `render_unified_diff`、`EditHistoryManager`。
- Produces（Task 7 前端门面依赖）:
  - `POST /api/v1/chat/file-changes/diff` → `ok({"path", "diff", "adds", "dels", "truncated"})`
  - `POST /api/v1/chat/file-changes/restore` → `ok({"path", "restored_to"})`
  - 生成client方法名 `openApiV1.chatFileChangeDiff` / `openApiV1.chatFileChangeRestore`（由 operationId 派生）。

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_chat_file_change_routes.py`。先读 `tests/test_announcement_route.py` 学习本仓库路由测试的 app/client 构造方式，沿用其 fixture 风格。核心用例（伪代码骨架按仓库既有 fixture 落地；若仓库路由测试统一用 `httpx.AsyncClient(transport=ASGITransport(app))` 模式则直接套用）：

```python
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from astrbot.core.tools.computer_tools.edit_history import EditHistoryManager


@pytest.fixture()
def seeded(tmp_path: Path) -> tuple[Path, EditHistoryManager, str]:
    target = tmp_path / "a.txt"
    target.write_text("v1\n", encoding="utf-8")
    history = EditHistoryManager(base_dir=tmp_path / "hist")
    entry = history.save_backup(str(target), b"v1\n")
    target.write_text("v1\nv2\n", encoding="utf-8")
    return target, history, entry.id


# Route handlers are thin; exercise them via the function objects with a
# fake request-free signature (they take payload + auth Depends).
@pytest.mark.asyncio
async def test_diff_route(seeded, monkeypatch):
    from astrbot.dashboard.api import chat as chat_api

    target, history, backup_id = seeded
    monkeypatch.setattr(
        chat_api, "get_history_manager", lambda: history
    )
    payload = chat_api.ChatFileChangeDiffRequest(
        path=str(target), backup_id=backup_id
    )
    resp = await chat_api.chat_file_change_diff(payload, None)
    assert resp["status"] == "ok"
    data = resp["data"]
    assert data["adds"] == 1 and data["dels"] == 0
    assert "-v1" in data["diff"] or "+v2" in data["diff"]
    assert data["truncated"] is False


@pytest.mark.asyncio
async def test_diff_route_created_requires_sha(seeded, monkeypatch):
    from astrbot.dashboard.api import chat as chat_api

    target, history, _ = seeded
    monkeypatch.setattr(chat_api, "get_history_manager", lambda: history)
    payload = chat_api.ChatFileChangeDiffRequest(path=str(target), backup_id="")
    resp = await chat_api.chat_file_change_diff(payload, None)
    assert resp["status"] == "error"


@pytest.mark.asyncio
async def test_restore_route_conflict(seeded, monkeypatch):
    from astrbot.dashboard.api import chat as chat_api

    target, history, backup_id = seeded
    monkeypatch.setattr(chat_api, "get_history_manager", lambda: history)
    payload = chat_api.ChatFileChangeRestoreRequest(
        path=str(target), backup_id=backup_id, expect_sha256="deadbeef"
    )
    resp = await chat_api.chat_file_change_restore(payload, None)
    assert resp["status"] == "error"
    # File untouched on conflict.
    assert target.read_text(encoding="utf-8") == "v1\nv2\n"


@pytest.mark.asyncio
async def test_restore_route_ok(seeded, monkeypatch):
    from astrbot.dashboard.api import chat as chat_api

    target, history, backup_id = seeded
    monkeypatch.setattr(chat_api, "get_history_manager", lambda: history)
    current = target.read_bytes()
    payload = chat_api.ChatFileChangeRestoreRequest(
        path=str(target),
        backup_id=backup_id,
        expect_sha256=hashlib.sha256(current).hexdigest(),
    )
    resp = await chat_api.chat_file_change_restore(payload, None)
    assert resp["status"] == "ok"
    assert target.read_bytes() == b"v1\n"
```

注：`ok()`/`error()` 的返回形态以 `astrbot/dashboard/responses.py` 实际结构为准（可能是 pydantic model 而非 dict）——执行时先读该文件，用其真实字段名（如 `resp.status` / `resp.data`）改写断言。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_chat_file_change_routes.py -v`
Expected: FAIL — `cannot import name 'ChatFileChangeDiffRequest'`

- [ ] **Step 3: 实现 schemas**

`astrbot/dashboard/schemas.py`，`ChatOpenFileRequest`（:140-143）之后：

```python
class ChatFileChangeDiffRequest(OpenModel):
    """Request the net diff for one file changed during an agent turn."""

    path: str
    backup_id: str = ""
    expect_sha256: str = ""


class ChatFileChangeRestoreRequest(OpenModel):
    """Request to undo one file changed during an agent turn."""

    path: str
    backup_id: str
    expect_sha256: str = ""
```

（若 `OpenModel` 对带默认值字段有额外要求，跟随 `ChatOpenFileRequest` 的既有写法调整。）

- [ ] **Step 4: 实现路由**

`astrbot/dashboard/api/chat.py`。顶部补 import：`import hashlib`；`from astrbot.core.tools.computer_tools.edit_history import get_history_manager, render_unified_diff`；schemas import 区（:20-30）加两个新 Request 类。`open_chat_local_folder`（:467）之后追加：

```python
@router.post("/chat/file-changes/diff")
async def chat_file_change_diff(
    payload: ChatFileChangeDiffRequest,
    _auth: AuthContext = Depends(require_chat_scope),
):
    """Unified diff between a turn's baseline backup and the current file.

    Backs the ChatUI end-of-turn file change summary card. When
    ``backup_id`` is empty (files created during the turn), the caller must
    prove knowledge of the content hash recorded at the end of the run —
    otherwise this endpoint would be an arbitrary file-read primitive.
    """
    raw_path = payload.path.strip()
    if not raw_path:
        return error("Missing file path")
    target = Path(raw_path)
    if not target.is_file():
        return error(f"File not found: {raw_path}")
    history = get_history_manager()
    if payload.backup_id:
        try:
            _, baseline_bytes = await asyncio.to_thread(
                history.read_backup, raw_path, payload.backup_id
            )
        except (ValueError, FileNotFoundError) as exc:
            return error(str(exc))
        except OSError as exc:
            return error(f"Failed to read backup: {exc}")
    else:
        if not payload.expect_sha256:
            return error("expect_sha256 is required when backup_id is empty")
        try:
            probe = await asyncio.to_thread(target.read_bytes)
        except OSError as exc:
            return error(f"Failed to read file: {exc}")
        if hashlib.sha256(probe).hexdigest() != payload.expect_sha256:
            return error("File content does not match the recorded checksum")
        baseline_bytes = b""
    try:
        current_bytes = await asyncio.to_thread(target.read_bytes)
    except OSError as exc:
        return error(f"Failed to read file: {exc}")
    return ok(render_unified_diff(baseline_bytes, current_bytes, raw_path))


@router.post("/chat/file-changes/restore")
async def chat_file_change_restore(
    payload: ChatFileChangeRestoreRequest,
    _auth: AuthContext = Depends(require_chat_scope),
):
    """Restore a file to its pre-turn baseline backup (undo one file).

    Mirrors the tool-level rollback safety: the current content is saved as
    a pre-restore snapshot before overwriting, and a checksum mismatch
    (file changed after the agent run) aborts the restore.
    """
    raw_path = payload.path.strip()
    if not raw_path:
        return error("Missing file path")
    target = Path(raw_path)
    if not target.is_file():
        return error(f"File not found: {raw_path}")
    history = get_history_manager()
    try:
        _, baseline_bytes = await asyncio.to_thread(
            history.read_backup, raw_path, payload.backup_id
        )
    except (ValueError, FileNotFoundError) as exc:
        return error(str(exc))
    except OSError as exc:
        return error(f"Failed to read backup: {exc}")
    try:
        current_bytes = await asyncio.to_thread(target.read_bytes)
    except OSError as exc:
        return error(f"Failed to read file: {exc}")
    if payload.expect_sha256 and (
        hashlib.sha256(current_bytes).hexdigest() != payload.expect_sha256
    ):
        return error(
            "File has been modified since the agent run; restore aborted."
        )
    try:
        await asyncio.to_thread(
            history.save_backup,
            raw_path,
            current_bytes,
            runtime="local",
            diff_preview="[pre-restore snapshot]",
        )
        await asyncio.to_thread(target.write_bytes, baseline_bytes)
    except OSError as exc:
        return error(f"Failed to restore file: {exc}")
    return ok({"path": raw_path, "restored_to": payload.backup_id})
```

核查点：`require_chat_scope` 是否在该文件的既有 import/用法中存在（`open_chat_local_file` 用的是 `require_chat_scope`——若实际 import 名不同，与文件顶部一致）。

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_chat_file_change_routes.py -v`
Expected: 4 PASS

- [ ] **Step 6: 更新 OpenAPI yaml 并再生成 client**

`openspec/openapi-v1.yaml`：在 `/api/v1/chat/open-folder` 块（约 :1538-1551）之后插入两个 path（缩进对齐既有 path 键）：

```yaml
  /api/v1/chat/file-changes/diff:
    post:
      tags: [Chat]
      summary: Unified diff between a file-change baseline backup and the current file
      operationId: chatFileChangeDiff
      x-astrbot-scope: chat
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/ChatFileChangeDiffRequest"
      responses:
        "200":
          $ref: "#/components/responses/Ok"

  /api/v1/chat/file-changes/restore:
    post:
      tags: [Chat]
      summary: Restore a file to its pre-turn baseline backup
      operationId: chatFileChangeRestore
      x-astrbot-scope: chat
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/ChatFileChangeRestoreRequest"
      responses:
        "200":
          $ref: "#/components/responses/Ok"
```

在 `ChatOpenFileRequest` schema（约 :6194-6200）之后插入：

```yaml
    ChatFileChangeDiffRequest:
      type: object
      required: [path]
      properties:
        path:
          type: string
        backup_id:
          type: string
        expect_sha256:
          type: string
      additionalProperties: false

    ChatFileChangeRestoreRequest:
      type: object
      required: [path, backup_id]
      properties:
        path:
          type: string
        backup_id:
          type: string
        expect_sha256:
          type: string
      additionalProperties: false
```

再生成前端 client：

```bash
cd dashboard && pnpm generate:api
```

验证：`grep -n "chatFileChangeDiff\|chatFileChangeRestore" dashboard/src/api/generated/openapi-v1/sdk.gen.ts` 有结果。

- [ ] **Step 7: 提交**

```bash
ruff format astrbot/dashboard/schemas.py astrbot/dashboard/api/chat.py tests/unit/test_chat_file_change_routes.py && ruff check astrbot/dashboard/schemas.py astrbot/dashboard/api/chat.py tests/unit/test_chat_file_change_routes.py
git add astrbot/dashboard/schemas.py astrbot/dashboard/api/chat.py openspec/openapi-v1.yaml dashboard/src/api/generated/openapi-v1
git commit -m "feat(dashboard): add file-changes diff and restore endpoints"
```

---

### Task 6: 前端 — 类型、payload 解析、流分发与历史重载

**Files:**
- Modify: `dashboard/src/utils/fileChangeTool.ts`
- Modify: `dashboard/src/utils/fileChangeTool.spec.ts`
- Modify: `dashboard/src/composables/useMessages.ts`

**Interfaces:**
- Consumes: Task 4 的顶层事件 `{type:"file_changes", data:{files:[...]}}`（data 也可能是 JSON 字符串——原始链路形态），历史 `content.file_changes`。
- Produces（Task 7 组件依赖）:
  - `interface FileChangeSummaryFile { path: string; kind: "edit"|"write"|"created"|"rollback"; adds: number|null; dels: number|null; backup_id: string; sha256: string; runtime: string; diff_available: boolean }`
  - `parseFileChangesPayload(data: unknown): FileChangeSummaryFile[]`
  - `ChatContent.fileChangeSummary?: FileChangeSummaryFile[]`

- [ ] **Step 1: 写失败测试**

在 `dashboard/src/utils/fileChangeTool.spec.ts` 末尾追加：

```typescript
import { parseFileChangesPayload } from "./fileChangeTool";

describe("parseFileChangesPayload", () => {
  it("parses object payloads and returns files", () => {
    const files = parseFileChangesPayload({
      files: [{ path: "/x/a.py", kind: "edit", adds: 3, dels: 1 }],
    });
    expect(files).toHaveLength(1);
    expect(files[0].path).toBe("/x/a.py");
  });

  it("parses JSON string payloads (raw chain_type form)", () => {
    const files = parseFileChangesPayload(
      JSON.stringify({ files: [{ path: "/x/b.py", kind: "created" }] }),
    );
    expect(files[0].kind).toBe("created");
  });

  it("drops entries without a path and non-list payloads", () => {
    expect(parseFileChangesPayload({ files: [{ kind: "edit" }] })).toEqual([]);
    expect(parseFileChangesPayload("garbage")).toEqual([]);
    expect(parseFileChangesPayload(null)).toEqual([]);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd dashboard && pnpm test -- fileChangeTool`
Expected: FAIL — `parseFileChangesPayload` 未导出

- [ ] **Step 3: 实现 fileChangeTool.ts**

在文件末尾（`collectFileChanges` 之后）追加：

```typescript
/** One aggregated file entry from the backend end-of-turn
 * `file_changes` event (net diff vs the turn's baseline backup). */
export interface FileChangeSummaryFile {
  path: string;
  kind: "edit" | "write" | "created" | "rollback";
  adds: number | null;
  dels: number | null;
  backup_id: string;
  sha256: string;
  runtime: string;
  diff_available: boolean;
}

/** Parse the `data` field of a `file_changes` event (object or raw JSON
 * string) into a validated file list. */
export function parseFileChangesPayload(data: unknown): FileChangeSummaryFile[] {
  let parsed: unknown = data;
  if (typeof parsed === "string") {
    try {
      parsed = JSON.parse(parsed);
    } catch {
      return [];
    }
  }
  const files = (parsed as { files?: unknown } | null)?.files;
  if (!Array.isArray(files)) return [];
  return files.filter(
    (f): f is FileChangeSummaryFile =>
      !!f && typeof f === "object" && typeof (f as FileChangeSummaryFile).path === "string",
  );
}
```

- [ ] **Step 4: 实现 useMessages.ts**

4a. `ChatContent`（:178-186，`agentStats` 字段在 :183）加字段：

```typescript
  fileChangeSummary?: FileChangeSummaryFile[];
```

文件顶部 import 区从 `@/utils/fileChangeTool` 的既有 import 中补 `parseFileChangesPayload` 与 `FileChangeSummaryFile`（核查该文件已有的 fileChangeTool import 行，合并进去）。

4b. `processStreamPayload`：`agent_stats` 分支（:1780-1784）之后加：

```typescript
    if (msgType === "file_changes" || chainType === "file_changes") {
      markMessageStarted(botRecord);
      const files = parseFileChangesPayload(data);
      if (files.length) {
        messageContent(botRecord).fileChangeSummary = files;
      }
      return;
    }
```

4c. `normalizeHistoryRecord`（函数起于 :1138，`agentStats:` 行在 :1154）：`agentStats:` 行之后加：

```typescript
      fileChangeSummary:
        (content.fileChangeSummary as FileChangeSummaryFile[] | undefined) ||
        (content.file_changes?.files as FileChangeSummaryFile[] | undefined),
```

（`content` 在该函数里是 `any`，直接取即可；历史持久化形态为 `content.file_changes = {files: [...]}`。）

- [ ] **Step 5: 运行测试确认通过**

Run: `cd dashboard && pnpm test -- fileChangeTool && pnpm typecheck`
Expected: 测试 PASS；typecheck 无错误

- [ ] **Step 6: 提交**

```bash
git add dashboard/src/utils/fileChangeTool.ts dashboard/src/utils/fileChangeTool.spec.ts dashboard/src/composables/useMessages.ts
git commit -m "feat(chatui): handle file_changes stream event and history reload"
```

---

### Task 7: 前端 — API 门面、总结卡片组件、i18n、挂载

**Files:**
- Modify: `dashboard/src/api/v1.ts`
- Create: `dashboard/src/components/chat/message_list_comps/FileChangeSummaryCard.vue`
- Modify: `dashboard/src/components/chat/ChatMessageList.vue`
- Modify: `dashboard/src/i18n/locales/zh-CN/features/chat.json`
- Modify: `dashboard/src/i18n/locales/en-US/features/chat.json`
- Modify: `dashboard/src/i18n/locales/ru-RU/features/chat.json`

**Interfaces:**
- Consumes: Task 5 的 `chatFileChangeDiff` / `chatFileChangeRestore`；Task 6 的 `FileChangeSummaryFile`、`ChatContent.fileChangeSummary`；现有 `DiffPreview.vue`（props: `content/filePath/summary/maxLines/collapsible/isDark`）、`useOpenOnDisk("fileChange")`（i18n 前缀 `fileChange` 已有 opened/openFailed 等键）。
- Produces: `<FileChangeSummaryCard :files="..." :is-dark="..." />`。

- [ ] **Step 1: chatApi 门面（v1.ts）**

在 `openLocalFolder`（约 :914-916）之后加：

```typescript
  fileChangeDiff(path: string, backupId: string, expectSha256: string) {
    return typed<any>(
      openApiV1.chatFileChangeDiff({
        body: {
          path,
          backup_id: backupId,
          expect_sha256: expectSha256,
        },
      }),
    );
  },
  restoreFileChange(path: string, backupId: string, expectSha256: string) {
    return typed<any>(
      openApiV1.chatFileChangeRestore({
        body: {
          path,
          backup_id: backupId,
          expect_sha256: expectSha256,
        },
      }),
    );
  },
```

（生成字段名若与 client 类型不符——以 `types.gen.ts` 里 `ChatFileChangeDiffRequest` 的实际属性名为准。）

- [ ] **Step 2: i18n 文案**

三个 locale 的 `features/chat.json` 中 `"fileChange": {...}` 块之后加 `"fileChanges"` 块（注意与现有 `fileChange` 块区分，多一个 s）。

zh-CN：

```json
  "fileChanges": {
    "title": "{count} 个文件已更改",
    "sandboxHint": "沙箱文件，暂不支持查看与撤销",
    "noStat": "无统计",
    "viewDiff": "查看 diff",
    "undo": "撤销",
    "undoConfirm": "再次点击确认撤销",
    "undoDone": "已撤销对 {name} 的更改",
    "undoFailed": "撤销失败：{message}",
    "diffLoadFailed": "加载 diff 失败：{message}",
    "diffTruncated": "diff 过大，仅显示部分内容",
    "restoring": "撤销中…"
  },
```

en-US：

```json
  "fileChanges": {
    "title": "{count} files changed",
    "sandboxHint": "Sandbox file; diff and undo are unavailable",
    "noStat": "no stats",
    "viewDiff": "View diff",
    "undo": "Undo",
    "undoConfirm": "Click again to confirm",
    "undoDone": "Reverted {name}",
    "undoFailed": "Undo failed: {message}",
    "diffLoadFailed": "Failed to load diff: {message}",
    "diffTruncated": "Diff too large; showing partial content",
    "restoring": "Undoing…"
  },
```

ru-RU：

```json
  "fileChanges": {
    "title": "Изменено файлов: {count}",
    "sandboxHint": "Файл в песочнице: просмотр и отмена недоступны",
    "noStat": "нет статистики",
    "viewDiff": "Открыть diff",
    "undo": "Отменить",
    "undoConfirm": "Нажмите ещё раз для подтверждения",
    "undoDone": "Изменения {name} отменены",
    "undoFailed": "Не удалось отменить: {message}",
    "diffLoadFailed": "Не удалось загрузить diff: {message}",
    "diffTruncated": "Diff слишком большой, показана часть",
    "restoring": "Отмена…"
  },
```

- [ ] **Step 3: FileChangeSummaryCard.vue**

新建 `dashboard/src/components/chat/message_list_comps/FileChangeSummaryCard.vue`：

```vue
<!--
  FileChangeSummaryCard
  ─────────────────────────────────────────────────────────────────────
  End-of-turn file change summary (2026-09-13). Rendered after the
  final reply blocks of a bot message that changed files through the
  built-in file tools. Entries are aggregated per file by the backend
  at agent-loop end (net diff vs the turn's baseline backup) and
  delivered via the `file_changes` stream event, persisted as
  `content.file_changes`.

  Row actions: expand → lazily fetch the unified diff
  (POST /chat/file-changes/diff), open on disk (existing
  useOpenOnDisk composable), two-click inline undo
  (POST /chat/file-changes/restore).

  Author: elecvoid243 | 2026-09-13
-->
<template>
  <div
    class="file-change-summary"
    :class="{ 'file-change-summary--dark': isDark }"
  >
    <div class="file-change-summary-head">
      <v-icon size="14">mdi-file-edit-outline</v-icon>
      <span class="file-change-summary-title">
        {{ tm("fileChanges.title", { count: files.length }) }}
      </span>
      <span v-if="totalAdds !== null" class="file-change-summary-total">
        <span class="stat-adds">+{{ totalAdds }}</span>
        <span class="stat-dels">−{{ totalDels }}</span>
      </span>
    </div>

    <div
      v-for="file in files"
      :key="file.path"
      class="file-change-summary-row"
    >
      <button
        type="button"
        class="file-change-summary-file"
        :disabled="!file.diff_available"
        @click="toggleRow(file)"
      >
        <v-icon size="14">{{ kindIcon(file.kind) }}</v-icon>
        <span class="file-change-summary-name" :title="file.path">
          {{ basename(file.path) }}
        </span>
        <template v-if="file.diff_available && file.adds !== null">
          <span class="stat-adds">+{{ file.adds }}</span>
          <span class="stat-dels">−{{ file.dels }}</span>
        </template>
        <span v-else class="file-change-summary-muted">
          {{ file.runtime === "sandbox"
            ? tm("fileChanges.sandboxHint")
            : tm("fileChanges.noStat") }}
        </span>
        <v-icon
          v-if="file.diff_available"
          size="16"
          class="file-change-summary-chevron"
          :class="{ expanded: expanded.has(file.path) }"
        >
          mdi-chevron-right
        </v-icon>
      </button>

      <div class="file-change-summary-actions">
        <v-btn
          v-if="file.runtime === 'local'"
          icon="mdi-open-in-new"
          size="x-small"
          variant="text"
          :title="tm('fileChange.openOnDisk')"
          @click.stop="openOnDisk(file.path, basename(file.path))"
        />
        <v-btn
          v-if="canUndo(file)"
          icon="mdi-undo"
          size="x-small"
          variant="text"
          :color="confirmingPath === file.path ? 'error' : undefined"
          :loading="restoringPath === file.path"
          :title="confirmingPath === file.path
            ? tm('fileChanges.undoConfirm')
            : tm('fileChanges.undo')"
          @click.stop="undoFile(file)"
        />
      </div>
    </div>

    <template v-for="file in files" :key="`body-${file.path}`">
      <div v-if="expanded.has(file.path)" class="file-change-summary-body">
        <div v-if="loadingPath === file.path" class="file-change-summary-loading">
          <v-progress-circular indeterminate size="14" width="2" />
        </div>
        <template v-else-if="diffs[file.path]">
          <div
            v-if="diffs[file.path].truncated"
            class="file-change-summary-muted file-change-summary-truncated"
          >
            {{ tm("fileChanges.diffTruncated") }}
          </div>
          <DiffPreview
            :content="diffs[file.path].diff"
            :file-path="file.path"
            :is-dark="isDark"
            :max-lines="400"
            :collapsible="false"
            :commentable="false"
          />
        </template>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import DiffPreview from "./DiffPreview.vue";
import { chatApi } from "@/api/v1";
import { useModuleI18n } from "@/i18n/composables";
import { useOpenOnDisk } from "@/composables/useOpenOnDisk";
import type { FileChangeSummaryFile } from "@/utils/fileChangeTool";

const props = defineProps<{
  files: FileChangeSummaryFile[];
  isDark?: boolean;
}>();

const { tm } = useModuleI18n("features/chat");
const { openOnDisk } = useOpenOnDisk("fileChange");

const expanded = reactive(new Set<string>());
const diffs = reactive<Record<string, { diff: string; truncated: boolean }>>({});
const loadingPath = ref("");
const confirmingPath = ref("");
const restoringPath = ref("");

const totalAdds = computed(() =>
  props.files.every((f) => f.adds !== null)
    ? props.files.reduce((sum, f) => sum + (f.adds || 0), 0)
    : null,
);
const totalDels = computed(() =>
  props.files.every((f) => f.dels !== null)
    ? props.files.reduce((sum, f) => sum + (f.dels || 0), 0)
    : null,
);

function basename(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  return normalized.slice(normalized.lastIndexOf("/") + 1) || path;
}

function kindIcon(kind: string): string {
  if (kind === "created") return "mdi-file-plus-outline";
  if (kind === "rollback") return "mdi-restore";
  return "mdi-file-edit-outline";
}

function canUndo(file: FileChangeSummaryFile): boolean {
  return file.runtime === "local" && !!file.backup_id;
}

function toggleRow(file: FileChangeSummaryFile) {
  if (!file.diff_available) return;
  if (expanded.has(file.path)) {
    expanded.delete(file.path);
    return;
  }
  expanded.add(file.path);
  if (!diffs[file.path]) void loadDiff(file);
}

async function loadDiff(file: FileChangeSummaryFile) {
  loadingPath.value = file.path;
  try {
    const resp = await chatApi.fileChangeDiff(
      file.path,
      file.backup_id,
      file.sha256,
    );
    const envelope = resp.data;
    if (envelope?.status === "error") {
      console.error(`file diff failed: ${envelope.message}`);
    } else if (envelope?.data) {
      diffs[file.path] = {
        diff: envelope.data.diff || "",
        truncated: !!envelope.data.truncated,
      };
    }
  } catch (error) {
    console.error("file diff request failed:", error);
  } finally {
    loadingPath.value = "";
  }
}

async function undoFile(file: FileChangeSummaryFile) {
  // Two-click inline confirmation: first click arms, second click fires.
  if (confirmingPath.value !== file.path) {
    confirmingPath.value = file.path;
    return;
  }
  confirmingPath.value = "";
  restoringPath.value = file.path;
  try {
    const resp = await chatApi.restoreFileChange(
      file.path,
      file.backup_id,
      file.sha256,
    );
    const envelope = resp.data;
    if (envelope?.status === "error") {
      console.error(`undo failed: ${envelope.message}`);
    } else {
      // File reverted: invalidate the shown diff and collapse the row.
      delete diffs[file.path];
      expanded.delete(file.path);
    }
  } catch (error) {
    console.error("undo request failed:", error);
  } finally {
    restoringPath.value = "";
  }
}
</script>

<style scoped>
.file-change-summary {
  margin-top: 6px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 8px;
  overflow: hidden;
  font-size: 13px;
}

.file-change-summary--dark {
  border-color: rgba(var(--v-theme-on-surface), 0.2);
}

.file-change-summary-head,
.file-change-summary-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
}

.file-change-summary-head {
  background: rgba(var(--v-theme-on-surface), 0.04);
}

.file-change-summary-title {
  font-weight: 500;
}

.file-change-summary-total,
.file-change-summary-file .stat-adds,
.file-change-summary-file .stat-dels {
  font-family: monospace;
  font-size: 12px;
}

.stat-adds {
  color: #2e7d32;
  margin-right: 4px;
}

.stat-dels {
  color: #c62828;
}

.file-change-summary-file {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
  background: none;
  border: none;
  padding: 2px 0;
  cursor: pointer;
  color: inherit;
  text-align: left;
}

.file-change-summary-file:disabled {
  cursor: default;
}

.file-change-summary-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-change-summary-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.file-change-summary-muted {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 12px;
}

.file-change-summary-chevron {
  margin-left: auto;
  transition: transform 0.15s;
}

.file-change-summary-chevron.expanded {
  transform: rotate(90deg);
}

.file-change-summary-body {
  padding: 0 10px 8px;
}

.file-change-summary-loading,
.file-change-summary-truncated {
  padding: 4px 0;
}
</style>
```

注：错误提示走 `console.error` 与组件内 muted 文案（与 `FileChangeCard.vue` 的克制风格一致）；若希望 toast，可再引入 `useToast`（`useOpenOnDisk` 内部已 toast 打开结果），v1 保持最小。样式类名 `stat-adds`/`stat-dels` 若与 `FileChangeCard.vue` 的 scoped 样式冲突（不在同一组件，scoped 隔离，无冲突）。

- [ ] **Step 4: 挂载到 ChatMessageList.vue**

4a. script setup 的组件 import 区（与 `ReasoningBlock` 等 import 并列）：

```typescript
import FileChangeSummaryCard from "./message_list_comps/FileChangeSummaryCard.vue";
```

4b. 模板：`visibleBlocks` 循环结束的 `</template>`（:396）与包裹层 `</template>`（:397，非编辑态 template 的闭合）之间插入：

```vue
                <FileChangeSummaryCard
                  v-if="messageContent(msg).fileChangeSummary?.length"
                  :files="messageContent(msg).fileChangeSummary"
                  :is-dark="isDark"
                />
```

（缩进对齐 :199 的 `<template v-for ...>` 层级；卡片渲染在 bubble 内、最终回复文本之后、message-meta 行之前。）

- [ ] **Step 5: 验证**

```bash
cd dashboard && pnpm typecheck && pnpm lint && pnpm test
```

Expected: 全部通过。

- [ ] **Step 6: 提交**

```bash
git add dashboard/src/api/v1.ts dashboard/src/components/chat/message_list_comps/FileChangeSummaryCard.vue dashboard/src/components/chat/ChatMessageList.vue dashboard/src/i18n/locales
git commit -m "feat(chatui): add end-of-turn file change summary card"
```

---

### Task 8: 全量校验、changelog 与手工 E2E

**Files:**
- Modify: `changelogs/internal/2026-09-13.md`（追加条目）

- [ ] **Step 1: 后端全量相关测试 + 格式**

```bash
ruff format . && ruff check .
uv run pytest tests/unit tests/test_computer_fs_tools.py tests/unit/test_runner_file_changes.py tests/unit/test_chat_file_change_routes.py tests/unit/test_chat_service_file_changes.py -q
```

Expected: 全部 PASS。

- [ ] **Step 2: 前端全量校验**

```bash
cd dashboard && pnpm typecheck && pnpm lint && pnpm test
```

Expected: 全部通过。

- [ ] **Step 3: 手工 E2E**

1. 终端 A：`uv run main.py`（API 于 http://localhost:6185）。
2. 终端 B：`cd dashboard && pnpm dev`（http://localhost:3000）。
3. 打开 ChatUI，选择已接入 LLM 的会话，发送："在当前工作区创建 demo.txt，内容写 hello，然后再把它改成 hello world"。
4. 验证：
   - Agent 结束后，最终回复下方出现"N 个文件已更改 +x −y"卡片，`demo.txt` 一行（created/编辑合并后 kind 取首条，stat 为净变化）。
   - 状态行"思考了x次…变更了z次文件"行为不变。
   - 点行展开 → 显示净 diff（两次修改合并为最终结果）。
   - 点"在磁盘上打开" → OS 打开文件并有 toast。
   - 点撤销图标一次 → 变为红色确认态；再点一次 → 文件内容恢复为"hello"（轮前状态），卡片 diff 收起。
   - 手工再改 demo.txt 后刷新历史页面 → 卡片仍在（持久化验证）；此时对该文件再点撤销 → 请求失败（checksum conflict），文件未被破坏。
   - 硬刷新 → 卡片从 `content.file_changes` 正确恢复。
5. 旧会话（无 `file_changes` 的历史消息）渲染无回归。

- [ ] **Step 4: changelog 条目**

在 `changelogs/internal/2026-09-13.md` 追加：

```markdown
- feat(chatui): end-of-turn file change summary card. File tools now record
  per-touch entries into `AstrAgentContext.extra["changed_files"]`; the agent
  runner emits a `file_changes` event when the loop completes, aggregating a
  net per-file diff against the turn's first baseline backup. The dashboard
  renders the summary card after the final reply with view-diff / open /
  per-file undo actions backed by `POST /chat/file-changes/{diff,restore}`.
```

- [ ] **Step 5: 最终提交**

```bash
git add changelogs/internal/2026-09-13.md
git commit -m "docs(changelog): note file change summary feature"
```

---

## 已知限制（记录在案，不阻塞交付）

- 中止（stop）的轮次不产出总结事件；切换会话中途加入的页面靠 run_snapshot 恢复，可能错过事件直到刷新（历史持久化兜底）。
- 沙箱 runtime 文件只出现在卡片上，无 diff/撤销。
- `file_access` 的 `readonly`/`workspace` 模式对两个新端点未做门禁（与 `/chat/open-file` 同信任级别：chat scope 鉴权；restore 仅能恢复 agent 本轮改过的文件）。如需收紧，后续把 `fs_access` 模式检查接入路由。
- 其他 agent runner（dify/coze/deerflow）不使用内置文件工具，天然不受影响。
