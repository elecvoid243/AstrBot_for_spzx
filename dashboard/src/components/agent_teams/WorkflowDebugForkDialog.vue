<template>
  <v-dialog :model-value="true" :persistent="starting" max-width="820" scrollable @update:model-value="value => !value && emit('close')">
    <v-card data-testid="debug-fork-preview">
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{ tm("debugFork.preview") }}</v-card-title>
      <v-card-text>
        <v-btn-toggle
          :model-value="mode"
          :disabled="loading || starting"
          mandatory
          density="compact"
          variant="outlined"
          class="mb-4"
          :aria-label="tm('debugFork.definitionChoice')"
          @update:model-value="value => loadPreview(value)"
        >
          <v-btn value="source" variant="text" data-testid="debug-fork-use-source">{{ tm("debugFork.sourceDefinition") }}</v-btn>
          <v-btn value="draft" variant="text" data-testid="debug-fork-use-draft">{{ tm("debugFork.currentDraft") }}</v-btn>
        </v-btn-toggle>
        <v-progress-linear v-if="loading" indeterminate />
        <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
        <template v-if="plan">
          <p class="font-weight-medium">{{ plan.source.workflow_name }} · {{ version }} · {{ plan.source.node_name }}</p>
          <p class="text-body-2 mb-2" data-testid="debug-fork-scope">{{ tm("debugFork.singleNode") }}</p>
          <p class="text-caption text-medium-emphasis mb-3 debug-attempt">
            <span v-if="plan.source.iteration != null">{{ tm("loops.iteration") }} {{ plan.source.iteration }} · </span>
            {{ tm("v2.attempt") }} {{ plan.source.attempt_id }}
          </p>
          <WorkflowDebugForkDraftDetails v-if="plan.definition" :definition="plan.definition" />
          <v-alert v-if="plan.issues?.length" type="warning" variant="tonal" class="mb-3" data-testid="debug-fork-diagnostics">
            <ul class="pl-4 debug-diagnostics">
              <li v-for="(issue, index) in plan.issues" :key="index">
                {{ issue.path }} · {{ bindingIssue(issue) }}
              </li>
            </ul>
          </v-alert>
          <WorkflowDebugForkDraftBindings
            v-if="plan.definition && plan.inputs.some(port => port.available_sources)"
            :inputs="plan.inputs"
            :disabled="starting || loading"
            @update="draftBindings = $event"
          />
          <v-btn
            v-if="mode === 'draft' && dirtyBindings"
            variant="tonal"
            class="mb-3"
            :disabled="!draftBindings || starting || loading || draftStale"
            data-testid="debug-fork-check-bindings"
            @click="loadPreview('draft', true)"
          >{{ tm("debugFork.checkBindings") }}</v-btn>
          <v-select
            v-if="mode === 'source' && editablePorts.length"
            v-model="field"
            :items="editablePorts"
            :label="tm('debugFork.replace')"
            variant="outlined"
            density="compact"
            clearable
            :disabled="starting"
          />
          <v-textarea
            v-if="mode === 'source' && selectedPort"
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
        <v-btn v-if="mode === 'draft'" variant="text" :disabled="starting || loading" @click="loadPreview('draft')">
          {{ tm("debugFork.refreshDraft") }}
        </v-btn>
        <v-spacer />
        <v-btn variant="text" :disabled="starting" @click="emit('close')">{{ tm("debugFork.close") }}</v-btn>
        <v-btn
          color="primary"
          variant="tonal"
          :loading="starting"
          :disabled="!canStart"
          data-testid="start-debug-fork"
          @click="start"
        >{{ tm("debugFork.start") }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, onScopeDispose, ref, shallowRef, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { WorkflowDebugForkAttempt, WorkflowDebugForkBindings, WorkflowDebugForkPreview, WorkflowDebugForkRequest } from "@/api/generated/openapi-v1";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";
import WorkflowDebugForkInputs from "./WorkflowDebugForkInputs.vue";
import WorkflowDebugForkDraftBindings from "./WorkflowDebugForkDraftBindings.vue";
import WorkflowDebugForkDraftDetails from "./WorkflowDebugForkDraftDetails.vue";

const props = defineProps<{ rootId: string; attempt: WorkflowDebugForkAttempt }>();
const emit = defineEmits<{ close: [] }>();
const { tm } = useModuleI18n("features/agent-teams");
const router = useRouter();
const route = useRoute();
const controller = new AbortController();
onScopeDispose(() => controller.abort());

type DefinitionMode = NonNullable<WorkflowDebugForkAttempt["definition_mode"]>;
const plan = shallowRef<WorkflowDebugForkPreview>();
const mode = ref<DefinitionMode>("source");
const draftBindings = shallowRef<WorkflowDebugForkBindings>();
const checkedBindings = shallowRef<WorkflowDebugForkBindings>({});
const draftStale = ref(false);
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
const dirtyBindings = computed(() => JSON.stringify(draftBindings.value) !== JSON.stringify(checkedBindings.value));
const canStart = computed(() => !!plan.value && !loading.value && !starting.value &&
  (mode.value === "source" ? !invalidJson.value : !!plan.value.ready && !dirtyBindings.value && !draftStale.value),
);
const previewInputs = computed(() => (plan.value?.inputs ?? []).map(port =>
  replacement.value?.name === port.name
    ? { ...port, value: replacement.value.value, changed: true }
    : port,
));

function bindingIssue(issue: { path: string; code: string; message: string }) {
  const port = plan.value?.inputs.find(input => issue.path === `inputs.${input.name}`);
  if (!port || issue.code !== "required") return issue.message;
  return tm(port.kind !== "reference" ? "debugFork.requiredBinding"
    : port.available_sources?.length ? "debugFork.requiredReference" : "debugFork.unavailableReference");
}

async function loadPreview(selection: DefinitionMode, checkBindings = false) {
  mode.value = selection;
  loading.value = true;
  error.value = "";
  requestId = undefined;
  if (!checkBindings) {
    plan.value = undefined;
    field.value = null;
    draftBindings.value = {};
    checkedBindings.value = {};
    draftStale.value = false;
  }
  const bindings = draftBindings.value ?? {};
  try {
    const result = await workflowAssetsApi.previewDebugFork(props.rootId, {
      ...props.attempt,
      ...(selection === "draft" ? {
        definition_mode: selection,
        draft_revision: checkBindings ? plan.value?.definition?.draft_revision : undefined,
        bindings,
      } : {}),
    }, controller.signal);
    if (!controller.signal.aborted) {
      plan.value = result;
      checkedBindings.value = bindings;
    }
  } catch (err) {
    if (!controller.signal.aborted) {
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
      draftStale.value = (err as { response?: { status?: number } })?.response?.status === 409;
    }
  } finally {
    loading.value = false;
  }
}

void loadPreview("source");

async function start() {
  if (!plan.value || !canStart.value) return;
  starting.value = true;
  error.value = "";
  requestId ??= globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  try {
    const { run_id } = await workflowAssetsApi.startDebugFork(props.rootId, {
      ...props.attempt,
      preview_token: plan.value.preview_token,
      request_id: requestId,
      ...(mode.value === "draft" ? {
        definition_mode: mode.value,
        draft_revision: plan.value.definition?.draft_revision,
        bindings: checkedBindings.value,
      } : { replacement: replacement.value }),
    });
    if (controller.signal.aborted) return;
    await router.push(route.name === "ChatRunConversation"
      ? { name: "ChatRunConversation", params: { runId: run_id } }
      : { name: "AgentTeams", query: { workflow_run: run_id } });
    emit("close");
  } catch (err) {
    if (!controller.signal.aborted) {
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
      draftStale.value = (err as { response?: { status?: number } })?.response?.status === 409;
    }
  } finally {
    starting.value = false;
  }
}
</script>

<style scoped>
.debug-attempt { overflow-wrap: anywhere; }
.debug-diagnostics { color: rgb(var(--v-theme-on-surface)); }
</style>
