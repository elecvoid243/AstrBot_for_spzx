import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import WorkflowRunProcess from "./WorkflowRunProcess.vue";

vi.mock("@/stores/customizer", () => ({
  useCustomizerStore: () => ({ isDark: false }),
}));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

describe("persisted model delivery", () => {
  it("merges ordered deltas and one complete response by receipt", async () => {
    const record = (record_id: string, kind: string, data: any) => ({
      record_id,
      kind,
      data,
      run_id: "task",
      attempt_id: "attempt",
    });
    const run: any = {
      state: {},
      process: [
        record("start", "model_delivery", {
          receipt_id: "delivery",
          invocation_id: "request",
          status: "started",
        }),
        record("second", "model_delta", {
          receipt_id: "delivery",
          sequence: 1,
          text: " world",
          reasoning_content: "",
        }),
        record("first", "model_delta", {
          receipt_id: "delivery",
          sequence: 0,
          text: "Hello",
          reasoning_content: "Thought",
        }),
      ],
    };
    const wrapper = mount(WorkflowRunProcess, {
      props: { run },
      global: {
        stubs: {
          MarkdownMessagePart: {
            props: ["content"],
            template: "<p>{{ content }}</p>",
          },
          ReasoningBlock: {
            props: ["reasoning"],
            template: "<p>{{ reasoning }}</p>",
          },
          ToolCallCard: true,
        },
      },
    });
    expect(wrapper.findAll('[data-testid="model-delivery"]')).toHaveLength(1);
    expect(wrapper.text()).toContain("Hello world");
    expect(wrapper.text()).toContain("Thought");
    const end = record("end", "model_delivery", {
      receipt_id: "delivery",
      status: "completed",
      text: "Hello world",
    });
    const response = record("response", "model_response", {
      receipt_id: "delivery",
      invocation_id: "request",
      text: "Hello world",
    });
    await wrapper.setProps({
      run: { ...run, process: [...run.process, end, response] },
    });
    expect(wrapper.findAll('[data-testid="model-delivery"]')).toHaveLength(1);
    expect(wrapper.text().match(/Hello world/g)).toHaveLength(1);
    wrapper.unmount();
  });
  it("marks a gap until the missing saved stream piece arrives", async () => {
    const delta = (sequence: number) => ({record_id:`delta-${sequence}`,kind:"model_delta",run_id:"task",attempt_id:"attempt",data:{receipt_id:"receipt",sequence,text:String(sequence)}});
    const run = {status:"running",state:{},process:[delta(0),delta(2)]};
    const wrapper = mount(WorkflowRunProcess,{props:{run},global:{stubs:{MarkdownMessagePart:{props:["content"],template:"<p>{{ content }}</p>"}, ReasoningBlock:true, ToolCallCard:true}}});
    expect(wrapper.find('[data-testid="missing-stream-chunks"]').exists()).toBe(true);
    await wrapper.setProps({run:{...run,process:[delta(0),delta(2),delta(1)]}});
    expect(wrapper.find('[data-testid="missing-stream-chunks"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("012");
    wrapper.unmount();
  });
  it("keeps persisted delivery and tool order and displays errors", () => {
    const record = (record_id: string, kind: string, data: any) => ({ record_id, kind, data, run_id: "task", attempt_id: "attempt" });
    const run = { status: "failed", state: {}, process: [
      record("a", "model_delivery", {receipt_id: "one", status: "completed", text: "Before tool"}),
      record("b", "tool_start", {tool_record_id: "tool", id: "call", name: "search", args: {}}),
      record("c", "tool_result", {id: "call", tool_record_id: "tool", result: "error: not found"}),
      record("d", "model_delivery", {receipt_id: "two", status: "completed", text: "After tool"}),
    ] };
    const wrapper = mount(WorkflowRunProcess, {props: {run}, global: {stubs: {
      MarkdownMessagePart: {props: ["content"], template: "<p>{{ content }}</p>"},
      ReasoningBlock: true,
      ToolCallCard: {props: ["toolCall"], template: "<p>{{ toolCall.result }}</p>"},
    }}});
    const text = wrapper.text();
    expect(text.indexOf("Before tool")).toBeLessThan(text.indexOf("error: not found"));
    expect(text.indexOf("error: not found")).toBeLessThan(text.indexOf("After tool"));
    wrapper.unmount();
  });
  it("keeps reused provider tool IDs separate and leaves a failed partial call unknown", () => {
    const record = (record_id: string, kind: string, data: any) => ({ record_id, kind, data, run_id: "task", attempt_id: "attempt" });
    const start = (id: string) => record(id, "tool_start", {tool_record_id: id, id: "same-provider-id", name: id, args: {}});
    const run = {status: "blocked", state: {}, process: [start("first"),
      record("first-result", "tool_result", {tool_record_id: "first", id: "same-provider-id", result: "first result"}),
      record("first-status", "tool_status", {tool_record_id: "first", id: "same-provider-id", status: "completed"}),
      start("second"),
      record("second-progress", "tool_progress", {tool_record_id: "second", id: "same-provider-id", index: 0, raw_result: {content: [{type: "text", text: "partial result"}]}}),
      record("second-status", "tool_status", {tool_record_id: "second", id: "same-provider-id", status: "unknown"})]};
    const wrapper = mount(WorkflowRunProcess, {props: {run}, global: {stubs: {
      ToolCallCard: {props: ["toolCall"], template: "<p>{{ toolCall.name }}: {{ toolCall.result }}</p>"},
      MarkdownMessagePart: true, ReasoningBlock: true,
    }}});
    const cards = wrapper.findAll('[data-testid="observed-tool"]');
    expect(cards).toHaveLength(2);
    expect(cards[0].text()).toContain("progress.toolStates.completed");
    expect(cards[0].text()).toContain("first result");
    expect(cards[0].text()).not.toContain("partial result");
    expect(cards[1].text()).toContain("progress.toolStates.unknown");
    expect(cards[1].text()).toContain("partial result");
    expect(cards[1].text()).not.toContain("first result");
    wrapper.unmount();
  });
  it("renders receipt-bearing final-only responses and fills a missing progress observation once", async () => {
    const record = (record_id: string, kind: string, data: any) => ({ record_id, kind, data, run_id: "task", attempt_id: "attempt" });
    const result = {tool_record_id: "tool", id: "call", index: 0, raw_result: {content: [{type: "text", text: "Recovered evidence"}]}};
    const process = [record("response", "model_response", {receipt_id: "parameter", invocation_id: "query", role: "assistant", text: "Parameter response"}),
      record("start", "tool_start", {tool_record_id: "tool", id: "call", name: "search", args: {}}),
      record("raw", "tool_result", result)];
    const wrapper = mount(WorkflowRunProcess, {props: {run: {status: "running", state: {}, process}}, global: {stubs: {
      MarkdownMessagePart: {props: ["content"], template: "<p>{{ content }}</p>"}, ReasoningBlock: true,
      ToolCallCard: {props: ["toolCall"], template: "<p>{{ toolCall.result }}</p>"},
    }}});
    expect(wrapper.text()).toContain("Parameter response");
    expect(wrapper.text()).toContain("progress.finalOnly");
    expect(wrapper.text().match(/Recovered evidence/g)).toHaveLength(1);
    await wrapper.setProps({run: {status: "running", state: {}, process: [...process,
      record("progress", "tool_progress", result),
      record("delivery", "model_delivery", {receipt_id: "parameter", status: "completed", text: "Parameter response"})]}});
    expect(wrapper.text().match(/Recovered evidence/g)).toHaveLength(1);
    expect(wrapper.text().match(/Parameter response/g)).toHaveLength(1);
    wrapper.unmount();
  });
});
