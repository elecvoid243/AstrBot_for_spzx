// Author: elecvoid243
// Date: 2026-07-28
// Spec: docs/superpowers/specs/2026-07-28-git-diff-recursive-directory-tree-design.md §3.3
// Test runner: node --test (Node v24 strips TS from .ts imports automatically).
// Run: cd dashboard && pnpm exec node --test --import tsx tests/buildDirectorySections.test.mjs
//
// Covers the recursive buildDirectorySections pure function. Mirrors
// tests/parseSpcodeGitDiff.test.mjs style — import directly from
// the .ts source; no Vue / happy-dom needed.

import assert from "node:assert/strict";
import test from "node:test";

import {
  buildDirectorySections,
  walkSections,
} from "../src/composables/parseDirectorySections.ts";

/** Minimal SpcodeGitDiffFile fixture factory. */
function file(path, additions = 0, deletions = 0) {
  return {
    path,
    status: "M",
    additions,
    deletions,
    slice: null,
    isBinary: false,
  };
}

// 1. Empty list
test("buildDirectorySections: empty list → []", () => {
  const result = buildDirectorySections([], "", "unstaged");
  assert.deepEqual(result, []);
});

// 2. Single file with no '/' → <root> section
test("buildDirectorySections: single root file → <root> section", () => {
  const result = buildDirectorySections(
    [file("README.md", 1, 0)],
    "",
    "unstaged",
  );
  assert.equal(result.length, 1);
  assert.equal(result[0].label, "<root>");
  assert.equal(result[0].fullPath, "");
  assert.equal(result[0].id, "dir:");
  assert.equal(result[0].fileCount, 1);
  assert.equal(result[0].additions, 1);
  assert.equal(result[0].deletions, 0);
  assert.equal(result[0].files.length, 1);
  assert.equal(result[0].files[0].path, "README.md");
  assert.equal(result[0].children.length, 0);
});

// 3. Multiple files in same top-level dir
test("buildDirectorySections: multiple files in same top dir", () => {
  const result = buildDirectorySections(
    [file("a/x.py"), file("a/y.py")],
    "",
    "unstaged",
  );
  assert.equal(result.length, 1);
  assert.equal(result[0].label, "a");
  assert.equal(result[0].fullPath, "a");
  assert.equal(result[0].id, "dir:a");
  assert.equal(result[0].fileCount, 2);
  assert.equal(result[0].files.length, 2);
  assert.equal(result[0].children.length, 0);
});

// 4. Multi-level nesting
test("buildDirectorySections: multi-level nesting", () => {
  // Path "a/b/c.py" means: c.py lives in directory a/b/. The tree
  // has 2 sections: a → a/b (with file c.py).
  const result = buildDirectorySections(
    [file("a/b/c.py")],
    "",
    "unstaged",
  );
  assert.equal(result.length, 1);
  assert.equal(result[0].label, "a");
  assert.equal(result[0].fileCount, 0);
  assert.equal(result[0].files.length, 0);
  assert.equal(result[0].children.length, 1);
  const b = result[0].children[0];
  assert.equal(b.label, "b");
  assert.equal(b.fullPath, "a/b");
  assert.equal(b.id, "dir:a/b");
  assert.equal(b.fileCount, 1);
  assert.equal(b.files[0].path, "a/b/c.py");
  assert.equal(b.children.length, 0);
});

// 5. Mixed: top-level file + nested file
test("buildDirectorySections: mixed top + nested", () => {
  const result = buildDirectorySections(
    [file("a/x.py", 1, 0), file("a/b/y.py", 2, 1)],
    "",
    "unstaged",
  );
  assert.equal(result.length, 1);
  const a = result[0];
  assert.equal(a.fileCount, 1);
  assert.equal(a.files[0].path, "a/x.py");
  assert.equal(a.additions, 1);
  assert.equal(a.children.length, 1);
  const b = a.children[0];
  assert.equal(b.fileCount, 1);
  assert.equal(b.files[0].path, "a/b/y.py");
  assert.equal(b.additions, 2);
});

// 6. Deep nesting chain
test("buildDirectorySections: deep nesting chain", () => {
  // Path "a/b/c/d/e.txt" means: e.txt lives in directory a/b/c/d/.
  // The tree has 4 sections: a → a/b → a/b/c → a/b/c/d (with file e.txt).
  const result = buildDirectorySections(
    [file("a/b/c/d/e.txt")],
    "",
    "unstaged",
  );
  let cur = result[0];
  const labels = [cur.label];
  for (;;) {
    if (cur.children.length === 0) break;
    cur = cur.children[0];
    labels.push(cur.label);
  }
  assert.deepEqual(labels, ["a", "b", "c", "d"]);
  assert.equal(cur.fileCount, 1);
  assert.equal(cur.files[0].path, "a/b/c/d/e.txt");
});

// 7. <root> identifier is set for repo-root files
test("buildDirectorySections: <root> label for repo-root files", () => {
  const result = buildDirectorySections(
    [file("README.md"), file("LICENSE")],
    "",
    "unstaged",
  );
  // Both files in <root> section
  assert.equal(result.length, 1);
  assert.equal(result[0].label, "<root>");
  assert.equal(result[0].files.length, 2);
});

// 8. Idempotence (id stability)
test("buildDirectorySections: same input → same ids", () => {
  const list = [file("a/x.py"), file("b/y.py"), file("a/b/z.py")];
  const run1 = buildDirectorySections(list, "", "unstaged");
  const run2 = buildDirectorySections(list, "", "unstaged");
  function collectIds(sections) {
    const ids = [];
    walkSections(sections, (s) => ids.push(s.id));
    return ids;
  }
  assert.deepEqual(collectIds(run1), collectIds(run2));
});

// 9. fileCount only counts direct files (not children)
test("buildDirectorySections: fileCount is direct-only", () => {
  const result = buildDirectorySections(
    [file("a/x.py"), file("a/b/y.py"), file("a/b/c/z.py")],
    "",
    "unstaged",
  );
  const a = result[0];
  assert.equal(a.fileCount, 1); // only x.py
  assert.equal(a.children[0].fileCount, 1); // only y.py
  assert.equal(a.children[0].children[0].fileCount, 1); // only z.py
});

// 10. additions/deletions only count direct files
test("buildDirectorySections: additions/deletions are direct-only", () => {
  const result = buildDirectorySections(
    [file("a/x.py", 5, 0), file("a/b/y.py", 10, 3)],
    "",
    "unstaged",
  );
  const a = result[0];
  assert.equal(a.additions, 5);
  assert.equal(a.deletions, 0);
  const b = a.children[0];
  assert.equal(b.additions, 10);
  assert.equal(b.deletions, 3);
});

// 11. Bucket order preserved
test("buildDirectorySections: bucket order preserved", () => {
  const result = buildDirectorySections(
    [file("a/x.py"), file("b/y.py"), file("a/z.py")],
    "",
    "unstaged",
  );
  // a appears before b
  assert.equal(result[0].label, "a");
  assert.equal(result[1].label, "b");
});

// 12. scope → badgeKind mapping
test("buildDirectorySections: scope drives badgeKind", () => {
  const r1 = buildDirectorySections([file("a.py")], "", "staged");
  assert.equal(r1[0].badgeKind, "staged");
  const r2 = buildDirectorySections([file("a.py")], "", "unstaged");
  assert.equal(r2[0].badgeKind, "unstaged");
  const r3 = buildDirectorySections([file("a.py")], "", "all");
  assert.equal(r3[0].badgeKind, "neutral");
});

// 13. No infinite recursion on deep trees
test("buildDirectorySections: terminates on deep nesting", () => {
  // 10 levels of nesting: lvl0/lvl1/.../lvl9/file.txt means file.txt
  // lives in lvl0/lvl1/.../lvl9/. The tree has 10 sections, the
  // last one (lvl9) holds the file.
  const path = Array.from({ length: 10 }, (_, i) => `lvl${i}`).join("/") + "/file.txt";
  const result = buildDirectorySections([file(path)], "", "unstaged");
  let cur = result[0];
  for (let i = 0; i < 9; i++) {
    assert.equal(cur.children.length, 1, `level ${i} should have 1 child`);
    cur = cur.children[0];
  }
  assert.equal(cur.fileCount, 1);
  assert.equal(cur.children.length, 0);
});

// 14. walkSections visits all nodes (DFS pre-order)
test("walkSections: visits all nodes depth-first", () => {
  const result = buildDirectorySections(
    [file("a/x.py"), file("a/b/y.py"), file("c/z.py")],
    "",
    "unstaged",
  );
  const labels = [];
  walkSections(result, (s) => labels.push(s.label));
  assert.deepEqual(labels, ["a", "b", "c"]);
});

// 15. parentPath threading
test("buildDirectorySections: parentPath threaded into child id", () => {
  // Simulate a recursion call by passing a non-empty parentPath
  const result = buildDirectorySections(
    [file("inner.py")],
    "outer",
    "unstaged",
  );
  assert.equal(result[0].fullPath, "outer");
  assert.equal(result[0].id, "dir:outer");
});

// ── 2026-10-25 compact-directory-chains ──────────────────────────────

import {
  buildDirectoryChain,
  isDirectoryChainLink,
} from "../src/composables/parseDirectorySections.ts";

// 16. a single-child directory run is a chain; its crumb covers head
//     through terminal
test("buildDirectoryChain: single-child run collapses to one crumb", () => {
  const tree = buildDirectorySections(
    [file("a/b/c/deep.py")],
    "",
    "unstaged",
  );
  // a → b → c → deep.py: a and b are links, c is the terminal (holds file)
  assert.equal(tree.length, 1);
  const chain = buildDirectoryChain(tree[0]);
  assert.ok(chain, "a should be a chain head");
  assert.deepEqual(chain.segments, ["a", "b", "c"]);
  assert.deepEqual(chain.linkIds, ["dir:a", "dir:a/b"]);
  assert.equal(chain.terminal.label, "c");
  assert.equal(chain.terminal.fileCount, 1);
});

// 17. a branch node (multiple children) is not a chain
test("buildDirectoryChain: branch node returns null", () => {
  const tree = buildDirectorySections(
    [file("a/x.py"), file("a/b/y.py")],
    "",
    "unstaged",
  );
  assert.equal(buildDirectoryChain(tree[0]), null);
  assert.equal(isDirectoryChainLink(tree[0]), false);
});

// 18. a directory holding direct files is not a link even with one child
test("buildDirectoryChain: dir with file + single subdir is not a link", () => {
  const tree = buildDirectorySections(
    [file("a/x.py"), file("a/b/y.py")],
    "",
    "unstaged",
  );
  const a = tree[0];
  // a has file x + dir b → not a link; b (one file, no children) → not a
  // link either (it holds files, chain ends there).
  assert.equal(buildDirectoryChain(a), null);
  assert.equal(buildDirectoryChain(a.children[0]), null);
});

// 19. chain ends at the first file-holding level
test("buildDirectoryChain: chain stops at terminal that holds files", () => {
  const tree = buildDirectorySections(
    [file("a/b/x.py"), file("a/b/y.py")],
    "",
    "unstaged",
  );
  // a is a link (only child b); b holds 2 files → terminal
  const chain = buildDirectoryChain(tree[0]);
  assert.ok(chain);
  assert.deepEqual(chain.segments, ["a", "b"]);
  assert.deepEqual(chain.linkIds, ["dir:a"]);
  assert.equal(chain.terminal.fileCount, 2);
});
