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
    props: [
      "modelValue",
      "items",
      "label",
      "placeholder",
      "hideNoData",
      "returnObject",
      "itemTitle",
      "itemValue",
    ],
    // 2026-09-08: render the #item slot for every entry so the grouped
    // picker's subheader/item branching is assertable without opening
    // the portal-rendered menu in jsdom. The payload mirrors Vuetify's
    // real item slot ({ item: { raw, title, value }, props }).
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
      // New props under test (spec §2 / 2026-09-08 grouped ref picker):
      refItems: [
        { title: "当前分支", type: "subheader" },
        { title: "main", value: "main" },
        { title: "标签", type: "subheader" },
        { title: "v1.0.0", value: "v1.0.0" },
      ],
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

  it("passes refItems through to the ref combobox", () => {
    const w = mountView();
    const combo = w.findComponent({ name: "v-combobox" });
    expect(combo.exists()).toBe(true);
    expect(combo.props("items")).toEqual([
      { title: "当前分支", type: "subheader" },
      { title: "main", value: "main" },
      { title: "标签", type: "subheader" },
      { title: "v1.0.0", value: "v1.0.0" },
    ]);
  });

  it("renders grouped ref items including tags", () => {
    const wrapper = mountView();
    const combo = wrapper.findComponent({ name: "v-combobox" });
    expect(combo.props("items")).toEqual(
      expect.arrayContaining([{ title: "标签", type: "subheader" }]),
    );
  });

  it("keeps the combobox model a plain string (return-object=false)", () => {
    const w = mountView();
    const combo = w.findComponent({ name: "v-combobox" });
    expect(combo.props("returnObject")).toBe(false);
  });

  it("renders group headers as v-list-subheader instead of selectable items", () => {
    const w = mountView();
    const combo = w.findComponent({ name: "v-combobox" });
    const headers = combo.findAll(".v-list-subheader");
    expect(headers.map((h) => h.text())).toEqual(["当前分支", "标签"]);
    // 4 entries → 2 headers + 2 selectable items: if the headers were
    // rendered as list items (the 3.7 default), there would be 4 items
    // and no subheaders at all.
    expect(combo.findAll(".v-list-item")).toHaveLength(2);
  });

  it("reverts to free-input combobox when refItems is empty", () => {
    const w = mountView({ refItems: [] });
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
