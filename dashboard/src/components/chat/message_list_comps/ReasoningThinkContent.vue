<template>
  <template v-if="showOriginal">
    <template v-for="(segment, index) in originalSegments" :key="index">
      <MarkdownMessagePart
        v-if="segment.kind === 'text' && inert"
        :content="segment.text"
        :refs="null"
        :is-dark="Boolean(isDark)"
        :custom-html-tags="[]"
        :is-streaming="isStreaming"
        inert
      />
      <MarkdownRender
        v-else-if="segment.kind === 'text'"
        :content="segment.text"
        class="chat-markdown reasoning-text markdown-content"
        :final="!isStreaming"
        :smooth-streaming="isStreaming ? 'auto' : false"
        :fade="false"
        :typewriter="false"
        :is-dark="isDark"
        :max-live-nodes="MARKDOWN_RENDER_MAX_LIVE_NODES"
        :style="CHAT_MARKDOWN_HEADING_STYLE"
      />
      <SanitizerFoldRule
        v-else-if="segment.kind === 'period' || segment.kind === 'run'"
        :kind="segment.kind"
        :repeats="segment.repeats"
        :period="segment.period"
        :interactive="interactive"
        :active="true"
        @toggle="emit('toggle-original')"
      />
      <SanitizerFallbackBlock
        v-else-if="segment.kind === 'fallback'"
        :lines="segment.lines"
        :chars="segment.chars"
        :interactive="interactive"
        :active="true"
        @toggle="emit('toggle-original')"
      />
    </template>
  </template>
  <template v-else>
    <template v-for="(segment, index) in segments" :key="index">
      <MarkdownMessagePart
        v-if="segment.kind === 'text' && inert"
        :content="segment.text"
        :refs="null"
        :is-dark="Boolean(isDark)"
        :custom-html-tags="[]"
        :is-streaming="isStreaming"
        inert
      />
      <MarkdownRender
        v-else-if="segment.kind === 'text'"
        :content="segment.text"
        class="chat-markdown reasoning-text markdown-content"
        :final="!isStreaming"
        :smooth-streaming="isStreaming ? 'auto' : false"
        :fade="false"
        :typewriter="false"
        :is-dark="isDark"
        :max-live-nodes="MARKDOWN_RENDER_MAX_LIVE_NODES"
        :style="CHAT_MARKDOWN_HEADING_STYLE"
      />
      <SanitizerFoldRule
        v-else-if="segment.kind === 'period' || segment.kind === 'run'"
        :kind="segment.kind"
        :repeats="segment.repeats"
        :period="segment.period"
        :interactive="interactive"
        :active="false"
        @toggle="emit('toggle-original')"
      />
      <SanitizerFallbackBlock
        v-else-if="segment.kind === 'fallback'"
        :lines="segment.lines"
        :chars="segment.chars"
        :interactive="interactive"
        :active="false"
        @toggle="emit('toggle-original')"
      />
    </template>
  </template>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { MarkdownRender } from "markstream-vue";
import {
  CHAT_MARKDOWN_HEADING_STYLE,
  MARKDOWN_RENDER_MAX_LIVE_NODES,
} from "@/components/chat/markdownRenderConfig";
import MarkdownMessagePart from "./MarkdownMessagePart.vue";
import SanitizerFoldRule from "./SanitizerFoldRule.vue";
import SanitizerFallbackBlock from "./SanitizerFallbackBlock.vue";
import {
  buildOriginalSegments,
  parseThinkSegments,
  type ThinkSegment,
} from "./reasoningSanitizer";

const props = defineProps<{
  content: string;
  originalContent?: string;
  showOriginal?: boolean;
  isDark?: boolean;
  isStreaming?: boolean;
  inert?: boolean;
  interactive?: boolean;
}>();

const emit = defineEmits<{ "toggle-original": [] }>();

type RenderSegment = {
  kind: ThinkSegment["kind"];
  text: string;
  repeats: number;
  period: number;
  lines: number;
  chars: number;
};

function toRenderSegment(segment: ThinkSegment): RenderSegment {
  return {
    kind: segment.kind,
    text: segment.kind === "text" ? segment.text : "",
    repeats:
      segment.kind === "period" || segment.kind === "run"
        ? segment.repeats
        : 0,
    period: segment.kind === "period" ? segment.period : 1,
    lines: segment.kind === "fallback" ? segment.lines : 0,
    chars: segment.kind === "fallback" ? segment.chars : 0,
  };
}

const segments = computed(() =>
  parseThinkSegments(props.content).map(toRenderSegment),
);

const originalSegments = computed(() =>
  props.originalContent
    ? buildOriginalSegments(props.content, props.originalContent).map(
        toRenderSegment,
      )
    : [],
);
</script>
