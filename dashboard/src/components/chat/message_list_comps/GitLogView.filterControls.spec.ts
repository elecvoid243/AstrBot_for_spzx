// Author: elecvoid243 @ 2026-09-09
// 筛选栏控件：数量（10 / 20 / 50 预设按钮组）+ 起始时间（原生 date 日历）。
//
// 沿用 GitLogView.tags.spec.ts 的 heavy-stub 策略：只挂载 GitLogView，
// 用轻量 stub 替掉 Vuetify 控件与 GitStatsPanel，断言的是「控件收到什么
// 值 / emit 出什么载荷」，而不是 Vuetify 的渲染细节。

import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import GitLogView from "./GitLogView.vue";

function makeState() {
  return {
    kind: "ok" as const,
    snapshot: {
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
      commits: [
        {
          sha: "a".repeat(40),
          shaShort: "aaaaaaa",
          author: { name: "n", email: "e" },
          committer: { name: "n", email: "e" },
          date: "2026-09-09T00:00:00+08:00",
          subject: "s",
          body: null,
          parents: [],
          shortstat: { files: 1, additions: 1, deletions: 0 },
          tags: [],
        },
      ],
    },
  };
}

const stubs = {
  "v-icon": { template: "<i />" },
  "v-text-field": {
    name: "v-text-field",
    props: ["modelValue", "label"],
    // 内部放一个真实 <input>，让 onSinceClick 的 querySelector("input")
    // 有东西可找（Vuetify 真实实现同理）。
    template: '<div><input type="date" /></div>',
  },
  "v-btn-toggle": {
    name: "v-btn-toggle",
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template: "<div><slot /></div>",
  },
  "v-btn": {
    name: "v-btn",
    props: ["value", "height", "minWidth"],
    template: "<button><slot /></button>",
  },
  GitStatsPanel: { name: "GitStatsPanel", template: "<div />" },
  FilePatchPanel: true,
};

function mountView() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return mount(GitLogView as any, {
    props: {
      state: makeState(),
      hasMore: false,
      isLoading: false,
      gitShow: {
        fetch: () => {},
        fetchFile: () => {},
        getState: () => ({ kind: "idle" }),
        getData: () => null,
      },
      focusedCommitSha: null,
      gitStats: { state: { value: { kind: "idle" } }, refresh: () => {} },
      statsOpen: false,
      range: { kind: "preset", preset: "1w" },
      topFilesLimit: 10,
      branchItems: [],
      tagItems: [],
      currentBranch: "main",
      activeBranch: "HEAD",
    },
    global: { stubs },
  });
}

function sinceField(w: ReturnType<typeof mountView>) {
  return w
    .findAllComponents({ name: "v-text-field" })
    .find((f) => f.props("label") === "起始时间");
}

describe("GitLogView 数量 button group", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("offers exactly 10 / 20 / 50 and starts on the 20 default", () => {
    const w = mountView();
    expect(w.find(".git-log-filter-n-label").text()).toBe("数量");
    const presets = w
      .findAllComponents({ name: "v-btn" })
      .filter((b) => b.props("value") !== undefined);
    expect(presets.map((b) => b.props("value"))).toEqual([10, 20, 50]);
    expect(presets.map((b) => b.text())).toEqual(["10", "20", "50"]);
    expect(w.findComponent({ name: "v-btn-toggle" }).props("modelValue")).toBe(
      20,
    );
  });

  it("applies the picked count on 筛选", async () => {
    const w = mountView();
    w.findComponent({ name: "v-btn-toggle" }).vm.$emit(
      "update:modelValue",
      50,
    );
    await nextTick();
    const applyBtn = w
      .findAll("button")
      .find((b) => b.text().includes("筛选"));
    expect(applyBtn).toBeTruthy();
    await applyBtn!.trigger("click");
    const applied = w.emitted("apply")?.[0]?.[0] as { n: number };
    expect(applied.n).toBe(50);
  });

  // 热力图点某天（n=200）/「加载更多」（n 翻倍）会写入非预设值 —— 按钮组
  // 如实反映（VBtnToggle 的 getIds 找不到匹配值 → 无选中项），不篡改值。
  it("passes a non-preset count through without rewriting it", async () => {
    const w = mountView();
    w.findComponent({ name: "GitStatsPanel" }).vm.$emit(
      "filter-path",
      "src/a.ts",
    );
    await nextTick();
    expect(w.findComponent({ name: "v-btn-toggle" }).props("modelValue")).toBe(
      200,
    );
  });
});

describe("GitLogView 起始时间 date picker", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("uses a native date input so clicking it opens a calendar", () => {
    const w = mountView();
    const since = sinceField(w);
    expect(since).toBeTruthy();
    expect(since!.attributes("type")).toBe("date");
    expect(since!.props("modelValue")).toBe("");
  });

  // 热力图写入 "2026-09-08T00:00:00"（--until 不能落在当天零点），而原生
  // date 控件只认 YYYY-MM-DD：显示层截断，载荷保持原值。
  it("displays a datetime since value as a plain date", async () => {
    const w = mountView();
    w.findComponent({ name: "GitStatsPanel" }).vm.$emit("filter-date", {
      since: "2026-09-08T00:00:00",
      until: "2026-09-08T23:59:59",
    });
    await nextTick();
    expect(sinceField(w)!.props("modelValue")).toBe("2026-09-08");
    const applied = w.emitted("apply")?.[0]?.[0] as { since: string };
    expect(applied.since).toBe("2026-09-08T00:00:00");
  });

  // Chrome / Edge 的原生 date 输入默认只在点右侧小图标时弹出日历；点击
  // 字段任意位置都由 onSinceClick 调 showPicker() 弹出（不支持时静默回退）。
  it("opens the calendar from a click anywhere on the field", async () => {
    const proto = HTMLInputElement.prototype as unknown as {
      showPicker?: () => void;
    };
    const original = proto.showPicker;
    const showPicker = vi.fn();
    proto.showPicker = showPicker;
    try {
      const w = mountView();
      await sinceField(w)!.trigger("click");
      expect(showPicker).toHaveBeenCalledTimes(1);
    } finally {
      if (original) proto.showPicker = original;
      else delete proto.showPicker;
    }
  });
});
