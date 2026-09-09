import { computed, onBeforeUnmount, reactive, ref, watch, type Ref } from "vue";
import { chatApi, fileApi } from "@/api/v1";
import { fetchWithAuth } from "@/api/http";
import { collectFileChanges } from "@/utils/fileChangeTool";
import { useInteractiveChoiceStore } from "@/stores/interactiveChoice";
import {
  createSystemStreamState,
  finalizeSystemSession,
  processSystemPayload,
} from "./systemStream";
import {
  isInteractiveChoicePayload,
  validateInteractiveChoice,
  truncateInteractiveChoice,
} from "./parseInteractiveChoice";
import {
  applyInteractiveChoiceSse,
  applyInteractiveChoiceResolved,
} from "./dispatchInteractiveChoice";
import { abandonPendingInteractiveChoices } from "./abandonPendingInteractiveChoices";
// Author: elecvoid243
// Date: 2026-07-05
// Plan: docs/superpowers/plans/2026-07-05-interactive-choice-history-roundtrip.md
// §2.4 Amendment F — keep the live SSE handler and the history
// reload path using the same predicates. The leaf module
// `askUserChoiceToolFilter.ts` has no `@/api` dependency so it is
// testable from a node-only test runner.
import {
  isAskUserChoiceToolCall,
  isAskUserChoiceToolCallResult,
} from "./askUserChoiceToolFilter";
// Author: elecvoid243
// Date: 2026-07-05
// Plan: docs/superpowers/plans/2026-07-05-interactive-choice-history-roundtrip.md
// §2.4 Amendment F — `normalizePartsInternal` was extracted to a
// leaf module so the ask_user_choice history-reload filter can be
// unit-tested without pulling in `@/api`.
import { normalizeMessageParts as normalizeMessagePartsFromLeaf } from "./normalizeMessageParts";
// Live reducer for structured subagent progress stream payloads.
import { applySubAgentEvent } from "./subagentRunReducer";
// spcode todo_* tool protocol: shared tool-name set + the history
// replay fold that rebuilds the todo snapshot on refresh / session
// switch (leaf module — see todoHistoryReplay.ts header).
import {
  TODO_TOOL_NAMES,
  replayTodoSnapshotFromHistory,
} from "./todoHistoryReplay";

export type TransportMode = "sse" | "websocket";

// History windowing: opening a session loads only the most recent messages;
// older pages are fetched on demand with a `before_id` cursor (ChatUI
// scroll-to-top or the "load older" button).
const HISTORY_PAGE_SIZE = 50;

/** Per-session history pagination state (window + exclusive cursor). */
type HistoryPaging = {
  hasMore: boolean;
  oldestLoadedId: number | null;
  totalMessages: number;
  loadingOlder: boolean;
};

/** One user message in the session-wide marker index (ChatUI strip). */
type SessionMarker = {
  id: number;
  index: number;
  snippet: string;
};

/** Session message-marker index, loaded lazily alongside the history. */
type SessionMarkers = {
  markers: SessionMarker[];
  totalMessages: number;
  truncated: boolean;
  loaded: boolean;
  loading: boolean;
};

/**
 * Per-message "thinking effort" value sent with each chat request. "auto"
 * keeps the provider-level static config; "off" disables thinking where the
 * provider supports it. Any other string is passed through to OpenAI-family
 * `reasoning_effort` verbatim (values differ per model / engine, e.g. "max"
 * on deepseek-v4 official API, "xhigh" on llama.cpp-hosted Qwen).
 */
export type ThinkingEffort = "auto" | "off" | (string & {});

export function buildChatRequestFlags(enableStreaming = true) {
  return {
    enable_inline_genui: true,
    enable_default_system_prompt: true,
    enable_streaming: enableStreaming,
  };
}

export interface MessagePart {
  type: string;
  text?: string;
  think?: string;
  message_id?: string | number;
  selected_text?: string;
  embedded_url?: string;
  embedded_file?: { url?: string; filename?: string; attachment_id?: string };
  attachment_id?: string;
  filename?: string;
  stored_filename?: string;
  tool_calls?: ToolCall[];
  [key: string]: unknown;
}

export interface ToolCall {
  id?: string;
  name?: string;
  arguments?: unknown;
  result?: unknown;
  ts?: number;
  finished_ts?: number;
  [key: string]: unknown;
}

/**
 * Todo 工具返回的标准化快照结构。
 *
 * 数据源:spcode 插件 4 个 todo 工具的 `call()` 返回值(经 `unwrap()` 包装成
 * `{"ok": true, "data": {list, stats, attention_items}}`)。前端在 SSE
 * `tool_call_result` 阶段从 tool.result 字符串中解析出来,按 sessionId 缓存,
 * 供 Chat.vue 的 todo summary bar 悬浮菜单实时消费。
 *
 * - `list.items[].attention` 由后端在 `_build_list_state` 时注入,标识需要
 *   关注的 item(stuck/blocked:`in_progress` 且 `notes` 非空)。
 * - `attentionItems` 是后端的 attention id 数组(便于 summary bar 展示徽标)。
 */
export type TodoItemStatus = "pending" | "in_progress" | "done" | "cancelled";

export interface TodoItem {
  id: number;
  title: string;
  status: TodoItemStatus;
  notes?: string;
  attention?: boolean;
}

export interface TodoList {
  title: string;
  items: TodoItem[];
  created_at?: string;
  updated_at?: string;
  sender_key?: string;
  platform?: string;
  sender_id?: string;
  [key: string]: unknown;
}

export interface TodoStats {
  total: number;
  done: number;
  in_progress: number;
  pending: number;
  cancelled: number;
  effective_total: number;
  progress_pct: number;
  blocked_count?: number;
}

export interface TodoSnapshot {
  list: TodoList;
  stats: TodoStats;
  attentionItems: number[];
  /** 写入时间戳,便于在多次事件中排序/去抖。 */
  updatedAt: number;
}

export interface ChatContent {
  type: "user" | "bot" | string;
  message: MessagePart[];
  reasoning?: string;
  isLoading?: boolean;
  agentStats?: any;
  refs?: any;
}

export interface MessageDisplayBlock {
  kind: "thinking" | "content";
  parts: MessagePart[];
}

export interface ChatRecord {
  id?: string | number;
  content: ChatContent;
  created_at?: string;
  sender_id?: string;
  sender_name?: string;
  llm_checkpoint_id?: string | null;
  threads?: ChatThread[];
}

export interface ChatThread {
  thread_id: string;
  parent_session_id: string;
  parent_message_id: number;
  base_checkpoint_id: string;
  selected_text: string;
  created_at?: string;
  updated_at?: string;
}

export interface ChatSessionProject {
  project_id: string;
  title: string;
  emoji?: string;
}

interface ActiveChatRun {
  run_id: string;
  session_id: string;
  llm_checkpoint_id?: string | null;
  status?: string;
  revision?: number;
  content?: ChatContent;
}

interface ActiveConnection {
  sessionId: string;
  messageId: string;
  runId?: string;
  transport: TransportMode;
  abort?: AbortController;
  ws?: WebSocket;
  botRecord?: ChatRecord;
  userRecord?: ChatRecord;
  completed?: boolean;
  errorShown?: boolean;
  botVisible?: boolean;
  deferredBeforeBot?: ChatRecord;
  followUpCaptured?: boolean;
  followUpTargetRunId?: string;
}

interface SendMessageStreamOptions {
  sessionId: string;
  messageId: string;
  parts: MessagePart[];
  transport: TransportMode;
  enableStreaming?: boolean;
  selectedProvider?: string;
  selectedModel?: string;
  thinkingEffort?: ThinkingEffort;
  userRecord?: ChatRecord;
  botRecord: ChatRecord;
  skipUserHistory?: boolean;
  llmCheckpointId?: string | null;
}

interface ContinueEditedMessageOptions {
  sessionId: string;
  sourceRecord: ChatRecord;
  enableStreaming?: boolean;
  selectedProvider?: string;
  selectedModel?: string;
  thinkingEffort?: ThinkingEffort;
}

interface CreateLocalExchangeOptions {
  sessionId: string;
  messageId: string;
  parts: MessagePart[];
}

interface UseMessagesOptions {
  currentSessionId: Ref<string>;
  onSessionsChanged?: () => Promise<void> | void;
  onStreamUpdate?: (sessionId: string) => void;
  /**
   * Fired exactly once when a streaming response for a given session
   * ends — either because the server closed the stream normally, an
   * error terminated it, the user pressed stop, or the regenerate
   * path completed. Fires for both SSE and WebSocket transports.
   *
   * This is the canonical "bot just finished talking" signal. It is
   * the right hook for refetching authoritative state that the bot's
   * response may have mutated (for example the spcode project
   * status). Distinct from :attr:`onSessionsChanged` which also
   * fires for session-list mutations unrelated to any specific
   * stream; ``onStreamEnd`` is a pure stream-lifecycle event.
   *
   * Args:
   *   sessionId: The session whose stream just ended.
   */
  onStreamEnd?: (sessionId: string) => void;
  /**
   * Fired when a new `ask_user_choice` prompt is pushed into a session
   * via the live stream. Lets the host (ChatUI) raise an attention
   * signal (sidebar highlight, title flash, notification) only for
   * genuinely new choices — the dispatcher returns `true` only when a
   * valid part was parsed.
   *
   * Args:
   *   sessionId: The bare conversation id (`session.session_id`) that
   *     just received the choice.
   */
  onInteractiveChoice?: (sessionId: string) => void;
  /**
   * Fired each time the active run's stream reports a real tool call
   * (`ask_user_choice` prompts excluded). Marks a tool-call boundary for
   * the pending follow-up queue: messages flushed here are captured by
   * the backend and injected into this tool's result, mirroring the
   * send-while-running timing before the queue existed.
   *
   * Args:
   *   sessionId: The session whose active run just called a tool.
   */
  onToolCallActivity?: (sessionId: string) => void;
}

export function useMessages(options: UseMessagesOptions) {
  const loadingMessages = ref(false);
  const sending = ref(false);
  const messagesBySession = reactive<Record<string, ChatRecord[]>>({});
  const loadedSessions = reactive<Record<string, boolean>>({});
  const activeConnections = reactive<Record<string, ActiveConnection>>({});
  const chatWebSockets: Record<string, WebSocket> = {};
  const closingChatWebSockets = new WeakSet<WebSocket>();
  const deferredBotAnchors = new WeakMap<ChatRecord, ChatRecord>();
  const attachmentBlobCache = new Map<string, Promise<string>>();
  const sessionProjects = reactive<Record<string, ChatSessionProject | null>>(
    {},
  );
  // 2026-08-13: archived flag per loaded session, from the getSession
  // payload. The ChatUI renders archived sessions in read-only mode.
  const sessionArchivedFlags = reactive<Record<string, boolean>>({});
  // 2026-09-07: history windowing state. `historyOffsetBySession` is the
  // absolute index (within the session's full history) of the first loaded
  // record, keeping `data-message-index` aligned with the search endpoint.
  const historyPagingBySession = reactive<Record<string, HistoryPaging>>({});
  const historyOffsetBySession = reactive<Record<string, number>>({});
  // 2026-09-07: session-wide user-message marker index for the scroll strip
  // (fetched fire-and-forget on session load; loaded=false keeps the DOM
  // measurement fallback in Chat.vue).
  const sessionMarkersBySession = reactive<Record<string, SessionMarkers>>({});
  // System event stream (goal-loop orphan turns): one long-lived SSE per
  // active session, feeding live records through the systemStream leaf.
  // Wrap with `reactive` so that (1) record mutations made via the raw
  // `entry.record` reference inside the leaf go through the proxy and
  // trigger renders (otherwise `last.text = ...` bypasses the proxy
  // because the record is also stored in the live-tracking map, and Vue
  // reactivity is only notified via proxy traps), and (2) `liveBySession`
  // key changes drive `hasLiveSystemRecord` so the ChatMessageList can
  // mark the in-flight system record as streaming (engaging the
  // markstream incremental/ typewriter path instead of `final=true`).
  const systemStreamState = reactive(createSystemStreamState());
  const systemConnections: Record<string, AbortController> = {};

  const activeMessages = computed(() =>
    options.currentSessionId.value
      ? messagesBySession[options.currentSessionId.value] || []
      : [],
  );

  onBeforeUnmount(() => {
    cleanupConnections();
    for (const promise of attachmentBlobCache.values()) {
      promise.then((url) => URL.revokeObjectURL(url)).catch(() => {});
    }
    attachmentBlobCache.clear();
  });

  // The system stream follows the currently open conversation.
  watch(
    () => options.currentSessionId.value,
    (sessionId, previousSessionId) => {
      if (previousSessionId && previousSessionId !== sessionId) {
        stopSystemStream(previousSessionId);
      }
      if (sessionId) {
        startSystemStream(sessionId);
      }
    },
    { immediate: true },
  );

  function startSystemStream(sessionId: string) {
    if (!sessionId || systemConnections[sessionId]) return;
    const abort = new AbortController();
    systemConnections[sessionId] = abort;

    void (async () => {
      for (
        let attempt = 0;
        attempt < 5 && !abort.signal.aborted;
        attempt += 1
      ) {
        try {
          const response = await fetchWithAuth(
            chatApi.systemStreamUrl(sessionId),
            {
              headers: { Accept: "text/event-stream" },
              signal: abort.signal,
            },
          );
          const contentType = response.headers.get("content-type") || "";
          if (
            !response.ok ||
            !response.body ||
            !contentType.includes("text/event-stream")
          ) {
            // Feature disabled or unavailable: stay silent, no retry.
            return;
          }
          await readSseStream(response.body, (payload) => {
            // 2026-08-27: first-class run announcements — attach the page to
            // the run's own stream (snapshot + live chunks through the exact
            // primary processing path) instead of rendering the turn through
            // the system leaf. Only synthetic-run registrations qualify: the
            // adapter's run_started mirror for user-initiated runs stays
            // filtered server-side, and goal-loop orphans keep rendering via
            // the system leaf below.
            if (
              payload?.type === "run_started" &&
              payload.synthetic &&
              payload.message_id != null
            ) {
              attachLiveRun(sessionId, String(payload.message_id));
              options.onStreamUpdate?.(sessionId);
              return;
            }
            messagesBySession[sessionId] = messagesBySession[sessionId] || [];
            const consumed = processSystemPayload(
              systemStreamState,
              sessionId,
              messagesBySession[sessionId],
              payload,
            );
            if (consumed) options.onStreamUpdate?.(sessionId);
          });
        } catch (error) {
          if (abort.signal.aborted) return;
          console.error("System event stream failed:", error);
        }
        if (abort.signal.aborted) return;
        // Server closed the stream: back off briefly, then reconnect.
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    })().finally(() => {
      if (systemConnections[sessionId] === abort) {
        delete systemConnections[sessionId];
      }
    });
  }

  function stopSystemStream(sessionId: string) {
    systemConnections[sessionId]?.abort();
    delete systemConnections[sessionId];
    finalizeSystemSession(systemStreamState, sessionId);
  }

  function isSessionRunning(sessionId: string) {
    return Object.values(activeConnections).some(
      (connection) => connection.sessionId === sessionId,
    );
  }

  function hasLiveSystemRecord(sessionId: string): boolean {
    // Drives `props.isStreaming` on the chat list so an in-flight goal-loop
    // (system-stream) turn is rendered with `final=false` and gets the
    // spinner, instead of being treated as a fully-finalised bubble. Reads
    // through the reactive `systemStreamState`, so the call site must be
    // inside a tracked context (computed / template binding).
    const bucket = systemStreamState.liveBySession[sessionId];
    return bucket ? Object.keys(bucket).length > 0 : false;
  }

  function isUserMessage(msg: ChatRecord) {
    return messageContent(msg).type === "user";
  }

  function messageContent(msg: ChatRecord): ChatContent {
    return msg.content || { type: "bot", message: [] };
  }

  function messageParts(msg: ChatRecord): MessagePart[] {
    const parts = messageContent(msg).message;
    if (Array.isArray(parts)) return parts;
    if (typeof parts === "string") return [{ type: "plain", text: parts }];
    return [];
  }

  function isMessageStreaming(msg: ChatRecord, _msgIndex: number) {
    const sessionId = options.currentSessionId.value;
    if (!sessionId || isUserMessage(msg)) return false;
    return Object.values(activeConnections).some(
      (connection) =>
        connection.sessionId === sessionId &&
        connection.botVisible !== false &&
        (connection.botRecord === msg ||
          (connection.botRecord?.id != null &&
            String(connection.botRecord.id) === String(msg.id))),
    );
  }

  async function resolvePartMedia(part: MessagePart): Promise<void> {
    if (part.embedded_url) return;
    let url: string;
    let cacheKey: string;
    const storedFilename =
      typeof part.stored_filename === "string" ? part.stored_filename : "";
    const lookupFilename = storedFilename || part.filename || "";
    if (part.attachment_id) {
      cacheKey = `att:${part.attachment_id}`;
      url = fileApi.contentUrl(part.attachment_id);
    } else if (lookupFilename) {
      cacheKey = `file:${lookupFilename}`;
      url = "";
    } else {
      return;
    }
    let promise = attachmentBlobCache.get(cacheKey);
    if (!promise) {
      if (!part.attachment_id && lookupFilename) {
        promise = fileApi
          .getByName(lookupFilename)
          .then((resp) => URL.createObjectURL(resp.data));
      } else {
        promise = fetchWithAuth(url).then(async (resp) => {
          if (!resp.ok) throw new Error(`Media request failed: ${resp.status}`);
          return URL.createObjectURL(await resp.blob());
        });
      }
      attachmentBlobCache.set(cacheKey, promise);
    }
    try {
      part.embedded_url = await promise;
    } catch (e) {
      attachmentBlobCache.delete(cacheKey);
      console.error("Failed to resolve media:", cacheKey, e);
    }
  }

  async function resolveRecordMedia(records: ChatRecord[]) {
    const mediaTypes = ["image", "record", "video"];
    const tasks: Promise<void>[] = [];
    for (const record of records) {
      for (const part of record.content?.message || []) {
        if (
          mediaTypes.includes(part.type) &&
          !part.embedded_url &&
          (part.attachment_id || part.stored_filename || part.filename)
        ) {
          tasks.push(resolvePartMedia(part));
        }
      }
    }
    await Promise.all(tasks);
  }

  async function loadSessionMessages(
    sessionId: string,
    resumeRuns = true,
    showLoading = true,
  ) {
    if (!sessionId) return;
    if (showLoading) loadingMessages.value = true;
    // Todo snapshot freshness cutoff: any snapshot written by a live
    // tool_call_result event AFTER this moment is fresher than the
    // history snapshot in flight and must not be overwritten by the
    // replay below.
    const todoReplayCutoff = Date.now();
    try {
      const response = await chatApi.getSession(sessionId);
      const payload = response.data?.data || {};
      const history = payload.history || [];
      const records: ChatRecord[] = history.map(normalizeHistoryRecord);
      attachThreads(records, payload.threads || []);
      await resolveRecordMedia(records);
      // History windowing: remember the window bounds so older pages can be
      // fetched with a before_id cursor, and so the rendered absolute index
      // (historyOffset + local index) stays aligned with the search endpoint.
      const oldestRecord = records.find((r) => Number.isFinite(Number(r.id)));
      const totalMessages = Number(payload.total_messages || 0);
      historyPagingBySession[sessionId] = {
        hasMore: Boolean(payload.has_more),
        oldestLoadedId: oldestRecord ? Number(oldestRecord.id) : null,
        totalMessages,
        loadingOlder: false,
      };
      historyOffsetBySession[sessionId] = Math.max(
        0,
        totalMessages - records.length,
      );
      // Marker index for the scroll strip — non-blocking, degrades to the
      // DOM-measured fallback on failure.
      void loadSessionMarkers(sessionId);
      // Live records (system stream / run resume) may have arrived while the
      // history snapshot was in flight; the snapshot then overwrote them.
      // Re-append anything not present in the snapshot so no message is lost.
      // System-stream records (`system-${run_id}`, goal-loop / collab turns)
      // are special: an in-flight orphan turn is not in the snapshot (it is
      // persisted only on completion) and is re-seeded by the freshly
      // re-subscribed system stream's run_snapshot — keep it. A finished one
      // is superseded by the persisted record now in the snapshot — drop the
      // frozen partial, otherwise both would render.
      const existing = messagesBySession[sessionId] || [];
      messagesBySession[sessionId] = records;
      if (existing.length) {
        const historyIds = new Set(
          records.map((r: ChatRecord) => String(r.id)),
        );
        const activeOrphanIds = new Set(
          (Array.isArray(payload.active_runs) ? payload.active_runs : [])
            .filter((r: ActiveChatRun) => !r.llm_checkpoint_id)
            .map((r: ActiveChatRun) => `system-${r.run_id}`),
        );
        const live = existing.filter((r: ChatRecord) => {
          const recordId = String(r.id || "");
          if (recordId.startsWith("system-")) {
            return activeOrphanIds.has(recordId);
          }
          return !historyIds.has(recordId);
        });
        if (live.length) {
          // 2026-09-01 (elecvoid243): no cross-format timestamp sorting here.
          // Live records necessarily postdate the persisted snapshot, so
          // appending preserves the visual order (user message first, then
          // the streaming bot reply). Sorting strings mixes server-side
          // "+00:00" isoformat timestamps with local `new Date().toISOString()`
          // "Z" values; localeCompare then groups by format instead of time,
          // which reordered the streaming reply above its user message after
          // switching back to a session mid-stream.
          messagesBySession[sessionId] = [...records, ...live];
        }
      }
      sessionProjects[sessionId] = normalizeSessionProject(payload.project);
      sessionArchivedFlags[sessionId] = Boolean(payload.archived);
      loadedSessions[sessionId] = true;
      // 2026-08-28 todo summary bar persistence: rebuild the session's
      // todo snapshot from the persisted history so the summary bar
      // survives a page
      // refresh or a session switch. The fold mirrors the live
      // tool_call_result path exactly (same parser, last successful
      // call wins, todo_clear clears). A snapshot written by a live
      // event while the fetch was in flight is newer than the cutoff
      // and is kept as-is.
      const existingTodoSnapshot = latestTodoSnapshotBySession.value[sessionId];
      if (
        !existingTodoSnapshot ||
        existingTodoSnapshot.updatedAt < todoReplayCutoff
      ) {
        const replayedTodoSnapshot = replayTodoSnapshotFromHistory(
          records,
          parseTodoToolResult,
        );
        if (replayedTodoSnapshot !== undefined) {
          writeTodoSnapshot(sessionId, replayedTodoSnapshot);
        }
      }
      if (resumeRuns && Array.isArray(payload.active_runs)) {
        await restoreNextActiveRun(sessionId, payload.active_runs);
      }
    } catch (error) {
      console.error("Failed to load session messages:", error);
      messagesBySession[sessionId] = messagesBySession[sessionId] || [];
    } finally {
      if (showLoading) loadingMessages.value = false;
    }
  }

  /**
   * Prepends one page of older history for a session (before_id cursor).
   * The caller is responsible for preserving the scroll position across the
   * prepend (scroll-height anchoring) — see Chat.vue load older flow.
   */
  async function loadOlderMessages(sessionId: string) {
    const paging = historyPagingBySession[sessionId];
    if (!paging || paging.loadingOlder || !paging.hasMore) return;
    paging.loadingOlder = true;
    try {
      const response = await chatApi.getHistory(sessionId, {
        before_id: paging.oldestLoadedId ?? undefined,
        limit: HISTORY_PAGE_SIZE,
      });
      const payload = response.data?.data || {};
      const olderRecords: ChatRecord[] = (payload.history || []).map(
        normalizeHistoryRecord,
      );
      attachThreads(olderRecords, payload.threads || []);
      await resolveRecordMedia(olderRecords);
      const existing: ChatRecord[] = messagesBySession[sessionId] || [];
      // Cursor pages are exclusive, so fetched records cannot overlap with the
      // loaded window; the id filter is a belt-and-suspenders guard.
      const existingIds = new Set(
        existing
          .map((r) => String(r.id))
          .filter((id) => id && id !== "undefined"),
      );
      const deduped = olderRecords.filter(
        (r) => r.id == null || !existingIds.has(String(r.id)),
      );
      if (deduped.length) {
        messagesBySession[sessionId] = [...deduped, ...existing];
      }
      const oldestRecord = deduped.find((r) => Number.isFinite(Number(r.id)));
      if (oldestRecord) paging.oldestLoadedId = Number(oldestRecord.id);
      paging.hasMore = Boolean(payload.has_more);
      historyOffsetBySession[sessionId] = Math.max(
        0,
        (historyOffsetBySession[sessionId] || 0) - deduped.length,
      );
    } catch (error) {
      console.error("Failed to load older messages:", error);
    } finally {
      paging.loadingOlder = false;
    }
  }

  /**
   * Fetches the session-wide user-message marker index for the scroll strip.
   * Fire-and-forget: a failure only degrades the strip to the loaded-window
   * (DOM measured) fallback.
   */
  async function loadSessionMarkers(sessionId: string) {
    const prev = sessionMarkersBySession[sessionId];
    if (prev?.loading) return;
    sessionMarkersBySession[sessionId] = {
      markers: prev?.markers || [],
      totalMessages: prev?.totalMessages || 0,
      truncated: prev?.truncated || false,
      loaded: prev?.loaded || false,
      loading: true,
    };
    try {
      const response = await chatApi.getMarkers(sessionId);
      const payload = response.data?.data || {};
      sessionMarkersBySession[sessionId] = {
        markers: (payload.markers || []).map((m: any) => ({
          id: Number(m.id),
          index: Number(m.index),
          snippet: String(m.snippet || ""),
        })),
        totalMessages: Number(payload.total_messages || 0),
        truncated: Boolean(payload.truncated),
        loaded: true,
        loading: false,
      };
    } catch (error) {
      console.error("Failed to load session markers:", error);
      sessionMarkersBySession[sessionId] = {
        markers: prev?.markers || [],
        totalMessages: prev?.totalMessages || 0,
        truncated: prev?.truncated || false,
        loaded: prev?.loaded || false,
        loading: false,
      };
    }
  }

  /**
   * Attach the page to a live run announced via the system stream
   * (`run_started`). The run stream serves a snapshot plus live chunks
   * through the exact primary processing path — the same transport a
   * user-initiated turn uses — so synthetic (agent-collab) turns render
   * identically to normal replies.
   */
  function attachLiveRun(sessionId: string, runId: string) {
    if (!sessionId || !runId || activeConnections[runId]) return;
    const messages = (messagesBySession[sessionId] =
      messagesBySession[sessionId] || []);
    // Already restored (e.g. via active_runs moments ago) — reuse that
    // record instead of pushing a duplicate bubble.
    const existing = messages.find(
      (r: ChatRecord) =>
        String(r.id) === `active-run-${runId}` ||
        String(r.id) === `local-bot-${runId}`,
    );
    if (existing) {
      startResumeStream(sessionId, runId, existing);
      return;
    }
    const botRecord = reactive<ChatRecord>({
      id: `local-bot-${runId}`,
      created_at: new Date().toISOString(),
      content: {
        type: "bot",
        message: [],
        reasoning: "",
        isLoading: true,
      },
    });
    messages.push(botRecord);
    startResumeStream(sessionId, runId, messages[messages.length - 1]);
  }

  async function restoreNextActiveRun(
    sessionId: string,
    activeRuns: ActiveChatRun[],
  ) {
    // 2026-08-27: goal-loop runs (llm_checkpoint_id = null) are still
    // owned by the system event stream — its subscribe-time run_snapshot
    // re-seeds the in-flight turn, so attaching a resume stream here
    // would render the turn twice. Collab injections are first-class
    // runs (registered via register_synthetic_chat_run) and carry a
    // checkpoint id, so they attach through this primary path.
    const run = activeRuns.find(
      (candidate) => candidate.run_id && candidate.llm_checkpoint_id,
    );
    if (!run || isSessionRunning(sessionId)) return;

    const checkpointId = run.llm_checkpoint_id || null;
    const records = (messagesBySession[sessionId] || []).filter((record) => {
      return !(
        checkpointId &&
        record.llm_checkpoint_id === checkpointId &&
        messageContent(record).type === "bot"
      );
    });
    const botRecord = normalizeHistoryRecord({
      id: `active-run-${run.run_id}`,
      content: run.content || { type: "bot", message: [] },
      llm_checkpoint_id: checkpointId,
      created_at: new Date().toISOString(),
    });
    botRecord.content.isLoading = botRecord.content.message.length === 0;
    records.push(botRecord);
    messagesBySession[sessionId] = records;
    const restoredRecords = messagesBySession[sessionId];
    const reactiveBotRecord = restoredRecords[restoredRecords.length - 1];
    await resolveRecordMedia([reactiveBotRecord]);
    startResumeStream(sessionId, run.run_id, reactiveBotRecord);
  }

  function createLocalExchange({
    sessionId,
    messageId,
    parts,
  }: CreateLocalExchangeOptions) {
    loadedSessions[sessionId] = true;
    messagesBySession[sessionId] = messagesBySession[sessionId] || [];

    abandonPendingInteractiveChoices(sessionId);

    const userRecord = reactive<ChatRecord>({
      id: `local-user-${messageId}`,
      created_at: new Date().toISOString(),
      content: {
        type: "user",
        message: parts.map(stripUploadOnlyFields),
      },
    });

    const botRecord = reactive<ChatRecord>({
      id: `local-bot-${messageId}`,
      created_at: new Date().toISOString(),
      content: {
        type: "bot",
        message: [],
        reasoning: "",
        isLoading: true,
      },
    });

    const sessionMessages = messagesBySession[sessionId];
    if (isSessionRunning(sessionId)) {
      const activeBotConnections = Object.values(activeConnections).filter(
        (connection) =>
          connection.sessionId === sessionId &&
          connection.botVisible !== false &&
          connection.botRecord &&
          sessionMessages.includes(connection.botRecord),
      );
      const activeBotRecord =
        activeBotConnections[activeBotConnections.length - 1]?.botRecord;
      if (activeBotRecord) {
        const activeBotIndex = sessionMessages.indexOf(activeBotRecord);
        sessionMessages.splice(activeBotIndex, 0, userRecord);
        deferredBotAnchors.set(botRecord, activeBotRecord);
      } else {
        sessionMessages.push(userRecord);
      }
    } else {
      sessionMessages.push(userRecord, botRecord);
    }

    return {
      userRecord,
      botRecord,
    };
  }

  function sendMessageStream({
    sessionId,
    messageId,
    parts,
    transport,
    enableStreaming = true,
    selectedProvider = "",
    selectedModel = "",
    thinkingEffort = "auto",
    botRecord,
    userRecord,
    skipUserHistory = false,
    llmCheckpointId = null,
  }: SendMessageStreamOptions) {
    if (transport === "websocket") {
      startWebSocketStream(
        sessionId,
        messageId,
        parts,
        botRecord,
        userRecord,
        enableStreaming,
        selectedProvider,
        selectedModel,
        thinkingEffort,
      );
      return;
    }
    startSseStream(
      sessionId,
      messageId,
      parts,
      botRecord,
      userRecord,
      enableStreaming,
      selectedProvider,
      selectedModel,
      thinkingEffort,
      skipUserHistory,
      llmCheckpointId,
    );
  }

  async function editMessage(
    sessionId: string,
    record: ChatRecord,
    editedText: string,
  ) {
    if (!sessionId || record.id == null) return { needsRegenerate: false };
    const content = cloneContentWithEditedText(record, editedText);
    const response = await chatApi.updateMessage(sessionId, record.id, {
      content: content as unknown as Record<string, unknown>,
    });
    const payload = response.data?.data || {};
    const updated = payload.message
      ? normalizeHistoryRecord(payload.message)
      : null;
    if (updated) {
      Object.assign(record, updated);
      await resolveRecordMedia([record]);
    }
    if (payload.truncated_after_message) {
      truncateMessagesAfter(sessionId, record);
    }
    return {
      needsRegenerate: Boolean(payload.needs_regenerate),
      truncatedAfterMessage: Boolean(payload.truncated_after_message),
    };
  }

  function truncateMessagesAfter(sessionId: string, record: ChatRecord) {
    const records = messagesBySession[sessionId];
    if (!records?.length || record.id == null) return;
    const index = records.findIndex(
      (message) => String(message.id) === String(record.id),
    );
    if (index < 0) return;
    messagesBySession[sessionId] = records.slice(0, index + 1);
  }

  function continueEditedMessage({
    sessionId,
    sourceRecord,
    enableStreaming = true,
    selectedProvider = "",
    selectedModel = "",
    thinkingEffort = "auto",
  }: ContinueEditedMessageOptions) {
    if (!sessionId) return;
    const parts = messageParts(sourceRecord).map(stripUploadOnlyFields);
    const messageId = crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    messagesBySession[sessionId] = messagesBySession[sessionId] || [];

    const botRecord: ChatRecord = {
      id: `local-edited-bot-${messageId}`,
      created_at: new Date().toISOString(),
      content: {
        type: "bot",
        message: [],
        reasoning: "",
        isLoading: true,
      },
    };
    messagesBySession[sessionId].push(botRecord);

    startSseStream(
      sessionId,
      messageId,
      parts,
      botRecord,
      undefined,
      enableStreaming,
      selectedProvider,
      selectedModel,
      thinkingEffort,
      true,
      sourceRecord.llm_checkpoint_id || null,
    );
  }

  async function regenerateMessage(
    sessionId: string,
    botRecord: ChatRecord,
    selectedProvider = "",
    selectedModel = "",
    enableStreaming = true,
    thinkingEffort = "auto",
  ) {
    if (!sessionId || botRecord.id == null) return;
    const targetMessageId = botRecord.id;

    botRecord.id = `local-regenerate-${Date.now()}`;
    botRecord.created_at = new Date().toISOString();
    botRecord.content = {
      type: "bot",
      message: [],
      reasoning: "",
      isLoading: true,
    };

    const abort = new AbortController();
    const connection: ActiveConnection = {
      sessionId,
      messageId: String(botRecord.id),
      transport: "sse",
      abort,
      botRecord,
      botVisible: true,
    };
    activeConnections[connection.messageId] = connection;

    try {
      const response = await fetchWithAuth(
        chatApi.regenerateMessageUrl(sessionId, targetMessageId),
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            selected_provider: selectedProvider,
            selected_model: selectedModel,
            thinking_effort: thinkingEffort,
            flags: buildChatRequestFlags(enableStreaming),
          }),
          signal: abort.signal,
        },
      );
      if (!response.ok || !response.body) {
        throw new Error(`Regenerate failed: ${response.status}`);
      }
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("text/event-stream")) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.message || "Regenerate failed.");
      }
      await readSseStream(response.body, (payload) => {
        processStreamPayload(botRecord, payload, undefined, connection);
        options.onStreamUpdate?.(sessionId);
      });
    } catch (error) {
      if (!abort.signal.aborted) {
        appendPlain(
          botRecord,
          `\n\n${String((error as Error)?.message || error)}`,
        );
        console.error("Regenerate failed:", error);
      }
    } finally {
      if (activeConnections[connection.messageId]?.abort === abort) {
        delete activeConnections[connection.messageId];
      }
      await options.onSessionsChanged?.();
      options.onStreamEnd?.(sessionId);
    }
  }

  async function stopSession(sessionId: string) {
    if (!sessionId) return;
    await chatApi.stopSession(sessionId);
  }

  function cleanupConnections() {
    Object.values(activeConnections).forEach((connection) => {
      connection.abort?.abort();
    });
    Object.values(chatWebSockets).forEach(closeTrackedWebSocket);
    Object.keys(activeConnections).forEach((messageId) => {
      delete activeConnections[messageId];
    });
    Object.keys(chatWebSockets).forEach((sessionId) => {
      delete chatWebSockets[sessionId];
    });
    Object.values(systemConnections).forEach((abort) => {
      abort.abort();
    });
    Object.keys(systemConnections).forEach((sessionId) => {
      finalizeSystemSession(systemStreamState, sessionId);
      delete systemConnections[sessionId];
    });
  }

  function normalizeHistoryRecord(record: any): ChatRecord {
    const content = record.content || {};
    const normalizedMessage = normalizeMessageParts(
      content.message || [],
      content.reasoning || "",
    );
    // Spread first so non-standard content types (e.g. branch_info) keep
    // their extra fields; known keys are normalized below.
    const normalizedContent: ChatContent = {
      ...content,
      type: content.type || (record.sender_id === "bot" ? "bot" : "user"),
      message: normalizedMessage,
      reasoning: extractReasoningText(
        normalizedMessage,
        content.reasoning || "",
      ),
      agentStats: content.agentStats || content.agent_stats,
      refs: content.refs,
    };

    return {
      ...record,
      content: normalizedContent,
    };
  }

  function attachThreads(records: ChatRecord[], threads: ChatThread[]) {
    const threadsByMessage = new Map<string, ChatThread[]>();
    for (const thread of threads) {
      const key = String(thread.parent_message_id);
      const list = threadsByMessage.get(key) || [];
      list.push(thread);
      threadsByMessage.set(key, list);
    }
    for (const record of records) {
      const key = record.id == null ? "" : String(record.id);
      record.threads = threadsByMessage.get(key) || [];
    }
  }

  function startSseStream(
    sessionId: string,
    messageId: string,
    parts: MessagePart[],
    botRecord: ChatRecord,
    userRecord: ChatRecord | undefined,
    enableStreaming: boolean,
    selectedProvider: string,
    selectedModel: string,
    thinkingEffort = "auto",
    skipUserHistory = false,
    llmCheckpointId: string | null = null,
  ) {
    const abort = new AbortController();
    const connection: ActiveConnection = {
      sessionId,
      messageId,
      transport: "sse",
      abort,
      botRecord,
      userRecord,
      botVisible: messagesBySession[sessionId]?.includes(botRecord) ?? false,
      deferredBeforeBot: deferredBotAnchors.get(botRecord),
    };
    activeConnections[messageId] = connection;

    fetchWithAuth(chatApi.sendStreamUrl(), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        session_id: sessionId,
        message: parts.map(partToPayload),
        flags: buildChatRequestFlags(enableStreaming),
        selected_provider: selectedProvider,
        selected_model: selectedModel,
        thinking_effort: thinkingEffort,
        _skip_user_history: skipUserHistory,
        _llm_checkpoint_id: llmCheckpointId || undefined,
      }),
      signal: abort.signal,
    })
      .then(async (response) => {
        if (!response.ok || !response.body) {
          throw new Error(`SSE connection failed: ${response.status}`);
        }
        await readSseStream(response.body, (payload) => {
          processStreamPayload(botRecord, payload, userRecord, connection);
          options.onStreamUpdate?.(sessionId);
        });
      })
      .catch((error) => {
        if (abort.signal.aborted) return;
        ensureBotRecordVisible(connection);
        appendPlain(botRecord, `\n\n${String(error?.message || error)}`);
        console.error("SSE chat failed:", error);
      })
      .finally(async () => {
        if (activeConnections[messageId]?.abort === abort) {
          delete activeConnections[messageId];
          await options.onSessionsChanged?.();
        }
        options.onStreamEnd?.(sessionId);
      });
  }

  function startResumeStream(
    sessionId: string,
    runId: string,
    botRecord: ChatRecord,
  ) {
    const abort = new AbortController();
    const connection: ActiveConnection = {
      sessionId,
      messageId: runId,
      runId,
      transport: "sse",
      abort,
      botRecord,
      botVisible: true,
    };
    activeConnections[runId] = connection;

    void (async () => {
      let receivedEnd = false;
      let lastError: unknown = null;

      for (
        let attempt = 0;
        attempt < 5 && !abort.signal.aborted;
        attempt += 1
      ) {
        let retryable = true;
        try {
          const response = await fetchWithAuth(
            chatApi.resumeRunStreamUrl(runId),
            {
              headers: { Accept: "text/event-stream" },
              signal: abort.signal,
            },
          );
          const contentType = response.headers.get("content-type") || "";
          if (
            !response.ok ||
            !response.body ||
            !contentType.includes("text/event-stream")
          ) {
            retryable = response.status >= 500;
            throw new Error(`Resume stream failed: ${response.status}`);
          }

          await readSseStream(response.body, (payload) => {
            processStreamPayload(botRecord, payload, undefined, connection);
            options.onStreamUpdate?.(sessionId);
            const payloadType = payload?.type || payload?.t;
            if (payloadType === "end") receivedEnd = true;
          });
          if (receivedEnd) break;
          lastError = new Error("Resume stream closed before completion.");
        } catch (error) {
          if (abort.signal.aborted) return;
          lastError = error;
        }

        if (!retryable || attempt === 4 || abort.signal.aborted) break;
        await new Promise<void>((resolve) => {
          const timeout = window.setTimeout(resolve, 250 * 2 ** attempt);
          abort.signal.addEventListener(
            "abort",
            () => {
              window.clearTimeout(timeout);
              resolve();
            },
            { once: true },
          );
        });
      }

      if (!receivedEnd && lastError && !abort.signal.aborted) {
        console.error("Resume chat stream failed:", lastError);
      }

      const ownsConnection = activeConnections[runId]?.abort === abort;
      if (ownsConnection) delete activeConnections[runId];
      if (!abort.signal.aborted && ownsConnection) {
        if (!isSessionRunning(sessionId)) {
          await loadSessionMessages(sessionId, true, false);
        }
        await options.onSessionsChanged?.();
      }
    })();
  }

  function startWebSocketStream(
    sessionId: string,
    messageId: string,
    parts: MessagePart[],
    botRecord: ChatRecord,
    userRecord: ChatRecord | undefined,
    enableStreaming: boolean,
    selectedProvider: string,
    selectedModel: string,
    thinkingEffort = "auto",
  ) {
    const ws = getOrCreateChatWebSocket(sessionId);

    const connection: ActiveConnection = {
      sessionId,
      messageId,
      transport: "websocket",
      ws,
      botRecord,
      userRecord,
      completed: false,
      errorShown: false,
      botVisible: messagesBySession[sessionId]?.includes(botRecord) ?? false,
      deferredBeforeBot: deferredBotAnchors.get(botRecord),
    };
    activeConnections[messageId] = connection;

    sendWebSocketPayload(sessionId, messageId, {
      ct: "chat",
      t: "send",
      session_id: sessionId,
      message_id: messageId,
      message: parts.map(partToPayload),
      flags: buildChatRequestFlags(enableStreaming),
      selected_provider: selectedProvider,
      selected_model: selectedModel,
      thinking_effort: thinkingEffort,
    });
  }

  function getWebSocketConnections(sessionId: string, ws?: WebSocket) {
    return Object.values(activeConnections).filter(
      (connection) =>
        connection.sessionId === sessionId &&
        connection.transport === "websocket" &&
        (!ws || connection.ws === ws),
    );
  }

  function getOrCreateChatWebSocket(sessionId: string) {
    const existing = chatWebSockets[sessionId];
    if (
      existing &&
      (existing.readyState === WebSocket.OPEN ||
        existing.readyState === WebSocket.CONNECTING)
    ) {
      return existing;
    }

    const token = localStorage.getItem("token") || "";
    const ws = new WebSocket(chatApi.unifiedWebSocketUrl(token));
    chatWebSockets[sessionId] = ws;

    ws.onmessage = (event) => {
      handleWebSocketMessage(sessionId, event);
    };
    ws.onerror = () => {
      for (const connection of getWebSocketConnections(sessionId, ws)) {
        if (!connection.botRecord) continue;
        connection.errorShown = true;
        ensureBotRecordVisible(connection);
        appendPlain(connection.botRecord, "\n\nWebSocket connection failed.");
      }
    };
    ws.onclose = async () => {
      if (chatWebSockets[sessionId] === ws) {
        delete chatWebSockets[sessionId];
      }

      const connections = getWebSocketConnections(sessionId, ws);
      for (const connection of connections) {
        if (
          !connection.completed &&
          !connection.errorShown &&
          !closingChatWebSockets.has(ws) &&
          connection.botRecord
        ) {
          ensureBotRecordVisible(connection);
          appendPlain(connection.botRecord, "\n\nWebSocket connection closed.");
        }
        delete activeConnections[connection.messageId];
      }
      if (connections.length) {
        await options.onSessionsChanged?.();
        options.onStreamEnd?.(sessionId);
      }
    };
    return ws;
  }

  function sendWebSocketPayload(
    sessionId: string,
    messageId: string,
    payload: Record<string, unknown>,
  ) {
    const ws = getOrCreateChatWebSocket(sessionId);
    const send = () => {
      const connection = activeConnections[messageId];
      if (
        connection?.transport !== "websocket" ||
        connection.messageId !== messageId ||
        connection.ws !== ws
      ) {
        return;
      }
      try {
        ws.send(JSON.stringify(payload));
      } catch (error) {
        connection.errorShown = true;
        if (connection.botRecord) {
          ensureBotRecordVisible(connection);
          appendPlain(connection.botRecord, "\n\nWebSocket connection failed.");
        }
        console.error("Failed to send WebSocket payload:", error);
        void finishWebSocketStream(sessionId, messageId);
      }
    };

    if (ws.readyState === WebSocket.OPEN) {
      send();
      return;
    }
    if (ws.readyState === WebSocket.CONNECTING) {
      ws.addEventListener("open", send, { once: true });
      return;
    }
    void finishWebSocketStream(sessionId, messageId);
  }

  function handleWebSocketMessage(sessionId: string, event: MessageEvent) {
    try {
      const payload = JSON.parse(event.data);
      const payloadMessageId =
        payload?.message_id == null ? "" : String(payload.message_id);
      let connection = payloadMessageId
        ? activeConnections[payloadMessageId]
        : undefined;
      if (!connection) {
        const candidates = getWebSocketConnections(sessionId);
        connection = candidates[0];
      }
      if (connection?.transport !== "websocket" || !connection.botRecord) {
        return;
      }
      processStreamPayload(
        connection.botRecord,
        payload,
        connection.userRecord,
        connection,
      );
      options.onStreamUpdate?.(sessionId);
      if (payload.type === "end" || payload.t === "end") {
        void finishWebSocketStream(sessionId, connection.messageId);
      }
    } catch (error) {
      console.error("Failed to parse WebSocket payload:", error);
    }
  }

  async function finishWebSocketStream(sessionId: string, messageId: string) {
    const connection = activeConnections[messageId];
    if (
      connection?.transport !== "websocket" ||
      connection.messageId !== messageId
    ) {
      return;
    }
    connection.completed = true;
    delete activeConnections[messageId];
    await options.onSessionsChanged?.();
    options.onStreamEnd?.(sessionId);
  }

  function closeTrackedWebSocket(ws: WebSocket) {
    closingChatWebSockets.add(ws);
    if (
      ws.readyState === WebSocket.OPEN ||
      ws.readyState === WebSocket.CONNECTING
    ) {
      ws.close();
    }
  }

  /**
   * 按 sessionId 隔离的最新 todo 快照。
   *
   * - value[sid] = TodoSnapshot:
   *     todo_create/query/add/update/delete(及 v2.12 之前的 todo_modify)
   *     成功时写入。
   * - value[sid] = null:todo_clear 成功时显式置空(bar 立即消失)。
   * - key 不存在:该 session 尚未调用过 todo 工具(bar 不显示)。
   *
   * 写入策略:每次整体替换 `value = {...current, [sid]: snap}`,
   * 100% 触发依赖此 ref 的 computed 重算(Chat.vue 里的 currentTodoSnapshot)。
   * 整体替换而非 in-place 修改,是为了在 useMessages 多次调用 writeTodos
   * 时 Vue 一定能捕获到依赖变化(对 in-place 写入 `value[sid] = x`,
   * Vue 3 的 ref 对嵌套对象可能不触发响应,需要 .value = .value 触发)。
   */
  const latestTodoSnapshotBySession = ref<Record<string, TodoSnapshot | null>>(
    {},
  );

  /**
   * 从 tool.result(JSON 字符串)中解析出 TodoSnapshot。
   *
   * 返回值语义:
   * - `TodoSnapshot` 对象:write 进去,bar/sidebar 立即刷新。
   * - `null`:显式清空(todo_clear 成功路径)。
   * - `undefined`:保持现状 — 用于失败/无法解析的情况,避免覆盖已有快照。
   *
   * 协议参考:spcode 插件 `tools/_helpers.py` 中的 `unwrap()` —
   * 成功路径输出 `{"ok": true, "data": {list, stats, attention_items}, ...}`,
   * 失败路径输出 `{"ok": false, "error": "..."}`(或 err_json)。
   * 部分响应可能携带 `proposal` / `options` 字段,这些不影响 data 段。
   */
  function parseTodoToolResult(
    toolName: string,
    resultJson: string,
  ): TodoSnapshot | null | undefined {
    if (!resultJson || typeof resultJson !== "string") return undefined;
    let parsed: any;
    try {
      parsed = JSON.parse(resultJson);
    } catch {
      return undefined;
    }
    if (!parsed || typeof parsed !== "object") return undefined;

    // 失败路径 → 保持现状,不动快照
    if (parsed.ok === false) return undefined;

    // todo_clear 成功 → 显式置空(让 bar 立即消失)
    if (toolName === "todo_clear") {
      return null;
    }

    // 拆 envelope:{"ok": true, "data": {...}} → 取 data
    // 也兼容少数情况(老 todo_list 工具、proposal 路径等)data 直接挂在根上
    const data =
      parsed.ok === true && parsed.data && typeof parsed.data === "object"
        ? parsed.data
        : parsed;

    const list = data?.list;
    const stats = data?.stats;
    if (!list || typeof list !== "object") return undefined;
    if (!stats || typeof stats !== "object") return undefined;

    const attentionRaw = data?.attention_items;
    const attentionItems: number[] = Array.isArray(attentionRaw)
      ? attentionRaw.filter((n) => typeof n === "number")
      : [];

    return {
      list: list as TodoList,
      stats: stats as TodoStats,
      attentionItems,
      updatedAt: Date.now(),
    };
  }

  /**
   * 在 record 的 tool_call parts 中按 id 找到对应的 tool 调用项。
   * 用于在 tool_call_result 阶段反查 tool_name 和 tool.arguments。
   */
  function findToolCallById(
    record: ChatRecord,
    callId: string | number | undefined,
  ): ToolCall | null {
    if (callId == null) return null;
    for (const part of record.content.message) {
      if (part.type !== "tool_call" || !Array.isArray(part.tool_calls))
        continue;
      const matched = part.tool_calls.find((item) => item.id === callId);
      if (matched) return matched;
    }
    return null;
  }

  /**
   * 写入 / 清除某个 session 的 todo 快照。
   * 触发响应式更新(整体替换 ref.value)。
   */
  function writeTodoSnapshot(
    sessionId: string | null | undefined,
    snapshot: TodoSnapshot | null,
  ) {
    if (!sessionId) return;
    latestTodoSnapshotBySession.value = {
      ...latestTodoSnapshotBySession.value,
      [sessionId]: snapshot,
    };
  }

  // Author: elecvoid243
  // Date: 2026-07-05
  // Plan: docs/superpowers/plans/2026-07-05-interactive-choice-history-roundtrip.md
  // §2.4 Amendment F — closure-scoped set of call_ids whose `tool_call`
  // was filtered as `ask_user_choice`. The matching `tool_call_result`
  // event is dropped using this set (the result payload itself does
  // not carry the tool name, so we cannot tell from the payload
  // alone).
  //
  // Scoping note: this is a per-SSE-connection set. The dashboard
  // opens a fresh SSE stream for each message send, so cross-session
  // leakage is structurally impossible — there is no shared
  // long-lived store. The history reload path
  // (`normalizePartsInternal`) handles the persistent-data side
  // independently.
  const filteredToolCallIds = new Set<string>();

  function ensureBotRecordVisible(connection: ActiveConnection) {
    const { botRecord, userRecord } = connection;
    if (!botRecord) return;
    const records = messagesBySession[connection.sessionId] || [];
    if (records.includes(botRecord)) {
      connection.botVisible = true;
      return;
    }
    if (!userRecord) return;

    const userIndex = records.indexOf(userRecord);
    let insertionAnchor = connection.deferredBeforeBot;
    if (insertionAnchor) {
      for (const candidate of Object.values(activeConnections)) {
        if (candidate.messageId === connection.messageId) break;
        if (
          candidate.sessionId === connection.sessionId &&
          candidate.deferredBeforeBot === connection.deferredBeforeBot &&
          candidate.botVisible &&
          candidate.botRecord &&
          records.includes(candidate.botRecord)
        ) {
          insertionAnchor = candidate.botRecord;
        }
      }
    }
    const anchorIndex = insertionAnchor ? records.indexOf(insertionAnchor) : -1;
    if (anchorIndex >= 0) {
      records.splice(anchorIndex + 1, 0, botRecord);
    } else if (userIndex >= 0) {
      records.splice(userIndex + 1, 0, botRecord);
    } else {
      records.push(botRecord);
    }
    connection.botVisible = true;
  }

  function processStreamPayload(
    botRecord: ChatRecord,
    payload: any,
    userRecord?: ChatRecord,
    connection?: ActiveConnection,
  ) {
    const sessionId = connection?.sessionId;
    const normalized =
      payload?.ct === "chat"
        ? { ...payload, type: payload.type || payload.t }
        : payload;
    const msgType = normalized?.type || normalized?.t;
    const chainType = normalized?.chain_type;
    const data = normalized?.data ?? "";

    if (msgType === "follow_up_captured") {
      if (connection) {
        connection.followUpCaptured = true;
        connection.followUpTargetRunId = String(data?.target_run_id || "");
        const target = Object.values(activeConnections).find(
          (candidate) =>
            candidate.runId === connection.followUpTargetRunId &&
            candidate.botRecord,
        );
        const records = messagesBySession[connection.sessionId] || [];
        if (target?.botRecord && connection.userRecord) {
          const userIndex = records.indexOf(connection.userRecord);
          const targetIndex = records.indexOf(target.botRecord);
          if (targetIndex >= 0) {
            if (userIndex > targetIndex) {
              records.splice(userIndex, 1);
              const updatedTargetIndex = records.indexOf(target.botRecord);
              records.splice(updatedTargetIndex, 0, connection.userRecord);
            } else if (userIndex < 0) {
              records.splice(targetIndex, 0, connection.userRecord);
            }
            connection.deferredBeforeBot = target.botRecord;
          } else if (userIndex < 0) {
            records.push(connection.userRecord);
          }
        }
      }
      return;
    }
    if (msgType === "run_started") {
      if (connection) {
        connection.runId = String(data?.run_id || connection.messageId);
        ensureBotRecordVisible(connection);
      }
      return;
    }
    if (msgType === "session_id" || msgType === "session_bound") return;
    if (connection && msgType !== "user_message_saved" && msgType !== "end") {
      ensureBotRecordVisible(connection);
    }
    if (msgType === "run_snapshot") {
      const snapshot = data && typeof data === "object" ? data : {};
      const snapshotRecord = normalizeHistoryRecord({
        id: `active-run-${snapshot.run_id || "unknown"}`,
        content: snapshot.content || { type: "bot", message: [] },
        llm_checkpoint_id: snapshot.llm_checkpoint_id || null,
      });
      snapshotRecord.content.isLoading =
        snapshot.status === "running" &&
        snapshotRecord.content.message.length === 0;
      botRecord.content = snapshotRecord.content;
      botRecord.llm_checkpoint_id = snapshotRecord.llm_checkpoint_id;
      void resolveRecordMedia([botRecord]);
      return;
    }
    if (msgType === "user_message_saved") {
      if (userRecord) {
        userRecord.id = data?.id || userRecord.id;
        userRecord.created_at = data?.created_at || userRecord.created_at;
        userRecord.llm_checkpoint_id =
          data?.llm_checkpoint_id || userRecord.llm_checkpoint_id;
      }
      return;
    }
    if (msgType === "message_saved") {
      markMessageStarted(botRecord);
      botRecord.id = data?.id || botRecord.id;
      botRecord.created_at = data?.created_at || botRecord.created_at;
      botRecord.llm_checkpoint_id =
        data?.llm_checkpoint_id || botRecord.llm_checkpoint_id;
      if (data?.refs) {
        messageContent(botRecord).refs = data.refs;
      }
      return;
    }
    if (msgType === "agent_stats" || chainType === "agent_stats") {
      markMessageStarted(botRecord);
      messageContent(botRecord).agentStats = data;
      return;
    }
    if (msgType === "error") {
      markMessageStarted(botRecord);
      appendPlain(botRecord, `\n\n${String(data)}`);
      return;
    }
    if (msgType === "complete" || msgType === "break") {
      markMessageStarted(botRecord);
      const finalText = payloadText(data);
      const existingText = botRecord.content.message
        .filter((part) => part.type === "plain")
        .map((part) => part.text || "")
        .join("");
      const missingText = finalText.slice(existingText.length);
      if (
        msgType === "complete" &&
        missingText &&
        finalText.startsWith(existingText)
      ) {
        appendPlain(botRecord, missingText);
      } else if (finalText && !hasPlainText(botRecord)) {
        appendPlain(botRecord, finalText, false);
      }
      return;
    }
    if (msgType === "end") {
      markMessageStarted(botRecord);
      return;
    }

    // ── InteractiveChoice (Plan Amendment E.1 / spec §5.1) ──────────
    // The backend's ask_user_choice plugin emits a top-level SSE event
    // (not embedded in a tool_call part) via
    // `_push_to_webchat_back_queue`. The dispatcher:
    //   1. parses + validates the wire payload into a part,
    //   2. pushes the part into `botRecord.content.message` so
    //      `ChatMessageList.vue` (which iterates `messageParts(msg)`)
    //      can render `<InteractiveChoiceBox>` immediately,
    //   3. mirrors the part into the Pinia store for hydrate / reconcile.
    // The store-only path is insufficient because the v-else-if
    // branch in `ChatMessageList.vue` reads from `messageParts(msg)`,
    // not from the store — without step (2) the box never mounts.
    if (msgType === "interactive_choice") {
      // Bug Y1 fix: scope the store write to the live session. We
      // guard on `sessionId` for safety — if a caller forgets to
      // thread sessionId through to a stream consumer, we skip the
      // write rather than dump the part into a phantom bucket.
      if (!sessionId) {
        console.warn(
          "[interactiveChoice] SSE event without sessionId; dropping",
        );
        return;
      }
      if (applyInteractiveChoiceSse(sessionId, botRecord, normalized)) {
        options.onInteractiveChoice?.(sessionId);
      }
      return;
    } else if (msgType === "interactive_choice_resolved") {
      // Bug Y1 fix (mirrors the branch above): scope the store
      // write to the live session. Guard on `sessionId` so a
      // caller that forgets to thread sessionId through to a
      // stream consumer doesn't trip the dispatcher's
      // "missing required 'umo'" throw. Do NOT push anything to
      // botRecord.content.message here — the resolved event
      // carries no spec; the box reads cancelledStates from the
      // store and re-renders via the `state` computed property
      // (added in Task F4).
      if (!sessionId) {
        console.warn(
          "[interactiveChoice] resolved SSE event without sessionId; dropping",
        );
        return;
      }
      applyInteractiveChoiceResolved(sessionId, normalized);
      return;
    }

    if (msgType === "plain") {
      markMessageStarted(botRecord);
      if (chainType === "reasoning") {
        appendReasoningPart(botRecord, payloadText(data));
        return;
      }
      if (chainType === "tool_call") {
        const parsed = parseJsonSafe(data);
        // Author: elecvoid243
        // Date: 2026-07-05
        // Plan: docs/superpowers/plans/2026-07-05-interactive-choice-history-roundtrip.md
        // §2.4 Amendment F — `ask_user_choice` is rendered exclusively as
        // an `interactive_choice` part. Skip the corresponding
        // `tool_call` (and the matching `tool_call_result` below) so the
        // dashboard never shows a phantom "tool" entry next to the
        // InteractiveChoiceBox. Remember the id so the result can be
        // dropped in turn.
        if (isAskUserChoiceToolCall(parsed)) {
          const callId = (parsed as { id?: unknown } | null)?.id;
          if (typeof callId === "string" || typeof callId === "number") {
            filteredToolCallIds.add(String(callId));
          }
          return;
        }
        upsertToolCall(botRecord, parsed);
        if (sessionId) options.onToolCallActivity?.(sessionId);
        return;
      }
      // Author: elecvoid243
      // Date: 2026-07-05
      // Plan: docs/superpowers/plans/2026-07-05-interactive-choice-history-roundtrip.md §2.4
      // The plugin emits the box as a chain_type event
      // (`type: "plain"`, `chain_type: "interactive_choice"`,
      // `data: <json string>`) so the chat_service can persist the
      // part into the bot record. We unwrap the JSON string into
      // the historical envelope `{type, data}` so
      // `applyInteractiveChoiceSse` and its downstream
      // `interactiveChoicePartFromSsePayload` helper need no
      // contract change.
      if (chainType === "interactive_choice") {
        if (!sessionId) {
          console.warn(
            "[interactiveChoice] SSE event without sessionId; dropping",
          );
          return;
        }
        const inner = parseJsonSafe(data);
        if (!inner || typeof inner !== "object") {
          console.warn(
            "[interactiveChoice] SSE data is not a JSON object; dropping",
          );
          return;
        }
        if (
          applyInteractiveChoiceSse(sessionId, botRecord, {
            type: "interactive_choice",
            data: inner,
          })
        ) {
          options.onInteractiveChoice?.(sessionId);
        }
        return;
      }
      if (chainType === "tool_call_result") {
        const parsed = parseJsonSafe(data);
        // Author: elecvoid243
        // Date: 2026-07-05
        // Plan: docs/superpowers/plans/2026-07-05-interactive-choice-history-roundtrip.md
        // §2.4 Amendment F — Drop `tool_call_result` events whose matching
        // `tool_call` was an `ask_user_choice` (already filtered above).
        // `finishToolCall` would otherwise synthesise a name-less part on
        // miss, which renders as "tool" next to the InteractiveChoiceBox.
        if (
          isAskUserChoiceToolCallResult(botRecord, parsed, filteredToolCallIds)
        ) {
          return;
        }
        finishToolCall(botRecord, parsed);

        // todo 工具结果 → 解析并写入按 sessionId 隔离的快照。
        // 注意:tool_call 事件先于 tool_call_result 到达,tool.name 已被
        // upsertToolCall 写入 botRecord,这里按 id 反查即可。
        if (sessionId && parsed && typeof parsed === "object") {
          const callId = (parsed as any).id;
          const matched = findToolCallById(botRecord, callId);
          const toolName = matched?.name;
          if (
            toolName &&
            TODO_TOOL_NAMES.has(toolName) &&
            typeof (parsed as any).result === "string"
          ) {
            const snapshot = parseTodoToolResult(
              toolName,
              (parsed as any).result,
            );
            // undefined → 保持现状;null → 清空;对象 → 写入。
            if (snapshot !== undefined) {
              writeTodoSnapshot(sessionId, snapshot);
            }
          }
        }
        return;
      }
      appendPlain(botRecord, payloadText(data), normalized.streaming !== false);
      return;
    }

    // Structured subagent progress events fold into a dedicated
    // `subagent_run` part on the bot record (live + persisted history).
    if (msgType === "subagent_event") {
      if (!Array.isArray(botRecord.content.message)) {
        botRecord.content.message = [];
      }
      applySubAgentEvent(botRecord.content.message as MessagePart[], data);
      return;
    }

    if (["image", "record", "file", "video"].includes(msgType)) {
      markMessageStarted(botRecord);
      const rawFilename = String(data)
        .replace("[IMAGE]", "")
        .replace("[RECORD]", "")
        .replace("[FILE]", "")
        .replace("[VIDEO]", "");
      const separatorIndex = rawFilename.indexOf("|");
      const storedFilename =
        separatorIndex >= 0
          ? rawFilename.slice(0, separatorIndex)
          : rawFilename;
      const displayFilename =
        separatorIndex >= 0
          ? rawFilename.slice(separatorIndex + 1)
          : storedFilename;
      const filename = displayFilename || storedFilename;
      const mediaPart: MessagePart = { type: msgType, filename };
      if (storedFilename && storedFilename !== filename) {
        mediaPart.stored_filename = storedFilename;
      }
      if (msgType !== "file") {
        resolvePartMedia(mediaPart).then(() => {
          messageContent(botRecord).message.push(mediaPart);
        });
      } else {
        messageContent(botRecord).message.push(mediaPart);
      }
    }
  }

  return {
    loadingMessages,
    sending,
    messagesBySession,
    loadedSessions,
    sessionProjects,
    sessionArchivedFlags,
    historyPagingBySession,
    historyOffsetBySession,
    sessionMarkersBySession,
    activeMessages,
    isSessionRunning,
    hasLiveSystemRecord,
    isUserMessage,
    isMessageStreaming,
    messageContent,
    messageParts,
    loadSessionMessages,
    loadOlderMessages,
    createLocalExchange,
    sendMessageStream,
    editMessage,
    continueEditedMessage,
    regenerateMessage,
    stopSession,
    cleanupConnections,
    /**
     * 按 sessionId 隔离的最新 todo 快照。
     * Chat.vue 通过此字段渲染 todo summary bar 悬浮菜单。
     * 详见 `parseTodoToolResult` 注释。
     */
    latestTodoSnapshotBySession,
  };
}

function cloneContentWithEditedText(
  record: ChatRecord,
  editedText: string,
): ChatContent {
  const content = record.content || { type: "bot", message: [] };
  const message = Array.isArray(content.message)
    ? content.message.map((part) => ({ ...part }))
    : [];
  let replaced = false;
  for (const part of message) {
    if (part.type === "plain") {
      part.text = editedText;
      replaced = true;
      break;
    }
  }
  if (!replaced && editedText) {
    message.push({ type: "plain", text: editedText });
  }
  return {
    ...content,
    message,
  };
}

function stripUploadOnlyFields(part: MessagePart): MessagePart {
  const copied = { ...part };
  delete copied.path;
  return copied;
}

function normalizeSessionProject(value: unknown): ChatSessionProject | null {
  if (!value || typeof value !== "object") return null;
  const project = value as Record<string, unknown>;
  if (
    typeof project.project_id !== "string" ||
    typeof project.title !== "string"
  ) {
    return null;
  }

  return {
    project_id: project.project_id,
    title: project.title,
    emoji: typeof project.emoji === "string" ? project.emoji : undefined,
  };
}

export function normalizeMessageParts(
  parts: unknown,
  fallbackReasoning = "",
): MessagePart[] {
  const normalizedParts = normalizePartsInternal(parts);
  if (
    fallbackReasoning &&
    !normalizedParts.some((part) => part.type === "think")
  ) {
    normalizedParts.unshift({ type: "think", think: fallbackReasoning });
  }
  return normalizedParts;
}

export function extractReasoningText(
  parts: MessagePart[] | unknown,
  fallbackReasoning = "",
) {
  const normalizedParts = Array.isArray(parts)
    ? parts
    : normalizeMessageParts(parts, fallbackReasoning);
  const text = normalizedParts
    .filter((part) => part.type === "think")
    .map((part) => String(part.think || ""))
    .join("");
  return text || fallbackReasoning;
}

export function reasoningActivityCounts(
  parts: MessagePart[] | unknown,
  fallbackReasoning = "",
) {
  const normalizedParts = Array.isArray(parts)
    ? parts
    : normalizeMessageParts(parts, fallbackReasoning);
  let thinkCount = 0;
  let toolCount = 0;

  for (const part of normalizedParts) {
    if (part.type === "think" && String(part.think || "").trim()) {
      thinkCount += 1;
    }
    if (part.type === "tool_call" && Array.isArray(part.tool_calls)) {
      toolCount += part.tool_calls.length;
    }
  }

  // 2026-08-11 file-change visibility: count edit/write/remove calls so
  // the collapsed reasoning bar can say "changed N files".
  const fileChangeCount = collectFileChanges(normalizedParts).length;

  return { thinkCount, toolCount, fileChangeCount };
}

export function reasoningActivityTitle(
  counts: ReturnType<typeof reasoningActivityCounts>,
  tm: (key: string, params?: Record<string, string | number>) => string,
) {
  return (
    [
      counts.thinkCount > 0
        ? tm("reasoning.thinkSummary", { count: counts.thinkCount })
        : "",
      counts.toolCount > 0
        ? tm("reasoning.toolSummary", { count: counts.toolCount })
        : "",
      counts.fileChangeCount > 0
        ? tm("reasoning.fileChangeSummary", { count: counts.fileChangeCount })
        : "",
    ]
      .filter(Boolean)
      .join(tm("reasoning.summarySeparator")) || tm("reasoning.thinking")
  );
}

export function thinkingParts(content: ChatContent): MessagePart[] {
  const firstThinkingBlock = messageBlocks(content).find(
    (block) => block.kind === "thinking",
  );
  if (firstThinkingBlock) return firstThinkingBlock.parts;

  const fallbackReasoning = String(content.reasoning || "");
  return fallbackReasoning ? [{ type: "think", think: fallbackReasoning }] : [];
}

export function displayParts(content: ChatContent): MessagePart[] {
  return messageBlocks(content)
    .filter((block) => block.kind === "content")
    .flatMap((block) => block.parts);
}

export function messageBlocks(content: ChatContent): MessageDisplayBlock[] {
  const parts = Array.isArray(content.message)
    ? content.message
    : normalizeMessageParts(content.message, content.reasoning || "");

  const blocks: MessageDisplayBlock[] = [];
  let currentKind: MessageDisplayBlock["kind"] | null = null;
  let currentParts: MessagePart[] = [];

  for (const part of parts) {
    if (isEmptyPlainPart(part)) continue;

    const nextKind: MessageDisplayBlock["kind"] = isThinkingPart(part)
      ? "thinking"
      : "content";

    if (currentKind !== nextKind) {
      if (currentKind && currentParts.length) {
        blocks.push({ kind: currentKind, parts: currentParts });
      }
      currentKind = nextKind;
      currentParts = [{ ...part }];
      continue;
    }

    currentParts.push({ ...part });
  }

  if (currentKind && currentParts.length) {
    blocks.push({ kind: currentKind, parts: currentParts });
  }

  if (!blocks.length && content.reasoning) {
    return [
      {
        kind: "thinking",
        parts: [{ type: "think", think: String(content.reasoning) }],
      },
    ];
  }

  return blocks;
}

function partToPayload(part: MessagePart) {
  if (part.type === "plain") return { type: "plain", text: part.text || "" };
  if (part.type === "reply") {
    return {
      type: "reply",
      message_id: part.message_id,
      selected_text: part.selected_text || "",
    };
  }
  return {
    type: part.type,
    attachment_id: part.attachment_id,
    filename: part.filename,
  };
}

async function readSseStream(
  body: ReadableStream<Uint8Array>,
  onPayload: (payload: any) => void,
) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";

    for (const event of events) {
      const data = event
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (!data) continue;
      try {
        onPayload(JSON.parse(data));
      } catch (error) {
        console.error("Failed to parse SSE payload:", error, data);
      }
    }
  }
}

function normalizePartsInternal(parts: unknown): MessagePart[] {
  // Author: elecvoid243
  // Date: 2026-07-05
  // Plan: docs/superpowers/plans/2026-07-05-interactive-choice-history-roundtrip.md
  // §2.4 Amendment F — `normalizePartsInternal` was moved to a leaf
  // module (`normalizeMessageParts.ts`) so the ask_user_choice
  // history-reload filter can be unit-tested from a bare node
  // runner. This thin wrapper exists only to preserve the
  // `useMessages.ts` internal call sites and the public
  // `normalizeMessageParts` re-export.
  return normalizeMessagePartsFromLeaf(parts) as MessagePart[];
}

function isEmptyPlainPart(part: MessagePart) {
  return part.type === "plain" && !String(part.text || "");
}

function isThinkingPart(part: MessagePart) {
  return part.type === "think" || part.type === "tool_call";
}

function firstNonEmptyPartIndex(parts: MessagePart[]) {
  return parts.findIndex((part) => !isEmptyPlainPart(part));
}

export function appendPlain(record: ChatRecord, text: string, append = true) {
  markMessageStarted(record);
  const content = record.content;
  let last = content.message[content.message.length - 1];
  if (!last || last.type !== "plain") {
    last = { type: "plain", text: "" };
    content.message.push(last);
  }
  last.text = append ? `${last.text || ""}${text}` : text;
}

export function appendReasoningPart(record: ChatRecord, text: string) {
  markMessageStarted(record);
  if (!text) return;
  const content = record.content;
  const last = content.message[content.message.length - 1];
  if (last?.type === "think") {
    last.think = `${String(last.think || "")}${text}`;
  } else {
    content.message.push({ type: "think", think: text });
  }
  content.reasoning = extractReasoningText(content.message);
}

export function upsertToolCall(record: ChatRecord, toolCall: any) {
  markMessageStarted(record);
  if (!toolCall || typeof toolCall !== "object") return;
  const targetId = toolCall.id;
  if (targetId != null) {
    for (const part of record.content.message) {
      if (part.type !== "tool_call" || !Array.isArray(part.tool_calls))
        continue;
      const matched = part.tool_calls.find((item) => item.id === targetId);
      if (matched) {
        Object.assign(matched, toolCall);
        return;
      }
    }
  }
  record.content.message.push({
    type: "tool_call",
    tool_calls: [{ ...toolCall }],
  });
}

export function finishToolCall(record: ChatRecord, result: any) {
  markMessageStarted(record);
  if (!result || typeof result !== "object") return;
  const targetId = result.id;
  for (const part of record.content.message) {
    if (part.type !== "tool_call" || !Array.isArray(part.tool_calls)) continue;
    const tool = part.tool_calls.find((item) => item.id === targetId);
    if (tool) {
      tool.result = result.result;
      tool.finished_ts = result.ts || Date.now() / 1000;
      return;
    }
  }
  record.content.message.push({
    type: "tool_call",
    tool_calls: [
      {
        id: targetId,
        result: result.result,
        finished_ts: result.ts || Date.now() / 1000,
      },
    ],
  });
}

// Author: elecvoid243
// Date: 2026-07-05
// Plan: docs/superpowers/plans/2026-07-05-interactive-choice-history-roundtrip.md
// §2.4 Amendment F — the predicate helpers
// `isAskUserChoiceToolCall` / `isAskUserChoiceToolCallResult` live
// in `askUserChoiceToolFilter.ts` (a leaf module with no `@/api`
// dependency) so the live SSE handler and the history reload path
// share one source of truth. The const
// `ASK_USER_CHOICE_TOOL_NAME` is exported from there directly;
// any consumer that previously imported it from `useMessages.ts`
// can switch to the leaf module without behavioural change.

export function markMessageStarted(record: ChatRecord) {
  record.content.isLoading = false;
}

export function hasPlainText(record: ChatRecord) {
  return record.content.message.some(
    (part) =>
      part.type === "plain" && typeof part.text === "string" && part.text,
  );
}

export function payloadText(value: unknown) {
  if (typeof value === "string") return value;
  if (value == null) return "";
  if (typeof value === "object") {
    const payload = value as Record<string, unknown>;
    if (typeof payload.text === "string") return payload.text;
    if (typeof payload.content === "string") return payload.content;
    if (typeof payload.message === "string") return payload.message;
  }
  return String(value);
}

export function parseJsonSafe(value: unknown) {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}
