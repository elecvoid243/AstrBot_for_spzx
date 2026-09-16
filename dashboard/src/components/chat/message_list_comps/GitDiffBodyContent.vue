<!-- Spec: docs/superpowers/specs/2026-06-17-chatui-git-diff-sidebar-design.md §4.2.3
     Updated 2026-06-22 — thread 'restore' event for file-restore button
     Updated 2026-06-28 — UI improvements batch:
       #1 directory grouping (collapsible per top-level dir)
       #2 summary bar (totals + dir count at the top)
       #3 expand-all / collapse-all + multi-select + stage/unstage selected
       #4 'all' view partition (staged vs unstaged as two segments)

     Grouping is purely visual: the flat `files` list is still the
     single source of truth, and `expanded` (per-file) is owned by
     the parent. The body content introduces 3 NEW local reactive
     collections (selectedFiles, collapsedGroups, groups) that never
     cross the parent boundary — they reset on viewMode / scope /
     worktree change thanks to the key prop on the <GitDiffBodyContent>
     element in GitDiffSidebar.vue. -->
<script setup lang="ts">
import { computed, ref, watch, type Ref } from "vue";
import type {
  GitDiffFetchState,
  GitDiffScope,
} from "@/composables/useSpcodeGitDiff";
import type { SpcodeGitDiffFile } from "@/composables/parseSpcodeGitDiff";
import {
  buildDirectorySections,
  walkSections,
  type DiffSection,
} from "@/composables/parseDirectorySections";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import { useSpcodeSession } from "@/composables/useSpcodeSession";
import { useModuleI18n } from "@/i18n/composables";
import GitDiffFileItem from "@/components/chat/message_list_comps/GitDiffFileItem.vue";
import DiffDirectoryNode from "@/components/chat/message_list_comps/DiffDirectoryNode.vue";

const { tm } = useModuleI18n("features/chat");

// ── Group / section data model ────────────────────────────────────
// 2026-07-28 recursive tree: DiffSection moved to
// parseDirectorySections.ts (added `fullPath` and `children` fields).
// The interface is imported above; this comment block remains to
// document the change. See §3.3.1 of the design spec for the
// recursive shape.

// ── Props & emits ─────────────────────────────────────────────────

const props = defineProps<{
  state: GitDiffFetchState;
  expanded: Set<string>;
  isDark: boolean;
  onRestore?: (path: string) => void;
  // Spec §6.2.3 + §6.2.4: parent (GitDiffSidebar) supplies the
  // scope + reactive Set<string> from useSpcodeGitStage / Unstage.
  // We pre-compute showStage / showUnstage booleans so the file item
  // stays scope-agnostic.
  selectedScope?: GitDiffScope;
  onStage?: (path: string) => void;
  onUnstage?: (path: string) => void;
  isStaging?: Ref<Set<string>>;
  isUnstaging?: Ref<Set<string>>;
  // Paths that should render as "new file" rows (teal accent + "新增
  // 文件" badge) instead of regular diff rows. Populated by GitDiffSidebar
  // from /spcode/git-status (scope=untracked | intent_to_add) when the
  // unstaged view is active. Pass-through prop so the body content stays
  // scope-agnostic (the merge happens at the sidebar level).
  newFilePaths?: ReadonlySet<string>;
  /** Passed through to GitDiffFileItem for the "view file" button. */
  onOpenFile?: (path: string) => void;
  // ── Spec 2026-07-07 hunk discard: pass-through props ──
  // Matches GitDiffFileItem's prop signature exactly so the prop can be
  // threaded through verbatim (callback-prop passthrough, not emit —
  // see task-7-report and Spec 2026-07-07-… §6.1.2).
  // v2 (2026-07-21): callback now carries the active `scope` so the
  // backend can pick `git apply --reverse[ --cached]` without
  // re-deriving from porcelain (file-discard-hunk-api v2.21).
  onDiscardHunk?: (params: {
    file: string;
    hunkIndex: number;
    patchText: string;
    scope: GitDiffScope;
  }) => void;
  /** Set of `${file.path}#${hunkIndex}` keys currently in flight. */
  discardingHunks?: ReadonlySet<string>;
}>();
const emit = defineEmits<{
  (e: "toggle", path: string): void;
  (e: "retry"): void;
  (e: "restore", path: string): void;
  (e: "stage", path: string): void;
  (e: "unstage", path: string): void;
  (e: "open-file", path: string): void;
  // UI #3: bulk stage / unstage of multi-selected files. Parent
  // routes to gitStage.stage({ files }) / gitUnstage.unstage({ files })
  // which already accept an array. Emitted with the full path list
  // (NOT deltas) so the parent can refresh stagedFiles with the
  // authoritative server response.
  (e: "stage-paths", paths: string[]): void;
  (e: "unstage-paths", paths: string[]): void;
  // Bulk restore: parent iterates the array and calls the file-restore
  // endpoint once per path (the backend only accepts a single file per
  // request), then aggregates the result into one snackbar + refresh.
  (e: "restore-paths", paths: string[]): void;
}>();

const spcodeStatus = useSpcodeProjectStatus();
const session = useSpcodeSession();
// Spec §6.2.3: scope 派生按钮显隐(项目必须已加载 + scope=unstaged 显示 ↑)
const showStageButton = computed(() => {
  if (!props.onStage) return false;
  if (!spcodeStatus.status.value.loaded) return false;
  if (!session.umo.value) return false;
  return props.selectedScope === "unstaged";
});
const showUnstageButton = computed(() => {
  if (!props.onUnstage) return false;
  if (!spcodeStatus.status.value.loaded) return false;
  if (!session.umo.value) return false;
  return props.selectedScope === "staged";
});
// Bulk restore (toolbar + per-file / per-section checkboxes) is
// available in every scope — staged, unstaged, AND `all` (with HEAD).
// The underlying `git checkout -- <file>` discards the working-tree
// change regardless of staging state, so a user looking at the
// unified `all` view can still bulk-revert a subset of files. The
// single-file "restore" button on GitDiffFileItem uses the same gate
// (it just isn't scope-aware), so the bulk UI mirrors it 1:1.
const showRestoreButton = computed(() => {
  if (!props.onRestore) return false;
  if (!spcodeStatus.status.value.loaded) return false;
  if (!session.umo.value) return false;
  return true;
});
// UI #3: bulk stage / unstage of selected files is only meaningful
// in the `unstaged` and `staged` scopes (every visible file is by
// definition in the matching state). In the `all` scope we don't
// know which is which without server-side per-file classification,
// so the bulk buttons stay hidden there. Per-file stage/unstage
// remains available on each row via GitDiffFileItem regardless.

function isStagingForPath(path: string): boolean {
  return props.isStaging?.value?.has(path) ?? false;
}
function isUnstagingForPath(path: string): boolean {
  return props.isUnstaging?.value?.has(path) ?? false;
}

// ── Spec 2026-07-07 hunk discard ──
// Per-hunk discard is wired only when the parent supplied a callback
// AND the file is a discard candidate: not a brand-new file (untracked
// / intent-to-add have no prior content to revert to), not binary
// (no textual hunk to discard), and the spcode project must be
// loaded with a UMO (no project → no git operations).
function discardableFor(file: SpcodeGitDiffFile): boolean {
  if (!props.onDiscardHunk) return false;
  if (props.newFilePaths?.has(file.path)) return false;
  if (file.isBinary) return false;
  if (!spcodeStatus.status.value.loaded) return false;
  if (!session.umo.value) return false;
  return true;
}

const REASON_I18N_KEYS: Record<string, string> = {
  feature_disabled:
    "spcodeProjectLoad.diffSidebar.error.reason.feature_disabled",
  no_project_loaded:
    "spcodeProjectLoad.diffSidebar.error.reason.no_project_loaded",
  directory_missing:
    "spcodeProjectLoad.diffSidebar.error.reason.directory_missing",
  not_a_git_repo: "spcodeProjectLoad.diffSidebar.error.reason.not_a_git_repo",
  git_unavailable: "spcodeProjectLoad.diffSidebar.error.reason.git_unavailable",
  git_error: "spcodeProjectLoad.diffSidebar.error.reason.git_error",
};

function localizedReason(reason: string): string {
  const key = REASON_I18N_KEYS[reason];
  if (key) return tm(key);
  if (reason === "network")
    return tm("spcodeProjectLoad.diffSidebar.error.networkTitle");
  return tm("spcodeProjectLoad.diffSidebar.error.reason.generic", { reason });
}

const errorInfo = computed(() => {
  if (props.state.kind !== "error") return null;
  return {
    reason: props.state.reason,
    hasPrevious: !!props.state.previousSnapshot,
  };
});

const files = computed(() => {
  if (props.state.kind === "ok") return props.state.snapshot.files;
  if (props.state.kind === "error" && props.state.previousSnapshot) {
    return props.state.previousSnapshot.files;
  }
  return [];
});

// ── UI #2: Summary stats ──────────────────────────────────────────
// 2026-07-28 recursive-tree: now walks the recursive section tree
// (one section per directory level) instead of bucketing by
// top-level dir only. Each section contributes its direct files'
// additions/deletions; recursion sums the whole subtree. The
// per-section file count is "direct files only" by design, so the
// top-bar file count = sum of all section.fileCount across the tree
// (which equals files.length, but the walk is the source of truth
// for both top-bar stats and per-section badges — they can never
// drift).

const summary = computed(() => {
  const list = files.value;
  let fileCount = 0;
  let additions = 0;
  let deletions = 0;
  walkSections(sections.value, (s) => {
    fileCount += s.fileCount;
    additions += s.additions;
    deletions += s.deletions;
  });
  return {
    fileCount,
    additions,
    deletions,
    dirCount: sections.value.length,
  };
});

// ── UI #1: Directory grouping (and #4: 'all' scope partition) ────

/** The flat `files` list is reshaped into a recursive tree of
 *  `DiffSection` nodes (one per directory level, including a
 *  synthetic `<root>` for repo-root files). 2026-07-28 recursive
 *  tree: replaced the previous top-level-only bucket by a call to
 *  the pure helper in `parseDirectorySections.ts`, which builds
 *  one section per `/`-segment in each file's path. The `id`
 *  field is path-derived and stable across re-renders so the
 *  `collapsedGroups` Set and selection state survive a refresh
 *  tick. */
const sections = computed<DiffSection[]>(() => {
  const list = files.value;
  if (list.length === 0) return [];
  // scope can be undefined when the parent hasn't wired it yet;
  // default to "unstaged" so the badge color matches the default state.
  return buildDirectorySections(list, "", props.selectedScope ?? "unstaged");
});

// ── UI #1: per-section collapse state ─────────────────────────────

const collapsedGroups = ref<Set<string>>(new Set());

function toggleGroup(id: string): void {
  const next = new Set(collapsedGroups.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  collapsedGroups.value = next;
}

function expandAll(): void {
  collapsedGroups.value = new Set();
  // Also expand every file row by emitting toggle for collapsed files.
  for (const f of files.value) {
    if (!props.expanded.has(f.path)) emit("toggle", f.path);
  }
}

function collapseAll(): void {
  // 2026-07-28 recursive-tree: collect every section id in the tree
  // (not just top-level) so collapse-all reaches nested sections too.
  const ids = new Set<string>();
  walkSections(sections.value, (s) => ids.add(s.id));
  collapsedGroups.value = ids;
  // Also collapse every file row by emitting toggle for expanded files.
  for (const f of files.value) {
    if (props.expanded.has(f.path)) emit("toggle", f.path);
  }
}

// ── UI #3: multi-select state ─────────────────────────────────────

const selectedFiles = ref<Set<string>>(new Set());

function toggleSelect(path: string, next: boolean): void {
  const updated = new Set(selectedFiles.value);
  if (next) updated.add(path);
  else updated.delete(path);
  selectedFiles.value = updated;
}

function clearSelection(): void {
  if (selectedFiles.value.size === 0) return;
  selectedFiles.value = new Set();
}

// Expose `clearSelection` to the parent so successful bulk
// stage/unstage can reset the toolbar's "selected N files" counter.
// Without this, the parent (GitDiffSidebar) handles bulk stage by
// emitting `stage-paths`/`unstage-paths`, which mutates the parent's
// `stagedFiles` set and triggers refresh — but the child's local
// `selectedFiles` is invisible to the parent, so the toolbar kept
// showing the original count even after the files were gone from the
// list. expose'ing the method lets the parent drive the reset on
// result.ok; failures intentionally keep the user's selection so
// they can retry without re-checking every box.
defineExpose({ clearSelection });

// Reset selection whenever the scope changes (so checking files in
// the unstaged view doesn't carry over when the user switches to
// the staged view, where the same paths are no longer visible).
// We DON'T reset on worktree / project changes here because the
// parent re-mounts the component (via key=), which clears the ref.
watch(
  () => props.selectedScope,
  () => {
    selectedFiles.value = new Set();
  },
);

// 2026-07-26 selection-prune: when the visible diff list shrinks
// (e.g. a per-file restore or stage on one row drops that path
// from `files.value` before the user fires a bulk action), drop
// stale selections whose paths no longer exist on screen. Without
// this the toolbar's "已选 N" counter keeps the pre-shrink count
// while the underlying list is shorter, and bulk stage / unstage /
// restore then tries to operate on paths that no longer exist in
// the diff — the backend 404s per missing path and the user gets a
// confusing partial-success / error response.
//
// We prune (not clear) so the surviving selections the user still
// intends to act on stay checked. We watch the *path list* (a plain
// string array derived from files.value) rather than the whole
// `files` computed so the watcher doesn't refire on every refresh
// tick that re-emits an equivalent list with identical paths.
watch(
  () => files.value.map((f) => f.path),
  (currentPaths) => {
    const visible = new Set(currentPaths);
    const sel = selectedFiles.value;
    let mutated = false;
    for (const p of sel) {
      if (!visible.has(p)) {
        sel.delete(p);
        mutated = true;
      }
    }
    // Reassign so Vue's reactivity picks up the in-place Set
    // mutation (Set's add/delete on a ref-wrapped Set doesn't
    // always trigger watchers — reassigning a new Set is the
    // safest, cheapest signal).
    if (mutated) selectedFiles.value = new Set(sel);
  },
);

// Snapshots selected paths that still exist in the current visible
// diff. Used by every bulk action as a defensive filter: even if
// the prune watcher above hasn't flushed yet (e.g. user clicks
// "暂存" in the same task as a per-file restore), the bulk action
// never forwards paths that aren't in `files.value` to the parent.
function effectiveSelectedPaths(): string[] {
  const visible = new Set(files.value.map((f) => f.path));
  return Array.from(selectedFiles.value).filter((p) => visible.has(p));
}

// Bulk stage / unstage of selected files: in the `unstaged` /
// `staged` scopes every visible file is in the matching state,
// so we pass the full selection through to the parent's bulk
// endpoint. The `all` scope hides these buttons entirely.
function onClickStageSelected(): void {
  const paths = effectiveSelectedPaths();
  if (paths.length === 0) return;
  emit("stage-paths", paths);
}
function onClickUnstageSelected(): void {
  const paths = effectiveSelectedPaths();
  if (paths.length === 0) return;
  emit("unstage-paths", paths);
}

// "Select all" toggle: when every visible file is already in the
// selection, clicking again clears the selection (matches the
// user-described "再次点击则取消全选" semantics). The selection
// stays scoped to the currently-visible file list (`files.value`),
// so switching scope or worktree naturally resets it via the
// `watch(selectedScope)` and component re-mount respectively.
const allSelected = computed<boolean>(() => {
  const list = files.value;
  if (list.length === 0) return false;
  for (const f of list) {
    if (!selectedFiles.value.has(f.path)) return false;
  }
  return true;
});

function toggleSelectAll(): void {
  if (allSelected.value) {
    clearSelection();
    return;
  }
  const updated = new Set<string>();
  for (const f of files.value) updated.add(f.path);
  selectedFiles.value = updated;
}

// Bulk restore: emit the full selection so the parent can show a
// confirmation dialog and then iterate. We do NOT clear the
// selection here — the parent drives that on success so failures
// leave the user's checkboxes intact for retry. The selection is
// filtered through effectiveSelectedPaths() so a stale path that
// was just removed by some other action (e.g. a per-file restore
// right before this click) is silently dropped instead of
// reaching the parent and tripping a "file not in diff" error.
function onClickRestoreSelected(): void {
  const paths = effectiveSelectedPaths();
  if (paths.length === 0) return;
  emit("restore-paths", paths);
}

function toggleGroupSelect(section: DiffSection, next: boolean): void {
  // 2026-07-28 recursive-tree: walk the whole subtree (direct
  // files + every nested section) so toggling a parent affects
  // every descendant path in lock-step. Without the walk, only
  // the parent's direct files flip and the nested sections retain
  // stale selection state — visually confusing when the user
  // expects the check to propagate down.
  const updated = new Set(selectedFiles.value);
  walkSections([section], (s) => {
    for (const f of s.files) {
      if (next) updated.add(f.path);
      else updated.delete(f.path);
    }
  });
  selectedFiles.value = updated;
}

function isSectionFullySelected(section: DiffSection): boolean {
  // 2026-07-28 recursive-tree: a section is "fully selected" only
  // when EVERY direct file AND EVERY nested section is itself
  // fully selected. An empty section (no files, no children) is
  // treated as "not selected" so the header checkbox is never
  // shown in a "checked" state for an empty branch.
  for (const f of section.files) {
    if (!selectedFiles.value.has(f.path)) return false;
  }
  for (const c of section.children) {
    if (!isSectionFullySelected(c)) return false;
  }
  return section.files.length + section.children.length > 0;
}

function isSectionPartiallySelected(section: DiffSection): boolean {
  // 2026-07-28 recursive-tree: aggregate direct-file and nested-
  // section selection state into a single tri-state. Indeterminate
  // when at least one descendant is selected but not all.
  let any = false;
  let all = true;
  for (const c of section.children) {
    if (isSectionFullySelected(c)) {
      any = true;
    } else if (isSectionPartiallySelected(c)) {
      any = true;
      all = false;
    } else {
      all = false;
    }
  }
  for (const f of section.files) {
    if (selectedFiles.value.has(f.path)) any = true;
    else all = false;
  }
  return any && !all;
}
</script>

<template>
  <!-- Branch 1: loading -->
  <div v-if="state.kind === 'loading'" class="git-diff-center">
    <v-progress-circular indeterminate :size="32" />
    <span class="git-diff-center-text">{{
      tm("spcodeProjectLoad.diffSidebar.loading")
    }}</span>
  </div>

  <!-- Branch 2: error with no previous -->
  <div
    v-else-if="state.kind === 'error' && !state.previousSnapshot && errorInfo"
    class="git-diff-center"
  >
    <v-icon size="36" color="error">mdi-alert-circle-outline</v-icon>
    <div class="git-diff-error-title">
      {{ tm("spcodeProjectLoad.diffSidebar.error.loadFailedTitle") }}
    </div>
    <div class="git-diff-error-detail">
      {{ localizedReason(errorInfo.reason) }}
    </div>
    <v-btn size="small" color="primary" @click="emit('retry')">
      {{ tm("spcodeProjectLoad.diffSidebar.error.retry") }}
    </v-btn>
  </div>

  <!-- Branch 3 & 4: success (or success with stale error) -->
  <template
    v-else-if="
      state.kind === 'ok' || (state.kind === 'error' && state.previousSnapshot)
    "
  >
    <div v-if="files.length === 0" class="git-diff-center">
      <v-icon size="36" color="grey">mdi-check-circle-outline</v-icon>
      <span class="git-diff-center-text">{{
        tm("spcodeProjectLoad.diffSidebar.empty")
      }}</span>
    </div>

    <!-- 2026-07-26 sticky-header: summary bar + bulk-action toolbar
         are wrapped in a single sticky container so they stay pinned
         to the top of .git-diff-sidebar-body while long diffs scroll
         underneath. The wrapper is opaque (matches the sidebar's
         surface) so sections never bleed through. The summary's own
         toolbar's per-element backgrounds keep their subtle tint on
         top of the opaque base. z-index:3 to sit above the
         below-rendered section rows. -->
    <div v-if="files.length > 0" class="git-diff-sticky-header">
      <!-- UI #2: summary bar. Always rendered when there are files,
         so the user sees the overall magnitude of the diff without
         scrolling. Two-line layout on narrow viewports collapses
         via the .git-diff-summary-line flex-wrap. -->
      <div class="git-diff-summary">
        <div class="git-diff-summary-line">
          <span class="git-diff-summary-count">
            {{
              tm("spcodeProjectLoad.diffPreview.summary.fileCount", {
                count: summary.fileCount,
                dirs: summary.dirCount,
              })
            }}
          </span>
          <span class="git-diff-summary-stats">
            <span class="git-diff-summary-add">+{{ summary.additions }}</span>
            <span class="git-diff-summary-del">−{{ summary.deletions }}</span>
          </span>
        </div>
      </div>

      <!-- UI #3: toolbar. Expand / collapse all + (when something is
         selected) the count + bulk stage / unstage / restore buttons.
         The "select all" toggle is shown alongside expand/collapse
         (matching the per-section "select all" pattern below) so it
         is always reachable, regardless of whether the user has
         already started selecting. Renders even when no items are
         selected so the user can find the "expand all" affordance.
         Note: stage / unstage are gated to their respective scopes
         (no per-file classification is available in `all`), but the
         select-all + restore affordances are reachable in every
         scope — see `showRestoreButton` above. -->
      <div class="git-diff-toolbar">
        <div class="git-diff-toolbar-group">
          <button
            type="button"
            class="git-diff-toolbar-btn"
            :title="tm('spcodeProjectLoad.diffPreview.toolbar.expandAll')"
            :aria-label="tm('spcodeProjectLoad.diffPreview.toolbar.expandAll')"
            @click="expandAll"
          >
            <v-icon size="14">mdi-unfold-more-horizontal</v-icon>
            <span>{{
              tm("spcodeProjectLoad.diffPreview.toolbar.expandAll")
            }}</span>
          </button>
          <button
            type="button"
            class="git-diff-toolbar-btn"
            :title="tm('spcodeProjectLoad.diffPreview.toolbar.collapseAll')"
            :aria-label="tm('spcodeProjectLoad.diffPreview.toolbar.collapseAll')"
            @click="collapseAll"
          >
            <v-icon size="14">mdi-unfold-less-horizontal</v-icon>
            <span>{{
              tm("spcodeProjectLoad.diffPreview.toolbar.collapseAll")
            }}</span>
          </button>
          <!-- "Select all" toggle. Reachable whenever ANY bulk
               affordance is available — stage, unstage, OR restore.
               This means it shows in the `all` scope too, where
               per-file checkboxes are also visible specifically for
               the bulk-restore flow. Label/icon flip between
               "all selected" and "not all selected" for clarity. -->
          <button
            v-if="
              showStageButton || showUnstageButton || showRestoreButton
            "
            type="button"
            class="git-diff-toolbar-btn"
            :class="{ 'is-active': allSelected }"
            :title="
              allSelected
                ? tm('spcodeProjectLoad.diffPreview.toolbar.deselectAll')
                : tm('spcodeProjectLoad.diffPreview.toolbar.selectAll')
            "
            :aria-label="
              allSelected
                ? tm('spcodeProjectLoad.diffPreview.toolbar.deselectAll')
                : tm('spcodeProjectLoad.diffPreview.toolbar.selectAll')
            "
            :aria-pressed="allSelected"
            @click="toggleSelectAll"
          >
            <v-icon size="14">{{
              allSelected
                ? "mdi-checkbox-marked"
                : "mdi-checkbox-multiple-blank-outline"
            }}</v-icon>
            <span>{{
              allSelected
                ? tm("spcodeProjectLoad.diffPreview.toolbar.deselectAll")
                : tm("spcodeProjectLoad.diffPreview.toolbar.selectAll")
            }}</span>
          </button>
        </div>
        <div v-if="selectedFiles.size > 0" class="git-diff-toolbar-group">
          <span class="git-diff-toolbar-selected">
            {{
              tm("spcodeProjectLoad.diffPreview.toolbar.selectedCount", {
                count: selectedFiles.size,
              })
            }}
          </span>
          <button
            v-if="showStageButton"
            type="button"
            class="git-diff-toolbar-btn is-stage"
            :disabled="selectedFiles.size === 0"
            @click="onClickStageSelected"
          >
            <v-icon size="14">mdi-arrow-up-bold-circle-outline</v-icon>
            <span>{{
              tm("spcodeProjectLoad.diffPreview.toolbar.stageSelected", {
                count: selectedFiles.size,
              })
            }}</span>
          </button>
          <button
            v-if="showUnstageButton"
            type="button"
            class="git-diff-toolbar-btn is-unstage"
            :disabled="selectedFiles.size === 0"
            @click="onClickUnstageSelected"
          >
            <v-icon size="14">mdi-arrow-down-bold-circle-outline</v-icon>
            <span>{{
              tm("spcodeProjectLoad.diffPreview.toolbar.unstageSelected", {
                count: selectedFiles.size,
              })
            }}</span>
          </button>
          <!-- "Restore changes" sits next to stage/unstage. Reachable
               in every scope (staged, unstaged, all) because the
               underlying git checkout works on working-tree state
               regardless of staging. Only enabled when at least one
               file is selected. The parent shows a confirmation
               dialog before discarding anything, since restore is
               irreversible. -->
          <button
            v-if="showRestoreButton"
            type="button"
            class="git-diff-toolbar-btn is-restore"
            :disabled="selectedFiles.size === 0"
            :title="tm('spcodeProjectLoad.diffPreview.toolbar.restoreSelected', { count: selectedFiles.size })"
            :aria-label="tm('spcodeProjectLoad.diffPreview.toolbar.restoreSelected', { count: selectedFiles.size })"
            @click="onClickRestoreSelected"
          >
            <v-icon size="14">mdi-restore</v-icon>
            <span>{{
              tm("spcodeProjectLoad.diffPreview.toolbar.restoreSelected", {
                count: selectedFiles.size,
              })
            }}</span>
          </button>
          <button
            type="button"
            class="git-diff-toolbar-btn"
            :title="tm('spcodeProjectLoad.diffPreview.toolbar.clearSelection')"
            :aria-label="tm('spcodeProjectLoad.diffPreview.toolbar.clearSelection')"
            @click="clearSelection"
          >
            <v-icon size="14">mdi-close</v-icon>
          </button>
        </div>
      </div>
      <!-- 2026-07-26 sticky-header: end of .git-diff-sticky-header
           wrapper that keeps the summary + toolbar pinned. -->
      </div>

      <!-- UI #1: recursive directory tree (replaces the previous
           top-level-only section block). Each <DiffDirectoryNode>
           renders one DiffSection — its direct files, then its
           children (nested sub-sections) — and routes events back
           up through the same emit surface the old flat block used.
           2026-07-28 recursive tree: file rows are identical to
           before; only the section grouping and the wrapper
           changed. -->
    <DiffDirectoryNode
      v-for="section in sections"
      :key="section.id"
      :section="section"
      :depth="0"
      :expanded="expanded"
      :collapsed-groups="collapsedGroups"
      :is-dark="isDark"
      :show-stage="showStageButton"
      :show-unstage="showUnstageButton"
      :show-restore="showRestoreButton"
      :new-file-paths="newFilePaths"
      :is-selected="(p: string) => selectedFiles.has(p)"
      :is-staging-for-path="isStagingForPath"
      :is-unstaging-for-path="isUnstagingForPath"
      :is-discardable-for="discardableFor"
      :is-section-fully-selected="isSectionFullySelected"
      :is-section-partially-selected="isSectionPartiallySelected"
      :selectable-aria-label="
        (p: string) => tm('spcodeProjectLoad.diffPreview.toolbar.selectFile', { path: p })
      "
      :on-toggle-group="toggleGroup"
      :on-toggle-select="toggleSelect"
      :on-toggle-group-select="toggleGroupSelect"
      :on-file-toggle="(p: string) => emit('toggle', p)"
      :on-file-stage="(p: string) => emit('stage', p)"
      :on-file-unstage="(p: string) => emit('unstage', p)"
      :on-file-restore="(p: string) => emit('restore', p)"
      :on-file-open="(p: string) => emit('open-file', p)"
      :on-file-discard-hunk="onDiscardHunk"
      :discarding-hunks="discardingHunks"
      :scope="selectedScope"
      @toggle="emit('toggle', $event)"
      @restore="emit('restore', $event)"
      @stage="emit('stage', $event)"
      @unstage="emit('unstage', $event)"
      @open-file="emit('open-file', $event)"
    />

    <div v-if="state.kind === 'error' && errorInfo" class="git-diff-banner-error">
      <span>{{ localizedReason(errorInfo.reason) }}</span>
      <button class="git-diff-banner-retry" @click="emit('retry')">
        {{ tm("spcodeProjectLoad.diffSidebar.error.retry") }}
      </button>
    </div>
  </template>

  <!-- Branch 5: idle (initial state, no fetch yet) -->
  <div v-else class="git-diff-center">
    <span class="git-diff-center-text">{{
      tm("spcodeProjectLoad.diffSidebar.loading")
    }}</span>
  </div>
</template>

<style scoped>
/* ── Sticky header (2026-07-26) ─────────────────────────────────
   Wraps the summary bar + bulk-action toolbar so the user keeps
   them visible at the top of .git-diff-sidebar-body while a long
   diff scrolls underneath. The wrapper sits inside the sidebar's
   vertical scroll container (the nearest scrolling ancestor), so
   `top: 0` pins it to the top of the body's content area without
   any extra JS. Per-row borders on .git-diff-summary +
   .git-diff-toolbar are kept so the two halves still show their
   divider lines. */

.git-diff-sticky-header {
  position: sticky;
  top: 0;
  /* Above the .git-diff-section rows (which have no explicit
     z-index) and above any future overlays the parent might
     stack inside .git-diff-sidebar-body. Without this the
     pinned region disappears behind the first section's
     header when sections scroll. */
  z-index: 3;
  /* Reuse the same sidebar surface var the parent uses so the
     pinned region blends seamlessly with the rest of the panel.
     Falls back to the Vuetify surface color when the chat
     token isn't defined (e.g. plain Vuetify contexts). The
     per-row borders on .git-diff-summary + .git-diff-toolbar
     stay where they are — this opaque base simply fills the
     gap the toolbar's translucent background leaves. */
  background: var(--chat-sidebar-bg, rgb(var(--v-theme-surface)));
}

.git-diff-summary {
  padding: 8px 14px 6px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.06);
}

.git-diff-summary-line {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  flex-wrap: wrap;
}

.git-diff-summary-count {
  color: rgba(var(--v-theme-on-surface), 0.65);
  font-weight: 500;
}

.git-diff-summary-stats {
  display: inline-flex;
  gap: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11.5px;
}

.git-diff-summary-add {
  color: rgb(46, 160, 67);
}
.git-diff-summary-del {
  color: rgb(248, 81, 73);
}

/* ── Toolbar (UI #3) ─────────────────────────────────────────── */

.git-diff-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 8px;
  padding: 6px 14px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.06);
  background: rgba(var(--v-theme-on-surface), 0.02);
}

.git-diff-toolbar-group {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.git-diff-toolbar-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  font-family: inherit;
  font-size: 11.5px;
  color: rgba(var(--v-theme-on-surface), 0.7);
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  cursor: pointer;
  transition:
    background 0.12s ease,
    color 0.12s ease,
    border-color 0.12s ease;
}

.git-diff-toolbar-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgb(var(--v-theme-on-surface));
}

.git-diff-toolbar-btn:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 1px;
}

.git-diff-toolbar-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.git-diff-toolbar-btn.is-stage {
  color: rgb(var(--v-theme-secondary));
}
.git-diff-toolbar-btn.is-stage:hover:not(:disabled) {
  background: rgba(var(--v-theme-secondary), 0.1);
}
.git-diff-toolbar-btn.is-unstage {
  color: rgb(255, 152, 0);
}
.git-diff-toolbar-btn.is-unstage:hover:not(:disabled) {
  background: rgba(255, 152, 0, 0.1);
}
/* "Restore changes" tints warning-red to match the destructive
   intent of git checkout (the single-file restore dialog uses
   the same color). Distinct from the orange unstage accent so
   the two are never confused at a glance. */
.git-diff-toolbar-btn.is-restore {
  color: rgb(248, 81, 73);
}
.git-diff-toolbar-btn.is-restore:hover:not(:disabled) {
  background: rgba(248, 81, 73, 0.1);
}
/* "Select all" pressed state mirrors the per-section checkbox's
   primary accent so the toolbar reflects the same selection
   signal the user sees in each section header. */
.git-diff-toolbar-btn.is-active {
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
}
.git-diff-toolbar-btn.is-active:hover:not(:disabled) {
  background: rgba(var(--v-theme-primary), 0.18);
}

.git-diff-toolbar-selected {
  font-size: 11.5px;
  color: rgb(var(--v-theme-primary));
  font-weight: 500;
  padding: 0 4px;
  border-left: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  margin-left: 4px;
}

/* ── Section / directory group (UI #1, UI #4) ─────────────────── */
/* 2026-07-28 recursive tree: section-level styles (header, dot,
   chevron, checkbox, meta, count, stats, body, is-dir/is-staged
   tint) moved to DiffDirectoryNode.vue as a self-contained
   component. GitDiffBodyContent only owns the sticky-header,
   toolbar, and the error/center state styles. */

/* ── Centered / loading / error states (unchanged) ──────────── */

.git-diff-center {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 32px 16px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  text-align: center;
}

.git-diff-center-text {
  font-size: 13px;
}

.git-diff-error-title {
  font-size: 14px;
  font-weight: 600;
  color: rgb(var(--v-theme-error));
}
.git-diff-error-detail {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.git-diff-banner-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 14px;
  font-size: 12px;
  background: rgba(var(--v-theme-error), 0.08);
  color: rgb(var(--v-theme-error));
  border-top: 1px solid rgba(var(--v-theme-error), 0.2);
}

.git-diff-banner-retry {
  background: none;
  border: 0;
  color: inherit;
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}
</style>
