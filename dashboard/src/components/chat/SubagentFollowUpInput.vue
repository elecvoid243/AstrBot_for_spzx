<template>
  <ChatInput
    class="subagent-follow-up-input"
    v-model:prompt="draft"
    mode="subagent"
    :placeholder="tm('subagentFollowUp.placeholder', { name: agentName })"
    :send-shortcut="sendShortcut"
    :staged-images-url="stagedImagesUrl"
    :staged-audio-url="stagedAudioUrl"
    :staged-files="stagedNonImageFiles"
    :failed-uploads="failedUploadViews"
    :active-uploads="activeUploadViews"
    :disabled="disabled || sending"
    :send-disabled="uploading > 0"
    :enable-streaming="true"
    :is-recording="false"
    :is-running="false"
    @send="send"
    @file-select="stageFiles"
    @paste-image="pasteImage"
    @remove-image="removeImage"
    @remove-audio="removeAudio"
    @remove-file="removeFile"
    @retry-failed-upload="!sending && retryFailedUpload($event)"
    @discard-failed-upload="discardFailedUpload"
    @cancel-active-upload="cancelActiveUpload"
  >
    <template #above-status>
      <div v-if="sendError" class="subagent-send-error" role="alert">{{ sendError }}</div>
    </template>
  </ChatInput>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { chatApi } from "@/api/v1";
import { useMediaHandling } from "@/composables/useMediaHandling";
import { useModuleI18n } from "@/i18n/composables";
import ChatInput from "./ChatInput.vue";

const props = defineProps<{
  agentName: string;
  sessionId: string;
  runId: string;
  disabled: boolean;
  sendShortcut?: "enter" | "shift_enter";
}>();
const { tm } = useModuleI18n("features/chat");
const {
  stagedFiles, stagedImagesUrl, stagedAudioUrl, stagedNonImageFiles,
  activeUploadViews, failedUploadViews, processAndUploadFile, handlePaste,
  removeImage, removeAudio, removeFile, retryFailedUpload, discardFailedUpload,
  cancelActiveUpload, clearStaged,
} = useMediaHandling();
const draft = ref("");
const sending = ref(false);
const uploading = ref(0);
const sendError = ref("");

async function stageFiles(files: FileList | File[]) {
  uploading.value++;
  try {
    for (const file of Array.from(files)) await processAndUploadFile(file);
  } finally {
    uploading.value--;
  }
}

async function pasteImage(event: ClipboardEvent) {
  uploading.value++;
  try {
    await handlePaste(event);
  } finally {
    uploading.value--;
  }
}

async function send() {
  const text = draft.value.trim();
  if (props.disabled || sending.value || uploading.value || activeUploadViews.value.length) return;
  const attachments = stagedFiles.value.map(({ type, attachment_id, filename }) => ({
    type, attachment_id, filename,
  }));
  if (!text && !attachments.length) return;
  sending.value = true;
  sendError.value = "";
  try {
    const response = await chatApi.updateSubagentConversation({
      session_id: props.sessionId,
      subagent_run_id: props.runId,
      text,
      attachments,
      continue_conversation: true,
    });
    if (!response.data.data?.accepted) throw new Error("Subagent rejected the follow-up");
    draft.value = "";
    clearStaged();
  } catch {
    sendError.value = tm("subagentFollowUp.updateFailed");
  } finally {
    sending.value = false;
  }
}
</script>

<style scoped>
.subagent-follow-up-input {
  padding-block: 8px 12px;
}
.subagent-send-error {
  width: var(--chat-content-width, 80%);
  max-width: var(--chat-content-max-width, 960px);
  margin: 0 auto 6px;
  color: rgb(var(--v-theme-error));
}
</style>
