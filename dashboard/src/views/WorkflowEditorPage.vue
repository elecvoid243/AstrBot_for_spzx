<template>
  <section
    class="agent-teams-page workflow-editor-page"
    :aria-label="tm('graph.canvas')"
  >
    <header class="workflow-editor-header">
      <v-btn
        variant="text"
        prepend-icon="mdi-arrow-left"
        :disabled="saving || draft.busy.value"
        @click="router.push('/agent-teams')"
        >{{ tm("editor.back") }}</v-btn
      >
      <span class="editor-header-divider" aria-hidden="true" />
      <div class="editor-name">
        <h1 class="editor-eyebrow">{{ tm("graph.canvas") }}</h1>
        <v-text-field
          v-model="form.name"
          :label="tm('v2.name')"
          :placeholder="tm('draft.untitled')"
          persistent-placeholder
          :disabled="!ready || saving || draft.busy.value"
          variant="plain"
          density="compact"
          hide-details
          data-testid="workflow-name"
        />
      </div>
      <div class="editor-actions">
        <v-btn
          variant="text"
          prepend-icon="mdi-cog-outline"
          :aria-expanded="settingsOpen"
          aria-controls="workflow-settings"
          @click="settingsOpen = !settingsOpen"
          >{{ tm("editor.settings") }}</v-btn
        >
        <v-btn
          variant="tonal"
          prepend-icon="mdi-play-outline"
          :disabled="!ready || saving || draft.busy.value"
          @click="openTrial"
          >{{ tm("trial.title") }}</v-btn
        >
        <v-btn
          variant="tonal"
          color="primary"
          class="at-primary-action"
          :loading="saving"
          :disabled="
            !ready ||
            invalid ||
            usageInvalid ||
            !form.name.trim() ||
            draft.busy.value
          "
          data-testid="publish-workflow"
          @click="publish"
          >{{ tm("draft.publish") }}</v-btn
        >
      </div>
    </header>
    <div class="editor-statusbar">
      <p>{{ tm("editor.canvasHint") }}</p>
      <DraftStatus
        v-if="ready"
        :draft="draft"
        @reload="reloadDraft"
        @discard="discardAndLeave"
        @use-latest="useLatestDraft"
      />
    </div>
    <v-alert
      v-if="error"
      type="error"
      variant="tonal"
      role="alert"
      class="editor-error"
      >{{ error }}</v-alert
    >
    <div v-if="!ready" class="editor-loading" role="status">
      <v-progress-circular
        v-if="loading"
        indeterminate
        color="primary"
        size="26"
      />
      <p>{{ loading ? tm("editor.loading") : tm("editor.loadFailed") }}</p>
      <v-btn v-if="!loading" variant="tonal" @click="loadEditor">{{
        tm("page.refresh")
      }}</v-btn>
    </div>
    <div
      v-else
      class="editor-body"
      :inert="saving || draft.busy.value ? '' : undefined"
    >
      <WorkflowGraphCanvas
        :key="editorKey"
        v-model:definition="form"
        class="editor-canvas"
        :roles="roles"
        :catalog="catalog"
        :issues="issues"
        :teams="teams"
        :workflows="workflows"
        @validation="invalid = $event"
        @select-task="selectedNode = $event"
        @role-saved="reloadRoles"
      />
      <aside
        v-show="settingsOpen"
        id="workflow-settings"
        class="editor-settings"
        :aria-label="tm('editor.settings')"
        @keydown.esc="settingsOpen = false"
      >
        <div class="editor-settings-heading">
          <h2>{{ tm("editor.settings") }}</h2>
          <v-btn
            variant="text"
            icon="mdi-close"
            size="small"
            :aria-label="tm('v2.close')"
            @click="settingsOpen = false"
          />
        </div>
        <p class="at-field-guide">{{ tm("guide.workflowSettings") }}</p>
        <v-select
          v-model="form.default_provider_id"
          :items="catalog.models"
          item-title="name"
          item-value="id"
          :label="tm('graph.defaultModel')"
          :hint="tm('guide.defaultModel')"
          persistent-hint
          clearable
        />
        <h3>{{ tm("graph.executionSettings") }}</h3>
        <v-text-field
          v-model.number="form.max_parallel"
          type="number"
          min="1"
          max="100"
          :label="tm('graph.parallel')"
          :hint="tm('guide.parallel')"
          persistent-hint
        />
        <v-text-field
          v-model.number="form.max_depth"
          type="number"
          min="1"
          max="20"
          :label="tm('child.maxDepth')"
          :hint="tm('guide.depth')"
          persistent-hint
        />
        <RunLimitsForm
          v-model="form.limits"
          @validation="usageInvalid = $event"
        />
        <div class="at-guide-note">
          <strong>{{ tm("draft.publish") }} / {{ tm("trial.title") }}</strong>
          <p>{{ tm("guide.publishTrial") }}</p>
        </div>
      </aside>
    </div>
    <TrialRunDialog
      v-if="trialDraft"
      v-model="trialOpen"
      :asset-id="trialDraft.asset_id"
      :revision="trialDraft.revision"
      kind="workflow"
      :nodes="form.tasks"
      :initial-node="selectedNode"
      @started="
        router.push({ path: '/agent-teams', query: { workflow_run: $event } })
      "
    />
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import {
  onBeforeRouteLeave,
  onBeforeRouteUpdate,
  useRoute,
  useRouter,
} from "vue-router";
import { useModuleI18n } from "@/i18n/composables";
import {
  draftApi,
  useAssetDraft,
  type Draft,
} from "@/composables/useAssetDraft";
import { roleAssetsApi } from "@/api/roleAssets";
import { workflowAssetsApi } from "@/api/workflowAssets";
import type { GraphDefinition } from "@/api/generated/openapi-v1";
import { extractApiError } from "@/utils/extractApiError";
import DraftStatus from "@/components/agent_teams/DraftStatus.vue";
import WorkflowGraphCanvas from "@/components/agent_teams/WorkflowGraphCanvas.vue";
import RunLimitsForm from "@/components/agent_teams/RunLimitsForm.vue";
import TrialRunDialog from "@/components/agent_teams/TrialRunDialog.vue";
import "@/styles/agent-teams-workbench.css";

const { tm } = useModuleI18n("features/agent-teams");
const route = useRoute();
const router = useRouter();
const draft = useAssetDraft("workflow");
const ready = ref(false);
const loading = ref(false);
const saving = ref(false);
const invalid = ref(false);
const usageInvalid = ref(false);
const settingsOpen = ref(false);
const error = ref("");
const issues = ref<{ path: string; message: string }[]>([]);
const assets = ref<any[]>([]);
const roles = ref<any[]>([]);
const catalog = ref<Record<string, any[]>>({
  profiles: [],
  models: [],
  personas: [],
  tools: [],
  skills: [],
  knowledge_bases: [],
});
const form = ref<GraphDefinition>({ name: "", tasks: [], edges: [] });
const openedId = ref<string | null>(null);
const teams = computed(() =>
  assets.value.filter((asset) => asset.definition.kind === "autonomous"),
);
const workflows = computed(() =>
  assets.value.filter(
    (asset) =>
      asset.definition.kind !== "autonomous" &&
      asset.asset_id !== openedId.value,
  ),
);
const editorKey = ref(0);
const selectedNode = ref<string | null>(null);
const trialOpen = ref(false);
const trialDraft = ref<Draft | null>(null);
let generation = 0;
let discard = false;

async function loadEditor() {
  const id = String(route.params.assetId);
  const token = ++generation;
  ready.value = false;
  loading.value = true;
  error.value = "";
  issues.value = [];
  selectedNode.value = null;
  trialDraft.value = null;
  trialOpen.value = false;
  invalid.value = false;
  draft.close();
  try {
    const [assetData, roleData, resources, drafts] = await Promise.all([
      workflowAssetsApi.list(),
      roleAssetsApi.list(),
      roleAssetsApi.resources(),
      draftApi.list("workflow"),
    ]);
    if (token !== generation) return;
    assets.value = assetData.assets;
    roles.value = roleData.roles;
    catalog.value = resources;
    const existing = drafts.find((item) => item.asset_id === id);
    const asset = assets.value.find(
      (item) => item.asset_id === id && item.definition.kind !== "autonomous",
    );
    if (id !== "new" && !existing && !asset)
      throw new Error(tm("editor.notFound"));
    const initial = existing?.body.definition ??
      asset?.definition ?? {
        name: "",
        default_provider_id: null,
        required_inputs: ["input"],
        result_task_id: "agent",
        max_parallel: 4,
        max_depth: 3,
        tasks: [
          {
            id: "agent",
            name: "Agent",
            member: "agent",
            role: { name: "Agent" },
            installation: { profile_id: "default" },
            task: "{input}",
            inputs: { input: { input: "input", field: "text" } },
            position: { x: 300, y: 100 },
          },
        ],
        edges: [],
      };
    const item = await draft.open(id === "new" ? null : id, {
      definition: initial,
    });
    if (token !== generation) return;
    openedId.value = item.asset_id;
    form.value = JSON.parse(JSON.stringify(item.body.definition));
    editorKey.value++;
    await nextTick();
    ready.value = true;
    if (id === "new")
      await router.replace({
        name: "AgentWorkflowEditor",
        params: { assetId: item.asset_id },
      });
  } catch (cause) {
    if (token === generation)
      error.value = extractApiError(
        cause,
        tm("errors.operationFailed"),
      ).message;
  } finally {
    if (token === generation) loading.value = false;
  }
}

watch(
  () => route.params.assetId,
  (id) => {
    if (id !== openedId.value) void loadEditor();
  },
  { immediate: true },
);
watch(
  form,
  () => {
    if (ready.value) draft.change({ definition: form.value });
  },
  { deep: true },
);
watch(usageInvalid, (value) => {
  if (value) settingsOpen.value = true;
});

async function reloadDraft() {
  try {
    const item = await draft.reload();
    ready.value = false;
    form.value = JSON.parse(JSON.stringify(item.body.definition));
    editorKey.value++;
    await nextTick();
    ready.value = true;
    error.value = "";
    issues.value = [];
  } catch (cause) {
    error.value = extractApiError(cause, tm("errors.operationFailed")).message;
  }
}

async function useLatestDraft(latest: any) {
  form.value = JSON.parse(JSON.stringify(latest.definition));
  editorKey.value++;
  draft.change({ definition: form.value });
  try {
    await draft.rebase();
  } catch (cause) {
    error.value = extractApiError(cause, tm("errors.operationFailed")).message;
  }
}

async function reloadRoles() {
  try {
    roles.value = (await roleAssetsApi.list()).roles;
  } catch (cause) {
    error.value = extractApiError(cause, tm("errors.operationFailed")).message;
  }
}

async function openTrial() {
  try {
    draft.change({ definition: form.value });
    if (draft.status.value === "failed" || draft.status.value === "conflict")
      throw new Error(tm(`draft.${draft.status.value}`));
    trialDraft.value = await draft.flush();
    trialOpen.value = true;
  } catch (cause) {
    error.value = extractApiError(cause, tm("errors.operationFailed")).message;
  }
}

async function publish() {
  if (saving.value || invalid.value || usageInvalid.value) return;
  saving.value = true;
  error.value = "";
  let published = false;
  try {
    draft.change({ definition: form.value });
    await draft.publish();
    published = true;
  } catch (cause) {
    const failure = extractApiError(cause, tm("errors.operationFailed"));
    issues.value = failure.fields
      .filter((field) => !field.path.includes(".AutonomousDefinition."))
      .map((field) => ({
        ...field,
        path: field.path
          .replace(/^body\./, "")
          .replace("definition.GraphDefinition.", "definition."),
      }));
    error.value = failure.message;
  } finally {
    saving.value = false;
  }
  if (published) await router.push("/agent-teams");
}

// Navigation must wait for the draft queue, including browser Back and sidebar links.
async function saveBeforeLeave() {
  if (saving.value || draft.busy.value) return false;
  if (!ready.value || discard) return true;
  if (draft.status.value === "failed" || draft.status.value === "conflict") {
    error.value = tm("editor.saveBeforeLeave");
    return false;
  }
  try {
    draft.change({ definition: form.value });
    await draft.flush();
    return true;
  } catch {
    error.value = tm("editor.saveBeforeLeave");
    return false;
  }
}
onBeforeRouteLeave(saveBeforeLeave);
onBeforeRouteUpdate(
  (to) => to.params.assetId === openedId.value || saveBeforeLeave(),
);

function discardAndLeave() {
  discard = true;
  void router.push("/agent-teams");
}
function beforeUnload(event: BeforeUnloadEvent) {
  if (
    ready.value &&
    (saving.value || draft.busy.value || draft.status.value !== "saved")
  ) {
    event.preventDefault();
    event.returnValue = "";
  }
}
window.addEventListener("beforeunload", beforeUnload);
onBeforeUnmount(() => {
  generation++;
  draft.close();
  window.removeEventListener("beforeunload", beforeUnload);
});
</script>

<style scoped>
.workflow-editor-page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--at-surface);
}
.workflow-editor-header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 24px;
  border-bottom: 1px solid var(--at-line);
  flex: 0 0 auto;
}
.editor-header-divider {
  width: 1px;
  height: 30px;
  background: var(--at-line);
}
.editor-name {
  min-width: 120px;
  flex: 1;
}
.editor-eyebrow {
  display: block;
  font-size: 11px;
  font-weight: 500;
  margin: 0;
  color: var(--at-muted);
  letter-spacing: 0.06em;
}
.editor-name :deep(.v-field__input) {
  font-size: 18px;
  font-weight: 650;
  padding: 0;
  min-height: 28px;
  line-height: 28px;
}
.editor-name :deep(.v-field) {
  --v-field-padding-top: 0px;
}
.editor-name :deep(.v-label) {
  display: none;
}
.editor-name :deep(input:focus) {
  box-shadow: 0 1px var(--at-accent);
}
.editor-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.editor-statusbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 24px;
  font-size: 12px;
  color: var(--at-muted);
  flex: 0 0 auto;
}
.editor-statusbar p {
  margin: 0;
}
.editor-statusbar :deep(.mb-3) {
  margin-bottom: 0 !important;
}
.editor-error {
  flex: 0 0 auto;
  margin: 0 20px 8px;
  max-height: 120px;
  overflow: auto;
}
.editor-loading {
  flex: 1;
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: center;
}
.editor-body {
  flex: 1;
  min-height: 0;
  position: relative;
  display: flex;
  overflow: hidden;
}
.editor-canvas {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-radius: 0;
  border-inline: 0;
  border-bottom: 0;
}
.editor-canvas :deep(.graph-columns) {
  flex: 1;
  height: auto;
  min-height: 0;
  grid-template-rows: minmax(0, 1fr);
}
.editor-canvas :deep(.graph-toolbar) {
  flex: 0 0 auto;
  padding: 6px 16px;
}
.editor-canvas :deep(.graph-footer) {
  padding: 6px 16px;
  font-size: 11px !important;
}
.editor-settings {
  position: absolute;
  inset: 0 0 0 auto;
  z-index: 6;
  width: min(380px, 100%);
  padding: 20px;
  overflow-y: auto;
  background: var(--at-surface);
  border-left: 1px solid var(--at-line);
  box-shadow: -8px 0 28px #24334210;
}
.editor-settings-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}
.editor-settings h2 {
  font-size: 16px;
}
.editor-settings h3 {
  font-size: 13px;
  margin: 20px 0 16px;
  padding-top: 18px;
  border-top: 1px solid var(--at-line);
}
.editor-settings :deep(.v-input) {
  margin-bottom: 14px;
}
@media (max-width: 1000px) {
  .editor-canvas :deep(.graph-columns) {
    position: relative;
    display: block;
  }
  .editor-canvas :deep(.graph-stage) {
    height: 100%;
  }
  .editor-canvas :deep(.graph-toolbox),
  .editor-canvas :deep(.graph-inspector) {
    position: absolute;
    top: 12px;
    z-index: 5;
    border: 1px solid var(--at-line);
    border-radius: 10px;
    max-height: calc(100% - 24px);
    padding: 12px;
    box-shadow: var(--at-shadow);
  }
  .editor-canvas :deep(.graph-toolbox) {
    left: 12px;
  }
  .editor-canvas :deep(.graph-inspector) {
    right: 12px;
  }
  .editor-canvas :deep(.graph-toolbox[open]),
  .editor-canvas :deep(.graph-inspector[open]) {
    width: min(320px, calc(100% - 24px));
  }
  .editor-canvas :deep(.graph-inspector[open]) {
    z-index: 6;
  }
}
@media (max-width: 600px) {
  .workflow-editor-header {
    padding: 10px 12px;
    gap: 8px;
    flex-wrap: wrap;
  }
  .editor-header-divider {
    display: none;
  }
  .editor-actions {
    width: 100%;
    justify-content: flex-end;
    gap: 4px;
  }
  .editor-actions :deep(.v-btn) {
    padding-inline: 10px;
  }
  .editor-statusbar {
    padding: 8px 12px;
    flex-wrap: wrap;
    gap: 4px;
  }
  .editor-statusbar > p {
    font-size: 11px;
  }
  .editor-canvas :deep(.graph-toolbar) {
    padding: 6px 8px;
  }
}
</style>
