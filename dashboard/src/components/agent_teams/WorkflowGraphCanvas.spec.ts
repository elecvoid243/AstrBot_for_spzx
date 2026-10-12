import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import { reactive } from "vue";
import WorkflowGraphCanvas from "./WorkflowGraphCanvas.vue";

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

describe("canvas asset editing", () => {
  it("keeps declared workflow inputs and name when a role is added after metadata editing", async () => {
    const definition = reactive({
      name: "",
      tasks: [],
      edges: [],
      required_inputs: [] as string[],
    });
    const wrapper = mount(WorkflowGraphCanvas, {
      props: {
        definition,
        roles: [
          { role_id: "writer", version: 2, definition: { name: "Writer" } },
        ],
      },
      global: {
        stubs: {
          TeamsFlowCanvas: true,
          "v-icon": true,
          "v-chip": true,
          "v-btn": { template: "<button><slot /></button>" },
          "v-text-field": true,
          "v-select": true,
          "v-textarea": true,
          "v-spacer": true,
          "v-alert": true,
          "v-switch": true,
        },
      },
    });
    definition.name = "Evidence report";
    definition.required_inputs = ["topic"];
    await wrapper.setProps({ definition });
    await flushPromises();
    await wrapper.get(".graph-role").trigger("click");
    const saved: any = wrapper.emitted("update:definition")?.at(-1)?.[0];
    expect(saved.name).toBe("Evidence report");
    expect(saved.required_inputs).toEqual(["topic"]);
    expect(saved.tasks[0].role_version).toBe(2);
    wrapper.unmount();
  });
});
