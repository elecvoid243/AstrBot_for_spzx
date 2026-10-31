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
  execution_state?: "active" | "stopped" | "closed" | "disabled";
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
  action_reset?: boolean;
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
const loading = ref(false);
const controlling = ref(false);
const controlError = ref(false);
const connection = ref("loading");
const resynchronized = ref(false);
const pageVisible = ref(document.visibilityState !== "hidden");
const online = ref(navigator.onLine !== false);
let pending: AbortController | null = null;
let retryTimer: ReturnType<typeof setTimeout> | undefined;
let retryAttempts = 0;
let lastIdentity = "";
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

/**
 * Follow one authorized lifetime after applying its consistent snapshot.
 *
 * Args:
 *   current: Snapshot carrying the lifetime and the last applied action cursor.
 *   controller: Cancellation scope for this viewing attempt.
 *   chatId: Captured conversation identity, never read from a later selection.
 */
async function follow(
  current: BrowserState,
  controller: AbortController,
  chatId: string,
): Promise<void> {
  const params = new URLSearchParams({
    chat_id: chatId,
    generation: current.generation!,
    browser_session_id: current.browser_session_id!,
    action_cursor: String(current.action_cursor ?? 0),
  });
  const response = await fetchWithAuth(
    `/api/v1/plugins/extensions/browser-use/stream?${params}`,
    { signal: controller.signal },
  );
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => null);
    throw Object.assign(new Error("Observation stream disconnected."), {
      response: { status: response.status, data },
    });
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  // Heartbeats arrive every second. Recover a half-open connection as well as EOF.
  let watchdog = setTimeout(() => void reader.cancel().catch(() => {}), 15000);
  const cancel = () => void reader.cancel().catch(() => {});
  controller.signal.addEventListener("abort", cancel, { once: true });
  try {
    while (!controller.signal.aborted) {
      const { done, value } = await reader.read();
      if (
        controller.signal.aborted ||
        pending !== controller ||
        props.chatId !== chatId
      )
        return;
      if (done) break;
      clearTimeout(watchdog);
      watchdog = setTimeout(() => void reader.cancel().catch(() => {}), 15000);
      buffer += decoder.decode(value, { stream: true });
      let end: number;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        if (end > 768 * 1024) throw new Error(t(`${key}.unavailable`));
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const data = block
          .split("\n")
          .find((line) => line.startsWith("data: "));
        if (!data || controller.signal.aborted || pending !== controller)
          continue;
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
        if (event.type === "error")
          throw new Error("Observation stream disconnected.");
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
        // A newer image must never restore an older action snapshot.
        if ((event.action_cursor ?? 0) < (previous?.action_cursor ?? 0)) {
          event.current_action = previous?.current_action;
          event.recent_actions = previous?.recent_actions;
          event.action_cursor = previous?.action_cursor;
          event.highlight = null;
        }
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
        if (event.action_reset) resynchronized.value = true;
        connection.value =
          event.execution_state === "stopped" ? "stopped" : "live";
        if (
          event.execution_state === "closed" ||
          event.execution_state === "disabled"
        ) {
          state.value = { ...event, image: undefined, highlight: null };
          connection.value = event.execution_state;
          return;
        }
        retryAttempts = 0;
      }
      if (buffer.length > 768 * 1024) throw new Error(t(`${key}.unavailable`));
    }
    if (!controller.signal.aborted)
      throw new Error("Observation stream disconnected.");
  } finally {
    clearTimeout(watchdog);
    controller.signal.removeEventListener("abort", cancel);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

/**
 * Reauthorize and resynchronize observation without starting a browser.
 *
 * Args:
 *   resuming: Show automatic reconnection instead of initial loading.
 */
async function refresh(resuming = false): Promise<void> {
  clearTimeout(retryTimer);
  pending?.abort();
  state.value = null;
  loading.value = false;
  if (!props.chatId || !props.visible || !pageVisible.value) return;
  if (!online.value) {
    connection.value = "disconnected";
    return;
  }
  const controller = new AbortController();
  pending = controller;
  loading.value = true;
  connection.value = resuming ? "reconnecting" : "loading";
  const chatId = props.chatId;
  let retry = true;
  let delay = Math.min(1000 * 2 ** Math.min(retryAttempts, 3), 5000);
  try {
    const initial = await pluginExtensionApi.get<BrowserState>(
      "browser-use/status",
      {
        params: { chat_id: chatId },
        signal: controller.signal,
      },
    );
    if (
      controller.signal.aborted ||
      pending !== controller ||
      props.chatId !== chatId
    )
      return;
    if (initial.data.status !== "ok")
      throw new Error(initial.data.message || undefined);
    const current = initial.data.data;
    if (!current.available) {
      state.value = current;
      connection.value =
        current.execution_state === "closed" ? "closed" : "idle";
      retryAttempts = 0;
      delay = 3000;
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
    if (
      controller.signal.aborted ||
      pending !== controller ||
      props.chatId !== chatId
    )
      return;
    if (snapshot.data.status !== "ok")
      throw new Error(snapshot.data.message || undefined);
    if (
      snapshot.data.data.generation !== current.generation ||
      snapshot.data.data.browser_session_id !== current.browser_session_id
    ) {
      throw new Error(t(`${key}.expired`));
    }
    state.value = snapshot.data.data;
    const identity = JSON.stringify([
      state.value.generation,
      state.value.browser_session_id,
    ]);
    if (lastIdentity && lastIdentity !== identity) resynchronized.value = true;
    lastIdentity = identity;
    connection.value =
      state.value.execution_state === "stopped" ? "stopped" : "snapshot";
    loading.value = false;
    await follow(snapshot.data.data, controller, chatId);
  } catch (err: any) {
    if (!controller.signal.aborted && pending === controller) {
      // Do not leave stale pixels or a running action visible while disconnected.
      state.value = null;
      const status = err.response?.status;
      const reason = err.response?.data?.data?.reason;
      connection.value =
        reason === "busy"
          ? "busy"
          : reason === "disabled" || status === 404
          ? "disabled"
          : status === 401 || status === 403
          ? "denied"
          : status === 503
          ? "unavailable"
          : "disconnected";
      if (status === 409 && reason !== "busy") resynchronized.value = true;
      retry = connection.value !== "denied";
      retryAttempts += 1;
    }
  } finally {
    if (pending === controller && !controller.signal.aborted) {
      loading.value = false;
      if (retry) retryTimer = setTimeout(() => void refresh(true), delay);
    }
  }
}

/**
 * Send a lifetime-bound control and apply only its confirmed server result.
 *
 * Args:
 *   operation: Stop execution or close the authorized browser lifetime.
 *
 * Returns:
 *   Resolves after confirmed cleanup or a visible control error.
 */
async function control(operation: "stop" | "close"): Promise<void> {
  const current = state.value;
  const chatId = props.chatId;
  const attempt = pending;
  if (!current?.available || !chatId || controlling.value) return;
  controlling.value = true;
  controlError.value = false;
  try {
    const response = await pluginExtensionApi.post<BrowserState>(
      `browser-use/${operation}`,
      undefined,
      {
        params: {
          chat_id: chatId,
          generation: current.generation,
          browser_session_id: current.browser_session_id,
        },
      },
    );
    if (
      props.chatId !== chatId ||
      pending !== attempt ||
      attempt?.signal.aborted
    )
      return;
    if (response.data.status !== "ok") throw new Error("Control failed.");
    if (operation === "close") {
      pending?.abort();
      clearTimeout(retryTimer);
      state.value = {
        ...response.data.data,
        image: undefined,
        highlight: null,
      };
      connection.value = "closed";
      loading.value = false;
      retryTimer = setTimeout(() => void refresh(true), 3000);
    } else {
      if ((response.data.data.sequence ?? 0) >= (state.value?.sequence ?? 0))
        state.value = {
          ...response.data.data,
          image: undefined,
          highlight: null,
        };
      connection.value = "stopped";
    }
  } catch {
    if (
      props.chatId === chatId &&
      pending === attempt &&
      !attempt?.signal.aborted
    )
      controlError.value = true;
  } finally {
    controlling.value = false;
  }
}

watch(
  () => [props.chatId, props.visible, pageVisible.value, online.value] as const,
  (next, previous) => {
    if (next[0] !== previous?.[0]) lastIdentity = "";
    retryAttempts = 0;
    resynchronized.value = false;
    void refresh();
  },
  { immediate: true, flush: "sync" },
);
const visibilityChanged = () => {
  pageVisible.value = document.visibilityState !== "hidden";
};
document.addEventListener("visibilitychange", visibilityChanged);
const networkChanged = () => {
  online.value = navigator.onLine !== false;
};
window.addEventListener("online", networkChanged);
window.addEventListener("offline", networkChanged);
onBeforeUnmount(() => {
  clearTimeout(retryTimer);
  clearTimeout(markerTimer);
  pending?.abort();
  document.removeEventListener("visibilitychange", visibilityChanged);
  window.removeEventListener("online", networkChanged);
  window.removeEventListener("offline", networkChanged);
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
    :data-connection="connection"
  >
    <div class="agent-browser-toolbar">
      <div class="agent-browser-page">
        <strong>{{ state?.title || t(`${key}.title`) }}</strong>
        <div class="agent-browser-url">{{ state?.url }}</div>
        <div class="agent-browser-status" aria-live="polite">
          {{ t(`${key}.connectionStates.${connection}`) }}
          <span v-if="state?.tab_id">
            · {{ t(`${key}.tab`) }} {{ state.tab_id.slice(-4) }}</span
          >
          <span v-if="state?.width">
            · {{ state.width }}×{{ state.height }} · #{{ state.sequence }}</span
          >
        </div>
      </div>
      <v-btn
        class="agent-browser-stop"
        variant="text"
        size="small"
        :disabled="
          !state?.available ||
          state?.execution_state === 'stopped' ||
          controlling
        "
        @click="control('stop')"
        >{{ t(`${key}.stop`) }}</v-btn
      >
      <v-btn
        class="agent-browser-close"
        variant="text"
        size="small"
        :disabled="!state?.available || controlling"
        @click="control('close')"
        >{{ t(`${key}.close`) }}</v-btn
      >
      <v-btn
        variant="text"
        size="small"
        :loading="loading"
        :disabled="!chatId"
        @click="refresh()"
      >
        {{ t(`${key}.refresh`) }}
      </v-btn>
    </div>
    <div v-if="controlError" role="alert" class="pa-2 text-caption">
      {{ t(`${key}.controlFailed`) }}
    </div>
    <div
      v-if="connection === 'stopped'"
      role="status"
      class="pa-2 text-caption"
    >
      {{ t(`${key}.stoppedHint`) }}
    </div>
    <div v-if="resynchronized" role="status" class="pa-2 text-caption">
      {{ t(`${key}.resynchronized`) }}
    </div>
    <div v-if="!state?.image" role="status" class="pa-4">
      {{
        connection === "idle"
          ? t(`${key}.empty`)
          : t(`${key}.connectionStates.${connection}`)
      }}
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
