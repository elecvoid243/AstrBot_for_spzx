<template>
  <v-dialog
    :model-value="modelValue"
    max-width="560"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card class="thinking-effort-levels-dialog">
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ tm("input.editThinkingEffortLevels") }}
      </v-card-title>
      <!-- Plain div (not v-card-subtitle, which is nowrap/ellipsis) so the
           hint wraps fully across lines. -->
      <div class="thinking-effort-levels-hint pa-4 pt-2 pb-0 pl-6">
        {{ tm("input.thinkingEffortLevelsHint") }}
      </div>

      <v-card-text class="pa-4">
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
        <div v-if="validationError" class="effort-level-validation-error">
          {{ validationError }}
        </div>
      </v-card-text>

      <v-card-actions class="pa-4 pt-0">
        <v-btn variant="tonal" size="small" @click="addLevel">
          <v-icon icon="mdi-plus" size="small"></v-icon>
          {{ tm("input.addLevel") }}
        </v-btn>
        <v-btn variant="tonal" size="small" @click="restoreDefaults">
          {{ tm("input.restoreDefaultLevels") }}
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

interface ThinkingEffortLevel {
  name: string;
  value: string;
}

const props = defineProps<{
  modelValue: boolean;
  levels: ThinkingEffortLevel[];
}>();

const emit = defineEmits<{
  "update:modelValue": [open: boolean];
  save: [levels: ThinkingEffortLevel[]];
}>();

const { tm } = useModuleI18n("features/chat");

// Local editable copy; the parent state is only mutated on save.
const localLevels = ref<ThinkingEffortLevel[]>([]);

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      localLevels.value = props.levels.map((level) => ({ ...level }));
    }
  },
  { immediate: true },
);

const validationError = computed(() => {
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

const isValid = computed(
  () => validationError.value === "" && localLevels.value.length > 0,
);

function addLevel() {
  localLevels.value.push({ name: "", value: "" });
}

function removeLevel(index: number) {
  localLevels.value.splice(index, 1);
}

function restoreDefaults() {
  localLevels.value = [
    { name: tm("input.thinkingEffortOptions.low"), value: "low" },
    { name: tm("input.thinkingEffortOptions.high"), value: "high" },
    { name: tm("input.thinkingEffortOptions.max"), value: "max" },
  ];
}

function save() {
  if (!isValid.value) return;
  emit(
    "save",
    localLevels.value.map((level) => ({
      name: level.name.trim(),
      value: level.value.trim(),
    })),
  );
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

.effort-level-validation-error {
  color: rgb(var(--v-theme-error));
  font-size: 12px;
  margin-top: 4px;
}
</style>
