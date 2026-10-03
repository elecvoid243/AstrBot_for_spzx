<template>
  <div v-if="timelineEntries.length" ref="rootRef" class="reasoning-timeline">
    <!-- 2026-08-11 file-change visibility: pinned per-file change cards
         above the timeline so users see edits/writes at a glance. The
         timeline entries below stay unchanged (full process record). -->
    <div v-if="fileChanges.length" class="file-change-pinned">
      <FileChangeCard
        v-for="change in fileChanges"
        :key="change.callId"
        :entry="change"
        :is-dark="isDark"
      />
    </div>

    <div
      v-for="(entry, entryIndex) in timelineEntries"
      :key="entry.key"
      class="reasoning-timeline-item"
    >
      <div class="reasoning-timeline-rail" aria-hidden="true">
        <span class="reasoning-timeline-dot"></span>
        <span
          v-if="entryIndex < timelineEntries.length - 1"
          class="reasoning-timeline-line"
        ></span>
      </div>

      <div class="reasoning-step">
        <div class="reasoning-step-meta">
          <span class="reasoning-step-title">{{ entry.title }}</span>
        </div>

        <MarkdownRender
          v-if="entry.kind === 'think' && !thinkCollapsed(entry)"
          :content="entry.think || ''"
          class="chat-markdown reasoning-text markdown-content"
          :final="!isStreaming"
          :smooth-streaming="isStreaming ? 'auto' : false"
          :fade="false"
          :typewriter="false"
          :is-dark="isDark"
          :max-live-nodes="MARKDOWN_RENDER_MAX_LIVE_NODES"
          :style="CHAT_MARKDOWN_HEADING_STYLE"
        />

        <div
          v-else-if="entry.kind === 'think'"
          class="reasoning-think-preview"
          :class="{ 'is-expandable': thinkExpandable(entry) }"
          data-testid="think-preview"
          :role="thinkExpandable(entry) ? 'button' : undefined"
          :tabindex="thinkExpandable(entry) ? 0 : undefined"
          :title="thinkExpandable(entry) ? tm('reasoning.expandThink') : ''"
          @click="thinkExpandable(entry) && toggleThink(entry.key)"
          @keydown.enter.prevent="
            thinkExpandable(entry) && toggleThink(entry.key)
          "
        >
          <span class="reasoning-think-preview-text">{{
            thinkPreviewText(entry, entryIndex)
          }}</span>
          <span
            v-if="thinkExpandable(entry)"
            class="think-toggle-label"
            data-testid="think-expand-label"
            >{{ tm("reasoning.expandThink") }}</span
          >
        </div>

        <div
          v-else-if="entry.kind === 'user_message'"
          class="reasoning-user-message"
        >
          <span class="reasoning-user-message-text">{{ entry.userText }}</span>
          <span v-if="entry.relayed" class="reasoning-user-message-relayed">
            {{ tm("reasoning.userMessageRelayed") }}
          </span>
        </div>

        <div v-else-if="entry.tool" class="reasoning-tool-call-block">
          <ToolCallItem v-if="isIPythonToolCall(entry.tool)" :is-dark="isDark">
            <template #label>
              <v-icon size="16">mdi-code-json</v-icon>
              <span>{{ entry.tool.name || "python" }}</span>
              <span class="tool-call-inline-status">
                {{ toolCallStatusText(entry.tool) }}
              </span>
            </template>
            <template #details>
              <IPythonToolBlock
                :tool-call="entry.tool"
                :is-dark="isDark"
                :show-header="false"
                :force-expanded="true"
              />
            </template>
          </ToolCallItem>
          <ToolCallCard v-else :tool-call="entry.tool" :is-dark="isDark" />
        </div>

        <button
          v-if="
            entry.kind === 'think' &&
            !thinkCollapsed(entry) &&
            thinkExpandable(entry)
          "
          class="think-collapse-toggle"
          data-testid="think-collapse-toggle"
          type="button"
          @click="collapseThinkAnchored($event, entry.key)"
        >
          {{ tm("reasoning.collapseThink") }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { MarkdownRender } from "markstream-vue";
import {
  CHAT_MARKDOWN_HEADING_STYLE,
  MARKDOWN_RENDER_MAX_LIVE_NODES,
} from "@/components/chat/markdownRenderConfig";
import IPythonToolBlock from "@/components/chat/message_list_comps/IPythonToolBlock.vue";
import ToolCallCard from "@/components/chat/message_list_comps/ToolCallCard.vue";
import ToolCallItem from "@/components/chat/message_list_comps/ToolCallItem.vue";
import FileChangeCard from "@/components/chat/message_list_comps/FileChangeCard.vue";
import { collectFileChanges } from "@/utils/fileChangeTool";
import { anchorBottom } from "@/utils/scrollAnchor";
import type { MessagePart } from "@/composables/useMessages";
import { useModuleI18n } from "@/i18n/composables";

const props = defineProps<{
  parts?: MessagePart[];
  reasoning?: string;
  isDark?: boolean;
  isStreaming?: boolean;
  /**
   * Collapse long think entries to a short preview (subagent run blocks).
   * The currently streaming entry stays expanded so live thinking remains
   * visible. Defaults to false (main-agent reasoning renders in full).
   */
  collapseThink?: boolean;
}>();

const { tm } = useModuleI18n("features/chat");

const THINK_PREVIEW_MAX_CHARS = 160;
const LIVE_PREVIEW_INTERVAL_MS = 2000;
/** Keys of think entries the user manually expanded. */
const expandedThinkKeys = ref(new Set<string>());

/**
 * Tail preview of a think entry: the last non-empty lines, capped in length.
 * Mirrors the main agent's reasoning preview, which surfaces the LATEST
 * thinking rather than the head.
 */
function tailPreview(think: string): { text: string; truncated: boolean } {
  const lines = think
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const tail = lines.slice(-2).join(" ");
  if (tail.length <= THINK_PREVIEW_MAX_CHARS) {
    return { text: tail, truncated: false };
  }
  return { text: `…${tail.slice(-THINK_PREVIEW_MAX_CHARS)}`, truncated: true };
}

function thinkCollapsed(entry: TimelineEntry): boolean {
  return (
    Boolean(props.collapseThink) &&
    entry.kind === "think" &&
    !expandedThinkKeys.value.has(entry.key)
  );
}

function thinkExpandable(entry: TimelineEntry): boolean {
  return entry.kind === "think" && tailPreview(entry.think).truncated;
}

function toggleThink(key: string): void {
  const next = new Set(expandedThinkKeys.value);
  if (next.has(key)) {
    next.delete(key);
  } else {
    next.add(key);
  }
  expandedThinkKeys.value = next;
}

/**
 * Collapse an expanded think entry while keeping its bottom edge (the toggle
 * the user just clicked) at the same viewport position — the block shrinks
 * upward, so the line under the user's eyes does not jump away.
 */
async function collapseThinkAnchored(event: MouseEvent, key: string): Promise<void> {
  const stepEl = (event.currentTarget as HTMLElement | null)?.closest<HTMLElement>(
    ".reasoning-step",
  );
  const beforeBottom = stepEl?.getBoundingClientRect().bottom ?? null;
  toggleThink(key);
  if (!stepEl || beforeBottom === null) return;
  await nextTick();
  anchorBottom(stepEl, beforeBottom);
}


// 2026-08-11 file-change visibility: distilled per-file changes for the
// pinned card section, and the scroll-to-locate entry point used by the
// ReasoningBlock chips (and the ReasoningSidebar focus hand-off).
const rootRef = ref<HTMLElement | null>(null);
const fileChanges = computed(() => collectFileChanges(renderParts.value));

/** Scroll the pinned card for `callId` into view and flash it. */
async function scrollToFile(callId: string): Promise<void> {
  await nextTick();
  const root = rootRef.value;
  if (!root) return;
  const escaped =
    typeof CSS !== "undefined" && CSS.escape ? CSS.escape(callId) : callId;
  const el = root.querySelector<HTMLElement>(`[data-call-id="${escaped}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  el.classList.add("file-change-flash");
  setTimeout(() => el.classList.remove("file-change-flash"), 1200);
}

/** Scroll the timeline entry holding `text` into view and flash it. */
async function scrollToText(text: string): Promise<void> {
  await nextTick();
  const root = rootRef.value;
  const needle = text.trim().toLowerCase();
  if (!root || !needle) return;
  for (const entry of root.querySelectorAll<HTMLElement>(
    ".reasoning-timeline-item",
  )) {
    if (!entry.textContent?.toLowerCase().includes(needle)) continue;
    entry.scrollIntoView({ behavior: "smooth", block: "center" });
    entry.classList.add("reasoning-match-flash");
    setTimeout(() => entry.classList.remove("reasoning-match-flash"), 1200);
    return;
  }
}

// The message-search jump opens this timeline on the thinking block that
// matched the keyword; landing at the top of a long trail would leave the
// user hunting for the hit again.
defineExpose({ scrollToFile, scrollToText });

type NormalizedToolCall = Record<string, unknown>;

type TimelineEntry =
  | {
      key: string;
      kind: "think";
      title: string;
      think: string;
    }
  | {
      key: string;
      kind: "tool_call";
      title: string;
      tool: NormalizedToolCall;
    }
  | {
      key: string;
      kind: "user_message";
      title: string;
      userText: string;
      relayed: boolean;
    };

const renderParts = computed<MessagePart[]>(() => {
  if (props.parts?.length) return props.parts;
  if (props.reasoning) {
    return [{ type: "think", think: props.reasoning }];
  }
  return [];
});

const timelineEntries = computed<TimelineEntry[]>(() => {
  const entries: TimelineEntry[] = [];

  renderParts.value.forEach((part, partIndex) => {
    if (part.type === "think" || part.type === "text") {
      const think = String(part.think || "");
      if (!think.trim()) return;
      entries.push({
        key: `think-${partIndex}`,
        kind: "think",
        // "text" parts are intermediate assistant narration streamed between
        // tool calls; label them differently from chain-of-thought.
        title:
          part.type === "think" ? tm("reasoning.think") : tm("reasoning.reply"),
        think,
      });
      return;
    }

    if (part.type === "user_message") {
      // A follow-up the user sent into a running subagent (subagent run
      // blocks only). Rendered as a compact user entry in the timeline.
      entries.push({
        key: `user-${partIndex}`,
        kind: "user_message",
        title: tm("reasoning.userMessage"),
        userText: String(part.userText || ""),
        relayed: Boolean(part.relayed),
      });
      return;
    }

    if (part.type !== "tool_call" || !Array.isArray(part.tool_calls)) return;

    part.tool_calls.forEach((tool, toolIndex) => {
      const normalizedTool = normalizeToolCall(tool);
      entries.push({
        key: `tool-${String(
          tool.id || tool.name || `${partIndex}-${toolIndex}`,
        )}`,
        kind: "tool_call",
        title: tm("reasoning.toolUsed"),
        tool: normalizedTool,
      });
    });
  });

  return entries;
});

// Live preview throttle (same cadence as the main agent's ReasoningBlock):
// while streaming, the last think entry's preview only refreshes on a 2s
// tick, so fast token output swaps text without per-delta flicker. The fixed
// line-clamped height means these swaps never move the layout.
const liveThinkPreview = ref("");
let livePreviewTimer: ReturnType<typeof setInterval> | null = null;

function updateLiveThinkPreview(): void {
  const entries = timelineEntries.value;
  const last = entries[entries.length - 1];
  liveThinkPreview.value =
    last && last.kind === "think" ? tailPreview(last.think).text : "";
}

function stopLivePreviewTimer(): void {
  if (livePreviewTimer) {
    clearInterval(livePreviewTimer);
    livePreviewTimer = null;
  }
}

watch(
  () => [props.isStreaming, props.collapseThink],
  () => {
    if (props.isStreaming && props.collapseThink) {
      updateLiveThinkPreview();
      if (!livePreviewTimer) {
        livePreviewTimer = setInterval(
          updateLiveThinkPreview,
          LIVE_PREVIEW_INTERVAL_MS,
        );
      }
      return;
    }
    stopLivePreviewTimer();
  },
  { immediate: true },
);

onBeforeUnmount(stopLivePreviewTimer);

/** Preview text for a collapsed think entry: throttled while it is the live
 * streaming entry, static tail otherwise. */
function thinkPreviewText(entry: TimelineEntry, index: number): string {
  if (
    props.isStreaming &&
    entry.kind === "think" &&
    index === timelineEntries.value.length - 1
  ) {
    return liveThinkPreview.value;
  }
  return entry.kind === "think" ? tailPreview(entry.think).text : "";
}

function normalizeToolCall(tool: Record<string, unknown>) {
  const normalized = { ...tool };
  normalized.args = parseJsonSafe(
    normalized.args ?? normalized.arguments ?? {},
  );
  normalized.result = parseJsonSafe(normalized.result);
  normalized.ts = normalized.ts ?? Date.now() / 1000;
  if (normalized.result && typeof normalized.result === "object") {
    normalized.result = JSON.stringify(normalized.result, null, 2);
  }
  return normalized;
}

function isIPythonToolCall(tool: Record<string, unknown>) {
  const name = String(tool.name || "").toLowerCase();
  return name.includes("python") || name.includes("ipython");
}

function toolCallStatusText(tool: Record<string, unknown>) {
  if (tool.finished_ts) return tm("toolStatus.done");
  return tm("toolStatus.running");
}

function parseJsonSafe(value: unknown) {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}
</script>

<style scoped>
.reasoning-timeline {
  display: flex;
  flex-direction: column;
  gap: 0;
  padding-top: 4px;
}

/* 2026-08-11 file-change visibility: pinned cards + locate flash */
.file-change-pinned {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 8px;
  padding-bottom: 8px;
  border-bottom: 1px dashed rgba(var(--v-theme-on-surface), 0.12);
}

:deep(.file-change-card.file-change-flash) {
  animation: fileChangeFlash 1.2s ease-out;
}

@keyframes fileChangeFlash {
  0%,
  40% {
    border-color: rgb(var(--v-theme-primary));
    box-shadow: 0 0 0 2px rgba(var(--v-theme-primary), 0.35);
  }

  100% {
    border-color: rgba(var(--v-theme-on-surface), 0.1);
    box-shadow: none;
  }
}

.reasoning-timeline-item {
  display: grid;
  grid-template-columns: 10px minmax(0, 1fr);
  column-gap: 10px;
  align-items: flex-start;
  padding-bottom: 10px;
}

/* Message-search reveal: flash the entry whose thinking text matched. */
.reasoning-timeline-item.reasoning-match-flash {
  animation: reasoningMatchFlash 1.2s ease-out;
  border-radius: 10px;
}

@keyframes reasoningMatchFlash {
  0%,
  40% {
    background: rgba(var(--v-theme-primary), 0.14);
  }

  100% {
    background: transparent;
  }
}

.reasoning-timeline-item:last-child {
  padding-bottom: 0;
}

.reasoning-timeline-rail {
  position: relative;
  display: flex;
  justify-content: center;
  min-height: 100%;
  padding-top: 6px;
}

.reasoning-timeline-dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: rgba(var(--v-theme-on-surface), 0.18);
}

.reasoning-timeline-line {
  position: absolute;
  top: 15px;
  bottom: -10px;
  left: 50%;
  width: 1px;
  transform: translateX(-50%);
  background: rgba(var(--v-theme-on-surface), 0.12);
}

.reasoning-step {
  min-width: 0;
  font-size: 14.5px;
  line-height: 1.62;
}

.reasoning-think-preview {
  font-size: 0.92em;
  line-height: 1.62;
  color: rgba(var(--v-theme-on-surface), 0.65);
  /* Fixed two-line clamp: streaming text swaps never change the height, so
     fast think→tool cycles cannot churn the layout. */
  overflow: hidden;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.reasoning-think-preview.is-expandable {
  cursor: pointer;
}

.reasoning-think-preview.is-expandable:hover {
  color: rgba(var(--v-theme-on-surface), 0.85);
}

.think-toggle-label {
  margin-left: 6px;
  font-size: 0.85em;
  color: rgba(var(--v-theme-primary, 25, 118, 210), 0.85);
}

.think-collapse-toggle {
  align-self: flex-start;
  margin-top: 2px;
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 0.78em;
  color: rgba(var(--v-theme-primary, 25, 118, 210), 0.85);
}

.reasoning-user-message {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 0.92em;
  line-height: 1.62;
}

.reasoning-user-message-text {
  white-space: pre-wrap;
  word-break: break-word;
}

.reasoning-user-message-relayed {
  font-size: 0.82em;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.reasoning-step-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
  color: rgba(var(--v-theme-on-surface), 0.54);
  font-size: 12px;
  line-height: 1.35;
}

.reasoning-step-title {
  color: rgba(var(--v-theme-on-surface), 0.76);
  font-weight: 600;
  font-size: 12px;
}

.reasoning-tool-call-block {
  margin-top: 4px;
  font-style: normal;
}

.reasoning-text {
  font-size: inherit;
  line-height: inherit;
  color: rgba(var(--v-theme-on-surface), 0.72);
  font-style: normal;
}

.reasoning-step :deep(.tool-call-card),
.reasoning-step :deep(.tool-call-item),
.reasoning-step :deep(.ipython-tool-block) {
  font-size: 13.5px;
  line-height: 1.56;
}

.reasoning-step :deep(.tool-call-card .detail-label) {
  font-size: 11.5px;
}

.reasoning-step :deep(.tool-call-card .detail-value),
.reasoning-step :deep(.ipython-tool-block .code-highlighted),
.reasoning-step :deep(.ipython-tool-block .code-fallback),
.reasoning-step :deep(.ipython-tool-block .result-label),
.reasoning-step :deep(.ipython-tool-block .result-content) {
  font-size: 12.5px;
}

.tool-call-inline-status {
  margin-left: 4px;
  color: rgba(var(--v-theme-on-surface), 0.48);
}
</style>
