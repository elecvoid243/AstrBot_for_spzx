// Author: elecvoid243
// Date: 2026-09-09
// API: docs/api/webapi-git-reset-api.md (plugin v2.27.0)
//
// Vue composable wrapping POST /spcode/git-reset. Lifecycle mirrors
// useSpcodeGitRevert.ts: a single in-flight call (the user confirms
// one reset at a time from the History view), one boolean state
// surface, abort-on-reentry, dispose on unmount.
//
// The endpoint runs `git reset --<mode> <ref>`: it moves the current
// branch pointer (history is rewritten only in the sense that commits
// after <ref> leave the branch), is non-idempotent in side effects,
// and REQUIRES an explicit ref — the caller always supplies the full
// SHA from the log row plus the confirmed mode (soft/mixed/hard).

import { ref } from "vue";
import { pluginExtensionApi } from "@/api/v1";

export type GitResetMode = "soft" | "mixed" | "hard";

export interface UseSpcodeGitReset {
  isResetting: import("vue").Ref<boolean>;
  reset: (params: ResetParams) => Promise<ResetResult>;
  dispose: () => void;
}

export interface ResetParams {
  /** Full SHA of the target commit (from the log row in practice). */
  ref: string;
  /** Reset mode; backend defaults to "mixed" when omitted. */
  mode: GitResetMode;
  worktree?: string | null;
  umo?: string | null;
}

export interface ResetSnapshot {
  /** Full SHA of HEAD before the reset ("" if the read-back failed). */
  beforeSha: string;
  /** Full SHA the branch now points at (== resolved ref). */
  afterSha: string;
  /** Mode actually executed, echoed by the backend. */
  mode: string;
}

export type ResetResult =
  | { ok: true; snapshot: ResetSnapshot }
  | { ok: false; reason: string; stderr?: string };

export function useSpcodeGitReset(): UseSpcodeGitReset {
  const isResetting = ref(false);
  let abortController: AbortController | null = null;
  let isMounted = true;

  async function reset(params: ResetParams): Promise<ResetResult> {
    if (!isMounted) return { ok: false, reason: "aborted" };
    abortController?.abort();
    abortController = new AbortController();
    isResetting.value = true;
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        "spcode/git-reset",
        {
          ref: params.ref,
          mode: params.mode,
          ...(params.worktree ? { worktree: params.worktree } : {}),
          ...(params.umo ? { umo: params.umo } : {}),
        },
        { signal: abortController.signal },
      );
      if (!isMounted) return { ok: false, reason: "aborted" };
      // Inline parse (no dedicated parser file — the envelope is
      // shallow): success ⇔ data.reason === null && data.reset.
      const data = (
        resp.data as {
          data?: {
            reset?: boolean;
            reason?: string | null;
            stderr?: string;
            before_sha?: string;
            after_sha?: string;
            mode?: string;
          };
        }
      )?.data;
      if (data && data.reason == null && data.reset === true) {
        return {
          ok: true,
          snapshot: {
            beforeSha: typeof data.before_sha === "string" ? data.before_sha : "",
            afterSha: typeof data.after_sha === "string" ? data.after_sha : "",
            mode: typeof data.mode === "string" ? data.mode : params.mode,
          },
        };
      }
      // Failure envelope: reason is guaranteed on every failure path.
      return {
        ok: false,
        reason:
          typeof data?.reason === "string" && data.reason
            ? data.reason
            : "unknown",
        stderr:
          typeof data?.stderr === "string" && data.stderr
            ? data.stderr
            : undefined,
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
      if (isResetting.value && isMounted) isResetting.value = false;
    }
  }

  function dispose(): void {
    isMounted = false;
    abortController?.abort();
    abortController = null;
  }

  return { isResetting, reset, dispose };
}
