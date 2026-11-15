// Author: impl_task9
// Date: 2026-11-15
//
// Developer-mode snapshot list/detail state. Chat.vue owns the developer
// mode toggle and its permission (`enabled`/`canEdit`); this composable
// only reads what the recorder exposes for the current session. Failures
// land on `error` instead of throwing into the component.

import { isRef, ref, type Ref } from "vue";
import { llmSnapshotsApi } from "@/api/v1";
import {
  parseLlmRequestSnapshots,
  type LlmRequestSnapshotEnvelope,
  type LlmSnapshotDetail,
  type LlmSnapshotLimits,
} from "./llmRequestSnapshots";

/** The chat session whose snapshots this instance reads. */
export type LlmSnapshotSessionSource =
  | string
  | null
  | undefined
  | Ref<string | null | undefined>
  | (() => string | null | undefined);

export interface LlmSnapshotListParams {
  messageId?: number;
  turnId?: string;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  const responseMessage = (
    error as { response?: { data?: { message?: string } } } | null
  )?.response?.data?.message;
  return responseMessage || String(error ?? "unknown error");
}

export function useLlmSnapshots(sessionId?: LlmSnapshotSessionSource) {
  const items = ref<LlmRequestSnapshotEnvelope[]>([]);
  const expired = ref(false);
  const limits = ref<LlmSnapshotLimits | null>(null);
  const error = ref<string | null>(null);
  const detailCache = new Map<string, LlmSnapshotDetail>();
  let lastParams: LlmSnapshotListParams = {};
  // Session the current list state and detail cache belong to. Reset on every
  // switch so a detail cached for one session is never returned for another,
  // and a slow list response cannot overwrite the session we moved to.
  let boundSessionId: string | null = null;

  function resolveSessionId(): string {
    const value =
      typeof sessionId === "function"
        ? sessionId()
        : isRef(sessionId)
          ? sessionId.value
          : sessionId;
    return typeof value === "string" ? value : "";
  }

  function bindSession(session: string): void {
    if (boundSessionId === session) return;
    boundSessionId = session;
    detailCache.clear();
  }

  async function loadList(params: LlmSnapshotListParams = {}): Promise<void> {
    lastParams = { ...params };
    // Resolve once: the request and the staleness check must use the same id.
    const requestSessionId = resolveSessionId();
    bindSession(requestSessionId);
    items.value = [];
    expired.value = false;
    limits.value = null;
    error.value = null;
    if (!requestSessionId) return;
    try {
      const response = await llmSnapshotsApi.list(requestSessionId, {
        message_id: params.messageId,
        turn_id: params.turnId,
      });
      if (resolveSessionId() !== requestSessionId) return;
      const list = response.data?.data;
      items.value = parseLlmRequestSnapshots(list);
      expired.value = Boolean(list?.expired);
      limits.value = list?.limits ?? null;
    } catch (e) {
      if (resolveSessionId() !== requestSessionId) return;
      error.value = errorMessage(e);
    }
  }

  async function loadDetail(
    snapshotId: string,
    options: { force?: boolean } = {},
  ): Promise<LlmSnapshotDetail | null> {
    const requestSessionId = resolveSessionId();
    bindSession(requestSessionId);
    // A forced read (detail-header refresh) must not answer from the cache,
    // otherwise the button would look inert.
    if (options.force) detailCache.delete(snapshotId);
    const cached = detailCache.get(snapshotId);
    if (cached) {
      error.value = null;
      return cached;
    }
    error.value = null;
    try {
      const response = await llmSnapshotsApi.get(snapshotId);
      // A detail that lands after a session switch must not enter the new
      // session's cache (or the Inspector would render it for the wrong chat).
      if (resolveSessionId() !== requestSessionId) return null;
      const detail = (response.data?.data ?? null) as LlmSnapshotDetail | null;
      if (!detail) return null;
      detailCache.set(snapshotId, detail);
      return detail;
    } catch (e) {
      if (resolveSessionId() !== requestSessionId) return null;
      error.value = errorMessage(e);
      return null;
    }
  }

  /** Re-read the list with the filters from the last `loadList` call. */
  async function refresh(): Promise<void> {
    await loadList(lastParams);
  }

  return {
    items,
    expired,
    limits,
    error,
    loadList,
    loadDetail,
    refresh,
  };
}
