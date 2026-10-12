<template>
  <section class="run-process mt-6">
    <h3 class="text-h6 mb-2">{{ tm("v2.process") }}</h3>
    <p class="text-caption text-medium-emphasis mb-3">
      {{ tm("progress.observation") }}
    </p>
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
          JSON.stringify(record.data.raw_result, null, 2)
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
import { useModuleI18n } from "@/i18n/composables";
defineProps<{ run: any }>();
const { tm } = useModuleI18n("features/agent-teams");
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
@media (max-width: 500px) {
  summary {
    padding: 12px;
  }
}
</style>
