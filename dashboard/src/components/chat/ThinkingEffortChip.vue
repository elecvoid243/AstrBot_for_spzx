<!--
  ThinkingEffortChip — compact dropdown for the per-message thinking
  effort (reasoning intensity) override, living in the input row next
  to the token-usage ring. Moved out of the "+" menu (2026-09-01): the
  effort level is a send-time companion of the context budget, so the
  two controls sit together.

  Two shapes share the popup (2026-10-03): the fixed "levels" list, and a
  "slider" track for models whose reasoning_effort is a free number
  (DeepSeek-V4.1-Flash: 1-100 with low/high/xhigh/max as alias values).
  The slider is deliberately free-form — no snapping, so the handle never
  fights the pointer — while the aliases stay as labels and shortcuts.

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
  effortValueLabel,
  findEffortSnap,
  normalizeEffortValue,
  type ThinkingEffortSliderConfig,
} from "@/composables/thinkingEffortSlider";
import type { ThinkingEffortLevel } from "@/composables/thinkingEffortPresets";

const props = defineProps<{
  modelValue: ThinkingEffort;
  levels: ThinkingEffortLevel[];
  /** "levels" keeps the fixed alias list; "slider" opens the numeric track. */
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

const sliderMode = computed(() => props.mode === "slider");

/** Track position for the current selection; non-numeric legacy values land on min. */
const sliderValue = computed<number>(() => {
  const parsed = Number(props.modelValue);
  return Number.isFinite(parsed)
    ? normalizeEffortValue(parsed, sliderConfig.value)
    : sliderConfig.value.min;
});

const activeLabel = computed<string>(() => {
  if (sliderMode.value) {
    return effortValueLabel(sliderValue.value, sliderConfig.value);
  }
  return (
    props.levels.find((level) => level.value === props.modelValue)?.name ??
    props.modelValue
  );
});

const activeSnapName = computed<string>(
  () => findEffortSnap(sliderValue.value, sliderConfig.value)?.name ?? "",
);

/** Percent along the track for the current value (thumb / fill reveal). */
const sliderPercent = computed<number>(() => {
  const { min, max } = sliderConfig.value;
  return ((sliderValue.value - min) / (max - min)) * 100;
});

const sliderPercentText = computed<string>(() => `${sliderPercent.value}%`);

/** Reveal the anchored gradient left of the thumb: clip from the right. */
const fillClipStyle = computed<string>(
  () => `inset(0 ${100 - sliderPercent.value}% 0 0)`,
);

function snapLeftText(value: number): string {
  const { min, max } = sliderConfig.value;
  return `${((value - min) / (max - min)) * 100}%`;
}

function select(value: ThinkingEffort): void {
  menuOpen.value = false;
  if (value === props.modelValue) return;
  emit("update:modelValue", value);
}

/** Live drag handler: the handle follows the pointer, nothing else. */
function onSliderInput(value: number | number[]): void {
  const raw = Array.isArray(value) ? value[0] : value;
  const next = String(normalizeEffortValue(Number(raw), sliderConfig.value));
  if (next === String(props.modelValue)) return;
  emit("update:modelValue", next);
}

/** Alias pill: jump onto the snap point without closing the popup. */
function selectSnap(value: number): void {
  const next = String(value);
  if (next === String(props.modelValue)) return;
  emit("update:modelValue", next);
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
            :aria-label="`${tm('input.thinkingEffort')}: ${activeLabel}`"
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
    <v-card
      class="effort-chip-card"
      :class="{ 'effort-chip-card--slider': sliderMode }"
    >
      <v-card-text>
        <div class="effort-chip-title">{{ tm("input.thinkingEffort") }}</div>
        <select v-if="presets && presets.length > 1" class="effort-chip-preset-select"
          :value="activePreset" :aria-label="tm('input.presetLabel')"
          @change="emit('selectPreset', ($event.target as HTMLSelectElement).value)">
          <option v-for="preset in presets" :key="preset.id" :value="preset.id">{{ preset.name }}</option>
        </select>
        <div v-else-if="presetName" class="effort-chip-preset">{{ presetName }}</div>

        <!-- Slider mode: free numeric track (1-100 by default). The named
             aliases are labels + click-to-jump shortcuts, never magnetism —
             pulling the handle under the pointer made it jitter. The native
             range stays invisible on top as the interaction layer (drag +
             keyboard), while the gradient track / thumb / snap columns are
             our own themed rendering underneath. -->
        <template v-if="sliderMode">
          <div class="effort-slider-readout">
            <span class="effort-slider-readout__main">
              {{ sliderValue }}
              <span
                v-if="activeSnapName"
                class="effort-slider-readout__alias"
              >{{ activeSnapName }}</span>
            </span>
            <span class="effort-slider-readout__range">
              {{ sliderConfig.min }}–{{ sliderConfig.max }}
            </span>
          </div>
          <div class="effort-slider-zone">
            <div class="effort-slider-hitzone">
              <div class="effort-slider-track">
                <div class="effort-slider-track__base"></div>
                <div
                  class="effort-slider-track__fill"
                  :style="{ clipPath: fillClipStyle }"
                ></div>
              </div>
              <div
                class="effort-slider-thumb"
                :style="{ left: sliderPercentText }"
              ></div>
              <input
                type="range"
                class="effort-slider-input"
                :min="sliderConfig.min"
                :max="sliderConfig.max"
                :step="sliderConfig.step"
                :value="sliderValue"
                :aria-label="tm('input.thinkingEffort')"
                @input="onSliderInput(Number(($event.target as HTMLInputElement).value))"
              />
            </div>
            <div
              v-if="sliderConfig.snaps.length"
              class="effort-slider-snaps"
            >
              <button
                v-for="snap in sliderConfig.snaps"
                :key="snap.value"
                type="button"
                class="effort-snap"
                :class="{
                  'effort-snap--active': snap.value === sliderValue,
                  'effort-snap--edge-end': snap.value === sliderConfig.max,
                  'effort-snap--edge-start': snap.value === sliderConfig.min,
                }"
                :style="{ left: snapLeftText(snap.value) }"
                :data-test="`effort-snap-${snap.name}`"
                @click="selectSnap(snap.value)"
              >
                <span class="effort-snap__name">{{ snap.name }}</span>
                <span class="effort-snap__value">{{ snap.value }}</span>
              </button>
            </div>
          </div>
        </template>

        <template v-else>
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
        </template>

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
.effort-chip-preset-select {
  width: 100%;
  padding: 6px 8px;
  margin-bottom: 8px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.15);
  border-radius: 6px;
  color: rgb(var(--v-theme-on-surface));
  background: rgb(var(--v-theme-surface));
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

/* Fixed width, not max-width: the popup is anchored to this button, so a
   label that grows mid-drag ("20" → "low 25") used to widen the chip and
   shift the whole slider sideways under the pointer. */
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

.effort-chip-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--sp-text-primary);
  margin-bottom: 2px;
}

/* Active preset name, read-only context under the title: the row itself
   renders whichever shape that preset defines. */
.effort-chip-preset {
  margin-bottom: 6px;
  color: var(--sp-text-muted);
  font-size: 12px;
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

/* Level mode sizes itself to its content: a label plus a check needs no wide
   floor, and the shared 264px used to leave a mostly empty right edge. The
   floor below only keeps a very short list from collapsing into a sliver. */
.effort-chip-card {
  min-width: 160px;
}

/* Slider mode keeps the wide floor: the track needs horizontal room to drag,
   and the card must not swallow the drag as a "click outside". */
.effort-chip-card--slider {
  min-width: 264px;
}

.effort-slider-readout {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin: 2px 0 10px;
}

.effort-slider-readout__main {
  color: rgb(var(--v-theme-primary));
  font-size: 20px;
  font-weight: 500;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.effort-slider-readout__alias {
  margin-left: 6px;
  font-size: 12px;
}

.effort-slider-readout__range {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

/* Track zone: the gradient stays anchored to the FULL track width (the fill
   is a clip-path reveal of the same layer), so the deep-blue end always sits
   at the right edge no matter where the handle is. The invisible native
   range on top owns dragging and keyboard input. */
.effort-slider-zone {
  min-width: 0;
}

.effort-slider-hitzone {
  position: relative;
  height: 22px;
  cursor: pointer;
}

.effort-slider-track {
  position: absolute;
  left: 0;
  right: 0;
  top: 50%;
  height: 8px;
  border-radius: 999px;
  overflow: hidden;
  transform: translateY(-50%);
  background: var(--sp-chip-divider, rgba(var(--v-theme-on-surface), 0.12));
}

.effort-slider-track__base,
.effort-slider-track__fill {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    to right,
    rgba(var(--v-theme-primary200), 0.55),
    rgb(var(--v-theme-primary)) 55%,
    rgb(var(--v-theme-darkprimary))
  );
}

.effort-slider-track__base {
  opacity: 0.22;
}

.effort-slider-thumb {
  position: absolute;
  top: 50%;
  width: 16px;
  height: 16px;
  border-radius: 999px;
  background: rgb(var(--v-theme-surface));
  border: 2px solid rgb(var(--v-theme-primary));
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.22);
  transform: translate(-50%, -50%);
  pointer-events: none;
  box-sizing: border-box;
  transition: box-shadow 0.15s ease;
}

.effort-slider-input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  margin: 0;
  opacity: 0;
  cursor: pointer;
  -webkit-appearance: none;
  appearance: none;
}

.effort-slider-hitzone:has(.effort-slider-input:focus-visible)
  .effort-slider-thumb {
  box-shadow:
    0 1px 4px rgba(0, 0, 0, 0.22),
    0 0 0 3px rgba(var(--v-theme-primary), 0.35);
}

/* Snap columns under the track, two rows (alias over number), one clickable
   column per alias. Edge columns align their outer edge with the track end
   instead of centering, so "max 100" never overflows the card. */
.effort-slider-snaps {
  position: relative;
  height: 30px;
  margin-top: 2px;
}

.effort-snap {
  position: absolute;
  top: 0;
  transform: translateX(-50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  padding: 2px 5px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
}

.effort-snap:hover {
  background: var(--sp-chip-hover-bg, rgba(var(--v-theme-on-surface), 0.06));
}

.effort-snap--edge-start {
  transform: translateX(0);
  padding-left: 0;
}

.effort-snap--edge-end {
  transform: translateX(-100%);
  padding-right: 0;
}

.effort-snap__name {
  font-size: 11px;
  line-height: 1.4;
  color: rgba(var(--v-theme-on-surface), 0.55);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.effort-snap__value {
  font-size: 11px;
  line-height: 1.4;
  color: rgba(var(--v-theme-on-surface), 0.4);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.effort-snap--active .effort-snap__name,
.effort-snap--active .effort-snap__value {
  color: rgb(var(--v-theme-primary));
  font-weight: 500;
}
</style>
