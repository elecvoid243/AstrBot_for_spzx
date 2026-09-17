// Author: elecvoid243 @ 2026-08-21
// API: astrbot_plugin_spcode_toolkit tools/webapi/git_stash.py
//
// Vue composable wrapping the git-stash endpoints:
//   * refreshList — GET  /spcode/git-stash (entries + per-stash file
//     lists) for the GitStashDialog
//   * stash       — POST /spcode/git-stash (git stash push -u)
//   * pop         — POST /spcode/git-stash-pop (apply back + drop)
//   * drop        — POST /spcode/git-stash-drop (delete one entry)
// The dialog-driven fetch means no polling; worktree is read from the
// passed ref at call time, like useSpcodeGitRemoteSync. Pop and drop
// share a single-flight guard: both reindex the entry list, so a
// concurrent second action could act on a stale index.

import { ref, toValue, type MaybeRef, type Ref } from "vue";
import { pluginExtensionApi } from "@/api/v1";
import { useSpcodeSession } from "@/composables/useSpcodeSession";
import {
  parseSpcodeStashDrop,
  parseSpcodeStashList,
  parseSpcodeStashPop,
  parseSpcodeStashPush,
  type SpcodeStashDropSnapshot,
  type SpcodeStashListSnapshot,
  type SpcodeStashPopSnapshot,
  type SpcodeStashPushSnapshot,
} from "./parseSpcodeGitStash";

export type StashListFetchState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; snapshot: SpcodeStashListSnapshot }
  | { kind: "error"; reason: string };

export interface StashParams {
  message?: string;
  worktree?: string | null;
  umo?: string | null;
}

export type StashResult =
  | { ok: true; snapshot: SpcodeStashPushSnapshot }
  | { ok: false; reason: string; stderr?: string };

export interface StashPopParams {
  index: number;
  /** Display ref ("stash@{0}") — keys the per-entry loading state. */
  ref: string;
  worktree?: string | null;
  umo?: string | null;
}

export type StashPopResult =
  | { ok: true; snapshot: SpcodeStashPopSnapshot }
  | { ok: false; reason: string; stderr?: string };

export type StashDropResult =
  | { ok: true; snapshot: SpcodeStashDropSnapshot }
  | { ok: false; reason: string; stderr?: string };

export interface UseSpcodeGitStash {
  listState: Ref<StashListFetchState>;
  isStashing: Ref<boolean>;
  /** Ref ("stash@{N}") of the entry currently being popped, or null. */
  popping: Ref<string | null>;
  /** Ref ("stash@{N}") of the entry currently being dropped, or null. */
  dropping: Ref<string | null>;
  refreshList: () => Promise<void>;
  stash: (params: StashParams) => Promise<StashResult>;
  pop: (params: StashPopParams) => Promise<StashPopResult>;
  drop: (params: StashPopParams) => Promise<StashDropResult>;
  dispose: () => void;
}

export function useSpcodeGitStash(
  worktreeRef: MaybeRef<string | null> = null,
): UseSpcodeGitStash {
  const listState = ref<StashListFetchState>({ kind: "idle" });
  const isStashing = ref(false);
  const popping = ref<string | null>(null);
  const dropping = ref<string | null>(null);
  const session = useSpcodeSession({ requireScoped: true });
  let listAbort: AbortController | null = null;
  let pushAbort: AbortController | null = null;
  let popAbort: AbortController | null = null;
  let dropAbort: AbortController | null = null;
  let isMounted = true;

  // Shared single-flight guard for the two index-based mutations (pop
  // and drop): both shift the indices of the entries after them.
  function mutationInFlight(): boolean {
    return popping.value !== null || dropping.value !== null;
  }

  async function refreshList(): Promise<void> {
    if (!isMounted) return;
    const umo = session.umo.value;
    if (!umo) {
      listState.value = { kind: "error", reason: "no_project_loaded" };
      return;
    }
    listAbort?.abort();
    listAbort = new AbortController();
    const isFirst = listState.value.kind !== "ok";
    if (isFirst) listState.value = { kind: "loading" };
    try {
      const worktree = toValue(worktreeRef);
      const resp = await pluginExtensionApi.get<unknown>("spcode/git-stash", {
        params: { umo, ...(worktree ? { worktree } : {}) },
        signal: listAbort.signal,
      });
      if (!isMounted) return;
      const parsed = parseSpcodeStashList(resp.data);
      if (parsed.kind !== "ok") throw new Error(parsed.reason);
      listState.value = { kind: "ok", snapshot: parsed.snapshot };
    } catch (err) {
      if (!isMounted) return;
      if ((err as { name?: string })?.name === "CanceledError") return;
      listState.value = {
        kind: "error",
        reason: err instanceof Error ? err.message : "unknown",
      };
    }
  }

  async function stash(params: StashParams): Promise<StashResult> {
    if (!isMounted) return { ok: false, reason: "aborted" };
    pushAbort?.abort();
    pushAbort = new AbortController();
    isStashing.value = true;
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        "spcode/git-stash",
        {
          ...(params.message ? { message: params.message } : {}),
          ...(params.worktree ? { worktree: params.worktree } : {}),
          ...(params.umo ? { umo: params.umo } : {}),
        },
        { signal: pushAbort.signal },
      );
      if (!isMounted) return { ok: false, reason: "aborted" };
      const parsed = parseSpcodeStashPush(resp.data);
      if (parsed.kind !== "ok") {
        return { ok: false, reason: "unknown" };
      }
      const snapshot = parsed.snapshot;
      if (snapshot.success) {
        return { ok: true, snapshot };
      }
      return {
        ok: false,
        reason: snapshot.reason ?? "unknown",
        stderr: snapshot.stderr || undefined,
      };
    } catch (err) {
      if (!isMounted) return { ok: false, reason: "aborted" };
      if ((err as { name?: string })?.name === "CanceledError") {
        return { ok: false, reason: "aborted" };
      }
      const anyErr = err as { code?: string; message?: string };
      if (
        anyErr.code === "ERR_NETWORK" ||
        /network/i.test(anyErr.message ?? "")
      ) {
        return { ok: false, reason: "network" };
      }
      return { ok: false, reason: "unknown" };
    } finally {
      if (isMounted) isStashing.value = false;
    }
  }

  async function pop(params: StashPopParams): Promise<StashPopResult> {
    if (!isMounted) return { ok: false, reason: "aborted" };
    if (mutationInFlight()) return { ok: false, reason: "aborted" };
    popping.value = params.ref;
    popAbort = new AbortController();
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        "spcode/git-stash-pop",
        {
          index: params.index,
          ...(params.worktree ? { worktree: params.worktree } : {}),
          ...(params.umo ? { umo: params.umo } : {}),
        },
        { signal: popAbort.signal },
      );
      if (!isMounted) return { ok: false, reason: "aborted" };
      const parsed = parseSpcodeStashPop(resp.data);
      if (parsed.kind !== "ok") {
        return { ok: false, reason: "unknown" };
      }
      const snapshot = parsed.snapshot;
      if (snapshot.success) {
        return { ok: true, snapshot };
      }
      return {
        ok: false,
        reason: snapshot.reason ?? "unknown",
        stderr: snapshot.stderr || undefined,
      };
    } catch (err) {
      if (!isMounted) return { ok: false, reason: "aborted" };
      if ((err as { name?: string })?.name === "CanceledError") {
        return { ok: false, reason: "aborted" };
      }
      const anyErr = err as { code?: string; message?: string };
      if (
        anyErr.code === "ERR_NETWORK" ||
        /network/i.test(anyErr.message ?? "")
      ) {
        return { ok: false, reason: "network" };
      }
      return { ok: false, reason: "unknown" };
    } finally {
      if (isMounted) popping.value = null;
    }
  }

  async function drop(params: StashPopParams): Promise<StashDropResult> {
    if (!isMounted) return { ok: false, reason: "aborted" };
    if (mutationInFlight()) return { ok: false, reason: "aborted" };
    dropping.value = params.ref;
    dropAbort = new AbortController();
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        "spcode/git-stash-drop",
        {
          index: params.index,
          ...(params.worktree ? { worktree: params.worktree } : {}),
          ...(params.umo ? { umo: params.umo } : {}),
        },
        { signal: dropAbort.signal },
      );
      if (!isMounted) return { ok: false, reason: "aborted" };
      const parsed = parseSpcodeStashDrop(resp.data);
      if (parsed.kind !== "ok") {
        return { ok: false, reason: "unknown" };
      }
      const snapshot = parsed.snapshot;
      if (snapshot.success) {
        return { ok: true, snapshot };
      }
      return {
        ok: false,
        reason: snapshot.reason ?? "unknown",
        stderr: snapshot.stderr || undefined,
      };
    } catch (err) {
      if (!isMounted) return { ok: false, reason: "aborted" };
      if ((err as { name?: string })?.name === "CanceledError") {
        return { ok: false, reason: "aborted" };
      }
      const anyErr = err as { code?: string; message?: string };
      if (
        anyErr.code === "ERR_NETWORK" ||
        /network/i.test(anyErr.message ?? "")
      ) {
        return { ok: false, reason: "network" };
      }
      return { ok: false, reason: "unknown" };
    } finally {
      if (isMounted) dropping.value = null;
    }
  }

  function dispose(): void {
    isMounted = false;
    listAbort?.abort();
    pushAbort?.abort();
    popAbort?.abort();
    dropAbort?.abort();
    listAbort = null;
    pushAbort = null;
    popAbort = null;
    dropAbort = null;
  }

  return {
    listState,
    isStashing,
    popping,
    dropping,
    refreshList,
    stash,
    pop,
    drop,
    dispose,
  };
}
