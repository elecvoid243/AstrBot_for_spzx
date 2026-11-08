<template>
  <RemoteMessageResource
    v-if="attachmentId && scope"
    :scope="scope"
    :part="{
      type: 'image',
      attachment_id: attachmentId,
      filename: node.alt || 'image',
    }"
    :enabled="ready"
    @preview="openImage?.($event)"
  />
  <ImageNode v-else-if="publicSource" :node="node" />
  <span v-else>{{ node.alt || node.title || node.src }}</span>
</template>
<script setup lang="ts">
import { computed, inject, type Ref } from "vue";
import { ImageNode } from "markstream-vue";
import type { RemoteResourceScope } from "@/api/remoteConnections";
import RemoteMessageResource from "../RemoteMessageResource.vue";
const props = defineProps<{
  node: {
    type: "image";
    src: string;
    alt?: string;
    title?: string;
    [key: string]: unknown;
  };
}>();
const injectedScope = inject<Ref<RemoteResourceScope | undefined> | null>(
  "remoteChatScope",
  null,
);
const injectedReady = inject<Ref<boolean> | null>("remoteChatReady", null);
const openImage = inject<((url: string) => void) | undefined>(
  "openMessageImage",
  undefined,
);
const scope = computed(() => injectedScope?.value);
const ready = computed(() => injectedReady?.value === true);
const attachmentId = computed(() => {
  try {
    return decodeURIComponent(
      new URL(props.node.src, window.location.origin).pathname.match(
        /^\/api\/v1\/files\/([^/]+)\/content$/,
      )?.[1] || "",
    );
  } catch {
    return "";
  }
});
const publicSource = computed(() => {
  if (
    /^data:image\/(png|jpeg|gif|webp|bmp|x-icon|vnd.microsoft.icon);base64,/i.test(
      props.node.src,
    )
  )
    return true;
  try {
    const url = new URL(props.node.src);
    return (
      ["http:", "https:"].includes(url.protocol) &&
      url.origin !== window.location.origin
    );
  } catch {
    return false;
  }
});
</script>
