<template>
  <div class="subagent-run-block" :class="{ 'is-dark': isDark }">
    <button class="subagent-run-header" type="button" @click="toggleExpanded">
      <span class="status-dot" :class="`status-${statusClass}`" />
      <v-icon size="18" class="agent-icon">mdi-robot-outline</v-icon>
      <span class="agent-name">{{ part.agent_name || "subagent" }}</span>
      <span class="status-label">{{ statusLabel }}</span>
      <span v-if="durationText" class="duration">{{ durationText }}</span>
      <v-icon class="expand-icon" size="18">
        {{ expanded ? "mdi-chevron-up" : "mdi-chevron-down" }}
      </v-icon>
    </button>

    <v-expand-transition>
      <div v-show="expanded" class="subagent-run-body">
        <div
          v-if="part.input_preview"
          class="subagent-section subagent-section-task"
        >
          <button
            class="section-header"
            type="button"
            @click="taskSectionExpanded = !taskSectionExpanded"
          >
            <span class="section-label">{{ tm("subagentSections.task") }}</span>
            <v-icon size="16">
              {{ taskSectionExpanded ? "mdi-chevron-up" : "mdi-chevron-down" }}
            </v-icon>
          </button>
          <div v-show="taskSectionExpanded" class="section-content">
            <div class="section-text">{{ displayedTaskText }}</div>
            <button
              v-if="hasFullTaskText"
              class="task-expand-toggle"
              type="button"
              @click="taskExpanded = !taskExpanded"
            >
              {{
                taskExpanded
                  ? tm("subagentTask.collapse")
                  : tm("subagentTask.expand")
              }}
            </button>
          </div>
        </div>

        <div
          v-if="activityParts.length"
          class="subagent-section subagent-section-execution"
        >
          <button
            class="section-header"
            type="button"
            @click="executionSectionExpanded = !executionSectionExpanded"
          >
            <span class="section-label">{{
              tm("subagentSections.execution")
            }}</span>
            <v-icon size="16">
              {{
                executionSectionExpanded ? "mdi-chevron-up" : "mdi-chevron-down"
              }}
            </v-icon>
          </button>
          <div v-show="executionSectionExpanded" class="section-content">
            <ReasoningTimeline
              :parts="activityParts"
              :is-dark="isDark"
              :is-streaming="part.status === 'running'"
            />
          </div>
        </div>

        <div v-if="part.text" class="subagent-section subagent-section-result">
          <button
            class="section-header"
            type="button"
            @click="resultSectionExpanded = !resultSectionExpanded"
          >
            <span class="section-label">{{
              tm("subagentSections.result")
            }}</span>
            <v-icon size="16">
              {{
                resultSectionExpanded ? "mdi-chevron-up" : "mdi-chevron-down"
              }}
            </v-icon>
          </button>
          <div v-show="resultSectionExpanded" class="section-content">
            <MarkdownMessagePart
              :content="part.text"
              :is-dark="isDark"
              :is-streaming="part.status === 'running'"
            />
          </div>
        </div>

        <div v-if="part.error" class="error-text">{{ part.error }}</div>
      </div>
    </v-expand-transition>

    <!-- Always-visible collapse affordance. Sticks to the bottom of the
         visible scroll viewport while this card is taller than it, so a
         long streaming run can be collapsed without scrolling back up. -->
    <button
      v-if="expanded"
      class="subagent-collapse-fab"
      type="button"
      :title="tm('subagentCollapse.hint')"
      :aria-label="tm('subagentCollapse.hint')"
      @click="expanded = false"
    >
      <v-icon size="16">mdi-chevron-up</v-icon>
    </button>
  </div>
</template>

<script setup>
// Author: elecvoid243
// Date: 2026-07-26
// Plan: docs/superpowers/plans/2026-07-26-subagent-chatui-progress.md (Task 6)
// Collapsible block rendering one `subagent_run` message part (live stream
// or persisted history). Reuses ReasoningBlock / MarkdownMessagePart /
// ToolCallCard for visual consistency with the main agent output.
import { computed, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import ReasoningTimeline from "@/components/chat/message_list_comps/ReasoningTimeline.vue";
import MarkdownMessagePart from "@/components/chat/message_list_comps/MarkdownMessagePart.vue";

const props = defineProps({
  part: {
    type: Object,
    required: true,
  },
  isDark: {
    type: Boolean,
    default: false,
  },
});

const { tm } = useModuleI18n("features/chat");

// Folded by default — a streaming subagent run can grow far past the
// viewport, so the user opts into the detail via the header and folds it
// back with the sticky button. The card never auto-expands or auto-folds.
const expanded = ref(false);

// A finished run should not keep its execution timeline open behind the
// result, so fold that inner section once the run stops running. The card
// itself stays exactly as the user left it: expansion is user-controlled
// (the header toggles it, the sticky button folds it back).
watch(
  () => props.part.status,
  (status, prev) => {
    if (prev === "running" && status !== "running") {
      executionSectionExpanded.value = false;
    }
  },
);

function toggleExpanded() {
  expanded.value = !expanded.value;
}

const taskExpanded = ref(false);
const taskSectionExpanded = ref(false);
const executionSectionExpanded = ref(props.part.status === "running");
const resultSectionExpanded = ref(true);

const hasFullTaskText = computed(() => {
  const full = String(props.part.input_full || "");
  return full.length > String(props.part.input_preview || "").length;
});

const displayedTaskText = computed(() =>
  taskExpanded.value && hasFullTaskText.value
    ? props.part.input_full
    : props.part.input_preview,
);

const statusClass = computed(() => {
  const status = props.part.status;
  if (status === "completed") return "completed";
  if (status === "failed" || status === "timeout") return "failed";
  return "running";
});

const statusLabel = computed(() => {
  const status = props.part.status;
  if (status === "completed") return tm("subagentStatus.completed");
  if (status === "failed") return tm("subagentStatus.failed");
  if (status === "timeout") return tm("subagentStatus.timeout");
  return tm("subagentStatus.running");
});

const durationText = computed(() => {
  const seconds = props.part.execution_time;
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  return `${seconds.toFixed(1)}s`;
});

const normalizedToolCalls = computed(() =>
  (props.part.tool_calls || []).map((tool) => {
    const normalized = { ...tool };
    normalized.args = normalized.args ?? normalized.arguments ?? {};
    if (normalized.result && typeof normalized.result === "object") {
      normalized.result = JSON.stringify(normalized.result, null, 2);
    }
    // ToolCallCard treats `finished_ts` as the done marker.
    if (normalized.result != null && !normalized.finished_ts) {
      normalized.ts = normalized.ts ?? Date.now() / 1000;
      normalized.finished_ts = normalized.ts;
    }
    return normalized;
  }),
);

const activityParts = computed(() => {
  const parts = [];
  // Prefer the chronological activity log (think/tool_call interleaved as
  // the LLM loop produced them). History records saved before this field
  // existed fall back to the aggregated reasoning + tool_calls view.
  const activity = props.part.activity;
  if (Array.isArray(activity) && activity.length) {
    let pendingTools = [];
    for (const entry of activity) {
      if (entry.kind === "think" || entry.kind === "text") {
        if (pendingTools.length) {
          parts.push({ type: "tool_call", tool_calls: pendingTools });
          pendingTools = [];
        }
        parts.push({ type: entry.kind, think: entry.text });
      } else if (entry.kind === "tool_call") {
        pendingTools.push(entry.call);
      }
    }
    if (pendingTools.length) {
      parts.push({ type: "tool_call", tool_calls: pendingTools });
    }
    return parts;
  }
  if (props.part.reasoning) {
    parts.push({ type: "think", think: props.part.reasoning });
  }
  if (normalizedToolCalls.value.length) {
    parts.push({ type: "tool_call", tool_calls: normalizedToolCalls.value });
  }
  return parts;
});
</script>

<style scoped>
.subagent-run-block {
  margin: 6px 0;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 8px;
  /* `clip` (not `hidden`) so this card is NOT a scroll container: the
     sticky collapse button below must resolve against the chat scroll
     container (.messages-panel), otherwise `hidden` would trap it in
     the card's own non-scrolling scrollport and it would never move. */
  overflow: clip;
}

.subagent-run-block.is-dark {
  border-color: rgba(255, 255, 255, 0.12);
}

.subagent-run-header {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  background: none;
  border: none;
  cursor: pointer;
  font: inherit;
  color: inherit;
  text-align: left;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.status-running {
  background: #4caf50;
  animation: pulse 1.2s ease-in-out infinite;
}

.status-completed {
  background: #4caf50;
}

.status-failed {
  background: #e57373;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.3;
  }
}

.agent-icon {
  opacity: 0.7;
}

.agent-name {
  font-weight: 600;
  font-size: 0.9em;
}

.status-label {
  font-size: 0.82em;
  opacity: 0.65;
}

.duration {
  font-size: 0.82em;
  opacity: 0.55;
}

.expand-icon {
  margin-left: auto;
  opacity: 0.5;
}

.subagent-run-body {
  padding: 4px 12px 10px;
  border-top: 1px solid rgba(0, 0, 0, 0.06);
}

.is-dark .subagent-run-body {
  border-top-color: rgba(255, 255, 255, 0.1);
}

/* Sticky collapse affordance (visible only while the card is expanded).
   2026-09-08: a long streaming subagent run pushes the card past the
   viewport bottom, so the card header's chevron is off-screen by the
   time the user wants to fold it. This button stays pinned 12 px above
   the bottom edge of the scroll viewport while the card is taller than
   the viewport, then it settles in the card's bottom-right corner. The
   negative top margin cancels the button's own height so the card keeps
   its previous size and the button reads as floating over the content
   instead of pushing the layout. */
.subagent-collapse-fab {
  position: sticky;
  bottom: 12px;
  z-index: 3;
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  width: 28px;
  height: 28px;
  margin: -28px 10px 0 auto;
  padding: 0;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: 50%;
  background: var(--chat-page-bg, rgb(var(--v-theme-surface)));
  color: rgba(var(--v-theme-on-surface), 0.6);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.14);
  cursor: pointer;
  transition:
    color 0.15s ease,
    border-color 0.15s ease;
}

.subagent-collapse-fab:hover {
  color: rgb(var(--v-theme-primary));
  border-color: rgba(var(--v-theme-primary), 0.45);
}

.is-dark .subagent-collapse-fab {
  border-color: rgba(255, 255, 255, 0.16);
  box-shadow: 0 1px 5px rgba(0, 0, 0, 0.5);
}

.section-label {
  font-size: 0.78em;
  opacity: 0.55;
  margin-bottom: 2px;
}

.section-text {
  font-size: 0.88em;
  opacity: 0.8;
  white-space: pre-wrap;
  word-break: break-word;
}

.subagent-section {
  margin: 6px 0;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 4px 0;
  background: none;
  border: none;
  cursor: pointer;
  color: inherit;
  text-align: left;
}

.section-header .section-label {
  margin: 0;
}

.section-content {
  padding: 4px 0 2px;
}

.input-preview {
  margin: 0;
}

.task-expand-toggle {
  display: inline-block;
  margin-top: 2px;
  padding: 0;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.78em;
  color: rgba(var(--v-theme-primary, 25, 118, 210), 0.85);
}

.error-text {
  margin-top: 6px;
  font-size: 0.85em;
  color: #e57373;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
