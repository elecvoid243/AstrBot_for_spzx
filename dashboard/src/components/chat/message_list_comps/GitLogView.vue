<!-- Author: elecvoid243
     Date: 2026-06-24
     Spec: docs/superpowers/specs/2026-06-24-chatui-git-workflow-controls-design.md §3.2, §6.5
     Pure presentation: takes a LogFetchState and exposes
     'apply' / 'loadMore' / 'refresh' events back to the parent
     (which owns the useSpcodeGitLog composable instance).

     Updated 2026-06-25 — when a commit row is expanded, also fetch
     the file list via /spcode/git-show (useSpcodeGitShow). The list
     renders inline below the commit body. Each commit's fetch is
     idempotent and per-SHA cached by the composable, so re-expanding
     a previously seen commit is a no-op (ETag 304 short-circuit).
     Spec: docs/superpowers/specs/2026-06-25-git-show-design.md. -->
<script setup lang="ts">
import { computed, inject, nextTick, ref, watch, type ComputedRef } from "vue";
import { storeToRefs } from "pinia";
import { useCustomizerStore } from "@/stores/customizer";
import { useModuleI18n } from "@/i18n/composables";
import type { LogFetchState, LogFilter } from "@/composables/useSpcodeGitLog";
import type {
  UseSpcodeGitShow,
  GitShowFetchState,
  GitShowFileFetchState,
} from "@/composables/useSpcodeGitShow";
import type { UseSpcodeGitStats } from "@/composables/useSpcodeGitStats";
import FilePatchPanel from "./FilePatchPanel.vue";
import GitStatsPanel from "./GitStatsPanel.vue";
import type {
  GitShowFile,
  GitShowFileStatus,
  GitShowData,
} from "@/composables/parseSpcodeGitShow";
import type { FileStatus } from "@/composables/parseSpcodeGitDiff";
import {
  rangeForPreset,
  type GitStatsRange,
} from "@/composables/parseSpcodeGitStats";
import { useOpenOnDisk } from "@/composables/useOpenOnDisk";
import { absoluteFromSelectedDoc } from "@/composables/pathUtils";

const { tm } = useModuleI18n("features/chat");
// Note (v3.9, 2026-06-25, elecvoid243): FilePatchPanel reads the
// customizer store itself for `isDark`, so we do not derive / forward
// it here. Keeping the source of truth in one place avoids the
// "computed reads the wrong field" trap that bit the earlier
// useTheme().global.name.value attempt.

/** 2026-09-08: Ref 选择器条目。subheader 为分组标题，不可选中。 */
export interface RefPickerItem {
  title: string;
  value?: string;
  type?: "item" | "subheader";
}

const props = defineProps<{
  state: LogFetchState;
  hasMore: boolean;
  isLoading: boolean;
  /** Composable handle injected by the sidebar (spec 2026-06-25 §3.1).
   *  GitLogView reads per-SHA state via the helper methods and auto-
   *  fetches on expand; the sidebar owns the lifecycle (worktree +
   *  umo + dispose). */
  gitShow: UseSpcodeGitShow;
  /**
   * 2026-07-15 history-sha-jump: SHA to highlight after a deep
   * link from a per-file history panel (workspace / document
   * manager). When set, the matching commit row gets the
   * `is-focused` class and is scrolled into view. `null` means
   * no deep-link is active. The parent owns persistence; we just
   * highlight.
   */
  focusedCommitSha: string | null;
  /** Composable handle injected by the sidebar (git-stats heatmap
   *  feature, 2026-07-18). The panel embedded above the log renders
   *  from gitStats.state; the sidebar owns lifecycle + refresh. */
  gitStats: UseSpcodeGitStats;
  /** Stats-panel collapsed state (persisted by the sidebar). */
  statsOpen: boolean;
  /** Heatmap time-range window (preset or custom). Owned by the
   *  sidebar; we forward it down to GitStatsPanel verbatim. */
  range: GitStatsRange;
  /** 2026-07-19 hot-files picker: user-controllable "Top N" cap
   *  (5..50). Owned by the sidebar so a change re-fetches with a
   *  new ETag bucket. Forwarded to GitStatsPanel for the live
   *  counter and used by the refresh() calls below. */
  topFilesLimit: number;
  /** 2026-09-08 (spec 2026-09-08-git-log-tags-and-filters §2.3):
   *  Ref 选择器条目 —— 当前分支 / 本地分支 / 远程分支 / 标签四组。 */
  refItems: RefPickerItem[];
  /** Spec §2b: name of the checked-out branch, or null (detached HEAD /
   *  branches not loaded). Drives the revert ⇄ cherry-pick visibility
   *  split below. */
  currentBranch: string | null;
  /** Spec §2b: the APPLIED filter ref (parent passes
   *  gitLog.filter.value.ref), NOT the in-progress draft in
   *  localFilter. ""/HEAD/<currentBranch> all mean "viewing the
   *  current branch". */
  activeRef: string | null;
  /** 2026-08-03 git-squash (spec 2026-08-03-git-squash-design §4.2):
   *  bumped by the sidebar after a successful squash so we clear the
   *  selection. */
  squashResetToken?: number;
  /** 2026-08-09 changelog: bumped by the sidebar when the changelog
   *  dialog closes so we clear the selection (mirrors squashResetToken). */
  changelogResetToken?: number;
}>();

/** Commit shape carried by the `squash` emit (spec
 *  2026-08-03-git-squash-design §4.2). Exported so the sidebar and
 *  the dialog can share the exact type. */
export interface SquashPayloadCommit {
  sha: string;
  subject: string;
  body: string | null;
}

// 2026-08-14 open-on-disk: per-file "open on the AstrBot host"
// action for the expanded commit's file list. The file paths are
// repo-relative; the effective worktree root is injected by
// GitDiffSidebar. Files deleted by the commit (status D) no longer
// exist on disk, so the action is hidden for them.
const openOnDiskRoot = inject<ComputedRef<string | null> | null>(
  "openOnDiskRoot",
  null,
);
const { opening: openingOnDisk, openOnDisk } = useOpenOnDisk(
  "spcodeProjectLoad.fileBrowser.preview",
);

function openOnDiskAbsPath(f: GitShowFile): string {
  const root = openOnDiskRoot?.value;
  if (!root || f.status === "D") return "";
  return absoluteFromSelectedDoc(root, ".", f.path);
}

function onOpenOnDiskClick(e: MouseEvent, f: GitShowFile): void {
  e.stopPropagation();
  const abs = openOnDiskAbsPath(f);
  if (!abs) return;
  void openOnDisk(abs, f.path);
}

const emit = defineEmits<{
  (e: "apply", filter: LogFilter): void;
  // reset is its own event (not just apply) so the parent can run
  // reset-specific prep — invalidate the ETag for the default filter
  // tuple and force a loading spinner — before re-fetching. Emitting
  // apply would lose that distinction; spec §6.5.1 reset behavior.
  (e: "reset", filter: LogFilter): void;
  (e: "loadMore"): void;
  (e: "refresh"): void;
  // 2026-07-17 git-revert: per-row "回滚" affordance. The sidebar
  // owns the confirm dialog + the /spcode/git-revert call (all git
  // writes live at the sidebar level); we only surface which
  // commit the user picked.
  (e: "revert", commit: { sha: string; subject: string }): void;
  // 2026-08-01 git-cherry-pick: per-row affordance mirroring "revert";
  // the sidebar owns the dialog + the /spcode/git-cherry-pick call.
  (e: "cherry-pick", commit: { sha: string; subject: string }): void;
  // 2026-08-13 git-commit-amend: edit the HEAD commit message. Only
  // emitted for the current branch's HEAD row; the sidebar owns the
  // dialog + the /spcode/git-commit-amend call.
  (e: "amend", commit: { sha: string; subject: string; body: string | null }): void;
  // Toolbar-level entry: open the cherry-pick dialog with an empty ref.
  (e: "cherry-pick-blank"): void;
  // 2026-08-03 git-squash: toolbar entry. The sidebar owns the dialog
  // + the /spcode/git-squash call; we surface the picked commits
  // ordered oldest → newest (dialog lists + pre-fills in this order).
  // `body` rides along so the dialog's AI-generate feature can feed
  // the full historical messages (subject + body) to the LLM.
  (e: "squash", payload: { commits: SquashPayloadCommit[] }): void;
  // 2026-08-09 changelog: toolbar entry. Same payload shape as squash.
  (e: "changelog", payload: { commits: SquashPayloadCommit[] }): void;
  /** Stats-panel collapse toggle (v-model:stats-open). */
  (e: "update:statsOpen", v: boolean): void;
  /** Range popover picked a new value — sidebar owns state + persistence. */
  (e: "update:range", v: GitStatsRange): void;
  /** 2026-07-19 hot-files picker: user picked a new "Top N" cap
   *  in the settings popover. Sidebar owns state + persistence
   *  and triggers the re-fetch via its statsRangeArgs watcher. */
  (e: "update:topFilesLimit", v: number): void;
}>();

// isDark for GitStatsPanel — sourced from the customizer store the
// same way FilePatchPanel does (storeToRefs keeps it a reactive Ref;
// see the v3.9 note above). The panel receives it as a prop so it
// stays store-free and unit-testable.
const { isDark } = storeToRefs(useCustomizerStore());

/** Spec 2026-08-01 §2b: complementary visibility — revert only makes
 *  sense on the current branch's own history; cherry-pick only when
 *  viewing a DIFFERENT ref (another branch, a sha, or a tag). The
 *  comparison uses the APPLIED ref (prop), so typing-but-not-applying
 *  a branch name never flips the buttons prematurely. */
const viewingCurrent = computed(() => {
  const r = props.activeRef;
  return !r || r === "HEAD" || r === props.currentBranch;
});

/** True when the given commit is the displayed list's HEAD row. */
function isHeadCommit(c: { sha: string }): boolean {
  return commits.value.length > 0 && commits.value[0].sha === c.sha;
}

// Local filter form state. Emitted on Apply; reset on Reset.
const localFilter = ref<LogFilter>({ ref: "HEAD", n: 20 });

/**
 * 2026-07-15 history-sha-jump: scroll the focused commit into
 * view after the list re-renders. Three subtleties:
 *
 *   1. We use a `data-commit-sha` attribute (set on each row)
 *      rather than a Vue template ref. With v-for + function refs,
 *      storing N refs is awkward and the lookup cost is the same.
 *   2. We await TWO `nextTick`s: one for the list to re-render,
 *      one for the freshly fetched `state` to settle into
 *      `commits` (when a deep-link lands, the parent's
 *      `focusCommit` runs a fresh `refresh()` which transitions
 *      through `loading` then `ok`, and we don't want to scroll
 *      on the stale snapshot).
 *   3. We auto-expand the focused row (when it isn't already) so
 *      the commit body + changed-files list render in the same
 *      motion as the scroll. This matches the deep-link affordance
 *      in the per-file history panel's "view this revision"
 *      action. We re-query the DOM after the expansion tick so
 *      scrollIntoView runs against the row's full (expanded)
 *      height — without the extra tick the row would scroll to
 *      its collapsed position and the expanded body would
 *      overflow below the viewport.
 *   4. We no-op silently if the SHA isn't in the visible
 *      window — the parent already pinned `ref` to the SHA so
 *      it should land at the top; if the user manually triggered
 *      a deeper filter (e.g. typed `path:` into the filter bar)
 *      that excludes the SHA we just skip the scroll.
 */
watch(
  () => [props.focusedCommitSha, props.state.kind] as const,
  async ([sha, kind]) => {
    if (!sha || kind !== "ok") return;
    await nextTick();
    await nextTick();
    const root = document.querySelector(".git-log-view");
    if (!root) return;
    const selector = `[data-commit-sha="${CSS.escape(sha)}"]`;
    const initialEl = root.querySelector(selector);
    if (!initialEl) return;
    if (!expanded.value.has(sha)) {
      toggleCommit(sha);
      await nextTick();
    }
    (root.querySelector(selector) ?? initialEl).scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  },
);

const commits = computed(() => {
  if (props.state.kind === "ok") return props.state.snapshot.commits;
  if (props.state.kind === "error") {
    // On error, keep showing the last successful snapshot's commits (if any)
    // so the user does not lose context during transient failures. For the
    // empty-repository case (reason === "empty_repository") the fallback
    // returns [] so the empty-illustration branch in the template fires.
    return props.state.previousSnapshot?.commits ?? [];
  }
  return [];
});

const isEmptyRepository = computed(() => {
  return (
    props.state.kind === "error" && props.state.reason === "empty_repository"
  );
});

const isTruncated = computed(() => {
  if (props.state.kind === "ok") return props.state.snapshot.truncated;
  if (props.state.kind === "error" && props.state.previousSnapshot) {
    return props.state.previousSnapshot.truncated;
  }
  return false;
});

const errorReason = computed(() => {
  if (props.state.kind !== "error") return null;
  return props.state.reason;
});

const expanded = ref<Set<string>>(new Set<string>());

function toggleCommit(sha: string): void {
  const next = new Set(expanded.value);
  if (next.has(sha)) next.delete(sha);
  else next.add(sha);
  expanded.value = next;
}

// ── Squash selection (2026-08-03, spec 2026-08-03-git-squash-design §4.2) ──
// Local-only by design: never lifted to Pinia, never persisted, never
// conflated with `expanded` (which tracks the accordion state).
//
// Interaction model (2026-08-03 revision): the toolbar button is
// ALWAYS clickable while viewingCurrent. The first click only arms
// "selection mode" — checkboxes appear at the left of every row. A
// second click submits, but the button stays disabled (grey) until
// the selection is valid (>= 2 HEAD-anchored contiguous rows). A
// separate cancel button exits the mode.
const squashSelecting = ref(false);
const selectedCommits = ref<Set<string>>(new Set<string>());

function toggleSquashSelection(sha: string): void {
  const next = new Set(selectedCommits.value);
  if (next.has(sha)) next.delete(sha);
  else next.add(sha);
  selectedCommits.value = next;
}

/** Valid iff the selection is exactly the top N rows (N >= 2) of the
 *  displayed list. Checkboxes only render while viewingCurrent, so
 *  the displayed list is the unfiltered HEAD-first history and
 *  "top N rows" ⇔ "HEAD-anchored contiguous". The backend
 *  re-validates authoritatively via rev-list — this is UX-only. */
const squashSelection = computed<"none" | "valid" | "invalid">(() => {
  const n = selectedCommits.value.size;
  if (n === 0) return "none";
  if (n < 2) return "invalid";
  const top = commits.value.slice(0, n);
  if (top.length < n) return "invalid";
  return top.every((c) => selectedCommits.value.has(c.sha))
    ? "valid"
    : "invalid";
});

function onSquashClick(): void {
  if (!squashSelecting.value) {
    // First click: arm selection mode, do NOT submit anything.
    // 2026-08-09: mutual exclusion with the changelog selection mode.
    if (changelogSelecting.value) onChangelogCancel();
    squashSelecting.value = true;
    return;
  }
  // `commits` is newest → oldest; take the top N, keep the picked
  // ones (all of them, by validity), then reverse to oldest → newest.
  const n = selectedCommits.value.size;
  const picked = commits.value
    .slice(0, n)
    .filter((c) => selectedCommits.value.has(c.sha))
    .reverse()
    .map((c) => ({ sha: c.sha, subject: c.subject, body: c.body }));
  emit("squash", { commits: picked });
}

function onSquashCancel(): void {
  squashSelecting.value = false;
  selectedCommits.value = new Set();
}

// ── Changelog selection (2026-08-09, spec 2026-08-09 §3.1) ──
// Mirrors the squash state machine with two deliberate differences:
//   1. Contiguity anywhere in the displayed list (NOT HEAD-anchored);
//      min 1 commit, max 50 (prompt-size guard).
//   2. Available in ANY history view (read-only operation), so no
//      viewingCurrent gate.
const CHANGELOG_MAX_COMMITS = 50;
const changelogSelecting = ref(false);
const selectedChangelogCommits = ref<Set<string>>(new Set<string>());

function toggleChangelogSelection(sha: string): void {
  const next = new Set(selectedChangelogCommits.value);
  if (next.has(sha)) next.delete(sha);
  else next.add(sha);
  selectedChangelogCommits.value = next;
}

/** Valid iff the selected shas form a contiguous segment of the
 *  displayed list and 1 <= size <= 50. */
const changelogSelection = computed<"none" | "valid" | "invalid">(() => {
  const n = selectedChangelogCommits.value.size;
  if (n === 0) return "none";
  if (n > CHANGELOG_MAX_COMMITS) return "invalid";
  const indices = commits.value
    .map((c, i) => (selectedChangelogCommits.value.has(c.sha) ? i : -1))
    .filter((i) => i !== -1);
  if (indices.length !== n) return "invalid";
  return indices[indices.length - 1] - indices[0] === n - 1
    ? "valid"
    : "invalid";
});

function onChangelogClick(): void {
  if (!changelogSelecting.value) {
    // Mutual exclusion: squash checkboxes cannot coexist.
    if (squashSelecting.value) onSquashCancel();
    changelogSelecting.value = true;
    return;
  }
  // Displayed list is newest → oldest; emit oldest → newest.
  const picked = commits.value
    .filter((c) => selectedChangelogCommits.value.has(c.sha))
    .reverse()
    .map((c) => ({ sha: c.sha, subject: c.subject, body: c.body }));
  emit("changelog", { commits: picked });
}

function onChangelogCancel(): void {
  changelogSelecting.value = false;
  selectedChangelogCommits.value = new Set();
}

// Reset channels mirror the squash watchers.
watch(
  () => props.changelogResetToken,
  () => {
    onChangelogCancel();
  },
);

// Reset channels: the sidebar bumps squashResetToken after a
// successful squash; leaving the current-branch view unmounts the
// checkboxes, so any stale selection must not survive invisibly.
watch(
  () => props.squashResetToken,
  () => {
    onSquashCancel();
  },
);
watch(viewingCurrent, (v) => {
  if (!v) onSquashCancel();
});

// v3.9 (2026-06-25): per-file expand/collapse state, keyed by
// `${sha}\u0001${path}`. Mirrors the `expanded` set's contract (idempotent
// toggles, no array mutation — reassign the ref to keep Vue reactivity
// happy with Set internals). The composable's fetchFile is the only
// observer that consumes this set; it dedupes in-flight and cache hits.
const expandedFiles = ref<Set<string>>(new Set<string>());

function fileKey(sha: string, filePath: string): string {
  return `${sha}\u0001${filePath}`;
}

function isFileExpanded(sha: string, filePath: string): boolean {
  return expandedFiles.value.has(fileKey(sha, filePath));
}

function toggleFile(sha: string, filePath: string): void {
  const k = fileKey(sha, filePath);
  const next = new Set(expandedFiles.value);
  if (next.has(k)) next.delete(k);
  else next.add(k);
  expandedFiles.value = next;
}

// Auto-fetch the file list for any newly-expanded commit. The
// composable's `fetch` is idempotent (no-op on cache hit / in flight),
// so calling it for already-cached SHAs is safe and cheap. Using a
// `watch` rather than `watchEffect` keeps the dependency surface
// explicit and avoids re-runs on every composable-internal mutation
// (state map / data map reassignments inside useSpcodeGitShow).
//
// `flush: "post"` defers the fetch until Vue has flushed the DOM
// update for the new `expanded` set, so the "Loading files…" row is
// visible during the first paint. Without this the spinner would not
// appear for sub-100ms responses (response arrives before Vue paints).
watch(
  () => Array.from(expanded.value),
  (newShas, oldShas = []) => {
    for (const sha of newShas) {
      if (!oldShas.includes(sha)) {
        void props.gitShow.fetch(sha);
      }
    }
  },
  { flush: "post" },
);

// v3.9: when a file row is expanded, lazily fetch its patch.
// Reusing the same `flush: "post"` + array-from-Set pattern keeps the
// spinner visible during the first paint and avoids re-runs on
// every composable-internal mutation.
watch(
  () => Array.from(expandedFiles.value),
  (newKeys, oldKeys = []) => {
    for (const k of newKeys) {
      if (oldKeys.includes(k)) continue;
      const sep = k.indexOf("\u0001");
      // Defensive: skip malformed keys (should never happen — fileKey
      // always inserts the NUL). Avoids throwing on corrupted state.
      if (sep < 0) continue;
      const sha = k.slice(0, sep);
      const path = k.slice(sep + 1);
      void props.gitShow.fetchFile(sha, path);
    }
  },
  { flush: "post" },
);

/** Spec §6.5.2: relative time formatter (P0-5 fix). */
function formatRelativeTime(isoDate: string, now: number = Date.now()): string {
  const t = new Date(isoDate).getTime();
  if (Number.isNaN(t)) return isoDate;
  const diff = now - t;
  const min = Math.floor(diff / 60_000);
  if (min < 1)
    return tm(
      "spcodeProjectLoad.diffSidebar.gitWorkflow.history.relativeTime.now",
    );
  if (min < 60)
    return tm(
      "spcodeProjectLoad.diffSidebar.gitWorkflow.history.relativeTime.minutesAgo",
      { n: min },
    );
  const h = Math.floor(min / 60);
  if (h < 24)
    return tm(
      "spcodeProjectLoad.diffSidebar.gitWorkflow.history.relativeTime.hoursAgo",
      { n: h },
    );
  const d = Math.floor(h / 24);
  if (d < 7)
    return tm(
      "spcodeProjectLoad.diffSidebar.gitWorkflow.history.relativeTime.daysAgo",
      { n: d },
    );
  const dt = new Date(t);
  const yyyy = dt.getFullYear();
  const mm = String(dt.getMonth() + 1).padStart(2, "0");
  const dd = String(dt.getDate()).padStart(2, "0");
  return tm(
    "spcodeProjectLoad.diffSidebar.gitWorkflow.history.relativeTime.exactDate",
    {
      date: `${yyyy}-${mm}-${dd}`,
    },
  );
}

function onApply(): void {
  emit("apply", { ...localFilter.value });
}

function onReset(): void {
  localFilter.value = { ref: "HEAD", n: 20 };
  // Distinct from onApply: the parent uses this to invalidate the ETag
  // for this filter tuple and force a loading transition. See the
  // `reset` event JSDoc in defineEmits above.
  emit("reset", { ...localFilter.value });
}

// ── Stats panel filter linkage (git-stats heatmap, 2026-07-18) ────

/** Day-cell click → apply the standard filter flow with the clicked
 *  day's explicit local-time range (a date-only --until parses as
 *  midnight in git and would empty a same-day filter). n=200 gives
 *  headroom for a busy day; ref stays the current filter's ref so a
 *  branch view does not jump back to HEAD. */
function onStatsFilterDate(p: { since: string; until: string }): void {
  localFilter.value = {
    ...localFilter.value,
    since: p.since,
    until: p.until,
    n: 200,
  };
  emit("apply", { ...localFilter.value });
}

/** Hot-file click → apply the standard filter flow with the path.
 *  since/until are dropped (dashboard spec §4): the hot-file ranking
 *  already answers "recent", and keeping a stale date window would
 *  hide the file's older commits the user is looking for. */
function onStatsFilterPath(path: string): void {
  localFilter.value = {
    ...localFilter.value,
    path,
    since: undefined,
    until: undefined,
    n: 200,
  };
  emit("apply", { ...localFilter.value });
}

/** Stats-panel refresh button — forward the current range so the
 *  composable hits the same ETag bucket the watcher already
 *  populates, instead of falling into `umo|worktree||` and
 *  replacing range-filtered hot files with whole-repo data.
 *  2026-07-18 fix(dashboard): previously called
 *  `gitStats.refresh({ forceLoading: true })` with no since/until
 *  — see the matching JSDoc on the sidebar's `statsRangeArgs` for
 *  the ETag-bucket story.
 *  2026-07-19 hot-files picker: also forwards the user's "Top N"
 *  cap so the manual refresh hits the same ETag bucket a setting
 *  change would. */
function onStatsRefresh(): void {
  const r = props.range;
  const args =
    r.kind === "preset"
      ? rangeForPreset(r.preset)
      : { since: r.since, until: r.until };
  void props.gitStats.refresh({
    forceLoading: true,
    ...args,
    topFiles: props.topFilesLimit,
  });
}

// ── File list helpers (spec 2026-06-25 §3.3) ──────────────────────

/** Map a git-show file status to the same FileStatus union used by
 *  git-diff. The two parsers share a 1-letter alphabet (M/A/D/R/C)
 *  so the cast is a no-op for valid statuses. */
function toFileStatus(s: GitShowFileStatus): FileStatus {
  return s as FileStatus;
}

/** Single source of truth for the per-file row icon (matches
 *  GitDiffFileItem.ICON_MAP so the two views look identical). The
 *  M/A/D/R/C palette is the same; T is folded into M by the parser
 *  per spec. */
const FILE_ICON_MAP: Record<
  GitShowFileStatus,
  { icon: string; color: string }
> = {
  M: { icon: "mdi-pencil", color: "primary" },
  A: { icon: "mdi-plus-circle", color: "success" },
  D: { icon: "mdi-minus-circle", color: "error" },
  R: { icon: "mdi-rename-box", color: "warning" },
  C: { icon: "mdi-content-copy", color: "info" },
  unknown: { icon: "mdi-file-document-edit-outline", color: "grey" },
};

function fileIcon(f: GitShowFile): { icon: string; color: string } {
  return FILE_ICON_MAP[f.status] ?? FILE_ICON_MAP.unknown;
}

function fileStatusLabel(s: GitShowFileStatus): string {
  return tm(
    `spcodeProjectLoad.diffSidebar.gitWorkflow.history.fileStatus.${s}`,
  );
}

/** Convert a rename/copy's `oldPath` + `similarity` into a single
 *  inline label. Returns null for non-rename / non-copy files. */
function renameLabel(f: GitShowFile): string | null {
  if ((f.status !== "R" && f.status !== "C") || !f.oldPath) return null;
  const similarity = f.similarity ?? 0;
  return tm("spcodeProjectLoad.diffSidebar.gitWorkflow.history.renameFrom", {
    old: f.oldPath,
    new: f.path,
    similarity,
  });
}

/** Per-commit error reason i18n key. Falls back to the raw reason
 *  string in the unlikely case the backend introduces a code we
 *  haven't localized yet. */
function fileErrorKey(reason: string): string {
  return `spcodeProjectLoad.diffSidebar.gitWorkflow.error.reason.${reason}`;
}

/** Extract a string reason from a `GitShowFetchState` discriminated
 *  union. Returns null for any non-error state. Used by the template
 *  (which can't run TypeScript `as` casts) to build the i18n key for
 *  the per-commit error message. */
function errorReasonOf(state: GitShowFetchState): string | null {
  if (state.kind === "error") return state.reason;
  return null;
}

/** Compose the user-facing error message for a per-commit file-load
 *  failure. Combines the "failed" prefix with the localized reason
 *  text. Returns null when the state is not an error. */
function fileErrorMessage(state: GitShowFetchState): string | null {
  const reason = errorReasonOf(state);
  if (reason === null) return null;
  return (
    tm("spcodeProjectLoad.diffSidebar.gitWorkflow.history.errorFiles") +
    ": " +
    tm(fileErrorKey(reason), { reason })
  );
}
</script>

<template>
  <div class="git-log-view">
    <!-- Git stats panel: embedded at the top of the history view -->
    <GitStatsPanel
      :state="gitStats.state.value"
      :open="statsOpen"
      :is-dark="isDark"
      :range="range"
      :top-files-limit="topFilesLimit"
      @update:open="(v) => emit('update:statsOpen', v)"
      @update:range="(v) => emit('update:range', v)"
      @update:top-files-limit="(v) => emit('update:topFilesLimit', v)"
      @refresh="onStatsRefresh"
      @filter-date="onStatsFilterDate"
      @filter-path="onStatsFilterPath"
    />

    <!-- Truncation banner (spec §6.5.2) -->
    <div v-if="isTruncated" class="git-log-truncated">
      {{ tm("spcodeProjectLoad.diffSidebar.gitWorkflow.history.truncated") }}
    </div>

    <!-- Filter bar (spec §6.5.1; search boxes → 12px, buttons → small) -->
    <div class="git-log-filter">
      <!-- 2026-08-01 branch-picker: v-text-field → v-combobox so the
           ref filter offers known branches (current first, then local,
           then remote) while keeping free input for sha/tag. Apply /
           Reset flow unchanged.
           2026-09-08 (spec §2.3): grouped picker — 当前分支 / 本地分支 /
           远程分支 / 标签 四组, subheader 条目不可选中。 -->
      <v-combobox
        v-model="localFilter.ref"
        :items="refItems"
        item-title="title"
        item-value="value"
        :hide-no-data="refItems.length === 0"
        :list-props="{ density: 'compact', class: 'git-log-branch-list' }"
        :label="
          tm('spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.ref')
        "
        :placeholder="
          tm(
            'spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.refPlaceholder',
          )
        "
        density="compact"
        variant="outlined"
        hide-details
        class="git-log-filter-field"
      />
      <!-- 2026-09-08 (spec §2.3): 提交名 grep 过滤（忽略大小写）。 -->
      <v-text-field
        v-model="localFilter.grep"
        :label="
          tm('spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.grep')
        "
        :placeholder="
          tm(
            'spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.grepPlaceholder',
          )
        "
        density="compact"
        variant="outlined"
        hide-details
        class="git-log-filter-field"
      />
      <v-text-field
        v-model="localFilter.author"
        :label="
          tm('spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.author')
        "
        :placeholder="
          tm(
            'spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.authorPlaceholder',
          )
        "
        density="compact"
        variant="outlined"
        hide-details
        class="git-log-filter-field"
      />
      <v-text-field
        v-model="localFilter.path"
        :label="
          tm('spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.path')
        "
        :placeholder="
          tm(
            'spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.pathPlaceholder',
          )
        "
        density="compact"
        variant="outlined"
        hide-details
        class="git-log-filter-field"
      />
      <v-text-field
        v-model="localFilter.since"
        :label="
          tm('spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.since')
        "
        :placeholder="
          tm(
            'spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.sincePlaceholder',
          )
        "
        density="compact"
        variant="outlined"
        hide-details
        class="git-log-filter-field"
      />
      <v-text-field
        v-model.number="localFilter.n"
        :label="
          tm('spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.n')
        "
        :placeholder="
          tm(
            'spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.nPlaceholder',
          )
        "
        density="compact"
        variant="outlined"
        hide-details
        type="number"
        min="1"
        max="200"
        class="git-log-filter-field git-log-filter-n"
      />
      <div class="git-log-filter-actions">
        <v-btn
          size="small"
          variant="flat"
          color="primary"
          :loading="isLoading"
          @click="onApply"
        >
          {{
            tm("spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.apply")
          }}
        </v-btn>
        <v-btn
          size="small"
          variant="text"
          :disabled="isLoading"
          @click="onReset"
        >
          {{
            tm("spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.reset")
          }}
        </v-btn>
        <!-- 2026-08-09 (elecvoid243) more-actions menu: Cherry-Pick and
             Squash/Changelog live in a "更多功能" dropdown so future
             history-tab actions can be added as extra v-list-items
             without widening the toolbar.

             2026-08-09 interaction revision (user feedback): while a
             selection mode is armed, the "更多功能" dropdown is
             REPLACED by a direct confirm button sitting exactly where
             the menu activator was — the second (confirming) click no
             longer requires reopening the menu. The button carries the
             same label + selection count + validity state as the old
             menu item; the "取消" button next to it still exits the
             mode. -->
        <v-btn
          v-if="viewingCurrent && squashSelecting"
          class="git-log-squash-confirm-btn"
          size="small"
          variant="tonal"
          color="primary"
          :disabled="squashSelection !== 'valid'"
          :title="
            squashSelection !== 'valid'
              ? tm('spcodeProjectLoad.diffSidebar.squash.selectionHint')
              : tm('spcodeProjectLoad.diffSidebar.squash.toolbarAria')
          "
          @click="onSquashClick"
        >
          <v-icon size="14" start>mdi-arrow-collapse-vertical</v-icon>
          {{ tm("spcodeProjectLoad.diffSidebar.squash.toolbar") }}
          <template v-if="selectedCommits.size > 0">
            ({{ selectedCommits.size }})
          </template>
        </v-btn>
        <v-btn
          v-else-if="changelogSelecting"
          class="git-log-changelog-confirm-btn"
          size="small"
          variant="tonal"
          color="primary"
          :disabled="changelogSelection !== 'valid'"
          :title="
            changelogSelection !== 'valid'
              ? tm('spcodeProjectLoad.diffSidebar.changelog.selectionHint')
              : tm('spcodeProjectLoad.diffSidebar.changelog.menuAria')
          "
          @click="onChangelogClick"
        >
          <v-icon size="14" start>mdi-file-document-edit-outline</v-icon>
          {{ tm("spcodeProjectLoad.diffSidebar.changelog.menu") }}
          <template v-if="selectedChangelogCommits.size > 0">
            ({{ selectedChangelogCommits.size }})
          </template>
        </v-btn>
        <v-menu v-else location="bottom end">
          <template #activator="{ props: menuProps }">
            <v-btn
              v-bind="menuProps"
              size="small"
              variant="text"
              :title="
                tm('spcodeProjectLoad.diffSidebar.moreActions.toolbarAria')
              "
            >
              <v-icon size="14" start>mdi-dots-horizontal</v-icon>
              {{ tm("spcodeProjectLoad.diffSidebar.moreActions.toolbar") }}
            </v-btn>
          </template>
          <v-list density="compact" class="git-log-more-actions-list">
            <v-list-item
              class="git-log-more-actions-item"
              @click="emit('cherry-pick-blank')"
            >
              <template #prepend>
                <v-icon size="14">mdi-source-branch-plus</v-icon>
              </template>
              <v-list-item-title>
                {{ tm("spcodeProjectLoad.diffSidebar.cherryPick.toolbar") }}
              </v-list-item-title>
              <v-tooltip
                activator="parent"
                location="start"
                :text="
                  tm('spcodeProjectLoad.diffSidebar.cherryPick.toolbarAria')
                "
              />
            </v-list-item>
            <!-- Squash (2026-08-03 revised interaction): the first
                 click arms selection mode (row checkboxes appear).
                 2026-08-09: while armed the menu is replaced by the
                 direct confirm button, so this item is only reachable
                 for the arming click; the disabled/count states below
                 are kept as defensive logic. -->
            <v-list-item
              v-if="viewingCurrent"
              class="git-log-more-actions-item git-log-squash-menu-item"
              :disabled="squashSelecting && squashSelection !== 'valid'"
              @click="onSquashClick"
            >
              <template #prepend>
                <v-icon size="14">mdi-arrow-collapse-vertical</v-icon>
              </template>
              <v-list-item-title>
                {{ tm("spcodeProjectLoad.diffSidebar.squash.toolbar") }}
                <template v-if="squashSelecting && selectedCommits.size > 0">
                  ({{ selectedCommits.size }})</template
                >
              </v-list-item-title>
              <v-tooltip
                activator="parent"
                location="start"
                :text="
                  squashSelecting && squashSelection !== 'valid'
                    ? tm('spcodeProjectLoad.diffSidebar.squash.selectionHint')
                    : tm('spcodeProjectLoad.diffSidebar.squash.toolbarAria')
                "
              />
            </v-list-item>
            <!-- 2026-08-09 changelog: contiguous-anywhere selection,
                 min 1 / max 50 commits, available in every history
                 view (read-only). -->
            <v-list-item
              class="git-log-more-actions-item git-log-changelog-menu-item"
              :disabled="changelogSelecting && changelogSelection !== 'valid'"
              @click="onChangelogClick"
            >
              <template #prepend>
                <v-icon size="14">mdi-file-document-edit-outline</v-icon>
              </template>
              <v-list-item-title>
                {{ tm("spcodeProjectLoad.diffSidebar.changelog.menu") }}
                <template
                  v-if="changelogSelecting && selectedChangelogCommits.size > 0"
                >
                  ({{ selectedChangelogCommits.size }})</template
                >
              </v-list-item-title>
              <v-tooltip
                activator="parent"
                location="start"
                :text="
                  changelogSelecting && changelogSelection !== 'valid'
                    ? tm(
                        'spcodeProjectLoad.diffSidebar.changelog.selectionHint',
                      )
                    : tm('spcodeProjectLoad.diffSidebar.changelog.menuAria')
                "
              />
            </v-list-item>
          </v-list>
        </v-menu>
        <!-- Selection-mode exit affordance (the main button is
             disabled while the selection is invalid, so it cannot
             double as the exit). -->
        <v-btn
          v-if="viewingCurrent && squashSelecting"
          class="git-log-squash-cancel-btn"
          size="small"
          variant="text"
          @click="onSquashCancel"
        >
          {{ tm("spcodeProjectLoad.diffSidebar.squash.cancelSelection") }}
        </v-btn>
        <!-- 2026-08-09 changelog: selection-mode exit affordance. -->
        <v-btn
          v-if="changelogSelecting"
          class="git-log-squash-cancel-btn"
          size="small"
          variant="text"
          @click="onChangelogCancel"
        >
          {{ tm("spcodeProjectLoad.diffSidebar.changelog.cancelSelection") }}
        </v-btn>
      </div>
    </div>

    <!-- Body: loading / empty repo / no commits / commit list / error -->
    <div v-if="state.kind === 'loading'" class="git-log-center">
      <v-progress-circular indeterminate :size="28" />
      <span class="git-log-center-text">
        {{ tm("spcodeProjectLoad.diffSidebar.gitWorkflow.history.loading") }}
      </span>
    </div>

    <div v-else-if="isEmptyRepository" class="git-log-center">
      <v-icon size="32" color="grey">mdi-source-branch</v-icon>
      <span class="git-log-center-text">
        {{
          tm(
            "spcodeProjectLoad.diffSidebar.gitWorkflow.history.emptyRepository",
          )
        }}
      </span>
    </div>

    <div v-else-if="commits.length === 0" class="git-log-center">
      <v-icon size="32" color="grey">mdi-source-commit-off</v-icon>
      <span class="git-log-center-text">
        {{ tm("spcodeProjectLoad.diffSidebar.gitWorkflow.history.empty") }}
      </span>
    </div>

    <div
      v-else
      class="git-log-list"
      :class="{ 'squash-selecting': squashSelecting || changelogSelecting }"
    >
      <div
        v-for="c in commits"
        :key="c.sha"
        class="git-log-item"
        :class="{
          expanded: expanded.has(c.sha),
          // 2026-07-15 history-sha-jump: only one row at a time
          // carries this class; it is consumed by the CSS rule
          // below AND by the watcher above which uses the
          // `data-commit-sha` selector to scrollIntoView.
          'is-focused': props.focusedCommitSha === c.sha,
        }"
        :data-commit-sha="c.sha"
      >
        <!-- 2026-08-03 git-squash: per-row selection checkbox, only
             rendered in selection mode (armed via the toolbar
             button) and absolutely positioned in the left gutter
             the rows grow while the mode is active. -->
        <button
          v-if="viewingCurrent && squashSelecting"
          type="button"
          class="git-log-item-select"
          :class="{ 'is-selected': selectedCommits.has(c.sha) }"
          :title="tm('spcodeProjectLoad.diffSidebar.squash.rowSelectTitle')"
          :aria-label="
            tm('spcodeProjectLoad.diffSidebar.squash.rowSelectAria', {
              sha: c.shaShort || c.sha.slice(0, 7),
            })
          "
          :aria-pressed="selectedCommits.has(c.sha)"
          @click.stop="toggleSquashSelection(c.sha)"
        >
          <v-icon size="14">
            {{
              selectedCommits.has(c.sha)
                ? "mdi-checkbox-marked"
                : "mdi-checkbox-blank-outline"
            }}
          </v-icon>
        </button>
        <!-- 2026-08-09 changelog: same checkbox UI bound to the
             changelog selection set (mutually exclusive modes). -->
        <button
          v-if="changelogSelecting"
          type="button"
          class="git-log-item-select"
          :class="{ 'is-selected': selectedChangelogCommits.has(c.sha) }"
          :title="tm('spcodeProjectLoad.diffSidebar.changelog.rowSelectTitle')"
          :aria-label="
            tm('spcodeProjectLoad.diffSidebar.changelog.rowSelectAria', {
              sha: c.shaShort || c.sha.slice(0, 7),
            })
          "
          :aria-pressed="selectedChangelogCommits.has(c.sha)"
          @click.stop="toggleChangelogSelection(c.sha)"
        >
          <v-icon size="14">
            {{
              selectedChangelogCommits.has(c.sha)
                ? "mdi-checkbox-marked"
                : "mdi-checkbox-blank-outline"
            }}
          </v-icon>
        </button>
        <button
          type="button"
          class="git-log-item-header"
          :aria-expanded="expanded.has(c.sha)"
          :aria-label="
            expanded.has(c.sha)
              ? tm(
                  'spcodeProjectLoad.diffSidebar.gitWorkflow.history.collapseCommit',
                )
              : tm(
                  'spcodeProjectLoad.diffSidebar.gitWorkflow.history.expandCommit',
                )
          "
          @click="toggleCommit(c.sha)"
        >
          <v-icon size="14" class="git-log-item-icon">mdi-source-commit</v-icon>
          <span class="git-log-item-sha">{{
            c.shaShort || c.sha.slice(0, 7)
          }}</span>
          <span class="git-log-item-subject">{{ c.subject }}</span>
        </button>
        <div class="git-log-item-meta">
          <span class="git-log-item-author">{{ c.author.name }}</span>
          <span class="git-log-item-sep">·</span>
          <span class="git-log-item-time">{{
            formatRelativeTime(c.date)
          }}</span>
          <span class="git-log-item-sep">·</span>
          <!--
            v3.9 (2026-06-25, elecvoid243): split stat rendering so the
            additions/deletions numbers can take git-diff colors
            (green / red). The i18n `filesStat` key now only renders
            the "N files" prefix; the colored +/ − spans are appended
            inline below.
          -->
          <span class="git-log-item-stat">
            {{
              tm(
                "spcodeProjectLoad.diffSidebar.gitWorkflow.history.filesStat",
                { files: c.shortstat.files },
              )
            }}
            <span class="git-log-item-stat-add"
              >+{{ c.shortstat.additions }}</span
            >
            <span class="git-log-item-stat-del"
              >−{{ c.shortstat.deletions }}</span
            >
          </span>
          <!-- 2026-08-13 git-commit-amend: only the current branch's
               HEAD row may have its message edited. -->
          <button
            v-if="viewingCurrent && isHeadCommit(c)"
            type="button"
            class="git-log-item-amend"
            :title="
              tm('spcodeProjectLoad.diffSidebar.amend.editAria', {
                sha: c.sha.slice(0, 7),
              })
            "
            :aria-label="
              tm('spcodeProjectLoad.diffSidebar.amend.editAria', {
                sha: c.sha.slice(0, 7),
              })
            "
            @click="
              emit('amend', { sha: c.sha, subject: c.subject, body: c.body })
            "
          >
            <v-icon size="13">mdi-pencil-outline</v-icon>
            {{ tm('spcodeProjectLoad.diffSidebar.amend.edit') }}
          </button>
          <!-- 2026-07-17 git-revert: per-row revert action, revealed
               on row hover (focus-within keeps it reachable by
               keyboard). Lives in the meta line — NOT inside the
               header <button> (nested buttons are invalid HTML). -->
          <button
            v-if="viewingCurrent"
            type="button"
            class="git-log-item-revert"
            :title="
              tm(
                'spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertTitle',
              )
            "
            :aria-label="
              tm('spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertAria')
            "
            @click="emit('revert', { sha: c.sha, subject: c.subject })"
          >
            <v-icon size="13">mdi-undo-variant</v-icon>
            {{ tm("spcodeProjectLoad.diffSidebar.gitWorkflow.history.revert") }}
          </button>
          <!-- 2026-08-01 git-cherry-pick: per-row action, hover-revealed
               like the revert button beside it. -->
          <button
            v-if="!viewingCurrent"
            type="button"
            class="git-log-item-cherry-pick"
            :title="
              tm('spcodeProjectLoad.diffSidebar.cherryPick.rowActionTitle')
            "
            :aria-label="
              tm('spcodeProjectLoad.diffSidebar.cherryPick.rowActionAria', {
                sha: c.sha.slice(0, 7),
              })
            "
            @click="emit('cherry-pick', { sha: c.sha, subject: c.subject })"
          >
            <v-icon size="13">mdi-source-branch-plus</v-icon>
            {{ tm("spcodeProjectLoad.diffSidebar.cherryPick.rowAction") }}
          </button>
        </div>
        <div v-if="expanded.has(c.sha) && c.body" class="git-log-item-body">
          <pre class="git-log-item-body-pre">{{ c.body }}</pre>
        </div>

        <!-- Changed files section (spec 2026-06-25 §3.3). Only rendered
             when the commit is expanded. Auto-fetches via the watch
             on `expanded` above; idempotent on the composable side. -->
        <div v-if="expanded.has(c.sha)" class="git-log-item-files">
          <div
            v-if="gitShow.getState(c.sha).kind === 'loading'"
            class="git-log-files-status"
          >
            <v-progress-circular indeterminate :size="14" :width="2" />
            <span class="git-log-files-status-text">
              {{
                tm(
                  "spcodeProjectLoad.diffSidebar.gitWorkflow.history.loadingFiles",
                )
              }}
            </span>
          </div>
          <div
            v-else-if="gitShow.getState(c.sha).kind === 'error'"
            class="git-log-files-status is-error"
          >
            <v-icon size="14" color="error">mdi-alert-circle-outline</v-icon>
            <span class="git-log-files-status-text">
              {{ fileErrorMessage(gitShow.getState(c.sha)) }}
            </span>
            <button
              type="button"
              class="git-log-files-retry"
              :aria-label="
                tm(
                  'spcodeProjectLoad.diffSidebar.gitWorkflow.history.errorFilesAria',
                )
              "
              @click="gitShow.fetch(c.sha)"
            >
              {{ tm("spcodeProjectLoad.diffSidebar.error.retry") }}
            </button>
          </div>
          <template v-else-if="gitShow.getState(c.sha).kind === 'ok'">
            <div
              v-if="(gitShow.getData(c.sha)?.files.length ?? 0) === 0"
              class="git-log-files-status"
            >
              <v-icon size="14" color="grey">mdi-file-outline</v-icon>
              <span class="git-log-files-status-text">
                {{
                  tm(
                    "spcodeProjectLoad.diffSidebar.gitWorkflow.history.noFiles",
                  )
                }}
              </span>
            </div>
            <ul v-else class="git-log-files-list" :data-sha="c.sha">
              <li
                v-for="f in gitShow.getData(c.sha)?.files ?? []"
                :key="f.path"
                class="git-log-files-item"
              >
                <!--
                  v3.9 (2026-06-25): file row is now interactive. The
                  button toggles a lazy-loaded patch panel below the
                  row. `aria-expanded` mirrors isFileExpanded so screen
                  readers announce the new state. `aria-label` uses a
                  dedicated i18n key with the path interpolated; falls
                  back to a generic key for non-renamed paths.
                -->
                <!--
                  2026-08-14 open-on-disk: the row button and the
                  open-on-disk icon button sit side by side in a
                  flex row (buttons cannot nest inside the row
                  button).
                -->
                <div class="git-log-files-item-row">
                  <button
                    type="button"
                    class="git-log-files-item-button"
                    :aria-expanded="isFileExpanded(c.sha, f.path)"
                    :aria-label="
                      tm(
                        'spcodeProjectLoad.diffSidebar.gitWorkflow.history.expandFileAria',
                        { path: renameLabel(f) ?? f.path },
                      )
                    "
                    @click="toggleFile(c.sha, f.path)"
                  >
                    <v-icon :size="14" :color="fileIcon(f).color">
                      {{ fileIcon(f).icon }}
                    </v-icon>
                    <!-- Status label: localized via fileStatus.<status> key.
                         The label is informational; screen readers also get
                         the title="..." attribute that duplicates the label
                         plus the file path for full context. -->
                    <span
                      class="git-log-files-status-badge"
                      :title="fileStatusLabel(f.status)"
                    >
                      {{ fileStatusLabel(f.status) }}
                    </span>
                    <!-- For R / C: show the "old → new (similarity%)" label
                         inline instead of the bare `f.path`, so the rename
                         source is visible without needing a tooltip. Other
                         statuses render the plain path. -->
                    <span v-if="renameLabel(f)" class="git-log-files-path">{{
                      renameLabel(f)
                    }}</span>
                    <span v-else class="git-log-files-path" :title="f.path">{{
                      f.path
                    }}</span>
                    <span class="git-log-files-stats">
                      <span class="git-log-files-add">+{{ f.additions }}</span>
                      <span class="git-log-files-del">−{{ f.deletions }}</span>
                    </span>
                    <!-- Chevron indicator: up = expanded, down = collapsed.
                         Uses a literal mdi name to avoid pulling in extra
                         icon components; the row button already handles
                         click semantics. -->
                    <v-icon :size="14" class="git-log-files-chevron">
                      {{
                        isFileExpanded(c.sha, f.path)
                          ? "mdi-chevron-up"
                          : "mdi-chevron-down"
                      }}
                    </v-icon>
                  </button>
                  <!-- 2026-08-14 open-on-disk: opens the file on the
                       AstrBot host with the OS default application.
                       Hidden for files deleted by the commit. -->
                  <button
                    v-if="openOnDiskAbsPath(f)"
                    type="button"
                    class="git-log-files-item-open-on-disk"
                    :disabled="openingOnDisk"
                    :aria-label="
                      tm('spcodeProjectLoad.diffSidebar.openOnDisk.buttonAria', {
                        path: f.path,
                      })
                    "
                    :title="
                      tm('spcodeProjectLoad.diffSidebar.openOnDisk.buttonAria', {
                        path: f.path,
                      })
                    "
                    @click="onOpenOnDiskClick($event, f)"
                  >
                    <v-icon :size="14">mdi-open-in-new</v-icon>
                  </button>
                </div>
                <!--
                  v3.9: lazy-loaded patch panel. States mirror the
                  composable's GitShowFileFetchState enum:
                    loading → spinner
                    error   → reason message + retry
                    ok      → DiffPreview (or fallback for binary/unknown)
                  We always render the panel container (not v-if) so
                  ARIA `aria-expanded` on the button stays consistent
                  with the visual layout; the inner content swaps
                  per-state.
                -->
                <div
                  v-if="isFileExpanded(c.sha, f.path)"
                  class="git-log-files-patch"
                >
                  <FilePatchPanel
                    :sha="c.sha"
                    :path="f.path"
                    :state="gitShow.getFileState(c.sha, f.path)"
                    :data="gitShow.getFileData(c.sha, f.path)"
                    @retry="gitShow.fetchFile(c.sha, f.path)"
                  />
                </div>
              </li>
            </ul>
            <div
              v-if="gitShow.getData(c.sha)?.truncated"
              class="git-log-files-truncated"
            >
              {{
                tm(
                  "spcodeProjectLoad.diffSidebar.gitWorkflow.history.filesTruncated",
                  {
                    shown: gitShow.getData(c.sha)?.count ?? 0,
                    total:
                      (gitShow.getData(c.sha)?.count ?? 0) +
                      // `count` is the returned count; the API doesn't
                      // expose the post-truncation hidden count, so we
                      // render "showing N of N+". The user can re-query
                      // with a higher max_files if needed.
                      0,
                    max: gitShow.getData(c.sha)?.maxFiles ?? 500,
                  },
                )
              }}
            </div>
          </template>
        </div>
      </div>

      <div v-if="hasMore" class="git-log-load-more">
        <v-btn
          size="small"
          variant="text"
          color="primary"
          :loading="isLoading"
          :disabled="isLoading"
          @click="emit('loadMore')"
        >
          {{ tm("spcodeProjectLoad.diffSidebar.gitWorkflow.history.loadMore") }}
        </v-btn>
      </div>
    </div>

    <!-- Generic error banner with retry (above any cached commits). -->
    <div v-if="errorReason && !isEmptyRepository" class="git-log-banner-error">
      <span>
        {{
          tm(
            `spcodeProjectLoad.diffSidebar.gitWorkflow.error.reason.${errorReason}`,
            { reason: errorReason },
          )
        }}
      </span>
      <button class="git-log-banner-retry" @click="emit('refresh')">
        {{ tm("spcodeProjectLoad.diffSidebar.error.retry") }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.git-log-view {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 0;
}

.git-log-truncated {
  padding: 6px 12px;
  margin: 0 12px;
  background: rgba(255, 193, 7, 0.12);
  color: rgb(255, 152, 0);
  font-size: 12px;
  border-radius: 4px;
}

/* 2026-07-18 git-log-filter-redesign: collapsed the previous
   2-column / 4-row layout into a symmetric 3×2 grid. Five filter
   fields + the actions row fill six equal cells (Ref / 作者 / 路径
   on row 1, 起始时间 / 数量 / [筛选 重设筛选条件] on row 2). The
   sidebar is
   resizable (GitDiffSidebar.sidebarWidth), so 1fr tracks keep the
   columns equal at any width the user drags to; at the typical
   ~600–800px panel this yields ~180–250px per cell. The 数量 cell
   stretches to a full cell on purpose — the compact number input
   filling the cell keeps the grid strictly symmetric (the approved
   mockup), and the actions cluster is right-aligned in its cell.

   2026-09-08 (spec §2.3): the 提交名 (grep) field makes it six
   fields; the actions row now spans the full width as a third row
   (grid-column: 1 / -1), keeping the six fields in two symmetric
   3-column rows. */
.git-log-filter {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px 8px;
  padding: 8px 12px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}
.git-log-filter-field {
  /* Match the commit subject (.git-log-item-subject) so the filter
     row visually aligns with the history list below it. Reduced to
     12px (from 13px) to feel proportional next to the commit rows
     that use 11.5-13px text. */
  font-size: 12px;
}
.git-log-filter-field :deep(.v-field__input),
.git-log-filter-field :deep(.v-label) {
  font-size: 12px;
}
/* Intentionally no max-width / justify-self on .git-log-filter-n:
   the count field is meant to stretch to fill its 1fr cell so the
   3×2 grid stays perfectly column-symmetric (matches the redesign
   mockup the reviewer approved). */
.git-log-filter-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 4px;
  /* 2026-09-08 (spec §2.3): six fields above → actions own the
     full third row instead of sharing a cell with 数量. */
  grid-column: 1 / -1;
}

.git-log-center {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px 16px;
  min-height: 140px;
}
.git-log-center-text {
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 13px;
}

.git-log-list {
  display: flex;
  flex-direction: column;
}
.git-log-item {
  /* 2026-08-03 git-squash: anchor for the absolutely positioned
     selection checkbox. */
  position: relative;
  padding: 8px 12px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}
/* 2026-07-15 history-sha-jump: highlight the row targeted by a
   deep-link from the per-file history panels (workspace / doc
   manager). Uses inset box-shadow so the 2px accent doesn't shift
   the inner content (the existing padding is preserved exactly). */
.git-log-item.is-focused {
  background: rgba(var(--v-theme-primary), 0.08);
  box-shadow: inset 2px 0 0 0 rgb(var(--v-theme-primary));
}
.git-log-item-header {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 0;
  background: transparent;
  border: none;
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  color: inherit;
}
.git-log-item-header:hover .git-log-item-subject {
  color: rgb(var(--v-theme-primary));
}
.git-log-item-icon {
  color: rgb(var(--v-theme-primary));
  flex-shrink: 0;
}
.git-log-item-sha {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.7);
  flex-shrink: 0;
  /* SHA selectable feedback: switch the cursor to "text" so the user
     sees the I-beam and knows the hash is copyable, and let a single
     click select the whole string. The parent <button> remains
     clickable — only the cursor + selection affordance is overridden
     on this child span. The muted background + left primary accent
     only render on hover/selection; at rest the SHA stays quiet to
     avoid distracting from the commit subject. */
  cursor: text;
  user-select: all;
  -webkit-user-select: all;
  padding: 0 4px;
  margin-left: -4px;
  border-radius: 3px;
  transition:
    background 0.12s ease,
    color 0.12s ease;
}
.git-log-item-sha:hover,
.git-log-item-sha:focus-visible {
  background: rgba(var(--v-theme-primary), 0.1);
  color: rgb(var(--v-theme-primary));
  outline: none;
}
.git-log-item-header:hover .git-log-item-sha {
  color: rgb(var(--v-theme-on-surface));
}
.git-log-item-subject {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.git-log-item-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  margin-top: 2px;
  padding-left: 20px;
}
/* 2026-07-17 git-revert: per-row revert action in the meta line.
   Hidden until the row is hovered / the button itself is focused
   (keyboard reachability), mirroring the file-list hover-action
   pattern used across the sidebar. */
.git-log-item-revert {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  padding: 1px 8px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.15);
  border-radius: 4px;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.65);
  cursor: pointer;
  opacity: 0;
  transition:
    opacity 0.12s,
    color 0.12s,
    border-color 0.12s;
}
.git-log-item:hover .git-log-item-revert,
.git-log-item-revert:focus-visible {
  opacity: 1;
}
.git-log-item-revert:hover {
  color: rgb(var(--v-theme-primary));
  border-color: rgba(var(--v-theme-primary), 0.4);
}
/* 2026-08-13 git-commit-amend: per-row action mirroring
   .git-log-item-revert (hover-reveal). Rendered before the revert
   button; no margin-left:auto (the revert button pushes the group). */
.git-log-item-amend {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 7px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: rgb(var(--v-theme-on-surface-variant));
  font-size: 11.5px;
  line-height: 1.2;
  opacity: 0;
  cursor: pointer;
  transition:
    opacity 0.12s,
    color 0.12s,
    border-color 0.12s;
}
.git-log-item:hover .git-log-item-amend,
.git-log-item-amend:focus-visible {
  opacity: 1;
}
.git-log-item-amend:hover {
  color: rgb(var(--v-theme-primary));
  border-color: rgba(var(--v-theme-primary), 0.4);
}
/* 2026-08-01 git-cherry-pick: per-row action mirroring
   .git-log-item-revert (hover-reveal). No margin-left:auto — the
   revert button already pushes the pair to the right edge. */
.git-log-item-cherry-pick {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  padding: 1px 8px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.15);
  border-radius: 4px;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.65);
  cursor: pointer;
  opacity: 0;
  transition:
    opacity 0.12s,
    color 0.12s,
    border-color 0.12s;
}
.git-log-item:hover .git-log-item-cherry-pick,
.git-log-item-cherry-pick:focus-visible {
  opacity: 1;
}
.git-log-item-cherry-pick:hover {
  color: rgb(var(--v-theme-primary));
  border-color: rgba(var(--v-theme-primary), 0.4);
}
.git-log-item-author {
  color: rgba(var(--v-theme-on-surface), 0.8);
}
.git-log-item-sep {
  color: rgba(var(--v-theme-on-surface), 0.35);
}
.git-log-item-stat {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
}
/* v3.9 (2026-06-25, elecvoid243): color the additions / deletions
   counters in the commit summary to match the diff-view palette so
   users can scan "+N" / "−N" deltas at a glance. Light-mode values
   mirror GitHub's diff colors; the dark-mode override below uses
   the brighter variants (matches DiffPreview). */
.git-log-item-stat-add {
  color: #2da44e;
  font-weight: 500;
}
.git-log-item-stat-del {
  color: #cf222e;
  font-weight: 500;
  margin-left: 4px;
}
/* Vuetify toggles `v-theme--dark` on the html root when the active
   theme is dark — use that selector (matches the rest of the
   dashboard, e.g. ConfigPage/ConversationPage) instead of the
   OS-level media query. */
.v-theme--dark .git-log-item-stat-add {
  color: #57ab5a;
}
.v-theme--dark .git-log-item-stat-del {
  color: #f47067;
}
.git-log-item-body {
  margin-top: 6px;
  padding-left: 20px;
}
.git-log-item-body-pre {
  margin: 0;
  padding: 6px 8px;
  background: rgba(var(--v-theme-on-surface), 0.04);
  border-radius: 4px;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: pre-wrap;
  word-break: break-word;
  color: rgba(var(--v-theme-on-surface), 0.85);
}

/* ── Changed files section (spec 2026-06-25 §3.3) ──────────── */

.git-log-item-files {
  /* Indented to align with the commit body / meta text. The same
     20px gutter as .git-log-item-body keeps the visual rhythm
     consistent across the expanded state. */
  margin-top: 6px;
  padding-left: 20px;
}

.git-log-files-status {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 12px;
}
.git-log-files-status.is-error {
  color: rgb(248, 81, 73);
}
.git-log-files-status-text {
  flex: 1;
  min-width: 0;
}
.git-log-files-retry {
  background: transparent;
  border: 1px solid currentColor;
  border-radius: 4px;
  padding: 1px 8px;
  cursor: pointer;
  color: inherit;
  font-family: inherit;
  font-size: 11px;
  flex-shrink: 0;
}
.git-log-files-retry:hover {
  background: rgba(248, 81, 73, 0.1);
}

.git-log-files-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.git-log-files-item {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 4px;
  padding: 3px 6px;
  border-radius: 4px;
  font-size: 12px;
  /* Subtle hover so the row feels interactive even though clicking
     it does nothing in the history view (the diff view is the right
     place to inspect / restore). Matches the row density of the
     diff view's GitDiffFileItem without inheriting its full style. */
  transition: background 0.12s ease;
}
.git-log-files-item:hover {
  background: transparent;
}
/* The row is now a <button> for accessibility — keep its inner items
   on a single line (badge / path / stats / chevron) by turning the
   button itself into a flex container. v3.9 (2026-06-25). */
.git-log-files-item-button {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 3px 6px;
  border-radius: 4px;
  font-size: 12px;
  background: transparent;
  border: none;
  color: inherit;
  text-align: left;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.12s ease;
}

/* 2026-08-14 open-on-disk: the row button and the open-on-disk
   icon button sit side by side; the row button keeps the flexible
   width, the icon button stays flush right. */
.git-log-files-item-row {
  display: flex;
  align-items: center;
  gap: 2px;
}
.git-log-files-item-row .git-log-files-item-button {
  flex: 1;
  min-width: 0;
  width: auto;
}
.git-log-files-item-open-on-disk {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  color: rgb(var(--v-theme-info));
  cursor: pointer;
  opacity: 0.5;
  transition:
    opacity 0.12s ease,
    background 0.12s ease;
  flex-shrink: 0;
}
.git-log-files-item:hover .git-log-files-item-open-on-disk {
  opacity: 1;
}
.git-log-files-item-open-on-disk:hover {
  background: rgba(var(--v-theme-info), 0.1);
}
.git-log-files-item-open-on-disk:disabled {
  cursor: default;
  opacity: 0.3;
}
.git-log-files-item-open-on-disk:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
  opacity: 1;
}
.git-log-files-item-button:hover {
  background: rgba(var(--v-theme-on-surface), 0.04);
}
.git-log-files-item-button:focus-visible {
  outline: 2px solid rgba(var(--v-theme-primary), 0.5);
  outline-offset: -2px;
}
.git-log-files-chevron {
  margin-left: auto;
  flex-shrink: 0;
  transition: transform 0.15s ease;
}
.git-log-files-status-badge {
  display: inline-block;
  min-width: 28px;
  text-align: center;
  padding: 1px 4px;
  border-radius: 3px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgba(var(--v-theme-on-surface), 0.7);
  font-size: 10px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  flex-shrink: 0;
}
.git-log-files-path {
  flex: 1;
  min-width: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.85);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.git-log-files-stats {
  display: flex;
  gap: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  flex-shrink: 0;
}
.git-log-files-add {
  color: rgb(46, 160, 67);
}
.git-log-files-del {
  color: rgb(248, 81, 73);
}

.git-log-files-truncated {
  margin-top: 4px;
  padding: 4px 6px;
  background: rgba(255, 193, 7, 0.1);
  color: rgb(255, 152, 0);
  font-size: 11px;
  border-radius: 3px;
}

.git-log-load-more {
  display: flex;
  justify-content: center;
  padding: 8px 0;
}

.git-log-banner-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 12px;
  margin: 8px 12px 0;
  background: rgba(248, 81, 73, 0.1);
  border-radius: 4px;
  font-size: 12px;
  color: rgb(248, 81, 73);
}
.git-log-banner-retry {
  background: transparent;
  border: 1px solid currentColor;
  border-radius: 4px;
  padding: 2px 8px;
  cursor: pointer;
  color: inherit;
}

/* 2026-07-18: removed the prior (max-width: 760px) override — it
   duplicated the base 1fr 1fr and didn't account for the resizable
   sidebar. The auto-fit grid above handles width adaptation. */

/* 2026-08-03 git-squash: in selection mode every row grows a left
   gutter that the absolutely positioned checkbox sits in, so the
   checkbox never overlaps the commit icon / sha text. */
.git-log-list.squash-selecting .git-log-item {
  padding-left: 32px;
}
/* 2026-08-03 git-squash: per-row selection checkbox. Rendered only
   in selection mode (no hover reveal) and always visible. Vertical
   math: row padding-top is 8px and the header line is ~18-20px
   tall, so top: 9px centers the 16px box on the header line. */
.git-log-item-select {
  position: absolute;
  left: 8px;
  top: 9px;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  padding: 0;
  border: none;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.45);
  cursor: pointer;
  transition: color 0.12s;
}
.git-log-item-select:hover,
.git-log-item-select:focus-visible {
  color: rgb(var(--v-theme-primary));
}
.git-log-item-select.is-selected {
  color: rgb(var(--v-theme-primary));
}
</style>

<!-- 2026-08-02 branch-picker density fix: the ref combobox dropdown
     renders in a teleported overlay (outside this component's DOM
     subtree), so scoped styles cannot reach it. This unscoped block
     targets the unique class passed via :list-props. -->
<style>
.git-log-branch-list .v-list-item-title {
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.git-log-branch-list .v-list-item {
  min-height: 28px;
}

/* 2026-08-09 (elecvoid243) more-actions menu: v-menu content is
   teleported outside this component's subtree, so these rules must
   live in the global block (same reason as .git-log-branch-list
   above). Keeps the two action rows compact and aligns the
   prepend icon with the label. */
.git-log-more-actions-list {
  min-width: 160px;
}
.git-log-more-actions-list .v-list-item {
  min-height: 32px;
}
.git-log-more-actions-list .v-list-item-title {
  font-size: 13px;
}
.git-log-more-actions-list .v-list-item__prepend .v-icon {
  margin-inline-end: 8px;
}
</style>
