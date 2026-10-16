<template>
  <v-card variant="outlined" class="my-3" data-testid="human-todo">
    <v-card-text>
      <strong>{{ todo.question }}</strong>
      <p class="text-caption mt-2">
        {{ tm(`human.status.${todo.status}`)
        }}<span v-if="!restricted"> · {{ todo.task_run_id }}</span>
      </p>
      <p v-if="paused && todo.status === 'answered'" class="mt-2">
        {{ tm("human.savedWaiting") }}
      </p>
      <v-alert
        v-if="error"
        type="error"
        variant="tonal"
        class="my-2"
        role="alert"
        >{{ error }}</v-alert
      >
      <template v-if="todo.artifact_ref">
        <p class="mt-3">
          {{ todo.artifact_ref.artifact_id }} · v{{ todo.artifact_ref.version }}
        </p>
        <v-btn
          variant="text"
          :loading="reading"
          data-testid="human-preview"
          @click="readPreview"
          >{{ tm("human.preview") }}</v-btn
        >
        <pre v-if="preview" class="human-content">{{
          Object.keys(preview.content).length === 1 &&
          typeof preview.content.text === "string"
            ? preview.content.text
            : JSON.stringify(preview.content, null, 2)
        }}</pre>
        <div v-if="preview" class="d-flex flex-wrap ga-2">
          <v-btn
            v-for="file in preview.files"
            :key="file.file_id"
            variant="text"
            @click="readFile(file.file_id, false)"
            >{{ tm("v2.previewFile") }} · {{ file.filename }}</v-btn
          >
        </div>
        <template v-if="todo.status === 'approved' && !restricted">
          <v-btn
            variant="tonal"
            :loading="exporting"
            data-testid="human-export"
            @click="exportVersion"
            >{{ tm("human.export") }} · v{{ todo.artifact_ref.version }}</v-btn
          >
          <v-btn
            v-for="file in preview?.files ?? []"
            :key="file.file_id"
            variant="text"
            @click="readFile(file.file_id, true)"
            >{{ tm("v2.downloadFile") }} · {{ file.filename }}</v-btn
          >
        </template>
      </template>
      <template v-if="todo.status === 'pending' && !cancelled">
        <template v-if="todo.kind === 'question'">
          <v-select
            v-if="todo.constraints.choices.length"
            v-model="answer"
            :items="todo.constraints.choices"
            :label="tm('human.answer')"
            data-testid="human-answer"
          />
          <v-textarea
            v-else
            v-model="answer"
            :maxlength="todo.constraints.max_length"
            :counter="todo.constraints.max_length"
            :label="tm('human.answer')"
            data-testid="human-answer"
          />
        </template>
        <v-textarea
          v-model="feedback"
          :label="tm('human.feedback')"
          data-testid="human-feedback"
        />
        <div class="d-flex flex-wrap ga-2">
          <v-btn
            variant="tonal"
            :disabled="
              busy || (todo.kind === 'question' ? !answer.trim() : !preview)
            "
            data-testid="human-submit"
            @click="submit(todo.kind === 'question' ? 'answer' : 'approve')"
            >{{
              tm(todo.kind === "question" ? "human.submit" : "human.approve")
            }}</v-btn
          >
          <v-btn
            variant="text"
            :disabled="busy || !feedback.trim()"
            data-testid="human-reject"
            @click="submit('reject')"
            >{{ tm("human.reject") }}</v-btn
          >
        </div>
      </template>
      <pre v-if="todo.response" class="human-content">{{
        todo.response.answer ?? todo.response.feedback ?? tm("human.approve")
      }}</pre>
    </v-card-text>
    <v-dialog v-model="previewingFile" max-width="900">
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          tm("v2.previewFile")
        }}</v-card-title>
        <v-card-text>
          <pre
            v-if="fileText"
            class="human-content"
            data-testid="human-file-content"
            >{{ fileText }}</pre
          >
          <iframe
            v-else-if="fileUrl"
            :src="fileUrl"
            sandbox=""
            :title="tm('v2.previewFile')"
            style="width: 100%; height: 60vh; border: 0"
          />
        </v-card-text>
        <v-card-actions
          ><v-btn variant="text" @click="previewingFile = false">{{
            tm("v2.close")
          }}</v-btn></v-card-actions
        >
      </v-card>
    </v-dialog>
  </v-card>
</template>

<script setup lang="ts">
import { onUnmounted, ref } from "vue";
import { workflowAssetsApi } from "@/api/workflowAssets";
import type { HumanAnswerRequest } from "@/api/generated/openapi-v1";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";

const props = defineProps<{
  todo: any;
  paused: boolean;
  cancelled: boolean;
  busy: boolean;
  restricted?: boolean;
  previewLoader?: (todoId: string) => Promise<any>;
  fileLoader?: (todoId: string, fileId: string) => Promise<{ data: Blob }>;
}>();
const emit = defineEmits<{
  (event: "submit", todoId: string, payload: HumanAnswerRequest): void;
}>();
const { tm } = useModuleI18n("features/agent-teams");
const answer = ref("");
const feedback = ref("");
const preview = ref<any>(null);
const reading = ref(false);
const exporting = ref(false);
const error = ref("");
const previewingFile = ref(false);
const fileText = ref("");
const fileUrl = ref("");
const requests = new Map<string, string>();
let disposed = false;
let fileRead = 0;
const urls: string[] = [];

function submit(action: HumanAnswerRequest["action"]) {
  if (props.busy || props.cancelled || props.todo.status !== "pending") return;
  if (action === "approve" && !preview.value) return;
  const values = {
    action,
    answer: action === "answer" ? answer.value.trim() : null,
    feedback: feedback.value.trim() || null,
  };
  if (
    (action === "reject" && !values.feedback) ||
    (action === "answer" && !values.answer)
  )
    return;
  const key = JSON.stringify(values);
  if (!requests.has(key))
    requests.set(
      key,
      typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    );
  emit("submit", props.todo.todo_id, {
    request_id: requests.get(key)!,
    ...values,
  });
}

async function readPreview() {
  if (reading.value) return;
  reading.value = true;
  error.value = "";
  try {
    const artifact = props.previewLoader
      ? await props.previewLoader(props.todo.todo_id)
      : await workflowAssetsApi.artifact(
          props.todo.artifact_ref.artifact_id,
          props.todo.artifact_ref.version,
        );
    if (!disposed) preview.value = artifact;
  } catch (err) {
    if (!disposed)
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
  } finally {
    reading.value = false;
  }
}

async function exportVersion() {
  if (exporting.value) return;
  exporting.value = true;
  try {
    const artifact = await workflowAssetsApi.export(
      props.todo.artifact_ref.artifact_id,
      props.todo.artifact_ref.version,
    );
    if (disposed) return;
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(artifact, null, 2)], {
        type: "application/json",
      }),
    );
    urls.push(url);
    const link = document.createElement("a");
    link.href = url;
    link.download = `approved-${artifact.artifact_id}-v${artifact.version}.json`;
    link.click();
  } catch (err) {
    if (!disposed)
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
  } finally {
    exporting.value = false;
  }
}

async function readFile(fileId: string, download: boolean) {
  const read = download ? null : ++fileRead;
  try {
    const response = props.fileLoader
      ? await props.fileLoader(props.todo.todo_id, fileId)
      : await workflowAssetsApi.file(
          props.todo.artifact_ref.artifact_id,
          props.todo.artifact_ref.version,
          fileId,
          download,
        );
    if (disposed || (!download && read !== fileRead)) return;
    if (download) {
      const url = URL.createObjectURL(response.data);
      urls.push(url);
      const link = document.createElement("a");
      link.href = url;
      link.download =
        preview.value?.files.find((file: any) => file.file_id === fileId)
          ?.filename ?? fileId;
      link.click();
    } else {
      const content = response.data.type.startsWith("text/")
        ? await response.data.text()
        : "";
      if (disposed || read !== fileRead) return;
      fileText.value = content;
      fileUrl.value = content ? "" : URL.createObjectURL(response.data);
      if (fileUrl.value) urls.push(fileUrl.value);
      previewingFile.value = true;
    }
  } catch (err) {
    if (!disposed && (download || read === fileRead))
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
  }
}

onUnmounted(() => {
  disposed = true;
  urls.forEach((url) => URL.revokeObjectURL(url));
});
</script>

<style scoped>
.human-content {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 300px;
  overflow: auto;
  margin: 12px 0;
}
</style>
