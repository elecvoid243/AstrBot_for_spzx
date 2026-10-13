import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import { workflowAssetsApi } from "@/api/workflowAssets";
import WorkflowGraphCanvas from "./WorkflowGraphCanvas.vue";
import TeamsFlowCanvas from "./TeamsFlowCanvas.vue";

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));
vi.mock("@/api/workflowAssets", () => ({
  workflowAssetsApi: { get: vi.fn() },
}));

const teams = ["oldGoal", "newGoal", "goal"].map((key, index) => ({
  asset_id: "research",
  version: index + 1,
  versions: [3, 2, 1],
  definition: {
    kind: "autonomous",
    name: `Research ${index + 1}`,
    required_inputs: ["goal", ...(key === "goal" ? [] : [key])],
    coordinator: { role_id: "coordinator", role_version: 1 },
    allowed_roles: [{ role_id: "writer", role_version: 1 }],
    output_contract: {
      type: "object",
      properties: { [key]: { type: "string" } },
      required: [key],
    },
  },
}));
const definition = {
  name: "Parent",
  required_inputs: ["goal", "newGoal", "oldGoal"],
  tasks: ["first", "second"].map((id) => ({
    id,
    name: id,
    team: { asset_id: "research", version: 3 },
    inputs: { goal: { input: "goal" } },
    output_contract: teams[2].definition.output_contract,
  })),
  edges: [],
};
const canvasGlobal = {
  stubs: {
    TeamsFlowCanvas: true,
    "v-icon": true,
    "v-chip": true,
    "v-btn": { template: "<button><slot /></button>" },
    "v-text-field": true,
    "v-select": true,
    "v-textarea": true,
    "v-spacer": true,
    "v-alert": { template: '<div role="alert"><slot /></div>' },
    "v-switch": true,
  },
};

describe("pinned child team selection", () => {
  it.each(["success", "failure"])(
    "clears an unavailable choice when reselecting the cached pin before old %s",
    async (outcome) => {
      let resolveOld!: (value: any) => void;
      let rejectOld!: (reason: Error) => void;
      const delayed = new Promise<any>((resolve, reject) => {
        resolveOld = resolve;
        rejectOld = reject;
      });
      vi.mocked(workflowAssetsApi.get)
        .mockReset()
        .mockImplementation((_id, version) =>
          version === 1
            ? delayed
            : Promise.reject(new Error("Unavailable choice")),
        );
      const wrapper = mount(WorkflowGraphCanvas, {
        props: { definition, teams: [teams[2]] },
        global: canvasGlobal,
      });
      try {
        wrapper.getComponent(TeamsFlowCanvas).vm.$emit("selectNode", "first");
        await flushPromises();
        const select = wrapper.getComponent<any>(
          '[data-testid="team-call-version"]',
        );
        select.vm.$emit("update:modelValue", 1);
        await flushPromises();
        select.vm.$emit("update:modelValue", 2);
        await flushPromises();
        expect(wrapper.text()).toContain("child.unavailable");
        expect(wrapper.emitted("validation")?.at(-1)).toEqual([true]);
        select.vm.$emit("update:modelValue", 3);
        await flushPromises();
        expect(wrapper.text()).not.toContain("child.unavailable");
        expect(wrapper.emitted("validation")?.at(-1)).toEqual([false]);
        if (outcome === "success") resolveOld(teams[0]);
        else rejectOld(new Error("Old unavailable"));
        await flushPromises();
        expect(wrapper.text()).not.toContain("child.unavailable");
        expect(wrapper.emitted("validation")?.at(-1)).toEqual([false]);
        expect(
          wrapper.getComponent(TeamsFlowCanvas).props("nodes")[0].data
            .configLabel,
        ).toBe("v3");
        expect(wrapper.emitted("update:definition")).toBeUndefined();
      } finally {
        wrapper.unmount();
        vi.restoreAllMocks();
      }
    },
  );
  it.each([
    ["success", "success"],
    ["failure", "success"],
    ["success", "failure"],
    ["failure", "failure"],
  ])(
    "ignores old %s after the latest selection's %s",
    async (oldOutcome, latestOutcome) => {
      let resolveOld!: (value: any) => void;
      let rejectOld!: (reason: Error) => void;
      const delayed = new Promise<any>((resolve, reject) => {
        resolveOld = resolve;
        rejectOld = reject;
      });
      vi.mocked(workflowAssetsApi.get)
        .mockReset()
        .mockImplementation((_id, version) =>
          version === 1
            ? delayed
            : latestOutcome === "success"
            ? Promise.resolve(teams[1])
            : Promise.reject(new Error("Latest unavailable")),
        );
      const wrapper = mount(WorkflowGraphCanvas, {
        props: { definition, teams: [teams[2]] },
        global: canvasGlobal,
      });
      try {
        wrapper.getComponent(TeamsFlowCanvas).vm.$emit("selectNode", "first");
        await flushPromises();
        const select = wrapper.getComponent<any>(
          '[data-testid="team-call-version"]',
        );
        select.vm.$emit("update:modelValue", 1);
        await flushPromises();
        select.vm.$emit("update:modelValue", 2);
        await flushPromises();
        const before =
          wrapper.emitted("update:definition")?.at(-1)?.[0] ?? definition;
        const expected = latestOutcome === "success" ? 2 : 3;
        expect((before as any).tasks[0].team.version).toBe(expected);
        const editCount = wrapper.emitted("update:definition")?.length ?? 0;
        if (oldOutcome === "success") resolveOld(teams[0]);
        else rejectOld(new Error("Old unavailable"));
        await flushPromises();
        expect(wrapper.emitted("update:definition")?.length ?? 0).toBe(
          editCount,
        );
        expect(
          wrapper.getComponent(TeamsFlowCanvas).props("nodes")[0].data
            .configLabel,
        ).toBe(`v${expected}`);
        const saved: any =
          wrapper.emitted("update:definition")?.at(-1)?.[0] ?? definition;
        const key = latestOutcome === "success" ? "newGoal" : "goal";
        expect(saved.tasks[0].inputs).toEqual({
          goal: { input: "goal" },
          [key]: { input: key },
        });
        expect(saved.tasks[0].output_contract.required).toEqual([key]);
        expect(wrapper.emitted("validation")?.at(-1)).toEqual([
          latestOutcome === "failure",
        ]);
        expect(wrapper.text().includes("child.unavailable")).toBe(
          latestOutcome === "failure",
        );
      } finally {
        wrapper.unmount();
        vi.restoreAllMocks();
      }
    },
  );

  it.each(
    [
      "switch",
      "switch-back",
      "delete",
      "reload",
      "reload-same",
      "readonly",
      "unmount",
    ].flatMap((context) =>
      ["success", "failure"].map((outcome) => [context, outcome]),
    ),
  )(
    "discards late %s-context %s after its editor context expires",
    async (context, outcome) => {
      let resolveOld!: (value: any) => void;
      let rejectOld!: (reason: Error) => void;
      const delayed = new Promise<any>((resolve, reject) => {
        resolveOld = resolve;
        rejectOld = reject;
      });
      vi.mocked(workflowAssetsApi.get).mockReset().mockReturnValue(delayed);
      const wrapper = mount(WorkflowGraphCanvas, {
        props: { definition, teams: [teams[2]] },
        global: canvasGlobal,
      });
      try {
        const canvas = wrapper.getComponent(TeamsFlowCanvas);
        canvas.vm.$emit("selectNode", "first");
        await flushPromises();
        wrapper
          .getComponent<any>('[data-testid="team-call-version"]')
          .vm.$emit("update:modelValue", 1);
        await flushPromises();
        if (context === "switch" || context === "switch-back") {
          canvas.vm.$emit("selectNode", "second");
          await flushPromises();
          if (context === "switch-back") {
            canvas.vm.$emit("selectNode", "first");
            await flushPromises();
          }
        } else if (context === "delete") {
          await wrapper
            .findAll("button")
            .find((button) => button.text() === "graph.removeTask")!
            .trigger("click");
        } else if (context === "reload" || context === "reload-same") {
          await wrapper.setProps({
            definition: {
              ...JSON.parse(JSON.stringify(definition)),
              name: context === "reload" ? "Reopened parent" : definition.name,
            },
          });
        } else if (context === "readonly")
          await wrapper.setProps({ readonly: true });
        else wrapper.unmount();
        await flushPromises();
        const editCount = wrapper.emitted("update:definition")?.length ?? 0;
        const validation = wrapper.emitted("validation")?.at(-1);
        if (outcome === "success") resolveOld(teams[0]);
        else rejectOld(new Error("Obsolete failure"));
        await flushPromises();
        expect(wrapper.emitted("update:definition")?.length ?? 0).toBe(
          editCount,
        );
        expect(wrapper.emitted("validation")?.at(-1)).toEqual(validation);
        if (context !== "unmount") {
          expect(wrapper.text()).not.toContain("child.unavailable");
          const node = wrapper
            .getComponent(TeamsFlowCanvas)
            .props("nodes")
            .find((node: any) => node.id === "first");
          if (context !== "delete") expect(node?.data.configLabel).toBe("v3");
          expect(wrapper.emitted("validation")?.at(-1)).toEqual([false]);
        }
      } finally {
        if (context !== "unmount") wrapper.unmount();
        vi.restoreAllMocks();
      }
    },
  );

  it.each(["success", "failure"])(
    "ignores a background definition read's late %s after reload",
    async (outcome) => {
      let resolveOld!: (value: any) => void;
      let rejectOld!: (reason: Error) => void;
      const delayed = new Promise<any>((resolve, reject) => {
        resolveOld = resolve;
        rejectOld = reject;
      });
      vi.mocked(workflowAssetsApi.get).mockReset().mockReturnValue(delayed);
      const wrapper = mount(WorkflowGraphCanvas, {
        props: {
          definition: {
            ...definition,
            tasks: [
              {
                ...definition.tasks[0],
                team: { asset_id: "research", version: 1 },
              },
            ],
          },
          teams: [teams[2]],
        },
        global: canvasGlobal,
      });
      try {
        await flushPromises();
        await wrapper.setProps({ definition });
        await flushPromises();
        if (outcome === "success") resolveOld(teams[0]);
        else rejectOld(new Error("Old definition unavailable"));
        await flushPromises();
        expect(wrapper.emitted("validation")?.at(-1)).toEqual([false]);
        expect(wrapper.text()).not.toContain("child.unavailable");
        expect(
          wrapper.getComponent(TeamsFlowCanvas).props("nodes")[0].data
            .configLabel,
        ).toBe("v3");
      } finally {
        wrapper.unmount();
        vi.restoreAllMocks();
      }
    },
  );
});
