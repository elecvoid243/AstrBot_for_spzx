import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { initI18n } from "@/i18n/composables";
import WorkflowExampleGuide from "./WorkflowExampleGuide.vue";

const stubs = {
  "v-icon": true,
  "v-btn": { props: ["disabled"], template: '<button :disabled="disabled"><slot /></button>' },
};

describe("Release brief walkthrough", () => {
  beforeAll(() => initI18n("en-US"));
  afterEach(() => {
    vi.useRealTimers();
  });

  it("waits for playback and stops at human review until the reader continues", async () => {
    vi.useFakeTimers();
    const wrapper = mount(WorkflowExampleGuide, { global: { stubs } });
    try {
      const caption = wrapper.get('[data-testid="release-guide-step"]');
      const play = wrapper.get('[data-testid="release-guide-play"]');
      await vi.advanceTimersByTimeAsync(60000);
      expect(caption.attributes("data-step")).toBe("canvas");
      expect(caption.text()).toContain("Open canvas");
      expect(wrapper.get('[data-testid="release-guide-previous"]').attributes("disabled")).toBeDefined();

      await play.trigger("click");
      await vi.advanceTimersToNextTimerAsync();
      expect(caption.attributes("data-step")).toBe("start");
      expect(caption.text()).toContain("Ready for release.");
      expect(wrapper.findAll(".guide-wire.is-active")).toHaveLength(1);
      await vi.advanceTimersToNextTimerAsync();
      expect(caption.text()).toContain("Open in Chat");
      await vi.advanceTimersByTimeAsync(60000);
      expect(caption.attributes("data-step")).toBe("review");
      expect(caption.text()).toContain("Approve this version");
      expect(wrapper.find(".guide-binding.is-active").exists()).toBe(false);
      expect(play.text()).toBe("Play walkthrough");

      await wrapper.get('[data-testid="release-guide-next"]').trigger("click");
      expect(caption.attributes("data-step")).toBe("result");
      expect(wrapper.get(".guide-binding.is-active").text()).toContain("project · summary");
      expect(wrapper.get('[data-testid="release-guide-next"]').attributes("disabled")).toBeDefined();
      await play.trigger("click");
      expect(caption.attributes("data-step")).toBe("canvas");
      expect(play.text()).toBe("Pause walkthrough");
      await play.trigger("click");
      await vi.advanceTimersByTimeAsync(60000);
      expect(caption.attributes("data-step")).toBe("canvas");
    } finally {
      wrapper.unmount();
    }
  });

  it.each([
    { kind: "team" as const, stages: ["configure", "goal", "delegate", "collect", "monitor"], title: "Editorial check" },
    { kind: "role" as const, stages: ["responsibility", "resources", "publish", "reuse"], title: "Fixed version" },
  ])("animates the $kind example through its own stages and replays", async ({ kind, stages, title }) => {
    vi.useFakeTimers();
    const wrapper = mount(WorkflowExampleGuide, { props: { kind }, global: { stubs } });
    try {
      const step = wrapper.get('[data-testid="release-guide-step"]');
      expect(wrapper.get('[data-testid="release-guide-graph"]').text()).toContain(title);
      expect(step.attributes("data-step")).toBe(stages[0]);
      await wrapper.get('[data-testid="release-guide-play"]').trigger("click");
      for (const stage of stages.slice(1)) {
        await vi.advanceTimersToNextTimerAsync();
        expect(step.attributes("data-step")).toBe(stage);
      }
      expect(wrapper.find(".guide-binding").exists()).toBe(false);
      expect(wrapper.get('[data-testid="release-guide-next"]').attributes("disabled")).toBeDefined();
      await wrapper.get('[data-testid="release-guide-play"]').trigger("click");
      expect(step.attributes("data-step")).toBe(stages[0]);
    } finally { wrapper.unmount(); }
  });

  it("supports keyboard navigation, pauses when stepped manually, and cancels playback on close", async () => {
    vi.useFakeTimers();
    const wrapper = mount(WorkflowExampleGuide, { global: { stubs } });
    try {
      const play = wrapper.get('[data-testid="release-guide-play"]');
      const caption = wrapper.get('[data-testid="release-guide-step"]');
      await play.trigger("click");
      await play.trigger("keydown", { key: "ArrowRight" });
      await vi.advanceTimersByTimeAsync(60000);
      expect(caption.attributes("data-step")).toBe("start");
      await play.trigger("keydown", { key: "ArrowLeft" });
      expect(caption.attributes("data-step")).toBe("canvas");
      await play.trigger("click");
      expect(vi.getTimerCount()).toBe(1);
    } finally {
      wrapper.unmount();
    }
    await nextTick();
    expect(vi.getTimerCount()).toBe(0);
  });
});
