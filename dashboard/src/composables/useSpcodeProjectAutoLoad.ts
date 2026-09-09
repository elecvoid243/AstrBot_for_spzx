// dashboard/src/composables/useSpcodeProjectAutoLoad.ts
//
// Silent (no chat-history pollution) spcode project-load driver for
// ChatUI "project"-typed projects. Called by the Chat.vue currSessionId
// watcher (Task 14) every time the active session changes.
//
// Behavior contract (mirrors spcode endpoint spec):
//   - workspace_type !== "custom"  -> return null (no-op)
//   - missing workspace_path        -> return null (no-op)
//   - the AGENTS.md / Codegraph load steps follow the project's
//     spcode_no_agentsmd / spcode_no_codegraph flags (same chips the
//     project dialog shows). Silent loading itself is always on since
//     2026-09-09 — the legacy spcode_auto_load switch is no longer read.
//   - same (project, umo) concurrent calls share one in-flight promise
//   - response success -> return data
//   - response reason "no_project_loaded" + previous_directory matches
//     workspace_path -> treat as success (idempotent re-entry)
//   - response reason "no_project_loaded" + true mismatch -> retry once
//     with force=true (the bound project is the source of truth)
//   - any other failure -> throw ProjectLoadError
//
// Author: elecvoid243

import { ref } from "vue";
import { pluginExtensionApi } from "@/api/v1";
import type { Project } from "@/components/chat/ProjectList.vue";

export type ProjectLoadReason =
  | "invalid_body"
  | "invalid_param"
  | "feature_disabled"
  | "no_project_loaded"
  | "path_unsafe"
  | "git_error"
  | "network_timeout"
  | "unknown";

export interface ProjectLoadData {
  loaded: boolean;
  directory: string;
  umo: string;
  skipped_substeps: string[];
  substep_messages: string[];
  previous_directory?: string;
  silent_reason?: string;
}

export interface ProjectLoadResponse {
  success: boolean;
  reason: ProjectLoadReason | null;
  elapsed_ms: number;
  data: ProjectLoadData;
}

/** Thrown when the silent project-load endpoint reports a non-success result
 *  or the network call itself fails. The reason string is suitable for i18n. */
export class ProjectLoadError extends Error {
  constructor(
    public reason: ProjectLoadReason,
    public data: ProjectLoadData,
  ) {
    super(`Project load failed: ${reason}`);
    this.name = "ProjectLoadError";
  }
}

export interface SilentLoadRequest {
  project: Project;
  umo: string;
  signal?: AbortSignal;
  timeoutMs?: number;
}

const DEFAULT_TIMEOUT_MS = 30_000;
const inflight = new Map<string, Promise<ProjectLoadData | null>>();

// ── Dirty tag: "session already loaded the project once" (2026-09-01) ──
// Records, per session, that a project load has *already been completed*
// in this frontend session. Keyed by sessionId (Chat.vue has it at every
// relevant call site: auto-load, session delete). A fast-path check in
// Chat.vue skips the whole /spcode/project-load round-trip when the tag
// exists AND both the project id and the backend boot id still match.
//
// Why boot id? tools/project/state is process memory. When AstrBot
// restarts, the backend forgets everything and a session that was
// "already loaded" must be loaded again. The boot id from
// /spcode/project-status lets us detect that restart cheaply.
//
// Invalidation (all handled by callers):
//   - manual unload            -> clearSessionLoadedTag
//   - manual load (any mode)   -> clearSessionLoadedTag (next auto-load re-evaluates)
//   - session deleted          -> clearSessionLoadedTag
//   - project binding changed  -> projectId mismatch in isSessionLoadedTag
//   - backend restarted        -> bootId mismatch in isSessionLoadedTag
interface SessionLoadTag {
  projectId: string;
  bootId: string | null;
}

const sessionLoadTags = new Map<string, SessionLoadTag>();

/** True when the session already loaded this exact project under this
 * backend process — callers may skip the silent project-load entirely.
 *
 * bootId null (never observed yet, e.g. old backend without boot_id)
 * is treated as "unknown process generation": the tag still applies,
 * because without a boot id we can't tell a restart apart, and the
 * backend's own idempotent rejection remains the safety net.
 */
export function isSessionLoadedTag(
  sessionId: string,
  projectId: string,
  bootId: string | null,
): boolean {
  const tag = sessionLoadTags.get(sessionId);
  if (!tag) return false;
  if (tag.projectId !== projectId) return false;
  if (bootId !== null && tag.bootId !== bootId) return false;
  return true;
}

/** Mark the session as having completed a project load for this identity. */
export function markSessionLoadedTag(
  sessionId: string,
  projectId: string,
  bootId: string | null,
): void {
  sessionLoadTags.set(sessionId, { projectId, bootId });
}

/** Drop the tag (manual unload, manual reload to another directory,
 * session deletion). The next switch re-runs the auto-load path. */
export function clearSessionLoadedTag(sessionId: string): void {
  sessionLoadTags.delete(sessionId);
}

/** Wipe all tags (e.g. backend boot id changed globally). */
export function clearAllSessionLoadedTags(): void {
  sessionLoadTags.clear();
}

/**
 * Normalize a path for equality comparison ONLY (never sent to the
 * backend — the server resolves the original workspace_path itself).
 * Handles trailing separators, mixed / and \, and Windows drive-letter
 * case so "same directory" always compares equal (2026-08-07).
 */
function normalizePathForCompare(p: string): string {
  return p
    .trim()
    .replace(/[\\/]+$/, "")
    .replace(/\//g, "\\")
    .toLowerCase();
}

function inflightKey(project: Project, umo: string): string {
  return `${project.project_id}::${umo}`;
}

async function postLoad(
  req: SilentLoadRequest,
  force: boolean,
): Promise<ProjectLoadResponse> {
  const controller = new AbortController();
  const timeoutMs = req.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const onExternalAbort = () => controller.abort();
  req.signal?.addEventListener("abort", onExternalAbort);
  try {
    // pluginExtensionApi.post signature: (pluginPath, data?, config?)
    // We pass the body as the 2nd arg and the AbortSignal via AxiosRequestConfig.
    const res = await pluginExtensionApi.post<ProjectLoadResponse>(
      "spcode/project-load",
      {
        directory: req.project.workspace_path!,
        umo: req.umo,
        force,
        no_agentsmd: req.project.spcode_no_agentsmd || undefined,
        no_codegraph: req.project.spcode_no_codegraph || undefined,
      },
      { signal: controller.signal },
    );
    // spcode's _make_envelope returns { status: "ok", data: { success, loaded,
    // directory, reason, ... } } — all fields are flat inside `data`, there is
    // no nested `data` sub-object.  We must reshape it into the
    // ProjectLoadResponse shape that silentLoad expects.
    const raw = res.data.data as unknown as Record<string, unknown>;
    return {
      success: Boolean(raw.success),
      reason: (raw.reason as ProjectLoadReason | null) ?? null,
      elapsed_ms: Number(raw.elapsed_ms) || 0,
      data: {
        loaded: Boolean(raw.loaded),
        directory: String(raw.directory ?? ""),
        umo: String(raw.umo ?? req.umo),
        skipped_substeps: (raw.skipped_substeps as string[]) ?? [],
        substep_messages: (raw.substep_messages as string[]) ?? [],
        previous_directory: raw.previous_directory
          ? String(raw.previous_directory)
          : undefined,
        silent_reason: raw.silent_reason
          ? String(raw.silent_reason)
          : undefined,
      },
    };
  } catch (err) {
    const aborted =
      controller.signal.aborted ||
      (err as { name?: string })?.name === "AbortError";
    throw new ProjectLoadError(aborted ? "network_timeout" : "unknown", {
      loaded: false,
      directory: req.project.workspace_path ?? "",
      umo: req.umo,
      skipped_substeps: [],
      substep_messages: [String((err as Error)?.message ?? err)],
    });
  } finally {
    clearTimeout(timer);
    req.signal?.removeEventListener("abort", onExternalAbort);
  }
}

export function useSpcodeProjectAutoLoad() {
  async function silentLoad(
    req: SilentLoadRequest,
  ): Promise<ProjectLoadData | null> {
    const { project, umo } = req;
    // spcode integration lives on the CUSTOM workspace type (2026-08-07):
    // custom's workspace_path is the session cwd AND the spcode mount
    // target, which is the only coherent binding. "project"-type paths
    // never affect cwd, so spcode no longer attaches to them.
    //
    // Silent loading is always on (2026-09-09): the spcode_auto_load
    // switch was removed from the UI and is no longer read here.
    if (project.workspace_type !== "custom") return null;
    if (!project.workspace_path) return null;

    const key = inflightKey(project, umo);
    const existing = inflight.get(key);
    if (existing) return existing;

    const promise = (async (): Promise<ProjectLoadData | null> => {
      // First attempt never forces: the server's idempotent rejection
      // (no_project_loaded + previous_directory) is the fast path for
      // "already in the desired state" (2026-08-07).
      let resp = await postLoad(req, false);

      if (resp.success) return resp.data;

      // Idempotent re-entry: server says "already loaded", and the loaded
      // directory is ours after path normalization -- treat as success.
      if (
        resp.reason === "no_project_loaded" &&
        resp.data.previous_directory &&
        normalizePathForCompare(resp.data.previous_directory) ===
          normalizePathForCompare(project.workspace_path!)
      ) {
        return { ...resp.data, loaded: true };
      }

      // True mismatch: the session's bound project is the source of
      // truth (design 2026-08-07), so always retry once with force=true.
      if (!resp.success && resp.reason === "no_project_loaded") {
        resp = await postLoad(req, true);
        if (resp.success) return resp.data;
      }

      throw new ProjectLoadError(resp.reason ?? "unknown", resp.data);
    })();

    inflight.set(key, promise);
    try {
      return await promise;
    } finally {
      inflight.delete(key);
    }
  }

  return { silentLoad };
}

// ── Manual-dialog silent operations (2026-08-06) ──────────────────────
// The silent endpoints below serve the ProjectLoadDialog's manual load /
// unload / codegraph-set paths. Unlike `silentLoad` (auto-load, with
// idempotent retry semantics), these are one-shot and surface failures by
// throwing ProjectLoadError so the caller can toast + drive the chip.

export interface SilentLoadDirectoryRequest {
  umo: string;
  directory: string;
  noAgentsmd?: boolean;
  noCodegraph?: boolean;
  force?: boolean;
  create?: boolean;
  gitInit?: boolean;
  timeoutMs?: number;
}

/** Shared POST with timeout/abort plumbing; returns the flattened data dict. */
async function postSilent(
  path: string,
  body: Record<string, unknown>,
  timeoutMs: number,
): Promise<Record<string, unknown>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await pluginExtensionApi.post(path, body, {
      signal: controller.signal,
    });
    return ((res.data as { data?: Record<string, unknown> })?.data ??
      {}) as Record<string, unknown>;
  } catch (err) {
    throw new ProjectLoadError("network_timeout", {
      loaded: false,
      directory: String(body.directory ?? ""),
      umo: String(body.umo ?? ""),
      skipped_substeps: [],
      substep_messages: [String((err as Error)?.message ?? err)],
    });
  } finally {
    clearTimeout(timer);
  }
}

/** Throw ProjectLoadError when the envelope reports failure. */
function raiseOnFailure(raw: Record<string, unknown>, umo: string): void {
  if (raw.success) return;
  throw new ProjectLoadError(
    (raw.reason as ProjectLoadReason | null) ?? "unknown",
    {
      loaded: Boolean(raw.loaded),
      directory: String(raw.directory ?? ""),
      umo,
      skipped_substeps: (raw.skipped_substeps as string[]) ?? [],
      substep_messages: (raw.substep_messages as string[]) ?? [],
      previous_directory: raw.previous_directory
        ? String(raw.previous_directory)
        : undefined,
      silent_reason: raw.silent_reason ? String(raw.silent_reason) : undefined,
    },
  );
}

export function useSpcodeSilentOps() {
  /** Silent project load for the manual dialog path (all flags explicit). */
  async function silentLoadDirectory(
    req: SilentLoadDirectoryRequest,
  ): Promise<ProjectLoadData> {
    const body: Record<string, unknown> = {
      directory: req.directory,
      umo: req.umo,
      force: req.force === true,
    };
    if (req.noAgentsmd) body.no_agentsmd = true;
    if (req.noCodegraph) body.no_codegraph = true;
    if (req.create) body.create = true;
    if (req.gitInit) body.git_init = true;
    const raw = await postSilent(
      "spcode/project-load",
      body,
      req.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    );
    raiseOnFailure(raw, req.umo);
    return {
      loaded: Boolean(raw.loaded),
      directory: String(raw.directory ?? ""),
      umo: String(raw.umo ?? req.umo),
      skipped_substeps: (raw.skipped_substeps as string[]) ?? [],
      substep_messages: (raw.substep_messages as string[]) ?? [],
      previous_directory: raw.previous_directory
        ? String(raw.previous_directory)
        : undefined,
      silent_reason: raw.silent_reason ? String(raw.silent_reason) : undefined,
    };
  }

  /** Silent project unload. Throws ProjectLoadError on failure. */
  async function silentUnload(umo: string): Promise<void> {
    const raw = await postSilent(
      "spcode/project-unload",
      { umo },
      DEFAULT_TIMEOUT_MS,
    );
    raiseOnFailure(raw, umo);
  }

  /** Silent codegraph set. Throws ProjectLoadError on failure. */
  async function silentCodegraphSet(
    umo: string,
    directory: string,
  ): Promise<void> {
    const raw = await postSilent(
      "spcode/codegraph-set",
      { umo, directory },
      200_000, // MCP restart timeout is 180 s on the backend
    );
    raiseOnFailure(raw, umo);
  }

  /** Silent codegraph init/update. Throws ProjectLoadError on failure. */
  async function silentCodegraphInit(
    umo: string,
    directory: string,
  ): Promise<void> {
    const raw = await postSilent(
      "spcode/codegraph-init",
      { umo, directory },
      600_000, // init 300 s + --force retry 180 s + margin
    );
    raiseOnFailure(raw, umo);
  }

  return {
    silentLoadDirectory,
    silentUnload,
    silentCodegraphSet,
    silentCodegraphInit,
  };
}
