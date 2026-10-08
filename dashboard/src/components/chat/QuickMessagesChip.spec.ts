// Author: elecvoid243, 2026-10-08
import { describe, expect, it } from "vitest";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";
import QuickMessagesChip from "./QuickMessagesChip.vue";
import type { QuickMessage } from "@/composables/quickMessages";

// Vuetify is not registered under test; only the pieces this chip touches
// are stubbed (same shape as ThinkingEffortChip.spec.ts).
const passthrough = (name: string) =>
  defineComponent({
    name,
    setup(_, { slots }) {
      return () => h("div", { class: name }, slots.default?.());
    },
  });

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
  "v-tooltip": defineComponent({
    name: "VTooltip",
    setup(_, { slots }) {
      return () =>
        h("div", { class: "v-tooltip" }, [
          slots.activator?.({ props: {} }),
          slots.default?.(),
        ]);
    },
  }),
  "v-card": passthrough("v-card"),
  "v-card-text": passthrough("v-card-text"),
};

const ITEMS: QuickMessage[] = [
  { id: "qm-1", content: "继续" },
  { id: "qm-2", content: "把上面的对话要点整理成清单，带上每条结论的依据" },
];

function mountChip(props: Record<string, unknown> = {}) {
  return mount(QuickMessagesChip, {
    props: { items: ITEMS, ...props },
    global: { stubs },
  });
}

describe("QuickMessagesChip", () => {
  it("lists every phrase as a row", () => {
    const wrapper = mountChip();
    const rows = wrapper.findAll(".quick-chip-row:not(.quick-chip-row--edit)");
    expect(rows).toHaveLength(2);
    expect(rows[1].text()).toContain("把上面的对话要点整理成清单");
  });

  it("exposes the full text through the title attribute", () => {
    const wrapper = mountChip();
    const rows = wrapper.findAll(".quick-chip-row:not(.quick-chip-row--edit)");
    // The row itself truncates with CSS; hover reveals the rest.
    expect(rows[1].attributes("title")).toBe(ITEMS[1].content);
  });

  it("emits the full content when a row is clicked", async () => {
    const wrapper = mountChip();
    await wrapper
      .findAll(".quick-chip-row:not(.quick-chip-row--edit)")[1]
      .trigger("click");
    expect(wrapper.emitted("send")?.[0]).toEqual([ITEMS[1].content]);
  });

  it("shows the empty hint when no phrase is configured", () => {
    const wrapper = mountChip({ items: [] });
    expect(wrapper.find(".quick-chip-empty").exists()).toBe(true);
    expect(
      wrapper.findAll(".quick-chip-row:not(.quick-chip-row--edit)"),
    ).toHaveLength(0);
  });

  it("hides the edit row unless the caller may rewrite the list", () => {
    const editable = mountChip({ canEdit: true });
    expect(editable.find(".quick-chip-row--edit").exists()).toBe(true);

    const readonly = mountChip({});
    expect(readonly.find(".quick-chip-row--edit").exists()).toBe(false);
    expect(readonly.find(".quick-chip-divider").exists()).toBe(false);
  });

  it("emits edit from the gear row", async () => {
    const wrapper = mountChip({ canEdit: true });
    await wrapper.find(".quick-chip-row--edit").trigger("click");
    expect(wrapper.emitted("edit")).toHaveLength(1);
  });
});
