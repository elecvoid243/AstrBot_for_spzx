// Author: elecvoid243
// Date: 2026-10-06
// Spec: docs/superpowers/specs/2026-10-06-git-file-range-diff-frontend-design.md §3.1
//
// Vue composable wrapping GET /spcode/git-file-diff (任意两版本单文件比较).
// 结构照抄 useSpcodeGitShow:per-key 响应式缓存 Map + in-flight
// AbortController 去重 + ETag 透传 + worktree 感知。
// 缓存键含原始 from/to 字符串(不用解析后 sha):分支名输入在分支移动后
// 必须重新请求,不能命中旧缓存。

import { ref, toValue, type MaybeRef } from "vue";
import { pluginExtensionApi } from "@/api/v1";
import { useSpcodeSession } from "@/composables/useSpcodeSession";
import {
  parseSpcodeGitFileDiff,
  type GitFileDiffData,
} from "./parseSpcodeGitFileDiff";

export type { GitFileDiffData };

export type GitFileDiffFetchState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; data: GitFileDiffData }
  | { kind: "error"; reason: string };

export interface UseSpcodeGitFileDiff {
  /** Fetch (or no-op) for a (path, from, to) triple. Idempotent. */
  fetchDiff: (path: string, from: string, to: string) => Promise<void>;
  /** Read cached data. Returns null if not cached. */
  getData: (path: string, from: string, to: string) => GitFileDiffData | null;
  /** Read per-key state. Returns { kind: "idle" } for unseen keys. */
  getState: (path: string, from: string, to: string) => GitFileDiffFetchState;
  /** Clear every cached entry and ETag. Does NOT abort in-flight. */
  invalidateAll: () => void;
  /** Abort in-flight, drop all caches. */
  dispose: () => void;
}

export function useSpcodeGitFileDiff(
  worktreeRef: MaybeRef<string | null> = null,
): UseSpcodeGitFileDiff {
  const stateMap = ref<Map<string, GitFileDiffFetchState>>(new Map());
  const dataMap = ref<Map<string, GitFileDiffData>>(new Map());
  const etagMap = new Map<string, string>();
  const inflight = new Map<string, AbortController>();
  let isMounted = true;
  const session = useSpcodeSession();

  // NUL 分隔保证与单段 key 不冲突(同 useSpcodeGitShow 的 fileKey 惯例)。
  // worktree 必须参与身份:不同 worktree 的同名文件内容可不同;
  // getData/getState 内部同样取当前 worktree,调用方无需感知。
  function keyOf(path: string, from: string, to: string): string {
    return `${toValue(worktreeRef) ?? ""}\u0001${path}\u0001${from}\u0001${to}`;
  }
  function etagKeyOf(umo: string, worktree: string | null, key: string): string {
    return `${umo}|${worktree ?? ""}|${key}`;
  }

  function setState(key: string, next: GitFileDiffFetchState): void {
    const m = new Map(stateMap.value);
    m.set(key, next);
    stateMap.value = m;
  }
  function setData(key: string, next: GitFileDiffData): void {
    const m = new Map(dataMap.value);
    m.set(key, next);
    dataMap.value = m;
  }

  async function fetchDiff(path: string, from: string, to: string): Promise<void> {
    if (!isMounted) return;
    if (!path || !from || !to) return;
    const key = keyOf(path, from, to);
    if (inflight.has(key)) return;
    if (stateMap.value.get(key)?.kind === "ok") return;

    const umo = session.umo.value;
    if (!umo) {
      setState(key, { kind: "error", reason: "no_project_loaded" });
      return;
    }

    const ctrl = new AbortController();
    inflight.set(key, ctrl);
    setState(key, { kind: "loading" });

    const worktree = toValue(worktreeRef);
    const etag = etagMap.get(etagKeyOf(umo, worktree, key));

    try {
      const resp = await pluginExtensionApi.get<unknown>(
        "spcode/git-file-diff",
        {
          params: {
            umo,
            ...(worktree ? { worktree } : {}),
            path,
            from,
            to,
          },
          headers: etag ? { "If-None-Match": etag } : {},
          validateStatus: (s: number) => (s >= 200 && s < 300) || s === 304,
          signal: ctrl.signal,
        },
      );
      if (!isMounted) return;
      inflight.delete(key);

      if (resp.status === 304) {
        const prev = dataMap.value.get(key);
        setState(
          key,
          prev ? { kind: "ok", data: prev } : { kind: "error", reason: "unknown" },
        );
        return;
      }

      const parsed = parseSpcodeGitFileDiff(resp.data);
      if (!parsed) {
        // envelope 失败(reason 在 data.reason)或结构非法
        const reason =
          (resp.data as { data?: { reason?: string } } | null)?.data?.reason ??
          "unknown";
        setState(key, { kind: "error", reason });
        return;
      }

      const headers = resp.headers as Record<string, string> | undefined;
      const newEtag = headers?.["etag"] ?? headers?.["ETag"];
      if (newEtag) etagMap.set(etagKeyOf(umo, worktree, key), newEtag);

      setData(key, parsed);
      setState(key, { kind: "ok", data: parsed });
    } catch (err) {
      if (!isMounted) return;
      inflight.delete(key);
      if ((err as { name?: string })?.name === "CanceledError") return;
      const anyErr = err as { code?: string; message?: string };
      const reason =
        anyErr.code === "ERR_NETWORK" || /network/i.test(anyErr.message ?? "")
          ? "network"
          : "unknown";
      setState(key, { kind: "error", reason });
    }
  }

  function getData(path: string, from: string, to: string): GitFileDiffData | null {
    return dataMap.value.get(keyOf(path, from, to)) ?? null;
  }
  function getState(path: string, from: string, to: string): GitFileDiffFetchState {
    return stateMap.value.get(keyOf(path, from, to)) ?? { kind: "idle" };
  }

  function invalidateAll(): void {
    stateMap.value = new Map();
    dataMap.value = new Map();
    etagMap.clear();
  }

  function dispose(): void {
    isMounted = false;
    for (const ctrl of inflight.values()) ctrl.abort();
    inflight.clear();
    invalidateAll();
  }

  return { fetchDiff, getData, getState, invalidateAll, dispose };
}
