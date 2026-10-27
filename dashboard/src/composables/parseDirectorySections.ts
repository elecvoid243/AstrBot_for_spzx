// Author: elecvoid243
// Date: 2026-07-28
// Spec: docs/superpowers/specs/2026-07-28-git-diff-recursive-directory-tree-design.md §3.3
//
// Recursive directory-section builder for the GitDiff sidebar's file
// list. The previous (2026-06-28) implementation grouped by top-level
// directory only, so a new tracked directory like `new_dir/` showed up
// as a single bucket even when it contained many nested files. After
// the spcode v2.21.1 git-status endpoint started returning recursive
// untracked paths (see webapi-git-status-api.md §6), the UI had no
// matching way to render the tree — this module fills that gap by
// folding each file into one section per ancestor directory.
//
// 2026-07-28 (rev): switched from a strip-as-you-recurse bucket
// pass to a fold-over-full-paths pass. The earlier design mutated
// `file.path` (stripped the ancestor prefix) so selection sets,
// stage/unstage/restore calls, and new-file lookups all received
// truncated paths the backend could not resolve. The fold keeps
// `file.path` verbatim (full repo-relative POSIX path) and records
// section membership by directory prefix instead.
//
// Pure function (no Vue, no axios) so it is unit-testable under
// `node --test` (see tests/buildDirectorySections.test.mjs).

import type { SpcodeGitDiffFile, GitDiffScope } from "./parseSpcodeGitDiff";

/**
 * A node in the directory tree rendered by `GitDiffBodyContent`.
 * Each section has either direct files (no further `/` in the path)
 * or child sections (sub-directories), or both.
 */
export interface DiffSection {
  /** Stable id derived from fullPath; used as `collapsedGroups` Set key. */
  id: string;
  /** Header label. Equals the last path segment, or "<root>" for repo-root. */
  label: string;
  /** Repo-relative POSIX path. Empty string for the synthetic <root> section. */
  fullPath: string;
  /** Number of direct files in this section (excludes children). */
  fileCount: number;
  /** Direct-file-only additions. */
  additions: number;
  /** Direct-file-only deletions. */
  deletions: number;
  /** Files that directly live in this section. Paths are verbatim
   *  repo-relative (NOT stripped) so selection / stage / restore /
   *  new-file lookups can round-trip to the backend unchanged. */
  files: SpcodeGitDiffFile[];
  /** Sub-directory sections, recursively the same shape. */
  children: DiffSection[];
  /** Visual badge kind, drives section header dot color. */
  badgeKind: "staged" | "unstaged" | "neutral";
}

/** Visual badge kind derived from the active diff scope. */
function scopeBadge(scope: GitDiffScope): DiffSection["badgeKind"] {
  if (scope === "staged") return "staged";
  if (scope === "all") return "neutral";
  return "unstaged";
}

/**
 * Group a flat file list into a tree of `DiffSection` by folding
 * each file into one section per ancestor directory. Bucket
 * first-seen order is preserved (= the order files appear in
 * `list`), matching the previous flat-section behavior so the user
 * sees the same diff sequence.
 *
 * Args:
 *   list:       flat list of files in any scope.
 *   parentPath: accumulated ancestor path; "" means the repo root.
 *               When non-empty, every input file is treated as
 *               living under parentPath (the prefix is prepended
 *               before folding) and the single parentPath section
 *               is returned.
 *   scope:      active diff scope, drives the per-section badge.
 *
 * Returns:
 *   DiffSection[] of (possibly empty) sections. Empty list → [].
 *
 * Idempotent: same input → same output, including ids. No exceptions
 * are thrown on any well-formed input.
 */
export function buildDirectorySections(
  list: SpcodeGitDiffFile[],
  parentPath: string,
  scope: GitDiffScope,
): DiffSection[] {
  if (list.length === 0) return [];

  // When a non-empty parentPath is supplied, re-anchor every file
  // under it so the fold below produces the expected single
  // section at parentPath.
  const anchored =
    parentPath === ""
      ? list
      : list.map((f) => ({ ...f, path: `${parentPath}/${f.path}` }));

  // filesByDir: full dir path → files directly inside it.
  // childDirsByDir: full dir path → set of direct child dir paths.
  // dirOrder: first-seen order of directories (drives section order).
  const filesByDir = new Map<string, SpcodeGitDiffFile[]>();
  const childDirsByDir = new Map<string, Set<string>>();
  const dirOrder: string[] = [];
  const ensureDir = (dir: string): void => {
    if (!filesByDir.has(dir)) {
      filesByDir.set(dir, []);
      dirOrder.push(dir);
    }
  };
  for (const f of anchored) {
    const dir = f.path.includes("/")
      ? f.path.slice(0, f.path.lastIndexOf("/"))
      : "";
    ensureDir(dir);
    filesByDir.get(dir)!.push(f);
    if (!dir) continue;
    // Register every ancestor dir and its direct child.
    const segs = dir.split("/");
    for (let i = 0; i < segs.length; i++) {
      const anc = segs.slice(0, i).join("/");
      const child = segs.slice(0, i + 1).join("/");
      ensureDir(anc);
      let children = childDirsByDir.get(anc);
      if (!children) {
        children = new Set();
        childDirsByDir.set(anc, children);
      }
      children.add(child);
    }
  }

  // Materialise sections in first-seen order.
  const sectionsByDir = new Map<string, DiffSection>();
  for (const dir of dirOrder) {
    const files = filesByDir.get(dir)!;
    let additions = 0;
    let deletions = 0;
    for (const f of files) {
      additions += f.additions;
      deletions += f.deletions;
    }
    sectionsByDir.set(dir, {
      id: `dir:${dir}`,
      label: dir === "" ? "<root>" : dir.slice(dir.lastIndexOf("/") + 1),
      fullPath: dir,
      fileCount: files.length,
      additions,
      deletions,
      files,
      children: [],
      badgeKind: scopeBadge(scope),
    });
  }
  // Attach children (order within a parent follows first-seen dir
  // order, matching the previous flat behavior). The synthetic
  // <root> section (dir === "") is NOT a tree parent: it is a flat
  // bucket of repo-root files, so top-level directories stay its
  // siblings rather than being duplicated underneath it.
  for (const dir of dirOrder) {
    if (dir === "") continue;
    const childSet = childDirsByDir.get(dir);
    if (!childSet) continue;
    const parent = sectionsByDir.get(dir)!;
    for (const child of childSet) {
      parent.children.push(sectionsByDir.get(child)!);
    }
  }

  // Re-anchored recursion: return just the parentPath section.
  if (parentPath !== "") {
    const sec = sectionsByDir.get(parentPath);
    return sec ? [sec] : [];
  }

  // Top-level sections: the synthetic <root> (only when it has
  // direct files) plus every dir with no '/' in its path. Dirs
  // that are only ancestors (no direct files) still appear here —
  // they are the real top-level directories the user expects.
  const top: DiffSection[] = [];
  for (const dir of dirOrder) {
    if (dir === "") {
      if (filesByDir.get(dir)!.length > 0) {
        top.push(sectionsByDir.get(dir)!);
      }
      continue;
    }
    if (!dir.includes("/")) {
      top.push(sectionsByDir.get(dir)!);
    }
  }
  return top;
}

/**
 * Walk every section in the tree in DFS pre-order, calling `fn` on
 * each. Used by the parent (GitDiffBodyContent) to accumulate the
 * top-bar summary, toggle collapse-all, and prune selection. Pure
 * helper, exposed so the test file can introspect id ordering.
 */
export function walkSections(
  sections: DiffSection[],
  fn: (s: DiffSection) => void,
): void {
  for (const s of sections) {
    fn(s);
    if (s.children.length > 0) walkSections(s.children, fn);
  }
}

/**
 * A run of directories that each contain exactly one sub-directory and
 * no direct files. Such runs carry no information per level — one
 * near-empty header row per level — so the tree renders them as a
 * single breadcrumb row (VS Code "compact folders").
 */
export interface DiffDirectoryChain {
  /** Labels from the chain head through the terminal, in order. The
   *  terminal's label is included: it names the level that finally
   *  branches (or holds files). */
  segments: string[];
  /** Ids of every chain LINK section (head included). Keys for the
   *  expandedChains set; the terminal is not a link and never keyed. */
  linkIds: string[];
  /** The first section that breaks the chain (has direct files, or
   *  more than one child). Owns the aggregated subtree; rendered as
   *  the deepest real level once the chain is expanded. */
  terminal: DiffSection;
}

/** True when the section neither holds files nor branches: exactly one
 *  sub-directory, nothing else. Such a section is pure "path filler". */
export function isDirectoryChainLink(section: DiffSection): boolean {
  return section.files.length === 0 && section.children.length === 1;
}

/**
 * Build the breadcrumb chain for a chain-link section, walking down
 * while each descendant is also a link. Returns null when the section
 * is not a chain link (branch node, file holder, or empty).
 *
 * Args:
 *   section: The candidate chain head.
 *
 * Returns:
 *   The chain descriptor (segments include the terminal's label), or
 *   null when the section is not a chain link.
 */
export function buildDirectoryChain(
  section: DiffSection,
): DiffDirectoryChain | null {
  if (!isDirectoryChainLink(section)) return null;
  const segments: string[] = [section.label];
  const linkIds: string[] = [section.id];
  let node = section.children[0];
  while (isDirectoryChainLink(node)) {
    segments.push(node.label);
    linkIds.push(node.id);
    node = node.children[0];
  }
  segments.push(node.label);
  return { segments, linkIds, terminal: node };
}
