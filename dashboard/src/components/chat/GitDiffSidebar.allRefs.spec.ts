// Author: elecvoid243 @ 2026-10-05
// Spec: docs/superpowers/specs/2026-10-05-git-log-graph-and-branch-tree-design.md §4.4 / §5
//
// 「所有分支」作用域必须把 activeBranch 留在哨兵值上：清成 null 会让
// viewingCurrent 判定为真，逐行操作门整组反转 —— 在别的分支的行上放出
// revert 与 reset --hard，同时把 cherry-pick 藏起来（与 spec §5 相反）。
//
// 沿用 GitDiffSidebar.logFocus.spec.ts 的「只 mock 传输层」策略：真 composable
// 打 mocked api，GitLogView 换成能读 prop / 回放事件的 stub。

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";
import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
}));

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { get: getMock, post: postMock },
}));

import { ALL_REFS_SENTINEL } from "@/composables/useSpcodeGitLog";
// status 单例由测试直接 seed(同 logFocus.spec):refresh() 在 session.umo
// 为空时会在发请求前返回,没有它本 spec 观察不到线上参数。
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import GitDiffSidebar from "./GitDiffSidebar.vue";

enableAutoUnmount(afterEach);

const FOCUS_COMMIT_KEY = "spcode:focusCommit";
const VIEW_MODE_STORAGE_KEY = "astrbot.spcode.gitDiffSidebar.viewMode";
const SHA = "a".repeat(40);

function okEnvelope(inner: unknown) {
  return { data: { status: "ok", data: inner } };
}

const FocusProbe = defineComponent({
  name: "FocusProbe",
  inject: { focusCommit: { from: FOCUS_COMMIT_KEY } },
  methods: {
    triggerFocus(sha: string) {
      (this as unknown as { focusCommit: (s: string) => void }).focusCommit(sha);
    },
  },
  render: () => h("div"),
});

const LogViewStub = defineComponent({
  name: "GitLogView",
  inheritAttrs: false,
  props: {
    state: { type: Object, default: null },
    focusedCommitSha: { type: String, default: null },
    activeBranch: { type: String, default: null },
  },
  emits: ["apply", "reset"],
  render: () => h("div"),
});

const STUBS = {
  FileBrowserView: FocusProbe,
  DocumentManager: true,
  GitDiffBodyContent: true,
  GitCommitBar: true,
  GitLogView: LogViewStub,
  GitStatsPanel: true,
  GitIgnoreEditor: true,
  GitConflictPanel: true,
  GitPullDialog: true,
  GitPushDialog: true,
  GitRemoteUrlDialog: true,
  GitStashDialog: true,
  GitMergeDialog: true,
  GitCherryPickDialog: true,
  GitSquashDialog: true,
  GitChangelogDialog: true,
  GitCommitDialog: true,
  GitCommitAmendDialog: true,
  WorktreeCreateDialog: true,
  LockReasonDialogBody: true,
  BranchSwitchConfirmDialog: true,
  BranchDeleteConfirmDialog: true,
};

beforeEach(() => {
  setActivePinia(createPinia());
  localStorage.removeItem(VIEW_MODE_STORAGE_KEY);
  getMock.mockReset();
  postMock.mockReset();
  useSpcodeProjectStatus().status.value = {
    loaded: true,
    directory: "F:/github/testproj",
    loadedAt: 1,
    umo: "session:test",
    allLoadedCount: 1,
    fetchedAt: 1,
    bootId: null,
  };
  getMock.mockImplementation(async (path: string) => {
    if (path === "spcode/git-worktrees") {
      return okEnvelope({
        loaded: true,
        directory: "F:/github/testproj",
        umo: "session:test",
        worktrees: [
          {
            path: "F:/github/testproj",
            head_sha: SHA,
            branch: "main",
            is_main: true,
            prunable: false,
            locked: null,
          },
        ],
        reason: null,
        stderr: "",
        elapsed_ms: 1,
      });
    }
    return okEnvelope({});
  });
});

async function mountHistorySidebar() {
  const w = mount(GitDiffSidebar, {
    props: { modelValue: true },
    global: { stubs: STUBS },
  });
  await flushPromises();

  // 深链是进入 history 视图的既有入口（同 logFocus.spec）。
  w.findComponent(FocusProbe).vm.triggerFocus(SHA);
  await flushPromises();

  const logView = w.findComponent(LogViewStub);
  expect(logView.exists(), "history 视图应挂载 GitLogView").toBe(true);
  return logView;
}

describe("GitDiffSidebar all-branches scope (2026-10-05)", () => {
  it("keeps the sentinel branch so the per-row action gates stay closed", async () => {
    const logView = await mountHistorySidebar();

    logView.vm.$emit("apply", { ref: ALL_REFS_SENTINEL, n: 20 });
    await flushPromises();

    // 哨兵 ≠ 当前分支 ⇒ viewingCurrent 为假 ⇒ revert / amend /
    // reset --hard 隐藏、cherry-pick 可见（spec §5）。
    expect(logView.props("activeBranch")).toBe(ALL_REFS_SENTINEL);
  });

  it("still drops ref from the wire in that scope", async () => {
    const logView = await mountHistorySidebar();

    logView.vm.$emit("apply", { ref: ALL_REFS_SENTINEL, n: 20 });
    await flushPromises();

    const logCalls = getMock.mock.calls.filter(
      ([path]) => path === "spcode/git-log",
    );
    expect(logCalls.length).toBeGreaterThan(0);
    const params = (
      logCalls.at(-1)?.[1] as { params: Record<string, unknown> }
    ).params;
    expect(params.all).toBe("true");
    expect(params.topo).toBe("true");
    expect(params.ref).toBeUndefined();
  });
});
