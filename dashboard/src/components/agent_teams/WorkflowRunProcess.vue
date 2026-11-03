<template>
  <section class="run-process mt-6">
    <h3 class="text-h6 mb-2">{{ tm("progress.readerTitle") }}</h3>
    <p class="text-caption text-medium-emphasis mb-3">{{ tm("progress.capabilityNote") }}</p>
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
    <template v-for="item in readingItems" :key="item.kind + ':' + item.key">
      <article v-if="item.kind === 'delivery'" class="process-record pa-4" data-testid="model-delivery">
        <p class="text-caption text-medium-emphasis">
          {{ tm("progress.modelResponse") }} · {{ tm(`progress.deliveryStates.${item.status}`) }} · {{ tm("v2.attempt") }} {{ item.attempt }}
        </p>
        <p v-if="item.streaming === false" class="text-caption">{{ tm("progress.finalOnly") }}</p>
        <p v-if="item.missingChunks" class="text-warning" data-testid="missing-stream-chunks">{{ tm("progress.missingChunks") }}</p>
        <details class="mt-2 context-details" data-testid="model-context">
          <summary>{{ tm("progress.actualContext") }}</summary>
          <div class="context-body">
            <p v-if="!item.context" class="text-caption">{{ tm("progress.contextUnavailable") }}</p>
            <template v-else>
              <p class="text-caption">{{ item.context.model }} · {{ tm(`progress.contextStages.${item.context.stage}`) }}</p>
              <details class="context-diagnostics"><summary>{{ tm("progress.contextDiagnostics") }}</summary><p class="text-caption">{{ item.context.invocation_id }} · {{ item.context.receipt_id }}</p></details>
              <p v-if="item.context.observation.truncated" class="text-warning">{{ tm("progress.contextTruncated", { limit: item.context.observation.limit, length: item.context.observation.original_length }) }}</p>
              <details v-for="(message, index) in item.context.observation.value.messages ?? []" :key="index" class="mt-2">
                <summary>{{ tm(`progress.messageRoles.${message.role}`) }} · {{ index + 1 }}</summary>
                <MarkdownMessagePart v-if="typeof message.content === 'string'" :content="message.content" :refs="null" :is-dark="isDark" :custom-html-tags="CHAT_MARKDOWN_CUSTOM_TAGS" :is-streaming="false" />
                <template v-else-if="Array.isArray(message.content)">
                  <template v-for="(part, partIndex) in message.content" :key="partIndex">
                    <MarkdownMessagePart v-if="part.type === 'text'" :content="part.text" :refs="null" :is-dark="isDark" :custom-html-tags="CHAT_MARKDOWN_CUSTOM_TAGS" :is-streaming="false" />
                    <p v-else class="process-text">{{ part.type }} · {{ part.omitted ? tm(`progress.mediaOmissions.${part.omitted}`) : part.url ?? tm("progress.contextUnavailable") }} <span v-if="part.reference_redacted">· {{ tm("progress.referenceRedacted") }}</span> <span v-if="part.original_length">· {{ tm("progress.mediaLength", { length: part.original_length }) }}</span></p>
                  </template>
                </template>
                <pre v-if="message.tool_calls?.length" class="process-text">{{ JSON.stringify(message.tool_calls, null, 2) }}</pre>
              </details>
              <details class="mt-2"><summary>{{ tm("progress.contextTools") }}</summary><pre class="process-text">{{ JSON.stringify(item.context.observation.value.tools ?? [], null, 2) }}</pre></details>
              <p class="text-caption mt-3">{{ tm("progress.sourceNote") }}</p>
              <p v-if="item.context.assembly.truncated" class="text-warning">{{ tm("progress.contextTruncated", { limit: item.context.assembly.limit, length: item.context.assembly.original_length }) }}</p>
              <details v-for="(source, index) in item.context.assembly.value.sources ?? []" :key="index" class="mt-2">
                <summary>{{ tm(`progress.sourceKinds.${source.kind}`) }} <span v-if="source.id">· {{ source.id }}</span><span v-if="source.ids?.length">· {{ source.ids.join(", ") }}</span></summary>
                <MarkdownMessagePart v-if="source.content" :content="source.content" :refs="null" :is-dark="isDark" :custom-html-tags="CHAT_MARKDOWN_CUSTOM_TAGS" :is-streaming="false" />
                <p v-else class="text-caption">{{ tm("progress.sourceAbsent") }}</p>
              </details>
              <LlmRequestInjectionsPanel v-if="item.context.assembly.value.injections?.length" :items="item.context.assembly.value.injections" class="mt-3" />
            </template>
          </div>
        </details>
        <ReasoningBlock v-if="item.reasoning" :reasoning="item.reasoning" :is-dark="isDark" />
        <MarkdownMessagePart v-if="item.text" :content="item.text" :refs="null" :is-dark="isDark" :custom-html-tags="CHAT_MARKDOWN_CUSTOM_TAGS" :is-streaming="item.status === 'started'" />
      </article>
      <div v-else class="process-record pa-2" data-testid="observed-tool">
        <p class="text-caption text-medium-emphasis">{{ tm(`progress.toolStates.${item.status}`) }} · {{ tm("v2.attempt") }} {{ item.attempt }}</p>
        <p v-if="item.missingParts" class="text-warning" data-testid="missing-tool-parts">{{ tm("progress.missingChunks") }}</p>
        <ToolCallCard :tool-call="item" :is-dark="isDark" :is-active="item.status === 'running' && run.status === 'running'" />
      </div>
    </template>
    <details
      v-for="record in otherRecords"
      :key="record.record_id"
      class="process-record"
      data-testid="process-record"
    >
      <summary>
        <span class="font-weight-medium">{{
          record.kind === "model_response"
            ? tm("progress.response")
            : record.kind === "remote_event"
            ? `${tm("progress.remoteEvent")} · ${record.data.provider} · ${
                record.data.event.event
              }`
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
        <pre
          v-if="record.kind === 'remote_event'"
          class="process-text"
          data-testid="remote-event"
          >{{ JSON.stringify(record.data.event, null, 2) }}</pre
        >
        <details class="mt-3">
          <summary>{{ tm("progress.fullRecord") }}</summary>
          <pre class="process-text pa-3">{{
            JSON.stringify(record.data, null, 2)
          }}</pre>
        </details>
      </div>
    </details>
    <details
      v-if="run.state.response && !deliveries.length"
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
      <p class="text-caption pa-4">{{ tm("progress.observation") }}</p>
      <pre class="process-text pa-4">{{
        JSON.stringify(run.events ?? [], null, 2)
      }}</pre>
    </details>
  </section>
</template>
<script setup lang="ts">
import { computed, reactive } from "vue";
import MarkdownMessagePart from "@/components/chat/message_list_comps/MarkdownMessagePart.vue";
import LlmRequestInjectionsPanel from "@/components/chat/LlmRequestInjectionsPanel.vue";
import ReasoningBlock from "@/components/chat/message_list_comps/ReasoningBlock.vue";
import ToolCallCard from "@/components/chat/message_list_comps/ToolCallCard.vue";
import { CHAT_MARKDOWN_CUSTOM_TAGS } from "@/components/chat/chatMarkdownComponents";
import { useCustomizerStore } from "@/stores/customizer";
import { workflowAssetsApi } from "@/api/workflowAssets";
import { extractApiError } from "@/utils/extractApiError";
import { useModuleI18n } from "@/i18n/composables";
import WorkflowToolResolution from "./WorkflowToolResolution.vue";
import type { ToolResolutionRequest } from "@/api/generated/openapi-v1";
const props = defineProps<{ run: any; busy?: boolean }>();
const customizer = useCustomizerStore();
const isDark = computed(() => customizer.isDark);
const deliveries = computed(() => {
  const groups = new Map<string, any>();
  for (const [order, record] of (props.run.process ?? []).entries()) {
    if (!["model_delivery", "model_delta"].includes(record.kind)) continue;
    const data = record.data;
    const key = `${record.run_id}:${record.attempt_id}:${data.receipt_id}`;
    if (!groups.has(key))
      groups.set(key, {
        key,
        order,
        kind: "delivery",
        invocation: data.invocation_id,
        attempt: record.attempt_id,
        status: "started",
        chunks: [],
        end: null,
      });
    const group = groups.get(key);
    if (record.kind === "model_delta") group.chunks.push(data);
    else if (data.status !== "started") group.end = data;
    else group.streaming = data.streaming;
  }
  for (const [order, record] of (props.run.process ?? []).entries()) {
    if (record.kind !== "model_context" || !record.data.receipt_id) continue;
    const key = `${record.run_id}:${record.attempt_id}:${record.data.receipt_id}`;
    if (!groups.has(key)) groups.set(key, {
      key, order, kind: "delivery", invocation: record.data.invocation_id, attempt: record.attempt_id,
      streaming: false, chunks: [], end: null,
    });
    groups.get(key).context = record.data;
  }
  for (const [order, record] of (props.run.process ?? []).entries()) {
    if (record.kind !== "model_response" || !record.data.receipt_id) continue;
    const key = `${record.run_id}:${record.attempt_id}:${record.data.receipt_id}`;
    if (!groups.has(key)) groups.set(key, {
      key, order, kind: "delivery", invocation: record.data.invocation_id, attempt: record.attempt_id,
      streaming: false, chunks: [], end: null,
    });
    const group = groups.get(key);
    if (group) {
      group.order = Math.min(group.order, order);
      group.response = record.data;
    }
  }
  return [...groups.values()].map((group) => {
    group.chunks.sort((a: any, b: any) => a.sequence - b.sequence);
    return {
      ...group,
      missingChunks: group.chunks.some((chunk: any, index: number) => chunk.sequence !== index),
      status: group.end?.status ?? (group.response ? (group.response.role === "err" ? "error" : "completed") : (props.run.status === "running" ? "started" : "incomplete")),
      text:
        group.response?.text ??
        group.end?.text ??
        group.chunks.map((item: any) => item.text).join(""),
      reasoning:
        group.response?.reasoning_content ??
        group.end?.reasoning_content ??
        group.chunks.map((item: any) => item.reasoning_content).join(""),
    };
  });
});
const observedTools = computed(() => {
  const groups = new Map<string, any>();
  for (const [order, record] of (props.run.process ?? []).entries()) {
    if (!["tool_start", "tool_progress", "tool_status"].includes(record.kind)) continue;
    const data = record.data;
    const key = `${record.run_id}:${record.attempt_id}:${data.tool_record_id}`;
    if (!groups.has(key))
      groups.set(key, {
        key,
        order,
        kind: "tool",
        attempt: record.attempt_id,
        id: data.id,
        name: data.name,
        args: data.args ?? {},
        parts: [],
        status: "running",
      });
    const group = groups.get(key);
    if (record.kind === "tool_start") group.args = data.args;
    else if (record.kind === "tool_progress") group.parts.push(data);
    else group.status = data.status;
  }
  for (const record of props.run.process ?? []) {
    if (record.kind !== "tool_result") continue;
    const key = `${record.run_id}:${record.attempt_id}:${record.data.tool_record_id}`;
    const group = groups.get(key);
    if (group && typeof record.data.result === "string") group.finalResult = record.data.result;
    if (group && record.data.raw_result && Number.isInteger(record.data.index) &&
        !group.parts.some((part: any) => part.index === record.data.index))
      group.parts.push(record.data);
  }
  return [...groups.values()].map((group) => {
    const progress = group.parts
      .sort((a: any, b: any) => a.index - b.index)
      .flatMap((item: any) => (item.raw_result?.content ?? [])
        .filter((part: any) => part.type === "text")
        .map((part: any) => part.text))
      .join("\n");
    return { ...group, missingParts: group.parts.some((part: any, index: number) => Number.isInteger(part.index) && part.index !== index), status: group.status === "running" && props.run.status !== "running" ? "incomplete" : group.status,
      result: group.finalResult && !progress.includes(group.finalResult)
      ? [progress, group.finalResult].filter(Boolean).join("\n") : progress };
  });
});
const readingItems = computed(() => [...deliveries.value, ...observedTools.value]
  .sort((a, b) => a.order - b.order));
const otherRecords = computed(() =>
  (props.run.process ?? []).filter((record: any) => {
    if (
      ["model_delivery", "model_delta", "model_context", "tool_start", "tool_progress", "tool_status"].includes(
        record.kind,
      )
    )
      return false;
    if (
      record.kind === "model_response" &&
      deliveries.value.some(
        (item) =>
          item.key ===
          `${record.run_id}:${record.attempt_id}:${record.data.receipt_id}`,
      )
    )
      return false;
    if (
      record.kind === "tool_result" &&
      record.data.tool_record_id && observedTools.value.some((item) => item.key === `${record.run_id}:${record.attempt_id}:${record.data.tool_record_id}`)
    )
      return false;
    return true;
  }),
);
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
.context-details summary {
  padding: 6px 0;
}
.context-body {
  padding: 0 8px 8px;
}
.context-body :deep(.markdown-content),
.context-body :deep(.markdown-renderer) {
  margin: 0;
  padding: 0;
  min-height: 0;
}
.context-body :deep(p.paragraph-node) {
  margin: 0 0 4px;
}
.context-diagnostics {
  margin-bottom: 4px;
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
