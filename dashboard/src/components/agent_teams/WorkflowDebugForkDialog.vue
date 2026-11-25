<template>
  <v-dialog :model-value="true" :persistent="starting" max-width="820" scrollable @update:model-value="value => !value && emit('close')">
    <v-card data-testid="debug-fork-preview">
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{ tm("debugFork.preview") }}</v-card-title>
      <v-card-text>
        <v-progress-linear v-if="loading" indeterminate />
        <v-alert v-if="error" type="error" variant="tonal" class="mb-3 debug-error">{{ error }}</v-alert>
        <template v-if="plan">
          <p class="font-weight-medium">{{ plan.source.workflow_name }} · {{ version }} · {{ plan.source.node_name }}</p>
          <p class="text-body-2 mb-2" data-testid="debug-fork-scope">{{ tm("debugFork.singleNode") }}</p>
          <p class="text-caption text-medium-emphasis mb-3 debug-attempt">
            <span v-if="plan.source.iteration != null">{{ tm("loops.iteration") }} {{ plan.source.iteration }} · </span>
            {{ tm("v2.attempt") }} {{ plan.source.attempt_id }}
          </p>
          <v-select
            v-if="plan.inputs.length"
            v-model="field"
            :items="plan.inputs.map(port => port.name)"
            :label="tm('debugFork.replace')"
            variant="outlined"
            density="compact"
            clearable
            :disabled="starting"
            data-testid="debug-fork-input-picker"
          />
          <template v-if="selectedPort">
            <WorkflowDebugForkReference
              v-if="selectedPort.kind === 'reference'"
              :key="`${selectedPort.name}:${editorRevision}`"
              :selection="referenceEdits.get(selectedPort.name)"
              :file="selectedPort.format === 'astrbot-file'"
              :disabled="starting"
              @update:selection="referenceEdits.set(selectedPort.name, $event)"
            />
            <v-textarea
              v-else
              :model-value="text"
              :label="tm(selectedPort.kind === 'text' ? 'debugFork.replacementText' : 'debugFork.replacementJson')"
              variant="outlined"
              rows="3"
              :disabled="starting"
              :error-messages="edits.has(selectedPort.name) && edits.get(selectedPort.name) === undefined ? [tm('debugFork.invalidJson')] : []"
              data-testid="debug-fork-replacement"
              @update:model-value="textEdits.set(selectedPort.name, $event)"
            />
            <v-btn v-if="edits.has(selectedPort.name)" variant="text" :disabled="starting" class="mb-2" @click="clearEdit(selectedPort.name)">
              {{ tm("debugFork.clearEdit") }}
            </v-btn>
          </template>
          <v-alert v-if="unresolved.length" type="warning" variant="tonal" class="mb-3">
            {{ tm("debugFork.resolveInputs", { names: unresolved.join(", ") }) }}
          </v-alert>
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
          :disabled="!canStart"
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
import type { ArtifactReference, WorkflowDebugForkAttempt, WorkflowDebugForkPreview, WorkflowDebugForkReplacement } from "@/api/generated/openapi-v1";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";
import WorkflowDebugForkInputs from "./WorkflowDebugForkInputs.vue";
import WorkflowDebugForkReference from "./WorkflowDebugForkReference.vue";

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
const textEdits = ref(new Map<string, string>());
const referenceEdits = ref(new Map<string, (ArtifactReference & { file_id?: string }) | undefined>());
const editorRevision = ref(0);
let requestId: string | undefined;
const version = computed(() =>
  plan.value?.source.draft_revision == null
    ? tm("debugFork.version", { version: plan.value?.source.asset_version ?? '' })
    : tm("debugFork.revision", { revision: plan.value.source.draft_revision }),
);
const selectedPort = computed(() => plan.value?.inputs.find(port => port.name === field.value));
const text = computed(() => {
  const port = selectedPort.value;
  if (!port) return "";
  return textEdits.value.get(port.name) ?? (port.kind === "text"
    ? String(port.original ?? "")
    : JSON.stringify(port.original, null, 2) ?? "");
});
const edits = computed(() => {
  const values = new Map<string, unknown>(referenceEdits.value);
  for (const port of plan.value?.inputs ?? []) {
    const value = textEdits.value.get(port.name);
    if (value === undefined) continue;
    try {
      values.set(port.name, port.kind === "text" ? value : JSON.parse(value, (_key, part) => {
        if (typeof part === "number" && !Number.isFinite(part)) {
          throw new Error("Use finite JSON numbers.");
        }
        return part;
      }));
    } catch {
      values.set(port.name, undefined);
    }
  }
  return values;
});
watch(edits, () => { requestId = undefined; }, { flush: "sync" });
const unresolved = computed(() => (plan.value?.inputs ?? [])
  .filter(port => (port.source === "missing" || port.source === "unavailable") && edits.value.get(port.name) === undefined)
  .map(port => port.name),
);
const canStart = computed(() => !!plan.value && !unresolved.value.length &&
  Array.from(edits.value.values()).every(value => value !== undefined));
const previewInputs = computed(() => (plan.value?.inputs ?? []).map(port => {
  const value = edits.value.get(port.name);
  if (value === undefined) return port;
  const [original, proposed] = [port.original, value].map(input => JSON.stringify(input, (_key, part) =>
    part && typeof part === "object" && !Array.isArray(part)
      ? Object.fromEntries(Object.keys(part).sort().map(key => [key, part[key]]))
      : part,
  ));
  const changed = original !== proposed;
  const selection = !("original" in port) ? "filled" as const
    : changed ? "replaced" as const : "reused" as const;
  return { ...port, value, changed, selection };
}));

function clearEdit(name: string) {
  textEdits.value.delete(name);
  referenceEdits.value.delete(name);
  editorRevision.value++;
}

void workflowAssetsApi.previewDebugFork(props.rootId, props.attempt, controller.signal)
  .then(result => { if (!controller.signal.aborted) plan.value = result; })
  .catch(err => {
    if (!controller.signal.aborted) error.value = extractApiError(err, tm("errors.operationFailed")).message;
  })
  .finally(() => { loading.value = false; });

async function start() {
  if (!plan.value || !canStart.value || starting.value) return;
  starting.value = true;
  error.value = "";
  requestId ??= globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  try {
    const replacements: WorkflowDebugForkReplacement[] = Array.from(edits.value, ([name, value]) => ({ name, value }));
    const { run_id } = await workflowAssetsApi.startDebugFork(props.rootId, {
      ...props.attempt,
      preview_token: plan.value.preview_token,
      request_id: requestId,
      replacements,
    });
    if (controller.signal.aborted) return;
    await router.push(route.name === "ChatRunConversation"
      ? { name: "ChatRunConversation", params: { runId: run_id } }
      : { name: "AgentTeams", query: { workflow_run: run_id } });
    emit("close");
  } catch (err) {
    if (!controller.signal.aborted) {
      const info = extractApiError(err, tm("errors.operationFailed"));
      error.value = info.fields.length ? info.fields.map(field => `${field.path} · ${field.message}`).join("\n") : info.message;
    }
  } finally {
    starting.value = false;
  }
}
</script>

<style scoped>
.debug-attempt { overflow-wrap: anywhere; }
.debug-error { white-space: pre-line; }
</style>
