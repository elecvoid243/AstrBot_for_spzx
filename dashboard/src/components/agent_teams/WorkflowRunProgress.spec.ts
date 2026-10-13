import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import WorkflowRunProgress from "./WorkflowRunProgress.vue";

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

describe("nested run progress", () => {
  it("shows real child tasks and selects their unique run identities", async () => {
    const child = {
      run_id: "child-task",
      node_id: "coordinator",
      status: "completed",
      snapshot: {
        parent_run_id: "child-scope",
        engine: "single_task_v2",
        definition: { name: "Child coordinator" },
        member: { member_id: "child-member", name: "Child member" },
      },
      state: { attempt_id: "child-attempt" },
    };
    const wrapper = mount(WorkflowRunProgress, {
      props: {
        run: {
          run_id: "root",
          status: "completed",
          snapshot: {
            asset_version: 1,
            definition: { name: "Parent", tasks: [] },
          },
          tasks: [
            {
              run_id: "parent-call",
              node_id: "call",
              status: "completed",
              snapshot: {
                parent_run_id: "root",
                definition: { name: "Team call" },
              },
              state: { attempt_id: "parent-attempt", capacity_released: true },
            },
          ],
          scopes: [
            {
              run_id: "child-scope",
              status: "completed",
              snapshot: {
                asset_version: 1,
                parent_run_id: "parent-call",
                definition: { name: "Pinned child", tasks: [] },
              },
              state: { scope_id: "scope-child" },
              control: { root_run_id: "root" },
              tasks: [child],
            },
          ],
          usage: { model_calls: 20, steps_used: 20 },
        },
        selectedTask: null,
      },
      global: {
        stubs: {
          WorkflowGraphCanvas: true,
          WorkflowLoopHistory: true,
          "v-icon": true,
          "v-chip": true,
          "v-card": { template: "<section><slot /></section>" },
          "v-card-title": { template: "<div><slot /></div>" },
          "v-card-text": { template: "<div><slot /></div>" },
          "v-progress-linear": true,
          "v-spacer": true,
        },
      },
    });
    try {
      expect(wrapper.findAll(".run-task")).toHaveLength(2);
      expect(wrapper.findAll(".run-waiting")).toHaveLength(0);
      await wrapper.findAll(".run-task")[1].trigger("click");
      expect(wrapper.emitted("select")?.at(-1)).toEqual(["child-task"]);
      expect(
        wrapper.get('[data-testid="child-scope"] summary').text(),
      ).toContain("Team call → Pinned child");
      expect(wrapper.text()).toContain("child.modelCalls: 20");
    } finally {
      wrapper.unmount();
    }
  });
});
