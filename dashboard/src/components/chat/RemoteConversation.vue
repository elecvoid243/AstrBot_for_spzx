<template>
  <section class="remote-conversation" :class="{ 'is-dark': isDark }">
    <header class="remote-header">
      <div class="remote-header__identity">
        <strong>{{ session.display_name || session.session_id }}</strong>
        <span class="remote-connection-chip" :title="connection.base_url"
          ><span class="remote-status-dot" :class="{ connected: ready }" />{{
            connection.name
          }}</span
        >
      </div>
      <v-btn variant="text" size="small" :disabled="loading" @click="refresh">{{
        tm("remoteConnections.refresh")
      }}</v-btn>
    </header>
    <v-alert v-if="error" type="error" variant="tonal" class="remote-alert">{{
      tm(`remoteConnections.status.${error}`)
    }}</v-alert>
    <v-progress-linear v-if="loading" indeterminate />
    <div ref="historyEl" class="remote-history">
      <div class="messages-list-shell">
        <ChatMessageList
          :messages="[...messages, ...liveMessages]"
          :is-dark="isDark"
          :is-streaming="running"
          remote-history
          :remote-scope="resourceScope"
          :remote-ready="ready"
          :history-has-more="hasMore"
          :history-loading-older="loading"
          @load-older="load(true)"
        />
      </div>
    </div>
    <form
      class="input-area remote-composer"
      :class="{ 'is-dark': isDark }"
      @submit.prevent="send"
      @dragover.prevent
      @drop.prevent="uploadFiles(Array.from($event.dataTransfer?.files || []))"
    >
      <div class="remote-composer-status">
        <span role="status"
          ><span class="remote-status-dot" :class="{ connected: ready }" />{{
            tm(running ? "remoteConnections.running" : "remoteConnections.idle")
          }}</span
        >
      </div>
      <v-alert
        v-if="uncertain"
        type="warning"
        variant="tonal"
        class="remote-uncertain"
      >
        {{ tm("remoteConnections.unknown")
        }}<v-btn
          variant="text"
          :disabled="!ready || sending"
          @click="checkReceipt"
          >{{ tm("remoteConnections.checked") }}</v-btn
        >
      </v-alert>
      <div class="input-container">
        <div v-if="attachments.length" class="remote-attachments">
          <div
            v-for="(file, index) in attachments"
            :key="file.attachment_id"
            class="remote-attachment"
          >
            <v-icon size="16">mdi-paperclip</v-icon
            ><span>{{ file.filename }}</span>
            <v-btn
              icon="mdi-close"
              variant="text"
              size="x-small"
              :aria-label="tm('remoteConnections.removeAttachment')"
              :disabled="sending || uncertain"
              @click="attachments.splice(index, 1)"
            />
          </div>
        </div>
        <div class="composer-row">
          <div class="input-field-shell">
            <textarea
              ref="textareaEl"
              v-model="draft"
              class="chat-textarea"
              rows="1"
              :aria-label="tm('remoteConnections.message')"
              :placeholder="tm('input.placeholder')"
              :disabled="sending || uncertain"
              @keydown="onKeydown"
              @paste="
                $event.clipboardData?.files.length &&
                  ($event.preventDefault(),
                  uploadFiles(Array.from($event.clipboardData.files)))
              "
            />
          </div>
          <div class="composer-toolbar">
            <div class="composer-toolbar__left">
              <v-btn
                icon="mdi-plus"
                variant="text"
                class="composer-ghost-btn"
                :aria-label="tm('remoteConnections.attach')"
                :loading="uploading"
                :disabled="!ready || sending || uncertain || uploading"
                @click="fileInput?.click()"
              />
              <span class="remote-model-hint">{{
                tm("remoteConnections.execution")
              }}</span>
            </div>
            <div class="composer-toolbar__right">
              <v-btn
                v-if="running"
                icon="mdi-stop"
                variant="text"
                class="composer-ghost-btn"
                :aria-label="tm('remoteConnections.stop')"
                :loading="stopping"
                :disabled="!ready || stopping"
                @click="stop"
              />
              <v-btn
                type="submit"
                icon="mdi-arrow-up"
                variant="flat"
                class="input-action-btn"
                :loading="sending"
                :aria-label="
                  tm(
                    running
                      ? 'remoteConnections.followUp'
                      : 'remoteConnections.send',
                  )
                "
                :disabled="
                  !ready ||
                  sending ||
                  uncertain ||
                  uploading ||
                  (!draft.trim() && !attachments.length)
                "
              />
            </div>
          </div>
        </div>
      </div>
      <input
        ref="fileInput"
        type="file"
        multiple
        class="remote-file-input"
        :disabled="!ready || sending || uncertain || uploading"
        @change="uploadFiles"
      />
    </form>
  </section>
</template>
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import ChatMessageList from "./ChatMessageList.vue";
import {
  remoteConnectionsApi,
  type RemoteConnection,
  type RemoteSession,
  type RemoteUpload,
} from "@/api/remoteConnections";
import { normalizeMessageParts } from "@/composables/normalizeMessageParts";
import type { ChatRecord, MessagePart } from "@/composables/useMessages";
import { accountSessionRevision } from "@/utils/accountSession";
import { useModuleI18n } from "@/i18n/composables";
import { readSseStream } from "@/utils/sseReader";
const props = defineProps<{
  connection: RemoteConnection;
  session: RemoteSession;
  isDark: boolean;
  sendShortcut?: "enter" | "shift_enter";
}>();
const emit = defineEmits<{ unavailable: [] }>();
const { tm } = useModuleI18n("features/chat");
const messages = ref<ChatRecord[]>([]),
  hasMore = ref(false),
  loading = ref(false),
  error = ref("");
const liveMessages = ref<ChatRecord[]>([]),
  draft = ref(""),
  uncertain = ref(false),
  sending = ref(false),
  stopping = ref(false),
  running = ref(false),
  ready = ref(false),
  requestId = ref("");
const historyEl = ref<HTMLElement>();
const textareaEl = ref<HTMLTextAreaElement>();
watch(draft, async () => {
  await nextTick();
  if (!textareaEl.value) return;
  textareaEl.value.style.height = "auto";
  textareaEl.value.style.height = `${textareaEl.value.scrollHeight}px`;
});
function onKeydown(event: KeyboardEvent) {
  if (
    event.isComposing ||
    event.keyCode === 229 ||
    event.key !== "Enter" ||
    event.ctrlKey ||
    event.metaKey ||
    event.altKey
  )
    return;
  if ((props.sendShortcut === "shift_enter") !== event.shiftKey) return;
  event.preventDefault();
  void send();
}
// Pages, persisted active rows and transient snapshots use the same display shape.
function normalizeRecord(row: ChatRecord, live = false): ChatRecord {
  const content = (row.content || {
    type: row.sender_id === "bot" ? "bot" : "user",
    message: [],
  }) as ChatRecord["content"] & { agent_stats?: unknown };
  const parts = normalizeMessageParts(content.message || []) as MessagePart[];
  if (content.reasoning && !parts.some((part) => part.type === "think"))
    parts.unshift({ type: "think", think: content.reasoning });
  return {
    ...row,
    isStreaming: live,
    content: {
      ...content,
      type: content.type || (row.sender_id === "bot" ? "bot" : "user"),
      message: parts,
      agentStats: content.agentStats || content.agent_stats,
      isLoading: live && !parts.length && !content.reasoning,
    },
  };
}
const fileInput = ref<HTMLInputElement>();
const attachments = ref<RemoteUpload[]>([]);
const uploading = ref(false);
const resourceScope = computed(() => ({
  connectionId: props.connection.connection_id,
  revision: props.connection.revision,
  sessionId: props.session.session_id,
}));
async function uploadFiles(event: Event | File[]) {
  if (!ready.value || sending.value || uncertain.value || uploading.value)
    return;
  const input = Array.isArray(event)
    ? null
    : (event.target as HTMLInputElement);
  const files = Array.isArray(event) ? event : Array.from(input?.files || []);
  const target = identity(),
    current = generation;
  if (attachments.value.length + files.length > 20) {
    error.value = "remote_attachment_limit";
    if (input) input.value = "";
    return;
  }
  uploading.value = true;
  try {
    for (const file of files) {
      const result = await remoteConnectionsApi.upload(
        resourceScope.value,
        file,
      );
      if (identity() !== target || generation !== current) return;
      attachments.value.push(result.data.data);
    }
  } catch (err: any) {
    if (identity() === target && generation === current)
      error.value = err.response?.data?.data?.code || "remote_rejected";
  } finally {
    if (identity() === target && generation === current)
      uploading.value = false;
    if (input) input.value = "";
  }
}
watch(
  [messages, liveMessages],
  () => {
    const el = historyEl.value;
    if (
      el &&
      !loading.value &&
      el.scrollHeight - el.clientHeight - el.scrollTop < 120
    )
      void nextTick(() => {
        el.scrollTop = el.scrollHeight;
      });
  },
  { deep: true },
);
let controller: AbortController | undefined,
  retry: ReturnType<typeof setTimeout> | undefined;
let restoring = false;
const draftKey = () =>
  "remote-chat-draft:" +
  JSON.stringify([
    localStorage.getItem("account_id"),
    props.connection.connection_id,
    props.connection.revision,
    props.session.session_id,
  ]);
watch(
  [draft, uncertain, requestId, attachments],
  () => {
    if (!restoring)
      localStorage.setItem(
        draftKey(),
        JSON.stringify({
          text: draft.value,
          attachments: attachments.value,
          uncertain: uncertain.value,
          requestId: requestId.value,
        }),
      );
  },
  { flush: "sync", deep: true },
);
let cursor: number | undefined,
  generation = 0;
const identity = () =>
  JSON.stringify([
    accountSessionRevision.value,
    props.connection.connection_id,
    props.connection.revision,
    props.session.session_id,
  ]);
async function load(older: boolean) {
  if (loading.value) return;
  const target = identity(),
    current = generation;
  loading.value = true;
  error.value = "";
  if (!older) {
    messages.value = [];
    hasMore.value = false;
    cursor = undefined;
  }
  try {
    const response = await remoteConnectionsApi.history(
      props.connection.connection_id,
      props.session.session_id,
      older ? cursor : undefined,
    );
    if (identity() !== target || current !== generation) return;
    const page = response.data.data;
    const rows = page.history.map((row) => normalizeRecord(row));
    messages.value = older ? [...rows, ...messages.value] : rows;
    cursor = page.next_before_id ?? undefined;
    hasMore.value = page.has_more && cursor !== undefined;
    if (!older)
      await nextTick(() => {
        if (historyEl.value)
          historyEl.value.scrollTop = historyEl.value.scrollHeight;
      });
  } catch (err: any) {
    if (identity() !== target || current !== generation) return;
    // Any failed admission clears the transcript, including previously loaded pages.
    messages.value = [];
    hasMore.value = false;
    cursor = undefined;
    error.value = err.response?.data?.data?.code || "remote_unavailable";
    emit("unavailable");
  } finally {
    if (identity() === target && current === generation) loading.value = false;
  }
}
async function subscribe(target: string, current: number) {
  const abort = new AbortController();
  controller = abort;
  try {
    const response = await remoteConnectionsApi.events(
      props.connection.connection_id,
      props.session.session_id,
      props.connection.revision,
      abort.signal,
    );
    if (!response.ok || !response.body) throw new Error("remote_offline");
    await readSseStream(
      response.body,
      (event) => {
        if (
          identity() !== target ||
          generation !== current ||
          abort.signal.aborted
        )
          return;
        if (event.type === "access_error") throwAccess(event.code);
        if (event.type !== "snapshot") return;
        const state = event.data;
        if (
          !Array.isArray(state?.history) ||
          !Array.isArray(state?.active_runs)
        ) {
          throwAccess("remote_protocol_error");
          return;
        }
        const rows = state.history.map((row: ChatRecord) =>
          normalizeRecord(row),
        );
        const first = rows[0]?.id;
        messages.value = [
          ...messages.value.filter(
            (row) => first !== undefined && (row.id ?? 0) < first,
          ),
          ...rows,
        ];
        for (const run of state.active_runs) {
          const row = messages.value.find(
            (row) => row.id === run.history_record_id,
          );
          if (row)
            Object.assign(
              row,
              normalizeRecord({ ...row, content: run.content }, true),
            );
        }
        if (cursor === undefined) {
          cursor = first;
          hasMore.value = state.has_more;
        }
        liveMessages.value = state.active_runs
          .filter(
            (run: any) =>
              !run.history_record_id ||
              !messages.value.some(
                (row: ChatRecord) => row.id === run.history_record_id,
              ),
          )
          .map((run: any, index: number) =>
            normalizeRecord(
              {
                id: `remote-run:${run.run_id || index}`,
                content: run.content || { type: "bot", message: [] },
              },
              true,
            ),
          );
        running.value = state.is_running || state.active_runs.length > 0;
        ready.value = true;
        error.value = "";
      },
      abort.signal,
    );
    if (!abort.signal.aborted) throw new Error("remote_offline");
  } catch {
    if (abort.signal.aborted || identity() !== target || generation !== current)
      return;
    throwAccess("remote_offline");
  }
}
function throwAccess(code: string) {
  error.value = code;
  ready.value = false;
  messages.value = [];
  liveMessages.value = [];
  cursor = undefined;
  hasMore.value = false;
  controller?.abort();
  emit("unavailable");
  if (code === "remote_offline") {
    const target = identity(),
      current = generation;
    clearTimeout(retry);
    retry = setTimeout(() => {
      if (identity() === target && generation === current)
        void subscribe(target, current);
    }, 1500);
  }
}
async function refresh() {
  const target = identity(),
    current = generation;
  controller?.abort();
  clearTimeout(retry);
  ready.value = false;
  await load(false);
  if (!error.value && identity() === target && generation === current)
    void subscribe(target, current);
  else if (
    error.value === "remote_offline" &&
    identity() === target &&
    generation === current
  )
    throwAccess("remote_offline");
}
async function send() {
  if (
    !ready.value ||
    sending.value ||
    uncertain.value ||
    uploading.value ||
    (!draft.value.trim() && !attachments.value.length)
  )
    return;
  if (navigator.onLine === false) {
    error.value = "remote_offline";
    return;
  }
  const target = identity(),
    current = generation,
    text = draft.value;
  // getRandomValues also works on HTTP LAN origins without randomUUID support.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  requestId.value = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(
    12,
    16,
  )}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  sending.value = true;
  // Persist uncertainty before network I/O, including page closure during admission.
  uncertain.value = true;
  try {
    const response = await remoteConnectionsApi.send(
      props.connection.connection_id,
      props.session.session_id,
      text,
      props.connection.revision,
      requestId.value,
      attachments.value.map((file) => file.attachment_id),
    );
    if (identity() !== target || generation !== current) return;
    const status = response.data.data.status;
    if (status === "accepted") {
      draft.value = "";
      attachments.value = [];
      uncertain.value = false;
    } else if (status === "rejected") {
      uncertain.value = false;
      error.value = "remote_rejected";
    }
  } catch (err: any) {
    if (identity() !== target || generation !== current) return;
    const code = err.response?.data?.data?.code;
    uncertain.value = ![
      "remote_offline",
      "remote_unauthorized",
      "remote_disabled",
      "remote_unavailable",
      "remote_rejected",
    ].includes(code);
    error.value = uncertain.value ? "remote_acceptance_unknown" : code;
  } finally {
    if (identity() === target && generation === current) sending.value = false;
  }
}
async function checkReceipt() {
  const target = identity(),
    current = generation;
  try {
    const response = await remoteConnectionsApi.receipt(
      props.connection.connection_id,
      props.session.session_id,
      props.connection.revision,
      requestId.value,
    );
    if (identity() !== target || current !== generation) return;
    const status = response.data.data.status;
    if (status === "accepted") {
      draft.value = "";
      attachments.value = [];
      uncertain.value = false;
    } else if (status === "rejected") {
      uncertain.value = false;
      error.value = "remote_rejected";
    }
  } catch {
    /* Keep uncertain input locked until an authoritative receipt is available. */
  }
}
async function stop() {
  const target = identity(),
    current = generation;
  stopping.value = true;
  try {
    await remoteConnectionsApi.stop(
      props.connection.connection_id,
      props.session.session_id,
      props.connection.revision,
    );
  } catch (err: any) {
    if (identity() === target && generation === current)
      error.value = err.response?.data?.data?.code || "remote_offline";
  } finally {
    if (identity() === target && generation === current) stopping.value = false;
  }
}
watch(
  identity,
  () => {
    generation += 1;
    controller?.abort();
    clearTimeout(retry);
    const saved = localStorage.getItem(draftKey());
    let value: any = {};
    try {
      value = saved ? JSON.parse(saved) : {};
    } catch {
      /* Ignore invalid local drafts. */
    }
    restoring = true;
    draft.value = value.text || "";
    attachments.value = Array.isArray(value.attachments)
      ? value.attachments
      : [];
    uploading.value = false;
    uncertain.value = value.uncertain === true;
    requestId.value = value.requestId || "";
    restoring = false;
    liveMessages.value = [];
    running.value = false;
    sending.value = false;
    stopping.value = false;
    messages.value = [];
    hasMore.value = false;
    loading.value = false;
    void refresh();
  },
  { immediate: true, flush: "sync" },
);
onBeforeUnmount(() => {
  generation += 1;
  controller?.abort();
  clearTimeout(retry);
  messages.value = [];
});
</script>
<style scoped src="./chatComposer.css"></style>
<style scoped>
.remote-conversation {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  width: 100%;
  overflow: hidden;
}
.remote-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 24px;
  border-bottom: 1px solid
    var(--chat-border, rgba(var(--v-theme-on-surface), 0.08));
}
.remote-header__identity {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.remote-header strong {
  font-size: 0.9rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.remote-connection-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  padding: 4px 8px;
  border-radius: 6px;
  background: rgba(var(--v-theme-on-surface), 0.04);
  color: rgba(var(--v-theme-on-surface), 0.65);
  font-size: 0.75rem;
}
.remote-status-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: rgba(var(--v-theme-on-surface), 0.35);
  margin-right: 5px;
}
.remote-status-dot.connected {
  background: #30c785;
}
.remote-alert,
.remote-uncertain {
  width: var(--chat-content-width, 80%);
  max-width: var(--chat-content-max-width, 960px);
  margin: 8px auto;
  flex-shrink: 0;
}
.remote-history {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-anchor: none;
  scrollbar-gutter: stable;
  padding: 24px 0 0;
}
.messages-list-shell {
  width: var(--chat-content-width, 80%);
  max-width: var(--chat-content-max-width, 960px);
  margin: 0 auto;
}
.remote-composer {
  container-type: inline-size;
  flex-shrink: 0;
  padding: 0 16px 16px;
}
.remote-composer-status {
  width: var(--chat-content-width, 80%);
  max-width: var(--chat-content-max-width, 960px);
  margin: 0 auto;
  padding: 6px;
  font-size: 0.75rem;
  color: rgba(var(--v-theme-on-surface), 0.6);
}
.remote-model-hint {
  font-size: 0.75rem;
  color: rgba(var(--v-theme-on-surface), 0.56);
}
.remote-file-input {
  display: none;
}
.remote-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 8px 12px 0;
}
.remote-attachment {
  display: flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 10px;
  padding-left: 8px;
  font-size: 0.8rem;
}
.remote-attachment span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
@media (max-width: 768px) {
  .messages-list-shell,
  .remote-composer-status,
  .remote-alert,
  .remote-uncertain {
    width: calc(100% - 24px);
  }
  .remote-header {
    padding: 8px 12px;
  }
  .remote-header__identity {
    gap: 6px;
  }
  .remote-composer {
    padding: 0;
  }
  .input-container {
    width: calc(100% - 20px) !important;
  }
  .remote-model-hint {
    max-width: 45vw;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}
</style>
