// Author: elecvoid243
// Date: 2026-07-30
// Spec: docs/superpowers/specs/2026-06-30-diff-fullscreen-design.md §4
//
// Regression coverage for the DiffPreview fullscreen / expansion
// relationship:
//   * fullscreen should always render the full diff (no `maxLines` cap);
//   * the "show all" inline button must not appear in the fullscreen
//     overlay (the user does not need to click it again);
//   * entering / exiting fullscreen must not mutate the
//     non-fullscreen `showAllLines` state — the two states are
//     independent.
//
// Mount strategy mirrors DocumentManager.spec.ts: stub the heavy
// children so the test focuses on DiffPreview's own state graph.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises, type VueWrapper } from "@vue/test-utils";
import { nextTick, ref } from "vue";
import DiffPreview from "./DiffPreview.vue";
import FileCommentEditor from "./FileCommentEditor.vue";
import { useFileComments } from "@/composables/useFileComments";

const STUB_CHILDREN = {
  FileCommentEditor: { template: "<div />" },
  "v-icon": { template: "<i />" },
  // 模式组第三格在基准懒取期间用转圈占位，断言按 class 匹配。
  "v-progress-circular": { template: "<span class='v-progress-circular' />" },
};

function buildDiffContent(totalLines: number): string {
  // Minimal valid unified diff: one @@ hunk with `totalLines` added
  // lines.  `parseUnifiedDiff` reads `@@` and the `+/ /-` prefix
  // characters, so this is enough to drive the line cap.
  const body = Array.from(
    { length: totalLines },
    (_, i) => `+line ${i + 1}`,
  ).join("\n");
  return `@@ -0,0 +1,${totalLines} @@\n${body}\n`;
}

/**
 * Build a diff large enough that its raw text length exceeds the
 * default `maxChars` (2000) so the `truncated` computed becomes
 * `true`. Each line carries a padding string to inflate the byte
 * count without changing the parse-count of lines / hunks.
 */
function buildLongDiffContent(totalLines: number, padding: string): string {
  const body = Array.from(
    { length: totalLines },
    (_, i) => `+line ${i + 1} ${padding}`,
  ).join("\n");
  return `@@ -0,0 +1,${totalLines} @@\n${body}\n`;
}

interface DiffPreviewInternals {
  isFullscreen: boolean;
  showAllLines: boolean;
  effectiveMaxLines: number;
  enterFullscreen: () => void;
  exitFullscreen: () => void;
  collapsedOverflow: number;
  truncated: boolean;
  showTruncationWarning: boolean;
}

function mountDiff(totalLines = 100): VueWrapper {
  return mount(DiffPreview, {
    props: {
      content: buildDiffContent(totalLines),
      filePath: "sample.txt",
      maxLines: 30,
    },
    global: { stubs: STUB_CHILDREN },
  });
}

/**
 * Mount variant that produces a diff whose raw text is long enough
 * (>2000 chars) to engage the `truncated` computed, so the
 * truncation-warning v-if has something to assert against.
 */
function mountLongDiff(): VueWrapper {
  // 300 lines × ~30 chars per padded line ≈ >9 KB. Plenty beyond
  // the 2000-char default `maxChars`, so `truncated === true`.
  return mount(DiffPreview, {
    props: {
      content: buildLongDiffContent(300, "abcdefghijklmnopqrstuvwxyz"),
      filePath: "sample.txt",
      maxLines: 30,
    },
    global: { stubs: STUB_CHILDREN },
  });
}

describe("DiffPreview fullscreen expansion", () => {
  beforeEach(async () => {
    document.body.style.overflow = "";
  });

  afterEach(() => {
    document.body.style.overflow = "";
  });

  it("entering fullscreen expands the diff even when showAllLines is false", async () => {
    const wrapper = mountDiff();
    const vm = wrapper.vm as unknown as DiffPreviewInternals;

    expect(vm.isFullscreen).toBe(false);
    expect(vm.showAllLines).toBe(false);
    expect(vm.effectiveMaxLines).toBe(30);
    expect(vm.collapsedOverflow).toBeGreaterThan(0);

    vm.enterFullscreen();
    await nextTick();

    expect(vm.isFullscreen).toBe(true);
    expect(vm.effectiveMaxLines).toBe(Number.POSITIVE_INFINITY);
    expect(vm.collapsedOverflow).toBe(0);
  });

  it("fullscreen overlay does not render the inline 'show more' button", async () => {
    const wrapper = mountDiff();
    const vm = wrapper.vm as unknown as DiffPreviewInternals;

    expect(wrapper.findAll(".diff-show-more").length).toBeGreaterThan(0);

    vm.enterFullscreen();
    await nextTick();

    const overlay = document.body.querySelector(".diff-fullscreen-overlay");
    expect(overlay).not.toBeNull();
    expect(overlay!.querySelectorAll(".diff-show-more").length).toBe(0);
  });

  it("entering fullscreen does not mutate the non-fullscreen showAllLines flag", async () => {
    const wrapper = mountDiff();
    const vm = wrapper.vm as unknown as DiffPreviewInternals;

    vm.enterFullscreen();
    await nextTick();

    expect(vm.isFullscreen).toBe(true);
    expect(vm.showAllLines).toBe(false);
  });

  it("exiting fullscreen restores the non-fullscreen line cap while keeping prior showAllLines state", async () => {
    const wrapper = mountDiff();
    const vm = wrapper.vm as unknown as DiffPreviewInternals;

    // Scenario A: never clicked "show all" → exit should re-cap.
    vm.enterFullscreen();
    await nextTick();
    vm.exitFullscreen();
    await nextTick();

    expect(vm.isFullscreen).toBe(false);
    expect(vm.showAllLines).toBe(false);
    expect(vm.effectiveMaxLines).toBe(30);

    // Scenario B: user previously expanded → exit should stay expanded.
    vm.showAllLines = true;
    await nextTick();
    expect(vm.effectiveMaxLines).toBe(Number.POSITIVE_INFINITY);

    vm.enterFullscreen();
    await nextTick();
    expect(vm.effectiveMaxLines).toBe(Number.POSITIVE_INFINITY);
    expect(vm.showAllLines).toBe(true);

    vm.exitFullscreen();
    await nextTick();
    expect(vm.isFullscreen).toBe(false);
    expect(vm.showAllLines).toBe(true);
    expect(vm.effectiveMaxLines).toBe(Number.POSITIVE_INFINITY);
  });
});

describe("DiffPreview truncation warning visibility", () => {
  beforeEach(() => {
    document.body.style.overflow = "";
  });

  afterEach(() => {
    document.body.style.overflow = "";
  });

  it("renders the yellow truncation warning in the default collapsed view", () => {
    const wrapper = mountLongDiff();
    const vm = wrapper.vm as unknown as DiffPreviewInternals;

    expect(vm.truncated).toBe(true);
    expect(vm.collapsedOverflow).toBeGreaterThan(0);
    expect(vm.showTruncationWarning).toBe(true);

    const warnings = wrapper.findAll(".diff-truncation-warning");
    expect(warnings.length).toBe(1);
    expect(warnings[0].text()).toContain("Diff truncated");
  });

  it("hides the warning after the user clicks 'Show all' inline", async () => {
    const wrapper = mountLongDiff();
    const vm = wrapper.vm as unknown as DiffPreviewInternals;

    expect(vm.showTruncationWarning).toBe(true);

    vm.showAllLines = true;
    await nextTick();

    // `truncated` (raw length) is unchanged, but the visible clip
    // is gone, so the user-facing warning should disappear.
    expect(vm.truncated).toBe(true);
    expect(vm.collapsedOverflow).toBe(0);
    expect(vm.showTruncationWarning).toBe(false);
    expect(wrapper.findAll(".diff-truncation-warning").length).toBe(0);
  });

  it("hides the warning when entering fullscreen", async () => {
    const wrapper = mountLongDiff();
    const vm = wrapper.vm as unknown as DiffPreviewInternals;

    expect(vm.showTruncationWarning).toBe(true);

    vm.enterFullscreen();
    await nextTick();

    // Same reasoning as the 'show all' path: fullscreen lifts the
    // `maxLines` cap, so there is nothing to warn about.
    expect(vm.truncated).toBe(true);
    expect(vm.collapsedOverflow).toBe(0);
    expect(vm.showTruncationWarning).toBe(false);

    const inlineWarnings = wrapper.findAll(".diff-truncation-warning");
    expect(inlineWarnings.length).toBe(0);

    const overlay = document.body.querySelector(".diff-fullscreen-overlay");
    expect(overlay).not.toBeNull();
    expect(overlay!.querySelectorAll(".diff-truncation-warning").length).toBe(
      0,
    );
  });

  it("hides the warning after exiting fullscreen when the user never clicked 'show all'", async () => {
    const wrapper = mountLongDiff();
    const vm = wrapper.vm as unknown as DiffPreviewInternals;

    expect(vm.showTruncationWarning).toBe(true);

    vm.enterFullscreen();
    await nextTick();
    expect(vm.showTruncationWarning).toBe(false);

    vm.exitFullscreen();
    await nextTick();

    // Exit should restore the original truncation state.
    expect(vm.isFullscreen).toBe(false);
    expect(vm.showAllLines).toBe(false);
    expect(vm.collapsedOverflow).toBeGreaterThan(0);
    expect(vm.showTruncationWarning).toBe(true);
    expect(wrapper.findAll(".diff-truncation-warning").length).toBe(1);
  });
});

describe("DiffPreview comment dock", () => {
  // 2026-07-30 sticky-dock: the inline comment editor is wrapped in
  // a `.diff-comment-dock` so it pins to the bottom of the *visible*
  // scroll viewport. Without the dock the editor sat at the diff
  // tail, and focusing its textarea made the browser scroll the
  // viewport down there — disorienting on long diffs. These tests
  // pin the *structural* contract (the dock exists exactly when the
  // editor is open, and the editor component is nested inside it);
  // the `position: sticky` rule itself lives in the scoped CSS and
  // is what makes the nesting translate into "pinned to viewport
  // bottom" at runtime.
  type EditorHost = {
    openNewEditor: (line: number) => void;
    enterFullscreen: () => void;
  };

  afterEach(() => {
    document.body.style.overflow = "";
  });

  it("renders no dock while the editor is closed", () => {
    const wrapper = mountDiff();
    expect(wrapper.find(".diff-comment-dock").exists()).toBe(false);
    expect(wrapper.findComponent(FileCommentEditor).exists()).toBe(false);
  });

  it("wraps the editor in a dock in the normal view when opened", async () => {
    const wrapper = mountDiff();
    const vm = wrapper.vm as unknown as EditorHost;

    vm.openNewEditor(1);
    await nextTick();

    const dock = wrapper.find(".diff-comment-dock");
    expect(dock.exists()).toBe(true);
    // The editor component must be a descendant of the dock — that
    // nesting is precisely what lets `position: sticky` on the dock
    // carry the editor to the viewport bottom. If a future change
    // moves the editor back out of the dock, the jump-to-tail bug
    // returns, so we assert the nesting explicitly.
    expect(dock.findComponent(FileCommentEditor).exists()).toBe(true);
  });

  it("wraps the editor in a dock inside the fullscreen overlay", async () => {
    const wrapper = mountDiff();
    const vm = wrapper.vm as unknown as EditorHost;

    vm.enterFullscreen();
    await nextTick();
    vm.openNewEditor(1);
    // The overlay is a <Teleport>; the dock only appears after
    // `activeEditLine` flips *post*-overlay-mount, which is an
    // incremental patch to the teleported subtree. happy-dom needs
    // a full promise flush (a single nextTick is not enough here,
    // unlike the truncation-warning case where the warning is
    // already determined at first overlay render) before the dock
    // lands in document.body. In a real browser this update is a
    // synchronous reactive flush, so the product is unaffected.
    await flushPromises();

    // The overlay is teleported to <body>, so query from the overlay
    // element rather than the wrapper's local DOM tree.
    const overlay = document.body.querySelector(".diff-fullscreen-overlay");
    expect(overlay).not.toBeNull();
    // NOTE: we deliberately do NOT assert the dock via
    // `overlay.querySelector(".diff-comment-dock")`. happy-dom's
    // Teleport moves the overlay into <body> on first render but
    // drops *later* incremental patches to the teleported subtree
    // (the dock only appears once `activeEditLine` flips after the
    // overlay mounted), so a DOM-level query reads stale markup
    // even after `flushPromises`. The component-instance layer is
    // unaffected by that quirk, and the editor renders *only*
    // inside a dock — so two editor instances (normal view + the
    // fullscreen copy) prove both docks rendered. In a real browser
    // the teleported DOM patch lands normally, so the product shows
    // the dock correctly.
    expect(wrapper.findAllComponents(FileCommentEditor).length).toBe(2);
  });

  it("teleports the dock into the provided scroll container", async () => {
    // The actual fix: with a provider, the dock must relocate into
    // the injected scroller (so `position: sticky` sticks to it)
    // instead of staying inside the component's own .diff-preview
    // tree. Asserting both sides makes this a real regression guard
    // — a future change that drops the <Teleport> would leave the
    // dock in .diff-preview and fail the second expectation, while
    // the no-provider test above would still pass.
    const container = document.createElement("div");
    document.body.appendChild(container);
    try {
      const wrapper = mount(DiffPreview, {
        props: {
          content: buildDiffContent(40),
          filePath: "sample.txt",
          maxLines: 30,
        },
        global: {
          stubs: STUB_CHILDREN,
          provide: { diffScrollContainer: ref(container) },
        },
      });
      const vm = wrapper.vm as unknown as EditorHost;

      vm.openNewEditor(1);
      await nextTick();

      expect(container.querySelector(".diff-comment-dock")).not.toBeNull();
      expect(wrapper.find(".diff-comment-dock").exists()).toBe(false);
    } finally {
      document.body.removeChild(container);
    }
  });
});

// 2026-08-09 (elecvoid243): bulk fold controls (fullscreen only).
describe("DiffPreview bulk fold controls (fullscreen)", () => {
  it("collapse all folds every hunk; expand all restores them", async () => {
    // Two hunks so the bulk action has more than one target.
    const content = [
      "@@ -1,2 +1,2 @@\n line1\n+line2\n",
      "@@ -5,1 +6,1 @@\n-old\n+new\n",
    ].join("");
    const wrapper = mount(DiffPreview, {
      props: { content, filePath: "sample.txt", maxLines: 30 },
      global: { stubs: STUB_CHILDREN },
    });
    const vm = wrapper.vm as unknown as DiffPreviewInternals & {
      collapseAllHunks: () => void;
      expandAllHunks: () => void;
    };
    vm.enterFullscreen();
    await nextTick();

    // The bulk buttons render inside the overlay (first teleport patch).
    const overlay = document.body.querySelector(".diff-fullscreen-overlay")!;
    expect(overlay.querySelectorAll(".diff-hunk-bulk-btn").length).toBe(2);

    // happy-dom's Teleport drops later patches / event listeners on the
    // overlay subtree (see the comment dock describe above), so we drive
    // the handlers through the setup bindings and assert on the inline
    // card — it shares the same `collapsedHunks` state and is not
    // teleported.
    expect(wrapper.findAll(".hunk-header").length).toBe(2);
    expect(wrapper.findAll(".is-hunk-folded").length).toBe(0);

    vm.collapseAllHunks();
    await nextTick();
    expect(wrapper.findAll(".is-hunk-folded").length).toBe(2);

    vm.expandAllHunks();
    await nextTick();
    expect(wrapper.findAll(".is-hunk-folded").length).toBe(0);
  });

  it("bulk buttons are not rendered in the inline (non-fullscreen) card", () => {
    const wrapper = mountDiff();
    expect(wrapper.findAll(".diff-hunk-bulk-btn").length).toBe(0);
  });
});

// 2026-08-13 (elecvoid243): del-line comments (old side).
// Spec: docs/superpowers/specs/2026-08-13-diff-comments-old-side-design.md §4
// A one-hunk diff: del oldNo=1, ctx oldNo=2/newNo=1, add newNo=2.
const DEL_DIFF = "@@ -1,3 +1,2 @@\n-old1\n ctx1\n+new1\n";

function mountDelDiff() {
  return mount(DiffPreview, {
    props: { content: DEL_DIFF, filePath: "a.txt", maxLines: 30 },
    global: { stubs: STUB_CHILDREN },
  });
}

describe("DiffPreview del-line comments (old side, spec 2026-08-13)", () => {
  beforeEach(() => {
    useFileComments().clearAll();
  });

  it("unified: hovering a del row shows the + button", async () => {
    const w = mountDelDiff();
    const delRow = w.find(".diff-line.del");
    await delRow.trigger("mouseenter");
    expect(delRow.find(".diff-comment-add").exists()).toBe(true);
    // ctx/add rows keep working on the new side.
    await w.find(".diff-line.add").trigger("mouseenter");
    expect(w.find(".diff-line.add .diff-comment-add").exists()).toBe(true);
  });

  it("unified: clicking + on a del row opens the editor with side=old", async () => {
    const w = mountDelDiff();
    const vm = w.vm as unknown as {
      activeEditSide: string;
      activeEditLine: number | null;
    };
    await w.find(".diff-line.del").trigger("mouseenter");
    await w.find(".diff-line.del .diff-comment-add").trigger("click");
    expect(vm.activeEditSide).toBe("old");
    expect(vm.activeEditLine).toBe(1);
  });

  it("unified: saving a del-row comment stores side=old + del-line content", async () => {
    const w = mountDelDiff();
    const vm = w.vm as unknown as {
      activeEditSide: string;
      onSaveComment: (p: {
        text: string;
        commentId: string | null;
        line: number;
      }) => void;
    };
    await w.find(".diff-line.del").trigger("mouseenter");
    await w.find(".diff-line.del .diff-comment-add").trigger("click");
    vm.onSaveComment({ text: "为什么删掉", commentId: null, line: 1 });
    await nextTick();
    const comments = useFileComments().commentsForFile("a.txt");
    expect(comments).toHaveLength(1);
    expect(comments[0].side).toBe("old");
    expect(comments[0].line).toBe(1);
    // Spec decision (B): lineContent comes from the hunk del line.
    expect(comments[0].lineContent).toBe("old1");
    expect(comments[0].diffHunk?.side).toBe("old");
    expect(comments[0].diffHunk?.oldLine).toBe(1);
  });

  it("unified: del row with an existing old comment renders the indicator", async () => {
    useFileComments().addCommentWithContext({
      filePath: "a.txt",
      line: 1,
      text: "已评论",
      context: { lineContent: "old1", contextBefore: null, contextAfter: null },
      side: "old",
      diffHunk: {
        header: "@@ -1,3 +1,2 @@",
        lines: [
          { type: "del", content: "old1", oldNo: 1, newNo: null },
          { type: "ctx", content: "ctx1", oldNo: 2, newNo: 1 },
          { type: "add", content: "new1", oldNo: null, newNo: 2 },
        ],
        newLine: null,
        side: "old",
        oldLine: 1,
      },
    });
    const w = mountDelDiff();
    const delRow = w.find(".diff-line.del");
    expect(delRow.classes()).toContain("has-comment");
    expect(delRow.find(".diff-comment-indicator").exists()).toBe(true);
  });

  it("split: del-only row shows + in the LEFT cell and opens with side=old", async () => {
    const w = mountDelDiff();
    const vm = w.vm as unknown as {
      activeEditSide: string;
      setViewMode: (m: "unified" | "split") => void;
    };
    vm.setViewMode("split");
    await nextTick();
    const delOnlyRow = w.find(".diff-row-split.del-only");
    await delOnlyRow.trigger("mouseenter");
    const btn = delOnlyRow.find(".diff-cell.left .diff-comment-add");
    expect(btn.exists()).toBe(true);
    await btn.trigger("click");
    expect(vm.activeEditSide).toBe("old");
  });
});

// 2026-08-14 diff syntax highlighting: lines are tokenized with the
// shared Shiki highlighter (old side = ctx+del, new side = ctx+add)
// and rendered as colored spans; unknown languages keep the escaped
// plain-text path.
describe("DiffPreview syntax highlighting (2026-08-14)", () => {
  it("renders colored token spans for a known language", async () => {
    const wrapper = mount(DiffPreview, {
      props: {
        // Context lines carry the Shiki colors: paired del/add lines
        // are claimed by the intra-line segment highlight instead
        // (changed segments win over token colors there).
        content:
          "@@ -1,3 +1,3 @@\n def f(): pass\n-x = 1\n+x = 2\n def g(): pass",
        filePath: "sample.py",
        isDark: false,
      },
      global: { stubs: STUB_CHILDREN },
    });
    // Shiki loads asynchronously; wait until a keyword token span
    // shows up in one of the line-content slots.
    await vi.waitFor(
      () => {
        const hit = wrapper
          .findAll(".line-content")
          .some((el) => el.html().includes('<span style="color:'));
        expect(hit).toBe(true);
      },
      { timeout: 8000 },
    );
    // Text content is unchanged by the highlight layer.
    expect(wrapper.text()).toContain("def f(): pass");
  });

  it("keeps escaped plain text for unknown languages", async () => {
    const wrapper = mount(DiffPreview, {
      props: {
        // No trailing newline: the parser renders a trailing empty
        // line for a final "\n", which would become the LAST
        // .line-content cell and break the assertion below.
        content: "@@ -1,1 +1,1 @@\n-a <b>\n+c <d>",
        filePath: "sample.txt",
        isDark: false,
      },
      global: { stubs: STUB_CHILDREN },
    });
    await nextTick();
    const cells = wrapper.findAll(".line-content");
    expect(cells.length).toBeGreaterThan(0);
    for (const cell of cells) {
      expect(cell.html()).not.toContain('<span style="color:');
    }
    // `<d>` must be escaped text, not interpreted markup.
    expect(cells[cells.length - 1].text()).toBe("c <d>");
  });
});

// Intra-line highlight: paired del/add lines wrap their changed
// word segments in .intra-hl-del / .intra-hl-add spans.
describe("DiffPreview intra-line highlight (2026-08-14)", () => {
  // viewMode initializes from localStorage and earlier tests in this
  // file may leave "split" behind; pin "unified" so the cell layout
  // is deterministic regardless of execution order.
  beforeEach(() => {
    localStorage.setItem("astrbot.diff.viewMode", "unified");
  });

  it("wraps the changed segments of paired del/add lines", () => {
    const wrapper = mount(DiffPreview, {
      props: {
        content: "@@ -1,1 +1,1 @@\n-const a = 1;\n+const a = 2;",
        filePath: "sample.txt",
        isDark: false,
      },
      global: { stubs: STUB_CHILDREN },
    });
    const cells = wrapper.findAll(".line-content");
    expect(cells.length).toBe(2);
    // del side: only the "1" is emphasized, surroundings stay plain.
    expect(cells[0].html()).toContain('intra-hl intra-hl-del">1<');
    expect(cells[0].text()).toBe("const a = 1;");
    // add side: only the "2".
    expect(cells[1].html()).toContain('intra-hl intra-hl-add">2<');
    expect(cells[1].text()).toBe("const a = 2;");
  });

  it("leaves add-only lines without intra-line spans", () => {
    const wrapper = mount(DiffPreview, {
      props: {
        content: "@@ -1,0 +1,1 @@\n+brand new line",
        filePath: "sample.txt",
        isDark: false,
      },
      global: { stubs: STUB_CHILDREN },
    });
    const cells = wrapper.findAll(".line-content");
    expect(cells.length).toBe(1);
    expect(cells[0].html()).not.toContain("intra-hl");
    expect(cells[0].text()).toBe("brand new line");
  });

  it("escapes markup inside changed segments", () => {
    const wrapper = mount(DiffPreview, {
      props: {
        content: "@@ -1,1 +1,1 @@\n-a <b>\n+a <d>",
        filePath: "sample.txt",
        isDark: false,
      },
      global: { stubs: STUB_CHILDREN },
    });
    const cells = wrapper.findAll(".line-content");
    expect(cells.length).toBe(2);
    // `<d>` renders as text, not as a parsed element. (Assert via
    // text(): happy-dom's serializer does not re-escape text nodes,
    // so an html()-level `&lt;` check is meaningless here.)
    expect(cells[1].text()).toBe("a <d>");
    expect(cells[1].findAll("span")).toHaveLength(1);
    expect(cells[1].find(".intra-hl-add").text()).toBe("d");
  });
});


// ─── Overlay mode (2026-10-06, range-compare full-file overlay) ─────
// Spec: docs/superpowers/specs/2026-10-06-git-file-range-diff-frontend-design.md §4

describe("overlay mode (baseContent provided)", () => {
  const BASE = ["l1", "l2", "l3", "l4", "l5"].join("\n");
  const PATCH = "@@ -2,3 +2,4 @@\n l2\n-l3\n+L3\n+L3x\n l4";

  function mountOverlay(extra: Record<string, unknown> = {}) {
    return mount(DiffPreview, {
      props: {
        content: PATCH,
        baseContent: BASE,
        filePath: "sample.txt",
        isDark: false,
        ...extra,
      },
      global: { stubs: STUB_CHILDREN },
    });
  }

  it("renders base lines plus inserted adds (full file + overlay)", () => {
    const wrapper = mountOverlay();
    const rows = wrapper.findAll(".overlay-body .diff-line");
    // 5 基准行(l3 为 del 行)+ 2 插入行(L3 替换 + L3x 纯新增)= 7
    expect(rows.length).toBe(7);
    const classes = rows.map((r) => r.classes());
    expect(classes[1]).toContain("ctx"); // l2
    expect(classes[2]).toContain("del"); // l3
    expect(classes[3]).toContain("add"); // L3
    expect(classes[4]).toContain("add"); // L3x
    expect(classes[5]).toContain("ctx"); // l4
  });

  it("shows base line numbers on ctx/del rows, blank on add rows", () => {
    const wrapper = mountOverlay();
    const rows = wrapper.findAll(".overlay-body .diff-line");
    const num = (i: number) =>
      rows[i].find(".line-number.old").text();
    expect(num(1)).toBe("2");
    expect(num(2)).toBe("3");
    expect(rows[3].find(".line-number.old").text()).toBe("");
  });

  it("folds a large gap and expands it on click", async () => {
    const base = Array.from({ length: 30 }, (_, i) => `l${i + 1}`).join("\n");
    const patch = [
      "@@ -1,3 +1,3 @@\n l1\n-l2\n+L2\n l3",
      "@@ -28,3 +28,3 @@\n l28\n-l29\n+L29\n l30",
    ].join("\n");
    const wrapper = mount(DiffPreview, {
      props: { content: patch, baseContent: base, filePath: "f", isDark: false },
      global: { stubs: STUB_CHILDREN },
    });
    const gap = wrapper.find(".overlay-gap");
    expect(gap.exists()).toBe(true);
    expect(gap.text()).toContain("24");
    // 折叠时:l4..l27 不渲染;可见 = 6 基准行 + 2 add 行(L2/L29)
    expect(wrapper.findAll(".overlay-body .diff-line").length).toBe(30 - 24 + 2);
    await gap.trigger("click");
    expect(wrapper.find(".overlay-gap").exists()).toBe(false);
    expect(wrapper.findAll(".overlay-body .diff-line").length).toBe(32);
  });

  it("renders the overlay slot as the third mode and keeps the group visible", () => {
    // 2026-10-08 (elecvoid243): overlay 不再是「独立按钮 + 顺便隐藏模式组」,
    // 而是模式组里的第三格。旧实现在 overlayActive 时整组消失,这里把它钉死。
    const wrapper = mountOverlay({ overlayMode: true });
    const group = wrapper.find(".diff-view-toggle");
    expect(group.exists()).toBe(true);

    const slots = group.findAll("button");
    expect(slots.length).toBe(3);
    expect(slots[2].attributes("aria-pressed")).toBe("true");
    expect(slots[0].attributes("aria-pressed")).toBe("false");
    expect(slots[1].attributes("aria-pressed")).toBe("false");
  });

  it("requests the mode instead of owning it", async () => {
    const wrapper = mountOverlay();
    await wrapper.findAll(".diff-view-toggle button")[2].trigger("click");

    expect(wrapper.emitted("update:overlayMode")).toEqual([[true]]);
  });

  it("leaving overlay for unified is one dimension, not two switches", async () => {
    const wrapper = mountOverlay({ overlayMode: true });
    await wrapper.findAll(".diff-view-toggle button")[0].trigger("click");

    expect(wrapper.emitted("update:overlayMode")).toEqual([[false]]);
  });

  it("spins in the slot while the base blob is in flight and keeps the patch body", () => {
    // 基准懒取的这段窗口今天完全不可见:按钮显示「已开启」,正文还是 patch。
    // 第三格用转圈把这段时间说清楚,正文等对齐成功再换。
    // 注意不传 baseContent —— 真实调用方在请求回来前也给不出基准,「基准已就绪
    // 且仍在加载」这个组合并不存在。
    const wrapper = mount(DiffPreview, {
      props: {
        content: PATCH,
        filePath: "sample.txt",
        isDark: false,
        overlayMode: true,
        overlayLoading: true,
      },
      global: { stubs: STUB_CHILDREN },
    });
    const slot = wrapper.findAll(".diff-view-toggle button")[2];

    expect(slot.attributes("aria-pressed")).toBe("true");
    expect(slot.find(".v-progress-circular").exists()).toBe(true);
    expect(wrapper.find(".overlay-body").exists()).toBe(false);
    expect(wrapper.find(".hunk-header").exists()).toBe(true);
  });

  it("disables the slot and explains why when the base is unusable", async () => {
    const wrapper = mountOverlay({
      overlayUnavailable: "HEAD 中没有该文件的基准版本",
    });
    const slot = wrapper.findAll(".diff-view-toggle button")[2];

    expect(slot.attributes("disabled")).toBeDefined();
    expect(slot.attributes("title")).toBe("HEAD 中没有该文件的基准版本");

    await slot.trigger("click");
    expect(wrapper.emitted("update:overlayMode")).toBeUndefined();
  });

  it("overlay rows receive shiki syntax highlighting", async () => {
    // I1(final review):overlay 行也必须走 Shiki token 高亮
    // (此前 overlayLineHtml 新建 DiffLine 字面量,高亮 Map 按对象身份
    // 查找永远 miss,只剩 escapeHtml 兜底)。
    const wrapper = mount(DiffPreview, {
      props: {
        content: "@@ -1,2 +1,2 @@\n-const a = 1\n+const a = 2\n const b = 3",
        baseContent: "const a = 1\nconst b = 3\n",
        filePath: "a.ts",
        isDark: false,
      },
      global: { stubs: STUB_CHILDREN },
    });
    await vi.waitFor(
      () => {
        const cell = wrapper.find(".overlay-body .diff-line .line-content");
        expect(cell.exists()).toBe(true);
        expect(cell.html()).toContain("color:");
      },
      { timeout: 8000, interval: 100 },
    );
  });

  it("falls back to plain patch rendering when alignment fails", () => {
    const wrapper = mount(DiffPreview, {
      props: {
        content: "@@ -1,2 +1,2 @@\n NOPE\n l2",
        baseContent: "l1\nl2\n",
        filePath: "f",
        isDark: false,
      },
      global: { stubs: STUB_CHILDREN },
    });
    expect(wrapper.find(".overlay-body").exists()).toBe(false);
    // 普通 patch 视图仍在(hunk header 存在)
    expect(wrapper.find(".hunk-header").exists()).toBe(true);
  });
});
