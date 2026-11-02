import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import TrialRunDialog from "./TrialRunDialog.vue";

const api = vi.hoisted(() => ({
  runs: vi.fn(),
  previewTrial: vi.fn(),
  startTrial: vi.fn(),
}));
vi.mock("@/api/workflowAssets", () => ({ workflowAssetsApi: api }));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));
const slot = { template: "<div><slot /></div>" };
const stubs = {
  VDialog: slot,
  VCard: slot,
  VCardTitle: slot,
  VCardText: slot,
  VCardActions: slot,
  VAlert: slot,
  VSpacer: slot,
  VProgressLinear: slot,
  VSwitch: slot,
  VBtn: {
    props: ["disabled", "loading"],
    template: '<button :disabled="disabled || loading"><slot /></button>',
  },
  VTextField: {
    props: ["modelValue", "label"],
    emits: ["update:modelValue"],
    template:
      '<input :aria-label="label" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  VTextarea: {
    props: ["modelValue", "label"],
    emits: ["update:modelValue"],
    template:
      '<textarea :aria-label="label" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  VSelect: {
    props: ["modelValue", "items", "label", "itemTitle", "itemValue"],
    emits: ["update:modelValue"],
    template:
      "<select :aria-label=\"label\" :value=\"modelValue\" @change=\"$emit('update:modelValue', $event.target.value)\"><option v-for=\"item in items\" :value=\"typeof item === 'string' ? item : item[itemValue || 'value']\">{{ typeof item === 'string' ? item : item[itemTitle || 'title'] }}</option></select>",
  },
};

describe("draft trial dialog", () => {
  it("previews server scope and submits typed manual data", async () => {
    api.runs.mockResolvedValue({ runs: [] });
    api.previewTrial.mockImplementation(async (_: string, request: any) =>
      request.mode === "full"
        ? { selected_nodes: ["a", "b"], required_inputs: [], requirements: {} }
        : {
            selected_nodes: ["b"],
            required_inputs: [],
            requirements: {
              a: {
                name: "A",
                pinned_only: false,
                fields: { score: ["integer"] },
                contract: {
                  type: "object",
                  required: ["score"],
                  properties: { score: { type: "integer" } },
                },
              },
            },
          },
    );
    let finish!: (value: { run_id: string }) => void;
    api.startTrial.mockReset().mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const wrapper = mount(TrialRunDialog, {
      props: {
        modelValue: true,
        assetId: "draft",
        revision: 3,
        kind: "workflow",
        nodes: [{ id: "b", name: "B" }],
        initialNode: "b",
      },
      global: { stubs },
    });
    await flushPromises();
    await wrapper.get('select[aria-label="trial.scope"]').setValue("single");
    await flushPromises();
    expect(wrapper.text()).toContain("trial.realCalls");
    expect(wrapper.text()).toContain("trial.selected: B");
    await wrapper.get('input[aria-label="score (integer)"]').setValue("5");
    const start = wrapper.findAll("button").find((button) => button.text() === "trial.start")!;
    await start.trigger("click");
    await start.trigger("click");
    expect(api.startTrial).toHaveBeenCalledTimes(1);
    finish({ run_id: "trial-run" });
    await flushPromises();
    expect(api.startTrial).toHaveBeenCalledWith("draft", {
      kind: "workflow",
      revision: 3,
      mode: "single",
      node_id: "b",
      inputs: {},
      sources: { a: { content: { score: 5 } } },
    });
    expect(wrapper.emitted("started")).toEqual([["trial-run"]]);
  });
  it("selects the exact historical artifact for approval", async () => {
    api.runs.mockResolvedValue({
      runs: [
        {
          snapshot: { definition: { name: "Past" } },
          artifacts: [
            { artifact_id: "report", version: 2, summary: "Revision" },
          ],
        },
      ],
    });
    api.previewTrial.mockResolvedValue({
      selected_nodes: ["approval"],
      required_inputs: [],
      requirements: {
        report: {
          name: "Report",
          pinned_only: true,
          fields: {},
          contract: null,
        },
      },
    });
    api.startTrial.mockResolvedValue({ run_id: "approval-run" });
    const wrapper = mount(TrialRunDialog, {
      props: {
        modelValue: true,
        assetId: "draft",
        revision: 8,
        kind: "workflow",
        nodes: [{ id: "approval", name: "Approval" }],
        initialNode: "approval",
      },
      global: { stubs },
    });
    await flushPromises();
    await wrapper.get('select[aria-label="trial.scope"]').setValue("single");
    await flushPromises();
    await wrapper
      .get('select[aria-label="trial.history"]')
      .setValue(JSON.stringify({ artifact_id: "report", version: 2 }));
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "trial.start")!
      .trigger("click");
    await flushPromises();
    expect(api.startTrial).toHaveBeenLastCalledWith("draft", {
      kind: "workflow",
      revision: 8,
      mode: "single",
      node_id: "approval",
      inputs: {},
      sources: { report: { artifact: { artifact_id: "report", version: 2 } } },
    });
  });
});
