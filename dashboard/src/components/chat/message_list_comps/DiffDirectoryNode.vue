<!-- Author: elecvoid243, 2026-07-28
     Spec: docs/superpowers/specs/2026-07-28-git-diff-recursive-directory-tree-design.md §3.5
     Recursive directory tree node. One section = one directory in
     the GitDiff sidebar's file list. Self-renders files (leaf rows)
     + children (nested sub-directories). All events bubble up to
     the parent (GitDiffBodyContent), which owns the
     collapsedGroups Set, selectedFiles Set, and refresh logic. -->
<script setup lang="ts">
import { computed, inject, provide } from "vue";
import type { SpcodeGitDiffFile, GitDiffScope } from "@/composables/parseSpcodeGitDiff";
import { useModuleI18n } from "@/i18n/composables";
import {
  buildDirectoryChain,
  type DiffSection,
} from "@/composables/parseDirectorySections";
import GitDiffFileItem from "./GitDiffFileItem.vue";

const { tm } = useModuleI18n("features/chat");

const props = defineProps<{
  section: DiffSection;
  depth: number;
  expanded: Set<string>;
  collapsedGroups: Set<string>;
  // 2026-10-25 compact-directory-chains: ids of chain heads the user
  // expanded into their real nested levels (owned by GitDiffBodyContent).
  expandedChains?: Set<string>;
  isDark: boolean;
  // Gate signals (booleans from parent)
  showStage: boolean;
  showUnstage: boolean;
  showRestore: boolean;
  newFilePaths?: ReadonlySet<string>;
  // 2026-10-25 chains: an ancestor chain is currently revealed, so this
  // node must render as an ordinary row (no breadcrumb compression).
  chainSuppressed?: boolean;
  // Selection helpers
  isSelected: (path: string) => boolean;
  isStagingForPath: (path: string) => boolean;
  isUnstagingForPath: (path: string) => boolean;
  isDiscardableFor: (f: SpcodeGitDiffFile) => boolean;
  isSectionFullySelected: (s: DiffSection) => boolean;
  isSectionPartiallySelected: (s: DiffSection) => boolean;
  /** Set of `${file.path}#${hunkIndex}` keys currently in flight. */
  discardingHunks?: ReadonlySet<string>;
  // i18n helpers
  selectableAriaLabel: (path: string) => string;
  // Callbacks
  onToggleGroup: (id: string) => void;
  onToggleChain?: (id: string) => void;
  onToggleSelect: (path: string, next: boolean) => void;
  onToggleGroupSelect: (s: DiffSection, next: boolean) => void;
  onFileToggle: (path: string) => void;
  onFileStage: (path: string) => void;
  onFileUnstage: (path: string) => void;
  onFileRestore: (path: string) => void;
  onFileOpen: (path: string) => void;
  onFileDiscardHunk?: (params: {
    file: string;
    hunkIndex: number;
    patchText: string;
    scope: GitDiffScope;
  }) => void;
  scope?: GitDiffScope;
}>();

/** Ancestor label provider chain. Each child reads the previous
 *  ancestor and overrides it with its own label, so a deeply nested
 *  node can render an ancestor context in its aria-label without
 *  walking the tree from the root. */
const PARENT_LABEL_KEY = "spcode:diffDirectoryNodeParentLabel";
const parentLabel = inject(PARENT_LABEL_KEY, "");
provide(PARENT_LABEL_KEY, props.section.label);

// Emit surface for the recursive node. Only string-payload events
// (leaf row actions) live here; object-payload events (`discard-hunk`,
// `select`) are routed through callback props because vue-tsc 1.6.0
// has a known overload-resolution quirk with 5+ overloads that
// picks the LAST entry's payload shape against unrelated call sites
// (e.g. `select`'s object payload gets matched against `open-file`'s
// string call site). Keeping the emit list short and homogeneous
// (every payload is `string`) avoids the bug entirely.
const emit = defineEmits<{
  (e: "toggle", path: string): void;
  (e: "restore", path: string): void;
  (e: "stage", path: string): void;
  (e: "unstage", path: string): void;
  (e: "open-file", path: string): void;
}>();

const isCollapsed = computed<boolean>(
  () => props.collapsedGroups.has(props.section.id),
);
const sectionHasContent = computed<boolean>(
  () => props.section.files.length > 0 || props.section.children.length > 0,
);
const anyBulkAffordance = computed<boolean>(
  () => props.showStage || props.showUnstage || props.showRestore,
);

// ── 2026-10-25 compact-directory-chains ──────────────────────────────
// A single-child directory run (every level holds exactly one
// sub-directory and no files) renders as ONE breadcrumb row instead of
// one near-empty header per level. Expanding the row reveals the real
// nested levels; a chain whose ancestor chain is revealed renders as an
// ordinary node (chainSuppressed) so the revealed run stays flat.

const selfChain = computed(() => buildDirectoryChain(props.section));

/** Breadcrumb segments with middle-ellipsis: >3 segments render as
 *  first › … › last (the middle names repeat the crumb's own path). */
const crumbSegments = computed<string[]>(() => {
  const chain = selfChain.value;
  if (!chain) return [];
  if (chain.segments.length <= 3) return chain.segments;
  return [chain.segments[0], "…", chain.segments[chain.segments.length - 1]];
});

const isChain = computed<boolean>(
  () => selfChain.value !== null && !props.chainSuppressed,
);

const isChainExpanded = computed<boolean>(
  () =>
    selfChain.value !== null &&
    !props.chainSuppressed &&
    (props.expandedChains?.has(props.section.id) ?? false),
);

/** Crumb-view aria path: full segment list, never ellipsised. */
const chainAriaPath = computed<string>(() =>
  selfChain.value ? selfChain.value.segments.join(" / ") : props.section.label,
);

/** Children of a chain LINK stay suppression-locked for the whole run;
 *  a terminal (branch / file holder) releases the lock so its own
 *  children may compress again. */
const childChainSuppressed = computed<boolean>(() => selfChain.value !== null);

function onHeaderClick(): void {
  if (selfChain.value !== null && !props.chainSuppressed) {
    // Chain header: toggle between the breadcrumb row and the revealed
    // levels (NOT the ordinary collapsedGroups collapse).
    props.onToggleChain?.(props.section.id);
    return;
  }
  props.onToggleGroup(props.section.id);
}

// Recursive counts for the section header badge. Walks the subtree
// in lock-step with GitDiffBodyContent's summary aggregation so
// the per-section meta matches the top-bar total.
function countDescendants(s: DiffSection): number {
  let n = s.fileCount;
  for (const c of s.children) n += countDescendants(c);
  return n;
}
function sumAdditions(s: DiffSection): number {
  let n = s.additions;
  for (const c of s.children) n += sumAdditions(c);
  return n;
}
function sumDeletions(s: DiffSection): number {
  let n = s.deletions;
  for (const c of s.children) n += sumDeletions(c);
  return n;
}
const recursiveFileCount = computed(() => countDescendants(props.section));
const recursiveAdditions = computed(() => sumAdditions(props.section));
const recursiveDeletions = computed(() => sumDeletions(props.section));

// Wrapper helpers that route through the appropriate channel:
// - File row events → re-emit (parent owns stagedFiles / dialogs)
function onSectionCheckChange(e: Event): void {
  const checked = (e.target as HTMLInputElement).checked;
  props.onToggleGroupSelect(props.section, checked);
}

// Recursive child event re-emitters. Defined as named functions
// (rather than inline `(p) => emit(...)` arrows) so vue-tsc 1.6.0
// resolves each emit name against the correct overload. The
// object-payload events (`discard-hunk`, `select`) are NOT
// re-emitted; the parent (GitDiffBodyContent) wires the per-file
// row callback (`:on-discard-hunk`, `:on-toggle-select`) directly,
// and the child section's per-row handler is invoked via the same
// prop callback. This keeps `emit` strictly string-payload and
// dodges vue-tsc's overload-resolution quirk for mixed-payload
// surfaces.
function onChildToggle(path: string): void {
  emit("toggle", path);
}
function onChildRestore(path: string): void {
  emit("restore", path);
}
function onChildStage(path: string): void {
  emit("stage", path);
}
function onChildUnstage(path: string): void {
  emit("unstage", path);
}
function onChildOpenFile(path: string): void {
  emit("open-file", path);
}
</script>

<template>
  <div
    class="git-diff-section is-dir"
    :class="[
      `is-${section.badgeKind}`,
      {
        'is-collapsed': isChain ? false : isCollapsed,
        'is-chain': isChain && !isChainExpanded,
        'is-chain-expanded': isChain && isChainExpanded,
      },
    ]"
    :style="{ '--depth': depth }"
  >
    <button
      type="button"
      class="git-diff-section-header"
      :aria-expanded="isChain ? isChainExpanded : !isCollapsed"
      :aria-label="
        isChain
          ? tm('spcodeProjectLoad.diffPreview.group.chainAria', {
              path: chainAriaPath,
            })
          : undefined
      "
      @click="onHeaderClick"
    >
      <v-icon
        size="14"
        class="git-diff-section-chevron"
        :class="{ expanded: isChain ? isChainExpanded : !isCollapsed }"
      >mdi-chevron-right</v-icon>

      <!-- per-section "select all" checkbox -->
      <span
        v-if="anyBulkAffordance && sectionHasContent"
        class="git-diff-section-check"
        @click.stop
      >
        <input
          type="checkbox"
          :checked="isSectionFullySelected(section)"
          :indeterminate.prop="isSectionPartiallySelected(section)"
          :aria-label="tm('spcodeProjectLoad.diffPreview.group.subdirAria', { parent: parentLabel, name: section.label })"
          @change="onSectionCheckChange"
        />
      </span>

      <span class="git-diff-section-dot" :class="`is-${section.badgeKind}`" />
      <!-- Chain crumb view: first › … › last, middle segments elided. -->
      <span v-if="isChain && !isChainExpanded" class="git-diff-section-label git-diff-section-crumb">
        <template v-for="(seg, i) in crumbSegments" :key="`${section.id}-crumb-${i}`">
          <span v-if="i" class="git-diff-crumb-sep">›</span>
          <span class="git-diff-crumb-seg" :class="{ 'is-muted': seg === '…' }">{{ seg }}</span>
        </template>
        <span class="git-diff-chain-hint">
          {{ tm("spcodeProjectLoad.diffPreview.group.chainExpand") }}
        </span>
      </span>
      <!-- Ordinary / chain-expanded view: the node's own label. -->
      <span v-else class="git-diff-section-label">{{ section.label }}</span>
      <span class="git-diff-section-meta">
        <span class="git-diff-section-count">{{ recursiveFileCount }}</span>
        <span class="git-diff-section-stats">
          <span class="git-diff-add">+{{ recursiveAdditions }}</span>
          <span class="git-diff-del">−{{ recursiveDeletions }}</span>
        </span>
      </span>
    </button>

    <div
      v-show="isChain ? isChainExpanded : !isCollapsed"
      class="git-diff-section-body"
    >
      <!-- Direct leaf files -->
      <GitDiffFileItem
        v-for="f in section.files"
        :key="f.path + ':' + f.status"
        :file="f"
        :expanded="expanded.has(f.path)"
        :is-dark="isDark"
        :show-stage="showStage"
        :show-unstage="showUnstage"
        :on-stage="(p: string) => emit('stage', p)"
        :on-unstage="(p: string) => emit('unstage', p)"
        :on-restore="(p: string) => emit('restore', p)"
        :on-open-file="(p: string) => emit('open-file', p)"
        :is-staging="isStagingForPath(f.path)"
        :is-unstaging="isUnstagingForPath(f.path)"
        :is-new-file="newFilePaths?.has(f.path) ?? false"
        :selectable="anyBulkAffordance"
        :is-selected="isSelected(f.path)"
        :selectable-aria-label="selectableAriaLabel(f.path)"
        :on-discard-hunk="onFileDiscardHunk"
        :discarding-hunks="discardingHunks"
        :discardable="isDiscardableFor(f)"
        :scope="scope"
        @toggle="emit('toggle', f.path)"
        @restore="emit('restore', f.path)"
        @stage="emit('stage', f.path)"
        @unstage="emit('unstage', f.path)"
        @open-file="emit('open-file', f.path)"
        @select="(selected: boolean) => onToggleSelect(f.path, selected)"
      />

      <!-- Recursive children: same component instance, depth+1.
         Each child event is re-emitted up the tree; the typed
         wrapper is needed because vue-tsc can't always infer
         $event's payload type for recursive child components.
         2026-10-25 chains: a revealed chain's links stay suppressed
         (rendered ordinary) until the run ends at the terminal. -->
      <DiffDirectoryNode
        v-for="child in section.children"
        :key="child.id"
        :section="child"
        :depth="depth + 1"
        :expanded="expanded"
        :collapsed-groups="collapsedGroups"
        :expanded-chains="expandedChains"
        :chain-suppressed="childChainSuppressed"
        :is-dark="isDark"
        :show-stage="showStage"
        :show-unstage="showUnstage"
        :show-restore="showRestore"
        :new-file-paths="newFilePaths"
        :is-selected="isSelected"
        :is-staging-for-path="isStagingForPath"
        :is-unstaging-for-path="isUnstagingForPath"
        :is-discardable-for="isDiscardableFor"
        :is-section-fully-selected="isSectionFullySelected"
        :is-section-partially-selected="isSectionPartiallySelected"
        :selectable-aria-label="selectableAriaLabel"
        :on-toggle-group="onToggleGroup"
        :on-toggle-chain="onToggleChain"
        :on-toggle-select="onToggleSelect"
        :on-toggle-group-select="onToggleGroupSelect"
        :on-file-toggle="onFileToggle"
        :on-file-stage="onFileStage"
        :on-file-unstage="onFileUnstage"
        :on-file-restore="onFileRestore"
        :on-file-open="onFileOpen"
        :on-file-discard-hunk="onFileDiscardHunk"
        :discarding-hunks="discardingHunks"
        :scope="scope"
        @toggle="onChildToggle"
        @restore="onChildRestore"
        @stage="onChildStage"
        @unstage="onChildUnstage"
        @open-file="onChildOpenFile"
      />
    </div>
  </div>
</template>

<style scoped>
.git-diff-section {
  /* depth 缩进: per-level +16px */
  padding-left: calc(var(--depth, 0) * 16px);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.06);
}
.git-diff-section:last-of-type {
  border-bottom: 0;
}
.git-diff-section-header {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 14px;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.75);
  background: rgba(var(--v-theme-on-surface), 0.025);
  border: 0;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.06);
  cursor: pointer;
  text-align: left;
  user-select: none;
  transition: background 0.12s ease;
}
.git-diff-section-header:hover {
  background: rgba(var(--v-theme-on-surface), 0.05);
}
.git-diff-section-header:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: -2px;
}
.git-diff-section-chevron {
  color: rgba(var(--v-theme-on-surface), 0.5);
  flex-shrink: 0;
  transition: transform 0.15s ease;
}
.git-diff-section-chevron.expanded {
  transform: rotate(90deg);
}
.git-diff-section-check {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
}
.git-diff-section-check input {
  cursor: pointer;
  width: 14px;
  height: 14px;
  margin: 0;
  accent-color: rgb(var(--v-theme-primary));
}
.git-diff-section-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.git-diff-section-dot.is-staged {
  background: rgba(76, 175, 80, 0.85);
}
.git-diff-section-dot.is-unstaged {
  background: rgba(var(--v-theme-secondary), 0.85);
}
.git-diff-section-dot.is-neutral {
  background: rgba(var(--v-theme-on-surface), 0.35);
}
.git-diff-section-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 2026-10-25 compact-directory-chains: breadcrumb row for a
   single-child directory run (first › … › last), plus a hover hint
   marking the row as expandable into real levels. */
.git-diff-section-crumb {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-weight: 600;
}

.git-diff-crumb-sep {
  color: rgba(var(--v-theme-on-surface), 0.4);
  flex-shrink: 0;
}

.git-diff-crumb-seg {
  overflow: hidden;
  text-overflow: ellipsis;
}

.git-diff-crumb-seg.is-muted {
  color: rgba(var(--v-theme-on-surface), 0.45);
}

.git-diff-chain-hint {
  flex-shrink: 0;
  margin-left: 6px;
  padding: 0 5px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  border-radius: 4px;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 10px;
  font-weight: 500;
  line-height: 14px;
  opacity: 0.75;
  transition: color 0.12s ease, border-color 0.12s ease;
}

.git-diff-section-header:hover .git-diff-chain-hint {
  color: rgb(var(--v-theme-primary));
  border-color: rgba(var(--v-theme-primary), 0.5);
  opacity: 1;
}
.git-diff-section-meta {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 500;
  font-size: 11.5px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  flex-shrink: 0;
}
.git-diff-section-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  padding: 1px 6px;
  border-radius: 8px;
  background: rgba(var(--v-theme-on-surface), 0.07);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 10.5px;
}
.git-diff-section-stats {
  display: inline-flex;
  gap: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
.git-diff-section-stats .git-diff-add {
  color: rgb(46, 160, 67);
}
.git-diff-section-stats .git-diff-del {
  color: rgb(248, 81, 73);
}
.git-diff-section.is-dir.is-staged {
  border-left: 2px solid rgba(76, 175, 80, 0.45);
}
</style>
