// Author: elecvoid243, 2026-10-03
import { describe, expect, it } from "vitest";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";
import ThinkingEffortLevelsDialog from "./ThinkingEffortLevelsDialog.vue";
import { DEFAULT_THINKING_EFFORT_SLIDER } from "@/composables/thinkingEffortSlider";

// Minimal Vuetify stand-ins: enough structure for the template to render
// and for v-model to round-trip through the stubbed inputs.
const passthrough = (name: string) =>
  defineComponent({
    name,
    setup(_, { slots }) {
      return () => h("div", { class: name }, slots.default?.());
    },
  });

const stubs = {
  "v-dialog": defineComponent({
    name: "VDialog",
    props: { modelValue: Boolean },
    setup(_, { slots }) {
      return () => h("div", { class: "v-dialog" }, slots.default?.());
    },
  }),
  "v-card": passthrough("v-card"),
  "v-card-title": passthrough("v-card-title"),
  "v-card-text": passthrough("v-card-text"),
  "v-card-actions": passthrough("v-card-actions"),
  "v-spacer": passthrough("v-spacer"),
  "v-btn-toggle": defineComponent({
    name: "VBtnToggle",
    setup(_, { slots }) {
      return () => h("div", { class: "v-btn-toggle" }, slots.default?.());
    },
  }),
  "v-btn": defineComponent({
    name: "VBtn",
    props: { disabled: { type: Boolean, default: false } },
    emits: ["click"],
    setup(props, { emit, slots }) {
      return () =>
        h(
          "button",
          {
            class: "v-btn",
            disabled: props.disabled,
            onClick: () => emit("click"),
          },
          slots.default?.(),
        );
    },
  }),
  "v-text-field": defineComponent({
    name: "VTextField",
    props: {
      modelValue: { type: [String, Number], default: "" },
      label: { type: String, default: "" },
    },
    emits: ["update:modelValue"],
    setup(props, { emit }) {
      return () =>
        h("input", {
          class: "text-field-stub",
          value: String(props.modelValue ?? ""),
          onInput: (event: Event) =>
            emit("update:modelValue", (event.target as HTMLInputElement).value),
        });
    },
  }),
};

const LEVELS = [
  { name: "低", value: "low" },
  { name: "高", value: "high" },
  { name: "最高", value: "max" },
];

function mountDialog(props: Record<string, unknown> = {}) {
  return mount(ThinkingEffortLevelsDialog, {
    props: {
      modelValue: false,
      mode: "levels",
      levels: LEVELS,
      slider: DEFAULT_THINKING_EFFORT_SLIDER,
      ...props,
    },
    global: { stubs },
  });
}

/** Open the dialog so the local draft syncs from the props. */
async function openDialog(props: Record<string, unknown> = {}) {
  const wrapper = mountDialog(props);
  await wrapper.setProps({ modelValue: true });
  return wrapper;
}

function buttonsByText(wrapper: ReturnType<typeof mountDialog>) {
  return wrapper.findAll(".v-card-actions button");
}

function saveButton(wrapper: ReturnType<typeof mountDialog>) {
  return buttonsByText(wrapper).at(-1)!;
}

describe("ThinkingEffortLevelsDialog", () => {
  it("shows the level rows in levels mode", async () => {
    const wrapper = await openDialog();
    expect(wrapper.findAll(".effort-level-row")).toHaveLength(3);
    expect(wrapper.find(".effort-slider-fields").exists()).toBe(false);
  });

  it("shows the track fields and snap rows in slider mode", async () => {
    const wrapper = await openDialog({ mode: "slider" });
    expect(wrapper.find(".effort-slider-fields").exists()).toBe(true);
    // One row per shipped snap point (low / high / xhigh / max).
    expect(wrapper.findAll(".effort-level-row")).toHaveLength(4);
  });

  it("saves the slider draft together with the selected mode", async () => {
    const wrapper = await openDialog({ mode: "slider" });
    // min / max / step are the first three inputs of the slider panel.
    const fields = wrapper.findAll(".effort-slider-fields input");
    await fields[2].setValue("5");
    await saveButton(wrapper).trigger("click");

    const payload = wrapper.emitted("save")?.[0]?.[0] as {
      mode: string;
      slider: { step: number; min: number; max: number; snaps: unknown[] };
    };
    expect(payload.mode).toBe("slider");
    expect(payload.slider.step).toBe(5);
    expect(payload.slider.min).toBe(1);
    expect(payload.slider.max).toBe(100);
    expect(payload.slider.snaps).toHaveLength(4);
    expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual([false]);
  });

  it("blocks saving and explains an inverted range", async () => {
    const wrapper = await openDialog({ mode: "slider" });
    const fields = wrapper.findAll(".effort-slider-fields input");
    await fields[1].setValue("0"); // max below min
    expect(saveButton(wrapper).attributes("disabled")).toBeDefined();
    expect(wrapper.find(".effort-level-validation-error").text()).toBe(
      "最小值必须小于最大值",
    );
  });

  it("keeps the level list untouched when saving from slider mode", async () => {
    const wrapper = await openDialog({ mode: "slider" });
    await saveButton(wrapper).trigger("click");
    const payload = wrapper.emitted("save")?.[0]?.[0] as { levels: unknown[] };
    expect(payload.levels).toEqual(LEVELS);
  });
});
