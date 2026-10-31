<!-- Author: elecvoid243, 2026-07-22
     Spec: docs/superpowers/specs/2026-07-22-binary-preview-design.md §6.3
     PDF rendering via pdfjs-dist 4.x. Decodes the Blob once when it
     arrives (or when the blob identity changes) and re-uses the
     PDFDocumentProxy across page/zoom changes. Worker is bundled via
     Vite's `?url` import so no extra asset pipeline is required. -->
<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import * as pdfjsLib from "pdfjs-dist";
// Bundled worker keeps the preview offline-friendly; Vite emits the
// file at build time and gives us a stable URL.
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type {
  PDFDocumentProxy,
  PDFPageProxy,
  PDFDocumentLoadingTask,
  RenderTask,
} from "pdfjs-dist/types/src/display/api";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerSrc;

const props = defineProps<{ blob: Blob }>();
const { tm } = useModuleI18n("features/chat");

const canvasRef = ref<HTMLCanvasElement | null>(null);
const currentPage = ref<number>(1);
const totalPages = ref<number>(0);
const scale = ref<number>(1.2);
const error = ref<string | null>(null);
const loading = ref<boolean>(false);

let doc: PDFDocumentProxy | null = null;
let loadToken = 0;
let loadingTask: PDFDocumentLoadingTask | null = null;
let renderTask: RenderTask | null = null;
let renderToken = 0;

async function loadDocument(): Promise<void> {
  const token = ++loadToken;
  // Tear down the previous document so we don't leak GPU buffers.
  renderToken++;
  renderTask?.cancel();
  renderTask = null;
  void loadingTask?.destroy();
  loadingTask = null;
  doc = null;
  if (!props.blob || props.blob.size === 0) {
    error.value = "empty_pdf";
    return;
  }
  loading.value = true;
  error.value = null;
  try {
    const buf = await props.blob.arrayBuffer();
    if (token !== loadToken) return;
    const task = pdfjsLib.getDocument({ data: buf });
    loadingTask = task;
    const loaded = await task.promise;
    if (token !== loadToken) {
      void loaded.destroy();
      return;
    }
    doc = loaded;
    totalPages.value = doc.numPages;
    currentPage.value = 1;
    await renderPage();
  } catch (e) {
    if (token !== loadToken) return;
    error.value = (e as Error)?.message ?? "parse_failed";
  } finally {
    if (token === loadToken) loading.value = false;
  }
}

async function renderPage(): Promise<void> {
  const token = ++renderToken;
  renderTask?.cancel();
  renderTask = null;
  const currentDoc = doc;
  if (!currentDoc || !canvasRef.value) return;
  let page: PDFPageProxy | null = null;
  try {
    page = await currentDoc.getPage(currentPage.value);
    if (token !== renderToken || currentDoc !== doc || !canvasRef.value) return;
    const viewport = page.getViewport({ scale: scale.value });
    const canvas = canvasRef.value;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    const task = page.render({ canvasContext: ctx, viewport });
    renderTask = task;
    await task.promise;
  } catch (e) {
    if (
      token === renderToken &&
      currentDoc === doc &&
      (e as Error)?.name !== "RenderingCancelledException"
    ) {
      error.value = (e as Error)?.message ?? "parse_failed";
    }
  } finally {
    page?.cleanup();
    if (token === renderToken) renderTask = null;
  }
}

function prevPage(): void {
  if (currentPage.value > 1) {
    currentPage.value -= 1;
    void renderPage();
  }
}
function nextPage(): void {
  if (currentPage.value < totalPages.value) {
    currentPage.value += 1;
    void renderPage();
  }
}
function zoomIn(): void {
  scale.value = Math.min(scale.value + 0.2, 3.0);
  void renderPage();
}
function zoomOut(): void {
  scale.value = Math.max(scale.value - 0.2, 0.5);
  void renderPage();
}

watch(
  () => props.blob,
  () => {
    void loadDocument();
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  loadToken++;
  renderToken++;
  renderTask?.cancel();
  void loadingTask?.destroy();
  loadingTask = null;
  doc = null;
});
</script>

<template>
  <div class="pdf-preview">
    <div v-if="error" class="pdf-preview__error">
      <v-icon icon="mdi-alert-circle-outline" />
      <span>{{
        tm(`spcodeProjectLoad.documentManager.binary.errors.${error}`, {
          reason: error,
        })
      }}</span>
    </div>
    <template v-else>
      <div class="pdf-preview__toolbar">
        <v-btn
          size="small"
          variant="text"
          :disabled="currentPage <= 1"
          :aria-label="tm('spcodeProjectLoad.documentManager.binary.pdf.prev')"
          @click="prevPage"
        >
          <v-icon icon="mdi-chevron-left" />
        </v-btn>
        <span class="pdf-preview__page">
          {{
            tm("spcodeProjectLoad.documentManager.binary.pdf.pageLabel", {
              current: currentPage,
              total: totalPages,
            })
          }}
        </span>
        <v-btn
          size="small"
          variant="text"
          :disabled="currentPage >= totalPages"
          :aria-label="tm('spcodeProjectLoad.documentManager.binary.pdf.next')"
          @click="nextPage"
        >
          <v-icon icon="mdi-chevron-right" />
        </v-btn>
        <span class="pdf-preview__spacer" />
        <v-btn
          size="small"
          variant="text"
          :aria-label="
            tm('spcodeProjectLoad.documentManager.binary.pdf.zoomOut')
          "
          @click="zoomOut"
        >
          <v-icon icon="mdi-magnify-minus-outline" />
        </v-btn>
        <span class="pdf-preview__zoom">{{ Math.round(scale * 100) }}%</span>
        <v-btn
          size="small"
          variant="text"
          :aria-label="
            tm('spcodeProjectLoad.documentManager.binary.pdf.zoomIn')
          "
          @click="zoomIn"
        >
          <v-icon icon="mdi-magnify-plus-outline" />
        </v-btn>
      </div>
      <div v-if="loading && totalPages === 0" class="pdf-preview__placeholder">
        <v-progress-circular indeterminate size="20" />
      </div>
      <div class="pdf-preview__canvas-wrap">
        <canvas ref="canvasRef" class="pdf-preview__canvas" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.pdf-preview {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 0;
  background: rgb(var(--v-theme-surface));
}
.pdf-preview__toolbar {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  font-size: 12px;
}
.pdf-preview__page {
  font-variant-numeric: tabular-nums;
  min-width: 60px;
  text-align: center;
}
.pdf-preview__spacer {
  flex: 1;
}
.pdf-preview__zoom {
  font-variant-numeric: tabular-nums;
  min-width: 44px;
  text-align: center;
  color: rgba(var(--v-theme-on-surface), 0.7);
}
.pdf-preview__canvas-wrap {
  flex: 1;
  min-height: 0;
  overflow: auto;
  display: flex;
  justify-content: center;
  padding: 12px;
  background: rgba(var(--v-theme-on-surface), 0.04);
}
.pdf-preview__canvas {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
  background: white;
  max-width: 100%;
}
.pdf-preview__placeholder {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}
.pdf-preview__error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  color: rgb(var(--v-theme-error));
  font-size: 13px;
}
</style>
