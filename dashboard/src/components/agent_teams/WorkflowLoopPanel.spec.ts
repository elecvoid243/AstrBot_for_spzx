import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import WorkflowGraphCanvas from "./WorkflowGraphCanvas.vue";
import TeamsFlowCanvas from "./TeamsFlowCanvas.vue";
import WorkflowLoopPanel from "./WorkflowLoopPanel.vue";

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

describe("bounded loop canvas", () => {
  it("configures a bound and collapses the explicit body without deleting tasks", async () => {
    const wrapper = mount(WorkflowGraphCanvas, {
      props: {
        definition: {
          name: "Review",
          result_task_id: "review",
          tasks: [
            {
              id: "write",
              name: "Writer",
              member: "writer",
              role_id: "role",
              role_version: 1,
              task: "Write",
            },
            {
              id: "review",
              name: "Review",
              member: "review",
              role_id: "role",
              role_version: 1,
              task: "Review",
            },
          ],
          edges: [{ source: "write", target: "review" }],
          loops: [
            {
              id: "revision",
              name: "Revision",
              body: ["write", "review"],
              exit_task_id: "review",
              exit_condition: { field: "approved", value: true },
              max_iterations: 3,
              feedback: [
                {
                  source: "review",
                  field: "text",
                  target: "write",
                  input: "draft",
                },
              ],
            },
          ],
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
        },
      },
    });
    try {
      expect(
        wrapper.findComponent(TeamsFlowCanvas).props("nodes"),
      ).toHaveLength(4);
      await wrapper
        .findAll("button")
        .find((button) => button.text() === "graph.layout")!
        .trigger("click");
      const positions = wrapper.findComponent(TeamsFlowCanvas).props("nodes");
      expect(
        positions.find((node: any) => node.id === "__input")!.position.x,
      ).toBeLessThan(
        positions.find((node: any) => node.id === "write")!.position.x - 220,
      );
      expect(
        positions.find((node: any) => node.id === "__output")!.position.x,
      ).toBeGreaterThan(
        positions.find((node: any) => node.id === "review")!.position.x + 220,
      );
      await wrapper
        .get('[data-testid="collapse-loop-revision"]')
        .trigger("click");
      expect(
        wrapper.findComponent(TeamsFlowCanvas).props("nodes"),
      ).toHaveLength(3);
      expect(
        wrapper
          .get('[data-testid="collapse-loop-revision"]')
          .attributes("aria-expanded"),
      ).toBe("false");
      wrapper
        .findAllComponents({ name: "v-text-field" })
        .find((field) => field.attributes("data-testid") === "loop-maximum")!
        .vm.$emit("update:modelValue", "2");
      await flushPromises();
      const saved: any = wrapper.emitted("update:definition")?.at(-1)?.[0];
      expect(saved.loops[0].max_iterations).toBe(2);
      expect(saved.tasks.map((task: any) => task.id)).toEqual([
        "write",
        "review",
      ]);
      await wrapper
        .get('[data-testid="collapse-loop-revision"]')
        .trigger("click");
      expect(
        wrapper.findComponent(TeamsFlowCanvas).props("nodes"),
      ).toHaveLength(4);
      expect(
        wrapper.find("details.loop-group").attributes("open"),
      ).toBeUndefined();
      wrapper.findComponent(TeamsFlowCanvas).vm.$emit("select-node", "write");
      await flushPromises();
      await wrapper
        .findAll("button")
        .find((button) => button.text() === "graph.removeTask")!
        .trigger("click");
      const removed: any = wrapper.emitted("update:definition")!.at(-1)![0];
      expect(removed.loops[0].body).toEqual(["review"]);
      expect(removed.loops[0].feedback).toEqual([]);
      await wrapper
        .findAll("button")
        .find((button) => button.text() === "graph.undo")!
        .trigger("click");
      const restored: any = wrapper.emitted("update:definition")!.at(-1)![0];
      expect(restored.loops[0].body).toEqual(["write", "review"]);
      expect(restored.loops[0].feedback).toHaveLength(1);
    } finally {
      wrapper.unmount();
    }
  });
  it("selects typed exit and feedback fields from task contracts", async () => {
    const definition: any = {
      name: "Review",
      tasks: [
        { id: "write", name: "Writer", inputs: { draft: {} } },
        { id: "review", name: "Review" },
      ],
      loops: [
        {
          id: "revision",
          name: "Revision",
          body: ["write", "review"],
          exit_task_id: "review",
          exit_condition: { field: "approved", operator: "eq", value: true },
          max_iterations: 2,
          feedback: [
            {
              source: "review",
              field: "text",
              target: "write",
              input: "draft",
            },
          ],
        },
      ],
    };
    const wrapper = mount(WorkflowLoopPanel, {
      props: {
        definition,
        collapsed: [],
        issues: [],
        fieldsForTask: (id: string) =>
          id === "review"
            ? [
                { title: "approved · boolean", value: "approved" },
                { title: "score · number", value: "score" },
                { title: "text · string", value: "text" },
              ]
            : [{ title: "text · string", value: "text" }],
      },
      global: {
        stubs: {
          "v-btn": { template: "<button><slot /></button>" },
          "v-select": {
            name: "VSelect",
            props: ["items", "modelValue", "label"],
            emits: ["update:modelValue"],
            template: "<div />",
          },
          "v-text-field": true,
          "v-alert": true,
        },
      },
    });
    const fields = wrapper
      .findAllComponents({ name: "VSelect" })
      .filter((item) => item.props("label") === "graph.field");
    expect(fields).toHaveLength(2);
    expect(fields[0].props("items")).toContainEqual({
      title: "score · number",
      value: "score",
    });
    fields[0].vm.$emit("update:modelValue", "score");
    await flushPromises();
    expect(
      (wrapper.emitted("update:loops")!.at(-1)![0] as any)[0].exit_condition,
    ).toEqual({ field: "score", operator: "eq", value: 0 });
    fields[1].vm.$emit("update:modelValue", "approved");
    await flushPromises();
    expect(
      (wrapper.emitted("update:loops")!.at(-1)![0] as any)[0].feedback[0].field,
    ).toBe("approved");
    wrapper.unmount();
  });
});
