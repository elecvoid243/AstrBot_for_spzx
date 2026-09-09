<template>
  <v-dialog v-model="isOpen" max-width="560" :persistent="submitting">
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ t('core.navigation.feedback.title') }}
      </v-card-title>
      <v-card-text class="pt-3">
        <div class="text-body-2 text-medium-emphasis mb-4">
          {{ t('core.navigation.feedback.subtitle') }}
        </div>

        <v-textarea
          v-model="content"
          :label="t('core.navigation.feedback.contentLabel')"
          :placeholder="t('core.navigation.feedback.contentPlaceholder')"
          variant="outlined"
          density="comfortable"
          rows="5"
          auto-grow
          counter
          maxlength="5000"
        ></v-textarea>

        <v-text-field
          v-model="contact"
          :label="t('core.navigation.feedback.contactLabel')"
          :placeholder="t('core.navigation.feedback.contactPlaceholder')"
          variant="outlined"
          density="comfortable"
        ></v-text-field>

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

<script setup>
import { computed, ref } from "vue";
import axios from "axios";
import { useI18n } from "@/i18n/composables";
import { useToastStore } from "@/stores/toast";
import { useCommonStore } from "@/stores/common";

const MAX_FILES = 10;
const IMAGE_RE = /\.(png|jpe?g|gif|webp|bmp)$/i;

const { t } = useI18n();
const toast = useToastStore();
const commonStore = useCommonStore();

const isOpen = ref(false);
const submitting = ref(false);
const content = ref("");
const contact = ref("");
const files = ref([]);
const fileInput = ref(null);
let fileSeq = 0;

const canSubmit = computed(() => content.value.trim().length > 0 && !submitting.value);
const versionText = computed(() => {
  const core = commonStore.astrbotVersion || "-";
  const dash = commonStore.dashboardVersion || "-";
  return `Core ${core} · Dashboard ${dash}`;
});

const open = () => {
  content.value = "";
  contact.value = "";
  files.value = [];
  submitting.value = false;
  isOpen.value = true;
};

function onFilesPicked(event) {
  const picked = Array.from(event.target.files || []);
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
  // 允许重复选择同一个文件
  if (fileInput.value) fileInput.value.value = "";
}

function removeFile(id) {
  files.value = files.value.filter((item) => item.id !== id);
}

function fileIcon(name) {
  if (IMAGE_RE.test(name)) return "mdi-image";
  if (/\.(zip|tar|gz|7z|rar)$/i.test(name)) return "mdi-zip-box";
  if (/\.(log|txt|json|yaml|yml)$/i.test(name)) return "mdi-text-box";
  return "mdi-paperclip";
}

function formatSize(bytes) {
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
    form.append("contact", contact.value.trim());
    for (const { file } of files.value) form.append("files", file);

    const resp = await axios.post("/api/system/feedback", form);
    toast.add({
      message: resp?.data?.message || t("core.navigation.feedback.success"),
      color: "success",
      timeout: 4000,
    });
    isOpen.value = false;
  } catch (err) {
    const message =
      err?.response?.data?.message || t("core.navigation.feedback.failed");
    toast.add({ message, color: "error", timeout: 5000 });
  } finally {
    submitting.value = false;
  }
}

defineExpose({ open });
</script>
