<template>
  <div
    ref="rootEl"
    class="subagent-run-block"
    :class="{ 'is-dark': isDark, 'is-locating': locating }"
  >
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
              collapse-think
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

        <!-- Follow-up input: only inside the expanded body, so a folded card
             offers no accidental input target. -->
        <div
          v-if="part.status === 'running' && sessionId && expanded"
          class="subagent-follow-up"
        >
          <input
            v-model="followUpText"
            data-testid="subagent-follow-up-input"
            class="subagent-follow-up-input"
            type="text"
            :placeholder="
              tm('subagentFollowUp.placeholder', {
                name: part.agent_name || 'subagent',
              })
            "
            :disabled="followUpSending"
            @keydown.enter.prevent="sendFollowUp"
          />
          <button
            class="subagent-follow-up-send"
            data-testid="subagent-follow-up-send"
            type="button"
            :disabled="followUpSending || !followUpText.trim()"
            :title="tm('subagentFollowUp.send')"
            :aria-label="tm('subagentFollowUp.send')"
            @click="sendFollowUp"
          >
            <v-icon size="16">mdi-send-outline</v-icon>
          </button>
        </div>
        <div v-if="followUpNotice" class="subagent-follow-up-notice">
          {{ followUpNotice }}
        </div>
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
      @click="collapseAndLocate"
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
import { computed, inject, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import { postSubagentFollowUp } from "@/api/generated/openapi-v1";
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
  // Live webchat session id; enables the follow-up input while the run is
  // active. Empty (e.g. transcript views) hides the input.
  sessionId: {
    type: String,
    default: "",
  },
});

const { tm } = useModuleI18n("features/chat");

const rootEl = ref(null);

// ChatUI's messages panel owns every scroll decision (stick-to-bottom, the
// reading anchor, search jumps). A card that folds asks it to centre the card
// through this hook; renderers without a panel (thread panel, standalone web
// chat) provide nothing and fall back to scrolling their own container.
const centerInMessagesPanel = inject("centerInMessagesPanel", null);

// Folded by default — a streaming subagent run can grow far past the
// viewport, so the user opts into the detail via the header and folds it
// back with the sticky button. The card never auto-expands or auto-folds.
const expanded = ref(false);

// Locate affordance state. Folding a tall card shrinks the document under the
// reader, so the fold button also asks to be re-centred and flashes the card
// for 1.5 s — the reader pressed it from somewhere deep inside the card and
// has no other cue for where that card went.
const locating = ref(false);
let locatingTimer = null;
let foldSettleTimer = null;
let foldSettleCleanup = null;

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
  // A header click takes the card's position back under the user's control;
  // a landing still waiting on the fold would otherwise move the viewport
  // after they re-opened the card.
  cancelFoldWait();
  expanded.value = !expanded.value;
  if (expanded.value) {
    // Re-opening makes the locate flash meaningless — it would light up the
    // whole expanded body instead of naming a folded card.
    window.clearTimeout(locatingTimer);
    locating.value = false;
  }
}

/**
 * Fold the card, then bring it back to the middle of the chat panel and flash
 * it. The flash starts with the landing: while the card is still folded away
 * under the reader's cursor there is nothing to see — the pulse is what names
 * the folded card once it arrives.
 */
function collapseAndLocate() {
  const body = rootEl.value?.querySelector(".subagent-run-body");
  expanded.value = false;
  whenFoldSettled(body, () => {
    centerCard();
    flashLocating();
  });
}

/**
 * Run `callback` once the expand transition has finished shrinking the body.
 *
 * Centring any earlier measures the still-expanded card: the fold is a height
 * transition, so the offset that centres the card keeps moving until it ends.
 *
 * @param body: Collapsing body element, or null when it never rendered.
 * @param callback: Runs exactly once — on transitionend, or after a bound wait
 *   when no CSS transition applies (reduced motion, overridden durations).
 */
function whenFoldSettled(body, callback) {
  cancelFoldWait();
  if (!body) {
    callback();
    return;
  }
  const finish = () => {
    cancelFoldWait();
    callback();
  };
  const onTransitionEnd = (event) => {
    if (event.target === body && event.propertyName === "height") finish();
  };
  body.addEventListener("transitionend", onTransitionEnd);
  foldSettleCleanup = () =>
    body.removeEventListener("transitionend", onTransitionEnd);
  foldSettleTimer = window.setTimeout(finish, 400);
}

/** Stop waiting for the fold; the card keeps whatever scroll it has. */
function cancelFoldWait() {
  window.clearTimeout(foldSettleTimer);
  foldSettleTimer = null;
  if (foldSettleCleanup) {
    foldSettleCleanup();
    foldSettleCleanup = null;
  }
}

/** Restart the 1.5 s locate flash, including when the button is pressed twice. */
function flashLocating() {
  window.clearTimeout(locatingTimer);
  // Drop the class for one paint so re-adding it restarts the animation
  // instead of being a no-op on an element that still carries it.
  locating.value = false;
  void nextTick(() => {
    locating.value = true;
    locatingTimer = window.setTimeout(() => {
      locating.value = false;
    }, 1500);
  });
}

/** Centre this card: the chat panel's coordinator when present, else locally. */
function centerCard() {
  const element = rootEl.value;
  if (!element) return;
  if (
    typeof centerInMessagesPanel === "function" &&
    centerInMessagesPanel(element)
  ) {
    return;
  }
  // No coordinator, or one that does not own this card (a thread panel beside
  // the main conversation): scroll the card's own container.
  const container = scrollParentOf(element);
  if (!container) return;
  const containerRect = container.getBoundingClientRect();
  const cardRect = element.getBoundingClientRect();
  const centered =
    container.scrollTop +
    (cardRect.top + cardRect.height / 2) -
    (containerRect.top + container.clientHeight / 2);
  container.scrollTo({
    top: Math.max(
      0,
      Math.min(centered, container.scrollHeight - container.clientHeight),
    ),
    behavior: "smooth",
  });
}

/**
 * Closest ancestor that scrolls vertically — the messages panel in the chat
 * page, `.standalone-messages` in the standalone web chat.
 *
 * @param element: Element to walk up from.
 * @returns The scrolling ancestor, or null when only the page scrolls.
 */
function scrollParentOf(element) {
  let node = element.parentElement;
  while (node && node !== document.body) {
    if (/^(auto|scroll|overlay)$/.test(getComputedStyle(node).overflowY)) {
      return node;
    }
    node = node.parentElement;
  }
  return null;
}

const taskExpanded = ref(false);
const taskSectionExpanded = ref(false);
const executionSectionExpanded = ref(props.part.status === "running");
const resultSectionExpanded = ref(true);

// Follow-up input state. Submissions go straight to the delivery endpoint;
// the accepted message renders via the SSE `user_message` echo, so the field
// is only cleared on acceptance and never appended optimistically.
const followUpText = ref("");
const followUpSending = ref(false);
const followUpNotice = ref("");

async function sendFollowUp() {
  const text = followUpText.value.trim();
  if (!text || followUpSending.value || !props.sessionId) return;
  followUpSending.value = true;
  followUpNotice.value = "";
  try {
    const resp = await postSubagentFollowUp({
      body: {
        session_id: props.sessionId,
        subagent_run_id: props.part.subagent_run_id,
        text,
      },
    });
    if (resp.data?.data?.accepted) {
      followUpText.value = "";
    } else {
      followUpNotice.value = tm("subagentFollowUp.notDelivered");
    }
  } catch {
    followUpNotice.value = tm("subagentFollowUp.notDelivered");
  } finally {
    followUpSending.value = false;
  }
}

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
      } else if (entry.kind === "user_message") {
        if (pendingTools.length) {
          parts.push({ type: "tool_call", tool_calls: pendingTools });
          pendingTools = [];
        }
        parts.push({
          type: "user_message",
          userText: entry.text,
          relayed: Boolean(entry.relayed),
        });
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

onBeforeUnmount(() => {
  window.clearTimeout(locatingTimer);
  cancelFoldWait();
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

/* Locate flash: the fold button re-centres the card, then three 0.5 s pulses
   (1.5 s in total) name the spot instead of leaving the reader to guess which
   of the surrounding cards just closed. Only the background and an outside
   ring animate, so the resting border (and its dark-mode variant) is left
   exactly as it is. */
.subagent-run-block.is-locating {
  animation: subagent-locate-flash 0.5s ease-in-out 3;
}

@keyframes subagent-locate-flash {
  0%,
  100% {
    background-color: transparent;
    box-shadow: 0 0 0 0 rgba(var(--v-theme-primary), 0);
  }
  50% {
    background-color: rgba(var(--v-theme-primary), 0.1);
    box-shadow: 0 0 0 3px rgba(var(--v-theme-primary), 0.22);
  }
}

@media (prefers-reduced-motion: reduce) {
  .subagent-run-block.is-locating {
    animation: none;
    background-color: rgba(var(--v-theme-primary), 0.1);
    box-shadow: 0 0 0 2px rgba(var(--v-theme-primary), 0.3);
  }
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

.subagent-follow-up {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 8px 10px 10px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: 8px;
  padding: 4px 6px 4px 12px;
}

.subagent-follow-up-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: 0.85em;
  color: inherit;
}

.subagent-follow-up-send {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.6);
  cursor: pointer;
}

.subagent-follow-up-send:hover:not(:disabled) {
  color: rgb(var(--v-theme-primary));
}

.subagent-follow-up-send:disabled {
  opacity: 0.4;
  cursor: default;
}

.subagent-follow-up-notice {
  margin: 0 10px 8px;
  font-size: 0.78em;
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.error-text {
  margin-top: 6px;
  font-size: 0.85em;
  color: #e57373;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
