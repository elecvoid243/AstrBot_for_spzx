// Author: elecvoid243
// Date: 2026-07-25
// Plan: docs/superpowers/plans/2026-07-25-webchat-system-event-stream.md Task 5
//
// Leaf module: webchat system event stream payload → chat record updates.
// No `@/api` imports so the module is testable under node --test.
//
// The system stream carries mirrored webchat payloads for turns that have no
// active primary chat run (goal-loop synthetic turns and future system
// flows). Each orphan turn is keyed by its `message_id`; payloads grow a
// live bot record, `complete`/`end` finalize it. The backend persists the
// turn on its side, so on the next history reload the persisted record
// replaces this local one.
//
// ── 2026-07-25 chain_type dispatch (Bug fix, see user report)
// The previous leaf only accepted `type ∈ {"plain","complete","end"}` and
// ignored `payload.chain_type`. As a result, payloads carrying
// `chain_type: "tool_call" | "tool_call_result" | "reasoning" |
// "agent_stats" | "interactive_choice"` were appended as raw plain text —
// goal-loop turns rendered their tool-call JSON, token-usage JSON, and
// thinking tokens inline in the chat bubble, never forming the
// "思考了 X 次，使用了 Y 次工具" reasoning block. This module now mirrors
// the primary `processStreamPayload` dispatch (useMessages.ts:1340+).
//
// Plain text keeps the original eager-push/append behavior so the existing
// leaf tests still pass: each `type: "plain"` with no chain_type (or
// chain_type "plain") pushes/extends a `plain` part immediately. Other
// chain_types flush any trailing plain part first so the part order
// matches the wire order, then dispatch into their own part types.

import {
  isAskUserChoiceToolCall,
  isAskUserChoiceToolCallResult,
  type RecordLike,
} from "./askUserChoiceToolFilter.ts";
import { interactiveChoicePartFromSsePayload } from "./parseInteractiveChoice.ts";

export interface SystemMessagePart {
  type: string;
  text?: string;
  think?: string;
  tool_calls?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface SystemBotRecord {
  id?: string | number;
  created_at?: string;
  content: {
    type: string;
    message: SystemMessagePart[];
    reasoning?: string;
    isLoading?: boolean;
    agentStats?: unknown;
  };
}

export interface SystemStreamState {
  /**
   * sessionId → messageId → live bot record while its turn is in flight.
   *
   * Public contract: `liveBySession[sessionId][messageId]` is the
   * `SystemBotRecord` itself (not a wrapper), so existing consumers can
   * reach the record with a single lookup. The matching accumulator
   * state is held in the parallel `accBySession` map (private).
   */
  liveBySession: Record<string, Record<string, SystemBotRecord>>;
}

/**
 * Per-record accumulator state. Held in a parallel map (`accBySession`)
 * so the public `liveBySession` shape is unchanged. The keys mirror
 * `liveBySession` exactly; both are populated / removed together by
 * `getOrCreateLive` and the finalize paths.
 *
 * `pendingToolCalls` mirrors the primary
 * `BotMessageAccumulator.pending_tool_calls`: keyed by tool call id, value
 * is the partial dict populated as the matching `tool_call` and (later)
 * `tool_call_result` events arrive. We flush the bucket to a single
 * `tool_call` part at finalize (so the persisted shape matches the
 * primary path).
 *
 * `filteredToolCallIds` is the live equivalent of
 * `BotMessageAccumulator._filtered_tool_call_ids` — set of call ids whose
 * matching `tool_call` was an `ask_user_choice` (and thus must NOT be
 * re-rendered as a tool_call part). Without this, the result-only fallback
 * in `mergeToolCallResult` would synthesise a name-less part next to the
 * InteractiveChoiceBox (Bug from
 * docs/superpowers/plans/2026-07-05-interactive-choice-history-roundtrip.md
 * §2.4 Amendment F).
 */
interface SystemAccState {
  /**
   * Map from tool_call id → index in `record.content.message` of the
   * `tool_call` part that holds it. Lets `mergeToolCallResult` find the
   * matching part in O(1) and update it in place rather than appending a
   * duplicate. The indices stay valid because we only ever `push` to the
   * message array (never splice) within one in-flight turn.
   */
  toolCallPartIndex: Map<string, number>;
  /** See module header for the ask_user_choice filter rationale. */
  filteredToolCallIds: Set<string>;
}

export function createSystemStreamState(): SystemStreamState {
  return { liveBySession: {} };
}

// Parallel map: same shape as `liveBySession`, but each key resolves to the
// accumulator state (private). Kept on the same `SystemStreamState` object
// so `finalizeSystemSession` can clear both at once. We don't expose it on
// the public type to discourage downstream reads — the record + its parts
// are the only stable surface.
type AccMap = Record<string, Record<string, SystemAccState>>;
function accOf(state: SystemStreamState): AccMap {
  return (
    (state as unknown as { _accBySession?: AccMap })._accBySession ||
    ((state as unknown as { _accBySession: AccMap })._accBySession = {})
  );
}

function makeRecord(messageId: string): SystemBotRecord {
  return {
    id: `system-${messageId}`,
    created_at: new Date().toISOString(),
    content: { type: "bot", message: [], reasoning: "", isLoading: true },
  };
}

function makeAccState(): SystemAccState {
  return {
    toolCallPartIndex: new Map(),
    filteredToolCallIds: new Set<string>(),
  };
}

/** Pair of (record, accumulator state) for one in-flight turn. */
type LiveEntry = { record: SystemBotRecord; state: SystemAccState };

function getOrCreateLive(
  state: SystemStreamState,
  sessionId: string,
  messageId: string,
): LiveEntry {
  const records = (state.liveBySession[sessionId] =
    state.liveBySession[sessionId] || {});
  const accs = (accOf(state)[sessionId] = accOf(state)[sessionId] || {});
  let record = records[messageId];
  if (!record) {
    record = makeRecord(messageId);
    records[messageId] = record;
    accs[messageId] = makeAccState();
  }
  // The state is paired with the record by construction; if a peer caller
  // ever mutates `liveBySession` directly we want a defensive pairing
  // rather than a silent miss.
  return { record, state: accs[messageId] };
}

function syncReasoningField(entry: LiveEntry): void {
  // Mirror the primary path: content.reasoning is the joined text of all
  // `think` parts, so the side bar can read it without re-walking parts.
  const joined = entry.record.content.message
    .filter((p) => p.type === "think")
    .map((p) => String(p.think || ""))
    .join("");
  entry.record.content.reasoning = joined;
}

function safeJsonParse(value: unknown): unknown {
  if (value == null) return null;
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function pushPlainPart(
  record: SystemBotRecord,
  text: string,
  streaming: boolean,
): void {
  if (!text) return;
  if (streaming) {
    // Streaming chunks are deltas: append into the trailing plain part,
    // matching the original leaf behaviour so live updates are visible
    // without flashing a new bubble per chunk.
    let last = record.content.message[record.content.message.length - 1];
    if (!last || last.type !== "plain") {
      last = { type: "plain", text: "" };
      record.content.message.push(last);
    }
    last.text = `${String(last.text || "")}${text}`;
    return;
  }
  record.content.message.push({ type: "plain", text });
}

function pushReasoningPart(entry: LiveEntry, text: string): void {
  if (!text) return;
  const { record } = entry;
  const last = record.content.message[record.content.message.length - 1];
  if (last && last.type === "think") {
    last.think = `${String(last.think || "")}${text}`;
  } else {
    record.content.message.push({ type: "think", think: text });
  }
  syncReasoningField(entry);
}

function pushOrMergeToolCall(entry: LiveEntry, parsed: unknown): void {
  if (!parsed || typeof parsed !== "object") return;
  const tc = parsed as Record<string, unknown>;
  const id = tc.id;
  if (id == null) return;
  const key = String(id);
  const { record, state } = entry;
  // Author: elecvoid243, 2026-07-05
  // Plan docs/superpowers/plans/2026-07-05-interactive-choice-history-roundtrip.md
  // §2.4 Amendment F — drop ask_user_choice tool_calls from the live render
  // and remember the id so the matching result is dropped too.
  if (isAskUserChoiceToolCall(tc)) {
    state.filteredToolCallIds.add(key);
    return;
  }
  // Eager push: a new tool_call id gets its own `tool_call` part right
  // away so the UI can render the tool card without waiting for the
  // `complete` event. A repeat id (rare; some LLM providers re-emit the
  // same tool_call with updated args) merges into the existing part.
  const existingIdx = state.toolCallPartIndex.get(key);
  if (existingIdx != null) {
    const part = record.content.message[existingIdx];
    if (part && part.type === "tool_call" && Array.isArray(part.tool_calls)) {
      const existing = part.tool_calls.find(
        (item) => String((item as Record<string, unknown>).id ?? "") === key,
      );
      if (existing) {
        Object.assign(existing as Record<string, unknown>, tc);
        return;
      }
    }
  }
  record.content.message.push({
    type: "tool_call",
    tool_calls: [{ ...tc }],
  });
  state.toolCallPartIndex.set(key, record.content.message.length - 1);
}

function mergeToolCallResult(entry: LiveEntry, parsed: unknown): void {
  if (!parsed || typeof parsed !== "object") return;
  const result = parsed as Record<string, unknown>;
  const id = result.id;
  if (id == null) return;
  const key = String(id);
  const { record, state } = entry;
  // Mirror primary path: drop the result if the original tool_call was an
  // ask_user_choice (already filtered live) or if a tool_call part with
  // the ask_user_choice name was already committed (defensive second
  // check, see isAskUserChoiceToolCallResult).
  const recordLike: RecordLike = {
    content: { message: record.content.message },
  };
  if (
    isAskUserChoiceToolCallResult(recordLike, result, state.filteredToolCallIds)
  ) {
    return;
  }
  const existingIdx = state.toolCallPartIndex.get(key);
  if (existingIdx != null) {
    const part = record.content.message[existingIdx];
    if (part && part.type === "tool_call" && Array.isArray(part.tool_calls)) {
      const existing = part.tool_calls.find(
        (item) => String((item as Record<string, unknown>).id ?? "") === key,
      );
      if (existing) {
        (existing as Record<string, unknown>).result = result.result;
        (existing as Record<string, unknown>).finished_ts =
          typeof result.ts === "number" ? result.ts : Date.now() / 1000;
        return;
      }
    }
  }
  // No matching tool_call yet: synthesise a result-only part. Matches
  // the primary `BotMessageAccumulator._store_tool_call_result` fallback
  // so the rendered shape stays consistent.
  record.content.message.push({
    type: "tool_call",
    tool_calls: [
      {
        id,
        result: result.result,
        finished_ts:
          typeof result.ts === "number" ? result.ts : Date.now() / 1000,
      },
    ],
  });
  state.toolCallPartIndex.set(key, record.content.message.length - 1);
}

function setAgentStats(entry: LiveEntry, data: unknown): void {
  // Defensive: agent_stats on the wire is a JSON string of the
  // `{token_usage, ...}` dict. Try to parse so downstream code that
  // expects an object (mirroring history reload via `agentStats`) does
  // not have to. If parsing fails, fall back to the raw value — better
  // than dropping the metric.
  const parsed = safeJsonParse(data);
  entry.record.content.agentStats = parsed === null ? data : parsed;
}

function pushInteractiveChoicePart(
  entry: LiveEntry,
  rawData: unknown,
): boolean {
  const inner = safeJsonParse(rawData);
  if (!inner || typeof inner !== "object") return false;
  // interactiveChoicePartFromSsePayload expects the v1.0 envelope
  // `{type: "interactive_choice", data: {...}}` (see parseInteractiveChoice
  // header). Our wire format is the v1.1 chain_type variant
  // (`type: "plain"` + `data: <json string>`) where the JSON object is
  // already the envelope — rewrap so the parser sees the shape it knows.
  const part = interactiveChoicePartFromSsePayload({
    type: "interactive_choice",
    data: inner,
  });
  if (!part) return false;
  entry.record.content.message.push(part as unknown as SystemMessagePart);
  entry.record.content.isLoading = false;
  return true;
}

function dispatchPlainPayload(
  entry: LiveEntry,
  chainType: unknown,
  data: unknown,
  streaming: boolean,
): void {
  // Mirror the primary `processStreamPayload` dispatch in useMessages.ts so
  // goal-loop turns render the same way as normal LLM runs:
  //   - reasoning          → think part
  //   - tool_call          → tool_call part (id-keyed upsert)
  //   - tool_call_result   → finishes the matching tool_call
  //   - interactive_choice → InteractiveChoicePart
  //   - agent_stats        → record.content.agentStats (no part)
  //   - everything else    → plain text
  switch (chainType) {
    case "reasoning":
      pushReasoningPart(entry, typeof data === "string" ? data : "");
      return;
    case "tool_call":
      pushOrMergeToolCall(entry, safeJsonParse(data));
      return;
    case "tool_call_result":
      mergeToolCallResult(entry, safeJsonParse(data));
      return;
    case "interactive_choice":
      pushInteractiveChoicePart(entry, data);
      return;
    case "agent_stats":
      setAgentStats(entry, data);
      return;
    default: {
      const text = typeof data === "string" ? data : "";
      pushPlainPart(entry.record, text, streaming);
    }
  }
}

/**
 * Apply one system stream payload to the session record list.
 *
 * Args:
 *   state: Shared live-turn tracking created by createSystemStreamState().
 *   sessionId: Conversation the payload belongs to.
 *   records: The session's mutable record list (messagesBySession entry).
 *   payload: Raw mirrored webchat payload.
 *
 * Returns:
 *   True when the payload was consumed (caller may notify stream update).
 */
export function processSystemPayload(
  state: SystemStreamState,
  sessionId: string,
  records: SystemBotRecord[],
  payload: any,
): boolean {
  const type = payload?.type;
  // Accept the same `type` set the system stream server forwards
  // (`plain` / `complete` / `end`), the `run_snapshot` catch-up event
  // emitted on (re)subscribe for in-flight orphan turns, and the
  // agent_stats top-level event the primary consumer republishes.
  // Anything else is not ours.
  if (
    type !== "plain" &&
    type !== "complete" &&
    type !== "end" &&
    type !== "agent_stats" &&
    type !== "assistant_file_comments" &&
    type !== "run_snapshot"
  ) {
    return false;
  }
  const messageId = payload?.message_id;
  if (messageId == null || messageId === "") return false;
  const key = String(messageId);

  // 2026-08-27 switch-back catch-up: `run_snapshot` seeds a live record
  // with the server-side accumulated state of an in-flight orphan turn,
  // so re-subscribing to a session restores everything streamed while
  // another session was open. If a record for this turn already exists
  // (frozen by `finalizeSystemSession` on session switch), reattach and
  // update it in place instead of pushing a duplicate bubble.
  if (type === "run_snapshot") {
    const snapshotParts = Array.isArray(payload?.data?.message)
      ? payload.data.message
      : [];
    const existingId = `system-${key}`;
    const existingIdx = records.findIndex((r) => r.id === existingId);
    let entry: LiveEntry;
    if (existingIdx >= 0) {
      // Reattach: reuse the frozen bubble both in the render list and in
      // the live-tracking map, so subsequent payloads keep mutating the
      // same record instead of an invisible fresh one.
      const reused = records[existingIdx];
      state.liveBySession[sessionId] = state.liveBySession[sessionId] || {};
      state.liveBySession[sessionId][key] = reused;
      const accs = accOf(state)[sessionId] || {};
      accs[key] = makeAccState();
      accOf(state)[sessionId] = accs;
      entry = { record: reused, state: accs[key] };
    } else {
      entry = getOrCreateLive(state, sessionId, key);
      if (!records.includes(entry.record)) {
        records.push(entry.record);
      }
    }
    entry.record.content.message = snapshotParts.map(
      (part: SystemMessagePart) => ({ ...part }),
    );
    syncReasoningField(entry);
    entry.record.content.isLoading = entry.record.content.message.length === 0;
    return true;
  }

  const entry = getOrCreateLive(state, sessionId, key);
  if (!records.includes(entry.record)) {
    records.push(entry.record);
  }

  const data = payload?.data;
  const streaming = Boolean(payload?.streaming);
  const chainType = payload?.chain_type;

  if (type === "assistant_file_comments") {
    if (data?.type === type && Array.isArray(data.comments)) {
      entry.record.content.message.push(data);
      entry.record.content.isLoading = false;
    }
    return true;
  }

  if (type === "agent_stats") {
    // Primary republishes agent_stats as `{type: "agent_stats", data: {...}}`
    // (no chain_type). Mirror `messageContent(botRecord).agentStats = data`
    // in the primary consumer.
    setAgentStats(entry, data);
    return true;
  }

  if (type === "plain") {
    // Mirror the primary path's `markMessageStarted(botRecord)`: the
    // first content-bearing payload ends the "加载中" placeholder so
    // streamed chunks render live. Previously isLoading was only
    // cleared on complete/end, which kept the whole goal-loop turn
    // hidden behind the loading div until the final flush.
    entry.record.content.isLoading = false;
    dispatchPlainPayload(entry, chainType, data, streaming);
    return true;
  }

  // complete / end: dispatch the final data through the chain_type
  // dispatcher for non-plain chain types (so a `complete` with
  // chain_type="reasoning" merges into the think part rather than
  // dumping raw text), then flush pending tool-call state and finalize.
  //
  // For plain / no-chain_type data, replicate the original late-subscriber
  // dedup: only push a plain part when the record is still empty. The
  // server already streams the trailing text, so a separate `complete`
  // payload with the same text would just duplicate it.
  if (type === "complete" && data) {
    if (
      chainType === "reasoning" ||
      chainType === "tool_call" ||
      chainType === "tool_call_result" ||
      chainType === "interactive_choice" ||
      chainType === "agent_stats"
    ) {
      dispatchPlainPayload(entry, chainType, data, streaming);
    } else if (entry.record.content.message.length === 0) {
      const text = typeof data === "string" ? data : "";
      if (text) {
        entry.record.content.message.push({ type: "plain", text });
      }
    }
  }
  syncReasoningField(entry);
  if (typeof payload?.reasoning === "string" && payload.reasoning) {
    // 2026-07-25 fix: webchat's `send_streaming` always carries the
    // full aggregated reasoning on the final `complete` payload. Mirror
    // it both into the `reasoning` field (kept for the side bar) and
    // into a fresh `think` part when the per-chain reasoning payloads
    // never produced one — otherwise the goal-loop turn has no thinking
    // block to render and the ReasoningTimeline stays closed.
    entry.record.content.reasoning = payload.reasoning;
    const hasThink = entry.record.content.message.some(
      (part) => part.type === "think",
    );
    if (!hasThink) {
      entry.record.content.message.unshift({
        type: "think",
        think: payload.reasoning,
      });
    }
  }
  entry.record.content.isLoading = false;
  delete state.liveBySession[sessionId][key];
  delete accOf(state)[sessionId]?.[key];
  return true;
}

/**
 * Finalize all live turns of a session (stream aborted or session switched).
 */
export function finalizeSystemSession(
  state: SystemStreamState,
  sessionId: string,
): void {
  const live = state.liveBySession[sessionId];
  if (!live) return;
  const accs = accOf(state)[sessionId] || {};
  for (const [messageId, record] of Object.entries(live)) {
    const acc = accs[messageId] || makeAccState();
    syncReasoningField({ record, state: acc });
    record.content.isLoading = false;
  }
  delete state.liveBySession[sessionId];
  delete accOf(state)[sessionId];
}
