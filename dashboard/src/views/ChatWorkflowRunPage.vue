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
          v-if="error || refreshError"
          type="error"
          variant="tonal"
          class="mb-4"
          role="alert"
          >{{ error || refreshError }}</v-alert
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
            v-else-if="!terminal"
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
            :busy="busy || checkingAnswer"
            restricted
            :preview-loader="
              (todoId) =>
                withConnection((id, token) =>
                  chatRunApi.preview(id, token, todoId),
                )
            "
            :file-loader="
              (todoId, fileId) =>
                withConnection((id, token) =>
                  chatRunApi.file(id, token, todoId, fileId),
                )
            "
            @submit="submit"
          />
          <template v-if="run.result !== null && run.result !== undefined">
            <h2 class="text-h5 mt-5 mb-3">{{ tm("chatRun.result") }}</h2>
            <pre class="chat-result" data-testid="chat-final-result">{{
              JSON.stringify(run.result, null, 2)
            }}</pre>
            <div
              v-for="file in run.result_files"
              :key="file.file_id"
              class="mt-3"
              data-testid="chat-result-file"
            >
              <p>{{ file.filename }} · v{{ file.version }}</p>
              <v-btn
                variant="text"
                :disabled="fileBusy"
                data-testid="chat-result-preview"
                @click="readResultFile(file, false)"
                >{{ tm("v2.previewFile") }}</v-btn
              >
              <v-btn
                variant="tonal"
                :disabled="fileBusy"
                data-testid="chat-result-download"
                @click="readResultFile(file, true)"
                >{{ tm("v2.downloadFile") }}</v-btn
              >
            </div>
            <pre
              v-if="fileText"
              class="chat-result mt-3"
              data-testid="chat-file-preview"
              >{{ fileText }}</pre
            >
            <p v-if="binaryPreview" class="mt-3">
              {{ tm("chatRun.binaryFile") }}
            </p>
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
  type ChatResultFile,
} from "@/api/chatRun";
import type { HumanAnswerRequest } from "@/api/generated/openapi-v1";
import WorkflowHumanTodo from "@/components/agent_teams/WorkflowHumanTodo.vue";
import LanguageSwitcher from "@/components/shared/LanguageSwitcher.vue";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";

type ChatRequestError = {
  response?: { status?: number; headers?: Record<string, string> };
};

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
const refreshError = ref("");
const checkingAnswer = ref(false);
const connecting = ref(false);
const busy = ref(false);
const fileBusy = ref(false);
const fileText = ref("");
const binaryPreview = ref(false);
const fileUrls: string[] = [];
let epoch = 0;
let answerRevision = 0;
let refreshing = false;
let timer: ReturnType<typeof setTimeout> | undefined;
let expiryTimer: ReturnType<typeof setTimeout> | undefined;
const terminal = computed(() =>
  ["completed", "cancelled"].includes(run.value?.status ?? ""),
);
const accessReasons = new Set([
  "expired",
  "invalid",
  "revoked",
  "identity_mismatch",
]);

function clearConnection() {
  epoch++;
  clearTimeout(timer);
  clearTimeout(expiryTimer);
  connection.value = null;
  run.value = null;
  connecting.value = false;
  refreshing = false;
  busy.value = false;
  checkingAnswer.value = false;
  fileBusy.value = false;
  fileText.value = "";
  binaryPreview.value = false;
  error.value = "";
  refreshError.value = "";
  for (const url of fileUrls.splice(0)) URL.revokeObjectURL(url);
}

async function withConnection<T>(
  request: (id: string, token: string) => Promise<T>,
): Promise<T> {
  const current = epoch;
  if (!connection.value) throw new Error(tm("chatRun.access.invalid"));
  try {
    const result = await request(runId.value, credential.value);
    if (current !== epoch) throw new Error(tm("chatRun.access.invalid"));
    return result;
  } catch (err) {
    const response = (err as ChatRequestError)?.response;
    const reason =
      response?.headers?.["x-astrbot-chat-access-error"] ??
      (response?.status === 401 ? "invalid" : "");
    if (current === epoch && accessReasons.has(reason)) {
      clearConnection();
      error.value = tm(`chatRun.access.${reason}`);
    }
    throw err;
  }
}

async function connect() {
  clearConnection();
  const current = epoch;
  if (!runId.value) {
    error.value = tm("chatRun.missing");
    return;
  }
  connecting.value = true;
  try {
    const created = await chatRunApi.connection(runId.value);
    if (current !== epoch) return;
    connection.value = created;
    expiryTimer = setTimeout(() => {
      clearConnection();
      error.value = tm("chatRun.access.expired");
    }, created.expires_in * 1000);
    await refresh(current);
  } catch (err) {
    if (current === epoch)
      error.value = extractApiError(err, tm("chatRun.failed")).message;
  } finally {
    if (current === epoch) connecting.value = false;
  }
}

async function refresh(current: number) {
  if (
    current !== epoch ||
    !connection.value ||
    busy.value ||
    refreshing ||
    terminal.value
  )
    return;
  clearTimeout(timer);
  refreshing = true;
  const revision = answerRevision;
  try {
    const state = await withConnection(chatRunApi.status);
    if (current !== epoch || revision !== answerRevision) return;
    if (state.confirmed) {
      const updated = await withConnection(chatRunApi.run);
      if (current !== epoch || revision !== answerRevision) return;
      run.value = updated;
      if (checkingAnswer.value) {
        checkingAnswer.value = false;
        error.value = "";
      }
    }
    refreshError.value = "";
  } catch (err) {
    if (current !== epoch) return;
    const status = (err as ChatRequestError)?.response?.status;
    if (!status || status === 408 || status === 429 || status >= 500) {
      if (revision === answerRevision) refreshError.value = tm("chatRun.retry");
    } else {
      clearConnection();
      error.value = extractApiError(err, tm("chatRun.failed")).message;
    }
  } finally {
    if (current === epoch) {
      refreshing = false;
      if (connection.value && !terminal.value) {
        timer = setTimeout(() => {
          void refresh(current);
        }, 1500);
      }
    }
  }
}

async function submit(todoId: string, payload: HumanAnswerRequest) {
  if (!connection.value || busy.value || checkingAnswer.value) return;
  const current = epoch;
  // An earlier poll may complete after the answer has changed the todo.
  answerRevision++;
  clearTimeout(timer);
  busy.value = true;
  error.value = "";
  try {
    const updated = await withConnection((id, token) =>
      chatRunApi.answer(id, token, todoId, payload),
    );
    if (current === epoch) {
      run.value = updated;
      refreshError.value = "";
    }
  } catch (err) {
    if (current !== epoch) return;
    const status = (err as ChatRequestError)?.response?.status;
    if (!status || status === 408 || status >= 500) {
      checkingAnswer.value = true;
      error.value = tm("chatRun.checkingAnswer");
    } else {
      error.value = extractApiError(err, tm("chatRun.failed")).message;
    }
  } finally {
    if (current === epoch) {
      busy.value = false;
      void refresh(current);
    }
  }
}

async function readResultFile(file: ChatResultFile, download: boolean) {
  if (fileBusy.value || !connection.value) return;
  const current = epoch;
  fileBusy.value = true;
  error.value = "";
  fileText.value = "";
  binaryPreview.value = false;
  try {
    const response = await withConnection((id, token) =>
      chatRunApi.resultFile(id, token, file.file_id, download),
    );
    if (current !== epoch) return;
    if (download) {
      const url = URL.createObjectURL(response.data);
      fileUrls.push(url);
      const link = document.createElement("a");
      link.href = url;
      link.download = file.filename;
      link.click();
    } else if (response.data.type.startsWith("text/")) {
      const text = await response.data.text();
      if (current === epoch) fileText.value = text;
    } else {
      binaryPreview.value = true;
    }
  } catch (err) {
    if (current === epoch)
      error.value = extractApiError(err, tm("chatRun.failed")).message;
  } finally {
    if (current === epoch) fileBusy.value = false;
  }
}

watch(
  runId,
  () => {
    void connect();
  },
  { immediate: true },
);
onUnmounted(clearConnection);
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
