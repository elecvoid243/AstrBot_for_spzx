// Author: elecvoid243, 2026-10-17
// Tests for the per-project quick-path ("快捷跳转") storage helpers
// that live in docsRootStorage.ts alongside the docs-root helpers.
// Run with: node --test tests/docPresetStorage.test.mjs

import assert from "node:assert/strict";
import test from "node:test";
import {
  DEFAULT_DOC_PRESETS,
  DOC_PRESETS_STORAGE_KEY,
  MAX_DOC_PRESETS,
  loadDocPresets,
  normalizePreset,
  projectStorageKey,
  saveDocPresets,
} from "../src/composables/docsRootStorage.ts";

function makeBackend(initial = {}) {
  const store = { ...initial };
  return {
    store,
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => {
      store[k] = String(v);
    },
    removeItem: (k) => {
      delete store[k];
    },
  };
}

test("projectStorageKey: normalizes separators, trailing slash and case", () => {
  assert.equal(projectStorageKey("F:\\repo\\AstrBot\\"), "f:/repo/astrbot");
  assert.equal(projectStorageKey("f:/repo/astrbot"), "f:/repo/astrbot");
  assert.equal(projectStorageKey(""), "");
  assert.equal(projectStorageKey(null), "");
});

test("loadDocPresets: returns defaults for an unknown project", () => {
  const backend = makeBackend();
  assert.deepEqual(loadDocPresets("f:/repo/a", backend), [
    ...DEFAULT_DOC_PRESETS,
  ]);
});

test("loadDocPresets: keys are isolated per project", () => {
  const backend = makeBackend({
    [DOC_PRESETS_STORAGE_KEY]: JSON.stringify({
      "f:/repo/a": [{ label: "Docs", kind: "dir", path: "docs" }],
      "f:/repo/b": [{ label: "Notes", kind: "dir", path: "notes" }],
    }),
  });
  assert.deepEqual(loadDocPresets("F:\\repo\\A", backend), [
    { label: "Docs", kind: "dir", path: "docs" },
  ]);
  assert.deepEqual(loadDocPresets("f:/repo/b/", backend), [
    { label: "Notes", kind: "dir", path: "notes" },
  ]);
  assert.deepEqual(loadDocPresets("f:/repo/c", backend), [
    ...DEFAULT_DOC_PRESETS,
  ]);
});

test("loadDocPresets: honors an explicitly empty stored list", () => {
  const backend = makeBackend({
    [DOC_PRESETS_STORAGE_KEY]: JSON.stringify({ "f:/repo/a": [] }),
  });
  assert.deepEqual(loadDocPresets("f:/repo/a", backend), []);
});

test("loadDocPresets: drops invalid entries and duplicates", () => {
  const backend = makeBackend({
    [DOC_PRESETS_STORAGE_KEY]: JSON.stringify({
      "f:/repo/a": [
        { label: "ok", kind: "dir", path: "docs" },
        { label: "dup", kind: "file", path: "docs" },
        { label: "escape", kind: "dir", path: "../outside" },
        { label: "absolute", kind: "dir", path: "/etc" },
        { label: "badkind", kind: "symlink", path: "src" },
      ],
    }),
  });
  assert.deepEqual(loadDocPresets("f:/repo/a", backend), [
    { label: "ok", kind: "dir", path: "docs" },
  ]);
});

test("loadDocPresets: corrupt JSON falls back to defaults", () => {
  const backend = makeBackend({ [DOC_PRESETS_STORAGE_KEY]: "{not json" });
  assert.deepEqual(loadDocPresets("f:/repo/a", backend), [
    ...DEFAULT_DOC_PRESETS,
  ]);
});

test("loadDocPresets: non-array entry falls back to defaults", () => {
  const backend = makeBackend({
    [DOC_PRESETS_STORAGE_KEY]: JSON.stringify({ "f:/repo/a": "docs" }),
  });
  assert.deepEqual(loadDocPresets("f:/repo/a", backend), [
    ...DEFAULT_DOC_PRESETS,
  ]);
});

test("normalizePreset: trims, strips ./ and derives a label", () => {
  assert.deepEqual(
    normalizePreset({ label: "  ", kind: "dir", path: " ./docs/plans/ " }),
    { label: "plans", kind: "dir", path: "docs/plans" },
  );
  // Project-root sentinel stays meaningful as a dir preset.
  assert.deepEqual(normalizePreset({ kind: "dir", path: "." }), {
    label: ".",
    kind: "dir",
    path: ".",
  });
});

test("normalizePreset: rejects escaping / absolute / unknown-kind input", () => {
  assert.equal(normalizePreset({ kind: "dir", path: "../x" }), null);
  assert.equal(normalizePreset({ kind: "dir", path: "/etc" }), null);
  assert.equal(normalizePreset({ kind: "dir", path: "C:/x" }), null);
  assert.equal(normalizePreset({ kind: "symlink", path: "docs" }), null);
  assert.equal(normalizePreset(null), null);
});

test("saveDocPresets: writes under the normalized project key", () => {
  const backend = makeBackend();
  const presets = [{ label: "Docs", kind: "dir", path: "docs" }];
  const result = saveDocPresets("F:\\repo\\A\\", presets, backend);
  assert.equal(result.ok, true);
  const map = JSON.parse(backend.store[DOC_PRESETS_STORAGE_KEY]);
  assert.deepEqual(map["f:/repo/a"], presets);
});

test("saveDocPresets: keeps other projects' entries intact", () => {
  const backend = makeBackend({
    [DOC_PRESETS_STORAGE_KEY]: JSON.stringify({
      "f:/repo/b": [{ label: "Notes", kind: "dir", path: "notes" }],
    }),
  });
  saveDocPresets(
    "f:/repo/a",
    [{ label: "Docs", kind: "dir", path: "docs" }],
    backend,
  );
  const map = JSON.parse(backend.store[DOC_PRESETS_STORAGE_KEY]);
  assert.deepEqual(map["f:/repo/b"], [
    { label: "Notes", kind: "dir", path: "notes" },
  ]);
  assert.deepEqual(map["f:/repo/a"], [
    { label: "Docs", kind: "dir", path: "docs" },
  ]);
});

test("saveDocPresets: rejects invalid / duplicate paths without writing", () => {
  const backend = makeBackend();
  const invalid = saveDocPresets(
    "f:/repo/a",
    [{ label: "x", kind: "dir", path: "../up" }],
    backend,
  );
  assert.deepEqual(invalid, { ok: false, reason: "invalid_path" });

  const duplicate = saveDocPresets(
    "f:/repo/a",
    [
      { label: "a", kind: "dir", path: "docs" },
      { label: "b", kind: "file", path: "docs" },
    ],
    backend,
  );
  assert.deepEqual(duplicate, { ok: false, reason: "invalid_path" });
  assert.equal(backend.store[DOC_PRESETS_STORAGE_KEY], undefined);
});

test("saveDocPresets: enforces MAX_DOC_PRESETS", () => {
  const backend = makeBackend();
  const many = Array.from({ length: MAX_DOC_PRESETS + 1 }, (_, i) => ({
    label: `p${i}`,
    kind: "dir",
    path: `dir${i}`,
  }));
  assert.deepEqual(saveDocPresets("f:/repo/a", many, backend), {
    ok: false,
    reason: "too_many",
  });
});

test("saveDocPresets: reports storage_unavailable when setItem throws", () => {
  const backend = {
    getItem: () => null,
    setItem: () => {
      throw new Error("quota");
    },
    removeItem: () => {},
  };
  assert.deepEqual(
    saveDocPresets(
      "f:/repo/a",
      [{ label: "d", kind: "dir", path: "docs" }],
      backend,
    ),
    { ok: false, reason: "storage_unavailable" },
  );
});
