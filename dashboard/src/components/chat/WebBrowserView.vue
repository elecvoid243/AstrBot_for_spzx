<!-- Author: elecvoid243 @ 2026-10-22
     Web preview tab of the project workbench. Hosts one iframe pointed at
     a user-supplied address (usually a local dev server), plus width
     presets, a reload/new-tab pair and per-project bookmarks.

     Deliberately not a browser: cross-origin iframes hide their history
     and current URL, so there is no back/forward and the address bar only
     holds the entry point. -->
<template>
  <div class="web-browser-view">
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
    </div>

    <div class="web-browser-options">
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

      <div
        class="web-browser-widths"
        role="group"
        :aria-label="tm(`${I18N}.widthLabel`)"
      >
        <button
          v-for="preset in WIDTH_PRESETS"
          :key="preset"
          type="button"
          class="web-browser-width-btn"
          :class="{ 'is-active': width === preset }"
          :aria-pressed="width === preset"
          @click="width = preset"
        >
          {{ widthLabel(preset) }}
        </button>
      </div>
    </div>

    <p v-if="errorMessage" class="web-browser-error" role="alert">
      {{ errorMessage }}
    </p>

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

    <div v-else class="web-browser-stage">
      <div class="web-browser-frame-wrap" :style="frameStyle">
        <iframe
          :key="reloadNonce"
          class="web-browser-frame"
          :src="committedUrl"
          :sandbox="sandbox"
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
import { computed, onMounted, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import {
  MAX_BOOKMARKS,
  QUICK_URLS,
  WIDTH_PRESETS,
  bookmarkLabel,
  buildIframeSandbox,
  loadBrowserState,
  normalizeBrowserUrl,
  saveBrowserState,
  type WidthPreset,
} from "./webBrowser";

const props = defineProps<{ projectRoot: string | null }>();

const { tm } = useModuleI18n("features/chat");
const I18N = "spcodeProjectLoad.webBrowser";

const draft = ref("");
const committedUrl = ref("");
const width = ref<WidthPreset>("fill");
const bookmarks = ref<string[]>([]);
const errorMessage = ref("");
const reloadNonce = ref(0);
// Last payload written (or hydrated). Skips redundant writes and the
// hydrate → watcher echo when the project changes.
let lastPersisted = "";

const sandbox = computed(() =>
  buildIframeSandbox(committedUrl.value, window.location.origin),
);
const isBookmarked = computed(
  () =>
    committedUrl.value !== "" && bookmarks.value.includes(committedUrl.value),
);
const frameStyle = computed(() => ({
  width: width.value === "fill" ? "100%" : `${width.value}px`,
}));
const quickLinks = computed(() =>
  QUICK_URLS.map((url) => ({ url, label: bookmarkLabel(url) })),
);

function widthLabel(preset: WidthPreset): string {
  return preset === "fill" ? tm(`${I18N}.widthFill`) : String(preset);
}

function persist(): void {
  const state = {
    url: committedUrl.value,
    width: width.value,
    bookmarks: bookmarks.value,
  };
  const serialized = JSON.stringify(state);
  if (serialized === lastPersisted) return;
  lastPersisted = serialized;
  saveBrowserState(props.projectRoot, state);
}

function hydrate(): void {
  const state = loadBrowserState(props.projectRoot);
  committedUrl.value = state.url;
  draft.value = state.url;
  width.value = state.width;
  bookmarks.value = state.bookmarks;
  errorMessage.value = "";
  reloadNonce.value = 0;
  lastPersisted = JSON.stringify(state);
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
  reloadNonce.value += 1;
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
watch([committedUrl, width, bookmarks], persist, { deep: true });
onMounted(hydrate);
</script>

<style scoped>
.web-browser-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 320px;
  gap: 8px;
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
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
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

.web-browser-widths {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-left: auto;
  padding: 2px;
  border-radius: 7px;
  background: rgba(var(--v-theme-on-surface), 0.05);
}
.web-browser-width-btn {
  border: 0;
  border-radius: 5px;
  background: transparent;
  padding: 2px 6px;
  font-family: inherit;
  font-size: 11px;
  line-height: 16px;
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.62));
  cursor: pointer;
  white-space: nowrap;
}
.web-browser-width-btn:hover {
  color: rgb(var(--v-theme-on-surface));
}
.web-browser-width-btn.is-active {
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-primary));
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
}

.web-browser-error {
  margin: 0;
  font-size: 11.5px;
  line-height: 16px;
  color: rgb(var(--v-theme-error));
}

.web-browser-empty {
  flex: 1;
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

.web-browser-stage {
  flex: 1;
  min-height: 0;
  overflow: auto;
  border-radius: 8px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  background: rgb(var(--v-theme-surface));
}
.web-browser-frame-wrap {
  /* auto margins (not flex centering) so an over-wide preset keeps its
     left edge reachable when the stage scrolls horizontally */
  height: 100%;
  margin: 0 auto;
}
.web-browser-frame {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
  background: #fff;
}

.web-browser-note {
  margin: 0;
  font-size: 11px;
  line-height: 15px;
  color: var(--chat-muted, rgba(var(--v-theme-on-surface), 0.42));
}
</style>
