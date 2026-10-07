// Author: elecvoid243 @ 2026-10-07
//
// End-to-end regression for "当前分支显示为 detached HEAD，必须手点刷新".
//
// Scenario (the normal dashboard lifecycle, not an edge case): the sidebar
// is mounted with the page and can stay open while the project is still
// being loaded. Its first git-branches request therefore races the project
// registration and gets `{ success: false, reason: "no_project_loaded" }`
// — before the fix that envelope was parsed as an "ok but empty" snapshot,
// so the branch button rendered the i18n fallback "detached HEAD" (the
// repo was on `main` all along), and nothing ever refetched: the umo never
// changed and the 30s branch poller was never started.
//
// Only the transport is mocked; the real composables and the real sidebar
// run. i18n is initialised by vitest.setup.ts, so the label assertions
// compare real localized strings.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, type Slot } from "vue";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
}));

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { get: getMock, post: postMock },
}));

import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import GitDiffSidebar from "./GitDiffSidebar.vue";

const UMO = "session:test";
const DIR = "F:/github/testproj";

// Vuetify is not installed in the test env (see vitest.setup.ts), so the
// branch menu — and with it the button inside its #activator slot — would
// never render. Mirror the setup file's approach: register a minimal stub
// that renders both slots, which is all the label assertion needs.
const VMenuStub = defineComponent({
  name: "VMenu",
  setup(_props, { slots }: { slots: { activator?: Slot; default?: Slot } }) {
    return () =>
      h("div", { class: "v-menu" }, [
        slots.activator?.({ props: {} }),
        slots.default?.(),
      ]);
  },
});

function okEnvelope(inner: unknown) {
  return { data: { status: "ok", data: inner } };
}

const WORKTREES_OK = okEnvelope({
  loaded: true,
  directory: DIR,
  umo: UMO,
  worktrees: [
    {
      path: DIR,
      head_sha: "abc1234",
      branch: "main",
      is_main: true,
      prunable: false,
      locked: null,
    },
  ],
  active_worktree: null,
  reason: null,
  stderr: "",
  elapsed_ms: 4,
});

const BRANCHES_OK = okEnvelope({
  success: true,
  branches: [
    {
      name: "main",
      sha: "abc1234",
      upstream: "",
      upstream_track: "",
      current: true,
      remote: false,
    },
    {
      name: "feat/x",
      sha: "beef789",
      upstream: "",
      upstream_track: "",
      current: false,
      remote: false,
    },
  ],
  total: 2,
  current: "main",
  detached: false,
  remotes: [],
  tags: [],
  reason: null,
  stderr: "",
  elapsed_ms: 5,
});

/** The "project not registered yet" answer — owns no `branches` key. */
const BRANCHES_NOT_READY = okEnvelope({
  success: false,
  reason: "no_project_loaded",
  stderr: "",
  elapsed_ms: 1,
});

/** A genuinely detached worktree (backend reported detached: true). */
const BRANCHES_DETACHED = okEnvelope({
  success: true,
  branches: [
    {
      name: "main",
      sha: "abc1234",
      upstream: "",
      upstream_track: "",
      current: false,
      remote: false,
    },
  ],
  total: 1,
  current: null,
  detached: true,
  remotes: [],
  tags: [],
  reason: null,
  stderr: "",
  elapsed_ms: 5,
});

function gitRepoCheck(isRepo: boolean) {
  return okEnvelope({
    is_git_repo: isRepo,
    git_available: true,
    directory: DIR,
    reason: isRepo ? null : "not_a_git_repo",
    stderr: "",
    elapsed_ms: 1,
  });
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

const BRANCH_ROW = ".git-diff-sidebar-branch-mgmt";
const BRANCH_LABEL = ".git-diff-sidebar-branch-mgmt-btn-name";

function waitMs(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function seedStatus(loaded: boolean, directory: string | null): void {
  const spcodeStatus = useSpcodeProjectStatus();
  spcodeStatus.status.value = {
    loaded,
    directory,
    loadedAt: loaded ? 1 : null,
    umo: UMO,
    allLoadedCount: loaded ? 1 : 0,
    fetchedAt: Date.now(),
    bootId: null,
  };
}

const wrappers: Array<{ unmount: () => void }> = [];

function mountSidebar() {
  const w = mount(GitDiffSidebar, {
    props: { modelValue: true },
    global: {
      stubs: STUBS,
      components: { "v-menu": VMenuStub },
    },
  });
  wrappers.push(w);
  return w;
}

describe("GitDiffSidebar — branch label on project load", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    getMock.mockReset();
    postMock.mockReset();
  });

  afterEach(() => {
    for (const w of wrappers.splice(0)) w.unmount();
  });

  it("picks up the branch name on its own when the project finishes loading", async () => {
    let probeIsRepo = false;
    let branchCalls = 0;
    getMock.mockImplementation(async (path: string) => {
      if (path === "spcode/git-repo-check") return gitRepoCheck(probeIsRepo);
      if (path === "spcode/git-worktrees") return WORKTREES_OK;
      if (path === "spcode/git-branches") {
        branchCalls += 1;
        // First flight races the project load and loses.
        return branchCalls === 1 ? BRANCHES_NOT_READY : BRANCHES_OK;
      }
      return okEnvelope({});
    });

    // Sidebar opened BEFORE the project is loaded (directory null).
    seedStatus(false, null);
    const w = mountSidebar();
    await flushPromises();
    await waitMs(600); // let the mount-time branches fetch land
    await flushPromises();

    // No repo yet → the branch row is gated off entirely.
    expect(w.find(BRANCH_ROW).exists()).toBe(false);
    expect(branchCalls).toBeGreaterThanOrEqual(1);

    // Project load finishes: `directory` flips, `umo` stays the same.
    probeIsRepo = true;
    seedStatus(true, DIR);
    await flushPromises();
    await waitMs(600); // refreshDelayed window + probe re-run
    await flushPromises();

    const label = w.find(BRANCH_LABEL);
    expect(label.exists()).toBe(true);
    expect(label.text()).toBe("main");
    // …and it took an automatic refetch, not a manual one.
    expect(branchCalls).toBeGreaterThanOrEqual(2);
  });

  it("keeps a real detached HEAD distinguishable from 'no data yet'", async () => {
    getMock.mockImplementation(async (path: string) => {
      if (path === "spcode/git-repo-check") return gitRepoCheck(true);
      if (path === "spcode/git-worktrees") return WORKTREES_OK;
      if (path === "spcode/git-branches") return BRANCHES_DETACHED;
      return okEnvelope({});
    });

    seedStatus(true, DIR);
    const w = mountSidebar();
    await flushPromises();
    await waitMs(600);
    await flushPromises();

    // Backend said detached → the literal string is correct here.
    expect(w.find(BRANCH_LABEL).text()).toBe("detached HEAD");
  });

  it("renders 'unavailable' (not 'detached HEAD') while the branch list cannot be fetched", async () => {
    getMock.mockImplementation(async (path: string) => {
      if (path === "spcode/git-repo-check") return gitRepoCheck(true);
      if (path === "spcode/git-worktrees") return WORKTREES_OK;
      // The project is loaded but the branch endpoint keeps failing.
      if (path === "spcode/git-branches") {
        return okEnvelope({
          success: false,
          reason: "feature_disabled",
          stderr: "",
          elapsed_ms: 1,
        });
      }
      return okEnvelope({});
    });

    seedStatus(true, DIR);
    const w = mountSidebar();
    await flushPromises();
    await waitMs(600);
    await flushPromises();

    const label = w.find(BRANCH_LABEL);
    expect(label.exists()).toBe(true);
    expect(label.text()).toBe("分支信息不可用");
  });
});
