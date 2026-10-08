<!--
  QuickMessagesChip — click-to-send phrases in the ChatUI input row, sitting
  next to the thinking-effort chip.

  The list lives in cmd_config.json (`chatui.quick_messages`); this chip is a
  pure surface. Clicking a row emits "send" with the full content, which
  ChatInput turns into a normal send. Rows show one truncated line and expose
  the full text through the native title tooltip, so a long template never
  widens the popup. Menu/row styling follows the ThinkingEffortChip pattern
  (same --sp-* chip tokens and 8px row rhythm).
-->
<script setup lang="ts">
import { ref } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import type { QuickMessage } from "@/composables/quickMessages";

const props = defineProps<{
  items: QuickMessage[];
  /** Config editors only — without it the edit row stays hidden. */
  canEdit?: boolean;
}>();

const emit = defineEmits<{
  (e: "send", content: string): void;
  (e: "edit"): void;
}>();

const { tm } = useModuleI18n("features/chat");

const menuOpen = ref(false);

function select(item: QuickMessage): void {
  menuOpen.value = false;
  emit("send", item.content);
}

function openEditor(): void {
  menuOpen.value = false;
  emit("edit");
}
</script>

<template>
  <v-menu
    v-model="menuOpen"
    location="top end"
    origin="bottom end"
    transition="none"
    :close-on-content-click="false"
  >
    <template #activator="{ props: menuProps }">
      <v-tooltip location="top" :open-delay="200">
        <template #activator="{ props: tipProps }">
          <button
            v-bind="{ ...tipProps, ...menuProps }"
            type="button"
            class="quick-chip-btn"
            :class="{ 'quick-chip-btn--open': menuOpen }"
            :aria-label="tm('input.quickMessages')"
          >
            <v-icon size="14" class="quick-chip-btn__icon">
              mdi-lightning-bolt
            </v-icon>
            <span class="quick-chip-btn__label">
              {{ tm("input.quickMessages") }}
            </span>
            <v-icon size="12" class="quick-chip-btn__chevron">
              mdi-menu-down
            </v-icon>
          </button>
        </template>
        <span>{{ tm("input.quickMessages") }}</span>
      </v-tooltip>
    </template>
    <v-card class="quick-chip-card">
      <v-card-text>
        <div class="quick-chip-title">{{ tm("input.quickMessages") }}</div>

        <template v-if="items.length">
          <button
            v-for="item in items"
            :key="item.id"
            type="button"
            class="quick-chip-row"
            :title="item.content"
            @click="select(item)"
          >
            <span class="quick-chip-row__text">{{ item.content }}</span>
          </button>
        </template>
        <div v-else class="quick-chip-empty">
          {{ tm("input.quickMessagesEmpty") }}
        </div>

        <template v-if="canEdit">
          <div class="quick-chip-divider"></div>
          <button
            type="button"
            class="quick-chip-row quick-chip-row--edit"
            @click="openEditor"
          >
            <v-icon size="14" class="quick-chip-row__gear">
              mdi-cog-outline
            </v-icon>
            <span class="quick-chip-row__text">
              {{ tm("input.editQuickMessages") }}
            </span>
          </button>
        </template>
      </v-card-text>
    </v-card>
  </v-menu>
</template>

<style scoped>
.quick-chip-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 9px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.72);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition:
    background-color 150ms ease,
    color 150ms ease;
}

.quick-chip-btn:hover {
  background: var(--sp-ghost-hover-bg, rgba(var(--v-theme-on-surface), 0.055));
  color: rgb(var(--v-theme-on-surface));
}

.quick-chip-btn--open {
  background: var(--sp-ghost-open-bg, rgba(var(--v-theme-on-surface), 0.07));
  color: rgb(var(--v-theme-on-surface));
}

.quick-chip-btn:active {
  background: var(--sp-chip-active-bg);
}

.quick-chip-btn:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 1px;
}

.quick-chip-btn__icon,
.quick-chip-btn__chevron {
  opacity: 0.7;
}

.quick-chip-btn__label {
  max-width: 96px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Cap the popup: rows truncate instead of stretching the card, which keeps
   the empty right edge (and the jump on long phrases) out of the picture. */
.quick-chip-card {
  min-width: 184px;
  max-width: 300px;
}

.quick-chip-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--sp-text-primary);
  margin-bottom: 4px;
}

.quick-chip-row {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 5px 8px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--sp-text-primary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.quick-chip-row:hover {
  background: var(--sp-chip-hover-bg);
}

.quick-chip-row__text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.quick-chip-empty {
  padding: 4px 8px 6px;
  color: var(--sp-text-muted);
  font-size: 12px;
  line-height: 1.45;
  white-space: normal;
}

.quick-chip-divider {
  height: 1px;
  margin: 4px 2px;
  background: var(--sp-chip-divider);
}

.quick-chip-row--edit {
  color: var(--sp-text-muted);
}

.quick-chip-row--edit:hover {
  color: var(--sp-text-primary);
}

.quick-chip-row__gear {
  flex-shrink: 0;
  opacity: 0.75;
}
</style>
