// Author: elecvoid243 @ 2026-08-02
// Spec: docs/superpowers/specs/2026-08-01-git-history-branch-picker-design.md
// Branch-picker combobox + complementary revert/cherry-pick visibility.
//
// Mounts the real GitLogView with the heavy-stub strategy (Vuetify and
// GitStatsPanel stubbed), mirroring GitRepoInitPrompt.spec.ts /
// DocumentManager.spec.ts. The point is to assert the new props drive
// the combobox items and the per-row button visibility — not to render
// the full Vuetify tree.

import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import GitLogView from "./GitLogView.vue";
import type { SpcodeLogSnapshot } from "@/composables/parseSpcodeGitWorkflow";

const vuetifyStubs = {
  "v-icon": { template: "<i />" },
  "v-text-field": {
    name: "v-text-field",
    props: ["modelValue", "label", "placeholder"],
    template: "<div />",
  },
  "v-combobox": {
    name: "v-combobox",
    props: ["modelValue", "items", "label", "placeholder", "hideNoData"],
    template: "<div />",
  },
  "v-progress-circular": { template: "<i />" },
  GitStatsPanel: { template: "<div />" },
};

function makeCommit(sha: string) {
  return {
    sha,
    shaShort: sha.slice(0, 7),
    author: { name: "alice", email: "alice@example.com" },
    committer: { name: "alice", email: "alice@example.com" },
    date: "2026-08-01T10:00:00+08:00",
    subject: `commit ${sha.slice(0, 7)}`,
    body: null,
    parents: [],
    shortstat: { files: 1, additions: 2, deletions: 3 },
    tags: [],
  };
}

function makeSnapshot(): SpcodeLogSnapshot {
  return {
    success: true,
    reason: null,
    loaded: true,
    elapsedMs: 1,
    umo: "u",
    worktree: "w",
    directory: "d",
    ref: "HEAD",
    resolvedRef: "",
    count: 1,
    hasMore: false,
    truncated: false,
    maxBytes: 1024,
    commits: [makeCommit("a".repeat(40))],
  };
}

function mountView(props: Record<string, unknown> = {}) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return mount(GitLogView as any, {
    props: {
      state: { kind: "ok", snapshot: makeSnapshot() },
      hasMore: false,
      isLoading: false,
      gitShow: {
        getState: () => ({ kind: "idle" }),
        getData: () => null,
        getFileState: () => ({ kind: "idle" }),
        fetch: () => Promise.resolve(),
        fetchFile: () => Promise.resolve(),
      },
      focusedCommitSha: null,
      gitStats: { state: { value: { kind: "idle" } }, refresh: () => {} },
      statsOpen: false,
      range: null,
      topFilesLimit: 10,
      // New props under test (spec §2):
      branchItems: ["main", "dev", "origin/dev"],
      currentBranch: "main",
      activeRef: "HEAD",
      ...props,
    },
    global: { stubs: vuetifyStubs },
  });
}

describe("GitLogView branch picker (spec 2026-08-01)", () => {
  beforeEach(() => {
    // GitLogView derives isDark from the customizer store via
    // storeToRefs — a pinia instance must be active before mount.
    setActivePinia(createPinia());
  });

  it("passes branchItems through to the ref combobox", () => {
    const w = mountView();
    const combo = w.findComponent({ name: "v-combobox" });
    expect(combo.exists()).toBe(true);
    expect(combo.props("items")).toEqual(["main", "dev", "origin/dev"]);
  });

  it("reverts to free-input combobox when branchItems is empty", () => {
    const w = mountView({ branchItems: [] });
    const combo = w.findComponent({ name: "v-combobox" });
    expect(combo.exists()).toBe(true);
    expect(combo.props("items")).toEqual([]);
    expect(combo.props("hideNoData")).toBe(true);
  });

  it("shows revert and hides cherry-pick when activeRef is HEAD", () => {
    const w = mountView({ activeRef: "HEAD" });
    expect(w.find(".git-log-item-revert").exists()).toBe(true);
    expect(w.find(".git-log-item-cherry-pick").exists()).toBe(false);
  });

  it("treats the current branch name as the current view", () => {
    const w = mountView({ activeRef: "main", currentBranch: "main" });
    expect(w.find(".git-log-item-revert").exists()).toBe(true);
    expect(w.find(".git-log-item-cherry-pick").exists()).toBe(false);
  });

  it("shows cherry-pick and hides revert when viewing another branch", () => {
    const w = mountView({ activeRef: "dev", currentBranch: "main" });
    expect(w.find(".git-log-item-revert").exists()).toBe(false);
    expect(w.find(".git-log-item-cherry-pick").exists()).toBe(true);
  });

  it("shows cherry-pick for a free-typed sha (loose rule, spec Q2)", () => {
    const w = mountView({ activeRef: "abc1234", currentBranch: "main" });
    expect(w.find(".git-log-item-revert").exists()).toBe(false);
    expect(w.find(".git-log-item-cherry-pick").exists()).toBe(true);
  });

  it("treats null/empty activeRef as the current view", () => {
    const w = mountView({ activeRef: null });
    expect(w.find(".git-log-item-revert").exists()).toBe(true);
    expect(w.find(".git-log-item-cherry-pick").exists()).toBe(false);
  });
});
