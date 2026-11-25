<template>
  <div class="run-canvas-context">
    <label v-if="scopes.length > 1">
      {{ tm("child.scope") }}
      <select v-model="scopeId" data-testid="inspection-scope" @change="roundKey = ''">
        <option v-for="item in scopes" :key="item.run_id" :value="item.run_id">
          {{ item.snapshot.definition.name }} · {{ item.run_id.slice(0, 8) }}
        </option>
      </select>
    </label>
    <label v-if="rounds.length">
      {{ tm("loops.iteration") }}
      <select v-model="roundKey" data-testid="inspection-round">
        <option value="">{{ tm("inspection.currentRounds") }}</option>
        <option v-for="round in rounds" :key="round.key" :value="round.key">
          {{ round.name }} · {{ tm("loops.iteration") }} {{ round.iteration }}
        </option>
      </select>
    </label>
  </div>
  <WorkflowGraphCanvas
    :key="scope.run_id"
    :definition="scope.runtime_definition ?? scope.snapshot.definition"
    :tasks="tasks"
    :child-assets="scope.snapshot.child_assets ?? {}"
    :inspection="{ rootId: run.snapshot.root_run_id ?? run.run_id, scopeId: scope.run_id, signal, taskId: focus?.taskId, attemptId: focus?.attemptId, loopStatuses }"
    readonly
    @select-task="nodeId => nodeId && emit('select', tasks.find((task: any) => task.node_id === nodeId)?.run_id ?? nodeId)"
  />
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import WorkflowGraphCanvas from "./WorkflowGraphCanvas.vue";

const props = defineProps<{
  run: any;
  signal?: AbortSignal;
  selectedTaskId?: string | null;
  focus?: { taskId: string; attemptId: string } | null;
}>();
const emit = defineEmits<{ select: [taskId: string] }>();
const { tm } = useModuleI18n("features/agent-teams");
const scopes = computed(() => [
  props.run,
  ...(props.run.scopes ?? []).filter((item: any) => Array.isArray(item.snapshot.definition?.tasks)),
]);
const scopeId = ref(props.run.run_id);
const scope = computed(() => scopes.value.find((item) => item.run_id === scopeId.value) ?? props.run);
const loopStatuses = computed<Record<string, string>>(() => Object.fromEntries(
  (scope.value.loops ?? []).map((loop: any) => [loop.loop_id, loop.status]),
));
const roundKey = ref("");
const rounds = computed(() =>
  (scope.value.loops ?? []).flatMap((loop: any) => loop.iterations.map((round: any) => ({
    ...round,
    key: `${loop.loop_id}:${round.iteration}`,
    name: scope.value.snapshot.definition.loops?.find((item: any) => item.id === loop.loop_id)?.name ?? loop.loop_id,
  }))),
);
const tasks = computed(() => {
  const selected = rounds.value.find((round: any) => round.key === roundKey.value);
  if (!selected) return scope.value.tasks;
  const byNode = new Map(selected.tasks.map((task: any) => [task.node_id, task]));
  return scope.value.tasks.map((task: any) => byNode.get(task.node_id) ?? task);
});

watch([() => props.run.run_id, () => props.focus?.taskId ?? props.selectedTaskId], ([rootId, taskId]) => {
  const selectedScope = scopes.value.find((item) =>
    item.tasks?.some((task: any) => task.run_id === taskId) ||
    item.loops?.some((loop: any) => loop.iterations.some((round: any) =>
      round.tasks.some((task: any) => task.run_id === taskId),
    )),
  );
  scopeId.value = selectedScope?.run_id ?? rootId;
  const selectedRound = rounds.value.find((round: any) => round.tasks.some((task: any) => task.run_id === taskId));
  roundKey.value = selectedRound?.key ?? "";
}, { immediate: true });
</script>

<style scoped>
.run-canvas-context {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-bottom: 8px;
}
label {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 0.875rem;
}
select {
  min-width: 0;
  max-width: 100%;
  appearance: auto;
  color-scheme: light dark;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.3);
  border-radius: 6px;
  padding: 4px 8px;
}
</style>
