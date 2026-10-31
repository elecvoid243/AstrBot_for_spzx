<!--
  ShellSessionWindow
  ─────────────────────────────────────────────────────────────────────
  Floating output window for one managed shell session (multi-instance:
  Chat.vue hosts one per entry in chatHeader.openShellWindows). Peek-polls
  via useShellSessionOutput (non-destructive; the agent's cursor is never
  consumed). Terminate is a two-click confirm. A window whose session was
  reaped keeps its last content and freezes — never auto-closes.

  Author: elecvoid243 | 2026-10-05
-->
<template>
  <div
    ref="winEl"
    class="shell-win"
    :style="{
      zIndex,
      right: `${pos.x}px`,
      bottom: `${pos.y}px`,
      width: `${chatHeader.shellWindowSize.width}px`,
      height: `${chatHeader.shellWindowSize.height}px`,
      maxWidth: `calc(100vw - ${pos.x}px)`,
      maxHeight: `calc(100vh - ${pos.y}px)`,
    }"
    @mousedown="chatHeader.FOCUS_SHELL_WINDOW(shellSessionId)"
  >
    <div class="win-head" @mousedown="startDrag">
      <span
        class="status-dot"
        :class="{ pulse: statusMeta.pulse && !sessionClosed }"
        :style="{ backgroundColor: statusMeta.color }"
      />
      <span class="sid" :title="shellSessionId">{{ shellSessionId }}</span>
      <span class="state" :style="{ color: statusMeta.color }">
        {{ tm(`shellSession.stateLabels.${statusMeta.i18nKey}`) }}
      </span>
      <span v-if="meta?.pid" class="meta">pid {{ meta.pid }}</span>
      <span v-if="meta?.started_at" class="meta">{{
        formatRelativeTime(meta.started_at)
      }}</span>
      <button
        v-if="canTerminate"
        class="win-btn term"
        :class="{ confirm: confirmTerminate }"
        @click="onTerminate"
      >
        {{
          confirmTerminate
            ? tm("shellSession.window.terminateConfirm")
            : tm("shellSession.window.terminate")
        }}
      </button>
      <button
        class="win-btn"
        :title="tm('shellSession.window.close')"
        @click="close"
      >
        ✕
      </button>
    </div>
    <div v-if="meta?.command" class="win-command" :title="meta.command">
      {{ meta.command }}
    </div>
    <div ref="logRef" class="win-body">
      {{ outputText || tm("shellSession.window.noOutput") }}
    </div>
    <div class="win-foot">
      <label class="follow">
        <input v-model="follow" type="checkbox" />
        {{ tm("shellSession.window.followOutput") }}
      </label>
      <span class="win-foot-spacer" />
      <span>{{
        sessionClosed
          ? tm("shellSession.window.closed")
          : tm("shellSession.window.polling")
      }}</span>
      <span>{{
        tm("shellSession.window.readBytes", { size: formatBytes(byteCount) })
      }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { chatApi } from "@/api/v1";
import { useChatHeaderStore } from "@/stores/chatHeader";
import { useModuleI18n } from "@/i18n/composables";
import { useShellSessionOutput } from "@/composables/useShellSessionOutput";
import { formatBytes } from "./message_list_comps/shell_session_tools/format";
import { formatRelativeTime } from "./message_list_comps/inta_shell_tools/format";
import { getShellSessionStatusMeta } from "./message_list_comps/shell_session_tools/icons";

const props = defineProps<{
  /** WebChat conversation id. */
  sessionId: string;
  /** Managed shell session id this window follows. */
  shellSessionId: string;
  zIndex: number;
}>();

const chatHeader = useChatHeaderStore();
const { tm } = useModuleI18n("features/chat");

// Snapshot list metadata at mount: a reaped session leaves the push list,
// but the window must keep showing its last-known identity.
const meta = ref(
  chatHeader.shellSessions?.find(
    (s) => s.session_id === props.shellSessionId,
  ) ?? null,
);

const { outputText, status, sessionClosed, follow, start, stop } =
  useShellSessionOutput(props.sessionId, props.shellSessionId);

// Snapshot the owning conversation at mount: props.sessionId tracks the
// LIVE conversation, which changes when the user switches sessions — but
// this window keeps following (and must keep terminating) the session it
// was opened for.
const ownerSessionId = props.sessionId;

const statusMeta = computed(() => getShellSessionStatusMeta(status.value));
// Terminate only makes sense while the process is alive: completed /
// failed / timed_out / terminated / already-reaped sessions hide it.
const canTerminate = computed(
  () => status.value === "running" && !sessionClosed.value,
);
const byteCount = computed(() => new Blob([outputText.value]).size);

// ── Auto-scroll while following ───────────────────────────────────
const logRef = ref<HTMLElement | null>(null);
const winEl = ref<HTMLElement | null>(null);
watch(outputText, async () => {
  if (!follow.value) return;
  await nextTick();
  if (logRef.value) logRef.value.scrollTop = logRef.value.scrollHeight;
});

// ── Terminate (two-click confirm, mirroring fileChange undo) ──────
const confirmTerminate = ref(false);
let confirmTimer: ReturnType<typeof setTimeout> | null = null;

async function onTerminate() {
  if (!confirmTerminate.value) {
    confirmTerminate.value = true;
    confirmTimer = setTimeout(() => (confirmTerminate.value = false), 3000);
    return;
  }
  if (confirmTimer) clearTimeout(confirmTimer);
  confirmTerminate.value = false;
  try {
    const resp = await chatApi.terminateShellSession(
      ownerSessionId,
      props.shellSessionId,
    );
    // Business errors arrive as HTTP 200 {status:"error"} envelopes — only
    // a real ok result flips the window to terminated.
    const envelope = resp.data as { status?: string } | undefined;
    if (!envelope || envelope.status !== "ok") {
      console.error("Failed to terminate shell session:", envelope);
      return;
    }
    status.value = "terminated";
    sessionClosed.value = true;
  } catch (error) {
    console.error("Failed to terminate shell session:", error);
  }
}

function close() {
  stop();
  chatHeader.SET_SHELL_WINDOW_OPEN(props.shellSessionId, false);
}

// ── Dragging (header), same interaction as the todo summary bar ────
// Position is stored as right/bottom offsets so the window stays anchored
// to the composer corner across resizes; the nth window cascades inward.
const idx = Math.max(
  chatHeader.openShellWindows.indexOf(props.shellSessionId),
  0,
);
const pos = ref({ x: 24 + idx * 28, y: 120 + idx * 28 });

function startDrag(e: MouseEvent) {
  if ((e.target as HTMLElement).closest("button")) return;
  e.preventDefault();
  chatHeader.FOCUS_SHELL_WINDOW(props.shellSessionId);
  const startX = e.clientX;
  const startY = e.clientY;
  const origin = { ...pos.value };
  const onMove = (ev: MouseEvent) => {
    // Clamp both directions: right/bottom offsets only grow when dragging
    // left/up, so an unclamped window could leave the viewport entirely.
    const el = winEl.value;
    const maxX = el
      ? window.innerWidth - el.offsetWidth
      : Number.MAX_SAFE_INTEGER;
    const maxY = el
      ? window.innerHeight - el.offsetHeight
      : Number.MAX_SAFE_INTEGER;
    pos.value = {
      x: Math.min(
        Math.max(0, origin.x - (ev.clientX - startX)),
        Math.max(0, maxX),
      ),
      y: Math.min(
        Math.max(0, origin.y - (ev.clientY - startY)),
        Math.max(0, maxY),
      ),
    };
  };
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
  };
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

let resizeObserver: ResizeObserver | null = null;
onMounted(() => {
  try {
    const saved = JSON.parse(
      localStorage.getItem("chat.shellWindowSize") || "null",
    );
    if (Number.isFinite(saved?.width) && Number.isFinite(saved?.height)) {
      chatHeader.shellWindowSize = {
        width: Math.max(320, saved.width),
        height: Math.max(220, saved.height),
      };
    }
  } catch {
    /* Ignore invalid or unavailable saved preferences. */
  }
  if (winEl.value) {
    resizeObserver = new ResizeObserver(() => {
      const el = winEl.value;
      if (!el || el.offsetWidth === 0 || el.offsetHeight === 0) return;
      const size = { width: el.offsetWidth, height: el.offsetHeight };
      if (
        size.width === chatHeader.shellWindowSize.width &&
        size.height === chatHeader.shellWindowSize.height
      )
        return;
      chatHeader.shellWindowSize = size;
      try {
        localStorage.setItem("chat.shellWindowSize", JSON.stringify(size));
      } catch {
        /* Storage may be disabled. */
      }
    });
    resizeObserver.observe(winEl.value);
  }
  void start();
});
onUnmounted(() => {
  resizeObserver?.disconnect();
  stop();
  if (confirmTimer) clearTimeout(confirmTimer);
});
</script>

<style scoped>
.shell-win {
  position: fixed;
  resize: both;
  min-width: min(320px, calc(100vw - 24px));
  min-height: min(220px, calc(100vh - 24px));
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 12px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.16);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.win-head {
  flex-shrink: 0;
  flex-wrap: wrap;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  cursor: move;
  user-select: none;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.pulse {
  animation: shell-pulse 1.6s ease-in-out infinite;
}

@keyframes shell-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

.sid {
  font-family: monospace;
  font-size: 12.5px;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.state {
  font-size: 12px;
  flex-shrink: 0;
}

.meta {
  font-size: 11px;
  opacity: 0.6;
  font-family: monospace;
  flex-shrink: 0;
}

.win-btn {
  border: none;
  background: none;
  cursor: pointer;
  padding: 3px 7px;
  border-radius: 6px;
  font-size: 12px;
  color: inherit;
  opacity: 0.75;
  flex-shrink: 0;
}

.win-btn:hover {
  background: rgba(var(--v-theme-on-surface), 0.06);
  opacity: 1;
}

.win-btn.term {
  color: #cf222e;
}

.win-btn.term.confirm {
  background: #cf222e;
  color: #fff;
  opacity: 1;
}

.win-body {
  /* The terminal log is the window's main content: it claims most of the
     vertical space instead of a fixed short strip. (Trimmed 30% from the
     first cut, which read as too tall.) */
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px 12px;
  background: #0d1117;
  color: #c9d1d9;
  font-family: monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}

.win-foot {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  font-size: 11.5px;
  opacity: 0.7;
}

.follow {
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
}

/* NOT `.spacer`: that class name carries a global 100px vertical padding
   from the layout scss (_container.scss), which ballooned this row into a
   ~200px-tall box with the text centered — the "blank area" bug. */
.win-foot-spacer {
  flex: 1;
}
.win-command {
  flex-shrink: 0;
  padding: 7px 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  font:
    12px/1.5 ui-monospace,
    SFMono-Regular,
    Menlo,
    Consolas,
    monospace;
}
</style>
