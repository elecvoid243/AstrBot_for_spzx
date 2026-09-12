// Author: elecvoid243
// Date: 2026-09-12
//
// Vue composable wrapping POST /spcode/git-tag-create. Used by the
// commit flow (2026-09-12): after a successful commit the sidebar pins
// the requested tag to the exact new SHA (`rev`), so the tag cannot
// land on a different commit if HEAD moves between the two calls.
// Lifecycle mirrors useSpcodeGitCommit.ts (single in-flight call).

import { ref } from "vue";
import { pluginExtensionApi } from "@/api/v1";
import {
  parseSpcodeGitTagCreate,
  type SpcodeTagCreateSnapshot,
} from "./parseSpcodeGitWorkflow";

export interface UseSpcodeGitTag {
  isCreating: import("vue").Ref<boolean>;
  createTag: (params: TagCreateParams) => Promise<TagCreateResult>;
  dispose: () => void;
}

export interface TagCreateParams {
  tag: string;
  /** Commit to tag; backend defaults to HEAD when omitted. */
  rev?: string | null;
  worktree?: string | null;
  umo?: string | null;
}

export type TagCreateResult =
  | { ok: true; snapshot: SpcodeTagCreateSnapshot }
  | { ok: false; reason: string; stderr?: string };

export function useSpcodeGitTag(): UseSpcodeGitTag {
  const isCreating = ref(false);
  let abortController: AbortController | null = null;
  let isMounted = true;

  async function createTag(params: TagCreateParams): Promise<TagCreateResult> {
    if (!isMounted) return { ok: false, reason: "aborted" };
    abortController?.abort();
    abortController = new AbortController();
    isCreating.value = true;
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        "spcode/git-tag-create",
        {
          tag: params.tag,
          ...(params.rev ? { rev: params.rev } : {}),
          ...(params.worktree ? { worktree: params.worktree } : {}),
          ...(params.umo ? { umo: params.umo } : {}),
        },
        { signal: abortController.signal },
      );
      if (!isMounted) return { ok: false, reason: "aborted" };
      const parsed = parseSpcodeGitTagCreate(resp.data);
      if (parsed.kind !== "ok") {
        return { ok: false, reason: "unknown" };
      }
      const snap = parsed.snapshot;
      if (snap.success) {
        return { ok: true, snapshot: snap };
      }
      // Return the raw ReasonCode string — the caller runs
      // `classifyReason` exactly once (see useSpcodeGitCommit).
      return {
        ok: false,
        reason: snap.reason ?? "unknown",
        stderr: snap.stderr || undefined,
      };
    } catch (err) {
      if (!isMounted) return { ok: false, reason: "aborted" };
      if ((err as { name?: string })?.name === "CanceledError") {
        return { ok: false, reason: "aborted" };
      }
      const anyErr = err as { code?: string; message?: string };
      if (anyErr.code === "ERR_NETWORK" || /network/i.test(anyErr.message ?? "")) {
        return { ok: false, reason: "network" };
      }
      return { ok: false, reason: "unknown" };
    } finally {
      if (isMounted) isCreating.value = false;
    }
  }

  function dispose(): void {
    isMounted = false;
    abortController?.abort();
    abortController = null;
  }

  return { isCreating, createTag, dispose };
}
