// WorktreeCreateDialog.spec.ts
//
// 2026-09-17 (elecvoid243). Guards the start-point combobox.
//
// Why: the dialog used to hard-code `base = "main"`. In a repo whose branch is
// `master` that start point does not exist, so git answered
// `fatal: invalid reference: main` and the sidebar showed a missing i18n key.
// The dialog must now default the start point to the project's CURRENT branch
// and offer the project's branches/tags as pickable items.
import { mount, type VueWrapper } from "@vue/test-utils";
import { defineComponent, nextTick, type PropType } from "vue";
import { describe, expect, it } from "vitest";
import WorktreeCreateDialog from "./WorktreeCreateDialog.vue";

const dialogStub = defineComponent({
  props: { modelValue: { type: Boolean, default: false } },
  template: '<div v-if="modelValue"><slot /></div>',
});

// Text-field stub: records its `name` so a test can address a specific field.
const textFieldStub = defineComponent({
  props: {
    modelValue: { type: String, default: "" },
    name: { type: String, default: "" },
    disabled: { type: Boolean, default: false },
  },
  emits: ["update:modelValue"],
  template: `
    <div data-tag="text-field" :data-name="name" :data-disabled="String(disabled)">
      <input :data-testid="name" :value="modelValue"
             @input="$emit('update:modelValue', $event.target.value)" />
    </div>
  `,
});

// Combobox stub: additionally exposes its `items` so the tests can assert what
// the user is able to pick (branches, tags, dedup order).
const comboboxStub = defineComponent({
  props: {
    modelValue: { type: String, default: "" },
    items: { type: Array as PropType<string[]>, default: () => [] },
    name: { type: String, default: "" },
  },
  emits: ["update:modelValue"],
  template: `
    <div data-tag="combobox" :data-name="name" :data-items="items.join('|')">
      <input :data-testid="name" :value="modelValue"
             @input="$emit('update:modelValue', $event.target.value)" />
    </div>
  `,
});

// Mirrors `value` onto data-value so the group stub can read the clicked option.
const radioStub = defineComponent({
  props: { value: { type: String, default: "" }, label: { type: String, default: "" } },
  template: `<button type="button" :data-value="value">{{ label }}</button>`,
});

const radioGroupStub = defineComponent({
  props: { modelValue: { type: String as PropType<string | null>, default: null } },
  emits: ["update:modelValue"],
  template: `<div class="v-radio-group" @click="onClick"><slot /></div>`,
  methods: {
    onClick(e: Event) {
      const target = e.target as HTMLElement | null;
      const button = target?.closest?.("button") as HTMLElement | null;
      const value = button?.dataset?.value;
      if (value !== undefined && value !== "") this.$emit("update:modelValue", value);
    },
  },
});

const buttonStub = defineComponent({
  props: { disabled: { type: Boolean, default: false } },
  template: `<button :disabled="disabled"><slot /></button>`,
});

const stubs = {
  "v-dialog": dialogStub,
  "v-card": { template: "<div><slot /></div>" },
  "v-card-title": { template: "<div><slot /></div>" },
  "v-card-text": { template: "<div><slot /></div>" },
  "v-card-actions": { template: "<div><slot /></div>" },
  "v-text-field": textFieldStub,
  "v-combobox": comboboxStub,
  "v-radio": radioStub,
  "v-radio-group": radioGroupStub,
  "v-chip": { template: "<span><slot /></span>" },
  "v-icon": { template: "<i><slot /></i>" },
  "v-btn": buttonStub,
  "v-spacer": { template: "<span />" },
};

const BRANCHES = ["master", "feat/x"];
const TAGS = ["v1.0", "master"]; // a tag may share a branch name -> dedupe

function mountDialog(): VueWrapper {
  return mount(WorktreeCreateDialog, {
    props: { modelValue: false, branches: BRANCHES, tags: TAGS, currentBranch: "master" },
    global: { stubs },
  });
}

/** Open the dialog (false -> true runs the form reset). */
async function open(wrapper: VueWrapper): Promise<void> {
  await wrapper.setProps({ modelValue: true });
  await nextTick();
}

function attr(wrapper: VueWrapper, selector: string, name: string): string | undefined {
  return wrapper.find(selector).attributes(name);
}

/** Vue sets `value` on <input> as a DOM property, not an attribute. */
function inputValue(wrapper: VueWrapper, selector: string): string {
  return (wrapper.find(selector).element as HTMLInputElement).value;
}

describe("WorktreeCreateDialog start point", () => {
  it("defaults the start point to the project's current branch, not a hard-coded 'main'", async () => {
    const wrapper = mountDialog();
    await open(wrapper);
    expect(inputValue(wrapper, '[data-name="wtc-base"] input')).toBe("master");
    expect(inputValue(wrapper, '[data-name="wtc-base"] input')).not.toBe("main");
  });

  it("leaves the start point empty when no branch is known yet", async () => {
    const wrapper = mount(WorktreeCreateDialog, {
      props: { modelValue: false, currentBranch: null },
      global: { stubs },
    });
    await open(wrapper);
    expect(inputValue(wrapper, '[data-name="wtc-base"] input')).toBe("");
  });

  it("offers branches and tags as pickable start points, deduped", async () => {
    const wrapper = mountDialog();
    await open(wrapper);
    expect(attr(wrapper, '[data-name="wtc-base"]', "data-items")).toBe("master|feat/x|v1.0");
  });

  it("keeps the branch field free-text in create mode and lists branches in force mode", async () => {
    const wrapper = mountDialog();
    await open(wrapper);
    // create mode: the NEW branch name cannot come from a list
    expect(wrapper.find('[data-name="wtc-branch"][data-tag="text-field"]').exists()).toBe(true);
    expect(wrapper.find('[data-name="wtc-branch"][data-tag="combobox"]').exists()).toBe(false);

    await wrapper.find('[data-value="force"]').trigger("click");
    await nextTick();
    expect(wrapper.find('[data-name="wtc-branch"][data-tag="combobox"]').exists()).toBe(true);
    expect(attr(wrapper, '[data-name="wtc-branch"]', "data-items")).toBe("master|feat/x");
  });

  it("submits the picked start point", async () => {
    const wrapper = mountDialog();
    await open(wrapper);
    await wrapper.find('[data-name="wtc-branch"] input').setValue("feat/new");
    await wrapper.find('[data-name="wtc-path"] input').setValue("C:/repo/.worktrees/feat-new");
    await wrapper.find('[data-name="wtc-base"] input').setValue("feat/x");

    const buttons = wrapper.findAll("button");
    await buttons[buttons.length - 1].trigger("click");

    expect(wrapper.emitted("submit")?.[0]?.[0]).toEqual({
      path: "C:/repo/.worktrees/feat-new",
      umo: null,
      branch: "feat/new",
      create: true,
      base: "feat/x",
    });
  });
});
