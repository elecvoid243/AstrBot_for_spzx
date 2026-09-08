// Author: impl_b4 @ 2026-09-08
// Spec: 2026-09-08-git-log-tags-and-filters §2.4 — tag 徽章渲染与点击筛选。
// 沿用 GitLogView.branchPicker.spec.ts 的 heavy-stub 策略：只断言徽章与 emit。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
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

function baseProps(tags: string[]) {
  return {
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
    gitStats: {
      state: { value: { kind: "idle" } },
      refresh: () => {},
    } as never,
    statsOpen: false,
    range: { kind: "preset", preset: "1w" } as never,
    topFilesLimit: 10,
    refItems: [],
    currentBranch: "main",
    activeRef: "HEAD",
  };
}

/** 2026-09-08: overrides 允许单个用例只替换 state / activeRef /
 *  appliedGrep 等个别 prop，而不必复制整份 props 样板。
 *  attachTo 供滚动相关用例把组件真正挂到 document 上（watch 回调里
 *  用 document.querySelector 查找根节点）。 */
function mountLog(
  tags: string[],
  overrides: Record<string, unknown> = {},
  attachTo?: Element,
) {
  return mount(GitLogView, {
    props: { ...baseProps(tags), ...overrides } as never,
    global: { stubs },
    ...(attachTo ? { attachTo } : {}),
  });
}

// GitLogView 的 useOpenOnDisk → useToast 需要 active pinia；提到模块作用域
// 供本文件所有 describe 共用（hash focus / 空态用例同样要挂载组件）。
beforeEach(() => {
  setActivePinia(createPinia());
});

// 滚动相关用例 spy 了 Element.prototype.scrollIntoView，逐例还原，避免泄漏。
afterEach(() => {
  vi.restoreAllMocks();
});

describe("GitLogView tag chips", () => {
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

describe("GitLogView hash focus", () => {
  // 2026-09-08 (spec §2.5): 用户输入 hash 时后端回显 resolvedRef，
  // 复用 focusedCommitSha 的 is-focused 链路。
  // 注意：`is-focused` 类挂在行容器 .git-log-item 上（与 CSS 规则
  // `.git-log-item.is-focused` 一致），不是 header 元素。
  it("focuses the resolved commit when the applied ref is a hash", () => {
    const state = makeState([]);
    state.snapshot.resolvedRef = "a".repeat(40);
    const wrapper = mountLog([], {
      state: state as never,
      activeRef: "aaaaaaaa",
    });
    const row = wrapper.find(".git-log-item");
    expect(row.attributes("data-commit-sha")).toBe("a".repeat(40));
    expect(row.classes()).toContain("is-focused");
  });

  it("does not focus anything for a non-hash ref", () => {
    const state = makeState([]);
    state.snapshot.resolvedRef = "a".repeat(40);
    const wrapper = mountLog([], {
      state: state as never,
      activeRef: "main",
    });
    expect(wrapper.find(".git-log-item").classes()).not.toContain("is-focused");
  });

  // 2026-09-08 fix: 历史轮询每 10s 用新对象替换 props.state。watcher 源
  // 必须是 getter 数组（逐元素比较）；若返回新数组，则每次轮询都被判定
  // 为「变化」，回调会把用户拽回高亮行。
  it("does not re-scroll when polling replaces state with the same focus", async () => {
    const scrollSpy = vi.spyOn(Element.prototype, "scrollIntoView");
    const state = makeState([]);
    state.snapshot.resolvedRef = "a".repeat(40);
    const wrapper = mountLog(
      [],
      { state: state as never, activeRef: "aaaaaaaa" },
      document.body,
    );

    // 模拟一次轮询：同 kind、同 resolvedRef，但对象引用是新的。
    const replacement = makeState([]);
    replacement.snapshot.resolvedRef = "a".repeat(40);
    await wrapper.setProps({ state: replacement as never });
    await flushPromises();

    expect(scrollSpy).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("scrolls when the focused commit genuinely changes", async () => {
    const scrollSpy = vi.spyOn(Element.prototype, "scrollIntoView");
    const wrapper = mountLog([], {}, document.body);

    await wrapper.setProps({ focusedCommitSha: "a".repeat(40) });
    await flushPromises();

    expect(scrollSpy).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });
});

describe("GitLogView no-match empty state", () => {
  // 2026-09-08 (spec §2.6): 已应用 grep 且无结果时用专门的空态文案，
  // 与「仓库暂无提交」（history.empty）区分开。
  it("shows the no-match text when an applied grep yields no commits", () => {
    const state = makeState([]);
    state.snapshot.commits = [];
    const wrapper = mountLog([], {
      state: state as never,
      appliedGrep: "zzz",
    });
    expect(wrapper.find(".git-log-center-text").text()).toBe("没有匹配的提交");
  });

  it("keeps the generic empty text when no grep is applied", () => {
    const state = makeState([]);
    state.snapshot.commits = [];
    const wrapper = mountLog([], { state: state as never });
    expect(wrapper.find(".git-log-center-text").text()).toBe("暂无提交记录");
  });

  // 2026-09-08 fix: 空态分支必须同时判定 commits.length === 0，否则
  // grep 生效且有结果时 no-match 分支会吞掉整个列表。
  it("keeps rendering the list when a grep is applied and matches exist", () => {
    const wrapper = mountLog(["v1.0.0"], { appliedGrep: "zzz" });
    expect(wrapper.find(".git-log-list").exists()).toBe(true);
    expect(wrapper.find(".git-log-item").exists()).toBe(true);
    expect(wrapper.find(".git-log-center").exists()).toBe(false);
  });
});
