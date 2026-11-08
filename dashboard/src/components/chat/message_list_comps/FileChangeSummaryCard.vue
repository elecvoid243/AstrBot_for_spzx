<!--
  FileChangeSummaryCard
  ------------------------------------------------------------------
  End-of-turn file change summary (2026-09-13). Rendered after the
  final reply blocks of a bot message that changed files through the
  built-in file tools. Entries are aggregated per file by the backend
  at agent-loop end (net diff vs the turn's baseline backup) and
  delivered via the `file_changes` stream event, persisted as
  `content.file_changes`.

  Row actions: expand → lazily fetch the unified diff
  (POST /chat/file-changes/diff), open the file (in the chat's
  Git-diff sidebar file browser, on disk, or its containing folder —
  one "open" button that expands into those three targets, sidebar
  first), and two-click inline undo
  (POST /chat/file-changes/restore). A reverted row collapses,
  shows a "changes reverted" badge and disables further
  diff/undo actions.

  Whole-card collapse (2026-09-17): the header is itself the toggle — a
  real <button> spanning the full row — so the click target is the whole
  "N files changed  +a −b" strip instead of a small chevron. Collapsing
  hides the file rows and keeps the count + totals visible, so a long
  list can be folded away once reviewed. The state is per-card-instance
  and starts expanded; the per-row expand set and cached diffs survive a
  collapse, so re-expanding costs no refetch.

  Author: elecvoid243 | 2026-09-13
-->
<template>
  <div
    v-if="accountRevision === accountSessionRevision"
    class="fcs-card"
    :class="{ 'fcs-card--dark': isDark, 'fcs-card--collapsed': collapsed }"
  >
    <button
      type="button"
      class="fcs-head"
      :title="toggleLabel"
      :aria-expanded="!collapsed"
      @click="
        collapsed = !collapsed;
        openGroupPath = '';
      "
    >
      <span class="fcs-head-icon">
        <v-icon size="13">mdi-file-edit-outline</v-icon>
      </span>
      <span class="fcs-title">
        {{ tm("fileChanges.title", { count: files.length }) }}
      </span>
      <span v-if="totalAdds !== null" class="fcs-total">
        <span class="stat-adds">+{{ totalAdds }}</span>
        <span class="stat-dels">−{{ totalDels }}</span>
      </span>
      <v-icon
        class="fcs-head-chevron"
        size="16"
        :icon="collapsed ? 'mdi-chevron-down' : 'mdi-chevron-up'"
      />
    </button>

    <template v-for="file in files" :key="file.path">
      <div
        v-if="!collapsed"
        class="fcs-row"
        :class="{ 'fcs-row--reverted': isReverted(file) }"
      >
        <button
          type="button"
          class="fcs-file"
          :disabled="!canExpand(file)"
          @click="toggleRow(file)"
        >
          <v-icon size="15" class="fcs-kind" :class="`fcs-kind--${file.kind}`">
            {{ kindIcon(file.kind) }}
          </v-icon>
          <span class="fcs-name" :title="file.path">
            {{ basename(file.path) }}
          </span>
          <span
            v-if="isReverted(file)"
            class="fcs-reverted-badge"
          >
            <v-icon size="11">mdi-check</v-icon>
            {{ tm("fileChanges.reverted") }}
          </span>
          <span v-else-if="file.kind === 'remove'" class="fcs-muted">
            {{ tm("fileChanges.removed") }}
          </span>
          <template v-else-if="file.diff_available && file.adds !== null">
            <span class="stat-adds">+{{ file.adds }}</span>
            <span class="stat-dels">−{{ file.dels }}</span>
          </template>
          <span v-else class="fcs-muted">
            {{
              file.runtime === "sandbox"
                ? tm("fileChanges.sandboxHint")
                : file.sha256 === ""
                  ? tm("fileChanges.fileGone")
                  : tm("fileChanges.noStat")
            }}
          </span>
          <v-icon
            v-if="canExpand(file)"
            size="15"
            class="fcs-chevron"
            :class="{ expanded: expanded.has(file.path) }"
          >
            mdi-chevron-right
          </v-icon>
        </button>

        <div class="fcs-actions">
          <div
            v-if="hasOpenActions(file)"
            class="fcs-open-group"
            :class="{ 'fcs-open-group--expanded': openGroupPath === file.path }"
          >
            <button
              v-if="openGroupPath !== file.path"
              type="button"
              class="fcs-open-toggle"
              :title="tm('fileChange.open')"
              @click.stop="openGroupPath = file.path"
            >
              <!-- Same glyph as the diff sidebar's open menu: the text label
                   made the compact action strip twice as wide. -->
              <v-icon size="15">mdi-open-in-app</v-icon>
              <v-icon size="12">mdi-chevron-down</v-icon>
            </button>
            <template v-else>
              <v-btn
                v-if="canOpenInSidebar(file)"
                icon="mdi-dock-right"
                size="x-small"
                variant="text"
                :title="tm('fileChange.openInSidebar')"
                @click.stop="openFileInSidebar(file)"
              />
              <v-btn
                v-if="canOpenFile(file)"
                icon="mdi-open-in-new"
                size="x-small"
                variant="text"
                :title="tm('fileChange.openOnDisk')"
                @click.stop="openFileOnDisk(file)"
              />
              <v-btn
                v-if="canOpenFolder(file)"
                icon="mdi-folder-open-outline"
                size="x-small"
                variant="text"
                :title="tm('fileChange.openFolder')"
                @click.stop="openContainingFolder(file)"
              />
            </template>
          </div>
          <v-btn
            v-if="canUndo(file)"
            icon="mdi-undo"
            size="x-small"
            variant="text"
            :color="confirmingPath === file.path ? 'error' : undefined"
            :loading="restoringPath === file.path"
            :title="
              confirmingPath === file.path
                ? tm('fileChanges.undoConfirm')
                : tm('fileChanges.undo')
            "
            @click.stop="undoFile(file)"
          />
        </div>
      </div>

      <div
        v-if="!collapsed && expanded.has(file.path)"
        class="fcs-body"
      >
        <div v-if="loadingPath === file.path" class="fcs-loading">
          <v-progress-circular indeterminate size="14" width="2" />
        </div>
        <template v-else-if="diffs[file.path]">
          <div v-if="diffs[file.path].truncated" class="fcs-muted fcs-truncated">
            {{ tm("fileChanges.diffTruncated") }}
          </div>
          <DiffPreview
            :content="diffs[file.path].diff"
            :file-path="file.path"
            :base-content="baseContents[file.path]"
            v-model:overlay-mode="overlayModes[file.path]"
            :overlay-supported="canOverlay(file)"
            :is-dark="isDark"
            :max-lines="400"
          />
        </template>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, onBeforeUnmount, reactive, ref, watch } from "vue";
import DiffPreview from "./DiffPreview.vue";
import { chatApi } from "@/api/v1";
import { useModuleI18n } from "@/i18n/composables";
import { useOpenOnDisk } from "@/composables/useOpenOnDisk";
import { OPEN_FILE_REFERENCE } from "@/composables/fileReference";
import { useToast } from "@/utils/toast";
import { accountSessionRevision, assertCurrentAccount } from "@/utils/accountSession";
import type { FileChangeSummaryFile } from "@/utils/fileChangeTool";

const props = defineProps<{
  files: FileChangeSummaryFile[];
  sessionId?: string;
  messageId?: string | number;
  runId?: string;
  sourceUnavailable?: boolean;
  isDark?: boolean;
}>();

const accountRevision = accountSessionRevision.value;
const isAdmin = localStorage.getItem("role") === "admin";
const hasSource = computed(() => Boolean(props.sessionId && (
  (props.messageId != null && /^\d+$/.test(String(props.messageId))) || props.runId
)));
const source = computed(() => ({
  session_id: props.sessionId,
  ...(props.messageId != null && /^\d+$/.test(String(props.messageId))
    ? { message_id: props.messageId }
    : { run_id: props.runId }),
}));
watch(accountSessionRevision, () => {
  expanded.clear();
  reverted.clear();
  openGroupPath.value = "";
  for (const cache of [diffs, baseContents, overlayModes]) {
    for (const key of Object.keys(cache)) delete cache[key];
  }
  confirmingPath.value = "";
}, { flush: "sync" });

const { tm } = useModuleI18n("features/chat");
const { openOnDisk, openFolder } = useOpenOnDisk("fileChange");
const toast = useToast();

/** Bridge to the Git-diff sidebar's file browser. Chat.vue provides the
 * same callback for Markdown file-reference links; surfaces without that
 * sidebar simply do not expose the "open in sidebar" target. */
const openFileReference = inject(OPEN_FILE_REFERENCE, undefined);

/** Path whose open group is expanded. Only one row expands at a time, and
 * "" means every row shows the collapsed "Open" button. */
const openGroupPath = ref("");

/** Whole-card collapse (2026-09-17): hides the file rows, keeps the
 * header (count + totals) visible. Per-instance and expanded by default
 * — a fresh card rendered by a history reload starts open again. */
const collapsed = ref(false);

const expanded = reactive(new Set<string>());
/** Paths known to match their baseline: collapsed, badged, actions locked.
 * Seeded by the end-of-turn undo and re-derived from the backend on load,
 * so the badge survives refreshes and covers LLM/manual reverts too. */
const reverted = reactive(new Set<string>());
/** True while the backend revert-status check has not answered yet. */
const statusPending = ref(false);
const diffs = reactive<Record<string, { diff: string; truncated: boolean }>>(
  {},
);
/**
 * 2026-10-18 (elecvoid243): 叠加视图的基准全文来自 /chat/file-changes/diff
 * (后端已持有该轮 baseline 备份)。没拿到基准的文件,第三格保持禁用。
 */
const baseContents = reactive<Record<string, string | undefined>>({});
/** 每个文件的叠加意图;DiffPreview 受控,这里持有状态。 */
const overlayModes = reactive<Record<string, boolean>>({});
const loadingPath = ref("");
const confirmingPath = ref("");
const restoringPath = ref("");

const totalAdds = computed(() =>
  props.files.every((f) => f.adds !== null)
    ? props.files.reduce((sum, f) => sum + (f.adds || 0), 0)
    : null,
);
const totalDels = computed(() =>
  props.files.every((f) => f.dels !== null)
    ? props.files.reduce((sum, f) => sum + (f.dels || 0), 0)
    : null,
);

/** Tooltip / aria label of the header toggle, describing the NEXT state. */
const toggleLabel = computed(() =>
  tm(collapsed.value ? "fileChanges.expand" : "fileChanges.collapse"),
);

function basename(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  return normalized.slice(normalized.lastIndexOf("/") + 1) || path;
}

function kindIcon(kind: string): string {
  if (kind === "created") return "mdi-file-plus-outline";
  if (kind === "remove") return "mdi-delete-outline";
  if (kind === "rollback") return "mdi-restore";
  return "mdi-file-edit-outline";
}

function isReverted(file: FileChangeSummaryFile): boolean {
  return reverted.has(file.path);
}

function canExpand(file: FileChangeSummaryFile): boolean {
  return hasSource.value && !props.sourceUnavailable && !!file.sha256 && file.diff_available && !isReverted(file);
}

/** 叠加需要非空基准原文;created / 空基准没有可叠在下面的旧内容。 */
function canOverlay(file: FileChangeSummaryFile): boolean {
  return file.kind !== "created" && !!baseContents[file.path];
}

/** Removals have no file to open (only the parent folder survives). */
function canOpenFile(file: FileChangeSummaryFile): boolean {
  return localStorage.getItem("role") === "admin" && file.runtime === "local" && file.kind !== "remove";
}

function canOpenFolder(file: FileChangeSummaryFile): boolean {
  return file.runtime === "local" && isAdmin;
}

/** A removal has no bytes to preview, so the sidebar would only show a
 * path-not-found error for it. */
function canOpenInSidebar(file: FileChangeSummaryFile): boolean {
  return (
    !!openFileReference &&
    isAdmin &&
    file.runtime === "local" &&
    file.kind !== "remove"
  );
}

function hasOpenActions(file: FileChangeSummaryFile): boolean {
  return canOpenFile(file) || canOpenFolder(file) || canOpenInSidebar(file);
}

function openFileOnDisk(file: FileChangeSummaryFile) {
  openGroupPath.value = "";
  void openOnDisk(file.path, basename(file.path));
}

function openContainingFolder(file: FileChangeSummaryFile) {
  openGroupPath.value = "";
  void openFolder(file.path, basename(file.path));
}

function openFileInSidebar(file: FileChangeSummaryFile) {
  openGroupPath.value = "";
  openFileReference?.({ path: file.path, startLine: null, endLine: null });
}

/** Any click outside the expanded group folds it back to the single button. */
function onDocumentPointerDown(event: PointerEvent) {
  const target = event.target;
  if (target instanceof Element && target.closest(".fcs-open-group--expanded"))
    return;
  openGroupPath.value = "";
}

watch(openGroupPath, (path) => {
  if (path) document.addEventListener("pointerdown", onDocumentPointerDown, true);
  else document.removeEventListener("pointerdown", onDocumentPointerDown, true);
});

onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", onDocumentPointerDown, true);
});

function canUndo(file: FileChangeSummaryFile): boolean {
  if (props.sourceUnavailable || !hasSource.value) return false;
  if (file.kind === "remove") {
    // Recycle-bin restore does not depend on a baseline backup.
    return file.runtime === "local" && file.restore_available === true && !statusPending.value;
  }
  return (
    file.runtime === "local" &&
    !!file.backup_id &&
    !!file.sha256 &&
    !isReverted(file) &&
    !statusPending.value
  );
}

function toggleRow(file: FileChangeSummaryFile) {
  if (!canExpand(file)) return;
  if (expanded.has(file.path)) {
    expanded.delete(file.path);
    return;
  }
  expanded.add(file.path);
  if (!diffs[file.path]) void loadDiff(file);
}

async function loadDiff(file: FileChangeSummaryFile) {
  loadingPath.value = file.path;
  try {
    const resp = await chatApi.fileChangeDiff(
      file.path,
      file.backup_id,
      file.sha256,
      source.value,
    );
    assertCurrentAccount(accountRevision);
    const envelope = resp.data;
    if (envelope?.status === "error") {
      toast.error(
        tm("fileChanges.diffLoadFailed", {
          message: envelope.message || "",
        }),
      );
    } else if (envelope?.data) {
      diffs[file.path] = {
        diff: envelope.data.diff || "",
        truncated: !!envelope.data.truncated,
      };
      baseContents[file.path] =
        typeof envelope.data.base_content === "string"
          ? envelope.data.base_content
          : undefined;
      overlayModes[file.path] = false;
    }
  } catch (error) {
    console.error("file diff request failed:", error);
  } finally {
    loadingPath.value = "";
  }
}

async function undoFile(file: FileChangeSummaryFile) {
  // Two-click inline confirmation: first click arms, second click fires.
  if (confirmingPath.value !== file.path) {
    confirmingPath.value = file.path;
    return;
  }
  confirmingPath.value = "";
  restoringPath.value = file.path;
  try {
    let envelope: any;
    if (file.kind === "remove") {
      const resp = await chatApi.restoreRemovedFileChange(file.path, source.value);
      envelope = resp.data;
    } else {
      const resp = await chatApi.restoreFileChange(
        file.path,
        file.backup_id,
        file.sha256,
        source.value,
      );
      envelope = resp.data;
    }
    assertCurrentAccount(accountRevision);
    if (envelope?.status === "error" || envelope?.data?.success === false) {
      const message =
        envelope?.data?.stderr || envelope?.message || envelope?.data?.reason || "";
      toast.error(tm("fileChanges.undoFailed", { message }));
    } else {
      toast.success(
        tm("fileChanges.undoDone", { name: basename(file.path) }),
      );
      // File reverted: collapse the row, drop the cached diff and badge it.
      markReverted(file.path);
    }
  } catch (error) {
    console.error("undo request failed:", error);
  } finally {
    restoringPath.value = "";
  }
}

function markReverted(path: string) {
  delete diffs[path];
  delete baseContents[path];
  delete overlayModes[path];
  expanded.delete(path);
  reverted.add(path);
}

/** Derive the reverted badge from backend truth: a local file whose
 * current bytes equal its baseline backup is reverted, however that
 * happened (our undo, the LLM's rollback tool, or a manual revert). */
async function refreshRevertStatus() {
  if (props.sourceUnavailable || !hasSource.value) return;
  const candidates = props.files.filter(
    (f) => f.runtime === "local" && f.backup_id && !reverted.has(f.path),
  );
  if (!candidates.length) return;
  statusPending.value = true;
  try {
    const resp = await chatApi.fileChangeStatus(
      candidates.map((f) => ({ path: f.path, backup_id: f.backup_id })),
      source.value,
    );
    assertCurrentAccount(accountRevision);
    const envelope = resp.data;
    if (envelope?.status === "ok" && Array.isArray(envelope.data?.files)) {
      for (const item of envelope.data.files) {
        if (item?.reverted && typeof item.path === "string") {
          markReverted(item.path);
        }
      }
    }
  } catch (error) {
    console.error("file change status request failed:", error);
  } finally {
    statusPending.value = false;
  }
}

watch(
  () => [props.files, props.messageId, props.runId],
  () => {
    if (props.files.length) void refreshRevertStatus();
  },
  { immediate: true },
);
</script>

<style scoped>
/* Visual contract: content cards (code blocks, file previews) stay neutral
   gray; this is an ACTION card, so it carries a primary-color tint to pull
   the eye. All colors derive from the theme, so dark mode adapts for free. */
.fcs-card {
  margin-top: 8px;
  border: 1px solid rgba(var(--v-theme-primary), 0.3);
  border-radius: 10px;
  background: rgba(var(--v-theme-primary), 0.045);
  /* `clip` (not `hidden`) keeps this card from becoming a scroll container,
     so the diff overlay's sticky collapse FAB resolves against the chat
     viewport instead of being trapped at the card's bottom. Same pattern as
     SubAgentRunBlock. */
  overflow: clip;
  font-size: 13px;
}

.fcs-card--dark {
  border-color: rgba(var(--v-theme-primary), 0.38);
  background: rgba(var(--v-theme-primary), 0.07);
}

/* The header doubles as the collapse toggle (2026-09-17): a full-width
   <button>, so the whole "N files changed +a −b" strip is the hit target.
   Button resets keep the row looking exactly as it did as a <div>. */
.fcs-head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 12px;
  border: 0;
  border-bottom: 1px solid rgba(var(--v-theme-primary), 0.14);
  background: rgba(var(--v-theme-primary), 0.08);
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background 0.12s ease;
}

.fcs-head:hover {
  background: rgba(var(--v-theme-primary), 0.15);
}

.fcs-head:focus-visible {
  outline: 2px solid rgba(var(--v-theme-primary), 0.55);
  outline-offset: -2px;
}

.fcs-head-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  background: rgba(var(--v-theme-primary), 0.16);
  color: rgb(var(--v-theme-primary));
  flex: none;
}

.fcs-title {
  font-weight: 600;
  font-size: 12.5px;
}

.fcs-total {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: monospace;
  font-size: 12px;
  font-weight: 600;
}

/* Collapse affordance. The header itself is the button, so the chevron is
   decoration: no hit area of its own, tinted with the card's accent. */
.fcs-head-chevron {
  flex: none;
  color: rgba(var(--v-theme-primary), 0.75);
}

/* Collapsed card: nothing follows the header, so its divider goes too. */
.fcs-card--collapsed .fcs-head {
  border-bottom: none;
}

.stat-adds {
  color: rgb(var(--v-theme-success));
}

.stat-dels {
  color: rgb(var(--v-theme-error));
}

.fcs-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px 3px 12px;
  transition: background 0.12s ease;
}

.fcs-row + .fcs-row,
.fcs-row + .fcs-body,
.fcs-body + .fcs-row {
  border-top: 1px solid rgba(var(--v-theme-primary), 0.12);
}

.fcs-row:hover {
  background: rgba(var(--v-theme-on-surface), 0.04);
}

.fcs-file {
  display: flex;
  align-items: center;
  gap: 7px;
  flex: 1;
  min-width: 0;
  background: none;
  border: none;
  padding: 3px 0;
  cursor: pointer;
  color: inherit;
  text-align: left;
}

.fcs-file:disabled {
  cursor: default;
}

.fcs-kind {
  flex: none;
}

/* Git-status color coding: modified amber, added green, reverted blue. */
.fcs-kind--edit,
.fcs-kind--write {
  color: rgb(var(--v-theme-warning));
}

.fcs-kind--created {
  color: rgb(var(--v-theme-success));
}

.fcs-kind--remove {
  color: rgb(var(--v-theme-error));
}

.fcs-kind--rollback {
  color: rgb(var(--v-theme-info));
}

.fcs-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12.5px;
}

.fcs-file:not(:disabled):hover .fcs-name {
  color: rgb(var(--v-theme-primary));
}

.stat-adds,
.stat-dels,
.fcs-file .fcs-reverted-badge {
  font-family: inherit;
}

.fcs-file > .stat-adds,
.fcs-file > .stat-dels {
  font-family: monospace;
  font-size: 11.5px;
  font-weight: 600;
  flex: none;
}

.fcs-reverted-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  font-weight: 500;
  line-height: 1;
  color: rgb(var(--v-theme-success));
  background: rgba(var(--v-theme-success), 0.12);
  border-radius: 999px;
  padding: 3px 8px;
  flex: none;
}

.fcs-muted {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 11.5px;
}

.fcs-chevron {
  margin-left: auto;
  color: rgba(var(--v-theme-on-surface), 0.45);
  transition: transform 0.15s ease;
  flex: none;
}

.fcs-chevron.expanded {
  transform: rotate(90deg);
}

.fcs-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex: none;
  opacity: 0;
  transition: opacity 0.12s ease;
}

/* One "Open" affordance at rest; clicking swaps it for the concrete
   targets (disk / sidebar / folder) in the same row slot. */
.fcs-open-group {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.fcs-open-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0;
  height: 24px;
  padding: 0 4px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.62);
  font: inherit;
  line-height: 1;
  cursor: pointer;
  transition:
    background 0.12s ease,
    color 0.12s ease;
}

.fcs-open-toggle:hover {
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
}

.fcs-open-toggle:focus-visible {
  outline: 2px solid rgba(var(--v-theme-primary), 0.55);
  outline-offset: -2px;
}

.fcs-row:hover .fcs-actions,
.fcs-row:focus-within .fcs-actions {
  opacity: 1;
}

/* Touch devices have no hover: keep the actions always visible. */
@media (hover: none) {
  .fcs-actions {
    opacity: 1;
  }
}

.fcs-row--reverted .fcs-name {
  text-decoration: line-through;
  color: rgba(var(--v-theme-on-surface), 0.45);
}

.fcs-row--reverted .fcs-actions {
  opacity: 1;
}

.fcs-body {
  padding: 6px 12px 10px;
  background: rgba(var(--v-theme-on-surface), 0.02);
}

.fcs-loading {
  padding: 6px 0;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.fcs-truncated {
  padding: 2px 0 6px;
  font-size: 11.5px;
}
</style>
