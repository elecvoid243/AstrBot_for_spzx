// Author: elecvoid243 @ 2026-08-01
// Spec: docs/superpowers/specs/2026-08-01-git-merge-cherrypick-conflict-frontend-design.md §3.3
//
// Vue composable wrapping POST /spcode/git-merge and
// POST /spcode/git-cherry-pick. Stateless-mutation pattern mirroring
// useSpcodeGitCommit: one in-flight call per operation, a busy ref per
// operation, reason codes surfaced verbatim for the caller to classify.

import { ref, type Ref } from "vue";
import { pluginExtensionApi } from "@/api/v1";
import { useSpcodeSession } from "@/composables/useSpcodeSession";
import {
  parseSpcodeGitMerge,
  parseSpcodeCherryPick,
  buildMergeBody,
  buildCherryPickBody,
  type SpcodeMergeParams,
  type SpcodeMergeResult,
  type SpcodeCherryPickParams,
  type SpcodeCherryPickResult,
} from "./parseSpcodeGitMerge";

export interface UseSpcodeGitMerge {
  isMerging: Ref<boolean>;
  isCherryPicking: Ref<boolean>;
  merge: (params: SpcodeMergeParams) => Promise<SpcodeMergeResult>;
  cherryPick: (params: SpcodeCherryPickParams) => Promise<SpcodeCherryPickResult>;
  dispose: () => void;
}

function classifyThrown(err: unknown): string {
  if ((err as { name?: string })?.name === "CanceledError") return "aborted";
  const anyErr = err as { code?: string; message?: string };
  if (anyErr.code === "ERR_NETWORK" || /network/i.test(anyErr.message ?? "")) {
    return "network";
  }
  return "unknown";
}

export function useSpcodeGitMerge(): UseSpcodeGitMerge {
  const isMerging = ref(false);
  const isCherryPicking = ref(false);
  const session = useSpcodeSession({ requireScoped: true });
  let mergeAbort: AbortController | null = null;
  let pickAbort: AbortController | null = null;
  let isMounted = true;

  async function merge(params: SpcodeMergeParams): Promise<SpcodeMergeResult> {
    if (!isMounted)
      return { ok: false, reason: "aborted", conflict: false, conflictedFiles: [] };
    const umo = session.umo.value;
    if (!umo)
      return {
        ok: false,
        reason: "no_project_loaded",
        conflict: false,
        conflictedFiles: [],
      };
    mergeAbort?.abort();
    mergeAbort = new AbortController();
    isMerging.value = true;
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        "spcode/git-merge",
        {
          umo,
          ...(params.worktree ? { worktree: params.worktree } : {}),
          ...buildMergeBody(params),
        },
        { signal: mergeAbort.signal },
      );
      if (!isMounted)
        return { ok: false, reason: "aborted", conflict: false, conflictedFiles: [] };
      return parseSpcodeGitMerge(resp.data);
    } catch (err) {
      if (!isMounted)
        return { ok: false, reason: "aborted", conflict: false, conflictedFiles: [] };
      const reason = classifyThrown(err);
      return { ok: false, reason, conflict: false, conflictedFiles: [] };
    } finally {
      if (isMounted) isMerging.value = false;
    }
  }

  async function cherryPick(
    params: SpcodeCherryPickParams,
  ): Promise<SpcodeCherryPickResult> {
    if (!isMounted)
      return { ok: false, reason: "aborted", conflict: false, conflictedFiles: [] };
    const umo = session.umo.value;
    if (!umo)
      return {
        ok: false,
        reason: "no_project_loaded",
        conflict: false,
        conflictedFiles: [],
      };
    pickAbort?.abort();
    pickAbort = new AbortController();
    isCherryPicking.value = true;
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        "spcode/git-cherry-pick",
        {
          umo,
          ...(params.worktree ? { worktree: params.worktree } : {}),
          ...buildCherryPickBody(params),
        },
        { signal: pickAbort.signal },
      );
      if (!isMounted)
        return { ok: false, reason: "aborted", conflict: false, conflictedFiles: [] };
      return parseSpcodeCherryPick(resp.data);
    } catch (err) {
      if (!isMounted)
        return { ok: false, reason: "aborted", conflict: false, conflictedFiles: [] };
      const reason = classifyThrown(err);
      return { ok: false, reason, conflict: false, conflictedFiles: [] };
    } finally {
      if (isMounted) isCherryPicking.value = false;
    }
  }

  function dispose(): void {
    isMounted = false;
    mergeAbort?.abort();
    mergeAbort = null;
    pickAbort?.abort();
    pickAbort = null;
  }

  return { isMerging, isCherryPicking, merge, cherryPick, dispose };
}
