/**
 * Shared geometry for the thinking-effort track.
 *
 * Level presets are enum-like: their values are free-form strings (`none`,
 * `low`, `max`) and only the array order carries meaning. The track therefore
 * positions levels by index, never by numeric value. Slider presets keep the
 * existing free numeric track, where named snaps are clickable shortcuts and
 * never magnetise the handle.
 */

import type { ThinkingEffortLevel } from "./thinkingEffortPresets";
import type { ThinkingEffortSliderConfig } from "./thinkingEffortSlider";

export type ThinkingEffortTrackMode = "levels" | "slider";

export interface ThinkingEffortTrackStop {
  key: string;
  label: string;
  sublabel?: string;
  value: string;
  percent: number;
  active: boolean;
}

export interface ThinkingEffortTrackView {
  mode: ThinkingEffortTrackMode;
  min: number;
  max: number;
  step: number;
  value: number;
  percent: number;
  stops: ThinkingEffortTrackStop[];
  disabled: boolean;
  ariaValueText: string;
  /** Chip / readout label: a level name or the numeric value. */
  activeLabel: string;
  /** Alias the slider sits exactly on, empty when it sits between snaps. */
  activeSubLabel: string;
  /** Position hint: "5 / 7" for levels, "1–100" for the numeric track. */
  positionLabel: string;
}

interface ThinkingEffortTrackInput {
  mode: ThinkingEffortTrackMode;
  value: string;
  levels: ThinkingEffortLevel[];
  slider: ThinkingEffortSliderConfig;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/**
 * Resolve a preset value into everything the track and its readout need.
 *
 * Args:
 *   input: Current mode, raw value, level list, and numeric track config.
 *
 * Returns:
 *   A render-ready view with native range bounds, stop positions, and labels.
 */
export function buildThinkingEffortTrack(
  input: ThinkingEffortTrackInput,
): ThinkingEffortTrackView {
  if (input.mode === "levels") {
    const levels = input.levels;
    if (!levels.length) {
      return {
        mode: "levels",
        min: 0,
        max: 1,
        step: 1,
        value: 0,
        percent: 0,
        stops: [],
        disabled: true,
        ariaValueText: input.value,
        activeLabel: input.value || "—",
        activeSubLabel: "",
        positionLabel: "0 / 0",
      };
    }

    const foundIndex = levels.findIndex((level) => level.value === input.value);
    const activeIndex = foundIndex >= 0 ? foundIndex : 0;
    const percent =
      levels.length === 1 ? 50 : (activeIndex / (levels.length - 1)) * 100;
    const activeLabel = levels[activeIndex]?.name || input.value || "—";

    return {
      mode: "levels",
      min: 0,
      max: Math.max(1, levels.length - 1),
      step: 1,
      value: activeIndex,
      percent,
      disabled: levels.length === 1,
      ariaValueText: activeLabel,
      activeLabel,
      activeSubLabel: "",
      positionLabel: `${activeIndex + 1} / ${levels.length}`,
      stops: levels.map((level, index) => ({
        key: `levels-${index}-${level.value}`,
        label: level.name || "—",
        value: level.value,
        percent:
          levels.length === 1 ? 50 : (index / (levels.length - 1)) * 100,
        active: index === activeIndex,
      })),
    };
  }

  const rawMin = Number(input.slider.min);
  const rawMax = Number(input.slider.max);
  const rawStep = Number(input.slider.step);
  const min = Number.isFinite(rawMin) ? rawMin : 0;
  const max = Number.isFinite(rawMax) && rawMax > min ? rawMax : min + 1;
  const step = Number.isFinite(rawStep) && rawStep > 0 ? rawStep : 1;

  let numeric = Number(input.value);
  if (!Number.isFinite(numeric)) {
    const alias = input.slider.snaps.find(
      (snap) =>
        snap.name.trim().toLowerCase() === input.value.trim().toLowerCase(),
    );
    numeric = alias ? alias.value : min;
  }
  if (!Number.isFinite(numeric)) numeric = min;
  const value = clamp(numeric, min, max);
  const percent = ((value - min) / (max - min)) * 100;
  const activeSnap = input.slider.snaps.find(
    (snap) => Number(snap.value) === value,
  );

  const stops = input.slider.snaps
    .map((snap, index) => ({ snap, index, numeric: Number(snap.value) }))
    .filter(
      (entry) =>
        Number.isFinite(entry.numeric) &&
        entry.numeric >= min &&
        entry.numeric <= max,
    )
    .sort((left, right) => left.numeric - right.numeric)
    .map((entry) => ({
      key: `slider-${entry.index}-${entry.numeric}`,
      label: entry.snap.name || "—",
      sublabel: String(entry.numeric),
      value: String(entry.numeric),
      percent: ((entry.numeric - min) / (max - min)) * 100,
      active: entry.numeric === value,
    }));

  return {
    mode: "slider",
    min,
    max,
    step,
    value,
    percent,
    stops,
    disabled: false,
    ariaValueText: activeSnap
      ? `${activeSnap.name} ${value}`
      : String(value),
    activeLabel: String(value),
    activeSubLabel: activeSnap?.name ?? "",
    positionLabel: `${min}–${max}`,
  };
}
