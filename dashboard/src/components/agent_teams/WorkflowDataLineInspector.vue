<template>
  <section class="data-line" :aria-label="tm('inspection.title')" data-testid="workflow-data-line">
    <div class="d-flex align-center justify-space-between ga-3">
      <h3 class="text-subtitle-1 font-weight-bold">{{ tm("inspection.title") }}</h3>
      <v-btn variant="text" size="small" icon="mdi-close" :aria-label="tm('inspection.close')" @click="emit('close')" />
    </div>
    <label v-if="availableAttempts.length > 1" class="data-line-choice">
      {{ tm("v2.attempt") }}
      <select :value="attemptId ?? availableAttempts[0].attempt_id" data-testid="inspection-attempt" @change="attemptId = ($event.target as HTMLSelectElement).value">
        <option v-for="option in availableAttempts" :key="option.attempt_id" :value="option.attempt_id">
          {{ getRaw(`v2.states.${option.status}`) ?? option.status }} · {{ option.attempt_id.slice(0, 8) }}
        </option>
      </select>
    </label>
    <p v-if="loading" role="status" class="text-body-2">{{ tm("inspection.loading") }}</p>
    <p v-else-if="error" role="alert" class="text-body-2">{{ error }}</p>
    <template v-else-if="data">
      <p class="font-weight-medium data-line-text">
        {{ data.source.node_id === "__input" ? tm("graph.inputs") : (nodeNames[data.source.node_id] ?? data.source.node_id) }}.{{ data.source.port }}
        →
        {{ nodeNames[data.target.node_id] ?? data.target.node_id }}.{{ data.target.port }}
        <span v-if="portType" class="text-medium-emphasis"> · {{ portType }}</span>
      </p>
      <p class="text-caption data-line-text" data-testid="inspection-context">
        <span v-if="data.iteration != null">{{ tm("loops.iteration") }} {{ data.iteration }} · </span>
        {{ getRaw(`v2.states.${data.task_status}`) ?? data.task_status }} ·
        {{ tm("v2.attempt") }} {{ data.attempt_id }}
      </p>
      <p class="text-body-2 font-weight-medium mt-2" data-testid="inspection-read-status" role="status">
        {{ tm(`inspection.states.${data.read_status}`) }}
      </p>
      <pre v-if="data.read_status === 'read' || data.read_status === 'null'" class="data-line-value" data-testid="inspection-value">{{ JSON.stringify(data.value, null, 2) }}</pre>
      <ul v-if="data.evidence?.artifact || data.evidence?.source_run_id || data.evidence?.reference" class="data-line-evidence text-caption" data-testid="inspection-evidence">
        <li v-if="data.evidence.artifact">
          {{ tm("inspection.sourceArtifact") }} · {{ data.evidence.artifact.artifact_id }} / v{{ data.evidence.artifact.version }}
        </li>
        <li v-if="data.evidence.source_run_id">
          {{ tm("inspection.sourceRun") }} · {{ data.evidence.source_run_id }}
        </li>
        <li v-if="data.evidence.reference">
          {{ tm("inspection.reference") }} · {{ data.evidence.reference.artifact_id }} / v{{ data.evidence.reference.version }}
          <span v-if="data.evidence.reference.file_id"> · {{ data.evidence.reference.file_id }}</span>
        </li>
      </ul>
      <details v-if="data.upstream && !['read', 'null'].includes(data.read_status)" class="mt-3" data-testid="inspection-upstream">
        <summary>{{ tm("inspection.upstream") }}</summary>
        <p v-if="data.upstream.artifact" class="text-caption mt-1 data-line-text">
          {{ data.upstream.artifact.artifact_id }} / v{{ data.upstream.artifact.version }}
        </p>
        <pre class="data-line-value">{{ JSON.stringify(data.upstream.value, null, 2) }}</pre>
      </details>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { WorkflowDataLine } from "@/api/generated/openapi-v1";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";

const props = defineProps<{
  rootId: string;
  scopeId: string;
  taskId: string;
  inputName: string;
  currentAttempt?: string;
  initialAttempt?: string;
  taskStatus?: string;
  executionId?: string;
  upstreamState?: string;
  nodeNames: Record<string, string>;
  signal?: AbortSignal;
}>();
const emit = defineEmits<{ close: [] }>();
const { tm, getRaw } = useModuleI18n("features/agent-teams");
const data = ref<WorkflowDataLine>();
const loading = ref(false);
const error = ref("");
const attempts = ref<{ taskId: string; options: WorkflowDataLine["attempts"] }>();
const availableAttempts = computed(() => attempts.value?.taskId === props.taskId ? attempts.value.options : []);
const attemptId = ref(props.initialAttempt);
const portType = computed(() => {
  const target = data.value?.target;
  if (!target) return undefined;
  if (target.format) return getRaw(`graph.portFormats.${target.format}`) ?? target.format;
  return Array.isArray(target.type) ? target.type.join(" | ") : target.type;
});

watch(() => [props.taskId, props.initialAttempt] as const, () => {
  attemptId.value = props.initialAttempt;
}, { flush: "sync" });
watch(
  () => [props.rootId, props.scopeId, props.taskId, props.inputName, attemptId.value ?? props.currentAttempt, props.signal, props.taskStatus, props.executionId, props.upstreamState] as const,
  async ([root, scope, task, input, attempt, signal], _previous, onCleanup) => {
    data.value = undefined;
    error.value = "";
    if (signal?.aborted) return;
    loading.value = true;
    const request = new AbortController();
    const abort = () => request.abort();
    signal?.addEventListener("abort", abort, { once: true });
    onCleanup(() => {
      signal?.removeEventListener("abort", abort);
      abort();
    });
    try {
      const result = await workflowAssetsApi.dataLine(root, {
        scope_run_id: scope, task_run_id: task, input_name: input, attempt_id: attempt,
      }, request.signal);
      if (request.signal.aborted) return;
      attempts.value = { taskId: task, options: result.attempts };
      data.value = result;
    } catch (err) {
      if (!request.signal.aborted) error.value = extractApiError(err, tm("errors.operationFailed")).message;
    } finally {
      if (!request.signal.aborted) loading.value = false;
    }
  },
  { immediate: true },
);
</script>

<style scoped>
.data-line {
  min-width: 0;
  overflow: auto;
  padding: 10px 14px 14px;
  border-inline-start: 1px solid rgba(var(--v-theme-primary), 0.25);
  background: rgb(var(--v-theme-surface));
}
.data-line-value {
  max-height: 16rem;
  overflow: auto;
  white-space: pre-wrap;
  padding: 8px;
  border-radius: 6px;
  background: rgba(var(--v-theme-on-surface), 0.05);
}
.data-line-text, .data-line-value, .data-line-evidence {
  overflow-wrap: anywhere;
}
.data-line-evidence {
  padding-inline-start: 18px;
  margin-top: 8px;
}
.data-line-choice {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 0.875rem;
}
select {
  max-width: 100%;
  appearance: auto;
  color-scheme: light dark;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.3);
  border-radius: 6px;
  padding: 4px 8px;
}
summary { cursor: pointer; }
</style>
