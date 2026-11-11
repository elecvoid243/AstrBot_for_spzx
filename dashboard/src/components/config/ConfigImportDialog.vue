<template>
  <v-dialog
    :model-value="modelValue"
    max-width="760"
    persistent
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card rounded="xl">
      <v-card-title
        class="text-h3 pa-4 pb-0 pl-6 d-flex align-center justify-space-between"
      >
        <span>{{ tm("configManagement.importTitle") }}</span>
        <v-btn
          icon="mdi-close"
          variant="text"
          :disabled="applying"
          @click="close"
        />
      </v-card-title>

      <v-card-text>
        <p class="text-medium-emphasis mb-4">
          {{ tm("configManagement.importHint") }}
        </p>

        <v-file-input
          v-model="fileInput"
          accept=".zip,.json"
          :label="tm('configManagement.importSelectFile')"
          prepend-icon="mdi-paperclip"
          variant="outlined"
          density="compact"
          hide-details
          :disabled="applying"
          @update:model-value="onFileChange"
        />

        <v-alert v-if="error" type="error" variant="tonal" class="mt-4">
          {{ error }}
        </v-alert>
        <v-progress-linear v-if="loading" indeterminate class="mt-4" />

        <template v-if="preview">
          <v-divider class="my-5" />

          <div class="text-subtitle-2 mb-2">
            {{ tm("configManagement.importConflictMode") }}
          </div>
          <v-radio-group
            v-model="conflictMode"
            inline
            hide-details
            :disabled="applying"
          >
            <v-radio
              value="overwrite"
              :label="tm('configManagement.importConflictOverwrite')"
            />
            <v-radio
              value="rename"
              :label="tm('configManagement.importConflictRename')"
            />
          </v-radio-group>

          <template v-if="preview.default?.included">
            <v-checkbox
              v-model="applyDefault"
              :label="tm('configManagement.importApplyDefault')"
              hide-details
              density="compact"
              class="mt-4"
              :disabled="applying"
            />
            <v-alert type="warning" variant="tonal" class="mt-3">
              {{ tm("configManagement.importDefaultHint") }}
            </v-alert>
          </template>

          <div class="import-list mt-4">
            <div
              v-for="(item, index) in preview.items"
              :key="`${item.source_id || 'new'}-${index}`"
              class="import-row"
            >
              <div class="flex-grow-1 min-width-0">
                <div class="font-weight-medium">{{ item.source_name }}</div>
                <div class="text-caption text-medium-emphasis">
                  {{ actionLabel(item) }}
                </div>
                <div v-if="item.error" class="text-error text-caption">
                  {{ item.error }}
                </div>
              </div>
              <v-chip
                :color="chipColor(item)"
                variant="tonal"
                size="small"
              >
                {{ actionLabel(item) }}
              </v-chip>
            </div>
          </div>

          <div class="text-caption text-medium-emphasis mt-3">
            {{ summaryText }}
          </div>
        </template>
      </v-card-text>

      <v-card-actions class="px-6 pb-5">
        <v-spacer />
        <v-btn variant="text" :disabled="applying" @click="close">
          {{ tm("buttons.cancel") }}
        </v-btn>
        <v-btn
          color="primary"
          variant="tonal"
          :loading="applying"
          :disabled="!canApply"
          @click="apply"
        >
          {{ tm("configManagement.importApply") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { configProfileApi } from "@/api/v1";
import { useModuleI18n } from "@/i18n/composables";

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
});

const emit = defineEmits(["update:modelValue", "imported"]);
const { tm } = useModuleI18n("features/config");

const fileInput = ref(null);
const preview = ref(null);
const error = ref("");
const loading = ref(false);
const applying = ref(false);
const conflictMode = ref("overwrite");
const applyDefault = ref(false);

const canApply = computed(() => {
  if (!preview.value || loading.value || applying.value) return false;
  return (
    preview.value.items.some((item) => item.action !== "error") ||
    preview.value.default?.action === "overwrite"
  );
});

const summaryText = computed(() => {
  const summary = preview.value?.summary;
  if (!summary) return "";
  return tm("configManagement.importSummary")
    .replace("{overwrite}", summary.overwrite)
    .replace("{create}", summary.create)
    .replace("{error}", summary.error);
});

function reset() {
  fileInput.value = null;
  preview.value = null;
  error.value = "";
  loading.value = false;
  applying.value = false;
  conflictMode.value = "overwrite";
  applyDefault.value = false;
}

function close() {
  if (applying.value) return;
  emit("update:modelValue", false);
}

function onFileChange(value) {
  preview.value = null;
  error.value = "";
  const file = Array.isArray(value) ? value[0] : value;
  if (!file) return;
  void loadPreview();
}

function currentFile() {
  const value = fileInput.value;
  return Array.isArray(value) ? value[0] : value;
}

function errorMessage(err) {
  return (
    err?.response?.data?.message ||
    err?.message ||
    tm("configManagement.importFailed")
  );
}

async function loadPreview() {
  const file = currentFile();
  if (!file) return;
  loading.value = true;
  error.value = "";
  try {
    const response = await configProfileApi.importProfiles(file, {
      dryRun: true,
      conflict: conflictMode.value,
      applyDefault: applyDefault.value,
    });
    if (response.data.status === "error") {
      throw new Error(response.data.message);
    }
    preview.value = response.data.data;
    if (!preview.value?.default?.included) {
      applyDefault.value = false;
    }
  } catch (err) {
    preview.value = null;
    error.value = errorMessage(err);
  } finally {
    loading.value = false;
  }
}

async function apply() {
  const file = currentFile();
  if (!file) return;
  applying.value = true;
  error.value = "";
  try {
    const response = await configProfileApi.importProfiles(file, {
      dryRun: false,
      conflict: conflictMode.value,
      applyDefault: applyDefault.value,
    });
    if (response.data.status === "error") {
      throw new Error(response.data.message);
    }
    emit("imported", response.data);
    emit("update:modelValue", false);
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    applying.value = false;
  }
}

function actionLabel(item) {
  if (item.action === "overwrite") {
    return tm("configManagement.importActionOverwrite").replace(
      "{name}",
      item.target_name,
    );
  }
  if (item.action === "create") {
    return tm("configManagement.importActionCreate").replace(
      "{name}",
      item.target_name,
    );
  }
  return tm("configManagement.importActionError");
}

function chipColor(item) {
  if (item.action === "overwrite") return "warning";
  if (item.action === "create") return "primary";
  return "error";
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) reset();
  },
);

watch([conflictMode, applyDefault], () => {
  if (props.modelValue && currentFile()) void loadPreview();
});
</script>

<style scoped>
.import-list {
  max-height: min(46vh, 360px);
  overflow-y: auto;
}

.import-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.min-width-0 {
  min-width: 0;
}
</style>
