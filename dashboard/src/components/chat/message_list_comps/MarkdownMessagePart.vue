<template>
  <div class="markdown-content">
    <MarkdownRender
      class="chat-markdown"
      :custom-id="inert ? 'remote-message' : 'chat-message'"
      :nodes="inert ? inertNodes : undefined"
      :content="content"
      :custom-html-tags="inert ? [] : customHtmlTags"
      :is-dark="isDark"
      :final="!isStreaming"
      :smooth-streaming="isStreaming ? 'auto' : false"
      :fade="false"
      :typewriter="false"
      :max-live-nodes="MARKDOWN_RENDER_MAX_LIVE_NODES"
      :style="CHAT_MARKDOWN_HEADING_STYLE"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, provide } from "vue";
import {
  MarkdownRender,
  getMarkdown,
  parseMarkdownToStructure,
} from "markstream-vue";
import {
  CHAT_MARKDOWN_HEADING_STYLE,
  MARKDOWN_RENDER_MAX_LIVE_NODES,
} from "@/components/chat/markdownRenderConfig";

const props = defineProps<{
  content: string;
  inert?: boolean;
  refs: { used?: Array<Record<string, unknown>> } | null;
  isDark: boolean;
  customHtmlTags: string[];
  isStreaming?: boolean;
}>();

// Remote transcripts must not resolve links, images or custom tags against this instance.
const inertMarkdown = getMarkdown("remote-safe", {
  markdownItOptions: { html: false, linkify: false },
});
inertMarkdown.disable([
  "link",
  "image",
  "autolink",
  "html_inline",
  "html_block",
]);
const inertNodes = computed(() =>
  props.inert
    ? parseMarkdownToStructure(props.content, inertMarkdown, {
        final: !props.isStreaming,
        streamParse: false,
      })
    : undefined,
);

const isDarkRef = computed(() => props.isDark);
const refsByIndex = computed(() => {
  const messageRefs = props.refs;
  const refs =
    messageRefs && Array.isArray(messageRefs.used) ? messageRefs.used : [];
  return refs.reduce<Record<string, Record<string, unknown>>>((acc, item) => {
    if (item.index != null) {
      acc[String(item.index)] = item;
    }
    return acc;
  }, {});
});

provide("isDark", isDarkRef);
provide(
  "chatReadOnly",
  computed(() => props.inert === true),
);
provide("webSearchResults", () => refsByIndex.value);
</script>
