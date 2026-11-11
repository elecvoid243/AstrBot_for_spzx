// Author: elecvoid243, 2026-10-03
import { describe, expect, it } from "vitest";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";
import ThinkingEffortChip from "./ThinkingEffortChip.vue";
import type { ThinkingEffortSliderConfig } from "@/composables/thinkingEffortSlider";

// Vuetify is not registered under test; only the pieces this chip touches
// are stubbed. The slider-mode interaction layer is a real native range
// input inside the component, so it needs no stub.
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

const LEVELS = [
  { name: "低", value: "low" },
  { name: "高", value: "high" },
  { name: "最高", value: "max" },
];

function mountChip(props: Record<string, unknown>) {
  return mount(ThinkingEffortChip, {
    props: { modelValue: "50", levels: [], ...props },
    global: { stubs },
  });
}

describe("ThinkingEffortChip (unified track)", () => {
  it("renders level presets as ordered stops instead of a row list", () => {
    const wrapper = mountChip({
      mode: "levels",
      modelValue: "low",
      levels: LEVELS,
    });
    expect(wrapper.find("input.effort-track__input").exists()).toBe(true);
    expect(
      wrapper.findAll(".effort-track__label").map((label) => label.text()),
    ).toEqual(["低", "高", "最高"]);
    expect(wrapper.find(".effort-chip-btn__label").text()).toBe("低");
  });

  it("snaps a level drag to the nearest stop", async () => {
    const wrapper = mountChip({
      mode: "levels",
      modelValue: "low",
      levels: LEVELS,
    });
    await wrapper.find("input.effort-track__input").setValue("2");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["max"]);
  });

  it("clips the gradient fill at the active stop", () => {
    const wrapper = mountChip({
      mode: "levels",
      modelValue: "high",
      levels: LEVELS,
    });
    expect(wrapper.find(".effort-track__fill").attributes("style")).toContain(
      "inset(0 50%",
    );
  });

  it("jumps to a level when its label is clicked", async () => {
    const wrapper = mountChip({
      mode: "levels",
      modelValue: "low",
      levels: LEVELS,
    });
    await wrapper.findAll(".effort-track__label")[1].trigger("click");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["high"]);
  });

  it("renders the continuous track with marker labels", () => {
    const wrapper = mountChip({ mode: "slider", slider: SLIDER });
    expect(wrapper.find("input.effort-track__input").exists()).toBe(true);
    expect(
      wrapper.findAll(".effort-track__label").map((label) => label.text()),
    ).toEqual(["low25", "high50", "xhigh75", "max100"]);
    // The end label aligns its outer edge with the track end (no overflow).
    expect(
      wrapper.findAll(".effort-track__label").at(-1)?.classes(),
    ).toContain("effort-track__label--edge-end");
  });

  it("shows the numeric readout and the alias it sits on", () => {
    const wrapper = mountChip({ mode: "slider", slider: SLIDER });
    expect(wrapper.find(".effort-chip-readout__value").text()).toBe("50");
    expect(wrapper.find(".effort-chip-readout__alias").text()).toBe("high");
    expect(wrapper.find(".effort-chip-btn__label").text()).toBe("50");
  });

  it("keeps a dragged value instead of pulling it onto an alias", async () => {
    // 48 sits 2 away from the "high" marker at 50; magnetism used to yank the
    // handle there, which made the controlled slider jitter under the pointer.
    const wrapper = mountChip({ modelValue: "37", mode: "slider", slider: SLIDER });
    await wrapper.find("input.effort-track__input").setValue("48");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["48"]);
  });

  it("reports a marker click as its numeric value", async () => {
    const wrapper = mountChip({ modelValue: "37", mode: "slider", slider: SLIDER });
    await wrapper.findAll(".effort-track__label")[2].trigger("click");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["75"]);
  });

  it("stays silent when the drag resolves to the current value", async () => {
    const wrapper = mountChip({ modelValue: "50", mode: "slider", slider: SLIDER });
    await wrapper.find("input.effort-track__input").setValue("50");
    expect(wrapper.emitted("update:modelValue")).toBeFalsy();
  });

  it("disables the track when a level preset has a single stop", () => {
    const wrapper = mountChip({
      mode: "levels",
      modelValue: "low",
      levels: [{ name: "低", value: "low" }],
    });
    expect(
      wrapper.find("input.effort-track__input").attributes("disabled"),
    ).toBeDefined();
    expect(wrapper.find(".effort-track__label--active").text()).toBe("低");
  });

  it("shows the active preset name under the title", () => {
    const wrapper = mountChip({ presetName: "DeepSeek-V4.1" });
    expect(wrapper.find(".effort-chip-preset").text()).toBe("DeepSeek-V4.1");
  });

  it("hides the edit row unless the caller may rewrite presets", () => {
    const editable = mountChip({ canEdit: true });
    expect(editable.find(".effort-chip-row--edit").exists()).toBe(true);

    // Read-only callers see the shapes and pick a value; the gear (and its
    // divider) simply is not there.
    const readonly = mountChip({});
    expect(readonly.find(".effort-chip-row--edit").exists()).toBe(false);
    expect(readonly.find(".effort-chip-divider").exists()).toBe(false);
  });
});
