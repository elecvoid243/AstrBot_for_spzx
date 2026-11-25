<template>
  <div class="d-flex flex-wrap align-center ga-2 mb-4" data-testid="debug-write-status">
    <span role="status">{{ status }}</span>
    <v-btn variant="tonal" data-testid="open-debug-write-preview" @click="open = true">
      {{ tm("debugWrite.title") }}
    </v-btn>
  </div>
  <v-dialog v-model="open" max-width="680">
    <v-card data-testid="debug-write-preview">
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{ tm("debugWrite.title") }}</v-card-title>
      <v-card-text>
        <v-alert type="info" variant="tonal" class="mb-4">{{ status }}</v-alert>
        <dl class="write-details">
          <dt>{{ tm("debugWrite.operation") }}</dt>
          <dd>{{ preview.operation }}</dd>
          <dt>{{ tm("debugWrite.target") }}</dt>
          <dd>{{ preview.target ?? tm("debugWrite.unknownTarget") }}</dd>
          <dt>{{ tm("debugWrite.parameters") }}</dt>
          <dd><pre>{{ JSON.stringify(preview.parameters, null, 2) }}</pre></dd>
        </dl>
        <details class="mt-4" data-testid="debug-write-history">
          <summary>{{ tm("debugWrite.history") }}</summary>
          <div v-for="receipt in preview.historical_receipts" :key="receipt.record_id" class="mt-3">
            <p>{{ tm(`debugWrite.receiptStates.${receipt.status}`) }}</p>
            <pre v-if="receipt.results.length">{{ JSON.stringify(receipt.results, null, 2) }}</pre>
            <p v-else>{{ tm("debugWrite.noReceipt") }}</p>
          </div>
          <p v-if="!preview.historical_receipts.length" class="mt-2">{{ tm("debugWrite.noReceipt") }}</p>
        </details>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="open = false">{{ tm("v2.close") }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import type { WorkflowDebugWritePreview } from "@/api/generated/openapi-v1";
import { useModuleI18n } from "@/i18n/composables";

const props = defineProps<{ preview: WorkflowDebugWritePreview; cancelled: boolean }>();
const { tm } = useModuleI18n("features/agent-teams");
const open = ref(false);
const status = computed(() => {
  if (props.cancelled) return tm("debugWrite.cancelled");
  if (props.preview.status === "unavailable" && props.preview.target) return tm("debugWrite.invalidParameters");
  return tm(`debugWrite.${props.preview.status}`);
});
</script>

<style scoped>
.write-details { overflow-wrap: anywhere; }
.write-details dt { font-weight: 600; }
.write-details dd + dt { margin-top: 12px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 16rem; overflow: auto; }
</style>
