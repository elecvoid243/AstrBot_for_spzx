// Author: elecvoid243
// Date: 2026-07-18
// Vue composable wrapping POST /spcode/file-remove — the generic
// file-delete endpoint backing the workspace file-browser editor
// toolbar (DELETE /spcode/docs is markdown-only by design, so code
// files go through this endpoint). Lifecycle mirrors useSpcodeFileWrite.

import { ref, toValue, type MaybeRef } from "vue";
import { pluginExtensionApi } from "@/api/v1";
import { useSpcodeSession } from "@/composables/useSpcodeSession";

export type FileRemoveResult =
  | { ok: true; path?: string }
  | { ok: false; reason: string };

export interface UseSpcodeFileRemove {
  isRemoving: import("vue").Ref<boolean>;
  remove: (params: {
    path: string;
    host?: boolean;
  }) => Promise<FileRemoveResult>;
  dispose: () => void;
}

export function useSpcodeFileRemove(
  worktreeRef: MaybeRef<string | null> = null,
): UseSpcodeFileRemove {
  const session = useSpcodeSession({ requireScoped: true });
  const isRemoving = ref(false);
  let ctrl: AbortController | null = null;
  let isMounted = true;

  async function remove(params: {
    path: string;
    host?: boolean;
  }): Promise<FileRemoveResult> {
    if (!isMounted) return { ok: false, reason: "aborted" };
    ctrl?.abort();
    ctrl = new AbortController();
    isRemoving.value = true;
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        params.host ? "spcode/host-file-manage" : "spcode/file-remove",
        {
          path: params.path,
          ...(params.host ? { operation: "trash" } : {}),
          umo: params.host ? undefined : session.umo.value ?? undefined,
          worktree: params.host ? undefined : toValue(worktreeRef) ?? undefined,
        },
        { signal: ctrl.signal },
      );
      if (!isMounted) return { ok: false, reason: "aborted" };
      const payload = resp.data as {
        status?: string;
        data?: {
          renamed?: boolean;
          deleted?: boolean;
          path?: string;
          new_path?: string;
          reason?: string | null;
        };
      };
      const data = payload?.data;
      if (data?.deleted !== true || (params.host && !data.path)) {
        return {
          ok: false,
          reason:
            data?.reason ||
            (payload?.status === "error" ? "spcode_unavailable" : "unknown"),
        };
      }
      return { ok: true, path: data.path };
    } catch (err) {
      if (!isMounted) return { ok: false, reason: "aborted" };
      const e = err as { name?: string; code?: string; message?: string };
      if (e?.name === "CanceledError") return { ok: false, reason: "aborted" };
      if (e?.code === "ERR_NETWORK" || /network/i.test(e?.message ?? "")) {
        return { ok: false, reason: "network" };
      }
      return { ok: false, reason: "unknown" };
    } finally {
      if (isMounted) isRemoving.value = false;
    }
  }

  function dispose(): void {
    isMounted = false;
    ctrl?.abort();
    ctrl = null;
  }

  return { isRemoving, remove, dispose };
}
