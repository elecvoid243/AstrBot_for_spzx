<!-- Author: elecvoid243, 2026-06-21
     Spec: docs/superpowers/specs/2026-06-21-file-browser-inline-comments-design.md §4.2
     2026-07-17 selection-comment: drag-select to open an action
     menu (copy / comment). Implemented as a self-contained
     selection listener + line mapper; the parent owns the
     editor-open logic.
     2026-08-11 elecvoid243 inline-ask: the answer bubble is now
     user-resizable via a bottom-right grip; the size is clamped
     to the viewport and persisted to localStorage. -->
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import {
  commentCoversLine,
  type FileComment,
} from "@/composables/useFileComments";
import {
  useInlineAnnotations,
  type InlineAnnotation,
} from "@/composables/useInlineAnnotations";
import SelectionActionMenu from "./SelectionActionMenu.vue";
import MarkdownView from "@/components/shared/MarkdownView.vue";

const props = withDefaults(
  defineProps<{
    highlightedHtml: string;
    filePath: string;
    comments: FileComment[];
    activeEditLine: number | null;
    activeEditCommentId: string | null;
    isDark: boolean;
    /**
     * 2026-07-02 sidebar-search: 1-based line number to center in the
     * code view after a search-result click. null / 0 = no scroll.
     */
    scrollToLine?: number | null;
    scrollToEndLine?: number | null;
    /**
     * 2026-07-17 selection-comment: when false, the selection menu
     * shows only the "copy" item (no comment). Defaults to true.
     * Historical-raw views pass false because their line numbers
     * refer to a stale blob and shouldn't accept comments.
     */
    selectionCommentable?: boolean;
    /** 2026-07-30 inline-ask: annotations for this file. */
    annotations?: InlineAnnotation[];
  }>(),
  {
    scrollToLine: null,
    selectionCommentable: true,
    annotations: () => [],
  },
);

const emit = defineEmits<{
  (e: "request-add", line: number): void;
  (e: "request-edit", commentId: string): void;
  (e: "request-add-range", payload: {
    startLine: number;
    endLine: number;
    selection: string;
  }): void;
  (e: "request-ask-inline", payload: {
    startLine: number;
    endLine: number;
    selection: string;
  }): void;
  (e: "copy-selection", text: string): void;
  (e: "delete-annotation", id: string): void;
}>();

const { tm } = useModuleI18n("features/chat");

// 2026-07-30 inline-ask: annotation helpers (module-level singleton)
const inlineAnnotations = useInlineAnnotations();

/** Total line count derived from the Shiki output by counting
 *  `<span class="line">` wrappers. Single point that knows the
 *  Shiki DOM convention (see spec §6 risk #1). Returns a number
 *  (not an array) so the template can use `v-for="line in count"`. */
const lineCount = computed<number>(() => {
  const m = props.highlightedHtml.match(/<span class="line">/g);
  return m ? m.length : 0;
});

const codeContentRef = ref<HTMLElement | null>(null);
const hoveredLine = ref<number | null>(null);

// 2026-07-30 inline-ask: tooltip state for annotation bubbles
const annotationTooltip = ref<{
  visible: boolean;
  x: number;
  y: number;
  annotation: InlineAnnotation | null;
}>({ visible: false, x: 0, y: 0, annotation: null });

// 2026-08-11 inline-ask resizable bubble: user-adjustable tooltip
// size, persisted across remounts and sessions. null = auto layout
// (CSS default max-width/max-height apply).
const TOOLTIP_SIZE_KEY = "astrbot.spcode.inlineAsk.tooltipSize";
const TOOLTIP_MIN_WIDTH = 200;
const TOOLTIP_MIN_HEIGHT = 120;
const TOOLTIP_VIEWPORT_MARGIN = 16;

const annotationTooltipRef = ref<HTMLElement | null>(null);
const tooltipSize = ref<{ width: number; height: number } | null>(null);
try {
  const raw = localStorage.getItem(TOOLTIP_SIZE_KEY);
  const parsed: unknown = raw ? JSON.parse(raw) : null;
  if (
    parsed !== null &&
    typeof parsed === "object" &&
    typeof (parsed as { width?: unknown }).width === "number" &&
    typeof (parsed as { height?: unknown }).height === "number"
  ) {
    const { width, height } = parsed as { width: number; height: number };
    tooltipSize.value = { width, height };
  }
} catch {
  // Ignore malformed persisted sizes and fall back to auto layout.
}

const tooltipStyle = computed<Record<string, string>>(() => {
  const style: Record<string, string> = {
    left: annotationTooltip.value.x + "px",
    top: annotationTooltip.value.y + "px",
  };
  if (tooltipSize.value) {
    // Clamp the persisted size so the bubble never overflows the
    // current viewport (e.g. after the window got smaller).
    const maxWidth = Math.max(
      window.innerWidth - annotationTooltip.value.x - TOOLTIP_VIEWPORT_MARGIN,
      TOOLTIP_MIN_WIDTH,
    );
    const maxHeight = Math.max(
      window.innerHeight - annotationTooltip.value.y - TOOLTIP_VIEWPORT_MARGIN,
      TOOLTIP_MIN_HEIGHT,
    );
    style.width = Math.min(tooltipSize.value.width, maxWidth) + "px";
    style.height = Math.min(tooltipSize.value.height, maxHeight) + "px";
  }
  return style;
});

/** Start a bottom-right grip drag that resizes the annotation
 *  tooltip. Listeners live on window so the drag survives the
 *  pointer leaving the bubble; size is persisted on mouseup. */
function startTooltipResize(e: MouseEvent): void {
  const el = annotationTooltipRef.value;
  if (!el) return;
  const startX = e.clientX;
  const startY = e.clientY;
  const startWidth = el.offsetWidth;
  const startHeight = el.offsetHeight;
  const maxWidth = Math.max(
    window.innerWidth - annotationTooltip.value.x - TOOLTIP_VIEWPORT_MARGIN,
    TOOLTIP_MIN_WIDTH,
  );
  const maxHeight = Math.max(
    window.innerHeight - annotationTooltip.value.y - TOOLTIP_VIEWPORT_MARGIN,
    TOOLTIP_MIN_HEIGHT,
  );

  function onMove(ev: MouseEvent): void {
    tooltipSize.value = {
      width: Math.min(
        Math.max(startWidth + ev.clientX - startX, TOOLTIP_MIN_WIDTH),
        maxWidth,
      ),
      height: Math.min(
        Math.max(startHeight + ev.clientY - startY, TOOLTIP_MIN_HEIGHT),
        maxHeight,
      ),
    };
  }
  function onUp(): void {
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
    if (tooltipSize.value) {
      try {
        localStorage.setItem(
          TOOLTIP_SIZE_KEY,
          JSON.stringify(tooltipSize.value),
        );
      } catch {
        // Persistence is best-effort only.
      }
    }
  }
  // Suppress text selection and keep the resize cursor while dragging.
  document.body.style.userSelect = "none";
  document.body.style.cursor = "nwse-resize";
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
}

// 2026-07-17 selection-comment: gutter coverage now considers
// range comments (commentCoversLine walks [line, endLine] for
// comments with endLine > line). Single-line comments are just
// [n, n] — handled uniformly.
function hasComment(line: number): boolean {
  return props.comments.some((c) => commentCoversLine(c, line));
}
function commentText(line: number): string {
  return props.comments.find((c) => commentCoversLine(c, line))?.text ?? "";
}
function commentIdFor(line: number): string | null {
  return props.comments.find((c) => commentCoversLine(c, line))?.id ?? null;
}

// 2026-07-30 inline-ask: annotation helpers
function hasAnnotation(line: number): boolean {
  return props.annotations.some((a) => inlineAnnotations.annotationCoversLine(a, line));
}
function annotationForLine(line: number): InlineAnnotation | null {
  return props.annotations.find((a) => inlineAnnotations.annotationCoversLine(a, line)) ?? null;
}

function onMouseMove(e: MouseEvent): void {
  if (!codeContentRef.value) return;
  const lineEls = codeContentRef.value.querySelectorAll<HTMLElement>(".line");
  for (let i = 0; i < lineEls.length; i++) {
    const rect = lineEls[i].getBoundingClientRect();
    if (rect.bottom > e.clientY) {
      hoveredLine.value = i + 1;
      return;
    }
  }
  hoveredLine.value = lineEls.length || null;
}

/** Clear the hovered line when the cursor leaves the entire code-view
 *  (gutter + line numbers + code area). Without this, the add button
 *  would stay visible after the mouse leaves the component. */
function onMouseLeave(): void {
  hoveredLine.value = null;
}

// ── 2026-07-17 selection-comment ────────────────────────────────
// Drag-select inside the code content opens a small fixed-position
// menu (copy / comment) anchored at the mouse-up point. The parent
// owns editor-open and clipboard logic; this component is only
// responsible for the line-mapping math and the menu lifecycle.

interface RangeSnapshot {
  x: number;
  y: number;
  startLine: number;
  endLine: number;
  selection: string;
}

const menuState = ref<RangeSnapshot | null>(null);

function closeMenu(): void {
  menuState.value = null;
}

/** Walk up from `node` to the nearest `.line` ancestor and return
 *  its 0-based index among sibling `.line` elements inside the
 *  Shiki container. Returns null if no `.line` ancestor is found
 *  (e.g. the selection starts in the gutter or line-number column). */
function lineElToIndex(node: Node | null): number | null {
  let n: Node | null = node;
  while (n instanceof Node && !(n instanceof HTMLElement)) {
    n = n.parentNode;
  }
  while (n instanceof HTMLElement && !n.classList.contains("line")) {
    n = n.parentElement;
  }
  if (!(n instanceof HTMLElement)) return null;
  if (!codeContentRef.value) return null;
  const all = codeContentRef.value.querySelectorAll<HTMLElement>(".line");
  return Array.from(all).indexOf(n);
}

function onContentMouseUp(e: MouseEvent): void {
  // Ignore mouse-ups inside the gutter or line-number column —
  // those aren't part of the commentable code area, and a stray
  // selection that started in the gutter would otherwise force us
  // to bail (lineElToIndex returns null). Better to skip upfront.
  if (
    e.target instanceof HTMLElement &&
    (e.target.closest(".code-gutter") ||
      e.target.closest(".line-numbers"))
  ) {
    return;
  }
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed) {
    closeMenu();
    return;
  }
  if (!sel.anchorNode || !sel.focusNode) return;
  if (!codeContentRef.value || !codeContentRef.value.contains(sel.anchorNode)) {
    closeMenu();
    return;
  }
  const aIdx = lineElToIndex(sel.anchorNode);
  const fIdx = lineElToIndex(sel.focusNode);
  if (aIdx === null || fIdx === null) {
    closeMenu();
    return;
  }
  const startLine = Math.min(aIdx, fIdx) + 1;
  const endLine = Math.max(aIdx, fIdx) + 1;
  const text = sel.toString();
  if (!text.trim()) {
    closeMenu();
    return;
  }
  menuState.value = {
    x: e.clientX,
    y: e.clientY,
    startLine,
    endLine,
    selection: text,
  };
}

/** Collapse outside the menu (a click anywhere else) closes it.
 *  Named so `onBeforeUnmount` can remove it (avoiding a listener
 *  leak across remounts of the same component instance). */
function onDocMouseDown(e: MouseEvent): void {
  if (!menuState.value) {
    // Also close annotation tooltip on outside click
    if (annotationTooltip.value.visible) {
      const t = e.target;
      if (
        t instanceof HTMLElement &&
        !t.closest(".annotation-tooltip") &&
        !t.closest(".line-annotated")
      ) {
        closeAnnotationTooltip();
      }
    }
    return;
  }
  const t = e.target;
  if (t instanceof HTMLElement && t.closest(".selection-action-menu")) return;
  closeMenu();
}

/** selectionchange fires on every selection tweak. When the
 *  selection collapses (e.g. the user clicks blank space), close
 *  the menu on the next microtask so the click that collapsed it
 *  can finish propagating without us reopening. */
function onSelectionChange(): void {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed) {
    queueMicrotask(closeMenu);
  }
}

function onMenuCopy(): void {
  if (menuState.value) emit("copy-selection", menuState.value.selection);
}

function onMenuComment(): void {
  if (!menuState.value) return;
  emit("request-add-range", {
    startLine: menuState.value.startLine,
    endLine: menuState.value.endLine,
    selection: menuState.value.selection,
  });
  closeMenu();
}

function onMenuAskInline(): void {
  if (!menuState.value) return;
  emit("request-ask-inline", {
    startLine: menuState.value.startLine,
    endLine: menuState.value.endLine,
    selection: menuState.value.selection,
  });
  closeMenu();
}

if (typeof window !== "undefined") {
  document.addEventListener("mousedown", onDocMouseDown);
  document.addEventListener("selectionchange", onSelectionChange);
}

// 2026-07-30 inline-ask: apply underline classes to annotated lines
// via DOM manipulation after Vue renders the v-html content.
watch(
  [() => props.highlightedHtml, () => props.annotations, () => props.filePath],
  async () => {
    await nextTick();
    applyAnnotationClasses();
  },
  { flush: "post" },
);

function applyAnnotationClasses(): void {
  if (!codeContentRef.value) return;
  const lineEls = codeContentRef.value.querySelectorAll<HTMLElement>(".line");
  // Clear previous annotation classes
  lineEls.forEach((el) => el.classList.remove("line-annotated"));
  // Apply to annotated lines
  for (const ann of props.annotations) {
    for (let i = ann.startLine; i <= ann.endLine && i <= lineEls.length; i++) {
      lineEls[i - 1]?.classList.add("line-annotated");
    }
  }
}

/** 2026-07-30 inline-ask: click on annotated line shows tooltip. */
function onCodeContentClick(e: MouseEvent): void {
  const target = e.target as HTMLElement | null;
  if (!target || !codeContentRef.value) return;
  // Walk up to the .line element
  let lineEl: HTMLElement | null = target;
  while (lineEl && !lineEl.classList.contains("line")) {
    lineEl = lineEl.parentElement;
  }
  if (!lineEl || !lineEl.classList.contains("line-annotated")) {
    annotationTooltip.value.visible = false;
    return;
  }
  // Determine which line was clicked
  const allLines = codeContentRef.value.querySelectorAll<HTMLElement>(".line");
  const lineIdx = Array.from(allLines).indexOf(lineEl);
  if (lineIdx < 0) return;
  const lineNo = lineIdx + 1;
  const ann = annotationForLine(lineNo);
  if (!ann) return;
  const rect = lineEl.getBoundingClientRect();
  annotationTooltip.value = {
    visible: true,
    x: rect.left,
    y: rect.bottom + 4,
    annotation: ann,
  };
}

function closeAnnotationTooltip(): void {
  annotationTooltip.value.visible = false;
}

function onDeleteAnnotation(id: string): void {
  emit("delete-annotation", id);
  closeAnnotationTooltip();
}

onBeforeUnmount(() => {
  if (typeof window !== "undefined") {
    document.removeEventListener("mousedown", onDocMouseDown);
    document.removeEventListener("selectionchange", onSelectionChange);
  }
  closeMenu();
});

// Hide the menu when the file's Shiki output changes (the .line
// indices would be stale) or the file itself switches.
watch(
  () => [props.highlightedHtml, props.filePath],
  () => closeMenu(),
);

// 2026-07-02 sidebar-search: center the requested line in the
// code-view scroll container when a search result is clicked.
//
// We watch three props so the scroll also re-fires after a slow
// file load (the user clicks a result while the file is still
// fetching, then `highlightedHtml` updates once Shiki has rendered
// the .line elements):
//   - props.scrollToLine   (1-based line number, null/0 = no-op)
//   - props.filePath        (new file)
//   - props.highlightedHtml (Shiki output for the new file)
//
// `scrollIntoView({ block: 'center' })` is used instead of a manual
// `scrollTop` calculation because the .code-view's grid layout
// (gutter + line numbers + <pre>) makes offsetTop arithmetic
// fragile across viewport sizes; the browser's native
// scrollIntoView walks the .line span and centers it in the
// nearest scrollable ancestor, which is exactly the .code-view.
// `behavior: 'smooth'` is gated on the document's reduced-motion
// preference to respect accessibility settings.
watch(
  [() => props.scrollToLine, () => props.filePath, () => props.highlightedHtml,
    () => props.scrollToEndLine, () => codeContentRef.value],
  async ([line, path, html]) => {
    if (!codeContentRef.value || !html) return;
    // Wait for the v-for + v-html to commit the .line spans to the
    // DOM. The Shiki render is sync but v-for reconciliation runs
    // on the next microtask, so one nextTick is sufficient.
    await nextTick();
    if (!codeContentRef.value) return;
    const lineEls = codeContentRef.value.querySelectorAll<HTMLElement>(".line");
    lineEls.forEach((element) => element.classList.remove("line-referenced"));
    if (!line || line < 1 || line !== props.scrollToLine ||
      path !== props.filePath || html !== props.highlightedHtml) return;
    const idx = line - 1;
    const end = props.scrollToEndLine ?? line;
    if (end > lineEls.length || end < line) return;
    for (let i = idx; i < end; i++) lineEls[i].classList.add("line-referenced");
    const reduceMotion =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    lineEls[idx].scrollIntoView({
      block: "center",
      behavior: reduceMotion ? "auto" : "smooth",
    });
  },
);
</script>

<template>
  <div
    class="code-view"
    :class="{ dark: isDark }"
    @mousemove="onMouseMove"
    @mouseleave="onMouseLeave"
    @scroll="closeMenu"
  >
    <div class="code-gutter">
      <div v-for="line in lineCount" :key="line" class="gutter-cell">
        <button
          v-if="line === hoveredLine && !hasComment(line)"
          class="gutter-add-btn"
          :aria-label="
            tm('spcodeProjectLoad.fileBrowser.comment.addButtonAria', { line })
          "
          @click="emit('request-add', line)"
        >
          +
        </button>
        <button
          v-else-if="hasComment(line)"
          class="gutter-comment-indicator"
          :title="commentText(line)"
          :aria-label="
            tm('spcodeProjectLoad.fileBrowser.comment.indicatorAria', {
              line,
              preview: commentText(line),
            })
          "
          @click="emit('request-edit', commentIdFor(line) ?? '')"
        >
          <v-icon size="12">mdi-comment-text-outline</v-icon>
        </button>
      </div>
    </div>
    <div class="line-numbers">
      <div v-for="line in lineCount" :key="line" class="line-number-cell">
        {{ line }}
      </div>
    </div>
    <pre
      ref="codeContentRef"
      class="code-content"
      v-html="highlightedHtml"
      @mouseup="onContentMouseUp"
      @click="onCodeContentClick"
    />
    <!-- 2026-07-17 selection-comment: action menu opens at the
         mouse-up position when the user drags across one or more
         .line spans. Position is fixed (viewport-anchored), so
         scroll-close above keeps it from getting stranded. -->
    <SelectionActionMenu
      v-if="menuState"
      :x="menuState.x"
      :y="menuState.y"
      :show-comment="props.selectionCommentable"
      @copy="onMenuCopy"
      @comment="onMenuComment"
      @ask-inline="onMenuAskInline"
      @close="closeMenu"
    />
    <!-- 2026-07-30 inline-ask: annotation tooltip bubble -->
    <div
      v-if="annotationTooltip.visible && annotationTooltip.annotation"
      ref="annotationTooltipRef"
      class="annotation-tooltip"
      :class="{ 'annotation-tooltip--resized': tooltipSize !== null }"
      :style="tooltipStyle"
      @mousedown.stop
    >
      <div class="annotation-tooltip-header">
        <v-icon size="12">mdi-lightbulb-on-outline</v-icon>
        <span class="annotation-tooltip-question">
          {{ annotationTooltip.annotation.question }}
        </span>
        <!-- 2026-08-11 inline-ask: split the old single "x" into a
             delete button (removes the annotation) and a close
             button (hides the bubble only, annotation kept). -->
        <button
          type="button"
          class="annotation-tooltip-btn annotation-tooltip-btn--danger"
          :aria-label="tm('spcodeProjectLoad.fileBrowser.inlineAsk.deleteAria')"
          :title="tm('spcodeProjectLoad.fileBrowser.inlineAsk.tooltipDelete')"
          @click="onDeleteAnnotation(annotationTooltip.annotation.id)"
        >
          <v-icon size="12">mdi-delete-outline</v-icon>
        </button>
        <button
          type="button"
          class="annotation-tooltip-btn"
          :aria-label="tm('spcodeProjectLoad.fileBrowser.inlineAsk.closeAria')"
          :title="tm('spcodeProjectLoad.fileBrowser.inlineAsk.tooltipClose')"
          @click="closeAnnotationTooltip"
        >
          <v-icon size="12">mdi-close</v-icon>
        </button>
      </div>
      <div class="annotation-tooltip-body">
        <template v-if="annotationTooltip.annotation.loading">
          <v-progress-circular
            indeterminate
            size="14"
            color="primary"
            class="mr-2"
          />
          {{ tm("spcodeProjectLoad.fileBrowser.inlineAsk.loading") }}
        </template>
        <template v-else-if="annotationTooltip.annotation.error">
          <v-icon size="12" color="error" class="mr-1">mdi-alert-circle-outline</v-icon>
          {{ annotationTooltip.annotation.error }}
        </template>
        <!-- 2026-07-31: render LLM reply as markdown — models almost
             always answer with markdown (code fences, lists, bold),
             which reads poorly as raw pre-wrap plain text. -->
        <MarkdownView
          v-else
          :source="annotationTooltip.annotation.reply ?? ''"
          :is-dark="isDark"
        />
      </div>
      <!-- 2026-08-11 inline-ask: bottom-right resize grip -->
      <div
        class="annotation-tooltip-resize"
        aria-hidden="true"
        @mousedown.stop.prevent="startTooltipResize"
      ></div>
    </div>
  </div>
</template>

<style scoped>
.code-view {
  flex: 1;
  display: grid;
  grid-template-columns: 24px auto 1fr;
  min-height: 0;
  overflow: auto;
  background: transparent;
}
.code-gutter,
.line-numbers {
  display: flex;
  flex-direction: column;
}
.gutter-cell,
.line-number-cell {
  /* 1.55em matches .code-content line-height * font-size (12.5px).
     The line-height on the cell is required so the cell box itself
     is exactly 1.55em tall — otherwise the flex item's intrinsic
     height uses the default line-height (~1.2) and the cells
     become shorter than the Shiki lines. */
  height: 1.55em;
  display: flex;
  align-items: center;
  font-family: ui-monospace, monospace;
  font-size: 12.5px;
  line-height: 1.55;
}
.gutter-add-btn {
  /* As soon as the hovered line reveals this button, it must look
     like a real, clickable chip — not a faint glyph that the user
     has to chase. Design intent (matches GitHub PR review gutter):
       • filled tinted background so it pops off the code line in
         both light and dark themes
       • 1.5px solid primary-tinted border for a clear "chip" edge
       • 0.85 baseline opacity: visible the moment it appears, no
         need to "find" it before clicking
       • subtle drop shadow for depth so it reads as a layered
         control, not a glyph painted on the code
     On direct hover/focus the chip becomes a fully-saturated,
     slightly-scaled, glowing target — unmistakable click affordance.
     Previous iteration (opacity 0.35 + transparent bg + thin
     0.4-alpha border) read as a stray text character. */
  opacity: 0.85;
  width: 20px;
  height: 20px;
  background: rgba(var(--v-theme-primary), 0.2);
  border: 1.5px solid rgba(var(--v-theme-primary), 0.7);
  border-radius: 5px;
  cursor: pointer;
  color: rgb(var(--v-theme-primary));
  margin: 0 auto;
  font-size: 14px;
  font-weight: 700;
  line-height: 1;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.12);
  transition:
    opacity 0.12s ease,
    background 0.12s ease,
    border-color 0.12s ease,
    transform 0.12s ease,
    box-shadow 0.12s ease;
}
/* Dark theme: lift the fill a touch and use a deeper shadow.
   Against a dark code background the same rgba(primary, 0.2) tint
   reads weaker than it does in light mode, so we bump the alpha
   and rely on a stronger (darker, larger) shadow for depth. */
.code-view.dark .gutter-add-btn {
  background: rgba(var(--v-theme-primary), 0.28);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.45);
}
.gutter-cell:hover .gutter-add-btn,
.gutter-add-btn:hover,
.gutter-add-btn:focus {
  opacity: 1;
  background: rgba(var(--v-theme-primary), 0.4);
  border-color: rgb(var(--v-theme-primary));
  transform: scale(1.15);
  /* Colored glow on hover mirrors the "primary accent" used
     elsewhere in the sidebar (scope pills, worktree tabs), so
     the affordance language stays consistent. */
  box-shadow: 0 2px 6px rgba(var(--v-theme-primary), 0.45);
}
.gutter-add-btn:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}
.gutter-comment-indicator {
  width: 18px;
  height: 18px;
  background: rgba(var(--v-theme-warning), 0.15);
  border: none;
  border-radius: 4px;
  cursor: pointer;
  color: rgb(var(--v-theme-warning));
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
}
.line-number-cell {
  padding-right: 8px;
  justify-content: flex-end;
  color: rgba(var(--v-theme-on-surface), 0.4);
  font-variant-numeric: tabular-nums;
  user-select: none;
}
.code-content {
  margin: 0;
  padding: 0 14px;
  background: transparent !important;
  font-family: ui-monospace, monospace;
  font-size: 12.5px;
  line-height: 1.55;
}
/* Reset Shiki's default <pre>/<code> styles. Two problems to fix:

   1. The default `<pre>` has `margin: 1em 0` (12.5px top/bottom) which
      pushed code content down relative to the gutter columns — that's
      the "blank line before line 1" symptom.

   2. Shiki emits a literal `\n` between each `<span class="line">`
      (the spans sit on separate source lines). When the `<code>` is
      `display: inline` (UA default) and lives inside a `<pre>`
      (`white-space: pre`), those `\n` text nodes render as line
      breaks, producing the "blank line after every code line" symptom
      and desyncing the line-number column.

   Fix: turn the inner `<code>` into a `flex` column container. Flex
   layout IGNORES the raw text nodes between flex items, so the `\n`
   separators cannot influence box generation at all — independent of
   `white-space` rules. Each `.line` becomes a block-level flex item
   and stacks with zero gap. `white-space: pre` is kept on `.line`
   itself so internal source formatting (multiple spaces in
   `import os`) is preserved.
*/
.code-content :deep(pre) {
  display: block;
  margin: 0;
  padding: 0;
  background: transparent;
  font-family: inherit;
  font-size: inherit;
  line-height: inherit;
}
.code-content :deep(code) {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  background: transparent;
  font-family: inherit;
  font-size: inherit;
  line-height: inherit;
}
.code-content :deep(.line) {
  display: block;
  white-space: pre;
  min-height: 1.55em;
  line-height: 1.55;
}
.code-content :deep(.line.line-referenced) {
  background: rgba(var(--v-theme-warning), 0.22);
  box-shadow: inset 3px 0 rgb(var(--v-theme-warning));
}
@media (max-width: 760px) {
  .code-view {
    grid-template-columns: 16px auto 1fr;
  }
  .gutter-add-btn,
  .gutter-comment-indicator {
    width: 16px;
    height: 16px;
  }
  .gutter-add-btn {
    font-size: 12px;
  }
}
/* 2026-07-30 inline-ask: underline effect on annotated lines */
.code-content :deep(.line.line-annotated) {
  text-decoration: underline;
  text-decoration-color: rgb(var(--v-theme-primary));
  text-decoration-style: wavy;
  text-underline-offset: 3px;
  cursor: pointer;
  background: rgba(var(--v-theme-primary), 0.06);
  border-radius: 2px;
}
/* 2026-07-30 inline-ask: annotation tooltip bubble */
.annotation-tooltip {
  position: fixed;
  z-index: 1200;
  max-width: 420px;
  min-width: 200px;
  background: rgba(var(--v-theme-surface), 0.97);
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.15);
  border-radius: 8px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.18);
  font-size: 12.5px;
  user-select: text;
  overflow: hidden;
  /* 2026-08-11 resizable bubble: column flex so the body fills the
     explicit height set by the resize grip. */
  display: flex;
  flex-direction: column;
}
/* 2026-08-11 resizable bubble: once the user picked a size, inline
   width/height take over and the auto max-width cap must not apply. */
.annotation-tooltip--resized {
  max-width: none;
}
.annotation-tooltip-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  background: rgba(var(--v-theme-primary), 0.04);
  /* 2026-08-11 resizable bubble: keep the header at natural height
     when the tooltip has an explicit (small) user-set height. */
  flex-shrink: 0;
}
.annotation-tooltip-question {
  flex: 1;
  font-weight: 500;
  color: rgba(var(--v-theme-on-surface), 0.7);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 2026-08-11 inline-ask: shared header action button style; the
   --danger modifier marks the destructive (delete) action. */
.annotation-tooltip-btn {
  background: transparent;
  border: none;
  cursor: pointer;
  color: rgba(var(--v-theme-on-surface), 0.4);
  padding: 2px;
  border-radius: 3px;
  display: flex;
  align-items: center;
}
.annotation-tooltip-btn:hover {
  color: rgba(var(--v-theme-on-surface), 0.8);
  background: rgba(var(--v-theme-on-surface), 0.08);
}
.annotation-tooltip-btn--danger:hover {
  color: rgb(var(--v-theme-error));
  background: rgba(var(--v-theme-error), 0.1);
}
.annotation-tooltip-body {
  padding: 8px 10px;
  line-height: 1.5;
  word-break: break-word;
  max-height: 300px;
  overflow-y: auto;
  display: flex;
  align-items: flex-start;
  gap: 4px;
  /* 2026-08-11 resizable bubble: fill the explicit tooltip height. */
  flex: 1;
  min-height: 0;
}
/* 2026-08-11 resizable bubble: user-set height replaces the auto cap. */
.annotation-tooltip--resized .annotation-tooltip-body {
  max-height: none;
}
/* 2026-08-11 resizable bubble: bottom-right grip. Positioned
   absolutely against the fixed tooltip; kept subtle until hover. */
.annotation-tooltip-resize {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 16px;
  height: 16px;
  cursor: nwse-resize;
  touch-action: none;
}
.annotation-tooltip-resize::after {
  content: "";
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 7px;
  height: 7px;
  border-right: 2px solid rgba(var(--v-theme-on-surface), 0.35);
  border-bottom: 2px solid rgba(var(--v-theme-on-surface), 0.35);
  border-bottom-right-radius: 4px;
}
.annotation-tooltip-resize:hover::after {
  border-color: rgb(var(--v-theme-primary));
}
/* 2026-07-31 markdown reply: MarkdownView renders block content,
   so drop the flex-row / pre-wrap layout inherited from the
   plain-text version and let the markdown-body fill the tooltip. */
.annotation-tooltip-body :deep(.markdown-body) {
  flex: 1;
  min-width: 0;
  font-size: 12.5px;
}
.annotation-tooltip-body :deep(.markdown-body pre) {
  max-width: 100%;
  overflow-x: auto;
}
</style>
