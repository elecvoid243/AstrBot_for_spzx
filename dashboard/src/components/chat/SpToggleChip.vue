<script setup lang="ts">
// Pill-shaped toggle chip (2026-09-09).
//
// A <label> wrapping a visually hidden native checkbox, so v-model,
// keyboard focus and assistive tech keep native checkbox semantics
// while the visual is a rounded chip that can carry an icon.
//
// Shared by ProjectLoadDialog (project load steps) and ProjectDialog
// (spcode load steps) so both dialogs stay visually identical by
// construction instead of duplicating the chip CSS.
//
// Author: elecvoid243

defineProps<{
  /** Whether the chip is currently selected. */
  modelValue: boolean;
  /** MDI icon name rendered before the label. */
  icon: string;
  /** Chip label text. */
  label: string;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
}>();
</script>

<template>
  <label class="sp-toggle-chip" :class="{ 'sp-toggle-chip--on': modelValue }">
    <input
      type="checkbox"
      class="sp-toggle-chip__input"
      :checked="modelValue"
      @change="
        emit('update:modelValue', ($event.target as HTMLInputElement).checked)
      "
    />
    <v-icon :icon="icon" size="16" />
    <span>{{ label }}</span>
    <v-icon icon="mdi-check" size="14" class="sp-toggle-chip__tick" />
  </label>
</template>

<style scoped>
.sp-toggle-chip {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.24);
  border-radius: 999px;
  color: rgb(var(--v-theme-on-surface));
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  user-select: none;
  transition:
    background-color 0.15s ease,
    border-color 0.15s ease,
    color 0.15s ease;
}

.sp-toggle-chip:hover {
  border-color: rgba(var(--v-theme-on-surface), 0.45);
}

.sp-toggle-chip--on {
  background-color: rgba(var(--v-theme-primary), 0.1);
  border-color: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-primary));
  font-weight: 500;
}

.sp-toggle-chip:focus-within {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}

.sp-toggle-chip__input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

/* Reserved space so toggling does not reflow the chip. */
.sp-toggle-chip__tick {
  opacity: 0;
  transition: opacity 0.15s ease;
}

.sp-toggle-chip--on .sp-toggle-chip__tick {
  opacity: 1;
}
</style>
