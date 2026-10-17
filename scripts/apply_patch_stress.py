"""Stress harness for astrbot_apply_patch against weak-LLM patch habits.

Run with: uv run python scripts/apply_patch_stress.py
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from bisect import bisect_left
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot.core.tools.computer_tools import fs as fs_tools
from astrbot.core.tools.computer_tools import patch_tool as patch_tool_module
from astrbot.core.tools.computer_tools import util as computer_util
from astrbot.core.tools.computer_tools.edit_engine import find_match_at_or_after
from astrbot.core.tools.computer_tools.patch_engine import (
    _line_starts,
    _lines_equal,
    parse_patch,
    plan_patch,
)

TIERS = ("exact", "rstrip", "trim", "unicode")


def classify_recovery(text: str, old: str, start: int = 0) -> str:
    """Return the tier that locates ``old`` inside ``text``."""
    expected = old.split("\n")
    lines = text.split("\n")
    offsets = _line_starts(text)
    start_index = bisect_left(offsets, start)
    last_index = len(lines) - len(expected)
    for tier in range(4):
        for index in range(start_index, last_index + 1):
            if _lines_equal(lines[index : index + len(expected)], expected, tier):
                return TIERS[tier]
    if find_match_at_or_after(text, old, start) is not None:
        return "replacer-chain"
    return "none"


CASES: list[dict] = [
    # ---------------- parser leniency ----------------
    {
        "id": "P1",
        "name": "缺少 Begin/End Patch 标记",
        "patch": "*** Update File: a.txt\n-old\n+new\n",
        "files": {"a.txt": "old\n"},
        "expect": "ok",
        "want": "new\n",
    },
    {
        "id": "P2",
        "name": "整段被 ```text 围栏包裹",
        "patch": "```text\n*** Update File: a.txt\n-old\n+new\n```\n",
        "files": {"a.txt": "old\n"},
        "expect": "ok",
        "want": "new\n",
    },
    {
        "id": "P3",
        "name": "标记后漏写空格（*** Update File:a.txt）",
        "patch": "*** Update File:a.txt\n-old\n+new\n",
        "files": {"a.txt": "old\n"},
        "expect": "ok",
        "want": "new\n",
    },
    {
        "id": "P4",
        "name": "补丁文本是 CRLF（Windows 模型输出）",
        "patch": "*** Update File: a.txt\r\n-old\r\n+new\r\n",
        "files": {"a.txt": "old\n"},
        "expect": "ok",
        "want": "new\n",
    },
    {
        "id": "P5",
        "name": "补丁前后夹带解释性文字",
        "patch": "好的，下面是补丁：\n*** Begin Patch\n*** Update File: a.txt\n-old\n+new\n*** End Patch\n希望有帮助。\n",
        "files": {"a.txt": "old\n"},
        "expect": "ok",
        "want": "new\n",
    },
    {
        "id": "P6",
        "name": "空补丁",
        "patch": "",
        "files": {},
        "expect": "error",
        "err": "no file sections",
    },
    # ---------------- matching tolerance ----------------
    {
        "id": "M1",
        "name": "精确匹配",
        "patch": "*** Update File: a.txt\n-old\n+new\n",
        "files": {"a.txt": "old\n"},
        "expect": "ok",
        "want": "new\n",
    },
    {
        "id": "M2",
        "name": "上下文行尾多了空格",
        "patch": "*** Update File: a.txt\n-old   \n+new\n",
        "files": {"a.txt": "old\n"},
        "expect": "ok",
        "want": "new\n",
    },
    {
        "id": "M3",
        "name": "缩进漂移：模型写 8 空格，文件 4 空格",
        "patch": "*** Update File: a.py\n-def f():\n-        return 1\n+def f():\n+    return 2\n",
        "files": {"a.py": "def f():\n    return 1\n"},
        "expect": "ok",
        "want": "def f():\n    return 2\n",
    },
    {
        "id": "M4",
        "name": "Tab 与空格混用（严格拒绝，避免破坏缩进）",
        "patch": "*** Update File: a.py\n-def f():\n-\treturn 1\n+def f():\n+\treturn 2\n",
        "files": {"a.py": "def f():\n    return 1\n"},
        "expect": "error",
        "err": "indentation style",
    },
    {
        "id": "M5",
        "name": "多行 old 被写成字面量 \\n",
        "patch": "*** Update File: a.txt\n-foo()\\nbar()\n+foo()\n+baz()\n",
        "files": {"a.txt": "foo()\nbar()\n"},
        "expect": "ok",
        "want": "foo()\nbaz()\n",
    },
    {
        "id": "M6",
        "name": "Unicode 破折号（文件是 —，补丁是 -）",
        "patch": "*** Update File: a.txt\n-value = a-b\n+value = c\n",
        "files": {"a.txt": "value = a\u2014b\n"},
        "expect": "ok",
        "want": "value = c\n",
    },
    {
        "id": "M7",
        "name": "全角空格（文件是 U+3000，补丁是普通空格）",
        "patch": "*** Update File: a.txt\n-x y\n+z\n",
        "files": {"a.txt": "x\u3000y\n"},
        "expect": "ok",
        "want": "z\n",
    },
    {
        "id": "M8",
        "name": "只给改动行、不给上下文（唯一匹配）",
        "patch": "*** Update File: a.txt\n-only_line\n+replaced\n",
        "files": {"a.txt": "header\nonly_line\nfooter\n"},
        "expect": "ok",
        "want": "header\nreplaced\nfooter\n",
    },
    {
        "id": "M9",
        "name": "重复代码块，单 hunk 应改第一处",
        "patch": "*** Update File: a.txt\n-x = 1\n+x = 2\n",
        "files": {"a.txt": "x = 1\nx = 1\nx = 1\n"},
        "expect": "ok",
        "want": "x = 2\nx = 1\nx = 1\n",
    },
    {
        "id": "M10",
        "name": "重复代码块，两个 hunk 依次命中",
        "patch": "*** Update File: a.txt\n-x = 1\n+x = 2\n-x = 1\n+x = 3\n",
        "files": {"a.txt": "x = 1\nx = 1\n"},
        "expect": "ok",
        "want": "x = 2\nx = 3\n",
    },
    {
        "id": "M11",
        "name": "@@ 锚点前后带多余空格",
        "patch": "*** Update File: a.txt\n@@    def foo():   \n-    return 1\n+    return 2\n",
        "files": {"a.txt": "def foo():\n    return 1\n"},
        "expect": "ok",
        "want": "def foo():\n    return 2\n",
    },
    {
        "id": "M12",
        "name": "上下文里带了行号前缀（12: foo）",
        "patch": "*** Update File: a.txt\n-12: def foo():\n-13:     return 1\n+def foo():\n+    return 2\n",
        "files": {"a.txt": "def foo():\n    return 1\n"},
        "expect": "error",
        "err": "context not found",
    },
    {
        "id": "M13",
        "name": "前缀写了两遍（--x = 1）",
        "patch": "*** Update File: a.txt\n--x = 1\n++x = 2\n",
        "files": {"a.txt": "x = 1\n"},
        "expect": "error",
        "err": "context not found",
    },
    {
        "id": "M14",
        "name": "hunk 顺序写反（后面的块放前面）",
        "patch": "*** Update File: a.txt\n-second\n+SECOND\n-first\n+FIRST\n",
        "files": {"a.txt": "first\nsecond\n"},
        "expect": "error",
        "err": "context not found",
    },
    {
        "id": "M15",
        "name": "上下文完全不存在",
        "patch": "*** Update File: a.txt\n-zzz\n+y\n",
        "files": {"a.txt": "old\n"},
        "expect": "error",
        "err": "context not found",
    },
    {
        "id": "M16",
        "name": "old 块比文件剩余部分更长",
        "patch": "*** Update File: a.txt\n-a\n-b\n-c\n+d\n",
        "files": {"a.txt": "a\nb\n"},
        "expect": "error",
        "err": "context not found",
    },
    {
        "id": "M17",
        "name": "End of File 标记命中文件末尾",
        "patch": "*** Update File: a.txt\n-last\n+second\n*** End of File\n",
        "files": {"a.txt": "a\nlast\n"},
        "expect": "ok",
        "want": "a\nsecond\n",
    },
    {
        "id": "M18",
        "name": "End of File 标记但块不在末尾（回退到普通搜索）",
        "patch": "*** Update File: a.txt\n-first\n+FIRST\n*** End of File\n",
        "files": {"a.txt": "first\nsecond\n"},
        "expect": "ok",
        "want": "FIRST\nsecond\n",
    },
    {
        "id": "M19",
        "name": "插入 hunk（只有 + 行，带锚点）",
        "patch": "*** Update File: a.txt\n@@ a\n+middle\n",
        "files": {"a.txt": "a\nb\n"},
        "expect": "ok",
        "want": "a\nmiddle\nb\n",
    },
    {
        "id": "M20",
        "name": "追加 hunk（只有 + 行，无锚点）",
        "patch": "*** Update File: a.txt\n+b\n",
        "files": {"a.txt": "a\n"},
        "expect": "ok",
        "want": "a\nb\n",
    },
    # ---------------- safety / planning ----------------
    {
        "id": "S1",
        "name": "Python 语法回归（def f(:）",
        "patch": "*** Update File: a.py\n-def f():\n-    return 1\n+def f(:\n",
        "files": {"a.py": "def f():\n    return 1\n"},
        "expect": "error",
        "err": "Python syntax",
    },
    {
        "id": "S2",
        "name": "原文件本来就语法错误（允许修复）",
        "patch": "*** Update File: a.py\n-def f(:\n+def f():\n",
        "files": {"a.py": "def f(:\n"},
        "expect": "ok",
        "want": "def f():\n",
    },
    {
        "id": "S3",
        "name": "Delete 不存在的文件",
        "patch": "*** Delete File: missing.txt\n",
        "files": {},
        "expect": "error",
        "err": "does not exist",
    },
    {
        "id": "S4",
        "name": "Move 目标已存在 → 标记破坏性",
        "patch": "*** Update File: a.txt\n*** Move to: b.txt\n-a\n+A\n",
        "files": {"a.txt": "a\n", "b.txt": "b\n"},
        "expect": "ok",
        "want": "A\n",
        "assert_destructive": "overwrite_move_target",
    },
    {
        "id": "S5",
        "name": "Add 覆盖已存在文件 → 标记破坏性",
        "patch": "*** Add File: a.txt\n+new\n",
        "files": {"a.txt": "old\n"},
        "expect": "ok",
        "want": "new\n",
        "assert_destructive": "overwrite_add",
    },
    {
        "id": "S6",
        "name": "CRLF + BOM 保留",
        "patch": "*** Update File: a.txt\n-a\n+b\n",
        "files": {"a.txt": "\ufeffa\r\n"},
        "expect": "ok",
        "want_bytes": b"\xef\xbb\xbfb\r\n",
    },
]


def _read_factory(files: dict[str, str]):
    def read(path: str) -> bytes | None:
        value = files.get(path)
        return value.encode("utf-8") if value is not None else None

    return read


def run_engine_case(case: dict) -> dict:
    row = {
        "id": case["id"],
        "name": case["name"],
        "expect": case["expect"],
        "actual": "",
        "recovery": "-",
        "detail": "",
        "ok": False,
    }
    try:
        entries = parse_patch(case["patch"])
        plan = plan_patch(entries, _read_factory(case["files"]))
    except Exception as exc:  # noqa: BLE001 - harness records any failure
        row["actual"] = "error"
        row["detail"] = f"{type(exc).__name__}: {exc}"
        if case["expect"] == "error":
            row["ok"] = case.get("err", "") in str(exc)
        return row

    if case["expect"] == "error":
        row["actual"] = "ok"
        row["detail"] = "unexpected success"
        return row

    item = plan.files[0]
    if "want_bytes" in case:
        got = item.new_bytes
        row["actual"] = "ok"
        row["ok"] = got == case["want_bytes"]
        row["detail"] = repr(got)
    else:
        got = (item.new_bytes or b"").decode("utf-8")
        row["actual"] = "ok"
        row["ok"] = got == case["want"]
        row["detail"] = repr(got)
    if "assert_destructive" in case:
        row["ok"] = row["ok"] and item.destructive_reason == case["assert_destructive"]
        row["recovery"] = item.destructive_reason or "-"
    elif item.kind == "update" and entries[0].hunks and entries[0].hunks[0].old_lines:
        old = "\n".join(entries[0].hunks[0].old_lines)
        row["recovery"] = classify_recovery(case["files"][entries[0].path], old)
    return row


def _context(*, umo="qq:friend:user-1", mode="full", role="admin"):
    holder = SimpleNamespace(
        get_config=lambda umo=None: {
            "provider_settings": {
                "computer_use_require_admin": False,
                "computer_use_runtime": "local",
                "file_access_default_mode": mode,
            }
        }
    )
    event = SimpleNamespace(
        role=role, unified_msg_origin=umo, get_sender_id=lambda: "user-1"
    )
    return holder, event


def _bind_tool_context(tmp: Path, *, mode="full"):
    from astrbot.core.agent.run_context import ContextWrapper

    holder, event = _context(mode=mode)
    computer_util.get_astrbot_workspaces_path = lambda: str(tmp / "workspaces")
    (tmp / "workspaces").mkdir(parents=True, exist_ok=True)
    ws = (
        tmp
        / "workspaces"
        / computer_util.normalize_umo_for_workspace(event.unified_msg_origin)
    )
    ws.mkdir(parents=True, exist_ok=True)
    fs_tools.fs_access.sp = SimpleNamespace(
        session_get=AsyncMock(return_value=None),
        session_put=AsyncMock(),
    )
    return ContextWrapper(context=SimpleNamespace(context=holder, event=event)), ws


async def run_tool_cases() -> list[dict]:
    from astrbot.core.tools.computer_tools.patch_tool import ApplyPatchTool

    rows: list[dict] = []

    async def tool_case(case_id, name, fn):
        tmp = Path(tempfile.mkdtemp(prefix="apply_patch_stress_"))
        try:
            context, ws = _bind_tool_context(tmp)
            result = await fn(context, ws)
            rows.append(
                {
                    "id": case_id,
                    "name": name,
                    "expect": "ok",
                    "actual": "ok" if result[0] else "fail",
                    "recovery": "-",
                    "detail": result[1],
                    "ok": result[0],
                }
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    async def t1(context, ws):
        (ws / "a.txt").write_text("a\n", encoding="utf-8")
        (ws / "b.txt").write_text("b\n", encoding="utf-8")
        (ws / "old.txt").write_text("bye\n", encoding="utf-8")
        result = await ApplyPatchTool().call(
            context,
            patch=(
                "*** Update File: a.txt\n-a\n+A\n"
                "*** Update File: b.txt\n*** Move to: moved.txt\n-b\n+B\n"
                "*** Add File: new.txt\n+hi\n"
                "*** Delete File: old.txt\n"
            ),
            confirm=True,
        )
        ok = (
            "Applied patch" in result
            and (ws / "a.txt").read_text(encoding="utf-8") == "A\n"
            and (ws / "moved.txt").read_text(encoding="utf-8") == "B\n"
            and not (ws / "b.txt").exists()
            and (ws / "new.txt").read_text(encoding="utf-8") == "hi\n"
            and not (ws / "old.txt").exists()
        )
        return ok, result.splitlines()[0]

    async def t2(context, ws):
        (ws / "a.txt").write_text("a\n", encoding="utf-8")
        (ws / "b.txt").write_text("b\n", encoding="utf-8")
        original_write = patch_tool_module._write_file_bytes

        def flaky(path, data):
            if Path(path).name == "b.txt":
                raise OSError("disk full")
            return original_write(path, data)

        patch_tool_module._write_file_bytes = flaky
        try:
            result = await ApplyPatchTool().call(
                context,
                patch=(
                    "*** Update File: a.txt\n-a\n+A\n*** Update File: b.txt\n-b\n+B\n"
                ),
            )
        finally:
            patch_tool_module._write_file_bytes = original_write
        ok = (
            "No files were modified" in result
            and (ws / "a.txt").read_text(encoding="utf-8") == "a\n"
            and (ws / "b.txt").read_text(encoding="utf-8") == "b\n"
        )
        return ok, result.splitlines()[0]

    async def t3(context, ws):
        (ws / "a.txt").write_text("a\n", encoding="utf-8")
        result = await ApplyPatchTool().call(context, patch="*** Delete File: a.txt\n")
        ok = "NOT applied" in result and (ws / "a.txt").exists()
        return ok, result.splitlines()[0]

    async def t4(context, ws):
        (ws / "a.txt").write_text("a\n", encoding="utf-8")
        result = await ApplyPatchTool().call(
            context, patch="*** Update File: a.txt\n-a\n+A\n", dry_run=True
        )
        ok = "Dry run" in result and (ws / "a.txt").read_text(encoding="utf-8") == "a\n"
        return ok, result.splitlines()[0]

    async def t5(context, ws):
        ws_context, _ = _bind_tool_context(ws.parents[1], mode="workspace")
        result = await ApplyPatchTool().call(
            ws_context, patch="*** Update File: ../outside.txt\n-a\n+b\n"
        )
        ok = "restricted" in result.lower() or "denied" in result.lower()
        return ok, result.splitlines()[0]

    async def t6(context, ws):
        (ws / "a.txt").write_text("a\n", encoding="utf-8")
        ro_context, _ = _bind_tool_context(ws.parents[1], mode="readonly")
        result = await ApplyPatchTool().call(
            ro_context, patch="*** Update File: a.txt\n-a\n+b\n"
        )
        ok = "readonly" in result.lower()
        return ok, result.splitlines()[0]

    await tool_case("T1", "多文件 add/update/move/delete 原子成功", t1)
    await tool_case("T2", "第二个文件写失败 → 回滚第一个", t2)
    await tool_case("T3", "Delete 未带 confirm → 零写入", t3)
    await tool_case("T4", "dry_run → 零写入", t4)
    await tool_case("T5", "WORKSPACE 模式下越界路径被拒", t5)
    await tool_case("T6", "READONLY 模式下拒绝写入", t6)
    return rows


def run_perf() -> dict:
    line_count = 5000
    text = "".join(f"line_{i} = {i}\n" for i in range(line_count))
    hunk = {
        "id": "PERF",
        "name": "5000 行文件尾部 hunk",
        "expect": "ok",
        "patch": "*** Update File: big.txt\n-line_4998 = 4998\n+line_4998 = 0\n",
        "files": {"big.txt": text},
        "want": text.replace("line_4998 = 4998", "line_4998 = 0"),
    }
    start = time.perf_counter()
    rows = [run_engine_case(hunk)]
    elapsed = time.perf_counter() - start
    return {"elapsed": elapsed, "ok": rows[0]["ok"], "lines": line_count}


def main() -> None:
    engine_rows = [run_engine_case(case) for case in CASES]
    tool_rows = asyncio.run(run_tool_cases())
    perf = run_perf()

    all_rows = engine_rows + tool_rows
    passed = sum(1 for row in all_rows if row["ok"])
    total = len(all_rows)
    recovered = [
        row
        for row in engine_rows
        if row["actual"] == "ok" and row["recovery"] not in ("-", "exact")
    ]

    print("## 结果明细\n")
    print("| ID | 场景 | 期望 | 实际 | 恢复路径 | 结果 |")
    print("|---|---|---|---|---|---|")
    for row in all_rows:
        mark = "PASS" if row["ok"] else "FAIL"
        print(
            f"| {row['id']} | {row['name']} | {row['expect']} | "
            f"{row['actual']} | {row['recovery']} | {mark} |"
        )

    print("\n## 汇总\n")
    print(f"- 用例：{passed}/{total} 通过")
    print(f"- 依赖宽容解析/模糊匹配救回：{len(recovered)} 例")
    print(f"- 5k 行文件尾部 hunk：{perf['elapsed'] * 1000:.1f} ms（{perf['ok']=}）")
    failures = [row for row in all_rows if not row["ok"]]
    if failures:
        print("\n## 未通过\n")
        for row in failures:
            print(f"- {row['id']} {row['name']}: {row['detail']}")


if __name__ == "__main__":
    main()
