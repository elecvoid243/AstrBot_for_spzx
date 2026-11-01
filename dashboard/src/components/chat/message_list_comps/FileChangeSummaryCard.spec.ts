// Tests for FileChangeSummaryCard.vue — the end-of-turn "N files changed"
// card. Focus: the header collapse toggle (2026-09-17), which folds the
// file rows away while keeping the count and totals readable.
//
// Mount strategy mirrors FileChangeCard.spec.ts: mock the API wrapper and
// the toast store so the card mounts without pinia / network, and stub the
// heavy children (DiffPreview, the Vuetify widgets without a global stub).
//
// Author: elecvoid243 | 2026-09-17

import { describe, it, expect, vi, beforeEach } from "vitest";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import FileChangeSummaryCard from "./FileChangeSummaryCard.vue";
import zh from "@/i18n/locales/zh-CN/features/chat.json";
import type { FileChangeSummaryFile } from "@/utils/fileChangeTool";

const mocks = vi.hoisted(() => ({
  fileChangeStatus: vi.fn(),
  fileChangeDiff: vi.fn(),
}));

vi.mock("@/api/v1", () => ({
  chatApi: {
    fileChangeStatus: mocks.fileChangeStatus,
    fileChangeDiff: mocks.fileChangeDiff,
  },
  pluginExtensionApi: { post: vi.fn() },
}));

vi.mock("@/utils/toast", () => ({
  useToast: () => ({ error: vi.fn(), success: vi.fn() }),
}));

// v-btn is not part of the global test stubs: render a plain button that
// forwards the fallthrough attrs (class / title / aria-expanded) so the
// spec can assert on the toggle's accessibility contract.
const VBtnStub = {
  name: "VBtn",
  inheritAttrs: false,
  template: '<button v-bind="$attrs"><slot /></button>',
};

const STUBS = {
  "v-btn": VBtnStub,
  "v-progress-circular": { template: "<span class='progress-stub' />" },
  DiffPreview: {
    name: "DiffPreview",
    props: [
      "content",
      "filePath",
      "baseContent",
      "overlayMode",
      "overlaySupported",
      "isDark",
      "maxLines",
      "collapsible",
      "commentable",
    ],
    template:
      "<div class='diff-stub' :data-base='baseContent' " +
      ":data-overlay-supported='String(overlaySupported)' />",
  },
};

const FILES: FileChangeSummaryFile[] = [
  {
    path: "F:\\proj\\a.vue",
    kind: "edit",
    adds: 47,
    dels: 8,
    backup_id: "b1",
    sha256: "s1",
    runtime: "local",
    diff_available: true,
  },
  {
    path: "F:\\proj\\b.ts",
    kind: "edit",
    adds: 0,
    dels: 12,
    backup_id: "b2",
    sha256: "s2",
    runtime: "local",
    diff_available: true,
  },
];

function mountCard(files: FileChangeSummaryFile[] = FILES) {
  return mount(FileChangeSummaryCard, {
    props: { files, isDark: false, sessionId: "owned-session", messageId: 42 },
    global: { stubs: STUBS },
  });
}

describe("FileChangeSummaryCard collapse toggle", () => {
  beforeEach(() => {
    mocks.fileChangeStatus.mockReset();
    mocks.fileChangeDiff.mockReset();
    mocks.fileChangeStatus.mockResolvedValue({
      data: { status: "ok", data: { files: [] } },
    });
    mocks.fileChangeDiff.mockResolvedValue({
      data: {
        status: "ok",
        data: { diff: "@@ -1 +1 @@", truncated: false, base_content: "old\n" },
      },
    });
  });

  it("keeps checksum-free legacy results display-only", async () => {
    const wrapper = mountCard([{ ...FILES[0], sha256: "" }]);
    await flushPromises();
    expect(wrapper.find(".fcs-file").attributes("disabled")).toBeDefined();
    expect(wrapper.find(".fcs-undo").exists()).toBe(false);
    expect(mocks.fileChangeDiff).not.toHaveBeenCalled();
  });

  it("starts expanded and offers the collapse action on the whole header", async () => {
    const wrapper = mountCard();
    await flushPromises();

    expect(wrapper.findAll(".fcs-row")).toHaveLength(2);
    expect(wrapper.classes()).not.toContain("fcs-card--collapsed");
    expect(wrapper.find(".fcs-title").text()).toContain("2");
    const head = wrapper.find(".fcs-head");
    // The full-width header row is the hit target, not a small chevron: a
    // native <button>, so tab focus and Enter/Space activation come free.
    expect(head.element.tagName).toBe("BUTTON");
    expect(head.attributes("type")).toBe("button");
    expect(head.attributes("aria-expanded")).toBe("true");
    expect(head.attributes("title")).toBe(zh.fileChanges.collapse);
  });

  it("hides the rows but keeps the count and totals visible", async () => {
    const wrapper = mountCard();
    await flushPromises();

    await wrapper.find(".fcs-head").trigger("click");

    expect(wrapper.findAll(".fcs-row")).toHaveLength(0);
    expect(wrapper.classes()).toContain("fcs-card--collapsed");
    expect(wrapper.find(".fcs-title").text()).toContain("2");
    expect(wrapper.find(".fcs-total").text()).toBe("+47−20");
    const head = wrapper.find(".fcs-head");
    expect(head.attributes("aria-expanded")).toBe("false");
    expect(head.attributes("title")).toBe(zh.fileChanges.expand);

    await wrapper.find(".fcs-head").trigger("click");

    expect(wrapper.findAll(".fcs-row")).toHaveLength(2);
    expect(wrapper.classes()).not.toContain("fcs-card--collapsed");
  });

  it("keeps the row expansion and the cached diff across a collapse", async () => {
    const wrapper = mountCard();
    await flushPromises();

    await wrapper.find(".fcs-file").trigger("click");
    await flushPromises();

    expect(mocks.fileChangeDiff).toHaveBeenCalledTimes(1);
    expect(wrapper.find(".fcs-body").exists()).toBe(true);

    await wrapper.find(".fcs-head").trigger("click");
    expect(wrapper.find(".fcs-body").exists()).toBe(false);

    await wrapper.find(".fcs-head").trigger("click");
    await flushPromises();

    // Re-expanding replays the cached diff instead of refetching it.
    expect(wrapper.find(".fcs-body").exists()).toBe(true);
    expect(wrapper.find(".diff-stub").exists()).toBe(true);
    expect(mocks.fileChangeDiff).toHaveBeenCalledTimes(1);
  });
});

describe("FileChangeSummaryCard overlay wiring", () => {
  beforeEach(() => {
    mocks.fileChangeStatus.mockReset();
    mocks.fileChangeDiff.mockReset();
    mocks.fileChangeStatus.mockResolvedValue({
      data: { status: "ok", data: { files: [] } },
    });
    mocks.fileChangeDiff.mockResolvedValue({
      data: {
        status: "ok",
        data: {
          diff: "@@ -1 +1 @@\n-old\n+new",
          truncated: false,
          base_content: "old\n",
        },
      },
    });
  });

  async function expandFirst(): Promise<VueWrapper> {
    const wrapper = mountCard([FILES[0]]);
    await flushPromises();
    await wrapper.find(".fcs-file").trigger("click");
    await flushPromises();
    return wrapper;
  }

  it("feeds the baseline to DiffPreview and accepts its mode requests", async () => {
    const wrapper = await expandFirst();
    const dp = wrapper.findComponent({ name: "DiffPreview" });

    expect(dp.props("baseContent")).toBe("old\n");
    expect(dp.props("overlaySupported")).toBe(true);
    expect(dp.props("overlayMode")).toBe(false);

    dp.vm.$emit("update:overlayMode", true);
    await flushPromises();

    expect(dp.props("overlayMode")).toBe(true);
  });

  it("enables the inline comment gutter on the expanded diff", async () => {
    const wrapper = await expandFirst();
    const dp = wrapper.findComponent({ name: "DiffPreview" });

    // The quick-comment gutter is DiffPreview's default; the summary card
    // must not opt out (it used to pass commentable=false). filePath is the
    // store partition key, so it must match the row path.
    expect(dp.props("commentable")).not.toBe(false);
    expect(dp.props("filePath")).toBe(FILES[0].path);
    // The overlay FAB collapses the card; the header must stay able to
    // expand it again.
    expect(dp.props("collapsible")).not.toBe(false);
  });

  it("keeps the slot disabled without a baseline or for created files", async () => {
    mocks.fileChangeDiff.mockResolvedValue({
      data: { status: "ok", data: { diff: "@@ -1 +1 @@", truncated: false } },
    });
    let wrapper = await expandFirst();
    let dp = wrapper.findComponent({ name: "DiffPreview" });
    expect(dp.props("overlaySupported")).toBe(false);

    // Created files legitimately have an empty baseline, but there is no
    // old content to lay the additions under → no overlay view.
    mocks.fileChangeDiff.mockResolvedValue({
      data: {
        status: "ok",
        data: { diff: "@@ -0,0 +1 @@\n+new", truncated: false, base_content: "" },
      },
    });
    wrapper = mountCard([{ ...FILES[0], kind: "created" }]);
    await flushPromises();
    await wrapper.find(".fcs-file").trigger("click");
    await flushPromises();
    dp = wrapper.findComponent({ name: "DiffPreview" });
    expect(dp.props("overlaySupported")).toBe(false);
  });
});

describe("FileChangeSummaryCard no-stat labels", () => {
  beforeEach(() => {
    mocks.fileChangeStatus.mockReset();
    mocks.fileChangeStatus.mockResolvedValue({
      data: { status: "ok", data: { files: [] } },
    });
  });

  it("labels a local file whose content is gone as removed, keeps a present-but-baseline-less file as no-stat", async () => {
    // The end-of-turn summary reads the current file from disk to compute the
    // net diff. A file the turn touched in a worktree that was then removed
    // (or a file that was moved) has no readable content: the backend leaves
    // sha256 empty, adds/dels null. The row must say the file is gone instead
    // of a bare "no stats", so it is not mistaken for a bug.
    const files: FileChangeSummaryFile[] = [
      {
        path: "F:\\proj\\gone.vue",
        kind: "edit",
        adds: null,
        dels: null,
        backup_id: "b1",
        sha256: "",
        runtime: "local",
        diff_available: false,
      },
      {
        path: "F:\\proj\\nobaseline.ts",
        kind: "edit",
        adds: null,
        dels: null,
        backup_id: "",
        sha256: "s",
        runtime: "local",
        diff_available: false,
      },
    ];
    const wrapper = mountCard(files);
    await flushPromises();

    const rows = wrapper.findAll(".fcs-row");
    expect(rows[0].text()).toContain("已移除或不可读");
    expect(rows[1].text()).toContain(zh.fileChanges.noStat);
  });
});

it("keeps imported file changes display-only", async () => {
  const wrapper = mountCard();
  await wrapper.setProps({ sourceUnavailable: true });
  await flushPromises();
  mocks.fileChangeDiff.mockClear();
  await wrapper.find(".fcs-file").trigger("click");
  expect(mocks.fileChangeDiff).not.toHaveBeenCalled();
  expect(wrapper.findAll(".fcs-row")).toHaveLength(2);
});
