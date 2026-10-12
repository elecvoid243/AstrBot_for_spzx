import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import { reactive } from "vue";
import WorkflowGraphCanvas from "./WorkflowGraphCanvas.vue";
import { VueFlow } from "@vue-flow/core";

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

describe("canvas asset editing", () => {
  it("undoes and redoes a complete drag gesture", async () => {
    const wrapper = mount(WorkflowGraphCanvas, {
      props: {
        definition: {
          name: "Report",
          tasks: [
            {
              id: "writer",
              name: "Writer",
              member: "writer",
              role_id: "role",
              role_version: 1,
              task: "Write",
              position: { x: 0, y: 0 },
            },
          ],
          edges: [],
        },
      },
      global: {
        stubs: {
          VueFlow: true,
          Background: true,
          Controls: true,
          MiniMap: true,
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
    const flow = wrapper.findComponent(VueFlow);
    for (let x = 1; x <= 5; x++)
      flow.vm.$emit("nodesChange", [
        {
          type: "position",
          id: "writer",
          position: { x, y: 0 },
          dragging: true,
        },
      ]);
    flow.vm.$emit("nodeDragStop", {
      node: { id: "writer", position: { x: 5, y: 0 } },
      nodes: [{ id: "writer", position: { x: 5, y: 0 } }],
    });
    await flushPromises();
    const undo = wrapper
      .findAll("button")
      .find((button) => button.text() === "graph.undo")!;
    await undo.trigger("click");
    let events: any[] = wrapper.emitted("update:definition")!;
    expect(events[events.length - 1][0].tasks[0].position).toEqual({
      x: 0,
      y: 0,
    });
    const redo = wrapper
      .findAll("button")
      .find((button) => button.text() === "graph.redo")!;
    await redo.trigger("click");
    events = wrapper.emitted("update:definition")!;
    expect(events[events.length - 1][0].tasks[0].position).toEqual({
      x: 5,
      y: 0,
    });
    wrapper.unmount();
  });
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
