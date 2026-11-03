<!--
  Author: elecvoid243, 2026-08-15
  Spec: D:\AstrbotWorkSpace\spcode-目录选择器设计方案.md §4.2 / §4.3

  ProjectDirectoryBrowser — 应用内目录选择对话框（spcode "打开文件浏览器"）。

  浏览器只列目录，所选路径一律来自后端 file-browser / home-directory 端点
  返回的绝对路径，前端不拼接、不规范化。双击进入目录、单击选中；底部
  "选择此文件夹" 把 selectedPath ?? currentPath 通过 `select` 事件回传父级。

  交互细节约定（spec §4.3）：
    - 打开时 goHome() 定位宿主 home；失败保持空列表 + 错误提示
    - 盘根 / POSIX 根 "/" 时禁用"上级"按钮
    - 空目录显示"此文件夹为空"；truncated 显示截断提示
    - 后端 reason（path_not_found / permission_denied）映射为中文提示，
      不崩溃、可返回上级
-->
<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import type { SpcodeFileBrowserEntry } from "@/composables/parseSpcodeFileBrowser";
import { useSpcodeDirectoryBrowser } from "@/composables/useSpcodeDirectoryBrowser";

const props = defineProps<{
  modelValue: boolean;
  hostBrowsing?: boolean;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  /** User confirmed a directory. `path` is a backend-returned absolute path. */
  select: [path: string];
}>();

const { tm } = useModuleI18n("features/chat");

const browser = useSpcodeDirectoryBrowser(computed(() => !!props.hostBrowsing));
/** Currently clicked directory (null = the browsed folder itself is the pick). */
const selectedPath = ref<string | null>(null);
/** Manual jump input (absolute path, Enter or button triggers). */
const pathInput = ref("");
/** True when the jump input was not an absolute path. */
const pathInputError = ref(false);

/** Windows drive / UNC / POSIX-root absolute path detection. */
function isAbsolutePath(p: string): boolean {
  return /^([A-Za-z]:[\\/]|\\\\|\/)/.test(p);
}

/** Jump to a manually typed absolute path. */
function onJumpInput(): void {
  const trimmed = pathInput.value.trim();
  if (!trimmed) return;
  if (!isAbsolutePath(trimmed)) {
    pathInputError.value = true;
    return;
  }
  pathInputError.value = false;
  void browser.list(trimmed);
}

// Open → reset selection, jump to host home (start position). `immediate`
// makes the initial mount with modelValue=true load home right away (and is
// a no-op when the dialog is initially closed).
watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      selectedPath.value = null;
      void browser.goHome();
    } else browser.cancel();
  },
  { immediate: true },
);
onBeforeUnmount(browser.cancel);

// Navigating resets the transient selection (currentPath becomes the pick).
watch(
  () => browser.currentPath.value,
  () => {
    selectedPath.value = null;
  },
);

function close(): void {
  emit("update:modelValue", false);
}

function onSelectEntry(entry: SpcodeFileBrowserEntry): void {
  selectedPath.value = entry.path;
}

function onEnterEntry(entry: SpcodeFileBrowserEntry): void {
  void browser.openEntry(entry);
}

function onConfirm(): void {
  const pick = selectedPath.value ?? browser.currentPath.value;
  if (!pick) return;
  emit("select", pick);
  close();
}

const errorText = computed<string>(() => {
  switch (browser.error.value) {
    case "spcode_unavailable":
      return tm("spcodeProjectLoad.fileBrowser.reference.pluginUnavailable");
    case "path_not_found":
      return tm("spcodeProjectLoad.directoryBrowser.errorPathNotFound");
    case "permission_denied":
      return tm("spcodeProjectLoad.directoryBrowser.errorPermissionDenied");
    default:
      return tm("spcodeProjectLoad.directoryBrowser.errorGeneric");
  }
});
</script>

<template>
  <v-dialog
    :model-value="modelValue"
    width="900"
    max-width="calc(100vw - 32px)"
    height="80vh"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card class="directory-browser-card">
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ tm("spcodeProjectLoad.directoryBrowser.title") }}
      </v-card-title>
      <v-card-text class="directory-browser-body">
        <!-- Toolbar: home + up + breadcrumb -->
        <div class="d-flex align-center ga-2 mb-2">
          <v-btn
            icon="mdi-home-variant-outline"
            variant="text"
            size="small"
            data-testid="dir-home"
            :title="tm('spcodeProjectLoad.directoryBrowser.home')"
            :aria-label="tm('spcodeProjectLoad.directoryBrowser.home')"
            @click="browser.goHome"
          />
          <v-btn
            icon="mdi-arrow-up"
            variant="text"
            size="small"
            data-testid="dir-up"
            :disabled="!browser.canGoUp.value || browser.loading.value"
            :title="tm('spcodeProjectLoad.directoryBrowser.up')"
            :aria-label="tm('spcodeProjectLoad.directoryBrowser.up')"
            @click="browser.goUp"
          />
          <div class="breadcrumb d-flex align-center overflow-hidden">
            <template v-if="browser.computerMode.value">
              <span class="breadcrumb-seg breadcrumb-seg--static">
                {{ tm("spcodeProjectLoad.directoryBrowser.computer") }}
              </span>
            </template>
            <template v-else>
              <button
                type="button"
                class="breadcrumb-seg"
                data-testid="dir-computer"
                @click="browser.openComputer"
              >
                {{ tm("spcodeProjectLoad.directoryBrowser.computer") }}
              </button>
              <template
                v-for="(seg, i) in browser.breadcrumbs.value"
                :key="seg.path"
              >
                <v-icon icon="mdi-chevron-right" size="x-small" class="mx-1" />
                <button
                  type="button"
                  class="breadcrumb-seg"
                  @click="browser.openSegment(seg)"
                >
                  {{ seg.label }}
                </button>
              </template>
            </template>
            <span
              v-if="
                !browser.computerMode.value && !browser.breadcrumbs.value.length
              "
              class="text-caption text-medium-emphasis"
            >
              {{ tm("spcodeProjectLoad.directoryBrowser.loading") }}
            </span>
          </div>
        </div>

        <!-- Manual path jump (2026-08-15) -->
        <div class="d-flex align-center ga-2 mb-1">
          <v-text-field
            v-model="pathInput"
            density="compact"
            variant="outlined"
            hide-details
            class="flex-grow-1"
            data-testid="dir-path-input"
            :placeholder="
              tm('spcodeProjectLoad.directoryBrowser.pathInputPlaceholder')
            "
            :error="pathInputError"
            @update:model-value="pathInputError = false"
            @keydown.enter="onJumpInput"
          />
          <v-btn
            color="primary"
            variant="tonal"
            data-testid="dir-jump"
            @click="onJumpInput"
          >
            {{ tm("spcodeProjectLoad.directoryBrowser.jump") }}
          </v-btn>
        </div>
        <div v-if="pathInputError" class="text-caption text-error mb-2">
          {{ tm("spcodeProjectLoad.directoryBrowser.pathInputError") }}
        </div>

        <!-- Error bar -->
        <v-alert
          v-if="browser.error.value"
          type="warning"
          density="compact"
          variant="tonal"
          class="mb-2"
          dismissible
        >
          {{ errorText }}
          <template #close>
            <v-btn
              icon="mdi-refresh"
              variant="text"
              size="x-small"
              @click="browser.goHome"
            >
              {{ tm("spcodeProjectLoad.directoryBrowser.retry") }}
            </v-btn>
          </template>
        </v-alert>

        <!-- Directory list -->
        <div v-if="browser.loading.value" class="d-flex justify-center pa-6">
          <v-progress-circular indeterminate size="28" />
        </div>

        <!-- This PC: available drives (Windows multi-drive navigation) -->
        <v-list
          v-else-if="
            !browser.error.value &&
            browser.computerMode.value &&
            browser.drives.value.length
          "
          density="compact"
          class="dir-list pa-0"
        >
          <v-list-item
            v-for="d in browser.drives.value"
            :key="d"
            rounded="md"
            class="dir-item"
            :class="{ 'dir-item--selected': selectedPath === d }"
            @click="selectedPath = d"
            @dblclick="browser.list(d)"
          >
            <template #prepend>
              <v-icon icon="mdi-harddisk" size="small" class="mr-2" />
            </template>
            <v-list-item-title class="dir-name">
              {{ d }}
            </v-list-item-title>
          </v-list-item>
        </v-list>
        <div
          v-else-if="!browser.error.value && browser.computerMode.value"
          class="pa-4 text-center text-caption text-medium-emphasis"
        >
          {{ tm("spcodeProjectLoad.directoryBrowser.noDrives") }}
        </div>

        <!-- Folder listing -->
        <div
          v-else-if="!browser.error.value && !browser.entries.value.length"
          class="pa-4 text-center text-caption text-medium-emphasis"
        >
          {{ tm("spcodeProjectLoad.directoryBrowser.empty") }}
        </div>
        <v-list
          v-else-if="!browser.error.value"
          density="compact"
          class="dir-list pa-0"
        >
          <v-list-item
            v-for="entry in browser.entries.value"
            :key="entry.path"
            rounded="md"
            class="dir-item"
            :class="{ 'dir-item--selected': selectedPath === entry.path }"
            @click="onSelectEntry(entry)"
            @dblclick="onEnterEntry(entry)"
          >
            <template #prepend>
              <v-icon icon="mdi-folder-outline" size="small" class="mr-2" />
            </template>
            <v-list-item-title class="dir-name">
              {{ entry.name }}
            </v-list-item-title>
          </v-list-item>
        </v-list>

        <div
          v-if="browser.truncated.value && !browser.error.value"
          class="text-caption text-medium-emphasis mt-1"
        >
          {{ tm("spcodeProjectLoad.directoryBrowser.truncated") }}
        </div>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="close">
          {{ tm("spcodeProjectLoad.directoryBrowser.cancel") }}
        </v-btn>
        <v-btn
          color="primary"
          variant="tonal"
          data-testid="dir-confirm"
          :disabled="
            browser.loading.value ||
            (!browser.currentPath.value && !selectedPath)
          "
          @click="onConfirm"
        >
          {{ tm("spcodeProjectLoad.directoryBrowser.open") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.directory-browser-card {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.directory-browser-body {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  overflow-y: auto;
}

.breadcrumb {
  flex: 1 1 auto;
  min-width: 0;
  white-space: nowrap;
}

.breadcrumb-seg {
  border: 0;
  background: transparent;
  color: rgb(var(--v-theme-primary));
  font-size: 13px;
  cursor: pointer;
  padding: 0 2px;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.breadcrumb-seg:hover {
  text-decoration: underline;
}

.dir-list {
  flex: 1 1 0;
  min-height: 96px;
  border: 1px solid var(--sp-chip-border);
  border-radius: 8px;
  overflow-y: auto;
}

.dir-item {
  cursor: pointer;
}

/* 选中态:用 AstrBot 主题蓝做浅色底 + 蓝色文字,保证深浅色模式下文字都可见。
   不要依赖 v-list-item 的 active overlay(深色主题下是黑底,文字不可见)。 */
.dir-item--selected {
  background-color: rgba(var(--v-theme-primary), 0.14);
}

.dir-item--selected .dir-name {
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
}

.dir-name {
  font-family: var(--v-font-mono, monospace);
  font-size: 13px;
  word-break: break-all;
}

/* "此电脑"静态面包屑(当前就在此电脑视图):不可点击,普通文字色 */
.breadcrumb-seg--static {
  cursor: default;
  color: rgb(var(--v-theme-on-surface));
  text-decoration: none;
}
</style>
