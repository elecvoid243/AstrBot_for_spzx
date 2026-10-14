<template>
  <section class="mb-4">
    <h4 class="text-subtitle-1 mb-2">{{ tm("usage.limits") }}</h4>
    <p class="text-body-2 text-medium-emphasis mb-3">
      {{ tm("usage.limitHint") }}
    </p>
    <div class="limit-fields">
      <v-text-field
        v-for="(maximum, key) in fields"
        :key="key"
        :model-value="modelValue?.[key] ?? ''"
        type="number"
        min="1"
        :max="maximum"
        :label="tm(`usage.${key}`)"
        :aria-label="tm(`usage.${key}`)"
        :data-testid="`limit-${key}`"
        :error="invalid(modelValue?.[key], maximum)"
        @update:model-value="update(key, $event)"
      />
    </div>
    <p class="text-caption text-medium-emphasis">{{ tm("usage.boundary") }}</p>
  </section>
</template>

<script setup lang="ts">
import { watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import type { RunLimits } from "@/api/generated/openapi-v1";
const props = defineProps<{ modelValue?: RunLimits | null }>();
const emit = defineEmits<{
  (event: "update:modelValue", value: RunLimits): void;
  (event: "validation", invalid: boolean): void;
}>();
const { tm } = useModuleI18n("features/agent-teams");
const fields = {
  max_steps: 100000,
  max_members: 10000,
  max_iterations: 10000,
  token_budget: 1000000000,
  max_output_tokens: 1000000,
};
function invalid(value: number | null | undefined, maximum: number) {
  return (
    value != null && (!Number.isInteger(value) || value < 1 || value > maximum)
  );
}
function update(key: keyof typeof fields, raw: string | number | null) {
  const value = { ...(props.modelValue ?? {}) };
  if (raw === "" || raw == null) delete value[key];
  else value[key] = Number(raw);
  emit("update:modelValue", value);
  emit(
    "validation",
    Object.entries(fields).some(([key, maximum]) =>
      invalid(value[key as keyof RunLimits], maximum),
    ),
  );
}
watch(
  () => props.modelValue,
  (value) => {
    emit(
      "validation",
      Object.entries(fields).some(([key, maximum]) =>
        invalid(value?.[key as keyof RunLimits], maximum),
      ),
    );
  },
  { immediate: true, deep: true },
);
</script>

<style scoped>
.limit-fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 180px), 1fr));
  gap: 12px;
}
</style>
