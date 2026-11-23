<script setup lang="ts">
import { computed, inject, unref, type Ref } from "vue";
import {
  ArrowUpRight,
  ChevronDown,
  FileText,
  MessageSquareText,
} from "@lucide/vue";
import { OPEN_FILE_REFERENCE } from "@/composables/fileReference";
import { useModuleI18n } from "@/i18n/composables";
import type { AssistantFileComment } from "@/utils/assistantFileComments";
import "../chatMarkdown.css";

const props = defineProps<{ comments: AssistantFileComment[]; readOnly?: boolean }>();
const openFile = inject(OPEN_FILE_REFERENCE, undefined);
const chatReadOnly = inject<Ref<boolean> | boolean>("chatReadOnly", false);
const { tm } = useModuleI18n("features/chat");
const canOpenFile = computed(
  () => Boolean(openFile && !props.readOnly && !unref(chatReadOnly)),
);
const entries = computed(() =>
  props.comments.map((comment) => {
    const separator =
      Math.max(comment.file.lastIndexOf("/"), comment.file.lastIndexOf("\\")) + 1;
    return {
      comment,
      directory: comment.file.slice(0, separator),
      name: comment.file.slice(separator),
      lines:
        comment.start_line === comment.end_line
          ? `L${comment.start_line}`
          : `L${comment.start_line}–${comment.end_line}`,
    };
  }),
);
</script>

<template>
  <details class="assistant-file-comments" open>
    <summary :aria-label="tm('assistantFileComments.count', { count: comments.length })">
      <MessageSquareText class="summary-icon" :size="14" aria-hidden="true" />
      {{ tm("assistantFileComments.title") }}
      <span class="count" aria-hidden="true">{{ comments.length }}</span>
      <ChevronDown class="summary-chevron" :size="14" aria-hidden="true" />
    </summary>
    <ul>
      <li v-for="({ comment, directory, name, lines }, index) in entries" :key="index">
        <div class="comment-title">
          <span v-if="comment.priority !== null" class="priority">P{{ comment.priority }}</span>
          <strong>{{ comment.title }}</strong>
        </div>
        <p>{{ comment.body }}</p>
        <button
          type="button"
          class="file-reference-link"
          :title="`${comment.file} · ${lines}`"
          :disabled="!canOpenFile"
          @click="
            openFile?.({
              path: comment.path,
              startLine: comment.start_line,
              endLine: comment.end_line,
              assistantComment: comment,
            })
          "
        >
          <FileText class="file-reference-icon" :size="14" :stroke-width="1.75" aria-hidden="true" />
          <span class="file-reference-label">
            <span v-if="directory" class="file-reference-directory">{{ directory }}</span>
            <span class="file-reference-name">{{ name }}</span>
          </span>
          <span class="file-reference-lines">{{ lines }}</span>
          <ArrowUpRight v-if="canOpenFile" class="file-reference-icon" :size="12" aria-hidden="true" />
        </button>
      </li>
    </ul>
  </details>
</template>

<style scoped>
.assistant-file-comments {
  margin-block: 1rem 0.65rem;
  border: 1px solid rgba(var(--v-theme-primary), 0.25);
  border-radius: 9px;
  background: rgb(var(--v-theme-surface));
  font-size: var(--chat-body-font-size);
  line-height: var(--chat-body-line-height);
}
summary {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 8px;
  background: rgba(var(--v-theme-primary), 0.06);
  font-weight: 600;
  list-style: none;
  cursor: pointer;
}
summary::-webkit-details-marker {
  display: none;
}
summary:hover {
  background: rgba(var(--v-theme-primary), 0.11);
}
summary:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: -2px;
}
.assistant-file-comments[open] summary {
  border-bottom: 1px solid rgba(var(--v-theme-primary), 0.16);
  border-radius: 8px 8px 0 0;
}
.summary-icon {
  flex: none;
  color: rgb(var(--v-theme-primary));
}
.summary-chevron {
  flex: none;
  margin-inline-start: auto;
  color: rgba(var(--v-theme-on-surface), 0.68);
}
.assistant-file-comments[open] .summary-chevron {
  transform: rotate(180deg);
}
.count {
  padding-inline: 5px;
  border-radius: 5px;
  background: rgba(var(--v-theme-primary), 0.12);
  font-size: 0.6875rem;
  line-height: 1.45;
  font-variant-numeric: tabular-nums;
}
ul {
  list-style: none;
  margin: 0;
  padding: 0;
}
li {
  padding: 10px 12px 12px;
}
li + li {
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.1);
}
.comment-title {
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.comment-title strong {
  font-weight: 600;
}
.priority {
  display: inline-block;
  margin-inline-end: 0.45em;
  padding: 1px 5px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.16);
  border-radius: 4px;
  color: rgba(var(--v-theme-on-surface), 0.68);
  font: 500 0.6875rem/1.22 "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  vertical-align: 0.09em;
}
p {
  margin: 4px 0 8px;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
</style>

<style scoped src="./fileReferenceChip.css"></style>
