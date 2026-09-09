// Author: elecvoid243 @ 2026-09-09
// Spec: docs/superpowers/specs/2026-09-09-git-reset-design.md (plugin repo)
//
// Per-row "重置" affordance (SourceTree-style "Reset current branch to
// this commit"). Heavy-stub strategy mirrors GitLogView.squash.spec.ts.
// Pins the visibility contract decided by the user:
//   - hidden on the HEAD row (reset to HEAD is a roundabout unstage-all)
//   - hidden when viewing a non-current ref (the endpoint resets the
//     CURRENT branch; that context offers cherry-pick instead)
//   - renders on every other row of the current branch's history and
//     emits `reset-branch` with the row's sha + subject

import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
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
  "v-tooltip": { template: "<i />" },
  GitStatsPanel: { template: "<div />" },
  FilePatchPanel: { template: "<div />" },
};

function makeCommit(sha: string) {
  return {
    sha,
    shaShort: sha.slice(0, 7),
    author: { name: "alice", email: "alice@example.com" },
    committer: { name: "alice", email: "alice@example.com" },
    date: "2026-09-01T10:00:00+08:00",
    subject: `commit ${sha.slice(0, 7)}`,
    body: null,
    parents: [],
    shortstat: { files: 1, additions: 2, deletions: 3 },
    tags: [],
  };
}

const SHAS = ["a".repeat(40), "b".repeat(40), "c".repeat(40)];

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
    count: SHAS.length,
    hasMore: false,
    truncated: false,
    maxBytes: 1024,
    commits: SHAS.map(makeCommit), // newest → oldest (git log order)
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
      // 2026-09-09 split-ref-filter: 分支 / 标签拆成两个 props。
      branchItems: [
        { title: "HEAD", value: "HEAD" },
        { title: "当前分支", type: "subheader" },
        { title: "main", value: "main" },
        { title: "dev", value: "dev" },
      ],
      tagItems: [],
      currentBranch: "main",
      activeBranch: "HEAD",
      // 2026-09-09 head-sha: 真实 HEAD = 列表第一行（未筛选场景）。
      headSha: SHAS[0],
      squashResetToken: 0,
      changelogResetToken: 0,
      ...props,
    },
    global: { stubs: vuetifyStubs },
  });
}

function findResetButtons(w: ReturnType<typeof mountView>) {
  return w.findAll(".git-log-item-reset");
}

describe("GitLogView per-row reset (spec 2026-09-09)", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders the reset button on non-HEAD rows of the current branch", () => {
    const w = mountView();
    const buttons = findResetButtons(w);
    // HEAD row (commits[0]) never gets the button; the other two do.
    expect(buttons).toHaveLength(SHAS.length - 1);
    // The reverted/reveal buttons live in the meta line of their rows —
    // verify the HEAD row's meta has a revert button but no reset one.
    const headMeta = w.findAll(".git-log-item")[0];
    expect(headMeta.find(".git-log-item-revert").exists()).toBe(true);
    expect(headMeta.find(".git-log-item-reset").exists()).toBe(false);
  });

  it("hides the reset button entirely when viewing a non-current ref", async () => {
    const w = mountView({ activeBranch: "dev" });
    expect(findResetButtons(w)).toHaveLength(0);
    // And back on the current branch it reappears (minus the HEAD row).
    await w.setProps({ activeBranch: "HEAD" });
    expect(findResetButtons(w)).toHaveLength(SHAS.length - 1);
  });

  // 2026-09-09 head-sha 回归：筛选后「列表第一行」只是最新一条命中结果，
  // 不等于仓库 HEAD。此时第一行也必须给「重置」按钮，而真正的 HEAD 行
  // （可能根本不在结果里）依旧不给。
  it("shows reset on the first row when the filter's top row is not HEAD", () => {
    const w = mountView({ headSha: SHAS[2] });
    // SHAS[2] 在列表里（最后一行）→ 它不给按钮，其余两行都给。
    const buttons = findResetButtons(w);
    expect(buttons).toHaveLength(SHAS.length - 1);
    const firstRow = w.findAll(".git-log-item")[0];
    expect(firstRow.find(".git-log-item-reset").exists()).toBe(true);
    const lastRow = w.findAll(".git-log-item")[SHAS.length - 1];
    expect(lastRow.find(".git-log-item-reset").exists()).toBe(false);
  });

  it("hides reset everywhere when the HEAD sha is unknown", () => {
    const w = mountView({ headSha: null });
    expect(findResetButtons(w)).toHaveLength(0);
  });

  it("emits reset-branch with the row's sha + subject on click", async () => {
    const w = mountView();
    const buttons = findResetButtons(w);
    await buttons[0].trigger("click");
    const events = w.emitted("reset-branch");
    expect(events).toHaveLength(1);
    const payload = events![0][0] as { sha: string; subject: string };
    // buttons[0] belongs to commits[1] (HEAD row has no button).
    expect(payload.sha).toBe(SHAS[1]);
    expect(payload.subject).toBe(`commit ${SHAS[1].slice(0, 7)}`);
    expect(w.emitted("revert")).toBeUndefined();
  });
});
