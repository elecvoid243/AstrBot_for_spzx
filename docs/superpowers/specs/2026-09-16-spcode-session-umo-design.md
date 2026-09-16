# spcode 会话级 umo 设计（阶段 1–3）

- 日期：2026-09-16
- 状态：设计已确认（开关 A = 严格失败；开关 B = 共享 ref 收紧为活跃会话），待实施
- 来源：worktree 创建跨会话 bug 的后续排查（该 bug 的传输层修复已落地，见 §1.1）
- 基线：`dashboard/src/composables/useSpcodeProjectStatus.ts`（模块级单例，2026-08-15 加固版）、`dashboard/src/components/chat/Chat.vue:2645`（`currentUmo` computed）

## 1. 背景与问题

### 1.1 已修复的前置（不在本设计范围）

用户报告"在会话 A 点 `+` 创建 worktree，结果建到了会话 B 的项目里"。根因是**传输层**：

| 环节 | 缺陷 | 状态 |
|---|---|---|
| 插件 `_wrap` 适配器 | POST 端点的 `umo` 只从 JSON body 读，前端却只放在 `?umo=` | 已修（body 优先 / query 兜底） |
| 插件 `_git_endpoint_preflight` | umo 缺失时回落到"所有会话中最近加载的项目" | 已修（5 个写端点 `require_session_umo=True` → `no_project_loaded`） |
| 前端 `useSpcodeWorktrees` | add/remove/lock/unlock 只把 umo 放 query | 已修（umo 进 body） |

### 1.2 遗留的结构性问题（本设计要解决）

传输修复只保证"送出去的 umo 不被丢"，**不保证送出去的就是对的那个**。三条隐患：

| 编号 | 问题 | 证据 |
|---|---|---|
| **R1** | **竞态**：`refresh(A)` 与 `refresh(B)` 可任意顺序落地，最后写入者获胜，没有"发起者是否仍是活跃会话"的守卫 | `useSpcodeProjectStatus.ts:14` 的 `inflightRefresh` 只按 umo 去重；`:80` 无条件覆盖共享 `status.value` → 共享 ref 语义 = 最后一次成功写入者 |
| **R2** | **`setLoaded` 混配**：只设 `loaded` + `directory`，**保留旧 umo** | `useSpcodeProjectStatus.ts:112` 的 `setLoaded(directory, loadedAt)` 签名不含 umo；调用点 `ChatInput.vue:1537` |
| **R3** | **读者隐式信任全局**：所有读点默认"共享状态就是这个会话的"，无任何校验 | 实测 `spcodeStatus.status.value.*` **97 处**（非 spec），见 §1.3 |

R1/R2 是"共享状态可能装错会话"的直接成因；R3 决定了**一旦装错，错误会传播到所有会改仓库的路径**（而不仅是显示）。

### 1.3 现状盘点（实测）

| 指标 | 数值 | 说明 |
|---|---|---|
| `status.value.*` 读点 | **97 处 / 非 spec** | `.umo` 71、`.loaded` 14、`.directory` 10、`.bootId` 2 |
| `.umo` 读点分布 | **21 文件 / 71 处** | `GitDiffSidebar.vue` 27（含模板 `:umo=` 4 处）；composable 内部 38（17 个文件）；子树组件 6（`GitDiffBodyContent` 4 / `GitDiffFileItem` 1 / `WorktreeCreateDialog` 1） |
| `.directory` 读点 | 10 处 / 7 文件 | `GitDiffSidebar` 3、`useSpcodeGitRepoProbe` 2、`useSpcodeWorktrees`/`GitStatus`/`GitDiff`/`GitBranches`/`WorktreeCreateDialog` 各 1 |
| 写入点 | **仅 2 文件** | `Chat.vue`（refresh ×4、reset ×2）、`ChatInput.vue`（setLoaded / setUnloaded / refresh ×2 / reset） |
| 写入所用 umo | 全部是**活跃会话**的 umo | 4 个 `refresh` 调用点均在 `sessionId === currSessionId` 或其等价路径内 |
| 挂载范围 | 所有会改仓库的 composable 只在 `Chat.vue` 子树内使用 | 唯一例外：`ProjectView.vue` 的 `SpcodeProjectStatusChip`（纯展示，不碰仓库） |

**关键约束（已核实）**：`git-*` / `file-*` / `worktrees` 三类 composable 的使用者全部位于 `Chat.vue` 模板树内（`ChatInput`、`GitDiffSidebar` 及其子树 `DocumentManager`/`FileBrowserView`/`GitChangelogDialog`/`BinaryPreview`/`DocumentHistoryPanel`）。因此"在 `Chat.vue` provide 会话上下文"可覆盖全部**写**路径。

**既有正确先例**：`components/chat/TerminalView.vue` 通过 **prop** 接收 umo（`L127` 声明、`L382/395/481` 使用），后端终端端点缺 umo 时 `missing_umo` 硬失败（`tools/webapi/terminal.py:69/115/178`）。本设计是把这套"显式身份 + 缺则失败"推广到 spcode 的其余功能，而非引入新范式。

## 2. 目标与非目标

### 2.1 目标

- **G1 身份显式**：侧边栏子树及其驱动的 composable，其 `umo` 来自"该侧边栏所属会话"，不再隐式读全局单例。
- **G2 写操作绝不猜**（开关 A）：身份不可解析（`resolveCurrentUmo()` 返回 null）时，写操作以 `no_project_loaded` 失败，绝不回落到别会话项目。
- **G3 共享状态收敛**（开关 B）：共享 ref 恒等于**活跃会话**的条目；乱序响应被丢弃；`setLoaded` 原子写入 umo + directory。
- **G4 零接口扩散**：composable 签名不变（走环境上下文），既有 13 个依赖单例的 spec **不需要改写**。
- **G5 不变量有测试**：把"单例被污染成 B、侧边栏属于 A → 请求仍带 A"钉成回归测试。

### 2.2 非目标

- 不改后端任何接口契约（上一轮已完成，本设计零后端改动）。
- 不改 chip 的展示语义：`SpcodeProjectIndicator` / `SpcodeProjectStatusChip` / `FileAccessModeChip` / `ChatInput` / `ProjectLoadDialog` 继续显示"活跃会话"。
- 不动终端功能（已是 prop 显式传参 + 后端硬失败）。
- 不废弃 `useSpcodeProjectStatus` 单例（chip 仍以其为数据源）。
- 不把 provider 变成强制（保留回落路径，仅以 dev 警告显式化）。

## 3. 设计

### 3.1 阶段重排（相对初版方案的修正）

初版方案把"上下文管道"放阶段 1、"per-umo 状态"放阶段 2。**顺序反转**：管道阶段若 `umo` 取会话值、而 `directory` 仍取单例值，会短暂出现 `{umo: 新会话, directory: 旧会话}` 的错配态（`WorktreeCreateDialog` 默认路径正读 `directory`），造出一个比现状更隐蔽的中间态。

因此执行顺序为：**阶段 1 = per-umo 状态**（行为修复）→ **阶段 2 = 上下文管道**（结构修复）→ **阶段 3 = 护栏 + 回归测试**。

### 3.2 阶段 1：per-umo 状态与守卫（`useSpcodeProjectStatus` 重构）

数据模型：

```ts
// 每个 umo 一个条目；共享 ref 仅镜像"活跃会话"那一条
const entries = reactive(new Map<string, SpcodeProjectStatus>())
const status = ref<SpcodeProjectStatus>({ ...EMPTY_STATUS })   // = entries[activeUmo]
let activeUmo: string | null = null
const requestSeq = new Map<string, number>()                    // 乱序守卫

function setActiveUmo(umo: string | null): void
  // Chat.vue 会话切换时调用；同步 status.value = entries[umo] ?? EMPTY

async function refresh(umo: string | null): Promise<SpcodeProjectStatus>
  // - umo 为空 → 不请求，写 EMPTY（保持 2026-08-15 的 null-umo 加固语义）
  // - seq = ++requestSeq[umo]；await 后若 seq !== requestSeq[umo] → 丢弃本次响应
  // - 结果写 entries[umo]；仅当 umo === activeUmo 时镜像到共享 ref
  // - 返回该条目（供 auto-load 等调用方直接消费，不再读共享 ref）

function statusFor(umo: MaybeRefOrGetter<string | null>): ComputedRef<SpcodeProjectStatus>
  // 响应式只读访问器：computed(() => entries.get(toValue(umo) ?? '') ?? EMPTY)

function setLoaded(umo: string, directory: string, loadedAt?: number): void
  // 原子写 { umo, directory, loaded: true }，消除 R2 的混合态
function setUnloaded(umo: string | null): void
function reset(): void   // 清 activeUmo + 共享 ref（不动 entries 缓存）
```

现状锚点（改造前）：`refresh()`（`:55`）返回 `void`、去重表 `inflightRefresh`（`:14`）存 `Promise<void>`、`setLoaded`（`:112`）签名无 umo、`setUnloaded`（`:123`）保留旧 umo。

要点：

- `refresh` 的**返回值**替代"写完再读共享 ref"的用法（`Chat.vue:2541-2546` 的 bootId 判定改为 `const s = await refresh(umo); isSessionLoadedTag(sessionId, projectId, s.bootId)`），消除"刷新期间切走会话 → 读到别人 bootId → dirty-tag 失效 → 重复静默加载"。
- 保留既有 `inflightRefresh` 同-umo 去重，但共享同一份结果，保证 `entries` 对所有并发调用方可见。
- 开关 B 的落点：`status.value` **只在 `setActiveUmo` 与 `umo === activeUmo` 的 `refresh` 回调里**被改写。

### 3.3 阶段 2：会话上下文（新增 `useSpcodeSession.ts`）

```ts
export interface SpcodeSessionContext {
  umo: ComputedRef<string | null>        // 会话身份（严格：可能为 null）
  directory: ComputedRef<string | null>  // 该会话已加载项目根
}
export const SPCODE_SESSION_KEY: InjectionKey<SpcodeSessionContext>
export function provideSpcodeSession(ctx: SpcodeSessionContext): void
export function useSpcodeSession(): SpcodeSessionContext
```

- **provide 点**：`Chat.vue`（其模板树覆盖全部写路径，见 §1.3）
  ```ts
  provideSpcodeSession({
    umo: currentUmo,                                                   // 开关 A：严格，不做单例回落
    directory: computed(() => spcodeStatus.statusFor(currentUmo).value.directory),
  })
  ```
- **回落规则**：无 provider 时返回基于共享状态的上下文（`umo: computed(() => status.value.umo)`）。覆盖两类调用方：`ProjectView` 的纯展示 chip、既有 spec。这是 G4（既有 spec 不改写）的实现基础。
- **composable 内部迁移**：17 个 composable 中 `spcodeStatus.status.value.umo` → `useSpcodeSession().umo.value`（1 行替换，签名不变）。`inject()` 在这些 composable 均于组件 `setup()` 内被调用，可正常解析。
- **组件迁移**：`GitDiffSidebar.vue`（27）+ 子树 3 个组件（6）→ 同上；4 处模板 `:umo=` 改为注入值。
- **不迁移**：`.loaded`（14 处）、`.bootId`（2 处，其中 1 处随 §3.2 改为 `refresh` 返回值）、chip 相关读点。

### 3.4 行为变化（开关 A 的可见后果）

| 场景 | 今天 | 改后 |
|---|---|---|
| 新建会话（`resolveCurrentUmo()` 为 null）点创建 worktree | 用上一个会话的 umo（后端修复前会建错仓库，修复后仍可能指向错误仓库） | 客户端 guard → `no_project_loaded`，提示"项目未载入" |
| 切换会话后、该会话 `project-status` 尚未返回 | 读到上一个会话的 `directory` / `umo` | `umo` = 新会话（正确）；`directory` = `statusFor(新会话)` 的当前值（可能短暂为 null → 默认路径建议为空，不误导） |
| 读路径（diff/log/status）在身份未解析时 | 显示上一个会话的数据 | 显示空态 + 既有错误态（`no_project_loaded`） |

**这是唯一面向用户的语义变化**，方向是"宁可空/报错，不显示或写入别的会话"。

### 3.5 受影响功能与配套改动（必须同阶段完成）

| # | 功能 | 现状依赖 | 配套改动 |
|---|---|---|---|
| 1 | 项目状态 chip | `status.value.loaded/loadedAt/directory/activeProject/mcpRunning`（StatusChip 另读 `.umo`） | chip 组件本身不改：语义收窄后共享 ref 仍表示活跃会话；改动落在 `Chat.vue`——会话切换处保留 `refresh(activeUmo)` 并新增 `setActiveUmo(activeUmo)` |
| 2 | spcode auto-load dirty-tag | `Chat.vue:2541-2546` 在 `refresh(umo)` 后读共享 ref 的 `bootId` | 改为消费 `refresh()` 返回值（§3.2） |
| 3 | `/project load`/`unload` 乐观更新 | `ChatInput.vue:1537/1543` | `setLoaded(umo, path)` / `setUnloaded(umo)`；调用点补传当前会话 umo |

### 3.6 阶段划分与提交边界

| 阶段 | 交付物 | 涉及文件 | 可独立验证 | 可独立 revert |
|---|---|---|---|---|
| **1** | per-umo 状态 + 守卫 + `setLoaded` 原子化 + bootId 读点修正 | `useSpcodeProjectStatus.ts`、`Chat.vue`、`ChatInput.vue`、`useSpcodeProjectStatus.spec.ts` | 是（新 spec + 16 个保险丝 spec） | 是（行为修复可单独回滚） |
| **2** | `useSpcodeSession.ts` + Chat.vue provide + 21 文件读点迁移 | 新增 1、改 22 | 是（typecheck + spec + 污染回归测试） | 是（结构改造） |
| **3** | dev 警告（写路径落到回落上下文时）+ 污染单例回归测试 | `useSpcodeSession.ts`、新增/扩充 2 spec | 是 | 是 |

## 4. 测试策略

**新增**

| spec | 覆盖 |
|---|---|
| `useSpcodeSession.spec.ts` | 注入解析；无 provider 时回落共享状态；provider 值变化时消费方响应 |
| `useSpcodeProjectStatus.spec.ts`（扩充） | per-umo 隔离；**乱序响应丢弃**（`refresh(B)` 先发后到不留痕）；`setLoaded(umo, dir)` 原子性；`refresh` 返回值含 `bootId` |
| `useSpcodeWorktrees.spec.ts`（扩充，关键回归） | 单例被污染成会话 B、在提供会话 A 的组件内发起 add/remove/lock/unlock → **body 与 query 均为 A** |

**保险丝（每阶段必须全绿）**：`vue-tsc --noEmit` + 16 个既有 spcode spec
`useSpcodeProjectStatus`(10) / `SpcodeProjectIndicator`(8) / `GitDiffSidebar.repoInit`(6) / `GitDiffSidebar.logFocus`(3) / `BinaryPreview`(2) / `ProjectLoadDialog`(2) / `useSpcodeFileBinary`(2) / `useSpcodeGitBranches`(2) / `useSpcodeGitConflict`(2) / `useSpcodeGitLog`(3) / `useSpcodeGitMerge`(2) / `useSpcodeGitRemoteSync`(2) / `useSpcodeGitStash`(2) / `useSpcodeGitRepoProbe`(2) / `useSpcodeGitStats`(2) / `useSpcodeWorktrees`(2)。

**手工烟测（提交前，需人工确认）**：`pnpm dev` → 开两个会话各加载不同项目 → 反复切换后确认侧边栏的分支/worktree/默认路径/文件树都跟随会话；新建空白会话时创建 worktree 应提示"项目未载入"而非写别的仓库。

## 5. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 100 处读点的机械替换引入笔误 | 分阶段提交 + 每阶段 `vue-tsc` + 16 个保险丝 spec；`.loaded`/`.bootId` 明确不动 |
| `inject()` 在组件外调用拿到回落值（测试/工具函数） | 设计内行为；阶段 3 对写路径的回落使用打 dev 警告，使其可见 |
| `entries` 缓存增长（会话很多时） | 单会话条目极小；本期不做淘汰（YAGNI），如后续需要再按 LRU 收敛 |
| 阶段 1 的守卫改变既有 chip 更新时机 | §3.5 的 3 处配套改动 + 保险丝 spec 覆盖；`SpcodeProjectIndicator.spec.ts`(8 处) 即为门禁 |

## 6. 后续（不在本期）

- 若将来在 `ProjectView` 等页面挂载侧边栏，provider 需上提到 `App` 层，并显式传入该页面的会话身份。
- 后端读路径仍保留"最近加载项目"回落（本次前端已不依赖它）；如需收紧，另出后端 spec。
