<template>
  <section class="run-process mt-6">
    <h3 class="text-h6 mb-2">{{ tm("v2.process") }}</h3>
    <p class="text-caption text-medium-emphasis mb-3">
      {{ tm("progress.observation") }}
    </p>
    <h4 v-if="run.messages?.length" class="text-subtitle-1 mt-4">
      {{ tm("communication.title") }}
    </h4>
    <details
      v-for="message in run.messages ?? []"
      :key="message.message_id"
      class="process-record"
      data-testid="member-message"
    >
      <summary>
        <span class="font-weight-medium"
          >{{ message.sender_member_id }} →
          {{ message.recipient_member_id }}</span
        >
        <span class="process-id text-caption"
          >{{ message.message_id }} ·
          {{
            tm(
              message.consumption
                ? "communication.consumed"
                : "communication.pending",
            )
          }}</span
        >
      </summary>
      <div class="pa-4">
        <p class="process-text text-caption">
          {{ tm("communication.scope") }}: {{ message.scope_id }} ·
          {{ tm("progress.task") }}: {{ message.sender_task_run_id }} ·
          {{ tm("v2.attempt") }}: {{ message.sender_attempt_id }}
        </p>
        <pre class="process-text my-3">{{ message.text }}</pre>
        <p v-if="message.consumption" class="process-text text-caption">
          {{ tm("communication.receivedBy") }}:
          {{ message.consumption.task_run_id }} ·
          {{ message.consumption.attempt_id }} ·
          {{ tm("recovery.contextPosition") }}:
          {{ message.consumption.context_position }} ·
          {{ message.consumption.invocation_id }}
        </p>
        <div
          v-for="reference in message.artifact_refs"
          :key="JSON.stringify(reference)"
          class="mt-3"
        >
          <v-btn
            variant="tonal"
            class="message-artifact-read"
            :loading="artifactReads[JSON.stringify(reference)]?.loading"
            @click="readMessageArtifact(reference)"
            >{{ tm("communication.readVersion") }} ·
            {{ reference.artifact_id }} · v{{ reference.version }}</v-btn
          >
          <p
            v-if="artifactReads[JSON.stringify(reference)]?.error"
            role="alert"
            class="process-text mt-2"
          >
            {{ artifactReads[JSON.stringify(reference)].error }}
          </p>
          <pre
            v-if="artifactReads[JSON.stringify(reference)]?.artifact"
            class="process-text mt-3"
            data-testid="message-artifact"
            >{{
              JSON.stringify(
                artifactReads[JSON.stringify(reference)].artifact,
                null,
                2,
              )
            }}</pre
          >
        </div>
      </div>
    </details>
    <details
      v-for="call in run.tool_calls ?? []"
      :key="call.record_id"
      class="process-record"
      data-testid="durable-tool-call"
    >
      <summary>
        <span class="font-weight-medium"
          >{{ call.name }} ·
          {{ tm(`recovery.toolStates.${call.status}`) }}</span
        >
        <span class="text-caption text-medium-emphasis process-id"
          >{{ call.call_id }} · {{ tm("v2.attempt") }}
          {{ call.attempt_id }}</span
        >
      </summary>
      <div class="pa-4">
        <p class="process-text">
          {{ tm("recovery.owner") }}: {{ call.execution_id }}
        </p>
        <p v-if="call.started_execution_id" class="process-text">
          {{ tm("recovery.startedOwner") }}: {{ call.started_execution_id }}
        </p>
        <p class="process-text">
          {{ tm("recovery.contextPosition") }}:
          {{ call.context_position ?? tm("recovery.notApplied") }}
        </p>
        <p
          v-if="call.recovery?.replacement_record_id"
          class="process-text mt-2"
        >
          {{ tm("resolution.replacedBy") }}:
          {{ call.recovery.replacement_record_id }}
        </p>
        <p v-if="call.recovery?.replaces_record_id" class="process-text mt-2">
          {{ tm("resolution.replaces") }}:
          {{ call.recovery.replaces_record_id }}
        </p>
        <pre class="process-text mt-3">{{
          JSON.stringify(call.args, null, 2)
        }}</pre>
        <pre v-if="call.raw_results?.length" class="process-text mt-3">{{
          JSON.stringify(call.raw_results, null, 2)
        }}</pre>
        <pre
          v-if="Object.keys(call.recovery ?? {}).length"
          class="process-text mt-3"
          >{{ JSON.stringify(call.recovery, null, 2) }}</pre
        >
        <WorkflowToolResolution
          v-if="call.available_actions?.length"
          :key="call.record_id"
          :actions="call.available_actions"
          :busy="busy ?? false"
          @submit="emit('resolve', call.record_id, $event)"
        />
      </div>
    </details>
    <p
      v-if="!run.process?.length && !run.state.response"
      class="text-body-2 text-medium-emphasis"
    >
      {{ tm("progress.emptyProcess") }}
    </p>
    <details
      v-for="record in run.process ?? []"
      :key="record.record_id"
      class="process-record"
      data-testid="process-record"
    >
      <summary>
        <span class="font-weight-medium">{{
          record.kind === "model_response"
            ? tm("progress.response")
            : `${tm("progress.toolResult")} · ${record.data.name}`
        }}</span>
        <span class="text-caption text-medium-emphasis process-id"
          >{{ tm("v2.attempt") }} {{ record.attempt_id }}</span
        >
      </summary>
      <div class="pa-4">
        <p class="text-caption text-medium-emphasis process-text mb-3">
          {{ tm("v2.member") }} {{ record.member_id }} ·
          {{ tm("progress.task") }} {{ record.task_id }}
        </p>
        <pre
          v-if="record.kind === 'model_response' && record.data.text"
          class="process-text"
          >{{ record.data.text }}</pre
        >
        <details
          v-if="record.data.reasoning_content"
          class="mt-3"
          data-testid="provider-thinking"
        >
          <summary>{{ tm("progress.thinking") }}</summary>
          <pre class="process-text pa-3">{{
            record.data.reasoning_content
          }}</pre>
        </details>
        <div v-if="record.data.tool_calls?.length" class="mt-3">
          <p class="font-weight-medium">{{ tm("progress.toolCalls") }}</p>
          <pre class="process-text">{{
            JSON.stringify(record.data.tool_calls, null, 2)
          }}</pre>
        </div>
        <pre v-if="record.kind === 'tool_result'" class="process-text">{{
          JSON.stringify(record.data.raw_result ?? record.data.result, null, 2)
        }}</pre>
        <details class="mt-3">
          <summary>{{ tm("progress.fullRecord") }}</summary>
          <pre class="process-text pa-3">{{
            JSON.stringify(record.data, null, 2)
          }}</pre>
        </details>
      </div>
    </details>
    <details
      v-if="run.state.response"
      class="process-record"
      data-testid="complete-response"
    >
      <summary>{{ tm("progress.collectedResponse") }}</summary>
      <pre class="process-text pa-4">{{
        JSON.stringify(run.state.response, null, 2)
      }}</pre>
    </details>
    <details class="process-record">
      <summary>{{ tm("progress.events") }} · {{ run.cursor ?? 0 }}</summary>
      <pre class="process-text pa-4">{{
        JSON.stringify(run.events ?? [], null, 2)
      }}</pre>
    </details>
  </section>
</template>
<script setup lang="ts">
import { reactive } from "vue";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { extractApiError } from "@/utils/extractApiError";
import { useModuleI18n } from "@/i18n/composables";
import WorkflowToolResolution from "./WorkflowToolResolution.vue";
import type { ToolResolutionRequest } from "@/api/generated/openapi-v1";
defineProps<{ run: any; busy?: boolean }>();
const emit = defineEmits<{
  (event: "resolve", recordId: string, payload: ToolResolutionRequest): void;
}>();
const { tm } = useModuleI18n("features/agent-teams");
const artifactReads = reactive<
  Record<string, { loading: boolean; artifact?: any; error?: string }>
>({});
async function readMessageArtifact(reference: {
  artifact_id: string;
  version: number;
}) {
  const key = JSON.stringify(reference);
  if (artifactReads[key]?.loading) return;
  artifactReads[key] = { loading: true };
  try {
    artifactReads[key].artifact = await workflowAssetsApi.artifact(
      reference.artifact_id,
      reference.version,
    );
  } catch (error) {
    artifactReads[key].error = extractApiError(
      error,
      tm("errors.operationFailed"),
    ).message;
  } finally {
    artifactReads[key].loading = false;
  }
}
</script>
<style scoped>
.run-process {
  min-width: 0;
}
.process-record {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 10px;
  margin-top: 10px;
  background: rgb(var(--v-theme-surface));
}
summary {
  cursor: pointer;
  padding: 14px;
  overflow-wrap: anywhere;
}
.process-id {
  display: block;
  margin-top: 4px;
}
.process-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-width: 100%;
  max-height: 32rem;
  overflow: auto;
  font: inherit;
}
.message-artifact-read {
  max-width: 100%;
  min-height: 36px;
  height: auto;
}
.message-artifact-read :deep(.v-btn__content) {
  white-space: normal;
  overflow-wrap: anywhere;
}
@media (max-width: 500px) {
  summary {
    padding: 12px;
  }
}
</style>
