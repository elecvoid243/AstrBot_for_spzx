import { describe, expect, it } from "vitest";
import {
  applySubAgentEvent,
  type SubAgentRunPart,
} from "./subagentRunReducer.ts";
import {
  normalizeMessageParts,
  type MessagePart,
} from "./normalizeMessageParts.ts";

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

  it("appends user_message activity and marks it relayed", () => {
    const parts: MessagePart[] = [];
    applySubAgentEvent(parts, ev("sa_1", "started", { input_preview: "task" }));
    applySubAgentEvent(
      parts,
      ev("sa_1", "user_message", { seq: 0, text: "note" }),
    );
    applySubAgentEvent(parts, ev("sa_1", "user_message_relayed", { seq: 0 }));
    const part = parts[0] as SubAgentRunPart;
    expect(part.activity.at(-1)).toEqual({
      kind: "user_message",
      text: "note",
      seq: 0,
      relayed: true,
    });
  });

  it("keeps user_message in chronological order between think and tool_call", () => {
    const parts: MessagePart[] = [];
    applySubAgentEvent(parts, ev("sa_1", "started", { input_preview: "task" }));
    applySubAgentEvent(parts, ev("sa_1", "reasoning_delta", { text: "hmm" }));
    applySubAgentEvent(
      parts,
      ev("sa_1", "user_message", { seq: 0, text: "steer it" }),
    );
    applySubAgentEvent(
      parts,
      ev("sa_1", "tool_call", { id: "c1", name: "web_search", args: {} }),
    );
    const part = parts[0] as SubAgentRunPart;
    expect(part.activity.map((a) => a.kind)).toEqual([
      "think",
      "user_message",
      "tool_call",
    ]);
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
    expect(normalized[1]).not.toHaveProperty("text");
    expect(normalized[1]).not.toHaveProperty("activity");
    expect(normalized[1]).not.toHaveProperty("tool_calls");
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
