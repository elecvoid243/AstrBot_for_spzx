import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import ReasoningThinkContent from "./ReasoningThinkContent.vue";

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({
    tm: (key: string) => {
      const messages: Record<string, string> = {
        "reasoning.sanitizerFolded": "已折叠",
        "reasoning.sanitizerExpanded": "已展开",
        "reasoning.sanitizerTimes": "次重复",
        "reasoning.sanitizerRunTimes": "次连续重复行",
        "reasoning.sanitizerLinesPer": "行/次",
        "reasoning.sanitizerFallbackEyebrow": "整段兜底",
        "reasoning.sanitizerFallbackTitle": "重复思考已折叠",
        "reasoning.sanitizerLines": "行",
        "reasoning.sanitizerChars": "字符",
        "reasoning.sanitizerFoldTooltip":
          "保留首次出现，移除后续 {repeats} 次重复（每次 {period} 行）",
        "reasoning.sanitizerRunTooltip":
          "保留首次出现，移除后续 {repeats} 次连续重复行",
      };
      return messages[key] ?? key;
    },
  }),
}));

const STUBS = {
  MarkdownRender: {
    props: ["content"],
    template: '<div class="markdown-render">{{ content }}</div>',
  },
  MarkdownMessagePart: {
    props: ["content"],
    template: '<div class="markdown-message-part">{{ content }}</div>',
  },
};

function mountContent(
  content: string,
  extra: Record<string, unknown> = {},
) {
  return mount(ReasoningThinkContent, {
    props: { content, ...extra },
    global: { stubs: STUBS },
  });
}

describe("ReasoningThinkContent", () => {
  it("replaces a period marker with a fold rule", () => {
    const wrapper = mountContent(
      "before\n[context sanitizer: collapsed 7 x 5-line repeated block]\nafter",
    );

    expect(wrapper.find(".sanitizer-fold-rule").exists()).toBe(true);
    expect(wrapper.text()).toContain("before");
    expect(wrapper.text()).toContain("after");
    expect(wrapper.text()).not.toContain("[context sanitizer:");
  });

  it("hides the kept first occurrence before a period marker", () => {
    const wrapper = mountContent(
      "intro\nblock 1\nblock 2\nblock 3\n[context sanitizer: collapsed 2 x 3-line repeated block]\nafter",
    );

    expect(wrapper.text()).toContain("intro");
    expect(wrapper.text()).not.toContain("block 1");
    expect(wrapper.text()).toContain("after");
  });

  it("exposes the marker as a toggle when interactive", async () => {
    const wrapper = mountContent(
      "intro\nblock 1\nblock 2\nblock 3\n[context sanitizer: collapsed 2 x 3-line repeated block]\nafter",
      { originalContent: "original", interactive: true },
    );

    const marker = wrapper.get(".sanitizer-fold-rule");
    expect(marker.attributes("role")).toBe("button");
    expect(marker.attributes("tabindex")).toBe("0");

    await marker.trigger("click");

    expect(wrapper.emitted("toggle-original")).toHaveLength(1);
  });

  it("keeps the marker at its original position in the original view", async () => {
    const wrapper = mountContent(
      "intro\nblock 1\nblock 2\nblock 3\n[context sanitizer: collapsed 2 x 3-line repeated block]\nafter",
      {
        originalContent:
          "intro\nblock 1\nblock 2\nblock 3\nblock 1\nblock 2\nblock 3\nblock 1\nblock 2\nblock 3\nafter",
        showOriginal: true,
        interactive: true,
      },
    );

    expect(wrapper.text()).toContain("after");
    const marker = wrapper.get(".sanitizer-fold-rule");
    expect(marker.attributes("aria-pressed")).toBe("true");
    expect(marker.text()).toContain("已展开");
    const html = wrapper.html();
    expect(html.indexOf("intro")).toBeLessThan(
      html.indexOf("sanitizer-fold-rule"),
    );

    await marker.trigger("click");

    expect(wrapper.emitted("toggle-original")).toHaveLength(1);
  });

  it("replaces a fallback marker with a fallback block", () => {
    const wrapper = mountContent(
      "[context sanitizer: collapsed repetitive reasoning (1741 lines, 28231 chars)]",
    );

    expect(wrapper.find(".sanitizer-fallback-block").exists()).toBe(true);
    expect(wrapper.text()).toContain("1,741");
    expect(wrapper.text()).not.toContain("[context sanitizer:");
  });

  it("renders plain text without marker components", () => {
    const wrapper = mountContent("plain thought");

    expect(wrapper.find(".sanitizer-fold-rule").exists()).toBe(false);
    expect(wrapper.find(".sanitizer-fallback-block").exists()).toBe(false);
    expect(wrapper.text()).toContain("plain thought");
  });

  it("uses the inert markdown renderer when inert is set", () => {
    const wrapper = mountContent("plain thought", { inert: true });

    expect(wrapper.find(".markdown-message-part").exists()).toBe(true);
    expect(wrapper.find(".markdown-render").exists()).toBe(false);
  });
});
