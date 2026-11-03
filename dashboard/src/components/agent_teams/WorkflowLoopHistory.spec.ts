import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import WorkflowLoopHistory from "./WorkflowLoopHistory.vue";
import MarkdownMessagePart from "@/components/chat/message_list_comps/MarkdownMessagePart.vue";

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));
vi.mock("@/stores/customizer", () => ({
  useCustomizerStore: () => ({ isDark: false }),
}));

describe("loop history", () => {
  it("reads second-round feedback as scalar inputs and displays the text result", async () => {
    const task = {
      run_id: "second",
      status: "completed",
      state: { attempt_id: "attempt-2" },
      snapshot: {
        definition: { name: "Writer" },
        inputs: { draft: "old draft", count: 2 },
        iteration_inputs: {
          draft: {
            raw_value: "Revised feedback",
            value: "Revised feedback",
            reference: { artifact_id: "first", version: 1 },
            field: "text",
          },
        },
      },
      result: { text: "# Revised report\nReady to deliver.", approved: true },
      result_ref: { artifact_id: "second", version: 1 },
    };
    const run = {
      snapshot: {
        definition: { tasks: [{ id: "write" }], edges: [], loops: [] },
      },
      loops: [
        {
          loop_id: "revision",
          status: "passed",
          iterations: [
            {
              iteration: 2,
              status: "passed",
              task_runs: { write: "second" },
              tasks: [task],
            },
          ],
        },
      ],
    };
    const wrapper = mount(WorkflowLoopHistory, {
      props: { run },
      global: {
        stubs: {
          WorkflowGraphCanvas: true,
          MarkdownMessagePart: {
            props: ["content"],
            template: "<div>{{ content }}</div>",
          },
        },
      },
    });
    const inputs = wrapper.get('[data-testid="iteration-inputs-second"]');
    expect(inputs.findAll("dt").map((item) => item.text())).toEqual([
      "draft",
      "count",
    ]);
    expect(inputs.findAll("dd").map((item) => item.text())).toEqual([
      "Revised feedback",
      "2",
    ]);
    expect(inputs.text()).not.toContain("old draft");
    expect(wrapper.findComponent(MarkdownMessagePart).props("content")).toBe(
      "# Revised report\nReady to deliver.",
    );
    expect(
      wrapper.get('[data-testid="iteration-result-second"]').find("dd").text(),
    ).toBe("true");
    expect(
      wrapper
        .find("details.mt-2 > pre.loop-raw")
        .element.parentElement?.hasAttribute("open"),
    ).toBe(false);
    await wrapper.get('[data-testid="iteration-task-second"]').trigger("click");
    expect(wrapper.emitted("select")?.[0]).toEqual(["second"]);
    await wrapper.setProps({
      run: {
        ...run,
        loops: [
          {
            ...run.loops[0],
            iterations: [
              {
                ...run.loops[0].iterations[0],
                tasks: [{ ...task, status: "pending" }],
              },
            ],
          },
        ],
      },
    });
    expect(
      wrapper.find('[data-testid="iteration-inputs-second"]').exists(),
    ).toBe(false);
    wrapper.unmount();
  });
});
