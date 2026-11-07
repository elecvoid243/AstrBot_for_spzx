<template>
  <div class="remote-resource">
    <template v-if="choice">
      <strong>{{ choice.title }}</strong>
      <p>{{ choice.prompt }}</p>
      <pre v-if="choice.extra_content" class="plain-content">{{
        choice.extra_content
      }}</pre>
      <p v-if="choice.answer || answer">
        {{ tm("interactiveChoice.alreadyChosen") }}:
        {{ (choice.answer || answer)?.choice_id }}
        {{ (choice.answer || answer)?.free_text }}
      </p>
      <p v-else-if="choice.resolved || expired">
        {{ tm("interactiveChoice.cancelled") }}
      </p>
      <template v-else>
        <div v-for="option in choice.options" :key="option.id" class="my-2">
          <v-btn
            variant="tonal"
            :disabled="!enabled || busy"
            @click="submit(option.id, '')"
            >{{ option.label }}</v-btn
          >
          <p v-if="option.description">{{ option.description }}</p>
        </div>
        <v-textarea
          v-model="freeText"
          :label="
            choice.input_placeholder ||
            tm('interactiveChoice.defaultPlaceholder')
          "
          rows="2"
          :disabled="!enabled || busy"
        />
        <v-btn
          variant="tonal"
          :disabled="!enabled || busy || !freeText.trim()"
          @click="submit('__free_text__', freeText)"
          >{{ tm("interactiveChoice.submit") }}</v-btn
        >
      </template>
    </template>
    <template v-else>
      <img
        v-if="imageUrl"
        :src="imageUrl"
        :alt="part.filename || tm('remoteConnections.attachment')"
        class="remote-image"
      />
      <div>{{ part.filename || tm("remoteConnections.attachment") }}</div>
      <v-btn variant="text" :disabled="!enabled || busy" @click="download">{{
        tm("remoteConnections.download")
      }}</v-btn>
    </template>
    <p v-if="error" role="alert">
      {{ tm(`remoteConnections.status.${error}`) }}
    </p>
  </div>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
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
}>();
const { tm } = useModuleI18n("features/chat");
const imageUrl = ref(""),
  error = ref(""),
  busy = ref(false),
  freeText = ref("");
const answer = ref<{ choice_id: string; free_text: string }>();
const choice = computed(() =>
  validateInteractiveChoice(props.part)
    ? (props.part as unknown as InteractiveChoicePart)
    : null,
);
const expired = computed(
  () => choice.value?.expires_at && choice.value.expires_at < Date.now() / 1000,
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
    if (imageUrl.value) URL.revokeObjectURL(imageUrl.value);
    imageUrl.value = "";
    answer.value = undefined;
    busy.value = false;
    error.value = "";
    if (
      !props.enabled ||
      props.part.type !== "image" ||
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
        [
          "image/png",
          "image/jpeg",
          "image/gif",
          "image/webp",
          "image/bmp",
        ].includes(response.data.type)
      )
        imageUrl.value = URL.createObjectURL(response.data);
    } catch {
      if (current === generation) error.value = "remote_unavailable";
    }
  },
  { immediate: true, flush: "sync" },
);
async function download() {
  if (!props.enabled || busy.value || !props.part.attachment_id) return;
  const current = generation;
  busy.value = true;
  try {
    const response = await remoteConnectionsApi.attachment(
      props.scope,
      props.part.attachment_id,
    );
    if (current !== generation) return;
    const url = URL.createObjectURL(response.data),
      link = document.createElement("a");
    link.href = url;
    link.download = props.part.filename || "attachment";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (err: any) {
    if (current === generation)
      error.value = err.response?.data?.data?.code || "remote_unavailable";
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
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value);
});
</script>
<style scoped>
.remote-image {
  max-width: 100%;
  max-height: 360px;
  object-fit: contain;
}
.remote-resource {
  margin: 8px 0;
}
</style>
