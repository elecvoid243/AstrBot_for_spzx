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
  (POST /chat/file-changes/diff), open on disk (existing
  useOpenOnDisk composable), two-click inline undo
  (POST /chat/file-changes/restore).

  Author: elecvoid243 | 2026-09-13
-->
<template>
  <div
    class="file-change-summary"
    :class="{ 'file-change-summary--dark': isDark }"
  >
    <div class="file-change-summary-head">
      <v-icon size="14">mdi-file-edit-outline</v-icon>
      <span class="file-change-summary-title">
        {{ tm("fileChanges.title", { count: files.length }) }}
      </span>
      <span v-if="totalAdds !== null" class="file-change-summary-total">
        <span class="stat-adds">+{{ totalAdds }}</span>
        <span class="stat-dels">−{{ totalDels }}</span>
      </span>
    </div>

    <div
      v-for="file in files"
      :key="file.path"
      class="file-change-summary-row"
    >
      <button
        type="button"
        class="file-change-summary-file"
        :disabled="!file.diff_available"
        :title="file.diff_available ? tm('fileChanges.viewDiff') : undefined"
        @click="toggleRow(file)"
      >
        <v-icon size="14">{{ kindIcon(file.kind) }}</v-icon>
        <span class="file-change-summary-name" :title="file.path">
          {{ basename(file.path) }}
        </span>
        <template v-if="file.diff_available && file.adds !== null">
          <span class="stat-adds">+{{ file.adds }}</span>
          <span class="stat-dels">−{{ file.dels }}</span>
        </template>
        <span v-else class="file-change-summary-muted">
          {{
            file.runtime === "sandbox"
              ? tm("fileChanges.sandboxHint")
              : tm("fileChanges.noStat")
          }}
        </span>
        <v-icon
          v-if="file.diff_available"
          size="16"
          class="file-change-summary-chevron"
          :class="{ expanded: expanded.has(file.path) }"
        >
          mdi-chevron-right
        </v-icon>
      </button>

      <div class="file-change-summary-actions">
        <v-btn
          v-if="file.runtime === 'local'"
          icon="mdi-open-in-new"
          size="x-small"
          variant="text"
          :title="tm('fileChange.openOnDisk')"
          @click.stop="openOnDisk(file.path, basename(file.path))"
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

    <template v-for="file in files" :key="`body-${file.path}`">
      <div v-if="expanded.has(file.path)" class="file-change-summary-body">
        <div
          v-if="loadingPath === file.path"
          class="file-change-summary-loading"
        >
          <v-progress-circular indeterminate size="14" width="2" />
        </div>
        <template v-else-if="diffs[file.path]">
          <div
            v-if="diffs[file.path].truncated"
            class="file-change-summary-muted file-change-summary-truncated"
          >
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
import { computed, reactive, ref } from "vue";
import DiffPreview from "./DiffPreview.vue";
import { chatApi } from "@/api/v1";
import { useModuleI18n } from "@/i18n/composables";
import { useOpenOnDisk } from "@/composables/useOpenOnDisk";
import { useToast } from "@/utils/toast";
import type { FileChangeSummaryFile } from "@/utils/fileChangeTool";

const props = defineProps<{
  files: FileChangeSummaryFile[];
  isDark?: boolean;
}>();

const { tm } = useModuleI18n("features/chat");
const { openOnDisk } = useOpenOnDisk("fileChange");
const toast = useToast();

const expanded = reactive(new Set<string>());
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

function basename(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  return normalized.slice(normalized.lastIndexOf("/") + 1) || path;
}

function kindIcon(kind: string): string {
  if (kind === "created") return "mdi-file-plus-outline";
  if (kind === "rollback") return "mdi-restore";
  return "mdi-file-edit-outline";
}

function canUndo(file: FileChangeSummaryFile): boolean {
  return file.runtime === "local" && !!file.backup_id;
}

function toggleRow(file: FileChangeSummaryFile) {
  if (!file.diff_available) return;
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
    const resp = await chatApi.restoreFileChange(
      file.path,
      file.backup_id,
      file.sha256,
    );
    const envelope = resp.data;
    if (envelope?.status === "error") {
      toast.error(
        tm("fileChanges.undoFailed", { message: envelope.message || "" }),
      );
    } else {
      toast.success(
        tm("fileChanges.undoDone", { name: basename(file.path) }),
      );
      // File reverted: invalidate the shown diff and collapse the row.
      delete diffs[file.path];
      expanded.delete(file.path);
    }
  } catch (error) {
    console.error("undo request failed:", error);
  } finally {
    restoringPath.value = "";
  }
}
</script>

<style scoped>
.file-change-summary {
  margin-top: 6px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 8px;
  overflow: hidden;
  font-size: 13px;
}

.file-change-summary--dark {
  border-color: rgba(var(--v-theme-on-surface), 0.2);
}

.file-change-summary-head,
.file-change-summary-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
}

.file-change-summary-head {
  background: rgba(var(--v-theme-on-surface), 0.04);
}

.file-change-summary-title {
  font-weight: 500;
}

.file-change-summary-total,
.file-change-summary-file .stat-adds,
.file-change-summary-file .stat-dels {
  font-family: monospace;
  font-size: 12px;
}

.stat-adds {
  color: #2e7d32;
  margin-right: 4px;
}

.stat-dels {
  color: #c62828;
}

.file-change-summary-file {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
  background: none;
  border: none;
  padding: 2px 0;
  cursor: pointer;
  color: inherit;
  text-align: left;
}

.file-change-summary-file:disabled {
  cursor: default;
}

.file-change-summary-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-change-summary-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.file-change-summary-muted {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 12px;
}

.file-change-summary-chevron {
  margin-left: auto;
  transition: transform 0.15s;
}

.file-change-summary-chevron.expanded {
  transform: rotate(90deg);
}

.file-change-summary-body {
  padding: 0 10px 8px;
}

.file-change-summary-loading,
.file-change-summary-truncated {
  padding: 4px 0;
}
</style>
