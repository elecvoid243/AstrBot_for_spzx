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
    <p v-if="scopeLabel" class="text-body-2">{{ tm(scopeLabel) }}</p>
    <p class="text-caption text-medium-emphasis mb-3">
      <span v-if="source.iteration != null">{{ tm("loops.iteration") }} {{ source.iteration }} · </span>
      {{ tm("v2.attempt") }} {{ source.attempt_id }}
    </p>
    <WorkflowDebugWritePreview
      v-for="{ preview, task } in writes"
      :key="preview.record_id"
      :preview="preview"
      :task="task"
      :active="active && task.status === 'blocked'"
      :busy="busy"
      @refresh="emit('refresh', run.run_id)"
    />
    <WorkflowDebugForkDraftDetails v-if="comparison.definition" :definition="comparison.definition" />
    <div class="fork-results mb-4">
      <div v-for="{ title, data } in results" :key="title" class="fork-result pa-3">
        <h4 class="text-subtitle-1 font-weight-bold">{{ tm(title) }}</h4>
        <p class="text-body-2 text-medium-emphasis">{{ getRaw(`v2.states.${data.status}`) ?? data.status }}</p>
        <p v-if="data.reason" class="text-body-2 mt-2">{{ data.reason }}</p>
        <WorkflowSimulatedSources :sources="data.simulated_sources" />
        <pre v-if="data.result != null" class="mt-2">{{ JSON.stringify(data.result, null, 2) }}</pre>
      </div>
    </div>
    <WorkflowDebugForkInputs :inputs="comparison.inputs" />
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import type { WorkflowDebugForkComparison, WorkflowDebugWritePreview as DebugWritePreview, WorkflowSimulatedSource } from "@/api/generated/openapi-v1";
import { useModuleI18n } from "@/i18n/composables";
import WorkflowDebugForkDraftDetails from "./WorkflowDebugForkDraftDetails.vue";
import WorkflowDebugForkInputs from "./WorkflowDebugForkInputs.vue";
import WorkflowSimulatedSources from "./WorkflowSimulatedSources.vue";
import WorkflowDebugWritePreview, { type DebugWriteTask } from "./WorkflowDebugWritePreview.vue";

const props = defineProps<{
  comparison: WorkflowDebugForkComparison;
  active: boolean;
  busy: boolean;
  run: {
    run_id: string;
    status: string;
    state?: { reason?: string; simulated_sources?: WorkflowSimulatedSource[] };
    result?: unknown;
    tasks?: RunTask[];
    loops?: RunLoop[];
    scopes?: { tasks?: RunTask[]; loops?: RunLoop[] }[];
  };
}>();
const emit = defineEmits<{ refresh: [rootId: string] }>();
type RunTask = DebugWriteTask & {
  state?: { reason?: string; debug_write_preview?: DebugWritePreview; simulated_sources?: WorkflowSimulatedSource[] };
  result?: unknown;
};
type RunLoop = { iterations: { tasks: RunTask[] }[] };
const { tm, getRaw } = useModuleI18n("features/agent-teams");
const route = useRoute();
const source = computed(() => props.comparison.source);
const scopeLabel = computed(() => props.comparison.scope === "child_workflow"
  ? "debugFork.childWorkflow"
  : props.comparison.scope === "downstream" ? "debugFork.downstream" : undefined);
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
      ...(kind === "parent" || props.comparison.scope === "child_workflow" ? { debug_call: "1" } : {}),
    };
    return [
      kind,
      route.name === "ChatRunConversation"
        ? { name: "ChatRunConversation", params: { runId: rootId }, query }
        : { name: "AgentTeams", query: { ...query, workflow_run: rootId } },
    ];
  }),
));
const writes = computed(() => {
  const records = new Map<string, { preview: DebugWritePreview; task: RunTask }>();
  for (const scope of [props.run, ...(props.run.scopes ?? [])]) {
    for (const task of [
      ...(scope.tasks ?? []),
      ...(scope.loops ?? []).flatMap(loop => loop.iterations.flatMap(round => round.tasks)),
    ]) {
      const preview = task.state?.debug_write_preview;
      if (preview && !records.has(preview.record_id)) records.set(preview.record_id, { preview, task });
    }
  }
  return [...records.values()];
});
const resultRun = computed(() => scopeLabel.value ? props.run : props.run.tasks?.[0]);
const results = computed(() => [
  { title: "debugFork.originalResult", data: props.comparison.original },
  { title: "debugFork.forkResult", data: {
    status: resultRun.value?.status ?? "pending",
    reason: resultRun.value?.state?.reason,
    result: resultRun.value?.result,
    simulated_sources: resultRun.value?.state?.simulated_sources,
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
