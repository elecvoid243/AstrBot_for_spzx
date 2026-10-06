// Author: elecvoid243 @ 2026-10-06
// 功能:Git 历史页文件行「查看此版本」——打开该提交下的文件内容
// (现有「在磁盘中打开」保留,仍开当前最新版)。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";

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

import GitLogView from "@/components/chat/message_list_comps/GitLogView.vue";

const SHA = "a".repeat(40);
const FILE = {
  path: "src/foo.ts",
  status: "M",
  additions: 3,
  deletions: 1,
  oldPath: null,
  similarity: null,
};

function makeState() {
  return {
    kind: "ok" as const,
    snapshot: {
      success: true,
      reason: null,
      loaded: true,
      elapsedMs: 1,
      umo: "u",
      worktree: "w",
      directory: "d",
      ref: "HEAD",
      count: 1,
      hasMore: false,
      truncated: false,
      maxBytes: 1024,
      resolvedRef: "",
      commits: [
        {
          sha: SHA,
          shaShort: "aaaaaaa",
          author: { name: "n", email: "e" },
          committer: { name: "n", email: "e" },
          date: "2026-10-06T00:00:00+08:00",
          subject: "s",
          body: null,
          parents: [],
          shortstat: { files: 1, additions: 3, deletions: 1 },
          tags: [],
        },
      ],
    },
  };
}

const stubs = {
  GitStatsPanel: true,
  FilePatchPanel: true,
  VTooltip: { template: "<span><slot name='activator' :props='{}'/><slot/></span>" },
};

function baseProps(fileStatus: string = "M") {
  return {
    state: makeState() as never,
    hasMore: false,
    isLoading: false,
    gitShow: {
      fetch: () => {},
      fetchFile: () => {},
      getState: () => ({ kind: "ok", data: { files: [{ ...FILE, status: fileStatus }] } }),
      getData: () => ({ files: [{ ...FILE, status: fileStatus }] }),
      getFileState: () => ({ kind: "idle" }),
      getFileData: () => null,
    } as never,
    focusedCommitSha: null,
    gitStats: { state: { value: { kind: "idle" } }, refresh: () => {} } as never,
    statsOpen: false,
    range: { kind: "preset", preset: "1w" } as never,
    topFilesLimit: 10,
    branchItems: [],
    tagItems: [],
    currentBranch: "main",
    activeBranch: "HEAD",
    headSha: SHA,
  };
}

function mountLog(overrides: Record<string, unknown> = {}) {
  return mount(GitLogView, {
    props: { ...baseProps(), ...overrides } as never,
    global: { stubs },
  });
}

async function expandFirstCommit(w: ReturnType<typeof mountLog>) {
  const vm = w.vm as unknown as { toggleCommit: (sha: string) => void };
  vm.toggleCommit(SHA);
  await nextTick();
}

describe("GitLogView view-this-revision", () => {
  enableAutoUnmount(afterEach);
  beforeEach(() => {
    setActivePinia(createPinia());
    mockFetchRef.mockReset();
    mockGetData.mockReset();
  });

  it("file row has a view-revision button next to open-on-disk", async () => {
    const w = mountLog();
    await expandFirstCommit(w);
    expect(w.find('[data-testid="view-revision-btn"]').exists()).toBe(true);
  });

  it("click → lazy fetch blob at that sha and dialog opens with content", async () => {
    mockGetData.mockReturnValue({
      content: "const v = 1;\n",
      isBinary: false,
      ref: SHA,
      size: 12,
      truncated: false,
      maxBytes: 1024,
      resolvedSha: SHA,
    });
    const w = mountLog();
    await expandFirstCommit(w);
    await w.find('[data-testid="view-revision-btn"]').trigger("click");
    expect(mockFetchRef).toHaveBeenCalledWith("src/foo.ts", SHA);
    await nextTick();
    const dlg = w.find('[data-testid="view-revision-dialog"]');
    expect(dlg.exists()).toBe(true);
    expect(dlg.text()).toContain("src/foo.ts");
    expect(dlg.text()).toContain("aaaaaaa");
    expect(dlg.text()).toContain("const v = 1;");
  });

  it("deleted file row hides the view-revision button", async () => {
    const w = mountLog({
      gitShow: {
        fetch: () => {},
        fetchFile: () => {},
        getState: () => ({ kind: "ok", data: { files: [{ ...FILE, status: "D" }] } }),
        getData: () => ({ files: [{ ...FILE, status: "D" }] }),
        getFileState: () => ({ kind: "idle" }),
        getFileData: () => null,
      } as never,
    });
    await expandFirstCommit(w);
    expect(w.find('[data-testid="view-revision-btn"]').exists()).toBe(false);
  });
});
