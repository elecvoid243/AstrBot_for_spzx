// Author: impl_b4 @ 2026-09-08
// Spec: 2026-09-08-git-log-tags-and-filters §2.4 — tag 徽章渲染与点击筛选。
// 沿用 GitLogView.branchPicker.spec.ts 的 heavy-stub 策略：只断言徽章与 emit。
import { beforeEach, describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import GitLogView from "@/components/chat/message_list_comps/GitLogView.vue";

function makeState(tags: string[]) {
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
      count: 1,
      hasMore: false,
      truncated: false,
      maxBytes: 1024,
      resolvedRef: "",
      commits: [
        {
          sha: "a".repeat(40),
          shaShort: "aaaaaaa",
          author: { name: "n", email: "e" },
          committer: { name: "n", email: "e" },
          date: "2026-09-08T00:00:00+08:00",
          subject: "s",
          body: null,
          parents: [],
          shortstat: { files: 1, additions: 1, deletions: 0 },
          tags,
        },
      ],
    },
  };
}

const stubs = {
  GitStatsPanel: true,
  FilePatchPanel: true,
  VTooltip: { template: "<span><slot name='activator' :props='{}'/><slot/></span>" },
};

function mountLog(tags: string[]) {
  return mount(GitLogView, {
    props: {
      state: makeState(tags) as never,
      hasMore: false,
      isLoading: false,
      // 2026-09-08 fix: 键盘展开测试会触发 expanded watcher 的 fetch()
      // 与展开态的 getState()，因此给出最小可用的 gitShow 句柄。
      gitShow: {
        fetch: () => {},
        fetchFile: () => {},
        getState: () => ({ kind: "idle" }),
        getData: () => null,
      } as never,
      focusedCommitSha: null,
      // GitStatsPanel 被 stub，但其 props 表达式仍会在父组件渲染时求值，
      // 故这里给出最小可用的 composable 句柄（state.value 必须存在）。
      gitStats: { state: { value: { kind: "idle" } }, refresh: () => {} } as never,
      statsOpen: false,
      range: { kind: "preset", preset: "1w" } as never,
      topFilesLimit: 10,
      refItems: [],
      currentBranch: "main",
      activeRef: "HEAD",
    },
    global: { stubs },
  });
}

describe("GitLogView tag chips", () => {
  beforeEach(() => {
    // GitLogView 的 useOpenOnDisk → useToast 需要 active pinia。
    setActivePinia(createPinia());
  });

  it("renders up to two tag chips and an overflow counter", () => {
    const wrapper = mountLog(["v1.0.0", "v1.1.0", "v1.2.0"]);
    const chips = wrapper.findAll(".git-log-item-tag");
    expect(chips.map((c) => c.text())).toEqual(["v1.0.0", "v1.1.0"]);
    expect(wrapper.find(".git-log-item-tag-more").text()).toBe("+1");
  });

  it("clicking a chip applies ref=<tag>", async () => {
    const wrapper = mountLog(["v1.0.0"]);
    await wrapper.find(".git-log-item-tag").trigger("click");
    const applied = wrapper.emitted("apply")?.[0]?.[0] as { ref: string };
    expect(applied.ref).toBe("v1.0.0");
  });

  // 2026-09-08 fix: 行 header 由 <button> 改为 <div role="button">，
  // 徽章不再是嵌套 button；并锁定“点徽章不展开行 + 保留其它筛选字段”。
  it("keeps chips out of any nested <button> and does not expand the row on chip click", async () => {
    const wrapper = mountLog(["v1.0.0"]);
    const header = wrapper.find(".git-log-item-header");
    expect(header.element.tagName).toBe("DIV");
    expect(header.attributes("role")).toBe("button");
    expect(header.attributes("tabindex")).toBe("0");
    expect(wrapper.find("button .git-log-item-tag").exists()).toBe(false);

    await wrapper.find(".git-log-item-tag").trigger("click");
    expect(header.attributes("aria-expanded")).toBe("false");
    const applied = wrapper.emitted("apply")?.[0]?.[0] as Record<string, unknown>;
    expect(applied.ref).toBe("v1.0.0");
    // 其它筛选字段被保留（apply 载荷是 localFilter 的 spread，而非裸 { ref }）：
    // 默认 n=20 仍在，且没有多出 / 丢失字段。
    expect(applied.n).toBe(20);
    expect(Object.keys(applied).sort()).toEqual(["n", "ref"]);
  });

  it("toggles the row from the header via keyboard, and chip keys do not toggle", async () => {
    const wrapper = mountLog(["v1.0.0"]);
    const header = wrapper.find(".git-log-item-header");
    await header.trigger("keydown", { key: "Enter" });
    expect(header.attributes("aria-expanded")).toBe("true");
    await header.trigger("keydown", { key: " " });
    expect(header.attributes("aria-expanded")).toBe("false");

    // 键盘在徽章上按 Enter 只应筛选，不得冒泡触发行展开。
    await wrapper.find(".git-log-item-tag").trigger("keydown", { key: "Enter" });
    expect(header.attributes("aria-expanded")).toBe("false");
  });
});
