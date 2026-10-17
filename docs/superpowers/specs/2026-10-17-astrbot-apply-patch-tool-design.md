# AstrBot apply_patch 工具 — 设计文档

> 日期: 2026-10-17 · 状态: 已定稿，待实施 · 作者: elecvoid243

## 背景与目标

AstrBot 现有的文件修改能力是三件独立工具：

- `astrbot_file_write_tool`：整文件覆盖
- `astrbot_file_edit_tool`：单文件 `old`/`new` 文本替换（9 级模糊匹配链）+ 备份/回滚
- `astrbot_file_remove`：单路径删除

一次跨 3 个文件的重构（改内容 + 新建 + 删除 + 重命名）需要 5~6 次工具调用、多个 round trip，
且中途失败会留下"一半新一半旧"的工作区。Codex 的 `apply_patch` 用一份补丁信封解决了这个问题，
但它的模型是"无备份、非原子、失败返回 delta"；AstrBot 有备份库和编辑引擎，可以做得更强。

目标：新增一个 core 内置工具 `astrbot_apply_patch`，**能力覆盖 `astrbot_file_edit_tool` 且只多不少**，
同时具备多文件、原子提交、预演和结构化错误。

## 已确认的需求边界

| 项 | 决定 | 理由 |
|---|---|---|
| 落点 | AstrBot core 内置工具 | 需要复用编辑引擎、沙箱读写、路径门控、备份库这些内部模块 |
| 输入格式 | Codex 信封 DSL（宽松解析） | 上下文行定位，不依赖模型算行号；内部 IR 隔离，后续可加 unified diff |
| 事务语义 | 真原子：预演 → 提交 → 失败回滚 | AstrBot 有原文备份，可以做到"要么全成，要么不变" |
| `replace_all` | 不提供参数 | 由多条 hunk 表达；补丁的定位语义是"某一处"，全局替换容易误伤 |
| `strict_indent` | 不提供参数，模糊兜底路径硬编码 strict 行为 | 它只在"`old` 缩进写错"的恢复路径上生效；补丁没有理由信任已经写错的缩进 |
| 破坏性操作 | `Delete File` / 覆盖式 `Add` / 覆盖式 `Move to` 需要 `confirm=true` | 与 `file_remove` 的确认习惯一致；未确认时只返回清单，不写盘 |

## 能力对照（验收标准）

| 能力 | `astrbot_file_edit_tool` | `astrbot_apply_patch` |
|---|---|---|
| 单处文本替换 | ✅ | ✅ Update hunk |
| 9 级模糊匹配 | ✅ | ✅ 模糊兜底路径复用同一套 replacer 链 |
| 一处替换改多处 | ✅ `replace_all=True` | ⚠️ 用 N 条 hunk 依次命中（见下方说明） |
| 缩进重建 | ✅ `strict_indent` | ✅ 硬编码 strict（按文件实际缩进） |
| 新建文件 | 需 `file_write` | ✅ `Add File`（自动建父目录） |
| 删除文件 | 需 `file_remove` | ✅ `Delete File`（需 confirm） |
| 重命名/移动 | 需 write + remove | ✅ `Move to` |
| 多文件一次调用 | ❌ | ✅ |
| 跨文件原子性 | ❌ | ✅ 失败回滚 |
| 写前备份 | ✅ | ✅ 同一备份库 |
| 历史回滚 | ✅ `backup_id` | ✅ 复用 `file_edit` 的 `backup_id` 回滚 |
| Python AST 校验 | ✅ | ✅ |
| 文件锁 | ✅ | ✅ 多路径按字典序加锁 |
| BOM / 行尾保留 | ✅ | ✅ |
| 路径门控 / 只读模式 | ✅ | ✅ |
| 沙箱运行时 | ✅ | ✅ |
| 预演（不写盘） | ❌ | ✅ `dry_run` |
| 变更 diff | 仅报错时附带 | ✅ 每次成功都返回 |
| 结构化错误 | 部分 | ✅ 文件 + hunk 序号 + 行号 + 原因 |

**关于 `replace_all` 的唯一差异**：`file_edit` 可以一条指令替换全部出现位置；`apply_patch` 要求
模型为每处出现写一条 hunk。这是有意取舍——补丁的每条 hunk 都有明确的上下文锚点，
不会因为"文件里恰好还有 3 处相同文本"而误伤。模型如果不知道出现次数，可以先 `grep` 再写补丁。

## 架构

新增两个模块 + 一处行为不变的重构：

| 文件 | 职责 | I/O |
|---|---|---|
| `astrbot/core/tools/computer_tools/patch_engine.py` | 解析补丁；在文本上应用 hunk；生成文件级计划；检测破坏性操作；统计增删行 | 纯函数 |
| `astrbot/core/tools/computer_tools/patch_tool.py` | `ApplyPatchTool`：参数校验、路径归一化、锁、备份、沙箱读写、原子提交/回滚、变更记录 | 全部 I/O |
| `astrbot/core/tools/computer_tools/edit_engine.py` | 新增可复用的匹配/替换原语（内部重构，对外行为不变） | 否 |
| `astrbot/core/tools/computer_tools/__init__.py` | 导出 `ApplyPatchTool` | — |

### `edit_engine.py` 的重构

现有 `robust_replace()` 要求"唯一匹配"，而补丁引擎需要"从游标向后找第一处"。抽出：

```python
def iter_match_positions(content: str, old_string: str) -> Iterator[tuple[int, str]]:
    """按 replacer 链顺序，返回第一个命中的 replacer 产出的全部 (位置, 匹配文本)。"""

def find_match_at_or_after(
    content: str, old_string: str, start: int
) -> tuple[int, str] | None:
    """按 replacer 链顺序，返回 start 之后（含）的第一个匹配。"""

def replace_match_at(
    content: str, idx: int, matched: str, new_string: str, *, strict_indent: bool = True
) -> str:
    """在指定位置替换，复用 _adjust_replacement_indent 的缩进处理。"""
```

`robust_replace()` 改为基于这三个原语的薄封装，**对外签名、返回值与异常信息完全不变**；
现有 `tests/unit/test_edit_engine.py` 作为回归保护。

同时把 `bytes_edit_file()` 里的 BOM / 行尾探测逻辑抽成两个内部函数（`decode_text_bytes` /
`encode_text_bytes`），供补丁引擎复用；`bytes_edit_file()` 行为不变。

## 输入格式

### 语法

```text
*** Begin Patch                 ← 可省略
*** Add File: path
+新文件内容
*** Update File: path
*** Move to: new_path           ← 可选
@@ fn anchor                    ← 可选
 上下文行
-被删除的行
+新增的行
*** End of File                  ← 可选
*** Delete File: path
*** End Patch                   ← 可省略
```

### 宽松解析规则

1. 外层 ```` ``` ```` / ```` ```text ```` 围栏自动剥离；`*** Begin Patch` 前、`*** End Patch` 后的散字忽略
2. `*** Begin Patch` / `*** End Patch` / `@@` 均可省略
3. 标记后的空格容错（`*** Update File:path` 也接受）
4. hunk 体内的**空行视为空上下文行**；因此"用空行分隔多个 hunk"不再合法，多个 hunk 必须靠 `@@` 或新的 `*** Update File` 分隔
5. 未知标记、缺少 `+`/`-`/空格前缀、`*** Add File` 无内容行 → 报"第 N 行、原因"
6. 同一补丁内同一路径出现两次 → 解析期拒绝

### 路径与文件语义

| 操作 | 语义 | 破坏性 |
|---|---|---|
| `Add File` | 新文件；`+` 行以 `\n` 连接；父目录不存在则创建 | 目标已存在 → 需要 `confirm` |
| `Update File` | 在游标上按 hunk 顺序应用 | 否 |
| `Update File` + `Move to` | 先在源内容上应用 hunk，再写到目标路径并删除源 | 目标已存在 → 需要 `confirm` |
| `Delete File` | 备份后删除源文件 | **总是**需要 `confirm` |

- 路径解析与 `file_edit` 一致：local 运行时走 `_normalize_rw_path(write=True)`（workspace root 为基准，
  拦截 `..` 与白名单外路径）；sandbox 运行时按远端路径原样使用
- 目标路径是目录 → 报错；`Delete File` 指向目录 → 报错并提示改用 `astrbot_file_remove`
- 删除类操作复用 `file_remove` 的禁删判定：`_is_forbidden_path` + 用户配置的 `file_remove_blacklist`
- `Move to` 与源路径相同 → 报错
- 新建文件统一使用 `\n`；已有文件保留原 BOM 与行尾

## 引擎算法

### 文本归一化

对每个待修改文件：

1. 用 `decode_text_bytes()` 拆出 `(text, eol, has_bom)`
2. 在 **LF 文本**上执行全部 hunk
3. 用 `encode_text_bytes()` 还原 BOM 与原行尾

这与 `bytes_edit_file()` 的处理顺序一致，保证同一个文件用两个工具编辑后行尾不发生漂移。

### 游标与 hunk 应用

`cursor` 是字符偏移，初始为 0，且始终停在行边界。逐个 hunk：

```text
1. 若 hunk 有 @@ 锚点：
     在 content 中从 cursor 起按**整行**匹配该锚点（允许两端空白差异，不做子串匹配）
     找不到 → hunk_not_found
     cursor 移到该行行尾之后

2. 若 old_lines 为空（纯插入）：
     有 @@ 锚点 → 插入到锚点行之后（即 cursor 处）
     无锚点 → 追加到文件末尾
     插入内容末尾补一个 "\n"
     cursor 更新到插入内容之后

3. 否则：
     old_text = "\n".join(old_lines)
     若 is_end_of_file：先尝试"匹配必须结束于文件末尾"，失败再回退到常规搜索
     在 content 中从 cursor 起找 old_text（逐级放宽）：
       a. 精确匹配
       b. 忽略行尾空白
       c. 忽略两端空白
       d. Unicode 标点归一化（— → -, “” → ", 全角空格 → 空格）
       e. 以上都失败 → find_match_at_or_after()，复用 9 级 replacer 链
     找不到 → hunk_not_found（附文件、hunk 序号、锚点/上下文首行）
     new_text = "\n".join(new_lines)
     按 strict 行为重建缩进：以**文件中被匹配块的实际缩进**为基准，
     保留 new_text 内部的相对缩进层级（等价于 strict_indent=True 的路径）
     执行替换；cursor = 替换起点 + len(adjusted_new)
```

补充约束：

- 多个 hunk 必须按文件内顺序排列；游标只向后推进，不回退（重复 hunk 会依次命中后续出现位置）
- `End of File` 命中后不允许再有后续 hunk
- 纯删除（`new_lines` 为空）后，cursor 停在被删区域起点

### Python AST 校验

与 `file_edit` 采用同一规则：仅对 `.py` 文件（大小写不敏感），
**原文可解析而新内容不可解析**时拒绝；原文本身不可解析时允许（用于修复坏文件）。
报错包含 `SyntaxError` 的行号、列号与消息，并附 diff 预览（标注"未写入"）。

### 变更统计

对每个文件用 `build_unified_diff()` 生成 diff，同时统计增删行数（`difflib` 即可），
汇总到工具结果。

## 执行流程

### 阶段 0：入口校验

- `fs_access.get_mode(context) is READONLY` → 返回只读错误（沿用 `_READONLY_WRITE_ERROR`）
- `parse_patch()` 失败 / 空补丁 / 重复路径 → 直接返回，零写入
- 路径归一化失败（越界、`..`、黑名单）→ 直接返回

### 阶段 1：预演（Plan）

按补丁中文件出现顺序：

1. 归一化源路径与目标路径
2. 读取原文（local：`_read_file_bytes`；sandbox：`_sandbox_read_bytes`）
3. 计算新内容（Update / Add / Move）或标记删除
4. 破坏性判定
5. Python AST 校验
6. 生成 diff、统计增删行
7. 维护 `path -> FilePlan`，同路径冲突在解析期已排除

产物 `PatchPlan`：文件计划列表、破坏性清单、总增删行数。

**确认门控**：
- `dry_run=true` → 不检查 confirm，直接返回预演结果，并标注"正式执行时哪些操作需要 confirm"
- 否则若存在破坏性操作且 `confirm != true` → 返回清单 + 重试指引，**不写盘**
- 纯 Update / 纯新增 → 直接进入提交

### 阶段 2：提交（Commit）

1. 收集全部涉及路径（含 Move 源与目标），按字符串字典序排序后逐个 `get_file_lock()`，
   用 `AsyncExitStack` 统一释放，避免死锁
2. 锁内重读每个文件，与阶段 1 的字节比对：
   - 不一致 → 中止并返回 `file_changed_since_plan` + 路径清单（零写入）
   - Add 目标：阶段 1 不存在、现在存在 → 同样中止
3. 写盘顺序固定：
   - 先 `Add` / `Update` / `Move` 目标
   - 再 `Delete` / `Move` 源
   - 目的：进程崩溃时最坏是"新旧都在"，不会出现文件消失
4. 备份规则：
   - `Update`：写前备份原文
   - `Delete`：删除前备份原文
   - `Move`：源文件与"已存在的目标文件"都备份
   - `Add`：目标已存在（覆盖式）→ 备份原文；真正新建 → 无备份
5. 目录创建：
   - local：`Add` 写前执行 `Path(parent).mkdir(parents=True, exist_ok=True)`
   - sandbox：`_sandbox_write_bytes(..., create_parents=True)`
6. 每个文件成功后 `_record_changed_file()`：
   - `Add` → `created`
   - `Update` → `edit`
   - `Move` 目标 → `write`，`Move` 源 → `remove`
   - `Delete` → `remove`

沙箱补充：父目录创建需要在远端执行；给 `_sandbox_write_bytes()` 增加
`create_parents: bool = False` 参数（默认值保持旧行为），Add 场景传 `True`。
删除在沙箱侧新增一个 `_sandbox_remove_file(sb, path)` 小工具（`os.remove`）。

### 阶段 3：回滚（仅失败时）

维护 `committed: list[(path, original_bytes | None)]`，逆序恢复：

- `original_bytes` 非空 → 写回原字节
- `original_bytes is None`（本次新建）→ 删除

回滚本身失败时，返回 `rollback_failed` 并列出残留文件；原始错误同时保留在结果里。

## 工具参数与返回

### 参数

```jsonc
{
  "patch":   "string",   // 必填
  "dry_run": "boolean",  // 默认 false
  "confirm": "boolean"   // 默认 false
}
```

### 返回

成功：

```text
✅ Applied patch: 3 files changed (+12 -4)
M  src/a.py  (+8 -2)
A  src/b.py  (+4)
R  src/old.py -> src/new.py
D  src/obsolete.py

<unified diff；超过 40 KB 截断并标注 "(diff truncated)">
```

预演：

```text
🔍 Dry run: no files were written. 3 files would change (+12 -4)
...
⚠️ Requires confirm=true when applying: Delete File src/obsolete.py
```

需要确认：

```text
⚠️ This patch contains destructive changes and was NOT applied.
- Delete File: src/obsolete.py
- Add File (overwrites existing): src/b.py
Retry with confirm=true after the user approves.
```

失败：

```text
❌ Patch failed at src/a.py (hunk #2): context not found: "@@ def foo("
No files were modified.
```

回滚失败：

```text
❌ Patch failed at src/a.py (hunk #2): ...
Rollback failed for: src/b.py
Manual cleanup required.
```

### 工具描述（模型可见）

英文，与现有 core 工具一致。必须包含：

- 适用场景："multi-file or structural edits (add / delete / move); for a single small edit prefer
  `astrbot_file_edit_tool`"
- 一个最小补丁示例（Add + Update）
- `confirm=true` 的两段式语义
- 提示"上下文行必须来自文件原文；不确定时先读取文件"

## 注册与兼容

- `@builtin_tool(config=_COMPUTER_RUNTIME_TOOL_CONFIG)`：local 与 sandbox 运行时都可用
- `computer_tools/__init__.py` 增加 `ApplyPatchTool` 导出；`_BUILTIN_TOOL_MODULES` 已包含该包，无需改动
- `astrbot/core/config/default.py` 的 `tools_inherent` 增加 `astrbot_apply_patch`
  （与 `file_edit` 对等，子 Agent 也能使用）
- 不新增配置项
- `file_edit` / `file_write` / `file_remove` 的对外行为与参数不变
- `robust_replace` / `bytes_edit_file` 重构后行为不变，由现有测试保护

## 测试

现有测试位置：`tests/unit/test_edit_engine.py`、`tests/test_computer_fs_tools.py`。

| 层 | 用例 |
|---|---|
| parser | 完整语法；省略 Begin/End；代码围栏包裹；标记空格容错；空行当上下文；`@@` 省略；未知标记报行号；重复路径；`Add` 无内容行 |
| 引擎 | 精确 → rstrip → trim → Unicode → 9 级模糊兜底；游标单调推进；重复 hunk 依次命中；`End of File`；空 `old_lines` 插入；锚点找不到报 hunk 序号；纯删除后游标位置 |
| 计划 | Add/Update/Delete/Move；目标为目录报错；Move 同路径报错；破坏性判定三种情形；Python AST 拒绝与放行；CRLF/BOM 保留；增删行统计 |
| 提交 | 写盘失败触发回滚（monkeypatch 写函数）；新建文件回滚为删除；删除文件回滚为写回；`file_changed_since_plan`；锁按序获取；Move 目标成功但源删除失败的回滚；父目录创建 |
| 工具层 | READONLY 拒绝；`..`/黑名单/越界路径拒绝；`confirm` 拦截；`dry_run` 零写入；结果格式；`changed_files` 记录 kind 正确 |
| 回归 | `robust_replace` / `bytes_edit_file` 原测试全过；`file_edit` 行为不变 |

验证命令：`ruff check astrbot/core/tools/computer_tools tests` + 相关 pytest 用例。

## 风险与取舍

- **沙箱逐文件 I/O**：一次补丁 N 个文件 = 2N 次 `sb.python.exec`。补丁通常 <10 个文件，可接受；
  后续可合并成一次批量脚本。
- **回滚是 best-effort**：回滚过程中的磁盘错误无法保证恢复；此时返回 `rollback_failed` 并列出残留。
- **外部并发**：文件锁只能挡同进程。阶段 2 的"重读比对"能发现预演后的外部改动，但在"比对通过 → 写入"
  之间仍有理论上的竞态窗口。这是本工具明确的能力边界。
- **`replace_all` 差异**：见能力对照，属有意取舍。
- **删除不进回收站**：`file_remove` 走系统回收站，`apply_patch` 的删除走备份 + 回滚。两者语义不同，
  文档与工具描述都要写明。
- **内存占用**：预演阶段持有所有目标文件的原文与新区内容。补丁场景下可接受。
- **`Add File` 固定 `\n`**：不猜测目标仓库的行尾约定；已有文件不受影响。

## 明确不做

- unified diff（`---`/`+++`/`@@ -1,3 +1,4 @@`）输入解析
- grammar 约束的 freeform 工具（AstrBot 工具层只支持 JSON-schema `FunctionTool`）
- `replace_all` / `strict_indent` 参数
- `list_history` / `backup_id` 模式（回滚继续由 `astrbot_file_edit_tool` 提供）
- 删除走系统回收站
- 沙箱批量读写合并
- UI 审批弹窗（`confirm` 为模型驱动的两段式）
- 二进制文件补丁（遇到不可 UTF-8 解码的文件直接报错）
