// Author: elecvoid243
// Date: 2026-06-17 (updated 2026-06-20 to thread the `scope` parameter;
//       2026-10-25 progressive loading — paged metadata, no patch text)
// Spec: docs/superpowers/specs/2026-06-17-chatui-git-diff-sidebar-design.md §4.1.2
//      + docs/superpowers/specs/2026-06-20-git-diff-scope-switcher-design.md §4.1
//
// 2026-10-25 (v2.32.0 progressive loading): the list endpoint returns
// paged metadata only (name/status/+/− per file + full-list totals);
// a file's patch text is fetched on demand via useSpcodeFileDiff when
// the user expands its row. `loadMore()` appends the next page.

import { ref, toValue, watch, type Ref, type MaybeRef } from "vue";
import { pluginExtensionApi } from "@/api/v1";
import { useSpcodeSession } from "@/composables/useSpcodeSession";
import {
  appendSpcodeGitDiffPage,
  parseSpcodeGitDiff,
  type GitDiffScope,
  type SpcodeGitDiffSnapshot,
  type SpcodeGitDiffRawResponse,
} from "@/composables/parseSpcodeGitDiff";

export type { GitDiffScope } from "@/composables/parseSpcodeGitDiff";

/** Default scope matches spcode plugin v3.1 (unstaged = plain `git diff`). */
export const DEFAULT_SCOPE: GitDiffScope = "unstaged";

/** Page size for the metadata list (server clamps to its own cap). */
export const GIT_DIFF_PAGE_LIMIT = 200;

export type GitDiffFetchState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; snapshot: SpcodeGitDiffSnapshot }
  | { kind: "error"; reason: string; previousSnapshot?: SpcodeGitDiffSnapshot }
  // Loading a subsequent page: keep the current snapshot rendered.
  | { kind: "ok-loading-more"; snapshot: SpcodeGitDiffSnapshot };

export interface UseSpcodeGitDiff {
  state: Ref<GitDiffFetchState>;
  refresh: () => Promise<void>;
  /** Fetch the next metadata page and append it (no-op while loading). */
  loadMore: () => Promise<void>;
  startPolling: (intervalMs?: number) => void;
  stopPolling: () => void;
  dispose: () => void;
}

const DEFAULT_POLL_MS = 10_000;

export function useSpcodeGitDiff(
  worktreeRef: MaybeRef<string | null> = null,
  scopeRef: MaybeRef<GitDiffScope> = DEFAULT_SCOPE,
): UseSpcodeGitDiff {
  const state = ref<GitDiffFetchState>({ kind: "idle" });
  const session = useSpcodeSession();
  let abortController: AbortController | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let isMounted = true;

  function currentSnapshot(): SpcodeGitDiffSnapshot | undefined {
    const s = state.value;
    return s.kind === "ok" || s.kind === "ok-loading-more"
      ? s.snapshot
      : undefined;
  }

  function setPrevError(reason: string): void {
    const prev = currentSnapshot();
    state.value = {
      kind: "error",
      reason,
      previousSnapshot: prev,
    };
  }

  async function fetchPage(
    offset: number,
    signal: AbortSignal,
  ): Promise<SpcodeGitDiffSnapshot> {
    const umo = session.umo.value;
    if (!umo) throw new Error("no_project_loaded");
    const worktree = toValue(worktreeRef);
    const scope = toValue(scopeRef);
    const resp = await pluginExtensionApi.get<SpcodeGitDiffRawResponse>(
      "spcode/git-diff",
      {
        params: {
          umo,
          scope,
          offset,
          limit: GIT_DIFF_PAGE_LIMIT,
          ...(worktree ? { worktree } : {}),
        },
        signal,
      },
    );
    const data = resp.data?.data;
    if (!data) throw new Error("empty response data");
    const snapshot = parseSpcodeGitDiff(data);
    // Drift detection: if the server echoed a different scope than the
    // user just selected, log it and let the next poll realign the UI.
    if (snapshot.meta.scope && snapshot.meta.scope !== scope) {
      console.warn(
        `[useSpcodeGitDiff] scope drift: requested=${scope} echoed=${snapshot.meta.scope}`,
      );
    }
    return snapshot;
  }

  async function refresh(): Promise<void> {
    if (!isMounted) return;
    if (!session.umo.value) {
      setPrevError("no_project_loaded");
      return;
    }
    abortController?.abort();
    abortController = new AbortController();
    const isFirst = currentSnapshot() === undefined;
    if (isFirst) state.value = { kind: "loading" };
    try {
      state.value = { kind: "ok", snapshot: await fetchPage(0, abortController.signal) };
    } catch (err) {
      if (!isMounted) return;
      if ((err as { name?: string })?.name === "CanceledError") return;
      setPrevError(classifyError(err));
    }
  }

  async function loadMore(): Promise<void> {
    if (!isMounted) return;
    const snap = currentSnapshot();
    if (!snap || !snap.hasMore) return;
    if (state.value.kind === "ok-loading-more") return;
    state.value = { kind: "ok-loading-more", snapshot: snap };
    try {
      // Snapshot switching invalidates the in-flight guard; re-check.
      const s = state.value;
      if (s.kind !== "ok-loading-more") return;
      const signal = abortController?.signal;
      if (!signal) return;
      state.value = {
        kind: "ok",
        snapshot: appendSpcodeGitDiffPage(
          s.snapshot,
          await fetchPage(s.snapshot.nextOffset, signal),
        ),
      };
    } catch (err) {
      if (!isMounted) return;
      if ((err as { name?: string })?.name === "CanceledError") return;
      // Page-load failure keeps the already-rendered rows; surface the
      // error via console (a full state swap would blank the list).
      console.error("[useSpcodeGitDiff] loadMore failed:", err);
      const s = state.value;
      if (s.kind === "ok-loading-more") state.value = { kind: "ok", snapshot: s.snapshot };
    }
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

  // Re-fetch when the user picks a different worktree OR a different
  // scope. Watching both in a single watcher keeps the request order
  // deterministic (only one in-flight at a time, controlled by the
  // abortController inside refresh()).
  watch(
    [() => toValue(worktreeRef), () => toValue(scopeRef)],
    () => {
      if (isMounted) void refresh();
    },
    { flush: "post" },
  );

  watch(
    () => session.umo.value,
    (newUmo, oldUmo) => {
      if (!isMounted) return;
      if (newUmo && newUmo !== oldUmo) {
        void refresh();
      }
    },
  );
  watch(
    () => session.directory.value,
    (newDir, oldDir) => {
      if (!isMounted) return;
      if (newDir && newDir !== oldDir && session.umo.value) {
        void refresh();
      }
    },
  );

  function dispose(): void {
    isMounted = false;
    stopPolling();
    abortController?.abort();
    abortController = null;
  }

  return { state, refresh, loadMore, startPolling, stopPolling, dispose };
}

function classifyError(err: unknown): string {
  if (typeof err === "object" && err !== null) {
    const anyErr = err as { code?: string; message?: string };
    if (
      anyErr.code === "ERR_NETWORK" ||
      /network/i.test(anyErr.message ?? "")
    ) {
      return "network";
    }
  }
  return "unknown";
}
