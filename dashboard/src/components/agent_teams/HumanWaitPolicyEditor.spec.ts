import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import HumanWaitPolicyEditor from "./HumanWaitPolicyEditor.vue";
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));
const stubs = {
  "v-switch": {
    props: ["modelValue", "label"],
    emits: ["update:modelValue"],
    template:
      "<button @click=\"$emit('update:modelValue', !modelValue)\">{{ label }}</button>",
  },
  "v-select": {
    name: "v-select",
    props: ["modelValue", "items"],
    emits: ["update:modelValue"],
    template:
      '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><option v-for="item in items" :value="item.value">{{ item.title }}</option></select>',
  },
  "v-text-field": {
    name: "v-text-field",
    props: ["modelValue", "errorMessages", "min", "step"],
    emits: ["update:modelValue"],
    template:
      '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
};
describe("human wait policy", () => {
  it("defaults to unlimited and requires enabling a deadline before selecting automatic action", async () => {
    const wrapper = mount(HumanWaitPolicyEditor, {
      props: { modelValue: null },
      global: { stubs },
    });
    expect(wrapper.text()).toContain("humanWait.unlimited");
    expect(wrapper.find("select").exists()).toBe(false);
    await wrapper.get("button").trigger("click");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([
      { timeout_seconds: 60, timeout_action: "wait" },
    ]);
    await wrapper.setProps({
      modelValue: { timeout_seconds: 60, timeout_action: "wait" },
    });
    await wrapper.findAll("select")[1].setValue("use_recommendation");
    expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual([
      { timeout_seconds: 60, timeout_action: "use_recommendation" },
    ]);
    await wrapper.get("button").trigger("click");
    expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual([null]);
  });
  it("distinguishes inheritance from an explicit unlimited override", async () => {
    const wrapper = mount(HumanWaitPolicyEditor, {
      props: { override: true },
      global: { stubs },
    });
    await wrapper.get("button").trigger("click");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([null]);
    await wrapper.setProps({ modelValue: null });
    await wrapper.findAll("button")[0].trigger("click");
    expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual([undefined]);
  });
});

it("persists and removes a local wait override in the shared resource editor", async () => {
  const { default: RoleResourcesEditor } = await import(
    "./RoleResourcesEditor.vue"
  );
  const definition: Record<string, unknown> = {};
  const wrapper = mount(RoleResourcesEditor, {
    props: { definition, override: true, modes: {}, catalog: {} },
    global: {
      stubs: {
        ...stubs,
        "v-expansion-panel": { template: "<div><slot /></div>" },
        "v-expansion-panel-title": { template: "<div><slot /></div>" },
        "v-expansion-panel-text": { template: "<div><slot /></div>" },
      },
    },
  });
  const editor = wrapper.getComponent(HumanWaitPolicyEditor);
  editor.vm.$emit("update:modelValue", {
    timeout_seconds: 120,
    timeout_action: "use_recommendation",
  });
  expect(definition.human_wait).toEqual({
    timeout_seconds: 120,
    timeout_action: "use_recommendation",
  });
  editor.vm.$emit("update:modelValue", null);
  expect(definition).toHaveProperty("human_wait", null);
  editor.vm.$emit("update:modelValue", undefined);
  expect(definition).not.toHaveProperty("human_wait");
});

it("changes units without changing the saved seconds and validates fractional minutes", async () => {
  const wrapper = mount(HumanWaitPolicyEditor, {
    props: { modelValue: { timeout_seconds: 90, timeout_action: "wait" } },
    global: { stubs },
  });
  const selects = wrapper.findAllComponents({ name: "v-select" });
  selects[0].vm.$emit("update:modelValue", 60);
  await wrapper.vm.$nextTick();
  const input = wrapper.findComponent({ name: "v-text-field" });
  expect(input.props("modelValue")).toBe(1.5);
  expect(input.props("errorMessages")).toEqual([]);
  expect(input.props("min")).toBe(1 / 60);
  expect(input.props("step")).toBe("any");
  expect(wrapper.emitted("update:modelValue")).toBeUndefined();
  await wrapper.get("input").setValue("2.5");
  expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual([
    { timeout_seconds: 150, timeout_action: "wait" },
  ]);
  await wrapper.get("input").setValue("0.01");
  expect(input.props("errorMessages")).toEqual(["humanWait.invalid"]);
  expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual([
    { timeout_seconds: 0, timeout_action: "wait" },
  ]);
  await wrapper.setProps({
    modelValue: { timeout_seconds: 61, timeout_action: "wait" },
  });
  expect(input.props("errorMessages")).toEqual([]);
  await wrapper.get("input").setValue("525600.1");
  expect(input.props("errorMessages")).toEqual(["humanWait.invalid"]);
  wrapper.unmount();
});
