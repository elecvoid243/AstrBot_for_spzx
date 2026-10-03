<!--
  Author: elecvoid243, 2026-07-09
  Spec: docs/superpowers/specs/2026-07-09-chat-input-chips-beautify-design.md §5.5

  GitDiffChip — the status row's workspace entry point.

  2026-10-03 (elecvoid243): re-promoted from the FileAccessModeChip dropdown
  back to a standalone capsule next to it, so the mode menu stays purely
  about permissions. Visual spec now matches .fa-chip-btn (28px capsule,
  1px --sp-chip-border, primary tint while the sidebar is open) — one
  capsule family for the whole right cluster.

  Event contract:
    - Emits `toggle-diff-sidebar` on click; the parent owns the open state
      (it flips it), so a second click closes the sidebar again.
-->
<script setup lang="ts">
import { useModuleI18n } from "@/i18n/composables";

const { tm } = useModuleI18n("features/chat");

withDefaults(
  defineProps<{
    /** Sidebar is open: tint the capsule instead of swapping the icon. */
    active?: boolean;
  }>(),
  { active: false },
);

const emit = defineEmits<{
  (e: "toggle-diff-sidebar"): void;
}>();

function toggle(): void {
  emit("toggle-diff-sidebar");
}
</script>

<template>
  <v-tooltip location="bottom" :open-delay="200">
    <template #activator="{ props: tipProps }">
      <button
        v-bind="tipProps"
        type="button"
        class="sp-ghost-btn"
        :class="{ 'sp-ghost-btn--active': active }"
        :aria-pressed="active"
        :aria-label="tm('spcodeProjectLoad.diffSidebar.chipTooltip')"
        @click="toggle"
      >
        <v-icon size="14">mdi-folder-open-outline</v-icon>
        <!-- Icon-only below md: the right cluster already carries the
             file-access capsule, and the tooltip keeps the meaning. -->
        <span class="sp-ghost-btn__label">{{ tm("spcodeProjectLoad.diffSidebar.chip") }}</span>
      </button>
    </template>
    <span>{{ tm("spcodeProjectLoad.diffSidebar.chipTooltip") }}</span>
  </v-tooltip>
</template>

<style scoped>
.sp-ghost-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  box-sizing: border-box;
  height: 28px;
  padding: 0 9px;
  border: 1px solid var(--sp-chip-border);
  border-radius: 8px;
  background: transparent;
  color: var(--sp-text-muted);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition:
    background-color 150ms ease,
    border-color 150ms ease,
    color 150ms ease;
}

.sp-ghost-btn:hover {
  background: var(--sp-ghost-hover-bg, rgba(var(--v-theme-on-surface), 0.055));
  border-color: var(--sp-chip-border-strong);
  color: var(--sp-text-primary);
}

.sp-ghost-btn--active {
  background: rgba(var(--v-theme-primary), 0.1);
  border-color: rgba(var(--v-theme-primary), 0.35);
  color: rgb(var(--v-theme-primary));
}

.sp-ghost-btn:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 1px;
}

/* Matches the composer's own 768px breakpoint (see .input-container). */
@media (max-width: 768px) {
  .sp-ghost-btn__label {
    display: none;
  }
}
</style>
