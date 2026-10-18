// Tests for FilePatchPanel history-overlay wiring (2026-10-18).
//
// The Git history panel gets the parent-side blob from
// /spcode/git-show?path= and hands it to DiffPreview as `base-content`.
// DiffPreview owns the mode group, so the panel only holds the requested
// mode and maps the backend base flags to a disabled-slot reason.

import { describe, it, expect, beforeEach } from "vitest";
import { mount, type VueWrapper } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import FilePatchPanel from "./FilePatchPanel.vue";
import zh from "@/i18n/locales/zh-CN/features/chat.json";
import type { GitShowFileView } from "@/composables/parseSpcodeGitShow";

const SHA_A = "a".repeat(40);
const SHA_B = "b".repeat(40);

const BASE_VIEW: GitShowFileView = {
  path: "src/a.ts",
  status: "M",
  additions: 1,
  deletions: 1,
  oldPath: null,
  isBinary: false,
  patch: "@@ -1,2 +1,2 @@\n-old\n+new\n ctx\n",
  baseContent: "old\nctx\n",
  baseSize: 8,
  baseBinary: false,
  baseTruncated: false,
};

const DiffPreviewStub = {
  name: "DiffPreview",
  props: [
    "content",
    "filePath",
    "baseContent",
    "overlayMode",
    "overlaySupported",
    "overlayUnavailable",
    "maxLines",
    "maxChars",
    "collapsible",
    "isDark",
  ],
  template:
    "<div class='dp' :data-base='baseContent' " +
    ":data-supported='String(overlaySupported)' " +
    ":data-reason='overlayUnavailable' />",
};

const STUBS = {
  DiffPreview: DiffPreviewStub,
  "v-icon": { template: "<i />" },
  "v-progress-circular": { template: "<span />" },
};

function mountPanel(
  view: GitShowFileView = BASE_VIEW,
  sha: string = SHA_A,
): VueWrapper {
  return mount(FilePatchPanel, {
    props: {
      sha,
      path: view.path,
      state: { kind: "ok" as const, data: view, notModified: false },
      data: view,
      isDark: false,
    },
    global: { stubs: STUBS },
  }) as VueWrapper;
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("FilePatchPanel history overlay", () => {
  it("passes the parent-side base and lets DiffPreview drive the mode", async () => {
    const wrapper = mountPanel();
    const dp = wrapper.findComponent({ name: "DiffPreview" });

    expect(dp.props("baseContent")).toBe("old\nctx\n");
    expect(dp.props("overlaySupported")).toBe(true);
    expect(dp.props("overlayMode")).toBe(false);

    dp.vm.$emit("update:overlayMode", true);
    await nextTick();

    expect(dp.props("overlayMode")).toBe(true);
  });

  it("keeps the slot disabled when the commit has no usable parent blob", () => {
    const wrapper = mountPanel({
      ...BASE_VIEW,
      status: "A",
      patch: "@@ -0,0 +1 @@\n+new\n",
      baseContent: "",
      baseSize: 0,
    });
    const dp = wrapper.findComponent({ name: "DiffPreview" });

    expect(dp.props("overlaySupported")).toBe(false);
    expect(dp.props("overlayUnavailable")).toBe("");
  });

  it("explains a truncated parent blob", () => {
    const wrapper = mountPanel({
      ...BASE_VIEW,
      baseContent: "",
      baseSize: 2048,
      baseTruncated: true,
    });
    const dp = wrapper.findComponent({ name: "DiffPreview" });

    expect(dp.props("overlaySupported")).toBe(false);
    expect(dp.props("overlayUnavailable")).toBe(
      zh.spcodeProjectLoad.diffSidebar.overlay.disabledTruncated,
    );
  });

  it("explains a binary parent blob", () => {
    const wrapper = mountPanel({
      ...BASE_VIEW,
      baseContent: "",
      baseSize: 2048,
      baseBinary: true,
    });
    const dp = wrapper.findComponent({ name: "DiffPreview" });

    expect(dp.props("overlaySupported")).toBe(false);
    expect(dp.props("overlayUnavailable")).toBe(
      zh.spcodeProjectLoad.diffSidebar.overlay.disabledBinary,
    );
  });

  it("disables overlay for a pure rename (base exists, no hunk)", () => {
    const wrapper = mountPanel({
      ...BASE_VIEW,
      status: "R",
      oldPath: "src/old.ts",
      patch: "diff --git a/src/old.ts b/src/a.ts\nsimilarity index 100%\nrename from src/old.ts\nrename to src/a.ts\n",
    });
    const dp = wrapper.findComponent({ name: "DiffPreview" });

    expect(dp.props("baseContent")).toBe("old\nctx\n");
    expect(dp.props("overlaySupported")).toBe(false);
    expect(dp.props("overlayUnavailable")).toBe(
      zh.spcodeProjectLoad.diffSidebar.overlay.disabledNoHunk,
    );
  });

  it("resets the mode when the panel switches commit", async () => {
    const wrapper = mountPanel();
    const dp = wrapper.findComponent({ name: "DiffPreview" });
    dp.vm.$emit("update:overlayMode", true);
    await nextTick();
    expect(dp.props("overlayMode")).toBe(true);

    await wrapper.setProps({
      sha: SHA_B,
      state: { kind: "ok" as const, data: BASE_VIEW, notModified: false },
    });
    await nextTick();

    expect(dp.props("overlayMode")).toBe(false);
  });
});
