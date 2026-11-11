<!--
  ThinkingEffortTrack — the single visual shape shared by level presets and
  continuous numeric presets.

  Levels are enum-like strings, so the track positions them by array index;
  dragging can only land on an integer stop. Slider presets keep a free
  numeric track, with named snaps acting as clickable shortcuts only.

  The native range input stays invisible on top as the interaction layer
  (pointer drag, touch, keyboard); the visible track, dots/ticks, thumb and
  labels are themed renderings underneath.
-->
<script setup lang="ts">
import { computed, ref } from "vue";
import type { ThinkingEffortLevel } from "@/composables/thinkingEffortPresets";
import {
  DEFAULT_THINKING_EFFORT_SLIDER,
  type ThinkingEffortSliderConfig,
} from "@/composables/thinkingEffortSlider";
import {
  buildThinkingEffortTrack,
  type ThinkingEffortTrackMode,
} from "@/composables/thinkingEffortTrack";

const props = withDefaults(
  defineProps<{
    modelValue: string;
    levels: ThinkingEffortLevel[];
    mode?: ThinkingEffortTrackMode;
    slider?: ThinkingEffortSliderConfig;
    ariaLabel?: string;
    /** Tighter labels for the preset editor preview. */
    compact?: boolean;
  }>(),
  {
    mode: "levels",
    slider: () => DEFAULT_THINKING_EFFORT_SLIDER,
    ariaLabel: "",
    compact: false,
  },
);

const emit = defineEmits<{
  (e: "update:modelValue", value: string): void;
}>();

const dragging = ref(false);

const view = computed(() =>
  buildThinkingEffortTrack({
    mode: props.mode,
    value: props.modelValue,
    levels: props.levels,
    slider: props.slider,
  }),
);

function emitValue(value: string): void {
  if (value === props.modelValue) return;
  emit("update:modelValue", value);
}

function onInput(event: Event): void {
  const raw = Number((event.target as HTMLInputElement).value);
  if (view.value.mode === "levels") {
    const index = Math.min(
      Math.max(0, Math.round(raw)),
      Math.max(0, props.levels.length - 1),
    );
    const level = props.levels[index];
    if (level) emitValue(level.value);
    return;
  }
  emitValue(String(raw));
}

function selectStop(value: string): void {
  emitValue(value);
}
</script>

<template>
  <div
    class="effort-track"
    :class="{ 'effort-track--compact': compact }"
  >
    <div class="effort-track__hitzone">
      <div
        class="effort-track__line"
        :class="{ 'effort-track__line--dragging': dragging }"
      >
        <div
          class="effort-track__fill"
          :style="{ clipPath: `inset(0 ${100 - view.percent}% 0 0)` }"
        ></div>
      </div>
      <div class="effort-track__stops" aria-hidden="true">
        <span
          v-for="stop in view.stops"
          :key="stop.key"
          class="effort-track__stop"
          :class="[
            view.mode === 'levels'
              ? 'effort-track__stop--dot'
              : 'effort-track__stop--tick',
            { 'effort-track__stop--active': stop.active },
          ]"
          :style="{ left: `${stop.percent}%` }"
        ></span>
      </div>
      <div
        class="effort-track__thumb"
        :class="{ 'effort-track__thumb--dragging': dragging }"
        :style="{ left: `${view.percent}%` }"
      ></div>
      <input
        class="effort-track__input"
        type="range"
        :min="view.min"
        :max="view.max"
        :step="view.step"
        :value="view.value"
        :disabled="view.disabled"
        :aria-label="ariaLabel || undefined"
        :aria-valuetext="view.ariaValueText"
        @input="onInput"
        @pointerdown="dragging = true"
        @pointerup="dragging = false"
        @pointercancel="dragging = false"
        @change="dragging = false"
        @blur="dragging = false"
      />
    </div>
    <div
      class="effort-track__labels"
      :class="{ 'effort-track__labels--sub': view.mode === 'slider' }"
    >
      <button
        v-for="(stop, index) in view.stops"
        :key="stop.key"
        type="button"
        class="effort-track__label"
        :class="{
          'effort-track__label--active': stop.active,
          'effort-track__label--edge-start': index === 0,
          'effort-track__label--edge-end': index === view.stops.length - 1,
        }"
        :style="{ left: `${stop.percent}%` }"
        :title="
          stop.sublabel === undefined
            ? stop.label
            : `${stop.label} · ${stop.sublabel}`
        "
        @click="selectStop(stop.value)"
      >
        <span class="effort-track__label-name">{{ stop.label }}</span>
        <span
          v-if="stop.sublabel !== undefined"
          class="effort-track__label-sub"
        >{{ stop.sublabel }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.effort-track {
  width: 100%;
  min-width: 0;
}

.effort-track__hitzone {
  position: relative;
  height: 26px;
  cursor: pointer;
  touch-action: none;
}

.effort-track__line {
  position: absolute;
  left: 0;
  right: 0;
  top: 50%;
  height: 12px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(var(--v-theme-on-surface), 0.12);
  transform: translateY(-50%);
}

.effort-track__fill {
  width: 100%;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(
    90deg,
    rgba(var(--v-theme-primary), 0.4) 0%,
    rgb(var(--v-theme-primary)) 50%,
    rgb(var(--v-theme-darkprimary))
  );
  transition: clip-path 160ms cubic-bezier(0.2, 0.8, 0.2, 1);
}

.effort-track__line--dragging .effort-track__fill,
.effort-track__thumb--dragging {
  transition: none;
}

.effort-track__stops {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.effort-track__stop {
  position: absolute;
  top: 50%;
  transform: translate(-50%, -50%);
  transition:
    background-color 150ms ease,
    border-color 150ms ease,
    opacity 150ms ease;
}

.effort-track__stop--dot {
  width: 6px;
  height: 6px;
  border: 1.5px solid rgba(var(--v-theme-on-surface), 0.22);
  border-radius: 50%;
  background: rgb(var(--v-theme-surface));
}

.effort-track__stop--dot.effort-track__stop--active {
  opacity: 0;
}

.effort-track__stop--tick {
  width: 2px;
  height: 8px;
  border-radius: 1px;
  background: rgba(var(--v-theme-on-surface), 0.2);
}

.effort-track__stop--tick.effort-track__stop--active {
  background: rgb(var(--v-theme-primary));
}

.effort-track__thumb {
  position: absolute;
  top: 50%;
  left: 0;
  z-index: 3;
  width: 16px;
  height: 16px;
  box-sizing: border-box;
  border: 2px solid rgb(var(--v-theme-primary));
  border-radius: 50%;
  background: rgb(var(--v-theme-surface));
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.2);
  transform: translate(-50%, -50%);
  pointer-events: none;
  transition:
    left 160ms cubic-bezier(0.2, 0.8, 0.2, 1),
    box-shadow 150ms ease,
    transform 150ms ease;
}

.effort-track__hitzone:active .effort-track__thumb {
  transform: translate(-50%, -50%) scale(1.06);
}

.effort-track__hitzone:focus-within .effort-track__thumb {
  box-shadow:
    0 0 0 4px rgba(var(--v-theme-primary), 0.28),
    0 1px 4px rgba(0, 0, 0, 0.2);
}

.effort-track__input {
  position: absolute;
  left: -8px;
  right: -8px;
  top: 0;
  z-index: 4;
  width: calc(100% + 16px);
  height: 100%;
  margin: 0;
  opacity: 0;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
}

.effort-track__input:disabled {
  cursor: default;
}

.effort-track__input::-webkit-slider-thumb {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  appearance: none;
  -webkit-appearance: none;
}

.effort-track__input::-moz-range-thumb {
  width: 16px;
  height: 16px;
  border: 0;
  border-radius: 50%;
}

.effort-track__labels {
  position: relative;
  height: 25px;
  margin-top: 1px;
}

.effort-track__labels--sub {
  height: 37px;
}

.effort-track__label {
  position: absolute;
  top: 0;
  left: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  max-width: 54px;
  padding: 3px 5px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--sp-text-muted);
  font-size: 10.5px;
  line-height: 1.35;
  white-space: nowrap;
  cursor: pointer;
  transform: translateX(-50%);
  transition:
    background-color 150ms ease,
    color 150ms ease;
}

.effort-track__label:hover {
  background: var(--sp-chip-hover-bg);
  color: var(--sp-text-primary);
}

.effort-track__label--active {
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
}

.effort-track__label-name {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
}

.effort-track__label-sub {
  color: var(--sp-text-muted);
  font-size: 9.5px;
  font-variant-numeric: tabular-nums;
  font-weight: 400;
}

.effort-track__label--active .effort-track__label-sub {
  color: rgb(var(--v-theme-primary));
  opacity: 0.75;
}

.effort-track__label--edge-start {
  align-items: flex-start;
  transform: translateX(0);
}

.effort-track__label--edge-end {
  align-items: flex-end;
  transform: translateX(-100%);
}

.effort-track__label:only-child {
  align-items: center;
  transform: translateX(-50%);
}

.effort-track--compact .effort-track__label {
  max-width: 48px;
  font-size: 10px;
}

.effort-track--compact .effort-track__label-sub {
  font-size: 9px;
}

.effort-track--compact .effort-track__labels {
  height: 23px;
}

.effort-track--compact .effort-track__labels--sub {
  height: 34px;
}
</style>
