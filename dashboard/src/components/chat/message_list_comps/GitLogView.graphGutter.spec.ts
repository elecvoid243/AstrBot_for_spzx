// Author: elecvoid243 @ 2026-10-05
// Spec: docs/superpowers/specs/2026-10-05-git-log-graph-and-branch-tree-design.md §4.1 / §4.2
//
// 行内接线契约：有分叉才出现 gutter、线性历史零占用、选择模式不隐藏 gutter。
// Heavy-stub 策略与 props 列表沿用 GitLogView.squash.spec.ts（v-menu 的 stub
// 会把两个 slot 内联渲染，否则武装不了选择模式）。

import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";
import GitLogView from "./GitLogView.vue";
import type { SpcodeLogSnapshot } from "@/composables/parseSpcodeGitWorkflow";

const vuetifyStubs = {
  "v-icon": { template: "<i />" },
  "v-btn": {
    name: "v-btn",
    props: ["disabled", "title", "size", "variant"],
    template:
      '<button class="v-btn-stub" :disabled="disabled" :title="title"><slot /></button>',
  },
  "v-text-field": {
    name: "v-text-field",
    props: ["modelValue", "label", "placeholder"],
    template: "<div />",
  },
  "v-autocomplete": {
    name: "v-autocomplete",
    props: ["modelValue", "items", "label", "placeholder", "hideNoData"],
    template: "<div />",
  },
  "v-combobox": {
    name: "v-combobox",
    props: ["modelValue", "items", "label", "placeholder", "hideNoData"],
    template: "<div />",
  },
  "v-progress-circular": { template: "<i />" },
  "v-menu": {
    name: "v-menu",
    template:
      '<div class="v-menu-stub"><slot name="activator" :props="{}" /><slot /></div>',
  },
  "v-list": { name: "v-list", template: "<div><slot /></div>" },
  "v-list-item": {
    name: "v-list-item",
    props: ["disabled"],
    template:
      '<button class="v-list-item-stub" :disabled="disabled"><slot name="prepend" /><slot /></button>',
  },
  "v-list-item-title": { template: "<span><slot /></span>" },
  "v-list-subheader": {
    name: "v-list-subheader",
    template: '<div class="v-list-subheader"><slot /></div>',
  },
  "v-tooltip": { template: "<i />" },
  GitStatsPanel: { template: "<div />" },
  FilePatchPanel: { template: "<div />" },
};

const SH = (ch: string): string => ch.repeat(40);
const M1 = SH("a");
const A1 = SH("b");
const B1 = SH("c");

function makeCommit(sha: string, parents: string[]) {
  return {
    sha,
    shaShort: sha.slice(0, 7),
    author: { name: "alice", email: "alice@example.com" },
    committer: { name: "alice", email: "alice@example.com" },
    date: "2026-08-01T10:00:00+08:00",
    subject: `commit ${sha.slice(0, 7)}`,
    body: null,
    parents,
    shortstat: { files: 1, additions: 2, deletions: 3 },
    tags: [],
  };
}

function makeSnapshot(
  commits: ReturnType<typeof makeCommit>[],
): SpcodeLogSnapshot {
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
    count: commits.length,
    hasMore: false,
    truncated: false,
    maxBytes: 1024,
    commits,
  };
}

/** merge 提交 + 两条父线 → 需要 2 列。 */
const FORKED = makeSnapshot([
  makeCommit(M1, [A1, B1]),
  makeCommit(A1, [B1]),
  makeCommit(B1, []),
]);

/** 单链 → 1 列 → gutter 必须零占用。 */
const LINEAR = makeSnapshot([
  makeCommit(SH("d"), [SH("e")]),
  makeCommit(SH("e"), []),
]);

function mountView(props: Record<string, unknown> = {}) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return mount(GitLogView as any, {
    props: {
      state: { kind: "ok", snapshot: FORKED },
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
      branchItems: [
        { title: "HEAD", value: "HEAD" },
        { title: "当前分支", type: "subheader" },
        { title: "main", value: "main" },
      ],
      tagItems: [],
      currentBranch: "main",
      activeBranch: "HEAD",
      headSha: M1,
      squashResetToken: 0,
      changelogResetToken: 0,
      ...props,
    },
    global: { stubs: vuetifyStubs },
  });
}

describe("GitLogView lane gutter (2026-10-05)", () => {
  beforeEach(() => {
    // GitLogView derives isDark from the customizer store via storeToRefs —
    // a pinia instance must be active before mount.
    setActivePinia(createPinia());
  });

  it("renders the gutter and drops the commit icon when the history forks", async () => {
    const w = mountView();
    await nextTick();

    expect(w.find(".git-log-gutter").exists()).toBe(true);
    expect(w.find(".git-log-item").attributes("style")).toContain("--gw: 34px");
    // lane 节点取代了 mdi-source-commit：同一行不要两套 commit 隐喻
    expect(w.findAll(".git-log-item-icon")).toHaveLength(0);
  });

  it("stays out of the way for a linear history", async () => {
    const w = mountView({ state: { kind: "ok", snapshot: LINEAR } });
    await nextTick();

    expect(w.find(".git-log-gutter").exists()).toBe(false);
    expect(w.find(".git-log-item").attributes("style")).toContain("--gw: 0px");
    expect(w.findAll(".git-log-item-icon").length).toBeGreaterThan(0);
  });

  it("keeps the gutter while the squash selection UI is armed", async () => {
    const w = mountView();
    await w.find(".git-log-squash-menu-item").trigger("click");

    expect(w.find(".git-log-list").classes()).toContain("squash-selecting");
    expect(w.find(".git-log-gutter").exists()).toBe(true);
  });
});
