<!--
  Thinking-effort preset editor.

  Presets live in cmd_config.json (`chatui.thinking_effort`) and this dialog is
  the only place that rewrites the list: pick a preset, edit its name and shape
  (level list or numeric track), add a copy, or delete it. The list is edited as
  a draft and handed to ChatInput on save, which persists it through
  /api/chat/ui-settings/presets and re-settles the current selection.

  Deleting every preset is allowed: an empty list means "use the built-in
  localized levels", and the input row renders that fallback.
-->
<template>
  <v-dialog
    :model-value="modelValue"
    max-width="560"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card class="thinking-effort-levels-dialog">
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ tm("input.editThinkingEffort") }}
      </v-card-title>
      <!-- Plain div (not v-card-subtitle, which is nowrap/ellipsis) so the
           hint wraps fully across lines. -->
      <div class="thinking-effort-levels-hint pa-4 pt-2 pb-0 pl-6">
        {{ tm("input.thinkingEffortLevelsHint") }}
      </div>

      <!-- Preset picker: switching keeps whatever is in the editor by
           committing it to the outgoing preset first. -->
      <div class="effort-preset-row pa-4 pb-0 pl-6">
        <v-select
          :model-value="localActiveId"
          :items="presetOptions"
          :label="tm('input.presetLabel')"
          density="compact"
          hide-details
          class="effort-preset-select"
          @update:model-value="onPresetChange"
        />
        <v-btn
          variant="tonal"
          size="small"
          @click="addPreset"
        >
          <v-icon icon="mdi-plus" size="small"></v-icon>
          {{ tm("input.addPreset") }}
        </v-btn>
      </div>

      <div v-if="!localPresets.length" class="thinking-effort-levels-hint pa-4 pb-0 pl-6">
        {{ tm("input.emptyPresets") }}
      </div>

      <template v-if="activeDraft">
        <div class="pa-4 pb-0 pl-6">
          <v-text-field
            v-model="localName"
            :label="tm('input.presetNameLabel')"
            density="compact"
            hide-details
            class="effort-preset-name"
          />
        </div>

        <!-- Mode picker: both shapes render as the same track; this only
             changes whether the handle snaps to stops or moves freely. -->
        <div class="pa-4 pb-0 pl-6">
          <v-btn-toggle
            :model-value="localMode"
            mandatory
            density="compact"
            variant="tonal"
            divided
            @update:model-value="onModeChange"
          >
            <v-btn value="levels" size="small">
              {{ tm("input.thinkingEffortModeLevels") }}
            </v-btn>
            <v-btn value="slider" size="small">
              {{ tm("input.thinkingEffortModeSlider") }}
            </v-btn>
          </v-btn-toggle>
        </div>

        <v-card-text class="pa-4">
          <div class="effort-editor-preview">
            <div class="effort-editor-preview__head">
              <span class="effort-editor-preview__caption">
                {{ tm("input.previewLabel") }}
              </span>
              <span class="effort-editor-preview__meta">{{ previewMeta }}</span>
            </div>
            <div class="effort-editor-preview__track">
              <ThinkingEffortTrack
                :model-value="localPreviewValue"
                :levels="localLevels"
                :mode="localMode"
                :slider="draftSlider"
                :aria-label="`${tm('input.previewLabel')} · ${tm('input.thinkingEffort')}`"
                compact
                @update:model-value="localPreviewValue = $event"
              />
            </div>
          </div>

          <template v-if="localMode === 'slider'">
            <div class="thinking-effort-levels-hint mb-3">
              {{ tm("input.thinkingEffortSliderHint") }}
            </div>

            <div class="effort-slider-fields">
              <v-text-field
                v-model="localMin"
                type="number"
                :label="tm('input.sliderMin')"
                density="compact"
                hide-details
              />
              <v-text-field
                v-model="localMax"
                type="number"
                :label="tm('input.sliderMax')"
                density="compact"
                hide-details
              />
              <v-text-field
                v-model="localStep"
                type="number"
                :label="tm('input.sliderStep')"
                density="compact"
                hide-details
              />
            </div>

            <div class="effort-rows-head">
              <span>{{ tm("input.sliderSnaps") }}</span>
              <span class="effort-rows-head__hint">
                {{ tm("input.sliderMarkersHint") }}
              </span>
            </div>
            <div
              v-for="(snap, index) in localSnaps"
              :key="index"
              class="effort-level-row"
            >
              <v-text-field
                v-model="snap.name"
                :label="tm('input.sliderSnapName')"
                density="compact"
                hide-details
                class="effort-level-name"
              />
              <v-text-field
                v-model="snap.value"
                type="number"
                :label="tm('input.sliderSnapValue')"
                density="compact"
                hide-details
                class="effort-level-value"
              />
              <v-btn
                icon
                size="small"
                variant="text"
                color="error"
                class="effort-level-delete"
                :aria-label="tm('input.sliderSnapDeleteAria')"
                @click="removeSnap(index)"
              >
                <v-icon icon="mdi-delete"></v-icon>
              </v-btn>
            </div>
          </template>

          <template v-else>
            <div class="effort-rows-head">
              <span>{{ tm("input.levelOrderLabel") }}</span>
              <span class="effort-rows-head__hint">
                {{ tm("input.levelOrderHint") }}
              </span>
            </div>
            <div
              v-for="(level, index) in localLevels"
              :key="index"
              class="effort-level-row"
              :class="levelRowClass(index)"
              @dragover.prevent="onLevelDragOver($event, index)"
              @dragleave="onLevelDragLeave($event, index)"
              @drop.prevent="onLevelDrop($event, index)"
            >
              <button
                ref="levelHandles"
                type="button"
                class="effort-level-handle"
                draggable="true"
                :aria-label="
                  tm('input.levelReorderAria', {
                    name: level.name || tm('input.levelName'),
                  })
                "
                :title="
                  tm('input.levelReorderAria', {
                    name: level.name || tm('input.levelName'),
                  })
                "
                @dragstart="onLevelDragStart($event, index)"
                @dragend="onLevelDragEnd"
                @keydown="onLevelHandleKeydown($event, index)"
              >
                <v-icon size="14">mdi-drag-vertical</v-icon>
              </button>
              <span class="effort-level-order">
                {{ String(index + 1).padStart(2, "0") }}
              </span>
              <v-text-field
                v-model="level.name"
                :label="tm('input.levelName')"
                density="compact"
                hide-details
                class="effort-level-name"
              />
              <v-text-field
                :model-value="level.value"
                :label="tm('input.levelValue')"
                density="compact"
                hide-details
                class="effort-level-value"
                @update:model-value="onLevelValueChange(index, $event)"
              />
              <v-btn
                icon
                size="small"
                variant="text"
                color="error"
                class="effort-level-delete"
                :aria-label="tm('input.levelDeleteAria')"
                @click="removeLevel(index)"
              >
                <v-icon icon="mdi-delete"></v-icon>
              </v-btn>
            </div>
          </template>

          <div v-if="validationError" class="effort-level-validation-error">
            {{ validationError }}
          </div>
        </v-card-text>
      </template>

      <v-card-actions class="pa-4 pt-0">
        <v-btn
          v-if="activeDraft"
          variant="tonal"
          size="small"
          @click="addRow"
        >
          <v-icon icon="mdi-plus" size="small"></v-icon>
          {{ localMode === "slider" ? tm("input.addSnap") : tm("input.addLevel") }}
        </v-btn>
        <v-btn
          v-if="activeDraft"
          variant="tonal"
          size="small"
          @click="restoreDefaults"
        >
          {{ tm("input.restoreDefaultLevels") }}
        </v-btn>
        <v-btn
          v-if="activeDraft"
          variant="text"
          size="small"
          color="error"
          class="effort-delete-preset"
          @click="removePreset"
        >
          {{ tm("input.deletePreset") }}
        </v-btn>
        <v-spacer />
        <v-btn variant="text" @click="emit('update:modelValue', false)">
          {{ tm("input.cancel") }}
        </v-btn>
        <v-btn
          variant="tonal"
          color="primary"
          :disabled="!isValid"
          @click="save"
        >
          {{ tm("input.save") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import {
  DEFAULT_THINKING_EFFORT_SLIDER,
  validateEffortSliderConfig,
  type EffortSliderError,
  type ThinkingEffortSliderConfig,
} from "@/composables/thinkingEffortSlider";
import {
  createPresetId,
  settleEffortValue,
  type ThinkingEffortLevel,
  type ThinkingEffortPreset,
} from "@/composables/thinkingEffortPresets";
import ThinkingEffortTrack from "./ThinkingEffortTrack.vue";

export interface ThinkingEffortEditorPayload {
  presets: ThinkingEffortPreset[];
  activePreset: string;
}

const props = defineProps<{
  modelValue: boolean;
  presets: ThinkingEffortPreset[];
  activePreset: string;
  /** Current input-row selection, used as the preview starting point. */
  value?: string;
}>();

const emit = defineEmits<{
  "update:modelValue": [open: boolean];
  save: [payload: ThinkingEffortEditorPayload];
}>();

const { tm } = useModuleI18n("features/chat");

// Draft list the dialog edits; props were already cloned on open.
const localPresets = ref<ThinkingEffortPreset[]>([]);
const localActiveId = ref("");

// Editor fields for the active draft. Numbers stay as raw strings so a
// cleared box reads as "invalid" instead of silently collapsing to 0; they
// are committed back onto the draft when the selection moves or on save.
const localName = ref("");
const localMode = ref<"levels" | "slider">("levels");
const localLevels = ref<ThinkingEffortLevel[]>([]);
const localMin = ref("1");
const localMax = ref("100");
const localStep = ref("1");
const localSnaps = ref<{ name: string; value: string }[]>([]);
const localPreviewValue = ref("");
const levelHandles = ref<HTMLButtonElement[]>([]);
const dragLevelIndex = ref<number | null>(null);
const dropTarget = ref<{ index: number; after: boolean } | null>(null);

const presetOptions = computed(() =>
  localPresets.value.map((preset) => ({ title: preset.name, value: preset.id })),
);

const activeDraft = computed(
  () => localPresets.value.find((preset) => preset.id === localActiveId.value) ?? null,
);

const previewMeta = computed(() => {
  if (localMode.value === "levels") {
    return tm("input.previewLevelsMeta", { count: localLevels.value.length });
  }
  return tm("input.previewSliderMeta", {
    min: localMin.value,
    max: localMax.value,
  });
});

const draftSlider = computed<ThinkingEffortSliderConfig>(() => ({
  min: toNumber(localMin.value),
  max: toNumber(localMax.value),
  step: toNumber(localStep.value),
  snaps: localSnaps.value.map((snap) => ({
    name: snap.name,
    value: toNumber(snap.value),
  })),
}));

function toNumber(raw: string): number {
  return raw.trim() === "" ? Number.NaN : Number(raw);
}

function clonePreset(preset: ThinkingEffortPreset): ThinkingEffortPreset {
  return {
    id: preset.id,
    name: preset.name,
    mode: preset.mode,
    levels: preset.levels.map((level) => ({ ...level })),
    slider: {
      min: preset.slider.min,
      max: preset.slider.max,
      step: preset.slider.step,
      snaps: preset.slider.snaps.map((snap) => ({ ...snap })),
    },
  };
}

function previewPreset(): ThinkingEffortPreset | null {
  const preset = activeDraft.value;
  if (!preset) return null;
  return {
    id: preset.id,
    name: localName.value.trim() || preset.name,
    mode: localMode.value,
    levels: localLevels.value.map((level) => ({ ...level })),
    slider: draftSlider.value,
  };
}

function previewCandidate(source?: string): string {
  const explicit = source?.trim() ?? "";
  if (explicit) return explicit;
  const current = localPreviewValue.value.trim();
  if (current) return current;
  const fromProps = props.value?.trim() ?? "";
  if (fromProps) return fromProps;
  if (localMode.value === "slider") {
    const min = toNumber(localMin.value);
    const max = toNumber(localMax.value);
    if (Number.isFinite(min) && Number.isFinite(max) && max > min) {
      return String(Math.round((min + max) / 2));
    }
  }
  return "";
}

function settlePreview(source?: string): void {
  localPreviewValue.value = settleEffortValue(
    previewCandidate(source),
    previewPreset(),
  );
}

function loadDraftFromPreset(
  preset: ThinkingEffortPreset | null,
  previewSource?: string,
): void {
  localName.value = preset?.name ?? "";
  localMode.value = preset?.mode ?? "levels";
  localLevels.value = (preset?.levels ?? []).map((level) => ({ ...level }));
  const track = preset?.slider ?? DEFAULT_THINKING_EFFORT_SLIDER;
  localMin.value = String(track.min);
  localMax.value = String(track.max);
  localStep.value = String(track.step);
  localSnaps.value = track.snaps.map((snap) => ({
    name: snap.name,
    value: String(snap.value),
  }));
  settlePreview(previewSource);
}

/** Push the editor fields onto the active draft (a no-op when none is selected). */
function commitDraftToActive(): void {
  const preset = activeDraft.value;
  if (!preset) return;
  const name = localName.value.trim();
  if (name) preset.name = name;
  preset.mode = localMode.value;
  preset.levels = localLevels.value
    .map((level) => ({ name: level.name.trim(), value: level.value.trim() }))
    .filter((level) => level.name !== "" || level.value !== "");
  // A track that is mid-edit (or invalid) keeps the previous values instead of
  // writing NaNs into the draft; validation blocks saving it anyway.
  const track = draftSlider.value;
  if (validateEffortSliderConfig(track) === null) {
    preset.slider = track;
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return;
    localPresets.value = props.presets.map(clonePreset);
    localActiveId.value = presetOptions.value.some(
      (option) => option.value === props.activePreset,
    )
      ? props.activePreset
      : (localPresets.value[0]?.id ?? "");
    loadDraftFromPreset(activeDraft.value, props.value);
  },
  { immediate: true },
);

const validationError = computed(() => {
  if (!activeDraft.value) return "";
  if (!localName.value.trim()) return tm("input.presetNameRequired");
  if (localMode.value === "slider") {
    const error = validateEffortSliderConfig(draftSlider.value);
    return error ? tm(SLIDER_ERROR_KEYS[error]) : "";
  }
  const seen = new Set<string>();
  for (const level of localLevels.value) {
    const name = level.name.trim();
    const value = level.value.trim();
    if (!name) return tm("input.levelNameRequired");
    if (!value) return tm("input.levelValueRequired");
    if (seen.has(value)) return tm("input.levelDuplicateValue");
    seen.add(value);
  }
  return "";
});

const SLIDER_ERROR_KEYS: Record<EffortSliderError, string> = {
  range: "input.sliderRangeInvalid",
  step: "input.sliderStepInvalid",
  snapName: "input.sliderSnapNameRequired",
  snapValue: "input.sliderSnapValueRequired",
  snapRange: "input.sliderSnapOutOfRange",
  snapDuplicate: "input.sliderSnapDuplicate",
};

// An empty list is a valid outcome (delete them all → built-in levels).
const isValid = computed(() => {
  if (!activeDraft.value) return true;
  if (validationError.value !== "") return false;
  return localMode.value === "slider" || localLevels.value.length > 0;
});

function onPresetChange(id: string): void {
  commitDraftToActive();
  localActiveId.value = id;
  loadDraftFromPreset(activeDraft.value, props.value);
}

function addPreset(): void {
  commitDraftToActive();
  // A new preset starts from what the editor currently shows, so "duplicate
  // and tweak" costs one click.
  const current = activeDraft.value;
  const preset: ThinkingEffortPreset = {
    id: createPresetId(),
    name: tm("input.newPresetName"),
    mode: current?.mode ?? "levels",
    levels: (current?.levels ?? []).map((level) => ({ ...level })),
    slider: current
      ? {
          min: current.slider.min,
          max: current.slider.max,
          step: current.slider.step,
          snaps: current.slider.snaps.map((snap) => ({ ...snap })),
        }
      : { ...DEFAULT_THINKING_EFFORT_SLIDER, snaps: [] },
  };
  localPresets.value.push(preset);
  localActiveId.value = preset.id;
  loadDraftFromPreset(preset, localPreviewValue.value || props.value);
}

function removePreset(): void {
  const index = localPresets.value.findIndex(
    (preset) => preset.id === localActiveId.value,
  );
  if (index < 0) return;
  localPresets.value.splice(index, 1);
  const next = localPresets.value[index] ?? localPresets.value[index - 1] ?? null;
  localActiveId.value = next?.id ?? "";
  loadDraftFromPreset(next, props.value);
}

function onModeChange(mode: string): void {
  if (mode !== "levels" && mode !== "slider") return;
  if (localMode.value === mode) return;
  localMode.value = mode;
  settlePreview(localPreviewValue.value);
}

function moveLevel(from: number, to: number): void {
  if (to < 0 || to >= localLevels.value.length || from === to) return;
  const [level] = localLevels.value.splice(from, 1);
  localLevels.value.splice(to, 0, level);
  void nextTick(() => levelHandles.value[to]?.focus());
}

function onLevelDragStart(event: DragEvent, index: number): void {
  dragLevelIndex.value = index;
  dropTarget.value = null;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", String(index));
  }
}

function onLevelDragOver(event: DragEvent, index: number): void {
  if (dragLevelIndex.value === null) return;
  const row = event.currentTarget as HTMLElement | null;
  if (!row) return;
  const rect = row.getBoundingClientRect();
  dropTarget.value = {
    index,
    after: event.clientY > rect.top + rect.height / 2,
  };
}

function onLevelDragLeave(event: DragEvent, index: number): void {
  const row = event.currentTarget as HTMLElement | null;
  const related = event.relatedTarget as Node | null;
  if (row && related && row.contains(related)) return;
  if (dropTarget.value?.index === index) dropTarget.value = null;
}

function onLevelDrop(event: DragEvent, index: number): void {
  if (dragLevelIndex.value === null) return;
  const after =
    dropTarget.value?.index === index ? dropTarget.value.after : false;
  let to = index + (after ? 1 : 0);
  if (dragLevelIndex.value < to) to -= 1;
  const from = dragLevelIndex.value;
  dragLevelIndex.value = null;
  dropTarget.value = null;
  moveLevel(from, to);
}

function onLevelDragEnd(): void {
  dragLevelIndex.value = null;
  dropTarget.value = null;
}

function onLevelHandleKeydown(event: KeyboardEvent, index: number): void {
  if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
  event.preventDefault();
  moveLevel(index, index + (event.key === "ArrowUp" ? -1 : 1));
}

function levelRowClass(index: number): Record<string, boolean> {
  return {
    "effort-level-row--dragging": dragLevelIndex.value === index,
    "effort-level-row--drop-before":
      dropTarget.value?.index === index && !dropTarget.value.after,
    "effort-level-row--drop-after":
      dropTarget.value?.index === index && dropTarget.value.after,
  };
}

function addRow() {
  if (localMode.value === "slider") {
    const track = draftSlider.value;
    const mid =
      Number.isFinite(track.min) && Number.isFinite(track.max)
        ? Math.round((track.min + track.max) / 2)
        : DEFAULT_THINKING_EFFORT_SLIDER.min;
    localSnaps.value.push({ name: "", value: String(mid) });
    return;
  }
  localLevels.value.push({ name: "", value: "" });
}

function removeLevel(index: number) {
  localLevels.value.splice(index, 1);
  settlePreview(localPreviewValue.value);
}

function onLevelValueChange(index: number, value: string): void {
  const level = localLevels.value[index];
  if (!level) return;
  const previous = level.value;
  level.value = value;
  if (localPreviewValue.value === previous) localPreviewValue.value = value;
}

function removeSnap(index: number) {
  localSnaps.value.splice(index, 1);
  settlePreview(localPreviewValue.value);
}

function restoreDefaults() {
  if (localMode.value === "slider") {
    const defaults = DEFAULT_THINKING_EFFORT_SLIDER;
    localMin.value = String(defaults.min);
    localMax.value = String(defaults.max);
    localStep.value = String(defaults.step);
    localSnaps.value = defaults.snaps.map((snap) => ({
      name: snap.name,
      value: String(snap.value),
    }));
  } else {
    localLevels.value = [
      { name: tm("input.thinkingEffortOptions.low"), value: "low" },
      { name: tm("input.thinkingEffortOptions.high"), value: "high" },
      { name: tm("input.thinkingEffortOptions.max"), value: "max" },
    ];
  }
  settlePreview(localPreviewValue.value);
}

function save() {
  if (!isValid.value) return;
  commitDraftToActive();
  emit("save", {
    presets: localPresets.value.map(clonePreset),
    activePreset: localActiveId.value,
  });
  emit("update:modelValue", false);
}
</script>

<style scoped>
.thinking-effort-levels-dialog {
  background-color: rgb(var(--v-theme-surface));
}

/* Hint note below the title — wraps across lines (v-card-subtitle would
   truncate it with an ellipsis). */
.thinking-effort-levels-hint {
  color: rgba(
    var(--v-theme-on-surface),
    var(--v-medium-emphasis-opacity, 0.62)
  );
  font-size: 12px;
  line-height: 1.4;
  white-space: normal;
}

.effort-preset-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.effort-preset-select {
  flex: 1 1 auto;
  min-width: 0;
}

.effort-preset-name {
  margin-bottom: 4px;
}

.effort-delete-preset {
  margin-left: 4px;
}

.effort-editor-preview {
  margin: 0 0 12px;
  padding: 10px 12px 7px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: 10px;
  background: rgba(var(--v-theme-on-surface), 0.03);
}

.effort-editor-preview__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.effort-editor-preview__caption {
  color: var(--sp-text-muted);
  font-size: 9.5px;
  font-weight: 600;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.effort-editor-preview__meta {
  color: var(--sp-text-muted);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}

.effort-editor-preview__track {
  margin-top: 6px;
}

.effort-rows-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin: 14px 0 6px;
  color: var(--sp-text-primary);
  font-size: 12px;
  font-weight: 600;
}

.effort-rows-head__hint {
  color: var(--sp-text-muted);
  font-size: 10.5px;
  font-weight: 400;
  text-align: right;
}

.effort-level-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  padding: 2px 3px;
  border: 1px solid transparent;
  border-radius: 8px;
  transition:
    background-color 150ms ease,
    opacity 150ms ease;
}

.effort-level-row:hover {
  background: var(--sp-chip-hover-bg);
}

.effort-level-row--dragging {
  opacity: 0.38;
}

.effort-level-row--drop-before {
  box-shadow: inset 0 2px 0 rgb(var(--v-theme-primary));
}

.effort-level-row--drop-after {
  box-shadow: inset 0 -2px 0 rgb(var(--v-theme-primary));
}

.effort-level-handle {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  width: 22px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--sp-text-muted);
  cursor: grab;
}

.effort-level-handle:hover {
  background: var(--sp-chip-active-bg);
  color: var(--sp-text-primary);
}

.effort-level-handle:active {
  cursor: grabbing;
}

.effort-level-handle:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 1px;
}

.effort-level-order {
  flex: 0 0 18px;
  width: 18px;
  color: var(--sp-text-muted);
  font-size: 10px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.effort-level-name {
  flex: 1 1 48%;
  min-width: 0;
}

.effort-level-value {
  flex: 1 1 38%;
  min-width: 0;
}

.effort-level-delete {
  flex: 0 0 auto;
}

.effort-slider-fields {
  display: flex;
  align-items: center;
  gap: 8px;
}

.effort-level-validation-error {
  color: rgb(var(--v-theme-error));
  font-size: 12px;
  margin-top: 4px;
}

@media (max-width: 600px) {
  .effort-level-order {
    display: none;
  }
}
</style>
