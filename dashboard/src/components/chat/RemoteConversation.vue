<template>
  <section class="remote-conversation">
    <header class="pa-4">
      <strong
        >{{ connection.name }} ·
        {{ session.display_name || session.session_id }}</strong
      >
      <div class="text-caption">
        {{ connection.base_url }} · {{ tm("remoteConnections.readOnly") }}
      </div>
      <v-btn variant="text" size="small" @click="load(false)">{{
        tm("remoteConnections.refresh")
      }}</v-btn>
    </header>
    <v-alert v-if="error" type="error" variant="tonal" class="ma-4">{{
      tm(`remoteConnections.status.${error}`)
    }}</v-alert>
    <v-progress-linear v-if="loading" indeterminate />
    <div class="remote-history">
      <ChatMessageList
        :messages="messages"
        :is-dark="isDark"
        remote-history
        :history-has-more="hasMore"
        :history-loading-older="loading"
        @load-older="load(true)"
      />
    </div>
  </section>
</template>
<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
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
watch(
  identity,
  () => {
    generation += 1;
    messages.value = [];
    hasMore.value = false;
    loading.value = false;
    void load(false);
  },
  { immediate: true, flush: "sync" },
);
onBeforeUnmount(() => {
  generation += 1;
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
