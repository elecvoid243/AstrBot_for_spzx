<!-- Author: elecvoid243 @ 2026-10-22
     Web preview tab of the project workbench. Hosts user-supplied addresses
     in separate iframes, plus per-project bookmarks.

     Deliberately not a browser: cross-origin iframes hide their history
     and current URL, so there is no back/forward and the address bar only
     holds the entry point. -->
<template>
  <div ref="browserRoot" class="web-browser-view">
    <div class="web-browser-tabs" :aria-label="tm(`${I18N}.tabsLabel`)">
      <div
        v-for="tab in tabs"
        :key="tab.id"
        class="web-browser-tab"
        :class="{ 'is-active': tab.id === activeTabId }"
      >
        <button
          type="button"
          class="web-browser-tab-select"
          :aria-pressed="tab.id === activeTabId"
          :title="tab.url || tm(`${I18N}.newTab`)"
          @click="activeTabId = tab.id"
        >
          <v-icon size="14">mdi-web</v-icon>
          <span>{{
            tab.url ? bookmarkLabel(tab.url) : tm(`${I18N}.newTab`)
          }}</span>
        </button>
        <button
          type="button"
          class="web-browser-tab-close"
          :aria-label="
            tm(`${I18N}.closeTab`, {
              name: tab.url ? bookmarkLabel(tab.url) : tm(`${I18N}.newTab`),
            })
          "
          @click="closeTab(tab.id)"
        >
          <v-icon size="13">mdi-close</v-icon>
        </button>
      </div>
      <button
        type="button"
        class="web-browser-icon-btn web-browser-add-tab"
        :title="tm(`${I18N}.addTab`)"
        :aria-label="tm(`${I18N}.addTab`)"
        @click="addTab"
      >
        <v-icon size="16">mdi-plus</v-icon>
      </button>
    </div>
    <div class="web-browser-address">
      <v-icon size="14" class="web-browser-address-icon">mdi-web</v-icon>
      <input
        v-model="draft"
        class="web-browser-input"
        type="text"
        spellcheck="false"
        autocomplete="off"
        autocapitalize="off"
        :placeholder="tm(`${I18N}.urlPlaceholder`)"
        :aria-label="tm(`${I18N}.urlLabel`)"
        :aria-invalid="errorMessage !== ''"
        @keydown.enter.prevent="submit"
      />
      <button
        type="button"
        class="web-browser-star"
        :class="{ 'is-on': isBookmarked }"
        :disabled="!committedUrl"
        :aria-pressed="isBookmarked"
        :title="
          tm(`${I18N}.${isBookmarked ? 'bookmarkRemove' : 'bookmarkAdd'}`)
        "
        :aria-label="
          tm(`${I18N}.${isBookmarked ? 'bookmarkRemove' : 'bookmarkAdd'}`)
        "
        @click="toggleBookmark"
      >
        <v-icon size="14">{{
          isBookmarked ? "mdi-star" : "mdi-star-outline"
        }}</v-icon>
      </button>
      <button
        type="button"
        class="web-browser-icon-btn"
        :disabled="!committedUrl"
        :title="tm(`${I18N}.reload`)"
        :aria-label="tm(`${I18N}.reload`)"
        @click="reload"
      >
        <v-icon size="16">mdi-refresh</v-icon>
      </button>
      <button
        type="button"
        class="web-browser-icon-btn"
        :disabled="!committedUrl"
        :title="tm(`${I18N}.openExternal`)"
        :aria-label="tm(`${I18N}.openExternal`)"
        @click="openExternal"
      >
        <v-icon size="16">mdi-open-in-new</v-icon>
      </button>
      <button
        ref="fullscreenButton"
        type="button"
        class="web-browser-icon-btn web-browser-fullscreen"
        :aria-pressed="fullscreen"
        :title="tm(`${I18N}.${fullscreen ? 'exitFullscreen' : 'fullscreen'}`)"
        :aria-label="
          tm(`${I18N}.${fullscreen ? 'exitFullscreen' : 'fullscreen'}`)
        "
        @click="toggleFullscreen"
      >
        <v-icon size="16">{{
          fullscreen ? "mdi-fullscreen-exit" : "mdi-fullscreen"
        }}</v-icon>
      </button>
    </div>

    <div v-if="bookmarks.length" class="web-browser-options">
      <span
        v-for="bookmark in bookmarks"
        :key="bookmark"
        class="web-browser-bookmark"
        :title="bookmark"
      >
        <button
          type="button"
          class="web-browser-bookmark-label"
          :aria-label="tm(`${I18N}.bookmarkOpen`, { url: bookmark })"
          @click="openQuick(bookmark)"
        >
          {{ bookmarkLabel(bookmark) }}
        </button>
        <button
          type="button"
          class="web-browser-bookmark-remove"
          :aria-label="tm(`${I18N}.bookmarkRemoveNamed`, { url: bookmark })"
          @click="removeBookmark(bookmark)"
        >
          <v-icon size="10">mdi-close</v-icon>
        </button>
      </span>
    </div>

    <p v-if="errorMessage" class="web-browser-error" role="alert">
      {{ errorMessage }}
    </p>

    <div ref="viewport" class="web-browser-content">
      <div v-if="!committedUrl" class="web-browser-empty">
        <v-icon size="28" class="web-browser-empty-icon">mdi-web</v-icon>
        <p class="web-browser-empty-title">{{ tm(`${I18N}.emptyTitle`) }}</p>
        <p class="web-browser-empty-hint">{{ tm(`${I18N}.emptyHint`) }}</p>
        <div class="web-browser-quick">
          <button
            v-for="quick in quickLinks"
            :key="quick.url"
            type="button"
            class="web-browser-quick-btn"
            @click="openQuick(quick.url)"
          >
            {{ quick.label }}
          </button>
        </div>
      </div>

      <div
        v-for="tab in tabs"
        :key="tab.id"
        v-show="tab.url && tab.id === activeTabId"
        class="web-browser-stage"
      >
        <iframe
          v-if="tab.url"
          :key="tab.reloadNonce"
          class="web-browser-frame"
          :style="frameStyle"
          :src="tab.url"
          :sandbox="buildIframeSandbox(tab.url, dashboardOrigin)"
          referrerpolicy="no-referrer"
          :title="tm(`${I18N}.frameTitle`)"
        />
      </div>
    </div>

    <p v-if="committedUrl" class="web-browser-note">
      {{ tm(`${I18N}.embedHint`) }}
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import {
  MAX_BOOKMARKS,
  QUICK_URLS,
  bookmarkLabel,
  buildIframeSandbox,
  loadBrowserState,
  normalizeBrowserUrl,
  saveBrowserState,
} from "./webBrowser";

const props = withDefaults(
  defineProps<{ projectRoot: string | null; visible?: boolean }>(),
  {
    visible: true,
  },
);

const { tm } = useModuleI18n("features/chat");
const I18N = "spcodeProjectLoad.webBrowser";
const dashboardOrigin = window.location.origin;
const browserRoot = ref<HTMLElement | null>(null);
const fullscreenButton = ref<HTMLButtonElement | null>(null);
const viewport = ref<HTMLElement | null>(null);
const viewportSize = ref({ width: 0, height: 0 });
const fullscreen = ref(false);
let resizeObserver: ResizeObserver | undefined;

// Scale the iframe itself: cross-origin documents remain private and interactive.
// Use the same desktop width floor as Agent browser, with vertical scrolling inside it.
const frameStyle = computed(() => {
  const { width, height } = viewportSize.value;
  const layoutWidth = Math.max(1280, width);
  const scale = width > 0 ? width / layoutWidth : 1;
  return {
    width: `${layoutWidth}px`,
    height: `${Math.max(1, Math.floor(height / scale))}px`,
    transform: `scale(${scale})`,
    visibility:
      width > 0 && height > 0 ? ("visible" as const) : ("hidden" as const),
  };
});

/** Toggle native fullscreen without moving or reloading the iframe document. */
async function toggleFullscreen(): Promise<void> {
  const root = browserRoot.value;
  if (!root) return;
  try {
    if (document.fullscreenElement === root) await document.exitFullscreen();
    else await root.requestFullscreen();
  } catch {
    errorMessage.value = tm(`${I18N}.fullscreenFailed`);
  }
}
const fullscreenChanged = () => {
  const wasFullscreen = fullscreen.value;
  fullscreen.value = document.fullscreenElement === browserRoot.value;
  if (wasFullscreen && !fullscreen.value && props.visible)
    fullscreenButton.value?.focus({ preventScroll: true });
};

let nextTabId = 0;
const tabs = ref([
  { id: nextTabId++, url: "", draft: "", error: "", reloadNonce: 0 },
]);
const activeTabId = ref(tabs.value[0].id);
const activeTab = computed(
  () => tabs.value.find((tab) => tab.id === activeTabId.value)!,
);
const draft = computed({
  get: () => activeTab.value.draft,
  set: (value: string) => {
    activeTab.value.draft = value;
  },
});
const committedUrl = computed({
  get: () => activeTab.value.url,
  set: (value: string) => {
    activeTab.value.url = value;
  },
});
const bookmarks = ref<string[]>([]);
const errorMessage = computed({
  get: () => activeTab.value.error,
  set: (value: string) => {
    activeTab.value.error = value;
  },
});
// Last payload written (or hydrated). Skips redundant writes and the
// hydrate → watcher echo when the project changes.
let lastPersisted = "";

const quickLinks = computed(() =>
  QUICK_URLS.map((url) => ({ url, label: bookmarkLabel(url) })),
);
const isBookmarked = computed(
  () =>
    committedUrl.value !== "" && bookmarks.value.includes(committedUrl.value),
);

function persist(): void {
  const state = {
    url: committedUrl.value,
    bookmarks: bookmarks.value,
    tabs: tabs.value.map((tab) => tab.url),
    activeTab: tabs.value.findIndex((tab) => tab.id === activeTabId.value),
  };
  const serialized = JSON.stringify(state);
  if (serialized === lastPersisted) return;
  lastPersisted = serialized;
  saveBrowserState(props.projectRoot, state);
}

function hydrate(): void {
  const state = loadBrowserState(props.projectRoot);
  tabs.value = (state.tabs ?? [state.url]).map((url) => ({
    id: nextTabId++,
    url,
    draft: url,
    error: "",
    reloadNonce: 0,
  }));
  activeTabId.value = tabs.value[state.activeTab ?? 0].id;
  bookmarks.value = state.bookmarks;
  lastPersisted = "";
}

function addTab(): void {
  const tab = {
    id: nextTabId++,
    url: "",
    draft: "",
    error: "",
    reloadNonce: 0,
  };
  tabs.value.push(tab);
  activeTabId.value = tab.id;
}

function closeTab(id: number): void {
  const index = tabs.value.findIndex((tab) => tab.id === id);
  if (index < 0) return;
  if (tabs.value.length === 1) {
    addTab();
  } else if (id === activeTabId.value) {
    activeTabId.value = tabs.value[index + 1]?.id ?? tabs.value[index - 1].id;
  }
  tabs.value.splice(index, 1);
}

function setError(reason: "invalid" | "scheme"): void {
  errorMessage.value = tm(
    `${I18N}.${reason === "scheme" ? "errorScheme" : "errorInvalid"}`,
  );
}

function commitUrl(url: string): void {
  errorMessage.value = "";
  committedUrl.value = url;
  draft.value = url;
}

function submit(): void {
  const result = normalizeBrowserUrl(draft.value);
  if (!result.ok) {
    if (result.reason === "empty") {
      committedUrl.value = "";
      errorMessage.value = "";
      return;
    }
    setError(result.reason);
    return;
  }
  commitUrl(result.url);
}

function openQuick(url: string): void {
  const result = normalizeBrowserUrl(url);
  if (result.ok) commitUrl(result.url);
}

function reload(): void {
  if (!committedUrl.value) return;
  activeTab.value.reloadNonce += 1;
}

function openExternal(): void {
  if (!committedUrl.value) return;
  window.open(committedUrl.value, "_blank", "noopener,noreferrer");
}

function toggleBookmark(): void {
  const current = committedUrl.value;
  if (!current) return;
  if (bookmarks.value.includes(current)) {
    bookmarks.value = bookmarks.value.filter((url) => url !== current);
    errorMessage.value = "";
    return;
  }
  if (bookmarks.value.length >= MAX_BOOKMARKS) {
    errorMessage.value = tm(`${I18N}.bookmarkLimit`, { max: MAX_BOOKMARKS });
    return;
  }
  bookmarks.value = [...bookmarks.value, current];
  errorMessage.value = "";
}

function removeBookmark(url: string): void {
  bookmarks.value = bookmarks.value.filter((entry) => entry !== url);
}

watch(() => props.projectRoot, hydrate);
watch(
  () => props.visible,
  (visible) => {
    if (!visible && document.fullscreenElement === browserRoot.value)
      void document.exitFullscreen().catch(() => {});
  },
);
watch([tabs, activeTabId, bookmarks], persist, { deep: true });
onMounted(() => {
  hydrate();
  document.addEventListener("fullscreenchange", fullscreenChanged);
  if (typeof ResizeObserver === "undefined" || !viewport.value) return;
  resizeObserver = new ResizeObserver(([entry]) => {
    if (entry && entry.contentRect.width > 0 && entry.contentRect.height > 0)
      viewportSize.value = {
        width: entry.contentRect.width,
        height: entry.contentRect.height,
      };
  });
  resizeObserver.observe(viewport.value);
});
onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  document.removeEventListener("fullscreenchange", fullscreenChanged);
});
</script>

<style scoped>
.web-browser-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 320px;
  min-width: 0;
  gap: 8px;
}
.web-browser-view:fullscreen {
  width: 100%;
  height: 100%;
  min-height: 0;
  padding: 12px;
  box-sizing: border-box;
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
}

.web-browser-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  overflow-x: auto;
  flex: 0 0 auto;
}
.web-browser-tab {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
  max-width: 180px;
  border: 1px solid transparent;
  border-radius: 8px;
  color: rgba(var(--v-theme-on-surface), 0.6);
}
.web-browser-tab.is-active {
  border-color: rgba(var(--v-theme-primary), 0.3);
  background: rgba(var(--v-theme-primary), 0.1);
  color: rgb(var(--v-theme-on-surface));
}
.web-browser-tab-select {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  padding: 5px 4px 5px 8px;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.web-browser-tab-select span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.web-browser-tab-close {
  flex: 0 0 auto;
  padding: 4px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.web-browser-tab-close:hover,
.web-browser-tab-select:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
}
.web-browser-add-tab {
  position: sticky;
  right: 0;
  background: rgb(var(--v-theme-surface));
}

.web-browser-address {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px 4px 10px;
  border-radius: 8px;
  background: rgba(var(--v-theme-on-surface), 0.04);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  transition:
    border-color 0.14s ease,
    box-shadow 0.14s ease;
}
.web-browser-address:focus-within {
  border-color: rgba(var(--v-theme-primary), 0.6);
  box-shadow: 0 0 0 3px rgba(var(--v-theme-primary), 0.1);
}
.web-browser-address-icon {
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.54));
  flex: 0 0 auto;
}
.web-browser-input {
  flex: 1 1 auto;
  min-width: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font-family: inherit;
  font-size: 13px;
  line-height: 24px;
}
.web-browser-input::placeholder {
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.42));
}

.web-browser-icon-btn {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.62));
  cursor: pointer;
  transition:
    background 0.14s ease,
    color 0.14s ease;
}
.web-browser-icon-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.08);
  color: rgb(var(--v-theme-on-surface));
}
.web-browser-icon-btn:disabled {
  opacity: 0.38;
  cursor: default;
}

.web-browser-options {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.web-browser-star {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 24px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.54));
  cursor: pointer;
}
.web-browser-star:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.08);
}
.web-browser-star.is-on {
  color: rgb(var(--v-theme-primary));
}
.web-browser-star:disabled {
  opacity: 0.38;
  cursor: default;
}

.web-browser-bookmark {
  display: inline-flex;
  align-items: stretch;
  max-width: 220px;
  border-radius: 6px;
  background: rgba(var(--v-theme-on-surface), 0.05);
  overflow: hidden;
}
.web-browser-bookmark-label {
  border: 0;
  background: transparent;
  padding: 3px 2px 3px 8px;
  font-family: inherit;
  font-size: 11.5px;
  line-height: 16px;
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.72));
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.web-browser-bookmark-label:hover {
  color: rgb(var(--v-theme-on-surface));
}
.web-browser-bookmark-remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  border: 0;
  background: transparent;
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.42));
  cursor: pointer;
}
.web-browser-bookmark-remove:hover {
  color: rgb(var(--v-theme-error));
}

.web-browser-error {
  margin: 0;
  font-size: 11.5px;
  line-height: 16px;
  color: rgb(var(--v-theme-error));
}

.web-browser-empty {
  flex: 1;
  height: 100%;
  box-sizing: border-box;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 24px 12px;
  text-align: center;
}
.web-browser-empty-icon {
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.32));
}
.web-browser-empty-title {
  margin: 0;
  font-size: 13px;
  font-weight: 500;
  color: rgba(var(--v-theme-on-surface), 0.72);
}
.web-browser-empty-hint {
  margin: 0;
  max-width: 320px;
  font-size: 11.5px;
  line-height: 17px;
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.5));
}
.web-browser-quick {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 6px;
  margin-top: 6px;
}
.web-browser-quick-btn {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 999px;
  background: transparent;
  padding: 4px 10px;
  font-family: inherit;
  font-size: 11.5px;
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.7));
  cursor: pointer;
}
.web-browser-quick-btn:hover {
  border-color: rgba(var(--v-theme-primary), 0.5);
  color: rgb(var(--v-theme-primary));
}

.web-browser-content {
  flex: 1;
  min-height: 0;
  min-width: 0;
  position: relative;
  overflow: hidden;
  border-radius: 8px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  background: rgb(var(--v-theme-surface));
}
.web-browser-stage {
  position: absolute;
  inset: 0;
  overflow: hidden;
}
.web-browser-frame {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
  transform-origin: top left;
  background: #fff;
}

.web-browser-note {
  margin: 0;
  font-size: 11px;
  line-height: 15px;
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.42));
}
</style>
