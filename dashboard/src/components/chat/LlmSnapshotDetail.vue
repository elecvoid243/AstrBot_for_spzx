<!--
  Author: impl_task10
  Date: 2026-11-16

  One developer-mode LLM request snapshot. The sections follow the approved
  order (diff, overview, response, system prompt, tools, messages, injections,
  diagnostics, raw JSON). Payload-bearing blocks use a monospace face and
  hairline dividers; diagnostics and raw JSON start collapsed so the panel
  stays scannable. Diagnostics render null as "unknown" instead of pretending
  it was false.
-->
<template>
  <article class="snapshot-detail">
    <header class="detail-head">
      <div class="detail-heading">
        <div class="detail-title">{{ title }}</div>
        <div class="detail-sub">{{ subtitle }}</div>
        <span v-if="envelope.truncated" class="badge-truncated">
          {{ tm("devMode.truncated", { size: formatSize(envelope.original_bytes) }) }}
        </span>
      </div>
      <div class="detail-actions">
        <span
          v-if="actionState !== 'idle'"
          class="copy-feedback action-feedback"
          role="status"
        >
          {{ feedbackText }}
        </span>
        <button
          type="button"
          class="detail-action action-download"
          @click="downloadJson"
        >
          {{ tm("devMode.action.download") }}
        </button>
        <button
          type="button"
          class="detail-action action-copy"
          @click="copyJson"
        >
          {{ tm("devMode.action.copy") }}
        </button>
        <button
          type="button"
          class="detail-action action-refresh"
          @click="emit('refresh')"
        >
          {{ tm("devMode.action.refresh") }}
        </button>
      </div>
    </header>

    <section
      v-if="showDiff"
      class="sec sec-diff"
      :class="{ collapsed: !isOpen('diff'), 'is-warning': diff?.warning }"
    >
      <button
        type="button"
        class="sec-head"
        :aria-expanded="isOpen('diff')"
        @click="toggleSection('diff')"
      >
        <span class="sec-title">{{ tm("devMode.diff.title") }}</span>
        <span class="sec-meta mono">
          {{ diff?.addedTools.length ? `+${diff.addedTools.length}` : "0" }}
          /
          {{ diff?.removedTools.length ? `-${diff.removedTools.length}` : "0" }}
        </span>
        <span class="sec-chevron" aria-hidden="true">›</span>
      </button>
      <div v-if="isOpen('diff')" class="sec-body">
        <div class="diff-grid">
          <div class="diff-row">
            <span class="diff-label mono">messages</span>
            <span class="diff-value mono">{{
              formatSigned(diff?.messageDelta ?? 0)
            }}</span>
          </div>
          <div class="diff-row">
            <span class="diff-label mono">tools</span>
            <span class="diff-value mono">
              <span v-if="diff?.addedTools.length" class="diff-added">
                +{{ diff.addedTools.join(", ") }}
              </span>
              <span v-if="diff?.removedTools.length" class="diff-removed">
                -{{ diff.removedTools.join(", ") }}
              </span>
              <span
                v-if="!diff?.addedTools.length && !diff?.removedTools.length"
                class="diff-same"
                >unchanged</span
              >
            </span>
          </div>
          <div class="diff-row">
            <span class="diff-label mono">system</span>
            <span class="diff-value mono">{{
              diff?.systemChanged ? "changed" : "unchanged"
            }}</span>
          </div>
          <div class="diff-row">
            <span class="diff-label mono">params</span>
            <span class="diff-value mono">{{
              diff?.paramsChanged ? "changed" : "unchanged"
            }}</span>
          </div>
        </div>
        <div v-if="diff?.warning" class="diff-warning mono">
          compressed / truncated payload
        </div>
      </div>
    </section>

    <section
      class="sec sec-overview"
      :class="{ collapsed: !isOpen('overview') }"
    >
      <button
        type="button"
        class="sec-head"
        :aria-expanded="isOpen('overview')"
        @click="toggleSection('overview')"
      >
        <span class="sec-title">{{ tm("devMode.section.overview") }}</span>
        <span class="sec-meta mono">{{ envelope.status }}</span>
        <span class="sec-chevron" aria-hidden="true">›</span>
      </button>
      <div v-if="isOpen('overview')" class="sec-body">
        <div class="kv-grid">
          <div v-for="row in overviewRows" :key="row.label" class="kv-row">
            <span class="kv-label mono">{{ row.label }}</span>
            <span class="kv-value mono">{{ row.value ?? "—" }}</span>
          </div>
        </div>
        <div v-if="redactions" class="redacted-note">
          {{ tm("devMode.notice.redacted", { count: redactions }) }}
        </div>
        <div v-if="envelope.truncated" class="truncated-note">
          {{
            tm("devMode.truncated", {
              size: formatSize(envelope.original_bytes),
            })
          }}
        </div>
      </div>
    </section>

    <div v-if="cacheMissWarning" class="cache-miss-warning" role="status">
      <span class="cache-miss-mark" aria-hidden="true">!</span>
      <div class="cache-miss-copy">
        <div class="cache-miss-title">{{ tm("devMode.cacheMiss.title") }}</div>
        <div class="cache-miss-body">{{ cacheMissText }}</div>
      </div>
    </div>

    <section
      class="sec sec-params"
      :class="{ collapsed: !isOpen('params') }"
    >
      <button
        type="button"
        class="sec-head"
        :aria-expanded="isOpen('params')"
        @click="toggleSection('params')"
      >
        <span class="sec-title">{{ tm("devMode.section.params") }}</span>
        <span class="sec-meta mono">{{ paramCount }}</span>
        <span class="sec-chevron" aria-hidden="true">›</span>
      </button>
      <div v-if="isOpen('params')" class="sec-body">
        <div v-if="requestParamRows.length" class="params-group">
          <div class="subhead mono">request</div>
          <div class="param-grid">
            <div
              v-for="row in requestParamRows"
              :key="row.label"
              class="param-row"
            >
              <span class="param-label mono">{{ row.label }}</span>
              <span class="param-value mono">{{ row.value }}</span>
            </div>
          </div>
        </div>
        <div v-if="providerParamRows.length" class="params-group">
          <div class="subhead mono">provider params</div>
          <div class="param-grid">
            <div
              v-for="row in providerParamRows"
              :key="row.label"
              class="param-row"
            >
              <span class="param-label mono">{{ row.label }}</span>
              <span class="param-value mono">{{ row.value }}</span>
            </div>
          </div>
        </div>
        <div v-if="extraParamRows.length" class="params-group">
          <div class="subhead mono">extra</div>
          <div class="param-grid">
            <div
              v-for="row in extraParamRows"
              :key="row.label"
              class="param-row"
            >
              <span class="param-label mono">{{ row.label }}</span>
              <span class="param-value mono">{{ row.value }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section
      class="sec sec-response"
      :class="{ collapsed: !isOpen('response') }"
    >
      <button
        type="button"
        class="sec-head"
        :aria-expanded="isOpen('response')"
        @click="toggleSection('response')"
      >
        <span class="sec-title">{{ tm("devMode.section.response") }}</span>
        <span class="sec-meta mono">{{
          str(response, "finish_reason") ?? "—"
        }}</span>
        <span class="sec-chevron" aria-hidden="true">›</span>
      </button>
      <div v-if="isOpen('response')" class="sec-body">
        <div class="kv-grid">
          <div v-for="row in responseRows" :key="row.label" class="kv-row">
            <span class="kv-label mono">{{ row.label }}</span>
            <span class="kv-value mono">{{ row.value ?? "—" }}</span>
          </div>
        </div>

        <div v-if="toolExecutions.length" class="tool-execs">
          <div class="subhead mono">tool_executions</div>
          <div
            v-for="(exec, index) in toolExecutions"
            :key="`${exec.call_id ?? exec.name}-${index}`"
            class="tool-exec"
          >
            <button
              type="button"
              class="tool-exec-row"
              :aria-expanded="expandedExec === index"
              @click="toggleExec(index)"
            >
              <span
                class="status-dot"
                :class="`is-${exec.status ?? 'unknown'}`"
                aria-hidden="true"
              />
              <span class="exec-name mono">{{ exec.name ?? "—" }}</span>
              <span class="exec-status mono">{{ exec.status ?? "unknown" }}</span>
              <span class="exec-duration mono">{{
                formatDuration(exec.duration_ms)
              }}</span>
              <span class="exec-bytes mono">{{
                formatSize(exec.result_bytes)
              }}</span>
              <span class="sec-chevron" aria-hidden="true">›</span>
            </button>
            <pre
              v-if="expandedExec === index"
              class="tool-exec-preview mono"
              >{{ exec.result_preview || "—" }}</pre
            >
          </div>
        </div>
      </div>
    </section>

    <section
      class="sec sec-system"
      :class="{ collapsed: !isOpen('system') }"
    >
      <button
        type="button"
        class="sec-head"
        :aria-expanded="isOpen('system')"
        @click="toggleSection('system')"
      >
        <span class="sec-title">{{ tm("devMode.section.system") }}</span>
        <span class="sec-meta mono">{{ systemText.length }} chars</span>
        <span class="sec-chevron" aria-hidden="true">›</span>
      </button>
      <div v-if="isOpen('system')" class="sec-body">
        <div v-if="provenance.length" class="prov-chips">
          <span
            v-for="(part, index) in provenance"
            :key="`${part.label}-${index}`"
            class="prov-chip"
            :class="{ 'is-approx': part.approx }"
          >
            <span class="prov-label mono">{{ part.label ?? "unknown" }}</span>
            <span class="prov-size mono">{{ part.chars ?? "—" }} ch</span>
            <span
              v-if="part.approx"
              class="prov-approx"
              :title="tm('devMode.provenance.approx')"
              >△</span
            >
          </span>
        </div>
        <div v-if="systemSource" class="system-source mono">
          source_field: {{ systemSource }}
        </div>
        <pre class="system-text mono">{{ systemText || "—" }}</pre>
      </div>
    </section>

    <section class="sec sec-tools" :class="{ collapsed: !isOpen('tools') }">
      <button
        type="button"
        class="sec-head"
        :aria-expanded="isOpen('tools')"
        @click="toggleSection('tools')"
      >
        <span class="sec-title">{{ tm("devMode.section.tools") }}</span>
        <span class="sec-meta mono">{{ tools.length }}</span>
        <span class="sec-chevron" aria-hidden="true">›</span>
      </button>
      <div v-if="isOpen('tools')" class="sec-body">
        <div class="tool-toolbar">
          <input
            v-model="toolQuery"
            type="search"
            class="tool-search mono"
            :placeholder="tm('devMode.tool.searchPlaceholder')"
            :aria-label="tm('devMode.tool.searchPlaceholder')"
          />
          <span class="tool-count mono"
            >{{ filteredTools.length }} / {{ tools.length }}</span
          >
        </div>

        <div v-if="!filteredTools.length" class="tool-empty">
          {{ tm("devMode.tool.empty") }}
        </div>

        <div
          v-for="(tool, index) in filteredTools"
          :key="`${tool.name}-${index}`"
          class="tool-row"
        >
          <button
            type="button"
            class="tool-row-head"
            :aria-expanded="expandedTool === toolKey(tool, index)"
            @click="toggleTool(toolKey(tool, index))"
          >
            <span class="tool-name mono">{{ tool.name ?? "—" }}</span>
            <span v-if="isNewTool(tool)" class="tag tag-new">new</span>
            <span v-if="toolOrigin(tool)" class="tag tag-origin">{{
              toolOrigin(tool)
            }}</span>
            <span v-if="toolDenied(tool) === true" class="tag tag-denied"
              >denied</span
            >
            <span v-else-if="toolDenied(tool) === null" class="tag tag-unknown"
              >denied: unknown</span
            >
            <span v-if="toolBackground(tool)" class="tag tag-bg"
              >background</span
            >
            <span class="tool-params mono"
              >{{ paramsCount(tool) }} params</span
            >
            <span class="sec-chevron" aria-hidden="true">›</span>
          </button>
          <pre
            v-if="expandedTool === toolKey(tool, index)"
            class="tool-schema mono"
            >{{ JSON.stringify(tool.parameters ?? {}, null, 2) }}</pre
          >
        </div>
      </div>
    </section>

    <section
      class="sec sec-messages"
      :class="{ collapsed: !isOpen('messages') }"
    >
      <button
        type="button"
        class="sec-head"
        :aria-expanded="isOpen('messages')"
        @click="toggleSection('messages')"
      >
        <span class="sec-title">{{ tm("devMode.section.messages") }}</span>
        <span class="sec-meta mono">{{ messages.length }}</span>
        <span class="sec-chevron" aria-hidden="true">›</span>
      </button>
      <div v-if="isOpen('messages')" class="sec-body">
        <div
          v-for="(message, index) in messages"
          :key="`${message.role}-${index}`"
          class="message-row"
        >
          <button
            type="button"
            class="message-row-head"
            :aria-expanded="isMessageExpanded(index)"
            @click="toggleMessage(index)"
          >
            <span class="role-chip mono">{{ message.role ?? "unknown" }}</span>
            <span v-if="message.source" class="message-source mono">{{
              message.source
            }}</span>
            <span v-else-if="message.name" class="message-source mono">{{
              message.name
            }}</span>
            <span class="message-meta mono"
              >{{ message.chars ?? messageText(message).length }} chars · ≈{{
                message.estimated_tokens ?? "—"
              }}
              tokens</span
            >
            <span class="sec-chevron" aria-hidden="true">›</span>
          </button>
          <div
            v-if="message.tool_calls?.length"
            class="message-tools mono"
          >
            {{
              message.tool_calls
                .map((call) => call.name ?? call.id ?? "tool")
                .join(", ")
            }}
          </div>
          <div
            v-if="isMessageExpanded(index)"
            class="message-text mono"
          >
            {{ messageText(message) }}
          </div>
          <div v-else class="message-preview mono">
            {{ messagePreview(message) }}
          </div>
        </div>
      </div>
    </section>

    <section
      class="sec sec-injections"
      :class="{ collapsed: !isOpen('injections') }"
    >
      <button
        type="button"
        class="sec-head"
        :aria-expanded="isOpen('injections')"
        @click="toggleSection('injections')"
      >
        <span class="sec-title">{{ tm("devMode.section.injections") }}</span>
        <span class="sec-meta mono">{{ injections.length }}</span>
        <span class="sec-chevron" aria-hidden="true">›</span>
      </button>
      <div v-if="isOpen('injections')" class="sec-body">
        <LlmRequestInjectionsPanel
          v-if="injections.length"
          :items="injections"
        />
        <div v-else class="sec-empty mono">—</div>
      </div>
    </section>

    <section
      class="sec sec-diagnostics"
      :class="{ collapsed: !isOpen('diagnostics') }"
    >
      <button
        type="button"
        class="sec-head"
        :aria-expanded="isOpen('diagnostics')"
        @click="toggleSection('diagnostics')"
      >
        <span class="sec-title">{{ tm("devMode.section.diagnostics") }}</span>
        <span class="sec-meta mono">—</span>
        <span class="sec-chevron" aria-hidden="true">›</span>
      </button>
      <div v-if="isOpen('diagnostics')" class="sec-body">
        <div class="diag-grid">
          <div v-for="row in diagnosticsRows" :key="row.label" class="diag-row">
            <span class="diag-label mono">{{ row.label }}</span>
            <span class="diag-value mono">{{ row.value }}</span>
          </div>
        </div>
      </div>
    </section>

    <section class="sec sec-raw" :class="{ collapsed: !isOpen('raw') }">
      <button
        type="button"
        class="sec-head"
        :aria-expanded="isOpen('raw')"
        @click="toggleSection('raw')"
      >
        <span class="sec-title">{{ tm("devMode.section.raw") }}</span>
        <span class="sec-meta mono">{{ formatSize(envelope.bytes) }}</span>
        <span class="sec-chevron" aria-hidden="true">›</span>
      </button>
      <div v-if="isOpen('raw')" class="sec-body">
        <pre class="raw-json mono">{{ rawJson }}</pre>
      </div>
    </section>
  </article>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";

import { useModuleI18n } from "@/i18n/composables";
import LlmRequestInjectionsPanel from "@/components/chat/LlmRequestInjectionsPanel.vue";
import type {
  LlmRequestSnapshotEnvelope,
  LlmSnapshotDetail,
} from "@/composables/llmRequestSnapshots";

const props = defineProps<{
  snapshot: LlmSnapshotDetail;
  previous?: LlmSnapshotDetail;
}>();

const emit = defineEmits<{ refresh: [] }>();

const { tm } = useModuleI18n("features/chat");

interface SnapshotSystem {
  text?: string;
  hash?: string;
  source_field?: string;
  parts?: unknown[];
}

interface SnapshotToolCall {
  id?: string;
  name?: string;
}

interface SnapshotMessage {
  index?: number;
  role?: string;
  name?: string | null;
  source?: string | null;
  content?: unknown;
  tool_calls?: SnapshotToolCall[];
  chars?: number;
  estimated_tokens?: number;
}

interface SnapshotTool {
  name?: string;
  description?: string;
  parameters?: unknown;
  origin?: string;
  active?: boolean;
  denied?: boolean | null;
  background?: boolean;
  schema_bytes?: number;
}

interface SnapshotView {
  system?: SnapshotSystem;
  messages?: SnapshotMessage[];
  tools?: SnapshotTool[];
  params?: Record<string, unknown>;
  extra?: Record<string, unknown>;
}

interface ProvenancePart {
  label?: string;
  chars?: number;
  approx?: boolean;
  span?: [number, number] | null;
}

interface ToolExecution {
  call_id?: string;
  name?: string;
  status?: string;
  duration_ms?: number;
  result_bytes?: number;
  result_preview?: string;
}

type SectionKey =
  | "diff"
  | "overview"
  | "params"
  | "response"
  | "system"
  | "tools"
  | "messages"
  | "injections"
  | "diagnostics"
  | "raw";

/** Reads a record-shaped payload field without trusting its index signature. */
function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function str(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function num(record: Record<string, unknown>, key: string): number | null {
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatSize(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  if (value < 1024) return `${value} B`;
  const kb = value / 1024;
  if (kb < 1024) {
    return `${kb >= 100 ? Math.round(kb) : kb.toFixed(1)} KB`;
  }
  return `${(kb / 1024).toFixed(1)} MB`;
}

function formatDuration(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(value < 10000 ? 2 : 1)} s`;
}

function formatSigned(value: number): string {
  if (value > 0) return `+${value}`;
  return String(value);
}

function formatRatio(value: number | null): string {
  return value === null ? "unknown" : `${Math.round(value * 100)}%`;
}

function formatParamValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}

const envelope = computed<LlmRequestSnapshotEnvelope>(
  () => props.snapshot.envelope,
);
const provider = computed(() => asRecord(envelope.value.provider));
const request = computed(() => asRecord(envelope.value.request));
const counts = computed(() => asRecord(envelope.value.counts));
const response = computed(() => asRecord(envelope.value.response));
const usage = computed(() => asRecord(response.value.usage));
const diagnostics = computed(() => asRecord(envelope.value.diagnostics));
const compression = computed(() => asRecord(diagnostics.value.compression));
const cache = computed(() => asRecord(diagnostics.value.cache));
const budget = computed(() => asRecord(diagnostics.value.budget));
const retries = computed(() => asRecord(diagnostics.value.retries));

const cacheMissWarning = computed(() => cache.value.miss_warning === true);
const cacheMissText = computed(() => {
  const hit = num(cache.value, "hit_ratio");
  return tm("devMode.cacheMiss.body", {
    prefix: formatRatio(num(cache.value, "prefix_match_ratio")),
    hit:
      hit === null ? tm("devMode.cacheMiss.unknown") : formatRatio(hit),
  });
});

const REQUEST_PARAM_KEYS = [
  "reasoning_effort",
  "temperature",
  "top_p",
  "max_tokens",
  "tool_choice",
  "stream",
  "timeout_s",
  "retry_max",
] as const;
const REQUEST_COMMON_KEYS = new Set<string>(REQUEST_PARAM_KEYS);

const view = computed(() => (props.snapshot.view ?? {}) as SnapshotView);
const system = computed(() => asRecord(view.value.system));
const messages = computed(() =>
  asArray<SnapshotMessage>(view.value.messages),
);
const tools = computed(() => asArray<SnapshotTool>(view.value.tools));
const requestParamRows = computed(() =>
  REQUEST_PARAM_KEYS.map((key) => ({
    label: key,
    value: formatParamValue(request.value[key]),
  })),
);
const providerParamRows = computed(() =>
  Object.entries(asRecord(view.value.params))
    .filter(([key]) => !REQUEST_COMMON_KEYS.has(key))
    .map(([label, value]) => ({ label, value: formatParamValue(value) })),
);
const extraParamRows = computed(() =>
  Object.entries(asRecord(view.value.extra)).map(([label, value]) => ({
    label,
    value: formatParamValue(value),
  })),
);
const paramCount = computed(
  () =>
    requestParamRows.value.length +
    providerParamRows.value.length +
    extraParamRows.value.length,
);
const provenance = computed(() =>
  asArray<ProvenancePart>(envelope.value.provenance),
);
const injections = computed(() => envelope.value.injections ?? []);
const toolExecutions = computed(() =>
  asArray<ToolExecution>(envelope.value.tool_executions),
);

const modelName = computed(
  () =>
    str(provider.value, "model") ??
    str(provider.value, "returned_model") ??
    str(response.value, "returned_model") ??
    "—",
);
const systemText = computed(() => str(system.value, "text") ?? "");
const systemSource = computed(() => str(system.value, "source_field"));
const redactions = computed(() => envelope.value.redactions ?? 0);

const KIND_KEYS: Record<string, string> = {
  chat: "devMode.kind.chat",
  compression: "devMode.kind.compression",
  title: "devMode.kind.title",
  aux: "devMode.kind.aux",
};

const kindKey = computed(() => KIND_KEYS[envelope.value.kind ?? "chat"]);
const kindLabel = computed(() =>
  kindKey.value ? tm(kindKey.value) : (envelope.value.kind ?? "chat"),
);

const title = computed(() => {
  const parts: string[] = [kindLabel.value];
  if (typeof envelope.value.step === "number") {
    parts.push(`step ${envelope.value.step}`);
  }
  return parts.join(" · ");
});

const subtitle = computed(() => {
  const attempt = num(request.value, "attempt");
  return [
    envelope.value.status,
    str(response.value, "finish_reason"),
    modelName.value,
    str(provider.value, "id"),
    str(provider.value, "host") ?? "—",
    attempt !== null ? `attempt ${attempt}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
});

const overviewRows = computed(() => [
  { label: "status", value: envelope.value.status },
  { label: "kind", value: envelope.value.kind ?? "chat" },
  { label: "model", value: modelName.value },
  { label: "provider", value: str(provider.value, "id") },
  { label: "host", value: str(provider.value, "host") },
  { label: "duration_ms", value: formatDuration(envelope.value.duration_ms) },
  { label: "bytes", value: formatSize(envelope.value.bytes) },
  { label: "attempt", value: num(request.value, "attempt") },
  { label: "messages", value: num(counts.value, "messages") },
  { label: "tools", value: num(counts.value, "tools") },
  { label: "system_chars", value: num(counts.value, "system_chars") },
  {
    label: "estimated_input_tokens",
    value: num(counts.value, "estimated_input_tokens"),
  },
  {
    label: "omitted_messages",
    value: num(counts.value, "omitted_messages"),
  },
]);

const totalTokens = computed(() => {
  const total = num(usage.value, "total");
  if (total !== null) return total;
  const other = num(usage.value, "input_other") ?? 0;
  const cached = num(usage.value, "input_cached") ?? 0;
  const output = num(usage.value, "output") ?? 0;
  return other + cached + output || null;
});

const responseRows = computed(() => [
  {
    label: "finish_reason",
    value: str(response.value, "finish_reason"),
  },
  { label: "response_id", value: str(response.value, "response_id") },
  {
    label: "ttft_ms",
    value:
      num(response.value, "ttft_ms") === null
        ? null
        : `${num(response.value, "ttft_ms")} ms`,
  },
  { label: "chunks", value: num(response.value, "chunks") },
  { label: "usage.input_other", value: num(usage.value, "input_other") },
  { label: "usage.input_cached", value: num(usage.value, "input_cached") },
  { label: "usage.output", value: num(usage.value, "output") },
  { label: "usage.total", value: totalTokens.value },
  { label: "usage_matches_request", value: response.value.usage_matches_request },
]);

const diagnosticsRows = computed(() => [
  {
    label: "compression.strategy",
    value: diagnosticValue(compression.value.strategy),
  },
  {
    label: "compression.tokens_before",
    value: diagnosticValue(compression.value.tokens_before),
  },
  {
    label: "compression.tokens_after",
    value: diagnosticValue(compression.value.tokens_after),
  },
  { label: "cache.hit_ratio", value: formatRatio(num(cache.value, "hit_ratio")) },
  {
    label: "cache.prefix_match_ratio",
    value: formatRatio(num(cache.value, "prefix_match_ratio")),
  },
  {
    label: "cache.prefix_warn_ratio",
    value: formatRatio(num(cache.value, "prefix_warn_ratio")),
  },
  {
    label: "cache.miss_warning",
    value: diagnosticValue(cache.value.miss_warning),
  },
  {
    label: "cache.system_changed",
    value: diagnosticValue(cache.value.system_changed),
  },
  {
    label: "cache.tools_changed",
    value: diagnosticValue(cache.value.tools_changed),
  },
  {
    label: "cache.divergence_index",
    value: diagnosticValue(cache.value.divergence_index),
  },
  { label: "budget.step_used", value: diagnosticValue(budget.value.step_used) },
  { label: "budget.step_max", value: diagnosticValue(budget.value.step_max) },
  {
    label: "budget.context_tokens",
    value: diagnosticValue(budget.value.context_tokens),
  },
  {
    label: "budget.context_limit",
    value: diagnosticValue(budget.value.context_limit),
  },
  {
    label: "budget.output_budget",
    value: diagnosticValue(budget.value.output_budget),
  },
  { label: "retries.count", value: diagnosticValue(retries.value.count) },
  {
    label: "retries.fallback",
    value: diagnosticValue(retries.value.fallback),
  },
  {
    label: "retries.image_fallback",
    value: diagnosticValue(retries.value.image_fallback),
  },
  {
    label: "retries.schema_dropped",
    value: diagnosticValue(retries.value.schema_dropped),
  },
  {
    label: "retries.history_popped",
    value: diagnosticValue(retries.value.history_popped),
  },
  { label: "stop_reason", value: diagnosticValue(diagnostics.value.stop_reason) },
  {
    label: "omitted_messages",
    value: diagnosticValue(diagnostics.value.omitted_messages),
  },
]);

/** Null means "unknown / not applicable", never a fake false. */
function diagnosticValue(value: unknown): string {
  if (value === null || value === undefined) return "unknown";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number" || typeof value === "string") {
    return String(value);
  }
  return JSON.stringify(value);
}

const openSections = ref<Record<SectionKey, boolean>>({
  diff: true,
  overview: true,
  params: true,
  response: true,
  system: true,
  tools: true,
  messages: true,
  injections: true,
  diagnostics: false,
  raw: false,
});

function isOpen(key: SectionKey): boolean {
  return openSections.value[key];
}

function toggleSection(key: SectionKey): void {
  openSections.value = {
    ...openSections.value,
    [key]: !openSections.value[key],
  };
}

const showDiff = computed(
  () =>
    Boolean(props.previous) &&
    typeof envelope.value.step === "number" &&
    envelope.value.step >= 2,
);

const diff = computed(() => {
  const previous = props.previous;
  if (!showDiff.value || !previous) return null;
  const previousView = (previous.view ?? {}) as SnapshotView;
  const previousMessages = asArray<SnapshotMessage>(previousView.messages);
  const previousTools = asArray<SnapshotTool>(previousView.tools);
  const previousToolNames = new Set(
    previousTools.map((tool) => tool.name ?? ""),
  );
  const currentToolNames = new Set(tools.value.map((tool) => tool.name ?? ""));
  const previousSystem = str(asRecord(previousView.system), "text") ?? "";
  return {
    messageDelta: messages.value.length - previousMessages.length,
    addedTools: tools.value
      .map((tool) => tool.name ?? "")
      .filter((name) => name && !previousToolNames.has(name)),
    removedTools: previousTools
      .map((tool) => tool.name ?? "")
      .filter((name) => name && !currentToolNames.has(name)),
    systemChanged: (systemText.value ?? "") !== previousSystem,
    paramsChanged:
      JSON.stringify(view.value.params ?? {}) !==
      JSON.stringify(previousView.params ?? {}),
    warning: Boolean(
      envelope.value.truncated || diagnostics.value.compression,
    ),
  };
});

const toolQuery = ref("");
const expandedTool = ref<string | null>(null);
const expandedExec = ref<number | null>(null);
const expandedMessages = ref<number[]>([]);

const filteredTools = computed(() => {
  const query = toolQuery.value.trim().toLowerCase();
  if (!query) return tools.value;
  return tools.value.filter((tool) => {
    const haystack = [
      tool.name ?? "",
      tool.description ?? "",
      tool.origin ?? "",
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });
});

function toolKey(tool: SnapshotTool, index: number): string {
  return `${tool.name ?? "tool"}:${index}`;
}

function toggleTool(key: string): void {
  expandedTool.value = expandedTool.value === key ? null : key;
}

function toggleExec(index: number): void {
  expandedExec.value = expandedExec.value === index ? null : index;
}

function paramsCount(tool: SnapshotTool): number {
  const properties = asRecord(asRecord(tool.parameters).properties);
  return Object.keys(properties).length;
}

function toolMetaFor(tool: SnapshotTool): Record<string, unknown> {
  const all = asRecord(envelope.value.tool_meta);
  return asRecord(all[tool.name ?? ""]);
}

function toolOrigin(tool: SnapshotTool): string | null {
  return tool.origin ?? str(toolMetaFor(tool), "origin");
}

function toolDenied(tool: SnapshotTool): boolean | null {
  if (typeof tool.denied === "boolean") return tool.denied;
  const value = toolMetaFor(tool).denied;
  return typeof value === "boolean" ? value : null;
}

function toolBackground(tool: SnapshotTool): boolean {
  return tool.background === true || toolMetaFor(tool).background === true;
}

function isNewTool(tool: SnapshotTool): boolean {
  return Boolean(diff.value?.addedTools.includes(tool.name ?? ""));
}

function isMessageExpanded(index: number): boolean {
  return expandedMessages.value.includes(index);
}

function toggleMessage(index: number): void {
  expandedMessages.value = isMessageExpanded(index)
    ? expandedMessages.value.filter((item) => item !== index)
    : [...expandedMessages.value, index];
}

function messageText(message: SnapshotMessage): string {
  const content = message.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === "string") return part;
        const record = asRecord(part);
        const text = str(record, "text");
        if (text !== null) return text;
        const type = str(record, "type");
        return type !== null ? `[${type}]` : "[part]";
      })
      .join("\n");
  }
  if (content === null || content === undefined) return "";
  return JSON.stringify(content, null, 2);
}

const PREVIEW_LIMIT = 180;

function messagePreview(message: SnapshotMessage): string {
  const text = messageText(message);
  if (text.length <= PREVIEW_LIMIT) return text;
  return `${text.slice(0, PREVIEW_LIMIT)}…`;
}

const rawJson = computed(() =>
  JSON.stringify(
    {
      envelope: props.snapshot.envelope,
      raw: props.snapshot.raw,
      view: props.snapshot.view,
    },
    null,
    2,
  ),
);

// ── Copy / download ─────────────────────────────────────────────────

type ActionState =
  | "idle"
  | "copied"
  | "copy-failed"
  | "downloaded"
  | "download-failed";

const FEEDBACK_KEYS: Record<Exclude<ActionState, "idle">, string> = {
  copied: "copy.copied",
  "copy-failed": "devMode.copy.failed",
  downloaded: "devMode.download.done",
  "download-failed": "devMode.download.failed",
};

const actionState = ref<ActionState>("idle");
let feedbackTimer: ReturnType<typeof setTimeout> | null = null;

const feedbackText = computed(() =>
  actionState.value === "idle" ? "" : tm(FEEDBACK_KEYS[actionState.value]),
);

function showActionFeedback(state: Exclude<ActionState, "idle">): void {
  actionState.value = state;
  if (feedbackTimer !== null) clearTimeout(feedbackTimer);
  feedbackTimer = setTimeout(() => {
    actionState.value = "idle";
    feedbackTimer = null;
  }, 1200);
}

async function writeClipboard(text: string): Promise<void> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  const copied = typeof document.execCommand === "function"
    ? document.execCommand("copy")
    : false;
  area.remove();
  if (!copied) throw new Error("copy failed");
}

async function copyJson(): Promise<void> {
  try {
    await writeClipboard(rawJson.value);
    showActionFeedback("copied");
  } catch {
    showActionFeedback("copy-failed");
  }
}

function downloadJson(): void {
  if (typeof URL === "undefined" || typeof URL.createObjectURL !== "function") {
    showActionFeedback("download-failed");
    return;
  }
  try {
    const blob = new Blob([rawJson.value], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${envelope.value.snapshot_id || "llm-snapshot"}.json`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    showActionFeedback("downloaded");
  } catch {
    showActionFeedback("download-failed");
  }
}

onBeforeUnmount(() => {
  if (feedbackTimer !== null) clearTimeout(feedbackTimer);
});
</script>

<style scoped>
.snapshot-detail {
  --sd-border: var(--chat-border, rgba(var(--v-theme-on-surface), 0.14));
  --sd-muted: rgba(var(--v-theme-on-surface), 0.56);
  --sd-faint: rgba(var(--v-theme-on-surface), 0.06);
  color: rgb(var(--v-theme-on-surface));
  font-size: 12px;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono",
    monospace;
  font-variant-ligatures: none;
}

.detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--sd-border);
}

.detail-heading {
  min-width: 0;
}

.detail-title {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.detail-sub {
  margin-top: 3px;
  color: var(--sd-muted);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 10.5px;
}

.badge-truncated {
  display: inline-block;
  margin-top: 6px;
  padding: 1px 6px;
  border: 1px solid rgba(var(--v-theme-warning), 0.45);
  border-radius: 3px;
  color: rgb(var(--v-theme-warning));
  font-size: 10.5px;
}

.detail-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.detail-action {
  padding: 3px 8px;
  border: 1px solid var(--sd-border);
  border-radius: 4px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}

.detail-action:hover {
  background: var(--sd-faint);
}

.copy-feedback {
  color: var(--sd-muted);
  font-size: 10.5px;
}

.sec {
  margin-top: 12px;
}

.sec-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  width: 100%;
  padding: 5px 0;
  border: 0;
  border-bottom: 1px solid var(--sd-border);
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.sec-title {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.sec-meta {
  margin-left: auto;
  color: var(--sd-muted);
  font-size: 10px;
}

.sec-chevron {
  color: rgba(var(--v-theme-on-surface), 0.38);
  font-size: 13px;
  line-height: 1;
  transform: rotate(90deg);
  transition: transform 0.12s ease-out;
}

.sec.collapsed .sec-chevron {
  transform: rotate(0deg);
}

.sec-body {
  padding-top: 8px;
}

.kv-grid,
.diff-grid,
.diag-grid {
  display: grid;
  gap: 4px 16px;
}

.kv-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.kv-row,
.diff-row,
.diag-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
  padding: 2px 0;
}

.kv-label,
.diff-label,
.diag-label {
  flex-shrink: 0;
  color: var(--sd-muted);
  font-size: 10.5px;
  letter-spacing: 0.02em;
}

.kv-value,
.diff-value,
.diag-value {
  min-width: 0;
  margin-left: auto;
  overflow-wrap: anywhere;
  font-size: 11.5px;
  text-align: right;
}

.redacted-note,
.truncated-note,
.diff-warning {
  margin-top: 6px;
  color: var(--sd-muted);
  font-size: 10.5px;
}

.truncated-note {
  color: rgb(var(--v-theme-warning));
}

.cache-miss-warning {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin: 0 12px;
  padding: 8px 10px;
  border-left: 2px solid rgb(var(--v-theme-warning));
  background: rgba(var(--v-theme-warning), 0.06);
}

.cache-miss-mark {
  color: rgb(var(--v-theme-warning));
  font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono",
    monospace;
  font-size: 11px;
  font-weight: 700;
  line-height: 1.3;
}

.cache-miss-title {
  color: rgb(var(--v-theme-on-surface));
  font-size: 11px;
  font-weight: 600;
}

.cache-miss-body {
  margin-top: 2px;
  color: var(--sd-muted);
  font-size: 10.5px;
  line-height: 1.45;
}

.params-group + .params-group {
  margin-top: 10px;
}

.param-grid {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.param-row {
  display: grid;
  grid-template-columns: minmax(120px, 0.55fr) minmax(0, 1.45fr);
  gap: 10px;
  padding: 2px 0;
  border-bottom: 1px solid var(--sd-faint);
}

.param-label {
  color: var(--sd-muted);
  font-size: 10.5px;
}

.param-value {
  min-width: 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  font-size: 11px;
}

.sec-diff.is-warning .sec-head {
  border-bottom-color: rgba(var(--v-theme-warning), 0.45);
}

.diff-warning {
  color: rgb(var(--v-theme-warning));
}

.diff-added {
  color: rgb(var(--v-theme-success));
}

.diff-removed {
  color: rgb(var(--v-theme-warning));
}

.diff-same {
  color: var(--sd-muted);
}

.subhead {
  margin: 10px 0 4px;
  color: var(--sd-muted);
  font-size: 10px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.tool-exec-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 4px 0;
  border: 0;
  border-bottom: 1px solid var(--sd-border);
  background: none;
  color: inherit;
  font: inherit;
  font-size: 11px;
  text-align: left;
  cursor: pointer;
}

.tool-exec-row:last-of-type {
  border-bottom: 0;
}

.status-dot {
  width: 6px;
  height: 6px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--sd-muted);
}

.status-dot.is-ok {
  background: rgb(var(--v-theme-success));
}

.status-dot.is-error,
.status-dot.is-denied {
  background: rgb(var(--v-theme-warning));
}

.exec-name {
  font-weight: 600;
}

.exec-status,
.exec-duration,
.exec-bytes {
  color: var(--sd-muted);
}

.exec-duration {
  margin-left: auto;
}

.tool-exec-preview,
.tool-schema,
.raw-json {
  margin: 4px 0 0;
  padding: 7px 9px;
  border-radius: 4px;
  background: var(--sd-faint);
  font-size: 11px;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 220px;
  overflow: auto;
  user-select: text;
}

.prov-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 6px;
}

.prov-chip {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  padding: 1px 6px;
  border: 1px solid var(--sd-border);
  border-radius: 3px;
  font-size: 10.5px;
}

.prov-label {
  font-weight: 600;
}

.prov-size {
  color: var(--sd-muted);
}

.prov-approx {
  color: rgb(var(--v-theme-warning));
}

.system-source {
  margin-bottom: 5px;
  color: var(--sd-muted);
  font-size: 10.5px;
}

.system-text {
  margin: 0;
  padding: 7px 9px;
  border-radius: 4px;
  background: var(--sd-faint);
  font-size: 11px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 124px;
  overflow: auto;
  user-select: text;
}

.tool-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.tool-search {
  flex: 1;
  min-width: 0;
  padding: 4px 7px;
  border: 1px solid var(--sd-border);
  border-radius: 4px;
  background: transparent;
  color: inherit;
  font-size: 11px;
  outline: none;
}

.tool-search:focus {
  border-color: rgba(var(--v-theme-primary), 0.6);
}

.tool-count {
  color: var(--sd-muted);
  font-size: 10.5px;
}

.tool-row {
  border-bottom: 1px solid var(--sd-border);
}

.tool-row:last-child {
  border-bottom: 0;
}

.tool-row-head {
  display: flex;
  align-items: baseline;
  gap: 6px;
  width: 100%;
  padding: 5px 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.tool-name {
  font-size: 11.5px;
  font-weight: 600;
}

.tag {
  padding: 0 4px;
  border: 1px solid var(--sd-border);
  border-radius: 3px;
  color: var(--sd-muted);
  font-size: 9.5px;
  line-height: 1.5;
}

.tag-denied {
  border-color: rgba(var(--v-theme-warning), 0.5);
  color: rgb(var(--v-theme-warning));
}

.tag-new {
  border-color: rgba(var(--v-theme-primary), 0.55);
  color: rgb(var(--v-theme-primary));
}

.tag-unknown {
  border-style: dashed;
}

.tag-bg {
  border-style: dashed;
}

.tool-params {
  margin-left: auto;
  color: var(--sd-muted);
  font-size: 10.5px;
}

.tool-empty,
.sec-empty {
  padding: 6px 0;
  color: var(--sd-muted);
  font-size: 11px;
}

.message-row {
  border-bottom: 1px solid var(--sd-border);
  padding-bottom: 4px;
}

.message-row:last-child {
  border-bottom: 0;
}

.message-row-head {
  display: flex;
  align-items: baseline;
  gap: 7px;
  width: 100%;
  padding: 5px 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.role-chip {
  padding: 0 5px;
  border: 1px solid var(--sd-border);
  border-radius: 3px;
  font-size: 9.5px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.message-source {
  color: var(--sd-muted);
  font-size: 10.5px;
}

.message-meta {
  margin-left: auto;
  color: var(--sd-muted);
  font-size: 10px;
}

.message-tools {
  color: var(--sd-muted);
  font-size: 10.5px;
  padding-left: 2px;
}

.message-preview {
  color: var(--sd-muted);
  font-size: 11px;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.message-text {
  font-size: 11px;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 112px;
  overflow: auto;
  user-select: text;
}

.diag-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.diag-label {
  color: var(--sd-muted);
}

@media (max-width: 700px) {
  .kv-grid,
  .diag-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
