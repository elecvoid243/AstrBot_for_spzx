<!--
  ThinkingEffortChip — compact dropdown for the per-message thinking
  effort (reasoning intensity) override, living in the input row next
  to the token-usage ring. Moved out of the "+" menu (2026-09-01): the
  effort level is a send-time companion of the context budget, so the
  two controls sit together.

  Both preset shapes share one visual track: levels snap between their
  ordered stops, while continuous presets move freely and use named markers
  only as clickable shortcuts. The popup never swaps between a list and a
  slider, so the chip's anchor and the readout stay put while dragging.

  The chip is a pure UI surface: administrators own the shared preset list,
  while each account owns its selected value. ChatInput forwards them via
  v-model/:levels/:mode/:slider/presetName and opens the editor dialog when
  this chip emits "edit". Menu/row styling follows the FileAccessModeChip
  pattern (same --sp-* chip tokens).
-->
<script setup lang="ts">
import { computed, ref } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import type { ThinkingEffort } from "@/composables/useMessages";
import {
  DEFAULT_THINKING_EFFORT_SLIDER,
  type ThinkingEffortSliderConfig,
} from "@/composables/thinkingEffortSlider";
import type { ThinkingEffortLevel } from "@/composables/thinkingEffortPresets";
import { buildThinkingEffortTrack } from "@/composables/thinkingEffortTrack";
import ThinkingEffortTrack from "./ThinkingEffortTrack.vue";

const props = defineProps<{
  modelValue: ThinkingEffort;
  levels: ThinkingEffortLevel[];
  /** "levels" snaps to ordered stops; "slider" moves freely. */
  mode?: "levels" | "slider";
  slider?: ThinkingEffortSliderConfig;
  /** Name of the preset being rendered; empty when none is configured. */
  presetName?: string;
  presets?: Array<{ id: string; name: string }>;
  activePreset?: string;
  /** Config editors only — without it the edit row stays hidden. */
  canEdit?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", value: ThinkingEffort): void;
  (e: "edit"): void;
  (e: "selectPreset", value: string): void;
}>();

const { tm } = useModuleI18n("features/chat");

const menuOpen = ref(false);

const sliderConfig = computed<ThinkingEffortSliderConfig>(
  () => props.slider ?? DEFAULT_THINKING_EFFORT_SLIDER,
);

const trackView = computed(() =>
  buildThinkingEffortTrack({
    mode: props.mode ?? "levels",
    value: props.modelValue,
    levels: props.levels,
    slider: sliderConfig.value,
  }),
);

const activeLabel = computed<string>(() => trackView.value.activeLabel);

/** Track interaction updates the value without closing the popup. */
function onTrackValue(value: string): void {
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
    :close-on-content-click="false"
  >
    <template #activator="{ props: menuProps }">
      <v-tooltip location="top" :open-delay="200">
        <template #activator="{ props: tipProps }">
          <button
            v-bind="{ ...tipProps, ...menuProps }"
            type="button"
            class="effort-chip-btn"
            :class="{ 'effort-chip-btn--open': menuOpen }"
            :aria-label="`${tm('input.thinkingEffort')}: ${trackView.ariaValueText}`"
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
    <v-card class="effort-chip-card">
      <v-card-text>
        <div class="effort-chip-head">
          <div class="effort-chip-title">{{ tm("input.thinkingEffort") }}</div>
          <select
            v-if="presets && presets.length > 1"
            class="effort-chip-preset-select"
            :value="activePreset"
            :aria-label="tm('input.presetLabel')"
            @change="emit('selectPreset', ($event.target as HTMLSelectElement).value)"
          >
            <option
              v-for="preset in presets"
              :key="preset.id"
              :value="preset.id"
            >{{ preset.name }}</option>
          </select>
          <div v-else-if="presetName" class="effort-chip-preset">{{ presetName }}</div>
        </div>

        <div class="effort-chip-readout">
          <div class="effort-chip-readout__main">
            <span class="effort-chip-readout__value">{{ trackView.activeLabel }}</span>
            <span
              v-if="trackView.activeSubLabel"
              class="effort-chip-readout__alias"
            >{{ trackView.activeSubLabel }}</span>
          </div>
          <span class="effort-chip-readout__meta">{{ trackView.positionLabel }}</span>
        </div>

        <div class="effort-chip-track">
          <ThinkingEffortTrack
            :model-value="modelValue"
            :levels="levels"
            :mode="mode"
            :slider="sliderConfig"
            :aria-label="tm('input.thinkingEffort')"
            @update:model-value="onTrackValue"
          />
        </div>

        <template v-if="canEdit">
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
              {{ tm("input.editThinkingEffort") }}
            </span>
          </button>
        </template>
      </v-card-text>
    </v-card>
  </v-menu>
</template>

<style scoped>
.effort-chip-card {
  width: 308px;
  max-width: calc(100vw - 24px);
}

.effort-chip-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.effort-chip-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--sp-text-primary);
  white-space: nowrap;
}

.effort-chip-preset-select {
  width: auto;
  max-width: 158px;
  padding: 3px 4px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--sp-text-muted);
  font-size: 11.5px;
  text-overflow: ellipsis;
  cursor: pointer;
  transition:
    background-color 150ms ease,
    color 150ms ease;
}

.effort-chip-preset-select:hover {
  background: var(--sp-chip-hover-bg);
  color: var(--sp-text-primary);
}

.effort-chip-preset-select:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 1px;
}

.effort-chip-preset {
  overflow: hidden;
  color: var(--sp-text-muted);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.effort-chip-readout {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  margin: 14px 0 0;
}

.effort-chip-readout__main {
  display: flex;
  align-items: baseline;
  gap: 7px;
  min-width: 0;
}

.effort-chip-readout__value {
  overflow: hidden;
  color: var(--sp-text-primary);
  font-size: 22px;
  font-weight: 600;
  line-height: 1.1;
  letter-spacing: -0.02em;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.effort-chip-readout__alias {
  color: rgb(var(--v-theme-primary));
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
}

.effort-chip-readout__meta {
  flex: 0 0 auto;
  padding-bottom: 4px;
  color: var(--sp-text-muted);
  font-size: 10.5px;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.effort-chip-track {
  margin-top: 12px;
}

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

/* Fixed width: the popup is anchored to this button, so a label that grows
   mid-drag used to widen the chip and shift the whole track sideways. */
.effort-chip-btn__label {
  width: 64px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.effort-chip-btn__chevron {
  opacity: 0.7;
}

.effort-chip-row {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 7px 6px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--sp-text-primary);
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
}

.effort-chip-row:hover {
  background: var(--sp-chip-hover-bg);
}

.effort-chip-row__label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.effort-chip-divider {
  height: 1px;
  margin: 10px -2px 3px;
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
