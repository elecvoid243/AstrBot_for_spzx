<!-- Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md §5.5
     Three-stage session-import flow (pick the package → preview what it
     holds → import result) backed by `chatApi.importSessions` /
     `chatApi.confirmImportSessions`. -->
<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import { chatApi } from "@/api/v1";

const props = defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  imported: [newSessionIds: string[]];
}>();

const { tm } = useModuleI18n("features/chat");

type Stage = "idle" | "uploading" | "preview" | "importing" | "done";
const stage = ref<Stage>("idle");
const selectedFileName = ref("");
const preview = ref<any | null>(null);
const result = ref<any | null>(null);
const errorMessage = ref("");

const dialogVisible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit("update:modelValue", value),
});

const canConfirm = computed(
  () => Boolean(preview.value?.can_import) && stage.value === "preview",
);

function reset() {
  stage.value = "idle";
  selectedFileName.value = "";
  preview.value = null;
  result.value = null;
  errorMessage.value = "";
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) reset();
  },
);

async function onFileSelected(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  selectedFileName.value = file.name;
  errorMessage.value = "";
  stage.value = "uploading";
  const formData = new FormData();
  formData.append("file", file);
  try {
    const response = await chatApi.importSessions(formData);
    preview.value = response.data?.data ?? null;
    stage.value = "preview";
  } catch (error: any) {
    errorMessage.value =
      error?.response?.data?.message || tm("import.uploadFailed");
    stage.value = "idle";
  } finally {
    input.value = "";
  }
}

async function confirmImport() {
  if (!preview.value?.import_id) return;
  stage.value = "importing";
  errorMessage.value = "";
  try {
    const response = await chatApi.confirmImportSessions(preview.value.import_id);
    result.value = response.data?.data ?? null;
    stage.value = "done";
    const newIds = (result.value?.created || [])
      .map((item: any) => item.new_session_id)
      .filter(Boolean);
    emit("imported", newIds);
  } catch (error: any) {
    errorMessage.value =
      error?.response?.data?.message || tm("import.confirmFailed");
    stage.value = "preview";
  }
}
</script>

<template>
  <v-dialog v-model="dialogVisible" max-width="640" persistent>
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ tm("import.title") }}
      </v-card-title>
      <v-card-text class="pt-4">
        <div v-if="stage === 'idle' || stage === 'uploading'">
          <p class="mb-4 text-medium-emphasis">{{ tm("import.description") }}</p>
          <v-file-input
            accept=".zip,application/zip"
            :label="tm('import.selectFile')"
            variant="outlined"
            density="comfortable"
            :loading="stage === 'uploading'"
            @change="onFileSelected"
          />
        </div>

        <div v-else-if="stage === 'preview' || stage === 'importing'">
          <p class="mb-2">{{ selectedFileName }}</p>
          <v-list density="compact">
            <v-list-item
              v-for="(item, index) in preview?.sessions || []"
              :key="index"
            >
              <v-list-item-title>
                {{ item.display_name || tm("conversation.newConversation") }}
              </v-list-item-title>
              <v-list-item-subtitle>
                {{ item.original_creator }} ·
                {{ tm("import.messageCount", { count: item.stats?.messages ?? 0 }) }}
                ·
                {{ tm("import.attachmentCount", { count: item.stats?.attachments ?? 0 }) }}
              </v-list-item-subtitle>
            </v-list-item>
          </v-list>

          <v-alert
            v-if="preview?.version_status?.upgrade_advised"
            type="warning"
            variant="tonal"
            class="mt-3"
          >
            {{ tm("import.versionMismatch") }}
          </v-alert>
          <v-alert
            v-for="(warning, index) in preview?.warnings || []"
            :key="`w-${index}`"
            type="warning"
            variant="tonal"
            density="compact"
            class="mt-2"
          >
            {{ warning }}
          </v-alert>
          <v-alert
            v-if="preview && !preview.can_import"
            type="error"
            variant="tonal"
            class="mt-2"
          >
            {{ tm("import.empty") }}
          </v-alert>
        </div>

        <div v-else class="text-center py-4">
          <p class="mb-2">{{ tm("import.done") }}</p>
          <v-alert
            v-for="(warning, index) in result?.warnings || []"
            :key="`rw-${index}`"
            type="warning"
            variant="tonal"
            density="compact"
            class="mb-2 text-left"
          >
            {{ warning }}
          </v-alert>
          <v-alert
            v-for="(failure, index) in result?.errors || []"
            :key="`re-${index}`"
            type="error"
            variant="tonal"
            density="compact"
            class="mb-2 text-left"
          >
            {{ failure }}
          </v-alert>
        </div>

        <v-alert
          v-if="errorMessage"
          type="error"
          variant="tonal"
          density="compact"
          class="mt-3"
        >
          {{ errorMessage }}
        </v-alert>
      </v-card-text>

      <v-card-actions class="px-6 pb-4">
        <v-btn variant="text" @click="dialogVisible = false">
          {{ tm("import.close") }}
        </v-btn>
        <v-spacer />
        <v-btn
          v-if="stage === 'preview' || stage === 'importing'"
          variant="tonal"
          color="primary"
          :disabled="!canConfirm"
          :loading="stage === 'importing'"
          @click="confirmImport"
        >
          {{ tm("import.confirm") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
