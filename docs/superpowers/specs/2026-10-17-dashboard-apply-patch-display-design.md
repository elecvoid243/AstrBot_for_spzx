# Dashboard apply_patch 展示 — 设计文档

> 日期: 2026-10-17 · 状态: 已定稿，待实施 · 作者: elecvoid243

## 背景与目标

`astrbot_apply_patch` 已经能原子地修改多个文件，但 dashboard 的逐工具文件卡片
（`FileChangeCard` / ReasoningBlock chips）只认识 `astrbot_file_edit_tool`、
`astrbot_file_write_tool`、`astrbot_file_remove`。一次 apply_patch 在 ChatUI 里目前只表现为
一条普通工具调用，用户看不到"改了哪几个文件"。

目标：让 apply_patch 产生与 file_edit **视觉一致**的逐文件卡片：

- 每个受影响文件一张卡（共享同一个 `callId`）
- Update → 黄卡 + `+a −d`
- Add → 绿卡 + 行数
- Delete → 红卡
- Move → 绿卡显示目标路径，并注明来源
- 运行中先按 `args.patch` 列出目标文件；完成后用真实 result 替换
- 回合结束的 `FileChangeSummaryCard` **不改**：`_record_changed_file` 已经把 apply_patch
  的 `created/edit/write/remove` 计入 `file_changes` 事件

## 数据来源

工具 result 已经是自描述格式：

```text
Applied patch: 3 files changed (+12 -4)
UPDATE src/a.py
ADD src/b.py
UPDATE src/old.py -> src/new.py
DELETE src/obsolete.py

--- src/a.py
+++ src/a.py
@@ -1,2 +1,2 @@
-old
+new
--- src/b.py
+++ src/b.py
@@ ...
```

- 文件清单：`Applied patch:` 之后到第一个空行之间的行
- 每文件 diff：按连续的 `--- <path>` + `+++ <same path>` 头切分；内容行的 `---` 一定带
  `+` / `-` / 空格前缀，不会误切
- Delete 没有 diff（后端对删除不生成 diff），卡片只显示红色状态

## 方案

### 1. `dashboard/src/utils/fileChangeTool.ts`

新增：

```ts
export const APPLY_PATCH_TOOL_NAME = "astrbot_apply_patch";
```

`isFileChangeToolName()` 纳入该名称。

`FileChangeEntry` 增加三个可选字段（不影响现有调用方）：

```ts
diff?: string;          // 该文件的 unified diff（apply_patch 专用）
summary?: string;       // 卡片展开时 DiffPreview 的摘要
renamedFrom?: string;   // Move 的来源路径
```

新增两个解析器：

```ts
export interface ApplyPatchFileEntry {
  kind: "edit" | "write" | "remove";
  filePath: string;       // Move 用目标路径
  renamedFrom?: string;
  diff: string;
  adds: number;
  dels: number;
  lineCount: number | null; // Add 的新增行数
}

export function parseApplyPatchResult(raw: string): ApplyPatchFileEntry[];

export function parseApplyPatchArgs(
  args: Record<string, unknown>,
): ApplyPatchFileEntry[];
```

`parseApplyPatchResult` 规则：

1. 匹配 `^Applied patch:\s+\d+\s+files? changed`；不匹配返回 `[]`（错误态由卡片显示原文）
2. 逐行解析 `^(UPDATE|ADD|DELETE)\s+(.+?)(?:\s+->\s+(.+))?$`，遇到空行停止
3. 用 `^--- (.+)$` 且下一行 `^\+\+\+ \1$` 的成对头切分 diff，按源路径建 map
4. 映射：
   - `UPDATE` → `kind: "edit"`，diff 取源路径段，`adds/dels` 由该段统计
   - `ADD` → `kind: "write"`，diff 取该段，`lineCount` = 以 `+` 开头且非 `+++` 的行数
   - `DELETE` → `kind: "remove"`，diff 为空，adds/dels = 0
   - `UPDATE ... -> dest` → `kind: "write"`，`filePath = dest`，`renamedFrom = source`
5. 文件在清单里但 diff 缺失（例如删除）不报错，返回空 diff

`parseApplyPatchArgs` 规则：

1. 取 `args.patch` 字符串，按行扫描
2. `^\*\*\* Add File:\s*(.+)$` → write
3. `^\*\*\* Delete File:\s*(.+)$` → remove
4. `^\*\*\* Update File:\s*(.+)$` → edit；随后若出现 `^\*\*\* Move to:\s*(.+)$`，
   把该条目改成 write，`filePath = 目标`，`renamedFrom = 源`
5. 重复路径去重；无 `patch` 参数返回 `[]`

`collectFileChanges` 对 apply_patch 的分支：

```ts
if (name === APPLY_PATCH_TOOL_NAME) {
  const finished = !running && result.trim().length > 0;
  const parsedResult = finished ? parseApplyPatchResult(result) : [];
  // A failed patch has no `Applied patch:` header; fall back to the args
  // parse so the user still sees which files were attempted (status=error).
  const files = parsedResult.length ? parsedResult : parseApplyPatchArgs(args);
  for (const file of files) {
    entries.push({
      ...base,
      kind: file.kind,
      filePath: file.filePath,
      diffStat: file.kind === "edit" ? { adds: file.adds, dels: file.dels } : null,
      lineCount: file.lineCount,
      diff: file.diff,
      summary: file.kind === "edit" ? `UPDATE ${file.renamedFrom ?? file.filePath}` : undefined,
      renamedFrom: file.renamedFrom,
    });
  }
}
```

运行中与完成后的条目共用同一个 `callId`（卡片 `data-call-id` 相同），状态从 running 变 done；
文件数量可能从"args 里声明的"变成"result 里实际成功的"，以 result 为准。

### 2. `dashboard/src/components/chat/message_list_comps/FileChangeCard.vue`

- `editParsed` 改为优先使用 entry 自带数据：

```ts
const editParsed = computed(() => {
  if (props.entry.diff !== undefined) {
    return {
      cleanResult: toolResult.value,
      notice: null,
      diff: props.entry.diff,
      filePath: props.entry.filePath,
      summary: props.entry.summary ?? "",
    };
  }
  return parseFileEditResult(toolResult.value);
});
```

- Move 卡片：当 `entry.renamedFrom` 存在且 diff 为空时，在展开区显示
  `tm("fileChange.renamedFrom", { path: entry.renamedFrom })`
- 错误态：apply_patch 的 result 没有 `Error editing file:` 前缀，`errorText` 回退到原始
  `toolResult` 即可（现有逻辑对非 edit 类型已经这样）

### 3. `dashboard/src/components/chat/message_list_comps/ToolCallCard.vue`

- 图标：`astrbot_apply_patch` → `mdi-file-document-multiple-outline`
- 参数表：当工具是 apply_patch 且 key 为 `patch` 时，显示
  `` `${fileCount} file section(s)` ``，不展示原始多行文本（`fileCount` 复用
  `parseApplyPatchArgs(args).length`）
- 不为 apply_patch 增加 ToolCallCard 内嵌 diff：逐文件卡片已经承担该职责

### 4. `dashboard/src/views/SubAgentPage.vue`

`DEFAULT_INHERENT` 数组加入 `astrbot_apply_patch`，与后端 `dynamic_agents.tools_inherent` 对齐。

### 5. i18n

三个 locale 的 `features/chat.json` 的 `fileChange` 增加：

| locale | key | 值 |
|---|---|---|
| zh-CN | `renamedFrom` | `重命名自 {path}` |
| en-US | `renamedFrom` | `Renamed from {path}` |
| ru-RU | `renamedFrom` | `Переименовано из {path}` |

## 测试

`dashboard/src/utils/fileChangeTool.spec.ts` 增加：

- result 解析：4 种 kind、Move 目标路径与 `renamedFrom`、per-file `adds/dels`
- 头部缺失时返回 `[]`
- diff 切分不误伤内容行（文件里含 `---` 文本）
- args 解析：Update/Add/Delete/Move、重复路径去重、无 patch 参数
- `collectFileChanges`：apply_patch 运行中/完成后的条目数量、kind、callId 一致性
- 现有 file_edit 用例全部保持通过

`dashboard/src/components/chat/message_list_comps/FileChangeCard.spec.ts` 增加：

- apply_patch edit 条目使用 entry.diff，不调用 `parseFileEditResult`
- Move 条目显示 renamedFrom

验证命令：

```bash
cd dashboard
pnpm test -- fileChangeTool FileChangeCard
pnpm typecheck
```

## 不做

- 不修改后端 result 格式（现有文本已包含全部信息）
- 不修改回合汇总 `FileChangeSummaryCard`（`changed_files` 已覆盖）
- 不做 patch 文本语法高亮
- 不合并同一回合的多个 apply_patch 卡片
