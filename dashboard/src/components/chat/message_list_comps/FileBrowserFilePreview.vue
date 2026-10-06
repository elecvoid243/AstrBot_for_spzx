<!-- Author: elecvoid243, 2026-06-20
     Spec: docs/superpowers/specs/2026-06-20-git-diff-sidebar-file-browser-design.md §4.5 -->
<script setup lang="ts">
import {
  computed,
  inject,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { useModuleI18n } from "@/i18n/composables";
import {
  detectLanguage,
  ensureShikiLanguages,
  renderShikiCode,
  escapeHtml,
} from "@/utils/shiki";
import { copyToClipboard } from "@/utils/clipboard";
import type { FileBrowserFetchState } from "@/composables/useSpcodeFileBrowser";
import { useDisplay } from "vuetify";
import {
  extractLineContext,
  useFileComments,
  type LineContext,
} from "@/composables/useFileComments";
import FileBrowserCodeView from "./FileBrowserCodeView.vue";
import BinaryPreview from "./binary_preview/BinaryPreview.vue";
import FileCommentEditor from "./FileCommentEditor.vue";
import InlineAskEditor from "./InlineAskEditor.vue";
import SelectionActionMenu from "./SelectionActionMenu.vue";
import DiffPreview from "./DiffPreview.vue";
import type { GitFileDiffData } from "@/composables/parseSpcodeGitFileDiff";
import CodeMirrorEditor from "./CodeMirrorEditor.vue";
import CodeCheckFormatBar from "./CodeCheckFormatBar.vue";
import MarkdownView from "@/components/shared/MarkdownView.vue";
import { useSpcodeFileWrite } from "@/composables/useSpcodeFileWrite";
import { useSpcodeFileRename } from "@/composables/useSpcodeFileRename";
import { useSpcodeFileRemove } from "@/composables/useSpcodeFileRemove";
import { useInlineAnnotations } from "@/composables/useInlineAnnotations";
import { useSpcodeBtw } from "@/composables/useSpcodeBtw";
import { useOpenOnDisk } from "@/composables/useOpenOnDisk";

/** Mirrors DocumentManager's viewMode union, trimmed to what the
 *  workspace pane actually renders: 'raw' (file content) and 'diff'
 *  (unified diff). The 'rendered' branch (markdown rendering) is
 *  intentionally absent — the workspace tab is a code viewer, not
 *  a document reader, so there is no markdown path to toggle. */
type HistoryViewMode = "raw" | "diff";

const props = defineProps<{
  state: FileBrowserFetchState;
  isDark: boolean;
  /**
   * 2026-07-15 workspace-history-inline: when non-null, the parent
   * (<FileBrowserView>) has picked a commit SHA from the history
   * pane; the preview body switches to either the historical blob
   * (viewMode === 'raw') or the unified diff vs HEAD ('diff')
   * instead of the current working-copy content. Matches
   * <DocumentManager>'s selectedRevision contract; null = no
   * revision picked = current file (legacy path).
   */
  selectedRevision?: string | null;
  /** 2026-07-15 workspace-history-inline: only meaningful when
   *  `selectedRevision` is non-null. Defaults to 'raw' so a missed
   *  prop falls back to "show file content", not to diff mode. */
  viewMode?: HistoryViewMode;
  /** Historical blob content for (current preview file, picked
   *  revision). The parent feeds in the live value from
   *  `useSpcodeGitFile.getData(path, rev)`; an empty string means
   *  "fetch not done yet" / "fetch failed" / "no revision picked",
   *  in which case the preview falls back to the loading / empty
   *  placeholder. */
  historicalContent?: string;
  /** True when the (path, rev) blob came back is_binary=true from
   *  the backend. Renders a "binary file" placeholder instead of an
   *  empty highlight pass. Default false. */
  historicalIsBinary?: boolean;
  /** Unified diff text from git-show?path= for the (current file,
   *  picked revision) pair. Null when not in 'diff' mode, or when
   *  the lazy fetch is still in flight, or when the file was
   *  binary / not-in-revision (in which case diffIsBinary is true). */
  diffPatch?: string | null;
  /** True when the diff backend returned isBinary=true for the
   *  current (file, revision) pair; renders a "binary file"
   *  placeholder in place of the (also null) patch. */
  diffIsBinary?: boolean;
  /**
   * 2026-07-02 sidebar-search: 1-based line number to center in the
   * code view after a search-result click. null = no scroll.
   * Forwarded to <FileBrowserCodeView>, where the scrollIntoView()
   * watcher lives.
   */
  scrollToLine?: number | null;
  /**
   * 2026-07-17 workspace file editor: repo-relative path of the
   * previewed file (the same value FileBrowserView feeds to the
   * history panel as `file-relative`). The file-write endpoint
   * requires a repo-relative path, so the edit affordance is hidden
   * when this is empty.
   */
  fileRelativePath?: string;
  /** Current worktree root, forwarded to the file-write endpoint. */
  worktree?: string | null;
  /**
   * 2026-10-06 range-compare: 任意两版本比较数据(git-file-diff)。
   * 非空时优先于 diffPatch 走「全文件 + diff 叠加」渲染;
   * status=added → 纯 patch 全绿;unchanged → 纯文件视图。
   */
  rangeDiff?: GitFileDiffData | null;
}>();
const emit = defineEmits<{
  (e: "navigate-target", resolvedPath: string): void;
  (e: "retry"): void;
  /**
   * 2026-07-15 workspace-history-banner: fired from the
   * "正在查看历史版本" banner's "回到当前" button. The parent
   * (FileBrowserView) owns the canonical `selectedRevision`
   * state, so we just bubble the click up — same shape as
   * DocumentManager's `onBackToCurrent`, which sets
   * `selectedRevision = null` directly. Mirrors
   * <DocumentManager>'s banner UX so the workspace tab and
   * the document-manager tab read identically when a revision
   * is picked.
   */
  (e: "back-to-current"): void;
  /**
   * 2026-07-17 workspace file editor: fired after a successful save
   * so the parent re-fetches the working-copy content (same handler
   * as `retry`).
   */
  (e: "saved"): void;
  /**
   * 2026-07-18 editor toolbar parity: fired after a successful rename
   * (with the new repo-relative path) so the parent refreshes the file
   * list and switches the preview to the renamed file; `deleted` is
   * fired after a successful delete so the parent closes the preview.
   */
  (e: "renamed", newPath: string): void;
  (e: "deleted"): void;
}>();
const { tm } = useModuleI18n("features/chat");

// 2026-07-15 workspace-history-parity: the file-level history
// affordance used to live as an "mdi-history" button in this meta
// header (2026-07-09). It was removed so the workspace tab matches
// the document-manager tab, where history is a per-file SIDE PANEL
// rather than a button that switches the entire sidebar to the
// History tab. The "spcode:setLogPathFilter" inject key is still
// provided by GitDiffSidebar; FileBrowserView (the sibling that owns
// the new right-edge history pane) consumes it directly. Keeping
// the noop default here makes this component reusable in isolation
// (storybook / unit tests) without asserting on the inject.

// 2026-07-17: the inner-fullscreen bridge (spcode:globalFullscreen /
// innerFullscreen / isAnyFullscreen / toggleInnerFullscreen injects)
// was removed — the preview-only fullscreen duplicated GitDiffSidebar's
// sidebar-wide fullscreen button. The preview no longer teleports to
// <body> and renders no fullscreen control of its own.

/**
 * Resolve a symlink target string (which may be relative) against the
 * symlink's parent directory. Mirrors POSIX symlink semantics:
 * - Absolute target: use as-is
 * - Relative target: join with parent dir of the symlink
 *
 * The backend does NOT resolve symlinks (per file-browser spec §3.5.4);
 * if the user wants to view the resolved content, we manually re-issue
 * the request with the resolved path so the backend re-classifies it.
 */
function resolveTargetPath(symlinkPath: string, target: string): string {
  const isWindows = symlinkPath.includes("\\");
  const sep = isWindows ? "\\" : "/";
  if (target.startsWith("/") || /^[a-zA-Z]:[\\/]/.test(target)) {
    return target; // Absolute path
  }
  const lastSep = Math.max(
    symlinkPath.lastIndexOf("/"),
    symlinkPath.lastIndexOf("\\"),
  );
  const parentDir = lastSep >= 0 ? symlinkPath.slice(0, lastSep) : symlinkPath;
  return parentDir + sep + target;
}

// 2026-07-17: the extension→language map moved to @/utils/shiki
// (exported as `detectLanguage`) so this preview and the file editor
// (CodeMirrorEditor, via @/utils/codemirrorLanguages) each resolve
// languages from the same extension set.

const shikiHighlighter = ref<any>(null);
const shikiReady = ref(false);

onMounted(async () => {
  // Pattern mirrored verbatim from ToolResultView.vue:289 — do NOT
  // pass an array arg to ensureShikiLanguages (it takes zero args
  // and silently ignores any). Assign the returned highlighter so
  // the !shikiHighlighter.value guard in highlightedHtml is satisfied.
  try {
    shikiHighlighter.value = await ensureShikiLanguages();
    shikiReady.value = true;
  } catch (err) {
    console.error("Shiki init failed:", err);
  }
});

const highlightedHtml = computed(() => {
  if (props.state.kind !== "file") return "";
  const snapshot = props.state.snapshot;
  if (snapshot.content === null) return "";
  if (!shikiReady.value || !shikiHighlighter.value) {
    return `<pre><code>${escapeHtml(snapshot.content)}</code></pre>`;
  }
  try {
    // renderShikiCode signature: (highlighter, code, language, colorMode)
    // colorMode="auto" enables dual-theme (light/dark) auto-switching.
    return renderShikiCode(
      shikiHighlighter.value,
      snapshot.content,
      detectLanguage(snapshot.meta.path),
      "auto",
    );
  } catch (err) {
    console.error("Shiki render failed:", err);
    return `<pre><code>${escapeHtml(snapshot.content)}</code></pre>`;
  }
});

/** Shiki-highlighted historical blob content (2026-07-15
 *  workspace-history-inline). Same highlighter pipeline as the
 *  current-file path above, but sourced from
 *  `historicalContent` (the (path, rev) blob from useSpcodeGitFile)
 *  and the language derived from the current file's meta path.
 *  The path is the same file at a different revision, so the
 *  extension-based language detection is still correct. Returns ""
 *  when there's no revision picked (the parent passes "" for that
 *  case), so callers can v-if on the returned string without
 *  having to know which view is active. */
const highlightedHistoricalHtml = computed<string>(() => {
  if (!props.selectedRevision) return "";
  const content = props.historicalContent ?? "";
  if (!content) return "";
  if (props.state.kind !== "file") return "";
  if (!shikiReady.value || !shikiHighlighter.value) {
    return `<pre><code>${escapeHtml(content)}</code></pre>`;
  }
  try {
    return renderShikiCode(
      shikiHighlighter.value,
      content,
      detectLanguage(props.state.snapshot.meta.path),
      "auto",
    );
  } catch (err) {
    console.error("Shiki (historical) render failed:", err);
    return `<pre><code>${escapeHtml(content)}</code></pre>`;
  }
});

/** Convenience flag for the template: true when a revision is
 *  picked AND the view is "diff". Used to swap the body out for
 *  <DiffPreview> instead of <FileBrowserCodeView>. */
const isHistoricalDiff = computed<boolean>(
  () => !!props.selectedRevision && (props.viewMode ?? "raw") === "diff",
);
/** Convenience flag for the template: true when a revision is
 *  picked AND the view is "raw". Used to swap the body out for the
 *  historical-blob code view. Both flags are exclusive with the
 *  legacy "current file" path below — the parent's `previewPath`
 *  watcher clears selectedRevision on file change. */
const isHistoricalRaw = computed<boolean>(
  () => !!props.selectedRevision && (props.viewMode ?? "raw") === "raw",
);
/** Short SHA label (first 7 chars) for the meta-header badge. We
 *  use git's 7-char convention; longer SHAs still fit the chip
 *  without wrapping. Empty string when no revision is picked so
 *  the template can hide the badge with v-if. */
const shortRevisionLabel = computed<string>(() =>
  props.selectedRevision ? props.selectedRevision.slice(0, 7) : "",
);

const copyButtonText = ref<string>("");
// Vuetify `color` accepts the theme token name; success/error give the
// user a clear visual hint that distinguishes "已复制" (green) from
// "复制失败" (red), instead of two identical grey states.
const copyButtonColor = ref<"success" | "error" | undefined>(undefined);
let copyResetTimer: ReturnType<typeof setTimeout> | null = null;

watch([highlightedHtml, highlightedHistoricalHtml], () => {
  // New file loaded → reset the transient success/fail feedback so
  // a "复制失败" toast from the previous file does not leak into the
  // freshly rendered header. Color is reset here too — without that,
  // a failed copy on the previous file would tint the new file's
  // copy button red before the user even clicks it. The 2026-07-15
  // workspace-history-inline revision adds `highlightedHistoricalHtml`
  // to the source list so toggling into / out of historical-raw mode
  // also clears any "已复制" feedback left from the previous file.
  copyButtonText.value = tm("spcodeProjectLoad.fileBrowser.preview.copy");
  copyButtonColor.value = undefined;
});

async function copyContent(): Promise<void> {
  // 2026-07-15 workspace-history-inline: support copying in both
  // current-file mode AND historical-raw mode. The meta header's
  // Copy button is already gated by `v-if` to avoid firing in
  // other states; this guard keeps the helper safe to call from
  // elsewhere (e.g. a keyboard shortcut) without depending on the
  // v-if. Diff mode is intentionally excluded — the diff body
  // isn't our text and is owned by <DiffPreview>.
  const text = isHistoricalRaw.value
    ? props.historicalContent ?? ""
    : props.state.kind === "file"
    ? props.state.snapshot.content
    : null;
  if (text === null || text === undefined || text === "") return;
  // Cancel any in-flight reset timer before scheduling a new one so
  // rapid double-clicks do not race the old timer into flipping the
  // button back to the default state mid-feedback.
  if (copyResetTimer) {
    clearTimeout(copyResetTimer);
    copyResetTimer = null;
  }
  // Use the shared clipboard utility: it auto-selects between
  // navigator.clipboard.writeText (secure context) and a
  // <textarea>+execCommand("copy") fallback for HTTP / non-secure
  // deployments, and logs `[clipboard] ...` breadcrumbs so we can
  // diagnose future failures from the console alone.
  const ok = await copyToClipboard(text);
  if (ok) {
    copyButtonColor.value = "success";
    copyButtonText.value = tm(
      "spcodeProjectLoad.fileBrowser.preview.copySuccess",
    );
  } else {
    copyButtonColor.value = "error";
    copyButtonText.value = tm("spcodeProjectLoad.fileBrowser.preview.copyFail");
  }
  copyResetTimer = setTimeout(() => {
    copyButtonText.value = tm("spcodeProjectLoad.fileBrowser.preview.copy");
    copyButtonColor.value = undefined;
    copyResetTimer = null;
  }, 2000);
}

// 2026-08-14 open-on-disk: ask the AstrBot host to open the
// previewed file with the OS default application. The file-browser
// snapshot echoes the requested ABSOLUTE path back in meta.path, so
// no client-side path math is needed.
const { opening: openingOnDisk, openOnDisk } = useOpenOnDisk(
  "spcodeProjectLoad.fileBrowser.preview",
);

function openPreviewOnDisk(): void {
  if (props.state.kind !== "file") return;
  void openOnDisk(
    props.state.snapshot.meta.path,
    props.state.snapshot.meta.name,
  );
}

// Drop any pending reset timer on unmount. Without this the closure
// would hold a stale ref to the destroyed component's copyButtonText
// and write to it 2s after the user has navigated away, tripping
// Vue's "set operation on key ... failed" warning.
onBeforeUnmount(() => {
  if (copyResetTimer) {
    clearTimeout(copyResetTimer);
    copyResetTimer = null;
  }
  // 2026-07-26 save-success-toast: drop the dismiss timer so it
  // can't fire against destroyed refs after the component is
  // gone (otherwise we'd see a "set on unmounted" warning on
  // hot-reload or route change).
  if (saveSuccessTimer) {
    clearTimeout(saveSuccessTimer);
    saveSuccessTimer = null;
  }
  document.removeEventListener("keydown", onKeyDown, true);
  fileWrite.dispose();
  fileRename.dispose();
  fileRemove.dispose();
});

// 2026-07-26 keyboard shortcut: register the Ctrl/Cmd+S listener
// after mount and tear it down in onBeforeUnmount above. Mount-time
// registration keeps the listener scope tight to the open preview
// (it dies when the user navigates to a different file).
onMounted(() => document.addEventListener("keydown", onKeyDown, true));

// ── Markdown rendering (2026-07-17 workspace-md-render) ─────────
// .md/.markdown files can be viewed rendered (reuses the shared
// <MarkdownView> — the same component the document-manager tab
// uses for its 渲染 mode) or as the raw Shiki code view. The mode
// resets to "rendered" on file change, mirroring DocumentManager's
// onTreeSelect which pins viewMode to rendered.
const mdViewMode = ref<"raw" | "rendered">("rendered");

const isMarkdownFile = computed<boolean>(() => {
  if (props.state.kind !== "file") return false;
  const name = props.state.snapshot.meta.name.toLowerCase();
  return name.endsWith(".md") || name.endsWith(".markdown");
});

// 2026-07-31 workspace-image-preview: image extensions rendered inline
// via <BinaryPreview>. Must stay in sync with the backend MIME_BY_EXT
// whitelist and useSpcodeFileBinary's IMAGE_EXTS.
const IMAGE_EXTS = [
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ".svg",
  ".bmp",
  ".ico",
];

/** True when the selected file is an image we can preview inline.
 *  Extension-based (matching the backend whitelist); both the current
 *  working copy and historical-raw views route through BinaryPreview,
 *  which re-fetches bytes via /spcode/file-binary with ETag caching. */
const isImageFile = computed<boolean>(() => {
  if (props.state.kind !== "file") return false;
  const name = props.state.snapshot.meta.name.toLowerCase();
  return IMAGE_EXTS.some((ext) => name.endsWith(ext));
});

/** True when the body should render markdown instead of the code
 *  view. Binary / too-large / loading are excluded by the template
 *  branches themselves (they win over the rendered branch). */
const mdRenderActive = computed<boolean>(
  () => isMarkdownFile.value && mdViewMode.value === "rendered",
);

/** Meta-header toggle visibility. Hidden in edit mode (the editor
 *  owns the body) and in historical-diff mode (a unified diff is
 *  not markdown-renderable). */
const canToggleMdView = computed<boolean>(
  () => isMarkdownFile.value && !editMode.value && !isHistoricalDiff.value,
);

watch(
  () => (props.state.kind === "file" ? props.state.snapshot.meta.path : null),
  () => {
    mdViewMode.value = "rendered";
  },
);

// ── 2026-07-17 workspace file editor ─────────────────────────────
// Edit mode mirrors DocumentManager's editMode/editBuffer pair, but
// saves through POST /spcode/file-write (POST /spcode/docs is
// markdown-only by design). The editor body is <CodeMirrorEditor>
// (CM6; lazy language packs, dark-aware theme).
const fileWrite = useSpcodeFileWrite(computed(() => props.worktree ?? null));
const editMode = ref(false);
// 2026-07-18 latency rework: the per-keystroke buffer lives INSIDE
// CodeMirrorEditor — this component only holds the session baseline +
// transition-level dirtiness, so keystrokes never re-render the whole
// preview pane.
const editInitialContent = ref("");
const editorRef = ref<InstanceType<typeof CodeMirrorEditor> | null>(null);
const isEditDirty = ref(false);
const saveError = ref<string | null>(null);
// 2026-07-26 save-success-toast: flipped to a localized "Saved"
// string after fileWrite.save resolves r.ok, then auto-cleared
// after SAVE_SUCCESS_TTL_MS so the inline green confirmation in
// the editor toolbar disappears on its own. Cleared on editMode
// exit and on file/revision switch so it never bleeds across
// targets.
const saveSuccessMessage = ref<string | null>(null);
let saveSuccessTimer: ReturnType<typeof setTimeout> | null = null;
/** Display duration for the save-success toast. Long enough to
 *  register at a glance, short enough to not block the toolbar. */
const SAVE_SUCCESS_TTL_MS = 2500;

/** The save endpoint rejects content > 2 MB (backend
 *  MAX_CONTENT_BYTES); hide the edit affordance for such files
 *  instead of failing on save. */
const MAX_EDIT_BYTES = 2 * 1024 * 1024;

const currentContent = computed<string>(() =>
  props.state.kind === "file" &&
  typeof props.state.snapshot.content === "string"
    ? props.state.snapshot.content
    : "",
);

function onEditDirtyChange(dirty: boolean): void {
  isEditDirty.value = dirty;
}

const canEdit = computed<boolean>(() => {
  if (props.state.kind !== "file") return false;
  // Historical revisions are read-only.
  if (props.selectedRevision) return false;
  const snap = props.state.snapshot;
  if (snap.meta.isBinary === true || typeof snap.content !== "string") {
    return false;
  }
  if (!props.fileRelativePath) return false;
  return new TextEncoder().encode(snap.content).length <= MAX_EDIT_BYTES;
});

/** Non-plain-UTF-8 files (GBK, UTF-8-BOM, ...) are re-encoded as
 *  UTF-8 on save (the backend write is fixed UTF-8), so surface that
 *  in the editor toolbar. */
const encodingNotice = computed<string | null>(() => {
  if (props.state.kind !== "file") return null;
  const enc = props.state.snapshot.meta.encoding;
  if (!enc) return null;
  const norm = enc.toLowerCase().replace("_", "-");
  if (norm === "utf-8" || norm === "utf8") return null;
  return tm("spcodeProjectLoad.fileBrowser.editor.encodingNotice", {
    encoding: enc,
  });
});

function onStartEdit(): void {
  // Freeze the loaded content as the editing session's baseline —
  // same "no mid-edit clobber" semantics as the old editBuffer init.
  editInitialContent.value = currentContent.value;
  isEditDirty.value = false;
  saveError.value = null;
  editMode.value = true;
}

async function onSaveEdit(): Promise<void> {
  if (!isEditDirty.value || fileWrite.isSaving.value) return;
  const savedContent = editorRef.value?.getValue() ?? "";
  const r = await fileWrite.save({
    path: props.fileRelativePath ?? "",
    content: savedContent,
  });
  if (r.ok) {
    // 2026-07-26 toolbar parity: stay in edit mode after save (matches
    // DocumentManager's onSave). The save button stays available for
    // further edits; cancel still exits the editor. Two flips are
    // needed for a clean post-save state:
    //   1. editor.setBaseline() — CodeMirrorEditor's internal lastDirty
    //      resets to clean, emits dirty-change false → the parent's
    //      isEditDirty (which gates the save button) drops to false.
    //   2. The next keystroke naturally re-transitions to dirty
    //      because props.modelValue is still the pre-save baseline.
    // emit("saved") bubbles up so the parent refresh() pulls the
    // post-save bytes back into currentContent for the read view
    // (matches DocumentManager's treeRef.refresh() / fileBrowser.refresh()
    // on save).
    editorRef.value?.setBaseline();
    saveError.value = null;
    emit("saved");
    // 2026-07-26 save-success-toast: surface a transient green
    // "Saved" confirmation in the editor toolbar. We cancel any
    // in-flight dismiss timer before scheduling a new one so
    // rapid back-to-back saves don't get their toast clipped
    // short by the previous schedule.
    saveSuccessMessage.value = tm(
      "spcodeProjectLoad.fileBrowser.editor.saveSuccess",
    );
    if (saveSuccessTimer) clearTimeout(saveSuccessTimer);
    saveSuccessTimer = setTimeout(() => {
      saveSuccessMessage.value = null;
      saveSuccessTimer = null;
    }, SAVE_SUCCESS_TTL_MS);
  } else {
    saveError.value = `${tm(
      "spcodeProjectLoad.fileBrowser.editor.saveError",
    )}: ${r.reason}`;
  }
}

// 2026-07-26 keyboard shortcut: Ctrl/Cmd+S triggers save when the
// inline editor is open. Same contract as DocumentEditor's onKeyDown:
// preventDefault to suppress the browser save dialog, stopPropagation
// to keep FileBrowserView's capture-phase handler from also consuming
// the chord. The isEditDirty / isSaving gate inside onSaveEdit makes
// repeated presses no-ops once the post-save baseline is pinned.
function onKeyDown(e: KeyboardEvent): void {
  if (!editMode.value) return;
  const isMod = e.metaKey || e.ctrlKey;
  if (isMod && (e.key === "s" || e.key === "S")) {
    e.preventDefault();
    e.stopPropagation();
    void onSaveEdit();
  }
}

function onCancelEdit(): void {
  if (isEditDirty.value) {
    const ok = window.confirm(
      tm("spcodeProjectLoad.fileBrowser.editor.discardConfirm"),
    );
    if (!ok) return;
  }
  editMode.value = false;
  saveError.value = null;
  // 2026-07-26 save-success-toast: drop the in-flight "Saved"
  // message and its dismiss timer when the user exits the editor
  // (cancel / switch) so the green confirmation does not linger
  // against an empty toolbar.
  saveSuccessMessage.value = null;
  if (saveSuccessTimer) {
    clearTimeout(saveSuccessTimer);
    saveSuccessTimer = null;
  }
}

// 2026-07-26 toolbar parity: copy the live editor buffer to the
// system clipboard — mirrors DocumentEditor.onCopyRaw. The preview's
// read-only copy button reads from the snapshot, so the editor copy
// has to read the up-to-date CodeMirror contents.
function onCopyEdit(): void {
  void copyToClipboard(editorRef.value?.getValue() ?? "");
}

// ── 2026-07-18 rename / delete (editor toolbar parity) ──────────
// Same-dir rename + hard delete backed by POST /spcode/file-rename
// and POST /spcode/file-remove (the docs endpoints are .md-only).
// Both actions operate on the on-disk file, so a dirty edit buffer
// would be lost — guard with the same confirm semantics as cancel.
const fileRename = useSpcodeFileRename(computed(() => props.worktree ?? null));
const fileRemove = useSpcodeFileRemove(computed(() => props.worktree ?? null));
const renameOpen = ref(false);
const renameName = ref("");
const renameError = ref<string | null>(null);
const deleteOpen = ref(false);
const deleteError = ref<string | null>(null);

const currentFileName = computed<string>(() => {
  const p = props.fileRelativePath ?? "";
  const idx = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
  return idx >= 0 ? p.slice(idx + 1) : p;
});

/** Dirty guard for destructive actions: returns true when the action
 *  may proceed (clean buffer, or the user confirmed losing edits). */
function confirmDiscardForAction(): boolean {
  if (!isEditDirty.value) return true;
  return window.confirm(
    tm("spcodeProjectLoad.fileBrowser.editor.unsavedActionConfirm"),
  );
}

function onRenameClick(): void {
  if (!confirmDiscardForAction()) return;
  renameName.value = currentFileName.value;
  renameError.value = null;
  renameOpen.value = true;
}

async function onConfirmRename(): Promise<void> {
  const newName = renameName.value.trim();
  if (!newName || newName === currentFileName.value) return;
  if (/[/\\\n\x00]/.test(newName)) {
    renameError.value = tm(
      "spcodeProjectLoad.fileBrowser.editor.renameFailed",
      { reason: "path_unsafe" },
    );
    return;
  }
  const r = await fileRename.rename({
    path: props.fileRelativePath ?? "",
    newName,
  });
  if (!r.ok) {
    renameError.value = tm(
      "spcodeProjectLoad.fileBrowser.editor.renameFailed",
      { reason: r.reason },
    );
    return;
  }
  // Compute the new repo-relative path (same dir, new basename) so the
  // parent can switch the preview over without a second lookup.
  const p = props.fileRelativePath ?? "";
  const idx = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
  const newRel = idx >= 0 ? `${p.slice(0, idx + 1)}${newName}` : newName;
  renameOpen.value = false;
  editMode.value = false;
  isEditDirty.value = false;
  emit("renamed", newRel);
}

function onDeleteClick(): void {
  if (!confirmDiscardForAction()) return;
  deleteError.value = null;
  deleteOpen.value = true;
}

async function onConfirmDelete(): Promise<void> {
  const r = await fileRemove.remove({ path: props.fileRelativePath ?? "" });
  if (!r.ok) {
    deleteError.value = tm(
      "spcodeProjectLoad.fileBrowser.editor.deleteFailed",
      { reason: r.reason },
    );
    return;
  }
  deleteOpen.value = false;
  editMode.value = false;
  isEditDirty.value = false;
  emit("deleted");
}

/** Dirty guard consulted by the parent (FileBrowserView) before any
 *  navigation that would swap the previewed file. Returns true when
 *  navigation may proceed (not editing / clean / user confirmed the
 *  discard); false when the user cancelled. */
function confirmLeaveEditing(): boolean {
  if (!editMode.value) return true;
  if (!isEditDirty.value) {
    editMode.value = false;
    return true;
  }
  const ok = window.confirm(
    tm("spcodeProjectLoad.fileBrowser.editor.discardConfirm"),
  );
  if (ok) editMode.value = false;
  return ok;
}
defineExpose({ confirmLeaveEditing });

// Safety nets: the parent's guards normally veto navigation while
// editing, but an external previewPath change (e.g. a sidebar-level
// search click) or a history pick can still swap the view. Exit edit
// mode in that case — the buffer is per-file and must never leak
// into another file / revision.
watch(currentFilePath, () => {
  if (editMode.value) editMode.value = false;
});
watch(
  () => props.selectedRevision,
  (rev) => {
    if (rev && editMode.value) editMode.value = false;
  },
);

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}
function formatMtime(mtime: number | null): string {
  if (!mtime) return "—";
  return new Date(mtime * 1000).toLocaleString();
}

// Map the composable's reason codes to localized messages.
const REASON_I18N_KEYS: Record<string, string> = {
  path_not_found: "spcodeProjectLoad.fileBrowser.error.pathNotFound",
  permission_denied: "spcodeProjectLoad.fileBrowser.error.permissionDenied",
  special_file: "spcodeProjectLoad.fileBrowser.error.specialFile",
};

function localizedReason(reason: string): string {
  const key = REASON_I18N_KEYS[reason];
  if (key) return tm(key);
  if (reason === "network") {
    return tm("spcodeProjectLoad.fileBrowser.error.network");
  }
  return tm("spcodeProjectLoad.fileBrowser.error.unknown", { reason });
}

// --- inline comments (Chunk 3) ---
// The file-comments store is a module-level singleton (see
// useFileComments.ts header for why provide/inject was abandoned).
// Every component that calls useFileComments() gets the same instance.
const fileComments = useFileComments();

const activeEditLine = ref<number | null>(null);
const activeEditCommentId = ref<string | null>(null);
const editorInitialText = ref<string>("");
const editorContext = ref<LineContext | null>(null);
/** 2026-07-17 selection-comment: when a comment was started from a
 *  drag-selection (not the gutter "+"), this holds the range. The
 *  editor receives it as `endLine`/`selectionContent` props and
 *  echoes it back in the save payload, so `onSaveComment` can route
 *  to `addSelectionComment` instead of `addComment`. */
const activeEditRange = ref<{
  startLine: number;
  endLine: number;
  selection: string;
} | null>(null);
/** 2026-07-17 selection-comment: copy-only action menu for the
 *  rendered-markdown container (no reliable source-line mapping in
 *  rendered HTML, so the menu hides the "comment" item). */
const renderedMenu = ref<{ x: number; y: number; text: string } | null>(null);
const snackbar = ref<{ visible: boolean; text: string }>({
  visible: false,
  text: "",
});

const { width } = useDisplay();
const isMobile = computed<boolean>(() => width.value < 760);

function currentFilePath(): string | null {
  return props.state.kind === "file" ? props.state.snapshot.meta.path : null;
}
function currentFileContent(): string | null {
  return props.state.kind === "file" ? props.state.snapshot.content : null;
}

/** INVARIANT: this watch is the ONLY point that writes to
 *  fileComments.contentCache. Both `onRequestAdd` (via extractLineContext
 *  on state.snapshot.content) and `addComment` (via contentCache) read
 *  the same value, so they MUST stay in sync — a stale preview would
 *  show context that doesn't match what the comment will actually store.
 *  2026-07-15 workspace-history-inline: when a revision is picked the
 *  current-file content no longer represents the rendered body, so we
 *  skip the registration. Otherwise the cached "context" extracted for
 *  a comment would mismatch the historical blob the user is reading.
 *  Both sources (current content + selected revision) are tracked so
 *  toggling into or out of historical mode also drops / re-establishes
 *  the cache. */
watch(
  [() => currentFileContent(), () => props.selectedRevision],
  ([content, rev]) => {
    if (rev) return;
    const path = currentFilePath();
    if (path && content !== null) {
      fileComments.registerFileContent(path, content);
    }
  },
  { immediate: true },
);

function onRequestAdd(line: number): void {
  const path = currentFilePath();
  const content = currentFileContent();
  if (!path || content === null) return;
  // Close inline ask editor if open (mutual exclusion)
  inlineAskState.value = null;
  activeEditLine.value = line;
  activeEditCommentId.value = null;
  activeEditRange.value = null;
  editorInitialText.value = "";
  editorContext.value = extractLineContext(content, line);
}

/** 2026-07-17 selection-comment: open the editor in range mode.
 *  `activeEditLine` is set to the start line so the existing
 *  `v-if="activeEditLine !== null"` gate keeps working; the range
 *  itself travels via `activeEditRange` → editor props → save
 *  payload. */
function onRequestAddRange(payload: {
  startLine: number;
  endLine: number;
  selection: string;
}): void {
  const path = currentFilePath();
  const content = currentFileContent();
  if (!path || content === null) return;
  // Close inline ask editor if open (mutual exclusion)
  inlineAskState.value = null;
  activeEditLine.value = payload.startLine;
  activeEditCommentId.value = null;
  activeEditRange.value = payload;
  editorInitialText.value = "";
  editorContext.value = null;
}

function onRequestCopySelection(text: string): void {
  void copyToClipboard(text);
}

/** 2026-07-17 selection-comment: rendered-markdown container
 *  mouseup → copy-only menu (rendered HTML has no reliable
 *  source-line mapping, so the comment item is hidden). */
function onRenderedMouseUp(e: MouseEvent): void {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed) {
    renderedMenu.value = null;
    return;
  }
  const root = e.currentTarget as HTMLElement | null;
  if (!root || !sel.anchorNode || !root.contains(sel.anchorNode)) {
    renderedMenu.value = null;
    return;
  }
  const text = sel.toString();
  if (!text.trim()) {
    renderedMenu.value = null;
    return;
  }
  renderedMenu.value = { x: e.clientX, y: e.clientY, text };
}

function onRenderedCopy(): void {
  if (renderedMenu.value) void copyToClipboard(renderedMenu.value.text);
  renderedMenu.value = null;
}

// 2026-07-17 selection-comment: toggling 原文/渲染 swaps the DOM
// under the menu (the selection itself is gone), so drop any open
// copy-menu instead of leaving it floating at stale coordinates.
watch(mdViewMode, () => {
  renderedMenu.value = null;
});

function onRequestEdit(commentId: string): void {
  const existing = fileComments.findCommentById(commentId);
  if (!existing) return;
  activeEditLine.value = existing.line;
  activeEditCommentId.value = existing.id;
  // 2026-07-17 selection-comment: restore range mode when editing
  // an existing range comment so the editor shows the selection
  // preview again (and the save payload keeps the range fields).
  activeEditRange.value =
    existing.endLine !== undefined && existing.endLine > existing.line
      ? {
          startLine: existing.line,
          endLine: existing.endLine,
          selection: existing.selection ?? existing.lineContent,
        }
      : null;
  editorInitialText.value = existing.text;
  editorContext.value = {
    lineContent: existing.lineContent,
    contextBefore: existing.contextBefore,
    contextAfter: existing.contextAfter,
  };
}

function onSaveComment(payload: {
  text: string;
  commentId: string | null;
  line: number;
  endLine: number | null;
  selection: string | null;
}): void {
  if (payload.commentId) {
    fileComments.updateComment(payload.commentId, payload.text);
    closeEditor();
    return;
  }
  const path = currentFilePath();
  if (!path) return;
  // 2026-07-17 selection-comment: the editor echoes the range in
  // the save payload when it was opened in range mode.
  const created =
    payload.endLine !== null &&
    payload.endLine > payload.line &&
    payload.selection !== null
      ? fileComments.addSelectionComment({
          filePath: path,
          startLine: payload.line,
          endLine: payload.endLine,
          selection: payload.selection,
          text: payload.text,
        })
      : fileComments.addComment({
          filePath: path,
          line: payload.line,
          text: payload.text,
        });
  if (created === null) {
    snackbar.value = {
      visible: true,
      text: tm("spcodeProjectLoad.fileBrowser.comment.saveError"),
    };
    return;
  }
  closeEditor();
}

function closeEditor(): void {
  activeEditLine.value = null;
  activeEditCommentId.value = null;
  activeEditRange.value = null;
  editorContext.value = null;
}

function onDeleteComment(commentId: string): void {
  fileComments.deleteComment(commentId);
  closeEditor();
}

// --- 2026-07-30 inline-ask ---
const inlineAnnotations = useInlineAnnotations();
const btw = useSpcodeBtw();

/** Inline ask editor state. Mutually exclusive with comment editor:
 *  opening one closes the other. */
const inlineAskState = ref<{
  startLine: number;
  endLine: number;
  selection: string;
} | null>(null);

function onRequestAskInline(payload: {
  startLine: number;
  endLine: number;
  selection: string;
}): void {
  // Close comment editor if open (mutual exclusion)
  closeEditor();
  inlineAskState.value = payload;
}

function onCloseInlineAsk(): void {
  inlineAskState.value = null;
}

/** Build the LLM prompt for an inline ask request. */
function buildInlineAskPrompt(params: {
  filePath: string;
  startLine: number;
  endLine: number;
  selection: string;
  question: string;
  fileContent: string;
}): string {
  const lines = params.fileContent.split("\n");
  const ctxStart = Math.max(0, params.startLine - 1 - 3);
  const ctxEnd = Math.min(lines.length, params.endLine + 3);
  const contextLines = lines
    .slice(ctxStart, ctxEnd)
    .map((l, i) => `${ctxStart + i + 1}: ${l}`)
    .join("\n");

  return [
    `File: ${params.filePath}`,
    `Lines ${params.startLine}-${params.endLine} (with ±3 context):`,
    "```",
    contextLines,
    "```",
    `Selected text (lines ${params.startLine}-${params.endLine}):`,
    "```",
    params.selection,
    "```",
    `User question: ${params.question}`,
  ].join("\n");
}

async function onSubmitInlineAsk(payload: {
  question: string;
  startLine: number;
  endLine: number;
  selection: string;
}): Promise<void> {
  const path = currentFilePath();
  const content = currentFileContent();
  if (!path || content === null) return;

  // Create annotation in loading state
  const ann = inlineAnnotations.addAnnotation({
    filePath: path,
    startLine: payload.startLine,
    endLine: payload.endLine,
    selection: payload.selection,
    question: payload.question,
  });

  // Close the editor
  inlineAskState.value = null;

  // Build prompt and call LLM
  const prompt = buildInlineAskPrompt({
    filePath: path,
    startLine: payload.startLine,
    endLine: payload.endLine,
    selection: payload.selection,
    question: payload.question,
    fileContent: content,
  });

  const result = await btw.ask({ prompt });
  if (result.ok) {
    inlineAnnotations.setReply(ann.id, result.reply);
  } else {
    const errMsg =
      result.reason === "network"
        ? tm("spcodeProjectLoad.fileBrowser.inlineAsk.error")
        : result.reason;
    inlineAnnotations.setError(ann.id, errMsg);
  }
}

function onDeleteAnnotation(id: string): void {
  inlineAnnotations.deleteAnnotation(id);
}

onBeforeUnmount(() => {
  btw.dispose();
});
</script>

<template>
  <div class="file-browser-preview" :class="{ 'is-mobile': isMobile }">
    <!-- 加载中 -->
    <div
      v-if="state.kind === 'idle' || state.kind === 'loading'"
      class="preview-center"
    >
      <v-progress-circular indeterminate color="primary" :size="32" />
      <span>{{ tm("spcodeProjectLoad.fileBrowser.loading") }}</span>
    </div>

    <!-- 错误(真错误:path_not_found / permission_denied / special_file / network / unknown) -->
    <div v-else-if="state.kind === 'error'" class="preview-center">
      <v-icon size="32" color="error">mdi-alert-circle-outline</v-icon>
      <div class="preview-error-title">
        {{ tm("spcodeProjectLoad.fileBrowser.error.loadFailedTitle") }}
      </div>
      <div class="preview-error-detail">
        {{ localizedReason(state.reason) }}
      </div>
      <v-btn
        size="small"
        color="primary"
        variant="tonal"
        prepend-icon="mdi-refresh"
        @click="emit('retry')"
      >
        {{ tm("spcodeProjectLoad.fileBrowser.error.retry") }}
      </v-btn>
    </div>

    <!-- 目录状态:左栏已经显示列表,右栏只显示提示 -->
    <div v-else-if="state.kind === 'directory'" class="preview-center">
      <v-icon size="32" color="grey">mdi-folder-open-outline</v-icon>
      <span class="preview-hint">{{
        tm("spcodeProjectLoad.fileBrowser.preview.selectFromLeft")
      }}</span>
    </div>

    <!-- symlink 状态 -->
    <div v-else-if="state.kind === 'symlink'" class="preview-center">
      <v-icon size="32" color="info">mdi-link-variant</v-icon>
      <div class="preview-symlink-info">
        <div class="preview-symlink-target-label">
          → {{ state.snapshot.meta.target }}
        </div>
        <div
          v-if="!state.snapshot.meta.targetExists"
          class="preview-symlink-dangling"
        >
          {{ tm("spcodeProjectLoad.fileBrowser.entryType.dangling") }}
        </div>
      </div>
      <v-btn
        v-if="state.snapshot.meta.targetExists"
        size="small"
        variant="tonal"
        prepend-icon="mdi-arrow-right"
        @click="
          emit(
            'navigate-target',
            resolveTargetPath(
              state.snapshot.meta.path,
              state.snapshot.meta.target,
            ),
          )
        "
      >
        {{ tm("spcodeProjectLoad.fileBrowser.preview.goToTarget") }}
      </v-btn>
    </div>

    <!-- 文件 -->
    <!-- 2026-07-17: the inner-fullscreen Teleport wrapper was removed
         — its role duplicated GitDiffSidebar's sidebar-wide fullscreen
         button. This single .preview-file div keeps the flex height
         chain (.file-browser-preview → .preview-file → .code-view with
         overflow:auto) so long files scroll inside the preview pane
         instead of clipping against the outer overflow:hidden. -->
        <div v-else-if="state.kind === 'file'" class="preview-file">
          <!--
            2026-07-15 workspace-history-banner: when a revision
            is picked, show the same "正在查看历史版本 {sha}" +
            "回到当前" banner that <DocumentManager> renders for
            its document-manager tab. Reuses the document-manager
            i18n keys (viewingRevision / backToCurrent) so both
            tabs read identically. The banner sits ABOVE the
            preview-file-meta row so it has full pane width and
            the user always sees a clear "you are looking at a
            historical revision" cue, plus a one-click way back.
            The previous inline `@0be821a` chip (right of the
            action buttons) was easy to miss; the banner matches
            the document-manager UX and is consistent with the
            user-feedback request to align the two tabs.
          -->
          <div
            v-if="props.rangeDiff || props.selectedRevision"
            class="preview-file__banner"
          >
            <span v-if="props.rangeDiff">
              {{
                tm(
                  "spcodeProjectLoad.documentManager.history.rangeBanner",
                  {
                    from: props.rangeDiff.from.slice(0, 7),
                    to: props.rangeDiff.to.slice(0, 7),
                  }
                )
              }}
            </span>
            <span v-else>
              {{
                tm(
                  "spcodeProjectLoad.documentManager.viewMode.viewingRevision",
                  { sha: shortRevisionLabel }
                )
              }}
            </span>
            <button
              type="button"
              class="preview-file__banner-btn"
              @click="emit('back-to-current')"
            >
              {{
                tm(
                  "spcodeProjectLoad.documentManager.viewMode.backToCurrent"
                )
              }}
            </button>
          </div>
          <!-- 元信息头 -->
          <div class="preview-file-meta">
            <span class="preview-file-path" :title="state.snapshot.meta.path">{{
              state.snapshot.meta.name
            }}</span>
            <!-- 2026-07-03 ANSI/GBK 支持:当文件编码不是 utf-8 时,
             在元信息头显示一个 subtle 灰色 chip 提示用户。utf-8
             不显示(主流情况,避免视觉噪声);cp936/gbk/gb18030/
             latin-1/utf-8-sig 等显示完整编码名。 -->
            <span
              v-if="
                state.snapshot.meta.encoding &&
                state.snapshot.meta.encoding !== 'utf-8'
              "
              class="preview-file-encoding"
              :title="
                tm('spcodeProjectLoad.fileBrowser.preview.encodingLabel', {
                  encoding: state.snapshot.meta.encoding,
                })
              "
            >
              {{ state.snapshot.meta.encoding }}
            </span>
            <span class="preview-file-size">{{
              formatBytes(state.snapshot.meta.size)
            }}</span>
            <span class="preview-file-mtime">{{
              formatMtime(state.snapshot.meta.mtime)
            }}</span>
            <!--
              2026-07-15 workspace-history-parity: the per-file "View
              file history" button used to live here. It was removed
              so the workspace tab mirrors the document-manager tab,
              where history is shown in a right-edge SIDE PANEL
              (<DocumentHistoryPanel>) owned by <FileBrowserView>.
              The "Copy" button (next) is the only remaining per-file
              action in this meta header.

              2026-07-15 workspace-history-banner: the inline
              `@<sha>` chip that previously lived here was removed
              in favor of the full-width banner rendered ABOVE this
              meta row (see `.preview-file__banner`). The chip was
              easy to miss and the document-manager tab already
              uses the banner pattern; aligning the two tabs here
              keeps the "viewing historical revision" affordance
              consistent across the workspace.

              2026-07-15 workspace-history-inline: the copy button now
          also serves the historical-raw view — copying the picked
          revision's content is a reasonable action (the user is
          inspecting it precisely because they want to compare).
          Diff mode copies nothing (it'd be the entire patch,
          which would be surprising); same as <DocumentManager>.
        -->
            <!-- 2026-07-17 workspace-md-render: raw ⇄ rendered flip
                 for markdown files. Shows the CURRENT mode (click to
                 switch), mirroring the document-manager 原文/渲染
                 view tabs; reuses its i18n keys. -->
            <v-btn
              v-if="canToggleMdView"
              size="x-small"
              variant="text"
              color="primary"
              :prepend-icon="
                mdViewMode === 'rendered'
                  ? 'mdi-language-markdown-outline'
                  : 'mdi-code-tags'
              "
              :aria-pressed="mdViewMode === 'rendered'"
              @click="
                mdViewMode = mdViewMode === 'rendered' ? 'raw' : 'rendered'
              "
            >
              {{
                tm(
                  mdViewMode === 'rendered'
                    ? 'spcodeProjectLoad.documentManager.viewMode.rendered'
                    : 'spcodeProjectLoad.documentManager.viewMode.raw',
                )
              }}
            </v-btn>
            <!-- 2026-07-17 workspace file editor: enters edit mode
                 (body swaps to <CodeMirrorEditor>). Hidden while editing,
                 for history views, binaries, oversized files, and
                 when no repo-relative path is available. -->
            <v-btn
              v-if="canEdit && !editMode"
              size="x-small"
              variant="text"
              color="primary"
              prepend-icon="mdi-pencil-outline"
              @click="onStartEdit"
            >
              {{ tm("spcodeProjectLoad.fileBrowser.editor.edit") }}
            </v-btn>
            <v-btn
              v-if="
                !editMode &&
                ((state.snapshot.content && !isHistoricalDiff) ||
                  (isHistoricalRaw && (props.historicalContent ?? '')))
              "
              size="x-small"
              variant="text"
              :color="copyButtonColor"
              prepend-icon="mdi-content-copy"
              @click="copyContent"
            >
              {{ copyButtonText }}
            </v-btn>
            <!-- 2026-08-14 open-on-disk: open the previewed file on
                 the AstrBot host with the OS default application.
                 Available for the current file and historical views
                 alike — the on-disk working copy is opened either
                 way. Same affordance as DocumentManager's toolbar. -->
            <v-btn
              v-if="!editMode"
              size="x-small"
              variant="text"
              color="primary"
              prepend-icon="mdi-open-in-new"
              :disabled="openingOnDisk"
              @click="openPreviewOnDisk"
            >
              {{ tm("spcodeProjectLoad.fileBrowser.preview.openOnDisk") }}
            </v-btn>
          </div>

          <!-- 2026-08-13 one-click code check / format toolbar. Rendered
               only for the live working copy (not edit mode, not a
               historical revision); the bar itself hides non-code files. -->
          <CodeCheckFormatBar
            v-if="!editMode && !props.selectedRevision"
            :file-path="props.fileRelativePath ?? ''"
            :worktree="props.worktree ?? null"
            :is-dark="isDark"
            @formatted="emit('saved')"
          />

          <!--
        2026-07-15 workspace-history-inline: when a revision is
        picked in 'diff' mode, the body becomes the unified
        diff between that revision and the working copy. We
        reuse <DiffPreview> verbatim (it lives next door and the
        document-manager tab already drives it the same way).
        Renders before the legacy branches so the historical view
        wins over both binary / too-large / current-text paths.
        DiffPreview accepts plain unified diff text; it parses
        the hunk headers itself, so the prop is just `content`.
        `commentable` is intentionally false — comments key on
        current-file line numbers, which don't match diff lines.
      -->
          <!-- 2026-07-17 workspace file editor: edit mode swaps the
               whole body for the overlay editor + a compact toolbar.
               Rendered before every read-only branch so it wins over
               the current-file code view. -->
          <div v-if="editMode" class="preview-editor">
            <CodeMirrorEditor
              ref="editorRef"
              :model-value="editInitialContent"
              :file-path="state.snapshot.meta.path"
              class="preview-editor-body"
              @dirty-change="onEditDirtyChange"
            />
            <div class="preview-editor-toolbar editor-bar">
              <span class="preview-editor-notice">{{
                encodingNotice ?? ""
              }}</span>
              <span v-if="saveError" class="preview-editor-error">{{
                saveError
              }}</span>
              <button
                type="button"
                class="editor-bar__btn editor-bar__btn--primary"
                :title="tm('spcodeProjectLoad.fileBrowser.editor.saveHint')"
                :disabled="!isEditDirty || fileWrite.isSaving.value"
                @click="onSaveEdit"
              >
                <v-icon size="14">mdi-content-save-outline</v-icon>
                {{ tm("spcodeProjectLoad.fileBrowser.editor.save") }}
              </button>
              <button
                type="button"
                class="editor-bar__btn"
                :disabled="fileWrite.isSaving.value"
                @click="onCancelEdit"
              >
                <v-icon size="14">mdi-close</v-icon>
                {{ tm("spcodeProjectLoad.fileBrowser.editor.cancel") }}
              </button>
              <button
                type="button"
                class="editor-bar__btn"
                :disabled="fileWrite.isSaving.value"
                @click="onRenameClick"
              >
                <v-icon size="14">mdi-rename-outline</v-icon>
                {{ tm("spcodeProjectLoad.fileBrowser.editor.rename") }}
              </button>
              <button
                type="button"
                class="editor-bar__btn"
                :title="tm('spcodeProjectLoad.fileBrowser.preview.copy')"
                :disabled="fileWrite.isSaving.value"
                @click="onCopyEdit"
              >
                <v-icon size="14">mdi-content-copy</v-icon>
              </button>
              <!-- 2026-07-26 save-success-toast: green inline
                   confirmation that lives in the dead space between
                   the copy button and the right-aligned delete.
                   Fades in when fileWrite.save resolves r.ok and
                   back out when the local auto-dismiss timer clears
                   the ref. -->
              <Transition name="preview-editor__success-fade">
                <span
                  v-if="saveSuccessMessage"
                  class="preview-editor__save-success"
                  role="status"
                  aria-live="polite"
                >
                  <v-icon size="14" color="success">mdi-check-circle-outline</v-icon>
                  {{ saveSuccessMessage }}
                </span>
              </Transition>
              <span class="editor-bar__spacer" />
              <button
                type="button"
                class="editor-bar__btn editor-bar__btn--danger"
                :disabled="fileWrite.isSaving.value"
                @click="onDeleteClick"
              >
                <v-icon size="14">mdi-delete-outline</v-icon>
                {{ tm("spcodeProjectLoad.fileBrowser.editor.delete") }}
              </button>
            </div>
          </div>

          <!-- 2026-10-06 range-compare:优先于历史 diff 分支,
               compareRange 激活时 selectedRevision 必为 null -->
          <div
            v-else-if="props.rangeDiff"
            class="preview-file__diff-wrapper"
          >
            <DiffPreview
              :content="props.rangeDiff.patch ?? ''"
              :file-path="state.snapshot.meta.path"
              :base-content="
                props.rangeDiff.status !== 'added'
                  ? props.rangeDiff.baseContent
                  : undefined
              "
              :is-dark="isDark"
              :commentable="false"
            />
          </div>
          <div
            v-else-if="isHistoricalDiff"
            class="preview-file__diff-wrapper"
          >
            <DiffPreview
              :content="props.diffPatch ?? ''"
              :file-path="state.snapshot.meta.path"
              :summary="
                props.diffIsBinary
                  ? tm('spcodeProjectLoad.fileBrowser.preview.binary')
                  : (props.diffPatch ?? '').length
                  ? ''
                  : tm('spcodeProjectLoad.fileBrowser.loading')
              "
              :is-dark="isDark"
              :commentable="false"
            />
          </div>

          <!--
        Historical-raw mode: a revision is picked but the user
        asked to see the file content at that revision (not the
        diff). The blob is binary → placeholder, otherwise it
        flows through the same <FileBrowserCodeView> as the
        current file. We deliberately DO NOT register a comment
        gutter here — see the historicalContent watcher above.
      -->
          <!-- 2026-07-31 workspace-image-preview: image files render
               inline through <BinaryPreview> (fetches bytes via
               /spcode/file-binary, dispatches to ImagePreview). Placed
               right after the historical-DIFF branch so images win over
               every generic "binary file" placeholder below, for both
               the current working copy and historical-raw views.
               Edit mode and historical diff mode still take precedence
               above. -->
          <div v-else-if="isImageFile" class="preview-image">
            <BinaryPreview
              :path="props.fileRelativePath ?? ''"
              :git-ref="props.selectedRevision ?? ''"
              :worktree="props.worktree ?? null"
            />
          </div>

          <div
            v-else-if="isHistoricalRaw && props.historicalIsBinary"
            class="preview-binary"
          >
            <v-icon size="32" color="grey">mdi-file-question-outline</v-icon>
            <span>{{
              tm("spcodeProjectLoad.fileBrowser.preview.binary")
            }}</span>
          </div>
          <!-- 2026-07-17 workspace-md-render: historical markdown in
               rendered mode. Placed before the raw code view so it
               wins once the blob is loaded; the loading branch below
               still covers the not-yet-fetched case. -->
          <div
            v-else-if="
              isHistoricalRaw && mdRenderActive && props.historicalContent
            "
            class="preview-markdown"
            @mouseup="onRenderedMouseUp"
          >
            <MarkdownView
              :source="props.historicalContent ?? ''"
              :is-dark="isDark"
            />
          </div>
          <FileBrowserCodeView
            v-else-if="isHistoricalRaw && highlightedHistoricalHtml"
            :highlighted-html="highlightedHistoricalHtml"
            :file-path="state.snapshot.meta.path"
            :comments="[]"
            :active-edit-line="null"
            :active-edit-comment-id="null"
            :is-dark="isDark"
            :scroll-to-line="null"
            :selection-commentable="false"
            @request-add="onRequestAdd"
            @request-edit="onRequestEdit"
            @request-add-range="onRequestAddRange"
            @copy-selection="onRequestCopySelection"
          />
          <div v-else-if="isHistoricalRaw" class="preview-binary">
            <v-progress-circular indeterminate color="primary" :size="20" />
            <span>{{ tm("spcodeProjectLoad.fileBrowser.loading") }}</span>
          </div>

          <!-- 二进制文件 (legacy: current working copy is binary) -->
          <div
            v-else-if="state.snapshot.meta.isBinary === true"
            class="preview-binary"
          >
            <v-icon size="32" color="grey">mdi-file-question-outline</v-icon>
            <span>{{
              tm("spcodeProjectLoad.fileBrowser.preview.binary")
            }}</span>
          </div>

          <!-- 过大文件 (legacy: current working copy is too large) -->
          <div
            v-else-if="state.snapshot.content === null"
            class="preview-binary"
          >
            <v-icon size="32" color="grey">mdi-file-alert-outline</v-icon>
            <span>{{
              tm("spcodeProjectLoad.fileBrowser.preview.tooLarge", {
                size: formatBytes(state.snapshot.meta.size),
              })
            }}</span>
          </div>

          <!-- 2026-07-17 workspace-md-render: current-file markdown
               in rendered mode. Sits after the binary / too-large
               branches so only real text content reaches it. The
               raw code view below remains the fallback (and the
               原文 mode target). -->
          <div
            v-else-if="mdRenderActive"
            class="preview-markdown"
            @mouseup="onRenderedMouseUp"
          >
            <MarkdownView
              :source="state.snapshot.content ?? ''"
              :is-dark="isDark"
            />
          </div>

          <!-- 文本内容(Shiki 高亮) + 行内评论 gutter/编辑器 (legacy: current) -->
          <FileBrowserCodeView
            v-else
            :highlighted-html="highlightedHtml"
            :file-path="state.snapshot.meta.path"
            :comments="fileComments.commentsForFile(state.snapshot.meta.path)"
            :active-edit-line="activeEditLine"
            :active-edit-comment-id="activeEditCommentId"
            :is-dark="isDark"
            :scroll-to-line="props.scrollToLine ?? null"
            :annotations="inlineAnnotations.annotationsForFile(state.snapshot.meta.path)"
            @request-add="onRequestAdd"
            @request-edit="onRequestEdit"
            @request-add-range="onRequestAddRange"
            @request-ask-inline="onRequestAskInline"
            @copy-selection="onRequestCopySelection"
            @delete-annotation="onDeleteAnnotation"
          />
          <FileCommentEditor
            v-if="activeEditLine !== null"
            :line="activeEditLine"
            :comment-id="activeEditCommentId"
            :initial-text="editorInitialText"
            :line-content="editorContext?.lineContent ?? null"
            :context-before="editorContext?.contextBefore ?? null"
            :context-after="editorContext?.contextAfter ?? null"
            :file-path="state.snapshot.meta.path"
            :end-line="activeEditRange?.endLine ?? null"
            :selection-content="activeEditRange?.selection ?? null"
            @save="onSaveComment"
            @cancel="closeEditor"
            @delete="onDeleteComment"
          />
          <InlineAskEditor
            v-if="inlineAskState !== null"
            :start-line="inlineAskState.startLine"
            :end-line="inlineAskState.endLine"
            :selection="inlineAskState.selection"
            :file-path="state.snapshot.meta.path"
            @submit="onSubmitInlineAsk"
            @cancel="onCloseInlineAsk"
          />
          <!-- 2026-07-17 selection-comment: copy-only menu for the
               rendered-markdown containers above (fixed position,
               one shared instance is enough). -->
          <SelectionActionMenu
            v-if="renderedMenu"
            :x="renderedMenu.x"
            :y="renderedMenu.y"
            :show-comment="false"
            @copy="onRenderedCopy"
            @close="renderedMenu = null"
          />
        </div>
    <!-- 2026-07-18 editor toolbar parity: rename / delete dialogs.
         Rename is same-dir only (bare file name); delete is a hard
         unlink, so the confirm copy carries the cannot-undo warning. -->
    <v-dialog v-model="renameOpen" max-width="420">
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">
          {{ tm("spcodeProjectLoad.fileBrowser.editor.renameDialogTitle") }}
        </v-card-title>
        <v-card-text class="pt-4">
          <v-text-field
            v-model="renameName"
            :label="tm('spcodeProjectLoad.fileBrowser.editor.newNameLabel')"
            density="comfortable"
            autofocus
            :error-messages="renameError ? [renameError] : []"
            @keyup.enter="onConfirmRename"
          />
        </v-card-text>
        <v-card-actions class="pa-4 pt-0">
          <v-spacer />
          <v-btn variant="text" @click="renameOpen = false">
            {{ tm("spcodeProjectLoad.fileBrowser.editor.cancel") }}
          </v-btn>
          <v-btn
            variant="tonal"
            color="primary"
            :loading="fileRename.isRenaming.value"
            @click="onConfirmRename"
          >
            {{ tm("spcodeProjectLoad.fileBrowser.editor.renameConfirm") }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
    <v-dialog v-model="deleteOpen" max-width="420">
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">
          {{ tm("spcodeProjectLoad.fileBrowser.editor.deleteDialogTitle") }}
        </v-card-title>
        <v-card-text class="pt-4">
          {{
            tm("spcodeProjectLoad.fileBrowser.editor.deleteConfirmMessage", {
              name: currentFileName,
            })
          }}
        </v-card-text>
        <v-card-actions class="pa-4 pt-0">
          <span v-if="deleteError" class="preview-editor-error pl-4">{{
            deleteError
          }}</span>
          <v-spacer />
          <v-btn variant="text" @click="deleteOpen = false">
            {{ tm("spcodeProjectLoad.fileBrowser.editor.cancel") }}
          </v-btn>
          <v-btn
            variant="tonal"
            color="error"
            :loading="fileRemove.isRemoving.value"
            @click="onConfirmDelete"
          >
            {{ tm("spcodeProjectLoad.fileBrowser.editor.deleteConfirm") }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
    <v-snackbar
      v-model="snackbar.visible"
      :timeout="4000"
      color="error"
      location="bottom"
    >
      {{ snackbar.text }}
    </v-snackbar>
  </div>
</template>

<style scoped>
@import "@/styles/editor-bar.css";

.file-browser-preview {
  /* Width is now driven by FileBrowserView's draggable divider
     (sets `width` via inline style on .file-browser-pane-right, which
     this root element inherits as its wrapping class). */
  flex: 1 1 auto;
  min-width: 0;
  height: 100%;
  overflow-y: auto;
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
  background: transparent;
}
.preview-center {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  height: 100%;
  padding: 32px 16px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 13px;
  text-align: center;
}
.preview-hint {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 12.5px;
}
.preview-error-title {
  color: rgba(var(--v-theme-error), 1);
  font-weight: 500;
  font-size: 14px;
}
.preview-error-detail {
  color: rgba(var(--v-theme-on-surface), 0.7);
  font-size: 12.5px;
  max-width: 320px;
}

/* 2026-07-17: the .preview-file.is-fullscreen rules were removed
   together with the inner-fullscreen feature (see template note). */
/* 2026-07-17 workspace-md-render: scrollable rendered-markdown body.
   Mirrors .document-manager__rendered from DocumentManager so the
   two tabs' 渲染 views read identically. */
.preview-markdown {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 12px;
}
.preview-file {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}
.preview-file__diff-wrapper {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}
.preview-file-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 14px;
  font-size: 11.5px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  background: rgba(var(--v-theme-surface), 0.4);
}
.preview-file-path {
  flex: 1;
  font-family: ui-monospace, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preview-file-size,
.preview-file-mtime {
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), 0.4);
}
/* 2026-07-03 ANSI/GBK 支持:非 utf-8 文件在元信息头显示编码徽章。
   视觉上与 .preview-file-size 同级但用 monospace + 浅色背景区分,
   让用户一眼看出"这不是 utf-8"。hover 时显示 i18n 完整提示。 */
.preview-file-encoding {
  font-family: ui-monospace, monospace;
  font-size: 10.5px;
  padding: 1px 6px;
  border-radius: 3px;
  background: rgba(var(--v-theme-on-surface), 0.08);
  color: rgba(var(--v-theme-on-surface), 0.7);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  cursor: help;
  user-select: none;
}
/*
  2026-07-15 workspace-history-banner: full-width banner shown
  ABOVE `.preview-file-meta` when a historical revision is
  picked. Mirrors `.document-manager__banner` from
  <DocumentManager> verbatim (same info-tinted background,
  same border-bottom separator, same body-font-size cue) so
  the "you are looking at an old version" affordance reads
  identically across the workspace tab and the document
  manager tab. The previous inline revision chip (the
  `.preview-file-revision` rule removed in the same change)
  was easy to miss and only used a tiny pill in the meta
  header; the user-feedback request was to align the two
  tabs, so we now render the same banner DocumentManager
  uses for its document-manager tab.
*/
.preview-file__banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  background: rgba(var(--v-theme-info), 0.08);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  font-size: 11.5px;
  color: rgb(var(--v-theme-info));
}
.preview-file__banner-btn {
  background: transparent;
  border: 1px solid currentColor;
  color: inherit;
  border-radius: 3px;
  padding: 1px 6px;
  font-size: 11px;
  cursor: pointer;
}
.preview-file-content {
  flex: 1;
  margin: 0;
  padding: 12px 14px;
  overflow: auto;
  font-family: ui-monospace, monospace;
  font-size: 12.5px;
  line-height: 1.55;
  background: transparent !important;
}
.preview-binary {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 32px 16px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 13px;
}
/* 2026-07-31 workspace-image-preview: keeps the flex height chain so
   BinaryPreview fills the pane and tall images scroll inside it. */
.preview-image {
  flex: 1;
  min-height: 0;
  display: flex;
  padding: 8px;
}
.preview-symlink-info {
  font-family: ui-monospace, monospace;
  font-size: 13px;
  text-align: center;
}
.preview-symlink-target-label {
  color: rgb(var(--v-theme-info));
}
.preview-symlink-dangling {
  color: rgb(248, 81, 73);
  font-size: 12px;
  margin-top: 6px;
}

/* Mobile fullscreen overlay for the inline-comment editor.
   Lives here (not in FileCommentEditor.vue) so the .file-browser-preview
   class is in the same Vue scope - see Chunk 2 review. */
.file-browser-preview.is-mobile .comment-editor {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgb(var(--v-theme-surface));
  display: flex;
  flex-direction: column;
}
.file-browser-preview.is-mobile .comment-editor-input {
  flex: 1;
  resize: none;
}

/* 2026-07-17 workspace file editor: edit-mode layout. Mirrors
   <DocumentEditor>'s structure — <CodeMirrorEditor> on top, the
   action toolbar fixed underneath (same border-top + padding
   rhythm DocumentEditor uses), filling the remaining height
   and scrolling internally. */
.preview-editor {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}
.preview-editor-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  flex-shrink: 0;
}
.preview-editor-notice {
  flex: 0 1 auto;
  min-width: 0;
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preview-editor-error {
  font-size: 12px;
  color: rgb(var(--v-theme-error));
  white-space: nowrap;
}
/* 2026-07-26 save-success-toast: green inline confirmation sitting
   in the toolbar's left-side flex flow. Inline-flex keeps the
   checkmark + text on one line; gap mirrors the toolbar button
   rhythm so the toast visually fits next to the action buttons. */
.preview-editor__save-success {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: 4px;
  font-size: 12px;
  color: rgb(var(--v-theme-success));
  white-space: nowrap;
}
/* Fade in/out for the toast transition. Slightly longer leave than
   enter so the message reads comfortably before it dissolves. */
.preview-editor__success-fade-enter-active,
.preview-editor__success-fade-leave-active {
  transition: opacity 180ms ease;
}
.preview-editor__success-fade-enter-from,
.preview-editor__success-fade-leave-to {
  opacity: 0;
}
.preview-editor-body {
  flex: 1;
  min-height: 0;
}
</style>
