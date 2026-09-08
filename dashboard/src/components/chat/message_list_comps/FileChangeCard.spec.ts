// Tests for FileChangeCard.vue — the per-file change card pinned on
// top of the ReasoningTimeline (2026-08-11 file-change visibility).
//
// Mount strategy mirrors DiffPreview.spec.ts: stub the heavy children
// (DiffPreview / CopyableText / v-icon) so the test focuses on the
// card's own state graph (collapse/expand, running/done/error).
//
// Author: elecvoid243 | 2026-08-11

import { describe, it, expect, vi, beforeEach } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import FileChangeCard from "./FileChangeCard.vue";
import type { FileChangeEntry } from "@/utils/fileChangeTool";

// 2026-08-14 open-on-disk / 2026-08-21 open-folder: mock the API wrapper
// and the toast store so the card can be mounted without pinia / network.
const mocks = vi.hoisted(() => ({
  openLocalFile: vi.fn(),
  openLocalFolder: vi.fn(),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
}));

vi.mock("@/api/v1", () => ({
  chatApi: {
    openLocalFile: mocks.openLocalFile,
    openLocalFolder: mocks.openLocalFolder,
  },
}));

vi.mock("@/utils/toast", () => ({
  useToast: () => ({
    error: mocks.toastError,
    success: mocks.toastSuccess,
  }),
}));

const STUBS = {
  DiffPreview: {
    name: "DiffPreview",
    props: ["content", "filePath", "summary", "isDark", "maxLines", "collapsible"],
    template: "<div class='diff-preview-stub' />",
  },
  CopyableText: {
    name: "CopyableText",
    props: ["value"],
    template: "<span class='copyable-stub'>{{ value }}</span>",
  },
  "v-icon": { template: "<i />" },
  "v-progress-circular": { template: "<span class='progress-stub' />" },
};

const WRITE_ENTRY: FileChangeEntry = {
  callId: "w1",
  kind: "write",
  filePath: "F:\\proj\\b.txt",
  status: "done",
  diffStat: null,
  lineCount: 3,
  tool: {
    id: "w1",
    name: "astrbot_file_write_tool",
    args: { path: "F:\\proj\\b.txt", content: "a\nb\nc" },
    result: "File written successfully: F:\\proj\\b.txt",
    finished_ts: 2,
  },
};

// 2026-09-09: the write tool appends " (encoding: xxx)" to its success
// result, so collectFileChanges stores the marked name in filePath. The
// card must keep showing the marker while the open APIs receive the
// clean path (the file on disk never carries it).
const WRITE_ENTRY_MARKED: FileChangeEntry = {
  ...WRITE_ENTRY,
  callId: "w2",
  filePath: "F:\\proj\\b.txt (encoding: utf-8)",
  tool: {
    id: "w2",
    name: "astrbot_file_write_tool",
    args: { path: "F:\\proj\\b.txt", content: "a\nb\nc" },
    result: "File written successfully: F:\\proj\\b.txt (encoding: utf-8)",
    finished_ts: 2,
  },
};

const EDIT_ENTRY: FileChangeEntry = {
  callId: "e1",
  kind: "edit",
  filePath: "F:\\proj\\a.py",
  status: "done",
  diffStat: { adds: 2, dels: 1 },
  lineCount: null,
  tool: {
    id: "e1",
    name: "astrbot_file_edit_tool",
    args: { path: "F:\\proj\\a.py" },
    result: [
      "Edited F:\\proj\\a.py. Replaced 1 occurrence(s) of the target text.",
      "",
      "Diff:",
      "```diff",
      "@@ -1,3 +1,4 @@",
      " line1",
      "-old line",
      "+new line",
      "+another line",
      "```",
    ].join("\n"),
    finished_ts: 2,
  },
};

const RUNNING_EDIT_ENTRY: FileChangeEntry = {
  ...EDIT_ENTRY,
  callId: "e2",
  status: "running",
  diffStat: null,
  tool: { id: "e2", name: "astrbot_file_edit_tool", args: { path: "F:\\proj\\a.py" } },
};

function mountCard(entry: FileChangeEntry) {
  return mount(FileChangeCard, {
    props: { entry, isDark: false },
    global: { stubs: STUBS },
  });
}

describe("FileChangeCard", () => {
  beforeEach(() => {
    mocks.openLocalFile.mockReset();
    mocks.openLocalFolder.mockReset();
    mocks.toastError.mockReset();
    mocks.toastSuccess.mockReset();
    mocks.openLocalFile.mockResolvedValue({
      data: { status: "ok", message: null, data: {} },
    });
    mocks.openLocalFolder.mockResolvedValue({
      data: { status: "ok", message: null, data: {} },
    });
  });

  it("shows basename and line count for a write entry", () => {
    const wrapper = mountCard(WRITE_ENTRY);
    expect(wrapper.text()).toContain("b.txt");
    expect(wrapper.text()).toContain("3");
    // collapsed by default: no content visible
    expect(wrapper.text()).not.toContain("a\nb\nc");
  });

  it("reveals the written content after expanding", async () => {
    const wrapper = mountCard(WRITE_ENTRY);
    await wrapper.find(".file-change-card-header").trigger("click");
    expect(wrapper.text()).toContain("a\nb\nc");
  });

  it("shows the diff stat for an edit entry", () => {
    const wrapper = mountCard(EDIT_ENTRY);
    expect(wrapper.text()).toContain("+2");
    expect(wrapper.text()).toContain("−1");
  });

  it("renders DiffPreview with the parsed diff when expanded", async () => {
    const wrapper = mountCard(EDIT_ENTRY);
    await wrapper.find(".file-change-card-header").trigger("click");
    const diff = wrapper.findComponent(STUBS.DiffPreview);
    expect(diff.exists()).toBe(true);
    expect(diff.props("filePath")).toBe("F:\\proj\\a.py");
    expect(diff.props("content")).toContain("-old line");
  });

  it("shows a spinner instead of a diff while the call is running", async () => {
    const wrapper = mountCard(RUNNING_EDIT_ENTRY);
    await wrapper.find(".file-change-card-header").trigger("click");
    expect(wrapper.find(".progress-stub").exists()).toBe(true);
    expect(wrapper.findComponent(STUBS.DiffPreview).exists()).toBe(false);
  });

  it("tints the whole card by change kind (write=green, edit=yellow, remove=red)", () => {
    expect(mountCard(WRITE_ENTRY).classes()).toContain(
      "file-change-card--green",
    );
    expect(mountCard(EDIT_ENTRY).classes()).toContain(
      "file-change-card--yellow",
    );
    const removeEntry: FileChangeEntry = {
      callId: "r1",
      kind: "remove",
      filePath: "F:\\proj\\old.txt",
      status: "done",
      diffStat: null,
      lineCount: null,
      tool: {
        id: "r1",
        name: "astrbot_file_remove_tool",
        args: { path: "F:\\proj\\old.txt" },
        result: "File removed: F:\\proj\\old.txt",
        finished_ts: 2,
      },
    };
    expect(mountCard(removeEntry).classes()).toContain(
      "file-change-card--red",
    );
  });

  it("colors the +adds green and the −dels red", () => {
    const wrapper = mountCard(EDIT_ENTRY);
    const adds = wrapper.find(".file-change-stat .stat-adds");
    const dels = wrapper.find(".file-change-stat .stat-dels");
    expect(adds.exists()).toBe(true);
    expect(adds.text()).toBe("+2");
    expect(dels.exists()).toBe(true);
    expect(dels.text()).toBe("−1");
  });

  // ── 2026-08-14 open-on-disk ─────────────────────────────────────

  it("opens the file on disk via the API without expanding the card", async () => {
    const wrapper = mountCard(WRITE_ENTRY);
    const btn = wrapper.find(".file-change-open-btn");
    expect(btn.exists()).toBe(true);
    await btn.trigger("click");
    await flushPromises();
    expect(mocks.openLocalFile).toHaveBeenCalledWith("F:\\proj\\b.txt");
    expect(mocks.toastSuccess).toHaveBeenCalled();
    expect(mocks.toastError).not.toHaveBeenCalled();
    // the click must not toggle the collapse state
    expect(wrapper.find(".file-change-body").exists()).toBe(false);
  });

  it("hides the open-on-disk button for removals and running calls", () => {
    const removeEntry: FileChangeEntry = {
      callId: "r1",
      kind: "remove",
      filePath: "F:\\proj\\old.txt",
      status: "done",
      diffStat: null,
      lineCount: null,
      tool: {
        id: "r1",
        name: "astrbot_file_remove_tool",
        args: { path: "F:\\proj\\old.txt" },
        result: "File removed: F:\\proj\\old.txt",
        finished_ts: 2,
      },
    };
    const removeCard = mountCard(removeEntry);
    expect(
      removeCard
        .find(".file-change-open-btn:not(.file-change-folder-btn)")
        .exists(),
    ).toBe(false);
    // the folder survives the removal, so the folder button stays
    expect(removeCard.find(".file-change-folder-btn").exists()).toBe(true);
    expect(
      mountCard(RUNNING_EDIT_ENTRY).find(".file-change-open-btn").exists(),
    ).toBe(false);
    expect(
      mountCard(RUNNING_EDIT_ENTRY).find(".file-change-folder-btn").exists(),
    ).toBe(false);
  });

  it("surfaces backend errors as an error toast", async () => {
    mocks.openLocalFile.mockResolvedValue({
      data: { status: "error", message: "File not found: x" },
    });
    const wrapper = mountCard(EDIT_ENTRY);
    await wrapper.find(".file-change-open-btn").trigger("click");
    await flushPromises();
    expect(mocks.toastError).toHaveBeenCalled();
    expect(mocks.toastSuccess).not.toHaveBeenCalled();
  });

  // ── 2026-08-21 open-folder ───────────────────────────────────────

  it("opens the containing folder via the API without expanding the card", async () => {
    const wrapper = mountCard(WRITE_ENTRY);
    const btn = wrapper.find(".file-change-folder-btn");
    expect(btn.exists()).toBe(true);
    await btn.trigger("click");
    await flushPromises();
    expect(mocks.openLocalFolder).toHaveBeenCalledWith("F:\\proj\\b.txt");
    expect(mocks.openLocalFile).not.toHaveBeenCalled();
    expect(mocks.toastSuccess).toHaveBeenCalled();
    expect(mocks.toastError).not.toHaveBeenCalled();
    // the click must not toggle the collapse state
    expect(wrapper.find(".file-change-body").exists()).toBe(false);
  });

  it("surfaces folder-open backend errors as an error toast", async () => {
    mocks.openLocalFolder.mockResolvedValue({
      data: { status: "error", message: "Folder not found: x" },
    });
    const wrapper = mountCard(EDIT_ENTRY);
    await wrapper.find(".file-change-folder-btn").trigger("click");
    await flushPromises();
    expect(mocks.toastError).toHaveBeenCalled();
    expect(mocks.toastSuccess).not.toHaveBeenCalled();
  });

  // ── 2026-09-09 encoding marker ───────────────────────────────────

  it("keeps the encoding marker in the display name but strips it for open-on-disk", async () => {
    const wrapper = mountCard(WRITE_ENTRY_MARKED);
    expect(wrapper.find(".file-change-name").text()).toContain(
      "b.txt (encoding: utf-8)",
    );

    await wrapper.find(".file-change-open-btn").trigger("click");
    await flushPromises();
    expect(mocks.openLocalFile).toHaveBeenCalledWith("F:\\proj\\b.txt");
    expect(mocks.toastError).not.toHaveBeenCalled();
  });

  it("strips the encoding marker for open-folder too", async () => {
    const wrapper = mountCard(WRITE_ENTRY_MARKED);
    await wrapper.find(".file-change-folder-btn").trigger("click");
    await flushPromises();
    expect(mocks.openLocalFolder).toHaveBeenCalledWith("F:\\proj\\b.txt");
    expect(mocks.openLocalFile).not.toHaveBeenCalled();
  });
});
