// Author: elecvoid243, 2026-10-03
import { describe, expect, it } from "vitest";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";
import ThinkingEffortChip from "./ThinkingEffortChip.vue";
import type { ThinkingEffortSliderConfig } from "@/composables/thinkingEffortSlider";

// Vuetify is not registered under test; only the pieces this chip touches
// are stubbed, and the slider stub emits like the real component does.
const stubs = {
  "v-menu": defineComponent({
    name: "VMenu",
    setup(_, { slots }) {
      return () =>
        h("div", { class: "v-menu" }, [
          slots.activator?.({ props: {} }),
          slots.default?.(),
        ]);
    },
  }),
  "v-card": defineComponent({
    name: "VCard",
    setup(_, { slots }) {
      return () => h("div", { class: "v-card" }, slots.default?.());
    },
  }),
  "v-card-text": defineComponent({
    name: "VCardText",
    setup(_, { slots }) {
      return () => h("div", { class: "v-card-text" }, slots.default?.());
    },
  }),
  "v-slider": defineComponent({
    name: "VSlider",
    props: { modelValue: { type: Number, default: 0 } },
    emits: ["update:modelValue"],
    setup(props, { emit }) {
      return () =>
        h("input", {
          class: "slider-stub",
          type: "range",
          value: String(props.modelValue),
          onInput: (event: Event) =>
            emit(
              "update:modelValue",
              Number((event.target as HTMLInputElement).value),
            ),
        });
    },
  }),
};

const SLIDER: ThinkingEffortSliderConfig = {
  min: 1,
  max: 100,
  step: 1,
  snaps: [
    { name: "low", value: 25 },
    { name: "high", value: 50 },
    { name: "xhigh", value: 75 },
    { name: "max", value: 100 },
  ],
};

function mountChip(props: Record<string, unknown>) {
  return mount(ThinkingEffortChip, {
    props: { modelValue: "50", levels: [], ...props },
    global: { stubs },
  });
}

describe("ThinkingEffortChip (slider mode)", () => {
  it("renders the track and the snap pills instead of the level list", () => {
    const wrapper = mountChip({ mode: "slider", slider: SLIDER });
    expect(wrapper.find("input.slider-stub").exists()).toBe(true);
    const pills = wrapper.findAll(".effort-snap-btn");
    expect(pills.map((pill) => pill.text())).toEqual([
      "low25",
      "high50",
      "xhigh75",
      "max100",
    ]);
  });

  it("labels the current value with the alias it sits on", () => {
    const wrapper = mountChip({ mode: "slider", slider: SLIDER });
    expect(wrapper.find(".effort-slider-readout").text()).toBe("high 50");
    expect(wrapper.find(".effort-chip-btn__label").text()).toBe("high 50");
  });

  it("keeps the dragged value instead of pulling it onto an alias", async () => {
    // 48 sits 2 away from the "high" alias at 50; magnetism used to yank the
    // handle there, which made the controlled slider jitter under the pointer.
    const wrapper = mountChip({ modelValue: "37", mode: "slider", slider: SLIDER });
    await wrapper.find("input.slider-stub").setValue("48");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["48"]);
  });

  it("reports the value unchanged when the pointer lands on an alias", async () => {
    const wrapper = mountChip({ modelValue: "37", mode: "slider", slider: SLIDER });
    await wrapper.find("input.slider-stub").setValue("50");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["50"]);
  });

  it("keeps free values that are not near an alias", async () => {
    const wrapper = mountChip({ mode: "slider", slider: SLIDER });
    await wrapper.find("input.slider-stub").setValue("37");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["37"]);
  });

  it("jumps onto a snap point when a pill is clicked", async () => {
    const wrapper = mountChip({ mode: "slider", slider: SLIDER });
    await wrapper.findAll(".effort-snap-btn")[2].trigger("click");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["75"]);
    expect(wrapper.find(".effort-snap-btn--active").exists()).toBe(true);
  });

  it("stays silent when the drag resolves to the current value", async () => {
    const wrapper = mountChip({ modelValue: "50", mode: "slider", slider: SLIDER });
    await wrapper.find("input.slider-stub").setValue("50");
    expect(wrapper.emitted("update:modelValue")).toBeFalsy();
  });

  it("still renders the level rows in levels mode", () => {
    const wrapper = mountChip({
      mode: "levels",
      levels: [{ name: "低", value: "low" }],
    });
    expect(wrapper.find("input.slider-stub").exists()).toBe(false);
    expect(wrapper.find(".effort-chip-row__label").text()).toBe("低");
  });
});
