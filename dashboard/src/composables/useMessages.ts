import { computed, onBeforeUnmount, reactive, ref, watch, type Ref } from "vue";
import { chatApi, fileApi } from "@/api/v1";
import {
  accountSessionRevision,
  assertCurrentAccount,
} from "@/utils/accountSession";
import { fetchWithAuth } from "@/api/http";
import {
  collectFileChanges,
  parseFileChangesPayload,
} from "@/utils/fileChangeTool";
import type { FileChangeSummaryFile } from "@/utils/fileChangeTool";
import { useInteractiveChoiceStore } from "@/stores/interactiveChoice";
import {
  createSystemStreamState,
  finalizeSystemSession,
  processSystemPayload,
} from "./systemStream";
import type { SessionGoalState } from "@/api/v1";
import type { Session } from "./useSessions";
import type { ShellSessionListItem } from "@/components/chat/message_list_comps/shell_session_tools/format";
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
import {
  assistantFileComments,
  withoutAssistantFileDirectives,
} from "@/utils/assistantFileComments";
// Author: elecvoid243
// Date: 2026-10-08
// Live-only context compression notice: the leaf owns the wire parsing and
// the toast message so this consumer stays thin wiring (and testable).
import {
  parseContextCompressionNotice,
  type ContextCompressionNotice,
} from "./contextCompressionNotice";
// LLM request injection trace: leaf parsing for the gutter marker payload.
import {
  parseLlmRequestInjections,
  type LlmRequestInjections,
} from "./llmRequestInjections";
// Developer-mode LLM request snapshots: live-only telemetry folded onto the
// bot record. Leaf parsing/merging keeps the SSE branch thin.
import {
  mergeLlmRequestSnapshots,
  parseLlmRequestSnapshots,
  type LlmRequestSnapshotEnvelope,
} from "./llmRequestSnapshots";
// Live reducer for structured subagent progress stream payloads.
import { applySubAgentEvent } from "./subagentRunReducer";
// spcode todo_* tool protocol: shared tool-name set + the history
// replay fold that rebuilds the todo snapshot on refresh / session
// switch (leaf module — see todoHistoryReplay.ts header).
import {
  TODO_TOOL_NAMES,
  parseTodoToolResult,
  replayTodoSnapshotFromHistory,
  resolveTodoAction,
} from "./todoHistoryReplay";

export type TransportMode = "sse" | "websocket";

// History windowing: opening a session asks for the most recent
// HISTORY_PAGE_SIZE records — the server default stays at the legacy full page
// for callers that do not page (v1 dashboard query, archived previews) — and
// older pages are fetched on demand with a `before_id` cursor (ChatUI
// scroll-to-top or the "load older" button).
const HISTORY_PAGE_SIZE = 50;

/** Per-session history pagination state (window + exclusive cursor). */
type HistoryPaging = {
  hasMore: boolean;
  oldestLoadedId: number | null;
  totalMessages: number;
  loadingOlder: boolean;
  /**
   * 2026-10-22 (elecvoid243): automatic top-of-list paging is armed for one
   * page per entry into the top band. The scroll handler disarms it when it
   * starts a page and re-arms it once the reader has moved clear of the band,
   * so a held scrollbar thumb (which reports `scrollTop < 80` for every
   * pointer-driven event) cannot chain page after page. `undefined` means
   * armed: every window opens armed and an explicit retry never disarms.
   */
  autoLoadArmed?: boolean;
  /**
   * Message of the last failed "load older" request. While set, the automatic
   * scroll-to-top loading stays paused (retrying a broken request on every
   * scroll event would spin); the explicit button clears it and retries.
   */
  error: string | null;
};

/** One user message in the session-wide marker index (ChatUI strip). */
type SessionMarker = {
  id: number;
  index: number;
  snippet: string;
  replySnippet: string;
  /** True when the message was inherited from a branched source session
   * (older than the branch_info divider) — hidden while
   * the inherited history is collapsed. */
  inherited: boolean;
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
 * provider supports it. Any other string is passed through verbatim (values
 * differ per model / engine, e.g. "max" on deepseek-v4 official API, "xhigh"
 * on llama.cpp-hosted Qwen, or a bare number such as "37" from the ChatUI
 * slider for models that accept a continuous range). OpenAI-family providers
 * receive it as `reasoning_effort`, or as `chat_template_kwargs.reasoning_effort`
 * when the provider config declares that channel (vLLM-style engines only
 * accept continuous values there).
 */
export type ThinkingEffort = "auto" | "off" | (string & {});

export function buildChatRequestFlags(enableStreaming = true) {
  return {
    enable_inline_genui: true,
    enable_default_system_prompt: true,
    enable_streaming: enableStreaming,
  };
}

/**
 * Resolve the chat transport mode for this page load.
 *
 * Query param wins over the stored preference so `?stream=legacy` can
 * diagnose a broken unified stream without editing localStorage. Tests
 * default to legacy so the existing per-send SSE specs keep their
 * transport; unified specs opt in explicitly.
 *
 * Args:
 *   search: Location search string; defaults to the current page URL. Kept
 *     injectable because jsdom cannot navigate between query strings.
 *
 * Returns:
 *   "unified" for the single-stream transport, "legacy" for per-send streams.
 */
export function resolveChatStreamMode(search?: string): "unified" | "legacy" {
  try {
    const query = new URLSearchParams(search ?? window.location.search).get(
      "stream",
    );
    if (query === "legacy" || query === "unified") return query;
    const stored = localStorage.getItem("chat.streamMode");
    if (stored === "legacy" || stored === "unified") return stored;
  } catch {
    // Ignore storage/URL failures and fall through to the default.
  }
  return import.meta.env.MODE === "test" ? "legacy" : "unified";
}

/** Frames the unified stream must apply immediately, ahead of buffered text. */
const UNIFIED_CONTROL_TYPES = new Set([
  "stream_ready",
  "run_started",
  "end",
  "error",
  "interactive_choice",
  "interactive_choice_resolved",
  "goal_state_changed",
  "shell_sessions_changed",
  "chat_session_created",
  "session_state",
  "resync_required",
]);

/** Chain types that must never be text-merged with a neighbour. */
const UNIFIED_NO_MERGE_CHAINS = new Set([
  "tool_call",
  "interactive_choice",
  "context_compression",
  "reasoning_loop",
]);

export interface MessagePart {
  type: string;
  comments?: unknown;
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
  image_attachment_ids?: string[];
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
  /** Checkpoint of the running turn that consumed this user message. */
  follow_up_checkpoint_id?: string;
  agent_source?: {
    kind: "agent";
    account_id: string;
    login_name: string;
    session_id: string;
    display_name: string;
    remote?: boolean;
    connection_name?: string | null;
    instance_name?: string | null;
  };
  reasoning?: string;
  isLoading?: boolean;
  agentStats?: any;
  /** Aggregated end-of-turn file change summary (net diff per file),
   * delivered via the `file_changes` stream event and persisted as
   * `content.file_changes`. */
  fileChangeSummary?: FileChangeSummaryFile[];
  fileChangeRunId?: string;
  fileChangeSourceUnavailable?: boolean;
  /** Plugin context injections observed on `on_llm_request`, delivered via
   * the `llm_request_injections` stream event and persisted as
   * `content.llm_request_injections`. */
  llmRequestInjections?: LlmRequestInjections | null;
  /** Developer-mode LLM request snapshots delivered via the live
   * `llm_request_snapshots` stream event. Ephemeral: never persisted and
   * never rebuilt from history. */
  llmRequestSnapshots?: LlmRequestSnapshotEnvelope[];
  refs?: any;
}

export interface MessageDisplayBlock {
  kind: "thinking" | "content";
  parts: MessagePart[];
}

export interface ChatRecord {
  isStreaming?: boolean;
  id?: string | number;
  content: ChatContent;
  created_at?: string;
  sender_id?: string;
  sender_name?: string;
  llm_checkpoint_id?: string | null;
  threads?: ChatThread[];
}

export interface MessagePreview {
  preview: string;
  replyPreview: string;
  replyLoading: boolean;
  messageIndex: number;
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
  segment_id?: string;
  history_record_id?: number | null;
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
  visibleRecords?: ChatRecord[];
  followUpCaptured?: boolean;
  precedingPlainText?: string;
}

interface SendMessageStreamOptions {
  configId?: string;
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
  configId?: string;
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
  accountRole?: string;
  onError?: (message: string, sessionId: string) => void;
  currentSessionId: Ref<string>;
  onSessionsChanged?: (projectId?: string | null) => Promise<void> | void;
  onStreamUpdate?: (sessionId: string) => void;
  /** Live tool calls only; persisted history and result events never fire this hook. */
  onToolCall?: (sessionId: string, tool: ToolCall, runId: string) => void;
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
   * Fired when the agent runner compressed the request context
   * (`chain_type: "context_compression"`).
   *
   * The notice is live-only: it is not persisted anywhere, so the consumer
   * (the chat page) owns presentation — the page shows it as a transient
   * toast. Args:
   *   sessionId: Bare conversation id the run belongs to.
   *   notice: Parsed notice with the strategy and the token delta.
   */
  onContextCompression?: (
    sessionId: string,
    notice: ContextCompressionNotice,
  ) => void;
  /**
   * Fired when the kernel goal loop pushes a `goal_state_changed` event over
   * the system stream. The payload is authoritative — replace the cached
   * state for the session.
   */
  onGoalStateChanged?: (
    sessionId: string,
    goal: SessionGoalState | null,
  ) => void;
  /**
   * Fired when a managed shell session lifecycle transition (create / exit /
   * removal) pushes a `shell_sessions_changed` snapshot over the system
   * stream. The payload is authoritative — replace the cached list for the
   * session.
   */
  onShellSessionsChanged?: (
    sessionId: string,
    sessions: ShellSessionListItem[],
  ) => void;
}

// Content-bearing payload types that should wake the chat view's tail-follow
// and marker refresh. Control frames and persistence-only acknowledgements are
// excluded so they never trigger a render pass on their own.
const STREAM_REVISION_TYPES = new Set([
  "plain",
  "complete",
  "tool_call",
  "agent_stats",
  "file_changes",
  "assistant_file_comments",
  "llm_request_injections",
  "message_saved",
  "run_snapshot",
  "message_segment",
  "interactive_choice",
  "interactive_choice_resolved",
]);

export function useMessages(options: UseMessagesOptions) {
  const accountRevision = accountSessionRevision.value;
  const loadingMessages = ref(false);
  const sending = ref(false);
  const messagesBySession = reactive<Record<string, ChatRecord[]>>({});
  // Per-session content revision, bumped once per content-bearing payload.
  // Chat.vue follows the tail from this signal instead of deep-watching the
  // whole message array on every token.
  const streamRevisionBySession = reactive<Record<string, number>>({});
  const loadedSessions = reactive<Record<string, boolean>>({});
  const activeConnections = reactive<Record<string, ActiveConnection>>({});
  const unifiedMode = resolveChatStreamMode() === "unified";
  const unifiedSeqBySession: Record<string, number> = {};
  let unifiedAbort: AbortController | null = null;
  let unifiedStreamStarted = false;
  let unifiedEpoch = "";
  let unifiedReconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let unifiedReconnectAttempt = 0;
  let unifiedStreamStopped = false;
  const chatWebSockets: Record<string, WebSocket> = {};
  const closingChatWebSockets = new WeakSet<WebSocket>();
  const attachmentBlobCache = new Map<string, Promise<string>>();
  const sessionProjects = reactive<Record<string, ChatSessionProject | null>>(
    {},
  );
  // 2026-08-13: archived flag per loaded session, from the getSession
  // payload. The ChatUI renders archived sessions in read-only mode.
  const sessionArchivedFlags = reactive<Record<string, boolean>>({});
  const sessionDetails = reactive<Record<string, Session>>({});
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
  const streamingBotRecords = computed(() => {
    const live = new Set<ChatRecord>();
    const sessionId = options.currentSessionId.value;
    if (!sessionId) return live;
    for (const connection of Object.values(activeConnections)) {
      if (
        connection.sessionId === sessionId &&
        !connection.completed &&
        connection.botRecord
      ) {
        live.add(connection.botRecord);
      }
    }
    for (const record of Object.values(
      systemStreamState.liveBySession[sessionId] || {},
    )) {
      live.add(record);
    }
    return live;
  });

  // Pair loaded replies with their questions so the gutter previews stay live
  // while streaming. History-only questions use the server's marker previews.
  const activeMessagePreviews = computed(() => {
    const previews = new Map<string, MessagePreview>();
    let turn: MessagePreview | null = null;
    const sessionId = options.currentSessionId.value;
    const offset = sessionId ? historyOffsetBySession[sessionId] || 0 : 0;
    for (let i = 0; i < activeMessages.value.length; i++) {
      const message = activeMessages.value[i];
      const type = messageContent(message).type;
      if (type === "branch_info") turn = null;
      if (type !== "user" && type !== "bot") continue;
      const text = messageParts(message)
        .filter(
          (part) => part.type === "plain" && typeof part.text === "string",
        )
        .map((part) => part.text)
        .join("")
        .replace(/\s+/g, " ")
        .trim();
      if (type === "user") {
        turn = null;
        if (message.id != null) {
          turn = {
            preview: text.slice(0, 160),
            replyPreview: "",
            replyLoading: false,
            messageIndex: offset + i,
          };
          previews.set(String(message.id), turn);
        }
      } else if (turn) {
        if (text)
          turn.replyPreview = `${turn.replyPreview} ${text}`
            .trim()
            .slice(0, 160);
        turn.replyLoading ||= Boolean(
          messageContent(message).isLoading || isMessageStreaming(message, i),
        );
      }
    }
    return previews;
  });

  watch(
    accountSessionRevision,
    () => {
      cleanupConnections();
      for (const state of [
        messagesBySession,
        loadedSessions,
        sessionProjects,
        sessionDetails,
        historyPagingBySession,
        historyOffsetBySession,
        sessionMarkersBySession,
      ]) {
        for (const key of Object.keys(state)) delete state[key];
      }
    },
    { flush: "sync" },
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
    if (unifiedMode || !sessionId || systemConnections[sessionId]) return;
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
              const input = payload.data?.input_message;
              const records = (messagesBySession[sessionId] ||= []);
              if (
                input &&
                !records.some((r) => String(r.id) === String(input.id))
              ) {
                records.push(normalizeHistoryRecord(input));
              }
              attachLiveRun(sessionId, String(payload.message_id));
              options.onStreamUpdate?.(sessionId);
              return;
            }
            if (payload?.type === "chat_session_created") {
              void options.onSessionsChanged?.(
                payload.data?.project_id ?? null,
              );
              return;
            }
            if (payload?.type === "goal_state_changed") {
              const data = payload.data as
                | { goal?: SessionGoalState | null }
                | undefined;
              options.onGoalStateChanged?.(sessionId, data?.goal ?? null);
              return;
            }
            if (payload?.type === "shell_sessions_changed") {
              const data = payload.data as
                | { sessions?: ShellSessionListItem[] }
                | undefined;
              options.onShellSessionsChanged?.(sessionId, data?.sessions ?? []);
              return;
            }
            // Author: elecvoid243
            // Date: 2026-10-08
            // Context compression notice on the system stream (goal-loop /
            // collab orphan turns). Handled inline like the session-scoped
            // events above so the wire JSON never reaches the leaf's
            // plain-text fall-through.
            if (payload?.chain_type === "context_compression") {
              const notice = parseContextCompressionNotice(payload.data);
              if (notice) {
                options.onContextCompression?.(sessionId, notice);
              }
              return;
            }
            // LLM request injection traces are primary-stream telemetry: an
            // orphan turn must not render the wire JSON as chat text.
            if (payload?.chain_type === "llm_request_injections") {
              return;
            }
            // Developer-mode snapshots are primary-stream telemetry too; an
            // orphan turn must not render the envelope JSON as chat text.
            if (payload?.chain_type === "llm_request_snapshots") {
              return;
            }
            messagesBySession[sessionId] = messagesBySession[sessionId] || [];
            const consumed = processSystemPayload(
              systemStreamState,
              sessionId,
              messagesBySession[sessionId],
              payload,
            );
            if (consumed) {
              bumpStreamRevision(sessionId);
              options.onStreamUpdate?.(sessionId);
            }
          });
        } catch (error) {
          if (
            abort.signal.aborted ||
            accountRevision !== accountSessionRevision.value
          )
            return;
          console.error("System event stream failed:", error);
        }
        if (
          abort.signal.aborted ||
          accountRevision !== accountSessionRevision.value
        )
          return;
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

  /**
   * Run ids currently streaming per session.
   *
   * Unified mode receives every owned run on one stream, including sessions
   * this tab has not opened. The sidebar must still show those sessions as
   * running, so the state cannot be derived from `activeConnections` alone.
   */
  const runningBySession = reactive<Record<string, string[]>>({});

  function markRunRunning(sessionId: string, runId: string) {
    const runs =
      runningBySession[sessionId] || (runningBySession[sessionId] = []);
    if (!runs.includes(runId)) runs.push(runId);
  }

  function markRunEnded(sessionId: string, runId: string) {
    const runs = runningBySession[sessionId];
    if (!runs) return;
    const index = runs.indexOf(runId);
    if (index >= 0) runs.splice(index, 1);
    if (!runs.length) delete runningBySession[sessionId];
  }

  function isSessionRunning(sessionId: string) {
    if (unifiedMode) {
      return (runningBySession[sessionId]?.length || 0) > 0;
    }
    return Object.values(activeConnections).some(
      (connection) =>
        connection.sessionId === sessionId && !connection.completed,
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
      // The newest window is opt-in: the server keeps the legacy full page for
      // callers that do not page, so the ChatUI asks for its window explicitly.
      const response = await chatApi.getSession(sessionId, {
        limit: HISTORY_PAGE_SIZE,
      });
      assertCurrentAccount(accountRevision);
      if (response.data?.status === "error")
        throw new Error(response.data?.message || "Resource is unavailable");
      const payload = response.data?.data || {};
      if (payload.session?.session_id === sessionId) {
        sessionDetails[sessionId] = {
          ...payload.session,
          created_at: payload.session.created_at ?? "",
          updated_at: payload.session.updated_at ?? "",
          branches: [],
          branch_source: null,
        };
      }
      const history = payload.history || [];
      const records: ChatRecord[] = history.map(normalizeHistoryRecord);
      attachThreads(records, payload.threads || []);
      await resolveRecordMedia(records);
      assertCurrentAccount(accountRevision);
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
        autoLoadArmed: true,
        error: null,
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
      // Re-append anything the snapshot cannot cover so no live message is
      // lost. System-stream records (`system-${run_id}`, goal-loop / collab
      // turns) are special: an in-flight orphan turn is not in the snapshot
      // (it is persisted only on completion) and is re-seeded by the freshly
      // re-subscribed system stream's run_snapshot — keep it. A finished one
      // is superseded by the persisted record now in the snapshot — drop the
      // frozen partial, otherwise both would render.
      //
      // Windowed history: the snapshot only covers the newest window, so
      // "absent from the snapshot" no longer implies "live". Persisted
      // records at or below the window are either duplicated by it or belong
      // to older pages loaded before this reload — both are dropped, the
      // window resets together with the paging state above. Records that
      // postdate the snapshot (persisted while the fetch was in flight, so
      // newer than everything in it) are kept.
      const existing = messagesBySession[sessionId] || [];
      // A record a live connection still owns is in flight by definition,
      // whatever id it carries. `message_saved` rewrites a streaming record's
      // id to the persisted numeric one (see processStreamPayload), so the
      // snapshot can hold a stale copy of the very same row: the live object is
      // the one the stream keeps writing into, so it replaces the snapshot copy
      // below — leaving both would render two bubbles with one id.
      // Scoped to ids actually present in `existing`: on a fresh load the
      // connection is created after this merge runs (restoreNextActiveRun), and
      // then the snapshot's copy is all there is to show.
      const ownedIds = new Set<string>();
      for (const connection of Object.values(activeConnections)) {
        if (connection.sessionId !== sessionId) {
          continue;
        }
        // Keep user handles too: their save acknowledgement can arrive after
        // a history refresh and must still update the rendered record.
        for (const record of [connection.botRecord, connection.userRecord]) {
          if (!record) continue;
          const ownedId = String(record.id ?? "");
          if (existing.some((r) => String(r.id ?? "") === ownedId)) {
            ownedIds.add(ownedId);
          }
        }
      }
      const snapshot = records;
      messagesBySession[sessionId] = snapshot;
      if (existing.length) {
        const historyIds = new Set(
          snapshot.map((r: ChatRecord) => String(r.id)),
        );
        const activeOrphanIds = new Set(
          (Array.isArray(payload.active_runs) ? payload.active_runs : [])
            .filter((r: ActiveChatRun) => !r.llm_checkpoint_id)
            .map((r: ActiveChatRun) => `system-${r.run_id}`),
        );
        const snapshotNumericIds = snapshot
          .map((r: ChatRecord) => Number(r.id))
          .filter((n) => Number.isFinite(n));
        const newestSnapshotId = snapshotNumericIds.length
          ? Math.max(...snapshotNumericIds)
          : null;
        const live = existing.filter((r: ChatRecord) => {
          const recordId = String(r.id || "");
          if (ownedIds.has(recordId)) return true;
          if (recordId.startsWith("system-")) {
            return activeOrphanIds.has(recordId);
          }
          if (!recordId) return true;
          const numericId = Number(recordId);
          if (Number.isFinite(numericId)) {
            return newestSnapshotId === null || numericId > newestSnapshotId;
          }
          // Temp ids (`local-*`, `active-run-*`, ...) are in-flight bubbles
          // the snapshot cannot contain yet.
          return !historyIds.has(recordId);
        });
        if (live.length) {
          // Keep persisted history order and anchor live rows to their existing
          // neighbors. Browser creation times and server save times can differ.
          const liveRecords = new Set(live);
          const merged = [...snapshot];
          let insertBefore = merged.length;
          for (let i = existing.length - 1; i >= 0; i -= 1) {
            const record = existing[i];
            const snapshotIndex = merged.findIndex(
              (candidate) => String(candidate.id) === String(record.id),
            );
            if (snapshotIndex >= 0) {
              if (liveRecords.has(record)) merged[snapshotIndex] = record;
              insertBefore = snapshotIndex;
            } else if (liveRecords.has(record)) {
              merged.splice(insertBefore, 0, record);
            }
          }
          messagesBySession[sessionId] = merged;
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
      if (accountRevision !== accountSessionRevision.value) return;
      options.onError?.(String(error), sessionId);
      if (accountRevision !== accountSessionRevision.value) return;
      console.error("Failed to load session messages:", error);
      messagesBySession[sessionId] = messagesBySession[sessionId] || [];
    } finally {
      if (showLoading) loadingMessages.value = false;
    }
  }

  /**
   * Prepends one page of older history for a session (before_id cursor).
   * The caller can capture the current viewport immediately before insertion.
   *
   * Args:
   *   sessionId: Session whose older records should be loaded.
   *   options: Retry a failed page when requested explicitly, and optionally
   *     capture the viewport with beforePrepend immediately before insertion,
   *     after fetching and resolving media. Automatic retries remain paused
   *     after a failure.
   */
  async function loadOlderMessages(
    sessionId: string,
    options: { retry?: boolean; beforePrepend?: () => void } = {},
  ) {
    const paging = historyPagingBySession[sessionId];
    if (!paging || paging.loadingOlder || !paging.hasMore) return;
    if (paging.error && !options.retry) return;
    paging.loadingOlder = true;
    paging.error = null;
    try {
      const response = await chatApi.getHistory(sessionId, {
        before_id: paging.oldestLoadedId ?? undefined,
        limit: HISTORY_PAGE_SIZE,
      });
      assertCurrentAccount(accountRevision);
      if (response.data?.status === "error")
        throw new Error(response.data?.message || "Resource is unavailable");
      const payload = response.data?.data || {};
      const olderRecords: ChatRecord[] = (payload.history || []).map(
        normalizeHistoryRecord,
      );
      attachThreads(olderRecords, payload.threads || []);
      await resolveRecordMedia(olderRecords);
      assertCurrentAccount(accountRevision);
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
        options.beforePrepend?.();
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
      if (accountRevision !== accountSessionRevision.value) return;
      console.error("Failed to load older messages:", error);
      // Pause automatic loading until the user retries explicitly.
      paging.error = String(
        (error as Error)?.message ?? error ?? "unknown error",
      );
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
      assertCurrentAccount(accountRevision);
      if (response.data?.status === "error")
        throw new Error(response.data?.message || "Resource is unavailable");
      const payload = response.data?.data || {};
      sessionMarkersBySession[sessionId] = {
        markers: (payload.markers || []).map((m: any) => ({
          id: Number(m.id),
          index: Number(m.index),
          snippet: String(m.snippet || ""),
          replySnippet: String(m.reply_snippet || ""),
          inherited: Boolean(m.inherited),
        })),
        totalMessages: Number(payload.total_messages || 0),
        truncated: Boolean(payload.truncated),
        loaded: true,
        loading: false,
      };
    } catch (error) {
      if (accountRevision !== accountSessionRevision.value) return;
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
    // An incoming Agent request may be consumed by the visible run as a
    // follow-up. Keep its receipt off the page until it produces real output.
    const anotherVisibleRun =
      hasLiveSystemRecord(sessionId) ||
      Object.values(activeConnections).some(
        (connection) =>
          connection.sessionId === sessionId &&
          connection.botVisible !== false &&
          !connection.completed,
      );
    if (!anotherVisibleRun) messages.push(botRecord);
    startResumeStream(sessionId, runId, botRecord);
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
    if (!run) return;
    if (unifiedMode) {
      if (activeConnections[run.run_id]) return;
    } else if (isSessionRunning(sessionId)) {
      return;
    }

    const checkpointId = run.llm_checkpoint_id || null;
    const records = (messagesBySession[sessionId] || []).filter((record) => {
      return !(
        checkpointId &&
        (run.history_record_id != null
          ? String(record.id) === String(run.history_record_id)
          : !run.segment_id && record.llm_checkpoint_id === checkpointId) &&
        messageContent(record).type === "bot"
      );
    });
    const botRecord = normalizeHistoryRecord({
      id: run.history_record_id ?? `active-run-${run.run_id}`,
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
    if (unifiedMode) {
      activeConnections[run.run_id] = {
        sessionId,
        messageId: run.run_id,
        runId: run.run_id,
        transport: "sse",
        botRecord: reactiveBotRecord,
        botVisible: true,
        visibleRecords: restoredRecords,
        completed: false,
      };
      markRunRunning(sessionId, run.run_id);
      return;
    }
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
    sessionMessages.push(userRecord);
    void resolveRecordMedia([userRecord]);
    // The backend carries work-only output across the user boundary. Keep
    // visible replies in place until the server sends the next segment.
    const userIndex = sessionMessages.indexOf(userRecord);
    for (let index = 0; index < userIndex; index += 1) {
      const candidate = sessionMessages[index];
      // "still streaming" is the live connection, not `isLoading` — the latter
      // clears on the first token, long before the turn ends.
      const streaming = Object.values(activeConnections).some(
        (connection) =>
          connection.botRecord === candidate && !connection.completed,
      );
      if (
        isUserMessage(candidate) ||
        !streaming ||
        messageParts(candidate).some(
          (part) => part.type !== "think" && part.type !== "tool_call",
        )
      ) {
        continue;
      }
      sessionMessages.splice(index, 1);
      sessionMessages.splice(
        sessionMessages.indexOf(userRecord) + 1,
        0,
        candidate,
      );
      break;
    }
    if (!isSessionRunning(sessionId)) sessionMessages.push(botRecord);

    return {
      userRecord,
      botRecord,
    };
  }

  function settleUnifiedConnection(connection: ActiveConnection) {
    // Retire the running marker before the `completed` guard: a captured
    // follow-up marks its connection completed in the `follow_up_captured`
    // branch, yet the receipt's terminal frame still arrives afterwards.
    // Skipping markRunEnded there left the receipt's run id in
    // runningBySession forever (session spun until the next reconnect).
    markRunEnded(connection.sessionId, connection.messageId);
    if (connection.completed) return;
    connection.completed = true;
    if (activeConnections[connection.messageId] === connection) {
      delete activeConnections[connection.messageId];
    }
    void options.onSessionsChanged?.();
    void loadSessionMarkers(connection.sessionId);
    options.onStreamEnd?.(connection.sessionId);
  }

  /**
   * Materialize a background run in a session this tab has already opened.
   *
   * Returns null when the session is not loaded: the unified stream still
   * tracks its running state, but token frames must not create records for a
   * conversation the user has never opened.
   */
  function ensureUnifiedRunConnection(
    sessionId: string,
    messageId: string,
    input: any,
  ): ActiveConnection | null {
    const existing = activeConnections[messageId];
    if (existing?.sessionId === sessionId) return existing;
    const records = messagesBySession[sessionId];
    if (!records) return null;
    let userRecord: ChatRecord | undefined;
    if (input?.id != null) {
      userRecord = records.find(
        (record) => String(record.id) === String(input.id),
      );
      if (!userRecord) {
        userRecord = normalizeHistoryRecord(input);
        records.push(userRecord);
      }
    }
    let botRecord = records.find(
      (record) => String(record.id) === `local-bot-${messageId}`,
    );
    if (!botRecord) {
      botRecord = reactive<ChatRecord>({
        id: `local-bot-${messageId}`,
        created_at: new Date().toISOString(),
        content: {
          type: "bot",
          message: [],
          reasoning: "",
          isLoading: true,
        },
      });
      const userIndex = userRecord ? records.indexOf(userRecord) : -1;
      if (userIndex >= 0) records.splice(userIndex + 1, 0, botRecord);
      else records.push(botRecord);
    }
    const connection: ActiveConnection = {
      sessionId,
      messageId,
      runId: messageId,
      transport: "sse",
      botRecord,
      userRecord,
      botVisible: true,
      visibleRecords: records,
      completed: false,
    };
    activeConnections[messageId] = connection;
    return connection;
  }

  /**
   * Apply control/attention frames for sessions that are not loaded.
   *
   * Token frames are intentionally ignored for those sessions; an
   * interactive choice is the one control event that must still reach the
   * store so the sidebar/notification can surface it.
   */
  function handleUnifiedControlFrame(
    sessionId: string,
    payload: any,
    msgType: string,
  ) {
    const scratch = () =>
      reactive<ChatRecord>({
        id: `local-control-${sessionId}`,
        created_at: new Date().toISOString(),
        content: {
          type: "bot",
          message: [],
          reasoning: "",
          isLoading: false,
        },
      });
    if (msgType === "interactive_choice") {
      if (applyInteractiveChoiceSse(sessionId, scratch(), payload)) {
        options.onInteractiveChoice?.(sessionId);
      }
      return;
    }
    if (msgType === "interactive_choice_resolved") {
      applyInteractiveChoiceResolved(sessionId, payload);
      return;
    }
    if (msgType === "plain" && payload.chain_type === "interactive_choice") {
      const inner = parseJsonSafe(payload.data);
      if (!inner || typeof inner !== "object") return;
      if (
        applyInteractiveChoiceSse(sessionId, scratch(), {
          type: "interactive_choice",
          data: inner,
        })
      ) {
        options.onInteractiveChoice?.(sessionId);
      }
    }
  }

  async function resolveLargeUnifiedFrame(
    payload: any,
    abort?: AbortController,
  ): Promise<boolean> {
    const msgType = String(payload?.type || payload?.t || "");
    if (msgType === "run_snapshot") {
      const data = payload.data;
      const ref = data?.content?.snapshot_ref ?? data?.snapshot_ref;
      if (!ref?.run_id) return true;
      try {
        const response = await fetchWithAuth(
          chatApi.runSnapshotUrl(String(ref.run_id)),
          { signal: abort?.signal },
        );
        const body = await response.json().catch(() => null);
        const content = body?.data?.content;
        if (!response.ok || body?.status === "error" || !content) {
          console.error(
            "Failed to load unified snapshot:",
            body?.message || response.status,
          );
          return false;
        }
        payload.data = { ...data, content };
        return true;
      } catch (error) {
        console.error("Failed to load unified snapshot:", error);
        return false;
      }
    }
    if (msgType === "complete") {
      const ref = payload?.data?.text_ref;
      if (!ref?.run_id) return true;
      try {
        const response = await fetchWithAuth(
          chatApi.runSnapshotUrl(String(ref.run_id)),
          { signal: abort?.signal },
        );
        const body = await response.json().catch(() => null);
        const content = body?.data?.content;
        if (!response.ok || body?.status === "error" || !content) {
          console.error(
            "Failed to load unified final text:",
            body?.message || response.status,
          );
          return false;
        }
        payload.data = (Array.isArray(content.message) ? content.message : [])
          .filter((part: any) => part?.type === "plain")
          .map((part: any) => String(part?.text || ""))
          .join("");
        return true;
      } catch (error) {
        console.error("Failed to load unified final text:", error);
        return false;
      }
    }
    return true;
  }

  async function handleUnifiedFrame(payload: any, abort?: AbortController) {
    if (!payload || typeof payload !== "object") return;
    const sessionId = String(payload.session_id || "");
    if (!sessionId) return;
    const msgType = String(payload.type || payload.t || "");
    if (msgType === "session_state") {
      const data = payload.data || {};
      options.onGoalStateChanged?.(sessionId, data.goal ?? null);
      options.onShellSessionsChanged?.(sessionId, data.sessions ?? []);
      return;
    }
    if (msgType === "resync_required") {
      scheduleUnifiedReconnect(100);
      return;
    }
    // Session-scoped events carry no run identity. Dispatch them before the
    // message-id gate, mirroring the legacy system-stream handler, otherwise
    // live shell/goal/session-list pushes are dropped until a refresh.
    if (msgType === "chat_session_created") {
      void options.onSessionsChanged?.(payload.data?.project_id ?? null);
      return;
    }
    if (msgType === "goal_state_changed") {
      options.onGoalStateChanged?.(sessionId, payload.data?.goal ?? null);
      return;
    }
    if (msgType === "shell_sessions_changed") {
      options.onShellSessionsChanged?.(sessionId, payload.data?.sessions ?? []);
      return;
    }
    const messageId = String(payload.message_id || payload.data?.run_id || "");
    if (!messageId) return;

    if (msgType === "run_started") {
      markRunRunning(sessionId, messageId);
      let connection: ActiveConnection | null =
        activeConnections[messageId] ?? null;
      if (!connection || connection.sessionId !== sessionId) {
        connection = ensureUnifiedRunConnection(
          sessionId,
          messageId,
          payload.data?.input_message,
        );
      }
      if (!connection) return;
      connection.runId = messageId;
      const input = payload.data?.input_message;
      if (input?.id != null && connection.userRecord) {
        connection.userRecord.id = input.id;
        if (input.created_at) {
          connection.userRecord.created_at = input.created_at;
        }
      }
      return;
    }

    if (!(await resolveLargeUnifiedFrame(payload, abort))) return;
    if (abort?.signal.aborted) return;

    const connection = activeConnections[messageId];
    if (!connection?.botRecord || connection.sessionId !== sessionId) {
      handleUnifiedControlFrame(sessionId, payload, msgType);
      if (msgType === "end" || msgType === "error") {
        markRunEnded(sessionId, messageId);
        void options.onSessionsChanged?.();
        options.onStreamEnd?.(sessionId);
      }
      return;
    }
    const botRecord = connection.botRecord;
    processStreamPayload(botRecord, payload, connection.userRecord, connection);
    options.onStreamUpdate?.(sessionId);
    if (msgType === "end" || msgType === "error") {
      settleUnifiedConnection(connection);
    }
  }

  function scheduleUnifiedReconnect(delay = -1) {
    if (!unifiedMode || unifiedStreamStopped) return;
    if (unifiedReconnectTimer) return;
    const wait =
      delay >= 0 ? delay : Math.min(500 * 2 ** unifiedReconnectAttempt, 10000);
    unifiedReconnectAttempt += 1;
    unifiedReconnectTimer = setTimeout(() => {
      unifiedReconnectTimer = null;
      if (unifiedStreamStopped) return;
      unifiedAbort?.abort();
      unifiedAbort = null;
      unifiedStreamStarted = false;
      startUnifiedStream();
    }, wait);
  }

  function startUnifiedStream() {
    if (!unifiedMode || unifiedStreamStarted || unifiedStreamStopped) return;
    unifiedStreamStarted = true;
    const abort = new AbortController();
    unifiedAbort = abort;
    void (async () => {
      let frameChain: Promise<void> = Promise.resolve();
      // Token frames are buffered for one animation frame and merged before
      // they reach the message list. Without this the renderer re-parses the
      // growing markdown once per SSE frame, which is O(n²) over a reply.
      // Control frames flush the buffer first (order matters) and then run
      // immediately so stop/choice/end never wait behind text.
      let pendingFrames: any[] = [];
      let flushRaf: number | null = null;
      let flushTimer: ReturnType<typeof setTimeout> | null = null;
      const cancelFlush = () => {
        if (flushRaf != null) {
          cancelAnimationFrame(flushRaf);
          flushRaf = null;
        }
        if (flushTimer != null) {
          clearTimeout(flushTimer);
          flushTimer = null;
        }
      };
      const chainFrame = (payload: any) => {
        frameChain = frameChain
          .then(() => {
            if (abort.signal.aborted) return;
            return handleUnifiedFrame(payload, abort);
          })
          .catch((error) => {
            console.error("Unified chat frame failed:", error);
          });
      };
      const flushFrames = () => {
        cancelFlush();
        if (!pendingFrames.length) return;
        const batch = pendingFrames;
        pendingFrames = [];
        const merged: any[] = [];
        for (const payload of batch) {
          const last = merged[merged.length - 1];
          if (
            last &&
            payload?.type === "plain" &&
            last.type === "plain" &&
            payload.session_id === last.session_id &&
            payload.message_id === last.message_id &&
            (payload.chain_type || "") === (last.chain_type || "") &&
            typeof payload.data === "string" &&
            typeof last.data === "string" &&
            !UNIFIED_NO_MERGE_CHAINS.has(payload.chain_type)
          ) {
            last.data += payload.data;
            last.seq = payload.seq;
            continue;
          }
          merged.push({ ...payload });
        }
        for (const payload of merged) chainFrame(payload);
      };
      const scheduleFlush = () => {
        if (flushRaf != null || flushTimer != null) return;
        if (typeof requestAnimationFrame === "function") {
          flushRaf = requestAnimationFrame(() => {
            flushRaf = null;
            flushFrames();
          });
        }
        // Background tabs never run rAF; keep the stream flowing anyway.
        flushTimer = setTimeout(() => {
          flushTimer = null;
          flushFrames();
        }, 100);
      };
      try {
        const response = await fetchWithAuth(chatApi.unifiedStreamUrl(), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            epoch: unifiedEpoch,
            cursors: unifiedSeqBySession,
          }),
          signal: abort.signal,
        });
        if (!response.ok || !response.body) {
          throw new Error(`Unified stream failed: ${response.status}`);
        }
        await readSseStream(response.body, (payload) => {
          if (payload?.type === "stream_ready") {
            const epoch = String(payload.epoch || "");
            if (unifiedEpoch && epoch && epoch !== unifiedEpoch) {
              Object.keys(unifiedSeqBySession).forEach((sessionId) => {
                delete unifiedSeqBySession[sessionId];
              });
            }
            unifiedEpoch = epoch;
            unifiedReconnectAttempt = 0;
            // A reconnect is authoritative: the server re-announces every
            // live run right after this frame. Anything still marked running
            // but not re-announced is a run whose terminal frame was lost in
            // the disconnect window; dropping the markers here keeps the
            // sidebar from spinning forever.
            for (const sessionId of Object.keys(runningBySession)) {
              delete runningBySession[sessionId];
            }
            return;
          }
          if (payload?.session_id && payload?.seq != null) {
            const sessionId = String(payload.session_id);
            const seq = Number(payload.seq);
            if (seq > (unifiedSeqBySession[sessionId] || 0)) {
              unifiedSeqBySession[sessionId] = seq;
            }
          }
          if (UNIFIED_CONTROL_TYPES.has(String(payload?.type || ""))) {
            flushFrames();
            chainFrame(payload);
            return;
          }
          pendingFrames.push(payload);
          scheduleFlush();
        });
        flushFrames();
        await frameChain;
      } catch (error) {
        if (abort.signal.aborted) return;
        console.error("Unified chat stream failed:", error);
      } finally {
        flushFrames();
        await frameChain.catch(() => {});
        if (unifiedAbort !== abort) return;
        unifiedAbort = null;
        if (!unifiedStreamStopped) {
          unifiedStreamStarted = false;
          scheduleUnifiedReconnect();
        }
      }
    })();
  }

  function stopUnifiedStream() {
    unifiedStreamStopped = true;
    if (unifiedReconnectTimer) {
      clearTimeout(unifiedReconnectTimer);
      unifiedReconnectTimer = null;
    }
    unifiedAbort?.abort();
    unifiedAbort = null;
    unifiedStreamStarted = false;
  }

  function startUnifiedSend({
    configId,
    sessionId,
    messageId,
    parts,
    enableStreaming = true,
    selectedProvider = "",
    selectedModel = "",
    thinkingEffort,
    botRecord,
    userRecord,
    extraPayload = {},
  }: Omit<
    SendMessageStreamOptions,
    "transport" | "skipUserHistory" | "llmCheckpointId"
  > & { extraPayload?: Record<string, unknown> }) {
    const abort = new AbortController();
    const connection: ActiveConnection = {
      sessionId,
      messageId,
      transport: "sse",
      abort,
      botRecord,
      userRecord,
      botVisible: messagesBySession[sessionId]?.includes(botRecord) ?? false,
    };
    activeConnections[messageId] = connection;
    markRunRunning(sessionId, messageId);

    const payload = {
      mode: "send",
      session_id: sessionId,
      message_id: messageId,
      content: parts.map(partToPayload),
      config_id: configId,
      selected_provider: selectedProvider,
      selected_model: selectedModel,
      thinking_effort: thinkingEffort,
      ...(options.accountRole === "user"
        ? {}
        : { flags: buildChatRequestFlags(enableStreaming) }),
      ...extraPayload,
    };

    fetchWithAuth(chatApi.sendMessageUrl(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: abort.signal,
    })
      .then(async (response) => {
        const body = await response.json().catch(() => null);
        if (!response.ok || body?.status === "error") {
          throw new Error(
            body?.message ||
              body?.data?.message ||
              `Send failed: ${response.status}`,
          );
        }
        const data = body?.data || {};
        if (data.run_id) connection.runId = String(data.run_id);
        if (data.input_message_id != null && userRecord) {
          userRecord.id = data.input_message_id;
        }
      })
      .catch((error) => {
        if (
          abort.signal.aborted ||
          accountRevision !== accountSessionRevision.value
        ) {
          return;
        }
        ensureBotRecordVisible(connection);
        appendPlain(botRecord, `\n\n${String(error?.message || error)}`);
        markMessageStarted(botRecord);
        delete activeConnections[messageId];
        markRunEnded(sessionId, messageId);
        options.onError?.(String(error?.message || error), sessionId);
        options.onStreamEnd?.(sessionId);
        void options.onSessionsChanged?.();
        console.error("Unified chat send failed:", error);
      });
  }

  function sendMessageStream({
    configId,
    sessionId,
    messageId,
    parts,
    transport,
    enableStreaming = true,
    selectedProvider = "",
    selectedModel = "",
    thinkingEffort,
    botRecord,
    userRecord,
    skipUserHistory = false,
    llmCheckpointId = null,
  }: SendMessageStreamOptions) {
    if (
      unifiedMode &&
      !skipUserHistory &&
      !llmCheckpointId
    ) {
      startUnifiedSend({
        configId,
        sessionId,
        messageId,
        parts,
        enableStreaming,
        selectedProvider,
        selectedModel,
        thinkingEffort,
        botRecord,
        userRecord,
      });
      return;
    }
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
        configId,
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
      configId,
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
    assertCurrentAccount(accountRevision);
    if (response.data?.status === "error")
      throw new Error(response.data?.message || "Resource is unavailable");
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
      void loadSessionMarkers(sessionId);
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
    configId,
    sessionId,
    sourceRecord,
    enableStreaming = true,
    selectedProvider = "",
    selectedModel = "",
    thinkingEffort,
  }: ContinueEditedMessageOptions) {
    if (!sessionId) return;
    const parts = messageParts(sourceRecord).map(stripUploadOnlyFields);
    const messageId = crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    messagesBySession[sessionId] = messagesBySession[sessionId] || [];

    // Author: elecvoid243
    // Date: 2026-10-08
    // Hand the stream the record the list actually renders — the reactive
    // one — instead of the raw literal pushed into the array. Raw writes
    // bypass the set trap, so every chunk of this run mutated the data while
    // the bubble kept rendering its first frame ("思考中...", `v-if="isLoading"`)
    // until some unrelated reactive change forced a re-render.
    // `createLocalExchange` and `attachLiveRun` avoid this the same way.
    const botRecord = reactive<ChatRecord>({
      id: `local-edited-bot-${messageId}`,
      created_at: new Date().toISOString(),
      content: {
        type: "bot",
        message: [],
        reasoning: "",
        isLoading: true,
      },
    });
    messagesBySession[sessionId].push(botRecord);

    if (unifiedMode) {
      // Edited continuation over the unified endpoint: reuse the latest saved
      // user turn (skip_user_history) and resume from its checkpoint, so this
      // run's frames land on the single stream instead of a per-send one.
      startUnifiedSend({
        configId,
        sessionId,
        messageId,
        parts,
        enableStreaming,
        selectedProvider,
        selectedModel,
        thinkingEffort,
        botRecord,
        extraPayload: {
          _skip_user_history: true,
          _llm_checkpoint_id: sourceRecord.llm_checkpoint_id || null,
        },
      });
      return;
    }

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
      configId,
    );
  }

  async function regenerateMessage(
    sessionId: string,
    botRecord: ChatRecord,
    selectedProvider = "",
    selectedModel = "",
    enableStreaming = true,
    thinkingEffort: ThinkingEffort | undefined = undefined,
    configId?: string,
  ) {
    if (!sessionId || botRecord.id == null) return;
    const targetMessageId = botRecord.id;
    const originalRecord = { ...botRecord };
    let streamAccepted = false;

    botRecord.id = `local-regenerate-${Date.now()}`;
    botRecord.created_at = new Date().toISOString();
    botRecord.content = {
      type: "bot",
      message: [],
      reasoning: "",
      isLoading: true,
    };

    if (unifiedMode) {
      // Regeneration over the unified endpoint. The run id is client-chosen
      // so this optimistic record is already registered when run_started
      // arrives; otherwise the stream would materialize a second bubble.
      const runId = crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;
      botRecord.id = `local-regenerate-${runId}`;
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
      markRunRunning(sessionId, runId);
      fetchWithAuth(chatApi.sendMessageUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: "regenerate",
          run_id: runId,
          session_id: sessionId,
          message_id: targetMessageId,
          config_id: configId,
          selected_provider: selectedProvider || undefined,
          selected_model: selectedModel || undefined,
          thinking_effort: thinkingEffort,
          ...(options.accountRole === "user"
            ? {}
            : { flags: buildChatRequestFlags(enableStreaming) }),
        }),
        signal: abort.signal,
      })
        .then(async (response) => {
          const body = await response.json().catch(() => null);
          if (!response.ok || body?.status === "error") {
            throw new Error(
              body?.message || `Regenerate failed: ${response.status}`,
            );
          }
          botRecord.threads = [];
        })
        .catch((error) => {
          if (accountRevision !== accountSessionRevision.value) return;
          Object.assign(botRecord, originalRecord);
          if (!("created_at" in originalRecord)) delete botRecord.created_at;
          delete activeConnections[runId];
          markRunEnded(sessionId, runId);
          options.onError?.(
            String((error as Error)?.message || error),
            sessionId,
          );
          options.onStreamEnd?.(sessionId);
          console.error("Regenerate failed:", error);
        });
      return;
    }

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
          body: JSON.stringify(
            options.accountRole === "user"
              ? {
                  config_id: configId,
                  thinking_effort: thinkingEffort,
                  selected_provider: selectedProvider || undefined,
                  selected_model: selectedModel || undefined,
                }
              : {
                  config_id: configId,
                  selected_provider: selectedProvider,
                  selected_model: selectedModel,
                  thinking_effort: thinkingEffort,
                  flags: buildChatRequestFlags(enableStreaming),
                },
          ),
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
      streamAccepted = true;
      botRecord.threads = [];
      await readSseStream(response.body, (payload) => {
        processStreamPayload(botRecord, payload, undefined, connection);
        options.onStreamUpdate?.(sessionId);
      });
    } catch (error) {
      if (accountRevision !== accountSessionRevision.value) return;
      if (!streamAccepted) {
        Object.assign(botRecord, originalRecord);
        if (!("created_at" in originalRecord)) delete botRecord.created_at;
      }
      if (!abort.signal.aborted) {
        options.onError?.(
          String((error as Error)?.message || error),
          sessionId,
        );
        if (streamAccepted)
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
      if (accountRevision === accountSessionRevision.value) {
        await options.onSessionsChanged?.();
        void loadSessionMarkers(sessionId);
      }
      options.onStreamEnd?.(sessionId);
    }
  }

  async function stopSession(sessionId: string) {
    if (!sessionId) return;
    await chatApi.stopSession(sessionId);
  }

  function cleanupConnections() {
    stopUnifiedStream();
    Object.keys(runningBySession).forEach((sessionId) => {
      delete runningBySession[sessionId];
    });
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
      llmRequestInjections:
        parseLlmRequestInjections(
          content.llm_request_injections ?? content.llmRequestInjections,
        ) ?? undefined,
      fileChangeSourceUnavailable: Boolean(content._imported),
      fileChangeSummary:
        (content.fileChangeSummary as FileChangeSummaryFile[] | undefined) ||
        (content.file_changes?.files as FileChangeSummaryFile[] | undefined),
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
    thinkingEffort: ThinkingEffort | undefined = undefined,
    skipUserHistory = false,
    llmCheckpointId: string | null = null,
    configId?: string,
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
    };
    activeConnections[messageId] = connection;

    fetchWithAuth(chatApi.sendStreamUrl(), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(
        options.accountRole === "user"
          ? {
              session_id: sessionId,
              message: parts.map(partToPayload),
              message_id: messageId,
              config_id: configId,
              thinking_effort: thinkingEffort,
              selected_provider: selectedProvider,
              selected_model: selectedModel,
              ...(skipUserHistory
                ? {
                    _skip_user_history: true,
                    _llm_checkpoint_id: llmCheckpointId,
                  }
                : {}),
            }
          : {
              session_id: sessionId,
              message: parts.map(partToPayload),
              flags: buildChatRequestFlags(enableStreaming),
              selected_provider: selectedProvider,
              selected_model: selectedModel,
              thinking_effort: thinkingEffort,
              config_id: configId,
              _client_message_id: messageId,
              _skip_user_history: skipUserHistory,
              _llm_checkpoint_id: llmCheckpointId || undefined,
            },
      ),
      signal: abort.signal,
    })
      .then(async (response) => {
        if (!response.ok || !response.body) {
          throw new Error(`SSE connection failed: ${response.status}`);
        }
        // Author: elecvoid243
        // Date: 2026-10-08
        // A rejected send is not an HTTP error here: both the v1 and legacy
        // send handlers answer `JSONResponse(error(...))` — HTTP 200 plus
        // `application/json`. Feeding that body to `readSseStream` yields zero
        // frames, so nothing ever cleared the placeholder's `isLoading` and
        // the bubble sat on "思考中..." forever while the server was healthy.
        // The regenerate and resume streams already guard on the content type;
        // this path was the one that did not, and it is the only send path.
        //
        // An absent content type is still treated as a stream (a proxy that
        // strips the header must not break a working stream); the terminal
        // state in `finally` is the backstop for whatever slips through.
        const contentType = response.headers.get("content-type") || "";
        if (contentType !== "" && !contentType.includes("text/event-stream")) {
          const payload = await response.json().catch(() => null);
          throw new Error(
            payload?.message ||
              payload?.data?.message ||
              `Chat stream rejected (${contentType})`,
          );
        }
        await readSseStream(response.body, (payload) => {
          assertCurrentAccount(accountRevision);
          processStreamPayload(botRecord, payload, userRecord, connection);
          options.onStreamUpdate?.(sessionId);
        });
      })
      .catch((error) => {
        if (
          abort.signal.aborted ||
          accountRevision !== accountSessionRevision.value
        )
          return;
        ensureBotRecordVisible(connection);
        appendPlain(botRecord, `\n\n${String(error?.message || error)}`);
        // The appended text is invisible while `isLoading` is still set: the
        // bubble renders the loading div instead of its content (see
        // ChatMessageList's `v-if`). A run that died before its first content
        // event must therefore settle here, or the reason stays hidden.
        markMessageStarted(botRecord);
        console.error("SSE chat failed:", error);
      })
      .finally(async () => {
        if (accountRevision !== accountSessionRevision.value) return;
        if (activeConnections[messageId]?.abort === abort) {
          delete activeConnections[messageId];
          await options.onSessionsChanged?.();
        }
        // Author: elecvoid243
        // Date: 2026-10-08
        // Terminal state for a run that delivered no event at all. `isLoading`
        // is only ever cleared by a stream payload (`markMessageStarted`), so
        // such a placeholder spins forever — and a refresh only "fixed" it
        // because the history snapshot replaced the whole array.
        //
        // Rule: the moment the run dies, the list must agree with what a
        // refresh would show. An empty placeholder carries nothing, so it is
        // dropped and the session re-syncs from history — which also surfaces
        // the reply the server persisted while the fan-out to this subscriber
        // was lost (run teardown drops buffered events; the subscriber attaches
        // lazily after the run may already have finished). This mirrors the
        // recovery the resume stream and the session switch already rely on.
        //
        // Guards: a normal run ends with `isLoading === false` and is left
        // untouched; an aborted request (page unmount, `cleanupConnections`)
        // must never touch the data; and a record another connection still owns
        // is not settled here.
        const isUnfulfilled =
          botRecord.content.isLoading === true &&
          messageParts(botRecord).length === 0 &&
          !botRecord.content.reasoning;
        // Another connection (resume stream) may still be feeding this record;
        // settle only when none does. Phrased by `messageId` + record identity
        // because comparing the connection's own `abort` is not reliable:
        // `AbortController` is proxied by Vue whenever the environment does not
        // tag it (`[object Object]`), which makes that equality silently false.
        const ownedElsewhere = Object.values(activeConnections).some(
          (candidate) =>
            candidate.messageId !== messageId &&
            candidate.botRecord === botRecord,
        );
        if (!abort.signal.aborted && isUnfulfilled && !ownedElsewhere) {
          const records = messagesBySession[sessionId];
          const index = records ? records.indexOf(botRecord) : -1;
          if (index >= 0) records.splice(index, 1);
          await loadSessionMessages(sessionId, true, false);
          // The reload keeps temp-id records the snapshot cannot contain, so a
          // user record whose persisted row IS in the snapshot would render
          // twice — its id was never adopted because no `user_message_saved`
          // event arrived. Ids cannot identify the pair here, so the local copy
          // is dropped only when the snapshot already carries the same text
          // (never losing content: a send the server never persisted keeps its
          // local record, which is exactly what a refresh would show).
          const plainTextOf = (candidate: ChatRecord) =>
            messageParts(candidate)
              .filter((part) => part.type === "plain")
              .map((part) => part.text || "")
              .join("");
          const userText = userRecord ? plainTextOf(userRecord) : "";
          const current = messagesBySession[sessionId] || [];
          const duplicated =
            userRecord != null &&
            String(userRecord.id).startsWith("local-") &&
            userText !== "" &&
            current.some(
              (candidate) =>
                candidate !== userRecord &&
                isUserMessage(candidate) &&
                plainTextOf(candidate) === userText,
            );
          if (duplicated) {
            const userIndex = current.indexOf(userRecord);
            if (userIndex >= 0) current.splice(userIndex, 1);
          }
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
      botVisible:
        messagesBySession[sessionId]?.some(
          (record) => String(record.id) === String(botRecord.id),
        ) ?? false,
    };
    activeConnections[runId] = connection;

    void (async () => {
      let receivedEnd = false;
      let runUnavailable = false;
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
            runUnavailable = response.status === 404;
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
          if (
            abort.signal.aborted ||
            accountRevision !== accountSessionRevision.value
          )
            return;
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
        // A completed receipt can have no reply, including after reconnecting
        // beyond its follow-up confirmation. It must not outlive the real run.
        if (
          (receivedEnd || runUnavailable) &&
          messageParts(botRecord).length === 0 &&
          !messageContent(botRecord).reasoning
        ) {
          const records = messagesBySession[sessionId] || [];
          const index = records.indexOf(botRecord);
          if (index >= 0) records.splice(index, 1);
        }
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
    thinkingEffort: ThinkingEffort | undefined = undefined,
    configId?: string,
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
    };
    activeConnections[messageId] = connection;

    sendWebSocketPayload(sessionId, messageId, {
      ct: "chat",
      t: "send",
      session_id: sessionId,
      message_id: messageId,
      message: parts.map(partToPayload),
      config_id: configId,
      selected_provider: selectedProvider,
      selected_model: selectedModel,
      thinking_effort: thinkingEffort,
      ...(options.accountRole === "user"
        ? {}
        : {
            _client_message_id: messageId,
            flags: buildChatRequestFlags(enableStreaming),
          }),
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
      if (accountRevision !== accountSessionRevision.value) return;
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
    if (accountRevision !== accountSessionRevision.value) return;
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
   * 每个 session 的“新建清单”成功计数（2026-11-07, elecvoid243）。
   *
   * 只有 create 动作成功写入快照时才 +1（v2.35 后由 resolveTodoAction 判定
   * todo_manage 的 action='create'，旧会话仍按 todo_create 名字判定）；
   * add / update / delete / query 不递增。Chat.vue 监听当前会话的计数变化，
   * 在“创建列表”那一刻展开顶栏的 todo 菜单；后续更新不再弹出，避免打断阅读。
   */
  const todoCreateTickBySession = ref<Record<string, number>>({});

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

  function bumpStreamRevision(sessionId: string) {
    streamRevisionBySession[sessionId] =
      (streamRevisionBySession[sessionId] || 0) + 1;
  }

  function ensureBotRecordVisible(connection: ActiveConnection) {
    const { botRecord, userRecord } = connection;
    if (!botRecord || connection.followUpCaptured) return;
    const records = messagesBySession[connection.sessionId] || [];
    // Fast path: the record was visible and the session array has not been
    // replaced since. A history reload swaps the array, which invalidates it.
    if (connection.botVisible && connection.visibleRecords === records) return;
    // Compare by id, not by object identity: the array is reactive, so a raw
    // reference and its proxied element are different objects and an identity
    // check can report the record absent (or present) for the wrong reason.
    const botId = String(botRecord.id ?? "");
    if (records.some((r) => String(r.id) === botId)) {
      connection.botVisible = true;
      connection.visibleRecords = records;
      return;
    }
    // No user record to anchor to: a resume connection (`restoreNextActiveRun`)
    // has none at all. Bailing here was why a bubble the history load dropped
    // could never come back — the connection kept streaming into a record
    // nothing rendered, and no later payload re-inserted it either.
    const userIndex = userRecord
      ? records.findIndex((r) => String(r.id) === String(userRecord.id))
      : -1;
    if (userIndex >= 0) {
      records.splice(userIndex + 1, 0, botRecord);
    } else {
      records.push(botRecord);
    }
    connection.botVisible = true;
    connection.visibleRecords = records;
  }

  function processStreamPayload(
    botRecord: ChatRecord,
    payload: any,
    userRecord?: ChatRecord,
    connection?: ActiveConnection,
  ) {
    if (accountRevision !== accountSessionRevision.value) return;
    const sessionId = connection?.sessionId;
    const normalized =
      payload?.ct === "chat"
        ? { ...payload, type: payload.type || payload.t }
        : payload;
    const msgType = normalized?.type || normalized?.t;
    const chainType = normalized?.chain_type;
    const data = normalized?.data ?? "";
    if (
      sessionId &&
      (STREAM_REVISION_TYPES.has(String(msgType)) ||
        STREAM_REVISION_TYPES.has(String(chainType)))
    ) {
      bumpStreamRevision(sessionId);
    }

    if (msgType === "follow_up_captured") {
      if (!connection || !sessionId) return;
      connection.followUpCaptured = true;
      connection.completed = true;
      connection.botVisible = false;
      markMessageStarted(botRecord);
      const records = messagesBySession[sessionId] || [];
      if (
        messageParts(botRecord).length === 0 &&
        !messageContent(botRecord).reasoning
      ) {
        const index = records.indexOf(botRecord);
        if (index >= 0) records.splice(index, 1);
      }
      const targetRunId =
        typeof data?.target_run_id === "string" ? data.target_run_id : "";
      const target = targetRunId
        ? Object.values(activeConnections).find(
            (candidate) =>
              candidate.sessionId === sessionId &&
              (candidate.runId === targetRunId ||
                candidate.messageId === targetRunId),
          )
        : undefined;
      const checkpointId = data?.target_checkpoint_id;
      if (typeof checkpointId === "string" && checkpointId) {
        const input = userRecord || connection.userRecord;
        const savedId = data.user_message_id ?? input?.id;
        const row = records.find(
          (record) =>
            isUserMessage(record) && String(record.id) === String(savedId),
        );
        if (input) input.content.follow_up_checkpoint_id = checkpointId;
        if (row) row.content.follow_up_checkpoint_id = checkpointId;
        if (target?.botRecord) target.botRecord.llm_checkpoint_id = checkpointId;
      }
      if (
        targetRunId &&
        targetRunId !== connection.messageId &&
        !systemStreamState.liveBySession[sessionId]?.[targetRunId] &&
        !target
      )
        attachLiveRun(sessionId, targetRunId);
      return;
    }
    if (connection?.followUpCaptured && msgType !== "user_message_saved")
      return;
    if (msgType === "message_segment" && connection && sessionId) {
      const records = messagesBySession[sessionId] || [];
      const oldIndex = records.indexOf(botRecord);
      // The end-of-turn file change summary is turn-scoped, but the stream
      // may deliver it before the final segment boundary. History persists it
      // on the last record, so carry it across the boundary instead of
      // leaving it on the segment above (which reads as "summary at the
      // top" once the turn continues in a new bubble).
      const carriedFileChanges = messageContent(botRecord).fileChangeSummary;
      const carriedFileChangeRunId =
        messageContent(botRecord).fileChangeRunId;
      // Retain the reactive handle for subsequent chunks. History may already
      // contain the closed segment; replace it once at the confirmed boundary.
      if (oldIndex >= 0) records.splice(oldIndex, 1);
      if (data.previous) {
        for (let i = records.length - 1; i >= 0; i -= 1) {
          if (String(records[i].id) === String(data.previous.id)) {
            records.splice(i, 1);
          }
        }
      }
      const savedUser = data.user;
      const localUserId = `local-user-${data.client_message_id}`;
      const sender = data.client_message_id
        ? activeConnections[data.client_message_id]
        : undefined;
      let boundaryUser =
        (sender?.sessionId === sessionId ? sender.userRecord : undefined) ||
        records.find((record) => String(record.id) === localUserId) ||
        records.find((record) => String(record.id) === String(savedUser?.id));
      if (savedUser && !boundaryUser) {
        boundaryUser = normalizeHistoryRecord(savedUser);
        records.push(boundaryUser);
      } else if (savedUser && boundaryUser) {
        boundaryUser.id = savedUser.id;
        boundaryUser.created_at = savedUser.created_at;
        boundaryUser.llm_checkpoint_id = savedUser.llm_checkpoint_id;
        // Reconcile by identity, never text: identical follow-ups are valid.
        for (let i = records.length - 1; i >= 0; i -= 1) {
          const record = records[i];
          if (
            record !== boundaryUser &&
            (String(record.id) === String(savedUser.id) ||
              String(record.id) === localUserId)
          ) {
            if (records.includes(boundaryUser)) records.splice(i, 1);
            else records.splice(i, 1, boundaryUser);
          }
        }
        if (!records.includes(boundaryUser)) records.push(boundaryUser);
      }
      connection.precedingPlainText = String(data.preceding_plain_text || "");
      botRecord.id = `active-segment-${data.segment_id}`;
      botRecord.llm_checkpoint_id =
        data.previous?.llm_checkpoint_id || botRecord.llm_checkpoint_id;
      botRecord.created_at = savedUser?.created_at || new Date().toISOString();
      botRecord.content = normalizeHistoryRecord({
        content: data.content || { type: "bot", message: [] },
      }).content;
      if (carriedFileChanges?.length) {
        botRecord.content.fileChangeSummary = carriedFileChanges;
        botRecord.content.fileChangeRunId = carriedFileChangeRunId;
      }
      botRecord.content.isLoading = botRecord.content.message.length === 0;
      let userIndex = boundaryUser
        ? records.indexOf(boundaryUser)
        : records.length - 1;
      if (data.previous) {
        records.splice(
          Math.max(userIndex, 0),
          0,
          normalizeHistoryRecord(data.previous),
        );
        userIndex += 1;
      }
      records.splice(userIndex + 1, 0, botRecord);
      connection.botVisible = true;
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
    if (
      connection &&
      !["user_message_saved", "end", "run_snapshot"].includes(msgType)
    ) {
      ensureBotRecordVisible(connection);
    }
    if (msgType === "run_snapshot") {
      const snapshot = data && typeof data === "object" ? data : {};
      const input = snapshot.input_message;
      const records = sessionId ? (messagesBySession[sessionId] ||= []) : [];
      if (
        input &&
        sessionId &&
        !records.some((r) => String(r.id) === String(input.id))
      ) {
        const index = records.indexOf(botRecord);
        records.splice(
          index < 0 ? records.length : index,
          0,
          normalizeHistoryRecord(input),
        );
      }
      const snapshotRecord = normalizeHistoryRecord({
        id: `active-run-${snapshot.run_id || "unknown"}`,
        content: snapshot.content || { type: "bot", message: [] },
        llm_checkpoint_id: snapshot.llm_checkpoint_id || null,
      });
      snapshotRecord.content.isLoading =
        snapshot.status === "running" &&
        snapshotRecord.content.message.length === 0;
      if (snapshot.history_record_id != null)
        botRecord.id = snapshot.history_record_id;
      if (connection)
        connection.precedingPlainText = snapshot.preceding_plain_text || "";
      botRecord.content = snapshotRecord.content;
      botRecord.content.fileChangeRunId = snapshot.run_id;
      botRecord.llm_checkpoint_id = snapshotRecord.llm_checkpoint_id;
      if (
        connection &&
        (connection.botVisible !== false ||
          messageParts(botRecord).length > 0 ||
          messageContent(botRecord).reasoning)
      )
        ensureBotRecordVisible(connection);
      void resolveRecordMedia([botRecord]);
      return;
    }
    if (msgType === "user_message_saved") {
      if (userRecord) {
        userRecord.id = data?.id || userRecord.id;
        userRecord.created_at = data?.created_at || userRecord.created_at;
        userRecord.llm_checkpoint_id =
          data?.llm_checkpoint_id || userRecord.llm_checkpoint_id;
        // History can arrive before either stream acknowledges the local row.
        const records = sessionId ? messagesBySession[sessionId] || [] : [];
        for (let i = records.length - 1; i >= 0; i -= 1) {
          if (
            records[i] !== userRecord &&
            String(records[i].id) === String(userRecord.id)
          ) {
            if (records.includes(userRecord)) records.splice(i, 1);
            else records.splice(i, 1, userRecord);
          }
        }
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
    if (
      msgType === "llm_request_snapshots" ||
      chainType === "llm_request_snapshots"
    ) {
      markMessageStarted(botRecord);
      messageContent(botRecord).llmRequestSnapshots = mergeLlmRequestSnapshots(
        messageContent(botRecord).llmRequestSnapshots || [],
        parseLlmRequestSnapshots(data),
      );
      return;
    }
    if (msgType === "agent_stats" || chainType === "agent_stats") {
      markMessageStarted(botRecord);
      messageContent(botRecord).agentStats = data;
      return;
    }
    if (
      msgType === "llm_request_injections" ||
      chainType === "llm_request_injections"
    ) {
      markMessageStarted(botRecord);
      messageContent(botRecord).llmRequestInjections =
        parseLlmRequestInjections(data) ?? undefined;
      return;
    }
    if (msgType === "assistant_file_comments") {
      if (data?.type === msgType && Array.isArray(data.comments)) {
        markMessageStarted(botRecord);
        messageContent(botRecord).message.push(data);
      }
      return;
    }
    if (msgType === "file_changes" || chainType === "file_changes") {
      markMessageStarted(botRecord);
      const files = parseFileChangesPayload(data);
      if (files.length) {
        messageContent(botRecord).fileChangeSummary = files;
        messageContent(botRecord).fileChangeRunId = connection?.runId;
      }
      return;
    }
    if (msgType === "error") {
      markMessageStarted(botRecord);
      appendPlain(botRecord, `\n\n${String(data)}`);
      return;
    }
    if (msgType === "complete" || msgType === "break") {
      markMessageStarted(botRecord);
      const completeText = payloadText(data);
      const prefix = connection?.precedingPlainText || "";
      const finalText =
        prefix && completeText.startsWith(prefix)
          ? completeText.slice(prefix.length)
          : completeText;
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
      // Author: elecvoid243
      // Date: 2026-10-08
      // The context compression notice is telemetry about the run, not part
      // of the answer: hand it to the page (which shows a toast) and stop.
      // Handled before `markMessageStarted` on purpose — the answer has not
      // arrived yet, so clearing the record's loading state here would leave
      // the bubble empty and un-marked until the first delta.
      if (chainType === "context_compression") {
        const notice = parseContextCompressionNotice(data);
        if (notice && sessionId) {
          options.onContextCompression?.(sessionId, notice);
        }
        return;
      }
      markMessageStarted(botRecord);
      if (chainType === "reasoning") {
        appendReasoningPart(botRecord, payloadText(data));
        return;
      }
      if (chainType === "reasoning_loop") {
        const notice = parseJsonSafe(data);
        if (notice?.status === "retry" || notice?.status === "failed") {
          messageContent(botRecord).message.push({
            type: "reasoning_loop",
            status: notice.status,
            retry: notice.retry,
            max_retries: notice.max_retries,
          });
        }
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
        if (sessionId && parsed && typeof parsed === "object") {
          options.onToolCall?.(
            sessionId,
            parsed,
            connection?.runId || connection?.messageId || String(botRecord.id),
          );
        }
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
          if (toolName === "create_chat_session") {
            const created = parseJsonSafe(matched?.result);
            if (
              created &&
              typeof created.session_id === "string" &&
              ["created", "created_send_failed"].includes(created.status)
            ) {
              // The source system stream may be closed while this run stays
              // active. Publish the new list entry as soon as creation returns.
              void options.onSessionsChanged?.(
                typeof created.project_id === "string"
                  ? created.project_id
                  : null,
              );
            }
          }
          if (
            toolName &&
            TODO_TOOL_NAMES.has(toolName) &&
            typeof (parsed as any).result === "string"
          ) {
            // `todo_manage` carries its operation in `args.action`; the
            // legacy standalone names encode it in the name itself.
            const todoAction = resolveTodoAction(toolName, matched?.args);
            const snapshot = parseTodoToolResult(
              toolName,
              (parsed as any).result,
              matched?.args,
            );
            // undefined → 保持现状;null → 清空;对象 → 写入。
            if (snapshot !== undefined) {
              writeTodoSnapshot(sessionId, snapshot);
              if (todoAction === "create" && snapshot) {
                todoCreateTickBySession.value = {
                  ...todoCreateTickBySession.value,
                  [sessionId]:
                    (todoCreateTickBySession.value[sessionId] ?? 0) + 1,
                };
              }
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
    sessionDetails,
    sessionArchivedFlags,
    historyPagingBySession,
    historyOffsetBySession,
    sessionMarkersBySession,
    activeMessages,
    streamingBotRecords,
    streamRevisionBySession,
    activeMessagePreviews,
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
    startUnifiedStream,
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
    /**
     * 每个 session 的 todo_create 成功计数；用于“创建时自动展开菜单”。
     */
    latestTodoCreateTickBySession: todoCreateTickBySession,
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
  const comments = content.type === "bot" ? assistantFileComments(parts) : [];

  const blocks: MessageDisplayBlock[] = [];
  let currentKind: MessageDisplayBlock["kind"] | null = null;
  let currentParts: MessagePart[] = [];

  for (const source of parts) {
    if (source.type === "assistant_file_comments") continue;
    const part = comments.length && source.type === "plain" && source.text
      ? { ...source, text: withoutAssistantFileDirectives(source.text, comments) }
      : source;
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

export interface AgentWorkSplit {
  /** Everything the agent produced around the final reply (thinking, tool
   * calls, intermediate outputs, resolved interactive choices). */
  workBlocks: MessageDisplayBlock[];
  /** The trailing final reply, kept visible when work is collapsed. */
  finalBlocks: MessageDisplayBlock[];
  /** Index of that reply inside the message's full block list. The visible
   * region is not a suffix of it (work that trails the reply folds back), so
   * a consumer that addresses blocks by index — the reasoning sidebar does —
   * must not reuse the position inside `finalBlocks`. */
  finalBlockIndex: number;
}

export function isFinalReplyBlock(block: MessageDisplayBlock): boolean {
  return (
    block.kind === "content" &&
    block.parts.some(
      (part) =>
        (part.type === "plain" && String(part.text || "").trim()) ||
        (part.type === "reasoning_loop" && part.status === "failed"),
    ) &&
    !block.parts.some((part) => part.type === "tool_call")
  );
}

function isInteractiveChoicePart(part: MessagePart) {
  return part.type === "interactive_choice";
}

function hasReplyText(parts: MessagePart[]) {
  return parts.some(
    (part) => part.type === "plain" && String(part.text || "").trim(),
  );
}

/**
 * The reply parts that stay visible while the work trail is collapsed.
 *
 * The capsule only shows the final output, so an `interactive_choice` inside
 * the reply block folds with the rest of the work: the run continued past it
 * (a pending choice keeps the whole message live, pill included), which makes
 * the box resolved history — expanding the pill renders it through the same
 * InteractiveChoiceBox path, review options included.
 *
 * @param parts - Parts of the trailing reply block.
 * @returns The parts to render under the pill.
 */
function visibleReplyParts(parts: MessagePart[]) {
  let choiceIndex = -1;
  for (let index = parts.length - 1; index >= 0; index -= 1) {
    if (isInteractiveChoicePart(parts[index])) {
      choiceIndex = index;
      break;
    }
  }
  if (choiceIndex < 0) return parts;
  // Text streamed after the last box is the actual answer; with none, the
  // agent asked its question and stopped, so the text before the box is what
  // the message opens on.
  const trailing = parts.slice(choiceIndex + 1);
  if (hasReplyText(trailing)) return trailing;
  const replyParts = parts.filter((part) => !isInteractiveChoicePart(part));
  return hasReplyText(replyParts) ? replyParts : parts;
}

export function splitAgentWork(
  content: ChatContent,
  hasEarlierWork = false,
): AgentWorkSplit | null {
  const blocks = messageBlocks(content);
  let finalStart = -1;
  for (let index = blocks.length - 1; index >= 0; index -= 1) {
    if (isFinalReplyBlock(blocks[index])) {
      finalStart = index;
      break;
    }
  }
  // No final reply, or nothing produced before it — nothing to collapse.
  if (finalStart < 0 || (finalStart === 0 && !hasEarlierWork)) return null;
  // Work that landed after the reply stays in the work group instead of
  // hanging under the capsule: a tool call the run never returned gets
  // frozen into the record when the `interactive_choice` event saves it,
  // and it used to render as a separate, empty tool block below the box.
  const replyParts = visibleReplyParts(blocks[finalStart].parts);
  // The reply region can leave parts of its block out (an
  // `interactive_choice` it hides, or the question a trailing answer
  // follows). They fold back into the work group instead of being dropped:
  // the collapsed capsule renders the reply region and nothing else, so a
  // part that belongs to neither region is rendered nowhere at all.
  const visible = new Set(replyParts);
  const foldedParts = blocks[finalStart].parts.filter(
    (part) => !visible.has(part),
  );
  const foldedBlocks: MessageDisplayBlock[] = foldedParts.length
    ? [{ kind: "content", parts: foldedParts }]
    : [];
  return {
    workBlocks: [
      ...blocks.slice(0, finalStart),
      ...foldedBlocks,
      ...blocks.slice(finalStart + 1),
    ],
    finalBlocks:
      replyParts === blocks[finalStart].parts
        ? [blocks[finalStart]]
        : [{ kind: "content", parts: replyParts }],
    finalBlockIndex: finalStart,
  };
}

/** Serialize public message fields while retaining previews only in local state. */
export function partToPayload(part: MessagePart) {
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

export async function readSseStream(
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
    const previous = String(last.think || "");
    last.think = `${previous}${text}`;
    // Streaming fast path: `reasoning` is the concatenation of all think
    // parts, so appending the delta is equivalent to re-extracting. Fall
    // back when the cached value is missing or stale.
    if (
      typeof content.reasoning === "string" &&
      content.reasoning.endsWith(previous)
    ) {
      content.reasoning = `${content.reasoning}${text}`;
    } else {
      content.reasoning = extractReasoningText(content.message);
    }
  } else {
    const previousReasoning = extractReasoningText(content.message);
    content.message.push({ type: "think", think: text });
    content.reasoning = `${previousReasoning}${text}`;
  }
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
      if (result.result !== undefined) tool.result = result.result;
      if (result.result_ref) tool.result_ref = result.result_ref;
      if (result.result_preview) tool.result_preview = result.result_preview;
      if (Array.isArray(result.image_attachment_ids)) {
        tool.image_attachment_ids = result.image_attachment_ids;
      }
      tool.finished_ts = result.ts || Date.now() / 1000;
      return;
    }
  }
  record.content.message.push({
    type: "tool_call",
    tool_calls: [
      {
        id: targetId,
        ...(Array.isArray(result.image_attachment_ids)
          ? { name: result.name, image_attachment_ids: result.image_attachment_ids }
          : {}),
        result: result.result,
        ...(result.result_ref ? { result_ref: result.result_ref } : {}),
        ...(result.result_preview
          ? { result_preview: result.result_preview }
          : {}),
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
