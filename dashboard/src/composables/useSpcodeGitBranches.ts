// Author: elecvoid243 @ 2026-07-21
// Spec: docs/superpowers/specs/2026-07-21-git-branch-switcher-frontend-design.md §3.2
//
// Composable for the git branch list. Mirrors useSpcodeWorktrees 1:1
// for the read path (state / refresh / polling / dispose). Mutation
// methods (switch / create / delete) are added in Task 4.

import { ref, watch, type Ref } from "vue";
import { pluginExtensionApi } from "@/api/v1";
import { useSpcodeSession } from "@/composables/useSpcodeSession";
import {
  parseSpcodeGitBranches,
  type SpcodeGitBranchesSnapshot,
  type SpcodeGitBranchesRawResponse,
} from "@/composables/parseSpcodeGitBranches";
import {
  parseSpcodeBranchSwitch,
  parseSpcodeBranchCreate,
  parseSpcodeBranchDelete,
  type SpcodeBranchMgmtSnapshot,
} from "@/composables/parseSpcodeBranchManagement";

export type BranchesFetchState =
  | { kind: "idle" }
  | { kind: "loading" }
  | {
      kind: "ok";
      snapshot: SpcodeGitBranchesSnapshot;
      notModified?: boolean;
    }
  | {
      kind: "error";
      reason: string;
      previousSnapshot?: SpcodeGitBranchesSnapshot;
    };

// Placeholder types — full implementations in Task 4.
export interface BranchSwitchParams {
  name: string;
  force?: boolean;
  detach?: boolean;
  umo?: string | null;
}
export interface BranchCreateParams {
  name: string;
  startPoint?: string;
  umo?: string | null;
}
export interface BranchDeleteParams {
  name: string;
  force?: boolean;
  umo?: string | null;
}
export type BranchMgmtResult =
  | { ok: true; snapshot: SpcodeGitBranchesSnapshot }
  | { ok: false; reason: string; stderr?: string };

export interface UseSpcodeGitBranches {
  state: Ref<BranchesFetchState>;
  refresh: () => Promise<void>;
  /** 2026-08-13: schedule a deferred refresh (~500ms) for the initial
   *  load and project-switch triggers, instead of firing immediately.
   *  Coalescing: a new call cancels the pending timer. */
  refreshDelayed: (delayMs?: number) => void;
  startPolling: (intervalMs?: number) => void;
  stopPolling: () => void;
  switch: (params: BranchSwitchParams) => Promise<BranchMgmtResult>;
  create: (params: BranchCreateParams) => Promise<BranchMgmtResult>;
  delete: (params: BranchDeleteParams) => Promise<BranchMgmtResult>;
  dispose: () => void;
}

// Single source of truth for the polling cadence — imported from
// the worktree composable rather than re-declared. Both composables
// start/stop in lockstep in GitDiffSidebar.vue.
const DEFAULT_POLL_MS = 30_000;

// 2026-10-07 (elecvoid243): 「项目尚未就绪」类失败的有界退避重试表。
//
// WHY: 项目加载完成的信号只有 session.directory 变化，而这个 composable
// 原先只 watch umo（项目加载时 umo 不变）。仅补 directory watcher 也不够：
// 聊天框 `/project load` 在命令发出的瞬间就乐观 setLoaded(umo, path)，之后
// onStreamEnd 的权威 refresh 写入同一个 path 值 —— watcher 不会第二次触发。
// 于是「首帧请求早于项目登记」这条路径上分支快照会永久停在空结果（UI 把它
// 渲染成 detached HEAD），只能靠用户手点刷新。8 次退避 ≈ 27.5s，覆盖正常
// 加载时长；更长的加载由 30s 轮询兜底。
const RETRY_BACKOFF_MS: readonly number[] = [
  500, 1000, 2000, 4000, 5000, 5000, 5000, 5000,
];

// 只有「项目还没登记好」类失败值得重试：换 / 建项目都无法修好网络错误、
// 参数错误或响应结构错误，重试只是浪费请求。
// (`success: false` 且 reason 为其他值 → 不重试。)
const RETRYABLE_REASONS: readonly string[] = [
  "no_project_loaded",
  "directory_missing",
];

export function useSpcodeGitBranches(): UseSpcodeGitBranches {
  const state = ref<BranchesFetchState>({ kind: "idle" });
  const session = useSpcodeSession({ requireScoped: true });
  let abortController: AbortController | null = null;
  let mutationAbort: AbortController | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let refreshDelayTimer: ReturnType<typeof setTimeout> | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let retryAttempt = 0;
  let retryKey: string | null = null;
  let isMounted = true;
  const etagMap = new Map<string, string>();
  const prevSnapshotMap = new Map<string, SpcodeGitBranchesSnapshot>();

  function etagKey(d: {
    umo: string | null;
    directory: string | null;
  }): string {
    return `branches|${d.umo ?? "null"}|${d.directory ?? "null"}`;
  }

  function clearRetry(): void {
    if (retryTimer) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
    retryAttempt = 0;
    retryKey = null;
  }

  /**
   * 失败后安排一次退避重试。`key` 是 umo|directory 上下文键 —— 上下文一变
   * （项目加载完 / 切会话）就重置计数，退避预算跟着新上下文重新开始。
   */
  function scheduleRetry(reason: string, key: string): void {
    if (!isMounted) return;
    if (!RETRYABLE_REASONS.includes(reason)) return;
    if (retryKey !== key) {
      retryKey = key;
      retryAttempt = 0;
    }
    if (retryAttempt >= RETRY_BACKOFF_MS.length) return;
    const delay = RETRY_BACKOFF_MS[retryAttempt];
    retryAttempt += 1;
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = setTimeout(() => {
      retryTimer = null;
      void refresh();
    }, delay);
  }

  async function refresh(): Promise<void> {
    if (!isMounted) return;
    const umo = session.umo.value ?? null;
    const directory = session.directory.value ?? null;
    const key = etagKey({ umo, directory });
    // 本次刷新取代任何已排队的延迟抓取 / 重试（失败会各自重新排）。
    // WHY: 退避重试与 umo/directory watcher 的 refreshDelayed 可能撞在同一个
    // 500ms 截止点上（会话刚建立那一下），不取消就会连发两个等价的 GET。
    if (refreshDelayTimer) {
      clearTimeout(refreshDelayTimer);
      refreshDelayTimer = null;
    }
    if (retryTimer) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
    if (!umo) {
      state.value = {
        kind: "error",
        reason: "no_project_loaded",
        previousSnapshot: undefined,
      };
      scheduleRetry("no_project_loaded", key);
      return;
    }
    abortController?.abort();
    abortController = new AbortController();
    const isFirst = state.value.kind !== "ok";
    if (isFirst) state.value = { kind: "loading" };
    const etag = etagMap.get(key);
    try {
      const resp = await pluginExtensionApi.get<unknown>(
        "spcode/git-branches",
        {
          params: { umo },
          headers: etag ? { "If-None-Match": etag } : {},
          validateStatus: (s) => (s >= 200 && s < 300) || s === 304,
          signal: abortController.signal,
        },
      );
      if (!isMounted) return;
      if (resp.status === 304) {
        const cached = prevSnapshotMap.get(key);
        if (cached) {
          state.value = { kind: "ok", snapshot: cached, notModified: true };
          clearRetry();
        }
        return;
      }
      const envelope = resp.data as {
        data?: SpcodeGitBranchesRawResponse & {
          success?: boolean;
          reason?: string | null;
        };
      };
      const data = envelope?.data;
      if (!data) throw new Error("empty response data");
      // 2026-10-07 (elecvoid243): 失败信封（如 no_project_loaded /
      // directory_missing）**没有** branches 字段，直接喂给 parser 会得到
      // 一个「成功但空」的快照 —— 那正是取数失败被渲染成 detached HEAD 的
      // 来源。判定口径与 parseSpcodeBranchManagement 一致：_make_envelope
      // 保证 success=true 时 reason 恒为 null，故 reason 非空即失败。
      const failureReason =
        typeof data.reason === "string" && data.reason ? data.reason : null;
      if (data.success === false || failureReason !== null) {
        const reason = failureReason ?? "unknown";
        const prev =
          state.value.kind === "ok" ? state.value.snapshot : undefined;
        state.value = { kind: "error", reason, previousSnapshot: prev };
        scheduleRetry(reason, key);
        return;
      }
      const snap = parseSpcodeGitBranches(data);
      prevSnapshotMap.set(key, snap);
      const newEtag = (resp.headers as Record<string, string> | undefined)?.[
        "etag"
      ] ?? (resp.headers as Record<string, string> | undefined)?.["ETag"];
      if (newEtag) etagMap.set(key, newEtag);
      state.value = { kind: "ok", snapshot: snap, notModified: false };
      clearRetry();
    } catch (err) {
      if (!isMounted) return;
      if ((err as { name?: string })?.name === "CanceledError") return;
      const anyErr = err as { code?: string; message?: string };
      const reason =
        anyErr.code === "ERR_NETWORK" || /network/i.test(anyErr.message ?? "")
          ? "network"
          : "unknown";
      const prev =
        state.value.kind === "ok" ? state.value.snapshot : undefined;
      state.value = { kind: "error", reason, previousSnapshot: prev };
    }
  }

  watch(
    () => session.umo.value,
    (newUmo, oldUmo) => {
      if (!isMounted) return;
      // 2026-08-13: project switches defer the fetch ~500ms instead of
      // firing the instant the umo flips (rapid switches coalesce).
      if (newUmo && newUmo !== oldUmo) refreshDelayed();
    },
  );

  // 2026-10-07 (elecvoid243): 项目加载完成时 umo 不变、只有 directory 翻，
  // 所以上面的 umo watcher 抓不到「项目刚加载」这一跳 —— 工作树列表
  // (useSpcodeWorktrees) 与 git-status (useSpcodeGitStatus) 都因此额外
  // watch 了 directory，唯独分支列表没有，于是侧栏出现了「工作区 chips
  // 已就绪、当前分支却停在 fallback 文案」的分裂现象。对齐它们。
  // (乐观 setLoaded 与权威 refresh 同值的情况由 scheduleRetry 兜底。)
  watch(
    () => session.directory.value,
    (newDir, oldDir) => {
      if (!isMounted) return;
      if (newDir && newDir !== oldDir) refreshDelayed();
    },
  );

  /**
   * 2026-08-13: defer the initial / project-switch fetch by ~500ms so
   * the sidebar doesn't send a request the moment it mounts or the
   * moment the project flips. Coalescing: a newer call cancels the
   * pending timer, so rapid triggers produce a single fetch. Only the
   * load/switch triggers use this — the manual refresh button and
   * polling still call refresh() directly.
   */
  function refreshDelayed(delayMs = 500): void {
    if (refreshDelayTimer) clearTimeout(refreshDelayTimer);
    refreshDelayTimer = setTimeout(() => {
      refreshDelayTimer = null;
      void refresh();
    }, delayMs);
  }

  function startPolling(intervalMs: number = DEFAULT_POLL_MS): void {
    if (pollTimer) return;
    pollTimer = setInterval(() => {
      void refresh();
    }, intervalMs);
  }
  function stopPolling(): void {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  // ── Mutation methods (spec §3.2) ────────────────────────
  //
  // All 3 share the same shape: build a new AbortController, POST to
  // the endpoint, parse the response, atomically swap state with
  // the refreshed snapshot. Single-flight per kind via mutationAbort.
  // The 3 different parser functions and endpoint paths are the only
  // variation, so we share the boilerplate via `runMutation`.

  type ParsedBranchResponse = {
    kind: "ok";
    snapshot: SpcodeBranchMgmtSnapshot;
  } | { kind: "error"; reason: string; stderr: string };

  async function runMutation(
    endpoint: string,
    body: Record<string, unknown>,
    parser: (raw: unknown) => ParsedBranchResponse,
  ): Promise<BranchMgmtResult> {
    if (!isMounted) return { ok: false, reason: "aborted" };
    const umo = session.umo.value ?? null;
    if (!umo) return { ok: false, reason: "no_project_loaded" };
    const ctrl = new AbortController();
    mutationAbort?.abort();
    mutationAbort = ctrl;
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        endpoint,
        body,
        { signal: ctrl.signal, params: { umo } },
      );
      if (!isMounted || ctrl.signal.aborted) {
        return { ok: false, reason: "aborted" };
      }
      const parsed = parser(resp.data);
      if (parsed.kind === "error") {
        return { ok: false, reason: parsed.reason, stderr: parsed.stderr };
      }
      // Atomically swap state with the refreshed branch list.
      const refreshed = parsed.snapshot.branches;
      // 2026-09-08: mutation 响应不带 tags(后端仅 GET git-branches 返回),
      // 故沿用上一份快照的 tag 列表,待下一次轮询刷新。
      // 2026-09-08 final-fix: 下面「响应 tags 优先」那一臂今天**不可达** ——
      // parseSpcodeBranchManagement.buildSnapshot 从不把 tags 透传给
      // refreshed.tags(恒为 []),因此实际生效的永远是 prevTags。保留该
      // 分支是 forward-compat:未来后端 / 解析器开始在 mutation 响应里
      // 带 tags 时,响应数据会自动优先,无需再改这里。
      const prevTags =
        state.value.kind === "ok" ? state.value.snapshot.tags : [];
      const rawResponse: SpcodeGitBranchesRawResponse = {
        loaded: parsed.snapshot.meta.loaded,
        directory: parsed.snapshot.meta.directory,
        umo: parsed.snapshot.meta.umo,
        branches: refreshed.branches.map((b) => ({
          name: b.name,
          sha: b.sha,
          upstream: b.upstream,
          upstream_track: b.upstreamTrack,
          current: b.current,
          remote: b.remote,
        })),
        tags: (refreshed.tags.length > 0 ? refreshed.tags : prevTags).map(
          (t) => ({
            name: t.name,
            sha: t.sha,
            annotated: t.annotated,
          }),
        ),
        total: refreshed.total,
        current: refreshed.current,
        detached: refreshed.detached,
        reason: parsed.snapshot.meta.reason,
        stderr: parsed.snapshot.meta.stderr,
        elapsed_ms: parsed.snapshot.meta.elapsedMs,
      };
      const newSnap = parseSpcodeGitBranches(rawResponse);
      const directory = parsed.snapshot.meta.directory;
      prevSnapshotMap.set(etagKey({ umo, directory }), newSnap);
      state.value = { kind: "ok", snapshot: newSnap, notModified: false };
      return { ok: true, snapshot: newSnap };
    } catch (err) {
      if (!isMounted) return { ok: false, reason: "aborted" };
      if ((err as { name?: string })?.name === "CanceledError") {
        return { ok: false, reason: "aborted" };
      }
      const anyErr = err as { code?: string; message?: string };
      const reason =
        anyErr.code === "ERR_NETWORK" || /network/i.test(anyErr.message ?? "")
          ? "network"
          : "unknown";
      return { ok: false, reason };
    }
  }

  async function doSwitch(
    params: BranchSwitchParams,
  ): Promise<BranchMgmtResult> {
    return runMutation(
      "spcode/git-branch-switch",
      {
        name: params.name,
        force: params.force ?? false,
        detach: params.detach ?? false,
      },
      parseSpcodeBranchSwitch,
    );
  }

  async function doCreate(
    params: BranchCreateParams,
  ): Promise<BranchMgmtResult> {
    return runMutation(
      "spcode/git-branch-create",
      {
        name: params.name,
        start_point: params.startPoint ?? "HEAD",
        force: false,
      },
      parseSpcodeBranchCreate,
    );
  }

  async function doDelete(
    params: BranchDeleteParams,
  ): Promise<BranchMgmtResult> {
    return runMutation(
      "spcode/git-branch-delete",
      { name: params.name, force: params.force ?? false },
      parseSpcodeBranchDelete,
    );
  }

  function dispose(): void {
    isMounted = false;
    stopPolling();
    if (refreshDelayTimer) clearTimeout(refreshDelayTimer);
    refreshDelayTimer = null;
    clearRetry();
    abortController?.abort();
    abortController = null;
    mutationAbort?.abort();
    mutationAbort = null;
  }

  return {
    state,
    refresh,
    refreshDelayed,
    startPolling,
    stopPolling,
    switch: doSwitch,
    create: doCreate,
    delete: doDelete,
    dispose,
  };
}
