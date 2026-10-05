// Author: elecvoid243
// Date: 2026-10-06
// Spec: docs/superpowers/specs/2026-10-06-git-file-range-diff-frontend-design.md
//
// Pure parser for GET /spcode/git-file-diff. No Vue / no axios —
// importable in isolation (mirrors parseSpcodeGitShow.ts).

export type GitFileDiffStatus =
  | "added"
  | "deleted"
  | "modified"
  | "renamed"
  | "unchanged";

export interface GitFileDiffData {
  from: string;
  to: string;
  fromSha: string;
  toSha: string;
  path: string;
  status: GitFileDiffStatus;
  /** 仅 renamed 有值(from 侧旧路径) */
  oldPath: string | null;
  isBinary: boolean;
  /** status=added 时为 ""；isBinary 时为 "" */
  baseContent: string;
  /** 原始 blob 字节数(截断前) */
  baseSize: number;
  /** 基准 blob 超上限被截断(叠加对齐可能降级为纯 patch 视图) */
  baseTruncated: boolean;
  /** unified diff;binary 时为 null,unchanged 时为 "" */
  patch: string | null;
  /** binary 时为 null */
  additions: number | null;
  deletions: number | null;
  truncated: boolean;
}

const VALID_STATUSES: ReadonlySet<string> = new Set([
  "added",
  "deleted",
  "modified",
  "renamed",
  "unchanged",
]);

function asString(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

/**
 * Parse the /spcode/git-file-diff envelope. Returns null when the
 * envelope is not a successful, well-formed response (caller treats
 * null as "fetch failed"; the reason code lives on the composable
 * state, not here).
 */
export function parseSpcodeGitFileDiff(raw: unknown): GitFileDiffData | null {
  if (typeof raw !== "object" || raw === null) return null;
  const data = (raw as { data?: unknown }).data;
  if (typeof data !== "object" || data === null) return null;
  const d = data as Record<string, unknown>;
  if (d.success !== true) return null;

  const from = asString(d.from);
  const to = asString(d.to);
  const path = asString(d.path);
  const status = asString(d.status);
  if (!from || !to || !path || !status || !VALID_STATUSES.has(status)) {
    return null;
  }

  return {
    from,
    to,
    fromSha: asString(d.from_sha) ?? "",
    toSha: asString(d.to_sha) ?? "",
    path,
    status: status as GitFileDiffStatus,
    oldPath: asString(d.old_path),
    isBinary: d.is_binary === true,
    baseContent: asString(d.base_content) ?? "",
    baseSize: typeof d.base_size === "number" ? d.base_size : 0,
    baseTruncated: d.base_truncated === true,
    patch: asString(d.patch),
    additions: typeof d.additions === "number" ? d.additions : null,
    deletions: typeof d.deletions === "number" ? d.deletions : null,
    truncated: d.truncated === true,
  };
}
