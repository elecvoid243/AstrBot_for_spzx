<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  ref,
  watch,
  type Component,
} from "vue";
import {
  Globe,
  Monitor,
  Square,
  X,
  RefreshCw,
  Eye,
  PanelsTopLeft,
  Scan,
  Hash,
  ListChecks,
  MousePointer2,
  TextCursorInput,
  ArrowUpDown,
  Check,
  CircleX,
  Clock3,
  Ban,
  LoaderCircle,
  CircleAlert,
  Info,
  Power,
  Unplug,
  ShieldAlert,
  UsersRound,
  ChevronDown,
  Maximize2,
  Minimize2,
} from "@lucide/vue";
import { useI18n } from "@/i18n/composables";
import { browserControlApi, pluginExtensionApi } from "@/api/v1";
import { fetchWithAuth } from "@/api/http";

const props = defineProps<{ chatId?: string | null; visible: boolean }>();
const { t } = useI18n();
const key = "features.chat.agentBrowser";
const fullscreen = ref(false);
const fullscreenButton = ref<HTMLButtonElement | null>(null);
const browserRoot = ref<HTMLElement | null>(null);
const fullscreenTheme = ref<Record<string, string>>({});
const fullscreenKey =
  "features.chat.spcodeProjectLoad.documentManager.fullscreen";
function toggleFullscreen() {
  if (!fullscreen.value && browserRoot.value) {
    // Teleport leaves the app's theme ancestor; preserve its actual colors.
    const theme = getComputedStyle(browserRoot.value);
    fullscreenTheme.value = Object.fromEntries(
      ["--v-theme-on-surface", "--v-theme-surface", "--v-theme-primary"].map(
        (name) => [name, theme.getPropertyValue(name)],
      ),
    );
  }
  fullscreen.value = !fullscreen.value;
}
watch(
  () => props.visible,
  (visible) => {
    if (!visible) fullscreen.value = false;
  },
);
watch(fullscreen, async () => {
  await nextTick();
  fullscreenButton.value?.focus({ preventScroll: true });
});
const onFullscreenKeyDown = (event: KeyboardEvent) => {
  if (!fullscreen.value) return;
  if (event.key === "Tab") {
    event.preventDefault();
    fullscreenButton.value?.focus({ preventScroll: true });
    return;
  }
  if (event.key !== "Escape") return;
  event.preventDefault();
  event.stopPropagation();
  fullscreen.value = false;
};
document.addEventListener("keydown", onFullscreenKeyDown, true);

interface BrowserState {
  available: boolean;
  execution_state?: "active" | "stopped" | "closed" | "disabled";
  manual_control_version?: number;
  control_mode?: "agent" | "acquiring" | "human" | "paused";
  control_epoch?: number;
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
const humanControl = ref(false);
const manualPending = ref(false);
const manualMessage = ref("");
let controlSocket: WebSocket | null = null;
let manualEpoch = 0;
let inputNumber = 0;
let controlAttempt = 0;
let heartbeatTimer: ReturnType<typeof setInterval> | undefined;
let acknowledgementTimer: ReturnType<typeof setTimeout> | undefined;

function disconnectControl() {
  if (humanControl.value || (manualPending.value && controlSocket))
    manualMessage.value = t(`${key}.manualDisconnected`);
  controlAttempt += 1;
  humanControl.value = false;
  manualPending.value = false;
  clearInterval(heartbeatTimer);
  clearTimeout(acknowledgementTimer);
  controlSocket?.close();
  controlSocket = null;
}

async function acquireControl() {
  const current = state.value;
  const chatId = props.chatId;
  if (
    !current?.available ||
    !current.generation ||
    !current.browser_session_id ||
    !chatId ||
    manualPending.value
  )
    return;
  disconnectControl();
  const attempt = controlAttempt;
  manualPending.value = true;
  manualMessage.value = "";
  try {
    const response = await browserControlApi.createSession({
      chat_id: chatId,
      generation: current.generation!,
      browser_session_id: current.browser_session_id!,
    });
    if (attempt !== controlAttempt) return;
    if (props.chatId !== chatId || !props.visible || !pageVisible.value) {
      manualPending.value = false;
      return;
    }
    const url = new URL(response.data.websocket_path, location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(url);
    controlSocket = socket;
    socket.onopen = () =>
      socket.send(JSON.stringify({ ticket: response.data.ticket }));
    acknowledgementTimer = setTimeout(() => {
      manualMessage.value = t(`${key}.manualDisconnected`);
      disconnectControl();
    }, 25000);
    socket.onmessage = (message) => {
      if (controlSocket !== socket) return;
      const ack = JSON.parse(message.data);
      if (ack.operation === "heartbeat" && ack.status === "applied") return;
      clearTimeout(acknowledgementTimer);
      manualPending.value = false;
      if (ack.status !== "applied") {
        manualMessage.value = ack.message || t(`${key}.manualFailed`);
        disconnectControl();
        return;
      }
      manualEpoch = ack.control_epoch;
      if (ack.state?.control_mode === "agent") {
        manualMessage.value = t(`${key}.handedBack`);
        disconnectControl();
        return;
      }
      if (!humanControl.value) {
        inputNumber = 0;
        humanControl.value = true;
        heartbeatTimer = setInterval(() => {
          if (socket.readyState === WebSocket.OPEN)
            socket.send(
              JSON.stringify({
                operation: "heartbeat",
                control_epoch: manualEpoch,
              }),
            );
        }, 5000);
      }
      manualMessage.value = t(`${key}.humanActive`);
    };
    socket.onclose = () => {
      if (controlSocket !== socket) return;
      manualMessage.value = t(`${key}.manualDisconnected`);
      disconnectControl();
    };
    socket.onerror = () => socket.close();
  } catch (error: any) {
    if (attempt !== controlAttempt) return;
    manualMessage.value =
      error.response?.data?.message || t(`${key}.manualFailed`);
    disconnectControl();
  }
}

function releaseControl() {
  if (!humanControl.value || manualPending.value) return;
  manualPending.value = true;
  humanControl.value = false;
  clearInterval(heartbeatTimer);
  controlSocket?.send(
    JSON.stringify({ operation: "release", control_epoch: manualEpoch }),
  );
  acknowledgementTimer = setTimeout(() => disconnectControl(), 12000);
}

function clickPage(event: MouseEvent) {
  const current = state.value;
  if (
    !humanControl.value ||
    manualPending.value ||
    !current?.image ||
    controlSocket?.readyState !== WebSocket.OPEN
  )
    return;
  const bounds = (
    event.currentTarget as HTMLImageElement
  ).getBoundingClientRect();
  if (!bounds.width || !bounds.height) return;
  const target = Object.fromEntries(
    [
      "tab_id",
      "document_id",
      "viewport_width",
      "viewport_height",
      "scroll_x",
      "scroll_y",
    ].map((name) => [name, current[name as keyof BrowserState]]),
  );
  manualPending.value = true;
  controlSocket.send(
    JSON.stringify({
      operation: "click",
      control_epoch: manualEpoch,
      input_id: ++inputNumber,
      x:
        ((event.clientX - bounds.left) / bounds.width) *
        current.viewport_width!,
      y:
        ((event.clientY - bounds.top) / bounds.height) *
        current.viewport_height!,
      target,
    }),
  );
  acknowledgementTimer = setTimeout(() => {
    manualMessage.value = t(`${key}.manualDisconnected`);
    disconnectControl();
  }, 25000);
}
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
const actionIcons: Record<BrowserAction["kind"], Component> = {
  navigate: Globe,
  click: MousePointer2,
  type: TextCursorInput,
  scroll: ArrowUpDown,
  tab: PanelsTopLeft,
  close: X,
  inspect: Scan,
};
const resultIcons: Record<BrowserAction["status"], Component> = {
  running: LoaderCircle,
  succeeded: Check,
  failed: CircleX,
  timed_out: Clock3,
  cancelled: Ban,
};
const stateIcons: Record<string, Component> = {
  busy: UsersRound,
  loading: LoaderCircle,
  reconnecting: LoaderCircle,
  disconnected: Unplug,
  unavailable: CircleAlert,
  denied: ShieldAlert,
  disabled: Power,
  stopped: Square,
  closed: Monitor,
};

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
  disconnectControl();
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
    disconnectControl();
  } catch (err: any) {
    if (!controller.signal.aborted && pending === controller) {
      disconnectControl();
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
  disconnectControl();
  manualMessage.value = "";
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
  disconnectControl();
  document.removeEventListener("keydown", onFullscreenKeyDown, true);
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
    ref="browserRoot"
    class="agent-browser-view"
    :aria-label="t(`${key}.title`)"
    :data-generation="state?.generation"
    :data-browser-session-id="state?.browser_session_id"
    :data-tab-id="state?.tab_id"
    :data-sequence="state?.sequence"
    :data-connection="connection"
    :data-human-control="humanControl"
  >
    <header class="agent-browser-toolbar">
      <div class="agent-browser-heading">
        <Globe :size="15" class="agent-browser-brand" aria-hidden="true" />
        <strong class="agent-browser-page" :title="state?.title">{{
          state?.title || t(`${key}.title`)
        }}</strong>
        <span
          class="agent-browser-status"
          :title="t(`${key}.connectionStates.${connection}`)"
          aria-live="polite"
        >
          <span class="agent-browser-status-dot" aria-hidden="true" />
          {{ t(`${key}.statusLabels.${connection}`) }}
        </span>
        <div class="agent-browser-controls">
          <button
            type="button"
            class="agent-browser-takeover agent-browser-button"
            :disabled="
              !state?.available ||
              state?.manual_control_version !== 1 ||
              manualPending ||
              controlling
            "
            :title="
              state?.manual_control_version === 1
                ? undefined
                : t(`${key}.upgradeControl`)
            "
            @click="humanControl ? releaseControl() : acquireControl()"
          >
            {{ t(`${key}.${humanControl ? "handBack" : "takeOver"}`) }}
          </button>
          <button
            type="button"
            class="agent-browser-stop agent-browser-button"
            :title="t(`${key}.stop`)"
            :aria-label="t(`${key}.stop`)"
            :disabled="
              !state?.available ||
              state?.execution_state === 'stopped' ||
              controlling
            "
            @click="control('stop')"
          >
            <Square :size="13" aria-hidden="true" />
          </button>
          <button
            type="button"
            class="agent-browser-close agent-browser-button"
            :title="t(`${key}.close`)"
            :aria-label="t(`${key}.close`)"
            :disabled="!state?.available || controlling"
            @click="control('close')"
          >
            <X :size="15" aria-hidden="true" />
          </button>
        </div>
      </div>
      <div class="agent-browser-address">
        <Globe :size="14" aria-hidden="true" />
        <span class="agent-browser-url" :title="state?.url">{{
          state?.url || t(`${key}.waitingUrl`)
        }}</span>
        <span
          v-if="state?.tab_id"
          class="agent-browser-meta"
          :title="`${t(`${key}.tab`)} ${state.tab_id}`"
        >
          <PanelsTopLeft :size="12" aria-hidden="true" />{{
            state.tab_id.slice(-4)
          }}
        </span>
        <button
          class="agent-browser-refresh"
          type="button"
          :title="t(`${key}.refresh`)"
          :aria-label="t(`${key}.refresh`)"
          :aria-busy="loading"
          :disabled="!chatId"
          @click="refresh()"
        >
          <RefreshCw
            :size="14"
            :class="{ 'is-spinning': loading }"
            aria-hidden="true"
          />
        </button>
      </div>
    </header>

    <div v-if="manualPending" role="status" class="agent-browser-notice">
      {{ t(`${key}.changingControl`) }}
    </div>
    <div v-else-if="manualMessage" role="status" class="agent-browser-notice">
      {{ manualMessage }}
    </div>

    <div
      v-if="controlError"
      role="alert"
      class="agent-browser-notice agent-browser-notice--error"
    >
      <CircleAlert :size="15" aria-hidden="true" /><span>{{
        t(`${key}.controlFailed`)
      }}</span>
    </div>
    <div
      v-if="connection === 'stopped'"
      role="status"
      class="agent-browser-notice agent-browser-notice--warning"
    >
      <Info :size="15" aria-hidden="true" /><span>{{
        t(`${key}.stoppedHint`)
      }}</span>
    </div>
    <div v-if="resynchronized" role="status" class="agent-browser-notice">
      <RefreshCw :size="14" aria-hidden="true" /><span>{{
        t(`${key}.resynchronized`)
      }}</span>
    </div>

    <Teleport to="body" :disabled="!fullscreen">
      <div
        class="agent-browser-stage"
        :class="{ 'is-fullscreen': fullscreen }"
        :style="fullscreen ? fullscreenTheme : undefined"
        :role="fullscreen ? 'dialog' : undefined"
        :aria-modal="fullscreen ? true : undefined"
        :aria-label="fullscreen ? t(`${key}.title`) : undefined"
      >
        <div v-if="!state?.image" role="status" class="agent-browser-empty">
          <span class="agent-browser-empty-icon">
            <component
              :is="stateIcons[connection] || Globe"
              :size="27"
              :stroke-width="1.5"
              :class="{ 'is-spinning': loading }"
              aria-hidden="true"
            />
          </span>
          <strong>{{ t(`${key}.statusLabels.${connection}`) }}</strong>
          <p>
            {{
              connection === "idle"
                ? t(`${key}.empty`)
                : t(`${key}.connectionStates.${connection}`)
            }}
          </p>
        </div>
        <div v-else class="agent-browser-image">
          <div
            class="agent-browser-viewport"
            :style="
              fullscreen && state?.width && state?.height
                ? {
                    width: `min(100%, calc((100dvh - 60px) * ${
                      state.width / state.height
                    }))`,
                  }
                : undefined
            "
          >
            <img
              :src="`data:${state.mime_type};base64,${state.image}`"
              :width="state.width"
              :height="state.height"
              :alt="state.title || t(`${key}.title`)"
              :style="
                humanControl
                  ? { cursor: manualPending ? 'wait' : 'crosshair' }
                  : undefined
              "
              draggable="false"
              @click="clickPage"
            />
            <span
              v-if="marker"
              class="agent-browser-target"
              :style="marker"
              :aria-label="t(`${key}.target`)"
            />
          </div>
        </div>
        <footer class="agent-browser-caption">
          <span
            ><Eye :size="12" aria-hidden="true" />{{
              t(`${key}.${humanControl ? "humanActive" : "readOnly"}`)
            }}</span
          >
          <span v-if="state?.width" class="agent-browser-capture-meta">
            <span
              ><Scan :size="12" aria-hidden="true" />{{ state.width }} ×
              {{ state.height }}</span
            >
            <span
              ><Hash :size="11" aria-hidden="true" />{{ state.sequence }}</span
            >
          </span>
          <button
            ref="fullscreenButton"
            type="button"
            class="agent-browser-fullscreen agent-browser-button"
            :title="t(`${fullscreenKey}.${fullscreen ? 'exit' : 'enter'}`)"
            :aria-label="t(`${fullscreenKey}.${fullscreen ? 'exit' : 'enter'}`)"
            :aria-pressed="fullscreen"
            @click="toggleFullscreen"
          >
            <component
              :is="fullscreen ? Minimize2 : Maximize2"
              :size="14"
              aria-hidden="true"
            />
          </button>
        </footer>
      </div>
    </Teleport>

    <details
      class="agent-browser-actions"
      :aria-label="t(`${key}.recentActions`)"
    >
      <summary class="agent-browser-actions-heading">
        <ListChecks :size="15" aria-hidden="true" />
        <strong>{{ t(`${key}.recentActions`) }}</strong>
        <span class="agent-browser-action-count">{{ actions.length }}</span>
        <ChevronDown
          :size="14"
          class="agent-browser-actions-chevron"
          aria-hidden="true"
        />
      </summary>
      <ol v-if="actions.length" aria-live="polite">
        <li
          v-for="action in actions"
          :key="action.action_id"
          :data-action-id="action.action_id"
          :data-action-status="action.status"
        >
          <span class="agent-browser-action-icon"
            ><component
              :is="actionIcons[action.kind] || Eye"
              :size="14"
              aria-hidden="true"
          /></span>
          <span class="agent-browser-action-label">{{
            t(`${key}.actionKinds.${action.kind}`)
          }}</span>
          <span class="agent-browser-action-result">
            <component
              :is="resultIcons[action.status] || Info"
              :size="12"
              :class="{ 'is-spinning': action.status === 'running' }"
              aria-hidden="true"
            />
            {{ t(`${key}.actionStatuses.${action.status}`) }}
          </span>
        </li>
      </ol>
      <div v-else class="agent-browser-actions-empty">
        <MousePointer2 :size="15" aria-hidden="true" />{{
          t(`${key}.emptyActions`)
        }}
      </div>
    </details>
  </section>
</template>

<style scoped>
.agent-browser-view,
.agent-browser-stage {
  --browser-ink: var(--v-theme-on-surface, 28, 35, 45);
  --browser-accent: var(--v-theme-primary, 60, 150, 202);
  --browser-surface: var(--v-theme-surface, 255, 255, 255);
  --browser-status: var(--browser-ink);
  color: rgb(var(--browser-ink));
}
.agent-browser-view {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  gap: 8px;
  min-height: 0;
  min-width: 0;
  padding: 8px;
  overflow: auto;
  background: rgba(var(--browser-ink), 0.025);
  color: rgb(var(--browser-ink));
}
.agent-browser-view[data-connection="live"] {
  --browser-status: var(--v-theme-success, 0, 158, 96);
}
.agent-browser-view,
.agent-browser-image,
.agent-browser-actions ol {
  scrollbar-width: thin;
  scrollbar-color: rgba(var(--browser-ink), 0.2) transparent;
}
.agent-browser-view:is(
    [data-connection="loading"],
    [data-connection="snapshot"],
    [data-connection="reconnecting"]
  ) {
  --browser-status: var(--browser-accent);
}
.agent-browser-view:is([data-connection="busy"], [data-connection="stopped"]) {
  --browser-status: var(--v-theme-warning, 206, 135, 25);
}
.agent-browser-view:is(
    [data-connection="unavailable"],
    [data-connection="denied"],
    [data-connection="disconnected"]
  ) {
  --browser-status: var(--v-theme-error, 229, 77, 77);
}
.agent-browser-toolbar,
.agent-browser-stage,
.agent-browser-actions {
  border: 1px solid rgba(var(--browser-ink), 0.09);
  border-radius: 12px;
  background: rgb(var(--browser-surface));
}
.agent-browser-toolbar {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 5px 8px;
}
.agent-browser-heading,
.agent-browser-controls,
.agent-browser-meta,
.agent-browser-caption,
.agent-browser-caption span,
.agent-browser-actions-heading,
.agent-browser-action-result {
  display: flex;
  align-items: center;
}
.agent-browser-heading {
  gap: 8px;
  min-width: 0;
}
.agent-browser-brand {
  flex: 0 0 auto;
  color: rgb(var(--browser-accent));
}
.agent-browser-page {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
}
.agent-browser-status {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
  gap: 6px;
  padding: 2px 7px;
  border: 1px solid rgba(var(--browser-status), 0.12);
  border-radius: 20px;
  background: rgba(var(--browser-status), 0.07);
  color: rgba(var(--browser-ink), 0.82);
  font-size: 10.5px;
  font-weight: 500;
}
.agent-browser-status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: rgb(var(--browser-status));
}
.agent-browser-address {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 0 0 0 7px;
  border: 1px solid rgba(var(--browser-ink), 0.08);
  border-radius: 7px;
  background: rgba(var(--browser-ink), 0.025);
  color: rgba(var(--browser-ink), 0.42);
}
.agent-browser-address > svg {
  flex: 0 0 auto;
}
.agent-browser-url {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgba(var(--browser-ink), 0.68);
  font-family: "SFMono-Regular", Consolas, monospace;
  font-size: 11px;
  line-height: 26px;
}
.agent-browser-button,
.agent-browser-refresh {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  gap: 6px;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: rgba(var(--browser-ink), 0.66);
  font: inherit;
  font-size: 11.5px;
  line-height: 18px;
  cursor: pointer;
  transition:
    background-color 150ms,
    color 150ms;
}
.agent-browser-refresh {
  padding: 0;
}
.agent-browser-button {
  border-color: rgba(var(--browser-ink), 0.08);
  background: rgba(var(--browser-ink), 0.025);
}
.agent-browser-button:hover:not(:disabled),
.agent-browser-refresh:hover:not(:disabled) {
  background: rgba(var(--browser-ink), 0.05);
  color: rgb(var(--browser-ink));
}
.agent-browser-stop:hover:not(:disabled),
.agent-browser-close:hover:not(:disabled) {
  background: rgba(var(--v-theme-error, 229, 77, 77), 0.07);
  color: rgb(var(--v-theme-error, 229, 77, 77));
}
.agent-browser-button:disabled,
.agent-browser-refresh:disabled {
  opacity: 0.35;
  cursor: default;
}
.agent-browser-button:focus-visible,
.agent-browser-refresh:focus-visible {
  outline: 2px solid rgb(var(--browser-accent));
  outline-offset: 2px;
}
.agent-browser-controls {
  gap: 3px;
}
.agent-browser-meta {
  flex: 0 0 auto;
  gap: 5px;
  color: rgba(var(--browser-ink), 0.46);
  font-size: 10.5px;
}
.agent-browser-notice {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  flex: 0 0 auto;
  padding: 10px 12px;
  border: 1px solid rgba(var(--browser-accent), 0.13);
  border-radius: 9px;
  background: rgba(var(--browser-accent), 0.06);
  color: rgba(var(--browser-ink), 0.76);
  font-size: 11.5px;
  line-height: 1.6;
}
.agent-browser-notice > svg {
  flex: 0 0 auto;
  margin-top: 2px;
  color: rgb(var(--browser-accent));
}
.agent-browser-notice--error {
  border-color: rgba(var(--v-theme-error), 0.2);
  background: rgba(var(--v-theme-error), 0.05);
}
.agent-browser-notice--error > svg {
  color: rgb(var(--v-theme-error));
}
.agent-browser-notice--warning {
  border-color: rgba(var(--v-theme-warning), 0.25);
  background: rgba(var(--v-theme-warning), 0.06);
}
.agent-browser-notice--warning > svg {
  color: rgb(var(--v-theme-warning));
}
.agent-browser-stage {
  display: flex;
  flex-direction: column;
  flex: 1 1 0;
  min-height: 120px;
  overflow: hidden;
}
.agent-browser-stage.is-fullscreen {
  position: fixed;
  inset: 0;
  z-index: 2400;
  border: 0;
  border-radius: 0;
  overscroll-behavior: contain;
}
.agent-browser-image {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 8px;
  background: rgba(var(--browser-ink), 0.035);
  overscroll-behavior: contain;
}
.agent-browser-stage.is-fullscreen .agent-browser-image {
  align-items: center;
  justify-content: center;
}
.agent-browser-image img {
  display: block;
  width: 100%;
  height: auto;
  pointer-events: auto;
}
.agent-browser-takeover {
  width: auto;
  min-width: 90px;
  padding-inline: 8px;
  white-space: nowrap;
  font-size: 11px;
}
.agent-browser-viewport {
  position: relative;
  flex: 0 0 auto;
  width: 100%;
  margin-block: auto;
  overflow: hidden;
  border-radius: 6px;
  background: white;
  box-shadow:
    0 2px 12px rgba(0, 0, 0, 0.06),
    0 0 0 1px rgba(0, 0, 0, 0.04);
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
  flex: 0 0 auto;
  min-height: 0;
  overflow: hidden;
}
.agent-browser-caption {
  flex: 0 0 auto;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 6px;
  padding: 3px 8px;
  border-top: 1px solid rgba(var(--browser-ink), 0.06);
  color: rgba(var(--browser-ink), 0.46);
  font-size: 10px;
}
.agent-browser-caption span {
  gap: 5px;
}
.agent-browser-capture-meta {
  margin-left: auto;
  gap: 12px !important;
  font-variant-numeric: tabular-nums;
}
.agent-browser-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex: 1;
  gap: 12px;
  padding: 28px 22px;
  text-align: center;
}
.agent-browser-empty-icon {
  display: grid;
  width: 60px;
  height: 60px;
  place-items: center;
  border: 1px solid rgba(var(--browser-accent), 0.1);
  border-radius: 18px;
  background: rgba(var(--browser-accent), 0.06);
  color: rgb(var(--browser-accent));
}
.agent-browser-empty strong {
  font-size: 13px;
  font-weight: 600;
}
.agent-browser-empty p {
  margin: 0;
  max-width: 32ch;
  color: rgba(var(--browser-ink), 0.5);
  font-size: 11.5px;
  line-height: 1.7;
}
.agent-browser-actions-heading {
  flex: 0 0 auto;
  gap: 7px;
  padding: 7px 10px;
  cursor: pointer;
  list-style: none;
  color: rgba(var(--browser-ink), 0.62);
  font-size: 12px;
}
.agent-browser-actions-heading::-webkit-details-marker {
  display: none;
}
.agent-browser-actions-heading:focus-visible {
  outline: 2px solid rgb(var(--browser-accent));
  outline-offset: -2px;
}
.agent-browser-actions-chevron {
  transition: transform 150ms;
}
.agent-browser-actions[open] .agent-browser-actions-chevron {
  transform: rotate(180deg);
}
.agent-browser-actions[open] .agent-browser-actions-heading {
  border-bottom: 1px solid rgba(var(--browser-ink), 0.06);
}
.agent-browser-actions-heading strong {
  font-size: 11.5px;
  font-weight: 600;
}
.agent-browser-action-count {
  margin-left: auto;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(var(--browser-ink), 0.05);
  color: rgba(var(--browser-ink), 0.48);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}
.agent-browser-actions ol {
  max-height: min(180px, 25dvh);
  min-height: 0;
  margin: 0;
  padding: 4px 0;
  overflow: auto;
  list-style: none;
}
.agent-browser-actions li {
  position: relative;
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 7px 12px;
}
.agent-browser-actions li:not(:last-child)::before {
  position: absolute;
  content: "";
  left: 25px;
  top: 34px;
  bottom: -8px;
  width: 1px;
  background: rgba(var(--browser-ink), 0.1);
}
.agent-browser-action-icon {
  z-index: 1;
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border: 1px solid rgba(var(--browser-ink), 0.08);
  border-radius: 8px;
  background: rgb(var(--browser-surface));
  color: rgba(var(--browser-ink), 0.52);
}
.agent-browser-action-label {
  min-width: 0;
  font-size: 11.5px;
  font-weight: 500;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.agent-browser-action-result {
  gap: 4px;
  padding: 3px 6px;
  border-radius: 5px;
  background: rgba(var(--browser-ink), 0.04);
  color: rgba(var(--browser-ink), 0.55);
  font-size: 10px;
  line-height: 1.4;
}
.agent-browser-action-result > svg {
  flex: 0 0 auto;
}
[data-action-status="running"] {
  background: rgba(var(--browser-accent), 0.035);
}
[data-action-status="running"] .agent-browser-action-result {
  color: rgb(var(--browser-accent));
  color: color-mix(
    in srgb,
    rgb(var(--browser-accent)) 65%,
    rgb(var(--browser-ink))
  );
  background: rgba(var(--browser-accent), 0.08);
}
[data-action-status="succeeded"] .agent-browser-action-result {
  color: rgb(var(--v-theme-success, 0, 158, 96));
  color: color-mix(
    in srgb,
    rgb(var(--v-theme-success, 0, 158, 96)) 65%,
    rgb(var(--browser-ink))
  );
  background: rgba(var(--v-theme-success, 0, 158, 96), 0.08);
}
[data-action-status="failed"] .agent-browser-action-result {
  color: rgb(var(--v-theme-error, 229, 77, 77));
  color: color-mix(
    in srgb,
    rgb(var(--v-theme-error, 229, 77, 77)) 65%,
    rgb(var(--browser-ink))
  );
  background: rgba(var(--v-theme-error, 229, 77, 77), 0.07);
}
[data-action-status="timed_out"] .agent-browser-action-result {
  color: rgb(var(--v-theme-warning, 190, 125, 25));
  color: color-mix(
    in srgb,
    rgb(var(--v-theme-warning, 190, 125, 25)) 65%,
    rgb(var(--browser-ink))
  );
  background: rgba(var(--v-theme-warning, 190, 125, 25), 0.09);
}
.agent-browser-actions-empty {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 12px;
  color: rgba(var(--browser-ink), 0.42);
  font-size: 11.5px;
  line-height: 1.6;
}
.is-spinning {
  animation: browser-spin 1.3s linear infinite;
}
@keyframes browser-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (max-width: 600px) {
  .agent-browser-view {
    padding: 8px;
    gap: 8px;
  }
  .agent-browser-toolbar {
    padding: 5px 7px;
  }
  .agent-browser-image {
    padding: 8px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .is-spinning {
    animation: none;
  }
  .agent-browser-button,
  .agent-browser-refresh {
    transition: none;
  }
}
</style>
