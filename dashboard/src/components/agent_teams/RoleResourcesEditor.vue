<template>
  <v-expansion-panel v-for="field in fields" :key="field" :value="field">
    <v-expansion-panel-title>{{
      tm(`roles.${field}`)
    }}</v-expansion-panel-title>
    <v-expansion-panel-text>
      <v-select
        :model-value="modes[field]"
        :data-issue-path="
          modes[field] === 'custom' ? undefined : `definition.${field}`
        "
        :items="modeItems"
        :label="tm('roles.mode')"
        :aria-label="`${tm(`roles.${field}`)}: ${tm('roles.mode')}`"
        @update:model-value="setMode(field, $event)"
      />
      <template v-if="modes[field] === 'custom'">
        <p class="text-caption mb-2">{{ tm("roles.customEmptyHint") }}</p>
        <v-select
          :model-value="definition[field]"
          :data-issue-path="`definition.${field}`"
          :items="catalog[field]"
          item-title="name"
          item-value="id"
          :multiple="true"
          chips
          closable-chips
          :label="tm(`roles.${field}`)"
          :aria-label="tm(`roles.${field}`)"
          :error-messages="fieldErrors(`definition.${field}`)"
          @update:model-value="
            definition[field] = $event;
            emit('change');
          "
        />
      </template>
    </v-expansion-panel-text>
  </v-expansion-panel>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useModuleI18n } from "@/i18n/composables";

const props = withDefaults(
  defineProps<{
    definition: Record<string, any>;
    modes: Record<string, string>;
    catalog: Record<string, any[]>;
    fieldErrors?: (path: string) => string[];
  }>(),
  { fieldErrors: () => [] },
);
const emit = defineEmits<{ (event: "change"): void }>();
const { tm } = useModuleI18n("features/agent-teams");
const fields = ["tools", "skills", "knowledge_bases"];
const modeItems = computed(() => [
  { title: tm("roles.inherit"), value: "inherit" },
  { title: tm("roles.override"), value: "custom" },
]);
function setMode(field: string, mode: string) {
  props.modes[field] = mode;
  props.definition[field] =
    mode === "inherit" ? null : props.definition[field] ?? [];
  emit("change");
}
</script>
