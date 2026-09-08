// Author: elecvoid243 @ 2026-07-21
// Spec: docs/superpowers/specs/2026-07-21-git-branch-switcher-frontend-design.md §3.2
//
// Composable for the git branch list. Mirrors useSpcodeWorktrees 1:1
// for the read path (state / refresh / polling / dispose). Mutation
// methods (switch / create / delete) are added in Task 4.

import { ref, watch, type Ref } from "vue";
import { pluginExtensionApi } from "@/api/v1";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
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

export function useSpcodeGitBranches(): UseSpcodeGitBranches {
  const state = ref<BranchesFetchState>({ kind: "idle" });
  const spcodeStatus = useSpcodeProjectStatus();
  let abortController: AbortController | null = null;
  let mutationAbort: AbortController | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let refreshDelayTimer: ReturnType<typeof setTimeout> | null = null;
  let isMounted = true;
  const etagMap = new Map<string, string>();
  const prevSnapshotMap = new Map<string, SpcodeGitBranchesSnapshot>();

  function etagKey(d: {
    umo: string | null;
    directory: string | null;
  }): string {
    return `branches|${d.umo ?? "null"}|${d.directory ?? "null"}`;
  }

  async function refresh(): Promise<void> {
    if (!isMounted) return;
    const umo = spcodeStatus.status.value.umo ?? null;
    const directory = spcodeStatus.status.value.directory ?? null;
    if (!umo) {
      state.value = {
        kind: "error",
        reason: "no_project_loaded",
        previousSnapshot: undefined,
      };
      return;
    }
    abortController?.abort();
    abortController = new AbortController();
    const isFirst = state.value.kind !== "ok";
    if (isFirst) state.value = { kind: "loading" };
    const key = etagKey({ umo, directory });
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
        }
        return;
      }
      const envelope = resp.data as { data?: SpcodeGitBranchesRawResponse };
      const data = envelope?.data;
      if (!data) throw new Error("empty response data");
      const snap = parseSpcodeGitBranches(data);
      prevSnapshotMap.set(key, snap);
      const newEtag = (resp.headers as Record<string, string> | undefined)?.[
        "etag"
      ] ?? (resp.headers as Record<string, string> | undefined)?.["ETag"];
      if (newEtag) etagMap.set(key, newEtag);
      state.value = { kind: "ok", snapshot: snap, notModified: false };
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
    () => spcodeStatus.status.value.umo,
    (newUmo, oldUmo) => {
      if (!isMounted) return;
      // 2026-08-13: project switches defer the fetch ~500ms instead of
      // firing the instant the umo flips (rapid switches coalesce).
      if (newUmo && newUmo !== oldUmo) refreshDelayed();
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
    const umo = spcodeStatus.status.value.umo ?? null;
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
