<!--
  Author: elecvoid243
  Date: 2026-10-08

  Expanded detail of one turn's LLM request injection trail: plugin +
  handler, one row per touched field with a signed delta, and the injected
  preview. A row whose change carries full text can be expanded in place
  (collapsed by default) to inspect the whole injection. Lossy changes
  (trims) get the warning tint, matching the compression chip's semantics.
-->
<template>
  <div class="injection-panel">
    <div class="injection-panel-head">
      <span class="injection-panel-title">{{
        tm("llmRequestInjections.title")
      }}</span>
      <span class="injection-panel-count">{{
        tm("llmRequestInjections.count", { count: changeCount })
      }}</span>
      <span class="injection-panel-note">{{
        tm("llmRequestInjections.note")
      }}</span>
    </div>

    <div
      v-for="(item, itemIndex) in items"
      :key="`${item.plugin}-${item.handler}-${itemIndex}`"
      class="injection-item"
    >
      <div class="injection-item-head">
        <span class="injection-item-plugin">{{ item.plugin }}</span>
        <span class="injection-item-handler">{{ item.handler }}</span>
      </div>

      <div
        v-for="(change, changeIndex) in item.changes"
        :key="`${change.field}-${changeIndex}`"
        class="injection-change"
        :class="{ 'is-lossy': change.lossy }"
      >
        <button
          type="button"
          class="injection-change-row"
          :class="{
            'is-expandable': canExpand(change),
            'is-open': isExpanded(itemIndex, changeIndex),
          }"
          :disabled="!canExpand(change)"
          :aria-expanded="
            canExpand(change)
              ? isExpanded(itemIndex, changeIndex)
              : undefined
          "
          @click="toggleChange(itemIndex, changeIndex)"
        >
          <span class="injection-change-field">{{ change.field }}</span>
          <span class="injection-change-delta">{{
            injectionChangeLabel(change, tm)
          }}</span>
          <v-icon
            v-if="canExpand(change)"
            class="injection-change-chevron"
            size="14"
          >
            mdi-chevron-right
          </v-icon>
        </button>

        <div
          v-if="isExpanded(itemIndex, changeIndex)"
          class="injection-change-full"
        >
          {{ change.full }}
        </div>
        <div v-else-if="change.preview" class="injection-change-preview">
          {{ change.preview }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";

import { useModuleI18n } from "@/i18n/composables";
import {
  injectionChangeLabel,
  type LlmRequestInjectionChange,
  type LlmRequestInjectionItem,
} from "@/composables/llmRequestInjections";

const props = defineProps<{ items: LlmRequestInjectionItem[] }>();

const { tm } = useModuleI18n("features/chat");

const changeCount = computed(() =>
  props.items.reduce(
    (total, item) => total + (item.changes?.length || 0),
    0,
  ),
);

// Rows expanded to their full text; collapsed by default so the panel stays
// a scannable summary until the detail is asked for.
const expandedChanges = ref(new Set<string>());

function canExpand(change: LlmRequestInjectionChange) {
  return Boolean(change.full && change.full !== change.preview);
}

function isExpanded(itemIndex: number, changeIndex: number) {
  return expandedChanges.value.has(`${itemIndex}:${changeIndex}`);
}

function toggleChange(itemIndex: number, changeIndex: number) {
  const key = `${itemIndex}:${changeIndex}`;
  const next = new Set(expandedChanges.value);
  if (!next.delete(key)) {
    next.add(key);
  }
  expandedChanges.value = next;
}
</script>

<style scoped>
.injection-panel {
  margin: 0 0 10px;
  border: 1px solid var(--chat-border, rgba(128, 128, 128, 0.25));
  border-radius: 12px;
  overflow: hidden;
  font-size: 12px;
}

.injection-panel-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 9px 13px;
  border-bottom: 1px solid var(--chat-border, rgba(128, 128, 128, 0.25));
}

.injection-panel-title {
  font-size: 12.5px;
  font-weight: 600;
}

.injection-panel-count,
.injection-panel-note {
  color: var(--chat-muted, #5c6673);
  font-size: 11px;
}

.injection-panel-note {
  margin-left: auto;
}

.injection-item {
  padding: 8px 13px;
}

.injection-item + .injection-item {
  border-top: 1px solid var(--chat-border, rgba(128, 128, 128, 0.25));
}

.injection-item-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.injection-item-plugin {
  font-size: 12.5px;
  font-weight: 600;
}

.injection-item-handler {
  margin-left: auto;
  color: var(--chat-muted, #5c6673);
  font-size: 11px;
}

.injection-change {
  margin-top: 5px;
}

.injection-change-row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  width: 100%;
  padding: 0;
  border: 0;
  background: none;
  text-align: left;
  font: inherit;
  color: inherit;
}

.injection-change-row.is-expandable {
  cursor: pointer;
}

.injection-change-row.is-expandable:hover .injection-change-chevron {
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.injection-change-field {
  color: var(--chat-muted, #5c6673);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}

.injection-change-delta {
  margin-left: auto;
  font-weight: 600;
}

.injection-change-chevron {
  margin-left: 2px;
  color: rgba(var(--v-theme-on-surface), 0.4);
  transition: transform 0.12s ease-out;
}

.injection-change-row.is-open .injection-change-chevron {
  transform: rotate(90deg);
}

.injection-change.is-lossy .injection-change-delta {
  color: rgb(var(--v-theme-warning));
}

.injection-change-preview,
.injection-change-full {
  margin-top: 4px;
  padding: 4px 8px;
  border-radius: 6px;
  background: rgba(var(--v-theme-on-surface, 0, 0, 0), 0.05);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 11.5px;
  line-height: 1.55;
}

.injection-change-preview {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.injection-change-full {
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 260px;
  overflow: auto;
  user-select: text;
}

.injection-change.is-lossy .injection-change-preview,
.injection-change.is-lossy .injection-change-full {
  color: rgb(var(--v-theme-warning));
  background: rgba(var(--v-theme-warning), 0.08);
}
</style>
