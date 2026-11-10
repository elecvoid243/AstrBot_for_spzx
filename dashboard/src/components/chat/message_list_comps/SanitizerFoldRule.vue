<template>
  <div
    class="sanitizer-fold-rule"
    role="note"
    :aria-label="tooltip"
    :title="tooltip"
  >
    <span class="sanitizer-fold-rule__label">
      <svg
        class="sanitizer-fold-rule__icon"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        stroke-width="1.4"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path d="M4 6.5 8 3l4 3.5" />
        <path d="M4 9.5 8 13l4-3.5" />
      </svg>
      <template v-if="kind === 'run'">
        <span>{{ tm("reasoning.sanitizerFolded") }}</span>
        <span class="sanitizer-fold-rule__count">{{ formatNumber(repeats) }}</span>
        <span>{{ tm("reasoning.sanitizerRunTimes") }}</span>
      </template>
      <template v-else>
        <span>{{ tm("reasoning.sanitizerFolded") }}</span>
        <span class="sanitizer-fold-rule__count">{{ formatNumber(repeats) }}</span>
        <span>{{ tm("reasoning.sanitizerTimes") }}</span>
        <span class="sanitizer-fold-rule__separator" aria-hidden="true">·</span>
        <span class="sanitizer-fold-rule__count">{{ formatNumber(period) }}</span>
        <span>{{ tm("reasoning.sanitizerLinesPer") }}</span>
      </template>
    </span>
    <span class="sanitizer-fold-rule__line" aria-hidden="true"></span>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useModuleI18n } from "@/i18n/composables";

const props = defineProps<{
  kind: "period" | "run";
  repeats: number;
  period: number;
}>();

const { tm } = useModuleI18n("features/chat");

function formatNumber(value: number): string {
  return value.toLocaleString("en-US");
}

const tooltip = computed(() => {
  const template =
    props.kind === "run"
      ? tm("reasoning.sanitizerRunTooltip")
      : tm("reasoning.sanitizerFoldTooltip");
  return template
    .replace("{repeats}", formatNumber(props.repeats))
    .replace("{period}", formatNumber(props.period));
});
</script>

<style scoped>
.sanitizer-fold-rule {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 16px 0 14px;
}

.sanitizer-fold-rule__label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
  font-size: 0.78em;
  line-height: 1;
  color: rgba(var(--v-theme-on-surface), 0.55);
  white-space: nowrap;
  cursor: default;
  transition: color 0.15s ease;
}

.sanitizer-fold-rule__label:hover {
  color: rgba(var(--v-theme-on-surface), 0.78);
}

.sanitizer-fold-rule__icon {
  width: 12px;
  height: 12px;
  opacity: 0.55;
  transition: opacity 0.15s ease;
}

.sanitizer-fold-rule__label:hover .sanitizer-fold-rule__icon {
  opacity: 0.85;
}

.sanitizer-fold-rule__count {
  font-family:
    ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.95em;
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.sanitizer-fold-rule__separator {
  opacity: 0.5;
}

.sanitizer-fold-rule__line {
  flex: 1;
  min-width: 20px;
  height: 1px;
  background: rgba(var(--v-theme-on-surface), 0.12);
}
</style>
