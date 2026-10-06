// DocumentManager fullscreen state + Esc wiring.
//
// Mounts the real DocumentManager with a heavy-stub strategy: the
// children that fetch from the backend (FileBrowserTree, DocumentEditor,
// DocumentHistoryPanel, DocumentViewModeTab, FileBrowserCodeView, etc.)
// are stubbed to a no-op div. The point of these tests is to assert
// the fullscreen state behavior, not to render the full tree.

import { beforeEach, describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick, ref } from "vue";

// 2026-10-06 range-compare: mock useSpcodeGitFileDiff(数据层已单测)
// + useSpcodeProjectStatus:body 区有「请先加载项目」门控,loaded 必须为 true
vi.mock("@/composables/useSpcodeProjectStatus", () => ({
  useSpcodeProjectStatus: () => ({
    status: {
      value: { umo: "test", directory: "D:/repo", loaded: true },
    },
    refresh: vi.fn(),
  }),
}));

const mockFetchDiff = vi.fn();
const mockGetData = vi.fn();
vi.mock("@/composables/useSpcodeGitFileDiff", () => ({
  useSpcodeGitFileDiff: () => ({
    fetchDiff: (...args: unknown[]) => mockFetchDiff(...args),
    getData: (...args: unknown[]) => mockGetData(...args),
    getState: () => ({ kind: "idle" }),
    invalidateAll: vi.fn(),
    dispose: vi.fn(),
  }),
}));

import DocumentManager from "./DocumentManager.vue";
import DocumentHistoryPanel from "./DocumentHistoryPanel.vue";
import DiffPreview from "./DiffPreview.vue";

const stubs = {
  FileBrowserTree: { template: "<div />" },
  DocumentEditor: { template: "<div />" },
  DocumentHistoryPanel: { template: "<div />" },
  DocumentViewModeTab: { template: "<div />" },
  DocumentTreePanel: { template: "<div />" },
  DocumentPathBar: { template: "<div />" },
  FileBrowserCodeView: { template: "<div />" },
  FileCommentEditor: { template: "<div />" },
  FileBrowserBreadcrumb: { template: "<div />" },
  DiffPreview: { template: "<div />" },
  MarkdownView: { template: "<div />" },
  RecentFilesBlock: { template: "<div />" },
  "v-icon": { template: "<i />" },
};

describe("DocumentManager fullscreen state", () => {
  beforeEach(() => {
    // 2026-09-15 (elecvoid243): DocumentManager 的 useOpenOnDisk →
    // useToast 需要 active pinia（与 GitDiffSidebar.*.spec.ts 同一处理）。
    setActivePinia(createPinia());
  });

  it("toggles isFullscreen when the fullscreen button is clicked", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const wrapper = mount(DocumentManager as any, {
      // cast: DocumentManager requires gitLog/gitShow in defineProps;
      // only state-handler behavior is exercised here, so the
      // composable-shaped objects are stubbed with empty refs.
      props: {
        umo: "test",
        worktree: null,
        projectRoot: null,
        gitLog: {
          state: ref({ kind: "idle" }),
          filter: ref({}),
          refresh: () => Promise.resolve(),
        },
        gitShow: { cached: ref(new Set()), getState: () => ({ kind: "idle" }) },
      },
      global: { stubs },
    });
    await nextTick();
    const vm = wrapper.vm as unknown as {
      isFullscreen: boolean;
      toggleFullscreen: () => void;
    };
    expect(vm.isFullscreen).toBe(false);
    vm.toggleFullscreen();
    expect(vm.isFullscreen).toBe(true);
    vm.toggleFullscreen();
    expect(vm.isFullscreen).toBe(false);
  });

  it("Esc exits fullscreen when fullscreen is on", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const wrapper = mount(DocumentManager as any, {
      props: {
        umo: "test",
        worktree: null,
        projectRoot: null,
        gitLog: {
          state: ref({ kind: "idle" }),
          filter: ref({}),
          refresh: () => Promise.resolve(),
        },
        gitShow: { cached: ref(new Set()), getState: () => ({ kind: "idle" }) },
      },
      global: { stubs },
    });
    await nextTick();
    const vm = wrapper.vm as unknown as {
      isFullscreen: boolean;
      toggleFullscreen: () => void;
    };
    vm.toggleFullscreen();
    expect(vm.isFullscreen).toBe(true);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await nextTick();
    expect(vm.isFullscreen).toBe(false);
  });

  it("Esc is a no-op when fullscreen is off (does not throw)", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const wrapper = mount(DocumentManager as any, {
      props: {
        umo: "test",
        worktree: null,
        projectRoot: null,
        gitLog: {
          state: ref({ kind: "idle" }),
          filter: ref({}),
          refresh: () => Promise.resolve(),
        },
        gitShow: { cached: ref(new Set()), getState: () => ({ kind: "idle" }) },
      },
      global: { stubs },
    });
    await nextTick();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await nextTick();
    const vm = wrapper.vm as unknown as { isFullscreen: boolean };
    expect(vm.isFullscreen).toBe(false);
  });
});


// ─── 2026-10-06 range-compare wiring ────────────────────────────────
// Spec: docs/superpowers/specs/2026-10-06-git-file-range-diff-frontend-design.md §5
const SHA_A = "a".repeat(40);
const SHA_B = "b".repeat(40);
const SHA_C = "c".repeat(40);

const RANGE_FIXTURE = {
  from: SHA_A,
  to: SHA_B,
  fromSha: SHA_A,
  toSha: SHA_B,
  path: "a.md",
  status: "modified" as const,
  oldPath: null,
  isBinary: false,
  baseContent: "v1\n",
  baseSize: 3,
  baseTruncated: false,
  patch: "@@ -1 +1 @@\n-v1\n+v2\n",
  additions: 1,
  deletions: 1,
  truncated: false,
};

describe("DocumentManager range compare wiring", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    mockFetchDiff.mockReset();
    mockGetData.mockReset();
    mockGetData.mockReturnValue(RANGE_FIXTURE);
  });

  const wiringStubs = {
    ...stubs,
    DocumentHistoryPanel: {
      props: ["gitLog", "fileRelative", "currentRevision", "isLoading", "rangeBase"],
      template: "<div />",
    },
    DiffPreview: {
      props: ["content", "baseContent", "commentable"],
      template: "<div />",
    },
  };

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function mountWiring(): any {
    const wrapper = mount(DocumentManager as any, {
      props: {
        umo: "test",
        worktree: null,
        projectRoot: null,
        gitLog: {
          state: ref({ kind: "idle" }),
          filter: ref({}),
          refresh: () => Promise.resolve(),
        },
        gitShow: { cached: ref(new Set()), getState: () => ({ kind: "idle" }) },
      },
      global: { stubs: wiringStubs },
    });
    return wrapper;
  }

  it("compare-range → fetchDiff + state set + selectedRevision cleared", async () => {
    const w = mountWiring();
    const vm = w.vm as any;
    vm.selectedDoc = "a.md";
    await nextTick();
    const panel = w.findComponent(DocumentHistoryPanel);
    panel.vm.$emit("set-range-base", SHA_A);
    await nextTick();
    expect(vm.rangeBase).toBe(SHA_A);
    panel.vm.$emit("compare-range", SHA_A, SHA_B);
    await nextTick();
    // gitLogPath = projectRelativeFromDoc(docsRoot="docs", "a.md") = "docs/a.md"
    expect(mockFetchDiff).toHaveBeenCalledWith("docs/a.md", SHA_A, SHA_B);
    expect(vm.compareRange).toEqual({ from: SHA_A, to: SHA_B });
    expect(vm.selectedRevision).toBeNull();
    expect(vm.viewMode).toBe("diff");
  });

  it("overlay props flow into DiffPreview (baseContent + commentable=false)", async () => {
    const w = mountWiring();
    const vm = w.vm as any;
    vm.selectedDoc = "a.md";
    await nextTick();
    w.findComponent(DocumentHistoryPanel).vm.$emit(
      "compare-range",
      SHA_A,
      SHA_B,
    );
    await nextTick();
    const dp = w.findComponent(DiffPreview);
    expect(dp.exists()).toBe(true);
    expect(dp.props("content")).toBe(RANGE_FIXTURE.patch);
    expect(dp.props("baseContent")).toBe(RANGE_FIXTURE.baseContent);
    expect(dp.props("commentable")).toBe(false);
  });

  it("select-revision clears compareRange (mutual exclusion)", async () => {
    const w = mountWiring();
    const vm = w.vm as any;
    vm.selectedDoc = "a.md";
    await nextTick();
    const panel = w.findComponent(DocumentHistoryPanel);
    panel.vm.$emit("compare-range", SHA_A, SHA_B);
    await nextTick();
    panel.vm.$emit("select-revision", SHA_C);
    await nextTick();
    expect(vm.compareRange).toBeNull();
    expect(vm.selectedRevision).toBe(SHA_C);
  });

  it("onBackToCurrent clears compareRange, keeps rangeBase", async () => {
    const w = mountWiring();
    const vm = w.vm as any;
    vm.selectedDoc = "a.md";
    await nextTick();
    const panel = w.findComponent(DocumentHistoryPanel);
    panel.vm.$emit("set-range-base", SHA_A);
    panel.vm.$emit("compare-range", SHA_A, SHA_B);
    await nextTick();
    vm.onBackToCurrent();
    await nextTick();
    expect(vm.compareRange).toBeNull();
    expect(vm.rangeBase).toBe(SHA_A);
  });

  it("switching doc clears rangeBase + compareRange", async () => {
    const w = mountWiring();
    const vm = w.vm as any;
    vm.selectedDoc = "a.md";
    await nextTick();
    const panel = w.findComponent(DocumentHistoryPanel);
    panel.vm.$emit("set-range-base", SHA_A);
    panel.vm.$emit("compare-range", SHA_A, SHA_B);
    await nextTick();
    vm.selectedDoc = "b.md";
    await nextTick();
    expect(vm.rangeBase).toBeNull();
    expect(vm.compareRange).toBeNull();
  });
});
