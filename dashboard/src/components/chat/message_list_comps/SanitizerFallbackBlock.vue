<template>
  <div
    class="sanitizer-fallback-block"
    :class="{ 'is-interactive': interactive, 'is-active': active }"
    :role="interactive ? 'button' : 'note'"
    :tabindex="interactive ? 0 : undefined"
    :aria-pressed="interactive ? active : undefined"
    :title="
      interactive
        ? active
          ? tm('reasoning.showSanitized')
          : tm('reasoning.showOriginal')
        : tm('reasoning.sanitizerFallbackTitle')
    "
    @click="interactive && emit('toggle')"
    @keydown.enter.prevent="interactive && emit('toggle')"
    @keydown.space.prevent="interactive && emit('toggle')"
  >
    <svg
      class="sanitizer-fallback-block__icon"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      stroke-width="1.3"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <path d="M3 5.5 8 3l5 2.5-5 2.5-5-2.5Z" />
      <path d="m3 8.5 5 2.5 5-2.5" />
      <path d="m3 11.5 5 2.5 5-2.5" />
    </svg>
    <div class="sanitizer-fallback-block__body">
      <div class="sanitizer-fallback-block__eyebrow">
        {{ tm("reasoning.sanitizerFallbackEyebrow") }}
      </div>
      <div class="sanitizer-fallback-block__title">
        {{ tm("reasoning.sanitizerFallbackTitle") }}
      </div>
      <div class="sanitizer-fallback-block__meta">
        {{ formatNumber(lines) }} {{ tm("reasoning.sanitizerLines") }}
        <span aria-hidden="true">·</span>
        {{ formatNumber(chars) }} {{ tm("reasoning.sanitizerChars") }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useModuleI18n } from "@/i18n/composables";

defineProps<{
  lines: number;
  chars: number;
  interactive?: boolean;
  active?: boolean;
}>();

const emit = defineEmits<{ toggle: [] }>();
const { tm } = useModuleI18n("features/chat");

function formatNumber(value: number): string {
  return value.toLocaleString("en-US");
}
</script>

<style scoped>
.sanitizer-fallback-block {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin: 14px 0;
  padding: 10px 12px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-left: 2px solid rgba(var(--v-theme-on-surface), 0.2);
  border-radius: 8px;
  background: rgba(var(--v-theme-on-surface), 0.035);
}

.sanitizer-fallback-block.is-interactive {
  cursor: pointer;
}

.sanitizer-fallback-block.is-interactive:hover {
  border-color: rgba(var(--v-theme-on-surface), 0.2);
  background: rgba(var(--v-theme-on-surface), 0.055);
}

.sanitizer-fallback-block.is-active {
  border-left-color: rgba(var(--v-theme-on-surface), 0.35);
}

.sanitizer-fallback-block__icon {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  margin-top: 1px;
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.sanitizer-fallback-block__eyebrow {
  margin-bottom: 2px;
  font-size: 0.66em;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-surface), 0.45);
}

.sanitizer-fallback-block__title {
  font-size: 0.86em;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.82);
}

.sanitizer-fallback-block__meta {
  margin-top: 2px;
  font-family:
    ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.72em;
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), 0.55);
}
</style>
