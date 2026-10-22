<template>
  <transition name="slide-left">
    <aside
      v-if="modelValue && thread"
      ref="panelRef"
      class="thread-panel"
      :class="{ 'is-side-chat': sideChat, 'is-dark': isDark }"
      :style="{ width: panelWidth + 'px' }"
    >
      <div class="thread-panel-resizer" @mousedown="startResize" />
      <div class="thread-panel-header">
        <div class="thread-panel-heading">
          <div class="thread-panel-title">{{ panelTitle }}</div>
          <div v-if="sideChat" class="thread-panel-subtitle">
            {{ tm("thread.sideHint") }}
          </div>
        </div>
        <div class="thread-panel-actions">
          <v-btn
            v-if="!sideChat"
            icon="mdi-delete-outline"
            class="thread-delete-button"
            size="small"
            variant="text"
            :title="tm('thread.delete')"
            :loading="deleting"
            :disabled="sending || deleting"
            @click="emit('delete', thread)"
          />
          <v-btn icon="mdi-close" size="small" variant="text" @click="close" />
        </div>
      </div>

      <blockquote
        v-if="!sideChat && thread.selected_text"
        class="thread-selected-text"
      >
        {{ thread.selected_text }}
      </blockquote>

      <div ref="messagesEl" class="thread-messages">
        <div class="thread-messages-shell">
          <ChatMessageList
            :messages="messages"
            :is-dark="isDark"
            :is-streaming="sending"
          />
        </div>
      </div>

      <form class="thread-composer" @submit.prevent="send">
        <div class="thread-composer-box">
          <textarea
            ref="textareaEl"
            v-model="draft"
            class="thread-input"
            :placeholder="
              sideChat
                ? tm('thread.sidePlaceholder')
                : tm('thread.placeholder')
            "
            rows="1"
            :disabled="sending"
            @input="resizeComposer"
            @keydown.enter.exact.prevent="send"
          ></textarea>
          <button
            class="thread-send-button"
            type="submit"
            :disabled="!draft.trim() || sending"
            :aria-label="tm('input.send')"
            :title="tm('input.send')"
          >
            <v-icon size="18">mdi-arrow-up</v-icon>
          </button>
        </div>
      </form>
    </aside>
  </transition>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { chatApi } from "@/api/v1";
import { fetchWithAuth } from "@/api/http";
import {
  appendPlain,
  appendReasoningPart,
  buildChatRequestFlags,
  extractReasoningText,
  finishToolCall,
  hasPlainText,
  markMessageStarted,
  normalizeMessageParts,
  parseJsonSafe,
  payloadText,
  upsertToolCall,
  type ChatRecord,
  type MessagePart,
  type ChatThread,
} from "@/composables/useMessages";
import { useModuleI18n } from "@/i18n/composables";
import ChatMessageList from "@/components/chat/ChatMessageList.vue";

const props = defineProps<{
  modelValue: boolean;
  thread: ChatThread | null;
  isDark: boolean;
  deleting?: boolean;
  /**
   * 2026-10-22 side-chat: a temporary thread with no parent message. The
   * panel drops the quoted selection and the delete button (closing the
   * panel destroys the thread instead).
   */
  sideChat?: boolean;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  delete: [thread: ChatThread];
}>();

const { tm } = useModuleI18n("features/chat");
const messages = ref<ChatRecord[]>([]);
const draft = ref("");
const sending = ref(false);
const messagesEl = ref<HTMLElement | null>(null);
const textareaEl = ref<HTMLTextAreaElement | null>(null);

const panelTitle = computed(() =>
  props.sideChat ? tm("thread.sideTitle") : tm("thread.title"),
);

// ── Drag resize (mirrors ReasoningSidebar) ─────────────────────────

const MIN_WIDTH = 320;
const MAX_WIDTH = 1800;
const DEFAULT_WIDTH = 520;

const panelWidth = ref(DEFAULT_WIDTH);
const panelRef = ref<HTMLElement | null>(null);
let isResizing = false;

function startResize(e: MouseEvent) {
  e.preventDefault();
  isResizing = true;
  document.body.style.cursor = "ew-resize";
  document.body.style.userSelect = "none";
  document.addEventListener("mousemove", onMouseMove);
  document.addEventListener("mouseup", onMouseUp);
}

function onMouseMove(e: MouseEvent) {
  if (!isResizing || !panelRef.value) return;
  // The sidebar's own right edge is the anchor: siblings rendered to the
  // right (if any) are already excluded from this rect.
  const rect = panelRef.value.getBoundingClientRect();
  panelWidth.value = Math.min(
    MAX_WIDTH,
    Math.max(MIN_WIDTH, rect.right - e.clientX),
  );
}

function onMouseUp() {
  if (!isResizing) return;
  isResizing = false;
  document.body.style.cursor = "";
  document.body.style.userSelect = "";
  document.removeEventListener("mousemove", onMouseMove);
  document.removeEventListener("mouseup", onMouseUp);
}

onBeforeUnmount(onMouseUp);

/** Grow the composer with its content, capped like the main input. */
function resizeComposer() {
  const el = textareaEl.value;
  if (!el) return;
  el.style.height = "0px";
  el.style.height = `${Math.min(140, Math.max(40, el.scrollHeight))}px`;
}

watch(
  () => props.thread?.thread_id,
  (threadId) => {
    if (threadId) {
      loadThread(threadId);
    } else {
      messages.value = [];
    }
  },
  { immediate: true },
);

function close() {
  emit("update:modelValue", false);
}

async function loadThread(threadId: string) {
  try {
    const response = await chatApi.getThread(threadId);
    const history = response.data?.data?.history || [];
    messages.value = history.map(normalizeRecord);
    scrollToBottom();
  } catch (error) {
    console.error("Failed to load thread:", error);
    messages.value = [];
  }
}

async function send() {
  if (!props.thread || sending.value || !draft.value.trim()) return;
  const text = draft.value.trim();
  draft.value = "";
  nextTick(resizeComposer);
  const messageId = crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  const userRecord: ChatRecord = {
    id: `local-thread-user-${messageId}`,
    created_at: new Date().toISOString(),
    content: {
      type: "user",
      message: [{ type: "plain", text }],
    },
  };
  const botRecord: ChatRecord = {
    id: `local-thread-bot-${messageId}`,
    created_at: new Date().toISOString(),
    content: {
      type: "bot",
      message: [],
      reasoning: "",
      isLoading: true,
    },
  };
  messages.value.push(userRecord, botRecord);
  const threadUserRecord = messages.value[messages.value.length - 2];
  const threadBotRecord = messages.value[messages.value.length - 1];
  scrollToBottom();

  const abort = new AbortController();
  sending.value = true;
  try {
    const response = await fetchWithAuth(chatApi.sendThreadMessageUrl(props.thread.thread_id), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        message: [{ type: "plain", text }],
        flags: buildChatRequestFlags(),
      }),
      signal: abort.signal,
    });
    if (!response.ok || !response.body) {
      throw new Error(`Thread request failed: ${response.status}`);
    }
    await readSseStream(response.body, (payload) => {
      processPayload(threadBotRecord, threadUserRecord, payload);
      scrollToBottom();
    });
  } catch (error) {
    appendPlain(
      threadBotRecord,
      `\n\n${String((error as Error)?.message || error)}`,
    );
    console.error("Failed to send thread message:", error);
  } finally {
    sending.value = false;
  }
}

function normalizeRecord(record: any): ChatRecord {
  const content = record.content || {};
  const normalizedMessage = normalizeMessageParts(
    content.message || [],
    content.reasoning || "",
  );
  return {
    ...record,
    content: {
      type: content.type || (record.sender_id === "bot" ? "bot" : "user"),
      message: normalizedMessage,
      reasoning: extractReasoningText(normalizedMessage, content.reasoning || ""),
      agentStats: content.agentStats || content.agent_stats,
      llmRequestInjections:
        content.llmRequestInjections || content.llm_request_injections,
      refs: content.refs,
    },
  };
}

async function readSseStream(
  stream: ReadableStream<Uint8Array>,
  onPayload: (payload: any) => void,
) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      const data = chunk
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (!data) continue;
      try {
        onPayload(JSON.parse(data));
      } catch (error) {
        console.error("Failed to parse thread SSE payload:", error, data);
      }
    }
  }
}

function processPayload(botRecord: ChatRecord, userRecord: ChatRecord, payload: any) {
  const normalized =
    payload?.ct === "chat"
      ? { ...payload, type: payload.type || payload.t }
      : payload;
  const type = normalized?.type || normalized?.t;
  const chainType = normalized?.chain_type;
  const data = normalized?.data ?? "";

  if (type === "session_id" || type === "session_bound") return;

  if (type === "user_message_saved") {
    userRecord.id = data?.id || userRecord.id;
    userRecord.created_at = data?.created_at || userRecord.created_at;
    userRecord.llm_checkpoint_id =
      data?.llm_checkpoint_id || userRecord.llm_checkpoint_id;
    return;
  }

  if (type === "message_saved") {
    markMessageStarted(botRecord);
    botRecord.id = data?.id || botRecord.id;
    botRecord.created_at = data?.created_at || botRecord.created_at;
    botRecord.llm_checkpoint_id =
      data?.llm_checkpoint_id || botRecord.llm_checkpoint_id;
    if (data?.refs) {
      botRecord.content.refs = data.refs;
    }
    return;
  }

  if (type === "agent_stats" || chainType === "agent_stats") {
    markMessageStarted(botRecord);
    botRecord.content.agentStats = data;
    return;
  }

  if (type === "error") {
    markMessageStarted(botRecord);
    appendPlain(botRecord, `\n\n${String(data)}`);
    return;
  }

  if (type === "complete" || type === "break") {
    markMessageStarted(botRecord);
    const finalText = payloadText(data);
    const existingText = botRecord.content.message
      .filter((part) => part.type === "plain")
      .map((part) => part.text || "")
      .join("");
    const missingText = finalText.slice(existingText.length);
    if (
      type === "complete" &&
      missingText &&
      finalText.startsWith(existingText)
    ) {
      appendPlain(botRecord, missingText);
    } else if (finalText && !hasPlainText(botRecord)) {
      appendPlain(botRecord, finalText, false);
    }
    return;
  }

  if (type === "end") {
    markMessageStarted(botRecord);
    return;
  }

  if (type === "plain") {
    markMessageStarted(botRecord);
    if (chainType === "reasoning") {
      appendReasoningPart(botRecord, payloadText(data));
      return;
    }
    if (chainType === "tool_call") {
      upsertToolCall(botRecord, parseJsonSafe(data));
      return;
    }
    if (chainType === "tool_call_result") {
      finishToolCall(botRecord, parseJsonSafe(data));
      return;
    }
    appendPlain(botRecord, payloadText(data), normalized.streaming !== false);
    return;
  }

  if (["image", "record", "file", "video"].includes(type)) {
    markMessageStarted(botRecord);
    const rawFilename = String(data)
      .replace("[IMAGE]", "")
      .replace("[RECORD]", "")
      .replace("[FILE]", "")
      .replace("[VIDEO]", "");
    const separatorIndex = rawFilename.indexOf("|");
    const storedFilename =
      separatorIndex >= 0 ? rawFilename.slice(0, separatorIndex) : rawFilename;
    const displayFilename =
      separatorIndex >= 0 ? rawFilename.slice(separatorIndex + 1) : storedFilename;
    const filename = displayFilename || storedFilename;
    const mediaPart: MessagePart = { type, filename };
    if (storedFilename && storedFilename !== filename) {
      mediaPart.stored_filename = storedFilename;
    }
    botRecord.content.message.push(mediaPart);
  }
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesEl.value) {
      messagesEl.value.scrollTop = messagesEl.value.scrollHeight;
    }
  });
}
</script>

<style scoped>
.thread-panel {
  height: calc(100% - var(--chat-panel-top-offset, 0px));
  margin-top: var(--chat-panel-top-offset, 0px);
  border-left: 1px solid var(--chat-border, rgba(var(--v-theme-on-surface), 0.1));
  background: var(--chat-page-bg, rgb(var(--v-theme-surface)));
  color: rgb(var(--v-theme-on-surface));
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  position: relative;
  min-width: 0;
}

/* 2026-10-22 side-panel: drag handle, identical to ReasoningSidebar. */
.thread-panel-resizer {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 6px;
  cursor: ew-resize;
  z-index: 10;
  transition: background 0.15s ease;
}

.thread-panel-resizer:hover,
.thread-panel-resizer:active {
  background: rgba(var(--v-theme-primary), 0.2);
}

.slide-left-enter-active,
.slide-left-leave-active {
  transition: all 0.2s ease;
}

.slide-left-enter-from,
.slide-left-leave-to {
  transform: translateX(100%);
  opacity: 0;
}

.thread-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px 8px;
  gap: 8px;
}

.thread-panel-heading {
  min-width: 0;
}

.thread-panel-title {
  font-size: 16px;
  font-weight: 600;
  line-height: 1.4;
  color: rgb(var(--v-theme-on-surface));
}

.thread-panel-subtitle {
  margin-top: 2px;
  font-size: 11.5px;
  line-height: 15px;
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.5));
}

.thread-panel-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.thread-delete-button {
  color: rgb(var(--v-theme-on-surface));
}

.thread-delete-button:hover {
  background: transparent;
}

.thread-selected-text {
  margin: 4px 16px 12px;
  padding: 12px 14px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  background: rgba(var(--v-theme-on-surface), 0.035);
  border-radius: 18px;
  color: rgba(var(--v-theme-on-surface), 0.72);
  font-size: 13px;
  line-height: 1.6;
  max-height: 120px;
  overflow-y: auto;
}

.thread-messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 4px 0 12px;
  scrollbar-gutter: stable;
}

/* Same content column as the main chat (Chat.vue .messages-list-shell). */
.thread-messages-shell {
  width: var(--chat-content-width, 80%);
  max-width: var(--chat-content-max-width, 960px);
  margin: 0 auto;
}

.thread-composer {
  flex: 0 0 auto;
  padding: 0 0 18px;
  background: transparent;
}

.thread-composer-box {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  width: var(--chat-content-width, 80%);
  max-width: var(--chat-content-max-width, 960px);
  margin: 0 auto;
  padding: 6px 8px 8px 14px;
  border: 1px solid #e0e0e0;
  border-radius: 24px;
  background: rgb(var(--v-theme-surface));
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.06);
}

.thread-panel.is-dark .thread-composer-box {
  border-color: rgba(255, 255, 255, 0.12);
  background: #2d2d2d;
  box-shadow: none;
}

.thread-input {
  flex: 1;
  min-width: 0;
  box-sizing: border-box;
  height: 40px;
  min-height: 40px;
  max-height: 140px;
  padding: 9px 4px 9px 0;
  border: 0;
  outline: none;
  resize: none;
  background: transparent;
  color: inherit;
  font: inherit;
  line-height: 20px;
}

.thread-input::placeholder {
  color: rgba(var(--v-theme-on-surface), 0.56);
}

.thread-send-button {
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: #5594c6;
  color: #fff;
  cursor: pointer;
  transition:
    background 0.14s ease,
    opacity 0.14s ease;
}

.thread-send-button:hover:not(:disabled) {
  background: #4c86b3;
}

.thread-send-button:disabled {
  background: rgba(85, 148, 198, 0.24);
  color: rgba(255, 255, 255, 0.72);
  cursor: default;
}

.thread-panel.is-dark .thread-send-button {
  background: rgb(var(--v-theme-on-surface));
  color: rgb(var(--v-theme-surface));
}

.thread-panel.is-dark .thread-send-button:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.86);
}

.thread-panel.is-dark .thread-send-button:disabled {
  background: rgba(var(--v-theme-on-surface), 0.14);
  color: rgba(var(--v-theme-on-surface), 0.4);
}

@media (max-width: 760px) {
  .thread-panel {
    position: fixed;
    inset: 0;
    z-index: 1300;
    width: 100vw !important;
    height: 100dvh;
    margin-top: 0;
    border-left: 0;
  }

  .thread-panel-resizer {
    display: none;
  }

  .thread-panel-header {
    min-height: 52px;
    padding: calc(10px + env(safe-area-inset-top)) 12px 8px;
    border-bottom: 1px solid var(--chat-border, rgba(var(--v-border-color), 0.12));
  }

  .thread-selected-text {
    margin: 10px 12px;
    padding: 10px 12px;
    border-radius: 14px;
    max-height: 96px;
    font-size: 13px;
  }

  .thread-messages {
    padding: 0 0 10px;
  }

  .thread-messages-shell {
    width: calc(100% - 24px);
    max-width: none;
  }

  .thread-composer {
    padding: 0 12px calc(10px + env(safe-area-inset-bottom));
    background: var(--chat-page-bg, rgb(var(--v-theme-surface)));
  }

  .thread-composer-box {
    width: 100%;
    max-width: none;
  }

  .thread-input {
    min-width: 0;
    font-size: 16px;
  }
}

</style>
