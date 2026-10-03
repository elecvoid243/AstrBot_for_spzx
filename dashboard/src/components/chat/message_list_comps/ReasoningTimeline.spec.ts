// Tests for the ReasoningTimeline pinned "file changes" section and
// the scrollToFile locate mechanism (2026-08-11 file-change
// visibility feature).
//
// Extended 2026-09-17 (elecvoid243) with the message-search reveal:
// scrollToText lands on the timeline entry whose thinking text holds the
// keyword the user searched for.
//
// Author: elecvoid243 | 2026-08-11

import { describe, it, expect, vi } from "vitest";
import { reactive } from "vue";
import { mount } from "@vue/test-utils";
import ReasoningTimeline from "./ReasoningTimeline.vue";

const anchorBottomMock = vi.hoisted(() => vi.fn());
vi.mock("@/utils/scrollAnchor", () => ({
  anchorBottom: anchorBottomMock,
  findScrollableAncestor: vi.fn(),
}));

const STUBS = {
  FileChangeCard: {
    name: "FileChangeCard",
    props: ["entry", "isDark"],
    template: "<div class='file-change-card-stub' :data-call-id='entry.callId' />",
  },
  ToolCallCard: { template: "<div />" },
  ToolCallItem: { template: "<div><slot name='label' /><slot name='details' /></div>" },
  IPythonToolBlock: { template: "<div />" },
  MarkdownRender: { props: ["content"], template: "<div>{{ content }}</div>" },
  "v-icon": { template: "<i />" },
};

const EDIT_CALL = {
  id: "c1",
  name: "astrbot_file_edit_tool",
  args: { path: "F:\\proj\\a.py" },
  result:
    "Edited F:\\proj\\a.py. Replaced 1 occurrence(s) of the target text.\n\nDiff:\n```diff\n@@ -1 +1 @@\n-old\n+new\n```",
  finished_ts: 2,
};

const SHELL_CALL = {
  id: "s1",
  name: "astrbot_execute_shell",
  args: { command: "dir" },
  result: "{}",
  finished_ts: 3,
};

function mountTimeline(parts: unknown[]) {
  return mount(ReasoningTimeline, {
    props: { parts: parts as never, isDark: false },
    global: { stubs: STUBS },
  });
}

describe("ReasoningTimeline pinned file-change section", () => {
  it("renders one pinned card per file change above the timeline", () => {
    const wrapper = mountTimeline([
      { type: "think", think: "hmm" },
      { type: "tool_call", tool_calls: [EDIT_CALL, SHELL_CALL] },
    ]);
    const pinned = wrapper.find(".file-change-pinned");
    expect(pinned.exists()).toBe(true);
    const cards = pinned.findAll(".file-change-card-stub");
    expect(cards).toHaveLength(1);
    expect(cards[0].attributes("data-call-id")).toBe("c1");
    // timeline entries themselves remain untouched (think + 2 tools)
    expect(wrapper.findAll(".reasoning-timeline-item")).toHaveLength(3);
  });

  it("omits the pinned section when no file change exists", () => {
    const wrapper = mountTimeline([
      { type: "tool_call", tool_calls: [SHELL_CALL] },
    ]);
    expect(wrapper.find(".file-change-pinned").exists()).toBe(false);
  });

  it("scrollToFile scrolls the matching card into view", async () => {
    const scrollSpy = vi.fn();
    Element.prototype.scrollIntoView = scrollSpy;
    const wrapper = mountTimeline([
      { type: "tool_call", tool_calls: [EDIT_CALL] },
    ]);
    await (
      wrapper.vm as unknown as { scrollToFile: (id: string) => Promise<void> }
    ).scrollToFile("c1");
    expect(scrollSpy).toHaveBeenCalledTimes(1);
  });
});

describe("ReasoningTimeline keyword locate", () => {
  /** The exposed scrollToText, typed for the test that drives it. */
  function scrollToText(wrapper: ReturnType<typeof mountTimeline>) {
    return (
      wrapper.vm as unknown as {
        scrollToText: (text: string) => Promise<void>;
      }
    ).scrollToText;
  }

  it("lands on the thinking entry that holds the keyword", async () => {
    const scrollSpy = vi.fn();
    Element.prototype.scrollIntoView = scrollSpy;
    const wrapper = mountTimeline([
      { type: "think", think: "first thought" },
      { type: "think", think: "the NEEDLE is here" },
    ]);
    await scrollToText(wrapper)("needle");
    expect(scrollSpy).toHaveBeenCalledTimes(1);
    const items = wrapper.findAll(".reasoning-timeline-item");
    expect(items[0].classes()).not.toContain("reasoning-match-flash");
    expect(items[1].classes()).toContain("reasoning-match-flash");
  });

  it("scrolls nowhere when no entry holds the keyword", async () => {
    const scrollSpy = vi.fn();
    Element.prototype.scrollIntoView = scrollSpy;
    const wrapper = mountTimeline([{ type: "think", think: "first thought" }]);
    await scrollToText(wrapper)("absent");
    expect(scrollSpy).not.toHaveBeenCalled();
    expect(wrapper.find(".reasoning-match-flash").exists()).toBe(false);
  });
});

describe("ReasoningTimeline think live preview", () => {
  const HEAD = `UNIQUE-HEAD ${"h".repeat(120)}`;
  const TAIL = `UNIQUE-TAIL ${"t".repeat(120)}`;
  const LONG_THINK = `${HEAD}\n${"m".repeat(40)}\n${TAIL}`;

  function mountCollapsible(
    parts: unknown[],
    extraProps: Record<string, unknown> = {},
  ) {
    return mount(ReasoningTimeline, {
      props: {
        parts: parts as never,
        isDark: false,
        collapseThink: true,
        ...extraProps,
      },
      global: { stubs: STUBS },
    });
  }

  it("shows a clamped tail preview of the thinking, not the head", () => {
    const wrapper = mountCollapsible([{ type: "think", think: LONG_THINK }]);
    const preview = wrapper.find("[data-testid='think-preview']");
    expect(preview.exists()).toBe(true);
    expect(preview.text()).toContain("UNIQUE-TAIL");
    expect(preview.text()).not.toContain("UNIQUE-HEAD");
  });

  it("keeps short think entries fully visible without an expand affordance", () => {
    const wrapper = mountCollapsible([{ type: "think", think: "short thought" }]);
    const preview = wrapper.find("[data-testid='think-preview']");
    expect(preview.text()).toContain("short thought");
    expect(preview.text()).not.toContain("…");
    expect(wrapper.find("[data-testid='think-expand-label']").exists()).toBe(
      false,
    );
  });

  it("expands to full markdown on click and collapses back", async () => {
    const wrapper = mountCollapsible([{ type: "think", think: LONG_THINK }]);
    await wrapper.find("[data-testid='think-preview']").trigger("click");
    expect(wrapper.text()).toContain("UNIQUE-HEAD");
    await wrapper
      .find("[data-testid='think-collapse-toggle']")
      .trigger("click");
    expect(wrapper.text()).not.toContain("UNIQUE-HEAD");
  });

  it("throttles the live preview to a 2s tick while streaming", async () => {
    vi.useFakeTimers();
    try {
      const parts = reactive([{ type: "think", think: "first tail" }]);
      const wrapper = mountCollapsible(parts, { isStreaming: true });
      const preview = wrapper.find("[data-testid='think-preview']");
      expect(preview.text()).toContain("first tail");

      // Reducer-style in-place mutation: newer thinking arrives.
      (parts[0] as { think: string }).think = "second tail";
      await wrapper.vm.$nextTick();
      // Before the next tick the preview still shows the old tail.
      expect(
        wrapper.find("[data-testid='think-preview']").text(),
      ).toContain("first tail");

      await vi.advanceTimersByTimeAsync(2100);
      expect(
        wrapper.find("[data-testid='think-preview']").text(),
      ).toContain("second tail");
    } finally {
      vi.useRealTimers();
    }
  });

  it("collapses the streaming entry too once the run ends", async () => {
    const parts = reactive([{ type: "think", think: LONG_THINK }]);
    const wrapper = mountCollapsible(parts, { isStreaming: true });
    expect(wrapper.find("[data-testid='think-preview']").exists()).toBe(true);
    await wrapper.setProps({ isStreaming: false });
    // Same collapsed form after completion; still the tail, still clamped.
    const preview = wrapper.find("[data-testid='think-preview']");
    expect(preview.exists()).toBe(true);
    expect(preview.text()).toContain("UNIQUE-TAIL");
    expect(preview.text()).not.toContain("UNIQUE-HEAD");
  });

  it("renders full think content when collapseThink is not set", () => {
    const wrapper = mountTimeline([{ type: "think", think: LONG_THINK }]);
    expect(wrapper.find("[data-testid='think-preview']").exists()).toBe(false);
    expect(wrapper.text()).toContain("UNIQUE-HEAD");
  });

  it("anchors the entry bottom when collapsing via the toggle", async () => {
    const wrapper = mountCollapsible([{ type: "think", think: LONG_THINK }]);
    await wrapper.find("[data-testid='think-preview']").trigger("click");
    anchorBottomMock.mockClear();

    await wrapper
      .find("[data-testid='think-collapse-toggle']")
      .trigger("click");

    expect(anchorBottomMock).toHaveBeenCalledTimes(1);
    const [el, beforeBottom] = anchorBottomMock.mock.calls[0] as [
      HTMLElement,
      number,
    ];
    expect(el.classList.contains("reasoning-step")).toBe(true);
    expect(typeof beforeBottom).toBe("number");
  });

  it("never shows the collapse toggle without collapseThink (main agent)", () => {
    const wrapper = mountTimeline([{ type: "think", think: LONG_THINK }]);
    expect(
      wrapper.find("[data-testid='think-collapse-toggle']").exists(),
    ).toBe(false);
  });
});
