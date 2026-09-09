// 2026-09-08 final-fix 回归测试：深链聚焦（focusCommit）在用户手动
// Apply / Reset 之后必须被清空。
//
// 背景：focusCommit 把 SHA 存进 focusedCommitSha；GitLogView 的
// effectiveFocusSha = props.focusedCommitSha ?? hashFocusSha，深链聚焦
// 优先级更高。若 Apply / Reset 后不清空，用户再用 hash 搜索时
// hashFocusSha 被压制，命中的行静默不高亮。
//
// 沿用 GitDiffSidebar.repoInit.spec.ts 的「只 mock 传输层」策略：
// files 是默认视图，用一个注入 spcode:focusCommit 的 FileBrowserView
// stub 触发真实的 focusCommit；再用自定义 GitLogView stub 读取
// focused-commit-sha prop 并回放 apply / reset 事件。

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";
import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";

const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
}));

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { get: getMock, post: postMock },
}));

// Real composables run against the mocked api — only the transport is
// faked. The status singleton is seeded directly by the test.
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";

const FOCUS_COMMIT_KEY = "spcode:focusCommit";
const VIEW_MODE_STORAGE_KEY = "astrbot.spcode.gitDiffSidebar.viewMode";
const SHA = "a".repeat(40);

function okEnvelope(inner: unknown) {
  return { data: { status: "ok", data: inner } };
}

/** files 视图默认渲染，用它注入并提供真实的 focusCommit 触发器。 */
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

/** 只声明断言 / 回放需要的 prop；其余透传属性一律丢弃。 */
const LogViewStub = defineComponent({
  name: "GitLogView",
  inheritAttrs: false,
  props: {
    focusedCommitSha: { type: String, default: null },
    state: { type: Object, default: null },
    // 2026-09-09 head-sha: 断言 sidebar 把 worktree 的 head_sha 透传下去。
    headSha: { type: String, default: null },
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

async function mountFocusedSidebar() {
  const { default: GitDiffSidebar } = await import("./GitDiffSidebar.vue");
  const w = mount(GitDiffSidebar, {
    props: { modelValue: true },
    global: { stubs: STUBS },
  });
  await flushPromises();

  // 触发深链（等价于文件预览面板点击「查看该文件历史」）。
  w.findComponent(FocusProbe).vm.triggerFocus(SHA);
  await flushPromises();

  const logView = w.findComponent(LogViewStub);
  expect(
    logView.exists(),
    "focusCommit 应切到 history 视图并挂载 GitLogView",
  ).toBe(true);
  expect(
    logView.props("focusedCommitSha"),
    "深链聚焦应先被点亮",
  ).toBe(SHA);
  return logView;
}

beforeEach(() => {
  localStorage.removeItem(VIEW_MODE_STORAGE_KEY);
  getMock.mockReset();
  postMock.mockReset();
  // 2026-09-09 head-sha: worktrees 端点返回一个带 head_sha 的主工作树，
  // 供「headSha 透传」用例断言；其余端点回空信封。
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
  const spcodeStatus = useSpcodeProjectStatus();
  spcodeStatus.status.value = {
    loaded: true,
    directory: "F:/github/testproj",
    loadedAt: 1,
    umo: "session:test",
    allLoadedCount: 1,
    fetchedAt: 1,
    bootId: null,
  };
});

enableAutoUnmount(afterEach);

describe("GitDiffSidebar deep-link focus clearing", () => {
  it("clears focusedCommitSha when a filter is applied", async () => {
    const logView = await mountFocusedSidebar();

    logView.vm.$emit("apply", { ref: "HEAD", n: 20 });
    await flushPromises();

    // 回归点：Apply 之后深链聚焦必须让位，否则它会在
    // GitLogView.effectiveFocusSha 里压过 hash 搜索的 resolvedRef。
    expect(logView.props("focusedCommitSha")).toBeNull();
  });

  it("clears focusedCommitSha when the filter is reset", async () => {
    const logView = await mountFocusedSidebar();

    logView.vm.$emit("reset", { ref: "HEAD", n: 20 });
    await flushPromises();

    expect(logView.props("focusedCommitSha")).toBeNull();
  });

  // 2026-09-09 head-sha: amend / reset / squash 的可见性依赖真实 HEAD，
  // sidebar 必须把当前工作树的 head_sha（来自 git worktree list）透传下去。
  it("passes the worktree HEAD sha down to GitLogView", async () => {
    const logView = await mountFocusedSidebar();
    expect(logView.props("headSha")).toBe(SHA);
  });
});
