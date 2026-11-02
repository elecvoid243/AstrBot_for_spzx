import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { describe, expect, it, vi } from "vitest";
import WorkflowRunProcess from "./WorkflowRunProcess.vue";

const api = vi.hoisted(() => ({ artifact: vi.fn() }));
vi.mock("@/api/workflowAssets", () => ({ workflowAssetsApi: api }));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

describe("member communication in official task process", () => {
  it("shows delivery and consumption correlations and reads the exact complete version", async () => {
    const complete = "Complete long evidence. ".repeat(3000) + "FULL_END";
    api.artifact.mockResolvedValue({
      artifact_id: "evidence",
      version: 2,
      content: { text: complete },
    });
    const wrapper = mount(WorkflowRunProcess, {
      props: {
        run: {
          state: {},
          messages: [
            {
              message_id: "message-one",
              scope_id: "scope-one",
              sender_member_id: "writer-a",
              recipient_member_id: "writer-b",
              sender_task_run_id: "task-source",
              sender_attempt_id: "attempt-source",
              text: "Please review the exact version",
              artifact_refs: [{ artifact_id: "evidence", version: 2 }],
              consumption: {
                task_run_id: "task-review",
                attempt_id: "attempt-review",
                context_position: 7,
                invocation_id: "model-one",
              },
            },
          ],
        },
      },
      global: { plugins: [createPinia()], stubs: { "v-btn": { template: "<button><slot /></button>" } } },
    });
    const record = wrapper.find('[data-testid="member-message"]');
    expect(record.text()).toContain("writer-a");
    expect(record.text()).toContain("writer-b");
    expect(record.text()).toContain("message-one");
    expect(record.text()).toContain("scope-one");
    expect(record.text()).toContain("task-source");
    expect(record.text()).toContain("attempt-source");
    expect(record.text()).toContain("task-review");
    expect(record.text()).toContain("communication.consumed");
    await record.find("button").trigger("click");
    await flushPromises();
    expect(api.artifact).toHaveBeenCalledWith("evidence", 2);
    expect(record.text()).toContain(complete);
  });
});
