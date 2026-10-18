<template>
  <v-btn
    variant="tonal"
    prepend-icon="mdi-source-branch"
    data-testid="open-migration"
    @click="open = true"
  >
    {{ tm("migration.title") }}
  </v-btn>
  <v-dialog
    v-model="open"
    class="workbench-dialog"
    scrollable
    max-width="1000"
    :persistent="saving"
  >
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
        tm("migration.title")
      }}</v-card-title>
      <v-card-text class="migration-text">
        <v-alert type="info" variant="tonal" class="mb-4">{{
          tm("migration.scope")
        }}</v-alert>
        <v-select
          v-model="teamId"
          :items="teams"
          item-title="name"
          item-value="team_id"
          :label="tm('migration.team')"
          :disabled="saving"
          data-testid="migration-team"
        />
        <v-btn
          variant="tonal"
          :disabled="!teamId || saving"
          :loading="checking"
          data-testid="preview-migration"
          @click="loadPreview"
          >{{ tm("migration.preview") }}</v-btn
        >
        <v-alert
          v-if="error"
          type="error"
          variant="tonal"
          class="mt-4"
          role="alert"
          >{{ error }}</v-alert
        >
        <template v-if="preview">
          <v-alert
            v-if="preview.active_runs.length"
            type="warning"
            variant="tonal"
            class="mt-4"
          >
            {{ tm("migration.active") }}
            <ul class="pl-5">
              <li v-for="run in preview.active_runs" :key="run.run_id">
                {{ run.run_id }} · {{ run.status }}
              </li>
            </ul>
          </v-alert>
          <v-alert
            :type="preview.ready ? 'success' : 'warning'"
            variant="tonal"
            class="my-4"
            data-testid="migration-readiness"
            >{{
              tm(preview.ready ? "migration.ready" : "migration.pending")
            }}</v-alert
          >
          <h4 class="text-h6">{{ tm("migration.assets") }}</h4>
          <ul class="pl-5 mb-4">
            <li v-for="asset in preview.assets" :key="asset.asset_id">
              {{ asset.definition.name }} ·
              {{
                tm(
                  asset.kind === "team"
                    ? "workbench.teams"
                    : "workbench.workflows",
                )
              }}
              · {{ asset.source_id }}
            </li>
          </ul>
          <details
            v-for="role in preview.roles"
            :key="role.role_id"
            class="mb-3"
          >
            <summary>
              {{ role.definition.name }} · {{ tm("migration.effective") }}
            </summary>
            <pre class="migration-json">{{
              JSON.stringify(
                {
                  effective: role.effective,
                  definition: role.definition,
                  sources: role.sources,
                  installation: role.installation,
                },
                null,
                2,
              )
            }}</pre>
          </details>
          <ul v-if="preview.issues.length" class="pl-5 my-4">
            <li v-for="(issue, index) in preview.issues" :key="index">
              {{ issue.path }} · {{ issue.code }}: {{ issue.message }}
            </li>
          </ul>
          <details class="mb-4">
            <summary>{{ tm("migration.definitions") }}</summary>
            <pre class="migration-json">{{
              JSON.stringify(
                { assets: preview.assets, mapping: preview.mapping },
                null,
                2,
              )
            }}</pre>
          </details>
          <v-checkbox
            v-if="!preview.ready && !result"
            v-model="savePending"
            :disabled="saving"
            :label="tm('migration.savePending')"
            data-testid="save-pending-migration"
          />
        </template>
        <v-alert
          v-if="result"
          type="success"
          variant="tonal"
          class="mt-4"
          data-testid="migration-result"
          >{{
            tm(result.existing ? "migration.reused" : "migration.saved")
          }}</v-alert
        >
      </v-card-text>
      <v-card-actions class="flex-wrap">
        <v-spacer />
        <v-btn variant="text" :disabled="saving" @click="open = false">{{
          tm("v2.close")
        }}</v-btn>
        <v-btn
          v-if="result"
          variant="tonal"
          color="primary"
          data-testid="open-migrated-assets"
          @click="
            open = false;
            $emit('migrated');
          "
          >{{ tm("workbench.assets") }}</v-btn
        >
        <v-btn
          v-else
          variant="tonal"
          color="primary"
          :disabled="!preview || checking || (!preview.ready && !savePending)"
          :loading="saving"
          data-testid="confirm-migration"
          @click="confirm"
          >{{ tm("migration.confirm") }}</v-btn
        >
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { legacyMigrationApi } from "@/api/legacyMigration";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";

defineProps<{ teams: any[] }>();
defineEmits<{ migrated: [] }>();
const { tm } = useModuleI18n("features/agent-teams");
const open = ref(false);
const teamId = ref("");
const preview = ref<any>(null);
const result = ref<any>(null);
const checking = ref(false);
const saving = ref(false);
const savePending = ref(false);
const error = ref("");
let generation = 0;
watch([open, teamId], () => {
  generation++;
  preview.value = null;
  result.value = null;
  checking.value = false;
  savePending.value = false;
  error.value = "";
});
onBeforeUnmount(() => {
  generation++;
});

/** Load a preview belonging to the currently selected legacy team. */
async function loadPreview() {
  const current = ++generation;
  preview.value = null;
  result.value = null;
  error.value = "";
  savePending.value = false;
  checking.value = true;
  try {
    const response = await legacyMigrationApi.preview(teamId.value);
    if (current === generation && open.value) preview.value = response;
  } catch (cause) {
    if (current === generation)
      error.value = extractApiError(
        cause,
        tm("errors.operationFailed"),
      ).message;
  } finally {
    if (current === generation) checking.value = false;
  }
}

/** Confirm only the displayed proposal; completion never starts a run. */
async function confirm() {
  if (
    !preview.value ||
    saving.value ||
    checking.value ||
    (!preview.value.ready && !savePending.value)
  )
    return;
  const current = generation;
  saving.value = true;
  error.value = "";
  try {
    const response = await legacyMigrationApi.confirm(teamId.value, {
      preview_token: preview.value.preview_token,
      save_pending: savePending.value,
    });
    if (current === generation) result.value = response;
  } catch (cause) {
    if (current === generation) {
      const failure = extractApiError(cause, tm("errors.operationFailed"));
      error.value = `${failure.message} ${failure.fields
        .map((field) => `${field.path}: ${field.message}`)
        .join("; ")}`;
      preview.value = null;
    }
  } finally {
    saving.value = false;
  }
}
</script>

<style scoped>
.migration-text {
  min-width: 0;
  overflow-wrap: anywhere;
}
.migration-json {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 0.8rem;
  margin: 12px 0;
}
</style>
