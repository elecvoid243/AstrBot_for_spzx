<template>
  <component
    :is="node.type === 'html_inline' ? HtmlInlineNode : HtmlBlockNode"
    :node="safeNode"
    custom-id="remote-message"
  />
</template>
<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, watch, type Ref } from "vue";
import { HtmlBlockNode, HtmlInlineNode } from "markstream-vue";
import DOMPurify from "dompurify";
import {
  remoteConnectionsApi,
  type RemoteResourceScope,
} from "@/api/remoteConnections";

const props = defineProps<{
  node: {
    type: "html_inline" | "html_block";
    content: string;
    loading?: boolean;
  };
}>();
const scope = inject<Ref<RemoteResourceScope | undefined> | null>(
  "remoteChatScope",
  null,
);
const ready = inject<Ref<boolean> | null>("remoteChatReady", null);
const previews = ref<Record<string, string>>({});
const fragment = computed(() =>
  DOMPurify.sanitize(props.node.content, {
    RETURN_DOM_FRAGMENT: true,
    FORBID_ATTR: ["srcset", "action", "formaction", "background"],
  }),
);
const attachmentIds = computed(() =>
  Array.from(fragment.value.querySelectorAll("img[src]"), (image) => {
    try {
      return decodeURIComponent(
        new URL(
          image.getAttribute("src")!,
          window.location.origin,
        ).pathname.match(/^\/api\/v1\/files\/([^/]+)\/content$/)?.[1] || "",
      );
    } catch {
      return "";
    }
  }).filter(Boolean),
);
const safeNode = computed(() => {
  const content = fragment.value.cloneNode(true) as DocumentFragment;
  for (const element of content.querySelectorAll("*")) {
    for (const attribute of ["src", "href", "poster", "xlink:href"]) {
      const source = element.getAttribute(attribute);
      if (!source) continue;
      try {
        const url = new URL(source, window.location.origin);
        const id =
          element.tagName === "IMG"
            ? decodeURIComponent(
                url.pathname.match(
                  /^\/api\/v1\/files\/([^/]+)\/content$/,
                )?.[1] || "",
              )
            : "";
        if (id && previews.value[id])
          element.setAttribute(attribute, previews.value[id]);
        else if (
          !(
            /^https?:\/\//i.test(source) &&
            url.origin !== window.location.origin
          ) &&
          !/^data:image\/(png|jpeg|gif|webp|bmp|x-icon|vnd.microsoft.icon);base64,/i.test(
            source,
          ) &&
          !(attribute === "href" && /^mailto:/i.test(source))
        )
          element.removeAttribute(attribute);
      } catch {
        element.removeAttribute(attribute);
      }
    }
    if (/url\s*\(|@import/i.test(element.getAttribute("style") || ""))
      element.removeAttribute("style");
  }
  const template = document.createElement("template");
  template.content.append(content);
  return {
    ...props.node,
    content: template.innerHTML,
    children: undefined,
    attrs: undefined,
    tag: undefined,
  };
});
let generation = 0;
let abort: AbortController | undefined;
watch(
  () => JSON.stringify([scope?.value, ready?.value, attachmentIds.value]),
  async () => {
    const current = ++generation;
    abort?.abort();
    abort = new AbortController();
    previews.value = {};
    if (!scope?.value || !ready?.value) return;
    for (const id of new Set(attachmentIds.value)) {
      try {
        const response = await remoteConnectionsApi.attachment(
          scope.value,
          id,
          abort.signal,
        );
        if (generation !== current) return;
        if (
          [
            "image/png",
            "image/jpeg",
            "image/gif",
            "image/webp",
            "image/bmp",
            "image/x-icon",
            "image/vnd.microsoft.icon",
          ].includes(response.data.type)
        ) {
          const source = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result));
            reader.onerror = reject;
            reader.readAsDataURL(response.data);
          });
          if (generation !== current) return;
          previews.value = {
            ...previews.value,
            [id]: source,
          };
        }
      } catch {
        /* Keep unavailable resources inert instead of loading them locally. */
      }
    }
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  generation++;
  abort?.abort();
});
</script>
