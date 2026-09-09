<!-- Author: elecvoid243, 2026-06-18
     Updated 2026-06-18 — worktree switcher (docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md)
     Updated 2026-06-20 — scope switcher (docs/superpowers/specs/2026-06-20-git-diff-scope-switcher-design.md)
     Layout mirrors ReasoningSidebar.vue so resizing the sidebar takes
     space from .chat-main (flex sibling) instead of overlaying it. -->
<script setup lang="ts">
import {
  ref,
  watch,
  provide,
  onBeforeUnmount,
  computed,
  onMounted,
  nextTick,
} from "vue";
import {
  useSpcodeGitDiff,
  DEFAULT_SCOPE,
  type GitDiffScope,
  type GitDiffFetchState,
} from "@/composables/useSpcodeGitDiff";
import { useSpcodeGitStatus } from "@/composables/useSpcodeGitStatus";
import {
  isNewFileScope,
  type SpcodeGitStatusFile,
} from "@/composables/parseSpcodeGitStatus";
import type {
  SpcodeGitDiffFile,
  SpcodeGitDiffSnapshot,
  FileStatus,
} from "@/composables/parseSpcodeGitDiff";
import type { SpcodeGitWorktree } from "@/composables/parseSpcodeWorktrees";
import {
  useSpcodeWorktrees,
  type WorktreeAddParams,
  type WorktreeLockParams,
} from "@/composables/useSpcodeWorktrees";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import { useSpcodeGitRepoProbe } from "@/composables/useSpcodeGitRepoProbe";
import GitRepoInitPrompt from "@/components/chat/message_list_comps/GitRepoInitPrompt.vue";
import {
  useSpcodeFileRestore,
  type RestoreResult,
} from "@/composables/useSpcodeFileRestore";
import {
  useSpcodeFileDiscardHunk,
  type DiscardHunkResult,
} from "@/composables/useSpcodeFileDiscardHunk";
import { useSpcodeGitStage } from "@/composables/useSpcodeGitStage";
import { useSpcodeGitUnstage } from "@/composables/useSpcodeGitUnstage";
import { useSpcodeGitCommit } from "@/composables/useSpcodeGitCommit";
import { useSpcodeGitCommitAmend } from "@/composables/useSpcodeGitCommitAmend";
import { useSpcodeGitRevert } from "@/composables/useSpcodeGitRevert";
import {
  useSpcodeGitReset,
  type GitResetMode,
} from "@/composables/useSpcodeGitReset";
import { useSpcodeFileWrite } from "@/composables/useSpcodeFileWrite";
import GitIgnoreEditor from "@/components/chat/message_list_comps/GitIgnoreEditor.vue";
import { useSpcodeGitLog, type LogFilter } from "@/composables/useSpcodeGitLog";
import { useSpcodeGitShow } from "@/composables/useSpcodeGitShow";
import { useSpcodeGitStats } from "@/composables/useSpcodeGitStats";
import {
  rangeForPreset,
  type GitStatsRange,
} from "@/composables/parseSpcodeGitStats";
import { useSpcodeNewFileLineCounts } from "@/composables/useSpcodeNewFileLineCounts";
import { classifyReason } from "@/composables/parseSpcodeGitWorkflow";
import { classifyWorktreeReason } from "@/composables/parseSpcodeWorktreeManagement";
import {
  useSpcodeGitBranches,
  type BranchDeleteParams,
  type BranchSwitchParams,
} from "@/composables/useSpcodeGitBranches";
import { useSpcodeGitMerge } from "@/composables/useSpcodeGitMerge";
import {
  classifyMergeReason,
  classifyCherryPickReason,
} from "@/composables/parseSpcodeGitMerge";
import { useSpcodeGitRemoteSync } from "@/composables/useSpcodeGitRemoteSync";
import {
  classifyPullReason,
  classifyPushReason,
  classifyRemoteReason,
  type SpcodeRemote,
} from "@/composables/parseSpcodeGitRemoteSync";
import GitPullDialog from "@/components/chat/GitPullDialog.vue";
import GitPushDialog from "@/components/chat/GitPushDialog.vue";
import GitRemoteUrlDialog from "@/components/chat/GitRemoteUrlDialog.vue";
import GitStashDialog from "@/components/chat/GitStashDialog.vue";
import { useSpcodeGitStash } from "@/composables/useSpcodeGitStash";
import { classifyStashReason } from "@/composables/parseSpcodeGitStash";
import { useSpcodeGitConflict } from "@/composables/useSpcodeGitConflict";
import GitMergeDialog from "@/components/chat/GitMergeDialog.vue";
import GitCherryPickDialog from "@/components/chat/GitCherryPickDialog.vue";
import GitSquashDialog from "@/components/chat/GitSquashDialog.vue";
import GitChangelogDialog from "@/components/chat/GitChangelogDialog.vue";
import { useSpcodeGitSquash } from "@/composables/useSpcodeGitSquash";
import type {
  SquashPayloadCommit,
  RefPickerItem,
} from "@/components/chat/message_list_comps/GitLogView.vue";
import GitConflictPanel from "@/components/chat/GitConflictPanel.vue";
import { classifyConflictReason } from "@/composables/parseSpcodeGitConflict";
import { pluginExtensionApi } from "@/api/v1";
import { useModuleI18n } from "@/i18n/composables";
import GitDiffBodyContent from "@/components/chat/message_list_comps/GitDiffBodyContent.vue";
import FileBrowserView from "@/components/chat/message_list_comps/FileBrowserView.vue";
import { useRecentFiles } from "@/composables/useRecentFiles";
import GitCommitBar from "@/components/chat/message_list_comps/GitCommitBar.vue";
import GitCommitDialog from "@/components/chat/message_list_comps/GitCommitDialog.vue";
import GitCommitAmendDialog from "@/components/chat/message_list_comps/GitCommitAmendDialog.vue";
import WorktreeCreateDialog from "@/components/chat/message_list_comps/WorktreeCreateDialog.vue";
import LockReasonDialogBody from "@/components/chat/message_list_comps/LockReasonDialogBody.vue";
import GitLogView from "@/components/chat/message_list_comps/GitLogView.vue";
import DocumentManager from "@/components/chat/message_list_comps/DocumentManager.vue";
import TerminalView from "@/components/chat/TerminalView.vue";
import BranchSwitchConfirmDialog from "@/components/chat/message_list_comps/BranchSwitchConfirmDialog.vue";
import BranchDeleteConfirmDialog from "@/components/chat/message_list_comps/BranchDeleteConfirmDialog.vue";
const { tm } = useModuleI18n("features/chat");

// Scroll container for the body (Files / Diff / History). Provided
// to descendant DiffPreview instances so their comment dock can
// <Teleport> out of the deeply-nested GitDiffFileItem subtree and
// become a direct child of the scroller. A `position: sticky`
// element only sticks when its scroll ancestor equals its
// containing block with no layout ancestors in between; the deep
// nesting inside the diff list breaks that, so an in-place sticky
// dock rendered at the document tail and the editor's focus-scroll
// jumped the viewport there. Teleporting to the scroller restores
// the textbook sticky setup (see DiffPreview sidebarScrollContainer).
const sidebarBodyRef = ref<HTMLElement | null>(null);
provide("diffScrollContainer", sidebarBodyRef);

// ── localStorage persistence (spec 2026-06-20 §5.1 + §6) ────────────
// Persists 4 view-state keys across page reloads. Values are loaded
// once at component creation and saved on every change (most are
// flush:"post" watchers; currentPath uses a 300ms debounce per spec
// §2 decision #9 to avoid thrashing during fast navigation).
//
// Validation rules: invalid persisted values are silently replaced
// with the spec-defined default. We never throw — localStorage may
// be disabled (private browsing) or the value may have been written
// by an older app version.
const STORAGE_KEYS = {
  viewMode: "astrbot.spcode.gitDiffSidebar.viewMode",
  fileBrowserCurrentPath:
    "astrbot.spcode.gitDiffSidebar.fileBrowserCurrentPath",
  selectedWorktree: "astrbot.spcode.gitDiffSidebar.selectedWorktree",
  selectedScope: "astrbot.spcode.gitDiffSidebar.selectedScope",
  // 2026-07-22 worktree-tabs-collapse: persists whether the worktree
  // tab bar is expanded (show all) or collapsed (show only the active
  // worktree). Default collapsed keeps the sidebar compact when many
  // worktrees exist (the long row was the reported UX problem).
  worktreeTabsExpanded: "astrbot.spcode.gitDiffSidebar.worktreeTabsExpanded",
  // 2026-07-18 git-stats heatmap: stats-panel collapsed state.
  gitStatsOpen: "astrbot.spcode.gitDiffSidebar.gitStatsOpen",
  gitStatsRange: "astrbot.spcode.gitDiffSidebar.gitStatsRange",
  // 2026-07-19 hot-files picker: user-controllable "Top N" cap
  // (5..50). Owned by the sidebar so the change can re-fetch with
  // a new ETag bucket via useSpcodeGitStats.refresh({topFiles}).
  gitStatsTopFilesLimit: "astrbot.spcode.gitDiffSidebar.gitStatsTopFilesLimit",
} as const;

function safeGetItem(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function safeSetItem(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* no-op */
  }
}

function loadViewMode(): "files" | "diff" | "history" | "docs" | "terminal" {
  const v = safeGetItem(STORAGE_KEYS.viewMode);
  // Spec §2 决策 #10:History 是第 3 个 viewMode,持久化时同样支持。
  // 2026-07-11 document-manager:docs 是第 4 个 viewMode。
  // 2026-09-01 terminal:终端是第 5 个 viewMode。
  if (
    v === "files" ||
    v === "diff" ||
    v === "history" ||
    v === "docs" ||
    v === "terminal"
  ) {
    return v;
  }
  return "files";
}
function loadFileBrowserCurrentPath(): string {
  return safeGetItem(STORAGE_KEYS.fileBrowserCurrentPath) ?? "";
}
function loadSelectedScope(): GitDiffScope {
  const v = safeGetItem(STORAGE_KEYS.selectedScope);
  if (v === "unstaged" || v === "staged" || v === "all") return v;
  return DEFAULT_SCOPE;
}

// 2026-07-18 git-stats heatmap: unlike the (now FileBrowserView-owned)
// search panel, the stats panel defaults to EXPANDED (only the literal
// "false" collapses) — the heatmap is the primary entry point of the
// History view.
function loadGitStatsOpen(): boolean {
  return safeGetItem(STORAGE_KEYS.gitStatsOpen) !== "false";
}

// 2026-07-19 hot-files picker: restore the user-controllable "Top N"
// cap. Anything outside [5, 50] falls back to the 10 default so a
// stale or hand-edited localStorage value can never put the panel
// in a state the backend would reject.
const HOT_FILES_MIN = 5;
const HOT_FILES_MAX = 50;
const HOT_FILES_DEFAULT = 10;
function loadGitStatsTopFilesLimit(): number {
  const v = safeGetItem(STORAGE_KEYS.gitStatsTopFilesLimit);
  if (v === null) return HOT_FILES_DEFAULT;
  const n = Number(v);
  if (!Number.isInteger(n)) return HOT_FILES_DEFAULT;
  if (n < HOT_FILES_MIN || n > HOT_FILES_MAX) return HOT_FILES_DEFAULT;
  return n;
}

// 2026-07-18 git-stats heatmap: restore the saved range window from
// localStorage. Anything we don't recognise falls back to the prior
// 6-month default (matching the launch behaviour of the feature).
const VALID_PRESETS = new Set(["1w", "1mo", "3mo", "6mo", "1y"]);
function loadGitStatsRange(): GitStatsRange {
  const raw = safeGetItem(STORAGE_KEYS.gitStatsRange);
  if (!raw) return { kind: "preset", preset: "6mo" };
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") throw 0;
    const obj = parsed as {
      kind?: unknown;
      preset?: unknown;
      since?: unknown;
      until?: unknown;
    };
    if (
      obj.kind === "preset" &&
      typeof obj.preset === "string" &&
      VALID_PRESETS.has(obj.preset)
    ) {
      return {
        kind: "preset",
        preset: obj.preset as GitStatsRange["kind" extends "preset"
          ? "preset"
          : never],
      } as GitStatsRange;
    }
    if (
      obj.kind === "custom" &&
      typeof obj.since === "string" &&
      typeof obj.until === "string" &&
      /^\d{4}-\d{2}-\d{2}$/.test(obj.since) &&
      /^\d{4}-\d{2}-\d{2}$/.test(obj.until) &&
      obj.since <= obj.until
    ) {
      return { kind: "custom", since: obj.since, until: obj.until };
    }
  } catch {
    /* fall through */
  }
  return { kind: "preset", preset: "6mo" };
}

// Debounced writer for currentPath (spec §5.1 lines 1273-1280).
// Avoids localStorage thrashing when the user clicks through
// directories rapidly.
let persistCurrentPathTimer: ReturnType<typeof setTimeout> | null = null;
function persistCurrentPath(path: string): void {
  if (persistCurrentPathTimer) clearTimeout(persistCurrentPathTimer);
  persistCurrentPathTimer = setTimeout(() => {
    safeSetItem(STORAGE_KEYS.fileBrowserCurrentPath, path);
  }, 300);
}

// Cross-root validator (spec §5.1 lines 1300-1310). Returns the input
// if it's inside the root, else the root. Used to reset stale paths
// after project / worktree switches.
function validateCurrentPath(
  persisted: string | null,
  root: string | null,
): string {
  if (!root) return "";
  if (!persisted) return root;
  const normPersisted = persisted.replace(/\\/g, "/");
  const normRoot = root.replace(/\\/g, "/").replace(/\/$/, "");
  if (normPersisted === normRoot || normPersisted.startsWith(normRoot + "/")) {
    return persisted;
  }
  return root;
}
const props = defineProps<{
  modelValue: boolean;
  isDark?: boolean;
}>();
const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (e: "fullscreen-change", v: boolean): void;
}>();

// ── Scope switcher (spec 2026-06-20 §3) ────────────────────────────
// `selectedScope` is the user's currently-displayed scope; it is
// forwarded to useSpcodeGitDiff which auto-refreshes on changes.
// `pendingScope` tracks the scope the user just clicked but whose
// response has not yet arrived; it powers the inline spinner on the
// active pill and the disabled state on the other two.
const selectedScope = ref<GitDiffScope>(DEFAULT_SCOPE);
const pendingScope = ref<GitDiffScope | null>(null);

interface ScopeOption {
  value: GitDiffScope;
  icon: string;
  labelKey: string;
}

// Single source of truth for the three pills: the v-for in the
// template iterates this list, so adding/removing a scope is a
// one-line change here. ReadonlyArray makes the contract explicit.
const SCOPE_OPTIONS: ReadonlyArray<ScopeOption> = [
  {
    value: "unstaged",
    icon: "mdi-pencil-outline",
    labelKey: "spcodeProjectLoad.diffSidebar.scope.unstaged",
  },
  {
    value: "staged",
    icon: "mdi-check-circle-outline",
    labelKey: "spcodeProjectLoad.diffSidebar.scope.staged",
  },
  {
    value: "all",
    icon: "mdi-format-list-bulleted",
    labelKey: "spcodeProjectLoad.diffSidebar.scope.all",
  },
];

// ── Worktree switcher state (spec 2026-06-18 §3.4) ─────────────────
// selectedWorktree is the path of the currently-displayed worktree.
// null = use primary (main) worktree. This ref is passed to
// useSpcodeGitDiff which auto-refreshes on changes.
const selectedWorktree = ref<string | null>(null);
// 2026-07-22 worktree-tabs-collapse: when many worktrees exist the
// full row of tab pills overflows the sidebar and pushes the
// actual content down. Collapsing hides all-but-the-active tab to
// keep the row single-line. Hydrated from localStorage.
// 2026-07-22 (rev): default flipped to EXPANDED (true) — the
// original opt-in collapsed default assumed the "many worktrees"
// case was the norm, but most projects in practice have 1-3
// worktrees where the full row fits comfortably and collapsing
// adds a click. Existing users who already chose collapsed keep
// their stored preference; only fresh sessions (no stored value)
// pick up the new default. The hydration returns the stored value
// verbatim when present, else `true`.
const _storedWorktreeTabsExpanded = safeGetItem(
  STORAGE_KEYS.worktreeTabsExpanded,
);
const worktreeTabsExpanded = ref<boolean>(
  _storedWorktreeTabsExpanded === null
    ? true
    : _storedWorktreeTabsExpanded === "true",
);

// ── Recent files (spec 2026-07-20 recent-files §3, §4) ──────────────
// One bucket per worktree, persisted to localStorage by
// useRecentFiles. The composable is instantiated further down,
// right after `currentRoot` is declared, so it can take
// `currentRoot` (the effective root = selectedWorktree ??
// mainWorktreePath ?? projectRoot) as its bucket key — that way
// recordOpen still works on the main worktree even before the user
// has explicitly switched worktrees. See useRecentFiles invocation
// and the [fileBrowserPreviewPath, currentRoot] watcher near the
// bottom of this <script setup>.
const showClearConfirm = ref(false);

// ── View-mode tab (spec 2026-06-20 §5.1 + §5.2) ─────────────────────
// "files" shows <FileBrowserView>; "diff" shows <GitDiffBodyContent>;
// "history" shows <GitLogView> (spec 2026-06-24 §2 决策 #10)。
// "docs" shows <DocumentManager> (spec 2026-07-11 document-manager §2 #1)。
// Default: "files" per spec §2 decision #10 (the more general view;
// first-time users likely want to "see what's in the project").
const viewMode = ref<"files" | "diff" | "history" | "docs" | "terminal">(
  loadViewMode(),
);
const fileBrowserCurrentPath = ref<string>(loadFileBrowserCurrentPath());
// ── Fullscreen state ──────────────────────────────────────────────
// Non-persisted. globalFullscreen expands the entire sidebar to the
// viewport and survives page switches (e.g. files → diff).
// 2026-07-17: the innerFullscreen mode (preview-only Teleport inside
// FileBrowserFilePreview) was removed — its role duplicated this
// sidebar-wide fullscreen button.
const globalFullscreen = ref(false);

function toggleGlobalFullscreen(): void {
  globalFullscreen.value = !globalFullscreen.value;
}

function onFullscreenKeyDown(e: KeyboardEvent): void {
  if (e.key !== "Escape" || !globalFullscreen.value) return;
  globalFullscreen.value = false;
}

watch(
  globalFullscreen,
  (v) => {
    emit("fullscreen-change", v);
    document.body.style.overflow = v ? "hidden" : "";
  },
  { immediate: true },
);
// fileBrowserPreviewPath is the file (if any) currently shown in the
// right pane. It is intentionally NOT persisted: when the user reloads
// the page we want the directory listing (persisted via currentPath),
// not an auto-reopened file. Cleared on worktree / project switches,
// breadcrumb clicks, and any directory navigation.
const fileBrowserPreviewPath = ref<string | null>(null);

// 2026-07-02 sidebar-search: scroll target for the file preview, set
// by onFileOpen() when the user clicks a search result. 1-based line
// number, null = no scroll. The watch in FileBrowserCodeView
// re-fires the scroll when (scrollToLine, filePath, highlightedHtml)
// changes, so the scroll also runs correctly after the file content
// finishes loading. Cleared by onFileBrowserNavigate() so a manual
// tree click does not inherit a stale scroll target.
const fileSearchScrollToLine = ref<number | null>(null);

// 2026-07-18 git-stats heatmap: History-tab stats panel toggle,
// persisted (flush:"post" writer).
const statsOpen = ref<boolean>(loadGitStatsOpen());
watch(statsOpen, (v) => safeSetItem(STORAGE_KEYS.gitStatsOpen, String(v)), {
  flush: "post",
});
// 2026-07-18 git-stats heatmap: owned by the sidebar, persisted across
// reloads, and the source for the range prop passed to GitLogView.
const gitStatsRange = ref<GitStatsRange>(loadGitStatsRange());
watch(
  gitStatsRange,
  (v) => safeSetItem(STORAGE_KEYS.gitStatsRange, JSON.stringify(v)),
  { flush: "post" },
);
// 2026-07-19 hot-files picker: owned by the sidebar, persisted across
// reloads, and forwarded to GitLogView as a prop so the panel can
// emit the new value and we re-fetch with the matching ETag bucket.
const gitStatsTopFilesLimit = ref<number>(loadGitStatsTopFilesLimit());
watch(
  gitStatsTopFilesLimit,
  (v) => safeSetItem(STORAGE_KEYS.gitStatsTopFilesLimit, String(v)),
  { flush: "post" },
);

// Hydrate selectedScope from localStorage (validated; fall back to default).
const _persistedScope = loadSelectedScope();
if (_persistedScope !== DEFAULT_SCOPE) selectedScope.value = _persistedScope;

// Hydrate selectedWorktree from localStorage. We store the literal
// string "null" for null (workaround: localStorage.getItem returns
// null for both "missing" and "stored null"). Validation against the
// worktree list happens in the worktree-list watcher below.
const _persistedWorktree = safeGetItem(STORAGE_KEYS.selectedWorktree);
if (_persistedWorktree !== null && _persistedWorktree !== "null") {
  selectedWorktree.value = _persistedWorktree;
}

const worktreesComposable = useSpcodeWorktrees();
const branchesComposable = useSpcodeGitBranches();
const branchList = computed(() => {
  const s = branchesComposable.state.value;
  return s.kind === "ok" ? s.snapshot.branches : [];
});
const currentBranchName = computed(() => {
  const s = branchesComposable.state.value;
  if (s.kind !== "ok") return null;
  return s.snapshot.current;
});
// 2026-08-01 branch-picker (spec 2026-08-01-git-history-branch-picker
// §3): picker items for the History view's branch filter — current
// branch first, then locals, then remotes. Reuses the existing
// branchList computed; empty while branches are not loaded.
// 2026-09-08 (spec 2026-09-08-git-log-tags-and-filters §2.3): Ref 选择器
// 升级为分组条目 —— 当前分支 / 本地分支 / 远程分支 / 标签。
// 2026-09-09 (elecvoid243) split-ref-filter: 一拆为二 —— 分支选择器
// （HEAD + 当前 / 本地 / 远程分支）与标签联想条目（仅标签分组）。标签
// 不再属于「分支」，SHA / 标签统一走 GitLogView 的 rev 过滤。
// 顶部的 HEAD 条目让用户在切走之后仍能回到「跟随当前检出提交」的默认
// 值（重设筛选条件之外的显式入口，游离 HEAD 时尤其有用）。
const branchPickerItems = computed<RefPickerItem[]>(() => {
  const items: RefPickerItem[] = [{ title: "HEAD", value: "HEAD" }];
  const groups: Array<[string, string[]]> = [
    [
      "filter.group.current",
      branchList.value.filter((b) => b.current && !b.remote).map((b) => b.name),
    ],
    [
      "filter.group.local",
      branchList.value
        .filter((b) => !b.current && !b.remote)
        .map((b) => b.name),
    ],
    [
      "filter.group.remote",
      branchList.value.filter((b) => b.remote).map((b) => b.name),
    ],
  ];
  for (const [labelKey, names] of groups) {
    if (names.length === 0) continue;
    items.push({
      title: tm(`spcodeProjectLoad.diffSidebar.gitWorkflow.history.${labelKey}`),
      type: "subheader",
    });
    for (const name of names) items.push({ title: name, value: name });
  }
  return items;
});
// 2026-09-09 (elecvoid243) split-ref-filter: 「提交 / 标签」输入框的联想
// 条目（标签分组）。为空时该框退化为纯 SHA 输入。
const tagPickerItems = computed<RefPickerItem[]>(() => {
  const s = branchesComposable.state.value;
  const tags = s.kind === "ok" ? s.snapshot.tags : [];
  if (tags.length === 0) return [];
  return [
    {
      title: tm(
        "spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.group.tags",
      ),
      type: "subheader",
    },
    ...tags.map((t) => ({ title: t.name, value: t.name })),
  ];
});
// Parse git's upstream-track string ("ahead 3" / "behind 1, ahead 2")
// into structured counts. Returns null when the field is empty (no
// upstream configured) or malformed — the UI then simply hides the
// ahead/behind badge.
function parseUpstreamTrack(
  track: string,
): { ahead: number; behind: number } | null {
  const trimmed = track.trim();
  if (!trimmed) return null;
  let ahead = 0;
  let behind = 0;
  let matched = false;
  const aheadMatch = /ahead\s+(\d+)/i.exec(trimmed);
  if (aheadMatch) {
    ahead = Number.parseInt(aheadMatch[1], 10);
    matched = true;
  }
  const behindMatch = /behind\s+(\d+)/i.exec(trimmed);
  if (behindMatch) {
    behind = Number.parseInt(behindMatch[1], 10);
    matched = true;
  }
  return matched ? { ahead, behind } : null;
}
const currentBranchTracking = computed(() => {
  const s = branchesComposable.state.value;
  if (s.kind !== "ok") return null;
  const current = s.snapshot.branches.find((b) => b.current);
  if (!current) return null;
  return parseUpstreamTrack(current.upstreamTrack);
});
// 2026-08-13 git-push dialog: data sources for the confirmation dialog.
// Remote names are derived from remote branch refs (origin/xxx → origin)
// plus the current branch's upstream, so the dialog can prefill even
// before any remote ref is fetched.
const currentBranch = computed(() => {
  const s = branchesComposable.state.value;
  if (s.kind !== "ok") return null;
  return s.snapshot.branches.find((b) => b.current) ?? null;
});
const currentBranchUpstream = computed(
  () => currentBranch.value?.upstream || null,
);
const remoteNames = computed<string[]>(() => {
  const names = new Set<string>();
  for (const b of branchList.value) {
    if (b.remote) {
      const i = b.name.indexOf("/");
      if (i > 0) names.add(b.name.slice(0, i));
    }
  }
  const up = currentBranchUpstream.value;
  if (up) {
    const i = up.indexOf("/");
    if (i > 0) names.add(up.slice(0, i));
  }
  // 2026-08-16: 并入 git-branches 返回的已配置远端 (git remote)。
  // 新 `remote add` 的远端在 fetch 前没有 refs/remotes/* ref,
  // 仅靠远端分支推导会漏掉它 → 推送对话框远端列表不更新。
  const s = branchesComposable.state.value;
  if (s.kind === "ok") {
    for (const r of s.snapshot.remotes) names.add(r);
  }
  return [...names].sort();
});
const localBranchNames = computed<string[]>(() =>
  branchList.value.filter((b) => !b.remote).map((b) => b.name),
);
// Spec 2026-07-16: "is this a Git repo?" probe + init mutation. Used by
// the GitRepoInitPrompt slot in the template to surface a one-click
// `git init` flow when the loaded directory has no .git/.
const gitRepoProbe = useSpcodeGitRepoProbe();
const worktreeList = computed(() => {
  const s = worktreesComposable.state.value;
  if (s.kind !== "ok") return [];
  return s.snapshot.worktrees;
});
const hasMultipleWorktrees = computed(() => worktreeList.value.length > 0);
// Path of the main worktree (used as the "active" comparison when
// selectedWorktree is null). Lets the main tab stay highlighted.
const mainWorktreePath = computed(
  () => worktreeList.value.find((w) => w.isMain)?.path ?? null,
);
// 2026-09-09 (elecvoid243) head-sha: 当前工作树的 HEAD 提交 SHA —— 取自
// `git worktree list --porcelain` 的 head_sha（git 权威值），而不是历史
// 列表的第一行（筛选生效后第一行只是「最新一条命中结果」）。
// amend / reset / squash 的可见性判定全部改用这个值（见 GitLogView 的
// headSha prop）。null = worktree 列表尚未加载 / 请求失败。
const currentHeadSha = computed<string | null>(() => {
  const target = selectedWorktree.value ?? mainWorktreePath.value;
  if (!target) return null;
  return worktreeList.value.find((w) => w.path === target)?.headSha || null;
});
// 2026-07-22 worktree-tabs-collapse: the row of tab pills shown to
// the user. When worktreeTabsExpanded is false, hide all but the
// active worktree so a long row doesn't dominate the sidebar;
// the toggle button next to the "工作树" label brings the full row
// back. Falls back to the full list if (somehow) the active path
// isn't in `worktreeList` — the watcher above normalizes this so
// it's a belt-and-braces guard for transient states during a switch.
const visibleWorktreeList = computed(() => {
  if (worktreeTabsExpanded.value) return worktreeList.value;
  const active = selectedWorktree.value ?? mainWorktreePath.value;
  const filtered = worktreeList.value.filter((w) => w.path === active);
  return filtered.length > 0 ? filtered : worktreeList.value;
});
// Path of the loaded project's root directory (from
// /spcode/project-status, independent of git). Used as a fallback
// root for the file browser when the project is NOT a git project
// (worktree list is empty → no main worktree path). Without this
// fallback, fileBrowserCurrentPath would collapse to "" and
// useSpcodeFileBrowser's watcher would skip the fetch
// (`if (!path) return`), leaving the Files view empty.
const projectRoot = computed(() => spcodeStatus.status.value.directory);

// Persist viewMode / selectedScope / selectedWorktree on every change.
// fileBrowserCurrentPath uses persistCurrentPath (300ms debounce).
watch(viewMode, (v) => safeSetItem(STORAGE_KEYS.viewMode, v), {
  flush: "post",
});
watch(selectedScope, (v) => safeSetItem(STORAGE_KEYS.selectedScope, v), {
  flush: "post",
});
watch(
  selectedWorktree,
  (v) => safeSetItem(STORAGE_KEYS.selectedWorktree, v === null ? "null" : v),
  { flush: "post" },
);
// 2026-07-22 worktree-tabs-collapse: persist expanded/collapsed state
// (default collapsed; hydration below reads the stored string at setup).
watch(
  worktreeTabsExpanded,
  (v) => safeSetItem(STORAGE_KEYS.worktreeTabsExpanded, v ? "true" : "false"),
  { flush: "post" },
);

// Closed-over by the worktree-list watcher below. Tracks the previous
// worktree path set so we can detect *topology* changes (paths
// added/removed) and skip the validation sweep on no-op polling
// ticks. Declared before the watcher so the closure binding is
// unambiguous; Vue's `<script setup>` does not hoist `let`.
let prevWorktreePaths: ReadonlySet<string> | null = null;

// Singleton spcode project status. Declared HERE — before the
// immediate worktree-list watcher below — because that watcher's
// callback reads `projectRoot` (→ `spcodeStatus`) on its immediate
// tick. Instantiating it further down (its natural home next to the
// other composables) puts the binding in TDZ during the immediate
// tick; production swallows the resulting ReferenceError with a bare
// console.error and silently skips the watcher's non-git hydration
// branch on every mount.
const spcodeStatus = useSpcodeProjectStatus();

// When the worktree list first loads, validate the persisted worktree
// AND cross-validate the persisted currentPath against the new root.
// This is the only place where fileBrowserCurrentPath is overwritten
// during initial hydration; thereafter the user is in control.
// Also clear any preview path: a preview from a different worktree
// would be invalid in the new context.
watch(
  () => worktreesComposable.state.value,
  (s) => {
    if (s.kind !== "ok") {
      // Non-git project (or the worktree fetch failed): there is no
      // worktree list to validate against, so the hydration logic
      // below — which is gated on `kind === "ok"` — never runs. Without
      // this branch, fileBrowserCurrentPath stays "" and
      // useSpcodeFileBrowser's `if (!path) return` short-circuits EVERY
      // fetch (the initial load AND the header refresh button), leaving
      // the Files view permanently empty until the project is turned
      // into a Git repo. projectRoot comes from /spcode/project-status
      // (independent of git) and is guaranteed non-empty by the time
      // this fires: the worktree request that drives this watcher is
      // itself triggered by spcodeStatus.directory/umo becoming
      // available, so directory (hence projectRoot) is already set. The
      // non-empty guard keeps the `immediate` tick (state=idle,
      // projectRoot still "") a no-op. validateCurrentPath preserves a
      // valid persisted path and only adopts the root when the path is
      // empty or outside it.
      if (projectRoot.value) {
        const validated = validateCurrentPath(
          fileBrowserCurrentPath.value,
          projectRoot.value,
        );
        if (fileBrowserCurrentPath.value !== validated) {
          fileBrowserCurrentPath.value = validated;
        }
      }
      return;
    }
    const wtList = s.snapshot.worktrees;
    const newPaths = new Set(wtList.map((w) => w.path));

    // Topology-only change detection.
    //
    // Pre-polling, this watcher was event-driven: a worktree-list
    // mutation triggered a one-shot sanity sweep (reset stale
    // selectedWorktree, re-validate currentPath, clear stale
    // preview). The 30s polling added in 2026-06-25 turned it into
    // a periodic callback — `state.value` is replaced on every
    // tick, even when the response is byte-for-byte identical.
    // Running the full sweep on every tick had a concrete user-
    // visible cost: `fileBrowserPreviewPath.value = null` (below)
    // unmounted <FileBrowserFilePreview> and reset the right pane
    // to the "select from left" hint, so the user lost their
    // scroll position mid-read.
    //
    // We only care about the WORKTREE SET (paths added/removed) —
    // a worktree's branch / head_sha / locked / prunable flipping
    // does not invalidate which files are accessible in the
    // current preview, so they must not trigger a reset. We track
    // the previous path set in a closure and skip the sweep when
    // the set is unchanged. The initial run (prevWorktreePaths ===
    // null) always runs, preserving the original hydration behavior
    // (validate persisted selectedWorktree / currentPath against
    // the freshly-loaded list).
    // Local alias so the type-narrowed `prev` is used in the every()
    // callback without a non-null assertion. After the `=== null`
    // check, `prev` is guaranteed non-null for the size/every
    // comparisons (TypeScript can't see this through the
    // short-circuit on its own).
    const prev = prevWorktreePaths;
    const topologyChanged =
      prev === null ||
      prev.size !== newPaths.size ||
      ![...newPaths].every((p) => prev.has(p));
    prevWorktreePaths = newPaths;
    if (!topologyChanged) return;

    // Validate selectedWorktree
    if (
      selectedWorktree.value &&
      !wtList.some((w) => w.path === selectedWorktree.value)
    ) {
      selectedWorktree.value = null;
    }
    // Validate currentPath against the (possibly new) root.
    // Fall back to projectRoot for non-git projects (no worktrees →
    // no main worktree path); otherwise fileBrowserCurrentPath would
    // collapse to "" and the file-browser fetch would be skipped.
    const root =
      selectedWorktree.value ??
      wtList.find((w) => w.isMain)?.path ??
      projectRoot.value;
    const validated = validateCurrentPath(fileBrowserCurrentPath.value, root);
    if (fileBrowserCurrentPath.value !== validated) {
      fileBrowserCurrentPath.value = validated;
    }
    // Preview path is transient and almost certainly invalid in a
    // worktree-set change context (the file it points to is in a
    // worktree that no longer exists in the new list). Clear it so
    // the right pane shows the "select from left" hint instead of a
    // stale file. Skipped on no-op polls by the topology guard above.
    if (fileBrowserPreviewPath.value !== null) {
      fileBrowserPreviewPath.value = null;
    }
  },
  { immediate: true },
);

// When selectedWorktree changes, reset currentPath to the new root.
// This fires for BOTH manual worktree switches and project switches
// (which reset selectedWorktree to null via the directory watcher
// further below). Per spec §5.1: "reset currentPath regardless of
// current viewMode" — we don't want stale paths leaking into a
// different worktree. The preview path is also cleared: a preview
// pointing into the old worktree is meaningless in the new one.
watch(selectedWorktree, (newVal) => {
  const root = newVal ?? mainWorktreePath.value;
  if (root && fileBrowserCurrentPath.value !== root) {
    fileBrowserCurrentPath.value = root;
    persistCurrentPath(root);
  }
  fileBrowserPreviewPath.value = null;
});

// Persist currentPath (debounced 300ms, spec §5.1 line 1357-1361).
// Empty path is skipped — we don't want to overwrite a valid persisted
// value with an empty string during the brief interval before the
// worktree-list watcher fires.
watch(fileBrowserCurrentPath, (newPath) => {
  if (newPath) persistCurrentPath(newPath);
});

// 2026-07-20 Recent Files §6.1: the watcher on [previewPath, currentRoot]
// is attached immediately after `currentRoot` is declared (further down),
// because Vue's analyser hoists variable usage but TypeScript does not.

const composable = useSpcodeGitDiff(selectedWorktree, selectedScope);
// git-status is fetched alongside git-diff ONLY for the unstaged view.
// The endpoint is complementary (branch / untracked / intent-to-add)
// and is not relevant to staged or all-scope views. Lifecycle
// (refresh + 10s polling + dispose) is wired in the modelValue / viewMode
// watcher and onBeforeUnmount below.
const gitStatus = useSpcodeGitStatus(selectedWorktree);
const expandedSet = ref<Set<string>>(new Set());

// ── Git workflow composables (spec 2026-06-24 §3.2) ──────────────
// All 4 live at the sidebar level so they can share stagedFiles state
// and call composable.refresh() after a write.
const gitStage = useSpcodeGitStage();
const gitUnstage = useSpcodeGitUnstage();
const gitCommit = useSpcodeGitCommit();
const gitCommitAmend = useSpcodeGitCommitAmend();
// 2026-09-08 (elecvoid243): the second argument gates the worktree-switch
// refetch on the History tab being visible — switching worktrees from
// Files / Diff / Docs must not fire a git-log request. The composable
// still resets its filter on the switch, so entering History afterwards
// (viewMode watcher below) fetches the default HEAD + 20 view.
const gitLog = useSpcodeGitLog(
  selectedWorktree,
  computed(() => viewMode.value === "history"),
);
// 2026-07-17 git-revert: History-view per-commit revert. The sidebar
// owns the confirm dialog + the write call (mirroring stage/commit).
const gitRevert = useSpcodeGitRevert();
// 2026-09-09 git-reset: History-view per-commit branch reset
// (SourceTree-style "Reset current branch to this commit").
const gitReset = useSpcodeGitReset();
// 2026-08-03 git-squash: History-view multi-commit squash. The
// sidebar owns the dialog + the write call (mirroring revert).
const gitSquash = useSpcodeGitSquash();
// 2026-08-01 git-merge / git-conflict: instantiated with the other
// composables so the polling watchers (immediate:true) below can
// safely reference them. Handler logic lives in the branch block.
const gitMerge = useSpcodeGitMerge();
const gitRemoteSync = useSpcodeGitRemoteSync();
// 2026-08-21 git-stash: stash dialog owns list + push; worktree is read
// from selectedWorktree at call time (dialog-driven fetch, no polling).
const gitStash = useSpcodeGitStash(selectedWorktree);
const gitConflict = useSpcodeGitConflict(selectedWorktree);

// ── .gitignore editor overlay (2026-07-17, latency rework 2026-07-18) ─
// Spec: docs/superpowers/specs/2026-07-17-gitignore-editor-design.md
// The sidebar owns load/save orchestration; GitIgnoreEditor is a
// purely presentational overlay (initial-content in, save(out) out).
// 2026-07-18: the editing buffer lives INSIDE the editor — per
// keystroke state no longer reaches the sidebar, so this 4600-line
// component is no longer in the typing render path.
const gitIgnoreEditorOpen = ref(false);
// On-disk content loaded by loadGitIgnore (the dirty baseline passed
// to the editor as `initial-content`). Persists across the editing
// session; updated only on a fresh load (open / retry).
const gitIgnoreLoadedContent = ref("");
const gitIgnoreIsNewFile = ref(false);
const gitIgnoreLoadError = ref<string | null>(null);
const gitIgnoreSaveError = ref<string | null>(null);
const gitIgnoreFileWrite = useSpcodeFileWrite(selectedWorktree);
// Repo-root .gitignore as an absolute path for the read endpoint
// (file-browser takes absolute paths; trailing slashes stripped).
const gitIgnoreAbsPath = computed(
  () => `${(currentRoot.value ?? "").replace(/\/+$/, "")}/.gitignore`,
);
const GITIGNORE_I18N_PREFIX =
  "spcodeProjectLoad.diffSidebar.gitWorkflow.gitignore";

async function loadGitIgnore(): Promise<void> {
  gitIgnoreLoadError.value = null;
  try {
    const resp = await pluginExtensionApi.get<unknown>("spcode/file-browser", {
      params: { path: gitIgnoreAbsPath.value },
    });
    const data = (
      resp.data as {
        data?: {
          type?: string | null;
          content?: unknown;
          reason?: string | null;
        };
      }
    )?.data;
    if (data?.type === "file" && typeof data.content === "string") {
      gitIgnoreLoadedContent.value = data.content;
      gitIgnoreIsNewFile.value = false;
      return;
    }
    // type === null + reason path_not_found → the repo simply has no
    // .gitignore yet: empty baseline + "will be created" hint.
    if (data?.reason === "path_not_found") {
      gitIgnoreLoadedContent.value = "";
      gitIgnoreIsNewFile.value = true;
      return;
    }
    gitIgnoreLoadError.value = tm(`${GITIGNORE_I18N_PREFIX}.loadError`, {
      reason: data?.reason ?? "unknown",
    });
  } catch (err) {
    const anyErr = err as { code?: string; message?: string };
    gitIgnoreLoadError.value = tm(`${GITIGNORE_I18N_PREFIX}.loadError`, {
      reason:
        anyErr.code === "ERR_NETWORK" || /network/i.test(anyErr.message ?? "")
          ? "network"
          : "unknown",
    });
  }
}

function onOpenGitIgnoreEditor(): void {
  gitIgnoreSaveError.value = null;
  gitIgnoreEditorOpen.value = true;
  void loadGitIgnore();
}

function onGitIgnoreCancel(): void {
  gitIgnoreEditorOpen.value = false;
}

async function onGitIgnoreSave(content: string): Promise<void> {
  if (gitIgnoreFileWrite.isSaving.value) return;
  gitIgnoreSaveError.value = null;
  const r = await gitIgnoreFileWrite.save({
    // Repo-relative path — the backend resolves it against the
    // worktree passed by the composable.
    path: ".gitignore",
    content,
  });
  if (r.ok) {
    gitIgnoreEditorOpen.value = false;
    showSnackbar(tm(`${GITIGNORE_I18N_PREFIX}.saveSuccess`), "success");
    // .gitignore changes which untracked files appear — refresh the
    // status + diff views in parallel.
    void Promise.all([gitStatus.refresh(), composable.refresh()]);
    return;
  }
  if (r.reason === "aborted") return;
  gitIgnoreSaveError.value = tm(`${GITIGNORE_I18N_PREFIX}.saveError`, {
    reason: r.reason,
  });
}

// The buffer belongs to the old worktree after a switch — close.
watch(selectedWorktree, () => {
  gitIgnoreEditorOpen.value = false;
});
// git-show: per-commit file list (spec 2026-06-25). One composable
// instance shared by every expanded commit in GitLogView; the
// composable maintains a per-SHA cache internally, so re-expanding
// a previously seen commit is an ETag-validated no-op.
const gitShow = useSpcodeGitShow(selectedWorktree);

// 2026-07-18 git-stats heatmap: whole-repo change stats for the
// History tab's GitStatsPanel. Single snapshot per umo|worktree
// (ETag-validated); refresh is lazy — see the dedicated watcher next
// to the polling watcher below.
const gitStats = useSpcodeGitStats(selectedWorktree);

// Spec §3.4 决策 #22/#23:切 worktree 保留,切 project / unload 清空。
const stagedFiles = ref<Set<string>>(new Set<string>());

// 2026-07-09: file-level git history bridge.
//
// The backend already supports `?path=<file>` on GET /spcode/git-log
// (see plugins/astrbot_plugin_spcode_toolkit/tools/webapi/git_log.py
// _qget("path") + `git log -- <path>`), and the frontend composable
// `useSpcodeGitLog` already threads `path` through the request and
// the ETag key. The only missing piece was a UI affordance: the
// file-preview pane has no way to say "show me the history of THIS
// file" without the user manually retyping the path in the History
// tab's filter form. We provide this setter so descendants (today:
// <FileBrowserFilePreview>'s "history" button; tomorrow: maybe a
// right-click menu) can switch the sidebar to the History tab and
// pre-fill the path filter in one call.
//
// We spread the current filter so the user's prior ref/author/since/
// until/n selections are preserved — only the path changes. View
// mode persistence (localStorage) happens automatically via the
// existing `watch(viewMode, ...)` flush:"post" below.
function setLogPathFilter(path: string): void {
  if (!path) return;
  viewMode.value = "history";
  // Preserve the user's current filter shape (ref / author / since
  // / until / n) and only override path. The composable's refresh()
  // replaces (not merges) the filter, so we have to spread here.
  void gitLog.refresh({ ...gitLog.filter.value, path });
}
// String inject key (the sidebar ↔ file-preview pair is the only
// consumer). Using a string (not Symbol) keeps the import surface in
// <FileBrowserFilePreview> trivial: `inject(SET_LOG_PATH_FILTER_KEY)`
// with the same string literal. A Symbol would force an extra shared
// module just to host the constant; not worth it for one consumer.
const SET_LOG_PATH_FILTER_KEY = "spcode:setLogPathFilter";
provide<(path: string) => void>(SET_LOG_PATH_FILTER_KEY, setLogPathFilter);

/**
 * 2026-07-15 history-sha-jump: jump from the per-file history
 * side panel (workspace + document manager) straight to a
 * specific commit in the global Git log. Mirrors `setLogPathFilter`
 * in shape but routes through a fresh inject key because the
 * semantic is different ("jump to THIS commit", not "filter the
 * log by THIS path"). The implementation:
 *
 *   1. Switches `viewMode` to `"history"` so the GitLogView
 *      mounts. Already-persisted via the existing
 *      `watch(viewMode, ...)` so the user lands back here next
 *      time.
 *   2. Pins the log's `rev` filter to the clicked SHA. `git log
 *      <sha>` returns that commit plus its ancestors, which is
 *      exactly what a deep-link to a single commit should show
 *      — the user gets the commit they clicked as the first
 *      row, with its history reachable via "load more". Drop the
 *      per-file panel's `path` filter too: the commit of interest
 *      is not guaranteed to touch the picked file in *this*
 *      revision (it might be a renaming or a non-related history).
 *      2026-09-09 split-ref-filter: the SHA goes into `rev` (the
 *      dedicated SHA / tag filter) instead of `ref`, so the branch
 *      context — and therefore the revert / amend / squash / reset
 *      ⇄ cherry-pick visibility — is preserved.
 *   3. Stashes the SHA in `focusedCommitSha`, which is forwarded
 *      to <GitLogView> as `focused-commit-sha`. GitLogView owns
 *      the highlight + expansion + scrollIntoView; see its
 *      watcher.
 */
const focusedCommitSha = ref<string | null>(null);
function focusCommit(sha: string): void {
  if (!sha) return;
  viewMode.value = "history";
  focusedCommitSha.value = sha;
  // Preserve the user's optional filter knobs (author / since /
  // until / n) but pin ref+path so the request is scoped to the
  // clicked commit. The explicit assignments after the spread
  // win over anything carried over from `gitLog.filter.value`.
  // 2026-09-09 split-ref-filter: 深链只改写 rev（git log 起点），保留
  // 分支上下文（ref），这样落在历史里的提交按钮可见性仍按当前浏览的
  // 分支判定，而不是因为一次 SHA 跳转就整体翻转。
  const { path: _path, rev: _rev, ...rest } = gitLog.filter.value;
  void gitLog.refresh({ ...rest, rev: sha, path: undefined });
}
// Dedicated key: this is a sibling affordance to setLogPathFilter
// and the two are not interchangeable. Both <FileBrowserView> and
// <DocumentManager> consume it via the same string literal.
const FOCUS_COMMIT_KEY = "spcode:focusCommit";
provide<(sha: string) => void>(FOCUS_COMMIT_KEY, focusCommit);

// 2026-07-17: the fullscreen provide/inject bridge (spcode:globalFullscreen
// / innerFullscreen / isAnyFullscreen / toggleInnerFullscreen) was
// removed together with the inner-fullscreen feature — no descendants
// consume these keys anymore.

// Spec §3.3.3:confirm dialog for "Stage all"。
const confirmStageAllOpen = ref(false);
const pendingStageAllCount = ref(0);
// 2026-07-17 git-revert: confirm dialog for the History view's
// per-commit revert action.
const revertDialogOpen = ref(false);
const pendingRevert = ref<{ sha: string; subject: string } | null>(null);
// 2026-09-09 git-reset: dialog state. resetMode defaults to "mixed"
// (SourceTree's default too); resetConfirmSha gates the confirm button —
// the user must type the target commit's 7-char SHA prefix for ALL
// modes (user decision: reset moves the branch pointer, so even
// soft/mixed deserve typed confirmation).
const resetDialogOpen = ref(false);
const pendingReset = ref<{ sha: string; subject: string } | null>(null);
const resetMode = ref<GitResetMode>("mixed");
const resetConfirmSha = ref("");
const resetConfirmReady = computed(
  () =>
    pendingReset.value !== null &&
    resetConfirmSha.value.trim().toLowerCase() ===
      pendingReset.value.sha.slice(0, 7).toLowerCase(),
);
// 2026-08-13 git-commit-amend: dialog state for editing the HEAD commit
// message. pendingAmend carries subject + body so the dialog can prefill.
const amendDialogOpen = ref(false);
const pendingAmend = ref<{
  sha: string;
  subject: string;
  body: string | null;
} | null>(null);
// 2026-08-03 git-squash: dialog state + the token that clears
// GitLogView's selection after a successful squash. pendingSquash
// carries the full SquashPayloadCommit (body included) so the
// dialog's AI-generate prompt can use the complete messages.
const squashDialogOpen = ref(false);
const pendingSquash = ref<SquashPayloadCommit[] | null>(null);
const squashResetToken = ref(0);
// 2026-08-09 changelog: dialog state + the token that clears
// GitLogView's changelog selection after the dialog closes (any
// path: save / cancel / scrim). Mirrors the squash pair above.
const changelogDialogOpen = ref(false);
const changelogCommits = ref<SquashPayloadCommit[]>([]);
const changelogResetToken = ref(0);

function onChangelogRequest(payload: { commits: SquashPayloadCommit[] }): void {
  changelogCommits.value = payload.commits;
  changelogDialogOpen.value = true;
}
function onChangelogSaved(path: string): void {
  showSnackbar(
    tm("spcodeProjectLoad.diffSidebar.changelog.saveSuccess", { path }),
    "success",
  );
}
function onChangelogDialogClose(): void {
  changelogResetToken.value++;
}
// Symmetric dialog for "Unstage all" — visible only from the staged
// scope where the bulk button's label flips to "取消全部暂存".
const confirmUnstageAllOpen = ref(false);
const pendingUnstageAllCount = ref(0);

// Spec §3.3.4:commit dialog state。
const commitDialogOpen = ref(false);
const commitLastError = ref<{ reason: string; stderr: string } | null>(null);

// ── file-restore (spec §3.2) ───────────────────────────────────────
// Composable instance lives at sidebar level so it can call
// composable.refresh() and reach the snackbar / dialog state.
const fileRestore = useSpcodeFileRestore();
const fileDiscardHunk = useSpcodeFileDiscardHunk();
const restoringFile = ref<string | null>(null);

// Spec §3.2 + restore-refresh-status: after a successful restore
// (single or batch) we must re-fetch BOTH git-diff and git-status in
// parallel. useSpcodeGitDiff does NOT touch git-status, and the
// polling interval is 10s — without an explicit refresh the user
// would see the row linger in the newFilePaths set / stagedCount
// counter for up to 10s after the file is gone. Mirrors the refresh
// pattern used by stage / unstage / commit (8 call sites all use
// the same `Promise.all([composable.refresh(), gitStatus.refresh()])`).
async function refreshDiffAndStatus(): Promise<void> {
  await Promise.all([composable.refresh(), gitStatus.refresh()]);
}

// Confirm dialog state.
const confirmDialogOpen = ref(false);
const confirmTargetPath = ref<string | null>(null);
// 2026-07-15 restore-edge-cases: when the target file is brand-new
// (untracked / from git-status newFile scope), restore = unlink the
// worktree copy, not "discard changes". Surface a different confirm
// message so the user isn't surprised by an implicit delete.
//
// Derived from `confirmTargetPath` + `newFilePaths` rather than a
// standalone `ref` so the message flag is *always* in lock-step with
// the path. A manually-set ref risked a brief render where the
// dialog opened with the new path but the *previous* file's isNew
// flag — perceived as "stacking" two dialogs.
const confirmTargetIsNew = computed<boolean>(
  () =>
    confirmTargetPath.value !== null &&
    newFilePaths.value.has(confirmTargetPath.value),
);

// Bulk-restore state. The handler iterates the captured path list
// one file at a time and aggregates the result into a single
// snackbar. We use a dedicated dialog (rather than reusing the
// single-file `confirmDialogOpen`) so the message can include the
// file count and so a single `false` button click cleanly aborts
// the whole batch.
const confirmRestorePathsOpen = ref(false);
const pendingRestorePaths = ref<string[]>([]);
const isBulkRestoring = ref(false);

// ── Recent files handlers (spec 2026-07-20 recent-files §6.1) ───────
// Spec:
//   - select  → open the file the user just clicked (no line jump)
//   - remove  → drop one entry from the per-worktree bucket
//   - clear   → already opened via the dialog; this confirms it
function onRecentSelect(payload: { path: string }): void {
  // Same navigation contract as onFileOpen but without a `line` field:
  // RecentFilesBlock emits `{ path }` only; we deliberately don't jump
  // to any anchor.
  fileBrowserPreviewPath.value = payload.path;
  const sep = payload.path.includes("\\") ? "\\" : "/";
  const lastSep = payload.path.lastIndexOf(sep);
  if (
    lastSep > 0 &&
    payload.path.slice(0, lastSep) !== fileBrowserCurrentPath.value
  ) {
    fileBrowserCurrentPath.value = payload.path.slice(0, lastSep);
  }
  fileSearchScrollToLine.value = null;
}

function onConfirmClear(): void {
  recentFiles.clear();
  showClearConfirm.value = false;
}

// ── Worktree management state (spec 2026-06-27 §2.4) ────────
const createDialogOpen = ref(false);
const removeDialogOpen = ref(false);

// ── Branch management state (spec 2026-07-21 §3.3) ─────────
const switchDialogOpen = ref(false);
const deleteDialogOpen = ref(false);
const switchTarget = ref<{ from: string | null; to: string } | null>(null);
const deleteTarget = ref<string | null>(null);
const switchDirtyCount = ref(0);
const isBranchSwitching = ref(false);
const isBranchDeleting = ref(false);
const isBranchCreating = ref(false);
const branchCreateExpanded = ref(false);
const branchCreateName = ref("");
const branchCreateStartPoint = ref("HEAD");
const branchCreateError = ref<string | null>(null);
const branchMenuOpen = ref(false);
const lockDialogOpen = ref(false);
const confirmUnlockOpen = ref(false);
const confirmUnlockPath = ref<string | null>(null);
const lockDialogTarget = ref<{ path: string; branch: string | null } | null>(
  null,
);
const removeDialogTarget = ref<{ path: string; branch: string | null } | null>(
  null,
);
const dirtyCount = ref<number | null>(null);
const isRemoving = ref(false);
const isLocking = ref(false);
const isUnlocking = ref(false);
const isCreating = ref(false);
const lastCreateError = ref<{ reason: string; stderr: string } | null>(null);
const removeForceChecked = ref(false);

// Context menu state (spec 2026-06-27 §2.3). Position is set from
// MouseEvent.clientX/Y in openContextMenu and applied as fixed
// `left/top` styles on the teleported wrapper (see contextMenuStyle).
// The menu DOM lives in <body> via <Teleport>, so `position: fixed`
// coordinates are viewport-relative — exactly what we want for a
// right-click at the cursor.
const contextMenu = ref<{
  open: boolean;
  x: number;
  y: number;
  wt: SpcodeGitWorktree | null;
}>({ open: false, x: 0, y: 0, wt: null });

// Wrapper element ref for outside-click detection. We attach a
// mousedown listener on document in onMounted; if the click target
// isn't inside this element, we close the menu.
const contextMenuEl = ref<HTMLElement | null>(null);

// Edge-clamp the menu so it stays inside the viewport. If the user
// right-clicks near the right/bottom edge, the menu would otherwise
// overflow. We measure after mount via nextTick and adjust left/top.
const contextMenuStyle = ref<Record<string, string>>({});
function positionContextMenu(): void {
  if (!contextMenu.value.open) return;
  const el = contextMenuEl.value;
  if (!el) return;
  const rect = el.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  let left = contextMenu.value.x;
  let top = contextMenu.value.y;
  // Flip horizontally if overflow right edge.
  if (left + rect.width > vw) {
    left = Math.max(4, vw - rect.width - 4);
  }
  // Flip vertically if overflow bottom edge.
  if (top + rect.height > vh) {
    top = Math.max(4, vh - rect.height - 4);
  }
  contextMenuStyle.value = {
    position: "fixed",
    left: `${left}px`,
    top: `${top}px`,
    zIndex: "2400",
  };
}

async function openContextMenu(
  e: MouseEvent,
  wt: SpcodeGitWorktree,
): Promise<void> {
  // Set state in two steps so Vue commits the DOM (the Teleport
  // target) before we measure it in positionContextMenu. Without the
  // first sync write + nextTick, contextMenuEl.value would be null
  // when we read getBoundingClientRect().
  contextMenu.value = { open: false, x: e.clientX, y: e.clientY, wt };
  await nextTick();
  contextMenu.value.open = true;
  await nextTick();
  positionContextMenu();
}

// Close helpers. We attach document-level listeners (see onMounted)
// and dispatch through these so menu items don't need to know about
// the ref shape.
function closeContextMenu(): void {
  contextMenu.value.open = false;
}
function closeContextMenuOnOutside(e: MouseEvent): void {
  if (!contextMenu.value.open) return;
  const el = contextMenuEl.value;
  if (el && e.target instanceof Node && !el.contains(e.target)) {
    closeContextMenu();
  }
}
function closeContextMenuOnEscape(e: KeyboardEvent): void {
  if (e.key === "Escape" && contextMenu.value.open) {
    closeContextMenu();
  }
}

// Snackbar state (success / warning / error). Spec §5.3 / §6.8:stderr
// 单独字段,withStderr reason 携带,模板里 <pre> 块渲染。
interface SnackbarState {
  show: boolean;
  message: string;
  color: "success" | "info" | "warning" | "error";
  stderr?: string;
}
const snackbar = ref<SnackbarState>({
  show: false,
  message: "",
  color: "success",
});

function showSnackbar(
  message: string,
  color: "success" | "info" | "warning" | "error",
  stderr?: string,
): void {
  snackbar.value = { show: true, message, color, stderr };
}

// Maps a restore reason code to a snackbar message + color.
const RESTORE_REASON_I18N_KEYS: Record<
  string,
  { key: string; color: "warning" | "error" }
> = {
  invalid_body: {
    key: "spcodeProjectLoad.diffSidebar.restore.error.reason.invalid_body",
    color: "error",
  },
  missing_file: {
    key: "spcodeProjectLoad.diffSidebar.restore.error.reason.missing_file",
    color: "error",
  },
  feature_disabled: {
    key: "spcodeProjectLoad.diffSidebar.restore.error.reason.feature_disabled",
    color: "error",
  },
  no_project_loaded: {
    key: "spcodeProjectLoad.diffSidebar.restore.error.reason.no_project_loaded",
    color: "error",
  },
  directory_missing: {
    key: "spcodeProjectLoad.diffSidebar.restore.error.reason.directory_missing",
    color: "error",
  },
  not_a_git_repo: {
    key: "spcodeProjectLoad.diffSidebar.restore.error.reason.not_a_git_repo",
    color: "error",
  },
  worktree_invalid: {
    key: "spcodeProjectLoad.diffSidebar.restore.error.reason.worktree_invalid",
    color: "error",
  },
  git_unavailable: {
    key: "spcodeProjectLoad.diffSidebar.restore.error.reason.git_unavailable",
    color: "error",
  },
  path_unsafe: {
    key: "spcodeProjectLoad.diffSidebar.restore.error.reason.path_unsafe",
    color: "error",
  },
  file_not_found: {
    key: "spcodeProjectLoad.diffSidebar.restore.error.reason.file_not_found",
    color: "error",
  },
  not_modified: {
    key: "spcodeProjectLoad.diffSidebar.restore.error.reason.not_modified",
    color: "warning",
  },
  // 2026-07-15 restore-edge-cases: untracked_file 由 file-restore 端点移除
  // —— 端点现在直接 unlink 未跟踪文件作为"撤销新增"路径,前端不再渲染
  // 这个 reason。reason code 仍存在于 file-discard-hunk 路径(useSpcodeFileDiscardHunk
  // 自己的 REASON 集合),不归此 palette 管。
  git_error: {
    key: "spcodeProjectLoad.diffSidebar.restore.error.reason.git_error",
    color: "error",
  },
  network: {
    key: "spcodeProjectLoad.diffSidebar.restore.error.reason.network",
    color: "error",
  },
  unknown: {
    key: "spcodeProjectLoad.diffSidebar.restore.error.reason.unknown",
    color: "error",
  },
};

// 2026-07-17 git-revert: reason palette for the History-view revert
// flow (docs/api/webapi-git-revert-api.md §4). withStderr reasons
// carry actionable git output (conflict details, hook messages) and
// are surfaced in the snackbar's <pre> block.
const REVERT_REASON_I18N_KEYS: Record<
  string,
  { key: string; color: "warning" | "error"; withStderr?: boolean }
> = {
  invalid_body: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.invalid_body",
    color: "error",
  },
  invalid_param: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.invalid_param",
    color: "error",
  },
  feature_disabled: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.feature_disabled",
    color: "error",
  },
  no_project_loaded: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.no_project_loaded",
    color: "error",
  },
  worktree_invalid: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.worktree_invalid",
    color: "error",
  },
  directory_missing: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.directory_missing",
    color: "error",
  },
  not_a_git_repo: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.not_a_git_repo",
    color: "error",
  },
  git_unavailable: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.git_unavailable",
    color: "error",
  },
  commit_not_found: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.commit_not_found",
    color: "error",
  },
  empty_repository: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.empty_repository",
    color: "error",
  },
  worktree_dirty: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.worktree_dirty",
    color: "warning",
  },
  // REVERT_HEAD intermediate state is left behind on conflict — the
  // message tells the user to finish/abort manually in a terminal.
  revert_conflict: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.revert_conflict",
    color: "warning",
    withStderr: true,
  },
  nothing_to_revert: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.nothing_to_revert",
    color: "warning",
  },
  hook_rejected: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.hook_rejected",
    color: "error",
    withStderr: true,
  },
  identity_not_set: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.identity_not_set",
    color: "error",
  },
  nothing_to_commit: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.nothing_to_commit",
    color: "error",
    withStderr: true,
  },
  // Frontend-only code: git refuses merge-commit reverts (no -m
  // support in the endpoint) → git_error whose stderr mentions
  // "is a merge". Mapped to a dedicated message below.
  merge_commit: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.merge_commit",
    color: "error",
  },
  git_error: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.git_error",
    color: "error",
    withStderr: true,
  },
  network: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.network",
    color: "error",
  },
  unknown: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertError.reason.unknown",
    color: "error",
  },
};

// 2026-08-03 git-squash: reason → snackbar mapping (mirrors
// REVERT_REASON_I18N_KEYS). withStderr entries surface git output
// in the snackbar's <pre> block.
const SQUASH_REASON_I18N_KEYS: Record<
  string,
  { key: string; color: "warning" | "error"; withStderr?: boolean }
> = {
  invalid_body: {
    key: "spcodeProjectLoad.diffSidebar.squash.error.invalid_body",
    color: "error",
  },
  invalid_param: {
    key: "spcodeProjectLoad.diffSidebar.squash.error.invalid_param",
    color: "error",
  },
  invalid_message: {
    key: "spcodeProjectLoad.diffSidebar.squash.error.invalid_message",
    color: "error",
  },
  head_not_selected: {
    key: "spcodeProjectLoad.diffSidebar.squash.error.head_not_selected",
    color: "warning",
  },
  not_contiguous: {
    key: "spcodeProjectLoad.diffSidebar.squash.error.not_contiguous",
    color: "warning",
  },
  root_commit: {
    key: "spcodeProjectLoad.diffSidebar.squash.error.root_commit",
    color: "warning",
  },
  worktree_dirty: {
    key: "spcodeProjectLoad.diffSidebar.squash.error.worktree_dirty",
    color: "warning",
  },
  operation_in_progress: {
    key: "spcodeProjectLoad.diffSidebar.squash.error.operation_in_progress",
    color: "warning",
  },
  commit_not_found: {
    key: "spcodeProjectLoad.diffSidebar.squash.error.commit_not_found",
    color: "error",
  },
  hook_rejected: {
    key: "spcodeProjectLoad.diffSidebar.squash.error.hook_rejected",
    color: "warning",
    withStderr: true,
  },
  identity_not_set: {
    key: "spcodeProjectLoad.diffSidebar.squash.error.identity_not_set",
    color: "error",
  },
  nothing_to_commit: {
    key: "spcodeProjectLoad.diffSidebar.squash.error.nothing_to_commit",
    color: "error",
    withStderr: true,
  },
  git_error: {
    key: "spcodeProjectLoad.diffSidebar.squash.error.git_error",
    color: "error",
    withStderr: true,
  },
  network: {
    key: "spcodeProjectLoad.diffSidebar.squash.error.network",
    color: "error",
  },
  unknown: {
    key: "spcodeProjectLoad.diffSidebar.squash.error.unknown",
    color: "error",
  },
};

// ── Revert commit (History view, 2026-07-17) ─────────────────────
function onLogRevertRequest(commit: { sha: string; subject: string }): void {
  pendingRevert.value = commit;
  revertDialogOpen.value = true;
}

function onCancelRevert(): void {
  revertDialogOpen.value = false;
  pendingRevert.value = null;
}

async function onConfirmRevert(): Promise<void> {
  const target = pendingRevert.value;
  if (!target) return;
  const result = await gitRevert.revert({
    ref: target.sha,
    worktree: selectedWorktree.value,
    umo: spcodeStatus.status.value.umo,
  });
  if (result.ok) {
    revertDialogOpen.value = false;
    pendingRevert.value = null;
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertSuccess", {
        message: result.snapshot.revertMessage,
      }),
      "success",
    );
    // The endpoint created a new HEAD commit — log / status / diff
    // caches are stale. Refresh in parallel; the read endpoints'
    // ETags revalidate server-side so plain refreshes return fresh
    // data (per the endpoint doc §6).
    void Promise.all([
      gitLog.refresh(),
      gitStatus.refresh(),
      composable.refresh(),
    ]);
    return;
  }
  if (result.reason === "aborted") return;
  // Merge commits land in git_error ("is a merge" in stderr) since
  // the endpoint passes no -m; remap to the dedicated message.
  const reason =
    result.reason === "git_error" && /is a merge/i.test(result.stderr ?? "")
      ? "merge_commit"
      : result.reason;
  const meta =
    REVERT_REASON_I18N_KEYS[reason] ?? REVERT_REASON_I18N_KEYS.unknown;
  revertDialogOpen.value = false;
  pendingRevert.value = null;
  showSnackbar(
    tm(meta.key),
    meta.color,
    meta.withStderr ? result.stderr : undefined,
  );
}

// ── Reset branch (History view, 2026-09-09) ─────────────────────
// SourceTree-style "Reset current branch to this commit" — the
// endpoint moves the branch pointer (no new commit). All three modes
// require typing the target commit's 7-char SHA prefix before the
// confirm button unlocks.
const RESET_REASON_I18N_KEYS: Record<
  string,
  { key: string; color: "warning" | "error"; withStderr?: boolean }
> = {
  invalid_body: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetError.reason.invalid_param",
    color: "error",
  },
  invalid_param: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetError.reason.invalid_param",
    color: "error",
  },
  feature_disabled: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetError.reason.feature_disabled",
    color: "warning",
  },
  no_project_loaded: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetError.reason.no_project_loaded",
    color: "warning",
  },
  worktree_invalid: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetError.reason.worktree_invalid",
    color: "warning",
  },
  directory_missing: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetError.reason.directory_missing",
    color: "error",
  },
  not_a_git_repo: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetError.reason.not_a_git_repo",
    color: "error",
  },
  git_unavailable: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetError.reason.git_unavailable",
    color: "error",
  },
  commit_not_found: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetError.reason.commit_not_found",
    color: "error",
  },
  operation_in_progress: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetError.reason.operation_in_progress",
    color: "warning",
  },
  git_error: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetError.reason.git_error",
    color: "error",
    withStderr: true,
  },
  network: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetError.reason.network",
    color: "error",
  },
  unknown: {
    key: "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetError.reason.unknown",
    color: "error",
  },
};

// Consequence line under the mode radios; keys stay static so i18n
// tooling can trace them.
const resetModeHint = computed(() => {
  if (resetMode.value === "soft") {
    return tm(
      "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetModeHintSoft",
    );
  }
  if (resetMode.value === "hard") {
    return tm(
      "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetModeHintHard",
    );
  }
  return tm(
    "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetModeHintMixed",
  );
});

// Uncommitted-change count for the hard-mode warning line. 0 when the
// status snapshot isn't loaded — the warning simply stays hidden.
const pendingResetDirtyCount = computed(() => {
  const s = gitStatus.state.value;
  return s.kind === "ok" ? s.snapshot.summary.total : 0;
});

function onLogResetRequest(commit: { sha: string; subject: string }): void {
  pendingReset.value = commit;
  resetMode.value = "mixed";
  resetConfirmSha.value = "";
  resetDialogOpen.value = true;
}

function onCancelReset(): void {
  resetDialogOpen.value = false;
  pendingReset.value = null;
  resetConfirmSha.value = "";
}

async function onConfirmReset(): Promise<void> {
  const target = pendingReset.value;
  if (!target || !resetConfirmReady.value) return;
  const result = await gitReset.reset({
    ref: target.sha,
    mode: resetMode.value,
    worktree: selectedWorktree.value,
    umo: spcodeStatus.status.value.umo,
  });
  if (result.ok) {
    resetDialogOpen.value = false;
    pendingReset.value = null;
    resetConfirmSha.value = "";
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetSuccess", {
        mode: resetMode.value,
        sha: result.snapshot.afterSha.slice(0, 7),
      }),
      "success",
    );
    // The branch pointer moved — and hard additionally rewrote the
    // worktree/index. Log / status / diff / stats are all stale
    // (same refresh set as amend).
    void Promise.all([
      gitLog.refresh(),
      gitStatus.refresh(),
      gitStats.refresh(),
      composable.refresh(),
    ]);
    return;
  }
  if (result.reason === "aborted") return;
  const meta =
    RESET_REASON_I18N_KEYS[result.reason] ?? RESET_REASON_I18N_KEYS.unknown;
  resetDialogOpen.value = false;
  pendingReset.value = null;
  resetConfirmSha.value = "";
  showSnackbar(
    tm(meta.key),
    meta.color,
    meta.withStderr ? result.stderr : undefined,
  );
}

// ── Amend commit message (History view, 2026-08-13) ────────────────
const AMEND_REASON_I18N_KEYS: Record<
  string,
  { key: string; color: "warning" | "error"; withStderr?: boolean }
> = {
  invalid_body: {
    key: "spcodeProjectLoad.diffSidebar.amend.error.invalid_body",
    color: "error",
  },
  invalid_message: {
    key: "spcodeProjectLoad.diffSidebar.amend.error.invalid_message",
    color: "error",
  },
  empty_repository: {
    key: "spcodeProjectLoad.diffSidebar.amend.error.empty_repository",
    color: "error",
  },
  operation_in_progress: {
    key: "spcodeProjectLoad.diffSidebar.amend.error.operation_in_progress",
    color: "warning",
  },
  staged_changes_present: {
    key: "spcodeProjectLoad.diffSidebar.amend.error.staged_changes_present",
    color: "warning",
  },
  cannot_amend_merge_commit: {
    key: "spcodeProjectLoad.diffSidebar.amend.error.cannot_amend_merge_commit",
    color: "warning",
  },
  hook_rejected: {
    key: "spcodeProjectLoad.diffSidebar.amend.error.hook_rejected",
    color: "warning",
    withStderr: true,
  },
  identity_not_set: {
    key: "spcodeProjectLoad.diffSidebar.amend.error.identity_not_set",
    color: "error",
  },
  nothing_to_commit: {
    key: "spcodeProjectLoad.diffSidebar.amend.error.nothing_to_commit",
    color: "error",
  },
  amend_failed: {
    key: "spcodeProjectLoad.diffSidebar.amend.error.amend_failed",
    color: "error",
    withStderr: true,
  },
  network: {
    key: "spcodeProjectLoad.diffSidebar.amend.error.network",
    color: "error",
  },
  unknown: {
    key: "spcodeProjectLoad.diffSidebar.amend.error.unknown",
    color: "error",
  },
};

function onLogAmendRequest(commit: {
  sha: string;
  subject: string;
  body: string | null;
}): void {
  pendingAmend.value = commit;
  amendDialogOpen.value = true;
}

async function onAmendSubmit(p: {
  sha: string;
  message: string;
}): Promise<void> {
  const target = pendingAmend.value;
  if (!target) return;
  const result = await gitCommitAmend.amend({
    message: p.message,
    worktree: selectedWorktree.value,
    umo: spcodeStatus.status.value.umo,
  });
  if (result.ok) {
    amendDialogOpen.value = false;
    pendingAmend.value = null;
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.amend.success", {
        sha: result.snapshot.afterSha.slice(0, 7),
      }),
      "success",
    );
    // amend rewrote HEAD — log / status / diff / stats are stale.
    void Promise.all([
      gitLog.refresh(),
      gitStatus.refresh(),
      gitStats.refresh(),
      composable.refresh(),
    ]);
    return;
  }
  if (result.reason === "aborted") return;
  const meta =
    AMEND_REASON_I18N_KEYS[result.reason] ?? AMEND_REASON_I18N_KEYS.unknown;
  amendDialogOpen.value = false;
  pendingAmend.value = null;
  showSnackbar(
    tm(meta.key),
    meta.color,
    meta.withStderr ? result.stderr : undefined,
  );
}

// ── Squash commits (History view, 2026-08-03) ────────────────────
function onLogSquashRequest(payload: { commits: SquashPayloadCommit[] }): void {
  pendingSquash.value = payload.commits;
  squashDialogOpen.value = true;
}

async function onSquashSubmit(p: {
  shas: string[];
  message: string;
}): Promise<void> {
  const result = await gitSquash.squash({
    shas: p.shas,
    message: p.message,
    worktree: selectedWorktree.value,
    umo: spcodeStatus.status.value.umo,
  });
  if (result.ok) {
    squashDialogOpen.value = false;
    pendingSquash.value = null;
    squashResetToken.value++;
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.squash.success", {
        sha: result.snapshot.newSha.slice(0, 7),
      }),
      "success",
    );
    // Plain refreshes (revert precedent): the git-log ETag embeds
    // head_sha + .git/index mtime — both changed by reset+commit —
    // so the conditional request revalidates server-side and never
    // replays a stale 304. No invalidateEtag call needed.
    void Promise.all([
      gitLog.refresh(),
      gitStatus.refresh(),
      composable.refresh(),
    ]);
    return;
  }
  if (result.reason === "aborted") return;
  const meta =
    SQUASH_REASON_I18N_KEYS[result.reason] ?? SQUASH_REASON_I18N_KEYS.unknown;
  squashDialogOpen.value = false;
  pendingSquash.value = null;
  showSnackbar(
    tm(meta.key),
    meta.color,
    meta.withStderr ? result.stderr : undefined,
  );
}

const isProjectLoaded = computed(
  () => spcodeStatus.status.value.loaded === true,
);

// Spec 2026-07-16: derived state for the GitRepoInitPrompt slot. All four
// refs/booleans are colocated so the template-binding code in Task 5
// stays compact and obvious.
const isGitRepo = computed(() => gitRepoProbe.state.value.kind === "ok");
const isRepoInitSubmitting = ref(false);
const repoInitLastError = ref<{ reason: string; stderr?: string } | null>(null);
const repoPromptDismissed = ref(false);
const showRepoInitPrompt = computed(
  () =>
    isProjectLoaded.value &&
    gitRepoProbe.state.value.kind === "not_a_git_repo" &&
    !repoPromptDismissed.value,
);
const showNotGitRepoChip = computed(
  () =>
    isProjectLoaded.value &&
    gitRepoProbe.state.value.kind === "not_a_git_repo" &&
    repoPromptDismissed.value,
);
// True while a scope-switch request is in flight. Drives the per-pill
// spinner and the disabled state on the *other* two pills, so the
// user can't queue up a second switch before the first resolves.
const isScopeLoading = computed(() => pendingScope.value !== null);

const isFetching = ref(false);
// Template ref to the <FileBrowserView> child so the header refresh
// button can reach its exposed refresh() method when the user is in
// files view. The component is rendered behind v-if="viewMode==='files'",
// so the ref is null while diff view is active — onManualRefresh
// handles the null case explicitly.
// Inline interface (instead of InstanceType<typeof FileBrowserView>)
// because <script setup> components don't auto-export their exposed
// methods to the public type — defineExpose types are local to the
// file that calls it. Mirroring the shape here keeps the consumer
// honest about what it can call.
const fileBrowserRef = ref<{ refresh: () => Promise<void> } | null>(null);
// Mirror of fileBrowserRef for <GitDiffBodyContent>. We need
// `clearSelection` to drop the "selected N files" counter after a
// successful bulk stage/unstage — the child owns the Set (it's a
// UI concern scoped to that component), so the parent reaches in
// via the exposed method instead of duplicating the state here.
// Same `v-if="viewMode==='diff'"` caveat: ref is null in other tabs,
// so callers null-check before invocation.
const gitDiffBodyRef = ref<{ clearSelection: () => void } | null>(null);
// Spec 2026-07-23 §4.1: manual refresh for the branch + worktree
// management row. 3-way parallel refresh (branches / worktrees /
// git-diff) so a single click pulls the latest server state across
// all three lists the row controls. Distinct from the top-bar
// `mdi-restart` button (onManualRefresh, viewMode-aware) which is
// the file-browser-aware refresh path.
//
// `useSpcodeGitBranches.refresh` and `useSpcodeWorktrees.refresh`
// already return Promises; we use `Promise.allSettled` (NOT
// `Promise.all`) so a single failure doesn't kill the other two
// requests' UI updates — branches may 404 on a non-repo while
// worktrees still answers, and we want both partial results
// visible. Loading state is derived in the template from each
// composable's `state.value.kind === 'loading'` rather than a
// local ref.
//
// Spec 2026-07-23 (rev for click feedback): the loading spinner
// alone is not enough — instant refreshes (cached, no changes)
// finish before the spinner can render, so the user gets no
// "click registered" confirmation. We layer a 350ms primary-tint
// flash via `bwClickFlash` so every click registers visually.
// The `nextTick` + `setTimeout` reset trick lets rapid re-clicks
// each get a fresh flash: setting false → await DOM patch →
// setting true re-applies the class, restarting any active CSS
// transition. The timer is cleared on each click so a stray timer
// from a prior click can't keep `is-flashing` stuck on.
const bwClickFlash = ref(false);
let bwClickFlashTimer: ReturnType<typeof setTimeout> | null = null;
function flashBwRefreshButton(): void {
  bwClickFlash.value = false;
  void nextTick(() => {
    bwClickFlash.value = true;
    if (bwClickFlashTimer) clearTimeout(bwClickFlashTimer);
    bwClickFlashTimer = setTimeout(() => {
      bwClickFlash.value = false;
      bwClickFlashTimer = null;
    }, 350);
  });
}
async function refreshBranchesAndWorktrees(): Promise<void> {
  flashBwRefreshButton();
  await Promise.allSettled([
    branchesComposable.refresh(),
    worktreesComposable.refresh(),
    composable.refresh(),
  ]);
}
async function onManualRefresh(): Promise<void> {
  if (isFetching.value) return;
  isFetching.value = true;
  try {
    // View-mode-aware dispatch (option B): in files view the button
    // reloads the workspace (directory listing + file preview);
    // otherwise it reloads the git diff data. The previous behavior
    // (always reload git diff) was a UX trap in files view — the
    // user could see the spinner but no visible data would change.
    //
    // Worktree list refreshes in PARALLEL regardless of viewMode:
    //   - It's cheap (one porcelain v1 call) so there's no UX cost.
    //   - The worktree tab switcher IS visible across all three
    //     tabs, so leaving it stale would surprise the user
    //     (e.g. they just added a worktree in a terminal and want
    //     to see it in the sidebar immediately).
    // 2026-08-09 (elecvoid243): in diff view the button also reloads
    // git-status (branch / untracked / intent-to-add), so the "new
    // files" section and the Stage-all count update immediately
    // instead of waiting for the next 10s polling tick. git-status
    // is irrelevant to history/docs views, so it only joins the
    // diff branch. Mirrors the parallel refresh pattern used by
    // stage / unstage / commit handlers.
    const worktreeRefresh = worktreesComposable.refresh();
    if (viewMode.value === "files") {
      await Promise.all([
        fileBrowserRef.value?.refresh() ?? Promise.resolve(),
        worktreeRefresh,
      ]);
    } else if (viewMode.value === "diff") {
      await Promise.all([
        composable.refresh(),
        gitStatus.refresh(),
        worktreeRefresh,
      ]);
    } else {
      await Promise.all([composable.refresh(), worktreeRefresh]);
    }
  } finally {
    isFetching.value = false;
  }
}

// Fetch worktree list once on mount (lightweight, fire-and-forget).
// Spec §3.3: useSpcodeWorktrees does NOT depend on umo.
onMounted(() => {
  void worktreesComposable.refresh();
  // 2026-08-13: defer the initial branch fetch ~500ms instead of firing
  // the instant the sidebar mounts (see refreshDelayed in the
  // composable). The umo watch already routes project switches through
  // the same delayed path.
  branchesComposable.refreshDelayed();
  // Spec 2026-07-16: initial probe on mount. The composable internally
  // watches `umo`/`directory` and re-probes on project switch, so a
  // single refresh() on mount is enough.
  void gitRepoProbe.refresh();
  // Listen on capture phase so we fire BEFORE any inner click handler
  // that might preventDefault / stopPropagation. mousedown is the
  // natural event for outside-click detection (mirrors what Vuetify
  // v-menu does internally with its overlay).
  document.addEventListener("mousedown", closeContextMenuOnOutside, true);
  document.addEventListener("keydown", closeContextMenuOnEscape, true);
  // 2026-08-21 git-conflict: refocus refresh (see refreshConflictOnWake).
  window.addEventListener("focus", refreshConflictOnWake);
  document.addEventListener("visibilitychange", refreshConflictOnWake);
  // Fullscreen cancel handler: bound to document (not window) in
  // bubble phase so the component-local Escape handlers (the file
  // browsers' capture-phase handlers, SearchPanel, etc.) run first.
  // The globalFullscreen guard filters out non-fullscreen cases so
  // other Escape shortcuts keep working unchanged.
  document.addEventListener("keydown", onFullscreenKeyDown);
});

// ── Worktree polling (added 2026-06-25, elecvoid243) ──────────────
// Spec: "当且仅当侧边栏打开时才触发轮询" — the agent can run
// `git worktree add` / `git worktree remove` while the user is
// staring at the sidebar, and the tab switcher must reflect that
// within a reasonable delay. We poll at 30s (the composable's
// DEFAULT_POLL_MS) so the user doesn't see a stale list after the
// agent bootstraps a new worktree.
//
// DECOUPLING NOTE: this watcher is intentionally separate from the
// viewMode-aware polling watcher below. Rationale:
//   - The worktree list powers the tab switcher for ALL three tabs
//     (diff / files / history), not just the diff tab.
//   - The user shouldn't have to switch to the diff tab to "wake
//     up" the worktree refresh — that's a footgun.
//   - Mixing the worktree polling lifecycle into the diff/history
//     branch logic would create cross-concern coupling (a
//     future tab addition would need to remember to start/stop
//     worktree polling, easy to forget).
//
// `immediate: true` covers the "sidebar is open at mount" case
// (which is the common case in this dashboard — the sidebar is
// always mounted, just sometimes closed). For the "starts closed
// then opens" path, modelValue flipping true starts the timer.
//
// isGitRepo is part of the gate: a non-Git project has no worktrees
// to list, so polling 30s for it is pure waste. The watcher below
// (`isGitRepo` race-catch) tears down any timer that already started
// when the probe lands on `not_a_git_repo`.
watch(
  () => props.modelValue,
  (open) => {
    if (open && isGitRepo.value) {
      worktreesComposable.startPolling(30_000);
    } else {
      worktreesComposable.stopPolling();
    }
  },
  { immediate: true },
);

// Spec 2026-07-21 §3.5: branch polling rides the same lifecycle
// as worktree polling (30s cadence) — both start when the sidebar
// opens in a git repo, both stop when it closes or the repo is
// unloaded. Single source of truth: the same [modelValue, isGitRepo]
// gate that drives worktree polling drives branch polling.
watch(
  () => props.modelValue,
  (open) => {
    if (open && isGitRepo.value) {
      branchesComposable.startPolling(30_000);
      // 2026-08-01 git-conflict: poll conflict-status on the same
      // lifecycle so externally-triggered merges (agent in a terminal)
      // light up the banner within one cadence.
      gitConflict.startPolling(30_000);
      // 2026-08-21 git-conflict: setInterval waits a full cadence
      // before the first fetch — refresh immediately on open so a
      // conflict created while the sidebar was closed lights up the
      // banner right away instead of 30 s later.
      void gitConflict.refresh();
    } else {
      branchesComposable.stopPolling();
      gitConflict.stopPolling();
    }
  },
  { immediate: true },
);

// 2026-08-21 git-conflict: catch the "user ran git in a terminal and
// alt-tabs back" case without waiting out the 30 s cadence. Both
// window focus (alt-tab / OS switch) and tab visibility (browser tab
// switch) funnel into one guarded refresh; refresh() is single-flight
// (aborts the in-flight request), so double-firing is harmless. The
// listener pair is registered in onMounted and removed in
// onBeforeUnmount like the other document-level listeners.
function refreshConflictOnWake(): void {
  if (
    document.visibilityState === "visible" &&
    props.modelValue &&
    isGitRepo.value
  ) {
    void gitConflict.refresh();
  }
}

// Spec 2026-07-16: one-shot check on open. The prompt is driven by
// "is the loaded directory a Git repo?", which doesn't change while
// the user is staring at the sidebar. Re-probing every 30s would just
// waste a round-trip. The umo/directory watcher inside the composable
// handles the only case where the answer can change without
// re-opening the sidebar: the user issuing a `/project load` while
// it's open.
watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      void gitRepoProbe.refresh();
    }
  },
  { immediate: true },
);

// Race-catch: modelValue→true fires startPolling synchronously, but
// the probe's `not_a_git_repo` resolution lands in a later microtask.
// Without this, a 30s worktree timer would already be ticking by the
// time the probe returns. Teardown all four composables together so
// the gate has a single source of truth.
watch(isGitRepo, (isRepo) => {
  if (isRepo) return;
  worktreesComposable.stopPolling();
  branchesComposable.stopPolling();
  gitConflict.stopPolling();
  composable.stopPolling();
  gitStatus.stopPolling();
  gitLog.stopPolling();
});

// Spec: polling starts ONLY when the sidebar is open, the user is on
// the Git Diff / History tab, AND the project is actually a Git repo.
// The Files ("workspace") tab never polls — there's no diff data to
// refresh, and pulling it would be wasted network/CPU. Non-Git
// projects never poll at all (no worktrees, no diff, no history) —
// the catch-all `else` branch keeps all three composables stopped.
// We track all three inputs in a single watcher so the polling
// lifecycle has one source of truth.
watch(
  [() => props.modelValue, viewMode, isGitRepo],
  async ([open, mode, isRepo]) => {
    const isDiff = mode === "diff";
    const isHistory = mode === "history";
    if (open && isDiff && isRepo) {
      isFetching.value = true;
      try {
        // Fetch diff + status in parallel. The git-status call is
        // intentionally NOT awaited via `await` in the polling branch
        // (a slow status call should not delay the diff refresh), but
        // here we DO await both so the initial load is consistent
        // before the first render. Fire-and-forget would risk showing
        // an empty "new files" section on first paint.
        await Promise.all([composable.refresh(), gitStatus.refresh()]);
      } finally {
        isFetching.value = false;
      }
      // Re-check conditions after await: the user may have switched
      // tabs, closed the sidebar, or the probe may have flipped to
      // `not_a_git_repo` during the refresh. Starting polling here
      // without re-checking would leak a timer in any of those cases.
      if (props.modelValue && viewMode.value === "diff" && isGitRepo.value) {
        composable.startPolling(10_000);
        // git-status polling rides on the same cadence so the
        // "new files" section stays in sync with diff changes.
        gitStatus.startPolling(10_000);
      }
      gitLog.stopPolling();
    } else if (open && isHistory && isRepo) {
      // History view polls gitLog instead of gitDiff.
      isFetching.value = true;
      try {
        await gitLog.refresh();
      } finally {
        isFetching.value = false;
      }
      if (props.modelValue && viewMode.value === "history" && isGitRepo.value) {
        gitLog.startPolling(10_000);
      }
      composable.stopPolling();
      gitStatus.stopPolling();
    } else {
      composable.stopPolling();
      gitStatus.stopPolling();
      gitLog.stopPolling();
    }
  },
  { immediate: true },
);

// Refresh git-status when the user switches INTO the unstaged view
// (the composable only watches `worktreeRef` for refetch triggers; we
// need scope-driven refresh too). Without this the user could see a
// stale untracked list after toggling between staged/all/unstaged.
watch(
  () => selectedScope.value,
  (scope) => {
    if (scope === "unstaged" && props.modelValue && viewMode.value === "diff") {
      void gitStatus.refresh();
    }
  },
);

// 2026-07-20 fix(dashboard, elecvoid243): sync stagedFiles from the
// authoritative git-status snapshot whenever it loads successfully.
// Without this, the commit dialog shows an empty file list and AI
// commit-message generation is disabled after component recreation
// (e.g. user navigates away from /chat to /settings and back), even
// though the actual git staging area still holds the staged files.
// The stagedCount computed already falls back to gitStatus, but
// stagedFiles (a local optimistic Set) was never initialised from it,
// so Array.from(stagedFiles) passed to GitCommitDialog was always
// empty on first mount.
watch(
  () => gitStatus.state.value,
  (s) => {
    if (s.kind !== "ok") return;
    const stagedPaths = s.snapshot.files
      .filter((f) => f.scope === "staged")
      .map((f) => f.path);
    stagedFiles.value = new Set(stagedPaths);
  },
);

// 2026-07-18 git-stats heatmap: lazy-fetch the stats snapshot once
// the panel is (or becomes) visible. Fires on: sidebar open while in
// history view, switching into history view, panel re-expand,
// worktree switch, and RANGE CHANGE (gitStatsRange is a watch
// source) while visible. The composable keys ETag + previous
// snapshot by umo|worktree|since|until, so same-key repeats are
// cheap 304s.
//
// 2026-07-18 fix(dashboard): stop returning `{}` for presets —
// every preset also resolves to a concrete since/until via
// rangeForPreset, and forwarding those is what makes hot files
// (and the day's totals) actually respond to the preset the user
// picked. The previous behaviour sent `umo|worktree` only, which
// gave every preset the same ETag key `umo|worktree||` — so
// switching 1w → 1y → 1w would 304-replay whichever preset's
// snapshot had been cached first, and the backend never got the
// chance to refilter hot files. Now each preset gets its own ETag
// bucket (1w="2026-07-12|2026-07-18", 1y="2025-07-20|2026-07-18",
// ...) and the backend's since/until filter is the source of
// truth for the hot-files slice, exactly the same way the heatmap
// frontend uses the same since/until to anchor its week columns.
function statsRangeArgs(): {
  since: string;
  until: string;
  topFiles: number;
} {
  const r = gitStatsRange.value;
  const sinceUntil =
    r.kind === "preset"
      ? rangeForPreset(r.preset)
      : { since: r.since, until: r.until };
  return {
    since: sinceUntil.since,
    until: sinceUntil.until,
    // 2026-07-19 hot-files picker: always forward the user's cap so
    // a changed cap re-fetches into a new ETag bucket immediately
    // (the watcher below does not need to special-case this).
    topFiles: gitStatsTopFilesLimit.value,
  };
}
watch(
  [
    () => props.modelValue,
    viewMode,
    isGitRepo,
    statsOpen,
    selectedWorktree,
    gitStatsRange,
    // 2026-07-19 hot-files picker: a cap change should re-fetch
    // immediately so the new bucket's ETag is populated and the
    // panel's emit-handler path is the same as the range's.
    gitStatsTopFilesLimit,
  ],
  ([open, mode, isRepo, panelOpen]) => {
    if (open && mode === "history" && isRepo && panelOpen) {
      void gitStats.refresh(statsRangeArgs());
    }
  },
  { immediate: true },
);

// ── Merged view (git-diff + git-status new files) ─────────────────
// For the **unstaged** and **all (vs HEAD)** views we want brand-new
// files (`untracked` + `intent_to_add`) to appear alongside modified
// files in one list, with the new files rendered with a distinct
// color. We achieve this by:
//   1. Building a `newFilePaths` Set from git-status's untracked +
//      intent_to_add entries. The set is the source of truth for
//      "is this row a new file"; the body content consults it.
//   2. When the diff snapshot is `ok`, we splice a synthesized entry
//      for each new file into the snapshot's `files` array so the body
//      iterates a single list. New files use `status: "A"` (matches
//      semantic meaning: "added to project") and carry a stub slice
//      (no diff content available from git-status). The
//      `!existing.has(f.path)` guard inside the merge is what makes
//      the same code safe across both views: `git diff HEAD` (all
//      scope) already returns `intent_to_add` paths, so the splice
//      becomes a no-op for those, while `untracked` paths are added
//      because no native diff command includes them.
//   3. We only skip the merge for the **staged** scope: there,
//      `git diff --cached` already covers everything that could be
//      staged, and an untracked file is by definition not staged.
const EMPTY_PATH_SET: ReadonlySet<string> = new Set<string>();

const newFilePaths = computed<ReadonlySet<string>>(() => {
  if (selectedScope.value === "staged") return EMPTY_PATH_SET;
  const s = gitStatus.state.value;
  if (s.kind !== "ok") return EMPTY_PATH_SET;
  const paths: string[] = [];
  for (const f of s.snapshot.files) {
    if (isNewFileScope(f.scope)) paths.push(f.path);
  }
  return new Set(paths);
});

// ── New-file line counts ────────────────────────────────────────────
// git-status does not return patch data, so the new-file stubs have
// no `additions` count. We compensate by reading each new file via
// /spcode/file-browser (cached, worktree-scoped) and counting lines
// locally. The composable fills `lineCounts.counts.value` reactively
// as fetches complete; until then the row renders "+0 −0" (same as a
// diff row with no slice). See useSpcodeNewFileLineCounts.ts for the
// cache / revalidation strategy. `currentRoot` is defined further
// below for the FileBrowserView — we inline its expression here so
// the composable runs before that declaration.
const lineCounts = useSpcodeNewFileLineCounts(
  newFilePaths,
  computed(() => selectedWorktree.value ?? mainWorktreePath.value ?? ""),
);

/** Build a SpcodeGitDiffFile-shaped stub for an untracked/intent-to-add
 *  entry. No native diff slice is available (git-status does not
 *  return patches), but `useSpcodeNewFileLineCounts` caches the raw
 *  file content per path, so we synthesize a unified-diff slice with
 *  a `@@ -0,0 +1,N @@` header and `+`-prefixed lines. This lets
 *  <DiffPreview> render the file with line numbers, the green
 *  "added" tint, the standard 30-line truncation, and a
 *  "Show all N lines" overflow button — all without a new
 *  component. When the content is not yet fetched (or the file is
 *  binary / too large), `slice` is null and the row shows the
 *  "noContent" placeholder. `status: "A"` keeps the row aligned
 *  with regular "added" diff entries; GitDiffFileItem.vue still
 *  uses the `isNewFile` prop to render a distinct left icon
 *  (mdi-file-plus-outline). */
function newFileStub(f: SpcodeGitStatusFile): SpcodeGitDiffFile {
  const content = lineCounts.contents.value.get(f.path);
  return {
    path: f.path,
    status: "A" as FileStatus,
    additions: lineCounts.counts.value.get(f.path) ?? 0,
    deletions: 0,
    slice: content !== undefined ? buildNewFileSlice(content) : null,
    isBinary: false,
  };
}

/** Build a synthetic unified-diff slice from the raw content of a
 *  brand-new file. Mirrors what `git diff /dev/null <file>` would
 *  emit: one hunk starting at line 1 of the new file, with every
 *  line marked as an addition. The trailing empty line introduced
 *  by a final `\n` is dropped so the hunk header's count matches
 *  the additions badge (which `countLines` already normalizes). */
function buildNewFileSlice(content: string): string {
  // Split on \n; file-browser already normalized \r\n → \n.
  const raw = content.split("\n");
  // Drop the trailing empty element caused by a final newline so
  // the displayed line count matches the +N badge.
  const lines = raw[raw.length - 1] === "" ? raw.slice(0, -1) : raw;
  const header = `@@ -0,0 +1,${lines.length} @@`;
  const body = lines.map((line) => `+${line}`).join("\n");
  return `${header}\n${body}\n`;
}

/**
 * State passed to <GitDiffBodyContent>. In the unstaged view it is the
 * composable's state with new files spliced into `snapshot.files`. In
 * any other scope we pass the composable's state untouched. The error
 * path is also untouched — new-file data should never mask a real
 * git-diff error.
 */
const diffBodyState = computed<GitDiffFetchState>(() => {
  const base = composable.state.value;
  // See `newFilePaths` above for why we skip the merge only for the
  // staged scope. The "all" scope shares the same splice logic as
  // "unstaged" — the `!existing.has` guard inside the loop
  // dedupes `intent_to_add` paths that already arrive from
  // `git diff HEAD`.
  if (selectedScope.value === "staged") return base;
  if (base.kind !== "ok") return base;
  const snapshot: SpcodeGitDiffSnapshot = base.snapshot;
  const statusSnap = gitStatus.state.value;
  if (statusSnap.kind !== "ok") return base;
  const existing = new Set(snapshot.files.map((f) => f.path));
  const stubs: SpcodeGitDiffFile[] = [];
  for (const f of statusSnap.snapshot.files) {
    if (isNewFileScope(f.scope) && !existing.has(f.path)) {
      stubs.push(newFileStub(f));
      existing.add(f.path);
    }
  }
  if (stubs.length === 0) return base;
  return {
    kind: "ok",
    snapshot: {
      ...snapshot,
      files: [...snapshot.files, ...stubs],
    },
  };
});

// Reset selectedWorktree to null (main) when project is unloaded or
// the loaded directory changes — the previous path may no longer be valid.
watch(
  () => spcodeStatus.status.value.loaded,
  (loaded) => {
    if (!loaded) {
      selectedWorktree.value = null;
      emit("update:modelValue", false);
    }
  },
);
watch(
  () => spcodeStatus.status.value.directory,
  () => {
    selectedWorktree.value = null;
  },
);

// Spec 2026-06-20 §3.4: switching worktree is a new working context;
// reset the scope to the default (`unstaged`). Handled in the click
// handler (see ``onWorktreeChange``) rather than a ``selectedWorktree``
// watcher, because the ``directory`` watcher above also writes to
// ``selectedWorktree`` on project switches and we MUST NOT reset the
// user's scope preference in that case (spec §3.4: directory change
// preserves scope).

// Spec 2026-06-20 §2.3: clear the in-flight marker the moment a
// response lands, regardless of which scope the server echoed.
//
// Why unconditional: the previous version gated clearing on
// `s.snapshot.meta.scope === pendingScope.value`. That failed in two
// real scenarios:
//   1. The spcode plugin running v3.0 (pre-scope) returns no
//      `data.scope` field, so `normalizeScope(undefined) === null`
//      and the equality check is `null === 'staged'` → false → the
//      spinner hangs forever even though the body already shows the
//      fresh data.
//   2. A late-arriving response for an already-superseded scope
//      (e.g. user clicked "staged" then "all" before the first
//      request returned) leaves the pill spinning on the older
//      scope value.
//
// The composable still logs a `console.warn` via its drift detector
// when the echoed scope doesn't match what was requested, so
// observability is preserved.
watch(
  () => composable.state.value,
  (s) => {
    if (pendingScope.value === null) return;
    if (s.kind === "ok" || s.kind === "error") {
      pendingScope.value = null;
    }
  },
);

// Spec 2026-06-20 §3.4: clicking a worktree tab is a new working
// context, so reset the scope to default. Done here (not in a
// watcher) so project switches — which also reset selectedWorktree
// via the directory watcher above — do NOT clobber scope.
function onWorktreeChange(path: string | null): void {
  // Spec 2026-07-16: switching worktrees is a new "did the user already
  // dismiss the prompt?" boundary. Reset so a different non-Git
  // worktree re-presents the init flow.
  repoPromptDismissed.value = false;
  selectedWorktree.value = path;
  selectedScope.value = DEFAULT_SCOPE;
  pendingScope.value = null;
  // 2026-09-08 (elecvoid243): the History view's focused commit (set by
  // the file-preview "view this file's history" jump) belongs to the
  // previous worktree. SHAs are shared across worktrees of the same
  // repo, so a stale SHA could highlight an unrelated commit in the new
  // list — clear it together with the filter reset in useSpcodeGitLog.
  focusedCommitSha.value = null;
}

// ── Branch management helpers (spec 2026-07-21 §3.3, §3.6, §3.7) ──

// Spec §3.7: branch error reason → i18n key. Per-endpoint tables.
// We keep this in-script rather than importing from
// `parseSpcodeBranchManagement` because we want one key per reason
// (without the `i18nKey` doubling that classifyBranchReason does
// for the worktree pattern), and the worktreeMgmt.* namespace uses
// `error.<reason>` not `<endpoint>.error.<reason>`. The reason
// → i18nKey map is small enough to inline.
const BRANCH_ERROR_KEYS = {
  switch: {
    worktree_dirty:
      "spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.worktree_dirty",
    branch_not_found:
      "spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.branch_not_found",
    invalid_branch:
      "spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.invalid_branch",
    invalid_body:
      "spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.git_error",
    git_error:
      "spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.git_error",
    network: "spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.git_error",
    unknown: "spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.git_error",
  },
  delete: {
    branch_is_current:
      "spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.branch_is_current",
    branch_not_merged:
      "spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.branch_not_merged",
    branch_not_found:
      "spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.branch_not_found",
    invalid_branch:
      "spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.git_error",
    git_error:
      "spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.git_error",
    network: "spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.git_error",
    unknown: "spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.git_error",
  },
  create: {
    branch_exists:
      "spcodeProjectLoad.diffSidebar.branchMgmt.create.error.branch_exists",
    invalid_branch:
      "spcodeProjectLoad.diffSidebar.branchMgmt.create.error.invalid_branch",
    git_error:
      "spcodeProjectLoad.diffSidebar.branchMgmt.create.error.git_error",
    network: "spcodeProjectLoad.diffSidebar.branchMgmt.create.error.git_error",
    unknown: "spcodeProjectLoad.diffSidebar.branchMgmt.create.error.git_error",
  },
} as const;

function showBranchError(
  endpoint: "switch" | "delete" | "create",
  reason: string,
  stderr: string,
): void {
  const key =
    (BRANCH_ERROR_KEYS[endpoint] as Record<string, string>)[reason] ??
    BRANCH_ERROR_KEYS[endpoint].git_error;
  showSnackbar(tm(key, { name: "", stderr }), "error", stderr);
}

// Spec §3.6: cascade refresh after a successful branch
// switch. Always refreshes worktree (wt.branch field changes).
// Also refreshes the currently visible view so the user sees the
// new branch state without waiting for the next 10s polling tick.
async function refreshAfterBranchChange(): Promise<void> {
  const tasks: Promise<unknown>[] = [worktreesComposable.refresh()];
  switch (viewMode.value) {
    case "diff":
      tasks.push(composable.refresh(), gitStatus.refresh());
      break;
    case "files":
      tasks.push(gitStatus.refresh());
      break;
    case "history":
      tasks.push(gitLog.refresh());
      break;
    case "docs":
      tasks.push(gitStatus.refresh());
      break;
  }
  await Promise.allSettled(tasks);
}

async function onBranchMenuItemClick(b: {
  name: string;
  current: boolean;
}): Promise<void> {
  if (b.current) {
    // Current branch is a no-op — close the menu.
    branchMenuOpen.value = false;
    return;
  }
  const fromBranch = currentBranchName.value;
  switchTarget.value = { from: fromBranch, to: b.name };
  switchDirtyCount.value = 0;
  switchDialogOpen.value = true;
  // Async dirty pre-check; if it fails, the dialog still opens
  // with dirtyCount=0 (assume clean) and the backend will reject
  // a real dirty switch.
  const umo = spcodeStatus.status.value.umo;
  if (umo) {
    try {
      // pluginExtensionApi.get<InnerShape>(...) returns
      // ApiEnvelope<InnerShape> = { data: InnerShape, ... }, so we
      // access resp.data.data.dirty_count for the inner field.
      const resp = await pluginExtensionApi.get<{
        dirty_count?: number;
      }>("spcode/git-status", {
        params: {
          umo,
          worktree: selectedWorktree.value ?? undefined,
        },
      });
      switchDirtyCount.value = resp.data?.data?.dirty_count ?? 0;
    } catch {
      switchDirtyCount.value = 0;
    }
  }
}

async function onBranchSwitchConfirm(name: string): Promise<void> {
  isBranchSwitching.value = true;
  try {
    const params: BranchSwitchParams = { name };
    const result = await branchesComposable.switch(params);
    if (!result.ok) {
      showBranchError("switch", result.reason, result.stderr ?? "");
      return;
    }
    switchDialogOpen.value = false;
    branchMenuOpen.value = false;
    await refreshAfterBranchChange();
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.branchMgmt.switch.success", { name }),
      "success",
    );
  } finally {
    isBranchSwitching.value = false;
  }
}

function onBranchDeleteClick(b: { name: string; current: boolean }): void {
  if (b.current) return; // UI never shows × for current, but defense-in-depth
  deleteTarget.value = b.name;
  deleteDialogOpen.value = true;
}

// 2026-08-01 git-merge: branch-menu per-row "merge into current" action
// plus the conflict lifecycle composable (banner/panel mounted later in
// the template; instantiated here because merge/cherry-pick conflict
// paths refresh it immediately). NOTE: gitMerge/gitConflict themselves
// are instantiated up with the other composables (~line 690) because
// the polling watchers below run with immediate:true.
const mergeDialogOpen = ref(false);
const mergeSource = ref("");

function onBranchMergeClick(b: {
  name: string;
  current: boolean;
  remote: boolean;
}): void {
  if (b.current || b.remote) return;
  mergeSource.value = b.name;
  mergeDialogOpen.value = true;
}

async function onMergeSubmit(params: {
  source: string;
  noFf: boolean;
  ffOnly: boolean;
  squash: boolean;
  message: string;
}): Promise<void> {
  const result = await gitMerge.merge({
    source: params.source,
    noFf: params.noFf,
    ffOnly: params.ffOnly,
    squash: params.squash,
    message: params.message || undefined,
    worktree: selectedWorktree.value,
  });
  if (result.ok) {
    mergeDialogOpen.value = false;
    await refreshAfterBranchChange();
    if (result.squash) {
      showSnackbar(
        tm("spcodeProjectLoad.diffSidebar.merge.squashSuccess", {
          source: params.source,
          count: result.filesTouched.length,
        }),
        "success",
      );
    } else {
      showSnackbar(
        tm(
          result.fastForward
            ? "spcodeProjectLoad.diffSidebar.merge.successFf"
            : "spcodeProjectLoad.diffSidebar.merge.success",
          { source: params.source, sha: result.mergeSha.slice(0, 7) },
        ),
        "success",
      );
    }
    return;
  }
  const meta = classifyMergeReason(result.reason);
  if (result.conflict) {
    // Conflict: close the dialog and light up the conflict banner
    // immediately instead of waiting for the next 30 s poll.
    mergeDialogOpen.value = false;
    branchMenuOpen.value = false;
    void gitConflict.refresh();
    showSnackbar(
      tm(meta.i18nKey, {
        source: params.source,
        count: result.conflictedFiles.length,
      }),
      "warning",
    );
    return;
  }
  showSnackbar(
    tm(meta.i18nKey, { reason: result.reason, stderr: result.stderr ?? "" }),
    meta.color,
    meta.withStderr ? result.stderr : undefined,
  );
}

// 2026-08-01 git-cherry-pick: dialog state. preset != null → log-row
// mode (readonly ref); preset == null → toolbar mode (editable ref).
const cherryPickDialogOpen = ref(false);
const cherryPickPreset = ref<{ sha: string; subject: string } | null>(null);

function onLogCherryPickRequest(commit: {
  sha: string;
  subject: string;
}): void {
  cherryPickPreset.value = commit;
  cherryPickDialogOpen.value = true;
}

function onToolbarCherryPickRequest(): void {
  cherryPickPreset.value = null;
  cherryPickDialogOpen.value = true;
}

async function onCherryPickSubmit(params: {
  ref: string;
  mainline: number | null;
}): Promise<void> {
  const result = await gitMerge.cherryPick({
    ref: params.ref,
    mainline: params.mainline,
    worktree: selectedWorktree.value,
  });
  if (result.ok) {
    cherryPickDialogOpen.value = false;
    await refreshAfterBranchChange();
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.cherryPick.success", {
        ref: params.ref.slice(0, 12),
        sha: result.newSha.slice(0, 7),
      }),
      "success",
    );
    return;
  }
  const meta = classifyCherryPickReason(result.reason);
  if (result.conflict) {
    cherryPickDialogOpen.value = false;
    void gitConflict.refresh();
    showSnackbar(
      tm(meta.i18nKey, {
        ref: params.ref.slice(0, 12),
        count: result.conflictedFiles.length,
      }),
      "warning",
    );
    return;
  }
  showSnackbar(
    tm(meta.i18nKey, {
      reason: result.reason,
      ref: params.ref.slice(0, 12),
      stderr: result.stderr ?? "",
    }),
    meta.color,
    meta.withStderr ? result.stderr : undefined,
  );
}

// 2026-08-12 git-pull/push/remote-set-url: remote sync actions for the
// branch-management row (docs/api/webapi-git-pull-push-remote-api.md).
// Pull goes through a small options dialog (merge / --ff-only /
// --rebase); push runs directly (the endpoint has no options — no
// force push, no tags); the remote URL dialog upserts the origin URL.
const pullDialogOpen = ref(false);
const pushDialogOpen = ref(false);
const remoteUrlDialogOpen = ref(false);
// 2026-08-16: 已配置远端 (name + url) 列表, 供 GitRemoteUrlDialog
// 展示与删除。打开对话框时加载; 设置/删除成功后重载。
const remoteList = ref<SpcodeRemote[]>([]);
const remoteRemoving = ref<string | null>(null);

// Load the remote list every time the remote-URL dialog opens so the
// shown remotes never go stale (git remote changes are not polled).
watch(remoteUrlDialogOpen, (open) => {
  if (open) void loadRemotes();
});

// ahead/behind lives in branchesComposable, so a remote sync must
// refresh it on top of the standard post-branch-change fan-out.
async function refreshAfterRemoteSync(): Promise<void> {
  await Promise.allSettled([
    branchesComposable.refresh(),
    refreshAfterBranchChange(),
  ]);
}

async function onPullSubmit(params: {
  ffOnly: boolean;
  rebase: boolean;
}): Promise<void> {
  const result = await gitRemoteSync.pull({
    ffOnly: params.ffOnly,
    rebase: params.rebase,
    worktree: selectedWorktree.value,
  });
  if (result.ok) {
    pullDialogOpen.value = false;
    await refreshAfterRemoteSync();
    if (!result.updated) {
      showSnackbar(tm("spcodeProjectLoad.diffSidebar.pull.upToDate"), "info");
      return;
    }
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.pull.success", {
        count: result.filesTouched.length,
        sha: result.afterSha.slice(0, 7) || "?",
      }),
      "success",
    );
    return;
  }
  const meta = classifyPullReason(result.reason);
  if (result.conflict) {
    // Conflict: close the dialog and light up the conflict banner
    // immediately instead of waiting for the next 30 s poll.
    pullDialogOpen.value = false;
    void gitConflict.refresh();
    showSnackbar(
      tm(meta.i18nKey, { count: result.conflictedFiles.length }),
      "warning",
    );
    return;
  }
  showSnackbar(
    tm(meta.i18nKey, { reason: result.reason, stderr: result.stderr ?? "" }),
    meta.color,
    meta.withStderr ? result.stderr : undefined,
  );
}

// ── 2026-08-21 git-stash: stash current changes + browse stashes ──
// The stash dialog combines both halves of the endpoint: POST push -u
// for the working tree, GET list for the existing entries (with their
// per-file numstat details). List loads on dialog open only — stash
// changes are not polled.
const stashDialogOpen = ref(false);

watch(stashDialogOpen, (open) => {
  if (open) void gitStash.refreshList();
});

const stashEntries = computed(() => {
  const s = gitStash.listState.value;
  return s.kind === "ok" ? s.snapshot.stashes : [];
});
const stashListTruncated = computed(() => {
  const s = gitStash.listState.value;
  return s.kind === "ok" && s.snapshot.truncated;
});
const stashLoadError = computed(() => {
  const s = gitStash.listState.value;
  return s.kind === "error" ? s.reason : null;
});

// Gates the dialog's stash button, mirroring the backend's
// nothing_to_stash pre-check. Reads the authoritative dirty flag from
// git-status (scope-independent; total includes untracked).
const hasLocalChanges = computed(() => {
  const s = gitStatus.state.value;
  if (s.kind !== "ok") return false;
  return s.snapshot.summary.total > 0;
});

async function onStashSubmit(params: { message: string }): Promise<void> {
  const result = await gitStash.stash({
    message: params.message || undefined,
    worktree: selectedWorktree.value,
    umo: spcodeStatus.status.value.umo,
  });
  if (isAborted(result)) return;
  if (result.ok) {
    // The changes left the worktree: refresh diff + status so the rows
    // disappear immediately, and the stash list so the new entry shows.
    await Promise.all([
      gitStash.refreshList(),
      composable.refresh(),
      gitStatus.refresh(),
    ]);
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.stash.stashed", {
        count: result.snapshot.fileCount,
        ref: result.snapshot.ref,
      }),
      "success",
    );
    return;
  }
  const meta = classifyStashReason(result.reason);
  showSnackbar(
    tm(meta.i18nKey, { reason: result.reason, stderr: result.stderr ?? "" }),
    meta.color,
    meta.withStderr ? result.stderr : undefined,
  );
}

// 2026-08-21 git-stash-pop: apply a stash entry back onto the working
// tree (git drops the entry on a clean apply; on conflict the entry is
// kept and the reason is stash_conflict).
async function onStashPop(params: { index: number; ref: string }): Promise<void> {
  const result = await gitStash.pop({
    index: params.index,
    ref: params.ref,
    worktree: selectedWorktree.value,
    umo: spcodeStatus.status.value.umo,
  });
  if (isAborted(result)) return;
  if (result.ok) {
    // The changes are back in the worktree: refresh diff + status so
    // the rows appear immediately, and the stash list so the popped
    // entry disappears (indices after it shift down by one).
    await Promise.all([
      gitStash.refreshList(),
      composable.refresh(),
      gitStatus.refresh(),
    ]);
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.stash.popped", {
        count: result.snapshot.fileCount,
        ref: result.snapshot.ref,
      }),
      "success",
    );
    return;
  }
  const meta = classifyStashReason(result.reason);
  showSnackbar(
    tm(meta.i18nKey, { reason: result.reason, stderr: result.stderr ?? "" }),
    meta.color,
    meta.withStderr ? result.stderr : undefined,
  );
}

// 2026-08-21 git-stash-drop: delete a stash entry. The worktree is
// untouched, so only the stash list needs a refresh.
async function onStashDrop(params: { index: number; ref: string }): Promise<void> {
  const result = await gitStash.drop({
    index: params.index,
    ref: params.ref,
    worktree: selectedWorktree.value,
    umo: spcodeStatus.status.value.umo,
  });
  if (isAborted(result)) return;
  if (result.ok) {
    await gitStash.refreshList();
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.stash.dropped", {
        ref: result.snapshot.ref,
        count: result.snapshot.stashCount,
      }),
      "success",
    );
    return;
  }
  const meta = classifyStashReason(result.reason);
  showSnackbar(
    tm(meta.i18nKey, { reason: result.reason, stderr: result.stderr ?? "" }),
    meta.color,
    meta.withStderr ? result.stderr : undefined,
  );
}

// 2026-08-13: push 现在走确认对话框 (onPushSubmit)；原一键直推的
// onPushClick 已移除，避免未使用的直推入口残留。

// 2026-08-13 git-push dialog: confirmation dialog submit handler.
// The dialog lets the user pick the remote, the local branch to push
// and an optional remote target branch (different-name push).
async function onPushSubmit(params: {
  remote: string;
  branch: string;
  remoteBranch: string;
}): Promise<void> {
  const result = await gitRemoteSync.push({
    remote: params.remote,
    branch: params.branch,
    ...(params.remoteBranch ? { remoteBranch: params.remoteBranch } : {}),
    worktree: selectedWorktree.value,
  });
  if (result.ok) {
    pushDialogOpen.value = false;
    await refreshAfterRemoteSync();
    if (!result.pushed) {
      showSnackbar(tm("spcodeProjectLoad.diffSidebar.push.upToDate"), "info");
      return;
    }
    showSnackbar(
      tm(
        result.setUpstream
          ? "spcodeProjectLoad.diffSidebar.push.successSetUpstream"
          : "spcodeProjectLoad.diffSidebar.push.success",
        {
          remoteBranch: result.remoteBranch || result.upstream || result.branch,
          upstream: result.upstream ?? "",
        },
      ),
      "success",
    );
    return;
  }
  const meta = classifyPushReason(result.reason);
  showSnackbar(
    tm(meta.i18nKey, { reason: result.reason, stderr: result.stderr ?? "" }),
    meta.color,
    meta.withStderr ? result.stderr : undefined,
  );
}

async function onRemoteUrlSubmit(params: {
  remote: string;
  url: string;
}): Promise<void> {
  const result = await gitRemoteSync.setRemoteUrl({
    remote: params.remote,
    url: params.url,
    worktree: selectedWorktree.value,
  });
  if (result.ok) {
    // 2026-08-16: 设置成功后保持对话框打开, 刷新列表后用户可继续
    // 新增/编辑/删除其他 remote (不再像旧版那样整窗关闭)。
    // 同时回刷分支/远端数据 (与 pull/push 对齐) —— 否则 git-branches
    // 的 ETag 不感知 remote 配置变化, 推送对话框的远端列表不更新。
    await refreshAfterRemoteSync();
    await loadRemotes();
    showSnackbar(
      tm(
        result.action === "added"
          ? "spcodeProjectLoad.diffSidebar.remote.successAdded"
          : result.action === "unchanged"
          ? "spcodeProjectLoad.diffSidebar.remote.successUnchanged"
          : "spcodeProjectLoad.diffSidebar.remote.successUpdated",
        { remote: result.remote },
      ),
      "success",
    );
    return;
  }
  const meta = classifyRemoteReason(result.reason);
  showSnackbar(
    tm(meta.i18nKey, { reason: result.reason, stderr: result.stderr ?? "" }),
    meta.color,
    meta.withStderr ? result.stderr : undefined,
  );
}

// 2026-08-16: load the configured-remotes list (name + url) shown by
// the remote-URL dialog. Failures surface as a snackbar and keep the
// previous list (dialog stays usable for upsert).
async function loadRemotes(): Promise<void> {
  const result = await gitRemoteSync.listRemotes();
  if (result.ok) {
    remoteList.value = result.remotes;
    return;
  }
  const meta = classifyRemoteReason(result.reason);
  showSnackbar(
    tm(meta.i18nKey, { reason: result.reason, stderr: result.stderr ?? "" }),
    meta.color,
    meta.withStderr ? result.stderr : undefined,
  );
}

// 2026-08-16: remove a configured remote. On success the remote-URL
// dialog list and the push dialog's remote dropdown must both refresh.
async function onRemoteRemove(name: string): Promise<void> {
  remoteRemoving.value = name;
  try {
    const result = await gitRemoteSync.removeRemote(name);
    if (result.ok) {
      remoteList.value = remoteList.value.filter((r) => r.name !== name);
      await refreshAfterRemoteSync();
      showSnackbar(
        tm("spcodeProjectLoad.diffSidebar.remote.successRemoved", {
          remote: name,
        }),
        "success",
      );
      return;
    }
    const meta = classifyRemoteReason(result.reason);
    showSnackbar(
      tm(meta.i18nKey, { reason: result.reason, stderr: result.stderr ?? "" }),
      meta.color,
      meta.withStderr ? result.stderr : undefined,
    );
  } finally {
    remoteRemoving.value = null;
  }
}

// 2026-08-01 git-conflict: panel event fan-out (spec §4.5 refresh matrix).
function onConflictFailed(payload: {
  reason: string;
  stderr?: string;
  count?: number;
}): void {
  const meta = classifyConflictReason(payload.reason);
  showSnackbar(
    tm(meta.i18nKey, {
      reason: payload.reason,
      stderr: payload.stderr ?? "",
      count: payload.count ?? 0,
    }),
    meta.color,
    meta.withStderr ? payload.stderr : undefined,
  );
}

async function onConflictResolved(): Promise<void> {
  // A file was rewritten + staged on disk → refresh the diff/status views.
  await Promise.allSettled([composable.refresh(), gitStatus.refresh()]);
}

// 2026-08-16: the conflict panel can take over the whole sidebar when
// expanded — track that here so the sidebar's own body hides while
// the panel is open and releases when it collapses.
const conflictBodyHidden = ref(false);
function onConflictExpandChange(open: boolean): void {
  conflictBodyHidden.value = open;
}
// Safety net: if the conflict operation ends (continue/abort) while
// the panel is expanded, force the sidebar body back into view even
// though the panel unmounts without emitting expand-change(false).
watch(
  () => gitConflict.state.value,
  (s) => {
    if (s.kind === "ok" && !s.snapshot.inConflict) {
      conflictBodyHidden.value = false;
    }
  },
);

async function onConflictCompleted(): Promise<void> {
  // continue/abort moved or restored HEAD → full cascade.
  await refreshAfterBranchChange();
}

async function onBranchDeleteConfirm(name: string): Promise<void> {
  isBranchDeleting.value = true;
  try {
    const params: BranchDeleteParams = { name };
    const result = await branchesComposable.delete(params);
    if (!result.ok) {
      showBranchError("delete", result.reason, result.stderr ?? "");
      return;
    }
    deleteDialogOpen.value = false;
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.branchMgmt.delete.success", { name }),
      "success",
    );
  } finally {
    isBranchDeleting.value = false;
  }
}

async function onBranchCreateSubmit(): Promise<void> {
  const name = branchCreateName.value.trim();
  if (!name) {
    branchCreateError.value = tm(
      "spcodeProjectLoad.diffSidebar.branchMgmt.create.nameRequired",
    );
    return;
  }
  branchCreateError.value = null;
  isBranchCreating.value = true;
  try {
    const result = await branchesComposable.create({
      name,
      startPoint: branchCreateStartPoint.value.trim() || "HEAD",
    });
    if (!result.ok) {
      branchCreateError.value = tm(
        (BRANCH_ERROR_KEYS.create as Record<string, string>)[result.reason] ??
          BRANCH_ERROR_KEYS.create.git_error,
        { name, stderr: result.stderr ?? "" },
      );
      return;
    }
    // Success: collapse the form, keep the menu open.
    branchCreateExpanded.value = false;
    branchCreateName.value = "";
    branchCreateStartPoint.value = "HEAD";
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.branchMgmt.create.success", { name }),
      "success",
    );
  } finally {
    isBranchCreating.value = false;
  }
}

// ── Worktree management handlers (spec 2026-06-27 §3) ────────

function openCreateDialog(): void {
  // 互斥：开 ADD 关其他
  removeDialogOpen.value = false;
  lockDialogOpen.value = false;
  confirmUnlockOpen.value = false;
  lastCreateError.value = null;
  createDialogOpen.value = true;
}

async function onCreateSubmit(params: WorktreeAddParams): Promise<void> {
  isCreating.value = true;
  lastCreateError.value = null;
  const result = await worktreesComposable.add(params);
  isCreating.value = false;
  if (isAborted(result)) {
    createDialogOpen.value = false;
    return;
  }
  if (result.ok) {
    // spec §7.4: 自动切到新 worktree + 切到 Files 视图
    const newWt = result.snapshot.worktrees.find((w) => w.path === params.path);
    if (newWt) {
      selectedWorktree.value = newWt.isMain ? null : newWt.path;
      viewMode.value = "files";
      fileBrowserCurrentPath.value = newWt.path;
      fileBrowserPreviewPath.value = null;
    }
    createDialogOpen.value = false;
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.worktreeMgmt.create.success", {
        branch: params.branch ?? newWt?.branch ?? "",
      }),
      "success",
    );
  } else {
    // 用 classifyWorktreeReason 走统一错误处理
    const meta = classifyWorktreeReason(result.reason, "add");
    const key = `spcodeProjectLoad.diffSidebar.${meta.i18nKey}`;
    const message = meta.withReason
      ? tm(key, { reason: result.reason })
      : tm(key);
    lastCreateError.value = {
      reason: result.reason,
      stderr: result.stderr ?? "",
    };
    showSnackbar(
      message,
      meta.color,
      meta.withStderr ? result.stderr : undefined,
    );
  }
}

function onLockClick(wt: SpcodeGitWorktree): void {
  contextMenu.value.open = false;
  if (wt.locked) {
    // 直接进入 unlock 流程
    confirmUnlockPath.value = wt.path;
    removeDialogOpen.value = false;
    createDialogOpen.value = false;
    lockDialogOpen.value = false;
    confirmUnlockOpen.value = true;
    return;
  }
  // Lock 流程：弹窗让用户填 reason
  lockDialogTarget.value = { path: wt.path, branch: wt.branch };
  removeDialogOpen.value = false;
  createDialogOpen.value = false;
  confirmUnlockOpen.value = false;
  lockDialogOpen.value = true;
}

async function onLockSubmit(reason: string | null): Promise<void> {
  const target = lockDialogTarget.value;
  if (!target) return;
  isLocking.value = true;
  const params: WorktreeLockParams = {
    path: target.path,
    umo: spcodeStatus.status.value.umo,
  };
  if (reason) params.reason = reason;
  const result = await worktreesComposable.lock(params);
  isLocking.value = false;
  if (isAborted(result)) {
    lockDialogOpen.value = false;
    return;
  }
  if (result.ok) {
    lockDialogOpen.value = false;
    lockDialogTarget.value = null;
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.worktreeMgmt.lock.success", {
        branch: target.branch ?? "",
      }),
      "success",
    );
  } else {
    const meta = classifyWorktreeReason(result.reason, "lock");
    const key = `spcodeProjectLoad.diffSidebar.${meta.i18nKey}`;
    const message = meta.withReason
      ? tm(key, { reason: result.reason })
      : tm(key);
    showSnackbar(
      message,
      meta.color,
      meta.withStderr ? result.stderr : undefined,
    );
  }
}

function onRemoveClick(wt: SpcodeGitWorktree): void {
  contextMenu.value.open = false;
  if (wt.isMain) return; // 双保险
  if (wt.locked) return; // 双保险
  removeDialogTarget.value = { path: wt.path, branch: wt.branch };
  dirtyCount.value = null;
  // Lazy load dirty count from /spcode/git-status
  void loadDirtyFor(wt);
  lockDialogOpen.value = false;
  createDialogOpen.value = false;
  confirmUnlockOpen.value = false;
  removeDialogOpen.value = true;
}

async function loadDirtyFor(wt: SpcodeGitWorktree): Promise<void> {
  const umo = spcodeStatus.status.value.umo;
  if (!umo) return;
  try {
    // Backend body is { status, data: { files, summary: { total } } };
    // axios unwraps to ApiEnvelope<T> (see @/api/v1.ts), so resp.data
    // is the envelope and we read resp.data.data.summary.total for the
    // authoritative dirty file count.
    const resp = await pluginExtensionApi.get<{
      files?: Array<unknown>;
      summary?: { total?: number };
    }>("spcode/git-status", { params: { umo, worktree: wt.path } });
    const data = resp.data?.data;
    dirtyCount.value = data?.summary?.total ?? data?.files?.length ?? 0;
  } catch {
    dirtyCount.value = null; // 不阻塞 UI
  }
}

async function onConfirmRemove(force: boolean): Promise<void> {
  const target = removeDialogTarget.value;
  if (!target) return;
  isRemoving.value = true;
  const result = await worktreesComposable.remove({
    path: target.path,
    force,
    umo: spcodeStatus.status.value.umo,
  });
  isRemoving.value = false;
  if (isAborted(result)) {
    removeDialogOpen.value = false;
    return;
  }
  if (result.ok) {
    // spec §7.5: 若被删的是当前 worktree,回退到主 worktree
    if (selectedWorktree.value === target.path) {
      selectedWorktree.value = null;
      // projectRoot.value is string|null; coerce to string with final fallback
      fileBrowserCurrentPath.value =
        mainWorktreePath.value ?? projectRoot.value ?? "";
    }
    removeDialogOpen.value = false;
    removeDialogTarget.value = null;
    dirtyCount.value = null;
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.worktreeMgmt.remove.success", {
        branch: target.branch ?? "",
      }),
      "success",
    );
  } else {
    const meta = classifyWorktreeReason(result.reason, "remove");
    const key = `spcodeProjectLoad.diffSidebar.${meta.i18nKey}`;
    const message = meta.withReason
      ? tm(key, { reason: result.reason })
      : tm(key);
    showSnackbar(
      message,
      meta.color,
      meta.withStderr ? result.stderr : undefined,
    );
  }
}

async function onConfirmUnlock(): Promise<void> {
  const path = confirmUnlockPath.value;
  if (!path) return;
  isUnlocking.value = true;
  const result = await worktreesComposable.unlock({
    path,
    umo: spcodeStatus.status.value.umo,
  });
  isUnlocking.value = false;
  if (isAborted(result)) {
    confirmUnlockOpen.value = false;
    return;
  }
  if (result.ok) {
    confirmUnlockOpen.value = false;
    confirmUnlockPath.value = null;
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.worktreeMgmt.unlock.success"),
      "success",
    );
  } else {
    const meta = classifyWorktreeReason(result.reason, "unlock");
    const key = `spcodeProjectLoad.diffSidebar.${meta.i18nKey}`;
    const message = meta.withReason
      ? tm(key, { reason: result.reason })
      : tm(key);
    showSnackbar(
      message,
      meta.color,
      meta.withStderr ? result.stderr : undefined,
    );
  }
}

function onCancelUnlock(): void {
  if (isUnlocking.value) return;
  confirmUnlockOpen.value = false;
  confirmUnlockPath.value = null;
}

// Spec 2026-06-20 §3.4: clicking a scope pill updates the ref and
// stamps `pendingScope` so the UI can show a spinner + lock the
// other two pills. The composable's internal watcher re-fires the
// request, so we don't need to call refresh() here.
function onScopeChange(scope: GitDiffScope): void {
  if (scope === selectedScope.value) return;
  selectedScope.value = scope;
  pendingScope.value = scope;
}

// Navigation payload from FileBrowserView: dirPath is the directory
// whose entries appear in the left pane; previewPath is an optional
// file to display in the right pane (null = show directory hint).
// FileBrowserEntryList sends previewPath=null for directory clicks
// and previewPath=file.path for file/symlink clicks.
function onFileBrowserNavigate(payload: {
  dirPath: string;
  previewPath: string | null;
}): void {
  fileBrowserCurrentPath.value = payload.dirPath;
  fileBrowserPreviewPath.value = payload.previewPath;
  // 2026-07-02: clear any pending search-result scroll target.
  // The user is navigating manually, not via a search result;
  // leaving fileSearchScrollToLine set would re-scroll the next
  // file the user opens to the line from a prior search click.
  fileSearchScrollToLine.value = null;
}

// 2026-07-02 sidebar-search: handle a "click this result" from
// SearchPanel (forwards via FileBrowserView).
//
// 2026-07-27 search-abs-path-fix (elecvoid243): payload.path is
// REPO-RELATIVE (POSIX separators, relative to the ACTIVE worktree
// root) — both the filename endpoint (file_name_search.py) and the
// content endpoint (file_search.py) return os.path.relpath()-style
// paths. The old comment here claimed the search composable joined
// the worktree root, but that join was never implemented, so the
// relative path flowed verbatim into fileBrowserPreviewPath and the
// /spcode/file-browser fetch resolved it against the AstrBot process
// CWD — working only when CWD happened to equal the project root.
// Anchor relative paths to currentRoot (selected worktree ?? main
// checkout) before assigning; mirrors the onOpenFile() glue below.
//
// Side effects:
//   1. fileBrowserPreviewPath = absolute path — FileBrowserView reacts
//      and triggers content fetch + scroll-to-line.
//   2. fileBrowserCurrentPath = dirOf(payload.path) — so the breadcrumb
//      shows the file's directory rather than the file itself, AND so
//      the left pane stays usable as a sibling listing.
//   3. (2026-07-18) closing the search panel moved INTO
//      FileBrowserView's onSearchOpenFile together with the rest of
//      the search UI — the panel REPLACES the file-browser body, so
//      it closes itself before this handler runs. The toolbar input
//      retains the query string, so the user can re-open the search
//      panel with one click on the magnifier.
//   4. fileSearchScrollToLine = payload.line — the 1-based target
//      line. 0 (filename mode) is normalized to null (= no scroll).
//      Propagated down to FileBrowserCodeView, which centers the
//      target line in the code-view scroll container.
function onFileOpen(payload: { path: string; line: number }): void {
  let abs = payload.path;
  // Drive-letter (C:\ / C:/), UNC (\\) and POSIX-rooted (/) paths are
  // already absolute; anything else is worktree-relative and must be
  // anchored (search results are always POSIX-separator relative).
  const isAbsolute = /^([a-zA-Z]:[\\/]|\\\\|\/)/.test(abs);
  if (!isAbsolute) {
    const root = currentRoot.value;
    // No project / worktree resolved yet → nothing to anchor to.
    // Skip rather than writing a relative path that file-browser
    // would resolve against the wrong base.
    if (!root) return;
    // Detect separator from the root (backend sends Windows `\`;
    // search results are POSIX `/`).
    const sep = root.includes("\\") ? "\\" : "/";
    const cleanRoot = root.replace(/[\\/]+$/, "");
    const cleanPath = abs.replace(/^[\\/]+/, "").replace(/\//g, sep);
    abs = cleanPath ? `${cleanRoot}${sep}${cleanPath}` : cleanRoot;
  }
  fileBrowserPreviewPath.value = abs;
  // POSIX + Windows separator: strip the trailing filename. Use a
  // single regex that matches either separator so the same code
  // works for both *nix and Windows worktree paths.
  const dir = abs.replace(/[\\/][^\\/]+$/, "");
  // Guard against degenerate empty dir (root file) and against an
  // unnecessary write if we're already pointing at this directory
  // (would re-trigger the persistCurrentPath debounce + a fetch).
  if (dir && dir !== fileBrowserCurrentPath.value) {
    fileBrowserCurrentPath.value = dir;
  }
  fileSearchScrollToLine.value = payload.line > 0 ? payload.line : null;
}

// Spec §5.3 + §6.4.1: 12 reason → i18n key. Reasons not in the map fall
// through to `error.reason.unknown` (caller passes raw reason to tm()).
const DISCARD_HUNK_REASON_I18N_KEYS: Record<string, string> = {
  patch_check_failed:
    "spcodeProjectLoad.diffSidebar.discardHunk.error.reason.patch_check_failed",
  patch_apply_failed:
    "spcodeProjectLoad.diffSidebar.discardHunk.error.reason.patch_apply_failed",
  patch_too_large:
    "spcodeProjectLoad.diffSidebar.discardHunk.error.reason.patch_too_large",
  patch_malformed:
    "spcodeProjectLoad.diffSidebar.discardHunk.error.reason.patch_malformed",
  not_modified:
    "spcodeProjectLoad.diffSidebar.discardHunk.error.reason.not_modified",
  untracked_file:
    "spcodeProjectLoad.diffSidebar.discardHunk.error.reason.untracked_file",
  multi_file_patch:
    "spcodeProjectLoad.diffSidebar.discardHunk.error.reason.multi_file_patch",
  patch_binary:
    "spcodeProjectLoad.diffSidebar.discardHunk.error.reason.patch_binary",
  no_project_loaded:
    "spcodeProjectLoad.diffSidebar.discardHunk.error.reason.no_project_loaded",
  worktree_invalid:
    "spcodeProjectLoad.diffSidebar.discardHunk.error.reason.worktree_invalid",
  not_a_git_repo:
    "spcodeProjectLoad.diffSidebar.discardHunk.error.reason.not_a_git_repo",
  git_unavailable:
    "spcodeProjectLoad.diffSidebar.discardHunk.error.reason.git_unavailable",
};

function classifySnackbarLevel(
  reason: string,
): "success" | "info" | "warning" | "error" {
  const FATAL = new Set([
    "not_a_git_repo",
    "git_unavailable",
    "feature_disabled",
  ]);
  const RETRY = new Set([
    "patch_apply_failed",
    "patch_check_failed",
    "git_error",
  ]);
  if (FATAL.has(reason)) return "error";
  if (RETRY.has(reason)) return "info";
  return "warning"; // user + config + unknown
}

// Spec §3.2 data flow: GitDiffFileItem -> GitDiffBodyContent -> here.
function onFileRestore(path: string): void {
  // ORDER MATTERS: set the path FIRST, then open the dialog.
  // `confirmTargetIsNew` is a computed derived from `confirmTargetPath`
  // (and `newFilePaths`), so its value is correct as soon as the path
  // is set. Opening the dialog while the path still held the previous
  // file's value would render the old file's message for one frame.
  confirmTargetPath.value = path;
  confirmDialogOpen.value = true;
}

/** Handles the "view file" button on a diff row: switches the sidebar
 *  to the Files tab and navigates to the file's parent directory with
 *  the file opened in the preview pane.
 *
 *  `path` arrives as a repo-relative path from git-diff (e.g.
 *  "dashboard/src/components/chat/GitDiffSidebar.vue"). The
 *  /spcode/file-browser backend requires absolute paths, and
 *  FileBrowserBreadcrumb's `normCurrent.startsWith(normRoot)` guard
 *  returns [] (no segments rendered) when the two don't share a
 *  common prefix — so passing the relative path through verbatim
 *  silently breaks the multi-level breadcrumb and misroutes the
 *  directory listing. Anchor to currentRoot (the active worktree or
 *  project root) before computing the parent. */
function onOpenFile(path: string): void {
  const root = currentRoot.value;
  // No project / worktree resolved yet → nothing to navigate to.
  // Skip silently rather than writing a relative path that would
  // corrupt the persisted currentPath.
  if (!root) return;
  // Detect separator from the root, since `root` comes from the
  // backend (Windows: `\`) and `path` comes from git (POSIX: `/`).
  const sep = root.includes("\\") ? "\\" : "/";
  const cleanRoot = root.replace(/[\\/]+$/, "");
  const cleanPath = path.replace(/^[\\/]+/, "").replace(/\//g, sep);
  const absolute = cleanPath ? `${cleanRoot}${sep}${cleanPath}` : cleanRoot;
  // Parent directory of the absolute path, using the same separator
  // style as the root. Falls back to the root when `path` had no
  // separator (top-level file like "README.md").
  const parent = absolute.includes(sep)
    ? absolute.substring(0, absolute.lastIndexOf(sep))
    : cleanRoot;
  viewMode.value = "files";
  fileBrowserCurrentPath.value = parent;
  fileBrowserPreviewPath.value = absolute;
  // Clear any pending search-result scroll target so the file does not
  // jump to a stale line from a prior search click. Mirrors
  // onFileBrowserNavigate which does the same on manual navigation.
  fileSearchScrollToLine.value = null;
}

function onCancelRestore(): void {
  // Close the dialog FIRST so the v-if-guarded v-card unmounts
  // synchronously — no in-between frame where v-card-text could
  // re-render with the previous file's flag.
  confirmDialogOpen.value = false;
  // Path cleanup happens in a microtask AFTER the close transition
  // begins, so the v-card is already gone (v-if="confirmDialogOpen"
  // unmounts it) by the time `confirmTargetIsNew` would flip to
  // false. The reset is still important for the next open: if we
  // skipped it, the next `onFileRestore` would see a stale path
  // (Vue would still pick the new value, but defensive nulling keeps
  // the state shape tidy).
  void nextTick(() => {
    confirmTargetPath.value = null;
  });
}

async function onConfirmRestore(): Promise<void> {
  const path = confirmTargetPath.value;
  if (!path) return;
  // Same pattern as onCancelRestore: close dialog first, clear path
  // after the next microtask. The v-if guard means no v-card lives
  // across the state change, so the user never sees the wrong
  // message text during the close transition.
  confirmDialogOpen.value = false;
  void nextTick(() => {
    confirmTargetPath.value = null;
  });
  restoringFile.value = path;
  const umo = spcodeStatus.status.value.umo;
  const worktree = selectedWorktree.value;
  const result: RestoreResult = await fileRestore.restore({
    file: path,
    worktree,
    umo,
  });
  restoringFile.value = null;
  // Special-case "aborted" (Chunk 2 review note): no toast, just reset state.
  // This only fires during teardown (pre-mount guard / post-await unmount / axios cancel).
  if (!result.ok && result.reason === "aborted") {
    return;
  }
  if (result.ok) {
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.restore.success", { path }),
      "success",
    );
    // Spec §3.2: success -> immediate refresh so the row disappears.
    await refreshDiffAndStatus();
  } else {
    const mapping =
      RESTORE_REASON_I18N_KEYS[result.reason] ??
      RESTORE_REASON_I18N_KEYS.unknown;
    const message =
      mapping.key ===
      "spcodeProjectLoad.diffSidebar.restore.error.reason.git_error"
        ? tm(mapping.key, { stderr: result.stderr ?? "" })
        : tm(mapping.key);
    showSnackbar(message, mapping.color);
  }
}

// ── Bulk restore (multi-select from GitDiffBodyContent) ────────────
//
// GitDiffBodyContent emits `restore-paths` with the array of currently
// selected paths. We open a dedicated confirmation dialog (showing the
// file count) and, on confirm, iterate one file at a time because the
// file-restore endpoint accepts a single `file` per request. The
// snackbar aggregates the result so the user sees a single toast
// (success / partial / all-failed) rather than N toasts.
function onRestorePaths(paths: string[]): void {
  if (paths.length === 0) return;
  pendingRestorePaths.value = paths.slice();
  confirmRestorePathsOpen.value = true;
}

function onCancelRestorePaths(): void {
  if (isBulkRestoring.value) return;
  confirmRestorePathsOpen.value = false;
  pendingRestorePaths.value = [];
}

async function onConfirmRestorePaths(): Promise<void> {
  const paths = pendingRestorePaths.value;
  if (paths.length === 0) return;
  confirmRestorePathsOpen.value = false;
  // Snapshot the list before clearing state so concurrent scope/worktree
  // switches (which would reset pendingRestorePaths via teardown) cannot
  // shorten the iteration.
  const toRestore = paths.slice();
  pendingRestorePaths.value = [];
  const umo = spcodeStatus.status.value.umo;
  const worktree = selectedWorktree.value;
  isBulkRestoring.value = true;
  let successCount = 0;
  let failedCount = 0;
  let aborted = false;
  try {
    for (const path of toRestore) {
      restoringFile.value = path;
      const result: RestoreResult = await fileRestore.restore({
        file: path,
        worktree,
        umo,
      });
      restoringFile.value = null;
      if (!result.ok) {
        if (result.reason === "aborted") {
          aborted = true;
          break;
        }
        failedCount++;
        continue;
      }
      successCount++;
    }
  } finally {
    restoringFile.value = null;
    isBulkRestoring.value = false;
  }
  // Aborted usually means the component is unmounting or the user
  // switched scope/worktree; skip the snackbar and leave the
  // selection in whatever state it ended up in (the new view will
  // rebuild it from scratch anyway).
  if (aborted) {
    return;
  }
  // Drop the toolbar's "已选 N 个" counter on a full success so the
  // user starts fresh; on partial / all-failed, keep the selection
  // so they can retry without re-ticking every box.
  if (successCount > 0 && failedCount === 0) {
    gitDiffBodyRef.value?.clearSelection();
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.restore.successMultiple", {
        count: successCount,
      }),
      "success",
    );
    await refreshDiffAndStatus();
  } else if (successCount > 0 && failedCount > 0) {
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.restore.successPartial", {
        success: successCount,
        total: toRestore.length,
        failed: failedCount,
      }),
      "warning",
    );
    await refreshDiffAndStatus();
  } else {
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.restore.error.allFailed", {
        count: toRestore.length,
      }),
      "error",
    );
  }
}

// Spec 2026-07-07 §3.3: hunk discard handler. Child (GitDiffFileItem)
// invokes this through the `onDiscardHunk` callback prop with a single
// object arg (matching the DiffPreview prop signature threaded through
// GitDiffFileItem → GitDiffBodyContent). Result drives both the
// snackbar toast and a refresh of the diff so the discarded hunk
// visually disappears. Mirrors the onConfirmRestore success /
// failure / aborted pattern.
async function onDiscardHunk(params: {
  file: string;
  hunkIndex: number;
  patchText: string;
  // v2 (2026-07-21): active diff scope forwarded down from
  // DiffPreview → FileItem → BodyContent so the backend can pick
  // the right `git apply --reverse[ --cached]` path without
  // re-deriving it from porcelain. When `scope` is missing (legacy
  // ToolCallCard / FilePatchPanel callers) the backend falls back
  // to the v1 auto-detect.
  scope: GitDiffScope;
}): Promise<void> {
  const { file, hunkIndex, patchText, scope } = params;
  const umo = spcodeStatus.status.value.umo;
  if (!umo) return;
  const worktree = selectedWorktree.value;
  const result: DiscardHunkResult = await fileDiscardHunk.discard({
    file,
    hunkIndex,
    patchText,
    scope,
    umo,
    worktree,
  });
  if (!result.ok && result.reason === "aborted") return;
  if (result.ok) {
    const n = result.snapshot.hunksReverted;
    const tmKey =
      n === 1
        ? "spcodeProjectLoad.diffSidebar.discardHunk.success"
        : "spcodeProjectLoad.diffSidebar.discardHunk.successMultiple";
    showSnackbar(tm(tmKey, { hunksReverted: n, file }), "success");
    // Spec §2 decision #7: success → immediate refresh so the hunk disappears.
    await composable.refresh();
  } else {
    const mapping = DISCARD_HUNK_REASON_I18N_KEYS[result.reason];
    const msg = mapping
      ? tm(mapping, { stderr: result.stderr ?? "" })
      : tm("spcodeProjectLoad.diffSidebar.discardHunk.error.reason.unknown", {
          reason: result.reason,
        });
    showSnackbar(msg, classifySnackbarLevel(result.reason));
  }
}

// ── Git workflow handlers (spec 2026-06-24 §3.3) ──────────────────
// All write paths follow the same recipe:
//   1. 调用 composable.{stage,unstage,commit}(params)
//   2. 成功 → 用响应 `files` 覆盖 stagedFiles,refresh diff,toast success
//   3. 失败 → toast (携带 stderr 的 reason 走 <pre> 块),不破坏本地状态
//   4. aborted → 静默(切 worktree / 卸载项目 / 组件卸载)
function isAborted(result: { ok: boolean; reason?: string }): boolean {
  return !result.ok && result.reason === "aborted";
}

function reasonKey(
  endpoint: "stage" | "unstage" | "commit",
  reason: string,
): string {
  // classifyReason 把 reason 字符串归一化到 ReasonMeta.i18nKey(已在
  // parseSpcodeGitWorkflow 暴露)。i18nKey 形如 "error.reason.path_unsafe"。
  const meta = classifyReason(reason, endpoint);
  return `spcodeProjectLoad.diffSidebar.gitWorkflow.${meta.i18nKey}`;
}

function reasonMeta(
  endpoint: "stage" | "unstage" | "commit",
  reason: string,
): { color: "error" | "warning"; withStderr: boolean; withReason: boolean } {
  const meta = classifyReason(reason, endpoint);
  return {
    color: meta.color,
    withStderr: !!meta.withStderr,
    withReason: !!meta.withReason,
  };
}

async function onStageFile(path: string): Promise<void> {
  if (gitStage.isStaging.value.has(path)) return;
  const umo = spcodeStatus.status.value.umo;
  const worktree = selectedWorktree.value;
  const result = await gitStage.stage({ files: [path], worktree, umo });
  if (isAborted(result)) return;
  if (result.ok) {
    // 乐观语义(spec §3.3.1):用响应 `files` 覆盖 stagedFiles。
    stagedFiles.value = new Set(result.snapshot.files);
    // Parallel refresh: git-diff shows the file in the staged view,
    // git-status moves it out of "untracked"/"intent_to_add" so the
    // merged unstaged list drops the new-file stub. Without the
    // second call the user would wait up to 10s for the polling
    // tick to remove the row.
    await Promise.all([composable.refresh(), gitStatus.refresh()]);
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.gitWorkflow.stage.success", { path }),
      "success",
    );
  } else {
    const meta = reasonMeta("stage", result.reason);
    const key = reasonKey("stage", result.reason);
    const message = meta.withReason
      ? tm(key, { reason: result.reason })
      : tm(key);
    showSnackbar(
      message,
      meta.color,
      meta.withStderr ? result.stderr : undefined,
    );
  }
}

// UI #3: bulk-stage handler. Calls gitStage once with the full path
// list (the backend already accepts an array), then mirrors the
// single-file `onStageFile` success path. Error handling is the same:
// reason-keyed i18n message + stderr in the snackbar's <pre> block.
async function onStagePaths(paths: string[]): Promise<void> {
  if (paths.length === 0) return;
  const umo = spcodeStatus.status.value.umo;
  const worktree = selectedWorktree.value;
  const result = await gitStage.stage({ files: paths, worktree, umo });
  if (isAborted(result)) return;
  if (result.ok) {
    stagedFiles.value = new Set(result.snapshot.files);
    // Parallel refresh: git-diff shows the file in the staged view,
    // git-status moves it out of "untracked"/"intent_to_add" so the
    // merged unstaged list drops the new-file stub. Without the
    // second call the user would wait up to 10s for the polling
    // tick to remove the row.
    await Promise.all([composable.refresh(), gitStatus.refresh()]);
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.gitWorkflow.stage.successAll", {
        count: paths.length,
      }),
      "success",
    );
    // Drop the toolbar's "暂存选中的 N 个文件" counter now that the
    // bulk write succeeded. Without this the child keeps holding the
    // file paths in its local selection Set, so the toolbar text
    // stays frozen at the pre-action count and the next click would
    // re-stage already-staged files. Run AFTER refresh so the row
    // removal is visible before the "已暂存 N" toast; failures skip
    // this so the user can retry without re-ticking every checkbox.
    gitDiffBodyRef.value?.clearSelection();
  } else {
    const meta = reasonMeta("stage", result.reason);
    const key = reasonKey("stage", result.reason);
    const message = meta.withReason
      ? tm(key, { reason: result.reason })
      : tm(key);
    showSnackbar(
      message,
      meta.color,
      meta.withStderr ? result.stderr : undefined,
    );
  }
}

async function onUnstageFile(path: string): Promise<void> {
  if (gitUnstage.isUnstaging.value.has(path)) return;
  const umo = spcodeStatus.status.value.umo;
  const worktree = selectedWorktree.value;
  const result = await gitUnstage.unstage({ files: [path], worktree, umo });
  if (isAborted(result)) return;
  if (result.ok) {
    stagedFiles.value = new Set(result.snapshot.files);
    // Parallel refresh: after unstaging, git-status reclassifies the
    // file (intent_to_add / unstaged) and the unstaged diff picks it
    // up; git-status must refresh in lock-step to avoid a stale
    // "Stage all" count and a stale new-file stub set.
    await Promise.all([composable.refresh(), gitStatus.refresh()]);
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.gitWorkflow.unstage.success", { path }),
      "success",
    );
  } else {
    const meta = reasonMeta("unstage", result.reason);
    const key = reasonKey("unstage", result.reason);
    const message = meta.withReason
      ? tm(key, { reason: result.reason })
      : tm(key);
    showSnackbar(
      message,
      meta.color,
      meta.withStderr ? result.stderr : undefined,
    );
  }
}

// Spec §6.7.1:unstagedCount 派生(分 scope)。
// Reads from diffBodyState (not composable.state) so the "Stage all"
// button includes untracked / intent-to-add files that were merged
// in from /spcode/git-status. The merge now runs for both the
// "unstaged" and "all (vs HEAD)" views (see `newFilePaths` above);
// without this the user would see "Stage all (3)" but only 2 diff
// rows get staged, leaving the new file behind. In the "all" view
// the formula becomes `total - stagedFiles.size` where `total`
// also includes the spliced untracked paths, which is the
// correct semantic — they are untracked, therefore unstaged.
const unstagedCount = computed(() => {
  const s = diffBodyState.value;
  if (s.kind !== "ok") return 0;
  const total = s.snapshot.files.length;
  if (selectedScope.value === "staged") return 0;
  if (selectedScope.value === "unstaged") return total;
  // all:total = staged + unstaged(快照的),减去当前 stagedFiles
  return Math.max(0, total - stagedFiles.value.size);
});

// Symmetric counterpart: number of files currently in the index,
// powering both the "取消全部暂存" button's disabled state and the
// commit button's disabled state (see GitCommitBar). Reads the
// authoritative backend count from git-status (which is polled in
// parallel with git-diff in the diff view, regardless of scope), and
// falls back to the local optimistic Set before the first status
// fetch completes so the count is never negative / flicker-y.
const stagedCount = computed(() => {
  const s = gitStatus.state.value;
  if (s.kind === "ok") return s.snapshot.summary.staged;
  return stagedFiles.value.size;
});

function onClickStageAll(): void {
  // 同步计算 pending count(spec §3.3.3 + P1-3 修复)。
  pendingStageAllCount.value = unstagedCount.value;
  confirmStageAllOpen.value = true;
}

function onCancelStageAll(): void {
  confirmStageAllOpen.value = false;
  // pendingStageAllCount 不重置(spec §3.3.3:无副作用)
}

async function onConfirmStageAll(): Promise<void> {
  confirmStageAllOpen.value = false;
  const umo = spcodeStatus.status.value.umo;
  const worktree = selectedWorktree.value;
  const result = await gitStage.stageAll({ worktree, umo });
  if (isAborted(result)) return;
  if (result.ok) {
    stagedFiles.value = new Set(result.snapshot.files);
    // Parallel refresh: "Stage all" affects every unstaged file
    // (modified + untracked); both endpoints must re-classify.
    await Promise.all([composable.refresh(), gitStatus.refresh()]);
    // Snackbar count: use the pre-action count (snapshotted in the
    // dialog at click time), NOT `result.snapshot.stagedCount` —
    // the latter is the POST-action remaining staged count per
    // the spcode API contract, which would be misleading here
    // (e.g. staging 3 files when 2 were already staged would
    // report 5; the user actually staged 3).
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.gitWorkflow.stage.successAll", {
        count: pendingStageAllCount.value,
      }),
      "success",
    );
  } else {
    const meta = reasonMeta("stage", result.reason);
    const key = reasonKey("stage", result.reason);
    const message = meta.withReason
      ? tm(key, { reason: result.reason })
      : tm(key);
    showSnackbar(
      message,
      meta.color,
      meta.withStderr ? result.stderr : undefined,
    );
  }
}

// Mirror of onClickStageAll for the staged scope, where the bar's
// bulk button flips to "取消全部暂存". Counts come from the
// authoritative git-status summary so the user sees the real number
// of files they'll move back to the worktree, not the optimistic
// Set (which lags by one user action on first load).
function onClickUnstageAll(): void {
  pendingUnstageAllCount.value = stagedCount.value;
  confirmUnstageAllOpen.value = true;
}

function onCancelUnstageAll(): void {
  confirmUnstageAllOpen.value = false;
  // pendingUnstageAllCount 不重置(对称 onCancelStageAll)
}

async function onConfirmUnstageAll(): Promise<void> {
  confirmUnstageAllOpen.value = false;
  const umo = spcodeStatus.status.value.umo;
  const worktree = selectedWorktree.value;
  const result = await gitUnstage.unstageAll({ worktree, umo });
  if (isAborted(result)) return;
  if (result.ok) {
    // Mirror onConfirmStageAll: overwrite the optimistic Set with
    // the response, then refresh diff + status in lock-step so the
    // row moves immediately from staged back to unstaged.
    stagedFiles.value = new Set(result.snapshot.files);
    await Promise.all([composable.refresh(), gitStatus.refresh()]);
    // Snackbar count: use the pre-action count from the dialog,
    // NOT `result.snapshot.stagedCount` — that field is the
    // POST-action remaining count, which would always be 0 after
    // unstage-all and read as "已取消暂存 0 个文件" (the bug the
    // user reported). `pendingUnstageAllCount` was captured when
    // the dialog opened, so it matches what the user just unstaged.
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.gitWorkflow.unstage.successAll", {
        count: pendingUnstageAllCount.value,
      }),
      "success",
    );
  } else {
    const meta = reasonMeta("unstage", result.reason);
    const key = reasonKey("unstage", result.reason);
    const message = meta.withReason
      ? tm(key, { reason: result.reason })
      : tm(key);
    showSnackbar(
      message,
      meta.color,
      meta.withStderr ? result.stderr : undefined,
    );
  }
}

// UI #3: bulk-unstage handler. Mirrors onStagePaths.
async function onUnstagePaths(paths: string[]): Promise<void> {
  if (paths.length === 0) return;
  const umo = spcodeStatus.status.value.umo;
  const worktree = selectedWorktree.value;
  const result = await gitUnstage.unstage({ files: paths, worktree, umo });
  if (isAborted(result)) return;
  if (result.ok) {
    stagedFiles.value = new Set(result.snapshot.files);
    await Promise.all([composable.refresh(), gitStatus.refresh()]);
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.gitWorkflow.unstage.successAll", {
        count: paths.length,
      }),
      "success",
    );
    // Symmetric to onStagePaths: the bulk unstage succeeds, the
    // selected rows leave the staged view, and we drop the toolbar's
    // "已选 N" counter so the next click does not re-fire the same
    // unstage on freshly-empty indices.
    gitDiffBodyRef.value?.clearSelection();
  } else {
    const meta = reasonMeta("unstage", result.reason);
    const key = reasonKey("unstage", result.reason);
    const message = meta.withReason
      ? tm(key, { reason: result.reason })
      : tm(key);
    showSnackbar(
      message,
      meta.color,
      meta.withStderr ? result.stderr : undefined,
    );
  }
}

function onClickCommit(): void {
  commitLastError.value = null;
  commitDialogOpen.value = true;
}

function onCancelCommit(): void {
  // commit 在飞行时不允许关闭(防 race)
  if (gitCommit.isCommitting.value) return;
  commitDialogOpen.value = false;
  commitLastError.value = null;
}

// Spec 2026-07-16: handlers for the GitRepoInitPrompt slot. The prompt
// is a single one-shot interaction (confirm or cancel), so neither
// handler takes any meaningful arguments beyond the existing
// composable state.
async function onRepoInitConfirm(): Promise<void> {
  // projectRoot (from /spcode/project-status) is the primary path source,
  // but it is a live computed over a ref other code refreshes — if it has
  // been momentarily emptied while the probe still holds a directory
  // snapshot, falling back to that snapshot keeps the click functional.
  // Returning silently here was indistinguishable from a dead button.
  const path =
    projectRoot.value ||
    (gitRepoProbe.state.value.kind === "not_a_git_repo"
      ? gitRepoProbe.state.value.directory
      : "");
  if (!path) {
    repoInitLastError.value = { reason: "unknown" };
    return;
  }
  repoInitLastError.value = null;
  isRepoInitSubmitting.value = true;
  // v2.17.1: `force: true` skips the backend's "non-empty directory"
  // guard. Rationale: by the time this prompt is visible, the project
  // has already been loaded into Astrbot - the directory *definitely*
  // exists and almost certainly already has files in it. So "init"
  // here really means "convert this existing project into a
  // Git-managed project", which is exactly the semantics `force=true`
  // unlocks (existing files become untracked, nothing is deleted).
  // Refs: docs/api/v2.17.1-git-init-force-frontend-notice.md §4.
  const result = await gitRepoProbe.gitInit({
    path,
    force: true,
  });
  isRepoInitSubmitting.value = false;
  if (result.ok) {
    // Success path: the composable has already transitioned state to
    // `ok` and cleared its cache, so the prompt will unmount itself
    // via the `showRepoInitPrompt` computed. Reset the dismiss flag
    // so re-probing the same project (rare) re-shows the prompt if
    // the user manually nukes .git/ again.
    repoPromptDismissed.value = false;
    return;
  }
  repoInitLastError.value = {
    reason: result.reason,
    ...(result.stderr !== undefined ? { stderr: result.stderr } : {}),
  };
}

function onRepoInitCancel(): void {
  repoPromptDismissed.value = true;
  repoInitLastError.value = null;
}

async function onConfirmCommit(payload: { message: string }): Promise<void> {
  const umo = spcodeStatus.status.value.umo;
  const worktree = selectedWorktree.value;
  // 进入提交前清空 lastError(spec §3.3.4)
  commitLastError.value = null;
  const result = await gitCommit.commit({
    message: payload.message,
    worktree,
    umo,
  });
  if (isAborted(result)) {
    // abort 路径下,onBeforeUnmount 也会关 dialog
    commitDialogOpen.value = false;
    return;
  }
  if (result.ok) {
    // 成功:覆盖 stagedFiles(后端响应),refresh diff+status,可
    // 选 refresh log。git-status 也要刷新:commit 后工作区清空,
    // 文件从 untracked/unstaged/staged 三个集合中全部消失。
    stagedFiles.value = new Set(result.snapshot.files);
    commitDialogOpen.value = false;
    commitLastError.value = null;
    await Promise.all([composable.refresh(), gitStatus.refresh()]);
    if (viewMode.value === "history") {
      await gitLog.refresh();
    }
    const shortSha = (result.snapshot.sha || "").slice(0, 7) || "?";
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.gitWorkflow.commit.success", {
        sha: shortSha,
      }),
      "success",
    );
  } else {
    // 失败:保持 dialog 打开,显示 stderr 块(spec §3.3.4 DIALOG_OPEN_KEEP_ERROR)
    const meta = reasonMeta("commit", result.reason);
    const key = reasonKey("commit", result.reason);
    commitLastError.value = {
      reason: result.reason,
      stderr: result.stderr ?? "",
    };
    const message = meta.withReason
      ? tm(key, { reason: result.reason })
      : tm(key);
    showSnackbar(
      message,
      meta.color,
      meta.withStderr ? result.stderr : undefined,
    );
  }
}

// Spec §3.4 决策 #24:切 worktree / 切 umo 时清空 ETag。
// 监听 selectedWorktree 和 umo,变更时调用 gitLog.invalidateEtag()。
watch(selectedWorktree, () => gitLog.invalidateEtag());
watch(
  () => spcodeStatus.status.value.umo,
  (newUmo, oldUmo) => {
    if (newUmo !== oldUmo) {
      gitLog.invalidateEtag();
    }
  },
);

// Spec §3.4 决策 #22:切 worktree 时保留 stagedFiles(本 spec 不重置)。
// 切 project (umo 变更) / 卸载项目 时清空。
watch(
  () => spcodeStatus.status.value.loaded,
  (loaded) => {
    if (!loaded) {
      stagedFiles.value = new Set<string>();
    }
  },
);
watch(
  () => spcodeStatus.status.value.directory,
  () => {
    // 目录变了 → 视作切了项目(decision #23)。
    stagedFiles.value = new Set<string>();
  },
);
// Spec §10 风险 #12:在 confirmStageAllOpen 打开时切 worktree,
// 先关 dialog(避免用户在切 worktree 后看到旧 worktree 的计数)。
// Same risk applies to confirmUnstageAllOpen — both dialogs snapshot
// the previous worktree's staged/unstaged count, so a switch mid-
// dialog would display a stale number after re-open.
watch(selectedWorktree, () => {
  if (confirmStageAllOpen.value) {
    confirmStageAllOpen.value = false;
  }
  if (confirmUnstageAllOpen.value) {
    confirmUnstageAllOpen.value = false;
  }
});

// ── History view wiring (spec 2026-06-24 §3.5) ───────────────────
const logHasMore = computed(() => {
  const s = gitLog.state.value;
  if (s.kind === "ok") return s.snapshot.hasMore;
  if (s.kind === "error" && s.previousSnapshot)
    return s.previousSnapshot.hasMore;
  return false;
});
const logIsLoading = computed(() => gitLog.state.value.kind === "loading");
function onLogApply(filter: LogFilter): void {
  // 2026-09-08 final-fix: 深链聚焦（focusCommit）在 GitLogView 的
  // effectiveFocusSha 里优先级高于 hash 搜索派生的 hashFocusSha。Apply
  // 后若不清空，用户再用 hash 搜索时命中行会静默不高亮。深链自身走
  // gitLog.refresh()，不经过本函数，所以这里清空不会打断深链。
  focusedCommitSha.value = null;
  // 用 filter 调用 refresh(spec §6.5.1:filter 变化时 key 自动变化,旧 ETag 不复用)
  void gitLog.refresh(filter);
}
// 2026-07-18 git-stats heatmap: the History-tab manual refresh button
// re-fetches the log AND the stats panel (ETag-validated; cheap 304s
// when nothing changed upstream).
//
// 2026-07-18 fix(dashboard): forward the current range to
// `gitStats.refresh()` so a manual refresh on a 1y / custom
// selection doesn't drop into the no-since/until bucket
// (`umo|worktree||`) and replace the range-filtered hot files
// with whole-repo data. The watcher already keeps that bucket
// fresh on every range change, so we just need the manual
// refresh to hit the same key.
function onLogRefresh(): void {
  void gitLog.refresh();
  void gitStats.refresh(statsRangeArgs());
  // 2026-09-09 head-sha: HEAD 判定依赖 worktree 列表里的 head_sha，所以
  // History 的刷新按钮把它一起刷新（一次 porcelain 调用，很便宜），
  // 否则外部提交后 amend / reset 的可见性会滞后最多 30s（轮询周期）。
  void worktreesComposable.refresh();
}
function onLogReset(filter: LogFilter): void {
  // 2026-09-08 final-fix: 同 onLogApply —— Reset 也必须让深链聚焦让位，
  // 否则 hash 搜索的 resolvedRef 高亮会被残留的 focusedCommitSha 压掉。
  focusedCommitSha.value = null;
  // Reset semantics differ from a regular Apply: the URL of the reset
  // request is identical to the very first history-tab load (?ref=HEAD&n=20),
  // so without dropping the ETag the backend returns 304 Not Modified and
  // the client-side 304 branch replays `prevSnapshot` — which was overwritten
  // by the user's most recent filter (e.g. author=alice) and therefore shows
  // the filtered result instead of the reset state. We invalidate only this
  // one tuple's ETag so other filter tuples (author=bob etc.) keep their
  // cache and remain cheap to revisit. forceLoading makes the spinner show
  // even though the previous state was already ok, so the user gets
  // feedback that a refresh is in flight.
  gitLog.invalidateEtagFor(filter);
  void gitLog.refresh(filter, { forceLoading: true });
}
function onLogLoadMore(): void {
  void gitLog.loadMore();
}

onBeforeUnmount(() => {
  onMouseUp();
  composable.dispose();
  gitStatus.dispose();
  fileRestore.dispose();
  fileDiscardHunk.dispose();
  worktreesComposable.dispose();
  branchesComposable.dispose();
  gitMerge.dispose();
  gitRemoteSync.dispose();
  gitStash.dispose();
  gitConflict.dispose();
  // Spec 2026-07-16: abort in-flight probe + init + clear polling
  // interval. Mirrors the worktree composable's dispose pattern.
  gitRepoProbe.dispose();
  gitStage.dispose();
  gitUnstage.dispose();
  gitCommit.dispose();
  gitCommitAmend.dispose();
  gitRevert.dispose();
  gitReset.dispose();
  gitSquash.dispose();
  gitIgnoreFileWrite.dispose();
  gitLog.dispose();
  // git-show composable holds per-SHA caches + inflight AbortControllers.
  // dispose() aborts in-flight requests and drops the maps; safe to
  // call after gitLog.dispose() since the two are independent.
  gitShow.dispose();
  // git-stats: abort in-flight refresh + clear ETag/snapshot maps.
  gitStats.dispose();
  if (persistCurrentPathTimer) {
    clearTimeout(persistCurrentPathTimer);
    persistCurrentPathTimer = null;
  }
  document.removeEventListener("mousedown", closeContextMenuOnOutside, true);
  document.removeEventListener("keydown", closeContextMenuOnEscape, true);
  window.removeEventListener("focus", refreshConflictOnWake);
  document.removeEventListener("visibilitychange", refreshConflictOnWake);
  document.removeEventListener("keydown", onFullscreenKeyDown);
  // Always restore body overflow on unmount. The watcher above already
  // does this whenever both modes become false; the explicit reset
  // covers the edge case where the sidebar closes while fullscreen is
  // still on (the watcher would otherwise leak the hidden overflow).
  document.body.style.overflow = "";
});

function toggleFile(path: string): void {
  const next = new Set(expandedSet.value);
  if (next.has(path)) next.delete(path);
  else next.add(path);
  expandedSet.value = next;
}

// ── Drag resize ────────────────────────────────────────────────────

const MIN_WIDTH = 320;
const MAX_WIDTH = 1800;
const DEFAULT_WIDTH = 800;

const sidebarWidth = ref(DEFAULT_WIDTH);
const sidebarRef = ref<HTMLElement | null>(null);
let isResizing = false;

function startResize(e: MouseEvent): void {
  e.preventDefault();
  isResizing = true;
  document.body.style.cursor = "ew-resize";
  document.body.style.userSelect = "none";
  document.addEventListener("mousemove", onMouseMove);
  document.addEventListener("mouseup", onMouseUp);
}

function onMouseMove(e: MouseEvent): void {
  if (!isResizing || !sidebarRef.value) return;
  // Use the sidebar's own right edge (not the parent's) as the
  // reference point. The flex layout places any siblings shown to
  // the right beyond `selfRect.right`, so the computed width is
  // automatically reduced by their combined width — same model
  // as ReasoningSidebar.
  const selfRect = sidebarRef.value.getBoundingClientRect();
  const newWidth = selfRect.right - e.clientX;
  sidebarWidth.value = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, newWidth));
}

function onMouseUp(): void {
  if (!isResizing) return;
  isResizing = false;
  document.body.style.cursor = "";
  document.body.style.userSelect = "";
  document.removeEventListener("mousemove", onMouseMove);
  document.removeEventListener("mouseup", onMouseUp);
}

const directoryPath = computed(() => {
  const s = composable.state.value;
  if (s.kind === "ok") return s.snapshot.meta.directory;
  if (s.kind === "error" && s.previousSnapshot)
    return s.previousSnapshot.meta.directory;
  return null;
});

const isTruncated = computed(() => {
  const s = composable.state.value;
  if (s.kind === "ok") return s.snapshot.meta.truncated;
  if (s.kind === "error" && s.previousSnapshot)
    return s.previousSnapshot.meta.truncated;
  return false;
});

const truncatedShown = computed(() => {
  const s = composable.state.value;
  if (s.kind === "ok") return s.snapshot.meta.truncatedAtBytes;
  if (s.kind === "error" && s.previousSnapshot)
    return s.previousSnapshot.meta.truncatedAtBytes;
  return 0;
});

const truncatedMax = computed(() => {
  const s = composable.state.value;
  if (s.kind === "ok") return s.snapshot.meta.maxBytes;
  if (s.kind === "error" && s.previousSnapshot)
    return s.previousSnapshot.meta.maxBytes;
  return 0;
});

// Root path for FileBrowserView: the active worktree, then the main
// worktree, then the loaded project's root directory. We pass this
// to FileBrowserView so it can render the breadcrumb. The projectRoot
// fallback ensures non-git projects still get a meaningful root for
// the breadcrumb (and a non-empty path for the file-browser fetch).
const currentRoot = computed<string | null>(() => {
  return selectedWorktree.value ?? mainWorktreePath.value ?? projectRoot.value;
});

// 2026-08-14 open-on-disk: expose the effective root to deeply-nested
// file rows (GitDiffFileItem via DiffDirectoryNode, GitLogView) so
// they can glue repo-relative paths into absolute ones for the
// "open on disk" action without threading props through every layer.
provide("openOnDiskRoot", currentRoot);

// 2026-08-27 recent-files-split: per-worktree bucket scoped to the
// Files view ("files"). The docs view (DocumentManager) keeps its own
// "docs" bucket, so the two pages no longer share a list. Keyed on
// currentRoot (the effective root this view browses) — worktree
// switches change the ref and the composable re-loads that worktree's
// own history. See useRecentFiles for the storage contract.
const recentFiles = useRecentFiles(currentRoot, "files");

// 2026-07-20 Recent Files §6.1: drive recordOpen off the canonical
// preview-path writer so every entry point (search jumps, tree clicks,
// recent-row clicks) shares one filter. Null writes (close preview /
// worktree switch) are skipped; empty paths are filtered inside
// recordOpen. `immediate: false` keeps the empty initial state clean.
watch(
  () => fileBrowserPreviewPath.value,
  (newPath) => {
    if (!newPath) return;
    recentFiles.recordOpen(newPath);
  },
  { immediate: false },
);
</script>

<template>
  <!-- Global fullscreen escape: teleport the sidebar shell to <body>
       only while globalFullscreen is on. When disabled, Teleport is a
       transparent pass-through — the existing chat-panel layout
       (sidebar body → document-manager flex column) is unchanged.
       The .is-fullscreen class on the <aside> drives the fixed
       viewport styles in the scoped CSS below. -->
  <Teleport to="body" :disabled="!globalFullscreen">
    <transition name="slide-left">
      <aside
        v-if="modelValue"
        ref="sidebarRef"
        class="git-diff-sidebar"
        :class="{ resizing: isResizing, 'is-fullscreen': globalFullscreen }"
        :style="{ width: sidebarWidth + 'px' }"
      >
        <div class="git-diff-sidebar-resizer" @mousedown="startResize" />
        <div class="git-diff-sidebar-header">
          <div class="git-diff-sidebar-title-wrap">
            <span class="git-diff-sidebar-title">
              {{
                viewMode === "files"
                  ? tm("spcodeProjectLoad.fileBrowser.title")
                  : tm("spcodeProjectLoad.diffSidebar.title")
              }}
            </span>
            <!-- UI #5: directory path is now shown as a compact breadcrumb-style
               strip directly under the title, with an inline folder icon.
               This keeps the header row uncluttered (title + actions only)
               while still giving the user a visible project root in all
               three view modes (not just diff). -->
          </div>
          <div class="git-diff-sidebar-actions">
            <!-- 2026-07-18: the .gitignore entry moved from this header
                 into GitCommitBar (next to the stage/commit controls)
                 so users notice it as a Git-management setting. -->
            <!-- UI #5: refresh button dropped the `tonal` background and
               dropped to `variant="text"` + `size="small"` so the header
               reads as a lightweight toolbar instead of three chunky
               "circle" buttons. Loading state is preserved (a small
               spinner replaces the icon while the request is in flight). -->
            <v-tooltip location="bottom" :open-delay="200">
              <template #activator="{ props: tipProps }">
                <v-btn
                  v-bind="tipProps"
                  icon
                  size="small"
                  variant="text"
                  :loading="isFetching"
                  @click="onManualRefresh"
                >
                  <v-icon size="18">mdi-restart</v-icon>
                </v-btn>
              </template>
              {{ tm("spcodeProjectLoad.diffSidebar.refreshTooltip") }}
            </v-tooltip>
            <v-btn
              :icon="
                globalFullscreen ? 'mdi-fullscreen-exit' : 'mdi-fullscreen'
              "
              size="small"
              variant="text"
              :aria-pressed="globalFullscreen"
              :aria-label="
                tm(
                  globalFullscreen
                    ? 'spcodeProjectLoad.documentManager.fullscreen.exit'
                    : 'spcodeProjectLoad.documentManager.fullscreen.enter',
                )
              "
              :title="
                tm(
                  globalFullscreen
                    ? 'spcodeProjectLoad.documentManager.fullscreen.exit'
                    : 'spcodeProjectLoad.documentManager.fullscreen.enter',
                )
              "
              @click="toggleGlobalFullscreen"
            />
            <v-btn
              icon="mdi-close"
              size="small"
              variant="text"
              @click="emit('update:modelValue', false)"
            />
          </div>
        </div>

        <!-- UI #5: a fixed path strip directly under the header. Visible in
           all three view modes so the user always knows which project
           (and which worktree) they are looking at. Two-line layout:
             [folder] <project-root>           (top)
             <worktree> · <branch>             (bottom, only when in a
                                                non-main worktree)
           The strip is muted (low-contrast) so it doesn't compete with
           the title above or the diff content below. -->
        <div
          v-if="currentRoot"
          class="git-diff-sidebar-path-strip"
          :title="currentRoot"
        >
          <div class="git-diff-sidebar-path-line">
            <v-icon size="12" class="git-diff-sidebar-path-icon"
              >mdi-folder-outline</v-icon
            >
            <span class="git-diff-sidebar-path-text">{{ currentRoot }}</span>
          </div>
          <div
            v-if="selectedWorktree && worktreeList.length > 0"
            class="git-diff-sidebar-path-sub"
          >
            {{
              worktreeList.find((w) => w.path === selectedWorktree)?.branch ??
              tm("spcodeProjectLoad.diffSidebar.worktreeTabs.detachedBadge")
            }}
          </div>
        </div>

        <!-- View-mode tab (spec 2026-06-20 §5.2): Files / Diff.
           aria-label is hardcoded (per advisory R2) to avoid adding
           a 31st i18n key — the visible button text already conveys
           the purpose for sighted users. -->
        <div
          v-if="isGitRepo || showNotGitRepoChip"
          class="git-diff-sidebar-view-tabs"
          role="tablist"
          aria-label="Switch view"
        >
          <button
            type="button"
            role="tab"
            :aria-selected="viewMode === 'files'"
            :class="[
              'git-diff-sidebar-view-tab',
              { 'is-active': viewMode === 'files' },
            ]"
            @click="viewMode = 'files'"
          >
            <v-icon size="14">mdi-folder-outline</v-icon>
            <span>{{
              tm("spcodeProjectLoad.fileBrowser.viewMode.files")
            }}</span>
          </button>
          <!-- 2026-07-11 document-manager:Documents 文档管理 sub-tab。 -->
          <button
            type="button"
            role="tab"
            :aria-selected="viewMode === 'docs'"
            :aria-label="tm('spcodeProjectLoad.gitDiffSidebar.tabs.docs')"
            :class="[
              'git-diff-sidebar-view-tab',
              { 'is-active': viewMode === 'docs' },
            ]"
            @click="viewMode = 'docs'"
          >
            <v-icon size="14">mdi-file-document-multiple-outline</v-icon>
            <span>{{ tm("spcodeProjectLoad.gitDiffSidebar.tabs.docs") }}</span>
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="viewMode === 'diff'"
            :class="[
              'git-diff-sidebar-view-tab',
              { 'is-active': viewMode === 'diff' },
            ]"
            @click="viewMode = 'diff'"
          >
            <v-icon size="14">mdi-source-pull</v-icon>
            <span>{{ tm("spcodeProjectLoad.fileBrowser.viewMode.diff") }}</span>
          </button>
          <!-- Spec 2026-06-24 §2 决策 #10:History 是第 3 个 viewMode。 -->
          <button
            type="button"
            role="tab"
            :aria-selected="viewMode === 'history'"
            :aria-label="
              tm('spcodeProjectLoad.diffSidebar.gitWorkflow.history.tabAria')
            "
            :class="[
              'git-diff-sidebar-view-tab',
              { 'is-active': viewMode === 'history' },
            ]"
            @click="viewMode = 'history'"
          >
            <v-icon size="14">mdi-history</v-icon>
            <span>{{
              tm("spcodeProjectLoad.diffSidebar.gitWorkflow.history.tab")
            }}</span>
          </button>
          <!-- 2026-09-01 terminal:终端子页面 tab。 -->
          <button
            type="button"
            role="tab"
            :aria-selected="viewMode === 'terminal'"
            :aria-label="tm('spcodeProjectLoad.gitDiffSidebar.tabs.terminal')"
            :class="[
              'git-diff-sidebar-view-tab',
              { 'is-active': viewMode === 'terminal' },
            ]"
            @click="viewMode = 'terminal'"
          >
            <v-icon size="14">mdi-console</v-icon>
            <span>{{
              tm("spcodeProjectLoad.gitDiffSidebar.tabs.terminal")
            }}</span>
          </button>
        </div>

        <!-- 2026-07-22 worktree-tabs-collapse: branch switcher and
             worktree tabs are wrapped in `.git-diff-sidebar-bw` so that
             when the worktree tabs are collapsed the two rows collapse
             into ONE row (worktree on the left, branch on the right) to
             save vertical space. When expanded, the wrapper is just a
             transparent group — both rows render with their original
             padding/border. The wrapper's v-if mirrors the worktree
             row's own condition (the broadest of the two), so the
             non-git-repo path keeps the worktree row without a stale
             branch row leaking in. -->
        <div
          v-if="hasMultipleWorktrees && (isGitRepo || showNotGitRepoChip)"
          class="git-diff-sidebar-bw"
          :class="{ 'is-collapsed': !worktreeTabsExpanded }"
        >
          <!-- Branch switcher (spec 2026-07-21 §3.3, layout revised 2026-07-21)
             Sits ABOVE the worktree tabs (not inside the flex row) so its
             dropdown anchors to the button instead of being squeezed by
             the tab strip. The button is wrapped by v-menu's activator slot
             so Vuetify's positioning pipeline has an HTMLElement to anchor
             against; without it the menu falls back to the viewport's
             top-left corner. -->
          <div
            v-if="isGitRepo && hasMultipleWorktrees"
            class="git-diff-sidebar-branch-mgmt"
          >
            <span class="git-diff-sidebar-branch-mgmt-label">
              {{ tm("spcodeProjectLoad.diffSidebar.branchMgmt.currentLabel") }}
            </span>
            <v-menu
              v-model="branchMenuOpen"
              :close-on-content-click="false"
              location="bottom end"
              max-width="320"
            >
              <template #activator="{ props: menuProps }">
                <button
                  v-bind="menuProps"
                  type="button"
                  class="git-diff-sidebar-branch-mgmt-btn"
                  :aria-label="
                    tm(
                      'spcodeProjectLoad.diffSidebar.branchMgmt.menuButtonAria',
                    )
                  "
                  :title="
                    tm('spcodeProjectLoad.diffSidebar.branchMgmt.menuButton')
                  "
                >
                  <v-icon size="14">mdi-source-branch</v-icon>
                  <span class="git-diff-sidebar-branch-mgmt-btn-name">
                    {{
                      currentBranchName ??
                      tm("spcodeProjectLoad.diffSidebar.branchMgmt.detached")
                    }}
                  </span>
                  <v-icon size="12">mdi-menu-down</v-icon>
                </button>
              </template>
              <v-list density="compact" class="git-diff-sidebar-branch-menu">
                <!-- Loading -->
                <template
                  v-if="branchesComposable.state.value.kind === 'loading'"
                >
                  <v-list-item>
                    <template #prepend>
                      <v-progress-circular
                        indeterminate
                        :size="14"
                        :width="2"
                      />
                    </template>
                    <v-list-item-title>
                      {{
                        tm("spcodeProjectLoad.diffSidebar.branchMgmt.loading")
                      }}
                    </v-list-item-title>
                  </v-list-item>
                </template>

                <!-- Error -->
                <template
                  v-else-if="branchesComposable.state.value.kind === 'error'"
                >
                  <v-list-item disabled>
                    <v-list-item-title class="text-caption text-error">
                      {{
                        tm("spcodeProjectLoad.diffSidebar.branchMgmt.error", {
                          reason: branchesComposable.state.value.reason,
                        })
                      }}
                    </v-list-item-title>
                  </v-list-item>
                </template>

                <!-- Branch list -->
                <template v-else>
                  <v-list-item v-if="branchList.length === 0" disabled>
                    <v-list-item-title class="text-caption">
                      {{ tm("spcodeProjectLoad.diffSidebar.branchMgmt.empty") }}
                    </v-list-item-title>
                  </v-list-item>
                  <v-list-item
                    v-for="b in branchList"
                    :key="b.name"
                    :active="b.current"
                    :disabled="isBranchSwitching"
                    @click="onBranchMenuItemClick(b)"
                  >
                    <template #prepend>
                      <v-icon v-if="b.current" size="14" color="primary"
                        >mdi-check</v-icon
                      >
                      <v-icon v-else-if="b.remote" size="14" color="grey"
                        >mdi-cloud-outline</v-icon
                      >
                      <v-icon v-else size="14">mdi-source-branch</v-icon>
                    </template>
                    <v-list-item-title>
                      {{ b.name }}
                    </v-list-item-title>
                    <template #append>
                      <!-- 2026-08-01 git-merge: per-row merge-into-current
                         action. Hidden for the current branch (no-op)
                         and remote branches. -->
                      <v-icon
                        v-if="!b.current && !b.remote"
                        size="14"
                        class="git-diff-sidebar-branch-merge"
                        :title="
                          tm('spcodeProjectLoad.diffSidebar.merge.rowAction')
                        "
                        @click.stop="onBranchMergeClick(b)"
                        >mdi-source-merge</v-icon
                      >
                      <v-icon
                        v-if="!b.current"
                        size="14"
                        class="git-diff-sidebar-branch-delete"
                        @click.stop="onBranchDeleteClick(b)"
                        >mdi-close</v-icon
                      >
                    </template>
                  </v-list-item>
                </template>

                <!-- Inline create form -->
                <v-divider />
                <div
                  v-if="branchCreateExpanded"
                  class="git-diff-sidebar-branch-create"
                >
                  <v-text-field
                    v-model="branchCreateName"
                    :label="
                      tm('spcodeProjectLoad.diffSidebar.branchMgmt.create.name')
                    "
                    :error-messages="
                      branchCreateError ? [branchCreateError] : []
                    "
                    density="compact"
                    variant="outlined"
                    autofocus
                    autocomplete="off"
                    name="branch-create-name"
                    @keyup.enter="onBranchCreateSubmit"
                  />
                  <v-text-field
                    v-model="branchCreateStartPoint"
                    :label="
                      tm(
                        'spcodeProjectLoad.diffSidebar.branchMgmt.create.startPoint',
                      )
                    "
                    :placeholder="'HEAD'"
                    density="compact"
                    variant="outlined"
                    class="mt-1"
                    autocomplete="off"
                    name="branch-create-start"
                    @keyup.enter="onBranchCreateSubmit"
                  />
                  <div class="git-diff-sidebar-branch-create-actions">
                    <v-btn
                      size="x-small"
                      variant="text"
                      :disabled="isBranchCreating"
                      @click="
                        branchCreateExpanded = false;
                        branchCreateError = null;
                      "
                    >
                      {{
                        tm(
                          "spcodeProjectLoad.diffSidebar.branchMgmt.create.cancel",
                        )
                      }}
                    </v-btn>
                    <v-btn
                      size="x-small"
                      variant="flat"
                      color="primary"
                      :loading="isBranchCreating"
                      :disabled="!branchCreateName.trim()"
                      @click="onBranchCreateSubmit"
                    >
                      {{
                        tm(
                          "spcodeProjectLoad.diffSidebar.branchMgmt.create.submit",
                        )
                      }}
                    </v-btn>
                  </div>
                </div>
                <v-list-item
                  v-else
                  :disabled="isBranchCreating"
                  @click="branchCreateExpanded = true"
                >
                  <template #prepend>
                    <v-icon size="14">mdi-plus</v-icon>
                  </template>
                  <v-list-item-title>
                    {{
                      tm(
                        "spcodeProjectLoad.diffSidebar.branchMgmt.create.menuItem",
                      )
                    }}
                  </v-list-item-title>
                </v-list-item>
              </v-list>
            </v-menu>
            <!-- Ahead/behind indicator for the current branch. Mirrors
                 the worktree tab badges ("主" / "游离" / "损坏") in
                 shape and palette; hidden when there's no upstream
                 (local-only branch) or no tracking info. -->
            <span
              v-if="currentBranchTracking"
              class="git-diff-sidebar-branch-mgmt-tracking"
              :title="
                tm(
                  'spcodeProjectLoad.diffSidebar.branchMgmt.tracking.tooltip',
                  {
                    ahead: currentBranchTracking.ahead,
                    behind: currentBranchTracking.behind,
                  },
                )
              "
            >
              <!-- 2026-07-22 redesigned: the original used the unicode
                 "↑" / "↓" characters in the same font / color / weight
                 as the count, which the eye confused with the digit
                 "1" — both render as a vertical stem with a small mark
                 on top. Swap the arrow for an MDI glyph
                 (`mdi-arrow-up-thick` / `-down-thick`) whose shape is
                 a solid triangle with NO stem, so the arrow and the
                 digits occupy completely different shape classes.
                 The count is the dominant information (how many
                 commits to push / pull), so it is the bold 13px digit
                 the user actually wants to read; the arrow is a
                 subordinate 10px hint that conveys direction. A 3px
                 gap between them gives the eye a clear break.
                 `aria-hidden` on the v-icon means screen readers only
                 announce the count, not the meaningless glyph, so the
                 badge is read aloud as "16 ahead, 5 behind" rather
                 than "up arrow 16, down arrow 5". -->
              <span
                v-if="currentBranchTracking.ahead > 0"
                class="git-diff-sidebar-branch-mgmt-tracking-ahead"
              >
                <v-icon
                  size="10"
                  class="git-diff-sidebar-branch-mgmt-tracking-arrow"
                  aria-hidden="true"
                  >mdi-arrow-up-thick</v-icon
                >
                <span class="git-diff-sidebar-branch-mgmt-tracking-count">{{
                  currentBranchTracking.ahead
                }}</span>
              </span>
              <span
                v-if="currentBranchTracking.behind > 0"
                class="git-diff-sidebar-branch-mgmt-tracking-behind"
              >
                <v-icon
                  size="10"
                  class="git-diff-sidebar-branch-mgmt-tracking-arrow"
                  aria-hidden="true"
                  >mdi-arrow-down-thick</v-icon
                >
                <span class="git-diff-sidebar-branch-mgmt-tracking-count">{{
                  currentBranchTracking.behind
                }}</span>
              </span>
            </span>
            <!-- 2026-08-12 git-pull/push/remote: remote-sync actions,
                 anchored next to the ahead/behind badge (the counts
                 tell the user WHY they would pull/push; these buttons
                 are the HOW). Busy state swaps the icon for a spinner,
                 mirroring the bw-refresh button; the remote-URL gear
                 is the escape hatch for remote_not_found /
                 auth_required failures. -->
            <span class="git-diff-sidebar-sync">
              <button
                type="button"
                class="git-diff-sidebar-sync-btn"
                :title="tm('spcodeProjectLoad.diffSidebar.pull.button')"
                :aria-label="
                  tm('spcodeProjectLoad.diffSidebar.pull.buttonAria')
                "
                :disabled="
                  gitRemoteSync.isPulling.value || gitRemoteSync.isPushing.value
                "
                @click="pullDialogOpen = true"
              >
                <v-progress-circular
                  v-if="gitRemoteSync.isPulling.value"
                  indeterminate
                  :size="12"
                  :width="2"
                />
                <v-icon v-else size="14">mdi-cloud-download-outline</v-icon>
              </button>
              <button
                type="button"
                class="git-diff-sidebar-sync-btn"
                :title="tm('spcodeProjectLoad.diffSidebar.push.button')"
                :aria-label="
                  tm('spcodeProjectLoad.diffSidebar.push.buttonAria')
                "
                :disabled="
                  gitRemoteSync.isPulling.value || gitRemoteSync.isPushing.value
                "
                @click="pushDialogOpen = true"
              >
                <v-progress-circular
                  v-if="gitRemoteSync.isPushing.value"
                  indeterminate
                  :size="12"
                  :width="2"
                />
                <v-icon v-else size="14">mdi-cloud-upload-outline</v-icon>
              </button>
              <button
                type="button"
                class="git-diff-sidebar-sync-btn"
                :title="tm('spcodeProjectLoad.diffSidebar.remote.button')"
                :aria-label="
                  tm('spcodeProjectLoad.diffSidebar.remote.buttonAria')
                "
                :disabled="gitRemoteSync.isSettingRemote.value"
                @click="remoteUrlDialogOpen = true"
              >
                <v-icon size="14">mdi-cog-outline</v-icon>
              </button>
            </span>
            <!-- Spec 2026-07-23 §4.2.1: branch/worktree row manual
               refresh. Expanded state: anchored to the right end of
               the branch row. Loading state replaces the icon with
               a progress spinner (derived from the underlying
               composables' `state.kind === 'loading'` rather than
               a local ref, so the spinner never lags the real
               request). -->
            <button
              v-if="worktreeTabsExpanded"
              type="button"
              class="git-diff-sidebar-bw-refresh"
              :class="{ 'is-flashing': bwClickFlash }"
              :title="tm('spcodeProjectLoad.diffSidebar.bwRefreshTooltip')"
              :aria-label="tm('spcodeProjectLoad.diffSidebar.bwRefreshTooltip')"
              :disabled="
                branchesComposable.state.value.kind === 'loading' ||
                worktreesComposable.state.value.kind === 'loading'
              "
              @click="refreshBranchesAndWorktrees"
            >
              <span class="git-diff-sidebar-bw-refresh-text">
                {{ tm("spcodeProjectLoad.diffSidebar.bwRefreshTooltip") }}
              </span>
              <v-progress-circular
                v-if="
                  branchesComposable.state.value.kind === 'loading' ||
                  worktreesComposable.state.value.kind === 'loading'
                "
                indeterminate
                :size="14"
                :width="2"
              />
              <v-icon v-else size="14">mdi-refresh</v-icon>
            </button>
          </div>

          <!-- Spec 2026-07-23 §4.2.2: collapsed-state counterpart of
             the refresh button above. Sits as a DIRECT CHILD of
             `.git-diff-sidebar-bw` so the collapsed flex layout
             (`justify-content: space-between`, 3 children) can
             park it visually in the middle. DOM order placed
             BEFORE .git-diff-sidebar-tabs so the screen-reader
             order matches the expanded layout (branch-related
             controls announced first). Visual position is
             controlled by `order: 2` in CSS. -->
          <button
            v-if="!worktreeTabsExpanded"
            type="button"
            class="git-diff-sidebar-bw-refresh"
            :class="{ 'is-flashing': bwClickFlash }"
            :title="tm('spcodeProjectLoad.diffSidebar.bwRefreshTooltip')"
            :aria-label="tm('spcodeProjectLoad.diffSidebar.bwRefreshTooltip')"
            :disabled="
              branchesComposable.state.value.kind === 'loading' ||
              worktreesComposable.state.value.kind === 'loading'
            "
            @click="refreshBranchesAndWorktrees"
          >
            <span class="git-diff-sidebar-bw-refresh-text">
              {{ tm("spcodeProjectLoad.diffSidebar.bwRefreshTooltip") }}
            </span>
            <v-progress-circular
              v-if="
                branchesComposable.state.value.kind === 'loading' ||
                worktreesComposable.state.value.kind === 'loading'
              "
              indeterminate
              :size="14"
              :width="2"
            />
            <v-icon v-else size="14">mdi-refresh</v-icon>
          </button>

          <!-- Worktree tabs (visible in BOTH views, spec 2026-06-20 §5.3) -->
          <div
            v-if="hasMultipleWorktrees && (isGitRepo || showNotGitRepoChip)"
            class="git-diff-sidebar-tabs"
            role="tablist"
            :aria-label="
              tm('spcodeProjectLoad.diffSidebar.worktreeTabs.ariaLabel')
            "
          >
            <!-- Section label: clarifies that the buttons below switch
             worktrees (otherwise they look like generic pills with
             no obvious purpose). Anchored to the left of the flex row;
             existing flex-wrap still lets the tabs wrap to a new line
             on narrow widths.
             2026-07-22 worktree-tabs-collapse: this label is now a
             toggle button. Chevron-down = expanded (full row visible),
             chevron-right = collapsed (only the active worktree
             visible). Clicking flips worktreeTabsExpanded so users
             with many worktrees can keep the sidebar compact. -->
            <button
              type="button"
              class="git-diff-sidebar-tabs-toggle"
              :aria-expanded="worktreeTabsExpanded"
              :title="
                tm(
                  worktreeTabsExpanded
                    ? 'spcodeProjectLoad.diffSidebar.worktreeTabs.collapse'
                    : 'spcodeProjectLoad.diffSidebar.worktreeTabs.expand',
                )
              "
              :aria-label="
                tm(
                  worktreeTabsExpanded
                    ? 'spcodeProjectLoad.diffSidebar.worktreeTabs.collapse'
                    : 'spcodeProjectLoad.diffSidebar.worktreeTabs.expand',
                )
              "
              @click="worktreeTabsExpanded = !worktreeTabsExpanded"
            >
              <span>{{
                tm("spcodeProjectLoad.diffSidebar.worktreeTabs.label")
              }}</span>
              <v-icon size="12" class="git-diff-sidebar-tabs-toggle-chevron">
                {{
                  worktreeTabsExpanded
                    ? "mdi-chevron-down"
                    : "mdi-chevron-right"
                }}
              </v-icon>
            </button>
            <button
              v-for="wt in visibleWorktreeList"
              :key="wt.path"
              type="button"
              role="tab"
              :aria-selected="
                (selectedWorktree ?? mainWorktreePath) === wt.path
              "
              :class="[
                'git-diff-sidebar-tab',
                {
                  'git-diff-sidebar-tab--active':
                    (selectedWorktree ?? mainWorktreePath) === wt.path,
                  'git-diff-sidebar-tab--prunable': wt.prunable,
                },
              ]"
              :title="
                wt.prunable
                  ? tm(
                      'spcodeProjectLoad.diffSidebar.worktreeTabs.prunableTooltip',
                    )
                  : wt.path
              "
              @click="onWorktreeChange(wt.isMain ? null : wt.path)"
              @contextmenu.prevent="(e) => openContextMenu(e, wt)"
            >
              <v-icon
                v-if="wt.isMain"
                size="12"
                class="git-diff-sidebar-tab-icon"
                >mdi-home</v-icon
              >
              <v-icon
                v-else-if="wt.prunable"
                size="12"
                class="git-diff-sidebar-tab-icon git-diff-sidebar-tab-icon--prunable"
                >mdi-alert</v-icon
              >
              <v-icon
                v-else-if="wt.locked"
                size="12"
                class="git-diff-sidebar-tab-icon"
                >mdi-lock</v-icon
              >
              <span class="git-diff-sidebar-tab-label">
                {{
                  wt.branch ??
                  (wt.isMain
                    ? tm("spcodeProjectLoad.diffSidebar.worktreeTabs.mainBadge")
                    : wt.headSha.slice(0, 7))
                }}
              </span>
              <span
                v-if="wt.prunable"
                class="git-diff-sidebar-tab-badge git-diff-sidebar-tab-badge--prunable"
                >{{
                  tm("spcodeProjectLoad.diffSidebar.worktreeTabs.prunableBadge")
                }}</span
              >
              <span v-else-if="!wt.branch" class="git-diff-sidebar-tab-badge">{{
                tm("spcodeProjectLoad.diffSidebar.worktreeTabs.detachedBadge")
              }}</span>
            </button>
            <!-- Add button (spec 2026-06-27 §2.1) -->
            <button
              type="button"
              class="git-diff-sidebar-tab-add"
              :aria-label="
                tm('spcodeProjectLoad.diffSidebar.worktreeMgmt.addButtonAria')
              "
              :title="
                tm('spcodeProjectLoad.diffSidebar.worktreeMgmt.addButton')
              "
              @click="openCreateDialog"
            >
              <v-icon size="14">mdi-plus</v-icon>
            </button>

            <!-- Context menu (spec 2026-06-27 §2.3)
                     Teleported to <body> and positioned with manual
                     `position: fixed` styles so the menu opens exactly at the
                     right-click cursor. Vuetify 3 v-menu's positioning pipeline
                     (position-x/y + connectedLocationStrategy) is unreliable
                     when the activator is a [x, y] tuple instead of an
                     HTMLElement; the menu consistently fell back to the top-
                     left corner of the viewport regardless of cursor position.
                     Manual positioning gives us deterministic behavior and a
                     single source of truth (contextMenuStyle computed). -->
            <Teleport to="body">
              <div
                v-if="contextMenu.open"
                ref="contextMenuEl"
                class="worktree-context-menu"
                :style="contextMenuStyle"
                role="menu"
                :aria-label="
                  tm(
                    'spcodeProjectLoad.diffSidebar.worktreeMgmt.contextMenu.ariaLabel',
                  )
                "
                @click.stop
                @contextmenu.prevent
              >
                <v-list density="compact">
                  <template v-if="contextMenu.wt && !contextMenu.wt.isMain">
                    <!-- Lock/unlock toggle: never disabled. When the worktree
                             is locked this button reads "unlock" and the click
                             opens the unlock confirm dialog; when unlocked it
                             reads "lock" and opens the lock-reason dialog.
                             Disabling it when locked would prevent the very
                             action it represents. -->
                    <v-list-item @click="onLockClick(contextMenu.wt!)">
                      <template #prepend>
                        <v-icon>{{
                          contextMenu.wt.locked
                            ? "mdi-lock-open-variant"
                            : "mdi-lock"
                        }}</v-icon>
                      </template>
                      <v-list-item-title>{{
                        contextMenu.wt.locked
                          ? tm(
                              "spcodeProjectLoad.diffSidebar.worktreeMgmt.contextMenu.unlock",
                            )
                          : tm(
                              "spcodeProjectLoad.diffSidebar.worktreeMgmt.contextMenu.lock",
                            )
                      }}</v-list-item-title>
                    </v-list-item>
                    <!-- Remove: disabled only when locked (a locked worktree
                             must be unlocked before it can be removed). -->
                    <v-list-item
                      :disabled="!!contextMenu.wt.locked"
                      @click="onRemoveClick(contextMenu.wt!)"
                    >
                      <template #prepend>
                        <v-icon color="error">mdi-trash-can-outline</v-icon>
                      </template>
                      <v-list-item-title>{{
                        tm(
                          "spcodeProjectLoad.diffSidebar.worktreeMgmt.contextMenu.remove",
                        )
                      }}</v-list-item-title>
                    </v-list-item>
                  </template>
                  <template v-else>
                    <v-list-item disabled>
                      <v-list-item-title class="text-caption">
                        {{
                          tm(
                            "spcodeProjectLoad.diffSidebar.worktreeMgmt.contextMenu.mainDisabled",
                          )
                        }}
                      </v-list-item-title>
                    </v-list-item>
                  </template>
                </v-list>
              </div>
            </Teleport>
          </div>
        </div>
        <!-- /git-diff-sidebar-bw -->

        <!-- 2026-08-01 git-conflict: banner + resolution panel, mounted
             outside the per-view templates so it stays visible in every
             view mode while a conflict operation is in progress. -->
        <GitConflictPanel
          :conflict="gitConflict"
          @resolved="onConflictResolved"
          @completed="onConflictCompleted"
          @failed="onConflictFailed"
          @expand-change="onConflictExpandChange"
        />

        <!-- Diff-only sub-UI: scope bar + truncation warning -->
        <template v-if="viewMode === 'diff'">
          <div
            class="git-diff-sidebar-scope"
            role="tablist"
            :aria-label="tm('spcodeProjectLoad.diffSidebar.scopeBar.ariaLabel')"
          >
            <div class="git-diff-sidebar-scope-pills">
              <button
                v-for="opt in SCOPE_OPTIONS"
                :key="opt.value"
                type="button"
                role="tab"
                :aria-selected="selectedScope === opt.value"
                :aria-label="tm(opt.labelKey)"
                :class="[
                  'git-diff-sidebar-scope-pill',
                  `is-${opt.value}`,
                  { 'is-active': selectedScope === opt.value },
                ]"
                :disabled="
                  !isProjectLoaded ||
                  (isScopeLoading && pendingScope !== opt.value)
                "
                @click="onScopeChange(opt.value)"
              >
                <v-icon size="14" class="git-diff-sidebar-scope-pill-icon">
                  {{ opt.icon }}
                </v-icon>
                <span class="git-diff-sidebar-scope-pill-text">
                  {{ tm(opt.labelKey) }}
                </span>
                <v-progress-circular
                  v-if="isScopeLoading && pendingScope === opt.value"
                  indeterminate
                  :size="12"
                  :width="2"
                  class="git-diff-sidebar-scope-pill-spinner"
                />
              </button>
            </div>
          </div>
          <div v-if="isTruncated" class="git-diff-sidebar-warning">
            {{
              tm("spcodeProjectLoad.diffSidebar.truncated", {
                shown: truncatedShown,
                max: truncatedMax,
              })
            }}
          </div>
        </template>

        <!-- Spec 2026-07-16: GitRepoInitPrompt + dismissed chip slot. The
             prompt occupies the full body area when shown; the chip
             floats above the body in dismissed mode so Files view
             (which does not need Git) stays usable. -->
        <GitRepoInitPrompt
          v-if="showRepoInitPrompt"
          :directory="
            gitRepoProbe.state.value.kind === 'not_a_git_repo'
              ? gitRepoProbe.state.value.directory
              : ''
          "
          :is-submitting="isRepoInitSubmitting"
          :last-error="repoInitLastError"
          @confirm="onRepoInitConfirm"
          @cancel="onRepoInitCancel"
        />
        <div
          v-else-if="showNotGitRepoChip"
          class="git-diff-sidebar-repo-chip"
          role="status"
        >
          <v-icon size="14">mdi-information-outline</v-icon>
          <span>{{
            tm("spcodeProjectLoad.diffSidebar.repoInit.dismissedChip")
          }}</span>
          <button
            type="button"
            class="git-diff-sidebar-repo-chip-action"
            @click="repoPromptDismissed = false"
          >
            {{ tm("spcodeProjectLoad.diffSidebar.repoInit.reopenPrompt") }}
          </button>
        </div>

        <!-- Body: Files / Diff / History -->
        <div
          ref="sidebarBodyRef"
          class="git-diff-sidebar-body"
          v-show="!conflictBodyHidden"
        >
          <!-- 2026-07-18 workspace-search-parity: the files-view search
             toolbar moved INTO FileBrowserView (mirroring
             DocumentManager) so it travels with the file-area
             fullscreen Teleport instead of being left behind in the
             sidebar. The body now renders FileBrowserView directly. -->
          <FileBrowserView
            v-if="viewMode === 'files'"
            ref="fileBrowserRef"
            :current-path="fileBrowserCurrentPath"
            :preview-path="fileBrowserPreviewPath"
            :is-dark="!!isDark"
            :root-path="currentRoot"
            :scroll-to-line="fileSearchScrollToLine"
            :umo="spcodeStatus.status.value.umo"
            :worktree="selectedWorktree"
            :git-log="gitLog"
            :git-show="gitShow"
            :recent-entries="recentFiles.entries.value"
            @navigate="onFileBrowserNavigate"
            @open-file="onFileOpen"
            @recent-select="onRecentSelect"
            @recent-remove="(p) => recentFiles.remove(p.path)"
            @recent-clear="showClearConfirm = true"
          />
          <GitDiffBodyContent
            v-else-if="viewMode === 'diff'"
            ref="gitDiffBodyRef"
            :state="diffBodyState"
            :expanded="expandedSet"
            :is-dark="!!isDark"
            :on-restore="onFileRestore"
            :selected-scope="selectedScope"
            :on-stage="onStageFile"
            :on-unstage="onUnstageFile"
            :is-staging="gitStage.isStaging"
            :is-unstaging="gitUnstage.isUnstaging"
            :new-file-paths="newFilePaths"
            :on-open-file="onOpenFile"
            :on-discard-hunk="onDiscardHunk"
            :discarding-hunks="fileDiscardHunk.isDiscardingHunk.value"
            @toggle="toggleFile"
            @retry="onManualRefresh"
            @restore="onFileRestore"
            @stage="onStageFile"
            @unstage="onUnstageFile"
            @open-file="onOpenFile"
            @stage-paths="onStagePaths"
            @unstage-paths="onUnstagePaths"
            @restore-paths="onRestorePaths"
          />
          <!-- Spec 2026-06-24 §6.5:History view 渲染 GitLogView。
             Spec 2026-06-25 §3.1:GitLogView 也接收 gitShow 句柄用于
             在 commit 展开时拉取 /spcode/git-show 并渲染变更文件列表。
             2026-09-08 (elecvoid243): key on the worktree so a switch
             remounts the view — this resets its local filter form
             (the applied filter is reset inside useSpcodeGitLog), the
             expanded commits and any squash / changelog selection. -->
          <GitLogView
            v-else-if="viewMode === 'history'"
            :key="selectedWorktree ?? 'main'"
            :state="gitLog.state.value"
            :has-more="logHasMore"
            :is-loading="logIsLoading"
            :git-show="gitShow"
            :focused-commit-sha="focusedCommitSha"
            :git-stats="gitStats"
            v-model:stats-open="statsOpen"
            :range="gitStatsRange"
            :top-files-limit="gitStatsTopFilesLimit"
            :branch-items="branchPickerItems"
            :tag-items="tagPickerItems"
            :current-branch="currentBranchName"
            :active-branch="gitLog.filter.value.ref ?? null"
            :head-sha="currentHeadSha"
            :applied-grep="gitLog.filter.value.grep ?? ''"
            :squash-reset-token="squashResetToken"
            :changelog-reset-token="changelogResetToken"
            @update:range="(v) => (gitStatsRange = v)"
            @update:top-files-limit="(v) => (gitStatsTopFilesLimit = v)"
            @apply="onLogApply"
            @reset="onLogReset"
            @load-more="onLogLoadMore"
            @refresh="onLogRefresh"
            @revert="onLogRevertRequest"
            @reset-branch="onLogResetRequest"
            @cherry-pick="onLogCherryPickRequest"
            @amend="onLogAmendRequest"
            @cherry-pick-blank="onToolbarCherryPickRequest"
            @squash="onLogSquashRequest"
            @changelog="onChangelogRequest"
          />
          <!-- 2026-07-11 document-manager:Documents 文档管理 sub-tab body。 -->
          <!--
          2026-07-14: pass currentRoot (not projectRoot) to
          DocumentManager so the docs sub-page is rooted at the
          active worktree. The file-browser endpoint
          (/spcode/file-browser) is stateless and resolves
          whatever absolute path we hand it, so feeding it the
          worktree root is what makes the breadcrumb's "项目根"
          segment show the worktree path (e.g.
          F:/repo/.worktrees/feature-x) instead of always
          collapsing back to the main repo root. The docs CRUD
          composable (useSpcodeDocs) already pins writes to
          `:worktree` above, so the listing + writes now agree
          on which worktree is in scope. Mirrors what
          FileBrowserView has been doing for the workspace tab.
        -->
          <DocumentManager
            v-else-if="viewMode === 'docs'"
            :worktree="selectedWorktree"
            :umo="spcodeStatus.status.value.umo"
            :project-root="currentRoot"
            :is-dark="!!isDark"
            :git-log="gitLog"
            :git-show="gitShow"
          />
          <!-- 2026-09-01 terminal:终端子页面 body。 -->
          <TerminalView
            v-else-if="viewMode === 'terminal'"
            :umo="spcodeStatus.status.value.umo"
            :project-root="currentRoot"
            :is-dark="!!isDark"
          />
        </div>

        <!-- Spec §6.7:粘性 commit bar(diff 视图下显示)。
           移动端 spec §10 风险 #10:仍可见,按钮缩窄。 -->
        <GitCommitBar
          v-if="viewMode === 'diff' && isProjectLoaded"
          :staged-count="stagedCount"
          :unstaged-count="unstagedCount"
          :is-staging-all="gitStage.isStagingAll.value"
          :is-unstaging-all="gitUnstage.isUnstagingAll.value"
          :is-committing="gitCommit.isCommitting.value"
          :is-stashing="gitStash.isStashing.value"
          :selected-scope="selectedScope"
          @stage-all="onClickStageAll"
          @unstage-all="onClickUnstageAll"
          @commit="onClickCommit"
          @open-gitignore="onOpenGitIgnoreEditor"
          @open-stash="stashDialogOpen = true"
        />

        <!-- Spec §6.3: inline <v-dialog persistent> confirmation.
           Two guards against "stacking" / flicker:
             1. `v-if="confirmDialogOpen"` on v-card so the v-card
                is unmounted SYNCHRONOUSLY the instant the dialog
                starts closing. Without this, v-dialog's close
                transition keeps the v-card alive for several
                frames, during which `confirmTargetIsNew` may
                already have flipped to false and the v-card-text
                re-renders the *old* wording in place — perceived
                as a brief flash of the wrong message.
             2. `:key="confirmTargetPath ?? 'empty'"` on v-card so
                opening the dialog with a different path (or
                reopening after cancel) unmounts the previous
                v-card and mounts a fresh one. Avoids the
                in-place text swap flicker on open. -->
        <v-dialog v-model="confirmDialogOpen" persistent max-width="440">
          <v-card v-if="confirmDialogOpen" :key="confirmTargetPath ?? 'empty'">
            <v-card-title class="text-h6">
              {{ tm("spcodeProjectLoad.diffSidebar.restore.confirmTitle") }}
            </v-card-title>
            <v-card-text>
              {{
                tm(
                  confirmTargetIsNew
                    ? "spcodeProjectLoad.diffSidebar.restore.confirmMessageNewFile"
                    : "spcodeProjectLoad.diffSidebar.restore.confirmMessage",
                  {
                    path: confirmTargetPath ?? "",
                  },
                )
              }}
            </v-card-text>
            <v-card-actions>
              <v-spacer />
              <v-btn variant="text" @click="onCancelRestore">{{
                tm("spcodeProjectLoad.diffSidebar.restore.confirmCancel")
              }}</v-btn>
              <v-btn
                variant="flat"
                color="warning"
                :loading="restoringFile !== null"
                @click="onConfirmRestore"
                >{{
                  tm("spcodeProjectLoad.diffSidebar.restore.confirmAction")
                }}</v-btn
              >
            </v-card-actions>
          </v-card>
        </v-dialog>

        <!-- Bulk-restore confirmation: separate dialog so the message
           can include the file count and so a single Cancel cleanly
           aborts the whole batch (the underlying endpoint only
           accepts one file per request, so a partial-restore
           mid-flight would be confusing UX). Tinted `warning` to
           match the single-file dialog and signal irreversibility. -->
        <v-dialog v-model="confirmRestorePathsOpen" persistent max-width="440">
          <v-card>
            <v-card-title class="text-h6">
              {{
                tm(
                  "spcodeProjectLoad.diffSidebar.restore.confirmTitleMultiple",
                  { count: pendingRestorePaths.length },
                )
              }}
            </v-card-title>
            <v-card-text>
              {{
                tm(
                  "spcodeProjectLoad.diffSidebar.restore.confirmMessageMultiple",
                  { count: pendingRestorePaths.length },
                )
              }}
            </v-card-text>
            <v-card-actions>
              <v-spacer />
              <v-btn
                variant="text"
                :disabled="isBulkRestoring"
                @click="onCancelRestorePaths"
                >{{
                  tm("spcodeProjectLoad.diffSidebar.restore.confirmCancel")
                }}</v-btn
              >
              <v-btn
                variant="flat"
                color="warning"
                :loading="isBulkRestoring"
                @click="onConfirmRestorePaths"
                >{{
                  tm("spcodeProjectLoad.diffSidebar.restore.confirmAction")
                }}</v-btn
              >
            </v-card-actions>
          </v-card>
        </v-dialog>

        <!-- 2026-07-17 git-revert: History-view revert confirmation.
             The endpoint creates a NEW revert commit (no history
             rewrite) — the hint line spells that out plus the
             clean-worktree prerequisite. -->
        <!-- 2026-08-01 git-merge: strategy dialog for the branch-menu
             merge action. -->
        <GitMergeDialog
          v-model="mergeDialogOpen"
          :source="mergeSource"
          :loading="gitMerge.isMerging.value"
          @submit="onMergeSubmit"
        />

        <!-- 2026-08-12 git-pull/push/remote: pull options + remote URL
             dialogs. 2026-08-13: push gained a confirmation dialog
             (remote / local branch / remote target branch). -->
        <GitPullDialog
          v-model="pullDialogOpen"
          :loading="gitRemoteSync.isPulling.value"
          @submit="onPullSubmit"
        />
        <GitPushDialog
          v-model="pushDialogOpen"
          :loading="gitRemoteSync.isPushing.value"
          :remotes="remoteNames"
          :local-branches="localBranchNames"
          :current-branch="currentBranchName"
          :upstream="currentBranchUpstream"
          @submit="onPushSubmit"
        />
        <GitRemoteUrlDialog
          v-model="remoteUrlDialogOpen"
          :loading="gitRemoteSync.isSettingRemote.value"
          :remotes="remoteList"
          :listing="gitRemoteSync.isListingRemotes.value"
          :removing="remoteRemoving"
          @submit="onRemoteUrlSubmit"
          @remove="onRemoteRemove"
        />

        <!-- 2026-08-21 git-stash: stash current changes + browse the
             existing stash entries with their file lists. -->
        <GitStashDialog
          v-model="stashDialogOpen"
          :stashes="stashEntries"
          :listing="gitStash.listState.value.kind === 'loading'"
          :is-stashing="gitStash.isStashing.value"
          :popping="gitStash.popping.value"
          :dropping="gitStash.dropping.value"
          :has-local-changes="hasLocalChanges"
          :load-error="stashLoadError"
          :truncated="stashListTruncated"
          @stash="onStashSubmit"
          @pop="onStashPop"
          @drop="onStashDrop"
          @refresh="gitStash.refreshList()"
        />

        <!-- 2026-08-01 git-cherry-pick: dialog for log-row + toolbar
             entries. -->
        <GitCherryPickDialog
          v-model="cherryPickDialogOpen"
          :preset="cherryPickPreset"
          :loading="gitMerge.isCherryPicking.value"
          @submit="onCherryPickSubmit"
        />

        <!-- 2026-08-13 git-commit-amend: dialog for editing the HEAD
                     commit message. -->
        <GitCommitAmendDialog
          v-model="amendDialogOpen"
          :commit="pendingAmend"
          :loading="gitCommitAmend.isAmending.value"
          @submit="onAmendSubmit"
        />

        <!-- 2026-08-03 git-squash: dialog for the history-view
             multi-commit squash (commits listed oldest → newest). -->
        <GitSquashDialog
          v-model="squashDialogOpen"
          :commits="pendingSquash ?? []"
          :loading="gitSquash.isSquashing.value"
          @submit="onSquashSubmit"
        />

        <!-- 2026-08-09 changelog: generation dialog (commits listed
             oldest → newest, same payload as squash). -->
        <GitChangelogDialog
          v-model="changelogDialogOpen"
          :commits="changelogCommits"
          :worktree="selectedWorktree"
          @saved="onChangelogSaved"
          @close="onChangelogDialogClose"
        />

        <v-dialog v-model="revertDialogOpen" persistent max-width="440">
          <v-card>
            <v-card-title class="text-h6">
              {{
                tm(
                  "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertConfirmTitle",
                )
              }}
            </v-card-title>
            <v-card-text>
              <div>
                {{
                  tm(
                    "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertConfirmMessage",
                    {
                      sha: pendingRevert?.sha.slice(0, 7) ?? "",
                      subject: pendingRevert?.subject ?? "",
                    },
                  )
                }}
              </div>
              <div class="git-diff-sidebar__revert-hint">
                {{
                  tm(
                    "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertConfirmHint",
                  )
                }}
              </div>
            </v-card-text>
            <v-card-actions>
              <v-spacer />
              <v-btn variant="text" @click="onCancelRevert">
                {{
                  tm(
                    "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertConfirmCancel",
                  )
                }}
              </v-btn>
              <v-btn
                variant="flat"
                color="primary"
                :loading="gitRevert.isReverting.value"
                @click="onConfirmRevert"
              >
                {{
                  tm(
                    "spcodeProjectLoad.diffSidebar.gitWorkflow.history.revertConfirmAction",
                  )
                }}
              </v-btn>
            </v-card-actions>
          </v-card>
        </v-dialog>

        <!-- 2026-09-09 git-reset: SourceTree-style "Reset current
             branch to this commit" dialog. Mode radios (soft/mixed/
             hard) + typed SHA-prefix confirmation for ALL modes —
             the confirm button stays disabled until the input matches
             the target commit's first 7 chars (case-insensitive). -->
        <v-dialog v-model="resetDialogOpen" persistent max-width="480">
          <v-card>
            <v-card-title class="text-h6">
              {{
                tm(
                  "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetConfirmTitle",
                )
              }}
            </v-card-title>
            <v-card-text>
              <div>
                {{
                  tm(
                    "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetConfirmMessage",
                    {
                      sha: pendingReset?.sha.slice(0, 7) ?? "",
                      subject: pendingReset?.subject ?? "",
                    },
                  )
                }}
              </div>
              <v-radio-group
                v-model="resetMode"
                density="compact"
                hide-details
                class="mt-3"
              >
                <v-radio
                  value="soft"
                  :label="
                    tm(
                      'spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetModeSoft',
                    )
                  "
                />
                <v-radio
                  value="mixed"
                  :label="
                    tm(
                      'spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetModeMixed',
                    )
                  "
                />
                <v-radio
                  value="hard"
                  :label="
                    tm(
                      'spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetModeHard',
                    )
                  "
                />
              </v-radio-group>
              <div class="git-diff-sidebar__revert-hint">
                {{ resetModeHint }}
              </div>
              <div
                v-if="resetMode === 'hard' && pendingResetDirtyCount > 0"
                class="text-warning text-caption mt-1"
              >
                {{
                  tm(
                    "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetHardDirtyWarning",
                    { count: pendingResetDirtyCount },
                  )
                }}
              </div>
              <v-text-field
                v-model="resetConfirmSha"
                :label="
                  tm(
                    'spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetConfirmShaLabel',
                    { sha: pendingReset?.sha.slice(0, 7) ?? '' },
                  )
                "
                :hint="
                  tm(
                    'spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetConfirmShaHint',
                  )
                "
                persistent-hint
                density="compact"
                autocomplete="off"
                class="mt-3"
                @keyup.enter="resetConfirmReady && onConfirmReset()"
              />
            </v-card-text>
            <v-card-actions>
              <v-spacer />
              <v-btn variant="text" @click="onCancelReset">
                {{
                  tm(
                    "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetConfirmCancel",
                  )
                }}
              </v-btn>
              <v-btn
                variant="flat"
                color="warning"
                :disabled="!resetConfirmReady"
                :loading="gitReset.isResetting.value"
                @click="onConfirmReset"
              >
                {{
                  tm(
                    "spcodeProjectLoad.diffSidebar.gitWorkflow.history.resetConfirmAction",
                  )
                }}
              </v-btn>
            </v-card-actions>
          </v-card>
        </v-dialog>

        <!-- Spec §6.3: 全部暂存确认弹窗(spec 2026-06-24 §3.3.3 + Q4)。 -->
        <v-dialog v-model="confirmStageAllOpen" persistent max-width="440">
          <v-card>
            <v-card-title class="text-h6">
              {{
                tm(
                  "spcodeProjectLoad.diffSidebar.gitWorkflow.stage.stageAll.confirmTitle",
                )
              }}
            </v-card-title>
            <v-card-text>
              {{
                tm(
                  "spcodeProjectLoad.diffSidebar.gitWorkflow.stage.stageAll.confirmMessage",
                  { count: pendingStageAllCount },
                )
              }}
            </v-card-text>
            <v-card-actions>
              <v-spacer />
              <v-btn variant="text" @click="onCancelStageAll">
                {{
                  tm(
                    "spcodeProjectLoad.diffSidebar.gitWorkflow.stage.stageAll.confirmCancel",
                  )
                }}
              </v-btn>
              <v-btn
                variant="flat"
                color="primary"
                :loading="gitStage.isStagingAll.value"
                @click="onConfirmStageAll"
              >
                {{
                  tm(
                    "spcodeProjectLoad.diffSidebar.gitWorkflow.stage.stageAll.confirmAction",
                  )
                }}
              </v-btn>
            </v-card-actions>
          </v-card>
        </v-dialog>

        <!-- Symmetric dialog for the staged scope's "取消全部暂存"
           bulk action. Mirrors confirmStageAllOpen in structure so
           the two flows feel identical from the user's POV — same
           persistent modal, same title/message/cancel/action layout,
           just routed through the unstage endpoint and tinted
           `warning` to signal the index-mutating direction. -->
        <v-dialog v-model="confirmUnstageAllOpen" persistent max-width="440">
          <v-card>
            <v-card-title class="text-h6">
              {{
                tm(
                  "spcodeProjectLoad.diffSidebar.gitWorkflow.unstage.unstageAll.confirmTitle",
                )
              }}
            </v-card-title>
            <v-card-text>
              {{
                tm(
                  "spcodeProjectLoad.diffSidebar.gitWorkflow.unstage.unstageAll.confirmMessage",
                  { count: pendingUnstageAllCount },
                )
              }}
            </v-card-text>
            <v-card-actions>
              <v-spacer />
              <v-btn variant="text" @click="onCancelUnstageAll">
                {{
                  tm(
                    "spcodeProjectLoad.diffSidebar.gitWorkflow.unstage.unstageAll.confirmCancel",
                  )
                }}
              </v-btn>
              <v-btn
                variant="flat"
                color="warning"
                :loading="gitUnstage.isUnstagingAll.value"
                @click="onConfirmUnstageAll"
              >
                {{
                  tm(
                    "spcodeProjectLoad.diffSidebar.gitWorkflow.unstage.unstageAll.confirmAction",
                  )
                }}
              </v-btn>
            </v-card-actions>
          </v-card>
        </v-dialog>

        <!-- Recent Files: Clear confirmation (spec 2026-07-20 recent-files §6.3).
             Shape mirrors the existing delete / restore dialogs. Tinted
             `error` because clearing is irreversible (no undo); not
             `warning` (which sidebar uses for reversible-but-impactive
             flows like unstage-all). -->
        <v-dialog v-model="showClearConfirm" max-width="420">
          <v-card>
            <v-card-title class="text-h3 pa-4 pb-0 pl-6">
              {{
                tm(
                  "spcodeProjectLoad.fileBrowser.recentFiles.clearConfirmTitle",
                )
              }}
            </v-card-title>
            <v-card-text class="pt-4">
              {{
                tm(
                  "spcodeProjectLoad.fileBrowser.recentFiles.clearConfirmMessage",
                )
              }}
            </v-card-text>
            <v-card-actions class="pa-4 pt-0">
              <v-spacer />
              <v-btn variant="text" @click="showClearConfirm = false">
                {{ tm("spcodeProjectLoad.fileBrowser.editor.cancel") }}
              </v-btn>
              <v-btn variant="text" color="error" @click="onConfirmClear">
                {{ tm("spcodeProjectLoad.fileBrowser.recentFiles.clear") }}
              </v-btn>
            </v-card-actions>
          </v-card>
        </v-dialog>

        <!-- Worktree CREATE dialog (spec 2026-06-27 §2.2.A) -->
        <WorktreeCreateDialog
          v-model="createDialogOpen"
          :is-submitting="isCreating"
          @submit="onCreateSubmit"
          @cancel="createDialogOpen = false"
        />

        <!-- Branch SWITCH confirm dialog (spec 2026-07-21 §3.4.1) -->
        <BranchSwitchConfirmDialog
          v-if="switchTarget"
          v-model="switchDialogOpen"
          :from="switchTarget.from"
          :to="switchTarget.to"
          :dirty-count="switchDirtyCount"
          :is-submitting="isBranchSwitching"
          @confirm="onBranchSwitchConfirm"
          @cancel="switchDialogOpen = false"
        />

        <!-- Branch DELETE confirm dialog (spec 2026-07-21 §3.4.2) -->
        <BranchDeleteConfirmDialog
          v-if="deleteTarget !== null"
          v-model="deleteDialogOpen"
          :name="deleteTarget"
          :is-submitting="isBranchDeleting"
          @confirm="onBranchDeleteConfirm"
          @cancel="deleteDialogOpen = false"
        />

        <!-- Worktree REMOVE confirm dialog (spec 2026-06-27 §2.2.B) -->
        <v-dialog v-model="removeDialogOpen" persistent max-width="480">
          <v-card>
            <v-card-title class="text-h6">
              {{
                tm(
                  "spcodeProjectLoad.diffSidebar.worktreeMgmt.remove.confirmTitle",
                )
              }}
            </v-card-title>
            <v-card-text>
              <p class="mb-2">
                {{
                  tm(
                    "spcodeProjectLoad.diffSidebar.worktreeMgmt.remove.confirmMessageWithPath",
                    {
                      path: removeDialogTarget?.path ?? "",
                      branch: removeDialogTarget?.branch ?? "",
                    },
                  )
                }}
              </p>
              <p
                v-if="dirtyCount !== null && dirtyCount > 0"
                class="text-caption text-warning mb-2"
              >
                {{
                  tm(
                    "spcodeProjectLoad.diffSidebar.worktreeMgmt.remove.dirtyHint",
                    { count: dirtyCount },
                  )
                }}
              </p>
              <v-checkbox
                v-if="dirtyCount !== null && dirtyCount > 0"
                v-model="removeForceChecked"
                density="compact"
                :label="
                  tm(
                    'spcodeProjectLoad.diffSidebar.worktreeMgmt.remove.force',
                    {
                      count: dirtyCount,
                    },
                  )
                "
                color="warning"
                hide-details
              />
            </v-card-text>
            <v-card-actions>
              <v-spacer />
              <v-btn
                variant="text"
                :disabled="isRemoving"
                @click="removeDialogOpen = false"
              >
                {{
                  tm("spcodeProjectLoad.diffSidebar.worktreeMgmt.remove.cancel")
                }}
              </v-btn>
              <v-btn
                variant="flat"
                color="warning"
                :loading="isRemoving"
                @click="onConfirmRemove(removeForceChecked)"
              >
                {{
                  tm(
                    "spcodeProjectLoad.diffSidebar.worktreeMgmt.remove.confirm",
                  )
                }}
              </v-btn>
            </v-card-actions>
          </v-card>
        </v-dialog>

        <!-- Worktree LOCK dialog (spec 2026-06-27 §2.2.C) -->
        <v-dialog v-model="lockDialogOpen" persistent max-width="480">
          <LockReasonDialogBody
            v-if="lockDialogOpen"
            :target-branch="lockDialogTarget?.branch ?? null"
            :is-locking="isLocking"
            @submit="onLockSubmit"
            @cancel="lockDialogOpen = false"
          />
        </v-dialog>

        <!-- Worktree UNLOCK confirm dialog (spec 2026-06-27 §2.2.D) -->
        <v-dialog v-model="confirmUnlockOpen" persistent max-width="440">
          <v-card>
            <v-card-title class="text-h6">
              {{
                tm(
                  "spcodeProjectLoad.diffSidebar.worktreeMgmt.unlock.confirmTitle",
                )
              }}
            </v-card-title>
            <v-card-text>
              {{
                tm(
                  "spcodeProjectLoad.diffSidebar.worktreeMgmt.unlock.confirmMessage",
                )
              }}
            </v-card-text>
            <v-card-actions>
              <v-spacer />
              <v-btn
                variant="text"
                :disabled="isUnlocking"
                @click="onCancelUnlock"
              >
                {{
                  tm("spcodeProjectLoad.diffSidebar.worktreeMgmt.unlock.cancel")
                }}
              </v-btn>
              <v-btn
                variant="flat"
                color="primary"
                :loading="isUnlocking"
                @click="onConfirmUnlock"
              >
                {{
                  tm(
                    "spcodeProjectLoad.diffSidebar.worktreeMgmt.unlock.confirm",
                  )
                }}
              </v-btn>
            </v-card-actions>
          </v-card>
        </v-dialog>

        <!-- Spec §6.4: 提交弹窗。 -->
        <GitCommitDialog
          v-model="commitDialogOpen"
          :staged-files="Array.from(stagedFiles)"
          :is-committing="gitCommit.isCommitting.value"
          :last-error="commitLastError ?? undefined"
          :umo="spcodeStatus.status.value.umo"
          :worktree="selectedWorktree"
          @confirm="onConfirmCommit"
          @cancel="onCancelCommit"
        />

        <!-- 2026-07-17 gitignore-editor: full-sidebar overlay. Mounted
             as a direct child of the root so position:absolute inset:0
             covers header + body + commit bar alike. -->
        <GitIgnoreEditor
          v-if="gitIgnoreEditorOpen"
          :initial-content="gitIgnoreLoadedContent"
          :is-new-file="gitIgnoreIsNewFile"
          :is-saving="gitIgnoreFileWrite.isSaving.value"
          :save-error="gitIgnoreSaveError"
          :load-error="gitIgnoreLoadError"
          @save="onGitIgnoreSave"
          @cancel="onGitIgnoreCancel"
          @retry="loadGitIgnore"
        />

        <!-- Spec §6.4: result snackbar (扩展:支持 stderr <pre> 块)。
             2026-09-09 toast-close: 右侧固定手动关闭按钮,toast 遮住
             下方操作按钮时用户可立即关掉,不必等 timeout 走完。 -->
        <v-snackbar
          v-model="snackbar.show"
          :color="snackbar.color"
          :timeout="snackbar.color === 'success' ? 4000 : 6000"
          location="bottom right"
          multi-line
        >
          <div class="spcode-snackbar-body">
            <div class="spcode-snackbar-body__text">
              <div v-if="snackbar.stderr" class="spcode-snackbar-stderr">
                <div class="spcode-snackbar-message">
                  {{ snackbar.message }}
                </div>
                <pre class="spcode-snackbar-pre">{{ snackbar.stderr }}</pre>
              </div>
              <div v-else>{{ snackbar.message }}</div>
            </div>
            <button
              type="button"
              class="spcode-snackbar-close"
              :aria-label="tm('spcodeProjectLoad.diffSidebar.toastClose')"
              :title="tm('spcodeProjectLoad.diffSidebar.toastClose')"
              @click="snackbar.show = false"
            >
              <v-icon size="16">mdi-close</v-icon>
            </button>
          </div>
        </v-snackbar>
      </aside>
    </transition>
  </Teleport>
</template>

<style scoped>
/* 2026-07-17 git-revert: secondary hint line inside the revert
   confirmation dialog (explains the new-commit behavior). */
.git-diff-sidebar__revert-hint {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.5;
  color: rgba(var(--v-theme-on-surface), 0.55);
}
.git-diff-sidebar {
  /* Anchors the .gitignore-editor overlay's absolute inset:0. */
  position: relative;
  width: 420px;
  height: calc(100% - var(--chat-panel-top-offset, 0px));
  margin-top: var(--chat-panel-top-offset, 0px);
  /* Tokens (defined on .chat-ui in Chat.vue) keep borders & backgrounds in
     sync with chat's session list / ReasoningSidebar across light & dark. */
  border-left: 1px solid
    var(--chat-border, rgba(var(--v-theme-on-surface), 0.1));
  background: var(--chat-sidebar-bg, rgb(var(--v-theme-surface)));
  color: rgb(var(--v-theme-on-surface));
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  position: relative;
}

/* Global workspace fullscreen: teleport's destination is fixed and
   fills the viewport. The inline width binding (user-resized sidebar)
   would otherwise cap us at the drag width, so it is overridden with
   !important. The high z-index sits above the chat layout (sidebar
   normally lives inside the chat-panel flex; teleporting to <body>
   removes that stacking context). */
.git-diff-sidebar.is-fullscreen {
  position: fixed;
  inset: 0;
  z-index: 1300;
  width: 100vw !important;
  height: 100vh;
  margin-top: 0;
  border-left: 0;
}

/* ── Drag handle ──────────────────────────────────────────────── */

.git-diff-sidebar-resizer {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  /* 6px constant hit-area. Matches ReasoningSidebar / TodoSidebar
     so all three sidebars share the same drag-handle feel. Hover
     only swaps the background colour - no width transition. */
  width: 6px;
  background: var(--chat-border, rgba(var(--v-theme-on-surface), 0.1));
  cursor: ew-resize;
  z-index: 10;
  transition: background 0.15s ease;
}

.git-diff-sidebar-resizer:hover,
.git-diff-sidebar-resizer:active {
  /* 0.2 alpha matches ReasoningSidebar / TodoSidebar so all three
     sidebars share the same hover affordance. */
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

.git-diff-sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px 10px;
  gap: 8px;
}

.git-diff-sidebar-title-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.git-diff-sidebar-title {
  font-size: 15.5px;
  font-weight: 600;
  line-height: 1.4;
  letter-spacing: 0.01em;
  color: rgb(var(--v-theme-on-surface));
}
/* UI #5: removed the inline folder icon + tooltip (`.git-diff-sidebar-dir*`)
   selectors — the project path is now rendered as a dedicated strip
   directly below the header (see .git-diff-sidebar-path-strip). Kept the
   dir selectors as no-op so any external style override (e.g. user
   CSS, devtools experiments) doesn't break the layout. */
.git-diff-sidebar-dir-icon {
  color: var(--chat-section-label, rgba(var(--v-theme-on-surface), 0.54));
}
.git-diff-sidebar-dir {
  font-size: 12px;
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.62));
}
.git-diff-sidebar-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

/* ── Path strip (UI #5) ──────────────────────────────────────────── */

.git-diff-sidebar-path-strip {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 0 14px 10px;
  /* Use chat's main font (no monospace) so the strip reads like
     a breadcrumb / status row, not a code path. */
  user-select: text;
}

.git-diff-sidebar-path-line {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.git-diff-sidebar-path-icon {
  color: var(--chat-section-label, rgba(var(--v-theme-on-surface), 0.48));
  flex-shrink: 0;
}

.git-diff-sidebar-path-text {
  font-size: 12px;
  font-weight: 500;
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.62));
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  direction: rtl; /* keep the END (project root) visible on overflow */
  text-align: left;
}

.git-diff-sidebar-path-sub {
  font-size: 11px;
  color: var(--chat-section-label, rgba(var(--v-theme-on-surface), 0.48));
  margin-left: 18px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-feature-settings: "tnum";
}

/* ── View-mode tab (spec 2026-06-20 §5.2) ──────────────────── */
/* Pill-style segmented control. Aligned with chat's session list buttons
   (border-radius: 8px, smooth color transitions, no underlines). */
.git-diff-sidebar-view-tabs {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin: 0 12px 8px;
  padding: 4px;
  border-radius: 10px;
  background: var(
    --chat-session-active-bg,
    rgba(var(--v-theme-on-surface), 0.06)
  );
}
.git-diff-sidebar-view-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 7px;
  border: 0;
  background: transparent;
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.62));
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition:
    background 0.14s ease,
    color 0.14s ease,
    box-shadow 0.14s ease;
}
.git-diff-sidebar-view-tab:hover {
  color: rgb(var(--v-theme-on-surface));
}
.git-diff-sidebar-view-tab.is-active {
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-primary));
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.git-diff-sidebar-view-tab:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}

/* ── Scope bar (spec 2026-06-20 §2.1) ──────────────────────── */

.git-diff-sidebar-scope {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  /* Distinct from worktree tabs: a subtle filled strip that visually
     frames the segmented control as one cohesive filter unit. */
  background: rgba(var(--v-theme-on-surface), 0.035);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.git-diff-sidebar-scope-pills {
  display: flex;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.git-diff-sidebar-scope-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 4px;
  border: 1px solid transparent;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.7);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  flex: 1;
  min-width: 0;
  transition:
    background 0.12s ease,
    color 0.12s ease,
    border-color 0.12s ease,
    opacity 0.12s ease;
}

.git-diff-sidebar-scope-pill:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgb(var(--v-theme-on-surface));
}

.git-diff-sidebar-scope-pill:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}

.git-diff-sidebar-scope-pill:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.git-diff-sidebar-scope-pill.is-active {
  font-weight: 500;
}

/* Per-scope accent colors. Use theme tokens so the pills follow
   AstrBot's primary palette instead of hardcoded greens. Material's
   success token is intentionally absent in default Vuetify; we fall
   back to a 4caf50 default that is overridden by any theme that
   defines --v-theme-success. */
.git-diff-sidebar-scope-pill.is-unstaged.is-active {
  color: rgb(var(--v-theme-secondary));
  background: rgba(var(--v-theme-secondary), 0.14);
  border-color: rgba(var(--v-theme-secondary), 0.4);
}
.git-diff-sidebar-scope-pill.is-staged.is-active {
  color: rgb(var(--v-theme-success, #4caf50));
  background: rgba(var(--v-theme-success, #4caf50), 0.14);
  border-color: rgba(var(--v-theme-success, #4caf50), 0.4);
}
.git-diff-sidebar-scope-pill.is-all.is-active {
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.14);
  border-color: rgba(var(--v-theme-primary), 0.4);
}

.git-diff-sidebar-scope-pill-icon {
  color: inherit;
  flex-shrink: 0;
}

.git-diff-sidebar-scope-pill-text {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.git-diff-sidebar-scope-pill-spinner {
  flex-shrink: 0;
}

/* ── Branch + Worktree wrapper (2026-07-22) ──────────────
   Transparent group around the branch switcher row and the worktree
   tab row. When the worktree tabs are collapsed the wrapper turns
   into a single flex row so the two rows share a line (worktree on
   the left, branch on the right) and save ~30px of vertical space.
   In the expanded state the wrapper is purely structural — the
   inner rows still own their own padding and border, so this is a
   pure layout pass with no visual regression. */

/* The wrapper itself is borderless and paddingless in the expanded
   (default) state; the inner worktree row keeps its `border-bottom`
   to separate the header strip from the main sidebar content. */
.git-diff-sidebar-bw {
  /* no display: block here — the inner rows are already block. */
}

/* 2026-07-22 (rev): expanded mode = the wrapper is NOT
   `.is-collapsed`. In that mode the inner branch row and worktree
   row are independent block elements with their own
   `padding-left: 14px` defaults. Nudge that to 16px so each row's
   first content (the "当前分支" / "工作树" labels) picks up an
   extra 2px of breathing room against the sidebar edge — the
   2px-inward counterpart of the collapsed-mode `margin-left/right`
   nudge. The selector is scoped to the wrapper's NOT-collapsed
   mode so the collapsed layout (which already gives the wrapper
   its own `padding: 6px 14px 8px` and zeroes the inner rows'
   padding) stays byte-for-byte identical. */
.git-diff-sidebar-bw:not(.is-collapsed) > .git-diff-sidebar-branch-mgmt {
  padding-left: 16px;
}
.git-diff-sidebar-bw:not(.is-collapsed) > .git-diff-sidebar-tabs {
  padding-left: 16px;
}

/* Collapsed: turn the wrapper into a single flex row.
   `justify-content: space-between` parks the worktree row flush
   LEFT and the branch row flush RIGHT, leaving an empty gap in
   the middle — the user-stated "工作树靠左, 分支靠右, 中间
   离远一些，对称" layout. `flex: 0 0 auto` on both children so
   each row only takes its natural width. `flex-wrap: wrap` is the
   belt-and-braces guard for the very narrow sidebar case where
   the two rows can't fit on one line.
   2026-07-22 (rev): the visual left/right is the INVERSE of the
   DOM order — the DOM keeps branch-then-worktree so the tab
   order / screen-reader order stays the same as the expanded
   layout, but the wrapper sets explicit `order` values to make
   worktree render on the left and branch on the right. */
.git-diff-sidebar-bw.is-collapsed {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
  padding: 6px 14px 8px;
  border-bottom: 1px solid
    var(--chat-border, rgba(var(--v-theme-on-surface), 0.08));
}
/* In the collapsed state the inner rows shed their own padding and
   border so the wrapper owns them — otherwise the combined row
   would carry an internal border line in the middle (the worktree
   row's `border-bottom`) and look broken. */
.git-diff-sidebar-bw.is-collapsed > .git-diff-sidebar-tabs,
.git-diff-sidebar-bw.is-collapsed > .git-diff-sidebar-branch-mgmt {
  padding: 0;
  border-bottom: 0;
  /* Natural-size rows so the wrapper's `space-between` actually
     creates an empty middle gap instead of having one row stretch
     to fill it. */
  flex: 0 0 auto;
}
/* Visual left/right is opposite the DOM order. `order` is a
   layout-only property: it does NOT change tab order, screen
   reader reading order, or selection — the focusable elements
   inside still follow DOM order, so keyboard users and AT users
   get the same logical order as the expanded state. Only the
   visual placement changes.
   2026-07-22 (rev): pull the two rows 2px closer to the center
   so "工作树" and "当前分支" are not flush against the wrapper's
   `space-between` extremes. The worktree row's leading label
   ("工作树") is at the row's left edge, so `margin-left: 2px`
   on the worktree row pushes the entire content 2px toward the
   center; the branch row's leading label ("当前分支") is at the
   row's left edge, so `margin-right: 2px` on the branch row
   shrinks the row's right padding under `space-between`, which
   pulls the row 2px left. Net effect: the empty middle gap is
   ~4px narrower, the labels themselves move 2px each toward
   center. Kept as a small visual nudge (`2px`) so the change is
   imperceptible in absolute terms but visibly tighter. */
.git-diff-sidebar-bw.is-collapsed > .git-diff-sidebar-tabs {
  order: 1; /* visually left */
  margin-left: 2px; /* nudge content 2px right */
}
.git-diff-sidebar-bw.is-collapsed > .git-diff-sidebar-bw-refresh {
  order: 2; /* visually middle (between worktree=1 and branch=3) */
  /* In collapsed mode the wrapper owns the row's padding
     (6px 14px 8px), so the button's own margin-right: 4px from
     the base rule would over-pad. Zero it out to keep the
     centered look tight. */
  margin-right: 0;
}
.git-diff-sidebar-bw.is-collapsed > .git-diff-sidebar-branch-mgmt {
  order: 3; /* visually right (was 2; bumped +1 for refresh button at order: 2) */
  margin-right: 2px; /* nudge content 2px left */
}

/* ── Branch/worktree row manual refresh button ──────────────────────
   Spec 2026-07-23 §4.3. A 24x24 ghost button that mirrors the
   visual weight of the existing worktree-tab "add" button
   (.git-diff-sidebar-tab-add) so the two feel like siblings in
   the row. Two DOM nodes share this class (one inside the
   branch row, one as a wrapper-level child); the collapsed/
   expanded position switch is driven by `order` in the parent
   `.is-collapsed` rules above, NOT by this base rule. */

/* Base shape + interaction states. Rectangular chip with text +
   trailing refresh icon (rev 2026-07-23: previously icon-only
   24×24 square; widened to a labeled button per user request so
   the refresh action is discoverable without hover). The text
   span inherits font/color from the button; `white-space: nowrap`
   keeps the label on one line even on narrow sidebars. */
.git-diff-sidebar-bw-refresh {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 24px;
  padding: 0 10px;
  margin: 0 4px 0 0; /* breathing room from preceding element */
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.02em;
  white-space: nowrap;
  user-select: none;
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}
.git-diff-sidebar-bw-refresh:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.08);
  color: rgba(var(--v-theme-on-surface), 0.9);
}
.git-diff-sidebar-bw-refresh:disabled {
  cursor: default;
  opacity: 0.5;
}
/* Click ack (spec 2026-07-23 rev): brief primary-tint flash so the
   user sees the click registered even on instant/cached refreshes
   where the loading spinner never visibly appears. Toggled for
   350ms via the `is-flashing` class from `flashBwRefreshButton()`
   inside `refreshBranchesAndWorktrees`. `transition` is on the
   base rule above so the background fades smoothly back to
   transparent when the class is removed. The tint overrides
   `:hover`/`:active` for those 350ms — that's intentional: the
   flash IS the post-click feedback, distinct from hover/press
   pre-click states. */
.git-diff-sidebar-bw-refresh.is-flashing {
  background: rgba(var(--v-theme-primary), 0.22);
  color: rgba(var(--v-theme-primary), 1);
}
/* Press feel: subtle scale-down on mouse hold so the click
   visually registers as soon as mousedown — pairs with the
   post-click flash above which confirms the click was accepted.
   Uses `:not(:disabled)` to keep the static `opacity: 0.5` on
   `disabled` buttons (a scale-down on a faded button looks
   broken). */
.git-diff-sidebar-bw-refresh:active:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.14);
  transform: scale(0.97);
}
.git-diff-sidebar-bw-refresh:focus-visible {
  outline: 2px solid rgba(var(--v-theme-primary), 0.5);
  outline-offset: 1px;
}

/* Expanded state: refresh button is flush-right inside the branch
   row. The branch row (`.git-diff-sidebar-branch-mgmt`) is already
   `display: flex; align-items: center` so `margin-left: auto` on
   this button eats all remaining horizontal space, pushing it to
   the row's inner right edge. Resulting layout reads as
   "[当前分支] [main ▾] ↑3↓2 __________ [刷新分支和工作树]"
   with the empty middle band visually anchoring both ends.

   This selector targets only the expanded-state button — it sits
   inside `.git-diff-sidebar-branch-mgmt`, while the collapsed-state
   button is a direct child of `.git-diff-sidebar-bw` (positioned
   by `order: 2` in the parent, NOT by this rule).

   The previous incarnation of this rule used
   `.git-diff-sidebar-bw:not(.is-collapsed) > .git-diff-sidebar-bw-refresh`,
   but that selector never matched anything in the current DOM
   (expanded button is a grandchild of `.git-diff-sidebar-bw`,
   collapsed button is excluded by `:not(.is-collapsed)`) — it
   was dead code that visually "worked" only because the
   parent row's `gap: 6px` already gave the spacing. The new
   selector and `margin-left: auto` replace both the dead rule
   and the implicit gap-based spacing. */
.git-diff-sidebar-branch-mgmt > .git-diff-sidebar-bw-refresh {
  margin-left: auto;
}

/* ── Remote-sync buttons (pull / push / remote URL) ─────────────
   2026-08-12 git-pull-push-remote. A tight cluster of 22×22
   icon-only ghost buttons sitting between the ahead/behind badge
   and the refresh chip. Same palette + interaction states as
   .git-diff-sidebar-bw-refresh so the row reads as one family. */
.git-diff-sidebar-sync {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin: 0 2px 0 4px;
}
.git-diff-sidebar-sync-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.6);
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}
.git-diff-sidebar-sync-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.08);
  color: rgba(var(--v-theme-on-surface), 0.9);
}
.git-diff-sidebar-sync-btn:disabled {
  cursor: default;
  opacity: 0.5;
}

/* ── Worktree tabs (spec 2026-06-18 §3.4) ──────────────────── */

.git-diff-sidebar-tabs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 6px 14px 8px;
  border-bottom: 1px solid
    var(--chat-border, rgba(var(--v-theme-on-surface), 0.08));
}

/* Section label: small muted text that anchors the tab row to a
   clear purpose ("these pills switch worktrees"). `flex-shrink: 0`
   keeps it from being squeezed when the tabs wrap on narrow
   widths; when the row wraps, the label stays on its own line. */
.git-diff-sidebar-tabs-label {
  flex-shrink: 0;
  margin-right: 4px;
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: rgba(var(--v-theme-on-surface), 0.6);
  user-select: none;
}
/* 2026-07-22 worktree-tabs-collapse: the label is a button now so the
   user can expand / collapse the worktree tab row. Inherits the same
   muted typography as the old static label so the only visible delta
   when collapsed is the chevron direction. Hover nudges the
   foreground color up so it reads as interactive.
   2026-07-22 align: horizontal padding is 0 so the leading character
   of "工作树" sits at the same x as "分支" (the branch label is a
   plain span with no left padding, so any button padding would
   visually push the worktree label right of the branch label). The
   right-side `margin-right: 4px` still gives breathing room before
   the first tab; the hover background is widened by the
   `border-radius: 4px` so the click target still feels generous. */
.git-diff-sidebar-tabs-toggle {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
  margin-right: 4px;
  padding: 2px 0;
  border: none;
  background: transparent;
  border-radius: 4px;
  font-family: inherit;
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: rgba(var(--v-theme-on-surface), 0.6);
  cursor: pointer;
  transition:
    color 0.14s ease,
    background 0.14s ease;
}
.git-diff-sidebar-tabs-toggle:hover {
  color: rgba(var(--v-theme-on-surface), 0.85);
  background: rgba(var(--v-theme-on-surface), 0.04);
}
.git-diff-sidebar-tabs-toggle-chevron {
  color: inherit;
  flex-shrink: 0;
}

/* Pill shape, fully aligned with chat's chat-session-active-bg hover.
   Inactive state always shows a 1px hairline so the tab reads as a
   clickable button (a fully transparent outline looks like plain
   text). The border uses the chat-border token — it darkens slightly
   on hover and is replaced by a primary-tinted border on the active
   state. Switching the alpha (rather than toggling border on/off) keeps
   the layout perfectly stable across states. */
.git-diff-sidebar-tab {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--chat-border, rgba(var(--v-theme-on-surface), 0.16));
  background: transparent;
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.62));
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition:
    background 0.14s ease,
    color 0.14s ease,
    border-color 0.14s ease;
  max-width: 180px;
}

.git-diff-sidebar-tab:hover {
  background: var(
    --chat-session-active-bg,
    rgba(var(--v-theme-on-surface), 0.06)
  );
  border-color: rgba(var(--v-theme-on-surface), 0.26);
  color: rgb(var(--v-theme-on-surface));
}

.git-diff-sidebar-tab--active {
  background: rgba(var(--v-theme-primary), 0.12);
  border-color: rgba(var(--v-theme-primary), 0.45);
  color: rgb(var(--v-theme-primary));
  font-weight: 500;
}

.git-diff-sidebar-tab-icon {
  color: inherit;
  flex-shrink: 0;
}

.git-diff-sidebar-tab-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Add button now matches the regular pill geometry (same 999px radius
   and same height), removing the dashed-circle orphan style. */
.git-diff-sidebar-tab-add {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 999px;
  border: 1px solid var(--chat-border, rgba(var(--v-theme-on-surface), 0.18));
  background: transparent;
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.62));
  cursor: pointer;
  flex-shrink: 0;
  transition:
    background 0.14s ease,
    color 0.14s ease,
    border-color 0.14s ease;
}
.git-diff-sidebar-tab-add:hover {
  background: var(
    --chat-session-active-bg,
    rgba(var(--v-theme-on-surface), 0.06)
  );
  border-color: rgb(var(--v-theme-primary));
}

/* Branch switcher (spec 2026-07-21 §3.3, layout revised 2026-07-21)
   Lives ABOVE the worktree-tabs flex row. The dropdown list scrolls
   internally instead of growing past the viewport when a project has
   many branches. Font-size matches the worktree tabs (12px) so a long
   branch name doesn't visually dominate the sidebar. */
.git-diff-sidebar-branch-mgmt {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 14px;
  font-size: 12px;
}
.git-diff-sidebar-branch-mgmt-label {
  flex-shrink: 0;
  white-space: nowrap; /* the label is "当前分支" (4 zh chars) —
                            keep it on one line even on narrow sidebars,
                            so it can never wrap next to the branch
                            button and break the row's vertical rhythm */
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 11px;
  letter-spacing: 0.02em;
}
.git-diff-sidebar-branch-mgmt-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity, 1));
  border-radius: 6px;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font-size: 12px;
  line-height: 1.2;
  cursor: pointer;
  min-width: 0;
  flex: 1;
  max-width: 220px;
  white-space: nowrap;
  transition:
    background 0.15s,
    border-color 0.15s;
}
.git-diff-sidebar-branch-mgmt-btn:hover {
  background: var(
    --chat-session-active-bg,
    rgba(var(--v-theme-on-surface), 0.06)
  );
  border-color: rgb(var(--v-theme-primary));
}
.git-diff-sidebar-branch-mgmt-btn-name {
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}
/* Scrollable branch list. max-height caps the dropdown to a viewport-
   friendly size; overflow-y gives the user a scrollbar instead of the
   menu growing past the screen edge. font-size mirrors the worktree
   tabs to keep visual weight consistent. */
.git-diff-sidebar-branch-menu {
  max-height: 360px;
  overflow-y: auto;
  font-size: 12px;
}
.git-diff-sidebar-branch-menu :deep(.v-list-item-title) {
  font-size: 12px;
}
.git-diff-sidebar-branch-menu :deep(.v-list-item__content) {
  padding-inline: 8px;
}
.git-diff-sidebar-branch-menu .git-diff-sidebar-branch-merge {
  opacity: 0.6;
  margin-right: 4px;
}
.git-diff-sidebar-branch-menu .git-diff-sidebar-branch-merge:hover {
  opacity: 1;
  color: rgb(var(--v-theme-primary));
}
.git-diff-sidebar-branch-menu .git-diff-sidebar-branch-delete {
  opacity: 0.6;
}
.git-diff-sidebar-branch-menu .git-diff-sidebar-branch-delete:hover {
  opacity: 1;
  color: rgb(var(--v-theme-error));
}
.git-diff-sidebar-branch-create {
  padding: 8px 12px 6px 12px;
}
.git-diff-sidebar-branch-create-actions {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
  margin-top: 4px;
}
.git-diff-sidebar-tab-badge {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 6px;
  background: rgba(var(--v-theme-on-surface), 0.08);
  color: rgba(var(--v-theme-on-surface), 0.6);
  flex-shrink: 0;
}
.git-diff-sidebar-tab--prunable {
  border-color: rgba(255, 152, 0, 0.4);
  background: rgba(255, 152, 0, 0.08);
}
.git-diff-sidebar-tab-icon--prunable {
  color: rgb(255, 152, 0);
}
.git-diff-sidebar-tab-badge--prunable {
  background: rgba(255, 152, 0, 0.15);
  color: rgb(255, 152, 0);
}
/* Ahead/behind indicator: mirrors the worktree tab badge shape so
   the branch row visually matches the worktree row. Ahead is a soft
   green (you have unpushed commits). Behind is a soft red
   (you should pull — same Vuetify `error` red used elsewhere so the
   hue is consistent across the sidebar, e.g. the trash icon in the
   worktree context menu). Both colors are tones (alpha background
   + saturated foreground) rather than full-saturation, so they
   read as a state hint, not a hard alert.
   2026-07-22: bumped font-size 10 → 13 (and tweaked padding) so
   the count is legible at a glance — at the original 10px the
   badge quietly disappeared next to the 14–16px branch name and
   drop-arrow button. The container, not the badge, holds the
   font-size so the badge layout inherits cleanly.
   2026-07-22 (rev): visual hierarchy rewritten. The badge is now a
   2-cell inline-flex: an MDI arrow icon (10px) on the left and a
   bold count (13px) on the right. The count dominates the badge —
   it's the information the user actually wants (how many commits
   ahead / behind). The arrow is a subordinate directional hint.
   The MDI glyph (`mdi-arrow-up-thick` / `-down-thick`) is a SOLID
   TRIANGLE with no vertical stem, so it cannot be confused with
   the digit "1" the way the unicode "↑" / "↓" could — those share
   the same "vertical-line + small top mark" silhouette as "1" in
   most sans-serif fonts, which is what the user reported. */
.git-diff-sidebar-branch-mgmt-tracking {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px; /* 2026-07-22: 13 → 12 to match the row's
                          sibling 12px (branch button text), so the
                          badge sits at the row's text rhythm
                          instead of one step taller */
  font-weight: 500;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}
.git-diff-sidebar-branch-mgmt-tracking-ahead,
.git-diff-sidebar-branch-mgmt-tracking-behind {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 7px;
  border-radius: 6px;
}
.git-diff-sidebar-branch-mgmt-tracking-ahead {
  background: rgba(76, 175, 80, 0.15); /* Vuetify success tint */
  color: rgb(76, 175, 80); /* "↑ ahead — you have unpushed commits" */
}
.git-diff-sidebar-branch-mgmt-tracking-behind {
  background: rgba(244, 67, 54, 0.15); /* Vuetify error tint */
  color: rgb(244, 67, 54); /* "↓ behind — you should pull" */
}
/* The v-icon's `size="10"` prop sets the glyph's font-size to 10px.
   This class just makes the icon flex-stable (won't shrink in a
   tight sidebar) and locks its line-height to 1 so the count's
   visual center aligns with the icon's visual center inside the
   inline-flex badge. */
.git-diff-sidebar-branch-mgmt-tracking-arrow {
  flex-shrink: 0;
  line-height: 1;
}
/* The count is the dominant information: bold 13px, inheriting
   `font-variant-numeric: tabular-nums` from the container so the
   badge width stays identical whether the count is 1 digit ("5")
   or 2 digits ("16"). */
.git-diff-sidebar-branch-mgmt-tracking-count {
  font-weight: 700;
  line-height: 1;
}

@media (max-width: 760px) {
  .git-diff-sidebar-tab {
    font-size: 11px;
    padding: 3px 8px;
    max-width: 140px;
  }
  .git-diff-sidebar-tab-label {
    max-width: 90px;
  }
  /* Scope pills collapse to icons-only on narrow screens to keep the
     three pills balanced. Text is hidden because the icon + the
     pill's active color already communicate the choice. */
  .git-diff-sidebar-scope-pill {
    padding: 4px;
  }
  .git-diff-sidebar-scope-pill-text {
    display: none;
  }
}

/* ── Truncation warning ──────────────────────────────────────── */

.git-diff-sidebar-warning {
  padding: 8px 14px;
  background: rgba(255, 193, 7, 0.12);
  color: rgb(255, 152, 0);
  font-size: 12px;
  border-bottom: 1px solid rgba(255, 193, 7, 0.3);
}

/* ── Body ─────────────────────────────────────────────────────── */

.git-diff-sidebar-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 14px 12px;
}

/* ── Snackbar stderr block (spec §6.8) ─────────────────────────── */
.spcode-snackbar-stderr {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
/* 2026-09-09 toast-close: 手动关闭按钮。flex 行把关闭按钮钉在右上,
   文字区域(min-width: 0)负责换行/滚动,不会把按钮挤出去。 */
.spcode-snackbar-body {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
}
.spcode-snackbar-body__text {
  flex: 1 1 auto;
  min-width: 0;
}
.spcode-snackbar-close {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  margin: -1px -4px 0 0;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: inherit;
  opacity: 0.72;
  cursor: pointer;
  transition: opacity 0.15s ease;
}
.spcode-snackbar-close:hover {
  opacity: 1;
}
.spcode-snackbar-close:focus-visible {
  opacity: 1;
  outline: 2px solid currentColor;
  outline-offset: 2px;
}
.spcode-snackbar-message {
  font-weight: 500;
  margin-bottom: 0;
}
.spcode-snackbar-pre {
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: inherit;
  padding: 6px 8px;
  border-radius: 4px;
  font-size: 11px;
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  margin: 0;
}

/* ── Dark mode refinements ──────────────────────────────────── */
/* Match the `.chat-ui.is-dark` token overrides defined in Chat.vue so
   the sidebar visually harmonizes with the rest of chat in dark mode:
   - hairline resizer is half-opacity (less aggressive on dark bg)
   - segmented control sits on the dark "active" surface
   - warning banner drops the harsh yellow border */
.chat-ui.is-dark .git-diff-sidebar-warning {
  background: rgba(255, 152, 0, 0.08);
  color: rgb(255, 167, 38);
  border-bottom-color: rgba(255, 152, 0, 0.25);
}
.chat-ui.is-dark .spcode-snackbar-pre {
  background: rgba(255, 255, 255, 0.06);
}

/* ── Mobile ───────────────────────────────────────────────────── */

@media (max-width: 760px) {
  .git-diff-sidebar {
    position: fixed;
    inset: 0;
    z-index: 1300;
    width: 100vw !important;
    height: 100dvh;
    margin-top: 0;
    border-left: 0;
  }
  .git-diff-sidebar-resizer {
    display: none;
  }
  .git-diff-sidebar-header {
    min-height: 52px;
    padding: calc(10px + env(safe-area-inset-top)) 12px 8px;
    border-bottom: 1px solid rgba(var(--v-border-color), 0.12);
  }
  .git-diff-sidebar-body {
    padding: 0 12px calc(12px + env(safe-area-inset-bottom));
  }
}

/* Context menu wrapper teleported to <body>. The wrapper itself is
   hidden visually; only the inner v-list renders. We give it a small
   minimum width so the menu items have room for icon + text. Scoped
   styles work through Teleport because Vue rewrites the data-v hash
   selector on both sides of the portal. Two-layer elevation (a tight
   ambient blur + a wider drop) mirrors Vuetify elevation-12 and feels
   consistent with chat's menus & snackbars. */
.worktree-context-menu {
  min-width: 180px;
  max-width: 280px;
  border-radius: 10px;
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid var(--chat-border, rgba(var(--v-theme-on-surface), 0.08));
  box-shadow:
    0 1px 2px rgba(var(--v-theme-on-surface), 0.06),
    0 8px 24px rgba(0, 0, 0, 0.18);
  overflow: hidden;
}

/* Spec 2026-07-16: dismissed "not a Git project" chip. Floats above
   the body so the Files view (which does not need Git) remains
   usable. Matches the path-strip and view-tab strip's neutral
   surface treatment. */
.git-diff-sidebar-repo-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: rgba(var(--v-theme-on-surface), 0.04);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  font-size: 12px;
}
.git-diff-sidebar-repo-chip-action {
  margin-left: auto;
  background: none;
  border: none;
  color: rgb(var(--v-theme-primary));
  cursor: pointer;
  font: inherit;
  padding: 0;
}
</style>
