import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import SanitizerFoldRule from "./SanitizerFoldRule.vue";

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({
    tm: (key: string) => {
      const messages: Record<string, string> = {
        "reasoning.sanitizerFolded": "已折叠",
        "reasoning.sanitizerExpanded": "已展开",
        "reasoning.sanitizerTimes": "次重复",
        "reasoning.sanitizerRunTimes": "次连续重复行",
        "reasoning.sanitizerLinesPer": "行/次",
        "reasoning.sanitizerFoldTooltip":
          "保留首次出现，移除后续 {repeats} 次重复（每次 {period} 行）",
        "reasoning.sanitizerRunTooltip":
          "保留首次出现，移除后续 {repeats} 次连续重复行",
      };
      return messages[key] ?? key;
    },
  }),
}));

describe("SanitizerFoldRule", () => {
  it("renders the period counts and a trailing rule", () => {
    const wrapper = mount(SanitizerFoldRule, {
      props: { kind: "period", repeats: 7, period: 5 },
    });

    expect(wrapper.text()).toContain("已折叠");
    expect(wrapper.text()).toContain("7");
    expect(wrapper.text()).toContain("次重复");
    expect(wrapper.text()).toContain("5");
    expect(wrapper.text()).toContain("行/次");
    expect(wrapper.find(".sanitizer-fold-rule__line").exists()).toBe(true);
    expect(wrapper.attributes("title")).toContain("7");
    expect(wrapper.attributes("title")).toContain("5");
  });

  it("renders the run variant without the per-line wording", () => {
    const wrapper = mount(SanitizerFoldRule, {
      props: { kind: "run", repeats: 8, period: 1 },
    });

    expect(wrapper.text()).toContain("8");
    expect(wrapper.text()).toContain("次连续重复行");
    expect(wrapper.text()).not.toContain("行/次");
  });

  it("is a keyboard-operable toggle when interactive", async () => {
    const wrapper = mount(SanitizerFoldRule, {
      props: { kind: "period", repeats: 7, period: 5, interactive: true },
    });

    expect(wrapper.attributes("role")).toBe("button");
    expect(wrapper.attributes("tabindex")).toBe("0");
    expect(wrapper.attributes("aria-pressed")).toBe("false");

    await wrapper.trigger("click");
    expect(wrapper.emitted("toggle")).toHaveLength(1);

    await wrapper.trigger("keydown.enter");
    expect(wrapper.emitted("toggle")).toHaveLength(2);
  });

  it("marks the expanded state", () => {
    const wrapper = mount(SanitizerFoldRule, {
      props: {
        kind: "period",
        repeats: 7,
        period: 5,
        interactive: true,
        active: true,
      },
    });

    expect(wrapper.attributes("aria-pressed")).toBe("true");
    expect(wrapper.classes()).toContain("is-active");
    expect(wrapper.text()).toContain("已展开");
  });
});
