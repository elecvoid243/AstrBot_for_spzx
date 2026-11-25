<template>
  <div v-for="port in ports" :key="port.name" class="mb-2">
    <v-select
      :model-value="choices.get(port.name) ?? ''"
      @update:model-value="value => choices.set(port.name, value)"
      :items="[
        { title: tm('debugFork.automaticBinding'), value: '' },
        ...(port.available_sources ?? []).map(name => ({
          title: tm('debugFork.recordedBinding', { name }), value: 'source:' + name,
        })),
        ...(port.kind === 'reference' ? [] : [{ title: tm('debugFork.testBinding'), value: 'test' }]),
      ]"
      :label="tm('debugFork.bindPort', { name: port.name })"
      variant="outlined"
      density="compact"
      hide-details
      :disabled="disabled"
      :data-testid="'debug-fork-bind-' + port.name"
    />
    <v-textarea
      v-if="choices.get(port.name) === 'test'"
      :model-value="values.get(port.name)"
      @update:model-value="value => values.set(port.name, value)"
      :label="tm(port.kind === 'text' ? 'debugFork.replacementText' : 'debugFork.replacementJson')"
      variant="outlined"
      rows="2"
      class="mt-2"
      :disabled="disabled"
      :error-messages="invalidPort === port.name ? [tm('debugFork.invalidJson')] : []"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, watch } from "vue";
import type { WorkflowDebugForkBindings, WorkflowDebugForkPreview } from "@/api/generated/openapi-v1";
import { useModuleI18n } from "@/i18n/composables";

const props = defineProps<{ inputs: WorkflowDebugForkPreview["inputs"]; disabled: boolean }>();
const emit = defineEmits<{ (event: "update", value: WorkflowDebugForkBindings | undefined): void }>();
const { tm } = useModuleI18n("features/agent-teams");
const choices = reactive(new Map<string, string>());
const values = reactive(new Map<string, string>());
const ports = computed(() => props.inputs.filter(port =>
  port.available_sources && (port.kind !== "reference" || port.available_sources.length),
));
const bindingState = computed(() => {
  const bindings: WorkflowDebugForkBindings = Object.create(null);
  for (const port of ports.value) {
    const choice = choices.get(port.name);
    if (!choice) continue;
    if (choice.startsWith("source:")) {
      bindings[port.name] = { source_port: choice.slice("source:".length) };
      continue;
    }
    const text = values.get(port.name) ?? "";
    try {
      const value = port.kind === "text" ? text : JSON.parse(text, (_key, item) => {
        if (typeof item === "number" && !Number.isFinite(item)) {
          throw new SyntaxError("Expected a finite JSON number");
        }
        return item;
      });
      bindings[port.name] = { value };
    } catch {
      return { invalid: port.name };
    }
  }
  return { bindings };
});
const invalidPort = computed(() => bindingState.value.invalid);
watch(bindingState, state => emit("update", state.bindings), { immediate: true });
</script>
