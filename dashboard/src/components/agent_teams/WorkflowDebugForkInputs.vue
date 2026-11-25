<template>
  <v-table density="compact" class="fork-inputs">
    <thead>
      <tr>
        <th scope="col">{{ tm("debugFork.input") }}</th>
        <th scope="col">{{ tm("debugFork.originalInput") }}</th>
        <th scope="col">{{ tm("debugFork.forkInput") }}</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="port in inputs" :key="port.name">
        <th scope="row">
          <div>{{ port.name }}</div>
          <div class="text-caption text-medium-emphasis">{{ tm(`debugFork.sources.${port.source}`, { name: port.source_port ?? "" }) }}</div>
          <div v-if="port.source !== 'mapped' && port.evidence?.artifact" class="text-caption font-weight-regular">
            {{ port.evidence.artifact.artifact_id }} / v{{ port.evidence.artifact.version }}
          </div>
        </th>
        <td>
          <pre v-if="'original' in port">{{ JSON.stringify(port.original, null, 2) }}</pre>
          <span v-else>—</span>
          <div v-if="port.original_evidence?.artifact" class="text-caption">
            {{ port.original_evidence.artifact.artifact_id }} / v{{ port.original_evidence.artifact.version }}
          </div>
        </td>
        <td>
          <pre v-if="port.changed || ('value' in port && !('original' in port))" data-testid="debug-fork-changed-value">{{ JSON.stringify(port.value, null, 2) }}</pre>
          <span v-else-if="'value' in port" class="text-medium-emphasis">{{ tm("debugFork.reused") }}</span>
          <span v-else>—</span>
          <div v-if="port.source === 'mapped' && port.evidence?.artifact" class="text-caption">
            {{ port.evidence.artifact.artifact_id }} / v{{ port.evidence.artifact.version }}
          </div>
        </td>
      </tr>
    </tbody>
  </v-table>
</template>

<script setup lang="ts">
import type { WorkflowDebugForkComparison } from "@/api/generated/openapi-v1";
import { useModuleI18n } from "@/i18n/composables";

defineProps<{ inputs: WorkflowDebugForkComparison["inputs"] }>();
const { tm } = useModuleI18n("features/agent-teams");
</script>

<style scoped>
.fork-inputs :deep(table) { table-layout: fixed; }
.fork-inputs th, .fork-inputs td { padding-block: 8px; vertical-align: top; overflow-wrap: anywhere; }
.fork-inputs pre { max-height: 10rem; overflow: auto; white-space: pre-wrap; }
</style>
