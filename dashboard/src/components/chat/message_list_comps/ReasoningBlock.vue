<template>
  <div class="reasoning-block" :class="{ 'reasoning-block--dark': isDark }">
    <div class="reasoning-header-row">
      <button
        class="reasoning-header"
        :class="{ 'reasoning-header--trigger': openInSidebar }"
        type="button"
        @click="handlePrimaryAction"
      >
        <span class="reasoning-title">
          {{ reasoningTitle }}
        </span>
        <v-icon
          size="22"
          class="reasoning-icon"
          :class="{ 'rotate-90': !openInSidebar && isExpanded }"
        >
          mdi-chevron-right
        </v-icon>
      </button>

      <!-- 2026-08-11 file-change visibility: per-file chips on the
           collapsed bar. Clicking a chip expands (or opens the
           sidebar) and scrolls to that file's pinned card. -->
      <span v-if="fileChanges.length" class="file-change-chips">
        <span
          v-for="chip in visibleChips"
          :key="chip.callId"
          class="file-change-chip"
          :class="`file-change-chip--${fileChangeTone(chip)}`"
          role="button"
          tabindex="0"
          :title="chip.filePath"
          @click.stop="onChipClick(chip.callId)"
          @keydown.enter.stop="onChipClick(chip.callId)"
        >
          <v-icon size="11" class="file-change-chip-icon">{{ chipIcon(chip) }}</v-icon>
          <span class="file-change-chip-name">{{ fileBasename(chip.filePath) }}</span>
          <v-progress-circular
            v-if="chip.status === 'running'"
            indeterminate
            size="10"
            width="2"
            class="file-change-chip-spinner"
          />
          <span
            v-else-if="chip.kind === 'edit' && chip.diffStat"
            class="file-change-chip-stat"
          >
            <span class="stat-adds">+{{ chip.diffStat.adds }}</span>
            <span class="stat-dels">−{{ chip.diffStat.dels }}</span>
          </span>
          <span
            v-else-if="chip.kind === 'write' && chip.lineCount !== null"
            class="file-change-chip-stat"
          >
            {{ tm("fileChange.lines", { count: chip.lineCount }) }}
          </span>
        </span>
        <span v-if="overflowCount > 0" class="file-change-chip file-change-chip--more">
          +{{ overflowCount }}
        </span>
      </span>
    </div>

    <div
      v-if="!openInSidebar && isExpanded"
      class="reasoning-content animate-fade-in"
    >
      <ReasoningTimeline
        ref="timelineRef"
        :parts="renderParts"
        :reasoning="reasoning"
        :is-dark="isDark"
        :is-streaming="isStreaming"
      />
    </div>

    <transition :name="previewTransitionName" mode="out-in">
      <div
        v-if="showStreamingPreview"
        :key="previewKey"
        class="reasoning-preview"
      >
        {{ previewText }}
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import {
  reasoningActivityCounts,
  reasoningActivityTitle,
  type MessagePart,
} from "@/composables/useMessages";
import { useModuleI18n } from "@/i18n/composables";
import ThinkingIndicator from "@/components/chat/ThinkingIndicator.vue";
import ReasoningTimeline from "@/components/chat/message_list_comps/ReasoningTimeline.vue";
import {
  collectFileChanges,
  fileBasename,
  fileChangeTone,
  type FileChangeEntry,
} from "@/utils/fileChangeTool";

const props = defineProps<{
  parts?: MessagePart[];
  reasoning?: string;
  isDark?: boolean;
  initialExpanded?: boolean;
  isStreaming?: boolean;
  hasNonReasoningContent?: boolean;
  openInSidebar?: boolean;
}>();

const emit = defineEmits<{
  open: [payload?: { callId?: string }];
}>();

const { tm } = useModuleI18n("features/chat");
const isExpanded = ref(Boolean(props.initialExpanded));
const timelineRef = ref<{ scrollToFile: (id: string) => Promise<void> } | null>(
  null,
);

// 2026-08-11 file-change visibility: chips on the collapsed bar.
// Capped at 3 visible chips + a "+N" overflow indicator.
const MAX_VISIBLE_CHIPS = 3;
const fileChanges = computed(() => collectFileChanges(renderParts.value));
const visibleChips = computed(() =>
  fileChanges.value.slice(0, MAX_VISIBLE_CHIPS),
);
const overflowCount = computed(() =>
  Math.max(0, fileChanges.value.length - MAX_VISIBLE_CHIPS),
);

function chipIcon(change: FileChangeEntry): string {
  if (change.kind === "edit") return "mdi-file-document-edit-outline";
  if (change.kind === "remove") return "mdi-delete-outline";
  return "mdi-content-save-outline";
}

/** Chip click: expand inline (then scroll to the card) or hand the
 *  callId off to the sidebar via the open event. */
async function onChipClick(callId: string): Promise<void> {
  if (openInSidebar.value) {
    emit("open", { callId });
    return;
  }
  isExpanded.value = true;
  await nextTick();
  await timelineRef.value?.scrollToFile(callId);
}
const previewText = ref("");
const previewKey = ref(0);
let previewTimer: ReturnType<typeof setInterval> | null = null;
let previewStartTimer: ReturnType<typeof setTimeout> | null = null;

const renderParts = computed<MessagePart[]>(() => {
  if (props.parts?.length) return props.parts;
  if (props.reasoning) {
    return [{ type: "think", think: props.reasoning }];
  }
  return [];
});

const openInSidebar = computed(() => Boolean(props.openInSidebar));

const activityCounts = computed(() =>
  reasoningActivityCounts(renderParts.value, props.reasoning || ""),
);

const reasoningTitle = computed(() =>
  reasoningActivityTitle(activityCounts.value, tm),
);

const thinkingText = computed(() =>
  renderParts.value
    .filter((part) => part.type === "think")
    .map((part) => String(part.think || ""))
    .join(""),
);

const showStreamingPreview = computed(
  () =>
    props.isStreaming &&
    (openInSidebar.value || !isExpanded.value) &&
    !props.hasNonReasoningContent &&
    previewText.value,
);

const previewTransitionName = computed(() =>
  props.hasNonReasoningContent
    ? "reasoning-preview-collapse"
    : "reasoning-preview-fade",
);

function handlePrimaryAction() {
  if (openInSidebar.value) {
    emit("open");
    return;
  }
  isExpanded.value = !isExpanded.value;
}

function latestReasoningPreview() {
  const lines = thinkingText.value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  return lines.slice(-3).join("\n");
}

function updatePreviewLine() {
  const nextText = latestReasoningPreview();
  if (!nextText || nextText === previewText.value) return;
  previewText.value = nextText;
  previewKey.value += 1;
}

function stopPreviewTimer() {
  if (!previewTimer) return;
  clearInterval(previewTimer);
  previewTimer = null;
}

function stopPreviewStartTimer() {
  if (!previewStartTimer) return;
  clearTimeout(previewStartTimer);
  previewStartTimer = null;
}

function startPreviewTimer() {
  updatePreviewLine();
  if (!previewTimer) {
    previewTimer = setInterval(updatePreviewLine, 2000);
  }
}

function syncPreviewTimer() {
  if (
    props.isStreaming &&
    (openInSidebar.value || !isExpanded.value) &&
    !props.hasNonReasoningContent
  ) {
    if (!previewTimer && !previewStartTimer) {
      previewStartTimer = setTimeout(() => {
        previewStartTimer = null;
        if (
          props.isStreaming &&
          (openInSidebar.value || !isExpanded.value) &&
          !props.hasNonReasoningContent
        ) {
          startPreviewTimer();
        }
      }, 2000);
    }
    return;
  }

  stopPreviewStartTimer();
  stopPreviewTimer();
  if (!props.isStreaming) {
    previewText.value = "";
  }
}

watch(
  () => [
    props.isStreaming,
    isExpanded.value,
    props.hasNonReasoningContent,
    thinkingText.value,
    openInSidebar.value,
  ],
  syncPreviewTimer,
  {
    immediate: true,
  },
);

onBeforeUnmount(() => {
  stopPreviewStartTimer();
  stopPreviewTimer();
});
</script>

<style scoped>
.reasoning-block {
  margin: 6px 0;
  max-width: 100%;
  color: rgba(var(--v-theme-on-surface), 0.7);
  font-size: inherit;
  line-height: inherit;
}

.reasoning-header {
  width: fit-content;
  max-width: 100%;
  border: 0;
  padding: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  user-select: none;
  display: flex;
  align-items: center;
  gap: 8px;
  font: inherit;
  font-size: 1rem;
  line-height: 1.7;
  text-align: left;
}

/* 2026-08-11 file-change visibility: header row + chips */
.reasoning-header-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-width: 0;
}

.file-change-chips {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  min-width: 0;
}

.file-change-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 6px;
  border-radius: 9px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  background: rgba(var(--v-theme-on-surface), 0.03);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 10.5px;
  line-height: 1.5;
  color: rgba(var(--v-theme-on-surface), 0.65);
  cursor: pointer;
  user-select: none;
  max-width: 200px;
  transition: background 0.15s ease;
}

.file-change-chip:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
  color: rgba(var(--v-theme-on-surface), 0.85);
}

/* 2026-08-11: chip tint by change kind (matches FileChangeCard) */
.file-change-chip--green {
  border-color: rgba(45, 164, 78, 0.4);
  background: rgba(45, 164, 78, 0.08);
}

.file-change-chip--green:hover {
  background: rgba(45, 164, 78, 0.16);
}

.file-change-chip--yellow {
  border-color: rgba(191, 135, 0, 0.4);
  background: rgba(191, 135, 0, 0.08);
}

.file-change-chip--yellow:hover {
  background: rgba(191, 135, 0, 0.16);
}

.file-change-chip--red {
  border-color: rgba(207, 34, 46, 0.4);
  background: rgba(207, 34, 46, 0.08);
}

.file-change-chip--red:hover {
  background: rgba(207, 34, 46, 0.16);
}

/* split-color diffstat (shared with FileChangeCard) */
.stat-adds {
  color: #2da44e;
  font-weight: 600;
}

.stat-dels {
  color: #cf222e;
  font-weight: 600;
  margin-left: 3px;
}

.file-change-chip-icon {
  color: rgba(var(--v-theme-on-surface), 0.45);
  flex-shrink: 0;
}

.file-change-chip-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.file-change-chip-stat {
  color: rgba(var(--v-theme-on-surface), 0.45);
  flex-shrink: 0;
}

.file-change-chip-spinner {
  flex-shrink: 0;
}

.file-change-chip--more {
  cursor: default;
  color: rgba(var(--v-theme-on-surface), 0.45);
}

.file-change-chip--more:hover {
  background: rgba(var(--v-theme-on-surface), 0.03);
  color: rgba(var(--v-theme-on-surface), 0.45);
}

.reasoning-header:hover {
  color: rgba(var(--v-theme-on-surface), 0.88);
}

.reasoning-icon {
  color: currentcolor;
  transition: transform 0.2s ease;
  flex-shrink: 0;
  align-self: center;
}

.reasoning-icon--thinking {
  color: rgba(var(--v-theme-on-surface), 0.45);
}

@media (prefers-reduced-motion: reduce) {
  .reasoning-icon--thinking {
    color: rgba(var(--v-theme-on-surface), 0.6);
  }
}

@media (forced-colors: active) {
  .reasoning-icon--thinking {
    color: CanvasText;
  }
}

.reasoning-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reasoning-content {
  margin-top: 10px;
  padding: 12px 14px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: 18px;
  background: rgb(var(--v-theme-surface));
  color: rgba(var(--v-theme-on-surface), 0.72);
  animation: fadeIn 0.2s ease-in-out;
  font-style: normal;
}

.reasoning-preview {
  max-width: 100%;
  margin-top: 4px;
  color: rgba(var(--v-theme-on-surface), 0.52);
  overflow: hidden;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  white-space: pre-line;
  font: inherit;
  font-size: 14.5px;
  line-height: 1.62;
  font-style: normal;
}

.animate-fade-in {
  animation: fadeIn 0.2s ease-in-out;
}

@keyframes fadeIn {
  from {
    opacity: 0;
  }

  to {
    opacity: 1;
  }
}

.rotate-90 {
  transform: rotate(90deg);
}

.reasoning-preview-fade-enter-active {
  transition: opacity 0.25s ease;
}

.reasoning-preview-fade-leave-active {
  transition: opacity 0.25s ease;
}

.reasoning-preview-fade-enter-from,
.reasoning-preview-fade-leave-to {
  opacity: 0;
}

.reasoning-preview-collapse-enter-active {
  transition: opacity 0.25s ease;
}

.reasoning-preview-collapse-leave-active {
  transition:
    opacity 0.18s ease,
    max-height 0.18s ease,
    margin-top 0.18s ease;
  overflow: hidden;
}

.reasoning-preview-collapse-enter-from {
  opacity: 0;
}

.reasoning-preview-collapse-leave-from {
  opacity: 1;
  max-height: 6.5em;
  margin-top: 4px;
}

.reasoning-preview-collapse-leave-to {
  opacity: 0;
  max-height: 0;
  margin-top: 0;
}
</style>
