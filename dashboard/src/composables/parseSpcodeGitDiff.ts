// Author: elecvoid243
// Date: 2026-06-17 (updated 2026-06-20 for scope echo; 2026-06-30
//       for empty-diff data-loss fix; 2026-10-25 for progressive
//       loading — see tests/parseSpcodeGitDiff.test.mjs)
// Spec: docs/superpowers/specs/2026-06-17-chatui-git-diff-sidebar-design.md §4.1.1
//      + docs/superpowers/specs/2026-06-20-git-diff-scope-switcher-design.md §4.2
//
// 2026-10-25 (v2.32.0 progressive loading): the sidebar's git-diff
// endpoint no longer ships patch text. `parseSpcodeGitDiff` consumes the
// paged metadata contract (files_changed + totals + etag + has_more);
// slices arrive on demand via GET /spcode/git-diff/file
// (see useSpcodeFileDiff.ts).

export type GitDiffScope = "unstaged" | "staged" | "all";

const VALID_SCOPES: ReadonlySet<GitDiffScope> = new Set([
  "unstaged",
  "staged",
  "all",
]);

/**
 * Narrow a free-form `scope` value (e.g. from the spcode response) to
 * the ``GitDiffScope`` union. Anything outside the three legal values
 * collapses to ``null`` so the UI never displays a phantom selection.
 */
function normalizeScope(raw: unknown): GitDiffScope | null {
  return VALID_SCOPES.has(raw as GitDiffScope) ? (raw as GitDiffScope) : null;
}

/** One paged metadata entry from GET /spcode/git-diff. */
export interface SpcodeGitDiffFileRaw {
  path: string;
  status: string;
  additions: number;
  deletions: number;
  is_binary?: boolean;
}

export interface SpcodeGitDiffRawResponse {
  loaded: boolean;
  directory: string | null;
  umo: string | null;
  scope?: GitDiffScope;
  etag?: string;
  files_changed: SpcodeGitDiffFileRaw[];
  stat: string | null;
  /** Patch text — ONLY present on GET /spcode/git-diff/full (commit
   *  dialog). The paged list endpoint never ships it. */
  diff?: string | null;
  total_files?: number;
  total_additions?: number;
  total_deletions?: number;
  offset?: number;
  limit?: number;
  has_more?: boolean;
  elapsed_ms: number;
  reason: string | null;
}

export type FileStatus = "M" | "A" | "D" | "R" | "C" | "T" | "unknown";

export interface SpcodeGitDiffFile {
  path: string;
  status: FileStatus;
  additions: number;
  deletions: number;
  /** On-demand patch (loaded via useSpcodeFileDiff when the file row is
   *  expanded). Null until fetched; binary files stay null forever. */
  slice: string | null;
  isBinary: boolean;
}

export interface SpcodeGitDiffMeta {
  directory: string | null;
  umo: string | null;
  loaded: boolean;
  /**
   * Server-echoed scope (only present when ``loaded=true``). Use this
   * to verify the requested ``scope`` reached the backend intact.
   */
  scope: GitDiffScope | null;
  /** Worktree etag echoed by the backend; the slice cache invalidates
   *  on change (worktree state moved → old patches are stale). */
  etag: string | null;
  reason: string | null;
  elapsedMs: number;
  fetchedAt: number;
}

export interface SpcodeGitDiffTotals {
  files: number;
  additions: number;
  deletions: number;
}

export interface SpcodeGitDiffSnapshot {
  meta: SpcodeGitDiffMeta;
  /** The currently loaded page(s) of files. `hasMore` reports whether
   *  the server holds more; totals cover the FULL list regardless. */
  files: SpcodeGitDiffFile[];
  /** Aggregates over the full (cross-page) list, server-computed. */
  totals: SpcodeGitDiffTotals;
  /** True when the server holds more pages than currently loaded. */
  hasMore: boolean;
  /** Offset the next page should request (== loaded file count). */
  nextOffset: number;
}

const VALID_STATUSES: ReadonlySet<FileStatus> = new Set([
  "M",
  "A",
  "D",
  "R",
  "C",
  "T",
]);

/** Normalize a raw git status code to the FileStatus union. */
function normalizeStatus(raw: string): FileStatus {
  const s = raw[0] as FileStatus;
  return VALID_STATUSES.has(s) ? s : "unknown";
}

export function parseSpcodeGitDiff(
  data: SpcodeGitDiffRawResponse,
): SpcodeGitDiffSnapshot {
  const files: SpcodeGitDiffFile[] = (
    Array.isArray(data.files_changed) ? data.files_changed : []
  ).map((f) => ({
    path: f.path,
    status: normalizeStatus(f.status),
    additions: f.additions ?? 0,
    deletions: f.deletions ?? 0,
    // Slices are on-demand now: no patch text ships with the list.
    slice: null,
    isBinary: f.is_binary === true,
  }));

  return {
    meta: {
      directory: data.directory,
      umo: data.umo,
      loaded: data.loaded,
      scope: normalizeScope(data.scope),
      etag: data.etag ?? null,
      reason: data.reason,
      elapsedMs: data.elapsed_ms,
      fetchedAt: Date.now(),
    },
    files,
    totals: {
      files: data.total_files ?? files.length,
      additions: data.total_additions ?? 0,
      deletions: data.total_deletions ?? 0,
    },
    hasMore: data.has_more === true,
    nextOffset: files.length,
  };
}

/**
 * Merge a newly fetched page into an existing snapshot (the "load more"
 * path). Pure: returns a new snapshot object; totals/meta are carried
 * over from the existing snapshot (the server recomputes them per page,
 * and they cover the full list — last page wins).
 */
export function appendSpcodeGitDiffPage(
  snapshot: SpcodeGitDiffSnapshot,
  page: SpcodeGitDiffSnapshot,
): SpcodeGitDiffSnapshot {
  return {
    meta: page.meta,
    files: [...snapshot.files, ...page.files],
    // Totals are cross-page aggregates; any page carries the same values.
    totals: page.totals,
    hasMore: page.hasMore,
    nextOffset: snapshot.nextOffset + page.files.length,
  };
}
