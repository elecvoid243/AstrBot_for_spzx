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
  (POST /chat/file-changes/diff), open on disk or reveal the
  containing folder (existing useOpenOnDisk composable), two-click
  inline undo (POST /chat/file-changes/restore). A reverted row
  collapses, shows a "changes reverted" badge and disables further
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
    class="fcs-card"
    :class="{ 'fcs-card--dark': isDark, 'fcs-card--collapsed': collapsed }"
  >
    <button
      type="button"
      class="fcs-head"
      :title="toggleLabel"
      :aria-expanded="!collapsed"
      @click="collapsed = !collapsed"
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
          <v-btn
            v-if="canOpenFile(file)"
            icon="mdi-open-in-new"
            size="x-small"
            variant="text"
            :title="tm('fileChange.openOnDisk')"
            @click.stop="openOnDisk(file.path, basename(file.path))"
          />
          <v-btn
            v-if="file.runtime === 'local'"
            icon="mdi-folder-open-outline"
            size="x-small"
            variant="text"
            :title="tm('fileChange.openFolder')"
            @click.stop="openFolder(file.path, basename(file.path))"
          />
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
            :is-dark="isDark"
            :max-lines="400"
            :collapsible="false"
            :commentable="false"
          />
        </template>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import DiffPreview from "./DiffPreview.vue";
import { chatApi, pluginExtensionApi } from "@/api/v1";
import { useModuleI18n } from "@/i18n/composables";
import { useOpenOnDisk } from "@/composables/useOpenOnDisk";
import { useToast } from "@/utils/toast";
import type { FileChangeSummaryFile } from "@/utils/fileChangeTool";

const props = defineProps<{
  files: FileChangeSummaryFile[];
  isDark?: boolean;
}>();

const { tm } = useModuleI18n("features/chat");
const { openOnDisk, openFolder } = useOpenOnDisk("fileChange");
const toast = useToast();

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
  return file.diff_available && !isReverted(file);
}

/** Removals have no file to open (only the parent folder survives). */
function canOpenFile(file: FileChangeSummaryFile): boolean {
  return file.runtime === "local" && file.kind !== "remove";
}

function canUndo(file: FileChangeSummaryFile): boolean {
  if (file.kind === "remove") {
    // Recycle-bin restore does not depend on a baseline backup.
    return file.runtime === "local" && !statusPending.value;
  }
  return (
    file.runtime === "local" &&
    !!file.backup_id &&
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
    );
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
      // Removals are undone via the spcode plugin's recycle-bin restore.
      const resp = await pluginExtensionApi.post("spcode/file-remove/restore", {
        path: file.path,
      });
      envelope = resp.data;
    } else {
      const resp = await chatApi.restoreFileChange(
        file.path,
        file.backup_id,
        file.sha256,
      );
      envelope = resp.data;
    }
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
  expanded.delete(path);
  reverted.add(path);
}

/** Derive the reverted badge from backend truth: a local file whose
 * current bytes equal its baseline backup is reverted, however that
 * happened (our undo, the LLM's rollback tool, or a manual revert). */
async function refreshRevertStatus() {
  const candidates = props.files.filter(
    (f) => f.runtime === "local" && f.backup_id && !reverted.has(f.path),
  );
  if (!candidates.length) return;
  statusPending.value = true;
  try {
    const resp = await chatApi.fileChangeStatus(
      candidates.map((f) => ({ path: f.path, backup_id: f.backup_id })),
    );
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
  () => props.files,
  (files) => {
    if (files.length) void refreshRevertStatus();
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
  overflow: hidden;
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
