<!--
  FileChangeCard
  ─────────────────────────────────────────────────────────────────────
  Per-file change card for the 2026-08-11 file-change visibility
  feature. Rendered in the ReasoningTimeline's pinned "file changes"
  section so users can see which files the agent edited/wrote without
  expanding the reasoning view.

  States:
    collapsed (default) → one-line summary: icon + basename + status
                          + diffstat (edit) / line count (write)
    expanded + running  → spinner + "editing…" note
    expanded + error    → raw result text in error styling
    expanded + edit     → DiffPreview (same props as ToolCallCard)
    expanded + write    → args.content code block, Shiki-highlighted
                          when the language is known (plain <pre>
                          fallback until the highlighter is ready)

  Author: elecvoid243 | 2026-08-11
-->
<template>
  <div
    class="file-change-card"
    :class="[
      `file-change-card--${tone}`,
      { expanded: isExpanded, error: entry.status === 'error' },
    ]"
    :data-call-id="entry.callId"
  >
    <div class="file-change-card-top">
      <button
        type="button"
        class="file-change-card-header"
        @click="isExpanded = !isExpanded"
      >
        <v-icon size="14" class="file-change-icon">{{ kindIcon }}</v-icon>
        <span class="file-change-name" :title="entry.filePath">{{ basename }}</span>
        <v-progress-circular
          v-if="entry.status === 'running'"
          indeterminate
          size="12"
          width="2"
          class="file-change-status"
        />
        <v-icon
          v-else-if="entry.status === 'error'"
          size="14"
          class="file-change-status file-change-status--error"
        >
          mdi-alert-circle-outline
        </v-icon>
        <span v-if="hasStat" class="file-change-stat">
          <template v-if="entry.kind === 'edit' && entry.diffStat">
            <span class="stat-adds">+{{ entry.diffStat.adds }}</span>
            <span class="stat-dels">−{{ entry.diffStat.dels }}</span>
          </template>
          <template v-else>{{ lineCountText }}</template>
        </span>
        <v-icon
          size="18"
          class="file-change-chevron"
          :class="{ expanded: isExpanded }"
        >
          mdi-chevron-right
        </v-icon>
      </button>
      <!-- 2026-08-14 open-on-disk: open the changed file on the AstrBot
           host with the OS default application. Hidden for removals
           (file is gone) and while the tool call is still running. -->
      <button
        v-if="canOpenOnDisk"
        type="button"
        class="file-change-open-btn"
        :disabled="opening"
        :title="tm('fileChange.openOnDisk')"
        @click.stop="openOnDisk(openPath, basename)"
      >
        <v-icon size="14">mdi-open-in-new</v-icon>
      </button>
      <!-- 2026-08-21 open-folder: reveal the file's containing folder in
           the host file manager. Also shown for removals — the folder
           survives the delete — but not while the call is running. -->
      <button
        v-if="canOpenFolder"
        type="button"
        class="file-change-open-btn file-change-folder-btn"
        :disabled="opening"
        :title="tm('fileChange.openFolder')"
        @click.stop="openFolder(openPath, basename)"
      >
        <v-icon size="14">mdi-folder-open-outline</v-icon>
      </button>
    </div>

    <div v-if="isExpanded" class="file-change-body">
      <!-- running -->
      <div v-if="entry.status === 'running'" class="file-change-running">
        <v-progress-circular indeterminate size="14" width="2" />
        <span>{{ tm("fileChange.editing") }}</span>
      </div>

      <!-- error -->
      <pre v-else-if="entry.status === 'error'" class="file-change-error">{{ errorText }}</pre>

      <!-- edit: standard diff preview -->
      <DiffPreview
        v-else-if="entry.kind === 'edit'"
        :content="editParsed.diff"
        :file-path="editParsed.filePath || entry.filePath"
        :summary="editParsed.summary"
        :is-dark="isDark"
        :max-lines="25"
        :collapsible="false"
      />

      <!-- remove: no content to show — just the outcome text -->
      <pre v-else-if="entry.kind === 'remove'" class="file-change-remove-note">{{ toolResult || entry.filePath }}</pre>

      <!-- write: the content passed to the write tool -->
      <CopyableText
        v-else
        :value="writeContent"
        mode="block"
        :multiline="true"
        bare
      >
        <div
          v-if="shikiReady && writeLanguage !== 'text'"
          class="write-content write-content-shiki"
          v-html="highlightedWriteContent"
        ></div>
        <pre v-else class="write-content">{{ writeContent }}</pre>
      </CopyableText>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import {
  ensureShikiLanguages,
  escapeHtml,
  renderShikiCode,
} from "@/utils/shiki";
import { detectLanguage } from "@/utils/codeLanguage";
import {
  fileBasename,
  fileChangeTone,
  normalizeToolArgs,
  parseFileEditResult,
  stripWriteEncodingMarker,
  type FileChangeEntry,
} from "@/utils/fileChangeTool";
import { useOpenOnDisk } from "@/composables/useOpenOnDisk";
import DiffPreview from "./DiffPreview.vue";
import CopyableText from "./__shared__/CopyableText.vue";

const props = defineProps<{
  entry: FileChangeEntry;
  isDark?: boolean;
}>();

const { tm } = useModuleI18n("features/chat");

const isExpanded = ref(false);

// ── 2026-08-14 open-on-disk / 2026-08-21 open-folder ──────────────

const { opening, openOnDisk, openFolder } = useOpenOnDisk("fileChange");

/**
 * 2026-09-09: path handed to the open-on-disk / open-folder APIs. Write
 * results carry a display-only ` (encoding: xxx)` marker that the file on
 * disk never has; the card keeps showing it (basename / title) but the
 * API must receive the clean path.
 */
const openPath = computed(() =>
  stripWriteEncodingMarker(props.entry.filePath),
);

/** Removals delete the file and a running call may not have flushed
 *  yet, so the open action only makes sense for finished edit/write
 *  entries with a resolved path. */
const canOpenOnDisk = computed(
  () =>
    Boolean(openPath.value) &&
    props.entry.kind !== "remove" &&
    props.entry.status !== "running",
);

/** The containing folder survives a removal, so it can be opened for
 *  every finished entry kind; only running calls are excluded. */
const canOpenFolder = computed(
  () => Boolean(openPath.value) && props.entry.status !== "running",
);

const kindIcon = computed(() => {
  if (props.entry.kind === "edit") return "mdi-file-document-edit-outline";
  if (props.entry.kind === "remove") return "mdi-delete-outline";
  return "mdi-content-save-outline";
});

// 2026-08-11: whole-card tint by change kind (write=green, edit=yellow,
// remove=red).
const tone = computed(() => fileChangeTone(props.entry));

const basename = computed(() => fileBasename(props.entry.filePath));

// Edit: "+2 −1" split-color spans from the diff stat; write: localized
// line count; remove: no stat.
const hasStat = computed(
  () =>
    (props.entry.kind === "edit" && props.entry.diffStat !== null) ||
    (props.entry.kind === "write" && props.entry.lineCount !== null),
);
const lineCountText = computed(() =>
  props.entry.lineCount !== null
    ? tm("fileChange.lines", { count: props.entry.lineCount })
    : "",
);

const toolResult = computed(() => {
  const r = props.entry.tool.result;
  return typeof r === "string" ? r : "";
});

const editParsed = computed(() => parseFileEditResult(toolResult.value));

const errorText = computed(() =>
  props.entry.kind === "edit"
    ? editParsed.value.cleanResult
    : toolResult.value,
);

// ── Write content + Shiki highlighting ────────────────────────────

const writeContent = computed(() =>
  String(normalizeToolArgs(props.entry.tool.args ?? props.entry.tool.arguments).content ?? ""),
);

// Language detection must use the clean path: the display marker
// (" (encoding: xxx)") breaks the trailing-extension match, which
// silently downgraded write cards to plain text.
const writeLanguage = computed(() => detectLanguage(openPath.value));

const shikiHighlighter = ref<any>(null);
const shikiReady = ref(false);

const highlightedWriteContent = computed(() => {
  if (!shikiReady.value || !shikiHighlighter.value) return "";
  const code = writeContent.value;
  if (!code) return "";
  try {
    return renderShikiCode(
      shikiHighlighter.value,
      code,
      writeLanguage.value,
      "auto",
    );
  } catch (err) {
    console.error("Failed to highlight write content with Shiki:", err);
    return `<pre><code>${escapeHtml(code)}</code></pre>`;
  }
});

onMounted(async () => {
  // Only load the highlighter for write cards with a known language.
  if (props.entry.kind !== "write" || writeLanguage.value === "text") return;
  try {
    shikiHighlighter.value = await ensureShikiLanguages();
    shikiReady.value = true;
  } catch (err) {
    console.error("Failed to initialize Shiki:", err);
  }
});
</script>

<style scoped>
.file-change-card {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: 6px;
  overflow: hidden;
  font-size: 12px;
  line-height: 1.55;
  background: rgba(var(--v-theme-on-surface), 0.02);
}

.file-change-card.error {
  border-color: rgba(207, 34, 46, 0.35);
}

/* 2026-08-14 open-on-disk: the header and the open button sit side by
   side in a flex row (buttons cannot nest inside the header button). */
.file-change-card-top {
  display: flex;
  align-items: stretch;
}

.file-change-card-top .file-change-card-header {
  flex: 1;
  min-width: 0;
}

.file-change-open-btn {
  display: flex;
  align-items: center;
  padding: 0 8px;
  border: 0;
  background: rgba(var(--v-theme-on-surface), 0.04);
  color: rgba(var(--v-theme-on-surface), 0.5);
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.15s ease;
}

.file-change-open-btn:hover {
  background: rgba(var(--v-theme-on-surface), 0.07);
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.file-change-open-btn:disabled {
  cursor: default;
  opacity: 0.5;
}

/* 2026-08-11: whole-card tint by change kind */
.file-change-card--green {
  background: rgba(45, 164, 78, 0.07);
  border-color: rgba(45, 164, 78, 0.35);
}

.file-change-card--green .file-change-card-header,
.file-change-card--green .file-change-open-btn {
  background: rgba(45, 164, 78, 0.12);
}

.file-change-card--green .file-change-card-header:hover,
.file-change-card--green .file-change-open-btn:hover {
  background: rgba(45, 164, 78, 0.18);
}

.file-change-card--yellow {
  background: rgba(191, 135, 0, 0.07);
  border-color: rgba(191, 135, 0, 0.35);
}

.file-change-card--yellow .file-change-card-header,
.file-change-card--yellow .file-change-open-btn {
  background: rgba(191, 135, 0, 0.12);
}

.file-change-card--yellow .file-change-card-header:hover,
.file-change-card--yellow .file-change-open-btn:hover {
  background: rgba(191, 135, 0, 0.18);
}

.file-change-card--red {
  background: rgba(207, 34, 46, 0.07);
  border-color: rgba(207, 34, 46, 0.35);
}

.file-change-card--red .file-change-card-header,
.file-change-card--red .file-change-open-btn {
  background: rgba(207, 34, 46, 0.12);
}

.file-change-card--red .file-change-card-header:hover,
.file-change-card--red .file-change-open-btn:hover {
  background: rgba(207, 34, 46, 0.18);
}

/* split-color diffstat */
.stat-adds {
  color: #2da44e;
  font-weight: 600;
}

.stat-dels {
  color: #cf222e;
  font-weight: 600;
  margin-left: 4px;
}

.file-change-card-header {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 4px 8px;
  border: 0;
  background: rgba(var(--v-theme-on-surface), 0.04);
  color: rgba(var(--v-theme-on-surface), 0.75);
  font: inherit;
  cursor: pointer;
  user-select: none;
  text-align: left;
}

.file-change-card-header:hover {
  background: rgba(var(--v-theme-on-surface), 0.07);
}

.file-change-icon {
  color: rgba(var(--v-theme-on-surface), 0.5);
  flex-shrink: 0;
}

.file-change-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11.5px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.file-change-status {
  flex-shrink: 0;
}

.file-change-status--error {
  color: #cf222e;
}

.file-change-stat {
  margin-left: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 10.5px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  flex-shrink: 0;
}

.file-change-chevron {
  margin-left: auto;
  color: rgba(var(--v-theme-on-surface), 0.4);
  transition: transform 0.2s ease;
  flex-shrink: 0;
}

/* When a stat is present it owns the auto margin; keep the chevron flush. */
.file-change-stat + .file-change-chevron {
  margin-left: 0;
}

.file-change-chevron.expanded {
  transform: rotate(90deg);
}

.file-change-body {
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.06);
  padding: 6px 8px;
}

.file-change-running {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
  font-size: 11.5px;
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.file-change-error {
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11.5px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
  color: #cf222e;
}

.file-change-remove-note {
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11.5px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-all;
  color: rgba(var(--v-theme-on-surface), 0.65);
}

.write-content {
  margin: 0;
  padding: 8px 10px;
  border-radius: 4px;
  background: rgba(var(--v-theme-on-surface), 0.04);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11.5px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 300px;
  overflow-y: auto;
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.write-content-shiki {
  padding: 0;
  border-radius: 6px;
  overflow: hidden;
  background: transparent;
}

.write-content-shiki :deep(pre.shiki) {
  margin: 0;
  padding: 10px 12px;
  border-radius: 6px;
  overflow: auto;
  max-height: 300px;
  font-size: 11.5px;
  line-height: 1.55;
  tab-size: 4;
}

.write-content-shiki :deep(pre.shiki code) {
  display: block;
  padding: 0;
  background: transparent;
  font-family: inherit;
  font-size: inherit;
  line-height: inherit;
}
</style>
