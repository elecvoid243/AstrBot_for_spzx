<template>
  <v-dialog v-model="isOpen" :width="dialogWidth" :persistent="submitting">
    <v-card class="feedback-card">
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ t('core.navigation.feedback.title') }}
      </v-card-title>
      <v-card-text class="feedback-body pt-3">
        <div class="text-body-2 text-medium-emphasis mb-4">
          {{ t('core.navigation.feedback.subtitle') }}
        </div>

        <v-textarea
          v-model="content"
          :label="t('core.navigation.feedback.contentLabel')"
          :placeholder="t('core.navigation.feedback.contentPlaceholder')"
          variant="outlined"
          density="comfortable"
          rows="16"
          counter
          maxlength="5000"
        ></v-textarea>

        <!-- Attachments: hidden multi-file input + removable chips -->
        <div class="mb-2">
          <div class="text-subtitle-2 mb-2">
            {{ t('core.navigation.feedback.attachments') }}
          </div>
          <input ref="fileInput" type="file" multiple hidden @change="onFilesPicked" />
          <div class="d-flex flex-wrap align-center ga-2">
            <v-chip
              v-for="item in files"
              :key="item.id"
              closable
              size="small"
              @click:close="removeFile(item.id)"
            >
              <v-icon start size="small">{{ fileIcon(item.file.name) }}</v-icon>
              {{ item.file.name }}
              <span class="text-medium-emphasis ml-1">({{ formatSize(item.file.size) }})</span>
            </v-chip>
            <v-btn
              variant="tonal"
              size="small"
              prepend-icon="mdi-paperclip"
              :disabled="files.length >= MAX_FILES"
              @click="fileInput?.click()"
            >
              {{ t('core.navigation.feedback.addAttachment') }}
            </v-btn>
          </div>
          <div class="text-caption text-medium-emphasis mt-2">
            {{ t('core.navigation.feedback.maxHint') }}
          </div>
        </div>

        <div v-if="versionText" class="text-caption text-medium-emphasis">
          {{ t('core.navigation.feedback.versionInfo', { info: versionText }) }}
        </div>
      </v-card-text>
      <v-card-actions>
        <v-spacer></v-spacer>
        <v-btn variant="text" :disabled="submitting" @click="isOpen = false">
          {{ t('core.navigation.feedback.cancel') }}
        </v-btn>
        <v-btn
          color="primary"
          variant="tonal"
          :loading="submitting"
          :disabled="!canSubmit"
          @click="submit"
        >
          {{ t('core.navigation.feedback.submit') }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script lang="ts">
// Module-level draft singleton: the draft survives dialog close and SPA route
// navigation, so users can leave to take screenshots and come back. Text is
// additionally mirrored into localStorage (restored on page reload);
// attachment File objects live in memory only. Cleared on successful submit.
import { ref } from "vue";

const DRAFT_KEY = "astrbot_feedback_draft_content";
const MAX_FILES = 10;

function loadDraftContent(): string {
  try {
    return localStorage.getItem(DRAFT_KEY) || "";
  } catch {
    return "";
  }
}

const content = ref(loadDraftContent());
const files = ref<{ id: number; file: File }[]>([]);
let fileSeq = 0;
</script>

<script setup lang="ts">
// `ref` is already imported in the module-scope <script> block above.
import { computed, watch } from "vue";
import axios from "axios";
import { useDisplay } from "vuetify";
import { useI18n } from "@/i18n/composables";
import { useToastStore } from "@/stores/toast";
import { useCommonStore } from "@/stores/common";

const IMAGE_RE = /\.(png|jpe?g|gif|webp|bmp)$/i;

const { t } = useI18n();
const toast = useToastStore();
const commonStore = useCommonStore();
const display = useDisplay();

// Width must come from the v-dialog prop (inline style on Vuetify's content
// wrapper) — sizing the card directly breaks the wrapper's centering.
const dialogWidth = computed(() => (display.width.value < 960 ? "92vw" : "62vw"));

const isOpen = ref(false);
const submitting = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);

// Text draft is mirrored into localStorage on every edit so it also
// survives a full page reload.
watch(content, (value) => {
  try {
    localStorage.setItem(DRAFT_KEY, value);
  } catch {
    // localStorage unavailable (quota / privacy mode): keep in-memory draft
  }
});

const canSubmit = computed(() => content.value.trim().length > 0 && !submitting.value);
const versionText = computed(() => {
  const core = commonStore.astrbotVersion || "-";
  const dash = commonStore.dashboardVersion || "-";
  return `Core ${core} · Dashboard ${dash}`;
});

const open = () => {
  // Deliberately do NOT reset the draft here: reopening must show what the
  // user typed before they cancelled.
  submitting.value = false;
  isOpen.value = true;
};

function onFilesPicked(event: Event) {
  const target = event.target as HTMLInputElement;
  const picked = Array.from(target.files || []);
  for (const file of picked) {
    if (files.value.length >= MAX_FILES) {
      toast.add({
        message: t("core.navigation.feedback.tooManyFiles", { max: MAX_FILES }),
        color: "warning",
      });
      break;
    }
    files.value.push({ id: ++fileSeq, file });
  }
  // Allow re-picking the same file after removal
  if (fileInput.value) fileInput.value.value = "";
}

function removeFile(id: number) {
  files.value = files.value.filter((item) => item.id !== id);
}

function clearDraft() {
  content.value = "";
  files.value = [];
  try {
    localStorage.removeItem(DRAFT_KEY);
  } catch {
    // ignore
  }
}

function fileIcon(name: string): string {
  if (IMAGE_RE.test(name)) return "mdi-image";
  if (/\.(zip|tar|gz|7z|rar)$/i.test(name)) return "mdi-zip-box";
  if (/\.(log|txt|json|yaml|yml)$/i.test(name)) return "mdi-text-box";
  return "mdi-paperclip";
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function submit() {
  if (!canSubmit.value) return;
  submitting.value = true;
  try {
    const form = new FormData();
    form.append("content", content.value.trim());
    form.append("astrbot_version", commonStore.astrbotVersion || "");
    form.append("dashboard_version", commonStore.dashboardVersion || "");
    for (const { file } of files.value) form.append("files", file);

    const resp = await axios.post("/api/system/feedback", form);
    toast.add({
      message: resp?.data?.message || t("core.navigation.feedback.success"),
      color: "success",
      timeout: 4000,
    });
    clearDraft();
    isOpen.value = false;
  } catch (err: any) {
    // Keep the draft on failure so the user can retry later.
    const message =
      err?.response?.data?.message || t("core.navigation.feedback.failed");
    toast.add({ message, color: "error", timeout: 5000 });
  } finally {
    submitting.value = false;
  }
}

defineExpose({ open });
</script>

<style scoped>
/* Height: at least 60% of the viewport for comfortable editing. Horizontal
   sizing/centering is handled by the v-dialog width prop. */
.feedback-card {
  min-height: 64vh;
  max-height: 92vh;
  display: flex;
  flex-direction: column;
}

.feedback-body {
  flex: 1 1 auto;
  overflow-y: auto;
}
</style>
