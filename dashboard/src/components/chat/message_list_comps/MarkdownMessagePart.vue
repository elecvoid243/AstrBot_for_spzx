<template>
  <div class="markdown-content">
    <div v-if="inert" class="chat-markdown" v-html="inertHtml" />
    <MarkdownRender
      v-else
      class="chat-markdown"
      custom-id="chat-message"
      :content="content"
      :custom-html-tags="customHtmlTags"
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
import MarkdownIt from "markdown-it";
import { MarkdownRender } from "markstream-vue";
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
const inertMarkdown = new MarkdownIt({ html: false, linkify: false });
inertMarkdown.disable(["link", "image", "autolink"]);
const inertHtml = computed(() => props.inert
  ? inertMarkdown.render(props.content) : "");

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
provide("webSearchResults", () => refsByIndex.value);
</script>
