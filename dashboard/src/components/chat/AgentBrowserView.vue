<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useI18n } from "@/i18n/composables";
import { pluginExtensionApi } from "@/api/v1";
import { fetchWithAuth } from "@/api/http";

const props = defineProps<{ chatId?: string | null; visible: boolean }>();
const { t } = useI18n();
const key = "features.chat.agentBrowser";

interface BrowserState {
  available: boolean;
  generation?: string;
  browser_session_id?: string;
  tab_id?: string;
  sequence?: number;
  url?: string;
  title?: string;
  width?: number;
  height?: number;
  image?: string;
  mime_type?: string;
  type?: "state" | "frame" | "action" | "error";
  document_id?: string;
  viewport_width?: number;
  viewport_height?: number;
  scroll_x?: number;
  scroll_y?: number;
  current_action?: BrowserAction | null;
  recent_actions?: BrowserAction[];
  action_cursor?: number;
  highlight?:
    | (BrowserState & {
        action_id: string;
        x: number;
        y: number;
        expires_at: number;
      })
    | null;
}

interface BrowserAction {
  action_id: string;
  kind: "navigate" | "click" | "type" | "scroll" | "tab" | "close" | "inspect";
  status: "running" | "succeeded" | "failed" | "timed_out" | "cancelled";
}

const state = ref<BrowserState | null>(null);
const error = ref("");
const loading = ref(false);
const live = ref(false);
const pageVisible = ref(document.visibilityState !== "hidden");
let pending: AbortController | null = null;
const now = ref(Date.now());
let markerTimer: ReturnType<typeof setTimeout> | undefined;
watch(
  () => state.value?.highlight,
  (marker) => {
    clearTimeout(markerTimer);
    now.value = Date.now();
    if (marker)
      markerTimer = setTimeout(
        () => {
          now.value = Date.now();
        },
        Math.max(0, marker.expires_at * 1000 - Date.now()) + 1,
      );
  },
);
const marker = computed(() => {
  const current = state.value;
  const point = current?.highlight;
  if (!point || !current?.image || point.expires_at * 1000 <= now.value)
    return null;
  if (
    [
      "generation",
      "browser_session_id",
      "tab_id",
      "document_id",
      "url",
      "viewport_width",
      "viewport_height",
      "scroll_x",
      "scroll_y",
    ].some(
      (field) =>
        point[field as keyof BrowserState] !==
        current[field as keyof BrowserState],
    )
  )
    return null;
  if (
    ![point.x, point.y, point.viewport_width, point.viewport_height].every(
      (value) => typeof value === "number" && Number.isFinite(value),
    )
  )
    return null;
  if (
    point.x < 0 ||
    point.y < 0 ||
    point.x >= point.viewport_width! ||
    point.y >= point.viewport_height!
  )
    return null;
  if (
    ![current.current_action, ...(current.recent_actions || [])].some(
      (action) => action?.action_id === point.action_id,
    )
  )
    return null;
  return {
    left: `${(point.x / point.viewport_width!) * 100}%`,
    top: `${(point.y / point.viewport_height!) * 100}%`,
  };
});
const actions = computed(() => [
  ...(state.value?.current_action ? [state.value.current_action] : []),
  ...(state.value?.recent_actions || []).slice(-32).reverse(),
]);

async function follow(
  current: BrowserState,
  controller: AbortController,
): Promise<void> {
  const params = new URLSearchParams({
    chat_id: props.chatId!,
    generation: current.generation!,
    browser_session_id: current.browser_session_id!,
    action_cursor: String(state.value?.action_cursor ?? 0),
  });
  const response = await fetchWithAuth(
    `/api/v1/plugins/extensions/browser-use/stream?${params}`,
    { signal: controller.signal },
  );
  if (!response.ok || !response.body) throw new Error(t(`${key}.disconnected`));
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (!controller.signal.aborted) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end: number;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        if (end > 768 * 1024) throw new Error(t(`${key}.unavailable`));
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const data = block
          .split("\n")
          .find((line) => line.startsWith("data: "));
        if (!data || controller.signal.aborted) continue;
        const event = JSON.parse(data.slice(6)) as BrowserState;
        if (
          event.generation !== current.generation ||
          event.browser_session_id !== current.browser_session_id
        )
          continue;
        if (
          !Number.isSafeInteger(event.sequence) ||
          event.sequence! <= (state.value?.sequence ?? -1)
        )
          continue;
        if (event.type === "error") throw new Error(t(`${key}.disconnected`));
        if (
          event.type !== "frame" &&
          event.type !== "state" &&
          event.type !== "action"
        )
          continue;
        if (
          event.type === "frame" &&
          (event.mime_type !== "image/jpeg" ||
            !event.image ||
            event.image.length > 700000 ||
            !event.width ||
            !event.height ||
            event.width < 0 ||
            event.height < 0 ||
            event.width > 1280 ||
            event.height > 720)
        )
          continue;
        // Every event is self-contained; sequence ordering also rejects old tabs.
        // Preserve pixels only while their document and coordinate space match.
        const previous = state.value;
        const unchanged =
          previous?.tab_id === event.tab_id &&
          previous?.url === event.url &&
          previous?.document_id === event.document_id &&
          previous?.viewport_width === event.viewport_width &&
          previous?.viewport_height === event.viewport_height &&
          previous?.scroll_x === event.scroll_x &&
          previous?.scroll_y === event.scroll_y;
        state.value =
          event.type === "frame"
            ? event
            : {
                ...event,
                ...(unchanged
                  ? {
                      image: previous?.image,
                      mime_type: previous?.mime_type,
                      width: previous?.width,
                      height: previous?.height,
                    }
                  : {}),
              };
        live.value = true;
      }
      if (buffer.length > 768 * 1024) throw new Error(t(`${key}.unavailable`));
    }
    if (!controller.signal.aborted) throw new Error(t(`${key}.disconnected`));
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

async function refresh(): Promise<void> {
  pending?.abort();
  state.value = null;
  error.value = "";
  live.value = false;
  loading.value = false;
  if (!props.chatId || !props.visible || !pageVisible.value) return;
  const controller = new AbortController();
  pending = controller;
  loading.value = true;
  const chatId = props.chatId;
  try {
    const initial = await pluginExtensionApi.get<BrowserState>(
      "browser-use/status",
      {
        params: { chat_id: chatId },
        signal: controller.signal,
      },
    );
    if (controller.signal.aborted) return;
    if (initial.data.status !== "ok")
      throw new Error(initial.data.message || undefined);
    const current = initial.data.data;
    if (!current.available) {
      state.value = current;
      return;
    }
    const snapshot = await pluginExtensionApi.get<BrowserState>(
      "browser-use/snapshot",
      {
        params: {
          chat_id: chatId,
          generation: current.generation,
          browser_session_id: current.browser_session_id,
        },
        signal: controller.signal,
      },
    );
    if (controller.signal.aborted) return;
    if (snapshot.data.status !== "ok")
      throw new Error(snapshot.data.message || undefined);
    if (
      snapshot.data.data.generation !== current.generation ||
      snapshot.data.data.browser_session_id !== current.browser_session_id
    ) {
      throw new Error(t(`${key}.expired`));
    }
    state.value = snapshot.data.data;
    loading.value = false;
    await follow(current, controller);
  } catch (err: any) {
    if (!controller.signal.aborted) {
      live.value = false;
      error.value =
        err.response?.data?.message || err.message || t(`${key}.unavailable`);
    }
  } finally {
    if (pending === controller) loading.value = false;
  }
}

watch(
  () => [props.chatId, props.visible, pageVisible.value],
  () => {
    void refresh();
  },
  { immediate: true },
);
const visibilityChanged = () => {
  pageVisible.value = document.visibilityState !== "hidden";
};
document.addEventListener("visibilitychange", visibilityChanged);
onBeforeUnmount(() => {
  clearTimeout(markerTimer);
  pending?.abort();
  document.removeEventListener("visibilitychange", visibilityChanged);
});
</script>

<template>
  <section
    class="agent-browser-view"
    :aria-label="t(`${key}.title`)"
    :data-generation="state?.generation"
    :data-browser-session-id="state?.browser_session_id"
    :data-tab-id="state?.tab_id"
    :data-sequence="state?.sequence"
  >
    <div class="agent-browser-toolbar">
      <div class="agent-browser-page">
        <strong>{{ state?.title || t(`${key}.title`) }}</strong>
        <div class="agent-browser-url">{{ state?.url }}</div>
        <div class="agent-browser-status" aria-live="polite">
          {{ live ? t(`${key}.live`) : t(`${key}.snapshot`) }}
          <span v-if="state?.tab_id">
            · {{ t(`${key}.tab`) }} {{ state.tab_id.slice(-4) }}</span
          >
          <span v-if="state?.width">
            · {{ state.width }}×{{ state.height }} · #{{ state.sequence }}</span
          >
        </div>
      </div>
      <v-btn
        variant="text"
        size="small"
        :loading="loading"
        :disabled="!chatId"
        @click="refresh"
      >
        {{ t(`${key}.refresh`) }}
      </v-btn>
    </div>
    <div v-if="error" role="alert" class="pa-4">{{ error }}</div>
    <div v-else-if="!state?.image" role="status" class="pa-4">
      {{ loading ? t(`${key}.loading`) : t(`${key}.empty`) }}
    </div>
    <div v-else class="agent-browser-image">
      <div class="agent-browser-viewport">
        <img
          :src="`data:${state.mime_type};base64,${state.image}`"
          :width="state.width"
          :height="state.height"
          :alt="state.title || t(`${key}.title`)"
        />
        <span
          v-if="marker"
          class="agent-browser-target"
          :style="marker"
          :aria-label="t(`${key}.target`)"
        />
      </div>
    </div>
    <div
      v-if="actions.length"
      class="agent-browser-actions"
      aria-live="polite"
      :aria-label="t(`${key}.recentActions`)"
    >
      <strong>{{ t(`${key}.recentActions`) }}</strong>
      <ol>
        <li
          v-for="action in actions"
          :key="action.action_id"
          :data-action-id="action.action_id"
          :data-action-status="action.status"
        >
          {{ t(`${key}.actionKinds.${action.kind}`) }} ·
          {{ t(`${key}.actionStatuses.${action.status}`) }}
        </li>
      </ol>
    </div>
  </section>
</template>

<style scoped>
.agent-browser-view {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.agent-browser-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.12);
}
.agent-browser-page {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}
.agent-browser-url {
  font-size: 12px;
  overflow-wrap: anywhere;
}
.agent-browser-status {
  font-size: 11px;
  opacity: 0.7;
}
.agent-browser-image {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.agent-browser-image img {
  display: block;
  width: 100%;
  height: auto;
  pointer-events: none;
}
.agent-browser-viewport {
  position: relative;
}
.agent-browser-target {
  position: absolute;
  width: 24px;
  height: 24px;
  border: 3px solid #e65100;
  border-radius: 50%;
  box-shadow: 0 0 0 2px white;
  transform: translate(-50%, -50%);
  pointer-events: none;
}
.agent-browser-actions {
  padding: 8px 12px;
  font-size: 12px;
  max-height: 150px;
  overflow: auto;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.12);
}
.agent-browser-actions ol {
  padding-left: 20px;
}
</style>
