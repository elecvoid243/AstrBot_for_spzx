# apply_patch 全能力与文件工具组切换 — 设计文档

> 日期: 2026-10-18 · 状态: 已定稿，待实施 · 作者: elecvoid243

## 背景与目标

`astrbot_apply_patch` 已具备多文件原子补丁能力，但与 `astrbot_file_edit_tool` /
`astrbot_file_write_tool` 仍有能力差距：没有 `replace_all`、没有编码控制、没有历史/回滚入口。
本设计做两件事：

1. 补齐 apply_patch 的缺口，使其在能力上覆盖 file_edit + file_write
2. 新增配置项 `provider_settings.file_tool_mode`，在**两套互斥的文件写工具组**之间切换

默认 `legacy`，升级后行为不变；切到 `patch` 后模型只看到 apply_patch。

## 已确认的决策

| 项 | 决定 |
|---|---|
| 合并范围 | 吸收 file_edit + file_write；保留 file_read / grep / file_remove |
| replace_all | 新增每文件一段 `*** Replace All:` 语法 |
| 历史/回滚 | 同一工具三种互斥模式：patch / list_history / backup_id |
| 工具组关系 | 互斥；legacy = write+edit，patch = apply_patch |
| 旧工具去留 | 类与注册全部保留，仅影响模型可见的 tool schema |
| 默认模式 | `legacy` |

## 1. 配置项

### 1.1 定义

```toml
[provider_settings]
file_tool_mode = "legacy"   # legacy | patch
```

- 缺省 / 非法值 → `legacy`
- 非法值用模块级 set 去重，同一非法值只记一次 warning（避免每请求刷日志）
- 按请求读取，切换后下一条消息生效，无需重启
- 不提供环境变量或命令行参数：这是部署级行为开关，不是单次调用参数

### 1.2 代码落点

新增 `astrbot/core/tools/file_tool_mode.py`：

```python
FILE_TOOL_MODE_KEY = "file_tool_mode"
FILE_TOOL_MODE_LEGACY = "legacy"
FILE_TOOL_MODE_PATCH = "patch"
DEFAULT_FILE_TOOL_MODE = FILE_TOOL_MODE_LEGACY


def resolve_file_tool_mode(provider_settings: dict | None) -> str:
    """Return 'legacy' or 'patch'; unknown values fall back to 'legacy'."""


def file_tool_allowed(name: str, mode: str) -> bool:
    """Whether a file write tool name is visible in this mode."""
```

`DEFAULT_CONFIG["provider_settings"]["file_tool_mode"] = "legacy"`。

### 1.3 WebUI 元数据

在 `astrbot/core/config/default.py` 的 `agent_computer_use.items` 增加：

```python
"provider_settings.file_tool_mode": {
    "description": "文件写入工具组",
    "type": "string",
    "options": ["legacy", "patch"],
    "labels": ["经典（file_write + file_edit）", "补丁（apply_patch）"],
    "hint": "两组工具互斥。切换后下一条消息生效；补丁模式下的覆盖写需要 confirm=true。",
}
```

Dashboard 的 `ConfigItemRenderer` 动态渲染 options/labels，无需前端代码改动或重新构建。

## 2. 工具组装配

| 装配点 | legacy 增加 | patch 增加 |
|---|---|---|
| `astr_main_agent._apply_local_env_tools` | FileWriteTool + FileEditTool | ApplyPatchTool |
| `astr_main_agent` sandbox 主 Agent 装配 | FileWriteTool + FileEditTool | ApplyPatchTool（当前缺失，需补） |
| `astr_agent_tool_exec._get_runtime_computer_tools` | write + edit | apply_patch |
| `astr_agent_tool_exec._build_handoff_toolset` 名称解析 | 过滤 write/edit | 过滤 apply_patch |

- 读/删/执行工具两组一致：`file_read`、`grep`、`file_remove`、shell、python、CUA
- `_get_runtime_computer_tools` 增加 `mode: str` 参数；调用方从 `provider_settings` 解析
- `_build_handoff_toolset` 在按名称解析前用 `file_tool_allowed(name, mode)` 过滤；
  `tools_inherent` 配置继续保留两个旧名，不会因工具组缺失而报错
- legacy 模式下 apply_patch 不可见；patch 模式下 file_edit/file_write 不可见

## 3. apply_patch 能力扩展

### 3.1 `*** Replace All:` 语法

```text
*** Replace All: path
-old line
-old line 2
+new line
+new line 2
```

规则：

- 一个文件一段，只含 `-old` / `+new` 行；不允许 `@@`、上下文行、`*** Move to:`
- 语义 = `robust_replace(old_text, new_text, replace_all=True, strict_indent=...)`
- 零次命中 → `PatchParseError`: `no occurrences found: '<首行>' (hunk #N)`
- 统计：命中次数 × 行数；diff 为整文件 unified diff
- 与 `*** Update File: <同路径>` 互斥（沿用重复路径拒绝）
- `Replace All` 与 `confirm` 无关（不删除、不覆盖目标路径）

`FilePatch.kind` 扩展为 `"add" | "update" | "delete" | "replace_all"`。

### 3.2 `encoding` 参数

- 调用级参数，默认 `"utf-8"`；作用于本次补丁的所有文件
- 支持别名归一化：
  - `utf-8` / `utf8` → `utf-8`
  - `utf-8 bom` / `utf-8-sig` / `utf8 bom` / `utf8 sig` → `utf-8-sig`
  - `ansi` → Windows 取 `cp<GetACP()>`，POSIX 取 `locale.getpreferredencoding(False)`
  - 其他值按 Python codec 名处理；非法名称在预演阶段报错，零写入
- 已有文件：按 `encoding` 解码；保留原 BOM 与行尾
- 新建文件：按 `encoding` 编码；`utf-8-sig` 写入 BOM，其余不写 BOM
- 一个补丁不能混合编码；混合场景拆分多次调用
- 历史/回滚模式不使用该参数（备份是原始字节）

### 3.3 `strict_indent` 参数

- 默认 `true`；关闭后等价于 `file_edit(strict_indent=False)`
- 透传到 hunk 应用与 `Replace All`；`true` 时保留 F1 的 tab/space 风格校验

### 3.4 三种互斥模式

工具参数：

```jsonc
{
  "patch": "string",          // 模式 1
  "path": "string",           // 模式 2/3
  "list_history": false,      // 模式 2
  "backup_id": "",            // 模式 3
  "dry_run": false,
  "confirm": false,
  "encoding": "utf-8",
  "strict_indent": true
}
```

校验（任一不满足即返回参数错误，列出合法形态）：

| 模式 | 条件 |
|---|---|
| 打补丁 | `patch` 非空；`path` / `list_history` / `backup_id` 必须为空 |
| 看历史 | `path` 非空 + `list_history=true`；`patch` / `backup_id` 为空 |
| 回滚 | `path` 非空 + `backup_id` 非空；`patch` / `list_history` 为空 |

- `list_history` 输出与 `FileEditTool._do_list_history` 完全一致
- 回滚：路径归一化 → 读取备份 → 保存 pre-rollback 快照 → 写回 → `_record_changed_file(kind="rollback")`
  → 返回与 file_edit 相同的成功文案；备份缺失/ID 不存在时返回原始错误
- local 与 sandbox 都支持；sandbox 写入走 `_sandbox_write_bytes`
- `dry_run` / `confirm` 在历史模式忽略（不参与校验）

### 3.5 工具描述

改成四段：

1. **Patch**：多文件 Update / Add / Delete / Move（保留最小示例）
2. **Replace All**：语法与适用场景（全文件重命名）
3. **History**：`path` 看历史、`path + backup_id` 回滚
4. **Options**：`encoding`、`strict_indent`、`confirm`、`dry_run`

## 4. 兼容性与不变式

- 默认 `legacy`：升级后模型看到的工具与现在完全一致
- 三类工具全部保留注册与导出；`file_tool_mode` 只影响 tool schema
- 历史消息渲染不变：Dashboard 的 `isFileChangeToolName` 继续认识旧名
- 直接内部调用（测试、插件、其他子系统）不受模式影响
- 两种模式的回滚共享同一份 `EditHistoryManager` 备份库
- `file_changes` 事件继续由 `_record_changed_file` 产生，包含 `rollback` kind
- 现有 file_edit / file_write / apply_patch 测试全部保持通过

## 5. 测试

| 层 | 用例 |
|---|---|
| 配置 | 缺省 → legacy；`patch`；非法值 → legacy + warning |
| 装配 | 四个装配点在两种模式下的工具集合；legacy 无 apply_patch，patch 无 write/edit |
| 子 Agent | patch 模式下 `tools_inherent` 中的旧名被过滤；legacy 下保留 |
| parser | Replace All 段、缺 old/new、含 `@@` 报错、与 Update 同路径冲突 |
| engine | Replace All 0/1/N 次、零命中报错、缩进、`strict_indent=false`、CRLF/BOM |
| encoding | GBK 文件更新、`utf-8-sig` 新建带 BOM、`ansi` 归一化、非法编码零写入 |
| modes | list_history 输出、backup_id 回滚、模式互斥校验、sandbox 回滚 |
| 回归 | 现有全部相关 pytest |

验证命令：

```bash
uv run pytest tests/unit/test_patch_engine.py tests/test_apply_patch_tool.py tests/unit/test_edit_engine.py -q
uv run ruff format/check <touched files>
```

## 6. 风险与边界

- **schema 变长**：参数从 3 个增到 7 个，描述变长；用分段文案降低误用
- **模式切换的即时性**：按请求读取，切换后下一条消息生效；同一会话的历史工具调用不受影响
- **补丁模式下覆盖写多一次往返**：`Add File` 覆盖已有文件仍需 `confirm=true`，这是刻意保留的安全门控
- **编码是调用级**：不支持一次补丁混合编码
- **插件/技能兼容**：patch 模式下若某个 skill 明确要求调用 `astrbot_file_edit_tool`，模型会看不到该工具；
  这是切换模式的预期代价，hint 中已说明

## 7. 不做

- 不删除 file_edit / file_write 的类或注册
- 不提供按子 Agent 覆盖的文件工具模式
- 不支持目录删除（继续用 file_remove）
- 不做一次补丁内多编码
- 不改 Dashboard 的逐文件卡片逻辑（已支持 apply_patch）
