// Author: elecvoid243, 2026-10-03

/**
 * Continuous "thinking effort" track for models whose reasoning_effort is a
 * free number instead of a fixed enum (DeepSeek-V4.1-Flash: 1-100, where
 * low / high / xhigh / max are plain aliases for 25 / 50 / 75 / 100).
 *
 * The track is purely free-form: dragging never pulls the handle towards an
 * alias (that fought the pointer and made the handle jitter), the aliases are
 * only labels plus click-to-jump shortcuts. Values are sent as strings, the
 * same free-form channel the level list already uses.
 */

/** A named alias on the track; `value` is what the model actually receives. */
export interface ThinkingEffortSnap {
  name: string;
  value: number;
}

export interface ThinkingEffortSliderConfig {
  min: number;
  max: number;
  step: number;
  snaps: ThinkingEffortSnap[];
}

export type EffortSliderError =
  | "range"
  | "step"
  | "snapName"
  | "snapValue"
  | "snapRange"
  | "snapDuplicate";

export const DEFAULT_THINKING_EFFORT_SLIDER: ThinkingEffortSliderConfig = {
  min: 1,
  max: 100,
  step: 1,
  snaps: [
    { name: "low", value: 25 },
    { name: "high", value: 50 },
    { name: "xhigh", value: 75 },
    { name: "max", value: 100 },
  ],
};

function clampToTrack(value: number, config: ThinkingEffortSliderConfig): number {
  return Math.min(config.max, Math.max(config.min, value));
}

/** Resolve the alias a value sits exactly on, for labeling. */
export function findEffortSnap(
  value: number,
  config: ThinkingEffortSliderConfig,
): ThinkingEffortSnap | null {
  return config.snaps.find((snap) => snap.value === value) ?? null;
}

/** Chip / slider readout: "high 50" on an alias, "37" on a free value. */
export function effortValueLabel(
  value: number,
  config: ThinkingEffortSliderConfig,
): string {
  const snap = findEffortSnap(value, config);
  return snap ? `${snap.name} ${value}` : String(value);
}

/**
 * Clamp a dragged value onto the track and nothing else. No alias snapping
 * (it fought the pointer and made the handle jitter) and no step
 * quantization (v-slider already emits grid values, and re-rounding would
 * silently rewrite a value stored under a coarser step).
 */
export function normalizeEffortValue(
  value: number,
  config: ThinkingEffortSliderConfig,
): number {
  const target = Number.isFinite(value) ? value : config.min;
  return clampToTrack(target, config);
}

/** First invalid field, or null when the draft can be saved. */
export function validateEffortSliderConfig(
  config: ThinkingEffortSliderConfig,
): EffortSliderError | null {
  if (
    !Number.isFinite(config.min) ||
    !Number.isFinite(config.max) ||
    !(config.max > config.min)
  ) {
    return "range";
  }
  if (!Number.isFinite(config.step) || config.step <= 0) return "step";
  const seen = new Set<number>();
  for (const snap of config.snaps) {
    if (!snap.name.trim()) return "snapName";
    if (!Number.isFinite(snap.value)) return "snapValue";
    if (snap.value < config.min || snap.value > config.max) return "snapRange";
    if (seen.has(snap.value)) return "snapDuplicate";
    seen.add(snap.value);
  }
  return null;
}

/**
 * Read a stored config back, dropping malformed pieces field by field so a
 * corrupt entry costs one setting instead of the whole track. An inverted
 * range reverts to the default track; snaps are kept as written (validation
 * is the editor's job, not the loader's).
 */
export function normalizeEffortSliderConfig(
  raw: unknown,
): ThinkingEffortSliderConfig {
  if (!raw || typeof raw !== "object") return DEFAULT_THINKING_EFFORT_SLIDER;
  const input = raw as Partial<ThinkingEffortSliderConfig>;
  const fallback = DEFAULT_THINKING_EFFORT_SLIDER;
  const min = Number.isFinite(Number(input.min)) ? Number(input.min) : fallback.min;
  const max = Number.isFinite(Number(input.max)) ? Number(input.max) : fallback.max;
  const step =
    Number.isFinite(Number(input.step)) && Number(input.step) > 0
      ? Number(input.step)
      : fallback.step;
  const snaps = Array.isArray(input.snaps)
    ? input.snaps
        .filter(
          (snap): snap is ThinkingEffortSnap =>
            !!snap &&
            typeof snap === "object" &&
            typeof (snap as ThinkingEffortSnap).name === "string" &&
            (snap as ThinkingEffortSnap).name.trim() !== "" &&
            Number.isFinite(Number((snap as ThinkingEffortSnap).value)),
        )
        .map((snap) => ({ name: snap.name, value: Number(snap.value) }))
    : fallback.snaps;
  return max > min ? { min, max, step, snaps } : { ...fallback, step, snaps };
}
