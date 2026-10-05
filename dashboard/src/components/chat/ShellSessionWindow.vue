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
    class="shell-win"
    :style="{ zIndex, right: `${pos.x}px`, bottom: `${pos.y}px` }"
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
      <button
        v-if="!terminated"
        class="win-btn term"
        :class="{ confirm: confirmTerminate }"
        @click="onTerminate"
      >
        {{ confirmTerminate ? tm("shellSession.window.terminateConfirm") : tm("shellSession.window.terminate") }}
      </button>
      <button class="win-btn" :title="tm('shellSession.window.close')" @click="close">✕</button>
    </div>
    <div ref="logRef" class="win-body">{{ outputText || tm("shellSession.window.noOutput") }}</div>
    <div class="win-foot">
      <label class="follow">
        <input v-model="follow" type="checkbox" />
        {{ tm("shellSession.window.followOutput") }}
      </label>
      <span class="spacer" />
      <span>{{ tm("shellSession.window.readBytes", { size: formatBytes(byteCount) }) }}</span>
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
  chatHeader.shellSessions?.find((s) => s.session_id === props.shellSessionId) ??
    null,
);

const { outputText, status, sessionClosed, follow, start, stop } =
  useShellSessionOutput(props.sessionId, props.shellSessionId);

const statusMeta = computed(() => getShellSessionStatusMeta(status.value));
const terminated = computed(() => status.value === "terminated");
const byteCount = computed(() => new Blob([outputText.value]).size);

// ── Auto-scroll while following ───────────────────────────────────
const logRef = ref<HTMLElement | null>(null);
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
    await chatApi.terminateShellSession(props.sessionId, props.shellSessionId);
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
const idx = Math.max(chatHeader.openShellWindows.indexOf(props.shellSessionId), 0);
const pos = ref({ x: 24 + idx * 28, y: 120 + idx * 28 });

function startDrag(e: MouseEvent) {
  if ((e.target as HTMLElement).closest("button")) return;
  e.preventDefault();
  chatHeader.FOCUS_SHELL_WINDOW(props.shellSessionId);
  const startX = e.clientX;
  const startY = e.clientY;
  const origin = { ...pos.value };
  const onMove = (ev: MouseEvent) => {
    pos.value = {
      x: Math.max(0, origin.x - (ev.clientX - startX)),
      y: Math.max(0, origin.y - (ev.clientY - startY)),
    };
  };
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
  };
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

onMounted(() => {
  void start();
});
onUnmounted(() => {
  stop();
  if (confirmTimer) clearTimeout(confirmTimer);
});
</script>

<style scoped>
.shell-win {
  position: fixed;
  width: 560px;
  max-width: calc(100vw - 48px);
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 12px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.16);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.win-head {
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
  height: 260px;
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

.spacer {
  flex: 1;
}
</style>
