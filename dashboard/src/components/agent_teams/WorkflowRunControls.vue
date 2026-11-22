<template>
  <section class="run-controls my-4" data-testid="run-controls">
    <h3 class="text-h6 mb-2">{{ tm("controls.title") }}</h3>
    <p class="text-body-2 mb-3" role="status">
      {{ tm(`controls.states.${run.status === "completed" ? "completed" : status}`) }}
    </p>
    <v-alert v-if="feedback" :type="feedback.error ? 'error' : 'success'" variant="tonal"
      :role="feedback.error ? 'alert' : 'status'" class="mb-3">
      {{ feedback.message }}
    </v-alert>
    <v-alert
      v-if="status === 'paused' || status === 'cancelled'"
      type="info"
      variant="tonal"
      class="mb-3"
    >
      {{ tm("controls.inFlight") }}
    </v-alert>
    <div class="d-flex flex-wrap ga-2">
      <v-btn
        v-if="!terminal && status === 'active'"
        variant="tonal"
        :disabled="busy"
        data-testid="pause-run"
        @click="submit('pause')"
        >{{ tm("controls.pause") }}</v-btn
      >
      <v-btn
        v-if="!terminal && status === 'paused'"
        variant="tonal"
        :disabled="busy"
        data-testid="continue-run"
        @click="submit('continue')"
        >{{ tm("controls.continue") }}</v-btn
      >
      <v-btn
        v-if="!terminal"
        variant="text"
        color="error"
        :disabled="busy"
        data-testid="cancel-run"
        @click="
          confirmationAction = 'cancel';
          confirming = true;
        "
        >{{ tm("controls.cancel") }}</v-btn
      >
      <v-btn
        v-if="failed && retryableTask && !terminal"
        variant="tonal"
        :disabled="busy || !!retryBlockedReason"
        data-testid="retry-task"
        @click="
          confirmationAction = 'retry';
          confirming = true;
        "
        >{{ tm("controls.retry") }}</v-btn
      >
      <v-btn
        v-if="canRerun"
        variant="tonal"
        :disabled="busy"
        data-testid="rerun-workflow"
        @click="
          confirmationAction = 'rerun';
          confirming = true;
        "
        >{{ tm("controls.rerun") }}</v-btn
      >
    </div>
    <p v-if="failed && !terminal && retryBlockedReason" class="text-body-2 mt-3">
      {{ tm(retryBlockedReason) }}
    </p>
    <p class="text-caption mt-3">{{ tm("controls.semantics") }}</p>
    <details
      v-for="attempt in task.attempts ?? []"
      :key="attempt.attempt_id"
      class="attempt-history mt-3"
      data-testid="attempt-history"
    >
      <summary>
        {{ tm("controls.previous") }} · {{ attempt.attempt_id }} ·
        {{ tm(`v2.states.${attempt.status}`) }}
      </summary>
      <p class="mt-2">{{ attempt.state?.reason }}</p>
      <p>{{ tm("controls.originalInputs") }}</p>
      <pre class="control-inputs">{{
        JSON.stringify(
          {
            inputs: attempt.snapshot?.inputs,
            input_artifacts: attempt.snapshot?.input_artifacts ?? [],
          },
          null,
          2,
        )
      }}</pre>
      <p class="text-caption">{{ tm("controls.usageRetained") }}</p>
      <pre class="control-inputs">{{
        JSON.stringify(attempt.checkpoint?.stats?.token_usage ?? null, null, 2)
      }}</pre>
    </details>
    <v-dialog v-model="confirming" max-width="720">
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          tm(`controls.${confirmationAction}`)
        }}</v-card-title>
        <v-card-text>
          <p>{{ tm(`controls.${confirmationAction}Hint`) }}</p>
          <p v-if="confirmationAction === 'rerun'" class="mt-3">
            {{ run.snapshot.definition.name }} 路 v{{ run.snapshot.asset_version }}
          </p>
          <template v-if="confirmationAction === 'retry'">
            <p class="mt-3">
              {{ tm("controls.originalInputs") }} · {{ task.state.attempt_id }}
            </p>
            <pre class="control-inputs">{{
              JSON.stringify(
                {
                  inputs: task.snapshot.inputs,
                  input_artifacts: task.snapshot.input_artifacts ?? [],
                },
                null,
                2,
              )
            }}</pre>
          </template>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="confirming = false">{{
            tm("v2.close")
          }}</v-btn>
          <v-btn
            variant="tonal"
            :disabled="busy"
            data-testid="confirm-run-control"
            @click="submit(confirmationAction)"
            >{{ tm("controls.confirm") }}</v-btn
          >
        </v-card-actions>
      </v-card>
    </v-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import type {
  WorkflowControlRequest,
  WorkflowRerunRequest,
  WorkflowRetryRequest,
} from "@/api/generated/openapi-v1";

const props = defineProps<{
  run: any;
  task: any;
  busy: boolean;
  feedback?: { message: string; error: boolean };
}>();
const emit = defineEmits<{
  (
    event: "submit",
    payload: WorkflowControlRequest | WorkflowRetryRequest | WorkflowRerunRequest,
  ): void;
}>();
const { tm } = useModuleI18n("features/agent-teams");
const status = computed(() => props.run.control?.status ?? "active");
const terminal = computed(
  () =>
    status.value === "cancelled" ||
    ["completed", "cancelled"].includes(props.run.status),
);
const canRerun = computed(() =>
  props.run.status === "completed" &&
  status.value !== "cancelled" &&
  props.run.snapshot?.engine === "graph_v2" &&
  props.run.snapshot.root_run_id === props.run.run_id &&
  !props.run.snapshot.trial,
);
const failed = computed(() => ["failed", "awaiting_result", "blocked"].includes(props.task.status));
const attemptId = computed(() => props.task.state?.attempt_id);
const retryableTask = computed(() =>
  !!attemptId.value &&
  !["graph_v2", "human_task_v2", "control_task_v2"].includes(props.task.snapshot?.engine) &&
  !(
    ["team_call_v2", "workflow_call_v2"].includes(props.task.snapshot?.engine) &&
    props.task.state?.child_scope_run_id
  ),
);
const uncertain = computed(() =>
  (props.task.tool_calls ?? []).some(
    (call: any) =>
      (!call.attempt_id || call.attempt_id === attemptId.value) &&
      ["started", "unknown"].includes(call.status) &&
      !call.recovery?.replacement_record_id,
  ),
);
const retryBlockedReason = computed(() => {
  if (!retryableTask.value) return "controls.selectFailedTask";
  if (props.task.pending_result) return "controls.recoverPendingResult";
  if (uncertain.value) return "controls.resolveFirst";
  if (props.task.recovery?.has_saved_tool_results) {
    return props.task.recovery.available ? "controls.recoverSavedTools" : "controls.reviewSavedTools";
  }
  return "";
});
const confirming = ref(false);
const confirmationAction = ref<"cancel" | "retry" | "rerun">("cancel");
const requests = new Map<string, string>();
watch([() => props.task.run_id, attemptId], () => {
  if (confirmationAction.value === "retry") confirming.value = false;
}, { flush: "sync" });

function submit(action: WorkflowControlRequest["action"] | "retry" | "rerun") {
  if (props.busy || (action === "rerun" ? !canRerun.value : terminal.value)) return;
  if (action === "retry" && (!failed.value || retryBlockedReason.value)) return;

  const revision = props.run.control?.revision ?? 0;
  const key = action === "retry"
    ? `retry:${props.task.run_id}:${attemptId.value}`
    : action === "rerun" ? undefined : `${action}:${revision}`;
  let request_id = key ? requests.get(key) : undefined;
  if (!request_id) {
    request_id = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    if (key) requests.set(key, request_id);
  }
  confirming.value = false;
  if (action === "rerun") {
    emit("submit", { request_id });
  } else if (action === "retry") {
    emit("submit", { request_id, attempt_id: attemptId.value, input_policy: "original" });
  } else {
    emit("submit", { request_id, action, expected_revision: revision });
  }
}
</script>

<style scoped>
.control-inputs {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 240px;
  overflow: auto;
}

.run-controls { min-width: 0; color: var(--at-ink, rgb(var(--v-theme-on-surface))); }
.run-controls :deep(.v-btn) { min-height: 40px; }
.run-controls > .text-caption { color: var(--at-muted, #718393); }
.attempt-history { border: 1px solid var(--at-line, rgba(var(--v-border-color), var(--v-border-opacity))); border-radius: 8px; padding: 10px 12px; overflow-wrap: anywhere; }
.attempt-history summary { cursor: pointer; }
.attempt-history summary:focus-visible, .run-controls :deep(button:focus-visible) { outline: 2px solid var(--at-accent, #287fad); outline-offset: 2px; }

</style>
