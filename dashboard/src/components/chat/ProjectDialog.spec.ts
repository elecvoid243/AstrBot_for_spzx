// ProjectDialog spec — covers the spcode load-step chips (2026-09-09).
//
// Focus: the chips speak in positive terms ("load AGENTS.md") while the
// API stores the inverse (spcode_no_agentsmd / spcode_no_codegraph), and
// the recent-path list is shared with the ChatInput project-load dialog.
//
// Author: elecvoid243

import { mount, type VueWrapper } from "@vue/test-utils";
import { defineComponent, nextTick, type PropType } from "vue";
import { describe, expect, it } from "vitest";
import ProjectDialog, { type Project } from "./ProjectDialog.vue";
import { useProjectPathHistory } from "@/composables/useProjectPathHistory";

const dialogStub = defineComponent({
  props: { modelValue: { type: Boolean, default: false } },
  template: '<div v-if="modelValue"><slot /></div>',
});

const textFieldStub = defineComponent({
  props: {
    modelValue: { type: String, default: "" },
    label: { type: String, default: "" },
  },
  emits: ["update:modelValue"],
  template: `
    <input
      :data-testid="label"
      :value="modelValue"
      @input="$emit('update:modelValue', $event.target.value)"
    />
  `,
});

const textareaStub = defineComponent({
  props: { modelValue: { type: String, default: "" } },
  emits: ["update:modelValue"],
  template: `<textarea :value="modelValue" @input="$emit('update:modelValue', $event.target.value)" />`,
});

const selectStub = defineComponent({
  props: {
    modelValue: { type: String, default: "" },
    items: {
      type: Array as PropType<{ label: string; value: string }[]>,
      default: () => [],
    },
  },
  emits: ["update:modelValue"],
  template: `
    <select :value="modelValue" @change="$emit('update:modelValue', $event.target.value)">
      <option v-for="item in items" :key="item.value" :value="item.value">
        {{ item.label }}
      </option>
    </select>
  `,
});

const directoryBrowserStub = defineComponent({
  name: "ProjectDirectoryBrowser",
  props: { modelValue: { type: Boolean, default: false } },
  emits: ["update:modelValue", "select"],
  template: `<span />`,
});

const stubs = {
  "v-dialog": dialogStub,
  "v-card": { template: "<div><slot /></div>" },
  "v-card-title": { template: "<div><slot /></div>" },
  "v-card-text": { template: "<div><slot /></div>" },
  "v-card-actions": { template: "<div><slot /></div>" },
  "v-text-field": textFieldStub,
  "v-textarea": textareaStub,
  "v-select": selectStub,
  "v-divider": { template: "<hr />" },
  "v-spacer": { template: "<span />" },
  "v-alert": { template: "<div><slot /></div>" },
  "v-list": { template: "<div><slot /></div>" },
  "v-list-item": { template: "<div><slot /></div>" },
  "v-list-item-title": { template: "<div><slot /></div>" },
  "v-btn": {
    props: { disabled: { type: Boolean, default: false } },
    template: "<button :disabled='disabled'><slot /></button>",
  },
  ProjectDirectoryBrowser: directoryBrowserStub,
};

async function mountDialog(project: Project | null = null) {
  // Mount closed, then open — the dialog loads its form in a
  // `watch(modelValue)` handler, exactly like the real open flow.
  const wrapper = mount(ProjectDialog, {
    props: { modelValue: false, project },
    global: { stubs },
  });
  await wrapper.setProps({ modelValue: true });
  await nextTick();
  return wrapper;
}

/** Chip checkboxes, in template order: AGENTS.md then Codegraph. */
function chipInputs(wrapper: VueWrapper) {
  return wrapper.findAll<HTMLInputElement>('input[type="checkbox"]');
}

async function fillAndSave(
  wrapper: VueWrapper,
  path = "C:/projects/demo",
): Promise<Record<string, unknown>> {
  await wrapper.get('[data-testid="项目名称"]').setValue("demo");
  await wrapper.get('[data-testid="工作区路径"]').setValue(path);
  const save = wrapper
    .findAll("button")
    .find((button) => button.text().trim() === "保存");
  expect(save, "save button not found").toBeTruthy();
  await save!.trigger("click");
  await nextTick();
  return wrapper.emitted("save")!.at(-1)![0] as Record<string, unknown>;
}

describe("ProjectDialog spcode load steps", () => {
  it("keeps both load steps on by default and maps them to false no_* flags", async () => {
    const wrapper = await mountDialog();
    await wrapper.get("select").setValue("custom");

    expect(chipInputs(wrapper).map((c) => c.element.checked)).toEqual([
      true,
      true,
    ]);

    const payload = await fillAndSave(wrapper);
    expect(payload).toMatchObject({
      workspace_type: "custom",
      workspace_path: "C:/projects/demo",
      spcode_no_agentsmd: false,
      spcode_no_codegraph: false,
    });
    // The legacy silent-load switch is gone from the form entirely.
    expect(payload.spcode_auto_load).toBeUndefined();
  });

  it("maps an unchecked chip to the corresponding no_* flag", async () => {
    const wrapper = await mountDialog();
    await wrapper.get("select").setValue("custom");
    await chipInputs(wrapper)[0].setValue(false);

    const payload = await fillAndSave(wrapper);
    expect(payload).toMatchObject({
      spcode_no_agentsmd: true,
      spcode_no_codegraph: false,
    });
  });

  it("reflects a stored no_agentsmd flag as an unchecked chip", async () => {
    const wrapper = await mountDialog({
      project_id: "p-1",
      title: "demo",
      workspace_type: "custom",
      workspace_path: "C:/projects/demo",
      spcode_no_agentsmd: true,
      spcode_no_codegraph: false,
      created_at: "",
      updated_at: "",
    });

    expect(chipInputs(wrapper).map((c) => c.element.checked)).toEqual([
      false,
      true,
    ]);
  });

  it("fills the path from the shared recent list and records saved paths", async () => {
    const { addToPathHistory, recentPaths } = useProjectPathHistory();
    addToPathHistory("C:/recent/one");

    const wrapper = await mountDialog();
    await wrapper.get("select").setValue("custom");
    await nextTick();

    const recentRow = wrapper
      .findAll(".history-item")
      .find((row) => row.text().includes("C:/recent/one"));
    expect(recentRow, "recent path row not rendered").toBeTruthy();
    await recentRow!.trigger("click");

    const pathInput = wrapper.get<HTMLInputElement>(
      '[data-testid="工作区路径"]',
    );
    expect(pathInput.element.value).toBe("C:/recent/one");

    await fillAndSave(wrapper, "C:/projects/another");
    expect(recentPaths.value[0]).toBe("C:/projects/another");
  });
});
