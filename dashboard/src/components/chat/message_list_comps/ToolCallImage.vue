<template>
  <v-dialog v-if="url && !failed" v-model="open" fullscreen :aria-label="label">
    <template #activator="{ props: activatorProps }">
      <button
        v-bind="activatorProps"
        type="button"
        class="tool-image-thumbnail"
        :title="label"
      >
        <img :src="url" :alt="label" @error="failed = true" />
      </button>
    </template>
    <div class="tool-image-fullscreen">
      <v-btn
        class="tool-image-close"
        icon="mdi-close"
        variant="tonal"
        :aria-label="t('core.common.close')"
        @click="open = false"
      />
      <img :src="url" :alt="label" />
    </div>
  </v-dialog>
  <div
    v-else
    class="tool-image-thumbnail tool-image-placeholder"
    role="status"
    :aria-label="label"
  >
    <v-icon
      size="20"
      :icon="failed ? 'mdi-image-off-outline' : 'mdi-image-outline'"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, inject, ref, watchEffect, type Ref } from "vue";
import { fetchWithAuth } from "@/api/http";
import { fileApi } from "@/api/v1";
import {
  remoteConnectionsApi,
  type RemoteResourceScope,
} from "@/api/remoteConnections";
import { useI18n } from "@/i18n/composables";

const props = defineProps<{
  attachmentId: string;
  filename: string;
  inert: boolean;
}>();
const scope = inject<Ref<RemoteResourceScope | undefined>>("remoteChatScope");
const remoteReady = inject<Ref<boolean>>("remoteChatReady");
const { t } = useI18n();
const label = computed(() => props.filename || t("core.common.imagePreview"));
const url = ref("");
const open = ref(false);
const failed = ref(false);

watchEffect((onCleanup) => {
  const remote = scope?.value;
  const id = props.attachmentId;
  const controller = new AbortController();
  const { signal } = controller;
  url.value = "";
  open.value = false;
  failed.value = false;
  let objectUrl = "";
  onCleanup(() => {
    controller.abort();
    if (objectUrl) URL.revokeObjectURL(objectUrl);
  });

  if (remote && remoteReady?.value === false) return;
  if (props.inert && !remote) {
    failed.value = true;
    return;
  }
  const image = remote
    ? remoteConnectionsApi
        .attachment(remote, id, signal)
        .then((response) => response.data)
    : fetchWithAuth(fileApi.contentUrl(id), { signal }).then((response) => {
        if (!response.ok)
          throw new Error(`Image request failed: ${response.status}`);
        return response.blob();
      });
  void image
    .then((blob) => {
      if (signal.aborted) return;
      if (!blob.type.startsWith("image/")) {
        failed.value = true;
        return;
      }
      objectUrl = URL.createObjectURL(blob);
      url.value = objectUrl;
    })
    .catch(() => {
      if (!signal.aborted) failed.value = true;
    });
});
</script>

<style scoped>
.tool-image-thumbnail {
  display: block;
  width: fit-content;
  max-width: 100%;
  padding: 0;
  overflow: hidden;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.2);
  border-radius: 8px;
  background: rgb(var(--v-theme-surface));
  cursor: zoom-in;
}

.tool-image-thumbnail:hover {
  border-color: rgb(var(--v-theme-primary));
}

.tool-image-thumbnail img {
  display: block;
  width: auto;
  height: auto;
  max-width: min(160px, 100%);
  max-height: 100px;
  object-fit: contain;
}

.tool-image-placeholder {
  display: grid;
  place-items: center;
  width: 100px;
  height: 64px;
  cursor: default;
}

.tool-image-fullscreen {
  position: relative;
  display: grid;
  place-items: center;
  width: 100%;
  height: 100%;
  padding: 56px 16px 16px;
  background: #161616;
  color: white;
}

.tool-image-fullscreen img {
  width: 100%;
  height: 100%;
  min-height: 0;
  object-fit: contain;
}

.tool-image-close {
  position: absolute;
  top: 8px;
  right: 8px;
}
</style>
