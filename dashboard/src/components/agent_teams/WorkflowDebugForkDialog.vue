<template>
  <v-dialog :model-value="true" :persistent="starting" max-width="820" scrollable @update:model-value="value => !value && emit('close')">
    <v-card data-testid="debug-fork-preview">
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{ tm("debugFork.preview") }}</v-card-title>
      <v-card-text>
        <label v-if="candidates && candidates.length > 1" class="d-block text-body-2 mb-3">
          {{ tm("debugFork.failedAttempt") }}
          <select
            class="debug-candidate d-block w-100 mt-1"
            :value="attempt.attempt_id"
            :disabled="starting"
            data-testid="debug-fork-candidate"
            @change="chooseCandidate(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="item in candidates" :key="item.attempt_id" :value="item.attempt_id">
              {{ item.node_name }} ·
              <template v-if="item.iteration != null">{{ tm("loops.iteration") }} {{ item.iteration }} · </template>
              {{ tm("v2.attempt") }} {{ item.attempt_id }}
            </option>
          </select>
        </label>
        <label v-if="!wholeChild" class="d-block text-body-2 mb-3">
          {{ tm("debugFork.scopeLabel") }}
          <select v-model="scope" class="debug-candidate d-block w-100 mt-1" :disabled="starting" data-testid="debug-fork-scope-select">
            <option value="single">{{ tm("debugFork.singleNode") }}</option>
            <option value="downstream">{{ tm("debugFork.downstream") }}</option>
          </select>
        </label>
        <v-progress-linear v-if="loading" indeterminate />
        <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
        <template v-if="plan">
          <p v-if="plan.source.parent_call" class="text-body-2 mb-2" data-testid="debug-fork-parent-context">
            {{ tm("debugFork.parentCall", { name: plan.source.parent_call.node_name }) }}
            <span v-if="plan.source.parent_call.iteration != null">
              · {{ tm("loops.iteration") }} {{ plan.source.parent_call.iteration }}
            </span>
          </p>
          <p class="font-weight-medium">{{ plan.source.workflow_name }} · {{ version }} · {{ plan.source.node_name }}</p>
          <p v-if="wholeChild" class="text-body-2 mb-2" data-testid="debug-fork-scope">
            {{ tm("debugFork.childWorkflow") }}
          </p>
          <template v-if="scope !== 'single'">
            <p class="text-caption text-medium-emphasis">{{ tm("debugFork.selectedNodes") }}</p>
            <p class="text-body-2 mb-2" data-testid="debug-fork-nodes">{{ plan.nodes?.map(node => node.name).join(" · ") }}</p>
          </template>
          <div v-if="plan.boundary?.length" class="mb-3" data-testid="debug-fork-boundary">
            <p class="text-caption text-medium-emphasis">{{ tm("debugFork.boundary") }}</p>
            <ul class="text-body-2 ps-5">
              <li v-for="node in plan.boundary" :key="node.id" :title="node.artifact?.artifact_id">
                {{ node.name }} · {{ getRaw(`v2.states.${node.status}`) ?? node.status }}
                <template v-if="node.artifact"> · {{ tm("debugFork.boundaryVersion", { version: node.artifact.version }) }}</template>
              </li>
            </ul>
          </div>
          <p class="text-caption text-medium-emphasis mb-3 debug-attempt">
            <span v-if="plan.source.iteration != null">{{ tm("loops.iteration") }} {{ plan.source.iteration }} · </span>
            {{ tm("v2.attempt") }} {{ plan.source.attempt_id }}
          </p>
          <p v-if="candidates && plan.original.reason" class="text-body-2 mb-3">{{ plan.original.reason }}</p>
          <v-select
            v-if="editablePorts.length"
            v-model="field"
            :items="editablePorts"
            :label="tm('debugFork.replace')"
            variant="outlined"
            density="compact"
            clearable
            :disabled="starting"
          />
          <v-textarea
            v-if="selectedPort"
            v-model="replacementText"
            :label="tm(selectedPort.kind === 'text' ? 'debugFork.replacementText' : 'debugFork.replacementJson')"
            variant="outlined"
            rows="3"
            :disabled="starting"
            :error-messages="invalidJson ? [tm('debugFork.invalidJson')] : []"
            data-testid="debug-fork-replacement"
          />
          <WorkflowDebugForkInputs :inputs="previewInputs" />
        </template>
      </v-card-text>
      <v-card-actions class="px-6 pb-4">
        <v-spacer />
        <v-btn variant="text" :disabled="starting" @click="emit('close')">{{ tm("debugFork.close") }}</v-btn>
        <v-btn
          color="primary"
          variant="tonal"
          :loading="starting"
          :disabled="!plan || invalidJson"
          data-testid="start-debug-fork"
          @click="start"
        >{{ tm("debugFork.start") }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { WorkflowDebugForkLocation, WorkflowDebugForkPreview, WorkflowDebugForkAttempt, WorkflowDebugForkRequest, WorkflowDebugForkScope } from "@/api/generated/openapi-v1";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";
import WorkflowDebugForkInputs from "./WorkflowDebugForkInputs.vue";

const props = defineProps<{
  rootId: string;
  attempt: Omit<WorkflowDebugForkAttempt, "scope">;
  candidates?: WorkflowDebugForkLocation[];
  wholeChild?: boolean;
}>();
const emit = defineEmits<{ close: []; selectCandidate: [candidate: WorkflowDebugForkLocation] }>();
const { tm, getRaw } = useModuleI18n("features/agent-teams");
const router = useRouter();
const route = useRoute();
const controller = new AbortController();
onScopeDispose(() => controller.abort());

const plan = ref<WorkflowDebugForkPreview>();
const loading = ref(true);
const starting = ref(false);
const error = ref("");
const scope = ref<WorkflowDebugForkScope>(props.wholeChild ? "child_workflow" : "single");
const field = ref<string | null>(null);
const replacementText = ref("");
let requestId: string | undefined;
const version = computed(() =>
  plan.value?.source.draft_revision == null
    ? tm("debugFork.version", { version: plan.value?.source.asset_version ?? '' })
    : tm("debugFork.revision", { revision: plan.value.source.draft_revision }),
);
const editablePorts = computed(() =>
  (plan.value?.inputs ?? [])
    .filter(port => port.kind !== "reference" && port.source === "recorded")
    .map(port => port.name),
);
const selectedPort = computed(() => plan.value?.inputs.find(port => port.name === field.value));
watch(selectedPort, port => {
  replacementText.value = port?.kind === "text"
    ? String(port.original ?? "")
    : JSON.stringify(port?.original, null, 2) ?? "";
});
watch([field, replacementText], () => { requestId = undefined; });

const replacement = computed<WorkflowDebugForkRequest["replacement"]>(() => {
  const port = selectedPort.value;
  if (!port) return null;
  try {
    return { name: port.name, value: port.kind === "text" ? replacementText.value : JSON.parse(replacementText.value) };
  } catch {
    return undefined;
  }
});
const invalidJson = computed(() => replacement.value === undefined);
const previewInputs = computed(() => (plan.value?.inputs ?? []).map(port =>
  replacement.value?.name === port.name
    ? { ...port, value: replacement.value.value, changed: true }
    : port,
));

function chooseCandidate(attemptId: string) {
  const selected = props.candidates?.find(item => item.attempt_id === attemptId);
  if (selected) emit("selectCandidate", selected);
}

watch(
  [() => props.rootId, () => props.attempt.scope_run_id, () => props.attempt.task_run_id, () => props.attempt.attempt_id, scope],
  ([rootId, scope_run_id, task_run_id, attempt_id, selectedScope], _, onCleanup) => {
    const read = new AbortController();
    onCleanup(() => read.abort());
    plan.value = undefined;
    field.value = null;
    requestId = undefined;
    error.value = "";
    loading.value = true;
    void workflowAssetsApi.previewDebugFork(rootId, { scope_run_id, task_run_id, attempt_id, scope: selectedScope }, read.signal)
      .then(result => { if (!read.signal.aborted) plan.value = result; })
      .catch(err => {
        if (!read.signal.aborted) error.value = extractApiError(err, tm("errors.operationFailed")).message;
      })
      .finally(() => { if (!read.signal.aborted) loading.value = false; });
  },
  { immediate: true },
);

async function start() {
  if (!plan.value || invalidJson.value || starting.value) return;
  starting.value = true;
  error.value = "";
  requestId ??= globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  try {
    const { run_id } = await workflowAssetsApi.startDebugFork(props.rootId, {
      scope_run_id: props.attempt.scope_run_id,
      task_run_id: props.attempt.task_run_id,
      attempt_id: props.attempt.attempt_id,
      scope: scope.value,
      preview_token: plan.value.preview_token,
      request_id: requestId,
      replacement: replacement.value,
    });
    if (controller.signal.aborted) return;
    await router.push(route.name === "ChatRunConversation"
      ? { name: "ChatRunConversation", params: { runId: run_id } }
      : { name: "AgentTeams", query: { workflow_run: run_id } });
    emit("close");
  } catch (err) {
    if (!controller.signal.aborted) error.value = extractApiError(err, tm("errors.operationFailed")).message;
  } finally {
    starting.value = false;
  }
}
</script>

<style scoped>
.debug-attempt { overflow-wrap: anywhere; }
.debug-candidate {
  appearance: auto;
  color-scheme: light dark;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.3);
  border-radius: 6px;
  padding: 4px 8px;
}
</style>
