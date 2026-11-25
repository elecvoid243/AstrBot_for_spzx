<template>
  <v-dialog :model-value="true" :persistent="starting" max-width="820" scrollable @update:model-value="value => !value && emit('close')">
    <v-card data-testid="debug-fork-preview">
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{ tm("debugFork.preview") }}</v-card-title>
      <v-card-text>
        <v-progress-linear v-if="loading" indeterminate />
        <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
        <template v-if="plan">
          <p class="font-weight-medium">{{ plan.source.workflow_name }} · {{ version }} · {{ plan.source.node_name }}</p>
          <p class="text-body-2 mb-2" data-testid="debug-fork-scope">{{ tm("debugFork.singleNode") }}</p>
          <p class="text-caption text-medium-emphasis mb-3 debug-attempt">
            <span v-if="plan.source.iteration != null">{{ tm("loops.iteration") }} {{ plan.source.iteration }} · </span>
            {{ tm("v2.attempt") }} {{ plan.source.attempt_id }}
          </p>
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
import type { WorkflowDebugForkAttempt, WorkflowDebugForkPreview, WorkflowDebugForkRequest } from "@/api/generated/openapi-v1";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";
import WorkflowDebugForkInputs from "./WorkflowDebugForkInputs.vue";

const props = defineProps<{ rootId: string; attempt: WorkflowDebugForkAttempt }>();
const emit = defineEmits<{ close: [] }>();
const { tm } = useModuleI18n("features/agent-teams");
const router = useRouter();
const route = useRoute();
const controller = new AbortController();
onScopeDispose(() => controller.abort());

const plan = ref<WorkflowDebugForkPreview>();
const loading = ref(true);
const starting = ref(false);
const error = ref("");
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

void workflowAssetsApi.previewDebugFork(props.rootId, props.attempt, controller.signal)
  .then(result => { if (!controller.signal.aborted) plan.value = result; })
  .catch(err => {
    if (!controller.signal.aborted) error.value = extractApiError(err, tm("errors.operationFailed")).message;
  })
  .finally(() => { loading.value = false; });

async function start() {
  if (!plan.value || invalidJson.value || starting.value) return;
  starting.value = true;
  error.value = "";
  requestId ??= globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  try {
    const { run_id } = await workflowAssetsApi.startDebugFork(props.rootId, {
      ...props.attempt,
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
</style>
