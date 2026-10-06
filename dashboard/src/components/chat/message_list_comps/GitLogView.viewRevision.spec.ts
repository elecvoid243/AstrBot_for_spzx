// Author: elecvoid243 @ 2026-10-06
// 功能:Git 历史页文件行「导出此版本并打开」——把该提交下的 blob 导出到
// 临时文件(后端 git-file-export),再用系统默认应用打开;
// 「在磁盘中打开」保留,仍开仓库里的最新版。
// 2026-10-06 修订:不再应用内查看(dialog 已移除)。

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";

import { chatApi, pluginExtensionApi } from "@/api/v1";
import GitLogView from "@/components/chat/message_list_comps/GitLogView.vue";

const SHA = "a".repeat(40);
const ABS_PATH = "G:/temp/git-history/aaaaaaa/src/foo.ts";
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

function mountLog(fileStatus: string = "M") {
  return mount(GitLogView, {
    props: { ...baseProps(fileStatus) } as never,
    global: { stubs },
  });
}

async function expandFirstCommit(w: ReturnType<typeof mountLog>) {
  const vm = w.vm as unknown as { toggleCommit: (sha: string) => void };
  vm.toggleCommit(SHA);
  await nextTick();
}

describe("GitLogView export-this-revision", () => {
  enableAutoUnmount(afterEach);
  /* eslint-disable @typescript-eslint/no-explicit-any */
  let postSpy: any;
  let openSpy: any;
  /* eslint-enable @typescript-eslint/no-explicit-any */

  beforeEach(() => {
    setActivePinia(createPinia());
    postSpy = vi
      .spyOn(pluginExtensionApi, "post")
      .mockResolvedValue({
        data: {
          status: "ok",
          data: { success: true, exported: true, abs_path: ABS_PATH },
        },
      } as never);
    openSpy = vi
      .spyOn(chatApi, "openLocalFile")
      .mockResolvedValue({ data: { status: "ok" } } as never);
  });

  it("file row has an export-revision button (history icon)", async () => {
    const w = mountLog();
    await expandFirstCommit(w);
    const btn = w.find('[data-testid="view-revision-btn"]');
    expect(btn.exists()).toBe(true);
    // 不再是应用内查看的"眼睛"图标
    expect(btn.text()).not.toContain("mdi-eye-outline");
  });

  it("click → POST git-file-export then open the returned abs path on disk", async () => {
    const w = mountLog();
    await expandFirstCommit(w);
    await w.find('[data-testid="view-revision-btn"]').trigger("click");
    await nextTick();
    expect(postSpy).toHaveBeenCalledWith(
      "spcode/git-file-export",
      expect.objectContaining({ path: "src/foo.ts", ref: SHA }),
    );
    expect(openSpy).toHaveBeenCalledWith(ABS_PATH);
  });

  it("export failure → does NOT open anything", async () => {
    postSpy.mockResolvedValue({
      data: {
        status: "ok",
        data: { success: false, reason: "ref_not_found" },
      },
    } as never);
    const w = mountLog();
    await expandFirstCommit(w);
    await w.find('[data-testid="view-revision-btn"]').trigger("click");
    await nextTick();
    expect(openSpy).not.toHaveBeenCalled();
  });

  it("deleted file row hides the export button", async () => {
    const w = mountLog("D");
    await expandFirstCommit(w);
    expect(w.find('[data-testid="view-revision-btn"]').exists()).toBe(false);
  });
});
