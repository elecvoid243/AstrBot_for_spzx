// Tests for ToolCallCard's apply_patch result rendering (2026-10-18):
// one DiffPreview per file so every diff block carries its own path.

import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import ToolCallCard from "./ToolCallCard.vue";

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({
    tm: (key: string, params?: Record<string, unknown>) =>
      params?.count !== undefined ? `${key}:${params.count}` : key,
  }),
}));

const STUBS = {
  DiffPreview: {
    name: "DiffPreview",
    props: ["content", "filePath", "summary", "isDark", "maxLines", "collapsible"],
    template: "<div class='diff-stub' />",
  },
  CopyableText: {
    name: "CopyableText",
    props: ["value", "displayValue", "mode", "showIcon"],
    template: "<span class='copy-stub' />",
  },
  ToolResultView: {
    name: "ToolResultView",
    props: ["toolName", "result", "toolArgs"],
    template: "<pre class='raw-stub'>{{ result }}</pre>",
  },
  "v-icon": { template: "<i />" },
};

const PATCH_RESULT = [
  "Applied patch: 3 files changed (+3 -2)",
  "UPDATE C:\\w\\a.txt",
  "UPDATE C:\\w\\b.txt -> C:\\w\\c.txt",
  "DELETE C:\\w\\d.txt",
  "",
  "--- C:\\w\\a.txt",
  "+++ C:\\w\\a.txt",
  "@@ -1 +1 @@",
  "-a",
  "+A",
  "--- C:\\w\\b.txt",
  "+++ C:\\w\\b.txt",
  "@@ -1 +1 @@",
  "-b",
  "+B",
].join("\n");

function mountCard(result: string) {
  return mount(ToolCallCard, {
    props: {
      toolCall: {
        id: "p1",
        name: "astrbot_apply_patch",
        args: { patch: "*** Update File: C:\\w\\a.txt\n-a\n+A\n" },
        result,
        finished_ts: 1,
      },
      isDark: false,
      initialExpanded: true,
    },
    global: { stubs: STUBS },
  });
}

describe("ToolCallCard apply_patch result", () => {
  it("renders one DiffPreview per file with its path", () => {
    const wrapper = mountCard(PATCH_RESULT);
    const diffs = wrapper.findAllComponents(STUBS.DiffPreview);
    expect(diffs).toHaveLength(2);
    expect(diffs[0].props("filePath")).toBe("C:\\w\\a.txt");
    expect(diffs[0].props("content")).toContain("-a");
    expect(diffs[1].props("filePath")).toBe("C:\\w\\b.txt -> C:\\w\\c.txt");
    expect(diffs[1].props("content")).toContain("-b");
    expect(wrapper.findComponent(STUBS.ToolResultView).exists()).toBe(false);
  });

  it("shows a path row for a file without a diff", () => {
    const wrapper = mountCard(PATCH_RESULT);
    const rows = wrapper.findAll(".apply-patch-file-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].text()).toContain("C:\\w\\d.txt");
  });

  it("keeps plain text rendering for dry-run results", () => {
    const wrapper = mountCard(
      "Dry run: no files were written. 1 files, +1 -1\nUPDATE C:\\w\\a.txt\n",
    );
    expect(wrapper.findAllComponents(STUBS.DiffPreview)).toHaveLength(0);
    expect(wrapper.findComponent(STUBS.ToolResultView).exists()).toBe(true);
  });
});
