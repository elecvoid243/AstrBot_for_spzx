import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import WorkflowGraphCanvas from "./WorkflowGraphCanvas.vue";
import TeamsFlowCanvas from "./TeamsFlowCanvas.vue";

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

describe("bounded loop canvas", () => {
  it("configures a bound and collapses the explicit body without deleting tasks", async () => {
    const wrapper = mount(WorkflowGraphCanvas, {
      props: {
        definition: {
          name: "Review",
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
      ).toHaveLength(2);
      await wrapper
        .get('[data-testid="collapse-loop-revision"]')
        .trigger("click");
      expect(
        wrapper.findComponent(TeamsFlowCanvas).props("nodes"),
      ).toHaveLength(1);
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
      ).toHaveLength(2);
    } finally {
      wrapper.unmount();
    }
  });
});
