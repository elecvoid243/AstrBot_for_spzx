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
        <v-alert v-if="error" type="error" variant="tonal" class="mb-4" role="alert">
          {{ error.message }}
          <ul v-if="error.fields.length" class="ps-4">
            <li v-for="field in error.fields" :key="field.path + field.message">
              <code>{{ field.path }}</code> · {{ field.message }}
            </li>
          </ul>
        </v-alert>
        <dl class="write-details">
          <dt>{{ tm("debugWrite.call") }}</dt>
          <dd>{{ preview.record_id }}</dd>
          <dt>{{ tm("debugWrite.operation") }}</dt>
          <dd>{{ preview.operation }}</dd>
          <dt>{{ tm("debugWrite.target") }}</dt>
          <dd>{{ preview.target ?? tm("debugWrite.unknownTarget") }}</dd>
          <dt>{{ tm("debugWrite.parameters") }}</dt>
          <dd><pre>{{ JSON.stringify(preview.parameters, null, 2) }}</pre></dd>
        </dl>
        <section v-if="preview.authorized_by" class="mt-4" data-testid="debug-write-current">
          <h3 class="text-subtitle-1 font-weight-bold">{{ tm("debugWrite.current") }}</h3>
          <p>{{ tm("debugWrite.authorizedBy", { name: preview.authorized_by }) }}</p>
          <p v-if="call?.started_execution_id">{{ tm("debugWrite.executor", { id: call.started_execution_id }) }}</p>
          <p v-if="uncertain">{{ tm("debugWrite.unknown") }}</p>
          <pre v-if="hasReceipt">{{ JSON.stringify(call?.raw_results, null, 2) }}</pre>
          <p v-else>{{ tm("debugWrite.noReceipt") }}</p>
        </section>
        <section v-if="preview.simulated_receipt" class="mt-4" data-testid="debug-write-simulated">
          <p>{{ tm("debugWrite.submittedBy", { name: preview.simulated_receipt.submitted_by }) }}</p>
          <pre>{{ JSON.stringify(preview.simulated_receipt.result, null, 2) }}</pre>
        </section>
        <section v-if="editing && canDecide" class="mt-4">
          <v-textarea
            v-model="receiptJson"
            :label="tm('debugWrite.testValue')"
            :hint="tm(preview.output_contract ? 'debugWrite.testValueHint' : 'debugWrite.textValueHint')"
            persistent-hint
            auto-grow
            rows="3"
            :max-rows="8"
            data-testid="debug-write-test-value"
          />
          <v-btn
            variant="text"
            class="mt-1"
            :disabled="submitting"
            @click="editing = false; error = undefined"
          >{{ tm("debugWrite.backToPreview") }}</v-btn>
          <details v-if="preview.output_contract" class="mt-2">
            <summary>{{ tm("debugWrite.outputContract") }}</summary>
            <pre>{{ JSON.stringify(preview.output_contract, null, 2) }}</pre>
          </details>
        </section>
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
        <v-btn
          v-if="canDecide"
          variant="tonal"
          :disabled="!active || busy || submitting"
          :loading="submitting && editing"
          data-testid="simulate-debug-write"
          @click="editing ? submit(true) : editing = true"
        >{{ tm(editing ? "debugWrite.submitTestValue" : "debugWrite.simulate") }}</v-btn>
        <v-btn
          v-if="canDecide && !editing"
          variant="tonal"
          color="primary"
          :disabled="!active || busy || submitting"
          :loading="submitting"
          data-testid="confirm-debug-write"
          @click="submit(false)"
        >{{ tm("debugWrite.execute") }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script lang="ts">
export interface DebugWriteTask {
  run_id: string;
  status: string;
  tool_calls?: {
    record_id: string;
    status: string;
    started_execution_id?: string | null;
    raw_results?: { isError?: boolean }[];
  }[];
}
</script>

<script setup lang="ts">
import { computed, ref } from "vue";
import type { WorkflowDebugWritePreview } from "@/api/generated/openapi-v1";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError, type ExtractedApiError } from "@/utils/extractApiError";

const props = defineProps<{
  preview: WorkflowDebugWritePreview;
  task: DebugWriteTask;
  active: boolean;
  busy: boolean;
}>();
const emit = defineEmits<{ refresh: [] }>();
const { tm } = useModuleI18n("features/agent-teams");
const open = ref(false);
const submitting = ref(false);
const accepted = ref<"execute" | "simulate">();
const editing = ref(false);
const receiptJson = ref("");
const error = ref<ExtractedApiError>();
const canDecide = computed(() =>
  props.preview.status === "pending" && !!props.preview.preview_token &&
  !props.preview.authorized_by && !props.preview.simulated_receipt && !accepted.value &&
  props.task.status !== "cancelled",
);
const call = computed(() => props.task.tool_calls?.find(({ record_id }) => record_id === props.preview.record_id));
const hasReceipt = computed(() =>
  ["result_saved", "applied"].includes(call.value?.status ?? "") && !!call.value?.raw_results?.length,
);
const uncertain = computed(() =>
  call.value?.status === "unknown" || call.value?.status === "started" && props.task.status !== "running",
);
const status = computed(() => {
  if (props.task.status === "cancelled") return tm("debugWrite.cancelled");
  if (props.preview.simulated_receipt || accepted.value === "simulate") return tm("debugWrite.simulated");
  if (uncertain.value) return tm("debugWrite.unknown");
  if (hasReceipt.value) return tm("debugWrite.receiptSaved");
  if (props.preview.authorized_by || accepted.value) return tm("debugWrite.authorized");
  if (props.preview.status === "unavailable" && props.preview.target) return tm("debugWrite.invalidParameters");
  return tm(`debugWrite.${props.preview.status}`);
});

async function submit(simulate: boolean) {
  const { record_id, preview_token } = props.preview;
  if (!preview_token || !canDecide.value || !props.active || props.busy || submitting.value) return;
  error.value = undefined;
  let result: unknown;
  if (simulate) {
    try {
      result = JSON.parse(receiptJson.value);
    } catch {
      error.value = { message: tm("debugWrite.invalidJson"), fields: [] };
      return;
    }
  }
  submitting.value = true;
  try {
    if (simulate) {
      await workflowAssetsApi.simulateDebugWrite(props.task.run_id, record_id, { preview_token, result });
    } else {
      await workflowAssetsApi.confirmDebugWrite(props.task.run_id, record_id, { preview_token });
    }
    accepted.value = simulate ? "simulate" : "execute";
  } catch (err) {
    error.value = extractApiError(err, tm("errors.operationFailed"));
  } finally {
    submitting.value = false;
    emit("refresh");
  }
}
</script>

<style scoped>
.write-details { overflow-wrap: anywhere; }
.write-details dt { font-weight: 600; }
.write-details dd + dt { margin-top: 12px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 16rem; overflow: auto; }
</style>
