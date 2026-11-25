<template>
  <div class="run-canvas-context">
    <label v-if="scopes.length > 1">
      {{ tm("child.scope") }}
      <select v-model="scopeId" data-testid="inspection-scope" @change="roundKey = ''; clearCall()">
        <option v-for="item in scopes" :key="item.run_id" :value="item.run_id">
          {{ item.snapshot.definition.name }} · {{ item.run_id.slice(0, 8) }}
        </option>
      </select>
    </label>
    <label v-if="rounds.length">
      {{ tm("loops.iteration") }}
      <select v-model="roundKey" data-testid="inspection-round" @change="clearCall">
        <option value="">{{ tm("inspection.currentRounds") }}</option>
        <option v-for="round in rounds" :key="round.key" :value="round.key">
          {{ round.name }} · {{ tm("loops.iteration") }} {{ round.iteration }}
        </option>
      </select>
    </label>
  </div>
  <div v-if="callTask && selectedCall" class="d-flex align-center flex-wrap ga-2 mb-2" data-testid="failed-child-call">
    <span class="text-body-2">{{ callTask.snapshot.definition.name }}</span>
    <v-btn v-if="callTask.state.attempt_id !== selectedCall.attemptId || ['blocked', 'failed'].includes(callTask.status)"
      size="small" variant="tonal" :loading="loading" data-testid="debug-failed-child" @click="openFailures">
      {{ tm("debugFork.openFailedChild") }}
    </v-btn>
    <v-btn size="small" variant="text" @click="emit('select', callTask.run_id)">{{ tm("debugFork.viewTask") }}</v-btn>
  </div>
  <v-alert v-if="callError" type="error" variant="tonal" class="mb-2">{{ callError }}</v-alert>
  <WorkflowGraphCanvas
    :key="scope.run_id"
    data-testid="workflow-run-canvas"
    :definition="scope.runtime_definition ?? scope.snapshot.definition"
    :tasks="tasks"
    :child-assets="scope.snapshot.child_assets ?? {}"
    :inspection="{ rootId, scopeId: scope.run_id, signal, taskId: displayFocus?.taskId, attemptId: displayFocus?.attemptId, focusNode: displayFocus?.call, loopStatuses }"
    readonly
    @select-task="selectCanvasTask"
  />
  <WorkflowDebugForkDialog
    v-if="candidate"
    :root-id="rootId"
    :attempt="{ scope_run_id: candidate.scope_run_id, task_run_id: candidate.task_run_id, attempt_id: candidate.attempt_id }"
    :candidates="candidates"
    @select-candidate="selectFailure"
    @close="candidate = undefined"
  />
</template>

<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from "vue";
import type { WorkflowDebugForkLocation } from "@/api/generated/openapi-v1";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { useModuleI18n } from "@/i18n/composables";
import { extractApiError } from "@/utils/extractApiError";
import WorkflowDebugForkDialog from "./WorkflowDebugForkDialog.vue";
import WorkflowGraphCanvas from "./WorkflowGraphCanvas.vue";

type CanvasFocus = { taskId: string; attemptId: string; call?: boolean };

const props = defineProps<{
  run: any;
  signal?: AbortSignal;
  selectedTaskId?: string | null;
  focus?: CanvasFocus | null;
}>();
const emit = defineEmits<{ select: [taskId: string] }>();
const { tm } = useModuleI18n("features/agent-teams");
const rootId = computed(() => props.run.snapshot.root_run_id ?? props.run.run_id);
const displayFocus = ref<CanvasFocus>();
const selectedCall = ref<{ taskId: string; attemptId: string }>();
const callTask = computed(() => tasks.value.find((task: any) => task.run_id === selectedCall.value?.taskId));
const callError = ref("");
const loading = ref(false);
const candidates = ref<WorkflowDebugForkLocation[]>([]);
const candidate = ref<WorkflowDebugForkLocation>();
let callRead: AbortController | undefined;
onScopeDispose(() => callRead?.abort());

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

function focusTask(taskId: string | null | undefined) {
  const selectedScope = scopes.value.find((item) =>
    item.tasks?.some((task: any) => task.run_id === taskId) ||
    item.loops?.some((loop: any) => loop.iterations.some((round: any) =>
      round.tasks.some((task: any) => task.run_id === taskId),
    )),
  );
  scopeId.value = selectedScope?.run_id ?? props.run.run_id;
  const selectedRound = rounds.value.find((round: any) => round.tasks.some((task: any) => task.run_id === taskId));
  roundKey.value = selectedRound?.key ?? "";
}

function clearCall() {
  callRead?.abort();
  loading.value = false;
  selectedCall.value = undefined;
  callError.value = "";
  displayFocus.value = undefined;
}

watch([() => props.focus, () => props.selectedTaskId], ([focus, taskId]) => {
  clearCall();
  focusTask(focus?.taskId ?? taskId);
  displayFocus.value = focus ?? undefined;
  if (focus?.call && tasks.value.some((task: any) => task.run_id === focus.taskId && task.snapshot.engine === "workflow_call_v2")) {
    selectedCall.value = { taskId: focus.taskId, attemptId: focus.attemptId };
  }
}, { immediate: true });

function selectCanvasTask(nodeId: string | null) {
  clearCall();
  if (!nodeId) return;
  const task = tasks.value.find((item: any) => item.node_id === nodeId);
  if (task?.snapshot.engine === "workflow_call_v2" &&
      ["blocked", "failed"].includes(task.status) && task.state?.child_scope_run_id) {
    selectedCall.value = { taskId: task.run_id, attemptId: task.state.attempt_id };
  } else {
    emit("select", task?.run_id ?? nodeId);
  }
}

function selectFailure(failure: WorkflowDebugForkLocation) {
  candidate.value = failure;
  focusTask(failure.task_run_id);
  displayFocus.value = { taskId: failure.task_run_id, attemptId: failure.attempt_id };
}

async function openFailures() {
  const call = selectedCall.value;
  if (!callTask.value || !call || loading.value) return;
  loading.value = true;
  callError.value = "";
  const controller = new AbortController();
  callRead = controller;
  try {
    const failures = await workflowAssetsApi.failedChildDebugAttempts(rootId.value, {
      scope_run_id: scope.value.run_id,
      task_run_id: call.taskId,
      attempt_id: call.attemptId,
    }, controller.signal);
    if (controller.signal.aborted) return;
    if (!failures.length) {
      callError.value = tm("debugFork.noChildFailures");
      return;
    }
    clearCall();
    candidates.value = failures;
    selectFailure(failures[0]);
  } catch (err) {
    if (!controller.signal.aborted) callError.value = extractApiError(err, tm("errors.operationFailed")).message;
  } finally {
    if (callRead === controller) loading.value = false;
  }
}
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
