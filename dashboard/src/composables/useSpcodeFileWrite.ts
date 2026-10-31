// Author: elecvoid243
// Date: 2026-07-17
// Vue composable wrapping POST /spcode/file-write — the generic
// text-file overwrite endpoint backing the workspace file-browser
// editor (POST /spcode/docs is markdown-only by design, so code files
// go through this endpoint). Lifecycle mirrors useSpcodeDocs.save.

import { ref, toValue, type MaybeRef } from "vue";
import { pluginExtensionApi } from "@/api/v1";
import { useSpcodeSession } from "@/composables/useSpcodeSession";

export interface SavedFileSnapshot {
  path: string;
  content: string;
  revision?: string;
  encoding?: string;
  newline?: string;
  size?: number;
  requestId?: number;
}

export type FileWriteResult =
  | {
      ok: true;
      revision?: string;
      encoding?: string;
      newline?: string;
      size?: number;
    }
  | { ok: false; reason: string; revision?: string };
type SaveParams = {
  path: string;
  content: string;
  baseRevision?: string;
  encoding?: string;
  newline?: string;
};

export interface UseSpcodeFileWrite {
  isSaving: import("vue").Ref<boolean>;
  save: (params: SaveParams) => Promise<FileWriteResult>;
  dispose: () => void;
}

export function useSpcodeFileWrite(
  worktreeRef: MaybeRef<string | null> = null,
): UseSpcodeFileWrite {
  const session = useSpcodeSession({ requireScoped: true });
  const isSaving = ref(false);
  let ctrl: AbortController | null = null;
  let isMounted = true;
  let generation = 0;

  async function save(params: SaveParams): Promise<FileWriteResult> {
    if (!isMounted) return { ok: false, reason: "aborted" };
    const request = ++generation;
    ctrl?.abort();
    ctrl = new AbortController();
    isSaving.value = true;
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        params.baseRevision !== undefined
          ? "spcode/host-file-write"
          : "spcode/file-write",
        {
          path: params.path,
          content: params.content,
          ...(params.baseRevision !== undefined
            ? {
                base_revision: params.baseRevision,
                ...(params.encoding ? { encoding: params.encoding } : {}),
                ...(params.newline ? { newline: params.newline } : {}),
              }
            : {
                umo: session.umo.value ?? undefined,
                worktree: toValue(worktreeRef) ?? undefined,
              }),
        },
        { signal: ctrl.signal },
      );
      if (!isMounted || request !== generation)
        return { ok: false, reason: "aborted" };
      if (
        params.baseRevision !== undefined &&
        (resp.data as { status?: string })?.status === "error"
      ) {
        return { ok: false, reason: "spcode_unavailable" };
      }
      const data = (
        resp.data as {
          data?: {
            saved?: boolean;
            reason?: string;
            revision?: string;
            encoding?: string;
            newline?: string;
            size?: number;
          };
        }
      )?.data;
      if (
        data?.saved === true &&
        (params.baseRevision === undefined || typeof data.revision === "string")
      ) {
        return {
          ok: true,
          revision: data.revision,
          ...(data.encoding ? { encoding: data.encoding } : {}),
          ...(data.newline ? { newline: data.newline } : {}),
          ...(typeof data.size === "number" ? { size: data.size } : {}),
        };
      }
      return {
        ok: false,
        reason: data?.reason || "unknown",
        revision: data?.revision,
      };
    } catch (err) {
      if (!isMounted || request !== generation)
        return { ok: false, reason: "aborted" };
      const e = err as { name?: string; code?: string; message?: string };
      if (e?.name === "CanceledError") return { ok: false, reason: "aborted" };
      if (e?.code === "ERR_NETWORK" || /network/i.test(e?.message ?? "")) {
        return { ok: false, reason: "network" };
      }
      return { ok: false, reason: "unknown" };
    } finally {
      if (isMounted && request === generation) isSaving.value = false;
    }
  }

  function dispose(): void {
    isMounted = false;
    ctrl?.abort();
    ctrl = null;
  }

  return { isSaving, save, dispose };
}
