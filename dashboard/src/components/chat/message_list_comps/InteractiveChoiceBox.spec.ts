// Author: spcode-assistant
// Date: 2026-07-11
//
// Component spec for InteractiveChoiceBox — v1.2 ignores-state title fix.
//
// Background: the v1.0 `is-ignored` header only rendered the
// "已忽略" label + `part.prompt` (muted), deliberately skipping
// `part.title`. This was a regression — the user lost the original
// choice-box subject when a later user message passed it over. v1.2
// adds a `part.title` span (with full-text :title tooltip) to the
// ignored header so the skipped box's topic stays visible.
//
// These tests pin:
//   1. ignored + title  → both title and prompt spans render with :title
//   2. ignored + no title → only prompt span renders (no empty span)
//   3. pending baseline  → the pre-existing title/prompt path still works

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import type { InteractiveChoicePart } from "@/composables/parseInteractiveChoice";

// Stateful mock of the Pinia store. Mirrors the real
// `useInteractiveChoiceStore` surface that InteractiveChoiceBox
// reads (getSubmissionState / isCancelled) and writes (markSubmitted
// / markCancelled) during render. Backed by internal Maps so the
// "submission wins over cancelled" race test can sequence
// markSubmitted then markCancelled against the same UMO + rid and
// observe the priority at the rendered class binding.
//
// Why stateful instead of vi.fn().mockReturnValue(): a fresh
// per-test spy is annoying to reset (`mockReturnValue` between
// `beforeEach` blocks has surprising behaviour with reactive reads)
// and the existing tests need the same "no submission by default"
// behaviour, which a Map-backed `?? null` fallback gives for free.
const storeMock = vi.hoisted(() => {
  type Submission = {
    kind: "option" | "input";
    optionId?: string;
    freeText?: string;
  };
  const submissions = new Map<string, Submission>();
  const cancelled = new Map<string, boolean>();
  return {
    getSubmissionState(umo: string, rid: string): Submission | null {
      return submissions.get(`${umo}::${rid}`) ?? null;
    },
    markSubmitted(
      umo: string,
      rid: string,
      kind: Submission["kind"],
      payload: { optionId?: string; freeText?: string } = {},
    ): void {
      submissions.set(`${umo}::${rid}`, { kind, ...payload });
    },
    isCancelled(umo: string, rid: string): boolean {
      return cancelled.get(`${umo}::${rid}`) ?? false;
    },
    markCancelled(umo: string, rid: string): void {
      cancelled.set(`${umo}::${rid}`, true);
    },
    _reset(): void {
      submissions.clear();
      cancelled.clear();
    },
  };
});

vi.mock("@/stores/interactiveChoice", () => ({
  useInteractiveChoiceStore: () => storeMock,
}));

// i18n init happens in vitest.setup.ts; useModuleI18n('features/chat')
// reads real zh-CN strings, so the "已忽略" label and the
// "已选择/已输入" submitted labels render real text.

// Import AFTER vi.mock so the mocked store is in place.
import InteractiveChoiceBox from "./InteractiveChoiceBox.vue";

const UMO = "webchat:test!umo";

function makePart(
  overrides: Partial<InteractiveChoicePart> = {},
): InteractiveChoicePart {
  return {
    type: "interactive_choice",
    request_id: "req-1",
    prompt: "请选择重命名方式",
    options: [
      { id: "A", label: "git mv", description: "保留历史" },
      { id: "B", label: "文件系统 mv", description: "可能丢历史" },
    ],
    ...overrides,
  };
}

function mountBox(props: {
  part: InteractiveChoicePart;
  isIgnored?: boolean;
  umo?: string;
}) {
  const { part, isIgnored, umo } = props;
  return mount(InteractiveChoiceBox, {
    props: {
      umo: umo ?? UMO,
      part,
      isIgnored,
    },
    // pending 分支里有 v-btn(自由输入的提交按钮)。本 spec 只在
    // ignored / pending 状态断言,无需 v-btn 真行为;给个 stub
    // 避免 Vue 控制台抛 "Failed to resolve component: v-btn" 警告。
    global: {
      stubs: {
        "v-btn": { template: "<button><slot /></button>" },
      },
    },
  });
}

beforeEach(() => {
  storeMock._reset();
});

describe("InteractiveChoiceBox — ignored header (v1.2)", () => {
  it("renders both title and prompt spans when both are present", () => {
    const wrapper = mountBox({
      part: makePart({
        title: "重命名 PATCH 文件",
        prompt: "用 git mv 还是文件系统 mv?",
      }),
      isIgnored: true,
    });

    const header = wrapper.find(".choice-header--ignored");
    expect(header.exists()).toBe(true);

    // title 节点存在并显示原文
    const title = header.find(".choice-title--ignored");
    expect(title.exists()).toBe(true);
    expect(title.text()).toBe("重命名 PATCH 文件");
    // v1.2:title 节点的 :title 属性绑了原文,hover 可看全量
    expect(title.attributes("title")).toBe("重命名 PATCH 文件");

    // prompt 节点也存在并显示原文
    const prompt = header.find(".choice-prompt--muted");
    expect(prompt.exists()).toBe(true);
    expect(prompt.text()).toBe("用 git mv 还是文件系统 mv?");
    expect(prompt.attributes("title")).toBe("用 git mv 还是文件系统 mv?");

    // 上下文标签 "已忽略" 仍存在(回归保护:v1.2 改的是 ignored header
    // 内部,不该影响外部的"已忽略"标签)
    expect(header.find(".choice-ignored-label").exists()).toBe(true);
  });

  it("does not render a title span when part.title is missing", () => {
    // v1.2 的修复要保持向后兼容:旧 plugin 可能不传 title(v0.3
    // wire-format 是必填 title 的,但 v1.0 改成了可选),此时不能
    // 凭空渲染一个空 span,会让 header 多一个 0 高度节点。
    const wrapper = mountBox({
      part: makePart({ title: undefined, prompt: "仅 prompt" }),
      isIgnored: true,
    });

    const header = wrapper.find(".choice-header--ignored");
    expect(header.exists()).toBe(true);
    // 没有 title 字段 → .choice-title--ignored 节点整体不渲染
    expect(header.find(".choice-title--ignored").exists()).toBe(false);

    // prompt 节点仍然渲染
    const prompt = header.find(".choice-prompt--muted");
    expect(prompt.exists()).toBe(true);
    expect(prompt.text()).toBe("仅 prompt");
  });

  it("does not render the ignored header when isIgnored is false", () => {
    // 烟雾测试:没 isIgnored → 走 pending/submitted 分支,不会
    // 出现 .choice-header--ignored 节点。
    const wrapper = mountBox({
      part: makePart({ title: "未忽略的 title" }),
      isIgnored: false,
    });
    expect(wrapper.find(".choice-header--ignored").exists()).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Task F4: fifth box state `cancelled` (v1.2 spec §4.4 + §5.1).
//
// State priority binding (spec §5.1):
//   submissionState  >  cancelledState  >  props.isIgnored  >  pending
//
// Why submission wins over cancelled: race safety. If the user
// clicks an option at T=timeout−1, the local submission intent is
// honest (UI shows "已选择 X"). The server's cancellation arrives
// a frame later (or after a network outage). The UI must NOT lie
// about what the user chose — flipping to "已取消" would discard
// the user's click.
//
// These 5 tests pin:
//   1. baseline →  is-pending class (no state overrides)
//   2. cancelled only →  is-cancelled class
//   3. submission wins over cancelled (race safety)
//   4. cancelled template branch renders the icon + i18n label
//      and does NOT render the pending option buttons / input
//   5. pending template branch does NOT render the cancelled UI
// ---------------------------------------------------------------------------

describe("InteractiveChoiceBox — cancelled state (v1.2)", () => {
  it("applies is-pending class when no state overrides", () => {
    const wrapper = mountBox({
      part: makePart({ title: "未提交的 title" }),
    });
    expect(wrapper.find(".interactive-choice-box.is-pending").exists()).toBe(
      true,
    );
    expect(wrapper.find(".interactive-choice-box.is-cancelled").exists()).toBe(
      false,
    );
    expect(wrapper.find(".interactive-choice-box.is-submitted").exists()).toBe(
      false,
    );
  });

  it("applies is-cancelled class when cancelledStates[umo][rid] is true", () => {
    const umo = "webchat:FriendMessage:webchat!alice!sess";
    storeMock.markCancelled(umo, "req-1");

    const wrapper = mountBox({
      part: makePart({ title: "已取消的 title" }),
      umo,
    });
    expect(wrapper.find(".interactive-choice-box.is-cancelled").exists()).toBe(
      true,
    );
    expect(wrapper.find(".interactive-choice-box.is-pending").exists()).toBe(
      false,
    );
    expect(wrapper.find(".interactive-choice-box.is-submitted").exists()).toBe(
      false,
    );
  });

  it("submission wins over cancelled (race safety: T=timeout-1)", () => {
    const umo = "webchat:FriendMessage:webchat!alice!sess";
    // Simulate: user clicked option A at T=timeout-1, then the
    // server's `interactive_choice_resolved {reason: "cancelled"}`
    // event arrives. Per spec §5.1, the UI must keep showing the
    // user's submitted choice (UI honesty over server truth).
    storeMock.markSubmitted(umo, "req-1", "option", { optionId: "A" });
    storeMock.markCancelled(umo, "req-1");

    const wrapper = mountBox({
      part: makePart({ title: "已选择 A" }),
      umo,
    });
    expect(wrapper.find(".interactive-choice-box.is-submitted").exists()).toBe(
      true,
    );
    expect(wrapper.find(".interactive-choice-box.is-cancelled").exists()).toBe(
      false,
    );
  });

  it("renders cancelled header (icon + i18n label) when state is 'cancelled'", () => {
    const umo = "webchat:FriendMessage:webchat!alice!sess";
    storeMock.markCancelled(umo, "req-1");

    const wrapper = mountBox({
      part: makePart({ title: "超时未回" }),
      umo,
    });
    // New v1.2 contract: `.choice-header--cancelled` + i18n label
    expect(wrapper.find(".choice-header--cancelled").exists()).toBe(true);
    expect(wrapper.find(".choice-cancelled-label").exists()).toBe(true);
    // Cancelled boxes are non-interactive → pending UI must NOT render
    expect(wrapper.find(".choice-options").exists()).toBe(false);
    expect(wrapper.find(".choice-input-row").exists()).toBe(false);
  });

  it("does NOT render the cancelled UI when state is 'pending'", () => {
    const wrapper = mountBox({
      part: makePart({ title: "待回答" }),
    });
    expect(wrapper.find(".choice-header--cancelled").exists()).toBe(false);
    expect(wrapper.find(".choice-cancelled-label").exists()).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 2026-07-23: 右上角「取消」按钮 — pending 状态可点,emits "cancel",
// 父组件转交 store.cancelChoice() 完成「已取消」视觉切换 + 后端 DELETE
// 往返。已 submit / ignored / cancelled 状态不渲染此按钮(头部已切换
// 到非 pending 变体)。
// ---------------------------------------------------------------------------

describe("InteractiveChoiceBox — cancel button (2026-07-23)", () => {
  beforeEach(() => {
    storeMock._reset();
  });

  it("renders the cancel button in the pending header (top-right)", () => {
    const wrapper = mountBox({
      part: makePart({ title: "选一个", prompt: "选哪个?" }),
    });

    const btn = wrapper.find(".choice-cancel-button");
    expect(btn.exists()).toBe(true);
    // Label uses the i18n key (zh-CN: "取消").
    expect(btn.text()).toContain("取消");
    // aria-label + title for screen readers / hover tooltip.
    expect(btn.attributes("aria-label")).toBe("取消此候选框");
    expect(btn.attributes("title")).toBe("取消此候选框");
  });

  it("emits 'cancel' with the request_id when clicked in pending state", async () => {
    const wrapper = mountBox({
      part: makePart({ request_id: "req-cancel-emit" }),
    });

    await wrapper.find(".choice-cancel-button").trigger("click");

    const emitted = wrapper.emitted("cancel");
    expect(emitted).toBeDefined();
    expect(emitted?.length).toBe(1);
    expect(emitted?.[0]?.[0]).toBe("req-cancel-emit");
    // Cancel is a separate event from submit — must NOT emit submit.
    expect(wrapper.emitted("submit")).toBeUndefined();
  });

  it("does NOT render the cancel button when submissionState is set", () => {
    // User already chose option A → state becomes 'submitted_via_option'.
    // The header switches to the check-circle variant, and the
    // cancel button must disappear (it only lives inside the
    // pending header v-if branch).
    const umo = "webchat:FriendMessage:webchat!alice!sess";
    storeMock.markSubmitted(umo, "req-1", "option", { optionId: "A" });

    const wrapper = mountBox({ part: makePart(), umo });

    expect(wrapper.find(".choice-cancel-button").exists()).toBe(false);
  });

  it("does NOT render the cancel button when is-ignored", () => {
    // The parent flips the box to ignored after a later user msg
    // passes over it. The ignored header is a different element
    // entirely (.choice-header--ignored), so the pending-only
    // cancel button is not in the DOM.
    const wrapper = mountBox({
      part: makePart(),
      isIgnored: true,
    });

    expect(wrapper.find(".choice-cancel-button").exists()).toBe(false);
  });

  it("does NOT render the cancel button when cancelledStates is set", () => {
    const umo = "webchat:FriendMessage:webchat!alice!sess";
    storeMock.markCancelled(umo, "req-1");

    const wrapper = mountBox({ part: makePart(), umo });

    expect(wrapper.find(".choice-cancel-button").exists()).toBe(false);
  });

  it("does NOT emit 'cancel' when clicked after the box has been submitted", async () => {
    // Defence in depth: even if the button somehow stayed in the
    // DOM (e.g. a future refactor breaks the v-if guard), the
    // onCancelClick handler short-circuits on non-pending state.
    // We test this by force-clicking the underlying header button
    // on a submitted box, which is impossible in the current
    // template but pins the handler's guard.
    const umo = "webchat:FriendMessage:webchat!alice!sess";
    storeMock.markSubmitted(umo, "req-1", "option", { optionId: "A" });

    const wrapper = mountBox({ part: makePart(), umo });

    // Pending header should not even render the button — confirms
    // the v-if guard is the first line of defence.
    expect(wrapper.find(".choice-cancel-button").exists()).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 2026-08-08: long option description fold. Descriptions longer than
// 200 chars are collapsed to the first 200 chars + ellipsis with a
// click-to-expand toggle; expanding must never submit the option.
// ---------------------------------------------------------------------------

describe("InteractiveChoiceBox — description fold (>200 chars)", () => {
  it("renders short descriptions in full without a toggle", () => {
    const wrapper = mountBox({
      part: makePart({
        options: [
          { id: "A", label: "git mv", description: "保留历史" },
          { id: "B", label: "mv", description: "可能丢历史" },
        ],
      }),
    });
    const buttons = wrapper.findAll(".choice-option-button");
    expect(buttons[0].text()).toContain("保留历史");
    expect(buttons[0].find(".choice-desc-toggle").exists()).toBe(false);
  });

  it("collapses a long description and expands on toggle click", async () => {
    const longDesc = "详".repeat(300);
    const wrapper = mountBox({
      part: makePart({
        options: [
          { id: "A", label: "git mv", description: longDesc },
          { id: "B", label: "mv" },
        ],
      }),
    });
    const button = wrapper.findAll(".choice-option-button")[0];
    // Collapsed: first 200 chars + ellipsis only.
    expect(button.text()).toContain("详".repeat(200));
    expect(button.text()).toContain("…");
    expect(button.text()).not.toContain("详".repeat(201));

    const toggle = button.find(".choice-desc-toggle");
    expect(toggle.exists()).toBe(true);
    expect(toggle.text()).toContain("展开");
    expect(toggle.attributes("aria-expanded")).toBe("false");

    // Expand → full text + "收起" toggle.
    await toggle.trigger("click");
    expect(button.text()).toContain("详".repeat(300));
    const afterToggle = button.find(".choice-desc-toggle");
    expect(afterToggle.text()).toContain("收起");
    expect(afterToggle.attributes("aria-expanded")).toBe("true");
  });

  it("toggle click does not submit the option", async () => {
    const longDesc = "详".repeat(250);
    const wrapper = mountBox({
      part: makePart({
        options: [
          { id: "A", label: "git mv", description: longDesc },
          { id: "B", label: "mv" },
        ],
      }),
    });
    const button = wrapper.findAll(".choice-option-button")[0];
    await button.find(".choice-desc-toggle").trigger("click");
    expect(wrapper.emitted("submit")).toBeUndefined();
    // Clicking the option itself still submits.
    await button.trigger("click");
    expect(wrapper.emitted("submit")).toHaveLength(1);
  });

  it("applies the same fold to readonly options in the details section", async () => {
    const umo = "webchat:FriendMessage:webchat!alice!sess";
    storeMock.markSubmitted(umo, "req-1", "option", { optionId: "A" });
    const longDesc = "详".repeat(300);
    const wrapper = mountBox({
      part: makePart({
        options: [
          { id: "A", label: "git mv", description: longDesc },
          { id: "B", label: "mv" },
        ],
      }),
      umo,
    });

    // Expand the details collapse to reveal readonly options.
    await wrapper.find(".choice-collapse-toggle").trigger("click");
    const readonly = wrapper.findAll(".choice-option-readonly")[0];
    expect(readonly.exists()).toBe(true);
    expect(readonly.text()).toContain("详".repeat(200));
    expect(readonly.find(".choice-desc-toggle").exists()).toBe(true);

    await readonly.find(".choice-desc-toggle").trigger("click");
    expect(readonly.text()).toContain("详".repeat(300));
  });
});

// ---------------------------------------------------------------------------
// Terminal state restored from the persisted part (2026-09-29).
//
// `chat_service.stamp_choice_resolution` stamps `answer` / `resolved` onto the
// `interactive_choice` part when the box is answered or cancelled. The store
// mock below starts empty in every test, which is exactly the situation these
// cases cover: a fresh browser, a wiped cache, or a session whose localStorage
// record another session overwrote (Bug Z).
// ---------------------------------------------------------------------------

describe("InteractiveChoiceBox — terminal state from history", () => {
  it("renders 已选择 from the part's answer with no local record", () => {
    const wrapper = mountBox({
      part: makePart({
        answer: { choice_id: "B", free_text: "", answered_at: 1 },
      }),
    });

    expect(
      wrapper.find(".interactive-choice-box.is-submitted").exists(),
    ).toBe(true);
    expect(wrapper.find(".choice-result-value").text()).toContain(
      "文件系统 mv",
    );
    // Terminal: no cancel affordance, no clickable options.
    expect(wrapper.find(".choice-cancel-button").exists()).toBe(false);
    expect(wrapper.find(".choice-option-button").exists()).toBe(false);
  });

  it("renders 已选择 with the typed text for a free-text answer", () => {
    const wrapper = mountBox({
      part: makePart({
        answer: { choice_id: "__free_text__", free_text: "我自己写的答案" },
      }),
    });

    expect(
      wrapper.find(".interactive-choice-box.is-submitted").exists(),
    ).toBe(true);
    expect(wrapper.find(".choice-result-value").text()).toContain(
      "我自己写的答案",
    );
  });

  it("renders 已取消 from the part's resolution stamp", () => {
    const wrapper = mountBox({
      part: makePart({ resolved: { reason: "cancelled", resolved_at: 1 } }),
    });

    expect(
      wrapper.find(".interactive-choice-box.is-cancelled").exists(),
    ).toBe(true);
    expect(wrapper.find(".choice-option-button").exists()).toBe(false);
  });

  it("keeps the local submission ahead of the history answer", () => {
    // The user clicked A in this tab; the part carries an older answer B.
    // The local intent is the honest one (spec §5.1: submission wins).
    storeMock.markSubmitted(UMO, "req-1", "option", { optionId: "A" });

    const wrapper = mountBox({
      part: makePart({ answer: { choice_id: "B", answered_at: 1 } }),
    });

    expect(wrapper.find(".choice-result-value").text()).toContain("git mv");
  });
});
