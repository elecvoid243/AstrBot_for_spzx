<template>
  <section class="fork-comparison mb-4 pa-4" data-testid="debug-fork-comparison">
    <div class="d-flex flex-wrap align-center justify-space-between ga-2 mb-2">
      <h3 class="text-h6">{{ tm("debugFork.title") }}</h3>
      <div class="d-flex flex-wrap ga-2">
        <v-btn v-if="links.parent" variant="text" :to="links.parent" data-testid="debug-fork-parent">{{ tm("debugFork.openParent") }}</v-btn>
        <v-btn variant="text" :to="links.source" data-testid="debug-fork-source">{{ tm("debugFork.openSource") }}</v-btn>
      </div>
    </div>
    <p v-if="source.parent_call" class="text-body-2 mb-1" data-testid="debug-fork-parent-context">
      {{ tm("debugFork.parentCall", { name: source.parent_call.node_name }) }}
      <span v-if="source.parent_call.iteration != null">· {{ tm("loops.iteration") }} {{ source.parent_call.iteration }}</span>
    </p>
    <p class="font-weight-medium">
      {{ source.workflow_name }} · {{ version }} · {{ source.node_name }}
    </p>
    <p class="text-caption text-medium-emphasis mb-3">
      <span v-if="source.iteration != null">{{ tm("loops.iteration") }} {{ source.iteration }} · </span>
      {{ tm("v2.attempt") }} {{ source.attempt_id }}
    </p>
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
import type { WorkflowDebugForkComparison } from "@/api/generated/openapi-v1";
import { useModuleI18n } from "@/i18n/composables";
import WorkflowDebugForkInputs from "./WorkflowDebugForkInputs.vue";

const props = defineProps<{
  comparison: WorkflowDebugForkComparison;
  task?: { status: string; state?: { reason?: string }; result?: unknown };
}>();
const { tm, getRaw } = useModuleI18n("features/agent-teams");
const route = useRoute();
const source = computed(() => props.comparison.source);
const version = computed(() => source.value.draft_revision == null
  ? tm("debugFork.version", { version: source.value.asset_version ?? '' })
  : tm("debugFork.revision", { revision: source.value.draft_revision }),
);
const links = computed(() => Object.fromEntries(
  Object.entries({ source: source.value, parent: source.value.parent_call }).map(([kind, location]) => {
    if (!location) return [kind, undefined];
    const rootId = source.value.root_run_id;
    const query = {
      debug_task: location.task_run_id,
      debug_attempt: location.attempt_id,
      ...(kind === "parent" ? { debug_call: "1" } : {}),
    };
    return [
      kind,
      route.name === "ChatRunConversation"
        ? { name: "ChatRunConversation", params: { runId: rootId }, query }
        : { name: "AgentTeams", query: { ...query, workflow_run: rootId } },
    ];
  }),
));
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
