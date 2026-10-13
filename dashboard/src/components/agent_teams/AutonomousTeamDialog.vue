<template>
  <v-dialog
    :model-value="modelValue"
    max-width="760"
    :persistent="saving"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <v-card>
      <h3 class="text-h3 pa-4 pb-0 pl-6">{{ tm("autonomous.title") }}</h3>
      <v-card-text>
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
          v-model="form.coordinator.role_id"
          :items="roleItems"
          :label="tm('autonomous.coordinator')"
          data-testid="team-coordinator"
          @update:model-value="
            form.coordinator.role_version = roleById($event)?.version ?? 1
          "
        />
        <v-select
          v-model="form.coordinator.role_version"
          :items="roleById(form.coordinator.role_id)?.versions ?? []"
          :label="tm('v2.roleVersion')"
        />
        <v-select
          v-model="allowedIds"
          :items="roleItems"
          multiple
          chips
          :label="tm('autonomous.allowedRoles')"
          data-testid="team-roles"
          @update:model-value="selectAllowed"
        />
        <div
          v-for="role in form.allowed_roles"
          :key="role.role_id"
          class="team-role"
        >
          <strong class="text-body-2">{{
            roleItems.find((item) => item.value === role.role_id)?.title ??
            role.role_id
          }}</strong>
          <v-select
            v-model="role.role_version"
            :items="roleById(role.role_id)?.versions ?? []"
            :label="tm('v2.roleVersion')"
            density="compact"
          />
        </div>
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
        </div>
        <v-textarea
          v-model="approval"
          :label="tm('autonomous.approval')"
          rows="2"
        />
        <v-textarea v-model="contract" :label="tm('v2.contract')" rows="3" />
      </v-card-text>
      <v-card-actions class="flex-wrap">
        <v-spacer />
        <v-btn
          variant="text"
          :disabled="saving"
          @click="$emit('update:modelValue', false)"
          >{{ tm("roles.cancel") }}</v-btn
        >
        <v-btn
          variant="tonal"
          color="primary"
          :loading="saving"
          :disabled="
            !form.name.trim() ||
            !form.coordinator.role_id ||
            !form.allowed_roles.length
          "
          data-testid="team-save"
          @click="save"
          >{{ tm("v2.save") }}</v-btn
        >
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";
import type { AutonomousDefinition } from "@/api/generated/openapi-v1";

const props = defineProps<{ modelValue: boolean; asset: any; roles: any[] }>();
const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  saved: [];
}>();
const { tm } = useModuleI18n("features/agent-teams");
const saving = ref(false);
const error = ref("");
const approval = ref("");
const contract = ref("");
const allowedIds = ref<string[]>([]);
const form = ref<AutonomousDefinition>({
  kind: "autonomous",
  name: "",
  coordinator: { role_id: "", role_version: 1, overrides: {} },
  allowed_roles: [],
  required_inputs: ["goal"],
  max_members: 8,
  max_parallel: 4,
  max_tasks: 50,
});
const roleItems = computed(() =>
  props.roles.map((role) => ({
    title: role.definition.name,
    value: role.role_id,
  })),
);
const roleById = (identity: string) =>
  props.roles.find((role) => role.role_id === identity);

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return;
    form.value = props.asset
      ? JSON.parse(JSON.stringify(props.asset.definition))
      : {
          kind: "autonomous",
          name: "",
          coordinator: { role_id: "", role_version: 1, overrides: {} },
          allowed_roles: [],
          required_inputs: ["goal"],
          max_members: 8,
          max_parallel: 4,
          max_tasks: 50,
        };
    allowedIds.value = form.value.allowed_roles.map((role) => role.role_id);
    approval.value = form.value.approval_question ?? "";
    contract.value = form.value.output_contract
      ? JSON.stringify(form.value.output_contract, null, 2)
      : "";
    error.value = "";
  },
  { immediate: true },
);

function selectAllowed(identities: string[]) {
  form.value.allowed_roles = identities.map(
    (identity) =>
      form.value.allowed_roles.find((role) => role.role_id === identity) ?? {
        role_id: identity,
        role_version: roleById(identity)?.version ?? 1,
        overrides: {},
      },
  );
}

async function save() {
  if (saving.value) return;
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
    await workflowAssetsApi.save(props.asset?.asset_id ?? null, {
      definition,
      expected_version: props.asset?.version,
    });
    emit("update:modelValue", false);
    emit("saved");
  } catch (err) {
    const failure = extractApiError(err, tm("errors.operationFailed"));
    error.value = `${failure.message} ${failure.fields
      .map((field) => `${field.path}: ${field.message}`)
      .join("; ")}`;
  } finally {
    saving.value = false;
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
