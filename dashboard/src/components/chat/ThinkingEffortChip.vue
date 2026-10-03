<!--
  ThinkingEffortChip — compact dropdown for the per-message thinking
  effort (reasoning intensity) override, living in the input row next
  to the token-usage ring. Moved out of the "+" menu (2026-09-01): the
  effort level is a send-time companion of the context budget, so the
  two controls sit together.

  The chip is a pure UI surface: the selection state (localStorage
  "thinkingEffort") and the custom level list stay owned by ChatInput,
  which forwards them via v-model/:levels and opens the level editor
  dialog when this chip emits "edit". Menu/row styling follows the
  FileAccessModeChip pattern (same --sp-* chip tokens).
-->
<script lang="ts">
export interface ThinkingEffortLevel {
  name: string;
  value: string;
}
</script>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import type { ThinkingEffort } from "@/composables/useMessages";

const props = defineProps<{
  modelValue: ThinkingEffort;
  levels: ThinkingEffortLevel[];
}>();

const emit = defineEmits<{
  (e: "update:modelValue", value: ThinkingEffort): void;
  (e: "edit"): void;
}>();

const { tm } = useModuleI18n("features/chat");

const menuOpen = ref(false);

const activeLabel = computed<string>(
  () =>
    props.levels.find((level) => level.value === props.modelValue)?.name ??
    props.modelValue,
);

function select(value: ThinkingEffort): void {
  menuOpen.value = false;
  if (value === props.modelValue) return;
  emit("update:modelValue", value);
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
  >
    <template #activator="{ props: menuProps }">
      <v-tooltip location="top" :open-delay="200">
        <template #activator="{ props: tipProps }">
          <button
            v-bind="{ ...tipProps, ...menuProps }"
            type="button"
            class="effort-chip-btn"
            :class="{ 'effort-chip-btn--open': menuOpen }"
            :aria-label="tm('input.thinkingEffort')"
          >
            <v-icon size="14" class="effort-chip-btn__icon">mdi-brain</v-icon>
            <span class="effort-chip-btn__label">{{ activeLabel }}</span>
            <v-icon size="12" class="effort-chip-btn__chevron">
              mdi-menu-down
            </v-icon>
          </button>
        </template>
        <span>{{ tm("input.thinkingEffort") }}</span>
      </v-tooltip>
    </template>
    <v-card>
      <v-card-text>
        <div class="effort-chip-title">{{ tm("input.thinkingEffort") }}</div>
        <button
          v-for="level in levels"
          :key="level.value"
          type="button"
          class="effort-chip-row"
          :class="{ 'effort-chip-row--selected': modelValue === level.value }"
          @click="select(level.value)"
        >
          <span class="effort-chip-row__label">{{ level.name }}</span>
          <v-icon
            v-if="modelValue === level.value"
            size="14"
            class="effort-chip-row__check"
          >
            mdi-check
          </v-icon>
        </button>
        <div class="effort-chip-divider"></div>
        <button
          type="button"
          class="effort-chip-row effort-chip-row--edit"
          @click="openEditor"
        >
          <v-icon size="14" class="effort-chip-row__gear"
            >mdi-cog-outline</v-icon
          >
          <span class="effort-chip-row__label">
            {{ tm("input.editThinkingEffortLevels") }}
          </span>
        </button>
      </v-card-text>
    </v-card>
  </v-menu>
</template>

<style scoped>
.effort-chip-btn {
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

.effort-chip-btn:hover {
  background: var(--sp-ghost-hover-bg, rgba(var(--v-theme-on-surface), 0.055));
  color: rgb(var(--v-theme-on-surface));
}

.effort-chip-btn--open {
  background: var(--sp-ghost-open-bg, rgba(var(--v-theme-on-surface), 0.07));
  color: rgb(var(--v-theme-on-surface));
}

.effort-chip-btn:active {
  background: var(--sp-chip-active-bg);
}

.effort-chip-btn:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 1px;
}

.effort-chip-btn__icon {
  opacity: 0.7;
}

/* Custom level names and long locales truncate instead of stretching
   the input row. */
.effort-chip-btn__label {
  max-width: 72px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.effort-chip-btn__chevron {
  opacity: 0.7;
}

.effort-chip-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--sp-text-primary);
  margin-bottom: 2px;
}

.effort-chip-row {
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

.effort-chip-row:hover {
  background: var(--sp-chip-hover-bg);
}

.effort-chip-row--selected {
  font-weight: 500;
}

.effort-chip-row__label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.effort-chip-row__check {
  flex-shrink: 0;
  color: rgb(var(--v-theme-primary));
}

.effort-chip-divider {
  height: 1px;
  margin: 4px 2px;
  background: var(--sp-chip-divider);
}

.effort-chip-row--edit {
  color: var(--sp-text-muted);
}

.effort-chip-row--edit:hover {
  color: var(--sp-text-primary);
}

.effort-chip-row__gear {
  flex-shrink: 0;
  opacity: 0.75;
}
</style>
