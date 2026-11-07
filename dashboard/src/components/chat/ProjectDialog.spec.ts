// ProjectDialog spec — covers workspace-path handling after the
// 2026-11-07 project-load redesign.
//
// Focus: project-level load-step chips are gone (AGENTS.md is injected
// whenever it exists; codegraph is manual), and the recent-path list is
// shared with the ChatInput project-load dialog.
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

describe("ProjectDialog workspace path", () => {
  it("submits only the workspace fields (no legacy load-step flags)", async () => {
    const wrapper = await mountDialog();

    expect(wrapper.findAll('input[type="checkbox"]')).toHaveLength(0);
    const payload = await fillAndSave(wrapper);
    expect(payload).toMatchObject({
      workspace_type: "custom",
      workspace_path: "C:/projects/demo",
    });
    expect(payload.spcode_no_agentsmd).toBeUndefined();
    expect(payload.spcode_no_codegraph).toBeUndefined();
    expect(payload.spcode_auto_load).toBeUndefined();
  });

  it("opens an existing custom project without resurrecting legacy flags", async () => {
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

    expect(wrapper.findAll('input[type="checkbox"]')).toHaveLength(0);
    const payload = await fillAndSave(wrapper);
    expect(payload.spcode_no_agentsmd).toBeUndefined();
    expect(payload.spcode_no_codegraph).toBeUndefined();
  });

  it("fills the path from the shared recent list and records saved paths", async () => {
    const { addToPathHistory, recentPaths } = useProjectPathHistory();
    addToPathHistory("C:/recent/one");

    const wrapper = await mountDialog();
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
