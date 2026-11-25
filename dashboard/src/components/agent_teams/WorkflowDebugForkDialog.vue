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
        <v-alert v-if="error" type="error" variant="tonal" class="mb-3 debug-error">{{ error }}</v-alert>
        <template v-if="plan">
          <p v-if="plan.source.parent_call" class="text-body-2 mb-2" data-testid="debug-fork-parent-context">
            {{ tm("debugFork.parentCall", { name: plan.source.parent_call.node_name }) }}
            <span v-if="plan.source.parent_call.iteration != null">
              · {{ tm("loops.iteration") }} {{ plan.source.parent_call.iteration }}
            </span>
          </p>
          <p class="font-weight-medium">{{ plan.source.workflow_name }} · {{ version }} · {{ plan.source.node_name }}</p>
          <p class="text-body-2 mb-2" data-testid="debug-fork-scope">{{ tm("debugFork.singleNode") }}</p>
          <p class="text-caption text-medium-emphasis mb-3 debug-attempt">
            <span v-if="plan.source.iteration != null">{{ tm("loops.iteration") }} {{ plan.source.iteration }} · </span>
            {{ tm("v2.attempt") }} {{ plan.source.attempt_id }}
          </p>
          <p v-if="candidates && plan.original.reason" class="text-body-2 mb-3">{{ plan.original.reason }}</p>
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
            :key="editorRevision"
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
            v-if="mode === 'source' && plan.inputs.length"
            v-model="field"
            :items="plan.inputs.map(port => port.name)"
            :label="tm('debugFork.replace')"
            variant="outlined"
            density="compact"
            clearable
            :disabled="starting"
            data-testid="debug-fork-input-picker"
          />
          <template v-if="mode === 'source' && selectedPort">
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
          <v-alert v-if="mode === 'source' && unresolved.length" type="warning" variant="tonal" class="mb-3">
            {{ tm("debugFork.resolveInputs", { names: unresolved.join(", ") }) }}
          </v-alert>
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
import type { ArtifactReference, WorkflowDebugForkAttempt, WorkflowDebugForkBindings, WorkflowDebugForkLocation, WorkflowDebugForkPreview, WorkflowDebugForkReplacement } from "@/api/generated/openapi-v1";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";
import WorkflowDebugForkInputs from "./WorkflowDebugForkInputs.vue";
import WorkflowDebugForkDraftBindings from "./WorkflowDebugForkDraftBindings.vue";
import WorkflowDebugForkDraftDetails from "./WorkflowDebugForkDraftDetails.vue";
import WorkflowDebugForkReference from "./WorkflowDebugForkReference.vue";

const props = defineProps<{
  rootId: string;
  attempt: WorkflowDebugForkAttempt;
  candidates?: WorkflowDebugForkLocation[];
}>();
const emit = defineEmits<{ close: []; selectCandidate: [candidate: WorkflowDebugForkLocation] }>();
const { tm } = useModuleI18n("features/agent-teams");
const router = useRouter();
const route = useRoute();
let previewRead: AbortController | undefined;
onScopeDispose(() => previewRead?.abort());

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
const dirtyBindings = computed(() => JSON.stringify(draftBindings.value) !== JSON.stringify(checkedBindings.value));
const canStart = computed(() => !!plan.value && !loading.value && !starting.value && (
  mode.value === "draft"
    ? !!plan.value.definition && !!plan.value.ready && !dirtyBindings.value && !draftStale.value
    : !unresolved.value.length && Array.from(edits.value.values()).every(value => value !== undefined)
));
const previewInputs = computed(() => (plan.value?.inputs ?? []).map(port => {
  if (mode.value === "draft") return port;
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

function chooseCandidate(attemptId: string) {
  const selected = props.candidates?.find(item => item.attempt_id === attemptId);
  if (selected) emit("selectCandidate", selected);
}

function bindingIssue(issue: { path: string; code: string; message: string }) {
  const port = plan.value?.inputs.find(input => issue.path === `inputs.${input.name}`);
  if (!port || issue.code !== "required") return issue.message;
  return tm(port.kind !== "reference" ? "debugFork.requiredBinding"
    : port.available_sources?.length ? "debugFork.requiredReference" : "debugFork.unavailableReference");
}

async function loadPreview(selection: DefinitionMode, checkBindings = false) {
  previewRead?.abort();
  const read = new AbortController();
  previewRead = read;
  mode.value = selection;
  loading.value = true;
  error.value = "";
  requestId = undefined;
  if (!checkBindings) {
    plan.value = undefined;
    field.value = null;
    textEdits.value.clear();
    referenceEdits.value.clear();
    editorRevision.value++;
    draftBindings.value = {};
    checkedBindings.value = {};
    draftStale.value = false;
  }
  const bindings = draftBindings.value ?? {};
  const { scope_run_id, task_run_id, attempt_id } = props.attempt;
  try {
    const result = await workflowAssetsApi.previewDebugFork(props.rootId, {
      scope_run_id, task_run_id, attempt_id,
      ...(selection === "draft" ? {
        definition_mode: selection,
        draft_revision: checkBindings ? plan.value?.definition?.draft_revision : undefined,
        bindings,
      } : {}),
    }, read.signal);
    if (read.signal.aborted) return;
    plan.value = result;
    checkedBindings.value = bindings;
  } catch (err) {
    if (!read.signal.aborted) {
      error.value = extractApiError(err, tm("errors.operationFailed")).message;
      draftStale.value = (err as { response?: { status?: number } })?.response?.status === 409;
    }
  } finally {
    if (!read.signal.aborted) loading.value = false;
  }
}

watch(
  [() => props.rootId, () => props.attempt.scope_run_id, () => props.attempt.task_run_id, () => props.attempt.attempt_id],
  () => {
    starting.value = false;
    void loadPreview("source");
  },
  { immediate: true },
);

async function start() {
  const read = previewRead;
  if (!read || !plan.value || !canStart.value) return;
  starting.value = true;
  error.value = "";
  requestId ??= globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  try {
    const replacements: WorkflowDebugForkReplacement[] = Array.from(edits.value, ([name, value]) => ({ name, value }));
    const { run_id } = await workflowAssetsApi.startDebugFork(props.rootId, {
      scope_run_id: props.attempt.scope_run_id,
      task_run_id: props.attempt.task_run_id,
      attempt_id: props.attempt.attempt_id,
      preview_token: plan.value.preview_token,
      request_id: requestId,
      ...(mode.value === "draft" ? {
        definition_mode: mode.value,
        draft_revision: plan.value.definition!.draft_revision,
        bindings: checkedBindings.value,
      } : { replacements }),
    });
    if (read.signal.aborted) return;
    await router.push(route.name === "ChatRunConversation"
      ? { name: "ChatRunConversation", params: { runId: run_id } }
      : { name: "AgentTeams", query: { workflow_run: run_id } });
    emit("close");
  } catch (err) {
    if (!read.signal.aborted) {
      if (mode.value === "draft") {
        draftStale.value = (err as { response?: { status?: number } })?.response?.status === 409;
      }
      const info = extractApiError(err, tm("errors.operationFailed"));
      error.value = info.fields.length ? info.fields.map(field => `${field.path} · ${field.message}`).join("\n") : info.message;
    }
  } finally {
    if (!read.signal.aborted) starting.value = false;
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
.debug-diagnostics { color: rgb(var(--v-theme-on-surface)); }
.debug-error { white-space: pre-line; }
</style>
