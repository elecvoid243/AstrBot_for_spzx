<template>
  <section class="remote-conversation">
    <header class="pa-4">
      <strong
        >{{ connection.name }} ·
        {{ session.display_name || session.session_id }}</strong
      >
      <div class="text-caption">
        {{ connection.base_url }} · {{ tm("remoteConnections.execution") }}
      </div>
      <v-btn variant="text" size="small" @click="refresh">{{
        tm("remoteConnections.refresh")
      }}</v-btn>
    </header>
    <v-alert v-if="error" type="error" variant="tonal" class="ma-4">{{
      tm(`remoteConnections.status.${error}`)
    }}</v-alert>
    <v-progress-linear v-if="loading" indeterminate />
    <div ref="historyEl" class="remote-history">
      <ChatMessageList
        :messages="[...messages, ...liveMessages]"
        :is-dark="isDark"
        remote-history
        :history-has-more="hasMore"
        :history-loading-older="loading"
        @load-older="load(true)"
      />
    </div>
    <form class="pa-4 remote-composer" @submit.prevent="send">
      <v-alert v-if="uncertain" type="warning" variant="tonal" class="mb-2">
        {{ tm("remoteConnections.unknown") }}
        <v-btn
          variant="text"
          :disabled="!ready || sending"
          @click="checkReceipt"
          >{{ tm("remoteConnections.checked") }}</v-btn
        >
      </v-alert>
      <div role="status" class="text-caption mb-2">
        {{
          tm(running ? "remoteConnections.running" : "remoteConnections.idle")
        }}
      </div>
      <v-textarea
        v-model="draft"
        :label="tm('remoteConnections.message')"
        rows="2"
        auto-grow
        hide-details
        :disabled="sending || uncertain"
      />
      <div class="d-flex ga-2 mt-2">
        <v-btn
          type="submit"
          variant="tonal"
          :disabled="!ready || sending || uncertain || !draft.trim()"
          >{{
            tm(
              running ? "remoteConnections.followUp" : "remoteConnections.send",
            )
          }}</v-btn
        >
        <v-btn
          v-if="running"
          variant="text"
          :disabled="!ready || stopping"
          @click="stop"
          >{{ tm("remoteConnections.stop") }}</v-btn
        >
      </div>
    </form>
  </section>
</template>
<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from "vue";
import ChatMessageList from "./ChatMessageList.vue";
import {
  remoteConnectionsApi,
  type RemoteConnection,
  type RemoteSession,
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
  [draft, uncertain, requestId],
  () => {
    if (!restoring)
      localStorage.setItem(
        draftKey(),
        JSON.stringify({
          text: draft.value,
          uncertain: uncertain.value,
          requestId: requestId.value,
        }),
      );
  },
  { flush: "sync" },
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
    const rows = page.history.map((row) => ({
      ...row,
      content: {
        ...row.content,
        type: row.content?.type || (row.sender_id === "bot" ? "bot" : "user"),
        message: normalizeMessageParts(
          row.content?.message || [],
        ) as MessagePart[],
      },
    }));
    messages.value = older ? [...rows, ...messages.value] : rows;
    cursor = page.next_before_id ?? undefined;
    hasMore.value = page.has_more && cursor !== undefined;
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
        const rows = state.history.map((row: ChatRecord) => ({
          ...row,
          content: {
            ...row.content,
            message: normalizeMessageParts(
              row.content?.message || [],
            ) as MessagePart[],
          },
        }));
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
            row.content = {
              ...run.content,
              type: "bot",
              message: normalizeMessageParts(run.content?.message || []),
            };
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
          .map((run: any, index: number) => ({
            id: -(index + 1),
            content: {
              ...run.content,
              type: "bot",
              message: normalizeMessageParts(run.content?.message || []),
            },
            isStreaming: true,
          }));
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
  if (!ready.value || sending.value || uncertain.value || !draft.value.trim())
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
    );
    if (identity() !== target || generation !== current) return;
    const status = response.data.data.status;
    if (status === "accepted") {
      draft.value = "";
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
<style scoped>
.remote-conversation {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  width: 100%;
}
.remote-history {
  flex: 1;
  overflow: auto;
  padding: 16px;
}
header {
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
</style>
