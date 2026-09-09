// Author: elecvoid243 @ 2026-08-02
// Spec: docs/superpowers/specs/2026-08-01-git-history-branch-picker-design.md
// Branch-picker + complementary revert/cherry-pick visibility.
//
// 2026-09-09 (elecvoid243) split-ref-filter: 原「Ref」多合一 combobox 拆成
// 「分支」（v-autocomplete，仅分支）与「提交 / 标签」（v-combobox，标签联想
// + SHA 自由输入）两个控件。本 spec 随之验证：
//   · branchItems / tagItems 各自透传到对应控件，分支列表不含标签；
//   · 按钮可见性只由 activeBranch（分支）决定 —— 用 SHA / 标签检索提交时
//     不再把视图误判成「非当前分支」（本次拆分的直接动机）。
//
// Mounts the real GitLogView with the heavy-stub strategy (Vuetify and
// GitStatsPanel stubbed), mirroring GitRepoInitPrompt.spec.ts /
// DocumentManager.spec.ts.

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
  "v-autocomplete": {
    name: "v-autocomplete",
    props: {
      modelValue: null,
      items: { type: Array, default: () => [] },
      label: String,
      placeholder: String,
      hideNoData: Boolean,
      itemTitle: String,
      itemValue: String,
    },
    // Render the #item slot for every entry so the grouped picker's
    // subheader/item branching is assertable without opening the
    // portal-rendered menu in jsdom.
    template: `
      <div>
        <template v-for="(entry, i) in items" :key="i">
          <slot
            name="item"
            :item="{ raw: entry, title: entry.title, value: entry.value }"
            :props="{ title: entry.title, value: entry.value }"
          />
        </template>
      </div>
    `,
  },
  "v-combobox": {
    name: "v-combobox",
    props: {
      modelValue: null,
      items: { type: Array, default: () => [] },
      label: String,
      placeholder: String,
      hideNoData: Boolean,
      returnObject: Boolean,
      itemTitle: String,
      itemValue: String,
      clearable: Boolean,
    },
    template: `
      <div>
        <template v-for="(entry, i) in items" :key="i">
          <slot
            name="item"
            :item="{ raw: entry, title: entry.title, value: entry.value }"
            :props="{ title: entry.title, value: entry.value }"
          />
        </template>
      </div>
    `,
  },
  "v-list-subheader": {
    name: "v-list-subheader",
    template: '<div class="v-list-subheader"><slot /></div>',
  },
  "v-list-item": {
    name: "v-list-item",
    template: '<div class="v-list-item" />',
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

const BRANCH_ITEMS = [
  { title: "HEAD", value: "HEAD" },
  { title: "当前分支", type: "subheader" },
  { title: "main", value: "main" },
];
const TAG_ITEMS = [
  { title: "标签", type: "subheader" },
  { title: "v1.0.0", value: "v1.0.0" },
];

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
      // Props under test (2026-09-09 split ref filter):
      branchItems: BRANCH_ITEMS,
      tagItems: TAG_ITEMS,
      currentBranch: "main",
      activeBranch: "HEAD",
      ...props,
    },
    global: { stubs: vuetifyStubs },
  });
}

describe("GitLogView split ref filter (2026-09-09)", () => {
  beforeEach(() => {
    // GitLogView derives isDark from the customizer store via
    // storeToRefs — a pinia instance must be active before mount.
    setActivePinia(createPinia());
  });

  it("passes branchItems through to the branch autocomplete", () => {
    const w = mountView();
    const ac = w.findComponent({ name: "v-autocomplete" });
    expect(ac.exists()).toBe(true);
    expect(ac.props("items")).toEqual(BRANCH_ITEMS);
  });

  it("never offers tags in the branch picker", () => {
    const w = mountView();
    const ac = w.findComponent({ name: "v-autocomplete" });
    const items = ac.props("items") as Array<{ title: string; value?: string }>;
    expect(items.some((i) => i.value === "v1.0.0")).toBe(false);
    expect(items.some((i) => i.title === "标签")).toBe(false);
  });

  it("renders branch group headers as v-list-subheader, not selectable items", () => {
    const w = mountView();
    const ac = w.findComponent({ name: "v-autocomplete" });
    expect(ac.findAll(".v-list-subheader").map((h) => h.text())).toEqual([
      "当前分支",
    ]);
    // 3 entries → 1 header + 2 selectable items.
    expect(ac.findAll(".v-list-item")).toHaveLength(2);
  });

  it("degrades to an empty (no-menu) picker when branchItems is empty", () => {
    const w = mountView({ branchItems: [] });
    const ac = w.findComponent({ name: "v-autocomplete" });
    expect(ac.props("items")).toEqual([]);
    expect(ac.props("hideNoData")).toBe(true);
  });

  it("passes tagItems through to the rev combobox as suggestions", () => {
    const w = mountView();
    const combo = w.findComponent({ name: "v-combobox" });
    expect(combo.exists()).toBe(true);
    expect(combo.props("items")).toEqual(TAG_ITEMS);
    // Free SHA input must stay possible → custom values allowed and the
    // "no data" row suppressed while typing a hash.
    expect(combo.props("returnObject")).toBe(false);
    expect(combo.props("hideNoData")).toBe(true);
    expect(combo.props("clearable")).toBe(true);
  });

  it("shows revert and hides cherry-pick when activeBranch is HEAD", () => {
    const w = mountView({ activeBranch: "HEAD" });
    expect(w.find(".git-log-item-revert").exists()).toBe(true);
    expect(w.find(".git-log-item-cherry-pick").exists()).toBe(false);
  });

  it("treats the current branch name as the current view", () => {
    const w = mountView({ activeBranch: "main", currentBranch: "main" });
    expect(w.find(".git-log-item-revert").exists()).toBe(true);
    expect(w.find(".git-log-item-cherry-pick").exists()).toBe(false);
  });

  it("shows cherry-pick and hides revert when viewing another branch", () => {
    const w = mountView({ activeBranch: "dev", currentBranch: "main" });
    expect(w.find(".git-log-item-revert").exists()).toBe(false);
    expect(w.find(".git-log-item-cherry-pick").exists()).toBe(true);
  });

  it("treats null/empty activeBranch as the current view", () => {
    const w = mountView({ activeBranch: null });
    expect(w.find(".git-log-item-revert").exists()).toBe(true);
    expect(w.find(".git-log-item-cherry-pick").exists()).toBe(false);
  });

  // 2026-09-09 split-ref-filter 回归：拆分前 SHA 写进 ref，按 hash 检索
  // 会把视图判成「非当前分支」，revert / amend / squash / reset 集体消失。
  // 拆分后 SHA 走 rev，按钮可见性只看 activeBranch。
  it("keeps current-branch buttons while a SHA lives in the rev filter", () => {
    const w = mountView({
      activeBranch: "HEAD",
      currentBranch: "main",
      state: { kind: "ok", snapshot: makeSnapshot() },
    });
    // rev 值只存在于子组件的本地草稿里（父组件通过 filter 传入的是
    // activeBranch），所以这里直接断言按钮可见性由 activeBranch 决定。
    // 注意 reset 按钮只在非 HEAD 行出现，单提交快照里唯一一行就是 HEAD，
    // 因此这里只校验 revert / amend 与 cherry-pick 的互补可见性。
    expect(w.find(".git-log-item-revert").exists()).toBe(true);
    expect(w.find(".git-log-item-amend").exists()).toBe(true);
    expect(w.find(".git-log-item-cherry-pick").exists()).toBe(false);
  });
});
