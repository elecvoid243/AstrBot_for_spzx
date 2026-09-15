// Integration tests for the GitRepoInitPrompt slot inside
// GitDiffSidebar: prompt rendering (probe → not_a_git_repo) and the
// confirm → POST spcode/git-init wiring, including the failure path
// (the prompt must stay mounted and surface the error).
//
// Only the transport is mocked — the real composables run against the
// mocked pluginExtensionApi, so the probe composable, the sidebar
// handler, and the prompt component are all exercised end-to-end.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

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
// 2026-09-15 (elecvoid243): hoisted out of mountSidebar(). The
// `await import()` used to compile this ~300KB SFC inside whichever test
// ran first, billing that work to the test's 5s timeout (measured 3.8s,
// so it tripped intermittently under full-suite load and failed the whole
// file with "Test timed out"). A static import moves the compile into the
// collection phase, outside every test's timeout budget.
import GitDiffSidebar from "./GitDiffSidebar.vue";

// Axios response carrying the plugin envelope for
// GET /spcode/git-repo-check on a non-git directory.
const GIT_REPO_CHECK_NOT_A_REPO = {
  data: {
    status: "ok",
    data: {
      is_git_repo: false,
      git_available: true,
      directory: "F:/github/testproj",
      reason: "not_a_git_repo",
      stderr: "fatal: not a git repository",
      elapsed_ms: 15.67,
    },
  },
};

// Axios response for a successful POST /spcode/git-init.
const GIT_INIT_OK = {
  data: {
    status: "ok",
    data: {
      reason: null,
      stderr: "",
      elapsed_ms: 10,
      initialized: true,
      path: "F:/github/testproj",
      initial_branch: "main",
      bare: false,
      force: true,
      git_dir: "F:/github/testproj/.git",
      umo: "session:test",
      worktree: "",
    },
  },
};

function okEnvelope(inner: unknown) {
  return { data: { status: "ok", data: inner } };
}

const STUBS = {
  FileBrowserView: true,
  DocumentManager: true,
  GitDiffBodyContent: true,
  GitCommitBar: true,
  GitLogView: true,
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

async function mountSidebar() {
  const w = mount(GitDiffSidebar, {
    props: { modelValue: true },
    global: { stubs: STUBS },
  });
  await flushPromises();
  return w;
}

describe("GitDiffSidebar repo-init prompt", () => {
  beforeEach(() => {
    // GitDiffSidebar 的 useOpenOnDisk → useToast 需要 active pinia。
    setActivePinia(createPinia());
    getMock.mockReset();
    postMock.mockReset();
    getMock.mockImplementation(async (path: string) => {
      if (path === "spcode/git-repo-check") {
        return GIT_REPO_CHECK_NOT_A_REPO;
      }
      if (path === "spcode/git-worktrees") {
        return okEnvelope({ worktrees: [] });
      }
      // Generic empty envelope for every other probe the sidebar fires.
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

  it("clicking confirm posts to spcode/git-init with force=true", async () => {
    postMock.mockResolvedValue(GIT_INIT_OK);
    const w = await mountSidebar();

    const confirmBtn = w.find('[data-testid="repo-init-confirm"]');
    expect(confirmBtn.exists(), "prompt confirm button should be rendered").toBe(
      true,
    );
    await confirmBtn.trigger("click");
    await flushPromises();

    expect(postMock).toHaveBeenCalledTimes(1);
    const [path, body] = postMock.mock.calls[0];
    expect(path).toBe("spcode/git-init");
    expect(body).toEqual({
      path: "F:/github/testproj",
      initial_branch: "main",
      bare: false,
      force: true,
    });
    w.unmount();
  });

  it("confirm survives an empty projectRoot by falling back to the probe directory", async () => {
    postMock.mockResolvedValue(GIT_INIT_OK);
    const w = await mountSidebar();
    expect(w.find('[data-testid="repo-init-confirm"]').exists()).toBe(true);

    // Simulate the status ref being refreshed to an empty directory AFTER
    // the probe already ran (the prompt keeps its own directory snapshot).
    const spcodeStatus = useSpcodeProjectStatus();
    spcodeStatus.status.value = {
      ...spcodeStatus.status.value,
      directory: null,
    };
    await flushPromises();
    expect(w.find('[data-testid="repo-init-confirm"]').exists()).toBe(true);

    await w.find('[data-testid="repo-init-confirm"]').trigger("click");
    await flushPromises();

    // The POST must still fire (path taken from the probe state's
    // directory snapshot), not silently no-op.
    expect(postMock).toHaveBeenCalledTimes(1);
    const [, body] = postMock.mock.calls[0];
    expect((body as { path: string }).path).toBe("F:/github/testproj");
    w.unmount();
  });

  it("keeps the prompt mounted with the error visible when the init request fails", async () => {
    postMock.mockRejectedValue(new Error("Network Error"));
    const w = await mountSidebar();

    const confirmBtn = w.find('[data-testid="repo-init-confirm"]');
    expect(confirmBtn.exists()).toBe(true);
    await confirmBtn.trigger("click");
    await flushPromises();

    // Regression: a rejected POST used to leave the probe state stuck on
    // "loading", which unmounted the prompt and hid the failure.
    expect(
      w.find('[data-testid="repo-init-confirm"]').exists(),
      "prompt must stay mounted after a failed init",
    ).toBe(true);
    expect(w.find('[data-testid="repo-init-error"]').exists()).toBe(true);
    w.unmount();
  });
});
