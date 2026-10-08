// Author: elecvoid243 @ 2026-10-06
// 功能:Git 变更页每文件「diff / 全文件叠加」模式切换。
// 设计(2026-10-06, bounded):overlay 复用 DiffPreview baseContent,
// 基准 = git-file?ref=HEAD&path 懒取;未取到 / 对齐失败自动回退纯 patch。
// 2026-10-08:切换器从「本组件的独立行按钮」改为 DiffPreview 模式组第三格,
// 本组件只保留状态机(overlayMode + 基准就绪/在途/不可用),通过
// v-model:overlayMode 与两个只读 prop 交接。

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
// 显式标注返回类型:`reason` 只在 error 态出现,推断成 { kind: string } 会让
// error 用例无法构造。
const mockGetState = vi.fn((): { kind: string; reason?: string } => ({
  kind: "idle",
}));
vi.mock("@/composables/useSpcodeGitFile", () => ({
  useSpcodeGitFile: () => ({
    fetchRef: (...args: unknown[]) => mockFetchRef(...args),
    getData: (...args: unknown[]) => mockGetData(...args),
    getState: () => mockGetState(),
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

import { useModuleI18n } from "@/i18n/composables";

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

/** git-file 快照:只保留 overlay 状态机需要读的字段。 */
function makeBlob(content: string) {
  return {
    content,
    isBinary: false,
    ref: "HEAD",
    size: content.length,
    truncated: false,
    maxBytes: 1024,
    resolvedSha: "a".repeat(40),
  };
}

const stubs = {
  DiffPreview: {
    // name 让 findComponent({ name }) 能拿到桩实例以驱动 v-model 事件。
    name: "DiffPreview",
    props: [
      "content",
      "baseContent",
      "filePath",
      "overlayMode",
      "overlayLoading",
      "overlayUnavailable",
    ],
    template:
      "<div class='dp' :data-content='content' :data-base='baseContent' " +
      ":data-overlay-mode='String(overlayMode)' " +
      ":data-overlay-loading='String(overlayLoading)' " +
      ":data-overlay-unavailable='overlayUnavailable' />",
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

describe("GitDiffFileItem overlay mode state machine", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    mockFetchRef.mockReset();
    mockGetData.mockReset();
    mockGetState.mockReset();
    mockGetState.mockReturnValue({ kind: "idle" });
  });

  it("no longer renders a toggle row of its own", () => {
    // 这一行是本次改动的全部理由:切换器搬进 DiffPreview 的模式组。
    const w = mountItem();
    expect(w.find('[data-testid="overlay-toggle"]').exists()).toBe(false);
    expect(w.find(".git-diff-file-item__overlay-toggle").exists()).toBe(false);
  });

  it("hands the requested mode down and lazily fetches the HEAD blob", async () => {
    mockGetData.mockReturnValue(makeBlob("old line\nctx\n"));
    const w = mountItem();
    expect(w.find(".dp").attributes("data-overlay-mode")).toBe("false");

    // 第三格的事件走 v-model:overlayMode —— 模式仍由本组件持有。
    w.findComponent({ name: "DiffPreview" }).vm.$emit("update:overlayMode", true);
    await nextTick();

    expect(mockFetchRef).toHaveBeenCalledWith("src/a.ts", "HEAD");
    expect(w.find(".dp").attributes("data-overlay-mode")).toBe("true");
    expect(w.find(".dp").attributes("data-base")).toBe("old line\nctx\n");

    w.findComponent({ name: "DiffPreview" }).vm.$emit("update:overlayMode", false);
    await nextTick();
    expect(w.find(".dp").attributes("data-overlay-mode")).toBe("false");
    expect(w.find(".dp").attributes("data-base")).toBeUndefined();
  });

  it("reports the in-flight base fetch only while the mode is requested", async () => {
    mockGetState.mockReturnValue({ kind: "loading" });
    const w = mountItem();
    // 后台预取不该让第三格转圈:未请求时 loading 保持 false。
    expect(w.find(".dp").attributes("data-overlay-loading")).toBe("false");

    w.findComponent({ name: "DiffPreview" }).vm.$emit("update:overlayMode", true);
    await nextTick();
    expect(w.find(".dp").attributes("data-overlay-loading")).toBe("true");
  });

  it("disables the slot for a file that has no HEAD version, without fetching", () => {
    const { tm } = useModuleI18n("features/chat");
    const w = mountItem({ isNewFile: true });

    expect(w.find(".dp").attributes("data-overlay-unavailable")).toBe(
      tm("spcodeProjectLoad.diffSidebar.overlay.disabledNoBase"),
    );
    expect(mockFetchRef).not.toHaveBeenCalled();
  });

  it("explains a failed base read instead of silently doing nothing", async () => {
    const { tm } = useModuleI18n("features/chat");
    mockGetState.mockReturnValue({ kind: "error", reason: "not_found" });
    const w = mountItem();

    w.findComponent({ name: "DiffPreview" }).vm.$emit("update:overlayMode", true);
    await nextTick();

    expect(w.find(".dp").attributes("data-overlay-unavailable")).toBe(
      tm("spcodeProjectLoad.diffSidebar.overlay.disabledError"),
    );
  });

  it("treats a truncated base blob as unusable", async () => {
    const { tm } = useModuleI18n("features/chat");
    mockGetState.mockReturnValue({ kind: "ok" });
    mockGetData.mockReturnValue({ ...makeBlob("head\n"), truncated: true });
    const w = mountItem();

    w.findComponent({ name: "DiffPreview" }).vm.$emit("update:overlayMode", true);
    await nextTick();

    expect(w.find(".dp").attributes("data-overlay-unavailable")).toBe(
      tm("spcodeProjectLoad.diffSidebar.overlay.disabledTruncated"),
    );
  });
});
