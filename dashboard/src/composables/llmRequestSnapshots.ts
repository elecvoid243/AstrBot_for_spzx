// Author: impl_task9
// Date: 2026-11-15
//
// Leaf parsing/merging for developer-mode LLM request snapshots. The live
// `llm_request_snapshots` event and the REST list route share the
// `{items: [...]}` envelope, while the detail route returns the full
// envelope (including `injections`) plus the sanitized raw payload.
// Telemetry is live-only: this module is deliberately free of history
// normalization and persistence.

import type {
  LlmSnapshotDetail as ApiLlmSnapshotDetail,
  LlmSnapshotEnvelope as ApiLlmSnapshotEnvelope,
  LlmSnapshotList,
} from "@/api/v1";
import type { LlmRequestInjectionItem } from "./llmRequestInjections";

/**
 * List/detail envelope. Mirrors the generated API type; `injections` is
 * optional because list items are deliberately lightweight while the
 * detail envelope carries them.
 */
export interface LlmRequestSnapshotEnvelope extends ApiLlmSnapshotEnvelope {
  injections?: LlmRequestInjectionItem[];
}

/** Detail response: envelope + sanitized provider body + derived view. */
export type LlmSnapshotDetail = Omit<ApiLlmSnapshotDetail, "envelope"> & {
  envelope: LlmRequestSnapshotEnvelope;
};

/** Recorder retention limits, as returned by the list route. */
export type LlmSnapshotLimits = LlmSnapshotList["limits"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * Parse a snapshot payload into envelopes.
 *
 * Accepts either the `{items: [...]}` list envelope or a bare array (the
 * live event passes the decoded object; some transports pass the JSON
 * string). Entries without a string `snapshot_id` are dropped — a bogus
 * envelope is worse than no envelope.
 */
export function parseLlmRequestSnapshots(
  payload: unknown,
): LlmRequestSnapshotEnvelope[] {
  let candidate: unknown = payload;
  if (typeof candidate === "string") {
    try {
      candidate = JSON.parse(candidate);
    } catch {
      return [];
    }
  }
  const rawItems = Array.isArray(candidate)
    ? candidate
    : isRecord(candidate) && Array.isArray(candidate.items)
      ? candidate.items
      : [];
  return rawItems.filter(
    (item): item is LlmRequestSnapshotEnvelope =>
      isRecord(item) && typeof item.snapshot_id === "string",
  );
}

/**
 * Merge incoming envelopes into the current list by `snapshot_id`.
 *
 * Incoming wins on duplicates and first-seen order is preserved: existing
 * entries keep their slot, new ids append in incoming order. Neither input
 * array is mutated.
 */
export function mergeLlmRequestSnapshots(
  current: LlmRequestSnapshotEnvelope[] = [],
  incoming: LlmRequestSnapshotEnvelope[] = [],
): LlmRequestSnapshotEnvelope[] {
  const merged = current.slice();
  const indexById = new Map<string, number>();
  merged.forEach((item, index) => {
    if (!indexById.has(item.snapshot_id)) {
      indexById.set(item.snapshot_id, index);
    }
  });

  for (const item of incoming) {
    const existingIndex = indexById.get(item.snapshot_id);
    if (existingIndex === undefined) {
      indexById.set(item.snapshot_id, merged.length);
      merged.push(item);
    } else {
      merged[existingIndex] = item;
    }
  }
  return merged;
}
