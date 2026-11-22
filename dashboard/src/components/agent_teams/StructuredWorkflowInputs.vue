<template>
  <v-textarea
    :model-value="modelValue"
    :label="tm('graph.inputValuesJson')"
    :hint="tm('graph.inputValuesHint')"
    persistent-hint
    spellcheck="false"
    rows="4"
    data-testid="structured-input-values"
    @update:model-value="emit('update:modelValue', $event)"
  />
  <details class="mb-4">
    <summary>{{ tm("graph.input_contract") }}</summary>
    <pre class="text-caption">{{ JSON.stringify(contract, null, 2) }}</pre>
  </details>
</template>

<script lang="ts">
export function parseWorkflowInputs(text: string): Record<string, unknown> {
  const values: unknown = JSON.parse(text, (_key, value) => {
    if (typeof value === "number" && !Number.isFinite(value))
      throw new Error("Use finite JSON numbers.");
    return value;
  });
  if (values === null || typeof values !== "object" || Array.isArray(values))
    throw new Error("Provide a JSON object.");
  return values as Record<string, unknown>;
}
</script>

<script setup lang="ts">
import type { WorkflowPortContract } from "@/api/generated/openapi-v1";
import { useModuleI18n } from "@/i18n/composables";

defineProps<{ contract: WorkflowPortContract; modelValue: string }>();
const emit = defineEmits<{ (event: "update:modelValue", value: string): void }>();
const { tm } = useModuleI18n("features/agent-teams");
</script>
