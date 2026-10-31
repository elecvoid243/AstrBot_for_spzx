<!-- Author: elecvoid243, 2026-07-22
     Spec: docs/superpowers/specs/2026-07-22-binary-preview-design.md §6.3
     DOCX rendering via mammoth.browser, with the shared DOMPurify
     dependency sanitizing generated HTML before displaying host documents. -->
<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
// mammoth.browser is the pre-bundled UMD entry; using it avoids
// pulling in the Node-specific `argparse`/`path-is-absolute` deps.
import * as mammoth from "mammoth/mammoth.browser";
import DOMPurify from "dompurify";

const props = defineProps<{ blob: Blob }>();
const { tm } = useModuleI18n("features/chat");

const html = ref<string>("");
const error = ref<string | null>(null);
const loading = ref<boolean>(false);

let loadToken = 0;

async function loadDocument(): Promise<void> {
  const token = ++loadToken;
  if (!props.blob) {
    error.value = "decode_failed";
    return;
  }
  loading.value = true;
  error.value = null;
  html.value = "";
  try {
    const buf = await props.blob.arrayBuffer();
    if (token !== loadToken) return;
    const result = await mammoth.convertToHtml({ arrayBuffer: buf });
    if (token !== loadToken) return;
    html.value = DOMPurify.sanitize(result.value);
  } catch (e) {
    if (token !== loadToken) return;
    error.value = (e as Error)?.message ?? "decode_failed";
  } finally {
    if (token === loadToken) loading.value = false;
  }
}

watch(
  () => props.blob,
  () => {
    void loadDocument();
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  // Invalidate any in-flight convertToHtml() so a late resolution
  // does not write to refs after unmount.
  loadToken++;
});
</script>

<template>
  <div class="docx-preview">
    <div
      v-if="error"
      class="docx-preview__placeholder docx-preview__placeholder--error"
    >
      <v-icon icon="mdi-alert-circle-outline" />
      <span>{{
        tm(`spcodeProjectLoad.documentManager.binary.errors.${error}`, {
          reason: error,
        })
      }}</span>
    </div>
    <div v-else-if="loading && !html" class="docx-preview__placeholder">
      <v-progress-circular indeterminate size="20" />
      <span>{{ tm("spcodeProjectLoad.documentManager.raw.loading") }}</span>
    </div>
    <div v-else class="docx-preview__body" v-html="html" />
  </div>
</template>

<style scoped>
.docx-preview {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 0;
  background: rgb(var(--v-theme-surface));
}
.docx-preview__placeholder {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 13px;
}
.docx-preview__placeholder--error {
  color: rgb(var(--v-theme-error));
}
.docx-preview__body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 16px 24px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  line-height: 1.6;
  color: rgb(var(--v-theme-on-surface));
}
.docx-preview__body :deep(h1) {
  font-size: 1.6em;
  margin: 0.8em 0 0.4em;
}
.docx-preview__body :deep(h2) {
  font-size: 1.3em;
  margin: 0.7em 0 0.4em;
}
.docx-preview__body :deep(h3) {
  font-size: 1.1em;
  margin: 0.6em 0 0.4em;
}
.docx-preview__body :deep(p) {
  margin: 0.6em 0;
}
.docx-preview__body :deep(ul),
.docx-preview__body :deep(ol) {
  padding-left: 1.5em;
  margin: 0.6em 0;
}
.docx-preview__body :deep(table) {
  border-collapse: collapse;
  margin: 0.8em 0;
}
.docx-preview__body :deep(table td),
.docx-preview__body :deep(table th) {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.2);
  padding: 4px 8px;
}
.docx-preview__body :deep(a) {
  color: rgb(var(--v-theme-primary));
}
</style>
