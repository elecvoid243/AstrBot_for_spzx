<!--
  Author: elecvoid243
  Date: 2026-10-08

  Expanded detail of one turn's LLM request injection trail: plugin +
  handler, one row per touched field with a signed delta, and a preview of
  what was injected. Lossy changes (trims) get the warning tint, matching
  the compression chip's lossy semantics.
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
        <div class="injection-change-row">
          <span class="injection-change-field">{{ change.field }}</span>
          <span class="injection-change-delta">{{
            injectionChangeLabel(change, tm)
          }}</span>
        </div>
        <div v-if="change.preview" class="injection-change-preview">
          {{ change.preview }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

import { useModuleI18n } from "@/i18n/composables";
import {
  injectionChangeLabel,
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
</script>

<style scoped>
.injection-panel {
  margin-top: 10px;
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
}

.injection-change-field {
  color: var(--chat-muted, #5c6673);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}

.injection-change-delta {
  margin-left: auto;
  font-weight: 600;
}

.injection-change.is-lossy .injection-change-delta {
  color: rgb(var(--v-theme-warning));
}

.injection-change-preview {
  margin-top: 4px;
  padding: 4px 8px;
  border-radius: 6px;
  background: rgba(var(--v-theme-on-surface, 0, 0, 0), 0.05);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 11.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.injection-change.is-lossy .injection-change-preview {
  color: rgb(var(--v-theme-warning));
  background: rgba(var(--v-theme-warning), 0.08);
}
</style>
