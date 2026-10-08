// Author: elecvoid243, 2026-10-08

/**
 * Thinking-effort presets as persisted in `cmd_config.json`
 * (`chatui.thinking_effort`).
 *
 * The backend (`astrbot/dashboard/services/chatui_settings.py`) owns
 * validation; these helpers keep the UI resilient while the payload is
 * loading or when an older config predates a field, and give the editor its
 * draft shapes. An empty preset list is a legitimate state: it means "no
 * custom presets", which the input row renders with the built-in localized
 * levels instead.
 */

import {
  DEFAULT_THINKING_EFFORT_SLIDER,
  normalizeEffortSliderConfig,
  type ThinkingEffortSliderConfig,
} from "./thinkingEffortSlider";

/** Fallback selection when the stored value is missing or unusable. */
export const DEFAULT_EFFORT_VALUE = "max";

export interface ThinkingEffortLevel {
  name: string;
  value: string;
}

export interface ThinkingEffortPreset {
  id: string;
  name: string;
  mode: "levels" | "slider";
  levels: ThinkingEffortLevel[];
  slider: ThinkingEffortSliderConfig;
}

export interface ThinkingEffortSettings {
  active_preset: string;
  value: string;
  presets: ThinkingEffortPreset[];
}

/** Id for a preset the user is creating; stable within one browser session. */
export function createPresetId(): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return `preset-${uuid}`;
  return `preset-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function normalizeLevels(raw: unknown): ThinkingEffortLevel[] {
  if (!Array.isArray(raw)) return [];
  const levels: ThinkingEffortLevel[] = [];
  for (const item of raw) {
    const entry = item as Partial<ThinkingEffortLevel> | null;
    if (!entry || typeof entry.name !== "string" || typeof entry.value !== "string") {
      continue;
    }
    const name = entry.name.trim();
    const value = entry.value.trim();
    if (!name || !value) continue;
    levels.push({ name, value });
  }
  return levels;
}

function normalizePreset(raw: unknown): ThinkingEffortPreset | null {
  const entry = raw as Partial<ThinkingEffortPreset> | null;
  if (!entry || typeof entry !== "object") return null;
  const id = typeof entry.id === "string" ? entry.id.trim() : "";
  const name = typeof entry.name === "string" ? entry.name.trim() : "";
  if (!id || !name) return null;
  return {
    id,
    name,
    mode: entry.mode === "slider" ? "slider" : "levels",
    levels: normalizeLevels(entry.levels),
    slider: normalizeEffortSliderConfig(entry.slider ?? DEFAULT_THINKING_EFFORT_SLIDER),
  };
}

export function normalizeEffortSettings(raw: unknown): ThinkingEffortSettings {
  const data = (raw ?? {}) as Partial<ThinkingEffortSettings>;
  const presets = Array.isArray(data.presets)
    ? data.presets
        .map((preset) => normalizePreset(preset))
        .filter((preset): preset is ThinkingEffortPreset => preset !== null)
    : [];

  const activeCandidate = typeof data.active_preset === "string" ? data.active_preset.trim() : "";
  const active_preset = presets.some((preset) => preset.id === activeCandidate)
    ? activeCandidate
    : (presets[0]?.id ?? "");

  const rawValue = typeof data.value === "string" ? data.value.trim() : "";
  const value = rawValue || DEFAULT_EFFORT_VALUE;

  return { active_preset, value, presets };
}

/** The preset the input row currently renders, or null when none exists. */
export function findActivePreset(
  settings: ThinkingEffortSettings,
): ThinkingEffortPreset | null {
  return settings.presets.find((preset) => preset.id === settings.active_preset) ?? null;
}

/**
 * Land a stored value on a preset: slider values are clamped onto the track,
 * aliases resolve to their numeric value, and level values pass through
 * untouched. Mirrors the fallback the input row applies on load.
 */
export function settleEffortValue(
  value: string,
  preset: ThinkingEffortPreset | null,
): string {
  const trimmed = value.trim();
  if (!preset) return trimmed || DEFAULT_EFFORT_VALUE;
  if (preset.mode === "levels") {
    const available = preset.levels.map((level) => level.value);
    if (available.includes(trimmed)) return trimmed;
    if (available.includes(DEFAULT_EFFORT_VALUE)) return DEFAULT_EFFORT_VALUE;
    return available[0] ?? (trimmed || DEFAULT_EFFORT_VALUE);
  }
  const track = preset.slider;
  const numeric = Number(trimmed);
  if (trimmed !== "" && Number.isFinite(numeric)) {
    return String(Math.min(track.max, Math.max(track.min, numeric)));
  }
  const alias = track.snaps.find(
    (snap) => snap.name.toLowerCase() === trimmed.toLowerCase(),
  );
  if (alias) return String(alias.value);
  return trimmed === "" ? String(track.min) : trimmed;
}
