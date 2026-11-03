<template>
  <v-card variant="outlined" class="my-3" data-testid="human-todo">
    <v-card-text>
      <div v-if="dynamic && todo.choice_spec?.title" class="text-overline">
        {{ todo.choice_spec.title }}
      </div>
      <strong>{{ todo.question }}</strong>
      <MarkdownView
        v-if="dynamic && todo.choice_spec?.extra_content"
        class="mt-3"
        :source="todo.choice_spec.extra_content"
      />
      <p
        v-if="!restricted && (todo.run_name || todo.task_name)"
        class="text-body-2 mt-2"
        data-testid="human-origin"
      >
        {{ tm("human.run") }}: {{ todo.run_name }} · {{ tm("human.task") }}:
        {{ todo.task_name }}
      </p>
      <p class="text-caption mt-2">
        {{ tm(`human.status.${todo.status}`) }}
      </p>
      <details v-if="!restricted" class="text-caption mt-2">
        <summary>{{ tm('workbench.identifiers') }}</summary>
        <p class="human-content">{{ todo.root_run_id }} / {{ todo.task_run_id }}</p>
      </details>
      <div class="text-caption mt-2" data-testid="human-deadline">
        <p>
          {{ tm("humanWait.deadline") }}:
          {{
            todo.deadline == null
              ? tm("humanWait.unlimited")
              : new Date(todo.deadline * 1000).toLocaleString()
          }}
        </p>
        <p>
          {{ tm("humanWait.action") }}:
          {{
            tm(
              todo.human_wait?.timeout_action === "use_recommendation"
                ? "humanWait.recommendation"
                : "humanWait.wait",
            )
          }}
        </p>
        <p v-if="todo.timeout_reason">
          {{ tm(`humanWait.${todo.timeout_reason}`) }}
        </p>
        <p v-if="todo.decision_source?.type">
          {{
            tm(
              todo.decision_source.type === "recommendation"
                ? "humanWait.decisionRecommendation"
                : "humanWait.decisionHuman",
            )
          }}
        </p>
      </div>
      <div
        v-if="todo.recommendation"
        class="mt-2"
        data-testid="human-recommendation"
      >
        <strong
          >{{
            tm(
              todo.kind === "approval"
                ? "human.recommendedAction"
                : "human.recommendation",
            )
          }}:
          {{
            todo.kind === "approval" && todo.recommendation.value === "approve"
              ? tm("human.approve")
              : todo.kind === "approval" &&
                todo.recommendation.value === "reject"
              ? tm("human.returnForRevision")
              : dynamic
              ? todo.choice_spec?.options?.find(
                  (option: any) => option.id === todo.recommendation.value,
                )?.label ?? todo.recommendation.value
              : todo.recommendation.value
          }}</strong
        >
        <p>{{ todo.recommendation.reason }}</p>
      </div>
      <p v-else-if="todo.recommendation_error" class="text-caption mt-2">
        {{ tm("human.recommendationMissing") }}
      </p>
      <p v-if="!restricted && todo.recommendation_ref" class="text-caption">
        {{ tm("human.recommendationSource") }}:
        {{ todo.recommendation_source_name }} ·
        {{ todo.recommendation_ref.artifact_id }} · v{{
          todo.recommendation_ref.version
        }}
        · {{ todo.recommendation_fields?.value }} /
        {{ todo.recommendation_fields?.reason }}
      </p>
      <p v-if="paused && todo.status === 'pending' && !cancelled" class="mt-2">
        {{ tm("human.canSavePaused") }}
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
        <template v-if="dynamic">
          <fieldset class="choice-list mt-4" :disabled="busy">
            <legend class="text-body-2">{{ tm("human.answer") }}</legend>
            <label
              v-for="option in todo.choice_spec?.options ?? []"
              :key="option.id"
              class="choice-option"
              :class="{ selected: answer === option.id }"
            >
              <input
                v-model="answer"
                type="radio"
                :value="option.id"
                :name="`choice-${todo.todo_id}`"
              />
              <span
                ><strong>{{ option.label }}</strong
                ><span v-if="option.description" class="choice-description">{{
                  option.description
                }}</span></span
              >
            </label>
            <label
              class="choice-option"
              :class="{ selected: answer === '__free_text__' }"
            >
              <input
                v-model="answer"
                type="radio"
                value="__free_text__"
                :name="`choice-${todo.todo_id}`"
              />
              <span>{{ tm("human.freeChoice") }}</span>
            </label>
          </fieldset>
          <v-textarea
            v-model="feedback"
            :maxlength="10000"
            :counter="10000"
            :label="
              tm(
                answer === '__free_text__'
                  ? 'human.freeChoice'
                  : 'human.optionalNote',
              )
            "
            :placeholder="todo.choice_spec?.input_placeholder"
            data-testid="human-choice-text"
          />
        </template>
        <template v-else-if="todo.kind === 'question'">
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
          v-if="!dynamic"
          v-model="feedback"
          :label="
            tm(
              todo.kind === 'approval'
                ? 'human.reworkFeedback'
                : 'human.feedback',
            )
          "
          data-testid="human-feedback"
        />
        <div class="d-flex flex-wrap ga-2">
          <v-btn
            variant="tonal"
            :disabled="
              busy ||
              (dynamic
                ? !answer || (answer === '__free_text__' && !feedback.trim())
                : todo.kind === 'question'
                ? !answer.trim()
                : !preview)
            "
            data-testid="human-submit"
            @click="submit(todo.kind === 'question' ? 'answer' : 'approve')"
            >{{
              tm(todo.kind === "question" ? "human.submit" : "human.approve")
            }}</v-btn
          >
          <v-btn
            v-if="!dynamic"
            variant="text"
            :disabled="
              busy || !feedback.trim() || (todo.kind === 'approval' && !preview)
            "
            data-testid="human-reject"
            @click="submit('reject')"
            >{{
              tm(
                todo.kind === "approval"
                  ? "human.returnForRevision"
                  : "human.reject",
              )
            }}</v-btn
          >
        </div>
      </template>
      <div
        v-if="dynamic && todo.response"
        class="human-content"
        data-testid="human-choice-response"
      >
        <strong>{{
          todo.response.choice_id === "__free_text__"
            ? tm("human.freeChoice")
            : todo.choice_spec?.options?.find(
                (option: any) => option.id === todo.response.choice_id,
              )?.label ?? todo.response.choice_id
        }}</strong>
        <p v-if="todo.response.free_text">{{ todo.response.free_text }}</p>
      </div>
      <pre v-else-if="todo.response" class="human-content">{{
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
import { computed, onUnmounted, ref, watch } from "vue";
import MarkdownView from "@/components/shared/MarkdownView.vue";
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
const dynamic = computed(
  () =>
    props.todo.source_kind === "tool_call" && props.todo.kind === "question",
);
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
let previewRead = 0;
watch(
  [
    () => props.todo.todo_id,
    () => props.todo.artifact_ref?.artifact_id,
    () => props.todo.artifact_ref?.version,
  ],
  () => {
    previewRead++;
    preview.value = null;
    error.value = "";
    reading.value = false;
    feedback.value = "";
    answer.value = "";
  },
);
const urls: string[] = [];

function submit(action: HumanAnswerRequest["action"]) {
  if (props.busy || props.cancelled || props.todo.status !== "pending") return;
  if (props.todo.kind === "approval" && !preview.value) return;
  if (
    dynamic.value &&
    (action !== "answer" ||
      !answer.value ||
      (answer.value === "__free_text__" && !feedback.value.trim()))
  )
    return;
  const values: Omit<HumanAnswerRequest, "request_id"> = dynamic.value
    ? {
        action: "answer",
        choice_id: answer.value,
        free_text: feedback.value.trim() || null,
      }
    : {
        action,
        answer: action === "answer" ? answer.value.trim() : null,
        feedback: feedback.value.trim() || null,
        artifact_ref:
          props.todo.kind === "approval" ? props.todo.artifact_ref : null,
      };
  if (
    (action === "reject" && !values.feedback) ||
    (!dynamic.value && action === "answer" && !values.answer)
  )
    return;
  const key = JSON.stringify([props.todo.todo_id, values]);
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
  const read = ++previewRead;
  try {
    const artifact = props.previewLoader
      ? await props.previewLoader(props.todo.todo_id)
      : await workflowAssetsApi.artifact(
          props.todo.artifact_ref.artifact_id,
          props.todo.artifact_ref.version,
        );
    if (!disposed && read === previewRead) preview.value = artifact;
  } catch (err) {
    if (!disposed && read === previewRead)
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
  } finally {
    if (read === previewRead) reading.value = false;
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
.choice-list {
  border: 0;
  padding: 0;
}
.choice-option {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 12px;
  margin: 8px 0;
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 8px;
  cursor: pointer;
}
.choice-option.selected {
  border-color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.06);
}
.choice-option input {
  margin-top: 4px;
  accent-color: rgb(var(--v-theme-primary));
}
.choice-description {
  display: block;
  opacity: 0.75;
  margin-top: 3px;
}
.human-content {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 300px;
  overflow: auto;
  margin: 12px 0;
}
</style>
