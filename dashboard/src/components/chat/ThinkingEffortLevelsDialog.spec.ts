// Author: elecvoid243, 2026-10-08
import { describe, expect, it } from "vitest";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";
import ThinkingEffortLevelsDialog from "./ThinkingEffortLevelsDialog.vue";
import { DEFAULT_THINKING_EFFORT_SLIDER } from "@/composables/thinkingEffortSlider";
import type { ThinkingEffortPreset } from "@/composables/thinkingEffortPresets";

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
  "v-select": defineComponent({
    name: "VSelect",
    props: {
      modelValue: { type: String, default: "" },
      items: { type: Array, default: () => [] },
    },
    emits: ["update:modelValue"],
    setup(props, { emit }) {
      return () =>
        h(
          "select",
          {
            class: "select-stub",
            value: props.modelValue,
            onChange: (event: Event) =>
              emit("update:modelValue", (event.target as HTMLSelectElement).value),
          },
          (props.items as { title: string; value: string }[]).map((item) =>
            h("option", { value: item.value }, item.title),
          ),
        );
    },
  }),
};

const LEVELS = [
  { name: "低", value: "low" },
  { name: "高", value: "high" },
  { name: "最高", value: "max" },
];

function makePreset(
  overrides: Partial<ThinkingEffortPreset> = {},
): ThinkingEffortPreset {
  return {
    id: "p1",
    name: "Test preset",
    mode: "levels",
    levels: LEVELS.map((level) => ({ ...level })),
    slider: {
      ...DEFAULT_THINKING_EFFORT_SLIDER,
      snaps: DEFAULT_THINKING_EFFORT_SLIDER.snaps.map((snap) => ({ ...snap })),
    },
    ...overrides,
  };
}

function mountDialog(props: Record<string, unknown> = {}) {
  return mount(ThinkingEffortLevelsDialog, {
    props: {
      modelValue: false,
      presets: [makePreset()],
      activePreset: "p1",
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

type Wrapper = ReturnType<typeof mountDialog>;

function saveButton(wrapper: Wrapper) {
  return wrapper.findAll(".v-card-actions button").at(-1)!;
}

function savePayload(wrapper: Wrapper) {
  return wrapper.emitted("save")?.[0]?.[0] as {
    presets: ThinkingEffortPreset[];
    activePreset: string;
  };
}

describe("ThinkingEffortLevelsDialog", () => {
  it("shows the level rows of the active preset", async () => {
    const wrapper = await openDialog();
    expect(wrapper.findAll(".effort-level-row")).toHaveLength(3);
    expect(wrapper.find(".effort-slider-fields").exists()).toBe(false);
  });

  it("shows the track fields and snap rows in slider mode", async () => {
    const wrapper = await openDialog({
      presets: [makePreset({ mode: "slider" })],
    });
    expect(wrapper.find(".effort-slider-fields").exists()).toBe(true);
    // One row per shipped snap point (low / high / xhigh / max).
    expect(wrapper.findAll(".effort-level-row")).toHaveLength(4);
  });

  it("saves the edited preset list together with the active id", async () => {
    const wrapper = await openDialog({
      presets: [makePreset({ mode: "slider" })],
    });
    // min / max / step are the first three inputs of the slider panel.
    const fields = wrapper.findAll(".effort-slider-fields input");
    await fields[2].setValue("5");
    await saveButton(wrapper).trigger("click");

    const payload = savePayload(wrapper);
    expect(payload.activePreset).toBe("p1");
    expect(payload.presets).toHaveLength(1);
    expect(payload.presets[0].slider.step).toBe(5);
    expect(payload.presets[0].slider.min).toBe(1);
    expect(payload.presets[0].slider.max).toBe(100);
    expect(payload.presets[0].slider.snaps).toHaveLength(4);
    expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual([false]);
  });

  it("blocks saving and explains an inverted range", async () => {
    const wrapper = await openDialog({
      presets: [makePreset({ mode: "slider" })],
    });
    const fields = wrapper.findAll(".effort-slider-fields input");
    await fields[1].setValue("0"); // max below min
    expect(saveButton(wrapper).attributes("disabled")).toBeDefined();
    expect(wrapper.find(".effort-level-validation-error").text()).toBe(
      "最小值必须小于最大值",
    );
  });

  it("adds a preset that copies the current shape", async () => {
    const wrapper = await openDialog();
    await wrapper.find(".effort-preset-row button").trigger("click");
    await saveButton(wrapper).trigger("click");

    const payload = savePayload(wrapper);
    expect(payload.presets).toHaveLength(2);
    expect(payload.presets[1].levels).toEqual(LEVELS);
    expect(payload.presets[1].mode).toBe("levels");
    // The new preset becomes the active one.
    expect(payload.activePreset).toBe(payload.presets[1].id);
  });

  it("deletes the last preset and still saves an empty list", async () => {
    const wrapper = await openDialog();
    await wrapper.find(".effort-delete-preset").trigger("click");
    await saveButton(wrapper).trigger("click");

    const payload = savePayload(wrapper);
    expect(payload.presets).toEqual([]);
    expect(payload.activePreset).toBe("");
  });

  it("keeps the edits of a preset when switching away and back", async () => {
    const wrapper = await openDialog({
      presets: [makePreset(), makePreset({ id: "p2", name: "Second" })],
      activePreset: "p1",
    });
    const firstName = wrapper.findAll(".effort-level-row input")[0];
    await firstName.setValue("最低");

    const select = wrapper.find("select.select-stub");
    await select.setValue("p2");
    await select.setValue("p1");

    await saveButton(wrapper).trigger("click");
    const payload = savePayload(wrapper);
    expect(payload.presets[0].levels[0].name).toBe("最低");
  });
});
