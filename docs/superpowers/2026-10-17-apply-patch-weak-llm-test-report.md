# astrbot_apply_patch 弱模型补丁压力测试报告

> 日期: 2026-10-17 · 作者: elecvoid243 · 分支: `feat/apply-patch`
> 复测状态: **F1/F2/F4/F5 已修复并复测，38/38 通过**

## 1. 测试目标与方法

目标：验证 `astrbot_apply_patch` 在"模型能力不足、补丁写法不规范"时的**恢复能力**与**安全边界**。

方法：真实调用 `parse_patch` / `plan_patch` / `apply_hunks_to_text` 与 `ApplyPatchTool.call`，
构造 38 个用例，分四组：

| 组 | 数量 | 内容 |
|---|---|---|
| P | 6 | 解析层宽容：缺标记、代码围栏、无空格、CRLF、夹带解释文字、空补丁 |
| M | 20 | 匹配层宽容：空白/缩进/转义/Unicode/重复块/锚点/顺序/EOF/插入 |
| S | 6 | 安全层：Python AST、删除缺失、覆盖式 Add/Move、CRLF+BOM |
| T | 6 | 工具层端到端：多文件原子、回滚、confirm、dry-run、路径门控、只读 |

判定：
- `ok` 用例比较**最终文件内容**，不是"没报错"；
- `error` 用例要求抛出异常且错误信息包含预期关键词；
- 额外记录命中的恢复层级（exact → rstrip → trim → unicode → 9 级 replacer 链）。

环境：Windows 11 / Python 3.12 / `uv run`；命令 `uv run python scripts/apply_patch_stress.py`。

## 2. 结果总览

**修复前 37/38；修复后 38/38。**

| 指标 | 数值 |
|---|---|
| 解析宽容救回 | 5 例（P1–P5：缺标记、围栏、无空格、CRLF、夹带文字） |
| 空白/缩进归一救回 | 2 例（M2 行尾空格、M3 缩进 8→4） |
| Unicode 归一救回 | 2 例（M6 破折号、M7 全角空格） |
| 转义归一救回 | 1 例（M5 字面量 `\n`） |
| 必须失败且正确失败 | 8 例 |
| 必须失败但错误地成功 | 修复前 1 例（M4），修复后 0 |

另有 3 项**错误文案质量检查**（不改变行为，不计入 38 例）：解析错误缺行号、hunk 错误缺文件/序号、
缺 `@@` 时报错指向第一行 —— 见 F4/F5。

### 2.1 解析层（P）

| ID | 场景 | 结果 |
|---|---|---|
| P1 | 缺少 `*** Begin/End Patch` | PASS |
| P2 | 整段被 ```text 围栏包裹 | PASS |
| P3 | 标记后漏写空格（`*** Update File:a.txt`） | PASS |
| P4 | 补丁文本是 CRLF | PASS |
| P5 | 补丁前后夹带解释性文字 | PASS |
| P6 | 空补丁 | PASS（拒绝，`no file sections`） |

### 2.2 匹配层（M）

| ID | 场景 | 结果 | 恢复路径 |
|---|---|---|---|
| M1 | 精确匹配 | PASS | exact |
| M2 | 上下文行尾多空格 | PASS | rstrip |
| M3 | 缩进漂移 8→4 | PASS | trim |
| M4 | Tab 与空格混用 | PASS（F1 修复后拒绝） | — |
| M5 | 多行 old 写成字面量 `\n` | PASS | replacer-chain |
| M6 | 破折号 `—` vs `-` | PASS | unicode |
| M7 | 全角空格 vs 普通空格 | PASS | unicode |
| M8 | 只给改动行、无上下文 | PASS | exact |
| M9 | 重复块，单 hunk 改第一处 | PASS | exact |
| M10 | 重复块，两 hunk 依次命中 | PASS | exact |
| M11 | `@@` 锚点带多余空格 | PASS | exact |
| M12 | 上下文带行号前缀（`12: foo`） | PASS（拒绝） | — |
| M13 | 前缀写两遍（`--x = 1`） | PASS（拒绝） | — |
| M14 | hunk 顺序写反 | PASS（拒绝） | — |
| M15 | 上下文完全不存在 | PASS（拒绝） | — |
| M16 | old 块比文件剩余部分长 | PASS（拒绝） | — |
| M17 | `End of File` 命中末尾 | PASS | exact |
| M18 | `End of File` 但块不在末尾 | PASS（回退普通搜索） | exact |
| M19 | 带锚点插入 | PASS | — |
| M20 | 无锚点追加 | PASS | — |

### 2.3 安全层（S）

| ID | 场景 | 结果 |
|---|---|---|
| S1 | Python 语法回归（`def f(:`） | PASS（拒绝） |
| S2 | 原文件本来语法错误，允许修复 | PASS |
| S3 | Delete 不存在的文件 | PASS（拒绝） |
| S4 | Move 目标已存在 | PASS（标记 `overwrite_move_target`） |
| S5 | Add 覆盖已存在文件 | PASS（标记 `overwrite_add`） |
| S6 | CRLF + BOM 保留 | PASS |

### 2.4 工具层（T）

| ID | 场景 | 结果 |
|---|---|---|
| T1 | 多文件 add/update/move/delete 原子成功 | PASS |
| T2 | 第二个文件写失败 → 回滚第一个 | PASS（两文件均保持原样） |
| T3 | Delete 未带 `confirm` → 零写入 | PASS |
| T4 | `dry_run` → 零写入 | PASS |
| T5 | WORKSPACE 模式越界路径被拒 | PASS |
| T6 | READONLY 模式拒绝写入 | PASS |

## 3. 发现

### F1（高，已修复）Tab/空格漂移会静默写入混合缩进

> 状态：已修复于 `7bf2392e1`。修复后三种变体全部拒绝；插入路径同样校验。
> 回归用例：`tests/unit/test_patch_engine.py` 3 例 + `tests/unit/test_edit_engine.py` 2 例。

**现象**：以下三种写法中，只有第一种被拒绝，另外两种**成功写入**了 Tab 缩进：

| 变体 | 结果 |
|---|---|
| `old` 只有 `\treturn 1`（无上下文） | `ValueError: indentation uses incompatible tab/space styles` ✅ |
| `old` 精确匹配，`new` 写成 `\treturn 2` | 写入 `def f():\n\treturn 2\n` ❌ |
| `old` 含无缩进上下文 `def f():` + Tab 行，`new` 用 Tab | 写入 `def f():\n\treturn 2\n` ❌ |

**影响**：Python 的 AST 校验**不会拦截**——单行 Tab 缩进在语法上合法。文件会变成"空格文件里混入 Tab"，
后续 diff、格式化、甚至 Python 的 `TabError`（当同一块内混用时）都可能被触发。

**根因**（`edit_engine.py:601-618`）：
1. 冲突检查只比较"文件匹配块的实际缩进"与"模型 `old` 的缩进"；`target_indent == source_indent` 时直接
   `return new_string`，**从不校验 `new` 的缩进风格**。
2. 当 hunk 含一行无缩进的上下文（如 `def f():`），`_common_leading_whitespace` 对文件块和 `old` 都返回 `""`，
   冲突检查被绕过。

**修复建议**：在 `adjusted_replacement()` 返回前增加一次"结果风格校验"：

```python
if _indent_styles_conflict(
    _common_leading_whitespace(matched),
    _common_leading_whitespace(adjusted),
):
    raise ValueError(
        "Replacement uses tabs where the file uses spaces (or vice versa); "
        "rewrite the hunk with the file's indentation style."
    )
```

校验对象是 `adjusted`（真正的落盘内容）而不是 `old`，并放在 early-return 之前。
需补 3 个回归用例（上表三变体）。

### F2（中高，已修复）精确匹配阶梯是 O(n²)，大文件单 hunk 超过 2 秒

> 状态：已修复于 `e8078c1d3`（预切分行数组 + 行索引比较）。
> 修复后同一基准：1k 2.9 ms / 2k 6.1 ms / 5k 14.0 ms / 10k 27.8 ms（10k 提升约 87 倍）。
> 回归用例：`test_apply_matches_near_end_of_large_file`（20k 行）。

**实测**（单 hunk，位于文件末尾）：

| 文件行数 | 耗时 |
|---|---|
| 1,000 | 27 ms |
| 2,000 | 105 ms |
| 5,000 | 621 ms |
| 10,000 | 2,428 ms |

每翻一倍约 4 倍增长，确认是 O(n²)。

**根因**（`patch_engine.py:_find_exact_ladder`）：对每个候选行起点执行
`text[pos:].split("\n")[:len(expected)]`，每次切片都复制剩余全文；4 个 tier 各跑一遍。

**影响**：补丁目标通常是源码文件，5k 行已属常见（单个大模块），此时一次补丁要 0.6 秒纯 CPU；
若一个补丁含多个 hunk，按 hunk 数线性叠加。弱模型一次生成 5 个 hunk 就是 3 秒。

**修复建议**：`text.split("\n")` 只做一次，按行索引比较，不再切片：

```python
lines = text.split("\n")
start_line = bisect_left(line_offsets, start)
for tier in range(4):
    for i in range(start_line, len(lines) - len(expected) + 1):
        if _lines_equal(lines[i:i + len(expected)], expected, tier):
            return line_offsets[i], "\n".join(lines[i:i + len(expected)])
```

预期降到 10k 行 < 20 ms。

### F3（低）压力脚本的 BOM 恢复层级统计有偏差

S6（CRLF+BOM）被脚本标成 `replacer-chain`，实际是"解码归一后精确匹配"。这是**测试脚本**的统计口径问题
（用原始带 BOM 字符串做分类），不是产品缺陷。报告中的恢复层级已按解码后文本修正。

### F4（中，已修复）结构化错误信息没有到达模型

> 状态：已修复于 `ac4d50205`。当前实际返回：
> `Error: invalid patch: ... (line 2)`；
> `Error: patch cannot be applied: a.txt: context not found: 'x' (hunk #2)`。
> 回归用例：`test_parse_error_message_includes_line_number`、
> `test_hunk_error_message_includes_file_and_hunk_number`、工具层 2 例。

Spec 承诺错误包含"文件 + hunk 序号 + 行号"，异常对象里也确实有
（`PatchParseError.line_number`、`HunkApplyError.hunk_index`），但 `__str__` 只输出 message，
工具层直接 `str(exc)`，于是模型实际看到的是：

| 场景 | 实际返回 | 期望 |
|---|---|---|
| 解析失败 | `Error: invalid patch: hunk lines must start with ...` | 附 `line 2` |
| hunk 找不到 | `Error: patch cannot be applied: context not found: 'old'` | 附 `a.txt` + `hunk #2` |
| 文件不存在 | `Error: patch cannot be applied: file does not exist: missing.txt` | 已达标 |

影响：模型无法根据位置自我修复，只能整段重试，弱模型会反复失败。

**修复建议**：
- `PatchParseError.__str__` → `f"{message} (line {line_number})"`（`line_number is None` 时省略）
- `HunkApplyError.__str__` → `f"{message} (hunk #{hunk_index + 1})"`
- `plan_patch` 捕获 `HunkApplyError` 后包一层：`f"{entry.path}: {exc}"`
- 补 3 个断言错误文案的用例

### F5（中，已修复）缺少 `@@` 时多个改动块被合并成一个 hunk，报错指向第一行

> 状态：已修复于 `9fd509ce4`。当 old 块首行在文件中存在、整块却匹配失败时，
> 错误信息追加 `; if these are separate edits, separate them with '@@'`。
> 首行也不存在时不加提示，避免噪声。回归用例 2 例。

**现象**：模型想改两处，但两段之间没写 `@@`：

```text
*** Update File: a.txt
-old
+new
-never
+x
```

解析器把四行合并成一个 hunk（`old_lines = ["old", "never"]`），应用时报
`context not found: 'old'`。但 `old` 在文件里**是存在的**，这个报错会让模型误以为
"上下文写错了"，从而反复微调第一行，始终无法定位真正问题（缺 `@@`）。

**根因**：`parse_patch` 只把 `@@` 当作新 hunk 的分隔符；没有 `@@` 时，连续的
`-`/`+` 行天然属于同一个 hunk。这与 Codex 语法一致，但弱模型经常省略 `@@`。

**修复建议**（择一）：
1. 低成本：错误信息附带"old block spans N lines starting with X; separate distant edits with `@@`"
2. 中成本：当 hunk 的 `-` 行序列在文件中无法整体匹配、但**第一行单独存在**时，错误里明确提示"可能是缺少 `@@`"
3. 不做启发式自动拆分（会把"一个块内包含多处修改"误判成多个 hunk）

## 4. 已验证安全的行为

- 解析失败、路径越界、只读模式、confirm 缺失 → **零写入**
- 多文件提交中途失败 → 已写文件回滚，两文件均保持原样
- 删除/移动前备份；`changed_files` 记录 kind
- Python 语法回归被拒绝；原文件已坏时允许修复
- 重复块按"游标向后"确定性命中，不会随机挑一处
- hunk 顺序写反、上下文不存在、行号前缀、双前缀 → 失败并零写入，错误带 file/hunk/line
- CRLF/BOM 在 Update 与 Add 路径均保留

## 5. 修复结果与剩余项

| 项 | 状态 | 提交 | 验证 |
|---|---|---|---|
| F1 缩进风格校验 | ✅ 已修复 | `7bf2392e1` | 3 变体全拒绝 + 插入路径 |
| F2 线性匹配 | ✅ 已修复 | `e8078c1d3` | 10k 行 2,428ms → 28ms |
| F4 错误位置外显 | ✅ 已修复 | `ac4d50205` | line / file / hunk 均可见 |
| F5 缺 `@@` 提示 | ✅ 已修复 | `9fd509ce4` | 首行存在才提示 |
| 压力脚本收敛为 `tests/` 回归 | ⏳ 待办 | — | 当前为 `scripts/` 独立脚本 |
| 行号前缀自动剥离 | ⏳ 待评估 | — | 当前明确拒绝，避免误改 |

复测：压力套件 **38/38**；相关 pytest **66 passed**；`tests/unit` **1965 passed**，
仅剩 1 个既有 Windows CRLF 失败（`test_computer.py::test_exec_simple_code`，与本次无关）。

## 6. 复现

```bash
# 38 个用例 + 性能
uv run python scripts/apply_patch_stress.py

# F1 三变体 + 性能梯度
uv run python scripts/_diag_apply_patch.py

# F4/F5 的错误文案
uv run python scripts/_diag_patch_errors.py
```

三个脚本当前未提交，位于 `scripts/` 下。
