<template>
  <section class="fork-comparison mb-4 pa-4" data-testid="debug-fork-comparison">
    <div class="d-flex flex-wrap align-center justify-space-between ga-2 mb-2">
      <h3 class="text-h6">{{ tm("debugFork.title") }}</h3>
      <v-btn variant="text" :to="sourceLink" data-testid="debug-fork-source">{{ tm("debugFork.openSource") }}</v-btn>
    </div>
    <p class="font-weight-medium">
      {{ source.workflow_name }} · {{ version }} · {{ source.node_name }}
    </p>
    <p class="text-caption text-medium-emphasis mb-3">
      <span v-if="source.iteration != null">{{ tm("loops.iteration") }} {{ source.iteration }} · </span>
      {{ tm("v2.attempt") }} {{ source.attempt_id }}
    </p>
    <WorkflowDebugWritePreview
      v-if="task?.state?.debug_write_preview"
      :key="task.state.debug_write_preview.record_id"
      :preview="task.state.debug_write_preview"
      :cancelled="task.status === 'cancelled'"
    />
    <div class="fork-results mb-4">
      <div v-for="{ title, data } in results" :key="title" class="fork-result pa-3">
        <h4 class="text-subtitle-1 font-weight-bold">{{ tm(title) }}</h4>
        <p class="text-body-2 text-medium-emphasis">{{ getRaw(`v2.states.${data.status}`) ?? data.status }}</p>
        <p v-if="data.reason" class="text-body-2 mt-2">{{ data.reason }}</p>
        <pre v-if="data.result != null" class="mt-2">{{ JSON.stringify(data.result, null, 2) }}</pre>
      </div>
    </div>
    <WorkflowDebugForkInputs :inputs="comparison.inputs" />
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import type { WorkflowDebugForkComparison, WorkflowDebugWritePreview as DebugWritePreview } from "@/api/generated/openapi-v1";
import { useModuleI18n } from "@/i18n/composables";
import WorkflowDebugForkInputs from "./WorkflowDebugForkInputs.vue";
import WorkflowDebugWritePreview from "./WorkflowDebugWritePreview.vue";

const props = defineProps<{
  comparison: WorkflowDebugForkComparison;
  task?: { status: string; state?: { reason?: string; debug_write_preview?: DebugWritePreview }; result?: unknown };
}>();
const { tm, getRaw } = useModuleI18n("features/agent-teams");
const route = useRoute();
const source = computed(() => props.comparison.source);
const version = computed(() => source.value.draft_revision == null
  ? tm("debugFork.version", { version: source.value.asset_version ?? '' })
  : tm("debugFork.revision", { revision: source.value.draft_revision }),
);
const sourceLink = computed(() => {
  const { root_run_id, task_run_id, attempt_id } = source.value;
  const query = { debug_task: task_run_id, debug_attempt: attempt_id };
  return route.name === "ChatRunConversation"
    ? { name: "ChatRunConversation", params: { runId: root_run_id }, query }
    : { name: "AgentTeams", query: { ...query, workflow_run: root_run_id } };
});
const results = computed(() => [
  { title: "debugFork.originalResult", data: props.comparison.original },
  { title: "debugFork.forkResult", data: {
    status: props.task?.status ?? "pending",
    reason: props.task?.state?.reason,
    result: props.task?.result,
  } },
]);
</script>

<style scoped>
.fork-comparison {
  border: 1px solid rgba(var(--v-theme-primary), 0.28);
  border-radius: 8px;
  overflow-wrap: anywhere;
}
.fork-results { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.fork-result { border-radius: 6px; background: rgba(var(--v-theme-on-surface), 0.05); }
.fork-result pre { max-height: 14rem; overflow: auto; white-space: pre-wrap; }
@media (max-width: 600px) { .fork-results { grid-template-columns: 1fr; } }
</style>
