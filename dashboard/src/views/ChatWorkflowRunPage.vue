<template>
  <main class="chat-run-page pa-4 pa-sm-6" data-testid="chat-run-page">
    <v-card variant="outlined">
      <v-card-title class="d-flex align-center ga-3 pa-6">
        <span class="text-h4">{{ tm("chatRun.title") }}</span>
        <v-spacer />
        <LanguageSwitcher />
      </v-card-title>
      <v-card-text>
        <p class="text-caption mb-3">{{ runId }}</p>
        <p class="mb-4">{{ tm("chatRun.scope") }}</p>
        <v-alert
          v-if="error"
          type="error"
          variant="tonal"
          class="mb-4"
          role="alert"
          >{{ error }}</v-alert
        >
        <v-alert
          v-if="connection && !run"
          type="info"
          variant="tonal"
          class="mb-4"
        >
          <p>{{ tm("chatRun.confirm") }}</p>
          <code
            class="chat-command mt-3"
            data-testid="chat-connection-command"
            >{{ connection.command }}</code
          >
          <p class="text-caption mt-3">{{ tm("chatRun.expiry") }}</p>
        </v-alert>
        <div class="d-flex flex-wrap ga-2 mb-4">
          <v-btn
            v-if="!connection"
            variant="tonal"
            :loading="connecting"
            :disabled="!runId || busy"
            @click="connect"
            >{{ tm("chatRun.connect") }}</v-btn
          >
          <v-btn
            v-else-if="!['completed', 'cancelled'].includes(run?.status ?? '')"
            variant="text"
            data-testid="chat-refresh"
            :disabled="busy"
            @click="refresh(epoch)"
            >{{ tm("chatRun.refresh") }}</v-btn
          >
        </div>
        <template v-if="run">
          <p class="text-h6 mb-3" data-testid="chat-run-status">
            {{ tm("chatRun.status") }}: {{ tm(`v2.states.${run.status}`) }}
          </p>
          <WorkflowHumanTodo
            v-for="todo in run.todos"
            :key="todo.todo_id"
            :todo="todo"
            :paused="run.control_status === 'paused'"
            :cancelled="run.control_status === 'cancelled'"
            :busy="busy"
            restricted
            :preview-loader="
              (todoId) => chatRunApi.preview(runId, credential, todoId)
            "
            :file-loader="
              (todoId, fileId) =>
                chatRunApi.file(runId, credential, todoId, fileId)
            "
            @submit="submit"
          />
          <template v-if="run.result !== null && run.result !== undefined">
            <h2 class="text-h5 mt-5 mb-3">{{ tm("chatRun.result") }}</h2>
            <pre class="chat-result" data-testid="chat-final-result">{{
              JSON.stringify(run.result, null, 2)
            }}</pre>
          </template>
        </template>
      </v-card-text>
    </v-card>
  </main>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import {
  chatRunApi,
  type ChatRunConnection,
  type ChatRunView,
} from "@/api/chatRun";
import type { HumanAnswerRequest } from "@/api/generated/openapi-v1";
import WorkflowHumanTodo from "@/components/agent_teams/WorkflowHumanTodo.vue";
import LanguageSwitcher from "@/components/shared/LanguageSwitcher.vue";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";

const route = useRoute();
const { tm } = useModuleI18n("features/agent-teams");
const runId = computed(() =>
  typeof route.query.workflow_run === "string" ? route.query.workflow_run : "",
);
const connection = ref<ChatRunConnection | null>(null);
const credential = computed(() =>
  connection.value
    ? `${connection.value.session_id}.${connection.value.token}`
    : "",
);
const run = ref<ChatRunView | null>(null);
const error = ref("");
const connecting = ref(false);
const busy = ref(false);
let epoch = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
let disposed = false;

async function connect() {
  const current = ++epoch;
  clearTimeout(timer);
  connection.value = null;
  run.value = null;
  error.value = "";
  if (!runId.value) {
    error.value = tm("chatRun.missing");
    return;
  }
  connecting.value = true;
  try {
    const created = await chatRunApi.connection(runId.value);
    if (current !== epoch || disposed) return;
    connection.value = created;
    await refresh(current);
  } catch (err) {
    if (current === epoch && !disposed)
      error.value = extractApiError(err, tm("chatRun.failed")).message;
  } finally {
    if (current === epoch && !disposed) connecting.value = false;
  }
}

async function refresh(current: number) {
  clearTimeout(timer);
  if (
    current !== epoch ||
    disposed ||
    !connection.value ||
    busy.value ||
    ["completed", "cancelled"].includes(run.value?.status ?? "")
  )
    return;
  try {
    const state = await chatRunApi.status(runId.value, credential.value);
    if (current !== epoch || disposed) return;
    if (state.confirmed) {
      const updated = await chatRunApi.run(runId.value, credential.value);
      if (current !== epoch || disposed) return;
      run.value = updated;
    }
    error.value = "";
  } catch (err) {
    if (current !== epoch || disposed) return;
    error.value = extractApiError(err, tm("chatRun.failed")).message;
    run.value = null;
    connection.value = null;
  } finally {
    if (
      current === epoch &&
      !disposed &&
      connection.value &&
      !["completed", "cancelled"].includes(run.value?.status ?? "")
    ) {
      timer = setTimeout(() => {
        void refresh(current);
      }, 1500);
    }
  }
}

async function submit(todoId: string, payload: HumanAnswerRequest) {
  if (!connection.value || busy.value) return;
  // Invalidate a poll already in flight so it cannot restore an old pending view.
  const current = ++epoch;
  clearTimeout(timer);
  busy.value = true;
  error.value = "";
  try {
    const updated = await chatRunApi.answer(
      runId.value,
      credential.value,
      todoId,
      payload,
    );
    if (current === epoch && !disposed) run.value = updated;
  } catch (err) {
    if (current === epoch && !disposed)
      error.value = extractApiError(err, tm("chatRun.failed")).message;
  } finally {
    if (current === epoch && !disposed) {
      busy.value = false;
      void refresh(current);
    }
  }
}

watch(
  runId,
  () => {
    void connect();
  },
  { immediate: true },
);
onUnmounted(() => {
  disposed = true;
  epoch++;
  clearTimeout(timer);
});
</script>

<style scoped>
.chat-run-page {
  max-width: 960px;
  margin: 0 auto;
  width: 100%;
}
.chat-command {
  display: block;
  overflow-wrap: anywhere;
  user-select: all;
}
.chat-result {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
