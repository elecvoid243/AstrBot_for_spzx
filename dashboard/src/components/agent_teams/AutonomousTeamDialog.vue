<template>
  <v-dialog
    class="workbench-dialog"
    scrollable
    :model-value="modelValue"
    max-width="760"
    persistent
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <v-card v-if="draftReady">
      <h3 class="text-h3 pa-4 pb-0 pl-6">{{ tm("autonomous.title") }}</h3>
      <v-card-text :inert="saving || draft.busy.value ? '' : undefined">
        <DraftStatus
          :draft="draft"
          @reload="reloadDraft"
          @discard="emit('update:modelValue', false)"
          @use-latest="useLatestDraft"
        />
        <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{
          error
        }}</v-alert>
        <p class="text-body-2 mb-4">{{ tm("autonomous.explanation") }}</p>
        <v-text-field
          v-model="form.name"
          :label="tm('autonomous.name')"
          data-testid="team-name"
        />
        <v-select
          v-model="form.default_provider_id"
          :items="catalog.models"
          item-title="name"
          item-value="id"
          :label="tm('graph.defaultModel')"
          clearable
        />
        <TeamPresetEditor
          :preset="form.coordinator"
          :initially-open="!asset"
          :title="tm('autonomous.coordinator')"
          :roles="roles"
          :catalog="catalog"
          :default-provider="form.default_provider_id"
        />
        <TeamPresetEditor
          v-for="(preset, index) in form.allowed_roles"
          :key="preset.id"
          :preset="preset"
          :title="`${tm('autonomous.allowedRoles')} ${index + 1}`"
          :roles="roles"
          :catalog="catalog"
          :default-provider="form.default_provider_id"
          removable
          @remove="form.allowed_roles.splice(index, 1)"
        />
        <v-btn variant="tonal" class="mb-4" @click="addPreset">{{
          tm("autonomous.addPreset")
        }}</v-btn>
        <div class="team-limits">
          <v-text-field
            v-model.number="form.max_members"
            type="number"
            min="2"
            max="100"
            :label="tm('autonomous.maxMembers')"
            :hint="tm('autonomous.coordinatorCount')"
            persistent-hint
          />
          <v-text-field
            v-model.number="form.max_parallel"
            type="number"
            min="1"
            max="100"
            :label="tm('graph.parallel')"
          />
          <v-text-field
            v-model.number="form.max_tasks"
            type="number"
            min="1"
            max="100"
            :label="tm('autonomous.maxTasks')"
          />
          <v-text-field
            v-model.number="form.max_depth"
            type="number"
            min="1"
            max="20"
            :label="tm('child.maxDepth')"
          />
        </div>
        <RunLimitsForm
          v-model="form.limits"
          @validation="usageInvalid = $event"
        />
        <AllowedWorkflowsPanel
          :model-value="form.allowed_workflows ?? []"
          @update:model-value="form.allowed_workflows = $event"
          :active="modelValue"
          @validation="workflowInvalid = $event"
        />
        <v-textarea
          v-model="approval"
          :label="tm('autonomous.approval')"
          rows="2"
        />
        <v-textarea v-model="contract" :label="tm('v2.contract')" rows="3" />
      </v-card-text>
      <v-card-actions class="flex-wrap">
        <v-btn variant="tonal" :disabled="saving || draft.busy.value" @click="openTrial">{{
          tm("trial.title")
        }}</v-btn>
        <v-spacer />
        <v-btn variant="text" :disabled="saving" @click="closeEditor">{{
          tm("v2.close")
        }}</v-btn>
        <v-btn
          variant="tonal"
          color="primary"
          :loading="saving"
          :disabled="
            draft.busy.value ||
            !form.name.trim() ||
            (!form.coordinator.role_id && !form.coordinator.role) ||
            !form.allowed_roles.length ||
            workflowInvalid ||
            usageInvalid
          "
          data-testid="team-save"
          @click="save"
          >{{ tm("draft.publish") }}</v-btn
        >
      </v-card-actions>
    </v-card>
    <v-card v-else
      ><v-card-text>{{ error || tm("draft.saving") }}</v-card-text
      ><v-card-actions
        ><v-btn variant="text" @click="$emit('update:modelValue', false)">{{
          tm("v2.close")
        }}</v-btn></v-card-actions
      ></v-card
    >
  </v-dialog>
  <TrialRunDialog
    v-if="trialDraft"
    v-model="trialOpen"
    :asset-id="trialDraft.asset_id"
    :revision="trialDraft.revision"
    kind="autonomous"
    :nodes="[]"
    @started="onTrialStarted"
  />
</template>

<script setup lang="ts">
import DraftStatus from "./DraftStatus.vue";
import TrialRunDialog from "./TrialRunDialog.vue";
import TeamPresetEditor from "./TeamPresetEditor.vue";
import RunLimitsForm from "./RunLimitsForm.vue";
import { nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useAssetDraft } from "@/composables/useAssetDraft";
import AllowedWorkflowsPanel from "./AllowedWorkflowsPanel.vue";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";
import type { AutonomousDefinition } from "@/api/generated/openapi-v1";

const props = defineProps<{
  modelValue: boolean;
  asset: any;
  roles: any[];
  catalog: Record<string, any[]>;
}>();
const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  saved: [];
  started: [runId: string];
}>();
const { tm } = useModuleI18n("features/agent-teams");
const saving = ref(false);
const draft = useAssetDraft("autonomous");
const trialOpen = ref(false);
const trialDraft = ref<any>(null);
async function openTrial() {
  try {
    draft.change({
      definition: {
        ...form.value,
        approval_question: approval.value.trim() || null,
        output_contract: contract.value.trim() ? JSON.parse(contract.value) : null,
      },
      editor: { approval: approval.value, contract: contract.value },
    });
    if (draft.status.value === "failed" || draft.status.value === "conflict")
      throw new Error(tm(`draft.${draft.status.value}`));
    trialDraft.value = await draft.flush();
    trialOpen.value = true;
  } catch (cause) {
    error.value = extractApiError(cause, tm("errors.operationFailed")).message;
  }
}
function onTrialStarted(runId: string) {
  emit("update:modelValue", false);
  emit("started", runId);
}
const draftReady = ref(false);
const error = ref("");
const workflowInvalid = ref(false);
let editGeneration = 0;
onBeforeUnmount(() => {
  editGeneration++;
});
const approval = ref("");
const contract = ref("");
const usageInvalid = ref(false);
const form = ref<AutonomousDefinition>({
  kind: "autonomous",
  name: "",
  coordinator: {
    id: "coordinator",
    role: { name: "Coordinator" },
    installation: { profile_id: "default" },
    overrides: {},
  },
  allowed_roles: [],
  required_inputs: ["goal"],
  max_members: 8,
  max_parallel: 4,
  max_tasks: 50,
  max_depth: 3,
  allowed_workflows: [],
});

watch(
  () => [props.modelValue, props.asset] as const,
  async ([open]) => {
    editGeneration++;
    const generation = editGeneration;
    draftReady.value = false;
    draft.close();
    saving.value = false;
    if (!open) return;
    form.value = props.asset
      ? JSON.parse(JSON.stringify(props.asset.definition))
      : {
          kind: "autonomous",
          name: "",
          coordinator: {
            id: "coordinator",
            role: { name: "Coordinator" },
            installation: { profile_id: "default" },
            overrides: {},
          },
          allowed_roles: [],
          required_inputs: ["goal"],
          max_members: 8,
          max_parallel: 4,
          max_tasks: 50,
          max_depth: 3,
          allowed_workflows: [],
        };
    form.value.allowed_workflows ??= [];
    form.value.max_depth ??= 3;
    approval.value = form.value.approval_question ?? "";
    contract.value = form.value.output_contract
      ? JSON.stringify(form.value.output_contract, null, 2)
      : "";
    error.value = "";
    try {
      const item = await draft.open(props.asset?.asset_id ?? null, {
        definition: form.value,
        editor: { approval: approval.value, contract: contract.value },
      });
      if (generation !== editGeneration) return;
      form.value = item.body.definition;
      approval.value =
        item.body.editor?.approval ?? form.value.approval_question ?? "";
      contract.value =
        item.body.editor?.contract ??
        (form.value.output_contract
          ? JSON.stringify(form.value.output_contract, null, 2)
          : "");
      await nextTick();
      draftReady.value = true;
    } catch (err) {
      if (generation === editGeneration)
        error.value = extractApiError(
          err,
          tm("errors.operationFailed"),
        ).message;
    }
  },
  { immediate: true },
);

watch(
  [form, approval, contract],
  () => {
    if (draftReady.value && props.modelValue)
      draft.change({
        definition: form.value,
        editor: { approval: approval.value, contract: contract.value },
      });
  },
  { deep: true },
);

async function reloadDraft() {
  draftReady.value = false;
  const item = await draft.reload();
  form.value = item.body.definition;
  approval.value =
    item.body.editor?.approval ?? form.value.approval_question ?? "";
  contract.value =
    item.body.editor?.contract ??
    (form.value.output_contract
      ? JSON.stringify(form.value.output_contract, null, 2)
      : "");
  await nextTick();
  draftReady.value = true;
}

async function useLatestDraft(latest: any) {
  form.value = JSON.parse(JSON.stringify(latest.definition));
  approval.value = form.value.approval_question ?? "";
  contract.value = form.value.output_contract
    ? JSON.stringify(form.value.output_contract, null, 2)
    : "";
  draft.change({
    definition: form.value,
    editor: { approval: approval.value, contract: contract.value },
  });
  try {
    await draft.rebase();
  } catch (err) {
    error.value = extractApiError(err, tm("errors.operationFailed")).message;
  }
}

async function closeEditor() {
  if (draft.status.value === "failed" || draft.status.value === "conflict")
    return;
  try {
    await draft.flush();
    emit("update:modelValue", false);
  } catch {
    /* The draft status retains the failure. */
  }
}

function addPreset() {
  form.value.allowed_roles.push({
    id: crypto.randomUUID(),
    role: { name: "Member" },
    installation: { profile_id: "default" },
    overrides: {},
  });
}

async function save() {
  if (saving.value || usageInvalid.value) return;
  const generation = editGeneration;
  const asset = props.asset;
  const current = () =>
    generation === editGeneration && props.modelValue && props.asset === asset;
  saving.value = true;
  error.value = "";
  try {
    const definition = {
      ...form.value,
      approval_question: approval.value.trim() || null,
      output_contract: contract.value.trim()
        ? JSON.parse(contract.value)
        : null,
    };
    draft.change({
      definition,
      editor: { approval: approval.value, contract: contract.value },
    });
    await draft.publish();
    if (!current()) return;
    emit("update:modelValue", false);
    emit("saved");
  } catch (err) {
    if (!current()) return;
    const failure = extractApiError(err, tm("errors.operationFailed"));
    error.value = `${failure.message} ${failure.fields
      .map((field) => `${field.path}: ${field.message}`)
      .join("; ")}`;
  } finally {
    if (current()) saving.value = false;
  }
}
</script>

<style scoped>
.team-role {
  overflow-wrap: anywhere;
}
.team-limits {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
@media (max-width: 600px) {
  .team-limits {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
