// Author: elecvoid243, 2026-07-28
// Spec: docs/superpowers/specs/2026-07-28-git-diff-recursive-directory-tree-design.md §3.5
//
// Component tests for the recursive directory tree node. Uses
// Vitest + happy-dom (matches GitIgnoreEditor.spec.ts style).
// GitDiffFileItem and the i18n composable are stubbed because the
// contract under test is the section header / body / recursion
// behaviour, not the leaf row rendering.

import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { defineComponent, h, provide } from "vue";

// i18n mock: returns the key + k=v params (same shape as
// GitIgnoreEditor.spec.ts).
const tmMock = vi.fn(
  (key: string, params?: Record<string, string | number>) => {
    if (!params) return key;
    return Object.entries(params).reduce(
      (acc, [k, v]) => `${acc} ${k}=${String(v)}`,
      key,
    );
  },
);
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: tmMock, getRaw: vi.fn() }),
}));

// GitDiffFileItem stub: real <div> with a `data-path` attr so the
// test can assert which file rows rendered. Emits the same events
// as the real component (we don't validate leaf rendering here).
vi.mock("./GitDiffFileItem.vue", async () => {
  const { defineComponent, h } = await import("vue");
  const Stub = defineComponent({
    name: "GitDiffFileItem",
    props: ["file"],
    emits: [
      "toggle",
      "stage",
      "unstage",
      "restore",
      "open-file",
      "select",
      "discard-hunk",
    ],
    setup(props, { emit }) {
      return () =>
        h(
          "div",
          {
            "data-path": props.file.path,
            "data-status": props.file.status,
            class: "git-diff-file-item",
            onClick: () => emit("toggle", props.file.path),
          },
          props.file.path,
        );
    },
  });
  return { default: Stub };
});

import DiffDirectoryNode from "./DiffDirectoryNode.vue";
import { buildDirectorySections, type DiffSection } from "@/composables/parseDirectorySections";
import type { SpcodeGitDiffFile } from "@/composables/parseSpcodeGitDiff";

function file(path: string, additions = 0, deletions = 0): SpcodeGitDiffFile {
  return { path, status: "M", additions, deletions, slice: null, isBinary: false };
}

function mountNode(
  section: ReturnType<typeof buildDirectorySections>[number],
  depth = 0,
  extraProps: Record<string, unknown> = {},
) {
  return mount(DiffDirectoryNode, {
    props: {
      section,
      depth,
      expanded: new Set<string>(),
      collapsedGroups: new Set<string>(),
      isDark: false,
      showStage: false,
      showUnstage: false,
      showRestore: false,
      newFilePaths: new Set<string>(),
      isSelected: (_p: string) => false,
      isStagingForPath: (_p: string) => false,
      isUnstagingForPath: (_p: string) => false,
      isDiscardableFor: (_f: unknown) => false,
      isSectionFullySelected: (_s: unknown) => false,
      isSectionPartiallySelected: (_s: unknown) => false,
      selectableAriaLabel: (p: string) => `Select ${p}`,
      onToggleGroup: vi.fn(),
      onToggleSelect: vi.fn(),
      onToggleGroupSelect: vi.fn(),
      onFileToggle: vi.fn(),
      onFileStage: vi.fn(),
      onFileUnstage: vi.fn(),
      onFileRestore: vi.fn(),
      onFileOpen: vi.fn(),
      onFileDiscardHunk: vi.fn(),
      ...extraProps,
    },
  });
}

describe("DiffDirectoryNode", () => {
  // 1. Renders top-level section (depth=0) with no indentation
  it("top-level section renders with no padding", () => {
    const sec = buildDirectorySections([file("a/x.py")], "", "unstaged")[0];
    const w = mountNode(sec, 0);
    const root = w.find(".git-diff-section");
    expect(root.exists()).toBe(true);
    // depth 0 → padding-left should be 0px
    expect(root.attributes("style")).toMatch(/--depth:\s*0/);
  });

  // 2. Renders nested section (depth=1) with indent
  it("nested section renders with depth=1 in style", () => {
    const list = [file("a/b/y.py")];
    const sec = buildDirectorySections(list, "", "unstaged")[0];
    // a → b (with y.py). b is a child section.
    const w = mountNode(sec, 0);
    // Find the inner nested section
    const nested = w.findAll(".git-diff-section").at(1);
    expect(nested).toBeDefined();
    expect(nested!.attributes("style")).toMatch(/--depth:\s*1/);
  });

  // 3. Collapsed parent → child not rendered
  it("collapsed parent hides children", () => {
    // a holds a direct file AND a subdir → not a chain link, so the
    // ordinary collapsedGroups collapse applies.
    const list = [file("a/x.py"), file("a/b/y.py")];
    const sec = buildDirectorySections(list, "", "unstaged")[0];
    const w = mountNode(sec, 0, {
      collapsedGroups: new Set(["dir:a"]),
    });
    // v-show, not v-if, so element exists but display:none
    const body = w.find(".git-diff-section.is-collapsed .git-diff-section-body");
    expect(body.exists()).toBe(true);
    expect(body.attributes("style")).toMatch(/display:\s*none/);
  });

  // 4. Expanded parent → child rendered
  it("expanded parent renders children", () => {
    const list = [file("a/b/y.py")];
    const sec = buildDirectorySections(list, "", "unstaged")[0];
    // a is a single-child chain head: reveal its levels to render the
    // nested section body visibly.
    const w = mountNode(sec, 0, {
      expandedChains: new Set(["dir:a"]),
    });
    const bodies = w.findAll(".git-diff-section-body");
    // 2 sections → 2 bodies
    expect(bodies.length).toBe(2);
    bodies.forEach((b) => {
      // The non-collapsed body should NOT have display:none. happy-dom
      // may return "" or omit the style attribute entirely; both are
      // acceptable signals of "not display:none".
      const style = b.attributes("style") ?? "";
      expect(style).not.toMatch(/display:\s*none/);
    });
  });

  // 5. Section checkbox three-state
  it("section checkbox reflects fully-selected state", () => {
    const sec = buildDirectorySections([file("a/x.py")], "", "unstaged")[0];
    const w = mountNode(sec, 0, {
      isSectionFullySelected: (_s: unknown) => true,
      isSectionPartiallySelected: (_s: unknown) => false,
      showStage: true, // gate visibility
    });
    const cb = w.find(".git-diff-section-check input");
    expect(cb.exists()).toBe(true);
    // Vue renders the boolean `checked` prop as the DOM property;
    // .element.checked is the reliable way to read it.
    expect((cb.element as HTMLInputElement).checked).toBe(true);
  });

  it("section checkbox reflects indeterminate state", () => {
    const sec = buildDirectorySections([file("a/x.py")], "", "unstaged")[0];
    const w = mountNode(sec, 0, {
      isSectionFullySelected: (_s: unknown) => false,
      isSectionPartiallySelected: (_s: unknown) => true,
      showStage: true,
    });
    const cb = w.find(".git-diff-section-check input");
    expect(cb.exists()).toBe(true);
    // .element.indeterminate is set via the .prop binding.
    expect((cb.element as HTMLInputElement).indeterminate).toBe(true);
  });

  // 6. aria-label includes ancestor path
  it("section checkbox aria-label includes parent and name", () => {
    const list = [file("a/b/y.py")];
    const sec = buildDirectorySections(list, "", "unstaged")[0];
    // a → b (with y.py). The inner section is `b`. Mount with
    // depth=1 and a parent label of "a" so the test exercises the
    // inject fallback (since we don't mount the outer `a` section).
    const inner = sec.children[0];
    // Use a parent wrapper to provide the parent label via inject.
    const ParentHarness = defineComponent({
      setup() {
        provide("spcode:diffDirectoryNodeParentLabel", "a");
        return () => h(DiffDirectoryNode, {
          section: inner,
          depth: 1,
          expanded: new Set<string>(),
          collapsedGroups: new Set<string>(),
          isDark: false,
          showStage: true,
          showUnstage: false,
          showRestore: false,
          newFilePaths: new Set<string>(),
          isSelected: (_p: string) => false,
          isStagingForPath: (_p: string) => false,
          isUnstagingForPath: (_p: string) => false,
          isDiscardableFor: (_f: unknown) => false,
          isSectionFullySelected: (_s: unknown) => false,
          isSectionPartiallySelected: (_s: unknown) => false,
          selectableAriaLabel: (p: string) => `Select ${p}`,
          onToggleGroup: vi.fn(),
          onToggleSelect: vi.fn(),
          onToggleGroupSelect: vi.fn(),
          onFileToggle: vi.fn(),
          onFileStage: vi.fn(),
          onFileUnstage: vi.fn(),
          onFileRestore: vi.fn(),
          onFileOpen: vi.fn(),
          onFileDiscardHunk: vi.fn(),
        });
      },
    });
    const w = mount(ParentHarness);
    const cb = w.find(".git-diff-section-check input");
    expect(cb.exists()).toBe(true);
    const aria = cb.attributes("aria-label") ?? "";
    expect(aria).toMatch(/name=b/);
    expect(aria).toMatch(/parent=a/);
  });

  // 7. emit toggle
  it("clicking the row toggles the section", async () => {
    const sec = buildDirectorySections([file("a/x.py")], "", "unstaged")[0];
    const onToggleGroup = vi.fn();
    const w = mountNode(sec, 0, { onToggleGroup });
    await w.find(".git-diff-section-header").trigger("click");
    expect(onToggleGroup).toHaveBeenCalledWith(sec.id);
  });

  // 8. emit select via prop callback
  it("invokes onToggleGroupSelect when checkbox changes", async () => {
    const sec = buildDirectorySections([file("a/x.py")], "", "unstaged")[0];
    const onToggleGroupSelect = vi.fn();
    const w = mountNode(sec, 0, {
      showStage: true,
      onToggleGroupSelect,
    });
    const cb = w.find(".git-diff-section-check input");
    expect(cb.exists()).toBe(true);
    await cb.setValue(true);
    expect(onToggleGroupSelect).toHaveBeenCalled();
  });

  // 9. forwards file row events via emit
  it("forwards file row events via emit", async () => {
    const sec = buildDirectorySections([file("a/x.py")], "", "unstaged")[0];
    const w = mountNode(sec, 0, { showStage: true });
    // The stubbed GitDiffFileItem renders a <div data-path="a/x.py">
    // Find it under the section body (NOT under the section header)
    const fileRow = w.find(".git-diff-section-body .git-diff-file-item");
    expect(fileRow.exists()).toBe(true);
    // The stub's onClick emits toggle; the parent DiffDirectoryNode
    // re-emits. We verify the parent's emit surface sees the path.
    await fileRow.trigger("click");
    expect(w.emitted("toggle")).toBeTruthy();
    expect(w.emitted("toggle")![0]).toEqual(["a/x.py"]);
  });

  // 10. section.files = [] still renders header
  it("section with empty files but children still renders header", () => {
    // Create a section by hand with empty files but a child
    const sec: DiffSection = {
      id: "dir:a",
      label: "a",
      fullPath: "a",
      fileCount: 0,
      additions: 0,
      deletions: 0,
      files: [],
      children: [
        {
          id: "dir:a/b",
          label: "b",
          fullPath: "a/b",
          fileCount: 1,
          additions: 0,
          deletions: 0,
          files: [file("a/b/y.py")],
          children: [],
          badgeKind: "unstaged",
        },
      ],
      badgeKind: "unstaged",
    };
    const w = mountNode(sec, 0);
    expect(w.find(".git-diff-section-header").exists()).toBe(true);
  });

  // 11. Empty section (no files, no children) hides checkbox
  it("empty section hides checkbox", () => {
    // Build a degenerate section by hand
    const sec: DiffSection = {
      id: "dir:empty",
      label: "empty",
      fullPath: "empty",
      fileCount: 0,
      additions: 0,
      deletions: 0,
      files: [],
      children: [],
      badgeKind: "unstaged",
    };
    const w = mountNode(sec, 0, { showStage: true });
    expect(w.find(".git-diff-section-check").exists()).toBe(false);
  });
});

describe("DiffDirectoryNode — compact directory chains", () => {
  it("renders a breadcrumb row for a single-child run, body hidden", () => {
    const sec = buildDirectorySections([file("a/b/y.py")], "", "unstaged")[0];
    const w = mountNode(sec, 0);
    const crumb = w.find(".git-diff-section-crumb");
    expect(crumb.exists()).toBe(true);
    expect(crumb.text()).toContain("a");
    expect(crumb.text()).toContain("b");
    expect(crumb.text()).toContain("›");
    expect(crumb.text()).toContain("group.chainExpand");
    // Crumb view hides the body.
    const body = w.find(".git-diff-section-body");
    expect(body.attributes("style")).toMatch(/display:\s*none/);
  });

  it("middle-ellipses the crumb when the chain runs deeper than 3", () => {
    const sec = buildDirectorySections(
      [file("a/b/c/d/e.py")],
      "",
      "unstaged",
    )[0];
    const w = mountNode(sec, 0);
    const segs = w
      .findAll(".git-diff-crumb-seg")
      .map((s) => s.text());
    // a → b → c → d(e.py): the file is a leaf, so the last DIRECTORY
    // segment d terminates the crumb.
    expect(segs).toEqual(["a", "…", "d"]);
  });

  it("chain header click routes to onToggleChain, not onToggleGroup", async () => {
    const sec = buildDirectorySections([file("a/b/y.py")], "", "unstaged")[0];
    const onToggleChain = vi.fn();
    const onToggleGroup = vi.fn();
    const w = mountNode(sec, 0, { onToggleChain, onToggleGroup });
    await w.find(".git-diff-section-header").trigger("click");
    expect(onToggleChain).toHaveBeenCalledWith("dir:a");
    expect(onToggleGroup).not.toHaveBeenCalled();
  });

  it("expanded chain reveals the real nested levels as ordinary rows", () => {
    const sec = buildDirectorySections([file("a/b/y.py")], "", "unstaged")[0];
    const w = mountNode(sec, 0, {
      expandedChains: new Set(["dir:a"]),
    });
    // Crumb replaced by the head's own label; the terminal level renders
    // as an ordinary (suppressed) section with its label.
    expect(w.find(".git-diff-section-crumb").exists()).toBe(false);
    const labels = w.findAll(".git-diff-section-label");
    expect(labels[0].text()).toBe("a");
    expect(labels[1].text()).toBe("b");
    // The file row is visible under the revealed chain.
    expect(
      w.find(".git-diff-section-body .git-diff-file-item").exists(),
    ).toBe(true);
  });

  it("suppressed chain link routes header clicks to onToggleGroup", async () => {
    const sec = buildDirectorySections([file("a/b/y.py")], "", "unstaged")[0];
    const onToggleChain = vi.fn();
    const onToggleGroup = vi.fn();
    const w = mountNode(sec, 0, {
      chainSuppressed: true,
      onToggleChain,
      onToggleGroup,
    });
    expect(w.find(".git-diff-section-crumb").exists()).toBe(false);
    await w.find(".git-diff-section-header").trigger("click");
    expect(onToggleGroup).toHaveBeenCalledWith("dir:a");
    expect(onToggleChain).not.toHaveBeenCalled();
  });

  it("revealed chain keeps its links suppressed for the whole run", () => {
    // a → b → c → d/e.py: a is the head, b a mid-link, c the terminal.
    // Expanding a reveals a, b, c as ordinary rows (no nested crumb).
    const sec = buildDirectorySections(
      [file("a/b/c/d/e.py")],
      "",
      "unstaged",
    )[0];
    const w = mountNode(sec, 0, {
      expandedChains: new Set(["dir:a"]),
    });
    expect(w.find(".git-diff-section-crumb").exists()).toBe(false);
    const labels = w.findAll(".git-diff-section-label").map((l) => l.text());
    expect(labels).toEqual(["a", "b", "c", "d"]);
  });
});
