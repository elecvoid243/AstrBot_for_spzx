// Author: elecvoid243, 2026-07-12
// Spec: docs/superpowers/specs/2026-07-11-document-manager-design.md §3.4
//
// Pure-TS localStorage helpers for the document manager:
//   1. the per-UMO docs root path (§3.4 below), and
//   2. the per-project quick-path chips ("快捷跳转", 2026-10-17).
// Both live here (instead of a second module) so preset paths are
// validated by the same `isValidDocsRoot` invariants and so the whole
// file stays import-free — `node --test` can load it directly, which
// it cannot do for a module that imports a sibling `.ts` file.
// No Vue imports — testable with node:test.
//
// The backend (POST/PATCH/DELETE /spcode/docs) does not persist
// docsRoot; this file is the only source of truth for the path the
// user sees in DocumentPathBar. localStorage is the storage layer;
// failures are swallowed (private mode / quota) and the caller
// falls back to the default.

export const DOCS_ROOT_STORAGE_KEY =
  "astrbot.spcode.documentManager.docsPathByUmo";
/** 2026-10-17 (elecvoid243): the default root is now the PROJECT
 *  root ("."), not "docs". Not every repository keeps its Markdown
 *  under `docs/`, and starting at the project root lets the user
 *  click into whichever folder actually exists instead of staring at
 *  an empty/erroring tree. The quick-path chips ("快捷跳转") are the
 *  opt-in shortcut for projects that DO have a docs folder. */
export const DEFAULT_DOCS_ROOT = ".";

/** Minimal subset of the Storage interface so tests can inject
 *  a fake without touching the real `localStorage` global. */
export interface StorageBackend {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

let storageBackend: StorageBackend | null = null;

/** Test-only seam: replace the localStorage shim. Pass `null` to
 *  restore the default (which uses `globalThis.localStorage` when
 *  available and a no-op stub otherwise). Production code MUST NOT
 *  call this — it's exported only so `tests/*.test.mjs` can plug
 *  in an in-memory map. */
export function __setStorageBackend(backend: StorageBackend | null): void {
  storageBackend = backend;
}

function defaultBackend(): StorageBackend {
  if (storageBackend) return storageBackend;
  // Defensive: in node:test without happy-dom, globalThis.localStorage
  // is undefined. Wrap in try/catch because real localStorage can
  // also throw (private browsing, disabled storage).
  return {
    getItem(key) {
      try {
        return globalThis.localStorage?.getItem(key) ?? null;
      } catch {
        return null;
      }
    },
    setItem(key, value) {
      try {
        globalThis.localStorage?.setItem(key, value);
      } catch {
        /* swallow */
      }
    },
    removeItem(key) {
      try {
        globalThis.localStorage?.removeItem(key);
      } catch {
        /* swallow */
      }
    },
  };
}

/** Path validator. Mirrors spec §3.4 + §3.6 invariants; the
 *  backend re-validates with the same rules server-side, so this
 *  is defense-in-depth only.
 *
 *  Accepts: the literal "." (means "the project root itself"),
 *  forward-slash subpaths ("docs", "docs/sub", "./docs" — the
 *  leading "./" is harmless and will be normalized away by
 *  callers), and ordinary hidden-directory names (".github",
 *  ".vscode") used as docs roots.
 *
 *  Rejects: empty, absolute (POSIX or Windows), drive-letter,
 *  UNC (\\server\share), parent traversal ("..", "a/..", "../foo"),
 *  leading slash or backslash, leading "~" (POSIX home expansion,
 *  which would escape the project entirely). */
export function isValidDocsRoot(p: string): boolean {
  if (!p) return false;
  if (p.length > 512) return false; // sanity bound, not a spec rule
  // Reject any `..` segment. Cheap: just look for the substring;
  // covers literal "..", "a/..", "../foo", and "a/../b". Fine
  // because we forbid backslashes and absolute paths upstream.
  if (p.includes("..")) return false;
  if (/^[a-zA-Z]:/.test(p)) return false; // Windows drive letter
  if (/^\\\\/.test(p)) return false; // UNC
  if (p.startsWith("/") || p.startsWith("\\")) return false;
  if (p.startsWith("~")) return false; // POSIX home expansion escapes the project
  return true;
}

/** True when the (already-coerced) docsRoot represents the
 *  project root itself, i.e. the user typed "." (or "./" before
 *  the trailing-slash strip). Callers use this to decide whether
 *  to resolve a docs-rooted path as `${root}/<docsRoot>` (normal
 *  case) or as just `${root}` (project root case). The empty
 *  case is NOT considered project root — `loadDocsRoot` already
 *  falls back to DEFAULT_DOCS_ROOT for empty stored values, and
 *  we don't want a typo'd empty input to silently widen scope
 *  to the entire project. */
export function isProjectRootDocs(p: string): boolean {
  return p === "." || p === "./";
}

/** Coerce user-typed input: trim, backslash → forward slash,
 *  strip trailing slash. Returns the cleaned string; returns ""
 *  if the result is empty. */
export function coerceDocsRoot(p: string): string {
  return p.trim().replace(/\\/g, "/").replace(/\/+$/, "");
}

/** Load the docs root for `umo`. Returns `DEFAULT_DOCS_ROOT` if
 *  the key is missing, the umo is not in the map, the stored
 *  value is empty, or the stored value is invalid (defensive). */
export function loadDocsRoot(umo: string): string {
  const backend = defaultBackend();
  let raw: string | null = null;
  try {
    raw = backend.getItem(DOCS_ROOT_STORAGE_KEY);
  } catch {
    return DEFAULT_DOCS_ROOT;
  }
  if (!raw) return DEFAULT_DOCS_ROOT;
  let map: Record<string, string> | null = null;
  try {
    map = JSON.parse(raw);
  } catch {
    return DEFAULT_DOCS_ROOT;
  }
  if (!map || typeof map !== "object") return DEFAULT_DOCS_ROOT;
  const v = map[umo];
  if (typeof v !== "string" || !v || !isValidDocsRoot(v)) {
    return DEFAULT_DOCS_ROOT;
  }
  return v;
}

export type SaveDocsRootResult =
  | { ok: true }
  | { ok: false; reason: "invalid_path" | "storage_unavailable" };

/** Persist `path` for `umo`. Coerces first; rejects if the
 *  coerced value is invalid. Returns `{ ok: false, reason }`
 *  on either validation or storage failure so the caller can
 *  surface an error to the user. */
export function saveDocsRoot(
  umo: string,
  path: string,
): SaveDocsRootResult {
  const cleaned = coerceDocsRoot(path);
  if (!isValidDocsRoot(cleaned)) {
    return { ok: false, reason: "invalid_path" };
  }
  const backend = defaultBackend();
  let existing: Record<string, string> = {};
  try {
    const raw = backend.getItem(DOCS_ROOT_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        existing = parsed as Record<string, string>;
      }
    }
  } catch {
    // Treat parse failure as an empty map; we'll overwrite below.
    existing = {};
  }
  existing[umo] = cleaned;
  try {
    backend.setItem(DOCS_ROOT_STORAGE_KEY, JSON.stringify(existing));
    return { ok: true };
  } catch {
    return { ok: false, reason: "storage_unavailable" };
  }
}

export const DOC_PRESETS_STORAGE_KEY =
  "astrbot.spcode.documentManager.docPresetsByProject";

/** Upper bound of chips rendered in the toolbar. The row wraps, so
 *  this is a UX bound rather than a hard technical limit. */
export const MAX_DOC_PRESETS = 12;

export type DocPresetKind = "dir" | "file";

/** One quick-path chip.
 *
 *  `path` is ALWAYS project-relative (POSIX separators, no leading
 *  "./", no trailing slash). `kind` decides what a click does:
 *  "dir" switches the docs root, "file" roots the manager at the
 *  file's parent directory and opens it. */
export interface DocPreset {
  label: string;
  kind: DocPresetKind;
  path: string;
}

/** Starting list for projects that never customized their chips.
 *  Deliberately mirrors the old hard-coded list so existing users
 *  see no change until they edit something. */
export const DEFAULT_DOC_PRESETS: readonly DocPreset[] = [
  { label: "AGENTS.md", kind: "file", path: "AGENTS.md" },
  { label: "README.md", kind: "file", path: "README.md" },
  { label: "specs", kind: "dir", path: "docs/superpowers/specs" },
  { label: "plans", kind: "dir", path: "docs/superpowers/plans" },
  { label: "changelogs", kind: "dir", path: "changelogs" },
];

/** Normalize a project root into a stable storage bucket key.
 *  Lowercased because Windows paths are case-insensitive and the
 *  same repo must not split into two buckets when the backend
 *  reports `F:/repo` one day and `f:\repo` the next. */
export function projectStorageKey(root: string | null | undefined): string {
  return (root ?? "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/\/+$/, "")
    .toLowerCase();
}

/** Clean + validate one raw preset (typically loaded from JSON or
 *  typed into the dialog). Returns `null` when the entry cannot be
 *  used, so callers can drop it (load) or surface an error (save). */
export function normalizePreset(raw: unknown): DocPreset | null {
  if (!raw || typeof raw !== "object") return null;
  const entry = raw as Partial<Record<keyof DocPreset, unknown>>;
  const kind =
    entry.kind === "file" ? "file" : entry.kind === "dir" ? "dir" : null;
  if (!kind) return null;
  const path = coerceDocsRoot(String(entry.path ?? "")).replace(/^\.\//, "");
  if (!isValidDocsRoot(path)) return null;
  const labelRaw = String(entry.label ?? "").trim();
  const label = (labelRaw || path.split("/").pop() || path).slice(0, 40);
  return { label, kind, path };
}

function sanitizePresets(raw: unknown): DocPreset[] {
  if (!Array.isArray(raw)) return [];
  const out: DocPreset[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    const preset = normalizePreset(item);
    if (!preset || seen.has(preset.path)) continue;
    seen.add(preset.path);
    out.push(preset);
    if (out.length >= MAX_DOC_PRESETS) break;
  }
  return out;
}

/** Load the chips for `projectKey`. Returns the defaults when the
 *  project has no stored entry yet (or the payload is corrupt).
 *  An explicitly stored EMPTY array is honored — the user is allowed
 *  to delete every chip. */
export function loadDocPresets(
  projectKey: string | null | undefined,
  backend: StorageBackend = defaultBackend(),
): DocPreset[] {
  const key = projectStorageKey(projectKey);
  let raw: string | null = null;
  try {
    raw = backend.getItem(DOC_PRESETS_STORAGE_KEY);
  } catch {
    return DEFAULT_DOC_PRESETS.map((p) => ({ ...p }));
  }
  if (!raw) return DEFAULT_DOC_PRESETS.map((p) => ({ ...p }));
  let map: Record<string, unknown> | null = null;
  try {
    map = JSON.parse(raw);
  } catch {
    return DEFAULT_DOC_PRESETS.map((p) => ({ ...p }));
  }
  if (!map || typeof map !== "object" || Array.isArray(map)) {
    return DEFAULT_DOC_PRESETS.map((p) => ({ ...p }));
  }
  const stored = map[key];
  if (!Array.isArray(stored)) return DEFAULT_DOC_PRESETS.map((p) => ({ ...p }));
  return sanitizePresets(stored);
}

export type SaveDocPresetsResult =
  | { ok: true; presets: DocPreset[] }
  | { ok: false; reason: "invalid_path" | "too_many" | "storage_unavailable" };

/** Persist `presets` for `projectKey`. Every entry is re-validated
 *  here so a caller bug can never write an escaping path into
 *  localStorage. */
export function saveDocPresets(
  projectKey: string | null | undefined,
  presets: readonly DocPreset[],
  backend: StorageBackend = defaultBackend(),
): SaveDocPresetsResult {
  if (presets.length > MAX_DOC_PRESETS) {
    return { ok: false, reason: "too_many" };
  }
  const cleaned: DocPreset[] = [];
  const seen = new Set<string>();
  for (const raw of presets) {
    const preset = normalizePreset(raw);
    if (!preset || seen.has(preset.path)) {
      return { ok: false, reason: "invalid_path" };
    }
    seen.add(preset.path);
    cleaned.push(preset);
  }
  const key = projectStorageKey(projectKey);
  let map: Record<string, unknown> = {};
  try {
    const raw = backend.getItem(DOC_PRESETS_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        map = parsed as Record<string, unknown>;
      }
    }
  } catch {
    // Treat unreadable storage as an empty map; the write below is
    // the operation that actually matters (and can still fail).
    map = {};
  }
  map[key] = cleaned;
  try {
    backend.setItem(DOC_PRESETS_STORAGE_KEY, JSON.stringify(map));
  } catch {
    return { ok: false, reason: "storage_unavailable" };
  }
  return { ok: true, presets: cleaned };
}
