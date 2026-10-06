// Author: elecvoid243 @ 2026-10-06
// 功能:Git 变更页每文件「diff / 全文件叠加」模式切换。
// 设计(2026-10-06, bounded):overlay 复用 DiffPreview baseContent,
// 基准 = git-file?ref=HEAD&path 懒取;未取到 / 对齐失败自动回退纯 patch。

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";

vi.mock("@/composables/useSpcodeProjectStatus", () => ({
  useSpcodeProjectStatus: () => ({
    status: { value: { umo: "umo-test", directory: "D:/repo", loaded: true } },
    refresh: vi.fn(),
  }),
}));

const mockFetchRef = vi.fn();
const mockGetData = vi.fn();
vi.mock("@/composables/useSpcodeGitFile", () => ({
  useSpcodeGitFile: () => ({
    fetchRef: (...args: unknown[]) => mockFetchRef(...args),
    getData: (...args: unknown[]) => mockGetData(...args),
    getState: () => ({ kind: "idle" }),
    isLoading: () => false,
    invalidateAll: vi.fn(),
    dispose: vi.fn(),
  }),
}));

vi.mock("@/composables/useOpenOnDisk", () => ({
  useOpenOnDisk: () => ({
    opening: { value: false },
    openOnDisk: vi.fn(),
    openFolder: vi.fn(),
  }),
}));

import GitDiffFileItem from "./GitDiffFileItem.vue";

const SLICE = "@@ -1,2 +1,2 @@\n-old line\n+new line\n ctx\n";

function makeFile(overrides: Record<string, unknown> = {}) {
  return {
    path: "src/a.ts",
    status: "M" as const,
    slice: SLICE,
    additions: 1,
    deletions: 1,
    ...overrides,
  };
}

const stubs = {
  DiffPreview: {
    props: ["content", "baseContent", "filePath"],
    template:
      "<div class='dp' :data-content='content' :data-base='baseContent' />",
  },
  "v-icon": { template: "<i />" },
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mountItem(extra: Record<string, unknown> = {}): any {
  return mount(GitDiffFileItem as any, {
    props: {
      file: makeFile(),
      expanded: true,
      isDark: false,
      ...extra,
    },
    global: { stubs },
  });
}

describe("GitDiffFileItem overlay toggle", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    mockFetchRef.mockReset();
    mockGetData.mockReset();
  });

  it("expanded file with slice shows the mode toggle button", () => {
    const w = mountItem();
    expect(w.find('[data-testid="overlay-toggle"]').exists()).toBe(true);
  });

  it("toggle on → lazy fetch HEAD blob; DiffPreview gets baseContent once loaded", async () => {
    mockGetData.mockReturnValue({
      content: "old line\nctx\n",
      isBinary: false,
      ref: "HEAD",
      size: 12,
      truncated: false,
      maxBytes: 1024,
      resolvedSha: "a".repeat(40),
    });
    const w = mountItem();
    // 未切换:不传 baseContent
    expect(w.find(".dp").attributes("data-base")).toBeUndefined();
    await w.find('[data-testid="overlay-toggle"]').trigger("click");
    expect(mockFetchRef).toHaveBeenCalledWith("src/a.ts", "HEAD");
    await nextTick();
    expect(w.find(".dp").attributes("data-base")).toBe("old line\nctx\n");
    // 切回 diff → 不再传
    await w.find('[data-testid="overlay-toggle"]').trigger("click");
    await nextTick();
    expect(w.find(".dp").attributes("data-base")).toBeUndefined();
  });

  it("HEAD blob missing (new file) → overlay falls back to plain patch (no baseContent)", async () => {
    mockGetData.mockReturnValue(null);
    const w = mountItem();
    await w.find('[data-testid="overlay-toggle"]').trigger("click");
    await nextTick();
    expect(mockFetchRef).toHaveBeenCalledWith("src/a.ts", "HEAD");
    expect(w.find(".dp").attributes("data-base")).toBeUndefined();
  });
});
