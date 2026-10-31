<!-- Author: elecvoid243, 2026-07-22
     Spec: docs/superpowers/specs/2026-07-22-binary-preview-design.md §4
     Container component for inline preview of binary files
     (PDF/DOCX/XLSX/CSV/MD + images). Owns the useSpcodeFileBinary composable
     and dispatches to a kind-specific child previewer based on the
     decoded file extension.

     Behaviour summary:
     - Triggers fetchBinary() on mount and on (path|ref) change.
     - Renders a unified header (filename + size) above all previews.
     - Surfaces loading / error / not-found / too-large states with
       i18n-friendly placeholders; never silently shows nothing.
     - Hands the fetched Blob to the matching preview child, which
       owns its own library wiring (pdfjs/mammoth/xlsx/papaparse). -->
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import {
  useSpcodeFileBinary,
  type FileBinaryKind,
} from "@/composables/useSpcodeFileBinary";
import { useModuleI18n } from "@/i18n/composables";
import PdfPreview from "./PdfPreview.vue";
import DocxPreview from "./DocxPreview.vue";
import XlsxPreview from "./XlsxPreview.vue";
import CsvPreview from "./CsvPreview.vue";
import BinaryMarkdownPreview from "./BinaryMarkdownPreview.vue";
import ImagePreview from "./ImagePreview.vue";

const props = defineProps<{
  /** Absolute host path in host mode; otherwise a project-relative path. */
  path: string;
  host?: boolean;
  /** Git ref (sha/branch/tag). Empty string = current working tree.
   *  Named `gitRef` (not `ref`) so Vue does not mistake it for the
   *  built-in template-ref attribute. */
  gitRef: string;
  /** Active worktree name; null when on main checkout. */
  worktree: string | null;
}>();

const { tm } = useModuleI18n("features/chat");

const binary = useSpcodeFileBinary(
  computed(() => props.worktree),
  computed(() => !!props.host),
);
const fetchToken = ref(0); // bump on path/ref change to re-trigger fetch

watch(
  () => [props.path, props.gitRef, props.host, props.worktree] as const,
  ([p, r]) => {
    if (!p) return;
    binary.invalidateAll();
    fetchToken.value += 1;
    void binary.fetchBinary(p, r);
  },
  { immediate: true },
);

onBeforeUnmount(() => binary.dispose());

const state = computed(() => binary.getState(props.path, props.gitRef));
const data = computed(() => binary.getData(props.path, props.gitRef));

const previewKind = computed<FileBinaryKind | null>(
  () => data.value?.kind ?? null,
);

const sizeLabel = computed<string>(() => {
  const bytes = data.value?.size ?? 0;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
});
</script>

<template>
  <div class="binary-preview">
    <div v-if="data" class="binary-preview__header">
      <span class="binary-preview__filename">{{ data.filename }}</span>
      <span class="binary-preview__meta">
        {{ data.kind.toUpperCase() }} · {{ sizeLabel }}
      </span>
    </div>

    <div
      v-if="
        state.kind === 'loading' || (state.kind === 'idle' && fetchToken === 0)
      "
      class="binary-preview__placeholder"
    >
      <v-progress-circular indeterminate size="24" />
      <span>{{ tm("spcodeProjectLoad.documentManager.raw.loading") }}</span>
    </div>

    <div
      v-else-if="state.kind === 'error'"
      class="binary-preview__placeholder binary-preview__placeholder--error"
    >
      <v-icon icon="mdi-alert-circle-outline" />
      <span>
        {{
          tm(
            `spcodeProjectLoad.documentManager.binary.errors.${state.reason}`,
            { reason: state.reason },
          )
        }}
      </span>
    </div>

    <div v-else-if="data" class="binary-preview__body">
      <PdfPreview v-if="previewKind === 'pdf'" :blob="data.blob" />
      <DocxPreview v-else-if="previewKind === 'docx'" :blob="data.blob" />
      <XlsxPreview v-else-if="previewKind === 'xlsx'" :blob="data.blob" />
      <CsvPreview v-else-if="previewKind === 'csv'" :blob="data.blob" />
      <BinaryMarkdownPreview
        v-else-if="previewKind === 'md'"
        :blob="data.blob"
      />
      <ImagePreview
        v-else-if="previewKind === 'image'"
        :blob="data.blob"
        :filename="data.filename"
      />
    </div>
  </div>
</template>

<style scoped>
.binary-preview {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 0;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 6px;
  overflow: hidden;
  background: rgb(var(--v-theme-surface));
}
.binary-preview__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 12px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  font-size: 12px;
  background: rgba(var(--v-theme-on-surface), 0.03);
}
.binary-preview__filename {
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface));
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.binary-preview__meta {
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-variant-numeric: tabular-nums;
}
.binary-preview__body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: rgb(var(--v-theme-surface));
}
.binary-preview__placeholder {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 13px;
}
.binary-preview__placeholder--error {
  color: rgb(var(--v-theme-error));
}
</style>
