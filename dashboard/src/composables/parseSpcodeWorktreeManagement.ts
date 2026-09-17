// Author: elecvoid243
// Date: 2026-06-27
// Spec: docs/superpowers/specs/2026-06-27-git-worktree-frontend-management-design.md §1.2
//
// Pure parser for the 4 git worktree management endpoints (git-worktree-add
// / git-worktree-remove / git-worktree-lock / git-worktree-unlock). No Vue /
// no axios. Mirrors parseSpcodeGitWorkflow.ts split.
//
// All 4 endpoints return the SAME envelope shape:
//   { status: "ok", data: { loaded, directory, umo, worktree, [endpoint-specific], worktrees, reason, stderr, elapsed_ms } }
// The `worktrees` field is the **refreshed complete list** of worktrees,
// which the consumer (useSpcodeWorktrees) uses to atomically replace
// its state — no extra GET roundtrip needed.

import type { SpcodeGitWorktreesSnapshot } from "./parseSpcodeWorktrees";

// ── Endpoint id union ──────────────────────────────────────
export type WorktreeMgmtEndpoint = "add" | "remove" | "lock" | "unlock" | "activate";

// ── Raw envelope shape (shared by all endpoints) ──────────
export interface SpcodeWorktreeMgmtRawData {
  loaded: boolean;
  directory: string | null;
  umo: string | null;
  worktree: string;
  // Endpoint-specific (optional in raw shape; present per endpoint).
  branch?: string | null;
  removed_path?: string;
  locked?: boolean;
  lock_reason?: string | null;
  // activate: resulting activation state (null when deactivated).
  active_worktree?: string | null;
  // The refreshed worktree list.
  worktrees: unknown[];
  reason: string | null;
  stderr: string;
  elapsed_ms: number;
}

export interface SpcodeWorktreeMgmtRawResponse {
  loaded?: boolean;
  directory?: string | null;
  umo?: string | null;
  worktree?: string;
  branch?: string | null;
  removed_path?: string;
  locked?: boolean;
  lock_reason?: string | null;
  active_worktree?: string | null;
  worktrees?: unknown[];
  reason?: string | null;
  stderr?: string;
  elapsed_ms?: number;
}

// ParseResult: error branch carries both `reason` (the ReasonCode from
// data.reason) and `stderr` (the raw git / handler error text from
// data.stderr). The reason alone is not enough — most git failure modes
// (e.g. non-existent base ref) surface as reason="git_error" with the
// actual error message in stderr. Callers MUST propagate stderr
// downstream so the snackbar can render it in the <pre> block.
export type ParseResult<T> =
  | { kind: "ok"; snapshot: T }
  | { kind: "error"; reason: string; stderr: string };

// ── Snapshot (consumer-facing) ────────────────────────────
//
// We embed the same `meta` shape SpcodeGitWorktreesSnapshot uses, so
// the useSpcodeWorktrees consumer can swap state with the refreshed
// list atomically.
export interface SpcodeWorktreeMgmtSnapshot {
  meta: {
    directory: string | null;
    umo: string | null;
    loaded: boolean;
    reason: string | null;
    stderr: string;
    elapsedMs: number;
    fetchedAt: number;
  };
  worktree: string;
  branch: string | null;
  removedPath: string | null;
  locked: boolean;
  lockReason: string | null;
  activeWorktree: string | null;
  worktrees: SpcodeGitWorktreesSnapshot["worktrees"];
}

// ── Envelope helpers (copied & adapted from parseSpcodeGitWorkflow) ─

function unwrapEnvelope(raw: unknown): unknown {
  if (typeof raw !== "object" || raw === null) {
    throw new Error("missing status envelope");
  }
  const env = raw as { status?: unknown; data?: unknown };
  if (env.status !== "ok") {
    throw new Error("unexpected status envelope");
  }
  if (typeof env.data !== "object" || env.data === null) {
    throw new Error("missing data in response");
  }
  return env.data;
}

function asString(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}
function asStringOrNull(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}
function asNumber(v: unknown, fallback = 0): number {
  return typeof v === "number" ? v : fallback;
}
function asBoolean(v: unknown, fallback = false): boolean {
  return typeof v === "boolean" ? v : fallback;
}

/** Build the snapshot's `meta` block + endpoint-specific fields.
 *  Pure function — takes the unwrapped `data` object and the consumer-
 *  facing field overrides. Centralizes the field-name mapping from
 *  snake_case (backend) to camelCase (frontend) so the 4 parsers
 *  share the same implementation. */
function buildSnapshot(
  d: SpcodeWorktreeMgmtRawData,
  overrides: Partial<{
    branch: string | null;
    removedPath: string | null;
    locked: boolean;
    lockReason: string | null;
  }> = {},
): SpcodeWorktreeMgmtSnapshot {
  return {
    meta: {
      directory: d.directory ?? null,
      umo: d.umo ?? null,
      loaded: Boolean(d.loaded),
      reason: d.reason ?? null,
      stderr: asString(d.stderr),
      elapsedMs: asNumber(d.elapsed_ms),
      fetchedAt: Date.now(),
    },
    worktree: asString(d.worktree),
    branch: overrides.branch !== undefined ? overrides.branch : asStringOrNull(d.branch),
    removedPath:
      overrides.removedPath !== undefined
        ? overrides.removedPath
        : (d.removed_path ?? null),
    locked: overrides.locked !== undefined ? overrides.locked : asBoolean(d.locked),
    lockReason:
      overrides.lockReason !== undefined
        ? overrides.lockReason
        : (d.lock_reason ?? null),
    activeWorktree: d.active_worktree ?? null,
    // We reuse the raw worktrees array as-is; useSpcodeWorktrees will
    // re-parse via parseSpcodeGitWorktrees() before swapping state.
    // The double-parse is intentional: it keeps the parser pure (no
    // import of parseSpcodeWorktrees here) and avoids drifting the
    // two parsers' field mappings.
    worktrees: (d.worktrees ?? []) as SpcodeGitWorktreesSnapshot["worktrees"],
  };
}

// ── Endpoint-specific parsers ─────────────────────────────
//
// All 4 share the same envelope shape; only the endpoint-specific
// field overrides differ. Each parser is 4-6 lines.

/** Parse the envelope from POST /spcode/git-worktree-add.
 *
 * The 4 management endpoints share the same envelope shape, so all 4
 * parsers run the same `data.reason` guard. If `reason` is non-null
 * the backend classified the call as failed (per API spec
 * docs/api/webapi-git-worktree-mgmt-api.md §2.3 / §3.1.5) — even though
 * HTTP is 200. The handlers (useSpcodeWorktrees.add etc.) must surface
 * this as a WorktreeMgmtResult with ok=false; otherwise the dialog
 * shows "created successfully" while git silently failed.
 *
 * Bug fix 2026-06-27: prior to this commit, the parsers unconditionally
 * returned `kind: "ok"` whenever the envelope was structurally valid,
 * discarding `data.reason`. For the most common failure mode (user
 * types a non-existent base ref) git returns `fatal: not a tree
 * object: <base>`, the backend maps it to `reason: "git_error"`, and
 * the UI displayed a green "created" toast — no error visible. */
export function parseSpcodeWorktreeAdd(
  raw: unknown,
): ParseResult<SpcodeWorktreeMgmtSnapshot> {
  const d = unwrapEnvelope(raw) as SpcodeWorktreeMgmtRawData;
  if (d.reason) {
    return { kind: "error", reason: d.reason, stderr: asString(d.stderr) };
  }
  return {
    kind: "ok",
    snapshot: buildSnapshot(d, { branch: d.branch ?? null }),
  };
}

/** Parse the envelope from POST /spcode/git-worktree-remove. */
export function parseSpcodeWorktreeRemove(
  raw: unknown,
): ParseResult<SpcodeWorktreeMgmtSnapshot> {
  const d = unwrapEnvelope(raw) as SpcodeWorktreeMgmtRawData;
  if (d.reason) {
    return { kind: "error", reason: d.reason, stderr: asString(d.stderr) };
  }
  return {
    kind: "ok",
    snapshot: buildSnapshot(d, { removedPath: d.removed_path ?? d.worktree }),
  };
}

/** Parse the envelope from POST /spcode/git-worktree-lock. */
export function parseSpcodeWorktreeLock(
  raw: unknown,
): ParseResult<SpcodeWorktreeMgmtSnapshot> {
  const d = unwrapEnvelope(raw) as SpcodeWorktreeMgmtRawData;
  if (d.reason) {
    return { kind: "error", reason: d.reason, stderr: asString(d.stderr) };
  }
  return {
    kind: "ok",
    snapshot: buildSnapshot(d, {
      locked: true,
      lockReason: d.lock_reason ?? null,
    }),
  };
}

/** Parse the envelope from POST /spcode/git-worktree-unlock. */
export function parseSpcodeWorktreeUnlock(
  raw: unknown,
): ParseResult<SpcodeWorktreeMgmtSnapshot> {
  const d = unwrapEnvelope(raw) as SpcodeWorktreeMgmtRawData;
  if (d.reason) {
    return { kind: "error", reason: d.reason, stderr: asString(d.stderr) };
  }
  return {
    kind: "ok",
    snapshot: buildSnapshot(d, {
      locked: false,
      lockReason: null,
    }),
  };
}

/** Parse the envelope from POST /spcode/worktree-activate (2026-08-20).
 *
 * `active_worktree` is the authoritative post-mutation activation state
 * (the activated path, or null after deactivate); the consumer uses it to
 * update the snapshot's meta.activeWorktree. */
export function parseSpcodeWorktreeActivate(
  raw: unknown,
): ParseResult<SpcodeWorktreeMgmtSnapshot> {
  const d = unwrapEnvelope(raw) as SpcodeWorktreeMgmtRawData;
  if (d.reason) {
    return { kind: "error", reason: d.reason, stderr: asString(d.stderr) };
  }
  return {
    kind: "ok",
    snapshot: buildSnapshot(d),
  };
}

// ── Reason classification (spec §4) ──────────────────────

export interface ReasonMeta {
  i18nKey: string;
  color: "error" | "warning";
  withStderr?: boolean;
  withReason?: boolean;
}

export const WORKTREE_MGMT_REASON_CODES: Record<string, ReasonMeta> = {
  // 前置类
  feature_disabled:        { i18nKey: "error.reason.feature_disabled", color: "error" },
  no_project_loaded:       { i18nKey: "error.reason.no_project_loaded", color: "error" },
  worktree_invalid:        { i18nKey: "error.reason.worktree_invalid", color: "error" },
  directory_missing:       { i18nKey: "error.reason.directory_missing", color: "error" },
  not_a_git_repo:          { i18nKey: "error.reason.not_a_git_repo", color: "error" },
  git_unavailable:         { i18nKey: "error.reason.git_unavailable", color: "error" },
  git_error:               { i18nKey: "error.reason.git_error", color: "error", withStderr: true },
  // body 类
  invalid_body:            { i18nKey: "error.reason.invalid_body", color: "error" },
  invalid_branch:          { i18nKey: "error.reason.invalid_branch", color: "error" },
  invalid_param:           { i18nKey: "error.reason.invalid_param", color: "error" },
  // 路径类
  path_unsafe:             { i18nKey: "error.reason.path_unsafe", color: "error" },
  // 业务类(ADD)
  path_exists_nonempty:    { i18nKey: "error.reason.path_exists_nonempty", color: "warning" },
  cannot_create_existing:  { i18nKey: "error.reason.cannot_create_existing", color: "warning" },
  // 2026-09-17: git reports `fatal: invalid reference: <ref>` when the start
  // point does not exist (plugin maps it here — git_worktree_add.py
  // `_map_add_stderr_to_reason`), and `worktree_not_in_repo` when the
  // post-create git-common-dir check fails. Both were missing from this
  // table, so they rendered as `unknown`.
  cannot_checkout_missing: { i18nKey: "error.reason.cannot_checkout_missing", color: "warning" },
  worktree_not_in_repo:    { i18nKey: "error.reason.worktree_not_in_repo", color: "error" },
  // 业务类(REMOVE/LOCK/UNLOCK)
  worktree_not_found:      { i18nKey: "error.reason.worktree_not_found", color: "warning" },
  cannot_remove_main:      { i18nKey: "error.reason.cannot_remove_main", color: "error" },
  worktree_locked:         { i18nKey: "error.reason.worktree_locked", color: "warning" },
  worktree_dirty:          { i18nKey: "error.reason.worktree_dirty", color: "warning" },
  already_locked:          { i18nKey: "error.reason.already_locked", color: "warning" },
  not_locked:              { i18nKey: "error.reason.not_locked", color: "warning" },
  // 网络/未知
  network:                 { i18nKey: "error.reason.network", color: "error" },
  unknown:                 { i18nKey: "error.reason.unknown", color: "error", withReason: true },
};

/** Allowed reason codes per endpoint (spec §4.1). */
export const ALLOWED_WORKTREE_REASONS: Record<WorktreeMgmtEndpoint, readonly string[]> = {
  add: [
    "feature_disabled", "no_project_loaded", "worktree_invalid",
    "directory_missing", "not_a_git_repo", "git_unavailable", "git_error",
    "invalid_body", "invalid_branch", "invalid_param", "path_unsafe",
    "path_exists_nonempty", "cannot_create_existing",
    "cannot_checkout_missing", "worktree_not_in_repo",
  ],
  remove: [
    "feature_disabled", "no_project_loaded", "worktree_invalid",
    "directory_missing", "not_a_git_repo", "git_unavailable", "git_error",
    "invalid_body", "path_unsafe",
    "worktree_not_found", "cannot_remove_main", "worktree_locked", "worktree_dirty",
  ],
  lock: [
    "feature_disabled", "no_project_loaded", "worktree_invalid",
    "directory_missing", "not_a_git_repo", "git_unavailable", "git_error",
    "invalid_body", "path_unsafe",
    "worktree_not_found", "already_locked",
  ],
  unlock: [
    "feature_disabled", "no_project_loaded", "worktree_invalid",
    "directory_missing", "not_a_git_repo", "git_unavailable", "git_error",
    "invalid_body", "path_unsafe",
    "worktree_not_found", "not_locked",
  ],
  activate: [
    "feature_disabled", "no_project_loaded", "worktree_invalid",
    "directory_missing", "not_a_git_repo", "git_unavailable", "git_error",
    "invalid_body", "path_unsafe",
    "worktree_not_found",
  ],
};

/** Classify a reason string to a ReasonMeta.
 *  Returns `unknown` for null / undefined / unknown / endpoint-mismatched codes. */
export function classifyWorktreeReason(
  reason: string | null | undefined,
  endpoint: WorktreeMgmtEndpoint,
): ReasonMeta {
  if (reason === null || reason === undefined) {
    return WORKTREE_MGMT_REASON_CODES.unknown;
  }
  if (reason === "network") {
    return WORKTREE_MGMT_REASON_CODES.network;
  }
  if (!(ALLOWED_WORKTREE_REASONS[endpoint] as readonly string[]).includes(reason)) {
    return WORKTREE_MGMT_REASON_CODES.unknown;
  }
  return WORKTREE_MGMT_REASON_CODES[reason] ?? WORKTREE_MGMT_REASON_CODES.unknown;
}
