// Author: elecvoid243, 2026-10-08
import { describe, expect, it } from "vitest";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";
import QuickMessagesDialog from "./QuickMessagesDialog.vue";
import type { QuickMessage } from "@/composables/quickMessages";

// Minimal Vuetify stand-ins: enough structure for the template to render and
// for v-model to round-trip through the stubbed textarea.
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
  "v-icon": passthrough("v-icon"),
  "v-btn": defineComponent({
    name: "VBtn",
    props: {
      disabled: { type: Boolean, default: false },
      ariaLabel: { type: String, default: "" },
    },
    emits: ["click"],
    setup(props, { emit, slots }) {
      return () =>
        h(
          "button",
          {
            class: "v-btn",
            disabled: props.disabled,
            "aria-label": props.ariaLabel,
            onClick: () => emit("click"),
          },
          slots.default?.(),
        );
    },
  }),
  "v-textarea": defineComponent({
    name: "VTextarea",
    props: { modelValue: { type: String, default: "" } },
    emits: ["update:modelValue"],
    setup(props, { emit }) {
      return () =>
        h("textarea", {
          class: "textarea-stub",
          value: props.modelValue,
          onInput: (event: Event) =>
            emit("update:modelValue", (event.target as HTMLTextAreaElement).value),
        });
    },
  }),
};

const ITEMS: QuickMessage[] = [
  { id: "qm-1", content: "继续" },
  { id: "qm-2", content: "总结一下" },
  { id: "qm-3", content: "换种说法" },
];

function mountDialog(props: Record<string, unknown> = {}) {
  return mount(QuickMessagesDialog, {
    props: { modelValue: false, items: ITEMS, ...props },
    global: { stubs },
  });
}

async function openDialog(props: Record<string, unknown> = {}) {
  const wrapper = mountDialog(props);
  await wrapper.setProps({ modelValue: true });
  return wrapper;
}

type Wrapper = ReturnType<typeof mountDialog>;

function saveButton(wrapper: Wrapper) {
  return wrapper.findAll(".v-card-actions button").at(-1)!;
}

function rowButton(wrapper: Wrapper, row: number, index: number) {
  return wrapper.findAll(".quick-message-row")[row].findAll("button")[index];
}

function savePayload(wrapper: Wrapper) {
  return wrapper.emitted("save")?.[0]?.[0] as { items: QuickMessage[] };
}

describe("QuickMessagesDialog", () => {
  it("renders one editable row per phrase", async () => {
    const wrapper = await openDialog();
    expect(wrapper.findAll(".quick-message-row")).toHaveLength(3);
    const fields = wrapper.findAll("textarea.textarea-stub");
    expect((fields[0].element as HTMLTextAreaElement).value).toBe("继续");
  });

  it("moves a row up and saves the new order", async () => {
    const wrapper = await openDialog();
    await rowButton(wrapper, 2, 0).trigger("click"); // up on the third row
    await saveButton(wrapper).trigger("click");

    expect(savePayload(wrapper).items.map((item) => item.content)).toEqual([
      "继续",
      "换种说法",
      "总结一下",
    ]);
  });

  it("moves a row down", async () => {
    const wrapper = await openDialog();
    await rowButton(wrapper, 0, 1).trigger("click"); // down on the first row
    await saveButton(wrapper).trigger("click");

    expect(savePayload(wrapper).items.map((item) => item.content)).toEqual([
      "总结一下",
      "继续",
      "换种说法",
    ]);
  });

  it("disables the arrows at the ends of the list", async () => {
    const wrapper = await openDialog();
    expect(rowButton(wrapper, 0, 0).attributes("disabled")).toBeDefined();
    expect(rowButton(wrapper, 0, 1).attributes("disabled")).toBeUndefined();
    expect(rowButton(wrapper, 2, 1).attributes("disabled")).toBeDefined();
  });

  it("deletes a row", async () => {
    const wrapper = await openDialog();
    await rowButton(wrapper, 1, 2).trigger("click"); // delete
    await saveButton(wrapper).trigger("click");

    expect(savePayload(wrapper).items.map((item) => item.content)).toEqual([
      "继续",
      "换种说法",
    ]);
  });

  it("appends an empty row and blocks saving until it is filled", async () => {
    const wrapper = await openDialog();
    await wrapper.findAll(".v-card-actions button")[0].trigger("click");

    expect(wrapper.findAll(".quick-message-row")).toHaveLength(4);
    expect(saveButton(wrapper).attributes("disabled")).toBeDefined();
    expect(wrapper.find(".quick-message-error").text()).toBe("内容不能为空");

    const fields = wrapper.findAll("textarea.textarea-stub");
    await fields[3].setValue("补充说明");
    await saveButton(wrapper).trigger("click");
    expect(savePayload(wrapper).items.at(-1)?.content).toBe("补充说明");
  });

  it("saves an empty list when every row is deleted", async () => {
    const wrapper = await openDialog({ items: [] });
    expect(wrapper.find(".quick-message-empty").exists()).toBe(true);
    await saveButton(wrapper).trigger("click");

    expect(savePayload(wrapper).items).toEqual([]);
    expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual([false]);
  });
});
