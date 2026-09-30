// Author: elecvoid243
// Date: 2026-07-26
// Plan: docs/superpowers/plans/2026-07-26-subagent-chatui-progress.md (Task 5)
// Live reducer for structured `subagent_event` stream payloads. The part is
// pushed at first-seen position and mutated in place so Vue reactivity
// updates the SubAgentRunBlock as tokens arrive.
import type { MessagePart } from "./normalizeMessageParts.ts";

export interface SubAgentToolCall {
  id?: string;
  name?: string;
  args?: Record<string, unknown>;
  result?: string;
  [key: string]: unknown;
}

export type SubAgentActivity =
  | { kind: "think"; text: string }
  | { kind: "text"; text: string }
  | { kind: "tool_call"; call: SubAgentToolCall }
  | { kind: "user_message"; text: string; seq: number; relayed?: boolean };

export interface SubAgentRunPart {
  type: "subagent_run";
  subagent_run_id: string;
  agent_name: string;
  status: "running" | "completed" | "failed" | "timeout";
  input_preview: string;
  input_full?: string;
  text: string;
  reasoning: string;
  tool_calls: SubAgentToolCall[];
  activity: SubAgentActivity[];
  started_ts?: number;
  execution_time?: number | null;
  error?: string;
  [key: string]: unknown;
}

export interface SubAgentEventData {
  subagent_run_id?: string;
  agent_name?: string;
  kind?: string;
  payload?: Record<string, unknown>;
  ts?: number;
}

export function applySubAgentEvent(parts: MessagePart[], data: unknown): void {
  if (!data || typeof data !== "object") return;
  const event = data as SubAgentEventData;
  const runId = String(event.subagent_run_id || "");
  if (!runId) return;
  const kind = String(event.kind || "");
  const payload =
    event.payload && typeof event.payload === "object" ? event.payload : {};

  let part: SubAgentRunPart | undefined;
  for (const p of parts) {
    if (p.type === "subagent_run" && p.subagent_run_id === runId) {
      part = p as unknown as SubAgentRunPart;
      break;
    }
  }
  if (!part) {
    const created: SubAgentRunPart = {
      type: "subagent_run",
      subagent_run_id: runId,
      agent_name: String(event.agent_name || ""),
      status: "running",
      input_preview: "",
      text: "",
      reasoning: "",
      tool_calls: [],
      activity: [],
      started_ts: event.ts,
      execution_time: null,
    };
    parts.push(created as MessagePart);
    part = created;
  }

  if (!Array.isArray(part.activity)) part.activity = [];

  if (kind === "started") {
    part.input_preview = String(payload.input_preview || "");
    part.input_full = String(payload.input_full || "");
  } else if (kind === "text_delta") {
    // Streamed assistant text is intermediate narration; keep it in the
    // chronological activity log. The final answer arrives via the
    // completed event's result_text.
    const text = String(payload.text || "");
    const last = part.activity[part.activity.length - 1];
    if (last && last.kind === "text") {
      last.text += text;
    } else {
      part.activity.push({ kind: "text", text });
    }
  } else if (kind === "reasoning_delta") {
    const text = String(payload.text || "");
    part.reasoning += text;
    // Append to the current think block, or open a new one when a tool
    // call happened in between — this preserves the chronological
    // think -> tool_call -> think -> tool_call order of the LLM loop.
    const last = part.activity[part.activity.length - 1];
    if (last && last.kind === "think") {
      last.text += text;
    } else {
      part.activity.push({ kind: "think", text });
    }
  } else if (kind === "tool_call") {
    const callId = payload.id;
    if (callId != null) {
      const existing = part.tool_calls.find((t) => t.id === callId);
      if (existing) {
        Object.assign(existing, payload);
      } else {
        const call = { ...payload } as SubAgentToolCall;
        if (event.ts != null) call.ts = event.ts;
        part.tool_calls.push(call);
        part.activity.push({ kind: "tool_call", call });
      }
    }
  } else if (kind === "tool_call_result") {
    const callId = payload.id;
    const existing = part.tool_calls.find((t) => t.id === callId);
    if (existing) {
      existing.result = String(payload.result ?? "");
      if (event.ts != null) existing.finished_ts = event.ts;
    } else if (callId != null) {
      const call = { ...payload } as SubAgentToolCall;
      if (event.ts != null) call.finished_ts = event.ts;
      part.tool_calls.push(call);
      part.activity.push({ kind: "tool_call", call });
    }
  } else if (kind === "user_message") {
    // A follow-up the user sent to this running subagent; keep it in the
    // chronological activity log.
    part.activity.push({
      kind: "user_message",
      text: String(payload.text || ""),
      seq: Number(payload.seq ?? -1),
      relayed: false,
    });
  } else if (kind === "user_message_relayed") {
    const seq = Number(payload.seq ?? -1);
    for (let i = part.activity.length - 1; i >= 0; i--) {
      const entry = part.activity[i];
      if (entry.kind === "user_message" && entry.seq === seq) {
        entry.relayed = true;
        break;
      }
    }
  } else if (kind === "completed") {
    part.status = "completed";
    if (payload.result_text) {
      part.text = String(payload.result_text);
    }
    // The final turn's streamed text duplicates result_text; drop it from
    // the activity log so the answer only shows in the Result section.
    while (
      part.activity.length &&
      part.activity[part.activity.length - 1].kind === "text"
    ) {
      part.activity.pop();
    }
    if (typeof payload.execution_time === "number") {
      part.execution_time = payload.execution_time;
    }
  } else if (kind === "failed" || kind === "timeout") {
    part.status = kind;
    if (payload.error) part.error = String(payload.error);
  }
}
