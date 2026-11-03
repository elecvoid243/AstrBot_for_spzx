<template>
  <transition name="slide-left">
    <aside
      v-if="modelValue"
      ref="sidebarRef"
      class="reasoning-sidebar"
      :style="{ width: sidebarWidth + 'px' }"
    >
      <!-- Drag handle -->
      <div class="reasoning-sidebar-resizer" @mousedown="startResize" />

      <div class="reasoning-sidebar-header">
        <div class="reasoning-sidebar-title">{{ reasoningTitle }}</div>
        <v-btn icon="mdi-close" size="small" variant="text" @click="close" />
      </div>

      <div
        ref="sidebarBody"
        class="reasoning-sidebar-body"
        tabindex="0"
        @scroll="handleSidebarScroll"
        @wheel.passive="handleSidebarInteraction"
        @touchstart.passive="handleSidebarInteraction"
        @touchmove.passive="handleSidebarInteraction"
        @pointerdown="handleSidebarInteraction"
        @keydown="handleSidebarInteraction"
      >
        <ReasoningTimeline
          v-if="parts.length || reasoning"
          ref="timelineRef"
          :parts="parts"
          :reasoning="reasoning"
          :is-dark="isDark"
          :is-streaming="isStreaming"
        />
        <div v-else class="reasoning-sidebar-empty">
          {{ reasoningTitle }}
        </div>
      </div>
    </aside>
  </transition>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, computed, watch } from "vue";
import {
  reasoningActivityCounts,
  reasoningActivityTitle,
  type MessagePart,
} from "@/composables/useMessages";
import { useModuleI18n } from "@/i18n/composables";
import ReasoningTimeline from "@/components/chat/message_list_comps/ReasoningTimeline.vue";

const props = defineProps<{
  modelValue: boolean;
  parts: MessagePart[];
  reasoning?: string;
  isDark?: boolean;
  isStreaming?: boolean;
  /**
   * 2026-08-11 file-change visibility: when set, the sidebar scrolls
   * to the pinned FileChangeCard of this tool call after opening
   * (the user clicked a file-change chip on the reasoning bar).
   */
  focusCallId?: string | null;
  /**
   * Message-search reveal: keyword to land on inside the timeline. Set when
   * the jump's target message matched the search in its thinking text, which
   * the main message list never renders inline.
   */
  focusQuery?: string | null;
}>();

const sidebarBody = ref<HTMLElement | null>(null);
const shouldStickToBottom = ref(true);
let lastSidebarScrollTop = 0;
let touchScrollY = 0;
let scrollIntent = 0;
let sidebarScrollFrame = 0;

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
}>();

const { tm } = useModuleI18n("features/chat");

const activityCounts = computed(() =>
  reasoningActivityCounts(props.parts, props.reasoning || ""),
);

const reasoningTitle = computed(() =>
  reasoningActivityTitle(activityCounts.value, tm),
);

function close() {
  emit("update:modelValue", false);
}

function scrollToLatestActivity() {
  if (!props.modelValue || !shouldStickToBottom.value || sidebarScrollFrame)
    return;
  // Streaming updates share one frame so they cannot compete for the viewport.
  sidebarScrollFrame = window.requestAnimationFrame(() => {
    sidebarScrollFrame = 0;
    const body = sidebarBody.value;
    if (!props.modelValue || !body || !shouldStickToBottom.value) return;
    body.scrollTop = Math.max(0, body.scrollHeight - body.clientHeight);
    lastSidebarScrollTop = Math.max(0, body.scrollTop);
  });
}

// Record user input separately from scroll events caused by rendering or focus.
function handleSidebarInteraction(
  event: WheelEvent | TouchEvent | PointerEvent | KeyboardEvent,
) {
  if (event instanceof WheelEvent) {
    if (event.ctrlKey || event.deltaY === 0) return;
    scrollIntent = Math.sign(event.deltaY);
  } else if (event.type === "touchstart" || event.type === "touchmove") {
    const touch = (event as TouchEvent).touches[0];
    if (!touch) return;
    if (event.type === "touchstart") {
      touchScrollY = touch.clientY;
      return;
    }
    scrollIntent = Math.sign(touchScrollY - touch.clientY);
    touchScrollY = touch.clientY;
  } else if (event instanceof KeyboardEvent) {
    const target = event.target as HTMLElement;
    if (target.closest("input, textarea, select, [contenteditable], button, a"))
      return;
    if (
      ["ArrowUp", "PageUp", "Home"].includes(event.key) ||
      (event.key === " " && event.shiftKey)
    ) {
      scrollIntent = -1;
    } else if (["ArrowDown", "PageDown", "End", " "].includes(event.key)) {
      scrollIntent = 1;
    } else {
      return;
    }
  } else {
    if (event.target !== sidebarBody.value) return;
    scrollIntent = 0;
    shouldStickToBottom.value = false;
  }
  if (scrollIntent < 0) shouldStickToBottom.value = false;
}

function handleSidebarScroll() {
  const body = sidebarBody.value;
  if (!body) return;
  const maxScrollTop = Math.max(0, body.scrollHeight - body.clientHeight);
  const scrollTop = Math.max(0, body.scrollTop);
  // Collapsing timeline entries can clamp the viewport without user scrolling.
  const previousTop = Math.min(lastSidebarScrollTop, maxScrollTop);
  const isAwayFromBottom = maxScrollTop - scrollTop > 2;
  if (scrollTop < previousTop) {
    shouldStickToBottom.value = false;
  } else if (
    scrollTop > previousTop &&
    !isAwayFromBottom &&
    scrollIntent >= 0
  ) {
    shouldStickToBottom.value = true;
  }
  lastSidebarScrollTop = scrollTop;
}

watch(
  () => props.modelValue,
  (open) => {
    window.cancelAnimationFrame(sidebarScrollFrame);
    sidebarScrollFrame = 0;
    if (!open) return;
    // A search/file target owns the viewport until the user returns to bottom.
    shouldStickToBottom.value = !props.focusCallId && !props.focusQuery;
    scrollIntent = shouldStickToBottom.value ? 0 : -1;
    lastSidebarScrollTop = 0;
    scrollToLatestActivity();
  },
  { flush: "post", immediate: true },
);

watch(() => [props.reasoning, props.parts], scrollToLatestActivity, {
  deep: true,
  flush: "post",
});

// 2026-08-11 file-change visibility: after the sidebar opens with a
// focus target, scroll the matching pinned card into view.
const timelineRef = ref<{
  scrollToFile: (id: string) => Promise<void>;
  scrollToText: (text: string) => Promise<void>;
} | null>(null);

watch(
  [() => props.modelValue, () => props.focusCallId],
  async ([open, callId]) => {
    if (!open || !callId) return;
    shouldStickToBottom.value = false;
    scrollIntent = -1;
    window.cancelAnimationFrame(sidebarScrollFrame);
    sidebarScrollFrame = 0;
    await nextTick();
    if (!props.modelValue || props.focusCallId !== callId) return;
    await timelineRef.value?.scrollToFile(callId);
  },
  { flush: "post", immediate: true },
);

// Message-search reveal: land on the thought that holds the keyword (the
// timeline scrolls itself, so no file target is involved).
watch(
  [() => props.modelValue, () => props.focusQuery],
  async ([open, query]) => {
    if (!open || !query) return;
    shouldStickToBottom.value = false;
    scrollIntent = -1;
    window.cancelAnimationFrame(sidebarScrollFrame);
    sidebarScrollFrame = 0;
    await nextTick();
    if (!props.modelValue || props.focusQuery !== query) return;
    await timelineRef.value?.scrollToText(query);
  },
  { flush: "post", immediate: true },
);

// ── Drag resize ────────────────────────────────────────────────────

const MIN_WIDTH = 280;
const MAX_WIDTH = 1800;
const DEFAULT_WIDTH = 600;

const sidebarWidth = ref(DEFAULT_WIDTH);
const sidebarRef = ref<HTMLElement | null>(null);
let isResizing = false;

function startResize(e: MouseEvent) {
  e.preventDefault();
  isResizing = true;
  document.body.style.cursor = "ew-resize";
  document.body.style.userSelect = "none";
  document.addEventListener("mousemove", onMouseMove);
  document.addEventListener("mouseup", onMouseUp);
}

function onMouseMove(e: MouseEvent) {
  if (!isResizing || !sidebarRef.value) return;
  // Use the sidebar's own right edge (not the parent's) as the
  // reference point. The flex layout places any siblings shown to
  // the right (e.g. GoalSidebar) beyond `selfRect.right`, so the
  // computed width is automatically reduced by their combined
  // width — no need to enumerate sibling sidebars.
  const selfRect = sidebarRef.value.getBoundingClientRect();
  const newWidth = selfRect.right - e.clientX;
  sidebarWidth.value = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, newWidth));
}

function onMouseUp() {
  if (!isResizing) return;
  isResizing = false;
  document.body.style.cursor = "";
  document.body.style.userSelect = "";
  document.removeEventListener("mousemove", onMouseMove);
  document.removeEventListener("mouseup", onMouseUp);
}

onBeforeUnmount(() => {
  window.cancelAnimationFrame(sidebarScrollFrame);
  onMouseUp();
});
</script>

<style scoped>
.reasoning-sidebar {
  --chat-side-panel-width: 380px;
  width: var(--chat-side-panel-width);
  height: calc(100% - var(--chat-panel-top-offset, 0px));
  margin-top: var(--chat-panel-top-offset, 0px);
  border-left: 1px solid
    var(--chat-border, rgba(var(--v-theme-on-surface), 0.1));
  background: var(--chat-page-bg, rgb(var(--v-theme-surface)));
  color: rgb(var(--v-theme-on-surface));
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  position: relative;
}

/* ── Drag handle ──────────────────────────────────────────────── */

.reasoning-sidebar-resizer {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 6px;
  cursor: ew-resize;
  z-index: 10;
  transition: background 0.15s ease;
}

.reasoning-sidebar-resizer:hover,
.reasoning-sidebar-resizer:active {
  background: rgba(var(--v-theme-primary), 0.2);
}

/* ── Transition ───────────────────────────────────────────────── */

.slide-left-enter-active,
.slide-left-leave-active {
  transition: all 0.2s ease;
}

.slide-left-enter-from,
.slide-left-leave-to {
  transform: translateX(100%);
  opacity: 0;
}

/* ── Header ───────────────────────────────────────────────────── */

.reasoning-sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px 8px;
}

.reasoning-sidebar-title {
  font-size: 16px;
  font-weight: 600;
  line-height: 1.4;
  color: rgb(var(--v-theme-on-surface));
}

/* ── Body ─────────────────────────────────────────────────────── */

.reasoning-sidebar-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 14px 12px;
  font-size: 14.5px;
  line-height: 1.62;
}

/* 2026-09-17:思考内容字号下调一档。
   markstream 的正文字号由它自己的 ``.markstream-vue`` 规则决定
   (font-size: var(--ms-text-body)),而 chatMarkdown.css 把该变量设为
   桌面 0.9375rem / 移动 1rem —— 所以单写 font-size 会被盖掉,必须覆盖
   变量本身;又要压过 ``.chat-markdown.markstream-vue`` 的 specificity,
   故带上 .reasoning-sidebar-body 前缀(:deep 穿透 scoped)。
   只作用于侧栏:消息列表里复用的内联 ReasoningBlock 保持原字号。 */
.reasoning-sidebar-body :deep(.chat-markdown.markstream-vue) {
  --ms-text-body: 0.9375rem; /* 移动端:1rem 下调一档 */
}

@media (min-width: 761px) {
  .reasoning-sidebar-body :deep(.chat-markdown.markstream-vue) {
    --ms-text-body: 0.875rem; /* 桌面端:0.9375rem 下调一档 */
  }
}

.reasoning-sidebar-body :deep(.reasoning-text) {
  --ms-text-body: 0.8125rem;
  --ms-leading-body: 1.55;
  font-size: 13px;
  line-height: 1.55;
}

.reasoning-sidebar-empty {
  padding: 12px 2px;
  color: rgba(var(--v-theme-on-surface), 0.54);
  font-size: 13px;
}

/* ── Mobile ───────────────────────────────────────────────────── */

@media (max-width: 760px) {
  .reasoning-sidebar {
    position: fixed;
    inset: 0;
    z-index: 1300;
    width: 100vw !important;
    height: 100dvh;
    margin-top: 0;
    border-left: 0;
  }

  .reasoning-sidebar-resizer {
    display: none;
  }

  .reasoning-sidebar-header {
    min-height: 52px;
    padding: calc(10px + env(safe-area-inset-top)) 12px 8px;
    border-bottom: 1px solid
      var(--chat-border, rgba(var(--v-border-color), 0.12));
  }

  .reasoning-sidebar-body {
    padding: 0 12px calc(12px + env(safe-area-inset-bottom));
  }
}
</style>
