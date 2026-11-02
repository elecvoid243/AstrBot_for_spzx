import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, ref } from "vue";
import { describe, expect, it, vi } from "vitest";
import { workflowAssetsApi } from "@/api/workflowAssets";
import AllowedWorkflowsPanel from "./AllowedWorkflowsPanel.vue";

vi.mock("@/api/workflowAssets", () => ({
  workflowAssetsApi: { list: vi.fn(), get: vi.fn() },
}));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

const versions = [1, 2, 3].map((version) => ({
  asset_id: "research",
  version,
  versions: [3, 2, 1],
  definition: {
    name: `Research v${version}`,
    required_inputs: [`topic${version}`],
    result_task_id: `result${version}`,
    tasks: [
      {
        id: `result${version}`,
        name: `Result ${version}`,
        output_contract: { type: "object", required: [`proof${version}`] },
      },
    ],
  },
}));

function openPanel(initialRefs = [{ asset_id: "research", version: 3 }]) {
  vi.mocked(workflowAssetsApi.list).mockResolvedValue({
    assets: [
      versions[2],
      { asset_id: "team", definition: { kind: "autonomous" } },
    ],
  });
  const host = defineComponent({
    components: { AllowedWorkflowsPanel },
    setup: () => ({
      refs: ref(initialRefs),
      active: ref(true),
    }),
    template: '<AllowedWorkflowsPanel v-model="refs" :active="active" />',
  });
  return mount(host, {
    global: {
      stubs: {
        "v-select": {
          props: ["modelValue", "items", "disabled"],
          emits: ["update:modelValue"],
          template: '<select :disabled="disabled" />',
        },
        "v-btn": { template: "<button><slot /></button>" },
        "v-alert": { template: '<div role="alert"><slot /></div>' },
      },
    },
  });
}

describe("allowed fixed workflow versions", () => {
  it("preserves concurrent selections in independent workflow rows", async () => {
    let finishFirst!: (value: any) => void;
    const delayed = new Promise((resolve) => {
      finishFirst = resolve;
    });
    const other = versions.map((asset) => ({ ...asset, asset_id: "other" }));
    vi.mocked(workflowAssetsApi.get)
      .mockReset()
      .mockImplementation((id, version) =>
        id === "research" && version === 1
          ? delayed
          : Promise.resolve(
              (id === "research" ? versions : other)[version! - 1],
            ),
      );
    const wrapper = openPanel([
      { asset_id: "research", version: 3 },
      { asset_id: "other", version: 3 },
    ]);
    try {
      await flushPromises();
      const panel = wrapper.getComponent(AllowedWorkflowsPanel);
      panel
        .getComponent<any>('[data-testid="allowed-workflow-version-0"]')
        .vm.$emit("update:modelValue", 1);
      await flushPromises();
      panel
        .getComponent<any>('[data-testid="allowed-workflow-version-1"]')
        .vm.$emit("update:modelValue", 2);
      await flushPromises();
      expect(panel.emitted("validation")?.at(-1)).toEqual([true]);
      finishFirst(versions[0]);
      await flushPromises();
      expect(panel.props("modelValue")).toEqual([
        { asset_id: "research", version: 1 },
        { asset_id: "other", version: 2 },
      ]);
      expect(panel.emitted("validation")?.at(-1)).toEqual([false]);
    } finally {
      wrapper.unmount();
    }
  });
  it("shows only saved workflows and declares the chosen return contract", async () => {
    vi.mocked(workflowAssetsApi.get).mockReset().mockResolvedValue(versions[2]);
    const wrapper = openPanel();
    await flushPromises();
    const panel = wrapper.getComponent(AllowedWorkflowsPanel);
    expect(
      panel
        .getComponent<any>('[data-testid="allowed-workflow-add"]')
        .props("items"),
    ).toEqual([versions[2]]);
    expect(panel.text()).toContain("topic3");
    expect(panel.text()).toContain("proof3");
    vi.mocked(workflowAssetsApi.get).mockResolvedValue(versions[1]);
    panel
      .getComponent<any>('[data-testid="allowed-workflow-version-0"]')
      .vm.$emit("update:modelValue", 2);
    await flushPromises();
    expect(panel.props("modelValue")).toEqual([
      { asset_id: "research", version: 2 },
    ]);
    expect(panel.text()).toContain("topic2");
    expect(panel.text()).toContain("proof2");
    wrapper.unmount();
  });

  it.each(["success", "failure"])(
    "ignores old %s after the latest selected version",
    async (outcome) => {
      let resolveOld!: (value: any) => void;
      let rejectOld!: (reason: Error) => void;
      const delayed = new Promise((resolve, reject) => {
        resolveOld = resolve;
        rejectOld = reject;
      });
      vi.mocked(workflowAssetsApi.get)
        .mockReset()
        .mockImplementation((_id, version) =>
          version === 1
            ? delayed
            : Promise.resolve(versions[version === 2 ? 1 : 2]),
        );
      const wrapper = openPanel();
      await flushPromises();
      const panel = wrapper.getComponent(AllowedWorkflowsPanel);
      const selector = panel.getComponent<any>(
        '[data-testid="allowed-workflow-version-0"]',
      );
      selector.vm.$emit("update:modelValue", 1);
      await flushPromises();
      selector.vm.$emit("update:modelValue", 2);
      await flushPromises();
      if (outcome === "success") resolveOld(versions[0]);
      else rejectOld(new Error("Late inaccessible v1"));
      await flushPromises();
      expect(panel.props("modelValue")).toEqual([
        { asset_id: "research", version: 2 },
      ]);
      expect(panel.text()).toContain("topic2");
      expect(panel.text()).not.toContain("Late inaccessible");
      expect(panel.emitted("validation")?.at(-1)).toEqual([false]);
      wrapper.unmount();
    },
  );

  it.each(["remove", "close", "replace", "unmount"])(
    "invalidates a version read on %s",
    async (change) => {
      let finish!: (value: any) => void;
      const delayed = new Promise((resolve) => {
        finish = resolve;
      });
      vi.mocked(workflowAssetsApi.get)
        .mockReset()
        .mockImplementation((_id, version) =>
          version === 1 ? delayed : Promise.resolve(versions[2]),
        );
      const wrapper = openPanel();
      await flushPromises();
      const panel = wrapper.getComponent(AllowedWorkflowsPanel);
      panel
        .getComponent<any>('[data-testid="allowed-workflow-version-0"]')
        .vm.$emit("update:modelValue", 1);
      await flushPromises();
      if (change === "remove")
        await panel
          .get('[data-testid="allowed-workflow-remove-0"]')
          .trigger("click");
      if (change === "close") (wrapper.vm as any).active = false;
      if (change === "replace")
        (wrapper.vm as any).refs = [
          { asset_id: "research", version: 3 },
          { asset_id: "other", version: 1 },
        ];
      if (change === "unmount") wrapper.unmount();
      await flushPromises();
      const before = panel.emitted("update:modelValue")?.length ?? 0;
      finish(versions[0]);
      await flushPromises();
      expect(panel.emitted("update:modelValue")?.length ?? 0).toBe(before);
      wrapper.unmount();
    },
  );
});
