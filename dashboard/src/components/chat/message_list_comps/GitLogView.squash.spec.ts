// Author: elecvoid243 @ 2026-08-03
// Spec: docs/superpowers/specs/2026-08-03-git-squash-design.md §4.2
// Selection checkboxes + validity + squash payload for GitLogView.
// Heavy-stub strategy mirrors GitLogView.branchPicker.spec.ts.

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
  // 2026-08-09: the squash / cherry-pick / changelog actions moved into
  // a "更多功能" v-menu; the stub renders both slots inline so the menu
  // items are reachable in tests without opening an overlay.
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
    date: "2026-08-01T10:00:00+08:00",
    subject: `commit ${sha.slice(0, 7)}`,
    body: null,
    parents: [],
    shortstat: { files: 1, additions: 2, deletions: 3 },
    tags: [],
  };
}

const SHAS = ["a".repeat(40), "b".repeat(40), "c".repeat(40), "d".repeat(40)];

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
      squashResetToken: 0,
      changelogResetToken: 0,
      ...props,
    },
    global: { stubs: vuetifyStubs },
  });
}

function findSquashButton(w: ReturnType<typeof mountView>) {
  // 2026-08-09: the squash action is now a v-list-item inside the
  // "更多功能" menu; it carries a dedicated class so tests can find
  // it among the sibling menu items.
  return w.find(".git-log-squash-menu-item");
}

// 2026-08-09 interaction revision: once selection mode is armed the
// "更多功能" dropdown is replaced by a direct confirm button.
function findSquashConfirm(w: ReturnType<typeof mountView>) {
  return w.find(".git-log-squash-confirm-btn");
}

function findChangelogItem(w: ReturnType<typeof mountView>) {
  return w.find(".git-log-changelog-menu-item");
}

function findChangelogConfirm(w: ReturnType<typeof mountView>) {
  return w.find(".git-log-changelog-confirm-btn");
}

describe("GitLogView squash selection (spec 2026-08-03, revised interaction)", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders no checkboxes before entering selection mode; button is clickable", () => {
    const w = mountView();
    expect(w.findAll(".git-log-item-select")).toHaveLength(0);
    const btn = findSquashButton(w);
    expect(btn.exists()).toBe(true);
    expect(btn.attributes("disabled")).toBeUndefined();
  });

  it("first click arms selection mode (checkboxes appear, no submit)", async () => {
    const w = mountView();
    await findSquashButton(w).trigger("click");
    expect(w.findAll(".git-log-item-select")).toHaveLength(SHAS.length);
    // Nothing submitted yet, and with 0 selected the confirm button
    // (which replaces the "更多功能" menu while armed) is grey.
    expect(w.emitted("squash")).toBeUndefined();
    expect(findSquashButton(w).exists()).toBe(false);
    expect(findSquashConfirm(w).exists()).toBe(true);
    expect(findSquashConfirm(w).attributes("disabled")).toBeDefined();
  });

  it("hides everything when viewing another ref", async () => {
    const w = mountView({ activeBranch: "dev" });
    expect(w.findAll(".git-log-item-select")).toHaveLength(0);
    expect(findSquashButton(w).exists()).toBe(false);
    // Arming first, then switching refs, also tears the mode down.
    await w.setProps({ activeBranch: "HEAD" });
    await findSquashButton(w).trigger("click");
    expect(w.findAll(".git-log-item-select")).toHaveLength(SHAS.length);
    await w.setProps({ activeBranch: "dev" });
    await w.setProps({ activeBranch: "HEAD" });
    expect(w.findAll(".git-log-item-select")).toHaveLength(0);
    expect(findSquashConfirm(w).exists()).toBe(false);
  });

  it("single selection stays grey; top-2 contiguous enables submit", async () => {
    const w = mountView();
    await findSquashButton(w).trigger("click");
    const boxes = w.findAll(".git-log-item-select");
    await boxes[0].trigger("click");
    expect(findSquashConfirm(w).attributes("disabled")).toBeDefined();
    await boxes[1].trigger("click");
    expect(findSquashConfirm(w).attributes("disabled")).toBeUndefined();
  });

  it("non-contiguous selection (gap) stays grey", async () => {
    const w = mountView();
    await findSquashButton(w).trigger("click");
    const boxes = w.findAll(".git-log-item-select");
    await boxes[0].trigger("click");
    await boxes[2].trigger("click"); // skips row 1 → gap
    expect(findSquashConfirm(w).attributes("disabled")).toBeDefined();
  });

  it("missing-HEAD selection stays grey", async () => {
    const w = mountView();
    await findSquashButton(w).trigger("click");
    const boxes = w.findAll(".git-log-item-select");
    await boxes[1].trigger("click");
    await boxes[2].trigger("click");
    expect(findSquashConfirm(w).attributes("disabled")).toBeDefined();
  });

  it("confirm button with a valid selection emits payload oldest → newest", async () => {
    const w = mountView();
    await findSquashButton(w).trigger("click");
    const boxes = w.findAll(".git-log-item-select");
    await boxes[0].trigger("click");
    await boxes[1].trigger("click");
    await findSquashConfirm(w).trigger("click");
    const events = w.emitted("squash");
    expect(events).toHaveLength(1);
    const payload = events![0][0] as {
      commits: { sha: string; subject: string }[];
    };
    expect(payload.commits.map((c) => c.sha)).toEqual([SHAS[1], SHAS[0]]);
    expect(payload.commits[0].subject).toBe(`commit ${SHAS[1].slice(0, 7)}`);
  });

  it("cancel button exits selection mode and clears the selection", async () => {
    const w = mountView();
    await findSquashButton(w).trigger("click");
    const boxes = w.findAll(".git-log-item-select");
    await boxes[0].trigger("click");
    await boxes[1].trigger("click");
    await w.find(".git-log-squash-cancel-btn").trigger("click");
    expect(w.findAll(".git-log-item-select")).toHaveLength(0);
    // The "更多功能" menu (and its squash item) is back.
    expect(findSquashConfirm(w).exists()).toBe(false);
    expect(findSquashButton(w).exists()).toBe(true);
    // Re-entering starts from a clean selection.
    await findSquashButton(w).trigger("click");
    expect(w.findAll(".git-log-item-select.is-selected")).toHaveLength(0);
  });

  it("clears the selection and exits the mode when squashResetToken changes", async () => {
    const w = mountView();
    await findSquashButton(w).trigger("click");
    const boxes = w.findAll(".git-log-item-select");
    await boxes[0].trigger("click");
    await boxes[1].trigger("click");
    await w.setProps({ squashResetToken: 1 });
    expect(w.findAll(".git-log-item-select")).toHaveLength(0);
    expect(findSquashConfirm(w).exists()).toBe(false);
    await findSquashButton(w).trigger("click");
    expect(w.findAll(".git-log-item-select.is-selected")).toHaveLength(0);
  });
});

// 2026-08-09 changelog selection (spec 2026-08-09 §3.1): contiguous
// anywhere in the displayed list, min 1 / max 50, available in every
// history view (no viewingCurrent gate), mutually exclusive with the
// squash mode.
describe("GitLogView changelog selection (spec 2026-08-09)", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("menu item is always visible, even when viewing another ref", () => {
    const w = mountView({ activeBranch: "dev" });
    expect(findChangelogItem(w).exists()).toBe(true);
  });

  it("first click arms selection mode (checkboxes appear, no submit)", async () => {
    const w = mountView();
    await findChangelogItem(w).trigger("click");
    expect(w.findAll(".git-log-item-select")).toHaveLength(SHAS.length);
    expect(w.emitted("changelog")).toBeUndefined();
    // 0 selected → the confirm button (replacing the menu) is disabled.
    expect(findChangelogItem(w).exists()).toBe(false);
    expect(findChangelogConfirm(w).exists()).toBe(true);
    expect(findChangelogConfirm(w).attributes("disabled")).toBeDefined();
  });

  it("single non-HEAD commit is a valid selection (differs from squash)", async () => {
    const w = mountView();
    await findChangelogItem(w).trigger("click");
    const boxes = w.findAll(".git-log-item-select");
    await boxes[2].trigger("click"); // middle row, not HEAD
    expect(findChangelogConfirm(w).attributes("disabled")).toBeUndefined();
  });

  it("non-contiguous selection (gap) stays disabled", async () => {
    const w = mountView();
    await findChangelogItem(w).trigger("click");
    const boxes = w.findAll(".git-log-item-select");
    await boxes[0].trigger("click");
    await boxes[2].trigger("click"); // skips row 1 → gap
    expect(findChangelogConfirm(w).attributes("disabled")).toBeDefined();
  });

  it("confirm button with a valid selection emits payload oldest → newest", async () => {
    const w = mountView();
    await findChangelogItem(w).trigger("click");
    const boxes = w.findAll(".git-log-item-select");
    await boxes[1].trigger("click");
    await boxes[2].trigger("click");
    await findChangelogConfirm(w).trigger("click");
    const events = w.emitted("changelog");
    expect(events).toHaveLength(1);
    const payload = events![0][0] as {
      commits: { sha: string; subject: string }[];
    };
    expect(payload.commits.map((c) => c.sha)).toEqual([SHAS[2], SHAS[1]]);
  });

  it("selection modes are exclusive; the menu is hidden while armed", async () => {
    const w = mountView();
    await findSquashButton(w).trigger("click");
    const boxes = w.findAll(".git-log-item-select");
    await boxes[0].trigger("click");
    // While squash is armed the "更多功能" menu is replaced by the
    // squash confirm button, so the changelog item is unreachable.
    expect(findChangelogItem(w).exists()).toBe(false);
    expect(findSquashConfirm(w).exists()).toBe(true);
    // Cancel exits back to the menu.
    await w.find(".git-log-squash-cancel-btn").trigger("click");
    expect(findChangelogItem(w).exists()).toBe(true);
    expect(findSquashConfirm(w).exists()).toBe(false);
    // Arming changelog shows its confirm button and hides the menu.
    await findChangelogItem(w).trigger("click");
    expect(findChangelogConfirm(w).exists()).toBe(true);
    const changelogBoxes = w.findAll(".git-log-item-select");
    await changelogBoxes[1].trigger("click");
    await findChangelogConfirm(w).trigger("click");
    const events = w.emitted("changelog");
    expect(events).toHaveLength(1);
    expect(w.emitted("squash")).toBeUndefined();
  });

  it("clears the selection and exits the mode when changelogResetToken changes", async () => {
    const w = mountView();
    await findChangelogItem(w).trigger("click");
    const boxes = w.findAll(".git-log-item-select");
    await boxes[1].trigger("click");
    await w.setProps({ changelogResetToken: 1 });
    expect(w.findAll(".git-log-item-select")).toHaveLength(0);
    expect(findChangelogConfirm(w).exists()).toBe(false);
    await findChangelogItem(w).trigger("click");
    expect(w.findAll(".git-log-item-select.is-selected")).toHaveLength(0);
  });
});
