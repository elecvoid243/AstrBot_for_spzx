import { mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";
import WorkflowRunProcess from "./WorkflowRunProcess.vue";

vi.mock("@/stores/customizer", () => ({
  useCustomizerStore: () => ({ isDark: false }),
}));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

it("lets the user inspect each saved or unknown tool call and its recovery owner", async () => {
  const wrapper = mount(WorkflowRunProcess, {
    props: {
      run: {
        state: {},
        tool_calls: [
          {
            record_id: "call-1",
            call_id: "provider-1",
            name: "publish",
            status: "result_saved",
            attempt_id: "original-attempt",
            execution_id: "recovered-owner",
            started_execution_id: "original-owner",
            context_position: null,
            args: { title: "Original report" },
            raw_results: [
              { content: [{ type: "text", text: "Publication receipt 731" }] },
            ],
          },
          {
            record_id: "call-2",
            call_id: "provider-2",
            name: "send",
            status: "unknown",
            attempt_id: "original-attempt",
            execution_id: "recovered-owner",
            started_execution_id: "original-owner",
            context_position: null,
            args: {},
            raw_results: [],
          },
        ],
      },
    },
  });
  const calls = wrapper.findAll('[data-testid="durable-tool-call"]');
  expect(calls).toHaveLength(2);
  expect(calls[0].get("summary").text()).toContain(
    "recovery.toolStates.result_saved",
  );
  expect(calls[1].get("summary").text()).toContain(
    "recovery.toolStates.unknown",
  );
  await calls[0].get("summary").trigger("click");
  expect(calls[0].text()).toContain("Publication receipt 731");
  expect(calls[0].text()).toContain("recovered-owner");
  expect(calls[0].text()).toContain("original-owner");
  expect(calls[0].text()).toContain("recovery.notApplied");
  expect(calls[0].text()).toContain("Original report");
  expect(calls[1].text()).toContain("provider-2");
  wrapper.unmount();
});


it("associates each actual context with its delivery receipt", () => {
  const observation = (text: string) => ({ value: { messages: [{ role: "user", content: text }], tools: [] }, truncated: false });
  const assembly = { value: { sources: [{ kind: "persona", content: null }], injections: [] }, truncated: false };
  const record = (kind: string, receipt: string, data: object = {}) => ({ kind, run_id: "run", attempt_id: "attempt", record_id: `${kind}-${receipt}`, data: { receipt_id: receipt, invocation_id: receipt, ...data } });
  const wrapper = mount(WorkflowRunProcess, { props: { run: { state: {}, status: "completed", process: [
    record("model_delivery", "first", { status: "started" }),
    record("model_context", "first", { model: "model", stage: "main", observation: observation("First actual request"), assembly }),
    record("model_context", "second", { model: "model", stage: "parameters", observation: observation("Parameter request"), assembly }),
  ] } } });
  const cards = wrapper.findAll('[data-testid="model-delivery"]');
  expect(cards).toHaveLength(2);
  expect(cards[0].text()).toContain("First actual request");
  expect(cards[0].text()).not.toContain("Parameter request");
  expect(cards[1].text()).toContain("Parameter request");
  expect(cards[1].text()).toContain("progress.contextStages.parameters");
});
