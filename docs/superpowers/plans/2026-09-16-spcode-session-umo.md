# spcode 会话级 umo 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 spcode 侧边栏及其驱动的 composable 的会话身份（umo）显式来自"侧边栏所属会话"，并让共享状态恒等于活跃会话，从结构上消除"在会话 A 操作却落到会话 B 仓库"的可能。

**Architecture:** 三阶段。阶段 1 重构 `useSpcodeProjectStatus` 为 per-umo 条目表 + 活跃会话钉定（pin）+ 乐观更新原子化；阶段 2 引入 `useSpcodeSession`（provide/inject 环境上下文）并把 21 个文件的读点从全局单例迁到上下文（composable 签名不变）；阶段 3 给写路径加 dev 警告 + 关键回归测试。

**Tech Stack:** Vue 3.3.4（`hasInjectionContext`/`reactive(new Map())`）、TypeScript、Vitest 1.6 + @vue/test-utils 2.4、Element Plus/Vuetify 无关。插件侧零改动。

**Spec:** `docs/superpowers/specs/2026-09-16-spcode-session-umo-design.md`

## Global Constraints

- 工作目录：`F:\github\Astrbot\dashboard`；包管理器 `pnpm`；不新增任何依赖。
- 在独立 worktree 的分支 `feat/spcode-session-umo` 上执行（见 §执行前准备）。
- 每个 task 结束必须通过：`pnpm typecheck`；以及该 task 涉及的 spec（`pnpm test <spec 路径>`）。
- 阶段完成门禁（阶段 1 结束、阶段 2 结束各跑一次）见 Task 11 的 16 个保险丝 spec。
- **禁止 push、禁止创建 PR**；仅本地提交。
- 新增代码注释用**英文**（与现有文件一致）；不要改动既有中文注释。
- 提交信息用 conventional commits（`feat(dashboard): ...` / `refactor(dashboard): ...` / `test(dashboard): ...`）。
- 不修改 `.loaded` / `.bootId` 的既有语义（除 `Chat.vue` auto-load 改为消费 `refresh()` 返回值）。

## 与 spec 的两处实现细化（先读）

1. **`pinned` 默认关闭（兼容既有 spec）**：`setActiveUmo()` 才把共享 ref 钉到某个 umo。未钉定时 `refresh()` 仍镜像结果 —— 与今天**完全一致**，因此除 `useSpcodeProjectStatus.spec.ts`（签名变更）外，其余 15 个保险丝 spec **不需要改写**（spec §2.1 G4）。
2. **不引入 `requestSeq` 序号表**：同一 umo 的并发请求已被 `inflightRefresh` 去重（任一时刻至多一个在飞），跨 umo 的"过期响应改写"由 `shouldMirror()` 拦下。spec §3.2 的"乱序响应丢弃"由 `shouldMirror` 等价实现，序号表在当前架构下不可达（遵循仓库 KISS/YAGNI 规则）。

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/composables/useSpcodeProjectStatus.ts` | 改（核心） | per-umo 条目表、pin、镜像规则、`statusFor`、原子 `setLoaded` |
| `src/composables/useSpcodeProjectStatus.spec.ts` | 改 | 新增 4 个用例；2 处 `setLoaded` 调用点补 umo |
| `src/composables/useSpcodeSession.ts` | **新建** | 会话上下文定义、provide/use、回落规则、`requireScoped` 警告 |
| `src/composables/useSpcodeSession.spec.ts` | **新建** | 注入解析 / 回落 / `statusFor` 跟随 / 警告 |
| `src/composables/useSpcodeWorktrees.spec.ts` | 改 | 新增"单例被污染"回归测试（G5） |
| `src/components/chat/Chat.vue` | 改 | `setActiveUmo` 钉定、`provideSpcodeSession`、auto-load 消费返回值 |
| `src/components/chat/ChatInput.vue` | 改 | `setLoaded(umo, path)` / `setUnloaded(umo)` 调用点 |
| `src/composables/useSpcode{Worktrees,GitConflict,GitShow,GitStatus,GitDiff,GitBranches,GitMerge,GitLog,GitFile,GitStats,GitStash,GitRemoteSync,FileRename,FileRemove,FileWrite,FileBinary,Docs}.ts` | 改（机械） | 读点由单例改为会话上下文 |
| `src/components/chat/GitDiffSidebar.vue`、`message_list_comps/{GitDiffBodyContent,GitDiffFileItem,WorktreeCreateDialog}.vue` | 改（机械） | 同上，含 4 处模板 `:umo=` 绑定 |

---

## 执行前准备

- [ ] **Step 0.1: 建 worktree 与分支**

```bash
cd F:/github/Astrbot
git worktree add .worktrees/feat-spcode-session-umo -b feat/spcode-session-umo all
cd .worktrees/feat-spcode-session-umo/dashboard
# 复用主仓 node_modules（Windows 目录联接，避免二次安装）
cmd /c mklink /J node_modules F:\github\Astrbot\dashboard\node_modules
```

- [ ] **Step 0.2: 确认基线绿**

Run: `pnpm typecheck && pnpm test src/composables/useSpcodeProjectStatus.spec.ts src/composables/useSpcodeWorktrees.spec.ts`
Expected: typecheck 无输出（exit 0）；`Tests 10 passed`（6 + 4）。

---

### Task 1: per-umo 条目表、pin 与 `statusFor`

**Files:**
- Modify: `src/composables/useSpcodeProjectStatus.ts`
- Test: `src/composables/useSpcodeProjectStatus.spec.ts`

**Interfaces:**
- Produces: `entries`（模块级 `reactive(Map<string, SpcodeProjectStatus>)`）、`setActiveUmo(umo: string | null): void`、`statusFor(umo: MaybeRefOrGetter<string | null>): ComputedRef<SpcodeProjectStatus>`、内部 `shouldMirror(umo: string): boolean`。
- Consumes: `EMPTY_STATUS`、`SpcodeProjectStatus`（`./parseSpcodeStatus`）。

- [ ] **Step 1: 写失败测试**（追加到 `useSpcodeProjectStatus.spec.ts` 末尾）

```ts
// 会话级重构（2026-09-16, elecvoid243）：共享 ref 只表示"活跃会话"。
// 未钉定（pinned=false）时保持旧语义（最后写入者获胜），因此既有 spec 不受影响。
const UMO_A = "webchat:FriendMessage:webchat!astrbot!cid-A";
const UMO_B = "webchat:FriendMessage:webchat!astrbot!cid-B";

function statusPayload(umo: string, directory: string) {
  return {
    data: {
      data: {
        loaded: true,
        directory,
        loaded_at: 1,
        umo,
        all_loaded_count: 1,
        boot_id: "4000-abc",
      },
    },
  } as never;
}

describe("useSpcodeProjectStatus session scoping", () => {
  beforeEach(() => {
    getMock.mockReset();
    useSpcodeProjectStatus().reset();
  });

  it("statusFor() reads each umo's own entry", async () => {
    const { refresh, statusFor } = useSpcodeProjectStatus();
    getMock.mockResolvedValue(statusPayload(UMO_A, "C:/proj/a"));
    await refresh(UMO_A);

    expect(statusFor(UMO_A).value.directory).toBe("C:/proj/a");
    expect(statusFor(UMO_B).value.directory).toBeNull();
    expect(statusFor(UMO_B).value.loaded).toBe(false);
  });

  it("setActiveUmo() pins the shared ref to that umo's entry", async () => {
    const { refresh, setActiveUmo, status } = useSpcodeProjectStatus();
    getMock.mockResolvedValue(statusPayload(UMO_A, "C:/proj/a"));
    await refresh(UMO_A);
    getMock.mockResolvedValue(statusPayload(UMO_B, "C:/proj/b"));
    await refresh(UMO_B);

    setActiveUmo(UMO_A);

    expect(status.value.umo).toBe(UMO_A);
    expect(status.value.directory).toBe("C:/proj/a");
  });

  it("once pinned, another umo's refresh must not hijack the shared ref", async () => {
    const { refresh, setActiveUmo, statusFor, status } =
      useSpcodeProjectStatus();
    getMock.mockResolvedValue(statusPayload(UMO_A, "C:/proj/a"));
    await refresh(UMO_A);
    setActiveUmo(UMO_A);

    getMock.mockResolvedValue(statusPayload(UMO_B, "C:/proj/b"));
    await refresh(UMO_B);

    expect(status.value.umo).toBe(UMO_A); // 共享 ref 未被劫持
    expect(status.value.directory).toBe("C:/proj/a");
    expect(statusFor(UMO_B).value.directory).toBe("C:/proj/b"); // 但进入缓存
  });

  it("reset() unpins and restores the legacy mirroring", async () => {
    const { refresh, setActiveUmo, reset, status } =
      useSpcodeProjectStatus();
    getMock.mockResolvedValue(statusPayload(UMO_A, "C:/proj/a"));
    await refresh(UMO_A);
    setActiveUmo(UMO_A);

    reset();
    getMock.mockResolvedValue(statusPayload(UMO_B, "C:/proj/b"));
    await refresh(UMO_B);

    expect(status.value.umo).toBe(UMO_B);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pnpm test src/composables/useSpcodeProjectStatus.spec.ts`
Expected: FAIL —— `statusFor is not a function` / `setActiveUmo is not a function`。

- [ ] **Step 3: 实现**（改 `useSpcodeProjectStatus.ts`）

导入行改为：

```ts
import {
  computed,
  reactive,
  ref,
  toValue,
  type ComputedRef,
  type MaybeRefOrGetter,
} from "vue";
```

在 `const status = ...`（原 `:8`）下增加：

```ts
// 2026-09-16 (elecvoid243): per-umo entries. The shared `status` ref is a
// mirror of the ACTIVE session's entry, so a refresh for another session
// can no longer overwrite what the sidebar/chip display. Kept unpinned by
// default (= legacy "last writer wins") so standalone callers and specs
// that never call setActiveUmo() behave exactly as before.
const entries = reactive(new Map<string, SpcodeProjectStatus>());
let activeUmo: string | null = null;
let pinned = false;

/** Whether a write for `umo` may update the shared ref. */
function shouldMirror(umo: string): boolean {
  return !pinned || umo === activeUmo;
}
```

在 `reset()` 之前增加三个新方法；并在 `reset()` 内加入解钉：

```ts
  /**
   * Pin the shared ref to `umo`'s entry. Called by Chat.vue on every
   * session switch — after this, only that session's writes may change
   * the shared ref.
   */
  function setActiveUmo(umo: string | null): void {
    pinned = true;
    activeUmo = umo;
    status.value = umo
      ? { ...(entries.get(umo) ?? EMPTY_STATUS) }
      : { ...EMPTY_STATUS };
  }

  /** Reactive read-only accessor for a specific session's status. */
  function statusFor(
    umo: MaybeRefOrGetter<string | null>,
  ): ComputedRef<SpcodeProjectStatus> {
    return computed(
      () => entries.get(toValue(umo) ?? "") ?? (EMPTY_STATUS as SpcodeProjectStatus),
    );
  }
```

```ts
  function reset() {
    pinned = false;
    activeUmo = null;
    status.value = {
      ...EMPTY_STATUS,
      bootId: status.value.bootId,
    };
  }
```

导出改为：

```ts
  return {
    status,
    refresh,
    setLoaded,
    setUnloaded,
    reset,
    setActiveUmo,
    statusFor,
  };
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pnpm test src/composables/useSpcodeProjectStatus.spec.ts && pnpm typecheck`
Expected: `Tests 10 passed`（6 旧 + 4 新）；typecheck exit 0。

- [ ] **Step 5: 提交**

```bash
git add src/composables/useSpcodeProjectStatus.ts src/composables/useSpcodeProjectStatus.spec.ts
git commit -m "refactor(dashboard): keep a per-umo status entry and pin the active session"
```

---

### Task 2: `refresh()` 只镜像活跃会话，并返回结果

**Files:**
- Modify: `src/composables/useSpcodeProjectStatus.ts`
- Test: `src/composables/useSpcodeProjectStatus.spec.ts`

**Interfaces:**
- Produces: `refresh(umo?: string | null): Promise<SpcodeProjectStatus>`（签名由 `Promise<void>` 变为返回该 umo 的状态）。
- Consumes: Task 1 的 `entries` / `shouldMirror`。

- [ ] **Step 1: 写失败测试**

```ts
  it("refresh() returns the fetched status for that umo", async () => {
    const { refresh } = useSpcodeProjectStatus();
    getMock.mockResolvedValue(statusPayload(UMO_A, "C:/proj/a"));

    const result = await refresh(UMO_A);

    expect(result.directory).toBe("C:/proj/a");
    expect(result.bootId).toBe("4000-abc");
  });

  it("a refresh for a non-active umo still returns its own status", async () => {
    const { refresh, setActiveUmo } = useSpcodeProjectStatus();
    getMock.mockResolvedValue(statusPayload(UMO_A, "C:/proj/a"));
    await refresh(UMO_A);
    setActiveUmo(UMO_A);

    getMock.mockResolvedValue(statusPayload(UMO_B, "C:/proj/b"));
    const result = await refresh(UMO_B);

    expect(result.directory).toBe("C:/proj/b");
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pnpm test src/composables/useSpcodeProjectStatus.spec.ts`
Expected: FAIL —— `result.directory` 为 `undefined`（当前返回 `void`）。

- [ ] **Step 3: 实现**（替换 `refresh()` 整个函数体）

```ts
  async function refresh(umo?: string | null): Promise<SpcodeProjectStatus> {
    if (!umo) {
      status.value = { ...EMPTY_STATUS };
      return status.value;
    }
    // dedup: multiple callers in the same tick share one network request
    const existing = inflightRefresh.get(umo);
    if (existing) return existing;
    const promise = (async (): Promise<SpcodeProjectStatus> => {
      try {
        const res = await pluginExtensionApi.get<{
          loaded: boolean;
          directory: string | null;
          loaded_at: number | null;
          umo: string | null;
          all_loaded_count: number;
          boot_id?: string | null;
        }>("spcode/project-status", {
          params: { umo },
        });
        const data = res.data?.data;
        if (!data) {
          // Soft-fail: keep the last known state for this umo.
          return entries.get(umo) ?? { ...EMPTY_STATUS };
        }
        const next: SpcodeProjectStatus = {
          loaded: Boolean(data.loaded),
          directory: data.directory ?? null,
          loadedAt: typeof data.loaded_at === "number" ? data.loaded_at : null,
          umo: data.umo ?? null,
          allLoadedCount:
            typeof data.all_loaded_count === "number"
              ? data.all_loaded_count
              : 0,
          fetchedAt: Date.now(),
          // 2026-09-01: backend boot id, drives dirty-tag invalidation.
          bootId: data.boot_id ?? null,
        };
        entries.set(umo, next);
        // Only the pinned (active) session may drive the shared ref; a
        // response that arrives after a session switch is cached but
        // must not overwrite what the sidebar/chip are displaying.
        if (shouldMirror(umo)) status.value = next;
        return next;
      } catch (err) {
        // Network or auth error: keep previous state, do not throw to callers.
        console.warn("[useSpcodeProjectStatus] refresh failed:", err);
        return entries.get(umo) ?? { ...EMPTY_STATUS };
      }
    })();
    inflightRefresh.set(umo, promise);
    try {
      return await promise;
    } finally {
      inflightRefresh.delete(umo);
    }
  }
```

同时把去重表类型改为：

```ts
const inflightRefresh = new Map<string, Promise<SpcodeProjectStatus>>();
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pnpm test src/composables/useSpcodeProjectStatus.spec.ts && pnpm typecheck`
Expected: `Tests 12 passed`；typecheck exit 0。

- [ ] **Step 5: 提交**

```bash
git add src/composables/useSpcodeProjectStatus.ts src/composables/useSpcodeProjectStatus.spec.ts
git commit -m "refactor(dashboard): mirror refreshes only for the pinned session"
```

---

### Task 3: `setLoaded` / `setUnloaded` 原子化 + ChatInput 调用点

**Files:**
- Modify: `src/composables/useSpcodeProjectStatus.ts`、`src/components/chat/ChatInput.vue`
- Test: `src/composables/useSpcodeProjectStatus.spec.ts`

**Interfaces:**
- Produces: `setLoaded(umo: string, directory: string, loadedAt?: number): void`、`setUnloaded(umo?: string | null): void`。
- Consumes: `shouldMirror`、`entries`。

- [ ] **Step 1: 写失败测试**

```ts
  it("setLoaded(umo, dir) writes umo and directory atomically", async () => {
    const { setLoaded, setActiveUmo, status, statusFor } =
      useSpcodeProjectStatus();
    setActiveUmo(UMO_A);

    setLoaded(UMO_A, "C:/proj/a");

    expect(status.value.umo).toBe(UMO_A);
    expect(status.value.directory).toBe("C:/proj/a");
    expect(statusFor(UMO_A).value.directory).toBe("C:/proj/a");
  });

  it("setLoaded() for a non-active umo never mixes the shared ref", async () => {
    const { refresh, setActiveUmo, setLoaded, status } =
      useSpcodeProjectStatus();
    getMock.mockResolvedValue(statusPayload(UMO_A, "C:/proj/a"));
    await refresh(UMO_A);
    setActiveUmo(UMO_A);

    setLoaded(UMO_B, "C:/proj/b");

    expect(status.value.umo).toBe(UMO_A); // 不再是 {umo:A, directory:B}
    expect(status.value.directory).toBe("C:/proj/a");
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pnpm test src/composables/useSpcodeProjectStatus.spec.ts`
Expected: FAIL —— 第 2 个用例 `status.value.directory` 变成 `C:/proj/b`（旧实现直接 spread 覆盖共享 ref）。

- [ ] **Step 3: 实现**（替换两个方法）

```ts
  /**
   * Optimistically mark `umo` as having a project loaded. Writes the umo
   * and the directory together so the pair can never mix sessions
   * (2026-09-16, elecvoid243).
   */
  function setLoaded(
    umo: string,
    directory: string,
    loadedAt: number = Date.now() / 1000,
  ) {
    const base = entries.get(umo) ?? status.value;
    const next: SpcodeProjectStatus = {
      ...base,
      loaded: true,
      directory,
      loadedAt,
      umo,
      fetchedAt: Date.now(),
    };
    entries.set(umo, next);
    if (shouldMirror(umo)) status.value = next;
  }

  /** Optimistically mark `umo` (default: the displayed session) as unloaded. */
  function setUnloaded(umo?: string | null) {
    const target = umo ?? (pinned ? activeUmo : status.value.umo);
    const base = target ? (entries.get(target) ?? status.value) : status.value;
    const next: SpcodeProjectStatus = {
      ...EMPTY_STATUS,
      umo: target,
      allLoadedCount: Math.max(0, base.allLoadedCount - 1),
      fetchedAt: Date.now(),
      // backend identity is session-independent: keep it (only refresh updates)
      bootId: base.bootId,
    };
    if (target) entries.set(target, next);
    if (!target || shouldMirror(target)) status.value = next;
  }
```

改 `useSpcodeProjectStatus.spec.ts` 里 2 处旧签名调用：

```ts
    setLoaded(UMO, "C:/proj/demo");   // 原: setLoaded("C:/proj/demo")  ×2 处
```

改 `ChatInput.vue` 的 `applyOptimisticProjectStatus`：

```ts
function applyOptimisticProjectStatus(text: string): void {
  // Session identity is required to write optimistically; without it
  // (brand-new chat) there is nothing to attach the state to.
  if (!props.currentSession) return;
  const umo = buildWebchatUmoDetails(
    props.currentSession.session_id,
    Boolean(props.currentSession.is_group),
  ).umo;
  const trimmed = text.trim();
  // load: <prefix>project load <path...>
  const loadMatch = trimmed.match(/^\S+\s+project\s+load\s+(\S[\s\S]*)$/);
  if (loadMatch) {
    let path = loadMatch[1].trim();
    if (path.length >= 2 && path.startsWith('"') && path.endsWith('"')) {
      path = path.slice(1, -1);
    }
    if (path) {
      spcodeStatus.setLoaded(umo, path);
    }
    return;
  }
  // unload: <prefix>project unload (optionally followed by an arg)
  if (/^\S+\s+project\s+unload(?:\s|$)/.test(trimmed)) {
    spcodeStatus.setUnloaded(umo);
  }
}
```

- [ ] **Step 4: 跑测试与门禁**

Run: `pnpm test src/composables/useSpcodeProjectStatus.spec.ts src/components/chat/ProjectLoadDialog.spec.ts && pnpm typecheck`
Expected: `Tests 14 passed` + `2 passed`；typecheck exit 0。

- [ ] **Step 5: 提交**

```bash
git add src/composables/useSpcodeProjectStatus.ts src/composables/useSpcodeProjectStatus.spec.ts src/components/chat/ChatInput.vue
git commit -m "fix(dashboard): write umo and directory atomically in the optimistic project state"
```

---

### Task 4: Chat.vue 接线（钉定 + 消费 `refresh()` 返回值）

**Files:**
- Modify: `src/components/chat/Chat.vue`（`currSessionId` watcher ~`:2466`；`tryAutoLoadSpcodeForSession` ~`:2541` 与 `:2556`）

**Interfaces:**
- Consumes: `setActiveUmo`（Task 1）、`refresh(): Promise<SpcodeProjectStatus>`（Task 2）。

- [ ] **Step 1: 钉定活跃会话**

把 watcher 内这段：

```ts
    const resolvedUmo = resolveCurrentUmo(next);
    if (resolvedUmo) {
      await spcodeStatus.refresh(resolvedUmo);
    } else {
      spcodeStatus.reset();
    }
```

改为：

```ts
    const resolvedUmo = resolveCurrentUmo(next);
    if (resolvedUmo) {
      // 2026-09-16 (elecvoid243): pin the shared status to THIS session
      // before refreshing, so a late response for another session can
      // no longer overwrite the chip / sidebar state.
      spcodeStatus.setActiveUmo(resolvedUmo);
      await spcodeStatus.refresh(resolvedUmo);
    } else {
      spcodeStatus.reset();
    }
```

- [ ] **Step 2: auto-load 消费返回值**

`:2541` 处：

```ts
  const statusBeforeLoad = await spcodeStatus.refresh(umo);
  if (
    isSessionLoadedTag(
      sessionId,
      project.project_id,
      statusBeforeLoad.bootId,
    )
  ) {
    return;
  }
```

`:2556` 处：

```ts
    if (data?.loaded) {
      const statusAfterLoad = await spcodeStatus.refresh(umo);
      markSessionLoadedTag(
        sessionId,
        project.project_id,
        statusAfterLoad.bootId,
      );
    }
```

- [ ] **Step 3: 门禁**

Run: `pnpm typecheck && pnpm test src/composables/useSpcodeProjectStatus.spec.ts src/components/chat/GitDiffSidebar.logFocus.spec.ts`
Expected: typecheck exit 0；`Tests 14 + 3 passed`（auto-load 的行为由 Task 11 的手工烟测确认，单测由 Task 1/2 的语义测试覆盖）。

- [ ] **Step 4: 提交**

```bash
git add src/components/chat/Chat.vue
git commit -m "fix(dashboard): pin the spcode status to the active session on switch"
```

---

### Task 5: 会话上下文 `useSpcodeSession`

**Files:**
- Create: `src/composables/useSpcodeSession.ts`
- Test: `src/composables/useSpcodeSession.spec.ts`

**Interfaces:**
- Produces: `SpcodeSessionContext`、`SPCODE_SESSION_KEY`、`provideSpcodeSession(ctx)`、`useSpcodeSession(opts?: { requireScoped?: boolean }): SpcodeSessionContext`（`umo`/`directory` 为 `ComputedRef`，`scoped: boolean`）。
- Consumes: `useSpcodeProjectStatus().status`（回落用）。

- [ ] **Step 1: 写失败测试**（新文件）

```ts
// useSpcodeSession.spec.ts
//
// The session context makes a component's session identity explicit.
// Without a provider (standalone composables, specs, display-only chips
// outside the chat page) it falls back to the shared status ref, which
// keeps existing call sites working unchanged.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { computed, defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { get: vi.fn(), post: vi.fn() },
}));

import { useSpcodeProjectStatus } from "./useSpcodeProjectStatus";
import { provideSpcodeSession, useSpcodeSession } from "./useSpcodeSession";

const UMO_A = "webchat:FriendMessage:webchat!astrbot!cid-A";

describe("useSpcodeSession", () => {
  beforeEach(() => {
    useSpcodeProjectStatus().reset();
  });

  it("resolves the provided session context", () => {
    let seen: string | null = null;
    let scoped = false;
    const Child = defineComponent({
      setup() {
        const session = useSpcodeSession();
        scoped = session.scoped;
        return () => {
          seen = session.umo.value;
          return h("div");
        };
      },
    });
    const Parent = defineComponent({
      setup() {
        provideSpcodeSession({
          umo: computed(() => UMO_A),
          directory: computed(() => "C:/proj/a"),
        });
        return () => h(Child);
      },
    });

    mount(Parent);

    expect(seen).toBe(UMO_A);
    expect(scoped).toBe(true);
  });

  it("falls back to the shared status outside a provider", () => {
    const spcodeStatus = useSpcodeProjectStatus();
    spcodeStatus.setLoaded(UMO_A, "C:/proj/a");
    spcodeStatus.setActiveUmo(UMO_A);

    const session = useSpcodeSession();

    expect(session.scoped).toBe(false);
    expect(session.umo.value).toBe(UMO_A);
    expect(session.directory.value).toBe("C:/proj/a");
  });

  it("warns in dev when requireScoped falls back", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    useSpcodeSession({ requireScoped: true });
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining("[useSpcodeSession]"),
    );
    warn.mockRestore();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pnpm test src/composables/useSpcodeSession.spec.ts`
Expected: FAIL —— 无法解析 `./useSpcodeSession`。

- [ ] **Step 3: 实现**（新文件）

```ts
// useSpcodeSession.ts
//
// Session-scoped identity for every spcode surface that talks to a
// repository (2026-09-16, elecvoid243).
//
// Why: the spcode status singleton used to be read directly by 71 call
// sites, so any code path that could make the singleton hold another
// session's project would silently address the wrong repository. The
// context makes "which session is this component acting for" explicit
// and injectable, while keeping composable signatures unchanged.
import {
  computed,
  hasInjectionContext,
  inject,
  provide,
  type ComputedRef,
  type InjectionKey,
} from "vue";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";

export interface SpcodeSessionContext {
  /** This session's umo, or null when it cannot be resolved yet. */
  umo: ComputedRef<string | null>;
  /** This session's loaded project root, or null. */
  directory: ComputedRef<string | null>;
  /** False when there is no provider (fallback to the shared status). */
  scoped: boolean;
}

export const SPCODE_SESSION_KEY: InjectionKey<SpcodeSessionContext> =
  Symbol("spcode:session");

/** Provide the session context from a page-level component (Chat.vue). */
export function provideSpcodeSession(ctx: {
  umo: ComputedRef<string | null>;
  directory: ComputedRef<string | null>;
}): void {
  provide(SPCODE_SESSION_KEY, { ...ctx, scoped: true });
}

/**
 * Read the session context.
 *
 * Args:
 *   options.requireScoped: set by composables that mutate a repository —
 *     a dev warning then flags the (unexpected) fallback path.
 *
 * Returns:
 *   The injected context, or a shared-status-backed fallback.
 */
export function useSpcodeSession(
  options: { requireScoped?: boolean } = {},
): SpcodeSessionContext {
  // hasInjectionContext() keeps this callable from specs (no component).
  const injected = hasInjectionContext()
    ? inject(SPCODE_SESSION_KEY, null)
    : null;
  if (injected) return injected;

  const { status } = useSpcodeProjectStatus();
  if (options.requireScoped && import.meta.env.DEV) {
    console.warn(
      "[useSpcodeSession] no session context: a repository operation will " +
        "fall back to the shared status. Render this composable inside the " +
        "chat page (provideSpcodeSession) to scope it to a session.",
    );
  }
  return {
    umo: computed(() => status.value.umo),
    directory: computed(() => status.value.directory),
    scoped: false,
  };
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pnpm test src/composables/useSpcodeSession.spec.ts && pnpm typecheck`
Expected: `Tests 3 passed`；typecheck exit 0。

- [ ] **Step 5: 提交**

```bash
git add src/composables/useSpcodeSession.ts src/composables/useSpcodeSession.spec.ts
git commit -m "feat(dashboard): add a session context for spcode repository calls"
```

---

### Task 6: Chat.vue 提供会话上下文

**Files:**
- Modify: `src/components/chat/Chat.vue`（`currentUmo` 定义处 ~`:2645` 之后）

**Interfaces:**
- Consumes: `provideSpcodeSession`（Task 5）、`currentUmo`（既有 computed）、`statusFor`（Task 1）。

- [ ] **Step 1: 实现**（`const currentUmo = computed(...)` 之后插入）

```ts
// 2026-09-16 (elecvoid243): the git-diff sidebar subtree acts on the
// ACTIVE conversation's repository. Provide its identity + project root
// explicitly instead of letting every consumer read the shared status
// (which another session's late response could have written).
provideSpcodeSession({
  umo: currentUmo,
  directory: computed(
    () => spcodeStatus.statusFor(currentUmo).value.directory,
  ),
});
```

同时确认 `provideSpcodeSession` 已加入本文件的 import（放在既有 composable import 附近）：

```ts
import { provideSpcodeSession } from "@/composables/useSpcodeSession";
```

- [ ] **Step 2: 门禁**

Run: `pnpm typecheck`
Expected: exit 0。

- [ ] **Step 3: 提交**

```bash
git add src/components/chat/Chat.vue
git commit -m "feat(dashboard): provide the spcode session context from the chat page"
```

---

### Task 7: 迁移 17 个 composable 的读点

**Files:**（机械替换，逐文件见下表）
- Modify: `src/composables/useSpcodeWorktrees.ts` 等 17 个文件

**Interfaces:**
- Consumes: `useSpcodeSession()`（Task 5）。
- Produces: 无签名变化 —— 所有 composable 的对外接口保持不变。

**统一改法（每个文件三步）**

1. 顶部加 import：`import { useSpcodeSession } from "@/composables/useSpcodeSession";`
2. 工厂函数首行加：`const session = useSpcodeSession();`
3. 把 `spcodeStatus.status.value.umo` → `session.umo.value`，`spcodeStatus.status.value.directory` → `session.directory.value`；若该文件不再使用 `spcodeStatus`，删除其 import 与变量声明。

**完整示例（`useSpcodeGitStatus.ts`，其余文件同构）**

```ts
import { useSpcodeSession } from "@/composables/useSpcodeSession";

export function useSpcodeGitStatus(
  worktreeRef: MaybeRef<string | null> = null,
): UseSpcodeGitStatus {
  const state = ref<GitStatusFetchState>({ kind: "idle" });
  const session = useSpcodeSession();          // was: const spcodeStatus = useSpcodeProjectStatus();
  ...
    const umo = session.umo.value;             // was: spcodeStatus.status.value.umo
  ...
  watch(
    () => session.umo.value,                   // was: () => spcodeStatus.status.value.umo
    (newUmo, oldUmo) => { ... },
  );
  watch(
    () => session.directory.value,             // was: spcodeStatus.status.value.directory
    (newDir, oldDir) => {
      if (!isMounted) return;
      if (newDir && newDir !== oldDir && session.umo.value) {   // was spcodeStatus.status.value.umo
        void refresh();
      }
    },
  );
```

**逐文件清单（`spcodeStatus.status.value.umo` 出现行号）**

| 文件 | 行号 | 备注 |
|---|---|---|
| `useSpcodeWorktrees.ts` | 183, 224, 277, 345, 392, 437, 484 | `:277+` 是 `params.umo ?? spcodeStatus...` → `params.umo ?? session.umo.value`（保留入参优先） |
| `useSpcodeGitConflict.ts` | 65, 71, 120, 149 | `:120` 是 watch 源 |
| `useSpcodeGitShow.ts` | 184, 285, 382, 526 | `:526` 是 watch 源 |
| `useSpcodeGitDiff.ts` | 49, 131, 143 | `:131` 是 watch 源；`:143` 在 directory watcher 的条件里 |
| `useSpcodeGitBranches.ts` | 98, 157, 214 | `:157` 是 watch 源 |
| `useSpcodeGitLog.ts` | 167, 391 | |
| `useSpcodeGitFile.ts` | 96, 205 | `:205` 是 watch 源 |
| `useSpcodeGitMerge.ts` | 51, 90 | 写路径 |
| `useSpcodeGitStash.ts` | 97 | 写路径 |
| `useSpcodeGitStats.ts` | 69 | |
| `useSpcodeGitRemoteSync.ts` | 91 | `return spcodeStatus.status.value.umo;` → `return session.umo.value;`（写路径） |
| `useSpcodeFileWrite.ts` | 42 | 写路径 |
| `useSpcodeFileRename.ts` | 45 | 写路径 |
| `useSpcodeFileRemove.ts` | 38 | 写路径 |
| `useSpcodeFileBinary.ts` | 185 | |
| `useSpcodeDocs.ts` | 110 | 写路径 |
| `useSpcodeGitRepoProbe.ts` | 2 处 `.directory` | 只改 directory |

- [ ] **Step 1: 按清单完成 17 个文件**
- [ ] **Step 2: 门禁**

Run: `pnpm typecheck && pnpm test src/composables/`
Expected: typecheck exit 0；composable spec 全绿（含 13 个依赖单例的保险丝 spec，它们走回落路径）。

- [ ] **Step 3: 提交**

```bash
git add src/composables
git commit -m "refactor(dashboard): read the session umo from the session context"
```

---

### Task 8: 迁移侧边栏组件与模板绑定

**Files:**
- Modify: `src/components/chat/GitDiffSidebar.vue`（27 处，含 4 处模板绑定 `:5691`/`:5788`/`:5797`/`:6475`）
- Modify: `src/components/chat/message_list_comps/GitDiffBodyContent.vue`（4 处，均为 `if (!umo) return false` 守卫）
- Modify: `src/components/chat/message_list_comps/GitDiffFileItem.vue`（1 处）、`WorktreeCreateDialog.vue`（1 处）

**Interfaces:**
- Consumes: `useSpcodeSession()`。

- [ ] **Step 1: `GitDiffSidebar.vue`** —— 在 `<script setup>` 的 composable 声明区加 `const session = useSpcodeSession();`，然后：

```ts
      umo: session.umo.value,     // 所有 `umo: spcodeStatus.status.value.umo,`（10 处）
      const umo = session.umo.value;                      // 12 处局部变量
      () => session.umo.value,                            // :4433 watch 源
```

模板 4 处：

```html
      :umo="session.umo.value"
```

- [ ] **Step 2: 子树组件** —— `GitDiffBodyContent.vue` 的 4 处守卫改为：

```ts
      if (!session.umo.value) return false;
```

`GitDiffFileItem.vue:139`、`WorktreeCreateDialog.vue:122` 同法（后者为 `umo: session.umo.value,`）。

- [ ] **Step 3: 门禁**

Run: `pnpm typecheck && pnpm test src/components/chat/GitDiffSidebar.repoInit.spec.ts src/components/chat/GitDiffSidebar.logFocus.spec.ts`
Expected: typecheck exit 0；`Tests 9 passed`。

- [ ] **Step 4: 提交**

```bash
git add src/components/chat/GitDiffSidebar.vue src/components/chat/message_list_comps
git commit -m "refactor(dashboard): read the sidebar session umo from the context"
```

---

### Task 9: 关键回归测试 —— 单例被污染的会话仍发对的 umo（G5）

**Files:**
- Test: `src/composables/useSpcodeWorktrees.spec.ts`

**Interfaces:**
- Consumes: `provideSpcodeSession`（Task 5）、`setActiveUmo`（Task 1）、`useSpcodeWorktrees`。

- [ ] **Step 1: 写测试**（追加到该 spec 的 describe 内）

```ts
  it("mutations carry the session umo even when the shared status holds another session", async () => {
    // Session B owns the shared status (e.g. the user was looking at it a
    // moment ago).
    getMock.mockResolvedValue({
      data: {
        data: {
          loaded: true,
          directory: "C:/proj/b",
          loaded_at: 1,
          umo: UMO_B,
          all_loaded_count: 1,
          boot_id: "4000-b",
        },
      },
    } as never);
    const spcodeStatus = useSpcodeProjectStatus();
    await spcodeStatus.refresh(UMO_B);
    spcodeStatus.setActiveUmo(UMO_B);

    // The sidebar belongs to session A.
    let api!: ReturnType<typeof useSpcodeWorktrees>;
    const Host = defineComponent({
      setup() {
        provideSpcodeSession({
          umo: computed(() => UMO_A),
          directory: computed(() => "C:/proj/a"),
        });
        api = useSpcodeWorktrees();
        return () => h("div");
      },
    });
    mount(Host);
    postMock.mockResolvedValue(okEnvelope({ created: { path: "C:/proj/a/.worktrees/feat-x" } }));

    await api.add({
      path: "C:/proj/a/.worktrees/feat-x",
      branch: "feat-x",
      create: true,
    });

    expect(bodyOf().umo).toBe(UMO_A);
    expect(configOf().params?.umo).toBe(UMO_A);
  });
```

文件顶部补 import 与常量：

```ts
import { computed, defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";
import { provideSpcodeSession } from "./useSpcodeSession";

const UMO_A = "webchat:FriendMessage:webchat!astrbot!cid-A";
const UMO_B = "webchat:FriendMessage:webchat!astrbot!cid-B";
const getMock = vi.mocked(pluginExtensionApi.get);
```

`beforeEach` 内加 `getMock.mockReset(); getMock.mockResolvedValue(okEnvelope());`（挂载后的 `onMounted` 会触发一次内部 GET）。

- [ ] **Step 2: 跑测试确认通过（且未被 Task 7 之前"意外通过"）**

Run: `pnpm test src/composables/useSpcodeWorktrees.spec.ts`
Expected: `Tests 5 passed`。若把 Task 7 的回退（临时改回 `spcodeStatus.status.value.umo`）后此用例应 **FAIL** —— 用 `git stash` 快速验证一次再恢复。

- [ ] **Step 3: 提交**

```bash
git add src/composables/useSpcodeWorktrees.spec.ts
git commit -m "test(dashboard): pin the cross-session umo invariant for worktree mutations"
```

---

### Task 10: 写路径的 dev 警告（阶段 3）

**Files:**
- Modify: `src/composables/useSpcodeWorktrees.ts`、`useSpcodeFileWrite.ts`、`useSpcodeFileRename.ts`、`useSpcodeFileRemove.ts`、`useSpcodeDocs.ts`、`useSpcodeGitMerge.ts`、`useSpcodeGitStash.ts`、`useSpcodeGitBranches.ts`、`useSpcodeGitConflict.ts`、`useSpcodeGitRemoteSync.ts`、`useSpcodeGitDiff.ts`
- Test: `src/composables/useSpcodeSession.spec.ts`

**Interfaces:**
- Consumes: `useSpcodeSession({ requireScoped: true })`。

- [ ] **Step 1: 写失败测试**（追加到 `useSpcodeSession.spec.ts`）

```ts
  it("repository-mutating composables use requireScoped", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    // 在组件外调用写路径 composable → 必须命中回落并告警
    const { useSpcodeFileWrite } = require("./useSpcodeFileWrite");
    useSpcodeFileWrite();
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
```

（若项目禁用 `require`，改为在文件顶部 `import { useSpcodeFileWrite } from "./useSpcodeFileWrite";`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `pnpm test src/composables/useSpcodeSession.spec.ts`
Expected: FAIL —— 当前 `useSpcodeFileWrite` 调用 `useSpcodeSession()` 未带 `{ requireScoped: true }`。

- [ ] **Step 3: 实现** —— 上述 11 个写路径文件把 Task 7 的 `useSpcodeSession()` 改为：

```ts
  const session = useSpcodeSession({ requireScoped: true });
```

- [ ] **Step 4: 门禁**

Run: `pnpm test src/composables/useSpcodeSession.spec.ts && pnpm typecheck`
Expected: `Tests 4 passed`；typecheck exit 0。

- [ ] **Step 5: 提交**

```bash
git add src/composables
git commit -m "chore(dashboard): warn in dev when a repository write falls back to the shared status"
```

---

### Task 11: 阶段门禁与手工烟测

- [ ] **Step 1: 类型 + 16 个保险丝 spec**

```bash
pnpm typecheck
pnpm test src/composables/useSpcodeProjectStatus.spec.ts \
  src/composables/useSpcodeSession.spec.ts \
  src/composables/useSpcodeWorktrees.spec.ts \
  src/composables/useSpcodeGitBranches.spec.ts \
  src/composables/useSpcodeGitConflict.spec.ts \
  src/composables/useSpcodeGitLog.spec.ts \
  src/composables/useSpcodeGitMerge.spec.ts \
  src/composables/useSpcodeGitRemoteSync.spec.ts \
  src/composables/useSpcodeGitStash.spec.ts \
  src/composables/useSpcodeGitStats.spec.ts \
  src/composables/useSpcodeGitRepoProbe.spec.ts \
  src/composables/useSpcodeFileBinary.spec.ts \
  src/components/chat/SpcodeProjectIndicator.spec.ts \
  src/components/chat/GitDiffSidebar.repoInit.spec.ts \
  src/components/chat/GitDiffSidebar.logFocus.spec.ts \
  src/components/chat/ProjectLoadDialog.spec.ts \
  src/components/chat/BinaryPreview.spec.ts
```

Expected: 全部 passed（新增 12 + 既有 60 上下）。

- [ ] **Step 2: 手工烟测（需要人工确认，见 spec §4）**

1. `pnpm dev`，开两个会话各加载不同项目（A、B）。
2. 在 A 打开侧边栏 → 建 branch/worktree → 确认落到 A 的仓库；切到 B 重复一次。
3. 来回切换 5 次后，侧边栏分支持续跟随会话（不残留上一个会话的分支）。
4. 新建空白会话 → 点创建 worktree → 应提示"项目未载入"，且不产生任何仓库改动。

- [ ] **Step 3: 全量前端测试（可选但推荐）**

Run: `pnpm test`
Expected: 与改动前相同的通过集合（基线差异需人工比对）。

- [ ] **Step 4: 合并回 `all` 前的最终提交**

```bash
git add -A
git commit -m "test(dashboard): session-scoped umo gates" || true
```

---

## Self-Review（作者自检记录）

- **Spec 覆盖**：§3.2 per-umo/pin → Task 1-2；§3.2 `setLoaded` 原子化 → Task 3；§3.3 上下文 → Task 5-6；§3.3 读点迁移 → Task 7-8；§2.1 G5 → Task 9；§3.6 阶段 3 → Task 10；§4 门禁与烟测 → Task 11。
- **占位符扫描**：无 TBD/TODO；每个改动步骤均给出完整代码或明确的"文件:行号 + 替换规则"。
- **类型一致性**：`refresh` 在 Task 2 起返回 `Promise<SpcodeProjectStatus>`，Task 4 的 `statusBeforeLoad.bootId` / `statusAfterLoad.bootId` 与之匹配；`setLoaded(umo, directory, loadedAt?)` 在 Task 3 定义、Task 3/10 调用一致；`useSpcodeSession(opts?)` 在 Task 5 定义、Task 10 使用 `requireScoped`。
