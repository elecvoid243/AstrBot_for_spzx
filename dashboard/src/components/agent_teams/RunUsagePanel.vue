<template>
  <section v-if="run.usage" class="usage-panel mb-4" data-testid="run-usage">
    <h3 class="text-subtitle-1 mb-2">{{ tm("usage.title") }}</h3>
    <div class="usage-values">
      <p data-testid="reported-tokens">
        {{ tm("usage.reported") }}:
        {{ run.usage.tokens?.reported ?? tm("usage.unknown") }}
      </p>
      <p>
        {{ tm("usage.estimated") }}:
        {{ run.usage.tokens?.estimated ?? tm("usage.unknown") }}
      </p>
      <p>
        {{ tm("usage.reserved") }}:
        {{ run.usage.tokens?.reserved ?? tm("usage.unknown") }}
      </p>
      <p>
        {{ tm("usage.unknownCalls") }}:
        {{ run.usage.tokens?.unknown_calls ?? tm("usage.unknown") }}
      </p>
    </div>
    <p class="text-body-2 mt-2">
      {{
        tm(
          run.usage.count_basis === "checkpoint"
            ? "usage.historicalSteps"
            : "usage.calls",
        )
      }}: {{ run.usage.steps_used ?? tm("usage.unknown") }}
    </p>
    <p
      v-if="run.usage.historical?.unmetered"
      class="text-body-2 mt-2"
      data-testid="historical-usage"
    >
      {{ tm("usage.historicalUnknown") }}
      {{ tm("usage.historicalResponses") }}:
      {{ run.usage.historical.model_responses }}.
    </p>
    <p v-if="run.usage.historical?.truncated" class="text-warning">{{ tm("usage.historicalTruncated") }}</p>
    <p v-for="(value, key) in run.usage.limits" :key="key" class="text-caption">
      {{ tm(`usage.${key}`) }}: {{ value ?? tm("usage.unlimited") }}
    </p>
    <div
      v-for="constraint in run.usage.constraints ?? []"
      :key="constraint.run_id"
      class="text-caption usage-identity"
    >
      {{ tm("usage.scope") }} {{ constraint.run_id }} ·
      {{ tm("graph.parallel") }}:
      {{ constraint.max_parallel ?? tm("usage.unlimited") }} ·
      {{ tm("child.maxDepth") }}:
      {{ constraint.max_depth ?? tm("usage.unlimited") }}
      <p v-for="(value, key) in constraint.limits ?? {}" :key="key">
        {{ tm(`usage.${key}`) }}: {{ value }}
      </p>
    </div>
    <p
      v-if="run.state?.limit_stop && !run.usage.stops?.length"
      class="text-body-2 mt-2"
      role="status"
    >
      {{ tm("usage.stopped") }}:
      {{ tm(`usage.${run.state.limit_stop.limit}`) }} ({{
        run.state.limit_stop.value
      }})
    </p>
    <p
      v-for="(stop, index) in run.usage.stops ?? []"
      :key="index"
      class="text-body-2 mt-2 usage-identity"
      role="status"
    >
      {{ tm("usage.stopped") }}: {{ tm(`usage.${stop.limit}`) }} ({{
        stop.value
      }}) · {{ tm("usage.task") }} {{ stop.task_run_id }}
    </p>
    <p class="text-caption text-medium-emphasis mt-2">
      {{ tm("usage.boundary") }}
    </p>
    <details v-if="run.usage.ledger?.length || older.length" class="mt-3">
      <summary>
        {{ tm("usage.details") }} ({{ visibleLedger.length }})
      </summary>
      <article
        v-for="receipt in visibleLedger"
        :key="receipt.receipt_id"
        class="usage-receipt mt-3"
      >
        <strong
          >{{ receipt.name ?? receipt.kind }} ·
          {{ tm(`usage.quality.${receipt.quality}`) }}</strong
        >
        <p>
          {{ tm("usage.tokens") }}:
          {{
            receipt.quality === "reported"
              ? receipt.reported_tokens
              : receipt.quality === "estimated"
              ? receipt.estimated_tokens
              : tm("usage.unknown")
          }}
        </p>
        <p
          v-if="
            receipt.quality === 'unknown' && receipt.estimated_tokens != null
          "
        >
          {{ tm("usage.estimated") }}: {{ receipt.estimated_tokens }}
        </p>
        <p class="usage-identity">
          {{ tm("usage.scope") }}: {{ receipt.scope_id }} ·
          {{ tm("usage.task") }}: {{ receipt.run_id }}
        </p>
        <p class="usage-identity">
          {{ tm("v2.attempt") }}: {{ receipt.attempt_id }}
        </p>
        <p class="usage-identity">
          {{ tm("usage.invocation") }}: {{ receipt.invocation_id }} ·
          {{ tm("usage.delivery") }}: {{ receipt.receipt_id }}
        </p>
      </article>
      <v-btn v-if="nextBefore" variant="tonal" :loading="loading" @click="loadOlder">{{ tm("usage.loadOlder") }}</v-btn>
      <v-btn v-if="truncated" variant="text" @click="resetHistory">{{ tm("progress.reloadLatest") }}</v-btn>
      <p v-if="truncated" class="text-warning">{{ tm("progress.historyWindow") }}</p>
      <p v-if="error" role="alert">{{ error }}</p>
    </details>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { extractApiError } from "@/utils/extractApiError";
const props = defineProps<{ run: any }>();
const { tm } = useModuleI18n("features/agent-teams");
const older = ref<any[]>([]);
const next = ref<string | null | undefined>(undefined);
const loading = ref(false);
const error = ref("");
const truncated = ref(false);
const nextBefore = computed(() => next.value === undefined ? props.run.usage?.ledger_before : next.value);
const visibleLedger = computed(() => {
  const entries = new Map([...older.value, ...(props.run.usage?.ledger ?? [])].map((receipt: any) => [receipt.receipt_id, receipt]));
  return [...entries.values()].sort((a: any, b: any) => String(a.created_at ?? "").localeCompare(String(b.created_at ?? "")) || a.receipt_id.localeCompare(b.receipt_id)).slice(0, 512);
});
watch(() => props.run.run_id, () => { older.value = []; next.value = undefined; error.value = ""; truncated.value = false; });
watch(() => older.value.length + (props.run.usage?.ledger?.length ?? 0), (count) => {
  if (count > 512) truncated.value = true;
});
async function loadOlder() {
  if (!nextBefore.value || loading.value) return;
  const taskId = props.run.run_id;
  const rootId = props.run.control?.root_run_id ?? taskId;
  loading.value = true;
  error.value = "";
  try {
    const page = await workflowAssetsApi.runHistoryPage(rootId, taskId, "usage", nextBefore.value);
    if (props.run.run_id !== taskId) return;
    const entries = new Map([...page.items, ...older.value].map((receipt: any) => [receipt.receipt_id, receipt]));
    const merged = [...entries.values()].sort((a: any, b: any) => String(a.created_at ?? "").localeCompare(String(b.created_at ?? "")) || a.receipt_id.localeCompare(b.receipt_id));
    if (merged.length + (props.run.usage?.ledger?.length ?? 0) > 512) truncated.value = true;
    older.value = merged.slice(0, 512);
    next.value = page.next_before;
  } catch (cause) {
    error.value = extractApiError(cause, tm("errors.operationFailed")).message;
  } finally {
    loading.value = false;
  }
}
function resetHistory() {
  older.value = [];
  next.value = undefined;
  truncated.value = false;
  error.value = "";
}
</script>

<style scoped>
.usage-panel {
  padding: 16px;
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 8px;
  min-width: 0;
}
.usage-values {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
}
.usage-identity {
  overflow-wrap: anywhere;
  font-size: 12px;
}
.usage-receipt {
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  padding-top: 8px;
}
summary {
  cursor: pointer;
}
</style>
