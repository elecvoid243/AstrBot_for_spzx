<template>
  <nav
    ref="root"
    class="scroll-marker-strip"
    :class="{
      'is-dark': isDark,
      'is-coarse': coarsePointer,
      'is-jumping': loading,
    }"
    :style="{ height: `${height}px`, right: `${right + 4}px` }"
    :aria-label="tm('markers.navigation')"
    @mouseleave="!selectionOpen && (activeSlot = null)"
    @focusout="onFocusOut"
    @keydown.esc.stop.prevent="closePopover(true)"
  >
    <div v-if="loading" class="scroll-marker-loading" role="status">
      <LoaderCircle :size="13" class="loading-icon" aria-hidden="true" />
      {{ tm("markers.loading") }}
    </div>
    <button
      v-for="group in groups"
      :key="group.slot"
      type="button"
      class="scroll-marker-dot"
      :class="{
        'scroll-marker-dot--current': group.markers.some(
          (m) => m.id === currentId,
        ),
        'scroll-marker-dot--inherited': group.markers.every((m) => m.inherited),
        'scroll-marker-dot--grouped': group.markers.length > 1,
      }"
      :style="{ top: `${group.top}px` }"
      :data-slot="group.slot"
      :aria-label="
        group.markers.length > 1
          ? tm('markers.group', {
              first: group.markers[0].ordinal,
              last: group.markers[group.markers.length - 1].ordinal,
            })
          : `${tm('markers.turn', { number: group.markers[0].ordinal })}: ${
              group.markers[0].preview
            }`
      "
      :aria-current="
        group.markers.some((m) => m.id === currentId) ? 'true' : undefined
      "
      :aria-expanded="
        group.markers.length > 1
          ? selectionOpen && activeSlot === group.slot
          : undefined
      "
      :disabled="loading"
      @mouseenter="openPopover(group)"
      @focus="openPopover(group)"
      @click="
        group.markers.length > 1
          ? openPopover(group, true)
          : selectMarker(group.markers[0])
      "
    >
      <span class="marker-tick" aria-hidden="true" />
    </button>
    <div
      v-if="activeGroup && !loading"
      ref="popover"
      class="scroll-marker-popover"
      :class="{ 'is-selection': selectionOpen }"
      :style="{
        top: `${popoverTop}px`,
        width: `${Math.min(
          280,
          Math.max(
            120,
            (container?.clientWidth ?? 320) - (coarsePointer ? 80 : 64),
          ),
        )}px`,
        maxHeight: `${Math.max(0, height - 16)}px`,
      }"
      :role="selectionOpen ? 'dialog' : 'tooltip'"
      :aria-label="selectionOpen ? tm('markers.choose') : undefined"
    >
      <div class="marker-preview-meta">
        <span>
          {{
            activeGroup.markers.length > 1
              ? tm("markers.group", {
                  first: activeGroup.markers[0].ordinal,
                  last: activeGroup.markers[activeGroup.markers.length - 1]
                    .ordinal,
                })
              : tm("markers.turn", { number: activeGroup.markers[0].ordinal })
          }}
        </span>
        <span
          v-if="activeGroup.markers.every((m) => m.inherited)"
          class="marker-origin"
        >
          {{ tm("markers.inherited") }}
        </span>
        <button
          v-if="selectionOpen"
          type="button"
          class="marker-close"
          :aria-label="tm('markers.close')"
          @click="closePopover(true)"
        >
          <X :size="14" aria-hidden="true" />
        </button>
      </div>
      <template v-if="!selectionOpen">
        <div class="marker-preview-text">
          {{ activeGroup.markers[0].preview }}
        </div>
        <div class="marker-preview-hint">
          <ChevronRight :size="12" aria-hidden="true" />
          {{
            tm(
              activeGroup.markers.length > 1
                ? "markers.expand"
                : "markers.jump",
            )
          }}
        </div>
      </template>
      <div v-else class="marker-choice-list">
        <button
          v-for="marker in activeGroup.markers"
          :key="marker.id"
          type="button"
          class="marker-choice"
          :aria-current="marker.id === currentId ? 'true' : undefined"
          @click="selectMarker(marker)"
        >
          <span class="marker-choice-number">{{ marker.ordinal }}</span>
          <span class="marker-choice-text">
            <span v-if="marker.inherited" class="marker-choice-origin">{{
              tm("markers.inherited")
            }}</span>
            {{ marker.preview }}
          </span>
        </button>
      </div>
    </div>
  </nav>
</template>

<script lang="ts">
export interface ChatScrollMarker {
  id: string | number;
  // Absolute jump index; -1 keeps the loaded-message id fallback.
  index: number;
  // Absolute row index used to identify the question currently in view.
  messageIndex: number;
  // One-based question number in the available marker index.
  ordinal: number;
  topPct: number;
  preview: string;
  inherited: boolean;
}
</script>

<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { ChevronRight, LoaderCircle, X } from "@lucide/vue";
import { useModuleI18n } from "@/i18n/composables";

const props = defineProps<{
  markers: ChatScrollMarker[];
  container: HTMLElement | null;
  height: number;
  right: number;
  loading: boolean;
  isDark: boolean;
}>();
const emit = defineEmits<{ (event: "jump", marker: ChatScrollMarker): void }>();
const { tm } = useModuleI18n("features/chat");
const root = ref<HTMLElement | null>(null);
const popover = ref<HTMLElement | null>(null);
const coarsePointer = ref(false);
const visibleMessageIndex = ref<number | null>(null);
const activeSlot = ref<number | null>(null);
const selectionOpen = ref(false);
const popoverTop = ref(8);
let scrollFrame = 0;
let pointerQuery: MediaQueryList | null = null;

// Fixed slots keep neighboring hit targets separate, including groups whose
// inherited and current-session messages meet at the same position.
const groups = computed(() => {
  const inset = Math.min(14, props.height / 2);
  const available = Math.max(0, props.height - inset * 2);
  const slots = Math.max(
    1,
    Math.floor(available / (coarsePointer.value ? 44 : 24)) + 1,
  );
  const result = new Map<
    number,
    { slot: number; top: number; markers: ChatScrollMarker[] }
  >();
  for (const marker of props.markers) {
    const position = Math.max(0, Math.min(100, marker.topPct));
    const slot = Math.round((position / 100) * (slots - 1));
    let group = result.get(slot);
    if (!group) {
      group = {
        slot,
        top: inset + (slot / Math.max(1, slots - 1)) * available,
        markers: [],
      };
      result.set(slot, group);
    }
    group.markers.push(marker);
  }
  return [...result.values()];
});
const activeGroup = computed(() =>
  groups.value.find((group) => group.slot === activeSlot.value),
);
const currentId = computed(() => {
  if (visibleMessageIndex.value == null) return null;
  for (let i = props.markers.length - 1; i >= 0; i--) {
    if (props.markers[i].messageIndex <= visibleMessageIndex.value)
      return props.markers[i].id;
  }
  return null;
});

/** Read the visible message without changing the chat's scroll position. */
function updateReadingPosition() {
  const container = props.container;
  visibleMessageIndex.value = null;
  if (!container) return;
  const focusY =
    container.getBoundingClientRect().top + container.clientHeight / 3;
  for (const row of container.querySelectorAll<HTMLElement>(".message-row")) {
    const rect = row.getBoundingClientRect();
    if (rect.height <= 0 || rect.width <= 0) continue;
    const index = Number(row.dataset.messageIndex);
    if (!Number.isFinite(index)) continue;
    if (rect.top > focusY) {
      if (visibleMessageIndex.value == null) visibleMessageIndex.value = index;
      break;
    }
    visibleMessageIndex.value = index;
  }
}

watch(
  () => props.container,
  (container, _, onCleanup) => {
    const onScroll = () => {
      if (!selectionOpen.value) closePopover();
      if (!scrollFrame)
        scrollFrame = requestAnimationFrame(() => {
          scrollFrame = 0;
          updateReadingPosition();
        });
    };
    container?.addEventListener("scroll", onScroll, { passive: true });
    updateReadingPosition();
    onCleanup(() => {
      container?.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(scrollFrame);
      scrollFrame = 0;
    });
  },
  { immediate: true, flush: "post" },
);
watch([() => props.markers, () => props.height], updateReadingPosition, {
  flush: "post",
});
watch(
  () => props.loading,
  (loading) => {
    if (loading) closePopover();
  },
);
watch(
  [
    () => props.height,
    () => coarsePointer.value,
    () => props.markers.map((m) => `${m.id}:${m.topPct}`).join("|"),
  ],
  () => closePopover(),
);

/**
 * Show a summary or expand the exact messages represented by a dense marker.
 *
 * Args:
 *   group: Marker slot to preview.
 *   select: Keep the popover open and focus its message choices.
 */
async function openPopover(
  group: (typeof groups.value)[number],
  select = false,
) {
  if (props.loading || (selectionOpen.value && !select)) return;
  activeSlot.value = group.slot;
  selectionOpen.value = select;
  await nextTick();
  if (activeSlot.value !== group.slot || !popover.value) return;
  popoverTop.value = Math.max(
    8,
    Math.min(
      props.height - popover.value.offsetHeight - 8,
      group.top - popover.value.offsetHeight / 2,
    ),
  );
  if (select)
    popover.value
      .querySelector<HTMLButtonElement>(".marker-choice")
      ?.focus({ preventScroll: true });
}

/**
 * Dismiss the popover and optionally return keyboard focus to its marker.
 *
 * Args:
 *   restoreFocus: Return focus after Escape or the close button is used.
 */
function closePopover(restoreFocus = false) {
  const slot = activeSlot.value;
  selectionOpen.value = false;
  activeSlot.value = null;
  if (restoreFocus) {
    root.value
      ?.querySelector<HTMLButtonElement>(`[data-slot="${slot}"]`)
      ?.focus({ preventScroll: true });
    // A focus event may open the summary again; Escape must dismiss it.
    activeSlot.value = null;
  }
}

function selectMarker(marker: ChatScrollMarker) {
  if (props.loading) return;
  closePopover(true);
  emit("jump", marker);
}

function onFocusOut(event: FocusEvent) {
  // Window blur has no next element; preserve the reader's open choices.
  if (event.relatedTarget && !root.value?.contains(event.relatedTarget as Node))
    closePopover();
}

function onOutsidePointer(event: PointerEvent) {
  if (!root.value?.contains(event.target as Node | null)) closePopover();
}

function onPointerChange(event: MediaQueryListEvent) {
  coarsePointer.value = event.matches;
}

onMounted(() => {
  pointerQuery = window.matchMedia("(pointer: coarse)");
  coarsePointer.value = pointerQuery.matches;
  pointerQuery.addEventListener("change", onPointerChange);
  document.addEventListener("pointerdown", onOutsidePointer);
  updateReadingPosition();
});
onBeforeUnmount(() => {
  pointerQuery?.removeEventListener("change", onPointerChange);
  document.removeEventListener("pointerdown", onOutsidePointer);
  cancelAnimationFrame(scrollFrame);
});
</script>

<style scoped>
.scroll-marker-strip {
  --marker-surface: #fff;
  --marker-text: #242427;
  --marker-muted: #79797f;
  --marker-line: #b8b8be;
  --marker-active: #45454c;
  --marker-border: #e8e8ec;
  --marker-hover: #f4f4f6;
  position: absolute;
  top: 0;
  width: 28px;
  z-index: 5;
  pointer-events: none;
  color: var(--marker-text);
}
.scroll-marker-strip.is-dark {
  --marker-surface: #29292d;
  --marker-text: #e6e6e9;
  --marker-muted: #a0a0a9;
  --marker-line: #686870;
  --marker-active: #dedee6;
  --marker-border: #39393f;
  --marker-hover: #303035;
}
.scroll-marker-dot {
  position: absolute;
  right: 0;
  width: 28px;
  height: 20px;
  display: grid;
  place-items: center;
  transform: translateY(-50%);
  border: 0;
  border-radius: 5px;
  padding: 0;
  background: transparent;
  pointer-events: auto;
  cursor: pointer;
}
.marker-tick {
  width: 8px;
  height: 2px;
  border-radius: 2px;
  background: var(--marker-line);
  opacity: 0.7;
  transition:
    width 0.16s ease,
    opacity 0.16s ease,
    background 0.16s ease;
}
.scroll-marker-dot--current .marker-tick {
  width: 15px;
  height: 3px;
  background: var(--marker-active);
  opacity: 1;
}
.scroll-marker-dot--inherited .marker-tick {
  height: 4px;
  background: transparent;
  border: 1px solid var(--marker-line);
}
.scroll-marker-dot--inherited.scroll-marker-dot--current .marker-tick {
  border-color: var(--marker-active);
}
.scroll-marker-dot--grouped::before {
  content: "";
  position: absolute;
  top: 5px;
  width: 6px;
  height: 1px;
  border-radius: 1px;
  background: var(--marker-line);
}
.scroll-marker-dot:hover .marker-tick,
.scroll-marker-dot:focus-visible .marker-tick {
  width: 20px;
  opacity: 1;
}
.scroll-marker-dot:focus-visible,
.marker-choice:focus-visible,
.marker-close:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}
.scroll-marker-dot:disabled {
  cursor: default;
  opacity: 0.35;
}
.scroll-marker-popover {
  position: absolute;
  right: calc(100% + 8px);
  display: flex;
  flex-direction: column;
  border: 1px solid var(--marker-border);
  border-radius: 11px;
  padding: 13px 15px;
  background: var(--marker-surface);
  box-shadow:
    0 12px 40px rgb(0 0 0 / 0.08),
    0 2px 8px rgb(0 0 0 / 0.04);
  overflow: hidden;
  pointer-events: none;
  text-align: left;
}
.scroll-marker-popover.is-selection {
  pointer-events: auto;
  padding: 10px 7px 7px;
}
.marker-preview-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--marker-muted);
  font-size: 11px;
  margin-bottom: 7px;
}
.is-selection .marker-preview-meta {
  padding: 0 7px;
}
.marker-origin {
  font-size: 10px;
  border: 1px solid var(--marker-border);
  border-radius: 4px;
  padding: 1px 5px;
}
.marker-preview-text {
  font-size: 13px;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}
.marker-preview-hint {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--marker-muted);
  margin-top: 9px;
}
.marker-close {
  margin-left: auto;
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border: 0;
  border-radius: 5px;
  color: var(--marker-muted);
  background: transparent;
  cursor: pointer;
}
.marker-close:hover {
  background: var(--marker-hover);
}
.marker-choice-list {
  overflow-y: auto;
  min-height: 0;
  max-height: 340px;
  overscroll-behavior: contain;
  scrollbar-width: thin;
}
.marker-choice {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 9px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--marker-text);
  text-align: left;
  cursor: pointer;
}
.marker-choice:hover,
.marker-choice[aria-current="true"] {
  background: var(--marker-hover);
}
.marker-choice-number {
  width: 24px;
  flex-shrink: 0;
  padding-top: 2px;
  color: var(--marker-muted);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.marker-choice-text {
  font-size: 12px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.marker-choice-origin {
  display: block;
  font-size: 10px;
  color: var(--marker-muted);
}
.scroll-marker-loading {
  position: absolute;
  top: 8px;
  right: 32px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 9px;
  border: 1px solid var(--marker-border);
  border-radius: 7px;
  background: var(--marker-surface);
  color: var(--marker-muted);
  font-size: 11px;
  white-space: nowrap;
}
.loading-icon {
  animation: marker-loading 1s linear infinite;
}
.is-coarse {
  width: 44px;
}
.is-coarse .scroll-marker-dot {
  width: 44px;
  height: 44px;
}
.is-coarse .marker-choice,
.is-coarse .marker-close {
  min-height: 44px;
}
.is-coarse .marker-close {
  min-width: 44px;
}
@keyframes marker-loading {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .marker-tick {
    transition: none;
  }
  .loading-icon {
    animation: none;
  }
}
</style>
