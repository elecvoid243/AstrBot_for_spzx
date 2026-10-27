<template>
  <section
    v-if="runner"
    class="runner-capabilities"
    data-testid="runner-capabilities"
  >
    <strong>{{ tm("runner.title") }}: {{ runner.label }}</strong>
    <p class="mt-2">{{ tm(`runner.recovery.${runner.recovery}`) }}</p>
    <p v-if="!runner.capabilities.includes('task_result')" class="mt-2">
      {{ tm("runner.taskResultRequired") }}
    </p>
    <div class="d-flex flex-wrap ga-2 mt-2">
      <v-chip
        v-for="capability in runner.capabilities"
        :key="capability"
        size="small"
        variant="tonal"
      >
        {{ tm(`runner.capabilities.${capability}`) }}
      </v-chip>
    </div>
    <p v-if="identity" class="mt-2">
      {{ tm("runner.identity") }}: {{ JSON.stringify(identity) }}
    </p>
    <p class="text-caption text-medium-emphasis mt-2">
      {{ tm("runner.boundaries") }}
    </p>
  </section>
</template>
<script setup lang="ts">
import { useModuleI18n } from "@/i18n/composables";
defineProps<{
  runner?: { label: string; recovery: string; capabilities: string[] };
  identity?: Record<string, string> | null;
}>();
const { tm } = useModuleI18n("features/agent-teams");
</script>
<style scoped>
.runner-capabilities {
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
