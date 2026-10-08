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

        <!-- Mode picker: the level list keeps the enum-style models working,
             the slider serves models whose effort field is a free number. -->
        <div class="pa-4 pb-0 pl-6">
          <v-btn-toggle
            v-model="localMode"
            mandatory
            density="compact"
            variant="tonal"
            divided
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

            <div class="effort-slider-snaps-head">
              {{ tm("input.sliderSnaps") }}
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
                variant="text"
                color="error"
                :aria-label="tm('input.sliderSnapDeleteAria')"
                @click="removeSnap(index)"
              >
                <v-icon icon="mdi-delete"></v-icon>
              </v-btn>
            </div>
          </template>

          <template v-else>
            <div
              v-for="(level, index) in localLevels"
              :key="index"
              class="effort-level-row"
            >
              <v-text-field
                v-model="level.name"
                :label="tm('input.levelName')"
                density="compact"
                hide-details
                class="effort-level-name"
              />
              <v-text-field
                v-model="level.value"
                :label="tm('input.levelValue')"
                density="compact"
                hide-details
                class="effort-level-value"
              />
              <v-btn
                icon
                variant="text"
                color="error"
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
import { computed, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import {
  DEFAULT_THINKING_EFFORT_SLIDER,
  validateEffortSliderConfig,
  type EffortSliderError,
  type ThinkingEffortSliderConfig,
} from "@/composables/thinkingEffortSlider";
import {
  createPresetId,
  type ThinkingEffortLevel,
  type ThinkingEffortPreset,
} from "@/composables/thinkingEffortPresets";

export interface ThinkingEffortEditorPayload {
  presets: ThinkingEffortPreset[];
  activePreset: string;
}

const props = defineProps<{
  modelValue: boolean;
  presets: ThinkingEffortPreset[];
  activePreset: string;
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

const presetOptions = computed(() =>
  localPresets.value.map((preset) => ({ title: preset.name, value: preset.id })),
);

const activeDraft = computed(
  () => localPresets.value.find((preset) => preset.id === localActiveId.value) ?? null,
);

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

function loadDraftFromPreset(preset: ThinkingEffortPreset | null): void {
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
    loadDraftFromPreset(activeDraft.value);
  },
  { immediate: true },
);

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
  loadDraftFromPreset(activeDraft.value);
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
  loadDraftFromPreset(preset);
}

function removePreset(): void {
  const index = localPresets.value.findIndex(
    (preset) => preset.id === localActiveId.value,
  );
  if (index < 0) return;
  localPresets.value.splice(index, 1);
  const next = localPresets.value[index] ?? localPresets.value[index - 1] ?? null;
  localActiveId.value = next?.id ?? "";
  loadDraftFromPreset(next);
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
}

function removeSnap(index: number) {
  localSnaps.value.splice(index, 1);
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
    return;
  }
  localLevels.value = [
    { name: tm("input.thinkingEffortOptions.low"), value: "low" },
    { name: tm("input.thinkingEffortOptions.high"), value: "high" },
    { name: tm("input.thinkingEffortOptions.max"), value: "max" },
  ];
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

.effort-level-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.effort-level-name {
  flex: 1 1 45%;
}

.effort-level-value {
  flex: 1 1 45%;
}

.effort-slider-fields {
  display: flex;
  align-items: center;
  gap: 8px;
}

.effort-slider-snaps-head {
  margin: 14px 0 6px;
  color: var(--sp-text-primary);
  font-size: 13px;
  font-weight: 600;
}

.effort-level-validation-error {
  color: rgb(var(--v-theme-error));
  font-size: 12px;
  margin-top: 4px;
}
</style>
