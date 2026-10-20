# Dashboard GitDiff 侧栏「递归目录树渲染」— 设计文档

| 项目 | 内容 |
|------|------|
| 主题 | 将 GitDiff 侧栏的目录分组从"顶级目录"升级为"递归目录树"，以承载后端 git-status 端点 v2.21.1+ 返回的多级未跟踪文件 |
| 日期 | 2026-07-28（创建） |
| 作者 | elecvoid243 |
| 状态 | Design — 待用户 review |
| 关联插件 | `astrbot_plugin_spcode_toolkit` v2.21.1+（**源码路径**：`F:\github\astrbot_plugin_spcode_toolkit`） |
| 关联端点 | `GET /spcode/git-status`（定义见 `astrbot_plugin_spcode_toolkit/docs/api/webapi-git-status-api.md`） |
| 关联代码 | `dashboard/src/components/chat/message_list_comps/GitDiffBodyContent.vue`、`GitDiffSidebar.vue`、`GitDiffFileItem.vue`、`dashboard/src/composables/parseSpcodeGitStatus.ts`、`useSpcodeGitStatus.ts`、`useSpcodeNewFileLineCounts.ts` |
| 前置 spec | `docs/superpowers/specs/2026-06-17-chatui-git-diff-sidebar-design.md`（GitDiff 侧栏基础结构 + 顶级目录分组 §4.2.3） |

---

## 1. 背景与目标

### 1.1 现状

`astrbot_plugin_spcode_toolkit` 的 `GET /spcode/git-status` 端点在 v2.21.1 之前，对**未跟踪目录**只会返回目录名（如 `new_dir/`），不会展开目录下的文件。这导致：

- 前端 `GitDiffSidebar` 只能拿到一个占位条目（`path: "new_dir"`）
- 用户在 WebChat 侧栏只看到一个图标 + 文件名，看不到目录里到底有几个新文件
- 展开该行（`expanded` toggle）只能看到空 body（无 diff slice）

v2.21.1+ 后，端点行为变更为等效于 `git status --porcelain=v1 -z -u`，**递归展开**目录为所有叶子文件。`webapi-git-status-api.md §6` 明确要求前端按 path 的 `/` 分隔关系**自行构造树形目录**，或者**直接显示完整相对路径**。

### 1.2 痛点

后端升级后，前端解析层（`parseSpcodeGitStatus.ts`）和数据流（`useSpcodeGitStatus.ts`）已经能正确处理多级文件路径——这些改动是 2026-06-24 一并完成。但**渲染层**仍是 2026-06-28 的"顶级目录分组"实现（`GitDiffBodyContent.vue` 的 `topLevelDir` 函数）：

- 把 `new_dir/root.txt` 和 `new_dir/nested/leaf.txt` 都塞进同一个 `new_dir` section
- 叶子行内显示完整 POSIX 路径（`new_dir/nested/leaf.txt`）
- 当某顶级目录下嵌套了 10+ 个文件时，行与行之间的视觉层级完全靠完整路径字符串区分，**没有"目录"和"叶子"的层次感**

举例：用户在 WebChat 看到这样的列表——

```
src
  src/a.py
  src/b/c.py
  src/b/d/e.py
  src/b/d/f.py
  src/b/d/g.py
  src/h/i.py
  src/h/j.py
```

视觉上 7 行平铺，看不出 `src/b/` 是个子目录、`src/b/d/` 是更深一层的子目录。

### 1.3 目标

在 `GitDiffBodyContent.vue` 中把"顶级目录分组"升级为**递归目录树渲染**：

1. 顶级 section 仍存在（保持与 `2026-06-28` 的 section 折叠/批量选择/计数交互一致）
2. section 下若所有文件都在更深一层的子目录，渲染嵌套的子 section
3. 子 section 仍可折叠、可批量选择、可计数
4. 嵌套层数无限制（自然递归）
5. 叶子行（即 `GitDiffFileItem`）的 prop 签名零变化
6. 完全零后端改动（前端纯渲染层升级）

### 1.4 非目标

- ❌ **不**修改 spcode 插件代码（端点行为已在 v2.21.1+ 就绪）
- ❌ **不**修改 `parseSpcodeGitStatus.ts` / `useSpcodeGitStatus.ts` / `useSpcodeNewFileLineCounts.ts`（解析、状态机、内容抓取层均已正确处理递归路径）
- ❌ **不**修改 `GitDiffFileItem.vue`（叶子行不感知是否嵌套）
- ❌ **不**修改 `GitDiffSidebar.vue`（外层包装，传 prop 即可）
- ❌ **不**修改任何后端 API、i18n 加载器、build 配置
- ❌ **不**引入新依赖（不引 vue-virtual-scroller 等；当前文件数远未到大列表优化阈值）
- ❌ **不**改动 FileBrowserView（工作区的目录树与本次任务无关）

---

## 2. 设计决策（已与用户确认）

| # | 决策点 | 选择 | 理由 |
|---|--------|------|------|
| 1 | 树的形式 | **A. 目录嵌套树** | IDE 风格能反映多级目录结构；与现有"section 折叠/批量选择/计数"交互兼容；与 FileBrowserView 的目录树视觉对齐 |
| 2 | 外层顶级 section | **A1. 保留** | 改动小——只在 `GitDiffBodyContent.vue` 让 section 支持子 section；统计、批量选择、折叠都按子目录递归；结构上保留 `new_dir/` 这种顶级 group，与现有 UI 习惯一致 |
| 3 | 实现方式 | **方案 1：递归 section 单文件改造 + 抽出小组件 `DiffDirectoryNode.vue`** | Vue SFC 模板不支持自引用；抽出小组件让递归节点独立可测；`GitDiffBodyContent.vue` 仍负责 prop 透传、event 聚合、toolbar 等顶层职责；零新增 composable（`buildDirectorySections` 是纯函数，单独 `parseDirectorySections.ts`） |
| 4 | 叶子文件 path 显示 | **保持原样**（`GitDiffFileItem` 仍显示完整 POSIX 路径） | 不改叶子组件；缩进由外层 `padding-left: depth * 16px` 提供视觉层次；用户能在每个嵌套层级一眼看到相对路径 |
| 5 | section id 稳定性 | `dir:${fullPath}`（如 `dir:new_dir/nested`） | 跨刷新稳定，跨 worktree 切换稳定（路径以仓库根为基准）；现有 `collapsedGroups: Set<string>` ref 复用，键名空间无冲突 |
| 6 | 折叠状态 | 沿用现有 `collapsedGroups: Set<string>` | 改动最小；空 Set 表示全展开；scope/worktree 切换时无需清空（id 自带上下文） |
| 7 | 展开/折叠所有 | 递归遍历所有 `DiffSection` 收集/清空 id | `expandAll` / `collapseAll` 一行递归 + 一行 file-level emit |
| 8 | 选中状态计算 | 递归遍历 `files ∪ children` 累加 | 父 section 选满 = 全部叶子选中；子 section 部分选 = 父 indeterminate 三态保留 |
| 9 | 空 `files` section | 仍渲染 header（用于浏览），隐藏复选框（无叶子可勾），meta 仍显示递归累加的 `fileCount` | 例子：`src/b/c.py` + `src/d/e.py` → `src` section `files: []`，但仍有 `children: [b, d]`，header 不应消失 |
| 10 | 缩进 | 每级 `padding-left: 16px`（仅 header；body 内子节点继续叠加） | 视觉对比够强，又不会挤掉 path 文本 |
| 11 | A11y 嵌套 section | 沿用现有 `<button>` + `aria-expanded`；子 section 的复选框 `aria-label` 用 i18n key 拼接父目录 | 不引入 `role="tree"`（与现有 flat list 风格一致，避免 ARIA 复杂度） |
| 12 | i18n 新键 | 仅 1 个：`diffPreview.group.subdirAria`（子 section 复选框 aria-label） | 其他 label 复用现有 `diffPreview.group.root` |

---

## 3. 架构与文件改动

### 3.1 架构原则

- **零后端改动**：纯前端渲染层升级
- **递归数据模型**：`DiffSection` 增加 `children: DiffSection[]` 字段；`files` 与 `children` 二选一占主导（实际上可以同时存在）
- **递归 UI**：`DiffDirectoryNode.vue` 自渲染 `files`（叶子）+ 递归渲染 `children`（同组件实例）
- **零事件穿透损耗**：回调 prop 显式列出，不引入 event-bus
- **KISS**：不引入 reactive map / 复杂状态机；纯函数 + computed + `Set<string>`

### 3.2 文件改动清单

| 文件 | 改动 | 行数 |
|------|------|------|
| `dashboard/src/composables/parseDirectorySections.ts` | 新增 | ~60 行（纯函数 + `DiffSection` 类型） |
| `dashboard/src/components/chat/message_list_comps/DiffDirectoryNode.vue` | 新增 | ~250 行（递归节点） |
| `dashboard/src/components/chat/message_list_comps/GitDiffBodyContent.vue` | 修改 | -50 / +30（删除旧 sections 构造、改为渲染 `<DiffDirectoryNode>`；summary 累加改递归） |
| `dashboard/tests/buildDirectorySections.test.mjs` | 新增 | ~150 行（10+ 场景单测） |
| `dashboard/src/components/chat/message_list_comps/DiffDirectoryNode.spec.ts` | 新增 | ~180 行（DOM 渲染 / 折叠 / 选择三态） |
| `dashboard/tests/parseSpcodeGitStatus.test.mjs` | 新增 | ~100 行（解析覆盖补齐） |
| `dashboard/src/i18n/locales/en-US/features/chat.json` | 修改 | +1 键 |
| `dashboard/src/i18n/locales/zh-CN/features/chat.json` | 修改 | +1 键 |
| `dashboard/src/i18n/locales/ru-RU/features/chat.json` | 修改 | +1 键 |

**未受影响：**

- `GitDiffSidebar.vue` —— 顶层 prop 签名零变化
- `GitDiffFileItem.vue` —— 叶子行不感知嵌套
- `parseSpcodeGitStatus.ts` —— 解析层已正确处理多级路径
- `useSpcodeGitStatus.ts` / `useSpcodeNewFileLineCounts.ts` —— 状态机与内容抓取层不动
- 现有所有 spec / 测试（不破坏，仅新增）

### 3.3 数据模型

#### 3.3.1 `DiffSection` 接口（`parseDirectorySections.ts`）

```ts
export interface DiffSection {
  /** Stable id derived from the section's full path. Used as
   *  `collapsedGroups` Set key. Includes `dir:` prefix to avoid
   *  collisions with file-level expanded Set<string> entries. */
  id: string;
  /** The label rendered in the section header. Equals the last
   *  path segment for non-root sections; equals "<root>" for
   *  repo-root files. */
  label: string;
  /** Repo-relative POSIX path of this section, e.g. "new_dir/nested".
   *  Empty string for the synthetic <root> section. Used for
   *  debug + i18n + aria-label context. */
  fullPath: string;
  /** Number of files directly belonging to this section
   *  (i.e. files whose first path segment equals this section's
   *  label). Does NOT include files in children. */
  fileCount: number;
  /** Direct-file-only additions, used to render the section's own
   *  "+N −M" badge. Sub-section totals are recursively accumulated
   *  into the parent's badge by the consumer (see §3.4). */
  additions: number;
  /** Direct-file-only deletions, paired with additions. */
  deletions: number;
  /** Files that directly live in this section (no further '/'). */
  files: SpcodeGitDiffFile[];
  /** Sub-directory sections. Recursive: each child has the same
   *  shape (may have its own children). */
  children: DiffSection[];
  /** Visual badge kind: 'staged' / 'unstaged' / 'neutral'.
   *  Mirrors the active diff scope at the section's depth. */
  badgeKind: "staged" | "unstaged" | "neutral";
}
```

#### 3.3.2 `buildDirectorySections` 纯函数

```ts
/**
 * Group `list` into a tree of DiffSection by recursively splitting
 * on the first `/` of each path. Bucket insertion order is preserved
 * (= first-seen order in `list`), matching the previous flat-section
 * behavior so users see the same diff sequence they used to.
 *
 * Args:
 *   list:      flat list of files in any scope / scope-mix.
 *   parentPath: accumulated ancestor path; "" means the repo root.
 *   scope:     active GitDiffScope, drives the per-section badge.
 *
 * Returns:
 *   DiffSection[] of (possibly empty) sections, each with its
 *   directly-attached files + nested children. Empty list yields [].
 *
 * Notes:
 *   - Idempotent: same input → same output, including ids.
 *   - No exceptions thrown on any well-formed input (incl. empty).
 *   - For files with no '/': they become a direct child of <root>
 *     section (label = file basename, fullPath = filename).
 */
export function buildDirectorySections(
  list: SpcodeGitDiffFile[],
  parentPath: string,
  scope: GitDiffScope,
): DiffSection[];
```

**算法骨架：**

```ts
function buildDirectorySections(list, parentPath, scope) {
  const buckets = new Map<string, SpcodeGitDiffFile[]>();
  for (const f of list) {
    const slash = f.path.indexOf("/");
    const head = slash < 0 ? f.path : f.path.slice(0, slash);
    const rest = slash < 0 ? f.path : f.path.slice(slash + 1);
    // rest 已剥皮：递归时用 rest 作为该子节点下的 path
    const bucket = buckets.get(head) ?? [];
    bucket.push({ ...f, path: rest });
    buckets.set(head, bucket);
  }
  const sections: DiffSection[] = [];
  for (const [head, items] of buckets) {
    const fullPath = parentPath ? `${parentPath}/${head}` : head;
    const directFiles = items.filter((f) => !f.path.includes("/"));
    const nested = items.filter((f) => f.path.includes("/"));
    const children = buildDirectorySections(nested, fullPath, scope);
    let adds = 0;
    let dels = 0;
    for (const f of directFiles) {
      adds += f.additions;
      dels += f.deletions;
    }
    sections.push({
      id: `dir:${fullPath}`,
      label: head === "" ? ROOT_LABEL : head,
      fullPath,
      fileCount: directFiles.length,
      additions: adds,
      deletions: dels,
      files: directFiles,
      children,
      badgeKind: scopeBadge(scope),
    });
  }
  return sections;
}
```

**例子**（`scope = "unstaged"`）：

输入：

```
new_dir/root.txt
new_dir/nested/leaf.txt
src/foo.py
src/sub/bar.py
README.md
```

输出：

```ts
[
  {
    id: "dir:new_dir",
    label: "new_dir",
    fullPath: "new_dir",
    files: [new_dir/root.txt],   // path = "root.txt" after剥皮
    children: [
      {
        id: "dir:new_dir/nested",
        label: "nested",
        fullPath: "new_dir/nested",
        files: [new_dir/nested/leaf.txt],  // path = "leaf.txt"
        children: [],
      },
    ],
  },
  {
    id: "dir:src",
    label: "src",
    fullPath: "src",
    files: [src/foo.py],
    children: [
      {
        id: "dir:src/sub",
        label: "sub",
        fullPath: "src/sub",
        files: [src/sub/bar.py],
        children: [],
      },
    ],
  },
  {
    id: "dir:README.md",  // 注意：单文件无 '/' 进 <root> section
    label: "<root>",
    fullPath: "",
    files: [README.md],
    children: [],
  },
]
```

**边界：**

- `list = []` → `[]`
- `list = [foo.py]`（单文件无 `/`）→ `<root>` section, files: [foo.py]
- `list = [a/b/c.py, a/d/e.py]` → 顶级 a section, files: [], children: [b(→c.py), d(→e.py)]
- `list = [a/b/c.py, a/b/d.py]` → 顶级 a section, files: [], children: [b(→[c.py, d.py])]
- 深嵌套 `a/b/c/d/e.txt` → 链式递归 `a → b → c → d → files: [e.txt]`
- 空 children section（如 `a/b/c.py` + `a/d.py` 时顶级 `a` files 长度 = 0）→ 仍渲染 header，**不**隐藏；meta 显示递归累加 fileCount

### 3.4 `GitDiffBodyContent.vue` 改造点

#### 3.4.1 删除

```ts
function topLevelDir(path: string): string { ... }  // 旧实现
function buildDirectorySections(  // 旧实现（非递归）
  list: SpcodeGitDiffFile[],
  scope: GitDiffScope,
): DiffSection[] { ... }
```

#### 3.4.2 修改

- `sections` computed 改用新的递归入口：

```ts
const sections = computed<DiffSection[]>(() => {
  const list = files.value;
  if (list.length === 0) return [];
  return buildDirectorySections(list, "", props.selectedScope ?? "unstaged");
});
```

- `summary` 改为递归累加（让 sticky-header 顶部的总文件数 / 总 +/- 与树一致）：

```ts
const summary = computed(() => {
  let fileCount = 0, adds = 0, dels = 0;
  walkSections(sections.value, (s) => {
    fileCount += s.fileCount;
    adds += s.additions;
    dels += s.deletions;
  });
  return { fileCount, additions: adds, deletions: dels, dirCount: sections.value.length };
});
```

`walkSections` 是组件内的小 helper：

```ts
function walkSections(sections: DiffSection[], fn: (s: DiffSection) => void): void {
  for (const s of sections) {
    fn(s);
    if (s.children.length > 0) walkSections(s.children, fn);
  }
}
```

- `uniqueTopLevelDirCount` 仍保留（但只数顶级 section 数，因为 `dirCount` 含义没变）。

- 模板顶层循环替换：

```vue
<!-- 旧 -->
<div v-for="section in sections" :key="section.id" class="git-diff-section ...">
  <header>...</header>
  <body>
    <GitDiffFileItem v-for="f in section.files" ... />
  </body>
</div>

<!-- 新 -->
<DiffDirectoryNode
  v-for="section in sections"
  :key="section.id"
  :section="section"
  :depth="0"
  :expanded="expanded"
  :collapsed-groups="collapsedGroups"
  :is-dark="isDark"
  :show-stage="showStageButton"
  :show-unstage="showUnstageButton"
  :show-restore="showRestoreButton"
  :new-file-paths="newFilePaths"
  :is-selected="isSelectedForPath"
  :selectable-aria-label="selectableAriaLabelFor"
  :on-toggle-group="toggleGroup"
  :on-toggle-select="toggleSelect"
  :on-toggle-group-select="toggleGroupSelect"
  :is-section-fully-selected="isSectionFullySelected"
  :is-section-partially-selected="isSectionPartiallySelected"
  :is-staging-for-path="isStagingForPath"
  :is-unstaging-for-path="isUnstagingForPath"
  :is-discardable-for="discardableFor"
  :on-file-toggle="(p) => emit('toggle', p)"
  :on-file-stage="(p) => emit('stage', p)"
  :on-file-unstage="(p) => emit('unstage', p)"
  :on-file-restore="(p) => emit('restore', p)"
  :on-file-open="(p) => emit('open-file', p)"
  :on-file-discard-hunk="onDiscardHunk"
  :scope="selectedScope"
  @stage="emit('stage', $event)"
  @unstage="emit('unstage', $event)"
  @restore="emit('restore', $event)"
  @open-file="emit('open-file', $event)"
  @toggle="emit('toggle', $event)"
  @select="toggleSelect($event.path, $event.selected)"
  @discard-hunk="onDiscardHunk"
/>
```

- `expandAll` / `collapseAll` 递归遍历：

```ts
function expandAll(): void {
  collapsedGroups.value = new Set();
  for (const f of files.value) {
    if (!props.expanded.has(f.path)) emit("toggle", f.path);
  }
}

function collapseAll(): void {
  const ids = new Set<string>();
  walkSections(sections.value, (s) => ids.add(s.id));
  collapsedGroups.value = ids;
  for (const f of files.value) {
    if (props.expanded.has(f.path)) emit("toggle", f.path);
  }
}
```

- `isSectionFullySelected` / `isSectionPartiallySelected` 改递归：

```ts
function isSectionFullySelected(section: DiffSection): boolean {
  // 1) 所有直接 files 选中
  for (const f of section.files) {
    if (!selectedFiles.value.has(f.path)) return false;
  }
  // 2) 所有 child sections 完全选中
  for (const c of section.children) {
    if (!isSectionFullySelected(c)) return false;
  }
  // 3) 空 section (无 files 无 children) 视为"未选"，避免 header 复选框看似可点
  return section.files.length + section.children.length > 0;
}

function isSectionPartiallySelected(section: DiffSection): boolean {
  let any = false;
  let all = true;
  // 累计子 section 状态
  for (const c of section.children) {
    if (isSectionFullySelected(c)) {
      any = true;
    } else if (isSectionPartiallySelected(c)) {
      any = true;
      all = false;
    } else {
      all = false;
    }
  }
  // 累计直接 files 状态
  for (const f of section.files) {
    if (selectedFiles.value.has(f.path)) any = true;
    else all = false;
  }
  return any && !all;
}
```

- `toggleGroupSelect` 改递归：

```ts
function toggleGroupSelect(section: DiffSection, next: boolean): void {
  const updated = new Set(selectedFiles.value);
  // 递归遍历收集所有 file path
  walkSections([section], (s) => {
    for (const f of s.files) {
      if (next) updated.add(f.path);
      else updated.delete(f.path);
    }
  });
  selectedFiles.value = updated;
}
```

`selection-prune` watcher（line ~480）保持不变：仍基于 `files.value.map((f) => f.path)`，对树形结构透明。

#### 3.4.3 不变

- `files` computed（line ~196）
- `summary.fileCount` / `summary.additions` / `summary.deletions` 名称不变；只是内部实现改递归
- 模板 `state.kind === 'loading'` / `state.kind === 'error'` / 空状态分支
- sticky-header（summary + toolbar）整体不变
- i18n 键名空间

### 3.5 `DiffDirectoryNode.vue` 设计

#### 3.5.1 Props（最小化穿透）

```ts
interface Props {
  section: DiffSection;
  depth: number;
  expanded: Set<string>;
  collapsedGroups: Set<string>;
  isDark: boolean;
  // Gate signals (booleans from parent)
  showStage: boolean;
  showUnstage: boolean;
  showRestore: boolean;
  newFilePaths?: ReadonlySet<string>;
  // Selection helpers
  isSelected: (path: string) => boolean;
  isStagingForPath: (path: string) => boolean;
  isUnstagingForPath: (path: string) => boolean;
  isDiscardableFor: (f: SpcodeGitDiffFile) => boolean;
  isSectionFullySelected: (s: DiffSection) => boolean;
  isSectionPartiallySelected: (s: DiffSection) => boolean;
  // i18n helpers
  selectableAriaLabel: (path: string) => string;
  // Callbacks
  onToggleGroup: (id: string) => void;
  onToggleSelect: (path: string, next: boolean) => void;
  onToggleGroupSelect: (s: DiffSection, next: boolean) => void;
  onFileToggle: (path: string) => void;
  onFileStage: (path: string) => void;
  onFileUnstage: (path: string) => void;
  onFileRestore: (path: string) => void;
  onFileOpen: (path: string) => void;
  onFileDiscardHunk: (params: {
    file: string;
    hunkIndex: number;
    patchText: string;
    scope: GitDiffScope;
  }) => void;
  scope?: GitDiffScope;
}
```

#### 3.5.2 Emits（与 GitDiffFileItem 对齐）

```ts
const emit = defineEmits<{
  (e: "toggle", path: string): void;
  (e: "restore", path: string): void;
  (e: "stage", path: string): void;
  (e: "unstage", path: string): void;
  (e: "open-file", path: string): void;
  (e: "discard-hunk", params: { ... }): void;
  (e: "select", payload: { path: string; selected: boolean }): void;
}>();
```

`onFile*` 回调与 emit 二选一：当前实现统一走 emit（与 `GitDiffFileItem` 一致），prop 中 `onFile*` 留作可选（用于未来 inline 拦截）。

#### 3.5.3 模板结构

```vue
<div
  class="git-diff-section is-dir"
  :class="[
    `is-${section.badgeKind}`,
    { 'is-collapsed': collapsedGroups.has(section.id) },
  ]"
  :style="{ '--depth': depth }"
>
  <button
    type="button"
    class="git-diff-section-header"
    :aria-expanded="!collapsedGroups.has(section.id)"
    @click="onToggleGroup(section.id)"
  >
    <v-icon
      size="14"
      class="git-diff-section-chevron"
      :class="{ expanded: !collapsedGroups.has(section.id) }"
    >mdi-chevron-right</v-icon>

    <!-- per-section "select all" checkbox (only when selectable + non-empty) -->
    <span
      v-if="anyBulkAffordance && sectionHasContent"
      class="git-diff-section-check"
      @click.stop
    >
      <input
        type="checkbox"
        :checked="isSectionFullySelected(section)"
        :indeterminate.prop="isSectionPartiallySelected(section)"
        :aria-label="tm('diffPreview.group.subdirAria', { parent: parentLabel, name: section.label })"
        @change="onToggleGroupSelect(section, ($event.target as HTMLInputElement).checked)"
      />
    </span>

    <span class="git-diff-section-dot" :class="`is-${section.badgeKind}`" />
    <span class="git-diff-section-label">{{ section.label }}</span>
    <span class="git-diff-section-meta">
      <span class="git-diff-section-count">{{ recursiveFileCount(section) }}</span>
      <span class="git-diff-section-stats">
        <span class="git-diff-add">+{{ recursiveAdditions(section) }}</span>
        <span class="git-diff-del">−{{ recursiveDeletions(section) }}</span>
      </span>
    </span>
  </button>

  <div v-show="!collapsedGroups.has(section.id)" class="git-diff-section-body">
    <!-- 直接 files -->
    <GitDiffFileItem
      v-for="f in section.files"
      :key="f.path + ':' + f.status"
      :file="f"
      ...
    />

    <!-- 递归 children -->
    <DiffDirectoryNode
      v-for="child in section.children"
      :key="child.id"
      :section="child"
      :depth="depth + 1"
      ...
    />
  </div>
</div>
```

`recursiveFileCount` / `recursiveAdditions` / `recursiveDeletions` 均为组件内 `computed` helper，用闭包包住 `walkSections`：

```ts
const recursiveFileCount = (s: DiffSection): number => {
  let n = s.fileCount;
  for (const c of s.children) n += recursiveFileCount(c);
  return n;
};
```

`parentLabel` 通过 `inject` 拿到（祖先链递归时显式注入）：

```ts
const PARENT_LABEL_KEY = "spcode:diffDirectoryNodeParentLabel";
const parentLabel = inject(PARENT_LABEL_KEY, "");
provide(PARENT_LABEL_KEY, section.label);  // 子节点拿到的就是自己的父 label
```

`sectionHasContent` = `section.files.length > 0 || section.children.length > 0`，空 section 也会有 children，所以始终 true（防御性判断）。

#### 3.5.4 样式

```scss
.git-diff-section {
  /* depth 缩进：每级 +16px */
  padding-left: calc(var(--depth, 0) * 16px);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.06);
}
.git-diff-section:last-of-type {
  border-bottom: 0;
}
.git-diff-section-header {
  /* 与旧版一致 */
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 14px;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.75);
  background: rgba(var(--v-theme-on-surface), 0.025);
  border: 0;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.06);
  cursor: pointer;
  text-align: left;
  user-select: none;
}
/* 嵌套 section header 颜色稍淡 */
.git-diff-section[style*="--depth"]:not([style*="--depth: 0"])
  .git-diff-section-header {
  color: rgba(var(--v-theme-on-surface), 0.65);
}
.git-diff-section-body {
  /* 内部继续缩进 */
}
```

> 注：`--depth` 通过 `:style` 绑定，CSS 选择器无法直接读 CSS 变量值（无 attr() 支持），但 `padding-left: calc(var(--depth) * 16px)` 直接生效。子节点继承父节点的 padding 即可视觉叠加。

#### 3.5.5 父组件 / 子组件的职责边界

- `DiffDirectoryNode` 不知道"哪个 scope 处于激活"——只接收 `section.badgeKind` 和 `showStage/showUnstage/showRestore` 布尔值
- 折叠状态 / 选中状态通过 `Set<string>` / 函数 prop 显式传递
- 不依赖 `useSpcodeProjectStatus` / `useModuleI18n` 之外的全局状态（项目状态仍可注入，因 `GitDiffFileItem` 也用）

---

## 4. i18n

仅 1 个新键（3 语言 × 1 键 = 3 处改动）：

```json
// en-US
"diffPreview": {
  "group": {
    "root": "Repository root",
    "subdirAria": "Select all files in {parent}/{name}"
  }
}

// zh-CN
"diffPreview": {
  "group": {
    "root": "仓库根目录",
    "subdirAria": "全选 {parent}/{name} 下的所有文件"
  }
}

// ru-RU
"diffPreview": {
  "group": {
    "root": "Корень репозитория",
    "subdirAria": "Выбрать все файлы в {parent}/{name}"
  }
}
```

`parent` 在顶级 section 时为空字符串，i18n 字符串里 `{parent}/{name}` 会渲染为 `/<label>`，可接受（顶级本身就是仓库根，子 section 才用到这个键）。

更干净的做法：`DiffDirectoryNode` 内判断 `depth === 0` 时只传 `{name}`，但这会增加组件复杂度。**当前 spec 接受 `/<label>` 渲染结果**（与现有 "项目根" 习惯一致；空前缀视觉上仍然清晰）。

---

## 5. 错误处理

| 错误 | 行为 |
|------|------|
| `buildDirectorySections` 输入含 null/undefined path | 跳过（`f.path` 必为 string；parseSpcodeGitDiff 已保证） |
| `buildDirectorySections` 输入空数组 | 返回 `[]`（sections computed 已短路） |
| `DiffDirectoryNode` 接收空 `files` 和空 `children` | 仍渲染 header（防御性，避免空 section 静默消失）；复选框条件 `sectionHasContent` 隐藏 |
| `collapsedGroups` Set 残留旧 id | 失配但无害——该 section 默认展开 |
| `useSpcodeNewFileLineCounts` 抓取某新文件失败 | 该行的 `additions = 0`, `slice = null`——body 渲染 "noContent" 占位（与现有行为一致） |
| 嵌套过深（理论 100+ 级） | 渲染正常；仅 CSS 缩进可能溢出（sidebar 横向滚动；与 FileBrowserView 的目录树行为一致） |

---

## 6. 测试

### 6.1 单元测试（`tests/buildDirectorySections.test.mjs`）

纯函数，直接调：

| 场景 | 输入 | 期望 |
|------|------|------|
| 空数组 | `[]` | `[]` |
| 单文件无 `/` | `[{ path: "foo.py" }]` | `[<root>: { files: [foo.py], children: [] }]` |
| 多文件同顶级 | `[{ path: "a/x.py" }, { path: "a/y.py" }]` | `[a: { files: [x, y], children: [] }]` |
| 多级嵌套 | `[{ path: "a/b/c.py" }]` | `[a: { files: [], children: [b: { files: [c.py], children: [] }] }]` |
| 混合（顶级 + 子目录） | `[{ path: "a/x.py" }, { path: "a/b/y.py" }]` | `[a: { files: [x.py], children: [b: { files: [y.py] }] }]` |
| 极深嵌套 | `[{ path: "a/b/c/d/e.txt" }]` | 4 层 chain, 最深 files: [e.txt] |
| `<root>` 标识 | `[{ path: "README.md" }]` | label === "`<root>`" |
| id 稳定（同输入两调） | run 1 + run 2 | 所有 section.id 字符串相等 |
| fileCount 仅计直接文件 | nested scenario | `a.fileCount === 0`, `a.children[0].fileCount === 1` |
| additions / deletions 仅计直接文件 | mixed scenario | `a.additions` === x.py 的 additions, 不含 y.py |
| 桶顺序保留 | `[a/x, b/y, a/z]` | sections 顺序: `[a, b]`（a 先出现） |
| scope → badgeKind | 3 scope × 1 file | staged/unstaged/neutral 各 1 |
| 无穷递归 | `[a, a/b, a/b/c]` | 链式终止，最深 3 层 |

### 6.2 组件测试（`DiffDirectoryNode.spec.ts`）

Vue Test Utils + happy-dom 风格（与 `GitIgnoreEditor.spec.ts` 一致）：

| 场景 | 断言 |
|------|------|
| 渲染顶级 section（depth=0） | `padding-left === "0px"` |
| 渲染嵌套 section（depth=1） | 父节点 + 子节点都在 DOM；父 padding 含 16px |
| 折叠父 → 子不在 DOM | `wrapper.findAll('.git-diff-section').length === 1` |
| 展开父 → 子在 DOM | `wrapper.findAll('.git-diff-section').length === 2` |
| section 级复选框三态 | `indeterminate` prop / `checked` attr 切换 |
| 复选框 aria-label 含祖先路径 | `getAttribute('aria-label')` 匹配 `/(parent)/name/` |
| emit toggle 事件 | `wrapper.emitted('toggle')[0]` === `f.path` |
| emit select 事件 | `wrapper.emitted('select')[0][0]` === `{ path, selected: true }` |
| emit stage/unstage/restore/open-file/discard-hunk 事件 | 5 个事件各一次 |
| section.files = [] 时仍渲染 | header 存在，files 区无 `<GitDiffFileItem>` |
| 复选框在空 section（无 files 无 children）时隐藏 | `find('.git-diff-section-check').exists() === false` |

### 6.3 解析补齐（`tests/parseSpcodeGitStatus.test.mjs`）

之前无单测，新增覆盖：

| 场景 | 输入 | 期望 |
|------|------|------|
| scope normalization | `"modified_both"` / `"untracked"` / `"intent_to_add"` / 非法值 | 前 3 保留；非法值 → `null` |
| recursive untracked paths | `[new_dir/nested/leaf.txt, new_dir/root.txt]` | files 数组保留所有 entry；summary.untracked === 2 |
| truncated 标志 | `{ truncated: true, max_files: 1000 }` | snapshot.truncated === true |
| upstream 解析 | `{ branch: "origin/main", ahead: 1, behind: 0 }` | snapshot.meta.upstream 包含全部字段 |
| 失败 reason | `{ loaded: false, reason: "no_project_loaded" }` | snapshot.meta.reason === "no_project_loaded" |
| `x_status` / `y_status` 透传 | `"??", "??"` | files[i].x_status === "??", y_status === "??" |

### 6.4 回归

- `pnpm test`：所有现存 spec 仍通过
- `i18n.completeness.spec.ts`：新键三种语言齐
- `parseSpcodeGitDiff.test.mjs`：解析层未改
- `restoreRefreshesGitStatus.test.mjs`：未改

---

## 7. 端到端验收清单

1. 在 `pnpm dev` 启动 dashboard
2. 加载 spcode 项目（带 git 仓库）
3. 工作区创建多级未跟踪目录：
   ```bash
   mkdir -p new_dir/nested
   echo "root" > new_dir/root.txt
   echo "leaf" > new_dir/nested/leaf.txt
   mkdir -p src/sub
   echo "x" > src/foo.py
   echo "y" > src/sub/bar.py
   echo "R" > README.md
   ```
4. 切到 GitDiff 侧栏，scope = unstaged
5. 预期渲染：

```
▶ new_dir
    root.txt
  ▼ new_dir/nested
        leaf.txt
▶ src
    foo.py
  ▼ src/sub
        bar.py
▶ (root)
    README.md
```

6. 折叠 `new_dir` → `new_dir/nested` 消失
7. 展开 → 子节点出现
8. 勾选 `new_dir` 复选框 → `root.txt` 和 `leaf.txt` 都进入 selectedFiles
9. 点击 "暂存" → 两条都进入 stagedFiles
10. 切到 staged view → 两条不再显示
11. 切到 all view → 两条出现在 staged section
12. 修改 `README.md`（让 diff 出现）→ `<root>` section 出现
13. 顶部 sticky summary 的 fileCount / additions / deletions 与所有叶子一致
14. 折叠全部 → 整个树收缩为一行
15. 展开全部 → 全部行可见
16. 切到 docs tab → 切回 diff → 折叠状态保留

---

## 8. 风险与缓解

| 风险 | 缓解 |
|------|------|
| Vue SFC 模板自引用不支持 | 已设计：抽出 `DiffDirectoryNode.vue` |
| 嵌套过深导致 sidebar 横向溢出 | 缩进 16px × depth；4 级 ≈ 64px；sidebar 宽度足够 |
| 旧 `collapsedGroups` id（如果有）失配 | 无害：默认展开；下次刷新后自然清空 |
| 旧 `selectAllInGroup` i18n 键被替换 | 保留作 fallback（顶级 section 仍用） |
| 大量 section 节点影响渲染性能 | 50+ 罕见；virtual scroll 留给后续 |
| `walkSections` 反复递归累加 O(n²) | 节点数 < 100，OK；与现有 O(n²) 选择计算一致 |
| `DiffDirectoryNode` 的 prop 数量膨胀 | 已用 callback + function prop 而非逐字段；与 `GitDiffFileItem` 风格一致 |
| emit 命名冲突（GitDiffBodyContent 也有 toggle / stage 等 emit） | 父组件在 `<DiffDirectoryNode @toggle="emit('toggle', $event)">` 透传；语义不变 |
| 顶部 summary 与递归累加不一致 | `summary` 改用同一 `walkSections` 路径 |
| 改动 GitDiffBodyContent.vue 时意外破坏其他 emit | 模板替换前保留原 emit 链；改完跑现有 spec 验证 |

---

## 9. 实施顺序（TDD）

1. **新建 `parseDirectorySections.ts` + `tests/buildDirectorySections.test.mjs`**
   - 跑红
   - 实现 `buildDirectorySections`
   - 跑绿
   - 覆盖 §6.1 全部 13 个场景

2. **新建 `DiffDirectoryNode.vue` 骨架 + `DiffDirectoryNode.spec.ts`**
   - 跑红（spec 期望的 props/emits 还未实现）
   - 实现组件
   - 跑绿
   - 覆盖 §6.2 全部 11 个场景

3. **改 `GitDiffBodyContent.vue`**
   - 用 `buildDirectorySections` 替换 `buildDirectorySections`
   - 模板顶层循环改为 `<DiffDirectoryNode>`
   - `summary` 改用 `walkSections` 递归累加
   - `isSectionFullySelected` / `isSectionPartiallySelected` / `toggleGroupSelect` 递归化
   - `expandAll` / `collapseAll` 改用 `walkSections`
   - 跑 `pnpm test`：全绿

4. **补 `tests/parseSpcodeGitStatus.test.mjs`**
   - 覆盖 §6.3 全部 6 个场景

5. **i18n 补全**
   - 3 个语言各加 `diffPreview.group.subdirAria`
   - 跑 `i18n.completeness.spec.ts` 验证

6. **手测**
   - §7 全部 16 步

7. **lint / format**
   - `pnpm exec eslint --fix src/components/chat/message_list_comps/DiffDirectoryNode.vue src/components/chat/message_list_comps/GitDiffBodyContent.vue src/composables/parseDirectorySections.ts`

---

## 10. 参考

- spcode 端点文档：`F:\github\astrbot_plugin_spcode_toolkit\docs\api\webapi-git-status-api.md` §6
- 现有顶级目录分组 spec：`docs/superpowers/specs/2026-06-17-chatui-git-diff-sidebar-design.md` §4.2.3
- 现有 section 折叠 + 批量选择 spec：`docs/superpowers/specs/2026-06-28-git-diff-ui-improvements-design.md`（如存在）
- 同模式参考（递归树）：`FileBrowserView.vue` + `FileTreeList.vue` + `FileBrowserEntryList.vue`（工作区侧栏，但单层）
- 姊妹 spec（diff 渲染）：`docs/superpowers/specs/2026-07-07-hunk-discard-design.md`（结构、决策表、章节格式参考）
