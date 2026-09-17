// Author: elecvoid243 @ 2026-08-01
// Spec: docs/superpowers/specs/2026-08-01-git-merge-cherrypick-conflict-frontend-design.md §3.3
//
// Vue composable for the conflict lifecycle. Read path mirrors
// useSpcodeGitBranches (state machine / ETag / 30 s polling / dispose);
// mutations (resolve / continue / abort) are single-flight and trigger an
// immediate refresh on success so the banner stays truthful.

import { ref, watch, toValue, type MaybeRef, type Ref } from "vue";
import { pluginExtensionApi } from "@/api/v1";
import { useSpcodeSession } from "@/composables/useSpcodeSession";
import {
  parseSpcodeConflictStatus,
  parseSpcodeConflictResolve,
  parseSpcodeConflictContinue,
  parseSpcodeConflictAbort,
  buildResolveBody,
  type ConflictSnapshot,
  type SpcodeResolveParams,
  type SpcodeResolveResult,
  type SpcodeConflictActionResult,
} from "./parseSpcodeGitConflict";

export type ConflictFetchState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; snapshot: ConflictSnapshot; notModified?: boolean }
  | { kind: "error"; reason: string; previousSnapshot?: ConflictSnapshot };

export interface UseSpcodeGitConflict {
  state: Ref<ConflictFetchState>;
  refresh: () => Promise<void>;
  startPolling: (intervalMs?: number) => void;
  stopPolling: () => void;
  resolve: (params: SpcodeResolveParams) => Promise<SpcodeResolveResult>;
  continueOp: (message?: string) => Promise<SpcodeConflictActionResult>;
  abort: () => Promise<SpcodeConflictActionResult>;
  dispose: () => void;
}

const DEFAULT_POLL_MS = 30_000;

function classifyThrown(err: unknown): string {
  if ((err as { name?: string })?.name === "CanceledError") return "aborted";
  const anyErr = err as { code?: string; message?: string };
  if (anyErr.code === "ERR_NETWORK" || /network/i.test(anyErr.message ?? "")) {
    return "network";
  }
  return "unknown";
}

export function useSpcodeGitConflict(
  worktreeRef: MaybeRef<string | null> = null,
): UseSpcodeGitConflict {
  const state = ref<ConflictFetchState>({ kind: "idle" });
  const session = useSpcodeSession({ requireScoped: true });
  let abortController: AbortController | null = null;
  let mutationAbort: AbortController | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let isMounted = true;
  const etagMap = new Map<string, string>();
  const prevSnapshotMap = new Map<string, ConflictSnapshot>();

  function etagKey(): string {
    const umo = session.umo.value ?? "null";
    return `conflict|${umo}|${toValue(worktreeRef) ?? "null"}`;
  }

  async function refresh(): Promise<void> {
    if (!isMounted) return;
    const umo = session.umo.value ?? null;
    if (!umo) {
      state.value = { kind: "error", reason: "no_project_loaded" };
      return;
    }
    abortController?.abort();
    abortController = new AbortController();
    const isFirst = state.value.kind !== "ok";
    if (isFirst) state.value = { kind: "loading" };
    const key = etagKey();
    const etag = etagMap.get(key);
    const worktree = toValue(worktreeRef);
    try {
      const resp = await pluginExtensionApi.get<unknown>(
        "spcode/git-conflict-status",
        {
          params: { umo, ...(worktree ? { worktree } : {}) },
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
      const snap = parseSpcodeConflictStatus(resp.data);
      prevSnapshotMap.set(key, snap);
      const headers = resp.headers as Record<string, string> | undefined;
      const newEtag = headers?.["etag"] ?? headers?.["ETag"];
      if (newEtag) etagMap.set(key, newEtag);
      state.value = { kind: "ok", snapshot: snap, notModified: false };
    } catch (err) {
      if (!isMounted) return;
      if ((err as { name?: string })?.name === "CanceledError") return;
      const prev = state.value.kind === "ok" ? state.value.snapshot : undefined;
      state.value = {
        kind: "error",
        reason: classifyThrown(err),
        previousSnapshot: prev,
      };
    }
  }

  watch(
    [() => session.umo.value, () => toValue(worktreeRef)],
    () => {
      if (!isMounted) return;
      etagMap.clear();
      void refresh();
    },
  );

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

  // ── Mutations (single-flight) ──────────────────────────

  async function runMutation(
    endpoint: string,
    extraBody: Record<string, unknown>,
    parser: (raw: unknown) => SpcodeResolveResult | SpcodeConflictActionResult,
  ): Promise<SpcodeResolveResult | SpcodeConflictActionResult> {
    if (!isMounted) return { ok: false, reason: "aborted" };
    const umo = session.umo.value ?? null;
    if (!umo) return { ok: false, reason: "no_project_loaded" };
    mutationAbort?.abort();
    mutationAbort = new AbortController();
    const worktree = toValue(worktreeRef);
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        endpoint,
        { umo, ...(worktree ? { worktree } : {}), ...extraBody },
        { signal: mutationAbort.signal },
      );
      if (!isMounted || mutationAbort.signal.aborted) {
        return { ok: false, reason: "aborted" };
      }
      const parsed = parser(resp.data);
      if (parsed.ok) void refresh();
      return parsed;
    } catch (err) {
      if (!isMounted) return { ok: false, reason: "aborted" };
      return { ok: false, reason: classifyThrown(err) };
    }
  }

  async function resolve(params: SpcodeResolveParams): Promise<SpcodeResolveResult> {
    return (await runMutation(
      "spcode/git-conflict-resolve",
      buildResolveBody(params),
      parseSpcodeConflictResolve,
    )) as SpcodeResolveResult;
  }

  async function continueOp(message?: string): Promise<SpcodeConflictActionResult> {
    return (await runMutation(
      "spcode/git-conflict-continue",
      message ? { message } : {},
      parseSpcodeConflictContinue,
    )) as SpcodeConflictActionResult;
  }

  async function abort(): Promise<SpcodeConflictActionResult> {
    return (await runMutation(
      "spcode/git-conflict-abort",
      {},
      parseSpcodeConflictAbort,
    )) as SpcodeConflictActionResult;
  }

  function dispose(): void {
    isMounted = false;
    stopPolling();
    abortController?.abort();
    abortController = null;
    mutationAbort?.abort();
    mutationAbort = null;
  }

  return {
    state,
    refresh,
    startPolling,
    stopPolling,
    resolve,
    continueOp,
    abort,
    dispose,
  };
}
