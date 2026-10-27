<template>
  <template v-if="issues?.length">
    <v-alert type="warning" variant="tonal" class="mb-3">
      {{ tm("migration.configure") }}
      <ul class="pl-5">
        <li v-for="(issue, index) in issues" :key="index">
          {{ issue.path }}: {{ issue.message }}
        </li>
      </ul>
    </v-alert>
    <v-checkbox
      :model-value="modelValue"
      :label="tm('migration.reviewed')"
      @update:model-value="$emit('update:modelValue', Boolean($event))"
    />
  </template>
</template>

<script setup lang="ts">
import { useModuleI18n } from "@/i18n/composables";

defineProps<{
  issues?: Record<string, unknown>[];
  modelValue: boolean;
}>();
defineEmits<{ "update:modelValue": [value: boolean] }>();
const { tm } = useModuleI18n("features/agent-teams");
</script>
