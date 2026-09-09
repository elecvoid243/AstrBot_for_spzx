// Author: elecvoid243
// Date: 2026-07-26
// Plan: docs/superpowers/plans/2026-07-26-subagent-chatui-progress.md (Task 5)
// Leaf-module unit tests, runnable from a bare node runner (vitest).
import { describe, expect, it } from "vitest";
import { mount, type DOMWrapper } from "@vue/test-utils";
import {
  applySubAgentEvent,
  type SubAgentRunPart,
} from "./subagentRunReducer.ts";
import {
  normalizeMessageParts,
  type MessagePart,
} from "./normalizeMessageParts.ts";
import SubAgentRunBlock from "@/components/chat/message_list_comps/SubAgentRunBlock.vue";

function ev(runId: string, kind: string, payload: any, agent = "researcher") {
  return { subagent_run_id: runId, agent_name: agent, kind, payload, ts: 1 };
}

describe("applySubAgentEvent", () => {
  it("folds a full lifecycle into one part at first-seen position", () => {
    const parts: MessagePart[] = [{ type: "plain", text: "before" }];
    applySubAgentEvent(parts, ev("sa_1", "started", { input_preview: "task" }));
    applySubAgentEvent(parts, ev("sa_1", "text_delta", { text: "Let me " }));
    applySubAgentEvent(parts, ev("sa_1", "reasoning_delta", { text: "hmm" }));
    applySubAgentEvent(
      parts,
      ev("sa_1", "tool_call", { id: "c1", name: "web_search", args: {} }),
    );
    applySubAgentEvent(
      parts,
      ev("sa_1", "tool_call_result", { id: "c1", result: "found" }),
    );
    applySubAgentEvent(parts, ev("sa_1", "text_delta", { text: "Final " }));
    applySubAgentEvent(parts, ev("sa_1", "text_delta", { text: "answer" }));
    applySubAgentEvent(
      parts,
      ev("sa_1", "completed", {
        result_text: "Final answer",
        execution_time: 3,
      }),
    );

    expect(parts).toHaveLength(2);
    const part = parts[1] as SubAgentRunPart;
    expect(part.type).toBe("subagent_run");
    // Result shows only the final LLM output, not intermediate narration.
    expect(part.text).toBe("Final answer");
    expect(part.reasoning).toBe("hmm");
    expect(part.tool_calls).toEqual([
      {
        id: "c1",
        name: "web_search",
        args: {},
        result: "found",
        ts: 1,
        finished_ts: 1,
      },
    ]);
    // Intermediate text stays in the chronological activity log; the final
    // turn's streamed text is dropped (it lives in part.text).
    expect(part.activity).toEqual([
      { kind: "text", text: "Let me " },
      { kind: "think", text: "hmm" },
      {
        kind: "tool_call",
        call: {
          id: "c1",
          name: "web_search",
          args: {},
          result: "found",
          ts: 1,
          finished_ts: 1,
        },
      },
    ]);
    expect(part.status).toBe("completed");
    expect(part.execution_time).toBe(3);
  });

  it("keeps concurrent runs separate", () => {
    const parts: MessagePart[] = [];
    applySubAgentEvent(parts, ev("sa_1", "text_delta", { text: "A" }));
    applySubAgentEvent(
      parts,
      ev("sa_2", "text_delta", { text: "B" }, "writer"),
    );
    expect(parts).toHaveLength(2);
    expect((parts[0] as SubAgentRunPart).activity).toEqual([
      { kind: "text", text: "A" },
    ]);
    expect((parts[1] as SubAgentRunPart).agent_name).toBe("writer");
  });

  it("stamps tool call start/finish times from event ts", () => {
    const parts: MessagePart[] = [];
    applySubAgentEvent(parts, {
      ...ev("sa_t", "tool_call", { id: "c1", name: "t1" }),
      ts: 10,
    });
    applySubAgentEvent(parts, {
      ...ev("sa_t", "tool_call_result", { id: "c1", result: "ok" }),
      ts: 12.5,
    });
    const part = parts[0] as SubAgentRunPart;
    // ToolCallCard renders elapsed time from ts->finished_ts; without these
    // stamps every call looks permanently "running".
    expect(part.tool_calls[0].ts).toBe(10);
    expect(part.tool_calls[0].finished_ts).toBe(12.5);
  });

  it("marks failure kinds and keeps error", () => {
    const parts: MessagePart[] = [];
    applySubAgentEvent(parts, ev("sa_1", "started", { input_preview: "" }));
    applySubAgentEvent(parts, ev("sa_1", "timeout", { error: "boom" }));
    const part = parts[0] as SubAgentRunPart;
    expect(part.status).toBe("timeout");
    expect(part.error).toBe("boom");
  });

  it("ignores malformed data without throwing", () => {
    const parts: MessagePart[] = [];
    applySubAgentEvent(parts, null);
    applySubAgentEvent(parts, { kind: "text_delta" }); // missing run id
    applySubAgentEvent(parts, "junk");
    expect(parts).toHaveLength(0);
  });
});

describe("SubAgentRunBlock rendering order", () => {
  it("renders intermediate text chronologically inside the execution timeline", () => {
    const part = {
      type: "subagent_run",
      subagent_run_id: "sa_text",
      agent_name: "researcher",
      status: "completed",
      input_preview: "task",
      text: "final answer",
      reasoning: "",
      tool_calls: [],
      activity: [
        { kind: "text", text: "Let me start" },
        { kind: "think", text: "hmm" },
        { kind: "tool_call", call: { id: "c1", name: "search" } },
        { kind: "text", text: "Search works" },
      ],
      execution_time: 1,
    };

    const wrapper = mount(SubAgentRunBlock, {
      props: { part, isDark: false },
      global: {
        stubs: {
          VIcon: { template: "<i><slot /></i>" },
          VExpandTransition: { template: "<div><slot /></div>" },
          MarkdownMessagePart: true,
          ReasoningTimeline: {
            props: ["parts"],
            template:
              '<div class="subagent-reasoning-timeline">{{ parts.map((p) => p.type).join(",") }}</div>',
          },
        },
      },
    });

    const timeline = wrapper.find(".subagent-reasoning-timeline");
    expect(timeline.exists()).toBe(true);
    // Intermediate narration appears in chronological order alongside
    // thinking and tool calls.
    expect(timeline.text()).toBe("text,think,tool_call,text");
  });

  it("renders reasoning and tool calls before the final result", () => {
    const part = {
      type: "subagent_run",
      subagent_run_id: "sa_1",
      agent_name: "researcher",
      status: "completed",
      input_preview: "task",
      text: "647",
      reasoning: "I should read the files first.",
      tool_calls: [{ id: "c1", name: "astrbot_file_read_tool", args: {} }],
      execution_time: 9,
    };

    const wrapper = mount(SubAgentRunBlock, {
      props: { part, isDark: false },
      global: {
        stubs: {
          VIcon: { template: "<i><slot /></i>" },
          VExpandTransition: { template: "<div><slot /></div>" },
          MarkdownMessagePart: {
            props: ["content"],
            template: '<div class="subagent-final-result">{{ content }}</div>',
          },
          ReasoningTimeline: {
            props: ["parts"],
            template:
              '<div class="subagent-reasoning-timeline">{{ parts.map((p) => p.type).join(",") }}</div>',
          },
        },
      },
    });

    const timeline = wrapper.find(".subagent-reasoning-timeline");
    const result = wrapper.find(".subagent-final-result");
    expect(timeline.exists()).toBe(true);
    expect(result.exists()).toBe(true);
    expect(timeline.text()).toBe("think,tool_call");
    expect(
      timeline.element.compareDocumentPosition(result.element) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
  it("renders task, execution, and result as independent collapsible sections", async () => {
    const part = {
      type: "subagent_run",
      subagent_run_id: "sa_sections",
      agent_name: "researcher",
      status: "completed",
      input_preview: "task preview",
      input_full: "full task",
      text: "final result",
      reasoning: "thinking",
      tool_calls: [{ id: "c1", name: "read", args: {} }],
      execution_time: 2,
    };

    const wrapper = mount(SubAgentRunBlock, {
      props: { part, isDark: false },
      global: {
        stubs: {
          VIcon: { template: "<i><slot /></i>" },
          VExpandTransition: { template: "<div><slot /></div>" },
          MarkdownMessagePart: {
            props: ["content"],
            template: '<div class="subagent-final-result">{{ content }}</div>',
          },
          ReasoningTimeline: {
            template: '<div class="subagent-execution-content">execution</div>',
          },
        },
      },
    });

    function isHidden(content: DOMWrapper<Element>) {
      return (content.element as HTMLElement).style.display === "none";
    }
    expect(wrapper.find(".subagent-section-task").exists()).toBe(true);
    expect(wrapper.find(".subagent-section-execution").exists()).toBe(true);
    expect(wrapper.find(".subagent-section-result").exists()).toBe(true);
    // Section labels come from i18n (zh-CN in tests).
    expect(
      wrapper.find(".subagent-section-task .section-header").text(),
    ).toContain("任务");
    expect(
      wrapper.find(".subagent-section-execution .section-header").text(),
    ).toContain("执行");
    expect(
      wrapper.find(".subagent-section-result .section-header").text(),
    ).toContain("结果");
    expect(
      isHidden(wrapper.find(".subagent-section-task .section-content")),
    ).toBe(true);
    expect(
      isHidden(wrapper.find(".subagent-section-execution .section-content")),
    ).toBe(true);
    // Result stays expanded by default so the final output is visible.
    expect(
      isHidden(wrapper.find(".subagent-section-result .section-content")),
    ).toBe(false);

    await wrapper
      .find(".subagent-section-task .section-header")
      .trigger("click");
    expect(
      isHidden(wrapper.find(".subagent-section-task .section-content")),
    ).toBe(false);
    expect(
      isHidden(wrapper.find(".subagent-section-execution .section-content")),
    ).toBe(true);

    await wrapper
      .find(".subagent-section-execution .section-header")
      .trigger("click");
    expect(
      isHidden(wrapper.find(".subagent-section-execution .section-content")),
    ).toBe(false);
    expect(
      isHidden(wrapper.find(".subagent-section-task .section-content")),
    ).toBe(false);

    await wrapper
      .find(".subagent-section-result .section-header")
      .trigger("click");
    expect(
      isHidden(wrapper.find(".subagent-section-result .section-content")),
    ).toBe(true);
  });

  it("expands the truncated task to reveal the full input", async () => {
    const preview = "short preview…";
    const full = "short preview plus the complete task details";
    const part = {
      type: "subagent_run",
      subagent_run_id: "sa_2",
      agent_name: "weather_reporter",
      status: "completed",
      input_preview: preview,
      input_full: full,
      text: "done",
      reasoning: "",
      tool_calls: [],
      execution_time: 1,
    };

    const wrapper = mount(SubAgentRunBlock, {
      props: { part, isDark: false },
      global: {
        stubs: {
          VIcon: { template: "<i><slot /></i>" },
          VExpandTransition: { template: "<div><slot /></div>" },
          MarkdownMessagePart: true,
          ReasoningTimeline: true,
        },
      },
    });

    await wrapper
      .find(".subagent-section-task .section-header")
      .trigger("click");
    const taskContent = wrapper.find(".subagent-section-task .section-text");
    expect(taskContent.text()).toBe(preview);
    const toggle = wrapper.find(".task-expand-toggle");
    expect(toggle.exists()).toBe(true);
    await toggle.trigger("click");
    expect(wrapper.find(".subagent-section-task .section-text").text()).toBe(
      full,
    );
    await toggle.trigger("click");
    expect(wrapper.find(".subagent-section-task .section-text").text()).toBe(
      preview,
    );
  });

  it("hides the expand toggle when there is no extra content", () => {
    const part = {
      type: "subagent_run",
      subagent_run_id: "sa_3",
      agent_name: "a",
      status: "completed",
      input_preview: "short",
      input_full: "short",
      text: "done",
      reasoning: "",
      tool_calls: [],
      execution_time: 1,
    };

    const wrapper = mount(SubAgentRunBlock, {
      props: { part, isDark: false },
      global: {
        stubs: {
          VIcon: { template: "<i><slot /></i>" },
          VExpandTransition: { template: "<div><slot /></div>" },
          MarkdownMessagePart: true,
          ReasoningTimeline: true,
        },
      },
    });

    expect(wrapper.find(".task-expand-toggle").exists()).toBe(false);
  });

  it("starts collapsed while the run is streaming and expands on demand", async () => {
    const part = {
      type: "subagent_run",
      subagent_run_id: "sa_fab",
      agent_name: "researcher",
      status: "running",
      input_preview: "task",
      text: "partial answer",
      reasoning: "thinking",
      tool_calls: [],
      execution_time: null,
    };

    const wrapper = mount(SubAgentRunBlock, {
      props: { part, isDark: false },
      global: {
        stubs: {
          VIcon: { template: "<i><slot /></i>" },
          VExpandTransition: { template: "<div><slot /></div>" },
          MarkdownMessagePart: true,
          ReasoningTimeline: true,
        },
      },
    });

    // jsdom's style object desyncs after display toggles none → "" → none
    // (cssstyle quirk), and isVisible() trusts getComputedStyle which jsdom
    // ignores for v-show. Read the serialized attribute instead.
    function isHidden() {
      return Boolean(
        wrapper
          .find(".subagent-run-body")
          .element.getAttribute("style")
          ?.includes("display: none"),
      );
    }

    // Default is folded even while streaming: the user opts in to the
    // detail, and a folded card has nothing to fold back yet.
    expect(isHidden()).toBe(true);
    expect(wrapper.find(".subagent-collapse-fab").exists()).toBe(false);

    await wrapper.find(".subagent-run-header").trigger("click");

    expect(isHidden()).toBe(false);
    const fab = wrapper.find(".subagent-collapse-fab");
    expect(fab.exists()).toBe(true);
    expect(fab.attributes("aria-label")).toBe("折叠执行卡片");

    // The sticky button folds the card again; it leaves with the body.
    await fab.trigger("click");
    expect(isHidden()).toBe(true);
    expect(wrapper.find(".subagent-collapse-fab").exists()).toBe(false);
  });

  it("only shows the collapse button while the card is expanded", async () => {
    const part = {
      type: "subagent_run",
      subagent_run_id: "sa_fab_done",
      agent_name: "researcher",
      status: "completed",
      input_preview: "task",
      text: "done",
      reasoning: "",
      tool_calls: [],
      execution_time: 2,
    };

    const wrapper = mount(SubAgentRunBlock, {
      props: { part, isDark: false },
      global: {
        stubs: {
          VIcon: { template: "<i><slot /></i>" },
          VExpandTransition: { template: "<div><slot /></div>" },
          MarkdownMessagePart: true,
          ReasoningTimeline: true,
        },
      },
    });

    // Finished runs load folded as well: nothing to collapse yet.
    expect(wrapper.find(".subagent-collapse-fab").exists()).toBe(false);

    await wrapper.find(".subagent-run-header").trigger("click");

    expect(wrapper.find(".subagent-collapse-fab").exists()).toBe(true);
  });
});

describe("normalizeMessageParts subagent_run passthrough", () => {
  it("preserves subagent_run parts from history", () => {
    const stored = [
      { type: "plain", text: "hi" },
      {
        type: "subagent_run",
        subagent_run_id: "sa_1",
        agent_name: "r",
        status: "completed",
        text: "done",
        tool_calls: [],
      },
    ];
    const normalized = normalizeMessageParts(stored);
    expect(normalized).toHaveLength(2);
    expect(normalized[1].type).toBe("subagent_run");
    expect((normalized[1] as any).subagent_run_id).toBe("sa_1");
  });

  it("interleaves reasoning and tool calls chronologically", () => {
    const parts: MessagePart[] = [];
    const evt = (kind: string, payload: Record<string, unknown>) => ({
      subagent_run_id: "sa_i",
      agent_name: "a",
      kind,
      payload,
      ts: 1.0,
    });
    applySubAgentEvent(parts, evt("reasoning_delta", { text: "think1 " }));
    applySubAgentEvent(parts, evt("tool_call", { id: "c1", name: "t1" }));
    applySubAgentEvent(parts, evt("reasoning_delta", { text: "think2 " }));
    applySubAgentEvent(parts, evt("tool_call", { id: "c2", name: "t2" }));
    applySubAgentEvent(parts, evt("tool_call", { id: "c3", name: "t3" }));
    applySubAgentEvent(parts, evt("reasoning_delta", { text: "think3" }));
    applySubAgentEvent(
      parts,
      evt("tool_call_result", { id: "c1", result: "r1" }),
    );

    const part = parts[0] as unknown as SubAgentRunPart;
    expect(part.activity).toEqual([
      { kind: "think", text: "think1 " },
      {
        kind: "tool_call",
        call: { id: "c1", name: "t1", ts: 1.0, result: "r1", finished_ts: 1.0 },
      },
      { kind: "think", text: "think2 " },
      { kind: "tool_call", call: { id: "c2", name: "t2", ts: 1.0 } },
      { kind: "tool_call", call: { id: "c3", name: "t3", ts: 1.0 } },
      { kind: "think", text: "think3" },
    ]);
  });
});
