import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import SanitizerFallbackBlock from "./SanitizerFallbackBlock.vue";

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({
    tm: (key: string) => {
      const messages: Record<string, string> = {
        "reasoning.sanitizerFallbackEyebrow": "整段兜底",
        "reasoning.sanitizerFallbackTitle": "重复思考已折叠",
        "reasoning.sanitizerLines": "行",
        "reasoning.sanitizerChars": "字符",
      };
      return messages[key] ?? key;
    },
  }),
}));

describe("SanitizerFallbackBlock", () => {
  it("renders the title and formatted counts", () => {
    const wrapper = mount(SanitizerFallbackBlock, {
      props: { lines: 1741, chars: 28231 },
    });

    expect(wrapper.text()).toContain("整段兜底");
    expect(wrapper.text()).toContain("重复思考已折叠");
    expect(wrapper.text()).toContain("1,741");
    expect(wrapper.text()).toContain("28,231");
    expect(wrapper.text()).toContain("行");
    expect(wrapper.text()).toContain("字符");
    expect(wrapper.attributes("title")).toBe("重复思考已折叠");
  });

  it("is a toggle when interactive", async () => {
    const wrapper = mount(SanitizerFallbackBlock, {
      props: { lines: 1741, chars: 28231, interactive: true },
    });

    expect(wrapper.attributes("role")).toBe("button");
    await wrapper.trigger("click");
    expect(wrapper.emitted("toggle")).toHaveLength(1);
  });
});
