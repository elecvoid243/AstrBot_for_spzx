<template>
  <div :class="choice ? 'remote-resource' : 'remote-attachment-resource'">
    <InteractiveChoiceBox
      v-if="displayChoice"
      :part="displayChoice"
      umo=""
      managed
      :is-dark="isDark"
      :is-ignored="isIgnored"
      :disabled="!enabled || busy"
      @submit="(_id, payload) => submit(payload.choice_id, payload.free_text)"
      @cancel="cancel"
    />
    <MessageAttachment
      v-else
      :part="part"
      :src="previewUrl"
      :sent="sent"
      :disabled="!enabled"
      :busy="busy"
      :downloadable="false"
      @preview="emit('preview', $event)"
    />
    <p v-if="error" role="alert">
      {{ tm(`remoteConnections.status.${error}`) }}
    </p>
  </div>
</template>
<script setup lang="ts">
import InteractiveChoiceBox from "./message_list_comps/InteractiveChoiceBox.vue";
import MessageAttachment from "./MessageAttachment.vue";
import { computed, onBeforeUnmount, provide, ref, watch } from "vue";
import {
  remoteConnectionsApi,
  type RemoteResourceScope,
} from "@/api/remoteConnections";
import {
  validateInteractiveChoice,
  type InteractiveChoicePart,
} from "@/composables/parseInteractiveChoice";
import type { MessagePart } from "@/composables/useMessages";
import { accountSessionRevision } from "@/utils/accountSession";
import { useModuleI18n } from "@/i18n/composables";
const props = defineProps<{
  scope: RemoteResourceScope;
  part: MessagePart;
  enabled: boolean;
  sent?: boolean;
  isDark?: boolean;
  isIgnored?: boolean;
}>();
provide("chatReadOnly", true);
const emit = defineEmits<{ preview: [url: string] }>();
const { tm } = useModuleI18n("features/chat");
const previewUrl = ref(""),
  error = ref(""),
  busy = ref(false);
const cancelled = ref(false);
const answer = ref<{ choice_id: string; free_text: string }>();
const choice = computed(() =>
  validateInteractiveChoice(props.part)
    ? (props.part as unknown as InteractiveChoicePart)
    : null,
);
const expired = computed(
  () =>
    !choice.value?.answer &&
    !answer.value &&
    !choice.value?.resolved &&
    choice.value?.expires_at &&
    choice.value.expires_at < Date.now() / 1000,
);
const displayChoice = computed(() =>
  choice.value
    ? {
        ...choice.value,
        ...(answer.value
          ? { answer: { ...answer.value, answered_at: Date.now() / 1000 } }
          : {}),
        ...(cancelled.value || expired.value
          ? {
              resolved: {
                reason: "cancelled" as const,
                resolved_at: Date.now() / 1000,
              },
            }
          : {}),
      }
    : null,
);
const identity = () =>
  JSON.stringify([
    accountSessionRevision.value,
    props.scope,
    props.part.attachment_id,
    props.part.request_id,
    props.enabled,
  ]);
let generation = 0,
  abort: AbortController | undefined;
watch(
  identity,
  async () => {
    const current = ++generation;
    abort?.abort();
    abort = new AbortController();
    if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
    previewUrl.value = "";
    answer.value = undefined;
    cancelled.value = false;
    busy.value = false;
    error.value = "";
    if (
      !props.enabled ||
      !["image", "record", "audio", "video"].includes(props.part.type) ||
      !props.part.attachment_id
    )
      return;
    try {
      const response = await remoteConnectionsApi.attachment(
        props.scope,
        props.part.attachment_id,
        abort.signal,
      );
      if (current !== generation) return;
      if (
        (props.part.type === "image" &&
          [
            "image/png",
            "image/jpeg",
            "image/gif",
            "image/webp",
            "image/bmp",
            "image/x-icon",
            "image/vnd.microsoft.icon",
          ].includes(response.data.type)) ||
        (["record", "audio"].includes(props.part.type) &&
          response.data.type.startsWith("audio/")) ||
        (props.part.type === "video" && response.data.type.startsWith("video/"))
      )
        previewUrl.value = URL.createObjectURL(response.data);
    } catch {
      if (current === generation) error.value = "remote_unavailable";
    }
  },
  { immediate: true, flush: "sync" },
);
async function cancel() {
  if (
    !props.enabled ||
    busy.value ||
    !choice.value ||
    displayChoice.value?.answer ||
    displayChoice.value?.resolved
  )
    return;
  const current = generation;
  busy.value = true;
  error.value = "";
  try {
    await remoteConnectionsApi.cancelChoice(
      props.scope,
      choice.value.request_id,
    );
    if (current === generation) cancelled.value = true;
  } catch (err: any) {
    if (current === generation)
      error.value = err.response?.data?.data?.code || "remote_rejected";
  } finally {
    if (current === generation) busy.value = false;
  }
}
async function submit(choice_id: string, free_text: string) {
  if (
    !props.enabled ||
    busy.value ||
    answer.value ||
    props.part.answer ||
    props.part.resolved
  )
    return;
  const current = generation;
  busy.value = true;
  error.value = "";
  try {
    await remoteConnectionsApi.answer(
      props.scope,
      String(props.part.request_id),
      {
        choice_id,
        free_text,
      },
    );
    if (current === generation) answer.value = { choice_id, free_text };
  } catch (err: any) {
    if (current === generation)
      error.value = err.response?.data?.data?.code || "remote_rejected";
  } finally {
    if (current === generation) busy.value = false;
  }
}
onBeforeUnmount(() => {
  generation++;
  abort?.abort();
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
});
</script>
<style scoped>
.remote-attachment-resource {
  display: contents;
}
.remote-resource {
  margin: 8px 0;
}
</style>
