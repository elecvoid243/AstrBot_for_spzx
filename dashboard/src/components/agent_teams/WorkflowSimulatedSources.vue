<template>
  <details v-if="sources?.length" class="text-caption my-2" data-testid="workflow-simulated-sources">
    <summary>{{ tm("debugWrite.derived") }}</summary>
    <ul class="ps-4">
      <li v-for="source in sources" :key="source.record_id" class="simulation-source">
        {{ tm("debugWrite.call") }} {{ source.record_id }} ·
        {{ tm("progress.task") }} {{ source.task_run_id }} ·
        {{ tm("v2.attempt") }} {{ source.attempt_id }}
      </li>
    </ul>
  </details>
</template>

<script setup lang="ts">
import type { WorkflowSimulatedSource } from "@/api/generated/openapi-v1";
import { useModuleI18n } from "@/i18n/composables";

defineProps<{ sources?: WorkflowSimulatedSource[] }>();
const { tm } = useModuleI18n("features/agent-teams");
</script>

<style scoped>
.simulation-source { overflow-wrap: anywhere; }
</style>
