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
import { flushPromises, mount } from "@vue/test-utils";
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
  DiffPreview: { name: "DiffPreview", template: "<div class='diff-stub' />" },
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
    props: { files, isDark: false },
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
      data: { status: "ok", data: { diff: "@@ -1 +1 @@", truncated: false } },
    });
  });

  it("starts expanded and offers the collapse action", async () => {
    const wrapper = mountCard();
    await flushPromises();

    expect(wrapper.findAll(".fcs-row")).toHaveLength(2);
    expect(wrapper.classes()).not.toContain("fcs-card--collapsed");
    expect(wrapper.find(".fcs-title").text()).toContain("2");
    const toggle = wrapper.find(".fcs-toggle");
    expect(toggle.attributes("aria-expanded")).toBe("true");
    expect(toggle.attributes("title")).toBe(zh.fileChanges.collapse);
  });

  it("hides the rows but keeps the count and totals visible", async () => {
    const wrapper = mountCard();
    await flushPromises();

    await wrapper.find(".fcs-toggle").trigger("click");

    expect(wrapper.findAll(".fcs-row")).toHaveLength(0);
    expect(wrapper.classes()).toContain("fcs-card--collapsed");
    expect(wrapper.find(".fcs-title").text()).toContain("2");
    expect(wrapper.find(".fcs-total").text()).toBe("+47−20");
    const toggle = wrapper.find(".fcs-toggle");
    expect(toggle.attributes("aria-expanded")).toBe("false");
    expect(toggle.attributes("title")).toBe(zh.fileChanges.expand);

    await wrapper.find(".fcs-toggle").trigger("click");

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

    await wrapper.find(".fcs-toggle").trigger("click");
    expect(wrapper.find(".fcs-body").exists()).toBe(false);

    await wrapper.find(".fcs-toggle").trigger("click");
    await flushPromises();

    // Re-expanding replays the cached diff instead of refetching it.
    expect(wrapper.find(".fcs-body").exists()).toBe(true);
    expect(wrapper.find(".diff-stub").exists()).toBe(true);
    expect(mocks.fileChangeDiff).toHaveBeenCalledTimes(1);
  });
});
