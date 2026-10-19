<template>
  <section class="my-4" data-testid="run-controls">
    <h3 class="text-h6 mb-2">{{ tm("controls.title") }}</h3>
    <p class="text-body-2 mb-3" role="status">
      {{ tm(`controls.states.${status}`) }}
    </p>
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
        @click="confirming = 'cancel'"
        >{{ tm("controls.cancel") }}</v-btn
      >
      <v-btn
        v-if="failed && !terminal"
        variant="tonal"
        :disabled="busy || uncertain || !!task.pending_result"
        data-testid="retry-task"
        @click="confirming = 'retry'"
        >{{ tm("controls.retry") }}</v-btn
      >
    </div>
    <p v-if="failed && uncertain" class="text-body-2 mt-3">
      {{ tm("controls.resolveFirst") }}
    </p>
    <p class="text-caption mt-3">{{ tm("controls.semantics") }}</p>
    <details
      v-for="attempt in task.attempts ?? []"
      :key="attempt.attempt_id"
      class="mt-3"
      data-testid="attempt-history"
    >
      <summary>
        {{ tm("controls.previous") }} · {{ attempt.attempt_id }} ·
        {{ tm(`v2.states.${attempt.status}`) }}
      </summary>
      <p class="mt-2">{{ attempt.state.reason }}</p>
      <p>{{ tm("controls.originalInputs") }}</p>
      <pre class="control-inputs">{{
        JSON.stringify(
          {
            inputs: attempt.snapshot.inputs,
            input_artifacts: attempt.snapshot.input_artifacts ?? [],
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
    <v-dialog
      :model-value="!!confirming"
      max-width="720"
      @update:model-value="!$event && (confirming = null)"
    >
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">{{
          tm(`controls.${confirming}`)
        }}</v-card-title>
        <v-card-text>
          <p>
            {{
              tm(
                confirming === "retry"
                  ? "controls.retryHint"
                  : "controls.cancelHint",
              )
            }}
          </p>
          <template v-if="confirming === 'retry'">
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
          <v-btn variant="text" @click="confirming = null">{{
            tm("v2.close")
          }}</v-btn>
          <v-btn
            variant="tonal"
            :disabled="busy"
            data-testid="confirm-run-control"
            @click="submit(confirming!)"
            >{{ tm("controls.confirm") }}</v-btn
          >
        </v-card-actions>
      </v-card>
    </v-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import type {
  WorkflowControlRequest,
  WorkflowRetryRequest,
} from "@/api/generated/openapi-v1";

const props = defineProps<{ run: any; task: any; busy: boolean }>();
const emit = defineEmits<{
  (
    event: "submit",
    payload: WorkflowControlRequest | WorkflowRetryRequest,
  ): void;
}>();
const { tm } = useModuleI18n("features/agent-teams");
const status = computed(() => props.run.control?.status ?? "active");
const terminal = computed(
  () =>
    status.value === "cancelled" ||
    ["completed", "cancelled"].includes(props.run.status),
);
const failed = computed(
  () =>
    ["failed", "awaiting_result", "blocked"].includes(props.task.status) &&
    !(
      ["team_call_v2", "workflow_call_v2"].includes(
        props.task.snapshot?.engine,
      ) && props.task.state.child_scope_run_id
    ),
);
const uncertain = computed(() =>
  (props.task.tool_calls ?? []).some(
    (call: any) =>
      (!call.attempt_id || call.attempt_id === props.task.state.attempt_id) &&
      ["started", "unknown"].includes(call.status) &&
      !call.recovery?.replacement_record_id,
  ),
);
const confirming = ref<"cancel" | "retry" | null>(null);
const requests = new Map<string, string>();

function submit(action: WorkflowControlRequest["action"] | "retry") {
  if (props.busy || terminal.value) return;
  const key = `${props.run.run_id}:${props.run.control?.revision ?? 0}:${
    props.task.state.attempt_id
  }:${action}`;
  if (!requests.has(key))
    requests.set(
      key,
      typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    );
  const request_id = requests.get(key)!;
  emit(
    "submit",
    action === "retry"
      ? {
          request_id,
          attempt_id: props.task.state.attempt_id,
          input_policy: "original",
        }
      : {
          request_id,
          action,
          expected_revision: props.run.control?.revision ?? 0,
        },
  );
  confirming.value = null;
}
</script>

<style scoped>
.control-inputs {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 240px;
  overflow: auto;
}
</style>
