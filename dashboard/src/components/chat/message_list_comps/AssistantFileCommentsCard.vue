<script setup lang="ts">
import { inject, unref, type Ref } from "vue";
import { OPEN_FILE_REFERENCE } from "@/composables/fileReference";
import { useModuleI18n } from "@/i18n/composables";
import type { AssistantFileComment } from "@/utils/assistantFileComments";

defineProps<{ comments: AssistantFileComment[]; readOnly?: boolean }>();
const openFile = inject(OPEN_FILE_REFERENCE, undefined);
const chatReadOnly = inject<Ref<boolean> | boolean>("chatReadOnly", false);
const { tm } = useModuleI18n("features/chat");
</script>

<template>
  <details class="assistant-file-comments" open>
    <summary>
      {{ tm("assistantFileComments.count", { count: comments.length }) }}
    </summary>
    <ul>
      <li v-for="(comment, index) in comments" :key="index">
        <button
          type="button"
          :disabled="readOnly || unref(chatReadOnly) || !openFile"
          @click="
            openFile?.({
              path: comment.path,
              startLine: comment.start_line,
              endLine: comment.end_line,
              assistantComment: comment,
            })
          "
        >
          <span v-if="comment.priority !== null" class="priority"
            >P{{ comment.priority }}</span
          >
          <strong>{{ comment.title }}</strong>
          <span class="location">
            {{ comment.file }}:{{ comment.start_line
            }}{{
              comment.start_line === comment.end_line
                ? ""
                : `–${comment.end_line}`
            }}
          </span>
        </button>
        <p>{{ comment.body }}</p>
      </li>
    </ul>
  </details>
</template>

<style scoped>
.assistant-file-comments {
  margin-block: 0.65rem;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.16);
  border-radius: 8px;
}
summary {
  padding: 0.65rem 0.8rem;
  cursor: pointer;
}
ul {
  list-style: none;
  margin: 0;
  padding: 0;
}
li {
  padding: 0.45rem 0.8rem;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.1);
}
button {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 0.25rem 0.5rem;
  text-align: start;
}
button:enabled:hover strong {
  text-decoration: underline;
}
.priority,
.location {
  font-size: 0.8em;
  color: rgba(var(--v-theme-on-surface), 0.68);
}
.priority {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.2);
  border-radius: 4px;
  padding-inline: 0.25rem;
}
.location,
p {
  overflow-wrap: anywhere;
}
p {
  margin: 0.15rem 0 0;
  white-space: pre-wrap;
}
</style>
